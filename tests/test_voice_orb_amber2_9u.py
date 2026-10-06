"""Amber-orb polish round 2: breathing, easing, depth, color richness.

Owner 2026-10-06: push the orb further toward organic/alive/3-D while
keeping every existing invariant. Hermetic (no Tk, no display, no
network): a recording FakeCanvas plus the pure render stats.

What these tests pin:

- Determinism: identical inputs -> identical stats AND identical
  recorded geometry (oval/line coordinates), so the new life is a pure
  function of the frame clock, never random.
- Idle is non-static across ``t`` (ambient breath + band drift) yet
  static for a fixed ``t`` (reduced-motion contract survives).
- The scalar energy follower (``EnergySmoother``) eases: fast attack,
  slow release, monotone, no snap.
- The hard invariants (line/oval budgets, hue dominance, spectrum
  ignored outside voice states, ``render(state) ==
  render(state, spectrum=None)`` for voice states, ``render(None, ...)``
  never raises, speed_scale/colors overrides kept).
- Depth (energy-gated parallax) and the soft outward ripple only appear
  with live audio, deterministically.
- Color richness: per-band hue micro-variation stays inside the state
  family (dominant channel preserved).
"""
from __future__ import annotations

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


def _render(state, t, **kw):
    """Return (stats, canvas) for one hermetic frame."""
    canvas = FakeCanvas()
    stats = E.render(canvas, 48, 48, 46, t, state, **kw)
    return stats, canvas


def _recording(canvas):
    return (list(canvas.ovals), list(canvas.lines))


# -- 1. determinism / idle life ---------------------------------------------

def test_same_inputs_identical_stats_and_geometry():
    for st in ("idle", "listening", "thinking", "working", "speaking",
               "error"):
        s1, c1 = _render(st, 3.1)
        s2, c2 = _render(st, 3.1)
        assert s1 == s2, st
        assert _recording(c1) == _recording(c2), st


def test_idle_non_static_across_t_but_static_at_fixed_t():
    _s1, c1 = _render("idle", 1.0)
    _s2, c2 = _render("idle", 5.0)
    assert _recording(c1) != _recording(c2)  # visibly alive while idle
    _s3, c3 = _render("idle", 5.0)
    assert _recording(c2) == _recording(c3)  # fixed t -> deterministic


def test_ambient_breath_is_pure_bounded_and_lively():
    g = E.AMBIENT_BREATH_GAIN
    assert E.ambient_breath(0.0) == 1.0
    for t in (0.0, 0.7, 3.3, 17.0, 123.4):
        v = E.ambient_breath(t)
        assert 1.0 - g <= v <= 1.0 + g, (t, v)
        assert v == E.ambient_breath(t)  # pure
    # It must actually move, not be a constant.
    assert len({round(E.ambient_breath(t), 6)
                for t in (0.0, 2.0, 5.0, 9.0, 14.0)}) > 1
    # Malformed input never raises.
    assert E.ambient_breath("nope") == 1.0


# -- 2. energy envelope easing ----------------------------------------------

def test_energy_smoother_fast_attack_no_snap():
    s = E.EnergySmoother()
    seq = [s.update(1.0, dt=0.1) for _ in range(6)]
    # Never overshoots the target and never snaps to it in one step.
    assert all(0.0 <= v <= 1.0 for v in seq)
    assert seq[0] > 0.5           # ~100ms attack reaches most of the way
    assert seq[0] < 1.0           # ... but does not snap instantly
    assert all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))
    # A tiny step moves only a little (no jump).
    tiny = E.EnergySmoother()
    assert tiny.update(1.0, dt=0.005) < 0.1


def test_energy_smoother_slow_release_monotone():
    s = E.EnergySmoother(value=1.0)
    seq = [s.update(0.0, dt=0.1) for _ in range(4)]
    assert all(0.0 <= v <= 1.0 for v in seq)
    assert all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
    # Release is slower than attack: one 100ms step falls less than it rose.
    s2 = E.EnergySmoother()
    rose = s2.update(1.0, dt=0.1)
    fell = 1.0 - s2.update(0.0, dt=0.1)
    assert fell < rose


def test_energy_smoother_deterministic_and_never_raises():
    a = [E.EnergySmoother().update(v, dt=0.1) for v in (0.4, 0.9)]
    b = [E.EnergySmoother().update(v, dt=0.1) for v in (0.4, 0.9)]
    assert a == b
    s = E.EnergySmoother()
    for bad in (None, "x", [1], object()):
        v = s.update(bad, dt=bad)
        assert 0.0 <= v <= 1.0
    assert 0.0 <= E.EnergySmoother(value="bad").value <= 1.0


# -- 3. hard invariants (kept intact) ---------------------------------------

def test_stat_keys_and_bare_render_never_raises():
    stats = E.render(None, 48, 48, 46, 1.0, "idle")
    assert set(stats) == {"ovals", "lines", "points", "min_y", "max_y",
                          "fills", "bars"}
    for bad in (None, "x", [1], object()):
        out = E.render(None, 48, 48, 46, 1.0, "speaking", speed_scale=bad)
        assert set(out) == set(stats)


def test_non_voice_no_spectrum_five_lines_and_hue():
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


def test_voice_bars_and_none_spectrum_loop():
    for st in ("listening", "speaking"):
        bare = E.render(None, 48, 48, 46, 2.5, st)
        none = E.render(None, 48, 48, 46, 2.5, st, spectrum=None)
        assert bare == none and bare["lines"] == 5
        bars = E.render(None, 48, 48, 46, 2.5, st, spectrum=[0.5] * 12)
        assert bars["bars"] is True and bars["lines"] > 5


def test_non_voice_states_ignore_spectrum_and_level():
    for st in ("thinking", "working", "idle", "error"):
        plain = E.render(None, 48, 48, 46, 2.5, st)
        spec = E.render(None, 48, 48, 46, 2.5, st, spectrum=[0.9] * 12)
        lvl = E.render(None, 48, 48, 46, 2.5, st, level=0.9)
        assert plain == spec == lvl, st


def test_listening_level_none_equals_full_and_speed_colors_kept():
    # level None and level 1.0 are the same loud end-stop (existing contract).
    none = E.render(None, 48, 48, 46, 3.3, "listening")
    full = E.render(None, 48, 48, 46, 3.3, "listening", level=1.0)
    assert none == full
    # speed_scale clamp still exact.
    lo = E.render(None, 48, 48, 46, 1.0, "idle", speed_scale=0.0)
    lo_edge = E.render(None, 48, 48, 46, 1.0, "idle",
                       speed_scale=E.SPEED_SCALE_MIN)
    assert lo == lo_edge
    # colors= family override still governs the core fill.
    fam = E.family_from_core(E.STATE_CORES["speaking"])
    stats = E.render(None, 48, 48, 46, 1.0, "idle", colors=fam)
    bright = E.resolve_params("idle")["brightness"]
    core_hex = E._hex(E._mix(fam["core"], E.NEAR_BLACK,
                             min(1.0, 0.55 + 0.35 * bright)))
    assert core_hex in stats["fills"]


# -- 4. depth + ripple (energy-gated, deterministic) -------------------------

def test_parallax_only_with_energy_and_deterministic():
    quiet_s, quiet_c = _render("speaking", 2.5)
    loud_s, loud_c = _render("speaking", 2.5, spectrum=[0.95] * 12)
    loud_s2, loud_c2 = _render("speaking", 2.5, spectrum=[0.95] * 12)
    assert _recording(loud_c) == _recording(loud_c2)  # deterministic
    assert _recording(loud_c) != _recording(quiet_c)  # energy widens depth
    # No-audio geometry is independent of level on a non-voice state.
    assert (E.render(None, 48, 48, 46, 2.5, "thinking")
            == E.render(None, 48, 48, 46, 2.5, "thinking", level=0.9))


def test_ripple_appears_only_with_live_audio():
    # Speaking: a live spectrum adds one outline ripple oval ...
    bare = E.render(None, 48, 48, 46, 2.5, "speaking")
    live = E.render(None, 48, 48, 46, 2.5, "speaking",
                    spectrum=[0.95] * 12)
    assert live["ovals"] == bare["ovals"] + 1
    # ... a silent spectrum does not (below the gate).
    silent = E.render(None, 48, 48, 46, 2.5, "speaking",
                      spectrum=[0.0] * 12)
    assert silent["ovals"] == bare["ovals"]
    # Listening folds None -> the loud end-stop, so the ripple is identical
    # for level=None and level=1.0 (documented contract preserved).
    none = E.render(None, 48, 48, 46, 2.5, "listening")
    full = E.render(None, 48, 48, 46, 2.5, "listening", level=1.0)
    assert none == full
    # Non-voice states never grow one.
    assert (E.render(None, 48, 48, 46, 2.5, "thinking",
                     spectrum=[0.95] * 12)["ovals"]
            == E.render(None, 48, 48, 46, 2.5, "thinking")["ovals"])


# -- 5. color richness -------------------------------------------------------

def test_band_shade_stays_in_family_and_dominant():
    for name, core in E.STATE_CORES.items():
        fam = E.state_colors(name)
        bands = E.bands_from_family(fam)
        dom = max(range(3), key=lambda i: core[i])
        for i in range(5):
            for t in (0.0, 1.3, 4.7):
                rgb = E.band_shade(bands, i, t)
                assert all(0 <= v <= 255 for v in rgb), (name, i, t, rgb)
                assert max(range(3), key=lambda i: rgb[i]) == dom, (
                    name, i, t, rgb)


def test_band_shade_varies_across_bands_is_pure_and_never_raises():
    bands = E.bands_from_family(E.STATE_COLORS["speaking"])
    shades = [E.band_shade(bands, i, 3.0) for i in range(5)]
    assert len(set(shades)) > 1  # richness: not all bands identical
    assert shades == [E.band_shade(bands, i, 3.0) for i in range(5)]
    # Malformed input degrades without raising.
    assert E.band_shade("nope", 0, 1.0)
    assert E.band_shade(None, 3, 1.0)


def test_render_band_fills_richness_within_state():
    # The 5 rendered band fills need not be identical (richness) but must
    # all stay in the state's family (violet: red+blue over green).
    stats = E.render(None, 48, 48, 46, 2.5, "speaking")
    band_fills = [f for f in stats["fills"]]
    assert len(set(band_fills)) > 5
    for fill in stats["fills"]:
        r, g, b = (int(fill[i:i + 2], 16) for i in (1, 3, 5))
        assert r > g or b > g, fill
