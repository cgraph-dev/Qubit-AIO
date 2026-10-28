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


class FakeWake:
    def __init__(self, text="hey qubit"):
        self.text = text
        self.phrase = "hey qubit"
        self.armed = []

    def _do_transcribe(self, pcm):
        return self.text

    def expect_reply(self, timeout_s=None):
        self.armed.append(timeout_s)


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

        def send_signal(self, signum):
            self._inner.send_signal(signum)

        def terminate(self):
            self._inner.terminate()

        def wait(self, timeout=None):
            return self._inner.wait(timeout=timeout)

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    helper._wake = FakeWake()
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


def test_barge_in_ignores_speech_without_the_wake_phrase(monkeypatch):
    """Confident speech alone must not stop a reply; only "hey qubit" does."""
    mic = FakeMic([_frame(0.01), _frame(0.60)])
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._wake = FakeWake("this is just a normal sentence")
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs and procs[0].terminated is False
    assert helper._barge_interrupted is False
    import signal
    assert signal.SIGSTOP in procs[0].signals
    assert signal.SIGCONT in procs[0].signals


def test_speak_silence_plays_through(monkeypatch):
    mic = FakeMic([_frame(0.01)] * 5)
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert procs, "expected playback to start"
    assert procs[0].terminated is False, "silence must not interrupt"
    assert procs[0].waited is True, "playback must complete normally"


def test_barge_in_stopped_status_returns_to_idle(monkeypatch):
    """The HUD must not stay on "Stopped" forever after a barge-in.

    A bare wake after an interrupted reply used to leave the status pill
    stuck because nothing re-set it. The clear is idle-only and short.
    """
    import time

    mic = FakeMic([_frame(0.01), _frame(0.60)])
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._speak("hello there this is a long spoken reply with many words")
    assert any(s == "interrupted" for s, _ in helper.overlay.statuses)
    helper._schedule_interrupted_clear(0.05)
    time.sleep(0.2)
    assert helper.overlay.statuses[-1][0] == "idle"


def test_barge_in_arms_wake_for_the_interrupting_utterance(monkeypatch):
    """A barge-in opens the wake arm so its command is not discarded."""
    from voicebridge import desktop_helper as DH

    class W(FakeWake):
        pass

    mic = FakeMic([_frame(0.01), _frame(0.60)])
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._wake = W()
    helper._speak("hello there this is a long spoken reply with many words")
    assert helper._wake.armed == [8.0]


def test_barge_in_shortens_the_tts_echo_guard(monkeypatch):
    """After a barge-in the reply is gone; do not block wake for 5 s."""
    from voicebridge import desktop_helper as DH

    seen = {}
    monkeypatch.setattr(DH, "recent_tts_speak",
                        lambda path, window_s=5.0: seen.setdefault(
                            "window_s", window_s) and False)
    helper = _helper(monkeypatch, _cfg(), FakeMic([]), [])
    helper._tts_echo_checked = 0.0
    helper._barge_interrupted = True
    helper._tts_echo_busy()
    assert seen["window_s"] == 1.0


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


# ---------------------------------------------------------------------------
# Wave 69 — generation-fenced barge-in takeover.
#
# A wake heard during TTS used to only stop the reply: the command spoken in
# the gate was discarded (the user had to repeat it) and the interrupted
# worker could still land its stale PREVIEW/status over the next turn. The
# takeover now fences that worker by turn generation, clears the HUD, and
# either executes the already-spoken remainder once or arms for the next one.
# ---------------------------------------------------------------------------


def _wait_for(predicate, timeout=2.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_barge_in_wake_command_executes_the_spoken_remainder_once(monkeypatch):
    """hey qubit + command during TTS executes that command (not discarded)."""
    mic = FakeMic([_frame(0.01), _frame(0.60)])
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._wake = FakeWake("hey qubit open youtube")
    calls = []
    monkeypatch.setattr(helper, "on_wake_command",
                        lambda command, _barge=False: calls.append(
                            (command, _barge)))
    helper._speak("hello there this is a long spoken reply with many words")
    assert _wait_for(lambda: bool(calls)), "the interrupting command must run"
    assert calls == [("open youtube", True)]
    assert helper.state == helper.IDLE


def test_barge_in_bare_wake_arms_and_leaves_status_idle(monkeypatch):
    """A bare wake stops the reply, arms the next utterance, and clears HUD."""
    mic = FakeMic([_frame(0.01), _frame(0.60)])
    procs = []
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), mic, procs)
    helper._wake = FakeWake("hey qubit")
    helper._speak("hello there this is a long spoken reply with many words")
    assert helper._wake.armed == [8.0]
    assert helper.state == helper.IDLE
    assert helper.overlay.statuses[-1][0] == "idle"


def test_second_barge_in_wake_cannot_double_emit(monkeypatch):
    """Only one takeover may run in a window; a second wake is ignored."""
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), FakeMic([]), [])
    calls = []
    monkeypatch.setattr(helper, "on_wake_command",
                        lambda command, _barge=False: calls.append(command))
    assert helper._begin_barge_takeover("open youtube") is True
    assert helper._begin_barge_takeover("open youtube") is False
    assert _wait_for(lambda: bool(calls))
    import time
    time.sleep(0.1)
    assert calls == ["open youtube"]


def test_wake_monitor_tail_cannot_reemit_the_takeover_command(monkeypatch):
    """The monitor's late tail of the same utterance is deduped, not run again."""
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), FakeMic([]), [])
    opened = []
    monkeypatch.setattr(helper, "_open_request",
                        lambda: opened.append(1) or False)
    helper._begin_barge_takeover("open youtube")
    assert _wait_for(lambda: bool(opened))
    assert opened == [1]
    # A second, non-barge emission of the SAME text right afterwards is the
    # captured tail, not a new command: it must never reach _open_request.
    helper.on_wake_command("open youtube")
    assert opened == [1]


def test_interrupted_turn_cannot_resurrect_its_preview(monkeypatch):
    """A worker that resumes after the barge-in must not overwrite the turn."""
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), FakeMic([]), [])
    calls = []
    monkeypatch.setattr(helper, "on_wake_command",
                        lambda command, _barge=False: calls.append(command))

    def fake_route(_rid, _text):
        return {"mode": "answer", "question": "what is up"}

    def fake_answer(_question, *, mode="answer"):
        # Simulate the barge-in landing while the old reply is speaking.
        helper._begin_barge_takeover("make it dark")
        return

    monkeypatch.setattr(helper.client, "route", fake_route, raising=False)
    monkeypatch.setattr(helper, "_answer_question", fake_answer)
    helper._process_transcript("what is up")
    # The old worker's post-speak PREVIEW write is fenced.
    assert helper.state != helper.PREVIEW
    assert helper.state == helper.IDLE
    assert _wait_for(lambda: bool(calls))
    assert calls == ["make it dark"]


def test_stale_worker_finish_after_takeover_is_discarded(monkeypatch):
    """A late old-worker finish cannot reset the interrupting turn; no crash."""
    helper = _helper(monkeypatch, _cfg(), FakeMic([]), [])
    helper._turn_gen = 5
    old_gen = helper._turn_gen
    helper._begin_barge_takeover("")  # fences: _turn_gen becomes 6
    helper.state = helper.PROCESSING   # the interrupting turn is now working
    helper._finish_error(old_gen)      # old worker lands late
    assert helper.state == helper.PROCESSING
    gone = helper._barge_takeover_pending
    assert isinstance(gone, bool)
    helper._finish_error(helper._turn_gen)
    assert helper.state == helper.IDLE


def test_takeover_during_brain_work_suppresses_the_stale_reply(monkeypatch):
    """A wake during ask_result must not let the old turn speak its answer."""
    helper = _helper(monkeypatch, _cfg(barge_in_threshold=0.20,
                                       barge_in_ms=200), FakeMic([]), [])
    helper._wake = FakeWake("hey qubit")
    spoken = []

    def fake_ask_result(_question, _rid):
        # The user barges in while the brain is still working.
        helper._begin_barge_takeover("")
        return {"ok": True, "answer": "stale answer that must not be spoken"}

    monkeypatch.setattr(helper.client, "ask_result", fake_ask_result,
                        raising=False)
    helper._speak = lambda text: spoken.append(text)
    helper._answer_question("what is up")
    assert spoken == []
    assert helper.state == helper.IDLE
