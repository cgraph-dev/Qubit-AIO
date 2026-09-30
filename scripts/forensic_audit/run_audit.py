#!/usr/bin/env python3
"""Re-runnable Qubit-vs-upstream forensic audit runner.

Three modes:

* default (no flag) -- run the checks, diff against the ledger, write the
  updated ledger and regenerate ``QubitVSupstream.md``.
* ``--check``        -- run the checks, diff against the committed ledger, write
  NOTHING, and exit non-zero when a NEW or REGRESSED HIGH/CRITICAL finding
  exists. This is the CI / pre-commit gate.
* ``--list-checks``  -- print the check catalog.

Determinism: two ``--check`` runs over an unchanged tree produce identical
findings (the unit test asserts this). The ledger is keyed by a stable
fingerprint that never includes line numbers, so a moved line is not a new
finding.

Usage::

    python scripts/forensic_audit/run_audit.py --check
    python scripts/forensic_audit/run_audit.py --check --fast
    python scripts/forensic_audit/run_audit.py
    python scripts/forensic_audit/run_audit.py --only home_fallback --check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from forensic_audit import checks as checks_mod  # noqa: E402
from forensic_audit import report as report_mod  # noqa: E402
from forensic_audit import schema  # noqa: E402

REPO_DEFAULT = HERE.parents[1]
LEDGER_DEFAULT = REPO_DEFAULT / "docs" / "generated" / "audit" / "ledger.json"
TRIAGE_DEFAULT = REPO_DEFAULT / "docs" / "generated" / "audit" / "triage.json"


def _gating_diff(current, ledger, triage, run_check_ids):
    rows = schema.diff_against_ledger(current, ledger, run_check_ids)
    rows = schema.apply_triage(rows, triage)
    return rows, schema.summarize(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Re-runnable Qubit forensic audit.")
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--ledger", default=str(LEDGER_DEFAULT))
    ap.add_argument("--triage", default=str(TRIAGE_DEFAULT))
    ap.add_argument("--check", action="store_true",
                    help="compare to ledger; write nothing; non-zero on gating")
    ap.add_argument("--fast", action="store_true",
                    help="skip slow checks (generator/import subprocesses)")
    ap.add_argument("--only", default="", help="comma-separated check ids")
    ap.add_argument("--exclude", default="", help="comma-separated check ids")
    ap.add_argument("--list-checks", action="store_true")
    args = ap.parse_args(argv)

    if args.list_checks:
        for c in checks_mod.CHECKS:
            print(f"{c['id']}\t{'slow' if c['slow'] else 'fast'}\t{c['description']}")
        return 0

    repo = Path(args.repo)
    ledger_path = Path(args.ledger)
    triage_path = Path(args.triage)

    only = [x for x in args.only.split(",") if x] or None
    exclude = [x for x in args.exclude.split(",") if x] or None

    current = checks_mod.run_checks(repo, fast=args.fast, only=only, exclude=exclude)
    ledger = schema.load_ledger(ledger_path)
    triage = schema.load_triage(triage_path)
    run_check_ids = [
        c["id"] for c in checks_mod.CHECKS
        if (not only or c["id"] in only)
        and (not exclude or c["id"] not in exclude)
        and not (args.fast and c["slow"])
    ]
    rows, summary = _gating_diff(current, ledger, triage, run_check_ids)

    if args.check:
        gating = summary["gating"]
        print(f"forensic audit: {summary['total']} findings "
              f"status={summary['by_status']} severity={summary['by_severity']}")
        if gating:
            print("GATING (new/regressed HIGH|CRITICAL):", file=sys.stderr)
            for gid in gating:
                print(f"  - {gid}", file=sys.stderr)
            return 1
        print("no new/regressed HIGH|CRITICAL findings")
        return 0

    run_seq = int(ledger.get("run_seq", 0)) + 1
    payload = schema.ledger_payload(rows, run_seq)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(schema.dumps(payload), encoding="utf-8")

    text = report_mod.render(rows, run_seq, [
        {"id": c["id"], "slow": c["slow"], "description": c["description"]}
        for c in checks_mod.CHECKS])
    out = report_mod.write(repo, text)
    print(f"forensic audit -> {ledger_path} (run {run_seq}); report -> {out}")
    print(f"  status={summary['by_status']} severity={summary['by_severity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
