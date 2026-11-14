"""P8-03 tamper refusal and crash/unknown recovery.

Hermetic: throwaway SQLite files under ``tmp_path``; no network, no service.
"""
from __future__ import annotations

import shutil

import pytest

from voicebridge.business import BusinessStore, backup, jobs, ledger
from voicebridge.business.errors import BusinessError
from voicebridge.business import disaster_recovery as dr

from _dr_helpers import seed_store


def _tamper(snapshot_dir):
    target = snapshot_dir / backup.ENCRYPTED_NAME
    payload = bytearray(target.read_bytes())
    payload[-1] ^= 0xFF
    target.write_bytes(bytes(payload))


def test_tampered_snapshot_is_refused_before_any_write(tmp_path):
    seed = seed_store(tmp_path / "live")
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(seed.store, snapshot_dir)
    _tamper(snapshot_dir)

    with pytest.raises(BusinessError) as verify:
        backup.verify_snapshot(snapshot_dir)
    assert verify.value.code == backup.BACKUP_HASH_MISMATCH_CODE

    target = tmp_path / "restored" / "business.db"
    with pytest.raises(BusinessError) as restore:
        dr.restore_snapshot(snapshot_dir, target)
    assert restore.value.code == backup.BACKUP_HASH_MISMATCH_CODE
    assert not target.exists()


def test_tamper_after_a_good_restore_does_not_touch_the_good_copy(tmp_path):
    seed = seed_store(tmp_path / "live")
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(seed.store, snapshot_dir)

    good = tmp_path / "restored" / "business.db"
    report = dr.restore_snapshot(snapshot_dir, good)
    assert report["ok"] is True

    tampered = tmp_path / "tampered"
    shutil.copytree(snapshot_dir, tampered)
    _tamper(tampered)
    with pytest.raises(BusinessError):
        dr.restore_snapshot(tampered, tmp_path / "restored2" / "business.db")

    # The already-restored good database still reads its rows.
    restored = BusinessStore(good)
    summary = ledger.summary(restored, seed.tenant_id)
    assert summary["net_minor"] == 1000


def test_expired_work_becomes_unknown_and_recovery_is_idempotent(tmp_path):
    seed = seed_store(tmp_path / "live")
    before = ledger.summary(seed.store, seed.tenant_id)

    first = dr.recover_unknown(seed.store, now=10**9)
    assert first["schema"] == dr.RECOVERY_SCHEMA
    assert first["retried"] is False
    assert first["unknown_jobs"] == [seed.running_job]
    assert first["unknown_outbox"] == [seed.claimed_outbox]

    assert jobs.get(
        seed.store, seed.tenant_id, seed.running_job
    ).status == "unknown"

    second = dr.recover_unknown(seed.store, now=10**9)
    assert second["unknown_jobs"] == []
    assert second["unknown_outbox"] == []

    after = ledger.summary(seed.store, seed.tenant_id)
    assert after == before


def test_recovery_runs_on_a_fresh_restore_not_the_live_file(tmp_path):
    seed = seed_store(tmp_path / "live")
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(seed.store, snapshot_dir)
    target = tmp_path / "restored" / "business.db"
    dr.restore_snapshot(snapshot_dir, target, live_db=seed.store.path)

    restored = BusinessStore(target)
    recovered = dr.recover_unknown(restored, now=10**9)
    assert seed.running_job in recovered["unknown_jobs"]
    assert seed.claimed_outbox in recovered["unknown_outbox"]

    # The live source still holds the running/claimed rows recovery did not
    # create there.
    assert jobs.get(
        seed.store, seed.tenant_id, seed.running_job
    ).status == "running"
