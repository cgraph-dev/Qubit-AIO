"""HUD gateway collector — Qubit-native port of ``backend/collectors/gateway.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: PORT (status) + refuse (actions)
------------------------------------------
The previous lane marked this GAP because "Qubit's gateway control is an
operator/action surface, not a read-only collector." The read half is wrong.
Evidence:

* upstream ``_do_collect_gateway`` reads ``gateway_state.json`` (state, pid,
  kind, active_agents, per-platform state) and probes pid liveness. Qubit has a
  better reader for the same file plus more: :func:`voicebridge.gateway_supervisor.
  status` returns ``{state, service_unit, running, pid, active_state, sub_state,
  started_at, port, port_in_use, exec_main_status, n_restarts, failed, health,
  daemon, control, daemon_live, daemon_pid, daemon_state}`` — the systemd unit,
  a bounded TCP health probe, the daemon's own ``gateway_state.json`` snapshot
  (``daemon`` including ``platforms``/``platforms_map``) and the live
  ``gateway.sock`` answer (``control``). That is a superset of upstream's read.
* Qubit also publishes the RPC surface: ``voicebridge.gateway.rpc.
  default_registry().names()`` (what ``GET /api/gateway/rpc`` serves).

NOT ported, by design: upstream's ``run_action`` (``gateway restart`` /
``hermes update`` — ``subprocess.Popen`` of the CLI, detached, writing a log).
Hard rule 3 makes the HUD a viewer, and hard rule 2 forbids shelling the binary;
the registry is built read-only here and no action endpoint is exposed. The
Qubit counterparts are reported by name so the panel can point at them.

Platform rows are read from the daemon's own snapshot only. When the daemon has
never written ``gateway_state.json`` the platforms list is empty with
``platforms_reason`` set — never an empty "all platforms connected" table.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

import socket
from typing import Any, Dict, List, Optional

#: The pinned read slice the gateway actually advertises for observability.
OBSERVABILITY_METHODS = (
    "health", "status", "logs.tail", "config.get", "config.schema",
    "exec.approvals.get", "sessions.list", "sessions.get", "sessions.search",
    "cron.list", "cron.status", "cron.runs", "memory.search", "usage.status",
    "tools.catalog", "hooks.status", "system.info", "gateway.identity.get",
)


def _status(cfg: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "detail": "", "status": {}}
    try:
        from .. import gateway_supervisor as GS

        st = GS.status(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"gateway supervisor unavailable ({type(exc).__name__}: {exc})"
        return out
    if not isinstance(st, dict):
        out["detail"] = f"gateway status returned {type(st).__name__}, not a dict"
        return out
    out.update(available=True, status=st, detail="voicebridge.gateway_supervisor")
    return out


def _platforms(st: Dict[str, Any]) -> Dict[str, Any]:
    """Per-platform rows from the daemon's own snapshot, never inferred."""
    daemon = st.get("daemon") if isinstance(st.get("daemon"), dict) else {}
    raw = daemon.get("platforms_map") if isinstance(daemon, dict) else None
    rows: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        for name in sorted(raw):
            info = raw[name] if isinstance(raw[name], dict) else {}
            rows.append({
                "name": str(name),
                "state": str(info.get("state") or "unknown"),
                "error_code": info.get("error_code"),
                "error_message": str(info.get("error_message") or "")[:200],
                "updated_at": info.get("updated_at"),
            })
    reason = ""
    if not rows:
        reason = ("The gateway daemon has published no platform snapshot "
                  "(gateway_state.json absent or empty), so no platform state "
                  "is shown.")
    return {
        "available": bool(rows),
        "detail": reason,
        "platforms": rows,
        "count": len(rows),
        "connected": sum(1 for r in rows if r["state"] == "connected"),
        "source": "gateway_state.json platforms (daemon-written)",
    }


def _rpc() -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "detail": "", "methods": [],
                           "observability": []}
    try:
        from ..gateway.rpc import default_registry

        names = list(default_registry().names())
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"RPC registry unavailable ({type(exc).__name__}: {exc})"
        return out
    obs = [m for m in OBSERVABILITY_METHODS if m in names]
    out.update(available=True,
               detail=f"{len(names)} method(s) on the single in-process registry",
               methods=names, observability=obs)
    return out


def _control(st: Dict[str, Any]) -> Dict[str, Any]:
    """The live control-socket answer, reported as the daemon sent it."""
    control = st.get("control")
    return {
        "available": isinstance(control, dict) and bool(control),
        "detail": ("" if isinstance(control, dict) and control else
                   "the gateway control socket did not answer this read; "
                   "systemd state above is unchanged by that"),
        "control": control if isinstance(control, dict) else None,
        "daemon_live": bool(st.get("daemon_live")),
        "daemon_pid": st.get("daemon_pid"),
        "daemon_state": st.get("daemon_state"),
    }


def collect_gateway(home: Optional[Any] = None, cfg: Any = None) -> Dict[str, Any]:
    """Live gateway status, platforms, control socket and the RPC surface."""
    status = _status(cfg)
    st = status.get("status") or {}
    platforms = _platforms(st) if st else {"available": False, "platforms": [],
                                           "count": 0, "connected": 0,
                                           "detail": status.get("detail", "")}
    rpc = _rpc()
    control = _control(st) if st else {"available": False, "control": None,
                                       "detail": status.get("detail", "")}
    return {
        "available": bool(status.get("available")),
        "detail": status.get("detail") or "",
        "state": st.get("state"),
        "running": bool(st.get("running")),
        "health": st.get("health"),
        "health_detail": str(st.get("health_detail") or ""),
        "service_unit": str(st.get("service_unit") or ""),
        "active_state": st.get("active_state"),
        "sub_state": st.get("sub_state"),
        "pid": st.get("pid"),
        "port": st.get("port"),
        "port_in_use": bool(st.get("port_in_use")),
        "n_restarts": st.get("n_restarts"),
        "failed": bool(st.get("failed")),
        "failure": st.get("failure"),
        "started_at": st.get("started_at"),
        "platforms": platforms,
        "control": control,
        "rpc": rpc,
        "actions_refused": {
            "gateway-restart": "the HUD is a viewer; use POST "
                               "/api/gateway/restart (confirm-gated dashboard "
                               "control) or `qubit gateway`",
            "hermes-update": "no upstream CLI is shelled from this tree",
        },
        "authority": "voicebridge.gateway_supervisor + voicebridge.gateway.rpc",
        "host": socket.gethostname(),
    }


__all__ = ["collect_gateway"]
