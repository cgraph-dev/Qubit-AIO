"""Office-seat -> Business OS agent bootstrap.

The Office Agents panel opens a control drawer for a clicked seat. Before this
lane a seat had no Business OS record, so the drawer honestly said "no tenant".
These tests pin the narrow, idempotent, safe bootstrap that links the office's
own 283-seat roster to durable control records:

* the Personal tenant/business is created once and reused;
* the first bootstrap creates 283 agents, a repeat creates zero;
* every created record uses the safe defaults (deny-all permissions, L0,
  zero spend, approvals, provider ``qubit``/model ``active``);
* a seat resolves through ``metadata.office_seat_slug`` (and the exact desk);
* an existing record is never modified;
* no model turn, no spend, no ledger/tool usage, no network;
* the route is owner-gated and the dashboard token gate is enforced.

No network, no credentials, no live service, no real business data.
"""
from __future__ import annotations

import pytest

from voicebridge import qubit_compat as HC
from voicebridge import webui as W
from voicebridge.business import AgentControlService, BusinessStore
from voicebridge.business import office_seats as SEATS
from voicebridge.office import agency_org

pytestmark = pytest.mark.unit


class _Cfg:
    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return self._state


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _Cfg(tmp_path)
    return cfg, BusinessStore.from_config(cfg)


def _bootstrap(cfg, body=None):
    return HC.dispatch(
        cfg, "POST", "/api/office/business/bootstrap",
        body=body if body is not None else {"owner": True},
    )


def _agents(store, tenant_id, business_id):
    return AgentControlService(store).list(tenant_id, business_id=business_id)


# ---------------------------------------------------------------------------
# Routes / auth
# ---------------------------------------------------------------------------
def test_bootstrap_route_is_registered_in_the_business_family():
    route, _params = HC.match(
        "POST", "/api/office/business/bootstrap")
    assert route is not None
    assert route["handler"] == "h_business_bootstrap"


def test_bootstrap_requires_owner_intent(env):
    cfg, _store = env
    status, body = _bootstrap(cfg, body={})
    assert status == 403
    assert body["code"] == "BUSINESS_OWNER_ONLY"


def test_bootstrap_is_behind_the_dashboard_token_gate():
    handler = object.__new__(W.WebUIHandler)
    handler.path = "/agent/api/office/business/bootstrap"
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
# Personal scope
# ---------------------------------------------------------------------------
def test_personal_scope_is_created_once_then_reused(env):
    _cfg, store = env
    first = SEATS.ensure_personal_scope(store)
    assert first.tenant_id == "ten_personal"
    assert first.created_tenant is True
    assert first.created_business is True
    tenant = store.get_tenant(first.tenant_id)
    business = store.get_business(first.tenant_id, first.business_id)
    assert tenant.name == "Personal"
    assert business.name == "Personal"

    second = SEATS.ensure_personal_scope(store)
    assert second.tenant_id == first.tenant_id
    assert second.business_id == first.business_id
    assert second.created_tenant is False
    assert second.created_business is False
    assert len(store.list_tenants()) == 1
    assert len(store.list_businesses(first.tenant_id)) == 1


def test_personal_scope_reuses_a_preexisting_personal_tenant(env):
    _cfg, store = env
    tenant = store.create_tenant("Personal", tenant_id="ten_existing")
    business = store.create_business(tenant.tenant_id, "My Studio", slug="studio")
    scope = SEATS.ensure_personal_scope(store)
    assert scope.tenant_id == "ten_existing"
    assert scope.business_id == business.business_id
    assert scope.created_tenant is False
    assert scope.created_business is False
    assert store.list_tenants() == [store.get_tenant("ten_existing")]


# ---------------------------------------------------------------------------
# Roster import
# ---------------------------------------------------------------------------
def test_first_bootstrap_creates_all_283_agents(env):
    cfg, store = env
    status, body = _bootstrap(cfg)
    assert status == 200, body
    assert body["ok"] is True
    assert body["tenant_id"] == "ten_personal"
    assert body["plan_ok"] is True
    assert body["total"] == 283
    assert body["created"] == 283
    assert body["existing"] == 0
    assert body["created_tenant"] is True
    assert body["created_business"] is True

    seats = agency_org.plan_building()["seats"]
    assert len(seats) == 283
    agents = _agents(store, body["tenant_id"], body["business_id"])
    assert len(agents) == 283
    assert {a.metadata["office_seat_slug"] for a in agents} == {
        seat["slug"] for seat in seats}


def test_repeat_bootstrap_is_idempotent(env):
    cfg, store = env
    _status, first = _bootstrap(cfg)
    assert first["created"] == 283

    status, second = _bootstrap(cfg)
    assert status == 200
    assert second["created"] == 0
    assert second["existing"] == 283
    assert second["total"] == 283
    assert second["created_tenant"] is False
    assert second["created_business"] is False
    agents = _agents(store, first["tenant_id"], first["business_id"])
    assert len(agents) == 283


def test_every_created_agent_uses_the_safe_defaults(env):
    cfg, store = env
    _status, body = _bootstrap(cfg)
    agents = _agents(store, body["tenant_id"], body["business_id"])
    assert len(agents) == 283
    for agent in agents:
        assert agent.provider == "qubit"
        assert agent.model == "active"
        assert agent.reasoning_effort == "medium"
        assert agent.permissions.is_empty(), agent.display_name
        assert agent.safety.autonomy == "L0"
        assert agent.safety.daily_spend_minor == 0
        assert agent.safety.monthly_spend_minor == 0
        assert agent.safety.per_action_limit_minor == 0
        assert set(agent.safety.approval_required) == {
            "spend", "legal", "publish", "contact", "irreversible"}
        assert agent.status == "active"
        assert agent.human_owner == "owner"
        assert agent.role_id.startswith("office:")
        assert agent.department
        meta = agent.metadata
        assert meta["source"] == "office_plan_building"
        assert meta["office_seat_slug"] == meta["seat_slug"]
        assert meta["seat_type"]
        assert "division" in meta and "supervisor" in meta
        assert "floor" in meta and "persona_color" in meta
        assert "persona_emoji" in meta


def test_seats_match_through_metadata_and_the_exact_desk(env):
    cfg, store = env
    _status, body = _bootstrap(cfg)
    plan = agency_org.plan_building()
    agents = _agents(store, body["tenant_id"], body["business_id"])
    by_metadata = {a.metadata["office_seat_slug"]: a for a in agents}
    by_desk = {a.desk: a for a in agents}
    for seat in plan["seats"]:
        linked = by_metadata[seat["slug"]]
        assert linked.desk == seat["slug"]
        assert by_desk[seat["slug"]].agent_id == linked.agent_id
        assert linked.room == (seat["room"] or None)


def test_an_existing_control_record_is_never_modified(env):
    cfg, store = env
    plan = agency_org.plan_building()
    first_seat = plan["seats"][0]
    scope = SEATS.ensure_personal_scope(store)
    service = AgentControlService(store)
    existing, created = service.create(
        scope.tenant_id,
        scope.business_id,
        role_id="custom:role",
        department="Custom",
        display_name="Owner Custom",
        human_owner="owner",
        provider="custom-provider",
        model="custom-model",
        idempotency_key="pre-existing",
        permissions={"data": ["read"]},
        safety={"autonomy": "L3", "daily_spend_minor": 500},
        metadata={"office_seat_slug": first_seat["slug"]},
    )
    assert created is True

    status, body = _bootstrap(cfg)
    assert status == 200
    assert body["created"] == 282
    assert body["existing"] == 1
    same = service.get(scope.tenant_id, existing.agent_id)
    assert same.model == "custom-model"
    assert same.provider == "custom-provider"
    assert same.revision == existing.revision
    assert same.safety.autonomy == "L3"
    assert same.safety.daily_spend_minor == 500
    assert same.permissions.to_dict()["data"] == ["read"]


def test_bootstrap_does_not_spend_or_invoke_tools(env):
    cfg, store = env
    _status, body = _bootstrap(cfg)
    with store.read() as conn:
        for table in (
            "bos_agent_usage",
            "bos_agent_operation",
            "bos_tool_invocation",
            "bos_ledger_entry",
            "bos_approval",
        ):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, f"{table} must stay empty ({body})"


def test_bootstrap_is_a_pure_read_of_the_office_plan(env):
    cfg, _store = env
    before = agency_org.plan_building()
    _bootstrap(cfg)
    after = agency_org.plan_building()
    assert after["headcount"] == before["headcount"] == 283
    assert [s["slug"] for s in after["seats"]] == [
        s["slug"] for s in before["seats"]]
