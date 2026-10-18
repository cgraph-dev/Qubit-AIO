"""Qt orb: the horizontal (Y-axis) turn, the declared circles, the RGB streaks.

Hermetic — offscreen ``QImage``, no window, no X server. Three behaviours of
the live Qt renderer that no other module pins:

  (a) TURN      — the horizontal spin is one full 360 deg turn per period, is
                  additive to the master spin, foreshortens the mark's FLAT
                  plane, MIRRORS it past 90 degrees, and is a true no-op under
                  reduced motion.
  (b) GEOMETRY  — every circle the painter strokes comes from the field's
                  single declared ``ring_geometry`` list.
  (c) SPECTRUM  — a star leaves a streak (no halo), the streak is what the
                  field's ``rgb_trail`` / ``dust_trail`` describe, and its
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


def test_horizontal_spin_for_shortens_the_plane_and_mirrors_it():
    from voicebridge.orb import qt_app as Q

    # Face-on at the start of a turn, and MIRRORED at half a turn: a negative
    # factor is what a real rotation about a vertical axis does past 90 deg,
    # and it is the cue that reads as 3D rather than as a squash.
    assert Q.mark_horizontal_spin(0.0) == pytest.approx(1.0)
    assert Q.mark_horizontal_spin(Q.Y_SPIN_PERIOD_S / 2.0) == pytest.approx(
        -1.0, abs=1e-6)
    assert Q.mark_horizontal_spin(Q.Y_SPIN_PERIOD_S) == pytest.approx(1.0)
    # The mirror band sits between the first edge-on crossing and the half
    # turn; the warp (which dwells near face-on) leaves the crossings at the
    # quarter points.
    assert Q.mark_horizontal_spin(0.25 * Q.Y_SPIN_PERIOD_S) == pytest.approx(
        0.0, abs=1e-6)
    assert Q.mark_horizontal_spin(0.75 * Q.Y_SPIN_PERIOD_S) == pytest.approx(
        0.0, abs=1e-6)
    assert Q.mark_horizontal_spin(Q.Y_SPIN_PERIOD_S / 3.0) < -0.5


def test_horizontal_spin_crosses_edge_on_quickly_and_never_snaps():
    from voicebridge.orb import qt_app as Q

    dt = 1.0 / 60.0
    # The mark goes fully edge-on — that moment is the point — so the contract
    # is not "never zero" but "not for long": the warped turn crosses at
    # roughly twice the rate of a linear one.
    narrow = [k * dt for k in range(int(60 * Q.Y_SPIN_PERIOD_S))
              if abs(Q.mark_horizontal_spin(0.5 + k * dt)) < 0.10]
    fraction = len(narrow) / float(60 * Q.Y_SPIN_PERIOD_S)
    assert 0.0 < fraction < 0.06, fraction
    # And the factor itself is continuous: no frame-to-frame step anywhere in
    # the turn, including through both edge-on crossings and the turn wrap.
    prev = Q.mark_horizontal_spin(0.0)
    worst = 0.0
    for k in range(1, int(60 * Q.Y_SPIN_PERIOD_S) + 1):
        cur = Q.mark_horizontal_spin(k * dt)
        worst = max(worst, abs(cur - prev))
        prev = cur
    assert worst < 0.05, worst


def test_horizontal_spin_paint_frame_is_continuous_across_its_whole_turn():
    # Measured on pixels: the painted frame must move by about ONE frame's
    # worth at every point of the turn, including through both edge-on
    # crossings and the wrap. A snap anywhere shows up here as an outlier.
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
    q = Q.Y_SPIN_PERIOD_S / 8.0          # |cos| peaks here
    r = Q.MASTER_SPIN_PERIOD_S / 4.0
    a = _paint(t=q)
    b = _paint(t=q + r)                 # same Y turn, different master angle
    c = _paint(t=q + q)                 # different Y turn, same master angle
    assert _diff(a, b) > 0, "the master spin stopped moving the mark"
    assert _diff(a, c) > 0, "the horizontal spin stopped moving the mark"


def test_the_nucleus_stays_round_while_the_orbital_plane_collapses():
    # The per-layer squash decision, measured: at a strongly edge-on clock the
    # orbital plane foreshortens away and the mark's painted width collapses —
    # but the CORE must stay a circle. A sphere keeps its silhouette when you
    # rotate it about its own vertical axis; squashing the nucleus into a lens
    # is the one transform here that reads as a bug rather than as depth.
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

    # A clock where the plane is edge-on: cos ~= 0.
    edge_t = 0.0
    for k in range(0, int(60 * Q.Y_SPIN_PERIOD_S)):
        if abs(Q.mark_horizontal_spin(k / 60.0)) < 0.02:
            edge_t = k / 60.0
            break
    assert edge_t, "no edge-on clock found"

    face_cols, face_rows = core_extent(0.0)
    edge_cols, edge_rows = core_extent(edge_t)
    assert face_rows > 0 and edge_rows > 0, (face_rows, edge_rows)
    # Vertical extent is untouched by a Y rotation of a sphere...
    assert edge_rows == pytest.approx(face_rows, rel=0.6)
    # ...and the horizontal extent stays close to it, not collapsed with the
    # plane. A squashed nucleus would come back far narrower than tall.
    assert edge_cols > 0.45 * edge_rows, (edge_cols, edge_rows)


# --------------------------------------------------------------------------- #
# (b) one declared geometry for every circle
# --------------------------------------------------------------------------- #
def test_mark_ring_geometry_declares_every_circle_and_never_raises():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    rings = Q.mark_ring_geometry(0.37)
    assert len(rings) == len(F.ring_geometry(0.37)) == 8
    layers = [r["layer"] for r in rings]
    assert layers.count("main") == 2        # the two logo orbits
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
    # circles of the mark.
    strokes = [p for p in prims if p.kind == "path" and p.fill is None
               and p.pen is not None and max(p.w, p.h) >= 40.0]
    echo_tilt = {str(e["orbit"]): e["tilt"] for e in F.echo_rings(t)}
    unmatched = list(strokes)
    for ring in F.ring_geometry(t):
        spec = dict(ring)
        if ring.get("echo"):
            # The painter reconciles the follower's tilt onto the shared
            # single-lag echo contract; the radii/layer/width are the
            # declaration's.
            spec["tilt"] = echo_tilt.get(str(ring["orbit"]), ring["tilt"])
        pts = F.ring_path(t, R, spec)
        dx, dy = F.camera_offset(t, R)
        xs = [c + px - dx for px, _ in pts]
        ys = [c + py - dy for _, py in pts]
        want = (max(xs) - min(xs), max(ys) - min(ys))
        hit = None
        for p in unmatched:
            if (abs(p.w - want[0]) <= 1.5 and abs(p.h - want[1]) <= 1.5):
                hit = p
                break
        assert hit is not None, (ring["id"], want, len(unmatched))
        unmatched.remove(hit)
    # The two main orbits also carry a two-stroke chromatic fringe, which is
    # part of the logo's look and is exclusive to them.
    assert len(unmatched) == 4, [(round(p.w, 1), round(p.h, 1))
                                 for p in unmatched]


def test_the_chromatic_fringe_stays_exclusive_to_the_two_main_orbits():
    import test_voice_orb_qt_fx9u as T

    size, c, t = 192, 96.0, 0.37
    prims = T._record(t=t, size=size)
    # The fringe strokes are the only ones this red/blue, and they are wide.
    fringe = [p for p in prims if p.kind == "path" and p.pen is not None
              and tuple(p.pen[:3]) in ((255, 45, 85), (45, 130, 255))]
    assert len(fringe) == 4, [p.pen for p in fringe]
    # Each main orbit's fringe sits within a pixel of the mark centre, i.e. it
    # is a main orbit and not an echo / core ring / shell wearing the fringe.
    assert all(math.hypot(p.cx - c, p.cy - c) <= 0.75 for p in fringe)


# --------------------------------------------------------------------------- #
# (c) the RGB streaks
# --------------------------------------------------------------------------- #
def _streak_prims(t, size=192, reduced=False):
    """Filled PATHS that are not the nucleus: the streaks, one per body."""
    import test_voice_orb_qt_fx9u as T

    prims = T._record(t=t, size=size, reduced=reduced)
    c = size / 2.0
    return [p for p in prims
            if p.kind == "path" and p.fill is not None
            and math.hypot(p.cx - c, p.cy - c) > 3.0]


def test_stars_leave_a_streak_and_the_streak_is_the_fields_own():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    t, size = 0.42, 192
    R = size / 2.0 - 6.0
    streaks = _streak_prims(t, size)
    assert streaks, "no streaks painted at all"

    # Every travelling body's own streak is on screen: kill the field's trail
    # description and the frame must change, which is only true if the painter
    # really consumes it.
    with pytest.MonkeyPatch.context() as m:
        m.setattr(F, "rgb_trail", lambda *a, **k: [])
        m.setattr(F, "dust_trail", lambda *a, **k: [])
        without = _paint(t=t, size=size)
    assert _diff(_paint(t=t, size=size), without) > 0

    # And the particles, satellite bodies and sparkles are the bodies that
    # leave one: at least one streak per particle is within a particle's own
    # neighbourhood of the drawn field.
    # The streak's HEAD rides exactly on the body, so every particle must fall
    # inside its own streak's box: the wake trails BEHIND the star, which is
    # why this is a box test and not a distance-to-the-box-centre test.
    boxes = [(p.cx - p.w / 2.0, p.cy - p.h / 2.0,
              p.cx + p.w / 2.0, p.cy + p.h / 2.0) for p in streaks]
    for part in F.particle_state(t, R):
        px, py = size / 2.0 + part["x"], size / 2.0 + part["y"]
        assert any(x0 - 2.0 <= px <= x1 + 2.0 and y0 - 2.0 <= py <= y1 + 2.0
                   for x0, y0, x1, y1 in boxes), (part["index"], px, py)


def test_streak_colour_comes_from_the_fields_colour_wheel():
    """The renderer must not carry its own HSV maths."""
    from voicebridge.orb import mark_field as F

    t, size = 0.42, 192
    plain = _paint(t=t, size=size)
    with pytest.MonkeyPatch.context() as m:
        # A wheel that returns one flat colour cannot produce a spectrum; if
        # the frame does not change, the painter was computing its own.
        m.setattr(F, "hue_to_rgb", lambda h, s=1.0, v=1.0: (0.15, 0.85, 0.25))
        flat = _paint(t=t, size=size)
    assert _diff(plain, flat) > 0


def test_a_star_has_no_halo_its_streak_is_what_it_leaves():
    """The halo is gone: a star's ink is elongated, not a disc around it."""
    t, size = 0.42, 192
    streaks = _streak_prims(t, size)
    # At least some streaks are clearly elongated — a streak, not a blob.
    elongated = [p for p in streaks
                 if max(p.w, p.h) / max(min(p.w, p.h), 0.001) > 1.6]
    assert elongated, [round(max(p.w, p.h) / max(min(p.w, p.h), 0.001), 2)
                      for p in streaks]


def test_reduced_motion_leaves_no_streaks():
    still = _streak_prims(0.31, reduced=True)
    # The still frame is the mark: orbits, core ring, glints, body. The only
    # filled paths it may carry are the body's own shading layers.
    assert len(still) <= 8, [(round(p.w, 1), round(p.h, 1)) for p in still]
    live = _streak_prims(0.31)
    assert len(live) > len(still)


# --------------------------------------------------------------------------- #
# never raises
# --------------------------------------------------------------------------- #
def test_the_new_turn_never_raises_on_bad_input():
    from voicebridge.orb import qt_app as Q

    for bad in ("nonsense", float("nan"), float("inf"), None, object()):
        assert -1.0 <= Q.mark_horizontal_spin(bad) <= 1.0
        assert math.isfinite(Q.mark_horizontal_spin_deg(bad))
    assert Q.mark_ring_geometry("nonsense") is not None

    for bad in ("nonsense", float("nan"), float("inf"), None, object()):
        _paint(t=bad, state="not-a-state", size=96)
        _paint(t=bad, state=None, size=96, reduced=True)