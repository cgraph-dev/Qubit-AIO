"""Slice 1 — barge-in: confident speech stops helper TTS (9u).

While the helper speaks a reply, 150-300 ms of confident mic speech stops
playback and yields. Silence/echo-level audio never interrupts; a disabled
flag restores the old blocking playback. All fakes: no audio, no process.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _frame(rms: float, ms: int = 300, rate: int = 16000) -> bytes:
    amp = max(0, min(32767, int(rms * 32767)))
    n = int(rate * ms / 1000)
    return struct.pack(f"<{n}h", *([amp] * n))


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


def _cfg(**ve):
    base = {"enabled": True, "speak_answers": True}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


def test_barge_in_trigger_sustained_loud_speech():
    from voicebridge import desktop_helper as DH
    w = DH.BargeInWatcher(threshold=0.20, min_ms=200, frame_ms=300)
    assert w.feed(0.01, 300) is False  # silence
    assert w.feed(0.50, 300) is True  # one confident 300 ms frame >= 200 ms


def test_barge_in_ignores_short_blip():
    from voicebridge import desktop_helper as DH
    w = DH.BargeInWatcher(threshold=0.20, min_ms=200, frame_ms=100)
    assert w.feed(0.50, 100) is False  # 100 ms < 200 ms
    assert w.feed(0.01, 100) is False  # gap resets the run
    assert w.feed(0.50, 100) is False  # fresh 100 ms run, not enough
    assert w.feed(0.50, 100) is True  # 200 ms cumulative


def test_barge_in_ignores_echo_level_audio():
    from voicebridge import desktop_helper as DH
    w = DH.BargeInWatcher(threshold=0.20, min_ms=200, frame_ms=300)
    for _ in range(10):
        assert w.feed(0.08, 300) is False  # sustained echo-level, never fires


def test_frame_rms_measures_s16():
    from voicebridge import desktop_helper as DH
    assert DH.frame_rms(_frame(0.0)) == 0.0
    got = DH.frame_rms(_frame(0.5))
    assert 0.45 < got < 0.55, got


class FakeProc:
    def __init__(self):
        self.terminated = False
        self.waited = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self.waited = True
        self._alive = False
        return 0


class FakeMic:
    def __init__(self, frames):
        self._frames = list(frames)
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def read_frame(self, timeout=0.5):
        if self._frames:
            return self._frames.pop(0)
        return None


class FakeClient:
    def __init__(self, wav=b"RIFF" + b"\x00" * 100):
        self._wav = wav

    def speak(self, text):
        return self._wav


class FakeOverlay:
    def __init__(self):
        self.statuses = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        pass

    def show_error(self, code, detail):
        pass

    def append_history(self, line):
        pass


def _helper(monkeypatch, cfg, mic, procs):
    from voicebridge import desktop_helper as DH

    class FakeDesktop:
        def session(self):
            return {"session_type": "x11"}

        def _tool(self, name):
            return False

    helper = DH.DesktopHelper(cfg, client=FakeClient(),
                              overlay=FakeOverlay(),
                              desktop=FakeDesktop(),
                              mic_factory=lambda: mic)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/paplay")

    class FakePopen:
        def __init__(self, argv, **kw):
            self._inner = FakeProc()
            procs.append(self._inner)

        def poll(self):
            # Die after the mic is exhausted so the loop always ends.
            if not mic._frames:
                self._inner._alive = False
            return self._inner.poll()

        def terminate(self):
            self._inner.terminate()

        def wait(self, timeout=None):
            return self._inner.wait(timeout=timeout)

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    return helper


def test_speak_loud_speech_interrupts_playback(monkeypatch):
    mic = FakeMic([_frame(0.01), _frame(0.60)])
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs, "expected playback to start"
    assert procs[0].terminated is True, "loud speech must stop playback"
    assert helper._speaking is False, "speaking flag must clear"


def test_speak_silence_plays_through(monkeypatch):
    mic = FakeMic([_frame(0.01)] * 5)
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs, "expected playback to start"
    assert procs[0].terminated is False, "silence must not interrupt"
    assert procs[0].waited is True, "playback must complete normally"


def test_speak_barge_in_disabled_plays_through(monkeypatch):
    mic = FakeMic([_frame(0.90)] * 5)  # even loud speech must not interrupt
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in=False), mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs and procs[0].terminated is False
    assert mic.started is False, "disabled barge-in must not open the mic"


# Lane 3 (2026-10-03): barge-in threshold calibrated to measured levels.
#
# Journal proof: owner speech peaks at 0.5 (max-abs) through the live
# ParecMic chain (gain 12, AGC off) while the 60 s drone baseline never
# exceeds RMS 0.073 and speaker echo never exceeds RMS 0.097. The old
# default (0.20) sat above every owner word at seating distance, so the
# first "stop" during TTS died silently (wake yields while _speaking, the
# watcher never tripped) and the louder retry arrived after playback as a
# new command. 0.10 sits above echo/drone with margin and below owner
# speech even when the word straddles two 300 ms frames.
CALIBRATED_BARGE_THRESHOLD = 0.10


def test_barge_default_threshold_is_calibrated():
    """The shipped default must match the measured calibration (0.10)."""
    from voicebridge import desktop_helper as DH
    assert DH.BargeInWatcher().threshold == CALIBRATED_BARGE_THRESHOLD
    probe = DH.DesktopHelper.__new__(DH.DesktopHelper)
    probe.cfg = _DefaultCfg()
    enabled, threshold, _min_ms = DH.DesktopHelper._barge_in_settings(probe)
    assert enabled is True
    assert threshold == CALIBRATED_BARGE_THRESHOLD


class _DefaultCfg:
    """Cfg with no barge keys: exercises the shipped defaults."""

    def get(self, *path, default=None):
        if tuple(path) == ("voice_edit", "enabled"):
            return True
        if tuple(path) == ("voice_edit", "speak_answers"):
            return True
        return default


def test_barge_owner_level_speech_interrupts_at_default(monkeypatch):
    """Owner-distance speech (RMS 0.13, one 300 ms frame) stops playback."""
    mic = FakeMic([_frame(0.01), _frame(0.13)])  # RMS 0.13: owner word
    procs = []
    helper = _helper(monkeypatch, _DefaultCfg(), mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs, "expected playback to start"
    assert procs[0].terminated is True, "owner speech must stop playback"
    assert helper._barge_interrupted is True
    assert helper._speaking is False, "speaking flag must clear"


def test_barge_echo_floor_never_trips_at_default(monkeypatch):
    """Sustained echo/drone-level audio (RMS ~0.09) plays through."""
    mic = FakeMic([_frame(0.09)] * 5)  # RMS 0.09: above the drone max
    procs = []
    helper = _helper(monkeypatch, _DefaultCfg(), mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs, "expected playback to start"
    assert procs[0].terminated is False, "echo floor must not interrupt"
    assert procs[0].waited is True, "playback must complete normally"
