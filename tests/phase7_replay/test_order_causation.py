"""Causation ordering and correlation preservation."""
from __future__ import annotations

from voicebridge.business.simulation_audit import (
    causal_order,
    project,
    record_projection,
    replay,
)

from conftest import CLOCK, SEED, TENANT, make_event


def test_a_cause_sorts_before_its_effect_even_with_a_later_clock():
    cause = make_event(
        "cause", "office.interaction.started", 1,
        {"interaction_id": "i1", "agent_id": "a1"},
        at="2026-01-01T00:00:10.000Z",
    )
    effect = make_event(
        "effect", "office.interaction.effect", 1,
        {"interaction_id": "i1", "effects": {"focus": 1}},
        at="2026-01-01T00:00:01.000Z",
        causation_id="cause",
    )
    ordered = causal_order([effect, cause])
    assert [event["event_id"] for event in ordered] == ["cause", "effect"]


def test_correlation_is_preserved_on_every_frame():
    events = [
        make_event(
            "e1", "office.interaction.started", 1,
            {"interaction_id": "i1", "agent_id": "a1"},
            correlation_id="corr-42",
        ),
        make_event(
            "e2", "office.interaction.completed", 2,
            {"interaction_id": "i1"},
            correlation_id="corr-42", causation_id="e1",
            at="2026-01-01T00:00:02.000Z",
        ),
    ]
    projection = project(events, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert [frame["correlation_id"] for frame in projection["frames"]] == [
        "corr-42", "corr-42"]
    assert projection["frames"][1]["causation_id"] == "e1"


def test_a_cycle_falls_back_to_clock_order_and_drops_nothing():
    first = make_event(
        "a", "office.interaction.started", 1,
        {"interaction_id": "i1", "agent_id": "a1"},
        at="2026-01-01T00:00:01.000Z", causation_id="b",
    )
    second = make_event(
        "b", "office.interaction.note", 2,
        {"interaction_id": "i1"},
        at="2026-01-01T00:00:02.000Z", causation_id="a",
    )
    ordered = causal_order([second, first])
    assert [event["event_id"] for event in ordered] == ["a", "b"]


def test_non_interaction_events_are_ignored_but_kept_for_order_audit(history):
    projection = project(
        history, tenant_id=TENANT, seed=SEED, clock=CLOCK,
        include_all_events=True)
    assert projection["frame_count"] == 6
    task_frame = projection["frames"][-1]
    assert task_frame["type"] == "task.created"
    assert task_frame["applied"] is False
    assert projection["final_state"]["counters"]["ignored"] == 1


def test_recording_and_replay_agree_under_all_events(history):
    recorded = record_projection(
        history, tenant_id=TENANT, seed=SEED, clock=CLOCK,
        include_all_events=True)
    result = replay(
        history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK,
        include_all_events=True)
    assert result["status"] == "match"
    assert result["frames_checked"] == 6
