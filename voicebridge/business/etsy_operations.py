"""Etsy digital-shop operations — sandbox/fake end to end.

P5-01 composes the existing Business OS primitives into one operable Etsy
digital shop. Nothing here is a second executor stack:

* :class:`~voicebridge.business.etsy_workflow.EtsyDigitalListingWorkflow` owns
  the draft job and the publish approval link;
* :class:`~voicebridge.business.tools.ToolGateway` is the only door to an
  external effect; the adapter is injected, so the provider path is a fake /
  sandbox transport and never a live Etsy call;
* :mod:`voicebridge.business.approvals` owns the durable human decision. The
  publish and refund approvals are bound to the exact normalized intent hash,
  and the hash is recomputed from the persisted draft/refund record at
  execution time, so a payload swap is a typed refusal;
* :mod:`voicebridge.business.ledger` is the money truth. Refund and payout
  effects append balanced double-entry rows; an unknown outcome appends none;
* :mod:`voicebridge.business.eventlog` is the replayable authority and
  :meth:`BusinessStore.record_artifact` holds the immutable receipts
  (draft, provider receipt, order, entitlement, support case, reconciliation).

The provider outcome is never guessed. A definite receipt completes the job;
an ambiguous response or a transport failure moves the job to ``unknown`` and
records an ``unknown`` receipt. Only an explicit reconciliation receipt with a
provider reference may complete or fail it.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..integrations.base import ApprovalToken, BaseAdapter
from ..integrations.etsy import (
    APPROVAL_REFUND,
    EtsyAdapter,
)
from . import approvals, eventlog, jobs, ledger, models
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    BusinessError,
)
from .etsy_workflow import (
    APPROVAL_ACTION_PUBLISH,
    PUBLISH_POLICY,
    WORKFLOW_KIND,
    EtsyDigitalListingWorkflow,
)
from .ids import fingerprint, new_id
from .integration_tools import (
    TOOL_ETSY_RECEIPT_REFUND,
    IntegrationTool,
    IntegrationToolRegistry,
)
from .tools import ExecutorResult, Policy, ToolGateway, ToolRequest

SCHEMA = "qubit-office-etsy-operations/1"

EVENT_NICHE_RECORDED = "etsy.niche.recorded"
EVENT_ASSET_REFERENCED = "etsy.asset.referenced"
EVENT_LISTING_APPROVAL_REQUESTED = "etsy.listing.approval_requested"
EVENT_LISTING_APPROVED = "etsy.listing.approved"
EVENT_LISTING_PUBLISHED = "etsy.listing.published"
EVENT_LISTING_UNKNOWN = "etsy.listing.outcome_unknown"
EVENT_ORDER_RECORDED = "etsy.order.recorded"
EVENT_ENTITLEMENT_GRANTED = "etsy.entitlement.granted"
EVENT_SUPPORT_OPENED = "etsy.support.opened"
EVENT_REFUND_APPROVAL_REQUESTED = "etsy.refund.approval_requested"
EVENT_REFUND_APPROVED = "etsy.refund.approved"
EVENT_REFUND_SETTLED = "etsy.refund.settled"
EVENT_REFUND_UNKNOWN = "etsy.refund.outcome_unknown"
EVENT_PAYOUT_RECORDED = "etsy.payout.recorded"
EVENT_RECONCILED = "etsy.reconciled"
EVENT_LEDGER_RECONCILED = "etsy.ledger.reconciled"

STATUS_DRAFTED = "drafted"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_APPROVED = "approved"
STATUS_PUBLISHED = "published"
STATUS_UNKNOWN = "unknown"
STATUS_RECONCILED = "reconciled"
STATUS_RECORDED = "recorded"
STATUS_OPEN = "open"
STATUS_SETTLED = "settled"
STATUS_BALANCED = "balanced"

ARTIFACT_NICHE = "etsy.niche_research"
ARTIFACT_ASSET = "etsy.asset_reference"
ARTIFACT_DRAFT = "etsy.listing_draft"
ARTIFACT_INTENT = "etsy.approval_intent"
ARTIFACT_PUBLISH_RECEIPT = "etsy.provider_receipt"
ARTIFACT_ORDER = "etsy.order"
ARTIFACT_ENTITLEMENT = "etsy.entitlement"
ARTIFACT_SUPPORT = "etsy.support_case"
ARTIFACT_REFUND_INTENT = "etsy.refund_intent"
ARTIFACT_REFUND_RECEIPT = "etsy.refund_receipt"
ARTIFACT_PAYOUT = "etsy.payout_receipt"
ARTIFACT_RECONCILIATION = "etsy.reconciliation"
ARTIFACT_LEDGER_RECONCILIATION = "etsy.ledger_reconciliation"

ETSY_BAD_INPUT = "etsy_operations_bad_input"
ETSY_PAID_ART_GUARD = "etsy_paid_art_guard"
ETSY_INLINE_ART_FORBIDDEN = "etsy_inline_art_forbidden"
ETSY_ASSET_REQUIRED = "etsy_asset_required"
ETSY_ASSET_VERSION_MISMATCH = "etsy_asset_version_mismatch"
ETSY_APPROVAL_REQUIRED = "etsy_approval_required"
ETSY_APPROVAL_PAYLOAD_MISMATCH = "etsy_approval_payload_mismatch"
ETSY_ADAPTER_UNAVAILABLE = "etsy_adapter_unavailable"
ETSY_PROVIDER_RECEIPT_INVALID = "etsy_provider_receipt_invalid"
ETSY_PUBLISH_NOT_EXECUTED = "etsy_publish_not_executed"
ETSY_NOT_PUBLISHED = "etsy_listing_not_published"
ETSY_RECONCILIATION_REQUIRED = "etsy_reconciliation_required"
ETSY_NOT_UNKNOWN = "etsy_outcome_not_unknown"
ETSY_RECONCILIATION_RECEIPT_REQUIRED = "etsy_reconciliation_receipt_required"
ETSY_ORDER_REQUIRED = "etsy_order_required"
ETSY_LEDGER_UNBALANCED = "etsy_ledger_unbalanced"
ETSY_EXTERNAL_EFFECT_MISMATCH = "etsy_external_effect_mismatch"

REFUND_KIND = "etsy.refund"
REFUND_POLICY = Policy(
    allowed_tools=frozenset({TOOL_ETSY_RECEIPT_REFUND}),
    autonomy="L4",
)

REFUND_RESOLUTIONS = frozenset(
    {"completed", "published", "refunded", "not_executed", "failed"}
)
EXECUTED_RESOLUTIONS = frozenset({"completed", "published", "refunded"})

_REFUND_ACTION = f"tool:{TOOL_ETSY_RECEIPT_REFUND}"

#: Keys that would inline provider art. They are refused on every asset
#: reference, and a paid asset additionally refuses redistribution rights.
_INLINE_ART_KEYS = (
    "content",
    "content_base64",
    "art",
    "art_bytes",
    "image",
    "image_bytes",
    "raw_bytes",
    "zip",
    "archive",
    "local_path_copy",
)


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return text


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} must be a positive integer (minor units)"
        )
    return value


def _non_negative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} must be a non-negative integer (minor units)"
        )
    return value


def _currency(value: Any) -> str:
    clean = _text(value, "currency", max_len=8).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BusinessError(BAD_INPUT_CODE, "currency must be a 3-letter code")
    return clean


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a mapping")
    return dict(value)


def _json_body(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))


def _loads(body: str | None) -> dict[str, Any]:
    if not body:
        return {}
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True, slots=True)
class OperationRecord:
    """One durable operation outcome: references, never a copy of truth."""

    operation: str
    status: str
    artifact: models.Artifact | None = None
    artifact_ids: tuple[str, ...] = ()
    job: models.JobRun | None = None
    approval: models.Approval | None = None
    invocation: models.ToolInvocation | None = None
    intent_hash: str | None = None
    receipt: dict[str, Any] | None = None
    ledger_entry_ids: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "status": self.status,
            "artifact_id": self.artifact.artifact_id if self.artifact else None,
            "artifact_ids": list(self.artifact_ids),
            "job_id": self.job.job_id if self.job else None,
            "job_status": self.job.status if self.job else None,
            "approval_id": (
                self.approval.approval_id if self.approval else None
            ),
            "approval_status": (
                self.approval.status if self.approval else None
            ),
            "invocation_id": (
                self.invocation.invocation_id if self.invocation else None
            ),
            "invocation_status": (
                self.invocation.status if self.invocation else None
            ),
            "intent_hash": self.intent_hash,
            "receipt": self.receipt,
            "ledger_entry_ids": list(self.ledger_entry_ids),
            "detail": self.detail,
        }


def _settle_refund(adapter: BaseAdapter, request: ToolRequest) -> ExecutorResult:
    """Adapter dispatch with NO gateway ledger directive.

    The built-in Etsy refund executor attaches a debit directive that the
    gateway appends as soon as the executor returns. A refund can return an
    ambiguous provider outcome, so this operations layer attaches the ledger
    rows itself only after it has a definite receipt. The adapter still gets
    the exact approval token it requires.
    """
    if not isinstance(adapter, EtsyAdapter):
        raise BusinessError(BAD_INPUT_CODE, "refund needs an Etsy adapter")
    body = request.payload if isinstance(request.payload, Mapping) else {}
    receipt_id = _text(body.get("receipt_id"), "receipt_id", max_len=200)
    amount_minor = _positive_int(body.get("amount_minor"), "amount_minor")
    token = (
        ApprovalToken(approval_id=request.approval_id, action=APPROVAL_REFUND)
        if request.approval_id
        else None
    )
    result = adapter.refund_receipt(
        receipt_id,
        amount_minor=amount_minor,
        idempotency_key=request.idempotency_key,
        reason=str(body.get("reason") or "")[:500],
        approval=token,
    )
    return ExecutorResult(
        result={"adapter": result.as_dict()},
        executed=result.executed,
        dry_run=result.dry_run,
    )


REFUND_TOOL = IntegrationTool(
    TOOL_ETSY_RECEIPT_REFUND,
    "etsy",
    "receipt.refund",
    _settle_refund,
    "refund",
    approval_action=APPROVAL_REFUND,
    money=True,
    description="Refund a receipt; this operations layer records the ledger.",
)


class InMemoryEtsySandboxTransport:
    """A deterministic provider double — never a network client.

    It is deliberately the only provider seam the operations module can use,
    and it is injected explicitly. ``ambiguous_publish`` / ``ambiguous_refund``
    model a lost response so the ``unknown`` path is exercised honestly.
    """

    def __init__(
        self,
        *,
        shop_id: str = "sandbox-shop",
        ambiguous_publish: bool = False,
        ambiguous_refund: bool = False,
        fail_publish: bool = False,
        fail_refund: bool = False,
    ) -> None:
        self.shop_id = shop_id
        self.ambiguous_publish = ambiguous_publish
        self.ambiguous_refund = ambiguous_refund
        self.fail_publish = fail_publish
        self.fail_refund = fail_refund
        self.calls: list[dict[str, Any]] = []
        self.draft_calls = 0
        self.publish_calls = 0
        self.refund_calls = 0
        self._listing_seq = 0
        self._receipt_seq = 0
        self._refund_seq = 0

    def _next_listing(self) -> str:
        self._listing_seq += 1
        return f"sbx-lst-{self._listing_seq}"

    def _next_receipt(self) -> str:
        self._receipt_seq += 1
        return f"sbx-rcpt-{self._receipt_seq}"

    def _next_refund(self) -> str:
        self._refund_seq += 1
        return f"sbx-ref-{self._refund_seq}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": dict(params or {}),
                "json_body": dict(json_body or {}),
            }
        )
        if method == "GET":
            if path.endswith("/receipts"):
                return {"count": 0, "results": []}
            if path.endswith("/listings"):
                return {"count": 0, "results": []}
            return {"shop_id": self.shop_id}
        if method == "POST" and "/listings" in path and "/refunds" not in path:
            self.draft_calls += 1
            listing_id = self._next_listing()
            return {
                "listing_id": listing_id,
                "state": "draft",
                "shop_id": self.shop_id,
                "type": "download",
            }
        if method == "PATCH" and "/listings/" in path:
            self.publish_calls += 1
            if self.fail_publish:
                raise RuntimeError("sandbox publish transport failure")
            listing_id = path.rstrip("/").rsplit("/", 1)[-1]
            if self.ambiguous_publish:
                return {
                    "listing_id": listing_id,
                    "state": "unknown",
                    "uncertain": True,
                }
            return {
                "listing_id": listing_id,
                "state": "active",
                "receipt_id": self._next_receipt(),
                "provider_request_id": f"sbx-req-{self.publish_calls}",
            }
        if method == "POST" and "/refunds" in path:
            self.refund_calls += 1
            if self.fail_refund:
                raise RuntimeError("sandbox refund transport failure")
            receipt_id = path.split("/receipts/", 1)[-1].split("/", 1)[0]
            if self.ambiguous_refund:
                return {"receipt_id": receipt_id, "status": "unknown"}
            return {
                "receipt_id": receipt_id,
                "status": "refunded",
                "refund_id": self._next_refund(),
                "amount_minor": int((json_body or {}).get("amount_minor") or 0),
            }
        raise RuntimeError(f"sandbox transport has no route for {method} {path}")


def sandbox_etsy_adapter(
    transport: InMemoryEtsySandboxTransport | None = None,
    *,
    shop_id: str = "sandbox-shop",
    credentials: Mapping[str, Any] | None = None,
) -> EtsyAdapter:
    """Build the non-network Etsy adapter this task accepts against."""
    return EtsyAdapter(
        shop_id=shop_id,
        transport=transport or InMemoryEtsySandboxTransport(shop_id=shop_id),
        credentials=dict(
            credentials
            or {"api_key": "sandbox-api-key", "access_token": "sandbox-token"}
        ),
        dry_run=False,
        rate_limit=1000,
    )


class EtsyOperations:
    """The P5-01 Etsy digital-shop operations service."""

    def __init__(
        self,
        store: Any,
        *,
        adapter: BaseAdapter | None = None,
        registry: IntegrationToolRegistry | None = None,
        gateway: ToolGateway | None = None,
        workflow: EtsyDigitalListingWorkflow | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or time.time
        if registry is None and adapter is not None:
            registry = IntegrationToolRegistry({"etsy": adapter})
            registry.register_tool(REFUND_TOOL)
        elif registry is not None:
            registry.register_tool(REFUND_TOOL)
        self._registry = registry
        self._gateway = gateway or (
            ToolGateway(store, registry) if registry is not None else ToolGateway(store)
        )
        self._workflow = workflow or EtsyDigitalListingWorkflow(
            store, self._gateway, clock=self._clock
        )

    # -- research and assets ------------------------------------------
    def record_niche(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        niche_id: str,
        title: str,
        evidence: Mapping[str, Any],
        source_ref: str,
        license_status: str = "unverified",
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        key = idempotency_key or f"etsy:niche:{niche_id}"
        self._require_scope(tenant_id, business_id)
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("record_niche", existing)
        niche = {
            "niche_id": _text(niche_id, "niche_id", max_len=200),
            "title": _text(title, "title", max_len=300),
            "evidence": _mapping(evidence, "evidence"),
            "source_ref": _text(source_ref, "source_ref", max_len=1000),
            "license_status": _text(
                license_status, "license_status", max_len=60
            ).lower(),
            "recorded_at": _now_iso(),
        }
        name = f"{ARTIFACT_NICHE}:{niche['niche_id']}"
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_NICHE,
            name=name,
            body=niche,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_NICHE_RECORDED,
            {
                "status": STATUS_RECORDED,
                "niche_id": niche["niche_id"],
                "artifact_id": artifact.artifact_id,
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event("record_niche", event, artifact=artifact)

    def reference_asset(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        asset_ref: str,
        version: str,
        license: str,
        license_source: str,
        paid: bool,
        redistribution_allowed: bool,
        credit_required: bool = True,
        intended_use: str = "listing",
        inline: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        """Record a licensed asset *reference*; never the art itself."""
        self._require_scope(tenant_id, business_id)
        ref = _text(asset_ref, "asset_ref", max_len=500)
        ver = _text(version, "version", max_len=200)
        key = idempotency_key or f"etsy:asset:{ref}:{ver}"
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("reference_asset", existing)
        content = dict(inline or {})
        for blocked in _INLINE_ART_KEYS:
            if blocked in content:
                if paid:
                    raise BusinessError(
                        ETSY_PAID_ART_GUARD,
                        "paid art must stay outside the tree; reference the"
                        " local asset ledger/recipe instead of inlining it",
                    )
                raise BusinessError(
                    ETSY_INLINE_ART_FORBIDDEN,
                    f"inline art field {blocked!r} is refused; record a"
                    " reference, not the bytes",
                )
        if paid and redistribution_allowed:
            raise BusinessError(
                ETSY_PAID_ART_GUARD,
                "paid art cannot be marked redistributable; keep it local-only",
            )
        body = {
            "asset_ref": ref,
            "version": ver,
            "license": _text(license, "license", max_len=200),
            "license_source": _text(
                license_source, "license_source", max_len=1000
            ),
            "paid": bool(paid),
            "redistribution_allowed": bool(redistribution_allowed),
            "credit_required": bool(credit_required),
            "intended_use": _text(intended_use, "intended_use", max_len=120),
            "local_only": bool(paid),
            "inline_bytes": False,
            "reference_sha256": fingerprint(
                {"asset_ref": ref, "version": ver, "license": license}
            ),
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_ASSET,
            name=f"{ARTIFACT_ASSET}:{ref}:{ver}",
            body=body,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_ASSET_REFERENCED,
            {
                "status": STATUS_RECORDED,
                "asset_ref": ref,
                "version": ver,
                "artifact_id": artifact.artifact_id,
                "paid": bool(paid),
                "redistribution_allowed": bool(redistribution_allowed),
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event("reference_asset", event, artifact=artifact)

    # -- listing, approval, sandbox publish ---------------------------
    def create_listing(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        niche_id: str,
        asset_id: str,
        listing: Mapping[str, Any],
        idempotency_key: str,
        expires_at: float | None = None,
        lease_seconds: float = 300.0,
    ) -> OperationRecord:
        self._require_scope(tenant_id, business_id)
        self._require_adapter()
        clean = self._validate_listing(listing)
        niche = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_NICHE}:{niche_id}"
        )
        if niche is None:
            raise BusinessError(
                ETSY_ASSET_REQUIRED,
                f"no niche research {niche_id!r} is recorded",
            )
        asset = self._asset_reference(
            tenant_id, business_id, asset_id, clean["asset_version"]
        )
        if asset is None:
            raise BusinessError(
                ETSY_ASSET_REQUIRED,
                f"no asset reference {asset_id!r} at version"
                f" {clean['asset_version']!r} is recorded; reference a licensed"
                " asset before drafting",
            )
        asset_body = _loads(asset.body)
        if clean["asset_version"] != asset_body.get("version"):
            raise BusinessError(
                ETSY_ASSET_VERSION_MISMATCH,
                f"listing names asset version {clean['asset_version']!r} but"
                f" the reference is {asset_body.get('version')!r}",
            )
        job_key = f"etsy.listing:{idempotency_key}"
        job, created = jobs.create(
            self._store,
            tenant_id=tenant_id,
            business_id=business_id,
            kind=WORKFLOW_KIND,
            idempotency_key=job_key,
            input={**clean, "asset_id": asset_body.get("asset_ref"), "niche_id": niche_id},
            actor=actor,
        )
        if not created:
            return self._existing_listing_record(tenant_id, business_id, job)
        job = jobs.enqueue(self._store, tenant_id, job.job_id, actor=actor)
        job = jobs.claim(
            self._store,
            tenant_id,
            job.job_id,
            worker=f"etsy-operations:{actor}",
            lease_seconds=lease_seconds,
            actor=actor,
        )
        outcome = self._gateway.invoke(
            tenant_id=tenant_id,
            business_id=business_id,
            actor=actor,
            tool="etsy.listing.draft",
            idempotency_key=f"etsy:listing:{job.job_id}:draft",
            policy=Policy(
                allowed_tools=frozenset({"etsy.listing.draft"}), autonomy="L2"
            ),
            payload=clean,
            dry_run=False,
        )
        data, adapter_meta = self._adapter_data(outcome.invocation)
        listing_id = _optional_text(data.get("listing_id"), "listing_id")
        if not listing_id:
            self._fail_job(
                tenant_id,
                job.job_id,
                actor,
                ETSY_PROVIDER_RECEIPT_INVALID,
                "the sandbox draft returned no listing id",
            )
            raise BusinessError(
                ETSY_PROVIDER_RECEIPT_INVALID,
                "the sandbox draft returned no listing id; no listing was"
                " recorded",
            )
        payload_hash = fingerprint(clean)
        draft_body = {
            "job_id": job.job_id,
            "listing_id": listing_id,
            "listing": clean,
            "payload_hash": payload_hash,
            "asset_id": asset_body.get("asset_ref"),
            "niche_id": niche_id,
            "version": clean["asset_version"],
            "currency": clean["currency"],
            "price_minor": clean["price_minor"],
            "provider": "etsy-sandbox",
            "draft_invocation_id": outcome.invocation.invocation_id,
            "provider_state": data.get("state"),
            "recorded_at": _now_iso(),
        }
        draft_artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_DRAFT,
            name=f"{ARTIFACT_DRAFT}:{job.job_id}",
            body=draft_body,
            job_id=job.job_id,
        )
        self._workflow.request_publish_approval(
            tenant_id=tenant_id,
            job_id=job.job_id,
            actor=actor,
            expires_at=expires_at,
        )
        legacy = self._linked_approval(tenant_id, job)
        if legacy is None:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                "the workflow did not record a publish approval",
            )
        intent = self._listing_intent(business_id, draft_body)
        intent_hash = approvals.intent_hash(intent)
        normalized = approvals.decide(
            self._store,
            tenant_id=tenant_id,
            approval_id=legacy.approval_id,
            decision="edit",
            decided_by=actor,
            reason="normalize to the exact publish intent",
            edited_intent=intent,
        )
        approval = self._pending_linked_approval(tenant_id, job)
        if approval is None or approval.approval_id != normalized.approval_id:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                "the normalized publish approval was not linked to the job",
            )
        if approval.request_hash != intent_hash:
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the publish approval is not bound to the exact intent hash",
            )
        intent_artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_INTENT,
            name=f"{ARTIFACT_INTENT}:{job.job_id}",
            body={
                "job_id": job.job_id,
                "approval_id": approval.approval_id,
                "intent": intent,
                "intent_hash": intent_hash,
                "policy_revision": PUBLISH_POLICY.policy_version,
            },
            job_id=job.job_id,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_LISTING_APPROVAL_REQUESTED,
            {
                "status": STATUS_AWAITING_APPROVAL,
                "job_id": job.job_id,
                "approval_id": approval.approval_id,
                "intent_hash": intent_hash,
                "draft_artifact_id": draft_artifact.artifact_id,
                "intent_artifact_id": intent_artifact.artifact_id,
                "listing_id": listing_id,
                "asset_id": asset_body.get("asset_ref"),
                "provider": adapter_meta.get("platform"),
            },
            actor=actor,
            key=f"etsy:listing:{job.job_id}:approval-requested",
        )
        return self._record_from_event(
            "create_listing",
            event,
            artifact=draft_artifact,
            job=jobs.get(self._store, tenant_id, job.job_id),
            approval=approval,
            invocation=outcome.invocation,
            intent_hash=intent_hash,
            detail={"listing_id": listing_id},
        )

    def approve_listing(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        decided_by: str,
        reason: str = "",
    ) -> OperationRecord:
        approval = approvals.decide(
            self._store,
            tenant_id=tenant_id,
            approval_id=approval_id,
            decision="approved",
            decided_by=decided_by,
            reason=reason,
        )
        if approval.job_id is None:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval_id!r} is not linked to a listing job",
            )
        event = self._emit(
            tenant_id,
            approval.business_id,
            EVENT_LISTING_APPROVED,
            {
                "status": STATUS_APPROVED,
                "job_id": approval.job_id,
                "approval_id": approval.approval_id,
                "request_hash": approval.request_hash,
                "decided_by": approval.decided_by,
            },
            actor=decided_by,
            key=f"etsy:listing:{approval.job_id}:approved",
        )
        return self._record_from_event(
            "approve_listing",
            event,
            job=jobs.get(self._store, tenant_id, approval.job_id),
            approval=approval,
            intent_hash=approval.request_hash,
        )

    def publish_listing(
        self,
        *,
        tenant_id: str,
        job_id: str,
        actor: str,
        approval_id: str | None = None,
        idempotency_key: str | None = None,
        lease_seconds: float = 300.0,
    ) -> OperationRecord:
        job = jobs.get(self._store, tenant_id, job_id)
        if job.status == "completed":
            return self._completed_listing_record(tenant_id, job)
        if job.status == "unknown":
            raise BusinessError(
                ETSY_RECONCILIATION_REQUIRED,
                f"job {job_id!r} has an unknown provider outcome; reconcile it"
                " with an explicit provider receipt before any retry",
            )
        if job.status not in ("queued", "waiting_approval"):
            raise BusinessError(
                BAD_STATE_CODE,
                f"publish needs an approved, queued job (status={job.status!r})",
            )
        approval, intent, intent_hash = self._verified_listing_approval(
            tenant_id, job, approval_id
        )
        if job.status != "queued":
            raise BusinessError(
                BAD_STATE_CODE,
                f"publish needs the approved job to be queued"
                f" (status={job.status!r})",
            )
        self._require_adapter()
        job = jobs.claim(
            self._store,
            tenant_id,
            job_id,
            worker=f"etsy-operations:{actor}",
            lease_seconds=lease_seconds,
            actor=actor,
        )
        key = idempotency_key or f"etsy:listing:{job_id}:publish"
        try:
            outcome = self._gateway.invoke(
                tenant_id=tenant_id,
                business_id=job.business_id,
                actor=actor,
                tool="etsy.listing.publish",
                idempotency_key=key,
                policy=PUBLISH_POLICY,
                payload={"listing_id": intent["target"]},
                dry_run=False,
                approval_id=approval.approval_id,
            )
        except BusinessError as exc:
            if exc.code == "adapter_transport_error":
                return self._record_unknown_publish(
                    tenant_id,
                    job,
                    actor,
                    intent,
                    intent_hash,
                    "transport failed after the publish attempt",
                    invocations=None,
                )
            self._fail_job(tenant_id, job_id, actor, exc.code, exc.detail)
            raise
        data, adapter_meta = self._adapter_data(outcome.invocation)
        executed = bool(adapter_meta.get("executed"))
        dry_run = bool(adapter_meta.get("dry_run", True))
        if not executed or dry_run:
            self._fail_job(
                tenant_id,
                job_id,
                actor,
                ETSY_PUBLISH_NOT_EXECUTED,
                "the adapter did not execute the publish",
            )
            raise BusinessError(
                ETSY_PUBLISH_NOT_EXECUTED,
                "the adapter did not execute the publish; nothing was published",
            )
        state = str(data.get("state") or data.get("status") or "").lower()
        listing_id = _optional_text(data.get("listing_id"), "listing_id")
        ambiguous = (
            bool(data.get("uncertain"))
            or state in {"unknown", "pending", "processing", "in_progress"}
            or not listing_id
        )
        if ambiguous:
            return self._record_unknown_publish(
                tenant_id,
                job,
                actor,
                intent,
                intent_hash,
                f"provider returned ambiguous state {state or 'missing'!r}",
                invocations=outcome.invocation,
            )
        receipt = {
            "kind": "publish",
            "listing_id": listing_id,
            "state": state or "active",
            "provider_request_id": (
                data.get("provider_request_id") or data.get("receipt_id")
            ),
            "approval_id": approval.approval_id,
            "intent_hash": intent_hash,
            "invocation_id": outcome.invocation.invocation_id,
            "provider": adapter_meta.get("platform") or "etsy",
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            job.business_id,
            kind=ARTIFACT_PUBLISH_RECEIPT,
            name=f"{ARTIFACT_PUBLISH_RECEIPT}:{job_id}",
            body=receipt,
            job_id=job_id,
        )
        event = self._emit(
            tenant_id,
            job.business_id,
            EVENT_LISTING_PUBLISHED,
            {
                "status": STATUS_PUBLISHED,
                "job_id": job_id,
                "listing_id": listing_id,
                "receipt_artifact_id": artifact.artifact_id,
                "intent_hash": intent_hash,
                "provider_request_id": receipt["provider_request_id"],
            },
            actor=actor,
            key=f"etsy:listing:{job_id}:published",
        )
        job = jobs.finish(
            self._store,
            tenant_id,
            job_id,
            "completed",
            actor=actor,
            output={
                "stage": STATUS_PUBLISHED,
                "listing_id": listing_id,
                "approval_id": approval.approval_id,
                "intent_hash": intent_hash,
                "receipt_artifact_id": artifact.artifact_id,
                "provider_request_id": receipt["provider_request_id"],
            },
        )
        return self._record_from_event(
            "publish_listing",
            event,
            artifact=artifact,
            job=job,
            approval=approval,
            invocation=outcome.invocation,
            intent_hash=intent_hash,
            receipt=receipt,
        )

    # -- order, entitlement, support ----------------------------------
    def record_order(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        job_id: str,
        order_id: str,
        receipt_id: str,
        amount_minor: int,
        currency: str,
        buyer_ref: str,
        asset_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        self._require_scope(tenant_id, business_id)
        order = _text(order_id, "order_id", max_len=200)
        key = idempotency_key or f"etsy:order:{order}"
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("record_order", existing)
        job = jobs.get(self._store, tenant_id, job_id)
        if job.status != "completed":
            raise BusinessError(
                ETSY_NOT_PUBLISHED,
                f"listing job {job_id!r} is {job.status!r}; an order needs a"
                " published listing with a provider receipt",
            )
        draft = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_DRAFT}:{job_id}"
        )
        if draft is None:
            raise BusinessError(
                ETSY_NOT_PUBLISHED,
                f"listing job {job_id!r} has no draft record",
            )
        draft_body = _loads(draft.body)
        amount = _positive_int(amount_minor, "amount_minor")
        code = _currency(currency)
        version = _optional_text(asset_version, "asset_version") or str(
            draft_body.get("version") or ""
        )
        body = {
            "order_id": order,
            "job_id": job_id,
            "listing_id": draft_body.get("listing_id"),
            "receipt_id": _text(receipt_id, "receipt_id", max_len=200),
            "amount_minor": amount,
            "currency": code,
            "buyer_ref": _text(buyer_ref, "buyer_ref", max_len=200),
            "asset_version": version,
            "provider_receipt": True,
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_ORDER,
            name=f"{ARTIFACT_ORDER}:{order}",
            body=body,
            job_id=job_id,
        )
        entry_ids = self._append_double_entry(
            tenant_id,
            business_id,
            order,
            job_id,
            [
                ("etsy.receivable", "debit", amount),
                ("etsy.sales", "credit", amount),
            ],
            memo=f"Etsy sale order {order}",
            actor=actor,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_ORDER_RECORDED,
            {
                "status": STATUS_RECORDED,
                "order_id": order,
                "job_id": job_id,
                "artifact_id": artifact.artifact_id,
                "amount_minor": amount,
                "currency": code,
                "asset_version": version,
                "ledger_entry_ids": list(entry_ids),
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event(
            "record_order",
            event,
            artifact=artifact,
            job=job,
            ledger_entry_ids=entry_ids,
            detail={"order_id": order},
        )

    def grant_entitlement(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        order_id: str,
        version: str,
        download_ref: str,
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        self._require_scope(tenant_id, business_id)
        order = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_ORDER}:{order_id}"
        )
        if order is None:
            raise BusinessError(
                ETSY_ORDER_REQUIRED,
                f"no order {order_id!r} is recorded",
            )
        order_body = _loads(order.body)
        version_clean = _text(version, "version", max_len=200)
        if version_clean != order_body.get("asset_version"):
            raise BusinessError(
                ETSY_ASSET_VERSION_MISMATCH,
                f"entitlement version {version_clean!r} does not match the"
                f" order asset version {order_body.get('asset_version')!r}",
            )
        key = idempotency_key or f"etsy:entitlement:{order_id}:{version_clean}"
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("grant_entitlement", existing)
        body = {
            "entitlement_id": new_id("ent"),
            "order_id": order_id,
            "version": version_clean,
            "download_ref": _text(download_ref, "download_ref", max_len=1000),
            "immutable": True,
            "version_sha256": hashlib.sha256(
                version_clean.encode("utf-8")
            ).hexdigest(),
            "issued_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_ENTITLEMENT,
            name=f"{ARTIFACT_ENTITLEMENT}:{order_id}:{version_clean}",
            body=body,
            job_id=order.job_id,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_ENTITLEMENT_GRANTED,
            {
                "status": STATUS_RECORDED,
                "order_id": order_id,
                "version": version_clean,
                "artifact_id": artifact.artifact_id,
                "immutable": True,
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event(
            "grant_entitlement", event, artifact=artifact
        )

    def open_support_case(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        order_id: str,
        case_ref: str,
        reason: str,
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        self._require_scope(tenant_id, business_id)
        order = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_ORDER}:{order_id}"
        )
        if order is None:
            raise BusinessError(
                ETSY_ORDER_REQUIRED, f"no order {order_id!r} is recorded"
            )
        ref = _text(case_ref, "case_ref", max_len=200)
        key = idempotency_key or f"etsy:support:{ref}"
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("open_support_case", existing)
        body = {
            "case_ref": ref,
            "order_id": order_id,
            "reason": _text(reason, "reason", max_len=2000),
            "status": STATUS_OPEN,
            "opened_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_SUPPORT,
            name=f"{ARTIFACT_SUPPORT}:{ref}",
            body=body,
            job_id=order.job_id,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_SUPPORT_OPENED,
            {
                "case_ref": ref,
                "order_id": order_id,
                "artifact_id": artifact.artifact_id,
                "status": STATUS_OPEN,
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event(
            "open_support_case", event, artifact=artifact
        )

    # -- refund --------------------------------------------------------
    def request_refund(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        order_id: str,
        amount_minor: int,
        reason: str,
        idempotency_key: str,
        case_ref: str | None = None,
        expires_at: float | None = None,
        lease_seconds: float = 300.0,
    ) -> OperationRecord:
        self._require_scope(tenant_id, business_id)
        order = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_ORDER}:{order_id}"
        )
        if order is None:
            raise BusinessError(
                ETSY_ORDER_REQUIRED, f"no order {order_id!r} is recorded"
            )
        order_body = _loads(order.body)
        amount = _positive_int(amount_minor, "amount_minor")
        if amount > int(order_body.get("amount_minor") or 0):
            raise BusinessError(
                BAD_INPUT_CODE,
                "a refund cannot exceed the recorded order amount",
            )
        job_key = f"etsy.refund-job:{order_id}:{idempotency_key}"
        job, created = jobs.create(
            self._store,
            tenant_id=tenant_id,
            business_id=business_id,
            kind=REFUND_KIND,
            idempotency_key=job_key,
            input={
                "order_id": order_id,
                "amount_minor": amount,
                "reason": str(reason or ""),
                "case_ref": case_ref,
            },
            actor=actor,
        )
        if not created:
            return self._existing_refund_record(tenant_id, business_id, job)
        job = jobs.enqueue(self._store, tenant_id, job.job_id, actor=actor)
        job = jobs.claim(
            self._store,
            tenant_id,
            job.job_id,
            worker=f"etsy-operations:{actor}",
            lease_seconds=lease_seconds,
            actor=actor,
        )
        intent = self._refund_intent(business_id, job, order_body, amount, reason)
        intent_hash = approvals.intent_hash(intent)
        approval, _ = approvals.request(
            self._store,
            tenant_id=tenant_id,
            business_id=business_id,
            action=_REFUND_ACTION,
            requested_by=actor,
            idempotency_key=f"etsy:refund:{job.job_id}:approval",
            job_id=job.job_id,
            payload=intent,
            policy_version=REFUND_POLICY.policy_version,
            expires_at=expires_at,
        )
        intent_artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_REFUND_INTENT,
            name=f"{ARTIFACT_REFUND_INTENT}:{job.job_id}",
            body={
                "job_id": job.job_id,
                "approval_id": approval.approval_id,
                "intent": intent,
                "intent_hash": intent_hash,
                "policy_revision": REFUND_POLICY.policy_version,
            },
            job_id=job.job_id,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_REFUND_APPROVAL_REQUESTED,
            {
                "status": STATUS_AWAITING_APPROVAL,
                "job_id": job.job_id,
                "order_id": order_id,
                "approval_id": approval.approval_id,
                "intent_hash": intent_hash,
                "amount_minor": amount,
                "intent_artifact_id": intent_artifact.artifact_id,
            },
            actor=actor,
            key=f"etsy:refund:{job.job_id}:approval-requested",
        )
        return self._record_from_event(
            "request_refund",
            event,
            artifact=intent_artifact,
            job=jobs.get(self._store, tenant_id, job.job_id),
            approval=approval,
            intent_hash=intent_hash,
            detail={"order_id": order_id},
        )

    def approve_refund(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        decided_by: str,
        reason: str = "",
    ) -> OperationRecord:
        approval = approvals.decide(
            self._store,
            tenant_id=tenant_id,
            approval_id=approval_id,
            decision="approved",
            decided_by=decided_by,
            reason=reason,
        )
        if approval.job_id is None:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval_id!r} is not linked to a refund job",
            )
        event = self._emit(
            tenant_id,
            approval.business_id,
            EVENT_REFUND_APPROVED,
            {
                "status": STATUS_APPROVED,
                "job_id": approval.job_id,
                "approval_id": approval.approval_id,
                "request_hash": approval.request_hash,
                "decided_by": approval.decided_by,
            },
            actor=decided_by,
            key=f"etsy:refund:{approval.job_id}:approved",
        )
        return self._record_from_event(
            "approve_refund",
            event,
            job=jobs.get(self._store, tenant_id, approval.job_id),
            approval=approval,
            intent_hash=approval.request_hash,
        )

    def refund(
        self,
        *,
        tenant_id: str,
        job_id: str,
        actor: str,
        approval_id: str | None = None,
        idempotency_key: str | None = None,
        lease_seconds: float = 300.0,
    ) -> OperationRecord:
        job = jobs.get(self._store, tenant_id, job_id)
        if job.status == "completed":
            return self._completed_refund_record(tenant_id, job)
        if job.status == "unknown":
            raise BusinessError(
                ETSY_RECONCILIATION_REQUIRED,
                f"job {job_id!r} has an unknown refund outcome; reconcile it"
                " with an explicit provider receipt before any retry",
            )
        if job.status not in ("queued", "waiting_approval"):
            raise BusinessError(
                BAD_STATE_CODE,
                f"refund needs an approved, queued job (status={job.status!r})",
            )
        approval, intent, intent_hash = self._verified_refund_approval(
            tenant_id, job, approval_id
        )
        if job.status != "queued":
            raise BusinessError(
                BAD_STATE_CODE,
                f"refund needs the approved job to be queued"
                f" (status={job.status!r})",
            )
        self._require_adapter()
        job = jobs.claim(
            self._store,
            tenant_id,
            job_id,
            worker=f"etsy-operations:{actor}",
            lease_seconds=lease_seconds,
            actor=actor,
        )
        key = idempotency_key or f"etsy:refund:{job_id}:refund"
        try:
            outcome = self._gateway.invoke(
                tenant_id=tenant_id,
                business_id=job.business_id,
                actor=actor,
                tool=TOOL_ETSY_RECEIPT_REFUND,
                idempotency_key=key,
                policy=REFUND_POLICY,
                payload={
                    "receipt_id": intent["target"],
                    "amount_minor": intent["amount_minor"],
                    "reason": intent["parameters"].get("reason", ""),
                    "currency": intent["currency"],
                },
                dry_run=False,
                approval_id=approval.approval_id,
            )
        except BusinessError as exc:
            if exc.code == "adapter_transport_error":
                return self._record_unknown_refund(
                    tenant_id,
                    job,
                    actor,
                    intent,
                    intent_hash,
                    "transport failed after the refund attempt",
                )
            self._fail_job(tenant_id, job_id, actor, exc.code, exc.detail)
            raise
        data, adapter_meta = self._adapter_data(outcome.invocation)
        executed = bool(adapter_meta.get("executed"))
        dry_run = bool(adapter_meta.get("dry_run", True))
        if not executed or dry_run:
            self._fail_job(
                tenant_id,
                job_id,
                actor,
                ETSY_PUBLISH_NOT_EXECUTED,
                "the adapter did not execute the refund",
            )
            raise BusinessError(
                ETSY_PUBLISH_NOT_EXECUTED,
                "the adapter did not execute the refund; no money moved",
            )
        state = str(data.get("status") or data.get("state") or "").lower()
        ambiguous = bool(data.get("uncertain")) or state in {
            "unknown",
            "pending",
            "processing",
            "in_progress",
        }
        if ambiguous:
            return self._record_unknown_refund(
                tenant_id,
                job,
                actor,
                intent,
                intent_hash,
                f"provider returned ambiguous refund state {state or 'missing'!r}",
            )
        order_id = str(intent["parameters"].get("order_id") or "")
        amount = int(intent["amount_minor"])
        entry_ids = self._append_double_entry(
            tenant_id,
            job.business_id,
            f"{job_id}:refund",
            job_id,
            [
                ("etsy.refunds", "debit", amount),
                ("etsy.receivable", "credit", amount),
            ],
            memo=f"Etsy refund job {job_id} order {order_id}",
            actor=actor,
        )
        receipt = {
            "kind": "refund",
            "order_id": order_id,
            "receipt_id": intent["target"],
            "amount_minor": amount,
            "currency": intent["currency"],
            "state": state or "refunded",
            "refund_id": data.get("refund_id"),
            "approval_id": approval.approval_id,
            "intent_hash": intent_hash,
            "invocation_id": outcome.invocation.invocation_id,
            "provider": adapter_meta.get("platform") or "etsy",
            "ledger_entry_ids": list(entry_ids),
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            job.business_id,
            kind=ARTIFACT_REFUND_RECEIPT,
            name=f"{ARTIFACT_REFUND_RECEIPT}:{job_id}",
            body=receipt,
            job_id=job_id,
        )
        event = self._emit(
            tenant_id,
            job.business_id,
            EVENT_REFUND_SETTLED,
            {
                "status": STATUS_SETTLED,
                "job_id": job_id,
                "order_id": order_id,
                "amount_minor": amount,
                "receipt_artifact_id": artifact.artifact_id,
                "intent_hash": intent_hash,
                "ledger_entry_ids": list(entry_ids),
            },
            actor=actor,
            key=f"etsy:refund:{job_id}:settled",
        )
        job = jobs.finish(
            self._store,
            tenant_id,
            job_id,
            "completed",
            actor=actor,
            output={
                "stage": STATUS_SETTLED,
                "order_id": order_id,
                "amount_minor": amount,
                "approval_id": approval.approval_id,
                "intent_hash": intent_hash,
                "receipt_artifact_id": artifact.artifact_id,
            },
        )
        return self._record_from_event(
            "refund",
            event,
            artifact=artifact,
            job=job,
            approval=approval,
            invocation=outcome.invocation,
            intent_hash=intent_hash,
            receipt=receipt,
            ledger_entry_ids=entry_ids,
        )

    # -- payout and reconciliation ------------------------------------
    def record_payout(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        order_id: str,
        payout_ref: str,
        provider_receipt_ref: str,
        gross_minor: int,
        fee_minor: int,
        currency: str | None = None,
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        self._require_scope(tenant_id, business_id)
        order = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_ORDER}:{order_id}"
        )
        if order is None:
            raise BusinessError(
                ETSY_ORDER_REQUIRED, f"no order {order_id!r} is recorded"
            )
        order_body = _loads(order.body)
        ref = _text(payout_ref, "payout_ref", max_len=200)
        provider_ref = _text(
            provider_receipt_ref, "provider_receipt_ref", max_len=500
        )
        gross = _positive_int(gross_minor, "gross_minor")
        fee = _non_negative_int(fee_minor, "fee_minor")
        code = _currency(currency or order_body.get("currency") or "USD")
        key = idempotency_key or f"etsy:payout:{ref}"
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("record_payout", existing)
        refunded = self._refunded_total(tenant_id, business_id, order_id)
        if refunded > gross:
            raise BusinessError(
                BAD_STATE_CODE,
                f"refunded {refunded} exceeds gross {gross}; the payout cannot"
                " be closed",
            )
        net = gross - fee - refunded
        if net < 0:
            raise BusinessError(
                BAD_STATE_CODE,
                f"payout net {net} is negative after fee/tax/refund",
            )
        job_id = order.job_id
        rows: list[tuple[str, str, int]] = [
            ("etsy.cash", "debit", net),
        ]
        if fee:
            rows.append(("etsy.fees", "debit", fee))
        rows.append(("etsy.receivable", "credit", net + fee))
        entry_ids = self._append_double_entry(
            tenant_id,
            business_id,
            f"{order_id}:payout:{ref}",
            job_id,
            rows,
            memo=f"Etsy payout {ref} for order {order_id}",
            actor=actor,
        )
        body = {
            "payout_ref": ref,
            "provider_receipt_ref": provider_ref,
            "order_id": order_id,
            "gross_minor": gross,
            "fee_minor": fee,
            "refunded_minor": refunded,
            "net_minor": net,
            "currency": code,
            "ledger_entry_ids": list(entry_ids),
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_PAYOUT,
            name=f"{ARTIFACT_PAYOUT}:{ref}",
            body=body,
            job_id=job_id,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_PAYOUT_RECORDED,
            {
                "status": STATUS_RECORDED,
                "payout_ref": ref,
                "provider_receipt_ref": provider_ref,
                "order_id": order_id,
                "gross_minor": gross,
                "fee_minor": fee,
                "refunded_minor": refunded,
                "net_minor": net,
                "currency": code,
                "artifact_id": artifact.artifact_id,
                "ledger_entry_ids": list(entry_ids),
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event(
            "record_payout",
            event,
            artifact=artifact,
            ledger_entry_ids=entry_ids,
            detail={"order_id": order_id, "net_minor": net},
        )

    def reconcile_unknown(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        job_id: str,
        resolution: str,
        provider_ref: str,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> OperationRecord:
        """Resolve an ``unknown`` job ONLY from an explicit provider receipt."""
        self._require_scope(tenant_id, business_id)
        resolution_clean = _text(resolution, "resolution", max_len=30).lower()
        if resolution_clean not in REFUND_RESOLUTIONS:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"resolution must be one of {', '.join(sorted(REFUND_RESOLUTIONS))}",
            )
        provider = _optional_text(provider_ref, "provider_ref", max_len=500)
        if provider is None:
            raise BusinessError(
                ETSY_RECONCILIATION_RECEIPT_REQUIRED,
                "an explicit provider reconciliation receipt is required; an"
                " unknown outcome is never completed on ambiguity",
            )
        job = jobs.get(self._store, tenant_id, job_id)
        key = f"etsy:reconcile:{job_id}:{provider}"
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("reconcile_unknown", existing)
        if job.status != "unknown":
            raise BusinessError(
                ETSY_NOT_UNKNOWN,
                f"job {job_id!r} is {job.status!r}, not unknown",
            )
        body = {
            "job_id": job_id,
            "kind": job.kind,
            "resolution": resolution_clean,
            "provider_ref": provider,
            "recorded_at": _now_iso(),
        }
        executed = resolution_clean in EXECUTED_RESOLUTIONS
        entry_ids: tuple[str, ...] = ()
        if executed and job.kind == REFUND_KIND:
            intent_artifact = self._artifact_by_name(
                tenant_id,
                business_id,
                f"{ARTIFACT_REFUND_INTENT}:{job_id}",
            )
            intent = _loads(intent_artifact.body).get("intent") if intent_artifact else {}
            if not isinstance(intent, Mapping):
                intent = {}
            amount = _non_negative_int(
                amount_minor
                if amount_minor is not None
                else intent.get("amount_minor") or 0,
                "amount_minor",
            )
            code = _currency(
                currency or str(intent.get("currency") or "USD")
            )
            body.update(
                {
                    "amount_minor": amount,
                    "currency": code,
                    "receipt_id": intent.get("target"),
                    "order_id": (intent.get("parameters") or {}).get("order_id"),
                }
            )
            if amount > 0:
                entry_ids = self._append_double_entry(
                    tenant_id,
                    business_id,
                    f"{job_id}:reconciled-refund",
                    job_id,
                    [
                        ("etsy.refunds", "debit", amount),
                        ("etsy.receivable", "credit", amount),
                    ],
                    memo=f"Etsy reconciled refund {job_id} ({provider})",
                    actor=actor,
                )
        artifact_kind = (
            ARTIFACT_REFUND_RECEIPT
            if job.kind == REFUND_KIND
            else ARTIFACT_PUBLISH_RECEIPT
        )
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=artifact_kind,
            name=f"{artifact_kind}:{job_id}:reconciled",
            body={**body, "ledger_entry_ids": list(entry_ids)},
            job_id=job_id,
        )
        reconciliation = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_RECONCILIATION,
            name=f"{ARTIFACT_RECONCILIATION}:{job_id}:{provider}",
            body={
                **body,
                "receipt_artifact_id": artifact.artifact_id,
                "ledger_entry_ids": list(entry_ids),
            },
            job_id=job_id,
        )
        if executed:
            job = jobs.finish(
                self._store,
                tenant_id,
                job_id,
                "completed",
                actor=actor,
                output={
                    "stage": STATUS_RECONCILED,
                    "resolution": resolution_clean,
                    "provider_ref": provider,
                    "reconciliation_artifact_id": reconciliation.artifact_id,
                    "receipt_artifact_id": artifact.artifact_id,
                },
            )
        else:
            job = jobs.finish(
                self._store,
                tenant_id,
                job_id,
                "failed",
                actor=actor,
                error_code="ETSY_RECONCILED_NOT_EXECUTED",
                error_detail=f"provider {provider} confirmed no effect",
                output={
                    "stage": STATUS_RECONCILED,
                    "resolution": resolution_clean,
                    "provider_ref": provider,
                    "reconciliation_artifact_id": reconciliation.artifact_id,
                },
            )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_RECONCILED,
            {
                "status": STATUS_RECONCILED,
                "job_id": job_id,
                "resolution": resolution_clean,
                "provider_ref": provider,
                "completed": executed,
                "receipt_artifact_id": artifact.artifact_id,
                "reconciliation_artifact_id": reconciliation.artifact_id,
                "ledger_entry_ids": list(entry_ids),
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event(
            "reconcile_unknown",
            event,
            artifact=artifact,
            artifact_ids=(artifact.artifact_id, reconciliation.artifact_id),
            job=job,
            ledger_entry_ids=entry_ids,
            receipt={"kind": artifact_kind, "provider_ref": provider},
        )

    def reconcile_ledger(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        order_id: str,
        payout_ref: str,
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        """Prove the order's double-entry books balance against the payout."""
        self._require_scope(tenant_id, business_id)
        key = idempotency_key or f"etsy:ledger-reconcile:{order_id}:{payout_ref}"
        existing = self._find_event(tenant_id, business_id, key)
        if existing is not None:
            return self._record_from_event("reconcile_ledger", existing)
        payouts = self._artifacts_by_kind(tenant_id, business_id, ARTIFACT_PAYOUT)
        payout = next(
            (
                item
                for item in payouts
                if _loads(item.body).get("order_id") == order_id
                and _loads(item.body).get("payout_ref") == payout_ref
            ),
            None,
        )
        if payout is None:
            raise BusinessError(
                ETSY_ORDER_REQUIRED,
                f"no payout {payout_ref!r} is recorded for order {order_id!r}",
            )
        payout_body = _loads(payout.body)
        summary = self.ledger_balance(
            tenant_id, business_id, order_id=order_id
        )
        if not summary["balanced"]:
            raise BusinessError(
                ETSY_LEDGER_UNBALANCED,
                f"order {order_id!r} ledger is unbalanced:"
                f" debits={summary['debit_minor']}"
                f" credits={summary['credit_minor']}",
            )
        if summary["cash_minor"] != int(payout_body.get("net_minor") or 0):
            raise BusinessError(
                ETSY_LEDGER_UNBALANCED,
                f"cash {summary['cash_minor']} does not equal payout net"
                f" {payout_body.get('net_minor')}",
            )
        body = {
            "order_id": order_id,
            "payout_ref": payout_ref,
            "debit_minor": summary["debit_minor"],
            "credit_minor": summary["credit_minor"],
            "cash_minor": summary["cash_minor"],
            "net_minor": payout_body.get("net_minor"),
            "balanced": True,
            "accounts": summary["accounts"],
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            business_id,
            kind=ARTIFACT_LEDGER_RECONCILIATION,
            name=f"{ARTIFACT_LEDGER_RECONCILIATION}:{order_id}:{payout_ref}",
            body=body,
        )
        event = self._emit(
            tenant_id,
            business_id,
            EVENT_LEDGER_RECONCILED,
            {
                "status": STATUS_BALANCED,
                "order_id": order_id,
                "payout_ref": payout_ref,
                "artifact_id": artifact.artifact_id,
                "debit_minor": summary["debit_minor"],
                "credit_minor": summary["credit_minor"],
                "balanced": True,
            },
            actor=actor,
            key=key,
        )
        return self._record_from_event(
            "reconcile_ledger", event, artifact=artifact, detail=body
        )

    # -- observation ---------------------------------------------------
    def ledger_balance(
        self,
        tenant_id: str,
        business_id: str,
        *,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        """Recompute per-account debit/credit sums from raw ledger rows."""
        entries = ledger.list_entries(
            self._store, tenant_id, business_id=business_id
        )
        accounts: dict[str, dict[str, int]] = {}
        if order_id is not None:
            job_ids = self._order_job_ids(tenant_id, business_id, order_id)
            entries = [row for row in entries if row.job_id in job_ids]
        for row in entries:
            slot = accounts.setdefault(
                row.account, {"debit_minor": 0, "credit_minor": 0}
            )
            slot[f"{row.direction}_minor"] += int(row.amount_minor)
        debit = sum(item["debit_minor"] for item in accounts.values())
        credit = sum(item["credit_minor"] for item in accounts.values())
        return {
            "tenant_id": tenant_id,
            "business_id": business_id,
            "order_id": order_id,
            "debit_minor": debit,
            "credit_minor": credit,
            "balanced": debit == credit,
            "cash_minor": accounts.get("etsy.cash", {}).get("debit_minor", 0)
            - accounts.get("etsy.cash", {}).get("credit_minor", 0),
            "accounts": accounts,
            "entry_count": len(entries),
        }

    # -- internals -----------------------------------------------------
    def _require_scope(self, tenant_id: str, business_id: str) -> None:
        self._store.require_business(tenant_id, business_id)

    def _require_adapter(self) -> None:
        if self._registry is None:
            raise BusinessError(
                ETSY_ADAPTER_UNAVAILABLE,
                "no Etsy adapter is injected; the operations layer never falls"
                " back to a real provider or a fabricated success",
            )

    def _validate_listing(self, listing: Mapping[str, Any]) -> dict[str, Any]:
        body = _mapping(listing, "listing")
        title = _text(body.get("title"), "listing.title", max_len=140)
        description = _text(
            body.get("description"), "listing.description", max_len=5000
        )
        price = _positive_int(body.get("price_minor"), "listing.price_minor")
        files = body.get("digital_files")
        if not isinstance(files, (list, tuple)) or not files:
            raise BusinessError(
                BAD_INPUT_CODE,
                "listing.digital_files must name at least one deliverable",
            )
        tags = body.get("tags") or []
        if not isinstance(tags, (list, tuple)):
            raise BusinessError(
                BAD_INPUT_CODE, "listing.tags must be a list of strings"
            )
        version = _text(
            body.get("asset_version") or body.get("version"),
            "listing.asset_version",
            max_len=200,
        )
        clean = {
            "title": title,
            "description": description,
            "price_minor": price,
            "currency": _currency(body.get("currency") or "USD"),
            "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
            "digital_files": [
                str(name).strip() for name in files if str(name).strip()
            ],
            "asset_version": version,
            "who_made": str(body.get("who_made") or "i_did"),
            "when_made": str(body.get("when_made") or "made_to_order"),
            "is_supply": bool(body.get("is_supply", False)),
        }
        if not clean["digital_files"]:
            raise BusinessError(
                BAD_INPUT_CODE,
                "listing.digital_files must name at least one deliverable",
            )
        return clean

    def _listing_intent(
        self, business_id: str, draft: Mapping[str, Any]
    ) -> dict[str, Any]:
        return approvals.normalized_intent(
            action=APPROVAL_ACTION_PUBLISH,
            target=str(draft["listing_id"]),
            parameters={
                "listing_id": draft["listing_id"],
                "listing_payload_hash": draft["payload_hash"],
                "asset_id": draft["asset_id"],
                "niche_id": draft["niche_id"],
                "version": draft["version"],
                "business_id": business_id,
            },
            currency=draft["currency"],
            amount_minor=int(draft["price_minor"]),
            policy_revision=PUBLISH_POLICY.policy_version,
            business_id=business_id,
        )

    def _refund_intent(
        self,
        business_id: str,
        job: models.JobRun,
        order: Mapping[str, Any],
        amount_minor: int,
        reason: str,
    ) -> dict[str, Any]:
        return approvals.normalized_intent(
            action=_REFUND_ACTION,
            target=str(order.get("receipt_id") or ""),
            parameters={
                "order_id": order.get("order_id"),
                "job_id": job.job_id,
                "receipt_id": order.get("receipt_id"),
                "amount_minor": int(amount_minor),
                "reason": str(reason or "")[:500],
                "business_id": business_id,
            },
            currency=str(order.get("currency") or "USD"),
            amount_minor=int(amount_minor),
            policy_revision=REFUND_POLICY.policy_version,
            business_id=business_id,
        )

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
        return linked[-1] if linked else None

    def _pending_linked_approval(
        self, tenant_id: str, job: models.JobRun
    ) -> models.Approval | None:
        linked = [
            approval
            for approval in approvals.list_approvals(
                self._store, tenant_id, business_id=job.business_id
            )
            if approval.job_id == job.job_id
            and approval.action == APPROVAL_ACTION_PUBLISH
            and approval.status == "pending"
        ]
        return linked[-1] if linked else None

    def _verified_listing_approval(
        self,
        tenant_id: str,
        job: models.JobRun,
        approval_id: str | None,
    ) -> tuple[models.Approval, dict[str, Any], str]:
        approval = (
            approvals.get(self._store, tenant_id, approval_id)
            if approval_id is not None
            else self._linked_approval(tenant_id, job)
        )
        if approval is None:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                "publish needs an approved owner decision; none is linked",
            )
        if approval.job_id not in (None, job.job_id):
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval.approval_id!r} belongs to another job",
            )
        if approval.action != APPROVAL_ACTION_PUBLISH:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval.approval_id!r} is for"
                f" {approval.action!r}, not {APPROVAL_ACTION_PUBLISH!r}",
            )
        if approval.status != "approved":
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval.approval_id!r} is {approval.status!r},"
                " not approved",
            )
        if approval.expires_at is not None and approval.expires_at <= self._clock():
            raise BusinessError(
                "BUSINESS_APPROVAL_EXPIRED",
                f"approval {approval.approval_id!r} is past its expiry",
            )
        intent_artifact = self._artifact_by_name(
            tenant_id, job.business_id, f"{ARTIFACT_INTENT}:{job.job_id}"
        )
        draft_artifact = self._artifact_by_name(
            tenant_id, job.business_id, f"{ARTIFACT_DRAFT}:{job.job_id}"
        )
        if intent_artifact is None or draft_artifact is None:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                "the publish intent or draft record is missing; request a"
                " fresh approval",
            )
        intent_body = _loads(intent_artifact.body)
        intent = intent_body.get("intent")
        if not isinstance(intent, Mapping):
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the stored publish intent is not a mapping",
            )
        intent_hash = approvals.intent_hash(intent)
        if intent_hash != intent_body.get("intent_hash"):
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the stored publish intent hash does not match its content",
            )
        if approval.request_hash != intent_hash:
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the approval is bound to a different intent hash",
            )
        payload = approval.payload
        if not isinstance(payload, Mapping) or approvals.intent_hash(payload) != intent_hash:
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the approval payload is not the exact normalized intent",
            )
        current = self._listing_intent(job.business_id, _loads(draft_artifact.body))
        if approvals.intent_hash(current) != intent_hash:
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the draft payload changed after approval; the approval is"
                " invalid",
            )
        return approval, intent, intent_hash

    def _verified_refund_approval(
        self,
        tenant_id: str,
        job: models.JobRun,
        approval_id: str | None,
    ) -> tuple[models.Approval, dict[str, Any], str]:
        linked = [
            approval
            for approval in approvals.list_approvals(
                self._store, tenant_id, business_id=job.business_id
            )
            if approval.job_id == job.job_id and approval.action == _REFUND_ACTION
        ]
        approval = (
            approvals.get(self._store, tenant_id, approval_id)
            if approval_id is not None
            else (linked[-1] if linked else None)
        )
        if approval is None:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                "refund needs an approved owner decision; none is linked",
            )
        if approval.job_id not in (None, job.job_id):
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval.approval_id!r} belongs to another job",
            )
        if approval.action != _REFUND_ACTION:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval.approval_id!r} is for"
                f" {approval.action!r}, not {_REFUND_ACTION!r}",
            )
        if approval.status != "approved":
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                f"approval {approval.approval_id!r} is {approval.status!r},"
                " not approved",
            )
        if approval.expires_at is not None and approval.expires_at <= self._clock():
            raise BusinessError(
                "BUSINESS_APPROVAL_EXPIRED",
                f"approval {approval.approval_id!r} is past its expiry",
            )
        intent_artifact = self._artifact_by_name(
            tenant_id, job.business_id, f"{ARTIFACT_REFUND_INTENT}:{job.job_id}"
        )
        if intent_artifact is None:
            raise BusinessError(
                ETSY_APPROVAL_REQUIRED,
                "the refund intent record is missing; request a fresh approval",
            )
        intent_body = _loads(intent_artifact.body)
        intent = intent_body.get("intent")
        if not isinstance(intent, Mapping):
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the stored refund intent is not a mapping",
            )
        intent_hash = approvals.intent_hash(intent)
        if intent_hash != intent_body.get("intent_hash"):
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the stored refund intent hash does not match its content",
            )
        if approval.request_hash != intent_hash:
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the approval is bound to a different refund intent hash",
            )
        payload = approval.payload
        if not isinstance(payload, Mapping) or approvals.intent_hash(payload) != intent_hash:
            raise BusinessError(
                ETSY_APPROVAL_PAYLOAD_MISMATCH,
                "the approval payload is not the exact normalized refund intent",
            )
        return approval, intent, intent_hash

    def _record_unknown_publish(
        self,
        tenant_id: str,
        job: models.JobRun,
        actor: str,
        intent: Mapping[str, Any],
        intent_hash: str,
        reason: str,
        *,
        invocations: models.ToolInvocation | None,
    ) -> OperationRecord:
        body = {
            "kind": "publish",
            "job_id": job.job_id,
            "listing_id": intent.get("target"),
            "intent_hash": intent_hash,
            "status": STATUS_UNKNOWN,
            "unknown": True,
            "reason": reason,
            "invocation_id": (
                invocations.invocation_id if invocations is not None else None
            ),
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            job.business_id,
            kind=ARTIFACT_PUBLISH_RECEIPT,
            name=f"{ARTIFACT_PUBLISH_RECEIPT}:{job.job_id}:unknown",
            body={**body, "unknown": True},
            job_id=job.job_id,
        )
        event = self._emit(
            tenant_id,
            job.business_id,
            EVENT_LISTING_UNKNOWN,
            {
                "status": STATUS_UNKNOWN,
                "job_id": job.job_id,
                "listing_id": intent.get("target"),
                "intent_hash": intent_hash,
                "artifact_id": artifact.artifact_id,
                "reason": reason,
            },
            actor=actor,
            key=f"etsy:listing:{job.job_id}:unknown",
        )
        job = jobs.mark_unknown(
            self._store, tenant_id, job.job_id, actor=actor, reason=reason
        )
        return self._record_from_event(
            "publish_listing",
            event,
            artifact=artifact,
            job=job,
            invocation=invocations,
            intent_hash=intent_hash,
            receipt=body,
        )

    def _record_unknown_refund(
        self,
        tenant_id: str,
        job: models.JobRun,
        actor: str,
        intent: Mapping[str, Any],
        intent_hash: str,
        reason: str,
    ) -> OperationRecord:
        body = {
            "kind": "refund",
            "job_id": job.job_id,
            "amount_minor": intent.get("amount_minor"),
            "currency": intent.get("currency"),
            "intent_hash": intent_hash,
            "status": STATUS_UNKNOWN,
            "reason": reason,
            "unknown": True,
            "recorded_at": _now_iso(),
        }
        artifact = self._record_artifact_once(
            tenant_id,
            job.business_id,
            kind=ARTIFACT_REFUND_RECEIPT,
            name=f"{ARTIFACT_REFUND_RECEIPT}:{job.job_id}:unknown",
            body=body,
            job_id=job.job_id,
        )
        event = self._emit(
            tenant_id,
            job.business_id,
            EVENT_REFUND_UNKNOWN,
            {
                "status": STATUS_UNKNOWN,
                "job_id": job.job_id,
                "intent_hash": intent_hash,
                "artifact_id": artifact.artifact_id,
                "reason": reason,
            },
            actor=actor,
            key=f"etsy:refund:{job.job_id}:unknown",
        )
        job = jobs.mark_unknown(
            self._store, tenant_id, job.job_id, actor=actor, reason=reason
        )
        return self._record_from_event(
            "refund",
            event,
            artifact=artifact,
            job=job,
            intent_hash=intent_hash,
            receipt=body,
        )

    def _existing_listing_record(
        self, tenant_id: str, business_id: str, job: models.JobRun
    ) -> OperationRecord:
        approval = self._linked_approval(tenant_id, job)
        draft = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_DRAFT}:{job.job_id}"
        )
        intent_hash = approval.request_hash if approval is not None else None
        if job.status == "completed":
            return self._completed_listing_record(tenant_id, job)
        if job.status == "unknown":
            unknown = self._artifact_by_name(
                tenant_id,
                business_id,
                f"{ARTIFACT_PUBLISH_RECEIPT}:{job.job_id}:unknown",
            )
            return OperationRecord(
                operation="create_listing",
                status=STATUS_UNKNOWN,
                artifact=unknown or draft,
                job=job,
                approval=approval,
                intent_hash=intent_hash,
            )
        status = (
            STATUS_AWAITING_APPROVAL
            if approval is not None and approval.status == "pending"
            else STATUS_DRAFTED
        )
        return OperationRecord(
            operation="create_listing",
            status=status,
            artifact=draft,
            job=job,
            approval=approval,
            intent_hash=intent_hash,
        )

    def _completed_listing_record(
        self, tenant_id: str, job: models.JobRun
    ) -> OperationRecord:
        receipt = self._artifact_by_name(
            tenant_id,
            job.business_id,
            f"{ARTIFACT_PUBLISH_RECEIPT}:{job.job_id}",
        )
        if receipt is None:
            receipt = self._artifact_by_name(
                tenant_id,
                job.business_id,
                f"{ARTIFACT_PUBLISH_RECEIPT}:{job.job_id}:reconciled",
            )
        approval = self._linked_approval(tenant_id, job)
        body = _loads(receipt.body) if receipt is not None else {}
        return OperationRecord(
            operation="publish_listing",
            status=STATUS_PUBLISHED,
            artifact=receipt,
            job=job,
            approval=approval,
            intent_hash=body.get("intent_hash"),
            receipt=body,
        )

    def _existing_refund_record(
        self, tenant_id: str, business_id: str, job: models.JobRun
    ) -> OperationRecord:
        linked = [
            approval
            for approval in approvals.list_approvals(
                self._store, tenant_id, business_id=business_id
            )
            if approval.job_id == job.job_id and approval.action == _REFUND_ACTION
        ]
        approval = linked[-1] if linked else None
        if job.status == "completed":
            return self._completed_refund_record(tenant_id, job)
        if job.status == "unknown":
            unknown = self._artifact_by_name(
                tenant_id,
                business_id,
                f"{ARTIFACT_REFUND_RECEIPT}:{job.job_id}:unknown",
            )
            return OperationRecord(
                operation="request_refund",
                status=STATUS_UNKNOWN,
                artifact=unknown,
                job=job,
                approval=approval,
            )
        intent = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_REFUND_INTENT}:{job.job_id}"
        )
        return OperationRecord(
            operation="request_refund",
            status=(
                STATUS_AWAITING_APPROVAL
                if approval is not None and approval.status == "pending"
                else STATUS_DRAFTED
            ),
            artifact=intent,
            job=job,
            approval=approval,
            intent_hash=approval.request_hash if approval is not None else None,
        )

    def _completed_refund_record(
        self, tenant_id: str, job: models.JobRun
    ) -> OperationRecord:
        receipt = self._artifact_by_name(
            tenant_id,
            job.business_id,
            f"{ARTIFACT_REFUND_RECEIPT}:{job.job_id}",
        )
        if receipt is None:
            receipt = self._artifact_by_name(
                tenant_id,
                job.business_id,
                f"{ARTIFACT_REFUND_RECEIPT}:{job.job_id}:reconciled",
            )
        body = _loads(receipt.body) if receipt is not None else {}
        return OperationRecord(
            operation="refund",
            status=STATUS_SETTLED,
            artifact=receipt,
            job=job,
            approval=(
                approvals.get(self._store, tenant_id, body["approval_id"])
                if body.get("approval_id")
                else None
            ),
            intent_hash=body.get("intent_hash"),
            receipt=body,
            ledger_entry_ids=tuple(body.get("ledger_entry_ids") or ()),
        )

    def _record_from_event(
        self,
        operation: str,
        event: eventlog.EventEnvelope,
        *,
        artifact: models.Artifact | None = None,
        artifact_ids: tuple[str, ...] = (),
        job: models.JobRun | None = None,
        approval: models.Approval | None = None,
        invocation: models.ToolInvocation | None = None,
        intent_hash: str | None = None,
        receipt: Mapping[str, Any] | None = None,
        ledger_entry_ids: tuple[str, ...] = (),
        detail: Mapping[str, Any] | None = None,
    ) -> OperationRecord:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if artifact is None:
            candidate = (
                payload.get("artifact_id")
                or payload.get("receipt_artifact_id")
                or payload.get("draft_artifact_id")
            )
            if candidate:
                try:
                    artifact = self._store.get_artifact(
                        event.tenant_id, str(candidate)
                    )
                except BusinessError:
                    artifact = None
        return OperationRecord(
            operation=operation,
            status=str(payload.get("status") or payload.get("stage") or "recorded"),
            artifact=artifact,
            artifact_ids=artifact_ids,
            job=job,
            approval=approval,
            invocation=invocation,
            intent_hash=intent_hash or payload.get("intent_hash"),
            receipt=dict(receipt) if receipt is not None else None,
            ledger_entry_ids=ledger_entry_ids
            or tuple(payload.get("ledger_entry_ids") or ()),
            detail=dict(detail or {}),
        )

    def _emit(
        self,
        tenant_id: str,
        business_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        key: str,
    ) -> eventlog.EventEnvelope:
        return eventlog.append_event(
            self._store,
            tenant_id,
            business_id,
            event_type,
            dict(payload),
            actor=actor,
            idempotency_key=key,
            business_id=business_id,
        )

    def _find_event(
        self, tenant_id: str, business_id: str, key: str
    ) -> eventlog.EventEnvelope | None:
        for event in eventlog.read_events(self._store, tenant_id, business_id):
            if event.idempotency_key == key:
                return event
        return None

    def _record_artifact_once(
        self,
        tenant_id: str,
        business_id: str,
        *,
        kind: str,
        name: str,
        body: Mapping[str, Any],
        job_id: str | None = None,
    ) -> models.Artifact:
        existing = self._artifact_by_name(tenant_id, business_id, name)
        if existing is not None:
            return existing
        return self._store.record_artifact(
            tenant_id,
            business_id,
            kind=kind,
            name=name,
            body=_json_body(body),
            job_id=job_id,
            media_type="application/json",
        )

    def _artifact_by_name(
        self, tenant_id: str, business_id: str, name: str
    ) -> models.Artifact | None:
        for artifact in self._store.list_artifacts(
            tenant_id, business_id=business_id
        ):
            if artifact.name == name:
                return artifact
        return None

    def _asset_reference(
        self,
        tenant_id: str,
        business_id: str,
        asset_ref: str,
        version: str,
    ) -> models.Artifact | None:
        for artifact in self._artifacts_by_kind(
            tenant_id, business_id, ARTIFACT_ASSET
        ):
            body = _loads(artifact.body)
            if (
                body.get("asset_ref") == asset_ref
                and body.get("version") == version
            ):
                return artifact
        return None

    def _artifacts_by_kind(
        self, tenant_id: str, business_id: str, kind: str
    ) -> list[models.Artifact]:
        return [
            artifact
            for artifact in self._store.list_artifacts(
                tenant_id, business_id=business_id
            )
            if artifact.kind == kind
        ]

    def _adapter_data(
        self, invocation: models.ToolInvocation
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = invocation.result if isinstance(invocation.result, Mapping) else {}
        adapter = result.get("adapter") if isinstance(result, Mapping) else None
        if not isinstance(adapter, Mapping):
            return {}, {}
        data = adapter.get("data")
        return (dict(data) if isinstance(data, Mapping) else {}), dict(adapter)

    def _refunded_total(
        self, tenant_id: str, business_id: str, order_id: str
    ) -> int:
        total = 0
        for artifact in self._artifacts_by_kind(
            tenant_id, business_id, ARTIFACT_REFUND_RECEIPT
        ):
            body = _loads(artifact.body)
            if body.get("order_id") == order_id and body.get("amount_minor"):
                total += int(body["amount_minor"])
        return total

    def _order_job_ids(
        self, tenant_id: str, business_id: str, order_id: str
    ) -> set[str]:
        jobs_with_rows: set[str] = set()
        for artifact in self._artifacts_by_kind(
            tenant_id, business_id, ARTIFACT_ORDER
        ):
            body = _loads(artifact.body)
            if body.get("order_id") != order_id:
                continue
            if artifact.job_id:
                jobs_with_rows.add(artifact.job_id)
            for refund in self._artifacts_by_kind(
                tenant_id, business_id, ARTIFACT_REFUND_RECEIPT
            ):
                refund_body = _loads(refund.body)
                if refund_body.get("order_id") == order_id and refund.job_id:
                    jobs_with_rows.add(refund.job_id)
        return jobs_with_rows

    def _append_double_entry(
        self,
        tenant_id: str,
        business_id: str,
        key_prefix: str,
        job_id: str | None,
        rows: list[tuple[str, str, int]],
        *,
        memo: str,
        actor: str,
    ) -> tuple[str, ...]:
        if not rows:
            return ()
        entries: list[str] = []
        for index, (account, direction, amount) in enumerate(rows):
            entry, _ = ledger.append(
                self._store,
                tenant_id=tenant_id,
                business_id=business_id,
                account=account,
                direction=direction,
                amount_minor=amount,
                currency=self._currency_of(tenant_id, business_id, job_id),
                idempotency_key=f"{key_prefix}:ledger:{index}:{direction}:{account}",
                job_id=job_id,
                memo=memo,
                actor=actor,
            )
            entries.append(entry.entry_id)
        return tuple(entries)

    def _currency_of(
        self, tenant_id: str, business_id: str, job_id: str | None
    ) -> str:
        if job_id is None:
            return "USD"
        try:
            job = jobs.get(self._store, tenant_id, job_id)
        except BusinessError:
            return "USD"
        output = job.output if isinstance(job.output, Mapping) else {}
        intent_artifact = self._artifact_by_name(
            tenant_id, business_id, f"{ARTIFACT_REFUND_INTENT}:{job_id}"
        )
        if intent_artifact is not None:
            intent = _loads(intent_artifact.body).get("intent")
            if isinstance(intent, Mapping):
                value = intent.get("currency")
                if isinstance(value, str) and len(value) == 3:
                    return value.upper()
        for artifact_kind in (
            ARTIFACT_ORDER,
            ARTIFACT_REFUND_RECEIPT,
            ARTIFACT_DRAFT,
        ):
            for artifact in self._artifacts_by_kind(
                tenant_id, business_id, artifact_kind
            ):
                body = _loads(artifact.body)
                if body.get("job_id") == job_id or artifact.job_id == job_id:
                    value = body.get("currency")
                    if isinstance(value, str) and len(value) == 3:
                        return value.upper()
        value = output.get("currency")
        if isinstance(value, str) and len(value) == 3:
            return value.upper()
        return "USD"

    def _fail_job(
        self,
        tenant_id: str,
        job_id: str,
        actor: str,
        code: str,
        detail: str,
    ) -> None:
        try:
            jobs.finish(
                self._store,
                tenant_id,
                job_id,
                "failed",
                actor=actor,
                error_code=code,
                error_detail=detail,
            )
        except BusinessError:
            pass


def _now_iso() -> str:
    from .util import now_iso

    return now_iso()


__all__ = [
    "ARTIFACT_ASSET",
    "ARTIFACT_DRAFT",
    "ARTIFACT_ENTITLEMENT",
    "ARTIFACT_INTENT",
    "ARTIFACT_LEDGER_RECONCILIATION",
    "ARTIFACT_NICHE",
    "ARTIFACT_ORDER",
    "ARTIFACT_PAYOUT",
    "ARTIFACT_PUBLISH_RECEIPT",
    "ARTIFACT_RECONCILIATION",
    "ARTIFACT_REFUND_INTENT",
    "ARTIFACT_REFUND_RECEIPT",
    "ARTIFACT_SUPPORT",
    "ETSY_ADAPTER_UNAVAILABLE",
    "ETSY_APPROVAL_PAYLOAD_MISMATCH",
    "ETSY_APPROVAL_REQUIRED",
    "ETSY_ASSET_REQUIRED",
    "ETSY_ASSET_VERSION_MISMATCH",
    "ETSY_BAD_INPUT",
    "ETSY_INLINE_ART_FORBIDDEN",
    "ETSY_LEDGER_UNBALANCED",
    "ETSY_NOT_PUBLISHED",
    "ETSY_NOT_UNKNOWN",
    "ETSY_ORDER_REQUIRED",
    "ETSY_PAID_ART_GUARD",
    "ETSY_PROVIDER_RECEIPT_INVALID",
    "ETSY_PUBLISH_NOT_EXECUTED",
    "ETSY_RECONCILIATION_RECEIPT_REQUIRED",
    "ETSY_RECONCILIATION_REQUIRED",
    "EVENT_ASSET_REFERENCED",
    "EVENT_ENTITLEMENT_GRANTED",
    "EVENT_LEDGER_RECONCILED",
    "EVENT_LISTING_APPROVAL_REQUESTED",
    "EVENT_LISTING_APPROVED",
    "EVENT_LISTING_PUBLISHED",
    "EVENT_LISTING_UNKNOWN",
    "EVENT_NICHE_RECORDED",
    "EVENT_ORDER_RECORDED",
    "EVENT_PAYOUT_RECORDED",
    "EVENT_RECONCILED",
    "EVENT_REFUND_APPROVAL_REQUESTED",
    "EVENT_REFUND_APPROVED",
    "EVENT_REFUND_SETTLED",
    "EVENT_REFUND_UNKNOWN",
    "EVENT_SUPPORT_OPENED",
    "EtsyOperations",
    "InMemoryEtsySandboxTransport",
    "OperationRecord",
    "REFUND_KIND",
    "REFUND_POLICY",
    "SCHEMA",
    "STATUS_APPROVED",
    "STATUS_AWAITING_APPROVAL",
    "STATUS_BALANCED",
    "STATUS_DRAFTED",
    "STATUS_OPEN",
    "STATUS_PUBLISHED",
    "STATUS_RECORDED",
    "STATUS_RECONCILED",
    "STATUS_SETTLED",
    "STATUS_UNKNOWN",
    "sandbox_etsy_adapter",
]
