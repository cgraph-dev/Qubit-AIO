"""Active/dormant policy: view signals never own business truth.

The master plan's non-negotiable rules (§5/§18.4) are pinned here:

  * camera alone raises a room only to the proxy/presentation tier;
  * an urgent event, interaction, deadline, approval, notification or priority
    can activate a room that is NOT on camera;
  * applying an active set never erases pending events, approvals or schedules;
  * rehydration rebuilds visuals from the authoritative room;
  * illegal lifecycle edges are refused;
  * the scheduler is deterministic and fires in ``(at, event_id)`` order.

Hermetic: pure functions, injected clock, no store, no network.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from voicebridge.office.world import active as A
from voicebridge.office.world import model as M


def _room(room_id: str, *, desks: int = 9,
          priority: int = 0) -> M.LogicalRoom:
    room = M.build_room(
        room_id, room_id.upper(), "wing-1", desks,
        business_id="biz-1", legacy_floor_id=0, priority=priority,
    )
    # Give every room operational state that dormancy must not erase.
    return replace(
        room,
        pending_events=({"kind": "approval_request", "id": f"{room_id}-ev"},),
        approvals=({"id": f"{room_id}-approval", "state": "pending"},),
        schedules=({"id": f"{room_id}-timer", "at": 120.0},),
    )


def _campus(*rooms: M.LogicalRoom) -> M.Campus:
    wing = M.Wing("wing-1", "Wing 1", 0, 0, tuple(rooms), z=0)
    business = M.Business(
        "biz-1", "Business 1", "wing-1",
        room_ids=tuple(r.room_id for r in rooms),
        headcount=sum(r.capacity for r in rooms),
    )
    return M.assemble_campus("campus-1", "Campus 1", (wing,), (business,))


def _decision(campus: M.Campus, signals: A.ActiveSignals,
              room_id: str = "room-a") -> A.ActiveDecision:
    return A.select_active_set(campus, signals).by_room()[room_id]


# --- signal -> tier/state ---------------------------------------------------

def test_camera_alone_is_presentation_proxy_not_simulation():
    campus = _campus(_room("room-a"))
    decision = _decision(campus, A.ActiveSignals(
        camera_rooms=frozenset({"room-a"})))
    assert decision.tier is A.Tier.PROXY
    assert decision.state is M.RoomState.PRESENTATION_ACTIVE
    assert decision.reasons == ("camera",)


@pytest.mark.parametrize(
    "signals",
    [
        A.ActiveSignals(urgent_rooms=frozenset({"room-a"})),
        A.ActiveSignals(interaction_room="room-a"),
        A.ActiveSignals(deadline_rooms=frozenset({"room-a"})),
        A.ActiveSignals(approval_rooms=frozenset({"room-a"})),
        A.ActiveSignals(notification_rooms=frozenset({"room-a"})),
        A.ActiveSignals(priorities={"room-a": 9}),
    ],
)
def test_non_camera_signals_activate_an_off_camera_room(signals):
    campus = _campus(_room("room-a"))
    decision = _decision(campus, signals)
    assert decision.tier > A.Tier.DORMANT
    assert decision.state is M.RoomState.SIMULATION_ACTIVE


def test_camera_plus_approval_keeps_simulation_state():
    campus = _campus(_room("room-a"))
    decision = _decision(campus, A.ActiveSignals(
        camera_rooms=frozenset({"room-a"}),
        approval_rooms=frozenset({"room-a"}),
    ))
    assert decision.tier is A.Tier.PROXY   # numerically higher than low-rate
    assert decision.state is M.RoomState.SIMULATION_ACTIVE
    assert "approval" in decision.reasons


def test_fairness_aging_lifts_a_starved_dormant_room_to_aggregate():
    campus = _campus(_room("room-a"))
    decision = _decision(campus, A.ActiveSignals(
        fairness={"room-a": 3600.0}))
    assert decision.tier is A.Tier.AGGREGATE
    assert decision.state is M.RoomState.OBSERVED


def test_no_signals_is_dormant():
    campus = _campus(_room("room-a"))
    decision = _decision(campus, A.ActiveSignals())
    assert decision.tier is A.Tier.DORMANT
    assert decision.state is M.RoomState.DORMANT
    assert decision.reasons == ()


def test_active_set_is_sorted_and_deterministic():
    campus = _campus(_room("room-b"), _room("room-a"))
    signals = A.ActiveSignals(camera_rooms=frozenset({"room-a", "room-b"}),
                              now=7.0)
    first = A.select_active_set(campus, signals).to_dict()
    second = A.select_active_set(campus, signals).to_dict()
    assert first == second
    assert exactly_sorted(first["decisions"], "room_id")


def exactly_sorted(rows, key):
    values = [row[key] for row in rows]
    return values == sorted(values)


# --- dormancy preserves operational state ----------------------------------

def test_applying_dormancy_never_erases_events_approvals_or_schedules():
    source = _room("room-a")
    campus = _campus(source)
    dorm = A.select_active_set(campus, A.ActiveSignals())
    applied = A.apply_active_set(campus, dorm)
    room = applied.room("room-a")
    assert room.state is M.RoomState.DORMANT
    assert room.pending_events == source.pending_events
    assert room.approvals == source.approvals
    assert room.schedules == source.schedules
    # Geometry, seats and the business record are untouched.
    assert room.capacity == source.capacity
    assert applied.business("biz-1").room_ids == ("room-a",)


def test_rehydration_rebuilds_visuals_from_authoritative_state():
    campus = _campus(_room("room-a"))
    dormant = A.apply_active_set(
        campus, A.select_active_set(campus, A.ActiveSignals()))
    rehydrated, snapshot = A.rehydrate_room(dormant.room("room-a"))
    assert rehydrated.state is M.RoomState.PRESENTATION_ACTIVE
    assert rehydrated.tier is A.Tier.PROXY
    assert snapshot.capacity == rehydrated.capacity == 9
    assert snapshot.headcount == rehydrated.headcount == 0
    assert snapshot.pending_events == 1
    assert snapshot.approvals == 1
    assert snapshot.schedules == 1
    assert [s["seat_id"] for s in snapshot.seats] == [
        s.seat_id for s in rehydrated.seats]


# --- lifecycle edges -------------------------------------------------------

def test_drive_room_walks_legal_edges_only():
    room = _room("room-a")
    loaded = A.drive_room(room, M.RoomState.OBSERVED)
    assert loaded.state is M.RoomState.OBSERVED
    dormant = A.drive_room(loaded, M.RoomState.DORMANT)
    assert dormant.state is M.RoomState.DORMANT
    back = A.drive_room(dormant, M.RoomState.SIMULATION_ACTIVE)
    assert back.state is M.RoomState.SIMULATION_ACTIVE
    assert M.can_transition(M.RoomState.DORMANT, M.RoomState.REHYDRATING)


def test_drive_room_refuses_an_undeclared_edge():
    # OBSERVED -> AUTHORED is not a declared edge: the lifecycle never rewinds
    # to "authored" once a room has been loaded.
    room = A.drive_room(_room("room-a"), M.RoomState.OBSERVED)
    with pytest.raises(ValueError):
        A.drive_room(room, M.RoomState.AUTHORED)


def test_apply_active_set_moves_from_authored_to_the_decided_state():
    campus = _campus(_room("room-a"))
    assert campus.room("room-a").state is M.RoomState.AUTHORED
    applied = A.apply_active_set(campus, A.select_active_set(
        campus, A.ActiveSignals(urgent_rooms=frozenset({"room-a"}))))
    assert applied.room("room-a").state is M.RoomState.SIMULATION_ACTIVE
    assert applied.room("room-a").tier is A.Tier.FULL


# --- scheduler --------------------------------------------------------------

def test_scheduler_fires_in_deterministic_order_and_rejects_duplicates():
    scheduler = A.DeterministicScheduler(start=10.0)
    scheduler.schedule("timer-b", 15.0, {"room": "room-b"})
    scheduler.schedule("timer-a", 15.0, {"room": "room-a"})
    scheduler.schedule("timer-c", 12.0)
    assert scheduler.now() == 10.0
    assert [e.event_id for e in scheduler.pending()] == [
        "timer-c", "timer-a", "timer-b"]
    with pytest.raises(ValueError):
        scheduler.schedule("timer-a", 20.0)
    with pytest.raises(ValueError):
        scheduler.schedule("timer-past", 5.0)

    fired = scheduler.advance(5.0)
    assert [e.event_id for e in fired] == ["timer-c", "timer-a", "timer-b"]
    assert scheduler.due() == ()
    assert scheduler.to_dict() == {"now": 15.0, "pending": []}


def test_scheduler_does_not_fire_early():
    scheduler = A.DeterministicScheduler()
    scheduler.schedule("later", 30.0)
    assert scheduler.advance(29.9) == ()
    assert scheduler.now() == 29.9
    assert scheduler.advance(0.1)[0].event_id == "later"
