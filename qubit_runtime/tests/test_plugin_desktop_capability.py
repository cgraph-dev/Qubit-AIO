"""The public plugin desktop capability: a closed operation family with no fallback."""

from __future__ import annotations

import pytest

from qubit_cli.plugin_desktop import DesktopPluginCapability, _valid_operation

_TOKEN = "t" * 24


@pytest.mark.parametrize("operation", [
    None,
    "context",
    {},
    {"type": "unknown"},
    {"type": "context", "extra": 1},
    {"type": "jobs.list", "job_id": "x"},
    {"type": "control", "context_token": "short", "navigation_generation": 1, "action": "play"},
    {"type": "control", "context_token": _TOKEN, "navigation_generation": -1, "action": "play"},
    {"type": "control", "context_token": _TOKEN, "navigation_generation": 1, "action": "rewind"},
    {"type": "control", "context_token": _TOKEN, "navigation_generation": 1, "action": "seek"},
    {"type": "control", "context_token": _TOKEN, "navigation_generation": 1, "action": "play",
     "position_seconds": 10},
    {"type": "transcript.start", "context_token": "short"},
    {"type": "transcript.start", "context_token": _TOKEN, "range": {"start_seconds": 30, "end_seconds": 10}},
    {"type": "transcript.start", "context_token": _TOKEN, "range": {"start_seconds": -1}},
    {"type": "transcript.start", "context_token": _TOKEN, "language": "e"},
    {"type": "download.start", "context_token": _TOKEN, "quality": "4k"},
    {"type": "job.status", "job_id": "bad id"},
    {"type": "job.cancel", "job_id": ""},
])
def test_malformed_operations_are_rejected(operation):
    assert _valid_operation(operation) is False
    assert DesktopPluginCapability().media_request(session_id="sid", operation=operation) == {
        "error": "invalid-operation",
    }


@pytest.mark.parametrize("operation", [
    {"type": "context"},
    {"type": "jobs.list"},
    {"type": "control", "context_token": _TOKEN, "navigation_generation": 0, "action": "pause"},
    {"type": "control", "context_token": _TOKEN, "navigation_generation": 3, "action": "seek",
     "position_seconds": 12.5},
    {"type": "transcript.start", "context_token": _TOKEN, "language": "en-US"},
    {"type": "download.start", "context_token": _TOKEN, "quality": "1080p"},
    {"type": "job.status", "job_id": "job-1_A"},
])
def test_valid_operations_are_accepted(operation):
    assert _valid_operation(operation) is True


@pytest.mark.parametrize("session_id", ["", "   ", None, 42])
def test_empty_session_is_refused(session_id):
    assert DesktopPluginCapability().media_request(session_id=session_id, operation={"type": "context"}) == {
        "error": "session-unavailable",
    }


def test_valid_operation_routes_through_local_player(monkeypatch):
    calls = []

    def request(session_id, operation):
        calls.append((session_id, operation))
        return {"ok": True, "route": "local"}

    monkeypatch.setattr("gateway.local_player.request", request)
    result = DesktopPluginCapability().media_request(
        session_id="stored", operation={"type": "context"})
    assert result == {"ok": True, "route": "local"}
    assert calls == [("stored", {"type": "context"})]


def test_unbound_lease_falls_back_to_session_route(monkeypatch):
    monkeypatch.setattr("gateway.local_player.request", lambda session_id, operation: None)
    seen = []

    def route(session_id, operation):
        seen.append((session_id, operation))
        return '{"ok": true, "route": "session"}'

    monkeypatch.setattr("tui_gateway.server.request_player_agent_for_session", route)
    result = DesktopPluginCapability().media_request(
        session_id="stored", operation={"type": "jobs.list"})
    assert result == {"ok": True, "route": "session"}
    assert seen == [("stored", {"type": "jobs.list"})]


def test_available_follows_desktop_ui_sink(monkeypatch):
    import tools.desktop_ui as desktop_ui

    monkeypatch.setattr(desktop_ui, "available", lambda: False)
    assert DesktopPluginCapability().available() is False
    monkeypatch.setattr(desktop_ui, "available", lambda: True)
    assert DesktopPluginCapability().available() is True


def test_plugin_context_desktop_capability_is_gated(monkeypatch, tmp_path):
    from qubit_cli.plugins import PluginContext, PluginManager
    from qubit_cli.plugins_manifest import PluginManifest
    import tools.desktop_ui as desktop_ui

    monkeypatch.setattr(desktop_ui, "available", lambda: False)
    context = PluginContext(PluginManifest(name="demo"), PluginManager(scope_key=str(tmp_path)))
    assert context.desktop.available() is False
