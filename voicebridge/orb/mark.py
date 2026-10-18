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
    GLINT_ARM_FRAC,
    GLINT_CORE_FRAC,
    SPARK_ARM_FRAC,
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
    echo_rings,
    satellites,
    SATELLITE_RING_ALPHA,
    ECHO_LAG_S,
    ECHO_SCALE,
    ECHO_ALPHA,
    ECHO_WIDTH,
    nucleus_sparks,
    NUCLEUS_SPARK_COUNT,
    NUCLEUS_SPARK_REACH,
    inner_ring_tilt,
    binary_state,
    INNER_RING_RX_FRAC,
    BINARY_COUNT,
)

#: Fringe offset in DEVICE pixels at 96px. A lens fringe is a pixel-level
#: artifact, not a scaled one: scaling it with the radius turned the mark into
#: three separate rainbow rings instead of a hint on one hairline. Kept as its
#: own name because the Qt renderer reads it too (``mark.FRINGE_PX_AT_96``).
FRINGE_PX_AT_96 = FRINGE_PX

#: Polyline sampling resolution for one ellipse.
RING_SEGMENTS = 72

#: Painter-side glint refinement, mirroring the Qt painter: the shared field
#: sets the size, but Tk's old arm/core floors kept tiny dust and sparkles
#: chunky. A low size floor, a smaller core and the field's own arm fraction
#: turn the crossing arms into pinpricks instead of fat crosses.
GLINT_MIN_SIZE = 0.16
GLINT_CORE_SCALE = 0.58
GLINT_CORE_MIN = 0.08

#: Shaded nucleus: concentric filled ovals approximate the radial gradient Tk
#: cannot draw (hot near-white core -> state hue -> fading rim), wrapped in a
#: soft outline halo. All layers are pure functions of the frame, so reduced
#: motion paints the identical static body.
NUCLEUS_LAYERS = 9
HALO_LAYERS = 3
NUCLEUS_OVAL_COUNT = HALO_LAYERS + NUCLEUS_LAYERS + 2

#: Master spin: a slow 360 deg rotation of the whole mark (period ~24s).
MASTER_SPIN_PERIOD_S = 24.0


def spin_deg(t: Any) -> float:
    try:
        tt = float(t)
    except (TypeError, ValueError):
        tt = 0.0
    if not math.isfinite(tt):
        tt = 0.0
    return 360.0 * tt / MASTER_SPIN_PERIOD_S


def _spin_pt(x: float, y: float, t: float = 0.0,
             cx: float = 0.0, cy: float = 0.0) -> Tuple[float, float]:
    a = math.radians(spin_deg(t))
    dx, dy = float(x) - float(cx), float(y) - float(cy)
    return (float(cx) + dx * math.cos(a) - dy * math.sin(a),
            float(cy) + dx * math.sin(a) + dy * math.cos(a))

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
    texts = 0
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

    def _bead(x: float, y: float, rad: float, lift: float, alpha: float,
              arm_frac: float = GLINT_ARM_FRAC) -> None:
        x, y = _spin_pt(x, y, tt, cx_f, cy_f)
        """A GLINT (four-point star), never an orb: the nucleus is the mark's
        only solid body. Two thin crossing arms + a small core read as a
        twinkle at orb size; a filled disc here looked like a marble."""
        if not (float(alpha) > 1e-3) or not (float(rad) > 0.0):
            return
        colour = _mix(fam["core"], (255, 255, 255), lift)
        shown = _dim(colour, float(alpha) * bright)
        sz = max(GLINT_MIN_SIZE, float(rad))
        arm = sz * arm_frac
        _line([x - arm, y, x + arm, y], shown, 1)
        _line([x, y - arm, x, y + arm], shown, 1)
        _oval(x, y, max(GLINT_CORE_MIN, sz * GLINT_CORE_FRAC * GLINT_CORE_SCALE),
              shown)
        fills.append(colour)

    def _beads(group: List[Dict[str, float]]) -> None:
        for b in group:
            _bead(cx_f + b["x"], cy_f + b["y"], b["size"], b["lift"], b["alpha"])

    def _text(x: float, y: float, char: str, size: float, lift: float,
              alpha: float) -> None:
        nonlocal texts, min_y, max_y
        try:
            xr, yr = _spin_pt(x, y, tt, cx_f, cy_f)
            colour = _mix(fam["core"], (255, 255, 255), lift)
            shown = _dim(colour, float(alpha) * bright)
            fs = max(6, int(float(size) * 28 + 4))
            canvas.create_text(xr, yr, text=str(char), fill=shown,
                               font=("TkDefaultFont", fs))
            texts += 1
            if yr < min_y:
                min_y = yr
            if yr > max_y:
                max_y = yr
        except Exception:
            pass

    def _ring(ccx: float, ccy: float, tilt: float,
              colour: Tuple[int, int, int], fringe_mix: float) -> None:
        """Three strokes: blue fringe, red fringe, then the hairline."""
        pts = _ellipse_points(ccx, ccy, rx, ry, tilt)
        _rot = []
        for _i in range(0, len(pts), 2):
            _rx, _ry = _spin_pt(pts[_i], pts[_i + 1], tt, cx_f, cy_f)
            _rot += [_rx, _ry]
        pts = _rot
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
        echoes = echo_rings(tt)
        shells = [] if reduced_motion else satellites(tt, r)
    except Exception:  # noqa: BLE001
        echoes = []
    try:
        sparks = nucleus_sparks(tt, r)
    except Exception:  # noqa: BLE001
        sparks = []
    try:
        comet_info = comet(tt, r)
    except Exception:  # noqa: BLE001
        comet_info = {"alpha": 0.0, "trail": []}
    if not isinstance(comet_info, dict):
        comet_info = {"alpha": 0.0, "trail": []}
    try:
        binaries = binary_state(tt, r)
    except Exception:  # noqa: BLE001
        binaries = []
    if not isinstance(binaries, list):
        binaries = []
    if reduced_motion:
        # A clean static frame: no dust, no glints, no comet, no trails, no
        # follower rings and no nucleus sparks.
        dust = []
        glints = []
        echoes = []
        sparks = []
        comet_info = {"alpha": 0.0, "trail": []}

    far_beads = [p for p in particles if float(p.get("depth", 0.0)) < 0.5]
    near_beads = [p for p in particles if float(p.get("depth", 0.0)) >= 0.5]
    far_dust = [d for d in dust if float(d.get("depth", 0.0)) < 0.5]
    near_dust = [d for d in dust if float(d.get("depth", 0.0)) >= 0.5]
    far_glints = [g for g in glints if float(g.get("depth", 0.0)) < 0.5]
    near_glints = [g for g in glints if float(g.get("depth", 0.0)) >= 0.5]
    far_binaries = [b for b in binaries if float(b.get("depth", 0.0)) < 0.5]
    near_binaries = [b for b in binaries if float(b.get("depth", 0.0)) >= 0.5]
    # Follower rings: orbit "a" rides the NEAR ring, orbit "b" the FAR one —
    # the same z-split the main orbits use, so the echo takes part in the
    # three-dimensional read instead of floating on top of it.
    far_echo = [e for e in echoes if str(e.get("orbit") or "b") != "a"]
    near_echo = [e for e in echoes if str(e.get("orbit") or "b") == "a"]
    far_sparks = [s for s in sparks if float(s.get("depth", 0.0)) < 0.5]
    near_sparks = [s for s in sparks if float(s.get("depth", 0.0)) >= 0.5]
    comet_on = (not reduced_motion) and float(comet_info.get("alpha", 0.0)) > 0.0

    def _echo_ring(ring: Dict[str, Any]) -> None:
        """One thin follower ring: outline-only, dimmer, lagged and scaled.

        Same ``orbit_point`` sampling as the main rings (never a re-derived
        geometry), one stroke and no chromatic fringe, mixed toward the
        background so it reads as a ghost trailing its orbit rather than a
        second decal.
        """
        if reduced_motion:
            return
        try:
            tilt = float(ring.get("tilt", TILT_A))
            rxs = rx * float(ring.get("rx_scale", ECHO_SCALE))
            rys = ry * float(ring.get("ry_scale", ECHO_SCALE))
            alpha = float(ring.get("alpha", ECHO_ALPHA)) * bright
            width = max(1, int(round(float(ring.get("width", ECHO_WIDTH)))))
            _line(_ellipse_points(ccx, ccy, rxs, rys, tilt),
                  _dim(ring_rgb, alpha), width)
        except Exception:  # noqa: BLE001
            pass

    def _satellite(shell: Dict[str, Any]) -> None:
        """One satellite shell: a thin CIRCLE and the small body on it."""
        try:
            ring = shell.get("ring") or {}
            body = shell.get("body") or {}
            rxs = rx * float(ring.get("rx_frac") or 0.0) / max(1e-9, RX_FRAC)
            rys = ry * float(ring.get("ry_frac") or 0.0) / max(1e-9, RY_FRAC)
            if rxs > 0.0 and rys > 0.0:
                pts = _ellipse_points(cx_f, cy_f, rxs, rys,
                                      float(ring.get("tilt") or 0.0))
                _line(pts, _dim(ring_rgb, float(ring.get("alpha")
                                               or SATELLITE_RING_ALPHA)),
                      int(ring.get("width") or 1))
            if not reduced_motion:
                for smp in shell.get("trail") or []:
                    _bead(cx_f + float(smp.get("x") or 0.0),
                          cy_f + float(smp.get("y") or 0.0),
                          float(smp.get("size") or 0.0),
                          float(smp.get("lift") or 0.0),
                          float(smp.get("alpha") or 0.0))
            _bead(cx_f + float(body.get("x") or 0.0),
                  cy_f + float(body.get("y") or 0.0),
                  float(body.get("size") or 0.0),
                  float(body.get("lift") or 0.0),
                  float(body.get("alpha") or 0.0))
        except Exception:  # noqa: BLE001
            pass

    def _draw_spark(s: Dict[str, Any]) -> None:
        """One nucleus spark as a glint: two crossing arms + a small core."""
        if reduced_motion:
            return
        try:
            _bead(cx_f + float(s["x"]), cy_f + float(s["y"]),
                  float(s["size"]), float(s.get("lift", 0.0)),
                  float(s["alpha"]), arm_frac=SPARK_ARM_FRAC)
        except Exception:  # noqa: BLE001
            pass

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
    for _ring_info in far_echo:
        _echo_ring(_ring_info)
    _beads(far_dust)
    for _p in far_beads:
        _draw_particle(_p)
    _beads(far_glints)
    for _b in far_binaries:
        _text(cx_f + float(_b.get("x") or 0.0), cy_f + float(_b.get("y") or 0.0),
              _b.get("char", "0"), float(_b.get("size") or 0.1),
              float(_b.get("lift") or 0.5), float(_b.get("alpha") or 0.2))
    for _s in far_sparks:
        _draw_spark(_s)
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

    # ------------------------------------------------------ equalizer (voice)
    # A live 12-band spectrum threads through as dim gradient bars at the
    # mark's seam: listening/speaking with a fresh spectrum draw real bars
    # (their peak-hold caps included) instead of nothing, while every other
    # state (or no spectrum) stays byte-identical — there is no audio to
    # analyse there. Bars carry no time phase, so reduced motion is static.
    bars_on = False
    try:
        key = str(state or "").strip().lower()
    except Exception:  # noqa: BLE001
        key = "idle"
    if key in ("listening", "speaking"):
        try:
            bands = _ember._spec_vector(spectrum)
            if bands is not None:
                caps = (_ember._spec_vector(spec_peaks)
                        if spec_peaks is not None else list(bands))
                if caps is None:
                    caps = list(bands)
                fam_bands = _ember.bands_from_family(fam)
                rects = _ember.spec_bar_rects(bands, caps, ccx, ccy, r, 1.0)
                for ri, rc in enumerate(rects):
                    base = _ember.band_shade(fam_bands, ri, tt)
                    top = rc["top"]
                    bottom = rc["bottom"]
                    span = bottom - top
                    steps = 1 if span < 2.0 else _ember.BAR_GRADIENT_STEPS
                    for si in range(steps):
                        f0 = si / steps
                        f1 = (si + 1) / steps
                        y_hi = top + span * f0
                        y_lo = top + span * f1
                        shade = 0.55 + _ember.BAR_TIP_GAIN * (1.0 - f0)
                        alpha = min(1.0, (0.24 + 0.66 * rc["value"])
                                    * shade * bright)
                        _line([rc["x"], y_hi, rc["x"], y_lo],
                              _hex(_mix(base, _ember.NEAR_BLACK, alpha)), 2)
                    hw = rc["half_w"]
                    cap_fill = _hex(_mix(base, _ember.NEAR_BLACK,
                                         min(1.0, 0.35 + 0.65 * bright)))
                    _line([rc["x"] - hw, rc["peak_top"],
                           rc["x"] + hw, rc["peak_top"]], cap_fill, 1)
                bars_on = True
        except Exception:  # noqa: BLE001 - a bad spectrum never breaks the frame
            bars_on = False

    # ------------------------------------------------------- seam: halo + nuc
    if reduced_motion:
        nscale, beat = 1.0, 0.0
    else:
        try:
            nscale, beat = nucleus_scale(tt), heartbeat(tt)
        except Exception:  # noqa: BLE001
            nscale, beat = 1.0, 0.0
    nuc_rad = max(1.1, r * NUCLEUS_FRAC) * 1.25 * nscale * (0.95 + 0.05 * gain)
    nuc_rad = nuc_rad * (1.0 + 0.08 * beat)
    halo_a = min(1.0, (HALO_BASE + (1.0 - HALO_BASE) * beat) * bright)
    # Inner ring: thin third orbit, low alpha, no fringe.
    try:
        _itilt = inner_ring_tilt(tt)
        _irx = r * INNER_RING_RX_FRAC
        _iry = _irx * (RY_FRAC / RX_FRAC)
        _ipts = _ellipse_points(ccx, ccy, _irx, _iry, _itilt)
        _irot = []
        for _i in range(0, len(_ipts), 2):
            _ix, _iy = _spin_pt(_ipts[_i], _ipts[_i + 1], tt, cx_f, cy_f)
            _irot += [_ix, _iy]
        _line(_irot, _dim(ring_rgb, 0.22 * bright), 1)
    except Exception:  # noqa: BLE001
        pass
    # Soft halo: dim outline rings (outline-only, never a disc), strongest on
    # the beat so the body reads as living even while the gradient stays still.
    halo_w = max(1, int(HALO_WIDTH))
    for hi in range(HALO_LAYERS):
        _ring_oval(ccx, ccy, nuc_rad * HALO_SCALE * (1.0 + 0.24 * hi),
                   _dim(nuc_rgb, halo_a * (1.0 - 0.5 * hi)), halo_w)
    # Shaded body: concentric filled ovals fake the radial gradient Tk cannot
    # draw — a fading rim, the state hue, then a hot near-white core. Drawn
    # outer to inner so the brightest layer lands on top.
    rim_rgb = _mix(nuc_rgb, _ember.NEAR_BLACK, 0.55 * (1.0 - 0.3 * bright))
    hot_rgb = _mix(nuc_rgb, (255, 255, 255), 0.78)
    for li in range(NUCLEUS_LAYERS):
        frac = li / max(1, NUCLEUS_LAYERS - 1)
        if frac <= 0.5:
            layer_rgb = _mix(rim_rgb, nuc_rgb, frac / 0.5)
        else:
            layer_rgb = _mix(nuc_rgb, hot_rgb, (frac - 0.5) / 0.5)
        _oval(ccx, ccy, nuc_rad * (1.0 - 0.70 * frac), _hex(layer_rgb))
    # animated inner swirl highlight
    _sa = math.radians(tt * 360.0 / 6.0)
    _sx, _sy = _spin_pt(ccx + nuc_rad * 0.22 * math.cos(_sa),
                        ccy + nuc_rad * 0.22 * math.sin(_sa),
                        tt, cx_f, cy_f)
    _oval(_sx, _sy, max(0.6, nuc_rad * 0.30),
          _hex(_mix(hot_rgb, (255, 255, 255), 0.5)))
    # slowly orbiting specular dot
    _pa = math.radians(tt * 360.0 / 12.0 + 90.0)
    _px, _py = _spin_pt(ccx + nuc_rad * 0.50 * math.cos(_pa),
                        ccy + nuc_rad * 0.50 * math.sin(_pa),
                        tt, cx_f, cy_f)
    _oval(_px, _py, max(0.5, nuc_rad * 0.07), "#ffffff")
    fills.append(nuc_rgb)

    # ------------------------------------------------------------ near field
    near_dim = _mix(ring_rgb, _ember.NEAR_BLACK, 1.0 - 0.95 * bright)
    try:
        _ring(ccx, ccy, near_tilt, near_dim, 0.25)
    except Exception:  # noqa: BLE001
        pass
    for _ring_info in near_echo:
        _echo_ring(_ring_info)
    _beads(near_dust)
    for _p in near_beads:
        _draw_particle(_p)
    _beads(near_glints)
    for _b in near_binaries:
        _text(cx_f + float(_b.get("x") or 0.0), cy_f + float(_b.get("y") or 0.0),
              _b.get("char", "0"), float(_b.get("size") or 0.1),
              float(_b.get("lift") or 0.5), float(_b.get("alpha") or 0.2))
    for _s in near_sparks:
        _draw_spark(_s)
    if comet_on:
        _bead(cx_f + comet_info["x"], cy_f + comet_info["y"],
              comet_info["size"], comet_info["lift"], comet_info["alpha"])

    if min_y == float("inf"):
        min_y = cy_f - r
    if max_y == float("-inf"):
        max_y = cy_f + r

    return {"ovals": ovals, "lines": lines, "points": points, "texts": texts,
            "min_y": min_y, "max_y": max_y, "fills": fills,
            "bars": bars_on}


__all__ = [
    "render", "resolve_core", "PARTICLES", "RING_SEGMENTS",
    "GLINT_MIN_SIZE", "GLINT_CORE_SCALE", "GLINT_CORE_MIN",
    "NUCLEUS_LAYERS", "HALO_LAYERS", "NUCLEUS_OVAL_COUNT",
    "MASTER_SPIN_PERIOD_S", "spin_deg", "_spin_pt",
    "RX_FRAC", "RY_FRAC", "TILT_A", "TILT_B", "NUCLEUS_FRAC",
    "FRINGE_PX", "FRINGE_PX_AT_96", "LIFT_RING", "LIFT_BEAD",
    "LIFT_NUCLEUS", "MIN_MARK_BRIGHTNESS", "HALO_SCALE", "HALO_BASE",
    "HALO_WIDTH", "SHOCKWAVE_WIDTH", "RING_SWAY_DEG", "RING_PERIOD_NEAR",
    "RING_PERIOD_FAR",
    "heartbeat", "nucleus_scale", "shockwave", "camera_offset",
    "orbit_tilts", "orbit_point", "particle_state", "trails",
    "dust_state", "comet", "sparkles",
    "echo_rings", "satellites", "ECHO_LAG_S", "ECHO_SCALE", "ECHO_ALPHA", "ECHO_WIDTH",
    "nucleus_sparks", "NUCLEUS_SPARK_COUNT", "NUCLEUS_SPARK_REACH",
    "inner_ring_tilt", "binary_state", "INNER_RING_RX_FRAC", "BINARY_COUNT",
]
