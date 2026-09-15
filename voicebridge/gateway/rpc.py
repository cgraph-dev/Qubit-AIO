"""Qubit gateway RPC method registry (transport-neutral, in-process).

OpenClaw's v2026.9.4 gateway added server methods (``environments.prepare``,
task history/suggestions, update report, workspace files, plugin mutations,
migrations). Qubit opens no public listener and starts no second authority,
so those methods are implemented here as real in-process handlers that
delegate to the SAME backend modules the CLI, dashboard, and voice surfaces
use. They are dispatched from the transport-neutral gateway frame path
(``FrameReader``), never from a duplicate broker, scheduler, or server.

Every handler is read-mostly and broker-gated: mutations route through the
existing plugin-runtime confirmation/e-stop gates, file access stays inside
the approved roots, and anything unobservable is typed UNAVAILABLE.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .gateway_ws import GatewayError

METHOD_NOT_FOUND = "GATEWAY_METHOD_NOT_FOUND"
METHOD_BAD_PARAMS = "GATEWAY_BAD_PARAMS"
SESSION_REPOSITORY_UNAVAILABLE = "SESSION_REPOSITORY_UNAVAILABLE"

Handler = Callable[[Any, Dict[str, Any]], Dict[str, Any]]


class MethodRegistry:
    """Named handlers keyed by gateway method name (no sockets, no threads)."""

    def __init__(self) -> None:
        self._methods: Dict[str, Handler] = {}

    def register(self, name: str, fn: Handler) -> Handler:
        clean = str(name or "").strip()
        if not clean:
            raise GatewayError(METHOD_BAD_PARAMS, "method name required")
        self._methods[clean] = fn
        return fn

    def names(self) -> list:
        return sorted(self._methods)

    def handle(self, cfg: Any, method: str,
               params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        name = str(method or "").strip()
        fn = self._methods.get(name)
        if fn is None:
            raise GatewayError(METHOD_NOT_FOUND,
                               f"unknown gateway method {name!r}")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise GatewayError(METHOD_BAD_PARAMS, "params must be an object")
        try:
            return fn(cfg, params)
        except GatewayError:
            raise
        except Exception as exc:  # backend typed errors become RPC errors
            code = getattr(exc, "code", None)
            detail = getattr(exc, "detail", None) or str(exc)
            if code:
                raise GatewayError(str(code), str(detail)[:300]) from exc
            raise GatewayError("GATEWAY_METHOD_FAILED",
                               str(exc)[:300]) from exc


# --------------------------------------------------------------- handlers
def _environments_prepare(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import node_host as NH
    from .. import runtime_images as RI

    name = str(params.get("name", "") or "").strip().lower()
    out: Dict[str, Any] = {"registry": RI.images()["images"],
                           "status": RI.status(cfg)["runtimes"],
                           "nodes": NH.status(cfg),
                           "mode": "plan"}
    if name:
        entry = next((r for r in RI.REGISTRY if r["name"] == name), None)
        if entry is None:
            return {"state": "UNKNOWN", "name": name,
                    "detail": "no pinned runtime with that name"}
        row = next(r for r in out["status"] if r["name"] == name)
        out = {"state": "OK", "name": name, "pin": entry["pin"],
               "runtime": row, "mode": "plan",
               "detail": "preparation plan only; nothing was installed"}
    else:
        out["state"] = "OK"
    return out


def _task_history(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import task_ledger as TL

    out = TL.list_tasks(cfg, source=params.get("source"),
                        state=params.get("state"),
                        limit=params.get("limit", 50))
    return out


def _task_suggestions(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import task_ledger as TL

    listing = TL.list_tasks(cfg, limit=int(params.get("limit", 100) or 100))
    rows = listing.get("tasks", []) if isinstance(listing, dict) else []
    open_states = {"pending", "queued", "running", "in_progress", "todo"}
    sugg = [r for r in rows if str(r.get("state", "")).lower() in open_states]
    return {"state": "OK", "suggestions": sugg[:25],
            "considered": len(rows),
            "authority": "derived read-only from the shared task ledger; "
                         "nothing is dispatched"}


def _update_report(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import runtime_images as RI

    report = RI.update_report(cfg)
    report["images"] = [r["name"] for r in RI.REGISTRY]
    return report


def _workspace_files(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import files as F

    action = str(params.get("action", "inspect") or "inspect").strip().lower()
    path = str(params.get("path", "") or "")
    if action == "list":
        return F.list_dir(cfg, path, depth=int(params.get("depth", 1) or 1))
    if action == "read":
        return F.read_file(cfg, path,
                           max_chars=int(params.get("max_chars", 4000) or 4000))
    if action == "inspect":
        return F.inspect(cfg, path)
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown workspace.files action {action!r}")


def _plugin_mutations(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import plugin_runtime as PR

    action = str(params.get("action", "status") or "status").strip().lower()
    confirmed = bool(params.get("confirmed", False))
    if action == "status":
        return PR.status(cfg)
    if action == "list":
        return PR.list_plugins(cfg)
    if action == "doctor":
        return PR.doctor(cfg)
    if action == "register":
        return PR.register(cfg, manifest=params.get("manifest"),
                           confirmed=confirmed,
                           requested_by=str(params.get("requested_by", "gateway")))
    if action == "unregister":
        return PR.unregister(cfg, str(params.get("plugin_id", "")),
                             confirmed=confirmed,
                             requested_by=str(params.get("requested_by", "gateway")))
    if action == "invoke":
        return PR.invoke(cfg, str(params.get("plugin_id", "")),
                         args=params.get("args") or {},
                         confirmed=confirmed,
                         requested_by=str(params.get("requested_by", "gateway")))
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown plugin.mutations action {action!r}")


def _ui_panels(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import panels as PAN

    return PAN.control_panels(cfg)


def _ui_panel_layout(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import panels as PAN

    action = str(params.get("action", "status") or "status").strip().lower()
    if action == "status":
        return PAN.panel_layout(cfg)
    if action == "arrange":
        return PAN.arrange(
            cfg, params.get("panels"), layout=str(params.get("layout",
                                                             "tabs")),
            requested_by=str(params.get("requested_by", "gateway")),
            confirmed=bool(params.get("confirmed", False)))
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown ui.panel_layout action {action!r}")


def _sessions_sharing(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit equivalent of the upstream session-sharing gateway methods.

    The pinned v2026.9.4 gateway added ``session.publicShare.set``,
    ``session.visibility.set`` and ``session.members.*`` over a cloud session
    repository. Qubit runs no cloud repository and no second policy authority,
    so this single method exposes exactly the safe local capability: publish a
    redacted, revocable, expiring read-only share of a receipt-store session
    (``voicebridge/session_share.py``), list shares, revoke, and resolve. Every
    mutation stays confirmation/e-stop gated in the shared backend; unknown
    actions are typed bad-params, never a fabricated member/visibility result.
    """
    from .. import session_share as SS

    action = str(params.get("action", "status") or "status").strip().lower()
    if action == "status":
        return SS.status(cfg)
    if action == "list":
        return SS.list_shares(cfg)
    if action == "publish":
        return SS.publish(
            cfg, str(params.get("session_id", "") or ""),
            ttl_s=params.get("ttl_s", SS.DEFAULT_TTL_S),
            confirmed=bool(params.get("confirmed", False)),
            requested_by=str(params.get("requested_by", "gateway")))
    if action == "revoke":
        return SS.revoke(cfg, str(params.get("share_id", "") or ""),
                         confirmed=bool(params.get("confirmed", False)))
    if action == "resolve":
        return SS.resolve(cfg, str(params.get("share_id", "") or ""),
                          str(params.get("token", "") or ""))
    if action == "purge":
        return SS.purge_expired(cfg)
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown sessions.sharing action {action!r}")


def _session_repository_artifacts(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only session artifact view (Qubit-safe local equivalent).

    Upstream lists artifacts from a cloud session repository
    (``session-repository-artifacts.ts``). Qubit runs no cloud repository, so
    the *only* session artifacts it has are the local opt-in public shares.
    This returns those read-only and reports the cloud path as a typed
    ``SESSION_REPOSITORY_UNAVAILABLE`` — it never fabricates a remote listing.
    """
    from .. import session_share as SS

    action = str(params.get("action", "list") or "list").strip().lower()
    if action != "list":
        raise GatewayError(METHOD_BAD_PARAMS,
                           f"unknown session.repository_artifacts action "
                           f"{action!r}")
    listing = SS.list_shares(cfg)
    return {
        "state": "OK",
        "mode": "local-shares",
        "artifacts": listing.get("shares", []),
        "count": listing.get("count", 0),
        "cloud": {"state": "UNAVAILABLE", "code": SESSION_REPOSITORY_UNAVAILABLE,
                  "detail": "no cloud session repository is run; local "
                            "read-only session shares are the safe equivalent"},
        "authority": "read-only; nothing is fetched or fabricated",
    }


def _migrations(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import lifecycle as LC
    from .. import product as PR

    contract = PR.upgrade_contract()
    return {"state": "OK", "contract": contract,
            "provenance": LC.provenance_view(),
            "authority": "read-only migration contract; run the migration "
                         "through the CLI/dashboard with explicit confirmation"}


def default_registry() -> MethodRegistry:
    """The canonical Qubit gateway RPC surface (single backend path)."""
    registry = MethodRegistry()
    registry.register("environments.prepare", _environments_prepare)
    registry.register("task.history", _task_history)
    registry.register("task.suggestions", _task_suggestions)
    registry.register("update.report", _update_report)
    registry.register("workspace.files", _workspace_files)
    registry.register("plugin.mutations", _plugin_mutations)
    registry.register("ui.panels", _ui_panels)
    registry.register("ui.panel_layout", _ui_panel_layout)
    registry.register("sessions.sharing", _sessions_sharing)
    registry.register("session.repository_artifacts",
                      _session_repository_artifacts)
    registry.register("migrations", _migrations)
    return registry


def handle_request(registry: MethodRegistry, cfg: Any,
                   msg: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a ``req`` frame into a ``res`` frame (never raises for RPC)."""
    rid = msg.get("id")
    if not rid:
        raise GatewayError("FRAME_BAD_SHAPE", "gateway request has no id")
    try:
        payload = registry.handle(cfg, str(msg.get("method", "")),
                                  msg.get("params") or {})
        return {"type": "res", "id": rid, "ok": True, "payload": payload}
    except GatewayError as exc:
        return {"type": "res", "id": rid, "ok": False,
                "error": {"code": exc.code, "message": exc.message,
                          "details": exc.details}}
