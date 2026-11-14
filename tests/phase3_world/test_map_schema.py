"""P3-03 — versioned map schema, Tiled round-trip and transactional registry.

Hermetic: the map is built from the pure tower projection; no store, clock,
network or renderer is involved. Invalid maps are refused with typed errors and
never partially published.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from voicebridge.business.roles import RoleRegistry
from voicebridge.office.world import map_schema as MS
from voicebridge.office.world import tower_plan as TP

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def document() -> MS.MapDocument:
    plan = TP.build_tower_plan(RoleRegistry.default())
    return MS.build_campus_map(plan)


def _asset(kind: str, ref: str, *, paid: bool = False) -> MS.MapAssetRef:
    return MS.MapAssetRef(
        asset_id="asset-test",
        kind=kind,
        ref=ref,
        sha256="a" * 64,
        paid=paid,
    )


def _room_with_asset(document, asset):
    room = document.rooms[0]
    return replace(
        document,
        rooms=(replace(room, assets=(asset,)), *document.rooms[1:]),
    )


def test_campus_map_is_valid_and_complete(document):
    assert MS.validate_map(document) == []
    assert document.schema == MS.SCHEMA
    assert document.version == MS.VERSION
    assert len(document.floors) == 100
    assert len(document.rooms) == 300
    assert len(document.cells) == 575
    assert len(document.desks) == 3075
    assert document.capacity == 3075
    assert document.egress == len(document.portals)


def test_every_room_is_reachable_from_its_floor_entry(document):
    for floor in document.floors:
        info = document.connectivity[floor.floor_id]
        assert info["unreachable_room_ids"] == []
        assert set(info["reachable_room_ids"]) == set(floor.room_ids)
        assert info["reachable_desk_count"] > 0


def test_tiled_round_trip_is_lossless(document):
    tiled = MS.map_to_tiled(document)
    assert tiled["type"] == "map"
    assert tiled["tilewidth"] == 16
    assert tiled["tileheight"] == 12
    restored = MS.tiled_to_map(tiled)
    assert restored == document
    assert restored.digest() == document.digest()


def test_stretched_cell_is_refused(document):
    cell = replace(document.cells[0], cols=24)
    bad = replace(document, cells=(cell, *document.cells[1:]))
    problems = MS.validate_map(bad)
    assert any("16x12" in problem for problem in problems)


def test_duplicate_ids_are_refused(document):
    bad = replace(
        document,
        rooms=(document.rooms[0], document.rooms[0], *document.rooms[2:]),
    )
    assert any("duplicate id" in problem for problem in MS.validate_map(bad))


def test_portal_to_unknown_room_is_refused(document):
    portal = MS.MapPortal(
        portal_id="p:bad",
        floor_id="floor-001",
        source_room_id=document.floors[0].entry_room_id,
        target_room_id="room-nowhere",
    )
    bad = replace(document, portals=(*document.portals, portal))
    problems = MS.validate_map(bad)
    assert any("unknown target room" in problem for problem in problems)


def test_door_outside_the_cell_is_refused(document):
    portal = MS.MapPortal(
        portal_id="p:outside",
        floor_id=document.floors[0].floor_id,
        source_room_id=document.floors[0].entry_room_id,
        target_room_id=document.floors[0].room_ids[1],
        x=99,
        y=99,
    )
    bad = replace(document, portals=(*document.portals, portal))
    assert any("outside the cell" in problem for problem in MS.validate_map(bad))


def test_unreachable_desks_are_refused(document):
    floor_id = document.floors[0].floor_id
    bad = replace(
        document,
        portals=tuple(p for p in document.portals if p.floor_id != floor_id),
    )
    problems = MS.validate_map(bad)
    assert any("unreachable" in problem and "desks" in problem
               for problem in problems)


def test_missing_egress_is_refused(document):
    room = replace(document.rooms[0], egress=())
    bad = replace(document, rooms=(room, *document.rooms[1:]))
    assert any("no egress" in problem for problem in MS.validate_map(bad))


def test_capacity_mismatch_is_refused(document):
    room = replace(document.rooms[0], capacity=document.rooms[0].capacity + 1)
    bad = replace(document, rooms=(room, *document.rooms[1:]))
    assert any("capacity" in problem for problem in MS.validate_map(bad))


def test_paid_art_is_refused(document):
    paid = _asset("tileset", "pack/office.png", paid=True)
    assert any(
        "paid art" in problem
        for problem in MS.validate_map(_room_with_asset(document, paid)))
    raw = _asset("tileset", "limezu/Office.png")
    assert any(
        "paid art" in problem or "raw sprite" in problem
        for problem in MS.validate_map(_room_with_asset(document, raw)))


def test_missing_owner_or_tenant_is_refused(document):
    assert any(
        "owner_id" in problem
        for problem in MS.validate_map(replace(document, owner_id="")))
    assert any(
        "tenant_id" in problem
        for problem in MS.validate_map(replace(document, tenant_id="")))


def test_schema_parser_rejects_unknown_fields_and_bad_activation(document):
    payload = document.to_dict()
    payload["secret_room"] = True
    with pytest.raises(MS.MapSchemaError):
        MS.MapDocument.from_dict(payload)
    bad = replace(document, activation_state="teleporting")
    assert any("activation_state" in problem for problem in MS.validate_map(bad))


def test_registry_publish_undo_and_rollback(document):
    registry = MS.MapRegistry()
    first = registry.publish(document)
    assert first.version == 1
    assert first.digest == MS.map_digest(document)
    second = registry.publish(replace(document, status="published"))
    assert second.version == 2
    assert registry.current(document.map_id).version == 2
    assert registry.undo(document.map_id).version == 1
    assert registry.current(document.map_id).version == 1
    # Undo keeps history; rollback moves the pointer to any published version.
    assert registry.rollback(document.map_id, 2).version == 2
    assert registry.current(document.map_id).version == 2
    assert registry.rollback(document.map_id, 1).version == 1
    assert [v.version for v in registry.versions(document.map_id)] == [1, 2]
    assert registry.publish(replace(document, status="archived")).version == 3


def test_registry_refuses_invalid_or_conflicting_publish(document):
    registry = MS.MapRegistry()
    registry.publish(document)
    registry.publish(replace(document, status="published"))
    before = len(registry.versions(document.map_id))
    with pytest.raises(MS.MapSchemaError):
        registry.publish(replace(document, status="nonsense"))
    assert len(registry.versions(document.map_id)) == before
    assert registry.current(document.map_id).version == 2
    with pytest.raises(MS.MapVersionConflict):
        registry.publish(document, expected_version=1)
    assert registry.current(document.map_id).version == 2
    assert len(registry.versions(document.map_id)) == before


def test_registry_undo_needs_a_previous_version(document):
    registry = MS.MapRegistry()
    registry.publish(document)
    with pytest.raises(MS.MapUndoUnavailable):
        registry.undo(document.map_id)


def test_tiled_import_refuses_a_map_without_business_data(document):
    tiled = MS.map_to_tiled(document)
    tiled["properties"] = []
    with pytest.raises(MS.MapSchemaError) as caught:
        MS.tiled_to_map(tiled)
    assert caught.value.code == "OFFICE_MAP_NO_BUSINESS_DATA"
    tiled = MS.map_to_tiled(document)
    tiled["tilewidth"] = 32
    with pytest.raises(MS.MapSchemaError) as caught:
        MS.tiled_to_map(tiled)
    assert caught.value.code == "OFFICE_MAP_TILE_SIZE"
