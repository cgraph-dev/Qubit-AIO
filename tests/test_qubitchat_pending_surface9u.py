"""Wave 9u — live pending-confirmation must be surfaced on EVERY read surface.

The staged-confirmation banner is client state the SPA rebuilds from a bounded
``pending_confirmation`` signal. Two gaps broke that contract:

* the session-show route (``GET /api/qubit/chat/sessions/<id>``) never carried
  it, so a page reload dropped a live yes/no banner while the action stayed
  staged (``ChatPage`` reads ``extractPendingConfirm(info)`` on load); and
* the non-consuming deterministic turn paths (slash / direct-facts / repeat)
  emitted no signal on their terminal frames, so the SPA's authoritative
  ``onDone`` clear wiped the banner even though nothing was settled.

Hermetic: the session store, model/agent, and the settle/slash executors are
doubles; the durable confirmation journal is a real ``ApprovalJournal`` under
``tmp_path`` (the same store production uses). No model, no worker, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_RAW_B64 = "aGk="


class _FakeStore:
    """Minimal chat_transport store double (ensure/append/messages/show/meta)."""

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

    def show(self, sid):
        if sid not in self.rows:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        msgs = list(self.rows.get(sid, []))
        return {"state": "OK", "session": {"id": sid, "message_count": len(msgs)},
                "messages": msgs, "message_count": len(msgs)}

    def meta_get(self, sid):
        return {"meta": {}}


def _tmp_journal(monkeypatch, tmp_path):
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


def _wire_stream(monkeypatch, fake, journal, *, slash_text="slash ok"):
    """Hermetic stream wiring: fake store + no real model/agent/slash executor."""
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda cfg: True)
    monkeypatch.setattr(CT, "_pending_interrupted_turn", lambda cfg, sid: None)
    monkeypatch.setattr(CT, "_task_journal", lambda cfg: journal)
    monkeypatch.setattr(
        CT, "resolve_route",
        lambda cfg, model=None, **kw: {"ok": True, "model": "stub-model",
                                       "kind": "local", "profile_id": None,
                                       "context_tokens": None})
    monkeypatch.setattr(CT, "_decide_branch",
                        lambda text, cfg=None, **kw: {"action": "answer_local",
                                                      "task_text": text})

    def _finish(cfg, st, sid, clean):
        return {"ok": True, "text": slash_text, "reply": slash_text,
                "receipt": {"route": "slash"}}

    monkeypatch.setattr(CT, "_finish_slash", _finish)
    monkeypatch.setattr(CT, "_finish_fact", _finish)
    monkeypatch.setattr(CT, "_finish_repeat", _finish)


def _stage(CT, cfg, sid, key="task-key"):
    return CT._stage_task_proposal(cfg, sid, key, _proposal())


# ---------------------------------------------------------------------------
# session-show surfaces the live pending (the page-reload banner)
# ---------------------------------------------------------------------------
def test_session_show_surfaces_pending_confirmation(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as QC

    fake = _FakeStore()
    fake.ensure("sess-A")
    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_pending_interrupted_turn", lambda cfg, sid: None)
    journal = _tmp_journal(monkeypatch, tmp_path)
    staged = _stage(CT, {}, "sess-A", "task-key")
    assert staged["confirmation_id"]

    status, out = QC.h_chat_session_show({}, {}, {}, {"id": "sess-A"})
    assert status == 200, out
    pending = out.get("pending_confirmation")
    assert isinstance(pending, dict), out
    assert pending.get("action_summary")
    assert pending.get("confirmation_id") == staged["confirmation_id"]
    assert isinstance(pending.get("staged_ms"), int)
    # The raw base64 payload never crosses the read boundary.
    assert _RAW_B64 not in str(out)


def test_session_show_without_pending_has_no_key(monkeypatch, tmp_path):
    """Idle session-show stays byte-identical (no empty pending signal)."""
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as QC

    fake = _FakeStore()
    fake.ensure("sess-clear")
    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_pending_interrupted_turn", lambda cfg, sid: None)
    _tmp_journal(monkeypatch, tmp_path)

    status, out = QC.h_chat_session_show({}, {}, {}, {"id": "sess-clear"})
    assert status == 200
    assert "pending_confirmation" not in out


# ---------------------------------------------------------------------------
# deterministic (non-consuming) stream turns keep the banner alive
# ---------------------------------------------------------------------------
def _done(events):
    return [e for e in events if e.get("event") == "done"][-1]


def test_slash_done_preserves_and_surfaces_pending(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    journal = _tmp_journal(monkeypatch, tmp_path)
    _wire_stream(monkeypatch, fake, journal)
    _stage(CT, {}, "sess-slash", "task-key")

    events = list(CT.stream_turn({}, "/skills", session="sess-slash"))
    pending = _done(events).get("pending_confirmation")
    assert isinstance(pending, dict) and pending.get("action_summary"), events
    # A slash turn is non-consuming: the staged action is still live.
    assert len(CT._pending_task({}, "sess-slash")) == 1


def test_direct_fact_done_preserves_pending(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    journal = _tmp_journal(monkeypatch, tmp_path)
    _wire_stream(monkeypatch, fake, journal)
    _stage(CT, {}, "sess-fact", "task-key")
    # Force the deterministic facts branch without a live clock/weather read.
    monkeypatch.setattr(CT, "_direct_fact_answer", lambda text: "It is 12:00.")

    events = list(CT.stream_turn({}, "what time is it", session="sess-fact"))
    assert _done(events).get("pending_confirmation"), events
    assert len(CT._pending_task({}, "sess-fact")) == 1


def test_repeat_done_preserves_pending(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    fake.rows["sess-repeat"] = [{"role": "assistant", "content": "prior answer"}]
    journal = _tmp_journal(monkeypatch, tmp_path)
    _wire_stream(monkeypatch, fake, journal)
    _stage(CT, {}, "sess-repeat", "task-key")
    # Stub the deterministic reread predicate so the branch is taken.
    monkeypatch.setattr(CT, "_repeat_request", lambda text: True)
    monkeypatch.setattr(CT, "_last_assistant_text",
                        lambda st, sid: "prior answer")

    events = list(CT.stream_turn({}, "say that again", session="sess-repeat"))
    assert _done(events).get("pending_confirmation"), events
    assert len(CT._pending_task({}, "sess-repeat")) == 1


def test_plain_done_has_no_pending_key(monkeypatch, tmp_path):
    """A turn with nothing staged stays byte-identical (no empty field)."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    journal = _tmp_journal(monkeypatch, tmp_path)
    _wire_stream(monkeypatch, fake, journal)

    events = list(CT.stream_turn({}, "/skills", session="sess-none"))
    assert "pending_confirmation" not in _done(events), events


# ---------------------------------------------------------------------------
# the voice session id is ONE canonical value across the surfaces
# ---------------------------------------------------------------------------
def test_voice_session_id_is_one_value(monkeypatch, tmp_path):
    from voicebridge.voice_edit import voice_sessions as VS
    from voicebridge.voice_edit.service import VoiceEditService
    from voicebridge.agent import resolve_session_id
    from voicebridge import chat_transport as CT
    from voicebridge import voice_edit as VE
    from voicebridge.voice_edit.wake import _voice_session_id

    cfg = {"voice_edit": {"voice_session_mode": "single"}}
    assert VS.SINGLE_SESSION_ID == "voice-chat"
    assert VS.voice_session_id(cfg) == "voice-chat"
    # service.py
    svc = VoiceEditService(cfg, estop=lambda cfg: False,
                           session_store=_FakeStore())
    assert svc._voice_session() == "voice-chat"
    # wake path
    assert _voice_session_id(cfg) == "voice-chat"
    # a named voice id is never rewritten by the alias mapper
    assert resolve_session_id(cfg, "voice-chat") == "voice-chat"
    # and the /qubit-chat web default is deliberately DISTINCT (isolation)
    assert CT.DEFAULT_SESSION == "dashboard-chat"
    assert CT.DEFAULT_SESSION != VS.SINGLE_SESSION_ID
    # the module the voice-edit routes resolve is the same authority
    assert VE is not None
