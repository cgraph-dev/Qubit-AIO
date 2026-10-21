#!/usr/bin/env python3
"""Bounded wait for a usable X11 ``$DISPLAY`` before launching the voice orb.

Reboot race: ``qubit-voice-orb.service`` can be started before the graphical
session has created its X socket, so ``python -m voicebridge.orb`` sees no
usable display, prints ``ORB_NO_DISPLAY`` and exits 0. This helper is run as an
``ExecStartPre``: it polls ``xdpyinfo`` (bounded) and returns as soon as the
display answers, so the orb starts a few hundred milliseconds later instead of
never.

Exit status is ALWAYS 0 by design. A genuinely headless host (no ``DISPLAY``
or no reachable X server) waits out the timeout and then exits 0, letting the
orb keep its existing graceful ``ORB_NO_DISPLAY`` exit instead of turning into
a ``Restart=on-failure`` storm.

Scope is X11 only, matching the orb itself: Qt/Tk are selected behind a
``DISPLAY`` check (``voicebridge/orb/__main__.py``) and there is no Wayland
backend. A Wayland session is usable through XWayland, which publishes a
``DISPLAY``; Wayland with no ``DISPLAY`` is headless for this unit.

No display index is hardcoded. ``DISPLAY``/``XAUTHORITY`` are inherited from
the unit environment; when ``XAUTHORITY`` is unset, the conventional
``~/.Xauthority`` is tried as a fallback (same rule as
``voicebridge.display``). The probe command and ``PATH`` are injectable for
hermetic tests via ``--probe`` and the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

DEFAULT_TIMEOUT_S = 45.0
DEFAULT_INTERVAL_S = 0.5
DEFAULT_PROBE = "xdpyinfo"
PROBE_TIMEOUT_S = 5.0


def probe_env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for the probe: inherit the unit env, add a checked-in
    ``~/.Xauthority`` fallback only when ``XAUTHORITY`` is not already set."""
    full = dict(os.environ if env is None else env)
    if not full.get("XAUTHORITY"):
        home = full.get("HOME") or str(Path.home())
        candidate = Path(home) / ".Xauthority"
        if candidate.exists():
            full["XAUTHORITY"] = str(candidate)
    return full


def display_probe(
    probe: str = DEFAULT_PROBE,
    env: Optional[Dict[str, str]] = None,
    runner: Callable[..., Any] = subprocess.run,
) -> bool:
    """True when ``probe`` (``xdpyinfo``) can open ``$DISPLAY``.

    A missing ``DISPLAY`` is never an error: it is simply not ready yet. A
    missing probe binary or a timed-out probe is also "not ready" rather than
    a crash, so the caller can keep polling or time out gracefully.
    """
    full = probe_env(env)
    if not (full.get("DISPLAY") or "").strip():
        return False
    exe = shutil.which(probe, path=full.get("PATH") or os.defpath)
    if not exe:
        return False
    try:
        proc = runner(
            [exe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=full,
            timeout=PROBE_TIMEOUT_S,
        )
    except Exception:
        return False
    return getattr(proc, "returncode", 1) == 0


def wait_for_display(
    timeout: float = DEFAULT_TIMEOUT_S,
    interval: float = DEFAULT_INTERVAL_S,
    probe: str = DEFAULT_PROBE,
    env: Optional[Dict[str, str]] = None,
    check: Optional[Callable[[], bool]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Poll until the display is usable or ``timeout`` elapses.

    Returns a small record. It never raises for headless/absent display: the
    caller (the unit) must not restart-storm.
    """
    timeout = max(0.0, float(timeout))
    interval = max(0.001, float(interval))
    probe_fn = check or (lambda: display_probe(probe, env))
    start = clock()
    deadline = start + timeout
    attempts = 0
    while True:
        attempts += 1
        if probe_fn():
            return {
                "ready": True,
                "attempts": attempts,
                "elapsed_s": round(clock() - start, 3),
            }
        remaining = deadline - clock()
        if remaining <= 0:
            return {
                "ready": False,
                "attempts": attempts,
                "elapsed_s": round(clock() - start, 3),
            }
        sleep(min(interval, remaining))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wait_for_display.py",
        description="Wait (bounded) for a usable X11 $DISPLAY; always exit 0.",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S,
        help=f"maximum seconds to wait (default {DEFAULT_TIMEOUT_S:g})")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"seconds between probes (default {DEFAULT_INTERVAL_S:g})")
    parser.add_argument(
        "--probe", default=DEFAULT_PROBE,
        help=f"command used to test the display (default {DEFAULT_PROBE})")
    args = parser.parse_args(argv)

    result = wait_for_display(args.timeout, args.interval, args.probe)
    result["event"] = "orb.display_wait"
    result["display"] = os.environ.get("DISPLAY", "")
    sys.stderr.write(json.dumps(result, sort_keys=True) + "\n")
    # Always 0: readiness is reported, not enforced. The orb itself decides
    # (and exits 0 with ORB_NO_DISPLAY) when there is still no display.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
