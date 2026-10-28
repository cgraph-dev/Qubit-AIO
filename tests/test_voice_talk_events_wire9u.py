"""Talk events wired into the desktop-helper turn lifecycle.

Hermetic: no X, no mic, no socket, no model. Drives a stubbed turn through
:class:`~voicebridge.desktop_helper.DesktopHelper` and asserts the typed talk
boundaries reach the sequencer in order, plus that a throwing sequencer can
never break a turn (observability is fail-soft).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
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
        self.history = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        self.previews.append(payload)

    def show_error(self, code, detail):
        self.errors.append((code, detail))

    def append_history(self, line):
        self.history.append(line)

    def show(self):
        pass

    def hide(self):
        pass

    def close(self):
        pass


class FakeClient:
    def __init__(self, *, answer="It is 14 degrees in Bucharest."):
        self.calls = []
        self._answer = answer

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self, **kw):
        self.calls.append(("begin",))
        return {"ok": True, "request_id": "req-1"}

    def capture(self, rid):
        self.calls.append(("capture", rid))
        return {"ok": True, "selection": {"available": False,
                                          "captured_via": "none",
                                          "app_class": "code"}}

    def mint_stt_ticket(self):
        self.calls.append(("ticket",))
        return "ticket-123"

    def route(self, rid, transcript):
        self.calls.append(("route", rid, transcript))
        return {"ok": True, "mode": "answer", "question": transcript}

    def ask_result(self, question, rid):
        self.calls.append(("ask_result", question))
        return {"answer": self._answer, "spoken": False}

    def task(self, text, *, confirm=True, request_id=""):
        self.calls.append(("task", text))
        return {"ok": True, "reply": "Task complete."}

    def speak(self, text):
        self.calls.append(("speak", text))
        return b"RIFF....WAVEfmt "

    def cancel(self, rid):
        self.calls.append(("cancel", rid))
        return {"ok": True}


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def is_fullscreen(self, window_id=""):
        return False

    def set_clipboard(self, text):
        return True

    def insert_text(self, *args, **kwargs):
        raise AssertionError("the answer path must never type into the field")


class FakeMic:
    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        return None

    def stop(self):
        pass


class FakeStt:
    error = None


class RecordingTalk:
    """Stand-in sequencer that records emit types (and can be made to throw)."""

    def __init__(self, *, raise_on_emit=False, raise_on_begin=False):
        self.events = []
        self.begins = 0
        self.ends = 0
        self.turn = None
        self._raise_emit = raise_on_emit
        self._raise_begin = raise_on_begin

    def begin_turn(self):
        if self._raise_begin:
            raise RuntimeError("begin boom")
        self.begins += 1
        self.turn = f"t{self.begins}"
        return self.turn

    def end_turn(self):
        self.ends += 1
        turn, self.turn = self.turn, None
        return turn

    def emit(self, event_type, *, journal=False, **fields):
        if self._raise_emit:
            raise RuntimeError("emit boom")
        self.events.append(event_type)
        return {"type": event_type, "seq": len(self.events),
                "turn_id": self.turn, **fields}

    def snapshot(self):
        return {"events": tuple({"type": t} for t in self.events)}

    def types(self):
        return list(self.events)


class _FakePopen:
    def __init__(self, *args, **kwargs):
        pass

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


def _make(monkeypatch, *, talk=None, client=None):
    monkeypatch.setattr(
        dh, "transcribe_once",
        lambda c, mic, stop, **kw: (kw.get("on_partial")
                                    and kw["on_partial"]("what is"),
                                    "what is the weather")[1])
    monkeypatch.setattr(dh.DesktopHelper, "_deliver_fired_timers",
                        lambda self: [])
    overlay = FakeOverlay()
    client = client or FakeClient()
    helper = dh.DesktopHelper(
        FakeCfg(), client=client, overlay=overlay, desktop=FakeDesktop(),
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt(),
        talk=talk)
    return helper, overlay, client


def _drive_answer_turn(monkeypatch, *, talk, client=None):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    helper, overlay, client = _make(monkeypatch, talk=talk, client=client)
    helper._start_listening()
    if helper._worker is not None:
        helper._worker.join(timeout=5)
    return helper, overlay, client


def test_answer_turn_emits_boundaries_in_order(monkeypatch):
    talk = RecordingTalk()
    helper, overlay, client = _drive_answer_turn(monkeypatch, talk=talk)

    assert talk.begins == 1, "one turn must be begun per activation"
    assert talk.types() == [
        "input.speech_started",
        "transcript.final",
        "response.started",
        "response.audio.delta",
        "playback.started",
        "playback.stopped",
        "response.done",
    ], talk.types()
    assert any(c[0] == "ask_result" for c in client.calls)


def test_capture_stop_emits_speech_stopped(monkeypatch):
    talk = RecordingTalk()
    helper, overlay, client = _drive_answer_turn(monkeypatch, talk=talk)
    helper._stop_listening()
    assert "input.speech_stopped" in talk.types()


def test_task_turn_emits_tool_call_and_result(monkeypatch):
    talk = RecordingTalk()
    helper, overlay, client = _drive_answer_turn(monkeypatch, talk=talk)
    client.calls.clear()
    helper._run_task("open a text document")
    types = talk.types()
    assert types.index("tool.call") < types.index("tool.result")
    assert ("task", "open a text document") in client.calls


def test_speak_emits_interrupt_on_barge_in(monkeypatch):
    class LoudMic:
        frame_ms = 300

        def start(self):
            pass

        def read_frame(self, timeout=0.2):
            return b"\x00\x40" * 4800  # loud s16 tone

        def stop(self):
            pass

    class BargePopen(_FakePopen):
        def __init__(self, *args, **kwargs):
            self._n = 0

        def poll(self):
            self._n += 1
            return None if self._n == 1 else 0

    talk = RecordingTalk()
    helper, overlay, client = _make(monkeypatch, talk=talk)

    class FakeWake:
        phrase = "hey qubit"

        def _do_transcribe(self, pcm):
            return "hey qubit"

        def expect_reply(self, timeout_s=None):
            pass

    helper._wake = FakeWake()
    helper._mic_factory = lambda: LoudMic()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "Popen", BargePopen)
    helper._talk_begin()
    helper._speak("hello there")
    assert "interrupt" in talk.types()
    assert helper._barge_interrupted is True


def test_a_throwing_sequencer_never_breaks_a_turn(monkeypatch):
    talk = RecordingTalk(raise_on_emit=True, raise_on_begin=True)
    helper, overlay, client = _drive_answer_turn(monkeypatch, talk=talk)
    # The turn still completed: the answer was requested and surfaced.
    assert any(c[0] == "ask_result" for c in client.calls), client.calls
    assert overlay.previews, "the answer preview must still render"
    assert helper.state != helper.PROCESSING


def test_helper_constructs_one_sequencer_by_default(monkeypatch):
    created = {}

    class SpySequencer(RecordingTalk):
        def __init__(self):
            super().__init__()
            created["made"] = True

    monkeypatch.setattr(dh, "TalkEventSequencer", SpySequencer)
    helper = dh.DesktopHelper(
        FakeCfg(), client=FakeClient(), overlay=FakeOverlay(),
        desktop=FakeDesktop(), mic_factory=lambda: FakeMic(),
        stt_factory=lambda t: FakeStt())
    assert created.get("made") is True
    assert isinstance(helper._talk, SpySequencer)


def test_talk_events_accessor_and_talk_state():
    helper = dh.DesktopHelper(
        FakeCfg(), client=FakeClient(), overlay=FakeOverlay(),
        desktop=FakeDesktop(), mic_factory=lambda: FakeMic(),
        stt_factory=lambda t: FakeStt())
    helper._talk_begin()
    helper._talk_emit("input.speech_started")
    helper._talk_emit("transcript.final")
    assert [e["type"] for e in helper.talk_events()] == [
        "input.speech_started", "transcript.final"]
    assert helper.talk_state() == "thinking"
    helper._talk_emit("response.done")
    assert helper.talk_state() == "idle"


def test_talk_accessor_is_fail_soft_on_a_broken_sequencer():
    class Broken:
        def snapshot(self):
            raise RuntimeError("no snapshot")

        def end_turn(self):
            raise RuntimeError("no end")

        def begin_turn(self):
            raise RuntimeError("no begin")

        def emit(self, *args, **kwargs):
            raise RuntimeError("no emit")

    helper = dh.DesktopHelper(
        FakeCfg(), client=FakeClient(), overlay=FakeOverlay(),
        desktop=FakeDesktop(), mic_factory=lambda: FakeMic(),
        stt_factory=lambda t: FakeStt(), talk=Broken())
    helper._talk_begin()
    helper._talk_emit("error")
    helper._talk_end()
    assert helper.talk_events() == ()
    assert helper.talk_state() == "idle"
