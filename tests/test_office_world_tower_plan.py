"""The deterministic 100-floor world projection and its read-only route.

Hermetic: pure data, no store, no clock, no clock drift, no network, no model.
These tests pin the master-plan §32–§40 contract for the static tower:

  * 100 floors over 5 wings, active/dormant taken from the templates;
  * rooms are cell clusters — every cell stays exactly 16x12;
  * room/cell/desk/seat ids are globally unique and stable;
  * no hard room/agent cap and no fabricated staffed agent;
  * ``GET /api/office/world/tower-plan`` is a bounded read with typed filters;
  * the route is registered and sits behind the dashboard token gate.
"""
from __future__ import annotations

import json
import math
import socket

import pytest

from voicebridge import qubit_compat as HC
from voicebridge import webui as W
from voicebridge.business.roles import RoleRegistry
from voicebridge.office.world import model as M
from voicebridge.office.world import tower_plan as TP

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def registry() -> RoleRegistry:
    return RoleRegistry.default()


@pytest.fixture(scope="module")
def plan(registry: RoleRegistry) -> TP.TowerPlan:
    return TP.build_tower_plan(registry)


class _Cfg:
    """Minimal Config stand-in. The world read must not touch its state dir."""

    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return self._state


def _call(cfg, method, path, *, query=None):
    return HC.dispatch(cfg, method, path, query=query or {}, body={})


# --------------------------------------------------------------- tower data
def test_one_hundred_floors_across_five_wings(plan, registry):
    assert plan.floor_count == 100
    assert plan.wing_count == 5
    assert [f.index for f in plan.floors] == list(range(1, 101))
    assert {f.wing for f in plan.floors} == {t.wing for t in registry.floors}
    assert sorted(w.wing_id for w in plan.wings) == sorted(
        {t.wing for t in registry.floors})
    assert sum(w.floor_count for w in plan.wings) == 100


def test_product_default_is_five_floors():
    plan = TP.build_tower_plan()
    assert plan.floor_count == 5
    assert plan.wing_count == 5
    assert [f.index for f in plan.floors] == [1, 2, 3, 4, 5]
    assert plan.counts() == {
        "floors": 5,
        "wings": 5,
        "active_floors": 5,
        "dormant_floors": 0,
        "rooms": 15,
        "cells": 28,
        "desks": 147,
        "staffed_agents": 0,
    }
    assert TP.validate_tower_plan(plan) == []


def test_active_and_dormant_split_comes_from_the_templates(plan, registry):
    by_id = {f.floor_id: f for f in plan.floors}
    for template in registry.floors:
        floor = by_id[template.floor_id]
        assert floor.active is template.active_by_default
        assert floor.to_dict()["state"] == (
            "ACTIVE" if template.active_by_default else "DORMANT")
        for room in floor.rooms:
            assert room.active is template.active_by_default
    assert plan.active_floor_count == len(registry.active_floors)
    assert plan.dormant_floor_count == len(registry.dormant_floors)
    assert plan.active_floor_count + plan.dormant_floor_count == 100


def test_room_and_desk_counts_match_the_templates(plan, registry):
    by_id = {f.floor_id: f for f in plan.floors}
    for template in registry.floors:
        floor = by_id[template.floor_id]
        assert floor.room_count == len(template.rooms)
        assert floor.desk_count == template.desk_capacity
        assert {(r.room_id, r.desk_count) for r in floor.rooms} == {
            (r.room_id, r.desks) for r in template.rooms}
        assert floor.max_rooms is None
        assert floor.max_agents is None
        assert floor.expansion_allowed is template.expansion_allowed
    assert plan.desk_count == sum(t.desk_capacity for t in registry.floors)


def test_every_cell_is_the_fixed_16x12_primitive(plan):
    seen_cluster = False
    for floor in plan.floors:
        for room in floor.rooms:
            assert room.cell_count == math.ceil(
                room.desk_count / M.CELL_DESK_CAPACITY)
            if room.cell_count > 1:
                seen_cluster = True
            for cell in room.cells:
                assert (cell.cols, cell.rows) == (16, 12)
                assert cell.to_dict()["standard"] is True
                assert 1 <= cell.desk_count <= M.CELL_DESK_CAPACITY
                for desk in cell.desks:
                    x, y = desk.tile
                    assert 0 <= x and x + desk.width <= cell.cols
                    assert 0 <= y and y + desk.height <= cell.rows
    assert seen_cluster, "the shipped catalog must exercise cell clusters"


def test_room_cell_desk_and_seat_ids_are_globally_unique(plan):
    room_ids, cell_ids, desk_ids, seat_ids = set(), set(), set(), set()
    for floor in plan.floors:
        for room in floor.rooms:
            assert room.room_id not in room_ids
            room_ids.add(room.room_id)
            for cell in room.cells:
                assert cell.cell_id not in cell_ids
                cell_ids.add(cell.cell_id)
                assert cell.cell_id.startswith(room.room_id + ":")
                for desk in cell.desks:
                    assert desk.desk_id not in desk_ids
                    desk_ids.add(desk.desk_id)
                    assert desk.seat_id not in seat_ids
                    seat_ids.add(desk.seat_id)
                    assert desk.desk_id.startswith(cell.cell_id + ":")
                    assert desk.seat_id.startswith(cell.cell_id + ":")
    assert len(room_ids) == plan.room_count
    assert len(cell_ids) == plan.cell_count
    assert len(desk_ids) == plan.desk_count == len(seat_ids)


def test_no_room_claims_a_staffed_agent(plan):
    for floor in plan.floors:
        for room in floor.rooms:
            assert room.staffed_agents == 0
            assert room.agent_slots == room.desk_count
            data = room.to_dict(detail="full")
            assert data["staffed_agents"] == 0
            assert data["agent_slots"] == data["desk_count"]
            assert "agents" not in data
            for cell in data["cells"]:
                for desk in cell["desks"]:
                    assert desk["occupied"] is False


def test_validation_reports_no_defect(plan):
    assert TP.validate_tower_plan(plan) == []


# ------------------------------------------------------------- determinism
def test_two_builds_are_byte_identical(registry):
    first = TP.build_tower_plan(registry)
    second = TP.build_tower_plan(registry)
    assert first.digest == second.digest
    assert json.dumps(first.full_dict(), sort_keys=True) == json.dumps(
        second.full_dict(), sort_keys=True)
    assert first.digest == TP.build_tower_plan_digest(first)


# ------------------------------------------------------------ payload shape
def test_default_payload_is_a_bounded_floor_summary(plan):
    payload = TP.tower_payload(plan)
    assert payload["ok"] is True
    assert payload["schema"] == TP.SCHEMA
    assert payload["digest"] == plan.digest
    assert payload["filter"] == {"floor": None, "wing": None}
    assert payload["detail"] == "summary"
    assert payload["tower"] == plan.counts()
    assert payload["counts"]["floors"] == 100
    assert payload["counts"]["staffed_agents"] == 0
    assert len(payload["wings"]) == 5
    assert len(payload["floors"]) == 100
    for floor in payload["floors"]:
        assert "rooms" not in floor
    # A summary must stay small: the full detail is opt-in per floor/wing.
    assert len(json.dumps(payload, separators=(",", ":"))) < 64_000


def test_floor_filter_returns_one_floor_with_cells_and_desks(plan):
    payload = TP.tower_payload(plan, floor="floor-012")
    assert payload["detail"] == "full"
    assert payload["counts"]["floors"] == 1
    assert payload["counts"]["staffed_agents"] == 0
    [floor] = payload["floors"]
    assert floor["floor_id"] == "floor-012"
    assert floor["rooms"]
    assert all(room["cells"] for room in floor["rooms"])
    assert all(
        cell["cols"] == 16 and cell["rows"] == 12
        for room in floor["rooms"] for cell in room["cells"])
    # Numeric shorthand resolves to the same floor.
    assert TP.tower_payload(plan, floor="12")["floors"][0]["floor_id"] == (
        "floor-012")


def test_wing_filter_returns_room_summaries(plan):
    payload = TP.tower_payload(plan, wing="north")
    assert payload["detail"] == "rooms"
    assert payload["counts"]["floors"] == 20
    assert payload["counts"]["floors"] == len(
        [f for f in plan.floors if f.wing == "north"])
    for floor in payload["floors"]:
        assert floor["wing"] == "north"
        for room in floor["rooms"]:
            assert room["agent_slots"] == room["desk_count"]
            assert room["staffed_agents"] == 0
            assert "cells" not in room


# ------------------------------------------------------------- HTTP surface
def test_route_registered_and_read_authority():
    route, _params = HC.match("GET", "/api/office/world/tower-plan")
    assert route is not None
    assert route["handler"] == "h_office_world_tower"
    assert route["disposition"] == "implemented"
    assert W.qubit_read_authority("/api/office/world/tower-plan")
    assert W.qubit_read_authority("/api/office/world")
    # It is a read-only route: no other method is registered.
    assert HC.match("POST", "/api/office/world/tower-plan")[0] is None


def test_dispatch_serves_the_projection_without_a_store(tmp_path):
    cfg = _Cfg(tmp_path / "state")
    code, payload = _call(cfg, "GET", "/api/office/world/tower-plan")
    assert code == 200
    assert payload["ok"] is True
    assert payload["tower"]["floors"] == 5
    assert payload["tower"]["wings"] == 5
    assert payload["counts"]["desks"] > 0
    # No store was opened or created by the read.
    assert not (tmp_path / "state").exists()


def test_floor_and_wing_filters_are_typed(tmp_path):
    cfg = _Cfg(tmp_path / "state")
    code, payload = _call(
        cfg, "GET", "/api/office/world/tower-plan", query={"floor": "floor-999"})
    assert code == 404
    assert payload["ok"] is False
    assert payload["code"] == "OFFICE_WORLD_UNKNOWN_FLOOR"

    code, payload = _call(
        cfg, "GET", "/api/office/world/tower-plan", query={"wing": "basement"})
    assert code == 404
    assert payload["ok"] is False
    assert payload["code"] == "OFFICE_WORLD_UNKNOWN_WING"

    code, payload = _call(
        cfg, "GET", "/api/office/world/tower-plan",
        query={"floor": "floor-001", "wing": "north"})
    assert code == 400
    assert payload["ok"] is False
    assert payload["code"] == "OFFICE_WORLD_CONFLICTING_FILTER"


def test_tokenless_non_loopback_request_fails_the_dashboard_gate():
    handler = object.__new__(W.WebUIHandler)
    handler.path = "/agent/api/office/world/tower-plan"
    handler.client_address = ("203.0.113.9", 1234)
    handler.headers = {}
    handler.server = type("S", (), {
        "app": type("A", (), {"api_token": "secret"})(),
    })()
    handler._api_token_ok = lambda: False
    assert handler._qubit_token_ok() is False
    handler.headers = {"X-Qubit-Session-Token": "wrong"}
    assert handler._qubit_token_ok() is False
    handler.headers = {"X-Qubit-Session-Token": "secret"}
    assert handler._qubit_token_ok() is True


def test_read_needs_no_network(tmp_path, monkeypatch):
    def _bomb(*_args, **_kwargs):
        raise AssertionError("world projection must not open a socket")

    monkeypatch.setattr(socket, "socket", _bomb)
    monkeypatch.setattr(socket, "create_connection", _bomb)
    cfg = _Cfg(tmp_path / "state")
    code, payload = _call(cfg, "GET", "/api/office/world/tower-plan")
    assert code == 200
    assert payload["tower"]["floors"] == 5
