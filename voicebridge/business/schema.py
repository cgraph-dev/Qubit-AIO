"""SQLite schema and forward-only migrations.

One database, one owner. Every table is prefixed ``bos_`` so the file can never
be confused with the office ledger (``office.db``) or the session store. Money
is integer minor units; a float is a bug class this schema cannot store.

Append-only is enforced by the database, not by discipline: the ledger, job
event log, audit log and tool-invocation history each carry ``BEFORE UPDATE``
and ``BEFORE DELETE`` triggers that raise ``ABORT``. A correction is a new row.
"""
from __future__ import annotations

SCHEMA_VERSION = 2

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

# -- v2: War Room / Idea Lab ------------------------------------------------
# Additive only. A war-room session is an intake that a human started; every
# claim it records is an evidence row with an explicit label, every debate turn
# is append-only, and the owner decision is its own immutable row. Nothing here
# can publish, spend or contact anyone: those remain tool-gateway actions that
# require a pending approval.
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
    )
    """,
    """
    CREATE INDEX bos_warroom_session_listing
        ON bos_warroom_session(tenant_id, state, created_at)
    """,
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
    )
    """,
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
    )
    """,
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
    )
    """,
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
    )
    """,
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
    )
    """,
]

# Evidence, debate turns and the owner decision are a record of what was said
# and decided. A correction is a new row, never an edit: dissent cannot be
# quietly deleted before a decision is made.
for _table in (
    "bos_warroom_evidence",
    "bos_warroom_argument",
    "bos_warroom_decision",
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

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, tuple(_V1)),
    (2, tuple(_V2)),
)
