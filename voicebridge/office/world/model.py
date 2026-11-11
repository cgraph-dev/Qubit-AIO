"""The Qubit Office world model: Campus -> Wing -> LogicalRoom -> Cell -> Desk/Seat.

This is the *authoritative data layer* from `docs/QUBIT-OFFICE-MASTER-PLAN.md`
§5 and §18.4. It contains no renderer, no timers and no store I/O. Every
question a business answer depends on can be answered from these objects without
knowing whether a room is on screen; the active/dormant machinery in
:mod:`voicebridge.office.world.active` is a *view* over this data, never its
owner.

The hierarchy is the plan's, verbatim:

    Campus
      Wing / Zone
        Logical Room
          Cell (16x12 Habblaud-compatible room primitive)
            Desk / Seat
              Agent presence
        Business / Department

Three rules from the plan are enforced here rather than trusted to a renderer:

* **A cell is always 16x12.** A room that needs more desks gets another cell;
  it never gets a longer cell. ``plan_cell_cluster`` is the only supported way
  to build one, and ``validate_campus`` names a stretched cell as a defect.
* **The three existing floors become three wings, and ``legacyFloorId`` is
  preserved.** ``LogicalRoom.legacy_floor_id`` and ``Wing.legacy_floor_id`` must
  agree; the compatibility key survives the hierarchy change.
* **A business record always exists.** ``Business`` is not derived from seat
  presence or from a loaded room. A dormant room keeps its pending events,
  approvals and schedules in the model (see ``validate_campus`` and
  ``LogicalRoom.to_dict``).

Seat IDs are namespaced exactly as the plan says: ``roomId:cellId:localSeatId``.
The helper :func:`seat_id` is the only place that spelling is built, and
``validate_campus`` re-derives it to catch drift.

Pure Python. No I/O, no clock, no network, deterministic.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "WorldModelError",
    "CharacterMode",
    "RoomState",
    "Tier",
    "CELL_COLS",
    "CELL_ROWS",
    "CELL_DESK_CAPACITY",
    "CELL_DESK_SLOTS",
    "CLUSTER_GAP",
    "CLUSTER_COLS",
    "WING_HEIGHT_UNITS",
    "Tile",
    "Seat",
    "Desk",
    "Cell",
    "Portal",
    "PathGraph",
    "LogicalRoom",
    "Wing",
    "Business",
    "AgentPresence",
    "Campus",
    "cell_origins",
    "plan_cell_cluster",
    "build_room",
    "assemble_campus",
    "seat_id",
    "campus_node",
    "wing_node",
    "room_node",
    "cell_node",
    "validate_campus",
    "assert_valid_campus",
    "can_transition",
]

# ------------------------------------------------------------------ geometry
#: One cell is the Habblaud-compatible room primitive. 16x12 is a *fixed*
#: contract, not a default: a wider room is a cluster of cells, never a wider
#: cell (master plan §5, "never stretch a 16x12 cell").
CELL_COLS = 16
CELL_ROWS = 12

#: Desks per cell. The plan's arithmetic (2 cells = 12 desks, 3 = 18, 4 = 24+)
#: only works at six desks per cell, so that is the constant. A cell may hold
#: fewer when a cluster distributes an odd headcount.
CELL_DESK_CAPACITY = 6

#: Fixed desk slots inside a cell, as top-left tiles of a 2x1 desk. They leave a
#: walkable aisle down the middle and along the bottom edge. The renderer may
#: draw them differently; the seat *identity* and capacity live here.
CELL_DESK_SLOTS: Tuple[Tuple[int, int], ...] = (
    (2, 2), (7, 2), (12, 2),
    (2, 8), (7, 8), (12, 8),
)

#: A cluster lays cells out on a fixed two-column grid with a two-tile gap so
#: one room's cells never touch another's.
CLUSTER_COLS = 2
CLUSTER_GAP = 2

#: A wing occupies vertical space in the projected view. Copied from
#: ``voicebridge.office.tower.FLOOR_HEIGHT_UNITS`` so a wing can replace a floor
#: without changing the renderer's z arithmetic.
WING_HEIGHT_UNITS = 3

_SEAT_ID_RE = re.compile(r"^[^:]+:[^:]+:[^:]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/{}@-]{0,127}$")

#: Legal room-state transitions, from master plan §18.4. ``PRESENTATION_ACTIVE``
#: is the proxy tier (lights/labels/silhouettes); ``SIMULATION_ACTIVE`` is the
#: full tier with pathfinding and animation.
_TRANSITIONS: Dict["RoomState", frozenset["RoomState"]] = {}


class WorldModelError(Exception):
    """The world data is internally inconsistent. ``problems`` names every one."""

    def __init__(self, problems: Iterable[str]):
        self.problems: List[str] = list(problems)
        super().__init__("; ".join(self.problems) or "invalid world model")


class CharacterMode(str, Enum):
    """How a room presents its occupants.

    ``single`` preserves classic project-room behavior (one character in the
    room's own idiom). ``multi`` is the Qubit department mode: many agents share
    the room and each keeps its own identity.
    """

    SINGLE = "single"
    MULTI = "multi"


class RoomState(str, Enum):
    """The room lifecycle from master plan §5/§18.4."""

    AUTHORED = "AUTHORED"
    LOADED_STATIC = "LOADED_STATIC"
    OBSERVED = "OBSERVED"
    PRESENTATION_ACTIVE = "PRESENTATION_ACTIVE"
    SIMULATION_ACTIVE = "SIMULATION_ACTIVE"
    DORMANT = "DORMANT"
    REHYDRATING = "REHYDRATING"


class Tier(IntEnum):
    """Active-set tiers. Higher means more of the room is simulated."""

    DORMANT = 0
    AGGREGATE = 1
    LOW_RATE = 2
    PROXY = 3
    FULL = 4


_TRANSITIONS.update({
    RoomState.AUTHORED: frozenset({RoomState.LOADED_STATIC}),
    RoomState.LOADED_STATIC: frozenset({RoomState.OBSERVED}),
    RoomState.OBSERVED: frozenset({RoomState.PRESENTATION_ACTIVE, RoomState.DORMANT}),
    RoomState.PRESENTATION_ACTIVE: frozenset({
        RoomState.SIMULATION_ACTIVE, RoomState.OBSERVED, RoomState.DORMANT}),
    RoomState.SIMULATION_ACTIVE: frozenset({
        RoomState.PRESENTATION_ACTIVE, RoomState.DORMANT}),
    RoomState.DORMANT: frozenset({RoomState.REHYDRATING}),
    RoomState.REHYDRATING: frozenset({
        RoomState.OBSERVED, RoomState.PRESENTATION_ACTIVE}),
})


def can_transition(source: RoomState, target: RoomState) -> bool:
    """Whether ``source -> target`` is a declared lifecycle edge.

    The same state is not an edge; callers that want idempotence should check
    ``source == target`` themselves.
    """

    return target in _TRANSITIONS.get(source, frozenset())


# --------------------------------------------------------------------- atoms
@dataclass(frozen=True, order=True)
class Tile:
    """An integer tile inside a cell. Bounds are checked by the owner."""

    x: int
    y: int

    def to_dict(self) -> Dict[str, int]:
        return {"x": int(self.x), "y": int(self.y)}


def seat_id(room_id: str, cell_id: str, local_seat_id: str) -> str:
    """The one spelling of a namespaced seat id: ``roomId:cellId:localSeatId``."""

    return f"{room_id}:{cell_id}:{local_seat_id}"


def campus_node(campus_id: str) -> str:
    return f"campus:{campus_id}"


def wing_node(wing_id: str) -> str:
    return f"wing:{wing_id}"


def room_node(room_id: str) -> str:
    return f"room:{room_id}"


def cell_node(room_id: str, cell_id: str) -> str:
    return f"room:{room_id}/cell:{cell_id}"


@dataclass(frozen=True)
class Seat:
    """A logical place a person or agent can occupy.

    ``seat_id`` is namespaced. ``occupied_by`` is a presence binding, not a
    business fact: an empty seat does not erase the room's queue, and a dormant
    room keeps the binding.
    """

    seat_id: str
    desk_id: str
    room_id: str
    cell_id: str
    local_seat_id: str
    tile: Tile
    kind: str = "work"
    occupied_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seat_id": self.seat_id,
            "desk_id": self.desk_id,
            "room_id": self.room_id,
            "cell_id": self.cell_id,
            "local_seat_id": self.local_seat_id,
            "tile": self.tile.to_dict(),
            "kind": self.kind,
            "occupied_by": self.occupied_by,
        }


@dataclass(frozen=True)
class Desk:
    """A desk slot and the seat it serves."""

    desk_id: str
    room_id: str
    cell_id: str
    local_desk_id: str
    tile: Tile
    seat: Seat
    width: int = 2
    height: int = 1

    @property
    def footprint(self) -> Tuple[int, int]:
        return (int(self.width), int(self.height))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "desk_id": self.desk_id,
            "room_id": self.room_id,
            "cell_id": self.cell_id,
            "local_desk_id": self.local_desk_id,
            "tile": self.tile.to_dict(),
            "footprint": {"w": int(self.width), "h": int(self.height)},
            "seat": self.seat.to_dict(),
        }


@dataclass(frozen=True)
class Cell:
    """A fixed 16x12 room primitive holding at most :data:`CELL_DESK_CAPACITY`.

    ``cols``/``rows`` are fields so a malformed cell can be *described*; the
    builder never varies them and ``validate_campus`` rejects anything else.
    """

    cell_id: str
    index: int
    origin: Tile
    desks: Tuple[Desk, ...] = ()
    cols: int = CELL_COLS
    rows: int = CELL_ROWS

    @property
    def seats(self) -> Tuple[Seat, ...]:
        return tuple(d.seat for d in self.desks)

    @property
    def capacity(self) -> int:
        return len(self.desks)

    @property
    def is_standard(self) -> bool:
        return (self.cols, self.rows) == (CELL_COLS, CELL_ROWS)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "index": int(self.index),
            "origin": self.origin.to_dict(),
            "cols": int(self.cols),
            "rows": int(self.rows),
            "standard": self.is_standard,
            "capacity": self.capacity,
            "desks": [d.to_dict() for d in self.desks],
        }


@dataclass(frozen=True)
class Portal:
    """A hierarchical path edge (door, corridor, lift, stairs, opening)."""

    portal_id: str
    source: str
    target: str
    kind: str = "door"
    tile: Optional[Tile] = None
    bidirectional: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "portal_id": self.portal_id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "tile": self.tile.to_dict() if self.tile else None,
            "bidirectional": bool(self.bidirectional),
        }


@dataclass(frozen=True)
class PathGraph:
    """The hierarchical movement graph. Nodes are string ids from the helpers."""

    portals: Tuple[Portal, ...] = ()

    def nodes(self) -> set[str]:
        out: set[str] = set()
        for p in self.portals:
            out.add(p.source)
            out.add(p.target)
        return out

    def neighbours(self, node: str) -> Tuple[str, ...]:
        out: List[str] = []
        for p in self.portals:
            if p.source == node:
                out.append(p.target)
            if p.bidirectional and p.target == node:
                out.append(p.source)
        return tuple(sorted(set(out)))

    def path(self, source: str, target: str) -> Optional[Tuple[str, ...]]:
        """Shortest node path, or ``None`` when unreachable. Deterministic BFS."""

        if source == target:
            return (source,)
        seen = {source}
        queue: List[Tuple[str, ...]] = [(source,)]
        while queue:
            trail = queue.pop(0)
            for nxt in self.neighbours(trail[-1]):
                if nxt in seen:
                    continue
                if nxt == target:
                    return trail + (nxt,)
                seen.add(nxt)
                queue.append(trail + (nxt,))
        return None

    def connected(self, root: str) -> bool:
        nodes = self.nodes()
        if not nodes:
            return True
        if root not in nodes:
            return False
        return all(n in {root} or self.path(root, n) is not None for n in nodes)

    def to_dict(self) -> Dict[str, Any]:
        return {"portals": [p.to_dict() for p in self.portals]}


@dataclass(frozen=True)
class AgentPresence:
    """An agent's presence in the world. Never the source of business truth."""

    agent_id: str
    room_id: str
    seat_id: Optional[str] = None
    state: str = "idle"
    urgent: bool = False
    deadline: Optional[float] = None
    priority: int = 0
    last_seen: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "room_id": self.room_id,
            "seat_id": self.seat_id,
            "state": self.state,
            "urgent": bool(self.urgent),
            "deadline": self.deadline,
            "priority": int(self.priority),
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True)
class LogicalRoom:
    """A business-meaningful room aggregating one or more fixed cells."""

    room_id: str
    name: str
    wing_id: str
    kind: str
    archetype: str
    business_id: str
    cells: Tuple[Cell, ...]
    legacy_floor_id: Optional[int] = None
    character_mode: CharacterMode = CharacterMode.MULTI
    style: Optional[Mapping[str, Any]] = None
    priority: int = 0
    state: RoomState = RoomState.AUTHORED
    tier: Tier = Tier.DORMANT
    pending_events: Tuple[Mapping[str, Any], ...] = ()
    approvals: Tuple[Mapping[str, Any], ...] = ()
    schedules: Tuple[Mapping[str, Any], ...] = ()

    @property
    def desks(self) -> Tuple[Desk, ...]:
        return tuple(d for c in self.cells for d in c.desks)

    @property
    def seats(self) -> Tuple[Seat, ...]:
        return tuple(d.seat for d in self.desks)

    @property
    def capacity(self) -> int:
        return len(self.desks)

    @property
    def headcount(self) -> int:
        return sum(1 for s in self.seats if s.occupied_by)

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def nodes(self) -> Tuple[str, ...]:
        return tuple([room_node(self.room_id)]
                     + [cell_node(self.room_id, c.cell_id) for c in self.cells])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "name": self.name,
            "wing_id": self.wing_id,
            "kind": self.kind,
            "archetype": self.archetype,
            "business_id": self.business_id,
            "legacyFloorId": self.legacy_floor_id,
            "characterMode": self.character_mode.value,
            "priority": int(self.priority),
            "state": self.state.value,
            "tier": int(self.tier),
            "capacity": self.capacity,
            "headcount": self.headcount,
            "cell_count": self.cell_count,
            "style": dict(self.style) if self.style else None,
            "cells": [c.to_dict() for c in self.cells],
            "pending_events": [dict(e) for e in self.pending_events],
            "approvals": [dict(a) for a in self.approvals],
            "schedules": [dict(s) for s in self.schedules],
        }


@dataclass(frozen=True)
class Wing:
    """A horizontal zone. The three legacy floors map onto three wings."""

    wing_id: str
    name: str
    index: int
    legacy_floor_id: int
    rooms: Tuple[LogicalRoom, ...] = ()
    z: int = 0

    @property
    def headcount(self) -> int:
        return sum(r.headcount for r in self.rooms)

    @property
    def capacity(self) -> int:
        return sum(r.capacity for r in self.rooms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wing_id": self.wing_id,
            "name": self.name,
            "index": int(self.index),
            "legacyFloorId": int(self.legacy_floor_id),
            "z": int(self.z),
            "capacity": self.capacity,
            "headcount": self.headcount,
            "rooms": [r.to_dict() for r in self.rooms],
        }


@dataclass(frozen=True)
class Business:
    """An authoritative business/department record. Always present."""

    business_id: str
    name: str
    wing_id: str
    department_id: Optional[str] = None
    room_ids: Tuple[str, ...] = ()
    headcount: int = 0
    priority: int = 0
    deadlines: Tuple[float, ...] = ()
    schedules: Tuple[Mapping[str, Any], ...] = ()
    state: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "business_id": self.business_id,
            "name": self.name,
            "wing_id": self.wing_id,
            "department_id": self.department_id,
            "room_ids": list(self.room_ids),
            "headcount": int(self.headcount),
            "priority": int(self.priority),
            "deadlines": [float(d) for d in self.deadlines],
            "schedules": [dict(s) for s in self.schedules],
            "state": self.state,
        }


@dataclass(frozen=True)
class Campus:
    """The whole world. Business data is independent of visibility."""

    campus_id: str
    name: str
    wings: Tuple[Wing, ...]
    businesses: Tuple[Business, ...]
    path_graph: PathGraph = field(default_factory=PathGraph)
    character_mode: CharacterMode = CharacterMode.MULTI

    def wing(self, wing_id: str) -> Wing:
        for w in self.wings:
            if w.wing_id == wing_id:
                return w
        raise KeyError(wing_id)

    def room(self, room_id: str) -> LogicalRoom:
        for w in self.wings:
            for r in w.rooms:
                if r.room_id == room_id:
                    return r
        raise KeyError(room_id)

    def business(self, business_id: str) -> Business:
        for b in self.businesses:
            if b.business_id == business_id:
                return b
        raise KeyError(business_id)

    def rooms(self) -> Tuple[LogicalRoom, ...]:
        return tuple(r for w in self.wings for r in w.rooms)

    def seats(self) -> Tuple[Seat, ...]:
        return tuple(s for r in self.rooms() for s in r.seats)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "campus_id": self.campus_id,
            "name": self.name,
            "characterMode": self.character_mode.value,
            "wings": [w.to_dict() for w in self.wings],
            "businesses": [b.to_dict() for b in self.businesses],
            "path_graph": self.path_graph.to_dict(),
        }


# ------------------------------------------------------------------ builders
def cell_origins(count: int) -> Tuple[Tile, ...]:
    """Deterministic cluster layout: two columns, two-tile gap."""

    if count < 0:
        raise WorldModelError([f"cell count must be >= 0, got {count}"])
    out: List[Tile] = []
    for i in range(count):
        col, row = i % CLUSTER_COLS, i // CLUSTER_COLS
        out.append(Tile(
            col * (CELL_COLS + CLUSTER_GAP),
            row * (CELL_ROWS + CLUSTER_GAP),
        ))
    return tuple(out)


def _distribute(desks: int, cells: int) -> List[int]:
    base, extra = divmod(desks, cells)
    return [base + (1 if i < extra else 0) for i in range(cells)]


def plan_cell_cluster(
    room_id: str,
    desks: int,
    *,
    kind: str = "work",
) -> Tuple[Cell, ...]:
    """Plan a room's cells for an exact desk count.

    Cells are never stretched. ``ceil(desks / 6)`` cells are created and the
    desks are distributed as evenly as possible, so 9 desks use two cells
    (5+4), 12 use two (6+6), 18 use three (6+6+6) and 24 use four (6+6+6+6).
    """

    if desks <= 0:
        raise WorldModelError([f"room {room_id!r} needs at least one desk"])
    if desks > CELL_DESK_CAPACITY * 64:
        raise WorldModelError([
            f"room {room_id!r} asks for {desks} desks; cap is "
            f"{CELL_DESK_CAPACITY * 64}"
        ])
    cell_count = math.ceil(desks / CELL_DESK_CAPACITY)
    origins = cell_origins(cell_count)
    counts = _distribute(desks, cell_count)
    cells: List[Cell] = []
    local_seat = 0
    for index, (origin, count) in enumerate(zip(origins, counts)):
        cell_id = f"c{index}"
        cell_desks: List[Desk] = []
        for slot in range(count):
            local_desk_id = f"d{slot}"
            local_seat_id = f"s{local_seat}"
            local_seat += 1
            sx, sy = CELL_DESK_SLOTS[slot]
            # Desk tiles are CELL-LOCAL; ``Cell.origin`` positions the cell in
            # the room grid. Keeping them local is what makes every cell a
            # standard 16x12 primitive and lets a cluster be laid out freely.
            desk_tile = Tile(sx, sy)
            sid = seat_id(room_id, cell_id, local_seat_id)
            seat = Seat(
                seat_id=sid,
                desk_id=f"{room_id}:{cell_id}:{local_desk_id}",
                room_id=room_id,
                cell_id=cell_id,
                local_seat_id=local_seat_id,
                tile=desk_tile,
                kind=kind,
            )
            cell_desks.append(Desk(
                desk_id=seat.desk_id,
                room_id=room_id,
                cell_id=cell_id,
                local_desk_id=local_desk_id,
                tile=desk_tile,
                seat=seat,
            ))
        cells.append(Cell(
            cell_id=cell_id,
            index=index,
            origin=origin,
            desks=tuple(cell_desks),
        ))
    return tuple(cells)


def build_room(
    room_id: str,
    name: str,
    wing_id: str,
    desks: int,
    *,
    business_id: str,
    archetype: str = "open_office",
    kind: str = "work",
    legacy_floor_id: Optional[int] = 0,
    character_mode: CharacterMode = CharacterMode.MULTI,
    style: Optional[Mapping[str, Any]] = None,
    priority: int = 0,
) -> LogicalRoom:
    """Build one room, using :func:`plan_cell_cluster` for its cells."""

    return LogicalRoom(
        room_id=room_id,
        name=name,
        wing_id=wing_id,
        kind=kind,
        archetype=archetype,
        business_id=business_id,
        cells=plan_cell_cluster(room_id, desks, kind=kind),
        legacy_floor_id=legacy_floor_id,
        character_mode=character_mode,
        style=style,
        priority=priority,
    )


def assemble_campus(
    campus_id: str,
    name: str,
    wings: Sequence[Wing],
    businesses: Sequence[Business],
    *,
    character_mode: CharacterMode = CharacterMode.MULTI,
) -> Campus:
    """Assemble a campus and derive the hierarchical path/portal graph.

    Every campus→wing, wing→room, room→cell edge is added. Portals between
    neighbouring cells across a room boundary are *not* invented; a caller that
    needs a door between two rooms passes its own :class:`PathGraph`.
    """

    portals: List[Portal] = []
    root = campus_node(campus_id)
    for wing in wings:
        wn = wing_node(wing.wing_id)
        portals.append(Portal(f"p:{root}->{wn}", root, wn, kind="opening"))
        for room in wing.rooms:
            rn = room_node(room.room_id)
            portals.append(Portal(f"p:{wn}->{rn}", wn, rn, kind="door"))
            for cell in room.cells:
                cn = cell_node(room.room_id, cell.cell_id)
                portals.append(Portal(
                    f"p:{rn}->{cn}", rn, cn, kind="opening"))
    return Campus(
        campus_id=campus_id,
        name=name,
        wings=tuple(wings),
        businesses=tuple(businesses),
        path_graph=PathGraph(tuple(portals)),
        character_mode=character_mode,
    )


# ---------------------------------------------------------------- validation
def _in_cell(cell: Cell, tile: Tile, width: int = 1, height: int = 1) -> bool:
    return (0 <= tile.x and 0 <= tile.y
            and tile.x + width <= cell.cols
            and tile.y + height <= cell.rows)


def _overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def validate_campus(campus: Campus, *, connectivity: bool = True) -> List[str]:
    """Return every structural defect, or an empty list.

    This is the only place the invariants are enforced; each check names the
    object it failed on so a caller can fix the data rather than guess.
    """

    problems: List[str] = []
    wing_ids: set[str] = set()
    room_ids: set[str] = set()
    business_ids: set[str] = set()
    seat_ids: set[str] = set()
    room_by_id: Dict[str, LogicalRoom] = {}

    for b in campus.businesses:
        if not b.business_id:
            problems.append("business: empty id")
        if b.business_id in business_ids:
            problems.append(f"business {b.business_id!r}: duplicate id")
        business_ids.add(b.business_id)
        if not b.wing_id:
            problems.append(f"business {b.business_id!r}: empty wing_id")

    for wing in campus.wings:
        if not wing.wing_id:
            problems.append("wing: empty id")
        if wing.wing_id in wing_ids:
            problems.append(f"wing {wing.wing_id!r}: duplicate id")
        wing_ids.add(wing.wing_id)
        if not isinstance(wing.legacy_floor_id, int):
            problems.append(
                f"wing {wing.wing_id!r}: legacyFloorId must be an int")
        if wing.z != wing.index * WING_HEIGHT_UNITS:
            problems.append(
                f"wing {wing.wing_id!r}: z {wing.z} != index*{WING_HEIGHT_UNITS}")
        for room in wing.rooms:
            room_by_id[room.room_id] = room
            if room.wing_id != wing.wing_id:
                problems.append(
                    f"room {room.room_id!r}: wing_id {room.wing_id!r} "
                    f"!= containing wing {wing.wing_id!r}")
            if room.legacy_floor_id != wing.legacy_floor_id:
                problems.append(
                    f"room {room.room_id!r}: legacyFloorId "
                    f"{room.legacy_floor_id!r} != wing "
                    f"{wing.legacy_floor_id!r}")
            if room.room_id in room_ids:
                problems.append(f"room {room.room_id!r}: duplicate id")
            room_ids.add(room.room_id)
            if not isinstance(room.character_mode, CharacterMode):
                problems.append(
                    f"room {room.room_id!r}: invalid characterMode "
                    f"{room.character_mode!r}")
            if not room.cells:
                problems.append(f"room {room.room_id!r}: no cells")
            if room.business_id not in business_ids:
                problems.append(
                    f"room {room.room_id!r}: business {room.business_id!r} "
                    "has no authoritative record")
            cell_rects: List[Tuple[int, int, int, int]] = []
            for cell in room.cells:
                where = f"room {room.room_id!r} cell {cell.cell_id!r}"
                if not cell.is_standard:
                    problems.append(
                        f"{where}: stretched cell {cell.cols}x{cell.rows}; "
                        f"cells are fixed {CELL_COLS}x{CELL_ROWS}")
                if cell.origin.x < 0 or cell.origin.y < 0:
                    problems.append(f"{where}: negative origin")
                rect = (cell.origin.x, cell.origin.y, cell.cols, cell.rows)
                if any(_overlap(rect, other) for other in cell_rects):
                    problems.append(f"{where}: overlaps another cell")
                cell_rects.append(rect)
                if len(cell.desks) > CELL_DESK_CAPACITY:
                    problems.append(
                        f"{where}: {len(cell.desks)} desks exceeds "
                        f"{CELL_DESK_CAPACITY}/cell")
                desk_rects: List[Tuple[int, int, int, int]] = []
                for desk in cell.desks:
                    dw = f"{where} desk {desk.local_desk_id!r}"
                    if desk.room_id != room.room_id or desk.cell_id != cell.cell_id:
                        problems.append(f"{dw}: wrong room/cell namespace")
                    local = desk.seat.local_seat_id
                    expected = seat_id(room.room_id, cell.cell_id, local)
                    if desk.seat.seat_id != expected:
                        problems.append(
                            f"{dw}: seat id {desk.seat.seat_id!r} != {expected!r}")
                    if not _SEAT_ID_RE.match(desk.seat.seat_id):
                        problems.append(f"{dw}: malformed namespaced seat id")
                    if desk.seat.seat_id in seat_ids:
                        problems.append(
                            f"{dw}: duplicate seat id {desk.seat.seat_id!r}")
                    seat_ids.add(desk.seat.seat_id)
                    if not _in_cell(cell, desk.tile, desk.width, desk.height):
                        problems.append(
                            f"{dw}: desk outside 16x12 cell at "
                            f"({desk.tile.x},{desk.tile.y})")
                    drect = (desk.tile.x, desk.tile.y, desk.width, desk.height)
                    if any(_overlap(drect, other) for other in desk_rects):
                        problems.append(f"{dw}: desk overlaps another desk")
                    desk_rects.append(drect)
            if room.capacity != len(room.seats):
                problems.append(
                    f"room {room.room_id!r}: capacity {room.capacity} "
                    "does not match seats")

    # businesses reference rooms that exist, and the room references them back.
    for b in campus.businesses:
        for rid in b.room_ids:
            room = room_by_id.get(rid)
            if room is None:
                problems.append(
                    f"business {b.business_id!r}: unknown room {rid!r}")
                continue
            if room.business_id != b.business_id:
                problems.append(
                    f"business {b.business_id!r}: room {rid!r} belongs to "
                    f"{room.business_id!r}")
        if b.wing_id not in wing_ids:
            problems.append(
                f"business {b.business_id!r}: unknown wing {b.wing_id!r}")

    # Hierarchical path/portal model.
    expected_nodes: set[str] = {campus_node(campus.campus_id)}
    expected_nodes.update(wing_node(w.wing_id) for w in campus.wings)
    for room in room_by_id.values():
        expected_nodes.add(room_node(room.room_id))
        expected_nodes.update(cell_node(room.room_id, c.cell_id)
                              for c in room.cells)
    graph_nodes = campus.path_graph.nodes()
    for node in sorted(graph_nodes - expected_nodes):
        problems.append(f"path_graph: portal references unknown node {node!r}")
    if connectivity and campus.wings:
        root = campus_node(campus.campus_id)
        for node in sorted(expected_nodes):
            if node not in graph_nodes:
                problems.append(
                    f"path_graph: node {node!r} has no portal edge")
            elif node != root and campus.path_graph.path(root, node) is None:
                problems.append(
                    f"path_graph: node {node!r} is unreachable from {root!r}")
    return problems


def assert_valid_campus(campus: Campus, *, connectivity: bool = True) -> None:
    problems = validate_campus(campus, connectivity=connectivity)
    if problems:
        raise WorldModelError(problems)
