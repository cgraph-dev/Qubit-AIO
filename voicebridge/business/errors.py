"""Typed Business OS failures.

Every refusal at the control-plane boundary carries a stable ``code`` so a
caller can branch on it, and a human sentence naming the remedy. No bare
traceback is a contract.
"""
from __future__ import annotations

NOT_FOUND_CODE = "BUSINESS_NOT_FOUND"
EXISTS_CODE = "BUSINESS_EXISTS"
BAD_INPUT_CODE = "BUSINESS_BAD_INPUT"
BAD_STATE_CODE = "BUSINESS_BAD_STATE"
IDEMPOTENCY_CONFLICT_CODE = "BUSINESS_IDEMPOTENCY_CONFLICT"
APPROVAL_REQUIRED_CODE = "BUSINESS_APPROVAL_REQUIRED"
APPROVAL_EXPIRED_CODE = "BUSINESS_APPROVAL_EXPIRED"
APPROVAL_NOT_PENDING_CODE = "BUSINESS_APPROVAL_NOT_PENDING"
BUDGET_EXCEEDED_CODE = "BUSINESS_BUDGET_EXCEEDED"
TOOL_NOT_ALLOWED_CODE = "BUSINESS_TOOL_NOT_ALLOWED"
TOOL_EXECUTOR_MISSING_CODE = "BUSINESS_TOOL_EXECUTOR_MISSING"
TOOL_IN_FLIGHT_CODE = "BUSINESS_TOOL_IN_FLIGHT"
TOOL_FAILED_CODE = "BUSINESS_TOOL_FAILED"
IO_CODE = "BUSINESS_IO"
WARROOM_OWNER_ONLY_CODE = "BUSINESS_WARROOM_OWNER_ONLY"
WARROOM_UNVERIFIED_FACT_CODE = "BUSINESS_WARROOM_UNVERIFIED_FACT"
WARROOM_ADAPTER_UNAVAILABLE_CODE = "BUSINESS_WARROOM_ADAPTER_UNAVAILABLE"
WARROOM_ADAPTER_FAILED_CODE = "BUSINESS_WARROOM_ADAPTER_FAILED"
# Research-provider seam. ``UNAVAILABLE`` means no provider implements the
# requested kind; ``NOT_CONFIGURED`` means the real provider seam exists but is
# disabled (the default). Neither implies a successful, empty result.
RESEARCH_UNAVAILABLE_CODE = "BUSINESS_RESEARCH_UNAVAILABLE"
RESEARCH_NOT_CONFIGURED_CODE = "BUSINESS_RESEARCH_NOT_CONFIGURED"
RESEARCH_FAILED_CODE = "BUSINESS_RESEARCH_FAILED"

# Agent control plane. These are separate from the generic business codes so a
# UI can tell "the agent is archived" from "the tenant is wrong".
AGENT_NOT_FOUND_CODE = "AGENT_NOT_FOUND"
AGENT_UNKNOWN_SCOPE_CODE = "AGENT_UNKNOWN_SCOPE"
AGENT_BUDGET_EXCEEDED_CODE = "AGENT_BUDGET_EXCEEDED"
AGENT_TOKEN_LIMIT_CODE = "AGENT_TOKEN_LIMIT"
AGENT_TURN_LIMIT_CODE = "AGENT_TURN_LIMIT"
AGENT_RATE_LIMITED_CODE = "AGENT_RATE_LIMITED"
AGENT_APPROVAL_REQUIRED_CODE = "AGENT_APPROVAL_REQUIRED"
AGENT_NO_ACTIVE_TURN_CODE = "AGENT_NO_ACTIVE_TURN"
# Turn preview. These are control-plane refusals for a *prospective* turn; a
# preview never starts one, spends, or runs a tool.
AGENT_PAUSED_CODE = "AGENT_PAUSED"
AGENT_REVOKED_CODE = "AGENT_REVOKED"
AGENT_ARCHIVED_CODE = "AGENT_ARCHIVED"
AGENT_AUTONOMY_DENIED_CODE = "AGENT_AUTONOMY_DENIED"
AGENT_TOOL_UNKNOWN_CODE = "AGENT_TOOL_UNKNOWN"
AGENT_TOOL_DENIED_CODE = "AGENT_TOOL_DENIED"
AGENT_PROVIDER_NOT_READY_CODE = "AGENT_PROVIDER_NOT_READY"
AGENT_MODEL_NOT_READY_CODE = "AGENT_MODEL_NOT_READY"

# Local durable substrate. A checkpoint is a recovery point, so reusing a
# ``(run_id, sequence)`` slot is a typed refusal rather than an overwrite. An
# outbox delivery is only mutable while its lease is held; a lost or expired
# lease cannot record an effect.
CHECKPOINT_EXISTS_CODE = "BUSINESS_CHECKPOINT_EXISTS"
OUTBOX_NOT_FOUND_CODE = "BUSINESS_OUTBOX_NOT_FOUND"
OUTBOX_BAD_STATE_CODE = "BUSINESS_OUTBOX_BAD_STATE"
OUTBOX_LEASE_LOST_CODE = "BUSINESS_OUTBOX_LEASE_LOST"

# Approval intent binding (P1-06). An approval is bound to one exact normalized
# intent and one policy revision; a mismatch at execution time is a typed
# refusal, never a silent pass. ``AUTOMATION_REFUSED`` keeps a model output from
# deciding its own approval.
APPROVAL_INTENT_MISMATCH_CODE = "BUSINESS_APPROVAL_INTENT_MISMATCH"
APPROVAL_POLICY_STALE_CODE = "BUSINESS_APPROVAL_POLICY_STALE"
APPROVAL_REVOKED_CODE = "BUSINESS_APPROVAL_REVOKED"
APPROVAL_DELEGATION_WIDENED_CODE = "BUSINESS_APPROVAL_DELEGATION_WIDENED"
APPROVAL_AUTOMATION_REFUSED_CODE = "BUSINESS_APPROVAL_AUTOMATION_REFUSED"

# A2A message fabric (P1-05). These codes are separate from the approval codes
# so a caller can tell a spoofed sender from a replay or a delivery-ACL refusal.
A2A_NOT_FOUND_CODE = "A2A_NOT_FOUND"
A2A_BAD_STATE_CODE = "A2A_BAD_STATE"
A2A_SPOOF_REFUSED_CODE = "A2A_SPOOF_REFUSED"
A2A_DELEGATION_REFUSED_CODE = "A2A_DELEGATION_REFUSED"
A2A_IDEMPOTENCY_CONFLICT_CODE = "A2A_IDEMPOTENCY_CONFLICT"
A2A_REPLAY_REFUSED_CODE = "A2A_REPLAY_REFUSED"
A2A_MESSAGE_EXPIRED_CODE = "A2A_MESSAGE_EXPIRED"
A2A_PAYLOAD_TOO_LARGE_CODE = "A2A_PAYLOAD_TOO_LARGE"
A2A_NOT_RECIPIENT_CODE = "A2A_NOT_RECIPIENT"

# Agent profile (P2-01). The profile is a projection, not a second authority:
# a selector that would grant authority on its own is refused, and a profile
# that cannot name a concrete model route is not silently completed.
AGENT_PROFILE_INVALID_CODE = "AGENT_PROFILE_INVALID"
AGENT_PROFILE_SCOPE_CODE = "AGENT_PROFILE_SCOPE_REFUSED"

# Durable sessions and explicit handoffs (P2-02). A handoff is a durable
# record; acceptance is the only transfer and rejection leaves ownership alone.
SESSION_NOT_FOUND_CODE = "BUSINESS_SESSION_NOT_FOUND"
SESSION_BAD_STATE_CODE = "BUSINESS_SESSION_BAD_STATE"
SESSION_WORKSPACE_MISMATCH_CODE = "BUSINESS_SESSION_WORKSPACE_MISMATCH"
HANDOFF_NOT_FOUND_CODE = "BUSINESS_HANDOFF_NOT_FOUND"
HANDOFF_BAD_STATE_CODE = "BUSINESS_HANDOFF_BAD_STATE"
HANDOFF_EXISTS_CODE = "BUSINESS_HANDOFF_EXISTS"
HANDOFF_RECIPIENT_MISMATCH_CODE = "BUSINESS_HANDOFF_RECIPIENT_MISMATCH"
HANDOFF_EXPIRED_CODE = "BUSINESS_HANDOFF_EXPIRED"


class BusinessError(Exception):
    """A typed Business OS refusal.

    ``code`` is stable API; ``detail`` is for a human. ``as_dict`` is the
    shape CLI/voice/HTTP surfaces render.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def as_dict(self, operation: str = "") -> dict:
        return {
            "state": "REFUSED",
            "code": self.code,
            "operation": operation or None,
            "explanation": self.detail,
            "rollback": "store untouched unless the refusal says otherwise",
        }
