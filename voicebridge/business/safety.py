"""P4-06 — behavior baseline, quarantine, lifecycle control and kill switch.

This module is the *operator-side* safety layer for the Business OS. It is
deterministic and hermetic: no network, no provider, no live service. Every
action is a typed method that either delegates to
:class:`~voicebridge.business.agents.AgentControlService` (pause/stop/revoke/
archive) or invokes an injected stopper, and every action writes an audit row.

Rules enforced here:

* **Baseline before verdict.** An agent's behavior is compared to its own
  recorded baseline. With fewer than ``MIN_BASELINE_SAMPLES`` samples the
  detector reports ``insufficient_baseline`` rather than inventing a verdict.
* **Quarantine blocks work.** Quarantine pauses the agent (so
  ``begin_turn`` refuses) *and* installs an in-memory guard that
  :class:`~voicebridge.business.capability_runtime.CapabilityRuntime` can use to
  refuse capability calls. It records the evidence it actually has; it never
  claims a turn was stopped when no turn existed.
* **No fabricated state.** A lifecycle receipt reports the agent row returned by
  the control service. A kill-switch report reports each stopper's real outcome;
  a thrown stopper is a failure, not a success.
* **Out-of-band.** The kill switch needs no agent and no agent control path: an
  operator with the store can engage it. It is idempotent and audited.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping

from .agents import Agent, AgentControlService
from .errors import BAD_INPUT_CODE, BusinessError
from .util import redact

SAFETY_QUARANTINED_CODE = "SAFETY_QUARANTINED"
SAFETY_KILL_SWITCH_INCOMPLETE_CODE = "SAFETY_KILL_SWITCH_INCOMPLETE"
SAFETY_KILL_SWITCH_BAD_INPUT_CODE = "SAFETY_KILL_SWITCH_BAD_INPUT"

MIN_BASELINE_SAMPLES = 3
DEFAULT_BASELINE_WINDOW = 20

KILL_SWITCH_SURFACES = ("scheduler", "gateway", "leases", "egress")

SEVERITIES = ("ok", "warn", "critical")


def _text(value: Any, label: str, *, max_len: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _count(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} must be a non-negative integer"
        )
    return value


# ---------------------------------------------------------------------------
# Behavior baseline
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class BehaviorSample:
    """One observed window of an agent's behavior."""

    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    spend_minor: int = 0
    unknown_outcomes: int = 0
    denied_calls: int = 0
    duration_seconds: float = 60.0

    def __post_init__(self) -> None:
        for label in (
            "turns",
            "tool_calls",
            "tool_errors",
            "spend_minor",
            "unknown_outcomes",
            "denied_calls",
        ):
            object.__setattr__(self, label, _count(getattr(self, label), label))
        if self.tool_errors > self.tool_calls:
            raise BusinessError(
                BAD_INPUT_CODE, "tool_errors cannot exceed tool_calls"
            )
        if self.denied_calls > self.tool_calls:
            raise BusinessError(
                BAD_INPUT_CODE, "denied_calls cannot exceed tool_calls"
            )
        duration = self.duration_seconds
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or duration <= 0
        ):
            raise BusinessError(
                BAD_INPUT_CODE, "duration_seconds must be a positive number"
            )
        object.__setattr__(self, "duration_seconds", float(duration))

    def as_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "spend_minor": self.spend_minor,
            "unknown_outcomes": self.unknown_outcomes,
            "denied_calls": self.denied_calls,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class BehaviorMetrics:
    """Rates derived from one or more samples."""

    turns_per_minute: float
    tool_error_rate: float
    spend_minor_per_minute: float
    unknown_outcome_rate: float
    denied_call_rate: float
    samples: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "turns_per_minute": round(self.turns_per_minute, 6),
            "tool_error_rate": round(self.tool_error_rate, 6),
            "spend_minor_per_minute": round(self.spend_minor_per_minute, 6),
            "unknown_outcome_rate": round(self.unknown_outcome_rate, 6),
            "denied_call_rate": round(self.denied_call_rate, 6),
            "samples": self.samples,
        }


@dataclass(frozen=True, slots=True)
class Anomaly:
    metric: str
    value: float
    baseline: float
    threshold: float
    severity: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": round(self.value, 6),
            "baseline": round(self.baseline, 6),
            "threshold": round(self.threshold, 6),
            "severity": self.severity,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class BehaviorReport:
    severity: str
    anomalies: tuple[Anomaly, ...]
    observed: BehaviorMetrics
    baseline: BehaviorMetrics
    samples: int
    insufficient_baseline: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "anomalies": [item.as_dict() for item in self.anomalies],
            "observed": self.observed.as_dict(),
            "baseline": self.baseline.as_dict(),
            "samples": self.samples,
            "insufficient_baseline": self.insufficient_baseline,
        }


def _metrics(samples: Iterable[BehaviorSample]) -> BehaviorMetrics:
    rows = list(samples)
    turns = sum(row.turns for row in rows)
    calls = sum(row.tool_calls for row in rows)
    errors = sum(row.tool_errors for row in rows)
    spend = sum(row.spend_minor for row in rows)
    unknown = sum(row.unknown_outcomes for row in rows)
    denied = sum(row.denied_calls for row in rows)
    seconds = sum(row.duration_seconds for row in rows) or 1.0
    minutes = seconds / 60.0
    return BehaviorMetrics(
        turns_per_minute=turns / minutes,
        tool_error_rate=(errors / calls) if calls else 0.0,
        spend_minor_per_minute=spend / minutes,
        unknown_outcome_rate=(unknown / turns) if turns else 0.0,
        denied_call_rate=(denied / calls) if calls else 0.0,
        samples=len(rows),
    )


class BehaviorBaseline:
    """A deterministic rolling baseline for one agent."""

    def __init__(
        self, samples: Iterable[BehaviorSample] = (), *, window: int = DEFAULT_BASELINE_WINDOW
    ) -> None:
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            raise BusinessError(BAD_INPUT_CODE, "window must be a positive integer")
        self._samples: deque[BehaviorSample] = deque(maxlen=window)
        for sample in samples:
            self.observe(sample)

    def observe(self, sample: BehaviorSample) -> None:
        if not isinstance(sample, BehaviorSample):
            raise BusinessError(BAD_INPUT_CODE, "sample must be a BehaviorSample")
        self._samples.append(sample)

    @property
    def samples(self) -> tuple[BehaviorSample, ...]:
        return tuple(self._samples)

    def metrics(self) -> BehaviorMetrics:
        return _metrics(self._samples)

    def classify(self, sample: BehaviorSample) -> BehaviorReport:
        """Compare one sample to the recorded baseline."""
        if not isinstance(sample, BehaviorSample):
            raise BusinessError(BAD_INPUT_CODE, "sample must be a BehaviorSample")
        base = self.metrics()
        observed = _metrics((sample,))
        if len(self._samples) < MIN_BASELINE_SAMPLES:
            return BehaviorReport(
                severity="ok",
                anomalies=(),
                observed=observed,
                baseline=base,
                samples=len(self._samples),
                insufficient_baseline=True,
            )
        anomalies: list[Anomaly] = []

        def check(metric: str, value: float, threshold: float, reason: str,
                  severity: str) -> None:
            if value > threshold:
                anomalies.append(
                    Anomaly(
                        metric=metric,
                        value=value,
                        baseline=getattr(base, metric),
                        threshold=threshold,
                        severity=severity,
                        reason=reason,
                    )
                )

        check(
            "tool_error_rate",
            observed.tool_error_rate,
            max(0.25, base.tool_error_rate * 2 + 0.10),
            f"tool error rate {observed.tool_error_rate:.2f} exceeds baseline"
            f" {base.tool_error_rate:.2f}",
            "warn",
        )
        check(
            "denied_call_rate",
            observed.denied_call_rate,
            max(0.20, base.denied_call_rate * 2 + 0.05),
            f"denied-call rate {observed.denied_call_rate:.2f} exceeds baseline"
            f" {base.denied_call_rate:.2f}",
            "warn",
        )
        check(
            "unknown_outcome_rate",
            observed.unknown_outcome_rate,
            max(0.25, base.unknown_outcome_rate * 2 + 0.05),
            f"unknown-outcome rate {observed.unknown_outcome_rate:.2f} exceeds"
            f" baseline {base.unknown_outcome_rate:.2f}",
            "critical",
        )
        check(
            "spend_minor_per_minute",
            observed.spend_minor_per_minute,
            max(base.spend_minor_per_minute * 3, base.spend_minor_per_minute + 50),
            f"spend {observed.spend_minor_per_minute:.2f}/min exceeds baseline"
            f" {base.spend_minor_per_minute:.2f}/min",
            "critical",
        )
        check(
            "turns_per_minute",
            observed.turns_per_minute,
            max(base.turns_per_minute * 3, base.turns_per_minute + 30),
            f"turn rate {observed.turns_per_minute:.2f}/min exceeds baseline"
            f" {base.turns_per_minute:.2f}/min",
            "warn",
        )
        if not anomalies:
            severity = "ok"
        elif any(item.severity == "critical" for item in anomalies):
            severity = "critical"
        else:
            severity = "warn"
        return BehaviorReport(
            severity=severity,
            anomalies=tuple(anomalies),
            observed=observed,
            baseline=base,
            samples=len(self._samples),
        )


# ---------------------------------------------------------------------------
# Lifecycle / quarantine / kill switch
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    tenant_id: str
    agent_id: str
    reason: str
    actor: str
    status_before: str
    status_after: str
    paused: bool
    report: BehaviorReport | None
    audit_id: str | None
    created_at: float
    replayed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "reason": self.reason,
            "actor": self.actor,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "paused": self.paused,
            "report": None if self.report is None else self.report.as_dict(),
            "audit_id": self.audit_id,
            "created_at": self.created_at,
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class LifecycleReceipt:
    action: str
    agent: Agent
    revocations: Mapping[str, Any] = field(default_factory=dict)
    audit_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "agent": self.agent.to_dict(),
            "revocations": dict(self.revocations),
            "audit_id": self.audit_id,
        }


@dataclass(frozen=True, slots=True)
class StopperOutcome:
    surface: str
    ok: bool
    detail: Any = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "ok": self.ok,
            "detail": self.detail,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class KillSwitchReport:
    status: str
    reason: str
    actor: str
    surfaces: tuple[StopperOutcome, ...]
    started_at: float
    audit_id: str | None = None
    audit_error: str = ""
    already_engaged: bool = False

    @property
    def engaged(self) -> bool:
        return self.status in ("STOPPED", "PARTIAL")

    @property
    def surfaces_stopped(self) -> tuple[str, ...]:
        return tuple(item.surface for item in self.surfaces if item.ok)

    @property
    def surfaces_failed(self) -> tuple[str, ...]:
        return tuple(item.surface for item in self.surfaces if not item.ok)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "engaged": self.engaged,
            "reason": self.reason,
            "actor": self.actor,
            "surfaces": [item.as_dict() for item in self.surfaces],
            "surfaces_stopped": list(self.surfaces_stopped),
            "surfaces_failed": list(self.surfaces_failed),
            "started_at": self.started_at,
            "audit_id": self.audit_id,
            "audit_error": self.audit_error,
            "already_engaged": self.already_engaged,
        }


class SafetyService:
    """Per-agent baseline, quarantine and lifecycle gate."""

    def __init__(
        self,
        store: Any,
        *,
        control: AgentControlService | None = None,
        clock: Callable[[], float] | None = None,
        credential_revoker: Callable[[str, str], Any] | None = None,
        lease_revoker: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.store = store
        self.control = control or AgentControlService(store)
        self._clock = clock or time.time
        self._credential_revoker = credential_revoker
        self._lease_revoker = lease_revoker
        self._baselines: dict[tuple[str, str], BehaviorBaseline] = {}
        self._reports: dict[tuple[str, str], BehaviorReport] = {}
        self._quarantine: dict[tuple[str, str], QuarantineRecord] = {}
        self._lock = threading.RLock()

    # -- baseline ------------------------------------------------------
    def baseline(self, tenant_id: str, agent_id: str) -> BehaviorBaseline:
        key = (_text(tenant_id, "tenant_id"), _text(agent_id, "agent_id"))
        with self._lock:
            return self._baselines.setdefault(key, BehaviorBaseline())

    def record_sample(
        self, tenant_id: str, agent_id: str, sample: BehaviorSample
    ) -> BehaviorReport:
        base = self.baseline(tenant_id, agent_id)
        report = base.classify(sample)
        base.observe(sample)
        with self._lock:
            self._reports[(_text(tenant_id, "tenant_id"), _text(agent_id, "agent_id"))] = report
        return report

    def last_report(self, tenant_id: str, agent_id: str) -> BehaviorReport | None:
        with self._lock:
            return self._reports.get(
                (_text(tenant_id, "tenant_id"), _text(agent_id, "agent_id"))
            )

    # -- quarantine ----------------------------------------------------
    def is_quarantined(self, tenant_id: str, agent_id: str) -> bool:
        tenant = _text(tenant_id, "tenant_id")
        agent = _text(agent_id, "agent_id")
        with self._lock:
            return (
                (tenant, agent) in self._quarantine
                or (tenant, _subject(agent)) in self._quarantine
            )

    def quarantine(
        self,
        tenant_id: str,
        agent_id: str,
        reason: str,
        *,
        actor: str = "operator",
        idempotency_key: str,
        now: float | None = None,
    ) -> QuarantineRecord:
        """Pause an agent and block its capability calls, with evidence."""
        tenant = _text(tenant_id, "tenant_id")
        agent = _text(agent_id, "agent_id")
        why = _text(reason, "reason", max_len=2000)
        who = _text(actor, "actor", max_len=200)
        key = _text(idempotency_key, "idempotency key", max_len=300)
        moment = self._clock() if now is None else float(now)
        with self._lock:
            existing = self._quarantine.get((tenant, agent))
            if existing is not None:
                return replace(existing, replayed=True)
        current = self.control.get(tenant, agent)
        status_before = current.status
        paused = False
        status_after = status_before
        if status_before == "active":
            updated = self.control.pause(
                tenant,
                agent,
                idempotency_key=f"quarantine:{key}",
                actor=who,
                note=f"quarantine: {why}",
            )
            paused = True
            status_after = updated.status
        report = self.last_report(tenant, agent)
        evidence = {
            "reason": why,
            "status_before": status_before,
            "status_after": status_after,
            "paused": paused,
            "baseline": report.as_dict() if report is not None else None,
            "quarantine_id": key,
        }
        audit_id = None
        try:
            event = self.store.audit(
                tenant,
                actor=who,
                action="agent.quarantine",
                resource_type="agent",
                resource_id=agent,
                business_id=current.business_id,
                outcome="ok",
                detail=redact(evidence),
            )
            audit_id = event.event_id
        except BusinessError:
            # The in-memory quarantine still blocks calls; the audit row is the
            # durable evidence and its absence is reported rather than hidden.
            audit_id = None
        record = QuarantineRecord(
            tenant_id=tenant,
            agent_id=agent,
            reason=why,
            actor=who,
            status_before=status_before,
            status_after=status_after,
            paused=paused,
            report=report,
            audit_id=audit_id,
            created_at=moment,
        )
        with self._lock:
            self._quarantine[(tenant, agent)] = record
            self._quarantine[(tenant, _subject(agent))] = record
        return record

    def release_quarantine(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        actor: str = "operator",
        idempotency_key: str,
    ) -> QuarantineRecord | None:
        """Remove the in-memory block. Resuming the agent is a separate call."""
        tenant = _text(tenant_id, "tenant_id")
        agent = _text(agent_id, "agent_id")
        with self._lock:
            record = self._quarantine.pop((tenant, agent), None)
            self._quarantine.pop((tenant, _subject(agent)), None)
        if record is None:
            return None
        self.store.audit(
            tenant,
            actor=_text(actor, "actor"),
            action="agent.quarantine.release",
            resource_type="agent",
            resource_id=agent,
            outcome="ok",
            detail=redact({"idempotency_key": idempotency_key}),
        )
        return record

    def guard(self, tenant_id: str, subject_id: str) -> None:
        """Pre-call guard for :class:`CapabilityRuntime`."""
        tenant = _text(tenant_id, "tenant_id")
        subject = _text(subject_id, "subject_id")
        with self._lock:
            record = (
                self._quarantine.get((tenant, subject))
                or self._quarantine.get((tenant, _agent_from_subject(subject)))
            )
        if record is not None:
            raise BusinessError(
                SAFETY_QUARANTINED_CODE,
                f"agent {record.agent_id!r} is quarantined: {record.reason}",
            )

    def guard_for_runtime(self) -> Callable[[str, str], None]:
        return self.guard

    def ensure_can_act(self, tenant_id: str, agent_id: str) -> None:
        """Refuse a new turn or capability call for a quarantined agent."""
        self.guard(tenant_id, agent_id)

    # -- lifecycle -----------------------------------------------------
    def pause(
        self, tenant_id: str, agent_id: str, *, idempotency_key: str,
        actor: str = "operator", note: str = "",
    ) -> LifecycleReceipt:
        agent = self.control.pause(
            _text(tenant_id, "tenant_id"),
            _text(agent_id, "agent_id"),
            idempotency_key=idempotency_key,
            actor=actor,
            note=note,
        )
        return self._receipt("pause", agent, {})

    def resume(
        self, tenant_id: str, agent_id: str, *, idempotency_key: str,
        actor: str = "operator", note: str = "",
    ) -> LifecycleReceipt:
        agent = self.control.resume(
            _text(tenant_id, "tenant_id"),
            _text(agent_id, "agent_id"),
            idempotency_key=idempotency_key,
            actor=actor,
            note=note,
        )
        return self._receipt("resume", agent, {})

    def stop(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        actor: str = "operator",
        turn_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        turn = self.control.stop(
            _text(tenant_id, "tenant_id"),
            _text(agent_id, "agent_id"),
            idempotency_key=idempotency_key,
            actor=actor,
            turn_id=turn_id,
            note=note,
        )
        return {"action": "stop", "turn": turn.to_dict()}

    def revoke(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        scopes: Iterable[str] | None = None,
        actor: str = "operator",
        note: str = "",
    ) -> LifecycleReceipt:
        """Revoke permissions and every injected credential/lease binding."""
        tenant = _text(tenant_id, "tenant_id")
        agent = _text(agent_id, "agent_id")
        updated = self.control.revoke(
            tenant,
            agent,
            idempotency_key=idempotency_key,
            actor=actor,
            scopes=scopes,
            note=note,
        )
        revocations = self._run_revokers(tenant, agent)
        return self._receipt("revoke", updated, revocations)

    def archive(
        self, tenant_id: str, agent_id: str, *, idempotency_key: str,
        actor: str = "operator", note: str = "",
    ) -> LifecycleReceipt:
        agent = self.control.archive(
            _text(tenant_id, "tenant_id"),
            _text(agent_id, "agent_id"),
            idempotency_key=idempotency_key,
            actor=actor,
            note=note,
        )
        return self._receipt("archive", agent, {})

    # -- internals -----------------------------------------------------
    def _receipt(
        self, action: str, agent: Agent, revocations: Mapping[str, Any]
    ) -> LifecycleReceipt:
        audit_id = None
        try:
            event = self.store.audit(
                agent.tenant_id,
                actor="operator",
                action=f"agent.{action}.safety",
                resource_type="agent",
                resource_id=agent.agent_id,
                business_id=agent.business_id,
                outcome="ok",
                detail=redact(
                    {"status": agent.status, "revocations": dict(revocations)}
                ),
            )
            audit_id = event.event_id
        except BusinessError:
            audit_id = None
        return LifecycleReceipt(
            action=action,
            agent=agent,
            revocations=dict(revocations),
            audit_id=audit_id,
        )

    def _run_revokers(self, tenant: str, agent: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for name, revoker in (
            ("credentials", self._credential_revoker),
            ("leases", self._lease_revoker),
        ):
            if revoker is None:
                continue
            try:
                results[name] = {"ok": True, "detail": revoker(tenant, agent)}
            except Exception as exc:  # noqa: BLE001 - injected external seam
                results[name] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
        return results


class OutOfBandKillSwitch:
    """One operator action that stops every enumerated surface.

    ``stoppers`` must name all of :data:`KILL_SWITCH_SURFACES`; a missing
    surface is refused at construction so a partial switch can never be
    mistaken for the real one. Each stopper is called once, in a stable order.
    A stopper that raises is recorded and the remaining stoppers still run.
    """

    def __init__(
        self,
        store: Any,
        stoppers: Mapping[str, Callable[[], Any]],
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(stoppers, Mapping):
            raise BusinessError(
                SAFETY_KILL_SWITCH_BAD_INPUT_CODE, "stoppers must be a mapping"
            )
        missing = [name for name in KILL_SWITCH_SURFACES if name not in stoppers]
        if missing:
            raise BusinessError(
                SAFETY_KILL_SWITCH_INCOMPLETE_CODE,
                "the kill switch is missing stoppers for: "
                + ", ".join(sorted(missing)),
            )
        not_callable = [
            name for name, stop in stoppers.items() if not callable(stop)
        ]
        if not_callable:
            raise BusinessError(
                SAFETY_KILL_SWITCH_BAD_INPUT_CODE,
                "stoppers must be callable: " + ", ".join(sorted(not_callable)),
            )
        self.store = store
        self._stoppers = {name: stoppers[name] for name in KILL_SWITCH_SURFACES}
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._report: KillSwitchReport | None = None

    def engage(
        self,
        *,
        tenant_id: str,
        reason: str,
        actor: str = "operator",
        idempotency_key: str = "",
    ) -> KillSwitchReport:
        tenant = _text(tenant_id, "tenant_id")
        why = _text(reason, "reason", max_len=2000)
        who = _text(actor, "actor", max_len=200)
        key = _text(idempotency_key or "kill-switch", "idempotency key", max_len=300)
        with self._lock:
            if self._report is not None:
                return replace(self._report, already_engaged=True)
            started = self._clock()
            outcomes: list[StopperOutcome] = []
            for surface in KILL_SWITCH_SURFACES:
                try:
                    detail = self._stoppers[surface]()
                    outcomes.append(
                        StopperOutcome(surface=surface, ok=True, detail=detail)
                    )
                except Exception as exc:  # noqa: BLE001 - a stopper is a seam
                    outcomes.append(
                        StopperOutcome(
                            surface=surface,
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}"[:500],
                        )
                    )
            stopped = [item for item in outcomes if item.ok]
            if len(stopped) == len(outcomes):
                status = "STOPPED"
            elif stopped:
                status = "PARTIAL"
            else:
                status = "FAILED"
            audit_id = None
            audit_error = ""
            try:
                event = self.store.audit(
                    tenant,
                    actor=who,
                    action="safety.kill_switch",
                    resource_type="tenant",
                    resource_id=tenant,
                    outcome="ok" if status == "STOPPED" else "error",
                    detail=redact(
                        {
                            "reason": why,
                            "status": status,
                            "idempotency_key": key,
                            "surfaces": [item.as_dict() for item in outcomes],
                        }
                    ),
                )
                audit_id = event.event_id
            except Exception as exc:  # noqa: BLE001 - audit is best effort
                audit_error = f"{type(exc).__name__}: {exc}"[:500]
            report = KillSwitchReport(
                status=status,
                reason=why,
                actor=who,
                surfaces=tuple(outcomes),
                started_at=started,
                audit_id=audit_id,
                audit_error=audit_error,
            )
            self._report = report
            return report

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._report is None:
                return {
                    "engaged": False,
                    "status": "IDLE",
                    "surfaces": list(KILL_SWITCH_SURFACES),
                }
            return self._report.as_dict()


# ---------------------------------------------------------------------------
# Ready-made stoppers
# ---------------------------------------------------------------------------
def runtime_stopper(runtime: Any) -> Callable[[], Any]:
    """Return a stopper that halts a :class:`CapabilityRuntime`."""

    def stop() -> dict[str, Any]:
        return runtime.halt("out-of-band kill switch")

    return stop


def lease_stopper(store: Any, tenant_id: str) -> Callable[[], Any]:
    """Return a stopper that releases every active Business OS lease."""

    def stop() -> dict[str, Any]:
        from . import runs

        leased = runs.list_leases(store, tenant_id, active_only=True)
        released: list[str] = []
        errors: list[dict[str, str]] = []
        for lease in leased:
            try:
                runs.release_lease(
                    store,
                    tenant_id,
                    lease.lease_id,
                    holder=lease.holder,
                    fencing_token=lease.fencing_token,
                    actor="kill-switch",
                )
                released.append(lease.lease_id)
            except BusinessError as exc:
                errors.append({"lease_id": lease.lease_id, "error": exc.code})
        return {"released": released, "errors": errors}

    return stop


def callable_stopper(cancel: Callable[[], Any], *, surface: str = "scheduler") -> Callable[[], Any]:
    """Adapt a zero-arg cancel callable into a stopper."""

    def stop() -> Any:
        return cancel()

    stop.__name__ = f"stop_{surface}"
    return stop


def _subject(agent_id: str) -> str:
    return f"agent:{agent_id}"


def _agent_from_subject(subject: str) -> str:
    if subject.startswith("agent:"):
        return subject.split(":", 1)[1]
    return subject
