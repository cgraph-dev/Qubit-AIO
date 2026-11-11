"""Etsy adapter — typed, dry-run, approval/rate-limit/idempotency contract."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from _integration_stubs import FakeClock, RecordingTransport
from voicebridge.integrations import etsy as etsy_module
from voicebridge.integrations.base import (
    ADAPTER_APPROVAL_REQUIRED_CODE,
    ADAPTER_BAD_INPUT_CODE,
    ADAPTER_IDEMPOTENCY_CONFLICT_CODE,
    ADAPTER_NOT_CONFIGURED_CODE,
    ADAPTER_RATE_LIMITED_CODE,
    ADAPTER_TRANSPORT_ERROR_CODE,
    ADAPTER_UNAVAILABLE_CODE,
    AdapterError,
    ApprovalToken,
)
from voicebridge.integrations.etsy import (
    APPROVAL_MESSAGE,
    APPROVAL_PRICE,
    APPROVAL_PUBLISH,
    APPROVAL_REFUND,
    EtsyAdapter,
)

CREDS = {"api_key": "key-1", "access_token": "token-1"}


def _adapter(
    *,
    transport=None,
    credentials=CREDS,
    shop_id="shop-1",
    dry_run=True,
    rate_limit=None,
    rate_window_seconds=None,
    clock=None,
):
    return EtsyAdapter(
        shop_id=shop_id,
        transport=transport if transport is not None else RecordingTransport(),
        credentials=credentials,
        dry_run=dry_run,
        rate_limit=rate_limit,
        rate_window_seconds=rate_window_seconds,
        clock=clock,
    )


def test_missing_transport_is_a_typed_unavailable_refusal():
    adapter = EtsyAdapter(shop_id="shop-1", credentials=CREDS)
    with pytest.raises(AdapterError) as exc:
        adapter.get_shop()
    assert exc.value.code == ADAPTER_UNAVAILABLE_CODE


def test_missing_credentials_is_a_typed_not_configured_refusal():
    adapter = _adapter(credentials={})
    with pytest.raises(AdapterError) as exc:
        adapter.list_listings()
    assert exc.value.code == ADAPTER_NOT_CONFIGURED_CODE
    assert "api_key" in exc.value.detail


def test_missing_shop_id_is_a_typed_not_configured_refusal():
    adapter = _adapter(shop_id="")
    with pytest.raises(AdapterError) as exc:
        adapter.get_shop()
    assert exc.value.code == ADAPTER_NOT_CONFIGURED_CODE
    assert "shop_id" in exc.value.detail


def test_reads_are_dry_run_by_default_and_do_not_call_the_transport():
    transport = RecordingTransport({"shop_id": "shop-1"})
    adapter = _adapter(transport=transport)
    result = adapter.get_shop()
    assert result.dry_run is True
    assert result.executed is False
    assert result.would_do.startswith("GET ")
    assert transport.calls == []


def test_reads_call_the_injected_transport_only_when_not_dry_run():
    transport = RecordingTransport({"shop_id": "shop-1"})
    adapter = _adapter(transport=transport, dry_run=False)
    result = adapter.get_shop()
    assert result.executed is True
    assert result.dry_run is False
    assert result.data == {"shop_id": "shop-1"}
    assert transport.calls[0]["method"] == "GET"
    assert "/shops/shop-1" in transport.calls[0]["path"]


def test_digital_draft_requires_a_file_and_validates_pricing():
    adapter = _adapter()
    with pytest.raises(AdapterError) as exc:
        adapter.create_digital_listing_draft(
            title="Planner",
            description="A digital planner",
            price_minor=500,
            idempotency_key="draft-1",
            digital_files=(),
        )
    assert exc.value.code == ADAPTER_BAD_INPUT_CODE
    with pytest.raises(AdapterError) as exc:
        adapter.create_digital_listing_draft(
            title="Planner",
            description="A digital planner",
            price_minor=0,
            idempotency_key="draft-1",
            digital_files=("planner.pdf",),
        )
    assert exc.value.code == ADAPTER_BAD_INPUT_CODE


def test_digital_draft_is_a_dry_run_without_an_approval_token():
    transport = RecordingTransport()
    adapter = _adapter(transport=transport)
    result = adapter.create_digital_listing_draft(
        title="Planner",
        description="A digital planner",
        price_minor=500,
        idempotency_key="draft-1",
        tags=("planner", "printable"),
        digital_files=("planner.pdf",),
    )
    assert result.executed is False
    assert result.dry_run is True
    assert result.would_do.startswith("POST ")
    assert transport.calls == []


@pytest.mark.parametrize(
    "method, args, approval_action",
    [
        (
            "publish_listing",
            {"listing_id": "listing-1", "idempotency_key": "pub-1"},
            APPROVAL_PUBLISH,
        ),
        (
            "update_listing_price",
            {
                "listing_id": "listing-1",
                "price_minor": 900,
                "idempotency_key": "price-1",
            },
            APPROVAL_PRICE,
        ),
        (
            "refund_receipt",
            {
                "receipt_id": "receipt-1",
                "amount_minor": 500,
                "idempotency_key": "refund-1",
            },
            APPROVAL_REFUND,
        ),
        (
            "send_message",
            {
                "receipt_id": "receipt-1",
                "body": "Thanks for your order",
                "idempotency_key": "msg-1",
            },
            APPROVAL_MESSAGE,
        ),
    ],
)
def test_gated_operations_refuse_without_the_right_approval(
    method, args, approval_action
):
    adapter = _adapter()
    with pytest.raises(AdapterError) as exc:
        getattr(adapter, method)(**args)
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    wrong = ApprovalToken("apr-1", "some.other.action")
    with pytest.raises(AdapterError) as exc:
        getattr(adapter, method)(**args, approval=wrong)
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    revoked = ApprovalToken("apr-1", approval_action, approved=False)
    with pytest.raises(AdapterError) as exc:
        getattr(adapter, method)(**args, approval=revoked)
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE


@pytest.mark.parametrize(
    "method, args, approval_action",
    [
        (
            "publish_listing",
            {"listing_id": "listing-1", "idempotency_key": "pub-1"},
            APPROVAL_PUBLISH,
        ),
        (
            "update_listing_price",
            {
                "listing_id": "listing-1",
                "price_minor": 900,
                "idempotency_key": "price-1",
            },
            APPROVAL_PRICE,
        ),
        (
            "refund_receipt",
            {
                "receipt_id": "receipt-1",
                "amount_minor": 500,
                "idempotency_key": "refund-1",
            },
            APPROVAL_REFUND,
        ),
        (
            "send_message",
            {
                "receipt_id": "receipt-1",
                "body": "Thanks for your order",
                "idempotency_key": "msg-1",
            },
            APPROVAL_MESSAGE,
        ),
    ],
)
def test_gated_operations_return_a_dry_run_plan_with_approval(
    method, args, approval_action
):
    transport = RecordingTransport()
    adapter = _adapter(transport=transport)
    token = ApprovalToken("apr-1", approval_action)
    result = getattr(adapter, method)(**args, approval=token)
    assert result.executed is False
    assert result.dry_run is True
    assert result.approval_id == "apr-1"
    assert transport.calls == []


def test_idempotent_replay_returns_the_original_result():
    transport = RecordingTransport({"ok": True})
    adapter = _adapter(transport=transport, dry_run=False)
    first = adapter.create_draft_listing(
        {"title": "Planner"}, idempotency_key="same-key"
    )
    second = adapter.create_draft_listing(
        {"title": "Planner"}, idempotency_key="same-key"
    )
    assert first.executed is True
    assert first.replayed is False
    assert second.executed is True
    assert second.replayed is True
    assert second.request_fingerprint == first.request_fingerprint
    assert len(transport.calls) == 1


def test_idempotency_key_with_a_different_request_is_a_conflict():
    adapter = _adapter()
    adapter.create_draft_listing(
        {"title": "Planner"}, idempotency_key="same-key"
    )
    with pytest.raises(AdapterError) as exc:
        adapter.create_draft_listing(
            {"title": "Different"}, idempotency_key="same-key"
        )
    assert exc.value.code == ADAPTER_IDEMPOTENCY_CONFLICT_CODE


def test_rate_limit_refuses_before_the_transport_is_called():
    clock = FakeClock()
    transport = RecordingTransport()
    adapter = _adapter(
        transport=transport,
        dry_run=False,
        rate_limit=1,
        rate_window_seconds=60,
        clock=clock,
    )
    adapter.get_shop()
    with pytest.raises(AdapterError) as exc:
        adapter.get_shop()
    assert exc.value.code == ADAPTER_RATE_LIMITED_CODE
    assert len(transport.calls) == 1
    clock.advance(61)
    adapter.get_shop()
    assert len(transport.calls) == 2


def test_transport_failure_is_typed_and_never_a_false_success():
    transport = RecordingTransport(fail=True)
    adapter = _adapter(transport=transport, dry_run=False)
    with pytest.raises(AdapterError) as exc:
        adapter.get_shop()
    assert exc.value.code == ADAPTER_TRANSPORT_ERROR_CODE
    assert "transport exploded" not in exc.value.detail


def test_no_dry_run_result_is_marked_executed():
    adapter = _adapter()
    assert adapter.get_shop().executed is False
    assert adapter.list_listings().executed is False
    assert adapter.get_listing("listing-1").executed is False
    assert adapter.list_receipts().executed is False
    assert adapter.get_inventory("listing-1").executed is False


def test_adapter_modules_do_not_import_network_clients():
    modules = [
        etsy_module,
        sys.modules["voicebridge.integrations.base"],
        sys.modules["voicebridge.integrations.social"],
        sys.modules["voicebridge.integrations.crm"],
        sys.modules["voicebridge.integrations.email"],
    ]
    forbidden = (
        "import requests",
        "import urllib",
        "import httpx",
        "import http.client",
        "import socket",
        "import aiohttp",
    )
    for module in modules:
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, (module.__name__, token)


def test_socket_is_never_opened(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("adapter opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    transport = RecordingTransport({"ok": True})
    adapter = _adapter(transport=transport, dry_run=False)
    adapter.get_shop()
    adapter.create_draft_listing(
        {"title": "Planner"}, idempotency_key="net-1"
    )
