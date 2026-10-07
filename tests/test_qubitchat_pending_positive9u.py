"""Pending-confirmation positive path + surfaced-kind settle (verifier 2026-10-07).

Locks two contracts on the /qubit-chat confirmation FSM:

* a LIVE staged confirmation surfaces a bounded ``pending_confirmation`` on the
  stream ``start``/``done`` frames AND on session-show, and an unrelated turn in
  the SAME session preserves it (a non-consuming turn never silently clears the
  banner); and
* a bare affirmation settles the SAME record the UI surfaced
  (``_pending_confirmation`` returns the newest staged action across kinds).
  Before the fix the dispatcher always settled a *registry* confirmation first,
  so with an older registry action and a newer build-write staged in one
  session, the banner showed the build write but "yes" ran the registry action.

Hermetic: fake store, no model/agent/worker; the durable confirmation journal is
a real ``ApprovalJournal`` under ``tmp_path`` (the same store production uses).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_RAW_B64 = "aGk="


class _FakeStore:
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
        return {"state": "OK",
                "session": {"id": sid, "message_count": len(msgs)},
                "messages": msgs, "message_count": len(msgs)}

    def meta_get(self, sid):
        return {"meta": {}}


def _journal(monkeypatch, tmp_path):
    """One real durable journal wired as BOTH kinds' authority (no split-brain)."""
    from voicebridge import chat_transport as CT
    from voicebridge import voice_commands as VC
    from voicebridge.approvals import ApprovalJournal

    journal = ApprovalJournal(tmp_path / "approvals.jsonl",
                              default_ttl_s=120.0)
    monkeypatch.setattr(CT, "_task_journal", lambda cfg: journal)
    monkeypatch.setattr(VC, "_journal", lambda cfg: journal)
    return journal


def _wire(monkeypatch, fake):
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda cfg: True)
    monkeypatch.setattr(CT, "_pending_interrupted_turn", lambda cfg, sid: None)
    monkeypatch.setattr(
        CT, "resolve_route",
        lambda cfg, model=None, **kw: {"ok": True, "model": "stub-model",
                                       "kind": "local", "profile_id": None,
                                       "context_tokens": None})
    monkeypatch.setattr(CT, "_decide_branch",
                        lambda text, cfg=None, **kw: {"action": "answer_local",
                                                      "task_text": text})
    monkeypatch.setattr(
        CT, "_agent_chat",
        lambda cfg, st, sid, clean, **kw: {"ok": True, "reply": "sure",
                                           "elapsed_ms": 1,
                                           "receipt": {"runtime": "stub"}})


def _stage_registry(journal, sid, summary="Open a URL: say yes to confirm."):
    return journal.create("voice.browser_open", summary, ttl_s=120,
                          session=sid, issued_by="user")


def _stage_task(CT, cfg, sid, key="task-key"):
    return CT._stage_task_proposal(
        cfg, sid, key,
        {"status": "needs-confirm",
         "pending": {"tool": "local.files_upload",
                     "args": {"target_path": "hello.html",
                              "content_base64": _RAW_B64}},
         "session": "worker-9u"})


def _starts(events):
    return [e for e in events if e.get("event") == "start"]


def _dones(events):
    return [e for e in events if e.get("event") == "done"]


# ---------------------------------------------------------------------------
# positive path: registry pending surfaces on stream start/done + session-show
# ---------------------------------------------------------------------------
def test_registry_pending_surfaces_on_stream_and_show(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as QC

    fake = _FakeStore()
    fake.ensure("sess-reg")
    journal = _journal(monkeypatch, tmp_path)
    _wire(monkeypatch, fake)
    reg = _stage_registry(journal, "sess-reg")

    events = list(CT.stream_turn({}, "what is the capital of France?",
                                 session="sess-reg"))
    assert _starts(events) and _dones(events), events
    start_pc = _starts(events)[0].get("pending_confirmation")
    done_pc = _dones(events)[-1].get("pending_confirmation")
    assert isinstance(start_pc, dict) and start_pc.get("action_summary"), events
    assert start_pc.get("confirmation_id") == reg.id, start_pc
    assert isinstance(done_pc, dict) and done_pc.get("confirmation_id") == reg.id

    # An unrelated turn is non-consuming: the staged action is still live.
    assert CT._pending_confirmation({}, "sess-reg")["confirmation_id"] == reg.id

    # session-show (page reload) carries the same bounded signal.
    status, out = QC.h_chat_session_show({}, {}, {}, {"id": "sess-reg"})
    assert status == 200, out
    shown = out.get("pending_confirmation")
    assert isinstance(shown, dict) and shown.get("confirmation_id") == reg.id
    # The raw file payload never crosses any boundary.
    assert _RAW_B64 not in json.dumps(out), out


# ---------------------------------------------------------------------------
# the surfaced-kind settle (red before the dispatcher fix)
# ---------------------------------------------------------------------------
def _record_settles(monkeypatch):
    from voicebridge import chat_transport as CT

    calls = []
    monkeypatch.setattr(
        CT, "_settle_affirmed",
        lambda cfg, st, sid: (calls.append("registry"),
                              {"ok": True, "text": "settled registry",
                               "reply": "settled registry", "session_id": sid,
                               "route": "registry", "receipt": {}})[1])
    monkeypatch.setattr(
        CT, "_settle_task_affirmed",
        lambda cfg, st, sid: (calls.append("task"),
                              {"ok": True, "text": "settled task",
                               "reply": "settled task", "session_id": sid,
                               "route": "task", "receipt": {}})[1])
    return calls


def test_affirm_settles_the_surfaced_kind_not_registry_first(monkeypatch,
                                                             tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    fake.ensure("sess-mix")
    journal = _journal(monkeypatch, tmp_path)
    _wire(monkeypatch, fake)
    _stage_registry(journal, "sess-mix")          # older
    time.sleep(0.02)                              # strict ordering
    _stage_task(CT, {}, "sess-mix")               # newer

    surfaced = CT._pending_confirmation({}, "sess-mix")
    assert surfaced and surfaced["kind"] == "task", surfaced

    calls = _record_settles(monkeypatch)
    events = list(CT.stream_turn({}, "yes", session="sess-mix"))
    assert _dones(events) and _dones(events)[-1].get("ok") is True, events
    assert calls == ["task"], (calls, surfaced)

    # The non-streaming turn() dispatches the same surfaced kind.
    calls.clear()
    out = CT.turn({}, "yes", session="sess-mix")
    assert out.get("ok") is True, out
    assert calls == ["task"], (calls, surfaced)


def test_affirm_with_only_registry_still_settles_registry(monkeypatch, tmp_path):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    fake.ensure("sess-reg-only")
    journal = _journal(monkeypatch, tmp_path)
    _wire(monkeypatch, fake)
    reg = _stage_registry(journal, "sess-reg-only")

    assert CT._pending_confirmation({}, "sess-reg-only")["kind"] == "registry"
    calls = _record_settles(monkeypatch)
    events = list(CT.stream_turn({}, "yes", session="sess-reg-only"))
    assert _dones(events) and _dones(events)[-1].get("ok") is True, events
    assert calls == ["registry"], calls
    assert reg.id  # staged id is real
