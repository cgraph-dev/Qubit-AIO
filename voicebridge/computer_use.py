"""Qubit desktop-control capability probes.

Qubit ``computer-use {install,status,doctor,permissions}`` reports
desktop-control readiness. Qubit performs
no desktop control outside supervised accessibility APIs, and never
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

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

NO_DRIVER_CODE = "COMPUTER_USE_NO_DRIVER"

SCREEN_SCHEMA = "qubit-screen/1"
SCREEN_CONFIRM_CODE = "SCREEN_CONFIRM_REQUIRED"
SCREEN_ESTOP_CODE = "SCREEN_ESTOP"
SCREEN_BAD_INPUT_CODE = "SCREEN_BAD_INPUT"
SCREEN_PANES: Tuple[str, ...] = (
    "terminal", "browser", "desktop", "files", "chat", "reports",
    "ops", "memory", "boards", "devices", "vision", "sessions",
)
SCREEN_LAYOUTS: Tuple[str, ...] = ("columns", "rows", "grid", "focus")
MAX_SCREEN_PANES = 6


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
            "screen": screen_panes(),
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


def desktop_session(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Typed graphical-session identification (read-only probe).

    Distinguishes X11, Wayland, both, and headless from environment +
    socket evidence — never spawns a process, never touches the session.
    The supervised Ghost desktop capabilities select their backend from
    this result; ``none``/``headless`` is a typed refusal reason, never a
    silent fallback to another display."""
    import glob as _glob

    source = dict(os.environ) if env is None else dict(env)
    session_type = (source.get("XDG_SESSION_TYPE", "") or "").strip().lower()
    wayland_display = (source.get("WAYLAND_DISPLAY", "") or "").strip()
    x_display = (source.get("DISPLAY", "") or "").strip()
    x11_sockets = sorted(_glob.glob("/tmp/.X11-unix/X[0-9]*"))
    wayland_sockets: List[str] = []
    runtime_dir = (source.get("XDG_RUNTIME_DIR", "") or "").strip()
    if runtime_dir:
        wayland_sockets = sorted(_glob.glob(runtime_dir + "/wayland-*"))
    x11 = bool(x_display or x11_sockets)
    wayland = bool(wayland_display or wayland_sockets
                   or session_type == "wayland")
    if x11 and wayland:
        kind = "both"
    elif wayland:
        kind = "wayland"
    elif x11:
        kind = "x11"
    else:
        kind = "none"
    return {"state": "OK", "schema": "qubit-desktop-session/1",
            "session": kind,
            "session_type": session_type or "unknown",
            "x_display": x_display,
            "wayland_display": wayland_display,
            "x11_sockets": len(x11_sockets),
            "wayland_sockets": len(wayland_sockets),
            "headless": kind == "none",
            "detail": ("no graphical session: desktop capabilities refuse"
                       " with NO_DESKTOP until you log into one"
                       if kind == "none" else
                       f"{kind} graphical session detected; supervised"
                       " desktop capabilities select this backend")}


# ---------------------------------------------------------------------------
# `screen` tool: bounded Control UI arrangement (OC-15)
# ---------------------------------------------------------------------------
def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True  # fail closed


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"screen.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _screen_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / "screen-layout.json"


def _load_screen(cfg: Any) -> Dict[str, Any]:
    try:
        raw = _screen_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_screen(cfg: Any, data: Dict[str, Any]) -> None:
    path = _screen_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _check_panes(panes: Any) -> List[str]:
    if isinstance(panes, str):
        items = [p.strip() for p in panes.split(",") if p.strip()]
    else:
        try:
            items = [str(p).strip() for p in panes]
        except TypeError:
            raise ValueError("panes must be a sequence of pane ids")
    if not items:
        raise ValueError("at least one pane is required")
    if len(items) > MAX_SCREEN_PANES:
        raise ValueError(f"at most {MAX_SCREEN_PANES} panes may be arranged")
    seen: List[str] = []
    for pane in items:
        if pane not in SCREEN_PANES:
            raise ValueError(
                f"unknown pane {pane!r}; allowed: {', '.join(SCREEN_PANES)}")
        if pane in seen:
            raise ValueError(f"duplicate pane {pane!r}")
        seen.append(pane)
    return seen


def screen_panes() -> Dict[str, Any]:
    """Bounded pane/layout catalog for CLI/voice/dashboard discovery."""
    return {"state": "OK", "schema": SCREEN_SCHEMA,
            "panes": list(SCREEN_PANES), "layouts": list(SCREEN_LAYOUTS),
            "max_panes": MAX_SCREEN_PANES,
            "detail": "arranges the Qubit Control UI only; never a window "
                      "manager or external display"}


def screen_status(cfg: Any) -> Dict[str, Any]:
    """Read the persisted Control UI arrangement (never touches a display)."""
    current = _load_screen(cfg)
    return {"state": "OK", "schema": SCREEN_SCHEMA,
            "arranged": bool(current),
            "layout": str(current.get("layout", "columns")),
            "panes": list(current.get("panes", [])),
            "updated_at": current.get("updated_at"),
            "rollback": "read-only; nothing to roll back"}


def arrange_screen(cfg: Any, panes: Any, *, layout: str = "columns",
                   requested_by: str = "desktop:user") -> Dict[str, Any]:
    """Validate + persist a bounded Control UI pane arrangement.

    The arrangement is a declarative layout descriptor for the Qubit Control
    UI; it never drives a window manager, moves a real window, or reads the
    screen. Unsupported panes/layouts and empty/oversized arrangements fail
    closed with a typed ``SCREEN_BAD_INPUT``.
    """
    clean_layout = str(layout or "columns").strip().lower()
    if clean_layout not in SCREEN_LAYOUTS:
        return {"state": "FAILED", "code": SCREEN_BAD_INPUT_CODE,
                "detail": f"layout must be one of {SCREEN_LAYOUTS}",
                "owner": "qubit-policy",
                "rollback": "Control UI untouched; nothing to roll back"}
    try:
        clean_panes = _check_panes(panes)
    except ValueError as exc:
        return {"state": "FAILED", "code": SCREEN_BAD_INPUT_CODE,
                "detail": str(exc), "owner": "qubit-policy",
                "rollback": "Control UI untouched; nothing to roll back"}
    data = {"schema": SCREEN_SCHEMA, "layout": clean_layout,
            "panes": clean_panes, "updated_at": time.time()}
    try:
        _save_screen(cfg, data)
    except OSError as exc:
        return {"state": "FAILED", "code": SCREEN_BAD_INPUT_CODE,
                "detail": f"arrangement could not be persisted ({exc})",
                "owner": "qubit-policy",
                "rollback": "previous arrangement preserved"}
    _audit("arrange", requested_by,
           {"layout": clean_layout, "panes": ",".join(clean_panes)})
    return {"state": "OK", "schema": SCREEN_SCHEMA,
            "layout": clean_layout, "panes": clean_panes,
            "updated_at": data["updated_at"],
            "authority": "Qubit Control UI arrangement only; no desktop or "
                         "display access"}


def screen(cfg: Any, panes: Any = None, *, layout: str = "columns",
           requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Confirmed ``screen`` entry point (arrange Control UI panes)."""
    if _estop(cfg):
        return {"state": "ESTOP", "code": SCREEN_ESTOP_CODE,
                "detail": "emergency stop latched; screen arrange refused",
                "owner": "qubit-policy",
                "rollback": "Control UI untouched; nothing to roll back"}
    if not confirmed:
        return {"state": "denied", "code": SCREEN_CONFIRM_CODE,
                "detail": "arranging the Control UI requires explicit "
                          "--confirm",
                "owner": "qubit-policy",
                "rollback": "Control UI untouched; nothing to roll back"}
    return arrange_screen(cfg, panes, layout=layout,
                          requested_by=requested_by)
