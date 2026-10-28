"""W2 — Qubit-safe chat transport (voicebridge.chat_transport).

Executes the production transport directly (no route mock): persisted session
lifecycle, reload, export/import, real streaming events, cancellation, model
routing, and the PTY-shaped agent-backed terminal. The model transport is
stubbed so the tests are hermetic; the live Brain4 proof lives in
``tests/test_qbpv2_hermes_brain4.py``.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _stub_chat_local(monkeypatch, reply="stub chat reply"):
    import voicebridge.local_llm as _llm

    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None):
        calls.append({"text": text, "model": model,
                      "conversation_prompt": conversation_prompt})
        return reply

    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local", _fake)
    stream_calls = []

    def _fake_stream(text, conversation_prompt="", model="qubit-brain",
                     timeout_s=4.0, max_tokens=100, context="", on_sentence=None,
                     should_stop=None, num_ctx=None, reasoning=None):
        stream_calls.append({"text": text, "model": model})
        for sentence in (reply,):
            if on_sentence:
                on_sentence(sentence)
        return reply

    _fake.stream_calls = stream_calls
    monkeypatch.setattr(_llm, "chat_local_stream", _fake_stream)
    return _fake


# ------------------------------------------------------------- session store
def test_session_lifecycle_roundtrip(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)

    created = st.create("My chat")
    assert created["state"] == "OK", created
    sid = created["session"]["id"]
    assert st.append(sid, "user", "hello")["state"] == "OK"
    assert st.append(sid, "assistant", "hi there")["state"] == "OK"

    listed = st.list()
    assert listed["total"] == 1
    row = listed["sessions"][0]
    assert row["id"] == sid and row["message_count"] == 2
    # ``list`` now reports the REAL state.db source; the canonical native
    # create path writes ``dashboard`` (the old ``qubit-chat`` label was a
    # hardcoded mask over the DB column).
    assert row["source"] == "dashboard"

    shown = st.show(sid)
    assert shown["state"] == "OK" and len(shown["messages"]) == 2

    renamed = st.rename(sid, "Renamed")
    assert renamed["state"] == "OK"
    assert st.show(sid)["session"]["name"] == "Renamed"

    denied = st.delete(sid)
    assert denied["state"] == "CONFIRM_REQUIRED"
    assert st.show(sid)["state"] == "OK"
    assert st.delete(sid, confirm=True)["state"] == "OK"
    assert st.show(sid)["state"] == "MISSING"


def test_persistence_survives_reload(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    sid = st.create("Persisted")["session"]["id"]
    st.append(sid, "user", "remember me")
    st.append(sid, "assistant", "I will")

    fresh = CT.store(cfg)  # new instance re-reads the same state dir
    shown = fresh.show(sid)
    assert shown["state"] == "OK"
    assert [m["content"] for m in shown["messages"]] == ["remember me", "I will"]
    assert shown["session"]["message_count"] == 2


def test_export_import_roundtrip(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    sid = st.create("Exportable")["session"]["id"]
    st.append(sid, "user", "q")
    st.append(sid, "assistant", "a")

    env = st.export(sid)
    assert env["state"] == "OK" and env["schema"] == CT.SESSION_SCHEMA

    dest = CT.ChatSessionStore(tmp_path / "other")
    dest.reload()
    imported = dest.import_session(env)
    assert imported["state"] == "OK", imported
    new_sid = imported["session"]["id"]
    got = dest.show(new_sid)
    assert [m["content"] for m in got["messages"]] == ["q", "a"]

    again = dest.import_session(env)
    assert again["state"] == "EXISTS", again


# ------------------------------------------------------------------- turns
def test_turn_persists_user_and_assistant(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    stub = _stub_chat_local(monkeypatch, "the answer is 4")
    res = CT.turn(cfg, "what is 2+2", session="math")
    assert res["ok"] is True, res
    assert res["reply"] == "the answer is 4"
    assert res["receipt"]["model"] == "qubit-brain"
    assert stub.calls and stub.calls[0]["text"] == "what is 2+2"

    shown = CT.store(cfg).show("math")
    roles = [m["role"] for m in shown["messages"]]
    assert roles == ["user", "assistant"]
    assert shown["messages"][1]["content"] == "the answer is 4"


def test_turn_passes_prior_conversation_context(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    stub = _stub_chat_local(monkeypatch)
    CT.turn(cfg, "my name is Trick", session="ctx")
    CT.turn(cfg, "what is my name", session="ctx")
    # context is folded into the agent turn text (the thin agent wrapper has no
    # conversation_prompt parameter)
    assert "Trick" in stub.calls[1]["text"]


def test_model_routing_defaults_to_qubit_brain4_and_refuses_unknown(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    assert CT.configured_model(cfg) == "qubit-brain"
    ok = CT.resolve_model(cfg, None)
    assert ok == {"ok": True, "model": "qubit-brain"}
    ok2 = CT.resolve_model(cfg, "qubit-brain")
    assert ok2["ok"] is True
    bad = CT.resolve_model(cfg, "gpt-4o")
    assert bad["ok"] is False and bad["code"] == "MODEL_NOT_ALLOWED"

    _stub_chat_local(monkeypatch)
    res = CT.turn(cfg, "hi", session="m", model="gpt-4o")
    assert res["ok"] is False and res["code"] == "MODEL_NOT_ALLOWED"


def test_empty_model_reply_is_typed_not_fabricated(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    _stub_chat_local(monkeypatch, "")
    res = CT.turn(cfg, "hi", session="empty")
    assert res["ok"] is False and res["code"] == "MODEL_UNAVAILABLE", res
    # no assistant message was fabricated
    shown = CT.store(cfg).show("empty")
    assert [m["role"] for m in shown["messages"]] == ["user"]


def test_model_exception_surfaces_real_cause(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    def _boom(*a, **k):
        raise RuntimeError("ollama connection refused: 127.0.0.1:11434")

    monkeypatch.setattr(_llm, "chat_local", _boom)
    res = CT.turn(cfg, "hi", session="boom")
    assert res["ok"] is False and res["code"] == "MODEL_ERROR"
    assert "connection refused" in res["detail"]


def test_retry_reinvokes_without_duplicating_transcript(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    import voicebridge.local_llm as _llm

    cfg = _cfg(tmp_path, monkeypatch)
    mode = {"fail": True}

    def _fake(*a, **k):
        if mode["fail"]:
            raise RuntimeError("model down")
        return "recovered answer"

    monkeypatch.setattr(_llm, "chat_local", _fake)
    first = CT.turn(cfg, "help me", session="retry")
    assert first["ok"] is False and first["code"] == "MODEL_ERROR"
    assert [m["role"] for m in CT.store(cfg).show("retry")["messages"]] == ["user"]

    mode["fail"] = False
    second = CT.retry_last(cfg, "retry")
    assert second["ok"] is True and second["reply"] == "recovered answer"
    roles = [m["role"] for m in CT.store(cfg).show("retry")["messages"]]
    assert roles == ["user", "assistant"], roles

    nothing = CT.retry_last(cfg, "retry")
    assert nothing["ok"] is False and nothing["code"] == "NOTHING_TO_RETRY"
    missing = CT.retry_last(cfg, "no-such-session")
    assert missing["code"] == "SESSION_NOT_FOUND"


def test_estop_refuses_before_model_call(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    calls = _stub_chat_local(monkeypatch)
    monkeypatch.setattr(CT, "_estop", lambda _cfg: True)
    res = CT.turn(cfg, "hi", session="stop")
    assert res["ok"] is False and res["code"] == "EMERGENCY_STOP"
    assert calls.calls == []


# --------------------------------------------------------------- streaming
def test_stream_turn_emits_real_deltas_and_persists(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    def _fake_stream(text, conversation_prompt="", model="qubit-brain",
                     timeout_s=4.0, max_tokens=100, context="", on_sentence=None,
                     should_stop=None, num_ctx=None, reasoning=None):
        for sentence in ("Hello.", " World."):
            if on_sentence:
                on_sentence(sentence)
        return "Hello. World."

    monkeypatch.setattr(_llm, "chat_local_stream", _fake_stream)
    events = list(CT.stream_turn(cfg, "greet me", session="stream"))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start"
    deltas = [e["text"] for e in events if e["event"] == "delta"]
    assert deltas == ["Hello.", " World."]
    done = [e for e in events if e["event"] == "done"]
    assert done and done[0]["reply"] == "Hello. World."
    assert done[0]["receipt"]["model"] == "qubit-brain"
    shown = CT.store(cfg).show("stream")
    assert shown["messages"][-1]["content"] == "Hello. World."


def test_stream_turn_empty_is_typed(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    monkeypatch.setattr(
        _llm, "chat_local_stream",
        lambda *a, **k: "")
    events = list(CT.stream_turn(cfg, "hi", session="sempty"))
    errs = [e for e in events if e["event"] == "error"]
    assert errs and errs[0]["code"] == "MODEL_UNAVAILABLE"


def test_stream_sse_frames_are_typed(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    monkeypatch.setattr(
        _llm, "chat_local_stream",
        lambda text, conversation_prompt="", model="qubit-brain", timeout_s=4.0,
        max_tokens=100, context="", on_sentence=None, should_stop=None,
        num_ctx=None, reasoning=None: (on_sentence and on_sentence("Done."),
                                       "Done.")[1])
    frames = list(CT.stream_sse(cfg, "x", session="sse"))
    assert frames[0].startswith("event: start\ndata: ")
    assert any(f.startswith("event: delta\ndata: ") for f in frames)
    assert any(f.startswith("event: done\ndata: ") for f in frames)


def test_cancel_marks_inflight_turn(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    started = threading.Event()
    release = threading.Event()

    def _blocking(*a, **k):
        started.set()
        release.wait(5)
        return "late reply"

    monkeypatch.setattr(_llm, "chat_local", _blocking)
    box = []

    def _run():
        box.append(CT.turn(cfg, "long task", session="cancel-me"))

    t = threading.Thread(target=_run)
    t.start()
    assert started.wait(5), "model call never started"
    out = CT.cancel("cancel-me")
    assert out["ok"] is True, out
    release.set()
    t.join(5)
    assert box and box[0]["code"] == "CANCELLED", box

    missing = CT.cancel("never-started")
    assert missing["ok"] is False and missing["code"] == "NO_ACTIVE_TURN"


# ------------------------------------------------------- PTY-shaped agent
def test_agent_pty_is_agent_backed_not_shell(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    stub = _stub_chat_local(monkeypatch, "pong")
    sess = CT.AgentPtySession(cfg, "pty-1")
    assert b"agent-backed" in sess.banner()
    out = b"".join(sess.feed(b"ping\r"))
    assert b"pong" in out
    assert b"qubit> " in out
    assert stub.stream_calls and stub.stream_calls[0]["text"] == "ping"

    sess.feed(b"\x1b[RESIZE:120;40]")
    assert (sess.cols, sess.rows) == (80, 24)  # generator: not consumed yet
    list(sess.feed(b"\x1b[RESIZE:120;40]"))
    assert (sess.cols, sess.rows) == (120, 40)

    list(sess.feed(b"\x04"))
    assert sess.alive is False


def test_pty_feed_assembles_keystrokes_across_frames(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    _stub_chat_local(monkeypatch, "frame reply")
    import base64

    banner = CT.pty_feed(cfg, "pty-2", "", reset=True)
    assert banner["ok"] is True
    assert b"agent-backed" in base64.b64decode(banner["output"])
    # keystrokes arrive one at a time; the in-process session buffers the line
    for ch in b"hello":
        CT.pty_feed(cfg, "pty-2", base64.b64encode(bytes([ch])).decode())
    frame = CT.pty_feed(cfg, "pty-2", base64.b64encode(b"\r").decode())
    assert frame["ok"] is True
    assert b"frame reply" in base64.b64decode(frame["output"])
    assert CT.store(cfg).show("pty-2")["messages"][-1]["content"] == "frame reply"


# ------------------------------------------------- S5 activity projection hook
def test_turn_records_session_activity_summary(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import session_activity as SA

    cfg = _cfg(tmp_path, monkeypatch)
    _stub_chat_local(monkeypatch, "the answer is 4")
    res = CT.turn(cfg, "what is 2+2", session="act")
    assert res["ok"] is True, res

    acts = SA.store(cfg).list("act")
    assert acts["total"] == 1
    event = acts["events"][0]
    assert event["kind"] == "summary"
    assert event["detail"] == "the answer is 4"


def test_stream_turn_records_session_activity_summary(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import session_activity as SA

    cfg = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    def _fake_stream(text, conversation_prompt="", model="qubit-brain",
                     timeout_s=4.0, max_tokens=100, context="", on_sentence=None,
                     should_stop=None, num_ctx=None, reasoning=None):
        if on_sentence:
            on_sentence("Streamed answer.")
        return "Streamed answer."

    monkeypatch.setattr(_llm, "chat_local_stream", _fake_stream)
    events = list(CT.stream_turn(cfg, "q", session="sact"))
    assert events[-1]["event"] == "done", events

    acts = SA.store(cfg).list("sact")
    assert acts["total"] == 1
    assert acts["events"][0]["detail"] == "Streamed answer."


def test_failed_turn_records_no_activity(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import session_activity as SA

    cfg = _cfg(tmp_path, monkeypatch)
    _stub_chat_local(monkeypatch, "")
    res = CT.turn(cfg, "hi", session="noact")
    assert res["ok"] is False
    assert SA.store(cfg).list("noact")["total"] == 0
