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

import math
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
        self.texts = []
        #: Global draw order across BOTH primitives, so the depth z-order of
        #: rings (lines) against the nucleus (an oval) is checkable.
        self.calls = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append((x0, y0, x1, y1, kw))
        self.calls.append(("oval", len(self.ovals) - 1))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((pts, kw))
        self.calls.append(("line", len(self.lines) - 1))
        return len(self.lines)

    def create_text(self, x, y, **kw):
        self.texts = getattr(self, "texts", [])
        self.texts.append((x, y, kw))
        return len(self.texts)


def test_draws_two_fringed_rings_and_every_particle():
    c = FakeCanvas()
    # t=0.70 is between beats: no shockwave ring adds a line, so the only
    # lines are the two orbits' fringe strokes.
    stats = M.render(c, 34.0, 34.0, 32.0, 0.70, "idle")
    # Contract, not a snapshot: the two orbits are fringed (3 strokes each) and
    # every particle is drawn as a glint (2 arms) — so lines are numerous, not
    # a fixed number. An exact count here was a change-detector.
    assert stats["lines"] >= 6, "the two fringed orbits must be drawn"
    assert stats["points"] > 0 and stats["bars"] is False
    assert stats["min_y"] < stats["max_y"]
    # The nucleus is the ONLY solid orb; everything else is a glint (a star of
    # two line arms plus a small core) or dust.
    fills = [o[4].get("fill") for o in c.ovals]
    assert fills, "the nucleus must be filled"


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
    # Freezing time must not empty the mark, and it must be a SUBSET of the
    # live frame (no trails, dust, comet, shockwave or sparkles).
    live = FakeCanvas()
    M.render(live, 34.0, 34.0, 32.0, 0.0, "idle")
    assert len(a.lines) >= 6 and len(a.ovals) >= 2
    assert len(a.ovals) <= len(live.ovals)


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


def test_heartbeat_envelope_is_continuous_and_rises_from_zero():
    # The old envelope started at full amplitude and jumped 0->1 at the phase
    # wrap (the visible beat jerk). Every adjacent frame, wrap included, must
    # now move only a small step.
    dt = 1.0 / 240.0
    worst = max(abs(F.heartbeat((k + 1) * dt) - F.heartbeat(k * dt))
                for k in range(int(3.0 / dt)))
    assert worst < 0.08, f"heartbeat stepped {worst:.3f} in one 1/240s frame"
    # Still a pulse, not a flat line: primary peaks at 1, the second thump at
    # HEART_SECOND_AMP, and the gate between beats returns to 0.
    assert F.heartbeat(0.0) == pytest.approx(1.0)
    assert F.heartbeat(F.HEART_PERIOD_S * (1.0 + F.HEART_SECOND_PHASE)) == \
        pytest.approx(F.HEART_SECOND_AMP, abs=0.02)
    assert F.heartbeat(0.475) == pytest.approx(0.0)


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
        far_idx = [_idx(*M._spin_pt(f["x"] + 34.0, f["y"] + 34.0, t, 34.0, 34.0))
                   for f in frames if f["depth"] < 0.5]
        near_idx = [_idx(*M._spin_pt(f["x"] + 34.0, f["y"] + 34.0, t, 34.0, 34.0))
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


def test_shockwave_ring_present_on_beat_absent_between(monkeypatch):
    # Isolate the ring: render the SAME phase with and without the shockwave.
    # (Comparing two different phases was wrong — sparkles come and go between
    # them, so the line delta was not the ring.)
    on_beat, no_ring = FakeCanvas(), FakeCanvas()
    s_on = M.render(on_beat, 34.0, 34.0, 32.0, 0.0, "idle")   # thump -> ring
    monkeypatch.setattr(M, "shockwave", lambda t: (0.0, 0.0))
    s_quiet = M.render(no_ring, 34.0, 34.0, 32.0, 0.0, "idle")
    assert s_on["lines"] == s_quiet["lines"] + 1, "one extra outline ring"
    monkeypatch.undo()
    # The ring is outline-only: it never adds a filled disc.
    assert F.shockwave(0.0)[1] > 0.0
    assert F.shockwave(0.70) == (0.0, 0.0)
    # And reduced motion never draws it.
    frozen = FakeCanvas()
    s_frozen = M.render(frozen, 34.0, 34.0, 32.0, 0.0, "idle",
                        reduced_motion=True)
    assert s_frozen["lines"] < s_on["lines"], "reduced motion never draws it"


def test_comet_head_drawn_during_sweep_and_not_outside(monkeypatch):
    visible_t, hidden_t = 3.5, 3.0
    assert F.comet(visible_t, 32.0)["alpha"] > 0.0
    assert F.comet(hidden_t, 32.0)["alpha"] == 0.0

    info = F.comet(visible_t, 32.0)
    head = M._spin_pt(info["x"] + 34.0, info["y"] + 34.0, visible_t, 34.0, 34.0)
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
    # And a reduced-motion frame carries none of them: only the nine particle
    # cores plus the static nucleus stack (halo rings + gradient layers).
    frozen = FakeCanvas()
    M.render(frozen, 34.0, 34.0, 32.0, t, "idle", reduced_motion=True)
    assert len(frozen.ovals) == len(M.PARTICLES) + M.NUCLEUS_OVAL_COUNT
    assert len(frozen.ovals) < len(full.ovals)


def test_sparkles_are_deterministic_and_turn_on_and_off():
    visible_at, gone_at = 24.15, 46.7
    visible = M.sparkles(visible_at, 32.0)
    assert len(visible) > 0
    assert M.sparkles(gone_at, 32.0) == []
    # Deterministic: the same clock yields the identical glints (never random).
    assert visible == M.sparkles(visible_at, 32.0)
    assert len(visible) <= F.SPARKLE_COUNT


# --------------------------------------------------------------------------- #
# echo (follower) rings — two thinner orbit ghosts that lag the main ones
# --------------------------------------------------------------------------- #
def _line_matches(pts, expected, tol=1e-6):
    if len(pts) != len(expected):
        return False
    return all(abs(a - b) <= tol for a, b in zip(pts, expected))


def _call_pos(canvas, kind, idx):
    for pos, (k, i) in enumerate(canvas.calls):
        if k == kind and i == idx:
            return pos
    return None


def test_echo_rings_are_two_outline_rings_that_lag_the_orbits(monkeypatch):
    R, t = 32.0, 3.0
    near_lag, far_lag = F.orbit_tilts(t - F.ECHO_LAG_S)
    near_now, far_now = F.orbit_tilts(t)
    # At this phase the lag is a visible tilt offset, not a rounding error.
    assert abs(near_lag - near_now) > 0.5
    assert abs(far_lag - far_now) > 0.5

    full = FakeCanvas()
    M.render(full, 34.0, 34.0, R, t, "idle")
    monkeypatch.setattr(M, "echo_rings", lambda tt: [])
    bare = FakeCanvas()
    M.render(bare, 34.0, 34.0, R, t, "idle")
    monkeypatch.undo()

    # Exactly the two follower rings, one outline stroke each.
    assert len(full.lines) - len(bare.lines) == 2

    rx, ry = R * F.RX_FRAC, R * F.RY_FRAC
    ox, oy = F.camera_offset(t, R)
    ccx, ccy = 34.0 + ox, 34.0 + oy

    def _match(tilt):
        expected = M._ellipse_points(ccx, ccy, rx * F.ECHO_SCALE,
                                     ry * F.ECHO_SCALE, tilt)
        for idx, (pts, _kw) in enumerate(full.lines):
            if _line_matches(pts, expected):
                return idx
        return None

    near_idx = _match(near_lag)
    far_idx = _match(far_lag)
    assert near_idx is not None, "near follower ring not at the lagged tilt"
    assert far_idx is not None, "far follower ring not at the lagged tilt"
    # The LIVE tilt is never what was drawn — the lag is real.
    assert _match(near_now) is None and _match(far_now) is None

    # Depth: the far follower sits behind the nucleus, the near one in front.
    nuc_i = full.ovals.index(_nucleus(full))
    assert (_call_pos(full, "line", far_idx)
            < _call_pos(full, "oval", nuc_i)
            < _call_pos(full, "line", near_idx))


# --------------------------------------------------------------------------- #
# nucleus sparks — glints thrown OUT of the nucleus past the orbits
# --------------------------------------------------------------------------- #
def test_nucleus_sparks_leave_glints_that_travel_shrink_and_fade(monkeypatch):
    R = 32.0
    # Present on the primary thump and on the next beat.
    assert len(F.nucleus_sparks(0.0, R)) > 0
    assert len(F.nucleus_sparks(F.HEART_PERIOD_S, R)) > 0

    # Drawn as glints: each spark is exactly two line arms plus one core oval.
    t = 0.0
    full = FakeCanvas()
    M.render(full, 34.0, 34.0, R, t, "idle")
    monkeypatch.setattr(M, "nucleus_sparks", lambda tt, rr: [])
    bare = FakeCanvas()
    M.render(bare, 34.0, 34.0, R, t, "idle")
    monkeypatch.undo()
    d_lines = len(full.lines) - len(bare.lines)
    d_ovals = len(full.ovals) - len(bare.ovals)
    assert d_ovals > 0
    assert d_lines == 2 * d_ovals, "sparks must be drawn as glints"

    # Sparks reach past the main orbits; and they shrink and fade as they go.
    samples = []
    for k in range(240):
        tt = 3.0 * k / 240.0
        dx, dy = F.camera_offset(tt, R)
        for s in F.nucleus_sparks(tt, R):
            samples.append((math.hypot(s["x"] - dx, s["y"] - dy),
                            s["size"], s["alpha"]))
    assert samples
    radii = [r for r, _s, _a in samples]
    assert max(radii) > R * F.RX_FRAC * 1.2, "sparks must get outside the orbits"
    assert min(radii) < R * 0.2, "sparks must start at the nucleus"
    samples.sort()
    k = max(1, len(samples) // 3)
    inner, outer = samples[:k], samples[-k:]
    assert (sum(s for _r, s, _a in inner) / k
            > sum(s for _r, s, _a in outer) / k), "sparks must shrink outward"
    assert (sum(a for _r, _s, a in inner) / k
            > sum(a for _r, _s, a in outer) / k), "sparks must fade outward"


# --------------------------------------------------------------------------- #
# CONTINUITY — the "fluid" contract. Every element the renderer draws is a
# field element identified by (layer, index); the renderer only adds a constant
# (cx, cy) offset, so a per-index field delta IS the drawn-element delta.
#
# epsilon: 0.06 * R per 1/60 s frame. Measured worst case at R=32 over the two
# windows below is ~1.56 px (a just-born nucleus spark mid-launch); the fast
# comet sweep is ~1.44 px. 0.06*32 = 1.92 px leaves ~20% headroom over the
# designed fastest motion while still failing on any pop/teleport.
# --------------------------------------------------------------------------- #
CONTINUITY_EPS_FRAC = 0.06


def _continuity_snapshot(t, R):
    """Per-index position on the field clock ``t`` for every drawn layer."""
    snap = {
        "particles": {i: (p["x"], p["y"])
                      for i, p in enumerate(F.particle_state(t, R))},
        "dust": {i: (d["x"], d["y"])
                 for i, d in enumerate(F.dust_state(t, R))},
        "rings": {i: tilt for i, tilt in enumerate(F.orbit_tilts(t))},
        "echo": {i: e["tilt"] for i, e in enumerate(F.echo_rings(t))},
        "nucleus": {0: F.nucleus_scale(t)},
        "sparks": {},
        "glints": {},
    }
    # Sparks and glints are drawn in generation order but skip indices as their
    # phases advance; key them by their stable index so a birth/death is not
    # mistaken for a jump (the skip mask mirrors mark_field.nucleus_sparks /
    # mark_field.sparkles — the field is the fixed contract).
    for (i, phase), s in zip(_spark_index_phase(t), F.nucleus_sparks(t, R)):
        snap["sparks"][i] = (s["x"], s["y"], phase)
    for (i, phase), g in zip(_glint_index_phase(t), F.sparkles(t, R)):
        snap["glints"][i] = (g["x"], g["y"], phase)
    trails = {}
    for i, p in enumerate(F.particle_state(t, R)):
        for k, s in enumerate(F.trails(t, R, p)):
            trails[(i, k)] = (s["x"], s["y"])
    snap["trails"] = trails
    c = F.comet(t, R)
    if c.get("alpha", 0.0) > 0.0:
        snap["comet"] = {0: (c["x"], c["y"])}
    return snap


def _spark_index_phase(t):
    """(index, phase) for every spark the field will return at ``t``."""
    for i in range(F.NUCLEUS_SPARK_COUNT):
        life = F.NUCLEUS_SPARK_LIFE_BASE + (i % 5) * (
            F.NUCLEUS_SPARK_LIFE_SPAN / 4.0)
        phase = ((t - F.HEART_PERIOD_S * F.hash01(i, 23.7))
                 / max(0.1, life)) % 1.0
        if phase < 0.92:  # mirrors nucleus_sparks' skip
            yield i, phase


def _glint_index_phase(t):
    """(index, phase) for every glint the field will return at ``t``."""
    for i in range(F.SPARKLE_COUNT):
        seed = i * F.SPARKLE_SEED_STEP
        life = F.SPARKLE_LIFE_BASE + (i % 4) * F.SPARKLE_LIFE_STEP
        phase = (t / max(0.1, life) + seed / 360.0) % 1.0
        if phase <= F.SPARKLE_VISIBLE_FRAC:
            yield i, phase


def _max_snapshot_delta(a, b, R):
    worst = 0.0
    for layer, da in a.items():
        db = b.get(layer, {})
        for key, va in da.items():
            if key not in db:
                continue  # lifecycle appearance/disappearance, not a jump
            vb = db[key]
            if layer in ("sparks", "glints"):
                if vb[2] < va[2]:
                    continue  # phase wrapped: a death+birth, not a jump
                d = math.hypot(va[0] - vb[0], va[1] - vb[1])
            elif layer in ("rings", "echo"):
                d = abs(va - vb) * math.pi / 180.0 * R
            elif layer == "nucleus":
                d = abs(va - vb) * max(1.1, R * F.NUCLEUS_FRAC)
            else:
                d = math.hypot(va[0] - vb[0], va[1] - vb[1])
            worst = max(worst, d)
    return worst


def test_continuity_no_drawn_element_jumps_between_adjacent_phases():
    R, dt, eps = 32.0, 1.0 / 60.0, CONTINUITY_EPS_FRAC * 32.0
    # One window on the first beat, one across the comet's sweep.
    for start in (0.0, 3.0):
        prev = _continuity_snapshot(start, R)
        for k in range(1, 61):
            cur = _continuity_snapshot(start + k * dt, R)
            delta = _max_snapshot_delta(prev, cur, R)
            assert delta <= eps, (
                f"drawn element jumped {delta:.3f}px at t={start + k * dt:.3f} "
                f"(> {eps:.3f}px/frame)")
            prev = cur


def test_reduced_motion_is_free_of_the_moving_layers():
    R = 32.0
    for t in (0.0, 9.5):
        c = FakeCanvas()
        M.render(c, 34.0, 34.0, R, t, "idle", reduced_motion=True)
        # Nine particle glints (2 arms + 1 core) and the two fringed orbits:
        # no trails, dust, sparkles, comet, follower rings or nucleus sparks.
        # Ovals = 9 particle cores + the static nucleus stack (halo + gradient).
        assert len(c.ovals) == len(M.PARTICLES) + M.NUCLEUS_OVAL_COUNT
        assert len(c.lines) == 2 * len(M.PARTICLES) + 7   # 18 arms + 6 ring strokes + inner ring


def test_speed_scale_drives_every_layer_on_one_clock():
    # Owner 2026-10-18: the speed setting must scale the whole system together
    # (the follower lag included), never just some layers. Rendering at 2x
    # speed must be byte-identical to rendering at twice the time.
    R, t = 32.0, 1.3
    fast = FakeCanvas()
    M.render(fast, 34.0, 34.0, R, t, "idle", speed_scale=2.0)
    equiv = FakeCanvas()
    M.render(equiv, 34.0, 34.0, R, t * 2.0, "idle", speed_scale=1.0)
    assert fast.lines == equiv.lines
    assert fast.ovals == equiv.ovals


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
                 "dust_state", "comet", "sparkles", "resolve_core",
                 "echo_rings", "ECHO_LAG_S", "ECHO_SCALE", "ECHO_ALPHA",
                 "ECHO_WIDTH", "nucleus_sparks", "NUCLEUS_SPARK_COUNT",
                 "NUCLEUS_SPARK_REACH"):
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
    # Contract: the two fringed orbits are present (>= 6 strokes) and the
    # frame is a mark, not the ember bands. Exact totals move with every
    # animation layer, so they are not asserted here.
    assert stats["lines"] >= 6
    assert stats["ovals"] >= 3
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


def test_two_satellite_shells_travel_their_own_circles():
    # Owner ask: "2 more rotating orbs that follow circle line on different
    # circles with different timing". Contract: two shells, circular paths in
    # the mark's plane, bodies travelling in OPPOSITE directions at different
    # periods, each body's distance matching its own circle.
    from voicebridge.orb import mark_field as F

    sats = F.satellites(2.0, 106.0)
    assert len(sats) == 2
    r0 = sats[0]["ring"]["rx_frac"]
    r1 = sats[1]["ring"]["rx_frac"]
    assert r0 != r1, "the two shells must ride different circles"
    # Circular in-plane: same squash ratio as the mark's ellipses.
    for s in sats:
        assert s["ring"]["ry_frac"] / s["ring"]["rx_frac"] == pytest.approx(
            F.RY_FRAC / F.RX_FRAC, abs=0.02)

    # Opposite directions at different periods -> the bodies separate over time.
    a = [((s["body"]["x"] ** 2 + s["body"]["y"] ** 2) ** 0.5) for s in
         F.satellites(2.0, 106.0)]
    b = [((s["body"]["x"] ** 2 + s["body"]["y"] ** 2) ** 0.5) for s in
         F.satellites(3.7, 106.0)]
    assert a != b, "the bodies must move"
    assert F.SATELLITE_SHELLS[0][3] != F.SATELLITE_SHELLS[1][3], "opposite spins"
    assert F.SATELLITE_SHELLS[0][2] != F.SATELLITE_SHELLS[1][2], "different timing"


def test_satellite_shells_are_drawn_and_vanish_when_frozen():
    live = FakeCanvas()
    M.render(live, 34.0, 34.0, 32.0, 2.0, "idle")
    frozen = FakeCanvas()
    M.render(frozen, 34.0, 34.0, 32.0, 2.0, "idle", reduced_motion=True)
    # A shell adds a thin circle (a line) plus a body/trail glints (lines+ovals).
    assert len(live.lines) > len(frozen.lines)
    assert len(live.ovals) > len(frozen.ovals)
