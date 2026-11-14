"""CRM, consent/suppression, pipeline and support tickets (P5-03).

This module owns **semantics**, not a second persistence layer. It uses only
the existing Business OS primitives:

* contacts and tasks from :class:`~voicebridge.business.store.BusinessStore`;
* the authoritative append-only event log for consent, suppression, pipeline
  stage, ticket activity, internal notes and customer messages;
* the shared approval gate for every customer-visible communication;
* the existing single-owner lease table for contact assignment.

There is no new database and no network client. A customer communication is
never sent from here: this module records the approved message intent and its
event. Delivery is an adapter concern outside the control plane.

Enforced invariants:

* identity dedupe is by ``external_ref`` first and lower-cased email second,
  and two different existing contacts cannot be silently merged;
* suppression for a channel (or ``all``) always blocks contact; consented
  marketing also requires an explicit ``granted`` consent for that channel;
* every customer-visible message is bound to one exact approval intent (body,
  ticket, contact, channel and body hash), so editing after approval is a
  typed refusal;
* internal notes are a separate event type and can never be listed as a
  customer-visible message;
* support tickets carry an SLA due time; handoff moves the single assignee and
  records whether the SLA was already breached;
* every read/write is tenant- and business-scoped.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import approvals, eventlog, models, runs
from .errors import (
    BAD_INPUT_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    BusinessError,
)
from .ids import fingerprint
from .util import now_iso, require_text

# -- typed codes ---------------------------------------------------------
CRM_IDENTITY_REQUIRED_CODE = "BUSINESS_CRM_IDENTITY_REQUIRED"
CRM_IDENTITY_CONFLICT_CODE = "BUSINESS_CRM_IDENTITY_CONFLICT"
CRM_CONSENT_BLOCKED_CODE = "BUSINESS_CRM_CONSENT_BLOCKED"
CRM_SUPPRESSED_CODE = "BUSINESS_CRM_SUPPRESSED"
CRM_STAGE_UNKNOWN_CODE = "BUSINESS_CRM_STAGE_UNKNOWN"
CRM_TICKET_NOT_FOUND_CODE = "BUSINESS_CRM_TICKET_NOT_FOUND"
CRM_TICKET_TERMINAL_CODE = "BUSINESS_CRM_TICKET_TERMINAL"
CRM_TICKET_SCOPE_CODE = "BUSINESS_CRM_TICKET_SCOPE"
CRM_NOTE_VISIBILITY_CODE = "BUSINESS_CRM_NOTE_VISIBILITY"
CRM_CONTENT_REFUSED_CODE = "BUSINESS_CRM_CONTENT_REFUSED"
CRM_MESSAGE_APPROVAL_CODE = "BUSINESS_CRM_MESSAGE_APPROVAL_REQUIRED"

#: Every channel a consent or suppression decision can be recorded for. The
#: ``all`` channel is a blanket decision that applies to every other channel.
CHANNELS = ("email", "sms", "social", "phone", "chat", "all")
CONSENT_STATUSES = ("granted", "revoked")
SUPPRESSION_STATUSES = ("suppressed", "cleared")

PIPELINE_STAGES = ("new", "qualified", "proposed", "won", "lost")
TERMINAL_STAGES = frozenset({"won", "lost"})
STAGE_EVENT = "crm.pipeline.stage"
CONSENT_EVENT = "crm.consent"
SUPPRESSION_EVENT = "crm.suppression"
CONTACT_CREATED_EVENT = "crm.contact.created"
CONTACT_MATCHED_EVENT = "crm.contact.matched"
ASSIGNMENT_ACQUIRED_EVENT = "crm.assignment.acquired"
ASSIGNMENT_RELEASED_EVENT = "crm.assignment.released"
TICKET_OPENED_EVENT = "crm.support.ticket_opened"
TICKET_HANDOFF_EVENT = "crm.support.handoff"
TICKET_STATUS_EVENT = "crm.support.status"
NOTE_EVENT = "crm.support.note"
MESSAGE_EVENT = "crm.support.message"

SUPPORT_MESSAGE_ACTION = "crm.support.message"
ASSIGNMENT_RESOURCE = "crm_contact"
PRIORITY_SLA_SECONDS = {
    "low": 72 * 3600,
    "normal": 24 * 3600,
    "high": 4 * 3600,
    "urgent": 3600,
}
MAX_TICKET_BODY = 8000
MAX_MESSAGE_BODY = 4000

#: Customer content that must never be recorded as an approved message.
_PROHIBITED_CLAIMS = (
    "guaranteed cure",
    "cures cancer",
    "risk-free investment",
    "risk free investment",
    "get rich quick",
    "instant wealth",
    "fda approved",
    "miracle cure",
)
_FAKE_ENGAGEMENT = (
    "buy followers",
    "buy likes",
    "buy views",
    "buy subscribers",
    "follow for follow",
    "like for like",
    "engagement pod",
)
_SPAM_PHRASES = (
    "act now!!!",
    "100% free!!!",
    "click here to win",
)


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(
    value: Any, label: str, *, max_len: int = 4096
) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _channel(value: Any) -> str:
    clean = _text(value, "channel", max_len=20).lower()
    if clean not in CHANNELS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown channel {clean!r}; expected one of {', '.join(CHANNELS)}",
        )
    return clean


def _priority(value: Any) -> str:
    clean = _text(value, "priority", max_len=20).lower()
    if clean not in PRIORITY_SLA_SECONDS:
        raise BusinessError(
            BAD_INPUT_CODE,
            "priority must be one of "
            + ", ".join(sorted(PRIORITY_SLA_SECONDS)),
        )
    return clean


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _epoch(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _append(
    store: "Any",
    tenant_id: str,
    business_id: str,
    aggregate_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    actor: str,
    idempotency_key: str,
    now: float | None = None,
) -> eventlog.EventEnvelope:
    return eventlog.append_event(
        store,
        tenant_id,
        aggregate_id,
        event_type,
        dict(payload),
        actor=actor,
        idempotency_key=idempotency_key,
        business_id=business_id,
        now=now,
    )


def _event_by_key(
    store: "Any", tenant_id: str, idempotency_key: str
) -> eventlog.EventEnvelope | None:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_event WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, idempotency_key),
        ).fetchone()
    return None if row is None else eventlog.EventEnvelope.from_row(row)


def _events_for(
    store: "Any", tenant_id: str, aggregate_id: str
) -> list[eventlog.EventEnvelope]:
    return eventlog.read_events(store, tenant_id, aggregate_id)


# -- contacts and identity ------------------------------------------------
def _find_contact(
    store: "Any",
    tenant_id: str,
    business_id: str,
    *,
    email: str | None,
    external_ref: str | None,
) -> models.Contact | None:
    rows = store.list_contacts(tenant_id, business_id=business_id)
    by_ref: models.Contact | None = None
    by_email: models.Contact | None = None
    for contact in rows:
        if external_ref and contact.external_ref == external_ref:
            by_ref = contact
        if email and contact.email and contact.email.lower() == email.lower():
            by_email = contact
    if by_ref is not None and by_email is not None:
        if by_ref.contact_id != by_email.contact_id:
            raise BusinessError(
                CRM_IDENTITY_CONFLICT_CODE,
                "the email and external_ref already belong to two different"
                " contacts; resolve the identity before merging",
            )
    return by_ref or by_email


def upsert_contact(
    store: "Any",
    tenant_id: str,
    business_id: str,
    *,
    kind: str,
    display_name: str,
    email: str | None = None,
    external_ref: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor: str = "system",
    idempotency_key: str,
    now: float | None = None,
) -> tuple[models.Contact, bool]:
    """Create or match one contact by external ref/email; return the row."""
    email_clean = (
        _text(email, "email", max_len=320).lower() if email else None
    )
    ref_clean = _optional_text(external_ref, "external_ref", max_len=200)
    if email_clean is None and ref_clean is None:
        raise BusinessError(
            CRM_IDENTITY_REQUIRED_CODE,
            "a CRM contact needs at least an email or an external_ref so it"
            " can be deduplicated",
        )
    key = _text(idempotency_key, "idempotency key", max_len=300)
    who = _text(actor, "actor", max_len=200)
    name = _text(display_name, "display_name", max_len=200)
    existing = _find_contact(
        store, tenant_id, business_id,
        email=email_clean, external_ref=ref_clean,
    )
    contact = store.create_contact(
        tenant_id,
        business_id,
        kind=kind,
        display_name=name,
        email=email_clean,
        external_ref=ref_clean,
        metadata=metadata,
    )
    created = existing is None
    identity = fingerprint(
        {"email": contact.email, "external_ref": contact.external_ref}
    )
    _append(
        store,
        tenant_id,
        business_id,
        contact.contact_id,
        CONTACT_CREATED_EVENT if created else CONTACT_MATCHED_EVENT,
        {
            "contact_id": contact.contact_id,
            "business_id": business_id,
            "kind": contact.kind,
            "identity_sha256": identity,
            "created": created,
        },
        actor=who,
        idempotency_key=f"crm:contact:{key}",
        now=now,
    )
    return contact, created


def get_contact(
    store: "Any", tenant_id: str, contact_id: str
) -> models.Contact:
    return store.get_contact(tenant_id, contact_id)


# -- consent and suppression ---------------------------------------------
def record_consent(
    store: "Any",
    tenant_id: str,
    contact_id: str,
    *,
    channel: str,
    status: str,
    source: str = "",
    actor: str = "system",
    idempotency_key: str,
    now: float | None = None,
) -> eventlog.EventEnvelope:
    """Append one consent decision for a contact/channel."""
    contact = store.get_contact(tenant_id, contact_id)
    clean_channel = _channel(channel)
    clean_status = _text(status, "consent status", max_len=20).lower()
    if clean_status not in CONSENT_STATUSES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"consent status must be one of {', '.join(CONSENT_STATUSES)}",
        )
    return _append(
        store,
        tenant_id,
        contact.business_id,
        contact.contact_id,
        CONSENT_EVENT,
        {
            "contact_id": contact.contact_id,
            "channel": clean_channel,
            "status": clean_status,
            "source": str(source or "")[:200],
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=f"crm:consent:{_text(idempotency_key, 'key', max_len=300)}",
        now=now,
    )


def record_suppression(
    store: "Any",
    tenant_id: str,
    contact_id: str,
    *,
    channel: str = "all",
    status: str = "suppressed",
    reason: str = "",
    actor: str = "system",
    idempotency_key: str,
    now: float | None = None,
) -> eventlog.EventEnvelope:
    """Append one suppression/clear decision for a contact/channel."""
    contact = store.get_contact(tenant_id, contact_id)
    clean_channel = _channel(channel)
    clean_status = _text(status, "suppression status", max_len=20).lower()
    if clean_status not in SUPPRESSION_STATUSES:
        raise BusinessError(
            BAD_INPUT_CODE,
            "suppression status must be one of "
            + ", ".join(SUPPRESSION_STATUSES),
        )
    return _append(
        store,
        tenant_id,
        contact.business_id,
        contact.contact_id,
        SUPPRESSION_EVENT,
        {
            "contact_id": contact.contact_id,
            "channel": clean_channel,
            "status": clean_status,
            "reason": str(reason or "")[:500],
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"crm:suppression:{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )


def consent_state(
    store: "Any", tenant_id: str, contact_id: str, channel: str
) -> dict[str, Any]:
    """Derive the effective consent/suppression state for one channel."""
    contact = store.get_contact(tenant_id, contact_id)
    clean_channel = _channel(channel)
    consent: dict[str, str] = {}
    suppression: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for envelope in _events_for(store, tenant_id, contact.contact_id):
        payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
        if envelope.type == CONSENT_EVENT:
            key = str(payload.get("channel") or "").lower()
            if key:
                consent[key] = str(payload.get("status") or "unknown")
        elif envelope.type == SUPPRESSION_EVENT:
            key = str(payload.get("channel") or "").lower()
            if key:
                suppression[key] = str(payload.get("status") or "unknown")
                if key not in reasons:
                    reasons[key] = str(payload.get("reason") or "")
    applicable = (clean_channel, "all")
    consent_value = next(
        (consent[key] for key in applicable if key in consent), "unknown"
    )
    suppressed = any(
        suppression.get(key) == "suppressed" for key in applicable
    )
    suppression_reason = next(
        (
            reasons[key]
            for key in applicable
            if suppression.get(key) == "suppressed"
        ),
        "",
    )
    state = {
        "contact_id": contact.contact_id,
        "business_id": contact.business_id,
        "channel": clean_channel,
        "consent": consent_value,
        "suppressed": suppressed,
        "suppression_reason": suppression_reason,
        "consent_by_channel": consent,
        "suppression_by_channel": suppression,
    }
    state["marketing_contactable"] = (
        not suppressed and consent_value == "granted"
    )
    return state


def assert_contactable(
    store: "Any",
    tenant_id: str,
    contact_id: str,
    channel: str,
    *,
    require_consent: bool = True,
) -> dict[str, Any]:
    """Raise a typed refusal when a channel cannot contact this contact."""
    state = consent_state(store, tenant_id, contact_id, channel)
    if state["suppressed"]:
        raise BusinessError(
            CRM_SUPPRESSED_CODE,
            f"contact {contact_id!r} is suppressed on {state['channel']!r}"
            + (
                f": {state['suppression_reason']}"
                if state["suppression_reason"]
                else ""
            ),
        )
    if require_consent and state["consent"] != "granted":
        raise BusinessError(
            CRM_CONSENT_BLOCKED_CODE,
            f"no granted consent for contact {contact_id!r} on"
            f" {state['channel']!r}",
        )
    return state


# -- pipeline -------------------------------------------------------------
def _stage_record(
    store: "Any", tenant_id: str, contact: models.Contact
) -> tuple[str, datetime, str]:
    stage = "new"
    changed = _parse_iso(contact.created_at) or datetime.now(timezone.utc)
    actor = "system"
    for envelope in _events_for(store, tenant_id, contact.contact_id):
        if envelope.type != STAGE_EVENT:
            continue
        payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
        stage = str(payload.get("to") or stage)
        changed = _parse_iso(envelope.occurred_at) or changed
        actor = envelope.actor
    return stage, changed, actor


def set_stage(
    store: "Any",
    tenant_id: str,
    contact_id: str,
    stage: str,
    *,
    actor: str,
    idempotency_key: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Record one pipeline stage transition (append-only)."""
    contact = store.get_contact(tenant_id, contact_id)
    target = _text(stage, "stage", max_len=20).lower()
    if target not in PIPELINE_STAGES:
        raise BusinessError(
            CRM_STAGE_UNKNOWN_CODE,
            f"unknown pipeline stage {target!r}; expected one of"
            f" {', '.join(PIPELINE_STAGES)}",
        )
    current, changed, _ = _stage_record(store, tenant_id, contact)
    if current != target:
        _append(
            store,
            tenant_id,
            contact.business_id,
            contact.contact_id,
            STAGE_EVENT,
            {
                "contact_id": contact.contact_id,
                "from": current,
                "to": target,
            },
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=(
                f"crm:stage:{_text(idempotency_key, 'key', max_len=300)}"
            ),
            now=now,
        )
        current, changed, _ = _stage_record(store, tenant_id, contact)
    return {
        "contact_id": contact.contact_id,
        "stage": current,
        "changed_at": changed.isoformat().replace("+00:00", "Z"),
    }


def _active_assignment(
    store: "Any", tenant_id: str, contact_id: str, now: float
) -> dict[str, Any] | None:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_lease WHERE tenant_id=?"
            " AND resource_type=? AND resource_id=? AND released_at IS NULL"
            " ORDER BY acquired_at DESC, lease_id DESC LIMIT 1",
            (tenant_id, ASSIGNMENT_RESOURCE, contact_id),
        ).fetchone()
    if row is None or float(row["expires_at"]) <= now:
        return None
    return {
        "lease_id": row["lease_id"],
        "holder": row["holder"],
        "fencing_token": str(row["fencing_token"]),
        "expires_at": float(row["expires_at"]),
    }


def pipeline(
    store: "Any",
    tenant_id: str,
    *,
    business_id: str | None = None,
    now: float | None = None,
    aging_seconds: float = 7 * 24 * 3600,
) -> list[dict[str, Any]]:
    """Return every contact's stage with age and an aging flag."""
    moment = _epoch(now)
    entries: list[dict[str, Any]] = []
    for contact in store.list_contacts(tenant_id, business_id=business_id):
        stage, changed, actor = _stage_record(store, tenant_id, contact)
        age = max(0.0, moment - changed.timestamp())
        assignment = _active_assignment(
            store, tenant_id, contact.contact_id, moment
        )
        entries.append(
            {
                "contact_id": contact.contact_id,
                "business_id": contact.business_id,
                "display_name": contact.display_name,
                "kind": contact.kind,
                "stage": stage,
                "stage_changed_at": changed.isoformat().replace(
                    "+00:00", "Z"
                ),
                "stage_actor": actor,
                "age_seconds": round(age, 3),
                "aging": age > float(aging_seconds)
                and stage not in TERMINAL_STAGES,
                "assigned_to": assignment["holder"] if assignment else None,
            }
        )
    return entries


def pipeline_summary(entries: list[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {stage: 0 for stage in PIPELINE_STAGES}
    aging = 0
    for entry in entries:
        stage = str(entry.get("stage") or "new")
        counts[stage] = counts.get(stage, 0) + 1
        if entry.get("aging"):
            aging += 1
    return {"counts": counts, "total": len(entries), "aging": aging}


# -- assignment leases (single owner) ------------------------------------
def assign(
    store: "Any",
    tenant_id: str,
    contact_id: str,
    *,
    holder: str,
    ttl_seconds: float = 300.0,
    actor: str = "system",
    idempotency_key: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Acquire the single assignment lease for a contact."""
    contact = store.get_contact(tenant_id, contact_id)
    who = _text(holder, "holder", max_len=200)
    lease = runs.acquire_lease(
        store,
        tenant_id,
        ASSIGNMENT_RESOURCE,
        contact.contact_id,
        holder=who,
        ttl_seconds=ttl_seconds,
        actor=actor,
        now=now,
    )
    _append(
        store,
        tenant_id,
        contact.business_id,
        contact.contact_id,
        ASSIGNMENT_ACQUIRED_EVENT,
        {
            "contact_id": contact.contact_id,
            "lease_id": lease.lease_id,
            "holder": lease.holder,
            "fencing_token": lease.fencing_token,
            "expires_at": lease.expires_at,
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"crm:assign:{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )
    return {
        "lease_id": lease.lease_id,
        "contact_id": contact.contact_id,
        "holder": lease.holder,
        "fencing_token": lease.fencing_token,
        "expires_at": lease.expires_at,
    }


def release_assignment(
    store: "Any",
    tenant_id: str,
    contact_id: str,
    *,
    lease_id: str,
    holder: str,
    fencing_token: str,
    actor: str = "system",
    idempotency_key: str,
    now: float | None = None,
) -> dict[str, Any]:
    contact = store.get_contact(tenant_id, contact_id)
    lease = runs.release_lease(
        store,
        tenant_id,
        lease_id,
        holder=holder,
        fencing_token=fencing_token,
        actor=actor,
        now=now,
    )
    _append(
        store,
        tenant_id,
        contact.business_id,
        contact.contact_id,
        ASSIGNMENT_RELEASED_EVENT,
        {
            "contact_id": contact.contact_id,
            "lease_id": lease.lease_id,
            "holder": lease.holder,
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"crm:release:{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )
    return {
        "lease_id": lease.lease_id,
        "contact_id": contact.contact_id,
        "holder": lease.holder,
        "released": lease.released_at is not None,
    }


def assignment(
    store: "Any", tenant_id: str, contact_id: str, *,
    now: float | None = None,
) -> dict[str, Any] | None:
    store.get_contact(tenant_id, contact_id)
    return _active_assignment(store, tenant_id, contact_id, _epoch(now))


# -- support tickets ------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Ticket:
    ticket_id: str
    tenant_id: str
    business_id: str
    contact_id: str
    subject: str
    body: str
    channel: str
    priority: str
    status: str
    assignee: str | None
    sla_due_at: str
    opened_at: str
    overdue: bool
    seconds_remaining: float
    handoffs: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "contact_id": self.contact_id,
            "subject": self.subject,
            "body": self.body,
            "channel": self.channel,
            "priority": self.priority,
            "status": self.status,
            "assignee": self.assignee,
            "sla_due_at": self.sla_due_at,
            "opened_at": self.opened_at,
            "overdue": self.overdue,
            "seconds_remaining": self.seconds_remaining,
            "handoffs": list(self.handoffs),
        }


def _ticket_task(store: "Any", tenant_id: str, ticket_id: str) -> models.Task:
    task = store.get_task(tenant_id, ticket_id)
    if (task.metadata or {}).get("kind") != "crm.support.ticket":
        raise BusinessError(
            CRM_TICKET_NOT_FOUND_CODE,
            f"task {ticket_id!r} is not a CRM support ticket",
        )
    return task


def _ticket_from_task(
    store: "Any", tenant_id: str, task: models.Task, now: float
) -> Ticket:
    metadata = task.metadata or {}
    body = ""
    opened_at = task.created_at
    handoffs: list[dict[str, Any]] = []
    for envelope in _events_for(store, tenant_id, task.task_id):
        payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
        if envelope.type == TICKET_OPENED_EVENT:
            body = str(payload.get("body") or "")
            opened_at = envelope.occurred_at
        elif envelope.type == TICKET_HANDOFF_EVENT:
            handoffs.append(
                {
                    "from": payload.get("from"),
                    "to": payload.get("to"),
                    "reason": payload.get("reason"),
                    "at": envelope.occurred_at,
                    "sla_breached": bool(payload.get("sla_breached")),
                }
            )
    due_raw = str(metadata.get("sla_due_at") or "")
    due = _parse_iso(due_raw)
    remaining = 0.0 if due is None else due.timestamp() - now
    return Ticket(
        ticket_id=task.task_id,
        tenant_id=task.tenant_id,
        business_id=task.business_id,
        contact_id=str(metadata.get("contact_id") or ""),
        subject=task.title,
        body=body,
        channel=str(metadata.get("channel") or "email"),
        priority=str(metadata.get("priority") or "normal"),
        status=task.status,
        assignee=task.assigned_to,
        sla_due_at=due_raw,
        opened_at=opened_at,
        overdue=due is not None and remaining < 0,
        seconds_remaining=round(remaining, 3),
        handoffs=tuple(handoffs),
    )


def open_ticket(
    store: "Any",
    tenant_id: str,
    business_id: str,
    contact_id: str,
    *,
    subject: str,
    body: str,
    channel: str = "email",
    priority: str = "normal",
    sla_seconds: float | None = None,
    assigned_to: str | None = None,
    actor: str = "system",
    idempotency_key: str,
    now: float | None = None,
) -> Ticket:
    """Open a support ticket as a real business task plus an SLA record."""
    contact = store.get_contact(tenant_id, contact_id)
    if contact.business_id != business_id:
        raise BusinessError(
            CRM_TICKET_SCOPE_CODE,
            "the contact belongs to a different business",
        )
    clean_subject = _text(subject, "subject", max_len=500)
    clean_body = _text(body, "body", max_len=MAX_TICKET_BODY)
    clean_channel = _channel(channel)
    clean_priority = _priority(priority)
    moment = _epoch(now)
    seconds = (
        float(sla_seconds)
        if sla_seconds is not None
        else float(PRIORITY_SLA_SECONDS[clean_priority])
    )
    if seconds <= 0:
        raise BusinessError(BAD_INPUT_CODE, "sla_seconds must be positive")
    due = now_iso(moment + seconds)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    existing = _event_by_key(store, tenant_id, f"crm:ticket:{key}")
    if existing is not None:
        if existing.type != TICKET_OPENED_EVENT:
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                "the idempotency key was used for a different CRM operation",
            )
        task = _ticket_task(store, tenant_id, existing.aggregate_id)
        return _ticket_from_task(store, tenant_id, task, moment)
    assignee = _optional_text(assigned_to, "assigned_to", max_len=200)
    task = store.create_task(
        tenant_id,
        business_id,
        clean_subject,
        assigned_to=assignee,
        metadata={
            "kind": "crm.support.ticket",
            "contact_id": contact.contact_id,
            "channel": clean_channel,
            "priority": clean_priority,
            "sla_due_at": due,
            "sla_seconds": seconds,
            "opened_at": now_iso(moment),
        },
    )
    _append(
        store,
        tenant_id,
        business_id,
        task.task_id,
        TICKET_OPENED_EVENT,
        {
            "ticket_id": task.task_id,
            "contact_id": contact.contact_id,
            "subject": clean_subject,
            "body": clean_body,
            "channel": clean_channel,
            "priority": clean_priority,
            "sla_due_at": due,
            "assigned_to": assignee,
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=f"crm:ticket:{key}",
        now=now,
    )
    return get_ticket(store, tenant_id, task.task_id, now=now)


def get_ticket(
    store: "Any",
    tenant_id: str,
    ticket_id: str,
    *,
    now: float | None = None,
) -> Ticket:
    task = _ticket_task(store, tenant_id, ticket_id)
    return _ticket_from_task(store, tenant_id, task, _epoch(now))


def list_tickets(
    store: "Any",
    tenant_id: str,
    *,
    business_id: str | None = None,
    status: str | None = None,
    now: float | None = None,
) -> list[Ticket]:
    moment = _epoch(now)
    tickets: list[Ticket] = []
    for task in store.list_tasks(
        tenant_id, business_id=business_id, status=status
    ):
        if (task.metadata or {}).get("kind") != "crm.support.ticket":
            continue
        tickets.append(_ticket_from_task(store, tenant_id, task, moment))
    return tickets


def handoff_ticket(
    store: "Any",
    tenant_id: str,
    ticket_id: str,
    *,
    to_assignee: str,
    reason: str = "",
    actor: str,
    idempotency_key: str,
    now: float | None = None,
) -> Ticket:
    """Move the single assignee and record the SLA state at handoff."""
    ticket = get_ticket(store, tenant_id, ticket_id, now=now)
    if ticket.status in ("done", "cancelled"):
        raise BusinessError(
            CRM_TICKET_TERMINAL_CODE,
            f"ticket {ticket_id!r} is {ticket.status!r}; it cannot be handed off",
        )
    target = _text(to_assignee, "to_assignee", max_len=200)
    if target == ticket.assignee:
        return ticket
    store.set_task_status(
        tenant_id,
        ticket_id,
        ticket.status,
        actor=_text(actor, "actor", max_len=200),
        assigned_to=target,
    )
    _append(
        store,
        tenant_id,
        ticket.business_id,
        ticket_id,
        TICKET_HANDOFF_EVENT,
        {
            "from": ticket.assignee,
            "to": target,
            "reason": str(reason or "")[:500],
            "sla_due_at": ticket.sla_due_at,
            "sla_breached": ticket.overdue,
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"crm:handoff:{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )
    return get_ticket(store, tenant_id, ticket_id, now=now)


def set_ticket_status(
    store: "Any",
    tenant_id: str,
    ticket_id: str,
    status: str,
    *,
    actor: str,
    idempotency_key: str,
    now: float | None = None,
) -> Ticket:
    ticket = get_ticket(store, tenant_id, ticket_id, now=now)
    store.set_task_status(
        tenant_id, ticket_id, status, actor=_text(actor, "actor", max_len=200)
    )
    _append(
        store,
        tenant_id,
        ticket.business_id,
        ticket_id,
        TICKET_STATUS_EVENT,
        {"from": ticket.status, "to": status},
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"crm:status:{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )
    return get_ticket(store, tenant_id, ticket_id, now=now)


def ticket_sla(
    store: "Any",
    tenant_id: str,
    ticket_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    ticket = get_ticket(store, tenant_id, ticket_id, now=now)
    return {
        "ticket_id": ticket.ticket_id,
        "status": ticket.status,
        "sla_due_at": ticket.sla_due_at,
        "overdue": ticket.overdue,
        "seconds_remaining": ticket.seconds_remaining,
        "breaches": sum(1 for item in ticket.handoffs if item["sla_breached"]),
    }


# -- notes vs customer messages -----------------------------------------
def check_customer_content(body: str) -> dict[str, Any]:
    """Deterministic brand/legal check for a customer-visible message."""
    text = str(body or "").strip()
    lowered = text.lower()
    findings: list[dict[str, str]] = []
    if not text:
        findings.append(
            {"code": "empty", "severity": "error", "detail": "body is empty"}
        )
    if len(text) > MAX_MESSAGE_BODY:
        findings.append(
            {
                "code": "too_long",
                "severity": "error",
                "detail": f"body exceeds {MAX_MESSAGE_BODY} characters",
            }
        )
    for phrase in _PROHIBITED_CLAIMS:
        if phrase in lowered:
            findings.append(
                {
                    "code": "prohibited_claim",
                    "severity": "error",
                    "detail": f"prohibited claim: {phrase!r}",
                }
            )
    for phrase in _FAKE_ENGAGEMENT:
        if phrase in lowered:
            findings.append(
                {
                    "code": "fake_engagement",
                    "severity": "error",
                    "detail": f"engagement abuse: {phrase!r}",
                }
            )
    for phrase in _SPAM_PHRASES:
        if phrase in lowered:
            findings.append(
                {
                    "code": "spam",
                    "severity": "error",
                    "detail": f"spam pattern: {phrase!r}",
                }
            )
    return {
        "ok": not any(item["severity"] == "error" for item in findings),
        "findings": findings,
        "body_sha256": fingerprint({"body": text}),
    }


@dataclass(frozen=True, slots=True)
class SupportMessage:
    message_id: str
    ticket_id: str
    contact_id: str
    business_id: str
    channel: str
    body: str
    visibility: str
    approval_id: str | None
    actor: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "ticket_id": self.ticket_id,
            "contact_id": self.contact_id,
            "business_id": self.business_id,
            "channel": self.channel,
            "body": self.body,
            "visibility": self.visibility,
            "approval_id": self.approval_id,
            "actor": self.actor,
            "created_at": self.created_at,
        }


def add_note(
    store: "Any",
    tenant_id: str,
    ticket_id: str,
    *,
    body: str,
    actor: str,
    idempotency_key: str,
    visibility: str = "internal",
    now: float | None = None,
) -> SupportMessage:
    """Record an internal note. It is never customer-visible."""
    ticket = get_ticket(store, tenant_id, ticket_id, now=now)
    if str(visibility or "").strip().lower() != "internal":
        raise BusinessError(
            CRM_NOTE_VISIBILITY_CODE,
            "a note is internal-only; use send_customer_message with an"
            " approval for anything the customer can see",
        )
    clean_body = _text(body, "body", max_len=MAX_MESSAGE_BODY)
    envelope = _append(
        store,
        tenant_id,
        ticket.business_id,
        ticket_id,
        NOTE_EVENT,
        {
            "ticket_id": ticket_id,
            "contact_id": ticket.contact_id,
            "body": clean_body,
            "visibility": "internal",
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"crm:note:{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )
    return SupportMessage(
        message_id=envelope.event_id,
        ticket_id=ticket_id,
        contact_id=ticket.contact_id,
        business_id=ticket.business_id,
        channel=ticket.channel,
        body=clean_body,
        visibility="internal",
        approval_id=None,
        actor=envelope.actor,
        created_at=envelope.occurred_at,
    )


def _message_intent(
    ticket: Ticket,
    *,
    body: str,
    channel: str,
    policy_version: str,
) -> dict[str, Any]:
    return approvals.normalized_intent(
        SUPPORT_MESSAGE_ACTION,
        f"ticket:{ticket.ticket_id}",
        {
            "ticket_id": ticket.ticket_id,
            "contact_id": ticket.contact_id,
            "channel": channel,
            "body": body,
            "body_sha256": fingerprint({"body": body}),
            "visibility": "customer",
        },
        "USD",
        0,
        policy_version,
        business_id=ticket.business_id,
    )


def request_message_approval(
    store: "Any",
    tenant_id: str,
    ticket_id: str,
    *,
    body: str,
    channel: str | None = None,
    requested_by: str,
    idempotency_key: str,
    policy_version: str = "v1",
    expires_at: float | None = None,
    now: float | None = None,
) -> models.Approval:
    """Ask a human to approve the exact customer-visible message payload."""
    ticket = get_ticket(store, tenant_id, ticket_id, now=now)
    clean_body = _text(body, "body", max_len=MAX_MESSAGE_BODY)
    clean_channel = _channel(channel or ticket.channel)
    check = check_customer_content(clean_body)
    if not check["ok"]:
        raise BusinessError(
            CRM_CONTENT_REFUSED_CODE,
            "customer message failed the brand/legal check: "
            + "; ".join(item["detail"] for item in check["findings"]),
        )
    intent = _message_intent(
        ticket,
        body=clean_body,
        channel=clean_channel,
        policy_version=policy_version,
    )
    approval, _created = approvals.request(
        store,
        tenant_id=tenant_id,
        business_id=ticket.business_id,
        action=SUPPORT_MESSAGE_ACTION,
        requested_by=_text(requested_by, "requested_by", max_len=200),
        idempotency_key=_text(idempotency_key, "key", max_len=300),
        payload=intent,
        policy_version=policy_version,
        expires_at=expires_at,
    )
    return approval


def send_customer_message(
    store: "Any",
    tenant_id: str,
    ticket_id: str,
    *,
    body: str,
    approval_id: str,
    actor: str,
    idempotency_key: str,
    channel: str | None = None,
    policy_version: str = "v1",
    consent_required: bool = True,
    now: float | None = None,
) -> SupportMessage:
    """Record an approved, brand-checked, consent-safe customer message."""
    ticket = get_ticket(store, tenant_id, ticket_id, now=now)
    clean_body = _text(body, "body", max_len=MAX_MESSAGE_BODY)
    clean_channel = _channel(channel or ticket.channel)
    check = check_customer_content(clean_body)
    if not check["ok"]:
        raise BusinessError(
            CRM_CONTENT_REFUSED_CODE,
            "customer message failed the brand/legal check: "
            + "; ".join(item["detail"] for item in check["findings"]),
        )
    if not str(approval_id or "").strip():
        raise BusinessError(
            CRM_MESSAGE_APPROVAL_CODE,
            "a customer-visible message requires an explicit approval_id",
        )
    assert_contactable(
        store,
        tenant_id,
        ticket.contact_id,
        clean_channel,
        require_consent=consent_required,
    )
    intent = _message_intent(
        ticket,
        body=clean_body,
        channel=clean_channel,
        policy_version=policy_version,
    )
    approvals.verify_execution(
        store,
        tenant_id,
        approval_id,
        intent,
        now=now,
        current_policy_version=policy_version,
    )
    envelope = _append(
        store,
        tenant_id,
        ticket.business_id,
        ticket_id,
        MESSAGE_EVENT,
        {
            "ticket_id": ticket_id,
            "contact_id": ticket.contact_id,
            "channel": clean_channel,
            "body": clean_body,
            "body_sha256": check["body_sha256"],
            "visibility": "customer",
            "approval_id": approval_id,
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"crm:message:{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )
    return SupportMessage(
        message_id=envelope.event_id,
        ticket_id=ticket_id,
        contact_id=ticket.contact_id,
        business_id=ticket.business_id,
        channel=clean_channel,
        body=clean_body,
        visibility="customer",
        approval_id=approval_id,
        actor=envelope.actor,
        created_at=envelope.occurred_at,
    )


def _messages(
    store: "Any", tenant_id: str, ticket_id: str, event_type: str
) -> list[SupportMessage]:
    ticket = get_ticket(store, tenant_id, ticket_id)
    messages: list[SupportMessage] = []
    for envelope in _events_for(store, tenant_id, ticket_id):
        if envelope.type != event_type:
            continue
        payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
        messages.append(
            SupportMessage(
                message_id=envelope.event_id,
                ticket_id=ticket_id,
                contact_id=str(payload.get("contact_id") or ""),
                business_id=ticket.business_id,
                channel=str(payload.get("channel") or ticket.channel),
                body=str(payload.get("body") or ""),
                visibility="customer"
                if envelope.type == MESSAGE_EVENT
                else "internal",
                approval_id=payload.get("approval_id"),
                actor=envelope.actor,
                created_at=envelope.occurred_at,
            )
        )
    return messages


def list_notes(
    store: "Any", tenant_id: str, ticket_id: str
) -> list[SupportMessage]:
    """Internal notes only; customer messages never appear here."""
    return _messages(store, tenant_id, ticket_id, NOTE_EVENT)


def list_customer_messages(
    store: "Any", tenant_id: str, ticket_id: str
) -> list[SupportMessage]:
    """Customer-visible messages only; internal notes never appear here."""
    return _messages(store, tenant_id, ticket_id, MESSAGE_EVENT)


__all__ = [
    "ASSIGNMENT_RESOURCE",
    "CHANNELS",
    "CONTACT_CREATED_EVENT",
    "CONTACT_MATCHED_EVENT",
    "CONSENT_EVENT",
    "CRM_CONSENT_BLOCKED_CODE",
    "CRM_CONTENT_REFUSED_CODE",
    "CRM_IDENTITY_CONFLICT_CODE",
    "CRM_IDENTITY_REQUIRED_CODE",
    "CRM_MESSAGE_APPROVAL_CODE",
    "CRM_NOTE_VISIBILITY_CODE",
    "CRM_STAGE_UNKNOWN_CODE",
    "CRM_SUPPRESSED_CODE",
    "CRM_TICKET_NOT_FOUND_CODE",
    "CRM_TICKET_SCOPE_CODE",
    "CRM_TICKET_TERMINAL_CODE",
    "MESSAGE_EVENT",
    "NOTE_EVENT",
    "PIPELINE_STAGES",
    "PRIORITY_SLA_SECONDS",
    "SUPPORT_MESSAGE_ACTION",
    "SUPPRESSION_EVENT",
    "SupportMessage",
    "TICKET_HANDOFF_EVENT",
    "TICKET_OPENED_EVENT",
    "Ticket",
    "add_note",
    "assert_contactable",
    "assign",
    "assignment",
    "check_customer_content",
    "consent_state",
    "get_contact",
    "get_ticket",
    "handoff_ticket",
    "list_customer_messages",
    "list_notes",
    "list_tickets",
    "open_ticket",
    "pipeline",
    "pipeline_summary",
    "record_consent",
    "record_suppression",
    "release_assignment",
    "request_message_approval",
    "send_customer_message",
    "set_stage",
    "set_ticket_status",
    "ticket_sla",
    "upsert_contact",
]
