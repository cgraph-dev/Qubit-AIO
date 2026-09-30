"""OpenClaw contractual-pin ``sessions`` advanced families -> canonical Qubit.

This module owns the remaining contractual-pin sessions-family methods that are
not part of the baseline read/create/delete slice in
:mod:`voicebridge.gateway.openclaw_handlers.sessions`:

* ``sessions.goal.*`` — a per-session goal persisted in the canonical store's
  ``model_config`` extension column (the same column the vendored runtime uses
  for ``yolo_mode``/``browser_model_lock``);
* ``session.suggestions.*`` / ``session.typing`` — per-session suggestion and
  typing state, persisted the same way;
* ``sessions.assignOwner`` — the real single-operator ownership model (Qubit is
  single-user by product decision; this records the owning identity on the
  canonical session row instead of inventing a multi-user directory);
* ``sessions.pluginPatch`` — a bounded plugin metadata patch on the canonical
  session row;
* ``sessions.reset`` / ``sessions.rewind`` / ``sessions.cleanup`` — destructive
  transcript mutations through the vendored ``SessionDB`` primitives
  (``clear_messages`` / ``rewind_to_message`` / ``delete_empty_sessions``),
  confirmation- and e-stop-gated;
* ``sessions.groups.*`` — a Qubit-owned session-group catalog in the SAME
  ``state.db`` (one database authority);
* ``sessions.subscribe`` / ``sessions.messages.subscribe`` /
  ``sessions.messages.unsubscribe`` / ``sessions.viewers.set`` — the canonical
  in-process subscription/viewer registry
  (:mod:`voicebridge.session_subscriptions`). The upstream mechanism is
  server-push; Qubit keeps the real registry and serves it request-scoped, so
  these carry their own SAFE_EQUIVALENT typed codes.

Every handler delegates to the one canonical authority the CLI, dashboard,
``/qubit-chat``, and voice surfaces use. No second session store is created.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from ..gateway_ws import GatewayError

SA_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes: the real local subscription/viewer registry is
#: served request-scoped instead of upstream's server-push fan-out. The manifest
#: asserts each is emitted by its registered handler.
SUBSCRIPTIONS_LOCAL_REGISTRY = "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY"
VIEWERS_LOCAL_REGISTRY = "SESSIONS_VIEWERS_LOCAL_REGISTRY"

_MAX_SUGGESTIONS = 50


# ------------------------------------------------------------------ helpers
def _sa_param(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _sa_sid(params: Dict[str, Any]) -> str:
    sid = _sa_param(params, "sessionKey", "session_id", "sessionId", "key",
                    "id", "session")
    if not sid:
        raise GatewayError(SA_BAD_PARAMS, "session key is required")
    return sid[:80]


def _sa_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return default


def _sa_confirmed(params: Dict[str, Any]) -> bool:
    return _sa_bool(params.get("confirm", params.get("confirmed", False)))


def _sa_estop(cfg: Any) -> bool:
    from ... import broker as BR

    return bool(BR.estop_engaged(cfg))


def _sa_estop_refusal(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
            "detail": f"emergency stop latched; {operation} refused"}


def _sa_store(cfg: Any) -> Any:
    from ... import chat_transport as CT

    return CT.store(cfg)


# ------------------------------------------------------------------- goal
def _sa_goal_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session goal update")
    sid = _sa_sid(params)
    goal = _sa_param(params, "goal", "text", "value")
    if not goal:
        raise GatewayError(SA_BAD_PARAMS, "goal text is required")
    return _sa_store(cfg).meta_patch(
        sid, {"goal": goal[:4000], "goal_updated_at": time.time()})


def _sa_goal_clear(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session goal clear")
    sid = _sa_sid(params)
    return _sa_store(cfg).meta_patch(
        sid, {"goal": None, "goal_updated_at": None})


# ------------------------------------------------------------ suggestions
def _sa_suggestions_list(cfg: Any, params: Dict[str, Any]
                         ) -> Dict[str, Any]:
    sid = _sa_sid(params)
    res = _sa_store(cfg).meta_get(sid)
    if str(res.get("state")) != "OK":
        return res
    rows = res.get("meta", {}).get("suggestions") or []
    if not isinstance(rows, list):
        rows = []
    return {"state": "OK", "sessionId": sid, "suggestions": rows,
            "total": len(rows)}


def _sa_suggestions_add(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session suggestion add")
    sid = _sa_sid(params)
    text = _sa_param(params, "text", "suggestion", "prompt", "title")
    if not text:
        raise GatewayError(SA_BAD_PARAMS, "suggestion text is required")
    st = _sa_store(cfg)
    current = st.meta_get(sid)
    if str(current.get("state")) != "OK":
        return current
    rows = current.get("meta", {}).get("suggestions") or []
    rows = rows if isinstance(rows, list) else []
    entry = {"id": f"sug-{len(rows) + 1}-{int(time.time())}",
             "text": text[:500],
             "kind": _sa_param(params, "kind") or "suggestion",
             "status": "open", "createdAt": time.time()}
    rows = (rows + [entry])[-_MAX_SUGGESTIONS:]
    out = st.meta_patch(sid, {"suggestions": rows})
    if str(out.get("state")) != "OK":
        return out
    return {"state": "OK", "sessionId": sid, "suggestion": entry,
            "total": len(rows), "authority": out.get("authority")}


def _sa_suggestions_resolve(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session suggestion resolve")
    sid = _sa_sid(params)
    sug_id = _sa_param(params, "suggestionId", "suggestion_id", "id")
    status = (_sa_param(params, "status", "resolution") or "resolved").lower()
    if not sug_id:
        raise GatewayError(SA_BAD_PARAMS, "suggestionId is required")
    st = _sa_store(cfg)
    current = st.meta_get(sid)
    if str(current.get("state")) != "OK":
        return current
    rows = current.get("meta", {}).get("suggestions") or []
    rows = rows if isinstance(rows, list) else []
    matched = None
    for row in rows:
        if str(row.get("id")) == sug_id:
            row["status"] = status
            row["resolvedAt"] = time.time()
            matched = row
            break
    if matched is None:
        return {"state": "MISSING", "code": "SUGGESTION_NOT_FOUND",
                "detail": f"no suggestion {sug_id!r} in session {sid!r}"}
    out = st.meta_patch(sid, {"suggestions": rows})
    if str(out.get("state")) != "OK":
        return out
    return {"state": "OK", "sessionId": sid, "suggestion": matched,
            "authority": out.get("authority")}


# ---------------------------------------------------------------- typing
def _sa_session_typing(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    sid = _sa_sid(params)
    raw = params.get("typing", params.get("state", True))
    if isinstance(raw, str):
        active = raw.strip().lower() in ("1", "true", "yes", "on", "typing",
                                         "active")
    else:
        active = _sa_bool(raw, True)
    who = _sa_param(params, "who", "userId", "user", "sender") or "operator"
    return _sa_store(cfg).meta_patch(
        sid, {"typing": {"active": active, "who": who,
                         "at": time.time()}})


# ----------------------------------------------------------------- owner
def _sa_assign_owner(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session owner assignment")
    sid = _sa_sid(params)
    owner = _sa_param(params, "owner", "userId", "user_id", "identity")
    if not owner:
        # Single-operator Qubit: an explicit clear is allowed; default owner is
        # the local operator identity, never a fabricated multi-user id.
        owner = "operator"
    return _sa_store(cfg).meta_patch(sid, {"owner": owner[:120]})


# ----------------------------------------------------------- plugin patch
def _sa_plugin_patch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session plugin patch")
    sid = _sa_sid(params)
    patch = params.get("patch")
    if patch is None:
        patch = {k: v for k, v in params.items()
                 if k not in ("sessionKey", "session_id", "sessionId", "key",
                              "id", "session")}
    if not isinstance(patch, dict) or not patch:
        raise GatewayError(SA_BAD_PARAMS, "patch must be a non-empty object")
    clean = {str(k)[:64]: v for k, v in patch.items()
             if isinstance(v, (str, int, float, bool, list, dict))}
    if not clean:
        raise GatewayError(SA_BAD_PARAMS, "patch has no supported values")
    return _sa_store(cfg).meta_patch(sid, {"plugin": clean})


# --------------------------------------------------------------- mutations
def _sa_sessions_reset(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session reset")
    sid = _sa_sid(params)
    return _sa_store(cfg).reset(sid, confirm=_sa_confirmed(params))


def _sa_sessions_rewind(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session rewind")
    sid = _sa_sid(params)
    target = params.get("messageId", params.get("message_id",
                        params.get("targetMessageId")))
    return _sa_store(cfg).rewind(sid, target, confirm=_sa_confirmed(params))


def _sa_sessions_cleanup(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session cleanup")
    if not _sa_confirmed(params):
        return {"state": "CONFIRM_REQUIRED", "code": "CONFIRM_REQUIRED",
                "detail": "session cleanup deletes empty sessions; pass "
                          "confirm=true"}
    return _sa_store(cfg).cleanup_empty()


# ---------------------------------------------------------------- groups
def _sa_groups_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    st = _sa_store(cfg)
    listing = st.groups_list()
    defaults = st.group_defaults_get()
    return {"state": "OK", "groups": listing.get("groups", []),
            "total": listing.get("total", 0),
            "sectionOrder": defaults.get("sectionOrder") or [],
            "defaultGroupId": defaults.get("defaultGroupId"),
            "authority": listing.get("authority")}


def _sa_groups_defaults(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sa_store(cfg).group_defaults_get()


def _sa_groups_put(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session group put")
    names = params.get("names")
    if isinstance(names, dict):
        targets: List[Any] = list(names.items())
    elif isinstance(names, list):
        targets = [(str(n), {}) for n in names if str(n).strip()]
    else:
        one = _sa_param(params, "name", "id")
        if not one:
            raise GatewayError(SA_BAD_PARAMS, "names or name is required")
        targets = [(one, {})]
    st = _sa_store(cfg)
    out_groups = []
    for name, fields in targets:
        gid = str(name).strip()[:80]
        if not gid:
            continue
        fields = fields if isinstance(fields, dict) else {}
        res = st.group_put(gid, gid, fields)
        if str(res.get("state")) == "OK":
            out_groups.append(res["group"])
    section_order = params.get("sectionOrder")
    if isinstance(section_order, list):
        st.group_defaults_patch({"sectionOrder": [str(s)[:80]
                                                  for s in section_order]})
    return {"state": "OK", "ok": True, "groups": out_groups,
            "total": len(out_groups)}


def _sa_groups_rename(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session group rename")
    name = _sa_param(params, "name", "from", "id")
    to = _sa_param(params, "to", "rename", "newName")
    if not name or not to:
        raise GatewayError(SA_BAD_PARAMS, "name and to are required")
    return _sa_store(cfg).group_rename(name, to)


def _sa_groups_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session group update")
    name = _sa_param(params, "name", "id")
    if not name:
        raise GatewayError(SA_BAD_PARAMS, "name is required")
    fields = {k: params[k] for k in ("cwd", "worktree", "sectionOrder")
              if k in params}
    if not fields:
        raise GatewayError(SA_BAD_PARAMS, "no updatable group fields")
    return _sa_store(cfg).group_update(name, fields=fields)


def _sa_groups_delete(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if _sa_estop(cfg):
        return _sa_estop_refusal("session group delete")
    name = _sa_param(params, "name", "id")
    if not name:
        raise GatewayError(SA_BAD_PARAMS, "name is required")
    return _sa_store(cfg).group_delete(name)


# --------------------------------------------------------- subscriptions
def _sa_sessions_subscribe(cfg: Any, params: Dict[str, Any]
                           ) -> Dict[str, Any]:
    from ... import session_subscriptions as SS

    sid = _sa_sid(params)
    viewers = params.get("viewers")
    rec = SS.subscribe(sid, kind="session",
                       viewers=viewers if isinstance(viewers, list) else None,
                       cursor=params.get("cursor", 0))
    return {"state": "OK", "code": SUBSCRIPTIONS_LOCAL_REGISTRY,
            "subscription": rec, "active": SS.REGISTRY.active_count(),
            "qubit_counterpart": "voicebridge.session_subscriptions registry",
            "delivery": "request-scoped (read the registry on the next call)"}


def _sa_messages_subscribe(cfg: Any, params: Dict[str, Any]
                           ) -> Dict[str, Any]:
    from ... import session_subscriptions as SS

    sid = _sa_sid(params)
    rec = SS.subscribe(sid, kind="messages",
                       cursor=params.get("cursor", 0))
    return {"state": "OK", "code": SUBSCRIPTIONS_LOCAL_REGISTRY,
            "subscription": rec, "active": SS.REGISTRY.active_count(),
            "qubit_counterpart": "voicebridge.session_subscriptions registry",
            "delivery": "request-scoped (read the registry on the next call)"}


def _sa_messages_unsubscribe(cfg: Any, params: Dict[str, Any]
                             ) -> Dict[str, Any]:
    from ... import session_subscriptions as SS

    sub_id = _sa_param(params, "subscriptionId", "subscription_id", "id")
    if sub_id:
        removed = SS.unsubscribe(sub_id)
        if removed is None:
            return {"state": "MISSING", "code": "SUBSCRIPTION_NOT_FOUND",
                    "detail": f"no subscription {sub_id!r}"}
        return {"state": "OK", "code": SUBSCRIPTIONS_LOCAL_REGISTRY,
                "removed": removed, "active": SS.REGISTRY.active_count()}
    sid = _sa_sid(params)
    removed_n = SS.unsubscribe_session(sid, kind="messages")
    return {"state": "OK", "code": SUBSCRIPTIONS_LOCAL_REGISTRY,
            "removed": removed_n, "active": SS.REGISTRY.active_count()}


def _sa_viewers_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import session_subscriptions as SS

    sid = _sa_sid(params)
    viewers = params.get("viewers")
    if not isinstance(viewers, list):
        raise GatewayError(SA_BAD_PARAMS, "viewers must be a list")
    out = SS.set_viewers(sid, viewers)
    return {"state": "OK", "code": VIEWERS_LOCAL_REGISTRY,
            "sessionId": out["sessionId"], "viewers": out["viewers"],
            "active": SS.REGISTRY.active_count(),
            "qubit_counterpart": "voicebridge.session_subscriptions registry"}


# ----------------------------------------------------------------- registry
def register(registry: Any) -> None:
    """Register the advanced sessions-family handlers on *registry*."""
    registry.register("sessions.goal.update", _sa_goal_update)
    registry.register("sessions.goal.clear", _sa_goal_clear)
    registry.register("session.suggestions.list", _sa_suggestions_list)
    registry.register("session.suggestions.add", _sa_suggestions_add)
    registry.register("session.suggestions.resolve", _sa_suggestions_resolve)
    registry.register("session.typing", _sa_session_typing)
    registry.register("sessions.assignOwner", _sa_assign_owner)
    registry.register("sessions.pluginPatch", _sa_plugin_patch)
    registry.register("sessions.reset", _sa_sessions_reset)
    registry.register("sessions.rewind", _sa_sessions_rewind)
    registry.register("sessions.cleanup", _sa_sessions_cleanup)
    registry.register("sessions.groups.list", _sa_groups_list)
    registry.register("sessions.groups.defaults", _sa_groups_defaults)
    registry.register("sessions.groups.put", _sa_groups_put)
    registry.register("sessions.groups.rename", _sa_groups_rename)
    registry.register("sessions.groups.update", _sa_groups_update)
    registry.register("sessions.groups.delete", _sa_groups_delete)
    registry.register("sessions.subscribe", _sa_sessions_subscribe)
    registry.register("sessions.messages.subscribe", _sa_messages_subscribe)
    registry.register("sessions.messages.unsubscribe",
                      _sa_messages_unsubscribe)
    registry.register("sessions.viewers.set", _sa_viewers_set)
