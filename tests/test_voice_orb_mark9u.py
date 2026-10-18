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
        # One follower is exactly one plain stroke: never fringed, because the
        # fringe is the logo's signature on the two MAIN orbits only.
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
    # head hue and its half-turn partner, lerped along the tail.
    from voicebridge.orb import mark_field as F
    R, t = 64.0, 1.7
    part = F.particle_state(t, R)[0]
    samples = F.rgb_trail(t, R, part)
    n = F.RGB_TRAIL_SAMPLES
    head = (part["hue"] + t * F.RGB_TRAIL_HUE_DRIFT) % 1.0
    tail = (head + F.HUE_PAIR_OFFSET) % 1.0
    assert F.HUE_PAIR_OFFSET == pytest.approx(0.5)
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
        assert r["layer"] in ("main", "echo", "inner", "satellite")
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
    assert F._hue_pair(0.2) == pytest.approx(0.7)
    assert F._hue_pair(0.9) == pytest.approx(0.4)  # wraps like the wheel


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
