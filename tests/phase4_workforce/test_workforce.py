"""P4-01 — every Business OS agent has a real durable workforce lane.

Hermetic: a throwaway SQLite file under ``tmp_path``. These tests pin the
workforce contract the Office relies on: provisioning is idempotent, each
non-archived agent has exactly one session/workspace, non-working states are
explicit (never a working desk), a seed/projection agent is not ``live``,
restart reconciliation claims nothing, tenants are isolated, and no grant
carries a wildcard.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from voicebridge.business import identity
from voicebridge.business import sessions as SESSIONS
from voicebridge.business import workforce
from voicebridge.business.errors import BusinessError
from voicebridge.business.store import BusinessStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_workforce.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_workforce_test", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path):
    verifier = _load_verifier()
    return verifier, verifier._seed_fixture(tmp_path / "business")


def _lane(fixture, key):
    agent = fixture["agents"][key]
    return fixture["lanes"][agent.agent_id]


def _sql(store: BusinessStore, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_provision_is_idempotent_and_one_lane_per_agent(tmp_path):
    _, fixture = _fixture(tmp_path)
    store = fixture["store"]
    tenant = fixture["tenant"]
    business = fixture["business"]
    first = fixture["provisioned"]

    second = workforce.provision_workforce(
        store, tenant.tenant_id, business.business_id, actor="owner"
    )
    assert second["created_sessions"] == 0
    assert second["created_tasks"] == 0
    assert second["created_workspaces"] == 0
    assert second["created_roles"] == 0
    assert second["created_grants"] == 0
    assert {item["agent_id"]: item["session_id"] for item in first["agents"]} == {
        item["agent_id"]: item["session_id"] for item in second["agents"]
    }

    for key, agent in fixture["agents"].items():
        if key == "archived":
            continue
        sessions = _sql(
            store,
            "SELECT session_id FROM bos_session WHERE tenant_id=?"
            " AND business_id=? AND agent_id=?",
            (tenant.tenant_id, business.business_id, agent.agent_id),
        )
        assert len(sessions) == 1, f"{key} has exactly one durable session"
        workspaces = _sql(
            store,
            "SELECT workspace_id FROM bos_workspace WHERE tenant_id=?"
            " AND session_id=?",
            (tenant.tenant_id, sessions[0]["session_id"]),
        )
        assert len(workspaces) == 1, f"{key} has exactly one pinned workspace"


def test_non_working_states_never_occupy_a_working_desk(tmp_path):
    _, fixture = _fixture(tmp_path)
    status = workforce.workforce_status(
        fixture["store"],
        fixture["tenant"].tenant_id,
        fixture["business"].business_id,
    )
    by_id = {item["agent_id"]: item for item in status["agents"]}
    expected = {
        "paused": "paused",
        "revoked": "revoked",
        "archived": "archived",
        "projection": "projection",
        "expired": "unknown",
        "idle": "idle",
    }
    for key, state in expected.items():
        agent = fixture["agents"][key]
        view = by_id[agent.agent_id]
        assert view["state"] == state, key
        assert view["working"] is (state in workforce.WORKING_STATES), key
    assert status["counts"]["working"] == 1
    assert status["counts"]["non_working"] == len(by_id) - 1
    assert status["working_states"] == ["idle", "live"]


def test_projection_agent_is_not_live(tmp_path):
    _, fixture = _fixture(tmp_path)
    projection = fixture["agents"]["projection"]
    status = workforce.workforce_status(
        fixture["store"],
        fixture["tenant"].tenant_id,
        fixture["business"].business_id,
    )
    view = next(
        item for item in status["agents"]
        if item["agent_id"] == projection.agent_id
    )
    assert view["state"] == "projection"
    assert view["state"] != "live"
    assert view["working"] is False
    assert "projection" in view["reason"]


def test_restart_reconcile_reports_expired_lane_without_claiming(tmp_path):
    _, fixture = _fixture(tmp_path)
    store = fixture["store"]
    tenant = fixture["tenant"]
    business = fixture["business"]
    lane = _lane(fixture, "expired")

    before_sessions = _sql(
        store,
        "SELECT session_id, status FROM bos_session WHERE tenant_id=?",
        (tenant.tenant_id,),
    )
    before_leases = _sql(
        store, "SELECT lease_id FROM bos_lease WHERE tenant_id=?",
        (tenant.tenant_id,),
    )
    report = workforce.reconcile_workforce(
        store, tenant.tenant_id, business.business_id
    )
    entry = next(
        item for item in report["lanes"]
        if item["agent_id"] == fixture["agents"]["expired"].agent_id
    )
    assert report["mutated"] is False
    assert entry["claimed"] is False
    assert entry["state"] == "unknown"
    assert entry["lease_state"] == "expired"
    assert entry["expired_leases"] == 1
    assert report["counts"]["claimed"] == 0

    after_sessions = _sql(
        store,
        "SELECT session_id, status FROM bos_session WHERE tenant_id=?",
        (tenant.tenant_id,),
    )
    after_leases = _sql(
        store, "SELECT lease_id FROM bos_lease WHERE tenant_id=?",
        (tenant.tenant_id,),
    )
    assert [tuple(row) for row in after_sessions] == [
        tuple(row) for row in before_sessions
    ]
    assert [tuple(row) for row in after_leases] == [
        tuple(row) for row in before_leases
    ]
    assert lane["session_id"] in {row["session_id"] for row in after_sessions}


def test_tenant_isolation(tmp_path):
    _, fixture = _fixture(tmp_path)
    store = fixture["store"]
    other = fixture["other"]
    status = workforce.workforce_status(
        store, other["tenant"].tenant_id, other["business"].business_id
    )
    assert {item["agent_id"] for item in status["agents"]} == {
        other["agent"].agent_id
    }
    with pytest.raises(BusinessError) as excinfo:
        workforce.workforce_status(
            store,
            other["tenant"].tenant_id,
            fixture["business"].business_id,
        )
    assert excinfo.value.code == "BUSINESS_NOT_FOUND"


def test_grants_are_explicit_and_never_wildcard(tmp_path):
    _, fixture = _fixture(tmp_path)
    store = fixture["store"]
    tenant = fixture["tenant"]
    lane = _lane(fixture, "idle")
    grants = identity.list_grants(
        store, tenant.tenant_id, subject_id=lane["principal_id"]
    )
    assert grants
    for grant in grants:
        for value in (grant.capability, grant.resource, grant.action):
            assert "*" not in value and "?" not in value
    pairs = {
        (grant.capability, grant.resource, grant.action) for grant in grants
    }
    assert ("session", lane["session_id"], "use") in pairs
    assert ("workspace", lane["workspace_id"], "use") in pairs
    assert ("task", lane["task_id"], "use") in pairs
    with pytest.raises(BusinessError) as excinfo:
        identity.grant(
            store,
            tenant.tenant_id,
            subject_id=lane["principal_id"],
            capability="*",
            resource="*",
            action="*",
        )
    assert excinfo.value.code == "BUSINESS_WILDCARD_NOT_ALLOWED"


def test_memory_policy_is_scope_names_only(tmp_path):
    _, fixture = _fixture(tmp_path)
    store = fixture["store"]
    lane = _lane(fixture, "idle")
    session = SESSIONS.get_session(
        store, fixture["tenant"].tenant_id, lane["session_id"]
    )
    assert session.default_memory_scopes
    assert set(session.default_memory_scopes) <= set(SESSIONS.MEMORY_SCOPES)
    tables = {
        row["name"]
        for row in _sql(
            store, "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert not any("memory" in name for name in tables), (
        "P4-01 records scope names only; no memory table is introduced"
    )


def test_verifier_catches_planted_inconsistency(tmp_path):
    verifier, fixture = _fixture(tmp_path)
    good = verifier.verify_workforce_store(
        fixture["store"],
        fixture["tenant"].tenant_id,
        fixture["business"].business_id,
    )
    assert good["ok"] is True
    assert good["checks_failed"] == 0
    assert good["checks_total"] >= 10

    planted = verifier._plant_inconsistencies(tmp_path / "planted")
    bad = verifier.verify_workforce_store(
        planted["store"],
        planted["tenant"].tenant_id,
        planted["business"].business_id,
    )
    assert bad["ok"] is False
    failed = {check["name"] for check in bad["checks"] if not check["ok"]}
    assert "no_wildcard_grants" in failed
    assert "session_workspace_links" in failed


def test_verifier_self_check_passes():
    verifier = _load_verifier()
    report = verifier.run_self_check()
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    names = {check["name"] for check in report["checks"]}
    assert "planted_inconsistency_detected" in names
    assert "expired_lane_is_unknown" in names
    assert "projection_not_live" in names


def test_provision_rejects_unknown_agent(tmp_path):
    _, fixture = _fixture(tmp_path)
    with pytest.raises(BusinessError) as excinfo:
        workforce.provision_workforce(
            fixture["store"],
            fixture["tenant"].tenant_id,
            fixture["business"].business_id,
            agents=["agt_does_not_exist"],
            actor="owner",
        )
    assert excinfo.value.code == "WORKFORCE_AGENT_MISSING"
