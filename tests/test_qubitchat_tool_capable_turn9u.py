"""DECISIONS #26 — a general /qubit-chat turn runs the tool-capable agent.

Root cause (measured 2026-10-05): ``chat_transport._decide_branch`` sent a
capability/meta question ("list every tool and skill you have access to") to
``answer_local`` — the legacy bare ``providers.gateway`` text path with no tool
schema — so the plain model hallucinated its toolset (and echoed legacy brand
names). General turns must instead run the SAME ``run_agent.AIAgent`` the
``/chat`` terminal drives (full tool schema, real tool calls).

These tests pin the routing hermetically: a general turn reaches
``voicebridge.agent.run_agent_turn`` on both the blocking and streaming paths,
the deterministic fast paths never do, and the legacy model path stays the
fallback when no tool-capable vendored runtime is available.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeStore:
    def __init__(self):
        self.rows = {}

    def ensure(self, sid, *, model=None, identity="dashboard:user"):
        sid = (sid or "").strip() or "dashboard-chat"
        self.rows.setdefault(sid, [])
        return {"state": "OK", "session": {"id": sid}, "messages": []}

    def append(self, sid, role, content, *, meta=None, model=None,
               timestamp=None):
        self.rows.setdefault(sid, []).append({"role": role, "content": content})
        return {"state": "OK", "message": {"role": role, "content": content},
                "message_count": len(self.rows[sid])}

    def messages(self, sid, *, limit=200, offset=0):
        rows = list(self.rows.get(sid, []))
        return {"state": "OK", "session_id": sid, "messages": rows,
                "total": len(rows), "limit": limit, "offset": offset}


def _hermetic(monkeypatch, fake, decision):
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda cfg: True)
    monkeypatch.setattr(CT, "_write_turn_marker", lambda *a, **k: None)
    monkeypatch.setattr(CT, "_clear_turn_marker", lambda *a, **k: None)
    monkeypatch.setattr(CT, "record_activity", lambda *a, **k: {"state": "OK"})
    monkeypatch.setattr(CT, "_decide_branch", lambda text, cfg: dict(decision))
    monkeypatch.setattr(CT, "resolve_route",
                        lambda *a, **k: {"ok": True, "model": "deepseek-v4.1-flash",
                                         "provider": "opencode-go", "kind": "api"})
    return CT


def test_blocking_general_turn_runs_tool_capable_agent(monkeypatch):
    from voicebridge import agent as AG

    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake,
                   {"action": "answer_local", "kind": "conversation",
                    "task_text": "list every tool and skill you have access to"})
    seen = {}

    def _agent(cfg, text, *, session, reasoning=None, estop_engaged=None):
        seen["text"] = text
        seen["session"] = session
        return {"state": "OK", "reply": "I have 33 tools across skills/plugins/MCP.",
                "session_id": session, "elapsed_ms": 5,
                "receipt": {"runtime": "qubit-vendored-agent",
                            "path": "voicebridge.agent.run_session",
                            "tools": ["terminal", "skills_list"]}}

    monkeypatch.setattr(AG, "run_agent_turn", _agent)
    monkeypatch.setattr(CT, "_generate",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("legacy model path must not run")))

    out = CT.turn({}, "list every tool and skill you have access to",
                  session="tools-9u")
    assert out["ok"] is True, out
    assert out["route"] == "agent", out
    assert "33 tools" in out["reply"], out
    assert seen["text"] == "list every tool and skill you have access to"


def test_streaming_general_turn_runs_tool_capable_agent(monkeypatch):
    from voicebridge import agent as AG

    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake,
                   {"action": "answer_local", "kind": "conversation",
                    "task_text": "list every tool and skill you have access to"})
    monkeypatch.setattr(AG, "run_agent_turn",
                        lambda cfg, text, *, session, reasoning=None,
                        estop_engaged=None: {
                            "state": "OK", "reply": "real toolset",
                            "session_id": session,
                            "receipt": {"tools": ["terminal"]}})

    events = list(CT.stream_turn(
        {}, "list every tool and skill you have access to",
        session="tools-stream-9u"))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start", events
    assert any(e["event"] == "delta" and e.get("text") == "real toolset"
               for e in events), events
    done = [e for e in events if e["event"] == "done"]
    assert done and done[0]["reply"] == "real toolset", events


def test_general_turn_falls_back_when_no_tool_runtime(monkeypatch):
    from voicebridge import agent as AG

    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake,
                   {"action": "answer_local", "kind": "conversation",
                    "task_text": "hello"})
    # No tool-capable vendored runtime -> None, so the legacy path runs.
    monkeypatch.setattr(AG, "run_agent_turn", lambda *a, **k: None)
    monkeypatch.setattr(CT, "_generate",
                        lambda cfg, sid, clean, model, context, **k: {
                            "ok": True, "reply": "legacy reply",
                            "session_id": sid})

    out = CT.turn({}, "hello", session="fallback-9u")
    assert out["ok"] is True and out["reply"] == "legacy reply", out


def test_fast_paths_never_reach_the_agent(monkeypatch):
    from voicebridge import agent as AG

    calls = []
    monkeypatch.setattr(AG, "run_agent_turn",
                        lambda *a, **k: calls.append(1))
    # A registry match is a fast path: the agent seam must not be consulted.
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake,
                   {"action": "registry", "kind": "conversation",
                    "task_text": "open youtube in the browser"})
    from voicebridge import voice_commands as VC

    monkeypatch.setattr(VC, "voice_say",
                        lambda cfg, text, **kw: {
                            "state": "ok", "speech": "Opened.",
                            "route": "browser.open", "entry": "browser.open"})
    out = CT.turn({}, "open youtube in the browser", session="fast-9u")
    assert out["ok"] is True, out
    assert calls == [], "a fast path must not run the agent"
