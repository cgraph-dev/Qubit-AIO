"""Bridge the dry-run integration adapters into the Business OS ToolGateway.

This module is *wiring*, not a second executor stack. Each registered tool
names an adapter operation; the gateway still owns tenant/business scope,
policy allow-listing, approval validation, budget, durable idempotency,
redaction, audit and the one ledger append. An executor here only:

* resolves the registered adapter for the tool,
* refuses readiness failures (missing transport/credentials) with the
  adapter's own typed ``adapter_unavailable``/``adapter_not_configured`` code,
* dispatches the typed adapter method with the request's idempotency key and
  derived approval token,
* maps the adapter result into an :class:`ExecutorResult` so a dry-run adapter
  can never be reported as a real execution.

No network client, OAuth flow or real credential exists in this module. The
adapters accept an injected ``Transport``; tests use a recording double.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from ..integrations.base import (
    ADAPTER_UNAVAILABLE_CODE,
    AdapterResult,
    ApprovalToken,
    BaseAdapter,
)
from ..integrations.crm import CrmAdapter
from ..integrations.email import EmailAdapter
from ..integrations.etsy import (
    APPROVAL_MESSAGE,
    APPROVAL_PRICE,
    APPROVAL_PUBLISH,
    APPROVAL_REFUND,
    EtsyAdapter,
)
from ..integrations.platform_policy import evaluate_action
from ..integrations.social import SocialAdapter
from .errors import BAD_INPUT_CODE, BusinessError
from .tools import ExecutorResult, ToolRequest

INTEGRATION_UNKNOWN_TOOL_CODE = "integration_unknown_tool"
INTEGRATION_BAD_INPUT_CODE = "integration_bad_input"

TOOL_ETSY_SHOP_READ = "etsy.shop.read"
TOOL_ETSY_LISTINGS_READ = "etsy.listings.read"
TOOL_ETSY_LISTING_DRAFT = "etsy.listing.draft"
TOOL_ETSY_LISTING_PUBLISH = "etsy.listing.publish"
TOOL_ETSY_LISTING_PRICE = "etsy.listing.price"
TOOL_ETSY_RECEIPT_REFUND = "etsy.receipt.refund"
TOOL_ETSY_MESSAGE_SEND = "etsy.message.send"
TOOL_SOCIAL_PUBLISH = "social.post.publish"
TOOL_CRM_CONTACT_READ = "crm.contact.read"
TOOL_CRM_CONTACT_UPSERT = "crm.contact.upsert"
TOOL_EMAIL_SEND = "email.send"
TOOL_EMAIL_DRAFT = "email.draft"

Handler = Callable[[BaseAdapter, ToolRequest], ExecutorResult]


@dataclass(frozen=True, slots=True)
class IntegrationTool:
    """One registered adapter operation and its dispatch handler."""

    name: str
    adapter: str
    operation: str
    handler: Handler
    policy_action: str
    approval_action: str | None = None
    money: bool = False
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "adapter": self.adapter,
            "operation": self.operation,
            "policy_action": self.policy_action,
            "approval_action": self.approval_action,
            "money": self.money,
            "description": self.description,
        }


def _payload(request: ToolRequest) -> Mapping[str, Any]:
    body = request.payload
    if body is None:
        return {}
    if not isinstance(body, Mapping):
        raise BusinessError(
            INTEGRATION_BAD_INPUT_CODE,
            "tool payload must be a mapping",
        )
    return body


def _required(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise BusinessError(
            INTEGRATION_BAD_INPUT_CODE, f"payload.{key} is required"
        )
    return value


def _positive_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BusinessError(
            INTEGRATION_BAD_INPUT_CODE,
            f"payload.{key} must be a positive integer (minor units)",
        )
    return value


def _bounded_int(
    payload: Mapping[str, Any], key: str, *, default: int, maximum: int
) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BusinessError(
            INTEGRATION_BAD_INPUT_CODE, f"payload.{key} must be a positive integer"
        )
    return min(value, maximum)


def _string_list(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key) or ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise BusinessError(
            INTEGRATION_BAD_INPUT_CODE, f"payload.{key} must be a list of strings"
        )
    return tuple(str(item) for item in value if str(item).strip())


def _token(action: str | None, request: ToolRequest) -> ApprovalToken | None:
    if action is None or not request.approval_id:
        return None
    return ApprovalToken(approval_id=request.approval_id, action=action)


def _expect(
    adapter: BaseAdapter, adapter_type: type[BaseAdapter], request: ToolRequest
) -> BaseAdapter:
    if not isinstance(adapter, adapter_type):
        raise BusinessError(
            INTEGRATION_BAD_INPUT_CODE,
            f"tool {request.tool!r} requires a {adapter_type.__name__} adapter",
        )
    return adapter


def _wrap(
    result: AdapterResult,
    *,
    ledger: Mapping[str, Any] | None = None,
) -> ExecutorResult:
    return ExecutorResult(
        result={"adapter": result.as_dict()},
        executed=result.executed,
        dry_run=result.dry_run,
        ledger=ledger,
    )


# -- handlers -----------------------------------------------------------
def _etsy_shop_read(adapter: BaseAdapter, request: ToolRequest) -> ExecutorResult:
    adapter = _expect(adapter, EtsyAdapter, request)
    return _wrap(adapter.get_shop())


def _etsy_listings_read(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, EtsyAdapter, request)
    body = _payload(request)
    state = body.get("state")
    return _wrap(
        adapter.list_listings(
            state=str(state) if state else None,
            limit=_bounded_int(body, "limit", default=100, maximum=100),
        )
    )


def _etsy_listing_draft(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, EtsyAdapter, request)
    body = _payload(request)
    kwargs: dict[str, Any] = {
        "title": _required(body, "title"),
        "description": _required(body, "description"),
        "price_minor": _positive_int(body, "price_minor"),
        "idempotency_key": request.idempotency_key,
        "currency": str(body.get("currency") or "USD"),
        "tags": _string_list(body, "tags"),
        "digital_files": _string_list(body, "digital_files"),
        "who_made": str(body.get("who_made") or "i_did"),
        "when_made": str(body.get("when_made") or "made_to_order"),
        "is_supply": bool(body.get("is_supply", False)),
    }
    if body.get("taxonomy_id") is not None:
        kwargs["taxonomy_id"] = int(body["taxonomy_id"])
    return _wrap(adapter.create_digital_listing_draft(**kwargs))


def _etsy_listing_publish(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, EtsyAdapter, request)
    body = _payload(request)
    return _wrap(
        adapter.publish_listing(
            _required(body, "listing_id"),
            idempotency_key=request.idempotency_key,
            approval=_token(APPROVAL_PUBLISH, request),
        )
    )


def _etsy_listing_price(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, EtsyAdapter, request)
    body = _payload(request)
    return _wrap(
        adapter.update_listing_price(
            _required(body, "listing_id"),
            price_minor=_positive_int(body, "price_minor"),
            idempotency_key=request.idempotency_key,
            currency=str(body.get("currency") or "USD"),
            approval=_token(APPROVAL_PRICE, request),
        )
    )


def _etsy_receipt_refund(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, EtsyAdapter, request)
    body = _payload(request)
    amount_minor = _positive_int(body, "amount_minor")
    result = adapter.refund_receipt(
        _required(body, "receipt_id"),
        amount_minor=amount_minor,
        idempotency_key=request.idempotency_key,
        reason=str(body.get("reason") or ""),
        approval=_token(APPROVAL_REFUND, request),
    )
    return _wrap(
        result,
        ledger={
            "account": "etsy.refunds",
            "direction": "debit",
            "amount_minor": amount_minor,
            "currency": str(body.get("currency") or "USD"),
            "memo": f"Etsy refund for receipt {body.get('receipt_id')}",
        },
    )


def _etsy_message_send(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, EtsyAdapter, request)
    body = _payload(request)
    return _wrap(
        adapter.send_message(
            _required(body, "receipt_id"),
            body=_required(body, "body"),
            idempotency_key=request.idempotency_key,
            approval=_token(APPROVAL_MESSAGE, request),
        )
    )


def _social_publish(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, SocialAdapter, request)
    body = _payload(request)
    platform = _required(body, "platform").lower()
    first_publish = bool(body.get("first_publish", False))
    decision = evaluate_action(platform, "publish")
    token = None
    if decision.allowed and (decision.requires_approval or first_publish):
        token = _token(f"{platform}.post.publish", request)
    return _wrap(
        adapter.publish_post(
            text=_required(body, "text"),
            idempotency_key=request.idempotency_key,
            approval=token,
            first_publish=first_publish,
            media=list(_string_list(body, "media")),
        )
    )


def _crm_contact_read(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, CrmAdapter, request)
    return _wrap(adapter.get_contact(_required(_payload(request), "external_id")))


def _crm_contact_upsert(
    adapter: BaseAdapter, request: ToolRequest
) -> ExecutorResult:
    adapter = _expect(adapter, CrmAdapter, request)
    body = _payload(request)
    contact = body.get("contact") if isinstance(body.get("contact"), Mapping) else body
    return _wrap(
        adapter.upsert_contact(
            contact, idempotency_key=request.idempotency_key
        )
    )


def _email_send(adapter: BaseAdapter, request: ToolRequest) -> ExecutorResult:
    adapter = _expect(adapter, EmailAdapter, request)
    body = _payload(request)
    return _wrap(
        adapter.send_email(
            to=_required(body, "to"),
            subject=_required(body, "subject"),
            body=_required(body, "body"),
            idempotency_key=request.idempotency_key,
            approval=_token("email.send", request),
            marketing=bool(body.get("marketing", False)),
            opted_in=bool(body.get("opted_in", False)),
            from_address=body.get("from_address"),
        )
    )


def _email_draft(adapter: BaseAdapter, request: ToolRequest) -> ExecutorResult:
    adapter = _expect(adapter, EmailAdapter, request)
    body = _payload(request)
    return _wrap(
        adapter.create_draft(
            to=_required(body, "to"),
            subject=_required(body, "subject"),
            body=_required(body, "body"),
            idempotency_key=request.idempotency_key,
            from_address=body.get("from_address"),
        )
    )


INTEGRATION_TOOLS: tuple[IntegrationTool, ...] = (
    IntegrationTool(
        TOOL_ETSY_SHOP_READ,
        "etsy",
        "shop.get",
        _etsy_shop_read,
        "read",
        description="Read the shop through the official Etsy API.",
    ),
    IntegrationTool(
        TOOL_ETSY_LISTINGS_READ,
        "etsy",
        "listing.list",
        _etsy_listings_read,
        "read",
        description="List shop listings through the official Etsy API.",
    ),
    IntegrationTool(
        TOOL_ETSY_LISTING_DRAFT,
        "etsy",
        "listing.create_digital_draft",
        _etsy_listing_draft,
        "draft",
        description="Create a digital-download listing draft.",
    ),
    IntegrationTool(
        TOOL_ETSY_LISTING_PUBLISH,
        "etsy",
        "listing.publish",
        _etsy_listing_publish,
        "publish",
        approval_action=APPROVAL_PUBLISH,
        description="Publish a listing; needs an approved human decision.",
    ),
    IntegrationTool(
        TOOL_ETSY_LISTING_PRICE,
        "etsy",
        "listing.price",
        _etsy_listing_price,
        "price",
        approval_action=APPROVAL_PRICE,
        money=True,
        description="Change a listing price; needs an approved human decision.",
    ),
    IntegrationTool(
        TOOL_ETSY_RECEIPT_REFUND,
        "etsy",
        "receipt.refund",
        _etsy_receipt_refund,
        "refund",
        approval_action=APPROVAL_REFUND,
        money=True,
        description="Refund a receipt; needs an approved human decision.",
    ),
    IntegrationTool(
        TOOL_ETSY_MESSAGE_SEND,
        "etsy",
        "message.send",
        _etsy_message_send,
        "message",
        approval_action=APPROVAL_MESSAGE,
        description="Message a buyer; needs an approved human decision.",
    ),
    IntegrationTool(
        TOOL_SOCIAL_PUBLISH,
        "social",
        "post.publish",
        _social_publish,
        "publish",
        description="Publish a post through a registered official social API.",
    ),
    IntegrationTool(
        TOOL_CRM_CONTACT_READ,
        "crm",
        "contact.get",
        _crm_contact_read,
        "read",
        description="Read a CRM contact.",
    ),
    IntegrationTool(
        TOOL_CRM_CONTACT_UPSERT,
        "crm",
        "contact.upsert",
        _crm_contact_upsert,
        "upsert",
        description="Upsert a CRM contact (system of record).",
    ),
    IntegrationTool(
        TOOL_EMAIL_SEND,
        "email",
        "email.send",
        _email_send,
        "send",
        approval_action="email.send",
        description="Send email; needs an approved human decision.",
    ),
    IntegrationTool(
        TOOL_EMAIL_DRAFT,
        "email",
        "email.draft",
        _email_draft,
        "draft",
        description="Create an ESP draft; no send and no approval.",
    ),
)

_BY_NAME: dict[str, IntegrationTool] = {tool.name: tool for tool in INTEGRATION_TOOLS}


def integration_tools() -> tuple[IntegrationTool, ...]:
    return INTEGRATION_TOOLS


def get_integration_tool(name: str) -> IntegrationTool:
    tool = _BY_NAME.get(str(name or "").strip())
    if tool is None:
        raise BusinessError(
            INTEGRATION_UNKNOWN_TOOL_CODE,
            f"no integration executor is registered for {name!r}",
        )
    return tool


class IntegrationToolRegistry:
    """The registered integration executor.

    Pass it as ``ToolGateway(store, registry.execute)`` (or the instance itself;
    it is callable). Adapters are injected, so every operation is dry-run until
    a caller supplies a transport and credentials.
    """

    def __init__(
        self,
        adapters: Mapping[str, BaseAdapter] | None = None,
        *,
        tools: Iterable[IntegrationTool] | None = None,
    ) -> None:
        self._adapters: dict[str, BaseAdapter] = dict(adapters or {})
        self._tools: dict[str, IntegrationTool] = {
            tool.name: tool for tool in (tools if tools is not None else INTEGRATION_TOOLS)
        }

    def register_adapter(self, key: str, adapter: BaseAdapter) -> None:
        clean = str(key or "").strip().lower()
        if not clean:
            raise BusinessError(BAD_INPUT_CODE, "an adapter key is required")
        if not isinstance(adapter, BaseAdapter):
            raise BusinessError(
                BAD_INPUT_CODE, "adapter must be a BaseAdapter instance"
            )
        self._adapters[clean] = adapter

    def register_tool(self, tool: IntegrationTool) -> None:
        if not isinstance(tool, IntegrationTool):
            raise BusinessError(
                BAD_INPUT_CODE, "tool must be an IntegrationTool"
            )
        self._tools[tool.name] = tool

    def tool(self, name: str) -> IntegrationTool:
        tool = self._tools.get(str(name or "").strip())
        if tool is None:
            raise BusinessError(
                INTEGRATION_UNKNOWN_TOOL_CODE,
                f"no integration executor is registered for {name!r}",
            )
        return tool

    def adapter_for(
        self, tool: IntegrationTool, payload: Mapping[str, Any]
    ) -> BaseAdapter:
        key = tool.adapter
        if key == "social":
            platform = str(payload.get("platform") or "").strip().lower()
            if not platform:
                raise BusinessError(
                    INTEGRATION_BAD_INPUT_CODE,
                    "payload.platform is required for a social publish",
                )
            key = f"social:{platform}"
        adapter = self._adapters.get(key)
        if adapter is None:
            raise BusinessError(
                ADAPTER_UNAVAILABLE_CODE,
                f"no {key!r} adapter is registered; inject a transport and"
                " credentials before calling a real integration",
            )
        return adapter

    def execute(self, request: ToolRequest) -> ExecutorResult:
        tool = self.tool(request.tool)
        body = _payload(request)
        adapter = self.adapter_for(tool, body)
        adapter.check_ready(tool.operation)
        return tool.handler(adapter, request)

    def __call__(self, request: ToolRequest) -> ExecutorResult:
        return self.execute(request)


__all__ = [
    "INTEGRATION_BAD_INPUT_CODE",
    "INTEGRATION_TOOLS",
    "INTEGRATION_UNKNOWN_TOOL_CODE",
    "IntegrationTool",
    "IntegrationToolRegistry",
    "TOOL_CRM_CONTACT_READ",
    "TOOL_CRM_CONTACT_UPSERT",
    "TOOL_EMAIL_DRAFT",
    "TOOL_EMAIL_SEND",
    "TOOL_ETSY_LISTING_DRAFT",
    "TOOL_ETSY_LISTING_PRICE",
    "TOOL_ETSY_LISTING_PUBLISH",
    "TOOL_ETSY_LISTINGS_READ",
    "TOOL_ETSY_MESSAGE_SEND",
    "TOOL_ETSY_RECEIPT_REFUND",
    "TOOL_ETSY_SHOP_READ",
    "TOOL_SOCIAL_PUBLISH",
    "get_integration_tool",
    "integration_tools",
]
