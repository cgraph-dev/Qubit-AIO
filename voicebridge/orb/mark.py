"""Qubit-mark orb renderer: the dashboard sidebar logo as the desktop orb.

Same visual language as ``frontend/qubit-web/src/components/QubitMark.tsx`` +
``styles/qubit-mark.css``: a hairline construction drawing, never a glow sticker.

  * two thin ellipse orbits (tilted -30deg / +30deg) that counter-rotate slowly,
  * a small solid nucleus at the centre,
  * particles riding each ellipse, each at its own period and orbital seed,
  * a chromatic fringe faked on the hairline rings (Tk has no blend modes, so
    the fringe is two extra offset strokes in dark red / blue on near-black).

State meaning is preserved from ``ember`` (the single palette source): the
``STATE_CORES`` hue still tints nucleus, beads and rings, so idle / listening /
thinking / working / speaking / error read exactly as before. Geometry that the
host owns (window placement, drag, click, collapse dot, indicator dots) is
untouched.

Tk has no rotated ellipse and no per-item alpha, so:

  * an ellipse is drawn as a smoothed closed polyline sampled from its
    parametric form, rotated about the centre at draw time;
  * "opacity" is a mix toward the near-black background, precomputed per bead.

This module never opens a window: ``render`` takes any canvas-like object with
``create_oval``/``create_line`` (a hermetic fake works for proof).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from . import ember as _ember

#: Ring half-widths as a fraction of the orb radius, matching the SVG mark's
#: 40x40 viewBox (RX 14.5, RY 5.6 -> ratio 2.59) scaled to fill the disc.
RX_FRAC = 0.72
RY_FRAC = 0.28
#: Orbit tilts in degrees (degrees are negated as Tk's y axis grows downward).
TILT_A = 30.0   # near field (SVG -30deg, y-down here)
TILT_B = -30.0  # far field (SVG +30deg)
#: Nucleus radius as a fraction of the orb radius (SVG 2.4 / 40 box).
NUCLEUS_FRAC = 0.050
#: Ring counter-rotation periods in seconds (SVG: 23s / 31s, far reversed).
#: The web mark spins each ellipse a full turn; at orb scale that reads as a
#: vertical "lens" for half of every cycle, so the orb keeps the logo's
#: RECOGNIZABLE crossing tilt (+/-30deg) and sways a few degrees around it
#: instead. The particles still travel the full orbit, which is where the
#: motion reads.
RING_PERIOD_NEAR = 23.0
RING_PERIOD_FAR = 31.0
RING_SWAY_DEG = 6.0
#: Polyline sampling resolution for one ellipse.
RING_SEGMENTS = 72
#: Chromatic fringe offset, in DEVICE pixels. A lens fringe is a pixel-level
#: artifact, not a scaled one: scaling it with the radius turned the mark into
#: three separate rainbow rings instead of a hint on one hairline.
FRINGE_PX_AT_96 = 0.7
#: How far each element is lifted toward white. The sidebar mark is a BRIGHT
#: near-white hairline with only a chromatic hint, not a dim state tint; these
#: keep the state hue legible while matching that crispness.
LIFT_RING = 0.28
LIFT_BEAD = 0.18
LIFT_NUCLEUS = 0.16
#: Opacity floor for the mark so idle never reads as "almost off".
MIN_MARK_BRIGHTNESS = 0.85

#: Particles from the SVG mark: (orbit, seed_deg, radius_px_at_96, period_s).
PARTICLES: Tuple[Tuple[str, float, float, float], ...] = (
    ("a", 18.0, 1.15, 7.3),
    ("a", 97.0, 0.90, 9.1),
    ("a", 165.0, 1.00, 6.4),
    ("a", 249.0, 0.85, 10.7),
    ("a", 318.0, 1.10, 8.2),
    ("b", 41.0, 0.85, 15.4),
    ("b", 128.0, 1.00, 13.7),
    ("b", 212.0, 0.80, 18.9),
    ("b", 291.0, 0.95, 16.3),
)

#: Fringe channel colors, mixed toward the background (Tk alpha substitute).
_FRINGE_RED = (150, 30, 55)
_FRINGE_BLUE = (30, 80, 160)


def _hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, float(t)))
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def resolve_core(state: Any, colors: Any = None) -> Tuple[int, int, int]:
    """State core hue, honoring a caller palette override (cross-fade)."""
    if isinstance(colors, dict):
        core = colors.get("core")
        if isinstance(core, (tuple, list)) and len(core) == 3:
            return (int(core[0]), int(core[1]), int(core[2]))
    if isinstance(colors, (tuple, list)) and len(colors) == 3:
        return (int(colors[0]), int(colors[1]), int(colors[2]))
    return _ember.state_core(state)


def _ellipse_points(cx: float, cy: float, rx: float, ry: float,
                    tilt_deg: float) -> List[float]:
    """Closed smoothed polyline for a tilted ellipse (x0,y0,x1,y1,...)."""
    tilt = math.radians(tilt_deg)
    cos_t, sin_t = math.cos(tilt), math.sin(tilt)
    pts: List[float] = []
    for i in range(RING_SEGMENTS):
        ang = (2.0 * math.pi * i) / RING_SEGMENTS
        x = rx * math.cos(ang)
        y = ry * math.sin(ang)
        pts.append(cx + x * cos_t - y * sin_t)
        pts.append(cy + x * sin_t + y * cos_t)
    return pts


def _orbit_point(cx: float, cy: float, rx: float, ry: float,
                 tilt_deg: float, deg: float) -> Tuple[float, float]:
    """A point on a tilted ellipse at orbital parameter ``deg``."""
    rad = math.radians(deg)
    tilt = math.radians(tilt_deg)
    x = rx * math.cos(rad)
    y = ry * math.sin(rad)
    cos_t, sin_t = math.cos(tilt), math.sin(tilt)
    return (cx + x * cos_t - y * sin_t, cy + x * sin_t + y * cos_t)


def render(canvas: Any, cx: float, cy: float, radius: float, t: float,
           state: Any = "idle", reduced_motion: bool = False,
           level: Any = None, spectrum: Any = None,
           spec_peaks: Any = None, colors: Any = None,
           speed_scale: float = 1.0) -> Dict[str, Any]:
    """Draw one Qubit-mark frame on a canvas-like object.

    Signature and return shape mirror :func:`voicebridge.orb.ember.render` so
    the host can swap renderers without touching frame discipline. ``level``
    (0..1 talk meter) brightens the near ring + nucleus while listening so
    speech still visibly pumps; ``reduced_motion`` freezes the animation clock
    at a fixed phase (a static, complete mark).
    """
    ovals = 0
    lines = 0
    points = 0
    fills: List[Tuple[int, int, int]] = []
    min_y = float("inf")
    max_y = float("-inf")

    try:
        r = float(radius)
    except (TypeError, ValueError):
        r = 32.0
    if r <= 0:
        r = 32.0

    core = resolve_core(state, colors)
    fam = _ember.family_from_core(core)

    try:
        params = _ember.resolve_params(state)
        brightness = float(params.get("brightness", 1.0))
    except Exception:  # noqa: BLE001 - unknown state renders as idle
        brightness = 1.0
    try:
        speed = max(0.2, min(3.0, float(speed_scale or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    if level is not None:
        try:
            gain = _ember.listening_gain(level)
        except Exception:  # noqa: BLE001 - a bad meter never breaks the frame
            gain = 1.0
    else:
        gain = 1.0

    # ------------------------------------------------------------------ time
    # The host already advances/clamps its own clock; reduced motion freezes
    # it so the mark is complete but perfectly still. A malformed clock is
    # treated as 0 (a static frame), never an exception mid-paint.
    try:
        t_val = float(t)
    except (TypeError, ValueError):
        t_val = 0.0
    tt = 0.0 if reduced_motion else t_val * speed

    rx = r * RX_FRAC
    ry = r * RY_FRAC
    fringe = FRINGE_PX_AT_96 * (r / 32.0)
    ring_w = 1 if r < 64 else 2

    def _line(pts: List[float], fill: str, width: int = 1) -> None:
        nonlocal lines, points, min_y, max_y
        try:
            canvas.create_line(*pts, fill=fill, width=width, smooth=True,
                               capstyle="round", joinstyle="round")
            lines += 1
            points += len(pts) // 2
            for i in range(1, len(pts), 2):
                y = pts[i]
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
        except Exception:
            pass

    def _oval(x: float, y: float, rad: float, fill: str) -> None:
        nonlocal ovals, min_y, max_y
        try:
            canvas.create_oval(x - rad, y - rad, x + rad, y + rad,
                               fill=fill, outline="")
            ovals += 1
            if y - rad < min_y:
                min_y = y - rad
            if y + rad > max_y:
                max_y = y + rad
        except Exception:
            pass

    # ------------------------------------------------------------- far field
    # Drawn first so the nucleus and the near field sit over it (depth).
    far_tilt = TILT_B + RING_SWAY_DEG * math.sin(
        tt * 2.0 * math.pi / RING_PERIOD_FAR)
    far_pts = _ellipse_points(cx, cy, rx, ry, far_tilt)
    # Vivid, logo-like treatment: the state hue stays SATURATED (only a
    # slight lift) — lifting it toward white washed the mark out.
    bright = max(MIN_MARK_BRIGHTNESS, float(brightness or 0.0))
    ring_rgb = _mix(fam["core"], (255, 255, 255), LIFT_RING)
    bead_rgb = _mix(fam["core"], (255, 255, 255), LIFT_BEAD)
    nuc_rgb = _mix(fam["core"], (255, 255, 255), LIFT_NUCLEUS)
    ring_dim = _mix(ring_rgb, _ember.NEAR_BLACK, 1.0 - 0.60 * bright)
    _line([p + (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(far_pts)],
          _hex(_mix(_FRINGE_BLUE, _ember.NEAR_BLACK, 0.45)), ring_w)
    _line([p - (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(far_pts)],
          _hex(_mix(_FRINGE_RED, _ember.NEAR_BLACK, 0.45)), ring_w)
    _line(far_pts, _hex(ring_dim), ring_w)

    for orbit, seed, bead_r, dur in PARTICLES:
        if orbit != "b":
            continue
        ang = seed + (tt * 360.0 / max(0.1, dur))
        bx, by = _orbit_point(cx, cy, rx, ry, far_tilt, ang)
        depth = 0.5 + 0.5 * math.cos(math.radians(ang))
        bead = _mix(bead_rgb, _ember.NEAR_BLACK,
                    1.0 - (0.70 * bright + 0.25 * depth))
        _oval(bx, by, max(0.7, bead_r * (r / 32.0) * (0.75 + 0.35 * depth)),
              _hex(bead))
        fills.append(bead)

    # ---------------------------------------------------------------- nucleus
    _oval(cx, cy, max(1.1, r * NUCLEUS_FRAC) * (0.95 + 0.05 * gain),
          _hex(nuc_rgb))

    # ------------------------------------------------------------ near field
    near_tilt = TILT_A + RING_SWAY_DEG * math.sin(
        tt * 2.0 * math.pi / RING_PERIOD_NEAR)
    near_pts = _ellipse_points(cx, cy, rx, ry, near_tilt)
    near_dim = _mix(ring_rgb, _ember.NEAR_BLACK, 1.0 - 0.95 * bright)
    _line([p + (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(near_pts)],
          _hex(_mix(_FRINGE_BLUE, _ember.NEAR_BLACK, 0.25)), ring_w)
    _line([p - (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(near_pts)],
          _hex(_mix(_FRINGE_RED, _ember.NEAR_BLACK, 0.25)), ring_w)
    _line(near_pts, _hex(near_dim), ring_w)

    for orbit, seed, bead_r, dur in PARTICLES:
        if orbit != "a":
            continue
        ang = seed + (tt * 360.0 / max(0.1, dur))
        bx, by = _orbit_point(cx, cy, rx, ry, near_tilt, ang)
        depth = 0.5 + 0.5 * math.cos(math.radians(ang))
        bead = _mix(bead_rgb, _ember.NEAR_BLACK,
                    1.0 - (0.85 * bright + 0.15 * depth))
        _oval(bx, by, max(0.8, bead_r * (r / 32.0) * (0.8 + 0.45 * depth)),
              _hex(bead))
        fills.append(bead)

    if min_y == float("inf"):
        min_y = cy - r
    if max_y == float("-inf"):
        max_y = cy + r

    return {"ovals": ovals, "lines": lines, "points": points,
            "min_y": min_y, "max_y": max_y, "fills": fills,
            "bars": False}


__all__ = ["render", "PARTICLES", "resolve_core", "RX_FRAC", "RY_FRAC"]
