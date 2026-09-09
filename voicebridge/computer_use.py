"""Packet 8T-F — Hermes computer-use parity via capability probes.

Hermes ``computer-use {install,status,doctor,permissions}`` manages the
``cua-driver`` binary used by its ``computer_use`` toolset. Qubit performs
no desktop control outside Packet 8V accessibility APIs, and never
downloads unpinned binaries — so this module provides the Qubit-native
surface:

* ``status`` — display session, accessibility-bus reachability, and driver
  presence as typed states (pure environment/path probes; no process is
  spawned, no session is touched);
* ``doctor`` — the same checks as an actionable checklist with guidance;
* ``permissions`` — the control policy record (control disabled; owner,
  8V accessibility APIs, and fresh confirmation govern any future path);
* ``install`` — typed ``COMPUTER_USE_NO_DRIVER`` refusal (unpinned binary
  downloads violate the pinned-source policy).

All outputs are redacted; paths are shortened to ``~`` where applicable.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

NO_DRIVER_CODE = "COMPUTER_USE_NO_DRIVER"


def _short(path: str) -> str:
    home = str(Path("~").expanduser())
    return ("~" + path[len(home):]) if home != "~" and path.startswith(home) else path


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _session() -> Dict[str, str]:
    return {
        "session_type": os.environ.get("XDG_SESSION_TYPE", "").strip() or "unknown",
        "wayland_display": os.environ.get("WAYLAND_DISPLAY", "").strip(),
        "x_display": os.environ.get("DISPLAY", "").strip(),
    }


def _driver() -> Dict[str, Any]:
    found = shutil.which("cua-driver")
    return {"present": found is not None,
            "path": _short(found) if found else None}


def _atspi() -> Dict[str, Any]:
    bus = os.environ.get("ATSPI_BUS_ADDRESS", "").strip()
    if bus:
        return {"reachable": True, "via": "ATSPI_BUS_ADDRESS"}
    try:
        uid = os.getuid()
    except AttributeError:
        return {"reachable": False, "via": "none"}
    sock = Path(f"/run/user/{uid}/at-spi/bus")
    alt = Path(f"/run/user/{uid}/at-spi/bus_0")
    if sock.exists() or alt.exists():
        return {"reachable": True, "via": "session bus socket"}
    return {"reachable": False, "via": "none"}


def status() -> Dict[str, Any]:
    """Desktop-control capability state (read-only probes)."""
    session = _session()
    driver = _driver()
    bus = _atspi()
    display = bool(session["wayland_display"] or session["x_display"])
    return {"state": "OK", "schema": "qubit-computer-use/1",
            "session_type": session["session_type"],
            "display_present": display,
            "atspi_reachable": bus["reachable"],
            "atspi_via": bus["via"],
            "driver": driver,
            "control": "DISABLED",
            "control_policy": "desktop control is Packet 8V scope "
                              "(accessibility APIs, owner confirmation); "
                              "no control path is active"}


def doctor() -> Dict[str, Any]:
    """Actionable checklist for desktop-control readiness (no remediation)."""
    st = status()
    checks: List[Dict[str, str]] = []
    checks.append({"check": "display-session",
                   "state": "OK" if st["display_present"] else "MISSING",
                   "guidance": "log into a graphical session to enable "
                               "future 8V desktop work" if not st["display_present"]
                               else "graphical session detected"})
    checks.append({"check": "accessibility-bus",
                   "state": "OK" if st["atspi_reachable"] else "DEGRADED",
                   "guidance": "accessibility bus reachable"
                               if st["atspi_reachable"] else
                               "enable assistive-technology support in the "
                               "desktop settings for future 8V work"})
    checks.append({"check": "driver",
                   "state": "MISSING",
                   "guidance": "no cua-driver binary: Qubit does not "
                               "download unpinned driver binaries; desktop "
                               "control arrives via 8V accessibility APIs"})
    checks.append({"check": "control-policy",
                   "state": "OK",
                   "guidance": "control stays DISABLED until 8V with owner "
                               "confirmation; status/doctor remain available"})
    overall = "DEGRADED" if any(c["state"] != "OK" for c in checks) else "OK"
    return {"state": overall, "schema": "qubit-computer-use-doctor/1",
            "checks": checks}


def permissions() -> Dict[str, Any]:
    """Control policy record (read-only; who could enable what, later)."""
    return {"state": "OK", "schema": "qubit-computer-use-permissions/1",
            "control": "DISABLED",
            "owner": "packet-8V",
            "rules": ["no control path is active",
                      "8V accessibility APIs are the only future mechanism",
                      "owner confirmation is required before any control",
                      "unpinned driver binaries are never downloaded",
                      "emergency stop latches before any control starts"],
            "rollback": "nothing enabled; nothing to roll back"}


def refuse_install() -> Dict[str, Any]:
    """Typed refusal for driver download/install (pinned-source policy)."""
    return {"state": "REJECTED", "code": NO_DRIVER_CODE,
            "explanation": "Qubit Agent cannot do this: downloading the "
                           "unpinned cua-driver binary violates the "
                           "pinned-source policy; desktop control arrives "
                           "via Packet 8V accessibility APIs with owner "
                           "confirmation.",
            "owner": "packet-8V",
            "rollback": "nothing installed; nothing to roll back"}
