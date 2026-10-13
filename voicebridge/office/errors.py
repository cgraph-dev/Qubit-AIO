"""Typed office failures.

The office never raises a bare exception at a CLI or voice surface: every
refusal carries a stable ``code`` the caller can branch on and a human
sentence that names what to do instead. Same contract as
:mod:`voicebridge.kanban`.
"""
from __future__ import annotations

CONFIRM_CODE = "OFFICE_CONFIRM_REQUIRED"
ESTOP_CODE = "OFFICE_ESTOP"
NOT_FOUND_CODE = "OFFICE_NOT_FOUND"
EXISTS_CODE = "OFFICE_EXISTS"
BAD_INPUT_CODE = "OFFICE_BAD_INPUT"
BAD_STATE_CODE = "OFFICE_BAD_STATE"
CAPACITY_CODE = "OFFICE_CAPACITY"
CHAIN_CODE = "OFFICE_CHAIN"
IO_CODE = "OFFICE_IO"


class OfficeError(Exception):
    """Typed office failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def as_dict(self, operation: str = "") -> dict:
        return {"state": "REFUSED", "code": self.code,
                "operation": operation or None, "explanation": self.detail,
                "owner": "docs/game-office/95-CONTRACT.md",
                "rollback": "store untouched; nothing to roll back"}
