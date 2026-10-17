"""Qubit-mark orb renderer: the dashboard sidebar logo as the desktop orb.

Same visual language as ``frontend/qubit-web/src/components/QubitMark.tsx`` +
``styles/qubit-mark.css``: a hairline construction drawing, never a glow sticker.

  * two thin ellipse orbits (tilted -30deg / +30deg) that precess slowly,
  * a small solid nucleus at the centre, beating,
  * nine particles riding each ellipse, each at its own period and orbital seed,
  * a chromatic fringe faked on the hairline rings (Tk has no blend modes, so
    the fringe is two extra offset strokes in dark red / blue on near-black).

ALL of the motion — orbits, particles, trails, dust, comet, beat and glints —
comes from :mod:`voicebridge.orb.mark_field`, the single deterministic
description shared with the Qt renderer. This module is ONLY the Tk translator:
it consumes the field, adds ``(cx, cy)`` to the centre-relative coordinates the
field returns, and draws with the techniques Tk actually has. It never
re-derives geometry or re-implements the field's maths.

State meaning is preserved from ``ember`` (the single palette source): the
``STATE_CORES`` hue still tints nucleus, beads and rings, so idle / listening /
thinking / working / speaking / error read exactly as before. Geometry that the
host owns (window placement, drag, click, collapse dot, indicator dots) is
untouched.

Tk has no rotated ellipse and no per-item alpha, so:

  * an ellipse is drawn as a smoothed closed polyline sampled from the field's
    ``orbit_point`` (``_ellipse_points``);
  * "opacity" is a mix toward the near-black background, precomputed per bead
    (``_mix`` + ``_hex``); circles are ``create_oval``.

Depth is the 3-D read: the field splits every element at ``depth = 0.5`` and
this renderer draws the far half behind the nucleus, the halo/shockwave at the
seam, and the near half in front.

This module never opens a window: ``render`` takes any canvas-like object with
``create_oval``/``create_line`` (a hermetic fake works for proof).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from . import ember as _ember
from .mark_field import (
    PARTICLES,
    RX_FRAC,
    RY_FRAC,
    TILT_A,
    TILT_B,
    NUCLEUS_FRAC,
    FRINGE_PX,
    LIFT_RING,
    LIFT_BEAD,
    LIFT_NUCLEUS,
    MIN_MARK_BRIGHTNESS,
    HALO_SCALE,
    HALO_BASE,
    HALO_WIDTH,
    SHOCKWAVE_WIDTH,
    RING_SWAY_DEG,
    RING_PERIOD_NEAR,
    RING_PERIOD_FAR,
    heartbeat,
    nucleus_scale,
    shockwave,
    camera_offset,
    orbit_tilts,
    orbit_point,
    particle_state,
    trails,
    dust_state,
    comet,
    sparkles,
)

#: Fringe offset in DEVICE pixels at 96px. A lens fringe is a pixel-level
#: artifact, not a scaled one: scaling it with the radius turned the mark into
#: three separate rainbow rings instead of a hint on one hairline. Kept as its
#: own name because the Qt renderer reads it too (``mark.FRINGE_PX_AT_96``).
FRINGE_PX_AT_96 = FRINGE_PX

#: Polyline sampling resolution for one ellipse.
RING_SEGMENTS = 72

#: Fringe channel colors, mixed toward the background (Tk alpha substitute).
_FRINGE_RED = (150, 30, 55)
_FRINGE_BLUE = (30, 80, 160)

#: Signature-compatible alias: the Qt renderer calls ``_mark._orbit_point``.
_orbit_point = orbit_point


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
    """Closed smoothed polyline for a tilted ellipse (x0,y0,x1,y1,...).

    Sampled through the field's ``orbit_point`` so ring geometry and particle
    geometry can never disagree about where the ellipse is.
    """
    pts: List[float] = []
    for i in range(RING_SEGMENTS):
        deg = (360.0 * i) / RING_SEGMENTS
        x, y = orbit_point(cx, cy, rx, ry, tilt_deg, deg)
        pts.append(x)
        pts.append(y)
    return pts


def render(canvas: Any, cx: float, cy: float, radius: float, t: float,
           state: Any = "idle", reduced_motion: bool = False,
           level: Any = None, spectrum: Any = None,
           spec_peaks: Any = None, colors: Any = None,
           speed_scale: float = 1.0) -> Dict[str, Any]:
    """Draw one Qubit-mark frame on a canvas-like object.

    Signature and return shape mirror :func:`voicebridge.orb.ember.render` so
    the host can swap renderers without touching frame discipline. The motion
    is read whole from :mod:`voicebridge.orb.mark_field`; ``level`` (0..1 talk
    meter) still visibly pumps the nucleus while listening, and
    ``reduced_motion`` freezes the clock at ``t=0`` for a clean static frame
    (no sparkles, trails, comet, shockwave or dust; nucleus at rest).
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
    if not math.isfinite(r) or r <= 0:
        r = 32.0
    try:
        cx_f = float(cx)
    except (TypeError, ValueError):
        cx_f = 0.0
    try:
        cy_f = float(cy)
    except (TypeError, ValueError):
        cy_f = 0.0

    core = resolve_core(state, colors)
    fam = _ember.family_from_core(core)

    try:
        params = _ember.resolve_params(state)
        brightness = float(params.get("brightness", 1.0))
    except Exception:  # noqa: BLE001 - unknown state renders as idle
        brightness = 1.0
    bright = max(MIN_MARK_BRIGHTNESS, float(brightness or 0.0))
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
    # The host advances/clamps its own clock; reduced motion freezes it at 0
    # so two wall-clock times paint byte-identical frames.
    try:
        t_val = float(t)
    except (TypeError, ValueError):
        t_val = 0.0
    if not math.isfinite(t_val):
        t_val = 0.0
    tt = 0.0 if reduced_motion else t_val * speed

    rx = r * RX_FRAC
    ry = r * RY_FRAC
    fringe = FRINGE_PX * (r / 32.0)
    ring_w = 1 if r < 64 else 2
    ring_rgb = _mix(fam["core"], (255, 255, 255), LIFT_RING)
    nuc_rgb = _mix(fam["core"], (255, 255, 255), LIFT_NUCLEUS)

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

    def _ring_oval(x: float, y: float, rad: float, colour: str,
                   width: int = 1) -> None:
        """Outline-only oval (Tk's alpha substitute: a dim hairline ring)."""
        nonlocal ovals, min_y, max_y
        try:
            canvas.create_oval(x - rad, y - rad, x + rad, y + rad,
                               fill="", outline=colour, width=width)
            ovals += 1
            if y - rad < min_y:
                min_y = y - rad
            if y + rad > max_y:
                max_y = y + rad
        except Exception:
            pass

    def _dim(colour: Tuple[int, int, int], alpha: float) -> str:
        """Tk alpha substitute: mix the colour toward the near-black bg."""
        a = 1.0 - max(0.0, min(1.0, float(alpha)))
        return _hex(_mix(colour, _ember.NEAR_BLACK, a))

    def _bead(x: float, y: float, rad: float, lift: float, alpha: float) -> None:
        """One filled circle: field radius/alpha/lift, dimmed by state."""
        if not (float(alpha) > 1e-3) or not (float(rad) > 0.0):
            return
        colour = _mix(fam["core"], (255, 255, 255), lift)
        _oval(x, y, max(0.4, float(rad)), _dim(colour, float(alpha) * bright))
        fills.append(colour)

    def _beads(group: List[Dict[str, float]]) -> None:
        for b in group:
            _bead(cx_f + b["x"], cy_f + b["y"], b["size"], b["lift"], b["alpha"])

    def _ring(ccx: float, ccy: float, tilt: float,
              colour: Tuple[int, int, int], fringe_mix: float) -> None:
        """Three strokes: blue fringe, red fringe, then the hairline."""
        pts = _ellipse_points(ccx, ccy, rx, ry, tilt)
        blue = _hex(_mix(_FRINGE_BLUE, _ember.NEAR_BLACK, fringe_mix))
        red = _hex(_mix(_FRINGE_RED, _ember.NEAR_BLACK, fringe_mix))
        _line([p + (fringe if i % 2 == 0 else 0.0)
               for i, p in enumerate(pts)], blue, ring_w)
        _line([p - (fringe if i % 2 == 0 else 0.0)
               for i, p in enumerate(pts)], red, ring_w)
        _line(pts, _hex(colour), ring_w)

    # ----------------------------------------------------------------- field
    # Every layer is one pure call; malformed input (already normalised) or a
    # future field change can never take the frame down.
    try:
        near_tilt, far_tilt = orbit_tilts(tt)
        ox, oy = camera_offset(tt, r)
    except Exception:  # noqa: BLE001
        near_tilt, far_tilt, ox, oy = TILT_A, TILT_B, 0.0, 0.0
    ccx, ccy = cx_f + ox, cy_f + oy

    try:
        particles = particle_state(tt, r)
    except Exception:  # noqa: BLE001
        particles = []
    try:
        dust = dust_state(tt, r)
    except Exception:  # noqa: BLE001
        dust = []
    try:
        glints = sparkles(tt, r)
    except Exception:  # noqa: BLE001
        glints = []
    try:
        comet_info = comet(tt, r)
    except Exception:  # noqa: BLE001
        comet_info = {"alpha": 0.0, "trail": []}
    if not isinstance(comet_info, dict):
        comet_info = {"alpha": 0.0, "trail": []}
    if reduced_motion:
        # A clean static frame: no dust, no glints, no comet, no trails.
        dust = []
        glints = []
        comet_info = {"alpha": 0.0, "trail": []}

    far_beads = [p for p in particles if float(p.get("depth", 0.0)) < 0.5]
    near_beads = [p for p in particles if float(p.get("depth", 0.0)) >= 0.5]
    far_dust = [d for d in dust if float(d.get("depth", 0.0)) < 0.5]
    near_dust = [d for d in dust if float(d.get("depth", 0.0)) >= 0.5]
    far_glints = [g for g in glints if float(g.get("depth", 0.0)) < 0.5]
    near_glints = [g for g in glints if float(g.get("depth", 0.0)) >= 0.5]
    comet_on = (not reduced_motion) and float(comet_info.get("alpha", 0.0)) > 0.0

    def _draw_particle(p: Dict[str, float]) -> None:
        if not reduced_motion:
            try:
                tail = trails(tt, r, p)
            except Exception:  # noqa: BLE001
                tail = []
            for s in tail:
                _bead(cx_f + s["x"], cy_f + s["y"], s["size"], s["lift"],
                      s["alpha"])
        _bead(cx_f + p["x"], cy_f + p["y"], p["size"], p["lift"], p["alpha"])

    # ------------------------------------------------------------- far field
    # Behind the nucleus: far orbit, far dust, far particles + trails, far
    # glints, then the comet's long tail.
    far_dim = _mix(ring_rgb, _ember.NEAR_BLACK, 1.0 - 0.60 * bright)
    try:
        _ring(ccx, ccy, far_tilt, far_dim, 0.45)
    except Exception:  # noqa: BLE001
        pass
    _beads(far_dust)
    for _p in far_beads:
        _draw_particle(_p)
    _beads(far_glints)
    if comet_on:
        for s in comet_info.get("trail", []):
            if float(s.get("alpha", 0.0)) > 0.0:
                _bead(cx_f + s["x"], cy_f + s["y"], s["size"], s["lift"],
                      s["alpha"])

    # --------------------------------------------------------- beat shockwave
    # Outline-only tilted ellipse at nucleus_radius*scale, strongest at the
    # beat; no filled disc (Tk has no alpha to fake one convincingly).
    if not reduced_motion:
        try:
            sw_scale, sw_alpha = shockwave(tt)
        except Exception:  # noqa: BLE001
            sw_scale, sw_alpha = 0.0, 0.0
        if sw_scale > 0.0 and sw_alpha > 0.0:
            sw_r = max(1.1, r * NUCLEUS_FRAC) * sw_scale
            sw_pts = _ellipse_points(ccx, ccy, sw_r,
                                     sw_r * (RY_FRAC / RX_FRAC), near_tilt)
            _line(sw_pts, _dim(ring_rgb, sw_alpha * bright),
                  max(1, int(round(SHOCKWAVE_WIDTH))))

    # ------------------------------------------------------- seam: halo + nuc
    if reduced_motion:
        nscale, beat = 1.0, 0.0
    else:
        try:
            nscale, beat = nucleus_scale(tt), heartbeat(tt)
        except Exception:  # noqa: BLE001
            nscale, beat = 1.0, 0.0
    nuc_rad = max(1.1, r * NUCLEUS_FRAC) * nscale * (0.95 + 0.05 * gain)
    halo_a = min(1.0, (HALO_BASE + (1.0 - HALO_BASE) * beat) * bright)
    _ring_oval(ccx, ccy, nuc_rad * HALO_SCALE, _dim(nuc_rgb, halo_a),
               max(1, int(HALO_WIDTH)))
    _oval(ccx, ccy, nuc_rad, _hex(nuc_rgb))
    fills.append(nuc_rgb)

    # ------------------------------------------------------------ near field
    near_dim = _mix(ring_rgb, _ember.NEAR_BLACK, 1.0 - 0.95 * bright)
    try:
        _ring(ccx, ccy, near_tilt, near_dim, 0.25)
    except Exception:  # noqa: BLE001
        pass
    _beads(near_dust)
    for _p in near_beads:
        _draw_particle(_p)
    _beads(near_glints)
    if comet_on:
        _bead(cx_f + comet_info["x"], cy_f + comet_info["y"],
              comet_info["size"], comet_info["lift"], comet_info["alpha"])

    if min_y == float("inf"):
        min_y = cy_f - r
    if max_y == float("-inf"):
        max_y = cy_f + r

    return {"ovals": ovals, "lines": lines, "points": points,
            "min_y": min_y, "max_y": max_y, "fills": fills,
            "bars": False}


__all__ = [
    "render", "resolve_core", "PARTICLES", "RING_SEGMENTS",
    "RX_FRAC", "RY_FRAC", "TILT_A", "TILT_B", "NUCLEUS_FRAC",
    "FRINGE_PX", "FRINGE_PX_AT_96", "LIFT_RING", "LIFT_BEAD",
    "LIFT_NUCLEUS", "MIN_MARK_BRIGHTNESS", "HALO_SCALE", "HALO_BASE",
    "HALO_WIDTH", "SHOCKWAVE_WIDTH", "RING_SWAY_DEG", "RING_PERIOD_NEAR",
    "RING_PERIOD_FAR",
    "heartbeat", "nucleus_scale", "shockwave", "camera_offset",
    "orbit_tilts", "orbit_point", "particle_state", "trails",
    "dust_state", "comet", "sparkles",
]
