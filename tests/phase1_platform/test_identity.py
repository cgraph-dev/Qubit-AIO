"""P1-01 — identity, RBAC + ABAC, deny-by-default, step-up and revocation.

Hermetic: a throwaway SQLite file under ``tmp_path``; no network, no live
service, no operator state.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore, identity
from voicebridge.business.errors import BusinessError

TENANT = "tenant-scope"
CAPABILITY = "files.read"
RESOURCE = "workspace:alpha"
ACTION = "read"


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _tenant(store: BusinessStore, name: str = "Acme") -> str:
    return store.create_tenant(name).tenant_id


def _principal(store: BusinessStore, tenant_id: str, kind: str = "agent") -> str:
    return identity.create_principal(store, tenant_id, kind=kind).principal_id


def _allow(store: BusinessStore, tenant_id: str, subject_id: str, **overrides):
    values = {
        "subject_id": subject_id,
        "capability": CAPABILITY,
        "resource": RESOURCE,
        "action": ACTION,
        "data_class": "internal",
        "environment": "production",
    }
    values.update(overrides)
    return identity.grant(store, tenant_id, **values)


def _decide(store: BusinessStore, tenant_id: str, subject_id: str, **overrides):
    values = {
        "capability": CAPABILITY,
        "resource": RESOURCE,
        "action": ACTION,
        "data_class": "internal",
        "environment": "production",
    }
    values.update(overrides)
    return identity.evaluate(store, tenant_id, subject_id, **values)


# -- deny by default -------------------------------------------------------
def test_no_grant_is_denied_by_default(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)

    decision = _decide(store, tenant, agent)
    assert decision.allowed is False
    assert decision.reason == "no_matching_grant"
    assert decision.grant_id is None
    assert decision.as_dict()["allowed"] is False


def test_missing_or_inactive_principal_is_denied(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)

    missing = _decide(store, tenant, "prn_MISSING")
    assert missing.allowed is False
    assert missing.reason == "principal_not_found"

    agent = _principal(store, tenant)
    _allow(store, tenant, agent)
    identity.revoke_principal(store, tenant, agent, actor="owner")
    revoked = _decide(store, tenant, agent)
    assert revoked.allowed is False
    assert revoked.reason == "principal_inactive"


def test_an_explicit_grant_allows_exactly_its_tuple(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    grant = _allow(store, tenant, agent)

    decision = _decide(store, tenant, agent)
    assert decision.allowed is True
    assert decision.reason == "grant_matched"
    assert decision.grant_id == grant.grant_id

    # Every ABAC axis is part of the identity of the permission.
    assert _decide(store, tenant, agent, data_class="restricted").allowed is False
    assert _decide(store, tenant, agent, environment="staging").allowed is False
    assert _decide(store, tenant, agent, action="write").allowed is False
    assert _decide(store, tenant, agent, resource="workspace:beta").allowed is False


def test_deny_beats_allow_regardless_of_order(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    _allow(store, tenant, agent)
    deny = _allow(store, tenant, agent, effect="deny")

    decision = _decide(store, tenant, agent)
    assert decision.allowed is False
    assert decision.reason == "deny_grant"
    assert decision.grant_id == deny.grant_id


def test_expired_grant_no_longer_allows(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    _allow(store, tenant, agent, expires_at=1000.0)

    assert _decide(store, tenant, agent, now=999.0).allowed is True
    assert _decide(store, tenant, agent, now=1000.0).allowed is False
    assert _decide(store, tenant, agent, now=1001.0).allowed is False


def test_revoked_grant_is_immediate_and_revoke_is_idempotent(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    grant = _allow(store, tenant, agent)

    identity.revoke_grant(store, tenant, grant.grant_id, actor="owner")
    assert _decide(store, tenant, agent).allowed is False

    # A second revocation does not rewrite history or invent a new row.
    again = identity.revoke_grant(store, tenant, grant.grant_id, actor="owner")
    assert again.revoked_at is not None
    assert len(identity.list_grants(store, tenant, subject_id=agent)) == 1


def test_cross_tenant_subject_and_grant_are_refused(tmp_path):
    store = _store(tmp_path)
    tenant_a = _tenant(store, "Tenant A")
    tenant_b = _tenant(store, "Tenant B")
    agent_a = _principal(store, tenant_a)
    agent_b = _principal(store, tenant_b)
    _allow(store, tenant_a, agent_a)

    # A's grant does not leak into B, and A's subject is unknown in B.
    assert _decide(store, tenant_b, agent_b).allowed is False
    assert _decide(store, tenant_b, agent_a).allowed is False
    assert _decide(store, tenant_a, agent_b).allowed is False

    with pytest.raises(BusinessError) as refused:
        identity.grant(
            store,
            tenant_b,
            subject_id=agent_a,
            capability=CAPABILITY,
            resource=RESOURCE,
            action=ACTION,
        )
    assert refused.value.code == identity.PRINCIPAL_NOT_FOUND_CODE


def test_role_permissions_expand_but_stay_explicit(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    role = identity.create_role(
        store,
        tenant,
        "reader",
        [
            {
                "capability": CAPABILITY,
                "resource": RESOURCE,
                "action": ACTION,
                "data_class": "internal",
                "environment": "production",
            }
        ],
    )
    _allow(store, tenant, agent, role_id=role.role_id)

    decision = _decide(store, tenant, agent)
    assert decision.allowed is True
    assert decision.matched_role_id == role.role_id

    # A role does not become a wildcard: a different data class is still denied.
    assert _decide(store, tenant, agent, data_class="restricted").allowed is False


def test_no_wildcard_grants_or_role_permissions(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)

    with pytest.raises(BusinessError) as refused:
        _allow(store, tenant, agent, capability="*")
    assert refused.value.code == identity.WILDCARD_NOT_ALLOWED_CODE

    with pytest.raises(BusinessError):
        _allow(store, tenant, agent, resource="workspace:*")

    with pytest.raises(BusinessError):
        identity.create_role(
            store,
            tenant,
            "wild",
            [{"capability": "*", "resource": RESOURCE, "action": ACTION}],
        )

    # Asking for a wildcard is a denial, not a match.
    assert _decide(store, tenant, agent, capability="*").allowed is False


def test_approval_required_is_carried_on_the_decision(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    _allow(store, tenant, agent, approval_required=True)

    decision = _decide(store, tenant, agent)
    assert decision.allowed is True
    assert decision.approval_required is True


# -- session tokens --------------------------------------------------------
def test_token_is_audience_bound(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    token, session = identity.issue_session_token(
        store, tenant, agent, audience="office-api", ttl_seconds=60, now=100.0
    )

    verified = identity.verify_session_token(
        store, token, audience="office-api", tenant_id=tenant, now=101.0
    )
    assert verified.auth_session_id == session.auth_session_id

    with pytest.raises(BusinessError) as refused:
        identity.verify_session_token(
            store, token, audience="other-audience", now=101.0
        )
    assert refused.value.code == identity.AUTH_AUDIENCE_MISMATCH_CODE

    with pytest.raises(BusinessError) as cross_tenant:
        identity.verify_session_token(
            store, token, audience="office-api", tenant_id="ten_other", now=101.0
        )
    assert cross_tenant.value.code == identity.AUTH_TENANT_MISMATCH_CODE


def test_token_expiry_and_revocation_are_immediate(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)

    token, session = identity.issue_session_token(
        store, tenant, agent, audience="office-api", ttl_seconds=10, now=100.0
    )
    with pytest.raises(BusinessError) as expired:
        identity.verify_session_token(
            store, token, audience="office-api", now=110.0
        )
    assert expired.value.code == identity.AUTH_TOKEN_EXPIRED_CODE

    token2, session2 = identity.issue_session_token(
        store, tenant, agent, audience="office-api", ttl_seconds=60, now=200.0
    )
    identity.revoke_session_token(
        store, tenant, session2.auth_session_id, actor="owner", now=201.0
    )
    with pytest.raises(BusinessError) as revoked:
        identity.verify_session_token(
            store, token2, audience="office-api", now=202.0
        )
    assert revoked.value.code == identity.AUTH_TOKEN_REVOKED_CODE

    # The raw token is never stored; only its SHA-256 hash is at rest.
    assert session.token_hash != token
    assert len(session.token_hash) == 64


def test_revoking_a_principal_kills_its_sessions(tmp_path):
    store = _store(tmp_path)
    tenant = _tenant(store)
    agent = _principal(store, tenant)
    token, _ = identity.issue_session_token(
        store, tenant, agent, audience="office-api", ttl_seconds=60, now=100.0
    )

    identity.revoke_principal(store, tenant, agent, actor="owner", now=101.0)

    with pytest.raises(BusinessError) as refused:
        identity.verify_session_token(
            store, token, audience="office-api", now=102.0
        )
    assert refused.value.code == identity.AUTH_TOKEN_REVOKED_CODE

    # A new session cannot be minted for a revoked principal.
    with pytest.raises(BusinessError) as inactive:
        identity.issue_session_token(
            store, tenant, agent, audience="office-api", ttl_seconds=60
        )
    assert inactive.value.code == identity.AUTH_PRINCIPAL_INACTIVE_CODE


def test_token_for_another_tenant_is_rejected_by_tenant_pin(tmp_path):
    store = _store(tmp_path)
    tenant_a = _tenant(store, "Tenant A")
    tenant_b = _tenant(store, "Tenant B")
    agent_a = _principal(store, tenant_a)
    token, _ = identity.issue_session_token(
        store, tenant_a, agent_a, audience="office-api", ttl_seconds=60, now=100.0
    )

    with pytest.raises(BusinessError) as refused:
        identity.verify_session_token(
            store, token, audience="office-api", tenant_id=tenant_b, now=101.0
        )
    assert refused.value.code == identity.AUTH_TENANT_MISMATCH_CODE


# -- step-up ---------------------------------------------------------------
def test_step_up_required_for_l4_and_l5():
    for risk in ("L4", "L5", 4, 5):
        result = identity.require_step_up("files.read", risk)
        assert result.required is True
        assert result.risk_level >= 4

    for risk in ("L1", "L2", "L3", 1, 2, 3):
        result = identity.require_step_up("files.read", risk)
        assert result.required is False


def test_high_risk_actions_require_step_up_at_any_level():
    assert identity.require_step_up("payment.refund", "L2").required is True
    assert identity.require_step_up("credential.rotate", "L1").required is True
    assert identity.require_step_up("workspace.publish", "L3").required is True
    assert identity.require_step_up("files.read", "L3").required is False

    with pytest.raises(BusinessError):
        identity.require_step_up("files.read", "L9")
