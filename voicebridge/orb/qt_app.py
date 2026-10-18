"""Qt voice orb: borderless always-on-top waveform window (X11).

Same contract as the Tk view (``voicebridge.orb.app``), only the view
layer differs: per-pixel alpha (``WA_TranslucentBackground``) so the
window has no opaque square, and ``QPainter`` radial gradients + real
alpha blending so the ember renderer glows instead of dithering.

Untouched and reused verbatim: state feed/IPC (``client``), state
resolution (``state``), geometry/drag math (``geometry``), position
persist (``persist``), fullscreen rule (``fullscreen``), frame/poll
cadence, reduced-motion, focus discipline (no focus-forcing or grab
calls anywhere; ``WindowDoesNotAcceptFocus`` +
``WA_ShowWithoutActivating``; showing uses ``show`` + ``raise_`` only,
which restacks without moving keyboard focus on X11), and the
in-game override. The Tk code stays as the fallback
(``QUBIT_ORB_BACKEND=tk``).

``PySide6`` is imported lazily so this module imports cleanly with no
display and without Qt installed (hermetic tests, ``--once`` proof
mode): pure helpers (``zone_at``, ``band_pen``, ``core_stops``,
``ring_pen``, ``backend_wanted``) need neither.
"""
from __future__ import annotations

import math
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import client as _client
from . import activity as _activity
from . import ember as _ember
from . import mark as _mark
from . import mark_field as _field
from . import fullscreen as _fs
from . import geometry as _geo
from . import monitors as _mon
from . import options as _options
from . import persist as _persist
from . import state as _state
from . import stream as _stream
from . import talk_state as _talk_state
from .app import LevelSmoother as _LevelSmoother

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
    from PySide6.QtWidgets import QApplication, QWidget
    _QT_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # headless / Qt-less: pure helpers still work
    Qt = None  # type: ignore[assignment]
    QTimer = None  # type: ignore[assignment]
    QColor = QPainter = QPainterPath = None  # type: ignore[assignment]
    QPen = QRadialGradient = QApplication = QWidget = None  # type: ignore[assignment]
    _QT_IMPORT_ERROR = f"{type(exc).__name__}"

#: Same cadence as the Tk view (single source of truth lives here too;
#: values match ``app.FRAME_MS``/``app.POLL_MS`` and are pinned by test).
FRAME_MS = _ember.FRAME_MS
POLL_MS = 500
NOTICE_S = 5.0

#: Collapsed dot fill (same contract as the Tk view).
TEAL = (103, 230, 197)
GREY = (139, 148, 158)
AMBER = (227, 179, 65)

_QT_BACKEND_ENV = "QUBIT_ORB_BACKEND"

#: -- mark life constants (the Tk sibling mirrors these exact numbers) ------
#: Nucleus heartbeat: a "lub-dub" double thump. One primary beat per period,
#: a weaker second thump a fraction of the way in, each a smooth ~0.16s pulse.
HEART_PERIOD_S = 0.95
HEART_SECOND_PHASE = 0.22
HEART_SECOND_AMP = 0.62
HEART_THUMP_S = 0.16
HEART_SCALE_AMP = 0.18
HALO_RADIUS_MULT = 1.6
HALO_ALPHA_MAX = 0.22
#: Depth: `depth = 0.5 + 0.5*cos(angle)` maps orbital angle to nearness
#: (1 = nearest). Radius/alpha/lift all ride it; parallax nudges the far
#: side inward along the orbit normal.
DEPTH_RADIUS_MIN = 0.55
DEPTH_RADIUS_GAIN = 0.75
DEPTH_ALPHA_MIN = 0.35
DEPTH_ALPHA_GAIN = 0.65
DEPTH_LIFT_MIN = 0.05
DEPTH_LIFT_GAIN = 0.30
DEPTH_PARALLAX = 0.06
#: Particle trails: short fading arc behind each bead's motion.
TRAIL_SAMPLES = 4
TRAIL_STEP_NEAR_DEG = 6.0
TRAIL_STEP_FAR_DEG = 4.0
TRAIL_ALPHA_BASE = 0.28
TRAIL_RADIUS_MULT = 0.55
#: Deterministic sparkles (no `random`; all derived from t and the index).
SPARKLE_COUNT = 14
SPARKLE_SEED_STEP = 97.3
SPARKLE_R_MIN_FRAC = 0.30
SPARKLE_R_SPAN_FRAC = 0.75
SPARKLE_LIFE_BASE = 1.1
SPARKLE_LIFE_STEP = 0.35
SPARKLE_VISIBLE_FRAC = 0.45
SPARKLE_MAX_ALPHA = 0.55
SPARKLE_SIZE_BASE = 0.5
SPARKLE_SIZE_PEAK = 0.9
SPARKLE_LIFT = 0.55
#: Nucleus sparks are drawn at a fraction of their field size: many young
#: sparks crowd the nucleus at once, so full-size crossing arms would mat
#: into a mesh instead of reading as a shower of tiny glints.
#: Sparks are already pinpoint-sized in the field; a glint's arms are
#: sized by SPARK_ARM_FRAC (shorter than a particle's) so they read as
#: pinpricks, never as stars the size of the nucleus.
NUCLEUS_SPARK_DRAW_SCALE = 1.0
#: Glints, painter-side refinement only (the shared field keeps its shape):
#: a lower size floor, a sub-pixel pen and a smaller core turn the shared
#: crossing arms into a pinprick instead of a fat cross. A star has NO halo
#: (owner 2026-10-18: "the stars still have old aura we need them to leave a
#: RGB trail not an outer glow"); it leaves a SPECTRAL STREAK instead, built by
#: :func:`_paint_mark` from the field's ``rgb_trail`` / ``dust_trail`` samples.
GLINT_MIN_SIZE = 0.16
GLINT_PEN_W = 0.8
GLINT_CORE_SCALE = 0.58
GLINT_CORE_MIN = 0.08
# --- RGB spectral streaks (one tapered fill path per body) -----------------
#: A streak's half-width is the body's field ``size`` times this, floored so a
#: far/small body still antialiases to a visible hairline instead of snapping
#: out of existence between frames.
TRAIL_HALF_WIDTH = 0.30
TRAIL_MIN_HALF_PX = 0.34
#: Below this total arc length a streak is not drawn at all: a sub-pixel
#: ribbon is one 8-bit step from appearing, which is exactly the hard pop the
#: field's C1 envelopes exist to avoid.
TRAIL_MIN_LEN_PX = 0.75
#: Owner 2026-10-18: "the curved lines what are those? ... refine even more".
#: The full field ``rgb_trail``/``dust_trail`` wake is a chain of samples
#: following the body's ORBITAL velocity, so it curves along the orbit and,
#: drawn at full length, read as an unexplained coloured curved line cutting
#: across the mark. The RGB-trail IDEA stays — a short local motion blur behind
#: a moving body — but only the brightest head samples are drawn, so a wake can
#: never span the mark. This is a hard ceiling on the sample chain, applied to
#: EVERY caller, so the field may keep publishing its full trail.
TRAIL_DRAW_SAMPLES = 3
#: Sparkles carry the short ``dust_trail``: four samples and a thinner ribbon,
#: so 26 of them gain spectrum without turning the field into 26 comets.
SPARKLE_TRAIL_HALF_WIDTH = 0.34
SPARKLE_TRAIL_GAIN = 1.0
#: A shell body is dimmer than a logo particle, so its streak starts dimmer too.
SATELLITE_TRAIL_GAIN = 1.1
#: The comet is the one body loud enough to earn the full streak.
COMET_TRAIL_HALF_WIDTH = 0.36
COMET_TRAIL_GAIN = 1.0
#: Its field bead tail stays, tinted along the same hue ramp: bright
#: motion-blur fragments sitting inside the streak read as meteor debris,
#: where an untinted grey tail next to a rainbow wake read as two tails.
COMET_BEAD_SAT = 0.80
#: Blink dots are pinpricks: no wake, no halo, just a dot in the dot's own
#: spectral hue, floored so a far/small one still antialiases instead of
#: snapping out between frames.
BLINK_DOT_SAT = 0.80
BLINK_DOT_MIN_PX = 0.30
# --- the new rich layers: batched draws ------------------------------------
#: The field now returns many more bodies (ring particles on EVERY circle, a
#: cylinder column, a rarer flare family, a travelling filament, plus the
#: existing dense sparkle cloud). They are all real layers, but the primitive
#: ceiling is fixed, so high-count dot layers are emitted as a few SPECTRAL
#: BUCKETS — each bucket is ONE fill path holding every body in it — and the
#: trailing layer is stroked as one path. More life, flat cost. ``*_DRAW_LIMIT``
#: bounds the bodies considered so the per-frame CPU stays flat too.
RING_PARTICLE_DRAW_LIMIT = 22
CYLINDER_DRAW_LIMIT = 32
ORBITER_DRAW_LIMIT = 30
FLARE_DRAW_LIMIT = 8
FILAMENT_DRAW_COUNT = 10
RING_PARTICLE_BUCKETS = 3
CYLINDER_BUCKETS = 2
ORBITER_BUCKETS = 2
FLARE_BUCKETS = 2
SPARKLE_BUCKETS = 4
#: The cylinder column is shaded by height: a particle at the top/bottom of the
#: column is dimmer than one at the nucleus's equator, so the column reads as a
#: volume with a falloff instead of a flat scatter.
CYLINDER_HEIGHT_DIM = 0.45
#: A streak is pulled this far toward the state's lifted nucleus colour. Full
#: saturation is the point, but an untinted rainbow in a mark that changes
#: colour with the state would read as a different product.
TRAIL_HUE_ANCHOR_BLEND = 0.10
# --- horizontal (Y-axis) 360 turn, layered on the master spin -------------
#: Owner: "add one more spinning animation from left to right horizontal spin
#: that spins 360". A second, INDEPENDENT full turn of the whole mark about a
#: VERTICAL axis through its centre, mirroring past 90 degrees. Period is
#: deliberately non-harmonic with the 24 s master spin and the 9.5/13.5 s ring
#: turns, so the two never visibly lock. Purely additive: the master spin is
#: untouched. ``mark_horizontal_theta`` reads the CLAMPED clock, so under
#: reduced motion the whole turn is an exact identity — see
#: :func:`y_turn_point`.
#: The period is deliberately non-harmonic with the 24 s master spin AND both
#: ring turns. It is 20.5 s, not the "about 19" first considered, because 19 is
#: exactly TWICE the 9.5 s near-ring period: the mark would then fall into the
#: same relative configuration every half turn and the two motions would read
#: as one locked pattern rather than as two independent lives.
Y_SPIN_PERIOD_S = 20.5
#: How deep the mark's depth axis runs, as a fraction of its radius. Every
#: element already carries the field's ``depth`` (0.0 far, 1.0 near), and this
#: is how far that depth is allowed to push an element along z. It replaces the
#: old FLAT squash, which projected a two-dimensional mark and therefore went
#: edge-on to a 1px line twice per turn — the owner's own words: "i dont what
#: that thin". With a real z, the near and far halves SEPARATE across x
#: instead of piling onto one line, so the mark opens up at exactly the moments
#: the flat version used to disappear.
Y_SPIN_Z_GAIN = 1.25
#: Perspective: how much an element's ROTATED depth (``zr``) scales it, as a
#: fraction of the mark radius. ``1 + Y_SPIN_PERSP * zr / R``. Clamped either
#: side, so a field value that ever runs away from its nominal depth range
#: cannot blow a near element up to fill the orb or shrink a far one out of
#: existence.
Y_SPIN_PERSP = 0.35
Y_SPIN_PERSP_MIN = 0.55
Y_SPIN_PERSP_MAX = 1.70
#: Size and alpha gain on the same rotated depth: a body swinging TOWARD the
#: viewer gets bigger and brighter, one tucking away gets smaller and dimmer.
#: This is what turns the turn(s) from a wobble into a rotation — the depth is
#: visible in each element, not only in the mark's outline. Raised for the X+Y
#: tumble (owner: hologram, not flat): the back half must unmistakably recede,
#: so the spread between depth 0 and depth 1 is wide on purpose. Still exactly
#: 1.0 at face-on (the rotated depth is 0 there whatever the gain), so the
#: still frame and the t=0 frame are byte-identical to before.
Y_SPIN_DEPTH_GAIN = 0.85
Y_SPIN_GAIN_MIN = 0.30
Y_SPIN_GAIN_MAX = 2.20
#: Uncorrelated projection depth (the "thin line" fix). The field's ``depth``
#: is ``0.5 + 0.5*cos(orbit angle)`` while an orbit's x is ``~rx*cos(angle)``,
#: so depth is a LINEAR function of x: every point satisfies ``z ~= c*x`` and
#: the whole "3D" mark is secretly one flat plane, which any rotation still
#: takes thin twice per turn. ``turn_qdepth`` breaks that lock by adding a
#: per-element static component from ``hash01`` of an identity seed (element
#: index, trail position, ring vertex): constant per element, uncorrelated
#: with x, so the mark is a genuine volume and opens instead of collapsing.
#: The fade still zeroes z at face-on phases, so still frames are untouched.
TURN_Z_SALT = 17.31
TURN_Z_SPREAD = 0.9
#: Ring vertices get a smooth out-of-plane tilt (same mechanism, phased
#: sine lobe): a declared circle must stay a clean ellipse face-on, so the
#: warp is a single low-frequency lobe per ring — a genuine 3D plane tilt,
#: never per-vertex noise (independent hash per vertex turned every orbit
#: into a lightning-bolt zigzag at edge-on phases). The lobe phase comes
#: from the ring's own seed, so no two circles share a plane and the set
#: never collapses onto one diagonal together.
TURN_RING_WARP = 0.14
TURN_RING_TILT = 0.35
TURN_RING_SALT = 5.77
#: The second half of the "more 3d" fix: the field now gives EVERY element two
#: independent volume axes, ``z`` and ``z2`` (uncorrelated ``hash01`` of the
#: element index, distinct salts). ``turn_qdepth`` still drives the projection
#: depth (the x/z rotation), but a single depth only makes the mark one SHEET
#: with a thickness. These two axes give each element a genuine 3D offset along
#: two ORTHOGONAL screen directions, so the cloud occupies a volume instead of
#: riding one plane. The offset is driven by the hash axes — NEVER by the
#: orbital angle — so it cannot reintroduce the depth/x lock that made the mark
#: go thin, and it is faded by the same combined-turn factor the projection
#: uses, so face-on and reduced-motion frames stay the unturned mark.
#: Raised 2026-10-19 (owner: "enhance 3d animation overall"): the field now
#: feeds the same axes to many more layers (volume particles, accretion,
#: infall), so a slightly wider displacement makes the interior read as a
#: volume instead of a thin sheet. Still faded by ``turn_fade``, so face-on and
#: reduced-motion frames are byte-identical.
VOLUME_X_FRAC = 0.17
VOLUME_Y_FRAC = 0.17
#: Brightness follows the same rotated depth: a body's tint is scaled by
#: ``TURN_TONE_BASE + TURN_TONE_SLOPE * gain``, so near bodies render hotter
#: and far ones cooler on top of the size/alpha spread. At gain 1 the factor
#: is exactly 1.0, so face-on and reduced-motion frames are untouched.
TURN_TONE_BASE = 0.72
TURN_TONE_SLOPE = 0.28
#: The X-axis tumble: the second half of the combined 3D turn, one full 360
#: deg rotation about a HORIZONTAL axis through the mark's centre per period,
#: applied AFTER the Y rotation (rotation order Y first, then X — the same
#: order the Tk lane bakes, so both lanes tumble identically). Single source:
#: ``mark_field.spin_angles`` owns both phases; the painter only consumes
#: them. 27.0 s is non-harmonic with the 20.5 s Y turn (and with the 9.5/13.5
#: s ring turns and the 24 s master spin), so the two axes never visibly lock
#: — the brief's assumed 19 s Y-spin was exactly 2x the 9.5 s near-ring period
#: and would have locked. Identity at t=0 and under reduced motion.
X_SPIN_PERIOD_S = 27.0
#: Nucleus hologram: a thin BRIGHT limb stroke at the body edge (the Fresnel
#: rim — a sphere is brightest at grazing incidence) plus a faint chromatic
#: split (two offset hairline rims, red/blue, very low alpha). Static art
#: direction, so reduced-motion stills stay self-consistent. Physical light
#: cues only: no scanlines, no flicker.
NUCLEUS_RIM_ALPHA = 0.55
NUCLEUS_RIM_WIDTH = 1.1
NUCLEUS_CHROMA_ALPHA = 0.10
#: The nucleus is exempt from the tumble squash (a sphere's silhouette is a
#: circle from every angle) but it still rides the turn: a small uniform
#: scale breathing with the combined phase, exactly 1.0 at t=0 and under
#: reduced motion so the still frame is untouched.
NUCLEUS_TUMBLE_SCALE = 0.02
#: Depth of field: how much a far element's alpha is pulled down. ``dof`` is
#: 0.0 sharp / 1.0 far, so this softens distance WITHOUT touching brightness
#: meaning — "far away" must not collapse into "barely visible".
#: DELIBERATELY NOT RAISED for the 3D turn. A value here touches every
#: primitive's alpha, including on a still frame, and the reduced-motion still
#: frame is required to be the unturned mark byte for byte. The far half now
#: reads as far because it is genuinely SMALLER and DIMMER (the rotated-depth
#: gain and the field's own depth size/alpha), not because it was softened
#: harder.
DOF_ALPHA_SOFT = 0.22
#: How many points a DECLARED circle is sampled at once it is warped. A warped
#: circle does cover a longer, more curved path on screen (the near arc bulges,
#: the far arc recedes), so a finer sample was tried here: 96 and 160 both
#: rendered identically to the field's own 72 at 900 px, so this stays at the
#: field's count. Two reasons not to raise it: it is pure cost (~1 ms/frame at
#: 96), and — decisively — a denser path CHANGES the still frame, because a
#: different chord approximation antialiases differently. The reduced-motion
#: frame is required to be the unturned mark byte for byte.
RING_WARP_SAMPLES = _field.RING_PATH_SAMPLES
#: Nucleus: a shaded sphere rather than a flat disc. A near-white hot core
#: falls through the state hue to a transparent rim, over a wide low-alpha
#: glow, with a small off-centre specular highlight. The body is the mark's
#: ONE solid ellipse; glow and highlight are gradient paths beside it. All of
#: it is static, so reduced motion is a valid still frame.
NUCLEUS_CORE_LIFT = 0.78
NUCLEUS_GLOW_MULT = 3.15
NUCLEUS_GLOW_ALPHA = 0.135
#: The aura is SMOKE, not a disc: a long power falloff sampled at many stops so
#: there is no alpha plateau and no visible rim, plus a few slow drifting
#: plumes that break the perfect circle. A 3-stop gradient at this radius read
#: as a flat grey disc — the plateau beside the opaque body was the hard edge.
NUCLEUS_AURA_POWER = 3.9
NUCLEUS_AURA_STOPS = 24
#: (orbit_frac, size_frac, period_s, phase_deg, alpha_scale) per smoke plume.
NUCLEUS_SMOKE = (
    (0.34, 0.92, 17.0, 0.0, 0.55),
    (0.52, 0.74, 23.0, 137.0, 0.40),
    (0.24, 0.58, 29.0, 251.0, 0.32),
)
NUCLEUS_SPEC_OFFSET_FRAC = 0.38
NUCLEUS_SPEC_RADIUS_FRAC = 0.36
NUCLEUS_SPEC_ALPHA = 0.90
#: Ring chromatic fringe: a faint, softened split, never a second line, so the
#: crisp main hairline stays the dominant stroke instead of a rainbow outline.
FRINGE_WIDTH_MULT = 1.6
FRINGE_ALPHA_MULT = 0.09
#: Master spin: a slow 360 deg rotation of the WHOLE mark about its centre,
#: layered on top of every other motion (the Tk sibling's
#: ``mark.MASTER_SPIN_PERIOD_S``). One full turn per period, so nothing in the
#: field snaps: the mark keeps its own life and the whole assembly turns under
#: it. A NO-OP under reduced motion (``mark_spin_deg`` reads the clamped clock).
MASTER_SPIN_PERIOD_S = 24.0
#: Studio nucleus: the shaded body is drawn 1.25x the field's base radius. The
#: field still owns the heartbeat breathing; this only scales the body.
NUCLEUS_RADIUS_MULT = 1.25
#: Inner core ring: a third, THIN orbit around the nucleus — depth-neutral
#: (drawn once, before the far pass), dim, and never chromatically fringed so
#: it reads as a core path instead of a third logo orbit.
INNER_RING_ALPHA = 0.16
# --- one declared geometry, four rings' worth of art direction --------------
#: ``mark_field.ring_geometry`` now declares EVERY circle of the mark once
#: (two main orbits, their echo followers, the inner core ring and the three
#: satellite shells) with its radius, layer, width and order. The painter
#: consumes that list and applies nothing but its own look on top, so the
#: circles cannot drift from the declaration. These scales only translate a
#: declared alpha into the mark's established brightness ladder.
RING_ALPHA_SCALE = {
    # The two main orbits straddle the nucleus: the far one is drawn dimmer
    # behind it and the near one brighter in front, which is how the two
    # complete ellipses read as one tilted plane.
    "far_main": 0.60 / _field.RING_ALPHA_MAIN,
    "near_main": 0.95 / _field.RING_ALPHA_MAIN,
    # An echo follower breathes on top of the shared value, and the painter's
    # depth-of-field softening pulls it down again. The scale puts the whole
    # breathing band back above the alpha at which a follower still reads as a
    # follower rather than dissolving, and far below the main orbits' ladder.
    "echo": 1.35,
    # The core ring is the quietest circle in the mark, and quieter than the
    # echo followers: it is a core path, not another logo orbit.
    "inner": INNER_RING_ALPHA / _field.RING_ALPHA_INNER,
    "satellite": 1.0,
}
#: Binary particles: tiny "1"/"0" data fragments. The field's ``size`` is a
#: radius-like unit that already scales with the orb, so the glyph is sized
#: from it and floored: at orb size these are PINS, not a wall of text. (The Tk
#: lane's much larger multiplier turned the same field into legible words.)
BINARY_FONT_MIN_PX = 5
BINARY_FONT_PX_PER_UNIT = 12.0
BINARY_FONT_PX_OFFSET = 2.0
# --- volume particles: the math-glyph TEXT layer's replacement -------------
#: Owner 2026-10-19: the ``√``/``Δ``/``∇`` math glyphs read as "check marks / v
#: lines" and are gone. The field now publishes ``volume_particles``: real 3D
#: bodies with their own inclined orbits, two independent volume axes (``z`` /
#: ``z2``), a per-body pulse and spin. They are drawn as round DOTS (never text,
#: never arms that could read as a check mark), sized/alpha'd by their own
#: depth. ``*_DRAW_LIMIT`` keeps the pass flat: the field publishes 52 bodies
#: and the painter draws an even subset of them.
VOLUME_DRAW_LIMIT = 18
#: Restrained saturation for the new dot families: the FIELD's own matte band,
#: never the old near-neon values, so the palette stays professional.
VOLUME_PARTICLE_SAT = _field.RGB_TRAIL_SAT_BASE
PHOTON_RING_SAT = _field.RGB_TRAIL_SAT_BASE
#: Accretion stream / infall streaks are dense dot families, batched by hue
#: like the ring and orbit bodies so their primitive cost is a few paths.
ACCRETION_DRAW_LIMIT = 28
ACCRETION_BUCKETS = 3
INFALL_DRAW_LIMIT = 14
INFALL_BUCKETS = 3
#: Photon ring: the lensed arcs hug the core. Only the BRIGHT samples are
#: drawn (its lobe profile is the point), capped so the arcs stay cheap.
PHOTON_DRAW_LIMIT = 16
PHOTON_MIN_ALPHA = 0.10
#: The photon ring is a lensed overlay over the core: drawn there, its samples
#: are enlarged and brightened in the painter, because the field's own sizes
#: are calibrated for the field scale and disappear into the aura at orb size.
PHOTON_SIZE_MULT = 2.4
PHOTON_ALPHA_MULT = 1.5
#: Screen-break bodies are pushed this much further out in the painter, on top
#: of the field's own >1 orbit fraction, so they genuinely clear the widget
#: edge (owner: "do more of that") instead of being drawn back in by the
#: perspective contraction.
SCREEN_BREAK_PUSH = 1.22
#: Screen-break bodies (owner likes elements that "break the screen"): bodies
#: the field deliberately places BEYOND the orb's nominal radius. Drawn as
#: glints so they read as real bodies leaving the mark; the painter never clips
#: them to a circle, so they genuinely run off the widget edge.
SCREEN_BREAK_DRAW_LIMIT = 12
SCREEN_BREAK_SAT = 0.84
#: Nucleus swirl: two counter-rotating hot lobes inside the body, so the centre
#: has internal movement instead of a static gradient.
NUCLEUS_SWIRL_RADIUS_FRAC = 0.22
NUCLEUS_SWIRL_SIZE_FRAC = 0.30
NUCLEUS_SWIRL_PERIOD_S = 6.0
NUCLEUS_SWIRL_RADIUS2_FRAC = 0.42
NUCLEUS_SWIRL_SIZE2_FRAC = 0.20
NUCLEUS_SWIRL_PERIOD2_S = 9.0
NUCLEUS_SWIRL_ALPHA = 0.85
#: Orbiting specular: a small white dot running around the body's rim, which
#: is what makes a shaded sphere read as turning rather than merely breathing.
NUCLEUS_SPECULAR_ORBIT_FRAC = 0.50
NUCLEUS_SPECULAR_SIZE_FRAC = 0.07
NUCLEUS_SPECULAR_PERIOD_S = 12.0
NUCLEUS_SPECULAR_PHASE_DEG = 90.0
NUCLEUS_SPECULAR_ALPHA = 0.90
# --- studio nucleus: chromatic corona + beat-synced flare --------------------
#: Owner: "refine ... make this more advanced studio level ... give 3d effect
#: and depth and artistic". The body, aura, swirl lobes and orbiting specular
#: make the core a SPHERE; a sphere needs something chromatic around it or the
#: surround stays monochrome however lively the core gets. These soft lobes sit
#: OUTSIDE the body and slowly turn through the spectrum, so the core reads as
#: radiating rather than merely glowing.
NUCLEUS_CORONA_COUNT = 3
#: The corona has to sit OUTSIDE the white aura to read as chromatic at all:
#: tucked inside the core it only tints an already-white blob. The aura falls
#: to nothing by r*NUCLEUS_GLOW_MULT and the core ring starts at
#: INNER_RING_RX_FRAC, so the corona lives in the gap between them.
NUCLEUS_CORONA_RADIUS_FRAC = 2.95
NUCLEUS_CORONA_SQUASH = 0.88
NUCLEUS_CORONA_SIZE_FRAC = 1.35
NUCLEUS_CORONA_PERIOD_S = 29.0
NUCLEUS_CORONA_PHASE_DEG = 24.0
NUCLEUS_CORONA_ALPHA = 0.34
#: A softer falloff than the aura's: the corona is a broad chromatic haze
#: around the core, not another tight blob, and the long power the aura needs
#: to kill its own rim would crush it to three dots.
NUCLEUS_CORONA_POWER = 1.8
#: The corona's own slow breathing, and how far its hue travels.
#: Owner 2026-10-19: "keep it professional". There is NO local hue ramp any
#: more: the corona blends between the field's own :data:`PALETTE_HUES` (two
#: adjacent cool tints), alternating which lobe sits on which end. The mark
#: therefore carries the field's disciplined two-colour palette everywhere,
#: and no renderer-side spectrum walk can reintroduce the rainbow.
NUCLEUS_CORONA_BREATH_S = 7.3
NUCLEUS_CORONA_BREATH_SPAN = 0.22
NUCLEUS_CORONA_HUE_PERIOD_S = 29.0
#: Beat-synced flare: a hot bloom over the body that fires on the thump and
#: decays with it, so the core pulses with the audio instead of only breathing.
#: It is a FILL path (never a hairline) precisely so it can never be mistaken
#: for the shockwave's expanding ring.
NUCLEUS_FLARE_RADIUS_FRAC = 2.30
NUCLEUS_FLARE_ALPHA = 0.52
#: The caustic inside the body rides the same beat: the hot inner bloom that
#: makes the core look like it is boiling rather than merely rotating.
NUCLEUS_CAUSTIC_RADIUS_FRAC = 0.86
NUCLEUS_CAUSTIC_ALPHA = 0.62
NUCLEUS_CAUSTIC_ALPHA_BASE = 0.34
NUCLEUS_CAUSTIC_PERIOD_S = 4.7
#: How much the beat opens the swirl lobes' alpha: the core's internal motion
#: is louder on the thump and settles between beats.
NUCLEUS_SWIRL_BEAT_GAIN = 0.55
# --- interior core shells: layered depth INSIDE the nucleus ---------------
#: Owner: "make inside orb more 3d give more depth and animations". The field
#: now declares nested interior bodies (``core_shells``): small shaders at
#: different radii, plane tilts and depths, each turning on its own period. The
#: nucleus body stays the ONE solid ellipse at the centre, so these shells are
#: drawn as small SHADED gradient paths (never ellipses) and layered back to
#: front by depth — the core then reads as a volume you look INTO rather than a
#: flat disc.
CORE_SHELL_MIN_PX = 0.4
CORE_SHELL_SAT = 0.88
CORE_SHELL_CORE_LIFT = 0.55
CORE_SHELL_SIZE_MULT = 1.3
CORE_SHELL_RIM_W = 0.9
CORE_SHELL_DRAW_LIMIT = 4
#: Below this alpha a primitive is imperceptible: skip it rather than let the
#: 8-bit quantisation snap it to zero in one frame (a tiny hard pop).
ALPHA_EPS = 0.004
_GOLDEN = 0.6180339887498949


def _orb_style() -> str:
    """Orb look: ``mark`` (the dashboard logo, default) or ``ember``."""
    import os as _os
    return str(_os.environ.get("QUBIT_ORB_STYLE", "mark") or "mark").strip().lower()


def _fract(x: float) -> float:
    """Fractional part of ``x`` (frame-reproducible pseudo-randomness)."""
    return float(x) - math.floor(float(x))


def heartbeat_envelope(t: Any) -> float:
    """Double-thump beat envelope in ``[0, 1]`` for clock time ``t``.

    Primary thump at the start of each :data:`HEART_PERIOD_S` cycle, a second
    thump at :data:`HEART_SECOND_PHASE` into it and :data:`HEART_SECOND_AMP`
    of its amplitude. Each is a raised-cosine pulse of width
    :data:`HEART_THUMP_S`. Pure, never raises.
    """
    try:
        x = float(t)
    except (TypeError, ValueError):
        x = 0.0
    if not math.isfinite(x):
        x = 0.0
    period = HEART_PERIOD_S
    local = x % period

    def _thump(center: float, amp: float) -> float:
        d = abs(local - center)
        d = min(d, period - d)
        if d >= HEART_THUMP_S:
            return 0.0
        return amp * 0.5 * (1.0 + math.cos(math.pi * d / HEART_THUMP_S))

    return min(1.0, _thump(0.0, 1.0) + _thump(HEART_SECOND_PHASE * period,
                                               HEART_SECOND_AMP))


def _mark_tt(t: Any) -> float:
    """Clamped mark clock: frozen at rest under reduced motion."""
    try:
        tv = float(t)
    except (TypeError, ValueError):
        tv = 0.0
    if not math.isfinite(tv):
        tv = 0.0
    return 0.0 if _reduced_motion() else tv


def mark_particle_layers(t: Any) -> List[Tuple[int, str, float, float]]:
    """Per-particle ``(index, "far"|"near", angle_deg, depth)`` for clock ``t``.

    Pure and frame-reproducible; the painter draws every ``far`` entry behind
    the nucleus and every ``near`` entry in front, so this is also the bead
    z-order. Never raises.
    """
    tt = _mark_tt(t)
    out: List[Tuple[int, str, float, float]] = []
    for idx, (orbit, seed, _bead_r, dur) in enumerate(_mark.PARTICLES):
        ang = seed + (tt * 360.0 / max(0.1, dur))
        depth = 0.5 + 0.5 * math.cos(math.radians(ang))
        out.append((idx, "near" if depth >= 0.5 else "far", ang, depth))
    return out


def mark_sparkles(t: Any) -> List[Tuple[int, float, float, float, float, float, float]]:
    """Visible sparkles for clock ``t`` as ``(i, r_frac, angle_deg, phase,
    fade, size, alpha)``.

    Deterministic: radius/speed/life/phase derive only from ``t`` and the
    index. Empty under reduced motion (a still frame has no glints). Never
    raises.
    """
    if _reduced_motion():
        return []
    tt = _mark_tt(t)
    out: List[Tuple[int, float, float, float, float, float, float]] = []
    for i in range(SPARKLE_COUNT):
        seed = i * SPARKLE_SEED_STEP
        r_frac = SPARKLE_R_MIN_FRAC + SPARKLE_R_SPAN_FRAC * _fract(seed * _GOLDEN)
        ang = seed + tt * (18.0 + (i % 5) * 6.0)
        life = SPARKLE_LIFE_BASE + (i % 4) * SPARKLE_LIFE_STEP
        phase = (tt / life + seed / 360.0) % 1.0
        if phase >= SPARKLE_VISIBLE_FRAC:
            continue
        p01 = phase / SPARKLE_VISIBLE_FRAC
        fade = 0.5 * (1.0 - math.cos(2.0 * math.pi * p01))
        alpha = SPARKLE_MAX_ALPHA * fade
        if alpha <= 0.0:
            continue
        size = SPARKLE_SIZE_BASE + SPARKLE_SIZE_PEAK * fade
        out.append((i, r_frac, ang, phase, fade, size, alpha))
    return out


def mark_echo_rings(t: Any) -> List[Dict[str, float]]:
    """Two thinner follower rings (shared field), frozen out under reduced.

    A pure pass-through to :func:`mark_field.echo_rings` on the clamped mark
    clock, with the same reduced-motion rule as :func:`mark_sparkles`: a still
    frame carries no echo rings. Never raises.
    """
    if _reduced_motion():
        return []
    try:
        return list(_field.echo_rings(_mark_tt(t)))
    except Exception:
        return []


def mark_satellites(t: Any, R: float) -> List[Dict[str, Any]]:
    """Satellite shells (circular paths + a travelling body each).

    Empty under reduced motion so a still frame keeps only the mark itself.
    """
    try:
        if _reduced_motion():
            return []
        return list(_field.satellites(_mark_tt(t), R))
    except Exception:  # noqa: BLE001 - a field fault must not blank the orb
        return []


def mark_nucleus_sparks(t: Any, R: Any) -> List[Dict[str, float]]:
    """Sparks thrown out of the nucleus (shared field), gone under reduced.

    A pure pass-through to :func:`mark_field.nucleus_sparks`, empty under
    reduced motion so a still frame stays clean. Never raises.
    """
    if _reduced_motion():
        return []
    try:
        return list(_field.nucleus_sparks(_mark_tt(t), R))
    except Exception:
        return []


def mark_spin_deg(t: Any) -> float:
    """Master-spin angle (degrees) of the whole mark at clock ``t``.

    One full 360 deg turn per :data:`MASTER_SPIN_PERIOD_S`, continuous by
    construction (a linear ramp, never a wrapped phase), so the rotation can
    never snap at the turn boundary. Reads the CLAMPED clock, so reduced
    motion yields ``0.0`` — the spin is a no-op there. Never raises.
    """
    return 360.0 * _mark_tt(t) / MASTER_SPIN_PERIOD_S


def mark_ring_geometry(t: Any) -> List[Dict[str, Any]]:
    """EVERY circle of the mark as ONE declared list (shared field).

    A pure pass-through to :func:`mark_field.ring_geometry` on the clamped mark
    clock: the two main orbits, their echo followers, the inner core ring and
    the satellite shells, already ordered back to front. The painter strokes
    this list instead of assembling circles itself, which is what stops the
    Qt and Tk renderers drifting into two slightly different sets of circles.

    Same fault rule as the other ``mark_*`` readers: a field that cannot
    declare its circles (a suppressed layer, a partial stub) yields no circles
    rather than raising through the painter mid-frame. The body, the particles
    and the spark shower are unaffected, so a degraded mark is never a blank
    one and never a crash. Never raises.
    """
    try:
        return list(_field.ring_geometry(_mark_tt(t)))
    except Exception:  # noqa: BLE001 - a field fault must not blank the orb
        return []


def mark_horizontal_spin_deg(t: Any) -> float:
    """Horizontal-tumble angle (degrees) of the whole mark at clock ``t``.

    The second, INDEPENDENT full turn layered on top of
    :func:`mark_spin_deg`: one left-to-right 360 deg rotation about a vertical
    axis through the mark's centre per :data:`Y_SPIN_PERIOD_S`. A linear ramp
    (never a wrapped phase), so it cannot snap at the turn boundary. Reads the
    CLAMPED clock, so reduced motion yields ``0.0`` — a true no-op. Never
    raises.
    """
    return 360.0 * _mark_tt(t) / Y_SPIN_PERIOD_S


def mark_horizontal_theta(t: Any) -> float:
    """The horizontal turn's phase in RADIANS at clock ``t``, to a turn.

    The ONE phase the whole Y-axis projection reads, so the painter, the tests
    and any future consumer cannot disagree about where in the turn the mark
    is. Wrapped to ``[0, 2*pi)`` only for numerical stability at a large clock
    — a full turn is the identity, so the wrap is invisible. Linear in the
    clock, therefore continuous everywhere including across the wrap. Reads the
    CLAMPED clock, so reduced motion yields ``0.0`` — an exact no-op. Never
    raises.
    """
    return math.radians(math.fmod(mark_horizontal_spin_deg(t), 360.0))


def mark_horizontal_spin(t: Any) -> float:
    """The horizontal turn's ``cos(theta)``: a FLAT plane's foreshortening.

    The mark is no longer flat, so this is no longer what the painter scales
    the whole assembly by (that was the edge-on collapse: owner, "i dont what
    that thin"). It is kept as the public read of the turn's PHASE: the
    factor a depth-NEUTRAL point would foreshorten by, positive face-on,
    negative past 90 degrees where the mark is mirrored. The painter uses it
    to carry the nucleus's specular highlight across the turning sphere, and
    the turn tests measure the phase against it.

    Exactly ``1.0`` under reduced motion. Never raises.
    """
    return math.cos(mark_horizontal_theta(t))


def mark_vertical_spin_deg(t: Any) -> float:
    """X-tumble angle (degrees) of the whole mark at clock ``t``.

    The second half of the combined 3D turn: one full 360 deg rotation about
    a HORIZONTAL axis through the mark's centre per :data:`X_SPIN_PERIOD_S`,
    applied AFTER the Y rotation. A linear
    ramp (never a wrapped phase), so it cannot snap at the turn boundary.
    Reads the CLAMPED clock, so reduced motion yields ``0.0`` — a true no-op.
    Never raises.
    """
    return 360.0 * _mark_tt(t) / X_SPIN_PERIOD_S


def mark_vertical_theta(t: Any) -> float:
    """The X-tumble phase in RADIANS at clock ``t``, to a turn.

    The Y phase's sibling: wrapped to ``[0, 2*pi)`` only for numerical
    stability, linear and continuous everywhere, ``0.0`` under reduced motion.
    Never raises.
    """
    return math.radians(math.fmod(mark_vertical_spin_deg(t), 360.0))


def mark_tumble_angles(t: Any) -> Tuple[float, float]:
    """``(theta_y, phi_x)`` in RADIANS at clock ``t``: the single source.

    Both phases come from :func:`mark_field.spin_angles` on the clamped mark
    clock — the painter, the tests and the Tk lane all read the same pair, so
    the lanes cannot disagree about where in the tumble the mark is. Wrapped
    to ``[0, 2*pi)`` per component for numerical stability at a large clock.
    ``(0.0, 0.0)`` at a still clock, so reduced motion is an exact identity.
    Never raises.
    """
    try:
        theta_y, phi_x = _field.spin_angles(_mark_tt(t))
        two_pi = 2.0 * math.pi
        return (float(theta_y) % two_pi, float(phi_x) % two_pi)
    except Exception:  # noqa: BLE001 - a field fault must not blank the orb
        return (0.0, 0.0)


def y_turn_gain(zr: Any) -> float:
    """Size/alpha gain for a body whose ROTATED depth (in radii) is ``zr``.

    One body swinging toward the viewer (``zr > 0``) grows and brightens; one
    tucking away shrinks and dims. Clamped, so a field value outside its
    nominal depth range can never produce a negative or runaway scale. Pure.
    Never raises.
    """
    z = _finite(zr, 0.0)
    g = 1.0 + Y_SPIN_DEPTH_GAIN * z
    return min(Y_SPIN_GAIN_MAX, max(Y_SPIN_GAIN_MIN, g))


#: Last ``(clock, cos, sin)`` for the turn. One frame projects on the order of
#: a thousand points (every streak sample, every ring vertex), and they all
#: share one phase, so the phase is computed once per frame rather than once
#: per point. Pure memoisation of a pure function: same clock in, same pair
#: out, and a frame is single-threaded so there is no race to lose.
_TURN_BASIS: Tuple[float, float, float] = (float("nan"), 0.0, 0.0)

#: Cached glyph fonts keyed by ``(family, pixel_size)``. The binary "1"/"0"
#: layer still draws dozens of fragments a frame; building a QFont (copy +
#: setPixelSize + setFont) for each was pure per-frame overhead. (The math
#: glyph layer that used this too is retired — see ``volume_particles``.)
_GLYPH_FONT_CACHE: Dict[Any, Any] = {}


def _turn_basis(t: Any) -> Tuple[float, float]:
    """``(cos(theta), sin(theta))`` of the turn at clock ``t``, memoised."""
    global _TURN_BASIS
    tt = _mark_tt(t)
    cached, ct, st = _TURN_BASIS
    if cached == tt:
        return ct, st
    theta = mark_horizontal_theta(tt)
    ct, st = math.cos(theta), math.sin(theta)
    _TURN_BASIS = (tt, ct, st)
    return ct, st


def y_turn_point(t: Any, R: Any, x: Any, y: Any, depth: Any = 0.5
                 ) -> Tuple[float, float, float]:
    """Project one field point through the mark's Y-axis 360 turn.

    Returns ``(X, Y, gain)`` in field/painter units RELATIVE to the mark's
    centre, plus the rotated-depth gain for that point's size and alpha.

    Every element in the mark already carries the field's ``depth`` (0.0 far,
    1.0 near), so the mark is not flat and must not be projected as if it
    were. The rotation about the vertical axis mixes x with that depth::

        z   = (depth - 0.5) * Z_GAIN * R * sin(theta)**2
        xr  = x * cos(theta) + z * sin(theta)
        zr  = -x * sin(theta) + z * cos(theta)
        persp = 1 + PERSP * zr / R
        X, Y = xr * persp, y * persp

    ``y`` is untouched by a rotation about a vertical axis, which is exactly
    why the mark's height never changes and only its DEPTH reads as turning.

    The depth offset is faded by ``sin(theta)**2``, i.e. by how far the
    assembly has actually turned. At ``theta = 0`` and ``theta = pi`` that
    factor is zero, so every term collapses to ``xr = +-x``, ``persp = 1`` and
    ``gain = 1``: the face-on phases are the unturned mark, bit for bit, and
    the turn cannot pop on start-up or leave a still frame altered. It is
    also smooth (``sin^2`` has no kink anywhere), so it cannot introduce a
    step of its own. The phase warp this replaced existed only to shorten the
    moments the mark went edge-on to a line; there are none left to shorten,
    so the turn is now a plain, even 360 degrees.

    Never raises.
    """
    r = _finite(R, 32.0)
    if r <= 0.0:
        r = 32.0
    ct, st = _turn_basis(t)
    return _y3d_project(_finite(x, 0.0), _finite(y, 0.0),
                        min(1.0, max(0.0, _finite(depth, 0.5))), ct, st, r)


def _y3d_project(x: float, y: float, depth: float, ct: float, st: float,
                 r: float) -> Tuple[float, float, float]:
    """The projection itself, with the turn's phase already resolved.

    Split out from :func:`y_turn_point` for the painter's inner loop: a frame
    projects on the order of a thousand points (every streak sample, every
    declared ring's vertices) and they all share one phase, so the painter
    resolves ``(cos, sin)`` once per frame and calls this with it. Identical
    arithmetic — :func:`y_turn_point` is a thin wrapper over it.
    """
    z = (depth - 0.5) * Y_SPIN_Z_GAIN * r * st * st
    xr = x * ct + z * st
    zr = -x * st + z * ct
    persp = 1.0 + Y_SPIN_PERSP * zr / r
    if persp < Y_SPIN_PERSP_MIN:
        persp = Y_SPIN_PERSP_MIN
    elif persp > Y_SPIN_PERSP_MAX:
        persp = Y_SPIN_PERSP_MAX
    nz = zr / r
    g = 1.0 + Y_SPIN_DEPTH_GAIN * nz
    if g < Y_SPIN_GAIN_MIN:
        g = Y_SPIN_GAIN_MIN
    elif g > Y_SPIN_GAIN_MAX:
        g = Y_SPIN_GAIN_MAX
    return (xr * persp, y * persp, g)


#: Last ``(clock, cos_y, sin_y, cos_x, sin_x)`` for the combined tumble. Same
#: memoisation deal as :data:`_TURN_BASIS`: one frame projects on the order of
#: a thousand points and they all share one phase pair.
_TUMBLE_BASIS: Tuple[float, float, float, float, float] = (
    float("nan"), 1.0, 0.0, 1.0, 0.0)


def _tumble_basis(t: Any) -> Tuple[float, float, float, float]:
    """``(cos_y, sin_y, cos_x, sin_x)`` of the combined tumble, memoised."""
    global _TUMBLE_BASIS
    tt = _mark_tt(t)
    cached, cy, sy, cx, sx = _TUMBLE_BASIS
    if cached == tt:
        return cy, sy, cx, sx
    theta_y, phi_x = mark_tumble_angles(tt)
    cy, sy, cx, sx = (math.cos(theta_y), math.sin(theta_y),
                      math.cos(phi_x), math.sin(phi_x))
    _TUMBLE_BASIS = (tt, cy, sy, cx, sx)
    return cy, sy, cx, sx


def _tumble_project(x: float, y: float, depth: float,
                    cy: float, sy: float, cx: float, sx: float,
                    r: float) -> Tuple[float, float, float]:
    """The combined X+Y projection, with both phases already resolved.

    ROTATION ORDER: Y first, then X — identical to the Tk lane. The Y turn
    mixes x with the field depth exactly as :func:`_y3d_project` does::

        z   = (depth - 0.5) * Z_GAIN * R * sin(theta_y)**2
        xr  = x * cos_y + z * sin_y
        zr  = -x * sin_y + z * cos_y

    and the X turn then mixes y with that rotated depth::

        yr  = y * cos_x - zr * sin_x
        zr2 = y * sin_x + zr * cos_x
        persp = clamp(1 + PERSP * zr2 / R)
        X, Y = xr * persp, yr * persp

    The gain reads the FINAL rotated depth ``zr2``, so a body recedes (and
    shrinks, dims, cools) through both turns, not just the Y one.

    The depth offset is faded by the COMBINED turn — ``f = sin²θy + sin²φx −
    sin²θy·sin²φx`` — instead of by the Y turn alone. The Y-only ``sin²θy``
    fade exists so the face-on phases are the unturned mark bit for bit (and
    the still frame with it); but under a second axis it also guarantees a
    total collapse: near Y face-on the depth vanishes exactly where the X
    turn tips the orbital plane edge-on, and the whole mark folds to a line.
    The combined fade is 0 if and only if BOTH phases are 0 — so ``t = 0``
    and reduced motion are still the exact identity — and 1 at ANY quarter
    turn of either axis, so the mark opens up to show its depth along
    whichever axis is edge-on, exactly as the Y-only turn does at its own
    quarters. It is a polynomial in the sines, so it has no kink anywhere.
    At ``phi_x = 0`` this is bit-for-bit :func:`_y3d_project`; at
    ``theta_y = phi_x = 0`` it is the identity with gain 1, so a still clock
    paints the unturned mark and reduced motion is an exact no-op.
    """
    fade = sy * sy + sx * sx - sy * sy * sx * sx
    z = (depth - 0.5) * Y_SPIN_Z_GAIN * r * fade
    xr = x * cy + z * sy
    zr = -x * sy + z * cy
    yr = y * cx - zr * sx
    zr2 = y * sx + zr * cx
    persp = 1.0 + Y_SPIN_PERSP * zr2 / r
    if persp < Y_SPIN_PERSP_MIN:
        persp = Y_SPIN_PERSP_MIN
    elif persp > Y_SPIN_PERSP_MAX:
        persp = Y_SPIN_PERSP_MAX
    nz = zr2 / r
    g = 1.0 + Y_SPIN_DEPTH_GAIN * nz
    if g < Y_SPIN_GAIN_MIN:
        g = Y_SPIN_GAIN_MIN
    elif g > Y_SPIN_GAIN_MAX:
        g = Y_SPIN_GAIN_MAX
    return (xr * persp, yr * persp, g)


def turn_qdepth(depth: Any, zseed: Any = None,
                spread: Any = TURN_Z_SPREAD,
                flat: bool = False) -> float:
    """Projection depth with the x-lock broken (see ``TURN_Z_SALT``).

    Pure. With no seed, the identity (raw field depth). With a seed, two
    modes: blended (``flat=False``) adds ``spread * (hash - 0.5)`` to the
    depth — a whisper of volume that keeps smooth loops smooth; flat
    (``flat=True``) returns the hash itself, fully independent of orbital
    position. Points use flat (a swarm must fill the volume, not ride one
    plane); ring vertices use blended (a declared circle must stay a clean
    ellipse face-on). At face-on phases the fade zeroes z whatever this
    returns, so still frames never change. Never raises. Tests pin the
    painter through this function, not around it.
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
        h = _field.hash01(float(zseed), TURN_Z_SALT)
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


def tumble_point(t: Any, R: Any, x: Any, y: Any, depth: Any = 0.5
                 ) -> Tuple[float, float, float]:
    """Project one field point through the combined X+Y tumble.

    Returns ``(X, Y, gain)`` relative to the mark's centre, plus the rotated
    depth gain. The painter's ``_place`` is this plus the frame's centre; the
    tests read this directly. Identity with gain 1 at ``t = 0`` and under
    reduced motion. Never raises.
    """
    r = _finite(R, 32.0)
    if r <= 0.0:
        r = 32.0
    cy, sy, cx, sx = _tumble_basis(t)
    return _tumble_project(_finite(x, 0.0), _finite(y, 0.0),
                           min(1.0, max(0.0, _finite(depth, 0.5))),
                           cy, sy, cx, sx, r)


def turn_tone(rgb: Tuple[int, int, int], gain: Any = 1.0) -> Tuple[int, int, int]:
    """Brightness after the tumble's rotated-depth gain.

    A body swinging toward the viewer renders hotter, one tucking away cooler
    — on top of the size spread (the painter scales radii by the gain) and the
    alpha spread (:func:`y_turn_gain` feeding ``_shade``). Pure: at gain 1 the
    factor is exactly 1.0, so face-on and reduced-motion frames are untouched.
    Never raises.
    """
    try:
        g = float(gain)
    except (TypeError, ValueError):
        g = 1.0
    if not math.isfinite(g):
        g = 1.0
    g = min(Y_SPIN_GAIN_MAX, max(Y_SPIN_GAIN_MIN, g))
    k = TURN_TONE_BASE + TURN_TONE_SLOPE * g
    return tuple(max(0, min(255, int(c * k))) for c in rgb)  # type: ignore[return-value]


#: Memo for ``turn_fade`` — it is consulted once per element through
#: ``volume_offset`` (500+ times a frame) and depends only on the frame clock.
_TURN_FADE_CACHE: Tuple[float, float] = (float("nan"), 0.0)


def turn_fade(t: Any) -> float:
    """The combined turn's depth-opening factor at clock ``t`` in ``[0, 1]``.

    ``sin²θy + sin²φx − sin²θy·sin²φx``: exactly 0 when BOTH tumble phases are
    zero (face-on, and every reduced-motion frame, since both reads clamp to 0)
    and 1 when an element's depth axis is fully edge-on. This is the SAME factor
    :func:`_tumble_project` multiplies the depth offset by, so anything routed
    through it is an exact no-op at a still clock. Never raises.
    """
    global _TURN_FADE_CACHE
    tt = _mark_tt(t)
    cached_t, cached_f = _TURN_FADE_CACHE
    if cached_t == tt:
        return cached_f
    try:
        cy, sy, cx, sx = _tumble_basis(tt)
        f = sy * sy + sx * sx - sy * sy * sx * sx
        f = 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)
    except Exception:  # noqa: BLE001 - a bad clock must not blank the orb
        f = 0.0
    _TURN_FADE_CACHE = (tt, f)
    return f


def volume_offset(t: Any, R: Any, z: Any, z2: Any = 0.5
                  ) -> Tuple[float, float]:
    """Screen-space 3D displacement from the field's ``z``/``z2`` volume axes.

    Each axis is a ``0..1`` hash of the element's index, so the displacement is
    CONSTANT per element and uncorrelated with its orbital angle — a real
    volume, not the depth/x lock that flattened the mark. ``z`` moves an element
    vertically and ``z2`` horizontally (the two axes are independent, so the
    cloud fills a box, not a line). The whole offset is faded by
    :func:`turn_fade`, so at a face-on phase and under reduced motion it is
    exactly ``(0, 0)``: still frames carry the unturned mark, unaltered. Pure,
    never raises.
    """
    f = turn_fade(t)
    r = _finite(R, 32.0)
    if r <= 0.0:
        r = 32.0
    dz = min(1.0, max(0.0, _finite(z, 0.5))) - 0.5
    dz2 = min(1.0, max(0.0, _finite(z2, 0.5))) - 0.5
    return (dz2 * (2.0 * VOLUME_X_FRAC) * r * f,
            -dz * (2.0 * VOLUME_Y_FRAC) * r * f)


def mark_blink_dots(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Blink dots (shared field), gone under reduced motion.

    A pure pass-through to :func:`mark_field.blink_dots` on the clamped mark
    clock; a still frame carries no materialising dots. Never raises.
    """
    if _reduced_motion():
        return []
    try:
        return list(_field.blink_dots(_mark_tt(t), _finite(R, 32.0)))
    except Exception:  # noqa: BLE001 - a field fault must not blank the orb
        return []


def mark_binaries(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Binary "1"/"0" fragments (shared field), gone under reduced motion.

    A pure pass-through to :func:`mark_field.binary_state` on the clamped mark
    clock; a still frame carries no moving text, so reduced motion returns an
    empty list instead of freezing a field of glyphs. Never raises.
    """
    if _reduced_motion():
        return []
    try:
        return list(_field.binary_state(_mark_tt(t), _finite(R, 32.0)))
    except Exception:  # noqa: BLE001 - a field fault must not blank the orb
        return []


def mark_volume_particles(t: Any, R: Any) -> List[Dict[str, Any]]:
    """The 3D volume-particle field (shared field), gone under reduced motion.

    Replaces the retired math-glyph TEXT layer (owner 2026-10-19: the ``√`` /
    ``Δ`` / ``∇`` symbols read as "check marks / v lines"). Each body carries
    ``{index, x, y, depth, z, z2, vx, vy, size, alpha, lift, hue, hue2, dof,
    skew, r, inc, pulse, spin}`` — a real 3D particle with its own pulse and
    spin. Drawn as round dots, never text. Read defensively through the
    optional-layer seam so a field revision without it simply draws no
    particles instead of blanking the orb. Never raises.
    """
    return _field_layer("volume_particles", t, R, reduced_empty=True)


def mark_accretion_stream(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Spiral accretion-stream samples (shared field); empty when still."""
    return _field_layer("accretion_stream", t, R, reduced_empty=True)


def mark_photon_ring(t: Any, R: Any) -> List[Dict[str, Any]]:
    """The lensed photon ring hugging the core (shared field); empty when still."""
    return _field_layer("photon_ring", t, R, reduced_empty=True)


def mark_infall_streaks(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Radial streaks falling toward the core (shared field); empty when still."""
    return _field_layer("infall_streaks", t, R, reduced_empty=True)


def mark_screen_break(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Bodies deliberately reaching BEYOND the orb radius (shared field).

    Owner likes the mark "breaking the screen", so the field declares a handful
    of bodies whose projected position leaves the orb's nominal radius; the
    painter draws them like any other body, and because nothing clips to a
    circle they visibly exit the widget. Empty under reduced motion. Never
    raises.
    """
    return _field_layer("screen_break", t, R, reduced_empty=True)


def mark_inner_ring_tilt(t: Any) -> float:
    """Tilt of the fast inner core ring; frozen at its base under reduced.

    Reads the clamped clock so a still frame parks the ring on
    :data:`mark_field.INNER_RING_TILT_BASE` rather than mid-rotation. Never
    raises.
    """
    try:
        if _reduced_motion():
            return _field.INNER_RING_TILT_BASE
        return _field.inner_ring_tilt(_mark_tt(t))
    except Exception:  # noqa: BLE001
        return _field.INNER_RING_TILT_BASE


def _field_layer(name: str, t: Any, R: Any, reduced_empty: bool = True
                 ) -> List[Dict[str, Any]]:
    """Read one optional field layer defensively (never raise, never blank).

    The new rich layers (``core_shells``, ``flare_dots``, ``filament_state``,
    ``ring_particles``, ``cylinder_particles``) are consumed through here so a
    field that has not landed a layer yet — or raises part-way — degrades to a
    missing layer instead of a crash mid-frame. ``reduced_empty`` drops live
    layers under reduced motion so a still frame stays clean; the nucleus
    interior passes ``False`` (interior depth survives at rest). Never raises.
    """
    if reduced_empty and _reduced_motion():
        return []
    fn = getattr(_field, name, None)
    if not callable(fn):
        return []
    try:
        return [e for e in fn(_mark_tt(t), _finite(R, 32.0)) if isinstance(e, dict)]
    except Exception:  # noqa: BLE001 - a field fault must not blank the orb
        return []


def mark_core_shells(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Nested interior bodies of the nucleus (shared field).

    Kept alive under reduced motion — the interior of the sphere is part of the
    still mark, and the field clamps its own clock — so a resting frame still
    reads as a layered core. Never raises.
    """
    return _field_layer("core_shells", t, R, reduced_empty=False)


def mark_flare_dots(t: Any, R: Any) -> List[Dict[str, Any]]:
    """The rare, larger blink-flare family; empty under reduced motion."""
    return _field_layer("flare_dots", t, R, reduced_empty=True)


def mark_filament(t: Any, R: Any) -> List[Dict[str, Any]]:
    """The travelling energy filament's samples; empty under reduced motion."""
    return _field_layer("filament_state", t, R, reduced_empty=True)


def mark_ring_particles(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Bodies riding every declared ring; empty under reduced motion."""
    return _field_layer("ring_particles", t, R, reduced_empty=True)


def mark_cylinder_particles(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Bodies forming a vertical column around the nucleus; empty when still."""
    return _field_layer("cylinder_particles", t, R, reduced_empty=True)


def mark_orbiter_particles(t: Any, R: Any) -> List[Dict[str, Any]]:
    """Bodies circling the NUCLEUS on inclined 3D orbits; empty when still."""
    return _field_layer("orbiter_particles", t, R, reduced_empty=True)


def strided(items: Any, target: int) -> List[Dict[str, Any]]:
    """At most ``target`` dicts from ``items``, evenly strided.

    The new rich layers return more bodies than the primitive ceiling can
    afford to draw. Drawing a fixed, evenly-spread subset keeps the mark rich
    while the per-frame cost stays flat and independent of the field's count.
    Junk entries are dropped; never raises.
    """
    seq = [x for x in (items or []) if isinstance(x, dict)]
    tgt = int(target) if isinstance(target, int) or str(target).lstrip("-").isdigit() else 0
    if tgt <= 0:
        return []
    if len(seq) <= tgt:
        return seq
    step = int(math.ceil(len(seq) / float(tgt)))
    return seq[::step][:tgt]


def _is_near(item: Any, near: bool) -> bool:
    """True when an element belongs to the near (depth >= 0.5) z-seam half."""
    try:
        return (_finite(item.get("depth"), 0.5) >= 0.5) == near
    except Exception:  # noqa: BLE001
        return False


def _finite(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _paint_mark(p: Any, cx: float, cy: float, R: float, t: float,
                state: Any, brightness: float, family: Any) -> None:
    """Paint the Qubit mark from the shared rich animation field.

    The geometry, motion and depth of every layer come from
    :mod:`voicebridge.orb.mark_field` — the ONE deterministic description
    shared with the Tk renderer. This function only translates field output
    to QPainter: hairline fringed orbits with a thinner lagged echo ring
    following each, a z-split particle field (far elements behind the
    nucleus, near elements in front), tapered duotone trails, dust, glints,
    blink dots that materialise out of nowhere, one comet, a spark shower
    thrown out of the nucleus on the beat, the beat shockwave and the beating
    nucleus with its Fresnel rim.

    The look stays a crisp hairline construction: rings are stroked paths
    (never gradient or blur fills), beads are small filled circles, and
    reduced motion paints a completely static, sparkle-free frame.
    """
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient,
                               QPainterPath, QPen, QRadialGradient)

    # A cached QFont per (family, pixel size). The glyph layers draw ~128
    # fragments a frame and each was constructing a fresh QFont (copy +
    # setPixelSize + setFont) AND pushing/popping the painter state — pure
    # overhead, since the font only depends on the size. The base family is
    # captured once from the painter, so the drawn glyphs are unchanged.
    _base_family = p.font().family()

    def _glyph_font(px: int) -> Any:
        key = (_base_family, px)
        got = _GLYPH_FONT_CACHE.get(key)
        if got is None:
            got = QFont(p.font())
            got.setPixelSize(px)
            _GLYPH_FONT_CACHE[key] = got
        return got

    def _draw_glyph(gx: float, gy: float, ch: str, rgb: Any,
                    alpha: float, px: int) -> None:
        """One text glyph, no per-call state push (brush is set by the caller)."""
        p.setPen(QColor(rgb[0], rgb[1], rgb[2],
                        _clamp8(255.0 * min(1.0, alpha))))
        p.setFont(_glyph_font(px))
        p.drawText(QPointF(gx, gy), ch)

    def _clamp8(v: Any) -> int:
        try:
            return max(0, min(255, int(v)))
        except (TypeError, ValueError):
            return 0

    def _num(v: Any, default: float) -> float:
        # Fast path: the field hands us floats almost every call, so skip the
        # float()/try entirely for the common case. Behaviour is identical.
        if type(v) is float:
            return v if math.isfinite(v) else default
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return f if math.isfinite(f) else default

    fam = family if isinstance(family, dict) else {}
    core = (255, 84, 0)
    raw_core = fam.get("core")
    if isinstance(raw_core, (tuple, list)) and len(raw_core) >= 3:
        core = tuple(_clamp8(v) for v in raw_core[:3])

    Rf = _num(R, 32.0)
    if Rf <= 0.0:
        Rf = 32.0
    ring_w = 1.1
    # A lens fringe is a PIXEL effect (fixed offset), never scaled with R.
    fringe = _field.FRINGE_PX * 0.7
    # The sidebar mark is a BRIGHT near-white hairline with a chromatic
    # hint, not a dim state tint: lift luminance hard and use the state hue
    # only as the tint. Idle must never read as "almost off".
    bright = max(_field.MIN_MARK_BRIGHTNESS, _num(brightness, 1.0))

    def _lift(amount: float) -> Tuple[int, int, int]:
        a = max(0.0, min(1.0, _num(amount, 0.0)))
        return (
            _clamp8(core[0] + (255 - core[0]) * a),
            _clamp8(core[1] + (255 - core[1]) * a),
            _clamp8(core[2] + (255 - core[2]) * a),
        )

    ring_rgb = _lift(_field.LIFT_RING)
    nuc_rgb = _lift(_field.LIFT_NUCLEUS)

    # ---- the combined X+Y tumble: where every drawn point goes --------------
    # The mark is NOT flat: every element in the field carries a ``depth``,
    # so the tumble is a real 3D rotation — first about a vertical axis, then
    # about a horizontal one — rather than a ``cos(theta)`` squash of a
    # two-dimensional picture. The squash is what the owner rejected ("i dont
    # what that thin"): a flat projection degenerates to a 1px line twice per
    # turn; here the near and far halves SEPARATE instead, so the mark opens
    # up to show its depth at exactly those moments. The whole projection
    # lives in :func:`tumble_point` (module level, so the tests can read it
    # without a painter); ``_place`` is that plus the frame's centre. The
    # Y-only :func:`y_turn_point` stays for the turn contract underneath.
    #
    # It is a per-POINT map, not a painter transform, because a transform
    # cannot express a point at a different depth: one transform would squash
    # a near element and a far one identically, which is the flat mark again.
    #
    # Every gain is exactly 1.0 when both phases are 0, so a still frame is
    # the unturned mark, unaltered and with no transform pushed on the painter.

    def _place(x: Any, y: Any, depth: Any = 0.5, zseed: Any = None,
                 zspread: Any = TURN_Z_SPREAD, zflat: bool = False,
                 z: Any = None, z2: Any = None
                 ) -> Tuple[float, float, float]:
        """A field point's painted position and rotated-depth gain.

        The tumble's phases are resolved once per frame above: a frame
        projects ~1000 points and they all share them, so the trigonometry is
        not repeated per point. ``zseed`` breaks the depth/x lock per element
        (see ``turn_qdepth``); ``None`` keeps the raw field depth. Points pass
        ``zflat=True`` (full volume); ring vertices pass a small spread.

        ``z``/``z2`` are the field's two independent VOLUME axes: when given,
        an element also receives a screen-space displacement along two
        orthogonal directions (:func:`volume_offset`), so the cloud occupies a
        genuine volume instead of one sheet. The displacement is faded by the
        combined turn, so it is exactly zero face-on and under reduced motion.
        """
        px, py, g = _tumble_project(_num(x, 0.0), _num(y, 0.0),
                                    turn_qdepth(depth, zseed, zspread,
                                                flat=zflat),
                                    turn_cy, turn_sy, turn_cx, turn_sx, Rf)
        if z is not None or z2 is not None:
            ox, oy = volume_offset(tt, Rf, z, z2)
            px += ox
            py += oy
        return cx + px, cy + py, g

    def _shade(alpha: Any, depth: Any = 0.5, gain: Any = 1.0) -> float:
        """Alpha after the turn's depth gain and the depth-of-field softening.

        ``gain`` is the element's own :func:`y_turn_gain` (bigger and brighter
        as it swings toward the viewer); ``depth`` is its FIELD depth, which
        drives the focus term. ``bright`` is deliberately NOT folded in here:
        several callers already apply it and a second application would
        quietly flatten the mark's brightness ladder.
        """
        a = _num(alpha, 0.0)
        d = min(1.0, max(0.0, _num(depth, 0.5)))
        a *= _num(gain, 1.0)
        a *= 1.0 - DOF_ALPHA_SOFT * _field.dof(d)
        return a

    def _tone(rgb: Tuple[int, int, int], gain: Any = 1.0
              ) -> Tuple[int, int, int]:
        """Brightness after the tumble's rotated-depth gain.

        A body swinging toward the viewer renders hotter, one tucking away
        cooler — on top of the size spread (the painter scales radii by the
        gain) and the alpha spread (:func:`_shade`). At gain 1 the factor is
        exactly 1.0, so face-on and reduced-motion frames are untouched.
        """
        return turn_tone(rgb, gain)

    # ---- the mark's spectrum ------------------------------------------------
    # The field owns the colour wheel (``mark_field.hue_to_rgb``, hue in TURNS
    # and wrapping like a colour wheel). The painter never interpolates hue:
    # it converts each sample and lets the RENDERED RGB blend, so a streak that
    # crosses 1.0 -> 0.0 takes the short way round instead of sweeping the whole
    # spectrum the wrong way.
    def _spec_rgb(hue: Any, sat: Any = 1.0, val: Any = 1.0) -> Tuple[int, int, int]:
        r, g, b = _field.hue_to_rgb(hue, sat, val)
        return (_clamp8(r * 255.0), _clamp8(g * 255.0), _clamp8(b * 255.0))

    def _anchor(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Pull a spectral colour a little toward the mark's own core colour.

        Enough that the streaks still change with the orb's state; not so much
        that the spectrum the owner asked for washes out.
        """
        k = TRAIL_HUE_ANCHOR_BLEND
        return tuple(_clamp8(c + (nuc_rgb[i] - c) * k)
                     for i, (c, n) in enumerate(zip(rgb, nuc_rgb)))

    # ---- one declared geometry for every circle -----------------------------
    # ``ring_geometry`` lists all eight circles back to front, so painting them
    # in that order IS the depth order. Radii, layer, order and width come from
    # the declaration; the painter adds only the mark's art direction (the
    # chromatic fringe on the two MAIN orbits, the brightness ladder, and the
    # stroke itself) — never its own geometry. Built once the clamped clock
    # exists, with the rest of the frame setup.

    def _ring_stroke(points: Any, alpha: float, width: float,
                     layer: str) -> None:
        """Stroke one declared circle: fringe on the main orbits, nothing else."""
        a = _shade(alpha)
        if a <= ALPHA_EPS or len(points) < 3:
            return
        path = QPainterPath()
        path.moveTo(points[0][0], points[0][1])
        for px, py in points[1:]:
            path.lineTo(px, py)
        p.setBrush(Qt.NoBrush)
        if layer == "main":
            # Chromatic fringe: the same hairline offset in x, red left / blue
            # right, so overlap reads near-white and the edges split (the CSS
            # mark's technique, drawn with real alpha here).
            # The CSS mark fringes with a BLURRED drop shadow. A crisp offset
            # stroke reads as a second line (a rainbow outline); a wide, very
            # translucent one reads as the soft chromatic halo the logo has.
            # EXCLUSIVE to the two main orbits: it is the logo's signature, and
            # an echo / core ring / satellite shell wearing it reads as four
            # more logo orbits instead of the quiet circles they are.
            for dx, col in ((fringe, QColor(255, 45, 85)),
                            (-fringe, QColor(45, 130, 255))):
                p.setPen(QPen(QColor(col.red(), col.green(), col.blue(),
                                     _clamp8(255.0 * a * FRINGE_ALPHA_MULT)),
                              width * FRINGE_WIDTH_MULT))
                p.translate(dx, 0.0)
                p.drawPath(path)
                p.translate(-dx, 0.0)
        pen = QPen(QColor(ring_rgb[0], ring_rgb[1], ring_rgb[2],
                          _clamp8(255.0 * min(1.0, a))),
                   max(0.6, width))
        p.setPen(pen)
        p.drawPath(path)
        p.setPen(Qt.NoPen)

    # Every declared circle is sampled at the same count, so the depth each
    # sample carries is the SAME list for all of them: built once per frame
    # (lazily, so a reduced-motion frame that skips the circles skips it too).
    _ring_depth_cache: Dict[int, List[float]] = {}

    def ring_depths(n: int) -> List[float]:
        """The field's depth convention sampled at ``n`` orbital angles."""
        got = _ring_depth_cache.get(n)
        if got is None:
            got = [0.5 + 0.5 * math.cos(2.0 * math.pi * i / float(n))
                   for i in range(n + 1)]
            _ring_depth_cache[n] = got
        return got

    def _ring_pass(near: bool) -> None:
        """Stroke the declared circles belonging to the far or near half.

        The list is already back to front; the split is at the nucleus, so the
        far circles take part in the z-seam and the near ones paint over it.
        """
        for spec in ring_specs:
            layer = str(spec.get("layer") or "")
            if (near != (str(spec.get("orbit") or "") == "a")):
                continue
            # Echo followers and satellite shells are LIFE, not the mark: a
            # still frame is the main orbits, the core ring and the body.
            if reduced and layer in ("echo", "satellite"):
                continue
            tilt = spec.get("tilt")
            if spec.get("echo") and str(spec.get("orbit") or "") in echo_tilt:
                tilt = echo_tilt[str(spec.get("orbit") or "")]
            draw_spec = dict(spec)
            # Fluid circles (owner: "make circles animation more fluid"): the
            # declaration now carries a per-ring PLANE PRECESSION
            # (``tilt_spin_deg``, a bounded sway on the ring's own non-harmonic
            # period) and a RADIUS BREATH (``ring_radius_scale``). Add both so
            # each circle slowly tips and breathes on its own schedule instead
            # of sitting rigid, while the declared base tilt/rx/ry the
            # contracts assert are untouched.
            spin = spec.get("tilt_spin_deg")
            if spin is None:
                spin = _field.ring_tilt_spin(tt, spec)
            breath = _field.ring_radius_scale(tt, spec)
            # Per-ring accretion detail (owner: the circles "look like
            # blackholes ... add more details and animations"). ``shimmer`` is
            # a sub-percent radius texture ON TOP of the shared rigid breath,
            # so the set stays proportional while each circle glitters;
            # ``intensity`` is a travelling bright hot-spot along the stroke.
            shimmer = spec.get("shimmer")
            if shimmer is None:
                shimmer = _field.ring_shimmer(tt, spec)
            intensity = spec.get("intensity")
            if intensity is None:
                intensity = _field.ring_intensity(tt, spec)
            draw_spec["tilt"] = _num(tilt, 0.0) + _num(spin, 0.0)
            draw_spec["rx_frac"] = (_num(spec.get("rx_frac"), _field.RX_FRAC)
                                    * breath * _num(shimmer, 1.0))
            draw_spec["ry_frac"] = (_num(spec.get("ry_frac"), _field.RY_FRAC)
                                    * breath * _num(shimmer, 1.0))
            # ``ring_path`` carries the field's camera sway. The BODY does not
            # sway, so a circle drawn around it must not either: leaving the
            # sway in slides the mark's visual centre off its own core and the
            # mark stops reading as one object. Take the sway back out — the
            # declared radii, tilt, layer, width and alpha are untouched.
            raw = _field.ring_path(tt, Rf, draw_spec,
                                   samples=RING_WARP_SAMPLES)
            # A circle is a STROKED PATH, not a set of field points, so each
            # point's depth has to come from where it sits on the circle. The
            # field's convention is ``0.5 + 0.5*cos(orbital angle)`` — the
            # same one it gives every particle — and ``ring_path`` samples at
            # ``360*i/n``, so the orbital angle is recoverable EXACTLY from the
            # index rather than read back off the tilted, parallaxed ellipse.
            # Reading it off the index is also what keeps the two halves
            # complementary (point ``i`` and its antipode are exactly ``1-d``
            # apart), so the near arc bulges and the far arc recedes with no
            # seam. The closing point repeats point 0, and ``cos(360 deg)`` is
            # ``cos(0)``, so the join matches too.
            warped = []
            add = warped.append
            n = max(1, len(raw) - 1)
            # One smooth out-of-plane lobe per ring (phased by the ring's own
            # seed): each circle becomes a genuinely tilted 3D loop, so the
            # set never shares one plane and never collapses onto a single
            # diagonal together. A per-vertex hash would be noise (zigzags);
            # a sine lobe is a plane tilt (clean). Face-on the fade zeroes z
            # whatever this returns, so the declared ellipse is exact at rest.
            rphase = (2.0 * math.pi
                      * _field.hash01(_num(spec.get("seed"), 0.0),
                                     TURN_RING_SALT))
            for i, (px, py) in enumerate(raw):
                qd = 0.5 + TURN_RING_TILT * math.sin(
                    2.0 * math.pi * float(i) / float(n) + rphase)
                qd = min(1.0, max(0.0, qd))
                X, Y, _g = _tumble_project(
                    px - sway_dx, py - sway_dy, qd,
                    turn_cy, turn_sy, turn_cx, turn_sx, Rf)
                add((cx + X, cy + Y))
            if layer == "main":
                scale = (RING_ALPHA_SCALE["near_main"] if near
                         else RING_ALPHA_SCALE["far_main"])
            else:
                scale = RING_ALPHA_SCALE.get(layer, 1.0)
            # Echo followers stay FAINT GHOSTS of their orbit (a fixed alpha
            # band is their whole visual role and the follower contracts pin
            # it); every real circle additionally carries the travelling
            # accretion hot-spot, so it reads as live structure rather than a
            # dead outline.
            hot = 1.0 if layer == "echo" else _num(intensity, 1.0)
            _ring_stroke(warped,
                         _field.ring_alpha(tt, spec) * scale * bright * hot,
                         ring_w * _num(spec.get("width"), 1.0) * (
                             1.15 if layer == "main" else 1.0),
                         layer)

    def _dot(x: float, y: float, rad: float, alpha: float,
             rgb: Tuple[int, int, int], gain: Any = 1.0) -> None:
        a = _num(alpha, 0.0)
        r = _num(rad, 0.0)
        if a <= ALPHA_EPS or r <= 0.0:
            return
        tint = _tone(rgb, gain)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tint[0], tint[1], tint[2],
                          _clamp8(255.0 * min(1.0, a))))
        p.drawEllipse(x - r, y - r, r * 2.0, r * 2.0)

    def _outline(radius: float, rgb: Tuple[int, int, int], alpha: float,
                 width: float) -> None:
        a = _num(alpha, 0.0)
        if a <= ALPHA_EPS or radius <= 0.0:
            return
        path = QPainterPath()
        path.addEllipse(QRectF(cx - radius, cy - radius,
                               radius * 2.0, radius * 2.0))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                             _clamp8(255.0 * min(1.0, a))), width))
        p.drawPath(path)
        p.setPen(Qt.NoPen)

    def _streak(samples: Any, depth: Any = 0.5, gain: float = 1.0,
                half: float = TRAIL_HALF_WIDTH, zseed: Any = None) -> None:
        """One body's spectral wake, as a SINGLE tapered fill path.

        Owner 2026-10-18: a star should leave an RGB TRAIL, not sit inside an
        outer glow. So the wake is built as a tapered ribbon — the field's
        ``rgb_trail`` / ``dust_trail`` samples walked in order, each offset
        perpendicular to the local direction by its OWN half width — and filled
        with a linear gradient whose stops are ``hue_to_rgb`` per sample.

        Hue is never interpolated: it wraps like a colour wheel, so each stop
        is converted independently and only the resulting RGB blends between
        neighbours. A streak whose hue runs 0.98 -> 0.02 therefore takes the
        short way round the wheel instead of sweeping the entire spectrum the
        wrong way. One path per body keeps the primitive budget flat.

        The ribbon is built in SCREEN space, after the X+Y tumble projection,
        so the wake bends with the turn instead of cutting a straight chord
        across the mark: each sample carries its own depth, so its position,
        its half width and its alpha all track how far that part of the wake
        has swung toward or away from the viewer.
        """
        smps = [s for s in (samples or []) if isinstance(s, dict)]
        # A wake is a SHORT local motion blur, never a curved line crossing the
        # mark: only the head samples of the field's trail are drawn (owner
        # 2026-10-18). The head rides the body, so the stub stays glued to it.
        smps = smps[:TRAIL_DRAW_SAMPLES]
        if len(smps) < 2:
            return
        placed = []
        for s in smps:
            d = _num(s.get("depth"), depth)
            X, Y, g = _place(s.get("x"), s.get("y"), d, zseed=zseed,
                             zflat=True, z=s.get("z"), z2=s.get("z2"))
            placed.append((X, Y, d, g))
        pts = [(px, py) for px, py, _d, _g in placed]
        run = [0.0]
        for i in range(1, len(pts)):
            run.append(run[-1] + math.hypot(pts[i][0] - pts[i - 1][0],
                                            pts[i][1] - pts[i - 1][1]))
        total = run[-1]
        alphas = [_shade(_num(s.get("alpha"), 0.0) * gain, d, g)
                  for s, (_px, _py, d, g) in zip(smps, placed)]
        # A sub-pixel ribbon is one 8-bit step away from not existing, which is
        # the hard pop the field's C1 envelopes exist to avoid; and a fully
        # transparent path is a wasted primitive.
        if total < TRAIL_MIN_LEN_PX or max(alphas) <= ALPHA_EPS:
            return
        left: List[Tuple[float, float]] = []
        right: List[Tuple[float, float]] = []
        last = len(pts) - 1
        for i in range(len(pts)):
            j0 = i - 1 if i > 0 else 0
            j1 = i + 1 if i < last else last
            dx = pts[j1][0] - pts[j0][0]
            dy = pts[j1][1] - pts[j0][1]
            ln = math.hypot(dx, dy)
            if ln <= 1e-9:
                dx, dy, ln = 1.0, 0.0, 1.0
            w = max(TRAIL_MIN_HALF_PX,
                    _num(smps[i].get("size"), 0.0) * half * placed[i][3])
            nx, ny = -dy / ln, dx / ln
            left.append((pts[i][0] + nx * w, pts[i][1] + ny * w))
            right.append((pts[i][0] - nx * w, pts[i][1] - ny * w))
        path = QPainterPath()
        path.moveTo(left[0][0], left[0][1])
        for q in left[1:]:
            path.lineTo(q[0], q[1])
        for q in reversed(right):
            path.lineTo(q[0], q[1])
        path.closeSubpath()
        grad = QLinearGradient(QPointF(*pts[0]), QPointF(*pts[-1]))
        for i, smp in enumerate(smps):
            rgb = _anchor(_spec_rgb(smp.get("hue"), smp.get("sat", 1.0),
                                    smp.get("val", 1.0)))
            grad.setColorAt(min(1.0, run[i] / total),
                            QColor(rgb[0], rgb[1], rgb[2],
                                   _clamp8(255.0 * min(1.0, alphas[i]))))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)

    def _glint(fx: float, fy: float, size: float, alpha: float,
               rgb: Tuple[int, int, int],
               arm_frac: float = _field.GLINT_ARM_FRAC,
               skew_deg: Any = 0.0, depth: Any = 0.5,
               zseed: Any = None, zflat: bool = True,
               z: Any = None, z2: Any = None) -> None:
        """A twinkling star, never a filled orb (owner 2026-10-17).

        Two thin crossing arms plus a tiny core read as a glint at this size
        and stay crisp — a filled disc here was what made the field look like
        marbles. Shared by the nine logo particles and the nucleus sparks so
        the shower reads as the same kind of element as the field.

        ``fx``/``fy`` are FIELD coordinates (not painter pixels): the star is
        placed by :func:`_place`, so its own depth both positions it through
        the Y turn and scales it — a star swinging toward the viewer grows
        and brightens, one tucking away shrinks and dims. That per-element
        depth response is most of what makes the turn read as rotation
        rather than as a wobble.

        ``skew_deg`` is the field's per-depth shear: a star near the front of
        the orbital plane is raked one way, one near the back the other, and
        level at the rim. Rotating the cross (not moving it) keeps the element's
        own position exactly where the field put it while making it read as a
        surface catching light from an angle instead of a flat cross.
        """
        d = _num(depth, 0.5)
        gx, gy, g = _place(fx, fy, d, zseed=zseed, zflat=zflat, z=z, z2=z2)
        sz = max(GLINT_MIN_SIZE, _num(size, 0.0) * g)
        a = _num(alpha, 0.0) * bright * _shade(1.0, d, g)
        if a <= ALPHA_EPS:
            return
        tint = _tone(rgb, g)
        arm = sz * arm_frac
        skew = math.radians(_num(skew_deg, 0.0))
        ax, ay = math.cos(skew) * arm, math.sin(skew) * arm
        pen = QPen(QColor(tint[0], tint[1], tint[2], int(255 * min(1.0, a))))
        pen.setWidthF(GLINT_PEN_W)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(gx - ax, gy - ay), QPointF(gx + ax, gy + ay))
        p.drawLine(QPointF(gx - ay, gy + ax), QPointF(gx + ay, gy + ax))
        _dot(gx, gy, max(GLINT_CORE_MIN, sz * _field.GLINT_CORE_FRAC
                         * GLINT_CORE_SCALE), a, rgb, g)

    def _particle(part: Dict[str, Any], draw_trails: bool) -> None:
        depth = _num(part.get("depth"), 0.5)
        pseed = _num(part.get("index"), None)
        if draw_trails:
            _streak(_field.rgb_trail(tt, Rf, part), depth, zseed=pseed)
        _glint(_num(part.get("x"), 0.0), _num(part.get("y"), 0.0),
               _num(part.get("size"), 0.0),
               _shade(_num(part.get("alpha"), 0.0), depth),
               _lift(_num(part.get("lift"), _field.LIFT_BEAD)),
               skew_deg=part.get("skew"), depth=depth, zseed=pseed,
               z=part.get("z"), z2=part.get("z2"))


    def _satellite(shell: Dict[str, Any], draw_trail: bool) -> None:
        """One satellite body riding its declared shell circle.

        The circle itself is stroked by the declared-geometry ring pass; this
        is only the body travelling it — a glint sized by depth, leaving the
        same spectral wake every other travelling body leaves. The field's
        ``vx``/``vy`` are what put the wake on the body's real path, so the
        streak stays on the orbit instead of cutting a chord across it.
        """
        body = shell.get("body") or {}
        depth = _num(body.get("depth"), 0.5)
        bseed = _num(body.get("index"), None)
        if draw_trail:
            # The SHORT streak here, not the full ``rgb_trail``: a shell body
            # turns its shell once in 5.5-13.5 s, and the long streak's step is
            # sized for the comet, so the full one drew a ~70px near-straight
            # scratch right across the mark. The field's own ``vx``/``vy`` put
            # even the short one on the body's real path, tangent to the shell.
            _streak(_field.dust_trail(tt, Rf, body), depth,
                    half=SPARKLE_TRAIL_HALF_WIDTH, gain=SATELLITE_TRAIL_GAIN,
                    zseed=bseed)
        _glint(_num(body.get("x"), 0.0), _num(body.get("y"), 0.0),
               max(0.3, _num(body.get("size"), 0.0)),
               _shade(_num(body.get("alpha"), 0.0), depth),
               _lift(_num(body.get("lift"), _field.LIFT_BEAD)),
               skew_deg=body.get("skew"), depth=depth, zseed=bseed,
               z=body.get("z"), z2=body.get("z2"))

    def _spark(spark: Dict[str, Any]) -> None:
        """One nucleus spark: the same glint shape, thrown out past the orbits."""
        depth = _num(spark.get("depth"), 0.5)
        _glint(_num(spark.get("x"), 0.0), _num(spark.get("y"), 0.0),
               _num(spark.get("size"), 0.0) * NUCLEUS_SPARK_DRAW_SCALE,
               _shade(_num(spark.get("alpha"), 0.0), depth),
               _lift(_num(spark.get("lift"), _field.LIFT_NUCLEUS)),
               arm_frac=_field.SPARK_ARM_FRAC,
               skew_deg=spark.get("skew"), depth=depth,
               zseed=_num(spark.get("index"), None),
               z=spark.get("z"), z2=spark.get("z2"))

    def _dust(mote: Dict[str, Any]) -> None:
        # No wake for the dense field: 30 motes each dragging a streak turns
        # the field into 30 comets. The motes keep the mark's depth read; the
        # travelling BODIES carry the spectrum.
        d = _num(mote.get("depth"), 0.5)
        mx, my, g = _place(mote.get("x"), mote.get("y"), d,
                           zseed=_num(mote.get("index"), None),
                           zflat=True, z=mote.get("z"), z2=mote.get("z2"))
        _dot(mx, my,
             max(0.25, _num(mote.get("size"), 0.0) * g),
             _shade(_num(mote.get("alpha"), 0.0) * bright, d, g),
             _lift(mote.get("lift")), g)

    def _sparkle(glint: Dict[str, Any], draw_trail: bool) -> None:
        depth = _num(glint.get("depth"), 0.5)
        if draw_trail:
            _streak(_field.dust_trail(tt, Rf, glint), depth,
                    gain=SPARKLE_TRAIL_GAIN,
                    half=SPARKLE_TRAIL_HALF_WIDTH,
                    zseed=_num(glint.get("index"), None))
        # The sparkle's DOT is not drawn here: the many field glints are
        # emitted as a few batched fill paths (_layer_batch) so the primitive
        # ceiling holds while the cloud stays as dense. Its wake is personal
        # and stays per-body.

    def _comet(traveller: Dict[str, Any], draw_trail: bool) -> None:
        if _num(traveller.get("alpha"), 0.0) <= 0.0:
            return  # outside its sweep the field reports alpha 0
        depth = _num(traveller.get("depth"), 0.5)
        tseed = _num(traveller.get("index"), 0.0)
        if draw_trail:
            _streak(_field.rgb_trail(tt, Rf, traveller), depth,
                    gain=COMET_TRAIL_GAIN, half=COMET_TRAIL_HALF_WIDTH,
                    zseed=tseed)
        # The field's own bead tail stays, tinted by the streak's duotone PAIR:
        # bright motion-blur fragments riding inside the wake. The head hue
        # and its half-turn partner are the streak's two colours (the old
        # HUE_STEP ramp walked the whole wheel and read as a third, untinted
        # grey-to-rainbow tail beside the wake). Left untinted it read as a
        # second, grey tail next to a rainbow one.
        hue_h = _num(traveller.get("hue"), 0.0)
        hue2_t = _num(traveller.get("hue2"), (hue_h + 0.5) % 1.0)
        head_hue = (hue_h + _num(tt, 0.0)
                    * _field.RGB_TRAIL_HUE_DRIFT) % 1.0
        # The pair's span, re-derived from the two endpoints (half a turn by
        # construction): the beads walk the SAME two colours as the streak.
        pair_span = (hue2_t - hue_h) % 1.0
        trail = list(traveller.get("trail") or [])
        n_beads = max(1, len(trail))
        for k, smp in enumerate(trail):
            hue = (head_hue + ((k + 1) / float(n_beads)) * pair_span) % 1.0
            bx, by, bg = _place(smp.get("x"), smp.get("y"), depth,
                                zseed=tseed, zflat=True,
                                z=smp.get("z"), z2=smp.get("z2"))
            _dot(bx, by,
                 _num(smp.get("size"), 0.0) * bg,
                 _shade(_num(smp.get("alpha"), 0.0) * bright, depth, bg),
                 _anchor(_spec_rgb(hue, COMET_BEAD_SAT)), bg)
        hx, hy, hg = _place(traveller.get("x"), traveller.get("y"), depth,
                            zseed=tseed, zflat=True,
                            z=traveller.get("z"), z2=traveller.get("z2"))
        _dot(hx, hy,
             max(0.4, _num(traveller.get("size"), 0.0) * hg),
             _shade(_num(traveller.get("alpha"), 0.0) * bright, depth, hg),
             _anchor(_spec_rgb(head_hue, COMET_BEAD_SAT)), hg)

    def _smoke(ox: float, oy: float, rad: float, rgb: Tuple[int, int, int],
               peak: float, power: float = NUCLEUS_AURA_POWER) -> None:
        """One soft smoke blob: a long power falloff, so it has no rim.

        Sampling the falloff at many stops is what removes the visible edge a
        3-stop gradient leaves: the alpha decays smoothly to zero instead of
        holding a plateau beside the body and then dropping.
        """
        rr = max(0.5, rad)
        g = QRadialGradient(QPointF(ox, oy), rr)
        for i in range(NUCLEUS_AURA_STOPS + 1):
            f = i / float(NUCLEUS_AURA_STOPS)
            a = peak * (1.0 - f) ** power
            g.setColorAt(f, QColor(rgb[0], rgb[1], rgb[2],
                                   _clamp8(255.0 * a)))
        path = QPainterPath()
        path.addEllipse(QRectF(ox - rr, oy - rr, rr * 2.0, rr * 2.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(path)

    def _nucleus(radius: float, rgb: Tuple[int, int, int], tt: float,
                   beat: float = 0.0) -> None:
        """The mark's ONE solid body, shaded instead of a flat disc.

        A near-white hot core falls through the state hue to a transparent
        rim, wrapped in a smoky aura, with a small off-centre specular
        highlight. The body is the only filled ellipse at the centre; the aura,
        the corona, the swirl lobes and the highlight are gradient paths beside
        it. On top of the heartbeat breathing (the caller's radius) the body
        carries two counter-rotating inner swirl lobes, a slowly orbiting
        specular dot, a CHROMATIC CORONA turning through the spectrum outside
        the rim, and a flare plus an inner caustic that fire on the thump and
        decay with it — so the nucleus reads as a living, radiating core rather
        than a static ball. Every term is a pure function of the clamped clock,
        so reduced motion paints the identical still body.
        """
        r = _num(radius, 0.0)
        if r <= 0.0:
            return
        hot = _lift(NUCLEUS_CORE_LIFT)
        ck = _num(tt, 0.0)
        bt = _num(beat, 0.0)
        # Chromatic corona first, so the white aura and the body sit ON it and
        # it reads as light radiated by the core rather than a haze lying over
        # it. Hue comes from the field's wheel and turns slowly: fast enough to
        # be visibly chromatic, slow enough that the three lobes read as one
        # field of colour instead of a rotor.
        for i in range(NUCLEUS_CORONA_COUNT):
            ang = math.radians(360.0 * ck / NUCLEUS_CORONA_PERIOD_S
                               + 360.0 * i / NUCLEUS_CORONA_COUNT
                               + NUCLEUS_CORONA_PHASE_DEG)
            breath = 1.0 - NUCLEUS_CORONA_BREATH_SPAN * 0.5 + \
                NUCLEUS_CORONA_BREATH_SPAN * 0.5 * math.sin(
                    2.0 * math.pi * ck / NUCLEUS_CORONA_BREATH_S + i * 1.7)
            dist = r * NUCLEUS_CORONA_RADIUS_FRAC * breath
            # No local spectrum walk: blend between the FIELD's two palette
            # hues, with each lobe phase-shifted so they alternate ends of the
            # band. Restrained by construction — the corona can only ever be
            # the mark's two cool tints, never a rainbow rotor.
            shades = tuple(_field.PALETTE_HUES) or (0.54, 0.60)
            lo, hi = shades[0], shades[-1]
            mix = 0.5 + 0.5 * math.sin(
                2.0 * math.pi * ck / max(0.1, NUCLEUS_CORONA_HUE_PERIOD_S)
                + 2.1 * i)
            hue = (lo + (hi - lo) * mix) % 1.0
            _smoke(cx + dist * math.cos(ang),
                   cy + dist * math.sin(ang) * NUCLEUS_CORONA_SQUASH,
                   max(0.6, r * NUCLEUS_CORONA_SIZE_FRAC * breath),
                   _anchor(_spec_rgb(hue, VOLUME_PARTICLE_SAT)),
                   NUCLEUS_CORONA_ALPHA * breath,
                   power=NUCLEUS_CORONA_POWER)
        # Smoky aura: a wide base falloff plus slow drifting plumes, so the
        # surround is soft and irregular instead of a hard-edged disc.
        _smoke(cx, cy, r * NUCLEUS_GLOW_MULT, rgb, NUCLEUS_GLOW_ALPHA)
        for orbit_frac, size_frac, period, phase, scale in NUCLEUS_SMOKE:
            ang = math.radians(360.0 * ck / period + phase)
            _smoke(cx + r * orbit_frac * math.cos(ang),
                   cy + r * orbit_frac * math.sin(ang) * 0.72,
                   r * NUCLEUS_GLOW_MULT * size_frac,
                   hot, NUCLEUS_GLOW_ALPHA * scale)
        if bt > 0.0:
            # Beat flare: a hot bloom that fires on the thump and decays with
            # it. A FILL, never a hairline, so it can never be confused with the
            # shockwave's expanding ring.
            _smoke(cx, cy,
                   max(0.6, r * NUCLEUS_FLARE_RADIUS_FRAC * (0.45 + 0.75 * bt)),
                   hot, NUCLEUS_FLARE_ALPHA * bt)
        body = QRadialGradient(QPointF(cx, cy), r)
        body.setColorAt(0.0, QColor(hot[0], hot[1], hot[2], 255))
        body.setColorAt(0.34, QColor(rgb[0], rgb[1], rgb[2], 250))
        body.setColorAt(0.70, QColor(rgb[0], rgb[1], rgb[2], 200))
        body.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
        p.setBrush(QBrush(body))
        p.drawEllipse(cx - r, cy - r, r * 2.0, r * 2.0)
        # Fresnel rim: a thin BRIGHT limb stroke at the body edge — a sphere
        # is brightest at grazing incidence — plus a faint chromatic split
        # (the same red/blue hairline pair as the main orbits' fringe, at the
        # same pixel offset, far dimmer). Static art direction, so stills stay
        # self-consistent; physical light cues only, no scanlines, no flicker.
        _outline(r, (255, 255, 255), NUCLEUS_RIM_ALPHA, NUCLEUS_RIM_WIDTH)
        for dx, col in ((fringe, (255, 45, 85)), (-fringe, (45, 130, 255))):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(col[0], col[1], col[2],
                                 _clamp8(255.0 * NUCLEUS_CHROMA_ALPHA)),
                          NUCLEUS_RIM_WIDTH * 0.7))
            p.translate(dx, 0.0)
            rim = QPainterPath()
            rim.addEllipse(QRectF(cx - r, cy - r, r * 2.0, r * 2.0))
            p.drawPath(rim)
            p.translate(-dx, 0.0)
            p.setPen(Qt.NoPen)
        if bt > 0.0:
            # Caustic: the boiling highlight inside the core. Between beats it
            # never vanishes entirely, so the centre keeps a live inner light
            # while the flare above it is the thump.
            craw = math.radians(360.0 * ck / NUCLEUS_CAUSTIC_PERIOD_S)
            ca = (NUCLEUS_CAUSTIC_ALPHA_BASE
                  + NUCLEUS_CAUSTIC_ALPHA * bt
                  * (0.5 + 0.5 * math.sin(2.0 * craw)))
            _smoke(cx + r * 0.16 * math.cos(craw),
                   cy + r * 0.16 * math.sin(craw),
                   max(0.6, r * NUCLEUS_CAUSTIC_RADIUS_FRAC), hot, ca)
        # Inner swirl: two counter-rotating hot lobes inside the body. They
        # give the centre internal motion the flat gradient could never have,
        # and they open on the beat so the core's internal motion is louder on
        # the thump than between beats.
        swirl_alpha = min(1.0, NUCLEUS_SWIRL_ALPHA
                          * (1.0 + NUCLEUS_SWIRL_BEAT_GAIN * bt))
        for orbit_frac, size_frac, period, phase in (
                (NUCLEUS_SWIRL_RADIUS_FRAC, NUCLEUS_SWIRL_SIZE_FRAC,
                 NUCLEUS_SWIRL_PERIOD_S, 0.0),
                (NUCLEUS_SWIRL_RADIUS2_FRAC, NUCLEUS_SWIRL_SIZE2_FRAC,
                 NUCLEUS_SWIRL_PERIOD2_S, 180.0)):
            ang = math.radians(360.0 * _num(tt, 0.0) / period + phase)
            lx = cx + r * orbit_frac * math.cos(ang)
            ly = cy + r * orbit_frac * math.sin(ang)
            lr = max(0.6, r * size_frac)
            lobe = QRadialGradient(QPointF(lx, ly), lr)
            lobe.setColorAt(0.0, QColor(255, 255, 255,
                                        _clamp8(255.0 * swirl_alpha)))
            lobe.setColorAt(0.6, QColor(hot[0], hot[1], hot[2],
                                        _clamp8(255.0 * swirl_alpha
                                                * 0.45)))
            lobe.setColorAt(1.0, QColor(hot[0], hot[1], hot[2], 0))
            lpath = QPainterPath()
            lpath.addEllipse(QRectF(lx - lr, ly - lr, lr * 2.0, lr * 2.0))
            p.setBrush(QBrush(lobe))
            p.drawPath(lpath)
        # Specular highlight, carried by the combined tumble. The body is
        # exempt from that tumble (a sphere's silhouette is a circle from every
        # angle), but it is NOT exempt from rotating: the light is fixed in the
        # room and the sphere turns under it, so the highlight sweeps across
        # the face on both axes — off to one side at face-on, across the
        # centre and to the far side as each axis turns, and back. A static
        # off-centre highlight on a turning body reads as a flat disc with a
        # smudge on it; a sweeping one is what makes the core read as a sphere
        # that is turning.
        sr = r * NUCLEUS_SPEC_RADIUS_FRAC
        # ``cos_y``/``cos_x`` are the tumble's two cosines: +1 face-on, -1
        # mirrored. The highlight therefore crosses the centre on both axes
        # across a combined turn and is exactly where it was on a still frame
        # (both phases 0). A static off-centre highlight on a turning body
        # reads as a flat disc with a smudge on it; a sweeping one is what
        # makes the core read as a sphere that is turning.
        sx = cx - r * NUCLEUS_SPEC_OFFSET_FRAC * cos_y
        sy = cy - r * NUCLEUS_SPEC_OFFSET_FRAC * cos_x
        spec = QRadialGradient(QPointF(sx, sy), sr)
        spec.setColorAt(0.0, QColor(255, 255, 255,
                                    _clamp8(255.0 * NUCLEUS_SPEC_ALPHA)))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        spath = QPainterPath()
        spath.addEllipse(QRectF(sx - sr, sy - sr, sr * 2.0, sr * 2.0))
        p.setBrush(QBrush(spec))
        p.drawPath(spath)
        # Orbiting specular: a small hot dot running once around the body per
        # period — the cue that reads as rotation rather than pulsing.
        pang = math.radians(360.0 * _num(tt, 0.0) / NUCLEUS_SPECULAR_PERIOD_S
                            + NUCLEUS_SPECULAR_PHASE_DEG)
        pr = max(0.5, r * NUCLEUS_SPECULAR_SIZE_FRAC)
        px = cx + r * NUCLEUS_SPECULAR_ORBIT_FRAC * math.cos(pang)
        py = cy + r * NUCLEUS_SPECULAR_ORBIT_FRAC * math.sin(pang)
        pd = QRadialGradient(QPointF(px, py), pr * 2.0)
        pd.setColorAt(0.0, QColor(255, 255, 255,
                                  _clamp8(255.0 * NUCLEUS_SPECULAR_ALPHA)))
        pd.setColorAt(0.5, QColor(255, 255, 255,
                                  _clamp8(255.0 * NUCLEUS_SPECULAR_ALPHA
                                          * 0.35)))
        pd.setColorAt(1.0, QColor(255, 255, 255, 0))
        ppath = QPainterPath()
        ppath.addEllipse(QRectF(px - pr * 2.0, py - pr * 2.0, pr * 4.0,
                                pr * 4.0))
        p.setBrush(QBrush(pd))
        p.drawPath(ppath)
        p.setBrush(Qt.NoBrush)

    def _binary(bit: Dict[str, Any]) -> None:
        """One "1"/"0" fragment as a small glyph at its field position.

        Sized from the field's own ``size`` so the fragments scale with the
        orb, and tinted by ``lift`` so they sit in the same palette as the
        particles instead of reading as pasted-in text. Placed and scaled
        through the Y turn like every other body, so a fragment on the near
        half of the plane grows and brightens as it swings toward the viewer.
        """
        d = _num(bit.get("depth"), 0.5)
        gx, gy, g = _place(bit.get("x"), bit.get("y"), d,
                           zseed=_num(bit.get("index"), None),
                           zflat=True, z=bit.get("z"), z2=bit.get("z2"))
        size = _num(bit.get("size"), 0.0)
        alpha = _num(bit.get("alpha"), 0.0) * bright * g
        if alpha <= ALPHA_EPS or size <= 0.0:
            return
        rgb = _lift(_num(bit.get("lift"), 0.5))
        # Glyphs use the PEN, not the brush; the brush is already NoBrush here.
        _draw_glyph(gx, gy, str(bit.get("char") or "0"), rgb, alpha,
                    max(BINARY_FONT_MIN_PX,
                        int(size * g * BINARY_FONT_PX_PER_UNIT
                            + BINARY_FONT_PX_OFFSET)))

    def _volume_particle(body: Dict[str, Any]) -> None:
        """One 3D volume particle as a round dot (never text, never an arm).

        The field's ``size``/``alpha`` already carry the body's own pulse, and
        its ``z``/``z2`` axes displace it through the volume; placing it through
        ``_place`` gives it the tumble's depth size/alpha gain too. A DOT is
        deliberate: the retired math layer's glyph strokes (and the glint arms
        used elsewhere) are exactly what read as "check marks / v lines", so a
        volume body has no line at all.
        """
        d = _num(body.get("depth"), 0.5)
        vx, vy, g = _place(body.get("x"), body.get("y"), d,
                           zseed=_num(body.get("index"), None), zflat=True,
                           z=body.get("z"), z2=body.get("z2"))
        size = _num(body.get("size"), 0.0)
        alpha = _num(body.get("alpha"), 0.0) * bright * _shade(1.0, d, g)
        if alpha <= ALPHA_EPS or size <= 0.0:
            return
        hue = body.get("hue")
        rgb = (_anchor(_spec_rgb(hue, VOLUME_PARTICLE_SAT))
               if hue is not None
               else _lift(_num(body.get("lift"), 0.5)))
        _dot(vx, vy, max(BLINK_DOT_MIN_PX, size * g), alpha, rgb, g)

    def _photon_dot(smp: Dict[str, Any]) -> None:
        """One bright sample of the lensed photon ring, a hot bead.

        The ring is a LENSED overlay hugging the core, so it is drawn over the
        nucleus and its samples are enlarged/brightened in the painter (the
        field's own sizes are calibrated for the field scale and would vanish
        into the aura at orb size).
        """
        d = _num(smp.get("depth"), 0.5)
        px, py, g = _place(smp.get("x"), smp.get("y"), d,
                           zseed=_num(smp.get("index"), None), zflat=True,
                           z=smp.get("z"), z2=smp.get("z2"))
        size = _num(smp.get("size"), 0.0) * PHOTON_SIZE_MULT
        alpha = (min(1.0, _num(smp.get("alpha"), 0.0) * PHOTON_ALPHA_MULT)
                 * bright * _shade(1.0, d, g))
        if alpha <= ALPHA_EPS or size <= 0.0:
            return
        hue = smp.get("hue")
        rgb = (_anchor(_spec_rgb(hue, PHOTON_RING_SAT))
               if hue is not None
               else _lift(_num(smp.get("lift"), 0.5)))
        _dot(px, py, max(BLINK_DOT_MIN_PX, size * g), alpha, rgb, g)

    def _screen_break(body: Dict[str, Any]) -> None:
        """One body the field placed BEYOND the orb radius.

        Owner: "i like it how it tries to break the screen, do more of that".
        Drawn as a glint (two pinprick arms plus a core) at its own field
        position, through the same tumble as every other body. Nothing here
        clips to the orb's circle, so a body the field pushes past ``R`` — or
        even past the widget edge — is still painted; the overflow is the look.
        """
        depth = _num(body.get("depth"), 0.5)
        hue = body.get("hue")
        rgb = (_anchor(_spec_rgb(hue, SCREEN_BREAK_SAT))
               if hue is not None
               else _lift(_num(body.get("lift"), 0.5)))
        # ``_glint`` places internally through ``_place`` (field coords in), so
        # a body the field pushed past ``R`` is projected like any other. The
        # coords are additionally pushed OUT by ``SCREEN_BREAK_PUSH``: the
        # tumble's perspective term pulls a far body back in, which would keep
        # some of these bodies inside the circle. Scaling the field position
        # keeps the deliberate overflow (owner: "do more of that") without
        # touching the field's own declared geometry.
        push = max(1.0, _num(SCREEN_BREAK_PUSH, 1.0))
        _glint(_num(body.get("x"), 0.0) * push,
               _num(body.get("y"), 0.0) * push,
               body.get("size"), body.get("alpha"), rgb,
               skew_deg=body.get("skew"), depth=depth,
               zseed=_num(body.get("index"), None), zflat=True,
               z=body.get("z"), z2=body.get("z2"))

    def _blink(bl: Dict[str, Any]) -> None:
        """One blink dot: a pinprick that materialises out of nowhere.

        No streak, no halo — the field's alpha envelope is 0→peak→0 with the
        dot fixed in place, so it appears and vanishes where it sits. Placed
        and scaled through the combined tumble like every other body, tinted
        by its own spectral hue on the shared colour wheel.
        """
        d = _num(bl.get("depth"), 0.5)
        bx, by, g = _place(bl.get("x"), bl.get("y"), d,
                           zseed=_num(bl.get("index"), None),
                           zflat=True, z=bl.get("z"), z2=bl.get("z2"))
        size = _num(bl.get("size"), 0.0)
        alpha = _num(bl.get("alpha"), 0.0) * bright * g
        if alpha <= ALPHA_EPS or size <= 0.0:
            return
        hue = _num(bl.get("hue"), 0.0)
        rgb = _anchor(_spec_rgb(hue, BLINK_DOT_SAT))
        _dot(bx, by, max(BLINK_DOT_MIN_PX, size * g), alpha, rgb, g)

    def _core_body(shell: Dict[str, Any]) -> None:
        """One nested interior body of the nucleus, as a tilted shaded disc.

        Sized and brightened by its rotated depth (a shell swinging toward the
        viewer swells and brightens), tinted by its own spectral hue and pulled
        toward the core's hot colour at the centre. The shell's own plane tilt is
        folded into the drawn ellipse's minor axis and a thin bright rim, so a
        stack of them reads as layered, angled discs INSIDE the sphere — depth
        you look INTO. Drawn as ``drawPath`` gradients + rims, never a
        ``drawEllipse``: the nucleus body must remain the mark's only solid
        ellipse.
        """
        d = _num(shell.get("depth"), 0.5)
        gx, gy, g = _place(shell.get("x"), shell.get("y"), d,
                           zseed=_num(shell.get("index"), None),
                           zflat=True, z=shell.get("z"), z2=shell.get("z2"))
        rr = max(CORE_SHELL_MIN_PX, _num(shell.get("size"), 0.0) * g
                 * CORE_SHELL_SIZE_MULT)
        a = _shade(_num(shell.get("alpha"), 0.0), d, g) * bright
        if a <= ALPHA_EPS or rr <= 0.0:
            return
        tilt = math.radians(_num(shell.get("tilt"), 0.0)
                            + _num(shell.get("spin_deg"), 0.0))
        ry = max(CORE_SHELL_MIN_PX, rr * (0.30 + 0.70 * abs(math.cos(tilt))))
        rgb = _anchor(_spec_rgb(_num(shell.get("hue"), 0.0), CORE_SHELL_SAT))
        hot = _lift(CORE_SHELL_CORE_LIFT)
        grad = QRadialGradient(QPointF(gx, gy), max(rr, ry))
        grad.setColorAt(0.0, QColor(hot[0], hot[1], hot[2],
                                    _clamp8(255.0 * min(1.0, a))))
        grad.setColorAt(0.42, QColor(rgb[0], rgb[1], rgb[2],
                                     _clamp8(255.0 * min(1.0, a) * 0.9)))
        grad.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
        path = QPainterPath()
        path.addEllipse(QRectF(gx - rr, gy - ry, rr * 2.0, ry * 2.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(path)
        # A thin bright limb: the shell reads as a BODY with an edge, which is
        # what makes the stack of them read as layered depth rather than haze.
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                             _clamp8(255.0 * min(1.0, a) * 0.8)),
                      CORE_SHELL_RIM_W))
        p.drawPath(path)
        p.setPen(Qt.NoPen)

    def _project_body(body: Dict[str, Any]) -> Tuple[float, float, float]:
        """``(px, py, size_px)`` of one body through place + rotated depth."""
        d = _num(body.get("depth"), 0.5)
        px, py, g = _place(body.get("x"), body.get("y"), d,
                           zseed=_num(body.get("index"), None), zflat=True,
                           z=body.get("z"), z2=body.get("z2"))
        return px, py, max(0.0, _num(body.get("size"), 0.0) * g)

    def _layer_batch(items: Any, buckets: int, arm_frac: float = 0.0
                     ) -> None:
        """Draw MANY field bodies as a FEW batched paths, bucketed by hue.

        The field's new layers return dozens of bodies (ring particles on
        every circle, the cylinder column, the flare family). Drawing each
        body as its own primitive would cross the mark's fixed primitive
        ceiling, so bodies are collected into ``buckets`` spectral groups and
        each group is emitted as ONE fill path (plus, when ``arm_frac`` > 0,
        ONE stroke path for glint arms). The cloud reads dense and spectral
        while the per-frame primitive cost stays flat.
        """
        n = max(1, int(buckets))
        groups: List[List[Tuple[float, float, float, float, float]]] = [
            [] for _ in range(n)]
        for b in items:
            if not isinstance(b, dict):
                continue
            d = _num(b.get("depth"), 0.5)
            alpha = _shade(_num(b.get("alpha"), 0.0), d) * bright
            px, py, size = _project_body(b)
            if alpha <= ALPHA_EPS or size <= 0.0:
                continue
            hue = max(0.0, min(0.999, _num(b.get("hue"), 0.0)))
            k = int(hue * n) % n
            groups[k].append((px, py, max(BLINK_DOT_MIN_PX, size), alpha,
                              hue))
        for k, pts in enumerate(groups):
            if not pts:
                continue
            # The bucket's colour is a REAL hue the field emitted (its first
            # member's), never a renderer-invented ``(k + 0.5) / n`` spread:
            # the field owns the palette now, so the painter must not paint a
            # hue the field never declared.
            rgb = _anchor(_spec_rgb(pts[0][4], BLINK_DOT_SAT))
            if arm_frac > 0.0:
                stroke = QPainterPath()
                for px, py, size, _a, _h in pts:
                    arm = size * arm_frac
                    stroke.moveTo(px - arm, py - arm)
                    stroke.lineTo(px + arm, py + arm)
                    stroke.moveTo(px - arm, py + arm)
                    stroke.lineTo(px + arm, py - arm)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                     _clamp8(255.0 * max(
                                         a for _x, _y, _s, a, _h in pts))),
                              GLINT_PEN_W))
                p.drawPath(stroke)
                p.setPen(Qt.NoPen)
            path = QPainterPath()
            for px, py, size, _a, _h in pts:
                rr = size * (GLINT_CORE_SCALE if arm_frac > 0.0 else 1.0)
                rr = max(GLINT_CORE_MIN, rr)
                path.addEllipse(QRectF(px - rr, py - rr, rr * 2.0, rr * 2.0))
            peak = max(a for _x, _y, _s, a, _h in pts)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(rgb[0], rgb[1], rgb[2], _clamp8(255.0 * peak)))
            p.drawPath(path)
            p.setBrush(Qt.NoBrush)

    def _cylinder_batch(items: Any, buckets: int) -> None:
        """The vertical column, batched; bodies dim toward the column ends."""
        n = max(1, int(buckets))
        groups: List[List[Tuple[float, float, float, float, float]]] = [
            [] for _ in range(n)]
        for b in items:
            if not isinstance(b, dict):
                continue
            d = _num(b.get("depth"), 0.5)
            hh = abs(_num(b.get("h"), 0.0)) / (Rf if Rf > 0.0 else 1.0)
            dim = 1.0 - CYLINDER_HEIGHT_DIM * min(1.0, hh)
            alpha = _shade(_num(b.get("alpha"), 0.0) * dim, d) * bright
            px, py, size = _project_body(b)
            if alpha <= ALPHA_EPS or size <= 0.0:
                continue
            hue = max(0.0, min(0.999, _num(b.get("hue"), 0.0)))
            k = int(hue * n) % n
            groups[k].append((px, py, max(BLINK_DOT_MIN_PX, size), alpha,
                              hue))
        for k, pts in enumerate(groups):
            if not pts:
                continue
            # A real field hue (its first member's), never a spread the field
            # did not declare.
            rgb = _anchor(_spec_rgb(pts[0][4], BLINK_DOT_SAT))
            path = QPainterPath()
            for px, py, size, _a, _h in pts:
                path.addEllipse(QRectF(px - size, py - size,
                                       size * 2.0, size * 2.0))
            peak = max(a for _x, _y, _s, a, _h in pts)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(rgb[0], rgb[1], rgb[2], _clamp8(255.0 * peak)))
            p.drawPath(path)
            p.setBrush(Qt.NoBrush)

    def _filament(smp: Dict[str, Any]) -> None:
        """One sample of the travelling energy filament, a pulsing dot."""
        d = _num(smp.get("depth"), 0.5)
        fx, fy, g = _place(smp.get("x"), smp.get("y"), d,
                           zseed=_num(smp.get("index"), None), zflat=True,
                           z=smp.get("z"), z2=smp.get("z2"))
        size = _num(smp.get("size"), 0.0)
        alpha = _num(smp.get("alpha"), 0.0) * bright * g
        if alpha <= ALPHA_EPS or size <= 0.0:
            return
        rgb = _anchor(_spec_rgb(_num(smp.get("hue"), 0.0), BLINK_DOT_SAT))
        _dot(fx, fy, max(BLINK_DOT_MIN_PX, size), alpha, rgb, g)

    tt = _mark_tt(t)
    reduced = _reduced_motion()
    # The combined X+Y tumble's phases, bound here now that the clamped clock
    # exists. They are NOT pushed as a painter transform: a transform squashes
    # a flat picture, which is the thin collapse the owner rejected. Instead
    # every drawn point is projected through ``tumble_point`` (see ``_place``),
    # and the only two things the phases reach directly are the nucleus's
    # specular highlight (a fixed light, a turning sphere) and this variable's
    # single read. Exactly ``(0, 0)`` at a still clock, so a still frame is
    # the unturned mark.
    cos_y = mark_horizontal_spin(tt)
    cos_x = math.cos(mark_vertical_theta(tt))
    # The tumble's phases, resolved ONCE for the frame: every point below
    # shares them, so the trigonometry is not repeated a thousand times per
    # frame.
    turn_cy, turn_sy, turn_cx, turn_sx = _tumble_basis(tt)
    ring_specs = mark_ring_geometry(tt)
    sway_dx, sway_dy = _field.camera_offset(tt, Rf)
    # Echo follower rings and the nucleus spark shower both come from the
    # shared field and both vanish under reduced motion, so a still frame is
    # just the main orbits, the core ring, the glints and the resting nucleus.
    echoes = mark_echo_rings(t)
    # The two echo entries declare a DOUBLE-lagged follower tilt (the field
    # composes ``echo_rings(tt - ECHO_LAG_S)`` inside ``ring_geometry``), while
    # the shared echo contract everywhere else — ``mark_field.echo_rings``, the
    # Tk lane and the echo contract test — is the SINGLE-lag follower. Take the
    # follower tilt from that same declaration so both lanes agree on where a
    # follower sits, without re-deriving any geometry here.
    echo_tilt: Dict[str, float] = {}
    for echo in echoes:
        echo_tilt[str(echo.get("orbit") or "")] = _num(echo.get("tilt"), 0.0)

    parts = _field.particle_state(tt, Rf)
    dust = [] if reduced else _field.dust_state(tt, Rf)
    glints = [] if reduced else _field.sparkles(tt, Rf)
    traveller = None if reduced else _field.comet(tt, Rf)
    sparks = mark_nucleus_sparks(t, Rf)
    shells = mark_satellites(t, Rf)
    # Interior core shells, back to front: the nucleus's own nested bodies. The
    # field declares them all depths; sorting by depth and drawing after the
    # body is what makes the core read as layered interior depth.
    core_shells = sorted(mark_core_shells(t, Rf),
                         key=lambda s: _num(s.get("depth"), 0.5)
                         )[:max(0, int(CORE_SHELL_DRAW_LIMIT))]
    far_shells = [s for s in shells
                  if _num((s.get("body") or {}).get("depth"), 0.0) < 0.5]
    near_shells = [s for s in shells
                   if _num((s.get("body") or {}).get("depth"), 0.0) >= 0.5]
    far_sparks = [s for s in sparks
                  if _num(s.get("depth"), 0.0) < 0.5]
    near_sparks = [s for s in sparks
                   if _num(s.get("depth"), 0.0) >= 0.5]
    binaries = mark_binaries(tt, Rf)
    far_binaries = [b for b in binaries if _num(b.get("depth"), 0.0) < 0.5]
    near_binaries = [b for b in binaries if _num(b.get("depth"), 0.0) >= 0.5]
    # 3D volume particles: the math-glyph TEXT layer's replacement. Real
    # bodies with their own pulse/spin; drawn as round dots, never glyphs.
    # Split by field depth exactly like every other body, so the field is a
    # genuine volume behind and in front of the nucleus.
    volumes = strided(mark_volume_particles(tt, Rf), VOLUME_DRAW_LIMIT)
    far_volumes = [v for v in volumes if _num(v.get("depth"), 0.0) < 0.5]
    near_volumes = [v for v in volumes if _num(v.get("depth"), 0.0) >= 0.5]
    # Black-hole structure (owner: the circles "look like blackholes"): a live
    # spiral accretion stream, the lensed photon ring hugging the core, and
    # matter falling inward. All are dot families batched by hue so they add
    # visible structure without crossing the primitive ceiling.
    accretion = ([] if reduced
                 else strided(mark_accretion_stream(tt, Rf),
                              ACCRETION_DRAW_LIMIT))
    photons = ([] if reduced
               else [p for p in strided(mark_photon_ring(tt, Rf),
                                        PHOTON_DRAW_LIMIT)
                     if _num(p.get("alpha"), 0.0) >= PHOTON_MIN_ALPHA])
    infall = ([] if reduced
              else strided(mark_infall_streaks(tt, Rf), INFALL_DRAW_LIMIT))
    # Screen-break bodies: the field pushes a handful of bodies past the orb's
    # radius. They are drawn, unclipped, so the mark "breaks the screen".
    sbreaks = strided(mark_screen_break(tt, Rf), SCREEN_BREAK_DRAW_LIMIT)
    far_sbreaks = [b for b in sbreaks if _num(b.get("depth"), 0.0) < 0.5]
    near_sbreaks = [b for b in sbreaks if _num(b.get("depth"), 0.0) >= 0.5]
    blinks = mark_blink_dots(tt, Rf)
    far_blinks = [b for b in blinks if _num(b.get("depth"), 0.0) < 0.5]
    near_blinks = [b for b in blinks if _num(b.get("depth"), 0.0) >= 0.5]
    # The new rich layers: bodies on every ring, a vertical cylinder around the
    # nucleus, a rarer flare family and the travelling energy filament. Each is
    # strided to a fixed cap, so the frame gains life without unbounded work.
    ring_parts = ([] if reduced
                  else strided(mark_ring_particles(t, Rf),
                               RING_PARTICLE_DRAW_LIMIT))
    cyl_parts = ([] if reduced
                 else strided(mark_cylinder_particles(t, Rf),
                              CYLINDER_DRAW_LIMIT))
    orbiters = ([] if reduced
                else strided(mark_orbiter_particles(t, Rf),
                             ORBITER_DRAW_LIMIT))
    flares = ([] if reduced else strided(mark_flare_dots(t, Rf), FLARE_DRAW_LIMIT))
    filament = ([] if reduced
                else strided(mark_filament(t, Rf), FILAMENT_DRAW_COUNT))
    draw_trails = not reduced

    # ---- the two turns ---------------------------------------------------
    # 1. The MASTER spin is a painter transform about (cx, cy): a slow 360 deg
    #    turn in the screen plane, layered on top of every layer's own motion.
    #    Taken modulo a full turn for numerical stability at large clocks — a
    #    turn is the identity, so that wrap is invisible (no snap). A transform
    #    is right for this: it turns the whole assembly rigidly, so every point
    #    keeps the field's exact coordinates and the recorder-based contracts
    #    all still read their own numbers.
    # 2. The COMBINED X+Y tumble is NOT a transform. It is a real 3D rotation
    #    — Y about a vertical axis first, then X about a horizontal one —
    #    applied per point through ``_place`` / ``tumble_point``, because a
    #    painter transform cannot give one element a different depth from the
    #    next — and depth is what stops the mark going edge-on to a line
    #    (owner: "i dont what that thin"). It is purely additive: the master
    #    spin is untouched.
    #
    # All three read the clamped clock, so under reduced motion the master
    # spin pushes no transform and the tumble collapses to an exact identity.
    #
    # The NUCLEUS takes part in the master spin but is exempt from the
    # tumble's SQUASH: a sphere's silhouette is a circle from every angle, and
    # squashing the core into a lens is the one transform here that would read
    # as a bug rather than as depth. It still carries the turn, through its
    # specular highlight (see ``_nucleus``) and a small uniform scale.
    spin = math.fmod(mark_spin_deg(tt), 360.0)

    def _field_pass(near: bool, shells_: List[Dict[str, Any]],
                    motes: List[Dict[str, Any]], bodies: List[Dict[str, Any]],
                    bits: List[Dict[str, Any]], sparks_: List[Dict[str, Any]],
                    glints_: List[Dict[str, Any]],
                    traveller_: Optional[Dict[str, Any]],
                    blinks_: List[Dict[str, Any]],
                    volumes_: List[Dict[str, Any]],
                    breaks_: List[Dict[str, Any]]) -> None:
        """One half of the z-seam: its circles first, then the bodies in it."""
        _ring_pass(near)
        # The dense new layers, batched into a few spectral paths per half:
        # ring bodies (glints) on every declared circle, the nucleus's vertical
        # column (shaded by height), the rarer flare family, and the many field
        # sparkle dots. Their individual positions come from the field; only
        # the draw is grouped, so the primitive cost stays flat.
        _layer_batch([p for p in ring_parts if _is_near(p, near)],
                     RING_PARTICLE_BUCKETS, arm_frac=_field.GLINT_ARM_FRAC * 0.8)
        _cylinder_batch([p for p in cyl_parts if _is_near(p, near)],
                        CYLINDER_BUCKETS)
        # Accretion + infall: dense dot families riding the rings / falling in.
        # Batched (they are dots, not wakes), so 28 + 14 samples cost a few
        # paths while the spiral and the inward motion stay visible.
        _layer_batch([p for p in accretion if _is_near(p, near)],
                     ACCRETION_BUCKETS)
        _layer_batch([p for p in infall if _is_near(p, near)], INFALL_BUCKETS)
        for body in volumes_:
            _volume_particle(body)
        # Orbiters circle the NUCLEUS on inclined 3D orbits, so they split by
        # their own depth: the far half passes behind the core, the near half
        # in front of it — that is what reads as orbiting the middle rather
        # than sliding along a ring. Plain dots (no glint arms): at orbiter
        # size the arms are sub-pixel, and the batched arm stroke read as a
        # third thin "orbit" outline to the echo-ring contract.
        _layer_batch([p for p in orbiters if _is_near(p, near)],
                     ORBITER_BUCKETS)
        _layer_batch([p for p in flares if _is_near(p, near)], FLARE_BUCKETS)
        _layer_batch(glints_, SPARKLE_BUCKETS)
        for smp in filament:
            if _is_near(smp, near):
                _filament(smp)
        for shell in shells_:
            _satellite(shell, draw_trails)
        for mote in motes:
            _dust(mote)
        for part in bodies:
            _particle(part, draw_trails)
        for spark in sparks_:
            _spark(spark)
        for glint in glints_:
            _sparkle(glint, draw_trails)
        for bit in bits:
            _binary(bit)
        for brk in breaks_:
            _screen_break(brk)
        for bl in blinks_:
            _blink(bl)
        if traveller_ is not None:
            _comet(traveller_, draw_trails)

    far_dust = [m for m in dust if m["depth"] < 0.5]
    near_dust = [m for m in dust if m["depth"] >= 0.5]
    far_parts = [q for q in parts if q["depth"] < 0.5]
    near_parts = [q for q in parts if q["depth"] >= 0.5]

    p.save()
    if spin:
        p.translate(cx, cy)
        p.rotate(spin)
        p.translate(-cx, -cy)

    # ---- far field (depth < 0.5), behind the nucleus ---------------------
    # Its circles include the inner core ring, which is depth-neutral: drawn
    # once here, before the far field, so it reads as a core path rather than
    # as another orbit taking part in the z-seam.
    #
    # Both halves run inside ONE save/restore that carries only the master
    # spin. There is no horizontal transform to push: each point's place is
    # already resolved by ``_place`` as it is drawn, which is the only way a
    # near element and a far element can sit at different depths in the same
    # frame.
    _field_pass(False, far_shells, far_dust, far_parts, far_binaries, far_sparks,
                [g for g in glints if g["depth"] < 0.5],
                traveller if (traveller is not None
                              and _num(traveller.get("depth"), 1.0) < 0.5)
                else None, far_blinks, far_volumes, far_sbreaks)

    # ---- the z-seam: shockwave, halo, nucleus ----------------------------
    beat = 0.0 if reduced else _field.heartbeat(tt)
    nuc_scale = 1.0 if reduced else _field.nucleus_scale(tt)
    nuc_r = (Rf * _field.NUCLEUS_FRAC * NUCLEUS_RADIUS_MULT * nuc_scale
             * (1.0 + NUCLEUS_TUMBLE_SCALE * (1.0 - turn_cy * turn_cx)))
    sw_scale, sw_alpha = (0.0, 0.0) if reduced else _field.shockwave(tt)
    if sw_alpha > 0.0:
        # One thin expanding ring per beat: stroke only, never a fill/glow.
        _outline(nuc_r * sw_scale, nuc_rgb, sw_alpha,
                 _field.SHOCKWAVE_WIDTH)
    if beat > 0.0:
        _outline(nuc_r * _field.HALO_SCALE, nuc_rgb,
                 _field.HALO_BASE * beat, 1.0)
    # The nucleus is EXEMPT from the tumble's squash: a sphere's silhouette is
    # a circle from every angle, and the smoke, corona, swirl lobes and
    # speculars are all part of that sphere, not of the flat orbital plane.
    # It still carries the turn, through its specular highlight and a small
    # uniform scale.
    _nucleus(nuc_r, nuc_rgb, tt, beat)
    # Interior shells paint ON the body, far to near, so the core's inside is
    # layered depth that turns and pulses with the rest of the mark.
    for _shell in core_shells:
        _core_body(_shell)
    # The photon ring is the LENSED image hugging the core: it is painted OVER
    # the nucleus (not z-split), so its bright arcs always read on top of the
    # silhouette instead of being swallowed by the body on the far half. Drawn
    # dot by dot so the field's lobe profile (two bright arcs, dim between)
    # survives — a single batched path would flatten it to one alpha.
    for _smp in photons:
        _photon_dot(_smp)

    # ---- near field (depth >= 0.5), in front of the nucleus --------------
    _field_pass(True, near_shells, near_dust, near_parts, near_binaries,
                near_sparks, [g for g in glints if g["depth"] >= 0.5],
                traveller if (traveller is not None
                              and _num(traveller.get("depth"), 0.0) >= 0.5)
                else None, near_blinks, near_volumes, near_sbreaks)
    p.restore()


def backend_wanted() -> str:
    """``qt``/``tk`` from the env (default ``auto`` -> ``qt``)."""
    import os as _os
    try:
        return str(_os.environ.get(_QT_BACKEND_ENV, "") or "").strip().lower()
    except Exception:
        return ""


def _reduced_motion() -> bool:
    import os as _os
    try:
        return str(_os.environ.get("QUBIT_REDUCED_MOTION", "") or "").strip().lower() in (
            "1", "true", "yes", "on")
    except Exception:
        return False


def zone_at(x: float, y: float, size: int, collapsed: bool) -> str:
    """Hit zone: always ``"main"`` — the orb carries no chrome.

    Owner 2026-10-04: the x (close) and - (collapse) buttons were
    removed outright (drawing + hit zones, both view layers), so there
    is nothing to hit but the orb itself. Signature kept (rather than
    deleted) so press/release/hover call sites and their tests keep one
    naming point. Pure, never raises.
    """
    return "main"


def _is_listening_state(orb_state: Any) -> bool:
    """True only for the listening state (level modulates it alone)."""
    try:
        return str(orb_state or "").strip().lower() == "listening"
    except Exception:
        return False


def _is_voice_state(orb_state: Any) -> bool:
    """True for listening + speaking (the equalizer states, spec 2026-10-05)."""
    try:
        return str(orb_state or "").strip().lower() in (
            "listening", "speaking")
    except Exception:
        return False


def spec_bars_wanted(orb_state: Any, spectrum: Any) -> bool:
    """True when the Qt paint path draws equalizer bars (pure).

    Listening/speaking with a valid 12-float spectrum -> bars;
    thinking/idle/error (no audio to analyse) or a missing/malformed
    spectrum -> today's sine loop. Never raises.
    """
    try:
        if not _is_voice_state(orb_state):
            return False
        return _ember.spec_bar_rects(spectrum, None, 48, 48, 46) != []
    except Exception:
        return False


def band_pen(band_index: int, orb_state: Any,
             level: Any = None, family: Any = None,
             brightness_scale: Any = None
             ) -> Tuple[Tuple[int, int, int], float, int]:
    """(rgb, alpha 0..1, width px) for one ember wave band.

    Color reads the single-source per-state table
    (``ember.band_colors``), or ``family`` when a caller is cross-fading
    states (``ember.ColorTransition``). Opacity/width keep the
    ``ember.render`` rule (opacity x brightness x 3, wide top bands).
    ``level`` modulates ONLY the listening state's brightness;
    ``brightness_scale`` (from a spectrum-energy / mic level) modulates
    any state uniformly. ``None`` everywhere renders exactly today's
    loop (same contract as ``ember.render``). Pure, never raises.
    """
    try:
        params = _ember.resolve_params(orb_state)
        spec = _ember.BAND_SPECS[int(band_index) % len(_ember.BAND_SPECS)]
        op = float(spec[4])
        bright = float(params["brightness"])
        if brightness_scale is not None:
            bright *= float(brightness_scale)
        elif level is not None and _is_listening_state(orb_state):
            bright *= float(_ember.listening_brightness(level))
        fams = (_ember.bands_from_family(family) if family is not None
                else _ember.band_colors(orb_state))
        rgb = fams[int(band_index) % len(fams)]
        alpha = min(1.0, op * bright * 3.0)
        width = 2 if int(band_index) >= 3 else 1
        return (tuple(int(v) for v in rgb), alpha, width)
    except (TypeError, ValueError, IndexError):
        return ((255, 84, 0), 0.1, 1)


def core_stops(orb_state: Any, level: Any = None, family: Any = None,
               brightness_scale: Any = None
               ) -> List[Tuple[float, Tuple[int, int, int, int]]]:
    """Radial-gradient stops for the state heart (offset, rgba).

    Highlight/mid read the single-source per-state family
    (``ember.state_colors``), or ``family`` when cross-fading.
    ``level`` modulates ONLY the listening state's brightness;
    ``brightness_scale`` modulates any state uniformly. Pure, never
    raises.
    """
    try:
        fam = (dict(family) if family is not None
               else _ember.state_colors(orb_state))
        hi = tuple(int(v) for v in fam["hi"])
        core = tuple(int(v) for v in fam["core"])
        bright = float(_ember.resolve_params(orb_state)["brightness"])
        if brightness_scale is not None:
            bright *= float(brightness_scale)
        elif level is not None and _is_listening_state(orb_state):
            bright *= float(_ember.listening_brightness(level))
    except (TypeError, ValueError):
        hi, core, bright = (255, 200, 120), (255, 84, 0), 0.45
    try:
        hi_a = int(255 * min(1.0, 0.75 + 0.25 * bright))
        mid_a = int(255 * min(1.0, 0.55 + 0.35 * bright))
    except (TypeError, ValueError):
        hi_a, mid_a = 200, 180
    return [
        (0.0, (hi[0], hi[1], hi[2], hi_a)),
        (0.55, (core[0], core[1], core[2], mid_a)),
        (1.0, (core[0], core[1], core[2], 0)),
    ]


def ring_pen(phase01: float, index: int, orb_state: Any,
             level: Any = None, brightness_scale: Any = None
             ) -> Tuple[float, int]:
    """(alpha 0..1, width px) for one pulse ring.

    ``level`` modulates ONLY the listening state's brightness;
    ``brightness_scale`` modulates any state uniformly. Pure, never
    raises.
    """
    try:
        bright = float(_ember.resolve_params(orb_state)["brightness"])
        if brightness_scale is not None:
            bright *= float(brightness_scale)
        elif level is not None and _is_listening_state(orb_state):
            bright *= float(_ember.listening_brightness(level))
        alpha = _ember.ring_alpha(phase01, bright)
        return (min(1.0, 0.25 + float(alpha) * 2), 2 if int(index) == 0 else 1)
    except (TypeError, ValueError):
        return (0.25, 1)


if QWidget is not None:
    class QtVoiceOrb(QWidget):  # type: ignore[no-redef]
        """Borderless Qt orb window. Constructing requires a display."""

        def __init__(self, poll: bool = True,
                     client_factory: Optional[Callable[[], Any]] = None,
                     fullscreen_probe: Optional[Callable[[], bool]] = None,
                     options: Any = None,
                     enable_tray: bool = False) -> None:
            super().__init__(
                None,
                Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                | Qt.SplashScreen | Qt.WindowDoesNotAcceptFocus
                | Qt.BypassWindowManagerHint,
            )
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(Qt.NoFocus)
            self.setWindowTitle("Qubit Voice Orb")
            self.setMouseTracking(True)

            self.orb_state: str = "idle"
            self.offline = False
            self.notice_until = 0.0
            self.ingame_override = False
            # Talk-meter render state (same rule as the Tk view): the
            # smoothed 0..1 level the paint path passes to the pen
            # helpers; None = no level available (old helper/offline)
            # -> exactly today's loop. Only the listening render path
            # reads it (helpers gate by state).
            try:
                self._level_smoother = _LevelSmoother()
            except Exception:
                self._level_smoother = None
            self._frame_level: Optional[float] = None
            # Equalizer render state (same additive pattern as the Tk
            # view): smoothed 12-float spectrum + peak caps; None = no
            # spectrum yet -> today's sine loop. Listening reads live
            # bands, speaking reads the TTS timeline frame.
            try:
                self._spec_smoother = _ember.SpectrumSmoother()
            except Exception:
                self._spec_smoother = None
            self._frame_spec: Optional[list] = None
            self._frame_peaks: Optional[list] = None
            # Raw targets recorded by the poll; the paint path advances
            # the per-frame smoothers from these so bars/brightness glide.
            self._spec_target: Optional[list] = None
            self._level_target: Optional[float] = None
            self._anim_last: Optional[float] = None
            # App options (size / opacity / speed / lock / per-action
            # colors). None -> built-in defaults (tests never read the
            # user's on-disk file); ``__main__`` passes load_options().
            self._options = (_options.clamp_options(options)
                             if options is not None else _options.OrbOptions())
            try:
                _geo.set_orb_size(self._options.size_px)
            except Exception:
                pass
            self._speed = float(self._options.speed)
            self._test_state: Optional[str] = None
            self._test_until = 0.0
            self._panel: Any = None
            self._tray: Any = None
            # Fluid color: every state change cross-fades through Oklab,
            # honouring the user's per-action color overrides.
            try:
                self._color_transition = _ember.ColorTransition(
                    self.orb_state,
                    overrides=_options.color_overrides(self._options))
            except Exception:
                self._color_transition = None
            self._pending_rid: Optional[str] = None
            self._pending_ts = 0.0
            # The LIVE turn's request id, learned from the state feed (see
            # ``state.active_request``). The helper path never stages a rid
            # locally, so without this a second press re-planned "begin" and
            # re-triggered capture instead of stopping the in-flight turn.
            self._active_rid: Optional[str] = None
            self._active_ts = 0.0
            self._client_factory = client_factory or _default_client
            self._client: Any = None
            self._toggle_requested = False
            self._quit_requested = False
            self._fullscreen_probe = (
                fullscreen_probe or _fs.active_window_is_fullscreen)
            self._fs_auto_hidden = False
            self._press: Optional[Dict[str, Any]] = None
            self._t0 = time.monotonic()
            self.frames = 0
            # Wall-clock render clock (hitch-clamped) + hysteresis:
            # phase advances by clamped wall dt, never by frame count,
            # so dropped frames cannot judder the animation; the
            # displayed state holds MIN_DWELL_S (listening/error instant).
            self._paint_t = 0.0
            self._paint_last_mono: Optional[float] = None
            self._frame_dts: List[float] = []
            try:
                self._display = _state.DisplayState(initial=self.orb_state)
            except Exception:
                self._display = None

            stored = _persist.load_position()
            self._pos = stored or _geo.OrbPosition(x=0, y=0)
            self._pos_is_default = stored is None
            self._place_initial()

            self._frame_timer = QTimer(self)
            self._frame_timer.setInterval(FRAME_MS)
            self._frame_timer.timeout.connect(self._frame_tick)
            self._frame_timer.start()
            if poll:
                self._ensure_poll_worker()
                self._poll_timer = QTimer(self)
                self._poll_timer.setInterval(POLL_MS)
                self._poll_timer.timeout.connect(self._poll_tick)
                self._poll_timer.start()
            self._sig_timer = QTimer(self)
            self._sig_timer.setInterval(250)
            self._sig_timer.timeout.connect(self._signal_tick)
            self._sig_timer.start()
            # Live voice-journal tail: the orb reacts to physical voice
            # boundaries (mic/TTS/transcript) at append time instead of
            # waiting for the next HTTP poll. The poll stays for status /
            # settings / heartbeat; events come from the journal.
            self._journal = None
            self._stream_queue = None
            self._stream_timer = None
            self._last_status: Any = None
            self._last_heartbeat: Any = None
            # Output-activity tracker (OpenClaw port, 2026-10-06): keeps
            # the speaking state exact when a TTS journal event is missed.
            # A TTS frame observed -> mark_audio; a speak marker ->
            # mark_playback_started; speak_done/expiry -> mark_stream_ending.
            try:
                self._activity = _activity.OutputActivityTracker()
            except Exception:
                self._activity = None
            if poll:
                try:
                    self._stream_queue = queue.Queue(maxsize=1)
                    self._journal = _stream.JournalTail(
                        on_event=lambda _ev: self._mark_stream_dirty())
                    self._journal.start()
                    self._stream_timer = QTimer(self)
                    self._stream_timer.setInterval(100)
                    self._stream_timer.timeout.connect(self._stream_tick)
                    self._stream_timer.start()
                except Exception:
                    self._journal = None
            self._apply_window_opacity()
            if enable_tray:
                self._maybe_start_tray()

        # -- live event stream (journal tail) ------------------------------
        def _mark_stream_dirty(self) -> None:
            """Called from the tail thread: signal 'events changed'."""
            try:
                q = self._stream_queue
                if q is not None:
                    q.put_nowait(True)
            except Exception:
                pass

        def _stream_tick(self) -> None:
            """GUI-thread: on new journal events, re-resolve state instantly.

            Reuses the full apply path (state resolution + samples + test
            override) with the journal's recent events and the last polled
            status/heartbeat — no HTTP, so onset is ~100 ms instead of a
            poll interval.
            """
            try:
                q = self._stream_queue
                if q is None:
                    return
                dirty = False
                while True:
                    try:
                        q.get_nowait()
                        dirty = True
                    except Exception:
                        break
                if not dirty:
                    return
                events = self._journal.recent() if self._journal is not None else []
                self._apply_poll_payload({
                    "status": self._last_status,
                    "events": events,
                    "heartbeat": self._last_heartbeat,
                })
            except Exception:
                pass

        # -- app layer: options / tray / settings panel --------------------
        def _apply_window_opacity(self) -> None:
            try:
                self.setWindowOpacity(float(self._options.opacity))
            except Exception:
                pass

        def apply_options(self, opts: Any) -> None:
            """Clamp, persist and apply new options live. Never raises."""
            try:
                self._options = _options.clamp_options(opts)
                _options.save_options(self._options)
                _geo.set_orb_size(self._options.size_px)
                self._speed = float(self._options.speed)
                self._apply_window_opacity()
                try:
                    self._color_transition = _ember.ColorTransition(
                        self.orb_state,
                        overrides=_options.color_overrides(self._options))
                except Exception:
                    pass
                self._apply_geometry()
                tray = getattr(self, "_tray", None)
                if tray is not None:
                    try:
                        tray.set_locked(self._options.locked)
                    except Exception:
                        pass
            except Exception:
                pass

        def set_test_state(self, state: Any, seconds: Any = 4.0) -> None:
            """Force a state for a few seconds (tray/panel 'test' actions)."""
            try:
                key = str(state or "").strip().lower()
                if key not in _ember.STATE_CORES:
                    return
                self._test_state = key
                self._test_until = time.monotonic() + max(
                    0.5, min(30.0, float(seconds)))
            except Exception:
                pass

        def open_settings(self) -> None:
            """Show the settings panel (created once, lazily). Never raises."""
            try:
                from . import panel as _panel
                cls = getattr(_panel, "SettingsPanel", None)
                if cls is None:
                    return
                if getattr(self, "_panel", None) is None:
                    self._panel = cls(self._options,
                                      on_change=self.apply_options,
                                      on_test=self.set_test_state)
                try:
                    self._panel.set_options(self._options)
                except Exception:
                    pass
                self._panel.show()
                self._panel.raise_()
            except Exception:
                pass

        def _maybe_start_tray(self) -> None:
            try:
                if not self._options.show_tray:
                    return
                from . import tray as _tray
                self._tray = _tray.OrbTray(
                    on_toggle=self.request_toggle,
                    on_settings=self.open_settings,
                    on_quit=self.request_quit,
                    on_test=self.set_test_state,
                    on_lock=lambda locked: self.apply_options(
                        _options.updated(self._options, locked=bool(locked))),
                    options=self._options,
                    parent=self,
                )
                if not self._tray.start():
                    self._tray = None
            except Exception:
                self._tray = None

        # -- geometry (same rules as the Tk view) -------------------------
        def _target_rect(self) -> Any:
            try:
                return _mon.target_rect()
            except Exception:
                return None

        def _place_initial(self) -> None:
            rect = self._target_rect()
            if rect is not None:
                if self._pos_is_default:
                    self._pos = _geo.default_position_for_rect(rect)
                else:
                    resolved, reset, _ = _geo.resolve_initial_position_for_rect(
                        self._pos, rect)
                    self._pos = resolved
                self._apply_geometry()
                return
            try:
                w, h = self.screen().availableGeometry().width(), \
                    self.screen().availableGeometry().height()
            except Exception:
                w, h = (1920, 1080)
            if self._pos_is_default:
                pos = _geo.default_position(w, h)
                self._pos = _geo.OrbPosition(x=pos.x, y=pos.y, collapsed=False)
            else:
                resolved, _reset, _ = _geo.resolve_initial_position(
                    self._pos, w, h)
                self._pos = resolved
            self._apply_geometry()

        def _apply_geometry(self) -> None:
            size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
            try:
                self.setFixedSize(size, size)
                self.move(self._pos.x, self._pos.y)
            except Exception:
                pass

        def show(self) -> None:  # restack only, never focus-forcing
            try:
                super().show()
                self.raise_()
            except Exception:
                pass

        def hide_to_background(self) -> None:
            """Withdraw, keep polling (tray-less).

            Owner 2026-10-04: the window has no close/minimize buttons,
            so this is reached only via the fullscreen auto-hide rule,
            ``toggle_visible``, and the ``--toggle`` CLI verb. Process
            exit is ``--quit`` / service stop only.
            """
            try:
                super().hide()
            except Exception:
                pass

        @property
        def visible(self) -> bool:
            try:
                return bool(self.isVisible())
            except Exception:
                return False

        def toggle_visible(self) -> None:
            if self.visible:
                self.hide_to_background()
            else:
                self.show()

        # -- input: drag anywhere, click toggles ---------------------------
        def mousePressEvent(self, ev: Any) -> None:
            try:
                size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
                self._press = {
                    "x": float(ev.globalPosition().x()),
                    "y": float(ev.globalPosition().y()),
                    "ox": self._pos.x, "oy": self._pos.y,
                    "zone": zone_at(float(ev.position().x()),
                                    float(ev.position().y()),
                                    size, self._pos.collapsed),
                    "dragging": False,
                }
            except Exception:
                self._press = None

        def mouseMoveEvent(self, ev: Any) -> None:
            press = self._press
            if not press:
                # No chrome: the whole orb is the voice surface.
                try:
                    self.setCursor(Qt.PointingHandCursor)
                except Exception:
                    pass
                return
            # Position lock (options): pressing still toggles voice on
            # release, but the window never drags.
            if getattr(self._options, "locked", False):
                return
            try:
                dx = float(ev.globalPosition().x()) - press["x"]
                dy = float(ev.globalPosition().y()) - press["y"]
            except Exception:
                return
            if not press["dragging"] and not _geo.is_drag(dx, dy):
                return
            press["dragging"] = True
            rect = self._target_rect()
            if rect is not None:
                clamped = _geo.clamp_to_rect(press["ox"] + int(dx),
                                             press["oy"] + int(dy), rect,
                                             collapsed=self._pos.collapsed)
            else:
                try:
                    w, h = self.screen().availableGeometry().width(), \
                        self.screen().availableGeometry().height()
                except Exception:
                    w, h = (1920, 1080)
                clamped = _geo.clamp_position(press["ox"] + int(dx),
                                              press["oy"] + int(dy), w, h,
                                              collapsed=self._pos.collapsed)
            self._pos = _geo.OrbPosition(x=clamped.x, y=clamped.y,
                                         collapsed=self._pos.collapsed)
            self._apply_geometry()

        def mouseReleaseEvent(self, ev: Any) -> None:
            press = self._press
            self._press = None
            if not press or press["dragging"]:
                if press and press["dragging"]:
                    _persist.save_position(None, self._pos)
                return
            try:
                size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
                zone = zone_at(float(ev.position().x()),
                               float(ev.position().y()),
                               size, self._pos.collapsed)
            except Exception:
                return
            if zone != press["zone"]:
                return
            # No chrome actions: every click toggles voice (close/collapse
            # buttons removed outright, owner 2026-10-04).
            self.toggle_voice()

        # -- voice toggle (same plan as the Tk view) -----------------------
        def toggle_voice(self) -> None:
            now = time.time()
            # The plan now sees the LIVE turn id learned from the state feed.
            # While Qubit is thinking/working/speaking a press is a STOP: it
            # cancels that request. Without this the helper path always
            # re-planned "begin" (``_pending_rid`` is only ever set on the
            # stage-only fallback) and re-triggered capture, so the reply
            # kept playing — the owner's "it stops too slow".
            action, rid = _state.plan_toggle(
                self._pending_rid, self._pending_ts, now,
                orb_state=getattr(self, "orb_state", None),
                active_rid=getattr(self, "_active_rid", None),
                active_ts=getattr(self, "_active_ts", 0.0))
            try:
                cli = self._get_client()
                if action == "cancel" and rid:
                    cli.cancel_request(rid)
                    self._pending_rid = None
                    if rid == getattr(self, "_active_rid", None):
                        self._active_rid = None
                        self._active_ts = 0.0
                    return
                try:
                    cli.trigger_helper_capture()
                    self._pending_rid = None
                except Exception as exc:
                    code = getattr(exc, "code", "")
                    if code == "ORB_HELPER_NOT_RUNNING":
                        out = cli.begin_session()
                        new_rid = str(out.get("request_id", "") or "")
                        self._pending_rid = new_rid or None
                        self._pending_ts = now
                    else:
                        raise
            except Exception:
                self.notice_until = now + NOTICE_S

        def _get_client(self) -> Any:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

        # -- state feed (worker thread + queue; GUI thread never blocks) --
        def _ensure_poll_worker(self) -> None:
            """Start the daemon poll worker (once; HTTP + queue only).

            Same contract as the Tk view: the worker never touches a
            widget — all paint/state work happens in
            ``_apply_poll_payload`` on the GUI thread.
            """
            try:
                if self._poll_worker_alive():
                    return
            except Exception:
                pass
            try:
                if getattr(self, "_poll_queue", None) is None:
                    self._poll_queue = queue.Queue(maxsize=1)
                worker = threading.Thread(target=self._poll_loop,
                                          name="orb-poll", daemon=True)
                worker.start()
                self._poll_thread = worker
            except Exception:
                pass

        def _poll_worker_alive(self) -> bool:
            try:
                worker = getattr(self, "_poll_thread", None)
                return bool(worker is not None and worker.is_alive())
            except Exception:
                return False

        def _poll_loop(self) -> None:
            """Worker body: fetch one round, hand it to the GUI thread.

            Runs on the daemon thread — HTTP and queue only. No widgets,
            no dwell/hysteresis object, no fullscreen rule (those run in
            ``_apply_poll_payload``/``_poll_tick`` on the GUI thread).
            Never raises off the thread.
            """
            while True:
                try:
                    payload = self._fetch_once()
                except Exception:
                    payload = None
                try:
                    if payload is not None:
                        _client.offer_latest(self._poll_queue, payload)
                except Exception:
                    pass
                try:
                    time.sleep(POLL_MS / 1000.0)
                except Exception:
                    return

        def _drain_poll_queue(self) -> Optional[Dict[str, Any]]:
            try:
                pending = getattr(self, "_poll_queue", None)
                if pending is None:
                    return None
                return _client.drain_latest(pending)
            except Exception:
                return None

        def _fetch_once(self) -> Dict[str, Any]:
            """One synchronous fetch round (worker, or worker-less fallback)."""
            try:
                tick = int(getattr(self, "_settings_tick", 0) or 0) + 1
            except (TypeError, ValueError):
                tick = 1
            self._settings_tick = tick
            try:
                cli = self._get_client()
            except Exception:
                return {"status": None, "events": [], "settings": None}
            try:
                return _client.fetch_poll_payload(cli, tick)
            except Exception:
                return {"status": None, "events": [], "settings": None}

        def _observe_output_activity(self, status: Any, events: Any,
                                     heartbeat: Any) -> None:
            """Feed one apply round into the output-activity tracker.

            Pure bookkeeping (no widget, no HTTP beyond the passed
            payload): a TTS/spectrum frame observed now -> ``mark_audio``
            (refreshes the activity tail); the newest speak marker newer
            than its done marker -> ``mark_playback_started`` (opening a
            new stream when the previous one had ended); a newer
            done marker or a passed tail with playback evidence ->
            ``mark_stream_ending``. Never raises.
            """
            try:
                act = getattr(self, "_activity", None)
                if act is None:
                    return
                now = time.time()
                speak_ts, done_ts = _state.speaking_markers(events, now)
                if speak_ts is not None and (done_ts is None
                                             or speak_ts > done_ts):
                    snap = act.snapshot()
                    if snap.get("streamEnding"):
                        act.mark_stream_opened()
                    act.mark_playback_started()
                elif done_ts is not None and (speak_ts is None
                                              or done_ts > speak_ts):
                    act.mark_stream_ending()
                # A TTS timeline frame under the play head is live audio.
                frame = None
                try:
                    frame = _client.spec_tts_frame_from_payload(status)
                except Exception:
                    frame = None
                if frame is None and heartbeat is not None:
                    try:
                        frame = _client.spec_tts_frame_from_payload(heartbeat)
                    except Exception:
                        frame = None
                if frame is not None:
                    act.mark_audio({"audioMs":
                                    _client.SPEC_TTS_FRAME_S * 1000.0})
                elif (act.snapshot().get("chunks", 0) > 0
                      and not act.is_active(now)):
                    # Expiry watchdog: audio was emitted but no frame has
                    # arrived for a full tail -> the stream has ended.
                    act.mark_stream_ending()
            except Exception:
                pass

        def _apply_poll_payload(self, payload: Dict[str, Any]) -> None:
            """Apply one fetched round on the GUI thread (widgets allowed)."""
            try:
                status = payload.get("status", None)
            except AttributeError:
                status = None
            try:
                events = payload.get("events", [])
                if not isinstance(events, list):
                    events = []
            except AttributeError:
                events = []
            try:
                heartbeat = payload.get("heartbeat", None)
                if not isinstance(heartbeat, dict):
                    heartbeat = None
            except AttributeError:
                heartbeat = None
            # Remember the latest polled status/heartbeat so the journal
            # stream tick can re-resolve without another HTTP round-trip.
            self._last_status = status
            self._last_heartbeat = heartbeat
            # Advance output-activity tracking BEFORE resolving: a frame
            # observed this round makes activity live, which extends the
            # speaking span across a momentarily-empty TTS timeline.
            self._observe_output_activity(status, events, heartbeat)
            self.offline = status is None
            _prev_state = str(getattr(self, "orb_state", ""))
            # Authoritative talk events (voice_edit.talk.* journal rows):
            # when present they drive the orb state directly so it matches
            # the voice FSM exactly; when absent, resolution falls back to
            # the recency-window inference below, byte-identically.
            try:
                _talk_events = _talk_state.talk_events_from_journal(events)
            except Exception:
                _talk_events = None
            if status is not None:
                raw = _state.resolve_orb_state(
                    status, events, time.time(), heartbeat=heartbeat,
                    activity=getattr(self, "_activity", None),
                    talk_events=_talk_events)
                try:
                    if self._display is not None:
                        self.orb_state = self._display.update(
                            raw, time.monotonic())
                    else:
                        self.orb_state = raw
                except Exception:
                    self.orb_state = raw
            else:
                try:
                    if self._display is not None:
                        self.orb_state = self._display.update(
                            "error", time.monotonic())
                    else:
                        self.orb_state = "error"
                except Exception:
                    self.orb_state = "error"
            # Learn the LIVE turn's request id from the same feed (no extra
            # HTTP). Capture rows carry it; keeping it once seen means a
            # journal window that later fills with wake noise cannot lose the
            # id before the reply ends. A newer turn's id simply replaces it,
            # and state.active_request clears it on a cancellation row.
            try:
                _live_rid, _live_ts = _state.active_request(
                    events, time.time())
            except Exception:
                _live_rid, _live_ts = (None, None)
            if _live_rid:
                self._active_rid = _live_rid
                try:
                    self._active_ts = float(_live_ts)
                except (TypeError, ValueError):
                    self._active_ts = time.time()
            # Test override (tray/panel "Test animation"): hold the forced
            # state for its window, then release back to the live feed.
            try:
                if self._test_state is not None:
                    if time.monotonic() < self._test_until:
                        self.orb_state = self._test_state
                    else:
                        self._test_state = None
            except Exception:
                pass
            # Transition-only journal row (2026-10-04: "stuck green"
            # reports are undecidable without it — no row means the
            # poll itself stopped firing). Never raises, never text.
            try:
                _now_state = str(getattr(self, "orb_state", ""))
                if _now_state and _now_state != _prev_state:
                    from ..logging_setup import log_event as _log_event
                    _log_event("orb.state", frm=_prev_state or "none",
                               to=_now_state)
            except Exception:
                pass
            # Talk-meter + equalizer SAMPLES: the poll only records the
            # raw target here; the paint path advances the smoothers per
            # frame (60fps) so brightness and bars glide between the 1s
            # polls instead of stepping. None = no level/spectrum yet.
            try:
                try:
                    _cli = self._get_client()
                except Exception:
                    _cli = None
                get_level = getattr(_cli, "mic_level", None)
                self._level_target = (get_level(status)
                                      if callable(get_level) else None)
            except Exception:
                self._level_target = None
            try:
                try:
                    _cli = self._get_client()
                except Exception:
                    _cli = None
                cur = str(getattr(self, "orb_state", "") or ""
                          ).strip().lower()
                fresh: Optional[list] = None
                if cur == "listening":
                    get_spec = getattr(_cli, "spectrum", None)
                    if callable(get_spec):
                        fresh = get_spec(status)
                    else:
                        fresh = _client.spec_bands_from_payload(status)
                elif cur == "speaking":
                    get_tts = getattr(_cli, "tts_frame", None)
                    if callable(get_tts):
                        fresh = get_tts(status)
                    else:
                        fresh = _client.spec_tts_frame_from_payload(
                            status)
                self._spec_target = fresh
            except Exception:
                self._spec_target = None
            # Settings is a third blocking HTTP round-trip on the GUI
            # thread (a slow bridge wedged the painted state for the whole
            # tick), yet the ingame override flips rarely. It is fetched
            # every 5th round and never while offline — same rule as the
            # Tk view — and the fetch moved to the worker thread
            # (2026-10-05); this apply stays on the GUI thread.
            try:
                settings = payload.get("settings", None)
            except AttributeError:
                settings = None
            if settings is not None:
                try:
                    self.ingame_override = bool(settings.get(
                        "waveform_ingame", False))
                except Exception:
                    pass

        def _poll_tick(self) -> None:
            try:
                payload = self._drain_poll_queue()
                if payload is None and not self._poll_worker_alive():
                    # No worker owns fetching (tests, worker-less paths):
                    # fetch synchronously, exactly like the old tick.
                    payload = self._fetch_once()
                if payload is not None:
                    self._apply_poll_payload(payload)
            finally:
                self._apply_fullscreen_rule()

        def _apply_fullscreen_rule(self) -> None:
            try:
                probe = getattr(self, "_fullscreen_probe", None)
                if probe is None:
                    return
                focused_fs = bool(probe())
            except Exception:
                return
            try:
                override = bool(getattr(self, "ingame_override", False))
                action, auto = _fs.next_visibility(
                    focused_fs, self.visible,
                    bool(getattr(self, "_fs_auto_hidden", False)),
                    ingame_override=override)
            except Exception:
                return
            try:
                if action == "hide":
                    self.hide_to_background()
                    self._fs_auto_hidden = True
                elif action == "show":
                    self.show()
                    self._fs_auto_hidden = False
                else:
                    self._fs_auto_hidden = auto
            except Exception:
                pass

        def _advance_anim(self, now_mono: float) -> None:
            """Advance the per-frame level/spectrum smoothers (60fps).

            Bars and brightness then glide continuously between the 1s
            polls instead of stepping. Never raises.
            """
            try:
                last = getattr(self, "_anim_last", None)
                self._anim_last = now_mono
                if last is None:
                    return
                dt = max(0.0, min(0.25, now_mono - float(last)))
                if dt <= 0.0:
                    return
                # Level: fast-attack / slow-release exponential follower.
                tgt = getattr(self, "_level_target", None)
                if tgt is None:
                    self._frame_level = None
                else:
                    cur = getattr(self, "_frame_level", None)
                    cur = 0.0 if cur is None else float(cur)
                    tau = 0.07 if float(tgt) >= cur else 0.28
                    a = 1.0 - math.exp(-dt / tau)
                    self._frame_level = cur + (float(tgt) - cur) * a
                # Spectrum: per-band fast-attack / slow-release + caps.
                smoother = getattr(self, "_spec_smoother", None)
                if smoother is None:
                    self._frame_spec = getattr(self, "_spec_target", None)
                    self._frame_peaks = None
                    return
                target = getattr(self, "_spec_target", None)
                if target is None and not bool(getattr(smoother, "fed", False)):
                    self._frame_spec = None
                    self._frame_peaks = None
                    return
                self._frame_spec = smoother.update(target, dt=dt)
                try:
                    self._frame_peaks = list(
                        getattr(smoother, "peaks", []) or [])
                except Exception:
                    self._frame_peaks = None
                try:
                    if target is None and max(
                            [0.0] + [float(v) for v in
                                     (self._frame_spec or [])]) < 1e-3:
                        self._frame_spec = None
                        self._frame_peaks = None
                except Exception:
                    pass
            except Exception:
                pass

        def _frame_tick(self) -> None:
            try:
                self._advance_anim(time.monotonic())
                if self.visible and _ember.needs_frame(
                        True, self._pos.collapsed):
                    # ADAPTIVE frame budget. One frame of this mark costs tens
                    # of ms of CPU, so a fixed interval that is shorter than the
                    # paint just makes the loop repaint back-to-back and peg a
                    # core. Size the next interval from the last frame's real
                    # paint cost, leaving slack, so the orb always yields CPU
                    # between frames. On a fast host this is just FRAME_MS
                    # (full smoothness); on a loaded one it stretches to keep
                    # headroom instead of spinning. Never changes what is drawn.
                    try:
                        paint_ms = float(getattr(self, "_last_paint_ms", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        paint_ms = 0.0
                    want = int(paint_ms * _ember.FRAME_SLACK)
                    interval = want if want > FRAME_MS else FRAME_MS
                    if interval != self._frame_timer.interval():
                        self._frame_timer.setInterval(interval)
                    self.update()
            except Exception:
                pass

        def _signal_tick(self) -> None:
            if self._quit_requested:
                try:
                    if self._journal is not None:
                        self._journal.stop()
                except Exception:
                    pass
                try:
                    tray = getattr(self, "_tray", None)
                    if tray is not None:
                        tray.stop()
                except Exception:
                    pass
                try:
                    self.close()
                finally:
                    try:
                        inst = QApplication.instance()
                        if inst is not None:
                            inst.quit()
                    except Exception:
                        pass
                return
            if self._toggle_requested:
                self._toggle_requested = False
                self.toggle_visible()

        def request_toggle(self) -> None:
            self._toggle_requested = True

        def request_quit(self) -> None:
            self._quit_requested = True

        def frame_time_stats(self) -> Dict[str, float]:
            """Inter-frame time distribution (ms) for fluidity proof.

            Reads the bounded paint-dt ring recorded in ``paintEvent``;
            hermetic shape ``{"count","p50","p95","max","mean"}``.
            Never raises.
            """
            try:
                return dict(_ember.frame_time_stats(
                    list(getattr(self, "_frame_dts", []) or [])))
            except Exception:
                return {"count": 0, "p50": 0.0, "p95": 0.0,
                        "max": 0.0, "mean": 0.0}

        # -- painting (Qt-native ember; same params as ember.render) -------
        def paintEvent(self, ev: Any) -> None:
            _paint_t0 = time.monotonic()
            try:
                now_mono = _paint_t0
                try:
                    last = self._paint_last_mono
                    if last is None:
                        step_ms = 0.0
                    else:
                        step_ms = max(0.0, (now_mono - float(last)) * 1000.0)
                    dts = getattr(self, "_frame_dts", None)
                    if isinstance(dts, list):
                        if step_ms > 0:
                            dts.append(step_ms)
                            del dts[:-600]
                    self._paint_t = _ember.advance_render_time(
                        getattr(self, "_paint_t", 0.0), last, now_mono)
                    self._paint_last_mono = now_mono
                except Exception:
                    pass
                t = float(getattr(self, "_paint_t", 0.0) or 0.0)
                if _reduced_motion():
                    t = 0.0
                else:
                    try:
                        t = t * float(getattr(self, "_speed", 1.0))
                    except Exception:
                        pass
                tr = getattr(self, "_color_transition", None)
                if tr is not None:
                    try:
                        fam = tr.update(self.orb_state, now_mono)
                    except Exception:
                        fam = _ember.state_colors(self.orb_state)
                else:
                    fam = _ember.state_colors(self.orb_state)
                size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing, True)
                if self._pos.collapsed:
                    r, g, b = TEAL
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(r, g, b, int(255 * 0.9)))
                    p.drawEllipse(1, 1, size - 2, size - 2)
                    p.end()
                    self.frames += 1
                    return
                cx = cy = size / 2.0
                R = max(4.0, size / 2.0 - 2)
                params = _ember.resolve_params(self.orb_state)
                speed = params["speed"]
                bright = params["brightness"]
                gain = params["band_gain"]
                # Voice pump: listening reads the mic talk-meter, speaking
                # reads the TTS spectrum energy so Qubit's own voice
                # deforms the orb too. ``gain`` scales the bars; ``bscale``
                # scales brightness for the inline glow/ring math; the pen
                # helpers receive ``brightness_scale`` so their own bright
                # matches. ``_lvl`` stays None (no double-apply).
                _lvl = None
                _bscale = None
                try:
                    _key = str(self.orb_state or "").strip().lower()
                    if _key == "listening":
                        _lf = getattr(self, "_frame_level", None)
                        if _lf is not None:
                            _bscale = _ember.listening_brightness(_lf)
                            gain = float(gain) * _ember.listening_gain(_lf)
                    elif _key == "speaking":
                        _en = _ember.spectrum_energy(
                            getattr(self, "_frame_spec", None))
                        if _en is not None:
                            _bscale = _ember.listening_brightness(_en)
                            gain = float(gain) * _ember.listening_gain(_en)
                except Exception:
                    _bscale = None
                _bright_eff = float(bright) * (
                    float(_bscale) if _bscale is not None else 1.0)

                # Default look: the product mark (dashboard sidebar logo).
                # QUBIT_ORB_STYLE=ember restores the ember glow; a bad value
                # falls back to ember so the orb can never paint nothing.
                if _orb_style() == "mark":
                    try:
                        _paint_mark(p, cx, cy, R, t, self.orb_state,
                                    _bright_eff, fam)
                    except Exception:  # noqa: BLE001 - never blank the orb
                        pass
                    # indicators + bookkeeping below are shared with ember.
                    s = size / 96.0
                    try:
                        rr_, gg_, bb_ = (int(v) for v in _ember.INDICATOR_RED)
                    except Exception:
                        rr_, gg_, bb_ = (255, 60, 50)
                    p.setPen(Qt.NoPen)
                    if self.offline:
                        p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.8)))
                        p.drawEllipse(cx + 24 * s, cy + 24 * s, 8 * s, 8 * s)
                    if time.time() < self.notice_until:
                        p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.95)))
                        p.drawEllipse(cx + 24 * s, cy - 32 * s, 8 * s, 8 * s)
                    p.end()
                    self.frames += 1
                    return

                # ambient mesh: two drifting radial glows
                for gi in range(2):
                    dx = math.sin(t * 0.23) * R * 0.12 * (1 if gi == 0 else -1)
                    dy = math.cos(t * 0.19 + 1.3) * R * 0.12 * (1 if gi == 0 else -1)
                    a = (0.10 if gi == 0 else 0.07) * _bright_eff
                    cr_, cg_, cb_ = (int(v) for v in fam["core"])
                    grad = QRadialGradient(cx + dx, cy + dy, R * 1.1)
                    grad.setColorAt(0.0, QColor(cr_, cg_, cb_, int(255 * min(1.0, a * 2.2))))
                    grad.setColorAt(0.5, QColor(cr_, cg_, cb_, int(255 * a * 0.5)))
                    grad.setColorAt(1.0, QColor(cr_, cg_, cb_, 0))
                    p.fillRect(0, 0, size, size, grad)

                # pulse rings: expand 0 -> R, fading
                n = max(1, int(params["rings"]))
                wa_ = tuple(int(v) for v in fam["warm_a"])
                for i in range(n):
                    ph = _ember.ring_phase(t, i, n, params["ring_speed"])
                    rr = max(1.0, ph * R)
                    alpha, width = ring_pen(ph, i, self.orb_state,
                                            level=_lvl,
                                            brightness_scale=_bscale)
                    p.setPen(QPen(QColor(wa_[0], wa_[1], wa_[2], int(255 * alpha)), width))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)

                # equalizer bars XOR wave bands: a smoothed spectrum in
                # the listening/speaking states draws 12 log-scaled
                # vertical bars (+ peak-hold cap ticks) in the state's
                # color family; every other state (or no spectrum) draws
                # exactly today's sine loop (fail-soft).
                _spec = getattr(self, "_frame_spec", None)
                _peaks = getattr(self, "_frame_peaks", None)
                _rects = (_ember.spec_bar_rects(
                    _spec, _peaks, cx, cy, R, gain)
                    if spec_bars_wanted(self.orb_state, _spec) else [])
                if _rects:
                    for ri, rc in enumerate(_rects):
                        bar_path = QPainterPath()
                        bar_path.moveTo(rc["x"], rc["top"])
                        bar_path.lineTo(rc["x"], rc["bottom"])
                        rgb, alpha, wpx = band_pen(
                            ri, self.orb_state, level=_lvl, family=fam,
                            brightness_scale=_bscale)
                        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                              int(255 * alpha)),
                                     max(wpx, 3)))
                        p.drawPath(bar_path)
                        cap_path = QPainterPath()
                        cap_path.moveTo(rc["x"] - rc["half_w"],
                                        rc["peak_top"])
                        cap_path.lineTo(rc["x"] + rc["half_w"],
                                        rc["peak_top"])
                        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                              int(255 * alpha)), 1))
                        p.drawPath(cap_path)
                else:
                    # wave bands: summed sines x gaussian envelope, real alpha
                    width = R * 2.0
                    x0 = cx - R
                    for bi, spec in enumerate(_ember.BAND_SPECS):
                        path = QPainterPath()
                        x = 0.0
                        first = True
                        while x <= width:
                            y = _ember.band_point(
                                x, t, spec, width, cy, R, gain, speed)
                            if first:
                                path.moveTo(x0 + x, y)
                                first = False
                            else:
                                path.lineTo(x0 + x, y)
                            x += 4
                        rgb, alpha, wpx = band_pen(bi, self.orb_state,
                                                   level=_lvl, family=fam,
                                                   brightness_scale=_bscale)
                        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                              int(255 * alpha)), wpx))
                        p.drawPath(path)

                # core disc: ember heart with transparent falloff
                cr = R * 0.40
                core = QRadialGradient(cx, cy, cr)
                for stop, rgba in core_stops(self.orb_state, level=_lvl,
                                             family=fam,
                                             brightness_scale=_bscale):
                    core.setColorAt(stop, QColor(*rgba))
                p.setPen(Qt.NoPen)
                p.setBrush(core)
                p.drawEllipse(cx - cr, cy - cr, cr * 2, cr * 2)

                # indicators: offline/notice dots only — the collapse/close
                # chrome was removed outright (owner 2026-10-04); exits
                # remain the --quit/--toggle CLI verbs + service stop.
                s = size / 96.0
                try:
                    rr_, gg_, bb_ = (int(v) for v in _ember.INDICATOR_RED)
                except Exception:
                    rr_, gg_, bb_ = (255, 60, 50)
                p.setPen(Qt.NoPen)
                if self.offline:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.8)))
                    p.drawEllipse(cx + 24 * s, cy + 24 * s, 8 * s, 8 * s)
                if time.time() < self.notice_until:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.95)))
                    p.drawEllipse(cx + 24 * s, cy - 32 * s, 8 * s, 8 * s)
                p.end()
                self.frames += 1
            except Exception:
                pass
            finally:
                # Record how long this frame's paint ACTUALLY took, on every
                # exit path (paintEvent has several early branches), so the tick
                # can size the next interval to leave the loop real idle time
                # instead of repainting back-to-back. See ``_frame_tick``.
                try:
                    self._last_paint_ms = (time.monotonic() - _paint_t0) * 1000.0
                except Exception:
                    pass

        def close(self) -> None:
            try:
                super().close()
            except Exception:
                pass


def _default_client() -> Any:
    cfg = _client.load_cfg()
    return _client.OrbClient.from_config(cfg)


__all__ = [
    "QtVoiceOrb",
    "backend_wanted",
    "zone_at",
    "band_pen",
    "core_stops",
    "ring_pen",
    "spec_bars_wanted",
    # mark life constants (shared with the Tk sibling / verifier)
    "HEART_PERIOD_S",
    "HEART_SECOND_PHASE",
    "HEART_SECOND_AMP",
    "HEART_THUMP_S",
    "HEART_SCALE_AMP",
    "HALO_RADIUS_MULT",
    "HALO_ALPHA_MAX",
    "DEPTH_RADIUS_MIN",
    "DEPTH_RADIUS_GAIN",
    "DEPTH_ALPHA_MIN",
    "DEPTH_ALPHA_GAIN",
    "DEPTH_LIFT_MIN",
    "DEPTH_LIFT_GAIN",
    "DEPTH_PARALLAX",
    "TRAIL_SAMPLES",
    "TRAIL_STEP_NEAR_DEG",
    "TRAIL_STEP_FAR_DEG",
    "TRAIL_ALPHA_BASE",
    "TRAIL_RADIUS_MULT",
    "SPARKLE_COUNT",
    "SPARKLE_SEED_STEP",
    "SPARKLE_R_MIN_FRAC",
    "SPARKLE_R_SPAN_FRAC",
    "SPARKLE_LIFE_BASE",
    "SPARKLE_LIFE_STEP",
    "SPARKLE_VISIBLE_FRAC",
    "SPARKLE_MAX_ALPHA",
    "SPARKLE_SIZE_BASE",
    "SPARKLE_SIZE_PEAK",
    "SPARKLE_LIFT",
    # pure frame helpers
    "heartbeat_envelope",
    "mark_particle_layers",
    "mark_sparkles",
    "mark_echo_rings",
    "mark_satellites",
    "mark_nucleus_sparks",
    # the Y-axis 360 turn: a real 3D rotation, not a flat squash
    "mark_horizontal_theta",
    "mark_horizontal_spin",
    "y_turn_point",
    "y_turn_gain",
    "Y_SPIN_PERIOD_S",
    "Y_SPIN_Z_GAIN",
    "Y_SPIN_PERSP",
    "Y_SPIN_DEPTH_GAIN",
    "RING_WARP_SAMPLES",
]
