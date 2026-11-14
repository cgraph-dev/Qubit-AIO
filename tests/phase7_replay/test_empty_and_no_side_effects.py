"""Empty histories, stubbed effects and no external side effects or work."""
from __future__ import annotations

import socket

from voicebridge.business import simulation_audit as SA

from conftest import CLOCK, SEED, TENANT, with_claim


def test_empty_history_is_empty_not_an_error():
    recorded = SA.record_projection([], tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert recorded["state"] == "empty"
    assert recorded["frame_count"] == 0
    assert recorded["event_count"] == 0
    result = SA.replay([], recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "empty"
    assert result["incidents"] == []


def test_replay_never_reads_the_store_or_calls_a_model(monkeypatch, history):
    recorded = SA.record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)

    def boom(*args, **kwargs):
        raise AssertionError("replay must not touch the business store")

    monkeypatch.setattr(SA.EVENTLOG, "append_event", boom)
    monkeypatch.setattr(SA.EVENTLOG, "list_events", boom)
    monkeypatch.setattr(SA.EVENTLOG, "read_events", boom)
    result = SA.replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "match"
    assert result["external_side_effects"] is False
    assert result["fabricated_work"] is False


def test_replay_opens_no_socket(monkeypatch, history):
    def no_socket(*args, **kwargs):
        raise AssertionError("replay must not open a socket")

    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    recorded = SA.record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    result = SA.replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "match"


def test_business_completion_claims_are_ignored_not_projected():
    events = with_claim()
    projection = SA.project(events, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    final = projection["final_state"]
    assert final["counters"]["claimed_business_truth_ignored"] == 1
    assert final["counters"]["completed"] == 1
    assert "artifacts" not in final
    assert "receipts" not in final
    assert projection["fabricated_work"] is False
    assert projection["effects"]["business_truth"] is False
    assert any(
        "business evidence" in warning for warning in projection["warnings"])


def test_effects_are_reported_as_stubbed():
    events = with_claim()
    projection = SA.project(events, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    for effect in projection["effects"]["sample"]:
        assert effect["stubbed"] is True
        assert effect["business_truth"] is False
    for fx in projection["fx"]["timeline"]:
        assert fx["stubbed"] is True
    assert projection["fx"]["real_calls"] is False
