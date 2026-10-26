"""RuntimePort engine selection on the ``qubit -z`` oneshot path.

P3 routes the simpler oneshot path through the port. These tests pin the
contract that matters: native is the default and stays byte-identical, a fake
backend's deltas reach the sink, failures map to the right exit/text, an unknown
engine is a typed exit 2, and OpenJarvis refuses cleanly (never falls back to
native) when its pinned CLI is absent.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import qubit_cli.oneshot as oneshot
from agent.runtime_port import RuntimeCapabilities, TurnEvent, TurnRequest


class _FakeBackend:
    name = "fake"
    capabilities = RuntimeCapabilities(streaming=True, cancel=False, tools=False, images=False)

    def __init__(self, events):
        self.events = list(events)
        self.requests = []

    def run_turn(self, request, cancel=None):
        self.requests.append(request)
        yield from self.events


def _fake_run_agent(response, result, captured):
    def run(prompt, **kwargs):
        captured.update(kwargs, prompt=prompt)
        return response, dict(result)

    return run


def test_default_engine_is_native(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        oneshot, "_run_agent",
        _fake_run_agent("ok", {"final_response": "ok", "completed": True}, captured),
    )

    assert oneshot.run_oneshot("hello") == 0

    assert captured["prompt"] == "hello"
    assert captured["engine"] == "native"
    assert captured["stream"] is None


def test_explicit_native_matches_the_default_bytes(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(
        oneshot, "_run_agent",
        _fake_run_agent("Paris.", {"final_response": "Paris.", "completed": True}, captured),
    )

    assert oneshot.run_oneshot("capital?") == 0
    default_out = capsys.readouterr()

    captured.clear()
    assert oneshot.run_oneshot("capital?", engine="native") == 0
    explicit_out = capsys.readouterr()

    assert explicit_out.out == default_out.out
    assert explicit_out.err == default_out.err
    assert captured["engine"] == "native"


def test_fake_backend_deltas_reach_the_stream_sink():
    backend = _FakeBackend([
        TurnEvent("delta", text="Hel"),
        TurnEvent("delta", text="lo"),
        TurnEvent("done", text="Hello", data={"completed": True}),
    ])
    sink: list[str] = []

    outcome = oneshot._run_port_turn(
        backend, TurnRequest(prompt="hi", session_id="s1"), stream=sink.append)

    assert sink == ["Hel", "lo"]
    assert outcome.terminal == "done"
    assert outcome.text == "Hello"
    assert "".join(outcome.deltas) == "Hello"
    assert backend.requests[0].prompt == "hi"


def test_failed_non_native_turn_exits_one_with_its_error(monkeypatch, capsys):
    failed = {
        "engine": "openjarvis",
        "_runtime_engine": "openjarvis",
        "completed": False,
        "failed": True,
        "final_response": "partial answer",
        "error": "OpenJarvis exited with status 7: boom",
    }
    monkeypatch.setattr(
        oneshot, "_run_agent",
        _fake_run_agent("partial answer", failed, {}),
    )

    assert oneshot.run_oneshot("hello", engine="openjarvis") == 1

    captured = capsys.readouterr()
    assert "partial answer" in captured.out
    assert "OpenJarvis exited with status 7: boom" in captured.err


def test_unknown_engine_exits_two_and_lists_registered_engines(capsys):
    assert oneshot.run_oneshot("hello", engine="does-not-exist") == 2

    err = capsys.readouterr().err
    assert "Unknown runtime engine" in err
    assert "native" in err


def test_openjarvis_refuses_cleanly_when_unconfigured(monkeypatch, tmp_path, capsys):
    # Force the resolver to fail before any spawn; a missing binary must be a
    # typed refusal, not an attempt to run the native engine instead.
    monkeypatch.setenv("QUBIT_OPENJARVIS_BIN", str(tmp_path / "missing-jarvis"))
    monkeypatch.setenv("QUBIT_OPENJARVIS_SOURCE", str(tmp_path / "missing-source"))
    monkeypatch.setenv("QUBIT_OPENJARVIS_HOME", str(tmp_path / "openjarvis-home"))

    def _no_native_fallback(**_kwargs):
        raise AssertionError("native AIAgent must not be constructed on an engine refusal")

    monkeypatch.setattr("run_agent.AIAgent", _no_native_fallback)

    assert oneshot.run_oneshot("hello", engine="openjarvis") == 2

    err = capsys.readouterr().err
    assert "openjarvis_unavailable" in err
    assert "QUBIT_OPENJARVIS_BIN" in err


def test_a_registered_non_native_engine_runs_and_prints_done_text(monkeypatch, capsys):
    import agent.runtime_registry as registry_mod
    from agent.runtime_registry import RuntimeRegistry

    backend = _FakeBackend([
        TurnEvent("delta", text="PORT"),
        TurnEvent("delta", text="_OK"),
        TurnEvent("usage", data={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}),
        TurnEvent("done", text="PORT_OK", data={"completed": True, "model": "fake-model"}),
    ])
    backend.name = "openjarvis"

    def fake_build_registry(factory, *, native_stream=True):
        registry = RuntimeRegistry()
        registry.register(backend)
        return registry

    monkeypatch.setattr(registry_mod, "build_registry", fake_build_registry)

    assert oneshot.run_oneshot("hello", engine="openjarvis") == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "PORT_OK"
    assert captured.err == ""
    assert backend.requests[0].prompt == "hello"


def test_non_native_resume_passes_history_without_writing_the_native_session(
    monkeypatch, tmp_path, capsys,
):
    import agent.runtime_registry as registry_mod
    from agent.runtime_registry import RuntimeRegistry
    from qubit_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="s1", source="cli")
    db.append_message("s1", "user", content="remember ZEBRA42")

    backend = _FakeBackend([TurnEvent("done", text="ok", data={"completed": True})])
    backend.name = "openjarvis"

    def fake_build_registry(factory, *, native_stream=True):
        registry = RuntimeRegistry()
        registry.register(backend)
        return registry

    monkeypatch.setattr(registry_mod, "build_registry", fake_build_registry)
    monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: db)

    assert oneshot.run_oneshot("next", engine="openjarvis", resume="s1") == 0

    request = backend.requests[0]
    assert request.session_id == "s1"
    assert request.history[0]["content"] == "remember ZEBRA42"

    reopened = SessionDB(db_path=tmp_path / "state.db")
    try:
        # The P2 adapter is stateless: the native row keeps only its original
        # history, so a later native resume is not fed a foreign engine's turn.
        assert len(reopened.get_messages("s1")) == 1
    finally:
        reopened.close()


def test_openjarvis_refuses_native_only_flags(capsys):
    assert oneshot.run_oneshot("hello", engine="openjarvis", model="some-model") == 2

    err = capsys.readouterr().err
    assert "--engine openjarvis does not accept --model" in err
    assert "QUBIT_OPENJARVIS_MODEL" in err


def test_top_level_parser_accepts_engine_with_native_default():
    from qubit_cli._parser import build_top_level_parser

    parser, _subparsers, _chat = build_top_level_parser()

    assert parser.parse_args(["-z", "hi"]).engine == "native"
    assert parser.parse_args(["-z", "hi", "--engine", "openjarvis"]).engine == "openjarvis"


def test_run_oneshot_from_args_forwards_the_engine(monkeypatch):
    import qubit_cli.main as main_mod

    captured = {}
    monkeypatch.setattr(main_mod, "_confirm_startup_expensive_model_override", lambda args: None)
    monkeypatch.setattr(main_mod, "_resolve_chat_session_args", lambda args, use_tui=False: None)
    monkeypatch.setattr(
        "qubit_cli.gateway_chat_startup.ensure_launch_provider", lambda args: True)
    monkeypatch.setattr(
        main_mod, "_run_and_exit_oneshot",
        lambda prompt, **kwargs: captured.update(prompt=prompt, **kwargs),
    )
    args = SimpleNamespace(
        oneshot="hello", model=None, provider=None, toolsets=None, skills=None,
        usage_file=None, resume=None, reasoning=None, engine="openjarvis",
    )

    main_mod._run_oneshot_from_args(args)

    assert captured["prompt"] == "hello"
    assert captured["engine"] == "openjarvis"


def test_chat_refuses_a_non_native_engine_for_an_interactive_session(capsys):
    import qubit_cli.main as main_mod

    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_chat(SimpleNamespace(engine="openjarvis"))

    assert exc.value.code == 2
    assert "only supported for non-interactive single-query runs" in capsys.readouterr().err
