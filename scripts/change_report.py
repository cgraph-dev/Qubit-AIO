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
    out = args.out
    if out is None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = args.title.lower().replace(" ", "_").replace("/", "_")
        out = OUT_DIR / f"{slug}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)

    if args.json:
        print(json.dumps({"title": args.title, "out": str(out),
                          "lines": len(md.splitlines()), "secrets": 0}, indent=2))
    print(f"wrote change report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())