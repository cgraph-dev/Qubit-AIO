"""Wiring tests for the read-only connector tools on the real tool surface.

These cover registry registration, toolset/config classification, credential
visibility, handler dispatch with fakes, the Apple platform gate, and name
uniqueness. They never touch the network or the owner's credentials.
"""

from __future__ import annotations

import json
import sys

import pytest

import tools.apple_connectors_tool as apple_tool
import tools.dropbox_tool  # noqa: F401 — registers ``dropbox``
import tools.google_calendar_tool  # noqa: F401 — registers ``google_calendar``
import tools.google_contacts_tool  # noqa: F401 — existing contacts wiring
from connectors.apple_common import REASON_NOT_SUPPORTED
from connectors.dropbox import DropboxEntry
from connectors.google_calendar import Event
from qubit_cli.tools_config import CONFIGURABLE_TOOLSETS, _DEFAULT_OFF_TOOLSETS
from tools.registry import invalidate_check_fn_cache, registry
from toolsets import TOOLSETS, _QUBIT_CORE_TOOLS, resolve_toolset, validate_toolset


CONNECTOR_TOOLSETS = {
    "google_contacts": "google_contacts",
    "google_calendar": "google_calendar",
    "dropbox": "dropbox",
    "apple_calendar": "apple_calendar",
    "apple_contacts": "apple_contacts",
    "apple_health": "apple_health",
    "apple_music": "apple_music",
}

GOOGLE_TOKEN = {
    "type": "authorized_user",
    "token": "fake-access-token",
    "refresh_token": "fake-refresh-token",
}


def _configured_names(names):
    return {
        item["function"]["name"]
        for item in registry.get_definitions(set(names))
    }


def _write_token(tmp_path, filename: str, payload: dict) -> None:
    (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")
    invalidate_check_fn_cache()


@pytest.mark.parametrize("name,toolset", sorted(CONNECTOR_TOOLSETS.items()))
def test_connector_tools_are_classified_in_their_named_toolsets(name, toolset):
    entry = registry.get_entry(name)
    assert entry is not None, f"{name} is not registered"
    assert entry.toolset == toolset
    assert entry.schema["name"] == name
    assert entry.check_fn is not None

    assert name in _QUBIT_CORE_TOOLS
    assert toolset in TOOLSETS
    assert TOOLSETS[toolset]["tools"] == [name]
    assert validate_toolset(toolset) is True
    assert resolve_toolset(toolset) == [name]

    configurable = {key for key, _label, _description in CONFIGURABLE_TOOLSETS}
    assert toolset in configurable
    assert toolset not in _DEFAULT_OFF_TOOLSETS


def test_connector_names_are_unique_and_map_back_to_known_toolsets():
    names = list(CONNECTOR_TOOLSETS)
    assert len(names) == len(set(names))
    assert len(_QUBIT_CORE_TOOLS) == len(set(_QUBIT_CORE_TOOLS))
    assert all(_QUBIT_CORE_TOOLS.count(name) == 1 for name in names)

    for name in names:
        entry = registry.get_entry(name)
        assert entry is not None
        assert entry.toolset in TOOLSETS, f"{name} has unknown toolset {entry.toolset!r}"
        assert name in TOOLSETS[entry.toolset]["tools"]
        assert entry.schema.get("name") == name


@pytest.mark.parametrize("name", ["google_contacts", "google_calendar"])
def test_google_tools_appear_only_with_the_existing_workspace_token(
    name, monkeypatch, tmp_path
):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    invalidate_check_fn_cache()
    assert registry.get_entry(name) is not None

    assert name not in _configured_names([name])
    _write_token(tmp_path, "google_token.json", GOOGLE_TOKEN)
    assert _configured_names([name]) == {name}


def test_dropbox_appears_only_with_its_token(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    invalidate_check_fn_cache()
    assert "dropbox" not in _configured_names(["dropbox"])

    _write_token(tmp_path, "dropbox_token.json", {"access_token": "sl.fake"})
    assert _configured_names(["dropbox"]) == {"dropbox"}


def test_google_token_without_a_usable_value_does_not_admit_calendar(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    _write_token(tmp_path, "google_token.json", {"type": "authorized_user"})
    assert "google_calendar" not in _configured_names(["google_calendar"])


def test_apple_tools_are_hidden_off_darwin(monkeypatch):
    monkeypatch.setattr(apple_tool.sys, "platform", "linux")
    for name in ("apple_calendar", "apple_contacts", "apple_health", "apple_music"):
        assert registry.get_entry(name).check_fn() is False


def test_apple_tools_appear_on_darwin_with_their_framework(monkeypatch):
    monkeypatch.setattr(apple_tool.sys, "platform", "darwin")
    monkeypatch.setattr(apple_tool, "_framework_module_available", lambda _name: True)
    monkeypatch.setattr(apple_tool.shutil, "which", lambda _name: "/usr/bin/osascript")

    for name in ("apple_calendar", "apple_contacts", "apple_health", "apple_music"):
        assert registry.get_entry(name).check_fn() is True


def test_apple_tools_stay_hidden_on_darwin_without_a_framework(monkeypatch):
    monkeypatch.setattr(apple_tool.sys, "platform", "darwin")
    monkeypatch.setattr(apple_tool, "_framework_module_available", lambda _name: False)
    monkeypatch.setattr(apple_tool.shutil, "which", lambda _name: None)

    for name in ("apple_calendar", "apple_contacts", "apple_health", "apple_music"):
        assert registry.get_entry(name).check_fn() is False


class _FakeGoogleCalendar:
    def __init__(self):
        self.calls = []

    def list_events(self, time_min, time_max="", limit=20, *, calendar_id="primary"):
        self.calls.append(("list", time_min, time_max, limit, calendar_id))
        return [Event(
            event_id="evt-1",
            title="Standup",
            start="2026-10-02T09:00:00Z",
            end="2026-10-02T10:00:00Z",
            all_day=False,
            attendee_count=3,
            status="confirmed",
        )]


def test_registry_dispatch_google_calendar_with_a_fake():
    fake = _FakeGoogleCalendar()
    raw = registry.dispatch(
        "google_calendar",
        {"action": "list", "time_min": "2026-10-01T00:00:00Z", "limit": 5},
        connector_factory=lambda: fake,
    )
    payload = json.loads(raw)

    assert payload["source"] == "google_calendar"
    assert payload["count"] == 1
    assert payload["events"][0]["title"] == "Standup"
    assert fake.calls == [
        ("list", "2026-10-01T00:00:00Z", "", 5, "primary")]


class _FakeDropbox:
    def __init__(self):
        self.calls = []

    def list_folder(self, path="", *, limit=20, recursive=False):
        self.calls.append(("list_folder", path, limit, recursive))
        return [DropboxEntry(
            entry_type="file",
            name="report.pdf",
            path="/Docs/report.pdf",
            entry_id="id:file1",
            size=1234,
            client_modified="2026-01-02T03:04:05Z",
            server_modified="2026-01-02T03:04:06Z",
            rev="01abc",
            content_hash="hash1",
        )]


def test_registry_dispatch_dropbox_with_a_fake():
    fake = _FakeDropbox()
    raw = registry.dispatch(
        "dropbox", {"action": "list_folder", "path": "/Docs", "limit": 4},
        connector_factory=lambda: fake,
    )
    payload = json.loads(raw)

    assert payload["source"] == "dropbox"
    assert payload["entries"][0]["name"] == "report.pdf"
    assert fake.calls == [("list_folder", "/Docs", 4, False)]


@pytest.mark.parametrize(
    "name,framework",
    [
        ("apple_calendar", "EventKit"),
        ("apple_contacts", "Contacts"),
        ("apple_health", "HealthKit"),
        ("apple_music", "MusicKit"),
    ],
)
def test_registered_apple_handlers_refuse_non_darwin(monkeypatch, name, framework):
    import connectors.apple_calendar as apple_calendar
    import connectors.apple_contacts as apple_contacts
    import connectors.apple_health as apple_health
    import connectors.apple_music as apple_music

    monkeypatch.setattr(apple_calendar, "current_platform", lambda: "linux")
    monkeypatch.setattr(apple_contacts, "current_platform", lambda: "linux")
    monkeypatch.setattr(apple_health, "current_platform", lambda: "linux")
    monkeypatch.setattr(apple_music, "current_platform", lambda: "linux")

    payload = json.loads(registry.dispatch(name, {}))

    assert payload["reason"] == REASON_NOT_SUPPORTED
    assert payload["platform"] == "linux"
    assert framework in payload["required_framework"]
    assert "macOS" in payload["error"]
