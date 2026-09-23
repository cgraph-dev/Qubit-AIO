"""OpenClaw contractual-pin ``talk.*`` and ``node.*`` families.

Qubit removed the realtime/duplex voice lane. ``voicebridge/orchestrator.py``
still resolves and logs the canonical realtime speech profile so an operator
selection is observable, but it starts no realtime adapter and opens no
provider transport. Qubit's canonical voice path is the local
mic -> ASR -> classify -> route -> worker -> speak pipeline
(``voicebridge/orchestrator.py``) whose agent turns run through
``voicebridge.chat_transport`` (``stream_turn`` / ``turn``) over the one
``QubitSessionStore``. There is no Talk session registry, no client-owned
WebRTC/provider session, and no server-push ``talk.event`` channel.

Qubit's canonical paired-device authority is ``voicebridge/node_host.py``
(enroll/revoke/show/dispatch). It runs no paired node agent relay: there is no
pending-work queue, no async invoke-result/progress registry, no node
skill/plugin-tool catalog, and no runner-inventory push, so those node methods
carry a typed refusal naming the missing relay.

Each pinned method resolves honestly:

* ``talk.mode`` — SAFE_EQUIVALENT: reports the real canonical speech/voice mode
  (``voicebridge.providers.speech`` profiles + resolved realtime profile); Qubit
  keeps no Talk mode state to set or broadcast.
* ``talk.client.steer`` — SAFE_EQUIVALENT: Qubit has no mid-turn steering
  channel, so the spoken control is appended to the canonical chat session for
  the next turn (``QubitSessionStore``).
* ``talk.client.transcript`` — SAFE_EQUIVALENT: appends the finalized
  ``{role, text}`` to the one canonical chat session store; there is no
  per-voiceSession entry-id dedupe.
* ``talk.client.toolCall`` — SAFE_EQUIVALENT: executes through the canonical
  ``CapabilityBroker`` (e-stop, policy, shared confirmation journal, audit)
  instead of a client-owned provider tool-forwarding round trip.
* ``talk.client.create`` / ``talk.client.close`` and the whole ``talk.session.*``
  family — OPERATOR_ONLY: they require a live provider realtime/relay transport
  Qubit does not run.
* ``node.rename`` — SAFE_EQUIVALENT: the node host records a label only at
  enroll time, so this revokes and re-enrolls the same endpoint/capabilities
  under the new label (a new node id).
* ``node.event`` / ``node.invoke.progress`` / ``node.invoke.result`` /
  ``node.pending.*`` / ``node.pluginTools.update`` /
  ``node.runnerInventory.update`` / ``node.skills.update`` — OPERATOR_ONLY: they
  need a paired node device/agent host running the native-harness relay.

The emergency stop, confirmation journal, and node gates are the same durable
authorities the CLI, dashboard, voice, and broker observe (fail closed).
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

TALK_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes (real local counterpart; delivery/contract
#: differs from the removed realtime lane).
TALK_MODE_LOCAL_SPEECH_PROFILE = "TALK_MODE_LOCAL_SPEECH_PROFILE"
TALK_CLIENT_STEER_LOCAL_TRANSCRIPT = "TALK_CLIENT_STEER_LOCAL_TRANSCRIPT"
TALK_CLIENT_TOOLCALL_LOCAL_BROKER = "TALK_CLIENT_TOOLCALL_LOCAL_BROKER"
TALK_CLIENT_TRANSCRIPT_LOCAL_STORE = "TALK_CLIENT_TRANSCRIPT_LOCAL_STORE"
NODE_RENAME_LOCAL_REENROLL = "NODE_RENAME_LOCAL_REENROLL"

#: OPERATOR_ONLY typed codes (missing live provider transport / node relay).
TALK_REALTIME_OPERATOR_REQUIRED = "TALK_REALTIME_OPERATOR_REQUIRED"
NODE_DEVICE_RELAY_OPERATOR_REQUIRED = "NODE_DEVICE_RELAY_OPERATOR_REQUIRED"

_TALK_REALTIME_PREREQUISITE = ("an OpenAI Realtime (or equivalent) provider "
                               "transport, which Qubit does not run")
_TALK_LOCAL_COUNTERPART = ("voicebridge.chat_transport.stream_turn/turn over "
                           "the QubitSessionStore, plus the /qubit-chat voice "
                           "path (mic -> ASR -> route -> worker -> speak, "
                           "voicebridge/orchestrator.py)")
_NODE_RELAY_PREREQUISITE = ("a paired node device/agent host running the "
                            "native-harness relay (not part of the single "
                            "local Qubit authority)")
_NODE_LOCAL_COUNTERPART = ("voicebridge.node_host (enroll/revoke/show/"
                           "dispatch) + `qubit node ...`")


def _talk_str(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _talk_require(params: Dict[str, Any], *keys: str) -> str:
    out = _talk_str(params, *keys)
    if not out:
        raise GatewayError(TALK_BAD_PARAMS,
                           f"one of {', '.join(keys)} is required")
    return out


def _talk_confirmed(params: Dict[str, Any]) -> bool:
    value = params.get("confirm", params.get("confirmed", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return False


def _talk_session_id(params: Dict[str, Any]) -> str:
    return _talk_str(params, "sessionKey", "session_key", "session",
                     "sessionId", "session_id")


def _talk_realtime_refusal(operation: str) -> Dict[str, Any]:
    """Typed OPERATOR_ONLY refusal for the removed realtime lane.

    Performs no backend call: the operation cannot run because Qubit starts no
    realtime adapter, so no success is fabricated.
    """
    return {"state": "UNAVAILABLE",
            "code": TALK_REALTIME_OPERATOR_REQUIRED,
            "operation": operation,
            "detail": ("Qubit removed the realtime/duplex lane; the realtime "
                       "profile is resolved read-only and no adapter or "
                       "provider transport is started, so no Talk session was "
                       "created, mutated, or closed"),
            "operator_prerequisite": _TALK_REALTIME_PREREQUISITE,
            "qubit_counterpart": _TALK_LOCAL_COUNTERPART}


def _node_relay_refusal(operation: str) -> Dict[str, Any]:
    """Typed OPERATOR_ONLY refusal for the absent paired-node relay.

    Performs no backend call: Qubit's node host is enroll/revoke/show/dispatch
    only, so no pending work, invoke result, event, or catalog is fabricated.
    """
    return {"state": "UNAVAILABLE",
            "code": NODE_DEVICE_RELAY_OPERATOR_REQUIRED,
            "operation": operation,
            "detail": ("Qubit's canonical device host (voicebridge.node_host) "
                       "enrolls/revokes/shows/dispatches only; it runs no "
                       "paired node agent relay, pending-work queue, async "
                       "invoke-result registry, node skill/plugin-tool catalog, "
                       "or runner-inventory push, so nothing was fabricated"),
            "operator_prerequisite": _NODE_RELAY_PREREQUISITE,
            "qubit_counterpart": _NODE_LOCAL_COUNTERPART}


# --------------------------------------------------------------- talk: mode
def _talk_mode(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Report the real canonical speech/voice mode (read-only)."""
    from ...providers import speech as SP

    realtime = SP.resolve_speech_dict(cfg, SP.REALTIME)
    requested = _talk_str(params, "mode")
    return {"state": "OK",
            "code": TALK_MODE_LOCAL_SPEECH_PROFILE,
            "requestedMode": requested or None,
            "effectiveMode": "local-stt-tts",
            "realtime": realtime,
            "profiles": SP.list_speech_profiles(cfg),
            "detail": ("Qubit keeps no Talk mode state to set or broadcast; it "
                       "reports the real local speech profile resolution and "
                       "the /qubit-chat voice path"),
            "qubit_counterpart": ("voicebridge.providers.speech."
                                  "resolve_speech_dict/list_speech_profiles + "
                                  "the /qubit-chat voice path"),
            "authority": "voicebridge.providers.speech"}


# ------------------------------------------------------- talk: client steer
def _talk_client_steer(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Append a spoken steer to the canonical session for the next turn."""
    sid = _talk_require(params, "sessionKey", "session_key", "session",
                        "sessionId", "session_id")
    text = _talk_require(params, "text", "utterance")
    mode = _talk_str(params, "mode")
    from ...qubit_session_store import QubitSessionStore

    store = QubitSessionStore(cfg)
    store.ensure(sid)
    appended = store.append(sid, "user", text)
    if not isinstance(appended, dict) or appended.get("state") != "OK":
        return {"state": "UNAVAILABLE",
                "code": TALK_CLIENT_STEER_LOCAL_TRANSCRIPT,
                "sessionKey": sid,
                "detail": "cannot append to the canonical chat session",
                "qubit_counterpart": _TALK_LOCAL_COUNTERPART}
    return {"state": "OK",
            "code": TALK_CLIENT_STEER_LOCAL_TRANSCRIPT,
            "sessionKey": sid,
            "mode": mode or "steer",
            "recorded": True,
            "detail": ("Qubit has no mid-turn steering channel; the spoken "
                       "control is recorded on the canonical session for the "
                       "next turn"),
            "qubit_counterpart": _TALK_LOCAL_COUNTERPART,
            "authority": ("voicebridge.qubit_session_store.QubitSessionStore."
                          "append")}


# -------------------------------------------------- talk: client transcript
def _talk_client_transcript(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Append one finalized transcript item to the canonical session."""
    sid = _talk_require(params, "sessionKey", "session_key", "session",
                        "sessionId", "session_id")
    text = _talk_require(params, "text", "content")
    role = _talk_str(params, "role") or "user"
    entry_id = _talk_str(params, "entryId", "entry_id")
    from ...qubit_session_store import QubitSessionStore

    store = QubitSessionStore(cfg)
    store.ensure(sid)
    appended = store.append(sid, role, text)
    if not isinstance(appended, dict) or appended.get("state") != "OK":
        return {"state": "UNAVAILABLE",
                "code": TALK_CLIENT_TRANSCRIPT_LOCAL_STORE,
                "sessionKey": sid,
                "detail": "cannot append to the canonical chat session",
                "qubit_counterpart": _TALK_LOCAL_COUNTERPART}
    return {"state": "OK",
            "code": TALK_CLIENT_TRANSCRIPT_LOCAL_STORE,
            "sessionKey": sid,
            "role": role,
            "entryId": entry_id or None,
            "message": appended.get("message"),
            "detail": ("appended to the one canonical chat-session store; Qubit "
                       "has no per-voiceSession entry-id dedupe"),
            "qubit_counterpart": ("voicebridge.qubit_session_store."
                                  "QubitSessionStore.append"),
            "authority": ("voicebridge.qubit_session_store.QubitSessionStore."
                          "append")}


# ------------------------------------------------- talk: client toolCall
def _talk_client_tool_call(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run one provider tool call through the canonical broker."""
    name = _talk_require(params, "toolName", "tool", "name", "capability")
    args = params.get("args")
    if not isinstance(args, dict):
        args = {}
    from ...broker import build_host_broker

    broker = build_host_broker(cfg=cfg)
    available = set(broker.registry.names())
    capability = name
    if capability not in available and f"local.{name}" in available:
        capability = f"local.{name}"
    confirmation_id = _talk_str(params, "confirmationId", "confirmation_id")
    res = broker.execute(
        capability, args, source="model",
        confirmation_id=(confirmation_id or None),
        session=(_talk_session_id(params) or None),
        utterance=_talk_str(params, "utterance"))
    common = {
        "code": TALK_CLIENT_TOOLCALL_LOCAL_BROKER,
        "toolName": capability,
        "ok": bool(res.ok),
        "qubit_counterpart": ("voicebridge.broker.CapabilityBroker.execute "
                              "(e-stop + policy + shared confirmation journal)"),
        "authority": "voicebridge.broker.build_host_broker",
    }
    if res.ok:
        return {**common, "state": "OK", "output": res.output,
                "correlationId": res.correlation_id}
    if res.denied:
        return {**common, "state": "UNAVAILABLE", "denied": str(res.denied),
                "detail": "broker denied the capability (no fabricated success)"}
    if res.cancelled:
        return {**common, "state": "UNAVAILABLE", "cancelled": True,
                "detail": "broker cancelled the capability"}
    return {**common, "state": "UNAVAILABLE",
            "detail": res.error or "capability execution failed"}


# ------------------------------------------------------- node: rename
def _node_rename(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Re-register an enrolled node under a new label.

    ``voicebridge.node_host`` records a label only at enroll time and exposes no
    in-place rename, so the canonical revoke + enroll primitives are used with
    an explicit confirmation; the node keeps its endpoint and capabilities but
    receives a new node id.
    """
    node_id = _talk_require(params, "nodeId", "node_id", "id")
    name = _talk_str(params, "name", "label", "displayName", "display_name")
    if not name:
        raise GatewayError(TALK_BAD_PARAMS,
                           "node.rename requires a non-empty 'name' label")
    from ... import node_host as NH

    current = NH.show(cfg, node_id)
    if not isinstance(current, dict) or current.get("state") != "OK":
        return {"state": "UNKNOWN", "code": NODE_RENAME_LOCAL_REENROLL,
                "node_id": node_id,
                "detail": "no such enrolled node; nothing renamed",
                "qubit_counterpart": _NODE_LOCAL_COUNTERPART}
    if not _talk_confirmed(params):
        return {"state": "CONFIRM_REQUIRED",
                "code": NODE_RENAME_LOCAL_REENROLL,
                "node_id": node_id, "name": name,
                "detail": ("renaming re-registers the node and needs an "
                           "explicit confirmation"),
                "qubit_counterpart": _NODE_LOCAL_COUNTERPART}
    record = current.get("node") or {}
    endpoint = str(record.get("endpoint") or "").strip()
    if not endpoint:
        return {"state": "UNAVAILABLE", "code": NODE_RENAME_LOCAL_REENROLL,
                "node_id": node_id,
                "detail": "enrolled node has no endpoint to re-register",
                "qubit_counterpart": _NODE_LOCAL_COUNTERPART}
    revoked = NH.revoke(cfg, node_id, confirmed=True, source="cli",
                        requested_by="gateway")
    if not isinstance(revoked, dict) or revoked.get("state") != "OK":
        return {**({} if not isinstance(revoked, dict) else revoked),
                "code": NODE_RENAME_LOCAL_REENROLL,
                "node_id": node_id,
                "detail": "could not re-register the node; nothing renamed",
                "qubit_counterpart": _NODE_LOCAL_COUNTERPART}
    enrolled = NH.enroll(
        cfg, name=name, endpoint=endpoint,
        capabilities=record.get("capabilities"),
        confirmed=True, source="cli", requested_by="gateway")
    return {**enrolled,
            "code": NODE_RENAME_LOCAL_REENROLL,
            "renamed": bool(isinstance(enrolled, dict)
                            and enrolled.get("state") == "OK"),
            "previousNodeId": node_id,
            "detail": ("Qubit's node host records the label at enroll time; "
                       "the node was re-registered under the new label with a "
                       "new node id"),
            "qubit_counterpart": ("voicebridge.node_host.revoke + "
                                  "voicebridge.node_host.enroll"),
            "authority": "voicebridge.node_host"}


# --------------------------------------------- talk: realtime OPERATOR_ONLY
def _talk_client_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.client.create")


def _talk_client_close(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.client.close")


def _talk_session_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.session.create")


def _talk_session_append_audio(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.session.appendAudio")


def _talk_session_cancel_output(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.session.cancelOutput")


def _talk_session_close(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.session.close")


def _talk_session_acknowledge_mark(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.session.acknowledgeMark")


def _talk_session_submit_tool_result(cfg: Any,
                                     params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.session.submitToolResult")


def _talk_session_steer(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _talk_realtime_refusal("talk.session.steer")


# ----------------------------------------------- node: relay OPERATOR_ONLY
def _node_event(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.event")


def _node_invoke_progress(cfg: Any,
                          params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.invoke.progress")


def _node_invoke_result(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.invoke.result")


def _node_pending_ack(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.pending.ack")


def _node_pending_drain(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.pending.drain")


def _node_pending_enqueue(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.pending.enqueue")


def _node_pending_pull(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.pending.pull")


def _node_plugin_tools_update(cfg: Any,
                              params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.pluginTools.update")


def _node_runner_inventory_update(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.runnerInventory.update")


def _node_skills_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _node_relay_refusal("node.skills.update")


def register(registry: Any) -> None:
    """Register the ``talk.*`` / ``node.*`` handlers on *registry*."""
    # talk — SAFE_EQUIVALENT local counterparts
    registry.register("talk.mode", _talk_mode)
    registry.register("talk.client.steer", _talk_client_steer)
    registry.register("talk.client.transcript", _talk_client_transcript)
    registry.register("talk.client.toolCall", _talk_client_tool_call)
    # talk — OPERATOR_ONLY (removed realtime lane)
    registry.register("talk.client.create", _talk_client_create)
    registry.register("talk.client.close", _talk_client_close)
    registry.register("talk.session.create", _talk_session_create)
    registry.register("talk.session.appendAudio", _talk_session_append_audio)
    registry.register("talk.session.cancelOutput",
                      _talk_session_cancel_output)
    registry.register("talk.session.close", _talk_session_close)
    registry.register("talk.session.acknowledgeMark",
                      _talk_session_acknowledge_mark)
    registry.register("talk.session.submitToolResult",
                      _talk_session_submit_tool_result)
    registry.register("talk.session.steer", _talk_session_steer)
    # node — SAFE_EQUIVALENT local re-registration
    registry.register("node.rename", _node_rename)
    # node — OPERATOR_ONLY (absent paired-node relay)
    registry.register("node.event", _node_event)
    registry.register("node.invoke.progress", _node_invoke_progress)
    registry.register("node.invoke.result", _node_invoke_result)
    registry.register("node.pending.ack", _node_pending_ack)
    registry.register("node.pending.drain", _node_pending_drain)
    registry.register("node.pending.enqueue", _node_pending_enqueue)
    registry.register("node.pending.pull", _node_pending_pull)
    registry.register("node.pluginTools.update", _node_plugin_tools_update)
    registry.register("node.runnerInventory.update",
                      _node_runner_inventory_update)
    registry.register("node.skills.update", _node_skills_update)
