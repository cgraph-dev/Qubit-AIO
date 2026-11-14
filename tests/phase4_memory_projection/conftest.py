"""Hermetic fixtures for the P4-05 memory projection tests.

One throwaway SQLite store and projection root per test. No network, no live
service, no operator state.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore, identity
from voicebridge.business.memory import MemoryService, SameTenantACL


@pytest.fixture
def store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


@pytest.fixture
def scope(store):
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    return tenant, business


@pytest.fixture
def tenant(scope):
    return scope[0]


@pytest.fixture
def principal(store, tenant):
    return identity.create_principal(store, tenant.tenant_id, kind="agent")


@pytest.fixture
def service(store, tmp_path) -> MemoryService:
    return MemoryService(store, tmp_path / "unused-projection", SameTenantACL())


class DepartmentACL:
    """Reference viewer ACL used to prove department isolation."""

    def __init__(self, department: str, *, tenant_id: str) -> None:
        self.department = department
        self.tenant_id = tenant_id

    def _department(self, record) -> str | None:
        raw = record.provenance.get("department")
        if raw:
            return str(raw)
        if record.scope == "department":
            return record.scope_id
        if record.scope == "agent" and "/" in record.scope_id:
            return record.scope_id.split("/", 1)[0]
        return None

    def can_read(self, principal, record) -> bool:
        if record.tenant_id != self.tenant_id:
            return False
        if record.visibility in ("office", "public"):
            return True
        return self._department(record) == self.department

    def can_write(self, principal, scope, record) -> bool:
        return record.tenant_id == self.tenant_id


@pytest.fixture
def department_acl(tenant):
    def make(department: str) -> DepartmentACL:
        return DepartmentACL(department, tenant_id=tenant.tenant_id)

    return make


__all__ = ["DepartmentACL"]
