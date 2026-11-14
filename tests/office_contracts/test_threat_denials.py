"""P0-05 — real denial tests for the Office threat catalog.

These are the Phase 0 threat-denial cases that the Business OS control plane can
prove today. They are hermetic: a throwaway SQLite file under ``tmp_path``, a
recording executor and no network or live service.

Each test asserts a *denial* (a typed refusal or a refused side effect), not a
happy path. A passing test here is evidence for the catalog rows that cite it.
"""
from __future__ import annotations

import time

import pytest

from voicebridge.business import BusinessStore, Policy, ToolGateway, approvals
from voicebridge.business.errors import BusinessError
from voicebridge.business.tools import ToolRequest


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _policy(tool: str, **kwargs) -> Policy:
    return Policy(allowed_tools=frozenset({tool}), **kwargs)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[ToolRequest] = []

    def __call__(self, request: ToolRequest):
        self.calls.append(request)
        return {"ok": True, "tool": request.tool}


def _invoke(
    gateway: ToolGateway,
    tenant,
    business,
    tool: str,
    *,
    payload=None,
    policy: Policy | None = None,
    key: str = "key-1",
    cost_minor: int = 0,
    dry_run: bool = False,
    approval_id: str | None = None,
):
    return gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:threat-test",
        tool=tool,
        idempotency_key=key,
        policy=policy or _policy(tool),
        payload=payload,
        cost_minor=cost_minor,
        dry_run=dry_run,
        approval_id=approval_id,
    )


def _approved(
    store: BusinessStore,
    tenant,
    business,
    action: str,
    *,
    payload=None,
    expires_at: float | None = None,
):
    approval, created = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action=action,
        requested_by="owner",
        idempotency_key=f"approval-{action}",
        payload=payload,
        expires_at=expires_at,
    )
    assert created
    return approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
    )


# --- ASI02: least agency and tool allowlist ------------------------------
def test_unknown_tool_denied_before_execution(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway,
            tenant,
            business,
            "payments.transfer",
            policy=_policy("email.send"),
        )

    assert exc.value.code == "BUSINESS_TOOL_NOT_ALLOWED"
    assert recorder.calls == []


def test_wildcard_is_not_a_tool_grant(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway,
            tenant,
            business,
            "shell.exec",
            policy=_policy("*"),
        )

    assert exc.value.code == "BUSINESS_TOOL_NOT_ALLOWED"
    assert recorder.calls == []


def test_dry_run_does_not_execute_the_executor(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    outcome = _invoke(
        gateway, tenant, business, "email.send", payload={"to": "a@example.com"},
        dry_run=True,
    )

    assert outcome.executed is False
    assert outcome.invocation.status == "dry_run"
    assert recorder.calls == []


def test_missing_executor_is_a_typed_refusal(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    gateway = ToolGateway(store)  # no external executor shipped

    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, "email.send")

    assert exc.value.code == "BUSINESS_TOOL_EXECUTOR_MISSING"


# --- ASI03: tenant boundary ---------------------------------------------
def test_cross_tenant_business_lookup_fails(tmp_path):
    store = _store(tmp_path)
    tenant_a, business_a = _scope(store, "Acme", "acme")
    tenant_b, _business_b = _scope(store, "Globex", "globex")

    with pytest.raises(BusinessError) as exc:
        store.get_business(tenant_b.tenant_id, business_a.business_id)

    assert exc.value.code == "BUSINESS_NOT_FOUND"


def test_cross_tenant_tool_invocation_fails_before_execution(tmp_path):
    store = _store(tmp_path)
    tenant_a, business_a = _scope(store, "Acme", "acme")
    tenant_b, business_b = _scope(store, "Globex", "globex")
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    with pytest.raises(BusinessError) as exc:
        gateway.invoke(
            tenant_id=tenant_b.tenant_id,
            business_id=business_a.business_id,  # belongs to tenant A
            actor="agent:threat-test",
            tool="email.send",
            idempotency_key="cross-tenant",
            policy=_policy("email.send"),
        )

    assert exc.value.code == "BUSINESS_NOT_FOUND"
    assert recorder.calls == []
    # The foreign tenant still owns its own business, untouched by the attempt.
    assert store.get_business(tenant_b.tenant_id, business_b.business_id).status == "draft"


# --- ASI09 / approval payload confusion ---------------------------------
def test_approval_request_hash_rejects_changed_payload(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="tool:payments.transfer",
        requested_by="owner",
        idempotency_key="approval-1",
        payload={"amount_minor": 500, "to": "vendor-1"},
    )

    with pytest.raises(BusinessError) as exc:
        approvals.request(
            store,
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            action="tool:payments.transfer",
            requested_by="owner",
            idempotency_key="approval-1",
            payload={"amount_minor": 900_000, "to": "attacker-1"},
        )

    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"


def test_approval_for_one_tool_cannot_authorise_another(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval = _approved(store, tenant, business, "tool:email.send")
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway,
            tenant,
            business,
            "payments.transfer",
            policy=_policy("payments.transfer", approval_required=True),
            approval_id=approval.approval_id,
        )

    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert recorder.calls == []


def test_expired_approval_is_refused(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval = _approved(
        store,
        tenant,
        business,
        "tool:payments.transfer",
        expires_at=time.time() - 60,
    )
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway,
            tenant,
            business,
            "payments.transfer",
            policy=_policy("payments.transfer", approval_required=True),
            approval_id=approval.approval_id,
        )

    assert exc.value.code == "BUSINESS_APPROVAL_EXPIRED"
    assert recorder.calls == []


def test_approval_from_another_tenant_is_not_found(tmp_path):
    store = _store(tmp_path)
    tenant_a, business_a = _scope(store, "Acme", "acme")
    tenant_b, business_b = _scope(store, "Globex", "globex")
    approval = _approved(store, tenant_a, business_a, "tool:email.send")
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    with pytest.raises(BusinessError) as exc:
        gateway.invoke(
            tenant_id=tenant_b.tenant_id,
            business_id=business_b.business_id,
            actor="agent:threat-test",
            tool="email.send",
            idempotency_key="foreign-approval",
            policy=_policy("email.send", approval_required=True),
            approval_id=approval.approval_id,
        )

    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert recorder.calls == []


# --- ASI08: cascading cost / duplicate side effect ----------------------
def test_budget_cap_denied_before_execution(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway,
            tenant,
            business,
            "payments.transfer",
            policy=_policy("payments.transfer", max_cost_minor=100),
            cost_minor=101,
        )

    assert exc.value.code == "BUSINESS_BUDGET_EXCEEDED"
    assert recorder.calls == []


def test_duplicate_side_effect_is_one_execution(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    recorder = _Recorder()
    gateway = ToolGateway(store, recorder)

    first = _invoke(gateway, tenant, business, "email.send", key="same-key")
    second = _invoke(gateway, tenant, business, "email.send", key="same-key")

    assert first.executed is True
    assert second.executed is False
    assert second.replayed is True
    assert len(recorder.calls) == 1

