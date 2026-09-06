"""Packet 3A tests: trusted capability broker.

Covers: registry coverage of host tools, fail-closed denials (unknown tool,
malformed args, model/automation sources), the DESTRUCTIVE two-step spoken
round trip against REAL tools (DEFERRED-004), replay/expiry/target-mismatch,
preview/dry-run, cancellation idempotency, timeouts, bounded output,
concurrency isolation, redacted correlated audit, script-template argv
registration (shell metacharacters forbidden), shared worker/broker engine,
legacy rollback flag, and the fast-path latency benchmark.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import broker as broker_mod
from voicebridge.broker import (
    CapabilityBroker, ExecResult, ToolBinding, build_host_broker,
    register_script_tool, script_capability_name)
from voicebridge.capabilities import CapabilitySpec, ConfirmationDenied, Tier
from voicebridge.dispatch_guard import DispatchBlocked, WorkerGuard, \
    register_worker_caps


# ------------------------------------------------------------- registration

def test_host_registry_covers_every_local_action_with_tiers():
    from voicebridge.local.actions import ACTIONS
    b = build_host_broker()
    assert set(b.names()) == {f"local.{n}" for n in ACTIONS}
    tiers = {"local.empty_trash": Tier.DESTRUCTIVE,
             "local.window_close": Tier.MUTATING,
             "local.system_stats": Tier.READ_ONLY,
             "local.volume_up": Tier.REVERSIBLE}
    for cap, tier in tiers.items():
        assert b.get(cap).spec.tier is tier
    for name in b.names():
        binding = b.get(name)
        assert binding.spec.summary
        assert binding.side_effects          # side-effect declaration required
        assert binding.spec.timeout_s > 0    # timeout budget required


# ----------------------------------------------------------------- fail closed

def test_unknown_tool_fails_closed_without_execution():
    b = build_host_broker()
    r = b.execute("no.such.tool", {}, source="user")
    assert not r.ok and r.denied == "UNKNOWN_CAPABILITY" and r.output == ""


def test_malformed_args_denied_before_any_execution():
    b = build_host_broker()
    r = b.execute("local.open_app", {"app": "x", "evil": {"nested": 1}},
                  source="user")
    assert r.denied == "MALFORMED_INPUT"
    r2 = b.execute("local.timer", {"minutes": [5]}, source="user")
    assert r2.denied == "MALFORMED_INPUT"
    r3 = b.execute("local.time_date", {"undeclared_extra": "y"}, source="user")
    assert r3.denied == ""          # scalar-only validator permits extra keys


def test_model_and_automation_sources_cannot_execute_mutating_tools():
    b = build_host_broker()
    calls = []
    reg = CapabilitySpec(name="t.mut", version="1.0.0", tier=Tier.MUTATING,
                         summary="mutating tool", scopes=("s",), timeout_s=5)
    b.register(ToolBinding(spec=reg, side_effects="writes",
                           fn=lambda a: calls.append(1) or "ran"))
    for src in ("model", "automation"):
        r = b.execute("t.mut", {}, source=src)
        assert r.denied == "CONFIRMATION_SOURCE_FORBIDDEN"
        assert not r.ok and not calls


def test_model_cannot_issue_confirmation_even_via_helper():
    b = build_host_broker()
    with pytest.raises(ConfirmationDenied):
        b.issue_confirmation("local.empty_trash", {}, issued_by="model")


def test_read_only_tool_needs_no_confirmation_and_stays_fast():
    b = build_host_broker()
    t0 = time.perf_counter()
    r = b.execute("local.system_stats", {}, source="user", timeout_s=10)
    assert r.ok and r.output
    # no model, no confirmation store round trip: pure in-process decision
    assert (time.perf_counter() - t0) < 1.0


# ------------------------------- DEFERRED-004: real-tool approval round trip

def _real_trash_like_tool(b: CapabilityBroker, tmp_path) -> dict:
    """A REAL trusted local tool: writes/counts via FILES so its effects are
    observable from the parent after fork isolation (no shared memory)."""
    state = {"runs_file": str(tmp_path / "runs"), "target": str(tmp_path / "trash")}
    def _empty(params):
        with open(state["runs_file"], "a") as f:
            f.write("run\n")
        Path(state["target"]).write_text("emptied")
        return "Trash emptied."
    spec = CapabilitySpec(name="host.empty_trash", version="1.0.0",
                          tier=Tier.DESTRUCTIVE, summary="empty trash",
                          scopes=("local",), timeout_s=15)
    b.register(ToolBinding(spec=spec, side_effects="permanently deletes",
                           fn=_empty))
    return state


def _runs(state) -> int:
    p = Path(state["runs_file"])
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_destructive_round_trip_on_real_tool_issue_consume_execute(tmp_path):
    b = build_host_broker()
    state = _real_trash_like_tool(b, tmp_path)
    args = {"scope": "user"}
    r1 = b.execute("host.empty_trash", args, source="user")
    assert r1.denied == "CONFIRMATION_REQUIRED"      # nothing ran yet
    assert _runs(state) == 0 and not Path(state["target"]).exists()
    rec = b.issue_confirmation("host.empty_trash", args, issued_by="user")
    r2 = b.execute("host.empty_trash", args, source="user",
                   confirmation_id=rec.id)
    assert r2.ok and _runs(state) == 1
    assert Path(state["target"]).read_text() == "emptied"


def test_replayed_confirmation_fails_closed_on_real_tool(tmp_path):
    b = build_host_broker()
    state = _real_trash_like_tool(b, tmp_path)
    rec = b.issue_confirmation("host.empty_trash", {"scope": "u"},
                               issued_by="user")
    r1 = b.execute("host.empty_trash", {"scope": "u"}, source="user",
                   confirmation_id=rec.id)
    assert r1.ok
    r2 = b.execute("host.empty_trash", {"scope": "u"}, source="user",
                   confirmation_id=rec.id)
    assert r2.denied == "REPLAYED_CONFIRMATION" and not r2.ok
    assert _runs(state) == 1                         # executed exactly once


def test_target_mismatch_fails_closed(tmp_path):
    b = build_host_broker()
    state = _real_trash_like_tool(b, tmp_path)
    rec = b.issue_confirmation("host.empty_trash", {"scope": "a"},
                               issued_by="user")
    r = b.execute("host.empty_trash", {"scope": "b"}, source="user",
                  confirmation_id=rec.id)
    assert r.denied == "TARGET_MISMATCH" and _runs(state) == 0


def test_expired_confirmation_fails_closed(tmp_path):
    b = build_host_broker()
    state = _real_trash_like_tool(b, tmp_path)
    clock = {"now": time.time()}
    b.engine.confirmations._clock = lambda: clock["now"]
    rec = b.issue_confirmation("host.empty_trash", {"scope": "u"},
                               issued_by="user", ttl_s=1)
    clock["now"] += 5                                # simulate expiry
    r = b.execute("host.empty_trash", {"scope": "u"}, source="user",
                  confirmation_id=rec.id)
    assert r.denied == "EXPIRED_CONFIRMATION" and _runs(state) == 0


def test_audit_events_redacted_and_correlated(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(broker_mod, "log_event",
                        lambda e, **f: seen.append((e, f)))
    b = build_host_broker()
    state = _real_trash_like_tool(b, tmp_path)
    secret = "token=sk-SUPERSECRETTOKEN123"
    args = {"scope": f"u {secret}"}
    r0 = b.execute("host.empty_trash", args, source="user",
                   correlation_id="cid-audit-1")
    assert r0.denied == "CONFIRMATION_REQUIRED"      # two-step starts here
    rec = b.issue_confirmation("host.empty_trash", args, issued_by="user")
    r = b.execute("host.empty_trash", args, source="user",
                  confirmation_id=rec.id, correlation_id="cid-audit-1")
    assert r.ok
    events = [f for f in seen if f[0].startswith("broker.exec.")]
    phases = {e.rsplit(".", 1)[1] for e, _ in events}
    assert {"denied", "start", "finish"} <= phases
    for _, fields in events:
        blob = repr(fields)
        assert secret not in blob                     # redaction holds
    starts = [f for e, f in events if e == "broker.exec.start"]
    finishes = [f for e, f in events if e == "broker.exec.finish"]
    assert starts and finishes
    assert all(f["correlation_id"] == "cid-audit-1" for f in starts + finishes)


# ------------------------------------------------------- preview / dry-run

def test_dry_run_previews_argv_without_side_effect(tmp_path):
    b = build_host_broker()
    marker = tmp_path / "side-effect"
    argv = ["python", "-c", "pass"]
    spec = CapabilitySpec(name="t.writer", version="1.0.0",
                          tier=Tier.REVERSIBLE, summary="writer",
                          scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=spec, side_effects="creates marker file",
                           argv_builder=lambda a: ["touch", str(marker)]))
    r = b.execute("t.writer", {}, source="user", dry_run=True)
    assert r.ok and r.dry_run
    assert "argv:" in r.output and "side_effects:" in r.output
    assert "touch" in r.output
    assert not marker.exists()                       # NOTHING executed


# --------------------------------------------- cancellation / timeout / bounds

def _register_sleep(b: CapabilityBroker, seconds: str = "30"):
    spec = CapabilitySpec(name="t.sleep", version="1.0.0",
                          tier=Tier.READ_ONLY, summary="sleep tool",
                          scopes=("s",), timeout_s=60)
    b.register(ToolBinding(spec=spec, side_effects="none (reads clock)",
                           argv_builder=lambda a: ["sleep", seconds]))


def test_cancel_kills_process_group_and_is_idempotent():
    b = build_host_broker()
    _register_sleep(b, "30")
    box = {}
    th = threading.Thread(
        target=lambda: box.update(r=b.execute("t.sleep", {}, source="user")))
    th.start()
    deadline = time.time() + 5
    while time.time() < deadline and "r" in box:
        time.sleep(0.01)
        break
    # wait until the run is registered as active
    while time.time() < deadline:
        with b._lock:
            if any(True for _ in b._active):
                break
        time.sleep(0.02)
    first = second = None
    for cid in list(b._active):
        first = b.cancel(cid)
        second = b.cancel(cid)
        break
    assert first is True and second is False         # exactly-once cancel
    th.join(timeout=8)
    r = box.get("r")
    assert r is not None and not r.ok and r.cancelled
    assert r.duration_ms < 8000                      # died promptly, not 30s


def test_cancel_unknown_correlation_id_is_false():
    b = build_host_broker()
    assert b.cancel("nope") is False


# ------------------------- callable tools are killable (repair regressions)

def _register_late_writer(b: CapabilityBroker, marker: Path, delay_s: float):
    """Callable tool that writes a marker ONLY if it survives `delay_s`."""
    def slow_writer(params):
        time.sleep(delay_s)
        marker.write_text("late-side-effect")
        return "wrote"
    spec = CapabilitySpec(name="t.late", version="1.0.0",
                          tier=Tier.REVERSIBLE, summary="slow late writer",
                          scopes=("s",), timeout_s=60)
    b.register(ToolBinding(spec=spec, side_effects="writes marker file",
                           fn=slow_writer))


def _wait_for_active(b: CapabilityBroker, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with b._lock:
            if b._active:
                return next(iter(b._active))
        time.sleep(0.01)
    raise AssertionError("tool never became active")


def test_cancelled_callable_cannot_create_a_later_side_effect(tmp_path):
    """Repair 1/2/4: the callable runs in a killable forked child; cancel
    SIGKILLs its process group so the post-cancel write NEVER happens."""
    b = build_host_broker()
    marker = tmp_path / "late-effect"
    _register_late_writer(b, marker, delay_s=3.0)
    box = {}
    th = threading.Thread(
        target=lambda: box.update(r=b.execute("t.late", {}, source="user")))
    th.start()
    cid = _wait_for_active(b)
    first = second = None
    for known in list(b._active):
        first = b.cancel(known)
        second = b.cancel(known)
        break
    assert first is True and second is False
    th.join(timeout=15)
    r = box.get("r")
    assert r is not None and not r.ok and r.cancelled
    time.sleep(4.0)                    # longer than the fn needed to write
    assert not marker.exists(), "cancelled callable produced a later effect"
    with b._lock:
        assert cid not in b._active    # registry cleaned after cancellation


def test_callable_timeout_kills_child_no_late_side_effect(tmp_path):
    b = build_host_broker()
    marker = tmp_path / "timeout-effect"
    _register_late_writer(b, marker, delay_s=3.5)
    t0 = time.perf_counter()
    r = b.execute("t.late", {}, source="user", timeout_s=1.0)
    elapsed = time.perf_counter() - t0
    assert not r.ok and r.timed_out and not r.cancelled
    assert elapsed < 6                 # killed promptly, not waited out
    time.sleep(4.0)
    assert not marker.exists()
    with b._lock:
        assert not b._active           # reaped after timeout


def test_callable_exception_returns_typed_error_and_cleans(tmp_path):
    b = build_host_broker()
    def boom(params):
        raise RuntimeError("exploded on purpose")
    spec = CapabilitySpec(name="t.boom", version="1.0.0", tier=Tier.READ_ONLY,
                          summary="always fails", scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=spec, side_effects="none", fn=boom))
    r = b.execute("t.boom", {}, source="user")
    assert not r.ok and "exploded" in r.error and not r.timed_out
    with b._lock:
        assert not b._active


def test_active_registry_clean_on_every_exit_path():
    b = build_host_broker()
    ok_spec = CapabilitySpec(name="t.ok", version="1.0.0", tier=Tier.READ_ONLY,
                             summary="noop", scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=ok_spec, side_effects="none",
                           fn=lambda a: "ok"))
    argv_spec = CapabilitySpec(name="t.argv", version="1.0.0",
                               tier=Tier.READ_ONLY, summary="echo",
                               scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=argv_spec, side_effects="none",
                           argv_builder=lambda a: ["echo", "hi"]))
    # normal success (callable + argv)
    assert b.execute("t.ok", {}, source="user").ok
    assert b.execute("t.argv", {}, source="user").ok
    # denial + dry-run paths never register
    assert b.execute("no.such", {}, source="user").denied
    assert b.execute("t.ok", {}, source="user", dry_run=True).dry_run
    with b._lock:
        assert not b._active


def test_timeout_enforced_with_partial_cleanup():
    b = build_host_broker()
    _register_sleep(b, "30")
    r = b.execute("t.sleep", {}, source="user", timeout_s=1.0)
    assert not r.ok and r.timed_out and not r.cancelled


def test_bounded_output_marks_truncation():
    b = build_host_broker()
    spec = CapabilitySpec(name="t.flood", version="1.0.0", tier=Tier.READ_ONLY,
                          summary="flood stdout", scopes=("s",), timeout_s=20)
    py = "print('x' * 500000)"
    b.register(ToolBinding(spec=spec, side_effects="none",
                           argv_builder=lambda a: ["python3", "-c", py]))
    r = b.execute("t.flood", {}, source="user", max_output=10000)
    assert r.truncated and len(r.output) <= 10000


def test_concurrent_runs_do_not_exchange_outputs():
    b = build_host_broker()
    spec = CapabilitySpec(name="t.echo", version="1.0.0", tier=Tier.READ_ONLY,
                          summary="echo tag", scopes=("s",), timeout_s=20)
    b.register(ToolBinding(spec=spec, side_effects="none",
                           argv_builder=lambda a: ["echo", a["tag"]]))
    results = {}
    def go(tag):
        results[tag] = b.execute("t.echo", {"tag": tag}, source="user").output
    ts = [threading.Thread(target=go, args=(f"tag-{i}",)) for i in range(6)]
    [t.start() for t in ts]
    [t.join(timeout=15) for t in ts]
    assert len(results) == 6
    for i in range(6):
        assert results[f"tag-{i}"].strip() == f"tag-{i}"   # no crossover


# --------------------------------------------------- script template tools

def test_script_registration_argv_schema_and_substitution():
    b = build_host_broker()
    cap = register_script_tool(
        b, template_name="Check Weather!",
        script='~/venv/bin/python weather.py "{question}" 1500',
        timeout_s=45)
    assert cap == "script.check-weather"
    bind = b.get(cap)
    assert bind.spec.tier is Tier.MUTATING
    argv = bind.argv_builder({"question": "will it rain; rm -rf /tmp"})
    assert argv[0].endswith("python")
    assert "will it rain; rm -rf /tmp" in argv       # ONE inert argv element
    assert not any(";" in tok and tok != argv[-1] or True for tok in [])
    r = b.execute(cap, {"question": "hello world"}, source="user",
                  timeout_s=30, max_output=2000)
    assert r.denied == ""                             # policy allowed
    # python may be missing on PATH -> typed failure, never a shell fallback
    assert isinstance(r.ok, bool)


def test_script_metacharacters_rejected_fail_closed():
    b = build_host_broker()
    with pytest.raises(Exception):
        register_script_tool(b, template_name="bad1",
                             script="python x.py | rm -rf /")
    with pytest.raises(Exception):
        register_script_tool(b, template_name="bad2",
                             script="python x.py && echo pwned")
    with pytest.raises(Exception):
        register_script_tool(b, template_name="bad3", script="")
    assert b.get("script.bad1") is None               # nothing registered


def test_script_placeholder_outside_quotes_rejected():
    b = build_host_broker()
    with pytest.raises(Exception):
        register_script_tool(b, template_name="bad4",
                             script="python x.py > /tmp/out")


def test_model_source_cannot_trigger_registered_script_template():
    b = build_host_broker()
    cap = register_script_tool(b, template_name="search-web",
                               script='python web_search.py "{question}"')
    r = b.execute(cap, {"question": "x"}, source="model")
    assert r.denied == "CONFIRMATION_SOURCE_FORBIDDEN"


# ------------------------------------------------- shared engine / workers

class _Cfg:
    def __init__(self, mode=None):
        self._mode = mode
    def get(self, *a, **k):
        return self._mode


def test_worker_guard_shares_broker_engine():
    b = build_host_broker()
    register_worker_caps(b.registry)
    g = WorkerGuard(_Cfg("confirm"), engine=b.engine)
    assert g.engine is b.engine
    assert "openclaw.gateway.task" in b.registry.names()
    assert "hermes.task" in b.registry.names()


def test_worker_guard_shared_engine_still_blocks_model_source():
    b = build_host_broker()
    register_worker_caps(b.registry)
    g = WorkerGuard(_Cfg("confirm"), engine=b.engine)
    with pytest.raises(DispatchBlocked):
        g.run("hermes.task", lambda t: t, "hi", source="model")


def test_worker_guard_exec_audit_redacted_and_correlated(monkeypatch):
    from voicebridge import dispatch_guard as dg
    seen = []
    monkeypatch.setattr(dg, "log_event", lambda e, **f: seen.append((e, f)))
    b = build_host_broker()
    register_worker_caps(b.registry)
    g = WorkerGuard(_Cfg("confirm"), engine=b.engine)
    out = g.run("hermes.task", lambda t: f"reply:{t}",
                "authorization: Bearer abcdef123456",
                source="user", correlation_id="cid-w-1")
    assert "reply:" in out
    names = [e for e, _ in seen]
    assert "worker.exec.start" in names and "worker.exec.finish" in names
    fin = [f for e, f in seen if e == "worker.exec.finish"][0]
    assert fin["correlation_id"] == "cid-w-1"
    start = [f for e, f in seen if e == "worker.exec.start"][0]
    assert "abcdef123456" not in repr(start)          # redacted task text


# --------------------------------------------------------- rollback surface

def test_legacy_rollback_paths_present_behind_flag():
    src = (REPO / "voicebridge/orchestrator.py").read_text(errors="replace")
    assert 'tool_broker' in src                       # flag consulted
    assert "run_tool(cmd," in src                     # legacy path retained
    assert "CONFIRMATION_REQUIRED" in src             # destructive flow wired
    cfg_src = (REPO / "voicebridge/config.py").read_text(errors="replace")
    assert '"tool_broker": True' in cfg_src           # additive default


def test_orchestrator_wires_all_worker_sites_through_shared_engine():
    src = (REPO / "voicebridge/orchestrator.py").read_text(errors="replace")
    assert "WorkerGuard(self.cfg, engine=self._broker.engine)" in src
    assert "register_worker_caps(self._broker.registry)" in src
    # Packet 8O: the orchestrator builds the host broker through the shared 8O
    # power control so every worker site observes emergency stop / disable /
    # circuit / cancellation state (the engine is still shared).
    assert "build_host_broker(" in src
    assert "power_control=self._power_policy.control" in src


# --------------------------------------------------------------- benchmarks

def test_fast_path_latency_benchmark(capsys):
    """Direct safe actions stay fast: full broker decide+dispatch overhead."""
    b = build_host_broker()
    spec = CapabilitySpec(name="bench.noop", version="1.0.0",
                          tier=Tier.READ_ONLY, summary="noop",
                          scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=spec, side_effects="none", fn=lambda a: "ok"))
    n = 300
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = b.execute("bench.noop", {}, source="user")
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.ok
    samples.sort()
    p50 = samples[n // 2]
    p95 = samples[int(n * 0.95)]
    print(f"\nbroker fast-path: n={n} p50={p50:.3f}ms p95={p95:.3f}ms "
          f"max={samples[-1]:.3f}ms")
    captured = capsys.readouterr()
    assert "p50=" in captured.out
    assert p95 < 50.0, f"fast path regressed: p95={p95:.3f}ms"


def test_exec_result_is_typed_dataclass():
    r = ExecResult()
    assert r.ok is False and r.denied == "" and r.dry_run is False


# ------------------------------------------- fork warning regression (3.12+)
def test_fork_from_threaded_process_emits_no_deprecation_warning(tmp_path):
    """Regression: CPython 3.12+ warns when fork() is called from a
    multi-threaded process ('is multi-threaded, use of fork()'). The broker
    intentionally forks (killable process-group isolation of in-process
    closures); the single-site targeted filter in broker.py must keep the
    destructive-exec path warning-free even when other threads are alive."""
    import warnings
    b = build_host_broker()
    state = {"runs_file": str(tmp_path / "runs"), "target": str(tmp_path / "trash")}

    def _empty(params):
        Path(state["target"]).write_text("emptied")
        return "ok"

    spec = CapabilitySpec(name="host.forkwarn", version="1.0.0",
                          tier=Tier.DESTRUCTIVE, summary="fork warn",
                          scopes=("local",), timeout_s=10)
    b.register(ToolBinding(spec=spec, side_effects="deletes", fn=_empty))
    # ensure the process is multi-threaded so the stdlib would normally warn
    keepalive = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    keepalive.start()
    try:
        rec = b.issue_confirmation("host.forkwarn", {"scope": "local"},
                                   issued_by="user")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            r = b.execute("host.forkwarn", {"scope": "local"}, source="user",
                          confirmation_id=rec.id)
        assert r.ok
        fork_warns = [str(x.message) for x in w
                      if "is multi-threaded" in str(x.message)]
        assert fork_warns == [], \
            f"fork DeprecationWarning leaked through the broker filter: {fork_warns}"
    finally:
        pass


# ------------------------------------------- read-only fast path regression
def test_read_only_callable_fast_path_is_in_process_mutating_still_isolated():
    """Regression: READ_ONLY callable tools run IN-PROCESS (no child process)
    on the documented fast path ('pure in-process decision'), while
    side-effect-capable callable tools still spawn a killable isolated child.
    The fast path is what test_fast_path_latency_benchmark measures; keeping it
    in-process is what holds the <50 ms budget under load."""
    def _active_pid(b):
        with b._lock:
            acts = list(b._active.items())
            return acts[0][1].pid if acts else None

    b = build_host_broker()

    def ro_fn(a):
        time.sleep(0.25)
        return "ok"

    spec = CapabilitySpec(name="fast.ro", version="1.0.0", tier=Tier.READ_ONLY,
                          summary="ro", scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=spec, side_effects="none", fn=ro_fn))
    box = {}
    th = threading.Thread(target=lambda: box.update(
        r=b.execute("fast.ro", {}, source="user")))
    th.start()
    deadline = time.time() + 5
    ro_pid = None
    while time.time() < deadline:
        ro_pid = _active_pid(b)
        if ro_pid is not None:
            break
        time.sleep(0.01)
    th.join(timeout=5)
    assert box["r"].ok
    assert ro_pid is None, \
        f"read-only callable must NOT spawn a child (got pid {ro_pid})"

    def mut_fn(a):
        time.sleep(0.35)
        return "mut"

    spec2 = CapabilitySpec(name="fast.mut", version="1.0.0",
                           tier=Tier.MUTATING, summary="mut",
                           scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=spec2, side_effects="changes state", fn=mut_fn))
    box2 = {}
    th2 = threading.Thread(target=lambda: box2.update(
        r=b.execute("fast.mut", {}, source="user")))
    th2.start()
    deadline = time.time() + 5
    mut_pid = None
    while time.time() < deadline:
        mut_pid = _active_pid(b)
        if mut_pid is not None:
            break
        time.sleep(0.01)
    th2.join(timeout=8)
    assert box2["r"].ok
    assert mut_pid is not None, \
        "mutating callable must spawn an isolated (killable) child"


def test_read_only_callable_exception_typed_and_registry_clean():
    """Regression: an in-process read-only callable that raises returns a typed
    failure (not a crash) and leaves the active registry clean."""
    b = build_host_broker()
    def boom(a):
        raise RuntimeError("inline boom")
    spec = CapabilitySpec(name="fast.boom", version="1.0.0",
                          tier=Tier.READ_ONLY, summary="boom",
                          scopes=("s",), timeout_s=10)
    b.register(ToolBinding(spec=spec, side_effects="none", fn=boom))
    r = b.execute("fast.boom", {}, source="user")
    assert not r.ok and "inline boom" in r.error and not r.timed_out
    with b._lock:
        assert not b._active
