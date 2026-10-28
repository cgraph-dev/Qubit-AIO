"""Slice 1 — voice ask()/route() route-word bypass (9u).

ask("in chat: ...") skips the deterministic stages (reply drafts, live
facts, skills) and dispatches straight to the full agent turn; ask("quick:
...") keeps today's direct behavior with the worker fallback disabled.
service.route() maps route-word utterances to answer mode so they reach
ask() instead of the voice-level task confirm staging. All fakes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.router import RouteDecision
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import TransformEngine


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node

    def state_dir(self):
        import tempfile
        return tempfile.gettempdir()


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return False


def _cfg(**ve):
    base = {"enabled": True, "routing": "local", "local_profile": "qubit-brain"}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


def _service(**kw):
    audits = []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(
        kw.pop("cfg", None) or _cfg(), desktop=FakeDesktop(),
        engine=TransformEngine(runner=lambda *a, **k: None),
        audit=audit, estop=lambda cfg: False, **kw)
    return svc, audits


def test_route_maps_in_chat_to_answer_mode_without_router():
    svc, _ = _service(router=None)
    # A build-flavoured utterance would route "task" (confirm staging);
    # the route word must map it to answer mode so ask() dispatches the
    # headless /chat worker directly. No request needed: route() only
    # needs the bypass before the router.
    svc._reqs["r1"] = type("R", (), {})()
    import types
    from voicebridge.voice_edit import service as SVC

    # Bypass needs a request row; reuse the real _Request shape minimally.
    req = SVC._Request("r1", "")
    svc._reqs["r1"] = req
    out = svc.route("r1", "in chat: build me an expense app")
    assert out["ok"] is True, out
    assert out["mode"] == "answer", out
    assert "in chat" in str(out.get("question", "")).lower(), out


def test_route_maps_quick_to_answer_mode():
    svc, _ = _service(router=None)
    from voicebridge.voice_edit import service as SVC
    svc._reqs["r1"] = SVC._Request("r1", "")
    out = svc.route("r1", "quick: build me an expense app")
    assert out["ok"] is True, out
    assert out["mode"] == "answer", out


def test_route_unprefixed_still_uses_router():
    def boom(transcript, *, has_selection):
        raise AssertionError("router must be consulted for unprefixed text")

    svc, _ = _service(router=type("R", (), {"decide": staticmethod(boom)})())
    from voicebridge.voice_edit import service as SVC
    svc._reqs["r1"] = SVC._Request("r1", "")
    with pytest.raises(AssertionError):
        svc.route("r1", "build me an expense app")


def test_ask_in_chat_skips_deterministic_stages(monkeypatch):
    svc, audits = _service()
    calls = []

    def fail_stage(question):
        calls.append("stage")
        return ("should-never-answer", "skill-x")

    monkeypatch.setattr(svc, "_skill_answer", fail_stage)
    monkeypatch.setattr(svc, "_direct_fact_answer",
                        lambda q: calls.append("fact") or "FACT")
    monkeypatch.setattr(svc, "_reply_draft", lambda q: calls.append("draft"))
    monkeypatch.setattr(
        svc, "_full_turn_answer",
        lambda q, **k: {"ok": True, "answer": "WORKER_DID_IT",
                        "mode": k.get("mode", "answer"),
                        "provider": "local", "model": "full-turn",
                        "spoken": False})
    out = svc.ask("in chat: what is the weather in Bucharest")
    assert out["ok"] is True, out
    assert out["answer"] == "WORKER_DID_IT", out
    assert calls == [], calls  # no deterministic stage ran


def test_ask_in_chat_strips_prefix_before_dispatch(monkeypatch):
    svc, _ = _service()
    seen = {}
    monkeypatch.setattr(svc, "_reply_draft", lambda q: None)
    monkeypatch.setattr(svc, "_skill_answer", lambda q: ("", ""))
    monkeypatch.setattr(svc, "_direct_fact_answer", lambda q: "")

    def capture(question, **kw):
        seen["q"] = question
        return None  # worker cannot answer -> fall through to brain

    monkeypatch.setattr(svc, "_full_turn_answer", capture)
    monkeypatch.setattr(svc, "_escalate_for", lambda mode: (False, "t"))
    monkeypatch.setattr(svc, "_local_profile", lambda: "qubit-brain")

    class Res:
        ok = True
        text = "BRAIN_OK"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role", lambda *a, **k: Res())
    monkeypatch.setattr(svc, "_remember_ask_exchange", lambda *a, **k: None)
    out = svc.ask("in chat:  do the thing")
    assert out["ok"] is True and out["answer"] == "BRAIN_OK", out
    assert seen["q"] == "do the thing", seen


def test_ask_quick_disables_worker_fallback(monkeypatch):
    svc, _ = _service()
    monkeypatch.setattr(svc, "_reply_draft", lambda q: None)
    monkeypatch.setattr(svc, "_skill_answer", lambda q: ("", ""))
    monkeypatch.setattr(svc, "_direct_fact_answer", lambda q: "")

    def fail_full(question, **kw):
        raise AssertionError("quick: must never dispatch the worker")

    monkeypatch.setattr(svc, "_full_turn_answer", fail_full)
    monkeypatch.setattr(svc, "_escalate_for", lambda mode: (False, "t"))
    monkeypatch.setattr(svc, "_local_profile", lambda: "qubit-brain")

    class Res:
        ok = False
        text = ""
        provider = "local"
        model = "qubit-brain"
        code = "BRAIN_EMPTY"
        message = "empty"

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role", lambda *a, **k: Res())
    out = svc.ask("quick: tell me something")
    assert out["ok"] is False, out
    assert out["code"] == "BRAIN_EMPTY", out


def test_ask_quick_keeps_direct_stages(monkeypatch):
    svc, _ = _service()
    monkeypatch.setattr(svc, "_reply_draft", lambda q: None)
    out = svc.ask("quick: what time is it")
    assert out["ok"] is True, out
    assert out["model"] == "live-facts", out
