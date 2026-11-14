"""P1-09 tenant-scoped restore and target-overwrite safety.

Hermetic: two synthetic tenants in one throwaway database, snapshotted under
``tmp_path``. No network, no live service.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge.business import BusinessStore, durable, jobs, ledger
from voicebridge.business import backup
from voicebridge.business.errors import BusinessError


def _seed_tenant(store: BusinessStore, label: str):
    tenant = store.create_tenant(f"{label} Holdings")
    business = store.create_business(tenant.tenant_id, label, slug=label.lower())
    job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="send-invoice",
        idempotency_key=f"{label}-job-1",
        input={"invoice": 1},
    )
    jobs.enqueue(store, tenant.tenant_id, job.job_id)
    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="cash",
        direction="credit",
        amount_minor=100,
        currency="USD",
        idempotency_key=f"{label}-ledger-1",
        job_id=job.job_id,
    )
    durable.record_checkpoint(
        store,
        tenant.tenant_id,
        business.business_id,
        f"{label}-run-1",
        1,
        state={"tenant": label},
        actor="tester",
    )
    durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        f"{label}-aggregate",
        "email",
        {"to": f"owner@{label.lower()}.test"},
        idempotency_key=f"{label}-outbox-1",
        actor="tester",
    )
    return tenant, business


def _two_tenants(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    acme, acme_business = _seed_tenant(store, "Acme")
    beta, beta_business = _seed_tenant(store, "Beta")
    return store, acme, acme_business, beta, beta_business


def _count(path, sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def test_tenant_scoped_restore_copies_only_that_tenant(tmp_path):
    store, acme, _, beta, _ = _two_tenants(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    target = tmp_path / "tenant-a.db"
    report = backup.restore(snapshot_dir, target, tenant_id=acme.tenant_id)
    assert report["tenants"] == [acme.tenant_id]
    assert report["scope"] == {"tenant_id": acme.tenant_id}
    restored = BusinessStore(target)
    assert len(restored.list_tenants()) == 1
    assert restored.list_tenants()[0].tenant_id == acme.tenant_id
    assert len(restored.list_businesses(acme.tenant_id)) == 1
    assert ledger.summary(restored, acme.tenant_id)["net_minor"] == 100

    # Beta cannot be reached at all, and no unscoped table carries its rows.
    with pytest.raises(BusinessError):
        restored.require_tenant(beta.tenant_id)
    assert ledger.summary(restored, beta.tenant_id)["entry_count"] == 0
    for table in (
        "bos_business",
        "bos_job",
        "bos_ledger_entry",
        "bos_checkpoint",
        "bos_outbox",
    ):
        assert _count(
            target,
            f'SELECT COUNT(*) FROM "{table}" WHERE tenant_id<>?',
            (acme.tenant_id,),
        ) == 0
    assert durable.list_checkpoints(restored, acme.tenant_id, "Acme-run-1")[0].state == {
        "tenant": "Acme"
    }


def test_tenant_snapshot_physically_excludes_other_tenants(tmp_path):
    store, acme, _, beta, _ = _two_tenants(tmp_path)
    snapshot_dir = tmp_path / "acme-only"
    manifest = backup.snapshot(store, snapshot_dir, tenant_id=acme.tenant_id)
    assert manifest["tenants"] == [acme.tenant_id]
    assert manifest["table_counts"]["bos_tenant"] == 1
    assert manifest["table_counts"]["bos_ledger_entry"] == 1

    # Restoring the whole scoped snapshot cannot resurrect Beta.
    target = tmp_path / "from-scoped.db"
    report = backup.restore(snapshot_dir, target)
    assert report["tenants"] == [acme.tenant_id]
    restored = BusinessStore(target)
    assert [tenant.tenant_id for tenant in restored.list_tenants()] == [
        acme.tenant_id
    ]
    assert _count(
        target,
        "SELECT COUNT(*) FROM bos_ledger_entry WHERE tenant_id=?",
        (beta.tenant_id,),
    ) == 0


def test_tenant_restore_refuses_an_absent_tenant(tmp_path):
    store, _acme, _, _beta, _ = _two_tenants(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    target = tmp_path / "missing-tenant.db"
    with pytest.raises(BusinessError) as refused:
        backup.restore(snapshot_dir, target, tenant_id="ten_does_not_exist")
    assert refused.value.code == backup.BACKUP_TENANT_NOT_FOUND_CODE
    assert not target.exists()


def test_tenant_restore_refuses_a_tenant_outside_a_scoped_snapshot(tmp_path):
    store, acme, _, beta, _ = _two_tenants(tmp_path)
    snapshot_dir = tmp_path / "acme-only"
    backup.snapshot(store, snapshot_dir, tenant_id=acme.tenant_id)

    with pytest.raises(BusinessError) as refused:
        backup.restore(snapshot_dir, tmp_path / "beta.db", tenant_id=beta.tenant_id)
    assert refused.value.code == backup.BACKUP_TENANT_NOT_FOUND_CODE


def test_restore_never_overwrites_an_existing_database(tmp_path):
    store, acme, _, _, _ = _two_tenants(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    live = tmp_path / "live.db"
    BusinessStore(live).create_tenant("Live Install")
    before = live.read_bytes()

    with pytest.raises(BusinessError) as refused:
        backup.restore(snapshot_dir, live, tenant_id=acme.tenant_id)
    assert refused.value.code == backup.BACKUP_TARGET_EXISTS_CODE
    assert live.read_bytes() == before


def test_restore_refuses_a_directory_as_target(tmp_path):
    store, acme, _, _, _ = _two_tenants(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    target_dir = tmp_path / "a-directory"
    target_dir.mkdir()
    with pytest.raises(BusinessError) as refused:
        backup.restore(snapshot_dir, target_dir, tenant_id=acme.tenant_id)
    assert refused.value.code == backup.BACKUP_TARGET_EXISTS_CODE


def test_cross_tenant_leakage_is_a_typed_refusal(tmp_path, monkeypatch):
    """The copy seam is sabotaged to prove the post-copy guard is real."""
    store, acme, _, beta, _ = _two_tenants(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    original = backup._copy_tenant_rows

    def leaky(src, dst, tenant_id):
        copied = original(src, dst, tenant_id)
        # ``bos_audit_event`` carries tenant_id without a tenant/business
        # foreign key, so this simulates a copy bug that smuggles another
        # tenant's row into the target.
        columns = backup._columns(src, "bos_audit_event")
        column_list = ",".join(f'"{name}"' for name in columns)
        placeholders = ",".join("?" for _ in columns)
        row = src.execute(
            f'SELECT {column_list} FROM "bos_audit_event" WHERE tenant_id=? LIMIT 1',
            (beta.tenant_id,),
        ).fetchone()
        dst.execute(
            f'INSERT INTO "bos_audit_event"({column_list}) VALUES({placeholders})',
            tuple(row),
        )
        return copied

    monkeypatch.setattr(backup, "_copy_tenant_rows", leaky)
    with pytest.raises(BusinessError) as refused:
        backup.restore(
            snapshot_dir, tmp_path / "leaky.db", tenant_id=acme.tenant_id
        )
    assert refused.value.code == backup.BACKUP_CROSS_TENANT_LEAK_CODE


def test_tenant_snapshot_of_an_absent_tenant_refuses_and_writes_nothing(tmp_path):
    store, _acme, _, _beta, _ = _two_tenants(tmp_path)
    dest = tmp_path / "unknown-tenant-snapshot"
    with pytest.raises(BusinessError) as refused:
        backup.snapshot(store, dest, tenant_id="ten_missing")
    assert refused.value.code == backup.BACKUP_TENANT_NOT_FOUND_CODE
    assert not (dest / "business.db").exists()
    assert not (dest / "business.db.enc").exists()
