"""Representative social adapter — official-API only, approval-gated.

One adapter class serves X, Meta/Instagram/Threads, LinkedIn, TikTok, YouTube,
Pinterest and Reddit because the *contract* is identical; the per-platform
policy registry decides whether automated publishing is allowed, limited or
forbidden. Posting, replying and direct messaging are typed methods; all are
dry-run by default and call no network client.

Hard rules enforced here: automated engagement is refused, bulk DMs are
refused, a DM needs recipient opt-in plus approval, and a first publish needs
approval on every platform even when later automated posts are allowed.
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


class SocialAdapter(BaseAdapter):
    """A typed publisher for any policy-registered social platform."""

    credential_keys = ("access_token",)
    default_rate_limit = 30
    default_rate_window_seconds = 60.0

    def __init__(
        self,
        platform_id: str,
        *,
        transport: Transport | None = None,
        credentials: Mapping[str, Any] | None = None,
        dry_run: bool = True,
        rate_limit: int | None = None,
        rate_window_seconds: float | None = None,
        clock: Any = None,
    ) -> None:
        self.platform_id = str(platform_id or "").strip().lower()
        super().__init__(
            transport=transport,
            credentials=credentials,
            dry_run=dry_run,
            rate_limit=rate_limit,
            rate_window_seconds=rate_window_seconds,
            clock=clock,
        )

    def _base_path(self) -> str:
        return f"/{self.platform_id}"

    def publish_post(
        self,
        *,
        text: str,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
        first_publish: bool = False,
        media: list[str] | None = None,
    ) -> AdapterResult:
        op = "post.publish"
        clean = str(text or "").strip()
        if not clean:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                f"{self._policy.display_name}: post text is required",
                platform=self.platform_id,
                operation=op,
            )
        decision = self._policy_decision("publish", op)
        need_approval = decision.requires_approval or first_publish
        expected = f"{self.platform_id}.post.publish"
        payload = {
            "text": clean[:10000],
            "media": [str(item) for item in (media or []) if str(item).strip()],
            "first_publish": bool(first_publish),
        }
        return self._mutate(
            op,
            payload,
            idempotency_key=idempotency_key,
            path=f"{self._base_path()}/posts",
            method="POST",
            policy_action="publish",
            approval_action=expected if need_approval else None,
            approval=approval,
        )

    def reply(
        self,
        post_id: str,
        *,
        text: str,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
    ) -> AdapterResult:
        op = "post.reply"
        target = str(post_id or "").strip()
        if not target:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                f"{self._policy.display_name}: post_id is required",
                platform=self.platform_id,
                operation=op,
            )
        clean = str(text or "").strip()
        if not clean:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                f"{self._policy.display_name}: reply text is required",
                platform=self.platform_id,
                operation=op,
            )
        return self._mutate(
            op,
            {"post_id": target, "text": clean[:10000]},
            idempotency_key=idempotency_key,
            path=f"{self._base_path()}/posts/{target}/replies",
            method="POST",
            policy_action="reply",
            approval_action=f"{self.platform_id}.post.reply",
            approval=approval,
        )

    def send_direct_message(
        self,
        recipient_id: str,
        *,
        text: str,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
        opt_in: bool = False,
        bulk: bool = False,
    ) -> AdapterResult:
        op = "dm.send"
        if bulk:
            self._policy_decision("bulk_dm", op)
        target = str(recipient_id or "").strip()
        if not target:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                f"{self._policy.display_name}: recipient_id is required",
                platform=self.platform_id,
                operation=op,
            )
        clean = str(text or "").strip()
        if not clean:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                f"{self._policy.display_name}: message text is required",
                platform=self.platform_id,
                operation=op,
            )
        if not opt_in:
            raise AdapterError(
                ADAPTER_CONSENT_REQUIRED_CODE,
                f"{self._policy.display_name}: direct messages require recorded"
                " recipient opt-in; unsolicited DMs are refused",
                platform=self.platform_id,
                operation=op,
            )
        return self._mutate(
            op,
            {"recipient_id": target, "text": clean[:4000], "opted_in": True},
            idempotency_key=idempotency_key,
            path=f"{self._base_path()}/messages",
            method="POST",
            policy_action="dm",
            approval_action=f"{self.platform_id}.dm.send",
            approval=approval,
        )

    def fetch_metrics(self, *, limit: int = 100) -> AdapterResult:
        bounded = max(1, min(int(limit), 100))
        return self._read(
            "metrics.fetch",
            path=f"{self._base_path()}/metrics",
            params={"limit": bounded},
            policy_action="metrics",
        )


__all__ = ["SocialAdapter"]
