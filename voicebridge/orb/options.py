"""Orb options: user-tunable appearance + behaviour, persisted to Qubit state.

One small JSON file under the canonical state dir (never the repo).
Every field is validated/clamped on load and on save, so a hand-edited or
corrupt file can never crash or mis-size the orb. All reads fail-closed
to the defaults; all writes are atomic (tmp + replace) and never raise.

This module is the shared interface between the settings panel, the
system tray, and the Qt/Tk views.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

OPTIONS_FILENAME = "voice_orb_options.json"

#: Clamp ranges (single source for the panel sliders + validation).
SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = 96, 320, 168
OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT = 0.35, 1.0, 1.0
SPEED_MIN, SPEED_MAX, SPEED_DEFAULT = 0.4, 2.0, 1.0

#: Per-action color overrides are optional; empty means "use ember's
#: built-in palette". Keys are the orb states.
_COLOR_STATES = ("idle", "listening", "thinking", "working", "speaking", "error")


@dataclass(frozen=True)
class OrbOptions:
    size_px: int = SIZE_DEFAULT
    opacity: float = OPACITY_DEFAULT
    locked: bool = False
    speed: float = SPEED_DEFAULT
    click_through: bool = False
    show_tray: bool = True
    #: state -> "#RRGGBB" override (empty = built-in palette).
    colors: Dict[str, str] = field(default_factory=dict)


def state_dir() -> Path:
    try:
        from voicebridge.config import canonical_state_dir
        return Path(canonical_state_dir())
    except Exception:
        return Path(os.path.expanduser("~/.local/state/qubit-voicebridge"))


def options_path() -> Path:
    return state_dir() / OPTIONS_FILENAME


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default
    except Exception:
        return default


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default
    except Exception:
        return default


def _normalize_hex(value: Any) -> Optional[str]:
    """'#RRGGBB' (case-normalized) or None when not a valid hex color."""
    try:
        s = str(value).strip()
    except Exception:
        return None
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        return None
    try:
        int(s, 16)
    except ValueError:
        return None
    return "#" + s.upper()


def clamp_options(opts: OrbOptions) -> OrbOptions:
    """Return a validated copy; never raises."""
    try:
        colors: Dict[str, str] = {}
        raw = opts.colors if isinstance(opts.colors, dict) else {}
        for state in _COLOR_STATES:
            hexv = _normalize_hex(raw.get(state))
            if hexv is not None:
                colors[state] = hexv
        return OrbOptions(
            size_px=_clamp_int(opts.size_px, SIZE_MIN, SIZE_MAX, SIZE_DEFAULT),
            opacity=_clamp_float(opts.opacity, OPACITY_MIN, OPACITY_MAX,
                                 OPACITY_DEFAULT),
            locked=bool(opts.locked),
            speed=_clamp_float(opts.speed, SPEED_MIN, SPEED_MAX, SPEED_DEFAULT),
            click_through=bool(opts.click_through),
            show_tray=bool(opts.show_tray),
            colors=colors,
        )
    except Exception:
        return OrbOptions()


def to_dict(opts: OrbOptions) -> Dict[str, Any]:
    o = clamp_options(opts)
    return {
        "size_px": o.size_px,
        "opacity": o.opacity,
        "locked": o.locked,
        "speed": o.speed,
        "click_through": o.click_through,
        "show_tray": o.show_tray,
        "colors": dict(o.colors),
    }


def from_dict(data: Any) -> OrbOptions:
    """Build validated options from a parsed JSON object; never raises."""
    if not isinstance(data, dict):
        return OrbOptions()
    return clamp_options(OrbOptions(
        size_px=data.get("size_px", SIZE_DEFAULT),
        opacity=data.get("opacity", OPACITY_DEFAULT),
        locked=data.get("locked", False),
        speed=data.get("speed", SPEED_DEFAULT),
        click_through=data.get("click_through", False),
        show_tray=data.get("show_tray", True),
        colors=data.get("colors", {}) if isinstance(
            data.get("colors"), dict) else {},
    ))


def load_options(path: Any = None) -> OrbOptions:
    """Read persisted options; missing/corrupt -> defaults. Never raises."""
    try:
        raw = Path(path) if path is not None else options_path()
        return from_dict(json.loads(raw.read_text(encoding="utf-8")))
    except Exception:
        return OrbOptions()


def save_options(opts: OrbOptions, path: Any = None) -> None:
    """Atomically persist options; never raises."""
    try:
        target = Path(path) if path is not None else options_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(to_dict(opts), indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                                   prefix=".orb-options-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, target)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass


def color_overrides(opts: OrbOptions) -> Dict[str, Tuple[int, int, int]]:
    """state -> (r,g,b) tuple for the states the user has recolored.

    Feed to ``ember.ColorTransition(overrides=...)``. Empty when the
    user left the built-in palette alone. Never raises.
    """
    out: Dict[str, Tuple[int, int, int]] = {}
    try:
        for state, hexv in (clamp_options(opts).colors or {}).items():
            h = _normalize_hex(hexv)
            if h is None:
                continue
            out[state] = (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))
    except Exception:
        return {}
    return out


def updated(opts: OrbOptions, **changes: Any) -> OrbOptions:
    """Return a clamped copy with ``changes`` applied. Never raises."""
    try:
        return clamp_options(replace(opts, **changes))
    except Exception:
        return clamp_options(opts)


__all__ = [
    "OrbOptions",
    "OPTIONS_FILENAME",
    "SIZE_MIN", "SIZE_MAX", "SIZE_DEFAULT",
    "OPACITY_MIN", "OPACITY_MAX", "OPACITY_DEFAULT",
    "SPEED_MIN", "SPEED_MAX", "SPEED_DEFAULT",
    "state_dir", "options_path",
    "clamp_options", "to_dict", "from_dict",
    "load_options", "save_options", "color_overrides", "updated",
]
