"""OpenClaw contractual-pin surface-control methods.

Covers the pinned ``controlUi.*`` and ``tools.invoke`` gateway methods whose
real local counterpart already exists in a canonical Qubit module. No new
store, broker, or authority is created; every handler delegates to the same
module the CLI, dashboard, ``/qubit-chat``, and voice surfaces use.

* ``controlUi.sessionPreview`` — IMPLEMENTED: a real projection over the single
  canonical chat-session store (``QubitSessionStore.show``), the same store the
  dashboard Sessions page and ``sessions.get`` read.
* ``controlUi.sessionPullRequests.subscribe`` — SAFE_EQUIVALENT: the canonical
  in-process subscription registry (``voicebridge.session_subscriptions``);
  delivery is request-scoped, not upstream server-push, so the handler emits its
  own typed code.
* ``tools.invoke`` — IMPLEMENTED: executes through the canonical
  ``CapabilityBroker`` (e-stop, policy, shared confirmation journal, audit) —
  the same broker the CLI, dashboard, and voice ``_agent_tool`` path use.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

SURFACE_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed code: real local registry, request-scoped delivery.
CONTROLUI_SESSION_PR_SUBSCRIBE_LOCAL_REGISTRY = \
    "CONTROLUI_SESSION_PR_SUBSCRIBE_LOCAL_REGISTRY"


def _surface_str(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _surface_confirmed(params: Dict[str, Any]) -> str:
    """The upstream ``confirm`` flag as a confirmation id (or empty)."""
    value = params.get("confirm", params.get("confirmed", False))
    if value is True:
        return "gateway:tools.invoke"
    if isinstance(value, str) and value.strip().lower() in (
            "1", "true", "yes", "on", "confirm"):
        return "gateway:tools.invoke"
    return ""


def _controlui_session_preview(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    """Project one canonical chat session into the upstream preview shape."""
    sid = _surface_str(params, "sessionKey", "session_key", "key")
    if not sid:
        raise GatewayError(SURFACE_BAD_PARAMS,
                           "invalid controlUi.sessionPreview params")
    from ...qubit_session_store import QubitSessionStore

    try:
        out = QubitSessionStore(cfg).show(sid)
    except Exception:
        return {"status": "unavailable", "sessionKey": sid}
    if not isinstance(out, dict) or out.get("state") != "OK":
        return {"status": "unavailable", "sessionKey": sid}
    session = out.get("session") or {}
    messages = out.get("messages") or []
    last = ""
    if messages:
        tail = messages[-1] or {}
        last = str(tail.get("content") or "")[:200]
    title = session.get("title") or session.get("name")
    return {
        "status": "ok",
        "sessionKey": sid,
        "agentId": session.get("agent") or session.get("agent_id"),
        "title": title,
        "derivedTitle": title,
        "lastMessagePreview": last or None,
        "messageCount": int(out.get("message_count") or len(messages)),
        "authority": ("voicebridge.qubit_session_store.QubitSessionStore.show "
                      "(the one SQLite state.db authority)"),
    }


def _controlui_session_pr_subscribe(cfg: Any,
                                    params: Dict[str, Any]) -> Dict[str, Any]:
    """Register real request-scoped session subscriptions."""
    keys = params.get("sessionKeys")
    if keys is None:
        keys = params.get("session_keys")
    if not isinstance(keys, list):
        raise GatewayError(
            SURFACE_BAD_PARAMS,
            "invalid controlUi.sessionPullRequests.subscribe params")
    from ... import session_subscriptions as SS

    session_keys = [str(k)[:120] for k in keys if str(k).strip()]
    subs = [SS.REGISTRY.subscribe(k, kind="session") for k in session_keys]
    return {
        "subscribed": len(session_keys) > 0,
        "code": CONTROLUI_SESSION_PR_SUBSCRIBE_LOCAL_REGISTRY,
        "subscriptions": subs,
        "delivery": "request-scoped registry (no server-push lane)",
        "qubit_counterpart": ("voicebridge.session_subscriptions."
                              "SessionSubscriptionRegistry"),
        "authority": "voicebridge.session_subscriptions.REGISTRY",
    }


def _tools_invoke(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one capability through the canonical broker."""
    name = _surface_str(params, "name", "toolName", "tool")
    if not name:
        raise GatewayError(SURFACE_BAD_PARAMS,
                           "invalid tools.invoke params: name required")
    args = params.get("args")
    if not isinstance(args, dict):
        args = {}
    if params.get("confirm") not in (True, "true", "1", "yes", "on", "confirm"):
        # Upstream's non-confirm mode reports approval state; Qubit's broker
        # still denies a mutating capability without a confirmation id, so the
        # report path is the broker's own decision, not a fabricated approval.
        pass
    from ...broker import build_host_broker

    broker = build_host_broker(cfg=cfg)
    available = set(broker.registry.names())
    capability = name
    if capability not in available and f"local.{name}" in available:
        capability = f"local.{name}"
    res = broker.execute(
        capability, args, source="user",
        confirmation_id=(_surface_confirmed(params) or None),
        session=(_surface_str(params, "sessionKey", "session_key") or None),
        utterance=_surface_str(params, "utterance"))
    if res.ok:
        return {
            "ok": True,
            "toolName": capability,
            "output": res.output,
            "source": "qubit-broker",
            "correlationId": res.correlation_id,
        }
    if res.denied:
        return {
            "ok": False,
            "toolName": capability,
            "requiresApproval": True,
            "error": {"code": "forbidden", "message": str(res.denied)},
        }
    if res.cancelled:
        return {
            "ok": False,
            "toolName": capability,
            "error": {"code": "forbidden", "message": "cancelled"},
        }
    return {
        "ok": False,
        "toolName": capability,
        "error": {"code": "internal_error",
                  "message": res.error or "tool execution failed"},
    }


def register(registry: Any) -> None:
    """Register the surface-control handlers on *registry*."""
    registry.register("controlUi.sessionPreview",
                      _controlui_session_preview)
    registry.register("controlUi.sessionPullRequests.subscribe",
                      _controlui_session_pr_subscribe)
    registry.register("tools.invoke", _tools_invoke)
