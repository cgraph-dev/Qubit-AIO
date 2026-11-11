"""Business OS control-plane foundation.

Hermetic: a throwaway SQLite file under ``tmp_path``. No network, no live
service, no external payment/email/CRM side effect, no secret on disk.

These tests pin the contract the master plan requires before any real
integration is allowed: idempotency is one effect, a restart leaves an
``unknown`` outcome instead of a blind retry, an approval pauses and resumes a
job, the ledger is append-only, tenants cannot see each other, and no state
machine can be driven backwards or decided twice.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from voicebridge.business import (
    BusinessStore,
    Policy,
    ToolGateway,
    approvals,
    jobs,
    ledger,
)
from voicebridge.business.errors import BusinessError


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _scalar(store: BusinessStore, sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(store.path)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def _count(store: BusinessStore, table: str) -> int:
    return _scalar(store, f"SELECT COUNT(*) FROM {table}")


# --- typed objects and tenant scope -------------------------------------
def test_typed_objects_persist_with_stable_ids_and_scoped_lookups(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    assert tenant.tenant_id.startswith("ten_")
    assert business.business_id.startswith("biz_")
    assert business.status == "draft"
    assert business.currency == "USD"

    contact = store.create_contact(
        tenant.tenant_id,
        business.business_id,
        kind="customer",
        display_name="Ada Lovelace",
        email="Ada@Example.com",
        external_ref="crm-1",
    )
    assert contact.contact_id.startswith("con_")
    assert contact.email == "ada@example.com"

    # The external ref is the dedupe key: the same customer is one row.
    again = store.create_contact(
        tenant.tenant_id,
        business.business_id,
        kind="customer",
        display_name="Ada L.",
        external_ref="crm-1",
    )
    assert again.contact_id == contact.contact_id
    assert _count(store, "bos_contact") == 1

    task = store.create_task(
        tenant.tenant_id, business.business_id, "send welcome email"
    )
    assert task.task_id.startswith("task_")
    assert task.status == "open"

    artifact = store.record_artifact(
        tenant.tenant_id,
        business.business_id,
        kind="invoice",
        name="invoice-001.txt",
        body="total: 12.00 USD\n",
    )
    assert artifact.artifact_id.startswith("art_")
    assert artifact.size_bytes == len("total: 12.00 USD\n")
    assert len(artifact.sha256) == 64

    other = store.create_tenant("Someone Else Ltd")
    with pytest.raises(BusinessError) as exc:
        store.get_business(other.tenant_id, business.business_id)
    assert exc.value.code == "BUSINESS_NOT_FOUND"
    with pytest.raises(BusinessError):
        store.get_contact(other.tenant_id, contact.contact_id)
    with pytest.raises(BusinessError):
        store.get_artifact(other.tenant_id, artifact.artifact_id)


def test_wal_is_enabled(tmp_path):
    store = _store(tmp_path)
    conn = store.connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


# --- jobs: idempotency, restart, unknown, state machine -----------------
def test_duplicate_job_idempotency_is_one_effect(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    args = dict(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="invoice.send",
        idempotency_key="invoice-42-send",
        input={"invoice": 42, "amount_minor": 1200},
    )

    first, created_first = jobs.create(store, **args)
    second, created_second = jobs.create(store, **args)

    assert created_first is True
    assert created_second is False
    assert second.job_id == first.job_id
    assert _count(store, "bos_job") == 1
    created_events = [
        event for event in jobs.list_events(
            store, tenant.tenant_id, first.job_id
        ) if event.kind == "job.created"
    ]
    assert len(created_events) == 1
    assert len(store.list_audit(tenant.tenant_id, action="job.create")) == 1

    with pytest.raises(BusinessError) as exc:
        jobs.create(
            store,
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            kind="invoice.send",
            idempotency_key="invoice-42-send",
            input={"invoice": 42, "amount_minor": 9999},
        )
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"
    assert _count(store, "bos_job") == 1


def test_restart_marks_expired_running_job_unknown_then_resumes(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="provider.charge",
        idempotency_key="charge-1",
        input={"amount_minor": 500},
    )
    jobs.enqueue(store, tenant.tenant_id, job.job_id)
    jobs.claim(
        store,
        tenant.tenant_id,
        job.job_id,
        worker="worker-a",
        lease_seconds=60.0,
        now=1000.0,
    )

    # A restart while the lease is still valid must NOT steal the job.
    assert jobs.recover(store, now=1010.0) == []
    assert jobs.get(store, tenant.tenant_id, job.job_id).status == "running"

    # A fresh store instance over the same file is the restart.
    restarted = BusinessStore(store.path)
    recovered = jobs.recover(restarted, now=2000.0)
    assert [item.job_id for item in recovered] == [job.job_id]
    unknown = jobs.get(restarted, tenant.tenant_id, job.job_id)
    assert unknown.status == "unknown"
    assert unknown.last_error_code == "JOB_OUTCOME_UNKNOWN"
    assert unknown.lease_owner is None
    assert [item.job_id for item in jobs.list_jobs(
        restarted, tenant.tenant_id, status="unknown"
    )] == [job.job_id]
    assert "job.unknown" in [
        event.kind for event in jobs.list_events(
            restarted, tenant.tenant_id, job.job_id
        )
    ]
    sweep = restarted.list_audit(tenant.tenant_id, action="recovery.sweep")
    assert len(sweep) == 1
    assert sweep[0].detail["unknown"] == [job.job_id]

    # A human reconciles ("provider says nothing was charged"), then resumes.
    jobs.requeue_unknown(
        restarted,
        tenant.tenant_id,
        job.job_id,
        actor="owner",
        note="provider confirms no charge; safe to retry",
    )
    jobs.claim(
        restarted,
        tenant.tenant_id,
        job.job_id,
        worker="worker-b",
        lease_seconds=60.0,
    )
    done = jobs.finish(
        restarted,
        tenant.tenant_id,
        job.job_id,
        "completed",
        actor="worker-b",
        output={"provider_id": "ch_1"},
    )
    assert done.status == "completed"
    assert done.attempt == 2


def test_job_state_machine_rejects_unsafe_and_duplicate_transitions(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="artifact.build",
        idempotency_key="build-1",
    )
    with pytest.raises(BusinessError) as exc:
        jobs.finish(store, tenant.tenant_id, job.job_id, "completed", actor="x")
    assert exc.value.code == "BUSINESS_BAD_STATE"

    jobs.enqueue(store, tenant.tenant_id, job.job_id)
    jobs.claim(store, tenant.tenant_id, job.job_id, worker="w", lease_seconds=60)
    with pytest.raises(BusinessError) as exc:
        jobs.claim(store, tenant.tenant_id, job.job_id, worker="w2", lease_seconds=60)
    assert exc.value.code == "BUSINESS_BAD_STATE"

    jobs.finish(store, tenant.tenant_id, job.job_id, "completed", actor="w")
    with pytest.raises(BusinessError):
        jobs.claim(store, tenant.tenant_id, job.job_id, worker="w3", lease_seconds=60)
    with pytest.raises(BusinessError):
        jobs.finish(store, tenant.tenant_id, job.job_id, "failed", actor="w")


# --- approvals: pause, resume, reject, expire ---------------------------
def test_approval_pauses_and_approval_resumes_job(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="payout.send",
        idempotency_key="payout-1",
    )
    jobs.enqueue(store, tenant.tenant_id, job.job_id)
    jobs.claim(store, tenant.tenant_id, job.job_id, worker="w", lease_seconds=60)

    approval, created = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="tool:money.payout",
        requested_by="agent:treasurer",
        idempotency_key="payout-approval-1",
        job_id=job.job_id,
        payload={"amount_minor": 500, "to": "vendor-1"},
    )
    assert created is True
    assert approval.status == "pending"
    paused = jobs.get(store, tenant.tenant_id, job.job_id)
    assert paused.status == "waiting_approval"
    assert paused.lease_owner is None

    # Replaying the approval request is the same pending gate, not a second one.
    replay, created_again = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="tool:money.payout",
        requested_by="agent:treasurer",
        idempotency_key="payout-approval-1",
        job_id=job.job_id,
        payload={"amount_minor": 500, "to": "vendor-1"},
    )
    assert created_again is False
    assert replay.approval_id == approval.approval_id
    waiting_events = [
        event for event in jobs.list_events(
            store, tenant.tenant_id, job.job_id
        ) if event.kind == "job.waiting_approval"
    ]
    assert len(waiting_events) == 1

    with pytest.raises(BusinessError):
        jobs.claim(store, tenant.tenant_id, job.job_id, worker="w", lease_seconds=60)

    decided = approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
        reason="invoice verified",
    )
    assert decided.status == "approved"
    resumed = jobs.get(store, tenant.tenant_id, job.job_id)
    assert resumed.status == "queued"
    jobs.claim(store, tenant.tenant_id, job.job_id, worker="w", lease_seconds=60)
    assert jobs.finish(
        store, tenant.tenant_id, job.job_id, "completed", actor="w"
    ).status == "completed"

    with pytest.raises(BusinessError) as exc:
        approvals.decide(
            store,
            tenant_id=tenant.tenant_id,
            approval_id=approval.approval_id,
            decision="rejected",
            decided_by="owner",
        )
    assert exc.value.code == "BUSINESS_APPROVAL_NOT_PENDING"


def test_rejected_or_expired_approval_cancels_job(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    def _waiting(key: str):
        job, _ = jobs.create(
            store,
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            kind="email.send",
            idempotency_key=key,
        )
        jobs.enqueue(store, tenant.tenant_id, job.job_id)
        jobs.claim(store, tenant.tenant_id, job.job_id, worker="w", lease_seconds=60)
        approval, _ = approvals.request(
            store,
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            action="tool:email.send",
            requested_by="agent:sales",
            idempotency_key=f"approval-{key}",
            job_id=job.job_id,
            payload={"to": "customer@example.com"},
        )
        return job, approval

    rejected_job, rejected = _waiting("mail-1")
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=rejected.approval_id,
        decision="rejected",
        decided_by="owner",
        reason="wrong customer",
    )
    assert jobs.get(
        store, tenant.tenant_id, rejected_job.job_id
    ).status == "cancelled"

    expiring_job, expiring = _waiting("mail-2")
    with store.transaction() as conn:
        conn.execute(
            "UPDATE bos_approval SET expires_at=? WHERE approval_id=?",
            (time.time() - 5, expiring.approval_id),
        )
    expired = approvals.expire(store)
    assert [item.approval_id for item in expired] == [expiring.approval_id]
    assert expired[0].status == "expired"
    assert jobs.get(
        store, tenant.tenant_id, expiring_job.job_id
    ).status == "cancelled"


# --- ledger: append-only and idempotent --------------------------------
def test_ledger_is_append_only_and_idempotent(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    args = dict(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="sales",
        direction="credit",
        amount_minor=1200,
        currency="USD",
        idempotency_key="sale-1",
        memo="first sale",
    )
    first, created = ledger.append(store, **args)
    replay, created_again = ledger.append(store, **args)
    assert created is True
    assert created_again is False
    assert replay.entry_id == first.entry_id
    assert _count(store, "bos_ledger_entry") == 1

    totals = ledger.summary(store, tenant.tenant_id, business_id=business.business_id)
    assert totals == {
        "tenant_id": tenant.tenant_id,
        "business_id": business.business_id,
        "currency": None,
        "debit_minor": 0,
        "credit_minor": 1200,
        "net_minor": 1200,
        "entry_count": 1,
    }

    conn = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE bos_ledger_entry SET amount_minor=1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM bos_ledger_entry")
    finally:
        conn.close()

    with pytest.raises(BusinessError) as exc:
        ledger.append(
            store,
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            account="sales",
            direction="credit",
            amount_minor=9999,
            currency="USD",
            idempotency_key="sale-1",
        )
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"
    assert _count(store, "bos_ledger_entry") == 1
    ledger.append_only_check(store)


# --- tenant isolation ---------------------------------------------------
def test_tenant_isolation_across_every_object(tmp_path):
    store = _store(tmp_path)
    t1, b1 = _scope(store, "Acme", "acme")
    t2, b2 = _scope(store, "Beta", "beta")
    job, _ = jobs.create(
        store,
        tenant_id=t1.tenant_id,
        business_id=b1.business_id,
        kind="build",
        idempotency_key="j1",
    )
    ledger.append(
        store,
        tenant_id=t1.tenant_id,
        business_id=b1.business_id,
        account="cash",
        direction="debit",
        amount_minor=100,
        currency="USD",
        idempotency_key="l1",
    )
    approval, _ = approvals.request(
        store,
        tenant_id=t1.tenant_id,
        business_id=b1.business_id,
        action="tool:email.send",
        requested_by="agent",
        idempotency_key="a1",
    )
    artifact = store.record_artifact(
        t1.tenant_id, b1.business_id, kind="note", name="n", body="x"
    )

    assert jobs.list_jobs(store, t2.tenant_id) == []
    assert ledger.list_entries(store, t2.tenant_id) == []
    assert store.list_artifacts(t2.tenant_id) == []
    assert store.list_contacts(t2.tenant_id) == []
    assert store.list_audit(t2.tenant_id, action="ledger.append") == []
    assert approvals.list_approvals(store, t2.tenant_id) == []
    lookups = (
        lambda ident: jobs.get(store, t2.tenant_id, ident),
        lambda ident: approvals.get(store, t2.tenant_id, ident),
        lambda ident: store.get_artifact(t2.tenant_id, ident),
    )
    for lookup, ident in zip(
        lookups,
        (job.job_id, approval.approval_id, artifact.artifact_id),
        strict=True,
    ):
        with pytest.raises(BusinessError) as exc:
            lookup(ident)
        assert exc.value.code == "BUSINESS_NOT_FOUND"

    # A business from tenant 1 cannot be operated through tenant 2.
    gateway = ToolGateway(store)
    with pytest.raises(BusinessError) as exc:
        gateway.invoke(
            tenant_id=t2.tenant_id,
            business_id=b1.business_id,
            actor="agent:2",
            tool="email.send",
            idempotency_key="cross-1",
            policy=Policy(allowed_tools=frozenset({"email.send"})),
            dry_run=True,
        )
    assert exc.value.code == "BUSINESS_NOT_FOUND"
    assert jobs.get(store, t1.tenant_id, job.job_id).status == "created"


# --- tool gateway -------------------------------------------------------
def test_tool_gateway_validates_dry_runs_and_redacts_secrets(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    calls = []

    def executor(request):
        calls.append(request)
        return {"ok": True, "value": request.payload["value"]}

    gateway = ToolGateway(store, executor)
    policy = Policy(allowed_tools=frozenset({"email.send"}), autonomy="L2")
    payload = {
        "value": 42,
        "api_key": "sk-live-ABCDEF123456",
        "note": "Authorization: Bearer tok_987654",
    }
    outcome = gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:sales",
        tool="email.send",
        idempotency_key="mail-1",
        policy=policy,
        payload=payload,
    )
    assert outcome.executed is True
    assert outcome.replayed is False
    assert len(calls) == 1
    stored = outcome.invocation
    assert stored.status == "success"
    assert stored.request["api_key"] == "***REDACTED***"
    assert stored.request["note"] == "Authorization: Bearer ***REDACTED***"
    assert stored.result == {"ok": True, "value": 42}
    assert _count(store, "bos_tool_invocation") == 1
    assert len(store.list_audit(tenant.tenant_id, action="tool.invoke")) == 1
    assert len(store.list_audit(tenant.tenant_id, action="tool.result")) == 1

    replay = gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:sales",
        tool="email.send",
        idempotency_key="mail-1",
        policy=policy,
        payload=payload,
    )
    assert replay.replayed is True
    assert replay.executed is False
    assert replay.invocation.invocation_id == stored.invocation_id
    assert len(calls) == 1
    assert _count(store, "bos_tool_invocation") == 1

    # Dry-run validates and records intent without executing.
    dry = gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:sales",
        tool="email.send",
        idempotency_key="mail-dry",
        policy=policy,
        payload={"value": 1},
        dry_run=True,
    )
    assert dry.executed is False
    assert dry.invocation.status == "dry_run"
    assert dry.invocation.result["dry_run"] is True
    assert len(calls) == 1


def test_tool_gateway_refuses_disallowed_approval_budget_and_missing_executor(
    tmp_path,
):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    def executor(_request):
        return {"ok": True}

    gateway = ToolGateway(store, executor)
    disallowed = Policy(allowed_tools=frozenset({"other.tool"}))
    with pytest.raises(BusinessError) as exc:
        gateway.invoke(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent",
            tool="email.send",
            idempotency_key="d1",
            policy=disallowed,
        )
    assert exc.value.code == "BUSINESS_TOOL_NOT_ALLOWED"

    high = Policy(
        allowed_tools=frozenset({"money.payout"}), autonomy="L4"
    )
    with pytest.raises(BusinessError) as exc:
        gateway.invoke(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent",
            tool="money.payout",
            idempotency_key="d2",
            policy=high,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"

    approval, _ = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="tool:money.payout",
        requested_by="agent",
        idempotency_key="payout-approval",
    )
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
    )
    paid = gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent",
        tool="money.payout",
        idempotency_key="d3",
        policy=high,
        approval_id=approval.approval_id,
        cost_minor=10,
    )
    assert paid.executed is True

    period = time.strftime("%Y-%m", time.gmtime())
    store.set_budget(
        tenant.tenant_id,
        business.business_id,
        currency="USD",
        period=period,
        limit_minor=10,
    )
    with pytest.raises(BusinessError) as exc:
        gateway.invoke(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent",
            tool="money.payout",
            idempotency_key="d4",
            policy=high,
            approval_id=approval.approval_id,
            cost_minor=1,
        )
    assert exc.value.code == "BUSINESS_BUDGET_EXCEEDED"

    bare = ToolGateway(store)
    with pytest.raises(BusinessError) as exc:
        bare.invoke(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent",
            tool="email.send",
            idempotency_key="d5",
            policy=Policy(allowed_tools=frozenset({"email.send"})),
        )
    assert exc.value.code == "BUSINESS_TOOL_EXECUTOR_MISSING"
    preview = bare.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent",
        tool="email.send",
        idempotency_key="d6",
        policy=Policy(allowed_tools=frozenset({"email.send"})),
        dry_run=True,
    )
    assert preview.invocation.status == "dry_run"


def test_tool_gateway_records_executor_failure_without_leaking(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    def failing(_request):
        raise RuntimeError("provider said no for token sk-live-SECRET123")

    gateway = ToolGateway(store, failing)
    with pytest.raises(BusinessError) as exc:
        gateway.invoke(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent",
            tool="email.send",
            idempotency_key="fail-1",
            policy=Policy(allowed_tools=frozenset({"email.send"})),
        )
    assert exc.value.code == "BUSINESS_TOOL_FAILED"
    failures = gateway.list_invocations(tenant.tenant_id)
    assert len(failures) == 1
    assert failures[0].status == "failed"
    error_events = store.list_audit(tenant.tenant_id, action="tool.result")
    assert error_events[0].outcome == "error"
    blob = str([event.detail for event in error_events])
    assert "sk-live-SECRET123" not in blob

    # Same key after the failure replays the recorded failure; it never
    # silently re-executes a possibly side-effecting call.
    replay = gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent",
        tool="email.send",
        idempotency_key="fail-1",
        policy=Policy(allowed_tools=frozenset({"email.send"})),
    )
    assert replay.replayed is True
    assert replay.invocation.status == "failed"


def test_store_from_config_resolves_the_business_state_dir(tmp_path):
    from types import SimpleNamespace

    cfg = SimpleNamespace(state_dir=lambda: tmp_path / "state")
    store = BusinessStore.from_config(cfg)
    assert store.path == tmp_path / "state" / "business" / "business.db"
    # Opening it creates the parent tree and migrates the schema.
    tenant = store.create_tenant("Config Co")
    assert store.get_tenant(tenant.tenant_id).name == "Config Co"
