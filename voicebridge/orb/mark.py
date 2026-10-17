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

# -- heartbeat nucleus (owner 2026-10-17: the mark must live, but stay crisp) --
#
# A double thump: a primary beat every HEART_PERIOD_S, a second thump at
# HEART_SECOND_AMP of the primary amplitude HEART_SECOND_PHASE into the cycle,
# each HEART_THUMP_S wide (a raised-cosine pulse, never a square edge). The
# envelope is 1.00 -> 1.18 -> 1.00. A thin halo ring rides the same beat at
# 1.6x the nucleus radius (a hairline outline, NOT a filled glow disc: Tk has
# no alpha or blur, so the ring is dimmed toward the background instead).
HEART_PERIOD_S = 0.95
HEART_SCALE_AMP = 0.18
HEART_SECOND_AMP = 0.62
HEART_SECOND_PHASE = 0.22
HEART_THUMP_S = 0.16
HALO_SCALE = 1.6
HALO_BASE = 0.22
HALO_WIDTH = 1

# -- particle depth (3-D near/far read) ------------------------------------
#
# depth = 0.5 + 0.5*cos(radians(angle)); the FAR half (depth < 0.5) draws
# behind the nucleus, the NEAR half in front. Radius, opacity and white lift
# are all linear in depth; beads also slide along the radial by
# (depth-0.5)*DEPTH_PARALLAX*radius (toward the centre on the far side).
DEPTH_RADIUS_BASE = 0.55
DEPTH_RADIUS_GAIN = 0.75
DEPTH_OPACITY_BASE = 0.35
DEPTH_OPACITY_GAIN = 0.65
DEPTH_LIFT_BASE = 0.05
DEPTH_LIFT_GAIN = 0.30
DEPTH_PARALLAX = 0.06

# -- particle trails -------------------------------------------------------
#
# TRAIL_COUNT fading samples behind each bead at angle - k*STEP (STEP is
# TRAIL_STEP_NEAR on orbit "a", TRAIL_STEP_FAR on orbit "b"), each at
# TRAIL_RADIUS_SCALE of the bead radius and alpha TRAIL_ALPHA*(1 - k/COUNT).
TRAIL_COUNT = 4
TRAIL_STEP_NEAR = 6.0
TRAIL_STEP_FAR = 4.0
TRAIL_ALPHA = 0.28
TRAIL_RADIUS_SCALE = 0.55

# -- deterministic sparkles ------------------------------------------------
#
# 14 glints, a pure function of the clock (never ``random``). Glint i:
# seed = i*SPARKLE_SEED_STEP; angle = seed + t*(SPARKLE_SPEED_BASE +
# (i%5)*SPARKLE_SPEED_STEP); radius in SPARKLE_R0..SPARKLE_R1 * radius;
# projected with the near orbit's tilt; life = SPARKLE_LIFE_BASE +
# (i%4)*SPARKLE_LIFE_STEP; phase = (t/life + seed/360) % 1, visible only in
# the first SPARKLE_VISIBLE_FRAC of the cycle with a raised-cosine fade.
SPARKLE_COUNT = 14
SPARKLE_SEED_STEP = 97.3
SPARKLE_SPEED_BASE = 18.0
SPARKLE_SPEED_STEP = 6.0
SPARKLE_LIFE_BASE = 1.1
SPARKLE_LIFE_STEP = 0.35
SPARKLE_VISIBLE_FRAC = 0.45
SPARKLE_R0 = 0.30
SPARKLE_R1 = 1.05
SPARKLE_RADIUS_BASE = 0.5
SPARKLE_RADIUS_GAIN = 0.9
SPARKLE_WHITE_MIX = 0.55
#: Golden-ratio low-discrepancy step for the deterministic sparkle radii.
_GOLDEN = 0.6180339887498949

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


def _bump(frac: float) -> float:
    """Raised-cosine bump 0 -> 1 -> 0 over ``frac`` in [0, 1]; 0 outside."""
    if frac <= 0.0 or frac >= 1.0:
        return 0.0
    return 0.5 * (1.0 - math.cos(2.0 * math.pi * frac))


def _finite(v: Any, default: float = 0.0) -> float:
    """Float ``v`` or ``default`` when malformed / non-finite. Never raises."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def heart_envelope(tt: float) -> float:
    """Nucleus scale envelope (a smooth double thump). Never raises.

    1.00 at rest, up to 1.00 + HEART_SCALE_AMP on the primary beat; the
    secondary thump peaks at HEART_SECOND_AMP of that. Both are
    raised-cosine pulses HEART_THUMP_S wide, so the beat never snaps.
    A malformed / non-finite clock rests at 1.00.
    """
    try:
        f = float(tt)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(f):
        return 1.0
    phase = (f / HEART_PERIOD_S) % 1.0
    half = (HEART_THUMP_S / HEART_PERIOD_S) * 0.5

    def _thump(center: float, amp: float) -> float:
        d = abs((phase - center + 0.5) % 1.0 - 0.5)
        if d >= half:
            return 0.0
        return amp * 0.5 * (1.0 + math.cos(math.pi * d / half))

    return (1.0
            + _thump(0.0, HEART_SCALE_AMP)
            + _thump(HEART_SECOND_PHASE, HEART_SCALE_AMP * HEART_SECOND_AMP))


def heart_beat(tt: float) -> float:
    """Primary-thump strength 0..1 for clock ``tt`` (drives the halo)."""
    f = _finite(tt)
    phase = (f / HEART_PERIOD_S) % 1.0
    half = (HEART_THUMP_S / HEART_PERIOD_S) * 0.5
    d = abs((phase + 0.5) % 1.0 - 0.5)
    if d >= half:
        return 0.0
    return 0.5 * (1.0 + math.cos(math.pi * d / half))


def orbit_tilts(tt: float) -> Tuple[float, float]:
    """(near_tilt, far_tilt) in degrees for the animated mark clock."""
    t_f = _finite(tt)
    near = TILT_A + RING_SWAY_DEG * math.sin(
        t_f * 2.0 * math.pi / RING_PERIOD_NEAR)
    far = TILT_B + RING_SWAY_DEG * math.sin(
        t_f * 2.0 * math.pi / RING_PERIOD_FAR)
    return (near, far)


def particle_frames(cx: float, cy: float, radius: float, tt: float,
                    near_tilt: float, far_tilt: float) -> List[Dict[str, Any]]:
    """Pure geometry for every particle at clock ``tt`` (incl. parallax).

    Each frame carries ``orbit``, ``ang``, ``depth``, the depth-scaled
    ``x``/``y`` (raw orbit point plus the radial parallax slide) and the
    ``tilt``/``seed``/``bead_r``/``dur`` it rides. Never raises.
    """
    frames: List[Dict[str, Any]] = []
    try:
        rx = float(radius) * RX_FRAC
        ry = float(radius) * RY_FRAC
        cxf, cyf = float(cx), float(cy)
    except (TypeError, ValueError):
        return frames
    tt_f = _finite(tt)
    for orbit, seed, bead_r, dur in PARTICLES:
        tilt = near_tilt if orbit == "a" else far_tilt
        ang = seed + (tt_f * 360.0 / max(0.1, dur))
        depth = 0.5 + 0.5 * math.cos(math.radians(ang))
        bx, by = _orbit_point(cxf, cyf, rx, ry, tilt, ang)
        ox, oy = bx - cxf, by - cyf
        dist = math.hypot(ox, oy)
        if dist > 1e-9:
            slide = (depth - 0.5) * DEPTH_PARALLAX * float(radius)
            bx += ox / dist * slide
            by += oy / dist * slide
        frames.append({"orbit": orbit, "seed": seed, "bead_r": bead_r,
                       "dur": dur, "ang": ang, "depth": depth,
                       "x": bx, "y": by, "tilt": tilt})
    return frames


def sparkles(cx: float, cy: float, radius: float, tt: float,
             nuc_rgb: Tuple[int, int, int],
             near_tilt: float) -> List[Dict[str, Any]]:
    """Deterministic glints for clock ``tt`` (pure; never ``random``).

    Returns the visible glints only (the first SPARKLE_VISIBLE_FRAC of each
    glint's life, raised-cosine faded), each ``{i, x, y, rad, peak, rgb}``.
    Projected with the near orbit's tilt. Never raises.
    """
    out: List[Dict[str, Any]] = []
    try:
        cxf, cyf, r_f = float(cx), float(cy), float(radius)
        tilt = math.radians(near_tilt)
        cos_t, sin_t = math.cos(tilt), math.sin(tilt)
    except (TypeError, ValueError):
        return out
    tt_f = _finite(tt)
    col = _mix(nuc_rgb, (255, 255, 255), SPARKLE_WHITE_MIX)
    for i in range(SPARKLE_COUNT):
        seed = i * SPARKLE_SEED_STEP
        life = SPARKLE_LIFE_BASE + (i % 4) * SPARKLE_LIFE_STEP
        phase = (tt_f / life + seed / 360.0) % 1.0
        if phase >= SPARKLE_VISIBLE_FRAC:
            continue
        peak = _bump(phase / SPARKLE_VISIBLE_FRAC)
        if peak <= 1e-6:
            continue
        frac = (i * _GOLDEN) % 1.0
        dist = r_f * (SPARKLE_R0 + (SPARKLE_R1 - SPARKLE_R0) * frac)
        ang = math.radians(seed + tt_f * (SPARKLE_SPEED_BASE
                                          + (i % 5) * SPARKLE_SPEED_STEP))
        sx = dist * math.cos(ang)
        sy = dist * math.sin(ang)
        out.append({
            "i": i,
            "x": cxf + sx * cos_t - sy * sin_t,
            "y": cyf + sx * sin_t + sy * cos_t,
            "rad": SPARKLE_RADIUS_BASE + SPARKLE_RADIUS_GAIN * peak,
            "peak": peak,
            "rgb": _mix(col, _ember.NEAR_BLACK, 1.0 - peak),
        })
    return out


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
    if not math.isfinite(t_val):
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

    # ------------------------------------------------------------- far field
    # Drawn first so the nucleus and the near field sit over it (depth).
    bright = max(MIN_MARK_BRIGHTNESS, float(brightness or 0.0))
    ring_rgb = _mix(fam["core"], (255, 255, 255), LIFT_RING)
    nuc_rgb = _mix(fam["core"], (255, 255, 255), LIFT_NUCLEUS)
    near_tilt, far_tilt = orbit_tilts(tt)
    frames = particle_frames(cx, cy, r, tt, near_tilt, far_tilt)
    far_beads = [f for f in frames if f["depth"] < 0.5]
    near_beads = [f for f in frames if f["depth"] >= 0.5]
    draw_trails = not reduced_motion

    def _draw_particle(fr: Dict[str, Any]) -> None:
        depth = fr["depth"]
        step = TRAIL_STEP_NEAR if fr["orbit"] == "a" else TRAIL_STEP_FAR
        raw_x, raw_y = _orbit_point(cx, cy, rx, ry, fr["tilt"], fr["ang"])
        slide_x = fr["x"] - raw_x
        slide_y = fr["y"] - raw_y
        colour = _mix(fam["core"], (255, 255, 255),
                      DEPTH_LIFT_BASE + DEPTH_LIFT_GAIN * depth)
        opacity = DEPTH_OPACITY_BASE + DEPTH_OPACITY_GAIN * depth
        bead_rad = max(0.7, fr["bead_r"] * (r / 32.0)
                       * (DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth))
        if draw_trails:
            for k in range(1, TRAIL_COUNT + 1):
                t_alpha = TRAIL_ALPHA * (1.0 - k / float(TRAIL_COUNT))
                if t_alpha <= 1e-6:
                    continue
                tx, ty = _orbit_point(cx, cy, rx, ry, fr["tilt"],
                                      fr["ang"] - k * step)
                _oval(tx + slide_x, ty + slide_y,
                      bead_rad * TRAIL_RADIUS_SCALE,
                      _hex(_mix(colour, _ember.NEAR_BLACK, 1.0 - t_alpha)))
        _oval(fr["x"], fr["y"], bead_rad,
              _hex(_mix(colour, _ember.NEAR_BLACK, 1.0 - opacity)))
        fills.append(colour)

    far_pts = _ellipse_points(cx, cy, rx, ry, far_tilt)
    ring_dim = _mix(ring_rgb, _ember.NEAR_BLACK, 1.0 - 0.60 * bright)
    _line([p + (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(far_pts)],
          _hex(_mix(_FRINGE_BLUE, _ember.NEAR_BLACK, 0.45)), ring_w)
    _line([p - (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(far_pts)],
          _hex(_mix(_FRINGE_RED, _ember.NEAR_BLACK, 0.45)), ring_w)
    _line(far_pts, _hex(ring_dim), ring_w)

    for fr in far_beads:
        _draw_particle(fr)

    # ---------------------------------------------------------------- nucleus
    # A heartbeat scale envelope (double thump) + a thin halo ring that
    # brightens on the beat. Reduced motion rests: scale 1.0, no beat.
    if reduced_motion:
        heart, beat = 1.0, 0.0
    else:
        heart, beat = heart_envelope(tt), heart_beat(tt)
    nuc_rad = max(1.1, r * NUCLEUS_FRAC) * (0.95 + 0.05 * gain) * heart
    halo_rgb = _mix(nuc_rgb, _ember.NEAR_BLACK,
                    1.0 - min(1.0, (HALO_BASE + (1.0 - HALO_BASE) * beat)
                              * bright))
    _ring_oval(cx, cy, nuc_rad * HALO_SCALE, _hex(halo_rgb), HALO_WIDTH)
    _oval(cx, cy, nuc_rad, _hex(nuc_rgb))
    fills.append(nuc_rgb)

    # ------------------------------------------------------------ near field
    near_pts = _ellipse_points(cx, cy, rx, ry, near_tilt)
    near_dim = _mix(ring_rgb, _ember.NEAR_BLACK, 1.0 - 0.95 * bright)
    _line([p + (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(near_pts)],
          _hex(_mix(_FRINGE_BLUE, _ember.NEAR_BLACK, 0.25)), ring_w)
    _line([p - (fringe if i % 2 == 0 else 0.0) for i, p in enumerate(near_pts)],
          _hex(_mix(_FRINGE_RED, _ember.NEAR_BLACK, 0.25)), ring_w)
    _line(near_pts, _hex(near_dim), ring_w)

    for fr in near_beads:
        _draw_particle(fr)

    # -------------------------------------------------------------- sparkles
    if not reduced_motion:
        for sp in sparkles(cx, cy, r, tt, nuc_rgb, near_tilt):
            _oval(sp["x"], sp["y"], sp["rad"], _hex(sp["rgb"]))
            fills.append(sp["rgb"])

    if min_y == float("inf"):
        min_y = cy - r
    if max_y == float("-inf"):
        max_y = cy + r

    return {"ovals": ovals, "lines": lines, "points": points,
            "min_y": min_y, "max_y": max_y, "fills": fills,
            "bars": False}


__all__ = [
    "render", "PARTICLES", "resolve_core", "RX_FRAC", "RY_FRAC",
    "HEART_PERIOD_S", "HEART_SCALE_AMP", "HEART_SECOND_AMP",
    "HEART_SECOND_PHASE", "HEART_THUMP_S", "HALO_SCALE", "HALO_BASE",
    "HALO_WIDTH", "DEPTH_RADIUS_BASE", "DEPTH_RADIUS_GAIN",
    "DEPTH_OPACITY_BASE", "DEPTH_OPACITY_GAIN", "DEPTH_LIFT_BASE",
    "DEPTH_LIFT_GAIN", "DEPTH_PARALLAX", "TRAIL_COUNT", "TRAIL_STEP_NEAR",
    "TRAIL_STEP_FAR", "TRAIL_ALPHA", "TRAIL_RADIUS_SCALE", "SPARKLE_COUNT",
    "SPARKLE_SEED_STEP", "SPARKLE_SPEED_BASE", "SPARKLE_SPEED_STEP",
    "SPARKLE_LIFE_BASE", "SPARKLE_LIFE_STEP", "SPARKLE_VISIBLE_FRAC",
    "SPARKLE_R0", "SPARKLE_R1", "SPARKLE_RADIUS_BASE",
    "SPARKLE_RADIUS_GAIN", "SPARKLE_WHITE_MIX", "orbit_tilts",
    "particle_frames", "sparkles", "heart_envelope", "heart_beat",
]
