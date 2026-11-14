"""Etsy Open API v3 adapter — typed, dry-run, approval-gated.

Reads (shop, listings, receipts, inventory) and digital-listing mutations
(draft, publish, price, refund, buyer message) are typed methods. The adapter
never imports a network client: a caller injects a ``Transport``. With no
transport or credentials every operation is a typed ``adapter_unavailable`` /
``adapter_not_configured`` refusal, never a fabricated success.

Publishing, price changes, refunds and buyer messages require an
:class:`ApprovalToken` whose ``action`` matches the operation. Mutations also
require an idempotency key, are rate limited, and replay the original result
for a repeated key (a different request under the same key is a typed
conflict).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .base import (
    ADAPTER_NOT_CONFIGURED_CODE,
    AdapterError,
    AdapterResult,
    ApprovalToken,
    BaseAdapter,
    Transport,
)

API_PREFIX = "/v3/application"

APPROVAL_PUBLISH = "etsy.listing.publish"
APPROVAL_PRICE = "etsy.listing.price"
APPROVAL_REFUND = "etsy.receipt.refund"
APPROVAL_MESSAGE = "etsy.message.send"

MAX_TITLE_LEN = 140
MAX_TAGS = 13
MAX_TAG_LEN = 20


class EtsyAdapter(BaseAdapter):
    """Typed Etsy operations over an injected transport."""

    platform_id = "etsy"
    credential_keys = ("api_key", "access_token")
    default_rate_limit = 10
    default_rate_window_seconds = 1.0

    def __init__(
        self,
        *,
        shop_id: str | None = None,
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
        source = credentials or {}
        self._shop_id = str(shop_id or source.get("shop_id") or "").strip()

    # -- readiness -----------------------------------------------------
    def _require_ready(self, operation: str) -> None:
        super()._require_ready(operation)
        if not self._shop_id:
            raise AdapterError(
                ADAPTER_NOT_CONFIGURED_CODE,
                "Etsy: missing shop_id (pass shop_id or credentials['shop_id'])",
                platform=self.platform_id,
                operation=operation,
            )

    def _shop_path(self, suffix: str = "") -> str:
        base = f"{API_PREFIX}/shops/{self._shop_id}"
        return f"{base}{suffix}"

    # -- reads ---------------------------------------------------------
    def get_shop(self) -> AdapterResult:
        return self._read("shop.get", path=self._shop_path())

    def list_listings(
        self, *, state: str | None = None, limit: int = 100
    ) -> AdapterResult:
        bounded = max(1, min(int(limit), 100))
        params: dict[str, Any] = {"limit": bounded}
        if state:
            params["state"] = str(state)
        return self._read(
            "listing.list", path=self._shop_path("/listings"), params=params
        )

    def get_listing(self, listing_id: str) -> AdapterResult:
        target = self._required(listing_id, "listing_id", "listing.get")
        return self._read(
            "listing.get", path=self._shop_path(f"/listings/{target}")
        )

    def list_receipts(self, *, limit: int = 100) -> AdapterResult:
        bounded = max(1, min(int(limit), 100))
        return self._read(
            "receipt.list",
            path=self._shop_path("/receipts"),
            params={"limit": bounded},
        )

    def get_inventory(self, listing_id: str) -> AdapterResult:
        target = self._required(listing_id, "listing_id", "inventory.get")
        return self._read(
            "inventory.get",
            path=self._shop_path(f"/listings/{target}/inventory"),
        )

    # -- digital listing drafts ---------------------------------------
    def create_draft_listing(
        self,
        listing: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> AdapterResult:
        payload = dict(listing)
        payload["state"] = "draft"
        return self._mutate(
            "listing.create_draft",
            payload,
            idempotency_key=idempotency_key,
            path=self._shop_path("/listings"),
            method="POST",
            policy_action="draft",
        )

    def create_digital_listing_draft(
        self,
        *,
        title: str,
        description: str,
        price_minor: int,
        idempotency_key: str,
        currency: str = "USD",
        tags: Sequence[str] = (),
        digital_files: Sequence[str] = (),
        taxonomy_id: int | None = None,
        who_made: str = "i_did",
        when_made: str = "made_to_order",
        is_supply: bool = False,
    ) -> AdapterResult:
        """Create a digital-download draft with Etsy-field validation."""
        op = "listing.create_digital_draft"
        clean_title = str(title or "").strip()
        clean_desc = str(description or "").strip()
        if not clean_title:
            raise self._bad_input("title is required", op)
        if len(clean_title) > MAX_TITLE_LEN:
            raise self._bad_input(
                f"title is longer than {MAX_TITLE_LEN} characters", op
            )
        if not clean_desc:
            raise self._bad_input("description is required", op)
        if (
            not isinstance(price_minor, int)
            or isinstance(price_minor, bool)
            or price_minor <= 0
        ):
            raise self._bad_input(
                "price_minor must be a positive integer (minor units)", op
            )
        clean_currency = str(currency or "").strip().upper()
        if len(clean_currency) != 3 or not clean_currency.isalpha():
            raise self._bad_input("currency must be a 3-letter code", op)
        clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        if len(clean_tags) > MAX_TAGS:
            raise self._bad_input(f"at most {MAX_TAGS} tags are allowed", op)
        if any(len(tag) > MAX_TAG_LEN for tag in clean_tags):
            raise self._bad_input(
                f"each tag must be at most {MAX_TAG_LEN} characters", op
            )
        clean_files = [
            str(name).strip() for name in digital_files if str(name).strip()
        ]
        if not clean_files:
            raise self._bad_input(
                "a digital listing requires at least one digital file", op
            )

        payload: dict[str, Any] = {
            "title": clean_title,
            "description": clean_desc,
            "price": {
                "amount": price_minor,
                "divisor": 100,
                "currency_code": clean_currency,
            },
            "tags": clean_tags,
            "type": "download",
            "is_digital": True,
            "state": "draft",
            "who_made": who_made,
            "when_made": when_made,
            "is_supply": bool(is_supply),
            "digital_files": clean_files,
        }
        if taxonomy_id is not None:
            payload["taxonomy_id"] = int(taxonomy_id)
        return self.create_draft_listing(
            payload, idempotency_key=idempotency_key
        )

    # -- gated mutations ----------------------------------------------
    def publish_listing(
        self,
        listing_id: str,
        *,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
    ) -> AdapterResult:
        target = self._required(listing_id, "listing_id", "listing.publish")
        return self._mutate(
            "listing.publish",
            {"state": "active"},
            idempotency_key=idempotency_key,
            path=self._shop_path(f"/listings/{target}"),
            method="PATCH",
            policy_action="publish",
            approval_action=APPROVAL_PUBLISH,
            approval=approval,
        )

    def update_listing_price(
        self,
        listing_id: str,
        *,
        price_minor: int,
        idempotency_key: str,
        currency: str = "USD",
        approval: ApprovalToken | None = None,
    ) -> AdapterResult:
        target = self._required(
            listing_id, "listing_id", "listing.price"
        )
        if (
            not isinstance(price_minor, int)
            or isinstance(price_minor, bool)
            or price_minor <= 0
        ):
            raise self._bad_input(
                "price_minor must be a positive integer (minor units)",
                "listing.price",
            )
        clean_currency = str(currency or "").strip().upper()
        if len(clean_currency) != 3 or not clean_currency.isalpha():
            raise self._bad_input("currency must be a 3-letter code", "listing.price")
        return self._mutate(
            "listing.price",
            {
                "price": {
                    "amount": price_minor,
                    "divisor": 100,
                    "currency_code": clean_currency,
                }
            },
            idempotency_key=idempotency_key,
            path=self._shop_path(f"/listings/{target}"),
            method="PATCH",
            policy_action="price",
            approval_action=APPROVAL_PRICE,
            approval=approval,
        )

    def refund_receipt(
        self,
        receipt_id: str,
        *,
        amount_minor: int,
        idempotency_key: str,
        reason: str = "",
        approval: ApprovalToken | None = None,
    ) -> AdapterResult:
        target = self._required(receipt_id, "receipt_id", "receipt.refund")
        if (
            not isinstance(amount_minor, int)
            or isinstance(amount_minor, bool)
            or amount_minor <= 0
        ):
            raise self._bad_input(
                "amount_minor must be a positive integer (minor units)",
                "receipt.refund",
            )
        return self._mutate(
            "receipt.refund",
            {"amount_minor": amount_minor, "reason": str(reason or "")[:500]},
            idempotency_key=idempotency_key,
            path=self._shop_path(f"/receipts/{target}/refunds"),
            method="POST",
            policy_action="refund",
            approval_action=APPROVAL_REFUND,
            approval=approval,
        )

    def send_message(
        self,
        receipt_id: str,
        *,
        body: str,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
    ) -> AdapterResult:
        target = self._required(receipt_id, "receipt_id", "message.send")
        clean_body = str(body or "").strip()
        if not clean_body:
            raise self._bad_input("message body is required", "message.send")
        return self._mutate(
            "message.send",
            {"receipt_id": target, "body": clean_body[:8000]},
            idempotency_key=idempotency_key,
            path=self._shop_path(f"/receipts/{target}/messages"),
            method="POST",
            policy_action="message",
            approval_action=APPROVAL_MESSAGE,
            approval=approval,
        )

    # -- helpers -------------------------------------------------------
    def _required(self, value: Any, label: str, operation: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise self._bad_input(f"{label} is required", operation)
        return clean


__all__ = [
    "APPROVAL_MESSAGE",
    "APPROVAL_PRICE",
    "APPROVAL_PUBLISH",
    "APPROVAL_REFUND",
    "API_PREFIX",
    "EtsyAdapter",
]
