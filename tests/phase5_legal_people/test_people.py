"""P5-07 people lifecycle, access revocation, procurement and SoD contracts."""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge.business import AgentControlService, identity
from voicebridge.business import people
from voicebridge.business.errors import BusinessError
from voicebridge.business.util import now_iso


def _principal(store, tenant_id, *, ref="human-1"):
    return identity.create_principal(
        store, tenant_id, kind="owner", external_ref=ref
    )


def _grant(store, tenant_id, principal_id):
    return identity.grant(
        store,
        tenant_id,
        subject_id=principal_id,
        capability="office",
        resource="room:ops",
        action="enter",
    )


def _agent(store, tenant, business, *, name="Ops Agent", principal_id=None):
    return AgentControlService(store).create(
        tenant.tenant_id,
        business.business_id,
        role_id="role-ops",
        department="Ops",
        display_name=name,
        human_owner="alice",
        provider="openai",
        model="gpt-test",
        idempotency_key=f"agent:{name}",
        security_principal_id=principal_id,
    )[0]


def _person(store, tenant, business, *, principal_id=None, agent_id=None):
    return people.join(
        store,
        tenant.tenant_id,
        business.business_id,
        display_name="Alice",
        department="Ops",
        email="Alice@Example.com",
        principal_id=principal_id,
        agent_id=agent_id,
        idempotency_key="join-alice",
    )


def _month():
    return now_iso()[:7]


def test_join_move_leave_keep_assignment_history(store, scope):
    tenant, business = scope
    profile = _person(store, tenant, business)
    assert profile["person"]["status"] == "active"
    assert profile["person"]["email"] == "alice@example.com"
    assert profile["active_assignment"]["department"] == "Ops"

    people.move(
        store,
        tenant.tenant_id,
        profile["person"]["person_id"],
        department="Finance",
        role_id="role-finance",
        title="Analyst",
    )
    people.move(
        store,
        tenant.tenant_id,
        profile["person"]["person_id"],
        department="Legal",
        role_id="role-legal",
    )
    moved = people.person_profile(
        store, tenant.tenant_id, profile["person"]["person_id"]
    )
    assert moved["active_assignment"]["department"] == "Legal"
    assert [item["department"] for item in moved["assignments"]] == [
        "Ops",
        "Finance",
        "Legal",
    ]
    assert [item["status"] for item in moved["assignments"]] == [
        "ended",
        "ended",
        "active",
    ]

    left = people.leave(
        store,
        tenant.tenant_id,
        profile["person"]["person_id"],
        reason="resigned",
    )
    assert left["person"]["status"] == "left"
    assert left["active_assignment"] is None
    assert left["access_revoked"] is True
    with pytest.raises(BusinessError) as exc:
        people.move(
            store,
            tenant.tenant_id,
            profile["person"]["person_id"],
            department="Ops",
        )
    assert exc.value.code == people.PEOPLE_BAD_STATE_CODE


def test_leave_revokes_access_and_agent_immediately(store, scope):
    tenant, business = scope
    principal = _principal(store, tenant.tenant_id)
    _grant(store, tenant.tenant_id, principal.principal_id)
    agent_principal = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref="ops-agent"
    )
    agent = _agent(
        store, tenant, business, principal_id=agent_principal.principal_id
    )
    profile = _person(
        store,
        tenant,
        business,
        principal_id=principal.principal_id,
        agent_id=agent.agent_id,
    )
    people.record_access(
        store,
        tenant.tenant_id,
        profile["person"]["person_id"],
        principal_id=principal.principal_id,
        scope="office",
    )
    people.record_access(
        store,
        tenant.tenant_id,
        profile["person"]["person_id"],
        principal_id=agent_principal.principal_id,
        scope="agent",
    )
    assert identity.evaluate(
        store, tenant.tenant_id, principal.principal_id, "office", "room:ops", "enter"
    ).allowed is True
    assert AgentControlService(store).get(tenant.tenant_id, agent.agent_id).status == "active"

    left = people.leave(store, tenant.tenant_id, profile["person"]["person_id"])
    assert identity.get_principal(store, tenant.tenant_id, principal.principal_id).status == "revoked"
    assert identity.evaluate(
        store, tenant.tenant_id, principal.principal_id, "office", "room:ops", "enter"
    ).allowed is False
    assert AgentControlService(store).get(tenant.tenant_id, agent.agent_id).status == "revoked"
    assert left["agent_status"] == "revoked"
    assert all(item["status"] == "revoked" for item in left["access"])


def test_access_can_be_revoked_without_ending_employment(store, scope):
    tenant, business = scope
    principal = _principal(store, tenant.tenant_id)
    _grant(store, tenant.tenant_id, principal.principal_id)
    profile = _person(store, tenant, business, principal_id=principal.principal_id)
    people.record_access(
        store,
        tenant.tenant_id,
        profile["person"]["person_id"],
        principal_id=principal.principal_id,
        scope="office",
    )
    revoked = people.revoke_access(
        store,
        tenant.tenant_id,
        profile["person"]["person_id"],
        principal_id=principal.principal_id,
        reason="lost badge",
    )
    assert revoked["person"]["status"] == "active"
    assert revoked["access"][0]["status"] == "revoked"
    assert identity.evaluate(
        store, tenant.tenant_id, principal.principal_id, "office", "room:ops", "enter"
    ).allowed is False
    with pytest.raises(BusinessError):
        people.revoke_access(
            store,
            tenant.tenant_id,
            profile["person"]["person_id"],
            principal_id="prn_not_linked",
        )


def test_training_and_skills_are_records(store, scope):
    tenant, business = scope
    profile = _person(store, tenant, business)
    person_id = profile["person"]["person_id"]
    people.record_skill(
        store, tenant.tenant_id, person_id, name="forklift", level="certified"
    )
    required = people.record_training(
        store, tenant.tenant_id, person_id, name="safety", required=True
    )
    done = people.record_training(
        store,
        tenant.tenant_id,
        person_id,
        name="first-aid",
        required=True,
        completed_at=now_iso(),
    )
    assert required["status"] == "required"
    assert done["status"] == "completed"
    due = people.due_training(store, tenant.tenant_id)
    assert [item["training_id"] for item in due] == [required["training_id"]]
    full = people.person_profile(store, tenant.tenant_id, person_id)
    assert [item["name"] for item in full["skills"]] == ["forklift"]


def test_procurement_approval_sod_spend_and_receipt(store, scope):
    tenant, business = scope
    store.set_budget(
        tenant.tenant_id,
        business.business_id,
        currency="USD",
        period=_month(),
        limit_minor=10_000,
    )
    vendor = store.create_contact(
        tenant.tenant_id,
        business.business_id,
        kind="vendor",
        display_name="Paper Co",
    )
    request = people.request_purchase(
        store,
        tenant.tenant_id,
        business.business_id,
        vendor_name="Paper Co",
        vendor_contact_id=vendor.contact_id,
        purpose="office paper",
        amount_minor=5_000,
        requester="alice",
        idempotency_key="buy-1",
    )
    assert request["status"] == "awaiting_approval"

    with pytest.raises(BusinessError) as exc:
        people.approve_purchase(
            store, tenant.tenant_id, request["request_id"], decided_by="alice"
        )
    assert exc.value.code == people.PEOPLE_SELF_APPROVAL_CODE

    approved = people.approve_purchase(
        store, tenant.tenant_id, request["request_id"], decided_by="boss"
    )
    assert approved["status"] == "approved"
    assert approved["budget_period"] == _month()

    receipt = people.record_receipt(
        store,
        tenant.tenant_id,
        request["request_id"],
        receipt_name="paper-receipt.txt",
        receipt_body="PAID 50.00 USD",
    )
    assert receipt["request"]["status"] == "completed"
    assert receipt["ledger_debit_id"].startswith("lgr_")
    assert receipt["ledger_credit_id"].startswith("lgr_")
    artifact = store.get_artifact(tenant.tenant_id, receipt["receipt_artifact_id"])
    assert artifact.kind == "procurement_receipt"

    conn = sqlite3.connect(store.path)
    try:
        rows = conn.execute(
            "SELECT direction, amount_minor, account FROM bos_ledger_entry"
            " WHERE tenant_id=? ORDER BY created_at, entry_id",
            (tenant.tenant_id,),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("debit", 5_000, "expense:procurement"), ("credit", 5_000, "cash")]


def test_procurement_spend_cap_and_unconfigured_cap(store, scope):
    tenant, business = scope
    store.set_budget(
        tenant.tenant_id,
        business.business_id,
        currency="USD",
        period=_month(),
        limit_minor=1_000,
    )
    request = people.request_purchase(
        store,
        tenant.tenant_id,
        business.business_id,
        vendor_name="Paper Co",
        purpose="over cap",
        amount_minor=2_000,
        requester="alice",
        idempotency_key="buy-cap",
    )
    with pytest.raises(BusinessError) as exc:
        people.approve_purchase(
            store, tenant.tenant_id, request["request_id"], decided_by="boss"
        )
    assert exc.value.code == people.PEOPLE_SPEND_CAP_EXCEEDED_CODE
    assert people.get_purchase(
        store, tenant.tenant_id, request["request_id"]
    )["status"] == "blocked_spend_cap"

    second_tenant = store.create_tenant("No Budget Ltd")
    second_business = store.create_business(
        second_tenant.tenant_id, "No Budget", slug="no-budget"
    )
    unconfigured = people.request_purchase(
        store,
        second_tenant.tenant_id,
        second_business.business_id,
        vendor_name="Paper Co",
        purpose="no cap",
        amount_minor=100,
        requester="alice",
        idempotency_key="buy-no-cap",
    )
    with pytest.raises(BusinessError) as exc:
        people.approve_purchase(
            store,
            second_tenant.tenant_id,
            unconfigured["request_id"],
            decided_by="boss",
        )
    assert exc.value.code == people.PEOPLE_SPEND_CAP_NOT_CONFIGURED_CODE


def test_procurement_reject_path(store, scope):
    tenant, business = scope
    request = people.request_purchase(
        store,
        tenant.tenant_id,
        business.business_id,
        vendor_name="Paper Co",
        purpose="not needed",
        amount_minor=100,
        requester="alice",
        idempotency_key="buy-reject",
    )
    rejected = people.reject_purchase(
        store, tenant.tenant_id, request["request_id"], decided_by="boss"
    )
    assert rejected["status"] == "rejected"
    with pytest.raises(BusinessError):
        people.record_receipt(
            store,
            tenant.tenant_id,
            request["request_id"],
            receipt_name="x.txt",
            receipt_body="x",
        )


def test_people_are_tenant_scoped(store, scope):
    tenant, business = scope
    profile = _person(store, tenant, business)
    other = store.create_tenant("Other")
    with pytest.raises(BusinessError):
        people.get_person(store, other.tenant_id, profile["person"]["person_id"])
    assert people.list_purchases(store, other.tenant_id) == []
    assert people.list_people(store, other.tenant_id) == []
