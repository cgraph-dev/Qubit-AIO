"""Tests for `chat -c <title>` failing loudly (stderr) and `--create-if-missing`.

Regression for #86794: a background/quiet `hermes chat -c "<title>" -q "..."`
against a not-yet-existing titled session silently no-oped — the error message
was written to stdout (which quiet/programmatic callers treat as the "final
response" channel) instead of stderr, and there was no way to create the
titled session from the same invocation.

Two behaviors are fixed here:

1. **fail loudly on stderr** — when no session matches `-c <title>`, the error
   goes to stderr (exit 1), so programmatic callers always see it.
2. **--create-if-missing** — same invocation with the flag creates a fresh
   session carrying the title and proceeds, giving plugins/scripts a
   deterministic "send to this named thread, making it if needed" primitive.
"""

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point QUBIT_HOME at a temp dir so session creation stays isolated."""
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return home


def _create_titled_session(title):
    from qubit_cli.main import _create_titled_session as fn

    return fn(title)


def _stub_gateway_writer(monkeypatch, result):
    """Replace the gateway writer main must delegate to (no live gateway in a unit test)."""
    from qubit_cli import gateway_session_store

    seen = []
    monkeypatch.setattr(
        gateway_session_store,
        "create_titled_session",
        lambda title: seen.append(title) or result,
    )
    return seen


class TestCreateIfMissingFlagParsing:
    def test_flag_parses_on_chat_subparser(self):
        """--create-if-missing is accepted by the real chat parser."""
        from qubit_cli._parser import build_top_level_parser

        parser, _subparsers, _chat_parser = build_top_level_parser()
        args = parser.parse_args(
            ["chat", "-c", "Bot Chat", "--create-if-missing", "-q", "hi"]
        )
        assert args.continue_last == "Bot Chat"
        assert getattr(args, "create_if_missing", False) is True

    def test_flag_absent_means_false(self):
        """Without the flag, create_if_missing stays unset (SUPPRESS default)."""
        from qubit_cli._parser import build_top_level_parser

        parser, _subparsers, _chat_parser = build_top_level_parser()
        args = parser.parse_args(["chat", "-c", "Bot Chat", "-q", "hi"])
        # SUPPRESS default: attribute absent, and cmd_chat's getattr(..., False)
        # must treat it as False.
        assert getattr(args, "create_if_missing", False) is False


class TestCreateTitledSession:
    def test_delegates_to_the_gateway_writer(self, isolated_home, monkeypatch):
        """The gateway is the single writer: main must call
        ``gateway_session_store.create_titled_session`` rather than mint a local row (#86794)."""
        seen = _stub_gateway_writer(monkeypatch, "gw-session-id")
        assert _create_titled_session("Bot Chat") == "gw-session-id"
        assert seen == ["Bot Chat"]

    def test_gateway_refusal_returns_none(self, isolated_home, monkeypatch):
        """A refused create (no gateway) surfaces as None so the caller prints its copy."""
        _stub_gateway_writer(monkeypatch, None)
        assert _create_titled_session("Bot Chat") is None

    def test_writer_failure_is_logged_and_returns_none(self, isolated_home, monkeypatch, caplog):
        from qubit_cli import gateway_session_store

        def _boom(title):
            raise RuntimeError("gateway unavailable")

        monkeypatch.setattr(gateway_session_store, "create_titled_session", _boom)
        assert _create_titled_session("Bot Chat") is None
        assert "Failed to create titled session 'Bot Chat'" in caplog.text


class TestChatCFailLoudlyOnStderr:
    """Behavior-level: run the real cmd_chat path and inspect channels."""

    def test_missing_session_fails_on_stderr(self, isolated_home, monkeypatch):
        """-c <missing title> → exit 1, message on stderr, stdout untouched."""
        import sys

        import qubit_cli.main as main_mod

        stderr_lines = []

        class _Exit(Exception):
            pass

        def _fake_exit(code):
            raise _Exit(code)

        class _Stderr:
            def write(self, text):
                stderr_lines.append(text)
                return len(text)

        class _Stdout:
            def write(self, text):
                return len(text)

        monkeypatch.setattr(sys, "exit", _fake_exit)
        monkeypatch.setattr(sys, "stderr", _Stderr())
        monkeypatch.setattr(sys, "stdout", _Stdout())

        args = type(
            "Args",
            (),
            {
                "continue_last": "Bot Chat",
                "resume": None,
                "create_if_missing": False,
            },
        )()

        with pytest.raises(_Exit) as ei:
            main_mod._resolve_continue_arg(args, use_tui=False)

        assert ei.value.args[0] == 1
        assert any("No session found matching 'Bot Chat'" in l for l in stderr_lines)
        assert not stderr_lines[0].startswith("Use 'hermes sessions list'")

    def test_create_if_missing_sets_resume(self, isolated_home, monkeypatch):
        """--create-if-missing resolves to the id the gateway writer created."""
        import qubit_cli.main as main_mod

        seen = _stub_gateway_writer(monkeypatch, "gw-created-id")
        args = type(
            "Args",
            (),
            {
                "continue_last": "Bot Chat",
                "resume": None,
                "create_if_missing": True,
            },
        )()

        main_mod._resolve_continue_arg(args, use_tui=False)

        assert args.resume == "gw-created-id"
        assert seen == ["Bot Chat"]

    def test_create_if_missing_refusal_exits_loudly(self, isolated_home, monkeypatch):
        """A gateway refusal still fails loudly on stderr (never a silent no-op)."""
        import sys

        import qubit_cli.main as main_mod

        _stub_gateway_writer(monkeypatch, None)
        stderr_lines = []

        class _Exit(Exception):
            pass

        class _Stderr:
            def write(self, text):
                stderr_lines.append(text)
                return len(text)

        monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(_Exit(code)))
        monkeypatch.setattr(sys, "stderr", _Stderr())

        args = type(
            "Args",
            (),
            {
                "continue_last": "Bot Chat",
                "resume": None,
                "create_if_missing": True,
            },
        )()

        with pytest.raises(_Exit) as ei:
            main_mod._resolve_continue_arg(args, use_tui=False)

        assert ei.value.args[0] == 1
        assert any("a new titled session could not be created" in line for line in stderr_lines)
