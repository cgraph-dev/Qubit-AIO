"""Fired-timer voice reminders (Lane 2, 2026-10-03, no X/mic/audio/model).

The timer firer writes only the ledger (never chat/session state), so a
fired timer reaches the user on the helper answer path: every voice turn
pulls ``poll_fired_timers()`` and delivers fired timers as spoken
reminders (+ History row, audited), honoring sleep_mode (sleeping ->
panel/History only, never speech).

Hermetic: QUBIT_TIMER_STATE_DIR points at a scratch dir; fired records are
rigged through the REAL ledger+firer (schedule_timer + fire_timer with stub
notify/sound); speech is captured via a stubbed _speak (no audio).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh


class FakeCfg:
    def __init__(self, data=None):
        self._data = data if data is not None else {
            "voice_edit": {"enabled": True}}

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
        self.history = []
        self.shown = 0

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        self.previews.append(payload)

    def show_error(self, code, detail):
        self.errors.append((code, detail))

    def append_history(self, line):
        self.history.append(line)

    def show(self):
        self.shown += 1

    def close(self):
        pass


class FakeClient:
    def __init__(self):
        self.calls = []

    def speak(self, text):
        self.calls.append(("speak", text))
        return b""

    def route(self, rid, text):
        self.calls.append(("route", rid, text))
        return {"ok": True, "mode": "answer", "question": text}

    def ask_result(self, text, rid=""):
        self.calls.append(("ask_result", text, rid))
        return {"ok": True, "answer": "The answer.", "mode": "answer",
                "provider": "local", "model": "test", "spoken": False}


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def is_fullscreen(self, window_id=""):
        return False


def _helper(**cfg_kw):
    data = {"voice_edit": {"enabled": True}}
    data["voice_edit"].update(cfg_kw)
    overlay = FakeOverlay()
    client = FakeClient()
    notified = []
    helper = dh.DesktopHelper(
        FakeCfg(data), client=client, overlay=overlay,
        desktop=FakeDesktop(), mic_factory=lambda: None,
        stt_factory=lambda t: None,
        notify=lambda title, body: notified.append((title, body)))
    spoken = []
    helper._speak = lambda text: spoken.append(text)  # stubbed, no audio
    return helper, overlay, client, notified, spoken


def _rig_fired_timer(mins=1):
    """A genuine `fired` ledger record via the real schedule+fire path."""
    from voicebridge.local import actions as A
    rec = A.schedule_timer(mins)
    out = A.fire_timer(
        rec["id"],
        notify_fn=lambda title, body: {"ok": True},
        sound_fn=lambda: {"ok": True})
    assert out.get("fired") is True
    return rec["id"]


def test_fired_poll_delivers_spoken_reminder(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_TIMER_STATE_DIR", str(tmp_path))
    helper, overlay, client, notified, spoken = _helper()
    tid = _rig_fired_timer(mins=1)
    delivered = helper._deliver_fired_timers()
    assert len(delivered) == 1
    assert delivered[0]["id"] == tid
    assert delivered[0]["slept"] is False
    assert "1-minute timer is up" in delivered[0]["text"]
    assert spoken and "1-minute timer is up" in spoken[0]
    assert any("timer" in line for line in overlay.history)
    assert notified and "timer is up" in notified[0][1]


def test_empty_poll_is_silent(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_TIMER_STATE_DIR", str(tmp_path))
    helper, overlay, client, notified, spoken = _helper()
    assert helper._deliver_fired_timers() == []
    assert spoken == []
    assert overlay.history == []
    assert notified == []


def test_sleep_suppresses_speech_but_records(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_TIMER_STATE_DIR", str(tmp_path))
    helper, overlay, client, notified, spoken = _helper(sleep_mode=True)
    _rig_fired_timer(mins=5)
    delivered = helper._deliver_fired_timers()
    assert len(delivered) == 1
    assert delivered[0]["slept"] is True
    assert "5-minute timer is up" in delivered[0]["text"]
    assert spoken == []  # never speech while sleeping
    assert any("timer" in line for line in overlay.history)
    assert notified and "timer is up" in notified[0][1]


def test_each_timer_announced_once(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_TIMER_STATE_DIR", str(tmp_path))
    helper, overlay, client, notified, spoken = _helper()
    _rig_fired_timer(mins=1)
    assert len(helper._deliver_fired_timers()) == 1
    assert helper._deliver_fired_timers() == []  # second turn: silent
    assert len(spoken) == 1
    assert len(overlay.history) == 1


def test_voice_turn_pulls_fired_timers(monkeypatch, tmp_path):
    """The natural turn cadence speaks a fired timer AND answers the turn."""
    monkeypatch.setenv("QUBIT_TIMER_STATE_DIR", str(tmp_path))
    helper, overlay, client, notified, spoken = _helper()
    helper.request_id = ""
    _rig_fired_timer(mins=1)
    helper._process_transcript("what time is it")
    assert any("1-minute timer is up" in s for s in spoken)
    assert any("The answer." in s for s in spoken)


def test_delivery_capped_per_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_TIMER_STATE_DIR", str(tmp_path))
    helper, overlay, client, notified, spoken = _helper()
    for _ in range(5):
        _rig_fired_timer(mins=1)
    first = helper._deliver_fired_timers()
    assert 1 <= len(first) <= 3
    rest = helper._deliver_fired_timers()
    assert len(first) + len(rest) == 5  # drains, never lost
