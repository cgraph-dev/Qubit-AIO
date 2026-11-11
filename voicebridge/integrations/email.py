"""Representative email adapter — official ESP API only, approval-gated.

Sending is a human-approved action in this foundation. Marketing mail also
requires a recorded opt-in; a missing opt-in is a typed consent refusal rather
than a sent message. No SMTP/HTTP client is created here.
"""
from __future__ import annotations

from typing import Any, Mapping

from .base import (
    ADAPTER_BAD_INPUT_CODE,
    ADAPTER_CONSENT_REQUIRED_CODE,
    AdapterError,
    AdapterResult,
    ApprovalToken,
    BaseAdapter,
    Transport,
)

APPROVAL_SEND = "email.send"


class EmailAdapter(BaseAdapter):
    platform_id = "email"
    credential_keys = ("api_key",)
    default_rate_limit = 30
    default_rate_window_seconds = 60.0

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        credentials: Mapping[str, Any] | None = None,
        dry_run: bool = True,
        rate_limit: int | None = None,
        rate_window_seconds: float | None = None,
        clock: Any = None,
    ) -> None:
        super().__init__(
            transport=transport,
            credentials=credentials,
            dry_run=dry_run,
            rate_limit=rate_limit,
            rate_window_seconds=rate_window_seconds,
            clock=clock,
        )

    def send_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
        marketing: bool = False,
        opted_in: bool = False,
        from_address: str | None = None,
    ) -> AdapterResult:
        op = "email.send"
        recipient = str(to or "").strip()
        clean_subject = str(subject or "").strip()
        clean_body = str(body or "").strip()
        if not recipient or "@" not in recipient:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                "Email: a valid recipient address is required",
                platform=self.platform_id,
                operation=op,
            )
        if not clean_subject:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                "Email: subject is required",
                platform=self.platform_id,
                operation=op,
            )
        if not clean_body:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                "Email: body is required",
                platform=self.platform_id,
                operation=op,
            )
        if marketing and not opted_in:
            raise AdapterError(
                ADAPTER_CONSENT_REQUIRED_CODE,
                "Email: marketing mail requires a recorded opt-in; refusing to"
                " send unsolicited marketing",
                platform=self.platform_id,
                operation=op,
            )
        return self._mutate(
            op,
            {
                "to": recipient,
                "subject": clean_subject[:998],
                "body": clean_body,
                "marketing": bool(marketing),
                "opted_in": bool(opted_in),
                "from_address": str(from_address or "").strip() or None,
            },
            idempotency_key=idempotency_key,
            path="/email/messages",
            method="POST",
            policy_action="send",
            approval_action=APPROVAL_SEND,
            approval=approval,
        )


__all__ = ["APPROVAL_SEND", "EmailAdapter"]
