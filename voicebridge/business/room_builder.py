"""Department / room builder over the authoritative map and style layers (P6-02).

This module is the one editing surface for a Qubit Office floor. It composes
the existing authorities instead of inventing a second map engine:

* :mod:`voicebridge.office.world.map_schema` owns the versioned map document,
  its validation, its paid-art refusal and its append-only
  :class:`~voicebridge.office.world.map_schema.MapRegistry` (publish / undo /
  rollback).
* :mod:`voicebridge.office.world.style` owns archetypes, palettes, recipes,
  seeds and recipe validation.
* :mod:`voicebridge.office.world.model` owns the fixed 16x12 cell contract and
  the ``roomId:cellId:localSeatId`` seat namespace.
* :mod:`voicebridge.business.room_capabilities` remains the capability
  authority; this builder only records the manifest id a room is assigned.

The workflow is deliberately staged, exactly as the plan asks:

    draft -> preview -> validate -> approve -> publish

Agents may *propose* a change; only the builder applies it. Nothing here writes
live walls: every edit produces a new immutable :class:`MapDocument`, and
:meth:`RoomBuilder.publish` refuses an invalid document atomically (the map
registry validates before appending, so a failed publish leaves history
untouched).

Paid art never travels through this module. A room stores a style *recipe*
(archetype, palette, slugs, coordinates, seed) and hashed asset metadata only;
raw purchased sprites are refused by the map schema's paid-art guard.

Pure Python. No store handle, no clock, no network, deterministic.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..office.world import map_schema as MS
from ..office.world import style as ST
from ..office.world.model import (
    CELL_COLS,
    CELL_DESK_CAPACITY,
    CELL_DESK_SLOTS,
    CELL_ROWS,
    cell_origins,
    plan_cell_cluster,
)

SCHEMA = "qubit-office-room-builder/1"

#: The minimum functional grammar of a department floor (design §2). A floor
#: may add more rooms; it may not drop one of these.
DEPARTMENT_ZONES: Tuple[str, ...] = (
    "reception",
    "work",
    "meeting",
    "focus",
    "head_office",
    "kitchen",
    "restroom",
    "lounge",
    "storage",
    "comms",
)

#: Room kind/archetype spellings that satisfy each required zone.
ZONE_ALIASES: Mapping[str, frozenset[str]] = {
    "reception": frozenset({"reception"}),
    "work": frozenset({"work", "open_office"}),
    "meeting": frozenset({"meeting", "executive", "boardroom"}),
    "focus": frozenset({"focus", "wellness"}),
    "head_office": frozenset({"head_office", "executive"}),
    "kitchen": frozenset({"kitchen"}),
    "restroom": frozenset({"restroom", "wellness"}),
    "lounge": frozenset({"lounge", "wellness"}),
    "storage": frozenset({"storage", "fulfillment"}),
    "comms": frozenset({"comms", "ops", "server_ops", "control_room"}),
}

_DEFAULT_ARCHETYPE: Mapping[str, str] = {
    "reception": "reception",
    "work": "open_office",
    "meeting": "meeting",
    "focus": "wellness",
    "head_office": "executive",
    "kitchen": "kitchen",
    "restroom": "wellness",
    "lounge": "wellness",
    "storage": "fulfillment",
    "comms": "control_room",
    "server_ops": "server_ops",
    "war_room": "executive",
    "ops": "control_room",
    "finance": "finance",
    "legal": "legal",
    "creative": "creative",
    "support": "support",
    "sales": "sales",
    "research": "research",
    "showcase": "showcase",
    "training": "training",
    "media": "media",
}

_OPERATION_NAMES: Tuple[str, ...] = (
    "add_room",
    "remove_room",
    "add_cell",
    "remove_cell",
    "add_desk",
    "remove_desk",
    "set_room_style",
    "assign_room",
    "clone_department",
    "merge_departments",
    "split_department",
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/{}@#+-]{0,127}$")
_PAID_MARKERS = (
    "limezu",
    "modern_interiors",
    "moderninteriors",
    "portrait_generator",
    "serene_village",
    "serenevillage",
)
_RAW_ART_SUFFIXES = (".png", ".webp", ".psd", ".aseprite", ".ase", ".gif")


class RoomBuilderError(Exception):
    """A builder operation failed. ``code`` is typed and stable."""

    code = "ROOM_BUILDER_INVALID"
    status = 400

    def __init__(
        self,
        detail: str,
        *,
        code: Optional[str] = None,
        problems: Optional[Iterable[str]] = None,
        status: Optional[int] = None,
    ):
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.detail = detail
        self.problems: List[str] = list(problems or ())
        super().__init__(detail)


class RoomBuilderRefused(RoomBuilderError):
    code = "ROOM_BUILDER_REFUSED"
    status = 422


class RoomBuilderConflict(RoomBuilderError):
    code = "ROOM_BUILDER_VERSION_CONFLICT"
    status = 409


class RoomBuilderNotFound(RoomBuilderError):
    code = "ROOM_BUILDER_NOT_FOUND"
    status = 404


# ------------------------------------------------------------------- styles
@dataclass(frozen=True)
class RoomStyleSpec:
    """A room style recipe plus the two Qubit-owned extension fields."""

    archetype: str
    palette: str
    floor: str
    wall: str
    rug: str
    furniture_set: str
    lighting: str
    shadow: str = "shadowless"
    signage: str = "sign"
    seed: int = 0
    style_version: int = 1
    department_style: str = "cool_teal_neutral"

    def to_recipe(self) -> ST.RoomStyleRecipe:
        return ST.RoomStyleRecipe(
            archetype=self.archetype,
            palette=self.palette,
            floor=self.floor,
            wall=self.wall,
            rug=self.rug,
            furniture_set=self.furniture_set,
            lighting=self.lighting,
            shadow=self.shadow,
            seed=int(self.seed),
            style_version=int(self.style_version),
        )

    def validate(self) -> List[str]:
        problems: List[str] = []
        where = f"style {self.archetype!r}"
        if self.archetype not in ST.ARCHETYPES:
            problems.append(f"{where}: unknown archetype")
        if self.palette not in ST.PALETTES:
            problems.append(f"{where}: unknown palette {self.palette!r}")
        for label in ("floor", "wall", "rug", "furniture_set", "lighting",
                      "signage", "department_style"):
            value = getattr(self, label)
            if not isinstance(value, str) or not _SLUG_RE.match(value):
                problems.append(f"{where}: {label} must be a slug, got {value!r}")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) \
                or self.seed < 0:
            problems.append(f"{where}: seed must be a non-negative int")
        if not isinstance(self.style_version, int) \
                or isinstance(self.style_version, bool) or self.style_version < 1:
            problems.append(f"{where}: style_version must be >= 1")
        try:
            problems.extend(ST.validate_recipe(self.to_recipe()))
        except ST.RoomStyleError as exc:  # pragma: no cover - defensive
            problems.extend(exc.problems)
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archetype": self.archetype,
            "palette": self.palette,
            "floor": self.floor,
            "wall": self.wall,
            "rug": self.rug,
            "furniture_set": self.furniture_set,
            "lighting": self.lighting,
            "shadow": self.shadow,
            "signage": self.signage,
            "seed": int(self.seed),
            "style_version": int(self.style_version),
            "department_style": self.department_style,
        }

    def to_map_style(
        self,
        *,
        zone: Optional[str] = None,
        assignment: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        out = self.to_dict()
        out["zone"] = zone or self.archetype
        if assignment is not None:
            out["assignment"] = dict(assignment)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RoomStyleSpec":
        if not isinstance(data, Mapping):
            raise RoomBuilderError("style must be a mapping")
        known = {
            "archetype", "palette", "floor", "wall", "rug", "furniture_set",
            "lighting", "shadow", "signage", "seed", "style_version",
            "department_style",
        }
        missing = sorted(known - set(data))
        if missing:
            raise RoomBuilderError(
                f"style is missing field(s): {', '.join(missing)}")
        try:
            spec = cls(
                archetype=str(data["archetype"]),
                palette=str(data["palette"]),
                floor=str(data["floor"]),
                wall=str(data["wall"]),
                rug=str(data["rug"]),
                furniture_set=str(data["furniture_set"]),
                lighting=str(data["lighting"]),
                shadow=str(data.get("shadow", "shadowless")),
                signage=str(data.get("signage", "sign")),
                seed=int(data.get("seed", 0)),
                style_version=int(data.get("style_version", 1)),
                department_style=str(data.get("department_style", "cool_teal_neutral")),
            )
        except (TypeError, ValueError) as exc:
            raise RoomBuilderError(f"style field is not valid: {exc}") from exc
        return spec


@dataclass(frozen=True)
class RoomAssignment:
    """Business/role/agent/capability binding recorded on a room."""

    business_id: str = ""
    department_id: str = ""
    role_ids: Tuple[str, ...] = ()
    agent_ids: Tuple[str, ...] = ()
    capability_manifest_id: str = ""
    memory_scope: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "business_id": self.business_id,
            "department_id": self.department_id,
            "role_ids": list(self.role_ids),
            "agent_ids": list(self.agent_ids),
            "capability_manifest_id": self.capability_manifest_id,
            "memory_scope": self.memory_scope,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RoomAssignment":
        if not isinstance(data, Mapping):
            raise RoomBuilderError("assignment must be a mapping")
        return cls(
            business_id=str(data.get("business_id", "") or ""),
            department_id=str(data.get("department_id", "") or ""),
            role_ids=tuple(str(item) for item in data.get("role_ids", ()) or ()),
            agent_ids=tuple(str(item) for item in data.get("agent_ids", ()) or ()),
            capability_manifest_id=str(
                data.get("capability_manifest_id", "") or ""),
            memory_scope=str(data.get("memory_scope", "") or ""),
        )

    def validate(self) -> List[str]:
        problems: List[str] = []
        for label in ("business_id", "department_id",
                      "capability_manifest_id", "memory_scope"):
            value = getattr(self, label)
            if value and not _TOKEN_RE.match(value):
                problems.append(f"assignment.{label} is not a valid token")
        for label in ("role_ids", "agent_ids"):
            values = getattr(self, label)
            if len(set(values)) != len(values):
                problems.append(f"assignment.{label} repeats a value")
            for value in values:
                if not _TOKEN_RE.match(value):
                    problems.append(
                        f"assignment.{label} has invalid value {value!r}")
        return problems


def seed_for(department_id: str, floor_id: str, room_id: str,
             style_version: int = 1) -> int:
    """Deterministic seed for a business room (delegates to the style module)."""
    return ST.room_seed(department_id, floor_id, room_id, style_version)


def default_style(
    archetype: str,
    seed: int = 0,
    *,
    department_style: Optional[str] = None,
    style_version: int = 1,
    signage: Optional[str] = None,
) -> RoomStyleSpec:
    """Build a validated deterministic recipe for ``archetype``."""
    if archetype not in ST.ARCHETYPES:
        raise RoomBuilderError(
            f"unknown archetype {archetype!r}", problems=[archetype])
    # ``decor_count=0`` avoids the shipped default decor pool naming a category
    # an archetype forbids (e.g. a plant in a server room); the builder adds
    # decor explicitly through the style controls instead.
    recipe = ST.build_room_recipe(
        archetype, int(seed), style_version=int(style_version), decor_count=0)
    spec = RoomStyleSpec(
        archetype=recipe.archetype,
        palette=recipe.palette,
        floor=recipe.floor,
        wall=recipe.wall,
        rug=recipe.rug,
        furniture_set=recipe.furniture_set,
        lighting=recipe.lighting,
        shadow=recipe.shadow,
        signage=signage or f"sign-{archetype}",
        seed=int(recipe.seed),
        style_version=int(recipe.style_version),
        department_style=department_style or recipe.palette,
    )
    problems = spec.validate()
    if problems:
        raise RoomBuilderError("; ".join(problems), problems=problems)
    return spec


def style_from_map_style(style: Mapping[str, Any]) -> RoomStyleSpec:
    return RoomStyleSpec.from_dict(style)


def _style_asset(room: MS.MapRoom) -> Tuple[MS.MapAssetRef, ...]:
    descriptor = json.dumps(
        {"room": room.room_id, "kind": room.kind, "style": dict(room.style)},
        sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
    return (
        MS.MapAssetRef(
            asset_id=f"style:{room.kind}",
            kind="recipe",
            ref=f"recipes/{room.kind}",
            sha256=digest,
        ),
    )


# ------------------------------------------------------------------ helpers
def _overlap(a: Tuple[int, int, int, int],
             b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _with_summary(document: MS.MapDocument) -> MS.MapDocument:
    summary = MS.compute_summary(document)
    return replace(
        document,
        connectivity=summary["connectivity"],
        capacity=summary["capacity"],
        egress=summary["egress"],
    )


def _floor(document: MS.MapDocument, floor_id: str) -> MS.MapFloor:
    for floor in document.floors:
        if floor.floor_id == floor_id:
            return floor
    raise RoomBuilderNotFound(f"floor {floor_id!r} does not exist")


def _room(document: MS.MapDocument, room_id: str) -> MS.MapRoom:
    for room in document.rooms:
        if room.room_id == room_id:
            return room
    raise RoomBuilderNotFound(f"room {room_id!r} does not exist")


def _cell(document: MS.MapDocument, cell_id: str) -> MS.MapCell:
    for cell in document.cells:
        if cell.cell_id == cell_id:
            return cell
    raise RoomBuilderNotFound(f"cell {cell_id!r} does not exist")


def _desk(document: MS.MapDocument, desk_id: str) -> MS.MapDesk:
    for desk in document.desks:
        if desk.desk_id == desk_id:
            return desk
    raise RoomBuilderNotFound(f"desk {desk_id!r} does not exist")


def _require_room_id(room_id: str) -> None:
    if not isinstance(room_id, str) or not _TOKEN_RE.match(room_id):
        raise RoomBuilderError(f"invalid room id {room_id!r}")


def _department_style(document: MS.MapDocument, floor_id: str) -> Optional[str]:
    """The style signature the rest of the floor uses, if one is declared."""
    for room in document.rooms:
        if room.floor_id != floor_id:
            continue
        value = room.style.get("department_style")
        if isinstance(value, str) and value:
            return value
    return None


def _require_unique(document: MS.MapDocument, room_id: str) -> None:
    if any(room.room_id == room_id for room in document.rooms):
        raise RoomBuilderConflict(f"room {room_id!r} already exists")


def _build_cells(
    room_id: str, desks: int, kind: str,
) -> Tuple[Tuple[MS.MapCell, ...], Tuple[MS.MapDesk, ...]]:
    cells: List[MS.MapCell] = []
    built_desks: List[MS.MapDesk] = []
    for cell in plan_cell_cluster(room_id, int(desks), kind=kind):
        cell_id = f"{room_id}:{cell.cell_id}"
        cell_desks: List[MS.MapDesk] = []
        for desk in cell.desks:
            cell_desks.append(MS.MapDesk(
                desk_id=desk.desk_id,
                seat_id=desk.seat.seat_id,
                cell_id=cell_id,
                room_id=room_id,
                x=int(desk.tile.x),
                y=int(desk.tile.y),
                width=int(desk.width),
                height=int(desk.height),
                seat_kind=kind,
            ))
        cells.append(MS.MapCell(
            cell_id=cell_id,
            room_id=room_id,
            index=int(cell.index),
            x=int(cell.origin.x),
            y=int(cell.origin.y),
            cols=CELL_COLS,
            rows=CELL_ROWS,
            desk_ids=tuple(desk.desk_id for desk in cell_desks),
        ))
        built_desks.extend(cell_desks)
    return tuple(cells), tuple(built_desks)


def _make_room(
    *,
    room_id: str,
    floor_id: str,
    name: str,
    kind: str,
    archetype: str,
    desks: int,
    style: RoomStyleSpec,
    zone: Optional[str] = None,
    assignment: Optional[RoomAssignment] = None,
    activation_state: str = "dormant",
    status: str = "draft",
) -> Tuple[MS.MapRoom, Tuple[MS.MapCell, ...], Tuple[MS.MapDesk, ...]]:
    cells, built_desks = _build_cells(room_id, desks, kind)
    style_map = style.to_map_style(
        zone=zone or kind,
        assignment=assignment.to_dict() if assignment else None,
    )
    room = MS.MapRoom(
        room_id=room_id,
        floor_id=floor_id,
        name=name,
        kind=kind,
        archetype=archetype,
        capacity=len(built_desks),
        cell_ids=tuple(cell.cell_id for cell in cells),
        desk_ids=tuple(desk.desk_id for desk in built_desks),
        portal_ids=(),
        egress=((CELL_COLS // 2, CELL_ROWS - 1),),
        memory_scope=(
            assignment.memory_scope if assignment and assignment.memory_scope
            else f"memory:{floor_id}"
        ),
        capability_manifest_id=(
            assignment.capability_manifest_id
            if assignment and assignment.capability_manifest_id
            else f"capability:{kind}:{room_id}"
        ),
        activation_state=activation_state,
        status=status,
        style=style_map,
        assets=(),
    )
    room = replace(room, assets=_style_asset(room))
    return room, cells, built_desks


def _rebuild_structure(document: MS.MapDocument) -> MS.MapDocument:
    """Recompute floor room lists, portals and the summary after a structural edit."""
    floors_sorted = sorted(document.floors, key=lambda floor: floor.index)
    rooms_by_floor: Dict[str, List[MS.MapRoom]] = {
        floor.floor_id: [] for floor in floors_sorted}
    for room in document.rooms:
        rooms_by_floor.setdefault(room.floor_id, []).append(room)

    portal_ids: Dict[str, List[str]] = {
        room.room_id: [] for room in document.rooms}
    portals: List[MS.MapPortal] = []
    new_floors: List[MS.MapFloor] = []
    floor_entries: List[Tuple[str, str]] = []

    for floor in floors_sorted:
        floor_rooms = rooms_by_floor.get(floor.floor_id, [])
        ids = [room.room_id for room in floor_rooms]
        entry = floor.entry_room_id if floor.entry_room_id in ids \
            else (ids[0] if ids else "")
        new_floors.append(replace(
            floor, room_ids=tuple(ids), entry_room_id=entry))
        if not ids:
            continue
        floor_entries.append((floor.floor_id, entry))
        for index, room_id in enumerate(ids):
            if room_id == entry:
                continue
            portal_id = f"p:{floor.floor_id}:{entry}->{room_id}"
            portals.append(MS.MapPortal(
                portal_id=portal_id,
                floor_id=floor.floor_id,
                source_room_id=entry,
                target_room_id=room_id,
                kind="door",
                x=(index * 3) % CELL_COLS,
                y=CELL_ROWS - 1,
            ))
            portal_ids[entry].append(portal_id)
            portal_ids[room_id].append(portal_id)

    previous: Optional[Tuple[str, str]] = None
    for floor_id, entry in floor_entries:
        if previous is not None and entry and previous[1]:
            portal_id = f"p:{floor_id}:elevator->{previous[1]}"
            portals.append(MS.MapPortal(
                portal_id=portal_id,
                floor_id=floor_id,
                source_room_id=entry,
                target_room_id=previous[1],
                kind="elevator",
                x=1,
                y=1,
            ))
            portal_ids[entry].append(portal_id)
            portal_ids[previous[1]].append(portal_id)
        previous = (floor_id, entry)

    rooms = [
        replace(room, portal_ids=tuple(portal_ids.get(room.room_id, ())))
        for room in document.rooms
    ]
    return _with_summary(replace(
        document,
        floors=tuple(new_floors),
        rooms=tuple(rooms),
        portals=tuple(portals),
    ))


# --------------------------------------------------------------- operations
def add_room(
    document: MS.MapDocument,
    *,
    floor_id: str,
    room_id: str,
    name: str,
    kind: str,
    archetype: Optional[str] = None,
    desks: int = 6,
    zone: Optional[str] = None,
    style: Optional[RoomStyleSpec | Mapping[str, Any]] = None,
    assignment: Optional[RoomAssignment | Mapping[str, Any]] = None,
) -> MS.MapDocument:
    _floor(document, floor_id)
    _require_room_id(room_id)
    _require_unique(document, room_id)
    if not isinstance(desks, int) or isinstance(desks, bool) or desks <= 0:
        raise RoomBuilderError("desks must be a positive integer")
    resolved_archetype = archetype or _DEFAULT_ARCHETYPE.get(kind, "open_office")
    resolved_style = (
        style if isinstance(style, RoomStyleSpec)
        else RoomStyleSpec.from_dict(style) if style is not None
        else default_style(
            resolved_archetype,
            seed_for(floor_id, floor_id, room_id),
            department_style=_department_style(document, floor_id),
        )
    )
    problems = resolved_style.validate()
    if problems:
        raise RoomBuilderError("invalid room style", problems=problems)
    resolved_assignment = (
        assignment if isinstance(assignment, RoomAssignment)
        else RoomAssignment.from_dict(assignment) if assignment is not None
        else None
    )
    if resolved_assignment is not None:
        problems = resolved_assignment.validate()
        if problems:
            raise RoomBuilderError("invalid assignment", problems=problems)
    room, cells, built_desks = _make_room(
        room_id=room_id,
        floor_id=floor_id,
        name=name,
        kind=kind,
        archetype=resolved_archetype,
        desks=desks,
        style=resolved_style,
        zone=zone,
        assignment=resolved_assignment,
    )
    return _rebuild_structure(replace(
        document,
        rooms=document.rooms + (room,),
        cells=document.cells + cells,
        desks=document.desks + built_desks,
    ))


def remove_room(document: MS.MapDocument, room_id: str) -> MS.MapDocument:
    room = _room(document, room_id)
    cell_ids = set(room.cell_ids)
    desk_ids = set(room.desk_ids)
    return _rebuild_structure(replace(
        document,
        rooms=tuple(r for r in document.rooms if r.room_id != room_id),
        cells=tuple(c for c in document.cells if c.cell_id not in cell_ids),
        desks=tuple(d for d in document.desks if d.desk_id not in desk_ids),
        portals=tuple(
            p for p in document.portals
            if room_id not in (p.source_room_id, p.target_room_id)
        ),
    ))


def add_cell(
    document: MS.MapDocument, room_id: str, *, desks: int = 6,
) -> MS.MapDocument:
    room = _room(document, room_id)
    if not isinstance(desks, int) or isinstance(desks, bool) \
            or desks < 0 or desks > CELL_DESK_CAPACITY:
        raise RoomBuilderError(
            f"a cell holds 0..{CELL_DESK_CAPACITY} desks")
    existing_cells = [c for c in document.cells if c.room_id == room_id]
    index = max((c.index for c in existing_cells), default=-1) + 1
    cell_id = f"{room_id}:c{index}"
    existing_cell_ids = {c.cell_id for c in document.cells}
    suffix = index
    while cell_id in existing_cell_ids:
        suffix += 1
        cell_id = f"{room_id}:c{suffix}"
    origins = cell_origins(suffix + 1)
    origin = origins[suffix]
    existing_desk_ids = {d.desk_id for d in document.desks}
    existing_seat_ids = {d.seat_id for d in document.desks}
    cell_desks: List[MS.MapDesk] = []
    local = 0
    for slot in range(desks):
        while f"{room_id}:{cell_id}:s{local}" in existing_seat_ids:
            local += 1
        seat_local = f"s{local}"
        desk_local = f"d{slot}"
        desk_id = f"{room_id}:{cell_id}:{desk_local}"
        while desk_id in existing_desk_ids:
            desk_id = f"{desk_id}-{local}"
        x, y = CELL_DESK_SLOTS[slot]
        cell_desks.append(MS.MapDesk(
            desk_id=desk_id,
            seat_id=f"{room_id}:{cell_id}:{seat_local}",
            cell_id=cell_id,
            room_id=room_id,
            x=x,
            y=y,
            width=2,
            height=1,
            seat_kind=room.kind,
        ))
        existing_seat_ids.add(f"{room_id}:{cell_id}:{seat_local}")
        existing_desk_ids.add(desk_id)
        local += 1
    cell = MS.MapCell(
        cell_id=cell_id,
        room_id=room_id,
        index=index,
        x=origin.x,
        y=origin.y,
        cols=CELL_COLS,
        rows=CELL_ROWS,
        desk_ids=tuple(d.desk_id for d in cell_desks),
    )
    updated_room = replace(
        room,
        cell_ids=room.cell_ids + (cell_id,),
        desk_ids=room.desk_ids + tuple(d.desk_id for d in cell_desks),
        capacity=room.capacity + len(cell_desks),
    )
    rooms = tuple(
        updated_room if r.room_id == room_id else r for r in document.rooms)
    return _with_summary(replace(
        document,
        rooms=rooms,
        cells=document.cells + (cell,),
        desks=document.desks + tuple(cell_desks),
    ))


def remove_cell(
    document: MS.MapDocument, room_id: str, cell_id: str,
) -> MS.MapDocument:
    room = _room(document, room_id)
    cell = _cell(document, cell_id)
    if cell.room_id != room_id:
        raise RoomBuilderError(
            f"cell {cell_id!r} does not belong to room {room_id!r}")
    desk_ids = set(cell.desk_ids)
    updated_room = replace(
        room,
        cell_ids=tuple(cid for cid in room.cell_ids if cid != cell_id),
        desk_ids=tuple(did for did in room.desk_ids if did not in desk_ids),
        capacity=len([did for did in room.desk_ids if did not in desk_ids]),
    )
    rooms = tuple(
        updated_room if r.room_id == room_id else r for r in document.rooms)
    return _with_summary(replace(
        document,
        rooms=rooms,
        cells=tuple(c for c in document.cells if c.cell_id != cell_id),
        desks=tuple(d for d in document.desks if d.desk_id not in desk_ids),
    ))


def add_desk(
    document: MS.MapDocument,
    room_id: str,
    *,
    cell_id: Optional[str] = None,
) -> MS.MapDocument:
    room = _room(document, room_id)
    cells = [c for c in document.cells if c.room_id == room_id]
    target: Optional[MS.MapCell] = None
    if cell_id is not None:
        target = _cell(document, cell_id)
        if target.room_id != room_id:
            raise RoomBuilderError(
                f"cell {cell_id!r} does not belong to room {room_id!r}")
    else:
        for cell in cells:
            if len(cell.desk_ids) < CELL_DESK_CAPACITY:
                target = cell
                break
    if target is None:
        document = add_cell(document, room_id, desks=0)
        room = _room(document, room_id)
        target = _cell(document, room.cell_ids[-1])
    if len(target.desk_ids) >= CELL_DESK_CAPACITY:
        raise RoomBuilderError(
            f"cell {target.cell_id!r} is already at {CELL_DESK_CAPACITY} desks")
    used = set()
    for desk_id in target.desk_ids:
        desk = _desk(document, desk_id)
        used.add((desk.x, desk.y))
    slot = next(
        (index for index, coords in enumerate(CELL_DESK_SLOTS)
         if coords not in used),
        None,
    )
    if slot is None:
        raise RoomBuilderError(
            f"cell {target.cell_id!r} has no free desk slot")
    x, y = CELL_DESK_SLOTS[slot]
    existing_desk_ids = {d.desk_id for d in document.desks}
    existing_seat_ids = {d.seat_id for d in document.desks}
    local = 0
    while f"{room_id}:{target.cell_id}:s{local}" in existing_seat_ids:
        local += 1
    desk_id = f"{room_id}:{target.cell_id}:d{slot}"
    while desk_id in existing_desk_ids:
        desk_id = f"{desk_id}-{local}"
    desk = MS.MapDesk(
        desk_id=desk_id,
        seat_id=f"{room_id}:{target.cell_id}:s{local}",
        cell_id=target.cell_id,
        room_id=room_id,
        x=x,
        y=y,
        width=2,
        height=1,
        seat_kind=room.kind,
    )
    updated_cell = replace(
        target, desk_ids=target.desk_ids + (desk.desk_id,))
    updated_room = replace(
        room,
        desk_ids=room.desk_ids + (desk.desk_id,),
        capacity=room.capacity + 1,
    )
    return _with_summary(replace(
        document,
        rooms=tuple(
            updated_room if r.room_id == room_id else r
            for r in document.rooms
        ),
        cells=tuple(
            updated_cell if c.cell_id == target.cell_id else c
            for c in document.cells
        ),
        desks=document.desks + (desk,),
    ))


def remove_desk(document: MS.MapDocument, desk_id: str) -> MS.MapDocument:
    desk = _desk(document, desk_id)
    room = _room(document, desk.room_id)
    cell = _cell(document, desk.cell_id)
    updated_cell = replace(
        cell,
        desk_ids=tuple(item for item in cell.desk_ids if item != desk_id),
    )
    updated_room = replace(
        room,
        desk_ids=tuple(item for item in room.desk_ids if item != desk_id),
        capacity=len([item for item in room.desk_ids if item != desk_id]),
    )
    return _with_summary(replace(
        document,
        rooms=tuple(
            updated_room if r.room_id == room.room_id else r
            for r in document.rooms
        ),
        cells=tuple(
            updated_cell if c.cell_id == cell.cell_id else c
            for c in document.cells
        ),
        desks=tuple(d for d in document.desks if d.desk_id != desk_id),
    ))


def set_room_style(
    document: MS.MapDocument,
    room_id: str,
    style: RoomStyleSpec | Mapping[str, Any],
) -> MS.MapDocument:
    room = _room(document, room_id)
    spec = style if isinstance(style, RoomStyleSpec) \
        else RoomStyleSpec.from_dict(style)
    problems = spec.validate()
    if problems:
        raise RoomBuilderError("invalid room style", problems=problems)
    assignment = room.style.get("assignment")
    zone = room.style.get("zone")
    if not isinstance(zone, str):
        zone = room.kind
    new_style = spec.to_map_style(
        zone=zone,
        assignment=assignment if isinstance(assignment, Mapping) else None,
    )
    updated = replace(room, style=new_style)
    updated = replace(updated, assets=_style_asset(updated))
    return _with_summary(replace(
        document,
        rooms=tuple(
            updated if r.room_id == room_id else r for r in document.rooms),
    ))


def assign_room(
    document: MS.MapDocument,
    room_id: str,
    assignment: RoomAssignment | Mapping[str, Any],
) -> MS.MapDocument:
    room = _room(document, room_id)
    value = assignment if isinstance(assignment, RoomAssignment) \
        else RoomAssignment.from_dict(assignment)
    problems = value.validate()
    if problems:
        raise RoomBuilderError("invalid assignment", problems=problems)
    style = dict(room.style)
    style["assignment"] = value.to_dict()
    updated = replace(
        room,
        style=style,
        memory_scope=value.memory_scope or room.memory_scope,
        capability_manifest_id=(
            value.capability_manifest_id or room.capability_manifest_id),
    )
    return _with_summary(replace(
        document,
        rooms=tuple(
            updated if r.room_id == room_id else r for r in document.rooms),
    ))


def clone_department(
    document: MS.MapDocument,
    source_floor_id: str,
    new_floor_id: str,
    *,
    new_name: Optional[str] = None,
) -> MS.MapDocument:
    source = _floor(document, source_floor_id)
    if any(floor.floor_id == new_floor_id for floor in document.floors):
        raise RoomBuilderConflict(f"floor {new_floor_id!r} already exists")
    if not _TOKEN_RE.match(new_floor_id):
        raise RoomBuilderError(f"invalid floor id {new_floor_id!r}")
    index = max((floor.index for floor in document.floors), default=0) + 1
    source_rooms = [r for r in document.rooms if r.floor_id == source_floor_id]
    new_floor = MS.MapFloor(
        floor_id=new_floor_id,
        index=index,
        wing=source.wing,
        name=new_name or f"{source.name} copy",
        z_index=index,
        activation_state="dormant",
        room_ids=(),
        entry_room_id="",
    )
    new_rooms: List[MS.MapRoom] = []
    new_cells: List[MS.MapCell] = []
    new_desks: List[MS.MapDesk] = []
    for room in source_rooms:
        new_room_id = f"{new_floor_id}:{room.room_id}"
        _require_unique(document, new_room_id)
        existing = style_from_map_style(room.style)
        spec = replace(
            existing,
            department_style=f"{existing.department_style}.{new_floor_id}",
        )
        assignment = None
        raw_assignment = room.style.get("assignment")
        if isinstance(raw_assignment, Mapping):
            assignment = RoomAssignment.from_dict(raw_assignment)
        built_room, cells, desks = _make_room(
            room_id=new_room_id,
            floor_id=new_floor_id,
            name=room.name,
            kind=room.kind,
            archetype=room.archetype,
            desks=max(1, room.capacity),
            style=spec,
            zone=room.style.get("zone") if isinstance(
                room.style.get("zone"), str) else room.kind,
            assignment=assignment,
            activation_state="dormant",
        )
        new_rooms.append(built_room)
        new_cells.extend(cells)
        new_desks.extend(desks)
    return _rebuild_structure(replace(
        document,
        floors=document.floors + (new_floor,),
        rooms=document.rooms + tuple(new_rooms),
        cells=document.cells + tuple(new_cells),
        desks=document.desks + tuple(new_desks),
    ))


def merge_departments(
    document: MS.MapDocument,
    floor_ids: Sequence[str],
    target_floor_id: str,
) -> MS.MapDocument:
    if target_floor_id not in floor_ids:
        raise RoomBuilderError("target floor must be one of the merged floors")
    floors = {floor.floor_id: floor for floor in document.floors}
    for floor_id in floor_ids:
        if floor_id not in floors:
            raise RoomBuilderNotFound(f"floor {floor_id!r} does not exist")
    target_rooms = [r for r in document.rooms if r.floor_id == target_floor_id]
    target_style = None
    if target_rooms:
        target_style = target_rooms[0].style.get("department_style")
    merged_room_ids: List[str] = []
    rooms: List[MS.MapRoom] = []
    for room in document.rooms:
        if room.floor_id not in floor_ids:
            rooms.append(room)
            continue
        style = dict(room.style)
        if isinstance(target_style, str) and target_style:
            style["department_style"] = target_style
        merged_room_ids.append(room.room_id)
        rooms.append(replace(room, floor_id=target_floor_id, style=style))
    return _rebuild_structure(replace(
        document,
        floors=tuple(
            floor for floor in document.floors
            if floor.floor_id not in set(floor_ids) - {target_floor_id}
        ),
        rooms=tuple(rooms),
    ))


def split_department(
    document: MS.MapDocument,
    source_floor_id: str,
    new_floor_id: str,
    room_ids: Sequence[str],
    *,
    new_name: Optional[str] = None,
) -> MS.MapDocument:
    source = _floor(document, source_floor_id)
    if any(floor.floor_id == new_floor_id for floor in document.floors):
        raise RoomBuilderConflict(f"floor {new_floor_id!r} already exists")
    if not _TOKEN_RE.match(new_floor_id):
        raise RoomBuilderError(f"invalid floor id {new_floor_id!r}")
    selected = set(room_ids)
    if not selected:
        raise RoomBuilderError("split needs at least one room")
    source_room_ids = {r.room_id for r in document.rooms
                       if r.floor_id == source_floor_id}
    if not selected <= source_room_ids:
        raise RoomBuilderError("split rooms must belong to the source floor")
    if selected == source_room_ids:
        raise RoomBuilderError("split would leave the source floor empty")
    index = max((floor.index for floor in document.floors), default=0) + 1
    source_style = None
    for room in document.rooms:
        if room.room_id in selected:
            source_style = room.style.get("department_style")
            break
    new_floor = MS.MapFloor(
        floor_id=new_floor_id,
        index=index,
        wing=source.wing,
        name=new_name or f"{source.name} split",
        z_index=index,
        activation_state="dormant",
        room_ids=(),
        entry_room_id="",
    )
    rooms: List[MS.MapRoom] = []
    for room in document.rooms:
        if room.room_id not in selected:
            rooms.append(room)
            continue
        style = dict(room.style)
        base = source_style if isinstance(source_style, str) and source_style \
            else str(style.get("department_style", "style"))
        style["department_style"] = f"{base}.{new_floor_id}"
        rooms.append(replace(room, floor_id=new_floor_id, style=style))
    return _rebuild_structure(replace(
        document,
        floors=document.floors + (new_floor,),
        rooms=tuple(rooms),
    ))


_OPERATIONS: Mapping[str, Any] = {
    "add_room": add_room,
    "remove_room": remove_room,
    "add_cell": add_cell,
    "remove_cell": remove_cell,
    "add_desk": add_desk,
    "remove_desk": remove_desk,
    "set_room_style": set_room_style,
    "assign_room": assign_room,
    "clone_department": clone_department,
    "merge_departments": merge_departments,
    "split_department": split_department,
}


# --------------------------------------------------------------- validation
def _style_problems(room: MS.MapRoom) -> List[str]:
    problems: List[str] = []
    try:
        spec = style_from_map_style(room.style)
    except RoomBuilderError as exc:
        return [f"room {room.room_id!r}: {exc.detail}"]
    problems.extend(
        f"room {room.room_id!r}: {problem}" for problem in spec.validate())
    zone = room.style.get("zone")
    if zone is not None and (
        not isinstance(zone, str) or not _SLUG_RE.match(zone)
    ):
        problems.append(f"room {room.room_id!r}: invalid zone {zone!r}")
    assignment = room.style.get("assignment")
    if assignment is not None:
        try:
            problems.extend(
                f"room {room.room_id!r}: {problem}"
                for problem in RoomAssignment.from_dict(assignment).validate()
            )
        except RoomBuilderError as exc:
            problems.append(f"room {room.room_id!r}: {exc.detail}")
    return problems


def _asset_problems(room: MS.MapRoom) -> List[str]:
    problems: List[str] = []
    for asset in room.assets:
        where = f"room {room.room_id!r} asset {asset.asset_id!r}"
        if asset.paid:
            problems.append(f"{where}: paid art may not be embedded")
        low = str(asset.ref).lower()
        if any(marker in low for marker in _PAID_MARKERS) \
                and asset.kind != "recipe":
            problems.append(f"{where}: references a paid art pack")
        if low.endswith(_RAW_ART_SUFFIXES):
            problems.append(f"{where}: raw sprite sheets are not embedded")
    return problems


def validate_room_builder_document(
    document: MS.MapDocument,
    *,
    required_zones: Sequence[str] = DEPARTMENT_ZONES,
    require_unique_style: bool = True,
) -> List[str]:
    """All structural, style, access and asset defects, or ``[]``."""
    problems: List[str] = list(MS.validate_map(document))
    rooms = {room.room_id: room for room in document.rooms}
    cells = {cell.cell_id: cell for cell in document.cells}
    desks = {desk.desk_id: desk for desk in document.desks}

    # A room's cells are fixed 16x12 rectangles; they may not overlap.
    for room in document.rooms:
        rects: List[Tuple[int, int, int, int]] = []
        for cell_id in room.cell_ids:
            cell = cells.get(cell_id)
            if cell is None:
                continue
            rect = (cell.x, cell.y, cell.cols, cell.rows)
            if any(_overlap(rect, other) for other in rects):
                problems.append(
                    f"room {room.room_id!r}: CELL_COLLISION at cell"
                    f" {cell_id!r}")
            rects.append(rect)
    for cell in document.cells:
        if len(cell.desk_ids) > CELL_DESK_CAPACITY:
            problems.append(
                f"cell {cell.cell_id!r}: CELL_OVER_CAPACITY"
                f" ({len(cell.desk_ids)} > {CELL_DESK_CAPACITY})")
        rects: List[Tuple[int, int, int, int]] = []
        for desk_id in cell.desk_ids:
            desk = desks.get(desk_id)
            if desk is None:
                continue
            rect = (desk.x, desk.y, desk.width, desk.height)
            if any(_overlap(rect, other) for other in rects):
                problems.append(
                    f"cell {cell.cell_id!r}: DESK_COLLISION at desk"
                    f" {desk_id!r}")
            rects.append(rect)

    for room in document.rooms:
        problems.extend(_style_problems(room))
        problems.extend(_asset_problems(room))
        if not room.portal_ids:
            problems.append(
                f"room {room.room_id!r}: MISSING_DOOR (no door or portal)")

    department_styles: Dict[str, str] = {}
    for floor in document.floors:
        present: set[str] = set()
        styles: set[str] = set()
        for room_id in floor.room_ids:
            room = rooms.get(room_id)
            if room is None:
                continue
            present.add(room.kind)
            archetype = room.style.get("archetype")
            if isinstance(archetype, str):
                present.add(archetype)
            zone = room.style.get("zone")
            if isinstance(zone, str):
                present.add(zone)
            signature = room.style.get("department_style")
            if isinstance(signature, str) and signature:
                styles.add(signature)
            else:
                problems.append(
                    f"room {room.room_id!r}: missing department_style")
        for zone in required_zones:
            aliases = ZONE_ALIASES.get(zone, frozenset({zone}))
            if not (present & aliases):
                problems.append(
                    f"floor {floor.floor_id!r}: MISSING_REQUIRED_ROOM"
                    f" {zone!r}")
        if require_unique_style and styles:
            if len(styles) > 1:
                problems.append(
                    f"floor {floor.floor_id!r}: MIXED_DEPARTMENT_STYLE"
                    f" {sorted(styles)}")
            else:
                signature = next(iter(styles))
                if signature in department_styles:
                    problems.append(
                        f"STYLE_COLLISION: floors"
                        f" {department_styles[signature]!r} and"
                        f" {floor.floor_id!r} both use {signature!r}")
                else:
                    department_styles[signature] = floor.floor_id
    # ``validate_map`` and the builder's own asset pass can name the same
    # paid-art defect; report each distinct defect once, in discovery order.
    seen: set[str] = set()
    unique: List[str] = []
    for problem in problems:
        if problem in seen:
            continue
        seen.add(problem)
        unique.append(problem)
    return unique


def assert_valid_document(
    document: MS.MapDocument,
    *,
    required_zones: Sequence[str] = DEPARTMENT_ZONES,
    require_unique_style: bool = True,
) -> None:
    problems = validate_room_builder_document(
        document,
        required_zones=required_zones,
        require_unique_style=require_unique_style,
    )
    if problems:
        raise RoomBuilderRefused(
            f"invalid draft: {problems[0]}", problems=problems)


# ---------------------------------------------------------------- proposals
@dataclass(frozen=True)
class RoomProposal:
    """An agent- or owner-authored change that has not been applied."""

    proposal_id: str
    op: str
    params: Mapping[str, Any]
    proposed_by: str
    base_revision: int
    document: MS.MapDocument
    problems: Tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "op": self.op,
            "params": dict(self.params),
            "proposed_by": self.proposed_by,
            "base_revision": int(self.base_revision),
            "valid": self.valid,
            "problems": list(self.problems),
            "document_digest": MS.map_digest(self.document),
        }


# ------------------------------------------------------------------ builder
class RoomBuilder:
    """Staged, undoable department/room drafts over one map document."""

    def __init__(
        self,
        document: MS.MapDocument,
        *,
        registry: Optional[MS.MapRegistry] = None,
        required_zones: Sequence[str] = DEPARTMENT_ZONES,
        require_unique_style: bool = True,
        capabilities: Any = None,
    ) -> None:
        self._document = document
        self._registry = registry or MS.MapRegistry()
        self._required_zones = tuple(required_zones)
        self._require_unique_style = bool(require_unique_style)
        self._capabilities = capabilities
        self._history: List[Tuple[MS.MapDocument, str]] = []
        self._future: List[Tuple[MS.MapDocument, str]] = []
        self._status = "draft"
        self._revision = 0
        self._proposals: List[RoomProposal] = []
        self._applied: List[Dict[str, Any]] = []
        self._approval: Optional[Dict[str, Any]] = None

    # -- reads ----------------------------------------------------------
    @property
    def document(self) -> MS.MapDocument:
        return self._document

    @property
    def status(self) -> str:
        return self._status

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    @property
    def can_redo(self) -> bool:
        return bool(self._future)

    @property
    def proposals(self) -> Tuple[RoomProposal, ...]:
        return tuple(self._proposals)

    @property
    def approval(self) -> Optional[Mapping[str, Any]]:
        return dict(self._approval) if self._approval else None

    def validate(self, document: Optional[MS.MapDocument] = None) -> List[str]:
        return validate_room_builder_document(
            document or self._document,
            required_zones=self._required_zones,
            require_unique_style=self._require_unique_style,
        )

    def preview(self, document: Optional[MS.MapDocument] = None) -> Dict[str, Any]:
        target = document or self._document
        problems = self.validate(target)
        summary = MS.compute_summary(target)
        return {
            "schema": SCHEMA,
            "status": self._status,
            "revision": int(self._revision),
            "document_digest": MS.map_digest(target),
            "document_status": target.status,
            "counts": {
                "floors": len(target.floors),
                "rooms": len(target.rooms),
                "cells": len(target.cells),
                "desks": len(target.desks),
                "portals": len(target.portals),
            },
            "capacity": int(summary["capacity"]),
            "egress": int(summary["egress"]),
            "problems": problems,
            "valid": not problems,
        }

    # -- proposals ------------------------------------------------------
    def propose(self, op: str, *, proposed_by: str = "agent:builder",
                **params: Any) -> RoomProposal:
        handler = _OPERATIONS.get(op)
        if handler is None:
            raise RoomBuilderError(f"unknown builder operation {op!r}")
        document = handler(self._document, **params)
        problems = tuple(self.validate(document))
        proposal = RoomProposal(
            proposal_id=(
                f"{op}:{self._revision}:{MS.map_digest(document)[:12]}"),
            op=op,
            params={key: _jsonable(value) for key, value in params.items()},
            proposed_by=proposed_by,
            base_revision=self._revision,
            document=document,
            problems=problems,
        )
        self._proposals.append(proposal)
        return proposal

    def propose_add_room(self, *, proposed_by: str = "agent:builder",
                         **params: Any) -> RoomProposal:
        return self.propose("add_room", proposed_by=proposed_by, **params)

    def propose_remove_room(self, room_id: str,
                            *, proposed_by: str = "agent:builder") -> RoomProposal:
        return self.propose(
            "remove_room", proposed_by=proposed_by, room_id=room_id)

    def propose_add_cell(self, room_id: str, *, desks: int = 6,
                         proposed_by: str = "agent:builder") -> RoomProposal:
        return self.propose(
            "add_cell", proposed_by=proposed_by, room_id=room_id, desks=desks)

    def propose_remove_cell(self, room_id: str, cell_id: str,
                            *, proposed_by: str = "agent:builder") -> RoomProposal:
        return self.propose(
            "remove_cell", proposed_by=proposed_by,
            room_id=room_id, cell_id=cell_id)

    def propose_add_desk(self, room_id: str, *,
                         cell_id: Optional[str] = None,
                         proposed_by: str = "agent:builder") -> RoomProposal:
        params: Dict[str, Any] = {"room_id": room_id}
        if cell_id is not None:
            params["cell_id"] = cell_id
        return self.propose(
            "add_desk", proposed_by=proposed_by, **params)

    def propose_remove_desk(self, desk_id: str,
                            *, proposed_by: str = "agent:builder") -> RoomProposal:
        return self.propose(
            "remove_desk", proposed_by=proposed_by, desk_id=desk_id)

    def propose_set_room_style(
        self, room_id: str, style: RoomStyleSpec | Mapping[str, Any],
        *, proposed_by: str = "agent:builder",
    ) -> RoomProposal:
        return self.propose(
            "set_room_style", proposed_by=proposed_by,
            room_id=room_id, style=style)

    def propose_assign_room(
        self, room_id: str, assignment: RoomAssignment | Mapping[str, Any],
        *, proposed_by: str = "agent:builder",
    ) -> RoomProposal:
        return self.propose(
            "assign_room", proposed_by=proposed_by,
            room_id=room_id, assignment=assignment)

    def propose_clone_department(self, source_floor_id: str,
                                 new_floor_id: str, *,
                                 new_name: Optional[str] = None,
                                 proposed_by: str = "agent:builder") -> RoomProposal:
        return self.propose(
            "clone_department", proposed_by=proposed_by,
            source_floor_id=source_floor_id, new_floor_id=new_floor_id,
            new_name=new_name)

    def propose_merge_departments(self, floor_ids: Sequence[str],
                                  target_floor_id: str,
                                  *, proposed_by: str = "agent:builder") -> RoomProposal:
        return self.propose(
            "merge_departments", proposed_by=proposed_by,
            floor_ids=list(floor_ids), target_floor_id=target_floor_id)

    def propose_split_department(
        self, source_floor_id: str, new_floor_id: str,
        room_ids: Sequence[str], *, new_name: Optional[str] = None,
        proposed_by: str = "agent:builder",
    ) -> RoomProposal:
        return self.propose(
            "split_department", proposed_by=proposed_by,
            source_floor_id=source_floor_id, new_floor_id=new_floor_id,
            room_ids=list(room_ids), new_name=new_name)

    # -- applying -------------------------------------------------------
    def apply(self, proposal: RoomProposal, *,
              applied_by: str = "owner:builder") -> "RoomBuilder":
        if not isinstance(proposal, RoomProposal):
            raise RoomBuilderError("apply needs a RoomProposal")
        if proposal.base_revision != self._revision:
            raise RoomBuilderConflict(
                f"proposal targets revision {proposal.base_revision}, current"
                f" revision is {self._revision}")
        self._history.append((self._document, self._status))
        self._future.clear()
        self._document = proposal.document
        self._status = "draft"
        self._approval = None
        self._revision += 1
        self._applied.append({
            "proposal_id": proposal.proposal_id,
            "op": proposal.op,
            "applied_by": applied_by,
            "revision": self._revision,
        })
        return self

    def undo(self) -> "RoomBuilder":
        if not self._history:
            raise RoomBuilderError(
                "nothing to undo", code="ROOM_BUILDER_UNDO_UNAVAILABLE",
                status=409)
        self._future.append((self._document, self._status))
        self._document, self._status = self._history.pop()
        self._status = "draft"
        self._approval = None
        self._revision += 1
        return self

    def redo(self) -> "RoomBuilder":
        if not self._future:
            raise RoomBuilderError(
                "nothing to redo", code="ROOM_BUILDER_REDO_UNAVAILABLE",
                status=409)
        self._history.append((self._document, self._status))
        self._document, self._status = self._future.pop()
        self._status = "draft"
        self._approval = None
        self._revision += 1
        return self

    def approve(self, *, approved_by: str = "owner:builder") -> Dict[str, Any]:
        problems = self.validate()
        if problems:
            raise RoomBuilderRefused(
                f"cannot approve an invalid draft: {problems[0]}",
                problems=problems)
        self._status = "approved"
        self._approval = {
            "approved_by": approved_by,
            "revision": int(self._revision),
            "document_digest": MS.map_digest(self._document),
        }
        return dict(self._approval)

    def publish(self, *, published_by: str = "owner:builder") -> MS.MapVersion:
        problems = self.validate()
        if problems:
            raise RoomBuilderRefused(
                f"cannot publish an invalid map: {problems[0]}",
                problems=problems)
        published = replace(self._document, status="published")
        record = self._registry.publish(published)
        self._document = published
        self._status = "published"
        self._approval = {
            "published_by": published_by,
            "revision": int(self._revision),
            "version": int(record.version),
            "document_digest": record.digest,
        }
        return record

    def rollback(self, version: int) -> MS.MapVersion:
        record = self._registry.get(self._document.map_id, int(version))
        self._history.append((self._document, self._status))
        self._future.clear()
        self._document = record.document
        self._status = "rolled_back"
        self._approval = None
        self._revision += 1
        return record

    def versions(self) -> Tuple[MS.MapVersion, ...]:
        return self._registry.versions(self._document.map_id)

    def resolve_capability(
        self, room_id: str, *, department_id: Optional[str] = None,
    ) -> Any:
        """Resolve the room's capability manifest through the authority.

        :mod:`voicebridge.business.room_capabilities` owns manifests; this
        builder never keeps a second capability list. A caller that has already
        built a registry may inject it at construction.
        """
        from .room_capabilities import build_shipped_registry

        registry = self._capabilities or build_shipped_registry()
        return registry.manifest_for(
            _room(self._document, room_id), department_id=department_id)


# ------------------------------------------------------------------- sample
_SAMPLE_ROOMS: Tuple[Tuple[str, str, str, int, str], ...] = (
    ("rec-1", "Reception", "reception", 2, "reception"),
    ("work-1", "Open Office", "work", 6, "open_office"),
    ("meet-1", "Boardroom", "meeting", 2, "meeting"),
    ("focus-1", "Focus Room", "focus", 1, "wellness"),
    ("head-1", "Head Office", "head_office", 1, "executive"),
    ("kitchen-1", "Kitchen", "kitchen", 1, "kitchen"),
    ("rest-1", "Restroom", "restroom", 1, "wellness"),
    ("lounge-1", "Lounge", "lounge", 1, "wellness"),
    ("store-1", "Storage", "storage", 1, "fulfillment"),
    ("comms-1", "Comms / Ops", "comms", 1, "control_room"),
)


def sample_document() -> MS.MapDocument:
    """A small, complete one-department floor used by the verifier and tests."""
    floor = MS.MapFloor(
        floor_id="floor-001",
        index=1,
        wing="central",
        name="Operations",
        z_index=1,
        activation_state="active",
        room_ids=(),
        entry_room_id="rec-1",
    )
    rooms: List[MS.MapRoom] = []
    cells: List[MS.MapCell] = []
    desks: List[MS.MapDesk] = []
    for offset, (room_id, name, kind, seat_count, archetype) in enumerate(
        _SAMPLE_ROOMS
    ):
        spec = default_style(
            archetype,
            seed=seed_for("operations", "floor-001", room_id),
            department_style="operations",
            signage=f"sign-{kind}",
        )
        assignment = RoomAssignment(
            business_id="business:sample",
            department_id="operations",
            role_ids=("operations-lead",),
            agent_ids=(),
            capability_manifest_id=f"capability:{kind}:{room_id}",
            memory_scope=f"memory:operations:{room_id}",
        )
        room, room_cells, room_desks = _make_room(
            room_id=room_id,
            floor_id=floor.floor_id,
            name=name,
            kind=kind,
            archetype=archetype,
            desks=seat_count,
            style=spec,
            zone=kind,
            assignment=assignment,
            activation_state="active",
        )
        rooms.append(room)
        cells.extend(room_cells)
        desks.extend(room_desks)
    document = MS.MapDocument(
        schema=MS.SCHEMA,
        version=MS.VERSION,
        campus_id="campus-sample",
        map_id="sample-map",
        owner_id="owner:template",
        tenant_id="tenant:template",
        status="draft",
        activation_state="active",
        memory_scope="memory:campus:sample",
        capability_manifest_id="capability:campus:sample",
        floors=(floor,),
        rooms=tuple(rooms),
        cells=tuple(cells),
        desks=tuple(desks),
        portals=(),
    )
    return _rebuild_structure(document)


# --------------------------------------------------------------------- util
def _jsonable(value: Any) -> Any:
    if isinstance(value, RoomStyleSpec):
        return value.to_dict()
    if isinstance(value, RoomAssignment):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "SCHEMA",
    "DEPARTMENT_ZONES",
    "ZONE_ALIASES",
    "RoomBuilderError",
    "RoomBuilderRefused",
    "RoomBuilderConflict",
    "RoomBuilderNotFound",
    "RoomStyleSpec",
    "RoomAssignment",
    "RoomProposal",
    "RoomBuilder",
    "seed_for",
    "default_style",
    "style_from_map_style",
    "add_room",
    "remove_room",
    "add_cell",
    "remove_cell",
    "add_desk",
    "remove_desk",
    "set_room_style",
    "assign_room",
    "clone_department",
    "merge_departments",
    "split_department",
    "validate_room_builder_document",
    "assert_valid_document",
    "sample_document",
]
