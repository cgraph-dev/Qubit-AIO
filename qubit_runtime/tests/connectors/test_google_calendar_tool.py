"""Tests for the unregistered ``google_calendar`` tool definition."""

from __future__ import annotations

import json

from connectors.google_calendar import (
    Event,
    GoogleCalendarConnector,
    GoogleCalendarError,
    REASON_MISSING_CREDENTIAL,
)
from tools.google_calendar_tool import (
    GOOGLE_CALENDAR_SCHEMA,
    build_google_calendar_tool,
    handle_google_calendar,
)
from tools.registry import registry


class _FakeConnector:
    def __init__(self, *, events=None, error=None) -> None:
        self.events = events or []
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def configured(self) -> bool:
        return True

    def list_events(self, time_min, time_max="", limit=20, *, calendar_id="primary"):
        self.calls.append(("list", {
            "time_min": time_min, "time_max": time_max,
            "limit": limit, "calendar_id": calendar_id,
        }))
        if self.error:
            raise self.error
        return list(self.events)

    def search(self, query, time_min, time_max="", limit=20, *, calendar_id="primary"):
        self.calls.append(("search", {
            "query": query, "time_min": time_min, "time_max": time_max,
            "limit": limit, "calendar_id": calendar_id,
        }))
        if self.error:
            raise self.error
        return list(self.events)


def _event(title: str = "Standup") -> Event:
    return Event(
        event_id="evt-1",
        title=title,
        start="2026-10-02T09:00:00Z",
        end="2026-10-02T10:00:00Z",
        all_day=False,
        attendee_count=3,
        status="confirmed",
    )


def test_factory_exposes_a_definition_without_global_wiring():
    definition = build_google_calendar_tool()
    assert definition["name"] == "google_calendar"
    assert definition["toolset"] == "google_calendar"
    assert definition["schema"] is GOOGLE_CALENDAR_SCHEMA
    assert definition["handler"] is handle_google_calendar
    assert definition["check_fn"] is not None
    assert registry.get_entry("google_calendar") is None


def test_schema_is_read_only_and_bounded():
    params = GOOGLE_CALENDAR_SCHEMA["parameters"]["properties"]
    assert set(params) == {
        "action", "time_min", "time_max", "query", "limit", "calendar_id"}
    assert params["action"]["enum"] == ["list", "search"]
    assert GOOGLE_CALENDAR_SCHEMA["parameters"]["required"] == [
        "action", "time_min"]
    description = GOOGLE_CALENDAR_SCHEMA["description"].lower()
    assert "read-only" in description
    assert "cannot create" in description
    assert "calendar.readonly" in description


def test_dispatch_list_returns_bounded_json():
    fake = _FakeConnector(events=[_event()])
    raw = handle_google_calendar(
        {"action": "list", "time_min": "2026-10-01T00:00:00Z",
         "time_max": "2026-11-01T00:00:00Z", "limit": 3},
        connector_factory=lambda: fake)
    payload = json.loads(raw)

    assert payload["source"] == "google_calendar"
    assert payload["action"] == "list"
    assert payload["count"] == 1
    assert payload["events"][0]["title"] == "Standup"
    assert payload["events"][0]["attendee_count"] == 3
    assert "query" not in payload
    assert fake.calls == [("list", {
        "time_min": "2026-10-01T00:00:00Z",
        "time_max": "2026-11-01T00:00:00Z",
        "limit": 3,
        "calendar_id": "primary",
    })]


def test_dispatch_search_passes_query_and_limits():
    fake = _FakeConnector(events=[_event("Board meeting")])
    raw = handle_google_calendar(
        {"action": "search", "query": "board",
         "time_min": "2026-10-01T00:00:00Z", "limit": 5},
        connector_factory=lambda: fake)
    payload = json.loads(raw)

    assert payload["action"] == "search"
    assert payload["query"] == "board"
    assert payload["events"][0]["title"] == "Board meeting"
    assert fake.calls == [("search", {
        "query": "board",
        "time_min": "2026-10-01T00:00:00Z",
        "time_max": "",
        "limit": 5,
        "calendar_id": "primary",
    })]


def test_dispatch_honours_an_explicit_calendar_id():
    fake = _FakeConnector()
    handle_google_calendar(
        {"action": "list", "time_min": "2026-10-01T00:00:00Z",
         "calendar_id": "team@example.com"},
        connector_factory=lambda: fake)
    assert fake.calls[0][1]["calendar_id"] == "team@example.com"


def test_dispatch_search_without_query_is_a_typed_error():
    raw = handle_google_calendar(
        {"action": "search", "time_min": "2026-10-01T00:00:00Z"},
        connector_factory=lambda: _FakeConnector())
    payload = json.loads(raw)
    assert payload["reason"] == "invalid_request"
    assert "query" in payload["error"]


def test_dispatch_requires_time_min():
    raw = handle_google_calendar(
        {"action": "list"}, connector_factory=lambda: _FakeConnector())
    payload = json.loads(raw)
    assert payload["reason"] == "invalid_request"
    assert "time_min" in payload["error"]


def test_dispatch_rejects_unknown_write_action():
    raw = handle_google_calendar(
        {"action": "delete", "time_min": "2026-10-01T00:00:00Z"},
        connector_factory=lambda: _FakeConnector())
    payload = json.loads(raw)
    assert payload["reason"] == "invalid_request"
    assert "read-only" in payload["error"]


def test_dispatch_missing_credential_keeps_the_typed_reason():
    error = GoogleCalendarError(
        REASON_MISSING_CREDENTIAL,
        "google_token.json not found; run the google-workspace setup")
    raw = handle_google_calendar(
        {"action": "list", "time_min": "2026-10-01T00:00:00Z"},
        connector_factory=lambda: _FakeConnector(error=error))
    payload = json.loads(raw)
    assert payload["reason"] == "missing_credential"
    assert "google-workspace setup" in payload["error"]


def test_dispatch_missing_scope_names_the_scope():
    error = GoogleCalendarError(
        "missing_scope",
        "the stored Google token is missing the Google Calendar scope",
        scope="https://www.googleapis.com/auth/calendar.readonly")
    raw = handle_google_calendar(
        {"action": "list", "time_min": "2026-10-01T00:00:00Z"},
        connector_factory=lambda: _FakeConnector(error=error))
    payload = json.loads(raw)
    assert payload["reason"] == "missing_scope"
    assert payload["scope"] == "https://www.googleapis.com/auth/calendar.readonly"


def test_dispatch_unexpected_failure_does_not_leak_the_exception():
    class _Broken(_FakeConnector):
        def list_events(self, *args, **kwargs):
            raise RuntimeError("secret ya29.SUPER_SECRET_TOKEN")

    raw = handle_google_calendar(
        {"action": "list", "time_min": "2026-10-01T00:00:00Z"},
        connector_factory=lambda: _Broken())
    payload = json.loads(raw)
    assert payload["reason"] == "tool_error"
    assert "ya29.SUPER_SECRET_TOKEN" not in raw


def test_check_fn_false_without_a_token(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    definition = build_google_calendar_tool()
    assert definition["check_fn"]() is False


def test_check_fn_true_with_the_existing_token(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    (tmp_path / "google_token.json").write_text(
        json.dumps({"type": "authorized_user", "token": "t", "refresh_token": "r"}),
        encoding="utf-8")
    definition = build_google_calendar_tool()
    assert definition["check_fn"]() is True


def test_default_factory_is_the_real_connector(monkeypatch):
    seen: list[object] = []

    def fake_list(self, time_min, time_max="", limit=20, *, calendar_id="primary"):
        seen.append(self)
        return []

    monkeypatch.setattr(GoogleCalendarConnector, "list_events", fake_list)
    payload = json.loads(handle_google_calendar(
        {"action": "list", "time_min": "2026-10-01T00:00:00Z"}))
    assert payload["count"] == 0
    assert seen and isinstance(seen[0], GoogleCalendarConnector)
