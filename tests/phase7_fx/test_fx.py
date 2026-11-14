"""P7-04 — event-to-FX projection (hermetic: no clock, store or network)."""
from __future__ import annotations

from pathlib import Path

import pytest

from voicebridge.office import fx as FX

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def ev(event_id: str, event_type: str, **extra):
    payload = dict(extra.pop("payload", {}))
    event = {"event_id": event_id, "type": event_type, "payload": payload}
    event.update(extra)
    return event


def test_every_rule_maps_deterministically_with_stable_seed():
    for event_type in FX.FX_RULE_TYPES:
        event = ev("event-1", event_type, agent="agent-a", room="room-a")
        first = FX.map_event(event)
        second = FX.map_event(event)
        assert first is not None, event_type
        assert first == second
        assert first.event_type == event_type
        assert first.fx_event_id.startswith("fx_")
        assert 0 <= first.seed < FX.SEED_LIMIT
        assert first.fallback
        FX.validate_fx_event(first)


def test_unknown_empty_and_malformed_events_produce_no_fx():
    assert FX.map_event({}) is None
    assert FX.map_event({"event_id": "", "type": "agent.turn.completed"}) is None
    assert FX.map_event({"event_id": "e1", "type": ""}) is None
    assert FX.map_event({"event_id": "e1", "type": "not.a.rule"}) is None
    assert FX.map_event(None) is None
    assert FX.map_event("event-id") is None
    assert FX.map_event({"event_id": "e1", "type": "not.a.rule",
                         "payload": {"completed": True}}) is None
    assert FX.map_events([]) == []


def test_room_and_agent_are_read_only_from_the_event():
    event = ev("e-room", "agent.turn.started",
               payload={"room_id": "room-7", "agent_id": "agent-7"})
    mapped = FX.map_event(event)
    assert mapped is not None
    assert mapped.room == "room-7"
    assert mapped.agent == "agent-7"
    assert FX.subject_key(mapped) == ("agent", "agent-7")


def test_seed_and_id_are_stable_per_subject_and_effect():
    a = FX.map_event(ev("e1", "agent.turn.started", agent="a", room="r"))
    b = FX.map_event(ev("e2", "agent.turn.started", agent="a", room="r"))
    assert a is not None and b is not None
    assert a.seed != b.seed
    assert a.fx_event_id != b.fx_event_id
    assert a.fx_event_id == FX.fx_event_id_for("e1", "agent.turn.started",
                                               a.animation, "a", "r")
    assert a.seed == FX.seed_for("e1", a.animation, "a", "r")


def test_queue_orders_by_priority_then_insertion():
    queue = FX.FXQueue(capacity=8)
    low = FX.map_event(ev("e-low", "task.created", agent="a"))
    normal = FX.map_event(ev("e-normal", "agent.turn.started", agent="a"))
    critical = FX.map_event(ev("e-critical", "agent.turn.failed", agent="b"))
    assert low and normal and critical
    assert queue.submit(low) == "accepted"
    assert queue.submit(normal) == "accepted"
    assert queue.submit(critical) == "accepted"
    first = queue.pop()
    assert first is not None and first.event_id == "e-critical"
    rest = queue.drain()
    assert [item.event_id for item in rest] == ["e-normal", "e-low"]


def test_dedupe_never_enqueues_the_same_effect_twice():
    queue = FX.FXQueue()
    mapped = FX.map_event(ev("e1", "agent.turn.started", agent="a"))
    assert mapped
    assert queue.submit(mapped) == "accepted"
    assert queue.submit(mapped) == "duplicate"
    assert len(queue) == 1


def test_replace_cancels_only_the_same_subject():
    queue = FX.FXQueue()
    work_a = FX.map_event(ev("e-work-a", "agent.turn.started", agent="a"))
    work_b = FX.map_event(ev("e-work-b", "agent.turn.started", agent="b"))
    done_a = FX.map_event(ev("e-done-a", "agent.turn.completed", agent="a"))
    assert work_a and work_b and done_a
    assert done_a.cancellation == "replace"
    queue.submit(work_a)
    queue.submit(work_b)
    assert queue.submit(done_a) == "replaced"
    assert len(queue) == 2
    assert work_a.fx_event_id not in queue
    assert work_b.fx_event_id in queue
    assert done_a.fx_event_id in queue


def test_cancel_policy_removes_prior_and_emits_the_stop_cue():
    queue = FX.FXQueue()
    work = FX.map_event(ev("e-work", "agent.turn.started", agent="a"))
    stop = FX.map_event(ev("e-stop", "agent.stopped", agent="a"))
    assert work and stop
    assert stop.cancellation == "cancel"
    queue.submit(work)
    assert queue.submit(stop) == "replaced"
    assert queue.peek() is not None
    assert queue.peek().event_id == "e-stop"
    assert work.fx_event_id not in queue


def test_cancel_and_cancel_subject_are_explicit():
    queue = FX.FXQueue()
    a = FX.map_event(ev("e-a", "agent.turn.started", agent="a"))
    b = FX.map_event(ev("e-b", "task.created", agent="b"))
    assert a and b
    queue.submit(a)
    queue.submit(b)
    assert queue.cancel(a.fx_event_id) is True
    assert queue.cancel("missing") is False
    assert queue.cancel_subject(agent="b") == 1
    assert len(queue) == 0


def test_queue_is_bounded_and_drops_the_least_important():
    queue = FX.FXQueue(capacity=2)
    critical = FX.map_event(ev("e-crit", "agent.turn.failed", agent="a"))
    high = FX.map_event(ev("e-high", "approval.requested", agent="b"))
    low = FX.map_event(ev("e-low", "task.created", agent="c"))
    assert critical and high and low
    assert queue.submit(low) == "accepted"
    assert queue.submit(high) == "accepted"
    assert queue.submit(critical) == "accepted"
    ids = {item.event_id for item in queue.drain()}
    assert ids == {"e-crit", "e-high"}
    with pytest.raises(FX.FXError) as exc:
        FX.FXQueue(capacity=0)
    assert exc.value.code == "FX_BAD_CAPACITY"


def test_reduced_motion_drops_nonessential_and_keeps_a_still_critical_cue():
    ambient = ev("e-ambient", "task.created", agent="a")
    assert FX.map_event(ambient, reduced_motion=True) is None
    done = FX.map_event(ev("e-done", "agent.turn.completed", agent="a"),
                        reduced_motion=True)
    assert done is not None
    assert done.reduced_motion is True
    assert done.essential is True
    assert done.animation == FX.EVENT_RULES["agent.turn.completed"].still
    assert done.particles is None
    assert done.still
    # The critical status channel survives: the still cue is not empty.
    assert done.animation != FX.EVENT_RULES["agent.turn.completed"].animation


def test_disable_nonessential_keeps_only_critical_cues():
    events = [
        ev("e1", "agent.turn.started", agent="a"),
        ev("e2", "approval.requested", agent="a"),
        ev("e3", "artifact.created", agent="a"),
        ev("e4", "agent.revoked", agent="a"),
    ]
    mapped = FX.map_events(events, disable_nonessential=True)
    assert [item.event_type for item in mapped] == [
        "approval.requested",
        "agent.revoked",
    ]
    assert all(item.essential for item in mapped)


def test_replay_is_deterministic_and_bounded_by_cancellation():
    events = [
        ev("e1", "agent.turn.started", agent="a"),
        ev("e2", "task.created", agent="a"),
        ev("e3", "agent.turn.completed", agent="a"),
        ev("e4", "approval.requested", agent="b"),
    ]
    first = FX.replay(events)
    second = FX.replay(events)
    assert first == second
    assert all(item.replay for item in first)
    assert [item.event_id for item in first] == ["e3", "e4"]
    assert first[0].seed == FX.map_event(events[2]).seed


def test_replay_of_empty_input_is_empty():
    assert FX.replay([]) == []
    assert FX.replay([ev("e", "not.a.rule")]) == []


def test_build_and_validate_payload_carries_no_business_truth():
    queue = FX.FXQueue()
    queue.submit(FX.map_event(ev("e1", "agent.turn.completed", agent="a")))
    queue.submit(FX.map_event(ev("e2", "approval.requested", agent="b")))
    payload = FX.build_fx_payload(queue.drain())
    assert payload["schema"] == FX.SCHEMA
    assert payload["mode"] == FX.MODE
    assert payload["business_truth"] is False
    assert FX.validate_fx_payload(payload) == []
    for forbidden in FX.FORBIDDEN_TRUTH_KEYS:
        assert forbidden not in payload
    assert all(forbidden not in event for event in payload["events"]
               for forbidden in FX.FORBIDDEN_TRUTH_KEYS)


def test_payload_validator_reports_tampering():
    queue = FX.FXQueue()
    queue.submit(FX.map_event(ev("e1", "agent.turn.completed", agent="a")))
    payload = FX.build_fx_payload(queue.drain())
    payload["business_truth"] = True
    payload["events"][0]["seed"] = -1
    problems = FX.validate_fx_payload(payload)
    assert any("business_truth" in problem for problem in problems)
    assert any("FX_BAD_SEED" in problem for problem in problems)
    assert FX.validate_fx_payload(None) == ["payload must be an object"]


def test_fx_module_has_no_clock_store_or_network_imports():
    source = (ROOT / "voicebridge" / "office" / "fx.py").read_text("utf-8")
    for token in ("import time", "import sqlite3", "import random",
                  "import socket", "import urllib", "import requests",
                  "import httpx"):
        assert token not in source
