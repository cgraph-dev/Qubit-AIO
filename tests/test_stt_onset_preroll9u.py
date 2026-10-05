"""Onset pre-roll: first words must survive record-start on both voice paths.

Owner report: BOTH the dashboard Voice button path AND the Right-Ctrl helper
path lose/warp the first 3-7 words; mid-stream accuracy is excellent. Measured
2026-10-04 on the real production engine (nemotron-560ms) with piper TTS
("Hello Qubit, how are you today, my name is Lukas"):

* truncated-0.5s + cold start -> "Bit how are you today? ..." (onset mangled)
* full audio + cold start    -> "Low qubit. How are you ..." (cold-start warp)
* truncated-0.5s + primed    -> "Qubit. How are you ..." ("Hello" unrecoverable)
* full audio + primed         -> verbatim ("Lucas" for "Lukas" homophone only)

Neither fix alone suffices: the capture gap (never recorded) needs a pre-roll
ring buffer (prepend, never cut — the wake loop's §67 mirror), and the cold
decoder needs warmup frames. AGC attack was measured and RULED OUT (cold vs
room-primed onset gain identical: 1.0 loud, 3.59 quiet).

Hermetic: no socket, mic, or model. Burst-at-t0 = speech frames delivered at
the instant of record-start; red = first frames lost/mangled, green = verbatim.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.asr.streaming import OnlineStreamingSession


# ---------------------------------------------------------------------------
# PreRollMic: rolling ~1s ring buffer prepended at record start.
# ---------------------------------------------------------------------------

def _frame(fill: int) -> bytes:
    return (np.full(4800, fill, dtype="<i2")).tobytes()


class _ScriptMic:
    """Mic double playing a canned script, recording lifecycle."""

    def __init__(self, script):
        self._script = list(script)
        self.events = []
        self.live_reads = 0

    def start(self):
        self.events.append("mic_start")

    def read_frame(self, timeout=0.2):
        if self._script:
            return self._script.pop(0)
        self.live_reads += 1
        return _frame(9)

    def stop(self):
        self.events.append("mic_stop")


def test_preroll_yields_buffered_frames_before_live():
    from voicebridge.voice_edit.stt_client import PreRollMic
    mic = _ScriptMic([])
    pre = PreRollMic(mic, pre_roll=[_frame(1), _frame(2)])
    got = [pre.read_frame() for _ in range(4)]
    assert got[0] == _frame(1)
    assert got[1] == _frame(2)
    # Then live audio flows (never replays the buffer).
    assert got[2] == _frame(9)
    assert got[3] == _frame(9)
    assert mic.live_reads == 2


def test_preroll_cap_drops_oldest_never_newest():
    from voicebridge.voice_edit.stt_client import PreRollMic
    mic = _ScriptMic([])
    # 1 s ring at 300 ms frames ~= 4 frames; older room tone must yield to
    # newer audio, but the newest (speech onset) is never discarded.
    pre = PreRollMic(mic, pre_roll=[_frame(i) for i in range(10)],
                     max_frames=4)
    got = [pre.read_frame() for _ in range(5)]
    assert got[:4] == [_frame(6), _frame(7), _frame(8), _frame(9)]
    assert got[4] == _frame(9)  # live after the buffer drains


def test_preroll_empty_passthrough_and_lifecycle():
    from voicebridge.voice_edit.stt_client import PreRollMic
    mic = _ScriptMic([])
    pre = PreRollMic(mic, pre_roll=[])
    pre.start()
    assert pre.read_frame() == _frame(9)
    pre.stop()
    assert mic.events == ["mic_start", "mic_stop"]


def test_preroll_never_raises_on_faulty_mic():
    from voicebridge.voice_edit.stt_client import PreRollMic

    class _Faulty:
        def read_frame(self, timeout=0.2):
            raise OSError("mic gone")

        def start(self):
            pass

        def stop(self):
            pass

    pre = PreRollMic(_Faulty(), pre_roll=[_frame(1)])
    assert pre.read_frame() == _frame(1)
    assert pre.read_frame() is None


# ---------------------------------------------------------------------------
# Decoder priming: the online transducer needs left context on its first
# real chunk (measured: full-audio cold start warped "Hello" -> "Low").
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self):
        self.waveforms = []

    def accept_waveform(self, sample_rate, waveform):
        self.waveforms.append(np.asarray(waveform, dtype=np.float32))


class _FakeRec:
    def __init__(self):
        self.ready_calls = 0

    def is_ready(self, stream):
        return False

    def decode_stream(self, stream):
        self.ready_calls += 1

    def get_result(self, stream):
        return ""


def test_online_session_primes_encoder_before_first_speech():
    stream = _FakeStream()
    OnlineStreamingSession(_FakeRec(), stream, sample_rate=16000)
    assert len(stream.waveforms) == 1
    prime = stream.waveforms[0]
    assert prime.size == int(16000 * 0.3)
    assert bool((prime == 0.0).all())


def test_online_session_first_real_chunk_follows_prime():
    stream = _FakeStream()
    sess = OnlineStreamingSession(_FakeRec(), stream, sample_rate=16000)
    chunk = (np.ones(4800, dtype=np.float32) * 0.1)
    sess.accept(chunk)
    assert len(stream.waveforms) == 2
    assert bool((stream.waveforms[0] == 0.0).all())
    assert np.array_equal(stream.waveforms[1], chunk)


# ---------------------------------------------------------------------------
# Helper path: the mic must open on the key-DOWN edge, BEFORE the ticket
# mint — today the ticket HTTP runs first and the onset is never recorded.
# ---------------------------------------------------------------------------

class _FakeCfg:
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


class _FakeOverlay:
    def set_status(self, state, detail=""):
        pass

    def show_preview(self, payload):
        pass

    def show_error(self, code, detail):
        pass


class _OrderClient:
    def __init__(self, events):
        self._events = events

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        self._events.append("begin")
        return {"ok": True, "request_id": "req-1"}

    def capture(self, rid):
        return {"ok": True, "selection": {"available": False}}

    def mint_stt_ticket(self):
        self._events.append("ticket")
        return "ticket-123"


class _OrderDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True}

    def focused_window(self):
        return {"window_id": "", "app_class": ""}


def test_helper_opens_mic_before_ticket_mint(monkeypatch):
    events = []

    def _mic_factory():
        mic = _ScriptMic([_frame(7)])
        orig_start = mic.start

        def _start():
            events.append("mic_start")
            return orig_start()

        mic.start = _start  # type: ignore[method-assign]
        return mic

    helper = dh.DesktopHelper(
        _FakeCfg(), client=_OrderClient(events), overlay=_FakeOverlay(),
        desktop=_OrderDesktop(), mic_factory=_mic_factory,
        stt_factory=lambda t: None)
    monkeypatch.setattr(
        dh, "transcribe_once",
        lambda client, mic, stop, **kw: "")
    monkeypatch.setattr(
        "voicebridge.voice_edit.stt_client.transcribe_accurate",
        lambda cfg, pcm, **kw: ("", "test-quiet"))
    helper.request_id = "req-1"
    helper._stop = threading.Event()
    helper.snapshot = None
    helper._transcribe()
    assert "mic_start" in events and "ticket" in events
    assert events.index("mic_start") < events.index("ticket"), events


def test_helper_preroll_frames_reach_the_stream(monkeypatch):
    """Burst-at-t0: speech present at key-DOWN must be in the streamed audio."""
    streamed = []

    def _mic_factory():
        return _ScriptMic([_frame(1), _frame(2)])

    helper = dh.DesktopHelper(
        _FakeCfg(), client=_OrderClient([]), overlay=_FakeOverlay(),
        desktop=_OrderDesktop(), mic_factory=_mic_factory,
        stt_factory=lambda t: None)

    def _fake_transcribe(client, mic, stop, **kw):
        # Drain exactly the pre-roll + one live frame, like the WS sender.
        for _ in range(3):
            frame = mic.read_frame(timeout=0.1)
            if frame:
                streamed.append(frame)
        return ""

    monkeypatch.setattr(dh, "transcribe_once", _fake_transcribe)
    monkeypatch.setattr(
        "voicebridge.voice_edit.stt_client.transcribe_accurate",
        lambda cfg, pcm, **kw: ("", "test-quiet"))
    helper.request_id = "req-1"
    helper._stop = threading.Event()
    helper.snapshot = None
    # Simulate the key-DOWN edge pump having captured onset frames already.
    helper._pre_roll = [_frame(1), _frame(2)]
    helper._transcribe()
    assert streamed[0] == _frame(1)
    assert streamed[1] == _frame(2)
