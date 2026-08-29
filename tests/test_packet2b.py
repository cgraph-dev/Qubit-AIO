"""Packet 2B tests: SSE replay (no gaps/dupes), snapshot/delta with stale-base
recovery, restart-safe approvals/cancellation on the 2A contract, concurrent
run isolation, and dashboard-facing endpoints (header-token auth preserved;
JSON polling fallback untouched)."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.events import (EventJournal, EventHub, EventType,
                                decode_sse, make_event)
from voicebridge.events.hub import resync_frame
from voicebridge.events.runs import RunCoordinator, StateTracker


def _hub(tmp_path):
    return EventHub(EventJournal(tmp_path / "typed-events.jsonl", fsync=False))


# ------------------------------------------------------------ replay semantics

def test_reconnect_replays_exact_continuation_no_gap_no_duplicate(tmp_path):
    hub = _hub(tmp_path)
    for i in range(5):
        hub.publish(make_event("STEP_STARTED", step_name=f"s{i}"))
    first = hub.subscribe()
    seen_a = [r["seq"] for r in first.drain(timeout=0.1)]
    hub.unsubscribe(first)

    # events published while the client is DISCONNECTED
    for i in range(5, 8):
        hub.publish(make_event("STEP_STARTED", step_name=f"s{i}"))

    sub = hub.subscribe(last_event_id=seen_a[-1])   # Last-Event-ID: 5
    got = sub.drain(timeout=0.1)
    seqs = [r["seq"] for r in got]
    assert seqs == [6, 7, 8]                        # continuation only
    types = [r["type"] for r in got]
    assert types == ["STEP_STARTED"] * 3
    names = [r["step_name"] for r in got]
    assert names == ["s5", "s6", "s7"]
    hub.unsubscribe(sub)


def test_fresh_subscriber_gets_full_journal_backlog_then_live_events(tmp_path):
    hub = _hub(tmp_path)
    hub.publish(make_event("CUSTOM", name="a", value=1))
    sub = hub.subscribe()                            # no cursor -> full replay
    seeded = sub.drain(timeout=0.1)
    assert [r["seq"] for r in seeded] == [1]
    hub.publish(make_event("CUSTOM", name="b", value=2))
    live = sub.drain(timeout=0.5)
    assert [r["name"] for r in live] == ["b"]        # live delivery after replay
    hub.unsubscribe(sub)


def test_publish_and_subscribe_are_serialized_no_missed_window(tmp_path):
    """A publish that lands between journal scan and registration must be
    delivered exactly once — covered by the shared-lock ordering."""
    hub = _hub(tmp_path)
    for i in range(20):
        hub.publish(make_event("STEP_FINISHED", step_name=str(i)))
    sub = hub.subscribe(last_event_id=10)
    got_now = sub.drain(timeout=0.1)                 # 11..20 from replay
    hub.publish(make_event("STEP_FINISHED", step_name="live"))
    got_live = sub.drain(timeout=0.5)
    all_seqs = [r["seq"] for r in got_now + got_live]
    assert all_seqs == list(range(11, 22))           # contiguous, no dupes
    hub.unsubscribe(sub)


def test_slow_subscriber_overflow_signals_resync_without_data_loss(tmp_path):
    hub = EventHub(EventJournal(tmp_path / "t.jsonl", fsync=False),
                   max_queue=3)
    sub = hub.subscribe()
    sub.drain(timeout=0.1)
    for i in range(6):
        hub.publish(make_event("CUSTOM", name="n", value=i))
    drained = sub.drain(timeout=0.5)
    assert sub.overflow is True                      # oldest dropped
    assert [r["value"]["i"] if isinstance(r["value"], dict) else r["value"]
            for r in drained][-1:] == [5]            # newest survived
    # recovery path: a fresh client (default-capacity hub over the SAME
    # journal) replays everything the slow subscriber dropped
    sub2 = EventHub(EventJournal(tmp_path / "t.jsonl", fsync=False)).subscribe()
    assert [r["seq"] for r in sub2.drain(timeout=0.1)] == [1, 2, 3, 4, 5, 6]
    hub.unsubscribe(sub)
    hub.unsubscribe(sub2)
    assert resync_frame().startswith(": resync")


# ------------------------------------------------- run coordination/terminal

def test_cancel_emits_exactly_one_terminal_even_when_raced_or_repeated(tmp_path):
    hub = _hub(tmp_path)
    coord = RunCoordinator(hub)
    coord.start("r1", thread_id="t1")
    results = []
    def worker():
        results.append(coord.cancel("r1", reason="user"))
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert results.count(True) == 1                  # one winner emitted it
    terminals = [r for r in EventJournal(
        tmp_path / "typed-events.jsonl").read_all()
        if r["type"] == "RUN_CANCELLED"]
    assert len(terminals) == 1
    assert terminals[0]["reason"] == "user"


def test_cancellation_is_restart_safe_across_coordinator_instances(tmp_path):
    path = tmp_path / "typed-events.jsonl"
    hub1 = EventHub(EventJournal(path, fsync=False))
    c1 = RunCoordinator(hub1)
    c1.start("rX", thread_id="t")
    assert c1.cancel("rX") is True
    # process "restart": fresh hub + coordinator over the same journal
    c2 = RunCoordinator(EventHub(EventJournal(path, fsync=False)))
    assert c2.cancel("rX") is False                  # journal says terminal
    recs = EventJournal(path).read_all()
    assert sum(1 for r in recs if r["type"] == "RUN_CANCELLED") == 1


def test_finish_and_fail_respect_single_terminal_rule(tmp_path):
    hub = _hub(tmp_path)
    coord = RunCoordinator(hub)
    coord.start("r2", thread_id="t2")
    assert coord.finish("r2", result={"ok": True}) is not None
    assert coord.fail("r2", message="late failure") is None
    assert coord.cancel("r2") is False
    kinds = [r["type"] for r in EventJournal(
        tmp_path / "typed-events.jsonl").read_all()]
    assert kinds.count("RUN_FINISHED") == 1 and "RUN_ERROR" not in kinds \
        and "RUN_CANCELLED" not in kinds


def test_abort_hook_runs_once_on_winning_transition(tmp_path):
    hub = _hub(tmp_path)
    calls = []
    coord = RunCoordinator(hub, abort_hook=lambda rid: calls.append(rid))
    coord.start("r9", thread_id="t")
    coord.cancel("r9")
    coord.cancel("r9")
    assert calls == ["r9"]


def test_concurrent_runs_do_not_exchange_terminal_events(tmp_path):
    hub = _hub(tmp_path)
    coord = RunCoordinator(hub)
    for rid in ("ra", "rb"):
        coord.start(rid, thread_id="shared-thread")
    coord.cancel("ra")
    coord.finish("rb")
    recs = EventJournal(tmp_path / "typed-events.jsonl").read_all()
    by_run = {}
    for r in recs:
        by_run.setdefault(r.get("run_id"), []).append(r["type"])
    assert by_run["ra"] == ["RUN_STARTED", "RUN_CANCELLED"]
    assert by_run["rb"] == ["RUN_STARTED", "RUN_FINISHED"]


# ------------------------------------------------------- snapshot/delta flow

def test_tracker_emits_snapshot_then_ordered_deltas(tmp_path):
    hub = _hub(tmp_path)
    tr = StateTracker(hub)
    tr.snapshot("rs", {"progress": 0})
    tr.update("rs", {"progress": 50})
    tr.update("rs", {"progress": 50, "label": "half"})
    recs = EventJournal(tmp_path / "typed-events.jsonl").read_all()
    kinds = [r["type"] for r in recs]
    assert kinds == ["STATE_SNAPSHOT", "STATE_DELTA", "STATE_DELTA"]
    snap = recs[0]["snapshot"]
    state = snap
    from voicebridge.events import apply_patch
    for d in recs[1:]:
        state = apply_patch(state, d["delta"])
    assert state == {"progress": 50, "label": "half"}   # reconstruction works


def test_stale_base_falls_back_to_fresh_snapshot_not_wrong_delta(tmp_path):
    hub = _hub(tmp_path)
    tr = StateTracker(hub)
    tr.snapshot("rx", {"mode": "idle"})
    # simulate tracker memory loss (restart) while journal keeps history:
    tr2 = StateTracker(hub)
    tr2.update("rx", {"mode": "busy"})               # no baseline -> snapshot
    recs = [r for r in EventJournal(
        tmp_path / "typed-events.jsonl").read_all() if r["run_id"] == "rx"]
    assert recs[-1]["type"] == "STATE_SNAPSHOT"
    assert recs[-1]["snapshot"] == {"mode": "busy"}


def test_client_staleness_rule_matches_baseline_seq(tmp_path):
    hub = _hub(tmp_path)
    tr = StateTracker(hub)
    tr.snapshot("rz", {"a": 1})
    base_seq = tr.baseline_seq("rz")
    assert base_seq is not None
    assert StateTracker.client_is_stale(base_seq - 1, base_seq) is True
    assert StateTracker.client_is_stale(base_seq, base_seq) is False
    assert StateTracker.client_is_stale(None, base_seq) is True
    assert StateTracker.client_is_stale(None, None) is True


# --------------------------------------------------- approval event bridging

def test_approval_transitions_mirror_into_typed_stream(tmp_path):
    from voicebridge.approvals import ApprovalJournal, COMPLETE
    typed_path = tmp_path / "typed.jsonl"
    hub = EventHub(EventJournal(typed_path, fsync=False))
    sink_events = []

    def sink(name, item):
        ev = hub.publish(make_event("CUSTOM", name=name,
                                    value={"approval_id": item.id,
                                           "capability": item.capability,
                                           "state": item.state,
                                           "decision": item.decision}))
        sink_events.append((name, ev["seq"]))

    aj = ApprovalJournal(tmp_path / "approvals.jsonl", event_sink=sink)
    a = aj.create("files.write", "edit notes.txt")
    aj.respond(a.id, "approve")
    aj.finish(a.id)
    aj.respond(a.id, "deny")                          # settle-once: no event

    assert [n for n, _ in sink_events] == ["approval.required",
                                           "approval.settled",
                                           "approval.finished"]
    stream = [json.loads(frame.split("data: ", 1)[1].strip())
              for frame in [EventHub.wire_frame(r) for r in
                            EventJournal(typed_path).read_all()]]
    assert [e["name"] for e in stream] == ["approval.required",
                                           "approval.settled",
                                           "approval.finished"]
    assert stream[1]["value"]["decision"] == "approve"


def test_durability_does_not_depend_on_sink_failure(tmp_path):
    from voicebridge.approvals import ApprovalJournal
    def bad_sink(name, item):
        raise RuntimeError("stream down")
    aj = ApprovalJournal(tmp_path / "ap.jsonl", event_sink=bad_sink)
    a = aj.create("x.y", "s")
    rec = aj.respond(a.id, "deny")
    assert rec.state == "denied"                      # durable despite sink
    reopened = ApprovalJournal(tmp_path / "ap.jsonl")
    assert reopened.get(a.id).state == "denied"


# ------------------------------------------------------ HTTP surface (webui)

@pytest.fixture()
def sse_server(tmp_path):
    """WebUI instance on an ephemeral port with token auth enabled."""
    class _Cfg:
        def get(self, *a, **k):
            return {"token": "sekrit", "host": "127.0.0.1"}.get(a[-1]) if a else None
        def section(self, name):
            return {}
    class _Task:
        active = False
        run_id = None
    class _Orch:
        cfg = _Cfg()
        task = _Task()
    from voicebridge.webui import WebUI
    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=tmp_path)
    ui.api_token = "sekrit"
    srv_thread = threading.Thread(target=ui.start, daemon=True)
    srv_thread.start()
    for _ in range(50):
        if ui._server is not None:
            break
        time.sleep(0.05)
    yield ui
    ui.stop()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_sse_stream_requires_header_token_and_serves_frames(sse_server, tmp_path):
    ui = sse_server
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    status, _, body = _get(base + "/api/events/stream")
    assert status == 401                              # fail closed, no token
    status, hdrs, _ = _get(base + "/api/events/stream?token=sekrit")
    assert status == 401                              # query tokens forbidden
    ui.runs.start("http-run", thread_id="web")
    req = urllib.request.Request(base + "/api/events/stream?last_seq=0&run_id=http-run",
                                 headers={"X-Api-Token": "sekrit"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        ctype = resp.headers.get("Content-Type", "")
        assert ctype.startswith("text/event-stream")
        deadline = time.time() + 5
        buf = b""
        while b"RUN_STARTED" not in buf and time.time() < deadline:
            chunk = resp.read1(4096)
            if not chunk:
                break
            buf += chunk
    frames = decode_sse(buf.decode())
    run_ev = [f for f in frames if f.get("type") == "RUN_STARTED"]
    assert run_ev and run_ev[0]["threadId"] == "web"  # AG-UI wire shape live


def test_cancel_endpoint_emits_one_terminal_via_http(sse_server, tmp_path):
    ui = sse_server
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    ui.runs.start("cancel-me", thread_id="web")
    def post(url):
        req = urllib.request.Request(url, method="POST",
                                     headers={"X-Api-Token": "sekrit",
                                              "Content-Type": "application/json"},
                                     data=b'{"reason":"user"}')
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    r1 = post(f"{base}/api/runs/cancel-me/cancel")
    r2 = post(f"{base}/api/runs/cancel-me/cancel")
    assert r1["terminal_emitted"] is True
    assert r2["terminal_emitted"] is False            # idempotent over HTTP
    terms = [r for r in EventJournal(
        Path(ui._events_hub._journal.path)).read_all()
        if r["type"] == "RUN_CANCELLED"]
    assert len(terms) == 1


def test_polling_fallback_endpoints_untouched(sse_server):
    ui = sse_server
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    status, _, body = _get(base + "/api/status",
                           {"X-Api-Token": "sekrit"})
    assert status == 200 and json.loads(body)["bridge"] == "up"


# ------------------------- repair #4: REAL orchestrator lifecycle emission --

def _real_orchestrator_skeleton(webui):
    """Real Orchestrator instance (no __init__: no audio/model side effects)
    carrying exactly the attributes the lifecycle hooks touch."""
    from voicebridge.orchestrator import VoiceOrchestrator as Orchestrator
    from voicebridge.intent.state import TaskState
    orch = Orchestrator.__new__(Orchestrator)
    orch.task = TaskState()
    orch.task.set_active("sess-1", "gw-run-77", "reorganize downloads")
    orch._webui = webui
    orch._route = ""            # _abort_task skips gateway branch
    orch._gateway = None
    orch._worker_guard = None
    return orch


def test_orchestrator_lifecycle_hooks_emit_typed_events(tmp_path):
    """The REAL orchestrator methods publish through RunCoordinator /
    StateTracker onto the durable journal (repair #2/#4)."""
    from voicebridge.webui import WebUI

    class _Cfg:
        def get(self, *a, **k):
            return None
    class _Orch:
        cfg = _Cfg()
    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=tmp_path)
    assert ui.runs is not None and ui.state_tracker is not None

    orch = _real_orchestrator_skeleton(ui)

    # started: gateway dispatch path hook (run_id present -> emitted)
    orch._notify_typed_task("started")
    recs = EventJournal(tmp_path / "typed-events.jsonl").read_all()
    assert [r["type"] for r in recs] == ["RUN_STARTED", "STATE_SNAPSHOT"]
    assert recs[0]["runId"] if False else recs[0]["run_id"] == "gw-run-77"
    assert recs[0]["thread_id"] == "sess-1"
    assert recs[1]["snapshot"]["stage"] == "running"

    # failed: non-"ok" agent result status -> typed degraded failure
    # (fresh tracker baseline for this run => snapshot fallback, not delta)
    orch.task.run_id = "gw-run-78"
    orch.task.session_key = "sess-2"
    orch._notify_typed_task("finished", status="partial", detail="half done")
    recs = EventJournal(tmp_path / "typed-events.jsonl").read_all()
    assert recs[-2]["type"] == "STATE_SNAPSHOT"
    assert recs[-2]["snapshot"]["stage"] == "failed"
    assert recs[-1]["type"] == "RUN_ERROR"
    assert recs[-1]["code"] == "partial" and "half done" in recs[-1]["message"]

    # finished: ok status -> single terminal RUN_FINISHED
    orch.task.run_id = "gw-run-79"
    orch.task.session_key = "sess-3"
    orch._notify_typed_task("finished", status="ok", detail="all good")
    recs = EventJournal(tmp_path / "typed-events.jsonl").read_all()
    assert recs[-1]["type"] == "RUN_FINISHED"

    # cancelled: REAL _abort_task emits exactly one terminal cancel
    orch.task.set_active("sess-4", "gw-run-80", "big task")
    orch._abort_task("user stop")
    assert orch.task.active is False                     # real clear() ran
    recs = EventJournal(tmp_path / "typed-events.jsonl").read_all()
    cancels = [r for r in recs if r["type"] == "RUN_CANCELLED"]
    assert len(cancels) == 1 and cancels[0]["run_id"] == "gw-run-80"
    # second abort on a cleared task is a no-op (guard clause) — still one
    orch.task.set_active("sess-4", "gw-run-80", "big task")
    orch._abort_task("user stop again")
    recs = EventJournal(tmp_path / "typed-events.jsonl").read_all()
    assert len([r for r in recs if r["type"] == "RUN_CANCELLED"]) == 1


def test_orchestrator_runidless_paths_emit_nothing(tmp_path):
    """No invented identifiers: CLI-path tasks (empty run_id) stay silent."""
    from voicebridge.webui import WebUI
    class _Cfg:
        def get(self, *a, **k):
            return None
    class _Orch:
        cfg = _Cfg()
    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=tmp_path)
    orch = _real_orchestrator_skeleton(ui)
    orch.task.set_active("cli-sess", "", "cli task")     # empty run_id
    orch._notify_typed_task("started")
    orch._notify_typed_task("finished", status="ok", detail="x")
    assert EventJournal(tmp_path / "typed-events.jsonl").read_all() == []


def test_orchestrator_source_wires_lifecycle_call_sites():
    """Static proof that the hooks sit inside the real dispatch paths."""
    src = (REPO / "voicebridge" / "orchestrator.py").read_text()
    assert 'self._notify_typed_task("started")' in src
    assert 'self._notify_typed_task("finished",' in src
    assert '"ok" = RUN_FINISHED' in src                  # status branch contract
    assert 'typed_task_cancelled' in src                 # abort path bridge
    # ...inside the right methods, not floating at module scope
    def body_of(name):
        m = re.search(rf"def {name}\(.*?\n(.*?)(?=\n    def )", src, re.S)
        return m.group(1)
    assert "_notify_typed_task" in body_of("_handle_agent_result")
    assert "typed_task_cancelled" in body_of("_abort_task")


def test_webui_typed_helpers_reject_without_coordinator(tmp_path, monkeypatch):
    """If stream init failed (hub None), hooks degrade to silent no-ops."""
    from voicebridge.webui import WebUI
    class _Cfg:
        def get(self, *a, **k):
            return None
    class _Orch:
        cfg = _Cfg()
    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=tmp_path)
    monkeypatch.setattr(ui, "runs", None)
    ui.typed_task_started("s", "r")          # must not raise
    ui.typed_task_finished("s", "r", status="ok")
    ui.typed_task_cancelled("s", "r")
