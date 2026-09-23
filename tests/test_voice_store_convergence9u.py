"""Session/store convergence for the plain voice conversation path (9u).

Before this change, ``VoiceOrchestrator._handle_conversation`` (the plain
spoken chat path — mic/PTT/duplex, ``voicebridge/orchestrator.py``) kept only an
in-memory :class:`voicebridge.intent.state.ConversationState` and wrote nothing
to the canonical chat store. A spoken chat turn was therefore invisible to
``/qubit-chat`` and a ``/qubit-chat`` turn was invisible to the next voice turn:
two transcripts, no continuity.

After: the plain voice conversation path persists each real user/assistant
exchange through the ONE ``chat_transport.store(cfg)`` authority
(:class:`voicebridge.qubit_session_store.QubitSessionStore`, a strict adapter
over the vendored ``qubit_state.SessionDB`` ``state.db``) and builds its
prior-conversation prompt from that same store via
``voicebridge/context_policy`` — so voice and ``/qubit-chat`` share one
transcript in BOTH directions. The in-memory ``ConversationState`` and every
return contract are unchanged; persistence is best-effort and never fabricates
or masks a turn. Per-task worker sessions stay explicit/distinct by design.

These tests are hermetic (stubbed local model, isolated XDG state via
``tests/conftest.py``); they do not run the full suite.
"""
from __future__ import annotations

import threading
import types
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


def _bare_orch(cfg, *, reply="voice reply", captured=None):
    """A VoiceOrchestrator with only the attributes ``_handle_conversation``
    touches, so the path is tested without loading audio/model layers."""
    from voicebridge.intent.state import ConversationState
    from voicebridge.orchestrator import VoiceOrchestrator

    orch = object.__new__(VoiceOrchestrator)  # skip heavy __init__
    orch.cfg = cfg
    orch.conversation = ConversationState(max_turns=8)
    orch.sticky_route = types.SimpleNamespace(target=lambda: "")
    orch.learning = types.SimpleNamespace(as_prompt=lambda: "")
    orch.shared = types.SimpleNamespace(append_assistant=lambda *a, **k: None)
    orch._web_lock = threading.Lock()
    orch._last_web_summary = ""
    orch._last_web_route = ""

    def _feed_push(_item):
        return None

    orch.feed_push = _feed_push
    orch.speak = lambda _text: None

    def _chat_local_reply(text, conv_prompt, llm_cfg, mem_ctx):
        if captured is not None:
            captured.append({"text": text, "conv_prompt": conv_prompt})
        return reply, False

    # An instance attribute shadows the bound method: it is called as
    # ``self._chat_local_reply(text, conv_prompt, llm_cfg, mem_ctx)``.
    orch._chat_local_reply = _chat_local_reply
    return orch


def _canonical(cfg):
    from voicebridge.agent import canonical_session_id
    return canonical_session_id(cfg)


def _assert_one_canonical_store(cfg):
    """The one writable chat store is the adapter over ``state.db``."""
    from voicebridge import qubit_runtime
    from voicebridge.chat_transport import store
    from voicebridge.qubit_session_store import QubitSessionStore

    st = store(cfg)
    assert isinstance(st, QubitSessionStore), type(st)
    runtime = qubit_runtime.runtime_home_for_cfg(cfg)
    assert (runtime / "state.db").exists(), list(runtime.iterdir())
    # No legacy/secondary chat store file is ever created.
    assert not (runtime / "chat_sessions.json").exists()
    assert not (Path(cfg.state_dir()) / "chat_sessions.json").exists()
    return st


# ---------------------------------------------------------------------------
# (1) Plain voice turns persist to the one canonical store
# ---------------------------------------------------------------------------
def test_plain_voice_turn_persists_to_canonical_store(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _bare_orch(cfg, reply="voice answer")

    orch._handle_conversation("tell me a short story")

    sid = _canonical(cfg)
    st = _assert_one_canonical_store(cfg)
    shown = st.show(sid)
    assert shown["state"] == "OK", shown
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
    assert shown["messages"][0]["content"] == "tell me a short story"
    assert shown["messages"][-1]["content"] == "voice answer"
    # The canonical default session id is the same one /qubit-chat resolves.
    assert sid == CT.DEFAULT_SESSION == "dashboard-chat"


def test_in_memory_conversation_is_unchanged(tmp_path, monkeypatch):
    """The additive change keeps the in-memory rolling history behavior."""
    cfg = _cfg(tmp_path, monkeypatch)
    orch = _bare_orch(cfg, reply="voice answer")

    orch._handle_conversation("first voice turn")

    assert [t["role"] for t in orch.conversation.turns] == ["user", "assistant"]
    assert orch.conversation.turns[-1]["text"] == "voice answer"


# ---------------------------------------------------------------------------
# (2) Bidirectional context through context_policy / the one store
# ---------------------------------------------------------------------------
def test_voice_turn_sees_prior_qubit_chat_turn(tmp_path, monkeypatch):
    """chat -> voice: a /qubit-chat turn is folded into the next voice prompt."""
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    sid = _canonical(cfg)

    # A dashboard /qubit-chat turn on the canonical session.
    _stub_local(monkeypatch, "chat answer")
    out = CT.turn(cfg, "dashboard question", session=sid)
    assert out["ok"] is True, out

    captured = []
    orch = _bare_orch(cfg, reply="voice answer", captured=captured)
    orch._handle_conversation("voice follow-up")

    assert captured, "the voice path reached the local model"
    prompt = captured[0]["conv_prompt"]
    assert "dashboard question" in prompt, prompt
    assert "chat answer" in prompt, prompt

    # One interleaved transcript, in order.
    st = _assert_one_canonical_store(cfg)
    shown = st.show(sid)
    assert [m["content"] for m in shown["messages"]] == [
        "dashboard question", "chat answer",
        "voice follow-up", "voice answer"]


def test_qubit_chat_sees_prior_voice_turn(tmp_path, monkeypatch):
    """voice -> chat: a plain voice turn is visible to /qubit-chat context."""
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    sid = _canonical(cfg)
    orch = _bare_orch(cfg, reply="voice answer")
    orch._handle_conversation("voice question")

    stub = _stub_local(monkeypatch, "chat answer")
    out = CT.turn(cfg, "dashboard follow-up", session=sid)
    assert out["ok"] is True, out
    assert stub.calls, "the chat turn reached the model"
    prompted = stub.calls[0]["text"]
    assert "dashboard follow-up" in prompted
    assert "voice question" in prompted, prompted
    assert "voice answer" in prompted, prompted

    st = _assert_one_canonical_store(cfg)
    shown = st.show(sid)
    assert [m["content"] for m in shown["messages"]] == [
        "voice question", "voice answer",
        "dashboard follow-up", "chat answer"]


# ---------------------------------------------------------------------------
# (3) Per-task worker sessions stay distinct by design
# ---------------------------------------------------------------------------
def test_plain_voice_conversation_uses_canonical_session(tmp_path, monkeypatch):
    """The plain conversation writes the canonical id; a per-task worker id is
    explicitly preserved (never collapsed onto the conversation)."""
    from voicebridge import chat_transport as CT
    from voicebridge.agent import resolve_session_id
    from voicebridge.orchestrator import _canonical_agent_session

    cfg = _cfg(tmp_path, monkeypatch)
    assert _canonical_agent_session(cfg) == _canonical(cfg) == CT.DEFAULT_SESSION
    # Explicit per-task worker ids are not aliases and stay verbatim.
    for task_id in ("qubit:voice:task-abc", "voice-task-deadbeef"):
        assert resolve_session_id(cfg, task_id) == task_id, task_id

    orch = _bare_orch(cfg, reply="voice answer")
    orch._handle_conversation("a conversational turn")
    st = _assert_one_canonical_store(cfg)
    # The conversation landed on the canonical id only.
    assert st.show(_canonical(cfg))["state"] == "OK"
    assert st.show("qubit:voice:task-abc")["state"] == "MISSING"


# ---------------------------------------------------------------------------
# (4) Filler preserves its "no history pollution" behavior
# ---------------------------------------------------------------------------
def test_filler_stays_out_of_the_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    orch = _bare_orch(cfg)

    orch._handle_conversation("hello")

    sid = _canonical(cfg)
    st = _assert_one_canonical_store(cfg)
    shown = st.show(sid)
    # Never created -> still MISSING; no filler turn was persisted.
    assert shown["state"] == "MISSING", shown
    assert orch.conversation.turns == []


# ---------------------------------------------------------------------------
# (5) Persistence is additive: a store fault never changes the turn
# ---------------------------------------------------------------------------
def test_store_fault_preserves_return_contract(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(_cfg):
        raise RuntimeError("canonical store unavailable")

    monkeypatch.setattr(CT, "store", _boom)
    orch = _bare_orch(cfg, reply="voice answer")

    # Must not raise, and the in-memory turn is still recorded.
    orch._handle_conversation("a conversational turn")
    assert [t["role"] for t in orch.conversation.turns] == ["user", "assistant"]
    assert orch.conversation.turns[-1]["text"] == "voice answer"


def test_voice_context_store_fault_falls_back_to_snapshot(
        tmp_path, monkeypatch):
    """When the store is unavailable the prompt falls back to the per-request
    in-memory snapshot instead of failing the turn."""
    from voicebridge import chat_transport as CT
    from voicebridge.orchestrator import _WebCtx

    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(_cfg):
        raise RuntimeError("canonical store unavailable")

    monkeypatch.setattr(CT, "store", _boom)
    captured = []
    orch = _bare_orch(cfg, reply="voice answer", captured=captured)

    # Seed the per-request snapshot the way process_web_utterance does.
    orch._new_ctx("voice follow-up")
    ctx = orch._cur_ctx()
    assert isinstance(ctx, _WebCtx)
    ctx.conv_snapshot = "user: earlier voice turn"

    orch._handle_conversation("voice follow-up")
    assert captured and captured[0]["conv_prompt"] == "user: earlier voice turn"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
