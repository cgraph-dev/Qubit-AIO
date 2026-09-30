"""W4 CLI parity — ``qubit chat`` is a real working command.

``qubit chat`` was a typed ``QUBIT_NATIVE_CHAT`` refusal with no working
counterpart. It is now a first-class **classified forward** to the vendored
interactive chat (``qubit agent chat``): the whole invocation is delegated to
the same in-process runtime, so upstream argparse help, provider resolution,
session persistence, and one-shot mode are preserved.

The interactive session still needs a TTY: a bare ``qubit chat`` on a non-TTY
stdin returns a typed, helpful ``CHAT_REQUIRES_TTY`` envelope (rc 2) naming the
real alternatives, instead of launching a TUI that cannot read input. Help and
every one-shot selector (``-q/--query/--query-file/--oneshot/-Q/--quiet``)
forward unchanged.

This is behavior, not a refusal: the proof below exercises the real production
entrypoint :func:`voicebridge.cli.main` and asserts the vendored runtime is
reached (monkeypatched) exactly when it should be.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import cli_qubit_forward as HF  # noqa: E402
from voicebridge import qubit_runtime  # noqa: E402


def _capture(monkeypatch):
    calls = []

    def _fake_run(argv=None):
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(qubit_runtime, "run", _fake_run)
    return calls


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _stdin(monkeypatch, *, tty: bool) -> None:
    monkeypatch.setattr(sys, "stdin", _Tty() if tty else io.StringIO())


# ---------------------------------------------------------------------------
# Routing: classified forward, never a refusal, never a raw passthrough.
# ---------------------------------------------------------------------------
def test_chat_is_not_a_refusal_anymore():
    assert "chat" not in HF.NATIVE_REFUSALS
    assert "chat_route" in HF.__all__


@pytest.mark.parametrize("argv", [
    ["chat"],
    ["chat", "-q", "hi"],
    ["chat", "--help"],
    ["chat", "--resume", "latest"],
    ["chat", "__no_such_subcommand__"],
])
def test_chat_forwards_verbatim(argv):
    route = HF.route(argv)
    assert route is not None and route.kind == "forward", (argv, route)
    assert route.argv == argv, (argv, route.argv)


def test_chat_strips_qubit_only_launcher_globals():
    route = HF.route(["chat", "--config", "/x", "-q", "hi"])
    assert route is not None and route.kind == "forward"
    assert route.argv == ["chat", "-q", "hi"], route.argv


# ---------------------------------------------------------------------------
# Dispatch: the TTY gate is real behavior, not a dead-end refusal.
# ---------------------------------------------------------------------------
def test_non_tty_bare_chat_is_typed_helpful(monkeypatch):
    calls = _capture(monkeypatch)
    _stdin(monkeypatch, tty=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(["chat"])
    assert rc == 2, rc
    envelope = json.loads(buf.getvalue())
    assert envelope["state"] == "needs-tty"
    assert envelope["code"] == "CHAT_REQUIRES_TTY"
    # Names real, working non-interactive counterparts.
    assert "qubit chat -q" in envelope["detail"]
    assert envelope["alternatives"]
    assert calls == [], calls


def test_tty_bare_chat_opens_the_real_chat(monkeypatch):
    calls = _capture(monkeypatch)
    _stdin(monkeypatch, tty=True)
    with redirect_stdout(io.StringIO()):
        rc = CLI.main(["chat"])
    assert rc == 0 and calls == [["chat"]], calls


@pytest.mark.parametrize("tail", [
    ["-q", "hi"],
    ["--query", "hi"],
    ["--query-file", "-"],
    ["--oneshot", "-q", "hi"],
    ["-Q", "-q", "hi"],
    ["--help"],
])
def test_non_tty_headless_and_help_forward(monkeypatch, tail):
    calls = _capture(monkeypatch)
    _stdin(monkeypatch, tty=False)
    with redirect_stdout(io.StringIO()):
        rc = CLI.main(["chat", *tail])
    assert rc == 0, rc
    assert calls == [["chat", *tail]], calls


def test_gate_payload_is_brand_clean(monkeypatch):
    _capture(monkeypatch)
    _stdin(monkeypatch, tty=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        CLI.main(["chat"])
    low = buf.getvalue().lower()
    for legacy in ("hermes", "openclaw", "nousresearch", "nousportal"):
        assert legacy not in low, legacy


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
