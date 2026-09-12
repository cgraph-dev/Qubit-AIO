"""Packet 7E: permission profiles, scoped grants, and versioned skill manifests.

Contract layer for future websites, desktop apps, editors, browser operations,
and marketplace packages. Two explicit permission modes:

- ``restricted`` (default): only explicitly granted capabilities and scopes work.
- ``unrestricted`` (opt-in): pre-authorizes routine NON-destructive
  (read-only/reversible) work inside user-selected scopes, but NEVER bypasses
  hard-deny capability patterns, schema validation, redaction, audit,
  isolation/resource limits, loopback-only boundaries, or confirmation for
  sending, publishing, purchasing, deleting, credential/account/security
  changes, or system-wide destructive operations.

Model output, page text, tool results, skill instructions, and generated plans
can never change the mode or widen a grant: decisions take only typed
capability/scope/args inputs and consult the durable store.

This module executes nothing and opens no browser/service. It is the typed
state + policy core; the CLI (cli.py), dashboard (webui.py), and tests consume
it. State persists as a private atomic JSON file with timestamped backups and
an append-style bounded audit log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .capabilities import (CapabilityRegistry, ConfirmationDenied,
                           ConfirmationStore, Tier)
from .logging_setup import log_event

MODE_RESTRICTED = "restricted"
MODE_UNRESTRICTED = "unrestricted"
MODES = (MODE_RESTRICTED, MODE_UNRESTRICTED)

SENSITIVITIES = ("public", "internal", "personal", "secret")
APPROVAL_POLICIES = ("auto", "confirm", "deny")

SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
SCOPE_RE = re.compile(r"^[a-z][a-z0-9_./:-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

AUDIT_CAP = 200

# Capability-name patterns that UNRESTRICTED mode never auto-approves. These
# touch sensitive/irreversible areas even when the tier is read-only/reversible
# by name; they always require explicit confirmation in either mode.
HARD_DENY_AUTO = (
    r"(^|\.)(send|reply|forward|react)(\.|$)",
    r"(^|\.)(publish|post|share|broadcast|tweet)(\.|$)",
    r"(^|\.)(purchase|buy|pay|order|checkout|payment|subscribe|transfer|refund)(\.|$)",
    r"(^|\.)(delete|remove|erase|wipe|purge|uninstall|drop|truncate|overwrite)(\.|$)",
    r"(^|\.)(credential|password|token|secret|account|session|security|2fa|mfa)(\.|$)",
    r"(^|\.)(system|shutdown|poweroff|reboot|suspend|halt|format|mkfs)(\.|$)",
)


class PermissionError(Exception):
    """Typed failure for the permission/skill layer."""


class SkillValidationError(PermissionError):
    """A skill manifest failed validation and was NOT registered."""


class ConfirmationRequiredError(PermissionError):
    """The action needs explicit user confirmation before it may persist."""


class PermissionDeniedError(PermissionError):
    """A policy decision denied the request (with a stable code)."""


def is_hard_deny_auto(capability: str) -> Optional[str]:
    """Return the matching hard-deny pattern for a capability name, or None."""
    low = capability.lower()
    for pat in HARD_DENY_AUTO:
        if re.search(pat, low):
            return pat
    return None


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Skill manifest
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SkillManifest:
    """Versioned, validated contract for a website/app/editor/browser skill."""

    skill_id: str
    version: str
    name: str
    description: str
    source: str                # repository URL / publisher
    revision: str              # pinned revision / commit / artifact revision
    license: str               # SPDX identifier
    content_hash: str          # sha256 of the skill payload
    capabilities: Tuple[str, ...]
    scopes: Tuple[str, ...]
    tier: Tier
    sensitivity: str
    approval_policy: str       # auto | confirm | deny
    timeout_s: float
    resource_limits: Dict[str, float]
    audit_events: Tuple[str, ...]
    compatibility: str
    rollback: str              # documented removal/rollback behavior

    def __post_init__(self) -> None:
        if not SKILL_ID_RE.match(self.skill_id or ""):
            raise SkillValidationError(f"bad skill_id: {self.skill_id!r}")
        if not VERSION_RE.match(self.version or ""):
            raise SkillValidationError(f"bad version: {self.version!r}")
        if not self.name or not self.name.strip():
            raise SkillValidationError("name required")
        if not self.source or not self.source.strip():
            raise SkillValidationError("source required")
        if not self.revision or not self.revision.strip():
            raise SkillValidationError("revision required")
        if not self.license or not self.license.strip():
            raise SkillValidationError("license required")
        if not SHA256_RE.match(self.content_hash or ""):
            raise SkillValidationError("content_hash must be a sha256 hex digest")
        if not self.capabilities:
            raise SkillValidationError("capabilities required (>=1)")
        for c in self.capabilities:
            if not re.match(r"^[a-z][a-z0-9_.-]{2,63}$", c):
                raise SkillValidationError(f"bad capability name: {c!r}")
        if not self.scopes:
            raise SkillValidationError("scopes required (>=1)")
        for s in self.scopes:
            if not SCOPE_RE.match(s):
                raise SkillValidationError(f"bad scope: {s!r}")
        if not isinstance(self.tier, Tier):
            raise SkillValidationError("tier must be a Tier")
        if self.sensitivity not in SENSITIVITIES:
            raise SkillValidationError(
                f"sensitivity must be one of {SENSITIVITIES}")
        if self.approval_policy not in APPROVAL_POLICIES:
            raise SkillValidationError(
                f"approval_policy must be one of {APPROVAL_POLICIES}")
        if not (0 < float(self.timeout_s) <= 3600):
            raise SkillValidationError("timeout_s outside (0, 3600]")
        if not isinstance(self.resource_limits, dict):
            raise SkillValidationError("resource_limits must be a mapping")
        for k, v in self.resource_limits.items():
            if not isinstance(v, (int, float)) or not (0 <= float(v)):
                raise SkillValidationError(
                    f"resource_limits[{k!r}] must be a non-negative number")
        if not self.rollback or not self.rollback.strip():
            raise SkillValidationError("rollback/removal behavior required")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SkillManifest":
        return cls(
            skill_id=str(d["skill_id"]),
            version=str(d["version"]),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            source=str(d.get("source", "")),
            revision=str(d.get("revision", "")),
            license=str(d.get("license", "")),
            content_hash=str(d.get("content_hash", "")),
            capabilities=tuple(d.get("capabilities", ())),
            scopes=tuple(d.get("scopes", ())),
            tier=Tier(d.get("tier", Tier.READ_ONLY.value)),
            sensitivity=str(d.get("sensitivity", "personal")),
            approval_policy=str(d.get("approval_policy", "auto")),
            timeout_s=float(d.get("timeout_s", 30.0)),
            resource_limits=dict(d.get("resource_limits", {})),
            audit_events=tuple(d.get("audit_events", ())),
            compatibility=str(d.get("compatibility", "")),
            rollback=str(d.get("rollback", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


# ---------------------------------------------------------------------------
# Durable store (mode, grants, skills, audit) — atomic + audited
# ---------------------------------------------------------------------------
@dataclass
class Grant:
    id: str
    capability: str
    scopes: frozenset
    issued_at: float
    expires_at: Optional[float]
    source: str = "user"
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "capability": self.capability,
                "scopes": sorted(self.scopes), "issued_at": self.issued_at,
                "expires_at": self.expires_at, "source": self.source,
                "note": self.note}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Grant":
        return cls(id=str(d["id"]), capability=str(d["capability"]),
                   scopes=frozenset(d.get("scopes", ())),
                   issued_at=float(d.get("issued_at", 0.0)),
                   expires_at=d.get("expires_at"),
                   source=str(d.get("source", "user")),
                   note=str(d.get("note", "")))

    def is_active(self, now: Optional[float] = None) -> bool:
        if self.expires_at is not None and \
                (now if now is not None else _now()) >= self.expires_at:
            return False
        return True


@dataclass
class SkillRecord:
    skill_id: str
    manifest: SkillManifest
    status: str                 # staged | active | disabled
    installed_at: float
    source_path: str = ""
    installed_by: str = "user"
    rollback_to: Optional[str] = None   # prior status for rollback

    def to_dict(self) -> Dict[str, Any]:
        return {"skill_id": self.skill_id, "manifest": self.manifest.to_dict(),
                "status": self.status, "installed_at": self.installed_at,
                "source_path": self.source_path, "installed_by": self.installed_by,
                "rollback_to": self.rollback_to}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SkillRecord":
        return cls(skill_id=str(d["skill_id"]),
                   manifest=SkillManifest.from_dict(d["manifest"]),
                   status=str(d.get("status", "staged")),
                   installed_at=float(d.get("installed_at", 0.0)),
                   source_path=str(d.get("source_path", "")),
                   installed_by=str(d.get("installed_by", "user")),
                   rollback_to=d.get("rollback_to"))


class PermissionsStore:
    """Persistent, atomic, audited permission + skill state.

    The store NEVER auto-grants: installs are staged disabled, mode is
    restricted by default, and every mutation writes a timestamped backup,
    persists atomically (tmp + os.replace), and appends an audit event.
    """

    def __init__(self, path: Path | str, *,
                 private_files: bool = True) -> None:
        self._path = Path(path)
        self._private_files = bool(private_files)
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {
            "schema": 1,
            "mode": MODE_RESTRICTED,
            "mode_scopes": [],
            "mode_expires_at": None,
            "mode_set_at": None,
            "grants": [],
            "skills": {},
            "audit": [],
        }
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text())
                if isinstance(loaded, dict) and loaded.get("mode") in MODES:
                    self._state = loaded
            except Exception:
                pass   # corrupt file -> fail closed to defaults (restricted)
        if self._private_files:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self._path.parent, 0o700)
            except OSError:
                pass

    # ------------------------------------------------------------ accessors
    @property
    def path(self) -> Path:
        return self._path

    def mode(self) -> Tuple[str, List[str], Optional[float]]:
        with self._lock:
            m = self._state.get("mode", MODE_RESTRICTED)
            expires = self._state.get("mode_expires_at")
            if expires is not None and _now() >= float(expires):
                # expired convenience mode fails CLOSED back to restricted
                return MODE_RESTRICTED, [], expires
            return (m, list(self._state.get("mode_scopes", [])), expires)

    def grants(self) -> List[Grant]:
        with self._lock:
            return [Grant.from_dict(g) for g in self._state.get("grants", [])]

    def skills(self) -> List[SkillRecord]:
        with self._lock:
            return [SkillRecord.from_dict(r)
                    for r in self._state.get("skills", {}).values()]

    def skill(self, skill_id: str) -> Optional[SkillRecord]:
        with self._lock:
            rec = self._state.get("skills", {}).get(skill_id)
            return SkillRecord.from_dict(rec) if rec else None

    def audit(self, limit: int = AUDIT_CAP) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._state.get("audit", []))[-int(limit):]

    # ------------------------------------------------------------ persistence
    def _backup(self) -> Optional[Path]:
        try:
            bak = self._path.with_name(
                f"{self._path.name}.{int(_now())}.bak")
            bak.write_text(json.dumps(self._state, indent=2))
            if self._private_files:
                os.chmod(bak, 0o600)
            return bak
        except OSError:
            return None

    def _save(self) -> Path:
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                   prefix=f".{self._path.name}.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._state, fh, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if self._private_files:
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        return self._path

    def _audit(self, action: str, actor: str, detail: Dict[str, Any]) -> None:
        audit = self._state.setdefault("audit", [])
        audit.append({"ts": _now(), "action": action, "actor": actor,
                      "detail": detail})
        if len(audit) > AUDIT_CAP:
            del audit[:len(audit) - AUDIT_CAP]
        log_event(f"permissions.{action}", actor=actor, detail=detail)

    # ------------------------------------------------------------ mode
    def set_mode(self, mode: str, *, requested_by: str, confirmed: bool,
                 scopes: Tuple[str, ...] = (), ttl_s: Optional[float] = None,
                 ) -> Dict[str, Any]:
        """Set the permission mode. Requires explicit user confirmation.

        ``unrestricted`` is opt-in, time-bounded (ttl_s) or resettable, and
        scoped to ``scopes``. An expired mode fails closed to restricted.
        """
        if mode not in MODES:
            raise PermissionError(f"invalid mode {mode!r} (choose {MODES})")
        if mode == MODE_UNRESTRICTED and not scopes:
            raise PermissionError(
                "unrestricted mode requires at least one user-selected scope")
        if not confirmed:
            raise ConfirmationRequiredError(
                "mode change requires explicit confirmation")
        with self._lock:
            self._backup()
            expires = _now() + float(ttl_s) if ttl_s is not None else None
            if mode == MODE_UNRESTRICTED:
                self._state["mode"] = mode
                self._state["mode_scopes"] = sorted(set(scopes))
                self._state["mode_expires_at"] = expires
                self._state["mode_set_at"] = _now()
            else:
                self._state["mode"] = MODE_RESTRICTED
                self._state["mode_scopes"] = []
                self._state["mode_expires_at"] = None
                self._state["mode_set_at"] = _now()
            self._audit("mode.set", requested_by,
                        {"mode": mode, "scopes": sorted(scopes),
                         "expires_at": expires})
            self._save()
            return {"state": "OK", "mode": mode, "scopes": sorted(scopes),
                    "expires_at": expires}

    # ------------------------------------------------------------ grants
    def grant(self, capability: str, *, requested_by: str, confirmed: bool,
              scopes: Tuple[str, ...], expires_at: Optional[float] = None,
              ttl_s: Optional[float] = None, note: str = "") -> Grant:
        if not capability or not capability.strip():
            raise PermissionError("capability required")
        if not scopes:
            raise PermissionError("grant requires at least one scope")
        if not confirmed:
            raise ConfirmationRequiredError("grant requires explicit confirmation")
        exp = expires_at
        if exp is None and ttl_s is not None:
            exp = _now() + float(ttl_s)
        with self._lock:
            self._backup()
            g = Grant(id=_new_id("grant"), capability=capability,
                      scopes=frozenset(scopes), issued_at=_now(),
                      expires_at=exp, source=requested_by, note=note)
            self._state.setdefault("grants", []).append(g.to_dict())
            self._audit("grant.add", requested_by,
                        {"capability": capability, "scopes": sorted(scopes),
                         "expires_at": exp})
            self._save()
            return g

    def revoke(self, grant_id: str, *, requested_by: str,
               confirmed: bool) -> Grant:
        if not confirmed:
            raise ConfirmationRequiredError("revoke requires explicit confirmation")
        with self._lock:
            grants = self._state.get("grants", [])
            for i, g in enumerate(grants):
                if g.get("id") == grant_id:
                    self._backup()
                    removed = grants.pop(i)
                    self._audit("grant.revoke", requested_by,
                                {"grant_id": grant_id,
                                 "capability": removed.get("capability")})
                    self._save()
                    return Grant.from_dict(removed)
        raise PermissionError(f"unknown grant {grant_id}")

    def reset(self, *, requested_by: str, confirmed: bool) -> Dict[str, Any]:
        """Return to restricted mode and revoke every grant (with backup)."""
        if not confirmed:
            raise ConfirmationRequiredError("reset requires explicit confirmation")
        with self._lock:
            self._backup()
            n = len(self._state.get("grants", []))
            self._state["grants"] = []
            self._state["mode"] = MODE_RESTRICTED
            self._state["mode_scopes"] = []
            self._state["mode_expires_at"] = None
            self._state["mode_set_at"] = _now()
            self._audit("permissions.reset", requested_by,
                        {"revoked_grants": n})
            self._save()
            return {"state": "OK", "mode": MODE_RESTRICTED,
                    "revoked_grants": n}

    # ------------------------------------------------------------ skills
    def install_skill(self, manifest: SkillManifest, *, requested_by: str,
                      confirmed: bool, source_path: str = "") -> SkillRecord:
        """Validate + stage a skill DISABLED until explicit user enablement."""
        if not confirmed:
            raise ConfirmationRequiredError(
                "skill install requires explicit confirmation")
        with self._lock:
            existing = self._state.get("skills", {}).get(manifest.skill_id)
            if existing is not None:
                raise PermissionError(
                    f"skill {manifest.skill_id} already installed "
                    f"(status={existing.get('status')})")
            self._backup()
            rec = SkillRecord(skill_id=manifest.skill_id, manifest=manifest,
                              status="staged", installed_at=_now(),
                              source_path=source_path, installed_by=requested_by)
            self._state.setdefault("skills", {})[manifest.skill_id] = rec.to_dict()
            self._audit("skill.install", requested_by,
                        {"skill_id": manifest.skill_id,
                         "version": manifest.version,
                         "status": "staged"})
            self._save()
            return rec

    def set_skill_status(self, skill_id: str, status: str, *,
                         requested_by: str, confirmed: bool) -> SkillRecord:
        if status not in ("active", "disabled"):
            raise PermissionError(f"invalid skill status {status!r}")
        if not confirmed:
            raise ConfirmationRequiredError(
                f"skill {status} requires explicit confirmation")
        with self._lock:
            rec = self._state.get("skills", {}).get(skill_id)
            if rec is None:
                raise PermissionError(f"unknown skill {skill_id}")
            self._backup()
            rec["rollback_to"] = rec.get("status")
            rec["status"] = status
            self._audit(f"skill.{status}", requested_by,
                        {"skill_id": skill_id})
            self._save()
            return SkillRecord.from_dict(rec)

    def enable_skill(self, skill_id: str, *, requested_by: str,
                     confirmed: bool) -> SkillRecord:
        return self.set_skill_status(skill_id, "active", requested_by=requested_by,
                                     confirmed=confirmed)

    def disable_skill(self, skill_id: str, *, requested_by: str,
                      confirmed: bool) -> SkillRecord:
        return self.set_skill_status(skill_id, "disabled", requested_by=requested_by,
                                     confirmed=confirmed)

    def remove_skill(self, skill_id: str, *, requested_by: str,
                     confirmed: bool) -> SkillRecord:
        if not confirmed:
            raise ConfirmationRequiredError(
                "skill removal requires explicit confirmation")
        with self._lock:
            rec = self._state.get("skills", {}).get(skill_id)
            if rec is None:
                raise PermissionError(f"unknown skill {skill_id}")
            self._backup()
            del self._state["skills"][skill_id]
            self._audit("skill.remove", requested_by, {"skill_id": skill_id})
            self._save()
            return SkillRecord.from_dict(rec)

    def rollback_skill(self, skill_id: str, *, requested_by: str,
                       confirmed: bool,
                       backup_path: Optional[Path] = None) -> SkillRecord:
        """Restore a skill from a backup snapshot (default: latest .bak)."""
        if not confirmed:
            raise ConfirmationRequiredError(
                "skill rollback requires explicit confirmation")
        with self._lock:
            if backup_path is not None and backup_path.exists():
                snap = json.loads(Path(backup_path).read_text())
                rec = snap.get("skills", {}).get(skill_id)
                if rec is None:
                    raise PermissionError(
                        f"no {skill_id} snapshot in {backup_path}")
                self._state["skills"][skill_id] = rec
                self._audit("skill.rollback", requested_by,
                            {"skill_id": skill_id, "backup": str(backup_path)})
                self._save()
                return SkillRecord.from_dict(rec)
            # default: roll back the previous status (active -> disabled etc.)
            rec = self._state.get("skills", {}).get(skill_id)
            if rec is None:
                raise PermissionError(f"unknown skill {skill_id}")
            prior = rec.get("rollback_to")
            if prior not in ("staged", "active", "disabled"):
                raise PermissionError(f"no rollback state for {skill_id}")
            self._backup()
            rec["status"] = prior
            rec["rollback_to"] = None
            self._audit("skill.rollback", requested_by,
                        {"skill_id": skill_id, "status": prior})
            self._save()
            return SkillRecord.from_dict(rec)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Policy decisions
# ---------------------------------------------------------------------------
DENY_UNKNOWN = "UNKNOWN_CAPABILITY"
DENY_MALFORMED = "MALFORMED_INPUT"
DENY_OUT_OF_SCOPE = "OUT_OF_SCOPE"
DENY_NOT_GRANTED = "NOT_GRANTED"
DENY_HARD_DENY = "HARD_DENIED"
DENY_CONFIRM = "CONFIRMATION_REQUIRED"
DENY_CONFIRM_SOURCE = "CONFIRMATION_SOURCE_FORBIDDEN"
DENY_EXPIRED = "MODE_EXPIRED"


class PermissionPolicy:
    """Mode-gated decision layer over the capability registry + grants.

    Layered checks, in order: capability exists -> args valid -> scopes valid
    -> (mode determines pre-authorization) -> grant requirement -> hard-deny
    auto patterns -> confirmation. Model/page/skill text never reaches these
    inputs; a caller passes only typed capability/scope/args.
    """

    def __init__(self, store: PermissionsStore,
                 registry: Optional[CapabilityRegistry] = None,
                 confirmations: Optional[ConfirmationStore] = None):
        self.store = store
        self.registry = registry or CapabilityRegistry()
        self.confirmations = confirmations or ConfirmationStore()

    def effective(self, capability: str, scopes: Tuple[str, ...] = ()) -> Dict[str, Any]:
        """Read-only diagnostic: would this request be allowed right now?"""
        d = self.decide(capability, tuple(scopes), source="check",
                        args={}, confirmation_id=None)
        return {"capability": capability, "scopes": list(scopes),
                "allowed": d["allowed"], "code": d["code"],
                "mode": self.store.mode()[0], "detail": d.get("detail", "")}

    def decide(self, capability: str, scopes: Tuple[str, ...], *, source: str,
               args: Dict[str, Any],
               confirmation_id: Optional[str] = None,
               confirmations: Optional[ConfirmationStore] = None) -> Dict[str, Any]:
        """Return a typed decision dict (never executes anything)."""
        store = self.store
        mode, mode_scopes, mode_expires = store.mode()
        spec = self.registry.get(capability)
        if spec is None:
            return self._deny(capability, DENY_UNKNOWN, source, scopes, args)
        if not isinstance(args, dict):
            return self._deny(capability, DENY_MALFORMED, source, scopes, args,
                              detail="args must be a mapping")
        if spec.arg_validator is not None:
            try:
                spec.arg_validator(dict(args))
            except Exception as exc:
                return self._deny(capability, DENY_MALFORMED, source, scopes, args,
                                  detail=f"validator: {exc}")
        if spec.scopes and not scopes:
            # A scoped capability requires the caller to declare its scopes;
            # an empty scope set must not silently bypass scope enforcement.
            return self._deny(capability, DENY_OUT_OF_SCOPE, source, scopes, args,
                              detail=f"capability requires declared scopes: "
                                     f"{sorted(spec.scopes)}")
        if scopes and not set(scopes) <= set(spec.scopes):
            return self._deny(capability, DENY_OUT_OF_SCOPE, source, scopes, args,
                              detail="undeclared scopes: "
                                     f"{sorted(set(scopes) - set(spec.scopes))}")

        # Unrestricted pre-authorization: routine NON-destructive work inside the
        # user-selected mode scopes. Never for hard-deny patterns or mutating tiers.
        if mode == MODE_UNRESTRICTED and mode_expires is not None \
                and _now() >= mode_expires:
            return self._deny(capability, DENY_EXPIRED, source, scopes, args,
                              detail="unrestricted mode expired (fail closed)")
        in_mode_scopes = bool(scopes) and set(scopes) <= set(mode_scopes)
        hard = is_hard_deny_auto(capability)

        auto_unrestricted = (
            mode == MODE_UNRESTRICTED and in_mode_scopes
            and hard is None
            and spec.tier in (Tier.READ_ONLY, Tier.REVERSIBLE)
        )
        if auto_unrestricted:
            d = self._allow(capability, confirmation_id=None,
                            detail=f"unrestricted pre-auth tier={spec.tier.value}")
            self._audit(capability, d, source, scopes, args)
            return d

        # Restricted (default) or unrestricted-outside-mode-scope / sensitive:
        # an explicit non-expired grant is required.
        grant = self._matching_grant(capability, scopes)
        if grant is None:
            return self._deny(capability, DENY_NOT_GRANTED, source, scopes, args,
                              detail="no matching grant; default deny")
        if hard is not None:
            return self._deny(capability, DENY_HARD_DENY, source, scopes, args,
                              detail=f"hard-deny pattern: {hard}")
        if spec.tier in (Tier.MUTATING, Tier.DESTRUCTIVE) or spec.financial:
            return self._require_confirmation(capability, source,
                                              confirmation_id, spec, args,
                                              scopes)
        d = self._allow(capability, confirmation_id=None,
                        detail=f"granted tier={spec.tier.value}")
        self._audit(capability, d, source, scopes, args)
        return d

    def _matching_grant(self, capability: str,
                        scopes: Tuple[str, ...]) -> Optional[Grant]:
        now = _now()
        for g in self.store.grants():
            if not g.is_active(now):
                continue
            if g.capability != capability:
                continue
            if g.scopes and not scopes:
                continue
            if scopes and not set(scopes) <= set(g.scopes):
                continue
            return g
        return None

    def _require_confirmation(self, capability, source, confirmation_id,
                              spec, args, scopes) -> Dict[str, Any]:
        if source != "user":
            return self._deny(capability, DENY_CONFIRM_SOURCE, source, scopes, args,
                              detail=f"source={source} cannot carry approvals")
        if not confirmation_id:
            return self._deny(capability, DENY_CONFIRM, source, scopes, args)
        try:
            rec = self.confirmations.consume(confirmation_id, spec.name, args)
        except ConfirmationDenied as exc:
            return self._deny(capability, exc.code, source, scopes, args)
        d = self._allow(capability, confirmation_id=rec.id,
                        detail=f"confirmed tier={spec.tier.value}")
        self._audit(capability, d, source, scopes, args)
        return d

    def _allow(self, capability: str, confirmation_id, detail: str) -> Dict[str, Any]:
        return {"allowed": True, "code": "ALLOWED", "capability": capability,
                "confirmation_id": confirmation_id, "detail": detail}

    def _deny(self, capability: str, code: str, source: str = "policy",
              scopes: Tuple[str, ...] = (), args: Dict[str, Any] = None,
              detail: str = "") -> Dict[str, Any]:
        d = {"allowed": False, "code": code, "capability": capability,
             "confirmation_id": None, "detail": detail}
        self._audit(capability, d, source, scopes, args or {})
        return d

    @staticmethod
    def _audit(capability, decision, source, scopes, args) -> None:
        try:
            log_event("permissions.decision", allowed=decision["allowed"],
                      code=decision["code"], capability=capability,
                      source=source, scopes=sorted(scopes))
        except Exception:
            pass


def permissions_store(cfg) -> PermissionsStore:
    """Open (or create) the permission store at the configured/state path."""
    path = cfg.get("permissions", "state_path", default="") or \
        (cfg.state_dir() / "permissions.json")
    return PermissionsStore(Path(path).expanduser())