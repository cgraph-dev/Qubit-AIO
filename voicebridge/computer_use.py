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


# ---------------------------------------------------------------------------
# OC-13: real desktop control through a typed, injected accessibility actuator
# ---------------------------------------------------------------------------
ACT_SCHEMA = "qubit-computer-act/1"
NO_DESKTOP_CODE = "COMPUTER_USE_NO_DESKTOP"
ACT_CONFIRM_CODE = "COMPUTER_USE_CONFIRM_REQUIRED"
ACT_BAD_INPUT_CODE = "COMPUTER_USE_BAD_ACTION"
ACT_ESTOP_CODE = "COMPUTER_USE_ESTOP"
ACT_FAILED_CODE = "COMPUTER_USE_ACTUATOR_FAILED"
MAX_ACT_TEXT = 2000
MAX_ACT_COORD = 32767
CONTROL_ACTIONS: Tuple[str, ...] = (
    "click", "double_click", "move", "type", "key", "scroll",
    "focus", "window_list", "window_activate",
)


def control_actions() -> Dict[str, Any]:
    """Bounded action catalog for desktop control (OC-13)."""
    return {"state": "OK", "schema": ACT_SCHEMA,
            "actions": list(CONTROL_ACTIONS),
            "detail": "semantic, accessibility-API actions only; no shell, "
                      "no coordinate injection, no unpinned driver"}


def _resolve_actuator(executor: Any) -> Optional[Any]:
    """Return a callable actuator boundary, or None when unavailable.

    ``executor`` is the *external hardware boundary*: a callable
    ``(action, args) -> mapping`` supplied by an injected accessibility
    backend. With no executor the call is an honest typed driver-missing
    state — Qubit never fabricates a desktop action.
    """
    if executor is None:
        return None
    if callable(executor):
        return executor
    if isinstance(executor, dict):
        def _dispatch(action: str, args: Dict[str, Any]) -> Any:
            fn = executor.get(action)
            if not callable(fn):
                return {"ok": False, "detail": f"no handler for {action}"}
            return fn(args)
        return _dispatch
    return None


def control_status(executor: Any = None) -> Dict[str, Any]:
    """Report whether a real desktop actuator is available (read-only)."""
    session = desktop_session()
    resolved = _resolve_actuator(executor)
    driver = _driver()
    if session["headless"]:
        state, code = "UNAVAILABLE", NO_DESKTOP_CODE
        detail = session["detail"]
    elif resolved is None and not driver["present"]:
        state, code = "UNAVAILABLE", NO_DRIVER_CODE
        detail = ("no accessibility actuator resolved and no driver present; "
                  "inject an AT-SPI executor or run with a graphical session")
    else:
        state, code = "READY", ""
        detail = ("accessibility actuator available; actions still require "
                  "confirmation and pass the e-stop")
    return {"state": state, "code": code, "schema": ACT_SCHEMA,
            "session": session["session"], "atspi_reachable":
            bool(_atspi()["reachable"]),
            "actuator": "injected" if resolved is not None else (
                "driver" if driver["present"] else "none"),
            "detail": detail, "actions": list(CONTROL_ACTIONS)}


def _check_act_args(action: str, args: Any) -> Dict[str, Any]:
    clean = dict(args) if isinstance(args, dict) else {}
    if action in ("click", "double_click", "move"):
        for key in ("x", "y"):
            if key in clean:
                try:
                    value = int(clean[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key} must be an integer")
                if not (-MAX_ACT_COORD <= value <= MAX_ACT_COORD):
                    raise ValueError(f"{key} out of range")
                clean[key] = value
    elif action == "type":
        text = clean.get("text", "")
        if not isinstance(text, str) or not text:
            raise ValueError("type requires non-empty text")
        if len(text) > MAX_ACT_TEXT:
            raise ValueError(f"text exceeds {MAX_ACT_TEXT} chars")
    elif action == "key":
        name = clean.get("key", "")
        if not isinstance(name, str) or not name or len(name) > 32:
            raise ValueError("key requires a bounded key name")
    elif action == "scroll":
        direction = str(clean.get("direction", "down"))
        if direction not in ("up", "down", "left", "right"):
            raise ValueError("scroll direction must be up/down/left/right")
        clean["direction"] = direction
    return clean


def act(cfg: Any, action: str, *, args: Any = None, executor: Any = None,
        confirmed: bool = False, requested_by: str = "cli:user",
        source: str = "user") -> Dict[str, Any]:
    """Perform one bounded desktop action through an injected actuator (OC-13).

    The actuator is the only external boundary. With a headless session the
    call refuses ``COMPUTER_USE_NO_DESKTOP``; with no actuator it refuses
    ``COMPUTER_USE_NO_DRIVER``; unconfirmed calls are denied before any
    actuator is invoked. Nothing here shells out or downloads a driver.
    """
    if _estop(cfg):
        return {"state": "ESTOP", "code": ACT_ESTOP_CODE,
                "detail": "emergency stop latched; desktop action refused",
                "owner": "qubit-policy",
                "rollback": "no action sent; nothing to roll back"}
    clean_action = str(action or "").strip().lower()
    if clean_action not in CONTROL_ACTIONS:
        return {"state": "FAILED", "code": ACT_BAD_INPUT_CODE,
                "detail": f"unknown action {action!r}; allowed: "
                          f"{', '.join(CONTROL_ACTIONS)}",
                "owner": "qubit-policy",
                "rollback": "no action sent; nothing to roll back"}
    session = desktop_session()
    if session["headless"]:
        return {"state": "UNAVAILABLE", "code": NO_DESKTOP_CODE,
                "detail": session["detail"], "owner": "qubit-policy",
                "rollback": "no action sent; nothing to roll back"}
    if not confirmed:
        return {"state": "denied", "code": ACT_CONFIRM_CODE,
                "detail": "desktop actions require explicit --confirm",
                "owner": "qubit-policy",
                "rollback": "no action sent; nothing to roll back"}
    actuator = _resolve_actuator(executor)
    if actuator is None and not _driver()["present"]:
        return refuse_install()
    try:
        clean_args = _check_act_args(clean_action, args)
    except ValueError as exc:
        return {"state": "FAILED", "code": ACT_BAD_INPUT_CODE,
                "detail": str(exc), "owner": "qubit-policy",
                "rollback": "no action sent; nothing to roll back"}
    if actuator is None:
        return refuse_install()
    try:
        result = actuator(clean_action, clean_args)
    except Exception as exc:
        return {"state": "FAILED", "code": ACT_FAILED_CODE,
                "detail": _redact(str(exc))[:200], "owner": "qubit-policy",
                "rollback": "action may be partial; retry or reset manually"}
    if isinstance(result, dict):
        detail = _redact(str(result.get("detail", "")))[:200]
        ok = bool(result.get("ok", result.get("state") == "OK"))
        extra = {k: v for k, v in result.items()
                 if k not in ("ok", "detail", "state") and
                 isinstance(v, (str, int, float, bool))}
    else:
        ok, detail, extra = bool(result), "", {}
    _audit("act", requested_by, {"action": clean_action, "ok": str(ok)})
    return {"state": "OK" if ok else "FAILED",
            "code": "" if ok else ACT_FAILED_CODE,
            "schema": ACT_SCHEMA, "action": clean_action,
            "detail": detail, "result": extra,
            "authority": "injected accessibility actuator; no shell, no "
                         "coordinates forced, no driver download"}


def ghost_executors(transport: Any) -> Dict[str, Any]:
    """Build real GhostRunner executors over an injected accessibility bus (OC-14).

    ``transport`` is the external boundary: a callable
    ``(action_id, env) -> str``. The returned mapping uses the exact Ghost
    action ids so the existing :class:`voicebridge.ghost.GhostRunner` (its
    gate, grants, audit, and e-stop) drives a real actuator instead of a
    fixture. No executor is invented for an unknown action id.
    """
    namespace = "ghost"

    def _executor(action_id: str) -> Any:
        def _run(env: Dict[str, Any]) -> str:
            return transport(f"{namespace}.{action_id}", env)
        return _run

    return {
        f"{namespace}.desktop.discover": _executor("desktop.discover"),
        f"{namespace}.desktop.launch": _executor("desktop.launch"),
        f"{namespace}.window.anchor": _executor("window.anchor"),
        f"{namespace}.desktop.screenshot": _executor("desktop.screenshot"),
        f"{namespace}.input.click": _executor("input.click"),
        f"{namespace}.input.type": _executor("input.type"),
        f"{namespace}.input.scroll": _executor("input.scroll"),
        f"{namespace}.input.key": _executor("input.key"),
    }


# ---------------------------------------------------------------------------
# OC-16 / OC-51: screen snapshot work journal
# ---------------------------------------------------------------------------
SNAPSHOT_SCHEMA = "qubit-screen-snapshot/1"
SNAPSHOT_CONFIRM_CODE = "COMPUTER_USE_SNAPSHOT_CONFIRM_REQUIRED"
SNAPSHOT_ESTOP_CODE = "COMPUTER_USE_SNAPSHOT_ESTOP"


def _journal(cfg: Any):
    from .evaluation.logbook import Logbook

    return Logbook(Path(cfg.state_dir()) / "work-journal.jsonl")


def snapshot(cfg: Any, *, grabber: Any = None, vision: Any = None,
             note: str = "", confirmed: bool = False,
             requested_by: str = "cli:user") -> Dict[str, Any]:
    """Capture one screen snapshot into the Logbook work journal (OC-16).

    ``grabber`` is the external display boundary; without it the call is the
    typed ``LOGBOOK_CAPTURE_UNAVAILABLE`` refusal. With it, the image is
    size-capped, persisted 0600, and journaled append-only.
    """
    if _estop(cfg):
        return {"state": "ESTOP", "code": SNAPSHOT_ESTOP_CODE,
                "detail": "emergency stop latched; snapshot refused",
                "owner": "qubit-policy",
                "rollback": "nothing captured; no screen access attempted"}
    if not confirmed:
        return {"state": "denied", "code": SNAPSHOT_CONFIRM_CODE,
                "detail": "screen snapshot requires explicit --confirm",
                "owner": "qubit-policy",
                "rollback": "nothing captured; no screen access attempted"}
    from .evaluation.logbook import capture_to

    out = capture_to(_journal(cfg), grabber=grabber, vision=vision,
                     evidence_dir=Path(cfg.state_dir()) / "screen-journal",
                     source="screen-journal", actor=requested_by,
                     confirmed=True)
    if isinstance(out, dict) and note:
        out["note"] = _redact(str(note))[:200]
    _audit("snapshot", requested_by, {"state": str(out.get("state", ""))})
    if out.get("state") == "OK":
        out["schema"] = SNAPSHOT_SCHEMA
    return out


def snapshot_journal(cfg: Any, *, limit: int = 50) -> Dict[str, Any]:
    """Read the screen-snapshot work journal (read-only)."""
    try:
        items = _journal(cfg).entries(limit=max(1, min(int(limit), 500)))
    except Exception as exc:
        return {"state": "DEGRADED", "code": "COMPUTER_USE_JOURNAL_ERROR",
                "detail": _redact(str(exc))[:200], "entries": []}
    snaps = [it for it in items if it.get("kind") == "screen.snapshot"]
    return {"state": "OK", "schema": SNAPSHOT_SCHEMA, "entries": snaps,
            "count": len(snaps),
            "authority": "read-only journal; images are untrusted evidence"}


# ---------------------------------------------------------------------------
# OC-17: CUA browser family (get_browser_state, etc.)
# ---------------------------------------------------------------------------
CUA_SCHEMA = "qubit-cua-browser/1"


def cua_browser_state(cfg: Any, target: str = "") -> Dict[str, Any]:
    """Normalized CUA browser-family state over the loopback CDP stack (OC-17).

    Composes the existing real browser operations (tabs + read) into the
    upstream CUA shape (url/title/tabs/focused text). With no CDP endpoint the
    result is the typed ``BROWSER_UNAVAILABLE`` state, never a fabricated
    state.
    """
    from . import browser_tabs

    tabs_out = browser_tabs.tabs(cfg)
    if tabs_out.get("state") != "OK":
        return {"state": tabs_out.get("state", "UNAVAILABLE"),
                "code": tabs_out.get("code", browser_tabs.UNAVAILABLE_CODE),
                "schema": CUA_SCHEMA, "detail": tabs_out.get("detail", ""),
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    try:
        page = browser_tabs.read(cfg, target)
    except browser_tabs.BrowserTabsError as exc:
        return {"state": "UNAVAILABLE", "code": exc.code,
                "schema": CUA_SCHEMA, "detail": exc.detail[:300],
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    if page.get("state") != "OK":
        return {"state": page.get("state", "UNAVAILABLE"),
                "code": page.get("code", ""), "schema": CUA_SCHEMA,
                "detail": page.get("detail", ""), "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    return {"state": "OK", "schema": CUA_SCHEMA,
            "url": page.get("url", ""), "title": page.get("title", ""),
            "text": page.get("text", ""),
            "current_target": tabs_out.get("current_target", ""),
            "tabs": tabs_out.get("tabs", []),
            "trust": "untrusted-page-text",
            "authority": "loopback CDP read-only; personal profiles untouched"}
