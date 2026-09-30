"""OpenClaw contractual-pin sessions compaction/lineage/companion family.

Every handler delegates to the canonical Qubit authority the CLI, dashboard,
``/qubit-chat``, and voice surfaces use:

* :mod:`voicebridge.session_compaction` — real compaction over the vendored
  ``SessionDB`` (``archive_and_compact`` / ``replace_messages``) and the
  vendored ``ContextCompressor``; a Qubit-owned checkpoint registry in the SAME
  ``state.db``; the real compression lineage walk;
* :mod:`voicebridge.session_companion` — the real side-chat companion session in
  the canonical store, answered through ``chat_transport.turn``.

Mechanism differences from upstream carry their own typed codes so they are
never presented as upstream behavior:

* ``sessions.branches.switch`` — upstream flips a leaf pointer; Qubit restores
  the target transcript in place (recoverable).
* ``sessions.compaction.branch`` / ``sessions.compaction.restore`` — upstream
  uses a file-backed checkpoint store; Qubit uses its DB checkpoint registry and
  the real soft-archived rows.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

SC_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes (real local counterpart, upstream mechanism
#: differs). The manifest asserts each is emitted by its registered handler.
SESSIONS_BRANCH_SWITCH_LOCAL_RESTORE = "SESSIONS_BRANCH_SWITCH_LOCAL_RESTORE"
SESSIONS_COMPACTION_BRANCH_LOCAL = "SESSIONS_COMPACTION_BRANCH_LOCAL"
SESSIONS_COMPACTION_RESTORE_LOCAL = "SESSIONS_COMPACTION_RESTORE_LOCAL"


def _sc_param(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _sc_sid(params: Dict[str, Any]) -> str:
    sid = _sc_param(params, "sessionKey", "session_id", "sessionId", "key",
                    "id", "session")
    if not sid:
        raise GatewayError(SC_BAD_PARAMS, "session key is required")
    return sid[:80]


def _sc_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return default


def _sc_confirmed(params: Dict[str, Any]) -> bool:
    return _sc_bool(params.get("confirm", params.get("confirmed", False)))


def _sc_estop(cfg: Any) -> bool:
    from ... import broker as BR

    return bool(BR.estop_engaged(cfg))


def _sc_estop_refusal(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
            "detail": f"emergency stop latched; {operation} refused"}


def _sc_store(cfg: Any) -> Any:
    from ... import chat_transport as CT

    return CT.store(cfg)


def _sc_require_confirm(params: Dict[str, Any], operation: str
                        ) -> Dict[str, Any]:
    return {"state": "CONFIRM_REQUIRED", "code": "CONFIRM_REQUIRED",
            "detail": f"{operation} is a lossy transcript mutation; pass "
                      "confirm=true"}


# ------------------------------------------------------------------- compact
def _sc_sessions_compact(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_compaction as SC

    if _sc_estop(cfg):
        return _sc_estop_refusal("session compact")
    sid = _sc_sid(params)
    if not _sc_confirmed(params):
        return _sc_require_confirm(params, "sessions.compact")
    raw_lines = params.get("maxLines", params.get("max_lines"))
    max_lines = None
    if raw_lines is not None:
        try:
            max_lines = max(1, int(raw_lines))
        except (TypeError, ValueError):
            raise GatewayError(SC_BAD_PARAMS, "maxLines must be an integer")
    focus = _sc_param(params, "focusTopic", "focus_topic", "topic") or None
    return SC.compact(_sc_store(cfg), sid, max_lines=max_lines,
                      focus_topic=focus)


# ----------------------------------------------------- compaction checkpoints
def _sc_compaction_branch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_compaction as SC

    if _sc_estop(cfg):
        return _sc_estop_refusal("compaction branch")
    sid = _sc_sid(params)
    checkpoint_id = _sc_param(params, "checkpointId", "checkpoint_id",
                              "checkpoint")
    if not checkpoint_id:
        raise GatewayError(SC_BAD_PARAMS, "checkpointId is required")
    if not _sc_confirmed(params):
        return _sc_require_confirm(params, "sessions.compaction.branch")
    out = SC.branch(_sc_store(cfg), sid, checkpoint_id)
    if str(out.get("state")) == "OK":
        out["code"] = SESSIONS_COMPACTION_BRANCH_LOCAL
        out["qubit_counterpart"] = ("voicebridge.session_compaction.branch "
                                    "(DB checkpoint registry + transcript copy)")
    return out


def _sc_compaction_restore(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_compaction as SC

    if _sc_estop(cfg):
        return _sc_estop_refusal("compaction restore")
    sid = _sc_sid(params)
    checkpoint_id = _sc_param(params, "checkpointId", "checkpoint_id",
                              "checkpoint")
    if not checkpoint_id:
        raise GatewayError(SC_BAD_PARAMS, "checkpointId is required")
    if not _sc_confirmed(params):
        return _sc_require_confirm(params, "sessions.compaction.restore")
    out = SC.restore(_sc_store(cfg), sid, checkpoint_id)
    if str(out.get("state")) == "OK":
        out["code"] = SESSIONS_COMPACTION_RESTORE_LOCAL
        out["qubit_counterpart"] = ("voicebridge.session_compaction.restore "
                                    "(replace_messages archive_dropped=True)")
    return out


# ------------------------------------------------------------ branches.switch
def _sc_branches_switch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_compaction as SC

    if _sc_estop(cfg):
        return _sc_estop_refusal("branch switch")
    sid = _sc_sid(params)
    leaf = _sc_param(params, "leafEntryId", "leaf_entry_id", "leafId",
                     "leaf_id", "branchId", "branch_id", "checkpointId")
    if not leaf:
        raise GatewayError(SC_BAD_PARAMS, "leafEntryId is required")
    if not _sc_confirmed(params):
        return _sc_require_confirm(params, "sessions.branches.switch")
    out = SC.switch_branch(_sc_store(cfg), sid, leaf)
    if str(out.get("state")) == "OK":
        out["code"] = SESSIONS_BRANCH_SWITCH_LOCAL_RESTORE
        out["qubit_counterpart"] = ("voicebridge.session_compaction."
                                    "switch_branch (restore in place)")
    return out


# ----------------------------------------------------------------- companion
def _sc_companion_ask(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_companion as COMP

    if _sc_estop(cfg):
        return _sc_estop_refusal("companion ask")
    sid = _sc_sid(params)
    question = _sc_param(params, "question", "text", "prompt")
    if not question:
        raise GatewayError(SC_BAD_PARAMS, "question is required")
    return COMP.ask(_sc_store(cfg), cfg, sid, question)


def _sc_companion_state(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_companion as COMP

    sid = _sc_sid(params)
    return COMP.state(_sc_store(cfg), sid)


def _sc_companion_reset(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_companion as COMP

    if _sc_estop(cfg):
        return _sc_estop_refusal("companion reset")
    sid = _sc_sid(params)
    return COMP.reset(_sc_store(cfg), sid, confirm=_sc_confirmed(params))


# ----------------------------------------------------------------- registry
def register(registry: Any) -> None:
    """Register the compaction/lineage/companion handlers on *registry*."""
    registry.register("sessions.compact", _sc_sessions_compact)
    registry.register("sessions.compaction.branch", _sc_compaction_branch)
    registry.register("sessions.compaction.restore", _sc_compaction_restore)
    registry.register("sessions.branches.switch", _sc_branches_switch)
    registry.register("sessions.companion.ask", _sc_companion_ask)
    registry.register("sessions.companion.state", _sc_companion_state)
    registry.register("sessions.companion.reset", _sc_companion_reset)
