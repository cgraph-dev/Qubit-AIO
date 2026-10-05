"""Wake path must never synthesize keys into the focused window (P0).

Saying "hey qubit" while the owner sits in a terminal killed the foreground
process (SIGINT): every wake turn called the service capture endpoint, and
the service capture synthesizes Ctrl+C into the focused window to read the
selection. In any terminal whose WM_CLASS is not on the adapter's allowlist
(VS Code's integrated terminal: class "code", gnome-console, JetBrains, ...,
or an empty class when detection fails) that bare Ctrl+C is SIGINT.

Contract (fail-closed): the wake turn builds its snapshot from a read-only
``focused_window()`` query only — no service capture call, no clipboard
touch, no key synthesis. Push-to-talk keeps the real capture path.

Hermetic: no X, mic, socket, model, or signal delivery.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.voice_edit.schema import SelectionSnapshot


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {"voice_edit": {"enabled": True}}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeOverlay:
    """Records panel activity; show() proves the wake panel still fires."""

    def __init__(self):
        self.statuses = []
        self.previews = []
        self.errors = []
        self.shown = 0
        self.history = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        self.previews.append(payload)

    def append_history(self, line):
        self.history.append(line)

    def show(self):
        self.shown += 1

    def show_error(self, code, detail):
        self.errors.append((code, detail))


class FakeClient:
    """The service boundary: capture() is the call that synthesizes Ctrl+C."""

    def __init__(self, *, mode="answer"):
        self.calls = []
        self._mode = mode

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        self.calls.append(("begin",))
        return {"ok": True, "request_id": "req-1"}

    def capture(self, rid):
        # Production: HTTP POST /api/voice-edit/{rid}/capture ->
        # service.capture -> desktop.capture_selection ->
        # `xdotool key Control_L+c` into the focused window.
        self.calls.append(("capture", rid))
        return {"ok": True, "selection": {"available": False}}

    def route(self, rid, transcript):
        self.calls.append(("route", rid, transcript))
        if self._mode == "answer":
            return {"ok": True, "mode": "answer", "question": transcript}
        return {"ok": True, "mode": "ignore"}

    def ask_result(self, text, rid=""):
        self.calls.append(("ask", text))
        return {"answer": "wake_turn_ok", "spoken": True}

    def speak(self, text):
        self.calls.append(("speak", text))
        return b""


class FakeKeyBackend:
    """Stands in for xdotool: records EVERY synthesized chord + kill."""

    def __init__(self, *, app_class="code"):
        self.key_calls = []
        self.kills = []
        self._app_class = app_class

    # -- DesktopAdapter surface used by the helper -----------------------
    def session(self):
        return {"session_type": "x11", "x11": True}

    def focused_window(self):
        # VS Code with its integrated terminal focused: NOT on the
        # adapter's terminal allowlist, so production sends bare Ctrl+C.
        return {"window_id": "4242", "app_class": self._app_class,
                "app_name": self._app_class, "window_title": "sleep"}

    def is_fullscreen(self, window_id=""):
        return False

    def set_clipboard(self, text):
        return True

    def insert_text(self, text, snapshot, cfg=None, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        return InsertionResult(ok=True, method="typed",
                               app_class=self._app_class)

    def activate_window(self, window_id):
        return True


def _helper(**kw):
    overlay = FakeOverlay()
    client = FakeClient(**kw)
    desktop = FakeKeyBackend()
    helper = dh.DesktopHelper(
        FakeCfg(), client=client, overlay=overlay, desktop=desktop,
        mic_factory=lambda: None, stt_factory=lambda t: None)
    return helper, overlay, client, desktop


def _join_wake_turn(h, timeout=10):
    th = getattr(h, "_wake_thread", None)
    if th is not None:
        th.join(timeout=timeout)


def test_wake_turn_never_calls_service_capture():
    """The service capture synthesizes Ctrl+C into the focused window.

    The wake turn must not invoke it at all (fail-closed): even one call
    per "hey qubit" is a SIGINT per utterance in a non-allowlisted
    terminal.
    """
    h, overlay, client, desktop = _helper()
    h.on_wake_command("what time is it")
    _join_wake_turn(h)
    captures = [c for c in client.calls if c[0] == "capture"]
    assert captures == [], f"wake turn hit service capture: {client.calls}"


def test_wake_turn_sends_zero_signals_to_foreign_pids(monkeypatch):
    """No os.kill / signal delivery may originate from a wake turn."""
    delivered = []
    monkeypatch.setattr(os, "kill",
                        lambda pid, sig: delivered.append((pid, sig)))
    h, overlay, client, desktop = _helper()
    h.on_wake_command("what time is it")
    _join_wake_turn(h)
    assert delivered == [], f"wake turn signalled pids: {delivered}"


def test_wake_turn_still_routes_and_answers():
    """Removing capture must not break the turn: route + answer survive."""
    h, overlay, client, desktop = _helper()
    h.on_wake_command("what time is it")
    _join_wake_turn(h)
    assert ("begin",) in client.calls
    routes = [c for c in client.calls if c[0] == "route"]
    assert routes and routes[0][2] == "what time is it"
    assert any(c[0] == "ask" for c in client.calls), client.calls


def test_wake_turn_still_shows_panel_and_heard_preview():
    """Beep + arm + panel show + status-line preview (§67) all survive."""
    h, overlay, client, desktop = _helper()
    h.on_wake_command("what time is it")
    _join_wake_turn(h)
    assert overlay.shown >= 1, "wake turn must still show the panel"
    assert any(s[0] == "heard" and "what time is it" in s[1]
               for s in overlay.statuses), overlay.statuses


def test_wake_snapshot_is_local_only_with_focus_identity():
    """The wake snapshot carries focus identity for the answer path but
    claims no selection (nothing was read, nothing synthesized)."""
    h, overlay, client, desktop = _helper()
    h.on_wake_command("what time is it")
    _join_wake_turn(h)
    snap = h.snapshot
    assert isinstance(snap, SelectionSnapshot)
    assert snap.window_id == "4242"
    assert snap.app_class == "code"
    assert snap.available is False


def test_ptt_path_still_captures_selection():
    """Push-to-talk keeps the real capture path (user holds the key over
    a selection there — explicit, foreground, consented)."""
    overlay = FakeOverlay()
    client = FakeClient()
    desktop = FakeKeyBackend()
    helper = dh.DesktopHelper(
        FakeCfg(), client=client, overlay=overlay, desktop=desktop,
        mic_factory=lambda: None, stt_factory=lambda t: None)
    helper.request_id = "req-ptt"
    assert helper._capture_selection() is True
    assert ("capture", "req-ptt") in client.calls
