"""A general chat turn must reach the client as REAL increments, not one blob.

The SSE contract (`chat/start` -> background pump -> `chat/stream` frames) was
already correct; what was wrong was the turn path behind it. A general
conversation turn runs the tool-capable vendored agent through
``chat_transport._agent_chat`` -> ``agent.run_agent_turn``, and that path
constructed ``AIAgent(...)`` WITHOUT ``stream_delta_callback``. The runtime
therefore had no seam to report increments on, and the stream carried exactly
one ``delta`` holding the whole finished answer — emitted seconds after the turn
started. Measured before the fix: a 30-line answer produced 1 ``token`` event at
12.983 s and ``done`` at 13.382 s.

These tests pin the contract that matters to a person watching the pane: text
arrives AS IT IS PRODUCED (spread over time, in order), a segment boundary
``None`` is never rendered, and a runtime that cannot stream still gets exactly
one truthful delta with ``receipt.streaming == False`` — never a reply split
into invented token events.
"""
from __future__ import annotations

import threading
import time

import pytest

from voicebridge import chat_transport as CT


class _FakeStore:
    def __getattr__(self, _name):
        return lambda *a, **k: {"state": "OK", "session": {"id": "stream-test"}}


class _Cfg:
    def get(self, *a, **k):
        return k.get("default", None)


def _run_turn(monkeypatch, agent_chat):
    """Drive the real ``stream_turn`` generator with a stubbed agent turn."""
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    monkeypatch.setattr(CT, "store", lambda cfg: _FakeStore())
    monkeypatch.setattr(
        CT, "_resolve_session",
        lambda st, session, model=None, cfg=None:
            {"state": "OK", "session": {"id": "stream-test"}})
    monkeypatch.setattr(CT, "stream_preflight",
                        lambda cfg, text, model=None, **kw: (200, None, "stub"))
    monkeypatch.setattr(CT, "_decide_branch",
                        lambda clean, cfg: {"action": "answer_local"})
    monkeypatch.setattr(CT, "_agent_chat", agent_chat)
    return list(CT.stream_turn(_Cfg(), "tell me a story", session="stream-test"))


def test_deltas_are_yielded_as_the_agent_produces_them(monkeypatch):
    """Four increments 0.4 s apart must produce four deltas spread over time."""
    pieces = ["The ", "lighthouse ", "stood ", "alone."]

    def fake_agent_chat(cfg, st, sid, clean, *, timeout_s=None, cancel=None,
                        reasoning=None, stream_delta=None):
        assert stream_delta is not None, (
            "stream_delta was not threaded into the agent turn")
        for piece in pieces:
            stream_delta(piece)
            time.sleep(0.4)
        return {"ok": True, "reply": "".join(pieces), "elapsed_ms": 1600,
                "receipt": {"path": "stub"}}

    events = _run_turn(monkeypatch, fake_agent_chat)

    deltas = [e for e in events if e.get("event") == "delta"]
    assert [d["text"] for d in deltas] == pieces
    assert len(deltas) > 1, (
        "the turn was collapsed into one delta again — the client cannot "
        "render text that only exists once the whole answer is finished")

    done = [e for e in events if e.get("event") == "done"][-1]
    assert done["reply"] == "".join(pieces)
    assert done["receipt"]["streaming"] is True, (
        "receipt.streaming must be measured from what actually streamed")
    # The start frame opens the turn, and it opens it as streaming.
    assert events[0]["event"] == "start"
    assert events[0]["streaming"] is True


def test_a_segment_boundary_is_never_rendered(monkeypatch):
    """``stream_delta(None)`` marks a boundary, not text."""
    def fake_agent_chat(cfg, st, sid, clean, *, timeout_s=None, cancel=None,
                        reasoning=None, stream_delta=None):
        stream_delta("before the tool")
        stream_delta(None)          # segment boundary before tool lines
        stream_delta("after the tool")
        return {"ok": True, "reply": "before the toolafter the tool",
                "elapsed_ms": 10, "receipt": {}}

    events = _run_turn(monkeypatch, fake_agent_chat)

    texts = [e["text"] for e in events if e.get("event") == "delta"]
    assert texts == ["before the tool", "after the tool"]
    assert all(t is not None for t in texts)


def test_a_runtime_that_cannot_stream_sends_one_truthful_delta(monkeypatch):
    """No increments -> ONE delta, and the receipt says so honestly."""
    def fake_agent_chat(cfg, st, sid, clean, *, timeout_s=None, cancel=None,
                        reasoning=None, stream_delta=None):
        return {"ok": True, "reply": "one whole answer", "elapsed_ms": 10,
                "receipt": {"path": "stub"}}

    events = _run_turn(monkeypatch, fake_agent_chat)

    deltas = [e for e in events if e.get("event") == "delta"]
    assert len(deltas) == 1
    assert deltas[0]["text"] == "one whole answer"
    done = [e for e in events if e.get("event") == "done"][-1]
    assert done["receipt"]["streaming"] is False, (
        "a non-streaming runtime must never be reported as streaming")


def test_a_failed_turn_is_a_typed_error_not_a_done(monkeypatch):
    def fake_agent_chat(cfg, st, sid, clean, *, timeout_s=None, cancel=None,
                        reasoning=None, stream_delta=None):
        return {"ok": False, "code": "MODEL_ERROR", "detail": "provider down"}

    events = _run_turn(monkeypatch, fake_agent_chat)

    assert [e for e in events if e.get("event") == "done"] == []
    err = [e for e in events if e.get("event") == "error"][-1]
    assert err["code"] == "MODEL_ERROR"


def test_the_turn_does_not_block_the_reader_before_its_first_delta(monkeypatch):
    """The generator yields the start frame before the agent finishes.

    A turn that only produced text at completion is the bug; this asserts the
    reader is not starved until the agent returns.
    """
    release = threading.Event()

    def fake_agent_chat(cfg, st, sid, clean, *, timeout_s=None, cancel=None,
                        reasoning=None, stream_delta=None):
        stream_delta("first")
        release.wait(5)
        return {"ok": True, "reply": "firstlast", "elapsed_ms": 5000,
                "receipt": {}}

    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    monkeypatch.setattr(CT, "store", lambda cfg: _FakeStore())
    monkeypatch.setattr(
        CT, "_resolve_session",
        lambda st, session, model=None, cfg=None:
            {"state": "OK", "session": {"id": "stream-test"}})
    monkeypatch.setattr(CT, "stream_preflight",
                        lambda cfg, text, model=None, **kw: (200, None, "stub"))
    monkeypatch.setattr(CT, "_decide_branch",
                        lambda clean, cfg: {"action": "answer_local"})
    monkeypatch.setattr(CT, "_agent_chat", fake_agent_chat)

    gen = CT.stream_turn(_Cfg(), "hi", session="stream-test")
    first = next(gen)
    assert first["event"] in ("start", "delta"), (
        "the first frame only arrived after the whole turn finished")
    assert first["event"] == "start"
    release.set()
    rest = list(gen)
    assert any(e.get("event") == "done" for e in rest)
