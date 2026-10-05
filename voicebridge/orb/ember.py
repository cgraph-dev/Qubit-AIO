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
FRAME_MS = 33

#: Ember-orange family on near-black (own palette values in that family).
EMBER_CORE = (255, 84, 0)
EMBER_MID = (255, 100, 30)
EMBER_WARM_A = (255, 140, 60)
EMBER_WARM_B = (255, 180, 80)
EMBER_HI = (255, 200, 120)
NEAR_BLACK = (11, 13, 18)

#: Per-state color table (single source; both renderers read this).
#: Owner contract (2026-10-05, supersedes the 2026-10-04 verbatim rule):
#: GREEN = VOICE (listening and speaking share ONE green family — the
#: agent hearing you and the agent talking to you read as the same
#: voice-active state), YELLOW = THINKING (turn in flight, no audio
#: either way), ORANGE = IDLE, RED = NOT WORKING. Core+bands+rings+
#: glows all tint from the same family entry so the state reads as one
#: color; gradients are preserved (each family carries its own
#: hi/mid/warm ramp). Motion params in _STATE_PARAMS are untouched.
STATE_COLORS: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "idle": {
        "core": (255, 84, 0),
        "mid": (255, 100, 30),
        "warm_a": (255, 140, 60),
        "warm_b": (255, 180, 80),
        "hi": (255, 200, 120),
    },
    "listening": {
        "core": (60, 220, 120),
        "mid": (80, 226, 136),
        "warm_a": (112, 236, 162),
        "warm_b": (150, 244, 190),
        "hi": (190, 250, 216),
    },
    "thinking": {
        "core": (255, 205, 70),
        "mid": (255, 214, 94),
        "warm_a": (255, 226, 122),
        "warm_b": (255, 238, 152),
        "hi": (255, 247, 185),
    },
    "speaking": {
        "core": (60, 220, 120),
        "mid": (80, 226, 136),
        "warm_a": (112, 236, 162),
        "warm_b": (150, 244, 190),
        "hi": (190, 250, 216),
    },
    "error": {
        "core": (255, 60, 50),
        "mid": (255, 84, 70),
        "warm_a": (255, 122, 104),
        "warm_b": (255, 162, 142),
        "hi": (255, 202, 186),
    },
}

#: Not-working indicator (offline/notice dots): the error family core.
INDICATOR_RED = (255, 60, 50)

#: Wall-clock clamp: one frame may advance the render clock by at most
#: this many seconds. Normal 30fps frames (~33ms) pass through
#: untouched (phase identical regardless of frame count); only a hitch
#: / suspend / dropped-frame burst clamps instead of teleporting.
MAX_DT = 0.25

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


def band_point(x: float, t: float, spec: Tuple[float, float, float, float, float],
               width: float, cy: float, radius: float,
               band_gain: float = 1.0, speed: float = 1.0) -> float:
    """Y of one summed-sine band at canvas x (3 harmonics x envelope).

    Own scaling: the hero amp is mapped so ``REF_AMP`` spans the orb
    radius, times the per-state band gain; the harmonic sum is
    normalized so the peak equals that scaled amp at envelope 1.0.
    The result is clamped to the orb disc (Tk cannot clip to a circle).
    """
    try:
        amp, freq, spd, phase, _op = spec
        tt = float(t) * float(speed)
        amp_px = float(amp) * (float(radius) / REF_AMP) * float(band_gain)
        f = float(freq)
        h1 = float(x) * f + tt * float(spd) + float(phase)
        h2 = float(x) * f * 2.7 + tt * float(spd) * 1.3 + float(phase) * 1.7
        h3 = float(x) * f * 5.1 + tt * float(spd) * 2.1 + float(phase) * 0.6
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
           level: Any = None) -> Dict[str, Any]:
    """Draw one ember-orb frame on a canvas-like object.

    Draw order (back to front): ambient glow ovals (stippled falloff),
    pulse rings (expanding outlines), wave bands (polylines), core disc.
    ``level`` is the talk-meter 0..1 float (or None): it modulates ONLY
    the listening state's band_gain/brightness (silence visibly settles,
    speech visibly pumps) while every other state renders byte-identical
    with or without it, and ``None`` renders exactly today's loop. Color
    always comes from the state family (state.py owns color).
    Returns frame stats for hermetic proof (no window needed):
    ``{"ovals", "lines", "points", "min_y", "max_y", "fills"}``.
    Never raises; a ``None`` canvas still yields stats shape with zeros.
    """
    params = resolve_params(state)
    fam = state_colors(state)
    fam_bands = band_colors(state)
    speed = params["speed"]
    brightness = params["brightness"]
    band_gain = params["band_gain"]
    if _state_key(state) == "listening" and level is not None:
        try:
            band_gain = float(band_gain) * listening_gain(level)
            brightness = float(brightness) * listening_brightness(level)
        except Exception:
            pass
    ring_count = int(params["rings"])
    ring_speed = params["ring_speed"]
    glow_layers = int(params["glow"])
    tt = effective_time(t, reduced_motion)
    try:
        cx_f, cy_f, r_f = float(cx), float(cy), max(4.0, float(radius))
    except (TypeError, ValueError):
        return {"ovals": 0, "lines": 0, "points": 0, "min_y": 0.0,
                "max_y": 0.0, "fills": []}
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

    # -- ambient mesh: 1-2 slowly-drifting radial glows, layered ovals ----
    drift_x = math.sin(tt * 0.23) * r_f * 0.12
    drift_y = math.cos(tt * 0.19 + 1.3) * r_f * 0.12
    glow_sets = (
        (fam["warm_b"], (0.95, 0.72, 0.50)),
        (fam["mid"], (0.80, 0.60, 0.40)),
    )[:max(1, min(2, glow_layers))]
    for gi, (glow_rgb, fracs) in enumerate(glow_sets):
        gx = cx_f + drift_x * (1 if gi == 0 else -1)
        gy = cy_f + drift_y * (1 if gi == 0 else -1)
        for li, frac in enumerate(fracs):
            rr = r_f * frac
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

    # -- 5 wave bands: summed sines x gaussian envelope --------------------
    x0 = cx_f - r_f
    for bi, spec in enumerate(BAND_SPECS):
        _amp, _f, _s, _p, op = spec
        pts = band_polyline(spec, tt, width, cy_f, r_f, band_gain, speed)
        shifted: List[float] = []
        for k in range(0, len(pts), 2):
            shifted.append(pts[k] + x0)
            shifted.append(pts[k + 1])
        alpha = min(1.0, float(op) * brightness * 3.0)
        fill = _hex(_mix(fam_bands[bi % len(fam_bands)],
                         NEAR_BLACK, alpha))
        _line(shifted, fill=fill, width=2 if bi >= 3 else 1,
              smooth=True)

    # -- core disc: solid ember heart ---------------------------------------
    core_alpha = min(1.0, 0.55 + 0.35 * brightness)
    core = _hex(_mix(fam["core"], NEAR_BLACK, core_alpha))
    cr = r_f * 0.34
    _oval(cx_f - cr, cy_f - cr, cx_f + cr, cy_f + cr,
          fill=core, outline="")

    return {"ovals": ovals, "lines": lines, "points": points,
            "min_y": min_y, "max_y": max_y, "fills": fills}


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
    "listening_gain",
    "listening_brightness",
    "LISTEN_LEVEL_FLOOR",
    "LISTEN_BRIGHT_FLOOR",
]
