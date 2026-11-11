"""Qubit Business OS — the control-plane foundation.

This package is the durable spine, not an ERP. It owns:

* **Scope** — tenants, businesses, contacts, tasks, artifacts and every record
  carrying ``tenant_id``/``business_id``.
* **Truth** — an append-only ledger and event/audit log enforced by SQLite
  triggers; integer minor units only.
* **Durability** — WAL SQLite, a job state machine with leases, and an explicit
  ``unknown`` outcome for work interrupted by a restart.
* **Human control** — pending approvals pause a job; approve resumes it, reject
  or expiry cancels it; a decided approval cannot be decided twice.
* **One door** — :class:`ToolGateway` validates tenant, actor, policy, approval,
  budget and idempotency, supports dry-run and records a redacted result. The
  skeleton ships no external executor.

Deliberately out of scope here: Temporal/queues, Postgres, Stripe/CRM/accounting
adapters, UI, voice commands, real payouts. See
``docs/QUBIT-BUSINESS-OS-FOUNDATION.md`` for the boundary and the next steps.
"""
from __future__ import annotations

from . import approvals, jobs, ledger, tools, warroom
from .errors import BusinessError
from .models import (
    Approval,
    Artifact,
    AuditEvent,
    Business,
    Contact,
    JobEvent,
    JobRun,
    LedgerEntry,
    Task,
    Tenant,
    ToolInvocation,
    WarRoomArgument,
    WarRoomDecision,
    WarRoomEvidence,
    WarRoomExperiment,
    WarRoomSession,
    WarRoomSource,
)
from .store import BusinessStore
from .tools import Policy, ToolGateway, ToolOutcome, ToolRequest

__all__ = [
    "Approval",
    "Artifact",
    "AuditEvent",
    "Business",
    "BusinessError",
    "BusinessStore",
    "Contact",
    "JobEvent",
    "JobRun",
    "LedgerEntry",
    "Policy",
    "Task",
    "Tenant",
    "ToolGateway",
    "ToolInvocation",
    "ToolOutcome",
    "ToolRequest",
    "WarRoomArgument",
    "WarRoomDecision",
    "WarRoomEvidence",
    "WarRoomExperiment",
    "WarRoomSession",
    "WarRoomSource",
    "approvals",
    "jobs",
    "ledger",
    "tools",
    "warroom",
]
