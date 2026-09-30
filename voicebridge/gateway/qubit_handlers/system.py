"""OpenClaw contractual-pin gateway handlers — system / operator families.

This module owns the ``(none)`` approval/question/secrets families plus the
``agent``, ``agents``, ``audit``, ``config``, ``device*``, ``devices``,
``diagnostics``, ``doctor``, ``environments``, ``fs``, ``migrations``,
``nodes``, ``plugins-control-ui``, ``restart``, ``suspend``, ``system``,
``system-agent`` (``openclaw.*``), ``system-changes``, ``update``, ``web`` and
``wizard`` families that the pinned v2026.9.2 descriptor table classifies. Every handler here delegates to the SAME canonical
Qubit module the CLI, dashboard, ``/qubit-chat`` and voice surfaces already
use: the shared approval journal (``voicebridge.approvals``), the question
contract store (``voicebridge.question_prompt``), the vault-reference registry
(``voicebridge.env_store`` / ``qubit_compat``), the action-receipt audit store
(``voicebridge.actions``), device/pairing/node-host stores, the runtime image
manager, the memory workspace, the plugin runtime and the lifecycle inventory.

Policy (matches ``qubit_handlers/__init__.py``):

* a method is registered ONLY when it has a real local counterpart that does
  work; a bare success literal is never registered;
* mutating operations route through the canonical confirmation/e-stop path
  (``approvals`` journal, ``permissions`` grants, ``qubit_compat`` confirm
  gate, ``node_host`` confirm gate, ``runtime_images`` guard) — the gateway is
  a transport, not a second authority;
* a capability whose upstream action needs a service Qubit does not run (cloud
  worker environments, host restart, update campaigns) emits a typed
  OPERATOR_ONLY refusal with its exact prerequisite, never a fabricated
  success;
* everything else stays OPEN in the generated manifest (not registered here).

Refusal handlers intentionally perform no backend call: the deterministic
manifest proof classifies a registered handler with a backend attribute call as
IMPLEMENTED, so a typed refusal must stay free of backend calls to be reported
as its real SAFE_EQUIVALENT / OPERATOR_ONLY disposition.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..gateway_ws import GatewayError

#: Typed refusal codes. Each is a terminal disposition the integration lead
#: records in ``PINNED_SURFACE_DISPOSITIONS``; the generator asserts the
#: registered handler actually emits the declared string.
SYS_EXEC_APPROVAL_NO_BLOCKING_WAIT = "EXEC_APPROVAL_NO_BLOCKING_WAIT"
SYS_PLUGIN_APPROVAL_NO_BLOCKING_WAIT = "PLUGIN_APPROVAL_NO_BLOCKING_WAIT"
SYS_QUESTION_NO_BLOCKING_WAIT = "QUESTION_NO_BLOCKING_WAIT"
SYS_SECRET_READ_REFUSED = "SECRET_READ_REFUSED"
SYS_ENVIRONMENT_CLOUD_OPERATOR_REQUIRED = "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED"
SYS_RESTART_OPERATOR_REQUIRED = "GATEWAY_RESTART_OPERATOR_REQUIRED"
SYS_UPDATE_HOLD_OPERATOR_ONLY = "UPDATE_HOLD_OPERATOR_ONLY"

_BAD = "GATEWAY_BAD_PARAMS"


def _sys_str(params: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = params.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _sys_require(params: Dict[str, Any], *keys: str) -> str:
    out = _sys_str(params, *keys)
    if not out:
        raise GatewayError(_BAD, f"one of {', '.join(keys)} is required")
    return out


def _sys_journal(cfg: Any) -> Any:
    """The one durable approval journal (same file rpc.py reads)."""
    from ...approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=120.0)


def _sys_approval_public(item: Any) -> Dict[str, Any]:
    import dataclasses

    return dataclasses.asdict(item)


# --------------------------------------------------------- approval family
def _sys_approval_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    approval_id = _sys_require(params, "id", "approvalId", "approval_id")
    item = _sys_journal(cfg).get(approval_id)
    if item is None:
        return {"state": "NOT_FOUND", "code": "APPROVAL_NOT_FOUND",
                "id": approval_id}
    return {"state": "OK", "approval": _sys_approval_public(item),
            "authority": "voicebridge.approvals.ApprovalJournal"}


def _sys_approval_resolve(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    approval_id = _sys_require(params, "id", "approvalId", "approval_id")
    decision = _sys_str(params, "decision", default="approve")
    if decision not in ("approve", "deny"):
        raise GatewayError(_BAD, "decision must be 'approve' or 'deny'")
    item = _sys_journal(cfg).respond(approval_id, decision)
    return {"state": "OK", "approval": _sys_approval_public(item),
            "authority": "voicebridge.approvals.ApprovalJournal"}


def _sys_exec_approval_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_approval_get(cfg, params)


def _sys_exec_approval_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    pending = _sys_journal(cfg).pending()
    shell = [a for a in pending
             if str(getattr(a, "capability", "")).startswith(("exec", "shell"))]
    return {"state": "OK",
            "approvals": [_sys_approval_public(a) for a in shell],
            "count": len(shell),
            "authority": "voicebridge.approvals.ApprovalJournal"}


def _sys_exec_approval_request(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    capability = _sys_str(params, "capability", default="exec.approval")
    summary = _sys_str(params, "summary", "command", "detail",
                       default="exec approval requested")
    item = _sys_journal(cfg).create(capability, summary,
                                    ttl_s=params.get("ttl_s"))
    return {"state": "OK", "approval": _sys_approval_public(item),
            "authority": "voicebridge.approvals.ApprovalJournal"}


def _sys_exec_approval_resolve(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_approval_resolve(cfg, params)


def _sys_exec_approval_wait_decision(cfg: Any,
                                     params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SYS_EXEC_APPROVAL_NO_BLOCKING_WAIT,
            "detail": "Qubit's approval journal is poll-based, not a blocking "
                      "wait; no decision is fabricated.",
            "qubit_counterpart": "exec.approval.list + exec.approval.get, or "
                                 "the dashboard/CLI `qubit approvals` banner"}


def _sys_exec_approval_grants_list(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    from ...permissions import permissions_store

    granted = permissions_store(cfg).grants()
    return {"state": "OK",
            "grants": [g.to_dict() for g in granted],
            "count": len(granted),
            "authority": "voicebridge.permissions.PermissionsStore"}


def _sys_exec_approval_grants_revoke(cfg: Any,
                                     params: Dict[str, Any]) -> Dict[str, Any]:
    from ...permissions import permissions_store

    grant_id = _sys_require(params, "grantId", "grant_id", "id")
    grant = permissions_store(cfg).revoke(
        grant_id, requested_by=_sys_str(params, "requested_by",
                                        default="gateway"),
        confirmed=bool(params.get("confirmed", params.get("confirm", False))))
    return {"state": "OK", "grant": grant.to_dict(),
            "authority": "voicebridge.permissions.PermissionsStore"}


def _sys_plugin_approval_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    pending = _sys_journal(cfg).pending()
    rows = [a for a in pending
            if str(getattr(a, "capability", "")).startswith("plugin")]
    return {"state": "OK", "approvals": [_sys_approval_public(a) for a in rows],
            "count": len(rows),
            "authority": "voicebridge.approvals.ApprovalJournal"}


def _sys_plugin_approval_request(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    capability = _sys_str(params, "capability", default="plugin.approval")
    summary = _sys_str(params, "summary", "pluginId", "plugin_id",
                       default="plugin approval requested")
    item = _sys_journal(cfg).create(capability, summary,
                                    ttl_s=params.get("ttl_s"))
    return {"state": "OK", "approval": _sys_approval_public(item),
            "authority": "voicebridge.approvals.ApprovalJournal"}


def _sys_plugin_approval_resolve(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_approval_resolve(cfg, params)


def _sys_plugin_approval_wait_decision(cfg: Any,
                                       params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": SYS_PLUGIN_APPROVAL_NO_BLOCKING_WAIT,
            "detail": "Qubit's approval journal is poll-based, not a blocking "
                      "wait; no plugin decision is fabricated.",
            "qubit_counterpart": "plugin.approval.list + the dashboard/CLI "
                                 "`qubit approvals` banner"}


# --------------------------------------------------------- question family
def _sys_question_request(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import question_prompt as QP

    return QP.ask(cfg, params.get("questions"),
                  session=_sys_str(params, "session"),
                  choice_limit=int(params.get("choice_limit") or
                                   QP.DEFAULT_CHOICE_LIMIT),
                  confirmed=bool(params.get("confirmed",
                                            params.get("confirm", False))))


def _sys_question_resolve(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import question_prompt as QP

    prompt_id = _sys_require(params, "promptId", "prompt_id", "id")
    return QP.answer(cfg, prompt_id, params.get("answers"))


def _sys_question_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import question_prompt as QP

    return QP.pending(cfg)


def _sys_question_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import question_prompt as QP

    prompt_id = _sys_require(params, "promptId", "prompt_id", "id")
    out = QP.pending(cfg)
    for item in out.get("pending", []):
        if str(item.get("prompt_id", "")) == prompt_id:
            return {"state": "OK", "prompt": item,
                    "authority": "voicebridge.question_prompt"}
    return {"state": "NOT_FOUND", "code": "QUESTION_NOT_FOUND",
            "prompt_id": prompt_id}


def _sys_question_wait_answer(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SYS_QUESTION_NO_BLOCKING_WAIT,
            "detail": "Qubit's question store is poll-based, not a blocking "
                      "wait; no answer is fabricated.",
            "qubit_counterpart": "question.list + question.resolve, or the "
                                 "dashboard/voice prompt card"}


# ---------------------------------------------------------- secrets family
def _sys_secrets_reload(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import env_store as ES
    from ...auth_status import auth_status

    return {"state": "OK", "env_references": ES.names(cfg),
            "auth": auth_status(cfg),
            "authority": "voicebridge.env_store + auth_status (references "
                         "only; no secret value is read or returned)"}


def _sys_secrets_resolve(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    # Deliberately performs no backend call: resolving a reference would read
    # or return a secret value, which the gateway never does.
    return {"state": "UNAVAILABLE", "code": SYS_SECRET_READ_REFUSED,
            "detail": "Qubit never resolves a secret value over the gateway; "
                      "only reference names are reportable.",
            "qubit_counterpart": "CLI `qubit auth status` (reference names "
                                 "only)"}


def _sys_secrets_store_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...credentials import inventory

    return inventory(cfg)


def _sys_secrets_store_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...qubit_compat import env_write

    name = _sys_require(params, "name", "key")
    value = _sys_str(params, "value", "ref", "key_ref")
    confirmed = bool(params.get("confirmed", params.get("confirm", False)))
    code, body = env_write(cfg, {}, {"name": name, "value": value,
                                     "confirm": confirmed}, {})
    if code >= 400:
        raise GatewayError(str(body.get("code", "SECRET_WRITE_REFUSED")),
                           str(body.get("detail", ""))[:300])
    return body


def _sys_secrets_store_delete(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...qubit_compat import env_delete

    name = _sys_require(params, "name", "key")
    confirmed = bool(params.get("confirmed", params.get("confirm", False)))
    code, body = env_delete(cfg, {}, {"name": name, "confirm": confirmed}, {})
    if code >= 400:
        raise GatewayError(str(body.get("code", "SECRET_WRITE_REFUSED")),
                           str(body.get("detail", ""))[:300])
    return body


# ----------------------------------------------------------- config family
def _sys_config_write(cfg: Any, params: Dict[str, Any],
                      mode: str) -> Dict[str, Any]:
    from ...qubit_compat import config_save

    payload = params.get("config")
    if not isinstance(payload, dict):
        payload = params.get("values")
    if not isinstance(payload, dict):
        payload = {k: v for k, v in params.items()
                   if k not in ("confirm", "confirmed", "requested_by",
                                "mode", "config", "values")}
    body = {"config": payload,
            "confirm": bool(params.get("confirmed",
                                       params.get("confirm", False)))}
    code, out = config_save(cfg, {}, body, {})
    if code >= 400:
        raise GatewayError(str(out.get("code", "CONFIG_WRITE_FAILED")),
                           str(out.get("detail", ""))[:300])
    return {**out, "mode": mode,
            "authority": "voicebridge.qubit_compat.config_save "
                         "(confirm-gated overrides store)"}


def _sys_config_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_config_write(cfg, params, "set")


def _sys_config_apply(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_config_write(cfg, params, "apply")


def _sys_config_patch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_config_write(cfg, params, "patch")


# --------------------------------------------------------------- fs family
def _sys_fs_list_dir(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import files as F

    path = _sys_require(params, "path", "dir")
    depth = int(params.get("depth") or 1)
    return F.list_dir(cfg, path, depth=depth)


# ------------------------------------------------------------ agent family
def _sys_agent(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...agent import turn

    text = _sys_require(params, "text", "message", "prompt")
    return turn(cfg, text,
                session=_sys_str(params, "session", default="qubit"),
                model=_sys_str(params, "model") or None,
                timeout_s=params.get("timeout_s"))


def _sys_agent_identity_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import device as DEV

    profile = DEV.profile(cfg)
    return {"state": "OK", "deviceId": str(profile.get("machine_id", "")),
            "enrolled": bool(profile.get("enrolled", False)),
            "profile": profile,
            "authority": "voicebridge.device.profile"}


# ------------------------------------------------------------ audit family
def _sys_audit_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...actions import actions_store

    limit = int(params.get("limit") or 50)
    entries = actions_store(cfg).audit(limit=limit)
    return {"state": "OK", "audit": entries, "count": len(entries),
            "authority": "voicebridge.actions.ActionStore"}


def _sys_audit_activity_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...actions import actions_store

    limit = int(params.get("limit") or 30)
    receipts = actions_store(cfg).receipts(limit=limit)
    return {"state": "OK",
            "activity": [r.to_dict() for r in receipts],
            "count": len(receipts),
            "authority": "voicebridge.actions.ActionStore"}


def _sys_audit_run_inspect(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...actions import actions_store

    run_id = _sys_require(params, "runId", "run_id", "receiptId", "id")
    receipt = actions_store(cfg).receipt(run_id)
    if receipt is None:
        return {"state": "NOT_FOUND", "code": "AUDIT_RUN_NOT_FOUND",
                "run_id": run_id}
    return {"state": "OK", "run": receipt.to_dict(),
            "authority": "voicebridge.actions.ActionStore"}


# ------------------------------------------------------ diagnostics family
def _sys_diagnostics_stability(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import runtime_inventory as RI
    from ...lifecycle import health_inventory

    return {"state": "OK", "runtime": RI.runtime_inventory(cfg),
            "health": health_inventory(cfg),
            "authority": "voicebridge.runtime_inventory + "
                         "voicebridge.lifecycle.health_inventory"}


def _sys_diagnostics_lanes(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import runtime_inventory as RI

    inv = RI.runtime_inventory(cfg)
    return {"state": "OK", "adapters": inv.get("adapters", {}),
            "voice": inv.get("voice", {}),
            "providers": inv.get("providers", {}),
            "authority": "voicebridge.runtime_inventory.runtime_inventory"}


# ----------------------------------------------------- doctor memory family
def _sys_doctor_memory_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...memory import workspace as MW

    return {"state": "OK", "providers": MW.providers(),
            "notes": MW.list_notes(cfg),
            "authority": "voicebridge.memory.workspace"}


def _sys_doctor_memory_dream_diary(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    from ...memory import workspace as MW

    return MW.read_note(cfg, "dreaming")


def _sys_doctor_memory_dedupe_dream_diary(cfg: Any,
                                          params: Dict[str, Any]) -> Dict[str, Any]:
    from ...memory import workspace as MW

    confirmed = bool(params.get("confirmed", params.get("confirm", False)))
    return MW.dream(cfg, requested_by=_sys_str(params, "requested_by",
                                               default="gateway"),
                    confirmed=confirmed)


def _sys_doctor_memory_backfill_dream_diary(cfg: Any,
                                            params: Dict[str, Any]) -> Dict[str, Any]:
    from ...memory import workspace as MW

    confirmed = bool(params.get("confirmed", params.get("confirm", False)))
    return MW.dream(cfg, requested_by=_sys_str(params, "requested_by",
                                               default="gateway"),
                    confirmed=confirmed)


def _sys_doctor_memory_repair_dreaming_artifacts(
        cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...memory import workspace as MW

    confirmed = bool(params.get("confirmed", params.get("confirm", False)))
    return MW.dream(cfg, requested_by=_sys_str(params, "requested_by",
                                               default="gateway"),
                    confirmed=confirmed)


def _sys_doctor_memory_reset_dream_diary(cfg: Any,
                                         params: Dict[str, Any]) -> Dict[str, Any]:
    from ...memory import workspace as MW

    confirmed = bool(params.get("confirmed", params.get("confirm", False)))
    return MW.forget_note(cfg, "dreaming",
                          requested_by=_sys_str(params, "requested_by",
                                                default="gateway"),
                          confirmed=confirmed)


# ----------------------------------------------------- environments family
def _sys_environments_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import cloud_workers as CW
    from ... import runtime_images as RI

    return {"state": "OK", "cloud_workers": CW.config_get(cfg),
            "images": RI.status(cfg), "cells": RI.cells(cfg),
            "authority": "voicebridge.cloud_workers.config_get + "
                         "voicebridge.runtime_images"}


def _sys_environments_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import cloud_workers as CW
    from ... import node_host as NH
    from ... import runtime_images as RI

    return {"state": "OK", "cloud_workers": CW.config_get(cfg),
            "images": RI.status(cfg), "cells": RI.cells(cfg),
            "nodes": NH.status(cfg),
            "authority": "voicebridge.cloud_workers + runtime_images + "
                         "node_host (read-only inventory)"}


def _sys_environments_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": SYS_ENVIRONMENT_CLOUD_OPERATOR_REQUIRED,
            "detail": "Cloud worker environments need a provider transport "
                      "Qubit does not run; no environment is fabricated.",
            "operator_prerequisite": "an injected cloud-worker provider "
                                     "transport and a reachable provider",
            "qubit_counterpart": "voicebridge.cloud_workers + local process "
                                 "cells (`qubit runtime cells`)"}


def _sys_environments_destroy(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": SYS_ENVIRONMENT_CLOUD_OPERATOR_REQUIRED,
            "stopped": False,
            "detail": "No cloud worker environment is attached or attachable; "
                      "nothing is destroyed.",
            "operator_prerequisite": "an injected cloud-worker provider "
                                     "transport and a reachable provider",
            "qubit_counterpart": "voicebridge.cloud_workers + local process "
                                 "cells (`qubit runtime cells`)"}


def _sys_desktop_launch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import computer_use as CU

    app = _sys_require(params, "app", "application", "name")
    return CU.launch_app(cfg, app,
                         target=_sys_str(params, "target", default="desktop"),
                         confirmed=bool(params.get("confirmed",
                                                   params.get("confirm",
                                                              False))),
                         requested_by=_sys_str(params, "requested_by",
                                               default="gateway"))


def _sys_desktop_observe(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import computer_use as CU

    return {**CU.screen_status(cfg),
            "authority": "voicebridge.computer_use.screen_status"}


def _sys_worker_desktop_launch(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_desktop_launch(cfg, params)


def _sys_worker_desktop_observe(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_desktop_observe(cfg, params)


# -------------------------------------------------------- migrations family
def _sys_migrations_memory_plan(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import qubit_migration as QM

    home = _sys_str(params, "home") or None
    return QM.plan(cfg, home=Path(home) if home else None)


def _sys_migrations_memory_apply(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import qubit_migration as QM

    backup_dir = _sys_str(params, "backup_dir", "backupDir") or None
    return QM.apply(cfg,
                    confirm=bool(params.get("confirmed",
                                            params.get("confirm", False))),
                    backup_dir=Path(backup_dir) if backup_dir else None)


# ------------------------------------------------------------- nodes family
def _sys_node_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import node_host as NH

    return NH.list_nodes(cfg)


def _sys_node_describe(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import node_host as NH

    node_id = _sys_require(params, "nodeId", "node_id", "id")
    return NH.show(cfg, node_id)


def _sys_node_pair_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import node_host as NH

    return NH.list_nodes(cfg)


def _sys_node_pair_approve(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import node_host as NH

    name = _sys_str(params, "name", "label", "nodeId", "node_id")
    endpoint = _sys_str(params, "endpoint", "url")
    if not name or not endpoint:
        raise GatewayError(_BAD, "node.pair.approve requires 'name' and "
                                 "'endpoint' for the canonical node enrollment")
    return NH.enroll(cfg, name=name, endpoint=endpoint,
                     capabilities=params.get("capabilities"),
                     confirmation_id=_sys_str(params, "confirmation_id",
                                              "confirmationId"),
                     confirmed=bool(params.get("confirmed",
                                               params.get("confirm", False))),
                     source=_sys_str(params, "source", default="cli"),
                     requested_by=_sys_str(params, "requested_by",
                                           default="gateway"))


def _sys_node_pair_reject(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import node_host as NH

    node_id = _sys_require(params, "nodeId", "node_id", "id")
    return NH.revoke(cfg, node_id,
                     confirmation_id=_sys_str(params, "confirmation_id",
                                              "confirmationId"),
                     confirmed=bool(params.get("confirmed",
                                               params.get("confirm", False))),
                     source=_sys_str(params, "source", default="cli"),
                     requested_by=_sys_str(params, "requested_by",
                                           default="gateway"))


def _sys_node_pair_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_node_pair_reject(cfg, params)


def _sys_node_plugin_surface_refresh(cfg: Any,
                                     params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import node_host as NH

    node_id = _sys_require(params, "nodeId", "node_id", "id")
    return NH.capabilities(cfg, node_id)


def _sys_node_invoke(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import node_host as NH

    node_id = _sys_require(params, "nodeId", "node_id", "id")
    capability = _sys_require(params, "capability", "method")
    return NH.dispatch(cfg, node_id, capability,
                       args=params.get("args"),
                       confirmed=bool(params.get("confirmed",
                                                 params.get("confirm",
                                                            False))),
                       requested_by=_sys_str(params, "requested_by",
                                             default="gateway"))


# ------------------------------------------------- plugins control-ui family
def _sys_plugins_controlui_list(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import plugin_runtime as PR
    from ... import qubit_plugins as QP

    listing = PR.list_plugins(cfg)
    rows = []
    for plugin in listing.get("plugins", []) or []:
        plugin_id = str(plugin.get("id", plugin.get("plugin_id", "")) or "")
        surface = QP.dashboard_surface(plugin_id) if plugin_id else None
        rows.append({"plugin_id": plugin_id,
                     "dashboard": bool(surface),
                     "surface": Path(surface).name if surface else ""})
    return {"state": "OK", "plugins": rows, "count": len(rows),
            "authority": "voicebridge.plugin_runtime.list_plugins + "
                         "voicebridge.qubit_plugins.dashboard_surface"}


def _sys_plugins_controlui_status(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import plugin_runtime as PR

    return PR.status(cfg)


def _sys_plugins_controlui_reload(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import plugin_runtime as PR

    return PR.reload(cfg)


def _sys_plugins_controlui_report(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import plugin_runtime as PR
    from ... import qubit_plugins as QP

    return {"state": "OK", "doctor": PR.doctor(cfg),
            "fleet": QP.plugin_fleet_audit(),
            "authority": "voicebridge.plugin_runtime.doctor + "
                         "voicebridge.qubit_plugins.plugin_fleet_audit"}


# ----------------------------------------------------------- restart family
def _sys_gateway_restart_preflight(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import health_inventory, service_inventory

    return {"state": "OK", "services": service_inventory(),
            "health": health_inventory(cfg),
            "authority": "voicebridge.lifecycle (read-only systemctl probe)"}


def _sys_gateway_restart_request(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SYS_RESTART_OPERATOR_REQUIRED,
            "restarting": False,
            "detail": "Qubit never restarts its own service from a gateway "
                      "call; nothing was restarted.",
            "operator_prerequisite": "an operator shell running `systemctl "
                                     "--user restart <qubit unit>`",
            "qubit_counterpart": "CLI `qubit setup health` + the operator "
                                 "runbook"}


# ----------------------------------------------------------- suspend family
def _sys_gateway_suspend_status(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ...power import power_control

    return {"state": "OK", "power": power_control(cfg).status(),
            "authority": "voicebridge.power.PowerControl (the one power/"
                         "suspend state authority)"}


# ------------------------------------------------------------ system family
def _sys_last_heartbeat(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import automation as AU

    listing = AU.heartbeat_list(cfg)
    return {"state": "OK", "heartbeats": listing.get("heartbeats", []),
            "due": AU.heartbeat_due(cfg),
            "authority": "voicebridge.automation.heartbeat_list"}


def _sys_set_heartbeats(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import automation as AU

    name = _sys_require(params, "name", "id")
    interval = int(params.get("interval_min") or params.get("interval") or 0)
    if interval <= 0:
        raise GatewayError(_BAD, "interval_min is required and must be > 0")
    return AU.heartbeat_add(cfg, name, interval,
                            note=_sys_str(params, "note"),
                            requested_by=_sys_str(params, "requested_by",
                                                  default="gateway"),
                            confirmed=bool(params.get("confirmed",
                                                      params.get("confirm",
                                                                 False))))


# ------------------------------------------------------------ update family
def _sys_update_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import runtime_images as RI

    report = RI.update_report(cfg)
    report["images"] = [r["name"] for r in RI.REGISTRY]
    return report


def _sys_update_runs_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import runtime_images as RI

    return RI.update_history(cfg, name=_sys_str(params, "name", "image"))


def _sys_update_runs_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import runtime_images as RI

    run_id = _sys_require(params, "runId", "run_id", "id")
    history = RI.update_history(cfg, name=_sys_str(params, "name", "image"))
    for entry in history.get("entries", []):
        if str(entry.get("id", entry.get("run_id", ""))) == run_id:
            return {"state": "OK", "run": entry,
                    "authority": "voicebridge.runtime_images.update_history"}
    return {"state": "NOT_FOUND", "code": "UPDATE_RUN_NOT_FOUND",
            "run_id": run_id}


def _sys_update_run(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import runtime_images as RI

    name = _sys_require(params, "name", "image")
    return RI.upgrade(cfg, name,
                      requested_by=_sys_str(params, "requested_by",
                                            default="gateway"),
                      confirmed=bool(params.get("confirmed",
                                                params.get("confirm",
                                                           False))))


def _sys_update_hold(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SYS_UPDATE_HOLD_OPERATOR_ONLY,
            "held": False,
            "detail": "Qubit runs no hosted update campaign to hold; nothing "
                      "was paused.",
            "operator_prerequisite": "none local; use `qubit runtime upgrade "
                                     "--confirm` (per image) for a real update",
            "qubit_counterpart": "voicebridge.runtime_images.update_history + "
                                 "`qubit runtime upgrade`"}


# --------------------------------------------------------------- web family
def _sys_web_login_start(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...auth_login import login_plan

    provider = _sys_require(params, "provider")
    return login_plan(cfg, provider,
                      requested_by=_sys_str(params, "requested_by",
                                            default="gateway"),
                      open_browser=bool(params.get("open_browser", False)))


def _sys_web_login_wait(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...auth_login import verify_provider

    provider = _sys_require(params, "provider")
    return verify_provider(cfg, provider)


# ------------------------------------------------------------ wizard family
def _sys_wizard_start(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import audio_wizard

    return audio_wizard(cfg)


def _sys_wizard_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import audio_wizard

    return audio_wizard(cfg)


# ----------------------------------------------------------- devices family
def _sys_device_pair_setup_code(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as P

    label = _sys_str(params, "label", default="device") or "device"
    scopes = params.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        scopes = ["operator.read"]
    return P.encode_setup(cfg, label, scopes,
                          account=_sys_str(params, "account"),
                          ttl_s=float(params.get("ttl_s") or P.DEFAULT_TTL_S),
                          base_url=_sys_str(params, "base_url", "baseUrl"),
                          requested_by=_sys_str(params, "requested_by",
                                                default="gateway"),
                          confirmed=bool(params.get("confirmed",
                                                    params.get("confirm",
                                                               False))))


def _sys_device_pair_setup_status(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as P

    return P.list_codes(cfg)


def _sys_device_pair_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as P

    codes = P.list_codes(cfg)
    return {"state": "OK", "codes": codes.get("codes", []),
            "pairings": P.channel_dm_pairings(cfg).get("pairings", []),
            "authority": "voicebridge.pairing (codes + accepted DM pairings)"}


def _sys_device_pair_approve(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as P

    code = _sys_require(params, "code", "pairingCode", "pairing_code")
    return P.approve(cfg, code,
                     requested_by=_sys_str(params, "requested_by",
                                           default="gateway"),
                     confirmed=bool(params.get("confirmed",
                                               params.get("confirm", False))))


def _sys_device_pair_reject(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as P

    code_id = _sys_require(params, "codeId", "code_id", "id")
    return P.revoke(cfg, code_id,
                    requested_by=_sys_str(params, "requested_by",
                                          default="gateway"),
                    confirmed=bool(params.get("confirmed",
                                              params.get("confirm", False))))


def _sys_device_pair_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sys_device_pair_reject(cfg, params)


def _sys_device_token_rotate(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import device as DEV

    token_id = _sys_require(params, "tokenId", "token_id", "id")
    return DEV.token_rotate(cfg, token_id,
                            requested_by=_sys_str(params, "requested_by",
                                                  default="gateway"),
                            confirmed=bool(params.get("confirmed",
                                                      params.get("confirm",
                                                                 False))))


def _sys_device_token_revoke(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import device as DEV

    token_id = _sys_require(params, "tokenId", "token_id", "id")
    return DEV.token_revoke(cfg, token_id,
                            requested_by=_sys_str(params, "requested_by",
                                                  default="gateway"),
                            confirmed=bool(params.get("confirmed",
                                                      params.get("confirm",
                                                                 False))))


# ---------------------------------------------- system-agent chat / approval
def _sys_openclaw_chat(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...agent import turn

    text = _sys_require(params, "text", "message", "prompt")
    return turn(cfg, text,
                session=_sys_str(params, "session", default="qubit"),
                model=_sys_str(params, "model") or None,
                timeout_s=params.get("timeout_s"))


def _sys_openclaw_chat_history(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    from ...qubit_session_store import QubitSessionStore

    session_id = _sys_require(params, "session", "sessionKey", "session_id")
    limit = int(params.get("limit") or 100)
    out = QubitSessionStore(cfg).messages(session_id, limit=limit)
    return {**out,
            "authority": "voicebridge.qubit_session_store."
                         "QubitSessionStore.messages"}


def _sys_openclaw_approval_list(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    pending = _sys_journal(cfg).pending()
    return {"state": "OK", "approvals": [_sys_approval_public(a)
                                         for a in pending],
            "count": len(pending),
            "authority": "voicebridge.approvals.ApprovalJournal (the one "
                         "system-agent confirmation journal)"}


# ------------------------------------------------- openclaw setup / changes
def _sys_openclaw_setup_detect(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import checklist

    return checklist(cfg, offline=bool(params.get("offline", False)))


def _sys_openclaw_setup_verify(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import health_inventory

    return health_inventory(cfg, offline=bool(params.get("offline", False)))


def _sys_openclaw_setup_prepare(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import setup_run

    steps = params.get("steps")
    return setup_run(steps=steps if isinstance(steps, list) else None,
                     dry_run=True, confirm=False)


def _sys_openclaw_setup_activate(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import setup_run

    steps = params.get("steps")
    confirmed = bool(params.get("confirmed", params.get("confirm", False)))
    return setup_run(steps=steps if isinstance(steps, list) else None,
                     dry_run=not confirmed, confirm=confirmed)


def _sys_openclaw_setup_auth_start(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    from ...auth_login import login_plan

    provider = _sys_require(params, "provider")
    return login_plan(cfg, provider,
                      requested_by=_sys_str(params, "requested_by",
                                            default="gateway"),
                      open_browser=bool(params.get("open_browser", False)))


def _sys_openclaw_changes_list(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    from ...lifecycle import audit_timeline

    limit = int(params.get("limit") or 50)
    return audit_timeline(limit=limit)


# ------------------------------------------------- plugin surface / presence / events
def _sys_plugin_surface_refresh(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import plugin_runtime as PR
    from ... import qubit_plugins as QP

    return {"state": "OK", "reload": PR.reload(cfg),
            "fleet": QP.plugin_fleet_audit(),
            "authority": "voicebridge.plugin_runtime.reload + "
                         "voicebridge.qubit_plugins.plugin_fleet_audit"}


def _sys_system_presence(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import gateway_peers as GP
    from ... import node_host as NH
    from ...qubit_session_store import QubitSessionStore

    sessions = QubitSessionStore(cfg).list(limit=50)
    return {"state": "OK",
            "discovery": GP.discovery(cfg),
            "nodes": NH.status(cfg),
            "sessions": sessions,
            "authority": "voicebridge.gateway_peers.discovery + "
                         "voicebridge.node_host.status + "
                         "voicebridge.qubit_session_store.QubitSessionStore"}


def _sys_system_event(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import automation as AU

    kind = _sys_require(params, "kind")
    source = _sys_str(params, "source", default="gateway")
    text = _sys_str(params, "text", "detail", "message")
    if not text:
        raise GatewayError(_BAD, "text is required for a system event")
    return AU.queue_event(cfg, kind=kind, source=source, text=text,
                          requested_by=_sys_str(params, "requested_by",
                                                default="gateway"),
                          confirmed=bool(params.get("confirmed",
                                                    params.get("confirm",
                                                               False))))


# --------------------------------------------------------------- register
def register(registry: Any) -> None:
    """Register every implemented system/operator family handler."""
    # (none) — approvals
    registry.register("approval.get", _sys_approval_get)
    registry.register("approval.resolve", _sys_approval_resolve)
    registry.register("exec.approval.get", _sys_exec_approval_get)
    registry.register("exec.approval.list", _sys_exec_approval_list)
    registry.register("exec.approval.request", _sys_exec_approval_request)
    registry.register("exec.approval.resolve", _sys_exec_approval_resolve)
    registry.register("exec.approval.waitDecision",
                      _sys_exec_approval_wait_decision)
    registry.register("exec.approval.grants.list",
                      _sys_exec_approval_grants_list)
    registry.register("exec.approval.grants.revoke",
                      _sys_exec_approval_grants_revoke)
    registry.register("plugin.approval.list", _sys_plugin_approval_list)
    registry.register("plugin.approval.request", _sys_plugin_approval_request)
    registry.register("plugin.approval.resolve", _sys_plugin_approval_resolve)
    registry.register("plugin.approval.waitDecision",
                      _sys_plugin_approval_wait_decision)
    # (none) — questions
    registry.register("question.request", _sys_question_request)
    registry.register("question.resolve", _sys_question_resolve)
    registry.register("question.list", _sys_question_list)
    registry.register("question.get", _sys_question_get)
    registry.register("question.waitAnswer", _sys_question_wait_answer)
    # (none) — secrets
    registry.register("secrets.reload", _sys_secrets_reload)
    registry.register("secrets.resolve", _sys_secrets_resolve)
    registry.register("secrets.store.list", _sys_secrets_store_list)
    registry.register("secrets.store.set", _sys_secrets_store_set)
    registry.register("secrets.store.delete", _sys_secrets_store_delete)
    # config
    registry.register("config.set", _sys_config_set)
    registry.register("config.apply", _sys_config_apply)
    registry.register("config.patch", _sys_config_patch)
    # fs
    registry.register("fs.listDir", _sys_fs_list_dir)
    # agent
    registry.register("agent", _sys_agent)
    registry.register("agent.identity.get", _sys_agent_identity_get)
    # audit
    registry.register("audit.list", _sys_audit_list)
    registry.register("audit.activity.list", _sys_audit_activity_list)
    registry.register("audit.run.inspect", _sys_audit_run_inspect)
    # diagnostics
    registry.register("diagnostics.stability", _sys_diagnostics_stability)
    registry.register("diagnostics.lanes", _sys_diagnostics_lanes)
    # doctor memory
    registry.register("doctor.memory.status", _sys_doctor_memory_status)
    registry.register("doctor.memory.dreamDiary",
                      _sys_doctor_memory_dream_diary)
    registry.register("doctor.memory.dedupeDreamDiary",
                      _sys_doctor_memory_dedupe_dream_diary)
    registry.register("doctor.memory.backfillDreamDiary",
                      _sys_doctor_memory_backfill_dream_diary)
    registry.register("doctor.memory.repairDreamingArtifacts",
                      _sys_doctor_memory_repair_dreaming_artifacts)
    registry.register("doctor.memory.resetDreamDiary",
                      _sys_doctor_memory_reset_dream_diary)
    # environments
    registry.register("environments.list", _sys_environments_list)
    registry.register("environments.status", _sys_environments_status)
    registry.register("environments.create", _sys_environments_create)
    registry.register("environments.destroy", _sys_environments_destroy)
    registry.register("desktop.launch", _sys_desktop_launch)
    registry.register("desktop.observe", _sys_desktop_observe)
    registry.register("worker.desktop.launch", _sys_worker_desktop_launch)
    registry.register("worker.desktop.observe", _sys_worker_desktop_observe)
    # migrations
    registry.register("migrations.memory.plan", _sys_migrations_memory_plan)
    registry.register("migrations.memory.apply", _sys_migrations_memory_apply)
    # nodes
    registry.register("node.list", _sys_node_list)
    registry.register("node.describe", _sys_node_describe)
    registry.register("node.pair.list", _sys_node_pair_list)
    registry.register("node.pair.approve", _sys_node_pair_approve)
    registry.register("node.pair.reject", _sys_node_pair_reject)
    registry.register("node.pair.remove", _sys_node_pair_remove)
    registry.register("node.pluginSurface.refresh",
                      _sys_node_plugin_surface_refresh)
    registry.register("node.invoke", _sys_node_invoke)
    # plugins control-ui
    registry.register("plugins.controlUi.list", _sys_plugins_controlui_list)
    registry.register("plugins.controlUi.status", _sys_plugins_controlui_status)
    registry.register("plugins.controlUi.reload", _sys_plugins_controlui_reload)
    registry.register("plugins.controlUi.report", _sys_plugins_controlui_report)
    # restart
    registry.register("gateway.restart.preflight",
                      _sys_gateway_restart_preflight)
    registry.register("gateway.restart.request", _sys_gateway_restart_request)
    # suspend
    registry.register("gateway.suspend.status", _sys_gateway_suspend_status)
    # system
    registry.register("last-heartbeat", _sys_last_heartbeat)
    registry.register("set-heartbeats", _sys_set_heartbeats)
    # update
    registry.register("update.status", _sys_update_status)
    registry.register("update.runs.list", _sys_update_runs_list)
    registry.register("update.runs.get", _sys_update_runs_get)
    registry.register("update.run", _sys_update_run)
    registry.register("update.hold", _sys_update_hold)
    # web
    registry.register("web.login.start", _sys_web_login_start)
    registry.register("web.login.wait", _sys_web_login_wait)
    # wizard
    registry.register("wizard.start", _sys_wizard_start)
    registry.register("wizard.status", _sys_wizard_status)
    # devices
    registry.register("device.pair.setupCode", _sys_device_pair_setup_code)
    registry.register("device.pair.setupStatus", _sys_device_pair_setup_status)
    registry.register("device.pair.list", _sys_device_pair_list)
    registry.register("device.pair.approve", _sys_device_pair_approve)
    registry.register("device.pair.reject", _sys_device_pair_reject)
    registry.register("device.pair.remove", _sys_device_pair_remove)
    registry.register("device.token.rotate", _sys_device_token_rotate)
    registry.register("device.token.revoke", _sys_device_token_revoke)
    # system-agent (openclaw.*) chat + approvals
    registry.register("openclaw.chat", _sys_openclaw_chat)
    registry.register("openclaw.chat.history", _sys_openclaw_chat_history)
    registry.register("openclaw.approval.list", _sys_openclaw_approval_list)
    registry.register("openclaw.changes.list", _sys_openclaw_changes_list)
    # openclaw setup ladder (canonical lifecycle/checklist + auth handoff)
    registry.register("openclaw.setup.detect", _sys_openclaw_setup_detect)
    registry.register("openclaw.setup.verify", _sys_openclaw_setup_verify)
    registry.register("openclaw.setup.prepare.start",
                      _sys_openclaw_setup_prepare)
    registry.register("openclaw.setup.activate", _sys_openclaw_setup_activate)
    registry.register("openclaw.setup.activate.start",
                      _sys_openclaw_setup_activate)
    registry.register("openclaw.setup.auth.start",
                      _sys_openclaw_setup_auth_start)
    # plugin UI surface + system presence + system event queue
    registry.register("plugin.surface.refresh", _sys_plugin_surface_refresh)
    registry.register("system-presence", _sys_system_presence)
    registry.register("system-event", _sys_system_event)
