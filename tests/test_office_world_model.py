"""The world model: fixed cells, clusters, namespaced seats, legacy IDs, refusal.

These tests pin the plan's hierarchy (§5, §18.4) as data, not prose:

  * 9/12/18/24 desks all materialise through cell clusters;
  * every cell is 16x12 — a wider room is more cells, never a stretched cell;
  * seat ids are ``roomId:cellId:localSeatId`` and unique;
  * ``legacyFloorId`` survives the floor -> wing rename;
  * a business record always exists, independent of room state;
  * the hierarchical path/portal graph reaches every room and cell;
  * malformed data is refused with named problems.

Hermetic: pure functions, no store, no clock, no network.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from voicebridge.office.world import model as M


def _campus(desks: int = 9) -> M.Campus:
    room = M.build_room(
        "room-1", "Room 1", "wing-1", desks,
        business_id="biz-1", legacy_floor_id=0,
    )
    wing = M.Wing("wing-1", "Wing 1", 0, 0, (room,), z=0)
    business = M.Business(
        "biz-1", "Business 1", "wing-1",
        room_ids=("room-1",), headcount=desks,
    )
    return M.assemble_campus("campus-1", "Campus 1", (wing,), (business,))


# --- cluster arithmetic -----------------------------------------------------

@pytest.mark.parametrize(
    "desks, expected_cells, expected_counts",
    [
        (9, 2, [5, 4]),
        (12, 2, [6, 6]),
        (18, 3, [6, 6, 6]),
        (24, 4, [6, 6, 6, 6]),
    ],
)
def test_cell_clusters_support_9_12_18_24_without_stretching(
    desks, expected_cells, expected_counts,
):
    cells = M.plan_cell_cluster("room-x", desks)
    assert len(cells) == expected_cells
    assert [len(c.desks) for c in cells] == expected_counts
    assert [c.index for c in cells] == list(range(expected_cells))
    for cell in cells:
        assert (cell.cols, cell.rows) == (16, 12)
        assert cell.is_standard is True
        assert len(cell.desks) <= M.CELL_DESK_CAPACITY


def test_cluster_origins_are_disjoint_and_deterministic():
    first = M.cell_origins(4)
    second = M.cell_origins(4)
    assert first == second
    # Two columns, then the next row; no two cells share a rectangle.
    assert first[0] == M.Tile(0, 0)
    assert first[1] == M.Tile(M.CELL_COLS + M.CLUSTER_GAP, 0)
    assert first[2] == M.Tile(0, M.CELL_ROWS + M.CLUSTER_GAP)
    assert first[3] == M.Tile(M.CELL_COLS + M.CLUSTER_GAP,
                              M.CELL_ROWS + M.CLUSTER_GAP)


def test_cluster_rejects_nonsense_desk_counts():
    with pytest.raises(M.WorldModelError):
        M.plan_cell_cluster("room-x", 0)
    with pytest.raises(M.WorldModelError):
        M.plan_cell_cluster("room-x", -3)
    with pytest.raises(M.WorldModelError):
        M.plan_cell_cluster("room-x", M.CELL_DESK_CAPACITY * 65)


# --- identity, legacy ids, API shape ---------------------------------------

def test_seat_ids_are_namespaced_and_unique():
    campus = _campus(24)
    seats = campus.seats()
    assert len(seats) == 24
    assert len({s.seat_id for s in seats}) == 24
    for seat in seats:
        room, cell, local = seat.seat_id.split(":")
        assert room == "room-1"
        assert (cell, local) == (seat.cell_id, seat.local_seat_id)


def test_legacy_floor_id_is_preserved_in_the_api_shape():
    campus = _campus(9)
    room = campus.room("room-1")
    assert room.legacy_floor_id == 0
    payload = campus.to_dict()
    assert payload["wings"][0]["legacyFloorId"] == 0
    assert payload["wings"][0]["rooms"][0]["legacyFloorId"] == 0
    assert payload["wings"][0]["rooms"][0]["characterMode"] == "multi"
    assert payload["businesses"][0]["business_id"] == "biz-1"
    assert len(payload["wings"][0]["rooms"][0]["cells"]) == 2


def test_character_mode_is_constrained_to_single_or_multi():
    assert M.CharacterMode("single") is M.CharacterMode.SINGLE
    assert M.CharacterMode("multi") is M.CharacterMode.MULTI
    room = M.build_room(
        "r", "R", "w", 9, business_id="b",
        character_mode=M.CharacterMode.SINGLE)
    assert room.to_dict()["characterMode"] == "single"


def test_business_record_exists_even_when_the_room_is_dormant():
    campus = _campus(9)
    room = replace(campus.room("room-1"), state=M.RoomState.DORMANT,
                   tier=M.Tier.DORMANT)
    wing = replace(campus.wings[0], rooms=(room,))
    dormant = replace(campus, wings=(wing,))
    assert dormant.business("biz-1").room_ids == ("room-1",)
    assert dormant.room("room-1").state is M.RoomState.DORMANT
    assert M.validate_campus(dormant) == []


# --- hierarchical paths -----------------------------------------------------

def test_assemble_derives_paths_to_every_room_and_cell():
    campus = _campus(12)
    graph = campus.path_graph
    root = M.campus_node("campus-1")
    room = campus.room("room-1")
    for cell in room.cells:
        node = M.cell_node("room-1", cell.cell_id)
        path = graph.path(root, node)
        assert path is not None
        assert path[-1] == node
    assert graph.connected(root) is True


# --- validation refusals ----------------------------------------------------

def test_stretched_cell_is_refused():
    campus = _campus(9)
    room = campus.room("room-1")
    stretched = replace(room.cells[0], cols=24)
    bad_room = replace(room, cells=(stretched, *room.cells[1:]))
    bad_wing = replace(campus.wings[0], rooms=(bad_room,))
    problems = M.validate_campus(replace(campus, wings=(bad_wing,)))
    assert any("stretched cell" in p for p in problems)


def test_duplicate_seat_id_is_refused():
    campus = _campus(9)
    room = campus.room("room-1")
    cell = room.cells[0]
    duplicate = replace(cell.desks[-1].seat, seat_id=cell.desks[0].seat.seat_id)
    bad_desk = replace(cell.desks[-1], seat=duplicate)
    bad_cell = replace(cell, desks=(*cell.desks[:-1], bad_desk))
    bad_room = replace(room, cells=(bad_cell, *room.cells[1:]))
    problems = M.validate_campus(replace(
        campus, wings=(replace(campus.wings[0], rooms=(bad_room,)),)))
    assert any("duplicate seat id" in p for p in problems)
    assert any("seat id" in p and "!=" in p for p in problems)


def test_missing_business_record_is_refused():
    campus = _campus(9)
    problems = M.validate_campus(replace(campus, businesses=()))
    assert any("has no authoritative record" in p for p in problems)


def test_legacy_floor_mismatch_is_refused():
    campus = _campus(9)
    room = replace(campus.room("room-1"), legacy_floor_id=2)
    bad_wing = replace(campus.wings[0], rooms=(room,))
    problems = M.validate_campus(replace(campus, wings=(bad_wing,)))
    assert any("legacyFloorId" in p for p in problems)


def test_portal_to_unknown_node_is_refused():
    campus = _campus(9)
    portal = M.Portal("p-bad", "campus:campus-1", "wing:nowhere")
    problems = M.validate_campus(replace(
        campus, path_graph=M.PathGraph((portal,))))
    assert any("unknown node" in p for p in problems)


def test_unreachable_room_is_refused():
    campus = _campus(9)
    # Remove the wing->room door: the room and its cells still exist, but no
    # path reaches them from the campus root.
    portals = tuple(
        p for p in campus.path_graph.portals
        if p.target not in ("room:room-1",)
    )
    problems = M.validate_campus(replace(
        campus, path_graph=M.PathGraph(portals)))
    assert any("unreachable" in p for p in problems)


def test_cell_overflow_and_desk_overlap_are_refused():
    campus = _campus(12)
    room = campus.room("room-1")
    cell = room.cells[0]
    # Seven desks in one cell: take a desk from the second cell and stack it.
    borrowed = room.cells[1].desks[0]
    overlapping = replace(borrowed, tile=cell.desks[0].tile)
    bad_cell = replace(cell, desks=(*cell.desks, overlapping))
    bad_room = replace(room, cells=(bad_cell, *room.cells[1:]))
    problems = M.validate_campus(replace(
        campus, wings=(replace(campus.wings[0], rooms=(bad_room,)),)))
    assert any("exceeds" in p for p in problems)
    assert any("overlaps another desk" in p for p in problems)


def test_assert_valid_campus_raises_with_all_problems():
    campus = _campus(9)
    bad = replace(campus, businesses=())
    with pytest.raises(M.WorldModelError) as excinfo:
        M.assert_valid_campus(bad)
    assert excinfo.value.problems == M.validate_campus(bad)
