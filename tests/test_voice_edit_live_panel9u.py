"""The Voice Edit panel streams the transcript while the user is speaking.

Regression (owner, 2026-10-26): words appeared only after the sentence
finished. The helper already received STT partials; they now reach the panel
before the final transcript, and the panel opens on the hotkey rather than at
preview time.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.voice_edit.schema import InsertionResult, SelectionSnapshot


class FakeCfg:
    def __init__(self):
        self._data = {"voice_edit": {"enabled": True, "history_panel": False}}

    def get(self, *path, default=None):
        node = self._data
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node


class RecordingOverlay:
    def __init__(self):
        self.events = []
        self.live = []
        self.levels = []
        self.statuses = []
        self.previews = []

    def show(self):
        self.events.append("show")

    def hide(self):
        self.events.append("hide")

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))
        self.events.append(("status", state))

    def set_live_transcript(self, text, final=False):
        self.live.append((text, bool(final)))
        self.events.append(("live", text, bool(final)))

    def set_level(self, level):
        self.levels.append(level)

    def show_preview(self, payload):
        self.previews.append(payload)

    def show_error(self, code, detail):
        self.events.append(("error", code))

    def append_history(self, line):
        pass

    def close(self):
        pass


class FakeClient:
    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        return {"ok": True, "request_id": "req-live"}

    def capture(self, rid):
        return {"ok": True, "selection": {"available": True,
                                          "captured_via": "clipboard",
                                          "app_class": "code"}}

    def mint_stt_ticket(self):
        return "ticket"

    def transform(self, rid, transcript):
        return {"ok": True, "mode": "preview", "original": "hello world",
                "proposed": "hello there", "model": "local",
                "provider": "ollama", "privacy_mode": "local",
                "detected": {"action": "rewrite"}}

    def cancel(self, rid):
        return {"ok": True}


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def insert_text(self, text, snapshot, cfg=None, method=""):
        return InsertionResult(ok=True, method="clipboard_paste",
                               app_class="code", focus_restored=True)

    def activate_window(self, window_id):
        return True

    def set_clipboard(self, text):
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


def _helper(monkeypatch, *, partials=("make it", "make it clearer"),
            final="make it clearer"):
    def fake_transcribe(client, mic, stop, **kw):
        for partial in partials:
            if kw.get("on_partial"):
                kw["on_partial"](partial)
        return final

    monkeypatch.setattr(dh, "transcribe_once", fake_transcribe)
    monkeypatch.setattr(dh.DesktopHelper, "_deliver_fired_timers",
                        lambda self: [])
    overlay = RecordingOverlay()
    helper = dh.DesktopHelper(
        FakeCfg(), client=FakeClient(), overlay=overlay, desktop=FakeDesktop(),
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt())
    return helper, overlay


def _drive_ptt(helper, overlay=None):
    helper.on_hotkey("ptt_start")
    if helper._worker is not None:
        helper._worker.join(timeout=5)
    helper.on_hotkey("ptt_end")
    deadline = time.monotonic() + 5
    while overlay is not None and not overlay.previews and time.monotonic() < deadline:
        time.sleep(0.01)


def test_partials_reach_the_panel_before_the_final_transcript(monkeypatch):
    helper, overlay = _helper(monkeypatch)

    _drive_ptt(helper, overlay)

    assert overlay.live[0] == ("", False)  # panel opens empty on the hotkey
    assert overlay.live[1:] == [
        ("make it", False),
        ("make it clearer", False),
        ("make it clearer", True),
    ]
    assert overlay.previews, "preview still ran"
    live_indexes = [i for i, e in enumerate(overlay.events)
                    if isinstance(e, tuple) and e[0] == "live"]
    final_index = max(live_indexes)
    assert live_indexes[0] < final_index


def test_panel_opens_on_the_hotkey_before_the_first_partial(monkeypatch):
    gate = threading.Event()

    def slow_transcribe(client, mic, stop, **kw):
        gate.wait(5)
        return "hello"

    monkeypatch.setattr(dh, "transcribe_once", slow_transcribe)
    monkeypatch.setattr(dh.DesktopHelper, "_deliver_fired_timers",
                        lambda self: [])
    overlay = RecordingOverlay()
    helper = dh.DesktopHelper(
        FakeCfg(), client=FakeClient(), overlay=overlay, desktop=FakeDesktop(),
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt())

    helper.on_hotkey("ptt_start")
    try:
        assert overlay.events and overlay.events[0] == "show"
        assert not overlay.live or all(not final for _, final in overlay.live)
    finally:
        gate.set()
        if helper._worker is not None:
            helper._worker.join(timeout=5)
        helper.on_hotkey("ptt_end")


def test_wake_turn_shows_the_final_words_immediately(monkeypatch):
    helper, overlay = _helper(monkeypatch)
    helper._wake_turn = lambda command: None  # do not run the model

    helper.on_wake_command("hello from the wake path")

    assert ("hello from the wake path", True) in overlay.live
    assert "show" in overlay.events


def test_capture_level_reaches_the_panel(monkeypatch):
    helper, overlay = _helper(monkeypatch)

    helper._note_capture_frame(0.62)

    assert overlay.levels and abs(overlay.levels[-1] - 0.62) < 1e-6
