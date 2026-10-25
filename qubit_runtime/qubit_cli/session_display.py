"""Reachable-X11-session resolution for processes outside the graphical session.

A dashboard / gateway started as a systemd user service (or any process detached
from the seat) inherits no ``DISPLAY``. The browser clipboard is unreachable from
the server side, so the embedded TUI's own ``xclip`` read is the only path for an
image paste on ``/chat`` — and with no ``DISPLAY`` it fails with "Can't open
display: (null)", surfacing as "No image found in clipboard".

This module derives the seat's display by PROBING real sockets under
``/tmp/.X11-unix`` and validating reachability, never by guessing "the highest
display" in a multi-session environment: an ambiguous result degrades to ``None``
and leaves ``DISPLAY`` unset (headless / multi-seat hosts are untouched).
"""

from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path
from typing import Mapping

# The exact probe binary need not matter; `xdpyinfo` is a direct X connect and
# is present wherever a desktop session is. Fall back to `xclip` so a host with
# only xclip still validates.
_PROBE_CANDIDATES = (("xdpyinfo",), ("xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"))


def _x11_display_numbers() -> list[int]:
    """Sorted real X11 display numbers from /tmp/.X11-unix (e.g. X0, X1)."""
    out = []
    for sock in glob.glob("/tmp/.X11-unix/X[0-9]*"):
        name = os.path.basename(sock)
        if name.startswith("X") and name[1:].isdigit():
            out.append(int(name[1:]))
    return sorted(out)


def _display_number(display: str) -> int | None:
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


def _candidate_xauthority(env: "Mapping[str, str]") -> str | None:
    """First existing per-user X authority file, or None."""
    for path in (
        env.get("XAUTHORITY"),
        "/run/user/%d/gdm/Xauthority" % os.getuid() if hasattr(os, "getuid") else None,
        str(Path.home() / ".Xauthority"),
    ):
        if path and os.path.exists(path):
            return str(path)
    return None


def _probe_display(display: str, env: dict | None = None) -> bool:
    """Bounded reachability probe: can a real X client connect to this display?"""
    full = dict(os.environ if env is None else env)
    full["DISPLAY"] = display
    auth = _candidate_xauthority(full)
    if auth:
        full["XAUTHORITY"] = auth
    for argv in _PROBE_CANDIDATES:
        try:
            proc = subprocess.run(
                list(argv),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=full, timeout=3,
            )
        except Exception:
            continue
        if proc.returncode == 0:
            return True
    return False


def _session_hint_display() -> str | None:
    """DISPLAY exported by the user systemd instance, when present."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
        )
        for line in (proc.stdout or "").splitlines():
            if line.startswith("DISPLAY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def resolve_session_display(env: dict | None = None) -> str | None:
    """Return a REACHABLE X11 ``DISPLAY`` for this session, or None.

    Resolution order (never guesses a display):

    1. An explicitly-set ``DISPLAY`` is returned only when its X socket exists
       AND a real client can connect (validated, not assumed).
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
    if cur and cur_num is not None and cur_num in numbers and _probe_display(cur, env):
        return cur
    hinted = _session_hint_display()
    if hinted and _display_number(hinted) in numbers and _probe_display(hinted, env):
        return hinted
    reachable = [f":{n}" for n in numbers if _probe_display(f":{n}", env)]
    if len(reachable) == 1:
        return reachable[0]
    return None


def apply_session_display(env: dict) -> dict:
    """Populate ``DISPLAY``/``XAUTHORITY`` in *env* in place when resolvable.

    Never clears an already-set ``DISPLAY`` and never writes a stale value.
    Returns *env* for call-site chaining.
    """
    if not env.get("DISPLAY"):
        disp = resolve_session_display(env)
        if disp:
            env["DISPLAY"] = disp
    if env.get("DISPLAY") and not env.get("XAUTHORITY"):
        auth = _candidate_xauthority(env)
        if auth:
            env["XAUTHORITY"] = auth
    return env
