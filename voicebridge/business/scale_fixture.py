"""P8-01 — deterministic 100-floor / 1,000-agent scale fixture.

This module is a *projection*, never an operational workforce. It builds the
shipped 100-floor / 5-wing / 300-room / 3,075-desk tower through the real
deterministic projection (:mod:`voicebridge.office.world.tower_plan`), then
derives 1,000 synthetic agent records on top of that geometry. Every record is
scoped to one synthetic tenant and to a department business; every agent is
marked ``status="projection"``, ``source="seed_projection"`` and
``operational=False`` so no caller can mistake the fixture for a real roster.

What this module *does* measure, and what it refuses to measure
---------------------------------------------------------------
It measures the deterministic data path:

* the tower projection (counts, ids, digest),
* the canonical active/dormant tiering from
  :mod:`voicebridge.office.world.active` (camera rooms are presentation-only;
  interaction/urgent/deadline/approval/notification rooms simulate),
* the active-agent and visible-avatar caps,
* hierarchical pathfinding over a campus → wing → floor → room → cell graph,
* a deterministic client-event budget model with coalescing.

It does **not** measure a browser, a GPU or a browser heap. Those metrics live
in :func:`device_tier_limits` as budgets and are reported ``not_measured`` by
``scripts/verify_scale.py`` unless a real sample is supplied. Fabricating them
here would be the failure this fixture exists to prevent.

Pure Python. No I/O, no clock, no store, no network, no model call, no
mutation of a shared object.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..office.world import active as active_module
from ..office.world import model as model_module
from ..office.world.tower_plan import build_tower_plan
from .roles import RoleRegistry

__all__ = [
    "SCHEMA",
    "SYNTHETIC_TENANT",
    "EXPECTED_COUNTS",
    "DEFAULT_ACTIVE_AGENT_LIMIT",
    "DEFAULT_VISIBLE_AVATAR_LIMIT",
    "EVENT_NORMAL_LIMIT",
    "EVENT_BURST_LIMIT",
    "PROJECTION_SOURCES",
    "DEVICE_TIERS",
    "ScaleFixtureError",
    "ScaleBusiness",
    "ScaleFloor",
    "ScaleRoom",
    "ScaleSeat",
    "ScaleAgent",
    "ScaleFixture",
    "ScaleView",
    "EventBudget",
    "PathfindingReport",
    "build_scale_fixture",
    "build_scale_path_graph",
    "ScalePathGraph",
    "select_scale_view",
    "sample_pathfinding",
    "model_event_budget",
    "device_tier_limits",
]

SCHEMA = "qubit-office-scale-fixture/1"

#: The one synthetic tenant. It is deliberately not shaped like a real id and
#: is refused by nothing else in the product; the fixture is self-contained.
SYNTHETIC_TENANT = "tenant:scale-fixture"

EXPECTED_COUNTS: Mapping[str, int] = {
    "floors": 100,
    "wings": 5,
    "rooms": 300,
    "cells": 575,
    "desks": 3075,
    "agents": 1000,
}

#: UI contract §3 / improvements §6: active agents ≤40, visible avatars ≤80.
DEFAULT_ACTIVE_AGENT_LIMIT = 40
DEFAULT_VISIBLE_AVATAR_LIMIT = 80

EVENT_NORMAL_LIMIT = 20
EVENT_BURST_LIMIT = 60

#: The projection-source vocabulary used by
#: :mod:`voicebridge.business.workforce`; a seed record is never ``live``.
PROJECTION_SOURCES = frozenset(
    {"office_plan_building", "seed", "projection", "seed_projection"}
)

#: Device budgets from the UI contract §3 and improvements §6. Frame time,
#: heap, decoded texture, map/chunk data and bundle sizes are browser/GPU
#: measurements; the fixture only carries the limits.
DEVICE_TIERS: Mapping[str, Mapping[str, float]] = {
    "desktop": {
        "frame_p95_ms": 16.7,
        "heap_mb": 512.0,
        "decoded_texture_mb": 256.0,
        "map_chunk_mb": 128.0,
        "visible_floor_bundle_mb": 8.0,
        "adjacent_prefetch_mb": 12.0,
        "active_agents": float(DEFAULT_ACTIVE_AGENT_LIMIT),
        "visible_avatars": float(DEFAULT_VISIBLE_AVATAR_LIMIT),
        "pathfinding_avg_ms": 2.0,
        "pathfinding_p95_ms": 8.0,
        "events_per_second": float(EVENT_NORMAL_LIMIT),
        "event_burst": float(EVENT_BURST_LIMIT),
    },
    "low": {
        "frame_p95_ms": 33.3,
        "heap_mb": 256.0,
        "decoded_texture_mb": 128.0,
        "map_chunk_mb": 32.0,
        "visible_floor_bundle_mb": 8.0,
        "adjacent_prefetch_mb": 12.0,
        "active_agents": float(DEFAULT_ACTIVE_AGENT_LIMIT),
        "visible_avatars": float(DEFAULT_VISIBLE_AVATAR_LIMIT),
        "pathfinding_avg_ms": 2.0,
        "pathfinding_p95_ms": 8.0,
        "events_per_second": float(EVENT_NORMAL_LIMIT),
        "event_burst": float(EVENT_BURST_LIMIT),
    },
}

#: Event-budget model constants. These are a *model*, not a measurement:
#: each simulated agent publishes a state update at a slow cadence, each
#: visible avatar's movement is quantised/coalesced, and UI chrome adds a
#: small constant. A burst multiplies the raw demand and then coalesces it.
_SIM_EVENT_HZ = 0.25
_AVATAR_EVENT_HZ = 0.10
_UI_EVENT_HZ = 1.0
_BURST_MULTIPLIER = 4.0
_BURST_COALESCE_RATIO = 0.75


class ScaleFixtureError(Exception):
    """A typed refusal from the scale fixture."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ------------------------------------------------------------------- records
@dataclass(frozen=True)
class ScaleBusiness:
    """A tenant-scoped business/department record (a projection, not live)."""

    business_id: str
    name: str
    department_id: str
    wing_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "business_id": self.business_id,
            "name": self.name,
            "department_id": self.department_id,
            "wing_id": self.wing_id,
            "projection": True,
            "operational": False,
        }


@dataclass(frozen=True)
class ScaleFloor:
    """One floor of the 100-floor projection."""

    floor_id: str
    index: int
    wing: str
    department_id: str
    active_by_default: bool
    room_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "floor_id": self.floor_id,
            "index": int(self.index),
            "wing": self.wing,
            "department_id": self.department_id,
            "active_by_default": bool(self.active_by_default),
            "room_ids": list(self.room_ids),
        }


@dataclass(frozen=True)
class ScaleRoom:
    """One room; capacity is desks, never an occupant."""

    room_id: str
    floor_id: str
    wing: str
    department_id: str
    business_id: str
    name: str
    kind: str
    archetype: str
    capacity: int
    cell_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "floor_id": self.floor_id,
            "wing": self.wing,
            "department_id": self.department_id,
            "business_id": self.business_id,
            "name": self.name,
            "kind": self.kind,
            "archetype": self.archetype,
            "capacity": int(self.capacity),
            "cell_ids": list(self.cell_ids),
        }


@dataclass(frozen=True)
class ScaleSeat:
    """One deterministic seat: a capability, not a person."""

    seat_id: str
    desk_id: str
    room_id: str
    floor_id: str
    cell_id: str


@dataclass(frozen=True)
class ScaleAgent:
    """A synthetic, tenant/business-scoped seed agent record.

    ``status``/``source``/``operational`` are the honesty flags: this record
    must never be presented as a running agent.
    """

    agent_id: str
    tenant_id: str
    business_id: str
    department_id: str
    floor_id: str
    room_id: str
    seat_id: str
    desk_id: str
    role_id: Optional[str]
    status: str = "projection"
    source: str = "seed_projection"
    operational: bool = False
    priority: int = 0
    urgent: bool = False
    deadline: Optional[float] = None

    @property
    def is_projection(self) -> bool:
        return (
            self.operational is False
            and self.status == "projection"
            and self.source in PROJECTION_SOURCES
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "department_id": self.department_id,
            "floor_id": self.floor_id,
            "room_id": self.room_id,
            "seat_id": self.seat_id,
            "desk_id": self.desk_id,
            "role_id": self.role_id,
            "status": self.status,
            "source": self.source,
            "operational": bool(self.operational),
            "priority": int(self.priority),
            "urgent": bool(self.urgent),
            "deadline": self.deadline,
        }


@dataclass(frozen=True)
class ScaleFixture:
    """The whole deterministic projection plus its tenant scope."""

    tenant_id: str
    seed: int
    businesses: Tuple[ScaleBusiness, ...]
    floors: Tuple[ScaleFloor, ...]
    rooms: Tuple[ScaleRoom, ...]
    seats: Tuple[ScaleSeat, ...]
    agents: Tuple[ScaleAgent, ...]
    digest: str = ""

    # -- indexes ---------------------------------------------------------
    def floor(self, floor_id: str) -> ScaleFloor:
        for item in self.floors:
            if item.floor_id == floor_id:
                return item
        raise ScaleFixtureError("SCALE_FIXTURE_UNKNOWN_FLOOR", floor_id)

    def business(self, business_id: str) -> ScaleBusiness:
        for item in self.businesses:
            if item.business_id == business_id:
                return item
        raise ScaleFixtureError("SCALE_FIXTURE_UNKNOWN_BUSINESS", business_id)

    def rooms_by_floor(self) -> Dict[str, Tuple[ScaleRoom, ...]]:
        grouped: Dict[str, List[ScaleRoom]] = {}
        for room in self.rooms:
            grouped.setdefault(room.floor_id, []).append(room)
        return {key: tuple(values) for key, values in grouped.items()}

    def rooms_by_id(self) -> Dict[str, ScaleRoom]:
        return {room.room_id: room for room in self.rooms}

    def agents_by_room(self) -> Dict[str, Tuple[ScaleAgent, ...]]:
        grouped: Dict[str, List[ScaleAgent]] = {}
        for agent in self.agents:
            grouped.setdefault(agent.room_id, []).append(agent)
        return {key: tuple(values) for key, values in grouped.items()}

    def default_active_floor(self) -> ScaleFloor:
        """The floor with the most desks (the trading floor in the shipped
        catalog). Ties break on ``floor_id`` so the choice is deterministic."""

        return sorted(self.floors, key=lambda f: (-self._floor_desks(f), f.floor_id))[0]

    def _floor_desks(self, floor: ScaleFloor) -> int:
        rooms = set(floor.room_ids)
        return sum(room.capacity for room in self.rooms if room.room_id in rooms)

    # -- tenant scope ----------------------------------------------------
    def agents_for(
        self,
        tenant_id: str,
        *,
        business_id: Optional[str] = None,
    ) -> Tuple[ScaleAgent, ...]:
        """Return only the agents scoped to ``tenant_id``.

        A foreign tenant returns an empty tuple. Isolation is a filter, not a
        guess: the fixture never hands another tenant's records to a caller.
        """

        return tuple(
            agent
            for agent in self.agents
            if agent.tenant_id == tenant_id
            and (business_id is None or agent.business_id == business_id)
        )

    def require_tenant(self, tenant_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise ScaleFixtureError(
                "SCALE_FIXTURE_FOREIGN_TENANT",
                f"{tenant_id!r} is not the synthetic fixture tenant",
            )

    # -- reporting -------------------------------------------------------
    def counts(self) -> Dict[str, int]:
        return {
            "floors": len(self.floors),
            "wings": len({floor.wing for floor in self.floors}),
            "rooms": len(self.rooms),
            "cells": sum(len(room.cell_ids) for room in self.rooms),
            "desks": len(self.seats),
            "agents": len(self.agents),
            "businesses": len(self.businesses),
            "departments": len({b.department_id for b in self.businesses}),
        }

    def to_dict(self) -> Dict[str, Any]:
        seat_ids_digest = hashlib.sha256(
            "\n".join(sorted(seat.seat_id for seat in self.seats)).encode("utf-8")
        ).hexdigest()
        return {
            "schema": SCHEMA,
            "tenant_id": self.tenant_id,
            "seed": int(self.seed),
            "synthetic": True,
            "projection": True,
            "operational": False,
            "counts": self.counts(),
            "seat_ids_sha256": seat_ids_digest,
            "businesses": [b.to_dict() for b in self.businesses],
            "floors": [f.to_dict() for f in self.floors],
            "rooms": [r.to_dict() for r in self.rooms],
            "agents": [a.to_dict() for a in self.agents],
        }

    def digest_payload(self) -> Dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class ScaleView:
    """The active/dormant view over a fixture: what a renderer may draw."""

    active_floor_id: str
    camera_floor_ids: Tuple[str, ...]
    camera_room_ids: Tuple[str, ...]
    interaction_room_id: str
    urgent_room_ids: Tuple[str, ...]
    deadline_room_ids: Tuple[str, ...]
    approval_room_ids: Tuple[str, ...]
    notification_room_ids: Tuple[str, ...]
    active_room_ids: Tuple[str, ...]
    dormant_room_ids: Tuple[str, ...]
    simulated_room_ids: Tuple[str, ...]
    simulated_candidate_count: int
    simulated_agents: Tuple[ScaleAgent, ...]
    visible_candidate_count: int
    visible_avatars: Tuple[ScaleAgent, ...]
    dormant_agents: Tuple[ScaleAgent, ...]
    throttled_agent_count: int
    decisions: Tuple[active_module.ActiveDecision, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_floor_id": self.active_floor_id,
            "camera_floor_ids": list(self.camera_floor_ids),
            "camera_rooms": len(self.camera_room_ids),
            "interaction_room_id": self.interaction_room_id,
            "urgent_rooms": len(self.urgent_room_ids),
            "deadline_rooms": len(self.deadline_room_ids),
            "approval_rooms": len(self.approval_room_ids),
            "notification_rooms": len(self.notification_room_ids),
            "active_rooms": len(self.active_room_ids),
            "dormant_rooms": len(self.dormant_room_ids),
            "simulated_rooms": len(self.simulated_room_ids),
            "active_agents": len(self.simulated_agents),
            "active_agent_candidates": int(self.simulated_candidate_count),
            "visible_avatars": len(self.visible_avatars),
            "visible_avatar_candidates": int(self.visible_candidate_count),
            "dormant_agents": len(self.dormant_agents),
            "throttled_agents": int(self.throttled_agent_count),
        }


@dataclass(frozen=True)
class EventBudget:
    """A deterministic event-rate *model* (never a browser measurement)."""

    normal_events: int
    burst_events: int
    burst_raw: int
    coalesced: int
    normal_cap: int
    burst_cap: int
    measured: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "budget_model",
            "measured": bool(self.measured),
            "normal_events": int(self.normal_events),
            "normal_cap": int(self.normal_cap),
            "burst_events": int(self.burst_events),
            "burst_raw": int(self.burst_raw),
            "burst_cap": int(self.burst_cap),
            "coalesced": int(self.coalesced),
        }


class ScalePathGraph:
    """The hierarchical movement graph with an adjacency index.

    :class:`voicebridge.office.world.model.PathGraph` is the reference shape
    (a portal list, consulted linearly). A renderer or worker indexes it once;
    this class does exactly that so a pathfinding sample measures the graph a
    client actually walks, not the cost of scanning every portal per hop. The
    portal list is still built from ``model.Portal`` objects, so the topology
    is the shipped hierarchy: campus → wing → floor → room → cell.
    """

    def __init__(self, portals: Sequence[model_module.Portal]):
        self._portals = tuple(portals)
        neighbours: Dict[str, set[str]] = {}
        for portal in portals:
            neighbours.setdefault(portal.source, set()).add(portal.target)
            if portal.bidirectional:
                neighbours.setdefault(portal.target, set()).add(portal.source)
            else:
                neighbours.setdefault(portal.target, set())
        self._adjacency: Dict[str, Tuple[str, ...]] = {
            node: tuple(sorted(values))
            for node, values in neighbours.items()
        }

    @property
    def portal_count(self) -> int:
        return len(self._portals)

    def nodes(self) -> set[str]:
        return set(self._adjacency)

    def neighbours(self, node: str) -> Tuple[str, ...]:
        return self._adjacency.get(node, ())

    def path(self, source: str, target: str) -> Optional[Tuple[str, ...]]:
        if source == target:
            return (source,)
        if source not in self._adjacency or target not in self._adjacency:
            return None
        seen = {source}
        queue: List[Tuple[str, ...]] = [(source,)]
        cursor = 0
        while cursor < len(queue):
            trail = queue[cursor]
            cursor += 1
            for nxt in self._adjacency.get(trail[-1], ()):
                if nxt in seen:
                    continue
                if nxt == target:
                    return trail + (nxt,)
                seen.add(nxt)
                queue.append(trail + (nxt,))
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": len(self._adjacency),
            "portals": len(self._portals),
            "adjacency_indexed": True,
            "hierarchy": ("campus", "wing", "floor", "room", "cell"),
        }


@dataclass(frozen=True)
class PathfindingReport:
    """Measured hierarchical pathfinding over the fixture graph."""

    queries: int
    samples: int
    iterations: int
    avg_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    avg_hops: float
    max_hops: int
    measurement: str = (
        "Python data path (adjacency-indexed hierarchical BFS) measured in"
        " this process; not a browser or worker frame"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queries": int(self.queries),
            "samples": int(self.samples),
            "iterations": int(self.iterations),
            "avg_ms": round(float(self.avg_ms), 6),
            "p95_ms": round(float(self.p95_ms), 6),
            "min_ms": round(float(self.min_ms), 6),
            "max_ms": round(float(self.max_ms), 6),
            "avg_hops": round(float(self.avg_hops), 3),
            "max_hops": int(self.max_hops),
            "measurement": self.measurement,
        }


# ------------------------------------------------------------------ builders
def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_int(value: str, modulus: int) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:12], 16) % modulus


def build_scale_fixture(
    *,
    seed: int = 8001,
    agent_count: int = 1000,
    tenant_id: str = SYNTHETIC_TENANT,
) -> ScaleFixture:
    """Build the deterministic scale projection.

    ``seed`` perturbs only per-agent priority/urgency, never the geometry or
    the active/visible caps. Two calls with the same seed return the same
    digest; different seeds change the digest while the counts stay fixed.
    """

    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ScaleFixtureError("SCALE_FIXTURE_BAD_SEED", "seed must be an int")
    if not isinstance(agent_count, int) or isinstance(agent_count, bool):
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_AGENT_COUNT", "agent_count must be an int"
        )
    if agent_count < 1 or agent_count > 10000:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_AGENT_COUNT",
            "agent_count must be 1..10000",
        )
    if not tenant_id:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_TENANT", "tenant_id must be non-empty"
        )

    registry = RoleRegistry.default()
    plan = build_tower_plan(registry)
    dept_by_floor = {
        floor.floor_id: floor.department_ids[0] for floor in registry.floors
    }

    rooms: List[ScaleRoom] = []
    seats: List[ScaleSeat] = []
    floors: List[ScaleFloor] = []
    for floor in plan.floors:
        department_id = dept_by_floor.get(floor.floor_id, "unassigned")
        business_id = f"biz-{department_id}"
        room_ids: List[str] = []
        for room in floor.rooms:
            cell_ids = tuple(cell.cell_id for cell in room.cells)
            room_ids.append(room.room_id)
            rooms.append(
                ScaleRoom(
                    room_id=room.room_id,
                    floor_id=floor.floor_id,
                    wing=floor.wing,
                    department_id=department_id,
                    business_id=business_id,
                    name=room.name,
                    kind=room.kind,
                    archetype=room.archetype,
                    capacity=room.desk_count,
                    cell_ids=cell_ids,
                )
            )
            for cell in room.cells:
                for desk in cell.desks:
                    seats.append(
                        ScaleSeat(
                            seat_id=desk.seat_id,
                            desk_id=desk.desk_id,
                            room_id=room.room_id,
                            floor_id=floor.floor_id,
                            cell_id=desk.cell_id,
                        )
                    )
        floors.append(
            ScaleFloor(
                floor_id=floor.floor_id,
                index=floor.index,
                wing=floor.wing,
                department_id=department_id,
                active_by_default=bool(floor.active),
                room_ids=tuple(room_ids),
            )
        )

    first_wing_by_dept: Dict[str, str] = {}
    for floor in floors:
        first_wing_by_dept.setdefault(floor.department_id, floor.wing)
    businesses = tuple(
        ScaleBusiness(
            business_id=f"biz-{department_id}",
            name=f"Scale {department_id.replace('_', ' ').title()}",
            department_id=department_id,
            wing_id=wing,
        )
        for department_id, wing in sorted(first_wing_by_dept.items())
    )

    floor_by_room = {room.room_id: room.floor_id for room in rooms}
    room_by_id = {room.room_id: room for room in rooms}
    room_roles = {
        room.room_id: tuple(room.role_ids)
        for floor in plan.floors
        for room in floor.rooms
    }
    ordered_seats = sorted(seats, key=lambda seat: seat.seat_id)
    if not ordered_seats:
        raise ScaleFixtureError("SCALE_FIXTURE_NO_SEATS", "the tower has no seats")

    agents: List[ScaleAgent] = []
    for index in range(agent_count):
        seat = ordered_seats[index * len(ordered_seats) // agent_count]
        room = room_by_id[seat.room_id]
        floor_id = floor_by_room[seat.room_id]
        agent_id = f"agent-scale-{index:04d}"
        # Room role assignments come from the tower projection; resolve one
        # deterministically when the room names any.
        roles = room_roles.get(room.room_id, ())
        role_id = roles[index % len(roles)] if roles else None
        agents.append(
            ScaleAgent(
                agent_id=agent_id,
                tenant_id=tenant_id,
                business_id=room.business_id,
                department_id=room.department_id,
                floor_id=floor_id,
                room_id=room.room_id,
                seat_id=seat.seat_id,
                desk_id=seat.desk_id,
                role_id=role_id,
                priority=_stable_int(f"{seed}:{agent_id}:priority", 10),
                urgent=_stable_int(f"{seed}:{agent_id}:urgent", 97) == 0,
                deadline=(
                    float(_stable_int(f"{seed}:{agent_id}:deadline", 600))
                    if _stable_int(f"{seed}:{agent_id}:deadline?", 53) == 0
                    else None
                ),
            )
        )

    digest = _digest(
        {
            "schema": SCHEMA,
            "tenant_id": tenant_id,
            "seed": int(seed),
            "counts": {
                "floors": len(floors),
                "rooms": len(rooms),
                "seats": len(seats),
                "agents": len(agents),
            },
            "floors": [f.to_dict() for f in floors],
            "rooms": [r.to_dict() for r in rooms],
            "agents": [a.to_dict() for a in agents],
        }
    )
    return ScaleFixture(
        tenant_id=tenant_id,
        seed=seed,
        businesses=businesses,
        floors=tuple(floors),
        rooms=tuple(rooms),
        seats=tuple(ordered_seats),
        agents=tuple(agents),
        digest=digest,
    )


# ---------------------------------------------------------------- policy view
def _policy_campus(fixture: ScaleFixture) -> model_module.Campus:
    """Build the canonical active-set view (Campus → Wing → Room).

    The world model predates the 100-floor projection: its ``Wing`` holds rooms
    directly, so floor identity is carried by :class:`ScaleFloor` and used for
    camera/adjacent selection here. The policy only reads room ids and
    priorities, so a minimal LogicalRoom is sufficient and cannot invent a
    business fact.
    """

    wing_rooms: Dict[str, List[model_module.LogicalRoom]] = {}
    for room in fixture.rooms:
        wing_rooms.setdefault(room.wing, []).append(
            model_module.LogicalRoom(
                room_id=room.room_id,
                name=room.name,
                wing_id=room.wing,
                kind=room.kind,
                archetype=room.archetype,
                business_id=room.business_id,
                cells=(),
                priority=0,
            )
        )
    wings: List[model_module.Wing] = []
    for index, wing_id in enumerate(sorted(wing_rooms)):
        wings.append(
            model_module.Wing(
                wing_id=wing_id,
                name=f"{wing_id.title()} Wing",
                index=index,
                legacy_floor_id=index,
                rooms=tuple(wing_rooms[wing_id]),
                z=index * model_module.WING_HEIGHT_UNITS,
            )
        )
    businesses = tuple(
        model_module.Business(
            business_id=b.business_id,
            name=b.name,
            wing_id=b.wing_id,
            department_id=b.department_id,
            room_ids=tuple(
                room.room_id
                for room in fixture.rooms
                if room.business_id == b.business_id
            ),
        )
        for b in fixture.businesses
    )
    return model_module.Campus(
        campus_id="campus-scale-fixture",
        name="Scale Fixture Campus",
        wings=tuple(wings),
        businesses=businesses,
    )


def _agent_order(agent: ScaleAgent) -> Tuple[int, int, int, str]:
    return (
        0 if agent.urgent else 1,
        0 if agent.deadline is not None else 1,
        -int(agent.priority),
        agent.agent_id,
    )


def select_scale_view(
    fixture: ScaleFixture,
    *,
    active_agent_limit: int = DEFAULT_ACTIVE_AGENT_LIMIT,
    visible_avatar_limit: int = DEFAULT_VISIBLE_AVATAR_LIMIT,
    adjacent_prefetch: bool = True,
    extra_simulated_rooms: int = 18,
    policy: Optional[active_module.ActivePolicy] = None,
) -> ScaleView:
    """Tier rooms through the canonical policy and bound the drawn set.

    The active floor is the densest floor (the shipped trading floor). Camera
    rooms for that floor and one adjacent floor are presentation-only; the
    interaction room plus deterministic urgent/deadline/approval/notification
    rooms are simulated. Active agents and visible avatars are capped.
    """

    if active_agent_limit < 1 or visible_avatar_limit < 1:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_LIMIT", "agent/avatar limits must be positive"
        )
    if visible_avatar_limit < active_agent_limit:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_LIMIT",
            "visible avatars must be at least the active-agent cap",
        )

    rooms_by_floor = fixture.rooms_by_floor()
    active_floor = fixture.default_active_floor()
    camera_floors: List[ScaleFloor] = [active_floor]
    if adjacent_prefetch:
        neighbours = [
            floor
            for floor in fixture.floors
            if floor.wing == active_floor.wing and floor.floor_id != active_floor.floor_id
        ]
        neighbours.sort(key=lambda f: (abs(f.index - active_floor.index), f.index))
        if neighbours:
            camera_floors.append(neighbours[0])

    camera_rooms: set[str] = set()
    for floor in camera_floors:
        camera_rooms.update(floor.room_ids)
    active_floor_rooms = sorted(
        rooms_by_floor.get(active_floor.floor_id, ()),
        key=lambda room: room.room_id,
    )
    if not active_floor_rooms:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_EMPTY_FLOOR", active_floor.floor_id
        )
    interaction_room = active_floor_rooms[0].room_id

    remaining = sorted(
        room.room_id for room in fixture.rooms if room.room_id not in camera_rooms
    )
    room_budget = max(0, DEFAULT_ACTIVE_AGENT_LIMIT - len(camera_rooms))
    extra_budget = min(int(extra_simulated_rooms), room_budget)
    buckets = {"urgent": 8, "deadline": 5, "approval": 3, "notification": 2}
    chosen: Dict[str, List[str]] = {key: [] for key in buckets}
    cursor = 0
    for name, share in buckets.items():
        take = min(share, max(0, extra_budget - cursor))
        chosen[name] = remaining[cursor:cursor + take]
        cursor += take
        if cursor >= extra_budget:
            break

    signals = active_module.ActiveSignals(
        camera_rooms=frozenset(camera_rooms),
        interaction_room=interaction_room,
        urgent_rooms=frozenset(chosen["urgent"]),
        deadline_rooms=frozenset(chosen["deadline"]),
        approval_rooms=frozenset(chosen["approval"]),
        notification_rooms=frozenset(chosen["notification"]),
        now=0.0,
    )
    decisions = active_module.select_active_set(
        _policy_campus(fixture), signals, policy
    ).decisions
    decision_by_room = {d.room_id: d for d in decisions}

    active_rooms = tuple(
        sorted(d.room_id for d in decisions if d.tier > active_module.Tier.DORMANT)
    )
    dormant_rooms = tuple(
        sorted(d.room_id for d in decisions if d.tier == active_module.Tier.DORMANT)
    )
    simulated_rooms = tuple(
        sorted(
            d.room_id
            for d in decisions
            if d.tier >= active_module.Tier.LOW_RATE
            and d.state == model_module.RoomState.SIMULATION_ACTIVE
        )
    )
    simulated_set = set(simulated_rooms)
    visible_set = set(active_rooms)
    dormant_set = set(dormant_rooms)

    simulated_candidates = sorted(
        (agent for agent in fixture.agents if agent.room_id in simulated_set),
        key=_agent_order,
    )
    simulated = tuple(simulated_candidates[:active_agent_limit])
    simulated_ids = {agent.agent_id for agent in simulated}

    visible_candidates = [agent for agent in fixture.agents if agent.room_id in visible_set]
    visible_candidates.sort(
        key=lambda agent: (
            0 if agent.agent_id in simulated_ids else 1,
            *_agent_order(agent),
        )
    )
    visible = tuple(visible_candidates[:visible_avatar_limit])
    dormant_agents = tuple(agent for agent in fixture.agents if agent.room_id in dormant_set)
    throttled = max(0, len(visible_candidates) - len(visible))

    # Keep the decision for every active room even when it was not simulated;
    # the tuple is sorted by room id by the policy already.
    ordered_decisions = tuple(
        decision_by_room[room_id] for room_id in sorted(decision_by_room)
    )
    return ScaleView(
        active_floor_id=active_floor.floor_id,
        camera_floor_ids=tuple(floor.floor_id for floor in camera_floors),
        camera_room_ids=tuple(sorted(camera_rooms)),
        interaction_room_id=interaction_room,
        urgent_room_ids=tuple(chosen["urgent"]),
        deadline_room_ids=tuple(chosen["deadline"]),
        approval_room_ids=tuple(chosen["approval"]),
        notification_room_ids=tuple(chosen["notification"]),
        active_room_ids=active_rooms,
        dormant_room_ids=dormant_rooms,
        simulated_room_ids=simulated_rooms,
        simulated_candidate_count=len(simulated_candidates),
        simulated_agents=simulated,
        visible_candidate_count=len(visible_candidates),
        visible_avatars=visible,
        dormant_agents=dormant_agents,
        throttled_agent_count=throttled,
        decisions=ordered_decisions,
    )


# ---------------------------------------------------------------- pathfinding
def build_scale_path_graph(fixture: ScaleFixture) -> ScalePathGraph:
    """Campus → wing → floor → room → cell, built once from the projection."""

    portals: List[model_module.Portal] = []
    campus = model_module.campus_node("campus-scale-fixture")
    wings = sorted({floor.wing for floor in fixture.floors})
    for wing_id in wings:
        wing = model_module.wing_node(wing_id)
        portals.append(
            model_module.Portal(f"p:{campus}->{wing}", campus, wing, kind="opening")
        )
        floors = [floor for floor in fixture.floors if floor.wing == wing_id]
        for floor in floors:
            floor_node = f"floor:{floor.floor_id}"
            portals.append(
                model_module.Portal(
                    f"p:{wing}->{floor_node}", wing, floor_node, kind="lift"
                )
            )
            for room in fixture.rooms:
                if room.floor_id != floor.floor_id:
                    continue
                room_node = model_module.room_node(room.room_id)
                portals.append(
                    model_module.Portal(
                        f"p:{floor_node}->{room_node}", floor_node, room_node,
                        kind="door",
                    )
                )
                for cell_id in room.cell_ids:
                    cell = model_module.cell_node(room.room_id, cell_id)
                    portals.append(
                        model_module.Portal(
                            f"p:{room_node}->{cell}", room_node, cell,
                            kind="opening",
                        )
                    )
    return ScalePathGraph(tuple(portals))


def _percentile(samples: Sequence[float], fraction: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower]) * (1.0 - weight) + float(ordered[upper]) * weight


def sample_pathfinding(
    fixture: ScaleFixture,
    graph: Optional[ScalePathGraph] = None,
    *,
    samples: int = 32,
    iterations: int = 3,
) -> PathfindingReport:
    """Measure hierarchical BFS between deterministic room/cell pairs."""

    if samples < 1 or iterations < 1:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_SAMPLE", "samples/iterations must be positive"
        )
    graph = graph or build_scale_path_graph(fixture)
    cell_nodes = sorted(
        model_module.cell_node(room.room_id, cell_id)
        for room in fixture.rooms
        for cell_id in room.cell_ids
    )
    if not cell_nodes:
        raise ScaleFixtureError("SCALE_FIXTURE_NO_CELLS", "path graph is empty")
    count = len(cell_nodes)
    pairs: List[Tuple[str, str, int]] = []
    for index in range(samples):
        source = cell_nodes[(index * 37) % count]
        target = cell_nodes[(index * 37 + count // 2 + index * 11) % count]
        if target == source:
            target = cell_nodes[(index * 37 + 1) % count]
        path = graph.path(source, target)
        if path is None:
            raise ScaleFixtureError(
                "SCALE_FIXTURE_DISCONNECTED",
                f"no path {source} -> {target}",
            )
        pairs.append((source, target, len(path) - 1))

    timings: List[float] = []
    for _ in range(iterations):
        for source, target, _hops in pairs:
            start = time.perf_counter()
            path = graph.path(source, target)
            timings.append((time.perf_counter() - start) * 1000.0)
            if path is None:  # pragma: no cover - built graph is connected
                raise ScaleFixtureError(
                    "SCALE_FIXTURE_DISCONNECTED", f"{source} -> {target}"
                )
    hops = [item[2] for item in pairs]
    return PathfindingReport(
        queries=len(timings),
        samples=len(pairs),
        iterations=iterations,
        avg_ms=statistics.fmean(timings),
        p95_ms=_percentile(timings, 0.95),
        min_ms=min(timings),
        max_ms=max(timings),
        avg_hops=statistics.fmean(hops),
        max_hops=max(hops),
    )


# ----------------------------------------------------------------- event model
def model_event_budget(
    simulated_agents: int,
    visible_avatars: int,
    *,
    normal_cap: int = EVENT_NORMAL_LIMIT,
    burst_cap: int = EVENT_BURST_LIMIT,
) -> EventBudget:
    """A deterministic client-event budget model with coalescing.

    The output is a model, not an observed browser rate: ``measured`` is
    always false here. Burst demand is multiplied then coalesced; the
    difference is the number of events the model suppresses.
    """

    if simulated_agents < 0 or visible_avatars < 0:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_EVENT_INPUT", "counts must be non-negative"
        )
    if normal_cap < 1 or burst_cap < 1:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_BAD_EVENT_CAP", "caps must be positive"
        )
    raw_normal = (
        _UI_EVENT_HZ
        + simulated_agents * _SIM_EVENT_HZ
        + visible_avatars * _AVATAR_EVENT_HZ
    )
    normal = min(int(math.ceil(raw_normal)), int(normal_cap))
    raw_burst = int(math.ceil(raw_normal * _BURST_MULTIPLIER))
    coalesced_burst = int(math.ceil(raw_burst * _BURST_COALESCE_RATIO))
    burst = min(coalesced_burst, int(burst_cap))
    return EventBudget(
        normal_events=normal,
        burst_events=burst,
        burst_raw=raw_burst,
        coalesced=max(0, raw_burst - burst),
        normal_cap=int(normal_cap),
        burst_cap=int(burst_cap),
        measured=False,
    )


def device_tier_limits(tier: str) -> Mapping[str, float]:
    try:
        return DEVICE_TIERS[tier]
    except KeyError as exc:
        raise ScaleFixtureError(
            "SCALE_FIXTURE_UNKNOWN_TIER", f"unknown device tier {tier!r}"
        ) from exc
