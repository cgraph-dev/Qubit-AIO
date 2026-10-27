"""Display health for the voice-edit service (no HTTP server, no X)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import health, routes


class FakeCfg:
    path = "voice.yaml"

    def get(self, *args, default=None):
        return default


class FakeService:
    def status(self):
        return {"enabled": True, "helper_live": False, "session": {}}


@pytest.fixture()
def routed(monkeypatch):
    monkeypatch.setattr(routes, "service_for", lambda cfg: FakeService())
    return routes


def _clear_display(monkeypatch):
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
        monkeypatch.delenv(key, raising=False)


def test_x11_session_is_healthy(monkeypatch, routed):
    _clear_display(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")

    report = health.display_health()
    assert report == {
        "x11": True,
        "session_type": "x11",
        "display_present": True,
        "wayland_present": False,
        "reason": "",
        "hint": "",
    }

    status, payload = routed.handle(
        FakeCfg(), "GET", "/api/voice-edit/status", {})
    assert status == 200
    assert payload["display_health"] == report


def test_wayland_alone_is_unsupported_with_actionable_hint(monkeypatch, routed):
    _clear_display(monkeypatch)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    report = health.display_health()
    assert report["x11"] is False
    assert report["session_type"] == "wayland"
    assert report["display_present"] is False
    assert report["wayland_present"] is True
    assert report["reason"] == "unsupported_session"
    assert "systemctl --user import-environment DISPLAY XAUTHORITY" in report["hint"]
    assert "systemctl --user restart qubit-voicebridge" in report["hint"]

    status, payload = routed.handle(
        FakeCfg(), "GET", "/api/voice-edit/status", {})
    assert status == 200
    assert payload["display_health"]["reason"] == "unsupported_session"
    assert payload["display_health"]["hint"] == report["hint"]


def test_no_display_reports_typed_reason(monkeypatch, routed):
    _clear_display(monkeypatch)

    report = health.display_health()
    assert report["x11"] is False
    assert report["session_type"] == "unknown"
    assert report["display_present"] is False
    assert report["wayland_present"] is False
    assert report["reason"] == "unsupported_session"
    assert "import-environment DISPLAY XAUTHORITY" in report["hint"]

    status, payload = routed.handle(
        FakeCfg(), "HEAD", "/api/voice-edit/status", {})
    assert status == 200
    assert payload["display_health"] == report


def test_display_values_never_reach_payload_or_logs(monkeypatch, caplog, routed):
    _clear_display(monkeypatch)
    display_value = "SECRET-DISPLAY-:77"
    xauth_value = "SECRET-XAUTHORITY-/tmp/xauth"
    monkeypatch.setenv("DISPLAY", display_value)
    monkeypatch.setenv("XAUTHORITY", xauth_value)
    monkeypatch.setattr(
        health.DesktopAdapter, "session",
        lambda self: {"session_type": "unknown", "display": "", "x11": False})

    caplog.set_level(logging.DEBUG)
    report = health.display_health()
    status, payload = routed.handle(
        FakeCfg(), "GET", "/api/voice-edit/status", {})

    assert status == 200
    assert report["reason"] == "unsupported_session"
    assert display_value not in repr(report)
    assert display_value not in repr(payload)
    assert xauth_value not in repr(payload)
    assert display_value not in caplog.text
    assert xauth_value not in caplog.text
