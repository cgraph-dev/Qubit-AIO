"""Marketing and social campaign lifecycle (P5-04).

A campaign moves through explicit, durable states:

    draft -> brand/legal check -> human approval (exact payload hash)
          -> sandbox/fake publication -> publication receipt

This module never opens a network connection. Publication goes through an
injected adapter (``PublicationAdapter``) that must return a receipt naming the
venue and time; an adapter result without a receipt is a typed refusal, not a
success. A failed or unknown outcome stays ``unknown`` until it is reconciled
against a real receipt.

Guarantees:

* no publication without an approved, unexpired approval bound to the exact
  payload hash (a changed payload is a typed mismatch);
* a draft is distinct from a published campaign, and a duplicate publish
  returns the stored receipt without calling the adapter again;
* platform policy and a persisted rate limit are checked before the adapter;
* engagement/follower/like abuse is refused outright and flagged in content;
* every publication, unknown outcome and reconciliation is an append-only
  event on the existing Business OS event log (no new database).
"""
from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from voicebridge.integrations.platform_policy import (
    PolicyError,
    evaluate_action,
    get_policy,
)

from . import approvals, eventlog, models
from .errors import IDEMPOTENCY_CONFLICT_CODE, BAD_INPUT_CODE, BusinessError
from .ids import fingerprint, new_id
from .util import require_text

# -- typed codes ---------------------------------------------------------
MARKETING_CAMPAIGN_NOT_FOUND_CODE = "BUSINESS_MARKETING_CAMPAIGN_NOT_FOUND"
MARKETING_CONTENT_REFUSED_CODE = "BUSINESS_MARKETING_CONTENT_REFUSED"
MARKETING_APPROVAL_REQUIRED_CODE = "BUSINESS_MARKETING_APPROVAL_REQUIRED"
MARKETING_RATE_LIMITED_CODE = "BUSINESS_MARKETING_RATE_LIMITED"
MARKETING_RECEIPT_REQUIRED_CODE = "BUSINESS_MARKETING_RECEIPT_REQUIRED"
MARKETING_RECEIPT_MISMATCH_CODE = "BUSINESS_MARKETING_RECEIPT_MISMATCH"
MARKETING_UNKNOWN_CODE = "BUSINESS_MARKETING_PUBLICATION_UNKNOWN"
MARKETING_FAILED_CODE = "BUSINESS_MARKETING_PUBLICATION_FAILED"
MARKETING_ENGAGEMENT_REFUSED_CODE = "BUSINESS_MARKETING_ENGAGEMENT_REFUSED"
MARKETING_NOT_RECONCILABLE_CODE = "BUSINESS_MARKETING_NOT_RECONCILABLE"
MARKETING_ENVIRONMENT_REFUSED_CODE = "BUSINESS_MARKETING_ENVIRONMENT_REFUSED"

PUBLISH_ACTION = "social.post.publish"
DRAFT_CREATED_EVENT = "marketing.draft.created"
PUBLISHED_EVENT = "marketing.published"
PUBLICATION_UNKNOWN_EVENT = "marketing.publication.unknown"
PUBLICATION_FAILED_EVENT = "marketing.publication.failed"

STATUS_DRAFT = "draft"
STATUS_UNKNOWN = "unknown"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"
CAMPAIGN_STATUSES = (STATUS_DRAFT, STATUS_UNKNOWN, STATUS_PUBLISHED, STATUS_FAILED)

SANDBOX_ENVIRONMENTS = ("sandbox", "fake")

DEFAULT_RATE_LIMIT = 5
DEFAULT_RATE_WINDOW_SECONDS = 3600.0
DEFAULT_APPROVAL_TTL_SECONDS = 3600.0

#: Conservative per-venue text bounds for the content check. These are local
#: authoring limits, not a claim about any platform's current API limit.
VENUE_MAX_CHARS = {
    "x": 280,
    "threads": 500,
    "instagram": 2200,
    "meta": 2200,
    "linkedin": 3000,
    "tiktok": 2200,
    "youtube": 5000,
    "pinterest": 500,
    "reddit": 40000,
}

_PROHIBITED_CLAIMS = (
    "guaranteed cure",
    "cures cancer",
    "risk-free investment",
    "risk free investment",
    "get rich quick",
    "instant wealth",
    "fda approved",
    "miracle cure",
)
_FAKE_ENGAGEMENT = (
    "buy followers",
    "buy likes",
    "buy views",
    "buy subscribers",
    "follow for follow",
    "like for like",
    "engagement pod",
    "comment to win",
)
_SPAM_PHRASES = (
    "act now!!!",
    "100% free!!!",
    "click here to win",
)
_DISCLOSURE_MARKERS = (
    "#ad",
    "#sponsored",
    "#paidpartnership",
    "advertisement",
    "sponsored",
    "paid partnership",
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]+")


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _epoch(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _events(
    store: "Any", tenant_id: str, campaign_id: str
) -> list[eventlog.EventEnvelope]:
    return eventlog.read_events(store, tenant_id, campaign_id)


def _event_by_key(
    store: "Any", tenant_id: str, idempotency_key: str
) -> eventlog.EventEnvelope | None:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_event WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, idempotency_key),
        ).fetchone()
    return None if row is None else eventlog.EventEnvelope.from_row(row)


def _append(
    store: "Any",
    tenant_id: str,
    business_id: str,
    campaign_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    actor: str,
    idempotency_key: str,
    now: float | None = None,
) -> eventlog.EventEnvelope:
    return eventlog.append_event(
        store,
        tenant_id,
        campaign_id,
        event_type,
        dict(payload),
        actor=actor,
        idempotency_key=idempotency_key,
        business_id=business_id,
        now=now,
    )


# -- content / brand / legal --------------------------------------------
def check_content(
    text: str,
    *,
    venue: str,
    disclosure_required: bool = False,
    disclosure: str = "",
) -> dict[str, Any]:
    """Deterministic brand/legal check for one authored post."""
    clean = str(text or "").strip()
    venue_key = str(venue or "").strip().lower()
    max_chars = VENUE_MAX_CHARS.get(venue_key, 4000)
    lowered = clean.lower()
    findings: list[dict[str, str]] = []

    def add(code: str, detail: str, severity: str = "error") -> None:
        findings.append(
            {"code": code, "severity": severity, "detail": detail}
        )

    if not clean:
        add("empty", "post text is empty")
    if len(clean) > max_chars:
        add(
            "too_long",
            f"post text is {len(clean)} chars; {venue_key} allows {max_chars}",
        )
    for phrase in _PROHIBITED_CLAIMS:
        if phrase in lowered:
            add("prohibited_claim", f"prohibited claim: {phrase!r}")
    for phrase in _FAKE_ENGAGEMENT:
        if phrase in lowered:
            add("fake_engagement", f"engagement abuse: {phrase!r}")
    for phrase in _SPAM_PHRASES:
        if phrase in lowered:
            add("spam", f"spam pattern: {phrase!r}")
    hashtags = _HASHTAG_RE.findall(clean)
    if len(hashtags) > 10:
        add("spam", f"{len(hashtags)} hashtags is spammy; keep it to 10")
    links = _URL_RE.findall(clean)
    if len(links) > 5:
        add("spam", f"{len(links)} links is spammy; keep it to 5")
    letters = [char for char in clean if char.isalpha()]
    if len(clean) >= 40 and letters:
        upper = sum(1 for char in letters if char.isupper())
        if upper / len(letters) >= 0.7:
            add(
                "spam",
                "all-caps post reads as spam; use sentence casing",
            )
    disclosure_text = f"{disclosure} {clean}".lower()
    disclosure_present = any(
        marker in disclosure_text for marker in _DISCLOSURE_MARKERS
    )
    if disclosure_required and not disclosure_present:
        add(
            "missing_disclosure",
            "a paid/sponsored post needs #ad / #sponsored / paid partnership",
        )
    if not disclosure_required and not disclosure_present:
        add(
            "no_disclosure",
            "no sponsorship disclosure; add one if this is paid",
            severity="warning",
        )
    errors = [item for item in findings if item["severity"] == "error"]
    return {
        "ok": not errors,
        "findings": findings,
        "errors": errors,
        "venue": venue_key,
        "max_chars": max_chars,
        "disclosure_required": bool(disclosure_required),
        "disclosure_present": disclosure_present,
    }


# -- campaign records ----------------------------------------------------
@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    receipt_id: str
    campaign_id: str
    business_id: str
    venue: str
    payload_sha256: str
    published_at: str
    external_id: str
    environment: str
    approval_id: str
    reconciled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "campaign_id": self.campaign_id,
            "business_id": self.business_id,
            "venue": self.venue,
            "payload_sha256": self.payload_sha256,
            "published_at": self.published_at,
            "external_id": self.external_id,
            "environment": self.environment,
            "approval_id": self.approval_id,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True, slots=True)
class Campaign:
    campaign_id: str
    tenant_id: str
    business_id: str
    venue: str
    title: str
    text: str
    media: tuple[str, ...]
    disclosure: str
    disclosure_required: bool
    status: str
    payload_sha256: str
    created_at: str
    receipt: PublicationReceipt | None = None

    @property
    def published(self) -> bool:
        return self.status == STATUS_PUBLISHED

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "venue": self.venue,
            "title": self.title,
            "text": self.text,
            "media": list(self.media),
            "disclosure": self.disclosure,
            "disclosure_required": self.disclosure_required,
            "status": self.status,
            "payload_sha256": self.payload_sha256,
            "created_at": self.created_at,
            "receipt": self.receipt.as_dict() if self.receipt else None,
        }


def _payload(
    *,
    venue: str,
    title: str,
    text: str,
    media: tuple[str, ...],
    disclosure: str,
    disclosure_required: bool,
) -> dict[str, Any]:
    return {
        "venue": venue,
        "title": title,
        "text": text,
        "media": list(media),
        "disclosure": disclosure,
        "disclosure_required": bool(disclosure_required),
    }


def _payload_hash(payload: Mapping[str, Any]) -> str:
    return fingerprint(dict(payload))


def _receipt_from_payload(
    *, campaign_id: str, business_id: str, payload: Mapping[str, Any]
) -> PublicationReceipt | None:
    raw = payload.get("receipt")
    if not isinstance(raw, Mapping):
        return None
    try:
        return PublicationReceipt(
            receipt_id=str(raw["receipt_id"]),
            campaign_id=str(raw.get("campaign_id") or campaign_id),
            business_id=str(raw.get("business_id") or business_id),
            venue=str(raw["venue"]),
            payload_sha256=str(raw["payload_sha256"]),
            published_at=str(raw["published_at"]),
            external_id=str(raw["external_id"]),
            environment=str(raw.get("environment") or ""),
            approval_id=str(raw.get("approval_id") or ""),
            reconciled=bool(raw.get("reconciled", False)),
        )
    except (KeyError, TypeError):
        return None


def _campaign_from_events(
    store: "Any", tenant_id: str, campaign_id: str
) -> Campaign | None:
    draft: dict[str, Any] | None = None
    status = STATUS_DRAFT
    receipt: PublicationReceipt | None = None
    created_at = ""
    for envelope in _events(store, tenant_id, campaign_id):
        payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
        if envelope.type == DRAFT_CREATED_EVENT and draft is None:
            draft = dict(payload)
            created_at = envelope.occurred_at
        elif envelope.type == PUBLISHED_EVENT:
            status = STATUS_PUBLISHED
            receipt = _receipt_from_payload(
                campaign_id=campaign_id,
                business_id=str(payload.get("business_id") or ""),
                payload=payload,
            )
        elif envelope.type == PUBLICATION_UNKNOWN_EVENT and status != STATUS_PUBLISHED:
            status = STATUS_UNKNOWN
        elif envelope.type == PUBLICATION_FAILED_EVENT and status == STATUS_DRAFT:
            status = STATUS_FAILED
    if draft is None:
        return None
    venue = str(draft.get("venue") or "")
    return Campaign(
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        business_id=str(draft.get("business_id") or ""),
        venue=venue,
        title=str(draft.get("title") or ""),
        text=str(draft.get("text") or ""),
        media=tuple(str(item) for item in draft.get("media") or ()),
        disclosure=str(draft.get("disclosure") or ""),
        disclosure_required=bool(draft.get("disclosure_required")),
        status=status,
        payload_sha256=str(
            draft.get("payload_sha256")
            or _payload_hash(
                _payload(
                    venue=venue,
                    title=str(draft.get("title") or ""),
                    text=str(draft.get("text") or ""),
                    media=tuple(str(item) for item in draft.get("media") or ()),
                    disclosure=str(draft.get("disclosure") or ""),
                    disclosure_required=bool(
                        draft.get("disclosure_required")
                    ),
                )
            )
        ),
        created_at=created_at,
        receipt=receipt,
    )


def get_campaign(store: "Any", tenant_id: str, campaign_id: str) -> Campaign:
    campaign = _campaign_from_events(store, tenant_id, campaign_id)
    if campaign is None:
        raise BusinessError(
            MARKETING_CAMPAIGN_NOT_FOUND_CODE,
            f"no campaign {campaign_id!r} in tenant {tenant_id!r}",
        )
    return campaign


def list_campaigns(
    store: "Any", tenant_id: str, *, business_id: str | None = None
) -> list[Campaign]:
    with store.read() as conn:
        rows = conn.execute(
            "SELECT DISTINCT aggregate_id FROM bos_event"
            " WHERE tenant_id=? AND type=? ORDER BY aggregate_id",
            (tenant_id, DRAFT_CREATED_EVENT),
        ).fetchall()
    campaigns: list[Campaign] = []
    for row in rows:
        campaign = _campaign_from_events(store, tenant_id, row["aggregate_id"])
        if campaign is None:
            continue
        if business_id is not None and campaign.business_id != business_id:
            continue
        campaigns.append(campaign)
    return campaigns


def _policy_venue(venue: str) -> str:
    clean = _text(venue, "venue", max_len=40).lower()
    try:
        get_policy(clean)
    except PolicyError as exc:
        raise BusinessError(MARKETING_CONTENT_REFUSED_CODE, exc.detail) from exc
    decision = evaluate_action(clean, "publish")
    if not decision.allowed:
        raise BusinessError(
            MARKETING_CONTENT_REFUSED_CODE,
            decision.reason,
        )
    return clean


def create_draft(
    store: "Any",
    tenant_id: str,
    business_id: str,
    *,
    venue: str,
    text: str,
    title: str = "",
    media: tuple[str, ...] | list[str] = (),
    disclosure: str = "",
    disclosure_required: bool = False,
    actor: str,
    idempotency_key: str,
    now: float | None = None,
) -> tuple[Campaign, bool]:
    """Create a campaign draft after the brand/legal check passes."""
    store.require_business(tenant_id, business_id)
    clean_venue = _policy_venue(venue)
    clean_text = _text(text, "text", max_len=40000)
    clean_title = str(title or "")[:500]
    clean_media = tuple(str(item) for item in media if str(item).strip())
    clean_disclosure = str(disclosure or "")[:200]
    check = check_content(
        clean_text,
        venue=clean_venue,
        disclosure_required=disclosure_required,
        disclosure=clean_disclosure,
    )
    if not check["ok"]:
        raise BusinessError(
            MARKETING_CONTENT_REFUSED_CODE,
            "draft failed the brand/legal check: "
            + "; ".join(item["detail"] for item in check["errors"]),
        )
    key = _text(idempotency_key, "idempotency key", max_len=300)
    event_key = f"marketing:draft:{key}"
    body = _payload(
        venue=clean_venue,
        title=clean_title,
        text=clean_text,
        media=clean_media,
        disclosure=clean_disclosure,
        disclosure_required=disclosure_required,
    )
    payload_sha256 = _payload_hash(body)
    existing = _event_by_key(store, tenant_id, event_key)
    if existing is not None:
        if existing.type != DRAFT_CREATED_EVENT:
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                "the idempotency key was used for a different operation",
            )
        if existing.payload_hash != payload_sha256:
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                "the idempotency key was reused with a different draft",
            )
        return get_campaign(store, tenant_id, existing.aggregate_id), False
    campaign_id = new_id("cmp")
    _append(
        store,
        tenant_id,
        business_id,
        campaign_id,
        DRAFT_CREATED_EVENT,
        {
            **body,
            "campaign_id": campaign_id,
            "business_id": business_id,
            "payload_sha256": payload_sha256,
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=event_key,
        now=now,
    )
    return get_campaign(store, tenant_id, campaign_id), True


def _publication_intent(
    campaign: Campaign, *, policy_version: str
) -> dict[str, Any]:
    return approvals.normalized_intent(
        PUBLISH_ACTION,
        f"{campaign.venue}:{campaign.campaign_id}",
        {
            "campaign_id": campaign.campaign_id,
            "venue": campaign.venue,
            "payload_sha256": campaign.payload_sha256,
            "media_count": len(campaign.media),
            "text": campaign.text,
        },
        "USD",
        0,
        policy_version,
        business_id=campaign.business_id,
    )


def request_approval(
    store: "Any",
    tenant_id: str,
    campaign_id: str,
    *,
    requested_by: str,
    idempotency_key: str,
    policy_version: str = "v1",
    expires_at: float | None = None,
    now: float | None = None,
) -> models.Approval:
    """Request human approval bound to the exact campaign payload hash."""
    campaign = get_campaign(store, tenant_id, campaign_id)
    if campaign.status in (STATUS_PUBLISHED, STATUS_UNKNOWN):
        raise BusinessError(
            MARKETING_CONTENT_REFUSED_CODE,
            f"campaign is {campaign.status!r}; only a draft or a failed"
            " attempt can be approved",
        )
    _policy_venue(campaign.venue)
    check = check_content(
        campaign.text,
        venue=campaign.venue,
        disclosure_required=campaign.disclosure_required,
        disclosure=campaign.disclosure,
    )
    if not check["ok"]:
        raise BusinessError(
            MARKETING_CONTENT_REFUSED_CODE,
            "campaign failed the brand/legal check: "
            + "; ".join(item["detail"] for item in check["errors"]),
        )
    moment = _epoch(now)
    expiry = (
        float(expires_at)
        if expires_at is not None
        else moment + DEFAULT_APPROVAL_TTL_SECONDS
    )
    intent = _publication_intent(campaign, policy_version=policy_version)
    approval, _created = approvals.request(
        store,
        tenant_id=tenant_id,
        business_id=campaign.business_id,
        action=PUBLISH_ACTION,
        requested_by=_text(requested_by, "requested_by", max_len=200),
        idempotency_key=_text(idempotency_key, "key", max_len=300),
        payload=intent,
        policy_version=policy_version,
        expires_at=expiry,
    )
    return approval


@runtime_checkable
class PublicationAdapter(Protocol):
    """The only publication seam. Implementations are injected by a caller."""

    def publish(
        self,
        *,
        campaign_id: str,
        tenant_id: str,
        business_id: str,
        venue: str,
        payload: Mapping[str, Any],
        payload_sha256: str,
        idempotency_key: str,
        approval_id: str,
    ) -> Mapping[str, Any]:
        ...  # pragma: no cover - protocol


class PublicationUnknown(Exception):
    """The adapter cannot tell whether the post was published."""


def _published_events(
    store: "Any", tenant_id: str
) -> list[tuple[eventlog.EventEnvelope, dict[str, Any]]]:
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM bos_event WHERE tenant_id=? AND type=?"
            " ORDER BY occurred_at, event_id",
            (tenant_id, PUBLISHED_EVENT),
        ).fetchall()
    result: list[tuple[eventlog.EventEnvelope, dict[str, Any]]] = []
    for row in rows:
        envelope = eventlog.EventEnvelope.from_row(row)
        payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
        result.append((envelope, dict(payload)))
    return result


def _rate_check(
    store: "Any",
    tenant_id: str,
    venue: str,
    *,
    moment: float,
    rate_limit: int,
    rate_window_seconds: float,
) -> None:
    if not isinstance(rate_limit, int) or isinstance(rate_limit, bool):
        raise BusinessError(BAD_INPUT_CODE, "rate_limit must be an integer")
    if rate_limit <= 0 or rate_window_seconds <= 0:
        raise BusinessError(
            BAD_INPUT_CODE, "rate limit and window must be positive"
        )
    cutoff = moment - float(rate_window_seconds)
    count = 0
    for envelope, payload in _published_events(store, tenant_id):
        if str(payload.get("venue") or "") != venue:
            continue
        published = _parse_iso(envelope.occurred_at)
        if published is not None and published.timestamp() >= cutoff:
            count += 1
    if count >= rate_limit:
        raise BusinessError(
            MARKETING_RATE_LIMITED_CODE,
            f"{venue} already published {count} post(s) in the last"
            f" {rate_window_seconds:g}s; limit is {rate_limit}",
        )


def _validate_receipt(
    *,
    campaign: Campaign,
    result: Mapping[str, Any],
    approval_id: str,
) -> PublicationReceipt:
    venue = str(result.get("venue") or "").strip().lower()
    external_id = str(result.get("external_id") or "").strip()
    published_at = str(result.get("published_at") or "").strip()
    environment = str(result.get("environment") or "").strip().lower()
    payload_sha256 = str(result.get("payload_sha256") or "").strip()
    if not venue or not external_id or not published_at:
        raise BusinessError(
            MARKETING_RECEIPT_REQUIRED_CODE,
            "the publication adapter returned no receipt (venue, external_id"
            " and published_at are required)",
        )
    if venue != campaign.venue:
        raise BusinessError(
            MARKETING_RECEIPT_MISMATCH_CODE,
            f"receipt venue {venue!r} does not match campaign venue"
            f" {campaign.venue!r}",
        )
    if payload_sha256 and payload_sha256 != campaign.payload_sha256:
        raise BusinessError(
            MARKETING_RECEIPT_MISMATCH_CODE,
            "receipt payload hash does not match the approved payload",
        )
    if _parse_iso(published_at) is None:
        raise BusinessError(
            MARKETING_RECEIPT_REQUIRED_CODE,
            "receipt published_at is not an ISO-8601 timestamp",
        )
    if environment not in SANDBOX_ENVIRONMENTS:
        raise BusinessError(
            MARKETING_ENVIRONMENT_REFUSED_CODE,
            f"publication environment {environment or '<missing>'!r} is not a"
            " sandbox/fake venue; live publication is out of scope",
        )
    return PublicationReceipt(
        receipt_id=str(result.get("receipt_id") or new_id("rcpt")),
        campaign_id=campaign.campaign_id,
        business_id=campaign.business_id,
        venue=venue,
        payload_sha256=payload_sha256 or campaign.payload_sha256,
        published_at=published_at,
        external_id=external_id,
        environment=environment,
        approval_id=approval_id,
    )


def publish(
    store: "Any",
    tenant_id: str,
    campaign_id: str,
    *,
    adapter: PublicationAdapter,
    approval_id: str,
    actor: str,
    idempotency_key: str,
    policy_version: str = "v1",
    rate_limit: int = DEFAULT_RATE_LIMIT,
    rate_window_seconds: float = DEFAULT_RATE_WINDOW_SECONDS,
    now: float | None = None,
) -> tuple[PublicationReceipt, bool]:
    """Publish one approved campaign through the injected sandbox adapter."""
    campaign = get_campaign(store, tenant_id, campaign_id)
    if campaign.status == STATUS_PUBLISHED and campaign.receipt is not None:
        return campaign.receipt, False
    if campaign.status == STATUS_UNKNOWN:
        raise BusinessError(
            MARKETING_UNKNOWN_CODE,
            "the previous publication outcome is unknown; reconcile it before"
            " attempting another publish",
        )
    if not str(approval_id or "").strip():
        raise BusinessError(
            MARKETING_APPROVAL_REQUIRED_CODE,
            "no approval_id was supplied; a campaign cannot self-approve",
        )
    _policy_venue(campaign.venue)
    check = check_content(
        campaign.text,
        venue=campaign.venue,
        disclosure_required=campaign.disclosure_required,
        disclosure=campaign.disclosure,
    )
    if not check["ok"]:
        raise BusinessError(
            MARKETING_CONTENT_REFUSED_CODE,
            "campaign failed the brand/legal check: "
            + "; ".join(item["detail"] for item in check["errors"]),
        )
    intent = _publication_intent(campaign, policy_version=policy_version)
    approvals.verify_execution(
        store,
        tenant_id,
        approval_id,
        intent,
        now=now,
        current_policy_version=policy_version,
    )
    moment = _epoch(now)
    _rate_check(
        store,
        tenant_id,
        campaign.venue,
        moment=moment,
        rate_limit=rate_limit,
        rate_window_seconds=rate_window_seconds,
    )
    key = _text(idempotency_key, "idempotency key", max_len=300)
    body = _payload(
        venue=campaign.venue,
        title=campaign.title,
        text=campaign.text,
        media=campaign.media,
        disclosure=campaign.disclosure,
        disclosure_required=campaign.disclosure_required,
    )
    publish_key = f"marketing:publish:{key}"
    try:
        result = adapter.publish(
            campaign_id=campaign.campaign_id,
            tenant_id=tenant_id,
            business_id=campaign.business_id,
            venue=campaign.venue,
            payload=body,
            payload_sha256=campaign.payload_sha256,
            idempotency_key=key,
            approval_id=approval_id,
        )
    except PublicationUnknown as exc:
        _append(
            store,
            tenant_id,
            campaign.business_id,
            campaign.campaign_id,
            PUBLICATION_UNKNOWN_EVENT,
            {
                "campaign_id": campaign.campaign_id,
                "venue": campaign.venue,
                "payload_sha256": campaign.payload_sha256,
                "approval_id": approval_id,
                "detail": str(exc)[:500],
            },
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=f"{publish_key}:unknown",
            now=now,
        )
        raise BusinessError(
            MARKETING_UNKNOWN_CODE,
            "the adapter reported an unknown publication outcome; reconcile"
            " against a provider receipt before retrying",
        ) from exc
    except BusinessError:
        raise
    except Exception as exc:  # noqa: BLE001 - adapter boundary
        _append(
            store,
            tenant_id,
            campaign.business_id,
            campaign.campaign_id,
            PUBLICATION_FAILED_EVENT,
            {
                "campaign_id": campaign.campaign_id,
                "venue": campaign.venue,
                "payload_sha256": campaign.payload_sha256,
                "approval_id": approval_id,
                "error": type(exc).__name__,
                "detail": str(exc)[:500],
            },
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=f"{publish_key}:failed",
            now=now,
        )
        raise BusinessError(
            MARKETING_FAILED_CODE,
            "the publication adapter failed; the campaign is not published",
        ) from exc
    if not isinstance(result, Mapping):
        _append(
            store,
            tenant_id,
            campaign.business_id,
            campaign.campaign_id,
            PUBLICATION_FAILED_EVENT,
            {
                "campaign_id": campaign.campaign_id,
                "venue": campaign.venue,
                "payload_sha256": campaign.payload_sha256,
                "approval_id": approval_id,
                "error": "receipt_required",
                "detail": "adapter returned no receipt mapping",
            },
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=f"{publish_key}:failed",
            now=now,
        )
        raise BusinessError(
            MARKETING_RECEIPT_REQUIRED_CODE,
            "the publication adapter returned no receipt mapping",
        )
    try:
        receipt = _validate_receipt(
            campaign=campaign, result=result, approval_id=approval_id
        )
    except BusinessError as exc:
        _append(
            store,
            tenant_id,
            campaign.business_id,
            campaign.campaign_id,
            PUBLICATION_FAILED_EVENT,
            {
                "campaign_id": campaign.campaign_id,
                "venue": campaign.venue,
                "payload_sha256": campaign.payload_sha256,
                "approval_id": approval_id,
                "error": exc.code,
                "detail": exc.detail[:500],
            },
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=f"{publish_key}:failed",
            now=now,
        )
        raise
    _append(
        store,
        tenant_id,
        campaign.business_id,
        campaign.campaign_id,
        PUBLISHED_EVENT,
        {
            "campaign_id": campaign.campaign_id,
            "business_id": campaign.business_id,
            "venue": receipt.venue,
            "payload_sha256": receipt.payload_sha256,
            "published_at": receipt.published_at,
            "external_id": receipt.external_id,
            "environment": receipt.environment,
            "approval_id": approval_id,
            "reconciled": False,
            "receipt": receipt.as_dict(),
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=publish_key,
        now=now,
    )
    return receipt, True


def reconcile(
    store: "Any",
    tenant_id: str,
    campaign_id: str,
    *,
    receipt: Mapping[str, Any],
    actor: str,
    idempotency_key: str,
    policy_version: str = "v1",
    now: float | None = None,
) -> PublicationReceipt:
    """Resolve an unknown publication against an exact provider receipt."""
    campaign = get_campaign(store, tenant_id, campaign_id)
    if campaign.status != STATUS_UNKNOWN:
        raise BusinessError(
            MARKETING_NOT_RECONCILABLE_CODE,
            f"campaign is {campaign.status!r}; only an unknown outcome can be"
            " reconciled",
        )
    if not isinstance(receipt, Mapping):
        raise BusinessError(
            MARKETING_RECEIPT_REQUIRED_CODE,
            "reconciliation requires a receipt mapping",
        )
    approval_id = _text(
        receipt.get("approval_id"), "receipt.approval_id", max_len=200
    )
    approvals.verify_execution(
        store,
        tenant_id,
        approval_id,
        _publication_intent(campaign, policy_version=policy_version),
        now=now,
        current_policy_version=policy_version,
    )
    validated = _validate_receipt(
        campaign=campaign, result=receipt, approval_id=approval_id
    )
    reconciled = replace(validated, reconciled=True)
    _append(
        store,
        tenant_id,
        campaign.business_id,
        campaign.campaign_id,
        PUBLISHED_EVENT,
        {
            "campaign_id": campaign.campaign_id,
            "business_id": campaign.business_id,
            "venue": reconciled.venue,
            "payload_sha256": reconciled.payload_sha256,
            "published_at": reconciled.published_at,
            "external_id": reconciled.external_id,
            "environment": reconciled.environment,
            "approval_id": reconciled.approval_id,
            "reconciled": True,
            "receipt": reconciled.as_dict(),
        },
        actor=_text(actor, "actor", max_len=200),
        idempotency_key=(
            f"marketing:reconcile:"
            f"{_text(idempotency_key, 'key', max_len=300)}"
        ),
        now=now,
    )
    return reconciled


def record_engagement(*_args: Any, **_kwargs: Any) -> None:
    """Fake engagement is never a supported capability."""
    raise BusinessError(
        MARKETING_ENGAGEMENT_REFUSED_CODE,
        "fake likes/followers/reviews are forbidden; marketing may only"
        " publish approved owned content",
    )


def rate_limit_state(
    store: "Any",
    tenant_id: str,
    venue: str,
    *,
    now: float | None = None,
    rate_window_seconds: float = DEFAULT_RATE_WINDOW_SECONDS,
) -> dict[str, Any]:
    moment = _epoch(now)
    cutoff = moment - float(rate_window_seconds)
    count = 0
    last: str | None = None
    for envelope, payload in _published_events(store, tenant_id):
        if str(payload.get("venue") or "") != str(venue or "").lower():
            continue
        published = _parse_iso(envelope.occurred_at)
        if published is not None and published.timestamp() >= cutoff:
            count += 1
            last = envelope.occurred_at
    return {
        "venue": str(venue or "").lower(),
        "window_seconds": float(rate_window_seconds),
        "published": count,
        "last_published_at": last,
    }


__all__ = [
    "CAMPAIGN_STATUSES",
    "DEFAULT_APPROVAL_TTL_SECONDS",
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_RATE_WINDOW_SECONDS",
    "DRAFT_CREATED_EVENT",
    "MARKETING_APPROVAL_REQUIRED_CODE",
    "MARKETING_CAMPAIGN_NOT_FOUND_CODE",
    "MARKETING_CONTENT_REFUSED_CODE",
    "MARKETING_ENGAGEMENT_REFUSED_CODE",
    "MARKETING_ENVIRONMENT_REFUSED_CODE",
    "MARKETING_FAILED_CODE",
    "MARKETING_NOT_RECONCILABLE_CODE",
    "MARKETING_RATE_LIMITED_CODE",
    "MARKETING_RECEIPT_MISMATCH_CODE",
    "MARKETING_RECEIPT_REQUIRED_CODE",
    "MARKETING_UNKNOWN_CODE",
    "PUBLISHED_EVENT",
    "PUBLICATION_FAILED_EVENT",
    "PUBLICATION_UNKNOWN_EVENT",
    "PUBLISH_ACTION",
    "PublicationAdapter",
    "PublicationReceipt",
    "PublicationUnknown",
    "Campaign",
    "check_content",
    "create_draft",
    "get_campaign",
    "list_campaigns",
    "publish",
    "rate_limit_state",
    "record_engagement",
    "reconcile",
    "request_approval",
]
