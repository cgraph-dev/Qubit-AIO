"""Journal-backed, risk-classified skill mutation contract (dashboard sweep).

The dashboard skill flows (create a skill, install a reviewed hub manifest,
remove a skill) previously answered a *staged confirmation* envelope with an
empty ``confirmation_id`` because the check was a local boolean, not the shared
durable confirmation authority. The pending-approvals banner could not settle
those records, so the UI could never complete the mutation.

This module is the canonical backend contract the compat route layer should
call. It provides:

* :func:`stage_skill_mutation` -- validate the exact request (including a real
  license check), classify its risk, and mint a **single-use durable
  confirmation** on the same ``approvals.jsonl`` journal the CLI/voice/classic
  dashboard use. The ``needs-confirm`` envelope always carries a populated
  ``confirmation_id``.
* :func:`settle_skill_mutation` -- consume that id exactly once and apply the
  mutation idempotently through the same ``PermissionsStore`` the CLI/voice use.
* :func:`handle_skill_mutation` -- one route-friendly entry point that settles
  when a ``confirmation_id`` is supplied and stages otherwise. A bare boolean
  ``confirm`` is *not* sufficient to settle; the durable id is.

Nothing here imports, evaluates, fetches, or executes skill content. The
journal payload stores only the normalized manifest metadata (never raw skill
content); the summary and audit text are redacted.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ._models import VerifiedSkillError, is_human_requester

SKILL_MUTATION_CAPABILITY = "skill.mutation"
MUTATION_SCHEMA = "qubit-skill-mutation/1"
DEFAULT_TTL_S = 300.0
MAX_TTL_S = 86400.0

# Typed codes returned in the ``needs-confirm``/failed envelopes. These are the
# route layer's contract, stable across the CLI/voice/dashboard surfaces.
CONFIRM_CODE = "CONFIRM_REQUIRED"
LICENSE_CODE = "LICENSE_REQUIRED"
INVALID_CODE = "SKILL_INVALID"
MALFORMED_CODE = "MALFORMED"
NOT_FOUND_CODE = "SKILL_NOT_FOUND"
CONFLICT_CODE = "SKILL_EXISTS"
ESTOP_CODE = "ESTOP_LATCHED"
ACTOR_CODE = "ACTOR_REFUSED"
REPLAYED_CODE = "CONFIRMATION_SETTLED"
EXPIRED_CODE = "CONFIRMATION_EXPIRED"
UNKNOWN_CODE = "CONFIRMATION_UNKNOWN"
SESSION_CODE = "SESSION_MISMATCH"
STAGE_CODE = "CONFIRM_STAGE_ERROR"

MUTATION_ACTIONS = ("create", "install", "remove")

# Risk classification drives the typed confirmation requirement. Every mutation
# needs a durable human confirmation; the label tells the surface how to word
# and rate the request.
RISK_REVERSIBLE = "reversible"
RISK_ELEVATED = "elevated"
RISK_DESTRUCTIVE = "destructive"
MUTATION_RISK = {
    "create": RISK_ELEVATED,        # installs a staged manifest (capability file)
    "install": RISK_ELEVATED,       # installs a reviewed hub manifest
    "remove": RISK_DESTRUCTIVE,     # deletes the installed skill record
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")


class SkillMutationError(VerifiedSkillError):
    """Typed skill-mutation failure carrying a stable ``code``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    try:
        from ..redaction import redact_text

        return str(redact_text(str(text or "")))
    except Exception:
        return str(text or "")


def _journal(cfg: Any):
    from ..approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=DEFAULT_TTL_S)


def _estop(cfg: Any) -> bool:
    try:
        from ..plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True   # fail closed


def _fail(code: str, detail: str, http: int = 400,
          state: str = "failed", **extra: Any) -> Dict[str, Any]:
    out = {"ok": False, "state": state, "code": code,
           "detail": _redact(detail)[:400], "http": int(http)}
    out.update(extra)
    return out


def mutation_risk(action: str) -> str:
    """Typed risk for one skill mutation action (defaults to elevated)."""
    return MUTATION_RISK.get(str(action or "").strip().lower(), RISK_ELEVATED)


def _slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9._-]+", "-", str(text or "").strip().lower())
    clean = clean.strip("-._")
    if clean and not clean[0].isalpha():
        clean = "s-" + clean
    return clean[:63]


def _skill_id(value: Any) -> str:
    clean = _slug(str(value or ""))
    return clean if _ID_RE.match(clean) else ""


def _manifest_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a manifest dict from a UI-style ``{name, content, license}`` body.

    A license is mandatory: the install contract validates the SPDX field and a
    missing one is a typed :data:`LICENSE_CODE` refusal, never a fabricated
    default. ``content`` (if present) is hashed and then dropped -- only the
    manifest metadata is persisted.
    """
    name = str(payload.get("name") or payload.get("skill_id") or "").strip()
    skill_id = _skill_id(payload.get("skill_id") or name)
    if not skill_id:
        raise SkillMutationError(
            MALFORMED_CODE,
            "skill name is required and must match ^[a-z][a-z0-9_.-]{2,63}$")
    license_id = str(payload.get("license", "") or "").strip()
    if not license_id:
        raise SkillMutationError(
            LICENSE_CODE,
            "license required: this skill manifest needs a license identifier "
            "(SPDX, e.g. 'MIT' or 'Apache-2.0') before it can be installed")
    content_hash = str(payload.get("content_hash", "") or "").strip().lower()
    content = payload.get("content")
    if not content_hash and isinstance(content, str) and content:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    capabilities = payload.get("capabilities") or [("skill." + skill_id)[:64]]
    scopes = payload.get("scopes") or ["skill:read"]
    return {
        "skill_id": skill_id,
        "version": str(payload.get("version") or "0.1.0"),
        "name": name or skill_id,
        "description": str(payload.get("description")
                           or payload.get("category") or name or skill_id),
        "source": str(payload.get("source") or "qubit-dashboard"),
        "revision": str(payload.get("revision") or "1"),
        "license": license_id,
        "content_hash": content_hash,
        "capabilities": list(capabilities),
        "scopes": list(scopes),
        "tier": payload.get("tier") or "reversible",
        "sensitivity": str(payload.get("sensitivity") or "internal"),
        "approval_policy": str(payload.get("approval_policy") or "confirm"),
        "timeout_s": float(payload.get("timeout_s") or 30),
        "resource_limits": dict(payload.get("resource_limits") or {}),
        "audit_events": list(payload.get("audit_events") or ["skill.install"]),
        "compatibility": str(payload.get("compatibility") or ""),
        "rollback": str(payload.get("rollback") or "remove the skill"),
    }


def _spec_from_manifest(manifest: Any):
    """Validate a manifest dict into ``permissions.SkillManifest``."""
    from ..capabilities import Tier
    from ..permissions import SkillManifest, SkillValidationError

    if not isinstance(manifest, dict):
        raise SkillMutationError(MALFORMED_CODE,
                                 "skill install needs a manifest object")
    if not str(manifest.get("license", "") or "").strip():
        raise SkillMutationError(
            LICENSE_CODE,
            "license required: the skill manifest must declare a license "
            "identifier before it can be installed")
    tier = manifest.get("tier", "reversible")
    try:
        return SkillManifest(
            skill_id=str(manifest.get("skill_id", manifest.get("name", ""))
                         or ""),
            version=str(manifest.get("version", "0.0.0") or "0.0.0"),
            name=str(manifest.get("name", manifest.get("skill_id", "")) or ""),
            description=str(manifest.get("description", "") or ""),
            source=str(manifest.get("source", "qubit-dashboard")
                       or "qubit-dashboard"),
            revision=str(manifest.get("revision", "1") or "1"),
            license=str(manifest.get("license", "") or ""),
            content_hash=str(manifest.get("content_hash", "") or ""),
            capabilities=tuple(manifest.get("capabilities", []) or []),
            scopes=tuple(manifest.get("scopes", []) or []),
            tier=tier if isinstance(tier, Tier) else Tier(str(tier)),
            sensitivity=str(manifest.get("sensitivity", "internal")
                            or "internal"),
            approval_policy=str(manifest.get("approval_policy", "confirm")
                                or "confirm"),
            timeout_s=float(manifest.get("timeout_s", 30) or 30),
            resource_limits=dict(manifest.get("resource_limits", {}) or {}),
            audit_events=tuple(manifest.get("audit_events", ["skill.install"])
                               or ["skill.install"]),
            compatibility=str(manifest.get("compatibility", "") or ""),
            rollback=str(manifest.get("rollback", "remove the skill")
                         or "remove the skill"))
    except SkillValidationError as exc:
        raise SkillMutationError(INVALID_CODE, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise SkillMutationError(INVALID_CODE, str(exc)) from exc


def _already_applied(cfg: Any, action: str,
                     exec_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Idempotency pre-check: return an executed envelope if already applied."""
    from ..permissions import permissions_store

    store = permissions_store(cfg)
    if action in ("create", "install"):
        spec = _spec_from_manifest(exec_payload.get("manifest"))
        rec = store.skill(spec.skill_id)
        if rec is None:
            return None
        if (rec.manifest.content_hash == spec.content_hash
                and rec.manifest.version == spec.version):
            return {"ok": True, "state": "executed", "code": "OK",
                    "action": action, "skill_id": spec.skill_id,
                    "status": rec.status, "already": True, "http": 200,
                    "detail": "skill already installed at this version"}
        return _fail(
            CONFLICT_CODE,
            f"skill {spec.skill_id!r} is already installed with a different "
            "manifest; remove it before installing another version", 409)
    skill_id = str(exec_payload.get("skill_id", "") or "")
    if store.skill(skill_id) is None:
        return {"ok": True, "state": "executed", "code": "OK", "action": action,
                "skill_id": skill_id, "removed": True, "already": True,
                "http": 200, "detail": "skill already absent"}
    return None


def _detail(action: str, target: str, risk: str) -> str:
    verb = {"create": "create", "install": "install",
            "remove": "remove"}.get(action, action)
    return (f"skill {verb} {target!r} is a {risk} mutation and requires a "
            "durable human confirmation")


def stage_skill_mutation(cfg: Any, action: str,
                         payload: Optional[Dict[str, Any]] = None, *,
                         manifest: Any = None, identifier: str = "",
                         actor: str = "dashboard:user",
                         session: str = "dashboard",
                         ttl_s: float = DEFAULT_TTL_S) -> Dict[str, Any]:
    """Validate and stage one skill mutation on the shared journal.

    Returns a ``needs-confirm`` envelope (``state='needs-confirm'``,
    ``code='CONFIRM_REQUIRED'``, populated ``confirmation_id``, ``http=409``)
    or an ``executed``/failed envelope. A malformed request, a missing license,
    or a conflicting install is a typed 400/409 and stages nothing.
    """
    action = str(action or "").strip().lower()
    if not is_human_requester(actor):
        return _fail(ACTOR_CODE,
                     "skill mutations require a confirmed human actor", 403)
    if action not in MUTATION_ACTIONS:
        return _fail(MALFORMED_CODE,
                     f"unknown skill action {action or '<empty>'!r}; expected "
                     f"one of {MUTATION_ACTIONS}", 400)
    if _estop(cfg):
        return _fail(ESTOP_CODE,
                     "emergency stop is latched; reset it before changing "
                     "skills", 403)
    work = dict(payload or {})
    if identifier and not work.get("skill_id") and not work.get("name"):
        work["skill_id"] = str(identifier)
    try:
        if action == "remove":
            skill_id = _skill_id(work.get("skill_id") or work.get("name")
                                 or identifier)
            if not skill_id:
                raise SkillMutationError(MALFORMED_CODE,
                                         "skill_id is required")
            exec_payload = {"skill_id": skill_id}
            target = skill_id
        else:
            raw = manifest if isinstance(manifest, dict) \
                else work.get("manifest")
            if not isinstance(raw, dict):
                raw = _manifest_from_payload(work)
            spec = _spec_from_manifest(raw)
            exec_payload = {"manifest": spec.to_dict()}
            target = spec.skill_id
    except SkillMutationError as exc:
        return _fail(exc.code, exc.detail, 400)

    pre = _already_applied(cfg, action, exec_payload)
    if pre is not None:
        return pre

    risk = mutation_risk(action)
    detail = _detail(action, target, risk)
    try:
        journal = _journal(cfg)
        approval = journal.create(
            SKILL_MUTATION_CAPABILITY, detail,
            ttl_s=max(1.0, min(float(ttl_s), MAX_TTL_S)),
            schema=MUTATION_SCHEMA, action=action, payload=exec_payload,
            risk=risk, actor=str(actor)[:64], session=str(session)[:64])
    except Exception as exc:  # noqa: BLE001 - typed at the boundary
        return _fail(STAGE_CODE,
                     "could not stage confirmation: " + _redact(str(exc))[:160],
                     500)
    return {"ok": False, "state": "needs-confirm", "code": CONFIRM_CODE,
            "confirmation_id": approval.id,
            "capability": SKILL_MUTATION_CAPABILITY,
            "action": action, "risk": risk, "detail": detail, "http": 409,
            "ttl_s": float(ttl_s),
            "qubit_counterpart": "pending-approvals banner "
                                 "(POST /api/ops/confirm) + CLI "
                                 "`qubit skills install/remove` + voice yes"}


def apply_skill_mutation(cfg: Any, action: str, exec_payload: Dict[str, Any],
                         *, actor: str = "dashboard:user") -> Dict[str, Any]:
    """Apply one already-authorized skill mutation (no journal access)."""
    from ..permissions import (SkillManifest, SkillValidationError,
                               permissions_store)

    action = str(action or "").strip().lower()
    store = permissions_store(cfg)
    if action in ("create", "install"):
        try:
            spec = SkillManifest.from_dict(dict(exec_payload.get("manifest")
                                                or {}))
        except (SkillValidationError, ValueError, TypeError, KeyError) as exc:
            return _fail(INVALID_CODE, str(exc), 400)
        try:
            rec = store.install_skill(spec, requested_by=actor, confirmed=True)
        except SkillValidationError as exc:
            return _fail(INVALID_CODE, _redact(str(exc))[:300], 400)
        except PermissionError:
            existing = store.skill(spec.skill_id)
            if existing is not None \
                    and existing.manifest.content_hash == spec.content_hash:
                return {"ok": True, "state": "executed", "code": "OK",
                        "action": action, "skill_id": spec.skill_id,
                        "status": existing.status, "already": True, "http": 200}
            return _fail(CONFLICT_CODE,
                         f"skill {spec.skill_id!r} is already installed", 409)
        return {"ok": True, "state": "executed", "code": "OK", "action": action,
                "skill_id": rec.skill_id, "status": rec.status, "http": 200,
                "qubit_counterpart": "CLI `qubit skills install <manifest>`"}
    if action == "remove":
        skill_id = str(exec_payload.get("skill_id", "") or "")
        try:
            rec = store.remove_skill(skill_id, requested_by=actor,
                                     confirmed=True)
        except PermissionError as exc:
            if "unknown skill" in str(exc):
                return {"ok": True, "state": "executed", "code": "OK",
                        "action": action, "skill_id": skill_id, "removed": True,
                        "already": True, "http": 200}
            return _fail(INVALID_CODE, _redact(str(exc))[:300], 400)
        return {"ok": True, "state": "executed", "code": "OK", "action": action,
                "skill_id": rec.skill_id, "removed": True, "http": 200,
                "qubit_counterpart": "CLI `qubit skills remove <id>`"}
    return _fail(MALFORMED_CODE, f"unknown skill action {action!r}", 400)


def settle_skill_mutation(cfg: Any, confirmation_id: str, *,
                          actor: str = "dashboard:user",
                          session: str = "dashboard") -> Dict[str, Any]:
    """Consume one staged confirmation exactly once and apply the mutation."""
    from ..approvals import (ABORTED, COMPLETE, DENIED, EXECUTING, IN_PROGRESS)

    if not is_human_requester(actor):
        return _fail(ACTOR_CODE,
                     "skill mutations require a confirmed human actor", 403)
    if _estop(cfg):
        return _fail(ESTOP_CODE,
                     "emergency stop is latched; reset it before changing "
                     "skills", 403)
    cid = str(confirmation_id or "").strip()
    try:
        journal = _journal(cfg)
        item = journal.get(cid)
    except Exception as exc:  # noqa: BLE001
        return _fail(STAGE_CODE, "confirmation journal unavailable: "
                     + _redact(str(exc))[:120], 500)
    if item is None or item.capability != SKILL_MUTATION_CAPABILITY:
        return _fail(UNKNOWN_CODE,
                     "unknown or wrong-capability confirmation id", 409)
    meta = item.meta if isinstance(item.meta, dict) else {}
    expected_session = str(meta.get("session", "dashboard") or "dashboard")
    if expected_session != str(session):
        return _fail(SESSION_CODE,
                     "that confirmation belongs to another session", 403)
    if item.state in (COMPLETE, ABORTED, DENIED):
        return _fail(REPLAYED_CODE,
                     "that confirmation was already settled, denied, or "
                     "expired", 409)
    if item.decision not in (None, "approve"):
        return _fail(REPLAYED_CODE, "that confirmation was denied", 409)
    if item.state == IN_PROGRESS:
        if item.expires_at <= time.time():
            try:
                journal.abort(cid)
            except Exception:
                pass
            return _fail(EXPIRED_CODE, "that confirmation has expired", 409)
        item = journal.respond(cid, "approve")
    if item.state != EXECUTING:
        return _fail(UNKNOWN_CODE, "that confirmation is not executable", 409)
    journal.finish(cid)
    return apply_skill_mutation(cfg, str(meta.get("action", "")),
                                meta.get("payload")
                                if isinstance(meta.get("payload"), dict) else {},
                                actor=actor)


def settle_skill_mutation_if_owned(cfg: Any, confirmation_id: str, *,
                                   actor: str = "dashboard:user",
                                   session: str = "dashboard"
                                   ) -> Optional[Dict[str, Any]]:
    """Settle an id owned by this module; ``None`` when it belongs elsewhere.

    The route layer's ``/api/ops/confirm`` can try this and fall through to the
    shared ``dashboard_ops.confirm_op`` for confirmation ids it does not own.
    """
    try:
        item = _journal(cfg).get(str(confirmation_id or "").strip())
    except Exception:
        return None
    if item is None or item.capability != SKILL_MUTATION_CAPABILITY:
        return None
    return settle_skill_mutation(cfg, confirmation_id, actor=actor,
                                 session=session)


def handle_skill_mutation(cfg: Any, action: str,
                          payload: Optional[Dict[str, Any]] = None, *,
                          confirm: bool = False, confirmation_id: str = "",
                          manifest: Any = None, identifier: str = "",
                          actor: str = "dashboard:user",
                          session: str = "dashboard") -> Dict[str, Any]:
    """Route-friendly entry point: settle with an id, otherwise stage.

    ``confirm`` is accepted for wire-shape compatibility but a bare boolean
    cannot settle a staged mutation; only the durable ``confirmation_id`` does.
    """
    cid = str(confirmation_id or "").strip()
    if cid:
        return settle_skill_mutation(cfg, cid, actor=actor, session=session)
    return stage_skill_mutation(cfg, action, payload, manifest=manifest,
                                identifier=identifier, actor=actor,
                                session=session)


def pending_skill_mutations(cfg: Any) -> list:
    """Pending skill mutations on the shared journal (for a banner/debug view)."""
    try:
        journal = _journal(cfg)
        return [{"id": a.id, "capability": a.capability,
                 "summary": _redact(a.summary)[:300],
                 "state": a.state, "expires_at": a.expires_at,
                 "action": (a.meta or {}).get("action"),
                 "risk": (a.meta or {}).get("risk")}
                for a in journal.pending()
                if a.capability == SKILL_MUTATION_CAPABILITY]
    except Exception:
        return []


__all__ = [
    "SKILL_MUTATION_CAPABILITY", "MUTATION_SCHEMA", "MUTATION_ACTIONS",
    "MUTATION_RISK", "DEFAULT_TTL_S", "SkillMutationError",
    "CONFIRM_CODE", "LICENSE_CODE", "INVALID_CODE", "MALFORMED_CODE",
    "NOT_FOUND_CODE", "CONFLICT_CODE", "ESTOP_CODE", "ACTOR_CODE",
    "REPLAYED_CODE", "EXPIRED_CODE", "UNKNOWN_CODE", "SESSION_CODE",
    "STAGE_CODE", "RISK_REVERSIBLE", "RISK_ELEVATED", "RISK_DESTRUCTIVE",
    "mutation_risk", "stage_skill_mutation", "settle_skill_mutation",
    "settle_skill_mutation_if_owned", "apply_skill_mutation",
    "handle_skill_mutation", "pending_skill_mutations",
]
