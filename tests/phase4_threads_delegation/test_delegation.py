"""P4-03 — bounded delegation, leases, worktrees and artifact versions.

Hermetic: a throwaway SQLite file under ``tmp_path``. These tests pin a bounded
delegation tree, an atomic one-winner claim, lease loss, no-progress/retry caps,
artifact-preserving handoff, single-writer worktrees and typed cap refusals.
No network, no service, no live database.
"""
from __future__ import annotations

import pytest

from voicebridge.business import (
    AgentControlService,
    BusinessStore,
    delegation,
    identity,
    runs,
    sessions,
)
from voicebridge.business.errors import BusinessError

OWNER = "owner"
A = "agent:a"
B = "agent:b"


def _fixture(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    identity.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref=OWNER,
        principal_id=OWNER)
    identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref=A,
        principal_id=A)
    identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref=B,
        principal_id=B)
    svc = AgentControlService(store)
    agent, _ = svc.create(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.analyst",
        department="Research",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key="agent-1",
        security_principal_id=A,
    )
    session = sessions.create_session(
        store,
        tenant.tenant_id,
        business.business_id,
        agent.agent_id,
        owner_principal=OWNER,
        actor="owner",
        task_title=None,
    )
    return store, tenant, business, session, agent


def _cap(**overrides):
    values = dict(
        max_depth=3, max_handoffs=3, max_attempts=3, max_wave_size=8,
        max_concurrency=4, no_progress_attempts=2, lease_ttl_seconds=300.0,
    )
    values.update(overrides)
    return delegation.DelegationCaps(**values)


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


def _claim(store, tenant, caps=None):
    claimed = delegation.claim_next(
        store, tenant.tenant_id, principal=A, caps=caps or _cap())
    assert claimed is not None
    return claimed


def test_bounded_delegation_tree_and_caps(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    root = delegation.delegate(
        store, tenant.tenant_id, business.business_id,
        title="Root", objective="coordinate", acceptance=["done"],
        budget={"usd": 1}, deadline="2030-01-01T00:00:00Z",
        required_capabilities=["cap:research"], data_class="internal",
        actor=OWNER,
    )
    assert root.depth == 0 and root.parent_task_id is None
    child = delegation.delegate(
        store, tenant.tenant_id, business.business_id,
        title="Child", parent_task_id=root.task.task_id, actor=OWNER,
        caps=_cap(max_depth=2),
    )
    assert child.depth == 1
    assert child.root_task_id == root.task.task_id
    assert delegation.delegation_info(
        store, tenant.tenant_id, child.task.task_id
    ).parent_task_id == root.task.task_id

    # depth ceiling: a grandchild under max_depth=1 is a typed refusal.
    with pytest.raises(BusinessError) as exc:
        delegation.delegate(
            store, tenant.tenant_id, business.business_id,
            title="Too deep", parent_task_id=child.task.task_id, actor=OWNER,
            caps=_cap(max_depth=1),
        )
    assert _code(exc) == delegation.DELEGATION_DEPTH_EXCEEDED_CODE
    assert runs.get_task(store, tenant.tenant_id, root.task.task_id).status == "draft"

    # wave ceiling: only one child per wave.
    with pytest.raises(BusinessError) as exc:
        delegation.delegate(
            store, tenant.tenant_id, business.business_id,
            title="Second sibling", parent_task_id=root.task.task_id,
            wave_id=child.wave_id, actor=OWNER,
            caps=_cap(max_wave_size=1),
        )
    assert _code(exc) == delegation.DELEGATION_WAVE_EXCEEDED_CODE


def test_claim_next_one_winner_and_concurrency_cap(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    first = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="One", actor=OWNER)
    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Two", actor=OWNER)

    claimed = delegation.claim_next(
        store, tenant.tenant_id, principal=A)
    assert claimed is not None
    assert claimed.task.task_id == first.task.task_id
    assert claimed.task.status == "claimed"
    assert claimed.lease.holder == A

    # A second claimer cannot claim the same task; it gets the other one.
    second = delegation.claim_next(
        store, tenant.tenant_id, principal=B)
    assert second is not None
    assert second.task.task_id != first.task.task_id

    # With one task left, a concurrent principal is refused by the cap.
    with pytest.raises(BusinessError) as exc:
        delegation.claim_next(
            store, tenant.tenant_id, principal=B,
            caps=_cap(max_concurrency=1))
    assert _code(exc) == delegation.DELEGATION_CONCURRENCY_EXCEEDED_CODE


def test_lease_expiry_stops_completion(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Leased", actor=OWNER)
    claimed = _claim(store, tenant, caps=_cap(lease_ttl_seconds=10))
    with pytest.raises(BusinessError) as exc:
        delegation.complete(
            store, tenant.tenant_id, claimed.task.task_id,
            principal=A, lease_id=claimed.lease.lease_id,
            fencing_token=claimed.lease.fencing_token,
            now=claimed.lease.expires_at + 1,
        )
    assert _code(exc) == runs.LEASE_LOST_CODE
    assert runs.get_task(
        store, tenant.tenant_id, claimed.task.task_id).status == "claimed"


def test_no_progress_detection_blocks(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Stuck", actor=OWNER)
    claimed = _claim(store, tenant, caps=_cap(no_progress_attempts=2))
    first = delegation.record_attempt(
        store, tenant.tenant_id, claimed.task.task_id, principal=A,
        caps=_cap(no_progress_attempts=2))
    assert first.code is None and first.no_progress_streak == 1
    second = delegation.record_attempt(
        store, tenant.tenant_id, claimed.task.task_id, principal=A,
        caps=_cap(no_progress_attempts=2))
    assert second.code == delegation.DELEGATION_NO_PROGRESS_CODE
    assert second.blocked is True
    assert second.task.status == "blocked"

    # Progress clears the streak: a changed fingerprint does not block.
    other = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Moving", actor=OWNER)
    moving = delegation.claim_next(store, tenant.tenant_id, principal=A)
    assert moving is not None and moving.task.task_id == other.task.task_id
    delegation.record_attempt(
        store, tenant.tenant_id, other.task.task_id, principal=A,
        progress={"step": 1}, caps=_cap(no_progress_attempts=2))
    result = delegation.record_attempt(
        store, tenant.tenant_id, other.task.task_id, principal=A,
        progress={"step": 2}, caps=_cap(no_progress_attempts=2))
    assert result.code is None and result.progress_changed is True


def test_retry_cap_blocks(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Retry", actor=OWNER)
    claimed = _claim(store, tenant, caps=_cap(max_attempts=2, no_progress_attempts=2))
    caps = _cap(max_attempts=2, no_progress_attempts=2)
    first = delegation.record_attempt(
        store, tenant.tenant_id, claimed.task.task_id, principal=A,
        progress={"n": 1}, caps=caps)
    second = delegation.record_attempt(
        store, tenant.tenant_id, claimed.task.task_id, principal=A,
        progress={"n": 2}, caps=caps)
    third = delegation.record_attempt(
        store, tenant.tenant_id, claimed.task.task_id, principal=A,
        progress={"n": 3}, caps=caps)
    assert first.code is None and second.code is None
    assert third.code == delegation.DELEGATION_ATTEMPTS_EXCEEDED_CODE
    assert third.blocked is True


def test_single_writer_worktree_and_immutable_artifacts(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Coding", actor=OWNER)
    claimed = _claim(store, tenant)
    workspace, created = delegation.bind_worktree(
        store, tenant.tenant_id, claimed.task.task_id,
        session_id=session.session_id,
        repository_ref="repo://acme/app", worktree_ref="wt/one",
        actor=OWNER,
    )
    assert created is True and workspace.current_revision == "1"
    _, created_again = delegation.bind_worktree(
        store, tenant.tenant_id, claimed.task.task_id,
        session_id=session.session_id,
        repository_ref="repo://acme/app", worktree_ref="wt/one",
        actor=OWNER,
    )
    assert created_again is False

    first = delegation.write_artifact(
        store, tenant.tenant_id, claimed.task.task_id,
        principal=A, lease_id=claimed.lease.lease_id,
        fencing_token=claimed.lease.fencing_token,
        name="report.md", kind="document", body="v1",
    )
    assert first.created is True and first.version == 1
    assert first.artifact.sha256
    duplicate = delegation.write_artifact(
        store, tenant.tenant_id, claimed.task.task_id,
        principal=A, lease_id=claimed.lease.lease_id,
        fencing_token=claimed.lease.fencing_token,
        name="report.md", kind="document", body="v1",
    )
    assert duplicate.created is False
    assert duplicate.artifact.artifact_id == first.artifact.artifact_id
    second = delegation.write_artifact(
        store, tenant.tenant_id, claimed.task.task_id,
        principal=A, lease_id=claimed.lease.lease_id,
        fencing_token=claimed.lease.fencing_token,
        name="report.md", kind="document", body="v2",
    )
    assert second.created is True and second.version == 2
    assert second.artifact.artifact_id != first.artifact.artifact_id
    # The old immutable version is still readable.
    assert store.get_artifact(
        tenant.tenant_id, first.artifact.artifact_id).body == "v1"

    # A different principal cannot write the same task/workspace.
    with pytest.raises(BusinessError) as exc:
        delegation.write_artifact(
            store, tenant.tenant_id, claimed.task.task_id,
            principal=B, lease_id=claimed.lease.lease_id,
            fencing_token=claimed.lease.fencing_token,
            name="other.md", kind="document", body="nope",
        )
    assert _code(exc) == delegation.DELEGATION_SINGLE_OWNER_CODE

    with pytest.raises(BusinessError) as exc:
        delegation.bind_worktree(
            store, tenant.tenant_id, claimed.task.task_id,
            session_id=session.session_id,
            repository_ref="repo://acme/app", worktree_ref="wt/two",
            actor=OWNER,
        )
    assert _code(exc) == delegation.DELEGATION_WORKTREE_CONFLICT_CODE


def test_handoff_preserves_artifacts_and_caps(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Hand off", actor=OWNER)
    claimed = _claim(store, tenant)
    delegation.bind_worktree(
        store, tenant.tenant_id, claimed.task.task_id,
        session_id=session.session_id,
        repository_ref="repo://acme/app", worktree_ref="wt/handoff",
        actor=OWNER,
    )
    written = delegation.write_artifact(
        store, tenant.tenant_id, claimed.task.task_id,
        principal=A, lease_id=claimed.lease.lease_id,
        fencing_token=claimed.lease.fencing_token,
        name="notes.md", kind="document", body="handoff notes",
    )
    record = delegation.handoff(
        store, tenant.tenant_id, session.session_id, claimed.task.task_id,
        from_principal=A, to_principal=B, reason="shift end",
        artifact_ids=[written.artifact.artifact_id],
        caps=_cap(max_handoffs=2), actor=A,
    )
    assert record.artifact_ids == (written.artifact.artifact_id,)
    accepted = delegation.accept_handoff(
        store, tenant.tenant_id, record.handoff_id, principal=B)
    assert accepted.status == "accepted"
    assert accepted.artifact_ids == (written.artifact.artifact_id,)
    task = runs.get_task(store, tenant.tenant_id, claimed.task.task_id)
    assert task.status == "claimed"
    assert task.assignee_principal == B
    events = [
        event for event in __import__(
            "voicebridge.business.eventlog", fromlist=["read_events"]
        ).read_events(store, tenant.tenant_id, claimed.task.task_id)
        if event.type == delegation.DELEGATION_HANDOFF_ACCEPTED
    ]
    assert events and events[-1].payload["artifact_ids"] == [
        written.artifact.artifact_id]

    # A third handoff proposal exceeds the ceiling with max_handoffs=2.
    # The task is now owned by B, so propose from B to A.
    delegation.handoff(
        store, tenant.tenant_id, session.session_id, claimed.task.task_id,
        from_principal=B, to_principal=A, reason="second",
        caps=_cap(max_handoffs=2), actor=B,
    )
    with pytest.raises(BusinessError) as exc:
        delegation.handoff(
            store, tenant.tenant_id, session.session_id, claimed.task.task_id,
            from_principal=A, to_principal=B, reason="third",
            caps=_cap(max_handoffs=2), actor=A,
        )
    assert _code(exc) == delegation.DELEGATION_HANDOFF_EXCEEDED_CODE


def test_delegate_idempotency_and_completion_no_duplicate(tmp_path):
    store, tenant, business, session, _ = _fixture(tmp_path)
    first = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Idem",
        idempotency_key="delegate-1", actor=OWNER,
    )
    again = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Idem",
        idempotency_key="delegate-1", actor=OWNER,
    )
    assert again.created is False
    assert again.task.task_id == first.task.task_id
    assert len(runs.list_tasks(
        store, tenant.tenant_id, business_id=business.business_id)) == 1

    claimed = _claim(store, tenant)
    done = delegation.complete(
        store, tenant.tenant_id, claimed.task.task_id,
        principal=A, lease_id=claimed.lease.lease_id,
        fencing_token=claimed.lease.fencing_token, summary="ok",
    )
    assert done.created is True and done.task.status == "completed"
    repeat = delegation.complete(
        store, tenant.tenant_id, claimed.task.task_id,
        principal=A, lease_id=claimed.lease.lease_id,
        fencing_token=claimed.lease.fencing_token, summary="ok",
    )
    assert repeat.created is False
    assert len([
        event for event in __import__(
            "voicebridge.business.eventlog", fromlist=["read_events"]
        ).read_events(store, tenant.tenant_id, claimed.task.task_id)
        if event.type == delegation.DELEGATION_COMPLETED
    ]) == 1
