"""Wave 9u — /qubit-chat turn confirmation is session-scoped + durable.

The staged-confirm path (T4/T5) is hardened to match the voice turn FSM
(:mod:`voicebridge.voice_edit.session_fsm`): a staged confirmation binds to the
session it was raised in, survives unrelated turns (and errors) in that session,
settles ONLY on an explicit affirmation, cancels on an explicit denial, and
exposes a bounded/redacted ``pending_confirmation`` descriptor on the turn's
``start``/``done`` events so the UI can render "awaiting confirmation".

Hermetic: the session store, model/agent, and the settle executor are doubles;
the durable confirmation journal is a real ``ApprovalJournal`` under ``tmp_path``
(the same store production uses). No model, no worker thread, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: Base64 of ``b"hi"`` — the raw file payload that must never reach an event.
_RAW_B64 = "aGk="


class _FakeStore:
    """Minimal chat_transport store double (ensure/append only)."""

    def __init__(self):
        self.rows = {}
        self.n = 0

    def ensure(self, sid, *, model=None, identity="dashboard:user"):
        sid = (sid or "").strip() or "dashboard-chat"
        self.rows.setdefault(sid, [])
        return {"state": "OK", "session": {"id": sid}, "messages": []}

    def append(self, sid, role, content, *, meta=None, model=None,
               timestamp=None):
        self.rows.setdefault(sid, []).append(
            {"role": role, "content": content, "meta": meta or {}})
        self.n += 1
        return {"state": "OK",
                "message": {"role": role, "content": content},
                "message_count": self.n}

    def messages(self, sid, *, limit=500, offset=0):
        msgs = list(self.rows.get(sid, []))
        return {"state": "OK", "session_id": sid, "messages": msgs,
                "total": len(msgs), "limit": limit, "offset": offset}


def _tmp_journal(monkeypatch, tmp_path):
    """Point the chat confirmation journal at a hermetic tmp file."""
    from voicebridge import chat_transport as CT
    from voicebridge.approvals import ApprovalJournal

    journal = ApprovalJournal(tmp_path / "approvals.jsonl",
                              default_ttl_s=120.0)
    monkeypatch.setattr(CT, "_task_journal", lambda cfg: journal)
    return journal


def _proposal(tool="local.files_upload"):
    return {
        "status": "needs-confirm",
        "pending": {
            "tool": tool,
            "args": {"target_path": "hello.html", "content_base64": _RAW_B64},
        },
        "session": "worker-9u",
    }


def _stage(CT, cfg, sid, key="task-key"):
    """Stage through the REAL staging primitive (durable journal record)."""
    return CT._stage_task_proposal(cfg, sid, key, _proposal())


def _wire(monkeypatch, fake, *, agent=None, decide=None):
    """Hermetic wiring: fake store + no model/agent + deterministic classify.

    ``agent`` is the canned ``_agent_chat`` result (None -> ok reply); ``decide``
    is the canned ``_decide_branch`` action (default ``answer_local``).
    """
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda cfg: True)
    monkeypatch.setattr(CT, "_pending_interrupted_turn",
                        lambda cfg, sid: None)
    monkeypatch.setattr(
        CT, "resolve_route",
        lambda cfg, model=None, **kw: {"ok": True, "model": "stub-model",
                                       "kind": "local", "profile_id": None,
                                       "context_tokens": None})
    action = decide or {"action": "answer_local"}
    monkeypatch.setattr(
        CT, "_decide_branch",
        lambda text, cfg=None, **kw: dict(action, task_text=text))
    canned = agent if agent is not None else {
        "ok": True, "reply": "sure thing", "elapsed_ms": 1,
        "receipt": {"runtime": "stub"}}
    calls = []

    def _fake_agent(cfg, st, sid, clean, **kw):
        calls.append(sid)
        return dict(canned)

    monkeypatch.setattr(CT, "_agent_chat", _fake_agent)
    return calls


def _pending_ids(journal):
    return [a.id for a in journal.pending()]


# ---------------------------------------------------------------------------
# (a)/(b) session binding + survival across sessions
# ---------------------------------------------------------------------------
def test_pending_is_session_scoped(monkeypatch, tmp_path):
    """Staging in session A does not leak into (or get touched by) session B."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake)
    journal = _tmp_journal(monkeypatch, tmp_path)

    staged = _stage(CT, {}, "sess-A")
    cid = staged["confirmation_id"]
    assert cid, staged

    # A turn in a DIFFERENT session is an ordinary turn: it must neither see,
    # settle, nor cancel A's pending.
    events = list(CT.stream_turn({}, "tell me a fun fact", session="sess-B"))
    assert any(e["event"] == "done" for e in events), events
    assert not any("pending_confirmation" in e for e in events), events

    assert _pending_ids(journal) == [cid], journal.pending()
    assert [a.id for a in CT._pending_task({}, "sess-A")] == [cid]
    assert CT._pending_task({}, "sess-B") == []


def test_affirm_settles_only_the_pending_session(monkeypatch, tmp_path):
    """A bare affirmation runs the staged action for ITS session, once."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake)
    journal = _tmp_journal(monkeypatch, tmp_path)
    staged = _stage(CT, {}, "sess-A")
    cid = staged["confirmation_id"]

    settle_calls = []

    def _fake_settle(cfg, st, sid):
        settle_calls.append(sid)
        for item in CT._pending_task({}, sid):
            journal.respond(item.id, "approve")
            journal.finish(item.id)
        return {"ok": True, "text": "Built hello.html", "reply": "Built hello.html",
                "session_id": sid, "route": "task"}

    monkeypatch.setattr(CT, "_settle_task_affirmed", _fake_settle)

    out = CT.turn({}, "yes", session="sess-A")
    assert out.get("ok") is True, out
    assert settle_calls == ["sess-A"], settle_calls
    assert CT._pending_task({}, "sess-A") == []
    assert journal.get(cid).state == "complete", journal.get(cid)


# ---------------------------------------------------------------------------
# (c) deny cancels; other preserves
# ---------------------------------------------------------------------------
def test_deny_cancels_the_pending(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake)
    journal = _tmp_journal(monkeypatch, tmp_path)
    staged = _stage(CT, {}, "sess-A")
    cid = staged["confirmation_id"]

    out = CT.turn({}, "no thanks", session="sess-A")
    assert out.get("ok") is True, out
    assert out.get("route") == "confirm", out
    assert CT._pending_task({}, "sess-A") == []
    assert journal.get(cid).state == "denied", journal.get(cid)


def test_other_text_preserves_pending_and_surfaces_it(monkeypatch, tmp_path):
    """Unrelated text is a NON-consuming turn: pending survives and is shown."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake)
    journal = _tmp_journal(monkeypatch, tmp_path)
    staged = _stage(CT, {}, "sess-A")
    cid = staged["confirmation_id"]

    events = list(CT.stream_turn({}, "what is the capital of France?",
                                 session="sess-A"))
    starts = [e for e in events if e["event"] == "start"]
    dones = [e for e in events if e["event"] == "done"]
    assert starts and dones, events
    pc = starts[0].get("pending_confirmation")
    assert pc is not None, starts[0]
    assert pc["action_summary"], pc
    assert isinstance(pc["staged_ms"], int) and pc["staged_ms"] >= 0, pc
    assert dones[-1].get("pending_confirmation"), dones[-1]

    # Bounded + redacted: the raw file payload never crosses the event boundary.
    blob = json.dumps(events)
    assert _RAW_B64 not in blob and "<h1>" not in blob, blob
    assert "content_base64" not in blob, blob

    # The pending was not consumed by the unrelated turn.
    assert CT._pending_task({}, "sess-A")[0].id == cid
    assert _pending_ids(journal) == [cid], journal.pending()


# ---------------------------------------------------------------------------
# (d) pending survives an error
# ---------------------------------------------------------------------------
def test_pending_survives_stream_error(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake, agent={"ok": False, "code": "MODEL_ERROR",
                                    "detail": "model blew up"})
    journal = _tmp_journal(monkeypatch, tmp_path)
    staged = _stage(CT, {}, "sess-A")
    cid = staged["confirmation_id"]

    events = list(CT.stream_turn({}, "do something unrelated",
                                 session="sess-A"))
    assert events[-1]["event"] == "error", events
    starts = [e for e in events if e["event"] == "start"]
    assert starts[0].get("pending_confirmation"), starts[0]

    # Never silently dropped on an error.
    assert _pending_ids(journal) == [cid], journal.pending()
    assert CT._pending_task({}, "sess-A")[0].id == cid


# ---------------------------------------------------------------------------
# (e) no-pending turns are unchanged
# ---------------------------------------------------------------------------
def test_no_pending_turn_has_no_confirmation_field(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake)
    _tmp_journal(monkeypatch, tmp_path)

    events = list(CT.stream_turn({}, "plain question", session="sess-clean"))
    assert any(e["event"] == "done" for e in events), events
    assert not any("pending_confirmation" in e for e in events), events


def test_deny_with_nothing_pending_is_a_normal_turn(monkeypatch, tmp_path):
    """A bare "no" with no staged work must keep the legacy path (no cancel)."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake)
    journal = _tmp_journal(monkeypatch, tmp_path)

    out = CT.turn({}, "no", session="sess-clean")
    assert out.get("route") != "confirm", out
    assert journal.pending() == []


def test_pending_confirmation_descriptor_is_bounded(monkeypatch, tmp_path):
    """The descriptor never contains the raw arguments or a secret, and none
    is returned when the session has no live pending."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _wire(monkeypatch, fake)
    _tmp_journal(monkeypatch, tmp_path)

    assert CT._pending_confirmation({}, "empty") is None
    _stage(CT, {}, "sess-A")
    desc = CT._pending_confirmation({}, "sess-A")
    assert desc and desc["kind"] == "task", desc
    assert desc["action_summary"] and "hello.html" in desc["action_summary"], desc
    blob = json.dumps(desc)
    assert _RAW_B64 not in blob and "content_base64" not in blob, blob
