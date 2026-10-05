"""Lane-3 talk-back net (9u): tool-shaped misses must stage, never narrate.

Journal case 2026-10-03 04:53 UTC (07:53 local): "Set the timer for one
minute." routed chat/intent:conversation -> ask() -> brain answered
"Set a 1-minute timer. Confirm?" (no task.proposed, no re-arm) -> the
owner's "Yes." arrived unarmed -> wake.discarded. The core yes-dies shape.

Fix contract (service.py + desktop_helper.py only):
* route()/ask() consult deterministic propose_tool for answer/chat text;
  a local.* hit stages via the task shape (helper stages + speaks + re-arms
  through the existing _propose_task path). web.* never stages (weather and
  the direct-fact/skill stages keep priority).
* Reply-shaped text is never stolen by the net (parse_reply guard).
* "what did I miss" triggers the on-request away digest (sleep never gates).
* Contraction time/date questions ("What's the time/date ...") answer from
  the clock instead of the confabulating brain.
* Helper _answer_question settles task-shaped ask results via _propose_task.
* No desktop capture/insertion on any touched path (wake no-Ctrl+C invariant).

No services, no browser, no model, no live config writes. The brain is
faked to explode if consulted on tool-shaped text.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from voicebridge.voice_edit.service import VoiceEditService


class FakeCfg:
    def __init__(self, data=None, path=None):
        self._data = data or {"voice_edit": {"enabled": True}}
        self.data = self._data
        self.path = path

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class BoomDesktop:
    """Any capture/insertion attempt explodes (wake no-Ctrl+C invariant)."""

    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def __getattr__(self, name):
        raise AssertionError(f"desktop.{name} must not run on talk-back paths")


class FakeStore:
    def __init__(self):
        self.turns = []

    def messages(self, sid, limit=200):
        return {"messages": []}


def _svc(monkeypatch, **kw):
    audits = []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(
        FakeCfg(kw.get("cfg_data")),
        desktop=BoomDesktop(),
        audit=audit,
        estop=lambda cfg: False,
    )
    svc._session_store = FakeStore()
    return svc, audits


def _begin(svc, rid="r1"):
    req = svc.begin(session_id="s", request_id=rid, target_app="tk")
    return req["request_id"] if isinstance(req, dict) else rid


# -- route() net ------------------------------------------------------------

def test_route_timer_stages_task_not_chat(monkeypatch):
    svc, audits = _svc(monkeypatch)
    rid = _begin(svc)
    out = svc.route(rid, "Set the timer for one minute.")
    assert out.get("mode") == "task", out
    assert out.get("requires_confirmation") is True
    assert "timer" in (out.get("task_text") or "").lower()
    assert any(op == "voice_edit.task.proposed" for op, _ in audits)


def test_route_timer_with_the_article_stages(monkeypatch):
    """The exact journal shape ("Set THE timer ...") must stage."""
    svc, audits = _svc(monkeypatch)
    rid = _begin(svc)
    out = svc.route(rid, "Set THE timer for one minute.")
    assert out.get("mode") == "task", out
    assert out.get("requires_confirmation") is True


def test_route_briefing_not_stolen_by_net(monkeypatch):
    svc, _ = _svc(monkeypatch)
    rid = _begin(svc)
    out = svc.route(rid, "Brief me.")
    assert out.get("mode") != "task", out


def test_route_reply_shaped_text_not_stolen_by_net(monkeypatch):
    svc, _ = _svc(monkeypatch)
    rid = _begin(svc)
    out = svc.route(rid, "Reply to mom on messenger saying hello there")
    assert out.get("mode") != "task", out


# -- ask() net (direct /ask callers; brain must never see tool text) --------

def _boom_brain(monkeypatch):
    from voicebridge.providers import gateway as GW

    def _boom(*a, **k):
        raise AssertionError("brain consulted on tool-shaped text")

    monkeypatch.setattr(GW, "run_role", _boom)


def test_ask_timer_stages_without_brain(monkeypatch):
    _boom_brain(monkeypatch)
    svc, audits = _svc(monkeypatch)
    rid = _begin(svc)
    out = svc.ask("Set the timer for one minute.", request_id=rid)
    assert out.get("ok") is True
    assert out.get("mode") == "task", out
    assert out.get("requires_confirmation") is True
    assert "timer" in (out.get("task_text") or "").lower()
    assert any(op == "voice_edit.task.proposed" for op, _ in audits)


def test_ask_web_shaped_text_never_stages(monkeypatch):
    """Deterministic non-tool answers keep priority over the net."""
    svc, _ = _svc(monkeypatch)
    svc._live_weather = lambda q: "Bucharest: 9C, Clear."
    rid = _begin(svc)
    out = svc.ask("What is the weather in Bucharest right now?",
                  request_id=rid)
    assert out.get("mode") != "task", out
    assert "Bucharest" in (out.get("answer") or "")


def test_ask_what_did_i_miss_returns_digest(monkeypatch):
    svc, audits = _svc(monkeypatch)
    svc._briefing_unread = lambda: ""
    svc._recent_completions = lambda limit=2: []
    svc._briefing_cron = lambda: "1 scheduled job on the list today."
    rid = _begin(svc)
    out = svc.ask("Hey qubit, what did I miss?", request_id=rid)
    assert out.get("ok") is True
    assert out.get("mode") == "away-digest", out
    assert "scheduled job" in (out.get("answer") or "")


def test_digest_trigger_detection():
    svc_like = VoiceEditService.__new__(VoiceEditService)
    assert VoiceEditService.is_digest_request("what did I miss?") is True
    assert VoiceEditService.is_digest_request(
        "hey qubit, what did I miss while I was away") is True
    assert VoiceEditService.is_digest_request("catch me up") is True
    assert VoiceEditService.is_digest_request("what's the weather?") is False
    assert VoiceEditService.is_digest_request("I miss my old phone") is False


# -- contraction clock facts (journal: wrong time + wrong date spoken) ------

def test_direct_fact_contraction_time(monkeypatch):
    svc, _ = _svc(monkeypatch)
    ans = svc._direct_fact_answer("What's the time in Bucharest right now?")
    assert ans.startswith("It is "), ans


def test_direct_fact_contraction_date(monkeypatch):
    svc, _ = _svc(monkeypatch)
    ans = svc._direct_fact_answer("What's the date now in Romania?")
    assert "2026" in ans, ans
    assert "April" not in ans, ans


def test_direct_fact_does_not_steal_identity(monkeypatch):
    svc, _ = _svc(monkeypatch)
    assert svc._direct_fact_answer("What's your name?") == ""
    assert svc._direct_fact_answer("What's your favorite color?") == ""


# -- sleep discipline preserved ----------------------------------------------

def test_unprompted_digest_suppressed_while_sleeping(monkeypatch):
    """Unprompted delivery stays silent in sleep (no unprompted callers exist
    yet — the gate is verified here for when one arrives)."""
    svc, _ = _svc(monkeypatch)
    rid = _begin(svc)
    svc.ask("I'm going to sleep.", request_id=rid)
    assert svc.sleeping() is True
    out = svc.away_digest(unprompted=True, request_id=rid)
    assert out.get("suppressed") is True
    assert out.get("answer") == ""


def test_sleep_command_still_sets_mode(monkeypatch):
    svc, _ = _svc(monkeypatch)
    rid = _begin(svc)
    out = svc.ask("I'm going to sleep.", request_id=rid)
    assert out.get("mode") == "sleep", out
    assert svc.sleeping() is True


def test_tool_stage_works_while_sleeping(monkeypatch):
    """On-request turns are never sleep-gated (owner decision S5 #2)."""
    _boom_brain(monkeypatch)
    svc, _ = _svc(monkeypatch)
    rid = _begin(svc)
    svc.ask("I'm going to sleep.", request_id=rid)
    assert svc.sleeping() is True
    out = svc.ask("Set the timer for one minute.", request_id=rid)
    assert out.get("mode") == "task", out
    assert out.get("requires_confirmation") is True


# -- helper settles task-shaped ask results -----------------------------------

def test_helper_answer_question_stages_task_shape(monkeypatch):
    from voicebridge import desktop_helper as DH

    cfg = FakeCfg()

    staged = {}

    class FakeWake:
        def __init__(self):
            self.rearms = 0

        def expect_reply(self, timeout_s=None):
            self.rearms += 1

    class FakeClient:
        def ask_result(self, text, request_id=""):
            return {"ok": True, "mode": "task",
                    "requires_confirmation": True,
                    "task_text": "Set the timer for one minute.",
                    "request_id": request_id}

        def speak(self, text):
            return b"wav"

    helper = DH.DesktopHelper.__new__(DH.DesktopHelper)
    helper.cfg = cfg
    helper.client = FakeClient()
    helper.request_id = "r1"
    helper.snapshot = None
    helper.state = DH.DesktopHelper.IDLE
    helper._preview = None
    helper._pending_task = {}
    helper._wake = FakeWake()
    helper._transcript = ""
    seen = {}

    def _boom_ui(*a, **k):
        raise AssertionError("error path must not run on staged tasks")

    helper._ui_error = _boom_ui
    helper._finish_error = lambda: seen.setdefault("finished", True)
    helper._ui_status = lambda *a, **k: None
    helper._ui_preview = lambda *a, **k: None
    helper._notify_status = lambda *a, **k: None
    helper._safe_show = lambda: None
    helper._history = lambda *a, **k: None
    orig_speak = DH.DesktopHelper._speak
    helper._speak = lambda text: seen.setdefault("spoken", text)

    helper._answer_question("Set the timer for one minute.", mode="answer")

    assert helper._pending_task.get("text") == "Set the timer for one minute."
    assert helper._wake.rearms == 1, "staged task must re-arm the wake window"
    assert "spoken" in seen, "staged task must speak the confirm prompt"
    assert "finished" not in seen
