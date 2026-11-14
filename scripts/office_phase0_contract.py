#!/usr/bin/env python3
"""P0-01 — freeze the Qubit Office authority set and verify it has not drifted.

The phase-0 contract records the plan version, the owner decisions in force and
a sha256 for every canonical plan document. ``--check`` recomputes those hashes
and fails when a frozen document changed, so an agent cannot silently rewrite
the plan to make progress look easier.

Exit codes: 0 frozen/matching, 1 drift or missing document, 2 usage/IO.
No network, no writes outside ``evidence/phase-0/contract.json``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "evidence" / "phase-0" / "contract.json"

PLAN_VERSION = 11

FROZEN_DOCS = (
    "docs/QUBIT-OFFICE-COMPLETE-PLAN.md",
    "docs/QUBIT-OFFICE-PLAN-AUDIT-V10.md",
    "docs/QUBIT-OFFICE-UI-CONTRACT.md",
    "docs/QUBIT-OFFICE-MANDATORY-CHECKLIST.md",
    "docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md",
    "docs/QUBIT-OFFICE-TRADING-DEPARTMENT.md",
    "docs/QUBIT-OFFICE-REPO-LEDGER.md",
    "docs/QUBIT-OFFICE-ASSET-LEDGER.md",
    "docs/QUBIT-OFFICE-PHASE-0.md",
    "docs/QUBIT-OFFICE-THREAT-MODEL.md",
    "docs/QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md",
    "docs/QUBIT-OFFICE-RESEARCH-GAPS.md",
    "docs/QUBIT-OFFICE-BROWSER-REVIEW.md",
    "docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md",
    "docs/QUBIT-OFFICE-IDEA-LEDGER.md",
    "docs/QUBIT-OFFICE-PLAN-REVIEW.md",
)

OWNER_DECISIONS = (
    "full-scope-from-start",
    "reference-lane-validates-architecture-not-a-scope-cut",
    "no-second-database-session-authority-character-editor-or-business-ledger",
    "reuse-existing-engines-assets-and-approved-repos",
    "paid-limezu-art-is-local-only",
    "agents-office-polyform-and-no-license-repos-are-concepts-only",
    "habblaud-additive-only-behind-a-flag",
    "every-capability-must-work-on-the-reference-dashboard-and-by-voice",
    "no-fake-completion-pending-or-unknown-never-renders-completed",
    "typed-refusal-is-not-a-working-capability",
)

NON_GOALS = (
    "no live external side effects during phase acceptance",
    "no live capital or live marketplace publish",
    "no second execution engine before a measured HA need",
    "no all-100-floor render or all-agent continuous simulation",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_contract() -> dict:
    docs = []
    for relative in FROZEN_DOCS:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        docs.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    plan = next(item for item in docs
                if item["path"] == "docs/QUBIT-OFFICE-COMPLETE-PLAN.md")
    return {
        "schema": "qubit-office-phase0-contract/1",
        "task_id": "P0-01",
        "status": "FROZEN",
        "plan_version": PLAN_VERSION,
        "plan_sha256": plan["sha256"],
        "generated_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "owner_decisions": list(OWNER_DECISIONS),
        "non_goals": list(NON_GOALS),
        "docs": docs,
    }


def contract_drift(existing: dict, current: dict) -> list[str]:
    problems: list[str] = []
    if existing.get("schema") != current["schema"]:
        problems.append("schema changed")
    if existing.get("plan_version") != current["plan_version"]:
        problems.append("plan_version changed")
    old_docs = {row.get("path"): row.get("sha256")
                for row in existing.get("docs", [])
                if isinstance(row, dict)}
    new_docs = {row["path"]: row["sha256"] for row in current["docs"]}
    for path, digest in new_docs.items():
        if path not in old_docs:
            problems.append(f"missing frozen document: {path}")
        elif old_docs[path] != digest:
            problems.append(f"frozen document changed: {path}")
    for path in old_docs:
        if path not in new_docs:
            problems.append(f"document removed from contract: {path}")
    for decision in current["owner_decisions"]:
        if decision not in existing.get("owner_decisions", []):
            problems.append(f"owner decision missing: {decision}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true",
                        help="verify the frozen contract still matches")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        current = build_contract()
    except (OSError, FileNotFoundError) as exc:
        print(f"office_phase0_contract: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out

    if args.check:
        if not out.is_file():
            print(f"office_phase0_contract: missing {out}", file=sys.stderr)
            return 1
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"office_phase0_contract: {exc}", file=sys.stderr)
            return 1
        problems = contract_drift(existing, current)
        report = {"ok": not problems, "plan_version": PLAN_VERSION,
                  "documents": len(current["docs"]), "problems": problems}
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            for problem in problems:
                print(f"FAIL {problem}")
            print(f"office_phase0_contract: "
                  f"{'PASS' if not problems else 'FAIL'} "
                  f"(plan v{PLAN_VERSION}, {len(current['docs'])} docs)")
        return 0 if not problems else 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    if args.json:
        print(json.dumps({"ok": True, "out": str(out),
                          "plan_version": PLAN_VERSION,
                          "documents": len(current["docs"])}, sort_keys=True))
    else:
        print(f"office_phase0_contract: froze plan v{PLAN_VERSION} "
              f"({len(current['docs'])} documents) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
