"""Business OS HTTP bridge: owner-scoped, dry-run, typed and durable.

The bridge is the Office UI's only door into the Wave-2 Business OS. These
tests use a throwaway ``business.db`` under a temporary state dir and pin the
contracts the UI depends on:

* reads report real rows (empty is a named empty state, never invented data);
* every mutation needs explicit owner intent and never a fabricated success;
* War Room intake/sources/evidence/decision/tasks follow the service rules;
* agent controls list/get/update/pause/resume/stop/revoke/archive and audit;
* a missing/incorrect session token is an auth failure at the dashboard gate;
* no route publishes, spends, contacts, or calls an external platform.

No network, no credentials, no live service, no real business data.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge import qubit_compat as HC
from voicebridge import webui as W
from voicebridge.business import AgentControlService, BusinessStore, RoleRegistry

pytestmark = pytest.mark.unit


class _Cfg:
    """Minimal Config stand-in: the bridge only reads ``state_dir()``."""

    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return self._state


def _count(store: BusinessStore, table: str) -> int:
    conn = sqlite3.connect(store.path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _Cfg(tmp_path)
    store = BusinessStore.from_config(cfg)
    return cfg, store


def _scope(store):
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    return tenant, business


def _call(cfg, method, path, *, body=None, query=None):
    return HC.dispatch(cfg, method, path, query=query or {}, body=body or {})


def _agent(store, tenant, business, **overrides):
    args = dict(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.social",
        department="Marketing",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key="create-1",
        permissions={"social": ["read", "draft"], "files": ["/tmp/workspace"]},
        safety={
            "autonomy": "L2",
            "daily_spend_minor": 1000,
            "monthly_spend_minor": 5000,
            "per_action_limit_minor": 400,
        },
    )
    args.update(overrides)
    return AgentControlService(store).create(**args)[0]


# ---------------------------------------------------------------------------
# Routes and auth
# ---------------------------------------------------------------------------
def test_routes_are_registered_under_the_office_family():
    for method, path in (
        ("GET", "/api/office/business/overview"),
        ("GET", "/api/office/business/directory"),
        ("GET", "/api/office/business/roles"),
        ("GET", "/api/office/business/org/floors"),
        ("GET", "/api/office/world/tower"),
        ("GET", "/api/office/business/warroom/sessions"),
        ("POST", "/api/office/business/warroom/sessions"),
        ("POST", "/api/office/business/warroom/etsy-pilot"),
        ("GET", "/api/office/business/agents"),
        ("POST", "/api/office/business/agents/agt_1/pause"),
    ):
        route, _params = HC.match(method, path)
        assert route is not None, f"{method} {path} has no compat route"


def test_business_reads_are_a_qubit_read_authority():
    assert W.qubit_read_authority("/api/office/business/overview")
    assert W.qubit_read_authority("/api/office/business/warroom/sessions")
    assert W.qubit_read_authority("/api/office/world/tower")


def test_a_tokenless_non_loopback_request_fails_the_dashboard_gate():
    handler = object.__new__(W.WebUIHandler)
    handler.path = "/agent/api/office/business/overview"
    handler.client_address = ("203.0.113.9", 1234)
    handler.headers = {}
    handler.server = type("S", (), {
        "app": type("A", (), {"api_token": "secret"})(),
    })()
    handler._api_token_ok = lambda: False
    assert handler._qubit_token_ok() is False

    handler.headers = {"X-Qubit-Session-Token": "wrong"}
    assert handler._qubit_token_ok() is False

    handler.headers = {"X-Qubit-Session-Token": "secret"}
    assert handler._qubit_token_ok() is True


# ---------------------------------------------------------------------------
# Overview / directory
# ---------------------------------------------------------------------------
def test_overview_is_empty_not_invented(env):
    cfg, _store = env
    code, out = _call(cfg, "GET", "/api/office/business/overview")
    assert code == 200
    assert out["ok"] is True
    assert out["tenants"] == []
    assert out["default_tenant_id"] is None


def test_directory_reports_the_real_template_catalog(env):
    cfg, _store = env
    code, out = _call(cfg, "GET", "/api/office/business/directory")
    assert code == 200
    counts = out["counts"]
    expected_roles = len(RoleRegistry.default().roles)
    assert expected_roles >= 145
    assert counts["roles"] == expected_roles
    assert counts["floors"] == 5
    assert counts["active_floors"] + counts["dormant_floors"] == 5
    assert len(out["roles"]) == expected_roles
    assert all("autonomy" in role and "budget" in role for role in out["roles"])
    assert all("wing" in floor for floor in out["floors"])


def test_world_tower_is_a_five_floor_unstaffed_room_scene(env):
    cfg, _store = env
    code, out = _call(cfg, "GET", "/api/office/world/tower")
    assert code == 200
    assert out["ok"] is True
    assert out["schema"] == "qubit-office-world-tower/1"
    assert out["source"] == "templates"
    assert out["staffed"] is False
    counts = out["counts"]
    assert counts["floors"] == 5
    assert counts["wings"] == 5
    assert counts["active_floors"] + counts["dormant_floors"] == 5
    assert len(out["wings"]) == 5
    assert len(out["floors"]) == 5
    # No fake agents: every rendered seat is empty and no agent is counted.
    assert counts["agents"] == 0
    assert counts["desks"] == sum(floor["capacity"]["desks"]
                                 for floor in out["floors"])
    for floor in out["floors"]:
        assert floor["active"] is floor["active_by_default"]
        assert floor["capacity"]["rooms"] == len(floor["rooms"])
        for room in floor["rooms"]:
            assert room["template"] is True
            assert room["state"] == "template"
            assert room["active"] is floor["active_by_default"]
            assert room["cell_count"] == len(room["cells"])
            desks = 0
            for cell in room["cells"]:
                assert (cell["cols"], cell["rows"]) == (16, 12)
                assert len(cell["desk_slots"]) <= 6
                for slot in cell["desk_slots"]:
                    assert slot["occupied_by"] is None
                    assert 0 <= slot["tile"]["x"] < 16
                    assert 0 <= slot["tile"]["y"] < 12
                    desks += 1
            assert desks == room["capacity"]


def test_roles_and_floors_can_be_filtered(env):
    cfg, _store = env
    code, out = _call(cfg, "GET", "/api/office/business/roles",
                      query={"department": "revenue"})
    assert code == 200
    assert out["roles"]
    assert {role["department"] for role in out["roles"]} == {"revenue"}
    code, out = _call(cfg, "GET", "/api/office/business/org/floors",
                      query={"wing": "north"})
    assert code == 200
    assert {floor["wing"] for floor in out["floors"]} == {"north"}


# ---------------------------------------------------------------------------
# War Room
# ---------------------------------------------------------------------------
def test_warroom_intake_is_owner_only_and_idempotent(env):
    cfg, _store = env
    body = {"title": "Printable planner", "owner": True,
            "idempotency_key": "intake-1"}
    code, out = _call(cfg, "POST", "/api/office/business/warroom/sessions",
                      body={k: v for k, v in body.items() if k != "owner"})
    assert code == 403
    assert out["code"] == "BUSINESS_OWNER_ONLY"

    code, first = _call(cfg, "POST", "/api/office/business/warroom/sessions",
                        body=body)
    assert code == 200 and first["created"] is True
    session_id = first["session"]["session_id"]
    code, again = _call(cfg, "POST", "/api/office/business/warroom/sessions",
                        body=body)
    assert code == 200 and again["created"] is False
    assert again["session"]["session_id"] == session_id

    code, listed = _call(cfg, "GET", "/api/office/business/warroom/sessions")
    assert code == 200
    assert [s["session_id"] for s in listed["sessions"]] == [session_id]


def test_warroom_bundle_source_and_evidence_rules(env):
    cfg, _store = env
    _code, first = _call(
        cfg, "POST", "/api/office/business/warroom/sessions",
        body={"title": "Idea", "owner": True, "idempotency_key": "i-1"})
    sid = first["session"]["session_id"]

    base = f"/api/office/business/warroom/sessions/{sid}"
    # Missing owner on a mutation is a typed refusal.
    code, out = _call(cfg, "POST", f"{base}/sources",
                      body={"kind": "note", "locator": "owner words"})
    assert code == 403 and out["code"] == "BUSINESS_OWNER_ONLY"

    code, source = _call(
        cfg, "POST", f"{base}/sources",
        body={"kind": "note", "locator": "owner words", "owner": True})
    assert code == 200 and source["created"] is True
    source_id = source["source"]["source_id"]

    # A FACT cannot cite an unextracted source: no fabricated research.
    code, out = _call(
        cfg, "POST", f"{base}/evidence",
        body={"label": "FACT", "claim": "demand is high", "owner": True,
              "source_id": source_id})
    assert code == 400 and out["code"] == "BUSINESS_WARROOM_UNVERIFIED_FACT"

    code, assumption = _call(
        cfg, "POST", f"{base}/evidence",
        body={"label": "ASSUMPTION", "claim": "buyers want this",
              "owner": True})
    assert code == 200 and assumption["evidence"]["label"] == "ASSUMPTION"

    code, bundle = _call(cfg, "GET", base)
    assert code == 200
    assert bundle["session"]["state"] == "intake"
    assert [s["source_id"] for s in bundle["sources"]] == [source_id]
    assert bundle["evidence"][0]["label"] == "ASSUMPTION"


def test_warroom_decision_and_tasks_are_owner_only(env):
    cfg, store = env
    tenant, business = _scope(store)
    # Build an approved-ready session through the real service, then exercise
    # the decision/task doors through the HTTP bridge.
    from voicebridge.business import warroom
    session, _ = warroom.create_session(
        store, tenant_id=tenant.tenant_id,
        business_id=business.business_id, title="Idea",
        idempotency_key="wr-1")
    warroom.add_source(store, tenant_id=tenant.tenant_id,
                       session_id=session.session_id, kind="note",
                       locator="notes")
    warroom.advance(store, tenant_id=tenant.tenant_id,
                    session_id=session.session_id, target="research")
    warroom.advance(store, tenant_id=tenant.tenant_id,
                    session_id=session.session_id, target="debate")
    warroom.synthesize(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id,
        brief={
            "idea": "planner", "niche": "printables",
            "icp": "shoppers", "offer": "PDF", "channels": ["etsy"],
            "content_plan": ["pins"], "lead_gen_plan": ["email"],
            "website_app_scope": "landing", "pricing": {"price_minor": 600},
            "budget": {"monthly_minor": 2000}, "risks": ["IP"],
            "plan_30_60_90": {"30": "ship"}, "tasks": ["draft layouts"],
        },
        score={"demand": 8, "effort": 4, "cost": 3, "risk": 3, "fit": 7})
    warroom.advance(store, tenant_id=tenant.tenant_id,
                    session_id=session.session_id, target="owner_decision")

    base = f"/api/office/business/warroom/sessions/{session.session_id}"
    code, out = _call(cfg, "POST", f"{base}/decisions",
                      body={"decision": "approved"})
    assert code == 403 and out["code"] == "BUSINESS_OWNER_ONLY"

    code, out = _call(cfg, "POST", f"{base}/decisions",
                      body={"decision": "approved", "owner": True,
                            "note": "ship it"})
    assert code == 200 and out["decision"]["status"] == "approved"

    code, out = _call(cfg, "POST", f"{base}/tasks", body={"owner": True})
    assert code == 200
    assert [task["title"] for task in out["tasks"]] == ["draft layouts"]

    code, out = _call(cfg, "POST", f"{base}/tasks", body={"owner": False})
    assert code == 403 and out["code"] == "BUSINESS_OWNER_ONLY"


# ---------------------------------------------------------------------------
# Agent controls
# ---------------------------------------------------------------------------
def test_agent_list_get_and_audit(env):
    cfg, store = env
    tenant, business = _scope(store)
    agent = _agent(store, tenant, business)
    code, out = _call(cfg, "GET", "/api/office/business/agents",
                      query={"tenant_id": tenant.tenant_id})
    assert code == 200
    assert [a["agent_id"] for a in out["agents"]] == [agent.agent_id]
    payload = out["agents"][0]
    assert payload["provider"] == "provider-a"
    assert payload["safety"]["autonomy"] == "L2"
    assert payload["permissions"]["social"] == ["draft", "read"]

    code, out = _call(cfg, "GET",
                      f"/api/office/business/agents/{agent.agent_id}",
                      query={"tenant_id": tenant.tenant_id})
    assert code == 200
    assert out["agent"]["model"] == "model-a"
    assert any(event["action"] == "agent.create" for event in out["audit"])

    code, out = _call(cfg, "GET",
                      f"/api/office/business/agents/{agent.agent_id}/audit",
                      query={"tenant_id": tenant.tenant_id})
    assert code == 200 and out["audit"]


def test_agent_lifecycle_mutations_require_owner(env):
    cfg, store = env
    tenant, business = _scope(store)
    agent = _agent(store, tenant, business)
    base = f"/api/office/business/agents/{agent.agent_id}"

    code, out = _call(cfg, "POST", f"{base}/pause",
                      body={"tenant_id": tenant.tenant_id})
    assert code == 403 and out["code"] == "BUSINESS_OWNER_ONLY"

    code, out = _call(cfg, "POST", f"{base}/pause",
                      body={"tenant_id": tenant.tenant_id, "owner": True,
                            "idempotency_key": "pause-1"})
    assert code == 200 and out["result"]["status"] == "paused"

    # Replaying the same key/body returns the same recorded effect.
    code, out = _call(cfg, "POST", f"{base}/pause",
                      body={"tenant_id": tenant.tenant_id, "owner": True,
                            "idempotency_key": "pause-1"})
    assert code == 200 and out["result"]["status"] == "paused"

    code, out = _call(cfg, "POST", f"{base}/resume",
                      body={"tenant_id": tenant.tenant_id, "owner": True})
    assert code == 200 and out["result"]["status"] == "active"

    code, out = _call(cfg, "POST", f"{base}/update",
                      body={"tenant_id": tenant.tenant_id, "owner": True,
                            "changes": {"model": "model-b",
                                        "reasoning_effort": "high"}})
    assert code == 200
    assert out["agent"]["model"] == "model-b"
    assert out["agent"]["reasoning_effort"] == "high"

    code, out = _call(cfg, "POST", f"{base}/revoke",
                      body={"tenant_id": tenant.tenant_id, "owner": True,
                            "scopes": ["social"]})
    assert code == 200
    assert out["result"]["permissions"]["social"] == []
    assert out["result"]["permissions"]["files"] == ["/tmp/workspace"]


def test_agent_stop_is_honest_about_an_active_turn(env):
    cfg, store = env
    tenant, business = _scope(store)
    agent = _agent(store, tenant, business)
    base = f"/api/office/business/agents/{agent.agent_id}"

    # No active turn: typed refusal, never a fabricated "stopped".
    code, out = _call(cfg, "POST", f"{base}/stop",
                      body={"tenant_id": tenant.tenant_id, "owner": True})
    assert code == 409 and out["code"] == "AGENT_NO_ACTIVE_TURN"

    service = AgentControlService(store)
    service.begin_turn(tenant.tenant_id, agent.agent_id,
                       idempotency_key="turn-1")
    code, out = _call(cfg, "POST", f"{base}/stop",
                      body={"tenant_id": tenant.tenant_id, "owner": True,
                            "idempotency_key": "stop-1"})
    assert code == 200
    assert out["result"]["status"] == "interrupted"

    code, out = _call(cfg, "POST", f"{base}/archive",
                      body={"tenant_id": tenant.tenant_id, "owner": True})
    assert code == 200 and out["result"]["status"] == "archived"


def test_unknown_agent_and_multi_tenant_reads_are_typed(env):
    cfg, store = env
    tenant, _business = _scope(store)
    code, out = _call(cfg, "GET", "/api/office/business/agents/agt_missing",
                      query={"tenant_id": tenant.tenant_id})
    assert code == 404 and out["code"] == "AGENT_NOT_FOUND"

    store.create_tenant("Second")
    code, out = _call(cfg, "GET", "/api/office/business/agents")
    assert code == 400 and out["code"] == "BUSINESS_TENANT_REQUIRED"


# ---------------------------------------------------------------------------
# Etsy digital-shop pilot
# ---------------------------------------------------------------------------
def test_etsy_pilot_route_is_registered():
    route, _params = HC.match(
        "POST", "/api/office/business/warroom/etsy-pilot")
    assert route is not None
    assert route["handler"] == "h_business_warroom_etsy_pilot"


def test_etsy_pilot_is_owner_gated_and_idempotent(env):
    cfg, _store = env
    path = "/api/office/business/warroom/etsy-pilot"

    code, out = _call(cfg, "POST", path, body={})
    assert code == 403 and out["code"] == "BUSINESS_OWNER_ONLY"
    assert _call(cfg, "GET", "/api/office/business/warroom/sessions")[1].get(
        "sessions") in (None, [])

    code, first = _call(cfg, "POST", path, body={"owner": True})
    assert code == 200, first
    assert first["created"] is True
    assert first["session"]["title"] == "Etsy digital shop pilot"
    assert first["session"]["state"] == "owner_decision"
    assert first["source_ids"]
    assert first["experiment_names"]
    sid = first["session"]["session_id"]

    code, again = _call(cfg, "POST", path, body={"owner": True})
    assert code == 200, again
    assert again["created"] is False
    assert again["session"]["session_id"] == sid

    code, listed = _call(cfg, "GET", "/api/office/business/warroom/sessions")
    assert code == 200
    assert [item["session_id"] for item in listed["sessions"]] == [sid]


def test_etsy_pilot_bundle_has_no_decision_or_tasks(env):
    cfg, store = env
    path = "/api/office/business/warroom/etsy-pilot"
    _code, out = _call(cfg, "POST", path, body={"owner": True})
    sid = out["session"]["session_id"]

    code, bundle = _call(
        cfg, "GET", f"/api/office/business/warroom/sessions/{sid}")
    assert code == 200
    assert bundle["decisions"] == []
    assert bundle["tasks"] == []
    assert bundle["session"]["state"] == "owner_decision"
    labels = {item["label"] for item in bundle["evidence"]}
    assert labels == {"FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN"}
    fact = next(item for item in bundle["evidence"] if item["label"] == "FACT")
    assert fact["source_id"]

    for table in ("bos_task", "bos_approval", "bos_warroom_decision"):
        assert _count(store, table) == 0


def test_etsy_pilot_real_provider_is_typed_and_inert(env):
    cfg, store = env
    code, out = _call(
        cfg, "POST", "/api/office/business/warroom/etsy-pilot",
        body={"owner": True, "provider": "remote"})
    assert code == 400
    assert out["code"] == "BUSINESS_RESEARCH_NOT_CONFIGURED"
    assert _count(store, "bos_warroom_session") == 0
