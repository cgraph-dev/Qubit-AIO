#!/usr/bin/env python3
"""Bounded runner for the Qubit Office feature matrix (P8-05 evidence).

This runs **only** the Qubit Office feature matrix:

``tests/phase*`` (all phase suites), ``tests/office_contracts``,
``tests/test_business*.py``, ``tests/test_office_business.py`` and
``tests/test_office_runner.py``.

It is **not** the repo-wide matrix and must never be presented as one. The
evidence it writes labels its scope explicitly so a later reader cannot mistake
it for a full-repository gate.

The runner records the commit and tree the matrix ran on. ``status: "PASS"``
requires a clean working tree and a zero pytest exit code; otherwise the
evidence is ``"FAIL"`` with the reason, because a matrix run over uncommitted
changes does not prove the frozen commit.

Exit codes: 0 PASS, 1 FAIL, 2 usage/IO/collection error. No network; the runner
itself starts no service. It writes only ``--out``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "qubit-office-final-matrix/1"
DEFAULT_OUT = "evidence/phase-8/final-matrix.json"
SCOPE = (
    "Qubit Office feature matrix (tests/phase*, tests/office_contracts, "
    "tests/test_business*.py, tests/test_office_business.py, "
    "tests/test_office_runner.py); not the repo-wide matrix"
)
TARGET_GLOBS = (
    "tests/phase*",
    "tests/office_contracts",
    "tests/test_business*.py",
    "tests/test_office_business.py",
    "tests/test_office_runner.py",
)


def iso_now(moment: _dt.datetime | None = None) -> str:
    moment = moment or _dt.datetime.now(_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def collect_targets(root: Path) -> tuple[list[str], list[str]]:
    """Return ``(existing, missing)`` repo-relative matrix targets."""
    existing: list[str] = []
    missing: list[str] = []
    for pattern in TARGET_GLOBS:
        matches = sorted(
            path.relative_to(root) for path in root.glob(pattern)
            if path.exists())
        if not matches:
            missing.append(pattern)
        for match in matches:
            rel = str(match)
            if rel not in existing:
                existing.append(rel)
    return existing, missing


def git_identifier(root: Path, commit: str = "HEAD") -> dict[str, str]:
    result: dict[str, str] = {"commit": "unknown", "tree": "unknown",
                              "working_tree": "unknown"}
    try:
        head = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{commit}}"], cwd=root,
            capture_output=True, text=True, check=True)
        tree = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=root,
            capture_output=True, text=True, check=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root,
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return result
    result["commit"] = head.stdout.strip()
    result["tree"] = tree.stdout.strip()
    result["working_tree"] = "clean" if not status.stdout.strip() else "dirty"
    return result


def _tail(text: str, limit: int = 4000) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return "…" + cleaned[-limit:]


def build_evidence(*, commit: str, tree: str, working_tree: str,
                   targets: Iterable[str], command: str, exit_code: int,
                   stdout: str = "", stderr: str = "",
                   generated_at: str | None = None,
                   passed: int | None = None, failed: int | None = None,
                   duration_s: float | None = None) -> dict[str, Any]:
    """Assemble the final-matrix evidence document.

    ``status`` is PASS only for a clean tree and a zero exit code with at least
    one passing test.
    """
    target_list = list(targets)
    problems: list[str] = []
    if not target_list:
        problems.append("no matrix targets were collected")
    if exit_code != 0:
        problems.append(f"pytest exited {exit_code}")
    if working_tree != "clean":
        problems.append(
            "working tree was not clean; the matrix does not prove the "
            "frozen commit")
    if passed is None or passed <= 0:
        problems.append("no passing test count was recorded")
    if failed is None:
        problems.append("no failed test count was recorded")
    elif failed != 0:
        problems.append(f"{failed} test(s) failed")
    status = "PASS" if not problems else "FAIL"
    return {
        "schema": SCHEMA,
        "task_id": "P8-05",
        "kind": "office-feature-matrix",
        "scope": SCOPE,
        "generated_at": generated_at or iso_now(),
        "commit": commit,
        "tree": tree,
        "working_tree": working_tree,
        "status": status,
        "command": command,
        "targets": target_list,
        "result": {
            "exit_code": exit_code,
            "passed": passed,
            "failed": failed,
            "duration_s": duration_s,
        },
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "problems": problems,
        "limitations": [
            "Qubit Office feature matrix only; the repo-wide matrix is "
            "not run here",
            "no live services, browser, external provider or trading path "
            "is exercised",
            "GPU frame budgets and live device tiers are out of scope",
        ],
    }


def parse_counts(stdout: str) -> tuple[int | None, int | None]:
    """Extract ``(passed, failed)`` from a pytest ``-q`` summary line."""
    passed = failed = None
    for line in reversed(stdout.splitlines()):
        lowered = line.lower()
        if "no tests ran" in lowered:
            return 0, 0
        passed_match = re.search(r"(\d+) passed", lowered)
        failed_match = re.search(r"(\d+) failed", lowered)
        if passed_match:
            passed = int(passed_match.group(1))
            failed = int(failed_match.group(1)) if failed_match else 0
            return passed, failed
        if failed_match:
            return 0, int(failed_match.group(1))
    return passed, failed


def run_matrix(root: Path, *, commit: str, out: Path,
               extra_args: list[str] | None = None) -> dict[str, Any]:
    targets, missing = collect_targets(root)
    frozen = git_identifier(root, commit)
    command = [sys.executable, "-m", "pytest", "-q", *targets,
               *(extra_args or [])]
    command_text = " ".join(command)
    if missing:
        return build_evidence(
            commit=frozen["commit"], tree=frozen["tree"],
            working_tree=frozen["working_tree"], targets=targets,
            command=command_text, exit_code=2,
            stderr=f"missing matrix targets: {', '.join(missing)}")
    started = _dt.datetime.now(_dt.timezone.utc)
    result = subprocess.run(command, cwd=root, capture_output=True,
                            text=True)
    duration = (_dt.datetime.now(_dt.timezone.utc) - started).total_seconds()
    passed, failed = parse_counts(result.stdout)
    evidence = build_evidence(
        commit=frozen["commit"], tree=frozen["tree"],
        working_tree=frozen["working_tree"], targets=targets,
        command=command_text, exit_code=result.returncode,
        stdout=result.stdout, stderr=result.stderr, passed=passed,
        failed=failed, duration_s=round(duration, 3))
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--commit", default="HEAD",
                        help="commit to run/attribute the matrix to")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the scope and targets without running")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("extra", nargs=argparse.REMAINDER,
                        help="extra pytest args after --")
    args = parser.parse_args(argv)
    root = Path(args.root)
    targets, missing = collect_targets(root)
    frozen = git_identifier(root, args.commit)
    if args.dry_run:
        payload = {
            "schema": SCHEMA, "scope": SCOPE,
            "commit": frozen["commit"], "tree": frozen["tree"],
            "working_tree": frozen["working_tree"],
            "targets": targets, "missing": missing,
            "command": [sys.executable, "-m", "pytest", "-q", *targets],
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"final_matrix: {SCOPE}")
            print(f"commit {frozen['commit'][:12]} tree "
                  f"{frozen['tree'][:12]} ({frozen['working_tree']})")
            for target in targets:
                print(f"  target {target}")
            for target in missing:
                print(f"  MISSING {target}")
        return 0 if not missing else 1
    extra = [item for item in args.extra if item != "--"]
    try:
        evidence = run_matrix(root, commit=args.commit,
                              out=(root / args.out
                                   if not Path(args.out).is_absolute()
                                   else Path(args.out)),
                              extra_args=extra)
    except OSError as exc:
        print(f"final_matrix: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(evidence, indent=2, sort_keys=True))
    else:
        print(f"final_matrix: {evidence['status']} -> {args.out}")
        for problem in evidence["problems"]:
            print(f"  FAIL: {problem}")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
