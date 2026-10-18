"""Renderer-agnostic animation-quality contract for the Qubit-mark orb.

The orb look is owned by TWO independent renderers that must stay in visual
agreement:

  * the Tk renderer  ``voicebridge.orb.mark.render``        (FakeCanvas proof)
  * the Qt renderer  ``voicebridge.orb.qt_app._paint_mark`` (offscreen QImage)

This module does not read source text. It *draws* a frame through each
renderer and inspects the recorded primitives (centre, radius, colour, alpha)
so a regression that silently drops an effect fails the contract for that
renderer. Every assertion is behavioural and runs against BOTH renderers via
the ``renderer`` parametrisation below.

Effects under contract (every assertion is behavioural and runs against BOTH
renderers via the ``renderer`` parametrisation below):

  (a) MOTION     — geometry changes between nearby phases for both renderers
  (b) HEARTBEAT  — nucleus size/opacity pulses; envelope repeats at ~0.95s
  (c) DEPTH      — farther beads are smaller and lower-contrast than near ones
  (d) SPARKLES   — deterministic glints turn on/off across the cycle, bounded
  (e) REDUCED    — identical frame for any ``t`` when reduced motion is on
  (f) ECHO       — two thin follower rings trailing the main orbits
  (g) SPARKS     — nucleus sparks leave the nucleus and reach past the orbits

The (f)/(g) effects are shared-field work that the Tk renderer draws and the
Qt painter gains in a sibling lane; their cases stay ``xfail(strict=False)``
until both renderers carry them, then pass for both with no edit.
"""
from __future__ import annotations

import math
import os
import sys
import dataclasses
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Frame centre / radius used for every probe (matches the Qt unit test scale).
CX = CY = 64.0
R = 56.0
BG = (11, 13, 18)  # ember.NEAR_BLACK


# --------------------------------------------------------------------------- #
# primitive model shared by both adapters
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class Prim:
    kind: str          # "ellipse" | "path" | "line"
    cx: float
    cy: float
    r: float           # characteristic radius (0 for lines)
    rgb: tuple
    alpha: float       # effective 0..1 opacity/contrast


@dataclasses.dataclass(frozen=True)
class Frame:
    prims: tuple

    @property
    def total(self) -> int:
        return len(self.prims)

    def ellipses(self):
        return [p for p in self.prims if p.kind == "ellipse"]

    def near(self, x: float, y: float, radius: float):
        return [p for p in self.prims
                if math.hypot(p.cx - x, p.cy - y) <= radius]


def _luminance(rgb) -> float:
    r, g, b = (max(0, min(255, int(v))) for v in rgb)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _contrast(rgb) -> float:
    """Contrast against the near-black background (Tk's alpha substitute)."""
    return max(0.0, _luminance(rgb) - _luminance(BG)) / (1.0 - _luminance(BG))


def _hex_to_rgb(h: str) -> tuple:
    h = str(h).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return (0, 0, 0)


# --------------------------------------------------------------------------- #
# Tk adapter (hermetic FakeCanvas, no display)
# --------------------------------------------------------------------------- #
class _TkCanvas:
    def __init__(self) -> None:
        self.ovals = []
        self.lines = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append((float(x0), float(y0), float(x1), float(y1), dict(kw)))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((tuple(float(v) for v in pts), dict(kw)))
        return len(self.lines)


def _tk_frame(t: float, reduced: bool = False) -> Frame:
    from voicebridge.orb import mark as M

    canvas = _TkCanvas()
    M.render(canvas, CX, CY, R, t, "idle", reduced_motion=reduced)
    prims = []
    for (x0, y0, x1, y1, kw) in canvas.ovals:
        rgb = _hex_to_rgb(kw.get("fill", "#000000"))
        prims.append(Prim(
            "ellipse", (x0 + x1) / 2.0, (y0 + y1) / 2.0,
            ((x1 - x0) + (y1 - y0)) / 4.0, rgb, _contrast(rgb)))
    for (pts, kw) in canvas.lines:
        rgb = _hex_to_rgb(kw.get("fill", "#000000"))
        xs, ys = pts[0::2], pts[1::2]
        prims.append(Prim(
            "line", sum(xs) / len(xs), sum(ys) / len(ys), 0.0, rgb,
            _contrast(rgb)))
    return Frame(tuple(prims))


# --------------------------------------------------------------------------- #
# Qt adapter (offscreen QImage painting through a recording painter proxy)
# --------------------------------------------------------------------------- #
class _QRecorder:
    """Wraps a real QPainter, recording every paint op, then forwarding it."""

    def __init__(self, painter) -> None:
        object.__setattr__(self, "_p", painter)
        object.__setattr__(self, "_brush", None)
        object.__setattr__(self, "_pen", None)
        object.__setattr__(self, "_opacity", 1.0)
        object.__setattr__(self, "_stack", [])
        object.__setattr__(self, "prims", [])

    # -- state setters -----------------------------------------------------
    def setBrush(self, brush):
        object.__setattr__(self, "_brush", brush)
        self._p.setBrush(brush)

    def setPen(self, pen):
        object.__setattr__(self, "_pen", pen)
        self._p.setPen(pen)

    def setOpacity(self, opacity):
        object.__setattr__(self, "_opacity", float(opacity))
        self._p.setOpacity(opacity)

    def save(self):
        self._stack.append((self._brush, self._pen, self._opacity))
        self._p.save()

    def restore(self):
        if self._stack:
            b, pen, o = self._stack.pop()
            object.__setattr__(self, "_brush", b)
            object.__setattr__(self, "_pen", pen)
            object.__setattr__(self, "_opacity", o)
        self._p.restore()

    # -- colour resolution -------------------------------------------------
    def _fill_rgba(self):
        b = self._brush
        if b is None:
            return None
        # ``Qt.NoBrush`` (a brush-style enum) means no fill.
        try:
            if hasattr(b, "style") and b.style() == Qt.NoBrush:
                return None
        except Exception:
            pass
        col = b.color() if hasattr(b, "color") else b
        if not hasattr(col, "red"):
            return None
        return (col.red(), col.green(), col.blue(), col.alpha())

    def _pen_rgba(self):
        pen = self._pen
        if pen is None:
            return None
        try:
            if pen.style() == Qt.NoPen:
                return None
        except Exception:
            pass
        col = pen.color() if hasattr(pen, "color") else pen
        if not hasattr(col, "red"):
            return None
        return (col.red(), col.green(), col.blue(), col.alpha())

    def _record(self, kind, cx, cy, r, rgba):
        if rgba is None:
            alpha = 0.0
            rgb = (0, 0, 0)
        else:
            rgb = rgba[:3]
            alpha = (rgba[3] / 255.0) * self._opacity
        self.prims.append(Prim(kind, float(cx), float(cy), float(r), rgb,
                               max(0.0, min(1.0, alpha))))

    # -- drawing -----------------------------------------------------------
    def drawEllipse(self, *args):
        cx = cy = r = 0.0
        rgba = self._fill_rgba() or self._pen_rgba()
        try:
            if len(args) == 4:
                x, y, w, h = (float(v) for v in args)
                cx, cy, r = x + w / 2.0, y + h / 2.0, (w + h) / 4.0
            elif len(args) == 1:
                rect = args[0]
                cx, cy = float(rect.center().x()), float(rect.center().y())
                r = (float(rect.width()) + float(rect.height())) / 4.0
            elif len(args) == 3:
                pt, rx, ry = args
                cx, cy = float(pt.x()), float(pt.y())
                r = (float(rx) + float(ry)) / 2.0
        except Exception:
            pass
        self._record("ellipse", cx, cy, r, rgba)
        self._p.drawEllipse(*args)

    def drawPath(self, path):
        rgba = self._pen_rgba() or self._fill_rgba()
        try:
            rect = path.boundingRect()
            cx, cy = float(rect.center().x()), float(rect.center().y())
            r = (float(rect.width()) + float(rect.height())) / 4.0
        except Exception:
            cx = cy = r = 0.0
        self._record("path", cx, cy, r, rgba)
        self._p.drawPath(path)

    def drawLine(self, *args):
        rgba = self._pen_rgba()
        try:
            if len(args) == 4:
                x0, y0, x1, y1 = (float(v) for v in args)
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                r = math.hypot(x1 - x0, y1 - y0) / 2.0
            else:
                cx = cy = r = 0.0
        except Exception:
            cx = cy = r = 0.0
        self._record("line", cx, cy, r, rgba)
        self._p.drawLine(*args)

    def __getattr__(self, name):
        return getattr(self._p, name)


_QT_APP = None


def _qt_app():
    global _QT_APP
    from PySide6.QtGui import QGuiApplication

    _QT_APP = QGuiApplication.instance() or QGuiApplication([])
    return _QT_APP


def _qt_frame(t: float, reduced: bool = False) -> Frame:
    _qt_app()
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    key = "QUBIT_REDUCED_MOTION"
    prior = os.environ.get(key)

    def _restore():
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior

    if reduced:
        os.environ[key] = "1"
    else:
        os.environ.pop(key, None)
    try:
        img = QImage(int(CX * 2), int(CY * 2), QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(*BG))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rec = _QRecorder(painter)
        Q._paint_mark(rec, CX, CY, R, t, "idle", 1.0,
                      E.family_from_core(E.state_core("idle")))
        painter.end()
        return Frame(tuple(rec.prims))
    finally:
        _restore()


_RENDERERS = {"tk": _tk_frame, "qt": _qt_frame}


@pytest.fixture(params=sorted(_RENDERERS))
def renderer(request):
    return request.param, _RENDERERS[request.param]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _beads(frame: Frame):
    """Filled ellipses that are not the centre nucleus."""
    return [p for p in frame.ellipses()
            if math.hypot(p.cx - CX, p.cy - CY) > 2.5]


def _nucleus(frame: Frame):
    """The nucleus body: the LARGEST ellipse sitting on the centre.

    Picking merely the nearest ellipse no longer identifies it: the studio
    nucleus carries an orbiting specular highlight and inner swirl layers that
    pass closer to the centre than the body itself, so "nearest" tracked the
    highlight and invented extra pulse peaks.
    """
    ell = list(frame.ellipses())
    if not ell:
        return None
    reach = max(p.r for p in ell) * 0.5
    centred = [p for p in ell if math.hypot(p.cx - CX, p.cy - CY) <= reach]
    pool = centred or ell
    return max(pool, key=lambda p: p.r)


def _sample(fn, t0, t1, n):
    return [fn(t0 + (t1 - t0) * i / float(n)) for i in range(n)]


def _multiset_diff(a, b):
    """Primitives emitted in ``a`` but not ``b`` (both iterables of Prim)."""
    diff = Counter(a)
    diff.subtract(Counter(b))
    return list(diff.elements())


def _suppress_field_effect(monkeypatch, name):
    """Make the shared field's ``name`` layer render nothing, for both backends.

    The Qt painter reads ``mark_field.<name>`` through its module handle and
    the Tk renderer imports it by name into ``voicebridge.orb.mark``; patching
    both covers whichever backend the fixture is exercising.
    """
    from voicebridge.orb import mark as _mark
    from voicebridge.orb import mark_field as _field

    for mod in (_field, _mark):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, **k: [])


def _peaks(values, min_sep):
    """Circular prominent-local-maxima indices, separated by >= ``min_sep``.

    The sampled window is exactly two 0.95s heartbeat periods, so the signal
    is treated as circular: a peak at the window edge is the same event as its
    wrapped partner (avoids counting one beat twice).
    """
    n = len(values)
    cand = []
    for i in range(n):
        prev, nxt = values[(i - 1) % n], values[(i + 1) % n]
        if values[i] > 0 and values[i] >= prev and values[i] >= nxt \
                and (values[i] > prev or values[i] > nxt):
            cand.append(i)

    def cdist(a, b):
        d = abs(a - b)
        return min(d, n - d)

    chosen = []
    for i in sorted(cand, key=lambda i: values[i], reverse=True):
        if all(cdist(i, j) >= min_sep for j in chosen):
            chosen.append(i)
    return sorted(chosen)


# --------------------------------------------------------------------------- #
# (a) MOTION
# --------------------------------------------------------------------------- #
def test_motion_geometry_changes_between_nearby_phases(renderer):
    name, draw = renderer
    a = draw(2.00)
    b = draw(2.05)
    geom = lambda f: sorted(
        (p.kind, round(p.cx, 3), round(p.cy, 3), round(p.r, 3)) for p in f.prims)
    assert geom(a) != geom(b), f"[{name}] frame is static between t=2.00 and t=2.05"


# --------------------------------------------------------------------------- #
# (b) HEARTBEAT  (heartbeat nucleus, period 0.95s, double thump)
# --------------------------------------------------------------------------- #
def test_heartbeat_nucleus_pulses_twice_per_1_9s(renderer):
    name, draw = renderer

    def signal(t):
        nuc = _nucleus(draw(t))
        assert nuc is not None, f"[{name}] no nucleus drawn at t={t}"
        # Size AND opacity together: a thump may read as either (or both).
        return nuc.r * (0.5 + 0.5 * nuc.alpha)

    n = 40
    ts = [1.9 * i / n for i in range(n)]
    vals = [signal(t) for t in ts]

    spread = (max(vals) - min(vals)) / max(1e-9, sum(vals) / len(vals))
    assert spread > 0.02, f"[{name}] nucleus size/opacity is flat (no pulse)"

    step = 1.9 / n
    peaks = _peaks(vals, min_sep=max(1, int(round(0.45 / step))))
    assert len(peaks) == 2, (
        f"[{name}] expected 2 heartbeat peaks over 1.9s, got {len(peaks)} "
        f"at {[round(ts[i], 3) for i in peaks]}")

    gap = ts[peaks[1]] - ts[peaks[0]]
    gap = min(gap, 1.9 - gap)  # circular period of a 2-period window
    assert 0.6 <= gap <= 1.3, (
        f"[{name}] heartbeat period {gap:.3f}s is not ~0.95s")


# --------------------------------------------------------------------------- #
# (c) DEPTH  (sibling: scale + alpha + white-lift by depth = 0.5+0.5cos(angle))
# --------------------------------------------------------------------------- #
def test_depth_beads_scale_and_contrast_together(renderer):
    name, draw = renderer
    beads = _beads(draw(1.234))
    assert len(beads) >= 6, f"[{name}] too few beads recorded ({len(beads)})"

    radii = [b.r for b in beads]
    assert max(radii) - min(radii) > 0.3, (
        f"[{name}] bead radii are uniform (no near/far depth scale)")

    order = sorted(beads, key=lambda b: b.r)
    k = max(1, len(order) // 3)
    far_ctrl = sum(b.alpha for b in order[:k]) / k
    near_ctrl = sum(b.alpha for b in order[-k:]) / k
    assert near_ctrl > far_ctrl, (
        f"[{name}] larger (near) beads are not higher-contrast than smaller "
        f"(far) beads: near={near_ctrl:.3f} far={far_ctrl:.3f}")


# --------------------------------------------------------------------------- #
# (d) SPARKLES  (14 deterministic glints, visible first 45% of cycle)
# --------------------------------------------------------------------------- #
def test_sparkles_add_bounded_extra_primitives(renderer):
    name, draw = renderer

    def visible(t):
        return sum(1 for p in draw(t).prims if p.alpha > 0.05)

    counts = [visible(0.15 * i) for i in range(40)]
    live = max(counts)
    hidden = min(counts)
    extra = live - hidden

    assert extra > 0, f"[{name}] no glints appear/disappear across the cycle"
    # The visible-primitive delta is a "pulses but stays bounded" contract, and
    # the bound moves with deliberate density work (owner asked twice for MORE
    # particles): 26 glints + the comet's 12-sample tail + particle glint arms
    # that cross the visibility floor as depth/twinkle dim them. An exploding
    # field would blow past this by an order of magnitude.
    assert extra <= 120, (
        f"[{name}] visible-primitive swing {extra} exceeds the cap of 120")


# --------------------------------------------------------------------------- #
# (e) REDUCED MOTION
# --------------------------------------------------------------------------- #
def test_reduced_motion_is_time_invariant(renderer):
    name, draw = renderer
    a = draw(0.31, reduced=True)
    b = draw(3.77, reduced=True)
    dump = lambda f: tuple(
        (p.kind, round(p.cx, 6), round(p.cy, 6), round(p.r, 6), p.rgb)
        for p in f.prims)
    assert dump(a) == dump(b), f"[{name}] reduced motion still animates"
    assert a.total > 0, f"[{name}] reduced motion blanked the mark"


# --------------------------------------------------------------------------- #
# (f) ECHO (FOLLOWER) RINGS — two thinner orbit ghosts, one per main orbit
# --------------------------------------------------------------------------- #
@pytest.mark.xfail(strict=False, reason=(
    "ECHO follower rings are not merged into every renderer yet: the Tk "
    "renderer draws them from mark_field.echo_rings; the Qt painter gains "
    "them in a sibling lane, so this flips to a real pass once both meet."))
def test_echo_rings_add_follower_outlines(renderer, monkeypatch):
    name, draw = renderer
    full = draw(3.0)  # precession has moved the lagged tilt off the live one
    _suppress_field_effect(monkeypatch, "echo_rings")
    bare = draw(3.0)
    extra = _multiset_diff(full.prims, bare.prims)

    assert len(extra) >= 2, (
        f"[{name}] fewer than two follower rings drawn (extra={len(extra)})")
    assert len(extra) <= 12, (
        f"[{name}] follower rings emitted {len(extra)} primitives (unbounded)")
    # Follower rings are OUTLINES: they never add a filled ellipse.
    assert all(p.kind in ("line", "path") for p in extra), (
        f"[{name}] a follower ring added a filled primitive: "
        f"{sorted({p.kind for p in extra})}")


# --------------------------------------------------------------------------- #
# (g) NUCLEUS SPARKS — glints thrown OUT of the nucleus past the orbits
# --------------------------------------------------------------------------- #
@pytest.mark.xfail(strict=False, reason=(
    "NUCLEUS sparks are not merged into every renderer yet: the Tk renderer "
    "draws them from mark_field.nucleus_sparks; the Qt painter gains them in a "
    "sibling lane, so this flips to a real pass once both meet."))
def test_nucleus_sparks_are_glints_that_reach_past_the_orbits(renderer,
                                                              monkeypatch):
    name, draw = renderer
    full = draw(0.0)  # the primary thump: the shower is out
    _suppress_field_effect(monkeypatch, "nucleus_sparks")
    bare = draw(0.0)
    extra = _multiset_diff(full.prims, bare.prims)

    assert len(extra) >= 3, f"[{name}] no nucleus sparks drawn ({len(extra)})"

    from voicebridge.orb import mark_field as _field

    # Sparks travel OUT: some reach beyond the main orbit radius.
    reach = max(math.hypot(p.cx - CX, p.cy - CY) for p in extra)
    assert reach > R * _field.RX_FRAC * 1.2, (
        f"[{name}] sparks never leave the orbits (reach={reach:.1f}px)")

    # A spark's core is a glint, not a solid disc the size of the nucleus.
    nuc_r = R * _field.NUCLEUS_FRAC
    cores = [p for p in extra if p.kind == "ellipse"]
    assert cores, f"[{name}] sparks carry no core glint"
    assert all(p.r < nuc_r for p in cores), (
        f"[{name}] a spark core was drawn as a solid disc (r={max(p.r for p in cores):.2f})")
