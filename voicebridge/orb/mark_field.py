"""Rich animation field for the Qubit-mark orb.

ONE deterministic description of everything that moves, shared by both orb
renderers (Qt live orb and the Tk fallback) so they cannot drift apart. Pure
functions of ``(t, R)`` only — no ``random``, no state, no I/O — which is what
makes the motion reproducible frame by frame, testable, and identical across
backends.

Layers, back to front:

  * ``camera_offset``   — slow floating sway of the whole mark (fakes depth).
  * ``orbit_tilts``     — the two orbit tilts, plus a slow precession sway.
  * ``ring_geometry``   — every circle of the mark as one stroke description,
    with ``ring_path``/``ring_alpha`` for the points and the breathing alpha.
  * ``heartbeat``       — the nucleus lub-dub envelope (0..1 beat strength).
  * ``shockwave``       — one thin ring expanding out of the nucleus per beat.
  * ``particle_state``  — the nine logo particles with depth (size/alpha/lift).
  * ``trails``          — tapered comet tails behind each particle.
  * ``dust_state``      — a denser field of tiny, slow motes on the orbits.
  * ``comet``           — one fast traveller with a long tail, periodic.
  * ``sparkles``        — short-lived deterministic glints across the plane.
  * ``rgb_trail``       — the spectrum streak each star LEAVES instead of an
    outer glow: hue walks the wheel along the tail and with the clock, and
    ``hue_to_rgb`` is the one colour conversion both renderers use.

Depth convention: ``depth = 0.5 + 0.5*cos(radians(angle))`` on a particle's
orbit — 1.0 is nearest the viewer. Renderers draw every ``depth < 0.5`` element
BEHIND the nucleus and every ``depth >= 0.5`` element IN FRONT; that z-split is
what makes the flat mark read as three-dimensional.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------- geometry
RX_FRAC = 0.70
RY_FRAC = 0.34
TILT_A = 30.0   # near field
TILT_B = -30.0  # far field
NUCLEUS_FRAC = 0.050
FRINGE_PX = 0.7
LIFT_RING = 0.28
LIFT_BEAD = 0.18
LIFT_NUCLEUS = 0.16
MIN_MARK_BRIGHTNESS = 0.85

# ------------------------------------------------------------- heartbeat
HEART_PERIOD_S = 0.95
HEART_SECOND_PHASE = 0.22
HEART_SECOND_AMP = 0.62
HEART_THUMP_S = 0.16
HEART_SCALE_AMP = 0.18
HALO_SCALE = 1.6
HALO_BASE = 0.22
HALO_WIDTH = 1.0

# --------------------------------------------------------------- 3D depth
DEPTH_RADIUS_BASE = 0.42
DEPTH_RADIUS_GAIN = 1.05
DEPTH_ALPHA_BASE = 0.10
DEPTH_ALPHA_GAIN = 1.00
DEPTH_LIFT_BASE = 0.04
DEPTH_LIFT_GAIN = 0.42
DEPTH_PARALLAX = 0.20
#: Far points contract slightly toward the centre: a cheap perspective cue.
DEPTH_PERSPECTIVE = 0.20
#: Elements nearest the viewer grow past their plane's size so the field reads
#: as coming OUT of the screen (and the nearest can cross the orbit envelope).
DEPTH_POPOUT = 0.70
#: Depth of field: ``dof()`` maps a particle's depth to an out-of-focus amount.
DOF_NEAR = 0.0
DOF_FAR = 1.0
DOF_GAMMA = 1.7
#: Per-depth shear (degrees) a renderer may rotate a body by. A point on a
#: tilted plane is foreshortened along one axis; a few degrees of skew is what
#: stops a glint from reading as a flat sticker pasted on the orbit.
SKEW_GAIN_DEG = 7.0

# ------------------------------------------------------------ camera drift
CAMERA_SWAY_FRAC = 0.016
CAMERA_SPEED_X = 0.27
CAMERA_SPEED_Y = 0.19

# ------------------------------------------------------------------ orbits
PRECESSION_DEG = 30.0
PRECESSION_PERIOD_NEAR = 15.0
PRECESSION_PERIOD_FAR = 21.0
RING_SWAY_DEG = 3.0
#: FULL-TURN periods. The orbits must ROTATE like an atom's shells, not sway
#: like a pendulum: a sine precession swings back and forth and reads as
#: pendulum motion. Counter-rotating (near +, far -) at non-harmonic periods,
#: so the pair keeps crossing in new places without ever locking.
RING_PERIOD_NEAR = 9.5
RING_PERIOD_FAR = 13.5
#: Per-particle wobble: a bounded PHASE offset in degrees, never a clock
#: multiplier (a multiplier makes the field accelerate with uptime).
SPEED_MOD_AMP = 0.10
SPEED_MOD_DEG = 3.0
SPEED_MOD_HZ = 0.45

# ------------------------------------------------------------------ trails
TRAIL_SAMPLES = 6
TRAIL_STEP_NEAR_DEG = 6.0
TRAIL_STEP_FAR_DEG = 4.0
TRAIL_ALPHA_BASE = 0.26
TRAIL_RADIUS_BASE = 0.42
TRAIL_FALLOFF = 1.6

# ------------------------------------------------------------- shockwave
SHOCKWAVE_S = 0.50
SHOCKWAVE_MAX_SCALE = 2.30
SHOCKWAVE_ALPHA = 0.20
SHOCKWAVE_WIDTH = 1.0

#: A particle is a GLINT (a twinkling four-point star), never a filled orb —
#: the nucleus is the mark's only solid body. ``GLINT_ARM_FRAC`` is the star
#: arm length as a multiple of the particle radius, and the twinkle period is
#: per-particle so the field shimmers instead of pulsing in unison.
GLINT_ARM_FRAC = 0.72
GLINT_CORE_FRAC = 0.5
GLINT_TWINKLE_BASE_S = 1.6
GLINT_TWINKLE_SPAN_S = 1.9
GLINT_MIN_ALPHA = 0.35

#: The nine logo particles: (orbit, seed_deg, radius_px_at_96, period_s).
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

#: Dust motes: a denser, slower field so the orbits are never bare.
DUST_COUNT = 30
DUST_R_FRAC_MIN = 0.86
DUST_R_FRAC_SPAN = 0.30
DUST_SIZE_BASE = 0.10
DUST_SIZE_SPAN = 0.13
DUST_PERIOD_BASE = 4.0
DUST_PERIOD_SPAN = 10.0
DUST_ALPHA_BASE = 0.16
DUST_ALPHA_GAIN = 0.40
DUST_LIFT_BASE = 0.45
DUST_LIFT_GAIN = 0.35

#: One fast traveller with a long tail, sweeping the near orbit periodically.
COMET_PERIOD_S = 7.0
COMET_SPEED_MULT = 1.9
COMET_TRAIL_SAMPLES = 12
COMET_STEP_DEG = 3.2
COMET_SIZE = 0.75
COMET_ALPHA = 0.95
COMET_LIFT = 0.80

# ------------------------------------------------------- echo (follower) rings
#: Two THINNER rings that follow the two main orbits: same tilt, lagged by a
#: phase and slightly scaled, so each orbit trails a ghost of itself. The lag
#: is what makes the pair read as one connected system rather than two decals.
ECHO_LAG_S = 1.15
ECHO_SCALE = 1.06
ECHO_ALPHA = 0.20
ECHO_WIDTH = 1.0

# ------------------------------------------------------ nucleus spark shower
#: Sparks thrown OUT of the nucleus: they are born on the beat, travel outward
#: past the orbits, shrink and fade. Deterministic (index + time), never
#: random, and connected to the heartbeat so the shower reads as the nucleus
#: breathing rather than a separate effect.
#: Spark arms are SHORTER than a particle glint's: at the nucleus they must
#: read as pinpricks, not stars the size of the nucleus itself.
SPARK_ARM_FRAC = 0.65
NUCLEUS_SPARK_COUNT = 18
NUCLEUS_SPARK_LIFE_BASE = 1.30
NUCLEUS_SPARK_LIFE_SPAN = 0.90
NUCLEUS_SPARK_REACH = 1.28
NUCLEUS_SPARK_SIZE = 0.34
NUCLEUS_SPARK_ALPHA = 0.70
NUCLEUS_SPARK_LIFT = 0.70
#: Fraction of a spark's life it is actually drawn for. The remaining tail is
#: dead time, so the fade is normalised over THIS window and reaches exactly
#: zero here: with the raw phase the spark vanished at a finite alpha and the
#: skip below read as a pop at the edge of the shower.
NUCLEUS_SPARK_VISIBLE = 0.92
#: Absolute alpha below which a spark is not emitted at all. The fade reaches
#: zero smoothly, but emitting sparks the renderer then drops on its own alpha
#: floor only widens the lifecycle gap the renderers have to tolerate.
NUCLEUS_SPARK_MIN_ALPHA = 0.010

# ------------------------------------------------------ satellite shells
#: Two extra CIRCULAR paths in the mark's plane, each carrying one small body
#: travelling it at its own rate ("two more rotating orbs on different circles
#: with different timing"). Circles, not ellipses: the tilt still squashes them
#: into the same plane, but they read as round shells around the nucleus.
SATELLITE_SHELLS = (
    # (rx_frac, ry_frac_ratio, period_s, direction, size_px, alpha, lift)
    (0.50, 1.0, 5.5, 1.0, 1.05, 0.95, 0.85),
    (0.93, 1.0, 9.0, -1.0, 0.85, 0.90, 0.75),
    # A third shell, turning WITH the first and between the two radii: three
    # counter-rotating bodies at non-harmonic periods cross in new places
    # continuously, which is what makes the mark read as 3D rather than flat.
    (0.70, 1.0, 13.5, 1.0, 0.72, 0.88, 0.72),
)
SATELLITE_RING_ALPHA = 0.14
SATELLITE_RING_WIDTH = 1.0
SATELLITE_TRAIL_SAMPLES = 5
SATELLITE_TRAIL_STEP_DEG = 7.0

# ----------------------------------------------------------------- glints
SPARKLE_COUNT = 26
SPARKLE_SEED_STEP = 97.3
SPARKLE_R_MIN_FRAC = 0.30
SPARKLE_R_SPAN_FRAC = 0.78
SPARKLE_LIFE_BASE = 1.1
SPARKLE_LIFE_STEP = 0.35
SPARKLE_VISIBLE_FRAC = 0.45
SPARKLE_MAX_ALPHA = 0.60
SPARKLE_SIZE_BASE = 0.09
SPARKLE_SIZE_PEAK = 0.18
SPARKLE_LIFT = 0.60


# ------------------------------------------------------------- RGB streaks
#: Owner: the stars carried a soft OUTER AURA; they should leave a TRAIL
#: instead. A trail is a polyline whose hue ROTATES along its own length and
#: with the clock, so one star leaves a spectrum streak rather than sitting
#: inside a halo. The field emits the samples; a renderer strokes them in
#: order with :func:`hue_to_rgb` per sample.
RGB_TRAIL_SAMPLES = 9
RGB_TRAIL_STEP_NEAR_DEG = 5.5
RGB_TRAIL_STEP_FAR_DEG = 3.6
RGB_TRAIL_STEP_PX = 2.8          # per sample, for bodies that carry a velocity
RGB_TRAIL_ALPHA_BASE = 0.62
RGB_TRAIL_SIZE_BASE = 0.85
RGB_TRAIL_TAPER = 0.78
RGB_TRAIL_FALLOFF = 1.25
#: Hue turns along the trail (one turn = the full spectrum) and per second of
#: clock, so the streak is never a static painted gradient.
RGB_TRAIL_HUE_STEP = 0.055
RGB_TRAIL_HUE_DRIFT = 0.018
RGB_TRAIL_SAT_BASE = 0.90
RGB_TRAIL_SAT_SPAN = 0.10
RGB_TRAIL_VAL_BASE = 1.00
#: Body hues live in a band around the mark's own cyan/violet so a field of
#: them still reads as ONE mark; the trail then walks the wheel away from it.
PARTICLE_HUE_BASE = 0.52
PARTICLE_HUE_SPAN = 0.34

#: The short version for the dense field (dust and glints): fewer samples,
#: dimmer and shorter, so 30 motes do not turn into 30 comets.
DUST_TRAIL_SAMPLES = 4
DUST_TRAIL_STEP_PX = 1.5
DUST_TRAIL_ALPHA_BASE = 0.22
DUST_TRAIL_SIZE_BASE = 0.70
DUST_TRAIL_HUE_STEP = 0.09


# ------------------------------------------------------------------ helpers
def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def hue_to_rgb(h: Any, s: Any = 1.0, v: Any = 1.0) -> Tuple[float, float, float]:
    """Standard HSV -> RGB, all channels 0..1. ``h`` is in TURNS and wraps.

    Renderers call this on a trail sample's ``hue`` instead of carrying their
    own colour wheel, so the Qt and Tk streaks are the same spectrum. The
    conversion is the textbook six-sector one: no dithering, no state, no
    dependence on anything but ``(h, s, v)``.
    """
    hu = (_finite(h) % 1.0) * 6.0
    sat = min(1.0, max(0.0, _finite(s, 1.0)))
    val = min(1.0, max(0.0, _finite(v, 1.0)))
    i = int(hu)
    f = hu - i
    p = val * (1.0 - sat)
    q = val * (1.0 - sat * f)
    u = val * (1.0 - sat * (1.0 - f))
    return ((val, u, p), (q, val, p), (p, val, u),
            (p, q, val), (u, p, val), (val, p, q))[i % 6]


def _hue_for(index: Any, seed: float = 0.0) -> float:
    """A body hue in the mark's band, deterministic in its index."""
    return (PARTICLE_HUE_BASE
            + PARTICLE_HUE_SPAN * hash01(int(_finite(index)) + 1, seed)) % 1.0


def _orbit_velocity(rx: float, ry: float, tilt_deg: float,
                    ang_deg: float) -> Tuple[float, float]:
    """Screen velocity of an orbit point, per degree of orbital parameter.

    Central difference over a small fixed step: analytic derivatives of the
    tilted ellipse are correct but unreadable, and this stays pure.
    """
    h = 0.05
    x0, y0 = orbit_point(0.0, 0.0, rx, ry, tilt_deg, ang_deg - h)
    x1, y1 = orbit_point(0.0, 0.0, rx, ry, tilt_deg, ang_deg + h)
    return ((x1 - x0) / (2.0 * h), (y1 - y0) / (2.0 * h))


def _rgb_trail(t: Any, R: float, body: Dict[str, Any], samples: int,
               step_deg: float, step_px: float, alpha_base: float,
               size_base: float, hue_step: float) -> List[Dict[str, float]]:
    """Shared streak builder behind :func:`rgb_trail` and :func:`dust_trail`.

    Sample 0 sits ON the body, the rest trail behind it. A body that carries an
    orbit angle is sampled back along the orbit (the streak stays on the path);
    any other body is back-projected along its own ``vx``/``vy``.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    n = max(1, int(samples))
    body = body if isinstance(body, dict) else {}
    dx, dy = camera_offset(tt, r)
    idx = body.get("index", 0.0)
    base_hue = _finite(body.get("hue"), -1.0)
    if not 0.0 <= base_hue <= 1.0:
        base_hue = _hue_for(idx, 31.7)
    sat = RGB_TRAIL_SAT_BASE + RGB_TRAIL_SAT_SPAN * hash01(
        int(_finite(idx)) + 1, 43.1)
    # The streak breathes with its body: a twinkling star's trail dims with it.
    strength = _finite(body.get("glint_alpha"), 1.0)
    strength = min(1.0, max(0.0, strength))
    size = abs(_finite(body.get("size"), 1.0))
    ang0 = body.get("angle")
    orbit = str(body.get("orbit") or "") if ang0 is not None else ""
    bx, by = _finite(body.get("x")), _finite(body.get("y"))
    vx = _finite(body.get("vx"), 0.0)
    vy = _finite(body.get("vy"), 0.0)
    speed = math.hypot(vx, vy)
    if speed > 1e-9:
        vx, vy = vx / speed, vy / speed
    else:
        vx = vy = 0.0
    near_tilt, far_tilt = orbit_tilts(tt)
    out: List[Dict[str, float]] = []
    for k in range(n):
        f = k / float(n)
        if k == 0:
            # The head rides ON the body, so the streak starts exactly at the
            # rendered star and inherits its camera sway and parallax.
            x, y, depth = bx, by, _finite(body.get("depth"), 0.5)
        elif orbit:
            tilt = near_tilt if orbit == "a" else far_tilt
            ang = _finite(ang0) - k * step_deg
            depth = _depth(ang)
            pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
            x, y = orbit_point(0.0, 0.0, r * RX_FRAC * pull,
                               r * RY_FRAC * pull, tilt, ang)
            x += dx
            y += dy
        elif vx or vy:
            dist = k * step_px * (r / 32.0)
            depth = _finite(body.get("depth"), 0.5)
            x, y = bx - vx * dist, by - vy * dist
        else:
            # No path and no velocity: hold the body's place rather than invent
            # a direction, so a hand-built dict still yields a usable streak.
            x, y, depth = bx, by, _finite(body.get("depth"), 0.5)
        out.append({
            "k": float(k),
            "x": x, "y": y,
            "hue": (base_hue + k * hue_step + tt * RGB_TRAIL_HUE_DRIFT) % 1.0,
            "sat": sat,
            "val": RGB_TRAIL_VAL_BASE * (1.0 - 0.25 * f),
            "alpha": alpha_base * strength * ((1.0 - f) ** RGB_TRAIL_FALLOFF),
            "size": size * size_base * (1.0 - RGB_TRAIL_TAPER * f),
            "depth": depth,
            "dof": dof(depth),
        })
    return out


def rgb_trail(t: Any, R: float, particle: Dict[str, Any]) -> List[Dict[str, float]]:
    """The RGB streak behind one orbiting star.

    ``RGB_TRAIL_SAMPLES`` samples, head first: ``{"x", "y", "hue", "sat",
    "val", "alpha", "size", "depth", "dof", "k"}``. Hue is in TURNS (feed it
    to :func:`hue_to_rgb`) and advances by ``RGB_TRAIL_HUE_STEP`` per sample
    and ``RGB_TRAIL_HUE_DRIFT`` per second, so the tail is a live spectrum
    ramp. A particle dict from :func:`particle_state` works as-is; any dict
    with ``x``/``y``/``vx``/``vy`` streaks along its own velocity.

    Hue wraps at 1.0 like the colour wheel does: convert per sample with
    :func:`hue_to_rgb` and do not lerp hue linearly across the wrap.
    """
    body = particle if isinstance(particle, dict) else {}
    orbit = str(body.get("orbit") or "a")
    return _rgb_trail(t, R, particle, RGB_TRAIL_SAMPLES,
                      RGB_TRAIL_STEP_NEAR_DEG if orbit == "a"
                      else RGB_TRAIL_STEP_FAR_DEG,
                      RGB_TRAIL_STEP_PX, RGB_TRAIL_ALPHA_BASE,
                      RGB_TRAIL_SIZE_BASE, RGB_TRAIL_HUE_STEP)


def dust_trail(t: Any, R: float, mote: Dict[str, Any]) -> List[Dict[str, float]]:
    """The short RGB streak for a dust mote or a sparkle.

    Same sample dict as :func:`rgb_trail`, four samples and much dimmer, so a
    dense field gains spectrum without gaining a comet per mote.
    """
    return _rgb_trail(t, R, mote, DUST_TRAIL_SAMPLES, 0.0,
                      DUST_TRAIL_STEP_PX, DUST_TRAIL_ALPHA_BASE,
                      DUST_TRAIL_SIZE_BASE, DUST_TRAIL_HUE_STEP)


def hash01(i: int, salt: float = 0.0) -> float:
    """Deterministic 0..1 hash of an index — the field's only 'randomness'."""
    x = math.sin((i + 1) * 12.9898 + salt * 78.233) * 43758.5453
    return x - math.floor(x)


def smoothstep(x: Any) -> float:
    """Clamped 0..1 ramp whose slope is ZERO at both ends.

    Every envelope in the field fades through this (or :func:`ease`), so a
    birth or a death has no velocity step: that is what keeps the motion
    continuous rather than merely bounded.
    """
    u = _finite(x)
    u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
    return u * u * (3.0 - 2.0 * u)


def ease(x: Any) -> float:
    """Raised-cosine 0..1 ease, clamped, C1 at both ends.

    Preferable to a fractional power fade (``x ** 0.6``), whose derivative is
    unbounded at 0: the element fades in, but it also ARRIVES at full speed.
    """
    u = _finite(x)
    u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
    return 0.5 * (1.0 - math.cos(math.pi * u))


def camera_offset(t: Any, R: float) -> Tuple[float, float]:
    """Small floating sway of the whole mark; sells the depth fake."""
    tt = _finite(t)
    r = _finite(R, 1.0) or 1.0
    return (math.sin(tt * CAMERA_SPEED_X) * r * CAMERA_SWAY_FRAC,
            math.cos(tt * CAMERA_SPEED_Y) * r * CAMERA_SWAY_FRAC * 0.6)


def orbit_tilts(t: Any) -> Tuple[float, float]:
    """(near, far) orbit tilts: continuous COUNTER-ROTATION, atom-style.

    Each shell makes full turns about the nucleus (near clockwise, far
    anticlockwise) at non-harmonic periods, plus a small sway so the motion is
    organic rather than mechanical. A sine-only precession swung back and
    forth and read as a pendulum, which is not what an atom does.
    """
    tt = _finite(t)
    near = (TILT_A
            + 360.0 * tt / RING_PERIOD_NEAR
            + RING_SWAY_DEG * math.sin(tt * 2.0 * math.pi / PRECESSION_PERIOD_NEAR))
    far = (TILT_B
           - 360.0 * tt / RING_PERIOD_FAR
           + RING_SWAY_DEG * math.sin(tt * 2.0 * math.pi / PRECESSION_PERIOD_FAR))
    return near, far


def orbit_point(cx: float, cy: float, rx: float, ry: float,
                tilt_deg: float, deg: float) -> Tuple[float, float]:
    """A point on a tilted ellipse at orbital parameter ``deg``."""
    rad = math.radians(deg)
    tilt = math.radians(tilt_deg)
    x = rx * math.cos(rad)
    y = ry * math.sin(rad)
    return (cx + x * math.cos(tilt) - y * math.sin(tilt),
            cy + x * math.sin(tilt) + y * math.cos(tilt))


def heartbeat(t: Any) -> float:
    """Lub-dub envelope in 0..1, zero between beats.

    Each thump is a raised-cosine pulse CENTRED on its time, so the envelope
    rises smoothly from zero and returns to zero: it no longer starts at full
    amplitude and jumps at the phase wrap (the old source of the beat jerk).
    """
    tt = _finite(t)
    if HEART_PERIOD_S <= 0:
        return 0.0
    phase = (tt % HEART_PERIOD_S) / HEART_PERIOD_S
    width = HEART_THUMP_S / HEART_PERIOD_S
    out = 0.0
    for at, amp in ((0.0, 1.0), (HEART_SECOND_PHASE, HEART_SECOND_AMP)):
        d = phase - at
        d -= round(d)
        if abs(d) < width:
            out = max(out, amp * 0.5 * (1.0 + math.cos(math.pi * d / width)))
    return out


def nucleus_scale(t: Any) -> float:
    return 1.0 + HEART_SCALE_AMP * heartbeat(t)


def shockwave(t: Any) -> Tuple[float, float]:
    """(scale, alpha) of the beat's expanding ring; (0, 0) between beats."""
    tt = _finite(t)
    if HEART_PERIOD_S <= 0:
        return (0.0, 0.0)
    age = tt % HEART_PERIOD_S
    if age >= SHOCKWAVE_S:
        return (0.0, 0.0)
    f = age / SHOCKWAVE_S
    return (1.0 + (SHOCKWAVE_MAX_SCALE - 1.0) * f,
            SHOCKWAVE_ALPHA * (1.0 - f) ** 1.4)


def _speed_mod(t: float, seed: float) -> float:
    """Legacy amplitude helper (kept for callers); see ``_phase_wobble``.

    Multiplying the CLOCK by a wobbling factor makes angular velocity grow
    linearly with uptime (the orb strobed after minutes and the motion-speed
    setting was swamped by the drift), so travel now adds a bounded PHASE
    offset instead.
    """
    return 1.0 + SPEED_MOD_AMP * math.sin(t * SPEED_MOD_HZ * 2.0 * math.pi
                                          + seed * math.pi / 180.0)


def _phase_wobble(t: float, seed: float) -> float:
    """Bounded phase offset (degrees) added to a particle's orbit angle.

    Constant angular speed + a small sinusoidal offset: the motion breathes
    without ever accelerating with uptime.
    """
    return SPEED_MOD_DEG * math.sin(t * SPEED_MOD_HZ * 2.0 * math.pi
                                    + seed * math.pi / 180.0)


def _depth(angle_deg: float) -> float:
    return 0.5 + 0.5 * math.cos(math.radians(angle_deg))


def _depth_size(depth: float, base: float) -> float:
    """Depth size with a pop-out term: near elements leave the plane."""
    plane = DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth
    pop = 1.0 + DEPTH_POPOUT * max(0.0, depth - 0.5)
    return base * plane * pop


def _depth_alpha(depth: float, top: float = 1.0) -> float:
    return top * (DEPTH_ALPHA_BASE + DEPTH_ALPHA_GAIN * depth)


def _depth_lift(depth: float) -> float:
    return DEPTH_LIFT_BASE + DEPTH_LIFT_GAIN * depth


def skew(depth: Any) -> float:
    """Per-depth shear in degrees: +ve one way, -ve the other, 0 at the rim."""
    d = min(1.0, max(0.0, _finite(depth, 0.5)))
    return SKEW_GAIN_DEG * math.cos(math.pi * d)


def dof(depth: Any) -> float:
    """How far out of focus an element at ``depth`` is: 0.0 sharp, 1.0 far.

    A depth-of-FIELD value, not a brightness one: a renderer softens or blurs
    a sample by this much and keeps its alpha, which is what separates "far
    away" from "barely visible".
    """
    d = min(1.0, max(0.0, _finite(depth, 0.5)))
    return DOF_NEAR + (DOF_FAR - DOF_NEAR) * (1.0 - d) ** DOF_GAMMA


def particle_state(t: Any, R: float) -> List[Dict[str, float]]:
    """The nine logo particles: position, depth, size, alpha, white-lift."""
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, far_tilt = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    rx, ry = r * RX_FRAC, r * RY_FRAC
    scale = r / 32.0
    out: List[Dict[str, float]] = []
    for idx, (orbit, seed, size96, period) in enumerate(PARTICLES):
        tilt = near_tilt if orbit == "a" else far_tilt
        ang = (seed + tt * 360.0 / max(0.1, period)
               + _phase_wobble(tt, seed))
        depth = _depth(ang)
        # Perspective: far points pull toward the centre.
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        par = (depth - 0.5) * DEPTH_PARALLAX * r
        x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
        nx, ny = (math.sin(math.radians(tilt)), -math.cos(math.radians(tilt)))
        # Parallax along the orbit normal, then the camera sway.
        tw = glint(tt, seed)
        out.append({
            "index": float(idx), "orbit": orbit, "angle": ang, "depth": depth,
            "x": x + nx * par + dx, "y": y + ny * par * 0.35 + dy,
            "size": _depth_size(depth, size96 * scale) * tw["scale"],
            "alpha": _depth_alpha(depth) * tw["alpha"],
            "lift": _depth_lift(depth),
            "glint_scale": tw["scale"], "glint_alpha": tw["alpha"],
            "hue": _hue_for(idx, 31.7), "dof": dof(depth),
            "skew": skew(depth),
        })
    return out


def glint(t: Any, seed: float) -> Dict[str, float]:
    """Twinkle factors for one glint: ``scale`` and ``alpha`` multipliers.

    Deterministic per-particle phase so the field shimmers rather than
    blinking in unison. Never below ``GLINT_MIN_ALPHA`` so a glint is always
    faintly present instead of popping in and out of existence.
    """
    tt = _finite(t)
    period = GLINT_TWINKLE_BASE_S + GLINT_TWINKLE_SPAN_S * hash01(int(seed) % 97, 17.3)
    phase = (tt / max(0.1, period) + seed / 360.0) % 1.0
    wave = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
    return {"scale": 0.75 + 0.45 * wave,
            "alpha": GLINT_MIN_ALPHA + (1.0 - GLINT_MIN_ALPHA) * wave}


def echo_rings(t: Any) -> List[Dict[str, float]]:
    """Two thinner follower rings, one per main orbit (lagged, scaled, faint)."""
    tt = _finite(t)
    lag = tt - ECHO_LAG_S
    near, far = orbit_tilts(lag)
    return [
        {"orbit": "a", "tilt": near, "rx_scale": ECHO_SCALE,
         "ry_scale": ECHO_SCALE, "alpha": ECHO_ALPHA, "width": ECHO_WIDTH},
        {"orbit": "b", "tilt": far, "rx_scale": ECHO_SCALE,
         "ry_scale": ECHO_SCALE, "alpha": ECHO_ALPHA, "width": ECHO_WIDTH},
    ]


def nucleus_sparks(t: Any, R: float) -> List[Dict[str, float]]:
    """Sparks thrown out of the nucleus, connected to the heartbeat.

    Each spark has its own deterministic birth phase inside the beat cycle, so
    a shower leaves the nucleus every thump and travels past the orbits while
    shrinking and fading. Empty under a still clock (the renderers pass t=0).
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    out: List[Dict[str, float]] = []
    for i in range(NUCLEUS_SPARK_COUNT):
        seed = i * 137.5
        life = NUCLEUS_SPARK_LIFE_BASE + (i % 5) * (
            NUCLEUS_SPARK_LIFE_SPAN / 4.0)
        # Births are tied to the heartbeat: a spark leaves on the thump.
        phase = ((tt - HEART_PERIOD_S * hash01(i, 23.7)) / max(0.1, life)) % 1.0
        if phase >= NUCLEUS_SPARK_VISIBLE:
            continue
        f = phase / NUCLEUS_SPARK_VISIBLE
        grow = f ** 0.65
        dist = r * NUCLEUS_SPARK_REACH * grow
        ang = math.radians(seed + 40.0 * phase)
        x = math.cos(ang) * dist
        y = math.sin(ang) * dist * 0.72  # squash: the plane is tilted
        # Reaches exactly zero, with zero slope, at the skip boundary above.
        fade = (1.0 - f) ** 1.5
        if NUCLEUS_SPARK_ALPHA * fade < NUCLEUS_SPARK_MIN_ALPHA:
            continue
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": 0.5 + 0.5 * math.cos(ang),
            "hue": _hue_for(i, 97.1), "dof": dof(0.5 + 0.5 * math.cos(ang)),
            "size": max(0.30, NUCLEUS_SPARK_SIZE * (r / 32.0)
                        * (1.0 - 0.72 * grow)),
            "alpha": NUCLEUS_SPARK_ALPHA * fade,
            "lift": NUCLEUS_SPARK_LIFT,
        })
    return out


def satellites(t: Any, R: float) -> List[Dict[str, Any]]:
    """Two circular shells, each with a small body travelling it.

    Returns one dict per shell: ``ring`` (the circular path, drawn thin) and
    ``body`` (position/depth/size/alpha/lift plus a short ``trail``). Bodies
    run in opposite directions at different periods, so the pair never beats
    in sync.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, far_tilt = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    for idx, (rx_frac, ratio, period, direction, size96, alpha, lift) in enumerate(
            SATELLITE_SHELLS):
        # A circle in this plane: same squash ratio as the mark's ellipses.
        tilt = near_tilt if idx == 0 else far_tilt
        rx = r * rx_frac
        ry = rx * (RY_FRAC / RX_FRAC) * ratio
        ang = 360.0 * direction * tt / max(0.1, period) + 137.0 * idx
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        bx, by = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
        trail: List[Dict[str, float]] = []
        for k in range(1, SATELLITE_TRAIL_SAMPLES + 1):
            a = ang - direction * k * SATELLITE_TRAIL_STEP_DEG
            d = _depth(a)
            pp = 1.0 - DEPTH_PERSPECTIVE * (1.0 - d)
            tx, ty = orbit_point(0.0, 0.0, rx * pp, ry * pp, tilt, a)
            trail.append({"x": tx + dx, "y": ty + dy,
                          "size": _depth_size(d, size96 * scale) * 0.5,
                          "alpha": 0.30 * alpha * (1.0 - k / (SATELLITE_TRAIL_SAMPLES + 1.0)),
                          "lift": lift})
        bvx, bvy = _orbit_velocity(rx, ry, tilt, ang)
        out.append({
            "index": float(idx),
            "ring": {"tilt": tilt, "rx_frac": rx_frac,
                     "ry_frac": rx_frac * (RY_FRAC / RX_FRAC) * ratio,
                     "alpha": SATELLITE_RING_ALPHA, "width": SATELLITE_RING_WIDTH},
            "body": {"index": float(idx), "x": bx + dx, "y": by + dy,
                     "depth": depth, "hue": _hue_for(idx, 89.3),
                     "vx": bvx * direction, "vy": bvy * direction,
                     "dof": dof(depth), "skew": skew(depth),
                     "size": _depth_size(depth, size96 * scale),
                     "alpha": _depth_alpha(depth, alpha), "lift": lift},
            "trail": trail,
        })
    return out


def trails(t: Any, R: float, particle: Dict[str, Any]) -> List[Dict[str, float]]:
    """Tapered tail behind one particle (oldest last, alpha fading)."""
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, far_tilt = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    rx, ry = r * RX_FRAC, r * RY_FRAC
    orbit = str(particle.get("orbit") or "a")
    tilt = near_tilt if orbit == "a" else far_tilt
    step = TRAIL_STEP_NEAR_DEG if orbit == "a" else TRAIL_STEP_FAR_DEG
    ang0 = _finite(particle.get("angle"), 0.0)
    base_size = float(particle.get("size") or 1.0)
    out: List[Dict[str, float]] = []
    for k in range(1, TRAIL_SAMPLES + 1):
        ang = ang0 - k * step
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
        out.append({
            "x": x + dx, "y": y + dy,
            "hue": _hue_for(particle.get("index", 0.0), 31.7),
            "dof": dof(depth),
            "size": base_size * TRAIL_RADIUS_BASE * (1.0 - 0.10 * k),
            "alpha": TRAIL_ALPHA_BASE * ((1.0 - k / (TRAIL_SAMPLES + 1.0))
                                         ** TRAIL_FALLOFF),
            "lift": _depth_lift(depth),
            "depth": depth,
        })
    return out


def dust_state(t: Any, R: float) -> List[Dict[str, float]]:
    """Denser motes on the same orbits: the field never looks bare."""
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, far_tilt = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    rx, ry = r * RX_FRAC, r * RY_FRAC
    scale = r / 32.0
    out: List[Dict[str, float]] = []
    for i in range(DUST_COUNT):
        orbit = "a" if i % 3 else "b"
        tilt = near_tilt if orbit == "a" else far_tilt
        seed = 360.0 * hash01(i, 1.7)
        period = DUST_PERIOD_BASE + DUST_PERIOD_SPAN * hash01(i, 3.1)
        ang = (seed + tt * 360.0 / max(0.1, period)
               + _phase_wobble(tt, seed))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        grow = DUST_R_FRAC_MIN + DUST_R_FRAC_SPAN * hash01(i, 5.9)
        x, y = orbit_point(0.0, 0.0, rx * pull * grow, ry * pull * grow, tilt, ang)
        vx, vy = _orbit_velocity(rx * pull * grow, ry * pull * grow, tilt, ang)
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": depth,
            "vx": vx * 360.0 / max(0.1, period),
            "vy": vy * 360.0 / max(0.1, period),
            "hue": _hue_for(i, 53.9), "dof": dof(depth),
            "skew": skew(depth),
            "size": (DUST_SIZE_BASE + DUST_SIZE_SPAN * hash01(i, 7.3)) * scale
                    * (DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth),
            "alpha": _depth_alpha(depth, DUST_ALPHA_BASE + DUST_ALPHA_GAIN
                                  * hash01(i, 9.1)),
            "lift": DUST_LIFT_BASE + DUST_LIFT_GAIN * hash01(i, 11.3),
        })
    return out


def comet(t: Any, R: float) -> Dict[str, Any]:
    """One fast traveller on the near orbit with a long tapered tail.

    Always returned (so a renderer's primitive count stays stable across a
    cycle); ``alpha`` is 0 outside its sweep window.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, _far = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    rx, ry = r * RX_FRAC, r * RY_FRAC
    scale = r / 32.0
    if COMET_PERIOD_S <= 0:
        return {"alpha": 0.0, "trail": []}
    phase = (tt % COMET_PERIOD_S) / COMET_PERIOD_S
    # Visible on the second half of the period, fading in and out.
    if phase < 0.45:
        return {"alpha": 0.0, "trail": []}
    f = (phase - 0.45) / 0.55
    ang = 360.0 * f * COMET_SPEED_MULT
    # Raised-cosine ease in and out (zero slope at both sweep boundaries) so
    # the traveller never pops into or out of existence; a fractional power
    # fade arrives at full speed.
    fade = ease(f)
    depth = _depth(ang)
    pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
    x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, near_tilt, ang)
    tail: List[Dict[str, float]] = []
    for k in range(1, COMET_TRAIL_SAMPLES + 1):
        a = ang - k * COMET_STEP_DEG
        d = _depth(a)
        p = 1.0 - DEPTH_PERSPECTIVE * (1.0 - d)
        tx, ty = orbit_point(0.0, 0.0, rx * p, ry * p, near_tilt, a)
        tail.append({"x": tx + dx, "y": ty + dy,
                     "size": COMET_SIZE * scale * 0.62 * (1.0 - k / 14.0),
                     "alpha": COMET_ALPHA * fade * (1.0 - k / (COMET_TRAIL_SAMPLES + 1.0)) ** 1.3,
                     "lift": COMET_LIFT})
    cvx, cvy = _orbit_velocity(rx * pull, ry * pull, near_tilt, ang)
    return {"x": x + dx, "y": y + dy, "depth": depth,
            "index": 0.0, "hue": _hue_for(0, 101.3),
            "vx": cvx * COMET_SPEED_MULT, "vy": cvy * COMET_SPEED_MULT,
            "dof": dof(depth), "skew": skew(depth),
            "size": COMET_SIZE * scale * (0.8 + 0.4 * depth),
            "alpha": COMET_ALPHA * fade, "lift": COMET_LIFT, "trail": tail}


def sparkles(t: Any, R: float) -> List[Dict[str, float]]:
    """Deterministic glints across the near plane (empty list when hidden)."""
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, _far = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    out: List[Dict[str, float]] = []
    for i in range(SPARKLE_COUNT):
        seed = i * SPARKLE_SEED_STEP
        life = SPARKLE_LIFE_BASE + (i % 4) * SPARKLE_LIFE_STEP
        phase = (tt / max(0.1, life) + seed / 360.0) % 1.0
        if phase > SPARKLE_VISIBLE_FRAC:
            continue
        f = phase / SPARKLE_VISIBLE_FRAC
        peak = 0.5 * (1.0 - math.cos(2.0 * math.pi * f))
        rate = 18.0 + (i % 5) * 6.0
        ang = seed + tt * rate
        rfrac = SPARKLE_R_MIN_FRAC + SPARKLE_R_SPAN_FRAC * hash01(i, 13.7)
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        sx, sy = r * rfrac * RX_FRAC * pull, r * rfrac * RY_FRAC * pull
        x, y = orbit_point(0.0, 0.0, sx, sy, near_tilt, ang)
        vx, vy = _orbit_velocity(sx, sy, near_tilt, ang)
        out.append({"index": float(i), "x": x + dx, "y": y + dy,
                    "depth": depth, "hue": _hue_for(i, 67.1),
                    "dof": dof(depth), "skew": skew(depth),
                    "vx": vx * rate, "vy": vy * rate,
                    "size": (SPARKLE_SIZE_BASE + SPARKLE_SIZE_PEAK * peak)
                            * (r / 32.0),
                    "alpha": SPARKLE_MAX_ALPHA * peak,
                    "lift": SPARKLE_LIFT})
    return out


# ------------------------------------------------------------- inner core ring
INNER_RING_RX_FRAC = 0.32
INNER_RING_PERIOD_S = 4.5
INNER_RING_DIRECTION = 1.0
INNER_RING_TILT_BASE = 0.0

# ------------------------------------------------------------- binary particle field
BINARY_COUNT = 56
BINARY_SIZE_BASE = 0.07
BINARY_SIZE_SPAN = 0.06
BINARY_PERIOD_BASE = 6.0
BINARY_PERIOD_SPAN = 12.0
BINARY_ALPHA_BASE = 0.20
BINARY_ALPHA_GAIN = 0.35
BINARY_LIFT_BASE = 0.50
BINARY_LIFT_GAIN = 0.30
BINARY_R_FRAC_MIN = 0.80
BINARY_R_FRAC_SPAN = 0.25


def inner_ring_tilt(t: Any) -> float:
    """Tilt of the fast inner counter-rotating core ring."""
    tt = _finite(t)
    return (INNER_RING_TILT_BASE
            + INNER_RING_DIRECTION * 360.0 * tt / max(0.1, INNER_RING_PERIOD_S)
            + RING_SWAY_DEG * math.sin(tt * 2.0 * math.pi
                                       / PRECESSION_PERIOD_NEAR))


def binary_state(t: Any, R: float) -> List[Dict[str, Any]]:
    """A field of tiny 1/0 data-fragments on the orbits and satellite shells."""
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, far_tilt = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    rx, ry = r * RX_FRAC, r * RY_FRAC
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    per_shell = 4
    main_n = BINARY_COUNT - per_shell * len(SATELLITE_SHELLS)
    for i in range(main_n):
        orbit = "a" if (i % 3) != 0 else "b"
        tilt = near_tilt if orbit == "a" else far_tilt
        seed = 360.0 * hash01(i, 2.7)
        period = BINARY_PERIOD_BASE + BINARY_PERIOD_SPAN * hash01(i, 4.1)
        ang = (seed + tt * 360.0 / max(0.1, period)
               + _phase_wobble(tt, seed))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        grow = BINARY_R_FRAC_MIN + BINARY_R_FRAC_SPAN * hash01(i, 5.9)
        x, y = orbit_point(0.0, 0.0, rx * pull * grow, ry * pull * grow,
                           tilt, ang)
        vx, vy = _orbit_velocity(rx * pull * grow, ry * pull * grow, tilt, ang)
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": depth,
            "vx": vx * 360.0 / max(0.1, period),
            "vy": vy * 360.0 / max(0.1, period),
            "hue": _hue_for(i, 71.5), "dof": dof(depth),
            "size": (BINARY_SIZE_BASE + BINARY_SIZE_SPAN * hash01(i, 7.3))
                    * scale * (DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth),
            "alpha": _depth_alpha(depth, BINARY_ALPHA_BASE
                                  + BINARY_ALPHA_GAIN * hash01(i, 9.1)),
            "lift": BINARY_LIFT_BASE + BINARY_LIFT_GAIN * hash01(i, 11.3),
            "char": "1" if (i % 2 == 0) else "0",
        })
    # A few fragments on each satellite shell.
    for j in range(per_shell * len(SATELLITE_SHELLS)):
        idx = min(len(SATELLITE_SHELLS) - 1, j // per_shell)
        (rx_frac, ratio, period, direction, _size, _alpha, _lift) = SATELLITE_SHELLS[idx]
        tilt = near_tilt if idx == 0 else far_tilt
        rx_s = r * rx_frac
        ry_s = rx_s * (RY_FRAC / RX_FRAC) * ratio
        seed = 137.0 * idx + 47.0 * j
        ang = (seed + 360.0 * direction * tt / max(0.1, period))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        x, y = orbit_point(0.0, 0.0, rx_s * pull, ry_s * pull, tilt, ang)
        vx, vy = _orbit_velocity(rx_s * pull, ry_s * pull, tilt, ang)
        i = main_n + j
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": depth,
            "vx": vx * direction * 360.0 / max(0.1, period),
            "vy": vy * direction * 360.0 / max(0.1, period),
            "hue": _hue_for(i, 83.9), "dof": dof(depth),
            "size": (BINARY_SIZE_BASE * 1.1) * scale,
            "alpha": _depth_alpha(depth, BINARY_ALPHA_BASE),
            "lift": BINARY_LIFT_BASE,
            "char": "1" if (i % 2 == 0) else "0",
        })
    return out


# ------------------------------------------------------------- ring geometry
#: Owner: "refine circles more". Every circle in the mark — the two orbits,
#: their echo followers, the inner core ring and the satellite shells — is
#: declared ONCE here, as a stroke description in the tilted plane, so the Qt
#: and Tk renderers cannot drift into two slightly different sets of circles.
RING_ALPHA_MAIN = 0.34
RING_ALPHA_ECHO = 0.20
RING_ALPHA_INNER = 0.24
RING_ALPHA_SAT = 0.14
RING_WIDTH_MAIN = 1.0
RING_WIDTH_ECHO = 1.0
RING_WIDTH_INNER = 0.8
RING_WIDTH_SAT = 1.0
#: Alpha breathing, per ring: a ring that never changes reads as printed on the
#: glass. The periods are non-harmonic so the set never pulses in unison.
RING_BREATH_SPAN = 0.14
RING_BREATH_PERIOD_NEAR = 11.0
RING_BREATH_PERIOD_FAR = 14.5
RING_BREATH_PERIOD_INNER = 6.5
RING_BREATH_PERIOD_ECHO = 17.0
#: Points in :func:`ring_path`; enough that a 300px ring shows no facets.
RING_PATH_SAMPLES = 72


def ring_geometry(t: Any) -> List[Dict[str, Any]]:
    """Every circle of the mark as a stroke description, back to front.

    Each entry: ``{"id", "layer", "orbit", "tilt", "rx_frac", "ry_frac",
    "alpha", "width", "echo", "seed"}``. Radii are FRACTIONS of the mark
    radius and the tilt is in degrees, so the same entry scales to any size;
    :func:`ring_path` turns one into points. ``layer`` is ``main``, ``echo``,
    ``inner`` or ``satellite``; the list is ordered so a renderer can paint it
    in order and get the near rings on top.
    """
    tt = _finite(t)
    near_tilt, far_tilt = orbit_tilts(tt)
    lag_near, lag_far = (e["tilt"] for e in echo_rings(tt - ECHO_LAG_S))
    out: List[Dict[str, Any]] = [
        {"id": "orbit_b", "layer": "main", "orbit": "b", "tilt": far_tilt,
         "rx_frac": RX_FRAC, "ry_frac": RY_FRAC, "alpha": RING_ALPHA_MAIN,
         "width": RING_WIDTH_MAIN, "echo": False, "seed": 0.0},
        {"id": "echo_b", "layer": "echo", "orbit": "b", "tilt": lag_far,
         "rx_frac": RX_FRAC * ECHO_SCALE, "ry_frac": RY_FRAC * ECHO_SCALE,
         "alpha": RING_ALPHA_ECHO, "width": RING_WIDTH_ECHO, "echo": True,
         "seed": 1.0},
    ]
    for idx, (rx_frac, ratio, _p, _d, _s, _a, _l) in enumerate(
            SATELLITE_SHELLS):
        out.append({
            "id": f"shell_{idx}", "layer": "satellite", "orbit": "",
            # Mirrors satellites(): the first shell rides the near plane, the
            # rest the far one, so the declared circle is the drawn circle.
            "tilt": near_tilt if idx == 0 else far_tilt,
            "rx_frac": rx_frac,
            "ry_frac": rx_frac * (RY_FRAC / RX_FRAC) * ratio,
            "alpha": RING_ALPHA_SAT, "width": RING_WIDTH_SAT, "echo": False,
            "seed": 2.0 + idx})
    out.append({
        "id": "inner", "layer": "inner", "orbit": "", "tilt": inner_ring_tilt(tt),
        "rx_frac": INNER_RING_RX_FRAC,
        "ry_frac": INNER_RING_RX_FRAC * (RY_FRAC / RX_FRAC),
        "alpha": RING_ALPHA_INNER, "width": RING_WIDTH_INNER, "echo": False,
        "seed": 5.0})
    out.append({
        "id": "echo_a", "layer": "echo", "orbit": "a", "tilt": lag_near,
        "rx_frac": RX_FRAC * ECHO_SCALE, "ry_frac": RY_FRAC * ECHO_SCALE,
        "alpha": RING_ALPHA_ECHO, "width": RING_WIDTH_ECHO, "echo": True,
        "seed": 6.0})
    out.append({
        "id": "orbit_a", "layer": "main", "orbit": "a", "tilt": near_tilt,
        "rx_frac": RX_FRAC, "ry_frac": RY_FRAC, "alpha": RING_ALPHA_MAIN,
        "width": RING_WIDTH_MAIN, "echo": False, "seed": 7.0})
    return out


def ring_path(t: Any, R: Any, ring: Dict[str, Any],
              samples: int = RING_PATH_SAMPLES) -> List[Tuple[float, float]]:
    """Points around one :func:`ring_geometry` ring, in field coordinates.

    Camera sway included, closed (the last point repeats the first) so a
    renderer can stroke it directly with no special case. Both backends run
    the same points, which is what makes the circles identical.
    """
    r = _finite(R, 32.0) or 32.0
    n = max(3, int(samples))
    ring = ring if isinstance(ring, dict) else {}
    dx, dy = camera_offset(t, r)
    rx = r * _finite(ring.get("rx_frac"), RX_FRAC)
    ry = r * _finite(ring.get("ry_frac"), RY_FRAC)
    tilt = _finite(ring.get("tilt"), 0.0)
    pts = [orbit_point(dx, dy, rx, ry, tilt, 360.0 * i / float(n))
           for i in range(n)]
    pts.append(pts[0])  # exactly closed, not closed to within a float epsilon
    return pts


def ring_alpha(t: Any, ring: Dict[str, Any]) -> float:
    """The ring's breathing alpha for clock ``t`` — never zero, never 1."""
    ring = ring if isinstance(ring, dict) else {}
    period = {0: RING_BREATH_PERIOD_NEAR, 1: RING_BREATH_PERIOD_FAR,
              5: RING_BREATH_PERIOD_INNER}.get(
                  int(_finite(ring.get("seed"))), RING_BREATH_PERIOD_ECHO)
    phase = 2.0 * math.pi * _finite(t) / max(0.1, period)
    phase += _finite(ring.get("seed")) * 0.7
    return _finite(ring.get("alpha"), RING_ALPHA_MAIN) * (
        1.0 - RING_BREATH_SPAN * 0.5
        + RING_BREATH_SPAN * 0.5 * math.sin(phase))


__all__ = [
    "PARTICLES", "RX_FRAC", "RY_FRAC", "TILT_A", "TILT_B", "NUCLEUS_FRAC",
    "FRINGE_PX", "LIFT_RING", "LIFT_BEAD", "LIFT_NUCLEUS",
    "MIN_MARK_BRIGHTNESS",
    "HEART_PERIOD_S", "HEART_SCALE_AMP", "HALO_SCALE", "HALO_BASE",
    "HALO_WIDTH", "SHOCKWAVE_S", "SHOCKWAVE_MAX_SCALE", "SHOCKWAVE_ALPHA",
    "DEPTH_RADIUS_BASE", "DEPTH_RADIUS_GAIN", "DEPTH_ALPHA_BASE",
    "DEPTH_ALPHA_GAIN", "DEPTH_LIFT_BASE", "DEPTH_LIFT_GAIN",
    "DEPTH_PARALLAX", "DEPTH_PERSPECTIVE", "TRAIL_SAMPLES", "TRAIL_ALPHA_BASE",
    "TRAIL_RADIUS_BASE", "TRAIL_FALLOFF", "DUST_COUNT", "DUST_SIZE_BASE",
    "DUST_ALPHA_BASE", "COMET_PERIOD_S", "COMET_TRAIL_SAMPLES",
    "COMET_ALPHA", "SPARKLE_COUNT", "SPARKLE_MAX_ALPHA", "SPARKLE_LIFT",
    "PRECESSION_DEG", "RING_SWAY_DEG", "CAMERA_SWAY_FRAC", "SPEED_MOD_AMP", "hash01", "camera_offset", "orbit_tilts",
    "orbit_point", "heartbeat", "nucleus_scale", "shockwave", "particle_state",
    "glint", "GLINT_ARM_FRAC", "GLINT_CORE_FRAC", "SPARK_ARM_FRAC",
    "_phase_wobble", "SPEED_MOD_DEG",
    "echo_rings", "ECHO_LAG_S", "ECHO_SCALE", "ECHO_ALPHA",
    "satellites", "SATELLITE_SHELLS", "SATELLITE_RING_ALPHA",
    "DEPTH_POPOUT",
    "nucleus_sparks", "NUCLEUS_SPARK_COUNT", "NUCLEUS_SPARK_REACH",
    "NUCLEUS_SPARK_VISIBLE", "smoothstep", "ease",
    "trails", "dust_state", "comet", "sparkles",
    "INNER_RING_RX_FRAC", "INNER_RING_PERIOD_S", "INNER_RING_DIRECTION",
    "INNER_RING_TILT_BASE", "inner_ring_tilt",
    "BINARY_COUNT", "BINARY_SIZE_BASE", "BINARY_PERIOD_BASE", "binary_state",
    "hue_to_rgb", "rgb_trail", "dust_trail", "RGB_TRAIL_SAMPLES",
    "RGB_TRAIL_HUE_STEP", "RGB_TRAIL_HUE_DRIFT", "RGB_TRAIL_ALPHA_BASE",
    "DUST_TRAIL_SAMPLES", "PARTICLE_HUE_BASE", "PARTICLE_HUE_SPAN",
    "dof", "DOF_NEAR", "DOF_FAR", "DOF_GAMMA", "skew", "SKEW_GAIN_DEG",
    "RING_PERIOD_NEAR", "RING_PERIOD_FAR",
    "ring_geometry", "ring_path", "ring_alpha", "RING_PATH_SAMPLES",
    "RING_ALPHA_MAIN", "RING_ALPHA_ECHO", "RING_ALPHA_INNER",
    "RING_ALPHA_SAT", "RING_WIDTH_MAIN", "RING_WIDTH_INNER",
    "RING_BREATH_SPAN",
]
