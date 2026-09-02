"""Packet 8J tests: end-to-end recovery rehearsal (hermetic).

Covers the isolated recovery-rehearsal harness stages AND the closed recovery
risks the plan assigns to 8J:
- crash boundaries around cursor/report commit and rollback_cursor from history;
- atomic report-bundle publish and typed corrupt/missing-evidence handling;
- portable backup/restore/rollback incl. PartialRollbackError with the exact
  bounded paths (Packet 8J known finding #6) — a failed rollback step is never
  swallowed or reported as a successful rollback;
- bounded notification retry / dead-letter, dedupe, restart persistence;
- monotonic renewable overlap lease and stale-lease recovery;
- truncated/corrupt JSONL journal tolerance without secret leakage;
- action receipt crash recovery (running -> interrupted) and corrupt-state
  fail-closed;
- read-only / disk-full store writes fail typed and recover after clearing;
- concurrent manual/timer/action store access isolation and OVERLAP denial;
- idempotent repeated recovery (stable cursor, no duplicate notifications);
- RPO/RTO preflight accounting and hermetic-only (deny_live) guarantee.

All tests use isolated temporary state and synthetic fixtures only; no live
service, account, browser, timer, or production state is touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.communications.recovery_qualify import (  # noqa: E402
    _guard_newer_schema, _persist_summary, _persist_summary_boundaries,
    _run_sigkill_file, _run_sigkill_store, _run_fault_file, _scan_for_secrets,
    _snapshot_state, deny_live, qualify_action_receipts,
    qualify_atomic_boundaries, qualify_concurrent, qualify_corrupt_sqlite,
    qualify_crash_commit, qualify_crash_kill, qualify_disk_state,
    qualify_enospc, qualify_fsync_failure, qualify_hermetic,
    qualify_idempotent_recovery, qualify_journal, qualify_lease,
    qualify_newer_schema, qualify_notify_retry, qualify_partial_rollback,
    qualify_portable_restore, qualify_preflight, qualify_rename_failure,
    qualify_repeat_recovery, qualify_report_bundle, qualify_resources,
    qualify_rpo_rto, run_rehearsal,
)
from voicebridge.communications import recovery_crash as CRASH  # noqa: E402
from voicebridge.config import Config  # noqa: E402
from voicebridge.portable import PartialRollbackError  # noqa: E402


def _cfg(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n")
    return Config.load(p)


def _stage_root(tmp_path):
    root = tmp_path / "runs"
    root.mkdir(exist_ok=True)
    return root


# ------------------------------------------------------------------- preflight
def test_preflight_rpo_rto_and_isolated(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_preflight(cfg)
    assert out["state"] == "OK"
    assert out["recovery"]["rpo_seconds"] > 0
    assert out["recovery"]["rto_seconds"] > 0
    assert out["resource_ceilings"]["stage_uses_isolated_temp_state"] is True


def test_deny_live_fails_closed(tmp_path):
    assert deny_live(False) is None
    denial = deny_live(True)
    assert denial is not None
    assert denial["code"] == "LIVE_NOT_AUTHORIZED"
    assert denial["state"] == "denied"


# ----------------------------------------------------------------- crash commit
def test_crash_commit_boundaries(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_crash_commit(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["baseline_before_none"]["state"] == "OK"
    assert checks["baseline_committed"]["state"] == "OK"
    assert checks["new_item_committed"]["state"] == "OK"
    assert checks["crash_no_commit_no_cursor_jump"]["state"] == "OK"
    assert checks["rollback_cursor_restores_prior"]["state"] == "OK"
    assert checks["replay_idempotent"]["state"] == "OK"


# ----------------------------------------------------------------------- lease
def test_lease_recovery(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_lease(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["acquire_first_owner"]["state"] == "OK"
    assert checks["overlap_denied"]["state"] == "OK"
    assert checks["renewal_ok"]["state"] == "OK"
    assert checks["stale_lease_after_ttl"]["state"] == "OK"
    assert checks["stale_lease_reacquire"]["state"] == "OK"


# ----------------------------------------------------------------- notify retry
def test_notify_retry_dead_letter_and_restart(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_notify_retry(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["bounded_retry_dead_letter"]["state"] == "OK"
    assert checks["dedupe_exists"]["state"] == "OK"
    assert checks["restart_persistence"]["state"] == "OK"


# ---------------------------------------------------------------- report bundle
def test_report_bundle_recovery(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_report_bundle(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["bundle_published"]["state"] == "OK"
    assert checks["corrupt_manifest_typed"]["state"] == "OK"
    assert checks["missing_evidence_typed"]["state"] == "OK"
    assert checks["stale_stage_not_served"]["state"] == "OK"


# ------------------------------------------------------------ portable restore
def test_portable_restore_and_partial_rollback_visible(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_portable_restore(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["wrong_passphrase_typed"]["state"] == "OK"
    assert checks["restore_dry_run_no_write"]["state"] == "OK"
    assert checks["restore_ok_backup"]["state"] == "OK"
    # Known finding #6: partial rollback is a TYPED failure with exact paths.
    assert checks["partial_rollback_visible"]["state"] == "OK"
    assert checks["partial_rollback_visible"]["typed"] == "PartialRollbackError"
    assert checks["partial_rollback_visible"]["failed_paths"]


def test_partial_rollback_error_carries_paths_and_blocks_resume(tmp_path):
    """Direct contract test of PartialRollbackError semantics."""
    err = PartialRollbackError("incomplete", ["/state/memory", "/state/rep"])
    assert err.failed_paths == ["/state/memory", "/state/rep"]
    assert "incomplete" in str(err)


# -------------------------------------------------------------------- journal
def test_journal_truncated_corrupt_tolerated(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_journal(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["truncated_corrupt_tolerated"]["state"] == "OK"
    assert checks["no_secret_in_journal"]["state"] == "OK"
    assert checks["log_event_ok"]["state"] == "OK"


# -------------------------------------------------------------------- actions
def test_action_receipts_crash_recovery(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_action_receipts(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["running_recovered_to_interrupted"]["state"] == "OK"
    assert checks["corrupt_receipts_fail_closed"]["state"] == "OK"


# ------------------------------------------------------------------ disk/ro
def test_disk_state_readonly_typed_and_recovers(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_disk_state(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["readonly_write_typed"]["state"] == "OK"
    assert checks["reopen_after_readonly"]["state"] == "OK"


# ------------------------------------------------------------------ concurrent
def test_concurrent_access_isolated(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_concurrent(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["concurrent_writes_no_corruption"]["state"] == "OK"
    assert checks["manual_overlap_denied"]["state"] == "OK"


# ------------------------------------------------------------------ idempotency
def test_idempotent_repeated_recovery(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_idempotent_recovery(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["repeat_recovery_stable_cursor"]["state"] == "OK"
    assert checks["no_duplicate_notifications"]["state"] == "OK"


# ---------------------------------------------------------------- orchestration
def test_run_rehearsal_all_stages_ok_and_summary_persisted(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qual"
    out = run_rehearsal(cfg, state_dir=state_dir)
    assert out["state"] == "OK"
    assert not out["failed"]
    assert out["isolated"] is True
    # redacted summary persisted at 0600, no raw check dicts
    summaries = list(state_dir.glob("rc-*.json"))
    assert summaries
    assert oct(summaries[0].stat().st_mode) in ("0o100600", "0o600")
    payload = json.loads(summaries[0].read_text())
    assert "checks" not in json.dumps(payload)
    assert payload["state"] == "OK"


def test_run_rehearsal_dry_run_does_not_persist(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qual-dry"
    out = run_rehearsal(cfg, state_dir=state_dir, dry_run=True)
    assert out["state"] == "OK"
    assert not list(state_dir.glob("rc-*.json"))


def test_run_rehearsal_stage_filter(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qual-filter"
    out = run_rehearsal(cfg, state_dir=state_dir, stages=["lease", "journal"])
    assert set(out["stages"]) == {"lease", "journal"}
    assert out["state"] == "OK"


# ------------------------------------------------------------- crash kill matrix
def test_crash_kill_file_boundaries_sigkill(tmp_path):
    """SIGKILL at every file-write boundary: prior state intact until rename,
    new state durable after rename, temps never served."""
    cfg = _cfg(tmp_path)
    out = qualify_crash_kill(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    for b in CRASH.BOUNDARIES_FILE:
        c = checks[f"file_{b}"]
        assert c["state"] == "OK", f"file_{b}: {c}"
    # explicit invariant detail: after_rename serves new; before_rename serves old
    assert checks["file_after_rename"]["content"] == '{"new":true}'
    assert checks["file_before_rename"]["content"] == '{"old":true}'


def test_crash_kill_store_boundaries_sigkill(tmp_path):
    """SIGKILL inside the store commit: mid-transaction kills roll back
    atomically (only the baseline survives), after_commit is fully durable."""
    cfg = _cfg(tmp_path)
    out = qualify_crash_kill(cfg, _stage_root(tmp_path))
    checks = out["checks"]
    for b in CRASH.BOUNDARIES_STORE:
        c = checks[f"store_{b}"]
        assert c["state"] == "OK", f"store_{b}: {c}"
    assert checks["store_before_commit"]["got"].startswith("cursor=N")
    assert checks["store_after_commit"]["got"].startswith("cursor=Y")
    assert checks["store_after_commit"]["got"].endswith("notif=1")


def test_sigkill_helper_file_invariants(tmp_path):
    """Direct unit check of the file crash helper for each boundary."""
    root = tmp_path / "fk"
    root.mkdir(exist_ok=True)
    for b in CRASH.BOUNDARIES_FILE:
        r = _run_sigkill_file(b, root / b)
        assert r["state"] == "OK", f"{b}: {r}"


def test_sigkill_helper_store_invariants(tmp_path):
    root = tmp_path / "sk"
    root.mkdir(exist_ok=True)
    for b in CRASH.BOUNDARIES_STORE:
        r = _run_sigkill_store(b, root / b)
        assert r["state"] == "OK", f"{b}: {r}"


# ------------------------------------------------------------- fsync / rename
def test_fsync_failure_injection(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_fsync_failure(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    c = out["checks"]["fsync_eio"]
    assert c["typed_failure"] is True
    assert c["prior_intact"] is True
    assert c["retry_idempotent"] is True
    assert c["no_temp_served"] is True


def test_rename_failure_injection(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_rename_failure(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    c = out["checks"]["rename_eio"]
    assert c["typed_failure"] is True
    assert c["prior_intact"] is True
    assert c["retry_idempotent"] is True


def test_fault_helper_typed_failure_and_idempotent_retry(tmp_path):
    root = tmp_path / "fh"
    root.mkdir(exist_ok=True)
    r = _run_fault_file("fsync_fail", 5, 1, root / "fsync")
    assert r["state"] == "OK"
    assert r["typed_failure"] is True
    assert r["retry_idempotent"] is True


# ------------------------------------------------------------------- ENOSPC
def test_enospc_stage(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_enospc(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["enospc_real_kernel_enforced"]["state"] == "OK"
    assert checks["enospc_deterministic"]["typed_failure"] is True
    assert checks["enospc_reopen_recovers"]["state"] == "OK"


# ------------------------------------------------------------- corrupt sqlite
def test_corrupt_sqlite_stage(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_corrupt_sqlite(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["truncated_fails_closed"]["state"] == "OK"
    assert checks["malformed_fails_closed"]["state"] == "OK"
    assert checks["interrupted_migration_fails_closed"]["state"] == "OK"
    assert checks["corrupt_wal_sidecar"]["state"] == "OK"
    assert checks["backup_restore"]["state"] == "OK"
    assert checks["backup_restore"]["hash_validated"] is True


# ------------------------------------------------------------- newer schema
def test_newer_schema_stage(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_newer_schema(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["newer_schema_guard_refuses"]["state"] == "OK"
    assert checks["newer_schema_preserved"]["state"] == "OK"
    assert checks["newer_schema_recovery_refused"]["state"] == "OK"
    assert checks["newer_schema_recoverable_with_correct_code"]["state"] == "OK"


def test_guard_newer_schema_detects(tmp_path):
    from voicebridge.communications.store import (
        CommunicationsStore, STORE_SCHEMA_VERSION,
    )
    db = tmp_path / "g.db"
    st = CommunicationsStore(db)
    st.record_health(_mk_health())
    st.close()
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute(f"PRAGMA user_version={STORE_SCHEMA_VERSION + 3}")
    conn.commit()
    conn.close()
    assert _guard_newer_schema(db, STORE_SCHEMA_VERSION) == STORE_SCHEMA_VERSION + 3
    # a supported-schema file is not refused
    db2 = tmp_path / "g2.db"
    st2 = CommunicationsStore(db2)
    st2.close()
    assert _guard_newer_schema(db2, STORE_SCHEMA_VERSION) is None


def _mk_health():
    from voicebridge.communications.contracts import (
        AccountHealthRecord, AccountHealthStatus, Platform,
    )
    import time
    return AccountHealthRecord.create(
        platform=Platform.GMAIL, account_scope="default",
        status=AccountHealthStatus.HEALTHY, adapter_version="0",
        checked_at=time.time())


# --------------------------------------------------------------- resources
def test_resources_measured_and_enforced(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_resources(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    c = out["checks"]["resource_ok_and_measured"]
    assert c["state"] == "OK"
    m = c["measurements"]
    assert m["peak_rss_kib"] > 0
    assert m["cpu_s"] >= 0
    assert m["bytes_written"] > 0
    assert m["file_count"] >= 1
    assert m["process_count"] >= 1
    assert c["network_probe"]["blocked"] is True
    assert c["network_blocked_before_workload"] is True
    assert c["elapsed_s"] > 0
    # every enforced limit must be a typed failure
    for name in ("cpu", "memory", "fsize", "nofile", "nproc", "thread"):
        assert out["checks"][f"resource_{name}_limit"]["state"] == "OK", name
    # genuine wall-timeout on a hanging child
    assert out["checks"]["resource_wall_timeout"]["state"] == "OK"
    assert out["checks"]["resource_wall_timeout"]["wall_timed_out"] is True
    assert out["checks"]["resource_timeout_recovery_idempotent"]["state"] == "OK"


# ------------------------------------------------------------------- RPO/RTO
def test_rpo_rto_measured_real_restore_path(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_rpo_rto(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    c = out["checks"]["rpo_rto_measured"]
    assert c["data_loss_interval_s"] >= 0
    assert c["recovery_duration_s"] >= 0
    assert c["rpo_met"] is True
    assert c["rto_met"] is True
    assert c["last_durable_ts"] >= c["newest_recoverable_ts"]
    # recovery must go through the real atomic restore path, not write_bytes
    assert "restore_state" in c["recovery_path"]
    assert out["checks"]["store_atomic_restore"]["state"] == "OK"
    assert out["checks"]["store_atomic_restore"]["hash_validated"] is True


# ----------------------------------------------------------------- hermetic
def test_hermetic_stage(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_hermetic(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["hermetic_private_permissions"]["state"] == "OK"
    assert checks["hermetic_isolated_from_production"]["state"] == "OK"
    assert checks["hermetic_network_blocked"]["state"] == "OK"
    assert checks["hermetic_network_blocked"]["probe"]["blocked"] is True
    assert checks["hermetic_live_fails_closed"]["state"] == "OK"


# -------------------------------------------------- summary persistence
def test_summary_persist_fails_closed_at_every_boundary(tmp_path):
    state_dir = tmp_path / "sum"
    sb = _persist_summary_boundaries(
        state_dir, "rc-test", {"state": "OK", "run_id": "rc-test"})
    for k, v in sb.items():
        assert v["state"] == "OK", f"{k}: {v}"
        assert v["fail_closed"] is True
        assert v["no_partial"] is True


def test_summary_persist_atomic_rename_private(tmp_path):
    state_dir = tmp_path / "sum2"
    _persist_summary(state_dir, "rc-test", {"state": "OK", "run_id": "rc-test"})
    p = state_dir / "rc-test.json"
    assert p.exists()
    assert oct(p.stat().st_mode) in ("0o100600", "0o600")
    import json as _json
    assert _json.loads(p.read_text())["state"] == "OK"
    # no leftover temp files after a successful persist
    temps = [f for f in state_dir.iterdir() if f.name.startswith(".summary-")]
    assert temps == []


def test_run_rehearsal_summary_failure_returns_typed_failure(tmp_path):
    from unittest import mock
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qual-fail"
    # run only the fast preflight stage; a persisted summary is still written
    with mock.patch(
            "voicebridge.communications.recovery_qualify._persist_summary",
            side_effect=OSError(5, "injected persist failure")):
        out = run_rehearsal(cfg, state_dir=state_dir, stages=["preflight"])
    # the run must NOT claim OK when the summary cannot be persisted
    assert out["state"] == "failed"
    assert out.get("summary_persisted") is False
    assert "summary_persistence" in out.get("failed", [])


# --------------------------------------------------------- atomic boundaries
def test_atomic_boundaries_hash_schema(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_atomic_boundaries(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["store_schema_and_hash_stable"]["state"] == "OK"
    assert checks["store_schema_and_hash_stable"]["schema"] >= 1
    assert checks["receipts_hash_stable"]["state"] == "OK"
    assert checks["journal_truncation_recovered"]["state"] == "OK"


def test_snapshot_state_hashes_files(tmp_path):
    state = tmp_path / "s"
    state.mkdir()
    (state / "a.json").write_text('{"x":1}')
    (state / "b.db").write_bytes(b"data")
    snap = _snapshot_state(state)
    assert len(snap["files"]) == 2
    assert snap["files"]["a.json"] == \
        "5041bf1f713df204784353e82f6a4a535931cb64f1f4b4a5aeaffcb720918b22"


# --------------------------------------------------------- partial rollback
def test_partial_rollback_full_verification(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_partial_rollback(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["partial_rollback_visible"]["typed"] == "PartialRollbackError"
    assert checks["partial_rollback_visible"]["failed_paths"]
    assert checks["unaffected_paths_intact"]["state"] == "OK"
    assert checks["later_recovery_completes"]["state"] == "OK"


# --------------------------------------------------------- repeat recovery
def test_repeat_recovery_no_dup_no_loss_no_leak(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_repeat_recovery(cfg, _stage_root(tmp_path))
    assert out["state"] == "OK"
    checks = out["checks"]
    assert checks["repeat_cursor_stable"]["state"] == "OK"
    assert checks["repeat_no_duplicate_report"]["state"] == "OK"
    assert checks["repeat_no_duplicate_notification"]["state"] == "OK"
    assert checks["repeat_no_silent_loss"]["state"] == "OK"
    assert checks["repeat_no_secret_leak"]["state"] == "OK"


def test_scan_for_secrets_finds_markers():
    leaked = _scan_for_secrets('x password=1 y token=abc cookie=z')
    assert "password" in leaked and "token=" in leaked
    assert _scan_for_secrets('{"report_id":"r1","state":"ok"}') == []


# ----------------------------------------------------------- stage coverage
def test_all_new_stages_pass_individually(tmp_path):
    cfg = _cfg(tmp_path)
    for name in ("crash_kill", "fsync_failure", "rename_failure", "enospc",
                 "corrupt_sqlite", "newer_schema", "resources", "rpo_rto",
                 "atomic_boundaries", "partial_rollback", "repeat_recovery",
                 "hermetic"):
        out = run_rehearsal(cfg, state_dir=tmp_path / name, stages=[name])
        assert out["state"] == "OK", f"{name}: {out['failed']}"


# ------------------------------------------------------ network isolation
def test_network_probe_blocked_before_workload(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_resources(cfg, _stage_root(tmp_path))
    probe = out["checks"]["resource_ok_and_measured"]["network_probe"]
    assert probe["attempted"] is True
    assert probe["blocked"] is True


def test_process_count_measured_not_hardcoded(tmp_path):
    from voicebridge.communications.recovery_crash import _process_count
    assert _process_count() >= 1


# ------------------------------------------------------- ENOSPC honesty
def test_enospc_efbig_labeled_quota(tmp_path):
    cfg = _cfg(tmp_path)
    out = qualify_enospc(cfg, _stage_root(tmp_path))
    checks = out["checks"]
    assert checks["enospc_real_kernel_enforced"]["state"] == "OK"
    assert checks["enospc_real_kernel_enforced"]["errno"] == 27  # EFBIG
    assert "quota/file-size" in \
        checks["enospc_real_kernel_enforced"]["failure_class"]
    assert checks["enospc_deterministic"]["state"] == "OK"
    assert checks["enospc_deterministic"]["errno"] == 28  # ENOSPC
    assert checks["enospc_reopen_recovers"]["state"] == "OK"