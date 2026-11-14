"""P5-04 — marketing drafts, approval binding, sandbox publication, receipts.

Hermetic: a throwaway Business OS SQLite file and an injected recording
adapter. No network, no credentials, no live social API. The tests pin:

* a draft is not a publication, and a campaign cannot publish itself;
* no publish without an approved human decision bound to the exact payload;
* a payload-confusion attempt is refused;
* a duplicate publish returns the stored receipt without a second effect;
* a publication receipt is mandatory (venue, time, payload hash);
* rate limits and platform policy refuse before the adapter;
* an unknown outcome stays unknown until reconciled against a receipt;
* engagement/follower/like abuse is refused.
"""
from __future__ import annotations

import pytest

from voicebridge.business import approvals, marketing
from voicebridge.business.errors import BusinessError
from voicebridge.business.store import BusinessStore

pytestmark = pytest.mark.unit

OWNER = "owner"
NOW = 1_800_000_000.0
PUBLISHED_AT = "2026-11-14T00:00:00Z"


class RecordingAdapter:
    """An injected sandbox publisher; records each call and returns a receipt."""

    def __init__(
        self,
        *,
        environment="sandbox",
        unknown=False,
        result=None,
    ):
        self.calls = []
        self.environment = environment
        self.unknown = unknown
        self.result = result

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if self.unknown:
            raise marketing.PublicationUnknown("provider timeout")
        if self.result is not None:
            return self.result
        return {
            "receipt_id": f"rcpt-{len(self.calls)}",
            "venue": kwargs["venue"],
            "payload_sha256": kwargs["payload_sha256"],
            "external_id": f"post-{len(self.calls)}",
            "published_at": PUBLISHED_AT,
            "environment": self.environment,
        }


@pytest.fixture()
def env(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    other = store.create_tenant("Other Co")
    other_business = store.create_business(
        other.tenant_id, "Other", slug="other"
    )
    return {
        "store": store,
        "tenant": tenant.tenant_id,
        "business": business.business_id,
        "other_tenant": other.tenant_id,
        "other_business": other_business.business_id,
    }


def _draft(env, *, text="Launch day. #ad", key="draft-1", venue="x"):
    campaign, _ = marketing.create_draft(
        env["store"],
        env["tenant"],
        env["business"],
        venue=venue,
        title="Launch",
        text=text,
        disclosure_required=True,
        disclosure="#ad",
        actor=OWNER,
        idempotency_key=key,
        now=NOW,
    )
    return campaign


def _approve(env, campaign, *, key="apr-1", policy_version="v1"):
    approval = marketing.request_approval(
        env["store"],
        env["tenant"],
        campaign.campaign_id,
        requested_by=OWNER,
        idempotency_key=key,
        policy_version=policy_version,
        now=NOW,
    )
    return approvals.decide(
        env["store"],
        tenant_id=env["tenant"],
        approval_id=approval.approval_id,
        decision="approved",
        decided_by=OWNER,
        reason="brand reviewed",
    )


def _publish(env, campaign, approval, adapter, *, key="pub-1", **kwargs):
    return marketing.publish(
        env["store"],
        env["tenant"],
        campaign.campaign_id,
        adapter=adapter,
        approval_id=approval.approval_id,
        actor=OWNER,
        idempotency_key=key,
        now=NOW,
        **kwargs,
    )


# -- draft lifecycle ------------------------------------------------------
def test_a_draft_is_not_published_and_cannot_self_approve(env):
    campaign = _draft(env)
    assert campaign.status == marketing.STATUS_DRAFT
    assert campaign.published is False
    adapter = RecordingAdapter()
    with pytest.raises(BusinessError) as no_approval:
        marketing.publish(
            env["store"],
            env["tenant"],
            campaign.campaign_id,
            adapter=adapter,
            approval_id="",
            actor=OWNER,
            idempotency_key="pub-0",
            now=NOW,
        )
    assert no_approval.value.code == marketing.MARKETING_APPROVAL_REQUIRED_CODE
    assert adapter.calls == []
    assert marketing.get_campaign(
        env["store"], env["tenant"], campaign.campaign_id
    ).status == marketing.STATUS_DRAFT


def test_publication_requires_an_approved_approval(env):
    campaign = _draft(env)
    approval = marketing.request_approval(
        env["store"], env["tenant"], campaign.campaign_id,
        requested_by=OWNER, idempotency_key="apr-pending", now=NOW,
    )
    adapter = RecordingAdapter()
    with pytest.raises(BusinessError) as pending:
        _publish(env, campaign, approval, adapter)
    assert pending.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert adapter.calls == []

    approvals.decide(
        env["store"], tenant_id=env["tenant"],
        approval_id=approval.approval_id, decision="approved",
        decided_by=OWNER,
    )
    receipt, created = _publish(env, campaign, approval, adapter)
    assert created is True
    assert receipt.external_id == "post-1"
    assert receipt.venue == "x"
    assert receipt.payload_sha256 == campaign.payload_sha256
    published = marketing.get_campaign(
        env["store"], env["tenant"], campaign.campaign_id
    )
    assert published.status == marketing.STATUS_PUBLISHED
    assert published.published is True
    assert published.receipt is not None


def test_payload_confusion_is_refused(env):
    approved_campaign = _draft(env, text="Launch day. #ad", key="draft-a")
    approval = _approve(env, approved_campaign, key="apr-a")
    other = _draft(env, text="A different launch. #ad", key="draft-b")
    adapter = RecordingAdapter()
    with pytest.raises(BusinessError) as confused:
        _publish(env, other, approval, adapter, key="pub-b")
    assert confused.value.code == "BUSINESS_APPROVAL_INTENT_MISMATCH"
    assert adapter.calls == []
    assert marketing.get_campaign(
        env["store"], env["tenant"], other.campaign_id
    ).status == marketing.STATUS_DRAFT


def test_duplicate_publish_is_deduped(env):
    campaign = _draft(env)
    approval = _approve(env, campaign)
    adapter = RecordingAdapter()
    first, created = _publish(env, campaign, approval, adapter)
    second, created_again = _publish(
        env, campaign, approval, adapter, key="pub-2"
    )
    assert created is True and created_again is False
    assert first.receipt_id == second.receipt_id
    assert len(adapter.calls) == 1
    assert len(marketing.list_campaigns(env["store"], env["tenant"])) == 1


def test_a_receipt_is_required_and_a_bad_receipt_does_not_publish(env):
    campaign = _draft(env)
    approval = _approve(env, campaign)
    adapter = RecordingAdapter(result={})
    with pytest.raises(BusinessError) as no_receipt:
        _publish(env, campaign, approval, adapter)
    assert no_receipt.value.code == marketing.MARKETING_RECEIPT_REQUIRED_CODE
    assert marketing.get_campaign(
        env["store"], env["tenant"], campaign.campaign_id
    ).status == marketing.STATUS_FAILED

    wrong_venue = RecordingAdapter(
        result={
            "venue": "linkedin",
            "external_id": "x",
            "published_at": PUBLISHED_AT,
            "environment": "sandbox",
        }
    )
    with pytest.raises(BusinessError) as mismatch:
        _publish(env, campaign, approval, wrong_venue, key="pub-2")
    assert mismatch.value.code == marketing.MARKETING_RECEIPT_MISMATCH_CODE


def test_live_publication_environment_is_refused(env):
    campaign = _draft(env)
    approval = _approve(env, campaign)
    adapter = RecordingAdapter(environment="live")
    with pytest.raises(BusinessError) as live:
        _publish(env, campaign, approval, adapter)
    assert live.value.code == marketing.MARKETING_ENVIRONMENT_REFUSED_CODE


# -- rate limits and policy ----------------------------------------------
def test_rate_limit_refuses_before_the_second_publish(env):
    first = _draft(env, text="First post. #ad", key="draft-1")
    second = _draft(env, text="Second post. #ad", key="draft-2")
    adapter = RecordingAdapter()
    _publish(
        env, first, _approve(env, first, key="apr-1"), adapter,
        key="pub-1", rate_limit=1, rate_window_seconds=3600,
    )
    with pytest.raises(BusinessError) as limited:
        _publish(
            env, second, _approve(env, second, key="apr-2"), adapter,
            key="pub-2", rate_limit=1, rate_window_seconds=3600,
        )
    assert limited.value.code == marketing.MARKETING_RATE_LIMITED_CODE
    assert len(adapter.calls) == 1


def test_platform_policy_and_content_checks(env):
    store = env["store"]
    with pytest.raises(BusinessError) as unknown:
        marketing.create_draft(
            store, env["tenant"], env["business"],
            venue="myspace", text="Hello", actor=OWNER,
            idempotency_key="d-unknown", now=NOW,
        )
    assert unknown.value.code == marketing.MARKETING_CONTENT_REFUSED_CODE

    with pytest.raises(BusinessError) as no_disclosure:
        marketing.create_draft(
            store, env["tenant"], env["business"],
            venue="x", text="A paid post without disclosure",
            disclosure_required=True, actor=OWNER,
            idempotency_key="d-disc", now=NOW,
        )
    assert no_disclosure.value.code == marketing.MARKETING_CONTENT_REFUSED_CODE

    with pytest.raises(BusinessError) as claim:
        marketing.create_draft(
            store, env["tenant"], env["business"],
            venue="linkedin", text="Our product is an fda approved miracle cure",
            actor=OWNER, idempotency_key="d-claim", now=NOW,
        )
    assert claim.value.code == marketing.MARKETING_CONTENT_REFUSED_CODE


def test_fake_engagement_is_refused(env):
    with pytest.raises(BusinessError) as engagement:
        marketing.create_draft(
            env["store"], env["tenant"], env["business"],
            venue="instagram", text="Buy followers and likes today",
            actor=OWNER, idempotency_key="d-fake", now=NOW,
        )
    assert engagement.value.code == marketing.MARKETING_CONTENT_REFUSED_CODE

    with pytest.raises(BusinessError) as record:
        marketing.record_engagement(
            env["store"], env["tenant"], "instagram", "like", 100
        )
    assert record.value.code == marketing.MARKETING_ENGAGEMENT_REFUSED_CODE


# -- unknown and reconciliation -----------------------------------------
def test_unknown_outcome_stays_unknown_until_reconciled(env):
    campaign = _draft(env)
    approval = _approve(env, campaign)
    unknown_adapter = RecordingAdapter(unknown=True)
    with pytest.raises(BusinessError) as unknown:
        _publish(env, campaign, approval, unknown_adapter)
    assert unknown.value.code == marketing.MARKETING_UNKNOWN_CODE
    assert marketing.get_campaign(
        env["store"], env["tenant"], campaign.campaign_id
    ).status == marketing.STATUS_UNKNOWN

    with pytest.raises(BusinessError) as again:
        _publish(
            env, campaign, approval, RecordingAdapter(), key="pub-retry"
        )
    assert again.value.code == marketing.MARKETING_UNKNOWN_CODE

    with pytest.raises(BusinessError) as bad_receipt:
        marketing.reconcile(
            env["store"], env["tenant"], campaign.campaign_id,
            receipt={
                "venue": "x",
                "external_id": "post-9",
                "published_at": PUBLISHED_AT,
                "payload_sha256": "0" * 64,
                "environment": "sandbox",
                "approval_id": approval.approval_id,
            },
            actor=OWNER, idempotency_key="rec-1", now=NOW,
        )
    assert bad_receipt.value.code == marketing.MARKETING_RECEIPT_MISMATCH_CODE

    receipt = marketing.reconcile(
        env["store"], env["tenant"], campaign.campaign_id,
        receipt={
            "receipt_id": "rcpt-9",
            "venue": "x",
            "external_id": "post-9",
            "published_at": PUBLISHED_AT,
            "payload_sha256": campaign.payload_sha256,
            "environment": "sandbox",
            "approval_id": approval.approval_id,
        },
        actor=OWNER, idempotency_key="rec-2", now=NOW,
    )
    assert receipt.reconciled is True
    resolved = marketing.get_campaign(
        env["store"], env["tenant"], campaign.campaign_id
    )
    assert resolved.status == marketing.STATUS_PUBLISHED
    assert resolved.receipt is not None
    assert resolved.receipt.reconciled is True


# -- scope ---------------------------------------------------------------
def test_tenant_isolation_and_campaign_listing(env):
    campaign = _draft(env)
    assert marketing.list_campaigns(env["store"], env["tenant"]) == [campaign]
    assert marketing.list_campaigns(env["store"], env["other_tenant"]) == []
    with pytest.raises(BusinessError) as missing:
        marketing.get_campaign(
            env["store"], env["other_tenant"], campaign.campaign_id
        )
    assert missing.value.code == marketing.MARKETING_CAMPAIGN_NOT_FOUND_CODE
