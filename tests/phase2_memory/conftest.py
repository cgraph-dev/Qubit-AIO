"""Shared hermetic fixtures for the P2-04 memory tests.

One throwaway SQLite file and one throwaway projection directory per test. No
network, no live service, no operator state.
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


def new_principal(store: BusinessStore, tenant_id: str, kind: str = "agent"):
    return identity.create_principal(store, tenant_id, kind=kind)


@pytest.fixture
def principal(store, tenant):
    return new_principal(store, tenant.tenant_id)


@pytest.fixture
def service(store, tmp_path) -> MemoryService:
    return MemoryService(
        store,
        tmp_path / "projection",
        SameTenantACL(),
    )
