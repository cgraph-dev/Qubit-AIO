"""P8-03 encrypted snapshot/restore, replay, tenant scope and RPO/RTO.

Hermetic: throwaway SQLite files and snapshot directories under ``tmp_path``.
No network, no live service, no operator data.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore, backup, ledger
from voicebridge.business.errors import BusinessError
from voicebridge.business import disaster_recovery as dr

from _dr_helpers import seed_store


def _add_second_tenant(store, label: str = "Beta"):
    tenant = store.create_tenant(f"{label} Holdings")
    business = store.create_business(tenant.tenant_id, label, slug=label.lower())
    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="cash",
        direction="credit",
        amount_minor=999,
        currency="USD",
        idempotency_key=f"{label.lower()}-ledger",
    )
    return tenant, business


def test_encrypted_roundtrip_replays_index_workflow_and_audit(tmp_path):
    seed = seed_store(tmp_path / "live")
    report = dr.run_restore_drill(seed.store, tmp_path / "drill")

    assert report["ok"] is True
    assert report["schema"] == dr.DR_SCHEMA
    assert report["snapshot"]["encrypted"] is True
    assert report["snapshot"]["snapshot_id"].startswith("bkp_")
    assert report["scope"] == {"tenant_id": None}
    assert report["restore"]["scope"] == {"tenant_id": None}
    assert report["restore"]["target"] == str(
        tmp_path / "drill" / "restored" / backup.DATABASE_NAME
    )

    names = {item["name"] for item in report["checks"]}
    assert {
        "backup_verify",
        "replay_verify",
        f"audit_replay.{seed.tenant_id}",
        f"event_index_replay.{seed.tenant_id}",
        f"workflow_jobs_replay.{seed.tenant_id}",
        f"workflow_job_events_replay.{seed.tenant_id}",
        f"workflow_outbox_replay.{seed.tenant_id}",
        f"ledger_replay.{seed.tenant_id}",
    } <= names
    assert all(item["ok"] for item in report["checks"])

    # The reopened database really holds the financial truth, not just counts.
    restored = BusinessStore(report["restored_path"])
    summary = ledger.summary(restored, seed.tenant_id)
    assert summary["credit_minor"] == 1250
    assert summary["debit_minor"] == 250
    assert summary["net_minor"] == 1000

    # The live database was never a restore target.
    assert report["restored_path"] != str(seed.store.path)


def test_tenant_scoped_restore_is_isolated(tmp_path):
    seed = seed_store(tmp_path / "live")
    other, _ = _add_second_tenant(seed.store)

    report = dr.run_restore_drill(
        seed.store,
        tmp_path / "drill",
        tenant_id=seed.tenant_id,
    )
    assert report["ok"] is True
    assert report["scope"] == {"tenant_id": seed.tenant_id}
    assert report["restore"]["scope"] == {"tenant_id": seed.tenant_id}
    assert report["restore"]["tenants"] == [seed.tenant_id]

    restored = BusinessStore(report["restored_path"])
    assert restored is not None
    with restored.read() as conn:
        tenants = [
            str(row[0])
            for row in conn.execute(
                "SELECT tenant_id FROM bos_tenant ORDER BY tenant_id"
            ).fetchall()
        ]
    assert tenants == [seed.tenant_id]
    assert other.tenant_id not in tenants


def test_restore_refuses_to_overwrite_the_live_database(tmp_path):
    seed = seed_store(tmp_path / "live")
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(seed.store, snapshot_dir)

    with pytest.raises(BusinessError) as exc:
        dr.restore_snapshot(
            snapshot_dir, seed.store.path, live_db=seed.store.path
        )
    assert exc.value.code == dr.DR_LIVE_DB_WRITE_REFUSED_CODE

    # Even without the explicit live path, an existing target is refused.
    with pytest.raises(BusinessError) as existing:
        dr.restore_snapshot(snapshot_dir, seed.store.path)
    assert existing.value.code == backup.BACKUP_TARGET_EXISTS_CODE


def test_rpo_and_rto_are_measured_without_an_sla_claim(tmp_path):
    seed = seed_store(tmp_path / "live")
    report = dr.run_restore_drill(seed.store, tmp_path / "drill")

    assert report["rpo"]["sla_claimed"] is False
    assert report["rpo"]["definition"]
    assert isinstance(report["rpo"]["rpo_seconds"], float)
    assert report["rpo"]["rpo_seconds"] >= 0.0
    assert report["rpo"]["newest_record_at"] is not None
    assert report["rpo"]["snapshot_started_at"] == report["snapshot"]["created_at"]

    assert report["rto"]["sla_claimed"] is False
    for key in (
        "snapshot_seconds",
        "verify_seconds",
        "restore_seconds",
        "replay_seconds",
        "rto_seconds",
    ):
        assert report["rto"][key] >= 0.0
    assert report["rto"]["rto_seconds"] == pytest.approx(
        report["rto"]["restore_seconds"] + report["rto"]["replay_seconds"]
    )


def test_drill_refuses_a_non_empty_workdir(tmp_path):
    seed = seed_store(tmp_path / "live")
    workdir = tmp_path / "drill"
    (workdir / "snapshot").mkdir(parents=True)
    (workdir / "snapshot" / "stale.txt").write_text("leftover", encoding="utf-8")

    with pytest.raises(BusinessError) as exc:
        dr.run_restore_drill(seed.store, workdir)
    assert exc.value.code == dr.DR_BAD_INPUT_CODE
