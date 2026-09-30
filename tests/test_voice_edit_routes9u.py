"""Authenticated voice-edit route handler tests (no HTTP server, no X)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import routes
from voicebridge.voice_edit.schema import (InsertionResult, SelectionSnapshot,
                                           TransformResult)
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import TransformEngine, digest_text


class FakeCfg:
    def __init__(self, data, path):
        self.data = data
        self.path = path

    def get(self, *p, default=None):
        node = self.data
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


class FakeDesktop:
    def __init__(self):
        self.selection = "selected words"

    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return True

    def capture_selection(self, cfg=None):
        return SelectionSnapshot(
            available=True, text=self.selection, char_count=len(self.selection),
            digest=digest_text(self.selection), captured_via="clipboard",
            app_class="code", window_id="42", focused=True)

    def insert_text(self, text, snap, cfg=None, method=""):
        return InsertionResult(ok=True, method="clipboard_paste", app_class="code")

    def revert_last(self, snap, cfg=None):
        return InsertionResult(ok=True, method="undo", reverted=True)

    def is_fullscreen(self, window_id=""):
        return False


class FakeResult:
    ok = True
    text = "PROPOSED"
    code = ""
    message = ""
    provider = "ollama"
    model = "qubit-brain4"
    details = {"locality": "local"}


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n  confirm_before_replace: true\n")
    return FakeCfg({"voice_edit": {"enabled": True,
                                   "confirm_before_replace": True}}, p)


@pytest.fixture()
def svc(cfg, monkeypatch):
    service = VoiceEditService(
        cfg, desktop=FakeDesktop(),
        engine=TransformEngine(runner=lambda *a, **k: FakeResult()),
        audit=lambda *a: None, estop=lambda cfg: False)
    monkeypatch.setattr(routes, "service_for", lambda c: service)
    routes.reset_service()
    return service


def test_not_our_path_returns_none():
    assert routes.handle(FakeCfg({}, None), "GET", "/api/other", {}) is None


def test_status_route(cfg, svc):
    status, payload = routes.handle(cfg, "GET", "/api/voice-edit/status", {})
    assert status == 200 and payload["enabled"] is True
    assert payload["privacy"].startswith("no selected text")


def test_log_route_returns_events_and_summary(cfg, svc, monkeypatch):
    from voicebridge.voice_edit import logview
    monkeypatch.setattr(logview, "recent_events",
                        lambda limit=200, **k: [{"ts": 1,
                                                 "event": "voice_edit.wake.hit"}])
    monkeypatch.setattr(logview, "summary",
                        lambda ev: {"count": len(ev), "errors": 0,
                                    "last_error": None, "last_event": ev[-1]})
    status, payload = routes.handle(cfg, "GET", "/api/voice-edit/log", {})
    assert status == 200
    assert payload["events"][0]["event"] == "voice_edit.wake.hit"
    assert payload["summary"]["count"] == 1


def test_settings_get_and_put(cfg, svc):
    status, payload = routes.handle(cfg, "GET", "/api/voice-edit/settings", {})
    assert status == 200 and payload["settings"]["enabled"] is True
    status, payload = routes.handle(cfg, "PUT", "/api/voice-edit/settings",
                                   {"routing": "local"})
    assert status == 200 and payload["settings"]["routing"] == "local"


def test_settings_restore_route(cfg, svc):
    status, payload = routes.handle(cfg, "POST",
                                    "/api/voice-edit/settings/restore",
                                    {"action": "shorten"})
    assert status == 200 and "settings" in payload


def test_export_import_routes(cfg, svc):
    status, payload = routes.handle(cfg, "GET", "/api/voice-edit/export", {})
    assert status == 200 and payload["export"]["schema"] == "qubit-voice-edit/1"
    status, payload = routes.handle(cfg, "POST", "/api/voice-edit/import",
                                    {"routing": "auto"})
    assert status == 200


def test_full_route_flow(cfg, svc):
    status, session = routes.handle(cfg, "POST", "/api/voice-edit/session", {})
    assert status == 200 and session["request_id"]
    rid = session["request_id"]
    status, cap = routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/capture", {})
    assert status == 200 and cap["selection"]["available"] is True
    status, prev = routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/transform",
                                 {"transcript": "make it shorter"})
    assert status == 200 and prev["mode"] == "preview"
    status, edit = routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/edit",
                                 {"text": "edited"})
    assert status == 200 and edit["proposed"] == "edited"
    status, applied = routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/apply",
                                    {"confirm": True})
    assert status == 200 and applied["ok"] is True
    status, reverted = routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/revert", {})
    assert status == 200 and reverted["ok"] is True
    status, cancelled = routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/cancel", {})
    assert status == 200


def test_apply_requires_confirm_route(cfg, svc):
    _, session = routes.handle(cfg, "POST", "/api/voice-edit/session", {})
    rid = session["request_id"]
    routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/capture", {})
    routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/transform",
                  {"transcript": "make it shorter"})
    status, payload = routes.handle(cfg, "POST", f"/api/voice-edit/{rid}/apply", {})
    assert status == 409 and payload["code"] == "VOICE_EDIT_CONFIRM_REQUIRED"


def test_unknown_request_route_404(cfg, svc):
    status, payload = routes.handle(cfg, "POST",
                                    "/api/voice-edit/nope/capture", {})
    assert status == 404 and payload["code"] == "VOICE_EDIT_UNKNOWN_REQUEST"


def test_disabled_route_403(tmp_path, monkeypatch):
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: false\n")
    cfg = FakeCfg({"voice_edit": {"enabled": False}}, p)
    service = VoiceEditService(cfg, desktop=FakeDesktop(),
                               audit=lambda *a: None, estop=lambda cfg: False)
    monkeypatch.setattr(routes, "service_for", lambda c: service)
    status, payload = routes.handle(cfg, "POST", "/api/voice-edit/session", {})
    assert status == 403 and payload["code"] == "VOICE_EDIT_DISABLED"


def test_estop_route_423(tmp_path, monkeypatch):
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")
    cfg = FakeCfg({"voice_edit": {"enabled": True}}, p)
    service = VoiceEditService(cfg, desktop=FakeDesktop(),
                               audit=lambda *a: None, estop=lambda cfg: True)
    monkeypatch.setattr(routes, "service_for", lambda c: service)
    status, payload = routes.handle(cfg, "POST", "/api/voice-edit/session", {})
    assert status == 423 and payload["code"] == "EMERGENCY_STOP"
