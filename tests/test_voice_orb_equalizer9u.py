"""Orb real-equalizer CONSUME+RENDER tests (TDD, hermetic).

Consume side of docs/superpowers/specs/2026-10-05-orb-equalizer-design.md:
helper publishes heartbeat extras (spec_bands/spec_ts live, spec_tts/
spec_tts_ts reply timeline); the orb only parses + renders. No mic, no
display, no network, no service contact — synthetic payloads only.

Publish side (desktop_helper.py) is another lane's; these tests code
against the spec schema with fail-soft fallbacks.
"""
from __future__ import annotations

import pathlib
import time

from voicebridge.orb import client as OC
from voicebridge.orb import ember as E


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


def _bands(hi=(2, 0.9), lo=0.02):
    v = [lo] * 12
    v[hi[0]] = hi[1]
    return v


# -- 1. client: live-band parser ---------------------------------------------

def test_spec_bands_fresh_twelve():
    now = time.monotonic()
    out = OC.spec_bands_from_payload(
        {"spec_bands": _bands(), "spec_ts": now}, now=now)
    assert out is not None and len(out) == 12
    assert out[2] == 0.9


def test_spec_bands_stale_is_none():
    now = time.monotonic()
    assert OC.spec_bands_from_payload(
        {"spec_bands": _bands(), "spec_ts": now - 5.0}, now=now) is None


def test_spec_bands_malformed_is_none():
    now = time.monotonic()
    assert OC.spec_bands_from_payload({}, now=now) is None
    assert OC.spec_bands_from_payload(
        {"spec_bands": [0.5] * 11, "spec_ts": now}, now=now) is None
    assert OC.spec_bands_from_payload(
        {"spec_bands": ["loud"] * 12, "spec_ts": now}, now=now) is None
    assert OC.spec_bands_from_payload(
        {"spec_bands": _bands()}, now=now) is None  # no ts: untrusted
    assert OC.spec_bands_from_payload(None, now=now) is None


def test_spec_bands_clamped():
    now = time.monotonic()
    out = OC.spec_bands_from_payload(
        {"spec_bands": [9.0] + [-3.0] + [0.5] * 10, "spec_ts": now},
        now=now)
    assert out is not None and out[0] == 1.0 and out[1] == 0.0


# -- 1b. client: TTS timeline parser ----------------------------------------

def _tts_payload(n=20, start=None):
    now = time.monotonic()
    frames = [[0.05] * 12 for _ in range(n)]
    frames[3][7] = 0.95
    return {"spec_tts": frames, "spec_tts_ts": start if start is not None else now}, now


def test_tts_frame_tracks_playback_position():
    payload, now = _tts_payload(n=20, start=None)
    start = payload["spec_tts_ts"]
    frame = OC.spec_tts_frame_from_payload(payload, now_mono=start + 0.35)
    assert frame is not None and len(frame) == 12
    assert frame[7] == 0.95  # 0.35s / 0.1s = frame 3


def test_tts_frame_past_end_is_none():
    payload, now = _tts_payload(n=20)
    start = payload["spec_tts_ts"]
    assert OC.spec_tts_frame_from_payload(
        payload, now_mono=start + 20 * 0.1 + 1.0) is None


def test_tts_frame_malformed_is_none():
    now = time.monotonic()
    assert OC.spec_tts_frame_from_payload({}, now_mono=now) is None
    assert OC.spec_tts_frame_from_payload(
        {"spec_tts": [[0.1] * 12], "spec_tts_ts": "soon"},
        now_mono=now) is None
    assert OC.spec_tts_frame_from_payload(None, now_mono=now) is None


def test_frame_spectrum_only_in_voice_states():
    now = time.monotonic()
    live = {"spec_bands": _bands(), "spec_ts": now}
    tts, _ = _tts_payload()
    assert OC.frame_spectrum_for_state("listening", live, now=now) is not None
    assert OC.frame_spectrum_for_state(
        "speaking", tts, now_mono=tts["spec_tts_ts"] + 0.05) is not None
    for st in ("thinking", "idle", "error"):
        assert OC.frame_spectrum_for_state("listening", {}, now=now) is None \
            or OC.frame_spectrum_for_state(st, live, now=now) is None


# -- 2. ember: bar geometry ---------------------------------------------------

def test_tone_lights_right_band():
    rects = E.spec_bar_rects(_bands((9, 0.9)), [0.0] * 12, 48, 48, 46)
    tallest = max(rects, key=lambda r: r["height"])
    assert tallest["band"] == 9


def test_smoother_fast_attack_slow_release():
    s = E.SpectrumSmoother()
    first = s.update([1.0] * 12, dt=0.1)[0]   # ~100ms attack
    assert first > 0.6
    calm = s.update([0.0] * 12, dt=0.1)[0]    # ~400ms release
    assert calm > first * 0.7


def test_smoother_none_decays_never_freezes():
    s = E.SpectrumSmoother()
    s.update([1.0] * 12, dt=0.1)
    vals = [1.0] * 12
    for _ in range(60):
        vals = s.update(None, dt=0.5)
    assert max(vals) < 0.01


def test_peak_caps_hold_then_fall():
    s = E.SpectrumSmoother()
    s.update([1.0] + [0.0] * 11, dt=0.5)  # ~full attack in one long step
    s.update([0.0] * 12, dt=0.1)
    assert s.peaks[0] > 0.9          # cap holds the hit ...
    for _ in range(40):
        s.update([0.0] * 12, dt=0.5)
    assert s.peaks[0] < 0.1          # ... then falls slowly away


# -- 2b. ember: render ----------------------------------------------------------

def test_bars_in_voice_states_only():
    fresh = _bands()
    for st in ("listening", "speaking"):
        sine = E.render(FakeCanvas(), 48, 48, 46, 2.5, st)
        bars = E.render(FakeCanvas(), 48, 48, 46, 2.5, st, spectrum=fresh)
        assert bars["lines"] > sine["lines"]  # 12 bars + caps vs 5 sines
        assert bars["bars"] is True and sine["bars"] is False
    for st in ("thinking", "working", "idle", "error"):
        a = E.render(FakeCanvas(), 48, 48, 46, 2.5, st)
        b = E.render(FakeCanvas(), 48, 48, 46, 2.5, st, spectrum=fresh)
        assert a == b  # byte-identical: no audio to analyse there


def test_none_spectrum_is_todays_loop():
    for st in ("listening", "speaking"):
        a = E.render(FakeCanvas(), 48, 48, 46, 2.5, st)
        b = E.render(FakeCanvas(), 48, 48, 46, 2.5, st, spectrum=None)
        assert a == b


def test_bars_keep_voice_family():
    # Speaking bars tint from the speaking family (owner palette
    # 2026-10-06: violet — red+blue over green).
    bars = E.render(FakeCanvas(), 48, 48, 46, 2.5, "speaking",
                    spectrum=_bands())
    for fill in bars["fills"]:
        r, g, b = (int(fill[i:i + 2], 16) for i in (1, 3, 5))
        assert r > g and b > g, fill


def test_bars_static_under_reduced_motion():
    c1, c2 = FakeCanvas(), FakeCanvas()
    s1 = E.render(c1, 48, 48, 46, 5.0, "listening",
                  reduced_motion=True, spectrum=_bands())
    s2 = E.render(c2, 48, 48, 46, 50.0, "listening",
                  reduced_motion=True, spectrum=_bands())
    assert (c1.lines, s1["points"]) == (c2.lines, s2["points"])


def test_motion_table_and_colors_untouched():
    # Owner palette 2026-10-06 adds the amber "working" action and gives
    # every state its own hue (listening != speaking).
    assert E._STATE_PARAMS["idle"] == {
        "speed": 0.6, "brightness": 0.45, "band_gain": 0.5,
        "rings": 3, "ring_speed": 0.12, "glow": 1}
    assert E._STATE_PARAMS["listening"] == {
        "speed": 1.4, "brightness": 0.95, "band_gain": 1.0,
        "rings": 4, "ring_speed": 0.30, "glow": 2}
    assert E._STATE_PARAMS["thinking"] == {
        "speed": 1.0, "brightness": 0.70, "band_gain": 0.8,
        "rings": 5, "ring_speed": 0.45, "glow": 2}
    assert E._STATE_PARAMS["speaking"] == {
        "speed": 1.8, "brightness": 1.0, "band_gain": 1.2,
        "rings": 3, "ring_speed": 0.25, "glow": 2}
    assert E._STATE_PARAMS["error"] == {
        "speed": 1.0, "brightness": 0.90, "band_gain": 0.8,
        "rings": 3, "ring_speed": 0.35, "glow": 2}
    assert "working" in E._STATE_PARAMS
    assert E.STATE_COLORS["listening"] != E.STATE_COLORS["speaking"]


# -- 3. app/qt threading (hermetic: no Tk()/Qt widgets) -------------------------

def test_draw_frame_threads_spectrum():
    from voicebridge.orb import app as OA
    c1, c2 = FakeCanvas(), FakeCanvas()
    a = OA.draw_orb_frame(c1, 68, "speaking", 2.5, spectrum=None)
    b = OA.draw_orb_frame(c2, 68, "speaking", 2.5, spectrum=_bands())
    assert b["lines"] > a["lines"]
    idle = OA.draw_orb_frame(FakeCanvas(), 68, "thinking", 2.5,
                             spectrum=_bands())
    plain = OA.draw_orb_frame(FakeCanvas(), 68, "thinking", 2.5)
    assert idle == plain


def test_qt_bar_helpers_voice_only():
    from voicebridge.orb import qt_app as OQ
    assert OQ.spec_bars_wanted("listening", _bands()) is True
    assert OQ.spec_bars_wanted("speaking", _bands()) is True
    assert OQ.spec_bars_wanted("thinking", _bands()) is False
    assert OQ.spec_bars_wanted("listening", None) is False


def test_orb_tree_opens_no_mic():
    orb_dir = pathlib.Path(__file__).resolve().parent.parent \
        / "voicebridge" / "orb"
    banned = ("sounddevice", "pyaudio", "numpy", "rfft", "open_stream",
              "parec", "arecord", "PortAudio", "get_record")
    hits = []
    for py in orb_dir.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        for token in banned:
            if token in src:
                hits.append(f"{py.name}:{token}")
    assert hits == []
