"""OpenClaw contractual-pin scheduler / media / task-control families.

Covers the pinned ``tts.*``, ``cron.*``, ``gateway.suspend.*``, ``portal.*``,
``taskSuggestions.*``, ``tasks.*`` and ``mentions.*`` gateway methods. Each
handler resolves to a real canonical Qubit module where one exists and otherwise
returns a typed refusal naming the exact prerequisite and the local path used
instead. No second scheduler, speech registry, session store, or mention inbox
is created.

* ``tts.setProvider`` — IMPLEMENTED: persists ``tts.provider`` through the
  canonical confirm-gated settings write (``qubit_compat.config_save``), the
  exact setting ``voicebridge/orchestrator._make_tts`` reads to build the TTS
  adapter.
* ``tts.enable`` / ``tts.disable`` / ``tts.setPersona`` — SAFE_EQUIVALENT:
  Qubit has no global TTS auto-mode flag or persona-bundle store; the real
  per-speech-out-profile ``enabled`` flag and session selection
  (``voicebridge.providers.speech``) are surfaced instead.
* ``cron.update`` — SAFE_EQUIVALENT: the canonical schedule store supports only
  the ``enabled`` (pause/resume) subset of an upstream patch; the real
  ``schedules.set_paused`` mutation is applied and the own code emitted.
* ``cron.scratch.get`` / ``cron.scratch.set`` — OPERATOR_ONLY: Qubit's schedule
  store has no private per-job scratch column and no scratch store exists.
* ``gateway.suspend.prepare`` / ``gateway.suspend.resume`` — SAFE_EQUIVALENT:
  the real local power/e-stop state (``voicebridge.power.PowerControl``) is
  reported; Qubit does not drain in-flight work or suspend its process.
* ``gateway.suspend.handoff`` — OPERATOR_ONLY: needs a service manager that owns
  process exit (systemd/launchd external restart owner).
* ``portal.list`` / ``portal.open`` / ``portal.close`` — OPERATOR_ONLY: no
  local-port portal HTTP-proxy service exists.
* ``taskSuggestions.create`` / ``taskSuggestions.accept`` /
  ``taskSuggestions.dismiss`` — OPERATOR_ONLY: no ephemeral suggestion registry.
* ``tasks.cancel`` / ``tasks.retry`` — SAFE_EQUIVALENT: the real canonical
  run-control primitives (``actions.ActionStore.cancel`` / ``retry`` and
  ``chat_transport.cancel`` / ``retry_last``) are used; Qubit's task ledger is
  read-only and maps no taskId to a live run.
* ``tasks.dismiss`` — OPERATOR_ONLY: Qubit keeps no subagent completion-delivery
  registry.
* ``mentions.list`` / ``mentions.dismiss`` — OPERATOR_ONLY: Qubit runs no
  human-mention inbox service.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

_BAD = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes (real local counterpart, different contract).
TTS_ENABLE_LOCAL_PROFILE = "TTS_ENABLE_LOCAL_PROFILE"
TTS_DISABLE_LOCAL_PROFILE = "TTS_DISABLE_LOCAL_PROFILE"
TTS_PERSONA_LOCAL_PROFILE = "TTS_PERSONA_LOCAL_PROFILE"
CRON_UPDATE_LOCAL_PAUSE_RESUME = "CRON_UPDATE_LOCAL_PAUSE_RESUME"
SUSPEND_PREPARE_LOCAL_POWER = "SUSPEND_PREPARE_LOCAL_POWER"
SUSPEND_RESUME_LOCAL_POWER = "SUSPEND_RESUME_LOCAL_POWER"
TASKS_CANCEL_LOCAL_RECEIPT = "TASKS_CANCEL_LOCAL_RECEIPT"
TASKS_RETRY_LOCAL_RECEIPT = "TASKS_RETRY_LOCAL_RECEIPT"

#: OPERATOR_ONLY typed codes (external service/runtime absent).
CRON_SCRATCH_OPERATOR_REQUIRED = "CRON_SCRATCH_OPERATOR_REQUIRED"
SUSPEND_HANDOFF_OPERATOR_REQUIRED = "SUSPEND_HANDOFF_OPERATOR_REQUIRED"
PORTAL_SERVICE_OPERATOR_REQUIRED = "PORTAL_SERVICE_OPERATOR_REQUIRED"
TASK_SUGGESTIONS_OPERATOR_REQUIRED = "TASK_SUGGESTIONS_OPERATOR_REQUIRED"
TASKS_DISMISS_OPERATOR_REQUIRED = "TASKS_DISMISS_OPERATOR_REQUIRED"
MENTIONS_INBOX_OPERATOR_REQUIRED = "MENTIONS_INBOX_OPERATOR_REQUIRED"

#: The TTS provider ids the canonical config/audio path recognizes.
TTS_PROVIDERS = ("piper", "edge", "hybrid", "openai", "kokoro", "gateway")


# --------------------------------------------------------------------- helpers
def _sm_str(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _sm_require(params: Dict[str, Any], *keys: str) -> str:
    out = _sm_str(params, *keys)
    if not out:
        raise GatewayError(_BAD, f"one of {', '.join(keys)} is required")
    return out


def _sm_present(params: Dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (list, tuple, set)) and any(
                str(v).strip() for v in value):
            return True
        if isinstance(value, (str, int)) and str(value).strip():
            return True
    return False


def _sm_bool(params: Dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = params.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in (
                "1", "true", "yes", "on", "confirm"):
            return True
    return False


# ------------------------------------------------------------------ tts family
def _tts_set_provider(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Persist ``tts.provider`` through the canonical settings write."""
    from ...qubit_compat import config_save

    provider = _sm_str(params, "provider", "id")
    if not provider:
        raise GatewayError(_BAD, "tts.setProvider requires 'provider'")
    if provider not in TTS_PROVIDERS:
        raise GatewayError(
            _BAD,
            f"unknown TTS provider {provider!r}; one of {', '.join(TTS_PROVIDERS)}")
    body = {"config": {"tts": {"provider": provider}},
            "confirm": _sm_bool(params, "confirm", "confirmed")}
    code, out = config_save(cfg, {}, body, {})
    if code >= 400:
        raise GatewayError(str(out.get("code", "CONFIG_WRITE_FAILED")),
                           str(out.get("detail", ""))[:300])
    return {"state": "OK", "provider": provider,
            "applied_to_home": bool(out.get("applied_to_home", False)),
            "authority": ("voicebridge.qubit_compat.config_save (persists "
                          "tts.provider; the setting "
                          "voicebridge/orchestrator._make_tts reads)")}


def _tts_toggle(cfg: Any, params: Dict[str, Any], *,
                enabled: bool) -> Dict[str, Any]:
    from ...providers import speech as SP

    code = TTS_ENABLE_LOCAL_PROFILE if enabled else TTS_DISABLE_LOCAL_PROFILE
    listing = SP.list_speech_profiles(cfg, "speech_out")
    rows = listing.get("profiles", []) if isinstance(listing, dict) else []
    profile_id = _sm_str(params, "profileId", "profile", "id")
    applied = False
    if profile_id:
        profile = SP.get_speech_profile(cfg, profile_id)
        if profile is None or profile.kind != "speech_out":
            raise GatewayError(_BAD,
                               f"no speech-out profile {profile_id!r}")
        updated = SP.SpeechProfile.from_record(
            {**profile.to_record(), "enabled": enabled})
        result = SP.upsert_speech_profile(
            cfg, updated, confirm=_sm_bool(params, "confirm", "confirmed"))
        applied = str(result.get("state")) == "OK" \
            and not bool(result.get("dry_run"))
    return {"state": "OK" if applied else "UNAVAILABLE", "code": code,
            "enabled": enabled, "applied": applied,
            "profiles": [p.get("id") for p in rows],
            "detail": ("Qubit has no global TTS auto-mode flag; TTS on/off is "
                       "the real per-speech-out-profile `enabled` flag plus "
                       "session selection, and the shipped default profile is "
                       "immutable so no global toggle is fabricated"),
            "qubit_counterpart": ("voicebridge.providers.speech speech-out "
                                  "profile `enabled` + "
                                  "set_session_speech_profile"),
            "authority": "voicebridge.providers.speech.list_speech_profiles"}


def _tts_enable(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _tts_toggle(cfg, params, enabled=True)


def _tts_disable(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _tts_toggle(cfg, params, enabled=False)


def _tts_set_persona(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...providers import speech as SP

    persona = _sm_str(params, "persona", "personaId", "profileId", "profile")
    if not persona:
        raise GatewayError(_BAD, "tts.setPersona requires 'persona'")
    session = _sm_str(params, "session", "sessionKey", "session_key")
    rows = SP.list_speech_profiles(cfg, "speech_out").get("profiles", [])
    selected = None
    profile = SP.get_speech_profile(cfg, persona)
    if profile is not None and profile.kind == "speech_out" \
            and not profile.default and session:
        out = SP.set_session_speech_profile(
            cfg, session, "speech_out", persona,
            confirm=_sm_bool(params, "confirm", "confirmed"))
        selected = out.get("profile")
    return {"state": "OK" if selected else "UNAVAILABLE",
            "code": TTS_PERSONA_LOCAL_PROFILE, "persona": persona,
            "selected": selected, "session": session or None,
            "profiles": [p.get("id") for p in rows],
            "detail": ("Qubit keeps no TTS persona-bundle store; the nearest "
                       "real persisted state is a speech-out profile selected "
                       "for a session"),
            "qubit_counterpart": ("voicebridge.providers.speech."
                                  "set_session_speech_profile"),
            "authority": "voicebridge.providers.speech"}


# ----------------------------------------------------------------- cron family
def _cron_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import schedules as S

    job_id = _sm_require(params, "id", "jobId", "job_id")
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        raise GatewayError(_BAD, "cron.update 'id' must be an integer")
    patch = params.get("patch")
    if not isinstance(patch, dict):
        patch = {}
    try:
        current = S.get_job(cfg, jid)
    except S.SchedulesError as exc:
        return {"state": "NOT_FOUND", "code": exc.code, "id": jid}
    applied = None
    if set(patch.keys()) == {"enabled"} and isinstance(patch.get("enabled"),
                                                       bool):
        out = S.set_paused(cfg, jid, not patch["enabled"],
                           requested_by="gateway:cron",
                           confirmed=_sm_bool(params, "confirm", "confirmed"))
        applied = out.get("job")
    return {"state": "OK" if applied else "UNAVAILABLE",
            "code": CRON_UPDATE_LOCAL_PAUSE_RESUME, "id": jid,
            "applied": applied, "job": current.get("job"),
            "detail": ("Qubit's canonical schedule store updates only the "
                       "enabled flag (pause/resume); the remaining upstream "
                       "cron.update patch fields have no local counterpart and "
                       "are not fabricated"),
            "qubit_counterpart": ("voicebridge.schedules.set_paused / "
                                  "delete_job (`qubit cron pause|resume|rm`)"),
            "authority": "voicebridge.schedules"}


def _cron_scratch(cfg: Any, params: Dict[str, Any], *, write: bool
                  ) -> Dict[str, Any]:
    jid = _sm_require(params, "id", "jobId", "job_id")
    return {
        "state": "UNAVAILABLE", "code": CRON_SCRATCH_OPERATOR_REQUIRED,
        "id": jid, "written": False,
        "detail": ("Qubit's schedule store has no private per-job scratch "
                   "column and no separate scratch store exists; nothing is "
                   "read or written"),
        "operator_prerequisite": ("an upstream per-job cron scratch store "
                                  "(cron_job_scratch); Qubit runs none"),
        "qubit_counterpart": ("voicebridge.schedules job fields + memory "
                              "workspace notes (`qubit memory note`)"),
        "authority": "voicebridge.schedules",
    }


def _cron_scratch_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cron_scratch(cfg, params, write=False)


def _cron_scratch_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cron_scratch(cfg, params, write=True)


# ------------------------------------------------------------ suspend family
def _suspend_prepare(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...power import power_control

    return {"state": "UNAVAILABLE", "code": SUSPEND_PREPARE_LOCAL_POWER,
            "prepared": False, "power": power_control(cfg).status(),
            "detail": ("upstream drains in-flight work and pauses the "
                       "scheduler for a cooperative process suspension; Qubit "
                       "reports the real local power/e-stop state and never "
                       "drains or suspends its process"),
            "qubit_counterpart": ("voicebridge.power.PowerControl.status "
                                  "(`qubit power status`)"),
            "authority": "voicebridge.power.power_control"}


def _suspend_resume(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...power import power_control

    return {"state": "UNAVAILABLE", "code": SUSPEND_RESUME_LOCAL_POWER,
            "resumed": False, "power": power_control(cfg).status(),
            "detail": ("upstream releases a prepared suspension; Qubit has no "
                       "suspension to release, so this reports the real local "
                       "power/e-stop state and fabricates no resume"),
            "qubit_counterpart": ("voicebridge.power.PowerControl.reset / "
                                  "global_enable (`qubit power reset` / "
                                  "`qubit power enable`)"),
            "authority": "voicebridge.power.power_control"}


def _suspend_handoff(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": SUSPEND_HANDOFF_OPERATOR_REQUIRED, "handedOff": False,
            "detail": ("upstream arms a handoff that lets a host lifecycle "
                       "owner exit the gateway process; Qubit never exits its "
                       "own process from a gateway call"),
            "operator_prerequisite": ("a service manager that owns process "
                                      "exit (systemd/launchd external restart "
                                      "owner)"),
            "qubit_counterpart": ("operator `systemctl --user restart <qubit "
                                  "unit>` + the operator runbook")}


# ------------------------------------------------------------- portal family
def _portal_refusal(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "state": "UNAVAILABLE", "code": PORTAL_SERVICE_OPERATOR_REQUIRED,
        "method": method, "opened": False, "closed": False,
        "detail": ("Qubit runs no local-port portal HTTP-proxy service; no "
                   "portal is opened, listed, or closed"),
        "operator_prerequisite": ("a browser/portal hosting service (the "
                                  "upstream portal HTTP proxy listening on a "
                                  "local port)"),
        "qubit_counterpart": ("the canonical website-integration store "
                              "(voicebridge.websites) + the operator's own "
                              "browser"),
        "authority": "voicebridge.websites.website_integrations_store",
    }


def _portal_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    out = _portal_refusal("portal.list", params)
    out["portals"] = []
    return out


def _portal_open(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    _sm_require(params, "port", "targetPort", "target_port")
    return _portal_refusal("portal.open", params)


def _portal_close(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    _sm_require(params, "id", "portalId", "portal_id")
    return _portal_refusal("portal.close", params)


# ---------------------------------------------------- taskSuggestions family
def _task_suggestions_refusal(method: str, params: Dict[str, Any]
                              ) -> Dict[str, Any]:
    return {
        "state": "UNAVAILABLE", "code": TASK_SUGGESTIONS_OPERATOR_REQUIRED,
        "method": method, "accepted": False, "created": False,
        "dismissed": False,
        "detail": ("Qubit keeps no ephemeral model-proposed task-suggestion "
                   "registry; no suggestion is created, accepted, or dismissed"),
        "operator_prerequisite": ("an upstream model-proposed task-suggestion "
                                  "registry + run-control runtime; Qubit runs "
                                  "none"),
        "qubit_counterpart": ("durable scheduled work via "
                              "voicebridge.schedules.create_job "
                              "(`qubit cron add`) + the canonical chat session"),
        "authority": "voicebridge.task_ledger.status",
    }


def _task_suggestions_create(cfg: Any, params: Dict[str, Any]
                             ) -> Dict[str, Any]:
    _sm_require(params, "sessionKey", "session_key", "session")
    _sm_require(params, "prompt", "title", "text")
    return _task_suggestions_refusal("taskSuggestions.create", params)


def _task_suggestions_accept(cfg: Any, params: Dict[str, Any]
                             ) -> Dict[str, Any]:
    _sm_require(params, "taskId", "task_id", "id")
    return _task_suggestions_refusal("taskSuggestions.accept", params)


def _task_suggestions_dismiss(cfg: Any, params: Dict[str, Any]
                              ) -> Dict[str, Any]:
    _sm_require(params, "taskId", "task_id", "id")
    return _task_suggestions_refusal("taskSuggestions.dismiss", params)


# -------------------------------------------------------------- tasks family
def _tasks_cancel(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT
    from ... import task_ledger as TL
    from ...actions import actions_store

    receipt_id = _sm_str(params, "receiptId", "receipt_id", "runId", "run_id")
    session = _sm_str(params, "session", "sessionKey", "session_key")
    task_id = _sm_str(params, "taskId", "task_id", "id")
    if receipt_id:
        out = actions_store(cfg).cancel(receipt_id)
        return {"state": str(out.get("state", "OK")),
                "code": TASKS_CANCEL_LOCAL_RECEIPT, "cancelled": True,
                "receipt_id": receipt_id, "result": out,
                "authority": "voicebridge.actions.ActionStore.cancel"}
    if session:
        out = CT.cancel(session)
        return {"state": "OK" if out.get("ok") else "UNAVAILABLE",
                "code": TASKS_CANCEL_LOCAL_RECEIPT,
                "cancelled": bool(out.get("cancelled")), "session": session,
                "result": out,
                "authority": "voicebridge.chat_transport.cancel"}
    if not task_id:
        raise GatewayError(
            _BAD, "tasks.cancel requires taskId, receiptId, or session")
    shown = TL.show(cfg, task_id)
    return {"state": "UNAVAILABLE", "code": TASKS_CANCEL_LOCAL_RECEIPT,
            "cancelled": False, "task": shown,
            "detail": ("Qubit's task ledger is read-only and maps no taskId to "
                       "a live run; cancel is available only for a real action "
                       "receipt or an in-flight chat session"),
            "qubit_counterpart": ("voicebridge.actions.ActionStore.cancel / "
                                  "voicebridge.chat_transport.cancel"),
            "authority": "voicebridge.task_ledger.show"}


def _tasks_retry(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT
    from ... import task_ledger as TL
    from ...actions import actions_store

    receipt_id = _sm_str(params, "receiptId", "receipt_id", "runId", "run_id")
    session = _sm_str(params, "session", "sessionKey", "session_key")
    if receipt_id:
        args = params.get("args") if isinstance(params.get("args"), dict) else {}
        out = actions_store(cfg).retry(
            receipt_id, args=args, target=_sm_str(params, "target"),
            scope=_sm_str(params, "scope"), source="gateway",
            confirmed=_sm_bool(params, "confirm", "confirmed"))
        return {"state": str(out.get("state", "OK")),
                "code": TASKS_RETRY_LOCAL_RECEIPT, "receipt_id": receipt_id,
                "result": out,
                "authority": "voicebridge.actions.ActionStore.retry"}
    if session:
        out = CT.retry_last(cfg, session)
        return {"state": "OK" if out.get("ok")
                 else str(out.get("state", "UNAVAILABLE")),
                "code": TASKS_RETRY_LOCAL_RECEIPT, "session": session,
                "result": out,
                "authority": "voicebridge.chat_transport.retry_last"}
    task_id = _sm_str(params, "taskId", "task_id", "id")
    if not task_id:
        raise GatewayError(
            _BAD, "tasks.retry requires taskId, receiptId, or session")
    shown = TL.show(cfg, task_id)
    return {"state": "UNAVAILABLE", "code": TASKS_RETRY_LOCAL_RECEIPT,
            "task": shown,
            "detail": ("Qubit's task ledger is read-only and stores no retry "
                       "arguments; retry is available only for a real action "
                       "receipt (exact args) or the last chat turn"),
            "qubit_counterpart": ("voicebridge.actions.ActionStore.retry / "
                                  "voicebridge.chat_transport.retry_last"),
            "authority": "voicebridge.task_ledger.show"}


def _tasks_dismiss(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _sm_present(params, "taskIds", "task_ids", "taskId", "task_id",
                       "id"):
        raise GatewayError(_BAD, "tasks.dismiss requires 'taskIds'")
    return {
        "state": "UNAVAILABLE", "code": TASKS_DISMISS_OPERATOR_REQUIRED,
        "dismissed": False,
        "detail": ("Qubit keeps no detached-task completion-delivery registry; "
                   "nothing is dismissed"),
        "operator_prerequisite": ("an upstream detached-task/subagent "
                                  "completion-delivery registry; Qubit keeps "
                                  "none"),
        "qubit_counterpart": ("the canonical read-only task ledger "
                              "(`qubit tasks`)"),
        "authority": "voicebridge.task_ledger.status",
    }


# ------------------------------------------------------------ mentions family
def _mentions_refusal(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "state": "UNAVAILABLE", "code": MENTIONS_INBOX_OPERATOR_REQUIRED,
        "method": method, "dismissed": False, "mentions": [],
        "detail": ("Qubit runs no human-mention inbox service; no mention is "
                   "listed or dismissed"),
        "operator_prerequisite": ("a human-mention inbox service backed by "
                                  "configured message channels"),
        "qubit_counterpart": ("voicebridge.automation outbox/orders "
                              "(`qubit channels`)"),
        "authority": "voicebridge.automation",
    }


def _mentions_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _mentions_refusal("mentions.list", params)


def _mentions_dismiss(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _sm_present(params, "ids", "id", "mentionIds", "mention_ids"):
        raise GatewayError(_BAD, "mentions.dismiss requires 'ids'")
    return _mentions_refusal("mentions.dismiss", params)


# --------------------------------------------------------------- register
def register(registry: Any) -> None:
    """Register the scheduler/media/task-control family handlers."""
    registry.register("tts.enable", _tts_enable)
    registry.register("tts.disable", _tts_disable)
    registry.register("tts.setProvider", _tts_set_provider)
    registry.register("tts.setPersona", _tts_set_persona)
    registry.register("cron.update", _cron_update)
    registry.register("cron.scratch.get", _cron_scratch_get)
    registry.register("cron.scratch.set", _cron_scratch_set)
    registry.register("gateway.suspend.prepare", _suspend_prepare)
    registry.register("gateway.suspend.resume", _suspend_resume)
    registry.register("gateway.suspend.handoff", _suspend_handoff)
    registry.register("portal.list", _portal_list)
    registry.register("portal.open", _portal_open)
    registry.register("portal.close", _portal_close)
    registry.register("taskSuggestions.create", _task_suggestions_create)
    registry.register("taskSuggestions.accept", _task_suggestions_accept)
    registry.register("taskSuggestions.dismiss", _task_suggestions_dismiss)
    registry.register("tasks.cancel", _tasks_cancel)
    registry.register("tasks.retry", _tasks_retry)
    registry.register("tasks.dismiss", _tasks_dismiss)
    registry.register("mentions.list", _mentions_list)
    registry.register("mentions.dismiss", _mentions_dismiss)
