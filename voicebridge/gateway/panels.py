"""Qubit Control-UI panel registry (OC-71, transport-neutral).

Real backend for the dashboard's terminal / browser / desktop / side-chat
panels. Each panel is advertised with a live availability probe where one
exists; the terminal panel is honestly gated (Qubit starts no interactive
shell). A validated, persisted, confirmed panel layout is served to the
dashboard through the shared gateway RPC (``ui.panels`` / ``ui.panel_layout``)
— never a second listener, broker, or authority.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

SCHEMA = "qubit-control-panels/1"
STORE_NAME = "panel-layout.json"
PANELS = ("side_chat", "terminal", "browser", "desktop")
LAYOUTS = ("tabs", "split", "stack")
MAX_PANELS = 8

CONFIRM_CODE = "PANEL_CONFIRM_REQUIRED"
ESTOP_CODE = "PANEL_ESTOP"
BAD_INPUT_CODE = "PANEL_BAD_INPUT"
ROLLBACK_NOTE = "panel layout untouched; nothing to roll back"


def _estop(cfg: Any) -> bool:
    try:
        from ..plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True  # fail closed


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from ..logging_setup import log_event

        log_event(f"panels.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _load(cfg: Any) -> Dict[str, Any]:
    try:
        raw = _store_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cfg: Any, data: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _side_chat() -> Dict[str, Any]:
    return {"state": "OK", "available": True,
            "backend": "POST /api/qubit/chat (one shared chat path)",
            "detail": "side chat is served by the existing Qubit chat "
                      "endpoint; no second listener"}


def _terminal() -> Dict[str, Any]:
    return {"state": "GATED", "available": False,
            "code": "PTY_NO_SHELL",
            "backend": "/api/pty and /api/console are typed PTY_NO_SHELL",
            "alternative": "`qubit tui` or a supervised `opencode` run",
            "detail": "Qubit starts no interactive shell in the dashboard; "
                      "terminal-shaped work goes through the supervised "
                      "opencode path or the operator TUI"}


def _device_family(cfg: Any, name: str) -> Dict[str, Any]:
    from .. import runtime_inventory as RINV

    inv = RINV.device_inventory(cfg)
    fam = inv.get(name) or {}
    state = str(fam.get("state") or "UNAVAILABLE")
    return {"state": state, "available": state == "OK",
            "backend": f"runtime_inventory device_inventory.{name} "
                       f"(read-only probe)",
            "detail": fam}


def _browser(cfg: Any) -> Dict[str, Any]:
    try:
        return _device_family(cfg, "browser")
    except Exception as exc:
        return {"state": "UNAVAILABLE", "available": False,
                "backend": "browser (loopback CDP)",
                "detail": str(exc)[:160]}


def _desktop(cfg: Any) -> Dict[str, Any]:
    try:
        return _device_family(cfg, "desktop")
    except Exception as exc:
        return {"state": "UNAVAILABLE", "available": False,
                "backend": "computer_use.status (read-only probe)",
                "detail": str(exc)[:160]}


def control_panels(cfg: Any) -> Dict[str, Any]:
    """The real Control-UI panel registry with live availability."""
    return {
        "schema": SCHEMA,
        "state": "OK",
        "panels": {
            "side_chat": _side_chat(),
            "terminal": _terminal(),
            "browser": _browser(cfg),
            "desktop": _desktop(cfg),
        },
        "layouts": list(LAYOUTS),
        "max_panels": MAX_PANELS,
        "authority": "panel availability is a read-only probe; the terminal "
                     "panel starts no shell and availability is never "
                     "authorization",
    }


def _check_panels(panels: Any) -> List[str]:
    if isinstance(panels, str):
        items = [p.strip() for p in panels.split(",") if p.strip()]
    else:
        try:
            items = [str(p).strip() for p in panels]
        except TypeError:
            raise ValueError("panels must be a sequence of panel ids")
    if not items:
        raise ValueError("at least one panel is required")
    if len(items) > MAX_PANELS:
        raise ValueError(f"at most {MAX_PANELS} panels may be arranged")
    seen: List[str] = []
    for panel in items:
        if panel not in PANELS:
            raise ValueError(
                f"unknown panel {panel!r}; allowed: {', '.join(PANELS)}")
        if panel in seen:
            raise ValueError(f"duplicate panel {panel!r}")
        seen.append(panel)
    return seen


def panel_layout(cfg: Any) -> Dict[str, Any]:
    """Read the persisted Control-UI panel layout (never starts a panel)."""
    current = _load(cfg)
    return {"state": "OK", "schema": SCHEMA,
            "arranged": bool(current),
            "layout": str(current.get("layout", "tabs")),
            "panels": list(current.get("panels", [])),
            "updated_at": current.get("updated_at"),
            "rollback": "read-only; nothing to roll back"}


def arrange_panels(cfg: Any, panels: Any, *, layout: str = "tabs",
                   requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Validate + persist a bounded Control-UI panel arrangement."""
    clean_layout = str(layout or "tabs").strip().lower()
    if clean_layout not in LAYOUTS:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": f"layout must be one of {LAYOUTS}",
                "owner": "qubit-policy", "rollback": ROLLBACK_NOTE}
    try:
        clean_panels = _check_panels(panels)
    except ValueError as exc:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": str(exc), "owner": "qubit-policy",
                "rollback": ROLLBACK_NOTE}
    data = {"schema": SCHEMA, "layout": clean_layout,
            "panels": clean_panels, "updated_at": time.time()}
    try:
        _save(cfg, data)
    except OSError as exc:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": f"panel layout could not be persisted ({exc})",
                "owner": "qubit-policy",
                "rollback": "previous panel layout preserved"}
    _audit("arrange", requested_by,
           {"layout": clean_layout, "panels": ",".join(clean_panels)})
    return {"state": "OK", "schema": SCHEMA, "layout": clean_layout,
            "panels": clean_panels, "updated_at": data["updated_at"],
            "authority": "Control-UI layout descriptor only; no panel is "
                         "started and no display is accessed"}


def arrange(cfg: Any, panels: Any, *, layout: str = "tabs",
            requested_by: str = "dashboard:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Confirmed entry point (e-stop aware)."""
    if _estop(cfg):
        return {"state": "ESTOP", "code": ESTOP_CODE,
                "detail": "emergency stop latched; panel layout refused",
                "owner": "qubit-policy", "rollback": ROLLBACK_NOTE}
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "detail": "arranging the Control-UI panels requires explicit "
                          "confirmation",
                "owner": "qubit-policy", "rollback": ROLLBACK_NOTE}
    return arrange_panels(cfg, panels, layout=layout,
                          requested_by=requested_by)
