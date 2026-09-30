"""Active graphical-session resolution (portable DISPLAY handling).

The installed systemd units historically carried an explicit ``DISPLAY`` value
(machine specific). If that value is missing, stale, or points at an X server
that is not actually reachable from the service's session, the bridge's xinput
hotkey listener and the monitor's notify-send silently fail. This module makes
session resolution portable AND validated:

- a ``DISPLAY`` that is set AND genuinely reachable (bounded probe) is kept;
- otherwise the active X11 session is derived by PROBING the real sockets under
  ``/tmp/.X11-unix``; the resolver never guesses "the highest display" in a
  multi-session environment — an ambiguous result degrades to ``None``;
- headless (no sockets) returns ``None`` and leaves ``DISPLAY`` unset,
  preserving the documented headless/manual PTT mode.
"""
from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path
from typing import Optional


def _x11_display_numbers() -> list:
    """Sorted real X11 display numbers from /tmp/.X11-unix (e.g. X0, X1)."""
    out = []
    for sock in glob.glob("/tmp/.X11-unix/X[0-9]*"):
        name = os.path.basename(sock)
        if name.startswith("X") and name[1:].isdigit():
            out.append(int(name[1:]))
    return sorted(out)


def _display_number(display: str) -> Optional[int]:
    """Parse ``[host][:N][.screen]`` into the display number N, or None."""
    try:
        d = (display or "").strip()
        if not d:
            return None
        if ":" in d:
            d = d.split(":", 1)[1]
        d = d.split(".", 1)[0]
        if d.isdigit():
            return int(d)
    except Exception:
        pass
    return None


def _probe_display(display: str, env: Optional[dict] = None) -> bool:
    """Bounded reachability probe: does `xinput list` succeed on this display?

    ``xinput`` is the exact backend the hotkey listener uses, so its ability to
    connect is the correct definition of "genuinely reachable". Runs with a
    3-second timeout; any failure (bad display, wrong authority, xinput
    missing) returns False. When ``XAUTHORITY`` is unset, the conventional
    per-user ``~/.Xauthority`` is tried as a fallback.
    """
    full = dict(os.environ if env is None else env)
    full["DISPLAY"] = display
    if not full.get("XAUTHORITY"):
        home_auth = Path.home() / ".Xauthority"
        if home_auth.exists():
            full["XAUTHORITY"] = str(home_auth)
    try:
        proc = subprocess.run(
            ["xinput", "list", "--short"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=full, timeout=3)
        return proc.returncode == 0
    except Exception:
        return False


def _session_hint_display() -> Optional[str]:
    """DISPLAY hint exported by the user systemd instance (if any).

    The graphical login imports the session environment into the user systemd
    manager; when the unit inherits that (rather than overriding it), this is
    the authoritative session value. Bounded and guarded; failure returns None.
    """
    try:
        proc = subprocess.run(["systemctl", "--user", "show-environment"],
                              capture_output=True, text=True, timeout=3)
        for line in (proc.stdout or "").splitlines():
            if line.startswith("DISPLAY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def display_reachable(display: str, env: Optional[dict] = None) -> bool:
    """Public reachability check: can xinput connect to this X display?

    Used by the hotkey listener to validate a display BEFORE reporting hotkeys
    active and by the resolver while deriving the session.
    """
    if not display:
        return False
    return _probe_display(display, env)


def session_display_socket(env: Optional[dict] = None) -> Optional[str]:
    """LIGHTWEIGHT, non-probing DISPLAY for consumers that only need a valid
    X socket (e.g. notify-send), not XI2 reachability.

    Returns the current ``DISPLAY`` when its socket exists, else the single
    real socket, else None. Never runs a subprocess — suitable for paths that
    must not pay for or interfere with a reachability probe.
    """
    env = os.environ if env is None else env
    numbers = _x11_display_numbers()
    if not numbers:
        return None
    cur = (env.get("DISPLAY") or "").strip()
    if cur and _display_number(cur) in numbers:
        return cur
    if len(numbers) == 1:
        return f":{numbers[0]}"
    return None


def resolve_session_display(env: Optional[dict] = None) -> Optional[str]:
    """Return a REACHABLE X11 ``DISPLAY`` for this session, or None.

    Resolution order (never guesses a display):

    1. An explicitly-set ``DISPLAY`` is returned only when its X socket exists
       AND ``xinput`` can actually connect (validated, not assumed).
    2. Otherwise the user-systemd session hint is tried (bounded probe).
    3. Otherwise the real sockets are probed; exactly one reachable display is
       returned. If more than one is reachable and none came from the
       environment/session, ``None`` is returned (ambiguous — never "the
       highest display").
    """
    env = os.environ if env is None else env
    numbers = _x11_display_numbers()
    if not numbers:
        return None
    cur = (env.get("DISPLAY") or "").strip()
    cur_num = _display_number(cur)
    if cur and cur_num is not None and cur_num in numbers \
            and _probe_display(cur, env):
        return cur
    hinted = _session_hint_display()
    if hinted and _display_number(hinted) in numbers \
            and _probe_display(hinted, env):
        return hinted
    reachable = [f":{n}" for n in numbers if _probe_display(f":{n}", env)]
    if len(reachable) == 1:
        return reachable[0]
    return None   # none, or multiple reachable -> ambiguous, never guess


def ensure_session_display(env: Optional[dict] = None) -> Optional[str]:
    """Apply the resolved, REACHABLE session display to the environment.

    Returns the resolved display (or None headless/ambiguous). Only sets
    ``DISPLAY`` when the display is genuinely reachable; never clears an
    already-valid one and never sets a stale/unreachable value.
    """
    disp = resolve_session_display(env)
    if disp:
        os.environ["DISPLAY"] = disp
    return disp


def resolve_notification_session(env: Optional[dict] = None) -> dict:
    """Packet 8I: validate and resolve the real X11/Wayland notification
    session for ``notify-send`` at run time.

    Never trusts a hardcoded unit ``DISPLAY``. Returns a typed record:

    - ``ok`` + ``session_type="x11"``: a reachable X11 display was resolved;
    - ``ok`` + ``session_type="wayland"``: a Wayland session with a usable
      user D-Bus/XDG_RUNTIME_DIR is present (no DISPLAY needed);
    - ``unavailable``: headless / neither session is usable (typed failure,
      never a silent guess).
    """
    env = os.environ if env is None else env
    wayland = (env.get("WAYLAND_DISPLAY") or "").strip()
    xdg = (env.get("XDG_RUNTIME_DIR") or "").strip()
    dbus = (env.get("DBUS_SESSION_BUS_ADDRESS") or "").strip()
    disp = session_display_socket(env)
    bus_ok = bool(dbus) or (
        bool(xdg) and os.path.isdir(xdg)
        and os.path.exists(os.path.join(xdg, "bus")))
    if wayland and bus_ok:
        return {"state": "ok", "session_type": "wayland",
                "wayland_display": wayland, "xdg_runtime_dir": xdg,
                "display": disp or None, "wayland": True}
    if disp:
        return {"state": "ok", "session_type": "x11", "display": disp,
                "wayland": False, "xdg_runtime_dir": xdg or None}
    return {"state": "unavailable", "session_type": "headless",
            "display": None, "wayland": bool(wayland),
            "xdg_runtime_dir": xdg or None,
            "detail": "no reachable X11 display and no usable Wayland session"}