"""Qubit-mark orb renderer: the dashboard sidebar logo as the desktop orb.

Same visual language as ``frontend/qubit-web/src/components/QubitMark.tsx`` +
``styles/qubit-mark.css``: a hairline construction drawing, never a glow sticker.

  * every circle declared ONCE by ``mark_field.ring_geometry`` (the two main
    orbits, their lagged echo followers, the fast inner core ring and the three
    satellite shells), stroked back to front,
  * a small solid nucleus at the centre, beating, inside a smoky aura and a
    chromatic corona,
  * nine particles riding each ellipse plus a dense dust field, a comet and a
    binary data-fragment field,
  * a chromatic fringe faked on the two MAIN hairline orbits only (Tk has no
    blend modes, so the fringe is two extra offset strokes in dark red / blue
    on near-black).

ALL of the motion — orbits, particles, streaks, dust, comet, beat, glints — comes
from :mod:`voicebridge.orb.mark_field`, the single deterministic description
shared with the Qt renderer. This module is ONLY the Tk translator: it consumes
the field, adds ``(cx, cy)`` to the centre-relative coordinates the field
returns, and draws with the techniques Tk actually has. It never re-derives
geometry or re-implements the field's maths.

State meaning is preserved from ``ember`` (the single palette source): the
``STATE_CORES`` hue still tints nucleus, beads and rings, so idle / listening /
thinking / working / speaking / error read exactly as before. Geometry that the
host owns (window placement, drag, click, collapse dot, indicator dots) is
untouched.

Tk has no rotated ellipse, no per-item alpha and no gradient, so:

  * an ellipse is drawn as a smoothed closed polyline sampled from the field's
    ``ring_path`` (which is itself sampled from ``orbit_point``);
  * "opacity" is a mix toward the near-black background, precomputed per
    primitive (``_mix`` + ``_hex`` + ``_dim``); circles are ``create_oval``;
  * a radial gradient is approximated by concentric filled ovals
    (``NUCLEUS_LAYERS`` for the body, ``_soft_oval`` for the aura/corona);
  * a spectral streak is drawn as one short stroke per consecutive pair of the
    field's ``rgb_trail`` / ``dust_trail`` samples, each coloured through
    ``mark_field.hue_to_rgb`` — hue is NEVER interpolated across the wheel.

Two whole-mark transforms are layered on every element:

  1. the master spin — a slow 360 deg turn in the screen plane (24 s), and
  2. the true-3D tumble — full 360 deg turns about the mark's vertical (Y,
     20.5 s) AND horizontal (X, 27.0 s) axes, Y first then X, exactly like the
     Qt painter. Every element carries the field's ``depth``, so the rotation
     mixes each point's x with its depth instead of foreshortening the flat
     plane by ``cos(theta)``: the near and far halves SEPARATE across x where
     the old squash collapsed to a line, and a perspective term plus a
     rotated-depth size/alpha gain sell the turn. ``spin_angles`` is the
     field's one phase source, so both lanes tumble the same way.

Both read the clamped clock, so under reduced motion neither is applied and
the frame is byte-identical for any ``t``. The NUCLEUS is deliberately outside
both 3-D turns: a sphere keeps its circular silhouette under a rotation about
its own axes, and squashing the core into a lens would read as a bug rather
than as depth.

Depth is the 3-D read: the field splits every element at ``depth = 0.5`` and
this renderer draws the far half behind the nucleus and the near half in front.

This module never opens a window: ``render`` takes any canvas-like object with
``create_oval``/``create_line``/``create_text`` (a hermetic fake works for proof).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from . import ember as _ember
from .mark_field import (
    hash01,
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
    RGB_TRAIL_HUE_DRIFT,
    X_SPIN_PERIOD_S,
    spin_angles,
    blink_dots,
    BLINK_COUNT,
    RING_ALPHA_MAIN,
    RING_ALPHA_INNER,
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
    ECHO_LAG_S,
    ECHO_SCALE,
    ECHO_ALPHA,
    ECHO_WIDTH,
    nucleus_sparks,
    NUCLEUS_SPARK_COUNT,
    NUCLEUS_SPARK_REACH,
    inner_ring_tilt,
    binary_state,
    hue_to_rgb,
    rgb_trail,
    dust_trail,
    ring_geometry,
    ring_path,
    ring_alpha,
    dof,
    skew,
    INNER_RING_RX_FRAC,
    BINARY_COUNT,
)

#: Fringe offset in DEVICE pixels, never scaled with the radius: a lens fringe
#: is a pixel-level artifact, and scaling it turned the mark into three separate
#: rainbow rings instead of a hint on one hairline. Mirrors the Qt painter.
FRINGE_PX_AT_96 = FRINGE_PX
FRINGE_PIXELS = FRINGE_PX * 0.7

#: Polyline sampling resolution for one ellipse drawn outside the declared set
#: (the beat shockwave). The declared circles bring their own sampling.
RING_SEGMENTS = 72

#: Painter-side glint refinement, mirroring the Qt painter: the shared field
#: sets the size, but Tk's old arm/core floors kept tiny dust and sparkles
#: chunky. A low size floor, a smaller core and the field's own arm fraction
#: turn the crossing arms into pinpricks instead of fat crosses.
GLINT_MIN_SIZE = 0.16
GLINT_CORE_SCALE = 0.58
GLINT_CORE_MIN = 0.08

#: Below this effective alpha a primitive is imperceptible on an 8-bit canvas:
#: skipping it is cheaper than letting the quantisation snap it in one frame.
ALPHA_EPS = 0.004
#: What decides whether an ELEMENT exists is the FIELD's own alpha, never this
#: renderer's shading: a body the field emitted must not be dropped by the
#: tumble dim or the depth-of-field softening, or its life would end early and
#: a whole layer would breathe on the renderer instead of on the field.
FIELD_ALPHA_FLOOR = 1e-3

# --- spectral streaks (the owner's "RGB trail, not an outer glow") -------------
#: A star leaves a TAPERED WAKE built from the field's ``rgb_trail`` /
#: ``dust_trail`` samples. There is no halo around a glint any more: the streak
#: is the effect. Each consecutive pair of samples is one short stroke, coloured
#: with ``hue_to_rgb`` for that sample (hue wraps like a colour wheel, so it is
#: converted per sample and never interpolated across the wrap).
STREAK_HALF_WIDTH = 0.30
STREAK_MIN_HALF_PX = 0.34
STREAK_MAX_WIDTH = 6
#: Below this total arc length the wake is not drawn at all: a sub-pixel ribbon
#: is one 8-bit step from appearing, which is exactly the hard pop the field's
#: C1 envelopes exist to avoid.
STREAK_MIN_LEN_PX = 0.75
#: Sparkles and satellite bodies take the SHORT streak: 40 glints and three
#: shells must gain spectrum without turning the field into 40 comets.
SHORT_STREAK_HALF_WIDTH = 0.34
SATELLITE_STREAK_GAIN = 1.1
SPARKLE_STREAK_GAIN = 1.0
COMET_STREAK_HALF_WIDTH = 0.36
COMET_STREAK_GAIN = 1.0
#: A streak is pulled this far toward the state's lifted nucleus colour: full
#: saturation is the point, but an untinted rainbow in a mark that changes
#: colour with the state would read as a different product.
STREAK_HUE_ANCHOR_BLEND = 0.10
#: The comet's own bead tail rides the same hue ramp as its wake, so it reads as
#: bright motion-blur debris inside the streak instead of a second grey tail.
COMET_BEAD_SAT = 0.95

# --- true-3D whole-mark tumble (parity with the Qt painter) ------------------
#: Master spin: a slow 360 deg rotation of the whole mark in the screen plane.
MASTER_SPIN_PERIOD_S = 24.0
#: Owner: "add one more spinning animation from left to right horizontal spin
#: that spins 360". The mark is NOT flat — every element carries the field's
#: ``depth`` — so the turn is a real rotation about the mark's vertical (Y)
#: axis layered with a second full turn about its horizontal (X) axis, Y first
#: then X, exactly like the Qt painter's per-point placement. The near and far
#: halves SEPARATE instead of piling onto one line, which is what killed the
#: old flat ``cos`` squash ("i dont what that thin"). 20.5 s is deliberately
#: non-harmonic with the 24 s master spin and with the 9.5 s near-ring period
#: (19 s would be exactly twice it, and the two motions would lock into one
#: pattern every half turn); 27.0 s is non-harmonic with both.
Y_SPIN_PERIOD_S = 20.5
X_SPIN_PERIOD_S = 27.0
#: Depth-axis gain: how far a point's ``depth`` reaches along the rotation
#: axis, in mark radii. What keeps the quarter turn OPEN: at theta = 90 deg a
#: ring's x-width is this times its depth spread, not zero.
Y_SPIN_Z_GAIN = 1.25
#: Perspective: ``1 + PERSP * zr / R``. Points that swing toward the viewer
#: grow apart; points tucking away contract — the cheap cue that reads as
#: volume rather than as a wobble.
Y_SPIN_PERSP = 0.35
Y_SPIN_PERSP_MIN = 0.55
Y_SPIN_PERSP_MAX = 1.70
#: Size/alpha gain on the rotated depth: a body swinging TOWARD the viewer
#: grows and brightens, one tucking away shrinks and dims.
Y_SPIN_DEPTH_GAIN = 0.55
Y_SPIN_GAIN_MIN = 0.35
Y_SPIN_GAIN_MAX = 1.90
#: Uncorrelated projection depth (the "thin line" fix — parity with the Qt
#: painter, same salt/spreads so both lanes put every element in the same
#: place). The field's ``depth`` is ``0.5 + 0.5*cos(angle)`` while an orbit's
#: x is ``~rx*cos(angle)``: depth is a LINEAR function of x, so the whole mark
#: is secretly one flat plane, which any rotation still takes thin twice per
#: turn. ``turn_qdepth`` breaks that lock per element (static hash) and per
#: ring (smooth phased lobe, never per-vertex noise).
TURN_Z_SALT = 17.31
TURN_Z_SPREAD = 0.9
TURN_RING_TILT = 0.35
TURN_RING_SALT = 5.77
#: Depth of field: how much a far element's alpha is pulled down. ``dof`` is
#: 0.0 sharp / 1.0 far, so this softens distance WITHOUT touching brightness
#: meaning — "far away" must not collapse into "barely visible".
DOF_ALPHA_SOFT = 0.22

# --- one declared geometry, every circle's art direction ---------------------
#: ``mark_field.ring_geometry`` declares EVERY circle of the mark once (the two
#: main orbits, their echo followers, the inner core ring and the three
#: satellite shells) with its radius, layer, width and order, already ordered
#: back to front. The renderer strokes that list and adds nothing but its own
#: look, so the circles cannot drift from the declaration. These scales only
#: translate a declared alpha into the mark's established brightness ladder.
RING_ALPHA_SCALE = {
    # The two main orbits straddle the nucleus: the far one is drawn dimmer
    # behind it and the near one brighter in front, which is how the two
    # complete ellipses read as one tilted plane.
    "far_main": 0.60 / RING_ALPHA_MAIN,
    "near_main": 0.95 / RING_ALPHA_MAIN,
    # An echo follower breathes on top of the shared value and the depth-of-
    # field softening pulls it down again: the scale puts the whole breathing
    # band back above the alpha at which a follower still reads as a follower,
    # and far below the main orbits' ladder.
    "echo": 1.35,
    # The core ring is the quietest circle in the mark.
    "inner": 0.16 / RING_ALPHA_INNER,
    "satellite": 1.0,
}

# --- the studio nucleus ------------------------------------------------------
#: The shaded body is drawn 1.25x the field's base radius. The field still owns
#: the heartbeat breathing; this only scales the body.
NUCLEUS_RADIUS_MULT = 1.25
#: How far the body's centre is lifted toward white — the hot core the shaded
#: gradient falls out of.
NUCLEUS_CORE_LIFT = 0.78
#: Concentric filled ovals approximate the radial gradient Tk cannot draw (hot
#: near-white core -> state hue -> fading rim), wrapped in a soft outline halo.
NUCLEUS_LAYERS = 9
HALO_LAYERS = 3
#: Two counter-rotating hot lobes inside the body, so the centre has internal
#: movement instead of a static gradient.
NUCLEUS_SWIRL_LAYERS = 3
NUCLEUS_SWIRL_RADIUS_FRAC = 0.22
NUCLEUS_SWIRL_SIZE_FRAC = 0.30
NUCLEUS_SWIRL_PERIOD_S = 6.0
NUCLEUS_SWIRL_RADIUS2_FRAC = 0.42
NUCLEUS_SWIRL_SIZE2_FRAC = 0.20
NUCLEUS_SWIRL_PERIOD2_S = 9.0
NUCLEUS_SWIRL_ALPHA = 0.85
#: How much the beat opens the swirl lobes: the core's internal motion is
#: louder on the thump and settles between beats.
NUCLEUS_SWIRL_BEAT_GAIN = 0.55
#: Fixed off-centre specular highlight, then a small hot dot running once around
#: the rim per period — the cue that reads as rotation rather than pulsing.
NUCLEUS_SPEC_OFFSET_FRAC = 0.38
NUCLEUS_SPEC_RADIUS_FRAC = 0.36
NUCLEUS_SPEC_LAYERS = 4
NUCLEUS_SPEC_ALPHA = 0.90
NUCLEUS_SPECULAR_ORBIT_FRAC = 0.50
NUCLEUS_SPECULAR_SIZE_FRAC = 0.07
NUCLEUS_SPECULAR_PERIOD_S = 12.0
NUCLEUS_SPECULAR_PHASE_DEG = 90.0
#: The caustic inside the body rides the beat: the hot inner bloom that makes
#: the core look like it is boiling rather than merely rotating. It never fully
#: vanishes, so the centre keeps a live inner light between beats.
NUCLEUS_CAUSTIC_RADIUS_FRAC = 0.86
NUCLEUS_CAUSTIC_LAYERS = 3
NUCLEUS_CAUSTIC_ALPHA = 0.62
NUCLEUS_CAUSTIC_ALPHA_BASE = 0.34
NUCLEUS_CAUSTIC_PERIOD_S = 4.7
#: Beat-synced flare: a hot bloom over the body that fires on the thump and
#: decays with it. A FILL, never a hairline, precisely so it can never be
#: mistaken for the shockwave's expanding ring.
NUCLEUS_FLARE_RADIUS_FRAC = 2.30
NUCLEUS_FLARE_ALPHA = 0.52
NUCLEUS_FLARE_ALPHA_BASE = 0.14
NUCLEUS_FLARE_LAYERS = 3
#: The aura is SMOKE, not a disc: slow drifting plumes that break the perfect
#: circle. In Tk these are concentric filled ovals, so they are drawn FIRST in
#: the frame — an opaque fill at a few percent alpha would otherwise erase the
#: hairline rings passing behind it (Tk has no alpha compositing; ``_dim`` is
#: contrast against the background, not a blend over what is already there).
NUCLEUS_SMOKE = (
    # (orbit_frac, size_frac, period_s, phase_deg, alpha_scale)
    (0.34, 0.92, 17.0, 0.0, 0.55),
    (0.52, 0.74, 23.0, 137.0, 0.40),
    (0.24, 0.58, 29.0, 251.0, 0.32),
)
NUCLEUS_SMOKE_LAYERS = 5
NUCLEUS_GLOW_MULT = 3.15
#: Tk has no alpha, so a glow is painted as an OPAQUE fill mixed toward the
#: background: anything below a few percent is indistinguishable from black.
#: The Qt painter can fade to 3% over the frame; these values are the Tk
#: medium's own floor for "a haze you can actually see", reached with a
#: gentler falloff and fewer stops instead of Qt's 24.
NUCLEUS_GLOW_ALPHA = 0.38
NUCLEUS_AURA_POWER = 1.2
#: Chromatic corona: soft lobes OUTSIDE the body turning slowly through the
#: spectrum, so the core reads as radiating rather than merely glowing. The
#: hue drift is small on purpose: over one full turn the whole corona stays
#: inside the mark's own cyan-to-magenta band.
NUCLEUS_CORONA_COUNT = 3
NUCLEUS_CORONA_LAYERS = 4
NUCLEUS_CORONA_RADIUS_FRAC = 2.95
NUCLEUS_CORONA_SQUASH = 0.88
NUCLEUS_CORONA_SIZE_FRAC = 1.35
NUCLEUS_CORONA_PERIOD_S = 29.0
NUCLEUS_CORONA_PHASE_DEG = 24.0
NUCLEUS_CORONA_ALPHA = 0.34  # see NUCLEUS_GLOW_ALPHA
NUCLEUS_CORONA_SAT = 0.92
NUCLEUS_CORONA_POWER = 1.3
NUCLEUS_CORONA_BREATH_S = 7.3
NUCLEUS_CORONA_BREATH_SPAN = 0.22
NUCLEUS_CORONA_HUE_BASE = 0.50
NUCLEUS_CORONA_HUE_TURNS = 0.018
NUCLEUS_CORONA_HUE_SPAN = 0.34
#: Fringe: a faint, softened split on the two MAIN orbits only, so the crisp
#: hairline stays the dominant stroke and the quiet circles stay quiet.
#: A hard-edged Tk stroke cannot be a sub-pixel alpha nudge the way the Qt
#: painter's 9% pen is: at any useful offset the fringe stops reading as a
#: chromatic hint on one hairline and becomes a second coloured line beside it.
#: Kept faint, it stays what the CSS mark's blurred drop shadow is — an edge.
FRINGE_ALPHA_MULT = 0.30
#: Hologram rim: a bright thin edge on the body plus a faint red/blue split
#: offset by a pixel — the lens-fringe cue, drawn as OUTLINES so the edge
#: stays a hairline and never a disc. Static (no time phase), so the still
#: frame keeps it.
NUCLEUS_RIM_ALPHA = 0.85
NUCLEUS_RIM_OFFSET_PX = 0.6
NUCLEUS_RIM_FRINGE_ALPHA = 0.35
NUCLEUS_RIM_OVALS = 3
#: Binary particles: tiny "1"/"0" data fragments. Sized from the field's own
BINARY_FONT_MIN_PX = 5
BINARY_FONT_PX_PER_UNIT = 12.0
BINARY_FONT_PX_OFFSET = 2.0

#: Exact primitive count of the nucleus stack, so a host or a test can predict
#: a still frame's ovals. Everything here is a pure function of the clamped
#: clock, so reduced motion paints the identical static body.
NUCLEUS_OVAL_COUNT = (
    len(NUCLEUS_SMOKE) * NUCLEUS_SMOKE_LAYERS      # drifting plumes
    + NUCLEUS_CORONA_COUNT * NUCLEUS_CORONA_LAYERS  # chromatic corona
    + NUCLEUS_FLARE_LAYERS                          # beat bloom
    + HALO_LAYERS                                   # outline smoke rings
    + NUCLEUS_LAYERS                                # the shaded body
    # On-body detail loses its zero-alpha rim stop (see ``_soft_oval``).
    + (NUCLEUS_CAUSTIC_LAYERS - 1)
    + 2 * (NUCLEUS_SWIRL_LAYERS - 1)
    + (NUCLEUS_SPEC_LAYERS - 1)
    + 1                                             # the orbiting specular
    + NUCLEUS_RIM_OVALS  # hologram rim: bright edge + red/blue split
)

#: Fringe channel colors, mixed toward the background (Tk alpha substitute).
_FRINGE_RED = (150, 30, 55)
_FRINGE_BLUE = (30, 80, 160)

#: Signature-compatible alias: the Qt renderer calls ``_mark._orbit_point``.
_orbit_point = orbit_point


def _clamp8(v: Any) -> int:
    try:
        return max(0, min(255, int(v)))
    except (TypeError, ValueError):
        return 0


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def spin_deg(t: Any) -> float:
    """Master-spin angle in degrees: one full turn per 24 s, never wrapped."""
    try:
        tt = float(t)
    except (TypeError, ValueError):
        tt = 0.0
    if not math.isfinite(tt):
        tt = 0.0
    return 360.0 * tt / MASTER_SPIN_PERIOD_S


def horizontal_spin_deg(t: Any) -> float:
    """Horizontal-tumble angle in degrees: one full turn per 20.5 s.

    The second, INDEPENDENT whole-mark rotation (see :data:`Y_SPIN_PERIOD_S`).
    A linear ramp, never a wrapped phase, so it cannot snap at the turn
    boundary. Reads the CLAMPED clock, so reduced motion yields ``0.0``.
    """
    return 360.0 * _num(t) / Y_SPIN_PERIOD_S


def horizontal_spin(t: Any) -> float:
    """The Y turn's ``cos(theta)``: the phase read of the horizontal 360.

    The mark is no longer flat, so this is no longer what the renderer scales
    the assembly by (that flat squash degenerated to a line twice per turn —
    the "thin" the owner rejected). It stays as the public read of the turn's
    phase: +1 face-on, -1 mirrored at the half turn. The renderer projects
    through :func:`turn_point` instead. Exactly ``1.0`` at a still clock.
    """
    theta = math.radians(math.fmod(horizontal_spin_deg(t), 360.0))
    return math.cos(theta)


def turn_basis(t: Any) -> Tuple[float, float, float, float]:
    """``(cos theta, sin theta, cos phi, sin phi)`` of the tumble at ``t``.

    ``theta`` is the Y turn (20.5 s), ``phi`` the X turn (27.0 s), both from
    the field's :func:`spin_angles` so the Tk and Qt lanes share one phase.
    Resolved once per frame; every point in the frame shares it.
    """
    try:
        theta, phi = spin_angles(t)
    except Exception:  # noqa: BLE001 - a field fault rests the unturned mark
        theta, phi = 0.0, 0.0
    return (math.cos(theta), math.sin(theta), math.cos(phi), math.sin(phi))


def turn_project(x: float, y: float, depth: float,
                 ct: float, st: float, cp: float, sp: float,
                 r: float) -> Tuple[float, float, float]:
    """Project one centre-relative field point: Y turn first, then X.

    Returns ``(X, Y, gain)`` in field units plus the rotated-depth size/alpha
    gain. The arithmetic mirrors the Qt painter's per-point placement with the
    same constants, so the two lanes put every element in the same place::

        z   = (depth - 0.5) * Z_GAIN * R * sin(theta)**2
        xr  = x * cos(theta) + z * sin(theta)
        zr0 = -x * sin(theta) + z * cos(theta)
        yr  = y * cos(phi) - zr0 * sin(phi)
        zr  = y * sin(phi) + zr0 * cos(phi)
        persp = 1 + PERSP * zr / R
        X, Y = xr * persp, yr * persp
        gain = 1 + DEPTH_GAIN * zr / R   (clamped)

    The depth offset is faded by ``sin(theta)**2``: at ``theta = 0`` and
    ``theta = pi`` every term collapses to ``xr = +-x``, ``persp = 1`` and
    ``gain = 1``, so the face-on phases are the unturned mark, bit for bit.
    ``phi = 0`` is likewise the identity for the X turn. ``y`` is untouched
    by the Y turn and ``x`` by the X turn, which is exactly why the mark's
    height never changes under Y alone and the turn never thins to a line:
    the depth axis carries the quarter phases.
    """
    z = (depth - 0.5) * Y_SPIN_Z_GAIN * r * st * st
    xr = x * ct + z * st
    zr0 = -x * st + z * ct
    yr = y * cp - zr0 * sp
    zr = y * sp + zr0 * cp
    persp = 1.0 + Y_SPIN_PERSP * zr / r
    if persp < Y_SPIN_PERSP_MIN:
        persp = Y_SPIN_PERSP_MIN
    elif persp > Y_SPIN_PERSP_MAX:
        persp = Y_SPIN_PERSP_MAX
    nz = zr / r
    gain = 1.0 + Y_SPIN_DEPTH_GAIN * nz
    if gain < Y_SPIN_GAIN_MIN:
        gain = Y_SPIN_GAIN_MIN
    elif gain > Y_SPIN_GAIN_MAX:
        gain = Y_SPIN_GAIN_MAX
    return (xr * persp, yr * persp, gain)


def turn_qdepth(depth: Any, zseed: Any = None,
                spread: Any = TURN_Z_SPREAD,
                flat: bool = False) -> float:
    """Projection depth with the x-lock broken (parity with Qt's helper).

    Pure: ``depth`` clamped to 0..1; no seed is the identity. With a seed,
    flat mode returns ``hash01(seed, TURN_Z_SALT)`` itself (points: a swarm
    must fill the volume, not ride one plane); blend mode adds
    ``spread * (hash - 0.5)`` (rings: a whisper that keeps smooth loops
    smooth). Same salt/spreads as the Qt painter, so both lanes agree.
    At face-on phases the fade zeroes z whatever this returns. Never raises.
    """
    try:
        d = float(depth)
    except (TypeError, ValueError):
        d = 0.5
    if not math.isfinite(d):
        d = 0.5
    d = min(1.0, max(0.0, d))
    if zseed is None:
        return d
    try:
        h = hash01(float(zseed), TURN_Z_SALT)
    except (TypeError, ValueError):
        return d
    if flat:
        return h
    try:
        sp = float(spread)
    except (TypeError, ValueError):
        sp = TURN_Z_SPREAD
    if not math.isfinite(sp):
        sp = TURN_Z_SPREAD
    return min(1.0, max(0.0, d + sp * (h - 0.5)))


def turn_point(t: Any, R: Any, x: Any, y: Any,
               depth: Any = 0.5) -> Tuple[float, float, float]:
    """Project one centre-relative field point through the Y+X tumble.

    Module level (like the Qt painter's ``y_turn_point``) so tests read the
    same projection the renderer paints. ``(X, Y, gain)`` in field units,
    relative to the mark's centre; the caller adds the centre. The identity
    at ``t = 0`` for every ``(x, y, depth)``. Never raises.
    """
    try:
        r = _num(R, 32.0)
    except Exception:  # noqa: BLE001
        r = 32.0
    if r <= 0.0:
        r = 32.0
    try:
        d = min(1.0, max(0.0, _num(depth, 0.5)))
        xx, yy = _num(x), _num(y)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 1.0)
    ct, st, cp, sp = turn_basis(t)
    return turn_project(xx, yy, d, ct, st, cp, sp, r)


def nucleus_radius(R: Any, t: Any, level_gain: Any = 1.0) -> float:
    """The nucleus BODY's radius at clock ``t`` — the studio body's own law.

    The field owns the heartbeat breathing and the talk meter owns the level
    pump; this only says how large the drawn body is for a given mark radius,
    so a host (or a test) can identify the body among the concentric ovals the
    studio nucleus is built from instead of guessing which one is which.
    Pure, and a no-op on the motion terms under a still clock. Never raises.
    """
    base = max(1.1, _num(R, 32.0) * NUCLEUS_FRAC)
    try:
        nscale = nucleus_scale(_num(t))
        beat = heartbeat(_num(t))
    except Exception:  # noqa: BLE001 - a field fault rests the body
        nscale, beat = 1.0, 0.0
    gain = max(0.0, min(1.0, _num(level_gain, 1.0)))
    return (base * NUCLEUS_RADIUS_MULT * nscale
            * (1.0 + 0.08 * beat) * (0.95 + 0.05 * gain))


def _spin_pt(x: float, y: float, t: float = 0.0,
              cx: float = 0.0, cy: float = 0.0) -> Tuple[float, float]:
    """Place a field point on screen: the Y+X tumble, then the master spin.

    The tumble projects the point at neutral depth (a bare point carries no
    depth of its own) in the mark's own frame; the master spin rotates the
    result in the screen plane about ``(cx, cy)`` — the Qt painter's
    composition. At a still clock this is the identity, which is what makes
    reduced motion a true no-op. Kept for callers that place depth-free
    points; the renderer itself threads each element's own depth through
    :func:`turn_point`.
    """
    ct, st, cp, sp = turn_basis(t)
    # NOTE: neutral-depth projection of a bare point; ``r`` only scales the
    # (zero) depth offset and the perspective of the rotated position, so any
    # sane radius does — 32.0 is the field's own unit.
    px, py, _g = turn_project(float(x) - float(cx), float(y) - float(cy),
                              0.5, ct, st, cp, sp, 32.0)
    a = math.radians(math.fmod(spin_deg(t), 360.0))
    return (float(cx) + px * math.cos(a) - py * math.sin(a),
            float(cy) + px * math.sin(a) + py * math.cos(a))


def echo_follower_tilt(t: Any, orbit: Any = "a") -> float:
    """Where the FOLLOWER of ``orbit`` sits, from the shared echo contract.

    :func:`mark_field.ring_geometry` composes its own echo lag internally, so
    its follower entries carry a double-lagged tilt; the single-lag follower is
    the contract every other consumer (the field's ``echo_rings``, the echo
    contract test, the Tk lane) uses. This reads that declaration, so both
    lanes agree on where a follower sits without re-deriving geometry.
    """
    key = str(orbit or "a")
    try:
        for echo in echo_rings(t):
            if str(echo.get("orbit") or "") == key:
                return _num(echo.get("tilt"), TILT_A if key == "a" else TILT_B)
    except Exception:  # noqa: BLE001 - a field fault falls back to the base tilt
        pass
    return TILT_A if key == "a" else TILT_B


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

    Sampled through the field's ``orbit_point`` so this ad-hoc circle (the beat
    shockwave) can never disagree with the declared set about where an ellipse
    is.
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
    (no sparkles, streaks, comet, shockwave or dust; nucleus at rest).
    """
    ovals = 0
    lines = 0
    points = 0
    texts = 0
    fills: List[Tuple[int, int, int]] = []
    min_y = float("inf")
    max_y = float("-inf")

    r = _num(radius, 32.0)
    if r <= 0:
        r = 32.0
    cx_f = _num(cx, 0.0)
    cy_f = _num(cy, 0.0)

    core = resolve_core(state, colors)
    fam = _ember.family_from_core(core)

    try:
        params = _ember.resolve_params(state)
        brightness = float(params.get("brightness", 1.0))
    except Exception:  # noqa: BLE001 - unknown state renders as idle
        brightness = 1.0
    bright = max(MIN_MARK_BRIGHTNESS, _num(brightness, 1.0))
    speed = max(0.2, min(3.0, _num(speed_scale, 1.0) or 1.0))
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
    t_val = _num(t, 0.0)
    tt = 0.0 if reduced_motion else t_val * speed

    rx = r * RX_FRAC
    ry = r * RY_FRAC
    fringe = FRINGE_PIXELS
    ring_w = 1 if r < 64 else 2
    ring_rgb = _mix(fam["core"], (255, 255, 255), LIFT_RING)
    nuc_rgb = _mix(fam["core"], (255, 255, 255), LIFT_NUCLEUS)

    # ------------------------------------------------------- the two spins
    # Master spin in the screen plane, outermost, then the Y+X tumble in the
    # mark's own frame — the Qt painter's composition (it rotates the painter
    # before drawing the tumbled points). The tumble's phase is resolved once
    # per frame: hundreds of points share it. This order is also what keeps
    # the mark open: where the tumble momentarily collapses one ring toward
    # a line in the mark's frame, the master spin has already turned that
    # line diagonal on screen, so the frame never thins to it.
    spin_a = math.radians(math.fmod(spin_deg(tt), 360.0))
    spin_c, spin_s = math.cos(spin_a), math.sin(spin_a)
    turn_ct, turn_st, turn_cp, turn_sp = turn_basis(tt)

    def _place(x: float, y: float, depth: Any = 0.5, zseed: Any = None,
               zspread: Any = TURN_Z_SPREAD, zflat: bool = False
               ) -> Tuple[float, float]:
        """A centre-relative FIELD point -> its screen position.

        The Y+X tumble at the element's own depth first, then the master
        spin — the same order and arithmetic as the Qt painter's per-point
        placement, so the two lanes put every element in the same place.
        ``zseed``/``zflat`` break the depth/x lock per element (see
        :func:`turn_qdepth`); ``None`` keeps the raw field depth.
        """
        try:
            d = turn_qdepth(depth, zseed, zspread, flat=zflat)
        except Exception:  # noqa: BLE001
            d = 0.5
        px, py, _g = turn_project(_num(x), _num(y), d, turn_ct, turn_st,
                                  turn_cp, turn_sp, r)
        sx = px * spin_c - py * spin_s
        sy = px * spin_s + py * spin_c
        return (cx_f + sx, cy_f + sy)

    def _place3(x: float, y: float,
                depth: Any = 0.5, zseed: Any = None,
                zspread: Any = TURN_Z_SPREAD, zflat: bool = False
                ) -> Tuple[float, float, float]:
        """Like :func:`_place`, plus the rotated-depth size/alpha gain."""
        try:
            d = turn_qdepth(depth, zseed, zspread, flat=zflat)
        except Exception:  # noqa: BLE001
            d = 0.5
        px, py, g = turn_project(_num(x), _num(y), d, turn_ct, turn_st,
                                 turn_cp, turn_sp, r)
        sx = px * spin_c - py * spin_s
        sy = px * spin_s + py * spin_c
        return (cx_f + sx, cy_f + sy, g)

    def _shade(alpha: Any, depth: Any = 0.5, gain: Any = 1.0) -> float:
        """Alpha after the turn's depth gain and the depth-of-field softening.

        ``gain`` is the element's own rotated-depth gain from :func:`_place3`
        (bigger and brighter as it swings toward the viewer); ``depth`` is its
        FIELD depth, which drives the focus term. ``bright`` is deliberately
        NOT folded in here: several callers already apply it and a second
        application would flatten the mark's brightness ladder.
        """
        a = _num(alpha, 0.0)
        d = max(0.0, min(1.0, _num(depth, 0.5)))
        try:
            a *= max(0.0, min(2.0, float(gain)))
        except (TypeError, ValueError):
            pass
        a *= 1.0 - DOF_ALPHA_SOFT * dof(d)
        return a

    # ------------------------------------------------------------- primitives
    def _line(pts: List[float], fill: str, width: int = 1) -> None:
        nonlocal lines, points, min_y, max_y
        try:
            canvas.create_line(*pts, fill=fill, width=max(1, int(width)),
                               smooth=True, capstyle="round", joinstyle="round")
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
        """Outline-only oval (Tk's soft-glow substitute: a dim hairline ring)."""
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

    def _dim(colour: Tuple[int, int, int], alpha: float,
             behind: Tuple[int, int, int] = _ember.NEAR_BLACK) -> str:
        """Tk alpha substitute: mix the colour toward whatever is BEHIND it.

        Against the background that is literally alpha compositing, and it is
        the right model everywhere else too: Tk has no alpha, so a translucent
        primitive has to be painted opaque at its pre-composited colour. Mixing
        a highlight toward the BACKGROUND when it actually sits on the opaque
        nucleus would paint a grey disc over the body instead of a lit spot, so
        an element on the body mixes toward the body's own colour.
        """
        a = 1.0 - max(0.0, min(1.0, float(alpha)))
        return _hex(_mix(colour, behind, a))

    def _lift(amount: Any) -> Tuple[int, int, int]:
        a = max(0.0, min(1.0, _num(amount, 0.0)))
        return (_clamp8(core[0] + (255 - core[0]) * a),
                _clamp8(core[1] + (255 - core[1]) * a),
                _clamp8(core[2] + (255 - core[2]) * a))

    # ---- the mark's spectrum ------------------------------------------------
    # The field owns the colour wheel. Hue is NEVER interpolated: it is
    # converted per sample and only the resulting RGB lands on the canvas, so a
    # streak whose hue runs 0.98 -> 0.02 takes the short way round the wheel.
    def _spec(hue: Any, sat: Any = 1.0, val: Any = 1.0) -> Tuple[int, int, int]:
        r, g, b = hue_to_rgb(hue, sat, val)
        return (_clamp8(r * 255.0), _clamp8(g * 255.0), _clamp8(b * 255.0))

    def _anchor(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Pull a spectral colour a little toward the mark's own core colour."""
        k = STREAK_HUE_ANCHOR_BLEND
        return tuple(_clamp8(c + (nuc_rgb[i] - c) * k)
                     for i, c in enumerate(rgb))

    # ---- the streak (one tapered wake per travelling body) ------------------
    def _streak(samples: Any, gain: float = 1.0,
                half: float = STREAK_HALF_WIDTH,
                zseed: Any = None) -> None:
        """One body's spectral wake, walked sample by sample.

        Owner 2026-10-18: a star should leave an RGB TRAIL, not sit inside an
        outer glow. The field's ``rgb_trail`` / ``dust_trail`` samples are
        stroked in order — one short line per consecutive pair, coloured through
        ``hue_to_rgb`` for that sample and tapered by the sample's own size and
        alpha, which Tk expresses as a mix toward the background.
        """
        smps = [s for s in (samples or []) if isinstance(s, dict)]
        if len(smps) < 2:
            return
        pts = [_place3(_num(s.get("x")), _num(s.get("y")),
                       _num(s.get("depth"), 0.5),
                       zseed=zseed, zflat=True) for s in smps]
        total = 0.0
        for i in range(1, len(pts)):
            total += math.dist(pts[i][:2], pts[i - 1][:2])
        if total < STREAK_MIN_LEN_PX:
            return
        for i in range(len(pts) - 1):
            here, nxt = smps[i], smps[i + 1]
            g = pts[i][2]
            a = _shade(_num(here.get("alpha")) * gain, here.get("depth"),
                       g)
            if a <= ALPHA_EPS:
                continue
            # Each segment takes the MEAN of its two endpoints' converted
            # colours — the same thing the Qt painter's gradient does between
            # two stops, and the only way every sample's hue reaches the frame
            # in a stroke-per-segment translation.
            colour = _anchor(tuple(
                (c0 + c1) / 2.0 for c0, c1 in zip(
                    _spec(here.get("hue"), here.get("sat", 1.0),
                          here.get("val", 1.0)),
                    _spec(nxt.get("hue"), nxt.get("sat", 1.0),
                          nxt.get("val", 1.0)))))
            w = (2.0 * max(STREAK_MIN_HALF_PX, _num(here.get("size")) * half)
                 * g)
            width = max(1, min(STREAK_MAX_WIDTH, int(round(w))))
            _line([pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]],
                  _dim(colour, a * bright), width)

    # ---- one twinkling star -------------------------------------------------
    def _glint(x: float, y: float, size: Any, alpha: Any,
               rgb: Tuple[int, int, int],
               arm_frac: float = GLINT_ARM_FRAC, skew_deg: Any = 0.0,
               depth: Any = 0.5, gain: Any = 1.0) -> None:
        """A twinkling star, never a filled orb (owner 2026-10-17) — and never
        a halo: the star's effect is the spectral streak it leaves.

        Two thin crossing arms plus a tiny core read as a glint at this size and
        stay crisp; a filled disc here was what made the field look like
        marbles. ``skew_deg`` is the field's per-depth shear: a star near the
        front of the orbital plane is raked one way, one near the back the other,
        and level at the rim, so it reads as a surface catching light from an
        angle instead of a flat cross pasted on the orbit. ``gain`` is the
        tumble's rotated-depth gain: a body swinging toward the viewer grows
        and brightens, one tucking away shrinks and dims.
        """
        try:
            g = max(0.0, min(2.0, float(gain)))
        except (TypeError, ValueError):
            g = 1.0
        sz = max(GLINT_MIN_SIZE, _num(size)) * g
        raw = _num(alpha)
        if raw <= FIELD_ALPHA_FLOOR or sz <= 0.0:
            return
        shown = _dim(rgb, _shade(raw, depth, g) * bright)
        arm = sz * arm_frac
        sk = math.radians(_num(skew_deg))
        ax, ay = math.cos(sk) * arm, math.sin(sk) * arm
        _line([x - ax, y - ay, x + ax, y + ay], shown, 1)
        _line([x - ay, y + ax, x + ay, y - ax], shown, 1)
        _oval(x, y, max(GLINT_CORE_MIN, sz * GLINT_CORE_FRAC * GLINT_CORE_SCALE),
              shown)
        fills.append(rgb)

    def _dot(x: float, y: float, rad: Any, alpha: Any,
              rgb: Tuple[int, int, int], depth: Any = 0.5,
              gain: Any = 1.0) -> None:
        """One filled dot — the dense field's mote, and the comet's head.

        Existence is decided by the FIELD's alpha (:data:`FIELD_ALPHA_FLOOR`);
        only the colour sees this renderer's tumble and depth shading. ``gain``
        is the tumble's rotated-depth gain: near bodies read bigger and
        brighter than far ones, which is the stronger near/far separation the
        hologram pass asks for.
        """
        try:
            g = max(0.0, min(2.0, float(gain)))
        except (TypeError, ValueError):
            g = 1.0
        raw = _num(alpha)
        r_ = max(GLINT_CORE_MIN, _num(rad)) * g
        if raw <= FIELD_ALPHA_FLOOR or r_ <= 0.0:
            return
        _oval(x, y, r_, _dim(rgb, _shade(raw, depth, g) * bright))

    # ---- one declared circle ----------------------------------------------
    def _ring_stroke(points: List[Tuple[float, float]], alpha: float,
                     width: float, layer: str) -> None:
        """Stroke one declared circle: fringe on the main orbits, nothing else.

        ``points`` are SCREEN positions — the caller projects each vertex at
        its own depth through the Y+X tumble first, so the circle bends with
        the turn instead of cutting a straight chord across the mark.
        """
        a = _shade(alpha)
        if a <= ALPHA_EPS or len(points) < 3:
            return
        flat: List[float] = []
        for sx, sy in points:
            flat += [sx, sy]
        w = max(1, int(round(width)))
        if layer == "main":
            # Chromatic fringe EXCLUSIVE to the two main orbits: it is the logo's
            # signature, and an echo / core ring / satellite shell wearing it
            # reads as four more logo orbits instead of the quiet circles they
            # are. Red to the right of the stroke, blue to the left, so the
            # overlap stays near-white and the edges split.
            _line([v + fringe if i % 2 == 0 else v for i, v in enumerate(flat)],
                  _dim(_FRINGE_RED, a * FRINGE_ALPHA_MULT), w)
            _line([v - fringe if i % 2 == 0 else v for i, v in enumerate(flat)],
                  _dim(_FRINGE_BLUE, a * FRINGE_ALPHA_MULT), w)
        _line(flat, _dim(ring_rgb, a), w)

    def _ring_pass(near: bool) -> None:
        """Stroke the declared circles belonging to the far or near half.

        The list is already back to front; the split is at the nucleus, so the
        far circles take part in the z-seam and the near ones paint over it.
        """
        for spec in specs:
            layer = str(spec.get("layer") or "")
            if near != (str(spec.get("orbit") or "") == "a"):
                continue
            # Echo followers and satellite shells are LIFE, not the mark: a
            # still frame is the main orbits, the core ring and the body.
            if reduced_motion and layer in ("echo", "satellite"):
                continue
            draw_spec = dict(spec)
            if spec.get("echo"):
                draw_spec["tilt"] = echo_follower_tilt(
                    tt, str(spec.get("orbit") or "a"))
            # ``ring_path`` carries the field's camera sway. The BODY does not
            # sway, so a circle drawn around it must not either: leaving the
            # sway in slides the mark's visual centre off its own core.
            raw = [(px - sway_dx, py - sway_dy)
                   for px, py in ring_path(tt, r, draw_spec)]
            # A circle is a STROKED PATH, not a set of field points, so each
            # vertex's depth comes from where it sits on the circle. The
            # field's convention is ``0.5 + 0.5*cos(orbital angle)`` — the same
            # one it gives every particle — and ``ring_path`` samples at
            # ``360*i/n``, so the orbital angle is recoverable EXACTLY from the
            # index. The closing vertex repeats vertex 0, and ``cos(360 deg)``
            # is ``cos(0)``, so the join matches too.
            n = max(1, len(raw) - 1)
            rphase = (2.0 * math.pi
                      * hash01(_num(spec.get("seed"), 0.0),
                              TURN_RING_SALT))
            pts = [_place(px, py,
                           min(1.0, max(0.0,
                               0.5 + TURN_RING_TILT * math.sin(
                                   2.0 * math.pi * float(i) / float(n)
                                   + rphase))))
                for i, (px, py) in enumerate(raw)]
            if layer == "main":
                scale = (RING_ALPHA_SCALE["near_main"] if near
                         else RING_ALPHA_SCALE["far_main"])
            else:
                scale = RING_ALPHA_SCALE.get(layer, 1.0)
            _ring_stroke(pts,
                         ring_alpha(tt, spec) * scale * bright,
                         ring_w * _num(spec.get("width"), 1.0)
                         * (1.15 if layer == "main" else 1.0),
                         layer)

    # ----------------------------------------------------------------- field
    # Every layer is one pure call; malformed input (already normalised) or a
    # future field change can never take the frame down.
    try:
        near_tilt, far_tilt = orbit_tilts(tt)
        sway_dx, sway_dy = camera_offset(tt, r)
    except Exception:  # noqa: BLE001
        near_tilt, far_tilt = TILT_A, TILT_B
        sway_dx, sway_dy = 0.0, 0.0
    try:
        specs = list(ring_geometry(tt))
    except Exception:  # noqa: BLE001 - a field fault costs circles, not the frame
        specs = []

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
        # Pinprick dots that materialise out of nowhere: fixed positions, only
        # the alpha envelope moves. LIFE, so a still frame carries none.
        dots = [] if reduced_motion else blink_dots(tt, r)
    except Exception:  # noqa: BLE001
        dots = []
    try:
        shells = [] if reduced_motion else satellites(tt, r)
    except Exception:  # noqa: BLE001
        shells = []
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
        # A clean static frame: no dust, no glints, no comet, no streaks, no
        # follower rings and no nucleus sparks.
        dust = []
        glints = []
        sparks = []
        comet_info = {"alpha": 0.0, "trail": []}

    far = [x for x in particles if _num(x.get("depth"), 0.5) < 0.5]
    near = [x for x in particles if _num(x.get("depth"), 0.5) >= 0.5]
    far_dust = [d for d in dust if _num(d.get("depth"), 0.5) < 0.5]
    near_dust = [d for d in dust if _num(d.get("depth"), 0.5) >= 0.5]
    far_glints = [g for g in glints if _num(g.get("depth"), 0.5) < 0.5]
    near_glints = [g for g in glints if _num(g.get("depth"), 0.5) >= 0.5]
    far_binaries = [b for b in binaries if _num(b.get("depth"), 0.5) < 0.5]
    near_binaries = [b for b in binaries if _num(b.get("depth"), 0.5) >= 0.5]
    far_shells = [s for s in shells
                  if _num((s.get("body") or {}).get("depth"), 0.5) < 0.5]
    near_shells = [s for s in shells
                   if _num((s.get("body") or {}).get("depth"), 0.5) >= 0.5]
    far_sparks = [s for s in sparks if _num(s.get("depth"), 0.5) < 0.5]
    near_sparks = [s for s in sparks if _num(s.get("depth"), 0.5) >= 0.5]
    far_dots = [d for d in dots if _num(d.get("depth"), 0.5) < 0.5]
    near_dots = [d for d in dots if _num(d.get("depth"), 0.5) >= 0.5]
    comet_on = (not reduced_motion) and _num(comet_info.get("alpha")) > 0.0

    # ------------------------------------------------------------- the bodies
    def _particle(part: Dict[str, Any]) -> None:
        """One logo particle: its spectral wake, then the star that left it."""
        depth = _num(part.get("depth"), 0.5)
        if not reduced_motion:
            _streak(rgb_trail(tt, r, part),
                    zseed=_num(part.get("index"), None))
        x, y, g = _place3(_num(part.get("x")), _num(part.get("y")),
                           depth, zseed=_num(part.get("index"), None),
                           zflat=True)
        _glint(x, y, part.get("size"), part.get("alpha"),
               _lift(_num(part.get("lift"), LIFT_BEAD)),
               skew_deg=part.get("skew"), depth=depth, gain=g)

    def _satellite(shell: Dict[str, Any]) -> None:
        """One satellite BODY riding its declared shell circle.

        The circle itself is stroked by the declared-geometry ring pass; this is
        only the traveller. It leaves the SHORT wake: a shell turns once in
        5.5-13.5 s and the long streak's step is sized for the comet, so the
        full one drew a near-straight scratch right across the mark. The
        field's own ``vx``/``vy`` put even the short one on the real path.
        """
        body = shell.get("body") or {}
        depth = _num(body.get("depth"), 0.5)
        if not reduced_motion:
            _streak(dust_trail(tt, r, body), gain=SATELLITE_STREAK_GAIN,
                    half=SHORT_STREAK_HALF_WIDTH,
                    zseed=_num(body.get("index"), None))
        x, y, g = _place3(_num(body.get("x")), _num(body.get("y")),
                           depth, zseed=_num(body.get("index"), None),
                           zflat=True)
        _glint(x, y, max(GLINT_MIN_SIZE, _num(body.get("size"))),
               body.get("alpha"),
               _lift(_num(body.get("lift"), LIFT_BEAD)),
               skew_deg=body.get("skew"), depth=depth, gain=g)

    def _spark(spark: Dict[str, Any]) -> None:
        """One nucleus spark: the same glint, thrown out past the orbits."""
        depth = _num(spark.get("depth"), 0.5)
        x, y, g = _place3(_num(spark.get("x")), _num(spark.get("y")),
                           depth, zseed=_num(spark.get("index"), None),
                           zflat=True)
        _glint(x, y, spark.get("size"), spark.get("alpha"),
               _lift(_num(spark.get("lift"), LIFT_NUCLEUS)),
               arm_frac=SPARK_ARM_FRAC,
               skew_deg=spark.get("skew"), depth=depth, gain=g)

    def _dust_mote(mote: Dict[str, Any]) -> None:
        """One dense mote: a plain dot, no wake.

        Dust motes (48 of them) each dragging a streak would turn the field
        into a wall of comets; the motes exist to keep the orbits from looking
        bare, and their depth read comes from size and alpha (plus the
        tumble's own rotated-depth gain now).
        """
        depth = _num(mote.get("depth"), 0.5)
        x, y, g = _place3(_num(mote.get("x")), _num(mote.get("y")),
                           depth, zseed=_num(mote.get("index"), None),
                           zflat=True)
        _dot(x, y, max(0.25, _num(mote.get("size"))), mote.get("alpha"),
             _lift(mote.get("lift")), depth=depth, gain=g)

    def _sparkle(glint: Dict[str, Any]) -> None:
        depth = _num(glint.get("depth"), 0.5)
        if not reduced_motion:
            _streak(dust_trail(tt, r, glint), gain=SPARKLE_STREAK_GAIN,
                    half=SHORT_STREAK_HALF_WIDTH,
                    zseed=_num(glint.get("index"), None))
        x, y, g = _place3(_num(glint.get("x")), _num(glint.get("y")),
                           depth, zseed=_num(glint.get("index"), None),
                           zflat=True)
        _dot(x, y, max(0.3, _num(glint.get("size"))), glint.get("alpha"),
             _lift(glint.get("lift")), depth=depth, gain=g)

    def _blink(dot: Dict[str, Any]) -> None:
        """One materialising dot: a pinprick oval, text-free, depth-split.

        Fixed field position, alpha-only envelope (0 -> peak -> 0), so each
        dot fades in from nothing and vanishes the same way instead of
        travelling. Drawn as an oval, never text: at orb size these are pins,
        not a wall of glyphs.
        """
        depth = _num(dot.get("depth"), 0.5)
        x, y, g = _place3(_num(dot.get("x")), _num(dot.get("y")),
                           depth, zseed=_num(dot.get("index"), None),
                           zflat=True)
        _dot(x, y, max(0.2, _num(dot.get("size"))), dot.get("alpha"),
             _lift(_num(dot.get("lift"), 0.5)), depth=depth, gain=g)

    def _binary(bit: Dict[str, Any]) -> None:
        """One "1"/"0" fragment as a small glyph at its field position."""
        nonlocal texts, min_y, max_y
        size = _num(bit.get("size"))
        depth = _num(bit.get("depth"), 0.5)
        if size <= 0.0 or _num(bit.get("alpha")) <= FIELD_ALPHA_FLOOR:
            return
        try:
            x, y, g = _place3(_num(bit.get("x")), _num(bit.get("y")),
                               depth,
                               zseed=_num(bit.get("index"), None),
                               zflat=True)
            alpha = _shade(bit.get("alpha"), depth, g) * bright
            if alpha <= ALPHA_EPS:
                return
            fs = max(BINARY_FONT_MIN_PX,
                     int(size * g * BINARY_FONT_PX_PER_UNIT
                         + BINARY_FONT_PX_OFFSET))
            canvas.create_text(x, y, text=str(bit.get("char") or "0"),
                               fill=_dim(_lift(_num(bit.get("lift"), 0.5)),
                                         alpha),
                               font=("TkDefaultFont", fs))
            texts += 1
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
        except Exception:
            pass

    def _comet_head() -> None:
        """The one fast traveller: a wake, its own debris tail, and a head.

        The field's bead tail rides the SAME hue ramp as the wake: left
        untinted it read as a second, grey tail beside a rainbow one. The
        beads walk the streak's duotone pair — head hue to ``hue2``, half a
        turn away — so the whole tail is exactly two colours, never a hue
        ramp of its own (the old ``HUE_STEP`` bead ramp is gone).
        """
        depth = _num(comet_info.get("depth"), 0.5)
        hue0 = _num(comet_info.get("hue"))
        # The drifted head hue and its half-turn partner: the pair the field's
        # own streak builder lerps between, read defensively so a hand-built
        # comet dict still yields a usable tail.
        head = (hue0 + tt * RGB_TRAIL_HUE_DRIFT) % 1.0
        try:
            tail_hue = float(comet_info.get("hue2"))
            if not 0.0 <= tail_hue <= 1.0:
                raise ValueError
        except (TypeError, ValueError):
            tail_hue = (head + 0.5) % 1.0
        if comet_on:
            _streak(rgb_trail(tt, r, comet_info), gain=COMET_STREAK_GAIN,
                    half=COMET_STREAK_HALF_WIDTH,
                    zseed=_num(comet_info.get("index"), 0.0))
            trail = list(comet_info.get("trail") or [])
            for k, smp in enumerate(trail):
                f = (k + 1.0) / (len(trail) + 1.0)
                hue = (head + f * 0.5) % 1.0
                x, y, g = _place3(_num(smp.get("x")), _num(smp.get("y")),
                                  depth,
                                  zseed=_num(comet_info.get("index"), 0.0),
                                  zflat=True)
                _dot(x, y, _num(smp.get("size")), smp.get("alpha"),
                     _anchor(_spec(hue, COMET_BEAD_SAT)), depth=depth,
                     gain=g)
        x, y, g = _place3(_num(comet_info.get("x")),
                          _num(comet_info.get("y")), depth,
                          zseed=_num(comet_info.get("index"), 0.0),
                          zflat=True)
        _dot(x, y, max(0.4, _num(comet_info.get("size"))),
             comet_info.get("alpha"),
             _anchor(_spec(hue0, COMET_BEAD_SAT)), depth=depth, gain=g)

    # ---- the studio nucleus (outside BOTH transforms: a sphere) ------------
    def _soft_oval(x: float, y: float, rad: float,
                   rgb: Tuple[int, int, int], peak: float, power: float,
                   layers: int, behind: Tuple[int, int, int] = _ember.NEAR_BLACK,
                   on_body: bool = False) -> None:
        """Concentric filled ovals approximating a radial gradient.

        Drawn largest-first, so the brightest (smallest) layer lands on top.
        ``on_body`` drops the outermost stop: its alpha is exactly zero, so it
        would paint ``behind`` flat OVER whatever it sits on and flat-erase a
        shaded body for nothing. Against the bare background the zero-alpha rim
        is free, and keeping it there is what gives the glow its soft edge
        without a hard disc.

        No other layer is ever skipped: an inconsistent count would make the
        primitive budget jitter from frame to frame.
        """
        rr = max(0.5, rad)
        for k in range(layers - 1 if on_body else layers, 0, -1):
            f = k / float(layers)
            _oval(x, y, rr * f, _dim(rgb, peak * ((1.0 - f) ** power), behind))

    def _nucleus(radius: float, rgb: Tuple[int, int, int], beat: float) -> None:
        """The mark's ONE solid body, shaded instead of a flat disc.

        A near-white hot core falls through the state hue to a fading rim, over
        a smoky aura and a chromatic corona, with an off-centre specular
        highlight. On top of the heartbeat breathing the body carries two
        counter-rotating swirl lobes, a caustic and an orbiting specular that
        ride the beat, and a flare that fires on the thump — so the nucleus
        reads as a living, radiating core rather than a static ball. Every term
        is a pure function of the clamped clock, so reduced motion paints the
        identical still body.
        """
        n_r = _num(radius)
        if n_r <= 0.0:
            return
        hot = _lift(NUCLEUS_CORE_LIFT)
        b = max(0.0, min(1.0, _num(beat)))

        # Soft halo: outline-only rings (never a disc), strongest on the beat so
        # the body reads as living even while the shading stays still.
        halo_a = min(1.0, (HALO_BASE + (1.0 - HALO_BASE) * b) * bright)
        halo_w = max(1, int(HALO_WIDTH))
        for hi in range(HALO_LAYERS):
            _ring_oval(cx_f, cy_f, n_r * HALO_SCALE * (1.0 + 0.24 * hi),
                       _dim(nuc_rgb, halo_a * (1.0 - 0.5 * hi)), halo_w)

        # Shaded body: concentric filled ovals fake the radial gradient Tk
        # cannot draw — a fading rim, the state hue, then a hot near-white core.
        rim_rgb = _mix(nuc_rgb, _ember.NEAR_BLACK, 0.55 * (1.0 - 0.3 * bright))
        for li in range(NUCLEUS_LAYERS):
            frac = li / max(1, NUCLEUS_LAYERS - 1)
            if frac <= 0.5:
                layer_rgb = _mix(rim_rgb, nuc_rgb, frac / 0.5)
            else:
                layer_rgb = _mix(nuc_rgb, hot, (frac - 0.5) / 0.5)
            _oval(cx_f, cy_f, n_r * (1.0 - 0.70 * frac), _hex(layer_rgb))

        # Hologram rim: a bright thin edge on the body plus a faint red/blue
        # split offset by a pixel — the lens-fringe cue. OUTLINES, so the edge
        # stays a hairline and never a disc; drawn glow-first-order-safe (all
        # glow was painted before the seam) and static, so the still frame
        # keeps it.
        _ring_oval(cx_f, cy_f, n_r,
                   _dim(hot, NUCLEUS_RIM_ALPHA * bright), 1)
        _ring_oval(cx_f + NUCLEUS_RIM_OFFSET_PX, cy_f, n_r,
                   _dim(_FRINGE_RED,
                        NUCLEUS_RIM_FRINGE_ALPHA * bright), 1)
        _ring_oval(cx_f - NUCLEUS_RIM_OFFSET_PX, cy_f, n_r,
                   _dim(_FRINGE_BLUE,
                        NUCLEUS_RIM_FRINGE_ALPHA * bright), 1)

        # Caustic: the boiling highlight inside the core, on its own slow turn
        # and louder on the thump, but never fully gone between beats.
        craw = math.radians(360.0 * tt / NUCLEUS_CAUSTIC_PERIOD_S)
        ca = (NUCLEUS_CAUSTIC_ALPHA_BASE
              + NUCLEUS_CAUSTIC_ALPHA * b * (0.5 + 0.5 * math.sin(2.0 * craw)))
        _soft_oval(cx_f + n_r * 0.16 * math.cos(craw),
                   cy_f + n_r * 0.16 * math.sin(craw),
                   max(0.6, n_r * NUCLEUS_CAUSTIC_RADIUS_FRAC), hot, ca,
                   1.6, NUCLEUS_CAUSTIC_LAYERS, rgb, on_body=True)

        # Inner swirl: two counter-rotating hot lobes inside the body, which
        # give the centre internal motion a flat gradient could never have.
        swirl_alpha = min(1.0, NUCLEUS_SWIRL_ALPHA
                          * (1.0 + NUCLEUS_SWIRL_BEAT_GAIN * b))
        for orbit_frac, size_frac, period, phase in (
                (NUCLEUS_SWIRL_RADIUS_FRAC, NUCLEUS_SWIRL_SIZE_FRAC,
                 NUCLEUS_SWIRL_PERIOD_S, 0.0),
                (NUCLEUS_SWIRL_RADIUS2_FRAC, NUCLEUS_SWIRL_SIZE2_FRAC,
                 NUCLEUS_SWIRL_PERIOD2_S, 180.0)):
            ang = math.radians(360.0 * tt / period + phase)
            _soft_oval(cx_f + n_r * orbit_frac * math.cos(ang),
                       cy_f + n_r * orbit_frac * math.sin(ang),
                       max(0.6, n_r * size_frac), hot, swirl_alpha, 1.6,
                       NUCLEUS_SWIRL_LAYERS, rgb, on_body=True)

        # Fixed off-centre specular highlight, then a small hot dot running once
        # around the rim: the cue that reads as rotation rather than pulsing.
        sr = max(0.6, n_r * NUCLEUS_SPEC_RADIUS_FRAC)
        _soft_oval(cx_f - n_r * NUCLEUS_SPEC_OFFSET_FRAC,
                   cy_f - n_r * NUCLEUS_SPEC_OFFSET_FRAC,
                   sr, (255, 255, 255), NUCLEUS_SPEC_ALPHA, 1.4,
                   NUCLEUS_SPEC_LAYERS, rgb, on_body=True)
        pang = math.radians(360.0 * tt / NUCLEUS_SPECULAR_PERIOD_S
                            + NUCLEUS_SPECULAR_PHASE_DEG)
        _oval(cx_f + n_r * NUCLEUS_SPECULAR_ORBIT_FRAC * math.cos(pang),
              cy_f + n_r * NUCLEUS_SPECULAR_ORBIT_FRAC * math.sin(pang),
              max(0.5, n_r * NUCLEUS_SPECULAR_SIZE_FRAC), "#ffffff")
        fills.append(nuc_rgb)

    def _nucleus_glow(n_r: float, hot: Tuple[int, int, int], b: float) -> None:
        """The aura, the smoke plumes and the chromatic corona.

        Drawn FIRST in the frame, before any hairline. Tk has no alpha
        compositing, so a translucent fill is painted OPAQUE at its
        background-mixed colour: painting it over the rings would erase them.
        Painting the soft glow first lets every hairline, particle and glyph sit
        on top of it and stay crisp — the one place where Tk's translucency
        substitute genuinely diverges from the Qt painter, and it reads better.
        """
        # Chromatic corona first, so the smoke and the body sit ON it and it
        # reads as light radiated by the core rather than a haze over it.
        for i in range(NUCLEUS_CORONA_COUNT):
            ang = math.radians(360.0 * tt / NUCLEUS_CORONA_PERIOD_S
                               + 360.0 * i / NUCLEUS_CORONA_COUNT
                               + NUCLEUS_CORONA_PHASE_DEG)
            breath = (1.0 - NUCLEUS_CORONA_BREATH_SPAN * 0.5
                      + NUCLEUS_CORONA_BREATH_SPAN * 0.5 * math.sin(
                          2.0 * math.pi * tt / NUCLEUS_CORONA_BREATH_S
                          + i * 1.7))
            dist = n_r * NUCLEUS_CORONA_RADIUS_FRAC * breath
            hue = (NUCLEUS_CORONA_HUE_BASE
                   + tt * NUCLEUS_CORONA_HUE_TURNS
                   + i * NUCLEUS_CORONA_HUE_SPAN
                   / float(NUCLEUS_CORONA_COUNT)) % 1.0
            _soft_oval(cx_f + dist * math.cos(ang),
                       cy_f + dist * math.sin(ang) * NUCLEUS_CORONA_SQUASH,
                       max(0.6, n_r * NUCLEUS_CORONA_SIZE_FRAC * breath),
                       _anchor(_spec(hue, NUCLEUS_CORONA_SAT)),
                       NUCLEUS_CORONA_ALPHA * breath, NUCLEUS_CORONA_POWER,
                       NUCLEUS_CORONA_LAYERS)
        # Smoke: slow drifting plumes that break the perfect circle.
        for orbit_frac, size_frac, period, phase, scale in NUCLEUS_SMOKE:
            ang = math.radians(360.0 * tt / period + phase)
            _soft_oval(cx_f + n_r * orbit_frac * math.cos(ang),
                       cy_f + n_r * orbit_frac * math.sin(ang) * 0.72,
                       n_r * NUCLEUS_GLOW_MULT * size_frac, hot,
                       NUCLEUS_GLOW_ALPHA * scale, NUCLEUS_AURA_POWER,
                       NUCLEUS_SMOKE_LAYERS)
        # Beat flare: a hot bloom that fires on the thump and decays with it,
        # with a small resting floor so the count never jumps between frames.
        _soft_oval(cx_f, cy_f,
                   max(0.6, n_r * NUCLEUS_FLARE_RADIUS_FRAC
                       * (0.45 + 0.75 * b)), hot,
                   NUCLEUS_FLARE_ALPHA * (NUCLEUS_FLARE_ALPHA_BASE
                                          + (1.0 - NUCLEUS_FLARE_ALPHA_BASE) * b),
                   1.8, NUCLEUS_FLARE_LAYERS)

    # ----------------------------------------------------- beat state (seam)
    if reduced_motion:
        beat = 0.0
    else:
        try:
            beat = heartbeat(tt)
        except Exception:  # noqa: BLE001
            beat = 0.0
    nuc_rad = nucleus_radius(r, tt, gain)
    hot_rgb = _mix(nuc_rgb, (255, 255, 255), NUCLEUS_CORE_LIFT)
    _nucleus_glow(nuc_rad, hot_rgb, beat)

    # ------------------------------------------------------------- far field
    # Behind the nucleus: far circles, far dust, far particles + streaks, far
    # glints, far binaries, far sparks, then the comet's tail.
    _ring_pass(False)
    for _mote in far_dust:
        _dust_mote(_mote)
    for _p in far:
        _particle(_p)
    for _g in far_glints:
        _sparkle(_g)
    for _d in far_dots:
        _blink(_d)
    for _b in far_binaries:
        _binary(_b)
    for _s in far_sparks:
        _spark(_s)
    for _shell in far_shells:
        _satellite(_shell)
    if comet_on and _num(comet_info.get("depth"), 1.0) < 0.5:
        _comet_head()

    # --------------------------------------------------------- beat shockwave
    # Outline-only tilted ellipse at nucleus_radius*scale, strongest on the
    # beat; no filled disc (Tk has no alpha to fake one convincingly).
    if not reduced_motion:
        try:
            sw_scale, sw_alpha = shockwave(tt)
        except Exception:  # noqa: BLE001
            sw_scale, sw_alpha = 0.0, 0.0
        if sw_scale > 0.0 and sw_alpha > 0.0:
            sw_r = max(1.1, r * NUCLEUS_FRAC) * sw_scale
            # Sampled about the ORIGIN (centre-relative, like every other
            # field point) and projected per vertex at its own depth, so the
            # ring turns with the mark instead of cutting across it.
            sw_pts = _ellipse_points(0.0, 0.0, sw_r,
                                     sw_r * (RY_FRAC / RX_FRAC), near_tilt)
            flat = []
            for i in range(0, len(sw_pts), 2):
                k = (i // 2) % RING_SEGMENTS
                d = 0.5 + 0.5 * math.cos(2.0 * math.pi * k / RING_SEGMENTS)
                sx, sy = _place(sw_pts[i], sw_pts[i + 1], d)
                flat += [sx, sy]
            _line(flat, _dim(ring_rgb, _shade(sw_alpha * bright, 0.5)),
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
                ccx, ccy = cx_f + sway_dx, cy_f + sway_dy
                rects = _ember.spec_bar_rects(bands, caps, ccx, ccy, r, 1.0)
                for ri, rc in enumerate(rects):
                    base = _ember.band_shade(fam_bands, ri, tt)
                    top = rc["top"]
                    bottom = rc["bottom"]
                    span = bottom - top
                    steps = 1 if span < 2.0 else _ember.BAR_GRADIENT_STEPS
                    # The seam rides the tumble at neutral depth, like any
                    # other depth-free element; the bars stay vertical (a UI
                    # overlay at the seam, not field geometry).
                    bx, _, bg = _place3(rc["x"] - cx_f, 0.0, 0.5)
                    for si in range(steps):
                        f0 = si / steps
                        f1 = (si + 1) / steps
                        y_hi = top + span * f0
                        y_lo = top + span * f1
                        shade = 0.55 + _ember.BAR_TIP_GAIN * (1.0 - f0)
                        alpha = min(1.0, (0.24 + 0.66 * rc["value"])
                                    * shade * bright)
                        _line([bx, y_hi, bx, y_lo],
                              _hex(_mix(base, _ember.NEAR_BLACK, alpha)), 2)
                    hw = rc["half_w"] * max(0.35, min(1.6, bg))
                    cap_fill = _hex(_mix(base, _ember.NEAR_BLACK,
                                         min(1.0, 0.35 + 0.65 * bright)))
                    _line([bx - hw, rc["peak_top"], bx + hw, rc["peak_top"]],
                          cap_fill, 1)
                bars_on = True
        except Exception:  # noqa: BLE001 - a bad spectrum never breaks the frame
            bars_on = False

    # ------------------------------------------------------- seam: the nucleus
    _nucleus(nuc_rad, nuc_rgb, beat)

    # ------------------------------------------------------------ near field
    _ring_pass(True)
    for _mote in near_dust:
        _dust_mote(_mote)
    for _p in near:
        _particle(_p)
    for _g in near_glints:
        _sparkle(_g)
    for _d in near_dots:
        _blink(_d)
    for _b in near_binaries:
        _binary(_b)
    for _s in near_sparks:
        _spark(_s)
    for _shell in near_shells:
        _satellite(_shell)
    if comet_on and _num(comet_info.get("depth"), 0.0) >= 0.5:
        _comet_head()

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
    "Y_SPIN_PERIOD_S", "X_SPIN_PERIOD_S", "Y_SPIN_Z_GAIN", "Y_SPIN_PERSP",
    "Y_SPIN_PERSP_MIN", "Y_SPIN_PERSP_MAX", "Y_SPIN_DEPTH_GAIN",
    "Y_SPIN_GAIN_MIN", "Y_SPIN_GAIN_MAX",
    "horizontal_spin_deg", "horizontal_spin",
    "turn_basis", "turn_project", "turn_point",
    "DOF_ALPHA_SOFT",
    "NUCLEUS_RIM_ALPHA", "NUCLEUS_RIM_OFFSET_PX", "NUCLEUS_RIM_FRINGE_ALPHA",
    "NUCLEUS_RIM_OVALS",
    "STREAK_HALF_WIDTH", "STREAK_MIN_LEN_PX", "ALPHA_EPS",
    "echo_follower_tilt", "nucleus_radius", "NUCLEUS_RADIUS_MULT",
    "RX_FRAC", "RY_FRAC", "TILT_A", "TILT_B", "NUCLEUS_FRAC",
    "FRINGE_PX", "FRINGE_PX_AT_96", "FRINGE_PIXELS", "LIFT_RING", "LIFT_BEAD",
    "LIFT_NUCLEUS", "MIN_MARK_BRIGHTNESS", "HALO_SCALE", "HALO_BASE",
    "HALO_WIDTH", "SHOCKWAVE_WIDTH", "RING_SWAY_DEG", "RING_PERIOD_NEAR",
    "RING_PERIOD_FAR",
    "heartbeat", "nucleus_scale", "shockwave", "camera_offset",
    "orbit_tilts", "orbit_point", "particle_state", "trails",
    "dust_state", "comet", "sparkles",
    "echo_rings", "satellites", "ECHO_LAG_S", "ECHO_SCALE", "ECHO_ALPHA", "ECHO_WIDTH",
    "nucleus_sparks", "NUCLEUS_SPARK_COUNT", "NUCLEUS_SPARK_REACH",
    "inner_ring_tilt", "binary_state", "INNER_RING_RX_FRAC", "BINARY_COUNT",
    "blink_dots", "BLINK_COUNT", "spin_angles",
    "hue_to_rgb", "rgb_trail", "dust_trail", "ring_geometry", "ring_path",
    "ring_alpha", "dof", "skew",
]