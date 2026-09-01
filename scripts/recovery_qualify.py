#!/usr/bin/env python3
"""Packet 8J: end-to-end recovery rehearsal CLI.

Usage:
  python scripts/recovery_qualify.py --preflight
  python scripts/recovery_qualify.py --crash-commit
  python scripts/recovery_qualify.py --crash-kill
  python scripts/recovery_qualify.py --fsync-failure
  python scripts/recovery_qualify.py --rename-failure
  python scripts/recovery_qualify.py --enospc
  python scripts/recovery_qualify.py --corrupt-sqlite
  python scripts/recovery_qualify.py --newer-schema
  python scripts/recovery_qualify.py --resources
  python scripts/recovery_qualify.py --rpo-rto
  python scripts/recovery_qualify.py --atomic-boundaries
  python scripts/recovery_qualify.py --partial-rollback
  python scripts/recovery_qualify.py --repeat-recovery
  python scripts/recovery_qualify.py --lease
  python scripts/recovery_qualify.py --notify-retry
  python scripts/recovery_qualify.py --report-bundle
  python scripts/recovery_qualify.py --portable-restore
  python scripts/recovery_qualify.py --journal
  python scripts/recovery_qualify.py --action-receipts
  python scripts/recovery_qualify.py --disk-state
  python scripts/recovery_qualify.py --concurrent
  python scripts/recovery_qualify.py --idempotent
  python scripts/recovery_qualify.py --all

Packet 8J is hermetic-only: every stage rehearses crash/recovery boundaries in
an ISOLATED temporary state root (default ``<config state>/qualification-recovery``).
No live service, account, browser, timer, or production state is touched, and
no destructive account action or real message is ever sent. ``--live`` has no
meaning here and always fails closed (``LIVE_NOT_AUTHORIZED``).

Persisted output is a redacted typed summary at ``0600``. A rollback that
cannot fully restore every touched path is reported as a typed failure with
the exact bounded paths — it is never swallowed or described as successful.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.config import Config  # noqa: E402
from voicebridge.communications.recovery_qualify import (  # noqa: E402
    deny_live, qualify_action_receipts, qualify_atomic_boundaries,
    qualify_concurrent, qualify_corrupt_sqlite, qualify_crash_commit,
    qualify_crash_kill, qualify_disk_state, qualify_enospc,
    qualify_fsync_failure, qualify_idempotent_recovery, qualify_journal,
    qualify_lease, qualify_newer_schema, qualify_notify_retry,
    qualify_partial_rollback, qualify_portable_restore, qualify_preflight,
    qualify_rename_failure, qualify_repeat_recovery, qualify_report_bundle,
    qualify_resources, qualify_rpo_rto, run_rehearsal,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DENIED = 2

STAGES = {
    "preflight": qualify_preflight,
    "crash_commit": qualify_crash_commit,
    "crash_kill": qualify_crash_kill,
    "fsync_failure": qualify_fsync_failure,
    "rename_failure": qualify_rename_failure,
    "enospc": qualify_enospc,
    "corrupt_sqlite": qualify_corrupt_sqlite,
    "newer_schema": qualify_newer_schema,
    "resources": qualify_resources,
    "rpo_rto": qualify_rpo_rto,
    "atomic_boundaries": qualify_atomic_boundaries,
    "partial_rollback": qualify_partial_rollback,
    "repeat_recovery": qualify_repeat_recovery,
    "lease": qualify_lease,
    "notify_retry": qualify_notify_retry,
    "report_bundle": qualify_report_bundle,
    "portable_restore": qualify_portable_restore,
    "journal": qualify_journal,
    "action_receipts": qualify_action_receipts,
    "disk_state": qualify_disk_state,
    "concurrent": qualify_concurrent,
    "idempotent_recovery": qualify_idempotent_recovery,
}


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _cfg(args) -> Config:
    return Config.load(args.config)


def _state_dir(args, cfg: Config) -> Path:
    if args.state_dir:
        return Path(args.state_dir)
    return cfg.state_dir() / "qualification-recovery"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="recovery-qualify",
        description="Packet 8J end-to-end recovery rehearsal (hermetic only)")
    ap.add_argument("--config", default=None, help="config path")
    ap.add_argument("--state-dir", default=None,
                    help="isolated state root (never the production state)")
    ap.add_argument("--fixture", default=None,
                    help="synthetic scan fixture path (default: repo fixtures)")
    ap.add_argument("--live", action="store_true",
                    help="accepted only to fail closed: 8J has no live stages")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not persist the redacted summary")
    for name in STAGES:
        ap.add_argument(f"--{name.replace('_', '-')}", action="store_true",
                        help=f"run the {name} stage")
    ap.add_argument("--all", action="store_true",
                    help="run every recovery stage")
    args = ap.parse_args(argv)
    cfg = _cfg(args)

    denial = deny_live(args.live)
    if denial is not None:
        _print(denial)
        return EXIT_DENIED

    state_dir = _state_dir(args, cfg)
    state_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    args.rid = uuid.uuid4().hex[:12]

    selected = [n for n in STAGES if getattr(args, n)]
    if args.all:
        selected = list(STAGES)
    if not selected:
        selected = ["preflight"]

    if len(selected) == 1 and selected[0] in STAGES and not args.all:
        # Single-stage invocation: reuse the orchestration so the summary is
        # consistent and idempotent per run.
        result = run_rehearsal(cfg, state_dir=state_dir,
                               fixture=Path(args.fixture) if args.fixture else None,
                               dry_run=args.dry_run,
                               stages=selected)
        _print({k: result[k] for k in
                ("state", "run_id", "failed", "degraded", "isolated")} |
               {"checks": result["stages"][selected[0]].get("checks", {})})
    else:
        result = run_rehearsal(cfg, state_dir=state_dir,
                               fixture=Path(args.fixture) if args.fixture else None,
                               dry_run=args.dry_run,
                               stages=selected or None)
        _print({k: result[k] for k in
                ("state", "run_id", "failed", "degraded", "isolated")} |
               {"stage_states": {n: s["state"] for n, s in
                                 result["stages"].items()}})
    return EXIT_OK if result["state"] == "OK" else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())