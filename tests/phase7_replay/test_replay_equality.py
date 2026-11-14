"""Deterministic equality: the same history and seed replay byte-identically."""
from __future__ import annotations

from voicebridge.business.simulation_audit import (
    PROJECTION_SCHEMA,
    SCHEMA,
    project,
    record_projection,
    replay,
)

from conftest import CLOCK, SEED, TENANT


def test_recorded_projection_is_deterministic(history):
    first = record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    second = record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert first["schema"] == PROJECTION_SCHEMA
    assert first["state"] == "ok"
    assert first["frame_count"] == 5
    assert first["event_count"] == 6
    assert first["final_digest"] == second["final_digest"]
    assert [frame["digest"] for frame in first["frames"]] == [
        frame["digest"] for frame in second["frames"]
    ]
    assert first["fx"]["seeds"] == [1001, 2002]


def test_replay_matches_the_recorded_projection(history):
    recorded = record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    first = replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    second = replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert first["schema"] == SCHEMA
    assert first["status"] == "match"
    assert first["incidents"] == []
    assert first["incident_count"] == 0
    assert first["frames_checked"] == 5
    assert first["replayed"]["digest"] == recorded["final_digest"]
    assert first["replayed"]["frame_count"] == recorded["frame_count"]
    assert first["status"] == second["status"]
    assert first["replayed"] == second["replayed"]
    assert first["run_id"] == second["run_id"]


def test_state_effects_and_fx_are_rebuilt(history):
    projection = project(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    final = projection["final_state"]
    ada = final["agents"]["agent-ada"]
    assert ada["needs"]["energy"] == 55
    assert ada["needs"]["stress"] == 48
    assert ada["needs"]["social"] == 53
    assert ada["relationships"]["agent-grace"] == {
        "trust": 2,
        "friendship": 1,
    }
    assert "agent-grace" not in final["agents"]
    assert final["counters"]["started"] == 1
    assert final["counters"]["completed"] == 1
    assert final["counters"]["effects"] == 1
    assert final["counters"]["fx"] == 2
    assert final["counters"]["notes"] == 1
    assert final["counters"]["ignored"] == 1
    assert projection["effects"]["applied_events"] == 1
    assert projection["effects"]["totals"]["energy"] == 5
    assert projection["effects"]["totals"]["stress"] == -2
    assert projection["effects"]["business_truth"] is False
    assert projection["effects"]["mode"] == "stubbed"
    assert projection["fx"]["mode"] == "stubbed"
    assert [entry["animation"] for entry in projection["fx"]["timeline"]] == [
        "walk_to_kitchen",
        "sip",
    ]
    assert projection["fx"]["real_calls"] is False
    assert projection["external_side_effects"] is False
    assert projection["fabricated_work"] is False


def test_replay_does_not_mutate_the_history_or_recorded(history):
    recorded = record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    before_history = [dict(event) for event in history]
    before_recorded = dict(recorded)
    replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert history == before_history
    assert recorded == before_recorded
