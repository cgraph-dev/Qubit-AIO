"""P1-09 replay verification, measured RPO/RTO and schema compatibility.

Hermetic: throwaway SQLite files under ``tmp_path``. No network, no live
service. The timing numbers are real wall-clock measurements from this run;
no SLA is asserted.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge.business import BusinessStore, durable, jobs, ledger
from voicebridge.business import backup
from voicebridge.business.errors import BusinessError
from voicebridge.business.schema import SCHEMA_VERSION


def _seed(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")

    invoiced, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="send-invoice",
        idempotency_key="job-1",
        input={"invoice": 7},
    )
    jobs.enqueue(store, tenant.tenant_id, invoiced.job_id)

    running, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="sync-crm",
        idempotency_key="job-2",
        input={"account": "crm-1"},
    )
    jobs.enqueue(store, tenant.tenant_id, running.job_id)
    jobs.claim(
        store,
        tenant.tenant_id,
        running.job_id,
        worker="worker-a",
        lease_seconds=10.0,
        now=1000.0,
    )

    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="cash",
        direction="credit",
        amount_minor=1250,
        currency="USD",
        idempotency_key="ledger-1",
        job_id=invoiced.job_id,
    )
    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="fees",
        direction="debit",
        amount_minor=250,
        currency="USD",
        idempotency_key="ledger-2",
    )
    for sequence in (1, 2, 3):
        durable.record_checkpoint(
            store,
            tenant.tenant_id,
            business.business_id,
            "run-1",
            sequence,
            state={"step": sequence},
            workspace_revision=f"ws-{sequence}",
            policy_revision="pol-1",
            actor="tester",
        )
    durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        "aggregate-1",
        "email",
        {"to": "customer@example.test"},
        idempotency_key="outbox-1",
        actor="tester",
    )
    return store, tenant, business


def test_replay_verification_passes_and_reports_durable_state(tmp_path):
    store, tenant, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)
    target = tmp_path / "restored.db"
    backup.restore(snapshot_dir, target)

    report = backup.replay_verify(snapshot_dir, target)
    assert report["ok"] is True
    assert report["schema"] == backup.REPLAY_SCHEMA
    assert report["tenants"] == [tenant.tenant_id]
    names = {check["name"] for check in report["checks"]}
    assert {
        "integrity_check",
        "foreign_key_check",
        "append_only_triggers",
        "job_event_sequence",
        "ledger_replay_equality",
        "jobs_recover",
        "post_recover_ledger_stable",
    } <= names
    assert report["checkpoints_by_run"] == {"run-1": 3}
    assert report["outbox_by_status"] == {"pending": 1}

    # The restart sweep is a real state change: the interrupted running job
    # becomes ``unknown``, never silently retried as completed.
    assert report["recovered_unknown_jobs"]
    recovered = jobs.get(
        BusinessStore(target), tenant.tenant_id, report["recovered_unknown_jobs"][0]
    )
    assert recovered.status == "unknown"


def test_replay_verification_refuses_a_missing_target(tmp_path):
    store, _, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)
    with pytest.raises(BusinessError) as refused:
        backup.replay_verify(snapshot_dir, tmp_path / "absent.db")
    assert refused.value.code == backup.BACKUP_RESTORE_FAILED_CODE


def test_rpo_rto_report_has_measured_values_and_no_sla_claim(tmp_path):
    store, _, _ = _seed(tmp_path)
    report = backup.rpo_rto_report(store, tmp_path / "measured")

    assert report["ok"] is True
    assert report["schema"] == backup.RPO_RTO_SCHEMA
    assert report["snapshot"]["seconds"] >= 0.0
    assert report["verify"]["seconds"] >= 0.0
    assert report["restore"]["seconds"] >= 0.0
    assert report["replay"]["seconds"] >= 0.0

    assert isinstance(report["rpo"]["rpo_seconds"], float)
    assert report["rpo"]["rpo_seconds"] >= 0.0
    assert report["rpo"]["sla_claimed"] is False
    assert report["rpo"]["newest_record_at"] is not None
    assert report["rpo"]["snapshot_started_at"] == report["snapshot"]["started_at"]

    assert isinstance(report["rto"]["rto_seconds"], float)
    assert report["rto"]["rto_seconds"] >= 0.0
    assert report["rto"]["rto_seconds"] == pytest.approx(
        report["rto"]["restore_seconds"] + report["rto"]["replay_seconds"],
        abs=1e-5,
    )
    assert report["rto"]["sla_claimed"] is False

    # A tenant-scoped measured cycle is also a supported shape.
    tenant_report = backup.rpo_rto_report(
        store,
        tmp_path / "measured-tenant",
        tenant_id=store.list_tenants()[0].tenant_id,
    )
    assert tenant_report["ok"] is True


def test_schema_newer_than_supported_is_refused(tmp_path):
    store, _, _ = _seed(tmp_path)
    conn = sqlite3.connect(str(store.path))
    try:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(BusinessError) as refused:
        backup.assert_compatible(store)
    assert refused.value.code == backup.BACKUP_SCHEMA_TOO_NEW_CODE

    with pytest.raises(BusinessError) as snapshot_refused:
        backup.snapshot(store, tmp_path / "too-new")
    assert snapshot_refused.value.code == backup.BACKUP_SCHEMA_TOO_NEW_CODE
    assert not (tmp_path / "too-new" / "business.db.enc").exists()


def test_assert_compatible_accepts_the_current_schema(tmp_path):
    store, _, _ = _seed(tmp_path)
    assert backup.assert_compatible(store) == {
        "schema_version": SCHEMA_VERSION,
        "supported": SCHEMA_VERSION,
    }


def test_snapshot_refuses_a_foreign_database(tmp_path):
    path = tmp_path / "not-business.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(BusinessError) as refused:
        backup.assert_compatible(BusinessStore(path))
    assert refused.value.code == backup.BACKUP_SOURCE_INVALID_CODE


def test_snapshot_refuses_a_missing_source(tmp_path):
    with pytest.raises(BusinessError) as refused:
        backup.assert_compatible(BusinessStore(tmp_path / "absent.db"))
    assert refused.value.code == backup.BACKUP_SOURCE_MISSING_CODE
