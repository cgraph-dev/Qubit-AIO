"""Crash recovery, file restore, and v1/v2 -> v3 migration.

Hermetic: throwaway SQLite files under ``tmp_path``; no network, no service.
A "process death" here is closing every connection and reopening the store,
which is exactly what a restart does to a WAL SQLite file.
"""
from __future__ import annotations

import shutil
import sqlite3

from voicebridge.business import BusinessStore
from voicebridge.business import durable, jobs, ledger
from voicebridge.business.schema import MIGRATIONS, SCHEMA_VERSION


def _scope(store: BusinessStore):
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    return tenant, business


def _row_count(path, sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def test_crash_after_effect_keeps_sent_and_parks_the_job_unknown(tmp_path):
    path = tmp_path / "business.db"
    store = BusinessStore(path)
    tenant, business = _scope(store)

    job, created = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="send-invoice",
        idempotency_key="job-1",
        input={"invoice": 7},
    )
    assert created is True
    jobs.enqueue(store, tenant.tenant_id, job.job_id)
    running = jobs.claim(
        store, tenant.tenant_id, job.job_id, worker="worker-a",
        lease_seconds=60, now=1000.0,
    )
    assert running.status == "running"

    sent = durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        job.job_id,
        "send-invoice",
        {"invoice": 7},
        idempotency_key="effect-1",
        actor="worker-a",
    )
    durable.claim_outbox(
        store, tenant.tenant_id, sent.outbox_id, worker="worker-a",
        lease_seconds=60, now=1000.0,
    )
    durable.mark_outbox_sent(
        store, tenant.tenant_id, sent.outbox_id, worker="worker-a",
        provider_receipt={"provider_id": "prov-1"}, now=1001.0,
    )

    # Still-claimed second effect that never got an outcome before the crash.
    pending_effect = durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        job.job_id,
        "notify-crm",
        {"invoice": 7},
        idempotency_key="effect-2",
        actor="worker-a",
    )
    durable.claim_outbox(
        store, tenant.tenant_id, pending_effect.outbox_id, worker="worker-a",
        lease_seconds=10, now=1000.0,
    )

    # "Process death": every operation above closed its connection; reopen the
    # file and run the restart sweep on a clock past both leases.
    reopened = BusinessStore(path)
    recovered_jobs = jobs.recover(reopened, now=5000.0)
    assert [item.job_id for item in recovered_jobs] == [job.job_id]
    assert recovered_jobs[0].status == "unknown"

    recovered_outbox = durable.recover_outbox(reopened, now=5000.0)
    assert [item.outbox_id for item in recovered_outbox] == [
        pending_effect.outbox_id
    ]
    assert recovered_outbox[0].status == "unknown"

    # The completed effect is still sent, with its receipt, after the restart.
    still_sent = durable.get_outbox(reopened, tenant.tenant_id, sent.outbox_id)
    assert still_sent.status == "sent"
    assert still_sent.provider_receipt == {"provider_id": "prov-1"}

    # Reconciliation is explicit and can only move unknown -> sent|dead.
    reconciled = durable.reconcile_outbox(
        reopened,
        tenant.tenant_id,
        pending_effect.outbox_id,
        actor="reconciler",
        outcome="sent",
        note="CRM log shows the write",
        provider_receipt={"provider_id": "crm-9"},
    )
    assert reconciled.status == "sent"


def test_restore_from_a_copied_file_preserves_every_record(tmp_path):
    path = tmp_path / "business.db"
    store = BusinessStore(path)
    tenant, business = _scope(store)
    durable.record_checkpoint(
        store, tenant.tenant_id, business.business_id, "run-1", 1,
        state={"step": 1}, workspace_revision="ws-1", policy_revision="pol-1",
        actor="tester",
    )
    durable.record_checkpoint(
        store, tenant.tenant_id, business.business_id, "run-1", 2,
        state={"step": 2}, workspace_revision="ws-2", policy_revision="pol-1",
        actor="tester",
    )
    entry, _ = ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="cash",
        direction="credit",
        amount_minor=1250,
        currency="USD",
        idempotency_key="ledger-1",
    )
    message = durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        "aggregate-1",
        "email",
        {"to": "customer@example.test"},
        idempotency_key="outbox-1",
        actor="tester",
    )

    restored_path = tmp_path / "restored.db"
    shutil.copy2(path, restored_path)
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, restored_path.with_name(restored_path.name + suffix))

    restored = BusinessStore(restored_path)
    checkpoints = durable.list_checkpoints(restored, tenant.tenant_id, "run-1")
    assert [row.sequence for row in checkpoints] == [1, 2]
    assert checkpoints[1].workspace_revision == "ws-2"
    assert checkpoints[1].policy_revision == "pol-1"
    assert ledger.get_entry(restored, tenant.tenant_id, entry.entry_id).amount_minor == (
        1250
    )
    assert durable.get_outbox(
        restored, tenant.tenant_id, message.outbox_id
    ).status == "pending"

    # The copy is independent: new work on the original never appears there.
    durable.record_checkpoint(
        store, tenant.tenant_id, business.business_id, "run-1", 3,
        state={"step": 3}, actor="tester",
    )
    assert len(durable.list_checkpoints(restored, tenant.tenant_id, "run-1")) == 2
    assert _row_count(
        restored_path, "SELECT COUNT(*) FROM bos_checkpoint WHERE run_id='run-1'"
    ) == 2


def test_v1_v2_database_migrates_to_v3_and_keeps_rows(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    try:
        for version, statements in MIGRATIONS:
            if version > 2:
                continue
            for statement in statements:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={version}")
        conn.execute(
            "INSERT INTO bos_tenant(tenant_id, name, created_at, metadata_json)"
            " VALUES('ten_legacy','Legacy','2026-01-01T00:00:00.000Z','{}')"
        )
        conn.execute(
            "INSERT INTO bos_business(business_id, tenant_id, name, slug, status,"
            " currency, created_at, updated_at, metadata_json)"
            " VALUES('biz_legacy','ten_legacy','Legacy Biz','legacy','active',"
            " 'USD','2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z','{}')"
        )
        conn.commit()
    finally:
        conn.close()

    store = BusinessStore(path)
    with store.read() as read_conn:
        version = int(read_conn.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            row[0]
            for row in read_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        tenant_names = [
            row[0] for row in read_conn.execute("SELECT name FROM bos_tenant")
        ]
        business_names = [
            row[0] for row in read_conn.execute("SELECT name FROM bos_business")
        ]
    assert version == SCHEMA_VERSION == 3
    assert {"bos_checkpoint", "bos_outbox"} <= tables
    # The v1/v2 tables and their rows are untouched.
    assert {"bos_job", "bos_agent", "bos_ledger_entry"} <= tables
    assert tenant_names == ["Legacy"]
    assert business_names == ["Legacy Biz"]

    # A checkpoint/outbox write works immediately on the migrated file.
    durable.record_checkpoint(
        store, "ten_legacy", "biz_legacy", "run-legacy", 1,
        state={"ok": True}, actor="tester",
    )

    # Opening an already-v3 file again is idempotent.
    again = BusinessStore(path)
    with again.read() as read_conn:
        assert int(read_conn.execute("PRAGMA user_version").fetchone()[0]) == 3
        assert read_conn.execute(
            "SELECT COUNT(*) FROM bos_checkpoint"
        ).fetchone()[0] == 1
        assert read_conn.execute(
            "SELECT COUNT(*) FROM bos_tenant"
        ).fetchone()[0] == 1
    assert _row_count(path, "SELECT COUNT(*) FROM bos_outbox") == 0
