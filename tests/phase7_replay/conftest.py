"""P7-06 fixtures; helper functions live in _replay_helpers."""
from __future__ import annotations

import pytest

from _replay_helpers import (
    BusinessStore, _Cfg, interaction_history,
)

@pytest.fixture()
def history() -> list[dict]:
    return interaction_history()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _Cfg(tmp_path / "state")
    business_store = BusinessStore.from_config(cfg)
    tenant = business_store.create_tenant("Simulation Audit")
    business = business_store.create_business(
        tenant.tenant_id, "Simulation Business", slug="simulation")
    return business_store, tenant, business


