"""Packet 8T-F — Hermes --tui parity via a terminal-native live board.

Proves rendering is pure/backend-shared (status and board agree), the
loop quits and refreshes deterministically, and non-terminal contexts
get a typed refusal instead of a crash.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout


def _inventory():
    return {
        "product": {"public_identity": "Qubit Agent"},
        "agent": {"runtime": "qubit-native", "state": "READY"},
        "broker": {"emergency_stop": False},
        "providers": {"resident_alias": "qubit-brain4", "route": "local"},
        "sessions": {"count": 3},
        "approvals": {"pending": 1},
        "auth": {"providers": [{"provider": "a"}, {"provider": "b"}]},
        "capabilities": {"total": 68, "states": {"IMPLEMENTED": 60}},
    }


def test_render_pure_and_bounded():
    from voicebridge import tui as TUI

    lines = TUI.render(_inventory(), 80)
    assert all(len(line) <= 80 for line in lines)
    blob = "\n".join(lines)
    assert "Qubit Agent" in blob and "qubit-native" in blob
    assert "states: IMPLEMENTED:60" in blob
    assert TUI.render({}, 80)[0].startswith("Qubit Agent")
    assert TUI.render(_inventory(), 10)[0].startswith("Qubit Agent")
    assert TUI.render(_inventory(), -5)[0].startswith("Qubit Agent")


def test_render_agrees_with_status_backend():
    from voicebridge import tui as TUI

    inv = _inventory()
    inv["broker"]["emergency_stop"] = True
    assert "LATCHED" in "\n".join(TUI.render(inv, 80))


def test_render_redacted_and_secret_free():
    from voicebridge import tui as TUI

    inv = _inventory()
    inv["sessions"] = {"count": 1, "note": "sk-live-s3cr3t-1"}
    blob = "\n".join(TUI.render(inv, 80))
    assert "sk-live-s3cr3t-1" not in blob


def test_loop_quit_and_refresh():
    from voicebridge import tui as TUI

    calls = []

    class _Screen:
        def __init__(self, keys):
            self.keys = list(keys)
            self.drawn = []

        def nodelay(self, flag):
            pass

        def timeout(self, ms):
            pass

        def getmaxyx(self):
            return 24, 80

        def addstr(self, row, col, text):
            self.drawn.append(text)

        def refresh(self):
            pass

        def getch(self):
            return self.keys.pop(0)

    screen = _Screen([ord("r"), ord("q")])
    assert TUI.run_loop(screen, lambda: calls.append(1) or _inventory()) == "quit"
    assert len(calls) == 2, "r refreshes (re-polls), q quits"
    assert screen.drawn, "frames drawn before input"
    assert TUI.run_loop(_Screen([ord("Q")]), _inventory) == "quit"


def test_main_refuses_without_tty(monkeypatch):
    from voicebridge import tui as TUI

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    out = TUI.main(None)
    assert out["code"] == "TUI_NO_TTY"
    assert "status" in out["detail"]


def test_main_refuses_without_curses(monkeypatch):
    import os

    from voicebridge import tui as TUI

    class _Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setitem(sys.modules, "curses", None)
    out = TUI.main(None)
    assert out["code"] == "TUI_NO_CURSES"
    assert os.environ["TERM"] == "xterm-256color"


def test_cli_wiring_non_tty():
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_tui(None)
    assert rc == 2, "pytest stdin is not a TTY: typed refusal, never curses"
    assert json.loads(buf.getvalue())["code"] == "TUI_NO_TTY"
    assert "tui" in CLI.build_parser().format_help()


def test_capability_catalog_reports_tui_implemented():
    from voicebridge.capability_catalog import check

    out = check("--tui")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"
