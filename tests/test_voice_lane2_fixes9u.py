"""Lane 2 (2026-10-03): absurd worker tools + narration + the 2000.

Journal proof:
- test-4 09:54:51/53 (events.jsonl): "In chat createvoicetest.txt in
  documents with Hello Voice." -> intent coding -> worker ran
  ``local.open_app {app: notepad.exe}`` (a Windows binary on a Linux box),
  steps=1, result_len 987, no file; full-turn answer was brain narration
  ("Qubit is available to assist with tasks. Let me know how I can help.").
- test-2 09:52:38: "Redo this text for me, the meeting is at 3pm, bring the
  report." -> intent:coding -> task.run executed ``web.search`` on the whole
  sentence (nonsense tool), FAILED with an empty reply.

Fixes pinned here (TDD red-first):
(a) transform-shaped utterances reach the TRANSFORM path, never task.run;
(b) create-file tasks never resolve to open_app/notepad.exe;
(c) narration-shaped worker results become a TYPED failure, never spoken
    as an ok answer;
(d) the silent ``prompt[:2000]`` truncation on the local gateway path is
    gone (streaming never truncated).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.chat_transport as CT  # noqa: E402
from voicebridge.providers import gateway as GW  # noqa: E402
from voicebridge.voice_edit.schema import TransformResult  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


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


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}


class FakeStore:
    def __init__(self):
        self.rows = []

    def messages(self, session_id, limit=200):
        return {"messages": []}


class FakeEngine:
    """Deterministic transform engine stub (no model)."""

    def __init__(self, text="REWRITTEN"):
        self.calls = []
        self.text = text

    def transform(self, cfg, cmd, selected_text="", **kw):
        self.calls.append((cmd.action, selected_text))
        return TransformResult(ok=True, text=self.text,
                               action=getattr(cmd, "action", ""),
                               model="test-engine", provider="local",
                               locality="local", privacy_mode="local")


def _cfg(**ve):
    base = {"enabled": True, "routing": "local",
            "confirm_before_replace": False}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


def _service(monkeypatch, engine=None):
    audits = []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(
        _cfg(), desktop=FakeDesktop(), engine=engine or FakeEngine(),
        router=None, audit=audit, estop=lambda cfg: False)
    svc._session_store = FakeStore()
    monkeypatch.setattr(svc, "_known_model_profile", lambda pid: True)
    return svc, audits


def _boom_turn(*a, **k):
    raise AssertionError("CT.turn (headless worker) must not run "
                         "for a transform-shaped utterance")


def _boom_tool(*a, **k):
    raise AssertionError("run_tool_task must not run "
                         "for a transform-shaped utterance")


def _boom_brain(prompt, cfg, **kw):
    raise AssertionError("brain run_role must not run "
                         "for a transform-shaped utterance")


# -- (a) transform-shaped utterances reach TRANSFORM, never task.run --------

REDO_Q = "Redo this text for me, the meeting is at 3pm, bring the report."


def test_redo_with_trailing_text_transforms_without_worker(monkeypatch):
    """The journal test-2 sentence: rewrite with its own trailing text."""
    svc, audits = _service(monkeypatch)
    monkeypatch.setattr(CT, "turn", _boom_turn)
    monkeypatch.setattr(GW, "run_role", _boom_brain)
    import voicebridge.voice_edit.tool_task as TT
    monkeypatch.setattr(TT, "run_tool_task", _boom_tool)
    out = svc.ask(REDO_Q, request_id="lane2-redo")
    assert out["ok"] is True
    assert "REWRITTEN" in out["answer"]
    assert out["model"] == "transform"
    # the trailing text became the transform input, not a web query
    assert svc.engine.calls and svc.engine.calls[0][1] != ""
    assert "meeting" in svc.engine.calls[0][1]


@pytest.mark.parametrize("phrase", [
    "rewrite this to sound professional",
    "make this shorter",
    "fix the grammar in this sentence",
    "summarize this for me",
    "redo these packs for me",  # loose ASR-mishear shape stays rewrite
])
def test_each_transform_shape_routes_to_transform(monkeypatch, phrase):
    svc, audits = _service(monkeypatch)
    monkeypatch.setattr(CT, "turn", _boom_turn)
    monkeypatch.setattr(GW, "run_role", _boom_brain)
    import voicebridge.voice_edit.tool_task as TT
    monkeypatch.setattr(TT, "run_tool_task", _boom_tool)
    out = svc.ask(phrase, request_id="lane2-shape")
    assert out["ok"] is True, phrase
    assert "REWRITTEN" in out["answer"], phrase
    assert svc.engine.calls, phrase


def test_coding_task_unaffected_by_pre_router(monkeypatch):
    """A genuine coding task is NOT transform-shaped: brain path intact."""
    svc, audits = _service(monkeypatch)
    assert svc._transform_pre_route(
        "write a python function to sort a list") is None

    class R:
        ok = True
        text = "Here is a sort function."
        code = ""
        provider = "local"
        model = "qubit-brain4"

    monkeypatch.setattr(GW, "run_role", lambda *a, **k: R())
    out = svc.ask("write a python function to sort a list",
                  request_id="lane2-code")
    assert out["ok"] is True
    assert "sort function" in out["answer"]
    assert svc.engine.calls == []


def test_route_steers_redo_away_from_task(monkeypatch):
    """route() with no selection must not classify redo as a task."""
    svc, audits = _service(monkeypatch)
    rid = svc.begin()["request_id"]
    out = svc.route(rid, REDO_Q)
    assert out["mode"] != "task", out
    assert out["mode"] in ("answer", "chat", "transform"), out


# -- (b) worker tool bias: no open_app/notepad.exe for file tasks -----------

def test_planner_rejects_notepad_exe():
    from voicebridge.planner import _validate
    out = _validate("local.open_app", {"app": "notepad.exe"}, 0.99,
                    "create VoiceTest.txt in documents with Hello Voice.")
    assert out is None


def test_planner_rejects_open_app_for_create_file():
    from voicebridge.planner import _validate
    out = _validate("local.open_app", {"app": "chromium"}, 0.99,
                    "create a text document called VoiceTest")
    assert out is None


def test_planner_keeps_legit_open_app():
    from voicebridge.planner import _validate
    out = _validate("local.open_app", {"app": "calculator"}, 0.99,
                    "open the calculator please")
    assert out is not None
    assert out.tool == "local.open_app"


# -- (c) T5 guard: narration-shaped worker results are typed failures -------

NARRATION = ("Qubit is available to assist with tasks. "
             "Let me know how I can help.")


def test_full_turn_narration_is_typed_failure(monkeypatch):
    svc, audits = _service(monkeypatch)

    def fake_turn(cfg, text, **kw):
        return {"ok": True, "reply": NARRATION,
                "session_id": "voice-chat", "route": "task",
                "tool_used": "local.open_app", "steps": 1}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc._full_turn_answer("create VoiceTest.txt in documents",
                                request_id="lane2-narr")
    assert out is not None
    assert out.get("ok") is not True
    assert out.get("code") == "VOICE_EDIT_WORKER_NARRATION"
    spoken = str(out.get("answer", "") or "")
    assert "available to assist" not in spoken
    assert "Let me know how I can help" not in spoken


def test_full_turn_real_report_passes(monkeypatch):
    svc, audits = _service(monkeypatch)

    def fake_turn(cfg, text, **kw):
        return {"ok": True,
                "reply": "Created VoiceTest.txt and opened it in the editor.",
                "session_id": "voice-chat", "route": "task"}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc._full_turn_answer("create VoiceTest.txt in documents",
                                request_id="lane2-real")
    assert out is not None and out.get("ok") is True
    assert "Created VoiceTest.txt" in out["answer"]


# -- (d) the 2000: full prompt reaches the local model ------------------------

def test_gateway_local_complete_passes_full_prompt(monkeypatch):
    """The non-streaming local path must not truncate at 2000 chars."""
    import voicebridge.local_llm as LL
    seen = {}

    def fake_chat(prompt, convo, **kw):
        seen["len"] = len(prompt)
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(LL, "chat_local", fake_chat)
    long_prompt = "x" * 5000 + " TAIL-MARKER"
    res = GW._local_complete(long_prompt, FakeCfg({}), 100)
    assert res.ok is True
    assert seen["len"] == len(long_prompt)
    assert seen["prompt"].endswith("TAIL-MARKER")
