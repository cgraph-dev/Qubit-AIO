"""Lease ownership: a lost, wrong or expired lease cannot record an effect.

Hermetic: a throwaway SQLite file under ``tmp_path``; no network, no service.
"""
from __future__ import annotations

import pytest

from voicebridge.business import BusinessStore
from voicebridge.business import durable
from voicebridge.business.errors import BusinessError


def _scope(store: BusinessStore):
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    return tenant, business


def _pending(store, tenant, business, *, key="key-1"):
    return durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        "aggregate-1",
        "email",
        {"to": "customer@example.test"},
        idempotency_key=key,
        actor="tester",
    )


def test_heartbeat_from_a_wrong_worker_is_refused(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _pending(store, tenant, business)
    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="owner-worker",
        lease_seconds=60, now=100.0,
    )
    with pytest.raises(BusinessError) as refused:
        durable.heartbeat_outbox(
            store, tenant.tenant_id, message.outbox_id, worker="other-worker",
            lease_seconds=60, now=101.0,
        )
    assert refused.value.code == "BUSINESS_OUTBOX_LEASE_LOST"

    extended = durable.heartbeat_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="owner-worker",
        lease_seconds=60, now=101.0,
    )
    assert extended.lease_expires_at == pytest.approx(161.0)


def test_expired_lease_cannot_record_sent_or_unknown(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _pending(store, tenant, business)
    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="w",
        lease_seconds=10, now=100.0,
    )
    with pytest.raises(BusinessError) as sent:
        durable.mark_outbox_sent(
            store, tenant.tenant_id, message.outbox_id, worker="w",
            provider_receipt={"id": "late"}, now=111.0,
        )
    assert sent.value.code == "BUSINESS_OUTBOX_LEASE_LOST"
    with pytest.raises(BusinessError) as unknown:
        durable.mark_outbox_unknown(
            store, tenant.tenant_id, message.outbox_id, worker="w",
            note="late", now=111.0,
        )
    assert unknown.value.code == "BUSINESS_OUTBOX_LEASE_LOST"

    # The row is still claimed: a refused mark mutated nothing.
    still = durable.get_outbox(store, tenant.tenant_id, message.outbox_id)
    assert still.status == "claimed"


def test_mark_sent_from_a_wrong_worker_is_refused(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _pending(store, tenant, business)
    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="owner",
        lease_seconds=60, now=100.0,
    )
    with pytest.raises(BusinessError) as refused:
        durable.mark_outbox_sent(
            store, tenant.tenant_id, message.outbox_id, worker="intruder",
            provider_receipt={"id": "r"}, now=101.0,
        )
    assert refused.value.code == "BUSINESS_OUTBOX_LEASE_LOST"


def test_mark_dead_is_terminal(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    message = _pending(store, tenant, business)
    durable.claim_outbox(
        store, tenant.tenant_id, message.outbox_id, worker="w",
        lease_seconds=60, now=100.0,
    )
    dead = durable.mark_outbox_dead(
        store, tenant.tenant_id, message.outbox_id, worker="w",
        reason="provider permanently rejected", now=101.0,
    )
    assert dead.status == "dead"
    assert dead.lease_owner is None
    with pytest.raises(BusinessError) as refused:
        durable.claim_outbox(
            store, tenant.tenant_id, message.outbox_id, worker="w2",
            lease_seconds=60, now=102.0,
        )
    assert refused.value.code == "BUSINESS_OUTBOX_BAD_STATE"


def test_recovery_leaves_a_live_lease_untouched(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    live = _pending(store, tenant, business, key="live")
    expired = _pending(store, tenant, business, key="expired")
    durable.claim_outbox(
        store, tenant.tenant_id, live.outbox_id, worker="live-worker",
        lease_seconds=600, now=100.0,
    )
    durable.claim_outbox(
        store, tenant.tenant_id, expired.outbox_id, worker="dead-worker",
        lease_seconds=10, now=100.0,
    )
    recovered = durable.recover_outbox(store, now=200.0)
    assert [row.outbox_id for row in recovered] == [expired.outbox_id]
    assert durable.get_outbox(store, tenant.tenant_id, live.outbox_id).status == (
        "claimed"
    )
    assert durable.get_outbox(store, tenant.tenant_id, expired.outbox_id).status == (
        "unknown"
    )
