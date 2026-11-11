"""Explicit occupancy projection for the 100-floor world tower.

Hermetic: pure data, no store, no clock, no network, no model. These tests pin
the master-plan rule that a BOS agent reaches a tower desk only through an
explicit link, that an ambiguous department is never guessed, and that an empty
tower stays empty.
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any

import pytest

from voicebridge.business.roles import RoleRegistry
from voicebridge.office.world import occupancy as OCC
from voicebridge.office.world.model import plan_cell_cluster

pytestmark = pytest.mark.unit


# --------------------------------------------------------------- fixtures
@dataclass(frozen=True)
class Room:
    room_id: str
    name: str
    kind: str
    desks: int


@dataclass(frozen=True)
class Floor:
    floor_id: str
    index: int
    name: str
    wing: str
    active_by_default: bool
    department_ids: tuple[str, ...]
    rooms: tuple[Room, ...]


def floor(index: int, department: str, rooms: tuple[Room, ...],
          *, active: bool = True) -> Floor:
    return Floor(
        floor_id=f"floor-{index:03d}",
        index=index,
        name=f"North Wing · Floor {index:03d}",
        wing="north",
        active_by_default=active,
        department_ids=(department,),
        rooms=rooms,
    )


def desk_ids(room: Room) -> list[str]:
    return [desk.desk_id
            for cell in plan_cell_cluster(room.room_id, room.desks)
            for desk in cell.desks]


def agent(agent_id: str, *, name: str | None = None, status: str = "active",
          department: str = "", metadata: dict[str, Any] | None = None,
          desk: str = "") -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "display_name": name or agent_id.replace("-", " ").title(),
        "status": status,
        "department": department,
        "desk": desk,
        "metadata": metadata or {},
    }


MAIN = Room("floor-001-main", "Main Floor", "work", 6)
FOCUS = Room("floor-001-focus", "Focus Rooms", "focus", 3)
FINANCE = floor(1, "finance", (MAIN, FOCUS))
REVENUE = floor(2, "revenue", (Room("floor-002-main", "Main Floor", "work", 6),))


# ------------------------------------------------------- explicit placement
def test_explicit_world_desk_places_exactly_that_desk():
    wanted = desk_ids(MAIN)[4]
    projection = OCC.project_occupancy(
        floors=[FINANCE, REVENUE],
        agents=[agent("agt-ada", metadata={"world_desk": wanted})],
    )
    assert projection.counts()["placed"] == 1
    [placement] = projection.placements
    assert placement.desk.desk_id == wanted
    assert placement.source == OCC.SOURCE_WORLD_DESK
    assert placement.agent_id == "agt-ada"
    assert placement.initials == "AA"
    assert projection.by_desk()[wanted]["name"] == "Agt Ada"


def test_explicit_world_room_picks_the_first_free_desk():
    projection = OCC.project_occupancy(
        floors=[FINANCE, REVENUE],
        agents=[agent("agt-ada", metadata={
            "world_floor": "floor-001",
            "world_room": "floor-001-main",
        })],
    )
    [placement] = projection.placements
    assert placement.desk.desk_id == desk_ids(MAIN)[0]
    assert placement.source == OCC.SOURCE_WORLD_ROOM


def test_world_room_name_is_ambiguous_without_a_floor():
    projection = OCC.project_occupancy(
        floors=[FINANCE, REVENUE],
        agents=[agent("agt-ada", metadata={"world_room": "Main Floor"})],
    )
    assert projection.placements == ()
    [unplaced] = projection.unplaced
    assert unplaced.reason == OCC.REASON_AMBIGUOUS_ROOM


def test_world_floor_alone_places_only_when_the_floor_has_one_room():
    projection = OCC.project_occupancy(
        floors=[FINANCE, REVENUE],
        agents=[agent("agt-ada", metadata={"world_floor": "floor-002"})],
    )
    [placement] = projection.placements
    assert placement.desk.floor_id == "floor-002"

    ambiguous = OCC.project_occupancy(
        floors=[FINANCE, REVENUE],
        agents=[agent("agt-ada", metadata={"world_floor": "floor-001"})],
    )
    assert ambiguous.placements == ()
    assert ambiguous.unplaced[0].reason == OCC.REASON_AMBIGUOUS_FLOOR


def test_unknown_world_coordinates_stay_unplaced():
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[
            agent("agt-floor", metadata={"world_floor": "floor-999"}),
            agent("agt-desk", metadata={"world_desk": "no-such-desk"}),
            agent("agt-room", metadata={
                "world_floor": "floor-001", "world_room": "no-such-room"}),
        ],
    )
    reasons = {item.agent_id: item.reason for item in projection.unplaced}
    assert reasons == {
        "agt-floor": OCC.REASON_UNKNOWN_FLOOR,
        "agt-desk": OCC.REASON_UNKNOWN_DESK,
        "agt-room": OCC.REASON_UNKNOWN_ROOM,
    }


# ------------------------------------------------------ office seat matching
def test_office_seat_slug_uses_the_seats_own_explicit_world_desk():
    wanted = desk_ids(MAIN)[2]
    seat = {
        "slug": "finance-analyst-1",
        "name": "Analyst",
        "division": "finance",
        "room": "some-office-room",
        "world_desk": wanted,
    }
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[agent("agt-ada", metadata={
            "office_seat_slug": "finance-analyst-1"})],
        office_seats=[seat],
    )
    [placement] = projection.placements
    assert placement.desk.desk_id == wanted
    assert placement.source == OCC.SOURCE_OFFICE_SEAT
    assert placement.detail == "world:metadata.world_desk"


def test_office_seat_division_places_when_the_department_is_unambiguous():
    projection = OCC.project_occupancy(
        floors=[FINANCE, REVENUE],
        agents=[agent("agt-ada", metadata={"seat_slug": "finance-analyst-1"})],
        office_seats=[{
            "slug": "finance-analyst-1",
            "name": "Analyst",
            "division": "finance",
            "room": "finance-desk-a",
            "floor": 99,
        }],
    )
    [placement] = projection.placements
    assert placement.desk.floor_id == "floor-001"
    assert placement.source == OCC.SOURCE_OFFICE_SEAT
    assert placement.detail == "department:finance@floor-001"


def test_office_seat_is_not_guessed_when_the_seat_is_missing():
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[agent("agt-ada", metadata={"office_seat_slug": "nope"})],
        office_seats=[],
    )
    assert projection.placements == ()
    assert projection.unplaced[0].reason == OCC.REASON_SEAT_NOT_FOUND


def test_office_seat_that_names_no_tower_location_stays_unplaced():
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[agent("agt-ada", metadata={"office_seat_slug": "academic-1"})],
        office_seats=[{
            "slug": "academic-1",
            "name": "Anthropologist",
            "division": "academic",
            "room": "boardroom",
            "floor": 1,
        }],
    )
    assert projection.placements == ()
    assert projection.unplaced[0].reason == OCC.REASON_SEAT_NOT_IN_WORLD


# --------------------------------------------------------- department rules
def test_department_only_placement_is_labeled():
    projection = OCC.project_occupancy(
        floors=[FINANCE, REVENUE],
        agents=[agent("agt-ada", department="finance")],
    )
    [placement] = projection.placements
    assert placement.source == OCC.SOURCE_DEPARTMENT
    assert placement.detail == "department:finance@floor-001"
    assert placement.desk.desk_id == desk_ids(FOCUS)[0]


def test_repeated_department_is_ambiguous_and_never_guessed():
    second = floor(3, "finance", (Room("floor-003-main", "Main", "work", 3),))
    projection = OCC.project_occupancy(
        floors=[FINANCE, second],
        agents=[agent("agt-ada", department="finance")],
    )
    assert projection.placements == ()
    assert projection.unplaced[0].reason == OCC.REASON_AMBIGUOUS_DEPARTMENT


def test_unknown_department_and_no_link_stay_unplaced():
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[
            agent("agt-ada", department="legal"),
            agent("agt-bob"),
        ],
    )
    reasons = {item.agent_id: item.reason for item in projection.unplaced}
    assert reasons == {
        "agt-ada": OCC.REASON_UNKNOWN_DEPARTMENT,
        "agt-bob": OCC.REASON_NO_MATCH,
    }


def test_real_catalog_does_not_guess_a_seat_by_department():
    # The shipped catalog repeats every department on five floors, so a
    # department-only agent must stay unplaced rather than pick one of them.
    registry = RoleRegistry.default()
    projection = OCC.project_occupancy(
        registry=registry,
        agents=[agent("agt-ada", department="finance")],
    )
    assert projection.placements == ()
    assert projection.unplaced[0].reason == OCC.REASON_AMBIGUOUS_DEPARTMENT


# --------------------------------------------------------- integrity rules
def test_a_desk_is_never_assigned_twice():
    wanted = desk_ids(MAIN)[0]
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[
            agent("agt-ada", metadata={"world_desk": wanted}),
            agent("agt-bob", metadata={"world_desk": wanted}),
        ],
    )
    assert len(projection.placements) == 1
    assert projection.placements[0].agent_id == "agt-ada"
    assert projection.unplaced[0].agent_id == "agt-bob"
    assert projection.unplaced[0].reason == OCC.REASON_DESK_TAKEN
    assert len(projection.by_desk()) == 1


def test_no_agent_is_invented_and_invalid_rows_are_ignored():
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[
            agent("agt-ada", department="finance"),
            agent("agt-bob", department="legal"),
            {"display_name": "no id"},
        ],
    )
    counts = projection.counts()
    assert counts["agents"] == 3
    assert counts["placed"] + counts["unplaced"] == 2
    assert counts["ignored"] == 1
    stored = {p.agent_id for p in projection.placements}
    stored |= {u.agent_id for u in projection.unplaced}
    assert stored == {"agt-ada", "agt-bob"}


def test_dormant_agents_never_occupy_a_desk():
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[
            agent("agt-active", department="finance"),
            agent("agt-paused", department="finance", status="paused"),
            agent("agt-archived", department="finance", status="archived"),
        ],
    )
    assert [p.agent_id for p in projection.placements] == ["agt-active"]
    reasons = {u.agent_id: u.reason for u in projection.unplaced}
    assert reasons == {
        "agt-paused": OCC.REASON_DORMANT,
        "agt-archived": OCC.REASON_DORMANT,
    }


def test_empty_scene_is_empty_and_stable():
    first = OCC.project_occupancy(floors=[FINANCE])
    second = OCC.project_occupancy(floors=[FINANCE])
    assert first.placements == () and first.unplaced == ()
    assert first.counts()["agents"] == 0
    assert first.by_desk() == {}
    assert first.digest == second.digest
    payload = OCC.occupancy_payload(first)
    assert payload["staffed"] is False
    assert payload["counts"]["placed"] == 0
    assert payload["placed"] == [] and payload["unplaced"] == []


def test_projection_is_deterministic_and_byte_identical():
    floors = [FINANCE, REVENUE]
    agents = [
        agent("agt-z", department="finance"),
        agent("agt-a", metadata={"world_desk": desk_ids(MAIN)[3]}),
        agent("agt-m", metadata={"world_room": "floor-002-main",
                                 "world_floor": "floor-002"}),
    ]
    first = OCC.project_occupancy(floors=floors, agents=agents)
    second = OCC.project_occupancy(floors=floors, agents=agents)
    assert first.digest == second.digest
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True)
    assert len(first.placements) == 3
    assert len({p.desk.desk_id for p in first.placements}) == 3


def test_payload_is_bounded():
    floors = [FINANCE]
    agents = [agent(f"agt-{i:04d}", department="finance")
              for i in range(40)]
    projection = OCC.project_occupancy(
        floors=floors, agents=agents, max_list=5)
    payload = OCC.occupancy_payload(projection, max_list=5)
    assert len(payload["placed"]) == 5
    assert len(payload["unplaced"]) == 5
    assert payload["truncated"] == {"placed": True, "unplaced": True}
    assert payload["counts"]["agents"] == 40


def test_projection_opens_no_socket(monkeypatch):
    def bomb(*_args, **_kwargs):
        raise AssertionError("occupancy must not open a socket")

    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(socket, "create_connection", bomb)
    projection = OCC.project_occupancy(
        floors=[FINANCE],
        agents=[agent("agt-ada", department="finance")],
    )
    assert projection.counts()["placed"] == 1


# ----------------------------------------------------------- HTTP surface
class _Cfg:
    """Minimal Config stand-in: the bridge only reads ``state_dir()``."""

    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return self._state


def _call(cfg, path, *, query=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "GET", path, query=query or {}, body={})


def _first_desk(cfg) -> str:
    code, payload = _call(cfg, "/api/office/world/tower")
    assert code == 200
    for floor in payload["floors"]:
        for room in floor["rooms"]:
            for cell in room["cells"]:
                for slot in cell["desk_slots"]:
                    return slot["desk_id"]
    raise AssertionError("the tower has no desks")


def _business_agents(state_dir, records):
    from voicebridge.business.agents import AgentControlService, Permissions
    from voicebridge.business.store import BusinessStore

    store = BusinessStore.from_config(_Cfg(state_dir))
    tenant = store.create_tenant("Personal", tenant_id="ten_personal")
    business = store.create_business(
        tenant.tenant_id, "Personal", slug="personal")
    service = AgentControlService(store)
    for index, record in enumerate(records):
        extra = {"desk": record["desk"]} if record.get("desk") else {}
        service.create(
            tenant.tenant_id,
            business.business_id,
            role_id="office:worker",
            department=record.get("department") or "Unassigned",
            display_name=record["display_name"],
            human_owner="owner",
            provider="qubit",
            model="active",
            permissions=Permissions(),
            metadata=record.get("metadata") or {},
            idempotency_key=f"occupancy-{index}",
            **extra,
        )
    return store


def test_staffed_read_serves_explicit_occupancy_and_unplaced(tmp_path):
    state = tmp_path / "state"
    cfg = _Cfg(state)
    wanted = _first_desk(cfg)
    _business_agents(state, [
        {
            "display_name": "Ada Lovelace",
            "department": "Unassigned",
            "metadata": {"world_desk": wanted, "persona_emoji": "🧮"},
        },
        {
            "display_name": "Grace Hopper",
            "department": "Unassigned",
            "metadata": {},
        },
    ])
    code, payload = _call(
        cfg, "/api/office/world/tower", query={"staffed": "1"})
    assert code == 200
    assert payload["staffed"] is True
    assert payload["counts"]["agents"] == 1
    occupancy = payload["occupancy"]
    assert occupancy["schema"] == OCC.SCHEMA
    assert occupancy["agents_source"] == "business_db"
    assert occupancy["counts"] == {
        "agents": 2, "placed": 1, "unplaced": 1, "ignored": 0,
        "occupied_desks": 1, "floors_staffed": 1, "rooms_staffed": 1,
    }
    matched = [
        slot for floor in payload["floors"]
        for room in floor["rooms"]
        for cell in room["cells"]
        for slot in cell["desk_slots"] if slot["desk_id"] == wanted
    ]
    assert len(matched) == 1
    occupant = matched[0]["occupied_by"]
    assert occupant["agent_id"]
    assert occupant["name"] == "Ada Lovelace"
    assert occupant["initials"] == "AL"
    assert occupant["emoji"] == "🧮"
    assert occupant["source"] == OCC.SOURCE_WORLD_DESK
    assert occupancy["placed"][0]["placement_source"] == OCC.SOURCE_WORLD_DESK
    [unplaced] = occupancy["unplaced"]
    assert unplaced["name"] == "Grace Hopper"
    assert unplaced["reason"] == OCC.REASON_UNKNOWN_DEPARTMENT


def test_staffed_read_does_not_create_a_business_store(tmp_path):
    state = tmp_path / "state"
    cfg = _Cfg(state)
    code, payload = _call(
        cfg, "/api/office/world/tower", query={"staffed": "1"})
    assert code == 200
    assert payload["staffed"] is False
    assert payload["occupancy"]["agents_source"] == "missing"
    assert payload["occupancy"]["counts"]["placed"] == 0
    assert not state.exists()
    for floor in payload["floors"]:
        for room in floor["rooms"]:
            for cell in room["cells"]:
                for slot in cell["desk_slots"]:
                    assert slot["occupied_by"] is None


def test_unstaffed_read_stays_unstaffed_even_with_agents(tmp_path):
    state = tmp_path / "state"
    cfg = _Cfg(state)
    wanted = _first_desk(cfg)
    _business_agents(state, [{
        "display_name": "Ada",
        "metadata": {"world_desk": wanted},
    }])
    code, payload = _call(cfg, "/api/office/world/tower")
    assert code == 200
    assert payload["staffed"] is False
    assert "occupancy" not in payload
    for floor in payload["floors"]:
        for room in floor["rooms"]:
            for cell in room["cells"]:
                for slot in cell["desk_slots"]:
                    assert slot["occupied_by"] is None


def test_staffed_read_opens_no_socket(tmp_path, monkeypatch):
    def bomb(*_args, **_kwargs):
        raise AssertionError("the staffed tower read must not open a socket")

    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(socket, "create_connection", bomb)
    code, payload = _call(
        _Cfg(tmp_path / "state"), "/api/office/world/tower",
        query={"staffed": "1"})
    assert code == 200
    assert payload["occupancy"]["counts"]["placed"] == 0
