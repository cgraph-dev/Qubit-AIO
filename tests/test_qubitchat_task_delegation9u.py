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


# --- T5 follow-up: user-confirmed headless builds complete with files. ---
#
# Proven live failure: the worker proposes local.files_upload, the broker
# (source="model") answers CONFIRMATION_SOURCE_FORBIDDEN, nothing is
# written — correctly. But launch/wait plumbed no user confirmation, so a
# confirmed-in-chat build could never complete. These tests pin the grant
# flow: needs-confirm surfaces the blocked proposal, the chat turn stages
# it (CONFIRM_REQUIRED + confirmation_id, same pattern as _turn_registry),
# and the bare affirmation resumes that exact call with a user-issued grant
# (source="user" + staged id) through the one broker path.


def _needs_confirm_wait(*, tool="local.files_upload", code=None):
    args = {"target_path": "hello.html",
            "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()}
    return {"status": "needs-confirm", "code": "TASK_CONFIRM_REQUIRED",
            "detail": "worker proposed a file write needing confirmation",
            "pending": {"tool": tool, "args": args,
                        "code": code or "CONFIRMATION_SOURCE_FORBIDDEN"},
            "session": "worker-9u-t5"}


def _tmp_journal(monkeypatch, tmp_path):
    """Point the chat task-confirm journal at a hermetic tmp file."""
    from voicebridge import chat_transport as CT
    from voicebridge.approvals import ApprovalJournal

    journal = ApprovalJournal(tmp_path / "approvals.jsonl",
                              default_ttl_s=120.0)
    monkeypatch.setattr(CT, "_task_journal", lambda cfg: journal)
    return journal


def test_backend_grant_executes_as_user_with_id(monkeypatch):
    """With a staged grant the same backend carries source=user + id; without
    it the file call stays model-origin (confirm-gated)."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    envelope = ('{"ok": true, "receipt": '
                '{"target": "hello.html", "bytes_after": 18}}')
    calls = _fake_broker_host(
        monkeypatch, _BK.ExecResult(ok=True, output=envelope))
    args = {"target_path": "hello.html",
            "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()}
    run = ORC._task_tool_backend(cfg=None)
    run("local.files_upload", dict(args))
    assert calls[0][2] == "model", calls
    granted = ORC._task_tool_backend(
        cfg=None, grant={"confirmation_id": "stage-9u-1"})
    out = granted("local.files_upload", dict(args))
    assert "hello.html" in out, out


def test_backend_grant_source_and_id_reach_broker(monkeypatch):
    """The granted call presents source=user with the staged id (one broker
    path, digest-bound single-use record consumed there)."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    seen = []

    class _GrantBroker:
        def execute(self, capability, args, **kw):
            seen.append((capability, kw.get("source"),
                         kw.get("confirmation_id")))
            return _BK.ExecResult(
                ok=True, output='{"ok": true, "receipt": '
                                '{"target": "hello.html"}}')

    monkeypatch.setattr(
        _BK, "build_host_broker", lambda *a, **kw: _GrantBroker())
    monkeypatch.setattr(
        _BK, "register_web_tools", lambda broker, cfg=None: ())
    monkeypatch.setattr(
        _BK, "register_file_patch", lambda broker, cfg=None: ())
    run = ORC._task_tool_backend(
        cfg=None, grant={"confirmation_id": "stage-9u-2"})
    run("local.files_upload",
        {"target_path": "hello.html",
         "content_base64": _b64mod.b64encode(b"x").decode()})
    assert seen == [("local.files_upload", "user", "stage-9u-2")], seen


def test_backend_blocked_proposal_recorded_in_memory(monkeypatch):
    """A confirmation-gated block records the exact proposal on the backend
    (in memory — full args never logged) and still raises DispatchBlocked
    so the worker records an honest tool failure."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC
    from voicebridge.dispatch_guard import DispatchBlocked

    _fake_broker_host(monkeypatch, _BK.ExecResult(
        ok=False, denied="CONFIRMATION_SOURCE_FORBIDDEN"))
    run = ORC._task_tool_backend(cfg=None)
    args = {"target_path": "hello.html",
            "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()}
    try:
        run("local.files_upload", dict(args))
    except DispatchBlocked as exc:
        assert "CONFIRMATION_SOURCE_FORBIDDEN" in str(exc.code), exc.code
    else:
        raise AssertionError("gated model write must be blocked")
    assert len(run.pending) == 1, run.pending
    assert run.pending[0]["tool"] == "local.files_upload", run.pending
    assert run.pending[0]["args"] == args, "exact args kept for the grant"


def test_wait_task_needs_confirm_surfaces_pending(monkeypatch):
    """Real launch/wait where the worker's file call is gated: wait returns
    needs-confirm with the proposal instead of a dead-end error."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC
    from voicebridge.config import Config

    _fake_broker_host(monkeypatch, _BK.ExecResult(
        ok=False, denied="CONFIRMATION_SOURCE_FORBIDDEN"))

    def _gated_session(cfg, text, **kw):
        try:
            kw["execute_tool"](
                "local.files_upload",
                {"target_path": "hello.html",
                 "content_base64": _b64mod.b64encode(
                     b"<h1>hi</h1>").decode()})
        except Exception as exc:
            return {"state": "FAILED", "code": "TOOL_BLOCKED",
                    "detail": str(exc)[:100]}
        raise AssertionError("gated write must not succeed")

    monkeypatch.setattr(
        "voicebridge.orchestrator._agent_run_session", _gated_session)
    cfg = Config.load()
    key = ORC.launch_task("make a one-page website that says hello",
                          session="test-needsconfirm-9u", cfg=cfg)
    res = ORC.wait_task(key, timeout_s=30)
    assert res.get("status") == "needs-confirm", res
    assert res.get("code") == "TASK_CONFIRM_REQUIRED", res
    assert res["pending"]["tool"] == "local.files_upload", res
    assert res["pending"]["args"]["target_path"] == "hello.html", res


def test_task_execute_granted_ok_and_denied(monkeypatch):
    """The settle primitive runs the exact call as user+id (ok names the
    receipt) and maps a broker denial to a typed code — never phantom."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    monkeypatch.setattr(_BK, "estop_engaged", lambda cfg=None: False)
    envelope = ('{"ok": true, "receipt": '
                '{"target": "hello.html", "bytes_after": 18}}')
    _fake_broker_host(monkeypatch, _BK.ExecResult(ok=True, output=envelope))
    args = {"target_path": "hello.html",
            "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()}
    out = ORC.task_execute_granted({}, "local.files_upload", dict(args),
                                   "stage-9u-3")
    assert out.get("ok") is True, out
    assert "hello.html" in out.get("output", ""), out


def test_task_execute_granted_denied_is_typed(monkeypatch):
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    monkeypatch.setattr(_BK, "estop_engaged", lambda cfg=None: False)
    _fake_broker_host(monkeypatch, _BK.ExecResult(
        ok=False, denied="EXPIRED_CONFIRMATION"))
    out = ORC.task_execute_granted(
        {}, "local.files_upload", {"target_path": "hello.html",
                                   "content_base64": _b64mod.b64encode(
                                       b"x").decode()},
        "stage-9u-4")
    assert out.get("ok") is False, out
    assert out.get("code") == "EXPIRED_CONFIRMATION", out


def test_task_execute_granted_outside_root_stays_refused(monkeypatch):
    """Path confinement is never granted away: an outside-root envelope
    stays a typed refusal even with a valid grant."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    monkeypatch.setattr(_BK, "estop_engaged", lambda cfg=None: False)
    envelope = ('{"ok": false, "code": "FILE_PATCH_OUTSIDE_ROOT", '
                '"detail": "target is outside the approved project folders"}')
    _fake_broker_host(monkeypatch, _BK.ExecResult(ok=True, output=envelope))
    out = ORC.task_execute_granted(
        {}, "local.files_upload", {"target_path": "/tmp/evil.html",
                                   "content_base64": _b64mod.b64encode(
                                       b"x").decode()},
        "stage-9u-5")
    assert out.get("ok") is False, out
    assert out.get("code") == "FILE_PATCH_OUTSIDE_ROOT", out


def test_task_execute_granted_estop_refuses(monkeypatch):
    """A latched e-stop refuses the granted write before any execution."""
    from voicebridge import broker as _BK
    from voicebridge import orchestrator as ORC

    monkeypatch.setattr(_BK, "estop_engaged", lambda cfg=None: True)

    def _boom(*a, **kw):
        raise AssertionError("broker must not run while latched")

    monkeypatch.setattr(_BK, "build_host_broker", _boom)
    out = ORC.task_execute_granted({}, "local.files_upload", {}, "s-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "EMERGENCY_STOP", out


def test_turn_task_needs_confirm_stages_without_executing(monkeypatch,
                                                          tmp_path):
    """Worker-needs-confirm → staged CONFIRM_REQUIRED (no execution, no
    phantom done, no file bytes in the journal)."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    journal = _tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-t5a")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: _needs_confirm_wait())
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-stage-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "CONFIRM_REQUIRED", out
    assert out.get("confirmation_id"), out
    assert out.get("route") == "task", out
    assert "hello.html" in out.get("detail", ""), out
    live = journal.pending()
    assert len(live) == 1, live
    assert live[0].id == out["confirmation_id"], live
    assert live[0].meta.get("session") == "test-task-stage-9u", live[0].meta
    raw = (tmp_path / "approvals.jsonl").read_text()
    assert "aGk=" not in raw and "<h1>" not in raw, \
        "file bytes must never reach the journal"
    transcript = fake.texts("test-task-stage-9u")
    assert "done" not in transcript.lower().replace(
        "i'll report back here.", ""), transcript


def _plant_box(monkeypatch, key, args):
    """Plant an in-memory blocked proposal for the settle path (real box
    map, real journal record — only the broker/model are doubles)."""
    from voicebridge import orchestrator as ORC

    ORC._TASK_BOXES[key] = {
        "event": None, "result": None, "cancel": False,
        "pending": [{"tool": "local.files_upload", "args": dict(args),
                     "code": "CONFIRMATION_SOURCE_FORBIDDEN"}]}


def test_turn_task_affirm_settles_grant_done_with_file(monkeypatch,
                                                       tmp_path):
    """Staged build + bare 'yes' with a mocked grant → done with a file
    receipt naming the file; the grant carried source=user + staged id; a
    second 'yes' settles nothing (typed, no replay)."""
    from voicebridge import broker as _BK
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    _tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-t5b")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: _needs_confirm_wait())
    first = CT.turn({}, "make me an app that tracks expenses",
                    session="test-task-settle-9u")
    cid = first.get("confirmation_id")
    assert cid, first
    task_key = first.get("task")
    assert task_key, first
    args = {"target_path": "hello.html",
            "content_base64": _b64mod.b64encode(b"<h1>hi</h1>").decode()}
    _plant_box(monkeypatch, task_key, args)
    monkeypatch.setattr(_BK, "estop_engaged", lambda cfg=None: False)
    seen = []

    class _GrantBroker:
        def execute(self, capability, args, **kw):
            seen.append((capability, kw.get("source"),
                         kw.get("confirmation_id")))
            return _BK.ExecResult(
                ok=True, output='{"ok": true, "receipt": '
                                '{"target": "hello.html", '
                                '"bytes_after": 18}}')

    monkeypatch.setattr(
        _BK, "build_host_broker", lambda *a, **kw: _GrantBroker())
    monkeypatch.setattr(
        _BK, "register_web_tools", lambda broker, cfg=None: ())
    monkeypatch.setattr(
        _BK, "register_file_patch", lambda broker, cfg=None: ())
    try:
        out = CT.turn({}, "yes", session="test-task-settle-9u")
        assert out.get("ok") is True, out
        assert out.get("status") == "done", out
        assert out.get("route") == "task", out
        assert "hello.html" in str(out.get("report", "")), out
        assert seen == [("local.files_upload", "user", cid)], seen
        # Settle-once: the journal record left IN_PROGRESS->EXECUTING, so a
        # second affirmation finds nothing pending and executes nothing.
        again = CT.turn({}, "yes", session="test-task-settle-9u")
        assert again.get("ok") is False, again
        assert len(seen) == 1, seen
    finally:
        ORC._TASK_BOXES.pop(task_key, None)


def test_turn_task_affirm_denied_is_typed(monkeypatch, tmp_path):
    """A denied staged write settles typed — the grant never executes.

    The denied record has already left pending(), so the settle race is
    fed directly: the journal still refuses the approve and the turn maps
    it typed (same for an aborted/expired record)."""
    from voicebridge import broker as _BK
    from voicebridge import chat_transport as CT

    for decision, code in (("deny", "TASK_CONFIRM_DENIED"),
                           ("abort", "TASK_CONFIRM_EXPIRED")):
        fake = _FakeStore()
        _hermetic(monkeypatch, fake)
        _agent_task_decision(monkeypatch)
        journal = _tmp_journal(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "voicebridge.orchestrator.launch_task",
            lambda task, session=None, **kw: "worker-9u-t5c")
        monkeypatch.setattr(
            "voicebridge.orchestrator.wait_task",
            lambda key, timeout_s=300, **kw: _needs_confirm_wait())
        sid = f"test-task-deny-9u-{decision}"
        first = CT.turn({}, "make me an app that tracks expenses",
                        session=sid)
        cid = first.get("confirmation_id")
        assert cid, first
        if decision == "deny":
            journal.respond(cid, "deny")
        else:
            journal.abort(cid)
        # Settle race: the record left pending() but the affirmation still
        # references it — the journal refuses the approve, typed.
        monkeypatch.setattr(
            CT, "_pending_task", lambda cfg, s: [journal.get(cid)])

        def _boom(*a, **kw):
            raise AssertionError("denied grant must never execute")

        monkeypatch.setattr(_BK, "build_host_broker", _boom)
        out = CT.turn({}, "yes", session=sid)
        assert out.get("ok") is False, out
        assert out.get("code") == code, out


def test_turn_task_affirm_lost_box_is_typed(monkeypatch, tmp_path):
    """Staged record but no worker state (evicted/restarted) → typed loss,
    journal cleaned, nothing executed."""
    from voicebridge import broker as _BK
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    journal = _tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-t5d")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: _needs_confirm_wait())
    first = CT.turn({}, "make me an app that tracks expenses",
                    session="test-task-lost-9u")
    cid = first.get("confirmation_id")
    assert cid, first

    def _boom(*a, **kw):
        raise AssertionError("lost grant must never execute")

    monkeypatch.setattr(_BK, "build_host_broker", _boom)
    out = CT.turn({}, "yes", session="test-task-lost-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_CONFIRM_LOST", out
    assert journal.get(cid).state == "denied", journal.get(cid)


def test_stream_task_needs_confirm_errors_typed(monkeypatch, tmp_path):
    """Stream variant: needs-confirm ends in a CONFIRM_REQUIRED error event
    carrying the confirmation_id — never done, never silent."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    _tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-t5e")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: _needs_confirm_wait())
    events = list(CT._stream_task({}, fake, "test-task-streamnc-9u",
                                   "make me an app",
                                   __import__("time").monotonic(),
                                   timeout_s=10))
    kinds = [e.get("event") for e in events]
    assert kinds[-1] == "error", kinds
    assert "done" not in kinds, kinds
    assert events[-1].get("code") == "CONFIRM_REQUIRED", events[-1]
    assert events[-1].get("confirmation_id"), events[-1]


def test_progress_chatter_with_filename_is_typed_no_build(monkeypatch):
    """Live phantom (2026-10-01): planner outage, worker replied 'Working
    on hello.html …' with zero tool calls — the filename mention alone
    must not count as a build."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-t5f")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {
            "status": "done",
            "reply": "Working on t5-live-hello-b3.html inside the "
                     "project folder."})

    def _boom(reply, cfg=None):
        raise AssertionError("summarizer must not run on progress chatter")

    monkeypatch.setattr("voicebridge.summarizer.summarize", _boom)
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-progress-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_NO_ACTION", out


def test_progress_with_acted_verb_still_reports_done(monkeypatch):
    """The veto is narrow: progress phrasing PLUS a past-tense acted verb
    stays a report — but a concrete filename still needs receipts (naming
    the file is not creating it), so this receipt-carrying worker counts."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-t5g")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {
            "status": "done",
            "reply": "Working on it — created hello.html with the "
                     "greeting page.",
            "steps": 1, "tool_used": "local.files_upload"})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "Created hello.html.")
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-progress-done-9u")
    assert out.get("ok") is True, out
    assert out.get("status") == "done", out


def test_future_promise_naming_file_is_typed_no_build(monkeypatch):
    """Live phantom (2026-10-01): planner outage, worker replied 'I'll
    create hello.html …' with zero tool calls — naming the file is not
    creating it."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-9u-t5h")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {
            "status": "done",
            "reply": "I'll create t5-live-hello.html in the project "
                     "folder. Review before commit."})

    def _boom(reply, cfg=None):
        raise AssertionError("summarizer must not run on a future promise")

    monkeypatch.setattr("voicebridge.summarizer.summarize", _boom)
    out = CT.turn({}, "make me an app that tracks expenses",
                  session="test-task-future-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_NO_BUILD", out


# --- Double-launch guard: one worker per session (t5-live extra-turn hunt).
#
# Audit: every single turn/stream call launches at most once (_turn_task and
# _stream_task each call launch_task exactly once; the affirmative settle
# path executes via task_execute_granted and never launches; retry_last is
# model-only). The double-execution vector is cross-request: two rapid
# turns on the same chat session resolve to the SAME _TASK_BOXES key, so
# the second launch overwrote the first box and orphaned its thread — two
# workers, one box, one unattributable extra build turn. The guard below
# refuses the second launch typed (TASK_INFLIGHT naming the running key);
# a finished box may still be replaced by a fresh sequential launch.


def test_second_launch_while_inflight_is_typed(monkeypatch):
    """Two rapid launches for one session start ONE worker; the second
    refuses typed TASK_INFLIGHT naming the running key. After the first
    finishes, the same session may launch again (same deterministic key)."""
    import threading as _th
    from voicebridge import orchestrator as ORC
    from voicebridge.config import Config

    release = _th.Event()
    started = _th.Event()

    def _blocking_session(cfg, text, **kw):
        started.set()
        assert release.wait(30), "worker never released"
        return {"state": "OK", "reply": "Built hello.html.",
                "steps": 1, "tool_used": "local.files_upload"}

    monkeypatch.setattr(
        "voicebridge.orchestrator._agent_run_session", _blocking_session)
    cfg = Config.load()
    key1 = ORC.launch_task("make a one-page website that says hello",
                          session="test-inflight-9u", cfg=cfg)
    assert key1, key1
    assert started.wait(10), "first worker never started"
    try:
        ORC.launch_task("make a one-page website that says hello",
                       session="test-inflight-9u", cfg=cfg)
    except ORC.TaskLaunchDenied as exc:
        assert exc.code == "TASK_INFLIGHT", exc.code
        assert key1 in exc.detail, exc.detail
    else:
        raise AssertionError("second concurrent launch must be refused")
    finally:
        release.set()
    res = ORC.wait_task(key1, timeout_s=30)
    assert res.get("status") == "done", res
    # Sequential reuse still works: a finished box is replaceable.
    key2 = ORC.launch_task("make another page", session="test-inflight-9u",
                           cfg=cfg)
    assert key2 == key1, (key1, key2)
    res2 = ORC.wait_task(key2, timeout_s=30)
    assert res2.get("status") == "done", res2


def test_turn_while_inflight_returns_typed_without_launching(monkeypatch):
    """A second turn on a session with a running worker gets typed
    TASK_INFLIGHT (UNAVAILABLE, retryable-busy) — the planted box is
    untouched, no duplicate worker starts, no phantom starter row."""
    import threading as _th
    from voicebridge import broker as _BK
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    _agent_task_decision(monkeypatch)
    monkeypatch.setattr(_BK, "estop_engaged", lambda cfg=None: False)
    sid = "test-task-inflight-turn-9u"
    key = ORC._task_session_key({}, sid)
    planted = {"event": _th.Event(), "result": None, "cancel": False,
               "model": "", "pending": []}
    ORC._TASK_BOXES[key] = planted
    before = {t.name for t in _th.enumerate()}
    try:
        out = CT.turn({}, "make me an app that tracks expenses",
                      session=sid)
    finally:
        assert ORC._TASK_BOXES.get(key) is planted, \
            "in-flight box must never be overwritten by a second turn"
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_INFLIGHT", out
    assert out.get("route") == "task", out
    assert out.get("state") == "UNAVAILABLE", out
    assert key in str(out.get("detail", "")), out
    after = {t.name for t in _th.enumerate()} - before
    assert not any(n.startswith("chat-task-") for n in after), after
    assert "Working on it" not in fake.texts(sid), fake.texts(sid)
