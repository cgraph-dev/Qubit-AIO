"""Wake reliability fixes (2026-10-29): barge-in, accurate decode, no re-wake.

Live evidence this pins:

* ``voice_edit.wake.busy_skip`` right after ``voice_edit.helper.speak``: a
  wake spoken while the assistant was talking was dropped, so the owner had
  to wait for the reply to finish and say it twice.
* ``voice_edit.wake.transcript`` "One GitHub" (owner said "open GitHub"):
  the KWS-miss probe preferred the streaming decoder.
* A bare wake arms the next utterance; an expired staged confirmation must
  not block a new command.

Hermetic: no microphone, no socket, no model, no journal writes.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.voice_edit import wake as wake_mod
from voicebridge.voice_edit.wake import WakeMonitor


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {
            "voice_edit": {"beep": False, "stt_mode": "accurate",
                           "wake": {"enabled": True, "vad": "energy"}}}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


def loud_frame(ms=300, amp=8000):
    n = int(16000 * ms / 1000)
    return struct.pack("<%dh" % n, *([amp] * n))


def silent_frame(ms=300):
    n = int(16000 * ms / 1000)
    return b"\x00\x00" * n


class SeqMic:
    def __init__(self, frames):
        self._frames = list(frames)
        self.frame_ms = 300

    def start(self):
        pass

    def stop(self):
        pass

    def read_frame(self, timeout=0.2):
        return self._frames.pop(0) if self._frames else None


class FakeKws:
    def __init__(self, hits=0):
        self._hits = hits
        self._fed = 0

    def feed(self, samples):
        self._fed += 1
        if self._fed <= self._hits:
            return "hey qubit"
        return None

    def reset(self):
        pass


def _run_loop(monitor, done, timeout=5.0):
    import time as _t
    monitor.start()
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        if done():
            break
        _t.sleep(0.02)
    monitor.stop()


# ---------------------------------------------------------------------------
# 1. wake during TTS is not dropped: the barge-in gate stops and arms
# ---------------------------------------------------------------------------
def _frame(rms, ms=300):
    amp = max(0, min(32767, int(rms * 32767)))
    n = int(16000 * ms / 1000)
    return struct.pack(f"<{n}h", *([amp] * n))


class FakeProc:
    def __init__(self):
        self.terminated = False
        self.waited = False
        self.signals = []
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def send_signal(self, signum):
        self.signals.append(int(signum))

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self.waited = True
        self._alive = False
        return 0


class BargeMic:
    def __init__(self, frames):
        self._frames = list(frames)
        self.frame_ms = 300

    def start(self):
        pass

    def stop(self):
        pass

    def read_frame(self, timeout=0.5):
        return self._frames.pop(0) if self._frames else None


class BargeWake:
    def __init__(self, text="hey qubit"):
        self.text = text
        self.phrase = "hey qubit"
        self.armed = []

    def _do_transcribe(self, pcm):
        return self.text

    def expect_reply(self, timeout_s=None):
        self.armed.append(timeout_s)


class BargeOverlay:
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


class BargeClient:
    def speak(self, text):
        return b"RIFF" + b"\x00" * 100


def _barge_helper(monkeypatch, mic, procs):
    class FakeDesktop:
        def session(self):
            return {"session_type": "x11"}

        def _tool(self, name):
            return False

    cfg = FakeCfg({"voice_edit": {"enabled": True, "speak_answers": True}})
    helper = dh.DesktopHelper(cfg, client=BargeClient(),
                              overlay=BargeOverlay(), desktop=FakeDesktop(),
                              mic_factory=lambda: mic)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/paplay")

    class FakePopen:
        def __init__(self, argv, **kw):
            self._inner = FakeProc()
            procs.append(self._inner)

        def poll(self):
            if not mic._frames:
                self._inner._alive = False
            return self._inner.poll()

        def send_signal(self, signum):
            self._inner.send_signal(signum)

        def terminate(self):
            self._inner.terminate()

        def wait(self, timeout=None):
            return self._inner.wait(timeout=timeout)

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    helper._wake = BargeWake()
    return helper


def test_wake_during_tts_stops_playback_and_arms(monkeypatch):
    """Owner speech at the measured live level (RMS 0.08) interrupts TTS.

    This is the regression: the old 0.10 gate sat above the owner's level, so
    a wake spoken while the assistant talked was silently dropped and the
    owner had to wait for the reply to end and repeat.
    """
    mic = BargeMic([_frame(0.01), _frame(0.08)])
    procs = []
    helper = _barge_helper(monkeypatch, mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs, "expected playback to start"
    assert procs[0].terminated is True, "a wake during TTS must stop playback"
    assert helper._barge_interrupted is True
    assert helper._wake.armed == [8.0], "a bare wake must arm the next command"


def test_post_tts_tail_is_short_not_the_old_five_seconds(tmp_path, monkeypatch):
    """After a short reply the next wake is not blocked for 5 s."""
    import json
    import time

    journal = tmp_path / "events.jsonl"
    now = time.time()
    journal.write_text(
        json.dumps({"ts": now - 0.3, "event": "voice_edit.helper.speak_done"})
        + "\n")
    import voicebridge.config as VC
    monkeypatch.setattr(VC, "canonical_state_dir", lambda: tmp_path)
    probe = dh.DesktopHelper.__new__(dh.DesktopHelper)
    probe._barge_interrupted = False
    probe._tts_echo_checked = 0.0
    assert probe._tts_echo_busy(window_s=5.0) is True  # inside 0.8 s tail
    probe._tts_echo_checked = 0.0
    # A ``speak`` START alone (no done) is covered by ``_speaking`` and must
    # not block wake for 5 s after the reply already ended.
    journal.write_text(
        json.dumps({"ts": now - 1.0, "event": "voice_edit.helper.speak"})
        + "\n")
    probe._tts_echo_checked = 0.0
    assert probe._tts_echo_busy(window_s=5.0) is False


# ---------------------------------------------------------------------------
# 2. the wake fallback uses the accurate decoder, not streaming
# ---------------------------------------------------------------------------
def test_kws_miss_uses_accurate_decoder_and_real_action(monkeypatch):
    """A KWS-miss probe decodes "Hey Qubit, open GitHub" accurately.

    The streaming engine (fake below) returns "One GitHub"; the accurate
    decoder returns the real phrase. The emitted command must be the real
    one, and the REAL local-action table must resolve it to GitHub.
    """
    import voicebridge.voice_edit.asr_accurate as A
    from voicebridge.local.actions import match

    monkeypatch.setattr(wake_mod, "_trace", lambda *a, **k: None)
    calls = {"accurate": 0, "streaming": 0}

    state = {"n": 0}

    def fake_sherpa(cfg, pcm, **kw):
        calls["accurate"] += 1
        state["n"] += 1
        return ("Hey Qubit, open GitHub" if state["n"] == 1 else "Hey Qubit"), ""

    def fake_stream(*a, **k):
        calls["streaming"] += 1
        return "One GitHub"

    monkeypatch.setattr(A, "transcribe_sherpa", fake_sherpa)
    import voicebridge.voice_edit.stt_client as SC
    monkeypatch.setattr(SC, "transcribe_once", fake_stream)
    monkeypatch.setattr(wake_mod, "_accurate_ready", lambda cfg: (False, "x"))

    commands = []
    m = WakeMonitor(
        FakeCfg(), on_command=commands.append, is_busy=lambda: False,
        mic_factory=lambda: SeqMic(
            [loud_frame(), loud_frame(), silent_frame(),
             silent_frame(), silent_frame()] * 2),
        kws=FakeKws(hits=0))
    _run_loop(m, lambda: bool(commands))

    assert commands == ["open GitHub"], commands
    assert calls["accurate"] >= 1
    assert calls["streaming"] == 0, "streaming must not decode the wake path"
    hit = match(commands[0])
    assert hit is not None and hit.name == "open_website"
    assert hit.params.get("url") == "https://github.com"
    assert match("One GitHub") is None


# ---------------------------------------------------------------------------
# 3. a bare wake arms the next utterance (no second wake)
# ---------------------------------------------------------------------------
def test_bare_wake_arms_next_utterance_without_second_wake(monkeypatch):
    import voicebridge.voice_edit.asr_accurate as A

    monkeypatch.setattr(wake_mod, "_trace", lambda *a, **k: None)
    replies = iter(["Hey Qubit", "open GitHub"])

    def fake_sherpa(cfg, pcm, **kw):
        return next(replies, ""), ""

    monkeypatch.setattr(A, "transcribe_sherpa", fake_sherpa)
    monkeypatch.setattr(wake_mod, "_accurate_ready", lambda cfg: (False, "x"))

    commands = []
    m = WakeMonitor(
        FakeCfg(), on_command=commands.append, is_busy=lambda: False,
        mic_factory=lambda: SeqMic(
            [loud_frame(), loud_frame(), silent_frame(),
             silent_frame(), silent_frame()] * 3),
        kws=FakeKws(hits=0))
    _run_loop(m, lambda: bool(commands))

    assert commands == ["open GitHub"], commands
