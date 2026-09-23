"""Session-continuity regression (9u): ONE canonical conversation across the
desktop/PTT/voice path and ``/qubit-chat``.

Before this change:

* ``/qubit-chat`` resolves its default session to
  ``chat_transport.DEFAULT_SESSION`` (``"dashboard-chat"``)
  (``voicebridge/chat_transport.py:41`` / ``_resolve_session`` at
  ``voicebridge/chat_transport.py:605``).
* the desktop/PTT agent delegation path hard-coded ``session="qubit-voice"``
  (``voicebridge/orchestrator.py:2927``), and the headless/worker paths use
  ``cfg.session_key(...)`` -> ``qubit:voice...``
  (``voicebridge/config.py:650``), so the voice default conversation and the
  dashboard conversation were two different transcripts.
* ``agent.run_session`` persisted to the canonical store but never folded the
  prior transcript into its own prompt, so a voice turn could not continue a
  ``/qubit-chat`` conversation.

The fix (this file's subject): a typed :func:`voicebridge.agent.resolve_session_id`
maps the voice-default aliases onto one configurable canonical conversation id
(``agent.session_id``, default ``chat_transport.DEFAULT_SESSION``), an explicit
id is preserved verbatim, and ``run_session`` reads bounded prior context from
the canonical store through ``voicebridge/context_policy.py`` and folds it into
the prompt. No second store is created.

These tests are hermetic: the local model is stubbed and XDG state is isolated
by ``tests/conftest.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _stub_local(monkeypatch):
    """Stub the local Brain4 call; replies are numbered per call so each
    surface's reply is distinguishable in the next surface's prompt."""
    import voicebridge.local_llm as _llm

    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain4",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None):
        calls.append({"text": text, "model": model,
                      "conversation_prompt": conversation_prompt,
                      "context": context})
        return f"stub-{len(calls)}"

    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local", _fake)
    return _fake


def _no_plan(monkeypatch):
    import voicebridge.planner as _pl

    monkeypatch.setattr(_pl, "plan", lambda _t: None)


def _assert_one_canonical_store(cfg):
    from voicebridge import qubit_runtime
    from voicebridge.chat_transport import store
    from voicebridge.qubit_session_store import QubitSessionStore

    st = store(cfg)
    assert isinstance(st, QubitSessionStore), type(st)
    runtime = qubit_runtime.runtime_home_for_cfg(cfg)
    assert (runtime / "state.db").exists(), list(runtime.iterdir())
    assert not (runtime / "chat_sessions.json").exists()
    assert not (Path(cfg.state_dir()) / "chat_sessions.json").exists()
    return st


# ---------------------------------------------------------------------------
# Typed resolution: default aliases converge; explicit ids are preserved
# ---------------------------------------------------------------------------
def test_default_voice_ids_converge_on_canonical_dashboard_session(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.agent import canonical_session_id, resolve_session_id

    cfg = _cfg(tmp_path, monkeypatch)
    canonical = canonical_session_id(cfg)
    assert canonical == CT.DEFAULT_SESSION == "dashboard-chat"

    # Every voice/PTT default spelling maps onto the one canonical id.
    for alias in ("", "   ", "voice", "qubit:voice", "qubit-voice"):
        assert resolve_session_id(cfg, alias) == canonical, alias

    # An explicit, non-alias id is never rewritten.
    assert resolve_session_id(cfg, "explicit-9u") == "explicit-9u"
    assert resolve_session_id(
        cfg, "qubit:voice:task-abc") == "qubit:voice:task-abc"


def test_canonical_session_id_is_configurable(tmp_path, monkeypatch):
    from voicebridge.agent import canonical_session_id, resolve_session_id

    cfg = _cfg(tmp_path, monkeypatch,
               extra="agent:\n  session_id: my-convo\n")
    assert canonical_session_id(cfg) == "my-convo"
    assert resolve_session_id(cfg, "qubit-voice") == "my-convo"
    assert resolve_session_id(cfg, "explicit") == "explicit"


def test_run_session_with_default_alias_lands_on_canonical_session(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.agent import canonical_session_id, run_session

    cfg = _cfg(tmp_path, monkeypatch)
    _no_plan(monkeypatch)
    _stub_local(monkeypatch)

    # session="" is the empty/voice-default alias: it must converge.
    res = run_session(cfg, "voice task", session="")
    assert res["state"] == "OK", res
    canonical = canonical_session_id(cfg)
    assert res["session"] == canonical
    assert res["session_id"] == canonical

    shown = CT.store(cfg).show(canonical)
    assert shown["state"] == "OK", shown
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
    _assert_one_canonical_store(cfg)


# ---------------------------------------------------------------------------
# Continuity in BOTH directions through the one canonical store
# ---------------------------------------------------------------------------
def test_voice_then_chat_and_chat_then_voice_are_continuous(
        tmp_path, monkeypatch):
    """The same conversation id yields continuous context voice->/qubit-chat
    and /qubit-chat->voice; exactly one store is written."""
    from voicebridge import chat_transport as CT
    from voicebridge.agent import canonical_session_id, run_session

    cfg = _cfg(tmp_path, monkeypatch)
    _no_plan(monkeypatch)
    stub = _stub_local(monkeypatch)
    sid = canonical_session_id(cfg)

    # 1. Voice (desktop/PTT worker) turn.
    res = run_session(cfg, "voice task", session=sid)
    assert res["state"] == "OK", res
    assert res["reply"] == "stub-1"
    assert "voice task" in stub.calls[0]["text"]

    # 2. /qubit-chat turn on the SAME canonical id sees the voice turn.
    out = CT.turn(cfg, "chat follow-up", session=sid)
    assert out["ok"] is True, out
    assert out["session_id"] == sid
    chat_prompt = stub.calls[1]["text"]
    assert "chat follow-up" in chat_prompt
    assert "voice task" in chat_prompt, chat_prompt
    assert "stub-1" in chat_prompt, chat_prompt

    # 3. A second voice turn now sees the chat turn (chat -> voice).
    res2 = run_session(cfg, "voice follow-up", session=sid)
    assert res2["state"] == "OK", res2
    voice_prompt = stub.calls[2]["text"]
    assert "voice follow-up" in voice_prompt
    assert "chat follow-up" in voice_prompt, voice_prompt
    assert "stub-2" in voice_prompt, voice_prompt

    # One transcript, in order, on the one canonical store.
    st = _assert_one_canonical_store(cfg)
    shown = st.show(sid)
    assert [m["role"] for m in shown["messages"]] == [
        "user", "assistant", "user", "assistant", "user", "assistant"]
    assert [m["content"] for m in shown["messages"]] == [
        "voice task", "stub-1",
        "chat follow-up", "stub-2",
        "voice follow-up", "stub-3"]


def test_run_worker_task_voice_alias_converges(tmp_path, monkeypatch):
    """The voice-task command passes ``qubit-voice`` into ``run_worker_task``;
    that alias must land on the canonical conversation, and its receipt must
    name the same session the store wrote."""
    import types

    from voicebridge import chat_transport as CT
    from voicebridge.agent import canonical_session_id
    from voicebridge.dispatch_guard import WorkerGuard
    from voicebridge.orchestrator import VoiceOrchestrator

    cfg = _cfg(tmp_path, monkeypatch)
    _no_plan(monkeypatch)
    _stub_local(monkeypatch)

    orch = object.__new__(VoiceOrchestrator)          # skip heavy __init__
    orch.cfg = cfg
    orch.dry_run = False
    orch.safety = types.SimpleNamespace(check=lambda text: None)
    orch._worker_guard = WorkerGuard(cfg)
    orch._power_policy = types.SimpleNamespace(
        control=types.SimpleNamespace(state={}))
    orch._agent_tool = None
    orch._augment_worker_text = lambda t: t

    canonical = canonical_session_id(cfg)
    res = orch.run_worker_task("voice task", session="qubit-voice")
    assert res["state"] == "OK", res
    assert res["session"] == canonical
    shown = CT.store(cfg).show(canonical)
    assert shown["state"] == "OK", shown
    assert [m["content"] for m in shown["messages"]] == [
        "voice task", "stub-1"]
    _assert_one_canonical_store(cfg)


# ---------------------------------------------------------------------------
# Desktop/PTT call site converges on the canonical resolver
# ---------------------------------------------------------------------------
def test_desktop_ptt_agent_path_uses_canonical_session(tmp_path, monkeypatch):
    from voicebridge.agent import canonical_session_id
    from voicebridge.orchestrator import _canonical_agent_session

    cfg = _cfg(tmp_path, monkeypatch)
    assert _canonical_agent_session(cfg) == canonical_session_id(cfg)

    import voicebridge.orchestrator as O

    src = Path(O.__file__).read_text(encoding="utf-8")
    assert '_canonical_agent_session(self.cfg)' in src
    assert 'session="qubit-voice"' not in src, \
        "the desktop PTT path must not hard-code the legacy voice session id"
