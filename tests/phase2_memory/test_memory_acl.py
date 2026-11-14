"""P2-04 — per-record ACL, cross-scope denial, tenant isolation, no global pool.

The injected ACL is consulted for every candidate record on read, and once on
write. A denial is silent (the record is withheld) on read and a typed refusal
on write. Nothing here relies on the projection or on a second store.
"""
from __future__ import annotations

from voicebridge.business import eventlog, identity
from voicebridge.business.errors import BusinessError
from voicebridge.business.memory import (
    MEMORY_WRITE_DENIED_CODE,
    MemoryService,
    SameTenantACL,
)


def new_principal(store, tenant_id, kind: str = "agent"):
    return identity.create_principal(store, tenant_id, kind=kind)


class _DenyIds(SameTenantACL):
    def __init__(self, denied: set[str]) -> None:
        self.denied = set(denied)
        self.read_calls: list[str] = []

    def can_read(self, principal, record):
        self.read_calls.append(record.memory_id)
        return record.memory_id not in self.denied


class _DenyWrites:
    def __init__(self) -> None:
        self.calls = 0

    def can_read(self, principal, record):
        return True

    def can_write(self, principal, scope, record):
        self.calls += 1
        return False


def _remember(service, principal, **overrides):
    values = {
        "scope": "department",
        "scope_id": "dept-a",
        "content": "A department fact.",
        "source": "owner",
    }
    values.update(overrides)
    return service.remember(principal, **values)


def test_acl_is_enforced_per_record(store, tmp_path, principal):
    acl = _DenyIds(set())
    service = MemoryService(store, tmp_path / "projection", acl)
    first = _remember(service, principal, content="alpha invoice")
    second = _remember(service, principal, content="beta invoice")
    acl.denied.add(first.memory_id)

    hits = service.recall(principal, "department", "invoice", 10, scope_id="dept-a")
    assert [hit.memory_id for hit in hits] == [second.memory_id]
    # Both records were offered to the ACL: the denial is per record, not a
    # whole-scope shortcut.
    assert set(acl.read_calls) == {first.memory_id, second.memory_id}


def test_write_denial_is_typed_and_writes_nothing(store, tmp_path, tenant, principal):
    acl = _DenyWrites()
    service = MemoryService(store, tmp_path / "projection", acl)
    try:
        _remember(service, principal)
    except BusinessError as exc:
        assert exc.code == MEMORY_WRITE_DENIED_CODE
    else:  # pragma: no cover - the refusal is the contract
        raise AssertionError("write was admitted without ACL permission")
    assert acl.calls == 1
    assert eventlog.read_events(
        store, tenant.tenant_id, "memory:department:dept-a"
    ) == []


def test_cross_scope_reads_are_separate_aggregates(service, principal):
    a = _remember(service, principal, scope="department", scope_id="dept-a",
                  content="Dept A secret.")
    b = _remember(service, principal, scope="department", scope_id="dept-b",
                  content="Dept B secret.")

    hits_a = service.recall(principal, "department", "secret", 10, scope_id="dept-a")
    hits_b = service.recall(principal, "department", "secret", 10, scope_id="dept-b")
    assert [hit.memory_id for hit in hits_a] == [a.memory_id]
    assert [hit.memory_id for hit in hits_b] == [b.memory_id]


def test_cross_tenant_reads_return_nothing(store, tmp_path, tenant, principal):
    service = MemoryService(store, tmp_path / "projection", SameTenantACL())
    secret = _remember(service, principal, scope="agent", scope_id="seat-1",
                       content="Tenant A only.")

    other = store.create_tenant("Other Holdings")
    store.create_business(other.tenant_id, "Other", slug="other")
    outsider = new_principal(store, other.tenant_id)

    assert service.recall(
        outsider, "agent", "tenant", 10, scope_id="seat-1"
    ) == []
    # The same aggregate name in another tenant is not the same memory.
    try:
        service.get(secret.memory_id, tenant_id=other.tenant_id)
    except BusinessError as exc:
        assert exc.code == "BUSINESS_MEMORY_NOT_FOUND"
    else:  # pragma: no cover
        raise AssertionError("cross-tenant lookup resolved the record")
    assert tenant.tenant_id != other.tenant_id


def test_principal_tenant_cannot_write_another_tenant(store, tmp_path, principal):
    other = store.create_tenant("Other Holdings")
    store.create_business(other.tenant_id, "Other", slug="other")
    outsider = new_principal(store, other.tenant_id)
    service = MemoryService(store, tmp_path / "projection", SameTenantACL())
    try:
        service.remember(
            outsider,
            scope="office",
            scope_id="office-1",
            content="Cross-tenant write.",
            tenant_id=principal.tenant_id,
        )
    except BusinessError as exc:
        assert exc.code == MEMORY_WRITE_DENIED_CODE
    else:  # pragma: no cover
        raise AssertionError("cross-tenant write was admitted")


def test_there_is_no_global_pool(service, principal):
    agent_record = _remember(
        service, principal, scope="agent", scope_id="seat-1",
        content="Agent-private note.",
    )
    office_record = _remember(
        service, principal, scope="office", scope_id="office-1",
        content="Office-wide note.",
    )
    office_hits = service.recall(
        principal, "office", "note", 10, scope_id="office-1"
    )
    assert [hit.memory_id for hit in office_hits] == [office_record.memory_id]
    assert agent_record.memory_id not in {hit.memory_id for hit in office_hits}
