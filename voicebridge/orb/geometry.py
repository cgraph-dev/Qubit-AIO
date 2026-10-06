"""Orb geometry: pure position math (no Tk, no display needed).

Mirrors the dashboard widget position logic
(``frontend/qubit-web/src/lib/voice/waveform-state.ts``): bottom-right
default with a margin, a movement budget below which a press is a
click (fires the voice toggle) rather than a drag, and clamping so
the orb can never be lost off-screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Orb window edge length in px. Owner 2026-10-06: enlarged from 68 to
#: 168 so the equalizer bars/rings are legible (68px was too small to
#: read as a real equalizer).
ORB_PX = 168

#: Collapsed dot edge length in px.
DOT_PX = 16

#: Margin from the screen edge for the default position.
MARGIN = 16

#: Movement budget before a press becomes a drag (click still toggles).
#: Same value as the widget's VOICE_WAVEFORM_CLICK_DRAG_THRESHOLD_PX.
CLICK_DRAG_THRESHOLD_PX = 6

#: Minimum visible sliver kept on-screen when clamping.
#: Legacy: load-time validation and clamping now require the whole
#: window onscreen (68px orb / 16px dot), so a sliver park resets to
#: the default instead of sticking half off-screen. Kept exported for
#: backward compatibility only.
MIN_VISIBLE_PX = 32


@dataclass(frozen=True)
class OrbPosition:
    x: int
    y: int
    collapsed: bool = False


@dataclass(frozen=True)
class ScreenSize:
    width: int
    height: int


def default_position(screen_w: int, screen_h: int) -> OrbPosition:
    """Bottom-right default, kept inside the screen with a margin."""
    width = max(0, int(screen_w or 0))
    height = max(0, int(screen_h or 0))
    return OrbPosition(
        x=max(MARGIN, width - ORB_PX - MARGIN),
        y=max(MARGIN, height - ORB_PX - MARGIN),
        collapsed=False,
    )


def window_size_px(collapsed: bool = False) -> int:
    """Edge length of the window for the mode (orb 154px, dot 16px)."""
    return DOT_PX if collapsed else ORB_PX


def set_orb_size(px: Any) -> int:
    """Override the module ``ORB_PX`` at runtime (clamped 48..400).

    Runtime seam for the options size slider: every geometry helper and
    both views read ``ORB_PX``, so one call resizes the whole orb.
    Returns the value actually set; never raises.
    """
    global ORB_PX
    try:
        v = max(48, min(400, int(round(float(px)))))
    except (TypeError, ValueError):
        return ORB_PX
    except Exception:
        return ORB_PX
    ORB_PX = v
    return ORB_PX


def is_fully_onscreen(x: int, y: int, screen_w: int, screen_h: int,
                      collapsed: bool = False) -> bool:
    """True only when the whole window sits inside the live screen dims.

    Fail-closed: malformed coords or dims report offscreen (caller resets
    to the default). Uses the passed live dims only, never constants.
    """
    try:
        fx, fy = int(x), int(y)
        width, height = int(screen_w), int(screen_h)
    except (TypeError, ValueError):
        return False
    if width <= 0 or height <= 0:
        return False
    size = DOT_PX if collapsed else ORB_PX
    return 0 <= fx and 0 <= fy and fx + size <= width and fy + size <= height


def resolve_initial_position(stored: object, screen_w: int,
                             screen_h: int) -> tuple:
    """Validate a persisted position against live screen dims.

    Returns ``(position, reset, reason)``: the stored position untouched
    when it places the whole window onscreen, else the bottom-right
    default with ``reset=True`` and a short machine-readable reason
    (``missing`` / ``malformed`` / ``partially_offscreen``). Pure and
    idempotent: resolving an already-resolved position never resets.

    Owner 2026-10-04 (de-chromed orb): the collapsed state is
    unreachable (no minus button) and the orb renders always-expanded,
    so a persisted ``collapsed=True`` is migrated, never honoured — the
    stored x/y is validated against the EXPANDED size and the resolved
    position is always ``collapsed=False``. A dot-parked position that
    cannot hold the full orb resets to the default instead of trapping
    the window as a dot.
    """
    try:
        width, height = int(screen_w), int(screen_h)
    except (TypeError, ValueError):
        width, height = 0, 0
    default = default_position(width, height)
    if stored is None:
        return (default, True, "missing")
    try:
        x, y = int(stored.x), int(stored.y)  # type: ignore[union-attr]
    except (TypeError, ValueError, AttributeError):
        return (default, True, "malformed")
    if not is_fully_onscreen(x, y, width, height, False):
        return (default, True, "partially_offscreen")
    return (OrbPosition(x=x, y=y, collapsed=False), False, "onscreen")


def clamp_position(x: int, y: int, screen_w: int, screen_h: int,
                   collapsed: bool = False) -> OrbPosition:
    """Keep a position fully on-screen so the orb can never be lost.

    Bounds use the passed live screen dims and the mode's window size
    (68px orb / 16px dot), never constants for the screen. Drag callers
    pass the collapsed flag so the dot clamps to its own edge.
    """
    width = max(1, int(screen_w or 1))
    height = max(1, int(screen_h or 1))
    size = DOT_PX if collapsed else ORB_PX
    try:
        fx, fy = int(x), int(y)
    except (TypeError, ValueError):
        fx, fy = MARGIN, MARGIN
    return OrbPosition(
        x=min(max(0, fx), max(0, width - size)),
        y=min(max(0, fy), max(0, height - size)),
    )


def is_drag(dx: float, dy: float,
            threshold_px: float = CLICK_DRAG_THRESHOLD_PX) -> bool:
    """True once pointer travel exceeds the click-vs-drag budget."""
    try:
        return (float(dx) ** 2 + float(dy) ** 2) ** 0.5 > float(threshold_px)
    except (TypeError, ValueError):
        return False


# -- monitor-aware placement (owner 2026-10-04: orb lives on PRIMARY) ----
#
# The virtual desktop spans every output (here 4500x1080), so validating
# against it parks the orb on an unwatched secondary. These helpers
# default/validate/clamp against ONE output rect (absolute root coords)
# instead. ``rect`` is a monitors.OutputRect (duck-typed: x/y/w/h); all
# are pure, fail-closed, and never raise.

def _rect_bounds(rect: object) -> tuple:
    """(ox, oy, ow, oh) from an output rect; (0,0,0,0) when unusable."""
    try:
        ox, oy = int(rect.x), int(rect.y)  # type: ignore[union-attr]
        w = int(getattr(rect, "w", getattr(rect, "width", 0)))
        h = int(getattr(rect, "h", getattr(rect, "height", 0)))
    except (TypeError, ValueError, AttributeError):
        return (0, 0, 0, 0)
    if w <= 0 or h <= 0:
        return (0, 0, 0, 0)
    return (ox, oy, w, h)


def default_position_for_rect(rect: object) -> OrbPosition:
    """Bottom-right default of the rect, in absolute root coords."""
    ox, oy, w, h = _rect_bounds(rect)
    if w <= 0 or h <= 0:
        return OrbPosition(x=MARGIN, y=MARGIN, collapsed=False)
    return OrbPosition(
        x=max(ox + MARGIN, ox + w - ORB_PX - MARGIN),
        y=max(oy + MARGIN, oy + h - ORB_PX - MARGIN),
        collapsed=False,
    )


def is_within_rect(x: int, y: int, rect: object,
                   collapsed: bool = False) -> bool:
    """True only when the whole window sits inside the rect. Fail-closed."""
    ox, oy, w, h = _rect_bounds(rect)
    if w <= 0 or h <= 0:
        return False
    try:
        fx, fy = int(x), int(y)
    except (TypeError, ValueError):
        return False
    size = DOT_PX if collapsed else ORB_PX
    return (ox <= fx and oy <= fy
            and fx + size <= ox + w and fy + size <= oy + h)


def resolve_initial_position_for_rect(stored: object,
                                      rect: object) -> tuple:
    """Validate a persisted position against the output rect.

    Same ``(position, reset, reason)`` shape as
    :func:`resolve_initial_position`: the stored position untouched
    when the whole window sits on the rect, else the rect's
    bottom-right default with ``reset=True``. A position that is
    onscreen on the virtual desktop but on another output (e.g.
    x=4292 on HDMI-1 while the rect is DP-2) resets. Pure and
    idempotent.

    Owner 2026-10-04 (de-chromed orb): like
    :func:`resolve_initial_position`, a persisted ``collapsed=True``
    is migrated to always-expanded (validated against the expanded
    size), never honoured as a dot.
    """
    default = default_position_for_rect(rect)
    if stored is None:
        return (default, True, "missing")
    try:
        x, y = int(stored.x), int(stored.y)  # type: ignore[union-attr]
    except (TypeError, ValueError, AttributeError):
        return (default, True, "malformed")
    if not is_within_rect(x, y, rect, False):
        return (default, True, "partially_offscreen")
    return (OrbPosition(x=x, y=y, collapsed=False), False, "onscreen")


def clamp_to_rect(x: int, y: int, rect: object,
                  collapsed: bool = False) -> OrbPosition:
    """Keep a position fully on the rect (absolute coords). Never raises."""
    ox, oy, w, h = _rect_bounds(rect)
    if w <= 0 or h <= 0:
        return OrbPosition(x=MARGIN, y=MARGIN, collapsed=collapsed)
    size = DOT_PX if collapsed else ORB_PX
    try:
        fx, fy = int(x), int(y)
    except (TypeError, ValueError):
        fx, fy = ox + MARGIN, oy + MARGIN
    return OrbPosition(
        x=min(max(ox, fx), max(ox, ox + w - size)),
        y=min(max(oy, fy), max(oy, oy + h - size)),
        collapsed=collapsed,
    )


__all__ = [
    "ORB_PX",
    "DOT_PX",
    "MARGIN",
    "CLICK_DRAG_THRESHOLD_PX",
    "MIN_VISIBLE_PX",
    "OrbPosition",
    "ScreenSize",
    "default_position",
    "window_size_px",
    "set_orb_size",
    "is_fully_onscreen",
    "resolve_initial_position",
    "clamp_position",
    "is_drag",
    "default_position_for_rect",
    "is_within_rect",
    "resolve_initial_position_for_rect",
    "clamp_to_rect",
]
