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
    model = "qubit-brain"
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


def test_commands_catalog_lists_real_actions(cfg, svc):
    status, payload = routes.handle(cfg, "GET", "/api/voice-edit/commands", {})
    assert status == 200
    assert payload["ok"] is True
    groups = {g["id"]: g for g in payload["groups"]}
    assert {"dictation", "local", "router", "skills"} <= set(groups)
    local_items = {i["name"]: i for i in groups["local"]["items"]}
    assert "open_website" in local_items
    assert local_items["open_website"]["examples"], "every action needs examples"
    assert payload["custom_commands"] == []
    assert set(payload["custom_actions"]) == {
        "open_website", "play_youtube", "open_app", "open_document"}


def test_commands_catalog_derived_from_live_actions(cfg, svc):
    status, payload = routes.handle(cfg, "GET", "/api/voice-edit/commands", {})
    assert status == 200
    from voicebridge.local import actions as _actions
    local_items = {
        i["name"]: i
        for g in payload["groups"] if g["id"] == "local" for i in g["items"]
    }
    # Every registered local action appears with its real desc (never stale).
    for name, act in _actions.ACTIONS.items():
        assert local_items[name]["desc"] == act.desc


def test_custom_commands_reject_junk(cfg, svc):
    for junk in ({"phrase": "", "action": "open_website", "target": "x"},
                 {"phrase": "x" * 81, "action": "open_website", "target": "x"},
                 {"phrase": "open it", "action": "rm_rf", "target": "x"},
                 {"phrase": "open it", "action": "open_website", "target": ""}):
        status, payload = routes.handle(cfg, "PUT", "/api/voice-edit/settings",
                                        {"custom_commands": [junk]})
        assert status == 400, junk
        assert payload["code"] == "VOICE_EDIT_BAD_SETTINGS"


def test_custom_commands_roundtrip(cfg, svc):
    alias = {"phrase": "open my dashboard", "action": "open_website",
             "target": "https://example.com"}
    status, payload = routes.handle(cfg, "PUT", "/api/voice-edit/settings",
                                    {"custom_commands": [alias]})
    assert status == 200
    assert payload["settings"]["custom_commands"] == [alias]
    status, payload = routes.handle(cfg, "GET", "/api/voice-edit/commands", {})
    assert status == 200
    assert payload["custom_commands"] == [alias]


def test_match_alias_first_hit_wins_case_insensitive(cfg):
    from voicebridge.voice_edit.custom_commands import match_alias
    aliases = [{"phrase": "Open My Dashboard", "action": "open_website",
                "target": "https://example.com"}]
    hit = match_alias("hey qubit, open my dashboard please", aliases)
    assert hit is not None and hit["name"] == "open_website"
    assert hit["params"]["url"] == "https://example.com"
    assert match_alias("something unrelated here", aliases) is None


def test_match_alias_param_mapping_per_action():
    from voicebridge.voice_edit.custom_commands import match_alias
    assert match_alias("play bedtime tunes", [
        {"phrase": "bedtime tunes", "action": "play_youtube",
         "target": "lofi hip hop"}])["params"]["query"] == "lofi hip hop"
    assert match_alias("start work mail", [
        {"phrase": "work mail", "action": "open_app",
         "target": "browser"}])["params"]["app"] == "browser"
    assert match_alias("show meeting notes", [
        {"phrase": "meeting notes", "action": "open_document",
         "target": "meeting"}])["params"]["name"] == "meeting"


def test_match_alias_longest_phrase_wins():
    from voicebridge.voice_edit.custom_commands import match_alias
    aliases = [{"phrase": "dashboard", "action": "open_app", "target": "browser"},
               {"phrase": "open my dashboard", "action": "open_website",
                "target": "https://example.com"}]
    hit = match_alias("open my dashboard", aliases)
    assert hit is not None and hit["name"] == "open_website"


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


def test_brain_power_actions_run_without_confirmation(svc, monkeypatch):
    import voicebridge.voice_edit.tool_task as TT
    calls = []

    def _run(cfg, text, **kw):
        calls.append((text, kw))
        return {"state": "OK", "reply": "Brain unloaded."}

    monkeypatch.setattr(TT, "run_tool_task", _run)

    req = svc.begin()
    rid = req["request_id"]
    out = svc.route(rid, "stop brain while I'm playing")
    assert out["mode"] == "answer", out
    assert not out.get("requires_confirmation"), out

    ans = svc.ask("stop brain while I'm playing",
                  request_id=rid, mode="answer")
    assert ans["answer"] == "Brain unloaded.", ans
    assert calls and calls[0][0] == "stop brain while I'm playing", calls
    assert calls[0][1].get("confirmed") is True, calls


def test_brain_warm_runs_without_confirmation(svc, monkeypatch):
    import voicebridge.voice_edit.tool_task as TT
    calls = []

    def _run(cfg, text, **kw):
        calls.append((text, kw))
        return {"state": "OK", "reply": "Warming the brain."}

    monkeypatch.setattr(TT, "run_tool_task", _run)
    req = svc.begin()
    rid = req["request_id"]
    out = svc.route(rid, "wake up the brain")
    assert out["mode"] == "answer", out
    ans = svc.ask("wake up the brain", request_id=rid, mode="answer")
    assert ans["answer"] == "Warming the brain.", ans
    assert calls and calls[0][1].get("confirmed") is True, calls


def test_bare_sleep_unloads_instead_of_entering_sleep_mode(svc, monkeypatch):
    import voicebridge.voice_edit.tool_task as TT
    monkeypatch.setattr(TT, "run_tool_task",
                        lambda cfg, text, **kw: {"state": "OK",
                                                 "reply": "Brain unloaded."})
    req = svc.begin()
    ans = svc.ask("go to sleep", request_id=req["request_id"], mode="answer")
    assert ans["answer"] == "Brain unloaded.", ans
    assert svc.sleeping() is False


def test_site_shortcuts_run_without_confirmation(svc, monkeypatch):
    import voicebridge.voice_edit.tool_task as TT
    calls = []

    def _run(cfg, text, **kw):
        calls.append((text, kw))
        return {"state": "OK", "reply": f"Opened {text} in the browser."}

    monkeypatch.setattr(TT, "run_tool_task", _run)
    for phrase in ("open youtube", "open messenger", "open instagram"):
        req = svc.begin()
        rid = req["request_id"]
        out = svc.route(rid, phrase)
        assert out["mode"] == "answer", (phrase, out)
        assert not out.get("requires_confirmation"), (phrase, out)
        ans = svc.ask(phrase, request_id=rid, mode="answer")
        assert phrase in ans["answer"], (phrase, ans)
    assert len(calls) == 3, calls
    assert all(c[1].get("confirmed") is True for c in calls), calls


def test_window_close_and_empty_trash_stay_confirm_gated(svc):
    for phrase in ("close this window", "empty the trash"):
        req = svc.begin()
        out = svc.route(req["request_id"], phrase)
        assert out["mode"] == "task", (phrase, out)
        assert out.get("requires_confirmation") is True, (phrase, out)


def test_weather_failure_is_fast_and_never_calls_model(svc, monkeypatch):
    monkeypatch.setattr(svc, "_live_weather", lambda q: "")
    called = []
    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role",
                        lambda *a, **k: called.append(True) or None)
    out = svc.ask("what is the weather in Bucharest right now?")
    assert "couldn't reach the weather service" in out["answer"], out
    assert called == [], called


def test_weather_success_short_circuits_model(svc, monkeypatch):
    monkeypatch.setattr(svc, "_live_weather",
                        lambda q: "Bucharest: 10°C, Clear.")
    called = []
    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role",
                        lambda *a, **k: called.append(True) or None)
    out = svc.ask("what is the weather in Bucharest?")
    assert out["answer"] == "Bucharest: 10°C, Clear.", out
    assert called == [], called
