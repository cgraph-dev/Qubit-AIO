"""Regression: the voice/orchestrator worker path shares the ONE canonical
chat-session authority with ``/qubit-chat``.

Trace (2026-09-21, direct inspection):

* ``/qubit-chat`` non-streaming (``POST /api/qubit/chat`` -> ``h_chat_turn``)
  calls :func:`voicebridge.chat_transport.turn`
  (``voicebridge/chat_transport.py:706``). That persists the user and assistant
  turns through :func:`voicebridge.chat_transport.store`
  (``voicebridge/chat_transport.py:369``) ->
  :class:`voicebridge.qubit_session_store.QubitSessionStore`
  (``voicebridge/qubit_session_store.py:155``), a strict adapter over the
  vendored ``qubit_state.SessionDB`` (SQLite ``state.db``).
* The desktop/voice push-to-talk worker path calls
  :func:`voicebridge.agent.run_session` via ``_agent_run_session`` at
  ``voicebridge/orchestrator.py:1693`` (``run_worker_task``),
  ``:1829`` (``_spawn_session_worker``) and ``:2926`` (``_handle_agent``).
* Before this regression, ``agent.run_session`` (``voicebridge/agent.py:213``)
  wrote NO transcript at all — only an ``agent.session`` audit line. A voice
  worker turn was therefore invisible to ``/qubit-chat`` and vice versa: the
  same session id was not continuous and the transcript was lost (a
  parallel audit lineage with no persisted transcript).
* The fix is additive: ``run_session`` now persists its user task and its
  assistant reply through the SAME ``chat_transport.store(cfg)`` authority,
  keeping its signature/return contract and its audit line. No second store is
  created.

These tests prove the convergence hermetically (stubbed local model, isolated
``XDG_STATE_HOME`` via ``tests/conftest.py``) and assert that the only writable
chat store is the canonical ``state.db`` — no ``chat_sessions.json`` legacy
store appears.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _stub_local(monkeypatch, reply="stub reply"):
    """Stub the local Brain4 call the gateway routes to (no Ollama/network)."""
    import voicebridge.local_llm as _llm

    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain4",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None):
        calls.append({"text": text, "model": model,
                      "conversation_prompt": conversation_prompt,
                      "context": context})
        return reply

    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local", _fake)
    return _fake


def _no_plan(monkeypatch):
    """Force the direct-answer path (deterministic, no planner proposal)."""
    import voicebridge.planner as _pl

    monkeypatch.setattr(_pl, "plan", lambda _t: None)


def _runtime_home(cfg):
    from voicebridge import qubit_runtime

    return qubit_runtime.runtime_home_for_cfg(cfg)


def _assert_one_canonical_store(cfg, st):
    """The one writable chat store is the adapter over ``state.db``."""
    from voicebridge.qubit_session_store import QubitSessionStore

    assert isinstance(st, QubitSessionStore), type(st)
    runtime = _runtime_home(cfg)
    assert (runtime / "state.db").exists(), list(runtime.iterdir())
    # No legacy/secondary chat store file is ever created.
    assert not (runtime / "chat_sessions.json").exists()
    assert not (Path(cfg.state_dir()) / "chat_sessions.json").exists()


def test_voice_run_session_and_qubit_chat_share_one_transcript(
        tmp_path, monkeypatch):
    """A voice worker turn and a ``/qubit-chat`` turn on one session id are
    mutually visible through the canonical store (both directions)."""
    from voicebridge import agent as A
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    _no_plan(monkeypatch)
    voice_stub = _stub_local(monkeypatch, "voice answer")

    sid = "voice9u-shared"
    # Voice/orchestrator worker turn == agent.run_session (the exact callable
    # the orchestrator invokes at orchestrator.py:1693/1829/2926).
    res = A.run_session(cfg, "voice task", session=sid)
    assert res["state"] == "OK", res
    assert res["session"] == sid
    assert voice_stub.calls and "voice task" in voice_stub.calls[0]["text"]

    st = CT.store(cfg)
    shown = st.show(sid)
    assert shown["state"] == "OK", shown
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
    assert shown["messages"][0]["content"] == "voice task"
    assert shown["messages"][-1]["content"] == "voice answer"
    _assert_one_canonical_store(cfg, st)

    # The ``/qubit-chat`` turn on the SAME session id is continuous and its
    # bounded context carries the voice turn (chat sees voice).
    chat_stub = _stub_local(monkeypatch, "chat answer")
    out = CT.turn(cfg, "chat follow-up", session=sid)
    assert out["ok"] is True, out
    assert out["session_id"] == sid
    assert chat_stub.calls, "the chat turn reached the model"
    chat_prompt = chat_stub.calls[0]["text"]
    assert "chat follow-up" in chat_prompt
    assert "voice answer" in chat_prompt, chat_prompt
    assert "voice task" in chat_prompt, chat_prompt

    # Both turns now live in one transcript; the voice path can read the chat
    # turn back through the same store (voice sees chat).
    shown2 = st.show(sid)
    assert [m["role"] for m in shown2["messages"]] == [
        "user", "assistant", "user", "assistant"]
    assert shown2["messages"][2]["content"] == "chat follow-up"
    assert shown2["messages"][3]["content"] == "chat answer"
    assert shown2["session"]["message_count"] == 4

    # A second voice turn appends after the chat turn in the one transcript.
    _stub_local(monkeypatch, "voice answer 2")
    res2 = A.run_session(cfg, "voice follow-up", session=sid)
    assert res2["state"] == "OK", res2
    shown3 = st.show(sid)
    assert [m["role"] for m in shown3["messages"]] == [
        "user", "assistant", "user", "assistant", "user", "assistant"]
    assert [m["content"] for m in shown3["messages"][-2:]] == [
        "voice follow-up", "voice answer 2"]
    _assert_one_canonical_store(cfg, st)


def test_orchestrator_worker_task_persists_through_canonical_store(
        tmp_path, monkeypatch):
    """``VoiceOrchestrator.run_worker_task`` (the headless voice worker path,
    which calls ``agent.run_session``) persists into the canonical store and
    stays continuous with ``/qubit-chat`` on the same session id."""
    from voicebridge import chat_transport as CT
    from voicebridge.dispatch_guard import WorkerGuard
    from voicebridge.orchestrator import VoiceOrchestrator

    cfg = _cfg(tmp_path, monkeypatch)
    _no_plan(monkeypatch)
    _stub_local(monkeypatch, "worker answer")

    orch = object.__new__(VoiceOrchestrator)          # skip heavy __init__
    orch.cfg = cfg
    orch.dry_run = False
    orch.safety = types.SimpleNamespace(check=lambda text: None)
    orch._worker_guard = WorkerGuard(cfg)
    orch._power_policy = types.SimpleNamespace(
        control=types.SimpleNamespace(state={}))
    orch._agent_tool = None
    orch._augment_worker_text = lambda t: t

    sid = "qubit:voice:9u"
    res = orch.run_worker_task("orchestrator voice task", session=sid)
    assert res["state"] == "OK", res
    assert res["session"] == sid
    assert res["reply"] == "worker answer"

    st = CT.store(cfg)
    shown = st.show(sid)
    assert shown["state"] == "OK", shown
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
    assert shown["messages"][0]["content"] == "orchestrator voice task"
    assert shown["messages"][-1]["content"] == "worker answer"
    _assert_one_canonical_store(cfg, st)

    # Continuous with the dashboard chat surface on that same session id.
    _stub_local(monkeypatch, "chat reply")
    out = CT.turn(cfg, "chat on the voice session", session=sid)
    assert out["ok"] is True and out["session_id"] == sid, out
    shown2 = st.show(sid)
    assert [m["role"] for m in shown2["messages"]] == [
        "user", "assistant", "user", "assistant"]
    assert shown2["session"]["message_count"] == 4
    _assert_one_canonical_store(cfg, st)


def test_run_session_store_failure_does_not_change_return_contract(
        tmp_path, monkeypatch):
    """Persistence is additive: if the canonical store is unavailable, the
    session still returns its typed OK result (never fabricated, never broken
    by a store fault)."""
    from voicebridge import agent as A

    cfg = _cfg(tmp_path, monkeypatch)
    _no_plan(monkeypatch)
    _stub_local(monkeypatch, "answer despite store fault")

    def _boom(_cfg):
        raise RuntimeError("canonical store unavailable")

    import voicebridge.chat_transport as CT

    monkeypatch.setattr(CT, "store", _boom)
    res = A.run_session(cfg, "task", session="fault9u")
    assert res["state"] == "OK", res
    assert res["reply"] == "answer despite store fault"
