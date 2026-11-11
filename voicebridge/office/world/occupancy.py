"""Explicit agent occupancy for the 100-floor world tower.

The tower projection (:mod:`voicebridge.office.world.tower_plan`) is a plan: it
knows floors, rooms, 16x12 cells and desk slots, and it deliberately claims no
occupant. This module is the one place that may turn a real Business OS agent
into an occupant, and the rule is **explicit match or nothing**:

* ``metadata.world_desk`` / ``metadata.world_room`` / ``metadata.world_floor``
  (also under a nested ``metadata.world`` mapping) name a real tower
  coordinate. A desk id or seat id must exist; a room must resolve to exactly
  one room, optionally narrowed by an explicit floor.
* ``metadata.office_seat_slug`` / ``metadata.seat_slug`` / ``metadata.office_slug``
  identify a seat in the office plan. The seat's own explicit ``world_*`` keys
  are honoured; otherwise its ``room`` is matched against the tower only when
  that name is unique, and its ``division`` is used only when it names a tower
  department on exactly one floor (or one floor at the seat's own index).
* With no explicit coordinate, an agent's ``department`` is used only when the
  tower has exactly one floor carrying that department. A department that
  repeats across the tower (the shipped catalog repeats each department on five
  floors) is **ambiguous** and the agent stays unplaced.

Everything else is an unplaced agent with a typed reason. No floor, room, desk
or agent is invented, a desk is never assigned twice, and a dormant agent never
occupies a desk. The projection is pure: no store, clock, network or model.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import plan_cell_cluster

SCHEMA = "qubit-office-world-occupancy/1"

#: A bounded response default. The full projection stays in memory; the wire
#: payload caps both lists so a 1,000-agent tower cannot grow without bound.
MAX_LIST = 250

DORMANT_STATUSES = frozenset({
    "paused", "stopped", "revoked", "archived", "inactive", "dormant",
    "disabled", "offline",
})

SOURCE_WORLD_DESK = "metadata.world_desk"
SOURCE_WORLD_ROOM = "metadata.world_room"
SOURCE_WORLD_FLOOR = "metadata.world_floor"
SOURCE_OFFICE_SEAT = "office_seat_slug"
SOURCE_DEPARTMENT = "department"
SOURCE_FLOOR = "floor"

REASON_DORMANT = "dormant"
REASON_NO_MATCH = "no_explicit_match"
REASON_UNKNOWN_FLOOR = "unknown_floor"
REASON_UNKNOWN_ROOM = "unknown_room"
REASON_UNKNOWN_DESK = "unknown_desk"
REASON_AMBIGUOUS_ROOM = "ambiguous_room"
REASON_AMBIGUOUS_FLOOR = "ambiguous_floor"
REASON_AMBIGUOUS_DEPARTMENT = "ambiguous_department"
REASON_UNKNOWN_DEPARTMENT = "unknown_department"
REASON_DESK_TAKEN = "desk_taken"
REASON_DESK_MISMATCH = "desk_mismatch"
REASON_ROOM_FULL = "room_full"
REASON_SEAT_NOT_FOUND = "seat_not_found"
REASON_SEAT_NOT_IN_WORLD = "seat_not_in_world"


class OccupancyError(Exception):
    """A projection invariant failed. Typed so an HTTP reader can answer."""

    code = "OFFICE_WORLD_OCCUPANCY_INVALID"
    status = 400

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


# --------------------------------------------------------------------- desks
@dataclass(frozen=True)
class DeskRef:
    """One real desk slot in the shipped plan, with its enclosing coordinates."""

    floor_id: str
    floor_index: int
    floor_name: str
    floor_active: bool
    department_ids: Tuple[str, ...]
    room_id: str
    room_name: str
    room_kind: str
    cell_id: str
    desk_id: str
    seat_id: str
    tile: Tuple[int, int]


@dataclass(frozen=True)
class Placement:
    """An agent explicitly placed on one real desk."""

    agent_id: str
    name: str
    initials: str
    status: str
    emoji: str
    color: str
    desk: DeskRef
    source: str
    detail: str

    def occupant(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "initials": self.initials,
            "status": self.status,
            "emoji": self.emoji,
            "color": self.color,
            "source": self.source,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.occupant(),
            "floor_id": self.desk.floor_id,
            "floor_index": int(self.desk.floor_index),
            "room_id": self.desk.room_id,
            "room_name": self.desk.room_name,
            "desk_id": self.desk.desk_id,
            "seat_id": self.desk.seat_id,
            "placement_source": self.source,
            "placement_detail": self.detail,
        }


@dataclass(frozen=True)
class Unplaced:
    """An agent that has no explicit tower coordinate."""

    agent_id: str
    name: str
    status: str
    reason: str
    source: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
        }
        if self.source:
            out["placement_source"] = self.source
        if self.detail:
            out["placement_detail"] = self.detail
        return out


@dataclass(frozen=True)
class OccupancyProjection:
    placements: Tuple[Placement, ...]
    unplaced: Tuple[Unplaced, ...]
    ignored: int
    digest: str

    def counts(self) -> Dict[str, int]:
        return {
            "agents": len(self.placements) + len(self.unplaced) + self.ignored,
            "placed": len(self.placements),
            "unplaced": len(self.unplaced),
            "ignored": self.ignored,
            "occupied_desks": len(self.placements),
            "floors_staffed": len({p.desk.floor_id for p in self.placements}),
            "rooms_staffed": len({p.desk.room_id for p in self.placements}),
        }

    def by_desk(self) -> Dict[str, Dict[str, Any]]:
        return {p.desk.desk_id: p.occupant() for p in self.placements}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "placements": [p.to_dict() for p in self.placements],
            "unplaced": [u.to_dict() for u in self.unplaced],
            "ignored": int(self.ignored),
            "digest": self.digest,
        }


# ---------------------------------------------------------------- tower index
def _floor_active(floor: Any) -> bool:
    if hasattr(floor, "active_by_default"):
        return bool(floor.active_by_default)
    return bool(getattr(floor, "active", False))


def _desks(floors: Sequence[Any]) -> List[DeskRef]:
    out: List[DeskRef] = []
    ordered = sorted(floors, key=lambda f: int(getattr(f, "index", 0)))
    for floor in ordered:
        department_ids = tuple(
            str(item) for item in (getattr(floor, "department_ids", ()) or ()))
        rooms = sorted(floor.rooms, key=lambda r: str(r.room_id))
        for room in rooms:
            for cell in plan_cell_cluster(
                    str(room.room_id), int(room.desks),
                    kind=str(getattr(room, "kind", "work"))):
                cell_id = f"{room.room_id}:{cell.cell_id}"
                for desk in cell.desks:
                    out.append(DeskRef(
                        floor_id=str(floor.floor_id),
                        floor_index=int(floor.index),
                        floor_name=str(getattr(floor, "name", "")),
                        floor_active=_floor_active(floor),
                        department_ids=department_ids,
                        room_id=str(room.room_id),
                        room_name=str(getattr(room, "name", "")),
                        room_kind=str(getattr(room, "kind", "work")),
                        cell_id=cell_id,
                        desk_id=str(desk.desk_id),
                        seat_id=str(desk.seat.seat_id),
                        tile=(int(desk.tile.x), int(desk.tile.y)),
                    ))
    return out


class _Index:
    """Read-only lookups over the shipped desks. Built once per projection."""

    def __init__(self, floors: Sequence[Any]):
        self.floors: Tuple[Any, ...] = tuple(
            sorted(floors, key=lambda f: int(getattr(f, "index", 0))))
        self.desks: Tuple[DeskRef, ...] = tuple(_desks(self.floors))
        self._by_id: Dict[str, DeskRef] = {}
        self._by_seat: Dict[str, DeskRef] = {}
        self._by_room: Dict[str, List[DeskRef]] = {}
        self._room_names: Dict[str, List[DeskRef]] = {}
        self._rooms_by_id: Dict[str, Any] = {}
        self._rooms_by_name: Dict[str, List[Any]] = {}
        self._floors_by_id: Dict[str, Any] = {}
        self._floors_by_index: Dict[int, Any] = {}
        for floor in self.floors:
            self._floors_by_id[str(floor.floor_id)] = floor
            self._floors_by_index[int(floor.index)] = floor
            for room in floor.rooms:
                self._rooms_by_id[str(room.room_id)] = room
                self._rooms_by_name.setdefault(
                    str(getattr(room, "name", "")).strip().casefold(),
                    []).append(room)
        for desk in self.desks:
            self._by_id.setdefault(desk.desk_id, desk)
            self._by_seat.setdefault(desk.seat_id, desk)
            self._by_room.setdefault(desk.room_id, []).append(desk)
            self._room_names.setdefault(
                desk.room_name.strip().casefold(), []).append(desk)

    # -- floors -------------------------------------------------------
    def floor(self, value: Any) -> Optional[Any]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return self._floors_by_index.get(value)
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            return self._floors_by_index.get(int(text))
        if text.startswith("floor-") and text[6:].isdigit():
            return self._floors_by_index.get(int(text[6:]))
        return self._floors_by_id.get(text)

    def floor_by_id(self, floor_id: str) -> Optional[Any]:
        return self._floors_by_id.get(floor_id)

    # -- rooms --------------------------------------------------------
    def room(self, value: Any, floor: Any = None) -> Tuple[Optional[Any], str]:
        if value is None or isinstance(value, bool):
            return None, REASON_UNKNOWN_ROOM
        text = str(value).strip()
        if not text:
            return None, REASON_UNKNOWN_ROOM
        matches: List[Any] = []
        direct = self._rooms_by_id.get(text)
        if direct is not None:
            matches = [direct]
        else:
            matches = list(self._rooms_by_name.get(text.casefold(), ()))
        if floor is not None:
            matches = [
                room for room in matches
                if self._room_floor_id(room) == str(floor.floor_id)]
        if not matches:
            return None, REASON_UNKNOWN_ROOM
        if len(matches) > 1:
            return None, REASON_AMBIGUOUS_ROOM
        return matches[0], ""

    def _room_floor_id(self, room: Any) -> str:
        for floor in self.floors:
            for candidate in floor.rooms:
                if candidate is room:
                    return str(floor.floor_id)
        return ""

    def room_has_free_desk(self, room: Any, occupied: Mapping[str, Any]) -> bool:
        return bool(self.free_desks(str(room.room_id), occupied))

    def free_desks(self, room_id: str, occupied: Mapping[str, Any]
                   ) -> List[DeskRef]:
        return [desk for desk in self._by_room.get(room_id, ())
                if desk.desk_id not in occupied]

    def first_free(self, room: Any, occupied: Mapping[str, Any]
                   ) -> Optional[DeskRef]:
        free = self.free_desks(str(room.room_id), occupied)
        return free[0] if free else None

    # -- desks --------------------------------------------------------
    def desk(self, value: Any) -> Optional[DeskRef]:
        text = str(value or "").strip()
        if not text:
            return None
        return self._by_id.get(text) or self._by_seat.get(text)

    def desks_in_named_room(self, value: Any) -> Tuple[List[DeskRef], str]:
        text = str(value or "").strip().casefold()
        if not text:
            return [], REASON_UNKNOWN_ROOM
        by_id = self._by_room.get(str(value).strip())
        if by_id:
            return list(by_id), ""
        matches = list(self._room_names.get(text, ()))
        if not matches:
            return [], REASON_UNKNOWN_ROOM
        room_ids = {desk.room_id for desk in matches}
        if len(room_ids) > 1:
            return [], REASON_AMBIGUOUS_ROOM
        return matches, ""


# --------------------------------------------------------------- agent shape
def _get(raw: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _text(value: Any, *, limit: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _initials(name: str, agent_id: str) -> str:
    words = [word for word in str(name or "").replace("_", " ").split() if word]
    if words:
        return "".join(word[0].upper() for word in words[:2])
    return (agent_id or "?")[:2].upper()


def _is_active(status: str) -> bool:
    return str(status or "").strip().lower() not in DORMANT_STATUSES


@dataclass(frozen=True)
class _Agent:
    agent_id: str
    name: str
    status: str
    department: str
    seat_slug: str
    metadata: Mapping[str, Any]


def _agents(raw_agents: Iterable[Any]) -> Tuple[List[_Agent], int]:
    out: List[_Agent] = []
    ignored = 0
    for raw in raw_agents or ():
        agent_id = _text(_get(raw, "agent_id"), limit=120)
        if not agent_id:
            ignored += 1
            continue
        metadata = _get(raw, "metadata") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        seat_slug = ""
        for key in ("office_seat_slug", "seat_slug", "office_slug"):
            seat_slug = _text(metadata.get(key), limit=160)
            if seat_slug:
                break
        if not seat_slug:
            seat_slug = _text(_get(raw, "desk"), limit=160)
        out.append(_Agent(
            agent_id=agent_id,
            name=_text(_get(raw, "display_name") or _get(raw, "name"),
                       limit=200) or agent_id,
            status=_text(_get(raw, "status"), limit=40) or "active",
            department=_text(_get(raw, "department"), limit=120),
            seat_slug=seat_slug,
            metadata=metadata,
        ))
    out.sort(key=lambda a: a.agent_id)
    return out, ignored


# --------------------------------------------------------------- placement
def _world_values(metadata: Mapping[str, Any]) -> Tuple[Any, Any, Any, bool]:
    """Return ``(floor, room, desk, present)`` from explicit world keys only.

    Flat ``metadata.world_*`` keys win over a nested ``metadata.world`` map.
    A plain ``metadata.floor`` is deliberately **not** read: that key belongs to
    the office-seat plan and its numbers do not describe tower floors.
    """

    nested = metadata.get("world")
    if not isinstance(nested, Mapping):
        nested = {}

    def pick(flat_name: str, nested_name: str) -> Any:
        value = metadata.get(flat_name)
        if value is not None and value != "":
            return value
        value = nested.get(nested_name)
        if value is not None and value != "":
            return value
        return None

    floor = pick("world_floor", "floor")
    room = pick("world_room", "room")
    desk = pick("world_desk", "desk")
    if desk is None:
        desk = pick("world_seat", "seat")
    present = any(value is not None for value in (floor, room, desk))
    return floor, room, desk, present


def _resolve_world(
    index: _Index, *,
    floor_value: Any, room_value: Any, desk_value: Any,
    occupied: Mapping[str, Any],
) -> Tuple[Optional[Any], str, str]:
    """Resolve one explicit coordinate. Returns ``(placement, source, reason)``."""
    floor = None
    if floor_value is not None:
        floor = index.floor(floor_value)
        if floor is None:
            return None, "", REASON_UNKNOWN_FLOOR

    room = None
    if room_value is not None:
        room, reason = index.room(room_value, floor)
        if room is None:
            return None, "", reason

    if desk_value is not None:
        desk = index.desk(desk_value)
        if desk is None:
            return None, "", REASON_UNKNOWN_DESK
        if floor is not None and desk.floor_id != str(floor.floor_id):
            return None, "", REASON_DESK_MISMATCH
        if room is not None and desk.room_id != str(room.room_id):
            return None, "", REASON_DESK_MISMATCH
        if desk.desk_id in occupied:
            return None, "", REASON_DESK_TAKEN
        return _PlacementTarget(desk), SOURCE_WORLD_DESK, ""

    if room is not None:
        desk = index.first_free(room, occupied)
        if desk is None:
            return None, "", REASON_ROOM_FULL
        return _PlacementTarget(desk), SOURCE_WORLD_ROOM, ""

    if floor is not None:
        if len(floor.rooms) != 1:
            return None, "", REASON_AMBIGUOUS_FLOOR
        desk = index.first_free(floor.rooms[0], occupied)
        if desk is None:
            return None, "", REASON_ROOM_FULL
        return _PlacementTarget(desk), SOURCE_WORLD_FLOOR, ""

    return None, "", REASON_NO_MATCH


@dataclass(frozen=True)
class _PlacementTarget:
    desk: DeskRef


def _seat_for_slug(
    seat_slug: str, seats: Mapping[str, Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    slug = str(seat_slug or "").strip()
    if not slug:
        return None
    direct = seats.get(slug)
    if direct is not None:
        return direct
    wanted = slug.casefold()
    for key, seat in seats.items():
        if str(key).casefold() == wanted:
            return seat
    return None


def _seat_department(seat: Mapping[str, Any]) -> List[str]:
    values: List[str] = []
    for key in ("world_department", "division"):
        text = _text(seat.get(key), limit=120)
        if text:
            values.append(text)
    label = _text(seat.get("division_label"), limit=120)
    if label:
        values.append(label)
    seen: List[str] = []
    for value in values:
        if value.casefold() not in {item.casefold() for item in seen}:
            seen.append(value)
    return seen


def _department_floor(
    index: _Index, department: str, floor_hint: Any = None,
) -> Tuple[Optional[Any], str]:
    """Resolve a department to one floor, or name why it is ambiguous."""
    wanted = str(department or "").strip().casefold()
    if not wanted:
        return None, REASON_UNKNOWN_DEPARTMENT
    candidates = [
        floor for floor in index.floors
        if any(str(item).casefold() == wanted
               for item in getattr(floor, "department_ids", ()) or ())
    ]
    if not candidates:
        return None, REASON_UNKNOWN_DEPARTMENT
    if floor_hint is not None:
        hinted = index.floor(floor_hint)
        if hinted is not None:
            narrowed = [
                floor for floor in candidates
                if str(floor.floor_id) == str(hinted.floor_id)]
            if len(narrowed) == 1:
                return narrowed[0], ""
    if len(candidates) == 1:
        return candidates[0], ""
    return None, REASON_AMBIGUOUS_DEPARTMENT


def _department_matches_floor(department: str, floor: Any) -> bool:
    wanted = str(department or "").strip().casefold()
    if not wanted:
        return False
    return any(str(item).casefold() == wanted
               for item in getattr(floor, "department_ids", ()) or ())


def _first_free_on_floor(
    index: _Index, floor: Any, occupied: Mapping[str, Any],
) -> Optional[DeskRef]:
    """First free desk on a floor, rooms in stable id order, then desk order."""
    for room in sorted(floor.rooms, key=lambda r: str(r.room_id)):
        desk = index.first_free(room, occupied)
        if desk is not None:
            return desk
    return None


def _resolve_seat(
    index: _Index, seat: Mapping[str, Any], occupied: Mapping[str, Any],
) -> Tuple[Optional[DeskRef], str, str]:
    """Resolve an office-plan seat to a desk, or name why it cannot be."""
    floor_value, room_value, desk_value, present = _world_values(seat)
    if present:
        target, source, reason = _resolve_world(
            index, floor_value=floor_value, room_value=room_value,
            desk_value=desk_value, occupied=occupied)
        if target is not None:
            return target.desk, SOURCE_OFFICE_SEAT, f"world:{source}"
        return None, SOURCE_OFFICE_SEAT, reason

    # A seat whose room name is unique in the tower is an explicit match.
    room_value = _text(seat.get("room"), limit=200)
    if room_value:
        desks, reason = index.desks_in_named_room(room_value)
        if reason == "" and desks:
            desk = next(
                (item for item in desks if item.desk_id not in occupied), None)
            if desk is not None:
                return desk, SOURCE_OFFICE_SEAT, f"room:{room_value}"
            return None, SOURCE_OFFICE_SEAT, REASON_ROOM_FULL
        if reason == REASON_AMBIGUOUS_ROOM:
            return None, SOURCE_OFFICE_SEAT, reason

    # A division that names a tower department on exactly one floor is a real
    # match. The seat's own floor is an allowed tie-breaker only when that
    # floor also carries the department.
    seat_floor = seat.get("world_floor") or seat.get("floor")
    for department in _seat_department(seat):
        floor, reason = _department_floor(index, department, seat_floor)
        if floor is not None:
            desk = _first_free_on_floor(index, floor, occupied)
            if desk is not None:
                return (desk, SOURCE_OFFICE_SEAT,
                        f"department:{department}@{floor.floor_id}")
            return None, SOURCE_OFFICE_SEAT, REASON_ROOM_FULL
        if reason == REASON_AMBIGUOUS_DEPARTMENT:
            return None, SOURCE_OFFICE_SEAT, reason
    return None, SOURCE_OFFICE_SEAT, REASON_SEAT_NOT_IN_WORLD


def project_occupancy(
    *,
    floors: Optional[Sequence[Any]] = None,
    registry: Any = None,
    agents: Iterable[Any] = (),
    office_seats: Iterable[Mapping[str, Any]] = (),
    max_list: int = MAX_LIST,
) -> OccupancyProjection:
    """Project real agents onto real desks, explicitly or not at all."""
    if floors is None:
        if registry is None:
            from ...business.roles import RoleRegistry

            registry = RoleRegistry.default()
        floors = list(registry.floors)
    index = _Index(list(floors))

    seat_map: Dict[str, Mapping[str, Any]] = {}
    for seat in office_seats or ():
        if not isinstance(seat, Mapping):
            continue
        slug = _text(seat.get("slug"), limit=160)
        if slug:
            seat_map.setdefault(slug, seat)

    records, ignored = _agents(agents)
    occupied: Dict[str, Dict[str, Any]] = {}
    placements: List[Placement] = []
    unplaced: List[Unplaced] = []

    for agent in records:
        if not _is_active(agent.status):
            unplaced.append(Unplaced(
                agent_id=agent.agent_id, name=agent.name,
                status=agent.status, reason=REASON_DORMANT))
            continue

        target: Optional[DeskRef] = None
        source = ""
        detail = ""
        reason = REASON_NO_MATCH

        floor_value, room_value, desk_value, present = _world_values(
            agent.metadata)
        if present:
            resolved, source, reason = _resolve_world(
                index, floor_value=floor_value, room_value=room_value,
                desk_value=desk_value, occupied=occupied)
            if resolved is not None:
                target = resolved.desk
                detail = f"agent:{agent.agent_id}"
        elif agent.seat_slug:
            seat = _seat_for_slug(agent.seat_slug, seat_map)
            if seat is None:
                reason = REASON_SEAT_NOT_FOUND
            else:
                target, source, note = _resolve_seat(index, seat, occupied)
                if target is not None:
                    detail = note or f"seat:{agent.seat_slug}"
                else:
                    reason = note
        else:
            department = _text(
                agent.metadata.get("world_department"), limit=120
            ) or agent.department
            if department:
                floor, reason = _department_floor(index, department)
                if floor is not None:
                    target = _first_free_on_floor(index, floor, occupied)
                    if target is None:
                        reason = REASON_ROOM_FULL
                    else:
                        source = SOURCE_DEPARTMENT
                        detail = f"department:{department}@{floor.floor_id}"

        if target is not None and target.desk_id in occupied:
            target = None
            reason = REASON_DESK_TAKEN

        if target is None:
            unplaced.append(Unplaced(
                agent_id=agent.agent_id, name=agent.name,
                status=agent.status, reason=reason, source=source,
                detail=detail))
            continue

        placement = Placement(
            agent_id=agent.agent_id,
            name=agent.name,
            initials=_initials(agent.name, agent.agent_id),
            status=agent.status,
            emoji=_text(agent.metadata.get("persona_emoji")
                        or agent.metadata.get("emoji"), limit=8),
            color=_text(agent.metadata.get("persona_color")
                        or agent.metadata.get("color"), limit=24),
            desk=target,
            source=source,
            detail=detail,
        )
        occupied[target.desk_id] = placement.occupant()
        placements.append(placement)

    placements.sort(key=lambda item: (item.desk.floor_index,
                                      item.desk.room_id,
                                      item.desk.desk_id,
                                      item.agent_id))
    unplaced.sort(key=lambda item: item.agent_id)
    digest = _digest(placements, unplaced, ignored)
    return OccupancyProjection(
        placements=tuple(placements),
        unplaced=tuple(unplaced),
        ignored=ignored,
        digest=digest,
    )


def _digest(placements: Sequence[Placement], unplaced: Sequence[Unplaced],
            ignored: int) -> str:
    body = {
        "placements": [item.to_dict() for item in placements],
        "unplaced": [item.to_dict() for item in unplaced],
        "ignored": int(ignored),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def occupancy_payload(
    projection: OccupancyProjection, *, max_list: int = MAX_LIST,
) -> Dict[str, Any]:
    """Bounded wire payload for the staffed tower read."""
    placements = list(projection.placements)
    unplaced = list(projection.unplaced)
    limit = max(1, int(max_list))
    return {
        "ok": True,
        "schema": SCHEMA,
        "staffed": bool(placements),
        "counts": projection.counts(),
        "placed": [item.to_dict() for item in placements[:limit]],
        "unplaced": [item.to_dict() for item in unplaced[:limit]],
        "truncated": {
            "placed": len(placements) > limit,
            "unplaced": len(unplaced) > limit,
        },
        "digest": projection.digest,
    }


__all__ = [
    "SCHEMA",
    "MAX_LIST",
    "OccupancyError",
    "DeskRef",
    "Placement",
    "Unplaced",
    "OccupancyProjection",
    "project_occupancy",
    "occupancy_payload",
    "SOURCE_WORLD_DESK",
    "SOURCE_WORLD_ROOM",
    "SOURCE_WORLD_FLOOR",
    "SOURCE_OFFICE_SEAT",
    "SOURCE_DEPARTMENT",
    "SOURCE_FLOOR",
]
