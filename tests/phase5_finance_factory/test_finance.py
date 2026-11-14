"""P5-05 — finance: double-entry, payment truth, reconciliation.

Hermetic: a throwaway SQLite store under ``tmp_path``. No live payment
provider, bank, network or credential is touched. Every assertion reads real
ledger/event/approval rows.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from voicebridge.business import approvals, finance
from voicebridge.business.errors import BusinessError
from voicebridge.business.store import BusinessStore

OWNER = "owner:trick"


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant.tenant_id, business.business_id


def _count(store: BusinessStore, table: str, where: str = "", params=()) -> int:
    conn = sqlite3.connect(store.path)
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def _approved_payment(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    key: str,
    *,
    amount: int = 500,
    account: str = "expense:supplies",
):
    intent, _created = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="stripe",
        provider_idempotency_key=key,
        amount_minor=amount,
        currency="USD",
        account=account,
        actor=OWNER,
    )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=intent.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    return finance.execute_payment(
        store, tenant_id=tenant_id, intent_id=intent.intent_id, actor=OWNER
    )


def test_unbalanced_posting_is_refused(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    with pytest.raises(BusinessError) as exc:
        finance.post(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            lines=[("cash", "debit", 100)],
            currency="USD",
            idempotency_key="unbalanced",
        )
    assert exc.value.code == finance.UNBALANCED_CODE
    with pytest.raises(BusinessError) as exc:
        finance.post(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            lines=[("cash", "debit", 100), ("revenue", "credit", 90)],
            currency="USD",
            idempotency_key="unbalanced-2",
        )
    assert exc.value.code == finance.UNBALANCED_CODE
    assert _count(store, "bos_ledger_entry") == 0


def test_balanced_posting_is_atomic_and_idempotent(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    lines = [("expense:office", "debit", 1200), ("cash", "credit", 1200)]
    posting, created = finance.post(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        lines=lines,
        currency="USD",
        idempotency_key="rent-1",
        memo="rent",
    )
    assert created is True
    assert posting.balanced
    assert posting.debit_minor == posting.credit_minor == 1200
    assert _count(store, "bos_ledger_entry") == 2

    replay, created_again = finance.post(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        lines=lines,
        currency="USD",
        idempotency_key="rent-1",
        memo="rent",
    )
    assert created_again is False
    assert replay.posting_id == posting.posting_id
    assert _count(store, "bos_ledger_entry") == 2

    with pytest.raises(BusinessError) as exc:
        finance.post(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            lines=[("expense:office", "debit", 999), ("cash", "credit", 999)],
            currency="USD",
            idempotency_key="rent-1",
        )
    assert exc.value.code == finance.PAYMENT_IDEMPOTENCY_CODE

    fetched = finance.get_posting(store, tenant_id, posting.posting_id)
    assert fetched.lines == posting.lines


def test_money_must_be_integer_minor_units(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    for amount in (12.5, True, "100"):
        with pytest.raises(BusinessError):
            finance.post(
                store,
                tenant_id=tenant_id,
                business_id=business_id,
                lines=[("cash", "debit", amount), ("revenue", "credit", amount)],
                currency="USD",
                idempotency_key=f"bad-{amount}",
            )


def test_payment_requires_human_approval(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    intent, created = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="stripe",
        provider_idempotency_key="pi-approval",
        amount_minor=500,
        currency="USD",
        account="expense:supplies",
        actor=OWNER,
    )
    assert created is True
    assert intent.state == finance.STATE_PENDING
    assert intent.approval_id
    with pytest.raises(BusinessError) as exc:
        finance.execute_payment(
            store, tenant_id=tenant_id, intent_id=intent.intent_id, actor=OWNER
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"

    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=intent.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    sent = finance.execute_payment(
        store, tenant_id=tenant_id, intent_id=intent.intent_id, actor=OWNER
    )
    assert sent.state == finance.STATE_SENT


def test_duplicate_payment_is_idempotent_and_conflicts_on_change(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    first, created = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="stripe",
        provider_idempotency_key="pi-dupe",
        amount_minor=500,
        currency="USD",
        account="expense:supplies",
        actor=OWNER,
    )
    second, created_again = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="stripe",
        provider_idempotency_key="pi-dupe",
        amount_minor=500,
        currency="USD",
        account="expense:supplies",
        actor=OWNER,
    )
    assert created is True
    assert created_again is False
    assert first.intent_id == second.intent_id
    assert _count(
        store, "bos_event", "type='finance.payment.created'"
    ) == 1

    with pytest.raises(BusinessError) as exc:
        finance.create_payment_intent(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            provider="stripe",
            provider_idempotency_key="pi-dupe",
            amount_minor=900,
            currency="USD",
            account="expense:supplies",
            actor=OWNER,
        )
    assert exc.value.code in (
        "BUSINESS_IDEMPOTENCY_CONFLICT",
        finance.PAYMENT_IDEMPOTENCY_CODE,
    )


def test_narrative_can_never_mark_paid(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, "pi-narrative")
    finance.mark_payment_unknown(
        store, tenant_id=tenant_id, intent_id=sent.intent_id, reason="timeout"
    )
    noted = finance.record_narrative(
        store,
        tenant_id=tenant_id,
        intent_id=sent.intent_id,
        text="The provider dashboard says paid, trust me.",
        actor=OWNER,
    )
    assert noted.state == finance.STATE_UNKNOWN
    assert finance.get_payment(store, tenant_id, sent.intent_id).state == (
        finance.STATE_UNKNOWN
    )


@pytest.mark.parametrize("outcome", ["sent", "compensated", "failed"])
def test_unknown_outcome_resolution_is_explicit(tmp_path, outcome):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, f"pi-{outcome}")
    finance.mark_payment_unknown(
        store, tenant_id=tenant_id, intent_id=sent.intent_id, reason="provider timeout"
    )
    receipt = None
    if outcome in ("sent", "compensated"):
        receipt = {
            "provider": "stripe",
            "provider_ref": f"ch-{outcome}",
            "amount_minor": 500,
            "currency": "USD",
            "status": "acked",
        }
    resolved = finance.resolve_unknown(
        store,
        tenant_id=tenant_id,
        intent_id=sent.intent_id,
        outcome=outcome,
        receipt=receipt,
        actor=OWNER,
    )
    assert resolved.state == outcome


@pytest.mark.parametrize("outcome", ["sent", "compensated"])
def test_financial_resolution_requires_provider_receipt(tmp_path, outcome):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, f"pi-noreceipt-{outcome}")
    finance.mark_payment_unknown(
        store, tenant_id=tenant_id, intent_id=sent.intent_id, reason="timeout"
    )
    with pytest.raises(BusinessError) as exc:
        finance.resolve_unknown(
            store,
            tenant_id=tenant_id,
            intent_id=sent.intent_id,
            outcome=outcome,
            actor=OWNER,
        )
    assert exc.value.code == finance.NARRATIVE_NOT_TRUTH_CODE
    assert finance.get_payment(store, tenant_id, sent.intent_id).state == (
        finance.STATE_UNKNOWN
    )


def test_acknowledge_and_complete_post_balanced_ledger(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, "pi-settle")
    acked = finance.acknowledge_payment(
        store,
        tenant_id=tenant_id,
        intent_id=sent.intent_id,
        receipt={
            "provider_ref": "ch-settle",
            "amount_minor": 500,
            "currency": "USD",
            "status": "acked",
        },
        actor=OWNER,
    )
    assert acked.state == finance.STATE_ACKED
    completed = finance.complete_payment(
        store, tenant_id=tenant_id, intent_id=acked.intent_id, actor=OWNER
    )
    assert completed.state == finance.STATE_COMPLETED
    again = finance.complete_payment(
        store, tenant_id=tenant_id, intent_id=completed.intent_id, actor=OWNER
    )
    assert again.postings == completed.postings
    assert _count(store, "bos_ledger_entry") == 2
    summary = finance.summary(store, tenant_id, business_id=business_id)
    assert summary["balanced"] is True
    assert summary["entry_count"] == 2


def test_refund_and_chargeback_entries_balance(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, "pi-refundable")
    acked = finance.acknowledge_payment(
        store,
        tenant_id=tenant_id,
        intent_id=sent.intent_id,
        receipt={
            "provider_ref": "ch-refundable",
            "amount_minor": 500,
            "currency": "USD",
        },
        actor=OWNER,
    )
    finance.complete_payment(
        store, tenant_id=tenant_id, intent_id=acked.intent_id, actor=OWNER
    )

    refund_approval = finance.request_money_approval(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="finance.refund",
        target=acked.intent_id,
        amount_minor=200,
        currency="USD",
        requested_by=OWNER,
    )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=refund_approval.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    refund = finance.record_refund(
        store,
        tenant_id=tenant_id,
        intent_id=acked.intent_id,
        amount_minor=200,
        approval_id=refund_approval.approval_id,
        actor=OWNER,
        idempotency_key="refund-1",
    )
    assert refund.balanced
    replay = finance.record_refund(
        store,
        tenant_id=tenant_id,
        intent_id=acked.intent_id,
        amount_minor=200,
        approval_id=refund_approval.approval_id,
        actor=OWNER,
        idempotency_key="refund-1",
    )
    assert replay.posting_id == refund.posting_id

    chargeback_approval = finance.request_money_approval(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="finance.chargeback",
        target=acked.intent_id,
        amount_minor=100,
        currency="USD",
        requested_by=OWNER,
    )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=chargeback_approval.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    chargeback = finance.record_chargeback(
        store,
        tenant_id=tenant_id,
        intent_id=acked.intent_id,
        amount_minor=100,
        approval_id=chargeback_approval.approval_id,
        actor=OWNER,
        idempotency_key="chargeback-1",
    )
    assert chargeback.balanced
    summary = finance.summary(store, tenant_id, business_id=business_id)
    assert summary["balanced"] is True
    assert summary["debit_minor"] == summary["credit_minor"]


def test_refund_requires_approval(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, "pi-refund-guard")
    with pytest.raises(BusinessError) as exc:
        finance.record_refund(
            store,
            tenant_id=tenant_id,
            intent_id=sent.intent_id,
            amount_minor=100,
            approval_id=None,
            actor=OWNER,
        )
    assert exc.value.code == finance.PAYMENT_APPROVAL_REQUIRED_CODE


def test_reconciliation_matches_receipts_and_flags_unknown(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, "pi-recon")
    acked = finance.acknowledge_payment(
        store,
        tenant_id=tenant_id,
        intent_id=sent.intent_id,
        receipt={
            "provider_ref": "ch-recon",
            "amount_minor": 500,
            "currency": "USD",
        },
        actor=OWNER,
    )
    finance.complete_payment(
        store, tenant_id=tenant_id, intent_id=acked.intent_id, actor=OWNER
    )
    report = finance.reconcile(store, tenant_id, business_id=business_id)
    assert len(report["matched"]) == 1
    assert report["mismatched"] == []
    assert report["ok"] is True

    unknown_sent = _approved_payment(store, tenant_id, business_id, "pi-recon-unknown")
    finance.mark_payment_unknown(
        store, tenant_id=tenant_id, intent_id=unknown_sent.intent_id, reason="timeout"
    )
    report = finance.reconcile(store, tenant_id, business_id=business_id)
    assert [row["intent_id"] for row in report["unknown"]] == [
        unknown_sent.intent_id
    ]
    assert report["ok"] is False


def test_reconciliation_flags_amount_mismatch(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    sent = _approved_payment(store, tenant_id, business_id, "pi-mismatch", amount=500)
    finance.acknowledge_payment(
        store,
        tenant_id=tenant_id,
        intent_id=sent.intent_id,
        receipt={
            "provider_ref": "ch-short",
            "amount_minor": 400,
            "currency": "USD",
        },
        actor=OWNER,
    )
    report = finance.reconcile(store, tenant_id, business_id=business_id)
    assert len(report["mismatched"]) == 1
    assert report["mismatched"][0]["intent_id"] == sent.intent_id
    assert report["ok"] is False


def test_period_summary_reads_the_ledger(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    finance.post(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        lines=[("cash", "debit", 800), ("revenue", "credit", 800)],
        currency="USD",
        idempotency_key="sale-period",
    )
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    summary = finance.period_summary(
        store, tenant_id, period, business_id=business_id
    )
    assert summary["debit_minor"] == 800
    assert summary["credit_minor"] == 800
    assert summary["balanced"] is True
    assert summary["entry_count"] == 2


def test_tenant_separation_for_payments_and_ledger(tmp_path):
    store = _store(tmp_path)
    tenant_a, business_a = _scope(store, "Alpha", "alpha")
    tenant_b, business_b = _scope(store, "Beta", "beta")
    sent = _approved_payment(store, tenant_a, business_a, "pi-tenant-a")

    with pytest.raises(BusinessError) as exc:
        finance.get_payment(store, tenant_b, sent.intent_id)
    assert exc.value.code == finance.PAYMENT_NOT_FOUND_CODE
    assert finance.list_payments(store, tenant_b) == []
    summary_b = finance.summary(store, tenant_b, business_id=business_b)
    assert summary_b["entry_count"] == 0


def test_payout_requires_approval_and_balances(tmp_path):
    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    with pytest.raises(BusinessError) as exc:
        finance.record_payout(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            reference="payout-1",
            amount_minor=250,
            currency="USD",
            approval_id=None,
            actor=OWNER,
        )
    assert exc.value.code == finance.PAYMENT_APPROVAL_REQUIRED_CODE

    approval = finance.request_money_approval(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="finance.payout",
        target="payout-1",
        amount_minor=250,
        currency="USD",
        requested_by=OWNER,
    )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    posting = finance.record_payout(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        reference="payout-1",
        amount_minor=250,
        currency="USD",
        approval_id=approval.approval_id,
        actor=OWNER,
    )
    assert posting.balanced
    summary = finance.summary(store, tenant_id, business_id=business_id)
    assert summary["balanced"] is True


def test_payment_intent_can_link_a_durable_job(tmp_path):
    from voicebridge.business import jobs

    store = _store(tmp_path)
    tenant_id, business_id = _scope(store)
    job, _ = jobs.create(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        kind="order",
        idempotency_key="order-1",
    )
    intent, _created = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="stripe",
        provider_idempotency_key="pi-job",
        amount_minor=500,
        currency="USD",
        account="expense:supplies",
        actor=OWNER,
        job_id=job.job_id,
    )
    assert intent.job_id == job.job_id
    assert finance.get_payment(store, tenant_id, intent.intent_id).job_id == job.job_id
