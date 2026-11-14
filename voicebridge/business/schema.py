"""SQLite schema and forward-only migrations.

One database, one owner. Every table is prefixed ``bos_`` so the file can never
be confused with the office ledger (``office.db``) or the session store. Money
is integer minor units; a float is a bug class this schema cannot store.

Append-only is enforced by the database, not by discipline: the ledger, job
event log, audit log and tool-invocation history each carry ``BEFORE UPDATE``
and ``BEFORE DELETE`` triggers that raise ``ABORT``. A correction is a new row.
"""
from __future__ import annotations

SCHEMA_VERSION = 5

_V1 = [
    """
    CREATE TABLE bos_tenant (
        tenant_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(name)
    )
    """,
    """
    CREATE TABLE bos_business (
        business_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        name TEXT NOT NULL,
        slug TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft','active','paused','archived')),
        currency TEXT NOT NULL DEFAULT 'USD' CHECK(length(currency) = 3),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(tenant_id, slug)
    )
    """,
    """
    CREATE TABLE bos_contact (
        contact_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        kind TEXT NOT NULL CHECK(kind IN ('customer','lead','vendor','partner')),
        display_name TEXT NOT NULL,
        email TEXT,
        external_ref TEXT,
        created_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE UNIQUE INDEX bos_contact_email
        ON bos_contact(tenant_id, business_id, lower(email))
        WHERE email IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX bos_contact_external_ref
        ON bos_contact(tenant_id, business_id, external_ref)
        WHERE external_ref IS NOT NULL
    """,
    """
    CREATE TABLE bos_task (
        task_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','doing','done','cancelled')),
        assigned_to TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE bos_job (
        job_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        kind TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN
            ('created','queued','running','waiting_approval','unknown',
             'failed','completed','cancelled')),
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        input_json TEXT NOT NULL DEFAULT '{}',
        output_json TEXT,
        attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
        lease_owner TEXT,
        lease_expires_at REAL,
        last_error_code TEXT,
        last_error_detail TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX bos_job_listing ON bos_job(tenant_id, business_id, status)
    """,
    """
    CREATE TABLE bos_job_event (
        event_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES bos_job(job_id) ON DELETE RESTRICT,
        seq INTEGER NOT NULL CHECK(seq > 0),
        kind TEXT NOT NULL,
        actor TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(job_id, seq)
    )
    """,
    """
    CREATE TABLE bos_approval (
        approval_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        job_id TEXT REFERENCES bos_job(job_id) ON DELETE RESTRICT,
        action TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN
            ('pending','approved','rejected','expired','cancelled')),
        requested_by TEXT NOT NULL,
        decided_by TEXT,
        reason TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        policy_version TEXT NOT NULL DEFAULT 'v1',
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        decided_at TEXT,
        expires_at REAL,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX bos_approval_listing
        ON bos_approval(tenant_id, business_id, status)
    """,
    """
    CREATE TABLE bos_ledger_entry (
        entry_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        job_id TEXT REFERENCES bos_job(job_id) ON DELETE RESTRICT,
        account TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('debit','credit')),
        amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
        currency TEXT NOT NULL CHECK(length(currency) = 3),
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        memo TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX bos_ledger_listing
        ON bos_ledger_entry(tenant_id, business_id, created_at)
    """,
    """
    CREATE TABLE bos_artifact (
        artifact_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        job_id TEXT REFERENCES bos_job(job_id) ON DELETE RESTRICT,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
        body TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE bos_audit_event (
        event_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        business_id TEXT,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        resource_id TEXT,
        outcome TEXT NOT NULL CHECK(outcome IN ('ok','refused','error')),
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX bos_audit_listing
        ON bos_audit_event(tenant_id, business_id, created_at)
    """,
    """
    CREATE TABLE bos_budget (
        budget_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        currency TEXT NOT NULL CHECK(length(currency) = 3),
        period TEXT NOT NULL,
        limit_minor INTEGER NOT NULL CHECK(limit_minor >= 0),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, business_id, currency, period)
    )
    """,
    """
    CREATE TABLE bos_tool_invocation (
        invocation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        actor TEXT NOT NULL,
        tool TEXT NOT NULL,
        autonomy TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)),
        status TEXT NOT NULL CHECK(status IN
            ('pending','success','failed','dry_run')),
        currency TEXT NOT NULL DEFAULT 'USD' CHECK(length(currency) = 3),
        cost_minor INTEGER NOT NULL DEFAULT 0 CHECK(cost_minor >= 0),
        request_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT,
        error_code TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key, dry_run)
    )
    """,
    """
    CREATE INDEX bos_tool_listing
        ON bos_tool_invocation(tenant_id, business_id, created_at)
    """,
]

# DB-enforced immutability for the record-of-truth tables.
# Tool invocations are *not* here: the row is reserved as ``pending`` under a
# unique idempotency key, then completed to ``success``/``failed``. The
# append-only record of what happened is the audit event, which is immutable.
_APPEND_ONLY_TABLES = (
    "bos_ledger_entry",
    "bos_job_event",
    "bos_audit_event",
)
for _table in _APPEND_ONLY_TABLES:
    _V1.append(
        f"""
        CREATE TRIGGER {_table}_no_update
        BEFORE UPDATE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )
    _V1.append(
        f"""
        CREATE TRIGGER {_table}_no_delete
        BEFORE DELETE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )

# -- v2: War Room + per-agent control plane -------------------------------
# Additive only. War-room evidence/debate/decisions and agent usage/operations
# are append-only truth; agent turns may transition once from active.
_V2 = [
    """
    CREATE TABLE bos_warroom_session (
        session_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        title TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'intake'
            CHECK(state IN ('intake','research','debate','synthesis',
                            'owner_decision','approved','rejected',
                            'tasks_created')),
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        brief_json TEXT NOT NULL DEFAULT '{}',
        score_json TEXT NOT NULL DEFAULT '{}',
        dissent_json TEXT NOT NULL DEFAULT '[]',
        owner_note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )""",
    """
    CREATE INDEX bos_warroom_session_listing
        ON bos_warroom_session(tenant_id, state, created_at)""",
    """
    CREATE TABLE bos_warroom_source (
        source_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        session_id TEXT NOT NULL REFERENCES bos_warroom_session(session_id)
            ON DELETE RESTRICT,
        kind TEXT NOT NULL CHECK(kind IN
            ('youtube','url','post','note','image','doc')),
        locator TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','extracted','failed')),
        adapter TEXT NOT NULL DEFAULT '',
        artifact_id TEXT REFERENCES bos_artifact(artifact_id) ON DELETE RESTRICT,
        text TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, session_id, kind, locator)
    )""",
    """
    CREATE TABLE bos_warroom_evidence (
        evidence_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        session_id TEXT NOT NULL REFERENCES bos_warroom_session(session_id)
            ON DELETE RESTRICT,
        label TEXT NOT NULL CHECK(label IN
            ('FACT','INFERENCE','ASSUMPTION','UNKNOWN')),
        claim TEXT NOT NULL,
        source_id TEXT REFERENCES bos_warroom_source(source_id) ON DELETE RESTRICT,
        quote TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, session_id, label, claim)
    )""",
    """
    CREATE TABLE bos_warroom_argument (
        argument_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        session_id TEXT NOT NULL REFERENCES bos_warroom_session(session_id)
            ON DELETE RESTRICT,
        round_no INTEGER NOT NULL CHECK(round_no > 0),
        role TEXT NOT NULL,
        stance TEXT NOT NULL CHECK(stance IN
            ('support','oppose','neutral','question')),
        text TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, session_id, round_no, role, stance, text)
    )""",
    """
    CREATE TABLE bos_warroom_experiment (
        experiment_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        session_id TEXT NOT NULL REFERENCES bos_warroom_session(session_id)
            ON DELETE RESTRICT,
        name TEXT NOT NULL,
        hypothesis TEXT NOT NULL DEFAULT '',
        method TEXT NOT NULL DEFAULT '',
        success_metric TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'planned'
            CHECK(status IN ('planned','running','done','abandoned')),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, session_id, name)
    )""",
    """
    CREATE TABLE bos_warroom_decision (
        decision_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        session_id TEXT NOT NULL REFERENCES bos_warroom_session(session_id)
            ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK(status IN ('approved','rejected')),
        decided_by TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )""",
    """
    CREATE TABLE bos_agent (
        agent_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        role_id TEXT NOT NULL,
        department TEXT NOT NULL,
        display_name TEXT NOT NULL,
        human_owner TEXT NOT NULL,
        room TEXT,
        desk TEXT,
        status TEXT NOT NULL CHECK(status IN
            ('active','paused','revoked','archived')),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        reasoning_effort TEXT NOT NULL,
        fallback_model TEXT,
        fallback_provider TEXT,
        temperature REAL,
        max_tokens INTEGER NOT NULL CHECK(max_tokens > 0),
        schedule TEXT NOT NULL,
        timezone TEXT NOT NULL,
        max_concurrent_turns INTEGER NOT NULL CHECK(max_concurrent_turns > 0),
        permissions_json TEXT NOT NULL,
        safety_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        taken_over_by TEXT,
        taken_over_at TEXT,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT,
        UNIQUE(tenant_id, idempotency_key)
    )""",
    """
    CREATE INDEX bos_agent_listing
        ON bos_agent(tenant_id, business_id, status)""",
    """
    CREATE INDEX bos_agent_role_listing
        ON bos_agent(tenant_id, role_id)""",
    """
    CREATE TABLE bos_agent_turn (
        turn_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        agent_id TEXT NOT NULL REFERENCES bos_agent(agent_id) ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK(status IN
            ('active','interrupted','completed','failed')),
        started_at TEXT NOT NULL,
        ended_at TEXT,
        note TEXT NOT NULL DEFAULT ''
    )""",
    """
    CREATE INDEX bos_agent_turn_active
        ON bos_agent_turn(tenant_id, agent_id, status)""",
    """
    CREATE TABLE bos_agent_usage (
        usage_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        agent_id TEXT NOT NULL REFERENCES bos_agent(agent_id) ON DELETE RESTRICT,
        turn_id TEXT,
        kind TEXT NOT NULL,
        cost_minor INTEGER NOT NULL DEFAULT 0 CHECK(cost_minor >= 0),
        tokens INTEGER NOT NULL DEFAULT 0 CHECK(tokens >= 0),
        day TEXT NOT NULL,
        month TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )""",
    """
    CREATE INDEX bos_agent_usage_listing
        ON bos_agent_usage(tenant_id, agent_id, created_at)""",
    """
    CREATE TABLE bos_agent_operation (
        operation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        agent_id TEXT NOT NULL REFERENCES bos_agent(agent_id) ON DELETE RESTRICT,
        action TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )""",
]

for _table in (
    "bos_warroom_evidence",
    "bos_warroom_argument",
    "bos_warroom_decision",
    "bos_agent_usage",
    "bos_agent_operation",
):
    _V2.append(
        f"""
        CREATE TRIGGER {_table}_no_update
        BEFORE UPDATE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )
    _V2.append(
        f"""
        CREATE TRIGGER {_table}_no_delete
        BEFORE DELETE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )

# -- v3: local durable substrate -----------------------------------------
# Additive only. The checkpoint log is append-only (recovery points are never
# rewritten); the outbox is the one intentionally mutable table because a
# delivery moves ``pending -> claimed -> sent|unknown|dead``. Every outbox
# transition is written to the append-only audit log in the same transaction,
# so the mutable row cannot change without a durable history beside it.
#
# Temporal stays a later adapter; this is the same control plane the rest of
# the runtime already uses, not a second database and not a second executor.
_V3 = [
    """
    CREATE TABLE bos_checkpoint (
        checkpoint_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        run_id TEXT NOT NULL,
        job_id TEXT,
        sequence INTEGER NOT NULL CHECK(sequence >= 1),
        state_json TEXT NOT NULL DEFAULT '{}',
        pending_actions_json TEXT NOT NULL DEFAULT '[]',
        tool_receipts_json TEXT NOT NULL DEFAULT '[]',
        artifact_ids_json TEXT NOT NULL DEFAULT '[]',
        workspace_revision TEXT,
        policy_revision TEXT,
        request_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(run_id, sequence)
    )
    """,
    """
    CREATE INDEX bos_checkpoint_run
        ON bos_checkpoint(tenant_id, run_id, sequence)
    """,
    """
    CREATE INDEX bos_checkpoint_business
        ON bos_checkpoint(tenant_id, business_id, created_at)
    """,
    """
    CREATE TABLE bos_outbox (
        outbox_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        aggregate_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','claimed','sent','unknown','dead')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        lease_owner TEXT,
        lease_expires_at REAL,
        provider_receipt TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        sent_at TEXT,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX bos_outbox_listing
        ON bos_outbox(tenant_id, status, created_at)
    """,
    """
    CREATE INDEX bos_outbox_lease
        ON bos_outbox(tenant_id, status, lease_expires_at)
    """,
    """
    CREATE INDEX bos_outbox_aggregate
        ON bos_outbox(tenant_id, aggregate_id, created_at)
    """,
]

for _table in ("bos_checkpoint",):
    _V3.append(
        f"""
        CREATE TRIGGER {_table}_no_update
        BEFORE UPDATE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )
    _V3.append(
        f"""
        CREATE TRIGGER {_table}_no_delete
        BEFORE DELETE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )

# -- v4: identity/RBAC, authoritative event log, run/task/workspace/lease --
# Additive only. v1-v3 are untouched.
#
# * Identity: principals are per-tenant; a grant is a *revocable* mutable row
#   (changing authority must be auditable, so grant/revoke writes an audit
#   consequence). Policy revisions are append-only: a new revision is a new row.
# * Event log: ``bos_event`` is append-only by trigger. ``bos_projection_checkpoint``
#   is intentionally mutable (a checkpoint advances); rebuilds read the log, not
#   the checkpoint.
# * ``bos_task`` already exists from v1 with a legacy four-value ``status``
#   CHECK. SQLite cannot widen a CHECK constraint in place, so the durable
#   workflow status is added as a new ``workflow_status`` column (with the P1-04
#   domain) plus the objective/ownership/dependency fields. Legacy
#   ``status``/``assigned_to`` keep their meaning and rows, and ``task_class``
#   separates legacy business tasks from durable execution tasks.
_V4 = [
    """
    CREATE TABLE bos_principal (
        principal_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        kind TEXT NOT NULL CHECK(kind IN ('owner','agent','service','run')),
        external_ref TEXT,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','suspended','revoked')),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, external_ref)
    )
    """,
    """
    CREATE INDEX bos_principal_listing
        ON bos_principal(tenant_id, kind, status)
    """,
    """
    CREATE TABLE bos_role (
        role_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        name TEXT NOT NULL,
        permissions_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, name)
    )
    """,
    """
    CREATE TABLE bos_grant (
        grant_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        subject_id TEXT NOT NULL REFERENCES bos_principal(principal_id)
            ON DELETE RESTRICT,
        role_id TEXT REFERENCES bos_role(role_id) ON DELETE RESTRICT,
        capability TEXT NOT NULL,
        resource TEXT NOT NULL,
        action TEXT NOT NULL,
        data_class TEXT NOT NULL DEFAULT 'internal',
        environment TEXT NOT NULL DEFAULT 'production',
        effect TEXT NOT NULL DEFAULT 'allow' CHECK(effect IN ('allow','deny')),
        expires_at REAL,
        approval_required INTEGER NOT NULL DEFAULT 0
            CHECK(approval_required IN (0,1)),
        revoked_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX bos_grant_subject
        ON bos_grant(tenant_id, subject_id, capability, resource, action)
    """,
    """
    CREATE INDEX bos_grant_expiry
        ON bos_grant(tenant_id, expires_at)
    """,
    """
    CREATE TABLE bos_auth_session (
        auth_session_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        subject_id TEXT NOT NULL REFERENCES bos_principal(principal_id)
            ON DELETE RESTRICT,
        audience TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at REAL NOT NULL,
        revoked_at TEXT,
        token_hash TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE INDEX bos_auth_session_subject
        ON bos_auth_session(tenant_id, subject_id, expires_at)
    """,
    """
    CREATE TABLE bos_policy_revision (
        revision_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        version TEXT NOT NULL,
        effective_at TEXT NOT NULL,
        doc_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, version)
    )
    """,
    """
    CREATE INDEX bos_policy_revision_listing
        ON bos_policy_revision(tenant_id, effective_at)
    """,
    """
    CREATE TABLE bos_event (
        event_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        aggregate_id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq > 0),
        type TEXT NOT NULL,
        actor TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        causation_id TEXT,
        idempotency_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        policy_version TEXT NOT NULL DEFAULT 'v1',
        occurred_at TEXT NOT NULL,
        retention_class TEXT NOT NULL DEFAULT 'standard'
            CHECK(retention_class IN
                ('ephemeral','standard','extended','legal-hold')),
        tombstoned_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, aggregate_id, seq),
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX bos_event_aggregate
        ON bos_event(tenant_id, aggregate_id, seq)
    """,
    """
    CREATE INDEX bos_event_timeline
        ON bos_event(tenant_id, occurred_at, event_id)
    """,
    """
    CREATE TABLE bos_projection_checkpoint (
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        projection TEXT NOT NULL,
        aggregate_id TEXT NOT NULL DEFAULT '',
        last_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_seq >= 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id, projection, aggregate_id)
    )
    """,
    """
    ALTER TABLE bos_task ADD COLUMN objective TEXT NOT NULL DEFAULT ''
    """,
    """
    ALTER TABLE bos_task ADD COLUMN owner_principal TEXT
    """,
    """
    ALTER TABLE bos_task ADD COLUMN assignee_principal TEXT
    """,
    """
    ALTER TABLE bos_task ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '[]'
    """,
    """
    ALTER TABLE bos_task ADD COLUMN required_capabilities_json
        TEXT NOT NULL DEFAULT '[]'
    """,
    """
    ALTER TABLE bos_task ADD COLUMN data_class TEXT NOT NULL DEFAULT 'internal'
    """,
    """
    ALTER TABLE bos_task ADD COLUMN budget_json TEXT NOT NULL DEFAULT '{}'
    """,
    """
    ALTER TABLE bos_task ADD COLUMN deadline TEXT
    """,
    """
    ALTER TABLE bos_task ADD COLUMN acceptance_json TEXT NOT NULL DEFAULT '[]'
    """,
    """
    ALTER TABLE bos_task ADD COLUMN artifact_ids_json TEXT NOT NULL DEFAULT '[]'
    """,
    """
    ALTER TABLE bos_task ADD COLUMN task_class TEXT NOT NULL DEFAULT 'business'
        CHECK(task_class IN ('business','execution'))
    """,
    """
    ALTER TABLE bos_task ADD COLUMN workflow_status TEXT NOT NULL DEFAULT 'draft'
        CHECK(workflow_status IN
            ('draft','assigned','claimed','blocked','handed_off',
             'completed','rejected','cancelled'))
    """,
    """
    ALTER TABLE bos_task ADD COLUMN handed_off_from TEXT
    """,
    """
    ALTER TABLE bos_task ADD COLUMN handed_off_to TEXT
    """,
    """
    ALTER TABLE bos_task ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0
        CHECK(read_only IN (0,1))
    """,
    """
    CREATE INDEX bos_task_workflow
        ON bos_task(tenant_id, business_id, workflow_status)
    """,
    """
    CREATE TABLE bos_workspace (
        workspace_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        task_id TEXT NOT NULL REFERENCES bos_task(task_id) ON DELETE RESTRICT,
        session_id TEXT NOT NULL,
        repository_ref TEXT,
        worktree_ref TEXT,
        storage_class TEXT NOT NULL DEFAULT 'ephemeral'
            CHECK(storage_class IN ('ephemeral','persistent','archive')),
        access_policy_json TEXT NOT NULL DEFAULT '{}',
        current_revision TEXT NOT NULL,
        artifact_manifest_json TEXT NOT NULL DEFAULT '[]',
        retention_policy TEXT NOT NULL DEFAULT 'standard',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX bos_workspace_task
        ON bos_workspace(tenant_id, task_id, workspace_id)
    """,
    """
    CREATE TABLE bos_lease (
        lease_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        resource_type TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        holder TEXT NOT NULL,
        fencing_token TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        expires_at REAL NOT NULL,
        heartbeat_at TEXT NOT NULL,
        released_at TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX bos_lease_active
        ON bos_lease(tenant_id, resource_type, resource_id)
        WHERE released_at IS NULL
    """,
    """
    CREATE INDEX bos_lease_holder
        ON bos_lease(tenant_id, holder, released_at)
    """,
]

for _table in ("bos_event", "bos_policy_revision"):
    _V4.append(
        f"""
        CREATE TRIGGER {_table}_no_update
        BEFORE UPDATE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )
    _V4.append(
        f"""
        CREATE TRIGGER {_table}_no_delete
        BEFORE DELETE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
        """
    )

# -- v5: durable sessions and explicit handoffs ---------------------------
# Additive only. v1-v4 are untouched.
#
# * ``bos_session`` is the durable owner<->agent lane. It points at the existing
#   ``bos_agent`` and its workspace is a real ``bos_workspace`` row (task
#   pinned), so this table never duplicates run/task/workspace truth.
# * ``bos_handoff`` is the explicit transfer record. A task may have at most one
#   *proposed* handoff at a time (partial unique index); accepting is the only
#   path that transfers the durable task, and rejecting leaves ownership alone.
# * ``bos_agent.security_principal_id`` makes the authorization subject explicit
#   instead of inferred from a desk/room. It is nullable for read-old rows; an
#   agent without a principal simply has no effective grants (deny by default).
_V5 = [
    """
    ALTER TABLE bos_agent ADD COLUMN security_principal_id TEXT
        REFERENCES bos_principal(principal_id) ON DELETE RESTRICT
    """,
    """
    CREATE TABLE bos_session (
        session_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id)
            ON DELETE RESTRICT,
        agent_id TEXT NOT NULL REFERENCES bos_agent(agent_id) ON DELETE RESTRICT,
        owner_principal TEXT NOT NULL,
        participants_json TEXT NOT NULL DEFAULT '[]',
        visibility TEXT NOT NULL DEFAULT 'private'
            CHECK(visibility IN ('private','owner','participants',
                                 'department','office','tenant')),
        default_memory_scopes_json TEXT NOT NULL DEFAULT '[]',
        default_workspace_id TEXT
            REFERENCES bos_workspace(workspace_id) ON DELETE RESTRICT,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','paused','archived')),
        last_checkpoint_id TEXT,
        policy_revision INTEGER NOT NULL DEFAULT 1
            CHECK(policy_revision >= 1),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT
    )
    """,
    """
    CREATE INDEX bos_session_listing
        ON bos_session(tenant_id, business_id, status)
    """,
    """
    CREATE INDEX bos_session_agent
        ON bos_session(tenant_id, agent_id, status)
    """,
    """
    CREATE INDEX bos_session_owner
        ON bos_session(tenant_id, owner_principal)
    """,
    """
    CREATE TABLE bos_handoff (
        handoff_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id)
            ON DELETE RESTRICT,
        task_id TEXT NOT NULL REFERENCES bos_task(task_id) ON DELETE RESTRICT,
        session_id TEXT NOT NULL REFERENCES bos_session(session_id)
            ON DELETE RESTRICT,
        from_principal TEXT NOT NULL,
        to_principal TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT REFERENCES bos_checkpoint(checkpoint_id)
            ON DELETE RESTRICT,
        artifact_ids_json TEXT NOT NULL DEFAULT '[]',
        unresolved_questions_json TEXT NOT NULL DEFAULT '[]',
        required_capabilities_json TEXT NOT NULL DEFAULT '[]',
        acceptance_deadline TEXT,
        status TEXT NOT NULL DEFAULT 'proposed'
            CHECK(status IN ('proposed','accepted','rejected','cancelled')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        accepted_at TEXT
    )
    """,
    """
    CREATE INDEX bos_handoff_listing
        ON bos_handoff(tenant_id, business_id, status, created_at)
    """,
    """
    CREATE INDEX bos_handoff_session
        ON bos_handoff(tenant_id, session_id, status)
    """,
    """
    CREATE INDEX bos_handoff_task
        ON bos_handoff(tenant_id, task_id, status)
    """,
    """
    CREATE UNIQUE INDEX bos_handoff_one_proposed
        ON bos_handoff(tenant_id, task_id)
        WHERE status='proposed'
    """,
]

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, tuple(_V1)),
    (2, tuple(_V2)),
    (3, tuple(_V3)),
    (4, tuple(_V4)),
    (5, tuple(_V5)),
)
