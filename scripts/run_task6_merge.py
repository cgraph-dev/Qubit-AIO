#!/usr/bin/env python
# scripts/run_task6_merge.py
"""Task 6 CLI: apply the v0.21.5 bucket merge to the frozen worktree.

The trees are fixed by the Task 6 brief (ruling R8):

    base   = engine(v0.21.4)   /tmp/qubit-task6-base/qubit_runtime
    ours   = engine(v0.21.5)   /tmp/qubit-v0215-candidate/qubit_runtime
    theirs = current Qubit     /tmp/qubit-sdd/qubit_runtime  (the target too)

Usage::

    python scripts/run_task6_merge.py --bucket agent   # one bucket + commit
    python scripts/run_task6_merge.py --all            # every bucket
    python scripts/run_task6_merge.py --check          # dry-run totals only

Every tree path is overridable with ``QUBIT_T6_BASE`` / ``QUBIT_T6_OURS`` /
``QUBIT_T6_TARGET`` so the driver is testable on temp trees.  The script never
touches the overlay trees; its only writer is
:func:`merge_v0215.driver.apply_record`, scoped under the target root.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from merge_v0215 import (  # noqa: E402
    BUCKET_ORDER,
    build_audit,
    classify,
    iter_files,
    run_bucket,
    write_audit,
)

import os  # noqa: E402

BASE = Path(os.environ.get("QUBIT_T6_BASE", "/tmp/qubit-task6-base/qubit_runtime"))
OURS = Path(os.environ.get("QUBIT_T6_OURS", "/tmp/qubit-v0215-candidate/qubit_runtime"))
TARGET = Path(os.environ.get("QUBIT_T6_TARGET", str(REPO / "qubit_runtime")))
TRIAGE = Path(os.environ.get(
    "QUBIT_T6_TRIAGE",
    str(REPO / ".superpowers/sdd/2026-09-25-qubit-v0.21.5-rebase-1to1/prep/TASK6_TRIAGE.json"),
))
AUDIT_OUT = REPO / "docs" / "generated" / "audit" / "V0_21_5_MERGE.json"


def _triage_class_counts() -> dict[str, dict[str, int]]:
    if not TRIAGE.is_file():
        return {}
    data = json.loads(TRIAGE.read_text(encoding="utf-8"))
    out: dict[str, dict[str, int]] = {}
    for bucket, lists in data.get("lists", {}).items():
        # Triage keys carry a trailing slash (``agent/``); the driver bucket
        # names do not.  Normalize so the cross-check compares like for like.
        key = bucket[:-1] if bucket.endswith("/") else bucket
        out[key] = {cls: len(paths) for cls, paths in lists.items() if paths}
    return out


def cross_check(records: list[dict]) -> list[str]:
    """Compare computed per-bucket class counts to the committed triage."""
    expected = _triage_class_counts()
    computed: dict[str, dict[str, int]] = {}
    for r in records:
        computed.setdefault(r["bucket"], {})
        computed[r["bucket"]][r["class"]] = computed[r["bucket"]].get(r["class"], 0) + 1
    problems: list[str] = []
    for bucket, exp in expected.items():
        got = computed.get(bucket, {})
        for cls, n in exp.items():
            if cls == "IDENTICAL":
                continue
            if got.get(cls, 0) != n:
                problems.append(
                    f"{bucket}: class {cls} expected {n}, computed {got.get(cls, 0)}"
                )
    return problems


def _load_records() -> list[dict]:
    if AUDIT_OUT.is_file():
        data = json.loads(AUDIT_OUT.read_text(encoding="utf-8"))
        return data.get("files", [])
    return []


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Task 6 bucket merge")
    p.add_argument("--bucket", choices=BUCKET_ORDER, help="merge one bucket")
    p.add_argument("--all", action="store_true", help="merge every bucket")
    p.add_argument("--check", action="store_true", help="dry-run, no writes")
    args = p.parse_args(argv)

    if args.check:
        recs: list[dict] = []
        for bucket in BUCKET_ORDER:
            recs.extend(run_bucket(TARGET, BASE, OURS, TARGET, bucket, apply=False))
        problems = cross_check(recs)
        print(json.dumps({
            "buckets": {b: sum(1 for r in recs if r["bucket"] == b) for b in BUCKET_ORDER},
            "by_class": _counts(r["class"] for r in recs),
            "by_resolution": _counts(r["resolution"] for r in recs),
            "triage_mismatches": problems,
        }, indent=1))
        return 1 if problems else 0

    if not args.all and not args.bucket:
        p.error("pass --bucket <name>, --all, or --check")

    buckets = BUCKET_ORDER if args.all else (args.bucket,)
    records = [] if args.all else _load_records()
    for bucket in buckets:
        if not args.all:
            records = [r for r in records if r["bucket"] != bucket]
        print(f"merging bucket {bucket} ...", flush=True)
        records.extend(run_bucket(TARGET, BASE, OURS, TARGET, bucket, apply=True))
        audit = build_audit(records, BASE, OURS, TARGET)
        write_audit(audit, AUDIT_OUT)
        print(f"  changed={audit['totals']['changed']} "
              f"written={audit['totals']['written']} "
              f"deleted={audit['totals']['deleted']} "
              f"conflicts={audit['totals']['conflicts']}", flush=True)

    problems = cross_check(records)
    if problems:
        print("TRIAGE MISMATCH:", file=sys.stderr)
        for line in problems:
            print("  " + line, file=sys.stderr)
        return 1
    print(f"audit: {AUDIT_OUT}")
    return 0


def _counts(it) -> dict[str, int]:
    from collections import Counter
    return dict(sorted(Counter(it).items()))


if __name__ == "__main__":
    raise SystemExit(main())
