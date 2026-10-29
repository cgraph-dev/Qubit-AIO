"""Session-scoped confirmation-state tests for the desktop helper.

The bug: the helper held ONE ``_pending_task`` dict, so any unrelated
utterance -- especially a command that tripped the wake spotter -- silently
superseded a staged "say yes" and the later "yes" ran the wrong action.
These tests pin the session-scoped FSM contract: a pending survives unrelated
input, affirm runs the ORIGINAL action, deny cancels, expiry stays honest, the
wake path guards the pending, and two sessions stay independent.

Hermetic: no X, mic, socket, model, or service. Each test drives the real
``_process_transcript`` / ``on_wake_command_routed`` bodies with stubs.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh


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
    def __init__(self):
        self.statuses = []
        self.previews = []
        self.errors = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        self.previews.append(payload)

    def show_error(self, code, detail):
        self.errors.append((code, detail))

    def show(self):
        pass

    def hide(self):
        pass

    def close(self):
        pass


class FakeDesktop:
    def __init__(self):
        self.inserted = []
        self.activated = []
        self.clipboard = None

    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def insert_text(self, text, snapshot, cfg=None, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        self.inserted.append(text)
        return InsertionResult(ok=True, method="clipboard_paste",
                               app_class="code", focus_restored=True)

    def activate_window(self, window_id):
        self.activated.append(window_id)
        return True

    def set_clipboard(self, text):
        self.clipboard = text
        return True

    def is_fullscreen(self, window_id=""):
        return False


class FakeMic:
    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        return None

    def stop(self):
        pass


class FakeStt:
    error = None


class FakeClient:
    """Routes matching utterances through ``routes``; records task runs."""

    def __init__(self, routes=None):
        self.calls = []
        self.runs = []
        self._routes = routes or {}
        self._seq = 0

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self, session_id=""):
        self._seq += 1
        self.calls.append(("begin", session_id))
        return {"ok": True, "request_id": f"req-{self._seq}"}

    def capture(self, rid):
        self.calls.append(("capture", rid))
        return {"ok": True, "selection": {
            "available": False, "captured_via": "clipboard",
            "app_class": "code"}}

    def speak(self, text):
        self.calls.append(("speak", text))
        return b""

    def route(self, rid, text):
        self.calls.append(("route", rid, text))
        low = (text or "").lower()
        for key, out in self._routes.items():
            if key in low:
                return dict(out)
        return {"ok": True, "mode": "answer", "question": text}

    def task(self, text, confirm=True, request_id=""):
        self.calls.append(("task", text, confirm))
        self.runs.append(text)
        return {"ok": True, "reply": f"ran: {text}"}

    def ask(self, text, rid=""):
        self.calls.append(("ask", text))
        return f"answer: {text}"

    def ask_result(self, text, rid=""):
        self.calls.append(("ask_result", text))
        return {"answer": f"answer: {text}", "spoken": True}

    def cancel(self, rid):
        self.calls.append(("cancel", rid))
        return {"ok": True, "cancelled": True}


def _helper(monkeypatch, routes=None):
    overlay = FakeOverlay()
    client = FakeClient(routes=routes)
    desktop = FakeDesktop()
    h = dh.DesktopHelper(
        FakeCfg(), client=client, overlay=overlay, desktop=desktop,
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt())
    return h, overlay, client, desktop


def _join_wake(h, timeout=10):
    th = getattr(h, "_wake_thread", None)
    if th is not None:
        th.join(timeout=timeout)


def _task_route(text):
    return {"mode": "task", "task_text": text}


# -- _process_transcript (Right-Ctrl / direct turn path) ---------------------

def test_unrelated_input_keeps_pending_and_does_not_route(monkeypatch):
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={"delete": _task_route("delete everything")})
    h._process_transcript("delete everything")
    assert h._pending_task.get("text") == "delete everything"

    client.calls.clear()
    h._process_transcript("open youtube")
    assert client.runs == [], "unrelated input must not run the staged task"
    assert h._pending_task.get("text") == "delete everything", \
        "the pending action must survive unrelated input"
    assert not any(c[0] == "route" for c in client.calls), \
        "unrelated input must not be routed over a pending confirmation"
    assert any("pending confirmation" in d for _, d in overlay.statuses), \
        overlay.statuses


def test_affirm_runs_the_original_action(monkeypatch):
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={"delete": _task_route("delete everything")})
    h._process_transcript("delete everything")
    h._process_transcript("open youtube")   # unrelated: pending survives
    h._process_transcript("yes")
    assert client.runs == ["delete everything"], \
        "affirm must run the ORIGINAL staged action, not a later utterance"
    assert h._pending_task == {}


def test_deny_cancels_without_running(monkeypatch):
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={"delete": _task_route("delete everything")})
    h._process_transcript("delete everything")
    h._process_transcript("no")
    assert client.runs == []
    assert h._pending_task == {}
    assert any(d == "task cancelled" for _, d in overlay.statuses)


def test_expiry_is_honest_and_never_runs(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._pending_task = {"text": "delete everything", "request_id": "old",
                       "staged_at": time.monotonic() - 9999.0}
    h._process_transcript("yes")
    assert client.runs == [], "a stale confirmation must never execute"
    assert h._pending_task == {}
    assert any("expired" in d for _, d in overlay.statuses), overlay.statuses


def test_expired_pending_does_not_block_a_new_command(monkeypatch):
    """An abandoned confirmation must not wedge the wake path.

    Live 2026-10-29: an expired staged task made every later wake command
    answer only "say yes, no, or cancel". Expiry still never RUNS the stale
    action, but a new non-affirm command now clears it and proceeds.
    """
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={"open": {"mode": "answer",
                                      "question": "open youtube"}})
    h._pending_task = {"text": "delete everything", "request_id": "old",
                       "staged_at": time.monotonic() - 9999.0}
    h._process_transcript("open youtube")
    assert client.runs == [], "a stale confirmation must never execute"
    assert h._pending_task == {}, "the stale stage must be cleared"
    assert ("route", h.request_id, "open youtube") in client.calls, \
        "a new command must reach routing instead of being blocked"
    assert not any("pending confirmation" in d
                   for _, d in overlay.statuses), overlay.statuses


def test_new_command_stages_only_after_cancel(monkeypatch):
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={
            "delete": _task_route("delete everything"),
            "open": _task_route("open youtube")})
    h._process_transcript("delete everything")
    h._process_transcript("no")             # cancel clears the stage
    assert h._pending_task == {}
    h._process_transcript("open youtube")   # now a new command may stage
    assert h._pending_task.get("text") == "open youtube"


def test_no_pending_path_unchanged(monkeypatch):
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={"type this": {"mode": "dictate",
                                           "transcript": "hello world"}})
    h._process_transcript("type this for me: hello world")
    assert desktop.inserted == ["hello world"]
    assert ("route", h.request_id, "type this for me: hello world") in client.calls
    assert h._pending_task == {}


# -- wake path ---------------------------------------------------------------

def test_wake_pending_guard_does_not_execute_unrelated_command(monkeypatch):
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={"delete": _task_route("delete everything")})
    traces = []
    monkeypatch.setattr(dh, "_trace",
                        lambda stage, **kw: traces.append(stage))

    h.on_wake_command_routed("delete everything", {"sessionKey": "sess-1"})
    _join_wake(h)
    assert h._pending_task.get("text") == "delete everything"

    h.on_wake_command_routed("open youtube", {"sessionKey": "sess-1"})
    _join_wake(h)
    assert client.runs == [], "wake must not execute a task over a pending"
    assert "wake.pending_guard" in traces, traces
    assert h._pending_task.get("text") == "delete everything"


def test_two_sessions_keep_independent_pendings(monkeypatch):
    h, overlay, client, desktop = _helper(
        monkeypatch, routes={
            "delete": _task_route("delete everything"),
            "format": _task_route("format the disk")})

    h.on_wake_command_routed("delete everything", {"sessionKey": "A"})
    _join_wake(h)
    h.on_wake_command_routed("format the disk", {"sessionKey": "B"})
    _join_wake(h)
    assert h._sessions.fsm("A").pending().text == "delete everything"
    assert h._sessions.fsm("B").pending().text == "format the disk"

    h.on_wake_command_routed("yes", {"sessionKey": "A"})
    _join_wake(h)
    assert client.runs == ["delete everything"]
    assert h._sessions.fsm("A").pending() is None
    assert h._sessions.fsm("B").pending().text == "format the disk", \
        "an unrelated session's pending must be untouched"
