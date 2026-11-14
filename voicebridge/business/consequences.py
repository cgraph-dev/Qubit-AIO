"""P7-02 — bounded consequence engine for the Office simulation.

Actions change *simulation* state (stress, health, morale, focus, reputation and
relationships). Business truth is never written here: an authoritative HR,
legal or business event is emitted only through an injected authority port, and
a sensitive consequence is refused outright when no approval (or consent) has
been granted.

Design rules:

* **Simulation and authority are separate.** ``apply`` always returns the
  simulation events it produced. An authoritative event is a *receipt* from an
  injected callback, never a local fabrication. A sensitive action whose
  authority channel is missing is refused, not guessed.
* **Sensitive consequences need approval.** Termination, contracts, money,
  customer impact, discrimination/HR cases and career changes carry a typed
  approval requirement and an explicit authority channel.
* **Effects are bounded.** Every metric is clamped to its declared range; a
  non-finite or unknown effect is a typed refusal.
* **Recovery is a path, not an afterthought.** Support actions lower stress and
  restore health/morale without an approval.
* **Nothing is silently dropped.** Refusals, authority failures and malformed
  actions leave an audit record and a bounded dead-letter entry.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .errors import BAD_INPUT_CODE, BusinessError
from .ids import new_id
from .util import now_iso

SIMULATION_METRICS = (
    "energy",
    "stress",
    "health",
    "morale",
    "focus",
    "reputation",
)
RELATIONSHIP_METRICS = (
    "trust",
    "respect",
    "attraction",
    "resentment",
    "friendship",
    "rivalry",
)
METRIC_BOUNDS: dict[str, tuple[float, float]] = {
    **{name: (0.0, 100.0) for name in SIMULATION_METRICS},
    **{name: (0.0, 100.0) for name in RELATIONSHIP_METRICS},
}
DEFAULT_METRIC_VALUE = 50.0

SENSITIVE_CATEGORIES = (
    "termination",
    "contract",
    "money",
    "customer_impact",
    "hr_case",
    "discrimination",
    "career",
    "legal",
)
SENSITIVE_AUTHORITY: dict[str, str] = {
    "termination": "hr",
    "contract": "legal",
    "money": "business",
    "customer_impact": "business",
    "hr_case": "hr",
    "discrimination": "hr",
    "career": "hr",
    "legal": "legal",
}

CONSEQUENCE_ACTION_UNKNOWN_CODE = "OFFICE_CONSEQUENCE_ACTION_UNKNOWN"
CONSEQUENCE_APPROVAL_REQUIRED_CODE = "OFFICE_CONSEQUENCE_APPROVAL_REQUIRED"
CONSEQUENCE_APPROVAL_INVALID_CODE = "OFFICE_CONSEQUENCE_APPROVAL_INVALID"
CONSEQUENCE_CONSENT_REQUIRED_CODE = "OFFICE_CONSEQUENCE_CONSENT_REQUIRED"
CONSEQUENCE_AUTHORITY_UNAVAILABLE_CODE = (
    "OFFICE_CONSEQUENCE_AUTHORITY_UNAVAILABLE"
)
CONSEQUENCE_AUTHORITY_FAILED_CODE = "OFFICE_CONSEQUENCE_AUTHORITY_FAILED"
CONSEQUENCE_INVALID_EFFECT_CODE = "OFFICE_CONSEQUENCE_INVALID_EFFECT"
CONSEQUENCE_INVALID_ACTION_CODE = "OFFICE_CONSEQUENCE_INVALID_ACTION"


def _text(value: Any, label: str, *, max_len: int = 200) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise BusinessError(BAD_INPUT_CODE, f"{label} is required")
    if len(raw) > max_len:
        raise BusinessError(BAD_INPUT_CODE, f"{label} is too long")
    return raw


def _finite_delta(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BusinessError(
            CONSEQUENCE_INVALID_EFFECT_CODE,
            f"{label} must be a finite number",
        )
    number = float(value)
    if not math.isfinite(number):
        raise BusinessError(
            CONSEQUENCE_INVALID_EFFECT_CODE,
            f"{label} must be a finite number",
        )
    return number


def _clamp(name: str, value: float) -> float:
    low, high = METRIC_BOUNDS[name]
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class ConsequenceAction:
    """A declared action effect. Data, not a hardcoded branch."""

    action_id: str
    effects: Mapping[str, float] = field(default_factory=dict)
    relationship_effects: Mapping[str, float] = field(default_factory=dict)
    sensitive: str | None = None
    requires_approval: bool = False
    requires_consent: bool = False
    recovery: bool = False
    reciprocal: bool = True
    description: str = ""

    @property
    def is_sensitive(self) -> bool:
        return self.sensitive is not None or self.requires_approval


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """A deliberate approval bound to one action, subject and scope."""

    approval_id: str
    action_id: str
    subject_id: str
    scope: str
    expires_at: float | None = None


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    """Consent from named participants, bound to the action."""

    consent_id: str
    action_id: str
    participants: frozenset[str]
    expires_at: float | None = None


@dataclass(slots=True)
class AuthorityPort:
    """Injected callbacks that own authoritative HR/legal/business records.

    Every callback returns an event id (or a mapping carrying one). A callback
    that returns nothing is an authority failure, not a successful event.
    """

    emit_hr: Callable[[Mapping[str, Any]], Any] | None = None
    emit_legal: Callable[[Mapping[str, Any]], Any] | None = None
    emit_business: Callable[[Mapping[str, Any]], Any] | None = None
    audit: Callable[[Mapping[str, Any]], Any] | None = None

    def channel(self, kind: str) -> Callable[[Mapping[str, Any]], Any] | None:
        return {
            "hr": self.emit_hr,
            "legal": self.emit_legal,
            "business": self.emit_business,
        }.get(kind)


@dataclass(frozen=True, slots=True)
class ConsequenceResult:
    action_id: str
    actor: str
    subject: str
    target: str | None
    correlation_id: str
    effects_applied: Mapping[str, float]
    relationship_effects_applied: Mapping[str, float]
    before: Mapping[str, float]
    after: Mapping[str, float]
    simulation_events: tuple[dict[str, Any], ...]
    authoritative_events: tuple[dict[str, Any], ...]
    recovery: bool
    audit_id: str


class SimulationState:
    """In-memory simulation state. Never business truth."""

    def __init__(self, initial: float = DEFAULT_METRIC_VALUE) -> None:
        self._initial = float(initial)
        self._metrics: dict[str, dict[str, float]] = {}
        self._relationships: dict[tuple[str, str], dict[str, float]] = {}

    def metrics(self, subject: str) -> dict[str, float]:
        row = self._metrics.get(subject)
        if row is None:
            row = {name: self._initial for name in SIMULATION_METRICS}
            self._metrics[subject] = row
        return dict(row)

    def relationship(self, a: str, b: str) -> dict[str, float]:
        key = (a, b)
        row = self._relationships.get(key)
        if row is None:
            row = {name: self._initial for name in RELATIONSHIP_METRICS}
            self._relationships[key] = row
        return dict(row)

    def apply_effects(
        self, subject: str, effects: Mapping[str, float]
    ) -> dict[str, float]:
        row = self._metrics.setdefault(
            subject, {name: self._initial for name in SIMULATION_METRICS}
        )
        applied: dict[str, float] = {}
        for name, delta in effects.items():
            before = row[name]
            after = _clamp(name, before + delta)
            row[name] = after
            applied[name] = round(after - before, 6)
        return applied

    def apply_relationship(
        self,
        a: str,
        b: str,
        effects: Mapping[str, float],
        *,
        reciprocal: bool,
    ) -> dict[str, float]:
        applied = self._apply_relationship_one(a, b, effects)
        if reciprocal:
            self._apply_relationship_one(b, a, effects)
        return applied

    def _apply_relationship_one(
        self, a: str, b: str, effects: Mapping[str, float]
    ) -> dict[str, float]:
        key = (a, b)
        row = self._relationships.setdefault(
            key, {name: self._initial for name in RELATIONSHIP_METRICS}
        )
        applied: dict[str, float] = {}
        for name, delta in effects.items():
            before = row[name]
            after = _clamp(name, before + delta)
            row[name] = after
            applied[name] = round(after - before, 6)
        return applied


def _default_actions() -> dict[str, ConsequenceAction]:
    specs = (
        ConsequenceAction(
            "hard_deadline_sprint",
            effects={"stress": 18, "energy": -15, "focus": -8, "morale": -4},
            description="A deadline sprint that taxes the whole body.",
        ),
        ConsequenceAction(
            "overtime",
            effects={"stress": 12, "energy": -12, "health": -6},
            description="Extended hours with a health cost.",
        ),
        ConsequenceAction(
            "coffee_break",
            effects={"stress": -8, "energy": 5, "morale": 3},
            description="A short social break.",
        ),
        ConsequenceAction(
            "conflict_argument",
            effects={"stress": 10, "morale": -8, "focus": -4},
            relationship_effects={
                "trust": -10,
                "respect": -5,
                "resentment": 8,
                "friendship": -4,
            },
            description="A heated disagreement; non-graphic.",
        ),
        ConsequenceAction(
            "counseling_support",
            effects={"stress": -25, "health": 10, "morale": 12, "focus": 4},
            recovery=True,
            description="Support/counselling path after a hard event.",
        ),
        ConsequenceAction(
            "sick_leave",
            effects={"health": 20, "stress": -12, "energy": 10, "morale": 4},
            recovery=True,
            description="Recovery leave.",
        ),
        ConsequenceAction(
            "termination",
            effects={"stress": 40, "morale": -30, "health": -10},
            relationship_effects={"trust": -40, "resentment": 30},
            sensitive="termination",
            requires_approval=True,
            description="Ending employment; an authoritative HR event.",
        ),
        ConsequenceAction(
            "hr_complaint_filed",
            effects={"stress": 14, "morale": -6},
            sensitive="hr_case",
            requires_approval=True,
            description="A formal complaint; an authoritative HR case.",
        ),
        ConsequenceAction(
            "discrimination_case",
            effects={"stress": 30, "morale": -25, "health": -8},
            sensitive="discrimination",
            requires_approval=True,
            description="A discrimination case opened through HR.",
        ),
        ConsequenceAction(
            "promotion",
            effects={"morale": 22, "stress": 6, "reputation": 8},
            sensitive="career",
            requires_approval=True,
            description="A career change recorded by HR.",
        ),
        ConsequenceAction(
            "contract_signing",
            effects={"reputation": 6, "stress": 4},
            sensitive="contract",
            requires_approval=True,
            description="A contract effect owned by legal.",
        ),
        ConsequenceAction(
            "customer_incident",
            effects={"reputation": -12, "stress": 16},
            sensitive="customer_impact",
            requires_approval=True,
            description="A customer impact owned by the business.",
        ),
        ConsequenceAction(
            "bonus_payment",
            effects={"morale": 18, "stress": -6},
            sensitive="money",
            requires_approval=True,
            description="A payment effect owned by the business ledger.",
        ),
    )
    return {spec.action_id: spec for spec in specs}


class ConsequenceEngine:
    """Apply declared action effects and separate them from authority events."""

    def __init__(
        self,
        *,
        authority: AuthorityPort | None = None,
        actions: Mapping[str, ConsequenceAction] | None = None,
        state: SimulationState | None = None,
        clock: Callable[[], float] | None = None,
        audit_limit: int = 512,
        dead_letter_limit: int = 128,
    ) -> None:
        self._authority = authority or AuthorityPort()
        self._state = state or SimulationState()
        self._clock = clock or time.time
        self._actions: dict[str, ConsequenceAction] = {}
        for action in (actions or _default_actions()).values():
            self.register(action)
        self._audit: deque[dict[str, Any]] = deque(maxlen=max(1, audit_limit))
        self._dead_letters: deque[dict[str, Any]] = deque(
            maxlen=max(1, dead_letter_limit)
        )
        self._incidents: deque[dict[str, Any]] = deque(maxlen=max(1, audit_limit))
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, audit_limit))

    # -- catalog ------------------------------------------------------
    @property
    def state(self) -> SimulationState:
        return self._state

    def register(self, action: ConsequenceAction) -> ConsequenceAction:
        action_id = _text(action.action_id, "action_id", max_len=120)
        for name, delta in action.effects.items():
            if name not in SIMULATION_METRICS:
                raise BusinessError(
                    CONSEQUENCE_INVALID_ACTION_CODE,
                    f"unknown simulation metric {name!r}",
                )
            _finite_delta(delta, f"effect {name}")
        for name, delta in action.relationship_effects.items():
            if name not in RELATIONSHIP_METRICS:
                raise BusinessError(
                    CONSEQUENCE_INVALID_ACTION_CODE,
                    f"unknown relationship metric {name!r}",
                )
            _finite_delta(delta, f"relationship effect {name}")
        if action.sensitive is not None and action.sensitive not in (
            SENSITIVE_CATEGORIES
        ):
            raise BusinessError(
                CONSEQUENCE_INVALID_ACTION_CODE,
                f"unknown sensitive category {action.sensitive!r}",
            )
        self._actions[action_id] = action
        return action

    def action(self, action_id: str) -> ConsequenceAction | None:
        return self._actions.get(action_id)

    # -- application --------------------------------------------------
    def apply(
        self,
        action_id: str,
        *,
        actor: str,
        subject: str,
        target: str | None = None,
        approved: ApprovalGrant | None = None,
        consent: ConsentGrant | None = None,
        correlation_id: str | None = None,
        tenant_id: str | None = None,
        business_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> ConsequenceResult:
        moment = float(self._clock())
        actor_id = _text(actor, "actor")
        subject_id = _text(subject, "subject")
        target_id = _text(target, "target") if target else None
        corr = _text(correlation_id, "correlation_id", max_len=80) if (
            correlation_id
        ) else new_id("cor")
        action = self._actions.get(str(action_id).strip())
        if action is None:
            self._dead_letter(
                "unknown_action", action_id=str(action_id), actor=actor_id
            )
            self._audit_unknown(
                action_id=str(action_id),
                actor=actor_id,
                subject=subject_id,
                target=target_id,
                correlation_id=corr,
                moment=moment,
            )
            raise BusinessError(
                CONSEQUENCE_ACTION_UNKNOWN_CODE,
                f"no declared consequence for action {action_id!r}",
            )

        try:
            self._validate_approval(action, subject_id, approved, moment)
            self._validate_consent(
                action, actor_id, subject_id, target_id, consent, moment
            )
        except BusinessError as exc:
            self._audit_record(
                action,
                actor=actor_id,
                subject=subject_id,
                target=target_id,
                decision="refused",
                reason_code=exc.code,
                correlation_id=corr,
                moment=moment,
            )
            raise

        authority_kind = SENSITIVE_AUTHORITY.get(action.sensitive or "")
        if authority_kind and self._authority.channel(authority_kind) is None:
            self._dead_letter(
                "authority_unavailable",
                action_id=action.action_id,
                channel=authority_kind,
            )
            self._audit_record(
                action,
                actor=actor_id,
                subject=subject_id,
                target=target_id,
                decision="refused",
                reason_code=CONSEQUENCE_AUTHORITY_UNAVAILABLE_CODE,
                correlation_id=corr,
                moment=moment,
            )
            raise BusinessError(
                CONSEQUENCE_AUTHORITY_UNAVAILABLE_CODE,
                f"{action.action_id!r} needs the {authority_kind} authority "
                "channel; no fabricated event is written",
            )

        before = self._state.metrics(subject_id)
        authoritative: list[dict[str, Any]] = []
        if authority_kind:
            payload = {
                "action_id": action.action_id,
                "sensitive": action.sensitive,
                "actor": actor_id,
                "subject": subject_id,
                "target": target_id,
                "tenant_id": tenant_id,
                "business_id": business_id,
                "correlation_id": corr,
                "approval_id": approved.approval_id if approved else None,
                "consent_id": consent.consent_id if consent else None,
                "simulation_summary": dict(action.effects),
                "context": dict(context or {}),
                "occurred_at": now_iso(moment),
            }
            try:
                raw = self._authority.channel(authority_kind)(payload)
            except Exception as exc:  # noqa: BLE001 - authority is external
                self._dead_letter(
                    "authority_failed",
                    action_id=action.action_id,
                    channel=authority_kind,
                    detail=f"{type(exc).__name__}: {exc}",
                )
                self._audit_record(
                    action,
                    actor=actor_id,
                    subject=subject_id,
                    target=target_id,
                    decision="refused",
                    reason_code=CONSEQUENCE_AUTHORITY_FAILED_CODE,
                    correlation_id=corr,
                    moment=moment,
                )
                raise BusinessError(
                    CONSEQUENCE_AUTHORITY_FAILED_CODE,
                    f"the {authority_kind} authority rejected the event",
                )
            event_id = _receipt_id(raw)
            if not event_id:
                self._dead_letter(
                    "authority_no_receipt",
                    action_id=action.action_id,
                    channel=authority_kind,
                )
                self._audit_record(
                    action,
                    actor=actor_id,
                    subject=subject_id,
                    target=target_id,
                    decision="refused",
                    reason_code=CONSEQUENCE_AUTHORITY_FAILED_CODE,
                    correlation_id=corr,
                    moment=moment,
                )
                raise BusinessError(
                    CONSEQUENCE_AUTHORITY_FAILED_CODE,
                    f"the {authority_kind} authority returned no event id",
                )
            authoritative.append(
                {
                    "kind": authority_kind,
                    "category": action.sensitive,
                    "event_id": event_id,
                }
            )

        effects_applied = self._state.apply_effects(subject_id, action.effects)
        relationship_applied: dict[str, float] = {}
        if target_id and action.relationship_effects:
            relationship_applied = self._state.apply_relationship(
                actor_id,
                target_id,
                action.relationship_effects,
                reciprocal=action.reciprocal,
            )
        after = self._state.metrics(subject_id)

        simulation_events = [
            self._simulation_event(
                "simulation.effect_applied",
                action,
                actor=actor_id,
                subject=subject_id,
                target=target_id,
                correlation_id=corr,
                moment=moment,
                data={
                    "effects": dict(effects_applied),
                    "before": before,
                    "after": after,
                    "recovery": action.recovery,
                },
            )
        ]
        if relationship_applied:
            simulation_events.append(
                self._simulation_event(
                    "simulation.relationship_changed",
                    action,
                    actor=actor_id,
                    subject=subject_id,
                    target=target_id,
                    correlation_id=corr,
                    moment=moment,
                    data={"relationship_effects": relationship_applied},
                )
            )
        for event in simulation_events:
            self._events.append(event)

        audit = self._audit_record(
            action,
            actor=actor_id,
            subject=subject_id,
            target=target_id,
            decision="applied",
            reason_code=None,
            correlation_id=corr,
            moment=moment,
            authoritative=authoritative,
            simulation_event_ids=[event["event_id"] for event in simulation_events],
        )
        if action.is_sensitive:
            self._incidents.append(
                {
                    "incident_id": new_id("inc"),
                    "audit_id": audit["audit_id"],
                    "action_id": action.action_id,
                    "category": action.sensitive,
                    "actor": actor_id,
                    "subject": subject_id,
                    "target": target_id,
                    "correlation_id": corr,
                    "authoritative_event_ids": [
                        item["event_id"] for item in authoritative
                    ],
                    "occurred_at": now_iso(moment),
                }
            )

        return ConsequenceResult(
            action_id=action.action_id,
            actor=actor_id,
            subject=subject_id,
            target=target_id,
            correlation_id=corr,
            effects_applied=dict(effects_applied),
            relationship_effects_applied=dict(relationship_applied),
            before=before,
            after=after,
            simulation_events=tuple(simulation_events),
            authoritative_events=tuple(authoritative),
            recovery=action.recovery,
            audit_id=audit["audit_id"],
        )

    # -- reads --------------------------------------------------------
    def audit_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._audit)

    def dead_letters(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._dead_letters)

    def incidents(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._incidents)

    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._events)

    # -- internals ----------------------------------------------------
    def _validate_approval(
        self,
        action: ConsequenceAction,
        subject_id: str,
        approved: ApprovalGrant | None,
        moment: float,
    ) -> None:
        if not action.is_sensitive:
            return
        if approved is None:
            raise BusinessError(
                CONSEQUENCE_APPROVAL_REQUIRED_CODE,
                f"{action.action_id!r} is a sensitive consequence and needs "
                "an explicit approval",
            )
        if not isinstance(approved, ApprovalGrant):
            raise BusinessError(
                CONSEQUENCE_APPROVAL_INVALID_CODE,
                "approval must be an ApprovalGrant",
            )
        if approved.action_id != action.action_id:
            raise BusinessError(
                CONSEQUENCE_APPROVAL_INVALID_CODE,
                "approval is bound to a different action",
            )
        if approved.subject_id != subject_id:
            raise BusinessError(
                CONSEQUENCE_APPROVAL_INVALID_CODE,
                "approval is bound to a different subject",
            )
        if approved.scope != action.sensitive:
            raise BusinessError(
                CONSEQUENCE_APPROVAL_INVALID_CODE,
                "approval scope does not cover this sensitive category",
            )
        if approved.expires_at is not None and approved.expires_at <= moment:
            raise BusinessError(
                CONSEQUENCE_APPROVAL_INVALID_CODE,
                "approval has expired",
            )

    def _validate_consent(
        self,
        action: ConsequenceAction,
        actor_id: str,
        subject_id: str,
        target_id: str | None,
        consent: ConsentGrant | None,
        moment: float,
    ) -> None:
        if not action.requires_consent:
            return
        if consent is None:
            raise BusinessError(
                CONSEQUENCE_CONSENT_REQUIRED_CODE,
                f"{action.action_id!r} needs recorded participant consent",
            )
        if not isinstance(consent, ConsentGrant):
            raise BusinessError(
                CONSEQUENCE_CONSENT_REQUIRED_CODE,
                "consent must be a ConsentGrant",
            )
        if consent.action_id != action.action_id:
            raise BusinessError(
                CONSEQUENCE_CONSENT_REQUIRED_CODE,
                "consent is bound to a different action",
            )
        if consent.expires_at is not None and consent.expires_at <= moment:
            raise BusinessError(
                CONSEQUENCE_CONSENT_REQUIRED_CODE,
                "consent has expired",
            )
        required = {actor_id, subject_id}
        if target_id:
            required.add(target_id)
        if not required.issubset(consent.participants):
            missing = sorted(required - set(consent.participants))
            raise BusinessError(
                CONSEQUENCE_CONSENT_REQUIRED_CODE,
                f"consent is missing from participant(s): {', '.join(missing)}",
            )

    def _audit_record(
        self,
        action: ConsequenceAction,
        *,
        actor: str,
        subject: str,
        target: str | None,
        decision: str,
        reason_code: str | None,
        correlation_id: str,
        moment: float,
        authoritative: list[dict[str, Any]] | None = None,
        simulation_event_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        record = {
            "audit_id": new_id("aud"),
            "action_id": action.action_id,
            "sensitive": action.sensitive,
            "recovery": action.recovery,
            "actor": actor,
            "subject": subject,
            "target": target,
            "decision": decision,
            "reason_code": reason_code,
            "correlation_id": correlation_id,
            "simulation_event_ids": list(simulation_event_ids or []),
            "authoritative_event_ids": [
                item["event_id"] for item in (authoritative or [])
            ],
            "occurred_at": now_iso(moment),
        }
        return self._append_audit(record)

    def _append_audit(self, record: dict[str, Any]) -> dict[str, Any]:
        self._audit.append(record)
        sink = self._authority.audit
        if sink is not None:
            try:
                sink(dict(record))
            except Exception as exc:  # noqa: BLE001 - audit sink is external
                self._dead_letter(
                    "audit_sink_failed",
                    action_id=record.get("action_id"),
                    detail=f"{type(exc).__name__}: {exc}",
                )
        return record

    def _audit_unknown(
        self,
        *,
        action_id: str,
        actor: str,
        subject: str,
        target: str | None,
        correlation_id: str,
        moment: float,
    ) -> dict[str, Any]:
        return self._append_audit(
            {
                "audit_id": new_id("aud"),
                "action_id": action_id,
                "sensitive": None,
                "recovery": False,
                "actor": actor,
                "subject": subject,
                "target": target,
                "decision": "refused",
                "reason_code": CONSEQUENCE_ACTION_UNKNOWN_CODE,
                "correlation_id": correlation_id,
                "simulation_event_ids": [],
                "authoritative_event_ids": [],
                "occurred_at": now_iso(moment),
            }
        )

    def _simulation_event(
        self,
        event_type: str,
        action: ConsequenceAction,
        *,
        actor: str,
        subject: str,
        target: str | None,
        correlation_id: str,
        moment: float,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "event_id": new_id("fx"),
            "type": event_type,
            "authoritative": False,
            "action_id": action.action_id,
            "actor": actor,
            "subject": subject,
            "target": target,
            "correlation_id": correlation_id,
            "occurred_at": now_iso(moment),
            "data": dict(data),
        }

    def _dead_letter(self, reason: str, **detail: Any) -> None:
        self._dead_letters.append(
            {
                "dead_letter_id": new_id("dlq"),
                "reason": reason,
                "occurred_at": now_iso(float(self._clock())),
                **detail,
            }
        )



def _receipt_id(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, Mapping):
        value = raw.get("event_id") or raw.get("id")
        return str(value).strip() if value else None
    return None
