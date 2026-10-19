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


def test_draws_translucent_rings_and_every_particle():
    c = FakeCanvas()
    # t=0.70 is between beats: no shockwave ring adds a line, so the lines are
    # the declared circles (one plain stroke each) plus every particle glint.
    stats = M.render(c, 34.0, 34.0, 32.0, 0.70, "idle")
    # Contract, not a snapshot: the rings are single translucent strokes and
    # every particle is drawn as a glint (2 arms) — so lines are numerous, not
    # a fixed number. An exact count here was a change-detector.
    assert stats["lines"] >= 6, "the declared circles must be drawn"
    assert stats["points"] > 0 and stats["bars"] is False
    assert stats["min_y"] < stats["max_y"]
    # The nucleus is the ONLY solid orb; everything else is a glint (a star of
    # two line arms plus a small core) or dust.
    fills = [o[4].get("fill") for o in c.ovals]
    assert fills, "the nucleus must be filled"


def _centered(canvas, cx=34.0, cy=34.0, filled=None):
    """The oval nearest (cx, cy); optionally filtered by fill.

    Used for the SEAM rings: the body does not sway with the field's camera
    offset, so the halo stack sits exactly on the geometric centre.
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


def _nucleus(canvas, cx=34.0, cy=34.0, R=32.0, t=0.0, tol=1e-6):
    """The nucleus BODY: the concentric filled oval at its own declared radius.

    "The nearest oval" no longer identifies it — the studio nucleus shares its
    centre with the beat bloom, and a caustic and two swirl lobes orbit inside
    it — so the body's own radius law (``mark.nucleus_radius``) does. Pinning
    it that way also checks the size law, not just a position.
    """
    want = M.nucleus_radius(R, t)
    for o in canvas.ovals:
        ox, oy = (o[0] + o[2]) / 2, (o[1] + o[3]) / 2
        if abs(ox - cx) > tol or abs(oy - cy) > tol:
            continue
        if abs(abs((o[2] - o[0]) / 2) - want) <= tol:
            return o
    return None


def _halo(canvas):
    """The FIRST outline-only oval: the outermost soft halo ring."""
    for o in canvas.ovals:
        if not o[4].get("fill"):
            return o
    return None


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
    nb = _nucleus(beat, R=32.0, t=0.0)
    nr = _nucleus(rest, R=32.0, t=0.475)
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

        nuc = _nucleus(c, R=32.0, t=t)
        nuc_idx = c.ovals.index(nuc)

        def _locate(f):
            # The renderer threads each element's own depth through the Y+X
            # tumble, so the locator must too: a depth-free helper lands on
            # the wrong pixel once the turn is a real 3-D rotation. The depth
            # goes through turn_qdepth (hash-broken, flat per element) and the
            # screen-space z/z2 volume offset, exactly as the renderer does.
            # Tumble first, then the master spin — the renderer's own order.
            qd = M.turn_qdepth(f.get("depth", 0.5), f.get("index"), flat=True)
            px, py, _g = M.turn_point(t, 32.0, f["x"], f["y"], qd)
            ox, oy = M.volume_offset(t, 32.0, f.get("z"), f.get("z2"))
            px, py = px + ox, py + oy
            a = math.radians(math.fmod(M.spin_deg(t), 360.0))
            rx = px * math.cos(a) - py * math.sin(a)
            ry = px * math.sin(a) + py * math.cos(a)
            return (34.0 + rx, 34.0 + ry)

        far_idx = [_idx(*_locate(f)) for f in frames if f["depth"] < 0.5]
        near_idx = [_idx(*_locate(f)) for f in frames if f["depth"] >= 0.5]
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

    halo_idx = c.ovals.index(_halo(c))
    nuc_idx = c.ovals.index(_nucleus(c, 40.0, 40.0, R=40.0, t=0.0))
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
    # Just after the thump: the ring's short attack has brought it to a
    # visible alpha (it starts at 0, so t=0 is intentionally blank now).
    t_ring = F.SHOCKWAVE_ATTACK_FRAC * F.SHOCKWAVE_S * 1.5
    s_on = M.render(on_beat, 34.0, 34.0, 32.0, t_ring, "idle")
    monkeypatch.setattr(M, "shockwave", lambda t: (0.0, 0.0))
    s_quiet = M.render(no_ring, 34.0, 34.0, 32.0, t_ring, "idle")
    assert s_on["lines"] == s_quiet["lines"] + 1, "one extra outline ring"
    monkeypatch.undo()
    # The ring is outline-only: it never adds a filled disc.
    assert F.shockwave(0.0)[1] == pytest.approx(0.0)
    assert F.shockwave(t_ring)[1] > 0.0
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
    # Depth-aware locator: the head rides the Y+X tumble at its own depth
    # (tumble first, then the master spin — the renderer's own order).
    # The projection depth goes through turn_qdepth (flat with the comet seed)
    # and the comet's screen-space z/z2 volume offset, exactly as the
    # renderer does.
    _qd = M.turn_qdepth(info.get("depth", 0.5), info.get("index", 0.0),
                        flat=True)
    _px, _py, _g = M.turn_point(visible_t, 32.0, info["x"], info["y"], _qd)
    _ox, _oy = M.volume_offset(visible_t, 32.0, info.get("z"),
                               info.get("z2"))
    _px, _py = _px + _ox, _py + _oy
    _a = math.radians(math.fmod(M.spin_deg(visible_t), 360.0))
    head = (34.0 + _px * math.cos(_a) - _py * math.sin(_a),
            34.0 + _px * math.sin(_a) + _py * math.cos(_a))
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
    visible_at = 24.15
    visible = M.sparkles(visible_at, 32.0)
    assert len(visible) > 0
    # Denser field: no single clock hides all forty at once, so on/off is a
    # per-dot property — over one sweep every dot is both seen and missed.
    seen = {i: [False, False] for i in range(F.SPARKLE_COUNT)}
    t = 0.0
    while t < 60.0:
        present = {d["index"] for d in F.sparkles(t, 32.0)}
        for i in seen:
            seen[i][0] |= float(i) in present
            seen[i][1] |= float(i) not in present
        t += 0.05
    assert all(on and off for on, off in seen.values())
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


def test_echo_rings_are_two_declared_follower_strokes(monkeypatch):
    # The circles are DECLARED once by the field now, so a follower is no
    # longer an ad-hoc ellipse assembled beside the orbits: it is one of the
    # declared entries, and the renderer only chooses its look.
    R, t = 32.0, 3.0
    near_lag, far_lag = F.orbit_tilts(t - F.ECHO_LAG_S)
    near_now, far_now = F.orbit_tilts(t)
    # At this phase the lag is a visible tilt offset, not a rounding error.
    assert abs(near_lag - near_now) > 0.5
    assert abs(far_lag - far_now) > 0.5

    specs = F.ring_geometry(t)
    echoes = [sp for sp in specs if sp["layer"] == "echo"]
    assert len(echoes) == 2, "one declared follower per main orbit"
    assert {str(sp["orbit"]) for sp in echoes} == {"a", "b"}
    for sp in echoes:
        assert sp["rx_frac"] == pytest.approx(F.RX_FRAC * F.ECHO_SCALE)
        assert sp["ry_frac"] == pytest.approx(F.RY_FRAC * F.ECHO_SCALE)
        # The declaration composes its own (second) lag, which is NOT the
        # single-lag follower the shared echo contract uses everywhere else.
        assert sp["tilt"] != pytest.approx(
            near_lag if str(sp["orbit"]) == "a" else far_lag, abs=1e-6)
    assert M.echo_follower_tilt(t, "a") == pytest.approx(near_lag)
    assert M.echo_follower_tilt(t, "b") == pytest.approx(far_lag)
    # The LIVE tilt is never what a follower sits at — the lag is real.
    assert abs(M.echo_follower_tilt(t, "a") - near_now) > 0.5
    assert abs(M.echo_follower_tilt(t, "b") - far_now) > 0.5

    full = FakeCanvas()
    M.render(full, 34.0, 34.0, R, t, "idle")
    monkeypatch.setattr(
        M, "ring_geometry",
        lambda tt: [sp for sp in specs if sp["layer"] != "echo"])
    bare = FakeCanvas()
    M.render(bare, 34.0, 34.0, R, t, "idle")
    monkeypatch.undo()

    # Exactly the two follower rings, one outline stroke each.
    assert len(full.lines) - len(bare.lines) == 2

    # Depth: drawn one at a time, the far follower sits behind the nucleus and
    # the near one in front, so the pair takes part in the z-seam.
    for orbit, behind in (("b", True), ("a", False)):
        only = [sp for sp in echoes if str(sp["orbit"]) == orbit]
        zero = FakeCanvas()
        monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
        M.render(zero, 34.0, 34.0, R, t, "idle")
        c = FakeCanvas()
        monkeypatch.setattr(M, "ring_geometry", lambda tt, o=only: o)
        M.render(c, 34.0, 34.0, R, t, "idle")
        monkeypatch.undo()
        # One follower is exactly one plain translucent stroke: the ring
        # fringe was retired (owner 2026-10-19).
        assert len(c.lines) - len(zero.lines) == 1
        nuc_pos = _call_pos(c, "oval", c.ovals.index(_nucleus(c, R=R, t=t)))
        # Each half of the z-seam opens with its circles, so the follower's
        # stroke is the first of its pass.
        pos = _call_pos(c, "line", 0 if behind else len(zero.lines))
        assert (pos < nuc_pos) if behind else (pos > nuc_pos)


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
# epsilon: 0.065 * R per 1/60 s frame. Measured worst case at R=32 over the two
# windows below is ~1.93 px (a just-born nucleus spark mid-launch, sampled one
# frame after birth now the shower is 32 strong); the fast comet sweep is
# ~1.44 px. The nucleus spark launches on ``f**0.65``, whose slope is unbounded
# at birth, so the worst sampled launch frame moves with the count: at 0.065*32
# = 2.08 px the budget still clears the measured designed maximum by ~8% while
# remaining far below any pop/teleport (a dropped or wrapped element jumps many
# pixels, not fractions of one).
# --------------------------------------------------------------------------- #
CONTINUITY_EPS_FRAC = 0.065


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
    # phases advance; key them by the field's own stable index so a birth/death
    # is not mistaken for a jump. The phase is recovered from alpha (the field
    # documents alpha = SPARK_ALPHA * (1 - phase/VISIBLE) ** 1.5), so no test
    # has to mirror the field's lifecycle mask.
    for s in F.nucleus_sparks(t, R):
        f = 1.0 - (s["alpha"] / F.NUCLEUS_SPARK_ALPHA) ** (2.0 / 3.0)
        snap["sparks"][int(s["index"])] = (s["x"], s["y"], f)
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
        # Nine particle glints (2 arms + 1 core) and the two main orbits plus
        # the core ring, each one plain translucent stroke:
        # no trails, dust, sparkles, comet, follower rings or nucleus sparks.
        # Ovals = 9 particle cores + the static nucleus stack (halo + gradient).
        assert len(c.ovals) == len(M.PARTICLES) + M.NUCLEUS_OVAL_COUNT
        assert len(c.lines) == 2 * len(M.PARTICLES) + 5   # 18 arms + 5 rings (2 main + core + 2 accent)


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
                 "LIFT_RING", "LIFT_BEAD", "LIFT_NUCLEUS",
                 "MIN_MARK_BRIGHTNESS", "HALO_SCALE", "HALO_BASE",
                 "HALO_WIDTH", "SHOCKWAVE_WIDTH", "RING_SWAY_DEG",
                 "RING_PERIOD_NEAR", "RING_PERIOD_FAR", "PARTICLES",
                 "RING_TRANSLUCENCY",
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
    # no style override must produce the mark's structure (translucent orbits +
    # beads/dust + nucleus/halo + glints), not the ember bands.
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
    # Contract: the declared circles are present (>= 6 strokes) and the
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


def test_satellite_shells_travel_their_own_circles():
    # Owner ask: "2 more rotating orbs that follow circle line on different
    # circles with different timing". Contract: a set of counter-rotating
    # shells, circular paths in the mark's plane, bodies travelling in
    # OPPOSITE directions at different periods, each body's distance matching
    # its own circle. The set may grow; what must hold is that every shell
    # keeps its own radius, spin sense and timing.
    from voicebridge.orb import mark_field as F

    sats = F.satellites(2.0, 106.0)
    assert len(sats) >= 2, "the shell set must not shrink"
    radii = [s["ring"]["rx_frac"] for s in sats]
    assert len(set(radii)) == len(radii), \
        "the shells must ride different circles"
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
    spins = [shell[3] for shell in F.SATELLITE_SHELLS]
    periods = [shell[2] for shell in F.SATELLITE_SHELLS]
    assert len(set(spins)) > 1, "the shells must counter-rotate"
    assert len(set(periods)) == len(periods), "different timing"
    assert any(a != b for a in spins for b in spins), "an opposing pair"


def test_satellite_shells_are_drawn_and_vanish_when_frozen():
    live = FakeCanvas()
    M.render(live, 34.0, 34.0, 32.0, 2.0, "idle")
    frozen = FakeCanvas()
    M.render(frozen, 34.0, 34.0, 32.0, 2.0, "idle", reduced_motion=True)
    # A shell adds a thin circle (a line) plus a body/trail glints (lines+ovals).
    assert len(live.lines) > len(frozen.lines)
    assert len(live.ovals) > len(frozen.ovals)


def test_binary_fragments_are_small_and_scatter_across_the_volume():
    # Owner 2026-10-19: "add more ... random 1 0 that spawn everywere small
    # ones". The fragments are no longer parked on the outer orbit: their
    # radius band starts near the core and reaches past the main orbit, and
    # there are more of them, each smaller.
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.23
    bits = F.binary_state(t, R)
    assert len(bits) == F.BINARY_COUNT
    assert F.BINARY_COUNT >= 120, "the fragment field stays dense"
    # The old band was 0.80..1.05 R (one ring); the new one is wide.
    assert F.BINARY_R_FRAC_MIN <= 0.20
    assert F.BINARY_R_FRAC_MIN + F.BINARY_R_FRAC_SPAN >= 1.0
    dx, dy = F.camera_offset(t, R)
    radii = [math.hypot(b["x"] - dx, b["y"] - dy) / R for b in bits]
    # Fragments actually populate the inner, middle AND outer thirds.
    assert any(r < 0.40 for r in radii), min(radii)
    assert any(0.55 < r < 0.95 for r in radii)
    # Past the main orbit's declared radius (``RX_FRAC``): the old band sat
    # right on it; the new field spills well outside it too.
    assert any(r > 0.85 for r in radii), max(radii)
    assert max(radii) > F.RX_FRAC
    # Small: a pinprick, not legible text. (The painter floors the FONT, so
    # the field size is what keeps them from reading as words.)
    assert F.BINARY_SIZE_BASE <= 0.07
    assert F.BINARY_SIZE_BASE + F.BINARY_SIZE_SPAN <= 0.13
    for b in bits:
        assert b["char"] in ("0", "1")
        assert 0.0 <= b["z"] <= 1.0 and 0.0 <= b["z2"] <= 1.0


def test_particle_field_is_denser_and_smaller_than_before():
    # Owner 2026-10-19: "add more particles ... small ones it looks like shit".
    # More bodies everywhere, each one smaller, so the field reads as pinprick
    # dust rather than a handful of beads.
    from voicebridge.orb import mark_field as F
    assert F.DUST_COUNT >= 80
    assert F.SPARKLE_COUNT >= 80
    assert F.BLINK_COUNT >= 40
    assert F.DUST_SIZE_BASE <= 0.08
    assert F.SPARKLE_SIZE_BASE <= 0.07
    assert F.BLINK_SIZE_BASE <= 0.07
    # Spread: blink dots now reach well inside the orbit band as well as out.
    assert F.BLINK_R_FRAC_MIN <= 0.25
    assert F.BLINK_R_FRAC_MIN + F.BLINK_R_FRAC_SPAN >= 1.0


def test_ring_set_is_translucent_and_near_monochrome():
    # Owner 2026-10-19: the circles "need to be more transparent and they are
    # too colored they look like galaxies". Every declared ring alpha is cut,
    # and the echo/satellite layers sit far below the main orbits.
    from voicebridge.orb import mark_field as F
    assert F.RING_ALPHA_MAIN <= 0.20
    assert F.RING_ALPHA_ECHO <= 0.14
    assert F.RING_ALPHA_INNER <= 0.14
    assert F.RING_ALPHA_SAT <= 0.08
    assert F.RING_ALPHA_ECHO < F.RING_ALPHA_MAIN
    assert F.RING_ALPHA_SAT < F.RING_ALPHA_ECHO


def test_binary_state_determinism_and_count():
    from voicebridge.orb import mark_field as F
    a = F.binary_state(1.23, 32.0)
    b = F.binary_state(1.23, 32.0)
    assert a == b
    assert len(a) == F.BINARY_COUNT
    for d in a:
        assert set(d) >= {"x", "y", "depth", "size", "alpha", "lift", "char"}
        assert d["char"] in ("0", "1")


def test_inner_ring_tilt_continuity():
    from voicebridge.orb import mark_field as F
    prev = F.inner_ring_tilt(1.0)
    for k in range(1, 61):
        cur = F.inner_ring_tilt(1.0 + k / 60.0)
        assert abs(cur - prev) < 5.0  # no step discontinuity per frame
        prev = cur


# --------------------------------------------------------------------------- #
# RGB TRAILS — owner 2026-10-18: "the stars still have old aura, we need them
# to leave a RGB trail not an outer glow". The field owns the streak: a
# computable list of samples from (t, R, body) whose hue walks the colour
# wheel along the tail. Renderers only stroke them.
# --------------------------------------------------------------------------- #
def test_hue_to_rgb_is_a_deterministic_standard_wheel():
    assert F.hue_to_rgb(0.0) == pytest.approx((1.0, 0.0, 0.0))
    assert F.hue_to_rgb(1.0) == pytest.approx(F.hue_to_rgb(0.0))
    assert F.hue_to_rgb(1.0 / 3.0) == pytest.approx((0.0, 1.0, 0.0))
    assert F.hue_to_rgb(2.0 / 3.0) == pytest.approx((0.0, 0.0, 1.0))
    assert F.hue_to_rgb(0.5) == pytest.approx((0.0, 1.0, 1.0))
    # Wraps instead of clamping, stays in range, and never raises on junk.
    assert F.hue_to_rgb(-0.25) == pytest.approx(F.hue_to_rgb(0.75))
    for bad in ("x", None, float("nan"), float("inf")):
        assert all(0.0 <= c <= 1.0 for c in F.hue_to_rgb(bad))
    # Saturation/value scale the result monotonically.
    full = F.hue_to_rgb(0.08)
    assert F.hue_to_rgb(0.08, 0.0, 1.0) == pytest.approx((1.0, 1.0, 1.0))
    assert F.hue_to_rgb(0.08, 1.0, 0.5) == pytest.approx(
        tuple(c * 0.5 for c in full))


def test_rgb_trail_is_pure_deterministic_and_starts_on_the_star():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    part = F.particle_state(t, R)[3]
    a = F.rgb_trail(t, R, part)
    assert a == F.rgb_trail(t, R, F.particle_state(t, R)[3])
    assert len(a) == F.RGB_TRAIL_SAMPLES
    for s in a:
        assert set(s) >= {"x", "y", "hue", "sat", "val", "alpha", "size",
                          "depth", "dof", "k"}
        assert 0.0 <= s["hue"] < 1.0 and 0.0 <= s["sat"] <= 1.0
        assert 0.0 <= s["val"] <= 1.0 and s["alpha"] >= 0.0
    # Head sits exactly on the rendered star; the tail trails away from it.
    assert (a[0]["x"], a[0]["y"]) == pytest.approx((part["x"], part["y"]))
    assert math.dist((a[-1]["x"], a[-1]["y"]),
                     (a[0]["x"], a[0]["y"])) > 1e-6


def test_rgb_trail_is_a_duotone_pair_lerp_that_drifts_with_the_clock():
    # Owner: two colours per streak, not a rainbow. Every streak is the
    # head hue and its NEARBY partner, lerped along the tail.
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    part = F.particle_state(t, R)[0]
    samples = F.rgb_trail(t, R, part)
    n = F.RGB_TRAIL_SAMPLES
    head = (part["hue"] + t * F.RGB_TRAIL_HUE_DRIFT) % 1.0
    tail = (head + F.HUE_PAIR_OFFSET) % 1.0
    # A SMALL offset: two neighbouring tints of one colour family. A half-turn
    # partner would make the lerp sweep the whole wheel (cyan->green->yellow->
    # red) and the mark read as a rainbow (owner: "too coloured").
    assert 0.0 < F.HUE_PAIR_OFFSET <= 0.15
    for s in samples:
        # One shared pair across the whole streak: exactly two hues.
        assert s["hue2"] == pytest.approx(tail)
    for s, k in zip(samples, range(n)):
        assert s["hue"] == pytest.approx(
            (head + (k / float(n - 1)) * F.HUE_PAIR_OFFSET) % 1.0)
    steps = [(b["hue"] - a["hue"]) % 1.0 for a, b in zip(samples, samples[1:])]
    assert all(s > 0.0 for s in steps), steps
    assert all(s == pytest.approx(F.HUE_PAIR_OFFSET / (n - 1), abs=1e-9)
               for s in steps)
    # Head-to-tail spans exactly half the wheel: two colours, no spectrum.
    span = (samples[-1]["hue"] - samples[0]["hue"]) % 1.0
    assert span == pytest.approx(F.HUE_PAIR_OFFSET)
    # The pair drifts with time TOGETHER, so no two moments share a streak.
    later = F.rgb_trail(t + 1.0, R, part)
    assert later[0]["hue"] != pytest.approx(samples[0]["hue"], abs=1e-6)
    assert later[0]["hue2"] == pytest.approx(
        (samples[0]["hue2"] + F.RGB_TRAIL_HUE_DRIFT) % 1.0)
    # The short variant is a duotone too.
    mote = F.dust_state(t, R)[0]
    dust = F.dust_trail(t, R, mote)
    assert len(dust) == F.DUST_TRAIL_SAMPLES
    assert all(s["hue2"] == pytest.approx(dust[0]["hue2"]) for s in dust)
    assert ((dust[-1]["hue"] - dust[0]["hue"]) % 1.0) == pytest.approx(
        F.HUE_PAIR_OFFSET)


def test_rgb_trail_head_is_a_neutral_highlight_tail_keeps_colour():
    # Owner: restrained, professional. The leading edge of every streak is
    # pulled toward white (low saturation) and the tail recovers colour, so a
    # body reads as a specular highlight with a coloured wake rather than a
    # saturated blob.
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    part = F.particle_state(t, R)[0]
    samples = F.rgb_trail(t, R, part)
    assert F.RGB_TRAIL_WHITE_FRAC > 0.0
    assert samples[0]["sat"] < samples[-1]["sat"], samples
    assert samples[0]["sat"] <= 1.0 and samples[-1]["sat"] <= 1.0
    # The tail keeps the body's colour family, never a white wash.
    assert samples[-1]["sat"] > 0.3
    # Still deterministic and junk-safe.
    assert samples == F.rgb_trail(t, R, F.particle_state(t, R)[0])


def test_rgb_trail_tapers_fades_and_follows_the_orbit_path():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 4.2
    part = F.particle_state(t, R)[1]
    a = F.rgb_trail(t, R, part)
    assert all(b["alpha"] < p["alpha"] for p, b in zip(a, a[1:]))
    assert all(b["size"] < p["size"] for p, b in zip(a, a[1:]))
    # Samples stay on the mark's own orbit envelope (no teleporting tail).
    dx, dy = F.camera_offset(t, R)
    near, far = F.orbit_tilts(t)
    for s in a:
        lx, ly = s["x"] - dx, s["y"] - dy
        assert abs(lx) <= R * F.RX_FRAC * 1.05 + 1e-6, s
        assert abs(ly) <= R * F.RY_FRAC * 1.6 + 1e-6, s
    # Consecutive samples never jump: the tail is a stroke, not a scatter.
    for p, b in zip(a, a[1:]):
        assert math.dist((p["x"], p["y"]), (b["x"], b["y"])) < 0.35 * R


def test_rgb_trail_streaks_a_free_body_along_its_own_velocity():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 5.0
    comet = F.comet(t, R)
    assert comet["alpha"] > 0.0
    a = F.rgb_trail(t, R, comet)
    assert len(a) == F.RGB_TRAIL_SAMPLES
    # The streak runs OPPOSITE the direction of travel.
    head = (a[0]["x"], a[0]["y"])
    tail = (a[-1]["x"], a[-1]["y"])
    assert (tail[0] - head[0]) * comet["vx"] + (tail[1] - head[1]) * comet["vy"] < 0
    body = F.satellites(t, R)[0]["body"]
    b = F.rgb_trail(t, R, body)
    assert len(b) == F.RGB_TRAIL_SAMPLES
    assert (b[0]["x"], b[0]["y"]) == pytest.approx((body["x"], body["y"]))


def test_rgb_trail_survives_a_body_with_no_path_and_no_velocity():
    from voicebridge.orb import mark_field as F
    a = F.rgb_trail(0.5, 32.0, {"x": 1.0, "y": 2.0})
    assert len(a) == F.RGB_TRAIL_SAMPLES
    assert all(math.isfinite(s["x"]) and math.isfinite(s["y"]) for s in a)
    assert F.rgb_trail("bad", "bad", None)[0]["alpha"] >= 0.0


def test_dust_trail_is_the_short_cheap_variant():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    mote = F.dust_state(t, R)[0]
    a = F.dust_trail(t, R, mote)
    assert len(a) == F.DUST_TRAIL_SAMPLES
    assert F.DUST_TRAIL_SAMPLES < F.RGB_TRAIL_SAMPLES
    # Every dense element can carry one, and they all move with the field.
    for m in F.dust_state(t, R):
        s = F.dust_trail(t, R, m)
        assert s[0]["x"] == pytest.approx(m["x"])
        assert math.dist((s[0]["x"], s[0]["y"]),
                         (s[-1]["x"], s[-1]["y"])) > 0.0
    # Sparkles and binary fragments take the same variant.
    assert F.dust_trail(t, R, F.sparkles(3.3, R)[0])
    assert F.dust_trail(t, R, F.binary_state(t, R)[3])


def test_every_element_carries_a_hue_and_a_dof_value():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    layers = {
        "particle": F.particle_state(t, R),
        "dust": F.dust_state(t, R),
        "binary": F.binary_state(t, R),
        "sparkle": F.sparkles(3.3, R),
        "spark": F.nucleus_sparks(0.4, R),
    }
    for name, items in layers.items():
        assert items, name
        for d in items:
            assert set(d) >= {"x", "y", "depth", "hue", "dof"}, (name, d)
            assert 0.0 <= d["hue"] < 1.0, (name, d)
            assert 0.0 <= d["dof"] <= 1.0, (name, d)
    for shell in F.satellites(t, R):
        assert 0.0 <= shell["body"]["dof"] <= 1.0
    # dof() is the shared mapping: sharp in front, fully soft at the back.
    assert F.dof(1.0) == pytest.approx(0.0)
    assert F.dof(0.0) == pytest.approx(1.0)
    assert 0.0 < F.dof(0.5) < 1.0
    assert F.dof(0.2) > F.dof(0.8)


def test_shells_counter_rotate_at_full_turn_rates():
    # Owner 2026-10-18: "spin 360 degree ... more 360 to give 3d effect".
    # The shells are not sways: each completes whole turns about the nucleus,
    # the pair keeps crossing, and no two share a period.
    from voicebridge.orb import mark_field as F
    assert F.RING_PERIOD_NEAR > 0 and F.RING_PERIOD_FAR > 0
    near0, far0 = F.orbit_tilts(0.0)
    # One period on, each shell has turned a whole turn (the sway term is the
    # only residue, so the tolerance covers a few degrees of precession).
    assert F.orbit_tilts(F.RING_PERIOD_NEAR)[0] == pytest.approx(
        near0 + 360.0, abs=3.0)
    assert F.orbit_tilts(F.RING_PERIOD_FAR)[1] == pytest.approx(
        far0 - 360.0, abs=3.0)
    # Counter-rotation: near turns one way, far the other.
    assert F.orbit_tilts(2.0)[0] > F.orbit_tilts(0.0)[0]
    assert F.orbit_tilts(2.0)[1] < F.orbit_tilts(0.0)[1]
    shells = F.satellites(0.0, 64.0)
    assert len(shells) >= 3
    assert len({s["ring"]["rx_frac"] for s in shells}) == len(shells)
    assert len({sh[3] for sh in F.SATELLITE_SHELLS}) > 1


def test_skew_is_a_bounded_per_depth_shear():
    from voicebridge.orb import mark_field as F
    assert F.skew(1.0) == pytest.approx(-F.SKEW_GAIN_DEG)
    assert F.skew(0.0) == pytest.approx(F.SKEW_GAIN_DEG)
    assert abs(F.skew(0.5)) < 1e-9
    for d in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert abs(F.skew(d)) <= F.SKEW_GAIN_DEG + 1e-9
        assert abs(F.skew("junk")) <= F.SKEW_GAIN_DEG + 1e-9
    # Every drawn body offers one, so a renderer can shear consistently.
    for p in F.particle_state(1.3, 64.0):
        assert "skew" in p and abs(p["skew"]) <= F.SKEW_GAIN_DEG + 1e-9
    for m in F.dust_state(1.3, 64.0):
        assert "skew" in m
    for shell in F.satellites(1.3, 64.0):
        assert "skew" in shell["body"]


# --------------------------------------------------------------------------- #
# RING GEOMETRY — owner 2026-10-18: "refine circles more". The whole set of
# circles is declared once, so the two renderers stroke the same set instead
# of each approximating it.
# --------------------------------------------------------------------------- #
def test_ring_geometry_declares_every_circle_back_to_front():
    from voicebridge.orb import mark_field as F
    t, R = 2.3, 90.0
    rings = F.ring_geometry(t)
    assert rings == F.ring_geometry(t)
    assert [r["id"] for r in rings] == [r["id"] for r in rings]
    ids = [r["id"] for r in rings]
    assert len(set(ids)) == len(ids), "one entry per circle"
    for r in rings:
        assert set(r) >= {"id", "layer", "orbit", "tilt", "rx_frac",
                          "ry_frac", "alpha", "width", "echo", "seed"}
        assert r["layer"] in ("main", "echo", "inner", "satellite", "accent")
        assert 0.0 < r["rx_frac"] <= 1.0 and 0.0 < r["ry_frac"] <= 1.0
        assert 0.0 < r["alpha"] < 1.0 and r["width"] > 0.0
        assert isinstance(r["echo"], bool)
    # Both orbits, both followers, the inner ring and every satellite shell.
    near, far = F.orbit_tilts(t)
    lags = [e["tilt"] for e in F.echo_rings(t - F.ECHO_LAG_S)]
    main = {r["orbit"]: r for r in rings if r["layer"] == "main"}
    assert main["a"]["tilt"] == pytest.approx(near)
    assert main["b"]["tilt"] == pytest.approx(far)
    assert main["a"]["rx_frac"] == pytest.approx(F.RX_FRAC)
    assert main["a"]["ry_frac"] == pytest.approx(F.RY_FRAC)
    assert sorted(r["tilt"] for r in rings if r["layer"] == "echo") == \
        pytest.approx(sorted(lags), abs=1e-9)
    assert sum(1 for r in rings if r["layer"] == "inner") == 1
    shells = [r for r in rings if r["layer"] == "satellite"]
    assert len(shells) == len(F.SATELLITE_SHELLS)
    for ring, shell in zip(shells, F.satellites(t, R)):
        assert ring["rx_frac"] == pytest.approx(shell["ring"]["rx_frac"])
        assert ring["ry_frac"] == pytest.approx(shell["ring"]["ry_frac"])
        assert ring["tilt"] == pytest.approx(shell["ring"]["tilt"])
    inner = next(r for r in rings if r["layer"] == "inner")
    assert inner["tilt"] == pytest.approx(F.inner_ring_tilt(t))
    assert inner["rx_frac"] == pytest.approx(F.INNER_RING_RX_FRAC)
    # Near rings come last, so a painter can walk the list top to bottom.
    assert rings[-1]["orbit"] == "a" and rings[0]["orbit"] == "b"


def test_ring_path_is_a_closed_smooth_ellipse_at_any_scale():
    from voicebridge.orb import mark_field as F
    t = 2.3
    ring = F.ring_geometry(t)[-1]
    for R in (24.0, 96.0):
        pts = F.ring_path(t, R, ring)
        assert len(pts) == F.RING_PATH_SAMPLES + 1
        assert pts[0] == pts[-1], "closed for a plain stroke"
        dx, dy = F.camera_offset(t, R)
        radii = [math.hypot(x - dx, y - dy) for x, y in pts]
        assert min(radii) == pytest.approx(R * ring["ry_frac"], abs=0.35)
        assert max(radii) == pytest.approx(R * ring["rx_frac"], abs=0.35)
        # No facets: the step grows with the axis exactly as the ellipse
        # does (an ellipse's long side is, correctly, the coarser one).
        steps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        assert max(steps) / min(steps) == pytest.approx(
            ring["rx_frac"] / ring["ry_frac"], rel=0.2)
        assert max(steps) < 0.2 * R
    # Junk in, points out.
    assert len(F.ring_path("bad", "bad", None, samples=8)) == 9


def test_ring_alpha_breathes_without_ever_vanishing_or_saturating():
    from voicebridge.orb import mark_field as F
    for ring in F.ring_geometry(0.0):
        base = ring["alpha"]
        prev = F.ring_alpha(0.0, ring)
        for k in range(1, 601):
            cur = F.ring_alpha(k / 100.0, ring)
            assert 0.0 < cur <= base * (1.0 + 1e-9)
            assert cur > base * (1.0 - F.RING_BREATH_SPAN) - 1e-9
            assert abs(cur - prev) < 0.01, (ring["id"], k)
            prev = cur
        # The two orbits breathe on different periods: the set never pulses.
        assert 0.0 < F.ring_alpha(0.0, ring) <= 1.0


def test_rgb_trails_and_shells_stay_continuous_between_adjacent_frames():
    # The same fluid contract as the drawn elements, extended to the layers
    # added for the 3D/rgb round: the streaks behind every star, the three
    # satellite bodies and the declared ring paths. Measured worst case over
    # four windows is ~0.72 px/frame at R=32 against a 1.92 px budget.
    R, dt, eps = 32.0, 1.0 / 60.0, CONTINUITY_EPS_FRAC * 32.0
    for start in (0.0, 3.0, 5.1, 9.7):
        prev = _streak_snapshot(start, R)
        for k in range(1, 61):
            cur = _streak_snapshot(start + k * dt, R)
            for layer in cur:
                for a, b in zip(prev[layer], cur[layer]):
                    assert _worst_step(a, b) <= eps, (
                        layer, start + k * dt, a, b)
            prev = cur


def _worst_step(a, b):
    """Largest point-to-point step between two (possibly nested) samples."""
    if isinstance(a[0], tuple):
        return max(_worst_step(x, y) for x, y in zip(a, b))
    return math.dist(a, b)


def _streak_snapshot(t, R):
    """Per-index point for every new layer: streaks, shell bodies, ring tips."""
    return {
        "rgb": [tuple((s["x"], s["y"]) for s in F.rgb_trail(t, R, p))
                for p in F.particle_state(t, R)],
        "dust": [tuple((s["x"], s["y"]) for s in F.dust_trail(t, R, m))
                 for m in F.dust_state(t, R)],
        "sat": [(s["body"]["x"], s["body"]["y"])
                for s in F.satellites(t, R)],
        "ring": [F.ring_path(t, R, r)[F.RING_PATH_SAMPLES // 2]
                 for r in F.ring_geometry(t)],
    }


def test_rgb_trail_alpha_never_steps_between_frames():
    # A streak that flickers between two alphas at the same sample index is a
    # visible strobe even when its geometry is perfectly continuous.
    R, dt = 64.0, 1.0 / 60.0
    for idx in range(len(F.particle_state(0.0, R))):
        prev = [(s["alpha"], s["size"]) for s in
                F.rgb_trail(0.0, R, F.particle_state(0.0, R)[idx])]
        for k in range(1, 181):
            part = F.particle_state(k * dt, R)[idx]
            cur = [(s["alpha"], s["size"]) for s in F.rgb_trail(k * dt, R, part)]
            for (pa, ps), (ca, cs) in zip(prev, cur):
                assert abs(ca - pa) < 0.02, (idx, k, pa, ca)
                assert abs(cs - ps) < 0.1, (idx, k, ps, cs)
            prev = cur


# --------------------------------------------------------------------------- #
# DUOTONE PAIRS — owner: two colours per streak. Every element dict carries
# hue2, the half-turn partner of its hue, so both render lanes stroke the
# same pair without carrying their own colour wheel.
# --------------------------------------------------------------------------- #
def test_every_element_dict_carries_its_duotone_partner():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    layers = {
        "particle": F.particle_state(t, R),
        "dust": F.dust_state(t, R),
        "binary": F.binary_state(t, R),
        "sparkle": F.sparkles(3.3, R),
        "spark": F.nucleus_sparks(0.4, R),
    }
    for name, items in layers.items():
        assert items, name
        for d in items:
            assert "hue2" in d, (name, d)
            assert d["hue2"] == pytest.approx(
                (d["hue"] + F.HUE_PAIR_OFFSET) % 1.0), (name, d)
    for shell in F.satellites(t, R):
        body = shell["body"]
        assert body["hue2"] == pytest.approx(
            (body["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
    comet = F.comet(t, R)
    if comet.get("alpha", 0.0) > 0.0:
        assert comet["hue2"] == pytest.approx(
            (comet["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
    # The partner is a NEARBY hue (a small offset), and wraps like the wheel.
    assert F._hue_pair(0.2) == pytest.approx((0.2 + F.HUE_PAIR_OFFSET) % 1.0)
    assert F._hue_pair(0.97) == pytest.approx(
        (0.97 + F.HUE_PAIR_OFFSET) % 1.0)


# --------------------------------------------------------------------------- #
# BLINK DOTS — tiny dots that appear out of nowhere: fixed positions, only
# the alpha envelope moves (0 -> peak -> 0), so they materialise and vanish.
# --------------------------------------------------------------------------- #
def test_blink_dots_materialise_in_place_and_vanish():
    from voicebridge.orb import mark_field as F
    R = 64.0
    dots = F.blink_dots(1.0, R)
    assert len(dots) == F.BLINK_COUNT
    for d in dots:
        assert set(d) >= {"x", "y", "depth", "size", "alpha", "lift",
                          "hue", "hue2"}
        assert 0.0 <= d["alpha"] <= F.BLINK_ALPHA_PEAK + 1e-9
        assert d["size"] > 0.0
        assert d["hue2"] == pytest.approx(
            (d["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
    # Deterministic: same clock, identical dots.
    assert F.blink_dots(1.0, R) == dots
    # No travel: each dot keeps its fixed orbit angle — depth is constant
    # across clocks and the position re-derives from that angle alone (the
    # dot rides the turning plane and the camera sway, but never advances).
    for t in (0.0, 2.5, 7.0):
        now = F.blink_dots(t, R)
        near, far = F.orbit_tilts(t)
        dx, dy = F.camera_offset(t, R)
        for i, d in enumerate(now):
            ang = 360.0 * F.hash01(i, 31.9)
            assert d["depth"] == pytest.approx(dots[i]["depth"])
            tilt = near if i % 2 else far
            pull = 1.0 - F.DEPTH_PERSPECTIVE * (1.0 - d["depth"])
            grow = (F.BLINK_R_FRAC_MIN
                    + F.BLINK_R_FRAC_SPAN * F.hash01(i, 37.1))
            ex, ey = F.orbit_point(0.0, 0.0, R * F.RX_FRAC * pull * grow,
                                   R * F.RY_FRAC * pull * grow, tilt, ang)
            assert (d["x"], d["y"]) == pytest.approx((ex + dx, ey + dy))
    # The envelope visits both ends: dots fade in from nothing and vanish.
    alphas = [[d["alpha"] for d in F.blink_dots(k * 0.25, R)]
              for k in range(80)]
    per_dot_max = [max(col) for col in zip(*alphas)]
    per_dot_min = [min(col) for col in zip(*alphas)]
    for d, mx in zip(F.blink_dots(0.0, R), per_dot_max):
        ceiling = F.BLINK_ALPHA_PEAK * (0.35 + 0.65 * d["depth"])
        assert mx > ceiling * 0.5
    assert all(mn < 0.05 for mn in per_dot_min)


# --------------------------------------------------------------------------- #
# WHOLE-MARK TURNS — combined X+Y spin, orb in the middle, circles outside.
# The field publishes the angles; renderers apply Y first, then X.
# --------------------------------------------------------------------------- #
def test_spin_angles_turn_full_circles_on_non_harmonic_periods():
    from voicebridge.orb import mark_field as F
    assert F.X_SPIN_PERIOD_S == pytest.approx(27.0)
    assert F.Y_SPIN_PERIOD_S == pytest.approx(20.5)
    assert F.spin_angles(0.0) == (pytest.approx(0.0), pytest.approx(0.0))
    ty, px = F.spin_angles(F.Y_SPIN_PERIOD_S)
    assert ty == pytest.approx(2.0 * math.pi)
    assert 0.0 < px < 2.0 * math.pi  # Y's turn is not X's turn
    ty2, px2 = F.spin_angles(F.X_SPIN_PERIOD_S)
    assert px2 == pytest.approx(2.0 * math.pi)
    assert 0.0 < (ty2 % (2.0 * math.pi)) < 2.0 * math.pi
    # Linear ramps, deterministic, finite on junk.
    assert F.spin_angles(3.0) == F.spin_angles(3.0)
    assert all(math.isfinite(a) for a in F.spin_angles("bad"))


def test_circles_stay_outside_with_the_orb_in_the_middle():
    # Locks the composition: every ring's radius is an order of magnitude
    # beyond the nucleus body, so the orb always sits in the middle with
    # the circles outside it — no drift can pull a ring onto the nucleus.
    from voicebridge.orb import mark_field as F
    body = F.NUCLEUS_FRAC * 1.25
    for t in (0.0, 1.7, 5.0, 11.0):
        rings = F.ring_geometry(t)
        assert rings, t
        for ring in rings:
            assert ring["rx_frac"] > body, (ring["id"], t)
            assert ring["ry_frac"] > body, (ring["id"], t)


# --------------------------------------------------------------------------- #
# VOLUME AXES (z/z2) — additive depth the renderers can project, so the mark
# reads as a cloud rather than one flat plane. ``depth`` is unchanged.
# --------------------------------------------------------------------------- #
def test_depth_axes_are_two_independent_deterministic_volume_axes():
    from voicebridge.orb import mark_field as F
    for i in range(32):
        z, z2 = F.depth_axes(i)
        assert 0.0 <= z <= 1.0 and 0.0 <= z2 <= 1.0
        assert F.depth_axes(i) == (z, z2)
        # Two independent axes: they never collapse onto one value.
        assert z != z2
    # Distinct salts -> independent streams.
    assert F.depth_axes(3) != F.depth_axes(3, 5.0)
    # Junk-safe, like every field helper.
    for bad in ("x", None, float("nan"), float("inf")):
        assert all(0.0 <= v <= 1.0 for v in F.depth_axes(bad))


def test_every_element_dict_carries_volume_axes():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    layers = {
        "particle": F.particle_state(t, R),
        "dust": F.dust_state(t, R),
        "binary": F.binary_state(t, R),
        "sparkle": F.sparkles(3.3, R),
        "spark": F.nucleus_sparks(0.4, R),
        "blink": F.blink_dots(t, R),
        "flare": F.flare_dots(t, R),
        "core": F.core_shells(t, R),
        "filament": F.filament_state(t, R),
    }
    for name, items in layers.items():
        assert items, name
        for d in items:
            assert "z" in d and "z2" in d, (name, d)
            assert 0.0 <= d["z"] <= 1.0 and 0.0 <= d["z2"] <= 1.0, (name, d)
    # Bodies that live inside nesting also carry them.
    for shell in F.satellites(t, R):
        body = shell["body"]
        assert 0.0 <= body["z"] <= 1.0 and 0.0 <= body["z2"] <= 1.0
        for s in shell["trail"]:
            assert 0.0 <= s["z"] <= 1.0 and 0.0 <= s["z2"] <= 1.0
    comet = F.comet(t, R)
    if comet.get("alpha", 0.0) > 0.0:
        assert 0.0 <= comet["z"] <= 1.0 and 0.0 <= comet["z2"] <= 1.0
        for s in comet["trail"]:
            assert 0.0 <= s["z"] <= 1.0 and 0.0 <= s["z2"] <= 1.0
    # Every trail sample, too.
    for p in F.particle_state(t, R):
        for s in F.rgb_trail(t, R, p) + F.trails(t, R, p):
            assert "z" in s and "z2" in s
    for m in F.dust_state(t, R):
        for s in F.dust_trail(t, R, m):
            assert "z" in s and "z2" in s


# --------------------------------------------------------------------------- #
# CORE SHELLS — the nucleus's interior structure: nested bodies at different
# radii and tilts, each turning at its own period.
# --------------------------------------------------------------------------- #
def test_core_shells_are_nested_deterministic_interior_depth():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 2.3
    shells = F.core_shells(t, R)
    assert len(shells) == F.CORE_SHELL_COUNT
    assert shells == F.core_shells(t, R)
    assert len({s["index"] for s in shells}) == len(shells)
    nucleus_r = R * F.NUCLEUS_FRAC
    for s in shells:
        assert set(s) >= {"x", "y", "depth", "z", "z2", "size", "alpha",
                          "hue", "hue2", "tilt", "spin_deg"}
        assert 0.0 <= s["depth"] <= 1.0
        assert 0.0 < s["r_frac"] < F.NUCLEUS_FRAC
        assert s["size"] > 0.0
        assert math.hypot(s["x"], s["y"]) < nucleus_r * 1.6
        assert s["hue2"] == pytest.approx((s["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
        assert 0.0 <= s["spin_deg"] < 360.0
    # Nested at distinct radii and plane tilts: real interior structure.
    assert len({round(s["r_frac"], 6) for s in shells}) == len(shells)
    assert len({s["tilt"] for s in shells}) == len(shells)
    # Continuous: a shell never jumps between adjacent frames.
    prev = {s["index"]: (s["x"], s["y"]) for s in shells}
    for k in range(1, 90):
        cur = {s["index"]: (s["x"], s["y"])
               for s in F.core_shells(t + k / 60.0, R)}
        for idx, (x, y) in cur.items():
            px, py = prev[idx]
            assert math.hypot(x - px, y - py) < 2.0, (idx, k)
        prev = cur


# --------------------------------------------------------------------------- #
# FLARE DOTS — a second, rarer, larger blink family.
# --------------------------------------------------------------------------- #
def test_flare_dots_are_a_second_slower_larger_blink_family():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.0
    flares = F.flare_dots(t, R)
    assert len(flares) == F.FLARE_COUNT
    assert flares == F.flare_dots(t, R)
    assert 0 < F.FLARE_COUNT < F.BLINK_COUNT, "rare: fewer than the pinpricks"
    assert F.FLARE_SIZE_BASE > F.BLINK_SIZE_BASE, "larger than the pinpricks"
    for f in flares:
        assert set(f) >= {"x", "y", "depth", "size", "alpha", "lift",
                          "hue", "hue2", "z", "z2"}
        assert f["size"] > 0.0
        assert 0.0 <= f["alpha"] <= F.FLARE_ALPHA_PEAK + 1e-9
        assert f["hue2"] == pytest.approx((f["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
    # The envelope visits both ends: births and deaths are fades, not pops.
    alphas = [[f["alpha"] for f in F.flare_dots(k * 0.3, R)]
              for k in range(80)]
    per_max = [max(col) for col in zip(*alphas)]
    per_min = [min(col) for col in zip(*alphas)]
    for f, mx in zip(flares, per_max):
        ceiling = F.FLARE_ALPHA_PEAK * (0.30 + 0.70 * f["depth"])
        assert mx > ceiling * 0.5
    assert all(mn < 0.05 for mn in per_min)


# --------------------------------------------------------------------------- #
# ENERGY FILAMENT — a travelling standing wave around a ring.
# --------------------------------------------------------------------------- #
def test_filament_is_a_deterministic_travelling_wave():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 2.0
    fil = F.filament_state(t, R)
    assert len(fil) == F.FILAMENT_SAMPLES
    assert fil == F.filament_state(t, R)
    for d in fil:
        assert set(d) >= {"x", "y", "depth", "z", "z2", "size", "alpha",
                          "hue", "hue2", "lift"}
        assert 0.0 < d["alpha"] <= 1.0
        assert d["size"] > 0.0
        assert d["hue2"] == pytest.approx((d["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
    # The wave TRAVELS: the alpha profile moves with the clock.
    a0 = [d["alpha"] for d in F.filament_state(0.0, R)]
    a1 = [d["alpha"] for d in F.filament_state(0.4, R)]
    assert a0 != a1
    assert max(a0) - min(a0) > 0.05
    # More than one crest around the ring: it is a wave, not a fading circle.
    n = len(a0)
    crests = sum(1 for i in range(n)
                 if a0[i] >= a0[(i - 1) % n] and a0[i] > a0[(i + 1) % n])
    assert crests >= 2
    # Positions ride ONE tilted ring of the declared radii: the long axis is
    # exactly the declared rx, the short axis the declared ry (minus the
    # far-side perspective pull).
    dx, dy = F.camera_offset(t, R)
    rad = [math.hypot(d["x"] - dx, d["y"] - dy) for d in fil]
    assert max(rad) == pytest.approx(R * F.FILAMENT_RX_FRAC, abs=1.0)
    assert min(rad) < max(rad)
    assert min(rad) >= (R * F.FILAMENT_RX_FRAC * (F.RY_FRAC / F.RX_FRAC)
                        * 0.85)


# --------------------------------------------------------------------------- #
# RING PRECESSION + RADIUS BREATH — fluid circles on non-harmonic periods.
# --------------------------------------------------------------------------- #
def _harmonically_locked(a, b, rel=0.02, max_den=3):
    r = a / b
    return any(abs(r - p / q) <= rel * (p / q)
               for q in range(1, max_den + 1) for p in range(1, max_den + 1))


def test_ring_set_moves_as_one_rigid_body():
    # Owner 2026-10-18: "shouldn't they all be on same axis, sometime one
    # becomes smaller or bigger than the other". The rings must stay ONE
    # concentric set: at any instant every ring shares the SAME radius scale
    # and the SAME tilt offset, so each circle keeps its declared size and
    # plane RELATIVE to the others. (The old per-ring periods made one ring
    # drift to +1.7% while another sat at -1.7%.)
    from voicebridge.orb import mark_field as F
    for t in (0.0, 1.3, 5.0, 7.7, 13.7, 31.0):
        rings = F.ring_geometry(t)
        scales = {round(F.ring_radius_scale(t, r), 12) for r in rings}
        spins = {round(F.ring_tilt_spin(t, r), 12) for r in rings}
        assert len(scales) == 1, (t, scales)
        assert len(spins) == 1, (t, spins)
        # The rigid offset reaches the geometry entries too, so a renderer that
        # reads ``tilt_spin_deg`` straight off an entry moves with the set.
        assert {round(r["tilt_spin_deg"], 12) for r in rings} == {*spins}, t
        assert {r["precess_period_s"] for r in rings} == {F.RING_MOTION_PERIOD_S}
    # Bounded, and always positive (a ring never inverts or vanishes).
    for t in (0.0, 2.0, 9.0, 20.0):
        ring = F.ring_geometry(t)[0]
        assert abs(F.ring_tilt_spin(t, ring)) <= F.RING_PRECESS_AMP_DEG + 1e-9
        scale = F.ring_radius_scale(t, ring)
        assert 1.0 - F.RING_RADIUS_BREATH_SPAN <= scale <= \
            1.0 + F.RING_RADIUS_BREATH_SPAN
        assert scale > 0.9
        assert ring["precess_period_s"] == pytest.approx(
            F.ring_precess_period(ring))

    # Proportionality is the rigidity property that must never regress: the
    # RATIO of any two rings' effective radii is identical at every clock, so
    # the set scales as ONE body and no circle can drift out of proportion. The
    # per-ring ``shimmer`` is deliberately sub-percent and does NOT enter here.
    def _ratios(t):
        rings = F.ring_geometry(t)
        eff = [r["rx_frac"] * F.ring_radius_scale(t, r) for r in rings]
        return [e / eff[0] for e in eff]

    base_ratios = _ratios(0.0)
    for t in (3.3, 12.1, 40.0):
        assert _ratios(t) == pytest.approx(base_ratios), t

    # Continuous: the shared motion never jumps between adjacent frames.
    prev = F.ring_tilt_spin(0.0)
    for k in range(1, 241):
        val = F.ring_tilt_spin(k / 60.0)
        assert abs(val - prev) < 1.0
        prev = val

    # New contract (owner 2026-10-19 "refine circles animation more"): the
    # rigid set is no longer one bare sine but a SUM of three weighted sines on
    # a non-harmonic period FAMILY — fluid, but still ONE shared offset. The
    # family must be non-harmonic against itself, the ring turns (9.5/13.5) and
    # the whole-mark spins (20.5/27), or the set could pulse in lock-step with a
    # spin. Rigidity (above) is unchanged and is the property under test.
    periods = list(F.RING_MOTION_PERIODS_S)
    assert periods == [F.RING_MOTION_PERIOD_S, F.RING_MOTION_PERIOD_2_S,
                       F.RING_MOTION_PERIOD_3_S]
    assert len(set(periods)) == len(periods)
    assert len(periods) == len(F.RING_PRECESS_WEIGHTS)
    fixed = ([F.RING_PERIOD_NEAR, F.RING_PERIOD_FAR, F.Y_SPIN_PERIOD_S,
              F.X_SPIN_PERIOD_S, F.INNER_RING_PERIOD_S]
             + [sh[2] for sh in F.SATELLITE_SHELLS])
    for i, a in enumerate(periods):
        for b in periods[i + 1:]:
            assert not _harmonically_locked(a, b), (a, b)
        for b in fixed:
            assert not _harmonically_locked(a, b), (a, b)


# --------------------------------------------------------------------------- #
# RING PARTICLES + CYLINDER — owner 2026-10-18: "check particles cos they spin
# only around one of the circles, add particles like in a cylinder over middle
# orb and fix particles to be everywhere". The field must populate EVERY
# declared ring, and wrap a vertical column of particles around the nucleus.
# --------------------------------------------------------------------------- #
def _legs(t, R, ring):
    """The ring's live (rx, ry, tilt) exactly as the field places bodies."""
    from voicebridge.orb import mark_field as F
    tilt = ring["tilt"] + ring["tilt_spin_deg"]
    rad = F.ring_radius_scale(t, ring)
    return (R * ring["rx_frac"] * rad, R * ring["ry_frac"] * rad, tilt)


def _ellipse_ratio(m, t, R, ring, rx, ry, tilt, scale=1.0):
    """How far off a body's ring's ellipse it sits (1.0 == exactly on it)."""
    from voicebridge.orb import mark_field as F
    dx, dy = F.camera_offset(t, R)
    sx, sy = m["x"] - dx, m["y"] - dy
    tr = math.radians(tilt)
    u = sx * math.cos(tr) + sy * math.sin(tr)
    v = -sx * math.sin(tr) + sy * math.cos(tr)
    pull = 1.0 - F.DEPTH_PERSPECTIVE * (1.0 - m["depth"])
    return math.hypot(u / (rx * scale * pull), v / (ry * scale * pull))


def test_ring_particles_populate_every_declared_ring():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 2.3
    rings = F.ring_geometry(t)
    ids = [r["id"] for r in rings]
    parts = F.ring_particles(t, R)
    assert parts == F.ring_particles(t, R)

    covered = {p["ring_id"] for p in parts}
    assert covered == set(ids), (covered, ids)
    per_ring = {}
    for p in parts:
        per_ring[p["ring_id"]] = per_ring.get(p["ring_id"], 0) + 1
    for r in rings:
        want = F.RING_PARTICLE_COUNTS.get(r["id"], F.RING_PARTICLE_DEFAULT)
        assert per_ring[r["id"]] == want
        assert per_ring[r["id"]] >= 2, "at least two bodies per ring"

    for p in parts:
        assert set(p) >= {"index", "x", "y", "depth", "z", "z2", "size",
                          "alpha", "lift", "hue", "hue2", "dof", "skew",
                          "orbit", "ring_id"}
        assert 0.0 <= p["depth"] <= 1.0
        assert 0.0 <= p["hue"] < 1.0
        assert p["hue2"] == pytest.approx((p["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
    assert len({p["index"] for p in parts}) == len(parts)

    # Each body rides its OWN ring's real animated path, not a stale circle.
    by_id = {r["id"]: r for r in rings}
    for p in parts:
        ring = by_id[p["ring_id"]]
        rx, ry, tilt = _legs(t, R, ring)
        assert _ellipse_ratio(p, t, R, ring, rx, ry, tilt) == \
            pytest.approx(1.0, abs=1e-9), p["ring_id"]

    # Motion: the bodies travel (different clock -> different positions).
    later = F.ring_particles(t + 0.5, R)
    assert [(p["x"], p["y"]) for p in parts] != \
        [(p["x"], p["y"]) for p in later]

    # Continuous: no body jumps between adjacent frames, on every ring.
    prev = {(p["ring_id"], p["index"]): (p["x"], p["y"]) for p in parts}
    for k in range(1, 90):
        cur = {(p["ring_id"], p["index"]): (p["x"], p["y"])
               for p in F.ring_particles(t + k / 60.0, R)}
        for key, (x, y) in cur.items():
            px, py = prev[key]
            assert math.hypot(x - px, y - py) < 3.0, (key, k)
        prev = cur


def test_dust_state_populates_every_declared_ring():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 4.4
    rings = F.ring_geometry(t)
    dust = F.dust_state(t, R)
    assert len(dust) == F.DUST_COUNT
    seen = set()
    for i, m in enumerate(dust):
        ring = rings[i % len(rings)]
        seen.add(ring["id"])
        rx, ry, tilt = _legs(t, R, ring)
        grow = F.DUST_R_FRAC_MIN + F.DUST_R_FRAC_SPAN * F.hash01(i, 5.9)
        assert _ellipse_ratio(m, t, R, ring, rx, ry, tilt, scale=grow) == \
            pytest.approx(1.0, abs=1e-9), ring["id"]
    assert seen == {r["id"] for r in rings}, seen


def test_cylinder_particles_are_deterministic_bounded_and_sweep():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    cyl = F.cylinder_particles(t, R)
    assert len(cyl) == F.CYLINDER_COUNT
    assert cyl == F.cylinder_particles(t, R)

    r_min = F.CYLINDER_R_FRAC_MIN * R
    r_max = (F.CYLINDER_R_FRAC_MIN + F.CYLINDER_R_FRAC_SPAN) * R
    h_max = F.CYLINDER_H_FRAC * R
    assert r_min < r_max and h_max > 0.0
    for p in cyl:
        assert set(p) >= {"index", "x", "y", "depth", "z", "z2", "size",
                          "alpha", "lift", "hue", "hue2", "dof", "skew",
                          "r", "h"}
        assert r_min <= p["r"] <= r_max + 1e-9
        assert abs(p["h"]) <= h_max + 1e-9
        assert 0.0 <= p["depth"] <= 1.0
        assert p["hue2"] == pytest.approx((p["hue"] + F.HUE_PAIR_OFFSET) % 1.0)

    # A full 360 sweep of the vertical axis: every particle's recovered orbital
    # angle covers all four quadrants over one period.
    n = 48
    for i in range(len(cyl)):
        angs = []
        for k in range(n):
            tk = F.CYLINDER_PERIOD_S * k / n
            dx, dy = F.camera_offset(tk, R)
            p = F.cylinder_particles(tk, R)[i]
            cos_a = (p["x"] - dx) / p["r"]
            sin_a = 2.0 * p["depth"] - 1.0
            angs.append(math.degrees(math.atan2(sin_a, cos_a)))
        unwrapped = _unwrap_deg(angs)
        assert max(unwrapped) - min(unwrapped) > 300.0, (i, unwrapped)

    # Continuous: no particle jumps between adjacent frames.
    prev = [(p["x"], p["y"]) for p in cyl]
    for k in range(1, 120):
        cur = [(p["x"], p["y"]) for p in F.cylinder_particles(t + k / 60.0, R)]
        for (x, y), (px, py) in zip(cur, prev):
            assert math.hypot(x - px, y - py) < 3.0, k
        prev = cur


def test_orbiter_particles_circle_the_middle_in_3d():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 2.1
    orb = F.orbiter_particles(t, R)
    assert len(orb) == F.ORBITER_COUNT
    assert orb == F.orbiter_particles(t, R)  # deterministic

    r_min = F.ORBITER_R_FRAC_MIN * R
    r_max = (F.ORBITER_R_FRAC_MIN + F.ORBITER_R_FRAC_SPAN) * R
    for p in orb:
        assert set(p) >= {"index", "x", "y", "depth", "z", "z2", "size",
                          "alpha", "lift", "hue", "hue2", "dof", "skew",
                          "r", "inc"}
        assert r_min <= p["r"] <= r_max + 1e-9
        assert 0.0 <= p["depth"] <= 1.0
        assert p["hue2"] == pytest.approx((p["hue"] + F.HUE_PAIR_OFFSET) % 1.0)

    # A real orbit AROUND the middle: over one full revolution each body's
    # depth visits both sides of the core (in front and behind), which is what
    # distinguishes circling the nucleus from sliding on a ring. Each body has
    # its own period, so sample across the LONGEST one to cover them all.
    span = F.ORBITER_PERIOD_BASE + F.ORBITER_PERIOD_SPAN
    for i in range(len(orb)):
        dep = [F.orbiter_particles(span * k / 60.0, R)[i]["depth"]
               for k in range(60)]
        assert min(dep) < 0.35 and max(dep) > 0.65, (i, min(dep), max(dep))

    # Depth spread across the whole set: the shell fills the volume, not a plane.
    depths = [p["depth"] for p in orb]
    assert min(depths) < 0.2 and max(depths) > 0.8

    # Continuous: no orbiter jumps between adjacent frames.
    prev = [(p["x"], p["y"]) for p in orb]
    for k in range(1, 120):
        cur = [(p["x"], p["y"]) for p in F.orbiter_particles(t + k / 60.0, R)]
        for (x, y), (px, py) in zip(cur, prev):
            assert math.hypot(x - px, y - py) < 3.0, k
        prev = cur


def _unwrap_deg(degs):
    """Unwrap a sequence of degrees so a 360 sweep is a monotone ramp."""
    out = [degs[0]]
    for d in degs[1:]:
        prev = out[-1]
        w = d
        while w - prev > 180.0:
            w -= 360.0
        while w - prev < -180.0:
            w += 360.0
        out.append(w)
    return out


def test_ring_and_cylinder_layers_are_exported():
    from voicebridge.orb import mark_field as F
    for name in ("ring_particles", "RING_PARTICLE_COUNTS",
                 "RING_PARTICLE_DEFAULT", "RING_PARTICLE_PERIOD_BASE",
                 "RING_PARTICLE_PERIOD_SPAN", "RING_PARTICLE_SIZE",
                 "RING_PARTICLE_ALPHA", "RING_PARTICLE_LIFT_BASE",
                 "RING_PARTICLE_LIFT_GAIN",
                 "cylinder_particles", "CYLINDER_COUNT",
                 "CYLINDER_R_FRAC_MIN", "CYLINDER_R_FRAC_SPAN",
                 "CYLINDER_H_FRAC", "CYLINDER_PERIOD_S",
                 "CYLINDER_DRIFT_PERIOD_S", "CYLINDER_DRIFT_AMP",
                 "CYLINDER_H_BASE", "CYLINDER_SIZE_BASE",
                 "CYLINDER_ALPHA_BASE", "CYLINDER_LIFT_BASE",
                 "CYLINDER_LIFT_GAIN"):
        assert name in F.__all__, name
        assert hasattr(F, name), name


# --------------------------------------------------------------------------- #
# TIGHTENED PALETTE — owner: "its still too colored". Every body hue now comes
# from a small fixed set instead of a wheel spray, so the whole mark carries a
# few colours; the duotone partner semantics are untouched.
# --------------------------------------------------------------------------- #
def test_palette_is_disciplined_to_a_few_hues():
    from voicebridge.orb import mark_field as F
    assert 2 <= len(F.PALETTE_HUES) <= 3
    assert len(set(F.PALETTE_HUES)) == len(F.PALETTE_HUES)
    # Owner follow-up: "more professional". The two hues must be ADJACENT
    # (one narrow colour family), not spread across the wheel, and the streak
    # saturation must be restrained rather than neon.
    spread = max(F.PALETTE_HUES) - min(F.PALETTE_HUES)
    assert spread <= 0.12, spread
    assert F.RGB_TRAIL_SAT_BASE <= 0.75
    assert 0.0 < F.RGB_TRAIL_WHITE_FRAC <= 1.0
    # Across every layer's salt and hundreds of indices, the set of body hues
    # is exactly the palette — never a continuum, never more than 3.
    hues = set()
    for i in range(600):
        for salt in (31.7, 53.9, 67.1, 71.5, 91.7, 107.3, 227.1, 529.7):
            hues.add(F._hue_for(i, salt))
    assert hues <= set(F.PALETTE_HUES)
    assert len(hues) <= len(F.PALETTE_HUES)
    # Deterministic and junk-safe, like every field helper.
    assert F.palette_hue(7, 1.0) == F.palette_hue(7, 1.0)
    for bad in ("x", None, float("nan"), float("inf")):
        assert F.palette_hue(bad) in set(F.PALETTE_HUES)
    # Duotone streaks stay duotone: the partner is still +0.5 of a turn.
    for h in hues:
        assert F._hue_pair(h) == pytest.approx((h + F.HUE_PAIR_OFFSET) % 1.0)


# --------------------------------------------------------------------------- #
# VOLUME PARTICLES — owner: "replace the checkmark / v lines ... with real
# particles that are like 3d and have animations". The retired math-glyph TEXT
# layer became this: bodies on their own inclined orbits, no ``char`` at all.
# --------------------------------------------------------------------------- #
def test_volume_particles_are_animated_3d_bodies_not_text():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 2.1
    a = F.volume_particles(t, R)
    assert a == F.volume_particles(t, R)  # deterministic
    assert len(a) == F.VOLUME_COUNT

    r_max = (F.VOLUME_R_FRAC_MIN + F.VOLUME_R_FRAC_SPAN) * R
    dx, dy = F.camera_offset(t, R)
    for d in a:
        assert set(d) >= {"index", "x", "y", "depth", "z", "z2", "vx", "vy",
                          "size", "alpha", "lift", "hue", "hue2", "dof",
                          "skew", "r", "inc", "pulse", "spin"}, d
        # A real particle, never a text glyph.
        assert "char" not in d, d
        assert 0.0 <= d["depth"] <= 1.0
        assert 0.0 <= d["z"] <= 1.0 and 0.0 <= d["z2"] <= 1.0
        assert 0.0 <= d["pulse"] <= 1.0
        assert 0.0 <= d["spin"] < 360.0
        assert 0.0 <= d["hue"] < 1.0
        assert d["hue2"] == pytest.approx((d["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
        # Positions bounded around the middle at the declared radius; every
        # body carries a screen velocity so a renderer can streak it.
        assert math.hypot(d["x"] - dx, d["y"] - dy) <= r_max + 1e-6, d
        assert math.isfinite(d["vx"]) and math.isfinite(d["vy"])

    # Depth drives size and alpha: the near bodies are larger and more opaque
    # than the far ones (the volume cue, not a flat scatter).
    near = max(a, key=lambda d: d["depth"])
    far = min(a, key=lambda d: d["depth"])
    assert near["size"] > far["size"]
    assert near["alpha"] > far["alpha"]

    # Each body ORBITS the middle: over a revolution its depth crosses both
    # sides of the core (in front of and behind it).
    span = F.VOLUME_PERIOD_BASE + F.VOLUME_PERIOD_SPAN
    for i in range(len(a)):
        deps = [F.volume_particles(span * k / 72.0, R)[i]["depth"]
                for k in range(72)]
        assert min(deps) < 0.35 and max(deps) > 0.65, (i, min(deps), max(deps))
    deps = [d["depth"] for d in a]
    assert min(deps) < 0.2 and max(deps) > 0.8

    # The per-body animations actually move across the clock.
    assert F.volume_particles(t + 0.37, R)[0]["pulse"] != pytest.approx(
        a[0]["pulse"])
    assert F.volume_particles(t + 0.37, R)[0]["spin"] != pytest.approx(
        a[0]["spin"])

    # Continuous: no body jumps between adjacent frames.
    prev = [(d["x"], d["y"]) for d in a]
    for k in range(1, 120):
        cur = [(d["x"], d["y"]) for d in F.volume_particles(t + k / 60.0, R)]
        for (x, y), (px, py) in zip(cur, prev):
            assert math.hypot(x - px, y - py) < 3.0, k
        prev = cur


def test_math_state_is_retired_and_never_raises():
    # The math-glyph text layer is retired: the name stays importable (so an
    # older renderer degrades cleanly) but emits nothing, and the MATH_*
    # constants are gone so no code can build a v-shaped checkmark again.
    from voicebridge.orb import mark_field as F
    assert F.math_state(1.0, 64.0) == []
    assert F.math_state("bad", None) == []
    for gone in ("MATH_SYMBOLS", "MATH_EQUATIONS", "MATH_COUNT",
                 "MATH_EQUATION_EVERY", "MATH_SIZE_BASE"):
        assert not hasattr(F, gone), gone


# --------------------------------------------------------------------------- #
# SCREEN BREAK — owner: "I like it how it tries to 'break' the screen".
# --------------------------------------------------------------------------- #
def test_screen_break_reaches_beyond_the_mark_radius():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 2.1
    a = F.screen_break(t, R)
    assert a == F.screen_break(t, R)  # deterministic
    assert len(a) == F.SCREEN_BREAK_COUNT
    assert F.SCREEN_BREAK_COUNT >= 8, "the break set stays a real set"
    # Task 6 "do more of that": the reach must cross the WIDGET edge, not just
    # the mark radius. A 2R-wide frame's edge sits ~1.14 R from the centre.
    assert F.SCREEN_BREAK_FRAC >= 1.30
    # The declared ceiling and the reachable radius cannot drift apart.
    r_max = (F.SCREEN_BREAK_R_FRAC_MIN + F.SCREEN_BREAK_R_FRAC_SPAN) * R
    assert r_max == pytest.approx(F.SCREEN_BREAK_FRAC * R)

    for p in a:
        assert set(p) >= {"index", "x", "y", "depth", "z", "z2", "vx", "vy",
                          "size", "alpha", "lift", "hue", "hue2", "dof",
                          "skew", "r", "inc"}, p
        assert p["r"] <= r_max + 1e-9
        assert 0.0 <= p["depth"] <= 1.0
        assert 0.0 <= p["z"] <= 1.0 and 0.0 <= p["z2"] <= 1.0
        assert p["hue2"] == pytest.approx((p["hue"] + F.HUE_PAIR_OFFSET) % 1.0)

    # Some body actually leaves the widget's nominal radius across a cycle,
    # and never by more than the declared ceiling.
    best = 0.0
    for k in range(600):
        for p in F.screen_break(k / 30.0, R):
            best = max(best, math.hypot(p["x"], p["y"]))
    assert best > R, f"screen_break never exits the widget (reach={best:.1f}px)"
    assert best > R * 1.2, (
        f"screen_break grazes the mark but never crosses the widget edge "
        f"(reach={best:.1f}px)")
    assert best <= F.SCREEN_BREAK_FRAC * R * 1.05
    # Continuous: no body teleports between adjacent frames.
    prev = [(p["x"], p["y"]) for p in a]
    for k in range(1, 120):
        cur = [(p["x"], p["y"]) for p in F.screen_break(t + k / 60.0, R)]
        for (x, y), (px, py) in zip(cur, prev):
            assert math.hypot(x - px, y - py) < 4.0, k
        prev = cur


def test_volume_and_screen_break_layers_are_exported():
    from voicebridge.orb import mark_field as F
    for name in ("volume_particles", "VOLUME_COUNT", "VOLUME_R_FRAC_MIN",
                 "VOLUME_R_FRAC_SPAN", "VOLUME_PERIOD_BASE",
                 "VOLUME_PERIOD_SPAN", "VOLUME_INC_MIN_DEG",
                 "VOLUME_INC_SPAN_DEG", "VOLUME_SIZE_BASE", "VOLUME_SIZE_SPAN",
                 "VOLUME_ALPHA_BASE", "VOLUME_ALPHA_GAIN", "VOLUME_LIFT_BASE",
                 "VOLUME_LIFT_GAIN", "VOLUME_PULSE_PERIOD_BASE",
                 "VOLUME_PULSE_PERIOD_SPAN", "VOLUME_SPIN_PERIOD_BASE",
                 "VOLUME_SPIN_PERIOD_SPAN", "math_state",
                 "screen_break", "SCREEN_BREAK_COUNT", "SCREEN_BREAK_FRAC",
                 "SCREEN_BREAK_R_FRAC_MIN", "SCREEN_BREAK_R_FRAC_SPAN",
                 "SCREEN_BREAK_PERIOD_BASE", "SCREEN_BREAK_PERIOD_SPAN",
                 "SCREEN_BREAK_INC_MIN_DEG", "SCREEN_BREAK_INC_SPAN_DEG",
                 "SCREEN_BREAK_SIZE_BASE", "SCREEN_BREAK_ALPHA_BASE",
                 "SCREEN_BREAK_LIFT_BASE", "SCREEN_BREAK_LIFT_GAIN",
                 "PALETTE_HUES", "palette_hue",
                 "accretion_stream", "ACCRETION_COUNT", "ACCRETION_R_OUT_FRAC",
                 "ACCRETION_R_IN_FRAC", "ACCRETION_PERIOD_BASE",
                 "ACCRETION_PERIOD_SPAN", "ACCRETION_SPIRAL_TURNS",
                 "photon_ring", "PHOTON_SAMPLES", "PHOTON_LOBES",
                 "infall_streaks", "INFALL_COUNT", "INFALL_TAIL_LEN_FRAC",
                 "ring_intensity", "ring_shimmer", "RING_INTENSITY_MIN",
                 "RING_STREAMERS_BASE",
                 "RING_MOTION_PERIOD_2_S", "RING_MOTION_PERIOD_3_S",
                 "RING_PRECESS_WEIGHTS", "RING_RADIUS_WEIGHTS"):
        assert name in F.__all__, name
        assert hasattr(F, name), name


def test_ring_detail_gives_each_circle_blackhole_texture():
    # Owner 2026-10-19: "the spirals or circles make them look like blackholes
    # add more details and animations". Every declared circle carries its own
    # deterministic intensity/shimmer/streamer detail, WITHOUT disturbing the
    # rigid proportionality of the set (shimmer is sub-percent by design).
    rings = F.ring_geometry(3.1)
    assert len(rings) >= 6
    intensities = []
    for r in rings:
        assert set(r) >= {"intensity_period_s", "shimmer_period_s",
                          "intensity", "shimmer", "streamers"}, r["id"]
        assert r["intensity_period_s"] > 0.0 and r["shimmer_period_s"] > 0.0
        assert F.RING_INTENSITY_MIN - 1e-9 <= r["intensity"] <= \
            F.RING_INTENSITY_MIN + F.RING_INTENSITY_SPAN + 1e-9
        assert 1.0 - F.RING_SHIMMER_SPAN <= r["shimmer"] <= \
            1.0 + F.RING_SHIMMER_SPAN
        assert F.RING_STREAMERS_BASE <= r["streamers"] <= \
            F.RING_STREAMERS_BASE + F.RING_STREAMERS_SPAN
        assert r["intensity"] == pytest.approx(F.ring_intensity(3.1, r))
        assert r["shimmer"] == pytest.approx(F.ring_shimmer(3.1, r))
        intensities.append(round(r["intensity"], 9))
    # Not one dead uniform outline: the rings sit at different brightnesses.
    assert len(set(intensities)) > 1
    assert F.ring_geometry(3.1) == rings  # deterministic
    # The per-ring shimmer only ever bends the shared radius scale by its span.
    for t in (0.0, 5.5, 17.3):
        rr = F.ring_geometry(t)
        shimmers = [F.ring_shimmer(t, r) for r in rr]
        assert max(shimmers) - min(shimmers) <= F.RING_SHIMMER_SPAN + 1e-9


def test_accretion_photon_and_infall_are_blackhole_structure():
    R, t = 64.0, 2.7
    # Accretion stream: matter winding in and out on a continuous spiral.
    acc = F.accretion_stream(t, R)
    assert acc == F.accretion_stream(t, R)
    assert len(acc) == F.ACCRETION_COUNT
    r_lo = R * F.ACCRETION_R_IN_FRAC * 0.85
    r_hi = R * F.ACCRETION_R_OUT_FRAC * 1.08
    for s in acc:
        assert set(s) >= {"index", "x", "y", "depth", "z", "z2", "vx", "vy",
                          "size", "alpha", "lift", "hue", "hue2", "dof",
                          "skew", "s", "r", "ring_id"}, s
        assert 0.0 <= s["depth"] <= 1.0
        assert 0.0 <= s["z"] <= 1.0 and 0.0 <= s["z2"] <= 1.0
        assert -0.05 <= s["s"] <= 1.05
        assert r_lo - 1e-6 <= s["r"] <= r_hi + 1e-6
    ss = [s["s"] for s in acc]
    assert max(ss) - min(ss) > 0.4, "the stream is not winding inward"
    prev = [(s["x"], s["y"]) for s in acc]
    for k in range(1, 90):
        cur = [(s["x"], s["y"]) for s in F.accretion_stream(t + k / 60.0, R)]
        for (x, y), (px, py) in zip(cur, prev):
            assert math.hypot(x - px, y - py) < 4.0, k
        prev = cur

    # Photon ring: a thin lensed ring with a couple of bright arcs.
    ph = F.photon_ring(t, R)
    assert ph == F.photon_ring(t, R)
    assert len(ph) == F.PHOTON_SAMPLES
    for p in ph:
        assert set(p) >= {"index", "x", "y", "depth", "z", "z2", "size",
                          "alpha", "lift", "hue", "hue2", "dof", "skew",
                          "angle"}, p
        assert 0.0 <= p["depth"] <= 1.0
    alphas = [p["alpha"] for p in ph]
    assert max(alphas) > F.PHOTON_ALPHA_BASE + 0.5 * F.PHOTON_ALPHA_PEAK
    assert min(alphas) < max(alphas) * 0.6, "photon ring is a uniform circle"

    # Infall streaks: inward velocity and a real tail.
    inf = F.infall_streaks(t, R)
    assert inf == F.infall_streaks(t, R)
    assert len(inf) == F.INFALL_COUNT
    for q in inf:
        assert set(q) >= {"index", "x", "y", "depth", "z", "z2", "vx", "vy",
                          "size", "alpha", "lift", "hue", "hue2", "dof",
                          "skew", "r", "tail_len", "s"}, q
        assert math.isfinite(q["vx"]) and math.isfinite(q["vy"])
        assert q["tail_len"] > 0.0 and 0.0 <= q["depth"] <= 1.0

    # Junk-safe: a bad clock / radius never raises; a list always comes back.
    for fn in (F.accretion_stream, F.photon_ring, F.infall_streaks):
        assert isinstance(fn("bad", None), list)


def test_depth_ladder_was_widened_for_volume():
    # Task 6: the depth ladder is deliberately stronger so the mark reads as a
    # volume, not a sheet. All of it is scalar gain the renderers multiply, so
    # the enhancement is additive.
    assert F.DEPTH_POPOUT >= 0.90
    assert F.DEPTH_PERSPECTIVE >= 0.25
    assert F.DEPTH_PARALLAX >= 0.25
    assert F.SKEW_GAIN_DEG >= 8.0
    # Near bodies are several times larger than far ones (real pop-out).
    assert F._depth_size(1.0, 1.0) / F._depth_size(0.0, 1.0) > 3.0
    # Lift rises monotonically with depth: the front of the volume is hotter.
    assert F._depth_lift(1.0) > F._depth_lift(0.5) > F._depth_lift(0.0)
    # The shear is signed and vanishes at the plane's rim.
    assert F.skew(0.0) == pytest.approx(F.SKEW_GAIN_DEG)
    assert F.skew(0.5) == pytest.approx(0.0, abs=1e-9)
    assert F.skew(1.0) == pytest.approx(-F.SKEW_GAIN_DEG)


def test_two_accent_circles_at_1_6x_the_main_opacity():
    # Owner 2026-10-19: "add 2 more circles again ... 60% more opacity than
    # from the others so they be a little more visible".
    from voicebridge.orb import mark_field as F
    accent = [r for r in F.ring_geometry(1.3) if r["layer"] == "accent"]
    assert len(accent) == 2, [r["id"] for r in F.ring_geometry(1.3)]
    assert {r["id"] for r in accent} == {"accent_a", "accent_b"}
    assert F.RING_ALPHA_ACCENT == pytest.approx(F.RING_ALPHA_MAIN * 1.6)
    for r in accent:
        assert r["alpha"] == pytest.approx(F.RING_ALPHA_MAIN * 1.6)
        assert r["rx_frac"] == pytest.approx(F.RING_ACCENT_RX_FRAC)


def test_particle_layers_move_in_both_directions():
    # Owner 2026-10-19: "different motions ... not all in same direction".
    from voicebridge.orb import mark_field as F
    for layer in (F.dust_state, F.binary_state, F.sparkles):
        bodies = layer(1.7, 32.0)
        signs = {1 if b["vx"] >= 0 else -1 for b in bodies if b.get("vx")}
        assert signs == {1, -1}, (layer.__name__, signs)


# --------------------------------------------------------------------------- #
# FLUID DETAIL — owner 2026-10-19: "refine orb complex animations more make
# more fluid, more professional, more detailed, more complex". The field gained
# a bounded micro-wobble, time-varying volume axes, a parallax haze, richer
# core/accretion/photon structure and two-frequency envelopes. These tests pin
# the properties the renderers rely on: determinism, bounds, smoothness and a
# frozen-clock ("still") contract.
# --------------------------------------------------------------------------- #
def test_micro_motion_is_bounded_deterministic_and_smooth():
    from voicebridge.orb import mark_field as F
    R = 64.0
    for idx, salt in ((0, 0.0), (3, 5.0), (17, 11.5)):
        a = F.micro_offset(1.7, idx, R, salt)
        assert a == F.micro_offset(1.7, idx, R, salt), "not deterministic"
        assert abs(a[0]) <= F.MICRO_WOBBLE_FRAC * R + 1e-9
        assert abs(a[1]) <= F.MICRO_WOBBLE_FRAC * R + 1e-9
        assert math.hypot(*a) <= math.sqrt(2.0) * F.MICRO_WOBBLE_FRAC * R + 1e-9
        # C1-ish between adjacent 1/60s frames: no positional pop.
        prev = F.micro_offset(0.0, idx, R, salt)
        for k in range(1, 240):
            cur = F.micro_offset(k / 60.0, idx, R, salt)
            assert math.hypot(cur[0] - prev[0], cur[1] - prev[1]) < 0.5
            prev = cur
    # Junk-safe: bad clock/index/radius still give finite pixels.
    for bad in ("bad", None, float("nan"), float("inf")):
        dx, dy = F.micro_offset(bad, bad, bad, bad)
        assert math.isfinite(dx) and math.isfinite(dy)


def test_volume_axes_sway_but_stay_inside_the_published_range():
    from voicebridge.orb import mark_field as F
    base = F.depth_axes(7, 3.0)
    assert base == F.depth_axes(7, 3.0)
    # The frozen hash contract is untouched; the animated variant drifts.
    assert F._volume_axes(2.2, 7, 3.0) != base
    assert F._volume_axes(2.2, 7, 3.0) == F._volume_axes(2.2, 7, 3.0)
    for k in range(120):
        z, z2 = F._volume_axes(k / 30.0, 7, 3.0)
        assert 0.0 <= z <= 1.0 and 0.0 <= z2 <= 1.0


def test_haze_state_is_a_slow_faint_deterministic_parallax_layer():
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 2.4
    haze = F.haze_state(t, R)
    assert len(haze) == F.HAZE_COUNT
    assert haze == F.haze_state(t, R), "not deterministic"
    # A frozen clock is static; a moving clock is not.
    assert haze != F.haze_state(t + 0.5, R)
    r_max = (F.HAZE_R_FRAC_MIN + F.HAZE_R_FRAC_SPAN) * R
    for h in haze:
        assert set(h) >= {"index", "x", "y", "depth", "z", "z2", "size",
                          "alpha", "lift", "hue", "hue2", "dof", "skew",
                          "r", "inc", "phase", "breath"}, h
        assert 0.0 <= h["depth"] <= 1.0
        assert 0.0 <= h["z"] <= 1.0 and 0.0 <= h["z2"] <= 1.0
        assert 0.0 <= h["breath"] <= 1.0
        assert h["r"] <= r_max + 1e-9
        assert h["size"] > 0.0
        # Faint by construction: the haze must never compete with the mark.
        assert 0.0 < h["alpha"] <= (
            F.HAZE_ALPHA_BASE + F.HAZE_ALPHA_GAIN + 1e-9)
        assert h["hue2"] == pytest.approx((h["hue"] + F.HUE_PAIR_OFFSET) % 1.0)
    # Continuous: the layer breathes, it never teleports.
    prev = [(h["x"], h["y"]) for h in haze]
    for k in range(1, 120):
        cur = [(h["x"], h["y"]) for h in F.haze_state(t + k / 60.0, R)]
        for (x, y), (px, py) in zip(cur, prev):
            assert math.hypot(x - px, y - py) < 3.0, k
        prev = cur


def test_core_shells_gained_two_more_swirling_interior_bodies():
    from voicebridge.orb import mark_field as F
    assert F.CORE_SHELL_COUNT == 6
    assert len(F.CORE_SHELL_PERIODS_S) >= F.CORE_SHELL_COUNT
    shells = F.core_shells(2.3, 64.0)
    assert len(shells) == F.CORE_SHELL_COUNT
    assert all("swirl" in s for s in shells)
    assert len({round(s["swirl"], 9) for s in shells}) > 1
    # The extra bodies stay inside the nucleus and keep distinct radii/tilts.
    nuc_r = 64.0 * F.NUCLEUS_FRAC
    assert len({round(s["r_frac"], 9) for s in shells}) == len(shells)
    assert len({s["tilt"] for s in shells}) == len(shells)
    assert all(math.hypot(s["x"], s["y"]) < nuc_r * 1.6 for s in shells)


def test_extended_motion_layers_are_pure_and_frozen_at_a_clock():
    from voicebridge.orb import mark_field as F
    for fn in (F.haze_state, F.volume_particles, F.orbiter_particles,
               F.cylinder_particles, F.screen_break):
        a = fn(2.1, 48.0)
        assert a == fn(2.1, 48.0), fn.__name__
        assert a != fn(2.7, 48.0), fn.__name__
