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
