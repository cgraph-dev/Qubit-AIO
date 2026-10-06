"""Fullscreen-focus detection for the voice orb (X11 only, EWMH).

Owner demand: the orb stays visible over everything EXCEPT fullscreen games.
While the focused window carries ``_NET_WM_STATE_FULLSCREEN`` the orb
withdraws; when focus leaves fullscreen it returns.

X11-only by design (``xprop`` over ``$DISPLAY``); Wayland is out of scope.
Every entry point is fail-closed and never raises: no X11, no ``xprop``,
unparsable output, or a failing command all mean "not fullscreen" (safe
no-op — the orb simply stays as it is). Callers inject ``run`` for hermetic
tests; production passes nothing (one cheap ``xprop`` per poll tick,
alongside the existing state poll — no new threads or processes beyond
what :meth:`VoiceOrb._poll_tick` already owns).
"""
from __future__ import annotations

import re
import subprocess
from typing import Any, Callable, List, Optional, Tuple

_ACTIVE_RE = re.compile(r"window id #\s*(0x[0-9a-fA-F]+|\d+)")


#: Per-probe subprocess budget: the probe runs on the orb mainloop inside
#: the existing state poll, so each xprop call gets ~0.5 s, never the old
#: 3 s (a sick X server wedged up to ~6 s of frames per tick). Both calls
#: fail-closed to "not fullscreen" on timeout, so a slow server only
#: skips one hide/show round instead of freezing the orb.
XPROP_TIMEOUT_S = 0.5


def _default_run(argv: List[str], **kw: Any) -> Any:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=XPROP_TIMEOUT_S,
        **{k: v for k, v in kw.items() if k != "timeout"})


def _out_of(result: Any) -> str:
    try:
        out = getattr(result, "stdout", "")
        return str(out or "")
    except Exception:
        return ""


def active_window_id(
        run: Optional[Callable[..., Any]] = None) -> Optional[str]:
    """Focused window id from the root ``_NET_ACTIVE_WINDOW`` (None = unknown).

    Never raises; None on any failure (no X11, no xprop, no active window).
    """
    run = run or _default_run
    try:
        out = _out_of(run(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                            timeout=XPROP_TIMEOUT_S))
    except Exception:
        return None
    try:
        m = _ACTIVE_RE.search(out or "")
        if not m:
            return None
        win = m.group(1).strip()
        if win in ("0x0", "0"):
            return None
        return win
    except Exception:
        return None


def window_states(win_id: str,
                  run: Optional[Callable[..., Any]] = None) -> List[str]:
    """EWMH state atoms for one window ([] when unknown). Never raises."""
    run = run or _default_run
    if not win_id:
        return []
    try:
        out = _out_of(run(["xprop", "-id", win_id, "_NET_WM_STATE"],
                            timeout=XPROP_TIMEOUT_S))
    except Exception:
        return []
    try:
        atoms = re.findall(r"_NET_WM_STATE_[A-Z_]+", out or "")
        seen: List[str] = []
        for atom in atoms:
            if atom not in seen:
                seen.append(atom)
        return seen
    except Exception:
        return []


def active_window_is_fullscreen(
        run: Optional[Callable[..., Any]] = None) -> bool:
    """True only when the focused window is EWMH-fullscreen. Never raises."""
    try:
        win = active_window_id(run=run)
        if not win:
            return False
        return "_NET_WM_STATE_FULLSCREEN" in window_states(win, run=run)
    except Exception:
        return False


def next_visibility(fullscreen_focused: bool, visible: bool,
                    auto_hidden: bool,
                    ingame_override: bool = False) -> Tuple[str, bool]:
    """Pure visibility rule: (action, new auto_hidden).

    - ``("hide", True)``: fullscreen focused while we are visible — withdraw.
    - ``("show", False)``: focus left fullscreen and WE hid it — restore
      (caller uses deiconify + lift only, never focus-forcing).
    - ``("none", …)``: everything else. In particular a window the owner
      closed themselves (withdrawn without our flag) is never forced back.

    In-game override (owner 2026-10-04, ``voice_edit.waveform_ingame``):
    when True the auto-withdraw is skipped — the orb stays mapped over
    fullscreen games — and an orb WE hid is restored even while fullscreen
    is still focused (the owner said SHOW while hidden). A window the owner
    closed themselves is still never forced back. Defaults False (today's
    hide-on-fullscreen + return-on-alt-tab).
    """
    try:
        fs = bool(fullscreen_focused)
        vis = bool(visible)
        auto = bool(auto_hidden)
        override = bool(ingame_override)
    except Exception:
        return ("none", bool(auto_hidden))
    if override:
        if not vis and auto:
            return ("show", False)
        return ("none", auto)
    if fs and vis and not auto:
        return ("hide", True)
    if not fs and auto:
        return ("show", False)
    return ("none", auto)


__all__ = ["active_window_id", "window_states",
           "active_window_is_fullscreen", "next_visibility",
           "XPROP_TIMEOUT_S"]
