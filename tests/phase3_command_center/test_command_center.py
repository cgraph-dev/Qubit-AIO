"""P3-05 command center: a read-only projection over real Business OS state.

Hermetic: a throwaway SQLite store under ``tmp_path``. These tests pin the
contract the Office shell depends on:

* entries are floors/rooms/desks/agents/tasks/approvals from the real stores;
* search and every filter are server-side and bounded;
* counts are derived from rows, never fabricated;
* a source that cannot be read is a typed ``unavailable`` state;
* the projection is tenant-gated and writes nothing.

No network, no credentials, no live service, no real business data.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge import qubit_compat as HC
from voicebridge import webui as W
from voicebridge.business import AgentControlService, BusinessStore
from voicebridge.business import approvals, identity, sessions
from voicebridge.business.command_center import SCHEMA, command_center
from voicebridge.business.errors import BusinessError


class _Cfg:
    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return str(self._state)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _Cfg(tmp_path)
    store = BusinessStore.from_config(cfg)
    return cfg, store


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _seed(store: BusinessStore, *, name="Acme", slug="acme"):
    tenant, business = _scope(store, name=name, slug=slug)
    owner = identity.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref=f"{slug}-owner")
    agent_principal = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref=f"{slug}-ada")
    service = AgentControlService(store)
    agent, _ = service.create(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.analyst",
        department="Research",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key=f"{slug}-agent",
        security_principal_id=agent_principal.principal_id,
        room="floor-001-main",
        desk="d1",
    )
    session = sessions.create_session(
        store, tenant.tenant_id, business.business_id, agent.agent_id,
        owner_principal=owner.principal_id, actor="owner",
        task_title="Draft the report",
    )
    task = store.create_task(
        tenant.tenant_id, business.business_id, "Ship the report",
        assigned_to=agent.agent_id,
    )
    approval, _ = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="publish",
        requested_by=agent.agent_id,
        idempotency_key=f"{slug}-approval",
        payload={"risk": "low"},
    )
    return {
        "tenant": tenant,
        "business": business,
        "agent": agent,
        "session": session,
        "task": task,
        "approval": approval,
    }


def _call(cfg, query=None):
    return HC.dispatch(
        cfg, "GET", "/api/office/business/command-center",
        query=query or {}, body={})


# ---------------------------------------------------------------------------
# Route and authority
# ---------------------------------------------------------------------------
def test_route_is_registered_and_is_a_read_authority():
    route, _ = HC.match("GET", "/api/office/business/command-center")
    assert route is not None
    assert route["handler"] == "h_business_command_center"
    assert W.qubit_read_authority("/api/office/business/command-center")


def test_route_matches_an_unknown_tenant_as_a_typed_refusal(env):
    cfg, store = env
    tenant, _ = _scope(store)
    status, body = _call(cfg, {"tenant_id": "ten_missing"})
    assert status == 404
    assert body["ok"] is False
    assert body["code"] == "BUSINESS_NOT_FOUND"
    # A real tenant is served.
    status, body = _call(cfg, {"tenant_id": tenant.tenant_id})
    assert status == 200
    assert body["ok"] is True
    assert body["projection"]["schema"] == SCHEMA


# ---------------------------------------------------------------------------
# Real composition
# ---------------------------------------------------------------------------
def test_projection_composes_floor_room_desk_agent_task_and_approval(env):
    cfg, store = env
    seeded = _seed(store)
    tenant = seeded["tenant"]
    code, body = _call(cfg, {"tenant_id": tenant.tenant_id, "kind": "agent"})
    assert code == 200
    projection = body["projection"]
    agent = next(e for e in projection["entries"] if e["kind"] == "agent")
    assert agent["agent_id"] == seeded["agent"].agent_id
    assert agent["title"] == "Ada"
    assert agent["floor_id"] == "floor-001"
    assert agent["room_id"] == "floor-001-main"
    assert agent["link"]["path"].startswith("/qubit-office?floor=floor-001&")
    code, task_body = _call(cfg, {"tenant_id": tenant.tenant_id, "kind": "task"})
    assert code == 200
    projection = task_body["projection"]
    task = next(e for e in projection["entries"] if e["kind"] == "task")
    assert task["task_id"] == seeded["task"].task_id
    assert task["agent_id"] == seeded["agent"].agent_id
    code, approval_body = _call(
        cfg, {"tenant_id": tenant.tenant_id, "kind": "approval"})
    assert code == 200
    projection = approval_body["projection"]
    approval = next(e for e in projection["entries"] if e["kind"] == "approval")
    assert approval["approval_id"] == seeded["approval"].approval_id
    assert approval["attention"] is True
    assert approval["status"] == "pending"


def test_counts_are_derived_from_rows(env):
    cfg, store = env
    seeded = _seed(store)
    code, body = _call(cfg, {"tenant_id": seeded["tenant"].tenant_id})
    assert code == 200
    counts = body["projection"]["counts"]
    assert counts["floors"] == 100
    assert counts["rooms"] == 300
    assert counts["desks"] > 0
    assert counts["agents"] == 1
    assert counts["tasks"] == 1
    assert counts["sessions"] == 1
    assert counts["approvals"] == 1
    assert counts["approvals_pending"] == 1
    assert counts["entries"] == counts["floors"] + counts["rooms"] + counts["desks"] + 3
    roster = body["projection"]["roster"]
    assert roster["total"] == 1
    assert roster["active"] == 1
    assert roster["placed"] == 1
    assert body["projection"]["inbox"]["counts"]["approvals_pending"] == 1
    assert body["projection"]["sessions"]["total"] == 1


def test_no_records_is_a_named_empty_query_not_a_fabricated_row(env):
    cfg, store = env
    seeded = _seed(store)
    code, body = _call(cfg, {
        "tenant_id": seeded["tenant"].tenant_id, "q": "no-such-record"})
    assert code == 200
    projection = body["projection"]
    assert projection["state"] == "empty"
    assert projection["entries"] == []
    assert projection["matched"] == 0
    # The roll-ups still report real rows; an empty search does not zero them.
    assert projection["counts"]["agents"] == 1
    assert projection["roster"]["total"] == 1


# ---------------------------------------------------------------------------
# Search and filters
# ---------------------------------------------------------------------------
def test_search_matches_agent_room_and_task_text(env):
    cfg, store = env
    seeded = _seed(store)
    tid = seeded["tenant"].tenant_id
    for query, expected_kind in (
        ("ada", "agent"),
        ("floor-001-main", "room"),
        ("ship the report", "task"),
        ("publish", "approval"),
    ):
        code, body = _call(cfg, {"tenant_id": tid, "q": query})
        assert code == 200
        assert body["projection"]["matched"] >= 1
        kinds = {entry["kind"] for entry in body["projection"]["entries"]}
        assert expected_kind in kinds, (query, kinds)


def test_kind_and_status_filters_are_applied(env):
    cfg, store = env
    seeded = _seed(store)
    tid = seeded["tenant"].tenant_id
    code, body = _call(cfg, {"tenant_id": tid, "kind": "agent"})
    assert code == 200
    assert {entry["kind"] for entry in body["projection"]["entries"]} == {"agent"}
    code, body = _call(cfg, {"tenant_id": tid, "status": "pending"})
    assert code == 200
    assert {entry["status"] for entry in body["projection"]["entries"]} == {"pending"}
    # Unknown kind is a typed input refusal, not an empty success.
    code, body = __import__("voicebridge.qubit_compat", fromlist=["dispatch"]).dispatch(
        cfg, "GET", "/api/office/business/command-center",
        query={"tenant_id": tid, "kind": "planet"})
    assert code == 400
    assert body["code"] == "BUSINESS_BAD_INPUT"


def test_floor_room_agent_and_task_filters(env):
    cfg, store = env
    seeded = _seed(store)
    tid = seeded["tenant"].tenant_id
    for key, value, expected in (
        ("floor_id", "floor-001", {"floor", "room", "desk", "agent"}),
        ("room_id", "floor-001-main", {"room", "desk", "agent"}),
        ("agent_id", seeded["agent"].agent_id, {"agent", "task", "approval"}),
        ("task_id", seeded["task"].task_id, {"task"}),
        ("approval_id", seeded["approval"].approval_id, {"approval"}),
    ):
        code, body = _call(cfg, {"tenant_id": tid, key: value})
        assert code == 200, (key, body)
        kinds = {entry["kind"] for entry in body["projection"]["entries"]}
        assert kinds <= expected, (key, kinds)
        assert kinds, key


def test_limit_bounds_results_and_reports_truncation(env):
    cfg, store = env
    seeded = _seed(store)
    tid = seeded["tenant"].tenant_id
    code, body = _call(cfg, {"tenant_id": tid, "limit": "3"})
    assert code == 200
    projection = body["projection"]
    assert len(projection["entries"]) == 3
    assert projection["truncated"] is True
    assert projection["matched"] > 3
    code, body = _call(cfg, {"tenant_id": tid, "limit": "0"})
    assert code == 400
    assert body["code"] == "BUSINESS_BAD_INPUT"


# ---------------------------------------------------------------------------
# Tenant isolation, read-only, unavailable sources
# ---------------------------------------------------------------------------
def test_tenant_isolation_hides_the_other_tenants_records(env):
    cfg, store = env
    first = _seed(store, name="Acme", slug="acme")
    second = _seed(store, name="Beta", slug="beta")
    code, body = _call(cfg, {"tenant_id": first["tenant"].tenant_id,
                             "q": "ada", "limit": "200"})
    assert code == 200
    projection = body["projection"]
    assert projection["counts"]["agents"] == 1
    assert projection["counts"]["tasks"] == 1
    assert projection["counts"]["approvals"] == 1
    assert {entry["agent_id"] for entry in projection["entries"]
            if entry["kind"] == "agent"} == {first["agent"].agent_id}
    # A cross-tenant task/approval id is simply not found in this tenant.
    code, body = _call(cfg, {"tenant_id": first["tenant"].tenant_id,
                             "task_id": second["task"].task_id})
    assert code == 200
    assert body["projection"]["matched"] == 0


def test_unknown_tenant_is_refused(env):
    cfg, store = env
    _seed(store)
    with pytest.raises(BusinessError) as excinfo:
        command_center(store, "ten_missing")
    assert excinfo.value.code == "BUSINESS_NOT_FOUND"


def test_projection_is_read_only(env):
    cfg, store = env
    seeded = _seed(store)
    with store.read() as conn:
        before = int(conn.execute(
            "SELECT COUNT(*) FROM bos_audit_event").fetchone()[0])
    code, _ = _call(cfg, {"tenant_id": seeded["tenant"].tenant_id, "q": "ada"})
    assert code == 200
    with store.read() as conn:
        after = int(conn.execute(
            "SELECT COUNT(*) FROM bos_audit_event").fetchone()[0])
    assert after == before, "a read projection must not write an audit row"


def test_unavailable_world_source_is_typed_not_invented(env):
    cfg, store = env
    seeded = _seed(store)

    class BrokenRegistry:
        @property
        def floors(self):
            raise RuntimeError("tower templates are unavailable")

    projection = command_center(
        store, seeded["tenant"].tenant_id, registry=BrokenRegistry())
    assert projection["sources"]["world"]["state"] == "unavailable"
    assert projection["sources"]["world"]["code"] == "RuntimeError"
    assert "unavailable" in projection["sources"]["world"]["reason"]
    assert projection["counts"]["floors"] == 0
    assert projection["links"]["floors"] == []
    # The other real sources still project.
    assert projection["counts"]["agents"] == 1
    assert projection["sources"]["roster"]["state"] == "ok"


def test_sqlite_error_in_one_source_is_reported(env, monkeypatch):
    cfg, store = env
    seeded = _seed(store)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is busy")

    monkeypatch.setattr(store, "list_tasks", boom)
    projection = command_center(store, seeded["tenant"].tenant_id)
    assert projection["sources"]["tasks"]["state"] == "unavailable"
    assert projection["sources"]["tasks"]["code"] == "OperationalError"
    assert projection["counts"]["tasks"] == 0
    assert projection["sources"]["roster"]["state"] == "ok"


def test_links_name_the_contract_route_template(env):
    cfg, store = env
    seeded = _seed(store)
    code, body = _call(cfg, {"tenant_id": seeded["tenant"].tenant_id})
    assert code == 200
    links = body["projection"]["links"]
    assert links["route_template"] == (
        "/qubit-office?floor={floor_id}&room={room_id}&agent={agent_id}")
    assert len(links["floors"]) == 100
    assert links["floors"][0]["path"].startswith("/qubit-office?floor=floor-")
