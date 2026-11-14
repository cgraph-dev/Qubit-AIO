"""Capability catalog and per-run policy snapshots (P1-03).

The capability catalog is the *declarative* half of the execution boundary: it
says which capabilities exist, what resource/action they touch, how risky they
are, and which approval/budget/rate posture they carry. A
:class:`CapabilitySnapshot` is the *frozen* half: a per-run, per-subject view
compiled from explicit grants and denials at a pinned policy revision.

Design rules, matching the rest of ``voicebridge.business``:

* **Deny by default.** A snapshot with no matching grant allows nothing. A
  caller cannot conjure authority from a catalog row.
* **Deny wins.** An explicit denial overrides any grant for the same
  subject/capability/resource/action/data-class/environment tuple.
* **No wildcard.** ``*``/``all``/``any`` are rejected at construction, never
  treated as a convenience match.
* **Expiry is enforced.** An expired snapshot, grant or denial is not a
  usable authority. A snapshot is frozen at ``compile_snapshot`` time and its
  digest is deterministic over its canonical content, so two compilations of
  the same inputs (same clock) produce the same snapshot id and digest.
* **Explainable decisions.** :func:`evaluate` returns a :class:`Decision`
  with a stable reason code and a human sentence; it never raises for a
  policy denial. The gateway turns a denial into a typed refusal.

The module is intentionally pure: no store handle, no network, no clock other
than the injected ``now``. The optional ``identity_evaluator`` seam lets a
later identity phase (P1-01) replace the built-in grant matcher without
changing the gateway contract.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .errors import BAD_INPUT_CODE, BusinessError
from .ids import fingerprint
from .util import json_dumps

AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")
APPROVAL_LEVELS = frozenset({"L4", "L5"})
APPROVAL_POLICIES = frozenset({"none", "required", "conditional"})

# Typed reason codes. ``tools.py`` raises these as ``BusinessError.code`` so a
# caller can branch on the exact refusal instead of parsing prose.
CAPABILITY_UNKNOWN_CODE = "BUSINESS_CAPABILITY_UNKNOWN"
CAPABILITY_DENIED_CODE = "BUSINESS_CAPABILITY_DENIED"
CAPABILITY_ARGS_MISMATCH_CODE = "BUSINESS_CAPABILITY_ARGS_MISMATCH"
POLICY_SNAPSHOT_EXPIRED_CODE = "BUSINESS_POLICY_SNAPSHOT_EXPIRED"
POLICY_SNAPSHOT_SCOPE_CODE = "BUSINESS_POLICY_SNAPSHOT_SCOPE"
POLICY_SNAPSHOT_INVALID_CODE = "BUSINESS_POLICY_SNAPSHOT_INVALID"

_WILDCARDS = frozenset({"*", "all", "any", "ALL", "ANY", "All", "Any"})


def _text(value: Any, label: str, *, max_len: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} must be a non-empty string"
        )
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _no_wildcard(value: str, label: str) -> str:
    if value in _WILDCARDS or "*" in value:
        raise BusinessError(
            POLICY_SNAPSHOT_INVALID_CODE,
            f"{label} must be an exact value; wildcards are not allowed"
            f" (got {value!r})",
        )
    return value


def _approval_flag(value: Any) -> str:
    if value is True:
        return "required"
    if value is False or value is None:
        return "none"
    clean = str(value).strip().lower() or "none"
    if clean not in APPROVAL_POLICIES:
        raise BusinessError(
            POLICY_SNAPSHOT_INVALID_CODE,
            f"approval policy must be one of"
            f" {', '.join(sorted(APPROVAL_POLICIES))}",
        )
    return clean


def approval_required(value: Any) -> bool:
    """True when an approval policy/level requires a human decision."""
    if isinstance(value, str) and value.upper() in APPROVAL_LEVELS:
        return True
    return _approval_flag(value) != "none"


@dataclass(frozen=True, slots=True)
class Capability:
    """One catalogued capability (domain: Capability)."""

    capability_id: str
    category: str
    resource: str
    action: str
    risk: str = "L0"
    required_scopes: tuple[str, ...] = ()
    approval_policy: str = "none"
    budget_minor: int | None = None
    rate_limit: int | None = None
    health: str = "healthy"
    version: str = "1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capability_id", _no_wildcard(
                _text(self.capability_id, "capability_id", max_len=120),
                "capability_id",
            )
        )
        object.__setattr__(
            self, "category", _no_wildcard(
                _text(self.category, "category", max_len=120), "category"
            )
        )
        object.__setattr__(
            self, "resource", _no_wildcard(
                _text(self.resource, "resource", max_len=300), "resource"
            )
        )
        object.__setattr__(
            self, "action", _no_wildcard(
                _text(self.action, "action", max_len=120), "action"
            )
        )
        if self.risk not in AUTONOMY_LEVELS:
            raise BusinessError(
                POLICY_SNAPSHOT_INVALID_CODE,
                f"risk must be one of {', '.join(AUTONOMY_LEVELS)}",
            )
        scopes = self.required_scopes
        if isinstance(scopes, str) or not isinstance(scopes, Sequence):
            raise BusinessError(
                POLICY_SNAPSHOT_INVALID_CODE,
                "required_scopes must be a sequence of strings",
            )
        object.__setattr__(
            self,
            "required_scopes",
            tuple(
                _no_wildcard(_text(scope, "required scope"), "required scope")
                for scope in scopes
            ),
        )
        object.__setattr__(
            self, "approval_policy", _approval_flag(self.approval_policy)
        )
        if self.budget_minor is not None and (
            not isinstance(self.budget_minor, int)
            or isinstance(self.budget_minor, bool)
            or self.budget_minor < 0
        ):
            raise BusinessError(
                POLICY_SNAPSHOT_INVALID_CODE,
                "budget_minor must be a non-negative integer or None",
            )
        if self.rate_limit is not None and (
            not isinstance(self.rate_limit, int)
            or isinstance(self.rate_limit, bool)
            or self.rate_limit < 0
        ):
            raise BusinessError(
                POLICY_SNAPSHOT_INVALID_CODE,
                "rate_limit must be a non-negative integer or None",
            )
        object.__setattr__(
            self, "health", _text(self.health, "health", max_len=60).lower()
        )
        object.__setattr__(
            self, "version", _text(self.version, "version", max_len=80)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "category": self.category,
            "resource": self.resource,
            "action": self.action,
            "risk": self.risk,
            "required_scopes": list(self.required_scopes),
            "approval_policy": self.approval_policy,
            "budget_minor": self.budget_minor,
            "rate_limit": self.rate_limit,
            "health": self.health,
            "version": self.version,
        }


class CapabilityCatalog:
    """Registry of known capabilities; unknown ids are a typed refusal."""

    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._entries: dict[str, Capability] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: Capability) -> Capability:
        if not isinstance(capability, Capability):
            raise BusinessError(
                BAD_INPUT_CODE, "capability must be a Capability"
            )
        self._entries[capability.capability_id] = capability
        return capability

    def get(self, capability_id: str) -> Capability:
        clean = _text(capability_id, "capability_id", max_len=120)
        capability = self._entries.get(clean)
        if capability is None:
            raise BusinessError(
                CAPABILITY_UNKNOWN_CODE,
                f"capability {clean!r} is not in the catalog",
            )
        return capability

    def has(self, capability_id: str) -> bool:
        return capability_id in self._entries

    def list(self) -> tuple[Capability, ...]:
        return tuple(
            self._entries[key] for key in sorted(self._entries)
        )

    def versions(self) -> dict[str, str]:
        return {
            capability.capability_id: capability.version
            for capability in self.list()
        }

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._entries)


def _normalise_expiry(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BusinessError(
            POLICY_SNAPSHOT_INVALID_CODE, f"{label} must be a number or None"
        )
    return float(value)


@dataclass(frozen=True, slots=True)
class Grant:
    """An explicit allow for one exact subject/capability tuple (domain: Grant)."""

    subject: str
    capability: str
    resource: str
    action: str
    data_class: str = "internal"
    environment: str = "production"
    expires_at: float | None = None
    approval: str = "none"

    def __post_init__(self) -> None:
        for label in (
            "subject",
            "capability",
            "resource",
            "action",
            "data_class",
            "environment",
        ):
            object.__setattr__(
                self,
                label,
                _no_wildcard(_text(getattr(self, label), label), label),
            )
        object.__setattr__(
            self, "expires_at", _normalise_expiry(self.expires_at, "expires_at")
        )
        object.__setattr__(self, "approval", _approval_flag(self.approval))

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "capability": self.capability,
            "resource": self.resource,
            "action": self.action,
            "data_class": self.data_class,
            "environment": self.environment,
            "expires_at": self.expires_at,
            "approval": self.approval,
        }


@dataclass(frozen=True, slots=True)
class Denial:
    """An explicit deny that wins over any matching grant (domain: Grant)."""

    subject: str
    capability: str
    resource: str
    action: str
    data_class: str = "internal"
    environment: str = "production"
    expires_at: float | None = None
    approval: str = "none"
    reason: str = ""

    def __post_init__(self) -> None:
        for label in (
            "subject",
            "capability",
            "resource",
            "action",
            "data_class",
            "environment",
        ):
            object.__setattr__(
                self,
                label,
                _no_wildcard(_text(getattr(self, label), label), label),
            )
        object.__setattr__(
            self, "expires_at", _normalise_expiry(self.expires_at, "expires_at")
        )
        object.__setattr__(self, "approval", _approval_flag(self.approval))
        object.__setattr__(self, "reason", str(self.reason or "").strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "capability": self.capability,
            "resource": self.resource,
            "action": self.action,
            "data_class": self.data_class,
            "environment": self.environment,
            "expires_at": self.expires_at,
            "approval": self.approval,
            "reason": self.reason,
        }


def _entry_key(entry: Grant | Denial) -> tuple[Any, ...]:
    return (
        entry.subject,
        entry.capability,
        entry.resource,
        entry.action,
        entry.data_class,
        entry.environment,
        "" if entry.expires_at is None else repr(entry.expires_at),
        entry.approval,
        getattr(entry, "reason", ""),
    )


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """A frozen per-run authority view pinned to one policy revision."""

    snapshot_id: str
    tenant_id: str
    subject_id: str
    policy_revision: str
    grants: tuple[Grant, ...] = ()
    denials: tuple[Denial, ...] = ()
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    model_constraints: Mapping[str, Any] = field(
        default_factory=dict
    )
    issued_at: float = 0.0
    expires_at: float | None = None
    digest: str = ""

    def is_expired(self, now: float | None = None) -> bool:
        moment = time.time() if now is None else float(now)
        return self.expires_at is not None and float(self.expires_at) < moment

    def grant_for(
        self,
        capability_id: str,
        resource: str,
        action: str,
        data_class: str = "internal",
        environment: str = "production",
        now: float | None = None,
    ) -> Grant | None:
        moment = time.time() if now is None else float(now)
        for grant in self.grants:
            if _entry_matches(
                grant,
                subject=self.subject_id,
                capability_id=capability_id,
                resource=resource,
                action=action,
                data_class=data_class,
                environment=environment,
                now=moment,
            ):
                return grant
        return None


@dataclass(frozen=True, slots=True)
class Decision:
    """Explainable outcome of one capability evaluation."""

    allowed: bool
    reason: str
    capability_id: str
    snapshot_id: str
    policy_revision: str
    approval_required: bool = False
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "capability_id": self.capability_id,
            "snapshot_id": self.snapshot_id,
            "policy_revision": self.policy_revision,
            "approval_required": self.approval_required,
            "detail": self.detail,
        }


IdentityEvaluator = Callable[..., "Decision | None"]


def _entry_matches(
    entry: Grant | Denial,
    *,
    subject: str,
    capability_id: str,
    resource: str,
    action: str,
    data_class: str,
    environment: str,
    now: float,
) -> bool:
    if entry.expires_at is not None and float(entry.expires_at) < now:
        return False
    return (
        entry.subject == subject
        and entry.capability == capability_id
        and entry.resource == resource
        and entry.action == action
        and entry.data_class == data_class
        and entry.environment == environment
    )


def compile_snapshot(
    *,
    tenant_id: str,
    subject_id: str,
    policy_revision: str | int,
    grants: Iterable[Grant] = (),
    denials: Iterable[Denial] = (),
    tool_versions: Mapping[str, str] | None = None,
    model_constraints: Mapping[str, Any] | None = None,
    issued_at: float | None = None,
    expires_at: float | None = None,
    snapshot_id: str | None = None,
    catalog: CapabilityCatalog | None = None,
) -> CapabilitySnapshot:
    """Freeze a deterministic per-run snapshot.

    ``catalog`` is optional. When supplied, every grant/denial must name a
    registered capability (deny-by-default: an unknown capability is a typed
    refusal, not an implicit allow) and ``tool_versions`` is filled from the
    catalog unless the caller pinned an explicit map.
    """
    tenant = _no_wildcard(_text(tenant_id, "tenant_id", max_len=120), "tenant_id")
    subject = _no_wildcard(
        _text(subject_id, "subject_id", max_len=200), "subject_id"
    )
    revision = _text(str(policy_revision), "policy_revision", max_len=120)
    grant_rows = tuple(grants)
    denial_rows = tuple(denials)
    for row in grant_rows:
        if not isinstance(row, Grant):
            raise BusinessError(
                POLICY_SNAPSHOT_INVALID_CODE, "grants must be Grant records"
            )
    for row in denial_rows:
        if not isinstance(row, Denial):
            raise BusinessError(
                POLICY_SNAPSHOT_INVALID_CODE, "denials must be Denial records"
            )
    if catalog is not None:
        for row in (*grant_rows, *denial_rows):
            catalog.get(row.capability)
        if tool_versions is None:
            tool_versions = catalog.versions()
    versions = {
        str(key): str(value)
        for key, value in sorted((tool_versions or {}).items())
    }
    constraints = dict(model_constraints or {})
    issued = time.time() if issued_at is None else float(issued_at)
    expires = _normalise_expiry(expires_at, "expires_at")
    if expires is not None and expires <= issued:
        raise BusinessError(
            POLICY_SNAPSHOT_INVALID_CODE,
            "a snapshot must expire after it was issued",
        )
    content = {
        "tenant_id": tenant,
        "subject_id": subject,
        "policy_revision": revision,
        "grants": [
            row.as_dict() for row in sorted(grant_rows, key=_entry_key)
        ],
        "denials": [
            row.as_dict() for row in sorted(denial_rows, key=_entry_key)
        ],
        "tool_versions": versions,
        "model_constraints": constraints,
        "issued_at": issued,
        "expires_at": expires,
    }
    digest = fingerprint(content)
    sid = snapshot_id or f"psnap_{digest[:26]}"
    return CapabilitySnapshot(
        snapshot_id=_no_wildcard(
            _text(sid, "snapshot_id", max_len=80), "snapshot_id"
        ),
        tenant_id=tenant,
        subject_id=subject,
        policy_revision=revision,
        grants=grant_rows,
        denials=denial_rows,
        tool_versions=versions,
        model_constraints=constraints,
        issued_at=issued,
        expires_at=expires,
        digest=digest,
    )


def evaluate(
    snapshot: CapabilitySnapshot,
    capability_id: str,
    resource: str,
    action: str,
    data_class: str = "internal",
    environment: str = "production",
    now: float | None = None,
    *,
    identity_evaluator: IdentityEvaluator | None = None,
) -> Decision:
    """Evaluate one call against a frozen snapshot, deny by default."""
    if not isinstance(snapshot, CapabilitySnapshot):
        raise BusinessError(
            BAD_INPUT_CODE, "snapshot must be a CapabilitySnapshot"
        )
    capability = _text(capability_id, "capability_id", max_len=120)
    res = _text(resource, "resource", max_len=300)
    act = _text(action, "action", max_len=120)
    dclass = _text(data_class, "data_class", max_len=120)
    env = _text(environment, "environment", max_len=120)
    moment = time.time() if now is None else float(now)

    if snapshot.is_expired(moment):
        return Decision(
            allowed=False,
            reason=POLICY_SNAPSHOT_EXPIRED_CODE,
            capability_id=capability,
            snapshot_id=snapshot.snapshot_id,
            policy_revision=snapshot.policy_revision,
            detail=(
                f"policy snapshot {snapshot.snapshot_id!r} expired at"
                f" {snapshot.expires_at}"
            ),
        )

    if identity_evaluator is not None:
        external = identity_evaluator(
            snapshot=snapshot,
            capability_id=capability,
            resource=res,
            action=act,
            data_class=dclass,
            environment=env,
            now=moment,
        )
        if external is not None:
            if not isinstance(external, Decision):
                raise BusinessError(
                    BAD_INPUT_CODE,
                    "identity_evaluator must return a Decision or None",
                )
            return external

    for denial in snapshot.denials:
        if _entry_matches(
            denial,
            subject=snapshot.subject_id,
            capability_id=capability,
            resource=res,
            action=act,
            data_class=dclass,
            environment=env,
            now=moment,
        ):
            return Decision(
                allowed=False,
                reason=CAPABILITY_DENIED_CODE,
                capability_id=capability,
                snapshot_id=snapshot.snapshot_id,
                policy_revision=snapshot.policy_revision,
                detail=denial.reason or "an explicit denial matches this call",
            )

    for grant in snapshot.grants:
        if _entry_matches(
            grant,
            subject=snapshot.subject_id,
            capability_id=capability,
            resource=res,
            action=act,
            data_class=dclass,
            environment=env,
            now=moment,
        ):
            return Decision(
                allowed=True,
                reason="granted",
                capability_id=capability,
                snapshot_id=snapshot.snapshot_id,
                policy_revision=snapshot.policy_revision,
                approval_required=_approval_flag(grant.approval) != "none",
                detail="an explicit grant matches this call",
            )

    return Decision(
        allowed=False,
        reason=CAPABILITY_DENIED_CODE,
        capability_id=capability,
        snapshot_id=snapshot.snapshot_id,
        policy_revision=snapshot.policy_revision,
        detail="no grant matches this call (deny by default)",
    )


def normalized_intent(
    *,
    tool: str,
    resource: str,
    action: str,
    parameters: Any,
    currency: str,
    amount_minor: int,
    data_class: str = "internal",
    environment: str = "production",
) -> dict[str, Any]:
    """Canonical approval intent: the exact payload a human approved.

    Parameters are serialised with the same canonical JSON used for hashing,
    so a semantically equal payload hashes identically and any byte-level
    change to the intent invalidates the binding.
    """
    return {
        "tool": _text(tool, "tool", max_len=120),
        "resource": _text(resource, "resource", max_len=300),
        "action": _text(action, "action", max_len=120),
        "parameters": json_dumps({} if parameters is None else parameters),
        "currency": _text(currency, "currency", max_len=8).upper(),
        "amount_minor": int(amount_minor),
        "data_class": _text(data_class, "data_class", max_len=120),
        "environment": _text(environment, "environment", max_len=120),
    }


def intent_digest(intent: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical normalized intent."""
    return fingerprint(dict(intent))


def same_intent(left: Any, right: Any) -> bool:
    """Structural equality under canonical JSON (order-insensitive)."""
    return json_dumps(left) == json_dumps(right)
