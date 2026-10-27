"""Tests for the Apple connector tool definitions and handler factories."""

from __future__ import annotations

import json

import pytest

from connectors.apple_calendar import AppleCalendarConnector
from connectors.apple_common import REASON_INVALID_REQUEST, REASON_NOT_SUPPORTED
from connectors.apple_contacts import AppleContactsConnector
from connectors.apple_health import AppleHealthConnector
from connectors.apple_music import AppleMusicConnector
from tools.apple_connectors_tool import (
    APPLE_CALENDAR_SCHEMA,
    APPLE_CONTACTS_SCHEMA,
    APPLE_HEALTH_SCHEMA,
    APPLE_MUSIC_SCHEMA,
    apple_tool_definitions,
    make_calendar_handler,
    make_contacts_handler,
    make_health_handler,
    make_music_handler,
)
from tools.registry import registry

APPLE_NAMES = (
    "apple_calendar",
    "apple_contacts",
    "apple_health",
    "apple_music",
)


class _Backend:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def list_events(self, **kwargs):
        self.calls.append(("list_events", kwargs))
        return [{
            "event_id": "evt-1",
            "title": "Standup",
            "calendar": "Work",
            "start": "2026-03-15T09:00:00Z",
        }]

    def list_contacts(self, **kwargs):
        self.calls.append(("list_contacts", kwargs))
        return [{"name": "Alice", "emails": ["alice@example.com"]}]

    def search_contacts(self, **kwargs):
        self.calls.append(("search_contacts", kwargs))
        return [{"name": "Alice", "emails": ["alice@example.com"]}]

    def health_summary(self, **kwargs):
        self.calls.append(("health_summary", kwargs))
        return [{"date": "2026-03-15", "steps": 1000}]

    def list_tracks(self, **kwargs):
        self.calls.append(("list_tracks", kwargs))
        return [{"name": "Song", "artist": "Artist"}]


def _json(raw: str) -> dict:
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload


def test_definitions_match_their_registered_tools():
    definitions = apple_tool_definitions()

    assert [item["name"] for item in definitions] == list(APPLE_NAMES)
    assert [item["toolset"] for item in definitions] == list(APPLE_NAMES)
    assert definitions[0]["schema"] is APPLE_CALENDAR_SCHEMA
    assert definitions[1]["schema"] is APPLE_CONTACTS_SCHEMA
    assert definitions[2]["schema"] is APPLE_HEALTH_SCHEMA
    assert definitions[3]["schema"] is APPLE_MUSIC_SCHEMA

    for item in definitions:
        entry = registry.get_entry(item["name"])
        assert entry is not None
        assert entry.toolset == item["toolset"]
        assert entry.schema is item["schema"]
        assert entry.check_fn is not None


@pytest.mark.parametrize(
    ("schema", "expected_params"),
    [
        (APPLE_CALENDAR_SCHEMA, {"days_ahead", "days_behind", "limit"}),
        (APPLE_CONTACTS_SCHEMA, {"action", "query", "limit"}),
        (APPLE_HEALTH_SCHEMA, {"days"}),
        (APPLE_MUSIC_SCHEMA, {"limit"}),
    ],
)
def test_schemas_are_read_only_and_bounded(schema, expected_params):
    assert set(schema["parameters"]["properties"]) == expected_params
    description = schema["description"].lower()
    assert "read-only" in description
    assert "cannot" in description
    assert "not_supported" in description
    assert "macos" in description


@pytest.mark.parametrize(
    ("factory", "framework"),
    [
        (make_calendar_handler, "EventKit"),
        (make_contacts_handler, "Contacts"),
        (make_health_handler, "HealthKit"),
        (make_music_handler, "MusicKit"),
    ],
)
def test_handlers_refuse_on_non_darwin_without_touching_the_backend(
    factory, framework
):
    backend = _Backend()
    handler = factory(platform="linux", backend=backend)

    payload = _json(handler({}))

    assert payload["reason"] == REASON_NOT_SUPPORTED
    assert payload["platform"] == "linux"
    assert framework in payload["required_framework"]
    assert "linux" in payload["error"]
    assert "macOS" in payload["error"]
    assert backend.calls == []


def test_calendar_handler_maps_a_darwin_backend():
    backend = _Backend()
    handler = make_calendar_handler(platform="darwin", backend=backend)

    payload = _json(handler({"days_ahead": 2, "days_behind": 0, "limit": 3}))

    assert payload["source"] == "apple_calendar"
    assert payload["count"] == 1
    assert payload["events"][0]["event_id"] == "evt-1"
    assert payload["events"][0]["title"] == "Standup"
    assert backend.calls == [(
        "list_events",
        {"days_ahead": 2, "days_behind": 0, "limit": 3},
    )]


def test_contacts_handler_maps_list_and_search():
    backend = _Backend()
    handler = make_contacts_handler(platform="darwin", backend=backend)

    listed = _json(handler({"action": "list", "limit": 2}))
    searched = _json(handler({
        "action": "search", "query": "ali", "limit": 2}))

    assert listed["action"] == "list"
    assert listed["contacts"][0]["name"] == "Alice"
    assert searched["action"] == "search"
    assert searched["query"] == "ali"
    assert backend.calls == [
        ("list_contacts", {"limit": 2}),
        ("search_contacts", {"query": "ali", "limit": 2}),
    ]


def test_contacts_handler_rejects_bad_actions_and_missing_queries():
    backend = _Backend()
    handler = make_contacts_handler(platform="darwin", backend=backend)

    bad_action = _json(handler({"action": "delete"}))
    missing_query = _json(handler({"action": "search"}))

    assert bad_action["reason"] == REASON_INVALID_REQUEST
    assert missing_query["reason"] == REASON_INVALID_REQUEST
    assert backend.calls == []


def test_health_and_music_handlers_map_a_darwin_backend():
    backend = _Backend()
    health = make_health_handler(platform="darwin", backend=backend)
    music = make_music_handler(platform="darwin", backend=backend)

    health_payload = _json(health({"days": 3}))
    music_payload = _json(music({"limit": 4}))

    assert health_payload["source"] == "apple_health"
    assert health_payload["days"][0]["steps"] == 1000
    assert music_payload["source"] == "apple_music"
    assert music_payload["tracks"][0]["name"] == "Song"
    assert backend.calls == [
        ("health_summary", {"days": 3}),
        ("list_tracks", {"limit": 4}),
    ]


def test_handlers_use_an_injected_connector_factory():
    class _Connector:
        def list_events(self, **kwargs):
            return [type("Event", (), {
                "as_dict": lambda self: {"event_id": "factory"}})()]

    handler = make_calendar_handler(connector_factory=_Connector)

    payload = _json(handler({}))

    assert payload["events"] == [{"event_id": "factory"}]


def test_handler_redacts_a_backend_failure():
    class _Boom(_Backend):
        def list_tracks(self, **kwargs):
            raise RuntimeError("token=abc /Users/alice/Music")

    handler = make_music_handler(platform="darwin", backend=_Boom())

    payload = _json(handler({}))

    assert payload["reason"] == "tool_error"
    assert "RuntimeError" in payload["error"]
    assert "token" not in payload["error"]
    assert "/Users/alice" not in payload["error"]


def test_connector_classes_are_exposed_for_factories():
    assert AppleCalendarConnector.connector_id == "apple_calendar"
    assert AppleContactsConnector.connector_id == "apple_contacts"
    assert AppleHealthConnector.connector_id == "apple_health"
    assert AppleMusicConnector.connector_id == "apple_music"
