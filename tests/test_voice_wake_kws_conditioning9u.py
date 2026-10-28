"""KWS-only input conditioning (no mic, no model, no X, no audio logging).

The live spotter sees post-AGC frames whose peaks sit at 0.03-0.10 while the
model expects a healthier level. Conditioning is boost-only, bounded, and
applied to the spotter's samples only; the ASR path keeps the raw capture.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.kws import condition_samples  # noqa: E402
from voicebridge.voice_edit.wake import WakeMonitor  # noqa: E402


class FakeCfg:
    def __init__(self, wake=None):
        self._d = {"voice_edit": {"beep": False,
                                  "wake": wake or {"enabled": True}}}

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


class SeqMic:
    def __init__(self, frames):
        self._frames = list(frames)

    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        if self._frames:
            return self._frames.pop(0)
        import time as _t
        _t.sleep(0.05)
        return b"\x00" * 9600

    def stop(self):
        pass


class RecordingKws:
    """Records every float frame the monitor feeds the spotter."""

    def __init__(self, hits=0):
        self.hits = hits
        self.seen = 0
        self.resets = 0
        self.fed = []

    def feed(self, samples):
        self.fed.append(np.array(samples, copy=True))
        self.seen += 1
        return "HEY QUBIT" if self.seen <= self.hits else None

    def reset(self):
        self.resets += 1


def tone_frame(peak=0.03, ms=300, freq=220.0):
    n = int(16000 * ms / 1000)
    amp = max(1, int(round(peak * 32767)))
    data = [int(round(amp * math.sin(2 * math.pi * freq * i / 16000)))
            for i in range(n)]
    return struct.pack("<%dh" % n, *data)


def silent_frame(ms=300):
    n = int(16000 * ms / 1000)
    return b"\x00\x00" * n


def _run_loop(m, until, timeout_s=6.0):
    import time as _t

    m.start()
    end = _t.time() + timeout_s
    while _t.time() < end and not until():
        _t.sleep(0.05)
    m.stop()


def _fed_peak(kws):
    assert kws.fed, "the spotter was never fed"
    return float(np.abs(kws.fed[0]).max())


def _raw_peak(frame):
    a = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.abs(a).max())


# ---------------------------------------------------------------------------
# pure conditioning function
# ---------------------------------------------------------------------------
def test_low_level_is_lifted_and_bounded():
    raw = np.full(4800, 0.03, dtype=np.float32)
    out, stats = condition_samples(raw)
    peak = float(np.abs(out).max())
    assert peak > 0.03
    assert peak <= 0.5 + 1e-6
    assert stats["normalized"] is True
    assert stats["raw_peak"] == 0.03
    assert 1.0 < stats["gain"] <= 6.0


def test_normal_level_is_not_distorted():
    raw = np.full(4800, 0.6, dtype=np.float32)
    out, stats = condition_samples(raw)
    assert np.array_equal(out, raw)
    assert stats["normalized"] is False
    assert stats["gain"] == 1.0


def test_near_silence_is_never_amplified():
    raw = np.full(4800, 0.001, dtype=np.float32)
    out, stats = condition_samples(raw)
    assert np.array_equal(out, raw)
    assert stats["normalized"] is False


def test_normalize_false_is_a_no_op():
    raw = np.full(4800, 0.03, dtype=np.float32)
    out, stats = condition_samples(raw, normalize=False)
    assert np.array_equal(out, raw)
    assert stats["gain"] == 1.0


def test_max_gain_bounds_the_lift():
    raw = np.full(4800, 0.005, dtype=np.float32)
    out, stats = condition_samples(raw, max_gain=2.0)
    assert abs(float(np.abs(out).max()) - 0.010) < 1e-6
    assert stats["gain"] == 2.0


def test_fixed_gain_before_normalization():
    raw = np.full(4800, 0.02, dtype=np.float32)
    out, stats = condition_samples(raw, gain=2.0, normalize=False)
    assert abs(float(np.abs(out).max()) - 0.04) < 1e-6
    assert stats["gain"] == 2.0


def test_empty_frame_safe():
    out, stats = condition_samples(np.zeros(0, dtype=np.float32))
    assert out.size == 0
    assert stats["gain"] == 1.0


# ---------------------------------------------------------------------------
# wake-loop integration
# ---------------------------------------------------------------------------
def test_low_level_frames_are_conditioned_before_feed():
    low = tone_frame(peak=0.03)
    kws = RecordingKws(hits=0)
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [low, low, low] + [silent_frame()] * 8),
                    transcribe=lambda pcm: "",
                    kws=kws)
    _run_loop(m, lambda: kws.seen >= 3)
    raw_peak = _raw_peak(low)
    fed_peak = _fed_peak(kws)
    assert fed_peak > raw_peak * 1.5
    assert fed_peak <= 0.5 + 1e-6


def test_normal_level_frames_reach_the_spotter_unchanged():
    normal = tone_frame(peak=0.6)
    expected = (np.frombuffer(normal, dtype="<i2").astype(np.float32)
                / 32768.0)
    kws = RecordingKws(hits=0)
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [normal, normal, normal] + [silent_frame()] * 8),
                    transcribe=lambda pcm: "",
                    kws=kws)
    _run_loop(m, lambda: kws.seen >= 1)
    assert np.array_equal(kws.fed[0], expected)


def test_default_off_config_leaves_low_level_untouched():
    low = tone_frame(peak=0.03)
    expected = (np.frombuffer(low, dtype="<i2").astype(np.float32)
                / 32768.0)
    kws = RecordingKws(hits=0)
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy",
                                  "kws_normalize": False}),
                    on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [low, low, low] + [silent_frame()] * 8),
                    transcribe=lambda pcm: "",
                    kws=kws)
    _run_loop(m, lambda: kws.seen >= 1)
    assert np.array_equal(kws.fed[0], expected)


def test_configured_fixed_gain_reaches_the_spotter():
    low = tone_frame(peak=0.03)
    raw_peak = _raw_peak(low)
    kws = RecordingKws(hits=0)
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy",
                                  "kws_normalize": False,
                                  "kws_input_gain": 2.0}),
                    on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [low, low, low] + [silent_frame()] * 8),
                    transcribe=lambda pcm: "",
                    kws=kws)
    _run_loop(m, lambda: kws.seen >= 1)
    fed_peak = _fed_peak(kws)
    assert abs(fed_peak - min(1.0, raw_peak * 2.0)) < 1e-4


def test_conditioning_never_creates_a_command_path():
    """A louder spotter still cannot execute without a wake transcript.

    The KWS fires on every frame, but the ASR transcript contains no wake
    phrase, so the forced path only arms silently (no command emitted).
    """
    loud = tone_frame(peak=0.2)
    commands = []
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=commands.append,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud, loud, loud] + [silent_frame()] * 8),
                    transcribe=lambda pcm: "the weather is nice today",
                    kws=RecordingKws(hits=10 ** 9))
    _run_loop(m, lambda: len(commands) > 0, timeout_s=2.0)
    assert commands == []


def test_miss_diagnostics_are_bounded_per_segment(monkeypatch):
    import voicebridge.voice_edit.wake as wake_mod

    stages = []

    def fake_trace(stage, **fields):
        stages.append((stage, fields))

    monkeypatch.setattr(wake_mod, "_trace", fake_trace)
    low = tone_frame(peak=0.03)
    kws = RecordingKws(hits=0)
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [low, low, low] + [silent_frame()] * 8),
                    transcribe=lambda pcm: "",
                    kws=kws)
    _run_loop(m, lambda: any(s == "kws.miss" for s, _ in stages))
    misses = [f for s, f in stages if s == "kws.miss"]
    assert len(misses) == 1
    assert misses[0]["bytes"] > 0
    assert misses[0]["raw_peak"] > 0
    assert misses[0]["conditioned"] >= misses[0]["raw_peak"]
    # No per-frame spam: fewer than 100 frames never logs kws.frames, and
    # there is no audio content in any trace field.
    assert not [s for s, _ in stages if s == "kws.frames"]
    for _s, fields in stages:
        assert "audio" not in fields and "samples" not in fields


def test_settings_expose_and_clamp_conditioning(tmp_path):
    from voicebridge.voice_edit import settings as settings_mod

    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")

    class Cfg:
        def __init__(self):
            self.data = {"voice_edit": {"enabled": True}}
            self.path = p

        def get(self, *path, default=None):
            node = self.data
            for seg in path:
                if isinstance(node, dict) and seg in node:
                    node = node[seg]
                else:
                    return default
            return node

    cfg = Cfg()
    view = settings_mod.settings_view(cfg)["wake"]
    assert view["kws_normalize"] is True
    assert view["kws_input_gain"] == 1.0
    assert view["kws_target_peak"] == 0.5
    assert view["kws_max_gain"] == 6.0

    settings_mod.update_voice_edit(cfg, {"wake": {
        "kws_normalize": False, "kws_input_gain": 100.0,
        "kws_target_peak": 2.0, "kws_max_gain": 0.1}})
    view2 = settings_mod.settings_view(cfg)["wake"]
    assert view2["kws_normalize"] is False
    assert view2["kws_input_gain"] == 16.0
    assert view2["kws_target_peak"] == 0.9
    assert view2["kws_max_gain"] == 1.0
