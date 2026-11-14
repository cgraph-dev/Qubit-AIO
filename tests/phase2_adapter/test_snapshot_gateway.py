"""P2-03 — a P1 gateway call is denied for an unregistered capability."""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore, Policy, ToolGateway
from voicebridge.business import dynamic_catalog as dc
from voicebridge.business import policy as pol
from voicebridge.business.errors import TOOL_NOT_ALLOWED_CODE, BusinessError
from _p2_helpers import EXPIRES, ISSUED, SUBJECT


def _env(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("Acme")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    record = dc.tool_capability("search", version="2.1.0")
    cat = dc.DynamicCapabilityCatalog(
        [dc.FunctionCatalogSource("tools", lambda: (record,))]
    )
    return store, tenant, business, record, cat


def _grant(record):
    return pol.Grant(
        subject=SUBJECT,
        capability=record.capability_id,
        resource=record.resource,
        action=record.action,
    )


def _invoke(gateway, tenant, business, cat, snapshot, tool):
    return gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor=SUBJECT,
        tool=tool,
        idempotency_key=f"idem-{tool}",
        policy=Policy(allowed_tools=frozenset({tool})),
        dry_run=True,
        snapshot=snapshot,
        catalog=cat.policy_catalog(),
        now=ISSUED,
    )


def test_gateway_refuses_an_unregistered_capability(tmp_path):
    store, tenant, business, record, cat = _env(tmp_path)
    snapshot = cat.snapshot_for(
        SUBJECT,
        (_grant(record),),
        tenant_id=tenant.tenant_id,
        issued_at=ISSUED,
        expires_at=EXPIRES,
    )
    gateway = ToolGateway(store)
    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, cat, snapshot, "tool:missing")
    assert exc.value.code == pol.CAPABILITY_UNKNOWN_CODE


def test_gateway_denies_a_known_capability_without_a_grant(tmp_path):
    store, tenant, business, record, cat = _env(tmp_path)
    snapshot = cat.snapshot_for(
        SUBJECT,
        (),
        tenant_id=tenant.tenant_id,
        issued_at=ISSUED,
        expires_at=EXPIRES,
    )
    gateway = ToolGateway(store)
    with pytest.raises(BusinessError) as exc:
        _invoke(gateway, tenant, business, cat, snapshot, record.capability_id)
    assert exc.value.code == TOOL_NOT_ALLOWED_CODE


def test_gateway_executes_a_granted_capability_as_a_dry_run(tmp_path):
    store, tenant, business, record, cat = _env(tmp_path)
    snapshot = cat.snapshot_for(
        SUBJECT,
        (_grant(record),),
        tenant_id=tenant.tenant_id,
        issued_at=ISSUED,
        expires_at=EXPIRES,
    )
    gateway = ToolGateway(store)
    outcome = _invoke(
        gateway, tenant, business, cat, snapshot, record.capability_id
    )
    # Dry run validates and records intent; it executes nothing.
    assert outcome.executed is False
    assert outcome.invocation is not None
