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
  * ``blink_dots``      — pinpricks that materialise in place and vanish.
  * ``flare_dots``      — a second, rarer family of larger flare blinks.
  * ``rgb_trail``       — the spectrum streak each star LEAVES instead of an
    outer glow: hue walks the wheel along the tail and with the clock, and
    ``hue_to_rgb`` is the one colour conversion both renderers use.
  * ``core_shells``     — the nucleus's INTERIOR structure: nested bodies at
    different radii and tilts, each turning at its own period.
  * ``filament_state``  — a travelling standing wave running around a ring.
  * ``binary_state``    — deterministic "1"/"0" data fragments (text characters,
    split by depth; the retired ``math_state`` never returns one).
  * ``inner_ring_tilt`` — the third, interior core orbit.
  * ``ring_particles``  — bodies riding EVERY :func:`ring_geometry` circle.
  * ``cylinder_particles`` — a vertical column around the nucleus (volume cue).
  * ``orbiter_particles`` — bodies on their OWN inclined orbits around the core.
  * ``volume_particles`` — a real 3D particle field of bodies on their own
    inclined orbits, each with a per-body twinkle (``pulse``) and rotation
    (``spin``) animation. It REPLACES the retired math-glyph text layer
    (``math_state``, now a no-op) — no ``char`` anywhere.
  * ``accretion_stream`` — matter winding around the nucleus on a continuous
    spiral, moving in and out (the black-hole accretion read).
  * ``photon_ring``     — the thin, bright lensed arcs hugging the core.
  * ``infall_streaks``  — fast radial streaks with an inward velocity + tail.
  * ``screen_break``    — fast bodies on wide orbits that reach BEYOND R (and
    past the widget edge): the deliberate overflow the owner asked for.
  * ``haze_state``      — a slow, very faint parallax haze of large soft bodies
    behind everything, each breathing on its own non-harmonic period.

Depth convention: ``depth = 0.5 + 0.5*cos(radians(angle))`` on a particle's
orbit — 1.0 is nearest the viewer. Renderers draw every ``depth < 0.5`` element
BEHIND the nucleus and every ``depth >= 0.5`` element IN FRONT; that z-split is
what makes the flat mark read as three-dimensional. ``depth`` is unchanged and
remains the shading / z-split scalar every existing contract reads.

Owner 2026-10-19 (task 6): the whole ``DEPTH_*`` ladder was WIDENED (stronger
perspective, a bigger near-body pop-out, more parallax, more skew and lift) so
the volume reads deeper front to back. All of it is scalar gain the renderers
already multiply; no key changed shape and nothing needs to be learned.

Volume convention (ADDITIVE): every element dict also carries ``z`` and ``z2``,
two INDEPENDENT deterministic depth axes in 0..1 built by :func:`depth_axes`
from ``hash01`` of the element's index with two distinct salts. ``depth`` is a
flat function of an element's orbit angle, so on its own it leaves elements at
the same orbit phase coplanar; ``z`` decorrelates them (a real volume) and
``z2`` gives a second, independent axis so a renderer can build a true 2.5D/3D
cloud rather than one scalar. Both are purely additive — nothing that reads
``depth`` changes, and a renderer may ignore ``z``/``z2`` entirely.
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
#: Owner 2026-10-19 (task 6): "enhance 3d animation overall and how it tries to
#: break the screen". The depth ladder was deliberately WIDENED so the mark
#: reads as a volume rather than a shallow sheet: near bodies are pushed
#: strongly OUT of the screen, far bodies contract hard toward the centre, and
#: the plane-skew grows with it. Every gain is a pure scalar the renderers
#: already multiply (size, alpha, lift, position, arm shear), so the enhancement
#: is ADDITIVE — no key changed shape, nothing new for a renderer to learn.
DEPTH_RADIUS_BASE = 0.42
DEPTH_RADIUS_GAIN = 1.05
DEPTH_ALPHA_BASE = 0.10
DEPTH_ALPHA_GAIN = 1.00
DEPTH_LIFT_BASE = 0.04
#: Near bodies lift further toward white, so the front of the volume reads hot
#: and the back stays cool — a stronger front/back light separation.
DEPTH_LIFT_GAIN = 0.50
#: Parallax along the orbit normal: the nearest logo particles sit visibly
#: off-plane from the farthest, which is the strongest single "this is 3D"
#: cue on the nine bodies (raised 0.20 -> 0.30 for the task-6 push).
DEPTH_PARALLAX = 0.30
#: Far points contract toward the centre: a cheap perspective cue, raised so
#: the ellipses read as receding into the screen instead of flat on the glass.
DEPTH_PERSPECTIVE = 0.30
#: Elements nearest the viewer grow past their plane's size so the field reads
#: as coming OUT of the screen (and the nearest can cross the orbit envelope).
#: Raised 0.70 -> 0.95: the pop-out is the depth cue the owner asked to amplify.
DEPTH_POPOUT = 0.95
#: Depth of field: ``dof()`` maps a particle's depth to an out-of-focus amount.
DOF_NEAR = 0.0
DOF_FAR = 1.0
DOF_GAMMA = 1.7
#: Per-depth shear (degrees) a renderer may rotate a body by. A point on a
#: tilted plane is foreshortened along one axis; a few degrees of skew is what
#: stops a glint from reading as a flat sticker pasted on the orbit. Raised
#: 7 -> 9 with the rest of the ladder so a body's lean tracks its depth harder.
SKEW_GAIN_DEG = 9.0

# --------------------------------------------------------- volume axes (z/z2)
#: ADDITIVE volume axes. ``depth`` (above) is a flat function of an element's
#: orbit angle, so elements sharing a phase share a plane. ``z``/``z2`` are two
#: independent 0..1 hashes of the element's index — distinct salts so the axes
#: never move in lockstep — that let a renderer offset an element along two
#: independent directions and so build a genuine 2.5D/3D cloud. ``depth`` is
#: untouched; these are extra keys only.
Z_SALT = 14.7
#: Distinct from ``Z_SALT`` on purpose: same hash, different salt, so ``z`` and
#: ``z2`` are uncorrelated.
Z2_SALT = 42.9

# ------------------------------------------------------------ camera drift
CAMERA_SWAY_FRAC = 0.016
CAMERA_SPEED_X = 0.27
CAMERA_SPEED_Y = 0.19
#: A second, much slower camera term on a NON-HARMONIC period. One sine per
#: axis reads as a metronome; summing two periods that never lock gives the
#: slow, unrepeating float of a hand-held camera. The weights sum to 1.0, so
#: the total sway is still bounded by :data:`CAMERA_SWAY_FRAC`.
CAMERA_SWAY_WEIGHTS = (0.68, 0.32)
CAMERA_SWAY_PERIOD_X2 = 11.7
CAMERA_SWAY_PERIOD_Y2 = 17.3
CAMERA_SWAY_PHASE_X2 = 1.1
CAMERA_SWAY_PHASE_Y2 = 2.4

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

# ------------------------------------------------------- whole-mark turns
#: The combined X+Y tumble of the whole assembly — orb in the middle, circles
#: outside it, full 360s on both axes. The field publishes only the ANGLES;
#: both renderers apply them, Y first then X, identically, or Tk and Qt tumble
#: in different directions. The periods are non-harmonic against each other
#: and the ring periods, so the tumble never locks to the shells.
Y_SPIN_PERIOD_S = 20.5
X_SPIN_PERIOD_S = 27.0


def spin_angles(t: Any) -> Tuple[float, float]:
    """(theta_y, phi_x) whole-mark turns in RADIANS at clock ``t``.

    ``theta_y = 2*pi*t/20.5`` about the vertical axis FIRST, then
    ``phi_x = 2*pi*t/27.0`` about the horizontal one. Both are linear ramps
    from zero, so the tumble is continuous and deterministic — and never a
    thin line, because neither axis holds the edge-on pose: the two turns
    keep pulling each other off it.
    """
    tt = _finite(t)
    return (2.0 * math.pi * tt / Y_SPIN_PERIOD_S,
            2.0 * math.pi * tt / X_SPIN_PERIOD_S)
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
#: Fraction of the ring's life spent fading IN from zero alpha. Without an
#: attack the ring popped into existence at full opacity on the beat; with it
#: the expansion is born out of the nucleus and only then becomes visible.
SHOCKWAVE_ATTACK_FRAC = 0.18

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

#: Dust motes: a denser, slower field so the orbits are never bare. Owner
#: 2026-10-19: "add more particles ... small ones". The count is up and every
#: mote is smaller, so the field reads as a fine pinprick scatter instead of
#: a handful of visible beads.
DUST_COUNT = 110
DUST_R_FRAC_MIN = 0.86
DUST_R_FRAC_SPAN = 0.30
DUST_SIZE_BASE = 0.070
DUST_SIZE_SPAN = 0.090
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
NUCLEUS_SPARK_COUNT = 32
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
#: Glints: the dense twinkle cloud. Owner 2026-10-19: "add more particles".
#: More, smaller glints fill the volume with pinpricks instead of a few
#: readable stars. They are batched by the painter, so the count is cheap.
SPARKLE_COUNT = 110
SPARKLE_SEED_STEP = 97.3
SPARKLE_R_MIN_FRAC = 0.30
SPARKLE_R_SPAN_FRAC = 0.78
SPARKLE_LIFE_BASE = 1.1
SPARKLE_LIFE_STEP = 0.35
SPARKLE_VISIBLE_FRAC = 0.45
SPARKLE_MAX_ALPHA = 0.60
SPARKLE_SIZE_BASE = 0.060
SPARKLE_SIZE_PEAK = 0.110
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
#: Owner: two colours per streak, not a rainbow. Every streak is a DUOTONE:
#: a head hue and exactly one partner ``HUE_PAIR_OFFSET`` of a turn away, and
#: every sample's hue is the lerp between the two — two hues per streak by
#: construction. ``_hue_pair`` is the one place the partner is computed.
#:
#: The partner is a NEARBY hue (a fraction of a turn), NOT the opposite colour.
#: A half-turn partner (the original 0.5) made the lerp between the two ends
#: sweep straight through the whole wheel — cyan -> green -> yellow -> red — so
#: every streak was a full rainbow and the mark read "too coloured" (owner
#: 2026-10-18). A small offset keeps the streak two neighbouring tints of ONE
#: colour family, so the mark stays a couple of colours total. Owner follow-up
#: ("more professional"): the offset is tightened again so even the two-tone
#: walk stays inside one hue family rather than crossing into a neighbour.
HUE_PAIR_OFFSET = 0.045
#: Saturation of a streak. Lowered from the neon 0.90 the owner called "too
#: colored": most bodies now sit in the 0.62..0.82 band, a restrained, matte
#: look rather than a saturated glow. The HEAD of every streak is pulled
#: further toward white (see ``RGB_TRAIL_WHITE_FRAC``) so each body carries a
#: neutral highlight and only its tail keeps colour — the "professional"
#: highlight/lowlight split rather than a coloured blob.
RGB_TRAIL_SAT_BASE = 0.62
RGB_TRAIL_SAT_SPAN = 0.20
#: How far toward white (zero saturation) the HEAD of a streak is pulled;
#: 0.0 = fully coloured head, 1.0 = white head. Falls off along the tail, so
#: only the leading edge reads as a neutral specular highlight.
RGB_TRAIL_WHITE_FRAC = 0.55
RGB_TRAIL_VAL_BASE = 1.00
#: Body hues live in a band around the mark's own cyan/violet so a field of
#: them still reads as ONE mark; the trail then walks the wheel away from it.
#: Kept for API compatibility; body hues now come from :data:`PALETTE_HUES`
#: through :func:`palette_hue`, not from this band.
PARTICLE_HUE_BASE = 0.52
PARTICLE_HUE_SPAN = 0.34

#: Owner: the mark is "still too colored". Every body used to draw its own
#: wheel position across ``PARTICLE_HUE_BASE..+SPAN`` (0.52 -> 0.86, i.e. six-
#: plus distinct colours). ``PALETTE_HUES`` collapses that spray to a small,
#: fixed set — now just TWO adjacent cool hues, the mark's own cyan and the
#: azure next to it — and :func:`palette_hue` is the ONE place a body hue is
#: chosen, so the whole field carries at most two colours. The duotone partner
#: still comes from :func:`_hue_pair` (a small nearby offset), so streaks stay
#: two-colour. The breadth moved OUT of the hue axis and INTO value/warmth:
#: the neutral white heads carry the highlights the extra hues used to.
PALETTE_HUES: Tuple[float, ...] = (0.54, 0.60)

#: The short version for the dense field (dust and glints): fewer samples,
#: dimmer and shorter, so 30 motes do not turn into 30 comets.
DUST_TRAIL_SAMPLES = 4
DUST_TRAIL_STEP_PX = 1.5
DUST_TRAIL_ALPHA_BASE = 0.22
DUST_TRAIL_SIZE_BASE = 0.70
DUST_TRAIL_HUE_STEP = 0.09


# ------------------------------------------------------------------ helpers
def _finite(value: Any, default: float = 0.0) -> float:
    # Fast path: callers hand us floats almost every time; skip float()/try.
    if type(value) is float:
        return value if math.isfinite(value) else default
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


def palette_hue(index: Any, salt: float = 0.0) -> float:
    """One of :data:`PALETTE_HUES`, chosen deterministically for ``index``.

    The single entry point for a body hue: ``hash01`` of the index (and an
    optional per-layer ``salt`` so different layers spread across the few
    entries) picks the palette slot. Junk-safe like every field helper — a bad
    index or salt still returns a real palette hue, never raises.
    """
    n = len(PALETTE_HUES)
    if n <= 0:
        return 0.0
    pick = int(hash01(int(_finite(index)) + 1, 7.7 + _finite(salt)) * n)
    if pick < 0:
        pick = 0
    elif pick >= n:
        pick = n - 1
    return PALETTE_HUES[pick]


def _hue_for(index: Any, seed: float = 0.0) -> float:
    """A body hue drawn from the small fixed palette, deterministic in index.

    Routed through :func:`palette_hue` so every layer shares the same
    disciplined set instead of each body spraying the wheel; ``seed`` still
    selects which palette entry, so the layers spread across the few hues.
    """
    return palette_hue(index, seed)


def _hue_pair(hue: Any) -> float:
    """The duotone partner of ``hue``: half a turn away, in 0..1."""
    return (_finite(hue) % 1.0 + HUE_PAIR_OFFSET) % 1.0


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

    Duotone: the streak's pair is the drifted head hue and its
    :func:`_hue_pair` partner, and sample ``k`` is the lerp between them —
    exactly two hues per streak, never a spectrum walk. ``hue_step`` is kept
    for signature compatibility only.
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
    # The pair drifts with the clock TOGETHER: both endpoints shift by the
    # same amount, so the half-turn relationship survives every moment.
    head = (base_hue + tt * RGB_TRAIL_HUE_DRIFT) % 1.0
    tail = _hue_pair(head)
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
        tz, tz2 = _volume_axes(tt, int(_finite(idx)) + k, 123.4)
        out.append({
            "k": float(k),
            "x": x, "y": y,
            "hue": (head + (k / float(n - 1) if n > 1 else 0.0)
                    * HUE_PAIR_OFFSET) % 1.0,
            "hue2": tail,
            "sat": max(0.0, min(1.0,
                               sat * (1.0 - RGB_TRAIL_WHITE_FRAC * (1.0 - f)))),
            "val": RGB_TRAIL_VAL_BASE * (1.0 - 0.25 * f),
            "alpha": alpha_base * strength * ((1.0 - f) ** RGB_TRAIL_FALLOFF),
            "size": size * size_base * (1.0 - RGB_TRAIL_TAPER * f),
            "depth": depth,
            "z": tz, "z2": tz2,
            "dof": dof(depth),
        })
    return out


def rgb_trail(t: Any, R: float, particle: Dict[str, Any]) -> List[Dict[str, float]]:
    """The duotone streak behind one orbiting star.

    ``RGB_TRAIL_SAMPLES`` samples, head first: ``{"x", "y", "hue", "hue2",
    "sat", "val", "alpha", "size", "depth", "dof", "k"}``. ``hue``/``hue2``
    are the streak's pair in TURNS (feed ``hue`` to :func:`hue_to_rgb`): the
    head's drifted hue and its half-turn partner, lerped along the tail, so
    one star leaves a two-colour streak. The pair drifts with the clock, so
    the tail is alive rather than a painted gradient. A particle dict from
    :func:`particle_state` works as-is; any dict with ``x``/``y``/``vx``/``vy``
    streaks along its own velocity.

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
    """The short duotone streak for a dust mote or a sparkle.

    Same sample dict as :func:`rgb_trail`, four samples and much dimmer, so a
    dense field gains colour without gaining a comet per mote.
    """
    return _rgb_trail(t, R, mote, DUST_TRAIL_SAMPLES, 0.0,
                      DUST_TRAIL_STEP_PX, DUST_TRAIL_ALPHA_BASE,
                      DUST_TRAIL_SIZE_BASE, DUST_TRAIL_HUE_STEP)


#: Memo for ``hash01``. Every field layer hashes each element's INDEX with a
#: fixed salt to derive its constant per-element data (radius, period, phase,
#: hue, volume axes). Those values do NOT depend on the clock, yet the whole
#: set was re-hashed every frame — thousands of ``math.sin`` calls a frame for
#: numbers that never change. Caching by ``(index, salt)`` removes that cost
#: entirely after the first frame; the salt set is a fixed list of literals, so
#: the cache is bounded. Same values, just computed once.
_HASH01_CACHE: Dict[Any, float] = {}


def hash01(i: int, salt: float = 0.0) -> float:
    """Deterministic 0..1 hash of an index — the field's only 'randomness'."""
    key = (i, salt)
    got = _HASH01_CACHE.get(key)
    if got is None:
        x = math.sin((i + 1) * 12.9898 + salt * 78.233) * 43758.5453
        got = x - math.floor(x)
        _HASH01_CACHE[key] = got
    return got


def depth_axes(index: Any, salt: float = 0.0) -> Tuple[float, float]:
    """``(z, z2)``: two independent deterministic volume axes in 0..1.

    ``z = hash01(index, Z_SALT + salt)`` and ``z2 = hash01(index, Z2_SALT +
    salt)``. The salts are distinct, so the two axes are uncorrelated: a
    renderer can offset a body along two independent directions (a real volume)
    instead of one. ADDITIVE — ``depth`` is unchanged and remains the shading /
    z-split scalar every existing contract reads. Renderers may ignore these.
    """
    i = int(_finite(index))
    return (hash01(i, Z_SALT + salt), hash01(i, Z2_SALT + salt))


#: The volume axes are no longer frozen per body: each one breathes on its own
#: slow NON-HARMONIC period, so a renderer's parallax offset drifts gently and
#: the cloud keeps changing shape instead of translating as one rigid slab.
#: The sway is deliberately small (±5% of an axis) — it must read as the volume
#: settling, never as a body teleporting between depth planes.
VOLUME_SWAY_PERIOD_A = 9.7
VOLUME_SWAY_PERIOD_B = 13.3
VOLUME_SWAY_SPAN = 0.052
VOLUME_SWAY_PHASE = 0.7


def _volume_axes(t: Any, index: Any, salt: float = 0.0) -> Tuple[float, float]:
    """Time-varying ``(z, z2)``: :func:`depth_axes` plus a gentle parallax sway.

    ADDITIVE on top of :func:`depth_axes` (which stays the pure, frozen hash
    contract): the two axes drift on independent non-harmonic periods around
    their hashed home, clamped back into 0..1. The motion is C1 in ``t`` and
    carries zero velocity at the turnarounds, so a body's volume offset never
    steps between frames.
    """
    i = int(_finite(index))
    z, z2 = depth_axes(i, salt)
    tt = _finite(t)
    seed = hash01(i + 1, 907.3 + _finite(salt)) * 2.0 * math.pi
    z += VOLUME_SWAY_SPAN * math.sin(
        2.0 * math.pi * tt / VOLUME_SWAY_PERIOD_A + seed)
    z2 += VOLUME_SWAY_SPAN * math.sin(
        2.0 * math.pi * tt / VOLUME_SWAY_PERIOD_B + seed * 1.37
        + VOLUME_SWAY_PHASE)
    return (min(1.0, max(0.0, z)), min(1.0, max(0.0, z2)))


# --------------------------------------------------------- micro-motion field
#: Owner 2026-10-19: "refine orb complex animations more make more fluid ...
#: more detailed". Every body gains a SECONDARY, bounded micro-oscillation on
#: top of its orbit: two non-harmonic sines per axis (sum of weights = 1.0),
#: so neighbouring bodies never wobble in unison and the field shimmers like
#: something alive. It is pure screen-space displacement — it cannot change an
#: orbit's topology or a ring's declared shape.
MICRO_WOBBLE_FRAC = 0.010
MICRO_WOBBLE_PERIOD_A = 6.9
MICRO_WOBBLE_PERIOD_B = 11.3
MICRO_WOBBLE_PERIOD_C = 8.7
MICRO_WOBBLE_PERIOD_D = 15.1
MICRO_WOBBLE_WEIGHTS = (0.62, 0.38)


def micro_offset(t: Any, index: Any, R: float,
                 salt: float = 0.0) -> Tuple[float, float]:
    """Bounded screen-space micro-wobble ``(dx, dy)`` in pixels.

    Two non-harmonic sines per axis, each weighted so every AXIS stays within
    ``MICRO_WOBBLE_FRAC * R`` (the 2-D magnitude can reach ``sqrt(2)`` times
    that). Deterministic in ``(index, salt)``, C1 in ``t`` (pure sines, no
    wrap), and junk-safe like every field helper.
    """
    i = int(_finite(index))
    r = abs(_finite(R, 32.0)) or 32.0
    tt = _finite(t)
    amp = MICRO_WOBBLE_FRAC * r
    w0, w1 = MICRO_WOBBLE_WEIGHTS
    s = hash01(i + 1, 911.7 + _finite(salt)) * 2.0 * math.pi
    x = (w0 * math.sin(2.0 * math.pi * tt / MICRO_WOBBLE_PERIOD_A + s)
         + w1 * math.sin(2.0 * math.pi * tt / MICRO_WOBBLE_PERIOD_B
                         + s * 1.61 + 0.9))
    y = (w0 * math.sin(2.0 * math.pi * tt / MICRO_WOBBLE_PERIOD_C + s * 0.73)
         + w1 * math.sin(2.0 * math.pi * tt / MICRO_WOBBLE_PERIOD_D
                         + s * 2.17 + 2.1))
    return (amp * x, amp * y)


#: A slow, per-body drift in 0..1 (inward-only when a layer multiplies a radius
#: by ``1 - k * drift``). A single cosine on a per-body non-harmonic period, so
#: nothing breathes as one. Used for the breathing radius/glow detail on the
#: volume, orbiter, cylinder, core and break layers.
DRIFT_PERIOD_BASE = 7.9
DRIFT_PERIOD_SPAN = 6.3
GLOW_PERIOD_BASE = 4.7
GLOW_PERIOD_SPAN = 5.3


def _drift01(t: Any, index: Any, salt: float = 0.0) -> float:
    """A smooth per-body 0..1 drift on its own non-harmonic period."""
    i = int(_finite(index))
    tt = _finite(t)
    period = DRIFT_PERIOD_BASE + DRIFT_PERIOD_SPAN * hash01(i + 1, 919.1 + salt)
    phase = hash01(i + 1, 921.7 + salt) * 2.0 * math.pi
    return 0.5 + 0.5 * math.sin(
        2.0 * math.pi * tt / max(0.1, period) + phase)


def _glow(t: Any, index: Any, salt: float = 0.0) -> float:
    """A smooth per-body glow multiplier around 1.0, own non-harmonic period."""
    i = int(_finite(index))
    tt = _finite(t)
    period = GLOW_PERIOD_BASE + GLOW_PERIOD_SPAN * hash01(i + 1, 923.3 + salt)
    phase = hash01(i + 1, 927.1 + salt) * 2.0 * math.pi
    return 1.0 + 0.10 * math.sin(
        2.0 * math.pi * tt / max(0.1, period) + phase)


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
    """Small floating sway of the whole mark; sells the depth fake.

    Two non-harmonic periods per axis, summed with weights that total 1.0, so
    the drift never settles into a visible beat and never exceeds the declared
    sway. Continuous in ``t`` (pure sines).
    """
    tt = _finite(t)
    r = _finite(R, 1.0) or 1.0
    w0, w1 = CAMERA_SWAY_WEIGHTS
    sx = (w0 * math.sin(tt * CAMERA_SPEED_X)
          + w1 * math.sin(2.0 * math.pi * tt / CAMERA_SWAY_PERIOD_X2
                          + CAMERA_SWAY_PHASE_X2))
    sy = (w0 * math.cos(tt * CAMERA_SPEED_Y)
          + w1 * math.sin(2.0 * math.pi * tt / CAMERA_SWAY_PERIOD_Y2
                          + CAMERA_SWAY_PHASE_Y2))
    return (sx * r * CAMERA_SWAY_FRAC, sy * r * CAMERA_SWAY_FRAC * 0.6)


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
    # Eased expansion, with a short attack so the ring does not pop into
    # existence at full opacity on the beat. smoothstep has zero slope at both
    # ends: the ring is born out of the nucleus, expands, and settles as it
    # dies — no velocity or opacity step anywhere, and exactly (0, 0) outside
    # the window.
    grow = smoothstep(f)
    attack = SHOCKWAVE_ATTACK_FRAC
    rise = smoothstep(f / attack) if attack > 0.0 else 1.0
    return (1.0 + (SHOCKWAVE_MAX_SCALE - 1.0) * grow,
            SHOCKWAVE_ALPHA * rise * (1.0 - grow))


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

    Constant angular speed + a small offset: the motion breathes without ever
    accelerating with uptime. The offset is a SUM of two non-harmonic sines
    (weights total 1.0), so the wander is irregular rather than a pure
    back-and-forth; it stays bounded by :data:`SPEED_MOD_DEG` and is C1 in
    ``t`` because both terms are pure sines of absolute time.
    """
    tt = _finite(t)
    ph = seed * math.pi / 180.0
    fast = math.sin(tt * SPEED_MOD_HZ * 2.0 * math.pi + ph)
    slow = math.sin(tt * SPEED_MOD_HZ * 0.37 * 2.0 * math.pi + ph * 1.7
                    + 0.9)
    return SPEED_MOD_DEG * (0.66 * fast + 0.34 * slow)


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
        # Owner 2026-10-19: "different motions ... not all in same direction".
        direction = 1.0 if hash01(idx, 19.3) >= 0.5 else -1.0
        ang = (seed + direction * tt * 360.0 / max(0.1, period)
               + _phase_wobble(tt, seed))
        depth = _depth(ang)
        # Perspective: far points pull toward the centre.
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        par = (depth - 0.5) * DEPTH_PARALLAX * r
        x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
        nx, ny = (math.sin(math.radians(tilt)), -math.cos(math.radians(tilt)))
        # Parallax along the orbit normal, then the camera sway, then the
        # per-body micro-wobble (a second, smaller oscillation on top of the
        # orbit, so no two stars move on identical rails).
        tw = glint(tt, seed)
        wx, wy = micro_offset(tt, idx, r, 17.1)
        pz, pz2 = _volume_axes(tt, idx, 17.1)
        out.append({
            "index": float(idx), "orbit": orbit, "angle": ang, "depth": depth,
            "x": x + nx * par + dx + wx,
            "y": y + ny * par * 0.35 + dy + wy,
            "z": pz, "z2": pz2,
            "wobble": math.hypot(wx, wy),
            "size": _depth_size(depth, size96 * scale) * tw["scale"],
            "alpha": _depth_alpha(depth) * tw["alpha"],
            "lift": _depth_lift(depth),
            "glint_scale": tw["scale"], "glint_alpha": tw["alpha"],
            "hue": _hue_for(idx, 31.7), "hue2": _hue_pair(_hue_for(idx, 31.7)),
            "dof": dof(depth),
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
    # A second, faster shimmer on an absolute-time period (NOT a multiple of
    # the wrapped phase, which would step at the phase seam). It is deliberately
    # small: the twinkle stays the main event, but a glint now has a live
    # micro-flicker instead of a single clean cosine.
    flicker = 0.5 + 0.5 * math.sin(
        2.0 * math.pi * tt / 1.13 + seed * 0.021)
    scale = 0.75 + 0.45 * wave + 0.055 * (flicker - 0.5)
    alpha = (GLINT_MIN_ALPHA + (1.0 - GLINT_MIN_ALPHA) * wave
             + 0.08 * (flicker - 0.5))
    return {"scale": scale,
            "alpha": min(1.0, max(GLINT_MIN_ALPHA, alpha)),
            "flicker": flicker}


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
        # The launch profile is part of the published spark contract (Qt's
        # outward-shower test recovers the phase from these curves), so it is
        # kept: ``f ** 0.65`` is the ejection, ``(1 - f) ** 1.5`` the fade.
        grow = f ** 0.65
        dist = r * NUCLEUS_SPARK_REACH * grow
        ang = math.radians(seed + 40.0 * phase)
        x = math.cos(ang) * dist
        y = math.sin(ang) * dist * 0.72  # squash: the plane is tilted
        # Reaches exactly zero, with zero slope, at the skip boundary above.
        fade = (1.0 - f) ** 1.5
        if NUCLEUS_SPARK_ALPHA * fade < NUCLEUS_SPARK_MIN_ALPHA:
            continue
        sz, sz2 = _volume_axes(tt, i, 197.9)
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": 0.5 + 0.5 * math.cos(ang),
            "z": sz, "z2": sz2,
            "hue": _hue_for(i, 97.1), "hue2": _hue_pair(_hue_for(i, 97.1)),
            "dof": dof(0.5 + 0.5 * math.cos(ang)),
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
            sz, sz2 = _volume_axes(tt, int(idx) * 100 + k, 151.7)
            trail.append({"x": tx + dx, "y": ty + dy,
                          "z": sz, "z2": sz2,
                          "size": _depth_size(d, size96 * scale) * 0.5,
                          "alpha": 0.30 * alpha * (1.0 - k / (SATELLITE_TRAIL_SAMPLES + 1.0)),
                          "lift": lift})
        bvx, bvy = _orbit_velocity(rx, ry, tilt, ang)
        bz, bz2 = _volume_axes(tt, idx, 157.3)
        out.append({
            "index": float(idx),
            "ring": {"tilt": tilt, "rx_frac": rx_frac,
                     "ry_frac": rx_frac * (RY_FRAC / RX_FRAC) * ratio,
                     "alpha": SATELLITE_RING_ALPHA, "width": SATELLITE_RING_WIDTH},
            "body": {"index": float(idx), "x": bx + dx, "y": by + dy,
                      "depth": depth, "hue": _hue_for(idx, 89.3),
                      "hue2": _hue_pair(_hue_for(idx, 89.3)),
                     "z": bz, "z2": bz2,
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
        tz, tz2 = _volume_axes(tt, int(_finite(particle.get("index", 0.0))) + k,
                             173.1)
        out.append({
            "x": x + dx, "y": y + dy,
            "z": tz, "z2": tz2,
            "hue": _hue_for(particle.get("index", 0.0), 31.7),
            "hue2": _hue_pair(_hue_for(particle.get("index", 0.0), 31.7)),
            "dof": dof(depth),
            "size": base_size * TRAIL_RADIUS_BASE * (1.0 - 0.10 * k),
            "alpha": TRAIL_ALPHA_BASE * ((1.0 - k / (TRAIL_SAMPLES + 1.0))
                                         ** TRAIL_FALLOFF),
            "lift": _depth_lift(depth),
            "depth": depth,
        })
    return out


def dust_state(t: Any, R: float) -> List[Dict[str, float]]:
    """Denser motes spread across EVERY declared ring, not just the two orbits.

    Each mote rides one of the circles :func:`ring_geometry` declares (cycling
    the list so the field is populated uniformly), on that ring's REAL animated
    path — live tilt plus precession and a radius scaled by
    :func:`ring_radius_scale`. The dict shape is unchanged.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    # Declare the same circles the renderers stroke, but from the field's own
    # tilts directly: dust rides the geometry, not a stubbed ``echo_rings``.
    # ``ring_geometry`` composes its echo tilts from ``echo_rings(tt -
    # ECHO_LAG_S)``, which subtracts the lag itself, so the declared follower
    # sits at a DOUBLE lag — match it exactly so dust rides the drawn circle.
    near_tilt, far_tilt = orbit_tilts(tt)
    lag_near, lag_far = orbit_tilts(tt - 2.0 * ECHO_LAG_S)
    rings = _ring_specs(tt, near_tilt, far_tilt, lag_near, lag_far)
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, float]] = []
    for i in range(DUST_COUNT):
        ring = rings[i % len(rings)] if rings else {}
        tilt = (_finite(ring.get("tilt"))
                + _finite(ring.get("tilt_spin_deg")))
        rad = ring_radius_scale(tt, ring)
        rx = r * _finite(ring.get("rx_frac"), RX_FRAC) * rad
        ry = r * _finite(ring.get("ry_frac"), RY_FRAC) * rad
        seed = 360.0 * hash01(i, 1.7)
        period = DUST_PERIOD_BASE + DUST_PERIOD_SPAN * hash01(i, 3.1)
        direction = 1.0 if hash01(i, 21.1) >= 0.5 else -1.0
        ang = (seed + direction * tt * 360.0 / max(0.1, period)
               + _phase_wobble(tt, seed))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        grow = DUST_R_FRAC_MIN + DUST_R_FRAC_SPAN * hash01(i, 5.9)
        x, y = orbit_point(0.0, 0.0, rx * pull * grow, ry * pull * grow, tilt, ang)
        vx, vy = _orbit_velocity(rx * pull * grow, ry * pull * grow, tilt, ang)
        dz, dz2 = _volume_axes(tt, i, 23.3)
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": depth,
            "z": dz, "z2": dz2,
            "vx": vx * direction * 360.0 / max(0.1, period),
            "vy": vy * direction * 360.0 / max(0.1, period),
            "hue": _hue_for(i, 53.9), "hue2": _hue_pair(_hue_for(i, 53.9)),
            "dof": dof(depth),
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
        tz, tz2 = _volume_axes(tt, k, 181.3)
        tail.append({"x": tx + dx, "y": ty + dy,
                     "z": tz, "z2": tz2,
                     "size": COMET_SIZE * scale * 0.62 * (1.0 - k / 14.0),
                     "alpha": COMET_ALPHA * fade * (1.0 - k / (COMET_TRAIL_SAMPLES + 1.0)) ** 1.3,
                     "lift": COMET_LIFT})
    cvx, cvy = _orbit_velocity(rx * pull, ry * pull, near_tilt, ang)
    cz, cz2 = _volume_axes(tt, 0, 187.7)
    return {"x": x + dx, "y": y + dy, "depth": depth,
            "index": 0.0, "hue": _hue_for(0, 101.3),
            "hue2": _hue_pair(_hue_for(0, 101.3)),
            "z": cz, "z2": cz2,
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
        direction = 1.0 if hash01(i, 25.7) >= 0.5 else -1.0
        rate = direction * (18.0 + (i % 5) * 6.0)
        ang = seed + tt * rate
        rfrac = SPARKLE_R_MIN_FRAC + SPARKLE_R_SPAN_FRAC * hash01(i, 13.7)
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        sx, sy = r * rfrac * RX_FRAC * pull, r * rfrac * RY_FRAC * pull
        x, y = orbit_point(0.0, 0.0, sx, sy, near_tilt, ang)
        vx, vy = _orbit_velocity(sx, sy, near_tilt, ang)
        wx, wy = micro_offset(tt, i, r, 189.7)
        gz, gz2 = _volume_axes(tt, i, 191.3)
        out.append({"index": float(i), "x": x + dx + wx,
                    "y": y + dy + wy,
                    "depth": depth, "hue": _hue_for(i, 67.1),
                    "hue2": _hue_pair(_hue_for(i, 67.1)),
                    "z": gz, "z2": gz2,
                    "dof": dof(depth), "skew": skew(depth),
                    "vx": vx * rate, "vy": vy * rate,
                    "size": (SPARKLE_SIZE_BASE + SPARKLE_SIZE_PEAK * peak)
                            * (r / 32.0),
                    "alpha": SPARKLE_MAX_ALPHA * peak,
                    "lift": SPARKLE_LIFT})
    return out


# ------------------------------------------------------------- blink dots
#: Tiny dots that materialise out of nowhere: fixed deterministic positions
#: with NO travel — only a raised-cosine alpha envelope 0 -> peak -> 0, so
#: each dot fades in from nothing and vanishes the same way, like data
#: materialising. The full count is always returned (stable frame to frame);
#: the envelope decides which dots are visible. Owner 2026-10-19: more of
#: them, smaller, and spread further out, so the pinprick field reads as
#: scattered data rather than a ring of beads.
BLINK_COUNT = 52
BLINK_SIZE_BASE = 0.060
BLINK_SIZE_SPAN = 0.080
BLINK_ALPHA_PEAK = 0.75
BLINK_LIFE_BASE = 2.2
BLINK_LIFE_SPAN = 2.6
BLINK_LIFT = 0.65
BLINK_R_FRAC_MIN = 0.22
BLINK_R_FRAC_SPAN = 0.80


def blink_dots(t: Any, R: float) -> List[Dict[str, float]]:
    """Dots that appear out of nowhere and vanish, travelling nowhere.

    One dict per dot: ``{"index", "x", "y", "depth", "size", "alpha",
    "lift", "hue", "hue2", "dof", "skew"}``. Positions are fixed orbit
    points from ``hash01``; only ``alpha`` moves, through a raised-cosine
    envelope with zero slope at both ends, so a birth and a death are both
    fades rather than pops.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, far_tilt = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, float]] = []
    for i in range(BLINK_COUNT):
        tilt = near_tilt if i % 2 else far_tilt
        ang = 360.0 * hash01(i, 31.9)
        life = BLINK_LIFE_BASE + BLINK_LIFE_SPAN * hash01(i, 33.3)
        phase = (tt / max(0.1, life) + hash01(i, 35.7)) % 1.0
        env = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        grow = BLINK_R_FRAC_MIN + BLINK_R_FRAC_SPAN * hash01(i, 37.1)
        x, y = orbit_point(0.0, 0.0, r * RX_FRAC * pull * grow,
                           r * RY_FRAC * pull * grow, tilt, ang)
        hue = _hue_for(i, 91.7)
        bz, bz2 = _volume_axes(tt, i, 227.1)
        out.append({
            "index": float(i), "x": x + dx, "y": y + dy, "depth": depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "z": bz, "z2": bz2,
            "dof": dof(depth), "skew": skew(depth),
            "size": ((BLINK_SIZE_BASE + BLINK_SIZE_SPAN * hash01(i, 39.3))
                     * scale * (DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth)),
            "alpha": BLINK_ALPHA_PEAK * env * (0.35 + 0.65 * depth),
            "lift": BLINK_LIFT,
        })
    return out


# --------------------------------------------------------- flare blink family
#: A SECOND blink family: the same "materialise in place, never travel" idea
#: but rarer, larger and longer-lived, so the field has occasional bright
#: flares among the dense pinpricks instead of one uniform dot population.
#: Distinct salts and a lower count keep it a rare event, not a second field.
FLARE_COUNT = 8
FLARE_SIZE_BASE = 0.16
FLARE_SIZE_SPAN = 0.10
FLARE_ALPHA_PEAK = 0.50
FLARE_LIFE_BASE = 4.6
FLARE_LIFE_SPAN = 4.0
FLARE_LIFT = 0.55
FLARE_R_FRAC_MIN = 0.42
FLARE_R_FRAC_SPAN = 0.50


def flare_dots(t: Any, R: float) -> List[Dict[str, float]]:
    """Rare, larger blink flares — a second, slower materialise-in-place family.

    Same contract as :func:`blink_dots` (fixed positions, only ``alpha`` moves
    through a raised-cosine 0 -> peak -> 0 envelope, so every birth and death is
    a fade), but fewer, bigger dots with much longer lives. ``FLARE_COUNT``
    is always returned so a renderer's primitive count stays stable. Carries the
    additive ``z``/``z2`` volume axes like every other element.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    near_tilt, far_tilt = orbit_tilts(tt)
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, float]] = []
    for i in range(FLARE_COUNT):
        tilt = far_tilt if i % 2 else near_tilt
        ang = 360.0 * hash01(i, 41.1)
        life = FLARE_LIFE_BASE + FLARE_LIFE_SPAN * hash01(i, 43.7)
        phase = (tt / max(0.1, life) + hash01(i, 45.9)) % 1.0
        env = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        grow = FLARE_R_FRAC_MIN + FLARE_R_FRAC_SPAN * hash01(i, 47.3)
        x, y = orbit_point(0.0, 0.0, r * RX_FRAC * pull * grow,
                           r * RY_FRAC * pull * grow, tilt, ang)
        hue = _hue_for(i, 107.3)
        fz, fz2 = _volume_axes(tt, i, 233.9)
        out.append({
            "index": float(i), "x": x + dx, "y": y + dy, "depth": depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "z": fz, "z2": fz2,
            "dof": dof(depth), "skew": skew(depth),
            "size": ((FLARE_SIZE_BASE + FLARE_SIZE_SPAN * hash01(i, 49.1))
                     * scale * (DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth)),
            "alpha": FLARE_ALPHA_PEAK * env * (0.30 + 0.70 * depth),
            "lift": FLARE_LIFT,
        })
    return out


# ------------------------------------------------------------- inner core ring
INNER_RING_RX_FRAC = 0.32
INNER_RING_PERIOD_S = 4.5
INNER_RING_DIRECTION = 1.0
INNER_RING_TILT_BASE = 0.0

# ------------------------------------------------------------- binary particle field
#: Owner 2026-10-19: "add more ... random 1 0 that spawn everywere small ones".
#: The fragments are no longer parked on the outer orbit: ``BINARY_R_FRAC_MIN``
#: starts near the core and the span reaches past the main orbit, so the glyphs
#: scatter across the whole volume. More of them, each smaller, so they read as
#: data dust instead of legible text.
BINARY_COUNT = 168
BINARY_SIZE_BASE = 0.060
BINARY_SIZE_SPAN = 0.055
BINARY_PERIOD_BASE = 6.0
BINARY_PERIOD_SPAN = 12.0
BINARY_ALPHA_BASE = 0.16
BINARY_ALPHA_GAIN = 0.30
BINARY_LIFT_BASE = 0.50
BINARY_LIFT_GAIN = 0.30
BINARY_R_FRAC_MIN = 0.14
BINARY_R_FRAC_SPAN = 0.95


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
        direction = 1.0 if hash01(i, 27.1) >= 0.5 else -1.0
        ang = (seed + direction * tt * 360.0 / max(0.1, period)
               + _phase_wobble(tt, seed))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        grow = BINARY_R_FRAC_MIN + BINARY_R_FRAC_SPAN * hash01(i, 5.9)
        x, y = orbit_point(0.0, 0.0, rx * pull * grow, ry * pull * grow,
                           tilt, ang)
        vx, vy = _orbit_velocity(rx * pull * grow, ry * pull * grow, tilt, ang)
        # NOTE: keep binary positions on their exact orbit path — the dense
        # glyph field is sampled positionally by the renderer contracts, so no
        # additive screen wobble here.
        bz, bz2 = _volume_axes(tt, i, 211.3)
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": depth,
            "z": bz, "z2": bz2,
            "vx": vx * direction * 360.0 / max(0.1, period),
            "vy": vy * direction * 360.0 / max(0.1, period),
            "hue": _hue_for(i, 71.5), "hue2": _hue_pair(_hue_for(i, 71.5)),
            "dof": dof(depth),
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
        bz, bz2 = _volume_axes(tt, i, 223.7)
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": depth,
            "z": bz, "z2": bz2,
            "vx": vx * direction * 360.0 / max(0.1, period),
            "vy": vy * direction * 360.0 / max(0.1, period),
            "hue": _hue_for(i, 83.9), "hue2": _hue_pair(_hue_for(i, 83.9)),
            "dof": dof(depth),
            "size": (BINARY_SIZE_BASE * 1.1) * scale,
            "alpha": _depth_alpha(depth, BINARY_ALPHA_BASE),
            "lift": BINARY_LIFT_BASE,
            "char": "1" if (i % 2 == 0) else "0",
        })
    return out


# ------------------------------------------------------------ volume particles
#: Owner: "there are some check marks that I don't like, replace the checkmark
#: or v lines or whatever those are with real particles that are like 3d and
#: have animations." The old MATH-GLYPH TEXT layer (the ``√``/``Δ``/``∇``
#: symbols and equations, drawn as ``char`` text) is what read as checkmarks,
#: so it is RETIRED. Its visual role is taken by this: a genuine 3D PARTICLE
#: field — no text at all.
#:
#: Each particle revolves a full 360 in its OWN inclined circular orbit around
#: the nucleus, so its depth crosses both sides of the core every revolution
#: (near half in front, far half behind). Every body carries two independent
#: volume axes (``z``/``z2``), its own screen velocity, and TWO per-body
#: animations: ``pulse`` (a twinkle that scales the body's size and alpha on
#: its own non-harmonic period) and ``spin`` (a body-rotation angle a renderer
#: can apply to a 3D geometry). Size and alpha both derive from ``depth``
#: through the shared :func:`_depth_size`/:func:`_depth_alpha` mapping, so the
#: field reads as volume, not a flat scatter.
#:
#: The dict shape is ADDITIVE and documented on :func:`volume_particles`.
VOLUME_COUNT = 52
VOLUME_R_FRAC_MIN = 0.12
VOLUME_R_FRAC_SPAN = 0.70
VOLUME_PERIOD_BASE = 5.4
VOLUME_PERIOD_SPAN = 9.6
#: Inclinations 18..162 deg keep ``|sin|`` >= 0.31, so even the flattest
#: particle's depth sweeps the ~0.35..0.65 band across a revolution — it
#: genuinely circles the middle rather than sliding on one plane.
VOLUME_INC_MIN_DEG = 18.0
VOLUME_INC_SPAN_DEG = 144.0
VOLUME_SIZE_BASE = 0.20
VOLUME_SIZE_SPAN = 0.14
VOLUME_ALPHA_BASE = 0.26
VOLUME_ALPHA_GAIN = 0.46
VOLUME_LIFT_BASE = 0.30
VOLUME_LIFT_GAIN = 0.46
#: Per-body twinkle periods (non-harmonic with the orbit periods and each
#: other) so the field shimmers instead of pulsing in unison.
VOLUME_PULSE_PERIOD_BASE = 1.4
VOLUME_PULSE_PERIOD_SPAN = 2.4
VOLUME_SPIN_PERIOD_BASE = 3.0
VOLUME_SPIN_PERIOD_SPAN = 5.0


def volume_particles(t: Any, R: float) -> List[Dict[str, Any]]:
    """A deterministic 3D particle field orbiting the nucleus (no text).

    One dict per particle:
    ``{"index", "x", "y", "depth", "z", "z2", "vx", "vy", "size", "alpha",
    "lift", "hue", "hue2", "dof", "skew", "r", "inc", "pulse", "spin"}``.

    * ``depth`` is the shared shading / z-split scalar; ``z``/``z2`` are two
      INDEPENDENT volume axes (see :func:`depth_axes`), so a renderer can build
      a true 3D cloud instead of one scalar.
    * ``vx``/``vy`` are the screen velocity per second, so a renderer can streak
      the particle through :func:`rgb_trail`.
    * ``pulse`` in 0..1 is the body's own twinkle animation (size and alpha
      already carry it); ``spin`` in 0..360 is its own rotation angle for a 3D
      body. Both are per-body, deterministic and non-harmonic.
    * No ``char`` — this layer is never drawn as text.

    Radii, inclinations, periods, directions, phases, pulse and spin periods
    are all derived by ``hash01`` of the index, so the field is varied but
    perfectly reproducible. Pure in ``(t, R)``: no ``random``, no state, no
    I/O, junk-safe and reduced-motion-safe (the renderer decides to drop it).
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    for i in range(VOLUME_COUNT):
        idx = i + 1
        rad = r * (VOLUME_R_FRAC_MIN
                   + VOLUME_R_FRAC_SPAN * hash01(idx, 701.3))
        inc = math.radians(VOLUME_INC_MIN_DEG
                           + VOLUME_INC_SPAN_DEG * hash01(idx, 703.7))
        period = (VOLUME_PERIOD_BASE
                  + VOLUME_PERIOD_SPAN * hash01(idx, 709.1))
        direction = 1.0 if hash01(idx, 711.7) >= 0.5 else -1.0
        phase0 = 360.0 * hash01(idx, 719.3)
        rate = direction * 360.0 / max(0.1, period)
        ang = phase0 + rate * tt + _phase_wobble(tt, phase0)
        ar = math.radians(ang)
        u, v = math.cos(ar), math.sin(ar)
        # The particle's own inclined plane: y is foreshortened by the
        # inclination, the out-of-plane component becomes depth (near half in
        # front of the core, far half behind it). A slow INWARD-only radial
        # drift gives each body a breathing radius, so the shell lives instead
        # of tracing one frozen circle; it can never grow past its declared
        # orbit (and so never past the layer's radius bound).
        drift = _drift01(tt, idx, 731.9)
        rad = rad * (1.0 - 0.045 * drift)
        x = rad * u
        y = rad * v * math.cos(inc)
        zunit = v * math.sin(inc)
        depth = min(1.0, max(0.0, 0.5 + 0.5 * zunit))
        # Per-body animation, both non-harmonic and deterministic.
        pulse_period = (VOLUME_PULSE_PERIOD_BASE
                        + VOLUME_PULSE_PERIOD_SPAN * hash01(idx, 731.9))
        pulse = 0.5 + 0.5 * math.sin(
            2.0 * math.pi * tt / max(0.1, pulse_period)
            + phase0 * math.pi / 180.0)
        spin_period = (VOLUME_SPIN_PERIOD_BASE
                       + VOLUME_SPIN_PERIOD_SPAN * hash01(idx, 733.3))
        spin = (360.0 * tt / max(0.1, spin_period) + phase0) % 360.0
        # A third, much slower per-body glow on its own non-harmonic period:
        # the field's brightness is a living tissue, not one shared flicker.
        glow = _glow(tt, idx, 733.9)
        # Screen velocity per second (the dt of the orbit), for streaking.
        vx = -rad * v * rate
        vy = rad * u * math.cos(inc) * rate
        cz, cz2 = _volume_axes(tt, idx, 723.9)
        hue = _hue_for(idx, 729.1)
        out.append({
            "index": float(idx),
            "x": x + dx, "y": y + dy,
            "depth": depth,
            "z": cz, "z2": cz2,
            "vx": vx, "vy": vy,
            "size": (_depth_size(
                depth,
                (VOLUME_SIZE_BASE
                 + VOLUME_SIZE_SPAN * hash01(idx, 731.3)) * scale)
                * (0.72 + 0.56 * pulse) * (0.96 + 0.08 * drift)),
            "alpha": min(1.0, (_depth_alpha(
                depth, VOLUME_ALPHA_BASE
                + VOLUME_ALPHA_GAIN * hash01(idx, 737.1))
                * (0.55 + 0.45 * pulse) * glow)),
            "lift": VOLUME_LIFT_BASE + VOLUME_LIFT_GAIN * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "r": rad, "inc": inc,
            "pulse": min(1.0, max(0.0, pulse)), "spin": spin,
            "drift": drift, "glow": glow,
        })
    return out


def math_state(t: Any, R: float) -> List[Dict[str, Any]]:
    """RETIRED no-op — the math-glyph TEXT layer is gone.

    Owner 2026-10-19: the ``√``/``Δ``/``∇`` math symbols and equations read as
    "check marks / v lines"; they are replaced by :func:`volume_particles`, a
    real 3D particle field with no ``char`` at all. This name is kept ONLY so
    anything built against the old field stays importable and fail-soft: it
    returns an empty list and never raises, so a renderer draws no glyphs
    instead of blanking. New work must call :func:`volume_particles`.
    """
    return []


# ---------------------------------------------------------------- core shells
#: The nucleus is not a flat disc: it has INTERIOR structure. ``core_shells``
#: declares a few nested bodies at different radii and plane tilts, each
#: turning the nucleus at its own NON-HARMONIC period, so a renderer can draw
#: them as layered depth INSIDE the core (owner: "make inside orb more 3d give
#: more depth and animations"). Radii are fractions of the MARK radius and sit
#: comfortably inside the nucleus body (``NUCLEUS_FRAC`` = 0.05).
CORE_SHELL_COUNT = 6
CORE_SHELL_R_FRAC_MIN = 0.012
CORE_SHELL_R_FRAC_SPAN = 0.033
#: One full-turn period per shell (seconds). Non-harmonic against each other,
#: against the ring turns (9.5/13.5) and against the spins (20.5/27). Six
#: shells (was four) give the interior a deeper, more layered read; the two
#: extra periods are chosen so no pair approaches a simple ratio.
CORE_SHELL_PERIODS_S = (2.9, 4.3, 5.9, 8.1, 6.7, 10.3)
#: Per-shell radius swirl: a small secondary radial oscillation on its own
#: non-harmonic period, so a shell's orbit breathes instead of tracing one
#: fixed circle. Inward-biased (it multiplies by ``1 - span*drift``), so it can
#: never escape the interior or the ``NUCLEUS_FRAC`` bound.
CORE_SHELL_SWIRL_SPAN = 0.14
#: Each shell's orbit plane tilt: base + step*index, so the shells nest at
#: different angles instead of sharing one plane.
CORE_SHELL_TILT_BASE = 16.0
CORE_SHELL_TILT_STEP = 53.0
#: Each shell's own spin phase (degrees): the shell turns under the light,
#: independent of where its body travels.
CORE_SHELL_SPIN_BASE = 11.0
CORE_SHELL_SPIN_STEP = 67.0
CORE_SHELL_SIZE_FRAC = 0.022
CORE_SHELL_SIZE_SPAN = 0.014
CORE_SHELL_ALPHA_BASE = 0.40
CORE_SHELL_ALPHA_GAIN = 0.36
CORE_SHELL_LIFT_BASE = 0.45
CORE_SHELL_LIFT_GAIN = 0.35


def core_shells(t: Any, R: float) -> List[Dict[str, float]]:
    """Nested interior bodies of the nucleus, one dict per shell.

    Each shell: ``{"index", "x", "y", "depth", "z", "z2", "size", "alpha",
    "hue", "hue2", "tilt", "spin_deg", "dof", "skew", "lift", "r_frac"}``.
    ``x``/``y`` are the shell body's position (camera sway included) inside the
    core; ``size`` is its drawn radius; ``tilt``/``spin_deg`` describe its plane
    and its own rotation so a renderer can layer them as real interior depth.
    Deterministic in ``(t, R)``; empty-safe and junk-safe like every layer.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    out: List[Dict[str, float]] = []
    for i in range(CORE_SHELL_COUNT):
        period = CORE_SHELL_PERIODS_S[i % len(CORE_SHELL_PERIODS_S)]
        direction = 1.0 if (i % 2 == 0) else -1.0
        rfrac = (CORE_SHELL_R_FRAC_MIN
                 + CORE_SHELL_R_FRAC_SPAN * hash01(i, 61.3))
        # A secondary radial swirl (inward-biased, so the shell can never
        # escape the nucleus) plus a bounded micro-wobble: the interior bodies
        # drift against each other instead of running one rigid clockwork.
        swirl = _drift01(tt, i, 61.3)
        rx = r * rfrac * (1.0 - CORE_SHELL_SWIRL_SPAN * swirl)
        ry = rx * (RY_FRAC / RX_FRAC)
        tilt = CORE_SHELL_TILT_BASE + CORE_SHELL_TILT_STEP * i
        ang = (360.0 * direction * tt / max(0.1, period)
               + 360.0 * hash01(i, 63.1))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
        wx, wy = micro_offset(tt, i, r, 73.0)
        cz, cz2 = _volume_axes(tt, i, 73.0)
        hue = _hue_for(i, 79.7)
        out.append({
            "index": float(i), "x": x + dx + wx, "y": y + dy + wy,
            "depth": depth,
            "z": cz, "z2": cz2,
            "swirl": swirl,
            "size": ((CORE_SHELL_SIZE_FRAC
                      + CORE_SHELL_SIZE_SPAN * hash01(i, 65.7)) * r
                     * (DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth)),
            "alpha": CORE_SHELL_ALPHA_BASE + CORE_SHELL_ALPHA_GAIN * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "tilt": tilt,
            "spin_deg": (CORE_SHELL_SPIN_BASE + CORE_SHELL_SPIN_STEP * i
                         + 360.0 * tt / max(0.1, period)) % 360.0,
            "dof": dof(depth), "skew": skew(depth),
            "lift": CORE_SHELL_LIFT_BASE
                    + CORE_SHELL_LIFT_GAIN * hash01(i, 67.1),
            "r_frac": rfrac,
        })
    return out


# ------------------------------------------------------------- energy filament
#: A travelling standing wave running around a tilted ring: a bright band that
#: sweeps the circle, so the mark gains a continuous "energy filament" layer
#: that is neither a body nor a static circle. Positions are fixed on the ring;
#: only the wave's phase moves, so the layer is fluid and cheap.
FILAMENT_SAMPLES = 36
FILAMENT_RX_FRAC = 0.78
FILAMENT_PERIOD_S = 8.3
FILAMENT_WAVE_COUNT = 3
FILAMENT_WAVE_SPEED = 0.55
#: A second, counter-travelling wave on a NON-HARMONIC crest count and speed.
#: One bare sine travelling around the ring reads as a rotating highlight; two
#: waves crossing make the filament's crests merge, split and slide — a live
#: energy band. Weights total 1.0.
FILAMENT_WAVE_COUNT_2 = 5
FILAMENT_WAVE_SPEED_2 = -0.31
FILAMENT_WAVE_WEIGHTS = (0.72, 0.28)
FILAMENT_TILT_BASE = 12.0
FILAMENT_TILT_SWAY_DEG = 8.0
FILAMENT_ALPHA_BASE = 0.06
FILAMENT_ALPHA_PEAK = 0.42
FILAMENT_SIZE_BASE = 0.09
FILAMENT_SIZE_PEAK = 0.16
FILAMENT_LIFT = 0.62


def filament_state(t: Any, R: float) -> List[Dict[str, float]]:
    """A travelling standing wave around a tilted ring.

    ``FILAMENT_SAMPLES`` points on a ring; each carries ``{"index", "x", "y",
    "depth", "z", "z2", "size", "alpha", "hue", "hue2", "lift"}``. The point
    positions are fixed on the circle; the ALPHA (and size) ride a travelling
    sine wave, ``FILAMENT_WAVE_COUNT`` crests running around the ring at
    ``FILAMENT_WAVE_SPEED``, so a renderer strokes a pulsing energy filament.
    Deterministic and junk-safe.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    n = FILAMENT_SAMPLES
    tilt = FILAMENT_TILT_BASE + FILAMENT_TILT_SWAY_DEG * math.sin(
        2.0 * math.pi * tt / max(0.1, FILAMENT_PERIOD_S))
    rx = r * FILAMENT_RX_FRAC
    ry = rx * (RY_FRAC / RX_FRAC)
    out: List[Dict[str, float]] = []
    for i in range(n):
        ang = 360.0 * i / float(n)
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
        w0, w1 = FILAMENT_WAVE_WEIGHTS
        wave_a = 0.5 + 0.5 * math.sin(
            2.0 * math.pi * (FILAMENT_WAVE_COUNT * i / float(n)
                             - FILAMENT_WAVE_SPEED * tt
                             / max(0.1, FILAMENT_PERIOD_S)))
        wave_b = 0.5 + 0.5 * math.sin(
            2.0 * math.pi * (FILAMENT_WAVE_COUNT_2 * i / float(n)
                             - FILAMENT_WAVE_SPEED_2 * tt
                             / max(0.1, FILAMENT_PERIOD_S)) + 1.7)
        wave = w0 * wave_a + w1 * wave_b
        fz, fz2 = _volume_axes(tt, i, 251.3)
        hue = (_hue_for(i, 109.3) + tt * RGB_TRAIL_HUE_DRIFT) % 1.0
        out.append({
            "index": float(i), "x": x + dx, "y": y + dy, "depth": depth,
            "z": fz, "z2": fz2,
            "crest": wave,
            "size": (FILAMENT_SIZE_BASE + FILAMENT_SIZE_PEAK * wave)
                    * (r / 32.0) * (DEPTH_RADIUS_BASE + DEPTH_RADIUS_GAIN * depth),
            "alpha": (FILAMENT_ALPHA_BASE + FILAMENT_ALPHA_PEAK * wave)
                     * (0.35 + 0.65 * depth),
            "hue": hue, "hue2": _hue_pair(hue),
            "lift": FILAMENT_LIFT,
            "dof": dof(depth), "skew": skew(depth),
        })
    return out


# ------------------------------------------------------------------- haze
#: Owner 2026-10-19: "make more fluid, more professional, more detailed ...
#: more complex". Behind everything else sits a slow parallax HAZE: a handful
#: of large, very faint bodies on their own inclined orbits, each breathing on
#: its own non-harmonic period. It is the cheapest way to make the volume feel
#: deep and alive without another dense particle population — the renderers can
#: draw it as soft translucent discs, and the whole layer stays at the declared
#: low alpha so it never competes with the mark. ADDITIVE layer: a renderer that
#: does not consume it simply ignores it.
HAZE_COUNT = 14
HAZE_R_FRAC_MIN = 0.30
HAZE_R_FRAC_SPAN = 0.78
HAZE_PERIOD_BASE = 23.0
HAZE_PERIOD_SPAN = 19.0
HAZE_INC_MIN_DEG = 12.0
HAZE_INC_SPAN_DEG = 152.0
HAZE_SIZE_BASE = 0.16
HAZE_SIZE_SPAN = 0.26
HAZE_ALPHA_BASE = 0.020
HAZE_ALPHA_GAIN = 0.045
HAZE_BREATH_PERIOD_BASE = 12.7
HAZE_BREATH_PERIOD_SPAN = 15.1
HAZE_LIFT = 0.18


def haze_state(t: Any, R: float) -> List[Dict[str, Any]]:
    """Slow, large, very faint parallax bodies behind the whole mark.

    One dict per body: ``{"index", "x", "y", "depth", "z", "z2", "size",
    "alpha", "lift", "hue", "hue2", "dof", "skew", "r", "inc", "phase",
    "breath"}``. Each body rides its own inclined orbit at its own slow,
    non-harmonic period, its radius drifts inward-only (so the layer never
    exceeds its declared band) and its alpha breathes on a second,
    independent period. Deterministic in ``(t, R)``, C1 in ``t``, junk-safe;
    it is a moving layer, so a renderer drops it under reduced motion exactly
    like the other life layers.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    for i in range(HAZE_COUNT):
        idx = i + 1
        rad = r * (HAZE_R_FRAC_MIN
                   + HAZE_R_FRAC_SPAN * hash01(idx, 941.3))
        inc = math.radians(HAZE_INC_MIN_DEG
                           + HAZE_INC_SPAN_DEG * hash01(idx, 943.7))
        period = (HAZE_PERIOD_BASE
                  + HAZE_PERIOD_SPAN * hash01(idx, 947.1))
        direction = 1.0 if hash01(idx, 949.3) >= 0.5 else -1.0
        phase0 = 360.0 * hash01(idx, 953.9)
        ang = (phase0 + direction * 360.0 * tt / max(0.1, period)
               + _phase_wobble(tt, phase0))
        drift = _drift01(tt, idx, 957.1)
        rad = rad * (1.0 - 0.05 * drift)
        ar = math.radians(ang)
        u, v = math.cos(ar), math.sin(ar)
        x = rad * u
        y = rad * v * math.cos(inc)
        depth = min(1.0, max(0.0, 0.5 + 0.5 * v * math.sin(inc)))
        bperiod = (HAZE_BREATH_PERIOD_BASE
                   + HAZE_BREATH_PERIOD_SPAN * hash01(idx, 959.7))
        breath = 0.5 + 0.5 * math.sin(
            2.0 * math.pi * tt / max(0.1, bperiod)
            + hash01(idx, 961.3) * 2.0 * math.pi)
        wx, wy = micro_offset(tt, idx, r, 963.7)
        cz, cz2 = _volume_axes(tt, idx, 967.1)
        hue = _hue_for(idx, 971.3)
        out.append({
            "index": float(idx),
            "x": x + dx + wx, "y": y + dy + wy,
            "depth": depth,
            "z": cz, "z2": cz2,
            "size": _depth_size(
                depth, (HAZE_SIZE_BASE
                        + HAZE_SIZE_SPAN * hash01(idx, 973.9))
                * scale * (0.85 + 0.30 * breath)),
            "alpha": min(1.0, (HAZE_ALPHA_BASE + HAZE_ALPHA_GAIN * depth)
                         * (0.55 + 0.45 * breath)),
            "lift": HAZE_LIFT,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "r": rad, "inc": inc, "phase": ang, "breath": breath,
        })
    return out


# ------------------------------------------------------------- ring geometry
#: Owner: "refine circles more". Every circle in the mark — the two orbits,
#: their echo followers, the inner core ring and the satellite shells — is
#: declared ONCE here, as a stroke description in the tilted plane, so the Qt
#: and Tk renderers cannot drift into two slightly different sets of circles.
#: Owner 2026-10-19: the circles "need to be more transparent ... they look
#: like galaxies". Every declared alpha is cut hard and the main orbits keep
#: only a faint identity, so the ring set reads as quiet glass paths rather
#: than a bright multi-coloured accretion disc.
RING_ALPHA_MAIN = 0.18
RING_ALPHA_ECHO = 0.12
RING_ALPHA_INNER = 0.12
RING_ALPHA_SAT = 0.06
#: Owner 2026-10-19: "add 2 more circles again ... 60% more opacity than from
#: the others so they be a little more visible". A second orbit PAIR at a
#: smaller radius, drawn at 1.6x the main orbits' declared alpha.
RING_ALPHA_ACCENT = RING_ALPHA_MAIN * 1.6
RING_ACCENT_RX_FRAC = 0.58
RING_ACCENT_RY_FRAC = RING_ACCENT_RX_FRAC * (RY_FRAC / RX_FRAC)
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
#: Ring-set PRECESSION and radius BREATH.
#:
#: Owner 2026-10-18: "shouldn't they all be on same axis, sometime one becomes
#: smaller or bigger than the other". The rings previously tipped and breathed
#: on their OWN periods, so at any instant one circle sat at +1.7% and another
#: at -1.7% — a visible size/axis mismatch between rings that are meant to be
#: one concentric set. The whole set now moves as ONE rigid body: a single
#: shared phase drives both the plane sway and the radius breath, so every
#: ring's radius and plane stay EXACTLY proportional to its declaration at all
#: times, while the set still breathes and sways fluidly. Exposed additively:
#: ``tilt`` stays the base tilt the contracts assert; ``tilt_spin_deg`` is the
#: offset a renderer ADDS to it, and :func:`ring_radius_scale` multiplies the
#: radii.
RING_MOTION_PERIOD_S = 23.3
RING_MOTION_PHASE = 0.0
#: Owner 2026-10-19: "refine circles animation more". The shared rigid motion
#: is no longer one bare sine (which read as a mechanical sway): it is a SUM of
#: three sines on non-harmonic periods, so the plane precesses and the set
#: breathes fluidly, like a body settling rather than a metronome. The weights
#: sum to 1.0, so the total amplitude stays EXACTLY :data:`RING_PRECESS_AMP_DEG`
#: for the whole rigid set — every ring still shares one offset.
RING_MOTION_PERIOD_2_S = 8.5
RING_MOTION_PERIOD_3_S = 31.7
RING_PRECESS_AMP_DEG = 7.0
RING_PRECESS_WEIGHTS = (0.62, 0.24, 0.14)
#: Radius breath: a fraction of the ring radius, never enough to disturb the
#: composition (always > 0.95). Driven by the SAME rigid period family, with a
#: different phase so the tilt and the radius do not move together. Weights sum
#: to 1.0, so the peak breath is exactly half :data:`RING_RADIUS_BREATH_SPAN`.
RING_RADIUS_BREATH_SPAN = 0.040
RING_RADIUS_BREATH_PHASE = 1.9
RING_RADIUS_WEIGHTS = (0.68, 0.32)


def _rigid_phase_vector(t: Any) -> Tuple[float, float, float]:
    """The three shared phases the rigid ring motion is built from."""
    tt = _finite(t)
    base = 2.0 * math.pi * tt
    return (base / RING_MOTION_PERIOD_S + RING_MOTION_PHASE,
            base / RING_MOTION_PERIOD_2_S + RING_MOTION_PHASE + 1.3,
            base / RING_MOTION_PERIOD_3_S + RING_MOTION_PHASE + 2.6)


def ring_precess_period(ring: Any = None) -> float:
    """The shared ring-set motion period (seconds).

    Kept as a function (and kept in each ``ring_geometry`` entry as
    ``precess_period_s``) for API compatibility, but the rigid set now moves on
    a family of non-harmonic periods (see :data:`RING_MOTION_PERIODS_S`); this
    returns the primary one. All rings still share the same motion, so no
    circle ever drifts out of proportion with the rest.
    """
    return RING_MOTION_PERIOD_S


def ring_tilt_spin(t: Any, ring: Any = None) -> float:
    """The shared plane-sway offset in DEGREES at clock ``t``.

    ADDITIVE: a renderer adds this to the entry's ``tilt``. Identical for every
    ring, so the set sways as one rigid body and each ring keeps its declared
    tilt RELATIVE to the others. A sum of three weighted sines on non-harmonic
    periods — smooth and bounded, so the plane evolves fluidly instead of
    snapping, and richer than a single sway. ``|result| <= RING_PRECESS_AMP_DEG``
    always (the weights sum to 1.0).
    """
    p1, p2, p3 = _rigid_phase_vector(t)
    w0, w1, w2 = RING_PRECESS_WEIGHTS
    return RING_PRECESS_AMP_DEG * (
        w0 * math.sin(p1) + w1 * math.sin(p2) + w2 * math.sin(p3))


def ring_radius_scale(t: Any, ring: Any = None) -> float:
    """The shared radius breath, ``~1.0`` and always positive.

    Identical for every ring, so all circles scale by the same factor and stay
    exactly proportional to their declared radii. A renderer multiplies the
    declared ``rx``/``ry`` by this; the declared fractions are untouched. Two
    weighted, non-harmonic sines give a fluid breath; the peak is exactly half
    :data:`RING_RADIUS_BREATH_SPAN`, so it never disturbs the composition.
    """
    p1, p2, _p3 = _rigid_phase_vector(t)
    w0, w1 = RING_RADIUS_WEIGHTS
    breath = w0 * math.sin(p1 + RING_RADIUS_BREATH_PHASE) \
        + w1 * math.sin(p2 + RING_RADIUS_BREATH_PHASE + 0.7)
    return 1.0 + RING_RADIUS_BREATH_SPAN * 0.5 * breath


#: Every rigid ring-motion period the field declares. The contract test checks
#: no two of these (nor any of them against the ring turns / spins) are
#: harmonically locked, so the set never pulses in lock-step with a spin.
RING_MOTION_PERIODS_S = (
    RING_MOTION_PERIOD_S, RING_MOTION_PERIOD_2_S, RING_MOTION_PERIOD_3_S)


def ring_geometry(t: Any) -> List[Dict[str, Any]]:
    """Every circle of the mark as a stroke description, back to front.

    Each entry: ``{"id", "layer", "orbit", "tilt", "rx_frac", "ry_frac",
    "alpha", "width", "echo", "seed", "tilt_spin_deg", "precess_period_s",
    "intensity_period_s", "shimmer_period_s", "intensity", "shimmer",
    "streamers"}``.
    Radii are FRACTIONS of the mark radius and the tilt is in degrees, so the
    same entry scales to any size; :func:`ring_path` turns one into points.
    ``layer`` is ``main``, ``echo``, ``inner`` or ``satellite``; the list is
    ordered so a renderer can paint it in order and get the near rings on top.

    ADDITIVE evolution keys: ``tilt`` remains the base tilt the contracts
    assert; ``tilt_spin_deg`` is a bounded offset the renderer ADDS to ``tilt``
    (:func:`ring_tilt_spin`) and is IDENTICAL for every ring — the rigid set — so
    ``precess_period_s`` (that ring's recorded period, kept for compatibility)
    no longer varies per ring. ``intensity`` (a 0..1 accretion hot-spot
    modulation, :func:`ring_intensity`), ``shimmer`` (a sub-percent per-ring
    radius glitter, :func:`ring_shimmer`) and ``streamers`` (a suggested
    streamer count) give each circle its own live detail WITHOUT disturbing the
    rigid proportionality; renderers multiply the ring's alpha by
    ``ring_alpha(t, ring) * ring_intensity(t, ring)`` and its radii by
    ``ring_radius_scale(t, ring) * ring_shimmer(t, ring)``.
    """
    tt = _finite(t)
    near_tilt, far_tilt = orbit_tilts(tt)
    lag_near, lag_far = (e["tilt"] for e in echo_rings(tt - ECHO_LAG_S))
    return _ring_specs(tt, near_tilt, far_tilt, lag_near, lag_far)


def _ring_specs(tt: float, near_tilt: float, far_tilt: float,
                lag_near: float, lag_far: float) -> List[Dict[str, Any]]:
    """Build the declared circle list from already-resolved ring tilts.

    Split out of :func:`ring_geometry` so the field's OWN consumers (dust)
    can declare the same circles without going through the patchable
    ``echo_rings`` reader: dust rides the geometry, not a stubbed layer.
    """
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
    # Owner 2026-10-19: a second orbit PAIR (far + near) at a smaller radius,
    # at 1.6x the main orbits' opacity so it reads a little more strongly.
    out.append({
        "id": "accent_b", "layer": "accent", "orbit": "b", "tilt": far_tilt,
        "rx_frac": RING_ACCENT_RX_FRAC, "ry_frac": RING_ACCENT_RY_FRAC,
        "alpha": RING_ALPHA_ACCENT, "width": RING_WIDTH_MAIN, "echo": False,
        "seed": 8.0})
    out.append({
        "id": "accent_a", "layer": "accent", "orbit": "a", "tilt": near_tilt,
        "rx_frac": RING_ACCENT_RX_FRAC, "ry_frac": RING_ACCENT_RY_FRAC,
        "alpha": RING_ALPHA_ACCENT, "width": RING_WIDTH_MAIN, "echo": False,
        "seed": 9.0})
    # Additive details. The PLANE offset is shared (rigid set), while each ring
    # gets its own deterministic intensity/shimmer/streamer detail so a stroked
    # circle reads as a live accretion structure instead of a dead outline.
    for entry in out:
        entry["precess_period_s"] = ring_precess_period(entry)
        entry["tilt_spin_deg"] = ring_tilt_spin(tt, entry)
        entry["intensity_period_s"] = _ring_detail_period(
            entry, "intensity_period_s", RING_INTENSITY_PERIOD_BASE,
            RING_INTENSITY_PERIOD_SPAN, 601.1)
        entry["shimmer_period_s"] = _ring_detail_period(
            entry, "shimmer_period_s", RING_SHIMMER_PERIOD_BASE,
            RING_SHIMMER_PERIOD_SPAN, 603.7)
        entry["intensity"] = ring_intensity(tt, entry)
        entry["shimmer"] = ring_shimmer(tt, entry)
        seed = int(_finite(entry.get("seed")))
        entry["streamers"] = RING_STREAMERS_BASE + int(
            hash01(seed + 1, 607.7) * (RING_STREAMERS_SPAN + 1))
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


# ---------------------------------------------------------- ring detail
#: Owner 2026-10-19: "the spirals or circles make them look like blackholes add
#: more details and animations". A plain stroked ellipse reads as a black-hole
#: ring; these per-ring detail keys give the renderers something to modulate the
#: stroke with, so each circle looks like a live structure:
#:
#:   * ``intensity`` — a 0..1 brightness modulation (accretion hot spots) on its
#:     own per-ring non-harmonic period. Renderers multiply the ring's alpha by
#:     ``ring_alpha(t, ring) * ring_intensity(t, ring)``.
#:   * ``shimmer`` — a small multiplicative RADIUS detail (~1.0) that makes the
#:     stroke breathe at the sub-percent level. It is deliberately much smaller
#:     than the shared :func:`ring_radius_scale`, so the rigid set stays rigid
#:     while each ring still glitters.
#:   * ``streamers`` — how many accretion streamer samples that ring suggests
#:     (a hint for :func:`accretion_stream`-style detail).
#:
#: All three are deterministic in the ring's ``seed`` and pure in ``t``.
RING_INTENSITY_MIN = 0.55
RING_INTENSITY_SPAN = 0.45
RING_INTENSITY_PERIOD_BASE = 6.4
RING_INTENSITY_PERIOD_SPAN = 9.1
RING_SHIMMER_SPAN = 0.030
RING_SHIMMER_PERIOD_BASE = 5.3
RING_SHIMMER_PERIOD_SPAN = 8.1
RING_STREAMERS_BASE = 2
RING_STREAMERS_SPAN = 3


def _ring_detail_period(ring: Dict[str, Any], key: str, base: float,
                        span: float, salt: float) -> float:
    """Resolve (or derive from the ring seed) one per-ring detail period."""
    ring = ring if isinstance(ring, dict) else {}
    have = _finite(ring.get(key), -1.0)
    if have > 0.0:
        return have
    seed = int(_finite(ring.get("seed")))
    return base + span * hash01(seed + 1, salt)


def ring_intensity(t: Any, ring: Any = None) -> float:
    """A ring's 0.55..1.0 brightness modulation at clock ``t`` (never 0).

    Deterministic in the ring's ``seed`` and its ``intensity_period_s``; this
    is the field-level "accretion hot spot" that stops a stroked circle from
    reading as a dead outline. Junk-safe like every field helper.
    """
    ring = ring if isinstance(ring, dict) else {}
    period = _ring_detail_period(
        ring, "intensity_period_s", RING_INTENSITY_PERIOD_BASE,
        RING_INTENSITY_PERIOD_SPAN, 601.1)
    tt = _finite(t)
    seed = _finite(ring.get("seed"))
    phase = 2.0 * math.pi * tt / max(0.1, period) + seed * 0.83
    # Two non-harmonic harmonics per ring: the hot spot slides and its width
    # breathes, so a ring's brightness is a live accretion signature rather
    # than one clean sine. The pair is normalised to stay inside the declared
    # [MIN, MIN + SPAN] band.
    phase2 = 2.0 * math.pi * tt / max(0.1, period * 0.43) + seed * 1.91 + 1.3
    mix = 0.70 * math.sin(phase) + 0.30 * math.sin(phase2)
    return RING_INTENSITY_MIN + RING_INTENSITY_SPAN * (0.5 + 0.5 * mix)


def ring_shimmer(t: Any, ring: Any = None) -> float:
    """A small per-ring radius shimmer (~1.0) at clock ``t``.

    ADDITIVE, per-ring texture only: the shared rigid
    :func:`ring_radius_scale` keeps every ring proportional to the others, and
    this sub-percent wobble sits on top so a ring's edge glitters. Never
    negative, junk-safe.
    """
    ring = ring if isinstance(ring, dict) else {}
    period = _ring_detail_period(
        ring, "shimmer_period_s", RING_SHIMMER_PERIOD_BASE,
        RING_SHIMMER_PERIOD_SPAN, 603.7)
    tt = _finite(t)
    seed = _finite(ring.get("seed"))
    phase = 2.0 * math.pi * tt / max(0.1, period) + seed * 1.37
    # A second, faster radius texture on a 0.61x (non-harmonic) period. Weighted
    # so the total stays inside the declared sub-percent shimmer span.
    phase2 = 2.0 * math.pi * tt / max(0.1, period * 0.61) + seed * 2.29 + 0.7
    mix = 0.76 * math.sin(phase) + 0.24 * math.sin(phase2)
    return 1.0 + RING_SHIMMER_SPAN * 0.5 * mix


# -------------------------------------------------------------- ring particles
#: Bodies riding EVERY circle :func:`ring_geometry` declares, so the mark reads
#: as populated everywhere instead of on the two main orbits alone. Each ring
#: contributes at least ``RING_PARTICLE_DEFAULT`` bodies (the two main orbits
#: more), each at its own non-harmonic period and direction, and each riding the
#: ring's REAL animated path — the live tilt (``tilt`` + ``tilt_spin_deg``) and
#: a radius scaled by :func:`ring_radius_scale` — rather than a stale circle.
RING_PARTICLE_COUNTS = {
    "orbit_a": 4, "orbit_b": 4,
    "echo_a": 3, "echo_b": 3,
    "shell_0": 2, "shell_1": 2, "shell_2": 2,
    "inner": 2,
}
RING_PARTICLE_DEFAULT = 2
RING_PARTICLE_PERIOD_BASE = 6.5
RING_PARTICLE_PERIOD_SPAN = 9.0
RING_PARTICLE_SIZE = 0.14
RING_PARTICLE_ALPHA = 0.34
RING_PARTICLE_LIFT_BASE = 0.30
RING_PARTICLE_LIFT_GAIN = 0.35


def ring_particles(t: Any, R: float) -> List[Dict[str, Any]]:
    """Deterministic bodies spread across EVERY :func:`ring_geometry` ring.

    One dict per body: ``{"index", "x", "y", "depth", "z", "z2", "size",
    "alpha", "lift", "hue", "hue2", "dof", "skew", "orbit", "ring_id"}``. Each
    body travels its own ring at its own non-harmonic period and direction,
    riding the ring's REAL animated path — its live tilt and a radius scaled by
    :func:`ring_radius_scale` — so the bodies follow the circles the renderers
    actually stroke. Pure in ``(t, R)``: no ``random`` anywhere.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    gi = 0
    for ring in ring_geometry(tt):
        rid = str(ring.get("id") or "")
        count = int(RING_PARTICLE_COUNTS.get(rid, RING_PARTICLE_DEFAULT))
        tilt = _finite(ring.get("tilt")) + _finite(ring.get("tilt_spin_deg"))
        rad = ring_radius_scale(tt, ring)
        rx = r * _finite(ring.get("rx_frac"), RX_FRAC) * rad
        ry = r * _finite(ring.get("ry_frac"), RY_FRAC) * rad
        for _k in range(max(1, count)):
            gi += 1
            seed = 360.0 * hash01(gi, 211.1)
            direction = 1.0 if hash01(gi, 213.7) >= 0.5 else -1.0
            period = (RING_PARTICLE_PERIOD_BASE
                      + RING_PARTICLE_PERIOD_SPAN * hash01(gi, 217.3))
            ang = (seed + direction * 360.0 * tt / max(0.1, period)
                   + _phase_wobble(tt, seed))
            depth = _depth(ang)
            pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
            x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
            pz, pz2 = _volume_axes(tt, gi, 223.9)
            hue = _hue_for(gi, 227.1)
            out.append({
                "index": float(gi), "x": x + dx, "y": y + dy, "depth": depth,
                "z": pz, "z2": pz2,
                "size": _depth_size(depth, RING_PARTICLE_SIZE * scale),
                "alpha": _depth_alpha(depth, RING_PARTICLE_ALPHA),
                "lift": (RING_PARTICLE_LIFT_BASE
                         + RING_PARTICLE_LIFT_GAIN * depth),
                "hue": hue, "hue2": _hue_pair(hue),
                "dof": dof(depth), "skew": skew(depth),
                "orbit": str(ring.get("orbit") or ""), "ring_id": rid,
            })
    return out


# ---------------------------------------------------------- accretion stream
#: Owner 2026-10-19: the plain circles read as "blackholes". An accretion
#: stream gives them structure: bodies that wind around the nucleus on a
#: continuous spiral, moving INWARD and OUTWARD over their own non-harmonic
#: period (never a pop), so a renderer can stroke a live, infalling stream
#: along the rings rather than a dead outline. ``s`` is 0 at the outer edge and
#: 1 at the inner edge; ``vx``/``vy`` let a renderer streak the sample.
ACCRETION_COUNT = 28
ACCRETION_R_OUT_FRAC = 0.86
ACCRETION_R_IN_FRAC = 0.26
ACCRETION_PERIOD_BASE = 6.2
ACCRETION_PERIOD_SPAN = 7.4
ACCRETION_SPIRAL_TURNS = 1.35
ACCRETION_SIZE_BASE = 0.12
ACCRETION_SIZE_SPAN = 0.10
ACCRETION_ALPHA_BASE = 0.12
ACCRETION_ALPHA_GAIN = 0.20
ACCRETION_LIFT = 0.34


def accretion_stream(t: Any, R: float) -> List[Dict[str, Any]]:
    """Spiral accretion-stream samples winding around the nucleus.

    One dict per sample:
    ``{"index", "x", "y", "depth", "z", "z2", "vx", "vy", "size", "alpha",
    "lift", "hue", "hue2", "dof", "skew", "s", "r", "ring_id"}``.

    ``s`` runs 0 (outer edge) .. 1 (inner edge) as the sample winds in, and
    ``r`` is its current radius in pixels. The spiral angle advances with time
    AND with ``s``, so the stream reads as matter winding toward the core; the
    radius oscillates smoothly between the two edges, so nothing teleports.
    Deterministic and junk-safe, pure in ``(t, R)``.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    near_tilt, far_tilt = orbit_tilts(tt)
    scale = r / 32.0
    rings = ring_geometry(tt)
    out: List[Dict[str, Any]] = []
    for i in range(ACCRETION_COUNT):
        idx = i + 1
        ring = rings[i % len(rings)] if rings else {}
        # Half the stream rides the near plane, half the far one, so the
        # spiral wraps the whole volume rather than one face.
        tilt = near_tilt if (i % 2 == 0) else far_tilt
        period = (ACCRETION_PERIOD_BASE
                  + ACCRETION_PERIOD_SPAN * hash01(idx, 801.3))
        direction = 1.0 if hash01(idx, 803.7) >= 0.5 else -1.0
        phase0 = 360.0 * hash01(idx, 809.1)
        phase = (2.0 * math.pi * tt / max(0.1, period)
                 + phase0 * math.pi / 180.0)
        inward = 0.5 - 0.5 * math.cos(phase)
        r_out = r * ACCRETION_R_OUT_FRAC * (
            0.92 + 0.16 * hash01(idx, 811.3))
        r_in = r * ACCRETION_R_IN_FRAC * (
            0.85 + 0.30 * hash01(idx, 813.9))
        # A second radial ripple on a non-harmonic harmonic of the winding
        # period: the stream's inflow is not one clean cosine, so the spiral
        # gains travelling density waves (the clumps a real accretion flow
        # shows) without ever leaving the declared inner/outer band.
        ripple = 0.5 + 0.5 * math.sin(
            phase * 2.37 + hash01(idx, 815.3) * 2.0 * math.pi)
        rad = r_out + (r_in - r_out) * inward
        rad = min(r_out, max(r_in, rad + (r_out - r_in)
                             * 0.035 * (0.5 - ripple)))
        ang = (phase0 + direction * 360.0 * tt / max(0.1, period)
               + ACCRETION_SPIRAL_TURNS * 360.0 * inward * direction
               + _phase_wobble(tt, phase0))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        rr = rad * pull
        x, y = orbit_point(0.0, 0.0, rr, rr * (RY_FRAC / RX_FRAC), tilt, ang)
        vx, vy = _orbit_velocity(rr, rr * (RY_FRAC / RX_FRAC), tilt, ang)
        wx, wy = micro_offset(tt, idx, r, 817.1)
        cz, cz2 = _volume_axes(tt, idx, 819.7)
        hue = _hue_for(idx, 821.1)
        # Travelling clumps: a slow per-sample brightness wave so the stream
        # glints in knots rather than as a uniform dotted spiral.
        clump = 0.70 + 0.30 * (0.5 + 0.5 * math.sin(
            2.0 * math.pi * tt / 5.9 + hash01(idx, 825.7) * 2.0 * math.pi))
        out.append({
            "index": float(idx),
            "x": x + dx + wx, "y": y + dy + wy, "depth": depth,
            "z": cz, "z2": cz2,
            "vx": vx * 360.0 / max(0.1, period),
            "vy": vy * 360.0 / max(0.1, period),
            "size": _depth_size(
                depth, (ACCRETION_SIZE_BASE
                        + ACCRETION_SIZE_SPAN * hash01(idx, 823.3)) * scale
                * (0.92 + 0.16 * clump)),
            "alpha": min(1.0, _depth_alpha(depth, ACCRETION_ALPHA_BASE
                                           + ACCRETION_ALPHA_GAIN
                                           * (0.35 + 0.65 * inward))
                         * clump),
            "lift": ACCRETION_LIFT + 0.30 * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "s": inward, "r": rad, "clump": clump,
            "ring_id": str(ring.get("id") or ""),
        })
    return out


# ------------------------------------------------------------- photon ring
#: Owner: more "blackhole" detail. A photon ring is the thin, bright, LENSED
#: ring hugging the core: not a uniform circle but a couple of bright arcs (the
#: lensed image of the far side) fading around the rest. This layer emits the
#: samples; the field owns the lobe profile so both renderers agree.
PHOTON_SAMPLES = 44
PHOTON_RX_FRAC = 0.15
PHOTON_RY_FRAC = 0.095
PHOTON_TILT_BASE = 10.0
PHOTON_TILT_SWAY_DEG = 6.0
PHOTON_PERIOD_S = 7.3
PHOTON_LOBES = 2
#: Slow precession of the lensed arcs (their bright lobes slide around the
#: ring) plus a fast per-sample sparkle, both non-harmonic with the ring spin.
PHOTON_PRECESS_PERIOD_S = 11.9
PHOTON_SPARKLE_PERIOD_S = 2.7
PHOTON_ALPHA_BASE = 0.08
PHOTON_ALPHA_PEAK = 0.52
PHOTON_SIZE_BASE = 0.055
PHOTON_SIZE_PEAK = 0.075
PHOTON_LIFT = 0.42


def photon_ring(t: Any, R: float) -> List[Dict[str, Any]]:
    """The bright, gravitationally-lensed ring hugging the nucleus.

    One dict per sample:
    ``{"index", "x", "y", "depth", "z", "z2", "size", "alpha", "lift", "hue",
    "hue2", "dof", "skew", "angle"}``.

    Samples sit on a small tilted ellipse just outside the core; their ALPHA
    and SIZE ride a ``PHOTON_LOBES``-lobe cosine profile (two bright lensed
    arcs, dim between them) that rotates slowly with the clock, so the ring
    reads as lensed light rather than a painted circle. Deterministic, pure in
    ``(t, R)``, junk-safe and reduced-motion-safe (renderer's call).
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    tilt = PHOTON_TILT_BASE + PHOTON_TILT_SWAY_DEG * math.sin(
        2.0 * math.pi * tt / max(0.1, PHOTON_PERIOD_S))
    rx = r * PHOTON_RX_FRAC
    ry = r * PHOTON_RY_FRAC
    spin = 2.0 * math.pi * tt / max(0.1, PHOTON_PERIOD_S)
    out: List[Dict[str, Any]] = []
    for i in range(PHOTON_SAMPLES):
        ang = 360.0 * i / float(PHOTON_SAMPLES)
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        x, y = orbit_point(0.0, 0.0, rx * pull, ry * pull, tilt, ang)
        ar = math.radians(ang)
        # The two bright lensed arcs slide around the ring on a slow
        # non-harmonic precession, and a small counter-travelling ripple bends
        # their outline — the lensed image of a moving far side, not a mask.
        precess = (0.24 * math.sin(2.0 * math.pi * tt
                                   / max(0.1, PHOTON_PRECESS_PERIOD_S))
                   + 0.10 * math.sin(2.0 * math.pi * tt / 17.3 + ar * 1.7))
        # ``lobe`` drives ALPHA and is kept exactly as published: the Qt
        # painter culls photon samples by alpha against its own threshold, so
        # the primitive count must not drift. The lensed precession instead
        # moves ``lobe_glow``, which drives SIZE only — richer light without a
        # primitive spent.
        lobe = 0.5 + 0.5 * math.cos(PHOTON_LOBES * ar - spin)
        lobe_glow = 0.5 + 0.5 * math.cos(PHOTON_LOBES * ar - spin + precess)
        sparkle = 0.82 + 0.18 * (0.5 + 0.5 * math.sin(
            2.0 * math.pi * tt / max(0.1, PHOTON_SPARKLE_PERIOD_S)
            + i * 0.61))
        cz, cz2 = _volume_axes(tt, i, 831.7)
        hue = _hue_for(i, 833.1)
        out.append({
            "index": float(i),
            "x": x + dx, "y": y + dy, "depth": depth,
            "z": cz, "z2": cz2,
            "size": (PHOTON_SIZE_BASE + PHOTON_SIZE_PEAK * lobe_glow)
                    * scale * (0.7 + 0.5 * depth) * (0.92 + 0.16 * sparkle),
            "alpha": (PHOTON_ALPHA_BASE + PHOTON_ALPHA_PEAK * lobe)
                     * (0.45 + 0.55 * depth),
            "lift": PHOTON_LIFT + 0.35 * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "angle": ang, "lobe": lobe, "sparkle": sparkle,
        })
    return out


# ------------------------------------------------------------ infall streaks
#: Fast radial streaks falling toward the core: the third piece of the
#: "blackhole" structure the owner asked for. Each streak carries an inward
#: velocity (``vx``/``vy``) and a ``tail_len`` the renderer can draw as a
#: tapering streak (or feed to :func:`rgb_trail`, which uses ``vx``/``vy``),
#: so the field reads as matter falling IN rather than orbiting forever.
INFALL_COUNT = 14
INFALL_R_OUT_FRAC = 1.00
INFALL_R_IN_FRAC = 0.20
INFALL_PERIOD_BASE = 3.6
INFALL_PERIOD_SPAN = 4.8
INFALL_SIZE_BASE = 0.13
INFALL_SIZE_SPAN = 0.08
INFALL_ALPHA_BASE = 0.14
INFALL_ALPHA_GAIN = 0.22
INFALL_LIFT = 0.36
INFALL_TAIL_LEN_FRAC = 0.30


def infall_streaks(t: Any, R: float) -> List[Dict[str, Any]]:
    """Streaks of matter falling toward the nucleus.

    One dict per streak:
    ``{"index", "x", "y", "depth", "z", "z2", "vx", "vy", "size", "alpha",
    "lift", "hue", "hue2", "dof", "skew", "r", "tail_len", "s"}``.

    The radius oscillates smoothly between the outer and inner edge (``s`` is
    0 outside .. 1 inside) while the streak slowly winds, so its motion is
    continuous and its velocity points INWARD (toward the core) on the way in
    and outward on the way out. ``tail_len`` is the recommended streak length
    in pixels. Deterministic, pure in ``(t, R)``, junk-safe.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    near_tilt, far_tilt = orbit_tilts(tt)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    for i in range(INFALL_COUNT):
        idx = i + 1
        tilt = near_tilt if (i % 2 == 0) else far_tilt
        period = (INFALL_PERIOD_BASE
                  + INFALL_PERIOD_SPAN * hash01(idx, 841.3))
        direction = 1.0 if hash01(idx, 843.7) >= 0.5 else -1.0
        phase0 = 360.0 * hash01(idx, 849.1)
        phase = (2.0 * math.pi * tt / max(0.1, period)
                 + phase0 * math.pi / 180.0)
        inward = 0.5 - 0.5 * math.cos(phase)
        r_out = r * INFALL_R_OUT_FRAC * (0.90 + 0.16 * hash01(idx, 851.3))
        r_in = r * INFALL_R_IN_FRAC * (0.80 + 0.35 * hash01(idx, 853.9))
        rad = r_out + (r_in - r_out) * inward
        # A small second radial ripple on the inbound leg: the stream has
        # density knots instead of one clean cosine fall.
        rad += (r_out - r_in) * 0.020 * (
            0.5 - 0.5 * math.sin(phase * 3.1 + hash01(idx, 855.1) * 6.283))
        ang = (phase0 + direction * 70.0 * tt / max(0.1, period)
               + 0.6 * direction * 360.0 * inward
               + _phase_wobble(tt, phase0))
        depth = _depth(ang)
        pull = 1.0 - DEPTH_PERSPECTIVE * (1.0 - depth)
        rr = rad * pull
        ratio = RY_FRAC / RX_FRAC
        x, y = orbit_point(0.0, 0.0, rr, rr * ratio, tilt, ang)
        # Velocity: radial (inward on the inbound leg) + tangential winding.
        dr_dt = ((r_in - r_out) * 0.5 * math.sin(phase)
                 * 2.0 * math.pi / max(0.1, period))
        tang = rr * math.radians(direction * 70.0 / max(0.1, period))
        vx = dr_dt * math.cos(math.radians(ang)) - tang * math.sin(
            math.radians(ang))
        vy = dr_dt * math.sin(math.radians(ang)) + tang * math.cos(
            math.radians(ang))
        wx, wy = micro_offset(tt, idx, r, 857.3)
        cz, cz2 = _volume_axes(tt, idx, 859.7)
        hue = _hue_for(idx, 861.1)
        # Per-streak brightness and tail flicker: falling matter gutters.
        flicker = 0.78 + 0.22 * (0.5 + 0.5 * math.sin(
            2.0 * math.pi * tt / 3.1 + hash01(idx, 865.9) * 2.0 * math.pi))
        out.append({
            "index": float(idx),
            "x": x + dx + wx, "y": y + dy + wy, "depth": depth,
            "z": cz, "z2": cz2,
            "vx": vx, "vy": vy,
            "size": _depth_size(
                depth, (INFALL_SIZE_BASE
                        + INFALL_SIZE_SPAN * hash01(idx, 863.3)) * scale
                * (0.9 + 0.2 * flicker)),
            "alpha": min(1.0, _depth_alpha(depth, INFALL_ALPHA_BASE
                                          + INFALL_ALPHA_GAIN
                                          * (0.30 + 0.70 * inward))
                         * flicker),
            "lift": INFALL_LIFT + 0.32 * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "r": rad,
            "tail_len": INFALL_TAIL_LEN_FRAC * r * (0.6 + 0.8 * inward)
                        * (0.8 + 0.4 * flicker),
            "s": inward, "flicker": flicker,
        })
    return out


# ---------------------------------------------------------- cylinder particles
#: A vertical column of particles wrapped around the nucleus: bodies on a
#: cylinder (axis = the mark's vertical Y axis) at deterministic radii and
#: heights, ORBITING the axis in a full 360 about the middle orb while the
#: column slowly drifts up and down. This is the strong volume cue the owner
#: asked for — the mark's interior is no longer only the rings. Radius is a
#: fraction of R so the column hugs the nucleus and stays around the orbit
#: envelope; height is signed and bounded to ~±0.5 R so it reads as a column.
CYLINDER_COUNT = 32
CYLINDER_R_FRAC_MIN = 0.18
CYLINDER_R_FRAC_SPAN = 0.37
CYLINDER_H_FRAC = 0.50
CYLINDER_PERIOD_S = 6.1
CYLINDER_DRIFT_PERIOD_S = 9.5
CYLINDER_DRIFT_AMP = 0.30
CYLINDER_H_BASE = 0.65
CYLINDER_SIZE_BASE = 0.16
CYLINDER_ALPHA_BASE = 0.55
CYLINDER_LIFT_BASE = 0.30
CYLINDER_LIFT_GAIN = 0.35

#: Orbiters: particles on their OWN inclined circular orbits around the
#: NUCLEUS — the owner's "particles orbit the middle, not the circles". Each
#: body revolves a full 360 in its own tilted plane at its own radius and
#: non-harmonic period, so the set forms a 3D shell around the core instead of
#: riding the flat declared rings. ``inc`` is the orbit's inclination: it mixes
#: the orbital y with the depth axis, which is what makes an orbiter read as
#: circling the middle rather than sliding along a ring.
ORBITER_COUNT = 30
ORBITER_R_FRAC_MIN = 0.14
ORBITER_R_FRAC_SPAN = 0.48
ORBITER_PERIOD_BASE = 4.6
ORBITER_PERIOD_SPAN = 6.9
ORBITER_INC_MIN_DEG = 18.0
ORBITER_INC_SPAN_DEG = 144.0
ORBITER_SIZE_BASE = 0.20
ORBITER_ALPHA_BASE = 0.62
ORBITER_LIFT_BASE = 0.30
ORBITER_LIFT_GAIN = 0.40


def orbiter_particles(t: Any, R: float) -> List[Dict[str, Any]]:
    """Particles on inclined 3D orbits around the NUCLEUS (not the rings).

    One dict per orbiter: ``{"index", "x", "y", "depth", "z", "z2", "size",
    "alpha", "lift", "hue", "hue2", "dof", "skew", "r", "inc"}``.

    Each body sweeps a full 360 in its own plane: a circle of radius ``r``
    (a fraction of R) inclined by ``inc`` degrees, so its y is the orbital y
    times ``cos(inc)`` and its out-of-plane travel rides the depth axis as
    ``sin(inc)*sin(angle)``. That depth swing is what reads as circling the
    MIDDLE — a body on the far side sits behind the nucleus and one on the
    near side in front of it, every revolution. Radii, inclinations, periods
    (non-harmonic) and directions are all deterministic from ``hash01`` of the
    index, so the shell is varied but perfectly reproducible. Pure in
    ``(t, R)``: no ``random`` anywhere.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    for i in range(ORBITER_COUNT):
        idx = i + 1
        rad = r * (ORBITER_R_FRAC_MIN
                   + ORBITER_R_FRAC_SPAN * hash01(idx, 401.3))
        inc = math.radians(ORBITER_INC_MIN_DEG
                           + ORBITER_INC_SPAN_DEG * hash01(idx, 403.7))
        period = (ORBITER_PERIOD_BASE
                  + ORBITER_PERIOD_SPAN * hash01(idx, 409.1))
        direction = 1.0 if hash01(idx, 411.7) >= 0.5 else -1.0
        phase0 = 360.0 * hash01(idx, 419.3)
        ang = (phase0 + direction * 360.0 * tt / max(0.1, period)
               + _phase_wobble(tt, phase0))
        ar = math.radians(ang)
        u = math.cos(ar)
        v = math.sin(ar)
        # Slow per-body radial drift (inward-only, so the declared `r` stays
        # the outer bound) plus its own glow: the shell breathes instead of
        # running as one rigid gyroscope.
        drift = _drift01(tt, idx, 423.1)
        r_eff = rad * (1.0 - 0.045 * drift)
        # The orbit's own plane: y is foreshortened by the inclination, the
        # out-of-plane component becomes depth (near half in front of the
        # core, far half behind it).
        x = r_eff * u
        y = r_eff * v * math.cos(inc)
        zunit = v * math.sin(inc)
        depth = min(1.0, max(0.0, 0.5 + 0.5 * zunit))
        cz, cz2 = _volume_axes(tt, idx, 421.9)
        hue = _hue_for(idx, 431.3)
        glow = _glow(tt, idx, 425.7)
        out.append({
            "index": float(idx),
            "x": x + dx, "y": y + dy,
            "depth": depth,
            "z": cz, "z2": cz2,
            "size": _depth_size(depth, ORBITER_SIZE_BASE * scale
                                * (0.96 + 0.08 * drift)),
            "alpha": min(1.0, _depth_alpha(depth, ORBITER_ALPHA_BASE)
                         * glow),
            "lift": ORBITER_LIFT_BASE + ORBITER_LIFT_GAIN * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "r": rad, "inc": inc, "drift": drift, "glow": glow,
        })
    return out


def cylinder_particles(t: Any, R: float) -> List[Dict[str, Any]]:
    """A breathing vertical cylinder of particles around the nucleus.

    One dict per particle: ``{"index", "x", "y", "depth", "z", "z2", "size",
    "alpha", "lift", "hue", "hue2", "dof", "skew", "r", "h"}``. ``r`` is the
    cylinder radius the particle sits at (a fraction of R, so the column hugs
    the nucleus and stays inside the orbit envelope); ``h`` is its signed
    height, so a renderer can shade by height. Particles orbit the mark's
    vertical axis — a full 360 over ``CYLINDER_PERIOD_S``, half of them the
    other way — while a slow vertical drift makes the column breathe. Pure in
    ``(t, R)``: no ``random`` anywhere.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    for i in range(CYLINDER_COUNT):
        idx = i + 1
        rad = r * (CYLINDER_R_FRAC_MIN
                   + CYLINDER_R_FRAC_SPAN * hash01(idx, 311.3))
        direction = 1.0 if hash01(idx, 313.9) >= 0.5 else -1.0
        phase0 = 360.0 * hash01(idx, 319.7)
        ang = (phase0 + direction * 360.0 * tt / max(0.1, CYLINDER_PERIOD_S)
               + _phase_wobble(tt, phase0))
        depth = 0.5 + 0.5 * math.sin(math.radians(ang))
        h_phase = (2.0 * math.pi * tt / max(0.1, CYLINDER_DRIFT_PERIOD_S)
                   + hash01(idx, 327.9) * 2.0 * math.pi)
        # Two non-harmonic vertical waves: the column drifts up and down like
        # a slow tide instead of one metronomic bob. Amplitude is split so the
        # total stays inside ``CYLINDER_H_FRAC``.
        h_unit = (CYLINDER_H_BASE * (2.0 * hash01(idx, 323.3) - 1.0)
                  + 0.72 * CYLINDER_DRIFT_AMP * math.sin(h_phase)
                  + 0.28 * CYLINDER_DRIFT_AMP * math.sin(
                      h_phase * 1.63 + 0.8))
        h = r * CYLINDER_H_FRAC * h_unit
        cz, cz2 = _volume_axes(tt, idx, 331.1)
        hue = _hue_for(idx, 337.7)
        glow = _glow(tt, idx, 333.7)
        out.append({
            "index": float(idx),
            "x": rad * math.cos(math.radians(ang)) + dx, "y": h + dy,
            "depth": depth,
            "z": cz, "z2": cz2,
            "size": _depth_size(depth, CYLINDER_SIZE_BASE * scale),
            "alpha": min(1.0, _depth_alpha(depth, CYLINDER_ALPHA_BASE)
                         * glow),
            "lift": CYLINDER_LIFT_BASE + CYLINDER_LIFT_GAIN * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "r": rad, "h": h, "glow": glow,
        })
    return out


# --------------------------------------------------------------- screen break
#: Owner: "I like it how it tries to 'break' the screen, do more of that".
#: Owner 2026-10-19 (task 6): push it further. A handful of fast bodies on WIDE,
#: steeply-inclined orbits whose radius grows past the mark's own envelope, so
#: they visibly sweep out of the widget. The orbit radius fraction reaches
#: :data:`SCREEN_BREAK_FRAC` (``> 1`` = beyond R); the excursion is a continuous
#: full turn, never a pop. Raised 1.18 -> 1.34 R and COUNT 8 -> 10 so the set
#: reliably crosses the widget edge (a 2R-wide frame has its edge at ~1.14 R)
#: rather than merely grazing the mark radius. The primitive budget absorbs ten
#: bodies (renderer draw limits are 12); ``R_FRAC_MIN + R_FRAC_SPAN`` still
#: equals ``SCREEN_BREAK_FRAC`` on purpose, so the declared ceiling and the
#: reachable maximum cannot drift apart.
SCREEN_BREAK_COUNT = 10
SCREEN_BREAK_FRAC = 1.34
SCREEN_BREAK_R_FRAC_MIN = 0.96
SCREEN_BREAK_R_FRAC_SPAN = 0.38
SCREEN_BREAK_PERIOD_BASE = 3.2
SCREEN_BREAK_PERIOD_SPAN = 3.4
SCREEN_BREAK_INC_MIN_DEG = 26.0
SCREEN_BREAK_INC_SPAN_DEG = 116.0
SCREEN_BREAK_SIZE_BASE = 0.28
SCREEN_BREAK_ALPHA_BASE = 0.80
SCREEN_BREAK_LIFT_BASE = 0.35
SCREEN_BREAK_LIFT_GAIN = 0.50


def screen_break(t: Any, R: float) -> List[Dict[str, Any]]:
    """Fast bodies on wide inclined orbits that reach BEYOND the mark radius.

    One dict per body: ``{"index", "x", "y", "depth", "z", "z2", "vx", "vy",
    "size", "alpha", "lift", "hue", "hue2", "dof", "skew", "r", "inc"}``. Each
    sweeps a full 360 at its own fast, non-harmonic period on a plane inclined
    by ``inc``; its orbit radius fraction grows up to :data:`SCREEN_BREAK_FRAC`
    (``> 1``), so at the widest phase a body is outside the widget's nominal
    radius — the deliberate overflow the owner asked for. NO perspective
    contraction is applied here: the layer exists to leave the mark, so pulling
    the far points in would fight its whole purpose. Continuous by
    construction (a body rides its circle, it never jumps out and back), and
    pure in ``(t, R)``: ``hash01`` and trig only, no ``random`` anywhere.
    """
    tt = _finite(t)
    r = _finite(R, 32.0) or 32.0
    dx, dy = camera_offset(tt, r)
    scale = r / 32.0
    out: List[Dict[str, Any]] = []
    for i in range(SCREEN_BREAK_COUNT):
        idx = i + 1
        rad = r * (SCREEN_BREAK_R_FRAC_MIN
                   + SCREEN_BREAK_R_FRAC_SPAN * hash01(idx, 601.3))
        inc = math.radians(SCREEN_BREAK_INC_MIN_DEG
                           + SCREEN_BREAK_INC_SPAN_DEG * hash01(idx, 603.7))
        period = (SCREEN_BREAK_PERIOD_BASE
                  + SCREEN_BREAK_PERIOD_SPAN * hash01(idx, 609.1))
        direction = 1.0 if hash01(idx, 611.7) >= 0.5 else -1.0
        phase0 = 360.0 * hash01(idx, 619.3)
        rate = direction * 360.0 / max(0.1, period)
        ang = phase0 + rate * tt + _phase_wobble(tt, phase0)
        ar = math.radians(ang)
        u, v = math.cos(ar), math.sin(ar)
        # Inward-only radial breath: the break bodies surge toward and away
        # from the edge without ever exceeding the declared ceiling.
        drift = _drift01(tt, idx, 613.1)
        rad = rad * (1.0 - 0.045 * drift)
        x = rad * u
        y = rad * v * math.cos(inc)
        zunit = v * math.sin(inc)
        depth = min(1.0, max(0.0, 0.5 + 0.5 * zunit))
        # Screen velocity per second (the dt of the orbit), so a renderer can
        # streak the body through :func:`_rgb_trail`.
        vx = -rad * v * rate
        vy = rad * u * math.cos(inc) * rate
        wx, wy = micro_offset(tt, idx, r, 619.9)
        bz, bz2 = _volume_axes(tt, idx, 623.7)
        hue = _hue_for(idx, 629.1)
        glow = _glow(tt, idx, 621.1)
        out.append({
            "index": float(idx),
            "x": x + dx + wx, "y": y + dy + wy,
            "depth": depth,
            "z": bz, "z2": bz2,
            "vx": vx, "vy": vy,
            "size": _depth_size(depth, SCREEN_BREAK_SIZE_BASE * scale
                                * (0.94 + 0.12 * drift)),
            "alpha": min(1.0, _depth_alpha(depth, SCREEN_BREAK_ALPHA_BASE)
                         * glow),
            "lift": SCREEN_BREAK_LIFT_BASE + SCREEN_BREAK_LIFT_GAIN * depth,
            "hue": hue, "hue2": _hue_pair(hue),
            "dof": dof(depth), "skew": skew(depth),
            "r": rad, "inc": inc, "drift": drift, "glow": glow,
        })
    return out


__all__ = [
    "PARTICLES", "RX_FRAC", "RY_FRAC", "TILT_A", "TILT_B", "NUCLEUS_FRAC",
    "FRINGE_PX", "LIFT_RING", "LIFT_BEAD", "LIFT_NUCLEUS",
    "MIN_MARK_BRIGHTNESS",
    "HEART_PERIOD_S", "HEART_SCALE_AMP", "HALO_SCALE", "HALO_BASE",
    "HALO_WIDTH", "SHOCKWAVE_S", "SHOCKWAVE_MAX_SCALE", "SHOCKWAVE_ALPHA",
    "SHOCKWAVE_ATTACK_FRAC",
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
    "math_state",
    "volume_particles", "VOLUME_COUNT", "VOLUME_R_FRAC_MIN",
    "VOLUME_R_FRAC_SPAN", "VOLUME_PERIOD_BASE", "VOLUME_PERIOD_SPAN",
    "VOLUME_INC_MIN_DEG", "VOLUME_INC_SPAN_DEG", "VOLUME_SIZE_BASE",
    "VOLUME_SIZE_SPAN", "VOLUME_ALPHA_BASE", "VOLUME_ALPHA_GAIN",
    "VOLUME_LIFT_BASE", "VOLUME_LIFT_GAIN", "VOLUME_PULSE_PERIOD_BASE",
    "VOLUME_PULSE_PERIOD_SPAN", "VOLUME_SPIN_PERIOD_BASE",
    "VOLUME_SPIN_PERIOD_SPAN",
    "hue_to_rgb", "rgb_trail", "dust_trail", "RGB_TRAIL_SAMPLES",
    "RGB_TRAIL_HUE_STEP", "RGB_TRAIL_HUE_DRIFT", "RGB_TRAIL_ALPHA_BASE",
    "RGB_TRAIL_WHITE_FRAC", "RGB_TRAIL_SAT_BASE", "RGB_TRAIL_SAT_SPAN",
    "DUST_TRAIL_SAMPLES", "PARTICLE_HUE_BASE", "PARTICLE_HUE_SPAN",
    "PALETTE_HUES", "palette_hue", "_hue_for",
    "HUE_PAIR_OFFSET", "_hue_pair",
    "dof", "DOF_NEAR", "DOF_FAR", "DOF_GAMMA", "skew", "SKEW_GAIN_DEG",
    "RING_PERIOD_NEAR", "RING_PERIOD_FAR",
    "ring_geometry", "ring_path", "ring_alpha", "RING_PATH_SAMPLES",
    "RING_ALPHA_MAIN", "RING_ALPHA_ECHO", "RING_ALPHA_INNER",
    "RING_ALPHA_SAT", "RING_WIDTH_MAIN", "RING_WIDTH_INNER",
    "RING_BREATH_SPAN",
    "blink_dots", "BLINK_COUNT", "BLINK_SIZE_BASE", "BLINK_ALPHA_PEAK",
    "BLINK_LIFE_BASE", "BLINK_LIFE_SPAN", "BLINK_LIFT",
    "BLINK_R_FRAC_MIN", "BLINK_R_FRAC_SPAN",
    "flare_dots", "FLARE_COUNT", "FLARE_SIZE_BASE", "FLARE_SIZE_SPAN",
    "FLARE_ALPHA_PEAK", "FLARE_LIFE_BASE", "FLARE_LIFE_SPAN", "FLARE_LIFT",
    "FLARE_R_FRAC_MIN", "FLARE_R_FRAC_SPAN",
    "spin_angles", "X_SPIN_PERIOD_S", "Y_SPIN_PERIOD_S",
    "depth_axes", "Z_SALT", "Z2_SALT",
    "core_shells", "CORE_SHELL_COUNT", "CORE_SHELL_PERIODS_S",
    "CORE_SHELL_R_FRAC_MIN", "CORE_SHELL_R_FRAC_SPAN", "CORE_SHELL_TILT_BASE",
    "CORE_SHELL_TILT_STEP", "CORE_SHELL_SPIN_BASE", "CORE_SHELL_SPIN_STEP",
    "CORE_SHELL_SIZE_FRAC", "CORE_SHELL_SIZE_SPAN", "CORE_SHELL_ALPHA_BASE",
    "CORE_SHELL_ALPHA_GAIN", "CORE_SHELL_LIFT_BASE", "CORE_SHELL_LIFT_GAIN",
    "filament_state", "FILAMENT_SAMPLES", "FILAMENT_PERIOD_S",
    "FILAMENT_WAVE_COUNT", "FILAMENT_WAVE_SPEED", "FILAMENT_RX_FRAC",
    "FILAMENT_TILT_BASE", "FILAMENT_TILT_SWAY_DEG", "FILAMENT_ALPHA_BASE",
    "FILAMENT_ALPHA_PEAK", "FILAMENT_SIZE_BASE", "FILAMENT_SIZE_PEAK",
    "FILAMENT_LIFT",
    "ring_precess_period", "ring_tilt_spin", "ring_radius_scale",
    "RING_MOTION_PERIOD_S", "RING_MOTION_PERIOD_2_S", "RING_MOTION_PERIOD_3_S",
    "RING_MOTION_PHASE", "RING_PRECESS_AMP_DEG", "RING_PRECESS_WEIGHTS",
    "RING_RADIUS_BREATH_SPAN", "RING_RADIUS_WEIGHTS",
    "RING_RADIUS_BREATH_PHASE", "RING_MOTION_PERIODS_S",
    "ring_intensity", "ring_shimmer", "RING_INTENSITY_MIN",
    "RING_INTENSITY_SPAN", "RING_INTENSITY_PERIOD_BASE",
    "RING_INTENSITY_PERIOD_SPAN", "RING_SHIMMER_SPAN",
    "RING_SHIMMER_PERIOD_BASE", "RING_SHIMMER_PERIOD_SPAN",
    "RING_STREAMERS_BASE", "RING_STREAMERS_SPAN",
    "accretion_stream", "ACCRETION_COUNT", "ACCRETION_R_OUT_FRAC",
    "ACCRETION_R_IN_FRAC", "ACCRETION_PERIOD_BASE", "ACCRETION_PERIOD_SPAN",
    "ACCRETION_SPIRAL_TURNS", "ACCRETION_SIZE_BASE", "ACCRETION_SIZE_SPAN",
    "ACCRETION_ALPHA_BASE", "ACCRETION_ALPHA_GAIN", "ACCRETION_LIFT",
    "photon_ring", "PHOTON_SAMPLES", "PHOTON_RX_FRAC", "PHOTON_RY_FRAC",
    "PHOTON_TILT_BASE", "PHOTON_TILT_SWAY_DEG", "PHOTON_PERIOD_S",
    "PHOTON_LOBES", "PHOTON_ALPHA_BASE", "PHOTON_ALPHA_PEAK",
    "PHOTON_SIZE_BASE", "PHOTON_SIZE_PEAK", "PHOTON_LIFT",
    "infall_streaks", "INFALL_COUNT", "INFALL_R_OUT_FRAC", "INFALL_R_IN_FRAC",
    "INFALL_PERIOD_BASE", "INFALL_PERIOD_SPAN", "INFALL_SIZE_BASE",
    "INFALL_SIZE_SPAN", "INFALL_ALPHA_BASE", "INFALL_ALPHA_GAIN",
    "INFALL_LIFT", "INFALL_TAIL_LEN_FRAC",
    "ring_particles", "RING_PARTICLE_COUNTS", "RING_PARTICLE_DEFAULT",
    "RING_PARTICLE_PERIOD_BASE", "RING_PARTICLE_PERIOD_SPAN",
    "RING_PARTICLE_SIZE", "RING_PARTICLE_ALPHA",
    "RING_PARTICLE_LIFT_BASE", "RING_PARTICLE_LIFT_GAIN",
    "cylinder_particles", "CYLINDER_COUNT", "CYLINDER_R_FRAC_MIN",
    "CYLINDER_R_FRAC_SPAN", "CYLINDER_H_FRAC", "CYLINDER_PERIOD_S",
    "CYLINDER_DRIFT_PERIOD_S", "CYLINDER_DRIFT_AMP", "CYLINDER_H_BASE",
    "CYLINDER_SIZE_BASE", "CYLINDER_ALPHA_BASE", "CYLINDER_LIFT_BASE",
    "CYLINDER_LIFT_GAIN",
    "orbiter_particles", "ORBITER_COUNT", "ORBITER_R_FRAC_MIN",
    "ORBITER_R_FRAC_SPAN", "ORBITER_PERIOD_BASE", "ORBITER_PERIOD_SPAN",
    "ORBITER_INC_MIN_DEG", "ORBITER_INC_SPAN_DEG", "ORBITER_SIZE_BASE",
    "ORBITER_ALPHA_BASE", "ORBITER_LIFT_BASE", "ORBITER_LIFT_GAIN",
    "screen_break", "SCREEN_BREAK_COUNT", "SCREEN_BREAK_FRAC",
    "SCREEN_BREAK_R_FRAC_MIN", "SCREEN_BREAK_R_FRAC_SPAN",
    "SCREEN_BREAK_PERIOD_BASE", "SCREEN_BREAK_PERIOD_SPAN",
    "SCREEN_BREAK_INC_MIN_DEG", "SCREEN_BREAK_INC_SPAN_DEG",
    "SCREEN_BREAK_SIZE_BASE", "SCREEN_BREAK_ALPHA_BASE",
    "SCREEN_BREAK_LIFT_BASE", "SCREEN_BREAK_LIFT_GAIN",
    "haze_state", "HAZE_COUNT", "HAZE_R_FRAC_MIN", "HAZE_R_FRAC_SPAN",
    "HAZE_PERIOD_BASE", "HAZE_PERIOD_SPAN", "HAZE_INC_MIN_DEG",
    "HAZE_INC_SPAN_DEG", "HAZE_SIZE_BASE", "HAZE_SIZE_SPAN",
    "HAZE_ALPHA_BASE", "HAZE_ALPHA_GAIN", "HAZE_BREATH_PERIOD_BASE",
    "HAZE_BREATH_PERIOD_SPAN", "HAZE_LIFT",
    "micro_offset", "MICRO_WOBBLE_FRAC", "VOLUME_SWAY_PERIOD_A",
    "VOLUME_SWAY_PERIOD_B", "VOLUME_SWAY_SPAN",
    "CAMERA_SWAY_WEIGHTS", "CAMERA_SWAY_PERIOD_X2", "CAMERA_SWAY_PERIOD_Y2",
    "FILAMENT_WAVE_COUNT_2", "FILAMENT_WAVE_SPEED_2",
    "FILAMENT_WAVE_WEIGHTS", "CORE_SHELL_SWIRL_SPAN",
    "PHOTON_PRECESS_PERIOD_S", "PHOTON_SPARKLE_PERIOD_S",
]
