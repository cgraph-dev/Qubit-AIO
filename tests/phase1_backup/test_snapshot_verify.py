"""P1-09 snapshot/verify happy path and tamper refusals.

Hermetic: throwaway SQLite files and snapshot directories under ``tmp_path``.
No network, no live service, no operator data.
"""
from __future__ import annotations

import json
import sqlite3
import stat

import pytest

from voicebridge.business import BusinessStore, approvals, durable, jobs, ledger
from voicebridge.business import backup
from voicebridge.business.errors import BusinessError


def _seed(tmp_path, label: str = "Acme"):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant(f"{label} Holdings")
    business = store.create_business(tenant.tenant_id, label, slug=label.lower())
    store.create_contact(
        tenant.tenant_id,
        business.business_id,
        kind="customer",
        display_name=f"{label} Customer",
        email=f"buyer@{label.lower()}.test",
    )
    store.create_task(tenant.tenant_id, business.business_id, "Reconcile invoices")

    invoiced, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="send-invoice",
        idempotency_key=f"{label}-job-1",
        input={"invoice": 7},
    )
    jobs.enqueue(store, tenant.tenant_id, invoiced.job_id)
    jobs.claim(
        store,
        tenant.tenant_id,
        invoiced.job_id,
        worker="worker-approval",
        lease_seconds=60.0,
    )
    approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="send-invoice",
        requested_by="owner",
        idempotency_key=f"{label}-approval-1",
        job_id=invoiced.job_id,
        payload={"invoice": 7},
        expires_at=10**12,
    )

    running, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="sync-crm",
        idempotency_key=f"{label}-job-2",
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
        idempotency_key=f"{label}-ledger-1",
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
        idempotency_key=f"{label}-ledger-2",
    )

    store.record_artifact(
        tenant.tenant_id,
        business.business_id,
        kind="receipt",
        name="receipt.txt",
        body="paid 12.50",
    )
    for sequence in (1, 2):
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
        idempotency_key=f"{label}-outbox-1",
        actor="tester",
    )
    return store, tenant, business


def test_encrypted_roundtrip_restores_rows_and_append_only_truth(tmp_path):
    store, tenant, business = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"

    manifest = backup.snapshot(store, snapshot_dir)
    assert manifest["schema"] == backup.BACKUP_SCHEMA
    assert manifest["scope"] == {"tenant_id": None}
    assert manifest["row_counts"]["total"] > 0
    assert (snapshot_dir / backup.MANIFEST_NAME).is_file()
    assert (snapshot_dir / "business.db.enc").is_file()
    key_file = snapshot_dir / backup.KEY_NAME
    assert key_file.is_file()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    report = backup.verify_snapshot(snapshot_dir)
    assert report["ok"] is True
    assert report["encrypted"] is True
    assert report["table_counts"]["bos_ledger_entry"] == 2
    assert report["table_counts"]["bos_checkpoint"] == 2

    target = tmp_path / "restored" / "business.db"
    restored_report = backup.restore(snapshot_dir, target)
    assert restored_report["scope"] == {"tenant_id": None}
    assert restored_report["table_counts"]["bos_ledger_entry"] == 2

    restored = BusinessStore(target)
    summary = ledger.summary(restored, tenant.tenant_id)
    assert summary["credit_minor"] == 1250
    assert summary["debit_minor"] == 250
    assert summary["net_minor"] == 1000
    assert [
        row.sequence for row in durable.list_checkpoints(
            restored, tenant.tenant_id, "run-1"
        )
    ] == [1, 2]
    assert restored.list_artifacts(tenant.tenant_id)[0].name == "receipt.txt"
    assert restored.list_contacts(tenant.tenant_id)[0].display_name == (
        "Acme Customer"
    )

    # Append-only is a database invariant, not a convention.
    ledger.append_only_check(restored)
    conn = sqlite3.connect(restored.path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE bos_ledger_entry SET amount_minor=0")
    finally:
        conn.close()


def test_plaintext_snapshot_is_explicit_and_restores(tmp_path):
    store, tenant, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "plain-snapshot"

    manifest = backup.snapshot(store, snapshot_dir, encrypt=False)
    assert manifest["encryption"]["enabled"] is False
    assert (snapshot_dir / "business.db").is_file()
    assert not (snapshot_dir / backup.KEY_NAME).exists()

    report = backup.verify_snapshot(snapshot_dir)
    assert report["encrypted"] is False
    target = tmp_path / "plain-restored.db"
    backup.restore(snapshot_dir, target)
    restored = BusinessStore(target)
    assert ledger.summary(restored, tenant.tenant_id)["net_minor"] == 1000


def test_tampered_snapshot_file_is_refused(tmp_path):
    store, _, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    stored = snapshot_dir / "business.db.enc"
    payload = bytearray(stored.read_bytes())
    payload[-1] ^= 0xFF
    stored.write_bytes(bytes(payload))

    with pytest.raises(BusinessError) as refused:
        backup.verify_snapshot(snapshot_dir)
    assert refused.value.code == backup.BACKUP_HASH_MISMATCH_CODE

    with pytest.raises(BusinessError) as restore_refused:
        backup.restore(snapshot_dir, tmp_path / "never-created.db")
    assert restore_refused.value.code == backup.BACKUP_HASH_MISMATCH_CODE
    assert not (tmp_path / "never-created.db").exists()


def test_missing_manifest_is_refused(tmp_path):
    store, _, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)
    (snapshot_dir / backup.MANIFEST_NAME).unlink()

    with pytest.raises(BusinessError) as refused:
        backup.verify_snapshot(snapshot_dir)
    assert refused.value.code == backup.BACKUP_MANIFEST_MISSING_CODE

    with pytest.raises(BusinessError) as restore_refused:
        backup.restore(snapshot_dir, tmp_path / "no-manifest.db")
    assert restore_refused.value.code == backup.BACKUP_MANIFEST_MISSING_CODE
    assert not (tmp_path / "no-manifest.db").exists()


def test_wrong_key_is_refused_when_encrypted(tmp_path):
    store, _, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    with pytest.raises(BusinessError) as refused:
        backup.restore(
            snapshot_dir, tmp_path / "wrong-key.db", key=b"\x01" * 32
        )
    assert refused.value.code == backup.BACKUP_BAD_KEY_CODE
    assert not (tmp_path / "wrong-key.db").exists()


def test_missing_key_file_is_refused(tmp_path):
    store, _, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)
    (snapshot_dir / backup.KEY_NAME).unlink()

    # The key file is a declared snapshot file: losing it is an integrity
    # failure, so restore refuses before touching the target.
    with pytest.raises(BusinessError) as refused:
        backup.restore(snapshot_dir, tmp_path / "no-key.db")
    assert refused.value.code == backup.BACKUP_FILE_MISSING_CODE
    assert not (tmp_path / "no-key.db").exists()


def test_crypto_unavailable_fails_closed_instead_of_writing_plaintext(
    tmp_path, monkeypatch
):
    store, _, _ = _seed(tmp_path)

    def unavailable():
        raise BusinessError(
            backup.BACKUP_CRYPTO_UNAVAILABLE_CODE,
            "cryptography is not importable",
        )

    monkeypatch.setattr(backup, "_load_crypto", unavailable)
    snapshot_dir = tmp_path / "no-crypto"
    with pytest.raises(BusinessError) as refused:
        backup.snapshot(store, snapshot_dir)
    assert refused.value.code == backup.BACKUP_CRYPTO_UNAVAILABLE_CODE
    assert not (snapshot_dir / "business.db").exists()
    assert not (snapshot_dir / "business.db.enc").exists()

    # An explicit plaintext snapshot still works without the crypto package.
    plain = tmp_path / "plain"
    backup.snapshot(store, plain, encrypt=False)
    assert (plain / "business.db").is_file()


def test_foreign_manifest_and_undeclared_files_are_refused(tmp_path):
    store, _, _ = _seed(tmp_path)
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(store, snapshot_dir)

    manifest_file = snapshot_dir / backup.MANIFEST_NAME
    manifest = json.loads(manifest_file.read_text())
    manifest["schema"] = "some-other-backup/9"
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(BusinessError) as refused:
        backup.verify_snapshot(snapshot_dir)
    assert refused.value.code == backup.BACKUP_MANIFEST_INVALID_CODE

    # A second snapshot for the undeclared-file case.
    other = tmp_path / "other"
    backup.snapshot(store, other)
    (other / "stray.bin").write_bytes(b"not ours")
    with pytest.raises(BusinessError) as stray_refused:
        backup.verify_snapshot(other)
    assert stray_refused.value.code == backup.BACKUP_MANIFEST_INVALID_CODE


def test_snapshot_refuses_a_non_empty_destination(tmp_path):
    store, _, _ = _seed(tmp_path)
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    with pytest.raises(BusinessError) as refused:
        backup.snapshot(store, dest)
    assert refused.value.code == backup.BACKUP_DEST_EXISTS_CODE
