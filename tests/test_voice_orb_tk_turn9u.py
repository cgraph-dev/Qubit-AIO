"""Tk-fallback contracts for the Qubit-mark orb's second animation round.

``voicebridge.orb.mark.render`` is the Tk sibling of the live Qt painter, and
this round added four things the Tk renderer could otherwise quietly lose:

  * NO spectral wake: owner 2026-10-19 retired the thick bent trail ribbons
    ("you didnt replace the L and V's"). Each body is its glint; the comet
    keeps only its own small bead tail,
  * a true-3D whole-mark tumble — full 360 turns about the vertical (Y, 20.5 s)
    and horizontal (X, 27.0 s) axes, Y first then X — so the mark NEVER thins
    to a line the way the old flat ``cos`` squash did,
  * blink dots: pinprick ovals that materialise out of nowhere (alpha-only
    envelopes, depth-split like everything else),
  * ONE declared set of circles (the field's ``ring_geometry``), each stroked
    once as ONE translucent tint — no red/blue fringe, no spectral spray,
  * a studio nucleus whose soft layers are concentric ovals, because Tk has no
    gradient and no alpha, finished with a hologram rim (bright edge plus a
    faint red/blue split).

Each case here is behavioural: it draws a frame through the renderer and
inspects the recorded primitives, so a refactor that drops an effect fails.
The last studio-nucleus case exists because Tk's translucency substitute has a
failure mode the Qt painter cannot have — a "translucent" primitive painted
opaque over something already on the canvas — and that bug is invisible in a
count.
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.orb import mark as M  # noqa: E402
from voicebridge.orb import mark_field as F  # noqa: E402

CX = CY = 64.0
R = 56.0
BG_HEX = "#%02x%02x%02x" % (11, 13, 18)


class FakeCanvas:
    def __init__(self) -> None:
        self.ovals = []
        self.lines = []
        self.texts = []
        self.calls = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append(((x0, y0, x1, y1), kw))
        self.calls.append(("oval", len(self.ovals) - 1))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((pts, kw))
        self.calls.append(("line", len(self.lines) - 1))
        return len(self.lines)

    def create_text(self, x, y, **kw):
        self.texts.append(((x, y), kw))
        return len(self.texts)


def render(t, R=R, cx=CX, cy=CY, reduced=False, state="idle"):
    c = FakeCanvas()
    stats = M.render(c, cx, cy, R, t, state, reduced_motion=reduced)
    return c, stats


def _rgb(hex_colour):
    h = str(hex_colour).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _new_lines(full, bare):
    """Strokes drawn in ``full`` but not in ``bare``, as ``(pts, kw)``."""
    left = Counter((pts, tuple(sorted(kw.items()))) for pts, kw in full.lines)
    left.subtract(Counter((pts, tuple(sorted(kw.items())))
                          for pts, kw in bare.lines))
    return list(left.elements())


def _new_ovals(full, bare):
    left = Counter((o, tuple(sorted(kw.items()))) for o, kw in full.ovals)
    left.subtract(Counter((o, tuple(sorted(kw.items()))) for o, kw in bare.ovals))
    return list(left.elements())


def _segments(canvas, min_len=3.0):
    """Two-point strokes longer than ``min_len`` — a wake, never a glint arm.

    A glint's arms are a hairline long (``size * GLINT_ARM_FRAC``); a streak
    sample steps several pixels. Filtering on the drawn geometry keeps these
    cases honest about what they are looking at.
    """
    out = []
    for pts, kw in canvas.lines:
        if len(pts) != 4:
            continue
        ln = math.dist(pts[:2], pts[2:4])
        if ln >= min_len:
            out.append((pts, kw))
    return out


# --------------------------------------------------------------------------- #
# the horizontal (Y-axis) 360 turn
# --------------------------------------------------------------------------- #
def test_horizontal_turn_is_one_full_360_per_20_5_seconds():
    assert M.Y_SPIN_PERIOD_S == pytest.approx(20.5)
    assert M.horizontal_spin_deg(0.0) == pytest.approx(0.0)
    assert M.horizontal_spin_deg(M.Y_SPIN_PERIOD_S) == pytest.approx(360.0)
    assert M.horizontal_spin_deg(M.Y_SPIN_PERIOD_S * 3) == pytest.approx(1080.0)
    # A linear ramp over the whole clock, never a wrapped phase, so it cannot
    # step at the turn boundary.
    step = M.horizontal_spin_deg(1000.0) - M.horizontal_spin_deg(999.99)
    assert step == pytest.approx(360.0 / M.Y_SPIN_PERIOD_S * 0.01, rel=1e-6)


def test_horizontal_turn_period_is_not_harmonic_with_the_others():
    # Owner: a second independent spin. Harmonic with the 24 s master spin or
    # the 9.5 s near-ring turn and the two would lock into one repeating pattern
    # instead of reading as two lives. The 27 s X turn joins the same rule.
    periods = {"master": M.MASTER_SPIN_PERIOD_S,
               "near ring": F.RING_PERIOD_NEAR,
               "far ring": F.RING_PERIOD_FAR,
               "inner ring": F.INNER_RING_PERIOD_S,
               "x turn": M.X_SPIN_PERIOD_S}
    for name, period in periods.items():
        assert period > 0.0, name
        ratio = M.Y_SPIN_PERIOD_S / period
        assert abs(ratio - round(ratio)) > 0.02, (
            f"the horizontal turn is harmonic with the {name} period "
            f"({ratio:.4f})")
    assert M.X_SPIN_PERIOD_S == pytest.approx(27.0)
    assert M.X_SPIN_PERIOD_S == pytest.approx(F.X_SPIN_PERIOD_S)
    assert M.Y_SPIN_PERIOD_S == pytest.approx(F.Y_SPIN_PERIOD_S)
    for name, period in {"master": M.MASTER_SPIN_PERIOD_S,
                         "near ring": F.RING_PERIOD_NEAR,
                         "y turn": M.Y_SPIN_PERIOD_S}.items():
        ratio = M.X_SPIN_PERIOD_S / period
        assert abs(ratio - round(ratio)) > 0.02, (
            f"the X turn is harmonic with the {name} period ({ratio:.4f})")
    # KNOWN FIELD-LEVEL 2:1: 27.0 s is exactly twice the 13.5 s far-ring
    # period, so the X tumble and the far shell re-align every 27 s. The
    # period is mandated (field's spin_angles, shared with the Qt lane), so
    # this lane pins the ratio instead of asserting it away — REPORTED as a
    # field defect for the field lane to resolve (retune one period).
    assert M.X_SPIN_PERIOD_S / F.RING_PERIOD_FAR == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# the true-3D Y+X tumble (replaces the old flat cos squash)
# --------------------------------------------------------------------------- #
# The old contract measured a FLAT plane foreshortened by ``cos(theta)`` —
# including an edge-on case asserting the mark thins toward a line. That is
# exactly what the owner rejected ("i dont what that thin"), so those
# assertions are gone, replaced by the never-thin contract below: the turn is
# a real rotation (Y first, then X) about the mark's own axes, and the near
# and far halves SEPARATE across x where the flat squash piled them onto one
# line.
def test_turn_reads_the_field_phase_y_first_then_x():
    # The field publishes the ONE phase both lanes share; the renderer must
    # apply Y first, then X — the reverse order is a different tumble.
    assert M.Y_SPIN_PERIOD_S == pytest.approx(F.Y_SPIN_PERIOD_S)
    assert M.X_SPIN_PERIOD_S == pytest.approx(F.X_SPIN_PERIOD_S)
    for t in (0.0, 1.7, 5.125, 11.0):
        theta, phi = F.spin_angles(t)
        ct, st, cp, sp = M.turn_basis(t)
        assert (ct, st) == pytest.approx((math.cos(theta), math.sin(theta)))
        assert (cp, sp) == pytest.approx((math.cos(phi), math.sin(phi)))


def test_turn_point_applies_y_before_x():
    # Manual Y-then-X composition of one point, against the helper — and
    # against the swapped order, which must differ (otherwise the order
    # contract proves nothing).
    t, R = 3.1, 90.0
    theta, phi = F.spin_angles(t)
    ct, st, cp, sp = math.cos(theta), math.sin(theta), math.cos(phi), math.sin(phi)
    for x, y, d in ((63.0, -21.0, 1.0), (-63.0, 21.0, 0.0), (12.5, 30.0, 0.7)):
        # The depth offset is faded by the COMBINED turn (sin²θ+sin²φ−sin²θsin²φ),
        # the Qt painter's polynomial — a Y-only fade collapses at an X edge-on.
        fade = st * st + sp * sp - st * st * sp * sp
        z = (d - 0.5) * M.Y_SPIN_Z_GAIN * R * fade
        xr = x * ct + z * st
        zr0 = -x * st + z * ct
        yr = y * cp - zr0 * sp
        zr = y * sp + zr0 * cp
        persp = 1.0 + M.Y_SPIN_PERSP * zr / R
        want = (xr * persp, yr * persp)
        got = M.turn_point(t, R, x, y, d)
        assert got[:2] == pytest.approx(want, abs=1e-9)
        # Swapped order (X first, then Y) lands elsewhere.
        zx = (d - 0.5) * M.Y_SPIN_Z_GAIN * R * sp * sp
        xx = x * cp + zx * sp
        xz0 = -x * sp + zx * cp
        yx = y * ct - xz0 * st
        xz = y * st + xz0 * ct
        px = 1.0 + M.Y_SPIN_PERSP * xz / R
        swapped = (xx * px, yx * px)
        assert got[:2] != pytest.approx(swapped, abs=1e-6)


def test_turn_is_the_identity_face_on():
    # theta = phi = 0 at a still clock: the unturned mark, bit for bit, and a
    # gain of exactly 1 — no pop on start-up, no altered still frame.
    R = 90.0
    for x, y in ((63.0, -21.0), (-63.0, 21.0), (0.0, 30.0), (-12.5, 0.0)):
        for d in (0.0, 0.5, 1.0):
            X, Y, g = M.turn_point(0.0, R, x, y, d)
            assert (X, Y, g) == pytest.approx((x, y, 1.0), abs=1e-9)


def test_turn_opens_the_mark_instead_of_flattening_it():
    # The owner's complaint, pinned at the numbers: a flat ``cos`` projection
    # degenerates to a line at a quarter turn. Here the depth axis carries the
    # quarter phases, so one declared orbit stays open.
    R = 90.0
    quarter = M.Y_SPIN_PERIOD_S / 4.0
    spec = [rg for rg in F.ring_geometry(0.0) if rg["id"] == "orbit_a"][0]
    pts = F.ring_path(0.0, R, spec)
    n = len(pts) - 1
    depths = [0.5 + 0.5 * math.cos(2.0 * math.pi * (i % n) / n)
              for i in range(len(pts))]

    def width_at(t):
        xs = [M.turn_point(t, R, x, y, d)[0] for (x, y), d in zip(pts, depths)]
        return max(xs) - min(xs)

    face = width_at(0.0)
    assert width_at(quarter) >= 0.45 * face, (width_at(quarter), face)
    assert width_at(M.Y_SPIN_PERIOD_S / 2.0) >= 0.45 * face

    # ...and the halves have really parted: two elements stacked in x
    # face-on — one at the very front, one at the very back — end up half a
    # mark radius apart instead of on one line.
    def front_back_gap(t):
        return (M.turn_point(t, R, 0.0, 0.0, 1.0)[0]
                - M.turn_point(t, R, 0.0, 0.0, 0.0)[0])
    assert front_back_gap(0.0) == pytest.approx(0.0, abs=1e-9)
    assert front_back_gap(quarter) >= 0.50 * face, front_back_gap(quarter)


def test_horizontal_turn_is_additive_to_the_master_spin():
    # Both are whole-mark transforms and neither absorbs the other: the master
    # spin is still exactly one turn per 24 s, whatever the horizontal turn is
    # doing, and the horizontal turn never waits for it.
    assert M.MASTER_SPIN_PERIOD_S == pytest.approx(24.0)
    assert M.spin_deg(M.MASTER_SPIN_PERIOD_S) == pytest.approx(360.0)
    for t in (0.0, 3.3, 7.7, 12.1, 19.9):
        assert M.horizontal_spin(t) != 1.0 or abs(t % M.Y_SPIN_PERIOD_S) < 1e-9
    # Two different phases of ONE horizontal turn reach different foreshortening
    # (cos is symmetric about a half turn, so only the warp breaks the tie).
    assert M.horizontal_spin(M.Y_SPIN_PERIOD_S * 0.2) != pytest.approx(
        M.horizontal_spin(M.Y_SPIN_PERIOD_S * 0.4), abs=1e-6)


def test_horizontal_turn_is_a_true_no_op_under_reduced_motion():
    # Reduced motion freezes the clock at 0, and both transforms read that
    # clock: no squash, no rotation, nothing left for the host to opt out of.
    assert M.horizontal_spin(0.0) == pytest.approx(1.0)
    assert M._spin_pt(CX + 30.0, CY + 12.0, 0.0, CX, CY) == pytest.approx(
        (CX + 30.0, CY + 12.0))
    a, _ = render(0.31, reduced=True)
    b, _ = render(3.77, reduced=True)
    assert a.lines == b.lines and a.ovals == b.ovals and a.texts == b.texts


def test_horizontal_turn_moves_drawn_elements_without_a_pop():
    # The fluid contract at the RENDERER level: every drawn particle moves a
    # bounded distance per frame, including through the edge-on crossing where
    # |dx/dt| is largest — a flat plane turning edge-on moves fastest exactly
    # where it looks smallest, which is where a naive implementation teleports.
    r, dt = 32.0, 1.0 / 60.0
    budget = 0.08 * r
    for start in (0.0, 4.6, 5.2, 12.0):
        prev = {i: M._spin_pt(p["x"] + 34.0, p["y"] + 34.0, start, 34.0, 34.0)
                for i, p in enumerate(F.particle_state(start, r))}
        for k in range(1, 61):
            t = start + k * dt
            cur = {i: M._spin_pt(p["x"] + 34.0, p["y"] + 34.0, t, 34.0, 34.0)
                   for i, p in enumerate(F.particle_state(t, r))}
            for i in prev:
                step = math.dist(prev[i], cur[i])
                assert step <= budget, (
                    f"particle {i} jumped {step:.3f}px at t={t:.3f} "
                    f"(> {budget:.3f}px/frame)")
            prev = cur


def _inked_bbox(canvas):
    """Inked bounding box of a recorded frame: every primitive's x/y extent."""
    xs, ys = [], []
    for (x0, y0, x1, y1), _kw in canvas.ovals:
        xs += [x0, x1]
        ys += [y0, y1]
    for pts, _kw in canvas.lines:
        xs += list(pts[0::2])
        ys += list(pts[1::2])
    for (x, y), _kw in canvas.texts:
        xs.append(x)
        ys.append(y)
    return (min(xs), max(xs), min(ys), max(ys))


def test_nucleus_keeps_its_circular_silhouette():
    # A sphere keeps its circular silhouette when it turns about its own
    # axes; squashing the core into a lens is the one transform here that
    # would read as a bug rather than as depth. The orbital plane IS turned,
    # so the contrast between the two is the contract. (The hologram rim is
    # an outline at the body's own radius: the body is the centred FILLED
    # oval at that radius.)
    t = M.Y_SPIN_PERIOD_S / 4.0
    c, _ = render(t)
    body = M.nucleus_radius(R, t)
    discs = [o for o, kw in c.ovals
             if abs((o[0] + o[2]) / 2 - CX) < 1e-6
             and abs((o[1] + o[3]) / 2 - CY) < 1e-6
             and abs(abs((o[2] - o[0]) / 2) - body) < 1e-6
             and kw.get("fill")]
    assert len(discs) == 1, "the body stays a full circle, edge-on or not"
    assert (discs[0][2] - discs[0][0]) == pytest.approx(
        discs[0][3] - discs[0][1])


def test_tumble_never_thins_to_a_line(monkeypatch):
    # The owner's complaint, measured on the durable assembly: dense-sample
    # the COMBINED Y+X turn over its FULL 553.5 s cycle (27 * 20.5 s, 0.5 s
    # steps) through the recorded-canvas bbox, and assert the width never
    # collapses toward a line the way the old flat ``cos`` squash did (which
    # reached roughly a fifth of the full width at the edge-on crossing and
    # looked like the orb had switched off). Sampling the full cycle matters:
    # the sliver phases are coincidence phases, and a short window can miss
    # them entirely.
    #
    # Ephemeral layers (dust, sparkles, sparks, blink dots, the comet) are
    # stripped from BOTH the face-on reference and the samples: they set the
    # full frame's width at face-on without participating in the slivers, so
    # leaving them in compares the sliver against a transient-inflated bar.
    # What is measured is the durable mark: rings, riders, streaks, binaries
    # and the nucleus stack.
    #
    # FLOOR 0.45 — the owner's "never thin" requirement, and now measured with
    # margin (the full-cycle scan bottoms out at ~0.60 of face-on, t=66.5 s).
    # The old 0.20 floor documented a real collapse: the projection fade was
    # Y-only (``sin²θ``), so near Y face-on the depth vanished exactly where
    # the X turn tipped the plane edge-on and the mark folded to a band. The
    # Tk projection now uses the Qt painter's COMBINED fade
    # (``sin²θ + sin²φ − sin²θ·sin²φ``), which is still exactly 0 only when
    # BOTH phases are 0 (face-on / reduced motion stay the unturned mark) but
    # stays open at every single-axis edge-on. Same polynomial, same result in
    # both lanes.
    monkeypatch.setattr(M, "dust_state", lambda *a, **k: [])
    monkeypatch.setattr(M, "sparkles", lambda *a, **k: [])
    monkeypatch.setattr(M, "nucleus_sparks", lambda *a, **k: [])
    monkeypatch.setattr(M, "blink_dots", lambda *a, **k: [])
    monkeypatch.setattr(M, "comet",
                        lambda *a, **k: {"alpha": 0.0, "trail": []})
    face, _ = render(0.0)
    fx0, fx1, _, _ = _inked_bbox(face)
    full = fx1 - fx0
    assert full > 0

    worst, worst_t = full, 0.0
    t, end = 0.0, 553.5
    while t < end:
        c, _ = render(t)
        x0, x1, _, _ = _inked_bbox(c)
        w = x1 - x0
        if w < worst:
            worst, worst_t = w, t
        t += 0.5
    assert worst >= 0.45 * full, (
        f"mark thinned to {worst:.1f}px ({worst / full:.0%} of {full:.1f}px) "
        f"at t={worst_t:.2f}s")


def test_rotated_depth_drives_size_and_brightness():
    # A body swinging toward the viewer gets BIGGER and BRIGHTER; one tucking
    # away gets smaller and dimmer — the cue that makes the turn read as a
    # rotation rather than a wobble. Pinned on the projection, then on the
    # frame: killing the depth axis must change what is drawn.
    Rq = 90.0
    X, Y, g = M.turn_point(0.0, Rq, 10.0, 5.0, 0.9)
    assert (X, Y, g) == pytest.approx((10.0, 5.0, 1.0), abs=1e-9)
    quarter = M.Y_SPIN_PERIOD_S / 4.0
    assert M.turn_point(quarter, Rq, -60.0, 0.0, 1.0)[2] > 1.0
    assert M.turn_point(quarter, Rq, 60.0, 0.0, 1.0)[2] < 1.0
    # Clamped, so no field value outside its nominal depth range can blow a
    # body up or invert it.
    for depth in (-2.0, 3.0):
        _x, _y, gg = M.turn_point(quarter, Rq, 10.0, 5.0, depth)
        assert M.Y_SPIN_GAIN_MIN <= gg <= M.Y_SPIN_GAIN_MAX
    # ...and the painter really consumes it: freezing the depth axis changes
    # the painted frame at the quarter turn.
    plain, _ = render(quarter)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(M, "Y_SPIN_Z_GAIN", 0.0)
        c, _ = render(quarter)
        assert (c.lines != plain.lines
                or c.ovals != plain.ovals), "depth axis dead"
    with pytest.MonkeyPatch.context() as m:
        m.setattr(M, "Y_SPIN_PERSP", 0.0)
        c, _ = render(quarter)
        assert (c.lines != plain.lines
                or c.ovals != plain.ovals), "perspective is unused"


# --------------------------------------------------------------------------- #
# blink dots: pinprick ovals that materialise from nowhere
# --------------------------------------------------------------------------- #
def _strip_all_but_blink(monkeypatch):
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "blink_dots", F.blink_dots)


def test_blink_dots_appear_and_vanish(monkeypatch):
    # The envelope visits both ends: dots fade in from nothing (alpha 0 omits
    # the oval) and vanish the same way. Counted against the field: every
    # visible dot is drawn, none invented.
    _strip_all_but_blink(monkeypatch)
    for t in (0.5, 1.7, 3.3, 9.0):
        c, _ = render(t)
        want = [d for d in F.blink_dots(t, R)
                if d["alpha"] > M.FIELD_ALPHA_FLOOR]
        got = len(c.ovals) - M.NUCLEUS_OVAL_COUNT - 1  # nucleus + 1 star
        assert got == len(want), (t, got, len(want))
    # ...and across the clock the layer really breathes rather than sitting
    # at a fixed count.
    counts = {len(render(k * 0.5)[0].ovals) for k in range(40)}
    assert len(counts) > 1, "blink dots never change"


def test_blink_dots_are_pinhole_ovals_depth_split(monkeypatch):
    # Text-free pinpricks, far half behind the nucleus, near half in front.
    _strip_moving_layers(monkeypatch)
    dots = [
        {"index": 0.0, "x": -20.0, "y": 5.0, "depth": 0.2, "size": 0.5,
         "alpha": 0.8, "lift": 0.5, "hue": 0.5, "hue2": 0.0},
        {"index": 1.0, "x": 18.0, "y": -6.0, "depth": 0.8, "size": 0.5,
         "alpha": 0.8, "lift": 0.5, "hue": 0.5, "hue2": 0.0},
    ]
    monkeypatch.setattr(M, "blink_dots", lambda tt, rr: list(dots))
    c, stats = render(1.7)
    assert stats["texts"] == 0, "blink dots are ovals, never text"
    body = M.nucleus_radius(R, 1.7)
    import math as _m
    a = _m.radians(_m.fmod(M.spin_deg(1.7), 360.0))

    def _screen(x, y, depth, index):
        # The renderer projects through turn_qdepth (hash-broken depth, flat
        # per element), not the raw field depth — the expectation must take
        # the same path or it asserts against a projection nobody paints.
        qd = M.turn_qdepth(depth, index, flat=True)
        px, py, _g = M.turn_point(1.7, R, x, y, qd)
        rx = px * _m.cos(a) - py * _m.sin(a)
        ry = px * _m.sin(a) + py * _m.cos(a)
        return (round(CX + rx, 6), round(CY + ry, 6))

    idx = {tuple(round(v, 6) for v in ((o[0] + o[2]) / 2,
                                       (o[1] + o[3]) / 2)): i
           for i, (o, _kw) in enumerate(c.ovals)}
    nuc_idx = next(i for i, (o, kw) in enumerate(c.ovals)
                   if kw.get("fill")
                   and abs(abs((o[2] - o[0]) / 2) - body) < 1e-6)
    far_at = _screen(-20.0, 5.0, 0.2, 0.0)
    near_at = _screen(18.0, -6.0, 0.8, 1.0)
    assert far_at in idx and near_at in idx, "both dots drawn"
    assert idx[far_at] < nuc_idx, "far dot behind the nucleus"
    assert idx[near_at] > nuc_idx, "near dot in front"


def test_reduced_motion_draws_no_blink_dots(monkeypatch):
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "blink_dots", lambda tt, rr: [
        {"index": 0.0, "x": 5.0, "y": 5.0, "depth": 0.8, "size": 1.0,
         "alpha": 0.8, "lift": 0.5, "hue": 0.5, "hue2": 0.0}])
    live, _ = render(1.7)
    frozen, _ = render(1.7, reduced=True)
    assert len(live.ovals) == len(frozen.ovals) + 1


# --------------------------------------------------------------------------- #
# duotone streaks: exactly two hues, never a hue ramp of the renderer's own
# --------------------------------------------------------------------------- #
def test_comet_beads_walk_the_duotone_pair(monkeypatch):
    # The old bead tail ran its own hue ramp (HUE_STEP per bead); now each
    # bead lerps the streak's duotone pair head -> hue2, half a turn apart.
    # Recomputed here through the documented pipeline (hue_to_rgb per sample,
    # anchor toward the state core, background-dim) at t=0, where the tumble
    # is the identity and the rotated-depth gain is exactly 1.
    from voicebridge.orb import ember as E
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "blink_dots", lambda *a, **k: [])
    hue0, hue2 = 0.2, 0.7
    trail = [{"x": 10.0 + k, "y": 5.0, "size": 1.0, "alpha": 1.0,
              "depth": 0.6} for k in range(3)]
    monkeypatch.setattr(M, "comet", lambda tt, rr: {
        "alpha": 1.0, "trail": trail, "x": 10.0, "y": 5.0, "depth": 0.6,
        "hue": hue0, "hue2": hue2, "size": 2.0, "lift": 0.8})
    c, _ = render(0.0)

    core = E.state_core("idle")
    nuc = tuple(int(core[i] + (255 - core[i]) * M.LIFT_NUCLEUS)
                for i in range(3))
    bright = max(M.MIN_MARK_BRIGHTNESS,
                 float(E.resolve_params("idle")["brightness"]))
    alpha = 1.0 * (1.0 - M.DOF_ALPHA_SOFT * F.dof(0.6)) * bright
    dim_k = max(0.0, min(1.0, alpha))

    def _expected(hue):
        r, g, b = F.hue_to_rgb(hue, M.COMET_BEAD_SAT, 1.0)
        spec = (max(0, min(255, int(r * 255.0))),
                max(0, min(255, int(g * 255.0))),
                max(0, min(255, int(b * 255.0))))
        anchored = tuple(max(0, min(255, int(col + (n - col) * 0.10)))
                         for col, n in zip(spec, nuc))
        mixed = tuple(int(col + (bg - col) * (1.0 - dim_k))
                      for col, bg in zip(anchored, E.NEAR_BLACK))
        return "#%02x%02x%02x" % mixed

    want = [_expected((hue0 + (k + 1.0) / 4.0 * 0.5) % 1.0)
            for k in range(3)]
    # Identify the three bead ovals by their radius-1.0 pinprick at y=5.
    pins = [(o, kw) for o, kw in c.ovals
            if abs((o[1] + o[3]) / 2 - (CY + 5.0)) < 1e-6
            and abs(abs((o[2] - o[0]) / 2) - 1.0) < 1e-6]
    assert len(pins) == 3, [kw.get("fill") for _o, kw in pins]
    pins.sort(key=lambda ok: (ok[0][0] + ok[0][2]) / 2)
    assert [kw["fill"] for _o, kw in pins] == want
    # ...and they really are two colours' lerp, not the old ramp: the old
    # HUE_STEP hues would have painted different fills.
    assert len(set(want)) == 3


# --------------------------------------------------------------------------- #
# no wakes: the field's trail builders are deliberately not stroked
# --------------------------------------------------------------------------- #
def _strip_moving_layers(monkeypatch):
    """Leave one particle, no wake: nothing else can pose as a trail."""
    part = dict(F.particle_state(1.7, R)[0])
    part.update({"x": 0.0, "y": 0.0, "depth": 0.5, "size": 1.0, "alpha": 0.9,
                 "lift": 0.4, "hue": 0.5})
    monkeypatch.setattr(M, "particle_state", lambda tt, rr: [part])
    monkeypatch.setattr(M, "dust_state", lambda *a, **k: [])
    monkeypatch.setattr(M, "sparkles", lambda *a, **k: [])
    monkeypatch.setattr(M, "satellites", lambda *a, **k: [])
    monkeypatch.setattr(M, "nucleus_sparks", lambda *a, **k: [])
    monkeypatch.setattr(M, "binary_state", lambda *a, **k: [])
    monkeypatch.setattr(M, "blink_dots", lambda *a, **k: [])
    monkeypatch.setattr(M, "comet",
                        lambda *a, **k: {"alpha": 0.0, "trail": []})
    # The studio-round life layers: a flare, the travelling filament and the
    # two concurrent field riders are their own layers, so an isolated-body
    # frame must suppress them too or their primitives pose as the body's.
    monkeypatch.setattr(M, "flare_dots", lambda *a, **k: [])
    monkeypatch.setattr(M, "filament_state", lambda *a, **k: [])
    monkeypatch.setattr(M, "_field_ring_particles", None)
    monkeypatch.setattr(M, "_field_cylinder_particles", None)
    monkeypatch.setattr(M, "_field_orbiter_particles", None)
    # The 3D volume-particle field, the black-hole structure layers and the
    # screen-break bodies are their own layers too, so an isolated-body frame
    # must suppress them or their bodies pose as the body's.
    monkeypatch.setattr(M, "_field_volume_particles", None)
    monkeypatch.setattr(M, "_field_accretion_stream", None)
    monkeypatch.setattr(M, "_field_photon_ring", None)
    monkeypatch.setattr(M, "_field_infall_streaks", None)
    monkeypatch.setattr(M, "_field_screen_break", None)
    # The parallax haze bed is a life layer too: its soft discs would otherwise
    # inflate an isolated-body frame's oval count.
    monkeypatch.setattr(M, "_field_haze", None)
    return part


def _thick_trail():
    """A synthetic two-sample trail thick enough to be an unmistakable wake."""
    return [
        {"x": 0.0, "y": 0.0, "hue": 0.5, "sat": 1.0, "val": 1.0,
         "alpha": 1.0, "size": 3.0, "depth": 0.5},
        {"x": 20.0, "y": 0.0, "hue": 0.5, "sat": 1.0, "val": 1.0,
         "alpha": 1.0, "size": 3.0, "depth": 0.5},
    ]


def test_no_travelling_body_draws_a_wake(monkeypatch):
    # Owner 2026-10-19: "you didnt replace the L and V's". The renderer must
    # never consult the field's trail builders: point them at a thick synthetic
    # trail and the frame is byte-identical to their absence.
    full, _ = render(1.7)
    monkeypatch.setattr(M, "rgb_trail", lambda *a, **k: _thick_trail())
    monkeypatch.setattr(M, "dust_trail", lambda *a, **k: _thick_trail())
    bare, _ = render(1.7)
    assert full.lines == bare.lines and full.ovals == bare.ovals


def test_a_body_draws_its_star_without_a_wake(monkeypatch):
    part = _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "rgb_trail", lambda *a, **k: _thick_trail())
    c, _ = render(1.7)
    assert not _segments(c), "the body left no wake"
    # The body itself is still drawn: its glint core is a single pinprick at
    # the same projection the trail would have shared.
    import math as _m2
    qd = M.turn_qdepth(0.5, part.get("index"), flat=True)
    _px, _py, _g = M.turn_point(1.7, R, 0.0, 0.0, qd)
    _ox, _oy = M.volume_offset(1.7, R, part.get("z"), part.get("z2"))
    _px, _py = _px + _ox, _py + _oy
    _a = _m2.radians(_m2.fmod(M.spin_deg(1.7), 360.0))
    star_x = CX + _px * _m2.cos(_a) - _py * _m2.sin(_a)
    star_y = CY + _px * _m2.sin(_a) + _py * _m2.cos(_a)
    at_star = [o for o, _kw in c.ovals
               if abs((o[0] + o[2]) / 2.0 - star_x) < 1e-6
               and abs((o[1] + o[3]) / 2.0 - star_y) < 1e-6
               and abs((o[2] - o[0]) / 2.0) < 1.0]
    assert len(at_star) == 1, "the star is still a single pinprick core"


def test_a_live_frame_has_no_bent_wakes(monkeypatch):
    # The only legitimate long two-point strokes in a live frame are the
    # black-hole infall tails (thin, straight, radial); suppress them and the
    # filament spray, and no thick bent segment may remain.
    monkeypatch.setattr(M, "_field_infall_streaks", None)
    monkeypatch.setattr(M, "filament_state", lambda *a, **k: [])
    c, _ = render(1.7)
    # ``_segments`` at 6 px clears a large body's glint arms (~5 px here).
    seg = _segments(c, min_len=6.0)
    assert not seg, f"{len(seg)} wake-like strokes remain"


def test_reduced_motion_draws_no_wake_at_all(monkeypatch):
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "rgb_trail", lambda *a, **k: _thick_trail())
    live, _ = render(1.7)
    frozen, _ = render(1.7, reduced=True)
    assert not _segments(live)
    assert not _segments(frozen)


def test_a_long_field_trail_is_ignored(monkeypatch):
    # Even a nine-sample field trail draws nothing now: the whole ribbon layer
    # is gone, not merely truncated.
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "rgb_trail", lambda *a, **k: [
        dict(_thick_trail()[0], x=float(k)) for k in range(9)])
    c, _ = render(1.7)
    assert not _segments(c)


def test_a_glint_carries_no_halo(monkeypatch):
    # A halo would be extra primitives around one star. One spark is exactly
    # two crossing arms plus one core oval — three primitives, whatever its
    # size — and its core never grows past a pinprick.
    t = 0.0
    assert F.nucleus_sparks(t, R)
    full, _ = render(t)
    monkeypatch.setattr(M, "nucleus_sparks", lambda *a, **k: [])
    bare, _ = render(t)
    sparks = _new_ovals(full, bare)
    assert sparks
    added_lines = _new_lines(full, bare)
    assert len(added_lines) == 2 * len(sparks), (
        "every spark is exactly a two-armed glint, never a halo around one")
    nuc_r = R * F.NUCLEUS_FRAC
    assert all(abs((o[2] - o[0]) / 2) < nuc_r for o, _kw in sparks)


# --------------------------------------------------------------------------- #
# one declared geometry
# --------------------------------------------------------------------------- #
def test_every_circle_is_stroked_from_the_declared_ring_set(monkeypatch):
    # The renderer owns no geometry of its own: with the declaration emptied it
    # draws no circles at all, and a ring that is not declared is not drawn.
    full, stats = render(2.3)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    bare, _ = render(2.3)
    at_zero, _ = render(0.0)
    circles = _new_lines(full, bare)
    # One plain stroke per declared circle: two main orbits, two followers,
    # three shells, one core ring. No fringe strokes any more (owner
    # 2026-10-19: the circles looked like galaxies).
    assert len(circles) == 2 + 2 + 3 + 1 + 2, len(circles)
    assert stats["lines"] > 0

    # ...and it is the DECLARED radius that is drawn, about the mark's centre.
    # Sampled at t=0, where both whole-mark transforms are the identity, so the
    # drawn points are the declared ones plus the body's own (unswayed) centre.
    probe = dict(F.ring_geometry(0.0)[0])
    probe.update({"id": "probe", "layer": "satellite", "orbit": "",
                  "rx_frac": 0.40, "ry_frac": 0.20, "alpha": 0.5, "tilt": 0.0,
                  # Isolate the radius: kill the shared plane precession so the
                  # drawn ellipse stays axis-aligned (otherwise max|x| is the
                  # rotated rx, and this would be a tilt test).
                  "tilt_spin_deg": 0.0})
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    zero, _ = render(0.0)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [probe])
    only, _ = render(0.0)
    added = _new_lines(only, zero)
    assert at_zero.lines == zero.lines, "the baseline really is empty"
    assert len(added) == 1
    pts = added[0][0]
    # No camera sway: the circles belong to the body, which does not sway. The
    # drawn radii carry the field's gentle shared radius breath AND the per-ring
    # shimmer detail, so the expectation reads the same additive scale the
    # renderer applies.
    rscale = F.ring_radius_scale(0.0, probe) * F.ring_shimmer(0.0, probe)
    assert max(abs(p - CX) for p in pts[0::2]) == pytest.approx(
        R * probe["rx_frac"] * rscale, abs=1e-6)
    assert max(abs(p - CY) for p in pts[1::2]) == pytest.approx(
        R * probe["ry_frac"] * rscale, abs=1e-6)


def test_every_circle_is_one_plain_translucent_stroke(monkeypatch):
    # Owner 2026-10-19: the red/blue lens fringe and the multi-hue spray made
    # the rings read as galaxies. Every declared circle is now exactly ONE
    # stroke — no follower / core ring / shell gains a second colour.
    specs = F.ring_geometry(2.3)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    bare, _ = render(2.3)
    for spec in specs:
        only = [spec]
        monkeypatch.setattr(M, "ring_geometry", lambda tt, o=only: o)
        one, _ = render(2.3)
        strokes = _new_lines(one, bare)
        assert len(strokes) == 1, (spec["id"], len(strokes))
        assert dict(strokes[0][1])["width"] >= 1
    # The translucency factor is what keeps a declared-alpha stroke faint.
    assert 0.0 < M.RING_TRANSLUCENCY < 0.75


def test_rings_have_no_chromatic_fringe(monkeypatch):
    # The retired fringe painted two extra offset strokes (dark red right, dark
    # blue left) beside every MAIN orbit, so one circle produced three distinct
    # fills. A near-monochrome circle produces exactly one tint at any alpha.
    specs = F.ring_geometry(2.3)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    bare, _ = render(2.3)
    for spec in specs:
        monkeypatch.setattr(M, "ring_geometry", lambda tt, o=spec: [o])
        one, _ = render(2.3)
        strokes = _new_lines(one, bare)
        fills = {dict(kw)["fill"] for _pts, kw in strokes if len(kw)}
        assert len(strokes) == 1, (spec["id"], len(strokes))
        assert len(fills) == 1, (spec["id"], fills)


def test_a_still_frame_drops_the_life_and_keeps_the_mark(monkeypatch):
    # Echo followers and satellite shells are LIFE: a reduced frame is the two
    # main orbits, the core ring, the glints and the resting nucleus.
    live, _ = render(2.3)
    frozen, _ = render(2.3, reduced=True)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    bare, _ = render(2.3)
    assert len(live.lines) > len(bare.lines) > len(frozen.lines)
    assert len(frozen.ovals) > 0, "the mark is still a mark when it is still"


# --------------------------------------------------------------------------- #
# the studio nucleus
# --------------------------------------------------------------------------- #
def test_nucleus_stack_is_the_declared_primitive_count(monkeypatch):
    # The stack is the same on every frame: no beat-conditional layer may
    # appear and disappear, or the primitive budget would jitter frame to frame.
    _strip_moving_layers(monkeypatch)
    for t in (0.0, 0.24, 0.475, 1.9, 6.3):
        _c, stats = render(t)
        assert stats["ovals"] == 1 + M.NUCLEUS_OVAL_COUNT, (t, stats["ovals"])


def test_a_still_frame_is_the_nine_glints_plus_the_nucleus_stack():
    frozen, stats = render(0.0, reduced=True)
    assert stats["ovals"] == len(M.PARTICLES) + M.NUCLEUS_OVAL_COUNT
    assert stats["lines"] == 2 * len(M.PARTICLES) + 5


def test_on_body_detail_never_paints_a_flat_background_disc():
    # The failure mode Tk's translucency substitute has and Qt's alpha does
    # not: a "translucent" primitive is painted OPAQUE at its background-mixed
    # colour, so a stop whose alpha rounds to zero flat-ERASES whatever is
    # already on the canvas. Inside the opaque nucleus that turns the shaded
    # sphere into a hole punched through to the background. (The hologram rim
    # is outlines at the body's radius, not background fills: the body's own
    # entry is the centred FILLED oval at that radius.)
    t = 1.9  # the primary thump: caustic, flare and swirl all at full
    c, _ = render(t)
    body = M.nucleus_radius(R, t)
    body_i = [i for i, (o, kw) in enumerate(c.ovals)
              if abs((o[0] + o[2]) / 2 - CX) < 1e-9
              and abs((o[1] + o[3]) / 2 - CY) < 1e-9
              and abs(abs((o[2] - o[0]) / 2) - body) < 1e-9
              and kw.get("fill")]
    assert len(body_i) == 1
    holes = [kw.get("fill") for o, kw in c.ovals[body_i[0] + 1:]
             if abs((o[0] + o[2]) / 2 - CX) < body
             and abs((o[1] + o[3]) / 2 - CY) < body
             and kw.get("fill") == BG_HEX]
    assert not holes, f"{len(holes)} primitives punched the body to background"


# --------------------------------------------------------------------------- #
# refine round: math glyphs, screen-break bodies
# --------------------------------------------------------------------------- #
def test_volume_particles_paint_as_glints_never_as_text(monkeypatch):
    # Owner retired the math-glyph TEXT layer ("they read as checkmarks /
    # v lines") for a real 3D particle field. The Tk renderer must draw each
    # body as a glint (two arms + a core), coloured from the field's palette,
    # and NEVER as a glyph.
    _strip_moving_layers(monkeypatch)
    bodies = [
        {"index": 0.0, "x": -20.0, "y": 0.0, "depth": 0.2, "size": 1.4,
         "alpha": 0.9, "lift": 0.5, "hue": 0.0, "spin": 30.0,
         "z": 0.2, "z2": 0.7},
        {"index": 1.0, "x": 18.0, "y": 0.0, "depth": 0.8, "size": 1.4,
         "alpha": 0.9, "lift": 0.5, "hue": 0.5, "spin": 210.0,
         "z": 0.6, "z2": 0.3},
    ]
    monkeypatch.setattr(M, "_field_volume_particles",
                        lambda tt, rr: [dict(b) for b in bodies])
    c, stats = render(1.7)
    assert stats["texts"] == 0, "volume particles must never be text"
    monkeypatch.setattr(M, "_field_volume_particles", lambda tt, rr: [])
    bare, _ = render(1.7)
    # Exactly one core oval and its two crossed arms per visible body, and no
    # text primitive anywhere.
    assert len(_new_ovals(c, bare)) == len(bodies)
    assert len(_new_lines(c, bare)) == 2 * len(bodies)


def test_accretion_photon_and_infall_layers_are_drawn(monkeypatch):
    # Owner: "the spirals or circles make them look like blackholes add more
    # details and animations". The three field layers must reach the canvas,
    # with the right primitive shape, and be gone under reduced motion like
    # every other life layer.
    _strip_moving_layers(monkeypatch)
    layer = {
        "index": 0.0, "x": 12.0, "y": 0.0, "depth": 0.8, "size": 1.2,
        "alpha": 0.9, "lift": 0.5, "hue": 0.6, "hue2": 0.65,
        "z": 0.5, "z2": 0.5, "vx": -3.0, "vy": 0.0, "tail_len": 8.0,
    }
    for attr, d_ovals, d_lines in (("_field_accretion_stream", 1, 0),
                                   ("_field_photon_ring", 1, 0),
                                   ("_field_infall_streaks", 1, 1)):
        monkeypatch.setattr(M, attr, lambda tt, rr: [])
        _base, sb = render(1.7)
        _fbase, sfb = render(1.7, reduced=True)
        monkeypatch.setattr(M, attr, lambda tt, rr, d=layer: [dict(d)])
        _live, sl = render(1.7)
        _flive, sfl = render(1.7, reduced=True)
        assert sl["ovals"] - sb["ovals"] == d_ovals, attr
        assert sl["lines"] - sb["lines"] == d_lines, attr
        # Reduced motion drops the layer entirely (compared like for like).
        assert sfl["ovals"] == sfb["ovals"], attr
        assert sfl["lines"] == sfb["lines"], attr
        monkeypatch.setattr(M, attr, None)


def test_ring_stroke_consumes_per_ring_intensity_and_shimmer(monkeypatch):
    # Task 2: the field's per-ring INTENSITY must modulate the stroke alpha and
    # its SHIMMER the drawn radius, so a circle reads as a live accretion
    # structure instead of a dead outline. Measured against an empty ring set
    # so the probe's stroke is the only line difference.
    probe = dict(F.ring_geometry(0.0)[0])
    probe.update({"id": "probe", "layer": "satellite", "orbit": "",
                  "rx_frac": 0.40, "ry_frac": 0.20, "alpha": 0.5,
                  "tilt": 0.0, "seed": 2.0})
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    empty, _ = render(0.0)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [probe])

    def probe_stroke():
        c, _ = render(0.0)
        added = _new_lines(c, empty)
        assert len(added) == 1, len(added)
        return added[0]

    monkeypatch.setattr(M, "ring_intensity", lambda tt, ring: 0.30)
    monkeypatch.setattr(M, "ring_shimmer", lambda tt, ring: 0.80)
    dim_pts, dim_kw = probe_stroke()
    monkeypatch.setattr(M, "ring_intensity", lambda tt, ring: 1.0)
    bright_pts, bright_kw = probe_stroke()
    # Intensity modulates the stroke colour (alpha is a mix toward background).
    assert dict(dim_kw)["fill"] != dict(bright_kw)["fill"]
    # Shimmer modulates the drawn radius; the shared rigid scale is untouched.
    monkeypatch.setattr(M, "ring_shimmer", lambda tt, ring: 1.30)
    wide_pts, _ = probe_stroke()
    assert (max(abs(p - CX) for p in wide_pts[0::2])
            > max(abs(p - CX) for p in bright_pts[0::2]))


def test_screen_break_bodies_are_drawn_outside_the_radius(monkeypatch):
    # Owner likes the mark "breaking the screen": a body the field pushes past
    # the radius is painted where it is, never clipped back inside.
    _strip_moving_layers(monkeypatch)
    bodies = [
        {"index": 0.0, "x": 1.4 * R, "y": 0.0, "depth": 0.5, "size": 1.6,
         "alpha": 0.95, "lift": 0.5, "hue": 0.0},
        {"index": 1.0, "x": -1.4 * R, "y": 0.0, "depth": 0.5, "size": 1.6,
         "alpha": 0.95, "lift": 0.5, "hue": 1.0 / 3.0},
    ]
    monkeypatch.setattr(M, "_field_screen_break",
                        lambda tt, rr: [dict(b) for b in bodies])
    c, _ = render(0.0)
    reach = max(abs((o[0] + o[2]) / 2.0 - CX) for o, _kw in c.ovals)
    assert reach > R, reach


def test_reduced_motion_draws_no_volume_or_screen_break(monkeypatch):
    monkeypatch.setattr(M, "_field_volume_particles", lambda tt, rr: [
        {"index": 0.0, "x": 0.0, "y": 0.0, "depth": 0.8, "size": 1.5,
         "alpha": 0.9, "lift": 0.5, "hue": 0.5, "spin": 0.0}])
    monkeypatch.setattr(M, "_field_screen_break", lambda tt, rr: [
        {"index": 0.0, "x": 1.5 * R, "y": 0.0, "depth": 0.8, "size": 1.5,
         "alpha": 0.9, "lift": 0.5, "hue": 0.5}])
    c, stats = render(1.7, reduced=True)
    # (Binary "1"/"0" fragments are a pre-existing still-frame layer; the
    # retired math glyphs, the volume field and the screen-break bodies must
    # all be absent from a still frame.)
    assert all(kw.get("text") in ("0", "1") for (_x, _y), kw in c.texts)
    assert stats["ovals"] == len(M.PARTICLES) + M.NUCLEUS_OVAL_COUNT


# --------------------------------------------------------------------------- #
# the parallax haze bed (field ``haze_state``)
# --------------------------------------------------------------------------- #
def _haze_probe(x=20.0):
    return {"index": 0.0, "x": x, "y": 0.0, "depth": 0.5, "z": 0.5,
            "z2": 0.5, "size": 2.0, "alpha": 0.05, "lift": 0.25,
            "hue": 0.6, "hue2": 0.65, "breath": 0.5}


def _pins_at(canvas, x, y=0.0):
    """Ovals centred exactly at ``(CX + x, CY + y)`` (t=0 has no tumble)."""
    return [(i, o, kw) for i, (o, kw) in enumerate(canvas.ovals)
            if abs((o[0] + o[2]) / 2.0 - (CX + x)) < 1e-6
            and abs((o[1] + o[3]) / 2.0 - (CY + y)) < 1e-6]


def test_haze_is_a_faint_bed_behind_every_hairline(monkeypatch):
    # Owner 2026-10-19: a faint parallax bed behind everything. Each body is
    # two soft background-composited discs (an outer and an inner stop), and
    # both are drawn BEFORE the first stroked circle so the bed can never
    # erase a ring (Tk has no alpha compositing).
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "_field_haze",
                        lambda tt, rr: [dict(_haze_probe())])
    c, _stats = render(0.0)
    pins = _pins_at(c, 20.0)
    assert len(pins) == 2, "haze is two soft stops per body"
    # Faint: both stops are much closer to the dark background than to white.
    from voicebridge.orb import ember as E
    for _i, _o, kw in pins:
        fill = kw.get("fill")
        rgb = tuple(int(fill[k:k + 2], 16) for k in (1, 3, 5))
        assert sum(abs(a - b) for a, b in zip(rgb, E.NEAR_BLACK)) < 220
    first_line = next(i for i, (kind, _j) in enumerate(c.calls)
                      if kind == "line")
    for oval_i, _o, _kw in pins:
        call_i = next(i for i, (kind, j) in enumerate(c.calls)
                      if kind == "oval" and j == oval_i)
        assert call_i < first_line, "haze must sit behind the hairlines"


def test_reduced_motion_draws_no_haze(monkeypatch):
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "_field_haze",
                        lambda tt, rr: [dict(_haze_probe())])
    live, _ = render(0.0)
    frozen, _ = render(0.0, reduced=True)
    assert len(_pins_at(live, 20.0)) == 2
    assert _pins_at(frozen, 20.0) == [], "haze is motion and must freeze out"


def test_haze_is_junk_safe(monkeypatch):
    monkeypatch.setattr(M, "_field_haze", lambda tt, rr: [
        None, "bad", {"index": "x", "x": "nope", "y": object(),
                      "depth": None, "size": "?", "alpha": "!",
                      "hue": "?"}])
    c, stats = render(1.7)
    assert stats["ovals"] > 0 and stats["lines"] > 0
