"""The Qubit Office role/capability registry.

Hermetic: no database, no service, no network, no model. These tests pin the
Wave-2 lane-B contract from ``docs/QUBIT-OFFICE-MASTER-PLAN.md`` §32–§36: at
least 100 role templates across every department, a full field schema, safe
permissions/approval gates, autonomy ordering, 100 floor/wing templates with
active/dormant materialization, and JSON/dict round-tripping.
"""
from __future__ import annotations

import json

import pytest

from voicebridge.business.roles import (
    APPROVAL_CATEGORIES,
    AUTONOMY_LEVELS,
    ROLE_BAD_AUTONOMY_CODE,
    ROLE_BAD_FLOOR_CODE,
    ROLE_BAD_INPUT_CODE,
    ROLE_DUPLICATE_ID_CODE,
    ROLE_MISSING_APPROVAL_CODE,
    ROLE_UNKNOWN_DEPARTMENT_CODE,
    ROLE_UNSAFE_PERMISSION_CODE,
    Department,
    FloorTemplate,
    RoleRegistry,
    RoleRegistryError,
    RoleTemplate,
    autonomy_at_least,
    autonomy_rank,
    required_approvals,
    unsafe_token,
)


@pytest.fixture(scope="module")
def registry() -> RoleRegistry:
    return RoleRegistry.default()


def _role_dict(registry: RoleRegistry, role_id: str = "crm-hygiene") -> dict:
    return dict(registry.get_role(role_id).to_dict())


def _mutated(registry: RoleRegistry, **changes) -> dict:
    data = _role_dict(registry)
    data.update(changes)
    return data


def _registry_with_role(registry: RoleRegistry, data: dict) -> RoleRegistry:
    return RoleRegistry(
        registry.departments,
        (*registry.roles, RoleTemplate.from_dict(data)),
        (),
    )


# --- catalog size, departments and schema ------------------------------
def test_catalog_has_at_least_100_unique_roles_and_every_department(registry):
    roles = registry.roles
    assert len(roles) >= 100
    ids = [role.id for role in roles]
    assert len(ids) == len(set(ids))

    department_ids = {dept.id for dept in registry.departments}
    assert len(department_ids) >= 22
    assert {role.department for role in roles} == department_ids
    for dept in registry.departments:
        assert registry.filter_roles(department=dept.id), dept.id


def test_every_role_declares_the_required_field_schema(registry):
    required_keys = {
        "id",
        "department",
        "title",
        "mission",
        "responsibilities",
        "skills",
        "toolsets",
        "data_scopes",
        "permissions",
        "budget",
        "autonomy",
        "approvals",
        "schedule",
        "kpis",
        "escalation",
        "model",
        "sop_refs",
        "memory_refs",
        "human_owner",
        "service_lines",
        "active_by_default",
    }
    non_empty = (
        "responsibilities",
        "skills",
        "toolsets",
        "data_scopes",
        "permissions",
        "kpis",
        "sop_refs",
        "memory_refs",
    )
    for role in registry.roles:
        data = role.to_dict()
        assert required_keys <= set(data), role.id
        for key in non_empty:
            assert data[key], f"{role.id}.{key}"
        assert data["autonomy"] in AUTONOMY_LEVELS
        assert data["budget"]["currency"] == "USD"
        assert data["budget"]["monthly_minor"] > 0
        assert data["budget"]["per_action_minor"] > 0
        assert data["budget"]["rate_limit_per_hour"] > 0
        assert data["model"]["provider_hint"]
        assert data["model"]["model_hint"]
        assert data["human_owner"]
        assert data["escalation"]
        assert data["schedule"]
        assert isinstance(data["active_by_default"], bool)


def test_serialization_round_trips_through_dict_and_json(registry):
    payload = registry.to_dict()
    assert payload["schema"] == "qubit-office-role-registry/1"
    assert json.loads(json.dumps(payload)) == payload

    clone = RoleRegistry.from_dict(json.loads(json.dumps(payload)))
    assert clone.to_dict() == payload
    assert len(clone.roles) == len(registry.roles)
    assert len(clone.floors) == len(registry.floors)

    role = registry.get_role("marketing-paid-ads")
    assert RoleTemplate.from_dict(role.to_dict()).to_dict() == role.to_dict()


def test_schema_rejects_unknown_fields(registry):
    data = _role_dict(registry)
    data["secret_admin"] = True
    with pytest.raises(RoleRegistryError) as caught:
        RoleTemplate.from_dict(data)
    assert caught.value.code == ROLE_BAD_INPUT_CODE


# --- validation and safety ---------------------------------------------
def test_duplicate_role_ids_are_refused(registry):
    clone = _role_dict(registry)
    with pytest.raises(RoleRegistryError) as caught:
        _registry_with_role(registry, clone)
    assert caught.value.code == ROLE_DUPLICATE_ID_CODE


def test_unknown_department_is_refused(registry):
    data = _mutated(registry, id="crm-ghost", department="ghost")
    with pytest.raises(RoleRegistryError) as caught:
        _registry_with_role(registry, data)
    assert caught.value.code == ROLE_UNKNOWN_DEPARTMENT_CODE


def test_invalid_autonomy_is_refused(registry):
    data = _mutated(registry, id="crm-bad-autonomy", autonomy="L9")
    with pytest.raises(RoleRegistryError) as caught:
        RoleTemplate.from_dict(data)
    assert caught.value.code == ROLE_BAD_AUTONOMY_CODE


@pytest.mark.parametrize(
    "field,token",
    (
        ("permissions", "*"),
        ("permissions", "admin.all"),
        ("permissions", "payments.delete"),
        ("permissions", "shell.exec"),
        ("toolsets", "all"),
        ("toolsets", "terminal"),
        ("data_scopes", "business.*"),
    ),
)
def test_wildcard_and_destructive_defaults_are_refused(registry, field, token):
    data = _mutated(registry, id="crm-unsafe", **{field: [token]})
    with pytest.raises(RoleRegistryError) as caught:
        RoleTemplate.from_dict(data)
    assert caught.value.code == ROLE_UNSAFE_PERMISSION_CODE


@pytest.mark.parametrize(
    "permission,category",
    (
        ("payments.initiate", "spend"),
        ("content.publish", "publish"),
        ("contracts.sign", "legal"),
        ("email.send", "contact"),
    ),
)
def test_spend_legal_publish_contact_need_approval_gates(
    registry, permission, category
):
    base = _role_dict(registry)
    data = {
        **base,
        "id": f"crm-needs-{category}",
        "department": "crm",
        "permissions": [permission],
        "approvals": [],
    }
    with pytest.raises(RoleRegistryError) as caught:
        RoleTemplate.from_dict(data)
    assert caught.value.code == ROLE_MISSING_APPROVAL_CODE
    assert category in str(caught.value.detail)

    safe = RoleTemplate.from_dict({**data, "approvals": [category]})
    assert safe.requires_approval(category)
    assert category in required_approvals([permission])


def test_no_shipped_role_defaults_to_wildcard_or_destructive_access(registry):
    forbidden_toolsets = {"all", "any", "terminal", "shell", "admin", "sudo"}
    for role in registry.roles:
        assert role.autonomy in {"L0", "L1", "L2", "L3"}, role.id
        assert set(role.approvals) <= set(APPROVAL_CATEGORIES)
        assert not (set(role.toolsets) & forbidden_toolsets), role.id
        for token in (*role.permissions, *role.toolsets, *role.data_scopes):
            assert not unsafe_token(token), f"{role.id}: {token}"


def test_high_risk_catalog_roles_carry_their_gates(registry):
    paid = registry.get_role("marketing-paid-ads")
    assert paid.requires_approval("spend")
    assert paid.requires_approval("publish")

    social = registry.get_role("marketing-social")
    assert social.requires_approval("publish")
    assert social.requires_approval("contact")

    contracts = registry.get_role("legal-contracts")
    assert contracts.requires_approval("legal")

    payments = registry.get_role("finance-payments")
    assert payments.requires_approval("spend")
    assert payments.budget["per_action_minor"] > 0


# --- autonomy ordering and lookup --------------------------------------
def test_autonomy_ordering():
    assert autonomy_rank("L0") == 0
    assert autonomy_rank("L5") == 5
    assert autonomy_rank("L2") < autonomy_rank("L3")
    assert autonomy_at_least("L3", "L2")
    assert not autonomy_at_least("L1", "L2")
    with pytest.raises(RoleRegistryError) as caught:
        autonomy_rank("L6")
    assert caught.value.code == ROLE_BAD_AUTONOMY_CODE


def test_lookup_and_filter_helpers(registry):
    assert registry.get_role("exec-ceo").title.startswith("Chief Executive")
    assert registry.get_department("finance").title == "Finance"
    assert registry.get_floor("floor-001").index == 1

    finance = registry.filter_roles(department="finance")
    assert finance and all(role.department == "finance" for role in finance)

    crm_roles = registry.filter_roles(capability="crm")
    assert crm_roles and all(role.matches_capability("crm") for role in crm_roles)

    ecommerce = registry.filter_roles(service_line="ecommerce")
    assert ecommerce and all(
        "ecommerce" in role.service_lines for role in ecommerce
    )

    l1 = registry.filter_roles(autonomy="L1")
    assert l1 and all(role.autonomy == "L1" for role in l1)
    l3_plus = registry.filter_roles(autonomy_min="L3")
    assert l3_plus and all(role.autonomy_rank >= 3 for role in l3_plus)
    low = registry.filter_roles(autonomy_max="L1")
    assert low and all(role.autonomy_rank <= 1 for role in low)
    assert registry.filter_roles(department="finance", autonomy="L1")

    with pytest.raises(RoleRegistryError) as caught:
        registry.filter_roles(department="ghost")
    assert caught.value.code == ROLE_UNKNOWN_DEPARTMENT_CODE
    with pytest.raises(RoleRegistryError) as caught:
        registry.get_role("ghost-role")
    assert caught.value.code == ROLE_BAD_INPUT_CODE
    with pytest.raises(RoleRegistryError) as caught:
        registry.get_floor("floor-999")
    assert caught.value.code == ROLE_BAD_FLOOR_CODE


# --- 100-floor org templates -------------------------------------------
def test_100_floor_and_wing_templates_are_complete(registry):
    floors = registry.floors
    assert len(floors) == 100
    assert [floor.index for floor in floors] == list(range(1, 101))
    assert len({floor.floor_id for floor in floors}) == 100
    assert len({floor.wing for floor in floors}) == 5

    for floor in floors:
        assert floor.department_ids
        for dept_id in floor.department_ids:
            registry.get_department(dept_id)
        assert floor.role_ids
        for role_id in floor.role_ids:
            registry.get_role(role_id)
        assert floor.rooms
        assert floor.desk_capacity == sum(room.desks for room in floor.rooms)
        assert floor.max_rooms is None
        assert floor.max_agents is None
        assert floor.expansion_allowed is True
        for room in floor.rooms:
            assert room.desks > 0
            assert set(room.role_ids) <= set(floor.role_ids)


def test_active_and_dormant_floors_materialize_only_active_roles(registry):
    assert len(registry.active_floors) == len(registry.departments)
    assert len(registry.dormant_floors) == 100 - len(registry.departments)
    assert registry.filter_floors(active=True) == registry.active_floors
    assert registry.filter_floors(active=False) == registry.dormant_floors
    assert registry.filter_floors(wing="north")
    assert all(floor.wing == "north" for floor in registry.filter_floors(wing="north"))
    assert registry.filter_floors(department="finance")

    all_role_ids = {role.id for role in registry.roles}
    materialized = set(registry.materialized_role_ids(active_only=True))
    assert materialized == all_role_ids
    assert set(registry.materialized_role_ids(active_only=False)) == all_role_ids
    dormant_assigned = {
        role_id
        for floor in registry.dormant_floors
        for role_id in floor.role_ids
    }
    assert dormant_assigned <= all_role_ids


def test_floor_validation_refuses_unknown_department_and_role(registry):
    payload = json.loads(json.dumps(registry.to_dict()))
    bad_role = json.loads(json.dumps(payload))
    bad_role["floors"][0]["role_ids"].append("ghost-role")
    with pytest.raises(RoleRegistryError) as caught:
        RoleRegistry.from_dict(bad_role)
    assert caught.value.code == ROLE_BAD_FLOOR_CODE

    bad_dept = json.loads(json.dumps(payload))
    bad_dept["floors"][0]["department_ids"] = ["ghost-department"]
    with pytest.raises(RoleRegistryError) as caught:
        RoleRegistry.from_dict(bad_dept)
    assert caught.value.code == ROLE_UNKNOWN_DEPARTMENT_CODE


def test_floor_templates_have_no_hard_cap(registry):
    for floor in registry.floors:
        assert isinstance(floor, FloorTemplate)
        assert floor.max_rooms is None
        assert floor.max_agents is None
    floor = registry.get_floor("floor-001")
    resolved = floor.to_dict()
    assert resolved["max_rooms"] is None
    assert resolved["max_agents"] is None
    assert resolved["expansion_allowed"] is True
    # A floor can be extended beyond the planned desks/rooms.
    assert floor.desk_capacity == sum(room.desks for room in floor.rooms)


def test_department_dataclass_round_trips(registry):
    dept = registry.get_department("legal_compliance")
    assert Department.from_dict(dept.to_dict()) == dept
    assert dept.to_dict()["service_lines"] == list(dept.service_lines)
