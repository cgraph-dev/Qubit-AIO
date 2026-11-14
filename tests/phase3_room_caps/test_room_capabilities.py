"""P3-02 — every shipped Office room maps to a real workflow/capability.

Hermetic: no store, no service, no network, no clock. These tests pin the
contract from ``docs/QUBIT-OFFICE-COMPLETE-PLAN.md`` §7 (P3-02) and
``docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md`` §2 "Room/Capability":

* every room in the shipped role/floors registry resolves to a manifest;
* a room without a manifest (a decorative room) is a typed failure;
* manifests compose role ids from ``RoleRegistry`` and capability ids from a
  catalog — unknown ids are typed refusals;
* the Trading Department keeps execution, risk and approval authority
  separated, and execution cannot mutate risk limits;
* wildcard capabilities never validate;
* manifest and registry digests are deterministic.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from voicebridge.business import policy as pol
from voicebridge.business import room_capabilities as rc
from voicebridge.business.errors import BusinessError
from voicebridge.business.roles import RoleRegistry, RoomTemplate


@pytest.fixture(scope="module")
def roles() -> RoleRegistry:
    return RoleRegistry.default()


@pytest.fixture(scope="module")
def catalog() -> pol.CapabilityCatalog:
    return rc.shipped_capability_catalog()


@pytest.fixture(scope="module")
def registry(roles, catalog) -> rc.RoomCapabilityRegistry:
    return rc.build_shipped_registry(roles=roles, capabilities=catalog)


def _base_manifest(**overrides) -> rc.RoomCapabilityManifest:
    values = {
        "manifest_id": "roomcap:test:work",
        "room_kinds": ("work",),
        "archetypes": (),
        "departments": ("finance",),
        "workflow_id": "finance.work",
        "workflow_name": "Finance work execution",
        "required_role_ids": ("finance-bookkeeper",),
        "required_capabilities": ("office.work.execute",),
        "allowed_tools": ("task.board",),
        "artifact_kinds": ("note",),
        "queue_kind": "department_work",
        "task_kind": "department_task",
        "kpi_ids": ("throughput",),
        "memory_scope": "department",
        "privacy_class": "department",
        "approval_policy": "none",
        "required_agent_count": 1,
        "room_modes": ("work",),
    }
    values.update(overrides)
    return rc.RoomCapabilityManifest(**values)


# -- coverage --------------------------------------------------------------
def test_every_shipped_room_is_covered(registry, roles):
    report = registry.validate_coverage(roles)
    assert report.total_rooms == 300
    assert report.covered_rooms == report.total_rooms
    assert report.uncovered_rooms == 0
    all_manifests = {manifest.manifest_id for manifest in registry.manifests}
    assert set(report.manifests_used) == all_manifests
    assert set(report.by_department) == {dept.id for dept in roles.departments}


def test_a_decorative_room_fails_coverage(registry, roles, catalog):
    without_war_rooms = rc.RoomCapabilityRegistry(
        [
            manifest
            for manifest in registry.manifests
            if "war_room" not in manifest.room_kinds
        ],
        roles=roles,
        capabilities=catalog,
    )
    with pytest.raises(BusinessError) as exc:
        without_war_rooms.validate_coverage(roles)
    assert exc.value.code == rc.ROOM_CAPABILITY_UNCOVERED_CODE
    assert "war_room" in str(exc.value)


def test_a_manifest_cannot_omit_its_workflow():
    with pytest.raises(BusinessError) as exc:
        _base_manifest(workflow_id="")
    assert exc.value.code == rc.ROOM_CAPABILITY_INVALID_CODE


def test_a_manifest_cannot_omit_roles_or_capabilities():
    for field in ("required_role_ids", "required_capabilities"):
        with pytest.raises(BusinessError) as exc:
            _base_manifest(**{field: ()})
        assert exc.value.code == rc.ROOM_CAPABILITY_INVALID_CODE


# -- lookup / refusal ------------------------------------------------------
def test_unknown_room_kind_and_archetype_are_refused(registry):
    with pytest.raises(BusinessError) as exc:
        registry.manifests_for_kind("greenhouse")
    assert exc.value.code == rc.ROOM_CAPABILITY_UNKNOWN_ROOM_CODE

    with pytest.raises(BusinessError) as exc:
        registry.manifests_for_archetype("greenhouse")
    assert exc.value.code == rc.ROOM_CAPABILITY_UNKNOWN_ROOM_CODE

    room = RoomTemplate(
        room_id="room-gh", name="Greenhouse", kind="greenhouse", desks=2
    )
    with pytest.raises(BusinessError) as exc:
        registry.manifest_for(room, department_id="finance")
    assert exc.value.code == rc.ROOM_CAPABILITY_UNKNOWN_ROOM_CODE


def test_unknown_manifest_id_is_refused(registry):
    with pytest.raises(BusinessError) as exc:
        registry.get("roomcap:nope:nope")
    assert exc.value.code == rc.ROOM_CAPABILITY_UNKNOWN_ROOM_CODE


def test_manifest_lookup_is_department_scoped(registry):
    finance_work = registry.manifest_for(
        RoomTemplate(
            room_id="room-f", name="Finance floor", kind="work", desks=20
        ),
        department_id="finance",
    )
    legal_work = registry.manifest_for(
        RoomTemplate(
            room_id="room-l", name="Legal floor", kind="work", desks=20
        ),
        department_id="legal_compliance",
    )
    assert finance_work.manifest_id == "roomcap:finance:work"
    assert legal_work.manifest_id == "roomcap:legal_compliance:work"
    assert finance_work.manifest_id != legal_work.manifest_id


# -- composition (roles + capabilities) ------------------------------------
def test_role_ids_compose_from_the_role_registry(registry, roles):
    known = {role.id for role in roles.roles}
    for manifest in registry.manifests:
        assert set(manifest.required_role_ids) <= known


def test_unknown_role_is_refused(roles, catalog):
    manifest = _base_manifest(required_role_ids=("no-such-role",))
    with pytest.raises(BusinessError) as exc:
        rc.RoomCapabilityRegistry(
            [manifest], roles=roles, capabilities=catalog
        )
    assert exc.value.code == rc.ROOM_CAPABILITY_UNKNOWN_ROLE_CODE


def test_capability_not_in_catalog_is_refused(roles):
    tiny = pol.CapabilityCatalog(
        [
            pol.Capability(
                capability_id="office.work.assign",
                category="office",
                resource="task",
                action="assign",
            )
        ]
    )
    with pytest.raises(BusinessError) as exc:
        rc.build_shipped_registry(roles=roles, capabilities=tiny)
    assert exc.value.code == rc.ROOM_CAPABILITY_UNKNOWN_CAPABILITY_CODE


def test_no_wildcard_capabilities(registry):
    with pytest.raises(BusinessError) as exc:
        _base_manifest(required_capabilities=("*",))
    assert exc.value.code == rc.ROOM_CAPABILITY_WILDCARD_CODE

    wildcards = {"*", "all", "any", "ALL", "ANY", "All", "Any"}
    for manifest in registry.manifests:
        for capability_id in manifest.required_capabilities:
            assert capability_id not in wildcards
            assert "*" not in capability_id


# -- trading separation ----------------------------------------------------
def test_trading_room_capability_manifests_exist(registry):
    kinds = {manifest for manifest in registry.counts_by_kind()}
    assert {
        "trading_floor",
        "risk_room",
        "strategy_lab",
        "data_room",
        "treasury",
        "compliance_archive",
        "incident_room",
    } <= kinds
    assert registry.counts_by_department()[rc.TRADING_DEPARTMENT] == 11


def test_trading_execution_cannot_change_risk_limits(registry):
    execution = registry.manifests_for_kind("trading_floor")
    assert len(execution) == 1
    floor = execution[0]
    assert floor.approval_policy == "required"
    assert floor.execution_capabilities == ("trading.order.execute",)
    assert "trading.risk.limits.write" not in floor.execution_capabilities
    assert "trading.risk.limits.write" not in floor.required_capabilities
    assert set(floor.execution_capabilities).isdisjoint(
        set(floor.risk_capabilities)
        | set(floor.approval_capabilities)
        | set(floor.risk_limit_capabilities)
    )
    assert set(floor.execution_role_ids).isdisjoint(
        set(floor.risk_role_ids) | set(floor.approval_role_ids)
    )

    risk = registry.manifests_for_kind("risk_room")[0]
    assert "trading.risk.limits.write" in risk.risk_capabilities
    assert not risk.execution_capabilities


def test_execution_manifest_with_shared_authority_is_refused(registry):
    floor = registry.manifests_for_kind("trading_floor")[0]
    with pytest.raises(BusinessError) as exc:
        replace(floor, approval_policy="none")
    assert exc.value.code == rc.ROOM_CAPABILITY_SEPARATION_CODE

    with pytest.raises(BusinessError) as exc:
        replace(
            floor,
            risk_limit_capabilities=("trading.order.execute",),
        )
    assert exc.value.code == rc.ROOM_CAPABILITY_SEPARATION_CODE

    with pytest.raises(BusinessError) as exc:
        replace(floor, risk_role_ids=("finance-payments",))
    assert exc.value.code == rc.ROOM_CAPABILITY_SEPARATION_CODE


# -- determinism / counts --------------------------------------------------
def test_digests_are_deterministic(roles, catalog):
    first = rc.build_shipped_registry(roles=roles, capabilities=catalog)
    second = rc.build_shipped_registry(roles=roles, capabilities=catalog)
    assert first.digest == second.digest
    assert [m.digest for m in first.manifests] == [
        m.digest for m in second.manifests
    ]
    changed = replace(first.manifests[0], approval_policy="conditional")
    assert changed.digest != first.manifests[0].digest


def test_counts_by_department_and_kind(registry, roles):
    counts = registry.counts_by_department()
    for department in roles.departments:
        if department.id == rc.TRADING_DEPARTMENT:
            assert counts[department.id] == 11
        else:
            assert counts[department.id] == 3
    assert counts[rc.TRADING_DEPARTMENT] == 11
    assert sum(registry.counts_by_kind().values()) == len(registry.manifests)


def test_every_manifest_has_a_real_workflow_and_artifacts(registry):
    for manifest in registry.manifests:
        assert manifest.workflow_id
        assert manifest.workflow_name
        assert manifest.queue_kind
        assert manifest.task_kind
        assert manifest.kpi_ids
        assert manifest.artifact_kinds
        assert manifest.required_agent_count >= 1
        assert manifest.room_modes
        assert manifest.privacy_class in rc.PRIVACY_CLASSES
        assert manifest.approval_policy in pol.APPROVAL_POLICIES


def test_archetype_lookup_resolves_a_world_room(registry):
    class _WorldRoom:
        room_id = "world-trading-1"
        kind = ""
        archetype = "trading_floor"
        department_id = rc.TRADING_DEPARTMENT

    manifest = registry.manifest_for(_WorldRoom())
    assert manifest.manifest_id == "roomcap:trading:trading_floor"
    assert manifest.workflow_id == "trading.trading_execution"


def test_a_dynamic_capability_catalog_is_accepted(roles):
    from voicebridge.business import dynamic_catalog as dc

    records = [
        dc.CapabilityRecord(
            capability_id=capability.capability_id,
            category=capability.category,
            source="tools",
            provider="test",
            resource=capability.resource,
            action=capability.action,
            risk=capability.risk,
            approval_policy=capability.approval_policy,
        )
        for capability in rc.shipped_capability_catalog().list()
    ]

    class _Source:
        source_id = "test"

        def capabilities(self):
            return tuple(records)

    catalog = dc.DynamicCapabilityCatalog([_Source()])
    composed = rc.build_shipped_registry(roles=roles, capabilities=catalog)
    expected = len(rc.build_shipped_manifests(roles)) + len(rc.build_trading_manifests())
    assert len(composed) == expected
    assert composed.validate_coverage(roles).covered_rooms == 300
