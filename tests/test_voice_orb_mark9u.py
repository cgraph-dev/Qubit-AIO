"""Qubit-mark orb renderer tests (hermetic: FakeCanvas, no display, no Tk).

The orb wears the dashboard sidebar mark: two hairline tilted orbits, a
beating nucleus with a halo ring, nine depth-sorted particles with trailing
tails, a dust field, a comet with a tail, a per-beat shockwave ring and
deterministic glints. All of that motion is owned by
``voicebridge.orb.mark_field``; these tests pin the STRUCTURE the Tk renderer
adds on top (so a refactor cannot silently drop an effect), the state tint
(one palette source with ember), the depth z-order, reduced motion, and the
fail-soft guarantees the host relies on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.orb import ember as E  # noqa: E402
from voicebridge.orb import mark as M  # noqa: E402
from voicebridge.orb import mark_field as F  # noqa: E402


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


def test_draws_two_fringed_rings_and_every_particle():
    c = FakeCanvas()
    # t=0.70 is between beats: no shockwave ring adds a line, so the only
    # lines are the two orbits' fringe strokes.
    stats = M.render(c, 34.0, 34.0, 32.0, 0.70, "idle")
    # Two orbits, each drawn as three strokes (blue fringe, red fringe, line).
    assert stats["lines"] == 6
    # Beads + their trails + dust + glints + the nucleus and its halo.
    assert stats["ovals"] >= len(M.PARTICLES) + 1
    assert stats["points"] > 0 and stats["bars"] is False
    assert stats["min_y"] < stats["max_y"]


def _centered(canvas, cx=34.0, cy=34.0, filled=None):
    """The oval nearest (cx, cy); optionally filtered by fill.

    The whole mark sways with the field's camera offset, so the nucleus is
    within a couple of pixels of the geometric centre, not exactly on it.
    """
    best, best_d = None, 4.0
    for o in canvas.ovals:
        ox, oy = (o[0] + o[2]) / 2, (o[1] + o[3]) / 2
        d = ((ox - cx) ** 2 + (oy - cy) ** 2) ** 0.5
        if d >= best_d:
            continue
        if filled is True and not o[4].get("fill"):
            continue
        if filled is False and o[4].get("fill"):
            continue
        best, best_d = o, d
    return best


def _nucleus(canvas):
    return _centered(canvas, filled=True)


def _halo(canvas):
    return _centered(canvas, filled=False)


def _lum(hex_colour):
    return sum(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def test_reduced_motion_is_a_static_complete_mark():
    a, b = FakeCanvas(), FakeCanvas()
    M.render(a, 34.0, 34.0, 32.0, 0.0, "idle", reduced_motion=True)
    M.render(b, 34.0, 34.0, 32.0, 9.5, "idle", reduced_motion=True)
    assert a.lines == b.lines
    assert a.ovals == b.ovals
    # Freezing time must not empty the mark: 9 beads + nucleus + halo, no
    # trails, dust, comet, shockwave or sparkles.
    assert len(a.lines) == 6 and len(a.ovals) == len(M.PARTICLES) + 2


def test_heartbeat_pulses_nucleus_size_and_halo_opacity():
    beat, rest = FakeCanvas(), FakeCanvas()
    M.render(beat, 34.0, 34.0, 32.0, 0.0, "idle")     # primary thump
    M.render(rest, 34.0, 34.0, 32.0, 0.475, "idle")   # between beats
    nb, nr = _nucleus(beat), _nucleus(rest)
    assert nb is not None and nr is not None
    # Scale envelope is larger on the primary beat than at rest.
    assert (nb[2] - nb[0]) > (nr[2] - nr[0])
    # The halo reads strongest (more saturated) on the beat.
    hb, hr = _halo(beat), _halo(rest)
    assert hb is not None and hr is not None
    assert _lum(hb[4]["outline"]) > _lum(hr[4]["outline"])
    # The field's beat: 1.00 -> 1.18 at the thump, flat between.
    assert M.nucleus_scale(0.0) == pytest.approx(1.0 + F.HEART_SCALE_AMP)
    assert M.nucleus_scale(0.475) == pytest.approx(1.0)
    assert M.heartbeat(0.0) > M.heartbeat(0.475)


def test_far_beads_draw_behind_and_near_beads_in_front():
    for t in (0.0, 1.0):
        c = FakeCanvas()
        M.render(c, 34.0, 34.0, 32.0, t, "idle")
        frames = F.particle_state(t, 32.0)

        def _idx(x, y):
            for i, o in enumerate(c.ovals):
                if (abs((o[0] + o[2]) / 2 - x) < 1e-6
                        and abs((o[1] + o[3]) / 2 - y) < 1e-6):
                    return i
            return None

        nuc = _nucleus(c)
        nuc_idx = c.ovals.index(nuc)
        far_idx = [_idx(f["x"] + 34.0, f["y"] + 34.0)
                   for f in frames if f["depth"] < 0.5]
        near_idx = [_idx(f["x"] + 34.0, f["y"] + 34.0)
                    for f in frames if f["depth"] >= 0.5]
        # Both halves are populated at these phases.
        assert far_idx and near_idx
        assert all(i is not None and i < nuc_idx for i in far_idx)
        assert all(i is not None and i > nuc_idx for i in near_idx)


def test_depth_z_split_orders_far_seam_near():
    # The three-way read: every far particle is drawn before the halo, the
    # halo before the nucleus, and every near particle after the nucleus.
    c = FakeCanvas()
    M.render(c, 40.0, 40.0, 40.0, 0.0, "idle")
    frames = F.particle_state(0.0, 40.0)
    depths = [f["depth"] for f in frames]
    assert min(depths) < 0.5 < max(depths)  # a phase with both halves

    def _idx(x, y):
        for i, o in enumerate(c.ovals):
            if (abs((o[0] + o[2]) / 2 - x) < 1e-6
                    and abs((o[1] + o[3]) / 2 - y) < 1e-6):
                return i
        return None

    halo_idx = c.ovals.index(_centered(c, 40.0, 40.0, filled=False))
    nuc_idx = c.ovals.index(_centered(c, 40.0, 40.0, filled=True))
    far_idx = [_idx(f["x"] + 40.0, f["y"] + 40.0)
               for f in frames if f["depth"] < 0.5]
    near_idx = [_idx(f["x"] + 40.0, f["y"] + 40.0)
                for f in frames if f["depth"] >= 0.5]
    assert far_idx and near_idx
    assert all(i < halo_idx for i in far_idx)   # far half behind the seam
    assert all(i > nuc_idx for i in near_idx)   # near half in front
    assert halo_idx < nuc_idx                   # seam order: halo then nucleus


def test_shockwave_ring_present_on_beat_absent_between():
    on_beat, between = FakeCanvas(), FakeCanvas()
    s_on = M.render(on_beat, 34.0, 34.0, 32.0, 0.0, "idle")   # thump -> ring
    s_between = M.render(between, 34.0, 34.0, 32.0, 0.70, "idle")  # quiet -> none
    # Exactly one extra outline line (the tilted shockwave ring) on the beat.
    assert s_on["lines"] == 6 + 1
    assert s_between["lines"] == 6
    # The ring is outline-only: it never adds a filled disc.
    assert F.shockwave(0.0)[1] > 0.0
    assert F.shockwave(0.70) == (0.0, 0.0)
    # And reduced motion never draws it.
    frozen = FakeCanvas()
    s_frozen = M.render(frozen, 34.0, 34.0, 32.0, 0.0, "idle",
                        reduced_motion=True)
    assert s_frozen["lines"] == 6


def test_comet_head_drawn_during_sweep_and_not_outside(monkeypatch):
    visible_t, hidden_t = 3.5, 3.0
    assert F.comet(visible_t, 32.0)["alpha"] > 0.0
    assert F.comet(hidden_t, 32.0)["alpha"] == 0.0

    info = F.comet(visible_t, 32.0)
    head = (info["x"] + 34.0, info["y"] + 34.0)
    live = FakeCanvas()
    M.render(live, 34.0, 34.0, 32.0, visible_t, "idle")
    assert any(abs((o[0] + o[2]) / 2 - head[0]) < 1e-6
               and abs((o[1] + o[3]) / 2 - head[1]) < 1e-6
               for o in live.ovals)

    # A zero-alpha comet is not drawn, even when it carries a position.
    monkeypatch.setattr(M, "comet", lambda t, R: {
        "alpha": 0.0, "trail": [], "x": 999.0, "y": 999.0,
        "size": 3.0, "lift": 0.5})
    off = FakeCanvas()
    M.render(off, 34.0, 34.0, 32.0, visible_t, "idle")
    assert not any(abs((o[0] + o[2]) / 2 - 1033.0) < 1e-6 for o in off.ovals)

    # Isolate it: with the comet suppressed, the head and its visible tail
    # samples are exactly the ovals that disappear.
    monkeypatch.setattr(M, "comet", lambda t, R: {"alpha": 0.0, "trail": []})
    quiet = FakeCanvas()
    M.render(quiet, 34.0, 34.0, 32.0, visible_t, "idle")
    expected = 1 + sum(1 for s in info["trail"] if s["alpha"] > 1e-3)
    assert len(live.ovals) - len(quiet.ovals) == expected


def test_dust_field_adds_its_motes(monkeypatch):
    t = 0.70  # quiet between beats, comet hidden
    full = FakeCanvas()
    M.render(full, 34.0, 34.0, 32.0, t, "idle")
    monkeypatch.setattr(M, "dust_state", lambda tt, R: [])
    without = FakeCanvas()
    M.render(without, 34.0, 34.0, 32.0, t, "idle")
    assert len(full.ovals) - len(without.ovals) == len(F.dust_state(t, 32.0))
    assert len(F.dust_state(t, 32.0)) == F.DUST_COUNT
    # And a reduced-motion frame carries none of them.
    frozen = FakeCanvas()
    M.render(frozen, 34.0, 34.0, 32.0, t, "idle", reduced_motion=True)
    assert len(frozen.ovals) == len(M.PARTICLES) + 2


def test_sparkles_are_deterministic_and_turn_on_and_off():
    visible_at, gone_at = 24.15, 46.7
    visible = M.sparkles(visible_at, 32.0)
    assert len(visible) > 0
    assert M.sparkles(gone_at, 32.0) == []
    # Deterministic: the same clock yields the identical glints (never random).
    assert visible == M.sparkles(visible_at, 32.0)
    assert len(visible) <= F.SPARKLE_COUNT


def test_state_hue_tints_the_mark_through_embers_palette():
    idle, listening = FakeCanvas(), FakeCanvas()
    M.render(idle, 34.0, 34.0, 32.0, 0.0, "idle")
    M.render(listening, 34.0, 34.0, 32.0, 0.0, "listening")
    idle_fills = {o[4]["fill"] for o in idle.ovals}
    listen_fills = {o[4]["fill"] for o in listening.ovals}
    assert idle_fills != listen_fills
    # Same palette source as the ember renderer, so the orb's state language
    # cannot drift from the rest of the product.
    core = E.state_core("listening")
    assert M.resolve_core("listening") == core


def test_palette_override_wins_for_cross_fade():
    assert M.resolve_core("idle", colors={"core": (10, 20, 30)}) == (10, 20, 30)
    assert M.resolve_core("idle", colors=(1, 2, 3)) == (1, 2, 3)


def test_bad_inputs_never_raise():
    c = FakeCanvas()
    M.render(c, 0.0, 0.0, 0.0, "nonsense", "not-a-state")  # degenerate radius/state
    M.render(c, 10.0, 10.0, 32.0, 1.0, "idle", level="bad")  # bad meter
    M.render(c, 10.0, 10.0, 32.0, 1.0, "idle", spectrum="bad",
             spec_peaks=object(), colors={"core": "bad"})  # bad spectrum/palette
    M.render(None, 10.0, 10.0, 32.0, None, None)  # None canvas/state/time
    M.render(c, float("nan"), float("inf"), -5.0, float("inf"), "idle")
    assert c.ovals or c.lines
    # The field the renderer consumes is equally fail-soft: a bad clock still
    # yields a finite frame rather than raising.
    assert len(M.particle_state("bad", 0.0)) == len(M.PARTICLES)
    assert isinstance(M.sparkles("bad", 0.0), list)
    assert isinstance(M.dust_state(None, 32.0), list)
    assert isinstance(M.comet("bad", 32.0), dict)
    assert isinstance(M.trails("bad", 32.0, M.particle_state(0.0, 32.0)[0]), list)


def test_new_constants_are_exported():
    for name in ("RX_FRAC", "RY_FRAC", "TILT_A", "TILT_B", "NUCLEUS_FRAC",
                 "FRINGE_PX", "LIFT_RING", "LIFT_BEAD", "LIFT_NUCLEUS",
                 "MIN_MARK_BRIGHTNESS", "HALO_SCALE", "HALO_BASE",
                 "HALO_WIDTH", "SHOCKWAVE_WIDTH", "RING_SWAY_DEG",
                 "RING_PERIOD_NEAR", "RING_PERIOD_FAR", "PARTICLES",
                 "heartbeat", "nucleus_scale", "shockwave", "camera_offset",
                 "orbit_tilts", "orbit_point", "particle_state", "trails",
                 "dust_state", "comet", "sparkles", "resolve_core"):
        assert name in M.__all__, name
        assert hasattr(M, name), name


def test_app_defaults_to_the_mark(monkeypatch):
    # The owner took the mark as the orb's default look: draw_orb_frame with
    # no style override must produce the mark's structure (2 fringed orbits +
    # beads/trails/dust + nucleus/halo + glints), not the ember bands.
    monkeypatch.delenv("QUBIT_ORB_STYLE", raising=False)
    from voicebridge.orb import app as orb_app

    class C:
        def __init__(self):
            self.ovals, self.lines = [], []

        def create_oval(self, *a, **k):
            self.ovals.append(a)
            return 1

        def create_line(self, *a, **k):
            self.lines.append(a)
            return 1

    stats = orb_app.draw_orb_frame(C(), 68, "idle", 4.2)
    # 2 orbits x (blue fringe + red fringe + line), plus the shockwave ring
    # when the clock happens to be on a beat.
    assert stats["lines"] in (6, 7)
    assert stats["ovals"] >= len(M.PARTICLES) + 2


def test_unknown_style_falls_back_to_ember(monkeypatch):
    monkeypatch.setenv("QUBIT_ORB_STYLE", "not-a-style")
    from voicebridge.orb import app as orb_app

    class C:
        def __init__(self):
            self.ovals, self.lines = [], []

        def create_oval(self, *a, **k):
            self.ovals.append(a)
            return 1

        def create_line(self, *a, **k):
            self.lines.append(a)
            return 1

    stats = orb_app.draw_orb_frame(C(), 68, "idle", 4.2)
    assert stats["lines"] >= 4  # ember bands still drew; a typo never blanks the orb
