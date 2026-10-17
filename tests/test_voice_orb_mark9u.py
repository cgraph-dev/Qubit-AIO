"""Qubit-mark orb renderer tests (hermetic: FakeCanvas, no display, no Tk).

The orb wears the dashboard sidebar mark: two hairline tilted orbits, a
nucleus, and per-particle beads. These pin the STRUCTURE (so a refactor cannot
silently drop a ring or a particle), the state tint (one palette source with
ember), reduced motion, and the fail-soft guarantees the host relies on.
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
    stats = M.render(c, 34.0, 34.0, 32.0, 1.0, "idle")
    # Two orbits, each drawn as three strokes (blue fringe, red fringe, line).
    assert stats["lines"] == 6
    # Beads + their trails + the nucleus, its halo and the sparkles.
    assert stats["ovals"] >= len(M.PARTICLES) + 1
    assert stats["points"] > 0 and stats["bars"] is False
    assert stats["min_y"] < stats["max_y"]


def _centered(canvas, cx=34.0, cy=34.0, filled=None):
    """The oval centred exactly on (cx, cy); optionally filtered by fill."""
    for o in canvas.ovals:
        if (abs((o[0] + o[2]) / 2 - cx) < 1e-9
                and abs((o[1] + o[3]) / 2 - cy) < 1e-9):
            if filled is True and not o[4].get("fill"):
                continue
            if filled is False and o[4].get("fill"):
                continue
            return o
    return None


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
    # trails and no sparkles.
    assert len(a.lines) == 6 and len(a.ovals) == len(M.PARTICLES) + 2


def test_heartbeat_pulses_nucleus_size_and_halo_opacity():
    beat, rest = FakeCanvas(), FakeCanvas()
    M.render(beat, 34.0, 34.0, 32.0, 0.0, "idle")
    M.render(rest, 34.0, 34.0, 32.0, M.HEART_PERIOD_S * 0.5, "idle")
    nb, nr = _nucleus(beat), _nucleus(rest)
    assert nb is not None and nr is not None
    # Scale envelope is larger on the primary beat than at rest.
    assert (nb[2] - nb[0]) > (nr[2] - nr[0])
    # The halo reads strongest (more saturated) on the beat.
    hb, hr = _halo(beat), _halo(rest)
    assert hb is not None and hr is not None
    assert _lum(hb[4]["outline"]) > _lum(hr[4]["outline"])
    # Pure envelope: 1.00 -> 1.18 -> 1.00, periodic, with the second thump.
    assert M.heart_envelope(0.0) == pytest.approx(1.0 + M.HEART_SCALE_AMP)
    assert M.heart_envelope(M.HEART_PERIOD_S) == pytest.approx(1.0 + M.HEART_SCALE_AMP)
    assert M.heart_envelope(M.HEART_PERIOD_S * 0.5) == pytest.approx(1.0)
    second = M.heart_envelope(M.HEART_SECOND_PHASE * M.HEART_PERIOD_S)
    assert second == pytest.approx(
        1.0 + M.HEART_SCALE_AMP * M.HEART_SECOND_AMP)
    assert M.heart_beat(0.0) == pytest.approx(1.0)
    assert M.heart_beat(M.HEART_PERIOD_S * 0.5) == pytest.approx(0.0)


def test_far_beads_draw_behind_and_near_beads_in_front():
    for t in (0.0, 1.0):
        c = FakeCanvas()
        M.render(c, 34.0, 34.0, 32.0, t, "idle")
        near_tilt, far_tilt = M.orbit_tilts(t)
        frames = M.particle_frames(34.0, 34.0, 32.0, t, near_tilt, far_tilt)
        nuc_idx = next(i for i, o in enumerate(c.ovals)
                       if _nucleus(c) is o)

        def _idx(x, y):
            for i, o in enumerate(c.ovals):
                if (abs((o[0] + o[2]) / 2 - x) < 1e-9
                        and abs((o[1] + o[3]) / 2 - y) < 1e-9):
                    return i
            return None

        far_idx = [_idx(f["x"], f["y"]) for f in frames if f["depth"] < 0.5]
        near_idx = [_idx(f["x"], f["y"]) for f in frames if f["depth"] >= 0.5]
        # Both halves are populated at these phases.
        assert far_idx and near_idx
        assert all(i is not None and i < nuc_idx for i in far_idx)
        assert all(i is not None and i > nuc_idx for i in near_idx)


def test_sparkles_appear_at_one_phase_and_vanish_at_another():
    core = M.resolve_core("idle")
    present_at = 1.0
    gone_at = 1.8
    visible = M.sparkles(34.0, 34.0, 32.0, present_at, core, 30.0)
    assert len(visible) > 0
    assert M.sparkles(34.0, 34.0, 32.0, gone_at, core, 30.0) == []
    # Deterministic: the same clock yields the identical glints (never random).
    assert visible == M.sparkles(34.0, 34.0, 32.0, present_at, core, 30.0)
    # And the renderer emits exactly that many extra ovals.
    present, gone = FakeCanvas(), FakeCanvas()
    M.render(present, 34.0, 34.0, 32.0, present_at, "idle")
    M.render(gone, 34.0, 34.0, 32.0, gone_at, "idle")
    assert len(present.ovals) - len(gone.ovals) == len(visible)


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
    # The new geometry helpers are equally fail-soft: a bad clock rests at a
    # finite frame rather than raising.
    assert M.heart_envelope("bad") == 1.0
    assert M.heart_beat(None) in (0.0, 1.0)
    assert M.orbit_tilts("bad") == pytest.approx((M.TILT_A, M.TILT_B), abs=1e-6)
    assert len(M.particle_frames(0.0, 0.0, 0.0, "bad", 30.0, -30.0)) == \
        len(M.PARTICLES)
    assert isinstance(M.sparkles(0.0, 0.0, 0.0, "bad", (0, 0, 0), 30.0), list)


def test_new_constants_are_exported():
    for name in ("HEART_PERIOD_S", "HEART_SCALE_AMP", "HEART_SECOND_AMP",
                 "HEART_SECOND_PHASE", "HEART_THUMP_S", "HALO_SCALE",
                 "DEPTH_RADIUS_BASE", "DEPTH_RADIUS_GAIN", "DEPTH_OPACITY_BASE",
                 "DEPTH_OPACITY_GAIN", "DEPTH_LIFT_BASE", "DEPTH_LIFT_GAIN",
                 "DEPTH_PARALLAX", "TRAIL_COUNT", "TRAIL_STEP_NEAR",
                 "TRAIL_STEP_FAR", "TRAIL_ALPHA", "TRAIL_RADIUS_SCALE",
                 "SPARKLE_COUNT", "SPARKLE_SEED_STEP", "SPARKLE_SPEED_BASE",
                 "SPARKLE_SPEED_STEP", "SPARKLE_LIFE_BASE", "SPARKLE_LIFE_STEP",
                 "SPARKLE_VISIBLE_FRAC", "SPARKLE_R0", "SPARKLE_R1",
                 "SPARKLE_RADIUS_BASE", "SPARKLE_RADIUS_GAIN",
                 "SPARKLE_WHITE_MIX", "heart_envelope", "heart_beat",
                 "orbit_tilts", "particle_frames", "sparkles"):
        assert name in M.__all__, name
        assert hasattr(M, name), name


def test_app_defaults_to_the_mark(monkeypatch):
    # The owner took the mark as the orb's default look: draw_orb_frame with
    # no style override must produce the mark's structure (2 fringed orbits +
    # beads/trails + nucleus/halo + sparkles), not the ember bands.
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
    assert stats["lines"] == 6          # 2 orbits x (blue fringe + red fringe + line)
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
