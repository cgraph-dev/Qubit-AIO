"""Platform policy registry for official-API-only integrations.

This module is the policy authority for every external platform Qubit may
touch. It is intentionally *data plus a small evaluator*, not an adapter: no
network client lives here, and nothing in it can publish, message or spend.

Hard rules encoded for every platform (see ``HARD_RULES``):

* no browser automation of logged-in platforms;
* no scraping while authenticated;
* no fake engagement, reviews or followers;
* no bulk/spam direct messages;
* human approval for first publish, claims/pricing, sensitive replies, spend
  and irreversible actions.

The master plan (``docs/QUBIT-OFFICE-MASTER-PLAN.md`` §33/§36) is the
requirement source. Where a platform's terms are stricter than these defaults,
the per-platform record carries the specific note.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# -- vocabulary ---------------------------------------------------------
PUBLISH_ALLOWED = "allowed"
PUBLISH_LIMITED = "limited"
PUBLISH_FORBIDDEN = "forbidden"
PUBLISH_MODES = (PUBLISH_ALLOWED, PUBLISH_LIMITED, PUBLISH_FORBIDDEN)

FORBIDDEN = "forbidden"

#: The five consequences the owner requires a human to approve on every
#: platform, regardless of how permissive that platform's API is.
BASELINE_APPROVALS: tuple[str, ...] = (
    "first_publish",
    "claims_pricing",
    "sensitive_replies",
    "spend",
    "irreversible_actions",
)

#: Every platform record must set each of these to ``forbidden``.
HARD_RULES: dict[str, str] = {
    "browser_automation": "no browser automation or logged-in UI driving",
    "logged_in_scraping": "no scraping while authenticated",
    "fake_engagement": "no fake likes/follows/engagement",
    "fake_reviews": "no fake reviews or testimonials",
    "fake_followers": "no purchased or fabricated followers",
    "bulk_dms": "no bulk, unsolicited or spam direct messages",
    "spam": "no spam, duplicate or deceptive posting",
}

_APPROVAL_ACTIONS = frozenset(
    {
        "first_publish",
        "claims_pricing",
        "claims",
        "pricing",
        "price",
        "price_change",
        "refund",
        "spend",
        "ads",
        "ad_spend",
        "sensitive_reply",
        "sensitive_replies",
        "reply",
        "dm",
        "direct_message",
        "message",
        "send",
        "irreversible",
        "irreversible_actions",
        "targeting_changes",
    }
)

_FORBIDDEN_ACTIONS = frozenset(
    {
        "browser_automation",
        "browser",
        "scrape",
        "scraping",
        "logged_in_scraping",
        "automated_engagement",
        "engagement",
        "like",
        "follow",
        "fake_engagement",
        "fake_reviews",
        "fake_followers",
        "fake_review",
        "bulk_dm",
        "bulk_dms",
        "mass_dm",
        "spam",
        "spamming",
        "comment",
        "auto_comment",
    }
)

_READ_ACTIONS = frozenset({"read", "get", "list", "analytics", "metrics"})
_WRITE_ACTIONS = frozenset({"draft", "write", "create", "update", "upsert"})


def _norm(action: str) -> str:
    return str(action or "").strip().lower().replace("-", "_").replace(" ", "_")


class PolicyError(Exception):
    """A typed refusal from the policy registry."""

    UNKNOWN_PLATFORM = "platform_unknown"
    INVALID_POLICY = "platform_policy_invalid"
    ACTION_FORBIDDEN = "platform_action_forbidden"

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def as_dict(self, operation: str = "") -> dict[str, Any]:
        return {
            "state": "REFUSED",
            "code": self.code,
            "operation": operation or None,
            "explanation": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PlatformPolicy:
    """One platform's official-API, approval, consent and fallback rules."""

    platform_id: str
    display_name: str
    category: str
    official_api: bool
    oauth_required: bool
    automated_publish: str
    api_notes: str
    rate_limit_notes: str
    approval_requirements: tuple[str, ...]
    bot_labeling: str
    app_review: str
    data_consent: str
    safe_fallback: str
    automated_engagement: str = FORBIDDEN
    browser_automation: str = FORBIDDEN
    logged_in_scraping: str = FORBIDDEN
    fake_engagement: str = FORBIDDEN
    fake_reviews: str = FORBIDDEN
    fake_followers: str = FORBIDDEN
    bulk_dms: str = FORBIDDEN
    spam: str = FORBIDDEN

    def __post_init__(self) -> None:
        if not str(self.platform_id or "").strip():
            raise PolicyError(PolicyError.INVALID_POLICY, "platform_id is required")
        if self.automated_publish not in PUBLISH_MODES:
            raise PolicyError(
                PolicyError.INVALID_POLICY,
                f"{self.platform_id}: automated_publish must be one of"
                f" {', '.join(PUBLISH_MODES)}",
            )
        if self.automated_engagement != FORBIDDEN:
            raise PolicyError(
                PolicyError.INVALID_POLICY,
                f"{self.platform_id}: automated engagement is forbidden",
            )
        for field_name in HARD_RULES:
            if getattr(self, field_name) != FORBIDDEN:
                raise PolicyError(
                    PolicyError.INVALID_POLICY,
                    f"{self.platform_id}: {field_name} must be 'forbidden'",
                )
        req = tuple(str(item) for item in self.approval_requirements)
        missing = [item for item in BASELINE_APPROVALS if item not in req]
        if missing:
            raise PolicyError(
                PolicyError.INVALID_POLICY,
                f"{self.platform_id}: missing baseline approvals"
                f" {', '.join(missing)}",
            )
        object.__setattr__(self, "approval_requirements", req)
        for attr in (
            "display_name",
            "category",
            "api_notes",
            "rate_limit_notes",
            "bot_labeling",
            "app_review",
            "data_consent",
            "safe_fallback",
        ):
            if not str(getattr(self, attr) or "").strip():
                raise PolicyError(
                    PolicyError.INVALID_POLICY,
                    f"{self.platform_id}: {attr} must be non-empty",
                )

    @property
    def allows_automated_publish(self) -> bool:
        return self.automated_publish == PUBLISH_ALLOWED

    @property
    def requires_publish_approval(self) -> bool:
        return self.automated_publish != PUBLISH_ALLOWED

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform_id": self.platform_id,
            "display_name": self.display_name,
            "category": self.category,
            "official_api": self.official_api,
            "oauth_required": self.oauth_required,
            "automated_publish": self.automated_publish,
            "automated_engagement": self.automated_engagement,
            "api_notes": self.api_notes,
            "rate_limit_notes": self.rate_limit_notes,
            "approval_requirements": list(self.approval_requirements),
            "bot_labeling": self.bot_labeling,
            "app_review": self.app_review,
            "data_consent": self.data_consent,
            "safe_fallback": self.safe_fallback,
            "browser_automation": self.browser_automation,
            "logged_in_scraping": self.logged_in_scraping,
            "fake_engagement": self.fake_engagement,
            "fake_reviews": self.fake_reviews,
            "fake_followers": self.fake_followers,
            "bulk_dms": self.bulk_dms,
            "spam": self.spam,
        }


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The evaluator's answer for one (platform, action) pair."""

    platform_id: str
    action: str
    allowed: bool
    automated: bool
    requires_approval: bool
    code: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform_id": self.platform_id,
            "action": self.action,
            "allowed": self.allowed,
            "automated": self.automated,
            "requires_approval": self.requires_approval,
            "code": self.code,
            "reason": self.reason,
        }


def _meta_policy(platform_id: str, display_name: str) -> PlatformPolicy:
    return PlatformPolicy(
        platform_id=platform_id,
        display_name=display_name,
        category="social",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes=(
            "Official Meta Graph/Threads APIs only; Page/professional accounts;"
            " app review and business verification where required."
        ),
        rate_limit_notes=(
            "Per-app and per-account frequency caps; respect Meta spam/frequency"
            " rules, messaging windows and per-24h limits."
        ),
        approval_requirements=BASELINE_APPROVALS
        + ("account_linking", "ad_spend", "messaging_window"),
        bot_labeling="Label automated accounts and posts where platform policy requires it.",
        app_review="App review plus business verification for publishing and messaging.",
        data_consent=(
            "Consent, data minimisation, deletion and platform messaging-window rules."
        ),
        safe_fallback="Queue drafts and replies for human review; never browser-automate.",
    )


PLATFORM_POLICIES: dict[str, PlatformPolicy] = {
    "etsy": PlatformPolicy(
        platform_id="etsy",
        display_name="Etsy",
        category="marketplace",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes=(
            "Official Etsy Open API v3 with OAuth 2.0; no scraping or browser"
            " automation of the seller dashboard."
        ),
        rate_limit_notes=(
            "Etsy per-app/per-shop rate limits; back off on HTTP 429 and respect"
            " quota headers; cache reads."
        ),
        approval_requirements=BASELINE_APPROVALS
        + ("listing_publish", "shop_policy", "buyer_message", "ad_spend"),
        bot_labeling=(
            "Seller identity is the shop; automated messages must not impersonate"
            " a human or hide that they are automated."
        ),
        app_review="Etsy app review/approval for production API credentials.",
        data_consent=(
            "Buyer data minimisation; no marketing without consent; Etsy API"
            " data-use terms."
        ),
        safe_fallback="Draft listings and buyer replies for human review/publish.",
    ),
    "x": PlatformPolicy(
        platform_id="x",
        display_name="X",
        category="social",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_ALLOWED,
        api_notes=(
            "X API v2 with OAuth; automated posts are allowed, automated"
            " likes/follows/bulk engagement are not."
        ),
        rate_limit_notes=(
            "Per-user and per-app caps; pay-per-use limits; back off on 429."
        ),
        approval_requirements=BASELINE_APPROVALS + ("account_linking",),
        bot_labeling="Automated-account labeling is required.",
        app_review="X developer app review and paid-tier access.",
        data_consent="Platform data-use terms; no bulk collection or resale.",
        safe_fallback="Queue the post for human publishing if the API is unavailable.",
    ),
    "meta": _meta_policy("meta", "Meta (Facebook)"),
    "instagram": _meta_policy("instagram", "Instagram"),
    "threads": _meta_policy("threads", "Threads"),
    "linkedin": PlatformPolicy(
        platform_id="linkedin",
        display_name="LinkedIn",
        category="social",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes=(
            "Only documented OAuth/Posts API routes; many read and messaging"
            " features are partner-only; no crawlers or browser automation."
        ),
        rate_limit_notes="Per-app/per-member limits; respect partner-tier quotas.",
        approval_requirements=BASELINE_APPROVALS + ("account_linking",),
        bot_labeling="Label automated posts where required; no inauthentic engagement.",
        app_review="LinkedIn partner program for many APIs.",
        data_consent="Member data minimisation and platform data-use terms.",
        safe_fallback="Draft the post for a human to publish.",
    ),
    "tiktok": PlatformPolicy(
        platform_id="tiktok",
        display_name="TikTok",
        category="social",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes=(
            "Content Posting API; unaudited clients may only post private/self-view"
            " content; no fake likes or followers."
        ),
        rate_limit_notes="Low per-token quotas; audit-gated public posting.",
        approval_requirements=BASELINE_APPROVALS + ("account_linking", "content_review"),
        bot_labeling="Disclose automated content where platform policy requires it.",
        app_review="TikTok content-posting audit required for public posts.",
        data_consent="Platform data-use terms; no scraping or bulk collection.",
        safe_fallback="Upload as a private draft for human review and publishing.",
    ),
    "youtube": PlatformPolicy(
        platform_id="youtube",
        display_name="YouTube",
        category="social",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes=(
            "YouTube Data API v3 uploads with privacy/publish-at controls; no"
            " scraping or browser automation."
        ),
        rate_limit_notes=(
            "Data API quota; 100 uploads/day bucket; back off on quotaExceeded."
        ),
        approval_requirements=BASELINE_APPROVALS + ("content_review", "account_linking"),
        bot_labeling="Disclose automated uploads where required.",
        app_review="Google OAuth verification/audit for upload scopes.",
        data_consent="Google API data-use terms; no bulk collection.",
        safe_fallback="Upload as private/unlisted for human review.",
    ),
    "pinterest": PlatformPolicy(
        platform_id="pinterest",
        display_name="Pinterest",
        category="social",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes="Pinterest API v5 with OAuth; no scraping or browser automation.",
        rate_limit_notes="Per-app/per-user limits; back off on 429.",
        approval_requirements=BASELINE_APPROVALS + ("account_linking", "content_review"),
        bot_labeling="Label automated pins where required.",
        app_review="Pinterest app review for production access.",
        data_consent="Platform data-use terms; no bulk collection.",
        safe_fallback="Draft pins for human review.",
    ),
    "reddit": PlatformPolicy(
        platform_id="reddit",
        display_name="Reddit",
        category="social",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes=(
            "Reddit Data API/OAuth only; respect subreddit rules, bot disclosure"
            " and per-account limits; no scraping."
        ),
        rate_limit_notes="OAuth rate limits; per-subreddit and per-account rules.",
        approval_requirements=BASELINE_APPROVALS + ("subreddit_rules", "account_linking"),
        bot_labeling="Bot disclosure required by API/subreddit rules.",
        app_review="Reddit API app registration/approval.",
        data_consent="Platform data-use terms; no bulk messaging.",
        safe_fallback="Draft the post for a human to submit.",
    ),
    "email": PlatformPolicy(
        platform_id="email",
        display_name="Email",
        category="email",
        official_api=True,
        oauth_required=False,
        automated_publish=PUBLISH_LIMITED,
        api_notes=(
            "Use a documented ESP API/SMTP endpoint with sender authentication"
            " (SPF/DKIM/DMARC); no scraping address books."
        ),
        rate_limit_notes=(
            "ESP sending limits and warm-up; bounce/complaint thresholds;"
            " suppression lists."
        ),
        approval_requirements=BASELINE_APPROVALS
        + ("bulk_send", "marketing_consent", "list_purchase_forbidden"),
        bot_labeling=(
            "Accurate sender identity, physical address and unsubscribe in"
            " marketing mail."
        ),
        app_review="ESP account policy and sending approval.",
        data_consent=(
            "GDPR/CCPA/CAN-SPAM; opt-in, suppression lists and one-click"
            " unsubscribe."
        ),
        safe_fallback="Save as a draft for a human to send.",
    ),
    "web": PlatformPolicy(
        platform_id="web",
        display_name="Generic web",
        category="web",
        official_api=False,
        oauth_required=False,
        automated_publish=PUBLISH_FORBIDDEN,
        api_notes=(
            "No generic web write API; use documented site endpoints/forms or"
            " exports. Never scrape or browser-automate."
        ),
        rate_limit_notes="Respect robots/terms and documented endpoint limits.",
        approval_requirements=BASELINE_APPROVALS,
        bot_labeling="Identify automated traffic; no fake form submissions.",
        app_review="Site-specific terms and API approval.",
        data_consent="Consent, cookie and privacy-policy rules.",
        safe_fallback="Export a draft for a human to submit.",
    ),
    "crm": PlatformPolicy(
        platform_id="crm",
        display_name="Generic CRM",
        category="crm",
        official_api=True,
        oauth_required=True,
        automated_publish=PUBLISH_LIMITED,
        api_notes="Use the CRM's documented API/OAuth; never scrape the CRM UI.",
        rate_limit_notes="Per-tenant API quotas; back off on 429.",
        approval_requirements=BASELINE_APPROVALS
        + ("targeting_changes", "high_value_replies"),
        bot_labeling="Record agent authorship and source on every activity.",
        app_review="CRM marketplace/app review where required.",
        data_consent="Consent, suppression and data-minimisation per record.",
        safe_fallback="Export a CSV/draft for human import.",
    ),
}

_ALIASES = {
    "facebook": "meta",
    "fb": "meta",
    "twitter": "x",
    "yt": "youtube",
    "gmail": "email",
    "generic_web": "web",
    "website": "web",
    "generic_crm": "crm",
}


def get_policy(platform_id: str) -> PlatformPolicy:
    """Return the policy for ``platform_id`` or raise a typed refusal."""
    key = str(platform_id or "").strip().lower()
    key = _ALIASES.get(key, key)
    policy = PLATFORM_POLICIES.get(key)
    if policy is None:
        raise PolicyError(
            PolicyError.UNKNOWN_PLATFORM,
            f"no platform policy for {platform_id!r}; known platforms:"
            f" {', '.join(sorted(PLATFORM_POLICIES))}",
        )
    return policy


def policy_matrix() -> tuple[PlatformPolicy, ...]:
    """Every registered policy, sorted by id (stable for docs/UI)."""
    return tuple(PLATFORM_POLICIES[key] for key in sorted(PLATFORM_POLICIES))


def evaluate_action(platform_id: str, action: str) -> PolicyDecision:
    """Evaluate one action against the platform policy.

    The result distinguishes three things a caller must not conflate: whether
    the action is permitted at all, whether it may be automated, and whether a
    human approval token is required.
    """
    policy = get_policy(platform_id)
    act = _norm(action)

    if act in _FORBIDDEN_ACTIONS:
        return PolicyDecision(
            policy.platform_id,
            act,
            allowed=False,
            automated=False,
            requires_approval=False,
            code=PolicyError.ACTION_FORBIDDEN,
            reason=(
                f"{policy.display_name}: {act} is forbidden; {HARD_RULES.get(act, 'platform terms forbid it')}. "
                f"Use the safe fallback: {policy.safe_fallback}"
            ),
        )

    if act == "publish":
        if policy.automated_publish == PUBLISH_FORBIDDEN:
            return PolicyDecision(
                policy.platform_id,
                act,
                allowed=False,
                automated=False,
                requires_approval=True,
                code=PolicyError.ACTION_FORBIDDEN,
                reason=(
                    f"{policy.display_name}: automated publish is forbidden."
                    f" {policy.safe_fallback}"
                ),
            )
        limited = policy.automated_publish == PUBLISH_LIMITED
        return PolicyDecision(
            policy.platform_id,
            act,
            allowed=True,
            automated=not limited,
            requires_approval=limited,
            code=None,
            reason=(
                f"{policy.display_name}: publish is limited; approval required."
                if limited
                else f"{policy.display_name}: automated publish is allowed."
            ),
        )

    if act in _APPROVAL_ACTIONS:
        if not policy.official_api:
            return PolicyDecision(
                policy.platform_id,
                act,
                allowed=False,
                automated=False,
                requires_approval=True,
                code=PolicyError.ACTION_FORBIDDEN,
                reason=(
                    f"{policy.display_name}: no official API for {act};"
                    f" {policy.safe_fallback}"
                ),
            )
        return PolicyDecision(
            policy.platform_id,
            act,
            allowed=True,
            automated=False,
            requires_approval=True,
            code=None,
            reason=f"{policy.display_name}: {act} requires human approval.",
        )

    if act in _READ_ACTIONS:
        if not policy.official_api:
            return PolicyDecision(
                policy.platform_id,
                act,
                allowed=False,
                automated=False,
                requires_approval=False,
                code=PolicyError.ACTION_FORBIDDEN,
                reason=(
                    f"{policy.display_name}: no official read API; scraping is"
                    f" forbidden. {policy.safe_fallback}"
                ),
            )
        return PolicyDecision(
            policy.platform_id,
            act,
            allowed=True,
            automated=True,
            requires_approval=False,
            code=None,
            reason=f"{policy.display_name}: read through the official API.",
        )

    if act in _WRITE_ACTIONS:
        if not policy.official_api:
            return PolicyDecision(
                policy.platform_id,
                act,
                allowed=False,
                automated=False,
                requires_approval=True,
                code=PolicyError.ACTION_FORBIDDEN,
                reason=(
                    f"{policy.display_name}: no official write API."
                    f" {policy.safe_fallback}"
                ),
            )
        return PolicyDecision(
            policy.platform_id,
            act,
            allowed=True,
            automated=True,
            requires_approval=False,
            code=None,
            reason=f"{policy.display_name}: draft/write through the official API.",
        )

    return PolicyDecision(
        policy.platform_id,
        act,
        allowed=False,
        automated=False,
        requires_approval=False,
        code=PolicyError.ACTION_FORBIDDEN,
        reason=(
            f"{policy.display_name}: unknown action {action!r}; refusing rather"
            " than guessing"
        ),
    )


def require_action(platform_id: str, action: str) -> PolicyDecision:
    """Evaluate and raise :class:`PolicyError` unless the action is allowed."""
    decision = evaluate_action(platform_id, action)
    if not decision.allowed:
        raise PolicyError(
            PolicyError.ACTION_FORBIDDEN,
            decision.reason,
        )
    return decision


__all__ = [
    "BASELINE_APPROVALS",
    "FORBIDDEN",
    "HARD_RULES",
    "PLATFORM_POLICIES",
    "PUBLISH_ALLOWED",
    "PUBLISH_FORBIDDEN",
    "PUBLISH_LIMITED",
    "PUBLISH_MODES",
    "PlatformPolicy",
    "PolicyDecision",
    "PolicyError",
    "evaluate_action",
    "get_policy",
    "policy_matrix",
    "require_action",
]
