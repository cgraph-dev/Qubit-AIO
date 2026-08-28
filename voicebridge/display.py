"""Active graphical-session resolution (portable DISPLAY handling).

The installed systemd units carry an explicit ``DISPLAY`` value (machine
specific; the bridge and monitor templates document ``echo $DISPLAY``). If that
value is missing or points at an X server that is not actually running, the
bridge's xinput hotkey listener and the monitor's notify-send would silently
fail. ``resolve_session_display()`` makes this portable: it keeps a valid
``DISPLAY``, or derives the active X11 display from the real Unix sockets under
``/tmp/.X11-unix``. Headless (no sockets) returns ``None`` and leaves DISPLAY
unset, preserving the documented headless/manual PTT mode.
"""
from __future__ import annotations

import glob
import os
from typing import Optional


def _x11_display_numbers() -> list:
    """Sorted real X11 display numbers from /tmp/.X11-unix (e.g. X0, X1)."""
    out = []
    for sock in glob.glob("/tmp/.X11-unix/X[0-9]*"):
        name = os.path.basename(sock)
        if name.startswith("X") and name[1:].isdigit():
            out.append(int(name[1:]))
    return sorted(out)


def resolve_session_display(env: Optional[dict] = None) -> Optional[str]:
    """Return a usable loopback X11 ``DISPLAY`` for this session, or None.

    - a ``DISPLAY`` already set to an existing X socket is kept unchanged;
    - otherwise the highest-numbered real X socket is chosen (a single active
      graphical session is the normal case; this host runs :1);
    - headless (no sockets) returns ``None``.
    """
    env = os.environ if env is None else env
    cur = (env.get("DISPLAY") or "").strip()
    numbers = _x11_display_numbers()
    if cur:
        num = cur.split(":", 1)[-1].split(".", 1)[0]
        if num.isdigit() and int(num) in numbers:
            return cur
    if not numbers:
        return None
    return f":{numbers[-1]}"


def ensure_session_display(env: Optional[dict] = None) -> Optional[str]:
    """Apply the resolved session display to the process environment.

    Returns the resolved display (or None headless). Only sets ``DISPLAY`` when
    a usable display is found; never clears an already-valid one.
    """
    disp = resolve_session_display(env)
    if disp:
        os.environ["DISPLAY"] = disp
    return disp