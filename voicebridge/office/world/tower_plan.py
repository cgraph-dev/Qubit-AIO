"""Deterministic world projection over the shipped templates.

Master plan §32–§40 asks for a navigable tower, not a drawing of one:

* **Five floors, five wings, active/dormant split from the data.** The floor/wing
  templates in :mod:`voicebridge.business.role_catalog` are the source of
  truth; nothing here invents a floor, a wing or an "active" state.
* **Rooms are cell clusters, never stretched cells.** Every room is laid out
  through :func:`voicebridge.office.world.model.plan_cell_cluster`, so a room
  with 9/12/18/24+ desks gets more 16x12 cells — never a wider 16x12 cell.
* **Stable, globally unique ids.** ``room_id`` comes from the floor template;
  ``cell_id`` is ``<room_id>:c<index>``; ``desk_id``/``seat_id`` extend that
  with the local slot. Two builds produce byte-identical ids and digest.
* **No fake agents.** A room exposes ``agent_slots`` (the desk count it could
  staff) and an explicit ``staffed_agents: 0``; it never lists an occupant.
  Only a real Business OS record may materialise an agent, which is outside
  this pure projection.
* **No hard caps.** ``max_rooms``/``max_agents`` are carried through as
  ``None`` (unbounded), exactly as the registry declares them.

The module is pure Python: no I/O, no store, no clock, no network, no model
call, no mutation. It reads the in-memory registry and returns a projection.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .model import (
    CELL_COLS,
    CELL_DESK_CAPACITY,
    CELL_ROWS,
    plan_cell_cluster,
)

__all__ = [
    "SCHEMA",
    "TowerPlanError",
    "UnknownFloorError",
    "UnknownWingError",
    "ConflictingFilterError",
    "DeskPlan",
    "CellPlan",
    "RoomPlan",
    "FloorPlan",
    "WingPlan",
    "TowerPlan",
    "build_tower_plan",
    "tower_payload",
    "validate_tower_plan",
    "ARCHETYPE_BY_KIND",
]

SCHEMA = "qubit-office-world-tower/1"

#: Room kind -> style archetype. The template `kind` is a planning label; the
#: archetype is the renderer-facing one. Unknown kinds fall back to the open
#: office so a new template kind never breaks the projection.
ARCHETYPE_BY_KIND: Mapping[str, str] = {
    "work": "open_office",
    "war_room": "executive",
    "focus": "research",
    "ops": "control_room",
}

_DEFAULT_ARCHETYPE = "open_office"


class TowerPlanError(Exception):
    """A projection query or invariant failed. ``code``/``status`` are typed."""

    code = "OFFICE_WORLD_INVALID"
    status = 400

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class UnknownFloorError(TowerPlanError):
    code = "OFFICE_WORLD_UNKNOWN_FLOOR"
    status = 404


class UnknownWingError(TowerPlanError):
    code = "OFFICE_WORLD_UNKNOWN_WING"
    status = 404


class ConflictingFilterError(TowerPlanError):
    code = "OFFICE_WORLD_CONFLICTING_FILTER"
    status = 400


# --------------------------------------------------------------- dataclasses
@dataclass(frozen=True)
class DeskPlan:
    """One desk slot and the seat it serves. Never claims an occupant."""

    desk_id: str
    seat_id: str
    cell_id: str
    local_desk_id: str
    tile: Tuple[int, int]
    width: int = 2
    height: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "desk_id": self.desk_id,
            "seat_id": self.seat_id,
            "cell_id": self.cell_id,
            "local_desk_id": self.local_desk_id,
            "tile": {"x": int(self.tile[0]), "y": int(self.tile[1])},
            "footprint": {"w": int(self.width), "h": int(self.height)},
            # Honesty: a seat is a capability, not a person. Nothing is seated
            # until a Business OS record (outside this projection) claims it.
            "occupied": False,
        }


@dataclass(frozen=True)
class CellPlan:
    """A fixed 16x12 room primitive. ``desk_capacity`` is the slot maximum."""

    cell_id: str
    room_id: str
    index: int
    origin: Tuple[int, int]
    cols: int
    rows: int
    desks: Tuple[DeskPlan, ...]

    @property
    def desk_count(self) -> int:
        return len(self.desks)

    @property
    def desk_capacity(self) -> int:
        return CELL_DESK_CAPACITY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "room_id": self.room_id,
            "index": int(self.index),
            "origin": {"x": int(self.origin[0]), "y": int(self.origin[1])},
            "cols": int(self.cols),
            "rows": int(self.rows),
            "standard": (self.cols, self.rows) == (CELL_COLS, CELL_ROWS),
            "desk_count": self.desk_count,
            "desk_capacity": self.desk_capacity,
            "desks": [d.to_dict() for d in self.desks],
        }


@dataclass(frozen=True)
class RoomPlan:
    """A floor room: its archetype, role ids, cells and slots (not staff)."""

    room_id: str
    name: str
    kind: str
    archetype: str
    role_ids: Tuple[str, ...]
    active: bool
    cells: Tuple[CellPlan, ...]

    @property
    def desk_count(self) -> int:
        return sum(c.desk_count for c in self.cells)

    @property
    def desk_capacity(self) -> int:
        return len(self.cells) * CELL_DESK_CAPACITY

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def agent_slots(self) -> int:
        """Planned slots only. A slot is a capability, never an occupant."""
        return self.desk_count

    @property
    def staffed_agents(self) -> int:
        """Always zero: this projection never materialises an agent."""
        return 0

    def to_dict(self, *, detail: str = "full") -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "room_id": self.room_id,
            "name": self.name,
            "kind": self.kind,
            "archetype": self.archetype,
            "role_ids": list(self.role_ids),
            "active": bool(self.active),
            "cell_count": self.cell_count,
            "desk_count": self.desk_count,
            "desk_capacity": self.desk_capacity,
            # Capacity is not a roster: no agent is created or implied.
            "agent_slots": self.desk_count,
            "staffed_agents": 0,
        }
        if detail == "full":
            out["cells"] = [c.to_dict() for c in self.cells]
        return out


@dataclass(frozen=True)
class FloorPlan:
    """One storey: a wing, a lifecycle state, rooms and unbounded caps."""

    floor_id: str
    index: int
    wing: str
    name: str
    active: bool
    rooms: Tuple[RoomPlan, ...]
    max_rooms: Optional[int] = None
    max_agents: Optional[int] = None
    expansion_allowed: bool = True

    @property
    def room_count(self) -> int:
        return len(self.rooms)

    @property
    def desk_count(self) -> int:
        return sum(r.desk_count for r in self.rooms)

    @property
    def desk_capacity(self) -> int:
        return self.desk_count

    @property
    def cell_count(self) -> int:
        return sum(r.cell_count for r in self.rooms)

    def to_dict(self, *, detail: str = "summary") -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "floor_id": self.floor_id,
            "index": int(self.index),
            "wing": self.wing,
            "name": self.name,
            "active": bool(self.active),
            "state": "ACTIVE" if self.active else "DORMANT",
            "room_count": self.room_count,
            "desk_count": self.desk_count,
            "desk_capacity": self.desk_capacity,
            "cell_count": self.cell_count,
            "max_rooms": self.max_rooms,
            "max_agents": self.max_agents,
            "expansion_allowed": bool(self.expansion_allowed),
        }
        if detail in ("rooms", "full"):
            out["rooms"] = [r.to_dict(detail=detail) for r in self.rooms]
        return out


@dataclass(frozen=True)
class WingPlan:
    """A horizontal zone and the floors it contains."""

    wing_id: str
    name: str
    index: int
    floors: Tuple[FloorPlan, ...]

    @property
    def floor_count(self) -> int:
        return len(self.floors)

    @property
    def active_floor_count(self) -> int:
        return sum(1 for f in self.floors if f.active)

    @property
    def dormant_floor_count(self) -> int:
        return self.floor_count - self.active_floor_count

    @property
    def room_count(self) -> int:
        return sum(f.room_count for f in self.floors)

    @property
    def desk_count(self) -> int:
        return sum(f.desk_count for f in self.floors)

    @property
    def cell_count(self) -> int:
        return sum(f.cell_count for f in self.floors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wing_id": self.wing_id,
            "name": self.name,
            "index": int(self.index),
            "floor_count": self.floor_count,
            "active_floor_count": self.active_floor_count,
            "dormant_floor_count": self.dormant_floor_count,
            "room_count": self.room_count,
            "desk_count": self.desk_count,
            "cell_count": self.cell_count,
            "floor_ids": [f.floor_id for f in self.floors],
        }


@dataclass(frozen=True)
class TowerPlan:
    """The whole projection plus a stable digest of its full representation."""

    floors: Tuple[FloorPlan, ...]
    wings: Tuple[WingPlan, ...]
    digest: str

    @property
    def floor_count(self) -> int:
        return len(self.floors)

    @property
    def wing_count(self) -> int:
        return len(self.wings)

    @property
    def active_floor_count(self) -> int:
        return sum(1 for f in self.floors if f.active)

    @property
    def dormant_floor_count(self) -> int:
        return self.floor_count - self.active_floor_count

    @property
    def room_count(self) -> int:
        return sum(f.room_count for f in self.floors)

    @property
    def cell_count(self) -> int:
        return sum(f.cell_count for f in self.floors)

    @property
    def desk_count(self) -> int:
        return sum(f.desk_count for f in self.floors)

    def floor(self, floor_id: str) -> FloorPlan:
        for item in self.floors:
            if item.floor_id == floor_id:
                return item
        raise UnknownFloorError(f"unknown floor {floor_id!r}")

    def wing(self, wing_id: str) -> WingPlan:
        wanted = wing_id.strip().lower()
        for item in self.wings:
            if item.wing_id == wanted:
                return item
        raise UnknownWingError(f"unknown wing {wing_id!r}")

    def counts(self) -> Dict[str, int]:
        return {
            "floors": self.floor_count,
            "wings": self.wing_count,
            "active_floors": self.active_floor_count,
            "dormant_floors": self.dormant_floor_count,
            "rooms": self.room_count,
            "cells": self.cell_count,
            "desks": self.desk_count,
            "staffed_agents": 0,
        }

    def full_dict(self) -> Dict[str, Any]:
        return {
            "floors": [f.to_dict(detail="full") for f in self.floors],
            "wings": [w.to_dict() for w in self.wings],
            "counts": self.counts(),
        }


# ------------------------------------------------------------------ builders
def _archetype_for(kind: str) -> str:
    return ARCHETYPE_BY_KIND.get(str(kind or "").lower(), _DEFAULT_ARCHETYPE)


def _room_plan(room: Any, *, active: bool) -> RoomPlan:
    cells = plan_cell_cluster(room.room_id, int(room.desks), kind=room.kind)
    planned: List[CellPlan] = []
    for cell in cells:
        cell_id = f"{room.room_id}:{cell.cell_id}"
        desks: List[DeskPlan] = []
        for desk in cell.desks:
            desks.append(DeskPlan(
                desk_id=f"{cell_id}:{desk.local_desk_id}",
                seat_id=f"{cell_id}:{desk.seat.local_seat_id}",
                cell_id=cell_id,
                local_desk_id=desk.local_desk_id,
                tile=(desk.tile.x, desk.tile.y),
                width=int(desk.width),
                height=int(desk.height),
            ))
        planned.append(CellPlan(
            cell_id=cell_id,
            room_id=room.room_id,
            index=int(cell.index),
            origin=(cell.origin.x, cell.origin.y),
            cols=int(cell.cols),
            rows=int(cell.rows),
            desks=tuple(desks),
        ))
    return RoomPlan(
        room_id=room.room_id,
        name=room.name,
        kind=room.kind,
        archetype=_archetype_for(room.kind),
        role_ids=tuple(room.role_ids),
        active=bool(active),
        cells=tuple(planned),
    )


def build_tower_plan(registry: Any = None) -> TowerPlan:
    """Project the shipped floor templates into a deterministic tower.

    ``registry`` is a :class:`voicebridge.business.roles.RoleRegistry`; when
    omitted the shipped catalog is built. No store, clock, network or model is
    touched.
    """

    if registry is None:
        from ...business.roles import RoleRegistry

        registry = RoleRegistry.product()

    floors: List[FloorPlan] = []
    for template in sorted(registry.floors, key=lambda item: item.index):
        active = bool(template.active_by_default)
        rooms = tuple(_room_plan(room, active=active) for room in template.rooms)
        floors.append(FloorPlan(
            floor_id=template.floor_id,
            index=int(template.index),
            wing=str(template.wing),
            name=template.name,
            active=active,
            rooms=rooms,
            max_rooms=template.max_rooms,
            max_agents=template.max_agents,
            expansion_allowed=bool(template.expansion_allowed),
        ))

    wing_order: List[str] = []
    wing_floors: Dict[str, List[FloorPlan]] = {}
    for floor in floors:
        if floor.wing not in wing_floors:
            wing_order.append(floor.wing)
            wing_floors[floor.wing] = []
        wing_floors[floor.wing].append(floor)

    wings = tuple(
        WingPlan(
            wing_id=wing_id,
            name=f"{wing_id.title()} Wing",
            index=index,
            floors=tuple(wing_floors[wing_id]),
        )
        for index, wing_id in enumerate(wing_order)
    )

    plan = TowerPlan(floors=tuple(floors), wings=wings, digest="")
    digest = hashlib.sha256(
        json.dumps(plan.full_dict(), sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return TowerPlan(floors=plan.floors, wings=plan.wings, digest=digest)


# ------------------------------------------------------------------- payload
def _floor_ref(value: str) -> str:
    clean = value.strip()
    if clean.isdigit():
        return f"floor-{int(clean):03d}"
    return clean


def _floors_dict(floors: Sequence[FloorPlan], detail: str) -> List[Dict[str, Any]]:
    return [floor.to_dict(detail=detail) for floor in floors]


def _subset_counts(floors: Sequence[FloorPlan]) -> Dict[str, int]:
    return {
        "floors": len(floors),
        "rooms": sum(f.room_count for f in floors),
        "cells": sum(f.cell_count for f in floors),
        "desks": sum(f.desk_count for f in floors),
        "active_floors": sum(1 for f in floors if f.active),
        "dormant_floors": sum(1 for f in floors if not f.active),
        "staffed_agents": 0,
    }


def tower_payload(
    plan: TowerPlan,
    *,
    floor: Optional[str] = None,
    wing: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the bounded JSON payload for a tower read.

    Default is a floor summary (no rooms/cells/desks); ``floor=`` returns that
    one floor in full; ``wing=`` returns the wing's floors with room summaries.
    Every form carries the same full-projection ``digest``.
    """

    floor_value = (floor or "").strip() or None
    wing_value = (wing or "").strip() or None
    if floor_value is not None and wing_value is not None:
        raise ConflictingFilterError(
            "choose either ?floor= or ?wing=, not both")

    if floor_value is not None:
        wanted = _floor_ref(floor_value)
        selected = (plan.floor(wanted),)
        detail = "full"
    elif wing_value is not None:
        selected = plan.wing(wing_value).floors
        detail = "rooms"
    else:
        selected = plan.floors
        detail = "summary"

    result: Dict[str, Any] = {
        "ok": True,
        "schema": SCHEMA,
        "digest": plan.digest,
        "filter": {"floor": floor_value, "wing": wing_value},
        "detail": detail,
        "tower": plan.counts(),
        "counts": _subset_counts(selected),
        "wings": [w.to_dict() for w in plan.wings],
        "floors": _floors_dict(selected, detail),
    }
    return result


# ---------------------------------------------------------------- validation
def validate_tower_plan(plan: TowerPlan) -> List[str]:
    """Return every structural defect, or ``[]``. Pure and deterministic."""

    problems: List[str] = []
    if plan.digest != build_tower_plan_digest(plan):
        problems.append("digest: does not match the full projection")

    if plan.floor_count < 1:
        problems.append(f"tower: expected at least one floor, got {plan.floor_count}")
    if plan.wing_count < 1:
        problems.append(f"tower: expected at least one wing, got {plan.wing_count}")

    floor_ids: set[str] = set()
    room_ids: set[str] = set()
    cell_ids: set[str] = set()
    desk_ids: set[str] = set()
    seat_ids: set[str] = set()

    for floor in plan.floors:
        if floor.floor_id in floor_ids:
            problems.append(f"floor {floor.floor_id!r}: duplicate id")
        floor_ids.add(floor.floor_id)
        if floor.max_rooms is not None:
            problems.append(
                f"floor {floor.floor_id!r}: max_rooms cap {floor.max_rooms}"
                f" violates the no-hard-cap rule")
        if floor.max_agents is not None:
            problems.append(
                f"floor {floor.floor_id!r}: max_agents cap {floor.max_agents}"
                f" violates the no-hard-cap rule")
        if floor.wing not in {w.wing_id for w in plan.wings}:
            problems.append(
                f"floor {floor.floor_id!r}: wing {floor.wing!r} is not declared")
        expected_rooms = len(floor.rooms)
        if floor.room_count != expected_rooms:
            problems.append(
                f"floor {floor.floor_id!r}: room_count {floor.room_count}"
                f" != {expected_rooms}")
        if floor.desk_count != sum(r.desk_count for r in floor.rooms):
            problems.append(
                f"floor {floor.floor_id!r}: desk_count does not sum its rooms")
        if floor.desk_capacity != floor.desk_count:
            problems.append(
                f"floor {floor.floor_id!r}: desk_capacity"
                f" {floor.desk_capacity} != desk_count {floor.desk_count}")

        for room in floor.rooms:
            if room.room_id in room_ids:
                problems.append(f"room {room.room_id!r}: duplicate id")
            room_ids.add(room.room_id)
            if room.staffed_agents != 0:
                problems.append(
                    f"room {room.room_id!r}: claims {room.staffed_agents}"
                    f" staffed agents")
            if room.agent_slots != room.desk_count:
                problems.append(
                    f"room {room.room_id!r}: agent_slots"
                    f" {room.agent_slots} != desk_count {room.desk_count}")
            expected_cells = -(-room.desk_count // CELL_DESK_CAPACITY)
            if room.cell_count != expected_cells:
                problems.append(
                    f"room {room.room_id!r}: {room.cell_count} cells for"
                    f" {room.desk_count} desks (expected {expected_cells})")
            if room.desk_capacity != room.cell_count * CELL_DESK_CAPACITY:
                problems.append(
                    f"room {room.room_id!r}: desk_capacity"
                    f" {room.desk_capacity} != cells*{CELL_DESK_CAPACITY}")

            for cell in room.cells:
                if cell.cell_id in cell_ids:
                    problems.append(f"cell {cell.cell_id!r}: duplicate id")
                cell_ids.add(cell.cell_id)
                if (cell.cols, cell.rows) != (CELL_COLS, CELL_ROWS):
                    problems.append(
                        f"cell {cell.cell_id!r}: {cell.cols}x{cell.rows} is not"
                        f" the {CELL_COLS}x{CELL_ROWS} contract")
                if not 1 <= cell.desk_count <= CELL_DESK_CAPACITY:
                    problems.append(
                        f"cell {cell.cell_id!r}: desk_count {cell.desk_count}"
                        f" outside 1..{CELL_DESK_CAPACITY}")
                for desk in cell.desks:
                    if desk.desk_id in desk_ids:
                        problems.append(
                            f"desk {desk.desk_id!r}: duplicate id")
                    desk_ids.add(desk.desk_id)
                    if desk.seat_id in seat_ids:
                        problems.append(
                            f"seat {desk.seat_id!r}: duplicate id")
                    seat_ids.add(desk.seat_id)
                    x, y = desk.tile
                    if not (0 <= x and 0 <= y
                            and x + desk.width <= CELL_COLS
                            and y + desk.height <= CELL_ROWS):
                        problems.append(
                            f"desk {desk.desk_id!r}: tile {desk.tile} does not"
                            f" fit the {CELL_COLS}x{CELL_ROWS} cell")

    for wing in plan.wings:
        if wing.floor_count != len(wing.floors):
            problems.append(
                f"wing {wing.wing_id!r}: floor_count does not sum its floors")
        if wing.active_floor_count + wing.dormant_floor_count != wing.floor_count:
            problems.append(
                f"wing {wing.wing_id!r}: active/dormant split is inconsistent")

    if sum(w.floor_count for w in plan.wings) != plan.floor_count:
        problems.append("tower: wings do not contain every floor exactly once")

    return problems


def build_tower_plan_digest(plan: TowerPlan) -> str:
    """Recompute the canonical digest for ``plan`` (used by validation)."""

    return hashlib.sha256(
        json.dumps(plan.full_dict(), sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()
