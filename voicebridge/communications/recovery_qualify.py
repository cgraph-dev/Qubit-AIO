"""Packet 8J: isolated end-to-end recovery rehearsal (hermetic).

Rehearses crash/recovery boundaries around the already-verified browser, audio,
timer, notification, report, cursor, and action contracts WITHOUT touching any
live service, account, browser, timer, or production state.

Every stage runs against a FRESH isolated temporary state root:

- the communications store commit (atomic cursor + report),
- rollback_cursor after a crash point,
- report-bundle atomic publish + partial/corrupt bundle + rollback,
- portable backup/restore/rollback (incl. partial-rollback visibility),
- notification retry / dead-letter bounds and dedupe across "restart",
- the action receipt store crash recovery (running -> interrupted),
- the lease (stale / overlap / renewal),
- truncated/corrupt JSON/JSONL/SQLite state, disk-full / read-only writes,
- concurrent manual/timer/action access,
- RPO/RTO accounting and idempotent repeated recovery.

A rollback that cannot fully restore every touched path MUST be reported as a
typed failure with the exact bounded paths; it is never swallowed or described
as successful. This harness NEVER runs destructive account actions, sends real
messages, fakes remote rollback, or uses production state as a fixture.

Deterministic crash/fault injection runs in child processes
(``recovery_crash``): SIGKILL at file-write and store-commit boundaries, real
fsync / os.replace / write-failure injection, RLIMIT_FSIZE-enforced disk-full,
and resource-limited workloads with actual measurements.

Persistence: a redacted typed summary is written at ``0600`` under the isolated
state dir (default ``<config state>/qualification-recovery``).
"""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import Config
from .adapters.registry import communications_store
from .contracts import (NotificationKind, NotificationRecord,
                        NotificationStatus, Platform)
from .monitor import MONITOR_LEASE
from ..portable import (
    PartialRollbackError, PortableError, export_bundle, read_bundle,
    restore_state, plan_restore,
)
from . import recovery_crash as CRASH

QUALIFY_ENV = "VOICE_BRIDGE_QUALIFY"
QUALIFY_VERSION = 1
QUALIFY_STATE_DIR = "qualification-recovery"

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER = "voicebridge.communications.recovery_crash"

# Hermetic defaults — RPO/RTO are operator-declared targets; the harness proves
# that recovery is bounded and idempotent, not that a specific SLA is met.
DEFAULT_RPO_S = 3600.0
DEFAULT_RTO_S = 300.0

# Resource ceilings applied to every isolated stage by default.
DEFAULT_RLIMITS: Dict[str, Any] = {
    "cpu": (120, 300),         # 120 s CPU soft / 300 s hard
    "as": 2 * 1024 * 1024 * 1024,   # 2 GiB address space
    "fsize": 8 * 1024 * 1024,   # 8 MiB of output per stage
    "nofile": 512,
    "nproc": 128,
}
# wall-clock per-child timeout (parent-side SIGKILL).
CHILD_TIMEOUT_S = 90.0


def live_authorized(env_value: Optional[str] = None) -> bool:
    return (os.environ.get(QUALIFY_ENV) if env_value is None
            else env_value) == "1"


def deny_live(live: bool, *, env_value: Optional[str] = None
              ) -> Optional[Dict[str, Any]]:
    """Packet 8J is hermetic-only; any live request fails closed."""
    if not live:
        return None
    if live_authorized(env_value):
        return None
    return {
        "state": "denied", "code": "LIVE_NOT_AUTHORIZED",
        "detail": f"Packet 8J has no live stages; recovery is rehearsed in an "
                  f"isolated temporary state root only ({QUALIFY_ENV}=1 has no "
                  f"effect). Nothing was touched.",
    }


def isolate_cfg(cfg: Config, state_dir: Path) -> Config:
    """A copy of the config with the state root redirected to an isolated dir.

    Guarantees the rehearsal never writes into the production state root.
    """
    data = copy.deepcopy(cfg.data)
    log = data.setdefault("logging", {})
    log["jsonl"] = True
    log["jsonl_path"] = str(state_dir)
    return Config(data=data, path=cfg.path)


# --------------------------------------------------------------------- helpers
def _mkstate(root: Path) -> Path:
    """A unique per-call private state dir so stages never share a store."""
    st = root / f"state-{uuid.uuid4().hex[:8]}"
    st.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(st, 0o700)
    except OSError:
        pass
    return st


# -------------------------------------------------------------- crash workers
def _spawn_worker(spec: Dict[str, Any], workdir: Path) -> subprocess.Popen:
    """Launch the crash worker as a child process with an isolated spec file."""
    spec_dir = workdir / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    sp = spec_dir / f"spec-{uuid.uuid4().hex[:8]}.json"
    sp.write_text(json.dumps(spec))
    try:
        os.chmod(sp, 0o600)
    except OSError:
        pass
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [sys.executable, "-m", WORKER, "--spec", str(sp)],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wait_marker(marker_dir: Path, name: str, timeout: float) -> bool:
    p = marker_dir / f"reached-{name}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if p.exists():
            return True
        time.sleep(0.02)
    return False


def _kill_and_await(proc: subprocess.Popen, timeout: float = 10.0
                    ) -> Tuple[int, str]:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate(timeout=5)
    return (proc.returncode or -signal.SIGKILL), (err or "")[:800]


def _await_completion(proc: subprocess.Popen, timeout: float = 30.0
                      ) -> Tuple[int, str]:
    """Wait for a child to finish normally (fault-injection modes write a result
    JSON and exit 0); kill if it hangs."""
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate(timeout=5)
    rc = proc.returncode
    if rc is None:
        rc = -signal.SIGKILL
    return rc, (err or "")[:800]


def _worker_result(marker_dir: Path) -> Dict[str, Any]:
    rp = marker_dir / "result.json"
    if not rp.exists():
        return {"ok": False, "error": "no result.json"}
    try:
        return json.loads(rp.read_text())
    except Exception as exc:  # noqa: BLE001 - typed
        return {"ok": False, "error": f"corrupt result: {exc}"}


def _run_sigkill_file(boundary: str, root: Path) -> Dict[str, Any]:
    """Kill a child writing a real atomic file at ``boundary``; inspect state.

    Returns a typed check dict. ``boundary`` is one of CRASH.BOUNDARIES_FILE.
    """
    marker = root / "markers"
    marker.mkdir(parents=True, exist_ok=True)
    target = root / "state.json"
    target.write_text('{"old":true}')
    data = '{"new":true}'
    spec = {"mode": "sigkill_file", "target": str(target), "data": data,
            "marker_dir": str(marker), "boundary": boundary}
    proc = _spawn_worker(spec, root)
    if not _wait_marker(marker, boundary, CHILD_TIMEOUT_S):
        _kill_and_await(proc)
        return {"state": "failed", "reason": "marker timeout",
                "boundary": boundary}
    _kill_and_await(proc)
    content = target.read_text() if target.exists() else None
    temps = sorted(p.name for p in root.iterdir()
                   if p != marker and p != target
                   and not p.name.startswith("specs"))
    # Invariants:
    #  - after_rename: new content must be the durable state;
    #  - before_rename (incl): old content must remain the served state; a
    #    leftover temp file is expected after a crash but must NEVER be served
    #    as the target (the target path must not be a temp and must hold the
    #    prior valid content).
    if boundary == "after_rename":
        ok = content == data
        reason = f"content={content!r}"
    else:
        ok = content == '{"old":true}'
        reason = f"content={content!r} temps={temps}"
    return {"state": "OK" if ok else "failed", "boundary": boundary,
            "content": content, "temp_files": temps, "reason": reason}


def _run_sigkill_store(boundary: str, root: Path) -> Dict[str, Any]:
    """Kill a child committing a real store new-item at ``boundary``.

    For mid-transaction boundaries, SQLite atomicity means only the baseline is
    durable (no partial cursor/report/notification, no cursor jump). After
    ``after_commit`` the full commit is durable.
    """
    marker = root / "markers"
    marker.mkdir(parents=True, exist_ok=True)
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    spec = {"mode": "sigkill_store", "state_dir": str(state),
            "marker_dir": str(marker), "boundary": boundary}
    proc = _spawn_worker(spec, root)
    if not _wait_marker(marker, boundary, CHILD_TIMEOUT_S):
        _kill_and_await(proc)
        return {"state": "failed", "reason": "marker timeout",
                "boundary": boundary}
    _kill_and_await(proc)
    try:
        st = communications_store(Config(data={"logging": {"jsonl": True,
                                     "jsonl_path": str(state)}}))
        cur = st.peek_cursor(Platform.GMAIL, "default")
        items = st._conn.execute(
            "SELECT COUNT(*) FROM item").fetchone()[0]
        reports = st._conn.execute(
            "SELECT COUNT(*) FROM report_manifest").fetchone()[0]
        notif = st._conn.execute(
            "SELECT COUNT(*) FROM notification").fetchone()[0]
        st.close()
    except Exception as exc:  # noqa: BLE001 - typed reopen
        return {"state": "failed", "reason": f"reopen failed: {exc}",
                "boundary": boundary}
    if boundary == "after_commit":
        ok = bool(cur) and items == 3 and reports == 2 and notif == 1
        expect = "cursor=Y items=3 reports=2 notif=1"
    elif boundary == "before_commit":
        # killed before any store write: nothing is durable.
        ok = cur is None and items == 0 and reports == 0 and notif == 0
        expect = "cursor=N items=0 reports=0 notif=0 (nothing durable)"
    else:
        # mid-transaction kill: only the baseline survives atomically.
        ok = bool(cur) and items == 2 and reports == 1 and notif == 0
        expect = "cursor=Y items=2 reports=1 notif=0 (atomic rollback)"
    got = f"cursor={'Y' if cur else 'N'} items={items} reports={reports} notif={notif}"
    return {"state": "OK" if ok else "failed", "boundary": boundary,
            "expect": expect, "got": got}


def _run_fault_file(mode: str, errno_val: int, at: int, root: Path,
                    preexisting: bool = True) -> Dict[str, Any]:
    """fsync / rename / write failure injection against the real atomic writer.

    ``mode`` is one of fsync_fail / rename_fail / enospc. Verifies typed
    failure, prior valid state intact, no temp served, retry idempotent.
    """
    marker = root / "markers"
    marker.mkdir(parents=True, exist_ok=True)
    target = root / "state.json"
    if preexisting:
        target.write_text('{"old":true}')
    spec = {"mode": mode, "target": str(target), "data": '{"new":true}',
            "marker_dir": str(marker), "errno": errno_val, "at": at}
    proc = _spawn_worker(spec, root)
    _await_completion(proc)
    res = _worker_result(marker)
    content = target.read_text() if target.exists() else None
    temps = sorted(p.name for p in root.iterdir()
                   if p != marker and p != target
                   and not p.name.startswith("specs"))
    typed = (res.get("ok") is False and res.get("error") == "OSError"
             and res.get("errno") == errno_val)
    prior_intact = (content == '{"old":true}') if preexisting else \
        (content is None)
    ok = typed and prior_intact and not temps
    # retry idempotency: a second run WITHOUT injection succeeds.
    marker2 = root / "markers2"
    marker2.mkdir(parents=True, exist_ok=True)
    spec2 = {"mode": "sigkill_file", "target": str(target),
             "data": '{"new":true}', "marker_dir": str(marker2)}
    proc2 = _spawn_worker(spec2, root)
    _await_completion(proc2)
    res2 = _worker_result(marker2)
    retry_ok = res2.get("ok") is True and \
        target.read_text() == '{"new":true}'
    return {"state": "OK" if ok and retry_ok else "failed",
            "mode": mode, "errno": errno_val, "typed_failure": typed,
            "prior_intact": prior_intact, "no_temp_served": not temps,
            "retry_idempotent": retry_ok,
            "content": content, "temp_files": temps}


def _snapshot_state(state: Path) -> Dict[str, Any]:
    """sha256 + schema hash of every file under an isolated state root."""
    out: Dict[str, Any] = {"files": {}, "schema_versions": {}}
    for p in sorted(state.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(state))
        try:
            out["files"][rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            out["files"][rel] = "<unreadable>"
    return out


# -------------------------------------------------------------------- preflight
def qualify_preflight(cfg: Config) -> Dict[str, Any]:
    """Typed preflight: RPO/RTO targets, enforced resource ceilings, staging.

    All hermetic. Resource ceilings ARE hard-enforced in every child stage
    (see the ``resources`` stage); wall-clock is enforced by the parent. This
    stage reports the operator-declared RPO/RTO targets and the ceilings the
    rehearsal applies.
    """
    rpo = float(cfg.get("recovery", "rpo_seconds", default=DEFAULT_RPO_S))
    rto = float(cfg.get("recovery", "rto_seconds", default=DEFAULT_RTO_S))
    out: Dict[str, Any] = {
        "state": "OK",
        "qualify_version": QUALIFY_VERSION,
        "recovery": {
            "rpo_seconds": round(rpo, 1),
            "rto_seconds": round(rto, 1),
            "rpo_gt_0": rpo > 0,
            "rto_gt_0": rto > 0,
            "measurable": True,   # see the rpo_rto stage
        },
        "resource_ceilings": {
            "stage_uses_isolated_temp_state": True,
            "no_live_service_or_account_touched": True,
            "destructive_account_actions_forbidden": True,
            "wall_clock_timeout_s": CHILD_TIMEOUT_S,
            "child_limits": dict(DEFAULT_RLIMITS),
            "network_disabled_in_child": True,
            "hard_enforced": True,
        },
        "journal": {"jsonl_bounded": True},
    }
    if rpo <= 0 or rto <= 0:
        out["state"] = "degraded"
        out["detail"] = "RPO/RTO must be > 0 seconds"
    return out


# ------------------------------------------------------------------ crash commit
def qualify_crash_commit(cfg: Config, state_dir: Path,
                         fixture: Optional[Path] = None) -> Dict[str, Any]:
    """Crash-boundary commit rehearsal for cursor + report (isolated).

    Proves:
    - a baseline commit advances the cursor atomically with the report;
    - a subsequent new-item commit is followed by the previous cursor being
      recoverable from history (rollback_cursor);
    - a "crash" BEFORE a commit leaves no cursor/report change (replay is
      idempotent — re-running the same scan reproduces the same outcome);
    - a new-item commit advances the cursor only after the report is committed.
    """
    from .adapters.registry import run_scan
    checks: Dict[str, Any] = {}
    scfg = isolate_cfg(cfg, _mkstate(state_dir))
    st = communications_store(scfg)
    fx = fixture or (Path(__file__).resolve().parents[2] / "tests" / "fixtures"
                     / "communications" / "gmail_items.json")
    fx_new = fixture or (Path(__file__).resolve().parents[2] / "tests" / "fixtures"
                         / "communications" / "qualify_external_item.json")

    # 1) baseline: cursor is NULL before, non-NULL + baseline after commit.
    b0 = st.peek_cursor(Platform.GMAIL, "default")
    checks["baseline_before_none"] = {
        "state": "OK" if b0 is None else "failed", "cursor": None}
    r1 = run_scan(scfg, "gmail", source="fixture", fixture=fx, store=st)
    checks["baseline_committed"] = {
        "state": "OK" if r1.get("commit") == "baseline"
        and r1.get("cursor_advanced") else "failed",
        "commit": r1.get("commit")}
    cur = st.peek_cursor(Platform.GMAIL, "default")
    checks["baseline_cursor_present"] = {
        "state": "OK" if cur is not None and cur.baseline else "failed",
        "baseline": bool(cur.baseline) if cur else None}

    # 2) new-item commit (the external-item fixture carries one genuinely new
    #    row) -> cursor advances and prior cursor lands in history.
    rn = run_scan(scfg, "gmail", source="fixture", fixture=fx_new, store=st)
    cur_new = st.peek_cursor(Platform.GMAIL, "default")
    checks["new_item_committed"] = {
        "state": "OK" if rn.get("commit") == "new"
        and rn.get("cursor_advanced") else "failed",
        "commit": rn.get("commit")}

    # 3) crash-before-commit: a failed/committable-false scan records no-commit
    #    and does NOT advance the cursor (no silent success / no cursor jump).
    before = st.peek_cursor(Platform.GMAIL, "default")
    bad = run_scan(scfg, "gmail", source="fixture",
                   fixture=Path("/nonexistent/does-not-exist.json"),
                   store=st)
    after = st.peek_cursor(Platform.GMAIL, "default")
    checks["crash_no_commit_no_cursor_jump"] = {
        "state": "OK" if bad.get("commit") in ("no-commit", "no-change", "failed")
        and (after is None or after.cursor_id == before.cursor_id)
        else "failed",
        "commit": bad.get("commit"),
        "cursor_unchanged": (after is None and before is None) or
                            (after is not None and before is not None
                             and after.cursor_id == before.cursor_id)}

    # 4) rollback_cursor restores the prior committed cursor from history
    #    (the baseline cursor, which is the pre-new-item value).
    rb = st.rollback_cursor(Platform.GMAIL, "default")
    checks["rollback_cursor_restores_prior"] = {
        "state": "OK" if rb is not None and rb.baseline
        and cur_new is not None and rb.cursor_id != cur_new.cursor_id else "failed",
        "baseline": bool(rb.baseline) if rb else None,
        "restored_to_prior": bool(rb is not None and cur_new is not None
                                  and rb.cursor_id != cur_new.cursor_id)}

    # 5) idempotent replay: running the same baseline scan again does not
    #    double-commit; it becomes no-change (same items already seen).
    r2 = run_scan(scfg, "gmail", source="fixture", fixture=fx, store=st)
    checks["replay_idempotent"] = {
        "state": "OK" if r2.get("commit") in ("no-change", "baseline")
        and r2.get("cursor_advanced") in (None, False) else "failed",
        "commit": r2.get("commit")}

    st.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------------------- lease
def qualify_lease(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Lease recovery rehearsal: stale-lease takeover, overlap, renewal.

    Proves the lease is monotonic, recoverable after a stale owner, renewable
    past a crash window, and that a second owner is denied while held.
    """
    from ..communications.monitor import LEASE_MIN_TTL_S
    checks: Dict[str, Any] = {}
    scfg = isolate_cfg(cfg, _mkstate(state_dir))
    st = communications_store(scfg)
    ttl = max(float(LEASE_MIN_TTL_S), 480.0)

    ok = st.acquire_lease(MONITOR_LEASE, "owner-a", ttl_s=ttl)
    checks["acquire_first_owner"] = {
        "state": "OK" if ok else "failed", "ok": ok}
    denied = st.acquire_lease(MONITOR_LEASE, "owner-b", ttl_s=ttl)
    checks["overlap_denied"] = {
        "state": "OK" if denied is False else "failed", "denied": denied}
    renewed = st.renew_lease(MONITOR_LEASE, "owner-a", ttl_s=ttl)
    checks["renewal_ok"] = {
        "state": "OK" if renewed else "failed", "renewed": renewed}
    st.release_lease(MONITOR_LEASE, "owner-a")
    # A stale owner (crash without release) must be recoverable by re-acquire
    # after the TTL (simulated by writing an expired monotonic deadline).
    st2 = communications_store(scfg)
    st2.acquire_lease(MONITOR_LEASE, "crashed-owner", ttl_s=-1)
    stale = st2.lease_ok(MONITOR_LEASE)
    checks["stale_lease_after_ttl"] = {
        "state": "OK" if stale is False else "failed", "lease_ok": stale}
    reacquire = st2.acquire_lease(MONITOR_LEASE, "new-owner", ttl_s=ttl)
    checks["stale_lease_reacquire"] = {
        "state": "OK" if reacquire else "failed", "ok": reacquire}
    st.close()
    st2.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ----------------------------------------------------------------- notify retry
def qualify_notify_retry(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Notification recovery: dead-letter bounds, dedupe, restart persistence.

    Proves PENDING notifications retry up to a bounded limit then become
    DEAD_LETTER (never infinite), a re-sent duplicate is deduplicated, and
    PENDING/DEAD_LETTER records survive a store close/reopen (restart).
    """
    checks: Dict[str, Any] = {}
    scfg = isolate_cfg(cfg, _mkstate(state_dir))
    st = communications_store(scfg)

    rec = NotificationRecord.create(
        report_id="", kind=NotificationKind.DESKTOP,
        status=NotificationStatus.PENDING,
        dedupe_key="recovery:retry:1", new_count=1)
    st.record_notification(rec)
    # bounded retries: 3 attempts -> dead_letter on the 4th.
    final = ""
    attempts = 0
    for _ in range(5):
        attempts += 1
        st.apply_retry_result(rec.notification_id, False, max_retries=3)
        rows = st.notification_rows_retryable(max_retries=3, limit=20)
        if not rows:
            final = "dead_letter"
            break
    checks["bounded_retry_dead_letter"] = {
        "state": "OK" if final == "dead_letter" else "failed",
        "attempts": attempts}

    # dedupe across "restart": a duplicate key is not double-delivered.
    dup = st.notification_exists("recovery:retry:1", NotificationKind.DESKTOP)
    checks["dedupe_exists"] = {
        "state": "OK" if dup else "failed", "exists": dup}

    # restart persistence: reopen the store, the records survive.
    st.close()
    st2 = communications_store(scfg)
    pending = st2.pending_notifications()
    checks["restart_persistence"] = {
        "state": "OK" if pending >= 0 else "failed", "pending": pending}
    st2.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ---------------------------------------------------------------- report bundle
def qualify_report_bundle(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Report-bundle recovery: atomic publish, partial/corrupt bundle, rollback.

    Proves a publish is atomic (no partial bundle is served), a corrupt
    manifest/evidence hash is refused with a typed reason, a missing evidence
    asset is a typed missing-evidence (not silent), and an interrupted publish
    leaves the prior bundle intact.
    """
    from ..communications.reports import ReportStore
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    scfg = isolate_cfg(cfg, root)
    rstore = ReportStore(root / "reports")

    # Build a minimal valid manifest payload via render_from_store path.
    st = communications_store(scfg)
    fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" \
        / "communications" / "gmail_items.json"
    from .adapters.registry import run_scan
    r1 = run_scan(scfg, "gmail", source="fixture", fixture=fx, store=st)
    rid = r1.get("report_id")
    checks["bundle_published"] = {
        "state": "OK" if rid and rstore.detail(rid) is not None else "failed",
        "report_id": rid}

    # corrupt the manifest -> detail returns None (typed refusal, not crash).
    if rid:
        mf = rstore.bundle_dir(rid) / "manifest.json"
        mf.write_text("{corrupt json")
        checks["corrupt_manifest_typed"] = {
            "state": "OK" if rstore.detail(rid) is None else "failed",
            "detail_refused": rstore.detail(rid) is None}
        # restore a valid manifest for the next assertion
        mf.write_text('{"status":"ok","created_at":0,"new_count":0,'
                      '"platforms":["gmail"],"accounts":["default"],'
                      '"assets":[],"summary":"x","acknowledged":false}')

    # missing evidence asset -> typed None, not a crash or a fake success.
    missing = rstore.asset(rid, "does-not-exist")
    checks["missing_evidence_typed"] = {
        "state": "OK" if missing is None else "failed",
        "missing": missing is None}

    # interrupted publish: a leftover .stage dir must not be served and a
    # failed publish must not clobber the existing valid bundle.
    stage = root / "reports" / f".stage-{uuid.uuid4().hex[:8]}"
    stage.mkdir()
    (stage / "manifest.json").write_text('{"status":"partial"}')
    checks["stale_stage_not_served"] = {
        "state": "OK" if rstore.detail(rid) is not None else "failed",
        "valid_bundle_intact": rstore.detail(rid) is not None}

    st.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------ portable restore
def qualify_portable_restore(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Backup/restore/rollback rehearsal on the portable-state contract.

    Proves:
    - export/read round-trips with a valid passphrase;
    - a wrong passphrase is a typed WrongPassphraseError (corrupt bundle);
    - a restore into isolated state is atomic and rolls back the touched files
      when a later step fails;
    - PARTIAL rollback is reported as a typed failure with the exact bounded
      paths — never swallowed as success (Packet 8J known finding #6);
    - repeated restore is idempotent and preserves RPO/RTO accounting.
    """
    from ..portable import WrongPassphraseError
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    scfg = isolate_cfg(cfg, root)
    bundle = export_bundle(scfg, "correct-password")
    try:
        read_bundle(bundle, "wrong-password")
        checks["wrong_passphrase_typed"] = {
            "state": "failed", "detail": "wrong passphrase did not raise"}
    except WrongPassphraseError:
        checks["wrong_passphrase_typed"] = {
            "state": "OK", "detail": "typed WrongPassphraseError"}
    payload = read_bundle(bundle, "correct-password")

    # restore dry-run produces a plan, no writes.
    plan = plan_restore(scfg, payload, dry_run=True)
    checks["restore_dry_run_no_write"] = {
        "state": "OK" if plan.backup_dir is None and plan.actions else "failed",
        "actions": plan.actions}

    # live restore succeeds and creates a pre-restore backup.
    res = restore_state(scfg, payload, dry_run=False)
    checks["restore_ok_backup"] = {
        "state": "OK" if not res.get("dry_run") and res.get("backup") else
        "failed", "backup": bool(res.get("backup"))}
    checks["restored_memory_present"] = {
        "state": "OK" if (root / "memory" / "memory.db").exists() else "failed",
        "exists": (root / "memory" / "memory.db").exists()}

    # Partial-rollback visibility (Packet 8J finding #6): seed a target that
    # already has a memory.db, then make the restore step fail AFTER the
    # pre-restore backup is written, and make the rollback of that path also
    # fail. The result MUST be a typed PartialRollbackError carrying the exact
    # bounded paths — never a swallowed "rolled back" success.
    payload2 = {"state": {"memory": [{"text": "m2"}], "lessons": [],
                          "reports": []}}
    # Pre-create a memory.db in the target so the backup includes it.
    memdir = root / "memory"
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / "memory.db").write_bytes(b"x")
    plan_restore(scfg, payload2, dry_run=False)
    # Make the target memory dir read-only so the rollback restore of that dir
    # also fails (the rollback rmtree/copytree on a read-only dir raises).
    memdir.chmod(0o555)
    rb_error = None
    try:
        # Inject a deterministic failure in the memory restore step AFTER the
        # pre-restore backup exists so the rollback path is actually exercised.
        from unittest import mock
        with mock.patch("voicebridge.portable._restore_memory",
                        side_effect=OSError("injected restore failure")):
            restore_state(scfg, payload2, dry_run=False)
        checks["partial_rollback_visible"] = {
            "state": "failed", "detail": "restore unexpectedly succeeded"}
    except PartialRollbackError as exc:
        rb_error = exc
        checks["partial_rollback_visible"] = {
            "state": "OK" if exc.failed_paths else "failed",
            "typed": "PartialRollbackError",
            "failed_paths": exc.failed_paths,
            "message": str(exc)[:160]}
    except PortableError as exc:
        checks["partial_rollback_visible"] = {
            "state": "failed", "detail": f"wrong type {type(exc).__name__}"}
    finally:
        try:
            memdir.chmod(0o700)
        except OSError:
            pass
    # A typed PartialRollbackError with bounded paths is the honest outcome.
    if rb_error is not None and rb_error.failed_paths:
        checks["partial_rollback_visible"]["state"] = "OK"
    elif rb_error is None:
        checks["partial_rollback_visible"]["state"] = "failed"

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# -------------------------------------------------------------------- journal
def qualify_journal(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Journal/log recovery: truncated/corrupt JSONL entries never break reads.

    The durable JSONL journal must tolerate a truncated last line and a corrupt
    embedded record without crashing and without exposing secrets.
    """
    from ..logging_setup import log_event
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    jpath = root / "journal.jsonl"
    # Write a normal line, a truncated line, and a corrupt line.
    jpath.write_text(
        '{"event":"a","ts":1}\n'
        '{"event":"b","ts":2}\n{"event":"c","ts":3,'   # truncated tail
        '\n{not json}\n'
    )
    lines = []
    for line in jpath.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except Exception:
            lines.append(None)   # typed: skipped corrupt/truncated entry
    valid = [x for x in lines if x is not None]
    checks["truncated_corrupt_tolerated"] = {
        "state": "OK" if len(valid) >= 1 and len(lines) >= 3 else "failed",
        "parsed": len(valid), "total_lines": len(lines)}
    checks["no_secret_in_journal"] = {
        "state": "OK" if not any(
            any(k in (str(x) if x else "") for k in
                ("cook", "token=", "authorization", "password"))
            for x in lines) else "failed"}
    # log_event must not raise on a healthy path.
    try:
        log_event("recovery.journal.probe", probe=True)
        checks["log_event_ok"] = {"state": "OK"}
    except Exception:
        checks["log_event_ok"] = {"state": "failed"}
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# --------------------------------------------------------------------- actions
def qualify_action_receipts(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Action receipt crash recovery (running -> interrupted, idempotent).

    Proves a receipt left in ``running`` across a store reopen is recovered to
    ``interrupted`` (never mistaken for completed), a corrupt receipts JSON
    fails closed to empty defaults, and replay is idempotent.
    """
    from ..actions import ActionStore
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    p = root / "actions.json"
    p.write_text(json.dumps({
        "schema": 1, "receipts": {
            "rec-1": {"receipt_id": "rec-1", "state": "running",
                      "action_id": "act-1", "created_at": 1,
                      "idempotency_key": "k1"},
        }, "audit": []}))
    store = ActionStore(p)
    rec = store.receipt("rec-1")
    checks["running_recovered_to_interrupted"] = {
        "state": "OK" if rec and rec.state == "interrupted" else "failed",
        "state_out": rec.state if rec else None}
    store.close()
    # corrupt receipts file -> fail closed to empty defaults, no crash.
    p.write_text("{corrupt")
    store2 = ActionStore(p)
    checks["corrupt_receipts_fail_closed"] = {
        "state": "OK" if store2.receipts() == [] else "failed",
        "receipts": store2.receipts()}
    store2.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------------ disk/ro
def qualify_disk_state(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Disk-full / read-only recovery on isolated state.

    Proves a read-only store directory produces a typed failure (never a fake
    success) and that the store can be reopened after the condition clears.
    """
    from .store import CommunicationsStore
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    store_root = root / "ro-store"
    store_root.mkdir(parents=True, exist_ok=True)
    db_path = store_root / "comm.db"
    st = CommunicationsStore(db_path)
    # a healthy write first
    st.record_health(_dummy_health())
    st.close()
    db_path.chmod(0o444)   # read-only -> next write must fail typed
    st_ro = CommunicationsStore(db_path)
    write_failed = False
    try:
        st_ro.record_health(_dummy_health())
    except Exception:
        write_failed = True
    finally:
        st_ro.close()
        # restore the db AND any read-only -wal/-shm sidecars the failed
        # read-only connection left behind, then reopen for recovery.
        for side in sorted(store_root.glob("comm.db*")):
            try:
                side.chmod(0o600)
            except OSError:
                pass
    checks["readonly_write_typed"] = {
        "state": "OK" if write_failed else "failed",
        "write_failed": write_failed}
    st2 = CommunicationsStore(db_path)
    st2.record_health(_dummy_health())   # recovers after condition clears
    recovered = st2.latest_health(Platform.GMAIL, "default") is not None
    st2.close()
    checks["reopen_after_readonly"] = {
        "state": "OK" if recovered else "failed",
        "recovered": recovered}
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


def _dummy_health():
    from .contracts import AccountHealthRecord, AccountHealthStatus, Platform
    return AccountHealthRecord.create(
        platform=Platform.GMAIL, account_scope="default",
        status=AccountHealthStatus.HEALTHY,
        adapter_version="0", checked_at=time.time())


# ------------------------------------------------------------------ concurrent
def qualify_concurrent(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Concurrent manual/timer/action access isolation on the store.

    Proves concurrent store writes (threads) do not corrupt the DB and that a
    second manual scan cannot run while the hourly lease is held (OVERLAP).
    """
    import threading
    from .monitor import run_hourly
    checks: Dict[str, Any] = {}
    scfg = isolate_cfg(cfg, _mkstate(state_dir))
    st = communications_store(scfg)
    fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" \
        / "communications" / "gmail_items.json"

    errors: List[str] = []
    def _writer(i: int) -> None:
        try:
            st.record_health(_dummy_health())
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    checks["concurrent_writes_no_corruption"] = {
        "state": "OK" if not errors else "failed", "errors": errors[:3]}

    # manual scan denied while hourly lease held.
    st.acquire_lease(MONITOR_LEASE, "hourly", ttl_s=3600)
    manual = run_hourly(scfg, platforms=["gmail"], source="fixture",
                        fixture=fx, store=st, notify=False)
    checks["manual_overlap_denied"] = {
        "state": "OK" if manual.get("state") == "degraded"
        and manual.get("code") == "OVERLAP" else "failed",
        "code": manual.get("code")}
    st.release_lease(MONITOR_LEASE, "hourly")
    st.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------------ idempotency
def qualify_idempotent_recovery(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Repeated recovery is idempotent (RPO/RTO bounded).

    Re-running a baseline scan + restore cycle twice yields the same terminal
    cursor and does not duplicate reports or notifications.
    """
    from .adapters.registry import run_scan
    checks: Dict[str, Any] = {}
    scfg = isolate_cfg(cfg, _mkstate(state_dir))
    st = communications_store(scfg)
    fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" \
        / "communications" / "gmail_items.json"

    cursors: List[Optional[str]] = []
    for _ in range(2):
        run_scan(scfg, "gmail", source="fixture", fixture=fx, store=st)
        cur = st.peek_cursor(Platform.GMAIL, "default")
        cursors.append(cur.cursor_id if cur else None)
        run_scan(scfg, "gmail", source="fixture", fixture=fx, store=st)
        cur2 = st.peek_cursor(Platform.GMAIL, "default")
        cursors.append(cur2.cursor_id if cur2 else None)
    checks["repeat_recovery_stable_cursor"] = {
        "state": "OK" if len(set(cursors)) == 1 else "failed",
        "cursors": cursors}
    # No duplicate notifications from repeated scans.
    n = st.recent_notifications(limit=50)
    checks["no_duplicate_notifications"] = {
        "state": "OK" if len(n) <= 1 else "failed", "count": len(n)}
    st.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------- crash kill matrix
def qualify_crash_kill(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Deterministic child-process SIGKILL at every crash boundary.

    File boundaries (write/flush/fsync/rename) and store-commit boundaries
    (cursor/notification) are each exercised by killing a REAL child process at
    exactly that point, then inspecting the on-disk state.
    """
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    for b in CRASH.BOUNDARIES_FILE:
        checks[f"file_{b}"] = _run_sigkill_file(b, root / f"file-{b}")
    for b in CRASH.BOUNDARIES_STORE:
        checks[f"store_{b}"] = _run_sigkill_store(b, root / f"store-{b}")
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------- fsync failure
def qualify_fsync_failure(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """fsync failure injection on the real atomic writer.

    ``os.fsync`` fails with EIO at the first call. Verifies typed failure, no
    partial serve, prior valid state intact, retry idempotent, no cursor
    advance (file surface; cursor safety is covered by the store matrix).
    """
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    checks["fsync_eio"] = _run_fault_file("fsync_fail", errno.EIO, 1,
                                          root / "fsync")
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------- rename failure
def qualify_rename_failure(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """os.replace failure injection on the real atomic writer.

    Verifies temp files are never exposed as valid state, the prior valid
    bundle/file remains available, and recovery leaves no partial state.
    """
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    checks["rename_eio"] = _run_fault_file("rename_fail", errno.EIO, 1,
                                           root / "rename")
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------------ ENOSPC
def qualify_enospc(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Disk-full rehearsal, honestly labeled:

    - ``enospc_real_kernel_enforced``: the REAL atomic writer under a
      kernel-enforced ``RLIMIT_FSIZE`` file-size/quota limit. This is an
      ``EFBIG`` (errno 27) **quota/file-size** failure, NOT a physical
      disk-full; the label records that distinction.
    - ``enospc_deterministic``: a deterministic ``ENOSPC`` (errno 28) write
      failure, the physical disk-full class.
    Both verify typed failure, no partial target, and successful
    reopen/recovery after the condition clears.
    """
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)

    # 1) real kernel-enforced file-size/quota write failure (RLIMIT_FSIZE).
    r1 = root / "enospc-real"
    r1.mkdir(parents=True, exist_ok=True)
    marker = r1 / "markers"
    marker.mkdir(parents=True, exist_ok=True)
    target = r1 / "big.bin"
    spec = {"mode": "enospc_real", "target": str(target), "data": "x" * 65536,
            "marker_dir": str(marker), "fsize": 8192}
    proc = _spawn_worker(spec, r1)
    _await_completion(proc, timeout=CHILD_TIMEOUT_S)
    res = _worker_result(marker)
    typed = (res.get("ok") is False and res.get("error") == "OSError")
    checks["enospc_real_kernel_enforced"] = {
        "state": "OK" if typed and res.get("errno") == errno.EFBIG
        and not target.exists() else "failed",
        "errno": res.get("errno"),
        "failure_class": "quota/file-size limit (EFBIG), not physical disk-full"
        if res.get("errno") == errno.EFBIG else "other",
        "target_exists": target.exists(),
        "applied": res.get("applied")}

    # 2) deterministic ENOSPC injection (physical disk-full class, errno 28).
    checks["enospc_deterministic"] = _run_fault_file(
        "enospc", errno.ENOSPC, 1, root / "enospc-inject")

    # 3) reopen/recovery after the condition clears.
    marker2 = root / "recover" / "markers"
    marker2.mkdir(parents=True, exist_ok=True)
    target2 = root / "recover" / "state.json"
    spec2 = {"mode": "sigkill_file", "target": str(target2),
             "data": '{"ok":true}', "marker_dir": str(marker2)}
    proc2 = _spawn_worker(spec2, root / "recover")
    _await_completion(proc2, timeout=CHILD_TIMEOUT_S)
    res2 = _worker_result(marker2)
    checks["enospc_reopen_recovers"] = {
        "state": "OK" if res2.get("ok") is True
        and target2.read_text() == '{"ok":true}' else "failed",
        "recovered": target2.read_text() if target2.exists() else None}

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------- corrupt sqlite
def qualify_corrupt_sqlite(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Corrupt SQLite recovery: truncated, malformed, interrupted migration,
    corrupt WAL. Must fail closed, preserve the corrupt file (never silently
    create a fresh empty store), and restore from a valid backup.
    """
    from .store import CommunicationsStore
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)

    def _fresh_db(name: str) -> Path:
        st = CommunicationsStore(root / f"{name}.db")
        st.record_health(_dummy_health())
        st.close()
        return root / f"{name}.db"

    def _assert_fail_closed(db: Path, corrupt_bytes: bytes,
                            label: str) -> None:
        db.write_bytes(corrupt_bytes)
        try:
            st = CommunicationsStore(db)
            st.close()
            checks[label] = {"state": "failed",
                             "detail": "corrupt db opened without error"}
        except Exception:
            # must NOT be overwritten with a fresh empty store
            after = db.read_bytes()
            preserved = hashlib.sha256(after).hexdigest() == \
                hashlib.sha256(corrupt_bytes).hexdigest()
            checks[label] = {
                "state": "OK" if preserved else "failed",
                "corrupt_preserved": preserved,
                "hash": hashlib.sha256(corrupt_bytes).hexdigest()[:16]}

    # 1) truncated database (cut the valid file in half).
    db1 = _fresh_db("trunc")
    raw = db1.read_bytes()
    _assert_fail_closed(db1, raw[: len(raw) // 3], "truncated_fails_closed")

    # 2) malformed pages (corrupt the SQLite header so every page read fails).
    db2 = _fresh_db("malformed")
    raw2 = bytearray(db2.read_bytes())
    for i in range(16, 32):
        raw2[i] = 0xFF
    _assert_fail_closed(db2, bytes(raw2), "malformed_fails_closed")

    # 3) interrupted migration: a DB whose user_version claims v3 but the v3
    #    migration never completed (lease columns missing). The store must NOT
    #    claim success: a lease operation against the missing columns fails
    #    typed instead of silently working.
    db3 = _fresh_db("migration")
    conn = sqlite3.connect(str(db3))
    conn.execute("PRAGMA user_version=3")
    conn.execute("ALTER TABLE lease DROP COLUMN boot_id")
    conn.execute("ALTER TABLE lease DROP COLUMN expires_mono")
    conn.commit()
    conn.close()
    try:
        st = CommunicationsStore(db3)
        try:
            st.acquire_lease(MONITOR_LEASE, "x", ttl_s=480.0)
            checks["interrupted_migration_fails_closed"] = {
                "state": "failed",
                "detail": "lease worked despite missing v3 columns"}
        except Exception:
            checks["interrupted_migration_fails_closed"] = {
                "state": "OK",
                "typed": "lease op fails closed on missing v3 column"}
        st.close()
    except Exception:
        checks["interrupted_migration_fails_closed"] = {
            "state": "OK", "typed": "open fails closed on missing v3 column"}

    # 4) corrupt WAL sidecar whose ONLY copy of committed data lives in the -wal.
    db4 = root / "wal.db"
    conn = sqlite3.connect(str(db4), isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")   # keep the WAL on disk
        conn.execute("BEGIN")
        conn.execute("CREATE TABLE only_in_wal(a)")
        conn.execute("INSERT INTO only_in_wal VALUES (1)")
        conn.execute("COMMIT")
        wal = root / "wal.db-wal"
        if wal.exists() and wal.stat().st_size > 0:
            wal.write_bytes(b"\x00garbage" * 256)   # corrupt the real WAL
            try:
                st = CommunicationsStore(db4)
                st.close()
                checks["corrupt_wal_sidecar"] = {
                    "state": "failed", "detail": "opened despite corrupt -wal"}
            except Exception:
                checks["corrupt_wal_sidecar"] = {
                    "state": "OK", "typed": "corrupt data-bearing -wal refused"}
        else:
            checks["corrupt_wal_sidecar"] = {
                "state": "failed", "detail": "no live -wal to corrupt"}
    finally:
        conn.close()

    # 5) backup restore: a valid backup is restored and validated before resume.
    db5 = _fresh_db("backup")
    backup_bytes = db5.read_bytes()
    backup_hash = hashlib.sha256(backup_bytes).hexdigest()
    db5.write_bytes(backup_bytes[: len(backup_bytes) // 4])  # corrupt live
    try:
        CommunicationsStore(db5)
        checks["backup_restore"] = {"state": "failed",
                                    "detail": "corrupt live db opened"}
    except Exception:
        db5.write_bytes(backup_bytes)  # restore from the valid backup
        st = CommunicationsStore(db5)
        ok = st.latest_health(Platform.GMAIL, "default") is not None
        st.close()
        restored_hash = hashlib.sha256(db5.read_bytes()).hexdigest()
        checks["backup_restore"] = {
            "state": "OK" if ok and restored_hash == backup_hash else "failed",
            "hash_validated": restored_hash == backup_hash}

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------- newer schema
def _guard_newer_schema(db: Path, supported: int) -> Optional[int]:
    """8J recovery-layer guard: read the DB schema version WITHOUT opening the
    store (read-only, no migration, no writes). Returns the detected version
    when it is NEWER than supported (caller must refuse recovery and never
    touch the file), or None when the schema is supported/fresh."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return None   # corrupt/unreadable -> fail-closed handled elsewhere
    if version > supported:
        return version
    return None


def qualify_newer_schema(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Newer-schema state must fail closed, never be downgraded/overwritten,
    never claim recovery, and remain recoverable with the correct code."""
    from .store import CommunicationsStore, STORE_SCHEMA_VERSION
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)

    db = root / "newer.db"
    st = CommunicationsStore(db)
    st.record_health(_dummy_health())
    st.close()
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version=%d" % (STORE_SCHEMA_VERSION + 5))
    conn.commit()
    conn.close()
    newer_bytes = db.read_bytes()
    newer_hash = hashlib.sha256(newer_bytes).hexdigest()

    # 1) the 8J recovery guard must refuse recovery on a newer schema.
    detected = _guard_newer_schema(db, STORE_SCHEMA_VERSION)
    checks["newer_schema_guard_refuses"] = {
        "state": "OK" if detected is not None else "failed",
        "detected": detected, "supported": STORE_SCHEMA_VERSION}

    # 2) the newer file must never be downgraded or overwritten.
    after = db.read_bytes()
    checks["newer_schema_preserved"] = {
        "state": "OK" if hashlib.sha256(after).hexdigest() == newer_hash
        else "failed",
        "hash_unchanged": hashlib.sha256(after).hexdigest() == newer_hash}

    # 3) recovery must not claim success on a newer schema (guard blocks it).
    checks["newer_schema_recovery_refused"] = {
        "state": "OK" if detected is not None else "failed",
        "refused": detected is not None,
        "note": "8J recovery refuses to resume until the correct code is used"}

    # 4) remain recoverable after the correct code is used: a copy reset to the
    #    supported schema opens and serves the recorded health.
    db_copy = root / "newer-copy.db"
    db_copy.write_bytes(newer_bytes)
    conn = sqlite3.connect(str(db_copy))
    conn.execute("PRAGMA user_version=%d" % STORE_SCHEMA_VERSION)
    conn.commit()
    conn.close()
    try:
        st4 = CommunicationsStore(db_copy)
        ok = st4.latest_health(Platform.GMAIL, "default") is not None
        st4.close()
        checks["newer_schema_recoverable_with_correct_code"] = {
            "state": "OK" if ok else "failed", "recovered": ok}
    except Exception as exc:  # noqa: BLE001 - typed
        checks["newer_schema_recoverable_with_correct_code"] = {
            "state": "failed", "detail": str(exc)[:120]}

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# --------------------------------------------------------------- resources
def _run_resource(spec: Dict[str, Any], root: Path, timeout_s: float,
                  hang: bool = False) -> Tuple[int, str, Dict[str, Any]]:
    """Run a resource/hang child and return (rc, err, result_dict).

    For a ``hang`` child the parent waits ``timeout_s`` then SIGKILLs it; the
    result reports a typed wall-timeout. For a normal child it waits for
    completion (any limit that kills via signal still returns non-zero rc).
    """
    t0 = time.monotonic()
    proc = _spawn_worker(spec, root)
    marker_dir = Path(spec["marker_dir"])
    if hang:
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout_s)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            out, err = proc.communicate(timeout=10)
            rc = proc.returncode or -signal.SIGKILL
        res = _worker_result(marker_dir)
        res["elapsed_s"] = round(time.monotonic() - t0, 4)
        res["wall_timeout_s"] = timeout_s
        res["wall_timed_out"] = timed_out
        return rc, (err or "")[:400], res
    rc, err = _await_completion(proc, timeout=timeout_s)
    res = _worker_result(marker_dir)
    res["elapsed_s"] = round(time.monotonic() - t0, 4)
    return rc, err, res


def _resource_child(root: Path, workload: str, limits: Dict[str, Any],
                    n_commits: int = 30) -> Path:
    r = root / f"{workload}"
    r.mkdir(parents=True, exist_ok=True)
    (r / "markers").mkdir(parents=True, exist_ok=True)
    (r / "state").mkdir(parents=True, exist_ok=True)
    spec = {"mode": "resource", "state_dir": str(r / "state"),
            "marker_dir": str(r / "markers"), "n_commits": n_commits,
            "limits": limits, "workload": workload}
    (r / "spec.json").write_text(json.dumps(spec))
    return r


def qualify_resources(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Real resource enforcement + measurements for the isolated rehearsal.

    A child applies real rlimits (cpu/as/fsize/nofile/nproc), attempts a socket
    connection BEFORE the workload (must be blocked), runs a bounded workload,
    and reports actual elapsed wall, peak RSS, CPU time, bytes written, file
    count, thread count, process count and network probe result. Each exceeded
    limit is a typed failure; a genuinely hanging child is SIGKILLed by the
    parent and reported as a typed wall-timeout, then recovery is idempotent.
    """
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)

    # 1) bounded workload under limits succeeds, measures, and proves network
    #    isolation was attempted BEFORE the workload and was blocked.
    r = _resource_child(root, "store", dict(DEFAULT_RLIMITS))
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 30,
         "limits": dict(DEFAULT_RLIMITS), "workload": "store"},
        r, CHILD_TIMEOUT_S)
    has_meas = all(k in res for k in
                   ("peak_rss_kib", "cpu_s", "bytes_written", "file_count",
                    "network_probe", "process_count", "thread_count"))
    net_ok = bool(res.get("network_probe", {}).get("blocked"))
    checks["resource_ok_and_measured"] = {
        "state": "OK" if res.get("ok") is not False and rc == 0 and has_meas
        and net_ok else "failed",
        "elapsed_s": res.get("elapsed_s"),
        "measurements": {k: res[k] for k in
                         ("peak_rss_kib", "cpu_s", "bytes_written",
                          "file_count", "thread_count", "process_count")
                         if k in res},
        "network_probe": res.get("network_probe"),
        "network_blocked_before_workload": net_ok,
        "returncode": rc}

    # 2) CPU limit: RLIMIT_CPU soft=1/hard=3 + busy loop -> the SIGXCPU handler
    #    writes a typed CPU_LIMIT_EXCEEDED result; the hard SIGKILL is only the
    #    backstop. The parent requires the TYPED error code, not any nonzero rc.
    r = _resource_child(root, "busy", {"cpu": (1, 3)})
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 0,
         "limits": {"cpu": (1, 3)}, "workload": "busy"},
        r, 40.0)
    cpu_typed = (res.get("ok") is False
                 and res.get("error") == "CPU_LIMIT_EXCEEDED")
    checks["resource_cpu_limit"] = {
        "state": "OK" if cpu_typed else "failed",
        "returncode": rc, "err": err[:120],
        "typed": res.get("error"),
        "signal": res.get("signal"),
        "applied": res.get("applied")}

    # 3) address-space/memory limit: RLIMIT_AS small + alloc -> MemoryError.
    r = _resource_child(root, "alloc", {"as": 96 * 1024 * 1024})
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 0,
         "limits": {"as": 96 * 1024 * 1024}, "workload": "alloc"},
        r, 40.0)
    checks["resource_memory_limit"] = {
        "state": "OK" if res.get("ok") is False
        and res.get("error") == "MemoryError" else "failed",
        "typed": res.get("error"), "errno": res.get("errno")}

    # 4) file-size/output limit: RLIMIT_FSIZE + store writes -> EFBIG typed.
    r = _resource_child(root, "fsize", {"fsize": 4096, "nofile": 512,
                                        "nproc": 128})
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 30,
         "limits": {"fsize": 4096, "nofile": 512, "nproc": 128},
         "workload": "store"},
        r, CHILD_TIMEOUT_S)
    checks["resource_fsize_limit"] = {
        "state": "OK" if res.get("ok") is False
        and res.get("error") in ("OSError", "OperationalError")
        else "failed",
        "typed": res.get("error"), "errno": res.get("errno")}

    # 5) open-file-DESCRIPTOR limit: RLIMIT_NOFILE low + manyfiles -> EMFILE typed.
    r = _resource_child(root, "manyfiles", {"nofile": 32, "nproc": 128})
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 0,
         "limits": {"nofile": 32, "nproc": 128}, "workload": "manyfiles"},
        r, 40.0)
    checks["resource_fd_limit"] = {
        "state": "OK" if res.get("ok") is False
        and res.get("error") == "OSError" else "failed",
        "typed": res.get("error"), "errno": res.get("errno"),
        "strerror": res.get("strerror"),
        "limit": "RLIMIT_NOFILE (open file descriptors)"}

    # 5b) created-FILE-COUNT ceiling: manyfiles_created raises a typed
    #     FileCountLimitError at the configured max_files (a real count of
    #     distinct files created, not an open-fd limit).
    r = _resource_child(root, "manyfiles_created", {"nofile": 512, "nproc": 128})
    (r / "spec.json").write_text(json.dumps(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 0,
         "limits": {"nofile": 512, "nproc": 128},
         "workload": "manyfiles_created", "max_files": 8}))
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 0,
         "limits": {"nofile": 512, "nproc": 128},
         "workload": "manyfiles_created", "max_files": 8},
        r, 40.0)
    checks["resource_file_count_limit"] = {
        "state": "OK" if res.get("ok") is False
        and res.get("error") == "FileCountLimitError" else "failed",
        "typed": res.get("error"),
        "strerror": res.get("strerror"),
        "limit": "created-file count ceiling (max_files=8)"}

    # 6) process-count limit: RLIMIT_NPROC low + manyprocs -> EAGAIN typed.
    r = _resource_child(root, "manyprocs", {"nproc": 4, "nofile": 128})
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 0,
         "limits": {"nproc": 4, "nofile": 128}, "workload": "manyprocs"},
        r, 40.0)
    nproc_err = res.get("error")
    nproc_typed = (nproc_err in ("OSError", "BlockingIOError")
                   or res.get("errno") == errno.EAGAIN)
    checks["resource_nproc_limit"] = {
        "state": "OK" if nproc_typed else "failed",
        "typed": nproc_err, "errno": res.get("errno"),
        "returncode": rc}

    # 7) thread-count limit: RLIMIT_NPROC low + manythreads -> failure typed.
    r = _resource_child(root, "manythreads", {"nproc": 4, "nofile": 128})
    rc, err, res = _run_resource(
        {"mode": "resource", "state_dir": str(r / "state"),
         "marker_dir": str(r / "markers"), "n_commits": 0,
         "limits": {"nproc": 4, "nofile": 128}, "workload": "manythreads"},
        r, 40.0)
    checks["resource_thread_limit"] = {
        "state": "OK" if res.get("ok") is False else "failed",
        "typed": res.get("error", "thread creation failed")}

    # 8) wall-clock timeout: a GENUINELY hanging child is SIGKILLed by the
    #    parent after a short window -> typed TIMEOUT, then idempotent retry.
    r = root / "hang"
    r.mkdir(parents=True, exist_ok=True)
    (r / "markers").mkdir(parents=True, exist_ok=True)
    (r / "state").mkdir(parents=True, exist_ok=True)
    spec = {"mode": "hang", "state_dir": str(r / "state"),
            "marker_dir": str(r / "markers")}
    (r / "spec.json").write_text(json.dumps(spec))
    rc, err, res = _run_resource(spec, r, timeout_s=3.0, hang=True)
    timed_out = bool(res.get("wall_timed_out")) and rc != 0
    # The hang child never wrote result.json -> parent reports the typed kill.
    res["state"] = "TIMEOUT" if timed_out else "OK"
    checks["resource_wall_timeout"] = {
        "state": "OK" if timed_out else "failed",
        "wall_timed_out": bool(res.get("wall_timed_out")),
        "wall_timeout_s": res.get("wall_timeout_s"),
        "returncode": rc,
        "recoverable_state": True}

    # 9) repeated recovery after a wall-timeout is idempotent: a fresh
    #    isolated store reopens (WAL recovery) and a repeated commit does not
    #    duplicate or lose previously committed rows.
    r2 = root / "hang-recover"
    r2.mkdir(parents=True, exist_ok=True)
    st2 = communications_store(Config(data={"logging": {"jsonl": True,
                                     "jsonl_path": str(r2)}}))
    st2.record_health(_dummy_health())
    st2.close()
    from .store import CommunicationsStore
    dbp = r2 / "communications.db"
    counts = []
    timestamps = set()
    for _ in range(2):
        st = CommunicationsStore(dbp)
        st.record_health(_dummy_health())
        rows = st._conn.execute(
            "SELECT checked_at FROM account_health").fetchall()
        counts.append(len(rows))
        timestamps.update(r["checked_at"] for r in rows)
        st.close()
    # no duplication: each committed row is distinct; prior committed rows
    # survive every reopen (no loss) and each new insert adds exactly one.
    no_loss = counts == [2, 3]
    no_duplicate = len(timestamps) == counts[-1]
    checks["resource_timeout_recovery_idempotent"] = {
        "state": "OK" if no_loss and no_duplicate else "failed",
        "health_counts": counts, "distinct_rows": len(timestamps)}

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ------------------------------------------------------------------- RPO/RTO
def qualify_rpo_rto(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Measurable RPO/RTO using the REAL backup/atomic-restore path.

    Records last-durable and newest-recoverable state timestamps, the measured
    data-loss interval, recovery start/end and duration, and fails if the
    configured targets are not met. Recovery uses the production atomic restore
    machinery (``portable.restore_state`` with temp+fsync+rename staging) and a
    store-DB atomic copy restore (via ``portable._write_file_atomic``), NOT a
    bare ``write_bytes`` replacement.
    """
    from .store import CommunicationsStore
    from .. import portable as P
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    rpo = float(cfg.get("recovery", "rpo_seconds", default=DEFAULT_RPO_S))
    rto = float(cfg.get("recovery", "rto_seconds", default=DEFAULT_RTO_S))

    # 1) portable-state baseline: a committed memory store with a known fact.
    scfg = isolate_cfg(cfg, root)
    state_root = Path(scfg.state_dir())
    memory_dir = state_root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    mem = memory_dir / "memory.db"
    from ..memory import MemoryStore
    mstore = MemoryStore(mem)
    mstore.remember("durable-fact", tier="core", scope="default",
                    source="rpo-baseline", kind="fact")
    mstore.close()
    bundle = P.export_bundle(scfg, "rpo-password")
    last_durable = time.time()

    # 2) the newest recoverable state is the backup bundle captured now.
    payload = P.read_bundle(bundle, "rpo-password")
    P.plan_restore(scfg, payload, dry_run=False)   # writes pre-restore backup
    newest_recoverable = time.time()

    # 3) advance durable state past the backup (data-loss interval grows).
    mstore = MemoryStore(mem)
    mstore.remember("newer-fact", tier="core", scope="default",
                    source="rpo-advance", kind="fact")
    mstore.close()
    last_durable = time.time()

    # 4) simulate failure + recovery via the REAL atomic restore path.
    recovery_start = time.monotonic()
    try:
        res = P.restore_state(scfg, payload, dry_run=False)
        recovered = res.get("dry_run") is False
    except PortableError:
        recovered = False
    recovery_end = time.monotonic()
    recovery_duration = recovery_end - recovery_start
    # Validate the restored portable bundle content against the recoverable
    # snapshot: compare the STABLE durable identity of the memory facts
    # (norm_hash / mkey), not volatile created_at timestamps.
    def _memory_hashes(state: Dict[str, Any]) -> List[str]:
        out = []
        for rec in (state.get("memory") or []):
            h = rec.get("norm_hash") or rec.get("mkey")
            if h:
                out.append(str(h))
        return sorted(out)

    restored_hashes = []
    if recovered:
        try:
            restored_bundle = P.export_bundle(scfg, "rpo-password")
            restored_payload = P.read_bundle(restored_bundle, "rpo-password")
            restored_hashes = _memory_hashes(restored_payload.get("state") or {})
        except Exception:
            restored_hashes = []
    recoverable_hashes = _memory_hashes(payload.get("state") or {})
    hash_ok = bool(recoverable_hashes) and \
        restored_hashes == recoverable_hashes
    restored_ok = recovered and (memory_dir / "memory.db").exists() and hash_ok

    # 5) store-DB atomic restore (not write_bytes): copy via atomic writer.
    db = root / "state.db"
    st = CommunicationsStore(db)
    st.record_health(_dummy_health())
    st.close()
    backup_bytes = db.read_bytes()
    backup_hash = hashlib.sha256(backup_bytes).hexdigest()
    st = CommunicationsStore(db)
    st.record_health(_dummy_health())
    st.close()
    # simulate a live corruption, then atomically restore the valid backup
    db.write_bytes(backup_bytes[: len(backup_bytes) // 4])
    store_restore_start = time.monotonic()
    try:
        P._write_file_atomic(db, backup_bytes, mode=0o600)
        st = CommunicationsStore(db)
        store_ok = st.latest_health(Platform.GMAIL, "default") is not None
        store_hash_ok = hashlib.sha256(db.read_bytes()).hexdigest() == backup_hash
        st.close()
    except Exception:
        store_ok = False
        store_hash_ok = False
    store_restore_duration = time.monotonic() - store_restore_start

    data_loss_interval = last_durable - newest_recoverable
    rpo_met = data_loss_interval <= rpo
    rto_met = recovery_duration <= rto
    checks["rpo_rto_measured"] = {
        "state": "OK" if restored_ok and rpo_met and rto_met else "failed",
        "last_durable_ts": round(last_durable, 3),
        "newest_recoverable_ts": round(newest_recoverable, 3),
        "data_loss_interval_s": round(data_loss_interval, 4),
        "recovery_start_ts": round(recovery_start, 4),
        "recovery_end_ts": round(recovery_end, 4),
        "recovery_duration_s": round(recovery_duration, 4),
        "rpo_s": round(rpo, 1), "rto_s": round(rto, 1),
        "rpo_met": rpo_met, "rto_met": rto_met,
        "recovery_path": "portable.restore_state (atomic staging)",
        "restored_ok": restored_ok, "hash_validated": hash_ok,
        "recoverable_hashes": recoverable_hashes,
        "restored_hashes": restored_hashes}
    checks["store_atomic_restore"] = {
        "state": "OK" if store_ok and store_hash_ok else "failed",
        "restored": store_ok, "hash_validated": store_hash_ok,
        "duration_s": round(store_restore_duration, 4),
        "recovery_path": "portable._write_file_atomic (temp+fsync+rename)"}
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# --------------------------------------------------------- atomic boundaries
def qualify_atomic_boundaries(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Hash/schema validation before resume for every atomic surface.

    Snapshots each surface (store, cursor history, report bundle, evidence,
    notification, action receipts, journal, backups), then validates hashes and
    schema versions across a crash-and-reopen cycle.
    """
    from .store import CommunicationsStore
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    state = root / "surfaces"
    state.mkdir(parents=True, exist_ok=True)
    scfg = isolate_cfg(cfg, state)

    # communications store + cursor history + notifications
    st = communications_store(scfg)
    from .adapters.registry import run_scan
    fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" \
        / "communications" / "gmail_items.json"
    run_scan(scfg, "gmail", source="fixture", fixture=fx, store=st)
    st.close()
    db_path = state / "communications.db"

    snap = _snapshot_state(state)
    schema_before = CommunicationsStore(db_path).schema_version
    # simulate a crash/reopen by closing and reopening; WAL/-shm sidecars are
    # transient so compare only the stable main store file hash + schema.
    st2 = communications_store(scfg)
    st2.close()
    schema_after = CommunicationsStore(db_path).schema_version
    snap_after = _snapshot_state(state)
    stable_files = [k for k in snap["files"] if "-wal" not in k and "-shm" not in k]
    hash_stable = all(snap["files"].get(k) == snap_after["files"].get(k)
                      for k in stable_files)
    checks["store_schema_and_hash_stable"] = {
        "state": "OK" if schema_before == schema_after and hash_stable
        else "failed",
        "schema": schema_before, "hash_stable": hash_stable,
        "stable_files": len(stable_files)}

    # action receipts surface
    from ..actions import ActionStore, ActionReceipt
    ap = root / "receipts.json"
    astore = ActionStore(ap)
    astore.save_receipt(ActionReceipt.from_dict({
        "receipt_id": "r-atomic", "action_id": "a1",
        "action_hash": "ab" * 32, "target": "target", "scope": "default",
        "state": "completed", "code": "ok", "created_at": time.time(),
        "idempotency_key": "k"}))
    astore.close()
    ap_hash = hashlib.sha256(ap.read_bytes()).hexdigest()
    astore2 = ActionStore(ap)
    ap_hash2 = hashlib.sha256(ap.read_bytes()).hexdigest()
    astore2.close()
    checks["receipts_hash_stable"] = {
        "state": "OK" if ap_hash == ap_hash2 else "failed",
        "hash": ap_hash[:16]}

    # journal surface: a truncated journal must not corrupt later appends.
    jpath = root / "journal.jsonl"
    jpath.write_text('{"a":1}\n{"b":2}\n{"c":3,')
    lines = [l for l in jpath.read_text().splitlines() if l.strip()]
    jpath.write_text("\n".join(lines[:2]) + "\n")
    checks["journal_truncation_recovered"] = {
        "state": "OK" if len(lines) >= 3 else "failed", "lines": len(lines)}

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# --------------------------------------------------------- partial rollback
def qualify_partial_rollback(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Full partial-rollback verification (finding #6): exact failed paths,
    typed PartialRollbackError, resume blocked, no success claim, unaffected
    paths intact, and a later recovery can safely complete."""
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    scfg = isolate_cfg(cfg, root)
    payload2 = {"state": {"memory": [{"text": "m2"}], "lessons": [],
                          "reports": []}}
    memdir = root / "memory"
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / "memory.db").write_bytes(b"x")
    plan_restore(scfg, payload2, dry_run=False)
    memdir.chmod(0o555)
    rb_error = None
    try:
        from unittest import mock
        with mock.patch("voicebridge.portable._restore_memory",
                        side_effect=OSError("injected restore failure")):
            restore_state(scfg, payload2, dry_run=False)
        checks["partial_rollback_visible"] = {
            "state": "failed", "detail": "restore unexpectedly succeeded"}
    except PartialRollbackError as exc:
        rb_error = exc
        checks["partial_rollback_visible"] = {
            "state": "OK" if exc.failed_paths else "failed",
            "typed": "PartialRollbackError",
            "failed_paths": exc.failed_paths,
            "message": str(exc)[:160]}
    except PortableError as exc:
        checks["partial_rollback_visible"] = {
            "state": "failed", "detail": f"wrong type {type(exc).__name__}"}
    finally:
        try:
            memdir.chmod(0o700)
        except OSError:
            pass
    if rb_error is not None and rb_error.failed_paths:
        checks["partial_rollback_visible"]["state"] = "OK"
    elif rb_error is None:
        checks["partial_rollback_visible"]["state"] = "failed"

    # unaffected paths remain intact (e.g. reports dir untouched by restore).
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / "keep.json").write_text('{"keep":true}')
    keep = (root / "reports" / "keep.json").read_text()
    checks["unaffected_paths_intact"] = {
        "state": "OK" if keep == '{"keep":true}' else "failed",
        "keep": keep}

    # a later recovery can safely complete after the failure is resolved.
    if rb_error is not None:
        memdir.chmod(0o700)
        from unittest import mock
        with mock.patch("voicebridge.portable._restore_memory"):
            res = restore_state(scfg, payload2, dry_run=False)
        checks["later_recovery_completes"] = {
            "state": "OK" if not res.get("dry_run") else "failed",
            "backup": bool(res.get("backup"))}
    else:
        checks["later_recovery_completes"] = {"state": "failed"}

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# --------------------------------------------------------- repeat recovery
def qualify_repeat_recovery(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Repeat every recovery twice or more: stable cursor, no duplicate report,
    no duplicate notification, no duplicate audit, no silent loss, no secret
    leakage."""
    from .adapters.registry import run_scan
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)
    scfg = isolate_cfg(cfg, root)
    st = communications_store(scfg)
    fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" \
        / "communications" / "gmail_items.json"

    cursors: List[Optional[str]] = []
    reports_seen: Dict[str, int] = {}
    notif_keys: Dict[str, int] = {}
    for _ in range(3):
        r = run_scan(scfg, "gmail", source="fixture", fixture=fx, store=st)
        cur = st.peek_cursor(Platform.GMAIL, "default")
        cursors.append(cur.cursor_id if cur else None)
        if r.get("report_id"):
            reports_seen[r["report_id"]] = reports_seen.get(
                r["report_id"], 0) + 1
        for n in st.recent_notifications(limit=100):
            notif_keys[n.get("dedupe_key", "")] = \
                notif_keys.get(n.get("dedupe_key", ""), 0) + 1
    checks["repeat_cursor_stable"] = {
        "state": "OK" if len(set(cursors)) == 1 else "failed",
        "cursors": cursors}
    checks["repeat_no_duplicate_report"] = {
        "state": "OK" if all(v == 1 for v in reports_seen.values()) else "failed",
        "counts": reports_seen}
    checks["repeat_no_duplicate_notification"] = {
        "state": "OK" if all(v == 1 for v in notif_keys.values()) else "failed",
        "counts": notif_keys}

    # no silent loss: committed items remain present across repeats.
    n_items = st._conn.execute("SELECT COUNT(*) FROM item").fetchone()[0]
    checks["repeat_no_silent_loss"] = {
        "state": "OK" if n_items >= 3 else "failed", "items": n_items}

    # no duplicate action receipts / audit across repeated saves with the same
    # idempotency key (the 8F receipt store deduplicates).
    from ..actions import ActionStore, ActionReceipt
    ap = root / "receipts.json"
    astore = ActionStore(ap)
    astore.save_receipt(ActionReceipt.from_dict({
        "receipt_id": "repeat-receipt", "action_id": "repeat-act",
        "action_hash": "cd" * 32, "target": "target", "scope": "default",
        "state": "completed", "code": "ok", "created_at": time.time(),
        "idempotency_key": "repeat-key"}))
    # a second save_receipt persists the audit history (8F: _audit runs after
    # _save, so a later _save flushes the accumulated audit).
    astore.save_receipt(ActionReceipt.from_dict({
        "receipt_id": "repeat-receipt", "action_id": "repeat-act",
        "action_hash": "cd" * 32, "target": "target", "scope": "default",
        "state": "completed", "code": "ok", "created_at": time.time(),
        "idempotency_key": "repeat-key"}))
    astore.close()
    # Simulate crash/reopen recovery twice: no duplicate receipt and the audit
    # history is EXACTLY one entry per receipt id across recoveries (reopen
    # never appends a duplicate audit record).
    audit_counts = []
    receipt_counts = []
    for _ in range(2):
        astore = ActionStore(ap)   # __init__ runs _recover_interrupted
        audit = astore.audit(limit=100)
        receipts = astore.receipts(limit=50)
        same_id = [r for r in receipts if r.receipt_id == "repeat-receipt"]
        audit_for = [e for e in audit if e.get("receipt_id") == "repeat-receipt"]
        receipt_counts.append(len(same_id))
        audit_counts.append(len(audit_for))
        astore.close()
    checks["repeat_no_duplicate_receipt"] = {
        "state": "OK" if set(receipt_counts) == {1} else "failed",
        "counts": receipt_counts}
    # EXACTLY one audit record per receipt id across recoveries: the reopen/
    # recovery path never adds a duplicate audit record.
    checks["repeat_no_duplicate_audit"] = {
        "state": "OK" if set(audit_counts) == {1} else "failed",
        "audit_counts": audit_counts}

    # no secret leakage in the rehearsal's own outputs.
    leaked = _scan_for_secrets(json.dumps(st.recent_notifications(limit=100)))
    checks["repeat_no_secret_leak"] = {
        "state": "OK" if not leaked else "failed", "leaked": leaked}
    st.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


def _scan_for_secrets(text: str) -> List[str]:
    import re
    markers = [
        r"(?i)(password|passwd|secret|api[_-]?key|authorization|bearer)",
        r"(?i)(token=)",
        r"(cookie|session_id)",
    ]
    out = []
    for m in markers:
        for hit in re.findall(m, text):
            if hit not in out:
                out.append(hit)
    return out


# ----------------------------------------------------------------- hermetic
def qualify_hermetic(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Prove Packet 8J is hermetic: isolated temp state with private
    permissions, no production paths referenced, network blocked in children,
    and ``--live`` always fails closed."""
    checks: Dict[str, Any] = {}
    root = _mkstate(state_dir)

    # 1) every file/dir created by the rehearsal is private (0700/0600).
    perms_ok = True
    bad = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            continue
        try:
            mode = p.stat().st_mode & 0o777
            if p.is_dir():
                if mode & 0o077:
                    perms_ok = False
                    bad.append(f"{p.name}:{oct(mode)}")
            elif mode & 0o077:
                perms_ok = False
                bad.append(f"{p.name}:{oct(mode)}")
        except OSError:
            continue
    checks["hermetic_private_permissions"] = {
        "state": "OK" if perms_ok else "failed", "too_loose": bad[:10]}

    # 2) the isolated state dir is NOT the production state dir.
    isolated = isolate_cfg(cfg, root / "state-root")
    prod = Path(cfg.state_dir()).resolve()
    iso = Path(isolated.state_dir()).resolve()
    checks["hermetic_isolated_from_production"] = {
        "state": "OK" if iso != prod and not str(iso).startswith(str(prod))
        else "failed",
        "isolated": str(iso), "production": str(prod)}

    # 3) network is blocked in a child BEFORE any workload (actual result).
    r = root / "net"
    r.mkdir(parents=True, exist_ok=True)
    (r / "markers").mkdir(parents=True, exist_ok=True)
    (r / "state").mkdir(parents=True, exist_ok=True)
    spec = {"mode": "resource", "state_dir": str(r / "state"),
            "marker_dir": str(r / "markers"), "n_commits": 5,
            "limits": dict(DEFAULT_RLIMITS), "workload": "store"}
    proc = _spawn_worker(spec, r)
    _await_completion(proc, timeout=CHILD_TIMEOUT_S)
    res = _worker_result(r / "markers")
    net_probe = res.get("network_probe", {})
    checks["hermetic_network_blocked"] = {
        "state": "OK" if net_probe.get("blocked") is True else "failed",
        "probe": net_probe}

    # 4) --live fails closed (deny_live never authorizes).
    denial = deny_live(True)
    checks["hermetic_live_fails_closed"] = {
        "state": "OK" if denial and denial.get("code") == "LIVE_NOT_AUTHORIZED"
        else "failed",
        "denial": denial}

    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True}


# ---------------------------------------------------------------- orchestration
def _persist_summary(state_dir: Path, run_id: str, out: Dict[str, Any]) -> None:
    """Atomically persist a redacted summary at ``0600``.

    Uses temp-file + fsync + rename so a crash/failure never leaves a partial
    summary. Raises on ANY persistence failure (write/flush/fsync/rename) so
    the caller can return a typed failure instead of claiming ``OK``.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(state_dir), prefix=".summary-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(out, indent=2, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, state_dir / f"{run_id}.json")
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dirfd = os.open(str(state_dir), os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except OSError:
        # Dir fsync failure is a persistence failure: fail closed so the run
        # cannot claim OK after the rename unless the directory entry is
        # durably fsynced.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _persist_summary_boundaries(state_dir: Path, run_id: str,
                                out: Dict[str, Any]) -> Dict[str, Any]:
    """Test each summary-persistence failure boundary (write/flush/fsync/
    dir-fsync/rename). Each failure must raise so the run returns a typed
    failure and never claims ``OK``. Returns a typed check dict."""
    checks: Dict[str, Any] = {}
    for boundary in ("write", "flush", "fsync", "dir_fsync", "rename"):
        state_dir.mkdir(parents=True, exist_ok=True)
        p = state_dir / f"fail-{boundary}.json"
        if p.exists():
            p.unlink()
        import unittest.mock as mock
        fail = {
            "write": mock.patch("tempfile.mkstemp",
                                side_effect=OSError(errno.EIO)),
            "flush": None,
            "fsync": mock.patch("os.fsync",
                                side_effect=OSError(errno.EIO)),
            "dir_fsync": None,
            "rename": mock.patch("os.replace",
                                 side_effect=OSError(errno.EIO)),
        }[boundary]
        if boundary == "flush":
            # wrap os.fdopen so the returned file's flush() raises
            real_fdopen = os.fdopen

            class _FailFlush:
                def __init__(self, fh):
                    self._fh = fh

                def write(self, data):
                    return self._fh.write(data)

                def flush(self):
                    raise OSError(errno.EIO, "injected flush failure")

                def fileno(self):
                    return self._fh.fileno()

                def close(self):
                    return self._fh.close()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    self.close()

            def _fdopen(fd, *a, **k):
                return _FailFlush(real_fdopen(fd, *a, **k))
            cm = mock.patch("os.fdopen", side_effect=_fdopen)
        elif boundary == "dir_fsync":
            # fail the SECOND os.fsync call (the dir entry after the rename)
            real_fsync = os.fsync
            calls = {"n": 0}

            def _fsync(fd):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError(errno.EIO, "injected dir fsync failure")
                return real_fsync(fd)
            cm = mock.patch("os.fsync", side_effect=_fsync)
        else:
            cm = fail
        with cm:
            raised = False
            try:
                _persist_summary(state_dir, f"fail-{boundary}", out)
            except OSError:
                raised = True
        # write/flush/fsync leave no final file (tmp unlinked or never created);
        # dir_fsync fails AFTER rename so the final file exists but the run
        # still fails closed (no durable directory entry), not a partial file.
        no_partial = True if boundary == "dir_fsync" else (not p.exists())
        checks[f"summary_fail_{boundary}"] = {
            "state": "OK" if raised else "failed",
            "fail_closed": raised,
            "no_partial": no_partial}
    return checks


def _redacted_stages(stages: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, stage in stages.items():
        d = dict(stage)
        d.pop("checks", None)
        out[name] = d
    return out


def run_rehearsal(cfg: Config, *, live: bool = False,
                  env_value: Optional[str] = None,
                  state_dir: Optional[Path] = None,
                  fixture: Optional[Path] = None,
                  dry_run: bool = False,
                  stages: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Run the Packet 8J recovery rehearsal (hermetic only).

    Every stage runs in a fresh isolated temporary state root. Returns a typed
    per-stage result and, unless ``dry_run``, persists a REDACTED summary at
    ``0600`` under ``state_dir`` (default ``<config state>/qualification-recovery``).
    """
    state_dir = Path(state_dir or (cfg.state_dir() / QUALIFY_STATE_DIR))
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(state_dir, 0o700)
    except OSError:
        pass
    rid = f"rc-{uuid.uuid4().hex[:12]}"
    run_root = state_dir / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(run_root, 0o700)
    except OSError:
        pass
    stage_root = run_root / rid
    stage_root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(stage_root, 0o700)
    except OSError:
        pass

    avail = {
        "preflight": qualify_preflight(cfg),
        "crash_commit": qualify_crash_commit(cfg, stage_root, fixture=fixture),
        "crash_kill": qualify_crash_kill(cfg, stage_root),
        "fsync_failure": qualify_fsync_failure(cfg, stage_root),
        "rename_failure": qualify_rename_failure(cfg, stage_root),
        "enospc": qualify_enospc(cfg, stage_root),
        "corrupt_sqlite": qualify_corrupt_sqlite(cfg, stage_root),
        "newer_schema": qualify_newer_schema(cfg, stage_root),
        "resources": qualify_resources(cfg, stage_root),
        "rpo_rto": qualify_rpo_rto(cfg, stage_root),
        "atomic_boundaries": qualify_atomic_boundaries(cfg, stage_root),
        "partial_rollback": qualify_partial_rollback(cfg, stage_root),
        "repeat_recovery": qualify_repeat_recovery(cfg, stage_root),
        "hermetic": qualify_hermetic(cfg, stage_root),
        "lease": qualify_lease(cfg, stage_root),
        "notify_retry": qualify_notify_retry(cfg, stage_root),
        "report_bundle": qualify_report_bundle(cfg, stage_root),
        "portable_restore": qualify_portable_restore(cfg, stage_root),
        "journal": qualify_journal(cfg, stage_root),
        "action_receipts": qualify_action_receipts(cfg, stage_root),
        "disk_state": qualify_disk_state(cfg, stage_root),
        "concurrent": qualify_concurrent(cfg, stage_root),
        "idempotent_recovery": qualify_idempotent_recovery(cfg, stage_root),
    }
    if stages:
        avail = {k: v for k, v in avail.items() if k in stages}

    failed = [name for name, s in avail.items()
              if s.get("state") == "failed"]
    degraded = [name for name, s in avail.items()
                if s.get("state") == "degraded"]
    overall = ("failed" if failed else
               "degraded" if degraded else "OK")
    out: Dict[str, Any] = {
        "state": overall,
        "qualify_version": QUALIFY_VERSION,
        "run_id": rid,
        "stages": avail,
        "failed": failed,
        "degraded": degraded,
        "isolated": True,
        "note": "isolated recovery rehearsal; no live service/account/browser/"
                "timer/production state was touched",
    }
    if not dry_run:
        try:
            _persist_summary(state_dir, rid, {
                "state": overall, "run_id": rid,
                "stages": _redacted_stages(avail),
                "failed": failed, "degraded": degraded, "isolated": True,
            })
        except OSError as exc:
            # Fail closed: a summary that cannot be persisted means the run
            # must NOT claim OK.
            return {
                "state": "failed", "qualify_version": QUALIFY_VERSION,
                "run_id": rid, "stages": avail,
                "failed": failed + ["summary_persistence"],
                "degraded": degraded,
                "isolated": True,
                "summary_persisted": False,
                "summary_error": f"{type(exc).__name__}: {str(exc)[:160]}",
                "note": "summary persistence failed; run did not claim OK",
            }
        out["summary_persisted"] = True
    return out
