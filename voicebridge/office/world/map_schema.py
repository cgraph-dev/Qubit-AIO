"""Versioned map schema, Tiled import/export and a transactional map registry.

P3-03 asks for a map data layer that a renderer may consume but may never use
as the source of business truth (``docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md`` §1,
``docs/QUBIT-OFFICE-UI-CONTRACT.md`` §285). This module carries the full
campus projection from :mod:`voicebridge.office.world.tower_plan` into a
strict, versioned document with portals, connectivity, capacity, egress, tile
coordinates, z-index, style/asset refs and hashes, activation state, memory
scope, capability-manifest id, owner and tenant.

Three rules are enforced here rather than trusted to a caller:

* **A cell is always 16x12.** A stretched cell is a named defect, not a
  different tile size.
* **Nothing is published half-validated.** :class:`MapRegistry.publish`
  validates first and only then appends a version; an invalid document raises
  a typed error and leaves the registry untouched.
* **Paid art is never embedded.** An asset ref may name a procedural style, a
  theme recipe coordinate or a hashed metadata asset — never a raw purchased
  sprite sheet. A paid ref is a typed refusal.

Tiled import/export is deliberately honest: layer objects are render hints and
carry no business facts (the UI contract says so). The canonical document
travels in a Tiled ``string`` property, and :func:`tiled_to_map` refuses a
Tiled file that lacks it instead of inventing a campus.

Pure Python. No I/O, no clock, no network, deterministic.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import CELL_COLS, CELL_ROWS

__all__ = [
    "SCHEMA",
    "VERSION",
    "MapSchemaError",
    "MapAssetRef",
    "MapDesk",
    "MapCell",
    "MapRoom",
    "MapFloor",
    "MapPortal",
    "MapDocument",
    "MapVersion",
    "MapRegistry",
    "build_campus_map",
    "validate_map",
    "assert_valid_map",
    "map_digest",
    "compute_summary",
    "map_to_tiled",
    "tiled_to_map",
]

SCHEMA = "qubit-office-map/1"
VERSION = 1
TILED_VERSION = "1.10"
TILED_TILEWIDTH = CELL_COLS
TILED_TILEHEIGHT = CELL_ROWS

DOC_STATUSES: Tuple[str, ...] = ("draft", "published", "archived")
ACTIVATION_STATES: Tuple[str, ...] = ("active", "dormant")
ASSET_KINDS: Tuple[str, ...] = ("procedural", "recipe", "tileset")
PORTAL_KINDS: Tuple[str, ...] = ("door", "opening", "elevator", "stairs", "corridor")

#: A paid pack's raw bytes must never ride in a map document. Recipe
#: coordinates are allowed (they are indices, not art); raw image refs and the
#: purchased pack names are not.
_PAID_ART_MARKERS = (
    "limezu", "modern_interiors", "moderninteriors", "portrait_generator",
    "serene_village", "serenevillage",
)
_RAW_ART_SUFFIXES = (".png", ".webp", ".psd", ".aseprite", ".ase", ".gif")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/{}@#+-]{0,127}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/{}@-]{0,127}$")
_SAFE_REF_RE = re.compile(r"^[^\\\x00-\x1f]+$")

TILED_DOCUMENT_PROPERTY = "qubit:document"
TILED_SCHEMA_PROPERTY = "qubit:schema"
TILED_VERSION_PROPERTY = "qubit:version"


class MapSchemaError(Exception):
    """A map document or registry operation failed. ``code`` is typed."""

    code = "OFFICE_MAP_INVALID"
    status = 400

    def __init__(
        self,
        detail: str,
        *,
        code: Optional[str] = None,
        status: Optional[int] = None,
        problems: Optional[Iterable[str]] = None,
    ):
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.detail = detail
        self.problems: List[str] = list(problems or ())
        super().__init__(detail)


class MapVersionConflict(MapSchemaError):
    code = "OFFICE_MAP_VERSION_CONFLICT"
    status = 409


class MapNotFound(MapSchemaError):
    code = "OFFICE_MAP_NOT_FOUND"
    status = 404


class MapUndoUnavailable(MapSchemaError):
    code = "OFFICE_MAP_UNDO_UNAVAILABLE"
    status = 409


class MapPaidArt(MapSchemaError):
    code = "OFFICE_MAP_PAID_ART"
    status = 403


# --------------------------------------------------------------------- atoms
@dataclass(frozen=True)
class MapAssetRef:
    """A renderer asset reference. Metadata only; no image bytes."""

    asset_id: str
    kind: str
    ref: str
    sha256: Optional[str] = None
    paid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "ref": self.ref,
            "sha256": self.sha256,
            "paid": bool(self.paid),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MapAssetRef":
        if not isinstance(data, Mapping):
            raise MapSchemaError("asset must be a mapping")
        _check_keys(
            data,
            frozenset({"asset_id", "kind", "ref", "sha256", "paid"}),
            "asset",
        )
        return cls(
            asset_id=_text(data.get("asset_id"), "asset.asset_id"),
            kind=_text(data.get("kind"), "asset.kind"),
            ref=_text(data.get("ref"), "asset.ref"),
            sha256=(
                None if data.get("sha256") is None
                else _text(data.get("sha256"), "asset.sha256")
            ),
            paid=bool(data.get("paid", False)),
        )


@dataclass(frozen=True)
class MapDesk:
    desk_id: str
    seat_id: str
    cell_id: str
    room_id: str
    x: int
    y: int
    width: int = 2
    height: int = 1
    seat_kind: str = "work"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "desk_id": self.desk_id,
            "seat_id": self.seat_id,
            "cell_id": self.cell_id,
            "room_id": self.room_id,
            "x": int(self.x),
            "y": int(self.y),
            "w": int(self.width),
            "h": int(self.height),
            "seat_kind": self.seat_kind,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MapDesk":
        if not isinstance(data, Mapping):
            raise MapSchemaError("desk must be a mapping")
        _check_keys(
            data,
            frozenset(
                {"desk_id", "seat_id", "cell_id", "room_id", "x", "y", "w",
                 "h", "seat_kind"}
            ),
            "desk",
        )
        return cls(
            desk_id=_text(data.get("desk_id"), "desk.desk_id"),
            seat_id=_text(data.get("seat_id"), "desk.seat_id"),
            cell_id=_text(data.get("cell_id"), "desk.cell_id"),
            room_id=_text(data.get("room_id"), "desk.room_id"),
            x=_int(data.get("x"), "desk.x"),
            y=_int(data.get("y"), "desk.y"),
            width=_int(data.get("w", 2), "desk.w"),
            height=_int(data.get("h", 1), "desk.h"),
            seat_kind=_text(data.get("seat_kind", "work"), "desk.seat_kind"),
        )


@dataclass(frozen=True)
class MapCell:
    cell_id: str
    room_id: str
    index: int
    x: int
    y: int
    cols: int = CELL_COLS
    rows: int = CELL_ROWS
    desk_ids: Tuple[str, ...] = ()

    @property
    def standard(self) -> bool:
        return (self.cols, self.rows) == (CELL_COLS, CELL_ROWS)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "room_id": self.room_id,
            "index": int(self.index),
            "x": int(self.x),
            "y": int(self.y),
            "cols": int(self.cols),
            "rows": int(self.rows),
            "desk_ids": list(self.desk_ids),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MapCell":
        if not isinstance(data, Mapping):
            raise MapSchemaError("cell must be a mapping")
        _check_keys(
            data,
            frozenset(
                {"cell_id", "room_id", "index", "x", "y", "cols", "rows",
                 "desk_ids"}
            ),
            "cell",
        )
        return cls(
            cell_id=_text(data.get("cell_id"), "cell.cell_id"),
            room_id=_text(data.get("room_id"), "cell.room_id"),
            index=_int(data.get("index"), "cell.index"),
            x=_int(data.get("x"), "cell.x"),
            y=_int(data.get("y"), "cell.y"),
            cols=_int(data.get("cols", CELL_COLS), "cell.cols"),
            rows=_int(data.get("rows", CELL_ROWS), "cell.rows"),
            desk_ids=tuple(
                _text(item, "cell.desk_id")
                for item in _sequence(data.get("desk_ids", ()), "cell.desk_ids")
            ),
        )


@dataclass(frozen=True)
class MapPortal:
    portal_id: str
    floor_id: str
    source_room_id: str
    target_room_id: str
    kind: str = "door"
    x: int = CELL_COLS // 2
    y: int = CELL_ROWS - 1
    bidirectional: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "portal_id": self.portal_id,
            "floor_id": self.floor_id,
            "source_room_id": self.source_room_id,
            "target_room_id": self.target_room_id,
            "kind": self.kind,
            "x": int(self.x),
            "y": int(self.y),
            "bidirectional": bool(self.bidirectional),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MapPortal":
        if not isinstance(data, Mapping):
            raise MapSchemaError("portal must be a mapping")
        _check_keys(
            data,
            frozenset(
                {"portal_id", "floor_id", "source_room_id", "target_room_id",
                 "kind", "x", "y", "bidirectional"}
            ),
            "portal",
        )
        return cls(
            portal_id=_text(data.get("portal_id"), "portal.portal_id"),
            floor_id=_text(data.get("floor_id"), "portal.floor_id"),
            source_room_id=_text(
                data.get("source_room_id"), "portal.source_room_id"),
            target_room_id=_text(
                data.get("target_room_id"), "portal.target_room_id"),
            kind=_text(data.get("kind", "door"), "portal.kind"),
            x=_int(data.get("x", CELL_COLS // 2), "portal.x"),
            y=_int(data.get("y", CELL_ROWS - 1), "portal.y"),
            bidirectional=bool(data.get("bidirectional", True)),
        )


@dataclass(frozen=True)
class MapRoom:
    room_id: str
    floor_id: str
    name: str
    kind: str
    archetype: str
    capacity: int
    cell_ids: Tuple[str, ...]
    desk_ids: Tuple[str, ...]
    portal_ids: Tuple[str, ...] = ()
    egress: Tuple[Tuple[int, int], ...] = ()
    memory_scope: str = ""
    capability_manifest_id: str = ""
    activation_state: str = "dormant"
    status: str = "draft"
    style: Mapping[str, Any] = field(default_factory=dict)
    assets: Tuple[MapAssetRef, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "floor_id": self.floor_id,
            "name": self.name,
            "kind": self.kind,
            "archetype": self.archetype,
            "capacity": int(self.capacity),
            "cell_ids": list(self.cell_ids),
            "desk_ids": list(self.desk_ids),
            "portal_ids": list(self.portal_ids),
            "egress": [{"x": int(x), "y": int(y)} for x, y in self.egress],
            "memory_scope": self.memory_scope,
            "capability_manifest_id": self.capability_manifest_id,
            "activation_state": self.activation_state,
            "status": self.status,
            "style": dict(self.style),
            "assets": [asset.to_dict() for asset in self.assets],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MapRoom":
        if not isinstance(data, Mapping):
            raise MapSchemaError("room must be a mapping")
        _check_keys(
            data,
            frozenset(
                {"room_id", "floor_id", "name", "kind", "archetype",
                 "capacity", "cell_ids", "desk_ids", "portal_ids", "egress",
                 "memory_scope", "capability_manifest_id", "activation_state",
                 "status", "style", "assets"}
            ),
            "room",
        )
        egress = tuple(
            (_int(_mapping(item, "room.egress").get("x"), "room.egress.x"),
             _int(_mapping(item, "room.egress").get("y"), "room.egress.y"))
            for item in _sequence(data.get("egress", ()), "room.egress")
        )
        return cls(
            room_id=_text(data.get("room_id"), "room.room_id"),
            floor_id=_text(data.get("floor_id"), "room.floor_id"),
            name=_text(data.get("name"), "room.name"),
            kind=_text(data.get("kind"), "room.kind"),
            archetype=_text(data.get("archetype"), "room.archetype"),
            capacity=_int(data.get("capacity"), "room.capacity"),
            cell_ids=tuple(
                _text(item, "room.cell_id")
                for item in _sequence(data.get("cell_ids", ()), "room.cell_ids")
            ),
            desk_ids=tuple(
                _text(item, "room.desk_id")
                for item in _sequence(data.get("desk_ids", ()), "room.desk_ids")
            ),
            portal_ids=tuple(
                _text(item, "room.portal_id")
                for item in _sequence(
                    data.get("portal_ids", ()), "room.portal_ids")
            ),
            egress=egress,
            memory_scope=_text(data.get("memory_scope"), "room.memory_scope"),
            capability_manifest_id=_text(
                data.get("capability_manifest_id"),
                "room.capability_manifest_id"),
            activation_state=_text(
                data.get("activation_state"), "room.activation_state"),
            status=_text(data.get("status", "draft"), "room.status"),
            style=dict(_mapping(data.get("style", {}), "room.style")),
            assets=tuple(
                MapAssetRef.from_dict(item)
                for item in _sequence(data.get("assets", ()), "room.assets")
            ),
        )


@dataclass(frozen=True)
class MapFloor:
    floor_id: str
    index: int
    wing: str
    name: str
    z_index: int
    activation_state: str
    room_ids: Tuple[str, ...]
    entry_room_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "floor_id": self.floor_id,
            "index": int(self.index),
            "wing": self.wing,
            "name": self.name,
            "z_index": int(self.z_index),
            "activation_state": self.activation_state,
            "room_ids": list(self.room_ids),
            "entry_room_id": self.entry_room_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MapFloor":
        if not isinstance(data, Mapping):
            raise MapSchemaError("floor must be a mapping")
        _check_keys(
            data,
            frozenset(
                {"floor_id", "index", "wing", "name", "z_index",
                 "activation_state", "room_ids", "entry_room_id"}
            ),
            "floor",
        )
        return cls(
            floor_id=_text(data.get("floor_id"), "floor.floor_id"),
            index=_int(data.get("index"), "floor.index"),
            wing=_text(data.get("wing"), "floor.wing"),
            name=_text(data.get("name"), "floor.name"),
            z_index=_int(data.get("z_index"), "floor.z_index"),
            activation_state=_text(
                data.get("activation_state"), "floor.activation_state"),
            room_ids=tuple(
                _text(item, "floor.room_id")
                for item in _sequence(
                    data.get("room_ids", ()), "floor.room_ids")
            ),
            entry_room_id=_text(
                data.get("entry_room_id"), "floor.entry_room_id"),
        )


@dataclass(frozen=True)
class MapDocument:
    """A complete, versioned campus map. Immutable once constructed."""

    schema: str
    version: int
    campus_id: str
    map_id: str
    owner_id: str
    tenant_id: str
    status: str
    activation_state: str
    memory_scope: str
    capability_manifest_id: str
    floors: Tuple[MapFloor, ...] = ()
    rooms: Tuple[MapRoom, ...] = ()
    cells: Tuple[MapCell, ...] = ()
    desks: Tuple[MapDesk, ...] = ()
    portals: Tuple[MapPortal, ...] = ()
    connectivity: Mapping[str, Any] = field(default_factory=dict)
    capacity: int = 0
    egress: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "version": int(self.version),
            "campus_id": self.campus_id,
            "map_id": self.map_id,
            "owner_id": self.owner_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "activation_state": self.activation_state,
            "memory_scope": self.memory_scope,
            "capability_manifest_id": self.capability_manifest_id,
            "floors": [floor.to_dict() for floor in self.floors],
            "rooms": [room.to_dict() for room in self.rooms],
            "cells": [cell.to_dict() for cell in self.cells],
            "desks": [desk.to_dict() for desk in self.desks],
            "portals": [portal.to_dict() for portal in self.portals],
            "connectivity": dict(self.connectivity),
            "capacity": int(self.capacity),
            "egress": int(self.egress),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MapDocument":
        if not isinstance(data, Mapping):
            raise MapSchemaError("map must be a mapping")
        _check_keys(
            data,
            frozenset(
                {"schema", "version", "campus_id", "map_id", "owner_id",
                 "tenant_id", "status", "activation_state", "memory_scope",
                 "capability_manifest_id", "floors", "rooms", "cells",
                 "desks", "portals", "connectivity", "capacity", "egress"}
            ),
            "map",
        )
        return cls(
            schema=_text(data.get("schema"), "map.schema"),
            version=_int(data.get("version"), "map.version"),
            campus_id=_text(data.get("campus_id"), "map.campus_id"),
            map_id=_text(data.get("map_id"), "map.map_id"),
            owner_id=_text(data.get("owner_id"), "map.owner_id"),
            tenant_id=_text(data.get("tenant_id"), "map.tenant_id"),
            status=_text(data.get("status"), "map.status"),
            activation_state=_text(
                data.get("activation_state"), "map.activation_state"),
            memory_scope=_text(data.get("memory_scope"), "map.memory_scope"),
            capability_manifest_id=_text(
                data.get("capability_manifest_id"),
                "map.capability_manifest_id"),
            floors=tuple(
                MapFloor.from_dict(item)
                for item in _sequence(data.get("floors", ()), "map.floors")
            ),
            rooms=tuple(
                MapRoom.from_dict(item)
                for item in _sequence(data.get("rooms", ()), "map.rooms")
            ),
            cells=tuple(
                MapCell.from_dict(item)
                for item in _sequence(data.get("cells", ()), "map.cells")
            ),
            desks=tuple(
                MapDesk.from_dict(item)
                for item in _sequence(data.get("desks", ()), "map.desks")
            ),
            portals=tuple(
                MapPortal.from_dict(item)
                for item in _sequence(data.get("portals", ()), "map.portals")
            ),
            connectivity=dict(
                _mapping(data.get("connectivity", {}), "map.connectivity")),
            capacity=_int(data.get("capacity", 0), "map.capacity"),
            egress=_int(data.get("egress", 0), "map.egress"),
        )

    def digest(self) -> str:
        return map_digest(self)


# ------------------------------------------------------------------- parsing
def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MapSchemaError(f"{label} must be a non-empty string")
    return value


def _int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MapSchemaError(f"{label} must be an integer")
    return int(value)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MapSchemaError(f"{label} must be a mapping")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise MapSchemaError(f"{label} must be a list")
    return value


def _check_keys(
    data: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise MapSchemaError(
            f"{label} has unknown field(s): {', '.join(sorted(unknown))}")


# ------------------------------------------------------------------- summary
def _floor_rooms(doc: MapDocument) -> Dict[str, List[MapRoom]]:
    by_floor: Dict[str, List[MapRoom]] = {f.floor_id: [] for f in doc.floors}
    for room in doc.rooms:
        by_floor.setdefault(room.floor_id, []).append(room)
    return by_floor


def _reachable(doc: MapDocument, floor: MapFloor) -> Tuple[str, ...]:
    rooms = {r.room_id for r in doc.rooms if r.floor_id == floor.floor_id}
    if floor.entry_room_id not in rooms:
        return ()
    adjacency: Dict[str, set[str]] = {room_id: set() for room_id in rooms}
    for portal in doc.portals:
        if portal.source_room_id in rooms and portal.target_room_id in rooms:
            adjacency[portal.source_room_id].add(portal.target_room_id)
            if portal.bidirectional:
                adjacency[portal.target_room_id].add(portal.source_room_id)
    seen = {floor.entry_room_id}
    queue = [floor.entry_room_id]
    while queue:
        current = queue.pop(0)
        for nxt in sorted(adjacency.get(current, ())):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return tuple(sorted(seen))


def compute_summary(doc: MapDocument) -> Dict[str, Any]:
    """Recompute the document's connectivity/capacity/egress from its records."""
    connectivity: Dict[str, Any] = {}
    for floor in sorted(doc.floors, key=lambda f: f.index):
        reachable = _reachable(doc, floor)
        floor_rooms = [r for r in doc.rooms if r.floor_id == floor.floor_id]
        unreachable = sorted(
            r.room_id for r in floor_rooms if r.room_id not in reachable)
        desk_total = sum(
            len(r.desk_ids) for r in floor_rooms if r.room_id in reachable)
        connectivity[floor.floor_id] = {
            "entry_room_id": floor.entry_room_id,
            "reachable_room_ids": list(reachable),
            "unreachable_room_ids": unreachable,
            "reachable_desk_count": int(desk_total),
            "room_count": len(floor_rooms),
        }
    capacity = sum(room.capacity for room in doc.rooms)
    return {
        "connectivity": connectivity,
        "capacity": int(capacity),
        "egress": sum(1 for portal in doc.portals),
    }


def map_digest(doc: MapDocument) -> str:
    """Stable content hash of the canonical document."""
    return hashlib.sha256(
        json.dumps(doc.to_dict(), sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------- validation
def _asset_problems(room_id: str, asset: MapAssetRef) -> List[str]:
    out: List[str] = []
    where = f"room {room_id!r} asset {asset.asset_id!r}"
    if not _ID_RE.match(asset.asset_id):
        out.append(f"{where}: invalid asset_id")
    if asset.kind not in ASSET_KINDS:
        out.append(
            f"{where}: kind {asset.kind!r} not one of {ASSET_KINDS}")
    if not _SAFE_REF_RE.match(asset.ref):
        return out + [f"{where}: unsafe ref"]
    ref = asset.ref.strip()
    low = ref.lower()
    if ".." in ref.split("/") or ref.startswith(("/", "\\")):
        return out + [f"{where}: ref escapes the asset root"]
    if asset.paid:
        return out + [f"{where}: paid art may not be embedded"]
    paid_marker = any(marker in low for marker in _PAID_ART_MARKERS)
    if paid_marker and asset.kind != "recipe":
        out.append(f"{where}: references a paid art pack")
    if paid_marker and low.endswith(_RAW_ART_SUFFIXES):
        out.append(f"{where}: paid art file is not a recipe coordinate")
    if asset.kind == "tileset" and low.endswith(_RAW_ART_SUFFIXES):
        out.append(f"{where}: raw sprite sheets are not embedded")
    if asset.kind in ("tileset", "recipe"):
        if asset.sha256 is None or not _SHA256_RE.match(asset.sha256):
            out.append(f"{where}: {asset.kind} ref needs a sha256 content hash")
    return out


def validate_map(doc: MapDocument) -> List[str]:
    """Return every structural defect, or ``[]``. Pure and deterministic."""
    problems: List[str] = []
    if doc.schema != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {doc.schema!r}")
    if doc.version != VERSION:
        problems.append(f"version: expected {VERSION}, got {doc.version}")
    for label, value in (
        ("campus_id", doc.campus_id),
        ("map_id", doc.map_id),
        ("owner_id", doc.owner_id),
        ("tenant_id", doc.tenant_id),
        ("memory_scope", doc.memory_scope),
        ("capability_manifest_id", doc.capability_manifest_id),
    ):
        if not value or not _SCOPE_RE.match(value):
            problems.append(f"{label}: missing or invalid")
    if doc.status not in DOC_STATUSES:
        problems.append(f"status: {doc.status!r} not one of {DOC_STATUSES}")
    if doc.activation_state not in ACTIVATION_STATES:
        problems.append(
            f"activation_state: {doc.activation_state!r} not one of"
            f" {ACTIVATION_STATES}")
    if not doc.floors:
        problems.append("map: has no floors")
    if not doc.rooms:
        problems.append("map: has no rooms")

    floor_ids: set[str] = set()
    floor_indexes: set[int] = set()
    room_ids: set[str] = set()
    cell_ids: set[str] = set()
    desk_ids: set[str] = set()
    seat_ids: set[str] = set()
    portal_ids: set[str] = set()

    rooms = {room.room_id: room for room in doc.rooms}
    cells = {cell.cell_id: cell for cell in doc.cells}
    desks = {desk.desk_id: desk for desk in doc.desks}

    for floor in doc.floors:
        if floor.floor_id in floor_ids:
            problems.append(f"floor {floor.floor_id!r}: duplicate id")
        floor_ids.add(floor.floor_id)
        if floor.index in floor_indexes:
            problems.append(f"floor {floor.floor_id!r}: duplicate index")
        floor_indexes.add(floor.index)
        if floor.index <= 0:
            problems.append(f"floor {floor.floor_id!r}: index must be positive")
        if not floor.wing or not _SCOPE_RE.match(floor.wing):
            problems.append(f"floor {floor.floor_id!r}: invalid wing")
        if floor.activation_state not in ACTIVATION_STATES:
            problems.append(
                f"floor {floor.floor_id!r}: invalid activation_state")
        if not floor.room_ids:
            problems.append(f"floor {floor.floor_id!r}: has no rooms")
        for room_id in floor.room_ids:
            if room_id not in rooms:
                problems.append(
                    f"floor {floor.floor_id!r}: unknown room {room_id!r}")
        if floor.entry_room_id not in floor.room_ids:
            problems.append(
                f"floor {floor.floor_id!r}: entry room"
                f" {floor.entry_room_id!r} is not on the floor")

    for room in doc.rooms:
        if room.room_id in room_ids:
            problems.append(f"room {room.room_id!r}: duplicate id")
        room_ids.add(room.room_id)
        if room.floor_id not in floor_ids:
            problems.append(
                f"room {room.room_id!r}: unknown floor {room.floor_id!r}")
        if room.capacity <= 0:
            problems.append(f"room {room.room_id!r}: capacity must be positive")
        if room.capacity != len(room.desk_ids):
            problems.append(
                f"room {room.room_id!r}: capacity {room.capacity} != "
                f"{len(room.desk_ids)} desks")
        if not room.desk_ids:
            problems.append(f"room {room.room_id!r}: has no desks")
        if not room.egress:
            problems.append(f"room {room.room_id!r}: has no egress")
        for x, y in room.egress:
            if not (0 <= x < CELL_COLS and 0 <= y < CELL_ROWS):
                problems.append(
                    f"room {room.room_id!r}: egress {(x, y)} outside the cell")
        if room.activation_state not in ACTIVATION_STATES:
            problems.append(f"room {room.room_id!r}: invalid activation_state")
        if room.status not in DOC_STATUSES:
            problems.append(f"room {room.room_id!r}: invalid status")
        if not room.memory_scope or not _SCOPE_RE.match(room.memory_scope):
            problems.append(f"room {room.room_id!r}: invalid memory_scope")
        if not room.capability_manifest_id or not _SCOPE_RE.match(
            room.capability_manifest_id
        ):
            problems.append(
                f"room {room.room_id!r}: invalid capability_manifest_id")
        for cell_id in room.cell_ids:
            if cell_id not in cells:
                problems.append(
                    f"room {room.room_id!r}: unknown cell {cell_id!r}")
        for desk_id in room.desk_ids:
            if desk_id not in desks:
                problems.append(
                    f"room {room.room_id!r}: unknown desk {desk_id!r}")
        for portal_id in room.portal_ids:
            if portal_id not in portal_ids and portal_id not in {
                p.portal_id for p in doc.portals
            }:
                problems.append(
                    f"room {room.room_id!r}: unknown portal {portal_id!r}")
        for asset in room.assets:
            problems.extend(_asset_problems(room.room_id, asset))

    for cell in doc.cells:
        if cell.cell_id in cell_ids:
            problems.append(f"cell {cell.cell_id!r}: duplicate id")
        cell_ids.add(cell.cell_id)
        if cell.room_id not in room_ids:
            problems.append(
                f"cell {cell.cell_id!r}: unknown room {cell.room_id!r}")
        else:
            room = rooms[cell.room_id]
            if cell.cell_id not in room.cell_ids:
                problems.append(
                    f"cell {cell.cell_id!r}: not listed on room"
                    f" {cell.room_id!r}")
        if not cell.standard:
            problems.append(
                f"cell {cell.cell_id!r}: {cell.cols}x{cell.rows} is not the"
                f" {CELL_COLS}x{CELL_ROWS} contract")
        if cell.x < 0 or cell.y < 0:
            problems.append(f"cell {cell.cell_id!r}: negative origin")
        for desk_id in cell.desk_ids:
            if desk_id not in desks:
                problems.append(
                    f"cell {cell.cell_id!r}: unknown desk {desk_id!r}")

    for desk in doc.desks:
        if desk.desk_id in desk_ids:
            problems.append(f"desk {desk.desk_id!r}: duplicate id")
        desk_ids.add(desk.desk_id)
        if desk.seat_id in seat_ids:
            problems.append(f"seat {desk.seat_id!r}: duplicate id")
        seat_ids.add(desk.seat_id)
        if desk.room_id not in room_ids:
            problems.append(
                f"desk {desk.desk_id!r}: unknown room {desk.room_id!r}")
        if desk.cell_id not in cell_ids:
            problems.append(
                f"desk {desk.desk_id!r}: unknown cell {desk.cell_id!r}")
        else:
            cell = cells[desk.cell_id]
            if desk.room_id in rooms and (
                desk.cell_id not in rooms[desk.room_id].cell_ids
            ):
                problems.append(
                    f"desk {desk.desk_id!r}: cell is not on its room")
            if not (0 <= desk.x and 0 <= desk.y
                    and desk.x + desk.width <= cell.cols
                    and desk.y + desk.height <= cell.rows):
                problems.append(
                    f"desk {desk.desk_id!r}: tile ({desk.x},{desk.y}) does not"
                    f" fit the {cell.cols}x{cell.rows} cell")
        if not desk.seat_kind:
            problems.append(f"desk {desk.desk_id!r}: missing seat_kind")

    for portal in doc.portals:
        if portal.portal_id in portal_ids:
            problems.append(f"portal {portal.portal_id!r}: duplicate id")
        portal_ids.add(portal.portal_id)
        if portal.kind not in PORTAL_KINDS:
            problems.append(
                f"portal {portal.portal_id!r}: unknown kind {portal.kind!r}")
        source = rooms.get(portal.source_room_id)
        target = rooms.get(portal.target_room_id)
        if source is None:
            problems.append(
                f"portal {portal.portal_id!r}: unknown source room"
                f" {portal.source_room_id!r}")
        if target is None:
            problems.append(
                f"portal {portal.portal_id!r}: unknown target room"
                f" {portal.target_room_id!r}")
        if portal.floor_id not in floor_ids:
            problems.append(
                f"portal {portal.portal_id!r}: unknown floor"
                f" {portal.floor_id!r}")
        elif source is not None and target is not None:
            if portal.floor_id not in (source.floor_id, target.floor_id):
                problems.append(
                    f"portal {portal.portal_id!r}: floor does not match either"
                    f" room")
        if not (0 <= portal.x < CELL_COLS and 0 <= portal.y < CELL_ROWS):
            problems.append(
                f"portal {portal.portal_id!r}: door tile"
                f" ({portal.x},{portal.y}) outside the cell")
        if source is not None and portal.portal_id not in source.portal_ids:
            problems.append(
                f"portal {portal.portal_id!r}: not listed on source room"
                f" {source.room_id!r}")

    # Every desk must be reachable from its floor's entry room. A room with no
    # path is a named defect; the renderer may not paper over it.
    summary = compute_summary(doc)
    for floor in doc.floors:
        info = summary["connectivity"].get(floor.floor_id, {})
        for room_id in info.get("unreachable_room_ids", ()):
            room = rooms[room_id]
            problems.append(
                f"floor {floor.floor_id!r}: unreachable room {room_id!r}"
                f" ({len(room.desk_ids)} unreachable desks)")
    if summary["capacity"] != doc.capacity:
        problems.append(
            f"capacity: document says {doc.capacity}, records total"
            f" {summary['capacity']}")
    if summary["egress"] != doc.egress:
        problems.append(
            f"egress: document says {doc.egress}, records total"
            f" {summary['egress']}")
    if dict(doc.connectivity) != summary["connectivity"]:
        problems.append("connectivity: does not match the portal graph")
    return problems


def assert_valid_map(doc: MapDocument) -> None:
    problems = validate_map(doc)
    if problems:
        raise MapSchemaError(
            f"invalid map: {problems[0]}", problems=problems)


# ------------------------------------------------------------------ builder
def _style_for(kind: str) -> Dict[str, Any]:
    return {
        "archetype": kind,
        "shadow": "shadowless",
        "palette": "qubit_matte",
    }


def _asset_for(room: MapRoom) -> Tuple[MapAssetRef, ...]:
    descriptor = json.dumps(
        {"room": room.room_id, "kind": room.kind, "style": dict(room.style)},
        sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
    return (
        MapAssetRef(
            asset_id=f"style:{room.kind}",
            kind="recipe",
            ref=f"recipes/{room.kind}",
            sha256=digest,
        ),
    )


def build_campus_map(
    plan: Any,
    *,
    campus_id: str = "campus-qubit-office",
    map_id: str = "campus-main",
    owner_id: str = "owner:template",
    tenant_id: str = "tenant:template",
    status: str = "draft",
    activation_state: str = "active",
) -> MapDocument:
    """Project a :class:`TowerPlan` into a validated :class:`MapDocument`.

    No tenant, business or agent record is placed; ``owner_id``/``tenant_id``
    default to explicit template markers so a fixture is never mistaken for
    production state.
    """
    floors: List[MapFloor] = []
    rooms: List[MapRoom] = []
    cells: List[MapCell] = []
    desks: List[MapDesk] = []
    portals: List[MapPortal] = []
    room_portal_ids: Dict[str, List[str]] = {}
    previous_entry: Optional[str] = None
    desk_seq = 0

    for floor in plan.floors:
        floor_room_ids: List[str] = []
        floor_rooms: List[Any] = list(floor.rooms)
        for room_index, room_plan in enumerate(floor_rooms):
            room_cells: List[MapCell] = []
            room_desks: List[MapDesk] = []
            for cell_plan in room_plan.cells:
                cell = MapCell(
                    cell_id=cell_plan.cell_id,
                    room_id=room_plan.room_id,
                    index=int(cell_plan.index),
                    x=int(cell_plan.origin[0]),
                    y=int(cell_plan.origin[1]),
                    cols=int(cell_plan.cols),
                    rows=int(cell_plan.rows),
                    desk_ids=tuple(d.desk_id for d in cell_plan.desks),
                )
                cells.append(cell)
                room_cells.append(cell)
                for desk_plan in cell_plan.desks:
                    desk_seq += 1
                    room_desks.append(MapDesk(
                        desk_id=desk_plan.desk_id,
                        seat_id=desk_plan.seat_id,
                        cell_id=cell_plan.cell_id,
                        room_id=room_plan.room_id,
                        x=int(desk_plan.tile[0]),
                        y=int(desk_plan.tile[1]),
                        width=int(desk_plan.width),
                        height=int(desk_plan.height),
                        seat_kind=room_plan.kind,
                    ))
            desks.extend(room_desks)
            room = MapRoom(
                room_id=room_plan.room_id,
                floor_id=floor.floor_id,
                name=room_plan.name,
                kind=room_plan.kind,
                archetype=room_plan.archetype,
                capacity=len(room_desks),
                cell_ids=tuple(c.cell_id for c in room_cells),
                desk_ids=tuple(d.desk_id for d in room_desks),
                egress=((CELL_COLS // 2, CELL_ROWS - 1),),
                memory_scope=f"memory:{floor.floor_id}",
                capability_manifest_id=f"capability:{room_plan.kind}:{room_plan.room_id}",
                activation_state="active" if floor.active else "dormant",
                status=status,
                style=_style_for(room_plan.archetype),
            )
            room = _with_assets(room)
            rooms.append(room)
            floor_room_ids.append(room.room_id)
            room_portal_ids[room.room_id] = []

        entry_room_id = floor_room_ids[0]
        for room_index, room_id in enumerate(floor_room_ids[1:], start=1):
            portal_id = f"p:{floor.floor_id}:{entry_room_id}->{room_id}"
            portal = MapPortal(
                portal_id=portal_id,
                floor_id=floor.floor_id,
                source_room_id=entry_room_id,
                target_room_id=room_id,
                kind="door",
                x=(room_index * 3) % CELL_COLS,
                y=CELL_ROWS - 1,
            )
            portals.append(portal)
            room_portal_ids[entry_room_id].append(portal_id)
            room_portal_ids[room_id].append(portal_id)
        if previous_entry is not None:
            portal_id = f"p:{floor.floor_id}:elevator->{previous_entry}"
            portal = MapPortal(
                portal_id=portal_id,
                floor_id=floor.floor_id,
                source_room_id=entry_room_id,
                target_room_id=previous_entry,
                kind="elevator",
                x=1,
                y=1,
            )
            portals.append(portal)
            room_portal_ids[entry_room_id].append(portal_id)
            room_portal_ids[previous_entry].append(portal_id)
        previous_entry = entry_room_id

        floors.append(MapFloor(
            floor_id=floor.floor_id,
            index=int(floor.index),
            wing=str(floor.wing),
            name=floor.name,
            z_index=int(floor.index),
            activation_state="active" if floor.active else "dormant",
            room_ids=tuple(floor_room_ids),
            entry_room_id=entry_room_id,
        ))

    # Attach the portal ids now that every portal exists.
    rooms = [
        replace(room, portal_ids=tuple(room_portal_ids.get(room.room_id, ())))
        for room in rooms
    ]

    partial = MapDocument(
        schema=SCHEMA,
        version=VERSION,
        campus_id=campus_id,
        map_id=map_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        status=status,
        activation_state=activation_state,
        memory_scope=f"memory:campus:{campus_id}",
        capability_manifest_id=f"capability:campus:{campus_id}",
        floors=tuple(floors),
        rooms=tuple(rooms),
        cells=tuple(cells),
        desks=tuple(desks),
        portals=tuple(portals),
    )
    summary = compute_summary(partial)
    return MapDocument(
        schema=partial.schema,
        version=partial.version,
        campus_id=partial.campus_id,
        map_id=partial.map_id,
        owner_id=partial.owner_id,
        tenant_id=partial.tenant_id,
        status=partial.status,
        activation_state=partial.activation_state,
        memory_scope=partial.memory_scope,
        capability_manifest_id=partial.capability_manifest_id,
        floors=partial.floors,
        rooms=partial.rooms,
        cells=partial.cells,
        desks=partial.desks,
        portals=partial.portals,
        connectivity=summary["connectivity"],
        capacity=summary["capacity"],
        egress=summary["egress"],
    )


def _with_assets(room: MapRoom) -> MapRoom:
    assets = _asset_for(room)
    return MapRoom(
        room_id=room.room_id,
        floor_id=room.floor_id,
        name=room.name,
        kind=room.kind,
        archetype=room.archetype,
        capacity=room.capacity,
        cell_ids=room.cell_ids,
        desk_ids=room.desk_ids,
        portal_ids=room.portal_ids,
        egress=room.egress,
        memory_scope=room.memory_scope,
        capability_manifest_id=room.capability_manifest_id,
        activation_state=room.activation_state,
        status=room.status,
        style=room.style,
        assets=assets,
    )


# ------------------------------------------------------------------- registry
@dataclass(frozen=True)
class MapVersion:
    map_id: str
    version: int
    digest: str
    document: MapDocument

    def to_dict(self) -> Dict[str, Any]:
        return {
            "map_id": self.map_id,
            "version": int(self.version),
            "digest": self.digest,
            "document": self.document.to_dict(),
        }


class MapRegistry:
    """Append-only, in-memory map versions with a movable current pointer.

    ``publish`` validates before appending and never leaves a partial record.
    ``undo`` moves the current pointer back one version while keeping history;
    ``rollback`` moves it to any published version. Content hashes are carried
    per version so two restores of the same content compare equal.
    """

    def __init__(self) -> None:
        self._history: Dict[str, List[MapVersion]] = {}
        self._current: Dict[str, int] = {}

    # -- reads ----------------------------------------------------------
    def versions(self, map_id: str) -> Tuple[MapVersion, ...]:
        return tuple(self._history.get(map_id, ()))

    def current(self, map_id: str) -> MapVersion:
        history = self._history.get(map_id) or []
        index = self._current.get(map_id)
        if not history or index is None:
            raise MapNotFound(f"map {map_id!r} has no published version")
        return history[index]

    def get(self, map_id: str, version: int) -> MapVersion:
        for index, record in enumerate(self._history.get(map_id, ())):
            if record.version == version:
                return record
        raise MapNotFound(f"map {map_id!r} has no version {version}")

    # -- writes ---------------------------------------------------------
    def publish(
        self, document: MapDocument, *, expected_version: Optional[int] = None
    ) -> MapVersion:
        """Validate then append one version. Never partially publishes."""
        assert_valid_map(document)
        history = self._history.setdefault(document.map_id, [])
        current = history[-1].version if history else 0
        if expected_version is not None and expected_version != current:
            raise MapVersionConflict(
                f"map {document.map_id!r}: expected version"
                f" {expected_version}, history tip is {current}")
        record = MapVersion(
            map_id=document.map_id,
            version=current + 1,
            digest=map_digest(document),
            document=document,
        )
        history.append(record)
        self._current[document.map_id] = len(history) - 1
        return record

    def undo(self, map_id: str) -> MapVersion:
        history = self._history.get(map_id) or []
        if len(history) < 2 or self._current.get(map_id, 0) <= 0:
            raise MapUndoUnavailable(f"map {map_id!r}: nothing to undo")
        self._current[map_id] -= 1
        return history[self._current[map_id]]

    def rollback(self, map_id: str, version: int) -> MapVersion:
        """Point the map at an already-published version (history is kept)."""
        for index, record in enumerate(self._history.get(map_id, ())):
            if record.version == version:
                self._current[map_id] = index
                return record
        raise MapNotFound(f"map {map_id!r} has no version {version}")


# --------------------------------------------------------------------- tiled
def _tiled_properties(pairs: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"name": name, "type": "string", "value": value}
        for name, value in pairs.items()
    ]


def _tiled_object(
    object_id: int,
    name: str,
    x: int,
    y: int,
    width: int,
    height: int,
    properties: Mapping[str, Any],
    *,
    point: bool = False,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": object_id,
        "name": name,
        "x": int(x),
        "y": int(y),
        "width": 0 if point else int(width),
        "height": 0 if point else int(height),
        "rotation": 0,
        "visible": True,
        "properties": _tiled_properties(properties),
    }
    if point:
        out["point"] = True
    return out


def map_to_tiled(doc: MapDocument) -> Dict[str, Any]:
    """Export a Tiled-compatible JSON map plus the canonical document."""
    floors_by_id = {floor.floor_id: floor for floor in doc.floors}
    rooms_by_floor: Dict[str, List[MapRoom]] = {fid: [] for fid in floors_by_id}
    for room in doc.rooms:
        rooms_by_floor.setdefault(room.floor_id, []).append(room)
    cells_by_room: Dict[str, List[MapCell]] = {r.room_id: [] for r in doc.rooms}
    for cell in doc.cells:
        cells_by_room.setdefault(cell.room_id, []).append(cell)
    desks_by_room: Dict[str, List[MapDesk]] = {r.room_id: [] for r in doc.rooms}
    for desk in doc.desks:
        desks_by_room.setdefault(desk.room_id, []).append(desk)

    max_index = max((f.index for f in doc.floors), default=1)
    offset_unit = (CELL_ROWS + 2) * 3
    layers: List[Dict[str, Any]] = []
    next_layer = 1
    next_object = 1
    for floor in sorted(doc.floors, key=lambda f: f.index):
        offset = (floor.index - 1) * offset_unit
        room_objects: List[Dict[str, Any]] = []
        cell_objects: List[Dict[str, Any]] = []
        desk_objects: List[Dict[str, Any]] = []
        portal_objects: List[Dict[str, Any]] = []
        for room in rooms_by_floor.get(floor.floor_id, ()):
            room_cells = cells_by_room.get(room.room_id, [])
            width = max(
                (c.x + c.cols for c in room_cells), default=CELL_COLS)
            height = max(
                (c.y + c.rows for c in room_cells), default=CELL_ROWS)
            room_objects.append(_tiled_object(
                next_object, room.name, 0, offset, width, height,
                {
                    "room_id": room.room_id,
                    "kind": room.kind,
                    "archetype": room.archetype,
                    "capacity": str(room.capacity),
                    "memory_scope": room.memory_scope,
                    "capability_manifest_id": room.capability_manifest_id,
                    "activation_state": room.activation_state,
                    "status": room.status,
                    "style": json.dumps(dict(room.style), sort_keys=True),
                    "assets": json.dumps(
                        [asset.to_dict() for asset in room.assets],
                        sort_keys=True),
                },
            ))
            next_object += 1
            for cell in room_cells:
                cell_objects.append(_tiled_object(
                    next_object, cell.cell_id, cell.x, offset + cell.y,
                    cell.cols, cell.rows,
                    {
                        "cell_id": cell.cell_id,
                        "room_id": cell.room_id,
                        "index": str(cell.index),
                        "cols": str(cell.cols),
                        "rows": str(cell.rows),
                    },
                ))
                next_object += 1
            for desk in desks_by_room.get(room.room_id, ()):
                cell = next(
                    (c for c in room_cells if c.cell_id == desk.cell_id), None)
                base_x = cell.x if cell else 0
                base_y = cell.y if cell else 0
                desk_objects.append(_tiled_object(
                    next_object, desk.desk_id,
                    base_x + desk.x, offset + base_y + desk.y,
                    desk.width, desk.height,
                    {
                        "desk_id": desk.desk_id,
                        "seat_id": desk.seat_id,
                        "cell_id": desk.cell_id,
                        "room_id": desk.room_id,
                        "seat_kind": desk.seat_kind,
                    },
                ))
                next_object += 1
        for portal in doc.portals:
            if portal.floor_id != floor.floor_id:
                continue
            portal_objects.append(_tiled_object(
                next_object, portal.portal_id, portal.x, offset + portal.y,
                0, 0,
                {
                    "portal_id": portal.portal_id,
                    "floor_id": portal.floor_id,
                    "source_room_id": portal.source_room_id,
                    "target_room_id": portal.target_room_id,
                    "kind": portal.kind,
                    "bidirectional": "true" if portal.bidirectional else "false",
                },
                point=True,
            ))
            next_object += 1
        group_layers: List[Dict[str, Any]] = []
        for layer_name, objects in (
            ("rooms", room_objects),
            ("cells", cell_objects),
            ("desks", desk_objects),
            ("portals", portal_objects),
        ):
            if not objects:
                continue
            group_layers.append({
                "id": next_layer,
                "name": f"{floor.floor_id}:{layer_name}",
                "type": "objectgroup",
                "draworder": "topdown",
                "opacity": 1,
                "visible": True,
                "objects": objects,
            })
            next_layer += 1
        layers.append({
            "id": next_layer,
            "name": floor.floor_id,
            "type": "group",
            "opacity": 1,
            "visible": floor.activation_state == "active",
            "layers": group_layers,
            "properties": _tiled_properties({
                "floor_id": floor.floor_id,
                "index": str(floor.index),
                "wing": floor.wing,
                "z_index": str(floor.z_index),
                "activation_state": floor.activation_state,
                "entry_room_id": floor.entry_room_id,
            }),
        })
        next_layer += 1

    document_json = json.dumps(
        doc.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        "type": "map",
        "version": TILED_VERSION,
        "tiledversion": "1.10.2",
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "infinite": False,
        "width": max(CELL_COLS * 2 + 2, 16),
        "height": max_index * offset_unit,
        "tilewidth": TILED_TILEWIDTH,
        "tileheight": TILED_TILEHEIGHT,
        "nextlayerid": next_layer,
        "nextobjectid": next_object,
        "tilesets": [],
        "layers": layers,
        "properties": _tiled_properties({
            TILED_SCHEMA_PROPERTY: SCHEMA,
            TILED_VERSION_PROPERTY: str(VERSION),
            TILED_DOCUMENT_PROPERTY: document_json,
        }),
    }


def _property_map(data: Mapping[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in _sequence(data.get("properties", ()), "tiled.properties"):
        entry = _mapping(item, "tiled.property")
        name = entry.get("name")
        value = entry.get("value")
        if isinstance(name, str):
            out[name] = value if isinstance(value, str) else json.dumps(value)
    return out


def tiled_to_map(data: Any) -> MapDocument:
    """Import the canonical document from a Tiled map.

    Layer objects are render hints only; a Tiled map without the canonical
    ``qubit:document`` property is a typed refusal rather than an invented
    campus.
    """
    if not isinstance(data, Mapping):
        raise MapSchemaError(
            "tiled map must be a mapping", code="OFFICE_MAP_NOT_TILED",
            status=415)
    if data.get("type") != "map":
        raise MapSchemaError(
            "tiled file is not a map", code="OFFICE_MAP_NOT_TILED",
            status=415)
    try:
        tilewidth = int(data.get("tilewidth", 0) or 0)
        tileheight = int(data.get("tileheight", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise MapSchemaError(
            "tiled map tile size is not numeric",
            code="OFFICE_MAP_TILE_SIZE", status=400) from exc
    if tilewidth != TILED_TILEWIDTH or tileheight != TILED_TILEHEIGHT:
        raise MapSchemaError(
            f"tiled map must use {TILED_TILEWIDTH}x{TILED_TILEHEIGHT} tiles",
            code="OFFICE_MAP_TILE_SIZE", status=400)
    properties = _property_map(data)
    raw = properties.get(TILED_DOCUMENT_PROPERTY)
    if not raw:
        raise MapSchemaError(
            "tiled map carries no canonical qubit:document property",
            code="OFFICE_MAP_NO_BUSINESS_DATA", status=422)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MapSchemaError(
            f"canonical map property is not JSON: {exc}",
            code="OFFICE_MAP_BAD_DOCUMENT", status=400) from exc
    document = MapDocument.from_dict(payload)
    assert_valid_map(document)
    return document
