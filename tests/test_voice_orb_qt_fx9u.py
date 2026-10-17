"""Qt mark life effects: heartbeat, depth, trails, dust, comet, shockwave.

Hermetic (offscreen QImage, no window, no X server). Pins the FX contract the
Tk sibling mirrors: the nucleus beats a lub-dub, the shared animation field
(``voicebridge.orb.mark_field``) provides a dense z-split particle field with
dust, glints and a comet, its trails taper, a thin shockwave expands per beat,
and reduced motion is a byte-identical still frame no matter the clock.

The richer assertions inspect the *recorded draw calls* (a forwarding painter
proxy), so they fail when the renderer silently drops a layer, not merely when
the numbers in this module change.
"""
from __future__ import annotations

import dataclasses
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


@pytest.fixture(autouse=True)
def _reduced_motion_off():
    """Default every test to full motion; individual tests opt in."""
    key = "QUBIT_REDUCED_MOTION"
    prior = os.environ.pop(key, None)
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


def _paint(t=1.2, state="idle", family=None, brightness=1.0, size=192,
           reduced=False):
    _qt_app()
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    if family is None:
        family = E.family_from_core(E.state_core(state))
    key = "QUBIT_REDUCED_MOTION"
    if reduced:
        os.environ[key] = "1"
    else:
        os.environ.pop(key, None)
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(*BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    Q._paint_mark(p, size / 2.0, size / 2.0, size / 2.0 - 6.0, t, state,
                  brightness, family)
    p.end()
    return img


def _bytes(img) -> bytes:
    return bytes(img.constBits())


def _diff(a, b) -> int:
    return sum(1 for x, y in zip(_bytes(a), _bytes(b)) if x != y)


def _central_ink(img) -> int:
    """Non-background pixels in a small box around the centre (nucleus only)."""
    from PySide6.QtGui import QColor

    bg = QColor(*BG).rgb()
    c = img.width() // 2
    n = 0
    for y in range(c - 6, c + 7):
        for x in range(c - 6, c + 7):
            if img.pixel(x, y) != bg:
                n += 1
    return n


# --------------------------------------------------------------------------- #
# recorded draw calls
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class Prim:
    kind: str                 # "ellipse" | "path" | "line"
    cx: float
    cy: float
    r: float
    fill: tuple | None        # (r, g, b, a) or None for an outline
    pen: tuple | None         # (r, g, b, a) or None
    width: float = 0.0
    #: Bounding-box size for paths (0 for ellipses/lines). Letting a test
    #: recover an outline's tilt needs w and h separately, not just r.
    w: float = 0.0
    h: float = 0.0

    @property
    def alpha(self) -> float:
        rgba = self.fill or self.pen
        return 0.0 if rgba is None else rgba[3] / 255.0


class _Recorder:
    """Forwarding QPainter proxy: records every op, then paints it for real."""

    def __init__(self, painter) -> None:
        object.__setattr__(self, "_p", painter)
        object.__setattr__(self, "_brush", None)
        object.__setattr__(self, "_pen", None)
        object.__setattr__(self, "prims", [])

    def setBrush(self, brush):
        object.__setattr__(self, "_brush", brush)
        self._p.setBrush(brush)

    def setPen(self, pen):
        object.__setattr__(self, "_pen", pen)
        self._p.setPen(pen)

    def _fill_rgba(self):
        brush = self._brush
        if brush is None:
            return None
        from PySide6.QtCore import Qt
        try:
            if hasattr(brush, "style") and brush.style() == Qt.NoBrush:
                return None
        except Exception:
            pass
        col = brush.color() if hasattr(brush, "color") else brush
        if not hasattr(col, "red"):
            return None
        return (col.red(), col.green(), col.blue(), col.alpha())

    def _pen_rgba(self):
        pen = self._pen
        if pen is None:
            return None
        from PySide6.QtCore import Qt
        try:
            if pen.style() == Qt.NoPen:
                return None
        except Exception:
            pass
        col = pen.color() if hasattr(pen, "color") else pen
        if not hasattr(col, "red"):
            return None
        return (col.red(), col.green(), col.blue(), col.alpha())

    def _width(self) -> float:
        pen = self._pen
        try:
            return float(pen.widthF())
        except Exception:
            return 0.0

    def drawEllipse(self, *args):
        cx = cy = r = 0.0
        try:
            if len(args) == 4:
                x, y, w, h = (float(v) for v in args)
                cx, cy, r = x + w / 2.0, y + h / 2.0, (w + h) / 4.0
            elif len(args) == 1:
                rect = args[0]
                cx, cy = float(rect.center().x()), float(rect.center().y())
                r = (float(rect.width()) + float(rect.height())) / 4.0
        except Exception:
            pass
        self.prims.append(Prim("ellipse", cx, cy, r, self._fill_rgba(),
                               self._pen_rgba(), self._width()))
        self._p.drawEllipse(*args)

    def drawPath(self, path):
        cx = cy = r = w = h = 0.0
        try:
            rect = path.boundingRect()
            cx, cy = float(rect.center().x()), float(rect.center().y())
            w, h = float(rect.width()), float(rect.height())
            r = (w + h) / 4.0
        except Exception:
            pass
        self.prims.append(Prim("path", cx, cy, r, self._fill_rgba(),
                               self._pen_rgba(), self._width(), w, h))
        self._p.drawPath(path)

    def drawLine(self, *args):
        cx = cy = r = 0.0
        try:
            if len(args) == 4:
                x0, y0, x1, y1 = (float(v) for v in args)
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                r = math.hypot(x1 - x0, y1 - y0) / 2.0
            elif len(args) == 2:  # drawLine(QPointF, QPointF), as _glint uses
                x0, y0 = float(args[0].x()), float(args[0].y())
                x1, y1 = float(args[1].x()), float(args[1].y())
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                r = math.hypot(x1 - x0, y1 - y0) / 2.0
        except Exception:
            pass
        self.prims.append(Prim("line", cx, cy, r, self._fill_rgba(),
                               self._pen_rgba(), self._width()))
        self._p.drawLine(*args)

    def __getattr__(self, name):
        return getattr(self._p, name)


def _record(t=1.2, state="idle", family=None, brightness=1.0, size=192,
            reduced=False):
    _qt_app()
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    if family is None:
        family = E.family_from_core(E.state_core(state))
    key = "QUBIT_REDUCED_MOTION"
    if reduced:
        os.environ[key] = "1"
    else:
        os.environ.pop(key, None)
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(*BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    rec = _Recorder(p)
    Q._paint_mark(rec, size / 2.0, size / 2.0, size / 2.0 - 6.0, t, state,
                  brightness, family)
    p.end()
    return tuple(rec.prims)


def _dump(prims):
    return tuple((p.kind, round(p.cx, 5), round(p.cy, 5), round(p.r, 5),
                  p.fill, p.pen) for p in prims)


def _at(prims, x, y, tol=0.75, filled=True):
    hits = []
    for p in prims:
        if filled and p.fill is None:
            continue
        if math.hypot(p.cx - x, p.cy - y) <= tol:
            hits.append(p)
    return hits


# -- (a) heartbeat ----------------------------------------------------------

def test_heartbeat_envelope_double_thump_and_period():
    from voicebridge.orb import qt_app as Q

    assert Q.HEART_PERIOD_S == pytest.approx(0.95)
    # Primary thump at the cycle start, second at 22% in, at 0.62 amplitude.
    assert Q.heartbeat_envelope(0.0) > 0.99
    assert Q.heartbeat_envelope(Q.HEART_SECOND_PHASE * Q.HEART_PERIOD_S) == (
        pytest.approx(Q.HEART_SECOND_AMP, abs=0.02))
    # Quiet between the thumps (0.16s wide, so 0.10 is only partially lit).
    assert Q.heartbeat_envelope(0.40) < 0.05
    # Repeats every period, phase-locked.
    for k in range(1, 5):
        assert Q.heartbeat_envelope(k * Q.HEART_PERIOD_S) == pytest.approx(1.0)
        assert Q.heartbeat_envelope(k * Q.HEART_PERIOD_S + 0.10) == pytest.approx(
            Q.heartbeat_envelope(0.10), abs=1e-9)


def test_heartbeat_changes_nucleus_size_and_alpha_across_phases():
    peak = _paint(0.0)     # beat envelope == 1
    quiet = _paint(0.5)    # beat envelope == 0
    assert _diff(peak, quiet) > 0
    # Bigger at the beat peak; the nucleus itself stays full alpha (crisp).
    assert _central_ink(peak) > _central_ink(quiet)
    assert peak.pixelColor(96, 96) == quiet.pixelColor(96, 96)


def test_nucleus_radius_follows_field_nucleus_scale():
    from voicebridge.orb import mark_field as F

    size, R = 192, 90.0
    for t in (0.0, 0.07, 0.5):
        prims = _record(t=t, size=size)
        nuc = _at(prims, size / 2.0, size / 2.0, tol=0.75)
        nuc = [p for p in nuc if p.kind == "ellipse" and p.fill is not None]
        assert len(nuc) == 1, (t, nuc)
        expect = R * F.NUCLEUS_FRAC * F.nucleus_scale(t)
        assert nuc[0].r == pytest.approx(expect, rel=0.03)
        assert nuc[0].fill[3] == 255


# -- (b) depth / far-near z-order ------------------------------------------

def test_bead_layers_flip_between_far_and_near_halves():
    from voicebridge.orb import qt_app as Q

    a = {i: layer for i, layer, _ang, _d in Q.mark_particle_layers(0.0)}
    b = {i: layer for i, layer, _ang, _d in Q.mark_particle_layers(1.1)}
    assert set(a.values()) == {"far", "near"}
    assert set(b.values()) == {"far", "near"}
    assert any(a[i] != b[i] for i in a), (a, b)


def test_depth_derives_size_alpha_and_parallax_numbers():
    from voicebridge.orb import qt_app as Q

    # depth 1 (nearest) -> 1.30x radius / alpha 1.00; depth 0 -> the floor.
    assert Q.DEPTH_RADIUS_MIN + Q.DEPTH_RADIUS_GAIN == pytest.approx(1.30)
    assert Q.DEPTH_ALPHA_MIN + Q.DEPTH_ALPHA_GAIN == pytest.approx(1.00)
    assert Q.DEPTH_RADIUS_MIN == pytest.approx(0.55)
    assert Q.DEPTH_ALPHA_MIN == pytest.approx(0.35)
    assert Q.DEPTH_LIFT_MIN + Q.DEPTH_LIFT_GAIN * 1.0 == pytest.approx(0.35)
    assert Q.DEPTH_PARALLAX == pytest.approx(0.06)


def test_far_elements_draw_before_nucleus_and_near_after():
    from voicebridge.orb import mark_field as F

    size, R, c = 192, 90.0, 96.0
    t = 0.37
    prims = _record(t=t, size=size)

    nucleus = [i for i, p in enumerate(prims)
               if p.kind == "ellipse" and p.fill is not None
               and math.hypot(p.cx - c, p.cy - c) <= 0.75]
    assert len(nucleus) == 1
    seam = nucleus[0]

    # Particles are GLINTS now: each is two line arms plus a small filled
    # core, so match a particle by its core ellipse (any kind of fill near the
    # particle centre) rather than by a bead-sized disc.
    parts = F.particle_state(t, R)
    far_idx, near_idx = [], []
    for i, p in enumerate(prims):
        if p.fill is None or i == seam:
            continue
        for part in parts:
            px, py = c + part["x"], c + part["y"]
            if math.hypot(p.cx - px, p.cy - py) <= 0.6:
                (far_idx if part["depth"] < 0.5 else near_idx).append(i)
                break

    assert len(far_idx) >= 3 and len(near_idx) >= 3, (far_idx, near_idx)
    assert max(far_idx) < seam, (far_idx, seam)
    assert min(near_idx) > seam, (near_idx, seam)


# -- (c) shockwave ----------------------------------------------------------

def test_shockwave_ring_appears_near_beat_and_not_between():
    from voicebridge.orb import mark_field as F

    size, R, c = 192, 90.0, 96.0
    beat_t, gap_t = 0.07, 0.5
    scale, alpha = F.shockwave(beat_t)
    assert alpha > 0.0 and 1.0 <= scale < F.SHOCKWAVE_MAX_SCALE
    assert F.shockwave(gap_t) == (0.0, 0.0)

    def shock_rings(t):
        prims = _record(t=t, size=size)
        nuc_r = R * F.NUCLEUS_FRAC * F.nucleus_scale(t)
        return [p for p in prims
                if p.kind == "path" and p.fill is None and p.pen is not None
                and math.hypot(p.cx - c, p.cy - c) <= 0.75
                and p.r <= nuc_r * 1.45]

    rings = shock_rings(beat_t)
    assert len(rings) == 1, rings
    assert rings[0].alpha == pytest.approx(alpha, abs=0.01)
    assert rings[0].width <= 1.5
    assert shock_rings(gap_t) == []


# -- (d) dust / comet -------------------------------------------------------

def test_dust_and_comet_paint_and_vanish_under_reduced_motion():
    from voicebridge.orb import mark_field as F

    size, R, c = 192, 90.0, 96.0
    t = 4.2  # inside the comet's sweep window (0.45..1.0 of a 7s period)
    traveller = F.comet(t, R)
    assert traveller["alpha"] > 0.2 and traveller["trail"]

    prims = _record(t=t, size=size)
    head = _at(prims, c + traveller["x"], c + traveller["y"], tol=0.6)
    assert head and max(p.alpha for p in head) > 0.05

    tail_hits = sum(
        1 for smp in traveller["trail"]
        if smp["alpha"] > 0.02
        and _at(prims, c + smp["x"], c + smp["y"], tol=0.6))
    assert tail_hits >= 6, tail_hits

    dust = F.dust_state(t, R)
    dust_hits = sum(
        1 for mote in dust
        if _at(prims, c + mote["x"], c + mote["y"], tol=0.5))
    assert dust_hits >= len(dust) - 1, (dust_hits, len(dust))

    # Reduced motion: the field clock freezes at 0, so compare against t=0.
    still = _record(t=t, size=size, reduced=True)
    assert F.comet(0.0, R)["alpha"] == 0.0
    dust0 = F.dust_state(0.0, R)
    assert not any(_at(still, c + mote["x"], c + mote["y"], tol=0.5)
                   for mote in dust0)
    # No trails/glints/comet either: a clean static mark plus the nucleus.
    # A clean static mark: orbits (3 strokes x2) + glints (2 arms +
    # core each) + nucleus + halo, and nothing that only lives on the
    # moving clock. Bounded relative to the live frame, not a magic
    # total.
    assert len(still) < len(prims), (len(still), len(prims))


# -- (e) sparkles -----------------------------------------------------------

def test_sparkles_appear_and_vanish_deterministically():
    from voicebridge.orb import qt_app as Q

    first = Q.mark_sparkles(0.0)
    assert 0 < len(first) <= Q.SPARKLE_COUNT
    for _i, _r, _ang, phase, fade, size, alpha in first:
        assert 0.0 <= phase < Q.SPARKLE_VISIBLE_FRAC
        assert 0.0 <= fade <= 1.0
        assert 0.0 < size <= Q.SPARKLE_SIZE_BASE + Q.SPARKLE_SIZE_PEAK + 1e-9
        assert 0.0 < alpha <= Q.SPARKLE_MAX_ALPHA + 1e-9
    # Reproducible from t alone.
    assert [tuple(s) for s in first] == [tuple(s) for s in Q.mark_sparkles(0.0)]
    # There are phases with glints and phases with none.
    seen = [len(Q.mark_sparkles(k / 100.0)) for k in range(0, 400)]
    assert any(n > 0 for n in seen)
    assert any(n == 0 for n in seen)


def test_field_glints_actually_paint_pixels(monkeypatch):
    from voicebridge.orb import qt_app as Q

    with_spark = _paint(0.0)
    with monkeypatch.context() as m:
        m.setattr(Q._field, "sparkles", lambda t, r: [])
        without = _paint(0.0)
    assert _diff(with_spark, without) > 0


# -- (f) primitive budget ---------------------------------------------------

def test_primitive_count_is_bounded_and_richer_than_pre_field():
    # Pre-field ceiling: 2 rings * 3 fringe strokes + 9 beads + 9*4 trails
    # + 14 glints + nucleus + halo = 67 primitives.
    pre_field_max = 2 * 3 + 9 + 9 * 4 + 14 + 2
    counts = [len(_record(t=0.31 + k * 0.17)) for k in range(10)]
    assert max(counts) > pre_field_max
    assert max(counts) <= 300
    assert max(counts) - min(counts) < 60


# -- (g) reduced motion is a still frame -----------------------------------

def test_reduced_motion_is_byte_identical_across_t():
    a = _paint(1.0, reduced=True)
    b = _paint(37.4, reduced=True)
    assert _bytes(a) == _bytes(b)


def test_reduced_motion_recorded_frame_is_identical_at_two_clocks():
    from voicebridge.orb import qt_app as Q

    a = _record(t=0.31, reduced=True)
    b = _record(t=3.77, reduced=True)
    assert _dump(a) == _dump(b)
    assert len(a) > 0
    # The legacy helpers freeze too while the switch is on.
    os.environ["QUBIT_REDUCED_MOTION"] = "1"
    try:
        assert Q.mark_sparkles(1.0) == []
        assert Q.mark_particle_layers(1.0) == Q.mark_particle_layers(99.0)
    finally:
        os.environ.pop("QUBIT_REDUCED_MOTION", None)


def test_reduced_motion_still_paints_the_mark():
    img = _paint(5.0, reduced=True)
    # Still has orbit lines + nucleus ink, just no life.
    assert _central_ink(img) > 0
    assert _diff(img, _paint(5.0, reduced=True)) == 0


# -- (h) never raises -------------------------------------------------------

def test_mark_effects_never_raise_on_bad_input():
    _paint(t="nonsense", state="not-a-state", family={}, brightness=0.0)
    _paint(t=float("nan"), state=None, family={"core": (-5, 300, 20)},
           brightness=-3.0)
    _paint(t=1e9, state="listening", family={})
    _paint(t=0.0, state="error", family={"core": "nope"}, brightness=99.0)
    _paint(t=float("inf"), state=object(), family={"core": None},
           brightness=float("nan"))


def test_mark_effects_never_raise_under_reduced_motion():
    _paint(t="x", state="???", family={}, brightness=0.0, reduced=True)
    _record(t="x", state="???", family={}, brightness=0.0, reduced=True)


# --------------------------------------------------------------------------- #
# (i) echo follower rings
# --------------------------------------------------------------------------- #
def _thin_orbit_outlines(prims, c):
    """Centred outline paths that are neither the main stroke nor a circle."""
    return [p for p in prims
            if p.kind == "path" and p.fill is None and p.pen is not None
            and math.hypot(p.cx - c, p.cy - c) <= 0.75
            and p.width <= 1.2 and 0.2 <= p.alpha <= 0.45
            and abs(p.w - p.h) > 4.0]


def _tilt_abs_deg(prim, rxs, rys):
    """Recover |tilt| of a rotated ellipse from its bounding-box width.

    A rotated ellipse has ``w^2/4 = rxs^2 cos^2 + rys^2 sin^2`` for rx > ry,
    so ``sin^2`` (and thus |tilt|) follows from the recorded width.
    """
    s2 = (rxs * rxs - (prim.w * prim.w) / 4.0) / (rxs * rxs - rys * rys)
    s2 = max(0.0, min(1.0, s2))
    return math.degrees(math.asin(math.sqrt(s2)))


def test_echo_rings_are_two_thin_lagged_followers():
    from voicebridge.orb import mark_field as F

    size, c = 192, 96.0
    R = size / 2.0 - 6.0
    t = 0.37
    prims = _record(t=t, size=size)

    thin = _thin_orbit_outlines(prims, c)
    assert len(thin) == 2, thin                 # exactly one per main orbit
    rx, ry = R * F.RX_FRAC, R * F.RY_FRAC
    rxs, rys = rx * F.ECHO_SCALE, ry * F.ECHO_SCALE
    got = sorted(_tilt_abs_deg(p, rxs, rys) for p in thin)
    expected = sorted(abs(e["tilt"]) for e in F.echo_rings(t))
    assert got == pytest.approx(expected, abs=2.0), (got, expected)

    # The lag matters: the follower tilt is the orbit tilt ECHO_LAG_S ago,
    # not today's, so at this phase the two disagree by a visible margin.
    unl = sorted(abs(v) for v in F.orbit_tilts(t))
    assert max(abs(a - b) for a, b in zip(got, unl)) > 0.5, (got, unl)


def test_echo_rings_follow_tilt_changes_continuously():
    from voicebridge.orb import mark_field as F

    # The follower tilt is a continuous function of the render clock, so a
    # 1/60s step can never jump: this is what keeps the spin fluid.
    prev = None
    for k in range(120):
        e = F.echo_rings(k / 60.0)
        cur = [ring["tilt"] for ring in e]
        if prev is not None:
            assert max(abs(a - b) for a, b in zip(cur, prev)) < 1.0
        prev = cur


# --------------------------------------------------------------------------- #
# (j) nucleus spark shower
# --------------------------------------------------------------------------- #
def test_nucleus_sparks_are_a_deterministic_outward_shower():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    R = 90.0
    # A shower exists at a primary thump and is frame-reproducible.
    at_thump = Q.mark_nucleus_sparks(0.0, R)
    assert 0 < len(at_thump) <= F.NUCLEUS_SPARK_COUNT
    assert at_thump == Q.mark_nucleus_sparks(0.0, R)

    for t in (0.0, 0.4, 1.2, 2.7):
        sparks = Q.mark_nucleus_sparks(t, R)
        dx, dy = F.camera_offset(t, R)
        for s in sparks:
            # Recover phase from alpha (alpha = SPARK_ALPHA * (1-phase)^1.5).
            phase = 1.0 - (s["alpha"] / F.NUCLEUS_SPARK_ALPHA) ** (2.0 / 3.0)
            assert 0.0 <= phase < 1.0
            grow = phase ** 0.65
            dist = F.NUCLEUS_SPARK_REACH * R * grow
            # Radial distance is governed by phase -- the spark travels OUT
            # of the nucleus -- within the squashed-plane factor and the
            # small camera offset.
            d = math.hypot(s["x"] - dx, s["y"] - dy)
            assert 0.66 * dist - 1.5 <= d <= 1.05 * dist + 1.5, (t, s, d, dist)
            # Shrinks and fades with phase: both bounded by the constants.
            assert 0.30 - 1e-9 <= s["size"] <= \
                F.NUCLEUS_SPARK_SIZE * (R / 32.0) + 1e-9
            assert 0.0 < s["alpha"] <= F.NUCLEUS_SPARK_ALPHA + 1e-9

    # Bigger (younger) sparks are brighter: size and alpha fall together.
    sparks = Q.mark_nucleus_sparks(1.2, R)
    order = sorted(sparks, key=lambda s: s["size"])
    agree = sum(1 for a, b in zip(order, order[1:])
                if b["alpha"] >= a["alpha"])
    assert agree >= len(order) - 1, order

    # The shower reaches OUTSIDE the near orbit at its peak.
    peak = max(max((math.hypot(s["x"], s["y"])
                    for s in Q.mark_nucleus_sparks(k / 60.0, R)), default=0.0)
               for k in range(600))
    assert peak > R * F.RX_FRAC * 1.2, peak


def test_nucleus_sparks_paint_glints():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, c = 192, 96.0
    R = size / 2.0 - 6.0
    t = 0.4
    prims = _record(t=t, size=size)
    sparks = Q.mark_nucleus_sparks(t, R)
    assert sparks
    for s in sparks:
        px, py = c + s["x"], c + s["y"]
        arms = [p for p in prims if p.kind == "line"
                and math.hypot(p.cx - px, p.cy - py) <= 0.6]
        core = [p for p in prims if p.kind == "ellipse" and p.fill is not None
                and math.hypot(p.cx - px, p.cy - py) <= 0.6]
        assert len(arms) == 2, (s, arms)   # two crossing arms
        assert len(core) == 1, (s, core)   # plus a small core


# --------------------------------------------------------------------------- #
# (k) reduced motion drops the new life too
# --------------------------------------------------------------------------- #
def test_reduced_motion_has_no_echo_or_sparks(monkeypatch):
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    with monkeypatch.context() as m:
        m.setenv("QUBIT_REDUCED_MOTION", "1")
        assert Q.mark_echo_rings(0.31) == []
        assert Q.mark_nucleus_sparks(0.31, 90.0) == []
        assert Q.mark_sparkles(0.31) == []

    # Painting a still frame must not even consult the moving-field layers.
    calls = []
    for name in ("echo_rings", "nucleus_sparks", "dust_state", "sparkles",
                 "comet", "trails"):
        orig = getattr(F, name)

        def _spy(*args, _n=name, _o=orig, **kwargs):
            calls.append(_n)
            return _o(*args, **kwargs)

        monkeypatch.setattr(F, name, _spy)

    a = _record(t=0.31, reduced=True)
    b = _record(t=3.77, reduced=True)
    assert calls == [], calls
    assert _dump(a) == _dump(b)
    # No thin follower outline can survive into the still frame.
    assert _thin_orbit_outlines(a, 96.0) == []


# --------------------------------------------------------------------------- #
# (l) continuity: the "fluid" contract
# --------------------------------------------------------------------------- #
#: Largest allowed position change of any entity between two adjacent 60fps
#: frames, in pixels, at R=90. The fastest legitimate motion is a fresh spark
#: leaving the nucleus and the comet head sweeping its orbit: both measured
#: <= ~5.1 px/frame here, so 6 px fails any real pop while leaving headroom.
CONTINUITY_EPS_PX = 6.0
CONTINUITY_DT = 1.0 / 60.0


def _stable_positions(t, R):
    """Per-identity positions of every continuously-lived layer at ``t``."""
    from voicebridge.orb import mark_field as F

    d = {}
    for i, p in enumerate(F.particle_state(t, R)):
        d[("particle", i)] = (p["x"], p["y"])
    for i, m in enumerate(F.dust_state(t, R)):
        d[("dust", i)] = (m["x"], m["y"])
    # Echo-ring tilt continuity is asserted in its own test (degrees, not px).
    comet = F.comet(t, R)
    if comet.get("alpha", 0.0) > 0.0 and "x" in comet:
        d[("comet", "head")] = (comet["x"], comet["y"])
        for k, smp in enumerate(comet.get("trail") or []):
            d[("comet", k)] = (smp["x"], smp["y"])
    for i, p in enumerate(F.particle_state(t, R)):
        for k, smp in enumerate(F.trails(t, R, p)):
            d[("trail", i, k)] = (smp["x"], smp["y"])
    d[("nucleus", "r")] = (F.NUCLEUS_FRAC * R * F.nucleus_scale(t), 0.0)
    return d


def _spark_positions(t, R):
    from voicebridge.orb import mark_field as F

    return [(s["x"], s["y"]) for s in F.nucleus_sparks(t, R)]


def _mutual_deltas(prev, cur):
    """Distances for sparks that are each other's nearest neighbour.

    The field does not carry a spark identity across frames (a spark is born
    at the nucleus and culled when it fades), so an identity-based delta is
    impossible; mutual-nearest matching reports the true within-life motion
    and leaves births/deaths unmatched.
    """
    out = []
    for ci, c in enumerate(cur):
        bi = min(range(len(prev)),
                 key=lambda j: math.hypot(c[0] - prev[j][0], c[1] - prev[j][1]))
        cur_near = min(range(len(cur)),
                       key=lambda j: math.hypot(prev[bi][0] - cur[j][0],
                                                prev[bi][1] - cur[j][1]))
        if cur_near == ci:
            out.append(math.hypot(c[0] - prev[bi][0], c[1] - prev[bi][1]))
    return out


def test_motion_is_frame_to_frame_continuous():
    from voicebridge.orb import mark_field as F

    R = 90.0
    # Start at 3.0s so the 60-frame window also crosses the comet's fade-in
    # boundary (phase 0.45 of its 7s period) and a heartbeat wrap.
    t0 = 3.0
    worst = 0.0
    worst_key = None
    unmatched_max = 0
    prev = _stable_positions(t0, R)
    prev_sparks = _spark_positions(t0, R)
    for k in range(1, 60):
        t = t0 + k * CONTINUITY_DT
        cur = _stable_positions(t, R)
        for key, pos in cur.items():
            if key not in prev:
                continue
            dd = math.hypot(pos[0] - prev[key][0], pos[1] - prev[key][1])
            if dd > worst:
                worst, worst_key = dd, key
        sparks = _spark_positions(t, R)
        matched = _mutual_deltas(prev_sparks, sparks)
        unmatched_max = max(unmatched_max, len(sparks) - len(matched))
        for dd in matched:
            if dd > worst:
                worst, worst_key = dd, "spark"
        prev, prev_sparks = cur, sparks

    assert unmatched_max <= 3, unmatched_max
    assert worst <= CONTINUITY_EPS_PX, (worst, worst_key)
    # And the contract is not vacuous: something really did move.
    assert worst > 0.5, worst
