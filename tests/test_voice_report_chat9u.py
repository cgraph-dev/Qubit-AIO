"""Voice capability: "report on /chat" — speak a concise session summary.

Hermetic: the canonical store and the summarizer are stubbed (no DB, no model,
no network). Verifies intent matching, routed-session preference, the bounded
non-raw summary contract, the honest empty answer, graceful summarizer
failure, and that the path never raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import TransformEngine


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
        return {"session_type": "x11"}

    def _tool(self, name):
        return False


class FakeSessionStore:
    """Persistence seam for ``_remember_ask_exchange`` (never touches disk)."""

    def __init__(self):
        self.appended = []

    def ensure(self, *a, **k):
        return {"state": "OK"}

    def append(self, session, role, content, **k):
        self.appended.append((session, role, content))
        return {"state": "OK"}


class FakeReportStore:
    """Records the session id it was asked for and returns fixed rows."""

    def __init__(self, messages=None, *, state="OK"):
        self._messages = list(messages or [])
        self._state = state
        self.seen_sessions = []

    def messages(self, session_id, *, limit=500, offset=0):
        self.seen_sessions.append(session_id)
        if self._state == "MISSING":
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": "no chat session"}
        return {"state": "OK", "session_id": session_id,
                "messages": self._messages,
                "total": len(self._messages), "limit": limit, "offset": offset}


def _cfg():
    return FakeCfg({"voice_edit": {"enabled": True, "routing": "local",
                                   "confirm_before_replace": False}})


def _service(*, report_store, summarizer=None, store_raises=False):
    """Build a service whose report store + summarizer are stubbed.

    ``summarizer`` is the callable used in place of
    ``chat_transport._summarize_report``; None means "raise" (fault case).
    """
    audits = []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(
        _cfg(), desktop=FakeDesktop(),
        engine=TransformEngine(runner=lambda *a, **k: None),
        router=None, audit=audit, estop=lambda cfg: False,
        session_store=FakeSessionStore())
    svc._report_store = report_store  # used by the module patch helper

    import voicebridge.chat_transport as CT

    if store_raises:
        def _boom_store(cfg):
            raise RuntimeError("store unavailable")

        CT.store = _boom_store
    else:
        CT.store = lambda cfg, _s=report_store: _s

    if summarizer is None:
        def _boom_summary(text, cfg):
            raise RuntimeError("summarizer unavailable")

        CT._summarize_report = _boom_summary
    else:
        CT._summarize_report = summarizer
    return svc, audits


@pytest.fixture(autouse=True)
def _restore_transport():
    import voicebridge.chat_transport as CT
    store, summarize = CT.store, CT._summarize_report
    yield
    CT.store, CT._summarize_report = store, summarize


# ---------------------------------------------------------------------------
# intent matching
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "report on my chat",
    "give me a report on the last session",
    "summarize the chat session",
    "summarize our conversation",
    "recap the conversation",
    "what did we do in that session",
    "what happened in that chat?",
])
def test_report_intent_matches(text):
    assert VoiceEditService.is_report_request(text) is True


@pytest.mark.parametrize("text", [
    "what time is it",
    "summarize this document",
    "how are you",
    "open youtube",
    "check my email",
    "what did we do",              # no chat noun -> conservative miss
    "say that again",
    "tell me a joke",
])
def test_ordinary_questions_do_not_match(text):
    assert VoiceEditService.is_report_request(text) is False


# ---------------------------------------------------------------------------
# _report_answer: summary, not raw transcript
# ---------------------------------------------------------------------------
def test_report_reads_session_and_returns_bounded_summary():
    raw = "assistant line that is very long and must never be spoken verbatim " * 6
    rows = [
        {"role": "user", "content": "What's the weather in Bucharest?"},
        {"role": "assistant", "content": raw},
        {"role": "tool", "content": "ignored"},
    ]
    seen = {}

    def summarizer(transcript, cfg):
        seen["transcript"] = transcript
        return "We checked the Bucharest weather and it was sunny."

    store = FakeReportStore(rows)
    svc, audits = _service(report_store=store, summarizer=summarizer)
    out = svc.ask("report on my chat", request_id="r-report", mode="answer")

    assert out["ok"] is True
    assert out["model"] == "skill-report"
    assert out["answer"] == "We checked the Bucharest weather and it was sunny."
    assert raw not in out["answer"]
    # The summarizer saw a compact USER:/ASSISTANT: transcript, tool rows dropped.
    assert "USER:" in seen["transcript"] and "ASSISTANT:" in seen["transcript"]
    assert "ignored" not in seen["transcript"]
    assert any(op == "voice_edit.report_chat" for op, _d in audits)


def test_report_summary_is_word_bounded():
    long_summary = " ".join(f"word{i}" for i in range(200))
    store = FakeReportStore([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}])
    svc, _audits = _service(report_store=store,
                            summarizer=lambda t, c: long_summary)
    out = svc.ask("summarize the chat session", request_id="r-b", mode="answer")
    assert out["ok"] and out["model"] == "skill-report"
    assert len(out["answer"].split()) <= 40


# ---------------------------------------------------------------------------
# empty / missing transcript -> honest answer
# ---------------------------------------------------------------------------
def test_missing_session_answers_honestly():
    svc, audits = _service(report_store=FakeReportStore(state="MISSING"),
                           summarizer=lambda t, c: "should not run")
    out = svc.ask("report on my chat", request_id="r-e", mode="answer")
    assert out["ok"] is True
    assert out["model"] == "skill-report"
    assert "no session transcript" in out["answer"].lower()
    assert any(op == "voice_edit.report_chat" for op, _d in audits)


def test_session_without_dialogue_answers_honestly():
    store = FakeReportStore([{"role": "tool", "content": "tool noise"}])
    svc, _audits = _service(report_store=store,
                            summarizer=lambda t, c: "should not run")
    out = svc.ask("what did we do in that session", request_id="r-e2",
                  mode="answer")
    assert out["ok"] and out["model"] == "skill-report"
    assert "no session transcript" in out["answer"].lower()


# ---------------------------------------------------------------------------
# failures degrade, never raise
# ---------------------------------------------------------------------------
def test_summarizer_failure_degrades_to_short_fallback():
    store = FakeReportStore([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}])
    svc, audits = _service(report_store=store, summarizer=None)  # raises
    out = svc.ask("report on my chat", request_id="r-f", mode="answer")
    assert out["ok"] is True
    assert out["model"] == "skill-report"
    assert out["answer"] == "I couldn't summarize that session right now."
    assert any(op == "voice_edit.report_chat" for op, _d in audits)


def test_empty_summarizer_output_degrades_to_short_fallback():
    store = FakeReportStore([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}])
    svc, _audits = _service(report_store=store,
                            summarizer=lambda t, c: "")
    out = svc.ask("report on my chat", request_id="r-f2", mode="answer")
    assert out["ok"] is True
    assert out["answer"] == "I couldn't summarize that session right now."


def test_store_failure_falls_through_without_raising():
    svc, _audits = _service(report_store=None, store_raises=True)
    assert svc._report_answer("report on my chat",
                              request_id="") == ("", "")

    class FakeRes:
        ok = True
        text = "brain fallback"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    original = GW.run_role
    GW.run_role = lambda prompt, cfg, **kw: FakeRes()
    try:
        out = svc.ask("report on my chat", request_id="", mode="answer")
    finally:
        GW.run_role = original
    assert out["ok"] and out["answer"] == "brain fallback"


# ---------------------------------------------------------------------------
# routed session preferred over the default voice session
# ---------------------------------------------------------------------------
def test_routed_session_is_preferred():
    store = FakeReportStore([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}])
    svc, _audits = _service(report_store=store,
                            summarizer=lambda t, c: "summary")
    rid = svc.begin(session_id="sess-42")["request_id"]
    out = svc.ask("report on my chat", request_id=rid, mode="answer")
    assert out["ok"] and out["model"] == "skill-report"
    assert store.seen_sessions[-1] == "sess-42"


def test_default_voice_session_used_when_unrouted():
    store = FakeReportStore([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}])
    svc, _audits = _service(report_store=store,
                            summarizer=lambda t, c: "summary")
    out = svc.ask("summarize the chat session", request_id="", mode="answer")
    assert out["ok"] and out["model"] == "skill-report"
    assert store.seen_sessions[-1] == "voice-chat"


# ---------------------------------------------------------------------------
# a non-report ask never touches the report path
# ---------------------------------------------------------------------------
def test_non_report_ask_does_not_use_report_store():
    store = FakeReportStore([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}])
    svc, _audits = _service(report_store=store,
                            summarizer=lambda t, c: "summary")

    class FakeRes:
        ok = True
        text = "brain answer"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    original = GW.run_role
    GW.run_role = lambda prompt, cfg, **kw: FakeRes()
    try:
        out = svc.ask("tell me about qubits", request_id="", mode="chat")
    finally:
        GW.run_role = original
    assert out["ok"] and out["answer"] == "brain answer"
    assert store.seen_sessions == []
