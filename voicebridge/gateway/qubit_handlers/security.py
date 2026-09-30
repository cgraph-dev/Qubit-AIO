"""OpenClaw contractual-pin approvals / devices / attach security family.

Every handler delegates to the one canonical Qubit authority:

* :class:`voicebridge.approvals.ApprovalJournal` — the shared, durable
  human-confirmation journal the CLI ``qubit approvals``, voice yes/no, the
  broker, and the dashboard all settle through (``approval.history``);
* :mod:`voicebridge.device` — the canonical device-scoped token authority
  (pending -> operator approval -> active), used for ``device.pair.rename`` and
  the scope-upgrade request/wait pair;
* :mod:`voicebridge.node_host` — the canonical node registry/capability policy
  for ``exec.approvals.node.get``.

Mechanism differences carry their own typed codes:

* ``exec.approvals.set`` — upstream edits a gateway-writable exec-approvals
  allowlist file. Qubit has no such file: command approval is the shared
  confirmation journal plus the broker capability policy, so this surfaces the
  real current policy tagged with its own code.
* ``exec.approvals.node.set`` / ``attach.grant`` / ``attach.revoke`` — need a
  node-side exec-policy owner or an MCP loopback attach runtime Qubit does not
  run; typed OPERATOR_ONLY with the real local counterpart named.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

SEC_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes (real local counterpart, upstream mechanism
#: differs). The manifest asserts each is emitted by its registered handler.
EXEC_APPROVALS_LOCAL_JOURNAL = "EXEC_APPROVALS_LOCAL_JOURNAL"
EXEC_APPROVALS_NODE_LOCAL_POLICY = "EXEC_APPROVALS_NODE_LOCAL_POLICY"
#: OPERATOR_ONLY typed codes (missing live prerequisite / service).
EXEC_APPROVALS_NODE_OPERATOR_REQUIRED = "EXEC_APPROVALS_NODE_OPERATOR_REQUIRED"
ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED = "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED"


def _sec_param(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _sec_sid(params: Dict[str, Any], default: str = "main") -> str:
    sid = _sec_param(params, "sessionKey", "session_id", "sessionId", "key",
                     "id", "session")
    return (sid or default)[:80]


def _sec_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return default


def _sec_confirmed(params: Dict[str, Any]) -> bool:
    return _sec_bool(params.get("confirm", params.get("confirmed", False)))


def _sec_estop(cfg: Any) -> bool:
    from ... import broker as BR

    return bool(BR.estop_engaged(cfg))


def _sec_estop_refusal(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
            "detail": f"emergency stop latched; {operation} refused"}


# ------------------------------------------------------------- approvals
def _sec_approval_history(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from pathlib import Path

    from ...approvals import ApprovalJournal

    try:
        from ...redaction import redact_obj
    except Exception:  # pragma: no cover - redaction always present
        redact_obj = lambda value: value  # noqa: E731

    kind = _sec_param(params, "kind")
    try:
        limit = max(1, min(int(params.get("limit", 50) or 50), 200))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = max(0, int(params.get("cursor", 0) or 0))
    except (TypeError, ValueError):
        raise GatewayError(SEC_BAD_PARAMS, "cursor must be an integer")
    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                              default_ttl_s=120.0)
    page = journal.history(kind=kind, limit=limit, offset=offset)
    items = [{"id": a.id, "capability": str(a.capability or ""),
              "summary": str(a.summary or "")[:300], "status": a.state,
              "decision": a.decision, "createdAt": a.created_at,
              "settledAt": a.settled_at, "expiresAt": a.expires_at,
              "meta": redact_obj(dict(a.meta or {}))}
             for a in page["items"]]
    out: Dict[str, Any] = {"state": "OK", "items": items,
                           "count": len(items), "total": page["total"],
                           "authority": "voicebridge.approvals.ApprovalJournal"}
    if page.get("next_cursor") is not None:
        out["nextCursor"] = str(page["next_cursor"])
    return out


def _sec_exec_approvals_set(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    if _sec_estop(cfg):
        return _sec_estop_refusal("exec approvals set")
    from pathlib import Path

    from ...approvals import ApprovalJournal

    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                              default_ttl_s=120.0)
    pending = journal.pending()
    data = getattr(cfg, "data", None)
    approvals_cfg = data.get("approvals", {}) if isinstance(data, dict) else {}
    return {"state": "UNAVAILABLE", "code": EXEC_APPROVALS_LOCAL_JOURNAL,
            "detail": "Qubit enforces command approval through the shared "
                      "confirmation journal and the broker capability policy; "
                      "there is no gateway-writable exec-approvals allowlist "
                      "file to edit",
            "qubit_counterpart": "`qubit approvals` + broker capability policy",
            "pending": len(pending),
            "resolvedDefaults": approvals_cfg}


def _sec_exec_approvals_node_get(cfg: Any, params: Dict[str, Any]
                                 ) -> Dict[str, Any]:
    from ... import node_host as NH

    node_id = _sec_param(params, "nodeId", "node_id", "id")
    if node_id:
        node = NH.show(cfg, node_id)
        caps = NH.capabilities(cfg, node_id)
    else:
        node = NH.status(cfg)
        caps = None
    return {"state": "OK", "code": EXEC_APPROVALS_NODE_LOCAL_POLICY,
            "nodeId": node_id or None, "node": node,
            "capabilities": caps,
            "qubit_counterpart": "`qubit node list/show` + broker capability "
                                 "confirmation policy",
            "detail": "Qubit nodes are capability- and confirmation-gated; "
                      "there is no per-node exec-approvals allowlist"}


def _sec_exec_approvals_node_set(cfg: Any, params: Dict[str, Any]
                                 ) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": EXEC_APPROVALS_NODE_OPERATOR_REQUIRED,
            "detail": "Qubit runs no remote node-side exec-approval allowlist; "
                      "node actions are capability- and confirmation-gated",
            "operator_prerequisite": "a paired node agent that owns its own "
                                     "exec policy",
            "qubit_counterpart": "`qubit node enroll/revoke` + broker "
                                 "confirmation policy"}


# --------------------------------------------------------------- devices
def _sec_device_pair_rename(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    from ... import device as DEV

    if _sec_estop(cfg):
        return _sec_estop_refusal("device rename")
    device_id = _sec_param(params, "deviceId", "device_id", "tokenId",
                           "token_id", "id")
    label = _sec_param(params, "label", "name")
    if not device_id:
        raise GatewayError(SEC_BAD_PARAMS, "deviceId is required")
    if not label:
        raise GatewayError(SEC_BAD_PARAMS, "label is required")
    if not _sec_confirmed(params):
        return {"state": "denied", "code": "CONFIRM_REQUIRED",
                "detail": "device rename requires confirm=true"}
    return DEV.token_rename(cfg, device_id, label,
                            requested_by="gateway:device",
                            confirmed=True)


def _sec_device_scopes_request(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    from ... import device as DEV

    if _sec_estop(cfg):
        return _sec_estop_refusal("device scope upgrade")
    device_id = _sec_param(params, "deviceId", "device_id", "id")
    scopes = params.get("scopes")
    if not device_id:
        raise GatewayError(SEC_BAD_PARAMS, "deviceId is required")
    if scopes is None:
        raise GatewayError(SEC_BAD_PARAMS, "scopes is required")
    if not _sec_confirmed(params):
        return {"state": "denied", "code": "CONFIRM_REQUIRED",
                "detail": "requesting a device scope upgrade requires "
                          "confirm=true"}
    out = DEV.token_issue(cfg, device_id=device_id, scopes=scopes,
                          label="scope upgrade",
                          requested_by="gateway:device", confirmed=True)
    if str(out.get("state")) != "OK":
        return out
    return {"state": "OK", "requestId": out["token_id"],
            "upgradeId": out["token_id"], "deviceId": out["device_id"],
            "requestedScopes": out["scopes"], "status": "pending",
            "expiresAt": out["expires_at"], "token": out["token"],
            "warning": out["warning"],
            "authority": "voicebridge.device.token_issue (pending approval)"}


def _sec_device_scopes_wait(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    from ... import device as DEV

    request_id = _sec_param(params, "requestId", "request_id", "upgradeId",
                            "upgrade_id", "tokenId", "token_id")
    if not request_id:
        raise GatewayError(SEC_BAD_PARAMS, "requestId is required")
    out = DEV.token_show(cfg, request_id)
    if str(out.get("state")) != "OK":
        return out
    rec = out["token"]
    return {"state": "OK", "requestId": request_id,
            "deviceId": rec.get("device_id"), "status": rec.get("status"),
            "scopes": rec.get("scopes"), "approvedAt": rec.get("approved_at"),
            "expiresAt": rec.get("expires_at"),
            "authority": "voicebridge.device.token_show"}


# ---------------------------------------------------------------- attach
def _sec_attach_operator_gate(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED,
            "detail": f"{operation} needs the MCP loopback attach runtime and "
                      "a paired browser device identity; Qubit serves no MCP "
                      "loopback HTTP server",
            "operator_prerequisite": "an MCP loopback attach server + a paired "
                                     "device identity",
            "qubit_counterpart": "scoped Qubit session share "
                                 "(voicebridge.session_share) + device tokens"}


def _sec_attach_grant(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sec_attach_operator_gate("attach.grant")


def _sec_attach_revoke(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sec_attach_operator_gate("attach.revoke")


# ----------------------------------------------------------------- registry
def register(registry: Any) -> None:
    """Register the approvals/devices/attach handlers on *registry*."""
    registry.register("approval.history", _sec_approval_history)
    registry.register("exec.approvals.set", _sec_exec_approvals_set)
    registry.register("exec.approvals.node.get",
                      _sec_exec_approvals_node_get)
    registry.register("exec.approvals.node.set",
                      _sec_exec_approvals_node_set)
    registry.register("device.pair.rename", _sec_device_pair_rename)
    registry.register("device.scopes.requestUpgrade",
                      _sec_device_scopes_request)
    registry.register("device.scopes.waitUpgrade", _sec_device_scopes_wait)
    registry.register("attach.grant", _sec_attach_grant)
    registry.register("attach.revoke", _sec_attach_revoke)
