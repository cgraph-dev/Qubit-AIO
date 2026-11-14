"""P1-04 — durable task/workspace/lease records and single-writer fencing.

Hermetic: a throwaway SQLite file under ``tmp_path``; no network, no service.
There is no second executor: jobs remain the run state machine.
"""
from __future__ import annotations

import threading

import pytest

from voicebridge.business import BusinessStore, eventlog, identity, jobs, runs
from voicebridge.business.errors import BusinessError


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _agent(store: BusinessStore, tenant_id: str) -> str:
    return identity.create_principal(store, tenant_id, kind="agent").principal_id


def _task(store, tenant, business, *, owner=None, **overrides):
    title = overrides.pop("title", "Draft the quarterly report")
    values = {
        "objective": "produce a verified artifact",
        "data_class": "internal",
        "actor": "owner",
    }
    if owner is not None:
        values["owner_principal"] = owner
    values.update(overrides)
    return runs.create_task(
        store,
        tenant.tenant_id,
        business.business_id,
        title,
        **values,
    )


def _workspace(store, tenant, task):
    return runs.create_workspace(
        store,
        tenant.tenant_id,
        task_id=task.task_id,
        session_id="ses_00000000000000000000000000",
        repository_ref="repo://acme/report",
        worktree_ref="wt/report",
        actor="owner",
    )


def _event_types(store, tenant, aggregate_id):
    return [
        event.event_type if hasattr(event, "event_type") else event.type
        for event in eventlog.read_events(store, tenant.tenant_id, aggregate_id)
    ]


# -- task movement ---------------------------------------------------------
def test_task_movement_is_append_only_events(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    agent = _agent(store, tenant.tenant_id)
    task = _task(store, tenant, business, owner=agent)

    assert task.status == "draft"
    assert task.legacy_status == "open"
    assert _event_types(store, tenant, task.task_id) == ["task.created"]

    claimed = runs.claim_task(
        store, tenant.tenant_id, task.task_id, principal=agent, actor=agent
    )
    assert claimed.status == "claimed"
    assert claimed.assignee_principal == agent
    assert _event_types(store, tenant, task.task_id) == [
        "task.created",
        "task.claimed",
    ]

    done = runs.transition_task(
        store,
        tenant.tenant_id,
        task.task_id,
        "completed",
        actor=agent,
        note="artifact attached",
    )
    assert done.status == "completed"
    assert done.legacy_status == "done"
    events = eventlog.read_events(store, tenant.tenant_id, task.task_id)
    assert [event.type for event in events] == [
        "task.created",
        "task.claimed",
        "task.transitioned",
    ]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[-1].payload["from"] == "claimed"
    assert events[-1].payload["to"] == "completed"

    # A no-op transition is idempotent and does not append a second event.
    again = runs.transition_task(
        store, tenant.tenant_id, task.task_id, "completed", actor=agent
    )
    assert again.status == "completed"
    assert len(eventlog.read_events(store, tenant.tenant_id, task.task_id)) == 3

    # Terminal tasks cannot move.
    with pytest.raises(BusinessError) as refused:
        runs.transition_task(
            store, tenant.tenant_id, task.task_id, "claimed", actor=agent
        )
    assert refused.value.code == "BUSINESS_BAD_STATE"


def test_claim_has_exactly_one_winner(tmp_path):
    path = tmp_path / "business.db"
    store = BusinessStore(path)
    tenant, business = _scope(store)
    first = _agent(store, tenant.tenant_id)
    second = _agent(store, tenant.tenant_id)
    task = _task(store, tenant, business)

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str]] = []
    lock = threading.Lock()

    def claim(principal: str) -> None:
        contender = BusinessStore(path)
        barrier.wait()
        try:
            runs.claim_task(
                contender, tenant.tenant_id, task.task_id, principal=principal
            )
            result = ("ok", principal)
        except BusinessError as exc:
            result = ("refused", exc.code)
        with lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=claim, args=(first,)),
        threading.Thread(target=claim, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [outcome for outcome in outcomes if outcome[0] == "ok"]
    losers = [outcome for outcome in outcomes if outcome[0] == "refused"]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0][1] == "BUSINESS_BAD_STATE"

    claimed = runs.get_task(store, tenant.tenant_id, task.task_id)
    assert claimed.status == "claimed"
    assert claimed.assignee_principal == winners[0][1]
    assert _event_types(store, tenant, task.task_id) == [
        "task.created",
        "task.claimed",
    ]


def test_claim_requires_completed_dependencies(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    agent = _agent(store, tenant.tenant_id)
    dependency = _task(store, tenant, business, title="dependency")
    dependent = _task(
        store,
        tenant,
        business,
        title="dependent",
        dependencies=[dependency.task_id],
    )

    with pytest.raises(BusinessError) as refused:
        runs.claim_task(
            store, tenant.tenant_id, dependent.task_id, principal=agent
        )
    assert refused.value.code == runs.TASK_DEPENDENCIES_INCOMPLETE_CODE


def test_handoff_requires_recipient_acceptance_and_source_is_read_only(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    source = _agent(store, tenant.tenant_id)
    recipient = _agent(store, tenant.tenant_id)
    task = _task(store, tenant, business, owner=source)
    runs.claim_task(store, tenant.tenant_id, task.task_id, principal=source)

    handed = runs.handoff_task(
        store,
        tenant.tenant_id,
        task.task_id,
        to_principal=recipient,
        actor=source,
        reason="recipient owns the data",
    )
    assert handed.status == "handed_off"
    assert handed.read_only is True
    assert handed.handed_off_from == source
    assert handed.handed_off_to == recipient
    assert handed.assignee_principal == recipient

    # The source is read-only immediately.
    with pytest.raises(BusinessError) as read_only:
        runs.transition_task(
            store, tenant.tenant_id, task.task_id, "completed", actor=source
        )
    assert read_only.value.code == runs.TASK_READ_ONLY_CODE

    # Only the named recipient can accept.
    with pytest.raises(BusinessError) as wrong_recipient:
        runs.accept_handoff(
            store, tenant.tenant_id, task.task_id, principal=source
        )
    assert wrong_recipient.value.code == runs.HANDOFF_RECIPIENT_MISMATCH_CODE

    accepted = runs.accept_handoff(
        store, tenant.tenant_id, task.task_id, principal=recipient
    )
    assert accepted.status == "claimed"
    assert accepted.read_only is False
    assert accepted.handed_off_from == source

    # The source stays read-only after acceptance; the recipient can finish.
    with pytest.raises(BusinessError) as still_read_only:
        runs.transition_task(
            store, tenant.tenant_id, task.task_id, "completed", actor=source
        )
    assert still_read_only.value.code == runs.TASK_READ_ONLY_CODE
    completed = runs.transition_task(
        store, tenant.tenant_id, task.task_id, "completed", actor=recipient
    )
    assert completed.status == "completed"

    events = eventlog.read_events(store, tenant.tenant_id, task.task_id)
    assert [event.type for event in events] == [
        "task.created",
        "task.claimed",
        "task.handed_off",
        "task.handoff_accepted",
        "task.transitioned",
    ]


# -- workspaces and leases -------------------------------------------------
def test_workspace_revision_is_compare_and_swap(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    task = _task(store, tenant, business)
    workspace = _workspace(store, tenant, task)
    assert workspace.current_revision == "1"

    lease = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder="writer-1",
        ttl_seconds=100,
        now=100.0,
    )
    revised = runs.revise_workspace(
        store,
        tenant.tenant_id,
        workspace.workspace_id,
        expected_revision="1",
        new_revision="2",
        holder="writer-1",
        fencing_token=lease.fencing_token,
        artifact_manifest=[{"artifact_id": "art_1", "sha256": "a" * 64}],
        now=101.0,
    )
    assert revised.current_revision == "2"
    assert revised.artifact_manifest[0]["artifact_id"] == "art_1"

    with pytest.raises(BusinessError) as conflict:
        runs.revise_workspace(
            store,
            tenant.tenant_id,
            workspace.workspace_id,
            expected_revision="1",
            new_revision="3",
            holder="writer-1",
            fencing_token=lease.fencing_token,
            now=102.0,
        )
    assert conflict.value.code == runs.WORKSPACE_REVISION_CONFLICT_CODE


def test_second_writer_is_refused_while_the_lease_is_held(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    task = _task(store, tenant, business)
    workspace = _workspace(store, tenant, task)

    lease = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder="writer-1",
        ttl_seconds=100,
        now=100.0,
    )

    with pytest.raises(BusinessError) as conflict:
        runs.acquire_lease(
            store,
            tenant.tenant_id,
            "workspace",
            workspace.workspace_id,
            holder="writer-2",
            ttl_seconds=100,
            now=101.0,
        )
    assert conflict.value.code == runs.LEASE_CONFLICT_CODE

    # A different holder cannot use the token either.
    with pytest.raises(BusinessError) as stolen:
        runs.revise_workspace(
            store,
            tenant.tenant_id,
            workspace.workspace_id,
            expected_revision="1",
            new_revision="2",
            holder="writer-2",
            fencing_token=lease.fencing_token,
            now=102.0,
        )
    assert stolen.value.code == runs.LEASE_LOST_CODE


def test_lease_expiry_and_stale_fencing_token_stop_work(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    task = _task(store, tenant, business)
    workspace = _workspace(store, tenant, task)

    first = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder="writer-1",
        ttl_seconds=10,
        now=100.0,
    )
    assert first.fencing_token == "1"
    assert runs.assert_fence(
        store,
        tenant.tenant_id,
        first.lease_id,
        holder="writer-1",
        fencing_token=first.fencing_token,
        now=105.0,
    ).lease_id == first.lease_id

    with pytest.raises(BusinessError) as expired:
        runs.assert_fence(
            store,
            tenant.tenant_id,
            first.lease_id,
            holder="writer-1",
            fencing_token=first.fencing_token,
            now=111.0,
        )
    assert expired.value.code == runs.LEASE_LOST_CODE

    # An expired lease is releaseable by a new acquirer with a new fence.
    second = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder="writer-2",
        ttl_seconds=100,
        now=112.0,
    )
    assert second.fencing_token == "2"

    with pytest.raises(BusinessError) as stale:
        runs.assert_fence(
            store,
            tenant.tenant_id,
            second.lease_id,
            holder="writer-2",
            fencing_token="1",
            now=113.0,
        )
    assert stale.value.code == runs.LEASE_LOST_CODE

    # The old lease id is released; it cannot be used after the re-acquire.
    with pytest.raises(BusinessError) as released:
        runs.assert_fence(
            store,
            tenant.tenant_id,
            first.lease_id,
            holder="writer-1",
            fencing_token="1",
            now=113.0,
        )
    assert released.value.code == runs.LEASE_LOST_CODE


def test_workspace_write_after_handoff_is_refused_for_the_source(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    source = _agent(store, tenant.tenant_id)
    recipient = _agent(store, tenant.tenant_id)
    task = _task(store, tenant, business, owner=source)
    runs.claim_task(store, tenant.tenant_id, task.task_id, principal=source)
    workspace = _workspace(store, tenant, task)
    lease = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder=source,
        ttl_seconds=100,
        now=100.0,
    )
    runs.handoff_task(
        store,
        tenant.tenant_id,
        task.task_id,
        to_principal=recipient,
        actor=source,
    )

    with pytest.raises(BusinessError) as read_only:
        runs.revise_workspace(
            store,
            tenant.tenant_id,
            workspace.workspace_id,
            expected_revision="1",
            new_revision="2",
            holder=source,
            fencing_token=lease.fencing_token,
            now=101.0,
        )
    assert read_only.value.code == runs.TASK_READ_ONLY_CODE


# -- run projection over jobs ---------------------------------------------
def test_crash_after_effect_becomes_unknown_and_retry_is_not_duplicate(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    job, created = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="fake.external.write",
        idempotency_key="job-key-1",
        input={"to": "customer@example.test"},
    )
    assert created is True
    jobs.enqueue(store, tenant.tenant_id, job.job_id)
    jobs.claim(
        store, tenant.tenant_id, job.job_id, worker="worker-1",
        lease_seconds=10, now=100.0,
    )

    recovered = jobs.recover(store, now=200.0)
    assert [item.job_id for item in recovered] == [job.job_id]
    assert recovered[0].status == "unknown"
    assert runs.run_status_for_job(recovered[0]) == "unknown"

    # A retry with the same idempotency key returns the same job, not a copy.
    again, created_again = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="fake.external.write",
        idempotency_key="job-key-1",
        input={"to": "customer@example.test"},
    )
    assert created_again is False
    assert again.job_id == job.job_id

    view = runs.run_view(store, tenant.tenant_id, job.job_id)
    assert view["status"] == "unknown"
    assert view["job_status"] == "unknown"


def test_run_transition_refuses_terminal_reversal():
    assert runs.assert_run_transition("executing", "completed") == "completed"
    assert runs.assert_run_transition("queued", "leased") == "leased"
    with pytest.raises(BusinessError) as refused:
        runs.assert_run_transition("completed", "executing")
    assert refused.value.code == runs.RUN_ILLEGAL_TRANSITION_CODE
    with pytest.raises(BusinessError):
        runs.assert_run_transition("unknown", "executing")


# -- restart and revocation ------------------------------------------------
def test_restart_preserves_tasks_workspaces_and_leases(tmp_path):
    path = tmp_path / "business.db"
    store = BusinessStore(path)
    tenant, business = _scope(store)
    agent = _agent(store, tenant.tenant_id)
    task = _task(store, tenant, business)
    runs.claim_task(store, tenant.tenant_id, task.task_id, principal=agent)
    workspace = _workspace(store, tenant, task)
    lease = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder=agent,
        ttl_seconds=100,
        now=100.0,
    )

    reopened = BusinessStore(path)
    assert runs.get_task(reopened, tenant.tenant_id, task.task_id).status == "claimed"
    assert (
        runs.get_workspace(reopened, tenant.tenant_id, workspace.workspace_id)
        .current_revision
        == "1"
    )
    restored = runs.get_lease(reopened, tenant.tenant_id, lease.lease_id)
    assert restored.holder == agent
    assert restored.fencing_token == lease.fencing_token

    revised = runs.revise_workspace(
        reopened,
        tenant.tenant_id,
        workspace.workspace_id,
        expected_revision="1",
        new_revision="2",
        holder=agent,
        fencing_token=lease.fencing_token,
        now=105.0,
    )
    assert revised.current_revision == "2"


def test_revoked_principal_cannot_claim_and_lost_lease_stops_writes(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    agent = _agent(store, tenant.tenant_id)
    task = _task(store, tenant, business)
    claimed = runs.claim_task(
        store, tenant.tenant_id, task.task_id, principal=agent
    )
    workspace = _workspace(store, tenant, claimed)
    lease = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder=agent,
        ttl_seconds=100,
        now=100.0,
    )

    identity.revoke_principal(store, tenant.tenant_id, agent, actor="owner")

    # Permission is gone immediately.
    assert identity.evaluate(
        store,
        tenant.tenant_id,
        agent,
        "files.read",
        "workspace:alpha",
        "read",
    ).allowed is False

    other = _task(store, tenant, business, title="second")
    with pytest.raises(BusinessError) as refused:
        runs.claim_task(
            store, tenant.tenant_id, other.task_id, principal=agent
        )
    assert refused.value.code == "BUSINESS_BAD_STATE"

    # Releasing the lease stops further writes even for the original holder.
    runs.release_lease(
        store,
        tenant.tenant_id,
        lease.lease_id,
        holder=agent,
        fencing_token=lease.fencing_token,
    )
    with pytest.raises(BusinessError) as lost:
        runs.revise_workspace(
            store,
            tenant.tenant_id,
            workspace.workspace_id,
            expected_revision="1",
            new_revision="2",
            holder=agent,
            fencing_token=lease.fencing_token,
        )
    assert lost.value.code == runs.LEASE_LOST_CODE
