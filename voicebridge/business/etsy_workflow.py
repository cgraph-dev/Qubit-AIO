"""The Etsy digital-listing pilot workflow.

This is the small, durable path the master plan asks for: draft → owner
approval → dry-run publish. It is built on the existing primitives, not beside
them:

* ``jobs`` owns the durable state machine and leases;
* ``approvals`` owns the human pause/resume; an approval is linked to the job;
* ``ToolGateway`` owns policy, scope, budget, idempotency and the redacted
  result/audit record — the draft and publish steps are gateway invocations,
  so nothing bypasses the one door.

Dry-run is the only mode. The draft step records intent without an adapter
call; publish re-checks the owner's approval itself (missing, pending,
expired, or issued for another action is a typed refusal) before asking the
gateway for a dry-run publish intent. No Etsy shop, transport, credential or
network call exists in this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import approvals, jobs, models
from .errors import (
    APPROVAL_EXPIRED_CODE,
    APPROVAL_REQUIRED_CODE,
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    BusinessError,
)
from .integration_tools import (
    TOOL_ETSY_LISTING_DRAFT,
    TOOL_ETSY_LISTING_PUBLISH,
)
from .tools import Policy, ToolGateway

WORKFLOW_KIND = "etsy.digital_listing"
APPROVAL_ACTION_PUBLISH = f"tool:{TOOL_ETSY_LISTING_PUBLISH}"
WORKFLOW_DRY_RUN_ONLY_CODE = "etsy_workflow_dry_run_only"

STAGE_CREATED = "created"
STAGE_DRAFTING = "drafting"
STAGE_AWAITING_APPROVAL = "awaiting_approval"
STAGE_APPROVAL_EXPIRED = "approval_expired"
STAGE_APPROVED = "approved"
STAGE_PUBLISHING = "publishing"
STAGE_PUBLISHED = "published"
STAGE_REJECTED = "rejected"
STAGE_CANCELLED = "cancelled"
STAGE_FAILED = "failed"
STAGE_UNKNOWN = "unknown"

WORKFLOW_STAGES: tuple[str, ...] = (
    STAGE_CREATED,
    STAGE_DRAFTING,
    STAGE_AWAITING_APPROVAL,
    STAGE_APPROVAL_EXPIRED,
    STAGE_APPROVED,
    STAGE_PUBLISHING,
    STAGE_PUBLISHED,
    STAGE_REJECTED,
    STAGE_CANCELLED,
    STAGE_FAILED,
    STAGE_UNKNOWN,
)

DRAFT_POLICY = Policy(
    allowed_tools=frozenset({TOOL_ETSY_LISTING_DRAFT}),
    autonomy="L2",
)
PUBLISH_POLICY = Policy(
    allowed_tools=frozenset({TOOL_ETSY_LISTING_PUBLISH}),
    autonomy="L4",
)


@dataclass(frozen=True, slots=True)
class ListingWorkflowState:
    stage: str
    job: models.JobRun
    approval: models.Approval | None = None
    draft: models.ToolInvocation | None = None
    publish: models.ToolInvocation | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "job_id": self.job.job_id,
            "job_status": self.job.status,
            "approval_id": self.approval.approval_id if self.approval else None,
            "approval_status": self.approval.status if self.approval else None,
            "approval_action": self.approval.action if self.approval else None,
            "approval_expires_at": (
                self.approval.expires_at if self.approval else None
            ),
            "draft_invocation_id": self.draft.invocation_id if self.draft else None,
            "publish_invocation_id": (
                self.publish.invocation_id if self.publish else None
            ),
        }


class EtsyDigitalListingWorkflow:
    def __init__(
        self,
        store: Any,
        gateway: ToolGateway | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway or ToolGateway(store)
        self._clock = clock or time.time

    # -- lifecycle -----------------------------------------------------
    def start(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        listing: Mapping[str, Any],
        idempotency_key: str,
        expires_at: float | None = None,
        lease_seconds: float = 300.0,
    ) -> ListingWorkflowState:
        """Create the job, record the draft intent and request approval."""
        self._validate_listing(listing)
        job, created = jobs.create(
            self._store,
            tenant_id=tenant_id,
            business_id=business_id,
            kind=WORKFLOW_KIND,
            idempotency_key=idempotency_key,
            input=dict(listing),
            actor=actor,
        )
        if not created and job.status in jobs.TERMINAL_STATUSES:
            return self.state(tenant_id, job.job_id)
        if not created:
            existing = self._linked_approval(tenant_id, job)
            if existing is not None and existing.status == "approved":
                return self.state(tenant_id, job.job_id)

        if job.status == "created":
            job = jobs.enqueue(self._store, tenant_id, job.job_id, actor=actor)
        if job.status == "queued":
            job = jobs.claim(
                self._store,
                tenant_id,
                job.job_id,
                worker=f"workflow:{actor}",
                lease_seconds=lease_seconds,
                actor=actor,
            )
        if job.status == "running":
            try:
                self._record_draft(tenant_id, business_id, actor, job)
                self._request_approval(
                    tenant_id,
                    business_id,
                    actor,
                    job,
                    expires_at=expires_at,
                )
            except BusinessError as exc:
                self._fail_job(tenant_id, job.job_id, actor, exc)
                raise
        elif job.status != "waiting_approval":
            raise BusinessError(
                BAD_STATE_CODE,
                f"workflow cannot start from job status {job.status!r}",
            )
        return self.state(tenant_id, job.job_id)

    def request_publish_approval(
        self,
        *,
        tenant_id: str,
        job_id: str,
        actor: str,
        expires_at: float | None = None,
    ) -> ListingWorkflowState:
        job = jobs.get(self._store, tenant_id, job_id)
        if job.status in jobs.TERMINAL_STATUSES:
            raise BusinessError(
                BAD_STATE_CODE,
                f"job {job_id!r} is already {job.status!r}",
            )
        existing = self._linked_approval(tenant_id, job)
        if existing is not None and existing.status in ("pending", "approved"):
            return self.state(tenant_id, job_id)
        if job.status == "waiting_approval":
            return self.state(tenant_id, job_id)
        if job.status not in ("queued", "running"):
            raise BusinessError(
                BAD_STATE_CODE,
                f"an approval can only be requested for a queued/running job"
                f" (status={job.status!r})",
            )
        if job.status == "queued":
            # ``approvals.request`` pauses a running job; claim it first so the
            # job state machine follows its declared edges.
            job = jobs.claim(
                self._store,
                tenant_id,
                job_id,
                worker=f"workflow:{actor}",
                actor=actor,
            )
        self._request_approval(
            job.tenant_id,
            job.business_id,
            actor,
            job,
            expires_at=expires_at,
        )
        return self.state(tenant_id, job_id)

    def decide(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        decision: str,
        decided_by: str,
        reason: str = "",
    ) -> ListingWorkflowState:
        approval = approvals.decide(
            self._store,
            tenant_id=tenant_id,
            approval_id=approval_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
        )
        job_id = approval.job_id
        if job_id is None:
            raise BusinessError(
                BAD_STATE_CODE,
                f"approval {approval_id!r} is not linked to a workflow job",
            )
        return self.state(tenant_id, job_id)

    def publish(
        self,
        *,
        tenant_id: str,
        job_id: str,
        actor: str,
        approval_id: str | None = None,
        idempotency_key: str | None = None,
        listing_id: str | None = None,
        dry_run: bool = True,
        lease_seconds: float = 300.0,
    ) -> ListingWorkflowState:
        """Publish in dry-run mode, only with a live, matching approval."""
        if not dry_run:
            raise BusinessError(
                WORKFLOW_DRY_RUN_ONLY_CODE,
                "the Etsy pilot workflow publishes in dry-run mode only;"
                " no real shop or transport is configured",
            )
        job = jobs.get(self._store, tenant_id, job_id)
        if job.status == "completed":
            return self.state(tenant_id, job_id)
        if job.status not in ("queued", "waiting_approval"):
            raise BusinessError(
                BAD_STATE_CODE,
                f"publish needs an approved, queued job (status={job.status!r});"
                " request and decide the approval first",
            )
        # Validate the human decision before looking at the job's transient
        # status, so a missing/pending/expired/wrong-action approval is always
        # the typed refusal rather than a generic state error.
        approval = self._publish_approval(tenant_id, job, approval_id)
        if job.status != "queued":
            raise BusinessError(
                BAD_STATE_CODE,
                f"publish needs the approved job to be queued"
                f" (status={job.status!r})",
            )
        target = listing_id or str(job.input.get("listing_id") or "").strip()
        if not target:
            raise BusinessError(
                BAD_INPUT_CODE,
                "a listing_id is required to publish (none was recorded on the"
                " draft)",
            )
        job = jobs.claim(
            self._store,
            tenant_id,
            job_id,
            worker=f"workflow:{actor}",
            lease_seconds=lease_seconds,
            actor=actor,
        )
        try:
            outcome = self._gateway.invoke(
                tenant_id=tenant_id,
                business_id=job.business_id,
                actor=actor,
                tool=TOOL_ETSY_LISTING_PUBLISH,
                idempotency_key=idempotency_key or f"{job_id}:publish",
                policy=PUBLISH_POLICY,
                payload={"listing_id": target},
                dry_run=True,
                approval_id=approval.approval_id,
            )
        except BusinessError as exc:
            self._fail_job(tenant_id, job_id, actor, exc)
            raise
        jobs.finish(
            self._store,
            tenant_id,
            job_id,
            "completed",
            actor=actor,
            output={
                "stage": STAGE_PUBLISHED,
                "approval_id": approval.approval_id,
                "publish_invocation_id": outcome.invocation.invocation_id,
                "dry_run": True,
            },
        )
        return self.state(tenant_id, job_id)

    # -- observation ---------------------------------------------------
    def state(self, tenant_id: str, job_id: str) -> ListingWorkflowState:
        job = jobs.get(self._store, tenant_id, job_id)
        approval = self._linked_approval(tenant_id, job)
        invocations = {
            invocation.idempotency_key: invocation
            for invocation in self._gateway.list_invocations(
                tenant_id, business_id=job.business_id
            )
        }
        return ListingWorkflowState(
            stage=self._stage(job, approval),
            job=job,
            approval=approval,
            draft=invocations.get(f"{job_id}:draft"),
            publish=invocations.get(f"{job_id}:publish"),
        )

    # -- internals -----------------------------------------------------
    def _validate_listing(self, listing: Mapping[str, Any]) -> None:
        if not isinstance(listing, Mapping):
            raise BusinessError(BAD_INPUT_CODE, "listing must be a mapping")
        for key in ("title", "description"):
            if not str(listing.get(key) or "").strip():
                raise BusinessError(
                    BAD_INPUT_CODE, f"listing.{key} is required"
                )
        price = listing.get("price_minor")
        if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
            raise BusinessError(
                BAD_INPUT_CODE,
                "listing.price_minor must be a positive integer (minor units)",
            )
        files = listing.get("digital_files")
        if not files:
            raise BusinessError(
                BAD_INPUT_CODE,
                "listing.digital_files must name at least one deliverable",
            )

    def _record_draft(
        self,
        tenant_id: str,
        business_id: str,
        actor: str,
        job: models.JobRun,
    ) -> models.ToolInvocation:
        outcome = self._gateway.invoke(
            tenant_id=tenant_id,
            business_id=business_id,
            actor=actor,
            tool=TOOL_ETSY_LISTING_DRAFT,
            idempotency_key=f"{job.job_id}:draft",
            policy=DRAFT_POLICY,
            payload=dict(job.input),
            dry_run=True,
        )
        return outcome.invocation

    def _request_approval(
        self,
        tenant_id: str,
        business_id: str,
        actor: str,
        job: models.JobRun,
        *,
        expires_at: float | None,
    ) -> models.Approval:
        approval, _ = approvals.request(
            self._store,
            tenant_id=tenant_id,
            business_id=business_id,
            action=APPROVAL_ACTION_PUBLISH,
            requested_by=actor,
            idempotency_key=f"{job.job_id}:publish-approval",
            job_id=job.job_id,
            payload={
                "tool": TOOL_ETSY_LISTING_PUBLISH,
                "listing": dict(job.input),
            },
            expires_at=expires_at,
        )
        return approval

    def _linked_approval(
        self, tenant_id: str, job: models.JobRun
    ) -> models.Approval | None:
        linked = [
            approval
            for approval in approvals.list_approvals(
                self._store, tenant_id, business_id=job.business_id
            )
            if approval.job_id == job.job_id
            and approval.action == APPROVAL_ACTION_PUBLISH
        ]
        if not linked:
            return None
        return linked[-1]

    def _publish_approval(
        self,
        tenant_id: str,
        job: models.JobRun,
        approval_id: str | None,
    ) -> models.Approval:
        if approval_id is not None:
            approval = approvals.get(self._store, tenant_id, approval_id)
            if approval.job_id not in (None, job.job_id):
                raise BusinessError(
                    APPROVAL_REQUIRED_CODE,
                    f"approval {approval_id!r} belongs to another job",
                )
        else:
            approval = self._linked_approval(tenant_id, job)
            if approval is None:
                raise BusinessError(
                    APPROVAL_REQUIRED_CODE,
                    "publish needs an approved owner decision; none is linked"
                    " to this job",
                )
        if approval.action != APPROVAL_ACTION_PUBLISH:
            raise BusinessError(
                APPROVAL_REQUIRED_CODE,
                f"approval {approval.approval_id!r} is for"
                f" {approval.action!r}, not {APPROVAL_ACTION_PUBLISH!r}",
            )
        if approval.status == "expired":
            raise BusinessError(
                APPROVAL_EXPIRED_CODE,
                f"approval {approval.approval_id!r} expired; request a fresh"
                " decision",
            )
        if approval.status != "approved":
            raise BusinessError(
                APPROVAL_REQUIRED_CODE,
                f"approval {approval.approval_id!r} is {approval.status!r}, not"
                " approved",
            )
        if approval.expires_at is not None and approval.expires_at <= self._clock():
            raise BusinessError(
                APPROVAL_EXPIRED_CODE,
                f"approval {approval.approval_id!r} is past its expiry; request"
                " a fresh decision",
            )
        return approval

    def _fail_job(
        self, tenant_id: str, job_id: str, actor: str, exc: BusinessError
    ) -> None:
        try:
            jobs.finish(
                self._store,
                tenant_id,
                job_id,
                "failed",
                actor=actor,
                error_code=exc.code,
                error_detail=exc.detail,
            )
        except BusinessError:
            pass

    def _stage(
        self, job: models.JobRun, approval: models.Approval | None
    ) -> str:
        if job.status == "completed":
            return STAGE_PUBLISHED
        if job.status == "failed":
            return STAGE_FAILED
        if job.status == "unknown":
            return STAGE_UNKNOWN
        if job.status == "cancelled":
            if approval is not None and approval.status == "rejected":
                return STAGE_REJECTED
            if approval is not None and approval.status == "expired":
                return STAGE_APPROVAL_EXPIRED
            return STAGE_CANCELLED
        if (
            approval is not None
            and approval.status in ("pending", "approved")
            and approval.expires_at is not None
            and approval.expires_at <= self._clock()
        ):
            return STAGE_APPROVAL_EXPIRED
        if job.status == "waiting_approval":
            return STAGE_AWAITING_APPROVAL
        if job.status == "queued":
            if approval is not None and approval.status == "approved":
                return STAGE_APPROVED
            return STAGE_AWAITING_APPROVAL if approval is not None else STAGE_CREATED
        if job.status == "running":
            return STAGE_PUBLISHING
        return STAGE_CREATED


__all__ = [
    "APPROVAL_ACTION_PUBLISH",
    "EtsyDigitalListingWorkflow",
    "ListingWorkflowState",
    "PUBLISH_POLICY",
    "DRAFT_POLICY",
    "STAGE_APPROVAL_EXPIRED",
    "STAGE_APPROVED",
    "STAGE_AWAITING_APPROVAL",
    "STAGE_CANCELLED",
    "STAGE_CREATED",
    "STAGE_DRAFTING",
    "STAGE_FAILED",
    "STAGE_PUBLISHED",
    "STAGE_PUBLISHING",
    "STAGE_REJECTED",
    "STAGE_UNKNOWN",
    "WORKFLOW_DRY_RUN_ONLY_CODE",
    "WORKFLOW_KIND",
    "WORKFLOW_STAGES",
]
