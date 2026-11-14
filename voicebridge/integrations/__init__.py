"""Qubit integrations — policy-gated, official-API-only, dry-run adapters.

Nothing in this package opens a network connection, holds a real credential or
performs an external side effect. Adapters accept an injected ``Transport`` and
default to ``dry_run=True``. The platform policy registry is the hard-rule
authority; the Business OS approval/tool gateway remains the enforcement point
for real calls.
"""
from __future__ import annotations

from .base import (
    ADAPTER_APPROVAL_REQUIRED_CODE,
    ADAPTER_BAD_INPUT_CODE,
    ADAPTER_CONSENT_REQUIRED_CODE,
    ADAPTER_IDEMPOTENCY_CONFLICT_CODE,
    ADAPTER_NOT_CONFIGURED_CODE,
    ADAPTER_POLICY_FORBIDDEN_CODE,
    ADAPTER_RATE_LIMITED_CODE,
    ADAPTER_TRANSPORT_ERROR_CODE,
    ADAPTER_UNAVAILABLE_CODE,
    AdapterError,
    AdapterResult,
    ApprovalToken,
    BaseAdapter,
    RateLimiter,
    Transport,
)
from .crm import CrmAdapter
from .email import EmailAdapter
from .etsy import EtsyAdapter
from .etsy_auth import (
    EtsyAuthError,
    EtsyOAuth,
    EtsyOAuthConfig,
    EtsyTokenStore,
    OAuthTransport,
    default_token_path,
    pkce_challenge,
)
from .etsy_auth_transport import EtsyOAuthHttpTransport
from .etsy_playbook import playbook, validate_playbook
from .platform_policy import (
    BASELINE_APPROVALS,
    HARD_RULES,
    PLATFORM_POLICIES,
    PUBLISH_ALLOWED,
    PUBLISH_FORBIDDEN,
    PUBLISH_LIMITED,
    PlatformPolicy,
    PolicyDecision,
    PolicyError,
    evaluate_action,
    get_policy,
    policy_matrix,
    require_action,
)
from .social import SocialAdapter

__all__ = [
    "ADAPTER_APPROVAL_REQUIRED_CODE",
    "ADAPTER_BAD_INPUT_CODE",
    "ADAPTER_CONSENT_REQUIRED_CODE",
    "ADAPTER_IDEMPOTENCY_CONFLICT_CODE",
    "ADAPTER_NOT_CONFIGURED_CODE",
    "ADAPTER_POLICY_FORBIDDEN_CODE",
    "ADAPTER_RATE_LIMITED_CODE",
    "ADAPTER_TRANSPORT_ERROR_CODE",
    "ADAPTER_UNAVAILABLE_CODE",
    "BASELINE_APPROVALS",
    "HARD_RULES",
    "PLATFORM_POLICIES",
    "PUBLISH_ALLOWED",
    "PUBLISH_FORBIDDEN",
    "PUBLISH_LIMITED",
    "AdapterError",
    "AdapterResult",
    "ApprovalToken",
    "BaseAdapter",
    "CrmAdapter",
    "EmailAdapter",
    "EtsyAdapter",
    "EtsyAuthError",
    "EtsyOAuth",
    "EtsyOAuthConfig",
    "EtsyOAuthHttpTransport",
    "EtsyTokenStore",
    "OAuthTransport",
    "PlatformPolicy",
    "PolicyDecision",
    "PolicyError",
    "RateLimiter",
    "SocialAdapter",
    "Transport",
    "default_token_path",
    "evaluate_action",
    "get_policy",
    "pkce_challenge",
    "playbook",
    "policy_matrix",
    "require_action",
    "validate_playbook",
]
