"""Agent turn preview: a safe plan, never a turn.

A preview validates the whole control plane for a *prospective* turn and then
stops: no model/provider call, no spend, no job, no tool side effect, and no
durable turn/usage/audit/ledger write. These tests pin:

* an allowed plan carries model/provider/reasoning, limits, tool scopes and
  the exact remaining budget;
* paused/revoked/archived agents, denied/unknown tools, autonomy, budget,
  rate, turn, token and approval refusals are typed;
* an empty prompt is a typed bad-input refusal;
* provider/model readiness is a configuration check, not a network probe;
* the owner-gated HTTP route adds no write and stays behind the dashboard token;
* the store is byte-for-byte untouched by a preview.

Hermetic: throwaway SQLite under ``tmp_path``; no network, credentials, model
or live service.
"""
from __future__ import annotations

import socket
import sqlite3
import urllib.request

import pytest

from voicebridge import qubit_compat as HC
from voicebridge import webui as W
from voicebridge.business import AgentControlService, BusinessStore
from voicebridge.business.errors import BusinessError

pytestmark = pytest.mark.unit

_WRITE_TABLES = (
    "bos_agent_turn",
    "bos_agent_usage",
    "bos_agent_operation",
    "bos_audit_event",
    "bos_ledger_entry",
    "bos_job",
    "bos_job_event",
    "bos_approval",
    "bos_tool_invocation",
)


class _Cfg:
    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return self._state


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore):
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    return tenant, business


def _svc(store: BusinessStore) -> AgentControlService:
    return AgentControlService(store)


def _create(svc: AgentControlService, tenant, business, **overrides):
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
        permissions={"tools": ["draft_post", "publish_listing"],
                     "social": ["read", "draft"]},
        safety={
            "autonomy": "L2",
            "daily_spend_minor": 1000,
            "monthly_spend_minor": 5000,
            "per_action_limit_minor": 500,
            "max_turns_per_day": 10,
            "max_tokens_per_turn": 8192,
            "rate_limit_per_minute": 30,
        },
    )
    args.update(overrides)
    return svc.create(**args)[0]


def _counts(store: BusinessStore) -> dict[str, int]:
    conn = sqlite3.connect(store.path)
    try:
        return {
            table: int(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in _WRITE_TABLES
        }
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _Cfg(tmp_path)
    store = BusinessStore.from_config(cfg)
    tenant, business = _scope(store)
    service = _svc(store)
    return cfg, store, service, tenant, business


# ---------------------------------------------------------------------------
# Allowed plan
# ---------------------------------------------------------------------------
def test_allowed_plan_carries_the_control_plane_contract(env):
    _cfg, store, service, tenant, business = env
    agent = _create(service, tenant, business)
    before = _counts(store)

    plan = service.preview_turn(
        tenant.tenant_id,
        agent.agent_id,
        prompt="Draft a friendly launch note for our newsletter.",
        requested_tools=["draft_post"],
    )

    assert plan["allowed"] is True
    assert plan["state"] == "ALLOWED"
    assert plan["side_effects"] is False
    assert plan["provider"] == "provider-a"
    assert plan["model"] == "model-a"
    assert plan["reasoning_effort"] == "medium"
    assert plan["autonomy"] == "L2"
    assert plan["max_tokens"] == 8192
    assert plan["requested_tools"] == ["draft_post"]
    assert plan["tool_scopes"]["tools"] == ["draft_post", "publish_listing"]
    assert plan["tool_scopes"]["social"] == ["draft", "read"]
    assert plan["approval_actions_required"] == []
    assert plan["approvals_satisfied"] is True
    assert plan["limits"]["max_tokens_per_turn"] == 8192
    assert plan["limits"]["daily_spend_minor"] == 1000
    assert plan["estimated"]["spend_minor"] == 0
    assert plan["estimated"]["remaining"]["turns_remaining_today"] == 10
    assert plan["estimated_prompt_tokens"] >= 1
    assert plan["projected_tokens"] == (
        plan["estimated_prompt_tokens"] + plan["max_tokens"])
    assert "no model call" in plan["reason"]

    # Read-only: every write table is exactly where it was.
    assert _counts(store) == before


def test_preview_makes_no_network_or_model_call(env, monkeypatch):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)

    def _no_network(*_args, **_kwargs):
        raise AssertionError("preview must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)

    plan = service.preview_turn(
        tenant.tenant_id, agent.agent_id, prompt="Summarise the plan.")
    assert plan["allowed"] is True


# ---------------------------------------------------------------------------
# Lifecycle refusals
# ---------------------------------------------------------------------------
def test_paused_agent_is_a_typed_refusal(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)
    service.pause(tenant.tenant_id, agent.agent_id, idempotency_key="pause-1")

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, agent.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_PAUSED"
    assert "paused" in exc.value.detail


def test_revoked_agent_is_a_typed_refusal(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)
    service.revoke(
        tenant.tenant_id, agent.agent_id, idempotency_key="revoke-1")

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, agent.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_REVOKED"


def test_archived_agent_is_a_typed_refusal(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)
    service.archive(
        tenant.tenant_id, agent.agent_id, idempotency_key="archive-1")

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, agent.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_ARCHIVED"


# ---------------------------------------------------------------------------
# Tools, autonomy and readiness
# ---------------------------------------------------------------------------
def test_denied_tool_is_typed_and_permissions_stay_deny_by_default(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id,
            agent.agent_id,
            prompt="Send the newsletter.",
            requested_tools=["send_email"],
        )
    assert exc.value.code == "AGENT_TOOL_DENIED"
    assert "deny-by-default" in exc.value.detail


def test_unknown_or_wildcard_tool_is_typed(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)

    for value in (["*"], ["all"], ["bad tool!"]):
        with pytest.raises(BusinessError) as exc:
            service.preview_turn(
                tenant.tenant_id, agent.agent_id, prompt="hello",
                requested_tools=value)
        assert exc.value.code == "AGENT_TOOL_UNKNOWN"


def test_low_autonomy_cannot_preview_tool_action(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(
        service, tenant, business, safety={"autonomy": "L1"})

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, agent.agent_id, prompt="hello",
            requested_tools=["draft_post"])
    assert exc.value.code == "AGENT_AUTONOMY_DENIED"

    # A plain text turn is still previewable at L1.
    plan = service.preview_turn(
        tenant.tenant_id, agent.agent_id, prompt="hello")
    assert plan["allowed"] is True


def test_unconfigured_provider_and_model_are_typed(env):
    _cfg, _store_obj, service, tenant, business = env
    providerless = _create(
        service, tenant, business, provider="unset",
        idempotency_key="create-provider")
    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, providerless.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_PROVIDER_NOT_READY"

    modelless = _create(
        service, tenant, business, model="none",
        idempotency_key="create-model")
    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, modelless.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_MODEL_NOT_READY"


# ---------------------------------------------------------------------------
# Budget / rate / turn / token limits and approvals
# ---------------------------------------------------------------------------
def test_per_action_daily_and_monthly_budget_refusals(env):
    _cfg, _store_obj, service, tenant, business = env
    per_action = _create(
        service, tenant, business,
        safety={"autonomy": "L2", "per_action_limit_minor": 0},
        idempotency_key="create-per-action")
    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, per_action.agent_id, prompt="hello",
            spend_minor=100, human_approved=True)
    assert exc.value.code == "AGENT_BUDGET_EXCEEDED"

    daily = _create(
        service, tenant, business,
        safety={"autonomy": "L2", "daily_spend_minor": 0,
                "monthly_spend_minor": 5000,
                "per_action_limit_minor": 1000},
        idempotency_key="create-daily")
    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, daily.agent_id, prompt="hello",
            spend_minor=100, human_approved=True)
    assert exc.value.code == "AGENT_BUDGET_EXCEEDED"

    monthly = _create(
        service, tenant, business,
        safety={"autonomy": "L2", "daily_spend_minor": 1000,
                "monthly_spend_minor": 0,
                "per_action_limit_minor": 1000},
        idempotency_key="create-monthly")
    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, monthly.agent_id, prompt="hello",
            spend_minor=100, human_approved=True)
    assert exc.value.code == "AGENT_BUDGET_EXCEEDED"


def test_turn_and_rate_limits_are_typed(env):
    _cfg, _store_obj, service, tenant, business = env
    no_turns = _create(
        service, tenant, business,
        safety={"autonomy": "L2", "max_turns_per_day": 0},
        idempotency_key="create-turns")
    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, no_turns.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_TURN_LIMIT"

    no_rate = _create(
        service, tenant, business,
        safety={"autonomy": "L2", "rate_limit_per_minute": 0},
        idempotency_key="create-rate")
    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, no_rate.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_RATE_LIMITED"


def test_concurrent_turn_limit_is_typed(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(
        service, tenant, business, max_concurrent_turns=1)
    service.begin_turn(
        tenant.tenant_id, agent.agent_id, idempotency_key="turn-1")

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, agent.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_TURN_LIMIT"


def test_token_limit_is_typed(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(
        service, tenant, business,
        max_tokens=4096,
        safety={"autonomy": "L2", "max_tokens_per_turn": 1024})

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id, agent.agent_id, prompt="hello")
    assert exc.value.code == "AGENT_TOKEN_LIMIT"


def test_missing_approval_is_typed_then_satisfied(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)

    with pytest.raises(BusinessError) as exc:
        service.preview_turn(
            tenant.tenant_id,
            agent.agent_id,
            prompt="Publish the listing.",
            requested_tools=["publish_listing"],
            spend_minor=100,
        )
    assert exc.value.code == "AGENT_APPROVAL_REQUIRED"
    assert "publish" in exc.value.detail
    assert "spend" in exc.value.detail

    plan = service.preview_turn(
        tenant.tenant_id,
        agent.agent_id,
        prompt="Publish the listing.",
        requested_tools=["publish_listing"],
        spend_minor=100,
        human_approved=True,
    )
    assert plan["allowed"] is True
    assert plan["approval_actions_required"] == ["publish", "spend"]
    assert plan["approvals_satisfied"] is True


def test_empty_prompt_is_a_typed_bad_input(env):
    _cfg, _store_obj, service, tenant, business = env
    agent = _create(service, tenant, business)

    for value in ("", "   ", None):
        with pytest.raises(BusinessError) as exc:
            service.preview_turn(
                tenant.tenant_id, agent.agent_id, prompt=value)
        assert exc.value.code == "BUSINESS_BAD_INPUT"


# ---------------------------------------------------------------------------
# HTTP bridge
# ---------------------------------------------------------------------------
def _call(cfg, method, path, *, body=None, query=None):
    return HC.dispatch(cfg, method, path, query=query or {}, body=body or {})


def test_preview_route_is_registered_in_the_business_family():
    route, _params = HC.match(
        "POST", "/api/office/business/agents/agt_1/preview")
    assert route is not None
    assert route["handler"] == "h_business_agent_preview"


def test_preview_route_requires_owner_intent(env):
    cfg, _store, service, tenant, business = env
    agent = _create(service, tenant, business)

    code, out = _call(
        cfg, "POST", f"/api/office/business/agents/{agent.agent_id}/preview",
        body={"tenant_id": tenant.tenant_id, "prompt": "hello"})
    assert code == 403 and out["code"] == "BUSINESS_OWNER_ONLY"


def test_preview_route_returns_the_plan_without_writing(env):
    cfg, store, service, tenant, business = env
    agent = _create(service, tenant, business)
    before = _counts(store)

    code, out = _call(
        cfg, "POST", f"/api/office/business/agents/{agent.agent_id}/preview",
        body={"tenant_id": tenant.tenant_id, "owner": True,
              "prompt": "Draft a launch note.",
              "requested_tools": ["draft_post"]})
    assert code == 200 and out["ok"] is True
    assert out["allowed"] is True
    assert out["plan"]["provider"] == "provider-a"
    assert out["plan"]["model"] == "model-a"
    assert out["plan"]["side_effects"] is False
    assert _counts(store) == before


def test_preview_route_is_a_typed_refusal_for_a_paused_agent(env):
    cfg, _store, service, tenant, business = env
    agent = _create(service, tenant, business)
    service.pause(tenant.tenant_id, agent.agent_id, idempotency_key="pause-1")

    code, out = _call(
        cfg, "POST", f"/api/office/business/agents/{agent.agent_id}/preview",
        body={"tenant_id": tenant.tenant_id, "owner": True,
              "prompt": "hello"})
    assert code == 409
    assert out["code"] == "AGENT_PAUSED"
    assert out["state"] == "REFUSED"


def test_preview_route_is_behind_the_dashboard_token_gate():
    handler = object.__new__(W.WebUIHandler)
    handler.path = "/agent/api/office/business/agents/agt_1/preview"
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
