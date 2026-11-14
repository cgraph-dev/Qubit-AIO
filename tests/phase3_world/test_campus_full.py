"""P3-01 — the full shipped campus, Trading Department included.

Hermetic: pure projection data, no store, no clock, no network. These tests pin
the published totals (100 floors / 5 wings / 300 rooms / 575 cells / 3,075
desks), the Trading Department floor grammar, deterministic identity and the
fixture-vs-production boundary (zero staffed agents, no tenant/business/agent
records).
"""
from __future__ import annotations

import json
import re

import pytest

from voicebridge.business.roles import RoleRegistry
from voicebridge.office.world import model as M
from voicebridge.office.world import tower_plan as TP

pytestmark = pytest.mark.unit

_TRADING_ROOMS = (
    "Trading Floor",
    "Strategy Lab",
    "Risk Room",
    "Data Room",
    "Treasury / Accounting",
    "Compliance / Archive",
    "Incident Room",
    "Reception",
    "Kitchen / Break",
    "Restrooms",
    "Lounge",
    "Boardroom",
    "Focus Rooms",
    "Head Office",
    "Storage / Archive",
    "Comms / Ops",
)
_PRODUCTION_ID_RE = re.compile(
    r"\b(?:tenant|business|agent)[_-][0-9a-f]{6,}\b", re.I)


@pytest.fixture(scope="module")
def registry() -> RoleRegistry:
    return RoleRegistry.default()


@pytest.fixture(scope="module")
def plan(registry: RoleRegistry) -> TP.TowerPlan:
    return TP.build_tower_plan(registry)


def test_published_totals_are_exact(plan):
    assert plan.counts() == {
        "floors": 100,
        "wings": 5,
        "active_floors": 23,
        "dormant_floors": 77,
        "rooms": 300,
        "cells": 575,
        "desks": 3075,
        "staffed_agents": 0,
    }
    assert TP.validate_tower_plan(plan) == []


def test_trading_department_exists_with_roles(registry):
    departments = {dept.id for dept in registry.departments}
    assert "trading" in departments
    roles = registry.filter_roles(department="trading")
    assert len(roles) >= 13
    assert {role.department for role in roles} == {"trading"}


def test_trading_floor_carries_the_design_rooms(plan):
    floor = next(
        f for f in plan.floors
        if any("Trading Floor" in room.name for room in f.rooms))
    names = {room.name for room in floor.rooms}
    for wanted in _TRADING_ROOMS:
        assert any(wanted in name for name in names), wanted
    assert floor.room_count == 16
    assert floor.cell_count == 19
    assert floor.desk_count == 111


def test_every_department_has_floors_and_rooms(registry):
    by_department = {}
    for floor in registry.floors:
        for dept_id in floor.department_ids:
            by_department.setdefault(dept_id, []).append(floor)
    assert set(by_department) == {dept.id for dept in registry.departments}
    for dept_id, floors in by_department.items():
        assert floors, dept_id
        assert all(f.rooms for f in floors)
        assert all(sum(r.desks for r in f.rooms) == f.desk_capacity
                   for f in floors)


def test_cells_stay_the_fixed_primitive(plan):
    for floor in plan.floors:
        for room in floor.rooms:
            for cell in room.cells:
                assert (cell.cols, cell.rows) == (M.CELL_COLS, M.CELL_ROWS)
                assert cell.desk_count <= M.CELL_DESK_CAPACITY


def test_projection_is_deterministic(registry):
    first = TP.build_tower_plan(registry)
    second = TP.build_tower_plan(registry)
    assert first.digest == second.digest
    assert json.dumps(first.full_dict(), sort_keys=True) == json.dumps(
        second.full_dict(), sort_keys=True)


def test_seed_is_not_production(plan):
    payload = json.dumps(plan.full_dict(), sort_keys=True)
    assert _PRODUCTION_ID_RE.search(payload) is None
    assert plan.counts()["staffed_agents"] == 0
    for floor in plan.floors:
        for room in floor.rooms:
            assert room.staffed_agents == 0
            assert room.agent_slots == room.desk_count
            data = room.to_dict(detail="full")
            assert "agents" not in data
            for cell in data["cells"]:
                assert all(desk["occupied"] is False for desk in cell["desks"])
