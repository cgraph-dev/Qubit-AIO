"""Ember-orb renderer: hero-orb look adapted to a 96px Tk canvas.

Technique port (own implementation, own parameters): five summed-sine
wave bands under a gaussian envelope, expanding pulse rings, and
slow-drifting ambient glows, with per-state speed/brightness/gain.

Adapted for Tk (which has no gradients, no per-item alpha, no blur):

- Glow is faked with layered concentric solid ovals; outer falloff
  layers use ``stipple`` dithering (``gray25``/``gray12``) so the edge
  reads as a falloff instead of a hard disc.
- "Opacity" is faked by mixing the ember color toward the near-black
  background at draw time (precomputed per state, no per-frame churn).
- Frame discipline: ~30fps cap (``FRAME_MS = 33``), pause when
  collapsed/hidden (``needs_frame``), reduced-motion freezes time,
  and the hot loop allocates only the point lists Tk needs.

State meaning is preserved from the orb feed
(``voicebridge.orb.state``): idle dim, listening brighter/faster,
thinking chase, speaking full amplitude. Unknown states fall back to
idle so the renderer can never stick on a state it does not know.

This module never touches Tk itself on import and never opens a
window: ``render`` takes any canvas-like object with
``create_oval``/``create_line`` (a hermetic fake works for proof).
Geometry, drag/click, persist, focus, IPC, and services are owned
elsewhere and untouched here.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

#: Frame budget: ~30fps cap for Tk (``after(33)`` in the host).
FRAME_MS = 16

#: Ember-orange family on near-black (own palette values in that family).
EMBER_CORE = (255, 84, 0)
EMBER_MID = (255, 100, 30)
EMBER_WARM_A = (255, 140, 60)
EMBER_WARM_B = (255, 180, 80)
EMBER_HI = (255, 200, 120)
NEAR_BLACK = (11, 13, 18)

#: Per-state CORE color. Owner palette 2026-10-06 (supersedes the
#: 2026-10-05 "GREEN = VOICE both directions" contract — the owner asked
#: for a distinct hue per action so YOU and QUBIT are never the same
#: color): idle ember-orange, listening CYAN (the owner's mic / you
#: talking), thinking INDIGO (model turn in flight), working AMBER (a
#: tool/agent action is running), speaking VIOLET (Qubit's TTS voice),
#: error red. Hues are deliberately far apart on the wheel so every
#: transition reads at a glance.
STATE_CORES: Dict[str, Tuple[int, int, int]] = {
    "idle": (255, 84, 0),
    "listening": (34, 211, 238),
    "thinking": (99, 102, 241),
    "working": (251, 191, 36),
    "speaking": (192, 132, 252),
    "error": (255, 60, 50),
}


def _family(core: Tuple[int, int, int]) -> Dict[str, Tuple[int, int, int]]:
    """Expand one core RGB into the 5-step ramp the renderers read.

    ``core`` -> ``mid``/``warm_a``/``warm_b``/``hi`` by mixing a growing
    fraction toward white, so the gradient stack keeps the same shape in
    every state while tinting wholly from the state's hue.
    """
    r, g, b = core

    def _toward_white(t: float) -> Tuple[int, int, int]:
        return (round(r + (255 - r) * t),
                round(g + (255 - g) * t),
                round(b + (255 - b) * t))

    return {"core": (r, g, b), "mid": _toward_white(0.10),
            "warm_a": _toward_white(0.24), "warm_b": _toward_white(0.38),
            "hi": _toward_white(0.55)}


#: Per-state color table (single source; both renderers read this).
#: Core+bands+rings+glows all tint from the same family entry so the
#: state reads as one color; each family carries its own hi/mid/warm
#: ramp. Motion params in ``_STATE_PARAMS`` are untouched.
STATE_COLORS: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    name: _family(core) for name, core in STATE_CORES.items()
}


def _coerce_core(core: Any) -> Optional[Tuple[int, int, int]]:
    """Validate/clamp an override RGB; malformed -> None. Never raises."""
    try:
        r, g, b = (int(v) for v in core)
    except Exception:
        return None
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def state_core(state: Any, overrides: Any = None) -> Tuple[int, int, int]:
    """Per-state core RGB (unknown -> idle). Never raises.

    ``overrides`` is an optional ``{state: (r, g, b)}`` map: when it holds
    the (normalized) state, that core wins; otherwise the single-source
    ``STATE_CORES`` table answers exactly as before. A malformed override
    falls back to the table rather than raising.
    """
    key = _state_key(state)
    if overrides is not None:
        try:
            if isinstance(overrides, dict):
                for cand in (state, key):
                    try:
                        if cand in overrides:
                            got = _coerce_core(overrides[cand])
                            if got is not None:
                                return got
                            break
                    except TypeError:
                        continue
        except Exception:
            pass
    return STATE_CORES[key]


def family_from_core(core: Any) -> Dict[str, Tuple[int, int, int]]:
    """Public ramp builder (see ``_family``); malformed -> idle family."""
    try:
        r, g, b = (int(v) for v in core)
        return _family((r, g, b))
    except Exception:
        return dict(STATE_COLORS["idle"])


def bands_from_family(fam: Any) -> Tuple[Tuple[int, int, int], ...]:
    """5 band colors from a family dict (core,mid,warm_a,warm_b,mid)."""
    try:
        return (fam["core"], fam["mid"], fam["warm_a"],
                fam["warm_b"], fam["mid"])
    except Exception:
        return BAND_COLORS


# -- perceptual color blend (fluid state transitions) --------------------
#
# Interpolating saturated hues in raw sRGB passes through muddy greys.
# These helpers convert through Oklab (Björn Ottosson) so a cyan->violet
# cross-fade stays vivid the whole way. All pure; never raise.

def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def rgb_to_oklab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """sRGB 0..255 -> Oklab (L, a, b). Never raises (bad input -> black)."""
    try:
        r, g, b = (_srgb_to_linear(max(0.0, min(1.0, v / 255.0)))
                   for v in rgb)
        l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
        m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
        s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
        l_ = max(0.0, l) ** (1.0 / 3.0)
        m_ = max(0.0, m) ** (1.0 / 3.0)
        s_ = max(0.0, s) ** (1.0 / 3.0)
        return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
                1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
                0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)
    except Exception:
        return (0.0, 0.0, 0.0)


def oklab_to_rgb(lab: Tuple[float, float, float]) -> Tuple[int, int, int]:
    """Oklab -> sRGB 0..255 (gamut-clamped). Never raises."""
    try:
        cap = 1.5
        L = max(-cap, min(cap, float(lab[0])))
        a = max(-cap, min(cap, float(lab[1])))
        b = max(-cap, min(cap, float(lab[2])))
        l_ = L + 0.3963377774 * a + 0.2158037573 * b
        m_ = L - 0.1055613458 * a - 0.0638541728 * b
        s_ = L - 0.0894841775 * a - 1.2914855480 * b
        l = l_ ** 3
        m = m_ ** 3
        s = s_ ** 3
        r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
        g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
        bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
        out = []
        for v in (r, g, bb):
            try:
                srgb = _linear_to_srgb(v)
            except (ValueError, OverflowError):
                srgb = 0.0
            out.append(int(round(255.0 * max(0.0, min(1.0, srgb)))))
        return (out[0], out[1], out[2])
    except Exception:
        return (0, 0, 0)


def blend_core(a: Any, b: Any, t: float) -> Tuple[int, int, int]:
    """Hue-swept, saturation-preserving blend of two core RGBs.

    Interpolates in Oklab's polar form (LCh): hue travels the shortest
    arc and chroma is interpolated directly, so a cyan->violet OR an
    orange->cyan cross-fade stays vivid instead of passing through a
    muddy grey (which raw-Oklab/sRGB lerp does at the midpoint).
    ``t`` 0 -> a, 1 -> b. Never raises.
    """
    try:
        tt = max(0.0, min(1.0, float(t)))
        la = rgb_to_oklab(a)
        lb = rgb_to_oklab(b)
        ca = math.hypot(la[1], la[2])
        cb = math.hypot(lb[1], lb[2])
        ha = math.atan2(la[2], la[1])
        hb = math.atan2(lb[2], lb[1])
        dh = (hb - ha + math.pi) % (2.0 * math.pi) - math.pi
        if ca < 1e-6:
            ha = hb
        if cb < 1e-6:
            hb = ha
        L = la[0] + (lb[0] - la[0]) * tt
        C = ca + (cb - ca) * tt
        h = ha + dh * tt
        return oklab_to_rgb((L, C * math.cos(h), C * math.sin(h)))
    except Exception:
        try:
            return tuple(int(v) for v in a)
        except Exception:
            return (255, 84, 0)


def _smoothstep(t: float) -> float:
    try:
        x = max(0.0, min(1.0, float(t)))
    except (TypeError, ValueError):
        return 1.0
    return x * x * (3.0 - 2.0 * x)


#: Cross-fade time between state colors. Active-state entry ~200ms with
#: an ease; anything the poll can do from 1s cadence reads as a smooth
#: fade rather than a hard switch.
COLOR_TRANSITION_S = 0.40


class ColorTransition:
    """Smoothly cross-fades the orb's color family between states.

    One instance per view. ``update(state, now)`` returns the current
    interpolated family dict (ready for ``render``/``band_pen``). A state
    change starts a fresh ``COLOR_TRANSITION_S`` fade from wherever the
    color currently is, so rapid transitions never snap. Pure time math;
    never raises.
    """

    def __init__(self, state: Any = "idle", now: Any = None,
                 duration: Any = COLOR_TRANSITION_S,
                 overrides: Any = None) -> None:
        self._overrides = overrides if isinstance(overrides, dict) else None
        self._from = state_core(state, self._overrides)
        self._to = self._from
        self._current = self._from
        try:
            self._duration = max(1e-3, float(duration))
        except (TypeError, ValueError):
            self._duration = COLOR_TRANSITION_S
        try:
            self._changed = None if now is None else float(now)
        except (TypeError, ValueError):
            self._changed = None

    def update(self, state: Any, now: Any) -> Dict[str, Tuple[int, int, int]]:
        target = state_core(state, self._overrides)
        try:
            now_f = float(now)
        except (TypeError, ValueError):
            return family_from_core(self._current)
        if target != self._to:
            self._from = self._current
            self._to = target
            self._changed = now_f
        if self._changed is None:
            self._current = self._to
        else:
            held = now_f - self._changed
            if held <= 0:
                self._current = self._from
            elif held >= self._duration:
                self._current = self._to
            else:
                self._current = blend_core(
                    self._from, self._to, _smoothstep(held / self._duration))
        return family_from_core(self._current)

    @property
    def core(self) -> Tuple[int, int, int]:
        return self._current

#: Not-working indicator (offline/notice dots): the error family core.
INDICATOR_RED = (255, 60, 50)

#: Wall-clock clamp: one frame may advance the render clock by at most
#: this many seconds. Normal 30fps frames (~33ms) pass through
#: untouched (phase identical regardless of frame count); only a hitch
#: / suspend / dropped-frame burst clamps instead of teleporting.
MAX_DT = 0.25

# -- polish tuning (owner 2026-10-06: alive / organic / beautiful) --------
#
# All of these are pure functions of the frame clock: the same ``t``
# always renders the same frame (no randomness anywhere), and every
# energy term is gated so that "no audio" leaves the geometry exactly as
# it was (bands only gain a small deterministic phase wobble).

#: ``speed_scale`` clamp: multiplier applied to the animation clock ``tt``.
SPEED_SCALE_MIN = 0.2
SPEED_SCALE_MAX = 3.0

#: Energy-driven living pulse: core disc + glow scale with voice energy.
BREATH_GAIN = 0.07
#: Subtle deterministic core heartbeat (fraction of the core radius).
CORE_PULSE_GAIN = 0.06
CORE_PULSE_HZ = 2.4
#: Max organic band-phase wobble in radians (several frequencies summed).
ORGANIC_PHASE_GAIN = 0.22
#: Soft outer aura: base alpha + energy boost (extra translucent ovals).
#: The base is kept high enough that mixing toward the blue-tinted
#: NEAR_BLACK never flips the fill's dominant channel.
AURA_BASE = 0.18
AURA_GAIN = 0.16

#: Ambient breath: a slow, purely time-driven swell applied to the aura,
#: glow and core radii so even idle never looks frozen. A pure function
#: of the animation clock (never random); small enough that the
#: un-modulated frame keeps its identity while visibly living.
AMBIENT_BREATH_GAIN = 0.05
AMBIENT_BREATH_HZ = 0.11

#: Depth parallax: as voice energy rises the glow layers slide farther
#: from center and the core counter-slides a little, so the flat stack
#: reads as layered 3-D. Every term is gated on the energy (``pulse01``),
#: so a no-audio frame stays byte-identical to the pre-polish renderer.
PARALLAX_GAIN = 0.55
CORE_PARALLAX_GAIN = 0.22

#: Per-band hue micro-variation: weight of a slow deterministic
#: oscillator that nudges each band a little toward its neighbour in
#: the state family ramp. Stays inside the family, so the state hue
#: remains dominant (a convex mix cannot flip the dominant channel).
BAND_TINT_GAIN = 0.18

#: Soft outward energy ripple: a translucent ring that rides the voice
#: energy and fades as it expands (deterministic phase, decays). Gated
#: on a live talk-meter / spectrum, so a bare frame never grows one.
RIPPLE_SPEED = 0.35
RIPPLE_GAIN = 0.10
RIPPLE_ALPHA = 0.30

# -- onset transient (owner 2026-10-07: sharper audio reactivity) ---------
#
# Onsets are broadband spikes: the loudest band jumps well above the band
# average. :func:`onset_punch` scores exactly that (peak-vs-mean spikiness
# scaled by level), so the orb's scale/glow can add a crisp extra punch on
# attack and let it fall away as the audio decays (the host's
# ``SpectrumSmoother`` supplies the fast attack / slow release + held caps).
# Every term below is gated on a ``0`` punch, so a flat, silent or absent
# spectrum renders byte-identically to the pre-polish renderer.
ONSET_PUNCH_GAIN = 0.35      # extra scale on the living-pulse breath
ONSET_AURA_GAIN = 0.22       # extra aura opacity/radius on attack
ONSET_PARALLAX_GAIN = 0.25   # extra glow-layer slide on attack
ONSET_CORE_GAIN = 0.12       # extra core-disc pulse on attack

# -- equalizer bar gradient (owner 2026-10-07: richer bars) ---------------
#
# Each bar body is drawn as this many stacked vertical segments, dim at the
# base and brighter toward the tip, so the bars read as a vertical gradient
# instead of a flat stick. Every segment stays inside the state color
# family (a mix toward the background cannot flip the dominant channel).
BAR_GRADIENT_STEPS = 3
BAR_TIP_GAIN = 0.35          # extra brightness of the topmost segment


#: 5 wave bands as (amp, freq, speed, phase, opacity).
#: Amps are in hero units and scaled to the orb radius at draw time.
BAND_SPECS: Tuple[Tuple[float, float, float, float, float], ...] = (
    (120.0, 0.002, 0.30, 55.0, 0.06),
    (80.0, 0.0035, 0.50, 38.0, 0.09),
    (55.0, 0.006, 0.75, 24.0, 0.13),
    (35.0, 0.01, 1.10, 16.0, 0.18),
    (20.0, 0.018, 1.60, 9.0, 0.25),
)

#: Reference hero amp: the widest band spans the full orb radius.
REF_AMP = 120.0

#: Harmonic weights for the 3-sine sum (fundamental + 2 partials).
H2_WEIGHT = 0.22
H3_WEIGHT = 0.10
H_NORM = 1.0 + H2_WEIGHT + H3_WEIGHT

#: Envelope width factor: t = (x/w - 0.5) * 2.3, peak 1.0 at center.
ENVELOPE_K = 2.3

#: Stipple falloff steps for the outer glow layers (Tk bitmap names).
STIPPLE_STEPS = ("", "gray75", "gray50", "gray25", "gray12")

_STATE_PARAMS: Dict[str, Dict[str, float]] = {
    # speed: time multiplier; brightness: opacity multiplier;
    # band_gain: amplitude multiplier; rings: pulse-ring count;
    # ring_speed: ring cycles/sec; glow: ambient glow layers (1-2).
    "idle": {"speed": 0.6, "brightness": 0.45, "band_gain": 0.5,
             "rings": 3, "ring_speed": 0.12, "glow": 1},
    "listening": {"speed": 1.4, "brightness": 0.95, "band_gain": 1.0,
                  "rings": 4, "ring_speed": 0.30, "glow": 2},
    "thinking": {"speed": 1.0, "brightness": 0.70, "band_gain": 0.8,
                 "rings": 5, "ring_speed": 0.45, "glow": 2},
    # Amber "working" (a tool/agent action is running): faster orbit than
    # thinking so a long action visibly differs from a model turn.
    "working": {"speed": 1.5, "brightness": 0.85, "band_gain": 0.9,
                "rings": 6, "ring_speed": 0.60, "glow": 2},
    "speaking": {"speed": 1.8, "brightness": 1.0, "band_gain": 1.2,
                 "rings": 3, "ring_speed": 0.25, "glow": 2},
    "error": {"speed": 1.0, "brightness": 0.90, "band_gain": 0.8,
              "rings": 3, "ring_speed": 0.35, "glow": 2},
}


#: Talk-meter floor for the listening state: silence settles near idle
#: (idle ``band_gain`` is 0.5; 0.15 of listening's 1.0 reads calmer still)
#: but never to a flat line, so the listening color stays visibly alive.
#: Speech at level 1.0 reproduces exactly today's loop (gain 1.0).
LISTEN_LEVEL_FLOOR = 0.15

#: Equalizer bars (spec design 2026-10-05): 12 log-spaced bands, drawn
#: in the state's color family for the two voice states only
#: (listening + speaking share the owner-contract green). Thinking /
#: idle / error ignore the spectrum and render byte-identical to today;
#: ``None`` renders exactly today's sine loop (fail-soft). Reduced
#: motion draws the same bars with frozen time (a static snapshot —
#: bars carry no time phase, so they are inherently still).
SPEC_BARS = 12

#: Per-band fast-attack (~100ms) / slow-release (~400ms) time constants
#: for :class:`SpectrumSmoother` (exponential approach per update).
SPEC_ATTACK_S = 0.1
SPEC_RELEASE_S = 0.4

#: Peak-hold cap fall rate (units/second): caps hold the hit, then fall
#: slowly away so transients leave a visible trace.
SPEC_PEAK_FALL = 0.6

#: Scalar energy-envelope follower constants (fast attack / slow release),
#: mirroring :class:`SpectrumSmoother`'s pair; exposed so hosts can tame
#: the voice pump so it eases instead of snapping.
ENERGY_ATTACK_S = SPEC_ATTACK_S
ENERGY_RELEASE_S = SPEC_RELEASE_S


class SpectrumSmoother:
    """Per-band fast-attack/slow-release follower + peak-hold caps.

    ``update(target, dt)`` folds one poll's 12-float band vector in and
    returns the smoothed 12-float list; ``update(None, dt)`` decays
    toward flat (stale eases, never freezes mid-air). ``peaks`` holds
    the slow-falling per-band caps the renderer draws as ticks. Pure
    math, no I/O; never raises. Same instance pattern as the orb views'
    ``LevelSmoother`` (one instance per view, updated on the poll tick).
    """

    def __init__(self, bands: int = SPEC_BARS,
                 attack_s: float = SPEC_ATTACK_S,
                 release_s: float = SPEC_RELEASE_S,
                 peak_fall: float = SPEC_PEAK_FALL) -> None:
        try:
            n = max(1, int(bands))
        except (TypeError, ValueError):
            n = SPEC_BARS
        self.values: List[float] = [0.0] * n
        self.peaks: List[float] = [0.0] * n
        try:
            self.attack_s = max(1e-3, float(attack_s))
        except (TypeError, ValueError):
            self.attack_s = SPEC_ATTACK_S
        try:
            self.release_s = max(1e-3, float(release_s))
        except (TypeError, ValueError):
            self.release_s = SPEC_RELEASE_S
        try:
            self.peak_fall = max(0.0, float(peak_fall))
        except (TypeError, ValueError):
            self.peak_fall = SPEC_PEAK_FALL
        #: True once a real vector was ever folded in (lets the caller
        #: tell "no spectrum yet" (render the legacy loop) from
        #: "spectrum gone stale" (ease the bars to flat)).
        self.fed = False

    def update(self, target: Any, dt: float = 1.0) -> List[float]:
        """Fold one sample in; return the smoothed band list."""
        try:
            step = max(0.0, float(dt))
        except (TypeError, ValueError):
            step = 1.0
        try:
            want = ([max(0.0, min(1.0, float(v))) for v in target]
                    if target is not None else [0.0] * len(self.values))
            if len(want) != len(self.values):
                want = [0.0] * len(self.values)
            elif target is not None:
                self.fed = True
        except (TypeError, ValueError):
            want = [0.0] * len(self.values)
        except Exception:
            want = [0.0] * len(self.values)
        try:
            a_rate = 1.0 - math.exp(-step / self.attack_s)
            r_rate = 1.0 - math.exp(-step / self.release_s)
        except (OverflowError, ZeroDivisionError):
            a_rate, r_rate = 1.0, 1.0
        out: List[float] = []
        try:
            for i, cur in enumerate(self.values):
                w = want[i]
                rate = a_rate if w >= cur else r_rate
                nxt = cur + (w - cur) * rate
                out.append(nxt)
                peak = self.peaks[i]
                if nxt >= peak:
                    self.peaks[i] = nxt
                else:
                    self.peaks[i] = max(nxt, peak - self.peak_fall * step)
            self.values = out
        except Exception:
            pass
        return list(self.values)


class EnergySmoother:
    """Scalar fast-attack / slow-release follower for voice energy.

    The audio envelope (a 0..1 loudness) drives the living pulse; folded
    in raw it snaps to each new sample. This follower reuses the exact
    exponential-approach math of :class:`SpectrumSmoother` on a single
    scalar: it rises quickly toward a louder target and eases back
    slowly, so the orb swells and settles instead of twitching. The
    estimate is monotone (never overshoots its target) and purely
    time-stepped; never raises.
    """

    def __init__(self, value: float = 0.0,
                 attack_s: float = ENERGY_ATTACK_S,
                 release_s: float = ENERGY_RELEASE_S) -> None:
        try:
            self.value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self.value = 0.0
        self.peaks = self.value
        try:
            self.attack_s = max(1e-3, float(attack_s))
        except (TypeError, ValueError):
            self.attack_s = ENERGY_ATTACK_S
        try:
            self.release_s = max(1e-3, float(release_s))
        except (TypeError, ValueError):
            self.release_s = ENERGY_RELEASE_S

    def update(self, target: Any, dt: float = 1.0) -> float:
        """Fold one energy sample in; return the eased 0..1 estimate."""
        try:
            step = max(0.0, float(dt))
        except (TypeError, ValueError):
            step = 1.0
        try:
            want = (0.0 if target is None
                    else max(0.0, min(1.0, float(target))))
        except (TypeError, ValueError):
            want = 0.0
        except Exception:
            want = 0.0
        try:
            rate = (1.0 - math.exp(-step / self.attack_s)
                    if want >= self.value
                    else 1.0 - math.exp(-step / self.release_s))
        except (OverflowError, ZeroDivisionError):
            rate = 1.0
        self.value = max(0.0, min(1.0, self.value + (want - self.value) * rate))
        if self.value >= self.peaks:
            self.peaks = self.value
        else:
            self.peaks = max(self.value, self.peaks - SPEC_PEAK_FALL * step)
        return self.value

#: Brightness floor paired with the gain floor: quiet listening dims but
#: keeps its color family (state.py owns color; this only modulates
#: amplitude/brightness, never the palette).
LISTEN_BRIGHT_FLOOR = 0.55


def listening_gain(level: Any) -> float:
    """Amplitude scale for the listening state from a 0..1 talk-meter level.

    ``None`` (no level: old helper/offline) returns 1.0 so rendering is
    exactly today's loop (fail-soft). 0.0 -> near-idle amplitude,
    1.0 -> full pump. Never raises.
    """
    if level is None:
        return 1.0
    try:
        lvl = max(0.0, min(1.0, float(level)))
    except (TypeError, ValueError):
        return 1.0
    except Exception:
        return 1.0
    return LISTEN_LEVEL_FLOOR + (1.0 - LISTEN_LEVEL_FLOOR) * lvl


def listening_brightness(level: Any) -> float:
    """Brightness scale paired with :func:`listening_gain` (same contract)."""
    if level is None:
        return 1.0
    try:
        lvl = max(0.0, min(1.0, float(level)))
    except (TypeError, ValueError):
        return 1.0
    except Exception:
        return 1.0
    return LISTEN_BRIGHT_FLOOR + (1.0 - LISTEN_BRIGHT_FLOOR) * lvl


def _speed_scale(value: Any) -> float:
    """Clamp an animation-clock multiplier to [0.2, 3.0]; bad -> 1.0."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    except Exception:
        return 1.0
    if not (v >= SPEED_SCALE_MIN):
        v = SPEED_SCALE_MIN
    return min(SPEED_SCALE_MAX, v)


def spectrum_energy(spectrum: Any) -> Optional[float]:
    """Scalar 0..1 loudness of a 12-band spectrum (drives the voice pump).

    A blend of the loudest band and the mean (so a few active low bands
    still read loud, but a single spike cannot slam the orb to full).
    ``None``/malformed -> ``None`` (caller renders with no modulation).
    Never raises.
    """
    try:
        vals = [max(0.0, min(1.0, float(v))) for v in spectrum]
        if not vals:
            return None
        peak = max(vals)
        mean = sum(vals) / len(vals)
        return max(0.0, min(1.0, 0.65 * peak + 0.35 * mean))
    except Exception:
        return None


def onset_punch(spectrum: Any, peaks: Any = None) -> float:
    """0..1 transient emphasis from a spectrum's spikiness (pure).

    An onset is a broadband spike: the loudest band sits well above the
    band average. This scores ``(peak - mean) / peak`` (0 for a flat or
    silent spectrum, ~1 for an isolated spike) scaled by the peak level,
    so a hard attack reads crisp while a steady tone reads 0 and a bare
    frame carries no punch at all.

    When ``peaks`` (the host's slow-falling per-band caps) are supplied,
    the score is additionally gated by how close the loudest band is to
    its held cap: a *fresh* hit tracks its cap (gate ~1) while a decaying
    tail has already fallen away from it (gate < 1). Combined with the
    host's fast-attack/slow-release smoother this yields an attack that
    decays instead of a hard snap. Deterministic; never raises.
    """
    try:
        vals = [max(0.0, min(1.0, float(v))) for v in spectrum]
    except (TypeError, ValueError):
        return 0.0
    except Exception:
        return 0.0
    if not vals:
        return 0.0
    peak = max(vals)
    if peak <= 0.0:
        return 0.0
    mean = sum(vals) / len(vals)
    spike = max(0.0, peak - mean) / peak
    punch = spike * (peak ** 1.5)
    if peaks is not None:
        try:
            caps = [max(0.0, min(1.0, float(v))) for v in peaks]
        except Exception:
            caps = []
        if len(caps) == len(vals):
            try:
                near = 0.0
                for i, band in enumerate(vals):
                    cap = caps[i]
                    if cap > 1e-6:
                        near = max(near, band / cap)
                punch *= min(1.0, max(0.0, near))
            except Exception:
                pass
    return max(0.0, min(1.0, punch))


def _hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix(fg: Tuple[int, int, int], bg: Tuple[int, int, int],
         alpha: float) -> Tuple[int, int, int]:
    """Fake opacity: mix a color toward the background (Tk has no alpha)."""
    try:
        a = min(1.0, max(0.0, float(alpha)))
    except (TypeError, ValueError):
        a = 0.0
    return tuple(int(round(f * a + b * (1.0 - a))) for f, b in zip(fg, bg))


# Precomputed solid fills (no per-frame string churn in the hot loop).
NEAR_BLACK_HEX = _hex(NEAR_BLACK)
BAND_FILLS = {
    "core": _hex(EMBER_CORE),
    "mid": _hex(EMBER_MID),
    "warm_a": _hex(EMBER_WARM_A),
    "warm_b": _hex(EMBER_WARM_B),
}
BAND_COLORS = (EMBER_CORE, EMBER_MID, EMBER_WARM_A, EMBER_WARM_B, EMBER_MID)


def _state_key(state: Any) -> str:
    """Normalized state key; unknown -> ``"idle"`` (fail-safe)."""
    try:
        key = str(state or "").strip().lower()
    except Exception:
        return "idle"
    if key not in STATE_COLORS:
        return "idle"
    return key


def state_colors(state: Any) -> Dict[str, Tuple[int, int, int]]:
    """Per-state color family (copy); unknown -> idle. Never raises."""
    try:
        return dict(STATE_COLORS[_state_key(state)])
    except Exception:
        return dict(STATE_COLORS["idle"])


def band_colors(state: Any) -> Tuple[Tuple[int, int, int], ...]:
    """5 band colors for the state (same order as ``BAND_COLORS``).

    Mirrors the legacy order (core, mid, warm_a, warm_b, mid) so the
    gradient stack reads identically in every state. Never raises.
    """
    try:
        fam = state_colors(state)
        return (fam["core"], fam["mid"], fam["warm_a"],
                fam["warm_b"], fam["mid"])
    except Exception:
        return BAND_COLORS


def clamp_dt(dt: float, max_dt: float = MAX_DT) -> float:
    """Clamp one clock step to [0, max_dt]. Never raises."""
    try:
        step = float(dt)
    except (TypeError, ValueError):
        return 0.0
    try:
        cap = float(max_dt)
    except (TypeError, ValueError):
        cap = MAX_DT
    if not (step >= 0):
        return 0.0
    return min(step, max(0.0, cap))


def advance_render_time(prev_render_t: float, prev_mono: Any,
                        now_mono: Any,
                        max_dt: float = MAX_DT) -> float:
    """Advance a render clock by wall time, hitch-clamped.

    ``prev_render_t`` + ``min(now_mono - prev_mono, max_dt)``. A
    ``None``/bad ``prev_mono`` holds the clock (first frame has no step
    yet). Normal frames pass through exactly, so the same wall-time
    reaches the same phase regardless of frame count; only hitches
    clamp. Never raises.
    """
    try:
        base = float(prev_render_t)
    except (TypeError, ValueError):
        base = 0.0
    try:
        if prev_mono is None:
            return base
        step = float(now_mono) - float(prev_mono)
    except (TypeError, ValueError):
        return base
    return base + clamp_dt(step, max_dt)


def frame_time_stats(dts_ms: Any) -> Dict[str, float]:
    """Distribution of inter-frame times in ms: count/p50/p95/max/mean.

    Pure, hermetic, never raises; empty input yields zeros. Percentile
    is nearest-rank so a steady 33ms stream reports p95 == 33.
    """
    try:
        vals = sorted(float(v) for v in (dts_ms or []))
    except (TypeError, ValueError):
        return {"count": 0, "p50": 0.0, "p95": 0.0,
                "max": 0.0, "mean": 0.0}
    n = len(vals)
    if n == 0:
        return {"count": 0, "p50": 0.0, "p95": 0.0,
                "max": 0.0, "mean": 0.0}

    def _rank(p: float) -> float:
        import math as _m
        k = max(1, min(n, int(_m.ceil(p * n))))
        return vals[k - 1]

    try:
        mean = sum(vals) / n
    except Exception:
        mean = 0.0
    return {"count": float(n), "p50": float(_rank(0.50)),
            "p95": float(_rank(0.95)), "max": float(vals[-1]),
            "mean": float(mean)}


def resolve_params(state: Any) -> Dict[str, float]:
    """Per-state speed/brightness/gain table; unknown -> idle (fail-safe)."""
    try:
        key = str(state or "").strip().lower()
    except Exception:
        key = ""
    if key not in _STATE_PARAMS:
        key = "idle"
    return dict(_STATE_PARAMS[key])


def effective_time(t: float, reduced_motion: bool) -> float:
    """Freeze time for reduced-motion (static frame); never raises."""
    if reduced_motion:
        return 0.0
    try:
        return float(t)
    except (TypeError, ValueError):
        return 0.0


def should_advance(last_ms: float, now_ms: float,
                   frame_ms: float = FRAME_MS) -> bool:
    """True once a full frame budget elapsed (~30fps cap). Never raises."""
    try:
        return (float(now_ms) - float(last_ms)) >= float(frame_ms)
    except (TypeError, ValueError):
        return True


def needs_frame(visible: bool, collapsed: bool) -> bool:
    """Pause rule: no animation while hidden or collapsed to the dot."""
    return bool(visible) and not bool(collapsed)


def gaussian_envelope(x: float, w: float) -> float:
    """Center-peaked falloff, exactly 1.0 at the middle. Never raises."""
    try:
        width = float(w)
        if width <= 0:
            return 0.0
        u = (float(x) / width - 0.5) * ENVELOPE_K
        return math.exp(-u * u)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _organic_phase(t: float, seed: float) -> float:
    """Deterministic pseudo-noise phase offset in ~[-1, 1].

    Several incommensurate sines of the frame clock (seeded per band by
    its phase) so the summed bands drift / breathe organically instead
    of marching mechanically. Pure function of ``t`` (and the seed):
    identical ``t`` always yields the identical offset — NEVER random.
    Never raises.
    """
    try:
        tt = float(t)
        sd = float(seed)
    except (TypeError, ValueError):
        return 0.0
    except Exception:
        return 0.0
    try:
        return (0.5 * math.sin(tt * 0.9 + sd * 0.13)
                + 0.3 * math.sin(tt * 1.7 + sd * 0.41)
                + 0.2 * math.sin(tt * 2.6 + sd * 0.77))
    except Exception:
        return 0.0


def ambient_breath(t: float) -> float:
    """Slow deterministic ambient swell in ``[1-GAIN, 1+GAIN]``.

    The idle "alive" life: a single very slow sine of the frame clock
    applied to the aura/glow/core radii. Pure function of ``t`` (no
    randomness), so the renderer stays byte-pure. Never raises.
    """
    try:
        return 1.0 + AMBIENT_BREATH_GAIN * math.sin(
            float(t) * AMBIENT_BREATH_HZ * 2.0 * math.pi)
    except (TypeError, ValueError, OverflowError):
        return 1.0


def band_shade(fam_bands: Any, index: int, t: float) -> Tuple[int, int, int]:
    """Color for one band: family ramp + a subtle deterministic hue nudge.

    Blends the band's family entry a little toward its neighbour with a
    slow per-band oscillator, so the ramp shifts gently across bands
    (color richness) instead of every band reading identically. A convex
    mix *inside* the family guarantees the state's dominant channel is
    preserved. Malformed input -> the plain family entry. Never raises.
    """
    try:
        n = len(fam_bands)
        i = int(index) % max(1, n)
        a = fam_bands[i]
        b = fam_bands[(i + 1) % n]
        w = BAND_TINT_GAIN * (0.5 + 0.5 * math.sin(
            float(t) * 0.6 + i * 1.3))
        return (round(a[0] + (b[0] - a[0]) * w),
                round(a[1] + (b[1] - a[1]) * w),
                round(a[2] + (b[2] - a[2]) * w))
    except Exception:
        try:
            fb = fam_bands[index % len(fam_bands)]
            return (int(fb[0]), int(fb[1]), int(fb[2]))
        except Exception:
            return EMBER_CORE


def band_point(x: float, t: float, spec: Tuple[float, float, float, float, float],
               width: float, cy: float, radius: float,
               band_gain: float = 1.0, speed: float = 1.0,
               organic: float = 1.0) -> float:
    """Y of one summed-sine band at canvas x (3 harmonics x envelope).

    Own scaling: the hero amp is mapped so ``REF_AMP`` spans the orb
    radius, times the per-state band gain; the harmonic sum is
    normalized so the peak equals that scaled amp at envelope 1.0.
    The result is clamped to the orb disc (Tk cannot clip to a circle).
    ``organic`` scales a small deterministic per-band phase wobble
    (0 disables it); it is a pure function of ``t``, never random.
    """
    try:
        amp, freq, spd, phase, _op = spec
        tt = float(t) * float(speed)
        try:
            org = (_organic_phase(float(t), float(phase))
                   * ORGANIC_PHASE_GAIN * float(organic))
        except (TypeError, ValueError):
            org = 0.0
        amp_px = float(amp) * (float(radius) / REF_AMP) * float(band_gain)
        f = float(freq)
        h1 = float(x) * f + tt * float(spd) + float(phase) + org
        h2 = (float(x) * f * 2.7 + tt * float(spd) * 1.3
              + float(phase) * 1.7 + org * 1.7)
        h3 = (float(x) * f * 5.1 + tt * float(spd) * 2.1
              + float(phase) * 0.6 + org * 0.6)
        wave = (math.sin(h1) + H2_WEIGHT * math.sin(h2)
                + H3_WEIGHT * math.sin(h3)) / H_NORM
        y = float(cy) + amp_px * wave * gaussian_envelope(float(x), float(width))
    except (TypeError, ValueError, OverflowError):
        return float(cy) if isinstance(cy, (int, float)) else 0.0
    try:
        lo, hi = float(cy) - float(radius), float(cy) + float(radius)
    except (TypeError, ValueError):
        return y
    return min(hi, max(lo, y))


def band_polyline(spec: Tuple[float, float, float, float, float], t: float,
                  width: float, cy: float, radius: float, band_gain: float,
                  speed: float, step: int = 4) -> List[float]:
    """Flat [x0,y0,x1,y1,...] polyline for one band (pre-steps, no churn)."""
    pts: List[float] = []
    try:
        w = max(8.0, float(width))
        st = max(2, int(step))
    except (TypeError, ValueError):
        return pts
    x = 0.0
    bp = band_point  # local bind: no attribute churn in the hot loop
    while x <= w:
        pts.append(x)
        pts.append(bp(x, t, spec, w, cy, radius, band_gain, speed))
        x += st
    return pts


def _spec_vector(values: Any) -> Optional[List[float]]:
    """Validated 12-float 0..1 spectrum, else None (fail-soft)."""
    try:
        vals = list(values)
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    if len(vals) != SPEC_BARS:
        return None
    out: List[float] = []
    try:
        for v in vals:
            f = float(v)
            if f != f:
                return None
            out.append(max(0.0, min(1.0, f)))
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    return out


def _bar_height(value: float, radius: float, band_gain: float) -> float:
    """Log-scaled bar half-height: 0 -> 0, 1 -> radius * gain."""
    try:
        return max(0.0, float(radius) * math.log10(1.0 + 9.0 * value)
                   * float(band_gain))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def spec_bar_rects(spectrum: Any, peaks: Any, cx: float, cy: float,
                   radius: float,
                   band_gain: float = 1.0) -> List[Dict[str, Any]]:
    """12 vertical-bar geometries for a spectrum (pure, hermetic).

    Even layout across the orb disc; heights log-scaled so quiet bands
    stay visible and loud bands never clip past the disc. Each rect is
    ``{"band", "x", "top", "bottom", "peak_top", "value", "half_w"}``.
    Malformed spectrum -> ``[]`` (the caller falls back to the sine
    loop). Never raises; carries no time phase (bars are a snapshot).
    """
    bands = _spec_vector(spectrum)
    if bands is None:
        return []
    caps = _spec_vector(peaks) if peaks is not None else list(bands)
    if caps is None:
        caps = list(bands)
    try:
        cx_f, cy_f = float(cx), float(cy)
        r_f = max(4.0, float(radius))
        gain = max(0.0, float(band_gain))
    except (TypeError, ValueError):
        return []
    width = r_f * 2.0
    slot = width / SPEC_BARS
    rects: List[Dict[str, Any]] = []
    for i in range(SPEC_BARS):
        h = min(r_f, _bar_height(bands[i], r_f, gain))
        ph = min(r_f, _bar_height(max(bands[i], caps[i]), r_f, gain))
        x = cx_f - r_f + (i + 0.5) * slot
        rects.append({
            "band": i,
            "x": x,
            "top": max(cy_f - r_f, cy_f - h),
            "bottom": min(cy_f + r_f, cy_f + h),
            "peak_top": max(cy_f - r_f, cy_f - ph),
            "value": bands[i],
            "half_w": max(1.0, slot * 0.28),
            "height": h,
        })
    return rects


def ring_phase(t: float, index: int, count: int, ring_speed: float) -> float:
    """0..1 expansion phase of one pulse ring (evenly staggered)."""
    try:
        n = max(1, int(count))
        ph = (float(t) * float(ring_speed) + float(index) / n) % 1.0
        return ph if ph >= 0 else ph + 1.0
    except (TypeError, ValueError):
        return 0.0


def ring_alpha(phase01: float, brightness: float) -> float:
    """Rings fade as they expand (fading opacity discipline)."""
    try:
        return max(0.0, (1.0 - float(phase01))) * 0.35 * float(brightness)
    except (TypeError, ValueError):
        return 0.0


def render(canvas: Any, cx: float, cy: float, radius: float, t: float,
           state: Any = "idle", reduced_motion: bool = False,
           level: Any = None, spectrum: Any = None,
           spec_peaks: Any = None, colors: Any = None,
           speed_scale: Any = 1.0) -> Dict[str, Any]:
    """Draw one ember-orb frame on a canvas-like object.

    Draw order (back to front): ambient glow ovals (stippled falloff),
    pulse rings (expanding outlines), wave bands (polylines) OR
    equalizer bars, core disc.
    ``level`` is the talk-meter 0..1 float (or None): it modulates the
    listening state's band_gain/brightness (silence visibly settles,
    speech visibly pumps) while every other state renders byte-identical
    with or without it, and ``None`` renders exactly today's loop.
    ``spectrum`` is the smoothed 12-float equalizer vector (or None):
    in the listening/speaking states it replaces the 5 sine polylines
    with 12 log-scaled vertical bars (+ slow-falling peak-hold cap
    ticks from ``spec_peaks``) in the same color family, and its energy
    drives the speaking state's pump (Qubit's own voice deforms the
    orb); thinking/working/idle/error ignore it (no audio to analyse —
    byte-identical rendering); ``None`` (or malformed) renders exactly
    today's sine loop (fail-soft). Bars carry no time phase, so
    reduced-motion draws the identical static snapshot.
    ``colors`` (optional) overrides the palette: a family dict from
    ``ColorTransition.update`` (or a bare core RGB tuple) lets a caller
    cross-fade state colors frame-by-frame; ``None`` reads the state's
    static family exactly as before.
    ``speed_scale`` (optional, default 1.0) multiplies the internal
    animation clock ``tt`` (clamped to 0.2..3.0); 1.0 leaves the clock
    untouched. The polish terms — a deterministic organic band wobble, a
    soft outer aura, and an energy-driven living pulse on the core disc
    and glow — are all pure functions of ``tt`` and the audio energy, so
    the same inputs always render the same frame (never random).
    Returns frame stats for hermetic proof (no window needed):
    ``{"ovals", "lines", "points", "min_y", "max_y", "fills", "bars"}``.
    Never raises; a ``None`` canvas still yields stats shape with zeros.
    """
    params = resolve_params(state)
    key = _state_key(state)
    if isinstance(colors, dict):
        fam = dict(colors)
        fam_bands = bands_from_family(fam)
    elif colors is not None:
        fam = family_from_core(colors)
        fam_bands = bands_from_family(fam)
    else:
        fam = state_colors(state)
        fam_bands = band_colors(state)
    speed = params["speed"]
    brightness = params["brightness"]
    band_gain = params["band_gain"]
    # Voice pump + polish energy: listening reads the mic talk-meter,
    # speaking reads the TTS spectrum energy — both settle and swell with
    # the actual audio. ``pulse01`` (0..1) is the living-pulse depth; for
    # listening, no level reads as the full reference (listening_gain
    # treats None as 1.0), so level=None and level=1.0 render byte-for-byte
    # the same frame (the existing loud==baseline contract).
    energy: Optional[float] = None
    pulse01 = 0.0
    onset = 0.0
    if key in ("listening", "speaking"):
        try:
            if key == "listening":
                pulse01 = listening_gain(level)
                if level is not None:
                    band_gain = float(band_gain) * listening_gain(level)
                    brightness = (float(brightness)
                                  * listening_brightness(level))
            else:
                energy = (spectrum_energy(spectrum)
                          if spectrum is not None else None)
                if energy is not None:
                    pump = listening_gain(energy)
                    band_gain = float(band_gain) * pump
                    brightness = (float(brightness)
                                  * listening_brightness(energy))
                    pulse01 = pump
            # Onset transient: how spikily the live spectrum hits. Only a
            # real spectrum can carry it, so a no-audio frame keeps its
            # exact geometry (``onset`` stays 0 -> every punch term is 0).
            if spectrum is not None:
                _spec_ok = _spec_vector(spectrum)
                if _spec_ok is not None:
                    _caps_ok = (_spec_vector(spec_peaks)
                                if spec_peaks is not None else None)
                    onset = onset_punch(_spec_ok, _caps_ok)
        except Exception:
            energy = None
            pulse01 = 0.0
            onset = 0.0
    ring_count = int(params["rings"])
    ring_speed = params["ring_speed"]
    glow_layers = int(params["glow"])
    tt = effective_time(t, reduced_motion) * _speed_scale(speed_scale)
    try:
        cx_f, cy_f, r_f = float(cx), float(cy), max(4.0, float(radius))
    except (TypeError, ValueError):
        return {"ovals": 0, "lines": 0, "points": 0, "min_y": 0.0,
                "max_y": 0.0, "fills": [], "bars": False}
    width = r_f * 2.0
    ovals = 0
    lines = 0
    points = 0
    min_y = cy_f + r_f
    max_y = cy_f - r_f
    fills: List[str] = []

    def _oval(x0: float, y0: float, x1: float, y1: float, **kw: Any) -> None:
        nonlocal ovals
        ovals += 1
        if "fill" in kw and isinstance(kw["fill"], str):
            fills.append(kw["fill"])
        if canvas is None:
            return
        try:
            canvas.create_oval(x0, y0, x1, y1, **kw)
        except Exception:
            pass

    def _line(pts: List[float], **kw: Any) -> None:
        nonlocal lines, points, min_y, max_y
        if len(pts) < 4:
            return
        lines += 1
        points += len(pts) // 2
        try:
            ys = pts[1::2]
            min_y = min(min_y, min(ys))
            max_y = max(max_y, max(ys))
        except (TypeError, ValueError):
            pass
        if "fill" in kw and isinstance(kw["fill"], str):
            fills.append(kw["fill"])
        if canvas is None:
            return
        try:
            canvas.create_line(*pts, **kw)
        except Exception:
            pass

    # -- living pulse + soft outer aura (pure fn of tt + audio energy) ----
    # ``breathe`` == 1.0 with no audio, so the un-modulated frame keeps
    # its exact geometry; it only swells with real voice energy. The
    # energy is eased (smoothstep) so the pump accelerates and settles
    # instead of snapping; 0 and 1 map exactly to themselves, so the
    # loud/quiet end-stop contracts are unchanged.
    pulse_eased = _smoothstep(pulse01) if pulse01 > 0.0 else 0.0
    onset_eased = _smoothstep(onset) if onset > 0.0 else 0.0
    ambient = ambient_breath(tt)
    breathe = 1.0
    if pulse_eased > 0.0 or onset_eased > 0.0:
        try:
            breathe = (1.0 + BREATH_GAIN * pulse_eased * (
                0.8 + 0.2 * math.sin(tt * 0.7 * math.pi))
                + ONSET_PUNCH_GAIN * onset_eased)
        except (ValueError, OverflowError):
            breathe = 1.0
    # Aura: two cheap translucent halos behind the glow that brighten with
    # energy (no blur, no deps — big stippled ovals only). The ambient
    # breath keeps them slowly swelling even when idle.
    for _ai, _frac in enumerate((0.98, 0.82)):
        _rr = r_f * _frac * (1.0 + 0.02 * pulse_eased
                             + 0.015 * onset_eased) * ambient
        _base = AURA_BASE * (1.0 if _ai == 0 else 0.8)
        _alpha = min(1.0, (_base + AURA_GAIN * pulse_eased
                           + ONSET_AURA_GAIN * onset_eased) * brightness)
        _fill = _hex(_mix(fam["warm_b"], NEAR_BLACK, _alpha))
        _kw: Dict[str, Any] = {"fill": _fill, "outline": ""}
        if _ai == 0:
            _kw["stipple"] = "gray12"
        _oval(cx_f - _rr, cy_f - _rr, cx_f + _rr, cy_f + _rr, **_kw)

    # -- ambient mesh: 1-2 slowly-drifting radial glows, layered ovals ----
    # Two-frequency drift + the deterministic organic wobble so the glow
    # never marches mechanically (same t -> same offset, never random).
    # Voice energy widens the parallax so the glow slabs slide ahead of
    # the core (a layered 3-D read); at no energy the multiplier is 1.0,
    # so a bare frame is byte-identical to the pre-polish renderer.
    _drift_wob = _organic_phase(tt, 3.0)
    drift_x = ((math.sin(tt * 0.23) * 0.75
                + math.sin(tt * 0.41 + 1.1) * 0.25) * r_f * 0.12
               + _drift_wob * r_f * 0.02)
    drift_y = ((math.cos(tt * 0.19 + 1.3) * 0.75
                + math.cos(tt * 0.37 + 0.4) * 0.25) * r_f * 0.12
               + _drift_wob * r_f * 0.02)
    parallax = (1.0 + PARALLAX_GAIN * pulse_eased
                + ONSET_PARALLAX_GAIN * onset_eased)
    glow_sets = (
        (fam["warm_b"], (0.95, 0.72, 0.50)),
        (fam["mid"], (0.80, 0.60, 0.40)),
    )[:max(1, min(2, glow_layers))]
    for gi, (glow_rgb, fracs) in enumerate(glow_sets):
        _sign = 1 if gi == 0 else -1
        gx = cx_f + drift_x * _sign * parallax
        gy = cy_f + drift_y * _sign * parallax
        for li, frac in enumerate(fracs):
            rr = r_f * frac * breathe * ambient
            alpha = (0.10 + 0.08 * (len(fracs) - li) / len(fracs)) * brightness
            fill = _hex(_mix(glow_rgb, NEAR_BLACK, min(1.0, alpha * 2.2)))
            kw: Dict[str, Any] = {"fill": fill, "outline": ""}
            if li == 0:
                kw["stipple"] = "gray25"
            _oval(gx - rr, gy - rr, gx + rr, gy + rr, **kw)

    # -- concentric pulse rings: expand 0 -> maxR, fading -----------------
    for i in range(max(1, ring_count)):
        ph = ring_phase(tt, i, max(1, ring_count), ring_speed)
        rr = max(1.0, ph * r_f)
        alpha = ring_alpha(ph, brightness)
        outline = _hex(_mix(fam["warm_a"], NEAR_BLACK, min(1.0, 0.25 + alpha * 2)))
        _oval(cx_f - rr, cy_f - rr, cx_f + rr, cy_f + rr,
              outline=outline, width=2 if i == 0 else 1)

    # -- soft outward energy ripple (gated on live audio; decays) ---------
    # One translucent ring whose radius grows with the clock phase and
    # whose opacity falls off as it expands, its depth scaled by the live
    # talk-meter / spectrum level. It is only drawn when a real sample
    # exists, so a bare or non-voice frame never grows one (byte-identical
    # to the pre-polish renderer). Outline-only: it never enters ``fills``.
    try:
        if key == "speaking":
            ripple_amp = (max(0.0, min(1.0, float(energy)))
                          if energy is not None else 0.0)
        elif key == "listening":
            # ``pulse01`` already folds None -> the full old-helper
            # reference (1.0), so level=None and level=1.0 stay identical.
            ripple_amp = pulse01
        else:
            ripple_amp = 0.0
        if ripple_amp > 0.02:
            _rp = (tt * RIPPLE_SPEED) % 1.0
            rphase = _rp if _rp >= 0 else _rp + 1.0
            rr = r_f * (0.45 + 0.60 * rphase)
            ralpha = min(1.0, RIPPLE_ALPHA * ripple_amp
                         * (1.0 - rphase) * (0.4 + RIPPLE_GAIN) * brightness)
            rout = _hex(_mix(fam["hi"], NEAR_BLACK, ralpha))
            _oval(cx_f - rr, cy_f - rr, cx_f + rr, cy_f + rr,
                  outline=rout, width=1)
    except Exception:
        pass

    # -- equalizer bars XOR 5 sine bands (voice states with a spectrum) ---
    bars_on = False
    try:
        if _state_key(state) in ("listening", "speaking"):
            bands = _spec_vector(spectrum)
            if bands is not None:
                caps = (_spec_vector(spec_peaks)
                        if spec_peaks is not None else list(bands))
                if caps is None:
                    caps = list(bands)
                rects = spec_bar_rects(bands, caps, cx_f, cy_f, r_f,
                                       band_gain)
                for ri, rc in enumerate(rects):
                    base = band_shade(fam_bands, ri, tt)
                    top = rc["top"]
                    bottom = rc["bottom"]
                    span = bottom - top
                    steps = 1 if span < 2.0 else BAR_GRADIENT_STEPS
                    # Vertical gradient: stacked segments, dim at the base
                    # and brighter toward the tip (still one family hue).
                    for si in range(steps):
                        f0 = si / steps
                        f1 = (si + 1) / steps
                        y_hi = top + span * f0
                        y_lo = top + span * f1
                        shade = 0.55 + BAR_TIP_GAIN * (1.0 - f0)
                        alpha = min(1.0, (0.24 + 0.66 * rc["value"])
                                    * shade * brightness)
                        fill = _hex(_mix(base, NEAR_BLACK, alpha))
                        _line([rc["x"], y_hi, rc["x"], y_lo],
                              fill=fill, width=3)
                    hw = rc["half_w"]
                    cap_alpha = min(1.0, 0.35 + 0.65 * brightness)
                    cap_fill = _hex(_mix(base, NEAR_BLACK, cap_alpha))
                    _line([rc["x"] - hw, rc["peak_top"],
                           rc["x"] + hw, rc["peak_top"]],
                          fill=cap_fill, width=1)
                bars_on = True
    except Exception:
        bars_on = False
    if not bars_on:
        # -- 5 wave bands: summed sines x gaussian envelope ---------------
        x0 = cx_f - r_f
        for bi, spec in enumerate(BAND_SPECS):
            _amp, _f, _s, _p, op = spec
            pts = band_polyline(spec, tt, width, cy_f, r_f, band_gain,
                                speed)
            shifted: List[float] = []
            for k in range(0, len(pts), 2):
                shifted.append(pts[k] + x0)
                shifted.append(pts[k + 1])
            alpha = min(1.0, float(op) * brightness * 3.0)
            fill = _hex(_mix(band_shade(fam_bands, bi, tt),
                             NEAR_BLACK, alpha))
            _line(shifted, fill=fill, width=2 if bi >= 3 else 1,
                  smooth=True)

    # -- core disc: solid ember heart (subtle energy pulse in radius) ------
    # The core counter-slides a touch against the glow parallax (depth)
    # and shares the ambient breath, so the heart visibly lives even idle.
    core_alpha = min(1.0, 0.55 + 0.35 * brightness)
    core = _hex(_mix(fam["core"], NEAR_BLACK, core_alpha))
    core_pulse = 1.0
    if pulse_eased > 0.0 or onset_eased > 0.0:
        try:
            core_pulse = (1.0 + CORE_PULSE_GAIN * pulse_eased * (
                0.5 + 0.5 * math.sin(tt * CORE_PULSE_HZ))
                + ONSET_CORE_GAIN * onset_eased)
        except (ValueError, OverflowError):
            core_pulse = 1.0
    core_dx = -drift_x * CORE_PARALLAX_GAIN * pulse_eased
    core_dy = -drift_y * CORE_PARALLAX_GAIN * pulse_eased
    cr = r_f * 0.34 * core_pulse * ambient
    _oval(cx_f + core_dx - cr, cy_f + core_dy - cr,
          cx_f + core_dx + cr, cy_f + core_dy + cr,
          fill=core, outline="")

    return {"ovals": ovals, "lines": lines, "points": points,
            "min_y": min_y, "max_y": max_y, "fills": fills,
            "bars": bars_on}


__all__ = [
    "FRAME_MS",
    "MAX_DT",
    "EMBER_CORE",
    "EMBER_MID",
    "EMBER_WARM_A",
    "EMBER_WARM_B",
    "EMBER_HI",
    "NEAR_BLACK",
    "STATE_COLORS",
    "STATE_CORES",
    "COLOR_TRANSITION_S",
    "SPEED_SCALE_MIN",
    "SPEED_SCALE_MAX",
    "state_core",
    "family_from_core",
    "bands_from_family",
    "rgb_to_oklab",
    "oklab_to_rgb",
    "blend_core",
    "ColorTransition",
    "spectrum_energy",
    "INDICATOR_RED",
    "BAND_SPECS",
    "resolve_params",
    "state_colors",
    "band_colors",
    "clamp_dt",
    "advance_render_time",
    "frame_time_stats",
    "effective_time",
    "should_advance",
    "needs_frame",
    "gaussian_envelope",
    "band_point",
    "band_polyline",
    "ring_phase",
    "ring_alpha",
    "render",
    "ambient_breath",
    "band_shade",
    "listening_gain",
    "listening_brightness",
    "LISTEN_LEVEL_FLOOR",
    "LISTEN_BRIGHT_FLOOR",
    "SPEC_BARS",
    "SPEC_ATTACK_S",
    "SPEC_RELEASE_S",
    "SPEC_PEAK_FALL",
    "SpectrumSmoother",
    "ENERGY_ATTACK_S",
    "ENERGY_RELEASE_S",
    "EnergySmoother",
    "AMBIENT_BREATH_GAIN",
    "AMBIENT_BREATH_HZ",
    "PARALLAX_GAIN",
    "CORE_PARALLAX_GAIN",
    "BAND_TINT_GAIN",
    "RIPPLE_SPEED",
    "RIPPLE_GAIN",
    "RIPPLE_ALPHA",
    "ONSET_PUNCH_GAIN",
    "ONSET_AURA_GAIN",
    "ONSET_PARALLAX_GAIN",
    "ONSET_CORE_GAIN",
    "BAR_GRADIENT_STEPS",
    "BAR_TIP_GAIN",
    "onset_punch",
    "spec_bar_rects",
]
