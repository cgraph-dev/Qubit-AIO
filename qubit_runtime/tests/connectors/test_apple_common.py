"""Hermetic tests for the shared Apple connector platform contract."""

from __future__ import annotations

from datetime import timezone

import pytest

from connectors.apple_common import (
    APPLE_EPOCH,
    REASON_CONNECTOR_NOT_CONFIGURED,
    REASON_INVALID_RESPONSE,
    REASON_NOT_SUPPORTED,
    REASON_TOOL_ERROR,
    AppleConnectorError,
    backend_rows,
    bounded_redacted_detail,
    bounded_values,
    coerce_days,
    coerce_limit,
    ensure_supported,
    map_backend_error,
    normalize_timestamp,
    require_backend,
)


def test_non_darwin_refusal_names_platform_and_framework():
    with pytest.raises(AppleConnectorError) as caught:
        ensure_supported(
            platform="linux",
            required_framework="EventKit (Calendar.app)",
            connector="Apple Calendar",
        )
    exc = caught.value
    assert exc.reason == REASON_NOT_SUPPORTED
    assert exc.platform == "linux"
    assert "linux" in exc.detail
    assert "EventKit" in exc.detail
    assert exc.required_framework == "EventKit (Calendar.app)"
    assert exc.as_extra()["reason"] == REASON_NOT_SUPPORTED


def test_darwin_without_adapter_is_connector_not_configured():
    with pytest.raises(AppleConnectorError) as caught:
        require_backend(
            None,
            platform="darwin",
            required_framework="HealthKit",
            connector="Apple Health",
        )
    assert caught.value.reason == REASON_CONNECTOR_NOT_CONFIGURED
    assert caught.value.platform == "darwin"
    assert "HealthKit" in caught.value.detail


def test_backend_rows_rejects_none_strings_and_mappings():
    for payload in (None, "rows", b"rows", {"events": []}):
        with pytest.raises(AppleConnectorError) as caught:
            backend_rows(
                payload,
                connector="Apple Calendar",
                required_framework="EventKit (Calendar.app)",
                platform="darwin",
            )
        assert caught.value.reason == REASON_INVALID_RESPONSE


def test_backend_rows_drops_non_mapping_rows():
    rows = backend_rows(
        [{"title": "ok"}, object(), "bad", {"title": "also"}],
        connector="Apple Calendar",
        required_framework="EventKit (Calendar.app)",
        platform="darwin",
    )
    assert [row["title"] for row in rows] == ["ok", "also"]


def test_map_backend_error_never_echoes_the_framework_message():
    exc = map_backend_error(
        RuntimeError("secret token=abc at /Users/alice/Library"),
        connector="Apple Contacts",
        required_framework="Contacts (CNContactStore)",
        platform="darwin",
    )
    assert exc.reason == REASON_TOOL_ERROR
    assert "RuntimeError" in exc.detail
    assert "secret" not in exc.detail
    assert "/Users/alice" not in exc.detail
    assert "token" not in exc.detail


def test_bounded_redacted_detail_is_one_line_and_bounded():
    detail = bounded_redacted_detail("a\n\nb " + "x" * 1000, limit=40)
    assert "\n" not in detail
    assert len(detail) <= 40


def test_limits_and_days_are_clamped():
    assert coerce_limit("999", default=20, maximum=100) == 100
    assert coerce_limit(0, default=20, maximum=100) == 20
    assert coerce_limit("nope", default=20, maximum=100) == 20
    assert coerce_days(9999, default=7, maximum=366) == 366
    assert coerce_days(0, default=7, maximum=366, minimum=1) == 1


def test_bounded_values_dedupe_and_extract_mapping_values():
    values = bounded_values(
        [
            {"value": "a@example.com"},
            "A@example.com",
            {"address": "b@example.com"},
            "",
            42,
        ],
        limit=5,
    )
    assert values == ("a@example.com", "b@example.com", "42")


def test_normalize_timestamp_accepts_apple_seconds_and_iso():
    assert normalize_timestamp(0) == APPLE_EPOCH.isoformat()
    assert APPLE_EPOCH.tzinfo is timezone.utc
    assert normalize_timestamp("2026-03-15T14:30:00Z") == (
        "2026-03-15T14:30:00+00:00")
    assert normalize_timestamp("not-a-date") == "not-a-date"
    assert normalize_timestamp(None) == ""
