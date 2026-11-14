"""P8-03 kill switch: every surface, failed stopper recorded, idempotent.

Hermetic: throwaway SQLite files under ``tmp_path``; injected stoppers are
plain Python callables. No network, no live service.
"""
from __future__ import annotations

import pytest

from voicebridge.business import runs
from voicebridge.business.errors import BusinessError
from voicebridge.business.safety import KILL_SWITCH_SURFACES
from voicebridge.business import disaster_recovery as dr

from _dr_helpers import seed_store


def test_build_stoppers_refuses_a_missing_injected_surface(tmp_path):
    seed = seed_store(tmp_path / "live")
    with pytest.raises(BusinessError) as exc:
        dr.build_stoppers(
            seed.store,
            seed.tenant_id,
            scheduler=lambda: "stopped",
            # gateway missing
            egress=lambda: "blocked",
        )
    assert exc.value.code == dr.DR_BAD_INPUT_CODE
    assert "gateway" in str(exc.value)


def test_kill_switch_stops_every_surface_once_and_is_idempotent(tmp_path):
    seed = seed_store(tmp_path / "live")
    calls: list[str] = []

    def make(name):
        def stop():
            calls.append(name)
            return f"{name}:stopped"

        return stop

    stoppers = dr.build_stoppers(
        seed.store,
        seed.tenant_id,
        scheduler=make("scheduler"),
        gateway=make("gateway"),
        egress=make("egress"),
    )
    assert set(stoppers) == set(KILL_SWITCH_SURFACES)

    controller = dr.KillSwitchController(seed.store, seed.tenant_id, stoppers)
    report = controller.engage(reason="drill", idempotency_key="ks-1")

    assert report["status"] == "STOPPED"
    assert report["engaged"] is True
    assert set(report["surfaces_stopped"]) == set(KILL_SWITCH_SURFACES)
    assert report["surfaces_failed"] == []
    assert report["audit_id"] is not None
    assert sorted(calls) == ["egress", "gateway", "scheduler"]

    # A fresh controller on the same tenant must not re-run the stoppers:
    # the durable ``safety.kill_switch`` audit row is the record.
    second_controller = dr.KillSwitchController(
        seed.store,
        seed.tenant_id,
        dr.build_stoppers(
            seed.store,
            seed.tenant_id,
            scheduler=make("scheduler-again"),
            gateway=make("gateway-again"),
            egress=make("egress-again"),
        ),
    )
    again = second_controller.engage(reason="again", idempotency_key="ks-2")
    assert again["already_engaged"] is True
    assert again["status"] == "STOPPED"
    assert set(again["surfaces_stopped"]) == set(KILL_SWITCH_SURFACES)
    assert sorted(calls) == ["egress", "gateway", "scheduler"]


def test_a_failing_stopper_is_recorded_and_the_rest_still_run(tmp_path):
    seed = seed_store(tmp_path / "live")
    calls: list[str] = []

    def scheduler():
        calls.append("scheduler")
        return "scheduler:stopped"

    def gateway():
        calls.append("gateway")
        raise RuntimeError("gateway socket refused")

    def egress():
        calls.append("egress")
        return "egress:blocked"

    controller = dr.KillSwitchController(
        seed.store,
        seed.tenant_id,
        dr.build_stoppers(
            seed.store,
            seed.tenant_id,
            scheduler=scheduler,
            gateway=gateway,
            egress=egress,
        ),
    )
    report = controller.engage(reason="drill", idempotency_key="ks-partial")

    assert report["status"] == "PARTIAL"
    assert report["engaged"] is True
    assert report["surfaces_failed"] == ["gateway"]
    assert "RuntimeError" in next(
        item["error"] for item in report["surfaces"] if item["surface"] == "gateway"
    )
    # The failing stopper did not skip the surfaces after it.
    assert sorted(calls) == ["egress", "gateway", "scheduler"]
    assert "leases" in report["surfaces_stopped"]


def test_lease_stopper_releases_real_business_leases(tmp_path):
    seed = seed_store(tmp_path / "live")
    lease = runs.acquire_lease(
        seed.store,
        seed.tenant_id,
        "room",
        "room-1",
        holder="agent:ops",
    )
    assert lease.released_at is None

    controller = dr.KillSwitchController(
        seed.store,
        seed.tenant_id,
        dr.build_stoppers(
            seed.store,
            seed.tenant_id,
            scheduler=lambda: "scheduler:stopped",
            gateway=lambda: "gateway:stopped",
            egress=lambda: "egress:blocked",
        ),
    )
    report = controller.engage(reason="drill", idempotency_key="ks-lease")
    assert report["status"] == "STOPPED"
    assert runs.get_lease(seed.store, seed.tenant_id, lease.lease_id).released_at
