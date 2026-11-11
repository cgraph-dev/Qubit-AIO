"""Platform policy matrix and hard rules (master plan §33)."""
from __future__ import annotations

import pytest

from voicebridge.integrations.platform_policy import (
    BASELINE_APPROVALS,
    HARD_RULES,
    PLATFORM_POLICIES,
    PUBLISH_ALLOWED,
    PUBLISH_FORBIDDEN,
    PUBLISH_LIMITED,
    PUBLISH_MODES,
    PlatformPolicy,
    PolicyError,
    evaluate_action,
    get_policy,
    policy_matrix,
)

REQUIRED_PLATFORMS = {
    "etsy",
    "x",
    "meta",
    "instagram",
    "threads",
    "linkedin",
    "tiktok",
    "youtube",
    "pinterest",
    "reddit",
    "email",
    "web",
    "crm",
}


def test_matrix_covers_every_required_platform():
    assert REQUIRED_PLATFORMS <= set(PLATFORM_POLICIES)
    assert {p.platform_id for p in policy_matrix()} == set(PLATFORM_POLICIES)


def test_every_policy_records_the_required_fields():
    for policy in policy_matrix():
        assert isinstance(policy, PlatformPolicy)
        assert isinstance(policy.official_api, bool)
        assert isinstance(policy.oauth_required, bool)
        assert policy.automated_publish in PUBLISH_MODES
        assert policy.automated_engagement == "forbidden"
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
            assert str(getattr(policy, attr)).strip(), (policy.platform_id, attr)
        for approval in BASELINE_APPROVALS:
            assert approval in policy.approval_requirements, (
                policy.platform_id,
                approval,
            )


def test_hard_rules_are_forbidden_on_every_platform():
    for policy in policy_matrix():
        for field_name in HARD_RULES:
            assert getattr(policy, field_name) == "forbidden", (
                policy.platform_id,
                field_name,
            )


def test_browser_automation_and_logged_in_scraping_are_refused():
    for policy in policy_matrix():
        for action in ("browser_automation", "logged_in_scraping", "scrape"):
            decision = evaluate_action(policy.platform_id, action)
            assert decision.allowed is False
            assert decision.automated is False
            assert decision.code == PolicyError.ACTION_FORBIDDEN


def test_fake_engagement_reviews_followers_and_bulk_dms_are_refused():
    for policy in policy_matrix():
        for action in (
            "like",
            "follow",
            "automated_engagement",
            "fake_engagement",
            "fake_reviews",
            "fake_followers",
            "bulk_dm",
            "spam",
        ):
            assert evaluate_action(policy.platform_id, action).allowed is False


@pytest.mark.parametrize(
    "platform, action, allowed, automated, approval",
    [
        ("x", "publish", True, True, False),
        ("linkedin", "publish", True, False, True),
        ("tiktok", "publish", True, False, True),
        ("youtube", "publish", True, False, True),
        ("pinterest", "publish", True, False, True),
        ("reddit", "publish", True, False, True),
        ("etsy", "publish", True, False, True),
        ("web", "publish", False, False, True),
        ("email", "publish", True, False, True),
        ("crm", "publish", True, False, True),
    ],
)
def test_publish_modes_are_encoded(
    platform, action, allowed, automated, approval
):
    decision = evaluate_action(platform, action)
    assert decision.allowed is allowed
    assert decision.automated is automated
    assert decision.requires_approval is approval


@pytest.mark.parametrize(
    "action",
    [
        "first_publish",
        "claims_pricing",
        "price_change",
        "pricing",
        "refund",
        "spend",
        "sensitive_reply",
        "reply",
        "dm",
        "direct_message",
        "send",
        "irreversible",
    ],
)
def test_consequential_actions_require_human_approval(action):
    for policy in policy_matrix():
        decision = evaluate_action(policy.platform_id, action)
        if policy.official_api:
            assert decision.allowed is True
            assert decision.requires_approval is True
            assert decision.automated is False


def test_generic_web_has_no_automated_write_path():
    assert PLATFORM_POLICIES["web"].official_api is False
    assert PLATFORM_POLICIES["web"].automated_publish == PUBLISH_FORBIDDEN
    assert evaluate_action("web", "read").allowed is False
    assert evaluate_action("web", "draft").allowed is False


def test_aliases_resolve_to_the_canonical_policy():
    assert get_policy("twitter") is PLATFORM_POLICIES["x"]
    assert get_policy("facebook") is PLATFORM_POLICIES["meta"]
    assert get_policy("generic_web") is PLATFORM_POLICIES["web"]


def test_unknown_platform_is_a_typed_refusal():
    with pytest.raises(PolicyError) as exc:
        get_policy("myspace")
    assert exc.value.code == PolicyError.UNKNOWN_PLATFORM


def test_policy_refuses_a_weakened_hard_rule():
    base = PLATFORM_POLICIES["x"]
    with pytest.raises(PolicyError) as exc:
        PlatformPolicy(
            platform_id="bad",
            display_name="Bad",
            category="social",
            official_api=True,
            oauth_required=True,
            automated_publish=PUBLISH_ALLOWED,
            api_notes="x",
            rate_limit_notes="x",
            approval_requirements=BASELINE_APPROVALS,
            bot_labeling="x",
            app_review="x",
            data_consent="x",
            safe_fallback="x",
            browser_automation="allowed",
        )
    assert exc.value.code == PolicyError.INVALID_POLICY
    assert base.browser_automation == "forbidden"


def test_policy_refuses_missing_baseline_approvals():
    with pytest.raises(PolicyError) as exc:
        PlatformPolicy(
            platform_id="bad",
            display_name="Bad",
            category="social",
            official_api=True,
            oauth_required=True,
            automated_publish=PUBLISH_LIMITED,
            api_notes="x",
            rate_limit_notes="x",
            approval_requirements=("first_publish",),
            bot_labeling="x",
            app_review="x",
            data_consent="x",
            safe_fallback="x",
        )
    assert exc.value.code == PolicyError.INVALID_POLICY


def test_policy_refuses_a_non_forbidden_engagement_mode():
    with pytest.raises(PolicyError):
        PlatformPolicy(
            platform_id="bad",
            display_name="Bad",
            category="social",
            official_api=True,
            oauth_required=True,
            automated_publish=PUBLISH_LIMITED,
            api_notes="x",
            rate_limit_notes="x",
            approval_requirements=BASELINE_APPROVALS,
            bot_labeling="x",
            app_review="x",
            data_consent="x",
            safe_fallback="x",
            automated_engagement="allowed",
        )


def test_unknown_action_is_refused_rather_than_guessed():
    decision = evaluate_action("x", "do_something_undefined")
    assert decision.allowed is False
    assert decision.code == PolicyError.ACTION_FORBIDDEN
