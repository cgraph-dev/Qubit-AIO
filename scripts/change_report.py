#!/usr/bin/env python3
"""Packet 7B: change-report generator.

Collects the current git status/diff summary plus test/benchmark/risk/rollback
notes into a timestamped Markdown change report under docs/CHANGE_REPORTS/.
Never reads secrets and never touches live config or services.

Usage:
  python scripts/change_report.py --title "Packet 7B" \
      --tests "unit/contract/integration green" \
      --bench "rollback drill: N flags, user data preserved" \
      --risks "flag writes require a writable config + backup dir" \
      --rollback "restore the .bak that every flag write keeps"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "docs" / "CHANGE_REPORTS"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def policy_section() -> tuple[str, dict]:
    """Workspace-conformance findings + attestation (OC-D02), best effort."""
    try:
        from voicebridge.evaluation.policy_conformance import attestation, doctor

        rep = doctor(REPO)
        att = attestation(REPO, report=rep)
        lines = ["", "## Policy conformance", "",
                 f"- state: {rep['state']}",
                 f"- attestation: {att['attestation_id']}",
                 f"- rules: {', '.join(rep['ruleset'])}",
                 "- counts: " + ", ".join(
                     f"{k}={rep['counts'].get(k, 0)}"
                     for k in ("OK", "FAIL", "MISSING", "SKIPPED"))]
        for f in rep["findings"]:
            if f["state"] in ("FAIL", "MISSING"):
                lines.append(f"- {f['state']} [{f['severity']}] "
                             f"{f['rule']}: {f['detail']} ({f['path']})")
        return "\n".join(lines) + "\n", att
    except Exception as exc:  # never fail a report because policy failed
        return f"\n## Policy conformance\n\n- unavailable: {type(exc).__name__}\n", {}


def _record_run(title: str, md: str, out: Path) -> None:
    """Append an execution-identity receipt + logbook entry (OC-51/52)."""
    try:
        import hashlib

        from voicebridge.evaluation.logbook import Logbook, LogbookEntry
        from voicebridge.evaluation.receipts import (ExecutionIdentity,
                                                     ReceiptChain)

        ident = ExecutionIdentity.create(run_id=title or "change-report",
                                         actor="report-script",
                                         workspace=str(REPO),
                                         tool="change_report")
        chain = ReceiptChain(ident)
        chain.append(status="OK",
                     outputs_digest=hashlib.sha256(md.encode()).hexdigest())
        base = out.with_suffix("")
        (base.parent / (base.name + ".receipts.jsonl")).write_text(
            "".join(json.dumps(r) + "\n" for r in chain.receipts()))
        lb = Logbook(base.parent / (base.name + ".logbook.jsonl"))
        lb.append(LogbookEntry.create(kind="report", summary=title,
                                      source="monitor",
                                      evidence_refs=[str(out)]))
    except Exception:
        pass


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(REPO),
                             capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr or "").strip()
    except Exception as exc:
        return f"(git unavailable: {exc})"


def _status_short() -> str:
    out = _git("status", "--short")
    return out or "(clean tree)"


def _diff_stat() -> str:
    out = _git("diff", "--stat")
    return out or "(no staged/working diff)"


def build_report(title: str, tests: str, bench: str, risks: str,
                 rollback: str) -> str:
    now = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return "\n".join([
        f"# Change report — {title}",
        "",
        f"Generated: {now}",
        f"Repository: {REPO}",
        "",
        "## Files changed",
        "```",
        _status_short(),
        "```",
        "",
        "## Diff summary",
        "```",
        _diff_stat(),
        "```",
        "",
        "## Tests",
        tests,
        "",
        "## Benchmarks / measurements",
        bench,
        "",
        "## Risks",
        risks,
        "",
        "## Rollback",
        rollback,
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="generate a change report")
    ap.add_argument("--title", required=True)
    ap.add_argument("--tests", default="", help="test commands/results")
    ap.add_argument("--bench", default="", help="benchmarks/measurements")
    ap.add_argument("--risks", default="", help="known risks")
    ap.add_argument("--rollback", default="", help="rollback instructions")
    ap.add_argument("--out", type=Path, default=None,
                    help="output file (default: docs/CHANGE_REPORTS/<title>.md)")
    ap.add_argument("--json", action="store_true", help="also print JSON metadata")
    args = ap.parse_args(argv)

    md = build_report(args.title, args.tests, args.bench, args.risks, args.rollback)
    section, att = policy_section()
    md = md + section
    out = args.out
    if out is None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = args.title.lower().replace(" ", "_").replace("/", "_")
        out = OUT_DIR / f"{slug}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    _record_run(args.title, md, out)

    if args.json:
        print(json.dumps({"title": args.title, "out": str(out),
                          "lines": len(md.splitlines()), "secrets": 0,
                          "conformance": att.get("state", "unavailable"),
                          "attestation_id": att.get("attestation_id", "")},
                         indent=2))
    print(f"wrote change report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())