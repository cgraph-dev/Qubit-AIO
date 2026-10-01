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
    routing suite; here the wiring under test is launch/wait/summarize).

    Explicit agent delegation ("ask qubit to …") is a pure-regex
    classifier path — a bare "make me an app …" reaches the
    temperature/load-dependent local LLM and flakes under a busy ollama
    (LLM timeout -> heuristic fallback -> not agent_task).
    """
    from voicebridge import chat_transport as CT

    decided = CT.decide_turn_action(
        "ask qubit to make me an app that tracks expenses", cfg={})
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


def test_agent_task_promise_only_is_typed_no_build(monkeypatch):
    """T5: a promise-only worker reply is a typed build failure, never ok done.

    Proven live failure: POST /agent/api/qubit/chat/stream
    {"text": "make a one-page website that says hello"} streamed
    start -> progress -> delta("I will make a one-page website…") ->
    done(ok:true). Zero drafts, zero files. Delegation worked; acceptance
    did not: any non-empty reply counted as "done".
    """
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-5")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {
            "status": "done",
            "reply": "I will make a one-page website that says hello."})

    def _boom(reply, cfg=None):
        raise AssertionError("summarizer must not run on a promise-only reply")

    monkeypatch.setattr("voicebridge.summarizer.summarize", _boom)
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-promise-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_NO_BUILD", out
    assert out.get("route") == "task", out
    assert out.get("status") != "done", out
    transcript = fake.texts("test-task-promise-9u")
    assert "I will make a one-page website" in transcript, transcript


def test_agent_task_empty_reply_is_typed_no_action(monkeypatch):
    """T5: a worker that finishes with no reply is TASK_NO_ACTION, not done."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-6")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {"status": "done", "reply": "   "})

    def _boom(reply, cfg=None):
        raise AssertionError("summarizer must not run on an empty reply")

    monkeypatch.setattr("voicebridge.summarizer.summarize", _boom)
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-empty-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_NO_ACTION", out
    assert out.get("route") == "task", out


def test_agent_task_tool_receipts_report_done(monkeypatch):
    """T5: a worker with tool/file receipts reports done with the report."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-7")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {
            "status": "done",
            "reply": "Created hello.html with the greeting page.",
            "steps": 1, "tool_used": "local.files_upload"})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "Created hello.html.")
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-receipts-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "task", out
    assert out.get("status") == "done", out
    assert out.get("report") == \
        "Created hello.html with the greeting page.", out
    assert out.get("text") == "Created hello.html.", out


def test_stream_task_promise_only_errors_never_done(monkeypatch):
    """T5 (stream): promise-only worker output ends in error, never done."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-8")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {
            "status": "done",
            "reply": "I will make a one-page website that says hello."})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "must never be spoken")
    events = list(CT._stream_task({}, fake, "test-task-streampromise-9u",
                                  "make me an app",
                                  __import__("time").monotonic(),
                                  timeout_s=10))
    kinds = [e.get("event") for e in events]
    assert "error" in kinds, kinds
    assert kinds[-1] == "error", kinds
    assert "done" not in kinds, kinds
    assert events[-1].get("code") == "TASK_NO_BUILD", events[-1]


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


# --- T5 follow-up: headless build worker creates files via the brokered,
# path-confined ``local.files_upload`` op (never an invented writer). ---

import base64 as _b64mod


def _fake_broker_host(monkeypatch, result):
    """Swap the broker factory behind _task_tool_backend for a double."""
    from voicebridge import broker as _BK

    calls = []

    class _FakeBroker:
        def execute(self, capability, args, **kw):
            calls.append((capability, dict(args), kw.get("source")))
            return result

    monkeypatch.setattr(
        _BK, "build_host_broker",
        lambda *a, **kw: _FakeBroker())
    monkeypatch.setattr(
        _BK, "register_web_tools", lambda broker, cfg=None: ())
    monkeypatch.setattr(
        _BK, "register_file_patch", lambda broker, cfg=None: ())
    return calls


def test_task_backend_file_upload_receipt_names_file(monkeypatch):
    """The backend accepts the planner file-build tool and the receipt
    names the created file."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    envelope = ('{"ok": true, "receipt": '
                '{"target": "hello.html", "bytes_after": 18}}')
    calls = _fake_broker_host(
        monkeypatch, _BK.ExecResult(ok=True, output=envelope))
    run = ORC._task_tool_backend(cfg=None)
    out = run("local.files_upload",
              {"target_path": "hello.html",
               "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()})
    assert "hello.html" in out, out
    assert calls and calls[0][0] == "local.files_upload", calls
    assert calls[0][2] == "model", calls  # TRUE origin: stays gated


def test_task_backend_outside_root_is_typed_refusal(monkeypatch):
    """A refused file write (outside-root envelope) raises typed — it can
    never report a phantom file."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    envelope = ('{"ok": false, "code": "FILE_PATCH_OUTSIDE_ROOT", '
                '"detail": "target is outside the approved project folders"}')
    _fake_broker_host(monkeypatch, _BK.ExecResult(ok=True, output=envelope))
    run = ORC._task_tool_backend(cfg=None)
    try:
        run("local.files_upload",
            {"target_path": "/tmp/evil.html",
             "content_base64": _b64mod.b64encode(b"x").decode()})
    except RuntimeError as exc:
        assert "FILE_PATCH_OUTSIDE_ROOT" in str(exc), exc
    else:
        raise AssertionError("outside-root write must raise typed")


def test_task_backend_file_upload_stays_confirm_gated(monkeypatch):
    """MUTATING file tools stay confirm-gated on the model path: a broker
    confirmation denial surfaces as DispatchBlocked, never a write."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC
    from voicebridge.dispatch_guard import DispatchBlocked

    _fake_broker_host(
        monkeypatch, _BK.ExecResult(ok=False,
                                    denied="CONFIRMATION_REQUIRED"))
    run = ORC._task_tool_backend(cfg=None)
    try:
        run("local.files_upload",
            {"target_path": "hello.html",
             "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()})
    except DispatchBlocked as exc:
        assert "CONFIRMATION_REQUIRED" in str(exc.code), exc.code
    else:
        raise AssertionError("ungated model write must be blocked")


def test_task_backend_unknown_tool_still_refused(monkeypatch):
    """The wider backend keeps failing closed on undeclared tools."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    _fake_broker_host(monkeypatch, _BK.ExecResult(ok=True, output="x"))
    run = ORC._task_tool_backend(cfg=None)
    for bad in ("local.nope_not_real", "file.write_text", "shell.run"):
        try:
            run(bad, {})
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad} must be refused")


def test_planner_build_tool_only_for_build_tasks():
    """File-build proposals validate for page-build requests only; every
    other request keeps the reversible-only discipline (no-decision)."""
    from voicebridge import planner as PL

    good = {"target_path": "hello.html",
            "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()}
    prop = PL._validate("local.files_upload", dict(good), 0.9,
                        "make a one-page website that says hello")
    assert prop is not None, "build task must propose the file tool"
    assert prop.tool == "local.files_upload", prop
    assert prop.requires_confirmation is True, prop
    # Non-build requests: no file tool, even well-formed.
    assert PL._validate("local.files_upload", dict(good), 0.9,
                        "turn up the volume") is None
    assert PL._validate("local.files_upload", dict(good), 0.9,
                        "make me an app that tracks expenses") is None
    # Malformed build proposals still fail closed.
    assert PL._validate("local.files_upload",
                        {"target_path": "hello.html",
                         "content_base64": "!!!not-base64!!!"},
                        0.9, "make a one-page website") is None
    assert PL._validate("local.files_upload",
                        {"target_path": "hello.html"}, 0.9,
                        "make a one-page website") is None
    # The frame tail alone must not qualify a non-build request.
    assert PL.is_build_task(
        "turn up the volume\n\nHeadless build instructions: website") \
        is False


def test_headless_build_chain_file_tool_done_names_file(monkeypatch):
    """Real launch/wait chain where the worker builds via the brokered
    file tool: done with a report naming the file."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC
    from voicebridge.config import Config

    envelope = ('{"ok": true, "receipt": '
                '{"target": "hello.html", "bytes_after": 18}}')
    _fake_broker_host(monkeypatch, _BK.ExecResult(ok=True, output=envelope))

    def _builder_session(cfg, text, **kw):
        out = kw["execute_tool"](
            "local.files_upload",
            {"target_path": "hello.html",
             "content_base64": _b64mod.b64encode(
                 b"<h1>hello</h1>").decode()})
        assert "hello.html" in out, out
        return {"state": "OK", "reply": "Created hello.html."}

    monkeypatch.setattr(
        "voicebridge.orchestrator._agent_run_session", _builder_session)
    cfg = Config.load()
    key = ORC.launch_task("make a one-page website that says hello",
                          session="test-buildfile-9u", cfg=cfg)
    res = ORC.wait_task(key, timeout_s=30)
    assert res.get("status") == "done", res
    assert "hello.html" in res.get("reply", ""), res
