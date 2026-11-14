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

from . import (
    agents,
    approvals,
    durable,
    jobs,
    ledger,
    office_seats,
    role_catalog,
    roles,
    tools,
    warroom,
)
from .durable import (
    Checkpoint,
    OutboxMessage,
    claim_outbox,
    enqueue_outbox,
    get_outbox,
    heartbeat_outbox,
    list_checkpoints,
    mark_outbox_dead,
    mark_outbox_sent,
    mark_outbox_unknown,
    reconcile_outbox,
    record_checkpoint,
    recover_outbox,
)
from .agents import (
    Agent,
    AgentControlService,
    AgentTurn,
    AgentUsage,
    Permissions,
    Safety,
)
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
from .roles import (
    Department,
    FloorTemplate,
    RoleRegistry,
    RoleRegistryError,
    RoleTemplate,
    RoomTemplate,
)
from .office_seats import (
    PersonalScope,
    SeatSyncResult,
    ensure_personal_scope,
    plan_building_seats,
    sync_office_seats,
)
from .etsy_workflow import (
    EtsyDigitalListingWorkflow,
    ListingWorkflowState,
    WORKFLOW_STAGES,
)
from .integration_tools import (
    IntegrationTool,
    IntegrationToolRegistry,
    integration_tools,
)
from .store import BusinessStore
from .tools import (
    ExecutorResult,
    Policy,
    ToolGateway,
    ToolOutcome,
    ToolRequest,
)

__all__ = [
    "Agent",
    "AgentControlService",
    "AgentTurn",
    "AgentUsage",
    "Approval",
    "Artifact",
    "AuditEvent",
    "Business",
    "BusinessError",
    "BusinessStore",
    "Checkpoint",
    "Contact",
    "Department",
    "OutboxMessage",
    "EtsyDigitalListingWorkflow",
    "ExecutorResult",
    "FloorTemplate",
    "IntegrationTool",
    "IntegrationToolRegistry",
    "JobEvent",
    "JobRun",
    "LedgerEntry",
    "ListingWorkflowState",
    "Permissions",
    "PersonalScope",
    "Policy",
    "RoleRegistry",
    "RoleRegistryError",
    "RoleTemplate",
    "RoomTemplate",
    "Safety",
    "SeatSyncResult",
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
    "WORKFLOW_STAGES",
    "agents",
    "approvals",
    "claim_outbox",
    "durable",
    "enqueue_outbox",
    "ensure_personal_scope",
    "get_outbox",
    "heartbeat_outbox",
    "list_checkpoints",
    "mark_outbox_dead",
    "mark_outbox_sent",
    "mark_outbox_unknown",
    "office_seats",
    "plan_building_seats",
    "reconcile_outbox",
    "record_checkpoint",
    "recover_outbox",
    "sync_office_seats",
    "integration_tools",
    "jobs",
    "ledger",
    "role_catalog",
    "roles",
    "tools",
    "warroom",
]
