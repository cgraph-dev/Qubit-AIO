"""P1-06 — durable approval workflow bound to an exact normalized intent.

Hermetic: a throwaway SQLite file under ``tmp_path``. No network, no live
service, no external side effect. These tests pin the execution-time contract:
an approval authorizes exactly one normalized intent under one policy revision;
A-approved/B-execution is refused, an edit invalidates, a delegation cannot
widen scope, expiry stales the gate, and a model cannot approve.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore, approvals, jobs
from voicebridge.business.errors import BusinessError


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _intent(
    business,
    *,
    action: str = "tool:money.payout",
    target: str = "vendor-1",
    recipient: str = "vendor-1",
    amount_minor: int = 500,
    currency: str = "USD",
    revision: str = "v1",
    capabilities: list[str] | None = None,
):
    parameters = {"recipient": recipient, "business_id": business.business_id}
    if capabilities is not None:
        parameters["capability_subset"] = capabilities
    return approvals.normalized_intent(
        action,
        target,
        parameters,
        currency,
        amount_minor,
        revision,
        business_id=business.business_id,
    )


def _request(
    store,
    tenant,
    business,
    *,
    intent=None,
    key: str = "approval-1",
    action: str = "tool:money.payout",
    requested_by: str = "agent:treasurer",
    expires_at: float | None = None,
    job_id: str | None = None,
):
    payload = intent if intent is not None else _intent(business)
    approval, created = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action=action,
        requested_by=requested_by,
        idempotency_key=key,
        job_id=job_id,
        payload=payload,
        policy_version=payload["policy_revision"],
        expires_at=expires_at,
    )
    return approval, payload, created


def _approve(store, tenant, approval_id, *, now: float | None = None):
    return approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval_id,
        decision="approved",
        decided_by="owner",
        now=now,
    )


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


# --- exact intent binding -------------------------------------------------
def test_approved_intent_executes_and_reauthorizes(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, created = _request(store, tenant, business)
    assert created is True
    assert approval.payload == intent

    _approve(store, tenant, approval.approval_id)

    verified = approvals.verify_execution(
        store, tenant.tenant_id, approval.approval_id, intent
    )
    assert verified.status == "approved"
    assert verified.request_hash == approvals.intent_hash(intent)

    authorized = approvals.execution_authorized(
        store,
        tenant.tenant_id,
        approval.approval_id,
        intent=intent,
        policy_version="v1",
    )
    assert authorized.approval_id == approval.approval_id


def test_approved_a_cannot_execute_b(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, _ = _request(store, tenant, business)
    _approve(store, tenant, approval.approval_id)

    variants = {
        "changed price": {**intent, "amount_minor": 900_000},
        "changed recipient": {
            **intent,
            "parameters": {**intent["parameters"], "recipient": "attacker-1"},
        },
        "changed target": {**intent, "target": "attacker-1"},
        "changed action": {**intent, "action": "tool:email.send"},
        "changed currency": {**intent, "currency": "EUR"},
        "hidden subaction": {
            **intent,
            "parameters": {**intent["parameters"], "subaction": "publish"},
        },
    }
    for label, changed in variants.items():
        with pytest.raises(BusinessError) as exc:
            approvals.verify_execution(
                store, tenant.tenant_id, approval.approval_id, changed
            )
        assert _code(exc) == "BUSINESS_APPROVAL_INTENT_MISMATCH", label


def test_request_replay_with_changed_intent_conflicts(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, _ = _request(store, tenant, business)
    replay, _payload, created = _request(store, tenant, business, intent=intent)
    assert created is False
    assert replay.approval_id == approval.approval_id

    with pytest.raises(BusinessError) as exc:
        _request(
            store,
            tenant,
            business,
            intent={**intent, "amount_minor": 900_000},
        )
    assert _code(exc) == "BUSINESS_IDEMPOTENCY_CONFLICT"


# --- staleness, expiry, policy -------------------------------------------
def test_expired_approval_cannot_be_executed(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, _ = _request(store, tenant, business, expires_at=100.0)

    # A late decision can still record a human's approval; the gate is closed at
    # execution time, where the deadline is re-checked.
    _approve(store, tenant, approval.approval_id, now=101.0)
    with pytest.raises(BusinessError) as exc:
        approvals.verify_execution(
            store, tenant.tenant_id, approval.approval_id, intent, now=101.0
        )
    assert _code(exc) == "BUSINESS_APPROVAL_EXPIRED"

    with pytest.raises(BusinessError) as exc:
        approvals.execution_authorized(
            store,
            tenant.tenant_id,
            approval.approval_id,
            intent=intent,
            policy_version="v1",
            now=101.0,
        )
    assert _code(exc) == "BUSINESS_APPROVAL_EXPIRED"


def test_approval_that_expires_before_execution_is_refused(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, _ = _request(store, tenant, business, expires_at=200.0)
    _approve(store, tenant, approval.approval_id, now=150.0)

    with pytest.raises(BusinessError) as exc:
        approvals.execution_authorized(
            store,
            tenant.tenant_id,
            approval.approval_id,
            intent=intent,
            policy_version="v1",
            now=201.0,
        )
    assert _code(exc) == "BUSINESS_APPROVAL_EXPIRED"


def test_expire_queue_timeout_cancels_linked_job(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="money.payout",
        idempotency_key="payout-job",
    )
    jobs.enqueue(store, tenant.tenant_id, job.job_id)
    jobs.claim(store, tenant.tenant_id, job.job_id, worker="w", lease_seconds=60)
    approval, intent, _ = _request(
        store,
        tenant,
        business,
        key="timeout",
        expires_at=100.0,
        job_id=job.job_id,
    )
    assert jobs.get(store, tenant.tenant_id, job.job_id).status == "waiting_approval"

    expired = approvals.expire(store, now=101.0)
    assert [item.approval_id for item in expired] == [approval.approval_id]
    assert approvals.get(
        store, tenant.tenant_id, approval.approval_id
    ).status == "expired"
    assert jobs.get(store, tenant.tenant_id, job.job_id).status == "cancelled"

    with pytest.raises(BusinessError) as exc:
        approvals.verify_execution(
            store, tenant.tenant_id, approval.approval_id, intent, now=101.0
        )
    assert _code(exc) == "BUSINESS_APPROVAL_EXPIRED"


def test_policy_revision_change_refused(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, _ = _request(store, tenant, business)
    _approve(store, tenant, approval.approval_id)

    with pytest.raises(BusinessError) as exc:
        approvals.execution_authorized(
            store,
            tenant.tenant_id,
            approval.approval_id,
            intent=intent,
            policy_version="v2",
        )
    assert _code(exc) == "BUSINESS_APPROVAL_POLICY_STALE"

    with pytest.raises(BusinessError) as exc:
        approvals.verify_execution(
            store,
            tenant.tenant_id,
            approval.approval_id,
            {**intent, "policy_revision": "v2"},
        )
    assert _code(exc) == "BUSINESS_APPROVAL_INTENT_MISMATCH"


# --- edit and delegate ----------------------------------------------------
def test_edit_invalidates_old_and_requires_a_new_decision(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, _ = _request(store, tenant, business)
    edited = _intent(business, amount_minor=700, recipient="vendor-2")

    with pytest.raises(BusinessError) as exc:
        approvals.decide(
            store,
            tenant_id=tenant.tenant_id,
            approval_id=approval.approval_id,
            decision="edit",
            decided_by="owner",
            edited_intent=intent,
        )
    assert _code(exc) == "BUSINESS_BAD_INPUT"

    replacement = approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="edit",
        decided_by="owner",
        edited_intent=edited,
        reason="vendor changed",
    )
    assert replacement.approval_id != approval.approval_id
    assert replacement.status == "pending"
    assert replacement.request_hash == approvals.intent_hash(edited)
    assert approvals.get(
        store, tenant.tenant_id, approval.approval_id
    ).status == "cancelled"

    with pytest.raises(BusinessError) as exc:
        approvals.verify_execution(
            store, tenant.tenant_id, approval.approval_id, intent
        )
    assert _code(exc) == "BUSINESS_APPROVAL_REVOKED"

    _approve(store, tenant, replacement.approval_id)
    assert approvals.verify_execution(
        store, tenant.tenant_id, replacement.approval_id, edited
    ).status == "approved"

    with pytest.raises(BusinessError) as exc:
        approvals.verify_execution(
            store, tenant.tenant_id, replacement.approval_id, intent
        )
    assert _code(exc) == "BUSINESS_APPROVAL_INTENT_MISMATCH"


def test_delegated_approval_cannot_widen_scope(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    intent = _intent(business, capabilities=["tool:read.vendor"])
    approval, _, _ = _request(
        store, tenant, business, intent=intent, key="delegate"
    )

    with pytest.raises(BusinessError) as exc:
        approvals.decide(
            store,
            tenant_id=tenant.tenant_id,
            approval_id=approval.approval_id,
            decision="delegate",
            decided_by="owner",
            delegate_to="owner:delegate",
            edited_intent=None,
            delegated_capabilities=["tool:read.vendor", "tool:money.payout"],
        )
    assert _code(exc) == "BUSINESS_APPROVAL_DELEGATION_WIDENED"

    with pytest.raises(BusinessError) as exc:
        approvals.delegate(
            store,
            tenant_id=tenant.tenant_id,
            approval_id=approval.approval_id,
            decided_by="owner",
            delegate_to="owner:delegate",
            intent={**intent, "amount_minor": 900_000},
        )
    assert _code(exc) == "BUSINESS_APPROVAL_DELEGATION_WIDENED"

    delegated = approvals.delegate(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decided_by="owner",
        delegate_to="owner:delegate",
        intent=intent,
    )
    assert delegated.status == "pending"
    assert delegated.request_hash == approvals.intent_hash(intent)
    assert approvals.get(
        store, tenant.tenant_id, approval.approval_id
    ).status == "cancelled"

    _approve(store, tenant, delegated.approval_id)
    assert approvals.execution_authorized(
        store,
        tenant.tenant_id,
        delegated.approval_id,
        intent=intent,
        policy_version="v1",
    ).approval_id == delegated.approval_id


# --- decision integrity ---------------------------------------------------
def test_decided_approval_cannot_be_redecided(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, _, _ = _request(store, tenant, business)
    _approve(store, tenant, approval.approval_id)

    with pytest.raises(BusinessError) as exc:
        approvals.decide(
            store,
            tenant_id=tenant.tenant_id,
            approval_id=approval.approval_id,
            decision="rejected",
            decided_by="owner",
        )
    assert _code(exc) == "BUSINESS_APPROVAL_NOT_PENDING"


def test_model_cannot_auto_approve(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    approval, intent, _ = _request(store, tenant, business)

    with pytest.raises(BusinessError) as exc:
        approvals.decide(
            store,
            tenant_id=tenant.tenant_id,
            approval_id=approval.approval_id,
            decision="approved",
            decided_by="model:gpt-5",
        )
    assert _code(exc) == "BUSINESS_APPROVAL_AUTOMATION_REFUSED"
    assert approvals.get(
        store, tenant.tenant_id, approval.approval_id
    ).status == "pending"

    _approve(store, tenant, approval.approval_id)
    assert approvals.verify_execution(
        store, tenant.tenant_id, approval.approval_id, intent
    ).status == "approved"


def test_tenant_scope_is_enforced_on_execution(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    other, _ = _scope(store, name="Other", slug="other")
    approval, intent, _ = _request(store, tenant, business)
    _approve(store, tenant, approval.approval_id)

    with pytest.raises(BusinessError) as exc:
        approvals.verify_execution(
            store, other.tenant_id, approval.approval_id, intent
        )
    assert _code(exc) == "BUSINESS_NOT_FOUND"
