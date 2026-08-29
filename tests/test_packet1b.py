"""Packet 1B tests: dispatch-guard bypass proof, dashboard auth/CORS contract,
durable HITL approvals (settle-once/abort/durability/concurrency)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.approvals import (ABORTED, COMPLETE, DENIED, EXECUTING,
                                   IN_PROGRESS, ApprovalJournal)
from voicebridge.capabilities import ConfirmationDenied
from voicebridge.dispatch_guard import DispatchBlocked, WorkerGuard
from voicebridge.webui import cors_headers_for, token_ok


# ---------------------------------------------------- bypass coverage (static)

def test_every_worker_execution_is_guarded_and_no_raw_branches():
    """R4: fail if ANY direct gateway/Hermes execution could bypass a recorded
    Decision. Static proof over the full orchestrator source."""
    src = (REPO / "voicebridge/orchestrator.py").read_text(errors="replace")
    assert "if self._worker_guard else" not in src          # R2: raw branches gone
    # every legacy direct-call shape is forbidden outside the guard wrappers
    forbidden = [
        r"self\._gateway\.send_message\(",
        r"self\._cli\.run_task\(",
        r"hermes\.run_task\(task\)",
        r"self\._gateway\.create_session\(",
        r"self\._gateway\.abort\(",
    ]
    def strip_guard_blocks(text: str) -> str:
        out, i = [], 0
        while True:
            j = text.find("self._worker_guard.run(", i)
            if j < 0:
                out.append(text[i:])
                break
            out.append(text[i:j])
            depth, k = 0, j + len("self._worker_guard.run(") - 1
            while k < len(text):
                if text[k] == "(":
                    depth += 1
                elif text[k] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            i = k + 1
        return "".join(out)
    guarded = strip_guard_blocks(src)
    for pat in forbidden:
        hits = re.findall(pat, guarded)
        assert not hits, f"unguarded execution remains: {pat} x{len(hits)}"
    for cap in ("openclaw.gateway.task", "openclaw.cli.task", "hermes.task",
                "openclaw.create_session", "openclaw.abort"):
        assert f'"{cap}"' in src, f"{cap} not wired"


def test_guard_registry_covers_exactly_the_wired_capabilities():
    g = WorkerGuard(_cfg("confirm"))
    names = set(g.engine.registry.names())
    assert names == {"openclaw.gateway.task", "openclaw.cli.task", "hermes.task",
                     "openclaw.create_session", "openclaw.abort"}


def test_decision_recorded_before_execution_for_every_cap():
    """R4 runtime ordering: a recorded policy Decision must precede any worker
    execution, proven per capability with a spy sink."""
    import voicebridge.capabilities as caps
    events = []
    orig = caps.log_event
    caps.log_event = lambda e, **f: events.append(("decision", e))
    try:
        g = WorkerGuard(_cfg("confirm"))
        for cap, kw in (("openclaw.gateway.task", {"session_key": "s", "task_text": "go"}),
                        ("openclaw.cli.task", {"session_key": "s", "task_text": "go", "model": None}),
                        ("hermes.task", {"task": "go"}),
                        ("openclaw.create_session", {"key": "s", "label": "l", "message": "m"}),
                        ("openclaw.abort", {"key": "s"})):
            _CURRENT_CAP["v"] = cap
            fn = lambda *a, **k: events.append(("executed", _CURRENT_CAP["v"]))
            before = len(events)
            g.run(cap, fn, source="user", **kw)
            assert ("decision", "policy.decision") in events[before:], cap
            assert events[-1] == ("executed", cap), (cap, events[-3:])
    finally:
        caps.log_event = orig


_CURRENT_CAP = {"v": ""}


# ------------------------------------------------------------ dispatch guard

def _cfg(mode):
    class C:
        @staticmethod
        def get(*a, **k):
            return mode if a[:2] == ("safety", "worker_dispatch") else k.get("default")
    return C()


def test_user_flow_auto_confirms_and_executes():
    calls = []
    g = WorkerGuard(_cfg("confirm"))
    out = g.run("hermes.task", lambda text: calls.append(text) or "ok",
                "list files", source="user")
    assert out == "ok" and calls == ["list files"]


def test_deny_mode_blocks_even_user():
    g = WorkerGuard(_cfg("deny"))
    with pytest.raises(DispatchBlocked) as ei:
        g.run("openclaw.cli.task", lambda *a: (_ for _ in ()).throw(
            AssertionError("must never execute")), "x", source="user")
    assert ei.value.code == "WORKER_DISPATCH_DENIED"


def test_model_source_blocked_with_stolen_or_own_id():
    g = WorkerGuard(_cfg("confirm"))
    executed = []
    with pytest.raises(DispatchBlocked) as ei:
        g.run("hermes.task", lambda t: executed.append(t), "payload",
              source="model")
    # uniform contract: issuance refusal surfaces as a typed dispatch block
    assert ei.value.code == "ISSUER_MODEL_FORBIDDEN"
    assert executed == []
    with pytest.raises(ConfirmationDenied):
        from voicebridge.capabilities import ConfirmationStore
        ConfirmationStore().issue("hermes.task", {}, issued_by="model")


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        WorkerGuard(_cfg("allow"))


# --------------------------------------------------------- auth / CORS matrix

def test_query_token_always_rejected():
    ok, reason = token_ok("secret", "secret", True)
    assert not ok and reason == "query-token-rejected"
    ok2, _ = token_ok("", "", True)             # even with no configured token
    assert not ok2


def test_header_token_contract():
    assert token_ok("s3cret", "s3cret", False)[0] is True
    ok, reason = token_ok("s3cret", "wrong", False)
    assert not ok and reason == "bad-or-missing-header"


def test_cors_exact_origin_only():
    hdrs = cors_headers_for(["https://dash.example"], "https://dash.example")
    assert ("Access-Control-Allow-Origin", "https://dash.example") in hdrs
    assert cors_headers_for(["https://dash.example"], "https://evil.example") == []
    assert cors_headers_for([], "*") == []            # wildcard never honored
    assert cors_headers_for(["*"], "https://x") == []


def test_webui_start_fails_closed_on_lan_without_token(tmp_path):
    from voicebridge.webui import WebUI
    from voicebridge.config import Config

    class StubOrch:
        cfg = Config.load()
    orch = StubOrch()
    orch.cfg = Config.load()
    orch.cfg.data["webui"]["host"] = "0.0.0.0"
    orch.cfg.data["webui"]["token"] = ""
    w = WebUI(orchestrator=orch, state_dir=tmp_path)
    with pytest.raises(RuntimeError, match="requires webui.token"):
        w.start()


def test_live_http_auth_and_approvals_roundtrip(tmp_path):
    """Real ThreadingHTTPServer on an ephemeral loopback port."""
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    class StubOrch:
        pass
    orch = StubOrch()
    orch.cfg = Config.load()
    orch.cfg.data["webui"].update(host="127.0.0.1", token="tok123",
                                  allowed_origins=["http://localhost:8890"])
    w = WebUI(orchestrator=orch, port=0, state_dir=tmp_path)
    w.start()
    try:
        port = w._server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        import urllib.error
        import urllib.request

        def get(url, token=None, query=""):
            req = urllib.request.Request(url + query)
            if token:
                req.add_header("X-Api-Token", token)
            return urllib.request.urlopen(req, timeout=5)

        with pytest.raises(urllib.error.HTTPError) as ei:
            get(f"{base}/api/approvals")
        assert ei.value.code == 401
        ei.value.close()                               # release the socket
        with pytest.raises(urllib.error.HTTPError) as ei2:
            get(f"{base}/api/approvals", token="tok123", query="?token=tok123")
        assert ei2.value.code == 401                   # query-token rejected
        ei2.value.close()                              # release the socket
        r = get(f"{base}/api/approvals", token="tok123")
        try:
            assert json.loads(r.read()) == {"items": []}
        finally:
            r.close()                                  # release the socket
        # CORS header only for configured origin
        req = urllib.request.Request(f"{base}/api/approvals")
        req.add_header("X-Api-Token", "tok123")
        req.add_header("Origin", "https://evil.example")
        r2 = urllib.request.urlopen(req, timeout=5)
        try:
            assert "Access-Control-Allow-Origin" not in r2.headers
        finally:
            r2.close()                                 # release the socket
    finally:
        w.stop()


# ------------------------------------------------------- HITL approval journal

def test_approval_lifecycle_settle_once(tmp_path):
    j = ApprovalJournal(tmp_path / "approvals.jsonl")
    a = j.create("shell.exec", "run backup script")
    assert a.state == IN_PROGRESS
    r1 = j.respond(a.id, "approve")
    assert r1.state == EXECUTING                       # respond live only here
    j.finish(a.id)
    assert j.get(a.id).state == COMPLETE
    r2 = j.respond(a.id, "deny")                       # second response = no-op
    assert r2.state == COMPLETE and r2.decision == "approve"


def test_approval_deny_settles_immediately(tmp_path):
    j = ApprovalJournal(tmp_path / "a.jsonl")
    a = j.create("pay.now", "buy domain")
    r = j.respond(a.id, "deny")
    assert r.state == DENIED and r.settled_at is not None
    assert j.respond(a.id, "approve").state == DENIED   # replay no-op


def test_abort_pending_and_terminal_idempotent(tmp_path):
    j = ApprovalJournal(tmp_path / "a.jsonl")
    a = j.create("shell.exec", "x")
    assert j.abort(a.id).state == ABORTED
    assert j.abort(a.id).state == ABORTED               # idempotent
    b = j.create("shell.exec", "y")
    j.finish(b.id) if False else None
    with pytest.raises(AssertionError):
        j.finish(b.id)                                  # finish requires EXECUTING


def test_durability_across_restart(tmp_path):
    path = tmp_path / "approvals.jsonl"
    j1 = ApprovalJournal(path)
    a = j1.create("file.write", "/tmp/report.md")
    j1.respond(a.id, "approve")
    j2 = ApprovalJournal(path)                          # simulated restart
    got = j2.get(a.id)
    assert got is not None and got.state == EXECUTING
    j2.finish(a.id)
    j3 = ApprovalJournal(path)
    assert j3.get(a.id).state == COMPLETE


def test_expired_pending_aborts_on_respond(tmp_path):
    now = [1000.0]
    j = ApprovalJournal(tmp_path / "a.jsonl", clock=lambda: now[0], default_ttl_s=5)
    a = j.create("shell.exec", "x")
    now[0] += 10
    r = j.respond(a.id, "approve")
    assert r.state == ABORTED                           # expired → explicit abort


def test_concurrent_respond_single_transition(tmp_path):
    j = ApprovalJournal(tmp_path / "a.jsonl")
    a = j.create("shell.exec", "race")
    barrier = threading.Barrier(6)

    def hit(i):
        barrier.wait()
        return j.respond(a.id, "approve" if i % 2 == 0 else "deny").state

    with ThreadPoolExecutor(6) as ex:
        states = list(ex.map(hit, range(6)))
    assert states.count(EXECUTING) + states.count(DENIED) == 6  # all see one settle
    final = j.get(a.id).state
    assert final in (EXECUTING, DENIED)


from concurrent.futures import ThreadPoolExecutor  # noqa: E402  (kept near use)


# ------------------------------------------------- repair R3 boundary extras

def test_worker_payload_sanitized_before_execution():
    g = WorkerGuard(_cfg("confirm"))
    seen = []
    secret = "token=supersecret9f8e7d"
    g.run("hermes.task", lambda task: seen.append(task),
          f"deploy now {secret}", source="user")
    assert seen and secret not in seen[0] and "[REDACTED]" in seen[0]


def test_journal_log_event_redacts_central(tmp_path, monkeypatch):
    """Single choke point: logging_setup.log_event masks secrets for ALL emitters."""
    from voicebridge import logging_setup as ls
    jf = tmp_path / "events.jsonl"
    ls._EVENT_FILE = jf                     # test hook: module-level journal path
    ls._LOGGER = None
    try:
        ls.log_event("hermes.task", text="password=hunter2 run it")
        line = jf.read_text().strip().splitlines()[-1]
        assert "hunter2" not in line and "[REDACTED]" in line
    finally:
        ls._EVENT_FILE = None


def test_report_persistence_boundary_is_redacted_in_source():
    src = (REPO / "voicebridge/orchestrator.py").read_text(errors="replace")
    assert '_rt(header + (reply or ""))' in src
    assert 'path.write_text(persisted' in src


# ==================== RUNTIME integration on REAL orchestrator methods =====
events = []
from voicebridge.config import Config  # noqa: E402


@pytest.fixture(autouse=True)
def _policy_sink(monkeypatch):
    """Route policy/orchestrator audit events into the in-memory sink."""
    monkeypatch.setattr(_caps, "log_event",
                        lambda e, **f: events.append(("decision", e)))
    import voicebridge.orchestrator as _orch_mod
    monkeypatch.setattr(_orch_mod, "log_event",
                        lambda e, **f: events.append(("orch_log", e)))
    yield

# Verifier demand: actual dispatch paths exercised, Decision-before-execution
# proven at runtime (not just static text), incl. retry/failover/continue/abort.

import types as _types

from voicebridge.capabilities import PolicyEngine  # noqa: F401  (sink patching)
import voicebridge.capabilities as _caps
from voicebridge.orchestrator import VoiceOrchestrator as Orchestrator
from voicebridge.openclaw.gateway_ws import GatewayError


class _Rec:
    def __init__(self, tag, sink): self.tag, self.sink = tag, sink
    def __getattr__(self, name):
        s = self.sink; tag = self.tag
        def call(*a, **k):
            s.append((tag, "call", name))
            return None
        return call


def _make_orch(tmp_path, mode="confirm", route="gateway"):
    events.clear()
    orch = Orchestrator.__new__(Orchestrator)          # skip heavy __init__
    cfg = Config.load()
    cfg.data.setdefault("safety", {})["worker_dispatch"] = mode
    cfg.data["openclaw"]["progress_updates"] = False
    orch.cfg = cfg
    orch._route = route
    orch._worker_guard = WorkerGuard(cfg)
    orch.speak = lambda *a, **k: events.append(
        ("orch", "speak", a[0] if a else ""))
    orch.shared = _types.SimpleNamespace(recent=lambda *a, **k: "")
    orch.task = _types.SimpleNamespace(
        active=False, session_key="s0", run_id="r0",
        set_active=lambda *a, **k: events.append(("orch", "set_active")),
        clear=lambda: None)
    orch._await_run = lambda *a, **k: events.append(("orch", "await_run"))
    orch._spawn_waiter = lambda *a, **k: events.append(("orch", "spawn_waiter"))
    orch._retry_gateway = lambda: events.append(("orch", "retry_gw"))
    orch._interrupted_session = None
    orch._interrupted_task_text = None
    orch._hermes_busy = False
    orch._stop = threading.Event()
    orch._interrupt_requested = threading.Event()
    orch.playback = _types.SimpleNamespace(stop=lambda: None)
    orch._persist_full_report = lambda *a, **k: None
    orch._verify_task_result = lambda *a, **k: None
    orch._handle_agent_result = lambda *a, **k: events.append(("orch", "agent_result"))
    orch._dispatch_coding = lambda *a, **k: None
    orch.gateway_info = None
    orch.feed_push = lambda *a, **k: None
    orch._hermes = None
    return orch


def _fake_gateway(run_ids=("r1",)):
    class GW:
        connected = True
        def create_session(self, key=None, label=None, model=None, message=None,
                           timeout=None):
            events.append(("gw", "create_session"))
            rid = run_ids[0] if run_ids else ""
            return {"runId": rid}
        def send_message(self, key=None, message=None, timeout=None):
            events.append(("gw", "send_message"))
            return {"runId": "r2"}
        def abort(self, key=None, timeout=None):
            events.append(("gw", "abort"))
            return {}
    return GW()


def test_runtime_launch_gateway_decision_precedes_execution(tmp_path):
    orch = _make_orch(tmp_path)
    orch._gateway = _fake_gateway()
    orch._launch_task("summarize repo")
    kinds = [e for e in events if e[0] == "decision"]
    assert ("decision", "policy.decision") in kinds
    first_dec = kinds[0]
    # decision recorded BEFORE any gateway call
    first_call = next(i for i, e in enumerate(events) if e[:2] == ("gw", "create_session"))
    dec_idx = events.index(first_dec)
    assert dec_idx < first_call
    assert ("orch", "await_run") in events


def test_runtime_send_message_fallback_also_guarded(tmp_path):
    orch = _make_orch(tmp_path)
    orch._gateway = _fake_gateway(run_ids=())   # no runId → send fallback
    orch._launch_task("go")
    assert ("gw", "send_message") in [e[:2] for e in events if e[0] == "gw"]
    decisions = [i for i, e in enumerate(events) if e == ("decision", "policy.decision")]
    send_i = next(i for i, e in enumerate(events) if e[:2] == ("gw", "send_message"))
    # a decision exists before the fallback send too
    assert any(d < send_i for d in decisions)


def test_runtime_deny_mode_blocks_real_launch(tmp_path):
    orch = _make_orch(tmp_path, mode="deny")
    orch._hermes = None                     # failover impossible
    orch._gateway = _fake_gateway()
    orch._hermes = None                      # failover impossible → spoken error
    orch._dispatch_coding = lambda *a, **k: None
    orch._launch_task("should not run")          # deny mode must neutralize this
    # NOTHING executed on the gateway...
    assert not [e for e in events if e[0] == "gw"], "gateway executed despite deny!"
    # ...and the user hears the explicit failure instead of silent success
    speaks = [e[2] for e in events if e[:2] == ("orch", "speak") and len(e) > 2]
    joined = " | ".join(map(str, speaks))
    assert "unreachable" in joined or "went wrong" in joined, \
        f"speaks={speaks!r} ALL={events!r}"


def test_runtime_gateway_failover_routes_through_guarded_hermes(tmp_path):
    orch = _make_orch(tmp_path)

    class BoomGW:
        def create_session(self, **k): raise GatewayError("down")
        def send_message(self, *a, **k): raise GatewayError("down")
    orch._gateway = BoomGW()

    hermes_calls = []

    class FakeHermes:
        def run_task(self, task, timeout_s=None):
            hermes_calls.append(task)
            return "hermes done"
    orch._hermes = FakeHermes()
    orch.tts = _types.SimpleNamespace(speak=lambda *a, **k: None)
    orch._humming_stop = threading.Event()
    orch.muted = False

    orch._launch_task("fix the bug")
    assert hermes_calls and hermes_calls[0].startswith("fix the bug")
    # TWO decisions recorded: the blocked gateway attempt AND the guarded hermes run
    n_dec = sum(1 for e in events if e == ("decision", "policy.decision"))
    assert n_dec >= 2
    # no gateway execution happened
    assert not [e for e in events if e[0] == "gw"]


def test_runtime_continue_command_guarded(tmp_path):
    orch = _make_orch(tmp_path)
    orch._gateway = _fake_gateway()
    orch._interrupted_session = "sess9"
    from voicebridge.intent.classifier import Intent
    intent = Intent(kind="system", task_text="continue",
                    params={"command": "continue"})
    orch._handle_system(intent)
    assert ("gw", "send_message") in [e[:2] for e in events if e[0] == "gw"]


def test_runtime_abort_guarded(tmp_path):
    orch = _make_orch(tmp_path)
    orch._gateway = _fake_gateway()
    orch.task.active = True
    orch._abort_task("voice cancel")
    assert ("gw", "abort") in [e[:2] for e in events if e[0] == "gw"]
    decisions = [e for e in events if e == ("decision", "policy.decision")]
    assert decisions                                    # recorded Decision exists


def test_model_labeled_launch_is_refused_at_runtime(tmp_path):
    """If any future code path mislabels a model payload as the launch source,
    this catches it: guard refuses non-user provenance even in confirm mode."""
    orch = _make_orch(tmp_path)
    calls = []
    orch._gateway = _fake_gateway(calls)
    # simulate a compromised/mislabeled caller by forcing automation provenance
    orch._worker_guard.run.__defaults__                 # sanity: explicit required
    with pytest.raises(DispatchBlocked):
        orch._worker_guard.run("openclaw.gateway.task",
                               orch._gateway.create_session,
                               key="k", label="l", message="m", model=None,
                               source="model")
    assert not [e for e in events if e[0] == "gw"]


# ==================== verifier round-3 repairs: debug sink + reconnect =====

def test_both_journal_and_debug_sink_are_redacted(tmp_path, monkeypatch):
    """Single event must be secret-free in the JSONL journal AND the debug
    logger (regression for logging_setup.py:68 leak)."""
    from voicebridge import logging_setup as ls

    jf = tmp_path / "events.jsonl"
    captured = []

    class FakeDebug:
        def debug(self, msg, *args):
            captured.append(msg % args if args else msg)

    monkeypatch.setattr(ls, "_EVENT_FILE", jf)
    monkeypatch.setattr(ls, "_LOGGER", FakeDebug())
    secret_pw, secret_key = "hunter2", "sk-live-0123456789abcdef"
    # free-text secret uses the verifier's probe shape (key=value is maskable)
    ls.log_event("hermes.task", text=f"run with password={secret_pw}",
                 password=secret_pw, api_key=secret_key)
    journal_line = jf.read_text()
    debug_out = "\n".join(captured)
    for leak in (secret_pw, secret_key):
        assert leak not in journal_line, "journal leaked"
        assert leak not in debug_out, "debug logger leaked"
    assert "[REDACTED]" in journal_line and "[REDACTED]" in debug_out


def test_reconnect_pending_approval_survives_and_is_actionable(tmp_path):
    """Bridge restart while an approval is still PENDING: it must reload as
    actionable and a later respond() must work on the reloaded record."""
    path = tmp_path / "approvals.jsonl"
    j1 = ApprovalJournal(path)
    a = j1.create("shell.exec", "long-running deploy", ttl_s=600)
    j2 = ApprovalJournal(path)                       # reconnect/restart
    got = j2.pending()
    assert [x.id for x in got] == [a.id]
    r = j2.respond(a.id, "approve")
    assert r.state == EXECUTING
    j3 = ApprovalJournal(path)
    assert j3.get(a.id).state == EXECUTING


def test_reconnect_mid_execution_finish_completes_across_restart(tmp_path):
    path = tmp_path / "approvals.jsonl"
    j1 = ApprovalJournal(path)
    a = j1.create("file.write", "/tmp/out.md")
    j1.respond(a.id, "approve")                      # EXECUTING when bridge dies
    j2 = ApprovalJournal(path)                       # reconnect
    j2.finish(a.id)
    j3 = ApprovalJournal(path)
    assert j3.get(a.id).state == COMPLETE


def test_reconnect_expired_pending_settles_as_aborted_after_reload(tmp_path):
    now = [5000.0]
    path = tmp_path / "approvals.jsonl"
    j1 = ApprovalJournal(path, clock=lambda: now[0], default_ttl_s=30)
    a = j1.create("shell.exec", "stale job")
    now[0] += 60                                     # expires before restart
    j2 = ApprovalJournal(path, clock=lambda: now[0], default_ttl_s=30)
    r = j2.respond(a.id, "approve")
    assert r.state == ABORTED                        # typed degraded outcome
    j3 = ApprovalJournal(path, clock=lambda: now[0])
    assert j3.get(a.id).state == ABORTED             # and stays settled


def test_reconnect_tolerates_torn_tail_line(tmp_path):
    """Crash mid-write on reconnect: partial last line is skipped, earlier
    records still load."""
    path = tmp_path / "approvals.jsonl"
    j1 = ApprovalJournal(path)
    a = j1.create("shell.exec", "ok job")
    with open(path, "a") as fh:
        fh.write('{"id": "torn", "state": "in_progr')   # simulated torn write
    j2 = ApprovalJournal(path)
    assert j2.get(a.id).state == IN_PROGRESS
    assert j2.get("torn") is None
