"""Wave C Task 5 — /qubit-chat build tasks delegate to the /chat worker.

Plan: docs/superpowers/plans/2026-09-30-qubit-chat-voice-routing.md Task 5
Step 1. ``chat_transport.turn`` consumes ``orchestrator.launch_task`` /
``wait_task`` + ``summarizer.summarize``; the transcript shows the worker
session key and the Spark report. Hermetic: the store, e-stop, decide (when
not under test), launch/wait, and summarizer are all doubles — no model,
no worker thread, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


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

    def texts(self, sid):
        return " ".join(
            str(m.get("content", "")) for m in self.rows.get(sid, []))


def _hermetic(monkeypatch, fake):
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)


def _agent_task_decision(monkeypatch):
    """Pin the branch deterministically (decide itself is covered by the
    routing suite; here the wiring under test is launch/wait/summarize)."""
    from voicebridge import chat_transport as CT

    decided = CT.decide_turn_action(
        "make me an app that tracks expenses", cfg={})
    assert decided["action"] == "agent_task", decided
    monkeypatch.setattr(
        CT, "decide_turn_action", lambda text, **kw: dict(decided))


def test_agent_task_delegates_and_reports(monkeypatch):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-1")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {"status": "done",
                                          "reply": "Built it."})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "Built it, two files.")
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "task", out
    assert "worker-9u-1" in str(out), out
    assert out.get("report") == "Built it.", out
    assert out.get("text") == "Built it, two files.", out
    transcript = fake.texts("test-task-9u")
    assert "worker-9u-1" in transcript, transcript
    assert "Built it." in transcript, transcript


def test_agent_task_cancelled_records_no_phantom_summary(monkeypatch):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-2")

    def _cancelled(key, timeout_s=300, **kw):
        return {"status": "cancelled", "reply": ""}

    monkeypatch.setattr("voicebridge.orchestrator.wait_task", _cancelled)

    def _boom(reply, cfg=None):
        raise AssertionError("summarizer must not run on a cancelled task")

    monkeypatch.setattr("voicebridge.summarizer.summarize", _boom)
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-cancel-9u")
    assert out.get("ok") is True, out
    assert out.get("status") == "cancelled", out
    assert "cancelled" in fake.texts("test-task-cancel-9u").lower()


def test_agent_task_launch_denied_is_typed(monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)

    def _denied(task, session=None, **kw):
        raise ORC.TaskLaunchDenied("EMERGENCY_STOP", "latched")

    monkeypatch.setattr("voicebridge.orchestrator.launch_task", _denied)
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-deny-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "EMERGENCY_STOP", out
    assert out.get("route") == "task", out


def test_agent_task_worker_error_is_typed(monkeypatch):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-3")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {"status": "error",
                                          "reply": "worker blew up",
                                          "code": "WORKER_ERROR"})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "should never be spoken")
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-err-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "WORKER_ERROR", out
    assert "worker-9u-3" in str(out), out


def test_wait_task_unknown_key_is_typed():
    from voicebridge import orchestrator as ORC

    out = ORC.wait_task("no-such-task-9u", timeout_s=1)
    assert out.get("status") == "unknown", out
    assert out.get("code") == "UNKNOWN_TASK", out


def test_launch_wait_real_chain_with_stubbed_session(monkeypatch):
    """The real launch/wait chain (e-stop + safety + guard + box + wait
    mapping) with only the model call stubbed — no network, no side
    effects, read-only live config."""
    from voicebridge import orchestrator as ORC
    from voicebridge.config import Config

    def _stub_session(cfg, text, **kw):
        assert kw.get("source", None) is None  # source goes to guard.run
        cancelled = kw.get("cancelled")
        assert callable(cancelled) or cancelled is None
        return {"state": "OK", "reply": "stubbed build done"}

    monkeypatch.setattr("voicebridge.orchestrator._agent_run_session", _stub_session)
    cfg = Config.load()
    key = ORC.launch_task("make a test widget", session="test-chain-9u",
                          cfg=cfg)
    assert key, key
    res = ORC.wait_task(key, timeout_s=30)
    assert res.get("status") == "done", res
    assert res.get("reply") == "stubbed build done", res


def test_launch_wait_cancel_flags_worker(monkeypatch):
    from voicebridge import orchestrator as ORC
    from voicebridge.config import Config

    seen = {}

    def _slow_session(cfg, text, **kw):
        import time as _t
        for _ in range(100):
            if kw.get("cancelled") and kw["cancelled"]():
                seen["cancelled"] = True
                return {"state": "CANCELLED", "code": "CANCELLED",
                        "detail": "session cancelled"}
            _t.sleep(0.05)
        return {"state": "OK", "reply": "too slow"}

    monkeypatch.setattr("voicebridge.orchestrator._agent_run_session", _slow_session)
    cfg = Config.load()
    key = ORC.launch_task("a slow test widget", session="test-chaincancel-9u",
                          cfg=cfg)
    res = ORC.wait_task(key, timeout_s=30, cancel=lambda: True)
    assert res.get("status") == "cancelled", res
    import time as _t
    deadline = _t.monotonic() + 5.0
    while not seen.get("cancelled") and _t.monotonic() < deadline:
        _t.sleep(0.05)
    assert seen.get("cancelled") is True, seen


def test_launch_task_requires_cfg():
    from voicebridge import orchestrator as ORC

    try:
        ORC.launch_task("do a thing", session="x-9u", cfg=None)
    except ORC.TaskLaunchDenied as exc:
        assert exc.code == "NO_CONFIG", exc.code
    else:
        raise AssertionError("launch_task(cfg=None) must refuse fail-closed")


def test_stream_task_progress_then_report(monkeypatch):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-4")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {"status": "done",
                                          "reply": "Streamed build."})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "Streamed build, summarized.")
    events = list(CT._stream_task({}, fake, "test-task-stream-9u",
                                  "make me an app",
                                  __import__("time").monotonic(),
                                  timeout_s=10))
    kinds = [e.get("event") for e in events]
    assert "progress" in kinds, kinds
    assert kinds[-1] == "done", kinds
    assert "worker-9u-4" in fake.texts("test-task-stream-9u")
