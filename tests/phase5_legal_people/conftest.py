"""Shared hermetic fixtures for the P5-06/P5-07 lane.

Every fixture is a throwaway SQLite file under ``tmp_path``; no network, no
live service, no credential and no external legal/HR/payment side effect.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore


@pytest.fixture
def store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


@pytest.fixture
def scope(store):
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    return tenant, business
