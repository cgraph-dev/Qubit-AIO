"""Shared fixtures for the Phase 1 capability-gateway tests.

Hermetic: a throwaway SQLite file under ``tmp_path`` and recording executors.
No network, no live service, no credentials, no live state.
"""
from __future__ import annotations

from typing import Any

from voicebridge.business import BusinessStore, Policy, ToolGateway
from voicebridge.business import policy as pol

NOW = 1_500_000.0
ISSUED = 1_000_000.0
EXPIRES = 2_000_000.0

READ_TOOL = "etsy.shop.read"
READ_RESOURCE = "etsy.shop"
READ_ACTION = "read"

WRITE_TOOL = "etsy.listing.publish"
WRITE_RESOURCE = "etsy.listing"
WRITE_ACTION = "publish"

SUBJECT = "agent:test"


def store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def catalog() -> pol.CapabilityCatalog:
    return pol.CapabilityCatalog(
        [
            pol.Capability(
                capability_id=READ_TOOL,
                category="commerce",
                resource=READ_RESOURCE,
                action=READ_ACTION,
                risk="L1",
                required_scopes=("etsy:read",),
                health="healthy",
                version="1.2.0",
            ),
            pol.Capability(
                capability_id=WRITE_TOOL,
                category="commerce",
                resource=WRITE_RESOURCE,
                action=WRITE_ACTION,
                risk="L4",
                required_scopes=("etsy:write",),
                approval_policy="required",
                budget_minor=10_000,
                health="degraded",
                version="3.0.0",
            ),
        ]
    )


def grant(
    capability: str,
    resource: str,
    action: str,
    *,
    subject: str = SUBJECT,
    approval: str = "none",
    expires_at: float | None = None,
) -> pol.Grant:
    return pol.Grant(
        subject=subject,
        capability=capability,
        resource=resource,
        action=action,
        data_class="internal",
        environment="production",
        expires_at=expires_at,
        approval=approval,
    )


def read_grant() -> pol.Grant:
    return grant(READ_TOOL, READ_RESOURCE, READ_ACTION)


def write_grant() -> pol.Grant:
    return grant(
        WRITE_TOOL, WRITE_RESOURCE, WRITE_ACTION, approval="required"
    )


def snapshot(
    tenant_id: str,
    *,
    grants=(),
    denials=(),
    subject: str = SUBJECT,
    revision: str = "pol-1",
    issued_at: float = ISSUED,
    expires_at: float | None = EXPIRES,
):
    return pol.compile_snapshot(
        tenant_id=tenant_id,
        subject_id=subject,
        policy_revision=revision,
        grants=grants,
        denials=denials,
        issued_at=issued_at,
        expires_at=expires_at,
        catalog=catalog(),
    )


def policy_for(*tools: str) -> Policy:
    return Policy(allowed_tools=frozenset(tools), autonomy="L2")


class Recorder:
    def __init__(self, result: Any = None) -> None:
        self.calls: list[Any] = []
        self.result = {"ok": True} if result is None else result

    def __call__(self, request):
        self.calls.append(request)
        return self.result


def gateway(store: BusinessStore, executor=None) -> ToolGateway:
    return ToolGateway(store, executor)


def invoke(
    gateway: ToolGateway,
    tenant,
    business,
    tool: str,
    payload=None,
    *,
    snap=None,
    cat=None,
    subject: str = SUBJECT,
    dry_run: bool = False,
    approval_id: str | None = None,
    key: str = "key-1",
    cost_minor: int = 0,
    resource: str | None = None,
    action: str | None = None,
    now: float = NOW,
):
    return gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor=subject,
        tool=tool,
        idempotency_key=key,
        policy=policy_for(tool),
        payload=payload,
        dry_run=dry_run,
        approval_id=approval_id,
        cost_minor=cost_minor,
        snapshot=snap,
        catalog=cat,
        subject_id=subject,
        resource=resource,
        action=action,
        now=now,
    )


def intent_for(
    tool: str,
    resource: str,
    action: str,
    payload,
    *,
    cost_minor: int = 0,
):
    return pol.normalized_intent(
        tool=tool,
        resource=resource,
        action=action,
        parameters=payload,
        currency="USD",
        amount_minor=cost_minor,
        data_class="internal",
        environment="production",
    )
