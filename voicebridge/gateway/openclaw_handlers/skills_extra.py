"""OpenClaw contractual-pin ``skills.*`` extra family (verified-skill surface).

Qubit's canonical skill authority is the verified-skill memory package
(:mod:`voicebridge.verified_skills`): the verified-before-remember lifecycle
store, the journal-backed skill mutation contract, and the agent-owned Skill
Workshop collection layer. The bundled registry
(:mod:`voicebridge.verified_skills._bundled`) supplies Qubit's read-only,
human-reviewed product skills. Every handler here delegates to one of those
modules; a capability whose upstream action needs an external service Qubit does
not run emits a typed refusal naming its exact prerequisite.

Dispositions for the 18 contractual-pin methods this module owns:

* ``skills.bins`` — SAFE_EQUIVALENT (``SKILLS_BINS_LOCAL_BUNDLED``): upstream
  collects skill *executables* from the agent workspace so node hosts can
  auto-allow them. Qubit skills are read-only instructional/advisory material
  that never ship executables, so the handler returns the real bundled skill
  registry with an empty ``bins`` list.
* ``skills.curator.pin`` / ``skills.curator.unpin`` /
  ``skills.curator.restore`` — SAFE_EQUIVALENT: upstream retired these to a
  stub that always errors ("the weekly collection review manages the skill
  collection"). Qubit keeps the real owner-gated primitives
  (``VerifiedSkillStore.pin`` / ``.unpin`` and
  ``SkillWorkshop.restore`` / ``.rollback``).
* ``skills.proposals.apply`` / ``skills.proposals.reject`` — SAFE_EQUIVALENT:
  the real owner-gated verified-skill verdict
  (``VerifiedSkillStore.apply_verdict``).
* ``skills.proposals.quarantine`` — SAFE_EQUIVALENT: the real
  ``VerifiedSkillStore.quarantine_revoked``.
* ``skills.proposals.revise`` / ``skills.proposals.update`` — SAFE_EQUIVALENT:
  a reviewed skill draft staged on the shared confirmation journal through
  ``voicebridge.verified_skills.mutations``.
* ``skills.proposals.requestRevision`` — SAFE_EQUIVALENT: upstream forwards
  free-text revision instructions to the owning agent's chat session; Qubit
  appends them to the one session authority for the next turn.
* ``skills.proposals.events.list`` — SAFE_EQUIVALENT: the canonical append-only
  verified-skill hash-chain journal.
* ``skills.update`` — SAFE_EQUIVALENT: the real local skill config mutation
  (``permissions.PermissionsStore`` enable/disable); raw keys/env are refused.
* ``skills.proposals.evaluate`` / ``skills.proposals.historyScan`` —
  OPERATOR_ONLY: Qubit has no isolated proposal evaluator or session-history
  scanner (verification is an explicit human owner verdict).
* ``skills.securityVerdicts`` — OPERATOR_ONLY: needs a reachable remote
  security-verdict registry.
* ``skills.upload.begin`` / ``skills.upload.chunk`` / ``skills.upload.commit``
  — OPERATOR_ONLY: Qubit runs no staged chunked skill-archive upload store.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

#: SAFE_EQUIVALENT typed codes (real local counterparts).
SKILLS_BINS_LOCAL_BUNDLED = "SKILLS_BINS_LOCAL_BUNDLED"
SKILLS_CURATOR_PIN_LOCAL = "SKILLS_CURATOR_PIN_LOCAL"
SKILLS_CURATOR_UNPIN_LOCAL = "SKILLS_CURATOR_UNPIN_LOCAL"
SKILLS_CURATOR_RESTORE_LOCAL = "SKILLS_CURATOR_RESTORE_LOCAL"
SKILLS_PROPOSALS_APPLY_LOCAL_VERDICT = "SKILLS_PROPOSALS_APPLY_LOCAL_VERDICT"
SKILLS_PROPOSALS_REJECT_LOCAL_VERDICT = "SKILLS_PROPOSALS_REJECT_LOCAL_VERDICT"
SKILLS_PROPOSALS_QUARANTINE_LOCAL = "SKILLS_PROPOSALS_QUARANTINE_LOCAL"
SKILLS_PROPOSALS_REVISE_LOCAL_STAGE = "SKILLS_PROPOSALS_REVISE_LOCAL_STAGE"
SKILLS_PROPOSALS_UPDATE_LOCAL_STAGE = "SKILLS_PROPOSALS_UPDATE_LOCAL_STAGE"
SKILLS_PROPOSALS_REQUESTREVISION_LOCAL_SESSION = (
    "SKILLS_PROPOSALS_REQUESTREVISION_LOCAL_SESSION")
SKILLS_PROPOSALS_EVENTS_LOCAL_JOURNAL = "SKILLS_PROPOSALS_EVENTS_LOCAL_JOURNAL"
SKILLS_UPDATE_LOCAL_CONFIG = "SKILLS_UPDATE_LOCAL_CONFIG"
#: OPERATOR_ONLY typed codes (external prerequisite absent).
SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED = (
    "SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED")
SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED = (
    "SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED")
SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED = (
    "SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED")
SKILLS_UPLOAD_OPERATOR_REQUIRED = "SKILLS_UPLOAD_OPERATOR_REQUIRED"
#: Shared local failure code for a canonical store refusal (never fabricated).
SKILLS_LOCAL_OP_FAILED = "SKILLS_LOCAL_OP_FAILED"

_BAD = "GATEWAY_BAD_PARAMS"


# ------------------------------------------------------------------ helpers
def _sk_str(params: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _sk_int(params: Dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        try:
            return int(params.get(key))
        except (TypeError, ValueError):
            continue
    return default


def _sk_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return default


def _sk_confirmed(params: Dict[str, Any]) -> bool:
    return _sk_bool(params.get("confirmed", params.get("confirm", False)))


def _sk_actor(params: Dict[str, Any]) -> str:
    # The gateway is a transport: an owner-gated mutation must arrive from a
    # canonical confirmed-human principal. Default to the dashboard identity
    # ONLY behind the explicit confirmed gate below.
    return _sk_str(params, "requested_by", "requestedBy",
                   default="dashboard:user")


def _sk_require_confirm(params: Dict[str, Any], what: str) -> None:
    if not _sk_confirmed(params):
        raise GatewayError(
            "CONFIRMATION_REQUIRED",
            f"{what} is owner-gated and requires confirmed=true")


def _sk_require_id(params: Dict[str, Any]) -> str:
    sid = _sk_str(params, "skill_id", "skillId", "proposalId", "proposal_id",
                  "id", "slug", "skill", "name")
    if not sid:
        raise GatewayError(_BAD, "skill_id/proposalId is required")
    return sid


def _sk_cid(params: Dict[str, Any]) -> str:
    return _sk_str(params, "confirmation_id", "confirmationId")


def _sk_store_call(fn, *args, **kwargs):
    """Call a canonical store primitive, mapping its typed error to the RPC."""
    from ...verified_skills import VerifiedSkillError

    try:
        return fn(*args, **kwargs)
    except VerifiedSkillError as exc:
        raise GatewayError(SKILLS_LOCAL_OP_FAILED, str(exc)[:300]) from exc


# ------------------------------------------------------------- bins family
def _skills_bins(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    bundled = verified_skills_store(cfg).bundled()
    names = sorted(str(m.get("name", "")) for m in bundled)
    return {"state": "OK", "code": SKILLS_BINS_LOCAL_BUNDLED, "bins": [],
            "bundledSkills": names,
            "detail": ("Qubit skills are read-only instructional/advisory "
                       "material and never provide executables; the real "
                       "bundled skill registry is reported instead"),
            "qubit_counterpart": ("voicebridge.verified_skills._bundled."
                                  "bundled_skill_manifests"),
            "authority": ("voicebridge.verified_skills.VerifiedSkillStore."
                          "bundled")}


# ---------------------------------------------------------- curator family
def _skills_curator_pin(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    _sk_require_confirm(params, "skills.curator.pin")
    skill_id = _sk_require_id(params)
    store = verified_skills_store(cfg)
    rec = _sk_store_call(store.pin, skill_id, requested_by=_sk_actor(params))
    return {"state": "OK", "code": SKILLS_CURATOR_PIN_LOCAL,
            "skill": rec.to_dict(),
            "qubit_counterpart": ("voicebridge.verified_skills."
                                  "VerifiedSkillStore.pin"),
            "authority": ("owner-gated verified-skill pin (upstream retired "
                          "this method to a stub)")}


def _skills_curator_unpin(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    _sk_require_confirm(params, "skills.curator.unpin")
    skill_id = _sk_require_id(params)
    store = verified_skills_store(cfg)
    rec = _sk_store_call(store.unpin, skill_id, requested_by=_sk_actor(params))
    return {"state": "OK", "code": SKILLS_CURATOR_UNPIN_LOCAL,
            "skill": rec.to_dict(),
            "qubit_counterpart": ("voicebridge.verified_skills."
                                  "VerifiedSkillStore.unpin"),
            "authority": ("owner-gated verified-skill unpin (upstream retired "
                          "this method to a stub)")}


def _skills_curator_restore(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills.workshop import verified_skill_workshop

    _sk_require_confirm(params, "skills.curator.restore")
    actor = _sk_actor(params)
    workshop = verified_skill_workshop(cfg)
    backup_id = _sk_str(params, "backupId", "backup_id")
    if backup_id:
        out = _sk_store_call(workshop.restore, backup_id, requested_by=actor)
    else:
        out = _sk_store_call(workshop.rollback, requested_by=actor)
    return {"state": "OK", "code": SKILLS_CURATOR_RESTORE_LOCAL, **out,
            "qubit_counterpart": ("voicebridge.verified_skills.workshop."
                                  "SkillWorkshop.restore/rollback"),
            "authority": ("human-only, digest-verified Skill Workshop "
                          "collection restore")}


# ------------------------------------------------------ proposals: decisions
def _skills_proposals_apply(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import ST_VERIFIED, verified_skills_store

    _sk_require_confirm(params, "skills.proposals.apply")
    skill_id = _sk_require_id(params)
    store = verified_skills_store(cfg)
    rec = _sk_store_call(store.apply_verdict, skill_id, decision=ST_VERIFIED,
                         requested_by=_sk_actor(params),
                         reason=_sk_str(params, "reason"))
    return {"state": "OK", "code": SKILLS_PROPOSALS_APPLY_LOCAL_VERDICT,
            "skill": rec.to_dict(),
            "qubit_counterpart": ("voicebridge.verified_skills."
                                  "VerifiedSkillStore.apply_verdict("
                                  "decision='verified')"),
            "authority": ("owner-gated verified-before-remember promotion on "
                          "the one verified-skill store")}


def _skills_proposals_reject(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import ST_REFUTED, verified_skills_store

    _sk_require_confirm(params, "skills.proposals.reject")
    skill_id = _sk_require_id(params)
    store = verified_skills_store(cfg)
    rec = _sk_store_call(store.apply_verdict, skill_id, decision=ST_REFUTED,
                         requested_by=_sk_actor(params),
                         reason=_sk_str(params, "reason"))
    return {"state": "OK", "code": SKILLS_PROPOSALS_REJECT_LOCAL_VERDICT,
            "skill": rec.to_dict(),
            "qubit_counterpart": ("voicebridge.verified_skills."
                                  "VerifiedSkillStore.apply_verdict("
                                  "decision='refuted')"),
            "authority": ("owner-gated refutation on the one verified-skill "
                          "store")}


def _skills_proposals_quarantine(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import verified_skills_store

    _sk_require_confirm(params, "skills.proposals.quarantine")
    skill_id = _sk_require_id(params)
    store = verified_skills_store(cfg)
    rec = _sk_store_call(store.quarantine_revoked, skill_id)
    return {"state": "OK", "code": SKILLS_PROPOSALS_QUARANTINE_LOCAL,
            "skill": rec.to_dict(),
            "qubit_counterpart": ("voicebridge.verified_skills."
                                  "VerifiedSkillStore.quarantine_revoked"),
            "authority": "canonical verified-skill quarantine transition"}


# ------------------------------------------------------- proposals: drafts
def _skills_proposals_revise(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    payload = params.get("payload") if isinstance(params.get("payload"), dict) \
        else params
    out = SM.handle_skill_mutation(cfg, "create", payload,
                                   confirmation_id=_sk_cid(params),
                                   actor=_sk_actor(params), session="gateway")
    return {**out, "code": SKILLS_PROPOSALS_REVISE_LOCAL_STAGE,
            "mutation_code": out.get("code"),
            "qubit_counterpart": ("voicebridge.verified_skills.mutations "
                                  "(stages a reviewed skill revision on the "
                                  "shared confirmation journal)"),
            "authority": ("the one durable confirmation journal "
                          "(voicebridge.approvals.ApprovalJournal)")}


def _skills_proposals_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...verified_skills import mutations as SM

    payload = params.get("payload") if isinstance(params.get("payload"), dict) \
        else params
    out = SM.handle_skill_mutation(cfg, "install", payload,
                                   confirmation_id=_sk_cid(params),
                                   actor=_sk_actor(params), session="gateway")
    return {**out, "code": SKILLS_PROPOSALS_UPDATE_LOCAL_STAGE,
            "mutation_code": out.get("code"),
            "qubit_counterpart": ("voicebridge.verified_skills.mutations "
                                  "(stages an updated reviewed manifest on the "
                                  "shared confirmation journal)"),
            "authority": ("the one durable confirmation journal "
                          "(voicebridge.approvals.ApprovalJournal)")}


def _skills_proposals_request_revision(cfg: Any,
                                       params: Dict[str, Any]
                                       ) -> Dict[str, Any]:
    from ... import chat_transport as CT

    _sk_require_confirm(params, "skills.proposals.requestRevision")
    session = _sk_str(params, "sessionKey", "session", "sessionId",
                      "session_id", default="qubit")
    instructions = _sk_str(params, "instructions", "message", "text",
                           "revision")
    if not instructions:
        raise GatewayError(_BAD,
                           "instructions are required for a revision request")
    proposal_id = _sk_str(params, "proposalId", "proposal_id", "id")
    store = CT.store(cfg)
    _sk_store_call(store.ensure, session)
    out = store.append(session, "user", instructions,
                       meta={"skillProposalRevision": proposal_id})
    return {"state": "OK",
            "code": SKILLS_PROPOSALS_REQUESTREVISION_LOCAL_SESSION,
            "appended": out, "sessionKey": session,
            "proposalId": proposal_id,
            "qubit_counterpart": ("voicebridge.qubit_session_store."
                                  "QubitSessionStore.append (the next turn "
                                  "consumes the revision instruction)"),
            "authority": ("recorded on the one Qubit session authority; "
                          "upstream forwards to chat.send, which Qubit has no "
                          "separate runtime for")}


# ------------------------------------------------------- proposals: history
def _skills_proposals_events_list(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    from ...state_journal import HashChainJournal
    from ...verified_skills import verified_skills_store

    store = verified_skills_store(cfg)
    proposal_id = _sk_str(params, "proposalId", "proposal_id", "id")
    after = _sk_int(params, "afterSequence", "after_sequence")
    limit = max(1, min(_sk_int(params, "limit", default=100) or 100, 500))
    journal = HashChainJournal(store.journal_path,
                               event_prefix="verified_skills")
    events = []
    for sequence, entry in enumerate(journal.replay_actions()):
        payload = entry.get("payload") or {}
        if proposal_id and str(payload.get("id", "")) != proposal_id:
            continue
        if sequence <= after:
            continue
        events.append({"sequence": sequence, "action": entry.get("action"),
                       "payload": payload})
        if len(events) >= limit:
            break
    return {"state": "OK", "code": SKILLS_PROPOSALS_EVENTS_LOCAL_JOURNAL,
            "events": events, "count": len(events),
            "journal": store.journal_verify(),
            "qubit_counterpart": ("voicebridge.state_journal.HashChainJournal "
                                  "(the verified-skill append-only journal)"),
            "authority": ("voicebridge.verified_skills.VerifiedSkillStore."
                          "journal_verify")}


# --------------------------------------------------------------- update
def _skills_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...permissions import permissions_store

    skill_id = _sk_require_id(params)
    if params.get("apiKey") or params.get("api_key") \
            or isinstance(params.get("env"), dict) and params.get("env"):
        return {"state": "UNAVAILABLE", "code": SKILLS_UPDATE_LOCAL_CONFIG,
                "detail": ("Qubit never accepts a raw skill API key or env "
                           "value; use an env:/file: reference name instead"),
                "qubit_counterpart": ("`qubit auth` credential references "
                                      "(names only)")}
    store = permissions_store(cfg)
    if params.get("enabled") is not None:
        _sk_require_confirm(params, "skills.update")
        enabled = _sk_bool(params.get("enabled"))
        fn = store.enable_skill if enabled else store.disable_skill
        rec = _sk_store_call(fn, skill_id, requested_by=_sk_actor(params),
                             confirmed=True)
        return {"state": "OK", "code": SKILLS_UPDATE_LOCAL_CONFIG,
                "skill": rec.to_dict(),
                "qubit_counterpart": ("voicebridge.permissions."
                                      "PermissionsStore.enable_skill/"
                                      "disable_skill"),
                "authority": ("the canonical permissioned record for the "
                              "installed skill")}
    rec = store.skill(skill_id)
    if rec is None:
        return {"state": "NOT_FOUND", "code": SKILLS_UPDATE_LOCAL_CONFIG,
                "skill_id": skill_id,
                "detail": "no installed skill with that id"}
    return {"state": "OK", "code": SKILLS_UPDATE_LOCAL_CONFIG,
            "skill": rec.to_dict(),
            "qubit_counterpart": ("voicebridge.permissions.PermissionsStore."
                                  "skill"),
            "authority": "the canonical permissioned skill record"}


# ------------------------------------------------------------- typed refusals
def _skills_proposals_evaluate(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED,
            "detail": ("Qubit has no isolated automated proposal evaluator; "
                       "verification is an explicit human owner verdict, never "
                       "a self-evaluation"),
            "operator_prerequisite": ("an isolated proposal-evaluation model "
                                      "runtime (Qubit runs no self-evaluation "
                                      "path)"),
            "qubit_counterpart": ("voicebridge.verified_skills.VerifiedSkill"
                                  "Store.apply_verdict (human owner verdict) + "
                                  "voicebridge.evaluation promotion gate")}


def _skills_proposals_history_scan(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED,
            "detail": ("Qubit runs no session-history scanner that extracts "
                       "pending skill proposals from the transcript"),
            "operator_prerequisite": ("a session-history scan runtime (an "
                                      "isolated model over the transcript) "
                                      "that emits pending proposals"),
            "qubit_counterpart": ("voicebridge.verified_skills.workshop."
                                  "SkillWorkshop.author (authored/quarantined "
                                  "proposals)")}


def _skills_security_verdicts(cfg: Any,
                              params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED,
            "detail": ("upstream fetches security verdicts for installed "
                       "skills from a remote registry; Qubit runs no such "
                       "service and fetches no remote verdicts"),
            "operator_prerequisite": ("a reachable skill security-verdict "
                                      "registry/endpoint and network access"),
            "qubit_counterpart": ("local bundled/verified skill validation "
                                  "(voicebridge.verified_skills._bundled + "
                                  "VerifiedSkillStore)")}


def _skills_upload_begin(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SKILLS_UPLOAD_OPERATOR_REQUIRED,
            "detail": ("Qubit runs no staged chunked skill-archive upload "
                       "store; nothing is staged"),
            "operator_prerequisite": ("a staged chunked upload store/transport "
                                      "for skill archives"),
            "qubit_counterpart": ("voicebridge.verified_skills.mutations "
                                  "(single-shot reviewed manifest on the "
                                  "shared confirmation journal) + "
                                  "verified_skills._transfer snapshot restore")}


def _skills_upload_chunk(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SKILLS_UPLOAD_OPERATOR_REQUIRED,
            "detail": ("no staged upload session exists and Qubit runs no "
                       "chunked upload store; no chunk is stored"),
            "operator_prerequisite": ("a staged chunked upload store/transport "
                                      "for skill archives"),
            "qubit_counterpart": ("voicebridge.verified_skills.mutations "
                                  "(single-shot reviewed manifest on the "
                                  "shared confirmation journal) + "
                                  "verified_skills._transfer snapshot restore")}


def _skills_upload_commit(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SKILLS_UPLOAD_OPERATOR_REQUIRED,
            "detail": ("no uploaded archive can be committed; Qubit installs "
                       "only reviewed local manifests and downloads nothing"),
            "operator_prerequisite": ("a staged chunked upload store/transport "
                                      "for skill archives"),
            "qubit_counterpart": ("voicebridge.verified_skills.mutations "
                                  "(single-shot reviewed manifest on the "
                                  "shared confirmation journal) + "
                                  "verified_skills._transfer snapshot restore")}


def register(registry: Any) -> None:
    """Register the ``skills.*`` extra family handlers on *registry*."""
    registry.register("skills.bins", _skills_bins)
    registry.register("skills.curator.pin", _skills_curator_pin)
    registry.register("skills.curator.unpin", _skills_curator_unpin)
    registry.register("skills.curator.restore", _skills_curator_restore)
    registry.register("skills.proposals.apply", _skills_proposals_apply)
    registry.register("skills.proposals.reject", _skills_proposals_reject)
    registry.register("skills.proposals.quarantine", _skills_proposals_quarantine)
    registry.register("skills.proposals.revise", _skills_proposals_revise)
    registry.register("skills.proposals.update", _skills_proposals_update)
    registry.register("skills.proposals.requestRevision",
                      _skills_proposals_request_revision)
    registry.register("skills.proposals.events.list",
                      _skills_proposals_events_list)
    registry.register("skills.update", _skills_update)
    registry.register("skills.proposals.evaluate", _skills_proposals_evaluate)
    registry.register("skills.proposals.historyScan",
                      _skills_proposals_history_scan)
    registry.register("skills.securityVerdicts", _skills_security_verdicts)
    registry.register("skills.upload.begin", _skills_upload_begin)
    registry.register("skills.upload.chunk", _skills_upload_chunk)
    registry.register("skills.upload.commit", _skills_upload_commit)
