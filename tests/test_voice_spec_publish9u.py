"""Orb real-equalizer PUBLISH side (spec 2026-10-05): helper-only tests.

Hermetic: no mic, no display, no network, no service contact, no orb/*.
Scope: ``voicebridge/desktop_helper.py`` only (live spectrum publish + TTS
timeline publish). Consume/render live in the orb lane.
"""
from __future__ import annotations

import array
import math
import struct
import time

from voicebridge import desktop_helper as DH


def _sine_pcm(freq_hz: float, *, secs: float = 0.1, rate: int = 16000,
              peak: int = 20000) -> bytes:
    n = int(rate * secs)
    out = array.array("h", [0]) * n
    for i in range(n):
        out[i] = int(peak * math.sin(2.0 * math.pi * freq_hz * i / rate))
    return out.tobytes()


def _wav_bytes(freq_hz: float, *, secs: float = 0.5, rate: int = 16000,
               channels: int = 1) -> bytes:
    pcm = _sine_pcm(freq_hz, secs=secs, rate=rate)
    if channels == 2:
        mono = array.array("h", pcm)
        stereo = array.array("h", [0]) * (len(mono) * 2)
        stereo[0::2] = mono
        stereo[1::2] = mono
        pcm = stereo.tobytes()
    data_len = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + data_len, b"WAVE", b"fmt ",
        16, 1, channels, rate, rate * channels * 2, channels * 2, 16,
        b"data", data_len)
    return header + pcm


def _band_with(freq_hz: float) -> int:
    import numpy as np
    edges = np.logspace(math.log10(80.0), math.log10(8000.0), 13)
    for i in range(12):
        if edges[i] <= freq_hz < edges[i + 1] or (
                i == 11 and freq_hz == edges[12]):
            return i
    raise AssertionError(freq_hz)


# -- spec_bands_for_pcm -------------------------------------------------------

def test_spec_window_shape_and_range():
    bands = DH.spec_bands_for_pcm(_sine_pcm(440.0))
    assert isinstance(bands, list) and len(bands) == 12
    assert all(isinstance(v, float) and 0.0 <= v <= 1.0 for v in bands)


def test_spec_silence_reads_zero_not_flicker():
    bands = DH.spec_bands_for_pcm(bytes(3200))
    assert bands == [0.0] * 12


def test_spec_short_empty_malformed_is_zeros():
    for bad in (b"", b"\x00\x01", b"\xff", bytes(3199), None, object(),
                "not-bytes"):
        bands = DH.spec_bands_for_pcm(bad)
        assert bands == [0.0] * 12, bad


def test_spec_tone_lights_its_band():
    bands = DH.spec_bands_for_pcm(_sine_pcm(440.0))
    peak = max(range(12), key=bands.__getitem__)
    assert peak == _band_with(440.0), bands
    assert bands[peak] > 0.2


def test_spec_higher_tone_lights_higher_band():
    lo = DH.spec_bands_for_pcm(_sine_pcm(440.0))
    hi = DH.spec_bands_for_pcm(_sine_pcm(3000.0))
    plo = max(range(12), key=lo.__getitem__)
    phi = max(range(12), key=hi.__getitem__)
    assert phi == _band_with(3000.0), hi
    assert phi > plo


def test_spec_single_window_cost_bounded():
    t0 = time.monotonic()
    DH.spec_bands_for_pcm(_sine_pcm(440.0))
    assert time.monotonic() - t0 < 0.1


# -- live accumulation --------------------------------------------------------

def test_live_accumulates_300ms_frames_into_100ms_windows():
    DH.reset_spec_state()
    DH.publish_spec_frame(_sine_pcm(440.0, secs=0.3))  # one mic frame
    bands, ts = DH.read_spec_bands()
    assert len(bands) == 12 and ts > 0.0
    assert bands[_band_with(440.0)] > 0.2


def test_live_partial_frame_buffers_without_publishing():
    DH.reset_spec_state()
    _, ts0 = DH.read_spec_bands()
    assert ts0 == 0.0
    DH.publish_spec_frame(bytes(1000))  # < 100ms: buffered, not published
    _, ts1 = DH.read_spec_bands()
    assert ts1 == 0.0
    DH.publish_spec_frame(bytes(2200))  # completes the window (silence)
    bands, ts2 = DH.read_spec_bands()
    assert ts2 > 0.0
    assert bands == [0.0] * 12


def test_live_publish_never_raises_and_keeps_last():
    DH.reset_spec_state()
    DH.publish_spec_frame(_sine_pcm(440.0, secs=0.3))
    before = DH.read_spec_bands()
    DH.publish_spec_frame(None)
    DH.publish_spec_frame(object())
    assert DH.read_spec_bands() == before


def test_level_tap_mic_also_publishes_spec():
    DH.reset_spec_state()

    class _Inner:
        def read_frame(self, timeout=0.5):
            return _sine_pcm(440.0, secs=0.3)

    tap = DH.LevelTapMic(_Inner())
    frame = tap.read_frame()
    assert frame and len(frame) == 9600  # audio bytes pass through untouched
    bands, ts = DH.read_spec_bands()
    assert ts > 0.0
    assert bands[_band_with(440.0)] > 0.2


def test_heartbeat_extra_carries_spec_live_keys():
    DH.reset_spec_state()
    DH.publish_spec_frame(_sine_pcm(440.0, secs=0.3))
    payload = DH.spec_for_heartbeat()
    assert set(payload) == {"spec_bands", "spec_ts"}
    assert len(payload["spec_bands"]) == 12
    assert payload["spec_ts"] > 0.0


# -- TTS timeline -------------------------------------------------------------

def test_wav_timeline_frames_track_duration_and_band():
    wav = _wav_bytes(440.0, secs=0.5)
    frames, rate, dur = DH.wav_to_spec_timeline(wav)
    assert rate == 16000
    assert abs(dur - 0.5) < 0.06
    assert len(frames) == 5
    for bands in frames:
        assert len(bands) == 12
        assert max(range(12), key=bands.__getitem__) == _band_with(440.0)


def test_wav_timeline_stereo_and_malformed():
    wav = _wav_bytes(440.0, secs=0.2, channels=2)
    frames, rate, dur = DH.wav_to_spec_timeline(wav)
    assert rate == 16000 and len(frames) == 2
    assert max(range(12), key=frames[0].__getitem__) == _band_with(440.0)
    for bad in (b"", b"junk-bytes", None, object()):
        frames, _rate, dur = DH.wav_to_spec_timeline(bad)
        assert frames == [] and dur == 0.0


def test_wav_timeline_truncates_at_cap(monkeypatch):
    monkeypatch.setattr(DH, "_SPEC_TTS_MAX_FRAMES", 5)
    frames, _rate, dur = DH.wav_to_spec_timeline(_wav_bytes(440.0, secs=1.0))
    assert len(frames) == 5
    assert abs(dur - 0.5) < 0.06  # dur covers the kept timeline


def test_publish_tts_sets_ts_dur_and_heartbeat_window(monkeypatch):
    DH.reset_spec_state()
    out = DH.publish_spec_tts(_wav_bytes(440.0, secs=0.5))
    assert len(out["spec_tts"]) == 5
    assert out["spec_tts_ts"] > 0.0
    assert abs(out["spec_tts_dur"] - 0.5) < 0.06
    frames, ts, dur = DH.read_spec_tts()
    assert len(frames) == 5 and ts > 0.0 and dur > 0.0
    live = DH.spec_tts_for_heartbeat()
    assert set(live) == {"spec_tts", "spec_tts_ts", "spec_tts_dur"}
    assert len(live["spec_tts"]) == 5
    # Past reply-end (+grace) the consumer must see silence, not a freeze.
    future = ts + dur + DH._SPEC_TTS_GRACE_S + 5.0
    monkeypatch.setattr(time, "monotonic", lambda: future)
    stale = DH.spec_tts_for_heartbeat()
    assert stale["spec_tts"] == []
    assert stale["spec_tts_ts"] == 0.0 and stale["spec_tts_dur"] == 0.0


def test_publish_tts_garbage_never_raises():
    DH.reset_spec_state()
    DH.publish_spec_tts(None)
    DH.publish_spec_tts(b"junk")
    frames, _ts, dur = DH.read_spec_tts()
    assert frames == [] and dur == 0.0


def test_speak_publishes_timeline_at_synth_time(monkeypatch):
    import shutil as _shutil
    from tests.test_voice_edit_helper9u import (
        FakeCfg, FakeClient, FakeDesktop, FakeMic, FakeOverlay)
    DH.reset_spec_state()
    wav = _wav_bytes(440.0, secs=0.5)
    overlay, client, desktop = FakeOverlay(), FakeClient(), FakeDesktop()
    client.speak = lambda text: wav
    monkeypatch.setattr(_shutil, "which", lambda name: None)  # no player
    helper = DH.DesktopHelper(
        FakeCfg(), client=client, overlay=overlay, desktop=desktop,
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: None)
    helper._speak("hello there")
    frames, ts, dur = DH.read_spec_tts()
    assert len(frames) == 5 and ts > 0.0 and dur > 0.0
