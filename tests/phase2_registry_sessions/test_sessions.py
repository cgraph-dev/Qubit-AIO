"""P2-02 — durable sessions, workspaces, checkpoints and handoffs.

Hermetic: a throwaway SQLite file under ``tmp_path``. These tests pin the
work-loss rules the Office relies on: a session is durable, its workspace is
task-pinned and single-writer, a checkpoint is the recovery point, and a
handoff transfers ownership only when the explicit recipient accepts.
"""
from __future__ import annotations

import pytest

from voicebridge.business import (
    AgentControlService,
    BusinessStore,
    eventlog,
    identity,
    runs,
    sessions,
)
from voicebridge.business.errors import BusinessError


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _principal(store, tenant_id, ref, kind="agent"):
    return identity.create_principal(
        store, tenant_id, kind=kind, external_ref=ref
    )


def _agent(store, tenant, business, principal, *, key="agent-1"):
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
        idempotency_key=key,
        security_principal_id=principal.principal_id,
    )
    return svc, agent


def _fixture(tmp_path, *, with_task=True):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    owner = _principal(store, tenant.tenant_id, "owner", kind="owner")
    source = _principal(store, tenant.tenant_id, "agent-1")
    recipient = _principal(store, tenant.tenant_id, "agent-2")
    svc, agent = _agent(store, tenant, business, source)
    session = sessions.create_session(
        store,
        tenant.tenant_id,
        business.business_id,
        agent.agent_id,
        owner_principal=owner.principal_id,
        actor="owner",
        task_title="Draft the report" if with_task else None,
    )
    return store, tenant, business, svc, agent, owner, source, recipient, session


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


def _events(store, tenant, aggregate):
    return [item.type for item in eventlog.read_events(store, tenant.tenant_id, aggregate)]


# -- creation and lifecycle ------------------------------------------------
def test_create_session_mints_task_and_pinned_workspace(tmp_path):
    store, tenant, business, _svc, agent, owner, _source, _recipient, session = (
        _fixture(tmp_path)
    )

    assert session.status == "active"
    assert session.agent_id == agent.agent_id
    assert session.owner_principal == owner.principal_id
    assert session.default_workspace_id
    assert sessions.get_session(store, tenant.tenant_id, session.session_id) == session

    workspace = runs.get_workspace(store, tenant.tenant_id, session.default_workspace_id)
    assert workspace.session_id == session.session_id
    task = runs.get_task(store, tenant.tenant_id, workspace.task_id)
    assert task.task_id == workspace.task_id
    assert task.business_id == business.business_id
    assert _events(store, tenant, f"session:{session.session_id}") == [
        "session.created"
    ]
    assert [item.session_id for item in sessions.list_sessions(store, tenant.tenant_id)] == [
        session.session_id
    ]


def test_pause_resume_archive_and_transitions(tmp_path):
    store, tenant, *_rest = _fixture(tmp_path)
    session = _rest[-1]

    paused = sessions.set_session_status(
        store, tenant.tenant_id, session.session_id, "paused", actor="owner"
    )
    assert paused.status == "paused"
    resumed = sessions.set_session_status(
        store, tenant.tenant_id, session.session_id, "active", actor="owner"
    )
    assert resumed.status == "active"
    archived = sessions.set_session_status(
        store, tenant.tenant_id, session.session_id, "archived", actor="owner"
    )
    assert archived.status == "archived"
    assert archived.archived_at

    with pytest.raises(BusinessError) as exc:
        sessions.set_session_status(
            store, tenant.tenant_id, session.session_id, "active", actor="owner"
        )
    assert _code(exc) == "BUSINESS_SESSION_BAD_STATE"
    assert _events(store, tenant, f"session:{session.session_id}") == [
        "session.created",
        "session.paused",
        "session.resumed",
        "session.archived",
    ]


def test_bind_workspace_requires_one_session_and_a_task_pin(tmp_path):
    store, tenant, business, _svc, agent, owner, _source, _recipient, _session = (
        _fixture(tmp_path, with_task=False)
    )
    session = sessions.create_session(
        store,
        tenant.tenant_id,
        business.business_id,
        agent.agent_id,
        owner_principal=owner.principal_id,
        actor="owner",
    )
    task = runs.create_task(
        store, tenant.tenant_id, business.business_id, "Bound work", actor="owner"
    )
    workspace = runs.create_workspace(
        store,
        tenant.tenant_id,
        task_id=task.task_id,
        session_id=session.session_id,
        actor="owner",
    )

    bound = sessions.bind_workspace(
        store,
        tenant.tenant_id,
        session.session_id,
        workspace.workspace_id,
        actor="owner",
    )
    assert bound.default_workspace_id == workspace.workspace_id
    assert _events(store, tenant, f"session:{session.session_id}")[-1] == (
        "session.workspace_bound"
    )

    other = sessions.create_session(
        store,
        tenant.tenant_id,
        business.business_id,
        agent.agent_id,
        owner_principal=owner.principal_id,
        actor="owner",
    )
    with pytest.raises(BusinessError) as exc:
        sessions.bind_workspace(
            store,
            tenant.tenant_id,
            other.session_id,
            workspace.workspace_id,
            actor="owner",
        )
    assert _code(exc) == "BUSINESS_SESSION_WORKSPACE_MISMATCH"


def test_task_pinned_workspace_has_one_writer(tmp_path):
    store, tenant, _business, _svc, _agent_obj, _owner, source, _recipient, session = (
        _fixture(tmp_path)
    )
    workspace = runs.get_workspace(store, tenant.tenant_id, session.default_workspace_id)
    lease = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder=source.principal_id,
        ttl_seconds=300,
    )
    revised = runs.revise_workspace(
        store,
        tenant.tenant_id,
        workspace.workspace_id,
        expected_revision="1",
        new_revision="2",
        holder=source.principal_id,
        fencing_token=lease.fencing_token,
    )
    assert revised.current_revision == "2"

    # A stale fencing token and a second writer are both refused.
    with pytest.raises(BusinessError) as exc:
        runs.revise_workspace(
            store,
            tenant.tenant_id,
            workspace.workspace_id,
            expected_revision="2",
            new_revision="3",
            holder="agent-2",
            fencing_token="999",
        )
    assert _code(exc) in {"BUSINESS_LEASE_CONFLICT", "BUSINESS_LEASE_LOST"}


# -- checkpoints -----------------------------------------------------------
def test_record_checkpoint_and_last_checkpoint(tmp_path):
    store, tenant, *_rest = _fixture(tmp_path)
    session = _rest[-1]

    first = sessions.record_checkpoint(
        store,
        tenant.tenant_id,
        session.session_id,
        run_id="run-1",
        sequence=1,
        state={"step": 1},
        actor="owner",
    )
    second = sessions.record_checkpoint(
        store,
        tenant.tenant_id,
        session.session_id,
        run_id="run-1",
        sequence=2,
        state={"step": 2},
        actor="owner",
    )
    assert session.last_checkpoint_id is None
    current = sessions.last_checkpoint(store, tenant.tenant_id, session.session_id)
    assert current.checkpoint_id == second.checkpoint_id
    assert current.sequence == 2
    stored = sessions.get_session(store, tenant.tenant_id, session.session_id)
    assert stored.last_checkpoint_id == second.checkpoint_id
    assert first.checkpoint_id != second.checkpoint_id

    # A reused (run, sequence) slot is never overwritten.
    with pytest.raises(BusinessError) as exc:
        sessions.record_checkpoint(
            store,
            tenant.tenant_id,
            session.session_id,
            run_id="run-1",
            sequence=2,
            state={"step": 3},
            actor="owner",
        )
    assert _code(exc) == "BUSINESS_CHECKPOINT_EXISTS"
    assert _events(store, tenant, f"session:{session.session_id}") == [
        "session.created",
        "session.checkpointed",
        "session.checkpointed",
    ]


def test_recover_reports_lost_lease_without_claiming_work(tmp_path):
    store, tenant, _business, _svc, _agent_obj, _owner, source, _recipient, session = (
        _fixture(tmp_path)
    )
    workspace = runs.get_workspace(store, tenant.tenant_id, session.default_workspace_id)
    task_id = workspace.task_id
    runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder=source.principal_id,
        ttl_seconds=10,
        now=1000.0,
    )

    lost = sessions.recover_sessions(store, tenant.tenant_id, now=2000.0)
    report = lost[0]
    assert report["session_id"] == session.session_id
    assert report["status"] == "active"
    assert report["lease_held"] is False
    assert report["lease_lost"] is True
    assert report["may_claim_work"] is False
    # Recovery never claims execution work on a lost lease.
    assert runs.get_task(store, tenant.tenant_id, task_id).status == "draft"

    held = runs.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        workspace.workspace_id,
        holder=source.principal_id,
        ttl_seconds=300,
        now=2000.0,
    )
    report = sessions.recover_sessions(store, tenant.tenant_id, now=2001.0)[0]
    assert report["lease_held"] is True
    assert report["lease_lost"] is False
    assert report["active_lease_ids"] == [held.lease_id]


# -- handoff ---------------------------------------------------------------
def test_handoff_propose_accept_transfers_and_freezes_the_source(tmp_path):
    store, tenant, business, _svc, _agent_obj, _owner, source, recipient, session = (
        _fixture(tmp_path)
    )
    workspace = runs.get_workspace(store, tenant.tenant_id, session.default_workspace_id)
    artifact = store.record_artifact(
        tenant.tenant_id,
        business.business_id,
        kind="report",
        name="draft.md",
        body="draft",
    )
    checkpoint = sessions.record_checkpoint(
        store,
        tenant.tenant_id,
        session.session_id,
        run_id="run-1",
        sequence=1,
        state={"step": 1},
        actor="owner",
    )

    handoff = sessions.propose_handoff(
        store,
        tenant.tenant_id,
        session.session_id,
        to_principal=recipient.principal_id,
        actor="owner",
        reason="needs a second pair of eyes",
        checkpoint_id=checkpoint.checkpoint_id,
        artifact_ids=[artifact.artifact_id],
        unresolved_questions=["which KPI wins?"],
        required_capabilities=["crm.read"],
        acceptance_deadline=9_999_999_999,
    )
    assert handoff.status == "proposed"
    assert handoff.checkpoint_id == checkpoint.checkpoint_id
    assert handoff.artifact_ids == (artifact.artifact_id,)
    assert handoff.unresolved_questions == ("which KPI wins?",)
    assert handoff.from_principal == source.principal_id
    # Proposing does not move the task yet.
    assert runs.get_task(store, tenant.tenant_id, workspace.task_id).assignee_principal == (
        source.principal_id
    )

    accepted = sessions.accept_handoff(
        store,
        tenant.tenant_id,
        handoff.handoff_id,
        principal=recipient.principal_id,
    )
    assert accepted.status == "accepted"
    assert accepted.accepted_at
    task = runs.get_task(store, tenant.tenant_id, workspace.task_id)
    assert task.assignee_principal == recipient.principal_id
    assert task.handed_off_from == source.principal_id

    # The source principal is now read-only for that task.
    with pytest.raises(BusinessError) as exc:
        runs.transition_task(
            store, tenant.tenant_id, task.task_id, "completed", actor=source.principal_id
        )
    assert _code(exc) == "BUSINESS_TASK_READ_ONLY"

    # Duplicate acceptance is refused.
    with pytest.raises(BusinessError) as exc:
        sessions.accept_handoff(
            store,
            tenant.tenant_id,
            handoff.handoff_id,
            principal=recipient.principal_id,
        )
    assert _code(exc) == "BUSINESS_HANDOFF_BAD_STATE"
    assert _events(store, tenant, f"handoff:{handoff.handoff_id}") == [
        "handoff.proposed",
        "handoff.accepted",
    ]


def test_handoff_reject_leaves_ownership_untouched(tmp_path):
    store, tenant, _business, _svc, _agent_obj, _owner, source, recipient, session = (
        _fixture(tmp_path)
    )
    workspace = runs.get_workspace(store, tenant.tenant_id, session.default_workspace_id)
    handoff = sessions.propose_handoff(
        store,
        tenant.tenant_id,
        session.session_id,
        to_principal=recipient.principal_id,
        actor="owner",
    )
    rejected = sessions.reject_handoff(
        store,
        tenant.tenant_id,
        handoff.handoff_id,
        principal=recipient.principal_id,
        reason="no capacity",
    )
    assert rejected.status == "rejected"
    task = runs.get_task(store, tenant.tenant_id, workspace.task_id)
    assert task.assignee_principal == source.principal_id
    assert task.handed_off_from is None
    assert _events(store, tenant, f"handoff:{handoff.handoff_id}") == [
        "handoff.proposed",
        "handoff.rejected",
    ]


def test_duplicate_and_stale_handoffs_are_refused(tmp_path):
    store, tenant, _business, _svc, _agent_obj, _owner, _source, recipient, session = (
        _fixture(tmp_path)
    )
    first = sessions.propose_handoff(
        store,
        tenant.tenant_id,
        session.session_id,
        to_principal=recipient.principal_id,
        actor="owner",
    )
    with pytest.raises(BusinessError) as exc:
        sessions.propose_handoff(
            store,
            tenant.tenant_id,
            session.session_id,
            to_principal=recipient.principal_id,
            actor="owner",
        )
    assert _code(exc) == "BUSINESS_HANDOFF_EXISTS"

    with pytest.raises(BusinessError) as exc:
        sessions.propose_handoff(
            store,
            tenant.tenant_id,
            session.session_id,
            to_principal=recipient.principal_id,
            actor="owner",
            acceptance_deadline=100,
            now=200,
        )
    assert _code(exc) == "BUSINESS_HANDOFF_EXPIRED"

    sessions.reject_handoff(
        store,
        tenant.tenant_id,
        first.handoff_id,
        principal=recipient.principal_id,
    )
    stale = sessions.propose_handoff(
        store,
        tenant.tenant_id,
        session.session_id,
        to_principal=recipient.principal_id,
        actor="owner",
        acceptance_deadline=1010,
        now=1000,
    )
    with pytest.raises(BusinessError) as exc:
        sessions.accept_handoff(
            store,
            tenant.tenant_id,
            stale.handoff_id,
            principal=recipient.principal_id,
            now=2000,
        )
    assert _code(exc) == "BUSINESS_HANDOFF_EXPIRED"
    assert sessions.get_handoff(store, tenant.tenant_id, stale.handoff_id).status == (
        "proposed"
    )


def test_tenant_isolation_for_sessions_and_handoffs(tmp_path):
    store, tenant, _business, _svc, _agent_obj, _owner, _source, recipient, session = (
        _fixture(tmp_path)
    )
    other_tenant, other_business = _scope(store, "Other", "other")
    other_owner = _principal(store, other_tenant.tenant_id, "owner", kind="owner")
    other_principal = _principal(store, other_tenant.tenant_id, "agent-x")
    _other_svc, other_agent = _agent(
        store, other_tenant, other_business, other_principal, key="other-agent"
    )
    other_session = sessions.create_session(
        store,
        other_tenant.tenant_id,
        other_business.business_id,
        other_agent.agent_id,
        owner_principal=other_owner.principal_id,
        actor="owner",
        task_title="Other work",
    )

    with pytest.raises(BusinessError) as exc:
        sessions.get_session(store, other_tenant.tenant_id, session.session_id)
    assert _code(exc) == "BUSINESS_SESSION_NOT_FOUND"
    assert sessions.list_sessions(store, other_tenant.tenant_id) == [other_session]

    handoff = sessions.propose_handoff(
        store,
        tenant.tenant_id,
        session.session_id,
        to_principal=recipient.principal_id,
        actor="owner",
    )
    with pytest.raises(BusinessError) as exc:
        sessions.get_handoff(store, other_tenant.tenant_id, handoff.handoff_id)
    assert _code(exc) == "BUSINESS_HANDOFF_NOT_FOUND"
    assert sessions.list_handoffs(store, other_tenant.tenant_id) == []

    second = sessions.create_session(
        store,
        tenant.tenant_id,
        _business.business_id,
        _agent_obj.agent_id,
        owner_principal=_owner.principal_id,
        actor="owner",
        task_title="Second work",
    )
    with pytest.raises(BusinessError) as exc:
        sessions.propose_handoff(
            store,
            tenant.tenant_id,
            second.session_id,
            to_principal=other_principal.principal_id,
            actor="owner",
        )
    assert _code(exc) == "BUSINESS_NOT_FOUND"
