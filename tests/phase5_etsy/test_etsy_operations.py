"""P5-01 — Etsy digital-shop operations, sandbox/fake only.

Hermetic: a throwaway SQLite store and an injected in-memory Etsy transport.
No network, no live service, no real Etsy publish, no paid art. The negative
cases are the contract: no approval → no publish, a changed payload → refusal,
a duplicate effect → one receipt, an ambiguous provider outcome → ``unknown``
until an explicit reconciliation receipt.
"""
from __future__ import annotations

import hashlib
import json
import socket

import pytest

from voicebridge.business import approvals, eventlog, jobs, ledger
from voicebridge.business.errors import BusinessError
from voicebridge.business.etsy_operations import (
    ARTIFACT_ASSET,
    ARTIFACT_DRAFT,
    ARTIFACT_ENTITLEMENT,
    ARTIFACT_LEDGER_RECONCILIATION,
    ARTIFACT_ORDER,
    ARTIFACT_PAYOUT,
    ARTIFACT_PUBLISH_RECEIPT,
    ARTIFACT_RECONCILIATION,
    ARTIFACT_REFUND_RECEIPT,
    ARTIFACT_SUPPORT,
    ETSY_APPROVAL_PAYLOAD_MISMATCH,
    ETSY_APPROVAL_REQUIRED,
    ETSY_ASSET_VERSION_MISMATCH,
    ETSY_INLINE_ART_FORBIDDEN,
    ETSY_NOT_PUBLISHED,
    ETSY_PAID_ART_GUARD,
    ETSY_PUBLISH_NOT_EXECUTED,
    ETSY_RECONCILIATION_RECEIPT_REQUIRED,
    ETSY_RECONCILIATION_REQUIRED,
    STATUS_APPROVED,
    STATUS_AWAITING_APPROVAL,
    STATUS_BALANCED,
    STATUS_PUBLISHED,
    STATUS_RECONCILED,
    STATUS_RECORDED,
    STATUS_SETTLED,
    STATUS_UNKNOWN,
)

from _etsy_ops_helpers import (
    approve,
    artifact_by_name,
    build_ops,
    count_artifacts,
    create_listing,
    event_rows,
    ledger_rows,
    publish_listing,
    record_inputs,
    record_order,
    refund_flow,
    tamper_artifact_body,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def tripwire(*_args, **_kwargs):
        raise AssertionError("the Etsy operations tests attempted network I/O")

    monkeypatch.setattr(socket, "socket", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    monkeypatch.setattr(socket, "create_server", tripwire)


def _ready(store, scope):
    ops, transport = build_ops(store)
    tenant, business = scope
    record_inputs(ops, tenant, business)
    return ops, transport, tenant, business


def test_full_happy_path_draft_approval_publish_order_refund_payout(
        store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)

    assert created.status == STATUS_AWAITING_APPROVAL
    assert created.job.status == "waiting_approval"
    assert created.approval.status == "pending"
    assert created.approval.action == "tool:etsy.listing.publish"
    assert created.intent_hash == created.approval.request_hash
    draft = artifact_by_name(
        store, tenant.tenant_id, f"{ARTIFACT_DRAFT}:{created.job.job_id}"
    )
    draft_body = json.loads(draft.body)
    assert draft_body["listing_id"].startswith("sbx-lst-")
    assert draft_body["provider"] == "etsy-sandbox"

    approved = approve(ops, tenant, created)
    assert approved.status == STATUS_APPROVED
    assert approved.job.status == "queued"

    published = publish_listing(ops, tenant, created)
    assert published.status == STATUS_PUBLISHED
    assert published.job.status == "completed"
    assert published.receipt["state"] == "active"
    assert published.receipt["listing_id"] == draft_body["listing_id"]
    assert published.intent_hash == created.intent_hash
    receipt = artifact_by_name(
        store,
        tenant.tenant_id,
        f"{ARTIFACT_PUBLISH_RECEIPT}:{created.job.job_id}",
    )
    assert json.loads(receipt.body)["approval_id"] == created.approval.approval_id
    assert transport.publish_calls == 1

    order = record_order(ops, tenant, business, created)
    assert order.status == STATUS_RECORDED
    assert len(order.ledger_entry_ids) == 2
    entitlement = ops.grant_entitlement(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        version="v1",
        download_ref="sandbox-download://planner/v1",
    )
    assert entitlement.status == STATUS_RECORDED
    assert json.loads(entitlement.artifact.body)["immutable"] is True
    support = ops.open_support_case(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        case_ref="case-1",
        reason="buyer asked for the download link",
    )
    assert support.status == "open"
    assert count_artifacts(store, ARTIFACT_SUPPORT) == 1
    assert count_artifacts(store, ARTIFACT_ORDER) == 1
    assert count_artifacts(store, ARTIFACT_ENTITLEMENT) == 1

    refund = refund_flow(ops, tenant, business)
    assert refund.status == STATUS_SETTLED
    assert refund.job.status == "completed"
    assert transport.refund_calls == 1

    payout = ops.record_payout(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
        provider_receipt_ref="etsy-payout-feed:2026-11-14:1",
        gross_minor=600,
        fee_minor=60,
    )
    assert payout.status == STATUS_RECORDED
    assert payout.detail["net_minor"] == 340
    assert count_artifacts(store, ARTIFACT_PAYOUT) == 1

    balance = ops.ledger_balance(
        tenant.tenant_id, business.business_id, order_id="order-1"
    )
    assert balance["balanced"] is True
    assert balance["debit_minor"] == balance["credit_minor"]
    assert balance["cash_minor"] == 340
    assert balance["accounts"]["etsy.sales"]["credit_minor"] == 600
    assert balance["accounts"]["etsy.refunds"]["debit_minor"] == 200

    reconciled = ops.reconcile_ledger(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
    )
    assert reconciled.status == STATUS_BALANCED
    assert reconciled.detail["balanced"] is True
    assert count_artifacts(store, ARTIFACT_LEDGER_RECONCILIATION) == 1
    assert transport.draft_calls == 1
    assert transport.publish_calls == 1
    assert transport.refund_calls == 1


def test_approval_payload_confusion_is_refused(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    draft = artifact_by_name(
        store, tenant.tenant_id, f"{ARTIFACT_DRAFT}:{created.job.job_id}"
    )

    def swap_price(body):
        body["listing"]["price_minor"] = 1
        body["listing_id"] = "sbx-lst-swapped"

    tamper_artifact_body(store, draft.artifact_id, swap_price)
    with pytest.raises(BusinessError) as excinfo:
        publish_listing(ops, tenant, created)
    assert excinfo.value.code == ETSY_APPROVAL_PAYLOAD_MISMATCH
    assert transport.publish_calls == 0
    assert jobs.get(store, tenant.tenant_id, created.job.job_id).status == "queued"
    assert count_artifacts(store, ARTIFACT_PUBLISH_RECEIPT) == 0


def test_approval_from_another_job_is_refused(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    first = create_listing(ops, tenant, business, key="listing-a")
    second = create_listing(ops, tenant, business, key="listing-b")
    approve(ops, tenant, second)
    with pytest.raises(BusinessError) as excinfo:
        ops.publish_listing(
            tenant_id=tenant.tenant_id,
            job_id=first.job.job_id,
            actor="agent:etsy",
            approval_id=second.approval.approval_id,
        )
    assert excinfo.value.code == ETSY_APPROVAL_REQUIRED
    assert transport.publish_calls == 0


def test_no_publish_without_approval(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    with pytest.raises(BusinessError) as excinfo:
        publish_listing(ops, tenant, created)
    assert excinfo.value.code == ETSY_APPROVAL_REQUIRED
    assert transport.publish_calls == 0
    assert count_artifacts(store, ARTIFACT_PUBLISH_RECEIPT) == 0
    assert jobs.get(store, tenant.tenant_id, created.job.job_id).status == "waiting_approval"


def test_duplicate_publish_is_deduped(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    first = publish_listing(ops, tenant, created)
    second = publish_listing(ops, tenant, created)
    assert second.artifact.artifact_id == first.artifact.artifact_id
    assert transport.publish_calls == 1
    assert count_artifacts(store, ARTIFACT_PUBLISH_RECEIPT) == 1
    invocation_rows = store.list_artifacts(tenant.tenant_id)
    assert len(
        [a for a in invocation_rows if a.kind == ARTIFACT_PUBLISH_RECEIPT]
    ) == 1


def test_duplicate_refund_is_deduped(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)
    record_order(ops, tenant, business, created)
    refund = refund_flow(ops, tenant, business, amount_minor=200)

    requested = jobs.get(store, tenant.tenant_id, refund.job.job_id)
    again = ops.refund(
        tenant_id=tenant.tenant_id,
        job_id=requested.job_id,
        actor="agent:etsy",
    )
    assert again.artifact.artifact_id == refund.artifact.artifact_id
    assert transport.refund_calls == 1
    assert count_artifacts(store, ARTIFACT_REFUND_RECEIPT) == 1
    assert len(ledger_rows(store, tenant.tenant_id, "etsy.refunds")) == 1
    assert len(ledger_rows(store, tenant.tenant_id, "etsy.receivable")) == 2


def test_unknown_publish_stays_unknown_until_reconciliation(store, scope):
    ops, transport = build_ops(store, ambiguous_publish=True)
    tenant, business = scope
    record_inputs(ops, tenant, business)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)

    unknown = publish_listing(ops, tenant, created)
    assert unknown.status == STATUS_UNKNOWN
    assert unknown.receipt["unknown"] is True
    assert jobs.get(store, tenant.tenant_id, created.job.job_id).status == "unknown"
    assert transport.publish_calls == 1
    assert count_artifacts(store, ARTIFACT_PUBLISH_RECEIPT) == 1

    with pytest.raises(BusinessError) as excinfo:
        publish_listing(ops, tenant, created)
    assert excinfo.value.code == ETSY_RECONCILIATION_REQUIRED
    with pytest.raises(BusinessError) as excinfo:
        record_order(ops, tenant, business, created)
    assert excinfo.value.code == ETSY_NOT_PUBLISHED
    with pytest.raises(BusinessError) as excinfo:
        ops.reconcile_unknown(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="owner",
            job_id=created.job.job_id,
            resolution="completed",
            provider_ref="",
        )
    assert excinfo.value.code == ETSY_RECONCILIATION_RECEIPT_REQUIRED

    reconciled = ops.reconcile_unknown(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="owner",
        job_id=created.job.job_id,
        resolution="completed",
        provider_ref="etsy-reconciliation:2026-11-14:1",
    )
    assert reconciled.status == STATUS_RECONCILED
    assert reconciled.job.status == "completed"
    assert count_artifacts(store, ARTIFACT_RECONCILIATION) == 1
    assert transport.publish_calls == 1
    order = record_order(ops, tenant, business, created)
    assert order.status == STATUS_RECORDED
    after = publish_listing(ops, tenant, created)
    assert after.artifact.artifact_id == reconciled.artifact.artifact_id
    assert transport.publish_calls == 1


def test_reconciliation_that_confirms_no_effect_fails_the_job(store, scope):
    ops, transport = build_ops(store, ambiguous_publish=True)
    tenant, business = scope
    record_inputs(ops, tenant, business)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)

    reconciled = ops.reconcile_unknown(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="owner",
        job_id=created.job.job_id,
        resolution="not_executed",
        provider_ref="etsy-reconciliation:2026-11-14:none",
    )
    assert reconciled.status == STATUS_RECONCILED
    assert reconciled.job.status == "failed"
    assert count_artifacts(store, ARTIFACT_PUBLISH_RECEIPT) == 2
    with pytest.raises(BusinessError) as excinfo:
        record_order(ops, tenant, business, created)
    assert excinfo.value.code == ETSY_NOT_PUBLISHED


def test_entitlement_is_immutable_and_versioned(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)
    record_order(ops, tenant, business, created)

    first = ops.grant_entitlement(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        version="v1",
        download_ref="sandbox-download://planner/v1",
    )
    again = ops.grant_entitlement(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        version="v1",
        download_ref="sandbox-download://planner/v1",
    )
    assert again.artifact.artifact_id == first.artifact.artifact_id
    assert again.artifact.sha256 == first.artifact.sha256
    assert count_artifacts(store, ARTIFACT_ENTITLEMENT) == 1
    with pytest.raises(BusinessError) as excinfo:
        ops.grant_entitlement(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent:etsy",
            order_id="order-1",
            version="v2",
            download_ref="sandbox-download://planner/v2",
        )
    assert excinfo.value.code == ETSY_ASSET_VERSION_MISMATCH


def test_refund_and_payout_double_entry_balance(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)
    record_order(ops, tenant, business, created)
    refund_flow(ops, tenant, business, amount_minor=200)
    payout = ops.record_payout(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
        provider_receipt_ref="etsy-payout-feed:2026-11-14:1",
        gross_minor=600,
        fee_minor=60,
    )
    assert payout.detail["net_minor"] == 340

    balance = ops.ledger_balance(
        tenant.tenant_id, business.business_id, order_id="order-1"
    )
    assert balance["balanced"] is True
    assert balance["debit_minor"] == balance["credit_minor"] == 1200
    assert balance["cash_minor"] == 340
    assert balance["accounts"]["etsy.fees"]["debit_minor"] == 60
    assert balance["accounts"]["etsy.receivable"] == {
        "debit_minor": 600,
        "credit_minor": 600,
    }
    summary = ledger.summary(
        store, tenant.tenant_id, business_id=business.business_id
    )
    assert summary["debit_minor"] == summary["credit_minor"]

    with pytest.raises(BusinessError) as excinfo:
        ops.record_payout(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent:etsy",
            order_id="order-1",
            payout_ref="payout-2",
            provider_receipt_ref="",
            gross_minor=600,
            fee_minor=60,
        )
    assert excinfo.value.code == "BUSINESS_BAD_INPUT"


def test_unknown_refund_is_not_completed_and_does_not_touch_the_ledger(
        store, scope):
    ops, transport = build_ops(store, ambiguous_refund=True)
    tenant, business = scope
    record_inputs(ops, tenant, business)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)
    record_order(ops, tenant, business, created)

    requested = ops.request_refund(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        amount_minor=200,
        reason="ambiguous provider response",
        idempotency_key="refund-1",
    )
    ops.approve_refund(
        tenant_id=tenant.tenant_id,
        approval_id=requested.approval.approval_id,
        decided_by="owner",
    )
    unknown = ops.refund(
        tenant_id=tenant.tenant_id,
        job_id=requested.job.job_id,
        actor="agent:etsy",
    )
    assert unknown.status == STATUS_UNKNOWN
    assert unknown.job.status == "unknown"
    assert len(ledger_rows(store, tenant.tenant_id, "etsy.refunds")) == 0
    with pytest.raises(BusinessError) as excinfo:
        ops.refund(
            tenant_id=tenant.tenant_id,
            job_id=requested.job.job_id,
            actor="agent:etsy",
        )
    assert excinfo.value.code == ETSY_RECONCILIATION_REQUIRED

    reconciled = ops.reconcile_unknown(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="owner",
        job_id=requested.job.job_id,
        resolution="refunded",
        provider_ref="etsy-refund-reconciliation:2026-11-14:1",
        amount_minor=200,
    )
    assert reconciled.job.status == "completed"
    assert len(ledger_rows(store, tenant.tenant_id, "etsy.refunds")) == 1
    assert len(ledger_rows(store, tenant.tenant_id, "etsy.receivable")) == 2


def test_paid_art_guard_and_no_inline_bytes(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    before = count_artifacts(store, ARTIFACT_ASSET)
    with pytest.raises(BusinessError) as excinfo:
        ops.reference_asset(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent:etsy",
            asset_ref="paid:limezu:1",
            version="v1",
            license="paid",
            license_source="owner purchase",
            paid=True,
            redistribution_allowed=False,
            inline={"content_base64": "AAAA"},
        )
    assert excinfo.value.code == ETSY_PAID_ART_GUARD
    with pytest.raises(BusinessError) as excinfo:
        ops.reference_asset(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent:etsy",
            asset_ref="paid:limezu:2",
            version="v1",
            license="paid",
            license_source="owner purchase",
            paid=True,
            redistribution_allowed=True,
        )
    assert excinfo.value.code == ETSY_PAID_ART_GUARD
    with pytest.raises(BusinessError) as excinfo:
        ops.reference_asset(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent:etsy",
            asset_ref="free:1",
            version="v1",
            license="mit",
            license_source="upstream",
            paid=False,
            redistribution_allowed=True,
            inline={"content": "raw-art"},
        )
    assert excinfo.value.code == ETSY_INLINE_ART_FORBIDDEN
    assert count_artifacts(store, ARTIFACT_ASSET) == before


def test_dry_run_adapter_cannot_fake_a_publish(store, scope):
    from voicebridge.business.etsy_operations import (
        EtsyOperations,
        InMemoryEtsySandboxTransport,
    )
    from voicebridge.integrations.etsy import EtsyAdapter

    tenant, business = scope
    ops, _ = build_ops(store)
    record_inputs(ops, tenant, business)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)

    dry_transport = InMemoryEtsySandboxTransport()
    dry_adapter = EtsyAdapter(
        shop_id="sandbox-shop",
        transport=dry_transport,
        credentials={"api_key": "k", "access_token": "t"},
        dry_run=True,
        rate_limit=1000,
    )
    dry_ops = EtsyOperations(store, adapter=dry_adapter)
    with pytest.raises(BusinessError) as excinfo:
        dry_ops.publish_listing(
            tenant_id=tenant.tenant_id,
            job_id=created.job.job_id,
            actor="agent:etsy",
        )
    assert excinfo.value.code == ETSY_PUBLISH_NOT_EXECUTED
    assert dry_transport.publish_calls == 0
    assert count_artifacts(store, ARTIFACT_PUBLISH_RECEIPT) == 0
    assert jobs.get(store, tenant.tenant_id, created.job.job_id).status == "failed"


def test_operations_without_an_adapter_refuse_provider_effects(store, scope):
    from voicebridge.business.etsy_operations import EtsyOperations

    tenant, business = scope
    ops = EtsyOperations(store)
    record_inputs(ops, tenant, business)
    with pytest.raises(BusinessError) as excinfo:
        create_listing(ops, tenant, business)
    assert excinfo.value.code == "etsy_adapter_unavailable"
    assert jobs.list_jobs(store, tenant.tenant_id) == []


def test_ledger_reconciliation_refuses_an_unbalanced_book(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)
    record_order(ops, tenant, business, created)
    payout = ops.record_payout(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
        provider_receipt_ref="etsy-payout-feed:2026-11-14:1",
        gross_minor=600,
        fee_minor=0,
    )
    assert payout.detail["net_minor"] == 600
    conn = __import__("sqlite3").connect(store.path)
    try:
        conn.execute(
            "INSERT INTO bos_ledger_entry(entry_id, tenant_id, business_id,"
            " job_id, account, direction, amount_minor, currency,"
            " idempotency_key, request_hash, memo, created_at)"
            " VALUES('lgr_extra',?,?,?,'etsy.cash','debit',999,'USD',"
            "'extra-unbalanced','hash','test','2026-11-14T00:00:00.000Z')",
            (tenant.tenant_id, business.business_id, created.job.job_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(BusinessError) as excinfo:
        ops.reconcile_ledger(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent:etsy",
            order_id="order-1",
            payout_ref="payout-1",
        )
    assert excinfo.value.code == "etsy_ledger_unbalanced"
    assert count_artifacts(store, ARTIFACT_LEDGER_RECONCILIATION) == 0


def test_events_are_ordered_and_reference_durable_receipts(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)
    record_order(ops, tenant, business, created)

    rows = event_rows(store, tenant.tenant_id)
    assert rows
    seqs = [int(row[0]) for row in rows]
    assert seqs == sorted(seqs)
    for _seq, _type, key, payload_hash, payload_json in rows:
        recomputed = hashlib.sha256(
            json.dumps(
                json.loads(payload_json),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert payload_hash == recomputed
        assert key

    replay = eventlog.read_events(store, tenant.tenant_id, business.business_id)
    published = [
        event for event in replay if event.type == "etsy.listing.published"
    ]
    assert len(published) == 1
    artifact = store.get_artifact(
        tenant.tenant_id, published[0].payload["receipt_artifact_id"]
    )
    assert artifact.kind == ARTIFACT_PUBLISH_RECEIPT

    approval_requested = [
        event
        for event in replay
        if event.type == "etsy.listing.approval_requested"
    ]
    intent_hash = approval_requested[0].payload["intent_hash"]
    approval = approvals.get(
        store,
        tenant.tenant_id,
        approval_requested[0].payload["approval_id"],
    )
    assert approval.request_hash == intent_hash
    record_order_event = [
        event for event in replay if event.type == "etsy.order.recorded"
    ]
    assert record_order_event[0].payload["ledger_entry_ids"]


def test_listing_requires_a_recorded_asset_and_niche(store, scope):
    ops, transport = build_ops(store)
    tenant, business = scope
    with pytest.raises(BusinessError) as excinfo:
        create_listing(ops, tenant, business)
    assert excinfo.value.code == "etsy_asset_required"
    record_inputs(ops, tenant, business, niche="other-niche")
    with pytest.raises(BusinessError) as excinfo:
        create_listing(ops, tenant, business)
    assert excinfo.value.code == "etsy_asset_required"


def test_transport_failure_stays_unknown_not_completed(store, scope):
    ops, transport = build_ops(store, fail_publish=True)
    tenant, business = scope
    record_inputs(ops, tenant, business)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)

    unknown = publish_listing(ops, tenant, created)
    assert unknown.status == STATUS_UNKNOWN
    assert unknown.job.status == "unknown"
    assert transport.publish_calls == 1
    receipt = artifact_by_name(
        store,
        tenant.tenant_id,
        f"{ARTIFACT_PUBLISH_RECEIPT}:{created.job.job_id}:unknown",
    )
    assert json.loads(receipt.body)["unknown"] is True
    assert count_artifacts(store, ARTIFACT_PUBLISH_RECEIPT) == 1

    reconciled = ops.reconcile_unknown(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="owner",
        job_id=created.job.job_id,
        resolution="not_executed",
        provider_ref="etsy-reconciliation:transport-failure:1",
    )
    assert reconciled.job.status == "failed"
    with pytest.raises(BusinessError) as excinfo:
        record_order(ops, tenant, business, created)
    assert excinfo.value.code == ETSY_NOT_PUBLISHED


def test_duplicate_payout_is_deduped(store, scope):
    ops, transport, tenant, business = _ready(store, scope)
    created = create_listing(ops, tenant, business)
    approve(ops, tenant, created)
    publish_listing(ops, tenant, created)
    record_order(ops, tenant, business, created)
    first = ops.record_payout(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
        provider_receipt_ref="etsy-payout-feed:2026-11-14:1",
        gross_minor=600,
        fee_minor=60,
    )
    before = ledger_rows(store, tenant.tenant_id)
    again = ops.record_payout(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
        provider_receipt_ref="etsy-payout-feed:2026-11-14:1",
        gross_minor=600,
        fee_minor=60,
    )
    after = ledger_rows(store, tenant.tenant_id)
    assert again.artifact.artifact_id == first.artifact.artifact_id
    assert before == after
    assert count_artifacts(store, ARTIFACT_PAYOUT) == 1
