"""P6-02 — department/room builder over the authoritative map and style layers.

Hermetic: the sample document is built from pure data; no store, renderer,
network or clock is involved. Negative cases pin the plan's acceptance list:
stretched cell, unreachable desk, missing door, style collision and an invalid
asset must all be refused.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from voicebridge.business import room_builder as RB
from voicebridge.office.world import map_schema as MS

pytestmark = pytest.mark.unit


@pytest.fixture()
def document() -> MS.MapDocument:
    return RB.sample_document()


def _room(document: MS.MapDocument, room_id: str) -> MS.MapRoom:
    return next(room for room in document.rooms if room.room_id == room_id)


def test_sample_floor_is_complete_and_valid(document):
    assert RB.validate_room_builder_document(document) == []
    assert len(document.floors) == 1
    assert len(document.rooms) == len(RB.DEPARTMENT_ZONES)
    assert document.capacity == len(document.desks) == 17
    assert document.egress == len(document.portals)
    assert all(room.portal_ids for room in document.rooms)
    summary = MS.compute_summary(document)
    assert summary["capacity"] == document.capacity
    assert summary["egress"] == document.egress
    for floor in document.floors:
        info = summary["connectivity"][floor.floor_id]
        assert info["unreachable_room_ids"] == []
        assert info["reachable_desk_count"] == document.capacity


def test_sample_is_deterministic():
    first = RB.sample_document()
    second = RB.sample_document()
    assert MS.map_digest(first) == MS.map_digest(second)


def test_proposal_does_not_apply_and_apply_is_owner_action(document):
    builder = RB.RoomBuilder(document)
    proposal = builder.propose_add_room(
        floor_id="floor-001", room_id="extra-1", name="Extra",
        kind="storage", desks=2, proposed_by="agent:demo")
    assert proposal.valid
    assert proposal.proposed_by == "agent:demo"
    assert len(builder.document.rooms) == len(document.rooms)
    builder.apply(proposal, applied_by="owner:builder")
    assert len(builder.document.rooms) == len(document.rooms) + 1
    assert builder.revision == 1


def test_stale_proposal_is_a_typed_conflict(document):
    builder = RB.RoomBuilder(document)
    first = builder.propose_add_room(
        floor_id="floor-001", room_id="extra-1", name="Extra", kind="storage")
    builder.apply(first)
    with pytest.raises(RB.RoomBuilderConflict):
        builder.apply(first)


def test_publish_undo_redo_rollback(document):
    builder = RB.RoomBuilder(document)
    approval = builder.approve(approved_by="owner:test")
    assert approval["document_digest"] == MS.map_digest(document)
    first = builder.publish()
    assert first.version == 1
    assert builder.status == "published"

    proposal = builder.propose_add_room(
        floor_id="floor-001", room_id="extra-1", name="Extra", kind="storage")
    builder.apply(proposal)
    second = builder.publish()
    assert second.version == 2

    builder.undo()
    assert len(builder.document.rooms) == len(document.rooms)
    builder.redo()
    assert len(builder.document.rooms) == len(document.rooms) + 1
    restored = builder.rollback(1)
    assert restored.version == 1
    assert MS.map_digest(builder.document) == first.digest


def test_undo_without_history_is_typed(document):
    builder = RB.RoomBuilder(document)
    with pytest.raises(RB.RoomBuilderError) as excinfo:
        builder.undo()
    assert excinfo.value.code == "ROOM_BUILDER_UNDO_UNAVAILABLE"
    with pytest.raises(RB.RoomBuilderError) as redone:
        builder.redo()
    assert redone.value.code == "ROOM_BUILDER_REDO_UNAVAILABLE"


def test_add_and_remove_room(document):
    builder = RB.RoomBuilder(document)
    builder.apply(builder.propose_add_room(
        floor_id="floor-001", room_id="extra-1", name="Extra",
        kind="storage", desks=3))
    added = _room(builder.document, "extra-1")
    assert added.capacity == 3
    assert added.portal_ids
    assert not builder.validate()

    builder.apply(builder.propose_remove_room("extra-1"))
    assert all(room.room_id != "extra-1" for room in builder.document.rooms)
    assert all(room.room_id != "extra-1" for room in builder.document.cells)
    assert not builder.validate()


def test_add_room_refuses_duplicate_id(document):
    builder = RB.RoomBuilder(document)
    with pytest.raises(RB.RoomBuilderConflict):
        builder.propose_add_room(
            floor_id="floor-001", room_id="work-1", name="Dup", kind="work")


def test_cells_are_always_fixed_16x12(document):
    builder = RB.RoomBuilder(document)
    builder.apply(builder.propose_add_cell("work-1", desks=2))
    work = _room(builder.document, "work-1")
    assert len(work.cell_ids) == 2
    for cell in builder.document.cells:
        if cell.room_id == "work-1":
            assert (cell.cols, cell.rows) == (16, 12)
    builder.apply(builder.propose_remove_cell("work-1", work.cell_ids[-1]))
    work = _room(builder.document, "work-1")
    assert len(work.cell_ids) == 1
    assert not builder.validate()


def test_add_and_remove_desk_tracks_capacity(document):
    builder = RB.RoomBuilder(document)
    work = _room(document, "work-1")
    builder.apply(builder.propose_add_desk("work-1"))
    updated = _room(builder.document, "work-1")
    assert updated.capacity == work.capacity + 1
    desk_id = updated.desk_ids[-1]
    builder.apply(builder.propose_remove_desk(desk_id))
    restored = _room(builder.document, "work-1")
    assert restored.capacity == work.capacity
    assert not builder.validate()


def test_cell_capacity_ceiling(document):
    builder = RB.RoomBuilder(document)
    builder.apply(builder.propose_add_cell("work-1", desks=6))
    work = _room(builder.document, "work-1")
    cell_id = work.cell_ids[-1]
    with pytest.raises(RB.RoomBuilderError):
        builder.apply(builder.propose_add_desk("work-1", cell_id=cell_id))
    assert not builder.validate()


def test_style_controls_and_invalid_style(document):
    builder = RB.RoomBuilder(document)
    style = RB.default_style(
        "creative", 909, department_style="operations",
        signage="sign-creative")
    builder.apply(builder.propose_set_room_style("work-1", style))
    work = _room(builder.document, "work-1")
    assert work.style["palette"] == style.palette
    assert work.style["lighting"] == style.lighting
    assert work.style["signage"] == style.signage
    assert work.style["seed"] == style.seed

    bad = RB.RoomStyleSpec(**{
        **style.to_dict(), "palette": "not_a_palette"})
    with pytest.raises(RB.RoomBuilderError) as excinfo:
        builder.propose_set_room_style("work-1", bad)
    assert any("palette" in item for item in excinfo.value.problems)


def test_assignment_records_business_roles_agents_and_memory(document):
    builder = RB.RoomBuilder(document)
    assignment = RB.RoomAssignment(
        business_id="business:sample",
        department_id="operations",
        role_ids=("operations-lead", "operations-analyst"),
        agent_ids=("agent:demo",),
        capability_manifest_id="capability:work:work-1",
        memory_scope="memory:operations:work-1",
    )
    builder.apply(builder.propose_assign_room("work-1", assignment))
    work = _room(builder.document, "work-1")
    assert work.memory_scope == "memory:operations:work-1"
    assert work.capability_manifest_id == "capability:work:work-1"
    assert work.style["assignment"]["role_ids"] == [
        "operations-lead", "operations-analyst"]
    assert not builder.validate()


def test_assignment_rejects_invalid_tokens(document):
    builder = RB.RoomBuilder(document)
    bad = RB.RoomAssignment(
        business_id="business sample", department_id="operations",
        role_ids=("operations-lead",))
    with pytest.raises(RB.RoomBuilderError) as excinfo:
        builder.propose_assign_room("work-1", bad)
    assert excinfo.value.problems


def test_clone_merge_and_split(document):
    cloner = RB.RoomBuilder(document)
    cloner.apply(cloner.propose_clone_department("floor-001", "floor-002"))
    assert len(cloner.document.floors) == 2
    assert len(cloner.document.rooms) == 2 * len(document.rooms)
    assert not cloner.validate()
    assert all(
        room.style["department_style"] != "operations"
        for room in cloner.document.rooms
        if room.floor_id == "floor-002"
    )

    cloner.apply(cloner.propose_merge_departments(
        ["floor-001", "floor-002"], "floor-002"))
    assert len(cloner.document.floors) == 1
    assert len(cloner.document.rooms) == 2 * len(document.rooms)
    assert not cloner.validate()

    splitter = RB.RoomBuilder(document, required_zones=())
    splitter.apply(splitter.propose_split_department(
        "floor-001", "floor-003", ["store-1"]))
    moved = _room(splitter.document, "store-1")
    assert moved.floor_id == "floor-003"
    assert not splitter.validate()

    default_problems = RB.validate_room_builder_document(splitter.document)
    assert any("MISSING_REQUIRED_ROOM" in item for item in default_problems)


def test_split_refuses_an_empty_or_foreign_floor(document):
    builder = RB.RoomBuilder(document)
    with pytest.raises(RB.RoomBuilderError):
        builder.propose_split_department("floor-001", "floor-003", ["nope-1"])
    with pytest.raises(RB.RoomBuilderConflict):
        builder.propose_clone_department("floor-001", "floor-001")


def test_stretched_cell_negative(document):
    broken = replace(
        document, cells=(replace(document.cells[0], cols=24), *document.cells[1:]))
    problems = RB.validate_room_builder_document(broken)
    assert any("16x12" in item for item in problems)


def test_missing_door_negative(document):
    entry = document.rooms[0].room_id
    rooms = tuple(
        replace(room, portal_ids=())
        if room.room_id == entry else room
        for room in document.rooms
    )
    problems = RB.validate_room_builder_document(replace(document, rooms=rooms))
    assert any("MISSING_DOOR" in item for item in problems)


def test_unreachable_desk_negative(document):
    problems = RB.validate_room_builder_document(replace(document, portals=()))
    assert any("unreachable" in item.lower() for item in problems)


def test_collision_negative(document):
    cell = document.cells[0]
    duplicate = replace(cell, cell_id="dup-cell")
    rooms = tuple(
        replace(room, cell_ids=room.cell_ids + ("dup-cell",))
        if room.room_id == cell.room_id else room
        for room in document.rooms
    )
    problems = RB.validate_room_builder_document(replace(
        document,
        cells=(cell, duplicate, *document.cells[1:]),
        rooms=rooms,
    ))
    assert any("CELL_COLLISION" in item for item in problems)


def test_desk_collision_negative(document):
    desk = document.desks[0]
    duplicate = replace(desk, desk_id="dup-desk", seat_id="dup-seat")
    cell = next(c for c in document.cells if c.cell_id == desk.cell_id)
    cells = tuple(
        replace(c, desk_ids=c.desk_ids + ("dup-desk",))
        if c.cell_id == cell.cell_id else c
        for c in document.cells
    )
    rooms = tuple(
        replace(r, desk_ids=r.desk_ids + ("dup-desk",),
                capacity=r.capacity + 1)
        if r.room_id == desk.room_id else r
        for r in document.rooms
    )
    problems = RB.validate_room_builder_document(replace(
        document,
        cells=cells,
        rooms=rooms,
        desks=(*document.desks, duplicate),
    ))
    assert any("DESK_COLLISION" in item for item in problems)


def test_capacity_mismatch_negative(document):
    room = document.rooms[0]
    rooms = tuple(
        replace(r, capacity=r.capacity + 1)
        if r.room_id == room.room_id else r for r in document.rooms)
    problems = RB.validate_room_builder_document(replace(document, rooms=rooms))
    assert any("capacity" in item.lower() for item in problems)


def test_style_collision_negative(document):
    builder = RB.RoomBuilder(document)
    builder.apply(builder.propose_clone_department("floor-001", "floor-002"))
    rooms = tuple(
        replace(room, style={**room.style, "department_style": "operations"})
        if room.floor_id == "floor-002" else room
        for room in builder.document.rooms
    )
    problems = RB.validate_room_builder_document(
        replace(builder.document, rooms=rooms))
    assert any("STYLE_COLLISION" in item for item in problems)


def test_mixed_department_style_negative(document):
    room = document.rooms[0]
    rooms = tuple(
        replace(r, style={**r.style, "department_style": "different"})
        if r.room_id == room.room_id else r for r in document.rooms)
    problems = RB.validate_room_builder_document(replace(document, rooms=rooms))
    assert any("MIXED_DEPARTMENT_STYLE" in item for item in problems)


def test_paid_and_raw_asset_negatives(document):
    room_id = document.rooms[0].room_id
    paid = MS.MapAssetRef(
        asset_id="art:paid", kind="recipe", ref="recipes/local",
        sha256="a" * 64, paid=True)
    rooms = tuple(
        replace(room, assets=(paid,))
        if room.room_id == room_id else room for room in document.rooms)
    problems = RB.validate_room_builder_document(replace(document, rooms=rooms))
    assert any("paid art" in item.lower() for item in problems)

    raw = MS.MapAssetRef(
        asset_id="art:raw", kind="tileset", ref="art/limezu/tile.png",
        sha256="b" * 64)
    rooms = tuple(
        replace(room, assets=(raw,))
        if room.room_id == room_id else room for room in document.rooms)
    problems = RB.validate_room_builder_document(replace(document, rooms=rooms))
    assert any("raw sprite" in item.lower() for item in problems)


def test_invalid_draft_cannot_be_approved_or_published(document):
    builder = RB.RoomBuilder(document)
    builder._document = replace(
        document, cells=(replace(document.cells[0], cols=32), *document.cells[1:]))
    with pytest.raises(RB.RoomBuilderRefused):
        builder.approve()
    with pytest.raises(RB.RoomBuilderRefused):
        builder.publish()
    assert builder.versions() == ()


def test_invalid_publish_is_atomic(document):
    builder = RB.RoomBuilder(document)
    builder.publish()
    before = len(builder.versions())
    builder._document = replace(
        document, cells=(replace(document.cells[0], cols=32), *document.cells[1:]))
    with pytest.raises(RB.RoomBuilderRefused):
        builder.publish()
    assert len(builder.versions()) == before


def test_preview_reports_counts_and_problems(document):
    builder = RB.RoomBuilder(document)
    preview = builder.preview()
    assert preview["valid"] is True
    assert preview["counts"] == {
        "floors": 1,
        "rooms": len(document.rooms),
        "cells": len(document.cells),
        "desks": len(document.desks),
        "portals": len(document.portals),
    }
    broken = replace(document, portals=())
    bad = builder.preview(broken)
    assert bad["valid"] is False
    assert bad["problems"]


def test_unknown_operation_is_typed(document):
    builder = RB.RoomBuilder(document)
    with pytest.raises(RB.RoomBuilderError):
        builder.propose("demolish_everything")
