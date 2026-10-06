"""Amber-orb polish round 3: onset transient + richer equalizer bars.

Owner 2026-10-07: make the orb more reactive/alive, keeping every
invariant. Hermetic (no Tk, no display, no network): a recording
FakeCanvas plus the pure render stats.

What these tests pin (all deterministic, no randomness, no new deps):

- ``onset_punch`` scores a spectrum's transient spikiness: exactly 0 for
  a flat/silent/absent spectrum, higher for a sharper/louder spike, and
  gated by the slow-falling peak caps so a *fresh* hit (band == cap)
  scores above a decayed tail (band << cap). Never raises.
- The host's ``SpectrumSmoother`` (fast attack / slow release) plus the
  onset score gives an attack that decays over frames instead of a snap.
- The onset is actually wired into the frame: a spiky spectrum changes
  the aura/glow ovals (scale/glow) versus a flat one at the same level.
- The equalizer bar body is drawn as a vertical gradient (>= 2 stacked
  segments per band, brighter toward the tip) with the per-bar peak-hold
  cap tick kept; every fill stays inside the voice state's color family.
- The hard invariants survive: same stat keys; ``render(None, ...)``
  never raises; no spectrum -> 5 lines + hue match; voice + spectrum ->
  bars; thinking/working/idle/error ignore spectrum byte-identically;
  voice ``render(state) == render(state, spectrum=None)``; determinism.
"""
from __future__ import annotations

import math
import pathlib

from voicebridge.orb import ember as E


class FakeCanvas:
    """Canvas-like recorder; no window, no Tk."""

    def __init__(self) -> None:
        self.ovals = []
        self.lines = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append((x0, y0, x1, y1, dict(kw)))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((tuple(pts), dict(kw)))
        return len(self.lines)


def _spike(peak: float, band: int = 4, floor: float = 0.05):
    v = [floor] * E.SPEC_BARS
    v[band] = peak
    return v


# -- 1. onset_punch: pure transient score -----------------------------------

def test_onset_punch_zero_for_flat_silent_and_absent():
    assert E.onset_punch(None) == 0.0
    assert E.onset_punch([]) == 0.0
    assert E.onset_punch([0.0] * 12) == 0.0
    # A flat spectrum has no transient to speak of.
    assert E.onset_punch([0.9] * 12) == 0.0
    assert E.onset_punch([0.9] * 12, [0.9] * 12) == 0.0
    assert E.onset_punch([0.05] * 12) == 0.0


def test_onset_punch_spike_beats_flat_and_scales_with_level():
    flat = [0.5] * 12
    soft = _spike(0.4)
    hard = _spike(0.95)
    assert E.onset_punch(flat) == 0.0
    assert E.onset_punch(soft) > 0.0
    assert E.onset_punch(hard) > E.onset_punch(soft)  # crisper = punchier
    assert 0.0 <= E.onset_punch(hard) <= 1.0


def test_onset_punch_cap_gating_fresh_hit_beats_decayed_tail():
    bands = _spike(0.9)
    fresh = E.onset_punch(bands, list(bands))          # band tracks its cap
    stale = E.onset_punch(bands, [1.0] * 12)           # cap held far above
    assert fresh > stale > 0.0
    # A mismatched/absent caps vector degrades to the pure spikiness score.
    assert E.onset_punch(bands, [0.5] * 11) == E.onset_punch(bands)
    assert E.onset_punch(bands, "bad") == E.onset_punch(bands)


def test_onset_punch_never_raises_on_malformed():
    for bad in (None, "x", 5, [1, 2], object(), [object()]):
        v = E.onset_punch(bad)
        assert isinstance(v, float) and 0.0 <= v <= 1.0, bad
    v = E.onset_punch(_spike(0.9), object())
    assert 0.0 <= v <= 1.0


def test_smoother_plus_onset_decays_over_frames():
    # Fast attack (SpectrumSmoother) + the onset score = a transient that
    # decays, not a hard snap.
    s = E.SpectrumSmoother()
    first = s.update(_spike(0.9), dt=0.05)
    p_first = E.onset_punch(first, s.peaks)
    assert p_first > 0.05
    prev = p_first
    scores = [p_first]
    for _ in range(20):
        bands = s.update(None, dt=0.05)
        p = E.onset_punch(bands, s.peaks)
        assert 0.0 <= p <= 1.0
        scores.append(p)
        prev = p
    # The punch has visibly decayed by the tail (and never grew upward).
    assert scores[-1] < p_first * 0.5
    assert max(scores[-3:]) < p_first


# -- 2. onset is wired into the rendered scale/glow --------------------------

def test_onset_changes_aura_ovals_vs_flat_at_same_level():
    flat = [0.4] * 12
    spike = _spike(0.9)
    assert E.onset_punch(flat) == 0.0 and E.onset_punch(spike) > 0.0
    c_flat, c_spike = FakeCanvas(), FakeCanvas()
    E.render(c_flat, 48, 48, 46, 2.5, "listening", level=0.5, spectrum=flat)
    E.render(c_spike, 48, 48, 46, 2.5, "listening", level=0.5, spectrum=spike)
    # The first oval is the outer aura; the onset widens/brightens it.
    assert c_spike.ovals[0] != c_flat.ovals[0]


def test_onset_attenuated_by_decayed_caps_in_render():
    bands = _spike(0.9)
    fresh = E.render(None, 48, 48, 46, 2.5, "speaking",
                     spectrum=bands, spec_peaks=list(bands))
    decayed = E.render(None, 48, 48, 46, 2.5, "speaking",
                       spectrum=bands, spec_peaks=[1.0] * 12)
    assert fresh != decayed


# -- 3. equalizer bars: vertical gradient + peak caps ------------------------

def _classify(lines):
    vertical = horizontal = 0
    for pts, _kw in lines:
        if len(pts) == 4 and pts[0] == pts[2]:
            vertical += 1
        elif len(pts) == 4 and pts[1] == pts[3]:
            horizontal += 1
    return vertical, horizontal


def test_bars_draw_stacked_gradient_segments_and_caps():
    canvas = FakeCanvas()
    stats = E.render(canvas, 48, 48, 46, 2.5, "listening",
                     spectrum=[0.8] * 12)
    assert stats["bars"] is True
    vertical, horizontal = _classify(canvas.lines)
    # One stacked segment column per step per band, plus one cap tick each.
    assert vertical == E.SPEC_BARS * E.BAR_GRADIENT_STEPS
    assert horizontal == E.SPEC_BARS
    assert vertical > E.SPEC_BARS  # richer than a single flat stick


def test_bar_gradient_and_caps_stay_in_voice_family():
    for st in ("listening", "speaking"):
        stats = E.render(None, 48, 48, 46, 2.5, st,
                         spectrum=[0.7] * 12,
                         spec_peaks=[0.85] * 12)
        core = E.STATE_CORES[st]
        dom = max(range(3), key=lambda i: core[i])
        assert stats["fills"], st
        for fill in stats["fills"]:
            assert fill.startswith("#") and len(fill) == 7, fill
            r, g, b = (int(fill[i:i + 2], 16) for i in (1, 3, 5))
            assert max(range(3), key=lambda i: (r, g, b)[i]) == dom, (st, fill)


def test_bar_geometry_includes_peak_cap_above_body():
    rects = E.spec_bar_rects([0.5] * 12, [0.95] * 12, 48, 48, 46)
    assert len(rects) == E.SPEC_BARS
    for rc in rects:
        assert rc["peak_top"] < rc["top"]  # cap sits above the body
        assert rc["top"] <= rc["bottom"]


# -- 4. hard invariants survive ---------------------------------------------

def test_stat_keys_and_bare_render_never_raises():
    stats = E.render(None, 48, 48, 46, 1.0, "idle")
    assert set(stats) == {"ovals", "lines", "points", "min_y", "max_y",
                          "fills", "bars"}
    for bad in (None, "x", [1], object()):
        out = E.render(None, 48, 48, 46, 1.0, "speaking",
                       spectrum=bad, spec_peaks=bad)
        assert set(out) == set(stats)


def test_no_spectrum_non_voice_states_five_lines_and_hue():
    for st in ("idle", "listening", "thinking", "working", "speaking",
               "error"):
        stats = E.render(None, 48, 48, 46, 4.2, st)
        assert stats["lines"] == 5, st
        assert stats["points"] > 100, st
        assert stats["ovals"] >= 1 + 3, st
        core = E.STATE_CORES[st]
        dom = max(range(3), key=lambda i: core[i])
        for fill in stats["fills"]:
            r, g, b = (int(fill[i:i + 2], 16) for i in (1, 3, 5))
            assert max(range(3), key=lambda i: (r, g, b)[i]) == dom, (st, fill)


def test_voice_bars_and_none_spectrum_equivalence():
    for st in ("listening", "speaking"):
        bare = E.render(None, 48, 48, 46, 2.5, st)
        none = E.render(None, 48, 48, 46, 2.5, st, spectrum=None)
        assert bare == none and bare["lines"] == 5
        # A passed peaks vector must not change a spectrum-less frame.
        with_peaks = E.render(None, 48, 48, 46, 2.5, st, spectrum=None,
                              spec_peaks=[1.0] * 12)
        assert with_peaks == bare
        bars = E.render(None, 48, 48, 46, 2.5, st, spectrum=[0.5] * 12)
        assert bars["bars"] is True and bars["lines"] > 5


def test_non_voice_states_ignore_spectrum_level_and_onset():
    spike = _spike(0.9)
    for st in ("thinking", "working", "idle", "error"):
        plain = E.render(None, 48, 48, 46, 2.5, st)
        spec = E.render(None, 48, 48, 46, 2.5, st, spectrum=spike,
                        spec_peaks=list(spike))
        lvl = E.render(None, 48, 48, 46, 2.5, st, level=0.9)
        assert plain == spec == lvl, st


def test_amber3_deterministic_stats_and_geometry():
    spike = _spike(0.9)
    for st in ("idle", "listening", "thinking", "working", "speaking",
               "error"):
        c1, c2 = FakeCanvas(), FakeCanvas()
        s1 = E.render(c1, 48, 48, 46, 3.1, st, spectrum=spike,
                      spec_peaks=[0.95] * 12)
        s2 = E.render(c2, 48, 48, 46, 3.1, st, spectrum=spike,
                      spec_peaks=[0.95] * 12)
        assert s1 == s2, st
        assert (c1.ovals, c1.lines) == (c2.ovals, c2.lines), st


def test_speed_scale_and_colors_overrides_still_work():
    fam = E.family_from_core(E.STATE_CORES["speaking"])
    stats = E.render(None, 48, 48, 46, 1.0, "idle", colors=fam)
    bright = E.resolve_params("idle")["brightness"]
    core_hex = E._hex(E._mix(fam["core"], E.NEAR_BLACK,
                             min(1.0, 0.55 + 0.35 * bright)))
    assert core_hex in stats["fills"]
    lo = E.render(None, 48, 48, 46, 1.0, "idle", speed_scale=0.0)
    lo_edge = E.render(None, 48, 48, 46, 1.0, "idle",
                       speed_scale=E.SPEED_SCALE_MIN)
    assert lo == lo_edge


def test_reduced_motion_static_with_onset_inputs():
    spike = _spike(0.9)
    c1, c2 = FakeCanvas(), FakeCanvas()
    s1 = E.render(c1, 48, 48, 46, 5.0, "speaking", reduced_motion=True,
                  spectrum=spike, spec_peaks=[0.95] * 12)
    s2 = E.render(c2, 48, 48, 46, 50.0, "speaking", reduced_motion=True,
                  spectrum=spike, spec_peaks=[0.95] * 12)
    assert (c1.lines, s1["points"]) == (c2.lines, s2["points"])


def test_onset_params_are_deterministic_constants():
    # No randomness: the module imports no RNG and the onset knobs are
    # plain ordered constants (bar gradient must be at least 2 steps).
    src = pathlib.Path(E.__file__).read_text(encoding="utf-8")
    assert "import random" not in src and "from random" not in src
    assert E.BAR_GRADIENT_STEPS >= 2
    assert E.ONSET_PUNCH_GAIN >= 0.0 and E.ONSET_CORE_GAIN >= 0.0
    # A non-finite band must not produce a NaN punch.
    v = E.onset_punch([float("nan")] + [0.05] * 11)
    assert math.isfinite(v) and 0.0 <= v <= 1.0
    assert math.isfinite(E.onset_punch(_spike(0.9)))
