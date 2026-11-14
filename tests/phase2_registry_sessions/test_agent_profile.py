"""P2-01 — Agent registry and the effective AgentProfile selectors.

Hermetic: a throwaway SQLite file under ``tmp_path``. No network, no model, no
live service. These tests pin the security rule the Office depends on: the
agent is the principal, an explicit grant is the only authority, a skill may
require a capability but may never grant one, and an MCP server is only usable
with a complete tool-schema hash.
"""
from __future__ import annotations

import pytest

from voicebridge.business import AgentControlService, BusinessStore, identity
from voicebridge.business.errors import BusinessError
from voicebridge.business.policy import Capability, CapabilityCatalog

SHA = "a" * 64


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _principals(store: BusinessStore, tenant_id: str):
    owner = identity.create_principal(
        store, tenant_id, kind="owner", external_ref="owner"
    )
    agent = identity.create_principal(
        store, tenant_id, kind="agent", external_ref="agent-1"
    )
    return owner, agent


def _agent(store, tenant, business, principal, **overrides):
    svc = AgentControlService(store)
    args = dict(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.analyst",
        department="Research",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key="create-1",
        security_principal_id=principal.principal_id,
    )
    args.update(overrides)
    agent, _ = svc.create(**args)
    return svc, agent


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


# -- grant composition -----------------------------------------------------
def test_effective_profile_separates_explicit_inherited_and_effective(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)
    role = identity.create_role(
        store,
        tenant.tenant_id,
        "Web reader",
        permissions=[
            {
                "capability": "web.read",
                "resource": "web",
                "action": "read",
                "effect": "allow",
            }
        ],
    )
    explicit = identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.read",
        resource="crm.contacts",
        action="read",
    )
    inherited = identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="role.bundle",
        resource="role",
        action="read",
        role_id=role.role_id,
    )
    denied = identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.delete",
        resource="crm.contacts",
        action="delete",
        effect="deny",
    )
    svc, agent = _agent(store, tenant, business, principal)

    profile = svc.effective_profile(tenant.tenant_id, agent.agent_id)

    assert profile["security_principal_id"] == principal.principal_id
    assert profile["autonomy_ceiling"] == "L0"
    assert profile["model_policy"]["provider"] == "provider-a"
    assert profile["model_policy"]["pinned"] is True

    explicit_caps = {item["capability"] for item in profile["grants"]["explicit"]}
    inherited_caps = {item["capability"] for item in profile["grants"]["inherited"]}
    effective_caps = {item["capability"] for item in profile["grants"]["effective"]}
    assert explicit_caps == {"crm.read"}
    assert inherited_caps == {"web.read"}
    assert effective_caps == {"crm.read", "web.read"}
    assert profile["grants"]["denials"][0]["capability"] == "crm.delete"
    assert set(profile["capability_grants"]) == {
        explicit.grant_id,
        inherited.grant_id,
    }
    assert denied.grant_id not in profile["capability_grants"]
    assert {item["source"] for item in profile["grants"]["effective"]} == {
        "explicit",
        "inherited",
    }


def test_deny_wins_over_a_matching_allow(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)
    identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.read",
        resource="crm.contacts",
        action="read",
    )
    identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.read",
        resource="crm.contacts",
        action="read",
        effect="deny",
    )
    svc, agent = _agent(store, tenant, business, principal)

    profile = svc.effective_profile(tenant.tenant_id, agent.agent_id)

    assert len(profile["grants"]["explicit"]) == 1
    assert profile["grants"]["explicit"][0]["effect"] == "allow"
    assert profile["grants"]["effective"] == []
    assert profile["grants"]["denials"][0]["effect"] == "deny"


def test_cross_tenant_revoked_expired_and_other_subject_grants_never_effective(
    tmp_path,
):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)
    other_principal = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref="agent-2"
    )
    other_tenant, other_business = _scope(store, "Other", "other")
    _other_owner, foreign_principal = _principals(store, other_tenant.tenant_id)

    identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.read",
        resource="crm.contacts",
        action="read",
    )
    revoked = identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.write",
        resource="crm.contacts",
        action="write",
    )
    identity.revoke_grant(store, tenant.tenant_id, revoked.grant_id, actor="owner")
    identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.export",
        resource="crm.contacts",
        action="export",
        expires_at=100.0,
    )
    identity.grant(
        store,
        tenant.tenant_id,
        subject_id=other_principal.principal_id,
        capability="crm.admin",
        resource="crm.contacts",
        action="write",
    )
    identity.grant(
        store,
        other_tenant.tenant_id,
        subject_id=foreign_principal.principal_id,
        capability="foreign.read",
        resource="foreign",
        action="read",
    )
    svc, agent = _agent(store, tenant, business, principal)

    profile = svc.effective_profile(tenant.tenant_id, agent.agent_id, now=200.0)

    effective_caps = {item["capability"] for item in profile["grants"]["effective"]}
    assert effective_caps == {"crm.read"}
    assert all(
        item["capability"] not in {"crm.admin", "foreign.read"}
        for item in profile["grants"]["explicit"]
    )
    # The other tenant's own session/profile stays isolated too.
    other_svc, other_agent = _agent(
        store,
        other_tenant,
        other_business,
        foreign_principal,
        idempotency_key="create-other",
    )
    other_profile = other_svc.effective_profile(
        other_tenant.tenant_id, other_agent.agent_id
    )
    assert {
        item["capability"] for item in other_profile["grants"]["effective"]
    } == {"foreign.read"}


def test_effective_profile_catalog_can_refuse_an_unknown_capability(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)
    identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="crm.read",
        resource="crm.contacts",
        action="read",
    )
    identity.grant(
        store,
        tenant.tenant_id,
        subject_id=principal.principal_id,
        capability="ghost.capability",
        resource="ghost",
        action="read",
    )
    svc, agent = _agent(store, tenant, business, principal)
    catalog = CapabilityCatalog(
        [
            Capability(
                capability_id="crm.read",
                category="crm",
                resource="crm.contacts",
                action="read",
                risk="L1",
            )
        ]
    )

    catalog.register(
        Capability(
            capability_id="ghost.capability",
            category="ghost",
            resource="ghost",
            action="read",
        )
    )
    profile = svc.effective_profile(
        tenant.tenant_id, agent.agent_id, catalog=catalog
    )
    assert {
        item["capability"]: item.get("risk")
        for item in profile["grants"]["effective"]
    } == {"crm.read": "L1", "ghost.capability": "L0"}

    with pytest.raises(BusinessError) as exc:
        svc.effective_profile(
            tenant.tenant_id,
            agent.agent_id,
            catalog=CapabilityCatalog(
                [
                    Capability(
                        capability_id="crm.read",
                        category="crm",
                        resource="crm.contacts",
                        action="read",
                    )
                ]
            ),
        )
    assert _code(exc) == "BUSINESS_CAPABILITY_UNKNOWN"


# -- selectors -------------------------------------------------------------
def test_selectors_round_trip_without_becoming_grants(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)
    metadata = {
        "skills": [
            {
                "skill_id": "research",
                "version": "1.2.0",
                "requires_capabilities": ["web.read"],
            }
        ],
        "mcp_servers": [
            {
                "name": "graft",
                "version": "0.12.0",
                "schema_hash": SHA,
                "enabled": True,
                "allowed_tools": ["find_code", "find_all"],
            }
        ],
        "memory_scopes": [
            {"scope": "agent", "read": True, "write": True, "append": False},
            {"scope": "session", "read": True, "write": False, "append": True},
        ],
        "secret_refs": ["env:PROVIDER_KEY"],
        "budgets": {
            "currency": "USD",
            "period": "monthly",
            "period_limit_minor": 5000,
            "per_turn_limit_minor": 100,
        },
        "appearance": {
            "character_profile_id": "char-atlas",
            "portrait": "portraits/atlas.png",
            "voice": "voice-atlas",
        },
        "schedule_ref": "schedule.weekday",
        "policy_revision": 7,
    }
    svc, agent = _agent(store, tenant, business, principal, metadata=metadata)

    profile = svc.effective_profile(tenant.tenant_id, agent.agent_id)

    assert profile["skills"] == ["research"]
    assert profile["skill_requirements"][0]["requirements"] == ["web.read"]
    assert profile["mcp_servers"][0]["allowed_tools"] == [
        "find_all",
        "find_code",
    ]
    assert profile["mcp_servers"][0]["schema_hash"] == SHA
    assert profile["memory_scopes"] == ["agent", "session"]
    assert profile["memory_scope_access"][1]["append"] is True
    assert profile["secret_refs"] == ["env:PROVIDER_KEY"]
    assert profile["budgets"]["period"] == "monthly"
    assert profile["schedule"] == {
        "cron": "manual",
        "timezone": "UTC",
        "ref": "schedule.weekday",
    }
    assert profile["appearance"]["voice"] == "voice-atlas"
    assert profile["policy_revision"] == 7
    # A skill requirement is not a grant.
    assert profile["capability_grants"] == []
    assert profile["grants"]["effective"] == []


def test_skill_claiming_a_capability_is_refused(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            metadata={"skills": [{"skill_id": "x", "grants": ["crm.read"]}]},
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"

    svc, agent = _agent(store, tenant, business, principal)
    with pytest.raises(BusinessError) as exc:
        svc.update(
            tenant.tenant_id,
            agent.agent_id,
            changes={
                "metadata": {
                    "skills": [
                        {"skill_id": "x", "grants_capability": True}
                    ]
                }
            },
            idempotency_key="update-skill-grant",
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"


def test_wildcards_in_selectors_are_refused(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            metadata={
                "mcp_servers": [
                    {
                        "name": "graft",
                        "schema_hash": SHA,
                        "allowed_tools": ["*"],
                    }
                ]
            },
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            idempotency_key="create-2",
            metadata={
                "skills": [
                    {"skill_id": "x", "requires_capabilities": ["all"]}
                ]
            },
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"


def test_enabled_mcp_server_requires_a_complete_schema_hash(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)

    for bad in (None, "abc", "A" * 64):
        entry = {"name": "graft", "enabled": True}
        if bad is not None:
            entry["schema_hash"] = bad
        with pytest.raises(BusinessError) as exc:
            _agent(
                store,
                tenant,
                business,
                principal,
                idempotency_key=f"create-{bad}",
                metadata={"mcp_servers": [entry]},
            )
        assert _code(exc) == "AGENT_PROFILE_INVALID"

    # A disabled server may omit the hash; it grants nothing either way.
    svc, agent = _agent(
        store,
        tenant,
        business,
        principal,
        idempotency_key="create-disabled",
        metadata={"mcp_servers": [{"name": "graft", "enabled": False}]},
    )
    profile = svc.effective_profile(tenant.tenant_id, agent.agent_id)
    assert profile["mcp_servers"][0]["enabled"] is False


def test_unknown_memory_scope_is_a_typed_refusal(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            metadata={"memory_scopes": ["galaxy"]},
        )
    assert _code(exc) == "AGENT_UNKNOWN_SCOPE"


def test_secret_refs_cannot_carry_a_secret_value(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            metadata={"secret_refs": ["sk-live-0123456789abcdef"]},
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"


def test_list_sizes_are_bounded(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            metadata={"skills": [f"skill-{n}" for n in range(65)]},
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"


# -- model route -----------------------------------------------------------
def test_fallback_route_must_be_fully_pinned(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            fallback_provider="provider-b",
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"

    with pytest.raises(BusinessError) as exc:
        _agent(
            store,
            tenant,
            business,
            principal,
            provider="unset",
            fallback_provider="provider-b",
            fallback_model="model-b",
        )
    assert _code(exc) == "AGENT_PROFILE_INVALID"

    svc, agent = _agent(
        store,
        tenant,
        business,
        principal,
        idempotency_key="create-fallback",
        fallback_provider="provider-b",
        fallback_model="model-b",
    )
    profile = svc.effective_profile(tenant.tenant_id, agent.agent_id)
    assert profile["model_policy"]["fallback_chain"] == [
        {"provider": "provider-b", "model": "model-b"}
    ]


def test_unpinned_primary_is_reported_not_silently_filled(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _owner, principal = _principals(store, tenant.tenant_id)
    svc, agent = _agent(
        store, tenant, business, principal, provider="unset"
    )

    profile = svc.effective_profile(tenant.tenant_id, agent.agent_id)

    assert profile["model_policy"]["pinned"] is False
    assert profile["model_policy"]["provider"] == "unset"
    assert profile["model_policy"]["fallback_chain"] == []
