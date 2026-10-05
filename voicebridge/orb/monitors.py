"""Monitor-aware placement for the voice orb (X11, ``xrandr --query``).

Owner hardware 2026-10-04: DP-2 primary 2580x1080+0+0, HDMI-1
1920x1080+2580+0. The orb used to validate against the VIRTUAL
desktop (4500x1080), so a persisted x=4292 looked onscreen while
parking on the unwatched secondary output.

Rule: parse ``xrandr --query`` (no new deps), pick the PRIMARY
output rect (else the focused window's output, else first
connected), and default/validate/clamp ALL against that rect.
Every entry point is fail-closed and never raises: no xrandr, no
X11, or foreign output (e.g. Wayland) yields ``[]``/``None`` and
the caller keeps today's virtual-desktop behavior. Callers inject
``run`` for hermetic tests; production passes nothing.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

# e.g. "DP-2 connected primary 2580x1080+0+0 (normal left ...)"
_OUTPUT_RE = re.compile(
    r"^(\S+)\s+connected\s+(primary\s+)?(\d+)x(\d+)\+(\d+)\+(\d+)")

# e.g. "  Absolute upper-left X:  0" / "  Width: 4500" (xwininfo).
_XWIN_RE = {
    "x": re.compile(r"Absolute upper-left X:\s*(-?\d+)"),
    "y": re.compile(r"Absolute upper-left Y:\s*(-?\d+)"),
    "w": re.compile(r"^\s*Width:\s*(\d+)", re.MULTILINE),
    "h": re.compile(r"^\s*Height:\s*(\d+)", re.MULTILINE),
}


@dataclass(frozen=True)
class OutputRect:
    """One connected output: name + absolute rect + primary flag."""
    name: str
    x: int
    y: int
    w: int
    h: int
    primary: bool = False


def _default_run(argv: List[str], **kw: Any) -> Any:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=3,
        **{k: v for k, v in kw.items() if k != "timeout"})


def _out_of(result: Any) -> str:
    try:
        return str(getattr(result, "stdout", "") or "")
    except Exception:
        return ""


def parse_xrandr(text: object) -> List[OutputRect]:
    """Connected outputs with usable geometry ([] when foreign). Never raises."""
    try:
        lines = str(text or "").splitlines()
    except Exception:
        return []
    outs: List[OutputRect] = []
    try:
        for line in lines:
            try:
                m = _OUTPUT_RE.match(line.strip())
                if not m:
                    continue
                name, prim, w, h, x, y = (m.group(1), m.group(2),
                                          m.group(3), m.group(4),
                                          m.group(5), m.group(6))
                wi, hi = int(w), int(h)
                if wi <= 0 or hi <= 0:
                    continue
                outs.append(OutputRect(name=name, x=int(x), y=int(y),
                                       w=wi, h=hi,
                                       primary=bool(prim and prim.strip())))
            except (TypeError, ValueError):
                continue
    except Exception:
        return []
    return outs


def query_outputs(run: Optional[Callable[..., Any]] = None) -> List[OutputRect]:
    """Live ``xrandr --query`` outputs ([] on any failure). Never raises."""
    run = run or _default_run
    try:
        out = _out_of(run(["xrandr", "--query"], timeout=3))
    except Exception:
        return []
    if not out:
        return []
    return parse_xrandr(out)


def _active_window_center(run: Callable[..., Any]) -> Optional[tuple]:
    """Center of the focused window in root coords (None = unknown)."""
    try:
        from . import fullscreen as _fs
        win = _fs.active_window_id(run=run)
        if not win:
            return None
        out = _out_of(run(["xwininfo", "-id", win], timeout=3))
        if not out:
            return None
        vals = {}
        for key, rx in _XWIN_RE.items():
            m = rx.search(out)
            if not m:
                return None
            vals[key] = int(m.group(1))
        return (vals["x"] + vals["w"] // 2, vals["y"] + vals["h"] // 2)
    except Exception:
        return None


def _containing(outputs: List[OutputRect], px: int, py: int) -> Optional[OutputRect]:
    try:
        for o in outputs:
            if o.x <= px < o.x + o.w and o.y <= py < o.y + o.h:
                return o
    except Exception:
        return None
    return None


def pick_target(outputs: List[OutputRect],
                focused: Optional[OutputRect] = None) -> Optional[OutputRect]:
    """PRIMARY, else the focused window's output, else first connected.

    ``focused`` is an already-resolved output (the caller maps the
    active window through :func:`focused_output`); None skips that
    step. Never raises; None when ``outputs`` is empty.
    """
    try:
        outs = list(outputs or [])
        if not outs:
            return None
        for o in outs:
            if o.primary:
                return o
        if focused is not None and focused in outs:
            return focused
        return outs[0]
    except Exception:
        return None


def focused_output(outputs: List[OutputRect],
                   run: Optional[Callable[..., Any]] = None) -> Optional[OutputRect]:
    """Output holding the focused window's center (None = unknown)."""
    try:
        if not outputs:
            return None
        center = _active_window_center(run or _default_run)
        if center is None:
            return None
        return _containing(outputs, center[0], center[1])
    except Exception:
        return None


def target_rect(run: Optional[Callable[..., Any]] = None) -> Optional[OutputRect]:
    """The rect the orb must live on (None = keep current behavior).

    PRIMARY wins; without a primary flag the focused window's
    output wins; else the first connected output. xrandr absent or
    foreign yields None (fail-closed: the caller falls back to the
    virtual-desktop dims it uses today).
    """
    try:
        runner = run or _default_run
        outs = query_outputs(run=runner)
        if not outs:
            return None
        for o in outs:
            if o.primary:
                return o
        foc = focused_output(outs, run=runner)
        if foc is not None:
            return foc
        return outs[0]
    except Exception:
        return None


__all__ = [
    "OutputRect",
    "parse_xrandr",
    "query_outputs",
    "pick_target",
    "focused_output",
    "target_rect",
]
