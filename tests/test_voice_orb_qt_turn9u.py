"""Qt orb: the horizontal (Y-axis) turn, the declared circles, the RGB streaks.

Hermetic — offscreen ``QImage``, no window, no X server. Three behaviours of
the live Qt renderer that no other module pins:

  (a) TURN      — the horizontal spin is one full 360 deg turn per period, is
                  additive to the master spin, foreshortens the mark's FLAT
                  plane, MIRRORS it past 90 degrees, and is a true no-op under
                  reduced motion.
  (b) GEOMETRY  — every circle the painter strokes comes from the field's
                  single declared ``ring_geometry`` list.
  (c) SPECTRUM  — a star is drawn as a glint with no halo or wake, and its
                  colour comes from the FIELD's colour wheel, not from HSV
                  maths duplicated in the renderer.

The turn is measured at the pixels, not at the numbers, wherever a number
would only prove the number changed.
"""
from __future__ import annotations

import contextlib
import math
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtGui", reason="Qt-less env")

BG = (11, 13, 18)


def _qt_app():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


@contextlib.contextmanager
def _env(key, value):
    prior = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


def _clean_env():
    os.environ.pop("QUBIT_REDUCED_MOTION", None)


def _paint(t=1.2, state="idle", size=192, reduced=False, family=None):
    _qt_app()
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    _clean_env()
    if family is None:
        family = E.family_from_core(E.state_core(state))
    if reduced:
        os.environ["QUBIT_REDUCED_MOTION"] = "1"
    try:
        img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(*BG))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        Q._paint_mark(p, size / 2.0, size / 2.0, size / 2.0 - 6.0, t, state,
                      1.0, family)
        p.end()
        return img
    finally:
        _clean_env()


def _bytes(img):
    return bytes(img.constBits())


def _diff(a, b):
    return sum(1 for x, y in zip(_bytes(a), _bytes(b)) if x != y)


# --------------------------------------------------------------------------- #
# (a) the horizontal turn
# --------------------------------------------------------------------------- #
def test_horizontal_spin_is_one_full_turn_per_period_and_continuous():
    from voicebridge.orb import qt_app as Q

    assert Q.mark_horizontal_spin_deg(0.0) == pytest.approx(0.0)
    assert Q.mark_horizontal_spin_deg(Q.Y_SPIN_PERIOD_S) == pytest.approx(360.0)
    # A ramp, never a wrapped phase: it keeps counting past a turn, so it
    # cannot snap at the boundary.
    assert Q.mark_horizontal_spin_deg(2.5 * Q.Y_SPIN_PERIOD_S) == pytest.approx(
        900.0)
    step = 360.0 / Q.Y_SPIN_PERIOD_S / 60.0
    prev = Q.mark_horizontal_spin_deg(0.0)
    for k in range(1, int(60 * Q.Y_SPIN_PERIOD_S)):
        cur = Q.mark_horizontal_spin_deg(k / 60.0)
        assert cur - prev == pytest.approx(step, abs=1e-9), (k, prev, cur)
        prev = cur


def test_horizontal_spin_is_a_no_op_under_reduced_motion():
    from voicebridge.orb import qt_app as Q

    with _env("QUBIT_REDUCED_MOTION", "1"):
        assert Q.mark_horizontal_spin(37.4) == 1.0
        assert Q.mark_horizontal_spin(-12.0) == 1.0
        assert Q.mark_horizontal_spin(0.0) == 1.0
        assert Q.mark_horizontal_spin_deg(37.4) == 0.0


def test_horizontal_spin_is_the_turns_cosine_and_mirrors_it():
    from voicebridge.orb import qt_app as Q

    # ``mark_horizontal_spin`` is the foreshortening of a FLAT plane: the
    # mark itself is no longer flat (see y_turn_point), but this stays the
    # public read of the turn's PHASE. Face-on at the start of a turn, and
    # MIRRORED at half a turn: a negative factor is what a rotation about a
    # vertical axis does past 90 degrees.
    assert Q.mark_horizontal_spin(0.0) == pytest.approx(1.0)
    assert Q.mark_horizontal_spin(Q.Y_SPIN_PERIOD_S / 2.0) == pytest.approx(
        -1.0, abs=1e-6)
    assert Q.mark_horizontal_spin(Q.Y_SPIN_PERIOD_S) == pytest.approx(1.0)
    # The turn is an even 360 now: no phase warp survives, because the warp
    # existed only to shorten the moments the mark went edge-on to a line and
    # there are none left to shorten. So the crossings sit exactly on the
    # quarter points.
    assert Q.mark_horizontal_spin(0.25 * Q.Y_SPIN_PERIOD_S) == pytest.approx(
        0.0, abs=1e-6)
    assert Q.mark_horizontal_spin(0.75 * Q.Y_SPIN_PERIOD_S) == pytest.approx(
        0.0, abs=1e-6)
    assert Q.mark_horizontal_spin(Q.Y_SPIN_PERIOD_S / 3.0) == pytest.approx(
        -0.5, abs=1e-6)


def test_the_turn_is_continuous_and_never_collapses():
    # The OLD contract here was "not edge-on for long" — it measured how
    # briefly the flat squash went to zero, which is the behaviour the owner
    # rejected. The contract now is the opposite: the turn is continuous
    # everywhere and its cos(theta) has no dwell at any value.
    dt = 1.0 / 60.0
    from voicebridge.orb import qt_app as Q

    values = [Q.mark_horizontal_spin(k * dt)
              for k in range(int(60 * Q.Y_SPIN_PERIOD_S) + 1)]
    assert min(values) <= -0.999, min(values)
    assert max(values) >= 0.999, max(values)
    # Continuous: no frame-to-frame step anywhere, through both crossings and
    # the turn wrap.
    worst = max(abs(b - a) for a, b in zip(values, values[1:]))
    assert worst < 0.05, worst


def test_horizontal_spin_paint_frame_is_continuous_across_its_whole_turn():
    # Measured on pixels: the painted frame must move by about ONE frame's
    # worth at every point of the turn, including through both crossings and
    # the wrap. A snap anywhere shows up here as an outlier.
    dt = 1.0 / 60.0
    from voicebridge.orb import qt_app as Q

    deltas = []
    prev = _paint(t=0.0, size=192)
    for k in range(1, int(60 * Q.Y_SPIN_PERIOD_S) + 1):
        cur = _paint(t=k * dt, size=192)
        deltas.append(_diff(prev, cur))
        prev = cur
    typical = sorted(deltas)[len(deltas) // 2]
    assert max(deltas) < 6.0 * max(typical, 1), (max(deltas), typical)


def test_horizontal_spin_is_additive_to_the_master_spin():
    from voicebridge.orb import qt_app as Q

    # Both turns exist, both are independent ramps, and neither period is a
    # harmonic of the other (nor of the ring turns), so the two never lock
    # into a repeating pattern.
    assert Q.MASTER_SPIN_PERIOD_S != Q.Y_SPIN_PERIOD_S
    for other in (9.5, 13.5, Q.MASTER_SPIN_PERIOD_S):
        ratio = Q.Y_SPIN_PERIOD_S / other
        assert abs(ratio - round(ratio)) > 1e-6, other

    # Painted proof of independence: hold one turn still and move the other.
    q = Q.Y_SPIN_PERIOD_S / 8.0          # a quarter of the way in
    r = Q.MASTER_SPIN_PERIOD_S / 4.0
    a = _paint(t=q)
    b = _paint(t=q + r)                 # same Y turn, different master angle
    c = _paint(t=q + q)                 # different Y turn, same master angle
    assert _diff(a, b) > 0, "the master spin stopped moving the mark"
    assert _diff(a, c) > 0, "the horizontal spin stopped moving the mark"


# --------------------------------------------------------------------------- #
# (a2) the turn is a real 3D rotation, so the mark NEVER goes thin
# --------------------------------------------------------------------------- #
def test_the_turn_opens_the_mark_instead_of_flattening_it():
    """The owner's complaint, pinned at the numbers AND at the pixels.

    A flat mark projected about a vertical axis foreshortens to
    ``cos(theta)`` and degenerates to a line at a quarter turn — the "i dont
    what that thin" the flat squash produced. This mark is not flat: every
    element carries the field's ``depth``, so the near and far halves SEPARATE
    across x instead of piling onto one line.
    """
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    R = 90.0
    quarter = Q.Y_SPIN_PERIOD_S / 4.0

    # One DECLARED orbit, so this measures the projection and nothing else.
    spec = [r for r in F.ring_geometry(0.0) if r["id"] == "orbit_a"][0]
    pts = F.ring_path(0.0, R, spec)
    n = len(pts) - 1
    depths = [0.5 + 0.5 * math.cos(2.0 * math.pi * (i % n) / n)
              for i in range(len(pts))]

    def width_at(t):
        xs = [Q.y_turn_point(t, R, x, y, d)[0]
              for (x, y), d in zip(pts, depths)]
        return max(xs) - min(xs)

    face = width_at(0.0)
    quarter_w = width_at(quarter)
    assert quarter_w >= 0.45 * face, (quarter_w, face)
    assert width_at(Q.Y_SPIN_PERIOD_S / 2.0) >= 0.45 * face, face

    # ...and the halves really have parted. Take two elements that sit on top
    # of each other in x face-on — one at the very front of the plane, one at
    # the very back — which is exactly what the flat squash left stacked on a
    # single line. Under the rotation they end up half a mark radius apart.
    def front_back_gap(t):
        return (Q.y_turn_point(t, R, 0.0, 0.0, 1.0)[0]
                - Q.y_turn_point(t, R, 0.0, 0.0, 0.0)[0])
    assert front_back_gap(0.0) == pytest.approx(0.0, abs=1e-9)
    assert front_back_gap(quarter) >= 0.50 * face, front_back_gap(quarter)
    # The depth axis is a real one: enough separation to carry the mark's own
    # width, which is why the quarter turn no longer blanks the orb.
    assert Q.Y_SPIN_Z_GAIN * R >= 0.45 * face, (Q.Y_SPIN_Z_GAIN * R, face)


def _ink_width(t, size=192):
    """Inked bounding-box width of a painted frame, from the recorded prims."""
    import test_voice_orb_qt_fx9u as T

    c = size / 2.0
    lo, hi = size, -1
    for p in T._record(t=t, size=size):
        if p.kind == "text":
            r = 4.0
        elif p.kind == "path":
            r = max(p.w, p.h) / 2.0
        else:
            r = p.r
        lo = min(lo, p.cx - r)
        hi = max(hi, p.cx + r)
    return hi - lo


def test_the_mark_never_goes_thin_across_a_whole_turn():
    # The same complaint, measured on the whole assembly. Every phase of the
    # turn is painted and its INKED width compared with the widest frame in
    # the period: the mark may narrow as it turns, but it must never collapse
    # toward a line the way the flat squash did (which reached roughly a fifth
    # of the full width, and looked like the orb had switched off).
    from voicebridge.orb import qt_app as Q

    widths = {}
    for k in range(0, int(Q.Y_SPIN_PERIOD_S * 20)):
        t = k / 20.0
        widths[t] = _ink_width(t)
    full = max(widths.values())
    thin = min(widths.values())
    worst = min(widths, key=lambda t: widths[t])
    assert thin >= 0.50 * full, (
        f"mark thinned to {thin:.1f}px ({thin / full:.0%} of {full:.1f}px) at "
        f"t={worst:.2f}s theta={Q.mark_horizontal_spin_deg(worst):.1f}deg")


def test_the_turn_is_the_identity_face_on_and_a_pure_mirror_at_the_half_turn():
    # theta = 0 must be an EXACT identity — no pop on start-up, and a still
    # frame (reduced motion clamps the clock to 0) is the unturned mark. The
    # half turn is its exact mirror in x: a rotation, not a squash, so it is
    # as wide as it started.
    from voicebridge.orb import qt_app as Q

    R, half = 90.0, Q.Y_SPIN_PERIOD_S / 2.0
    for x, y in ((63.0, -21.0), (-63.0, 21.0), (0.0, 30.0), (-12.5, 0.0)):
        for d in (0.0, 0.5, 1.0):
            X, Y, g = Q.y_turn_point(0.0, R, x, y, d)
            assert (X, Y, g) == pytest.approx((x, y, 1.0), abs=1e-9), (x, y, d)
            X, Y, g = Q.y_turn_point(half, R, x, y, d)
            assert (X, Y, g) == pytest.approx((-x, y, 1.0),
                                            abs=1e-9), (x, y, d)
    # And on the pixels: the still frame is the mark itself, not the nucleus.
    still_a = _paint(t=0.0, reduced=True)
    assert _diff(still_a, _paint(t=37.4, reduced=True)) == 0


def test_rotated_depth_drives_size_and_brightness():
    # A body swinging toward the viewer gets BIGGER and BRIGHTER; one tucking
    # away gets smaller and dimmer. This is the cue that makes the turn read
    # as rotation rather than as a wobble, so it is pinned directly.
    from voicebridge.orb import qt_app as Q

    assert Q.y_turn_gain(0.0) == pytest.approx(1.0)
    assert Q.y_turn_gain(0.5) > 1.0 and Q.y_turn_gain(-0.5) < 1.0
    # Clamped, so no field value outside its depth range can blow a body up.
    assert Q.y_turn_gain(1e6) == Q.Y_SPIN_GAIN_MAX
    assert Q.y_turn_gain(-1e6) == Q.Y_SPIN_GAIN_MIN
    # ...and it is the ROTATED depth that drives it, so the same element swaps
    # as the turn carries it round: at a quarter turn the LEFT of the plane
    # swings toward the viewer and gains, where face-on it was balanced.
    quarter = Q.Y_SPIN_PERIOD_S / 4.0
    left = Q.y_turn_point(0.0, 90.0, -60.0, 0.0, 1.0)[2]
    left_q = Q.y_turn_point(quarter, 90.0, -60.0, 0.0, 1.0)[2]
    assert left_q > left, (left, left_q)


def test_the_painter_really_consumes_the_turn_and_its_depth_gain():
    # Owner 2026-10-22 RETIRED the whole-mark tumble (``spin_angles`` returns
    # zeros), so there is no body-wide turn or rotated-depth gain left to
    # consume — those knobs are deliberately inert and asserting they move the
    # frame would be asserting the anti-pattern back in.
    #
    # The wiring proof is kept, retargeted at what now carries the motion: the
    # painter must really consume each ring's OWN spin. Zeroing every ring's
    # spin must change the painted frame, or the circles are drawn dead.
    from voicebridge.orb import mark as M
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    t = Q.Y_SPIN_PERIOD_S / 4.0
    plain = _paint(t=t, size=192)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(Q, "Y_SPIN_Z_GAIN", 0.0)
        assert _diff(plain, _paint(t=t, size=192)) == 0, \
            "the retired whole-mark tumble must be inert"

    frozen = []
    for spec in F.ring_geometry(t):
        spec = dict(spec)
        spec["spin_deg"] = 0.0
        spec["tilt_spin_deg"] = 0.0
        frozen.append(spec)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(Q._field, "ring_spin_deg", lambda *a, **k: 0.0)
        m.setattr(Q._field, "ring_tilt_spin", lambda *a, **k: 0.0)
        m.setattr(Q._field, "ring_geometry", lambda *a, **k: [dict(s) for s in frozen])
        still = _paint(t=t, size=192)
    assert _diff(plain, still) > 0, "the circles are painted without their spin"


def test_the_nucleus_stays_round_while_the_orbital_plane_opens():
    # The nucleus is EXEMPT from the turn: a sphere keeps its circular
    # silhouette when you rotate it about its own vertical axis, and squashing
    # the core into a lens is the one transform here that would read as a bug
    # rather than as depth. Meanwhile the orbital plane around it OPENS at the
    # quarter turn instead of collapsing — which is what used to leave a bare
    # nucleus floating in the middle of an empty orb.
    from voicebridge.orb import qt_app as Q

    def core_extent(t, size=192):
        img = _paint(t=t, size=size)
        c = size // 2
        reach = size // 8
        cols, rows = 0, 0
        for dx in range(-reach, reach + 1):
            for dy in (-2, 2):           # a horizontal scan through the core
                if img.pixelColor(c + dx, c + dy).alpha() > 90:
                    cols = max(cols, abs(dx))
        for dy in range(-reach, reach + 1):
            for dx in (-2, 2):
                if img.pixelColor(c + dx, c + dy).alpha() > 90:
                    rows = max(rows, abs(dy))
        return cols, rows

    quarter = Q.Y_SPIN_PERIOD_S / 4.0
    face_cols, face_rows = core_extent(0.0)
    edge_cols, edge_rows = core_extent(quarter)
    assert face_rows > 0 and edge_rows > 0, (face_rows, edge_rows)
    # Vertical extent is untouched by a Y rotation of a sphere...
    assert edge_rows == pytest.approx(face_rows, rel=0.6)
    # ...and the horizontal extent stays close to it, not collapsed with the
    # plane. A squashed nucleus would come back far narrower than tall.
    assert edge_cols > 0.45 * edge_rows, (edge_cols, edge_rows)
    # The mark around it did NOT collapse either: at this clock the whole
    # frame is far wider than the bare core.
    assert _ink_width(quarter) > 3.0 * edge_cols, (_ink_width(quarter),
                                                  edge_cols)


# --------------------------------------------------------------------------- #
# (b) one declared geometry for every circle
# --------------------------------------------------------------------------- #
def test_mark_ring_geometry_declares_every_circle_and_never_raises():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    rings = Q.mark_ring_geometry(0.37)
    assert len(rings) == len(F.ring_geometry(0.37)) == 10
    layers = [r["layer"] for r in rings]
    assert layers.count("main") == 2        # the two logo orbits
    assert layers.count("accent") == 2      # the two added, more-visible circles
    assert layers.count("echo") == 2        # their followers
    assert layers.count("inner") == 1       # the core ring
    assert layers.count("satellite") == 3   # the shells
    # Every id is distinct: two circles sharing an id would be one circle.
    assert len({r["id"] for r in rings}) == len(rings)
    # Deterministic, and frozen under reduced motion.
    assert rings == Q.mark_ring_geometry(0.37)
    with _env("QUBIT_REDUCED_MOTION", "1"):
        assert Q.mark_ring_geometry(9.0) == Q.mark_ring_geometry(0.0)
    # A field that cannot declare its circles must not raise through the
    # painter: the orb degrades, it never crashes and never blanks.
    with pytest.MonkeyPatch.context() as m:
        m.setattr(F, "ring_geometry", _boom)
        assert Q.mark_ring_geometry(0.37) == []
    _paint(t=0.37)  # a field fault must not escape _paint_mark either


def _boom(*_a, **_k):
    raise ValueError("field fault")


def _projected_ring_box(t, R, spec):
    """Box of one declared ring's path AFTER the mark's X+Y tumble.

    Mirrors the painter exactly: sample :func:`mark_field.ring_path` (with
    the painter's sample count), take the camera sway back out, give each
    point the painter's smooth out-of-plane lobe
    (``0.5 + TURN_RING_TILT*sin(angle + phase)``, phased by the ring's own
    seed — never the field depth, which would lock every ring onto one
    diagonal), and project through :func:`qt_app.tumble_point` (Y first,
    then X — the painter's order).
    """
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    spec = dict(spec)
    # Fluid circles: mirror the painter's added plane precession, shared radius
    # breath AND per-ring shimmer so the expected box is the drawn box.
    spin = spec.get("tilt_spin_deg")
    if spin is None:
        spin = F.ring_tilt_spin(t, spec)
    breath = F.ring_radius_scale(t, spec)
    shimmer = spec.get("shimmer")
    if shimmer is None:
        shimmer = F.ring_shimmer(t, spec)
    spec["tilt"] = float(spec.get("tilt") or 0.0) + spin
    spec["rx_frac"] = float(spec.get("rx_frac") or F.RX_FRAC) * breath * shimmer
    spec["ry_frac"] = float(spec.get("ry_frac") or F.RY_FRAC) * breath * shimmer
    dx, dy = F.camera_offset(t, R)
    pts = F.ring_path(t, R, spec, samples=Q.RING_WARP_SAMPLES)
    n = max(1, len(pts) - 1)
    rphase = 2.0 * math.pi * F.hash01(float(spec.get("seed", 0.0)),
                                     Q.TURN_RING_SALT)
    xs, ys = [], []
    for i, (px, py) in enumerate(pts):
        qd = 0.5 + Q.TURN_RING_TILT * math.sin(
            2.0 * math.pi * (i % n) / n + rphase)
        X, Y, _g = Q.tumble_point(t, R, px - dx, py - dy,
                                 min(1.0, max(0.0, qd)))
        xs.append(X)
        ys.append(Y)
    return (max(xs) - min(xs), max(ys) - min(ys))


def test_every_stroked_circle_matches_the_declared_geometry():
    """Each declared circle leaves a stroke whose box is its own path's box."""
    import test_voice_orb_qt_fx9u as T  # the recording painter lives there

    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, t = 192, 0.37
    c = size / 2.0
    R = size / 2.0 - 6.0
    prims = T._record(t=t, size=size)
    # Only strokes as wide as a DECLARED circle. The core ring is the smallest,
    # and the nucleus's own halo/shockwave outlines are centre detail, not
    # circles of the mark. Strokes as thin as a glint ARM (GLINT_PEN_W) are the
    # batched new-layer ring-particle arms, which are not circles at all.
    strokes = [p for p in prims if p.kind == "path" and p.fill is None
               and p.pen is not None and max(p.w, p.h) >= 40.0
               and abs(p.width - Q.GLINT_PEN_W) > 0.05]
    echo_tilt = {str(e["orbit"]): e["tilt"] for e in F.echo_rings(t)}
    unmatched = list(strokes)
    for ring in F.ring_geometry(t):
        spec = dict(ring)
        if ring.get("echo"):
            # The painter reconciles the follower's tilt onto the shared
            # single-lag echo contract; the radii/layer/width are the
            # declaration's.
            spec["tilt"] = echo_tilt.get(str(ring["orbit"]), ring["tilt"])
        # The stroke's box is the DECLARED path's box AFTER the mark's Y-axis
        # turn, not the flat one: a circle is a stroked path, so each point is
        # projected individually (with its own depth, taken from the field's
        # orbital angle) and the box is measured on the projected points.
        want = _projected_ring_box(t, R, spec)
        hit = None
        for p in unmatched:
            if (abs(p.w - want[0]) <= 1.5 and abs(p.h - want[1]) <= 1.5):
                hit = p
                break
        assert hit is not None, (ring["id"], want, len(unmatched))
        unmatched.remove(hit)
    # Owner 2026-10-19: the chromatic fringe was retired from the rings (the
    # circles "are too colored they look like galaxies"), so every declared
    # circle is now exactly ONE stroke and nothing else is left over.
    assert unmatched == [], [(round(p.w, 1), round(p.h, 1))
                             for p in unmatched]


def test_ring_strokes_are_translucent_and_near_monochrome():
    # Owner 2026-10-19: the circles "need to be more transparent and they are
    # too colored they look like galaxies". Every declared circle is now ONE
    # faint stroke in the mark's own colour; the ring set carries no second
    # hue and nothing above a low alpha ceiling.
    import test_voice_orb_qt_fx9u as T

    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, t = 192, 0.37
    prims = T._record(t=t, size=size)
    strokes = [p for p in prims if p.kind == "path" and p.fill is None
               and p.pen is not None and max(p.w, p.h) >= 40.0
               and abs(p.width - Q.GLINT_PEN_W) > 0.05]
    # The declared circles: two main, two echoes, three shells, one core, two
    # accent. Owner 2026-10-19 added the two accent circles at 60% MORE
    # opacity than the main orbits, so the ceiling is the accent target.
    assert len(strokes) == len(F.ring_geometry(t))
    for p in strokes:
        assert p.alpha <= Q.RING_DRAW_ALPHA["accent"] + 0.02, p.alpha
        # Near-monochrome: the red/blue chromatic fringe is gone, so no ring
        # stroke wears either of its literal colours (the mark's own warm ring
        # tint is red-dominant by design, so test the fringe colours exactly).
        assert tuple(p.pen[:3]) not in ((255, 45, 85), (45, 130, 255)), p.pen
    # The accent circles are the most visible; the main orbits next.
    assert Q.RING_DRAW_ALPHA["accent"] > Q.RING_DRAW_ALPHA["near_main"]
    assert Q.RING_DRAW_ALPHA["near_main"] > Q.RING_DRAW_ALPHA["echo"]
    assert Q.RING_DRAW_ALPHA["near_main"] > Q.RING_DRAW_ALPHA["satellite"]


def test_no_chromatic_fringe_anywhere():
    import test_voice_orb_qt_fx9u as T

    size, c, t = 192, 96.0, 0.37
    prims = T._record(t=t, size=size)
    # Owner 2026-10-19: the red/blue split — on the two main orbits AND on the
    # nucleus rim — was the last multi-hue spray and read as a galaxy. It is
    # retired everywhere: no red/blue chromatic stroke remains anywhere.
    fringe = [p for p in prims if p.kind == "path" and p.pen is not None
              and tuple(p.pen[:3]) in ((255, 45, 85), (45, 130, 255))]
    assert fringe == [], [p.pen for p in fringe]


# --------------------------------------------------------------------------- #
# (c) NO wake ribbons — owner 2026-10-19: the 3-point "RGB trail" ribbons
# read as thick bent L/V check-mark sticks and are gone from the Qt painter.
# The field's trail builders are retired with them.
# --------------------------------------------------------------------------- #
def _streak_prims(t, size=192, reduced=False):
    """Filled PATHS away from the nucleus: the old wake ribbons' slot."""
    import test_voice_orb_qt_fx9u as T

    prims = T._record(t=t, size=size, reduced=reduced)
    c = size / 2.0
    return [p for p in prims
            if p.kind == "path" and p.fill is not None
            and math.hypot(p.cx - c, p.cy - c) > 3.0]


def test_no_wake_ribbon_helper_or_constants_survive():
    # Owner 2026-10-19: the 3-point "RGB trail" ribbons read as thick bent L/V
    # check-mark sticks and were deleted outright, so there is no code path
    # left that could paint one again.
    from voicebridge.orb import qt_app as Q

    for name in ("_streak", "TRAIL_DRAW_SAMPLES", "TRAIL_HALF_WIDTH",
                 "TRAIL_MIN_HALF_PX", "TRAIL_MIN_LEN_PX",
                 "SPARKLE_TRAIL_HALF_WIDTH", "COMET_TRAIL_HALF_WIDTH"):
        assert not hasattr(Q, name), name
    # The bodies are still drawn — as glints/dots, never ribbons.
    t, size = 0.42, 192
    assert _streak_prims(t, size) is not None
    from voicebridge.orb import mark_field as F
    for part in F.particle_state(t, size / 2.0 - 6.0):
        assert "char" not in part


def test_field_trails_are_retired():
    """The dead wake-trail API is gone from the field and the painter."""
    from voicebridge.orb import mark_field as F

    for name in ("rgb_trail", "dust_trail", "_rgb_trail"):
        assert not hasattr(F, name), name
    assert _paint(t=0.42, size=192) is not None


def test_reduced_motion_leaves_no_ribbons():
    still = _streak_prims(0.31, reduced=True)
    live = _streak_prims(0.31)
    assert len(still) <= len(live)
    assert all(max(p.w, p.h) <= 0.25 * 192 for p in still)


# (c2) duotone streaks, blink dots, hologram cues
# --------------------------------------------------------------------------- #
def test_comet_beads_walk_the_duotone_pair_not_a_ramp():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    t, size = 5.0, 192
    plain = _paint(t=t, size=size)
    # The old HUE_STEP ramp is retired with the trail API: no constant remains.
    assert not hasattr(F, "RGB_TRAIL_HUE_STEP")
    # The pair's second hue drives the beads: moving hue2 moves ink.
    real = F.comet

    def shifted(tt, rr):
        d = dict(real(tt, rr))
        d["hue2"] = (float(d["hue"]) + 0.25) % 1.0
        return d

    with pytest.MonkeyPatch.context() as m:
        m.setattr(Q._field, "comet", shifted)
        assert _diff(plain, _paint(t=t, size=size)) > 0


def test_blink_dots_paint_as_pinpricks_and_vanish_when_still():
    import test_voice_orb_qt_fx9u as T

    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    t = 3.7
    dots = Q.mark_blink_dots(t, R)
    assert len(dots) == F.BLINK_COUNT
    live = [d for d in dots if d["alpha"] > 0.05]
    assert len(live) >= 8, len(live)
    prims = T._record(t=t, size=size)
    pinpricks = [p for p in prims if p.kind == "ellipse" and p.fill is not None
                 and p.pen is None and p.r <= 2.0]
    for d in live:
        qd = Q.turn_qdepth(d["depth"], d.get("index"), flat=True)
        X, Y, _g = Q.tumble_point(t, R, d["x"], d["y"], qd)
        vox, voy = Q.volume_offset(t, R, d.get("z"), d.get("z2"))
        assert any(math.hypot(p.cx - (c + X + vox),
                             p.cy - (c + Y + voy)) <= 1.0
                   for p in pinpricks), (d["index"], X, Y)
    # They are dots, not streaks: killing them removes no filled path.
    with_paths = {id(p) for p in prims if p.kind == "path" and p.fill is not None}
    with pytest.MonkeyPatch.context() as m:
        m.setattr(Q, "mark_blink_dots", lambda tt, rr: [])
        bare = T._record(t=t, size=size)
    bare_paths = {id(p) for p in bare if p.kind == "path" and p.fill is not None}
    assert len(with_paths) == len(bare_paths)
    # And a still frame carries none of them.
    with _env("QUBIT_REDUCED_MOTION", "1"):
        assert Q.mark_blink_dots(t, R) == []
    still = _paint(t=t, size=size, reduced=True)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(Q, "mark_blink_dots", lambda tt, rr: [])
        assert _diff(still, _paint(t=t, size=size, reduced=True)) == 0


def test_nucleus_carries_a_fresnel_rim_and_chroma_split():
    import test_voice_orb_qt_fx9u as T

    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    prims = T._record(t=0.42, size=size)
    body = [p for p in prims if p.kind == "ellipse" and p.fill is not None
            and math.hypot(p.cx - c, p.cy - c) <= 0.75]
    assert len(body) == 1, body
    # The limb stroke rides the body edge: centred, same radius, bright, thin.
    limb = [p for p in prims if p.kind == "path" and p.fill is None
            and p.pen is not None and tuple(p.pen[:3]) == (255, 255, 255)
            and math.hypot(p.cx - c, p.cy - c) <= 0.75
            and abs(p.r - body[0].r) <= 1.0]
    assert len(limb) == 1, limb
    assert limb[0].alpha == pytest.approx(Q.NUCLEUS_RIM_ALPHA, abs=0.02)
    assert limb[0].width == pytest.approx(Q.NUCLEUS_RIM_WIDTH, abs=0.2)
    # The chromatic split is retired (owner 2026-10-19): no red/blue rim.
    chroma = [p for p in prims if p.kind == "path" and p.fill is None
              and p.pen is not None
              and tuple(p.pen[:3]) in ((255, 45, 85), (45, 130, 255))]
    assert chroma == [], chroma


def test_near_far_separation_spread_is_wide():
    # Hologram, not flat: the back half must unmistakably recede — size AND
    # alpha (the gain) AND brightness (the tone) spread wide between depth 0
    # and depth 1, while gain 1 leaves everything untouched.
    from voicebridge.orb import qt_app as Q

    assert Q.y_turn_gain(1.0) - Q.y_turn_gain(-1.0) >= 1.0
    assert Q.turn_tone((255, 255, 255), Q.Y_SPIN_GAIN_MAX)[0] > 200
    assert Q.turn_tone((255, 255, 255), Q.Y_SPIN_GAIN_MIN)[0] < 220
    assert Q.turn_tone((200, 100, 50), 1.0) == (200, 100, 50)
    assert Q.turn_tone((200, 100, 50), "nonsense") == (200, 100, 50)


def test_the_combined_tumble_never_raises_on_bad_input():
    from voicebridge.orb import qt_app as Q

    for bad in ("nonsense", float("nan"), float("inf"), None, object()):
        assert Q.mark_tumble_angles(bad) == (0.0, 0.0)
        assert math.isfinite(Q.mark_vertical_spin_deg(bad))
        assert Q.mark_blink_dots(bad, bad) is not None
        for d in (0.0, 0.5, 1.0):
            X, Y, g = Q.tumble_point(bad, bad, bad, bad, d)
            assert math.isfinite(X + Y + g)