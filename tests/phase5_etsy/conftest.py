"""Pytest fixtures for the P5-01 Etsy operations tests."""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore


@pytest.fixture()
def store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


@pytest.fixture()
def scope(store):
    tenant = store.create_tenant("Pilot Holdings")
    business = store.create_business(tenant.tenant_id, "Pilot Shop", slug="pilot")
    return tenant, business
