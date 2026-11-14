"""Transactional outbox: one effect, one row, one winner.

Hermetic: a throwaway SQLite file under ``tmp_path``; no network, no service.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore
from voicebridge.business import durable
from voicebridge.business.errors import BusinessError


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _enqueue(store, tenant, business, *, key="key-1", payload=None, kind="email"):
    return durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        "aggregate-1",
        kind,
        {"to": "customer@example.test"} if payload is None else payload,
        idempotency_key=key,
        actor="tester",
    )


def test_same_idempotency_key_is_one_row_and_same_hash_replays(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    first = _enqueue(store, tenant, business)
    second = _enqueue(store, tenant, business)
    assert first.outbox_id == second.outbox_id
    assert first.request_hash == second.request_hash
    with store.read() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM bos_outbox WHERE tenant_id=?", (tenant.tenant_id,)
        ).fetchone()[0]
    assert count == 1


def test_same_key_with_different_payload_is_a_typed_conflict(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _enqueue(store, tenant, business, payload={"to": "a@example.test"})
    with pytest.raises(BusinessError) as refused:
        _enqueue(store, tenant, business, payload={"to": "b@example.test"})
    assert refused.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"


def test_claim_has_exactly_one_winner(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _enqueue(store, tenant, business)

    claimed = durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="worker-a",
        lease_seconds=30, now=1000.0,
    )
    assert claimed.status == "claimed"
    assert claimed.lease_owner == "worker-a"
    assert claimed.attempts == 1

    with pytest.raises(BusinessError) as refused:
        durable.claim_outbox(
            store, tenant.tenant_id, message.outbox_id, worker="worker-b",
            lease_seconds=30, now=1001.0,
        )
    assert refused.value.code == "BUSINESS_OUTBOX_BAD_STATE"


def test_retry_expires_to_unknown_then_reconciles_sent(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _enqueue(store, tenant, business)

    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="worker-a",
        lease_seconds=10, now=1000.0,
    )
    # Lease expired at 1010; the restart sweep parks it as unknown.
    recovered = durable.recover_outbox(store, now=1011.0)
    assert [row.outbox_id for row in recovered] == [message.outbox_id]
    parked = recovered[0]
    assert parked.status == "unknown"
    assert parked.attempts == 1
    assert parked.lease_owner is None
    assert "reconcile" in (parked.last_error or "")

    # Unknown is not claimable: that would be a blind retry of a possible effect.
    with pytest.raises(BusinessError) as refused:
        durable.claim_outbox(
            store, tenant.tenant_id, message.outbox_id, worker="worker-b",
            lease_seconds=30, now=1012.0,
        )
    assert refused.value.code == "BUSINESS_OUTBOX_BAD_STATE"

    reconciled = durable.reconcile_outbox(
        store,
        tenant.tenant_id,
        message.outbox_id,
        actor="reconciler",
        outcome="sent",
        note="provider shows the send at 10:00",
        provider_receipt={"provider_id": "prov-123", "status": "delivered"},
    )
    assert reconciled.status == "sent"
    assert reconciled.provider_receipt["provider_id"] == "prov-123"
    assert reconciled.sent_at is not None


def test_unknown_reconciles_dead_without_a_receipt(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _enqueue(store, tenant, business)
    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="w", lease_seconds=1,
        now=1.0,
    )
    durable.recover_outbox(store, now=99.0)
    dead = durable.reconcile_outbox(
        store,
        tenant.tenant_id,
        message.outbox_id,
        actor="reconciler",
        outcome="dead",
        note="provider confirms nothing was sent",
    )
    assert dead.status == "dead"
    assert dead.provider_receipt is None


def test_reconcile_to_sent_without_a_receipt_is_refused(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _enqueue(store, tenant, business)
    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="w", lease_seconds=1,
        now=1.0,
    )
    durable.recover_outbox(store, now=99.0)
    with pytest.raises(BusinessError) as refused:
        durable.reconcile_outbox(
            store, tenant.tenant_id, message.outbox_id, actor="reconciler",
            outcome="sent", note="trust me",
        )
    assert refused.value.code == "BUSINESS_BAD_INPUT"
    # The row is still unknown; a refusal never silently completes it.
    with store.read() as conn:
        status = conn.execute(
            "SELECT status FROM bos_outbox WHERE outbox_id=?",
            (message.outbox_id,),
        ).fetchone()[0]
    assert status == "unknown"


def test_sent_is_terminal_and_cannot_be_reclaimed_or_reconciled(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _enqueue(store, tenant, business)
    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="w", lease_seconds=30,
        now=100.0,
    )
    durable.mark_outbox_sent(
        store, tenant.tenant_id, message.outbox_id, worker="w",
        provider_receipt={"id": "r"}, now=101.0,
    )
    with pytest.raises(BusinessError) as claimed:
        durable.claim_outbox(
            store, tenant.tenant_id, message.outbox_id, worker="w2",
            lease_seconds=30, now=102.0,
        )
    assert claimed.value.code == "BUSINESS_OUTBOX_BAD_STATE"
    with pytest.raises(BusinessError) as reconciled:
        durable.reconcile_outbox(
            store, tenant.tenant_id, message.outbox_id, actor="x",
            outcome="dead", note="too late",
        )
    assert reconciled.value.code == "BUSINESS_OUTBOX_BAD_STATE"


def test_attempts_track_each_claim(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _enqueue(store, tenant, business)
    first = durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="w", lease_seconds=1,
        now=10.0,
    )
    durable.mark_outbox_unknown(
        store, tenant.tenant_id, message.outbox_id, worker="w",
        note="provider timed out", now=10.5,
    )
    # Explicit reconciliation back to pending is not offered; a fresh message
    # is how a new attempt is queued. The original attempt count is durable.
    assert first.attempts == 1
    parked = durable.get_outbox(store, tenant.tenant_id, message.outbox_id)
    assert parked.status == "unknown"
    assert parked.attempts == 1
