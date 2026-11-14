"""P4-05 — OpenViking scopes: office/global, department, department/agent.

The injected adapter is authenticated and deny-by-default. A scoped gate can
only touch its fixed collections; a cross-scope write or read is a typed
refusal, never a global fallback.
"""
from __future__ import annotations

import pytest

from voicebridge.business.errors import BusinessError
from voicebridge.business.memory import (
    MEMORY_OPENVIKING_FAILED_CODE,
    InMemoryOpenViking,
    MemoryService,
    SameTenantACL,
)
from voicebridge.business.memory_projection import (
    MEMORY_PROJECTION_SCOPE_DENIED_CODE,
    OPENVIKING_GLOBAL_SCOPE,
    OpenVikingScopeGate,
    agent_scope,
    department_scope,
    scope_collections,
)


def test_fixed_scope_shapes():
    assert OPENVIKING_GLOBAL_SCOPE == "office/global"
    assert department_scope("engineering") == "office/engineering"
    assert agent_scope("engineering", "seat-7") == "office/engineering/seat-7"
    assert scope_collections(
        department="engineering", agent="seat-7"
    ) == ("office/engineering", "office/engineering/seat-7")
    assert scope_collections(
        department="engineering", agent="seat-7", allow_global=True
    ) == (
        "office/global",
        "office/engineering",
        "office/engineering/seat-7",
    )


def test_agent_scope_without_a_department_is_refused():
    with pytest.raises(BusinessError) as caught:
        scope_collections(agent="seat-7")
    assert caught.value.code == MEMORY_PROJECTION_SCOPE_DENIED_CODE


def test_department_gate_allows_its_scope_and_refuses_everything_else():
    adapter = InMemoryOpenViking(["tenant/t1"])
    gate = OpenVikingScopeGate(adapter, department="engineering")
    assert gate.allowed_collections == ("office/engineering",)

    gate.write(
        "tenant/t1", "office/engineering", "mem-1", "ok", {"scope": "department"}
    )
    assert set(adapter.records("tenant/t1", "office/engineering")) == {"mem-1"}
    assert gate.records("tenant/t1", "office/engineering")["mem-1"]["content"] == "ok"

    for collection in ("office/global", "office/finance"):
        with pytest.raises(BusinessError) as caught:
            gate.write("tenant/t1", collection, "mem-2", "leak", {})
        assert caught.value.code == MEMORY_PROJECTION_SCOPE_DENIED_CODE
        with pytest.raises(BusinessError) as caught:
            gate.records("tenant/t1", collection)
        assert caught.value.code == MEMORY_PROJECTION_SCOPE_DENIED_CODE
        with pytest.raises(BusinessError) as caught:
            gate.delete("tenant/t1", collection, "mem-2")
        assert caught.value.code == MEMORY_PROJECTION_SCOPE_DENIED_CODE

    gate.delete("tenant/t1", "office/engineering", "mem-1")
    assert adapter.records("tenant/t1", "office/engineering") == {}


def test_agent_gate_reads_department_and_agent_but_no_global():
    adapter = InMemoryOpenViking(["tenant/t1"])
    gate = OpenVikingScopeGate(
        adapter, department="engineering", agent="seat-7"
    )
    assert gate.allowed_collections == (
        "office/engineering",
        "office/engineering/seat-7",
    )
    gate.write("tenant/t1", "office/engineering", "d-1", "dept", {})
    gate.write("tenant/t1", "office/engineering/seat-7", "a-1", "agent", {})
    assert set(gate.records("tenant/t1", "office/engineering")) == {"d-1"}
    assert set(gate.records("tenant/t1", "office/engineering/seat-7")) == {
        "a-1"
    }
    with pytest.raises(BusinessError):
        gate.write("tenant/t1", "office/global", "g-1", "global", {})


def test_service_mirror_through_the_gate_never_widens(
    store, tmp_path, tenant, principal
):
    namespace = f"tenant/{tenant.tenant_id}"
    adapter = InMemoryOpenViking([namespace])
    gate = OpenVikingScopeGate(adapter, department="engineering")
    service = MemoryService(
        store, tmp_path / "unused", SameTenantACL(), openviking=gate
    )

    service.remember(
        principal,
        scope="department",
        scope_id="engineering",
        content="Department-scoped memory.",
        source="owner",
        provenance={"writer": "owner", "department": "engineering"},
    )
    assert set(adapter.records(namespace, "office/engineering"))

    # A department gate cannot mirror an office/global record; the refusal is
    # typed and the adapter does not fall back to a wider collection.
    with pytest.raises(BusinessError) as caught:
        service.remember(
            principal,
            scope="office",
            scope_id="office-1",
            content="Office-wide memory through a department gate.",
            source="owner",
        )
    assert caught.value.code in (
        MEMORY_PROJECTION_SCOPE_DENIED_CODE,
        MEMORY_OPENVIKING_FAILED_CODE,
    )
    assert adapter.records(namespace, "office/global") == {}


def test_global_gate_allows_only_the_office_global_collection():
    adapter = InMemoryOpenViking(["tenant/t1"])
    gate = OpenVikingScopeGate(adapter, allow_global=True)
    assert gate.allowed_collections == ("office/global",)
    gate.write("tenant/t1", "office/global", "g-1", "global", {})
    assert set(gate.records("tenant/t1", "office/global")) == {"g-1"}
    for collection in ("office/engineering", "office/finance"):
        with pytest.raises(BusinessError) as caught:
            gate.write("tenant/t1", collection, "x", "leak", {})
        assert caught.value.code == MEMORY_PROJECTION_SCOPE_DENIED_CODE
