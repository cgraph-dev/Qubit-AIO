"""P8-03 audit-backed incident records.

Hermetic: throwaway SQLite files under ``tmp_path``; no network, no service.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore, backup
from voicebridge.business.errors import BusinessError
from voicebridge.business import disaster_recovery as dr

from _dr_helpers import seed_store


def test_incident_open_is_idempotent_and_audited(tmp_path):
    seed = seed_store(tmp_path / "live")

    first = dr.record_incident(
        seed.store,
        seed.tenant_id,
        kind="provider_outage",
        severity="high",
        summary="inference provider returned 503",
        actor="operator",
        idempotency_key="inc-1",
    )
    assert first["schema"] == dr.INCIDENT_SCHEMA
    assert first["status"] == "open"
    assert first["replayed"] is False
    assert first["incident_id"].startswith("inc-")
    assert first["open_audit_id"]

    again = dr.record_incident(
        seed.store,
        seed.tenant_id,
        kind="provider_outage",
        severity="high",
        summary="inference provider returned 503",
        actor="operator",
        idempotency_key="inc-1",
    )
    assert again["replayed"] is True
    assert again["incident_id"] == first["incident_id"]
    assert again["open_audit_id"] == first["open_audit_id"]

    with seed.store.read() as conn:
        opens = conn.execute(
            "SELECT COUNT(*) FROM bos_audit_event WHERE tenant_id=?"
            " AND action='dr.incident.open'",
            (seed.tenant_id,),
        ).fetchone()[0]
    assert opens == 1


def test_incident_resolution_links_the_kill_switch_and_replays(tmp_path):
    seed = seed_store(tmp_path / "live")
    incident = dr.record_incident(
        seed.store,
        seed.tenant_id,
        kind="rogue_agent",
        severity="critical",
        summary="agent exceeded its budget",
        actor="operator",
        idempotency_key="inc-critical",
    )
    kill_switch = {"status": "STOPPED", "engaged": True}

    resolved = dr.resolve_incident(
        seed.store,
        seed.tenant_id,
        incident["incident_id"],
        summary="agent quarantined; leases released",
        actor="operator",
        kill_switch=kill_switch,
    )
    assert resolved["status"] == "resolved"
    assert resolved["replayed"] is False
    assert resolved["kill_switch_status"] == "STOPPED"
    assert resolved["resolve_audit_id"]

    again = dr.resolve_incident(
        seed.store,
        seed.tenant_id,
        incident["incident_id"],
        summary="duplicate",
        actor="operator",
    )
    assert again["replayed"] is True
    assert again["resolution"] == "agent quarantined; leases released"


def test_list_incidents_and_missing_incident_refusal(tmp_path):
    seed = seed_store(tmp_path / "live")
    dr.record_incident(
        seed.store,
        seed.tenant_id,
        kind="backup_failure",
        severity="medium",
        summary="snapshot refused",
        actor="operator",
        idempotency_key="inc-a",
    )
    dr.record_incident(
        seed.store,
        seed.tenant_id,
        kind="provider_outage",
        severity="low",
        summary="slow provider",
        actor="operator",
        idempotency_key="inc-b",
    )

    incidents = dr.list_incidents(seed.store, seed.tenant_id)
    assert len(incidents) == 2
    assert {item["kind"] for item in incidents} == {
        "backup_failure",
        "provider_outage",
    }

    with pytest.raises(BusinessError) as exc:
        dr.resolve_incident(
            seed.store,
            seed.tenant_id,
            "inc-does-not-exist",
            summary="nothing",
            actor="operator",
        )
    assert exc.value.code == dr.DR_INCIDENT_MISSING_CODE


def test_invalid_severity_is_a_typed_refusal(tmp_path):
    seed = seed_store(tmp_path / "live")
    with pytest.raises(BusinessError) as exc:
        dr.record_incident(
            seed.store,
            seed.tenant_id,
            kind="other",
            severity="catastrophic",
            summary="bad input",
            actor="operator",
            idempotency_key="inc-bad",
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"


def test_incident_survives_snapshot_and_restore(tmp_path):
    seed = seed_store(tmp_path / "live")
    incident = dr.record_incident(
        seed.store,
        seed.tenant_id,
        kind="provider_outage",
        severity="high",
        summary="retained incident",
        actor="operator",
        idempotency_key="inc-retained",
    )
    snapshot_dir = tmp_path / "snapshot"
    backup.snapshot(seed.store, snapshot_dir)
    target = tmp_path / "restored" / "business.db"
    dr.restore_snapshot(snapshot_dir, target, live_db=seed.store.path)

    restored = BusinessStore(target)
    restored_incidents = dr.list_incidents(restored, seed.tenant_id)
    assert [item["incident_id"] for item in restored_incidents] == [
        incident["incident_id"]
    ]
    assert restored_incidents[0]["open_audit_id"] == incident["open_audit_id"]
