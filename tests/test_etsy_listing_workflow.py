"""Etsy digital-listing pilot workflow: draft -> approval -> dry-run publish."""
from __future__ import annotations

import time

import pytest

from voicebridge.business import BusinessStore, approvals, jobs
from voicebridge.business.errors import BusinessError
from voicebridge.business.etsy_workflow import (
    APPROVAL_ACTION_PUBLISH,
    EtsyDigitalListingWorkflow,
    STAGE_APPROVAL_EXPIRED,
    STAGE_APPROVED,
    STAGE_AWAITING_APPROVAL,
    STAGE_PUBLISHED,
    STAGE_REJECTED,
    WORKFLOW_DRY_RUN_ONLY_CODE,
    WORKFLOW_KIND,
)

LISTING = {
    "listing_id": "lst-1",
    "title": "Minimal weekly planner",
    "description": "A printable weekly planner with a clean layout.",
    "price_minor": 600,
    "currency": "USD",
    "digital_files": ["planner.pdf"],
    "tags": ["planner", "printable"],
}


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore):
    tenant = store.create_tenant("Pilot Holdings")
    business = store.create_business(tenant.tenant_id, "Pilot Shop", slug="pilot")
    return tenant, business


def _start(workflow, tenant, business, *, key="wf-1", **kwargs):
    return workflow.start(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        listing=LISTING,
        idempotency_key=key,
        **kwargs,
    )


def test_happy_path_draft_approval_then_dry_run_publish(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)

    started = _start(workflow, tenant, business)
    assert started.stage == STAGE_AWAITING_APPROVAL
    assert started.job.status == "waiting_approval"
    assert started.approval is not None
    assert started.approval.status == "pending"
    assert started.approval.action == APPROVAL_ACTION_PUBLISH
    assert started.draft is not None
    assert started.draft.status == "dry_run"
    assert started.draft.tool == "etsy.listing.draft"
    assert started.publish is None

    decided = workflow.decide(
        tenant_id=tenant.tenant_id,
        approval_id=started.approval.approval_id,
        decision="approved",
        decided_by="owner",
    )
    assert decided.stage == STAGE_APPROVED
    assert decided.job.status == "queued"

    published = workflow.publish(
        tenant_id=tenant.tenant_id,
        job_id=started.job.job_id,
        actor="agent:etsy",
    )
    assert published.stage == STAGE_PUBLISHED
    assert published.job.status == "completed"
    assert published.publish is not None
    assert published.publish.status == "dry_run"
    assert published.publish.tool == "etsy.listing.publish"
    assert published.job.output["dry_run"] is True

    # Replay is a read: no second invocation, no job re-run.
    again = workflow.publish(
        tenant_id=tenant.tenant_id,
        job_id=started.job.job_id,
        actor="agent:etsy",
    )
    assert again.stage == STAGE_PUBLISHED
    assert again.publish is not None
    assert again.publish.invocation_id == published.publish.invocation_id


def test_start_is_idempotent_for_the_same_key(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)

    first = _start(workflow, tenant, business)
    second = _start(workflow, tenant, business)
    assert second.job.job_id == first.job.job_id
    assert second.approval.approval_id == first.approval.approval_id
    assert second.draft.invocation_id == first.draft.invocation_id
    assert len(workflow._gateway.list_invocations(tenant.tenant_id)) == 1
    assert len(approvals.list_approvals(store, tenant.tenant_id)) == 1


def test_publish_before_approval_is_refused_with_a_typed_error(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)
    started = _start(workflow, tenant, business)

    with pytest.raises(BusinessError) as exc:
        workflow.publish(
            tenant_id=tenant.tenant_id,
            job_id=started.job.job_id,
            actor="agent:etsy",
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert jobs.get(store, tenant.tenant_id, started.job.job_id).status == "waiting_approval"


def test_publish_without_any_linked_approval_is_refused(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind=WORKFLOW_KIND,
        idempotency_key="bare-job",
        input=LISTING,
        actor="agent:etsy",
    )
    jobs.enqueue(store, tenant.tenant_id, job.job_id, actor="agent:etsy")
    workflow = EtsyDigitalListingWorkflow(store)
    with pytest.raises(BusinessError) as exc:
        workflow.publish(
            tenant_id=tenant.tenant_id,
            job_id=job.job_id,
            actor="agent:etsy",
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert jobs.get(store, tenant.tenant_id, job.job_id).status == "queued"


def test_rejected_approval_cancels_the_workflow(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)
    started = _start(workflow, tenant, business)
    decided = workflow.decide(
        tenant_id=tenant.tenant_id,
        approval_id=started.approval.approval_id,
        decision="rejected",
        decided_by="owner",
        reason="niche not approved",
    )
    assert decided.stage == STAGE_REJECTED
    assert decided.job.status == "cancelled"
    with pytest.raises(BusinessError) as exc:
        workflow.publish(
            tenant_id=tenant.tenant_id,
            job_id=started.job.job_id,
            actor="agent:etsy",
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"


def test_expired_approval_refuses_publish(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)
    started = _start(
        workflow, tenant, business, expires_at=time.time() - 60
    )
    workflow.decide(
        tenant_id=tenant.tenant_id,
        approval_id=started.approval.approval_id,
        decision="approved",
        decided_by="owner",
    )
    state = workflow.state(tenant.tenant_id, started.job.job_id)
    assert state.stage == STAGE_APPROVAL_EXPIRED
    with pytest.raises(BusinessError) as exc:
        workflow.publish(
            tenant_id=tenant.tenant_id,
            job_id=started.job.job_id,
            actor="agent:etsy",
        )
    assert exc.value.code == "BUSINESS_APPROVAL_EXPIRED"


def test_approval_for_another_action_is_refused(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    job, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind=WORKFLOW_KIND,
        idempotency_key="wrong-action-job",
        input=LISTING,
        actor="agent:etsy",
    )
    jobs.enqueue(store, tenant.tenant_id, job.job_id, actor="agent:etsy")
    jobs.claim(
        store, tenant.tenant_id, job.job_id, worker="test-worker", actor="agent:etsy"
    )
    approval, _ = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="tool:etsy.listing.price",
        requested_by="owner",
        idempotency_key="price-approval",
        job_id=job.job_id,
    )
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
    )
    workflow = EtsyDigitalListingWorkflow(store)
    with pytest.raises(BusinessError) as exc:
        workflow.publish(
            tenant_id=tenant.tenant_id,
            job_id=job.job_id,
            actor="agent:etsy",
            approval_id=approval.approval_id,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert jobs.get(store, tenant.tenant_id, job.job_id).status == "queued"


def test_real_publish_is_refused_by_the_dry_run_pilot(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)
    started = _start(workflow, tenant, business)
    workflow.decide(
        tenant_id=tenant.tenant_id,
        approval_id=started.approval.approval_id,
        decision="approved",
        decided_by="owner",
    )
    with pytest.raises(BusinessError) as exc:
        workflow.publish(
            tenant_id=tenant.tenant_id,
            job_id=started.job.job_id,
            actor="agent:etsy",
            dry_run=False,
        )
    assert exc.value.code == WORKFLOW_DRY_RUN_ONLY_CODE


def test_listing_validation_rejects_an_incomplete_draft(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)
    with pytest.raises(BusinessError) as exc:
        workflow.start(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            actor="agent:etsy",
            listing={"title": "", "description": "x", "price_minor": 1, "digital_files": ["a"]},
            idempotency_key="bad-1",
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"
    assert jobs.list_jobs(store, tenant.tenant_id) == []


def test_publish_requires_a_recorded_listing_id(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    workflow = EtsyDigitalListingWorkflow(store)
    listing = {k: v for k, v in LISTING.items() if k != "listing_id"}
    started = workflow.start(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        listing=listing,
        idempotency_key="no-listing-id",
    )
    workflow.decide(
        tenant_id=tenant.tenant_id,
        approval_id=started.approval.approval_id,
        decision="approved",
        decided_by="owner",
    )
    with pytest.raises(BusinessError) as exc:
        workflow.publish(
            tenant_id=tenant.tenant_id,
            job_id=started.job.job_id,
            actor="agent:etsy",
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"
