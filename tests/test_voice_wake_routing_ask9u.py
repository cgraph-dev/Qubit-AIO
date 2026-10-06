"""Wake routing reaches the ask/task turn seams.

A wake command routed to a session (``route_target.sessionKey``) is bound on
the request at ``begin(session_id=...)`` by ``desktop_helper._open_request``.
``VoiceEditService.ask`` and ``.task`` must run their *dispatch* turn on that
session instead of the default ``voice-chat``; the deterministic/brain fast
paths stay on the voice session, and an empty/absent/malformed session id is
byte-identical to today.

Hermetic: CT.turn and the tool-task dispatch are stubbed to capture the
session argument; no model, socket, mic, or desktop.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.chat_transport as CT  # noqa: E402
import voicebridge.voice_edit.tool_task as TT  # noqa: E402
from voicebridge.providers import gateway as GW  # noqa: E402
from voicebridge.voice_edit.service import (  # noqa: E402
    VoiceEditService, _Request)

_ROUTED = "agent:main:routed-9u"


class _Boom:
    """A session id whose string form raises (malformed-id probe)."""

    def __str__(self):  # noqa: D105
        raise RuntimeError("boom")

    def __repr__(self):  # noqa: D105
        raise RuntimeError("boom")


class FakeCfg:
    def __init__(self, data=None, path=None):
        self._data = data or {}
        self.path = path

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}


class CountingStore:
    """Fake canonical store (the settle gate only checks it is non-None)."""

    def __init__(self):
        self.rows = []


def _cfg(**ve):
    base = {"enabled": True, "routing": "local",
            "confirm_before_replace": False}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


def _service(monkeypatch, *, brain=None):
    audits = []
    svc = VoiceEditService(
        _cfg(), desktop=FakeDesktop(), engine=None, router=None,
        audit=lambda op, actor, detail: audits.append((op, detail)),
        estop=lambda cfg: False)
    svc._session_store = CountingStore()

    class R:
        ok = (brain or {}).get("ok", True)
        text = (brain or {}).get("text", "")
        code = (brain or {}).get("code", "")
        message = ""
        provider = "local"
        model = "qubit-brain4"

    monkeypatch.setattr(GW, "run_role", lambda *a, **k: R())
    monkeypatch.setattr(svc, "_known_model_profile", lambda pid: True)
    return svc, audits


def _patch_no_ct_pending(monkeypatch, seen=None):
    def _reg(cfg, sid):
        if seen is not None:
            seen["settle_sid"] = sid
        return []

    monkeypatch.setattr(CT, "_pending_registry", _reg)
    monkeypatch.setattr(CT, "_pending_task", lambda cfg, sid: [])


# --- ask(): full-turn fallback routes to the bound session -----------------

def test_ask_full_turn_uses_routed_session(monkeypatch):
    svc, audits = _service(
        monkeypatch, brain={"ok": False, "code": "MALFORMED_RESPONSE"})
    rid = svc.begin(session_id=_ROUTED)["request_id"]
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen.update(kw)
        return {"ok": True, "reply": "Done on the routed session."}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("do the thing", request_id=rid)
    assert out["ok"] is True
    assert seen.get("session") == _ROUTED
    assert any(
        op == "voice_edit.full_turn_session"
        and detail.get("session_id") == _ROUTED
        and detail.get("request_id") == rid
        for op, detail in audits)


def test_ask_defaults_to_voice_session_when_unset(monkeypatch):
    svc, audits = _service(
        monkeypatch, brain={"ok": False, "code": "MALFORMED_RESPONSE"})
    # begin() without a routed session -> today's default, byte-identical.
    rid = svc.begin()["request_id"]
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen.update(kw)
        return {"ok": True, "reply": "Default session answer."}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("do the thing", request_id=rid)
    assert out["ok"] is True
    assert seen.get("session") == "voice-chat"
    # No routed session -> no routing audit.
    assert not any(op == "voice_edit.full_turn_session" for op, _ in audits)


def test_ask_without_request_keeps_default(monkeypatch):
    """A direct /ask with no request id behaves exactly as today."""
    svc, _ = _service(
        monkeypatch, brain={"ok": False, "code": "MALFORMED_RESPONSE"})
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen.update(kw)
        return {"ok": True, "reply": "No-request answer."}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("do the thing")
    assert out["ok"] is True
    assert seen.get("session") == "voice-chat"


# --- task(): tool dispatch routes to the bound session ---------------------

def test_task_dispatch_uses_routed_session(monkeypatch):
    svc, audits = _service(monkeypatch)
    rid = svc.begin(session_id=_ROUTED)["request_id"]
    seen = {}
    _patch_no_ct_pending(monkeypatch, seen)

    def fake_tool(cfg, clean, **kw):
        seen["session"] = kw.get("session")
        return {"state": "OK", "reply": "did the thing",
                "tool_used": "local.open_website",
                "session_id": _ROUTED}

    monkeypatch.setattr(TT, "run_tool_task", fake_tool)
    out = svc.task("do the thing", confirm=True, request_id=rid)
    assert out["ok"] is True
    assert seen.get("session") == _ROUTED
    # The CT-staged settle is scoped to the same routed session.
    assert seen.get("settle_sid") == _ROUTED
    assert any(
        op == "voice_edit.task_session"
        and detail.get("session_id") == _ROUTED
        for op, detail in audits)


def test_task_dispatch_default_when_unset(monkeypatch):
    svc, audits = _service(monkeypatch)
    rid = svc.begin()["request_id"]
    seen = {}
    _patch_no_ct_pending(monkeypatch, seen)

    def fake_tool(cfg, clean, **kw):
        seen["session"] = kw.get("session")
        return {"state": "OK", "reply": "did the thing",
                "tool_used": "local.open_website", "session_id": "voice-chat"}

    monkeypatch.setattr(TT, "run_tool_task", fake_tool)
    out = svc.task("do the thing", confirm=True, request_id=rid)
    assert out["ok"] is True
    # Voice turns land in the dedicated voice session (voice-chat); a routed
    # wake target still wins (tested separately).
    assert seen.get("session") == "voice-chat"
    assert not any(op == "voice_edit.task_session" for op, _ in audits)


# --- malformed session ids never raise -------------------------------------

def test_malformed_session_id_never_raises_ask(monkeypatch):
    svc, _ = _service(
        monkeypatch, brain={"ok": False, "code": "MALFORMED_RESPONSE"})
    svc._reqs["bad"] = _Request("bad", _Boom())
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen.update(kw)
        return {"ok": True, "reply": "Fallback answer."}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("do the thing", request_id="bad")
    assert out["ok"] is True
    assert seen.get("session") == "voice-chat"


def test_malformed_session_id_never_raises_task(monkeypatch):
    svc, _ = _service(monkeypatch)
    svc._reqs["bad"] = _Request("bad", _Boom())
    seen = {}
    _patch_no_ct_pending(monkeypatch, seen)

    def fake_tool(cfg, clean, **kw):
        seen["session"] = kw.get("session")
        return {"state": "OK", "reply": "did the thing",
                "tool_used": "local.open_website", "session_id": ""}

    monkeypatch.setattr(TT, "run_tool_task", fake_tool)
    out = svc.task("do the thing", confirm=True, request_id="bad")
    assert out["ok"] is True
    assert seen.get("session") == "voice-chat"
