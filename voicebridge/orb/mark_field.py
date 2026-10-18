"""Rich animation field for the Qubit-mark orb.

ONE deterministic description of everything that moves, shared by both orb
renderers (Qt live orb and the Tk fallback) so they cannot drift apart. Pure
functions of ``(t, R)`` only — no ``random``, no state, no I/O — which is what
makes the motion reproducible frame by frame, testable, and identical across
backends.

Layers, back to front:

  * ``camera_offset``   — slow floating sway of the whole mark (fakes depth).
  * ``orbit_tilts``     — the two orbit tilts, plus a slow precession sway.
  * ``heartbeat``       — the nucleus lub-dub envelope (0..1 beat strength).
  * ``shockwave``       — one thin ring expanding out of the nucleus per beat.
  * ``particle_state``  — the nine logo particles with depth (size/alpha/lift).
  * ``trails``          — tapered comet tails behind each particle.
  * ``dust_state``      — a denser field of tiny, slow motes on the orbits.
  * ``comet``           — one fast traveller with a long tail, periodic.
  * ``sparkles``        — short-lived deterministic glints across the plane.

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
DEPTH_PERSPECTIVE = 0.15
#: Elements nearest the viewer grow past their plane's size so the field reads
#: as coming OUT of the screen (and the nearest can cross the orbit envelope).
DEPTH_POPOUT = 0.55

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
RING_PERIOD_NEAR = 12.0
RING_PERIOD_FAR = 17.0
#: Per-particle wobble: a bounded PHASE offset in degrees, never a clock
#: multiplier (a multiplier makes the field accelerate with uptime).
SPEED_MOD_AMP = 0.10
SPEED_MOD_DEG = 7.0
SPEED_MOD_HZ = 0.7

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
GLINT_ARM_FRAC = 1.45
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
DUST_SIZE_BASE = 0.20
DUST_SIZE_SPAN = 0.26
DUST_PERIOD_BASE = 5.0
DUST_PERIOD_SPAN = 9.0
DUST_ALPHA_BASE = 0.16
DUST_ALPHA_GAIN = 0.40
DUST_LIFT_BASE = 0.45
DUST_LIFT_GAIN = 0.35

#: One fast traveller with a long tail, sweeping the near orbit periodically.
COMET_PERIOD_S = 7.0
COMET_SPEED_MULT = 1.9
COMET_TRAIL_SAMPLES = 12
COMET_STEP_DEG = 3.2
COMET_SIZE = 1.5
COMET_ALPHA = 0.95
COMET_LIFT = 0.80

# ------------------------------------------------------- echo (follower) rings
#: Two THINNER rings that follow the two main orbits: same tilt, lagged by a
#: phase and slightly scaled, so each orbit trails a ghost of itself. The lag
#: is what makes the pair read as one connected system rather than two decals.
ECHO_LAG_S = 1.15
ECHO_SCALE = 1.14
ECHO_ALPHA = 0.30
ECHO_WIDTH = 1.0

# ------------------------------------------------------ nucleus spark shower
#: Sparks thrown OUT of the nucleus: they are born on the beat, travel outward
#: past the orbits, shrink and fade. Deterministic (index + time), never
#: random, and connected to the heartbeat so the shower reads as the nucleus
#: breathing rather than a separate effect.
#: Spark arms are SHORTER than a particle glint's: at the nucleus they must
#: read as pinpricks, not stars the size of the nucleus itself.
SPARK_ARM_FRAC = 1.30
NUCLEUS_SPARK_COUNT = 18
NUCLEUS_SPARK_LIFE_BASE = 1.30
NUCLEUS_SPARK_LIFE_SPAN = 0.90
NUCLEUS_SPARK_REACH = 1.28
NUCLEUS_SPARK_SIZE = 0.34
NUCLEUS_SPARK_ALPHA = 0.70
NUCLEUS_SPARK_LIFT = 0.70

# ------------------------------------------------------ satellite shells
#: Two extra CIRCULAR paths in the mark's plane, each carrying one small body
#: travelling it at its own rate ("two more rotating orbs on different circles
#: with different timing"). Circles, not ellipses: the tilt still squashes them
#: into the same plane, but they read as round shells around the nucleus.
SATELLITE_SHELLS = (
    # (rx_frac, ry_frac_ratio, period_s, direction, size_px, alpha, lift)
    (0.50, 1.0, 5.5, 1.0, 1.05, 0.95, 0.85),
    (0.93, 1.0, 9.0, -1.0, 0.85, 0.90, 0.75),
)
SATELLITE_RING_ALPHA = 0.22
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
SPARKLE_SIZE_BASE = 0.18
SPARKLE_SIZE_PEAK = 0.36
SPARKLE_LIFT = 0.60


# ------------------------------------------------------------------ helpers
def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def hash01(i: int, salt: float = 0.0) -> float:
    """Deterministic 0..1 hash of an index — the field's only 'randomness'."""
    x = math.sin((i + 1) * 12.9898 + salt * 78.233) * 43758.5453
    return x - math.floor(x)


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
        if phase >= 0.92:
            continue
        grow = phase ** 0.65
        dist = r * NUCLEUS_SPARK_REACH * grow
        ang = math.radians(seed + 40.0 * phase)
        x = math.cos(ang) * dist
        y = math.sin(ang) * dist * 0.72  # squash: the plane is tilted
        fade = (1.0 - phase) ** 1.5
        out.append({
            "x": x + dx, "y": y + dy, "depth": 0.5 + 0.5 * math.cos(ang),
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
        out.append({
            "index": float(idx),
            "ring": {"tilt": tilt, "rx_frac": rx_frac,
                     "ry_frac": rx_frac * (RY_FRAC / RX_FRAC) * ratio,
                     "alpha": SATELLITE_RING_ALPHA, "width": SATELLITE_RING_WIDTH},
            "body": {"x": bx + dx, "y": by + dy, "depth": depth,
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
        out.append({
            "x": x + dx, "y": y + dy, "depth": depth,
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
    # Eased in and out (smoothstep-ish) so the traveller never pops
    # into or out of existence at its sweep boundary.
    fade = math.sin(math.pi * f) ** 1.1
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
    return {"x": x + dx, "y": y + dy, "depth": depth,
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
        ang = seed + tt * (18.0 + (i % 5) * 6.0)
        rfrac = SPARKLE_R_MIN_FRAC + SPARKLE_R_SPAN_FRAC * hash01(i, 13.7)
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        x, y = orbit_point(0.0, 0.0, r * rfrac * RX_FRAC * pull,
                           r * rfrac * RY_FRAC * pull, near_tilt, ang)
        out.append({"x": x + dx, "y": y + dy, "depth": depth,
                    "size": (SPARKLE_SIZE_BASE + SPARKLE_SIZE_PEAK * peak)
                            * (r / 32.0),
                    "alpha": SPARKLE_MAX_ALPHA * peak,
                    "lift": SPARKLE_LIFT})
    return out


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
    "trails", "dust_state", "comet", "sparkles",
]
