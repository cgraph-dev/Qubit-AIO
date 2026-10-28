"""Decision-26 Slice 1: voice ask() falls back to the full agent turn.

The limited voice track (tool-less run_role brain) is no longer the
ceiling: when the deterministic stages (reply drafts, live facts, skill
answers) yield nothing AND the local brain fails or refuses, ask()
dispatches through chat_transport.turn — the same persisted agent turn
/qubit-chat uses, with branches (web/local/registry/agent_task) and the
voice-local model. Anything that works today behaves byte-identically;
only today's failures get a second chance. Staged confirmations and
turn errors still fall back to the brain (today's behavior) — settling
a CT-staged confirmation through voice "yes" is Slice-1b, not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.chat_transport as CT  # noqa: E402
from voicebridge.providers import gateway as GW  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


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
    """Fake canonical store: counts persisted rows (double-write guard)."""

    def __init__(self):
        self.rows = []


def _cfg(**ve):
    base = {"enabled": True, "routing": "local",
            "confirm_before_replace": False}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


def _service(monkeypatch, *, brain=None):
    audits = []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(
        _cfg(), desktop=FakeDesktop(), engine=None, router=None,
        audit=audit, estop=lambda cfg: False)
    store = CountingStore()
    svc._session_store = store

    class R:
        ok = True
        text = (brain or {}).get("text", "")
        code = (brain or {}).get("code", "")
        provider = "local"
        model = "qubit-brain"

        def __init__(self):
            self.ok = (brain or {}).get("ok", True)

    def fake_run_role(prompt, cfg, **kw):
        return R()

    monkeypatch.setattr(GW, "run_role", fake_run_role)
    monkeypatch.setattr(svc, "_known_model_profile", lambda pid: True)
    return svc, audits, store


def test_brain_answer_never_touches_full_turn(monkeypatch):
    """A working brain answer behaves exactly as today: CT.turn uncalled."""
    svc, audits, store = _service(
        monkeypatch, brain={"ok": True, "text": "Why did the router cross the road."})

    def _boom(*a, **k):
        raise AssertionError("CT.turn must not run when the brain answers")

    monkeypatch.setattr(CT, "turn", _boom)
    out = svc.ask("tell me a joke about routers", request_id="r-1")
    assert out["ok"] is True
    assert "router cross the road" in out["answer"]


def test_brain_failure_gets_full_turn_second_chance(monkeypatch):
    """Brain fails -> the same persisted turn /qubit-chat uses answers."""
    svc, audits, store = _service(
        monkeypatch, brain={"ok": False, "code": "MALFORMED_RESPONSE"})
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen.update(kw)
        seen["text"] = text
        return {"ok": True, "reply": "Did the thing via the full turn.",
                "session_id": "voice-chat"}

    monkeypatch.setattr(CT, "turn", fake_turn)
    # NOTE (Lane 3, 2026-10-03): this used "create and open a txt document",
    # which is now a deterministic local action (stages before the brain by
    # design). The fallback contract needs free-form text no tool claims.
    out = svc.ask("do the thing", request_id="r-2")
    assert out["ok"] is True
    assert "Did the thing" in out["answer"]
    assert seen.get("session") == "voice-chat"
    assert store.rows == []  # the turn persists itself; never double-write


def test_brain_cant_do_refusal_escalates_to_full_turn(monkeypatch):
    """A polite-but-useless brain refusal is a failure, not an answer."""
    svc, audits, store = _service(
        monkeypatch, brain={"ok": True,
                            "text": "I'm sorry, I can't do that."})
    called = []

    def fake_turn(cfg, text, **kw):
        called.append(text)
        return {"ok": True, "reply": "Done via the full turn."}

    monkeypatch.setattr(CT, "turn", fake_turn)
    # NOTE (Lane 3, 2026-10-03): was "create and open a txt document" (now a
    # deterministic local action); free-form text keeps the tested contract.
    out = svc.ask("do the thing", request_id="r-3")
    assert out["ok"] is True
    assert out["answer"] == "Done via the full turn."
    assert called == ["do the thing"]


def test_staged_confirmation_falls_back_to_brain(monkeypatch):
    """A CT-staged confirmation has no voice settle path yet: keep the
    brain answer (today's behavior) rather than a promise that "yes"
    cannot settle."""
    svc, audits, store = _service(
        monkeypatch, brain={"ok": True, "text": "Brain fallback answer."})

    def fake_turn(cfg, text, **kw):
        return {"ok": False, "state": "UNAVAILABLE",
                "code": "CONFIRM_REQUIRED", "confirmation_id": "c-1",
                "detail": "needs confirmation"}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("do the thing", request_id="r-4")
    assert out["ok"] is True
    assert out["answer"] == "Brain fallback answer."


def test_full_turn_error_keeps_brain_outcome(monkeypatch):
    svc, audits, store = _service(
        monkeypatch, brain={"ok": False, "code": "MALFORMED_RESPONSE"})

    def fake_turn(cfg, text, **kw):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("do the thing", request_id="r-5")
    assert out["ok"] is False
    assert any(op == "voice_edit.full_turn_error" for op, _ in audits)


# --- Slice-1b: voice "yes" settles CT-staged confirmations via task() ---


def test_slice1b_pending_registry_settles_on_yes(monkeypatch):
    """Pending CT registry confirmation + task(confirm=True) settles it.

    run_tool_task must never run (it knows nothing of the CT-staged id).
    """
    svc, audits, store = _service(
        monkeypatch, brain={"ok": True, "text": "Brain fallback answer."})
    monkeypatch.setattr(CT, "_pending_registry",
                        lambda cfg, sid: [{"id": "c-1"}])
    monkeypatch.setattr(CT, "_pending_task", lambda cfg, sid: [])

    def fake_settle(cfg, st, sid):
        assert sid == svc._voice_session()
        return {"ok": True, "text": "Opened youtube", "reply": "Opened youtube",
                "session_id": sid, "route": "registry", "entry": "open_website"}

    monkeypatch.setattr(CT, "_settle_affirmed", fake_settle)

    import voicebridge.voice_edit.tool_task as TT

    def _boom(*a, **k):
        raise AssertionError("run_tool_task must not run when CT settles")

    monkeypatch.setattr(TT, "run_tool_task", _boom)
    out = svc.task("yes", confirm=True, request_id="r-s1b1")
    assert out["ok"] is True
    assert out["mode"] == "task_result"
    assert "Opened youtube" in out["reply"]
    assert out["tool_used"] == "full-turn"
    assert any(op == "voice_edit.task.settled" for op, _ in audits)


def test_slice1b_no_pending_keeps_tool_task_path(monkeypatch):
    """No CT pending -> today's run_tool_task behavior, byte-identical shape."""
    svc, audits, store = _service(
        monkeypatch, brain={"ok": True, "text": "Brain fallback answer."})
    monkeypatch.setattr(CT, "_pending_registry", lambda cfg, sid: [])
    monkeypatch.setattr(CT, "_pending_task", lambda cfg, sid: [])

    import voicebridge.voice_edit.tool_task as TT

    def fake_tool(cfg, clean, **kw):
        return {"state": "OK", "reply": "did the thing",
                "tool_used": "local.open_website", "session_id": "voice-chat"}

    monkeypatch.setattr(TT, "run_tool_task", fake_tool)
    out = svc.task("do the thing", confirm=True, request_id="r-s1b2")
    assert out["ok"] is True
    assert out["mode"] == "task_result"
    assert out["reply"] == "did the thing"
    assert out["tool_used"] == "local.open_website"
    assert not any(op == "voice_edit.task.settled" for op, _ in audits)


def test_slice1b_pending_with_confirm_false_still_refuses(monkeypatch):
    """Pending CT confirmation + confirm=False refuses, settles nothing."""
    svc, audits, store = _service(
        monkeypatch, brain={"ok": True, "text": "Brain fallback answer."})
    monkeypatch.setattr(CT, "_pending_registry",
                        lambda cfg, sid: [{"id": "c-1"}])

    def _boom(cfg, st, sid):
        raise AssertionError("settle must not run without confirm=True")

    monkeypatch.setattr(CT, "_settle_affirmed", _boom)
    monkeypatch.setattr(CT, "_settle_task_affirmed", _boom)
    with pytest.raises(Exception) as e:
        svc.task("yes", confirm=False, request_id="r-s1b3")
    assert e.value.code == "VOICE_EDIT_CONFIRM_REQUIRED"
