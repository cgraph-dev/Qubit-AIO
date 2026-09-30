"""OpenClaw contractual-pin handler family: content.

Every handler here delegates to a canonical Qubit module the CLI, dashboard,
``/qubit-chat``, and voice surfaces already use; nothing is fabricated and no
handler only returns success. Read operations delegate to canonical read
modules; mutations route through the canonical confirmation/e-stop/journal
gates of their owning module (``schedules``, ``projects``, ``kanban``,
``pairing``, ``plugin_runtime``, ``verified_skills.mutations``).

Methods whose upstream action has no real local counterpart stay ``OPEN`` in
the generated manifest (this module simply does not register them). A handful
of operator-only/remote-only actions are registered as typed refusals with a
real local counterpart; they are trivial literal returns so the manifest's
non-trivial-work proof cannot mistake them for implementations — the
integration lead pairs each with a ``PINNED_SURFACE_DISPOSITIONS`` record.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError


# ------------------------------------------------------------------ helpers
def _cnt_bad(detail: str) -> None:
    """Raise the canonical bad-params gateway error."""
    raise GatewayError("GATEWAY_BAD_PARAMS", detail)


def _cnt_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return default


def _cnt_str(params: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _cnt_int(params: Dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        value = params.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _cnt_requested(params: Dict[str, Any]) -> str:
    return str(params.get("requested_by", "") or "").strip() \
        or "gateway:operator"


def _cnt_confirmed(params: Dict[str, Any]) -> bool:
    return _cnt_bool(params.get("confirmed", params.get("confirm", False)))


def _cnt_cid(params: Dict[str, Any]) -> str:
    return _cnt_str(params, "confirmation_id", "confirmationId")


# ------------------------------------------------------------------- cron
def _cnt_cron_add(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import schedules as S

    name = _cnt_str(params, "name")
    when = _cnt_str(params, "when", "schedule", "spec", "cron")
    task = _cnt_str(params, "task", "taskText", "task_text", "text", "payload")
    if not name:
        _cnt_bad("name is required")
    if not when:
        _cnt_bad("when/schedule is required (a local recurrence string)")
    if not task:
        _cnt_bad("task/taskText is required")
    out = S.create_job(cfg, name, task, when,
                       deliver_to=_cnt_str(params, "deliver_to", "deliverTo"),
                       requested_by=_cnt_requested(params),
                       confirmed=_cnt_confirmed(params))
    return {"state": str(out.get("state", "OK")), "job": out.get("job", {}),
            "authority": "voicebridge.schedules.create_job"}


def _cnt_cron_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import schedules as S

    job_id = _cnt_int(params, "id", "jobId", "job_id")
    if not job_id:
        _cnt_bad("id is required")
    out = S.delete_job(cfg, job_id,
                       requested_by=_cnt_requested(params),
                       confirmed=_cnt_confirmed(params))
    return {"state": "OK", "deleted": out.get("deleted"),
            "history_retained": out.get("history_retained"),
            "authority": "voicebridge.schedules.delete_job"}


def _cnt_cron_run(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import schedules as S

    job_id = _cnt_int(params, "id", "jobId", "job_id")
    mode = _cnt_str(params, "mode", default="due").lower()
    if mode not in ("due", "force", "if-enabled"):
        _cnt_bad("mode must be due, force, or if-enabled")
    if mode == "force":
        out = S.trigger(cfg, job_id, requested_by=_cnt_requested(params))
    else:
        out = S.run_due(cfg, only_job_id=job_id,
                        requested_by=_cnt_requested(params))
    return {"state": str(out.get("state", "OK")), "ran": out.get("ran", 0),
            "results": out.get("results", []),
            "authority": "voicebridge.schedules.run_due/trigger"}


# --------------------------------------------------------------- projects
def _cnt_projects_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import projects as P

    out = P.list_projects(cfg,
                          include_archived=_cnt_bool(
                              params.get("includeArchived", False)))
    return {"state": str(out.get("state", "OK")),
            "projects": out.get("projects", out.get("rows", [])),
            "authority": "voicebridge.projects.list_projects"}


def _cnt_projects_add(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import projects as P

    source = _cnt_str(params, "gitUrl", "git_url", "url", "source")
    if not source:
        _cnt_bad("gitUrl is required")
    name = _cnt_str(params, "name") or source.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    out = P.clone(cfg, name, source,
                  description=_cnt_str(params, "description"),
                  requested_by=_cnt_requested(params),
                  confirmed=_cnt_confirmed(params))
    return {"state": str(out.get("state", "OK")), "project": out,
            "authority": "voicebridge.projects.clone"}


def _cnt_projects_register(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import projects as P

    path = _cnt_str(params, "path", "folder")
    if not path:
        _cnt_bad("path is required")
    name = _cnt_str(params, "name") or path.rstrip("/").rsplit("/", 1)[-1]
    out = P.create(cfg, name, [path],
                   description=_cnt_str(params, "description"),
                   requested_by=_cnt_requested(params),
                   confirmed=_cnt_confirmed(params))
    return {"state": str(out.get("state", "OK")), "project": out,
            "authority": "voicebridge.projects.create"}


def _cnt_projects_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import projects as P

    slug = _cnt_str(params, "id", "slug", "project")
    if not slug:
        _cnt_bad("id is required")
    out = P.archive(cfg, slug, requested_by=_cnt_requested(params),
                    confirmed=_cnt_confirmed(params))
    return {"state": str(out.get("state", "OK")), "archived": out,
            "authority": "voicebridge.projects.archive (reversible removal)"}


# ------------------------------------------------------------------ skills
def _cnt_skills_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...permissions import permissions_store

    store = permissions_store(cfg)
    rows = [rec.to_dict() for rec in store.skills()]
    return {"state": "OK", "skills": rows, "count": len(rows),
            "authority": "voicebridge.permissions.PermissionsStore.skills"}


def _cnt_skills_search(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...permissions import permissions_store
    from ...verified_skills import verified_skills_store

    query = _cnt_str(params, "query", "q").lower()
    limit = _cnt_int(params, "limit", default=25) or 25
    limit = max(1, min(limit, 100))
    rows = [rec.to_dict() for rec in permissions_store(cfg).skills()]
    if query:
        rows = [r for r in rows
                if query in str(r.get("skill_id", "")).lower()
                or query in str(r.get("manifest", {}).get("name", "")).lower()
                or query in str(r.get("manifest", {}).get("description", "")).lower()]
    recall = verified_skills_store(cfg).recall(_cnt_str(params, "query", "q"),
                                               top_k=limit) \
        if query else {"selected": []}
    return {"state": "OK", "results": rows[:limit], "total": len(rows),
            "recall": recall.get("selected", []),
            "authority": "voicebridge.permissions.PermissionsStore.skills + "
                         "voicebridge.verified_skills.VerifiedSkillStore.recall"}


def _cnt_skills_detail(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...permissions import permissions_store
    from ...verified_skills import verified_skills_store

    slug = _cnt_str(params, "slug", "id", "skill_id")
    if not slug:
        _cnt_bad("slug is required")
    rec = permissions_store(cfg).skill(slug)
    vrec = verified_skills_store(cfg).get(slug)
    if rec is None and vrec is None:
        _cnt_bad(f"no skill {slug!r}")
    return {"state": "OK",
            "skill": rec.to_dict() if rec is not None else None,
            "verified": vrec.to_dict() if vrec is not None else None,
            "authority": "voicebridge.permissions + "
                         "voicebridge.verified_skills"}


def _cnt_skills_skill_card(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    slug = _cnt_str(params, "slug", "id", "skill_id")
    if not slug:
        _cnt_bad("slug is required")
    rec = verified_skills_store(cfg).get(slug)
    if rec is None:
        _cnt_bad(f"no verified skill {slug!r}")
    return {"state": "OK", "skillCard": rec.to_dict(),
            "authority": "voicebridge.verified_skills.VerifiedSkillStore.get"}


def _cnt_skills_curator_status(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    from ...verified_skills.workshop import verified_skill_workshop

    return {"state": "OK",
            "curator": verified_skill_workshop(cfg).curator_status(),
            "authority": "voicebridge.verified_skills.workshop.SkillWorkshop"}


def _cnt_skills_proposals_list(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    status = _cnt_str(params, "status") or None
    rows = [rec.to_dict() for rec in verified_skills_store(cfg).list(
        status=status)]
    return {"state": "OK", "proposals": rows, "count": len(rows),
            "authority": "voicebridge.verified_skills.VerifiedSkillStore.list"}


def _cnt_skills_proposals_inspect(cfg: Any, params: Dict[str, Any]
                                  ) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    slug = _cnt_str(params, "id", "skill_id", "slug")
    if not slug:
        _cnt_bad("id is required")
    rec = verified_skills_store(cfg).get(slug)
    if rec is None:
        _cnt_bad(f"no skill proposal {slug!r}")
    return {"state": "OK", "proposal": rec.to_dict(),
            "authority": "voicebridge.verified_skills.VerifiedSkillStore.get"}


def _cnt_skills_proposals_history_status(cfg: Any, params: Dict[str, Any]
                                         ) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    return {"state": "OK",
            "journal": verified_skills_store(cfg).journal_verify(),
            "authority": "voicebridge.verified_skills journal_verify"}


def _cnt_skills_proposals_create(cfg: Any, params: Dict[str, Any]
                                 ) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    payload = params.get("payload") if isinstance(params.get("payload"),
                                                  dict) else params
    out = SM.handle_skill_mutation(cfg, "create", payload,
                                   confirmation_id=_cnt_cid(params),
                                   actor="web:user", session="gateway")
    return {"state": str(out.get("state", "")), **out,
            "authority": "voicebridge.verified_skills.mutations (shared "
                         "confirmation journal)"}


def _cnt_skills_library_list(cfg: Any, params: Dict[str, Any]
                             ) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    rows = [rec.to_dict() for rec in verified_skills_store(cfg).list()]
    return {"state": "OK", "skills": rows, "count": len(rows),
            "authority": "voicebridge.verified_skills.VerifiedSkillStore.list"}


def _cnt_skills_library_read(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    slug = _cnt_str(params, "id", "slug", "skill_id")
    if not slug:
        _cnt_bad("id is required")
    rec = verified_skills_store(cfg).get(slug)
    if rec is None:
        _cnt_bad(f"no skill {slug!r}")
    return {"state": "OK", "skill": rec.to_dict(),
            "authority": "voicebridge.verified_skills.VerifiedSkillStore.get"}


def _cnt_skills_install(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    out = SM.handle_skill_mutation(
        cfg, "install", params,
        confirmation_id=_cnt_cid(params),
        manifest=params.get("manifest") if isinstance(params.get("manifest"),
                                                      dict) else None,
        identifier=_cnt_str(params, "identifier", "id"),
        actor="web:user", session="gateway")
    return {"state": str(out.get("state", "")), **out,
            "authority": "voicebridge.verified_skills.mutations (stages a "
                         "local reviewed manifest; nothing is downloaded)"}


# ----------------------------------------------------------------- plugins
def _cnt_plugins_install(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import plugin_runtime as PR

    manifest = params.get("manifest") if isinstance(params.get("manifest"),
                                                    dict) else None
    out = PR.install(cfg, manifest=manifest,
                     identifier=_cnt_str(params, "identifier", "id", "path"),
                     confirmed=_cnt_confirmed(params),
                     confirmation_id=_cnt_cid(params),
                     requested_by=_cnt_requested(params))
    return {"state": str(out.get("state", "OK")), **out,
            "authority": "voicebridge.plugin_runtime.install"}


def _cnt_plugins_uninstall(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import plugin_runtime as PR

    plugin_id = _cnt_str(params, "pluginId", "plugin_id", "id", "name")
    if not plugin_id:
        _cnt_bad("pluginId is required")
    out = PR.unregister(cfg, plugin_id, confirmed=_cnt_confirmed(params),
                        confirmation_id=_cnt_cid(params),
                        requested_by=_cnt_requested(params))
    return {"state": str(out.get("state", "OK")), **out,
            "authority": "voicebridge.plugin_runtime.unregister"}


# ------------------------------------------------------------------ models
def _cnt_models_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import local_models as LM
    from ... import model_profiles_ops as MPO

    catalog = LM.catalog(cfg)
    profiles = MPO.list_model_profiles(cfg)
    return {"state": "OK", "models": catalog.get("models", []),
            "profiles": profiles.get("profiles", []),
            "runtime": {"state": catalog.get("state"),
                        "runtime_observed": catalog.get("runtime_observed")},
            "authority": "voicebridge.local_models.catalog + "
                         "voicebridge.model_profiles_ops.list_model_profiles"}


def _cnt_models_auth_status(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    from ... import auth_status as AS

    return {"state": "OK", **AS.auth_status(cfg),
            "authority": "voicebridge.auth_status.auth_status"}


def _cnt_models_probe(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import model_profiles_ops as MPO

    profile_id = _cnt_str(params, "profileId", "profile_id", "id")
    if not profile_id:
        provider = _cnt_str(params, "provider")
        if not provider:
            _cnt_bad("profileId or provider is required")
        listing = MPO.list_model_profiles(cfg)
        profiles = listing.get("profiles", []) \
            if isinstance(listing, dict) else []
        match = next((p for p in profiles
                      if str(p.get("provider", "")) == provider), None)
        if match is None:
            _cnt_bad(f"no local model profile for provider {provider!r}")
        profile_id = str(match.get("id", "") or "")
    out = MPO.test_model_profile(cfg, profile_id, probe=True)
    return {"state": str(out.get("state", "OK")), **out,
            "authority": "voicebridge.model_profiles_ops.test_model_profile"}


# --------------------------------------------------------------------- tts
def _cnt_tts_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...providers import speech as SP

    effective = SP.resolve_speech_out(cfg)
    return {"state": "OK", **effective.to_public_dict(),
            "authority": "voicebridge.providers.speech.resolve_speech_out"}


def _cnt_tts_providers(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...providers import speech as SP

    rows = [p.to_record() for p in SP.default_speech_profiles(cfg)
            if p.kind == SP.SPEECH_OUT]
    configured = cfg.get("tts", "provider", default="piper")
    return {"state": "OK", "providers": rows, "configured": configured,
            "authority": "voicebridge.providers.speech.default_speech_profiles"}


def _cnt_tts_personas(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...providers import speech as SP

    out = SP.list_speech_profiles(cfg, kind=SP.SPEECH_OUT)
    return {"state": "OK", **out,
            "authority": "voicebridge.providers.speech.list_speech_profiles"}


def _cnt_tts_speak(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..rpc import _talk_voice_complete

    return _talk_voice_complete(cfg, params)


# ------------------------------------------------------------------ talk
def _cnt_talk_catalog(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...providers import speech as SP

    out = SP.list_speech_profiles(cfg)
    return {"state": "OK", **out,
            "authority": "voicebridge.providers.speech.list_speech_profiles "
                         "(STT/TTS/realtime profile registry)"}


def _cnt_talk_speak(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..rpc import _talk_voice_complete

    return _talk_voice_complete(cfg, params)


# --------------------------------------------------------------- channels
def _cnt_channels_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import channels_inbound as CI

    return {"state": "OK", **CI.status(cfg),
            "authority": "voicebridge.channels_inbound.status"}


# --------------------------------------------------------- channel-pairing
def _cnt_pairing_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as PA

    out = PA.list_codes(cfg)
    return {"state": str(out.get("state", "OK")),
            "pairings": out.get("codes", []),
            "authority": "voicebridge.pairing.list_codes"}


def _cnt_pairing_approve(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as PA

    code = _cnt_str(params, "code", "pairing_code", "pairingCode", "id")
    if not code:
        _cnt_bad("code is required")
    out = PA.approve(cfg, code, requested_by=_cnt_requested(params),
                     confirmed=_cnt_confirmed(params))
    return {"state": str(out.get("state", "OK")), **out,
            "authority": "voicebridge.pairing.approve"}


def _cnt_pairing_dismiss(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import pairing as PA

    code_id = _cnt_str(params, "id", "code_id", "codeId")
    if not code_id:
        _cnt_bad("id is required")
    out = PA.revoke(cfg, code_id, requested_by=_cnt_requested(params),
                    confirmed=_cnt_confirmed(params))
    return {"state": str(out.get("state", "OK")), **out,
            "authority": "voicebridge.pairing.revoke"}


# --------------------------------------------------------------- commands
def _cnt_commands_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import voice_commands as VC

    rows = []
    for entry in VC._iter_entries():
        rows.append({"id": entry.id, "title": entry.title,
                     "aliases": list(entry.aliases),
                     "backend": entry.backend,
                     "scope": list(entry.scope),
                     "confirm": bool(entry.confirm)})
    return {"state": "OK", "commands": rows, "count": len(rows),
            "authority": "voicebridge.voice_commands registry"}


# --------------------------------------------------------------- voicewake
def _cnt_voicewake_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import wake as W

    return {"state": "OK", **W.status(cfg),
            "authority": "voicebridge.wake.status"}


# ---------------------------------------------------------------- mcp-app
def _cnt_mcp_app_view(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import mcp_servers as MS
    from ... import plugin_runtime as PR

    grants = PR.mcp_grants(cfg)
    servers = MS.list_servers(cfg)
    return {"state": "OK", "host": grants.get("host"),
            "grants": grants.get("grants", []),
            "servers": servers.get("servers", []),
            "authority": "voicebridge.plugin_runtime.mcp_grants + "
                         "voicebridge.mcp_servers.list_servers"}


def _cnt_mcp_app_list_tools(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    from ... import plugin_runtime as PR

    out = PR.mcp_doctor(cfg)
    return {"state": str(out.get("state", "OK")),
            "tools": out.get("tools", []),
            "tool_kinds": out.get("tool_kinds", {}),
            "authority": "voicebridge.plugin_runtime.mcp_doctor (in-process "
                         "loopback MCP tool catalog)"}


# ------------------------------------------------------------------ board
def _cnt_board_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import kanban as K

    board = _cnt_str(params, "board", "slug", default="main") or "main"
    out = K.board_open(cfg, board)
    return {**out, "authority": "voicebridge.kanban.board_open"}


def _cnt_board_widget_app_view(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    from ... import kanban as K

    widget_id = _cnt_int(params, "widgetId", "widget_id", "id")
    if not widget_id:
        _cnt_bad("widgetId is required")
    out = K.show_widget(cfg, widget_id)
    return {**out, "authority": "voicebridge.kanban.show_widget"}


def _cnt_board_widget_put(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import kanban as K

    board = _cnt_str(params, "board", default="main") or "main"
    kind = _cnt_str(params, "kind", "content_kind", "contentKind")
    if not kind:
        _cnt_bad("kind is required")
    out = K.widget_add(cfg, board, kind,
                       title=_cnt_str(params, "title"),
                       content=_cnt_str(params, "content"),
                       content_kind=_cnt_str(params, "contentKind"),
                       requested_by=_cnt_requested(params),
                       confirmed=_cnt_confirmed(params))
    return {"state": str(out.get("state", "OK")), **out,
            "authority": "voicebridge.kanban.widget_add"}


# ----------------------------------------------------------------- canvas
def _cnt_canvas_document_view(cfg: Any, params: Dict[str, Any]
                              ) -> Dict[str, Any]:
    from ... import browser_tabs as BT

    out = BT.read(cfg, _cnt_str(params, "target"))
    return {"state": str(out.get("state", "OK")),
            "document": {"title": str(out.get("title", "") or ""),
                         "url": str(out.get("url", "") or ""),
                         "text": str(out.get("text", "") or ""),
                         "trust": str(out.get("trust", "") or "")},
            "counterpart": "Qubit read-only browser document view",
            "authority": str(out.get("authority", "") or "")}


# ---------------------------------------------------------- tts/talk (cont.)
def _cnt_tts_convert(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..rpc import _talk_voice_complete

    return _talk_voice_complete(cfg, params)


def _cnt_talk_config(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...audio import settings as AS
    from ...providers import speech as SP

    return {"state": "OK",
            "profiles": SP.list_speech_profiles(cfg),
            "speech_in": SP.resolve_speech_in(cfg).to_public_dict(),
            "speech_out": SP.resolve_speech_out(cfg).to_public_dict(),
            "audio": AS.status(cfg),
            "authority": "voicebridge.providers.speech (canonical profile "
                         "registry + resolution) + "
                         "voicebridge.audio.settings.status"}


# --------------------------------------------------------- skills (library)
def _cnt_skills_library_save(cfg: Any, params: Dict[str, Any]
                             ) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    payload = params.get("payload") if isinstance(params.get("payload"),
                                                  dict) else params
    out = SM.handle_skill_mutation(cfg, "create", payload,
                                   confirmation_id=_cnt_cid(params),
                                   actor="web:user", session="gateway")
    return {"state": str(out.get("state", "")), **out,
            "authority": "voicebridge.verified_skills.mutations (stages a "
                         "reviewed local skill on the shared confirmation "
                         "journal)"}


def _cnt_skills_library_mutate(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    action = _cnt_str(params, "action", "op").lower()
    if action not in ("create", "install", "remove"):
        _cnt_bad("action must be create, install, or remove")
    payload = params.get("payload") if isinstance(params.get("payload"),
                                                  dict) else params
    out = SM.handle_skill_mutation(cfg, action, payload,
                                   confirmation_id=_cnt_cid(params),
                                   actor="web:user", session="gateway")
    return {"state": str(out.get("state", "")), **out,
            "authority": "voicebridge.verified_skills.mutations"}


def _cnt_skills_library_import(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    out = SM.handle_skill_mutation(
        cfg, "install", params,
        confirmation_id=_cnt_cid(params),
        manifest=params.get("manifest") if isinstance(params.get("manifest"),
                                                      dict) else None,
        identifier=_cnt_str(params, "identifier", "id", "slug"),
        actor="web:user", session="gateway")
    return {"state": str(out.get("state", "")), **out,
            "authority": "voicebridge.verified_skills.mutations (stages a "
                         "local manifest; nothing is downloaded)"}


def _cnt_skills_library_upload(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    manifest = params.get("manifest")
    out = SM.handle_skill_mutation(
        cfg, "install", params,
        confirmation_id=_cnt_cid(params),
        manifest=manifest if isinstance(manifest, dict) else None,
        identifier=_cnt_str(params, "identifier", "id", "slug"),
        actor="web:user", session="gateway")
    return {"state": str(out.get("state", "")), **out,
            "authority": "voicebridge.verified_skills.mutations (local upload "
                         "manifest; no remote fetch)"}


def _cnt_skills_library_activate(cfg: Any, params: Dict[str, Any]
                                 ) -> Dict[str, Any]:
    from ...permissions import permissions_store

    skill_id = _cnt_str(params, "id", "skill_id", "slug")
    if not skill_id:
        _cnt_bad("id is required")
    if not _cnt_confirmed(params):
        raise GatewayError("CONFIRMATION_REQUIRED",
                           f"activating skill {skill_id!r} requires "
                           "confirmed=true")
    store = permissions_store(cfg)
    if store.skill(skill_id) is None:
        _cnt_bad(f"no skill {skill_id!r}")
    enabled = _cnt_bool(params.get("enabled", True), True)
    fn = store.enable_skill if enabled else store.disable_skill
    rec = fn(skill_id, requested_by=_cnt_requested(params), confirmed=True)
    return {"state": "OK",
            "skill": rec.to_dict() if hasattr(rec, "to_dict") else rec,
            "enabled": enabled,
            "authority": "voicebridge.permissions.PermissionsStore."
                         "enable_skill/disable_skill"}


# ------------------------------------------------------ plugins (mutations)
def _cnt_plugins_set_enabled(cfg: Any, params: Dict[str, Any]
                             ) -> Dict[str, Any]:
    from ... import plugin_runtime as PR

    name = _cnt_str(params, "name", "pluginId", "plugin_id", "id")
    if not name:
        _cnt_bad("name is required")
    enabled = _cnt_bool(params.get("enabled", True), True)
    out = PR.handle_toolset_mutation(
        cfg, name, enabled,
        confirmation_id=_cnt_cid(params),
        actor=_cnt_str(params, "actor", "requested_by") or "web:user",
        session="gateway")
    return {"state": str(out.get("state", "OK")), **out,
            "authority": "voicebridge.plugin_runtime.handle_toolset_mutation "
                         "(shared confirmation journal)"}


# ------------------------------------------------------------- board (cont.)
def _cnt_board_data_read(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import kanban as K

    out = K.export(cfg)
    return {"state": str(out.get("state", "OK")),
            "snapshot": out.get("snapshot", {}),
            "authority": "voicebridge.kanban.export"}


# ---------------------------------------- typed refusals (real counterpart)
def _cnt_projects_search_remote(cfg: Any, params: Dict[str, Any]
                                ) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": "PROJECT_REMOTE_SEARCH_OPERATOR_REQUIRED",
            "detail": "Remote project discovery needs a configured forge "
                      "credential and network access; Qubit runs no remote "
                      "search service.",
            "operator_prerequisite": "a configured git forge credential and "
                                     "a network-reachable forge API",
            "qubit_counterpart": "local projects: `qubit projects list`"}


def _cnt_channels_start(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
            "detail": "Qubit runs no persistent inbound channel listener; "
                      "channels are configured as credential references and "
                      "driven by verified webhook/poll ingress.",
            "operator_prerequisite": "a configured inbound channel transport "
                                     "(webhook/poll) and its credential",
            "qubit_counterpart": "`qubit channels status/configure` + "
                                 "channels_inbound ingress"}


def _cnt_channels_stop(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
            "detail": "Qubit runs no persistent inbound channel listener to "
                      "stop; ingress is request-scoped.",
            "operator_prerequisite": "a configured inbound channel transport "
                                     "(webhook/poll) and its credential",
            "qubit_counterpart": "`qubit channels status/configure` + "
                                 "channels_inbound ingress"}


def _cnt_channels_logout(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
            "detail": "Qubit stores channel credential references only; "
                      "logout is the operator removing the reference.",
            "operator_prerequisite": "operator-managed credential "
                                     "reference removal",
            "qubit_counterpart": "`qubit channels configure/unconfigure`"}


def _cnt_models_auth_logout(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    return {"state": "REFUSED", "code": "AUTH_LOGOUT_MANUAL_HANDOFF",
            "detail": "Qubit persists credential references (env:/file:) "
                      "only; it never stores a provider token to log out.",
            "qubit_counterpart": "CLI `qubit auth status` + remove the "
                                 "env/file reference"}


# ------------------- typed refusals: hosted/remote-only pinned families
def _cnt_push_refusal(method: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "method": method,
            "code": "PUSH_WEB_OPERATOR_REQUIRED",
            "detail": "Qubit runs no web-push service; browser push needs a "
                      "VAPID keypair and a subscription store it does not "
                      "host.",
            "operator_prerequisite": "a configured web-push service (VAPID "
                                     "keypair + subscription store) and browser "
                                     "notification permission",
            "qubit_counterpart": "in-app dashboard notices + the canonical "
                                 "messaging outbox (`voicebridge.messaging`)"}


def _cnt_push_test(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cnt_push_refusal("push.test")


def _cnt_push_vapid_public_key(cfg: Any, params: Dict[str, Any]
                               ) -> Dict[str, Any]:
    return _cnt_push_refusal("push.web.vapidPublicKey")


def _cnt_push_web_subscribe(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    return _cnt_push_refusal("push.web.subscribe")


def _cnt_push_web_unsubscribe(cfg: Any, params: Dict[str, Any]
                              ) -> Dict[str, Any]:
    return _cnt_push_refusal("push.web.unsubscribe")


def _cnt_push_web_test(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cnt_push_refusal("push.web.test")


def _cnt_push_prefs_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cnt_push_refusal("push.web.preferences.get")


def _cnt_push_prefs_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cnt_push_refusal("push.web.preferences.set")


def _cnt_github_refusal(method: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "method": method,
            "code": "GITHUB_OPERATOR_REQUIRED",
            "detail": "Qubit stores credential references only and runs no "
                      "GitHub OAuth/app service.",
            "operator_prerequisite": "a GitHub credential reference "
                                     "(env:/file:) with the required scopes and "
                                     "network access",
            "qubit_counterpart": "CLI `qubit auth status` + an operator-managed "
                                 "credential reference"}


def _cnt_github_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cnt_github_refusal("tools.github.status")


def _cnt_github_configure(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cnt_github_refusal("tools.github.configure")


def _cnt_github_auth_start(cfg: Any, params: Dict[str, Any]
                           ) -> Dict[str, Any]:
    return _cnt_github_refusal("tools.github.authorize.start")


def _cnt_github_auth_poll(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cnt_github_refusal("tools.github.authorize.poll")


def _cnt_github_auth_cancel(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    return _cnt_github_refusal("tools.github.authorize.cancel")


# ------------------------------------------------------------- registration
def register(registry: Any) -> None:
    """Register the content-family pinned methods on *registry*."""
    registry.register("cron.add", _cnt_cron_add)
    registry.register("cron.remove", _cnt_cron_remove)
    registry.register("cron.run", _cnt_cron_run)

    registry.register("projects.list", _cnt_projects_list)
    registry.register("projects.add", _cnt_projects_add)
    registry.register("projects.register", _cnt_projects_register)
    registry.register("projects.remove", _cnt_projects_remove)

    registry.register("skills.status", _cnt_skills_status)
    registry.register("skills.search", _cnt_skills_search)
    registry.register("skills.detail", _cnt_skills_detail)
    registry.register("skills.skillCard", _cnt_skills_skill_card)
    registry.register("skills.curator.status", _cnt_skills_curator_status)
    registry.register("skills.proposals.list", _cnt_skills_proposals_list)
    registry.register("skills.proposals.inspect", _cnt_skills_proposals_inspect)
    registry.register("skills.proposals.historyStatus",
                      _cnt_skills_proposals_history_status)
    registry.register("skills.proposals.create", _cnt_skills_proposals_create)
    registry.register("skills.library.list", _cnt_skills_library_list)
    registry.register("skills.library.read", _cnt_skills_library_read)
    registry.register("skills.install", _cnt_skills_install)

    registry.register("plugins.install", _cnt_plugins_install)
    registry.register("plugins.uninstall", _cnt_plugins_uninstall)

    registry.register("models.list", _cnt_models_list)
    registry.register("models.authStatus", _cnt_models_auth_status)
    registry.register("models.probe", _cnt_models_probe)

    registry.register("tts.status", _cnt_tts_status)
    registry.register("tts.providers", _cnt_tts_providers)
    registry.register("tts.personas", _cnt_tts_personas)
    registry.register("tts.speak", _cnt_tts_speak)

    registry.register("talk.catalog", _cnt_talk_catalog)
    registry.register("talk.speak", _cnt_talk_speak)

    registry.register("channels.status", _cnt_channels_status)
    registry.register("channels.pairing.list", _cnt_pairing_list)
    registry.register("channels.pairing.approve", _cnt_pairing_approve)
    registry.register("channels.pairing.dismiss", _cnt_pairing_dismiss)

    registry.register("commands.list", _cnt_commands_list)
    registry.register("voicewake.get", _cnt_voicewake_get)

    registry.register("mcp.app.view", _cnt_mcp_app_view)
    registry.register("mcp.app.listTools", _cnt_mcp_app_list_tools)

    registry.register("board.get", _cnt_board_get)
    registry.register("board.widget.appView", _cnt_board_widget_app_view)
    registry.register("board.widget.put", _cnt_board_widget_put)
    registry.register("board.data.read", _cnt_board_data_read)
    registry.register("canvas.document.view", _cnt_canvas_document_view)

    registry.register("tts.convert", _cnt_tts_convert)
    registry.register("talk.config", _cnt_talk_config)

    registry.register("skills.library.save", _cnt_skills_library_save)
    registry.register("skills.library.mutate", _cnt_skills_library_mutate)
    registry.register("skills.library.import", _cnt_skills_library_import)
    registry.register("skills.library.upload", _cnt_skills_library_upload)
    registry.register("skills.library.activate", _cnt_skills_library_activate)

    registry.register("plugins.setEnabled", _cnt_plugins_set_enabled)

    registry.register("projects.searchRemote", _cnt_projects_search_remote)
    registry.register("channels.start", _cnt_channels_start)
    registry.register("channels.stop", _cnt_channels_stop)
    registry.register("channels.logout", _cnt_channels_logout)
    registry.register("models.authLogout", _cnt_models_auth_logout)

    registry.register("push.test", _cnt_push_test)
    registry.register("push.web.vapidPublicKey", _cnt_push_vapid_public_key)
    registry.register("push.web.subscribe", _cnt_push_web_subscribe)
    registry.register("push.web.unsubscribe", _cnt_push_web_unsubscribe)
    registry.register("push.web.test", _cnt_push_web_test)
    registry.register("push.web.preferences.get", _cnt_push_prefs_get)
    registry.register("push.web.preferences.set", _cnt_push_prefs_set)

    registry.register("tools.github.status", _cnt_github_status)
    registry.register("tools.github.configure", _cnt_github_configure)
    registry.register("tools.github.authorize.start", _cnt_github_auth_start)
    registry.register("tools.github.authorize.poll", _cnt_github_auth_poll)
    registry.register("tools.github.authorize.cancel", _cnt_github_auth_cancel)
