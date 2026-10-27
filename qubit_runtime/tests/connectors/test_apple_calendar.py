"""Hermetic tests for the platform-gated read-only Apple Calendar connector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from connectors.apple_calendar import (
    CONNECTOR_NAME,
    MAX_DAYS,
    MAX_LIMIT,
    REQUIRED_FRAMEWORK,
    AppleCalendarConnector,
)
from connectors.apple_common import (
    APPLE_EPOCH,
    REASON_INVALID_RESPONSE,
    REASON_NOT_SUPPORTED,
    REASON_TOOL_ERROR,
    AppleConnectorError,
)


class _FakeCalendarBackend:
    def __init__(self, rows=None, *, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls: list[dict] = []

    def list_events(self, *, days_ahead, days_behind, limit):
        self.calls.append({
            "days_ahead": days_ahead,
            "days_behind": days_behind,
            "limit": limit,
        })
        if self.error is not None:
            raise self.error
        return self.rows

    def create_event(self, *args, **kwargs):
        raise AssertionError("the connector must never write")


def _apple_seconds(delta_days: int = 0) -> float:
    moment = datetime(2026, 3, 15, 14, 30, tzinfo=timezone.utc) + timedelta(
        days=delta_days)
    return (moment - APPLE_EPOCH).total_seconds()


def test_non_darwin_refuses_before_touching_the_adapter():
    backend = _FakeCalendarBackend([{"title": "should not be read"}])
    connector = AppleCalendarConnector(platform="linux", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_events()

    exc = caught.value
    assert exc.reason == REASON_NOT_SUPPORTED
    assert exc.platform == "linux"
    assert exc.required_framework == REQUIRED_FRAMEWORK
    assert "linux" in exc.detail
    assert "EventKit" in exc.detail
    assert backend.calls == []
    assert connector.available() is False


def test_darwin_maps_the_event_shape_with_bounded_fields():
    long_title = "Planning " + "x" * 500
    backend = _FakeCalendarBackend([
        {
            "event_id": "evt-1",
            "summary": long_title,
            "calendar_title": "Work",
            "start_date": _apple_seconds(),
            "end_date": _apple_seconds(delta_days=1),
            "all_day": 1,
            "location": "Room " + "y" * 500,
            "has_attendees": "true",
        }
    ])
    connector = AppleCalendarConnector(platform="darwin", backend=backend)

    events = connector.list_events(days_ahead=14, days_behind=2, limit=5)

    assert len(events) == 1
    event = events[0]
    assert event.event_id == "evt-1"
    assert event.title.startswith("Planning ")
    assert len(event.title) <= 300
    assert event.calendar == "Work"
    assert event.start == "2026-03-15T14:30:00+00:00"
    assert event.end == "2026-03-16T14:30:00+00:00"
    assert event.all_day is True
    assert event.has_attendees is True
    assert len(event.location) <= 300
    assert connector.available() is True
    assert backend.calls == [{
        "days_ahead": 14, "days_behind": 2, "limit": 5}]


def test_darwin_clamps_the_read_window_and_limit():
    backend = _FakeCalendarBackend([])
    connector = AppleCalendarConnector(platform="darwin", backend=backend)

    connector.list_events(days_ahead=9999, days_behind=9999, limit=9999)

    assert backend.calls == [{
        "days_ahead": MAX_DAYS,
        "days_behind": MAX_DAYS,
        "limit": MAX_LIMIT,
    }]


def test_darwin_skips_rows_without_a_title_or_identity_and_stops_at_limit():
    backend = _FakeCalendarBackend([
        {"calendar": "Work"},
        object(),
        {"title": "One", "event_id": "1"},
        {"title": "Two", "event_id": "2"},
    ])
    connector = AppleCalendarConnector(platform="darwin", backend=backend)

    events = connector.list_events(limit=1)

    assert [event.title for event in events] == ["One"]


def test_darwin_derives_a_bounded_id_when_the_adapter_omits_one():
    backend = _FakeCalendarBackend([
        {"summary": "Standup", "start": "2026-03-15T09:00:00Z"},
    ])
    connector = AppleCalendarConnector(platform="darwin", backend=backend)

    event = connector.list_events()[0]

    assert event.event_id == "Standup:2026-03-15T09:00:00+00:00"


def test_darwin_rejects_an_unexpected_payload():
    backend = _FakeCalendarBackend({"events": []})
    connector = AppleCalendarConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_events()

    assert caught.value.reason == REASON_INVALID_RESPONSE


def test_darwin_redacts_a_framework_failure():
    backend = _FakeCalendarBackend(
        error=RuntimeError("token=abc /Users/alice/Library/Calendar"))
    connector = AppleCalendarConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_events()

    exc = caught.value
    assert exc.reason == REASON_TOOL_ERROR
    assert "RuntimeError" in exc.detail
    assert "secret" not in exc.detail
    assert "token" not in exc.detail
    assert "/Users/alice" not in exc.detail


def test_calendar_surface_has_no_write_operations():
    connector = AppleCalendarConnector(platform="darwin", backend=[])
    for name in (
        "create_event",
        "update_event",
        "delete_event",
        "respond_to_invite",
    ):
        assert not hasattr(connector, name)
    assert CONNECTOR_NAME == "Apple Calendar"
