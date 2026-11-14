"""Hermetic fixtures for the Phase 8 disaster-recovery tests.

Throwaway SQLite files under ``tmp_path``; no network, no live service and no
credentials. Every seed value is synthetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from voicebridge.business import (
    BusinessStore,
    approvals,
    durable,
    eventlog,
    jobs,
    ledger,
)

EXPIRED = 1000.0


@dataclass(frozen=True)
class Seed:
    store: BusinessStore
    tenant_id: str
    business_id: str
    completed_job: str
    running_job: str
    claimed_outbox: str


def seed_store(
    tmp_path,
    *,
    label: str = "Acme",
    slug: str | None = None,
    running: bool = True,
) -> Seed:
    slug = slug or label.lower()
    store = BusinessStore(tmp_path / f"{slug}.db")
    tenant = store.create_tenant(f"{label} Holdings")
    business = store.create_business(tenant.tenant_id, label, slug=slug)
    store.create_contact(
        tenant.tenant_id,
        business.business_id,
        kind="customer",
        display_name=f"{label} Customer",
        email=f"buyer@{slug}.test",
    )
    store.create_task(
        tenant.tenant_id, business.business_id, f"Reconcile {label} invoices"
    )

    completed, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="send-invoice",
        idempotency_key=f"{slug}-job-completed",
        input={"invoice": 7},
    )
    jobs.enqueue(store, tenant.tenant_id, completed.job_id)
    jobs.claim(
        store,
        tenant.tenant_id,
        completed.job_id,
        worker="worker-done",
        lease_seconds=60.0,
    )
    jobs.finish(
        store,
        tenant.tenant_id,
        completed.job_id,
        "completed",
        actor="worker-done",
        output={"receipt": "synthetic"},
    )

    running_job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="sync-crm",
        idempotency_key=f"{slug}-job-running",
        input={"account": "crm-1"},
    )
    jobs.enqueue(store, tenant.tenant_id, running_job.job_id)
    if running:
        jobs.claim(
            store,
            tenant.tenant_id,
            running_job.job_id,
            worker="worker-a",
            lease_seconds=10.0,
            now=EXPIRED,
        )

    approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="send-invoice",
        requested_by="owner",
        idempotency_key=f"{slug}-approval",
        payload={"invoice": 7},
        expires_at=10**12,
    )

    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="cash",
        direction="credit",
        amount_minor=1250,
        currency="USD",
        idempotency_key=f"{slug}-ledger-credit",
        job_id=completed.job_id,
    )
    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="fees",
        direction="debit",
        amount_minor=250,
        currency="USD",
        idempotency_key=f"{slug}-ledger-debit",
    )

    for sequence in (1, 2, 3):
        durable.record_checkpoint(
            store,
            tenant.tenant_id,
            business.business_id,
            f"{slug}-run-1",
            sequence,
            state={"step": sequence},
            workspace_revision=f"ws-{sequence}",
            policy_revision="pol-1",
            actor="tester",
        )

    pending = durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        f"{slug}-aggregate-pending",
        "email",
        {"to": "customer@example.test"},
        idempotency_key=f"{slug}-outbox-pending",
        actor="tester",
    )
    claimed = durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        f"{slug}-aggregate-claimed",
        "notify-crm",
        {"invoice": 7},
        idempotency_key=f"{slug}-outbox-claimed",
        actor="tester",
    )
    durable.claim_outbox(
        store,
        tenant.tenant_id,
        claimed.outbox_id,
        worker="worker-a",
        lease_seconds=10.0,
        now=EXPIRED,
    )
    assert pending.outbox_id != claimed.outbox_id

    eventlog.append_event(
        store,
        tenant.tenant_id,
        f"{slug}-aggregate-1",
        "invoice.sent",
        {"invoice": 7},
        actor="tester",
        idempotency_key=f"{slug}-event-1",
    )
    eventlog.append_event(
        store,
        tenant.tenant_id,
        f"{slug}-aggregate-1",
        "invoice.reconciled",
        {"invoice": 7},
        actor="tester",
        idempotency_key=f"{slug}-event-2",
    )

    return Seed(
        store=store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        completed_job=completed.job_id,
        running_job=running_job.job_id,
        claimed_outbox=claimed.outbox_id,
    )


def row_count(path, sql: str, params: tuple[Any, ...] = ()) -> int:
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()
