"""Talk-meter level tests (TDD RED first): helper publish -> orb consume/render.

Hermetic: no mic, no display, no network, no service contact.
"""
from __future__ import annotations

import array
import time

from voicebridge import desktop_helper as DH
from voicebridge.orb import client as OC
from voicebridge.orb import ember as E


def _s16_frame(peak: int, n: int = 480) -> bytes:
    a = array.array("h", [0] * n)
    if n:
        a[0] = max(-32768, min(32767, peak))
        a[n // 2] = max(-32768, min(32767, -peak))
    return a.tobytes()


class FakeCanvas:
    def __init__(self) -> None:
        self.ovals = []
        self.lines = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append((x0, y0, x1, y1, kw))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((pts, kw))
        return len(self.lines)


def _amp(stats) -> float:
    return float(stats["max_y"]) - float(stats["min_y"])


# -- publish ---------------------------------------------------------------

def test_publish_loud_frame_near_one():
    DH.publish_mic_level(_s16_frame(30000))
    lvl, _ts = DH.read_mic_level()
    assert lvl > 0.5


def test_publish_silence_near_zero():
    DH.publish_mic_level(bytes(9600))
    lvl, _ts = DH.read_mic_level()
    assert lvl == 0.0


def test_publish_clamps_and_never_raises():
    DH.publish_mic_level(None)
    DH.publish_mic_level(b"")
    DH.publish_mic_level(object())
    lvl, ts = DH.read_mic_level()
    assert 0.0 <= lvl <= 1.0
    assert ts > 0.0


def test_heartbeat_payload_carries_level_and_ts():
    DH.publish_mic_level(_s16_frame(16000))
    payload = DH.mic_level_for_heartbeat()
    assert 0.0 <= payload["mic_level"] <= 1.0
    assert payload["mic_level_ts"] > 0.0


def test_level_tap_mic_passes_frames_through_and_publishes():
    inner_frames = [_s16_frame(20000), _s16_frame(0)]

    class _Inner:
        def __init__(self):
            self.i = 0
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def read_frame(self, timeout=0.5):
            if self.i < len(inner_frames):
                f = inner_frames[self.i]
                self.i += 1
                return f
            return None

    inner = _Inner()
    tap = DH.LevelTapMic(inner)
    tap.start()
    assert tap.read_frame() == inner_frames[0]
    lvl, _ts = DH.read_mic_level()
    assert lvl > 0.5
    assert inner.started and not inner.stopped
    tap.stop()
    assert inner.stopped


# -- consume ---------------------------------------------------------------

def test_consume_fresh_level():
    now = time.monotonic()
    assert OC.mic_level_from_payload(
        {"mic_level": 0.7, "mic_level_ts": now}, now=now) == 0.7


def test_consume_stale_reads_zero():
    now = time.monotonic()
    assert OC.mic_level_from_payload(
        {"mic_level": 0.9, "mic_level_ts": now - 5.0}, now=now) == 0.0


def test_consume_missing_is_none_fail_soft():
    now = time.monotonic()
    assert OC.mic_level_from_payload({}, now=now) is None
    assert OC.mic_level_from_payload(
        {"mic_level": 0.5}, now=now) is None  # no ts: untrusted
    assert OC.mic_level_from_payload(None, now=now) is None
    assert OC.mic_level_from_payload(
        {"mic_level": "loud", "mic_level_ts": now}, now=now) is None


def test_consume_clamps():
    now = time.monotonic()
    assert OC.mic_level_from_payload(
        {"mic_level": 9.0, "mic_level_ts": now}, now=now) == 1.0
    assert OC.mic_level_from_payload(
        {"mic_level": -3.0, "mic_level_ts": now}, now=now) == 0.0


# -- smoothing --------------------------------------------------------------

def test_smoother_fast_attack_slow_release():
    from voicebridge.orb.app import LevelSmoother
    s = LevelSmoother(0.0)
    loud = s.update(1.0)      # attack: most of the way in one step
    assert loud > 0.5
    calm = s.update(0.0)      # release: barely moves in one step
    assert calm > loud * 0.8


# -- render -----------------------------------------------------------------

def test_listening_silence_calms_and_speech_pumps():
    t = 3.3
    base = E.render(FakeCanvas(), 48, 48, 46, t, "listening")
    calm = E.render(FakeCanvas(), 48, 48, 46, t, "listening", level=0.0)
    loud = E.render(FakeCanvas(), 48, 48, 46, t, "listening", level=1.0)
    assert _amp(calm) < _amp(base)
    assert _amp(loud) > _amp(calm)
    # Full-scale level reproduces exactly today's loop.
    assert loud == base
    # Calm keeps the listening color family (state owns color; only the
    # amplitude/brightness is modulated) — it must not collapse to idle.
    idle = E.render(FakeCanvas(), 48, 48, 46, t, "idle")
    assert calm["fills"] and base["fills"]
    assert calm["fills"][0] != idle["fills"][0]


def test_other_states_byte_identical_with_level():
    for state in ("idle", "thinking", "speaking", "error"):
        a = E.render(FakeCanvas(), 48, 48, 46, 2.5, state)
        b = E.render(FakeCanvas(), 48, 48, 46, 2.5, state, level=0.9)
        assert a == b


def test_none_level_is_todays_loop():
    a = E.render(FakeCanvas(), 48, 48, 46, 2.5, "listening")
    b = E.render(FakeCanvas(), 48, 48, 46, 2.5, "listening", level=None)
    assert a == b


def test_orb_tree_never_opens_the_mic():
    import pathlib
    orb_dir = pathlib.Path(__file__).resolve().parent.parent / "voicebridge" / "orb"
    banned = ("sounddevice", "pyaudio", "parec", "PortAudio", "open_stream",
              "get_record", "Microphone")
    hits = []
    for py in orb_dir.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        for token in banned:
            if token in src:
                hits.append(f"{py.name}:{token}")
    assert hits == []
