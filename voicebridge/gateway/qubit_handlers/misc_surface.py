"""OpenClaw contractual-pin singleton / small families.

This module owns the remaining scattered pinned methods that do not belong to a
larger family: ``plugins.*`` session actions/descriptors, ``voicewake.*``,
``wizard.next``/``wizard.cancel``, ``assistant.media.get``, ``chat.toolTitles``,
``controlUi.githubPreview``, ``doctor.memory.resetGroundedShortTerm``,
``models.authOrderSet``, ``nativeHook.invoke``, ``progressCard.put``,
``ui.command`` and ``wake``. Every handler delegates to the SAME canonical
Qubit module the CLI, dashboard, ``/qubit-chat`` and voice surfaces already use,
or emits a typed refusal naming its real local counterpart / operator
prerequisite. No new store, broker, or authority is created.

Dispositions (each registered handler emits its declared typed code):

* ``plugins.sessionAction`` — SAFE_EQUIVALENT: real in-process plugin handler
  invocation through ``voicebridge.plugin_runtime.invoke`` (e-stop + shared
  confirmation journal); upstream invokes a plugin-declared *session* action,
  a different contract.
* ``plugins.uiDescriptors`` — SAFE_EQUIVALENT: the canonical plugin dashboard
  surface registry (``plugin_runtime.list_plugins`` +
  ``qubit_plugins.dashboard_surface``); upstream reads a plugin-declared
  Control-UI descriptor registry Qubit does not have.
* ``voicewake.set`` / ``voicewake.routing.get`` — OPERATOR_ONLY: Qubit has no
  writable wake-trigger or routing store; wake phrases come from the
  operator-fetched sherpa-onnx keyword bundle and route through the one audio
  listener.
* ``wizard.next`` / ``wizard.cancel`` — SAFE_EQUIVALENT: the canonical
  question-prompt store (``question_prompt.answer``/``pending``) and the
  stateless ``lifecycle.audio_wizard``; Qubit has no step-wizard session
  registry.
* ``assistant.media.get`` — SAFE_EQUIVALENT: Qubit serves no binary media data
  URLs by policy (``MEDIA_NOT_SERVED``); the real counterpart is bounded text
  file reads plus in-browser attachment vision.
* ``chat.toolTitles`` — SAFE_EQUIVALENT: utility-model tool titles are retired
  upstream; Qubit likewise has none and reports the canonical session's
  agent-provided tool activity.
* ``controlUi.githubPreview`` — OPERATOR_ONLY: needs a configured forge
  credential and a reachable forge API (``FORGE_API_OPERATOR_REQUIRED``).
* ``doctor.memory.resetGroundedShortTerm`` — SAFE_EQUIVALENT: the canonical
  markdown memory workspace (``memory.workspace.forget_note``/``list_notes``);
  Qubit has no grounded-short-term candidate artifact.
* ``models.authOrderSet`` — SAFE_EQUIVALENT: the canonical model-profile
  registry and ``model_profiles_ops.select_session_model``; Qubit has no
  multi-credential auth store to reorder.
* ``nativeHook.invoke`` — OPERATOR_ONLY: needs a live native harness process
  exposing a registered hook relay.
* ``progressCard.put`` — SAFE_EQUIVALENT: the read-only canonical session
  activity projection (``session_activity.build_projection``); Qubit keeps no
  writable card store.
* ``ui.command`` — SAFE_EQUIVALENT: the canonical control-panel state
  (``gateway.panels.control_panels``/``panel_layout``); Qubit has no
  client-push lane.
* ``wake`` — SAFE_EQUIVALENT: the canonical automation heartbeat/event queue
  (``automation.queue_event``/``heartbeat_due``).
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

MISC_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes (real local counterpart, different contract).
PLUGIN_SESSION_ACTION_LOCAL_INVOKE = "PLUGIN_SESSION_ACTION_LOCAL_INVOKE"
PLUGIN_UI_DESCRIPTORS_LOCAL_SURFACE = "PLUGIN_UI_DESCRIPTORS_LOCAL_SURFACE"
WIZARD_NEXT_LOCAL_QUESTION_STORE = "WIZARD_NEXT_LOCAL_QUESTION_STORE"
WIZARD_CANCEL_LOCAL_QUESTION_STORE = "WIZARD_CANCEL_LOCAL_QUESTION_STORE"
MEDIA_NOT_SERVED = "MEDIA_NOT_SERVED"
TOOL_TITLES_AGENT_PROVIDED = "TOOL_TITLES_AGENT_PROVIDED"
MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET = \
    "MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET"
AUTH_ORDER_LOCAL_PROFILE_PRIORITY = "AUTH_ORDER_LOCAL_PROFILE_PRIORITY"
PROGRESS_CARD_PUT_LOCAL_PROJECTION = "PROGRESS_CARD_PUT_LOCAL_PROJECTION"
UI_COMMAND_LOCAL_PANELS = "UI_COMMAND_LOCAL_PANELS"
WAKE_LOCAL_AUTOMATION = "WAKE_LOCAL_AUTOMATION"
#: OPERATOR_ONLY typed codes (external prerequisite absent).
WAKE_TRIGGERS_OPERATOR_REQUIRED = "WAKE_TRIGGERS_OPERATOR_REQUIRED"
WAKE_ROUTING_OPERATOR_REQUIRED = "WAKE_ROUTING_OPERATOR_REQUIRED"
FORGE_API_OPERATOR_REQUIRED = "FORGE_API_OPERATOR_REQUIRED"
NATIVE_HOOK_RELAY_OPERATOR_REQUIRED = "NATIVE_HOOK_RELAY_OPERATOR_REQUIRED"


def _misc_str(params: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = params.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _misc_confirmed(params: Dict[str, Any]) -> bool:
    value = params.get("confirm", params.get("confirmed", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return False


# --------------------------------------------------------------- plugins
def _misc_plugins_session_action(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a real in-process plugin handler through the canonical host."""
    plugin_id = _misc_str(params, "pluginId", "plugin_id", "plugin", "name")
    action_id = _misc_str(params, "actionId", "action_id", "action")
    if not plugin_id or not action_id:
        raise GatewayError(MISC_BAD_PARAMS,
                           "pluginId and actionId are required")
    payload = params.get("payload")
    if not isinstance(payload, dict):
        payload = params.get("args") if isinstance(params.get("args"),
                                                   dict) else {}
    from ... import plugin_runtime as PR

    out = PR.invoke(
        cfg, plugin_id, args=payload, confirmed=_misc_confirmed(params),
        confirmation_id=_misc_str(params, "confirmationId", "confirmation_id"),
        requested_by="gateway:plugins.sessionAction")
    return {"state": str(out.get("state", "OK")),
            "code": PLUGIN_SESSION_ACTION_LOCAL_INVOKE,
            "pluginId": plugin_id, "actionId": action_id,
            "result": out.get("result"), "plugin": out.get("plugin"),
            "qubit_counterpart": ("voicebridge.plugin_runtime.invoke "
                                  "(in-process plugin handler host)"),
            "authority": "voicebridge.plugin_runtime.invoke"}


def _misc_plugins_ui_descriptors(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    """Read the canonical plugin dashboard-surface registry."""
    from ... import plugin_runtime as PR
    from ... import qubit_plugins as QP

    listing = PR.list_plugins(cfg)
    rows = []
    for plugin in listing.get("plugins", []) or []:
        plugin_id = str(plugin.get("id", plugin.get("plugin_id", "")) or "")
        surface = QP.dashboard_surface(plugin_id) if plugin_id else None
        name = str(plugin.get("name", plugin_id) or plugin_id)
        rows.append({"id": plugin_id, "pluginId": plugin_id,
                     "pluginName": name, "label": name,
                     "surface": "dashboard" if surface else "none",
                     "descriptor": str(surface) if surface else None})
    return {"state": "OK", "ok": True,
            "code": PLUGIN_UI_DESCRIPTORS_LOCAL_SURFACE,
            "descriptors": rows, "count": len(rows),
            "detail": ("Qubit exposes plugin dashboard surfaces from the "
                       "canonical plugin runtime, not a plugin-declared "
                       "Control-UI descriptor registry."),
            "qubit_counterpart": ("voicebridge.qubit_plugins.dashboard_surface "
                                  "+ voicebridge.plugin_runtime.list_plugins"),
            "authority": "voicebridge.plugin_runtime.list_plugins"}


# --------------------------------------------------------------- voicewake
def _misc_voicewake_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": WAKE_TRIGGERS_OPERATOR_REQUIRED,
            "triggers": [],
            "detail": ("Qubit has no writable wake-trigger store; wake phrases "
                       "come from the operator-fetched sherpa-onnx keyword "
                       "bundle, never a gateway mutation."),
            "operator_prerequisite": ("a configurable wake-trigger store "
                                      "(writable keywords/model bundle)"),
            "qubit_counterpart": ("voicewake.get + voicebridge.wake.status "
                                  "(single audio listener)")}


def _misc_voicewake_routing_get(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": WAKE_ROUTING_OPERATOR_REQUIRED,
            "config": None,
            "detail": ("Qubit has no voice-wake routing configuration; wake "
                       "detection routes through the one audio listener."),
            "operator_prerequisite": "a configurable wake-trigger/routing store",
            "qubit_counterpart": ("voicewake.get + voicebridge.wake.status "
                                  "(single audio listener)")}


# ----------------------------------------------------------------- wizard
def _misc_wizard_next(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Advance the real question-prompt store (no step-wizard registry)."""
    from ... import question_prompt as QP

    prompt_id = _misc_str(params, "promptId", "prompt_id",
                          "sessionId", "session_id")
    if not prompt_id:
        raise GatewayError(MISC_BAD_PARAMS,
                           "sessionId or promptId is required")
    answer = params.get("answer")
    resolved = None
    if answer is not None:
        answers = answer.get("value") if isinstance(answer, dict) else answer
        resolved = QP.answer(cfg, prompt_id, answers)
    pending = QP.pending(cfg)
    items = pending.get("pending", []) or []
    return {"state": "OK", "code": WIZARD_NEXT_LOCAL_QUESTION_STORE,
            "sessionId": prompt_id, "answer": resolved,
            "pending": items, "count": len(items),
            "detail": ("Qubit has no step-wizard session registry; the real "
                       "counterpart is the canonical question-prompt store, "
                       "which advances by answering a pending prompt."),
            "qubit_counterpart": ("voicebridge.question_prompt.answer + "
                                  "question_prompt.pending"),
            "authority": "voicebridge.question_prompt"}


def _misc_wizard_cancel(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import question_prompt as QP
    from ...lifecycle import audio_wizard

    session_id = _misc_str(params, "sessionId", "session_id",
                           "promptId", "prompt_id")
    if not session_id:
        raise GatewayError(MISC_BAD_PARAMS, "sessionId is required")
    pending = QP.pending(cfg)
    wizard = audio_wizard(cfg)
    return {"state": "UNAVAILABLE",
            "code": WIZARD_CANCEL_LOCAL_QUESTION_STORE,
            "sessionId": session_id, "cancelled": False,
            "pending": pending.get("pending", []) or [],
            "wizard_state": str(wizard.get("state", "")),
            "detail": ("Qubit has no step-wizard session registry to cancel; "
                       "a pending prompt is settled through question.resolve "
                       "and the audio wizard is a stateless readiness report."),
            "qubit_counterpart": ("voicebridge.question_prompt + "
                                  "voicebridge.lifecycle.audio_wizard"),
            "authority": ("voicebridge.question_prompt.pending + "
                          "voicebridge.lifecycle.audio_wizard")}


# ----------------------------------------------------------- media / titles
def _misc_assistant_media_get(cfg: Any,
                              params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": MEDIA_NOT_SERVED,
            "sessionKey": _misc_str(params, "sessionKey", "session_key") or None,
            "mediaId": _misc_str(params, "mediaId", "media_id", "id") or None,
            "detail": ("Gateway binary media data URLs are not served through "
                       "the JSON API; Qubit file reads return bounded, redacted "
                       "text and Qubit Chat renders attachment images "
                       "in-browser."),
            "qubit_counterpart": ("CLI `qubit files read` + Qubit Chat "
                                  "attachment vision"),
            "authority": "voicebridge.qubit_compat._NO_MEDIA (policy refusal)"}


def _misc_chat_tool_titles(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Return the retired disabled shape plus the real session tool activity."""
    session_key = _misc_str(params, "sessionKey", "session_key", "session")
    tool_names = []
    message_count = 0
    if session_key:
        from ...qubit_session_store import QubitSessionStore

        out = QubitSessionStore(cfg).messages(session_key, limit=200)
        messages = out.get("messages", []) if isinstance(out, dict) else []
        message_count = len(messages)
        for message in messages:
            name = message.get("tool_name") if isinstance(message, dict) else None
            if name:
                tool_names.append(str(name))
    return {"titles": {}, "disabled": True,
            "code": TOOL_TITLES_AGENT_PROVIDED,
            "sessionKey": session_key or None,
            "toolNames": tool_names[:50], "messageCount": message_count,
            "detail": ("Utility-model tool titles are retired upstream and in "
                       "Qubit; tool activity descriptions are agent-provided "
                       "in the canonical session runtime."),
            "qubit_counterpart": ("voicebridge.qubit_session_store "
                                  "(agent-provided tool activity)"),
            "authority": ("voicebridge.qubit_session_store."
                          "QubitSessionStore.messages")}


# ------------------------------------------------------------- control ui
def _misc_controlui_github_preview(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": FORGE_API_OPERATOR_REQUIRED,
            "detail": ("Remote GitHub preview requires a configured forge "
                       "credential and a network-reachable forge API; Qubit "
                       "stores no forge token by default."),
            "operator_prerequisite": ("a configured forge credential (e.g. a "
                                      "GitHub/GitLab token) and a "
                                      "network-reachable forge API"),
            "qubit_counterpart": ("local change review: CLI `qubit git "
                                  "status|log|diff|show` and the dashboard git "
                                  "review surface")}


# ----------------------------------------------------------- doctor memory
def _misc_doctor_memory_reset_grounded_short_term(
        cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Read or reset canonical markdown memory (no grounded candidate store)."""
    from ...memory import workspace as MW

    name = _misc_str(params, "name", "note")
    if name:
        out = MW.forget_note(
            cfg, name,
            requested_by=_misc_str(params, "requested_by", default="gateway"),
            confirmed=_misc_confirmed(params))
        return {**out, "code": MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET,
                "qubit_counterpart": "voicebridge.memory.workspace.forget_note",
                "authority": "voicebridge.memory.workspace.forget_note"}
    notes = MW.list_notes(cfg)
    providers = MW.providers()
    return {"state": "OK", "code": MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET,
            "notes": notes.get("notes", []), "count": notes.get("count", 0),
            "providers": providers.get("providers", []),
            "detail": ("Qubit's canonical memory workspace is markdown-note "
                       "based and has no grounded-short-term candidate store; "
                       "reset a note through forget_note (`doctor.memory."
                       "resetDreamDiary` or `qubit memory forget <name>`)."),
            "qubit_counterpart": "voicebridge.memory.workspace.forget_note",
            "authority": "voicebridge.memory.workspace"}


# ---------------------------------------------------------------- models
def _misc_models_auth_order_set(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    """Report the canonical model-profile ordering (no credential store)."""
    from ... import model_profiles_ops as MPO

    provider = _misc_str(params, "provider")
    listing = MPO.list_model_profiles(cfg)
    profiles = listing.get("profiles", []) if isinstance(listing, dict) else []
    if provider:
        profiles = [p for p in profiles
                    if str(p.get("provider", "")) == provider]
    order = [str(p.get("id", "")) for p in profiles]
    return {"state": "OK", "code": AUTH_ORDER_LOCAL_PROFILE_PRIORITY,
            "provider": provider or None, "profileIds": order,
            "profiles": profiles,
            "detail": ("Qubit has no multi-credential auth store to reorder; "
                       "provider selection is by canonical model profile "
                       "(select_session_model) with its role/fallback chain."),
            "qubit_counterpart": ("voicebridge.model_profiles_ops."
                                  "select_session_model"),
            "authority": ("voicebridge.model_profiles_ops."
                          "list_model_profiles")}


# ----------------------------------------------------------- native hook
def _misc_native_hook_invoke(cfg: Any,
                             params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": NATIVE_HOOK_RELAY_OPERATOR_REQUIRED,
            "detail": ("native hook relay invocation needs a live native "
                       "harness process with a registered relay; Qubit runs "
                       "no native harness relay."),
            "operator_prerequisite": ("a live native harness process exposing "
                                      "a registered hook relay "
                                      "(provider/relayId/generation)"),
            "qubit_counterpart": ("voicebridge.hooks (canonical hook "
                                  "declarations) + voicebridge.node_host")}


# --------------------------------------------------------- progress card
def _misc_progress_card_put(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Build the read-only canonical session activity projection."""
    from ... import session_activity as SA
    from ...qubit_session_store import QubitSessionStore

    session_key = _misc_str(params, "sessionKey", "session_key",
                            "sessionId", "session_id")
    if not session_key:
        raise GatewayError(MISC_BAD_PARAMS, "sessionKey is required")
    projection = SA.build_projection(cfg, session_key,
                                     session_store=QubitSessionStore(cfg))
    return {"state": str(projection.get("state", "OK")),
            "code": PROGRESS_CARD_PUT_LOCAL_PROJECTION,
            "sessionKey": session_key, "projection": projection,
            "detail": ("upstream writes a mutable progress card; Qubit builds "
                       "the real read-only session activity projection."),
            "qubit_counterpart": ("voicebridge.session_activity."
                                  "build_projection"),
            "authority": "voicebridge.session_activity.build_projection"}


# --------------------------------------------------------------- ui / wake
def _misc_ui_command(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Read canonical control-panel state (no client-push lane)."""
    if not params.get("command"):
        raise GatewayError(MISC_BAD_PARAMS, "command is required")
    from .. import panels as P

    panels = P.control_panels(cfg)
    layout = P.panel_layout(cfg)
    return {"state": "UNAVAILABLE", "ok": False,
            "code": UI_COMMAND_LOCAL_PANELS,
            "panels": panels.get("panels", {}),
            "layout": layout.get("layout", "tabs"),
            "arranged": bool(layout.get("arranged", False)),
            "detail": ("Qubit has no client-push lane; the canonical control "
                       "panel state is applied through the dashboard, not "
                       "broadcast to UI clients."),
            "qubit_counterpart": ("voicebridge.gateway.panels.control_panels + "
                                  "panel_layout"),
            "authority": "voicebridge.gateway.panels"}


def _misc_wake(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Queue a wake as a canonical internal automation event."""
    from ... import automation as AU

    text = _misc_str(params, "text", "message", "prompt")
    if not text:
        raise GatewayError(MISC_BAD_PARAMS, "text is required")
    mode = _misc_str(params, "mode", default="now").lower()
    if mode not in ("now", "next-heartbeat"):
        raise GatewayError(MISC_BAD_PARAMS,
                           "mode must be 'now' or 'next-heartbeat'")
    event = AU.queue_event(
        cfg, kind="internal", source="gateway:wake", text=text,
        requested_by=_misc_str(params, "requested_by", default="gateway"),
        confirmed=_misc_confirmed(params))
    return {"state": str(event.get("state", "OK")), "code": WAKE_LOCAL_AUTOMATION,
            "mode": mode, "event": event.get("event"),
            "due_heartbeats": AU.heartbeat_due(cfg),
            "qubit_counterpart": ("voicebridge.automation.queue_event + "
                                  "heartbeat_due (automation tick)"),
            "authority": "voicebridge.automation.queue_event"}


def register(registry: Any) -> None:
    """Register the singleton/small pinned handlers on *registry*."""
    registry.register("plugins.sessionAction", _misc_plugins_session_action)
    registry.register("plugins.uiDescriptors", _misc_plugins_ui_descriptors)
    registry.register("voicewake.set", _misc_voicewake_set)
    registry.register("voicewake.routing.get", _misc_voicewake_routing_get)
    registry.register("wizard.next", _misc_wizard_next)
    registry.register("wizard.cancel", _misc_wizard_cancel)
    registry.register("assistant.media.get", _misc_assistant_media_get)
    registry.register("chat.toolTitles", _misc_chat_tool_titles)
    registry.register("controlUi.githubPreview",
                      _misc_controlui_github_preview)
    registry.register("doctor.memory.resetGroundedShortTerm",
                      _misc_doctor_memory_reset_grounded_short_term)
    registry.register("models.authOrderSet", _misc_models_auth_order_set)
    registry.register("nativeHook.invoke", _misc_native_hook_invoke)
    registry.register("progressCard.put", _misc_progress_card_put)
    registry.register("ui.command", _misc_ui_command)
    registry.register("wake", _misc_wake)
