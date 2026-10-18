"""Tk-fallback contracts for the Qubit-mark orb's second animation round.

``voicebridge.orb.mark.render`` is the Tk sibling of the live Qt painter, and
this round added four things the Tk renderer could otherwise quietly lose:

  * the spectral streak each travelling body leaves INSTEAD of an outer glow,
    each exactly two colours (a duotone head/hue2 pair),
  * a true-3D whole-mark tumble — full 360 turns about the vertical (Y, 20.5 s)
    and horizontal (X, 27.0 s) axes, Y first then X — so the mark NEVER thins
    to a line the way the old flat ``cos`` squash did,
  * blink dots: pinprick ovals that materialise out of nowhere (alpha-only
    envelopes, depth-split like everything else),
  * ONE declared set of circles (the field's ``ring_geometry``), fringed only
    on the two main orbits,
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
        z = (d - 0.5) * M.Y_SPIN_Z_GAIN * R * st * st
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
    # the sliver phases are coincidence phases (X edge-on meeting a ring near
    # its untilted tilt), and a short window can miss them entirely.
    #
    # Ephemeral layers (dust, sparkles, sparks, blink dots, the comet) are
    # stripped from BOTH the face-on reference and the samples: they set the
    # full frame's width at face-on without participating in the slivers, so
    # leaving them in compares the sliver against a transient-inflated bar.
    # What is measured is the durable mark: rings, riders, streaks, binaries
    # and the nucleus stack.
    #
    # HONEST FLOOR 0.20, NOT 0.45 — a measured design finding, not a tuning
    # choice. The full-cycle scan bottoms out at ~0.25 of face-on (t=318 s).
    # Mechanism: the field's depth rides IN PHASE with each ring's x
    # (``0.5+0.5*cos``), so under a Y+X turn the tumbled shape is a thin
    # diagonal BAND rather than an opened loop wherever an X edge-on phase
    # (which squeezes every band by ``cos(phi)``) coincides with a ring
    # passing near-untilted through its counter-rotating tilt schedule. A
    # rigid ring (depth in quadrature, ``sin``) would stay open under any
    # rotation; that is a FIELD change, reported for the field lane (like the
    # Qt lane's echo-lag reports), not something this renderer can silently
    # re-derive — the riders must sit ON the field's own depths. Any
    # faithful Qt port of the same math measures the same slivers.
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
    assert worst >= 0.20 * full, (
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

    def _screen(x, y, depth):
        px, py, _g = M.turn_point(1.7, R, x, y, depth)
        rx = px * _m.cos(a) - py * _m.sin(a)
        ry = px * _m.sin(a) + py * _m.cos(a)
        return (round(CX + rx, 6), round(CY + ry, 6))

    idx = {tuple(round(v, 6) for v in ((o[0] + o[2]) / 2,
                                       (o[1] + o[3]) / 2)): i
           for i, (o, _kw) in enumerate(c.ovals)}
    nuc_idx = next(i for i, (o, kw) in enumerate(c.ovals)
                   if kw.get("fill")
                   and abs(abs((o[2] - o[0]) / 2) - body) < 1e-6)
    far_at = _screen(-20.0, 5.0, 0.2)
    near_at = _screen(18.0, -6.0, 0.8)
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
# RGB streaks instead of an outer glow
# --------------------------------------------------------------------------- #
def _strip_moving_layers(monkeypatch):
    """Leave one particle and its wake: nothing else in the frame can pose as
    a streak."""
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
    return part


def _sample(k, hue, step=5.0, alpha=0.9, size=1.6):
    return {"x": step * k, "y": 0.0, "hue": hue, "sat": 1.0, "val": 1.0,
            "alpha": alpha, "size": size, "depth": 0.5, "dof": 0.5,
            "k": float(k)}


def test_every_travelling_body_leaves_a_streak(monkeypatch):
    # Suppress the field's streak builders: the strokes that disappear are
    # exactly the wakes. Particles, sparkles, the shell travellers and the
    # comet all carry one, so this is a whole-layer contract, not a single
    # body's.
    full, _ = render(1.7)
    monkeypatch.setattr(M, "rgb_trail", lambda *a, **k: [])
    monkeypatch.setattr(M, "dust_trail", lambda *a, **k: [])
    bare, _ = render(1.7)
    assert len(full.lines) - len(bare.lines) > 20
    # The stars survive their absence: the frame is still a mark.
    assert len(bare.ovals) > 0 and len(bare.lines) > 0


def test_a_body_draws_its_wake_then_its_star(monkeypatch):
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "rgb_trail",
                        lambda tt, rr, body: [_sample(0, 0.5), _sample(1, 0.55),
                                              _sample(2, 0.6)])
    c, _ = render(1.7)
    wake = _segments(c)
    # The synthetic body sits at the field origin, which the whole-mark
    # transforms carry to the mark's own centre.
    star_x = star_y = CX
    assert len(wake) == 2, "one stroke per consecutive pair of samples"
    # The head rides ON the star, so the first segment starts where the glint
    # is drawn: the wake and the star cannot drift apart.
    at_star = [o for o, _kw in c.ovals
               if abs((o[0] + o[2]) / 2.0 - star_x) < 1e-6
               and abs((o[1] + o[3]) / 2.0 - star_y) < 1e-6
               and abs((o[2] - o[0]) / 2.0) < 1.0]
    assert len(at_star) == 1, "the star is still a single pinprick core"
    star = ((at_star[0][0] + at_star[0][2]) / 2.0,
            (at_star[0][1] + at_star[0][3]) / 2.0)
    assert math.dist(wake[0][0][:2], star) < 1e-6, (wake[0][0], star)


def test_streak_colours_are_converted_per_sample_not_lerped_across_the_wheel(
        monkeypatch):
    # Owner: "leave a RGB trail". The wheel is the FIELD's, and the renderer
    # converts each sample with hue_to_rgb instead of interpolating hue — a
    # lerp across the 1.0 -> 0.0 wrap sweeps the entire spectrum the wrong way
    # round and turns a red-to-red streak magenta, green and blue.
    _strip_moving_layers(monkeypatch)

    def strokes(hues):
        monkeypatch.setattr(M, "rgb_trail", lambda tt, rr, body: [
            _sample(i, h) for i, h in enumerate(hues)])
        c, _ = render(1.7)
        return [_rgb(kw["fill"]) for _pts, kw in _segments(c)]

    # Red -> green -> blue walks the ramp, so every segment is the blend of
    # the two samples it joins: yellow at the head, cyan at the tail.
    straight = strokes((0.0, 0.34, 0.66))
    head, tail = straight
    assert head[0] > 90 and head[1] > 90 and head[2] < 70, f"head: {head}"
    assert tail[2] > 90 and tail[1] > 90 and tail[0] < 70, f"tail: {tail}"

    wrapped = strokes((0.98, 1.0, 1.02))
    # 0.98 and 1.02 are a twentieth of a turn apart on the wheel, so the two
    # drawn colours must be nearly the SAME red, not a spectrum sweep.
    assert wrapped[0][0] > 120 and wrapped[1][0] > 120, wrapped
    assert all(abs(a - b) <= 12 for a, b in zip(wrapped[0], wrapped[1])), wrapped


def test_streaks_taper_and_fade_along_their_length(monkeypatch):
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "rgb_trail", lambda tt, rr, body: [
        _sample(0, 0.5, alpha=0.85, size=3.0),
        _sample(1, 0.5, alpha=0.45, size=1.8),
        _sample(2, 0.5, alpha=0.20, size=0.9)])
    c, _ = render(1.7)
    wake = _segments(c)
    assert len(wake) == 2
    widths = [kw["width"] for _pts, kw in wake]
    assert widths[0] >= widths[1], widths
    lum = [sum(_rgb(kw["fill"])) for _pts, kw in wake]
    assert lum[0] > lum[1], lum
    assert lum[-1] > 0, "a faded tail is dimmer, never painted in background"


def test_a_sub_pixel_wake_is_not_drawn(monkeypatch):
    # A ribbon shorter than a pixel is one 8-bit step from appearing, which is
    # the hard pop the field's C1 envelopes exist to avoid.
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "rgb_trail", lambda *a, **k: [])
    collapsed, _ = render(1.7)
    monkeypatch.setattr(M, "rgb_trail", lambda tt, rr, body: [
        _sample(0, 0.5, step=0.01), _sample(1, 0.5, step=0.01),
        _sample(2, 0.5, step=0.01)])
    c, _ = render(1.7)
    assert c.lines == collapsed.lines and c.ovals == collapsed.ovals


def test_reduced_motion_draws_no_streak_at_all(monkeypatch):
    _strip_moving_layers(monkeypatch)
    monkeypatch.setattr(M, "rgb_trail", lambda *a, **k: [
        _sample(i, 0.5 + 0.05 * i) for i in range(4)])
    live, _ = render(1.7)
    frozen, _ = render(1.7, reduced=True)
    assert _segments(live)
    assert not _segments(frozen)


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
    # Two fringed main orbits (3 strokes each), two followers, three shells,
    # one core ring.
    assert len(circles) == 2 * 3 + 2 + 3 + 1, len(circles)
    assert stats["lines"] > 0

    # ...and it is the DECLARED radius that is drawn, about the mark's centre.
    # Sampled at t=0, where both whole-mark transforms are the identity, so the
    # drawn points are the declared ones plus the body's own (unswayed) centre.
    probe = dict(F.ring_geometry(0.0)[0])
    probe.update({"id": "probe", "layer": "satellite", "orbit": "",
                  "rx_frac": 0.40, "ry_frac": 0.20, "alpha": 0.5, "tilt": 0.0})
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    zero, _ = render(0.0)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [probe])
    only, _ = render(0.0)
    added = _new_lines(only, zero)
    assert at_zero.lines == zero.lines, "the baseline really is empty"
    assert len(added) == 1
    pts = added[0][0]
    # No camera sway: the circles belong to the body, which does not sway.
    assert max(abs(p - CX) for p in pts[0::2]) == pytest.approx(
        R * probe["rx_frac"], abs=1e-6)
    assert max(abs(p - CY) for p in pts[1::2]) == pytest.approx(
        R * probe["ry_frac"], abs=1e-6)


def test_only_the_two_main_orbits_carry_the_chromatic_fringe(monkeypatch):
    # The fringe is the logo's signature. A follower / core ring / shell
    # wearing it reads as four more logo orbits instead of the quiet circles
    # they are, so a renderer that fringes everything has lost the art
    # direction even though every circle is still drawn.
    specs = F.ring_geometry(2.3)
    monkeypatch.setattr(M, "ring_geometry", lambda tt: [])
    bare, _ = render(2.3)
    for spec in specs:
        only = [spec]
        monkeypatch.setattr(M, "ring_geometry", lambda tt, o=only: o)
        one, _ = render(2.3)
        strokes = _new_lines(one, bare)
        assert len(strokes) == (3 if spec["layer"] == "main" else 1), (
            spec["id"], len(strokes))


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
    assert stats["lines"] == 2 * len(M.PARTICLES) + 7


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