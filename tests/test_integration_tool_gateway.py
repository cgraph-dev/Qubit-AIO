"""Integration adapters behind the Business OS ToolGateway.

Hermetic: a throwaway SQLite file under ``tmp_path`` and recording transports.
No network client, socket, OAuth flow, real credential or live service exists in
the product module or in these tests.
"""
from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

from _integration_stubs import RecordingTransport
from voicebridge.business import BusinessStore, Policy, ToolGateway, approvals, ledger
from voicebridge.business.errors import BusinessError
from voicebridge.business.integration_tools import (
    INTEGRATION_UNKNOWN_TOOL_CODE,
    IntegrationTool,
    IntegrationToolRegistry,
    TOOL_CRM_CONTACT_UPSERT,
    TOOL_EMAIL_DRAFT,
    TOOL_ETSY_LISTING_PUBLISH,
    TOOL_ETSY_RECEIPT_REFUND,
    TOOL_ETSY_SHOP_READ,
    TOOL_SOCIAL_PUBLISH,
    integration_tools,
)
from voicebridge.business.tools import ExecutorResult, ToolRequest
from voicebridge.integrations.base import (
    ADAPTER_APPROVAL_REQUIRED_CODE,
    ADAPTER_NOT_CONFIGURED_CODE,
    ADAPTER_POLICY_FORBIDDEN_CODE,
    ADAPTER_RATE_LIMITED_CODE,
    ADAPTER_UNAVAILABLE_CODE,
)
from voicebridge.integrations.crm import CrmAdapter
from voicebridge.integrations.email import EmailAdapter
from voicebridge.integrations.etsy import EtsyAdapter
from voicebridge.integrations.social import SocialAdapter

ETSY_CREDS = {"api_key": "key-1", "access_token": "token-1"}
CRM_CREDS = {"api_key": "crm-key"}
EMAIL_CREDS = {"api_key": "email-key"}
SOCIAL_CREDS = {"access_token": "social-token"}


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _etsy(**kwargs) -> EtsyAdapter:
    transport = kwargs.pop("transport", None)
    credentials = kwargs.pop("credentials", ETSY_CREDS)
    shop_id = kwargs.pop("shop_id", "shop-1")
    dry_run = kwargs.pop("dry_run", False)
    return EtsyAdapter(
        shop_id=shop_id,
        transport=transport if transport is not None else RecordingTransport({"ok": True}),
        credentials=credentials,
        dry_run=dry_run,
        **kwargs,
    )


def _approve(store, tenant, business, action: str, key: str):
    approval, _ = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action=action,
        requested_by="owner",
        idempotency_key=key,
    )
    return approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
    )


def _policy(tool: str, autonomy: str = "L2") -> Policy:
    return Policy(allowed_tools=frozenset({tool}), autonomy=autonomy)


def _invoke(
    gateway: ToolGateway,
    tenant,
    business,
    tool: str,
    payload=None,
    *,
    policy: Policy | None = None,
    dry_run: bool = False,
    approval_id: str | None = None,
    key: str = "key-1",
    cost_minor: int = 0,
    rate_limit: int | None = None,
    rate_window_seconds: float | None = None,
):
    return gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:test",
        tool=tool,
        idempotency_key=key,
        policy=policy or _policy(tool),
        payload=payload,
        dry_run=dry_run,
        approval_id=approval_id,
        cost_minor=cost_minor,
        rate_limit=rate_limit,
        rate_window_seconds=rate_window_seconds,
    )


# -- registration -------------------------------------------------------
def test_registry_lists_every_integration_tool_and_registers_customs(tmp_path):
    names = {tool.name for tool in integration_tools()}
    assert names == {
        "etsy.shop.read",
        "etsy.listings.read",
        "etsy.listing.draft",
        "etsy.listing.publish",
        "etsy.listing.price",
        "etsy.receipt.refund",
        "etsy.message.send",
        "social.post.publish",
        "crm.contact.read",
        "crm.contact.upsert",
        "email.send",
        "email.draft",
    }

    store = _store(tmp_path)
    tenant, business = _scope(store)
    registry = IntegrationToolRegistry({"crm": CrmAdapter(transport=RecordingTransport(), credentials=CRM_CREDS, dry_run=False)})

    def echo(adapter, request):
        return ExecutorResult(result={"echo": request.payload})

    registry.register_tool(
        IntegrationTool(
            "custom.echo",
            "crm",
            "echo",
            echo,
            "read",
            description="test-only tool",
        )
    )
    gateway = ToolGateway(store, registry.execute)
    outcome = _invoke(
        gateway, tenant, business, "custom.echo", {"x": 1},
        policy=_policy("custom.echo"),
    )
    assert outcome.executed is True
    assert outcome.invocation.result == {"echo": {"x": 1}}


def test_unknown_tool_is_a_typed_refusal(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    registry = IntegrationToolRegistry()
    gateway = ToolGateway(store, registry.execute)
    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, "etsy.unknown.action", {})
    assert exc.value.code == INTEGRATION_UNKNOWN_TOOL_CODE


# -- dry-run and readiness ---------------------------------------------
def test_gateway_dry_run_records_intent_and_calls_no_adapter(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    registry = IntegrationToolRegistry()  # no adapter at all
    gateway = ToolGateway(store, registry.execute)
    outcome = _invoke(
        gateway, tenant, business, TOOL_ETSY_LISTING_PUBLISH,
        {"listing_id": "lst-1"}, dry_run=True,
    )
    assert outcome.executed is False
    assert outcome.invocation.status == "dry_run"
    assert outcome.invocation.result["dry_run"] is True
    assert outcome.invocation.request["listing_id"] == "lst-1"


def test_missing_transport_is_typed_adapter_unavailable(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    adapter = EtsyAdapter(shop_id="shop-1", credentials=ETSY_CREDS, dry_run=False)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, TOOL_ETSY_SHOP_READ, {})
    assert exc.value.code == ADAPTER_UNAVAILABLE_CODE
    assert "no transport" in exc.value.detail
    recorded = gateway.list_invocations(tenant.tenant_id)
    assert recorded[0].status == "failed"
    assert recorded[0].error_code == ADAPTER_UNAVAILABLE_CODE


def test_missing_credentials_is_typed_adapter_not_configured(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    adapter = _etsy(transport=RecordingTransport(), credentials={})
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, TOOL_ETSY_SHOP_READ, {})
    assert exc.value.code == ADAPTER_NOT_CONFIGURED_CODE
    assert "api_key" in exc.value.detail


def test_dry_run_adapter_is_never_reported_as_executed(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    transport = RecordingTransport({"shop_id": "shop-1"})
    adapter = _etsy(transport=transport, dry_run=True)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    outcome = _invoke(gateway, tenant, business, TOOL_ETSY_SHOP_READ, {})
    assert outcome.executed is False
    assert outcome.invocation.status == "dry_run"
    assert outcome.invocation.result["adapter"]["dry_run"] is True
    assert transport.calls == []


def test_configured_transport_executes_and_maps_the_adapter_result(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    transport = RecordingTransport({"shop_id": "shop-1"})
    adapter = _etsy(transport=transport, dry_run=False)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    outcome = _invoke(gateway, tenant, business, TOOL_ETSY_SHOP_READ, {})
    assert outcome.executed is True
    assert outcome.invocation.status == "success"
    assert outcome.invocation.result["adapter"]["data"] == {"shop_id": "shop-1"}
    assert transport.calls == [
        {"method": "GET", "path": "/v3/application/shops/shop-1", "params": None, "json_body": None}
    ]


# -- approval gates -----------------------------------------------------
@pytest.mark.parametrize(
    ("tool", "payload", "approval_action"),
    [
        (TOOL_ETSY_LISTING_PUBLISH, {"listing_id": "lst-1"}, "etsy.listing.publish"),
        ("etsy.listing.price", {"listing_id": "lst-1", "price_minor": 500}, "etsy.listing.price"),
        (TOOL_ETSY_RECEIPT_REFUND, {"receipt_id": "rcp-1", "amount_minor": 500}, "etsy.receipt.refund"),
        ("etsy.message.send", {"receipt_id": "rcp-1", "body": "thanks"}, "etsy.message.send"),
    ],
)
def test_gated_etsy_actions_require_an_approved_owner_decision(
    tmp_path, tool, payload, approval_action
):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    transport = RecordingTransport({"ok": True})
    adapter = _etsy(transport=transport, dry_run=False)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway, tenant, business, tool, payload,
            policy=_policy(tool, autonomy="L4"),
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert transport.calls == []

    approval = _approve(
        store, tenant, business, f"tool:{tool}", f"apr-{tool}"
    )
    # The adapter's own approval token action also has to match.
    outcome = _invoke(
        gateway, tenant, business, tool, payload,
        policy=_policy(tool, autonomy="L4"),
        approval_id=approval.approval_id,
    )
    assert outcome.executed is True
    assert len(transport.calls) == 1


def test_adapter_level_approval_gate_is_typed(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    adapter = _etsy(transport=RecordingTransport(), dry_run=False)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    # L2 policy does not add the gateway approval gate, so this reaches the
    # adapter and proves the deeper gate exists too.
    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway, tenant, business, TOOL_ETSY_LISTING_PUBLISH,
            {"listing_id": "lst-1"},
        )
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE


def test_expired_gateway_approval_is_refused(tmp_path):
    import time

    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, _ = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action=f"tool:{TOOL_ETSY_LISTING_PUBLISH}",
        requested_by="owner",
        idempotency_key="exp-1",
        expires_at=time.time() - 60,
    )
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
    )
    adapter = _etsy(transport=RecordingTransport(), dry_run=False)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway, tenant, business, TOOL_ETSY_LISTING_PUBLISH,
            {"listing_id": "lst-1"},
            policy=_policy(TOOL_ETSY_LISTING_PUBLISH, autonomy="L4"),
            approval_id=approval.approval_id,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_EXPIRED"


# -- policy -------------------------------------------------------------
def test_platform_policy_forbidden_action_is_typed(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    adapter = SocialAdapter(
        "web",
        transport=RecordingTransport({"ok": True}),
        credentials=SOCIAL_CREDS,
        dry_run=False,
    )
    registry = IntegrationToolRegistry({"social:web": adapter})
    gateway = ToolGateway(store, registry.execute)
    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway, tenant, business, TOOL_SOCIAL_PUBLISH,
            {"platform": "web", "text": "hello"},
        )
    assert exc.value.code == ADAPTER_POLICY_FORBIDDEN_CODE
    assert "automated publish is forbidden" in exc.value.detail


def test_social_publish_requires_platform_and_a_registered_adapter(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    gateway = ToolGateway(store, IntegrationToolRegistry().execute)
    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway, tenant, business, TOOL_SOCIAL_PUBLISH,
            {"text": "hi"}, key="social-1",
        )
    assert exc.value.code == "integration_bad_input"

    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway, tenant, business, TOOL_SOCIAL_PUBLISH,
            {"platform": "x", "text": "hi"}, key="social-2",
        )
    assert exc.value.code == ADAPTER_UNAVAILABLE_CODE


# -- idempotency --------------------------------------------------------
def test_idempotent_replay_and_changed_request_conflict(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    transport = RecordingTransport({"ok": True})
    adapter = EmailAdapter(
        transport=transport, credentials=EMAIL_CREDS, dry_run=False
    )
    gateway = ToolGateway(store, IntegrationToolRegistry({"email": adapter}).execute)
    payload = {"to": "buyer@example.com", "subject": "Hello", "body": "Hi"}
    first = _invoke(gateway, tenant, business, TOOL_EMAIL_DRAFT, payload, key="draft-1")
    assert first.executed is True
    assert len(transport.calls) == 1

    replay = _invoke(gateway, tenant, business, TOOL_EMAIL_DRAFT, payload, key="draft-1")
    assert replay.replayed is True
    assert replay.executed is False
    assert replay.invocation.invocation_id == first.invocation.invocation_id
    assert len(transport.calls) == 1

    with pytest.raises(BusinessError) as exc:
        _invoke(
            gateway, tenant, business, TOOL_EMAIL_DRAFT,
            {**payload, "body": "Changed"}, key="draft-1",
        )
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"
    assert len(transport.calls) == 1


def test_rate_limit_refusal_is_typed(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    adapter = _etsy(
        transport=RecordingTransport(), dry_run=False,
        rate_limit=1, rate_window_seconds=60.0,
    )
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    _invoke(gateway, tenant, business, TOOL_ETSY_SHOP_READ, {}, key="rate-1")
    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, TOOL_ETSY_SHOP_READ, {}, key="rate-2")
    assert exc.value.code == ADAPTER_RATE_LIMITED_CODE


# -- ledger and audit ---------------------------------------------------
def test_ledger_and_audit_are_recorded_once_across_replay(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    transport = RecordingTransport({"refund_id": "rfd-1"})
    adapter = _etsy(transport=transport, dry_run=False)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)
    approval = _approve(
        store, tenant, business,
        f"tool:{TOOL_ETSY_RECEIPT_REFUND}", "refund-approval",
    )
    payload = {"receipt_id": "rcp-1", "amount_minor": 1250, "currency": "USD"}
    outcome = _invoke(
        gateway, tenant, business, TOOL_ETSY_RECEIPT_REFUND, payload,
        policy=_policy(TOOL_ETSY_RECEIPT_REFUND, autonomy="L4"),
        approval_id=approval.approval_id,
        key="refund-1",
    )
    assert outcome.executed is True
    assert outcome.invocation.result["_ledger"]["amount_minor"] == 1250
    entries = ledger.list_entries(store, tenant.tenant_id)
    assert len(entries) == 1
    assert entries[0].direction == "debit"
    assert entries[0].amount_minor == 1250
    assert ledger.summary(store, tenant.tenant_id)["debit_minor"] == 1250
    assert len(store.list_audit(tenant.tenant_id, action="tool.invoke")) == 1
    assert len(store.list_audit(tenant.tenant_id, action="tool.result")) == 1

    replay = _invoke(
        gateway, tenant, business, TOOL_ETSY_RECEIPT_REFUND, payload,
        policy=_policy(TOOL_ETSY_RECEIPT_REFUND, autonomy="L4"),
        approval_id=approval.approval_id,
        key="refund-1",
    )
    assert replay.replayed is True
    assert len(ledger.list_entries(store, tenant.tenant_id)) == 1
    assert len(store.list_audit(tenant.tenant_id, action="tool.invoke")) == 1
    assert len(store.list_audit(tenant.tenant_id, action="tool.result")) == 1


def test_audit_failure_is_recorded_once(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    def failing(_request):
        raise RuntimeError("provider exploded")

    gateway = ToolGateway(store, failing)
    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, "email.send")
    assert exc.value.code == "BUSINESS_TOOL_FAILED"
    assert len(store.list_audit(tenant.tenant_id, action="tool.result")) == 1


# -- executor context ---------------------------------------------------
def test_executor_receives_scope_policy_approval_and_rate_context(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    seen: list[ToolRequest] = []

    def capture(request):
        seen.append(request)
        return {"ok": True}

    gateway = ToolGateway(store, capture)
    policy = Policy(allowed_tools=frozenset({"email.draft"}), autonomy="L2")
    _invoke(
        gateway, tenant, business, "email.draft", {"x": 1},
        policy=policy,
        approval_id="apr-manual",
        key="ctx-1",
        rate_limit=5,
        rate_window_seconds=30.0,
    )
    request = seen[0]
    assert request.tenant_id == tenant.tenant_id
    assert request.business_id == business.business_id
    assert request.actor == "agent:test"
    assert request.policy is policy
    assert request.approval_id == "apr-manual"
    assert request.idempotency_key == "ctx-1"
    assert request.rate_limit == 5
    assert request.rate_window_seconds == 30.0


def test_crm_upsert_and_read_are_wired(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    transport = RecordingTransport({"external_id": "crm-1", "name": "Ada"})
    adapter = CrmAdapter(
        transport=transport, credentials=CRM_CREDS, dry_run=False
    )
    gateway = ToolGateway(store, IntegrationToolRegistry({"crm": adapter}).execute)
    upserted = _invoke(
        gateway, tenant, business, TOOL_CRM_CONTACT_UPSERT,
        {"external_id": "crm-1", "name": "Ada"},
        key="crm-up-1",
    )
    assert upserted.executed is True
    read = _invoke(
        gateway, tenant, business, "crm.contact.read",
        {"external_id": "crm-1"}, key="crm-read-1",
    )
    assert read.executed is True
    assert read.invocation.result["adapter"]["data"]["name"] == "Ada"


# -- hermeticity --------------------------------------------------------
def test_product_modules_import_no_network_client():
    forbidden = {"requests", "httpx", "aiohttp", "urllib", "http", "socket", "oauthlib"}
    modules = [
        "voicebridge/business/tools.py",
        "voicebridge/business/integration_tools.py",
        "voicebridge/business/etsy_workflow.py",
        "voicebridge/integrations/base.py",
        "voicebridge/integrations/etsy.py",
        "voicebridge/integrations/social.py",
        "voicebridge/integrations/crm.py",
        "voicebridge/integrations/email.py",
    ]
    root = Path(__file__).resolve().parents[1]
    for relative in modules:
        tree = ast.parse((root / relative).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), f"{relative} imports {imported & forbidden}"


def test_execution_opens_no_socket(tmp_path, monkeypatch):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    adapter = _etsy(transport=RecordingTransport({"shop_id": "shop-1"}), dry_run=False)
    gateway = ToolGateway(store, IntegrationToolRegistry({"etsy": adapter}).execute)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a network socket was opened")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    outcome = _invoke(gateway, tenant, business, TOOL_ETSY_SHOP_READ, {})
    assert outcome.executed is True
