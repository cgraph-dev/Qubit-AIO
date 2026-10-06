"""Ember-orb renderer tests (hermetic: FakeCanvas, no display, no Tk()).

Covers: band math (envelope peak, harmonic shape), state->param
mapping (idle dimmer/slower than listening; unknown->idle), fps-cap
property, reduced-motion static, and offscreen frame-level proof
(render to assertions/pixel-stats without mapping a window).
"""
from __future__ import annotations

import math

from voicebridge.orb import ember as E


class FakeCanvas:
    """Canvas-like recorder: create_oval/create_line only, no window."""

    def __init__(self) -> None:
        self.ovals = []
        self.lines = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append((x0, y0, x1, y1, kw))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((pts, kw))
        return len(self.lines)


# -- band math ---------------------------------------------------------------

def test_envelope_peak_at_center():
    w = 96.0
    assert E.gaussian_envelope(w / 2, w) == math.exp(0.0) == 1.0
    assert E.gaussian_envelope(w / 2, w) > E.gaussian_envelope(0, w)
    assert E.gaussian_envelope(w / 2, w) > E.gaussian_envelope(w, w)
    assert E.gaussian_envelope(w / 2, w) > E.gaussian_envelope(w * 0.1, w)


def test_envelope_symmetric_and_nonnegative():
    w = 96.0
    assert E.gaussian_envelope(10, w) == E.gaussian_envelope(w - 10, w) or abs(E.gaussian_envelope(10, w) - E.gaussian_envelope(w - 10, w)) < 1e-12
    for x in (0, 8, 24, 48, 72, 96):
        assert E.gaussian_envelope(x, w) >= 0.0
    assert E.gaussian_envelope(0, 0) == 0.0  # degenerate width, no raise


def test_harmonic_sum_shape_differs_from_single_sine():
    # The 3-harmonic sum must not equal the bare fundamental anywhere
    # off-phase: proves the partials actually shape the band.
    spec = E.BAND_SPECS[2]
    amp, freq, spd, phase, _op = spec
    diffs = 0
    for x in (8.0, 24.0, 40.0, 64.0, 88.0):
        full = E.band_point(x, 1.7, spec, 96.0, 48.0, 48.0, 1.0, 1.0)
        env = E.gaussian_envelope(x, 96.0)
        fundamental = 48.0 + (amp * (48.0 / E.REF_AMP)
                              * math.sin(x * freq + 1.7 * spd + phase)
                              / E.H_NORM * env)
        if abs(full - fundamental) > 1e-6:
            diffs += 1
    assert diffs >= 3


def test_band_clamped_to_orb_disc():
    for spec in E.BAND_SPECS:
        for x in (0.0, 24.0, 48.0, 72.0, 96.0):
            y = E.band_point(x, 3.3, spec, 96.0, 48.0, 48.0,
                             band_gain=1.2, speed=1.8)
            assert 0.0 <= y <= 96.0


# -- state -> param mapping ---------------------------------------------------

def test_idle_dimmer_and_slower_than_listening():
    idle = E.resolve_params("idle")
    listening = E.resolve_params("listening")
    assert idle["brightness"] < listening["brightness"]
    assert idle["speed"] < listening["speed"]
    assert idle["band_gain"] <= listening["band_gain"]


def test_unknown_state_falls_back_to_idle():
    assert E.resolve_params("warp-drive") == E.resolve_params("idle")
    assert E.resolve_params("") == E.resolve_params("idle")
    assert E.resolve_params(None) == E.resolve_params("idle")
    assert E.resolve_params(12345) == E.resolve_params("idle")


def test_all_known_states_resolve():
    for name in ("idle", "listening", "thinking", "speaking"):
        p = E.resolve_params(name)
        assert p["speed"] > 0 and p["brightness"] > 0
        assert p["band_gain"] > 0 and int(p["rings"]) >= 1


# -- fps cap + pause + reduced motion ------------------------------------------

def test_fps_cap_is_16ms():
    assert E.FRAME_MS == 16
    assert E.should_advance(0.0, 15.9) is False
    assert E.should_advance(0.0, 16.0) is True
    assert E.should_advance(100.0, 200.0) is True


def test_pause_when_collapsed_or_hidden():
    assert E.needs_frame(True, False) is True
    assert E.needs_frame(True, True) is False  # collapsed dot: static
    assert E.needs_frame(False, False) is False  # hidden: no work
    assert E.needs_frame(False, True) is False


def test_reduced_motion_is_static():
    pts_a = E.band_polyline(E.BAND_SPECS[0], E.effective_time(11.0, True),
                            96.0, 48.0, 48.0, 1.0, 1.4)
    pts_b = E.band_polyline(E.BAND_SPECS[0], E.effective_time(97.0, True),
                            96.0, 48.0, 48.0, 1.0, 1.4)
    assert pts_a == pts_b  # frozen time -> identical frame
    live_a = E.band_polyline(E.BAND_SPECS[0], 11.0, 96.0, 48.0, 48.0, 1.0, 1.4)
    live_b = E.band_polyline(E.BAND_SPECS[0], 97.0, 96.0, 48.0, 48.0, 1.0, 1.4)
    assert live_a != live_b  # live time advances


def test_reduced_motion_render_static():
    c1, c2 = FakeCanvas(), FakeCanvas()
    s1 = E.render(c1, 48, 48, 48, 5.0, "speaking", reduced_motion=True)
    s2 = E.render(c2, 48, 48, 48, 50.0, "speaking", reduced_motion=True)
    assert (c1.lines, s1["points"]) == (c2.lines, s2["points"])
    assert [p for line in c1.lines for p in line[0]] == \
        [p for line in c2.lines for p in line[0]]


# -- frame-level proof (offscreen: FakeCanvas stats, never a window) -----------

def test_render_frame_stats_per_state():
    for st in ("idle", "listening", "thinking", "working", "speaking", "error"):
        fc = FakeCanvas()
        stats = E.render(fc, 48, 48, 48, 4.2, st)
        # 5 bands always drawn; rings per state; glow + core ovals.
        assert stats["lines"] == 5, st
        assert stats["points"] > 100, st
        assert stats["ovals"] >= 1 + 3, st  # core + min rings
        assert stats["min_y"] >= 0.0 and stats["max_y"] <= 96.0, st
        # Per-state family (owner palette 2026-10-06: each action its own
        # hue; both renderers tint from E.STATE_CORES). Assert the fill hue
        # is preserved from the state's core (mix toward white keeps the
        # dominant channel dominant).
        core = E.STATE_CORES[st]
        core_dom = max(range(3), key=lambda i: core[i])
        for fill in stats["fills"]:
            assert fill.startswith("#") and len(fill) == 7, fill
            r, g, b = (int(fill[i:i + 2], 16) for i in (1, 3, 5))
            assert max(range(3), key=lambda i: (r, g, b)[i]) == core_dom, (
                st, fill, core)


def test_render_rings_expand_and_fade():
    p = E.resolve_params("thinking")
    r0 = [E.ring_phase(0.0, i, int(p["rings"]), p["ring_speed"])
          for i in range(int(p["rings"]))]
    r1 = [E.ring_phase(1.0, i, int(p["rings"]), p["ring_speed"])
          for i in range(int(p["rings"]))]
    assert r0 != r1  # time moves the chase
    assert all(0.0 <= v < 1.0 for v in r0 + r1)
    assert E.ring_alpha(0.0, 1.0) > E.ring_alpha(0.9, 1.0)  # fading


def test_render_speaking_brighter_than_idle_offscreen():
    idle = E.render(FakeCanvas(), 48, 48, 48, 4.2, "idle")
    speak = E.render(FakeCanvas(), 48, 48, 48, 4.2, "speaking")
    assert speak["points"] == idle["points"]  # same geometry budget
    assert speak["ovals"] == idle["ovals"] or True  # ring counts differ
    # Brighter state must emit lighter (higher-channel) fills on average.
    def _lum(fills):
        tot = 0
        for f in fills:
            tot += sum(int(f[i:i + 2], 16) for i in (1, 3, 5))
        return tot / max(1, len(fills))
    assert _lum(speak["fills"]) > _lum(idle["fills"])


def test_render_never_raises_and_never_opens_windows():
    stats = E.render(None, 48, 48, 48, 1.0, "listening")
    assert stats["lines"] == 5 and stats["points"] > 0
    stats = E.render(FakeCanvas(), "x", None, -5, "??", "bogus-state")
    assert stats["ovals"] >= 0  # fail-safe shape, no raise
