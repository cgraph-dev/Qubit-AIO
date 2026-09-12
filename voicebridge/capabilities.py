"""Typed capability registry, confirmation records, and fail-closed policy
decisions (Packet 1A).

Framework-free core for Phase 1. This module deliberately executes NOTHING:
it only classifies requests and issues/consumes confirmations. Wiring into
dispatch paths happens in Packet 1B; execution behind the broker in 3A.

Fail-closed guarantees (each enforced by code below and by tests):
  unknown capability / malformed args / out-of-scope request /
  expired, replayed, or target-mismatched confirmation /
  destructive or financial action without a user-issued confirmation /
  non-user source carrying any confirmation id
  -> DENIED decision; no code path here can execute an action.
Model-originated requests can neither issue nor supply nor consume authority:
the store rejects issuer="model", and the policy engine rejects any
confirmation id carried by a non-user request before consulting the store.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .logging_setup import log_event
from .redaction import redact_obj

NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


class CapabilityError(ValueError):
    """Invalid capability specification."""


class ConfirmationDenied(Exception):
    """Typed refusal to consume a confirmation record."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Tier(str, enum.Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


# One authority for confirmation issuance: only an authenticated human control
# surface may mint a confirmation record. Model output, page text, tool
# results, generated plans, and arbitrary caller strings are refused at
# issuance AND at consumption, so untrusted text can never grant or widen
# scope (even if a non-human issuer label was persisted by another process).
HUMAN_ISSUERS = frozenset({"user", "cli:user", "dashboard", "voice", "voice:user"})
ISSUER_MODEL_FORBIDDEN = "ISSUER_MODEL_FORBIDDEN"
ISSUER_NOT_HUMAN = "ISSUER_NOT_HUMAN"
STORE_FULL = "CONFIRMATION_STORE_FULL"
DEFAULT_MAX_RECORDS = 4096


@dataclass(frozen=True)
class CapabilitySpec:
    """Immutable declaration of one tool/action contract."""
    name: str
    version: str
    tier: Tier
    summary: str
    scopes: tuple[str, ...] = ()
    timeout_s: float = 30.0
    financial: bool = False
    arg_validator: Optional[Callable[[Dict[str, Any]], None]] = None

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.name or ""):
            raise CapabilityError(f"bad capability name: {self.name!r}")
        if not VERSION_RE.match(self.version or ""):
            raise CapabilityError(f"bad version: {self.version!r}")
        if not isinstance(self.tier, Tier):
            raise CapabilityError("tier must be a Tier")
        if not self.summary or len(self.summary) > 200:
            raise CapabilityError("summary required (<=200 chars)")
        if not (0 < float(self.timeout_s) <= 3600):
            raise CapabilityError("timeout_s outside (0, 3600]")
        if self.arg_validator is not None and not callable(self.arg_validator):
            raise CapabilityError("arg_validator must be callable")

    @property
    def needs_confirmation(self) -> bool:
        return self.tier in (Tier.MUTATING, Tier.DESTRUCTIVE) or self.financial


class CapabilityRegistry:
    def __init__(self) -> None:
        self._by_name: Dict[str, CapabilitySpec] = {}
        self._versions: Dict[str, set] = {}

    def register(self, spec: CapabilitySpec) -> CapabilitySpec:
        prev = self._by_name.get(spec.name)
        if prev is not None:
            if spec.tier is not prev.tier:
                raise CapabilityError(
                    f"{spec.name}: tier change {prev.tier}->{spec.tier} forbidden")
            if spec.version in self._versions[spec.name]:
                raise CapabilityError(f"{spec.name}@{spec.version} already registered")
        self._by_name[spec.name] = spec
        self._versions.setdefault(spec.name, set()).add(spec.version)
        return spec

    def get(self, name: str) -> Optional[CapabilitySpec]:
        return self._by_name.get(name)

    def names(self) -> tuple:
        return tuple(sorted(self._by_name))


def _args_digest(args: Dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"),
                           default=repr)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ConfirmationRecord:
    id: str
    capability: str
    args_digest: str
    issued_at: float
    expires_at: float
    issued_by: str
    consumed: bool = False
    consumed_at: Optional[float] = None


class ConfirmationStore:
    """Expiring, single-use, target-bound confirmation records."""

    def __init__(self, clock: Callable[[], float] = time.time,
                 default_ttl_s: float = 300.0,
                 max_records: int = DEFAULT_MAX_RECORDS):
        if not (0 < default_ttl_s <= 86400):
            raise ValueError("default_ttl_s outside (0, 86400]")
        if max_records < 1:
            raise ValueError("max_records must be >= 1")
        self._clock = clock
        self._ttl = float(default_ttl_s)
        self._max_records = int(max_records)
        self._records: Dict[str, ConfirmationRecord] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _require_human(issued_by: str) -> None:
        """Fail closed unless the issuer is an authenticated human surface."""
        if issued_by == "model":
            # A model proposal can never create authority for itself.
            raise ConfirmationDenied(ISSUER_MODEL_FORBIDDEN)
        if issued_by not in HUMAN_ISSUERS:
            raise ConfirmationDenied(ISSUER_NOT_HUMAN)

    def _prune_locked(self, now: float) -> None:
        """Drop consumed/expired records; caller holds ``self._lock``."""
        dead = [rid for rid, r in self._records.items()
                if r.consumed or now >= r.expires_at]
        for rid in dead:
            self._records.pop(rid, None)

    def issue(self, capability: str, args: Dict[str, Any], *, issued_by: str,
              ttl_s: Optional[float] = None) -> ConfirmationRecord:
        self._require_human(issued_by)
        ttl = float(ttl_s) if ttl_s is not None else self._ttl
        if not (1.0 <= ttl <= 86400):
            raise ValueError("ttl_s outside [1, 86400]")
        now = self._clock()
        rec = ConfirmationRecord(
            id=uuid.uuid4().hex, capability=str(capability),
            args_digest=_args_digest(dict(args)), issued_at=now,
            expires_at=now + ttl, issued_by=issued_by)
        with self._lock:
            if len(self._records) >= self._max_records:
                self._prune_locked(now)
            if len(self._records) >= self._max_records:
                # Bounded resource: fail closed rather than grow without limit.
                raise ConfirmationDenied(STORE_FULL)
            self._records[rec.id] = rec
        log_event("confirmation.issued", capability=capability,
                  confirmation_id=rec.id, ttl_s=round(ttl, 3),
                  issued_by=issued_by)
        return rec

    def consume(self, confirmation_id: str, capability: str,
                args: Dict[str, Any]) -> ConfirmationRecord:
        """Single-use consumption; every failure raises a typed denial."""
        now = self._clock()
        with self._lock:
            rec = self._records.get(confirmation_id)
            if rec is None:
                raise ConfirmationDenied("UNKNOWN_CONFIRMATION_ID")
            # Defense in depth: a record loaded from a durable store (or
            # tampered with) is still refused unless a human surface issued it.
            self._require_human(rec.issued_by)
            if rec.consumed:
                raise ConfirmationDenied("REPLAYED_CONFIRMATION")
            if now >= rec.expires_at:
                raise ConfirmationDenied("EXPIRED_CONFIRMATION")
            if rec.capability != capability:
                raise ConfirmationDenied("CAPABILITY_MISMATCH")
            if rec.args_digest != _args_digest(dict(args)):
                raise ConfirmationDenied("TARGET_MISMATCH")
            rec.consumed = True
            rec.consumed_at = now
            return rec

    def verify(self, confirmation_id: str, capability: str,
               args: Dict[str, Any]) -> ConfirmationRecord:
        """Non-destructive validation used by policy pre-decision layers.

        Identical checks to ``consume`` (existence, single-use, expiry,
        capability match, exact argument digest) but does NOT mark the record
        consumed, so the broker can still consume it once at execution time.
        Every failure raises a typed :class:`ConfirmationDenied`.
        """
        now = self._clock()
        with self._lock:
            rec = self._records.get(confirmation_id)
            if rec is None:
                raise ConfirmationDenied("UNKNOWN_CONFIRMATION_ID")
            self._require_human(rec.issued_by)
            if rec.consumed:
                raise ConfirmationDenied("REPLAYED_CONFIRMATION")
            if now >= rec.expires_at:
                raise ConfirmationDenied("EXPIRED_CONFIRMATION")
            if rec.capability != capability:
                raise ConfirmationDenied("CAPABILITY_MISMATCH")
            if rec.args_digest != _args_digest(dict(args)):
                raise ConfirmationDenied("TARGET_MISMATCH")
            return rec


@dataclass(frozen=True)
class ActionRequest:
    capability: str
    args: Dict[str, Any]
    source: str = "user"                      # user | model | automation
    scopes: frozenset = frozenset()           # requested scope tags
    confirmation_id: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str                                 # ALLOWED or a denial reason
    capability: str
    confirmation_id: Optional[str] = None
    detail: str = ""


DENY_UNKNOWN = "UNKNOWN_CAPABILITY"
DENY_MALFORMED = "MALFORMED_INPUT"
DENY_SCOPE = "OUT_OF_SCOPE"
DENY_CONFIRM = "CONFIRMATION_REQUIRED"
DENY_CONFIRM_SOURCE = "CONFIRMATION_SOURCE_FORBIDDEN"


class PolicyEngine:
    """Classifies ActionRequests against the registry + confirmations.

    No execution, no shell, no fallback path exists on this object.
    """

    def __init__(self, registry: CapabilityRegistry,
                 confirmations: Optional[ConfirmationStore] = None):
        self.registry = registry
        self.confirmations = confirmations or ConfirmationStore()

    def decide(self, req: ActionRequest) -> Decision:
        spec = self.registry.get(req.capability)
        if spec is None:
            return self._deny(req, DENY_UNKNOWN)
        if not isinstance(req.args, dict):
            return self._deny(req, DENY_MALFORMED, "args must be a mapping")
        if spec.arg_validator is not None:
            try:
                spec.arg_validator(dict(req.args))
            except Exception as exc:
                return self._deny(req, DENY_MALFORMED, f"validator: {exc}")
        missing_scope = set(req.scopes) - set(spec.scopes)
        if missing_scope:
            return self._deny(req, DENY_SCOPE,
                              f"undeclared scopes: {sorted(missing_scope)}")

        confirmation_id = None
        if spec.needs_confirmation:
            # Repair R1: only a user-originated request may present or consume a
            # confirmation id. Checked BEFORE the store so untrusted sources get
            # no oracle and cause no state change.
            if req.source != "user":
                return self._deny(req, DENY_CONFIRM_SOURCE,
                                  f"source={req.source} cannot carry approvals")
            if not req.confirmation_id:
                return self._deny(req, DENY_CONFIRM)
            try:
                rec = self.confirmations.consume(req.confirmation_id,
                                                 spec.name, req.args)
            except ConfirmationDenied as exc:
                return self._deny(req, exc.code)
            confirmation_id = rec.id
        d = Decision(True, "ALLOWED", spec.name,
                     confirmation_id=confirmation_id,
                     detail=f"tier={spec.tier.value}")
        self._audit(req, d)
        return d

    def _deny(self, req: ActionRequest, code: str, detail: str = "") -> Decision:
        d = Decision(False, code, req.capability, detail=detail)
        self._audit(req, d)
        return d

    @staticmethod
    def _audit(req: ActionRequest, d: Decision) -> None:
        log_event("policy.decision", allowed=d.allowed, code=d.code,
                  capability=req.capability, source=req.source,
                  correlation_id=req.correlation_id,
                  args=redact_obj(req.args))
