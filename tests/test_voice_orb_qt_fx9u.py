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
import contextlib
import math
import os
import sys
from collections import Counter
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


@contextlib.contextmanager
def monkeypatch_env(key, value):
    """Scoped env override (the fixture above only guarantees the default)."""
    prior = os.environ.get(key)
    os.environ[key] = value
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
    #: Glyph drawn by ``drawText`` (empty for every other kind).
    text: str = ""

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

    def drawText(self, *args):
        cx = cy = 0.0
        text = ""
        try:
            if len(args) >= 2 and hasattr(args[0], "x"):
                pt, text = args[0], args[1]
                cx, cy = float(pt.x()), float(pt.y())
        except Exception:
            pass
        try:
            self.prims.append(Prim("text", cx, cy, 0.0,
                                   self._pen_rgba() or self._fill_rgba(),
                                   None, 0.0, 0.0, 0.0, str(text)))
        except Exception:
            pass
        self._p.drawText(*args)

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

    # Bigger at the beat peak. The nucleus now carries a wide glow that
    # fills the small central box at both phases, so compare the recorded
    # body radius directly instead of central-ink saturation.
    def _body_r(t):
        prims = _record(t=t, size=192)
        nuc = [p for p in _at(prims, 96.0, 96.0, tol=0.75)
               if p.kind == "ellipse" and p.fill is not None]
        assert len(nuc) == 1, nuc
        return nuc[0].r

    assert _body_r(0.0) > _body_r(0.5)
    # The nucleus itself stays full alpha (crisp). A radial gradient varies
    # the colour with radius, so it is the opacity at the centre that is
    # pinned here, not one exact hue.
    assert peak.pixelColor(96, 96).alpha() == 255
    assert quiet.pixelColor(96, 96).alpha() == 255
    assert _central_ink(peak) > 0 and _central_ink(quiet) > 0


def test_nucleus_radius_follows_field_nucleus_scale():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, R = 192, 90.0
    for t in (0.0, 0.07, 0.5):
        prims = _record(t=t, size=size)
        nuc = _at(prims, size / 2.0, size / 2.0, tol=0.75)
        nuc = [p for p in nuc if p.kind == "ellipse" and p.fill is not None]
        assert len(nuc) == 1, (t, nuc)
        # The field owns the heartbeat breathing; the studio body is drawn
        # NUCLEUS_RADIUS_MULT times the field's base radius on top of it.
        expect = (R * F.NUCLEUS_FRAC * F.nucleus_scale(t)
                  * Q.NUCLEUS_RADIUS_MULT)
        assert nuc[0].r == pytest.approx(expect, rel=0.03)
        assert nuc[0].fill[3] == 255


def test_studio_nucleus_is_bigger_than_the_field_base_radius():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    # The body is a studio nucleus, not a hair-thin core: measurably larger
    # than the field's own base radius, and it carries extra shading layers
    # (glow, swirl lobes, specular) rather than a single flat disc.
    assert Q.NUCLEUS_RADIUS_MULT > 1.0
    prims = _record(t=0.4, size=size)
    body = [p for p in _at(prims, c, c, tol=0.75)
            if p.kind == "ellipse" and p.fill is not None]
    assert len(body) == 1, body
    assert body[0].r > R * F.NUCLEUS_FRAC * 1.15, body[0].r

    shading = [p for p in prims if p.kind == "path" and p.fill is not None
               and math.hypot(p.cx - c, p.cy - c)
               <= body[0].r * Q.NUCLEUS_GLOW_MULT * 0.5]
    # glow + two counter-rotating swirl lobes + static specular + orbiting one
    assert len(shading) >= 5, len(shading)

    # The orbiting specular actually travels around the body rim: a small
    # highlight out near the body's edge, distinct from the larger static one.
    def specular_pos(t):
        prims = _record(t=t, size=size)
        # The nucleus is the LARGEST centred filled ellipse: with the field's
        # z/z2 volume axes now displacing every element, a stray pinprick can
        # occasionally land inside the 0.75px centre box, so "first centred
        # ellipse" (the old locator) is no longer the body. The body itself is
        # always centred, so taking the largest centred one is correct at every
        # clock.
        nuc = [p for p in _at(prims, c, c, tol=0.75)
               if p.kind == "ellipse" and p.fill is not None]
        assert nuc, t
        nuc_r = max(p.r for p in nuc)
        rim = [p for p in prims if p.kind == "path" and p.fill is not None
               and 0.3 * nuc_r <= math.hypot(p.cx - c, p.cy - c) <= 0.8 * nuc_r
               and p.r <= 0.25 * nuc_r]
        assert rim, (t, nuc_r)
        dot = min(rim, key=lambda p: p.r)
        assert dot.r < 0.25 * nuc_r, (t, dot.r, nuc_r)
        return math.atan2(dot.cy - c, dot.cx - c)

    a, b = specular_pos(0.0), specular_pos(Q.NUCLEUS_SPECULAR_PERIOD_S / 4.0)
    assert abs(math.degrees(b - a)) > 30.0, (a, b)


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
            # The painter places each particle through the mark's X+Y tumble,
            # so the expected pixel is the projected one, not the raw field
            # coordinate. The z-split being asserted is the FIELD's, and the
            # projection takes the hash-broken depth (see ``turn_qdepth``),
            # so the expectation routes through the same function — plus the
            # field's z/z2 volume displacement, exactly as ``_place`` adds it.
            from voicebridge.orb import qt_app as _Q
            qd = _Q.turn_qdepth(part["depth"], part.get("index"), flat=True)
            tx, ty = _turned(t, R, part["x"], part["y"], qd)
            vox, voy = _Q.volume_offset(t, R, part.get("z"), part.get("z2"))
            px, py = c + tx + vox, c + ty + voy
            if math.hypot(p.cx - px, p.cy - py) <= 0.6:
                (far_idx if part["depth"] < 0.5 else near_idx).append(i)
                break

    assert len(far_idx) >= 3 and len(near_idx) >= 3, (far_idx, near_idx)
    assert max(far_idx) < seam, (far_idx, seam)
    assert min(near_idx) > seam, (near_idx, seam)


# -- (c) shockwave ----------------------------------------------------------

def test_shockwave_ring_appears_near_beat_and_not_between():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    beat_t, gap_t = 0.07, 0.5
    scale, alpha = F.shockwave(beat_t)
    assert alpha > 0.0 and 1.0 <= scale < F.SHOCKWAVE_MAX_SCALE
    assert F.shockwave(gap_t) == (0.0, 0.0)

    def shock_rings(t):
        prims = _record(t=t, size=size)
        scale, alpha = F.shockwave(t)
        nuc_r = R * F.NUCLEUS_FRAC * F.nucleus_scale(t) * Q.NUCLEUS_RADIUS_MULT
        # The shockwave is the EXPANDING ring: match its radius (the field's
        # own scale at this clock) and its alpha, not just "a centred
        # outline". The nucleus now carries its own centred strokes — the
        # Fresnel limb at the body edge and the chromatic split — which a
        # bare radius ceiling would sweep in.
        want_r = nuc_r * scale
        return [p for p in prims
                if p.kind == "path" and p.fill is None and p.pen is not None
                and math.hypot(p.cx - c, p.cy - c) <= 0.75
                and abs(p.r - want_r) <= 0.25 * want_r
                and abs(p.alpha - alpha) <= 0.05]

    rings = shock_rings(beat_t)
    assert len(rings) == 1, rings
    assert rings[0].alpha == pytest.approx(alpha, abs=0.01)
    assert rings[0].width <= 1.5
    assert shock_rings(gap_t) == []


# -- (d) dust / comet -------------------------------------------------------

def test_dust_and_comet_paint_and_vanish_under_reduced_motion():
    from voicebridge.orb import mark_field as F

    size, R, c = 192, 90.0, 96.0
    t = 5.0  # mid comet sweep (0.45..1.0 of a 7s period), at full brightness
    traveller = F.comet(t, R)
    assert traveller["alpha"] > 0.2 and traveller["trail"]

    prims = _record(t=t, size=size)
    head = _at(prims, *_pix(t, R, c, traveller), tol=0.6)
    assert head and max(p.alpha for p in head) > 0.05

    tail_hits = sum(
        1 for smp in traveller["trail"]
        if smp["alpha"] > 0.02
        and _at(prims, *_pix(t, R, c, smp, depth=traveller["depth"],
                             zseed=traveller.get("index")), tol=0.6))
    assert tail_hits >= 6, tail_hits

    dust = F.dust_state(t, R)
    dust_hits = sum(1 for mote in dust
                    if _at(prims, *_pix(t, R, c, mote), tol=0.5))
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
    # Count SHAPE primitives only, matching the binding perf contract
    # (``test_voice_orb_fx_perf9u`` counts drawEllipse/drawPath/drawLine, never
    # drawText). The math-symbol and "1"/"0" glyph layers are text, so they are
    # outside the shape budget by the same rule the perf cap uses.
    counts = [len([p for p in _record(t=0.31 + k * 0.17)
                   if p.kind != "text"]) for k in range(10)]
    assert max(counts) > pre_field_max
    # The holo budget: the field lane densified the frame (dust 48, sparkles
    # 40, binary 72, sparks 26) and the Qt lane adds blink dots, the screen-break
    # bodies and the nucleus rim pair, so the old 300 ceiling is stale. The
    # binding cap is the 420 of the perf contract, which this stays under.
    assert max(counts) <= 420
    assert max(counts) - min(counts) < 80


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
def _turned(t, R, x, y, depth=0.5):
    """Where the painter puts a field point: the combined X+Y tumble.

    Re-derived HERE from the painter's documented formula rather than by
    calling it, so the tests below pin the geometry the renderer claims to
    draw rather than agreeing with it by construction. Rotation order Y
    first, then X — the painter's order. The depth offset is faded by the
    COMBINED turn (``sin²θy + sin²φx − sin²θy·sin²φx``, 0 only when both
    phases are 0), so the mark opens its depth along whichever axis is
    edge-on::

        z     = (depth - 0.5) * Z_GAIN * R * fade
        xr    = x*cos_y + z*sin_y
        zr    = -x*sin_y + z*cos_y
        yr    = y*cos_x - zr*sin_x
        zr2   = y*sin_x + zr*cos_x
        X, Y  = xr*(1 + PERSP*zr2/R), yr*(1 + PERSP*zr2/R)

    The two phases come from the painter's own public reads
    (``mark_horizontal_spin_deg`` / ``mark_vertical_spin_deg``) while the
    mixing above stays local. ``t = 0`` (and a still clock, which both reads
    clamp to) is an exact identity. No clock makes the tumble "contribute
    nothing" any more — the two axes only complete together every 1107 s —
    so box-based assertions below always expect the PROJECTED box, never the
    flat one.
    """
    from voicebridge.orb import qt_app as Q

    ty = math.radians(Q.mark_horizontal_spin_deg(t))
    px = math.radians(Q.mark_vertical_spin_deg(t))
    cy, sy, cx, sx = math.cos(ty), math.sin(ty), math.cos(px), math.sin(px)
    d = min(1.0, max(0.0, float(depth)))
    fade = sy * sy + sx * sx - sy * sy * sx * sx
    z = (d - 0.5) * Q.Y_SPIN_Z_GAIN * R * fade
    xr = x * cy + z * sy
    zr = -x * sy + z * cy
    yr = y * cx - zr * sx
    zr2 = y * sx + zr * cx
    persp = 1.0 + Q.Y_SPIN_PERSP * zr2 / R
    persp = min(Q.Y_SPIN_PERSP_MAX, max(Q.Y_SPIN_PERSP_MIN, persp))
    return xr * persp, yr * persp


def _pix(t, R, c, item, depth=None, zseed="auto", micro_salt=None):
    """The painted PIXEL position of one field element (``c`` is the centre).

    ``depth`` defaults to the element's own ``depth`` field; pass ``None``
    explicitly for a trail sample that inherits its BODY's depth. The
    projection depth goes through the painter's ``turn_qdepth`` — hash-broken
    and flat per element — exactly as the renderer does: ``zseed`` defaults
    to the item's own ``index`` (pass the BODY's index explicitly for a trail
    sample that rides its body's rigid streak). The field's two VOLUME axes
    (``z``/``z2``) add the painter's screen-space volume displacement
    (:func:`qt_app.volume_offset`) on top of the projection, exactly as
    ``_place`` does. ``micro_salt`` adds the field's bounded micro-wobble for
    the painter passes that consume it (volume/orbiter/cylinder/filament/...).
    """
    from voicebridge.orb import qt_app as Q
    d = item.get("depth", 0.5) if depth is None else depth
    seed = item.get("index") if zseed == "auto" else zseed
    qd = Q.turn_qdepth(d, seed, flat=True) if seed is not None else d
    tx, ty = _turned(t, R, item.get("x", 0.0), item.get("y", 0.0), qd)
    ox, oy = Q.volume_offset(t, R, item.get("z"), item.get("z2"))
    if micro_salt is not None:
        mx, my = Q._field.micro_offset(t, item.get("index"), R, micro_salt)
        ox += mx
        oy += my
    return c + tx + ox, c + ty + oy


def _declared_ring_box(t, R, ring, tilt):
    """Bounding box of one DECLARED ring as the painter's tumble projects it.

    Vertices carry the painter's smooth out-of-plane lobe
    (``0.5 + TURN_RING_TILT*sin(angle + phase)``, phased by the ring's own
    seed) rather than the field's depth convention — a per-vertex hash would
    be noise (zigzags), a lobe is a plane tilt (clean) — so the expectation
    re-derives the same lobe instead of the old depth ramp. ``tilt`` is
    supplied by the caller so this can be asked for the LAGGED follower tilt
    and for today's orbit tilt and the two compared.
    """
    from voicebridge.orb import qt_app as Q
    from voicebridge.orb import mark_field as F

    spec = dict(ring)
    # Fluid circles: the painter adds the ring's plane precession
    # (``tilt_spin_deg``) to the supplied base tilt and multiplies the declared
    # radii by ``ring_radius_scale`` AND the per-ring ``shimmer`` (the sub-
    # percent accretion radius texture). Mirror all three here so the expected
    # box is the drawn box.
    spin = ring.get("tilt_spin_deg")
    if spin is None:
        spin = F.ring_tilt_spin(t, ring)
    breath = F.ring_radius_scale(t, ring)
    shimmer = ring.get("shimmer")
    if shimmer is None:
        shimmer = F.ring_shimmer(t, ring)
    spec["tilt"] = tilt + spin
    spec["rx_frac"] = ring.get("rx_frac", F.RX_FRAC) * breath * shimmer
    spec["ry_frac"] = ring.get("ry_frac", F.RY_FRAC) * breath * shimmer
    dx, dy = F.camera_offset(t, R)
    raw = F.ring_path(t, R, spec)
    n = len(raw) - 1
    rphase = 2.0 * math.pi * F.hash01(float(ring.get("seed", 0.0)),
                                     Q.TURN_RING_SALT)
    xs, ys = [], []
    for i, (px, py) in enumerate(raw):
        qd = 0.5 + Q.TURN_RING_TILT * math.sin(
            2.0 * math.pi * (i % n) / n + rphase)
        tx, ty = _turned(t, R, px - dx, py - dy, min(1.0, max(0.0, qd)))
        xs.append(tx)
        ys.append(ty)
    return max(xs) - min(xs), max(ys) - min(ys)


def _thin_orbit_outlines(prims, c, tol=0.75):
    """Centred outline paths that are neither the main stroke nor a circle.

    ``tol`` bounds how far a stroke's bounding-box centre may sit off the
    mark's axis. It has to be loosened away from sub-pixel: under a real 3D
    tumble with perspective, a circle's projected silhouette is genuinely not
    centred on the axis (the far arc shrinks while the near arc grows), and
    the recorded box centre drifts a pixel or two with it.
    """
    # Owner 2026-10-19: the whole ring set was cut to faint, translucent
    # hairlines (followers now sit near 7% alpha, the main orbits at 16-26%),
    # so the old 0.2..0.45 follower band no longer separates them. Callers
    # identify a follower by its DECLARED box instead of by its alpha; this
    # helper only filters "a thin centred elliptical outline".
    return [p for p in prims
            if p.kind == "path" and p.fill is None and p.pen is not None
            and math.hypot(p.cx - c, p.cy - c) <= tol
            and p.width <= 1.2 and 0.005 <= p.alpha <= 0.60
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

    from voicebridge.orb import qt_app as Q

    size, c = 192, 96.0
    R = size / 2.0 - 6.0
    t = 0.37
    prims = _record(t=t, size=size)

    # Thin centred outlines include the two satellite CIRCLES as well as the
    # two echo followers, so the followers are picked out the way the painter
    # builds them: each is a DECLARED ring (``ring_geometry``) stroked through
    # the mark's combined-tumble projection, so its recorded box must equal
    # that ring's projected box. Matching the box (rather than a ratio of its sides) is
    # what makes this work at any clock: the projection changes the box, so a
    # clock-independent test has to expect the projected one.
    lagged = {str(e["orbit"]): e["tilt"] for e in F.echo_rings(t)}
    near_tilt, far_tilt = F.orbit_tilts(t)
    fresh = {"a": near_tilt, "b": far_tilt}
    want, stale = [], []
    for ring in F.ring_geometry(t):
        if ring["layer"] != "echo":
            continue
        want.append(_declared_ring_box(t, R, ring, lagged[str(ring["orbit"])]))
        stale.append(_declared_ring_box(t, R, ring, fresh[str(ring["orbit"])]))

    thin = _thin_orbit_outlines(prims, c, tol=0.05 * R)

    def _matches(p, boxes):
        return any(abs(p.w - w) <= 0.75 and abs(p.h - h) <= 0.75
                   for w, h in boxes)

    # Exactly one follower outline per main orbit, matched by the DECLARED
    # (projected) box — the ring alphas were cut on 2026-10-19, so alpha no
    # longer separates a follower from the main orbits.
    followers = [p for p in thin if _matches(p, want)]
    assert len(followers) == 2, followers

    # The lag matters: the follower sits at the orbit tilt ECHO_LAG_S ago, not
    # today's, and at this phase the two disagree by a visible margin. If the
    # painter were using today's tilt, these strokes would land on `stale`.
    assert any(abs(a[0] - b[0]) > 2.0 or abs(a[1] - b[1]) > 2.0
               for a, b in zip(sorted(want), sorted(stale))), (want, stale)
    for p in followers:
        assert not _matches(p, stale), (p.w, p.h, stale)
    # The lag is real at this clock: the follower's tilt genuinely differs from
    # its own orbit's base tilt. (The old trailing check recovered an in-plane
    # tilt from the 3-D-projected bounding box, which is not a valid recovery
    # under the X+Y tumble and read 0 spuriously; the box matches above are the
    # actual contract.)
    assert all(abs(lagged[o] - fresh[o]) > 1.0 for o in ("a", "b")), \
        (lagged, fresh)


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


def test_glint_arms_are_a_cross_not_an_L_or_V_hook():
    # Owner 2026-10-19: "you didnt replace the L and V's!!!!!" — the glint's
    # second arm was drawn from ``(gx-ay, gy+ax)`` to ``(gx+ay, gy+ax)``, i.e.
    # a HORIZONTAL stroke sharing an endpoint with the first arm, so every star
    # rendered as an L/V hook. Both arms must now cross at the core, span the
    # same length and be perpendicular.
    import os as _os

    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    size, t = 192, 0.4
    _qt_app()
    fam = E.family_from_core(E.state_core("idle"))
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(*BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)

    class _Lines(_Recorder):
        def __init__(self, painter):
            super().__init__(painter)
            object.__setattr__(self, "segs", [])

        def drawLine(self, *args):
            try:
                a, b = args
                object.__setattr__(self, "segs", self.segs + [
                    ((float(a.x()), float(a.y())),
                     (float(b.x()), float(b.y())))])
            except Exception:
                pass
            super().drawLine(*args)

    rec = _Lines(p)
    Q._paint_mark(rec, size / 2.0, size / 2.0, size / 2.0 - 6.0, t, "idle",
                  1.0, fam)
    p.end()

    # Pair up the two arms of each glint by their shared midpoint.
    groups = {}
    for (x0, y0), (x1, y1) in rec.segs:
        mid = (round((x0 + x1) / 2.0, 3), round((y0 + y1) / 2.0, 3))
        groups.setdefault(mid, []).append(((x0, y0), (x1, y1)))

    checked = 0
    for mid, segs in groups.items():
        if len(segs) != 2:
            continue
        (a0, a1), (b0, b1) = segs
        da = (a1[0] - a0[0], a1[1] - a0[1])
        db = (b1[0] - b0[0], b1[1] - b0[1])
        la = math.hypot(*da)
        lb = math.hypot(*db)
        if la < 0.5 or lb < 0.5:
            continue
        # Equal-length arms...
        assert la == pytest.approx(lb, rel=0.02), (mid, la, lb)
        # ...that are perpendicular (dot product ~ 0), i.e. a CROSS.
        dot = (da[0] * db[0] + da[1] * db[1]) / (la * lb)
        assert abs(dot) < 0.05, (mid, dot, da, db)
        checked += 1
    assert checked >= 10, checked


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
        px, py = _pix(t, R, c, s)
        arms = [p for p in prims if p.kind == "line"
                and math.hypot(p.cx - px, p.cy - py) <= 0.6]
        core = [p for p in prims if p.kind == "ellipse" and p.fill is not None
                and math.hypot(p.cx - px, p.cy - py) <= 0.6]
        assert len(arms) == 2, (s, arms)   # two crossing arms
        # A core lands here too, but now that the shells are denser another
        # body can share the pixel: what the spark must guarantee is that its
        # own core is painted, not that it is the only thing at this spot.
        assert core, (s, core)


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
    # Painting a still frame must not consult ANY field layer that carries
    # motion. The one name that can still appear is ``echo_rings``, and only
    # because the painter now reads its circles from the field's SINGLE
    # declared geometry (``ring_geometry``), which composes ``echo_rings``
    # internally — at the frozen clock, and with every ``echo`` entry then
    # dropped from the still frame. The behavioural guarantees are asserted
    # below regardless: identical still frames and no follower outlines.
    assert set(calls) <= {"echo_rings"}, calls
    assert "nucleus_sparks" not in calls and "dust_state" not in calls
    assert "sparkles" not in calls and "comet" not in calls
    assert "trails" not in calls
    assert _dump(a) == _dump(b)
    # No FOLLOWER outline can survive into the still frame. Match the declared
    # follower boxes (the still-frame ring set legitimately keeps its main
    # orbits, which are also thin centred outlines).
    from voicebridge.orb import mark_field as F
    frozen = 0.0
    lagged = {str(e["orbit"]): e["tilt"] for e in F.echo_rings(frozen)}
    R_still = 90.0
    follower_boxes = [
        _declared_ring_box(frozen, R_still, ring, lagged[str(ring["orbit"])])
        for ring in F.ring_geometry(frozen) if ring["layer"] == "echo"]
    thin = _thin_orbit_outlines(a, 96.0)
    for p in thin:
        assert not any(abs(p.w - w) <= 0.75 and abs(p.h - h) <= 0.75
                       for w, h in follower_boxes), (p.w, p.h, follower_boxes)


def test_comet_and_glints_ease_instead_of_stepping():
    from voicebridge.orb import mark_field as F

    # The comet is invisible at its sweep boundary and eases in: no hard on.
    start = 0.45 * F.COMET_PERIOD_S
    assert F.comet(start, 90.0)["alpha"] == pytest.approx(0.0, abs=1e-6)
    assert F.comet(start + 0.02, 90.0)["alpha"] < 0.1
    assert F.comet(start + 0.6, 90.0)["alpha"] > F.comet(start + 0.02, 90.0)["alpha"]

    # Glints never pop in or out: alpha stays above the shared floor.
    for k in range(400):
        assert F.glint(k / 100.0, 137.5)["alpha"] >= F.GLINT_MIN_ALPHA - 1e-9


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


# --------------------------------------------------------------------------- #
# (m) master spin: a slow full rotation of the WHOLE mark
# --------------------------------------------------------------------------- #
def test_master_spin_is_one_full_turn_per_period_and_continuous():
    from voicebridge.orb import qt_app as Q

    assert Q.mark_spin_deg(0.0) == pytest.approx(0.0)
    # One full turn per period, and it keeps counting past it (a wrapped
    # phase would snap; a ramp cannot).
    assert Q.mark_spin_deg(Q.MASTER_SPIN_PERIOD_S) == pytest.approx(360.0)
    assert Q.mark_spin_deg(2.5 * Q.MASTER_SPIN_PERIOD_S) == pytest.approx(900.0)
    step = 360.0 / Q.MASTER_SPIN_PERIOD_S / 60.0
    prev = Q.mark_spin_deg(0.0)
    for k in range(1, int(60 * Q.MASTER_SPIN_PERIOD_S)):
        cur = Q.mark_spin_deg(k / 60.0)
        assert cur - prev == pytest.approx(step, abs=1e-9), (k, prev, cur)
        prev = cur


def test_master_spin_is_a_no_op_under_reduced_motion():
    from voicebridge.orb import qt_app as Q

    with monkeypatch_env("QUBIT_REDUCED_MOTION", "1"):
        assert Q.mark_spin_deg(37.4) == 0.0
        assert Q.mark_spin_deg(-12.0) == 0.0


def test_master_spin_turns_the_whole_mark_without_snapping():
    from voicebridge.orb import qt_app as Q

    # Across the wrap the painted frame moves only by ONE frame's worth of
    # rotation: the spin never jumps, it eases through the turn.
    near_wrap = _diff(_paint(23.999), _paint(24.0))
    one_second = _diff(_paint(23.999), _paint(24.999))
    assert 0 < near_wrap < one_second, (near_wrap, one_second)
    # A quarter turn is plainly visible: the assembly really does rotate.
    quarter = _diff(_paint(1.0), _paint(1.0 + Q.MASTER_SPIN_PERIOD_S / 4.0))
    assert quarter > 0


def test_reduced_motion_paint_carries_no_spin_transform():
    # A still frame must not leave a rotation on the painter either: the
    # transform is pushed and popped around the mark body.
    _qt_app()
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    os.environ["QUBIT_REDUCED_MOTION"] = "1"
    try:
        img = QImage(128, 128, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(*BG))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        Q._paint_mark(p, 64.0, 64.0, 58.0, 21.7, "idle", 1.0,
                      E.family_from_core(E.state_core("idle")))
        assert p.transform().isIdentity(), p.transform()
        p.end()
    finally:
        os.environ.pop("QUBIT_REDUCED_MOTION", None)


# --------------------------------------------------------------------------- #
# (n) binary particles: a field of tiny 1/0 data fragments
# --------------------------------------------------------------------------- #
def test_binaries_are_deterministic_and_gone_under_reduced_motion():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    R = 90.0
    bits = Q.mark_binaries(0.7, R)
    assert len(bits) == F.BINARY_COUNT, len(bits)
    assert bits == Q.mark_binaries(0.7, R)
    for b in bits:
        assert b["char"] in ("0", "1")
        assert 0.0 <= b["depth"] <= 1.0
        assert b["size"] > 0.0 and b["alpha"] > 0.0
    with monkeypatch_env("QUBIT_REDUCED_MOTION", "1"):
        assert Q.mark_binaries(0.7, R) == []


def test_binaries_paint_as_glyphs_split_by_depth():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    t = 0.42
    prims = _record(t=t, size=size)
    # Binary fragments are the "1"/"0" glyphs; math-symbol/equation glyphs are
    # drawn as text too (see the math cases below), so select by char. Draw
    # order is far fragments first, then near ones, each in field order.
    glyphs = [(i, p) for i, p in enumerate(prims)
              if p.kind == "text" and p.text in ("0", "1")]
    bits = sorted(F.binary_state(t, R), key=lambda b: b["depth"] >= 0.5)
    assert len(glyphs) == len(bits) == F.BINARY_COUNT

    seam = [i for i, p in enumerate(prims)
            if p.kind == "ellipse" and p.fill is not None
            and math.hypot(p.cx - c, p.cy - c) <= 0.75]
    assert len(seam) == 1, seam
    far, near = [], []
    for (at, p), b in zip(glyphs, bits):
        bx, by = _pix(t, R, c, b)
        assert p.text == b["char"]
        assert math.hypot(p.cx - bx, p.cy - by) <= 0.01
        (far if b["depth"] < 0.5 else near).append(at)

    # The same z-split as every other layer: far fragments behind the
    # nucleus, near ones in front of it.
    assert len(far) >= 8 and len(near) >= 8, (len(far), len(near))
    assert max(far) < seam[0] < min(near), (far, seam, near)


def test_binaries_actually_paint_pixels(monkeypatch):
    from voicebridge.orb import qt_app as Q

    with_spark = _paint(0.42)
    with monkeypatch.context() as m:
        m.setattr(Q._field, "binary_state", lambda t, r: [])
        without = _paint(0.42)
    assert _diff(with_spark, without) > 0


# --------------------------------------------------------------------------- #
# (o) inner core ring: a thin third orbit around the nucleus
# --------------------------------------------------------------------------- #
#: Widest pen the inner core ring may be stroked with (it is a hairline).
Q_INNER_RING_MAX_W = 1.2


def _inner_ring_declared(t, R):
    """The single DECLARED inner core ring at clock ``t``."""
    from voicebridge.orb import mark_field as F

    inner = [r for r in F.ring_geometry(t) if r["layer"] == "inner"]
    assert len(inner) == 1
    return inner[0]


def _match_inner_ring(prims, c, R, t):
    """The recorded stroke matching the inner ring's projected declared box.

    Same matching as the echo followers above: the ring is a DECLARED circle
    stroked through the combined tumble, so its recorded box must equal its
    declared path's box AFTER that projection (``_declared_ring_box``), not
    the flat one. The centre bound is the echo one: the projected silhouette
    is genuinely off-centre by a pixel or two.
    """
    from voicebridge.orb import qt_app as Q

    want = _declared_ring_box(t, R, _inner_ring_declared(t, R),
                              Q.mark_inner_ring_tilt(t))
    return [p for p in prims if p.kind == "path" and p.fill is None
            and p.pen is not None
            and abs(p.w - want[0]) <= 0.75 and abs(p.h - want[1]) <= 0.75
            and math.hypot(p.cx - c, p.cy - c) <= 0.05 * R
            and p.width <= Q_INNER_RING_MAX_W]


def test_inner_ring_is_a_thin_dim_depth_neutral_orbit():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    # No clock makes the tumble "contribute nothing" any more, so — like the
    # echo followers above — this measures the core ring's declared geometry
    # THROUGH the projection rather than the flat one.
    t = 0.37
    prims = _record(t=t, size=size)
    rings = _match_inner_ring(prims, c, R, t)
    assert len(rings) == 1, rings
    ring = rings[0]
    # Thin, dim, and never brighter than the echo followers.
    assert ring.width <= 1.2
    assert 0.0 < ring.alpha <= Q.INNER_RING_ALPHA + 1e-6, ring.alpha
    assert ring.alpha < F.ECHO_ALPHA, (ring.alpha, F.ECHO_ALPHA)

    seam = [i for i, p in enumerate(prims)
            if p.kind == "ellipse" and p.fill is not None
            and math.hypot(p.cx - c, p.cy - c) <= 0.75]
    assert len(seam) == 1
    # Depth-neutral: drawn once, before the far pass, never a near/far pair.
    drawn_at = [i for i, p in enumerate(prims) if p is ring]
    assert len(drawn_at) == 1 and drawn_at[0] < seam[0]


def test_inner_ring_tilt_tracks_the_shared_field_and_freezes_when_still():
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    size, c = 192, 96.0
    R = size / 2.0 - 6.0
    # The flat-ellipse tilt recovery (``_tilt_abs_deg``) assumed a single-axis
    # turn; under the combined tumble a box width mixes the tilt with the X
    # compression, so the recovery is invalid. Instead each clock's recorded
    # stroke must equal that clock's PROJECTED declared box with that clock's
    # field tilt — and the two clocks' boxes must differ, because the 4.5 s
    # core ring genuinely turns between them.
    boxes = []
    for t in (0.37, 2.0):
        rings = _match_inner_ring(_record(t=t, size=size), c, R, t)
        assert len(rings) == 1, (t, rings)
        assert Q.mark_inner_ring_tilt(t) == pytest.approx(F.inner_ring_tilt(t))
        boxes.append((rings[0].w, rings[0].h))
    assert (abs(boxes[0][0] - boxes[1][0]) > 2.0
            or abs(boxes[0][1] - boxes[1][1]) > 2.0), boxes

    with monkeypatch_env("QUBIT_REDUCED_MOTION", "1"):
        assert Q.mark_inner_ring_tilt(9.0) == F.INNER_RING_TILT_BASE


# --------------------------------------------------------------------------- #
# (p) refine round: short wakes, math glyphs, screen-break bodies
# --------------------------------------------------------------------------- #
def _counter_diff(a, b):
    diff = Counter(a)
    diff.subtract(Counter(b))
    return list(diff.elements())


def test_wake_ribbons_are_gone_and_the_field_trails_are_retired():
    # Owner 2026-10-19: the 3-point "RGB trail" ribbons rendered as thick bent
    # L/V check-mark sticks ("you didnt replace the L and V's!!!!!"), so the Qt
    # painter no longer draws them at all. The field's trail builders are
    # retired with them, leaving no API a renderer could pick back up.
    from voicebridge.orb import mark_field as F
    from voicebridge.orb import qt_app as Q

    for name in ("rgb_trail", "dust_trail", "_rgb_trail"):
        assert not hasattr(F, name), name
    # And the painter has no streak helper left to regress through.
    assert not hasattr(Q, "_streak")
    assert not hasattr(Q, "TRAIL_DRAW_SAMPLES")
    assert _record(t=1.234, size=192) is not None


def test_volume_particles_paint_as_dots_split_by_depth(monkeypatch):
    # Owner 2026-10-19: the math-symbol/equation TEXT glyphs read as
    # "check marks / v lines" and are gone. The field's ``volume_particles``
    # (real 3D bodies) is drawn INSTEAD, as round DOTS — never text, never a
    # glyph — split like every other layer: far behind the nucleus, near in
    # front of it.
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    t = 0.42
    bodies = [
        {"index": 0.0, "x": -30.0, "y": 12.0, "depth": 0.2, "size": 1.4,
         "alpha": 0.9, "lift": 0.5, "hue": 0.0},
        {"index": 1.0, "x": -12.0, "y": -8.0, "depth": 0.3, "size": 1.4,
         "alpha": 0.9, "lift": 0.5, "hue": 0.0},
        {"index": 2.0, "x": 14.0, "y": 9.0, "depth": 0.8, "size": 1.4,
         "alpha": 0.9, "lift": 0.5, "hue": 0.5},
        {"index": 3.0, "x": 28.0, "y": -14.0, "depth": 0.7, "size": 1.4,
         "alpha": 0.9, "lift": 0.5, "hue": 0.5},
    ]
    with monkeypatch.context() as m:
        m.setattr(Q._field, "volume_particles",
                  lambda tt, rr: [dict(s) for s in bodies], raising=False)
        m.setattr(Q._field, "binary_state", lambda *a, **k: [])
        prims = _record(t=t, size=size)

    # No text at all: the retired math layer must stay retired.
    assert not [p for p in prims if p.kind == "text"]
    for s in bodies:
        px, py = _pix(t, R, c, s, micro_salt=719.9)
        dots = [p for p in prims if p.kind == "ellipse" and p.fill is not None
                and math.hypot(p.cx - px, p.cy - py) <= 0.75]
        assert dots, (s["index"], px, py)
    seam = [i for i, p in enumerate(prims)
            if p.kind == "ellipse" and p.fill is not None
            and math.hypot(p.cx - c, p.cy - c) <= 0.75]
    assert len(seam) == 1, seam
    # Split by the FIELD's depth, not by paint order alone.
    def _dot_index(s):
        px, py = _pix(t, R, c, s, micro_salt=719.9)
        return min(i for i, p in enumerate(prims)
                   if p.kind == "ellipse" and p.fill is not None
                   and math.hypot(p.cx - px, p.cy - py) <= 0.75)

    far_i = [_dot_index(s) for s in bodies if s["depth"] < 0.5]
    near_i = [_dot_index(s) for s in bodies if s["depth"] >= 0.5]
    assert len(far_i) == 2 and len(near_i) == 2, (far_i, near_i)
    assert max(far_i) < seam[0] < min(near_i), (far_i, seam, near_i)


def test_reduced_motion_draws_no_volume_particles(monkeypatch):
    from voicebridge.orb import qt_app as Q

    # The layer is dropped WITHOUT even being consulted under reduced motion.
    calls = []
    with monkeypatch.context() as m:
        m.setattr(Q._field, "volume_particles",
                  lambda tt, rr: calls.append(1) or [
                      {"index": 0.0, "x": 0.0, "y": 0.0, "depth": 0.8,
                       "size": 1.5, "alpha": 0.9, "lift": 0.5, "hue": 0.5}],
                  raising=False)
        rec = _record(t=1.0, reduced=True)
    assert calls == []
    assert not [p for p in rec if p.kind == "text"]


def test_screen_break_bodies_paint_beyond_the_orb_radius(monkeypatch):
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    t = 0.0
    outside = [
        {"index": 0.0, "x": 1.35 * R, "y": 0.0, "depth": 0.5, "size": 1.6,
         "alpha": 0.95, "lift": 0.5, "hue": 0.0},
        {"index": 1.0, "x": -1.30 * R, "y": 0.0, "depth": 0.5, "size": 1.6,
         "alpha": 0.95, "lift": 0.5, "hue": 1.0 / 3.0},
    ]
    with monkeypatch.context() as m:
        m.setattr(Q._field, "screen_break",
                  lambda tt, rr: [dict(s) for s in outside], raising=False)
        with_break = _record(t=t, size=size)
    with monkeypatch.context() as m:
        m.setattr(Q._field, "screen_break", lambda *a, **k: [], raising=False)
        bare = _record(t=t, size=size)
    extra = _counter_diff(with_break, bare)
    assert extra, "screen-break bodies were not drawn at all"
    # Nothing clips to the orb's circle: the bodies genuinely exit it.
    reach = max(math.hypot(p.cx - c, p.cy - c) for p in extra)
    assert reach > R, reach


def test_batched_layer_colours_come_from_field_hues(monkeypatch):
    # Palette discipline: the batched dot layers must paint a hue the FIELD
    # emitted, never a renderer-invented ``(k + 0.5) / n`` spread across the
    # whole wheel. With two bodies at red (0.0) and green (1/3) the frame must
    # carry BOTH colours; a spread would have painted orange/cyan/magenta, none
    # of them green-dominant.
    from voicebridge.orb import qt_app as Q

    size = 192
    t = 0.0
    bodies = [
        {"index": 0.0, "x": -20.0, "y": 0.0, "depth": 0.5, "size": 1.4,
         "alpha": 0.9, "hue": 0.0, "lift": 0.5},
        {"index": 1.0, "x": 20.0, "y": 0.0, "depth": 0.5, "size": 1.4,
         "alpha": 0.9, "hue": 1.0 / 3.0, "lift": 0.5},
    ]
    with monkeypatch.context() as m:
        m.setattr(Q, "mark_ring_particles",
                  lambda tt, rr: [dict(b) for b in bodies])
        for name in ("mark_cylinder_particles", "mark_orbiter_particles",
                     "mark_flare_dots", "mark_filament", "mark_screen_break",
                     "mark_volume_particles", "mark_accretion_stream",
                     "mark_photon_ring", "mark_infall_streaks"):
            m.setattr(Q, name, lambda tt, rr: [])
        prims = _record(t=t, size=size)
    fills = [p.fill[:3] for p in prims if p.fill is not None
             and p.kind == "path"]
    assert any(g > r + 40 and g > b + 40 for r, g, b in fills), fills


# --------------------------------------------------------------------------- #
# (p) studio light: atmospheric bloom + depth-of-field bokeh
# --------------------------------------------------------------------------- #
def test_atmospheric_bloom_lights_the_room_under_the_mark():
    # Owner: "soft glows/bloom ... professional". The mark emits its own light:
    # ONE wide, low-alpha state-tinted fill path sits under the whole
    # construction (bigger than the mark's own radius), so the vector shapes
    # read as floating in the orb's atmosphere rather than pasted on a void.
    from voicebridge.orb import qt_app as Q

    size, R = 192, 90.0
    prims = _record(t=0.42, size=size)
    big = [p for p in prims if p.kind == "path" and p.fill is not None
           and max(p.w, p.h) > 2.0 * R]
    # Exactly one such path (the bloom); it is a soft gradient fill, so the
    # recorder cannot read its apex alpha (gradient brushes report their base
    # colour), which is why this pins presence and count, not opacity.
    assert len(big) == 1, [round(max(p.w, p.h), 1) for p in big]


# --------------------------------------------------------------------------- #
# (q) the fluid refinement pass: haze bed + the new per-body keys
# --------------------------------------------------------------------------- #
def test_haze_bed_is_painted_and_dropped_under_reduced_motion(monkeypatch):
    # Owner: "make more fluid ... more detailed ... more complex". The field's
    # ``haze_state`` is the slow atmospheric parallax bed behind the mark; the
    # painter consumes it as soft glows. Full motion must paint it; reduced
    # motion must drop the moving layer and stay a still frame.
    from voicebridge.orb import qt_app as Q

    size = 192
    t = 4.7
    with_haze = _record(t=t, size=size)
    with monkeypatch.context() as m:
        m.setattr(Q, "mark_haze", lambda tt, rr: [])
        bare = _record(t=t, size=size)
    assert _counter_diff(with_haze, bare), "the haze bed was not painted"

    with monkeypatch_env("QUBIT_REDUCED_MOTION", "1"):
        assert Q.mark_haze(1.0, 90.0) == []
        a = _record(t=1.0, size=size, reduced=True)
        b = _record(t=37.4, size=size, reduced=True)
    assert _dump(a) == _dump(b)


def test_volume_glow_breathes_the_dot_size(monkeypatch):
    # ``glow`` is folded into ALPHA only for the volume layer in the field, so
    # the painter uses it for a size breath (an additive detail, not a double
    # count).
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    t = 0.42
    base = {"index": 0.0, "x": -24.0, "y": 6.0, "depth": 0.6, "size": 1.4,
            "alpha": 0.9, "lift": 0.5, "hue": 0.58}

    def rec(glow):
        with monkeypatch.context() as m:
            m.setattr(Q, "mark_volume_particles",
                      lambda tt, rr: [dict(base, glow=glow)])
            return _record(t=t, size=size)

    def radius(prims):
        px, py = _pix(t, R, c, base, micro_salt=719.9)
        hits = [p for p in prims if p.kind == "ellipse"
                and p.fill is not None
                and math.hypot(p.cx - px, p.cy - py) <= 0.75]
        assert hits, (px, py)
        return max(p.r for p in hits)

    assert radius(rec(1.25)) > radius(rec(0.75)) * 1.1


def test_photon_lobe_blends_toward_the_second_palette_hue(monkeypatch):
    # ``lobe`` is the lensed bright-arc profile. The painter keeps the field's
    # alpha contract and uses ``lobe`` for a hue blend toward the sample's
    # paired ``hue2`` (red -> green here), so the bright arcs are not a flat
    # single-colour dotted circle.
    from voicebridge.orb import qt_app as Q

    size, R, c = 192, 90.0, 96.0
    t = 0.0
    samples = [
        {"index": 0.0, "x": -0.18 * R, "y": 0.0, "depth": 0.6, "size": 0.34,
         "alpha": 0.7, "lift": 0.5, "hue": 0.0, "hue2": 1.0 / 3.0,
         "lobe": 0.0, "sparkle": 1.0},
        {"index": 1.0, "x": 0.18 * R, "y": 0.0, "depth": 0.6, "size": 0.34,
         "alpha": 0.7, "lift": 0.5, "hue": 0.0, "hue2": 1.0 / 3.0,
         "lobe": 1.0, "sparkle": 1.0},
    ]
    with monkeypatch.context() as m:
        m.setattr(Q, "mark_photon_ring",
                  lambda tt, rr: [dict(s) for s in samples])
        prims = _record(t=t, size=size)

    def fill_at(item):
        px, py = _pix(t, R, c, item)
        hits = [p for p in prims if p.kind == "ellipse"
                and p.fill is not None
                and math.hypot(p.cx - px, p.cy - py) <= 0.75]
        assert hits, (item["index"], px, py)
        return max(hits, key=lambda p: p.r).fill

    cold = fill_at(samples[0])
    hot = fill_at(samples[1])
    assert hot[1] > cold[1] + 8, (cold, hot)
