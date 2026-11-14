#!/usr/bin/env python3
"""Verify phase evidence JSON: status, freshness, hashes and command outcomes.

Supports two shapes:

* a task evidence document (``task_id``/``status``/``inputs``/``outputs``/
  ``commands``), and
* a phase manifest (``phase``/``status``/``tasks``/``evidence_hashes``).

Repo-relative hashes are recomputed and must match. Absolute or missing paths
outside the repository are reported as ``external`` instead of failing, because
a generated artefact such as a local asset ledger intentionally lives outside
the tree and is not committed.

Exit codes: 0 all checked documents pass, 1 any failure, 2 usage/IO/corrupt.
No network, no writes.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_time(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc)


def resolve(path_value: str) -> Path:
    candidate = Path(path_value)
    return candidate if candidate.is_absolute() else ROOT / candidate


def check_hash_row(row: Any, problems: list[str], external: list[str],
                   label: str) -> None:
    if not isinstance(row, dict):
        problems.append(f"{label}: hash row is not an object")
        return
    raw_path = row.get("path")
    expected = row.get("sha256")
    if not isinstance(raw_path, str) or not raw_path.strip():
        problems.append(f"{label}: hash row has no path")
        return
    if not isinstance(expected, str) or len(expected) != 64:
        problems.append(f"{label}: {raw_path} has no sha256")
        return
    path = resolve(raw_path)
    if not path.exists():
        if Path(raw_path).is_absolute():
            external.append(f"{label}: {raw_path} (external, not present)")
        else:
            problems.append(f"{label}: {raw_path} is missing")
        return
    actual = sha256_file(path)
    if actual != expected:
        problems.append(
            f"{label}: {raw_path} sha256 {actual[:12]} != {expected[:12]}")


def check_task(doc: dict[str, Any], max_age_days: float) -> dict[str, Any]:
    problems: list[str] = []
    external: list[str] = []
    task_id = doc.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        problems.append("task_id is missing")
    status = doc.get("status")
    if status != "PASS":
        problems.append(f"status is {status!r}, expected 'PASS'")
    generated = parse_time(doc.get("generated_at"))
    now = _dt.datetime.now(_dt.timezone.utc)
    if generated is None:
        problems.append("generated_at is missing or not ISO-8601")
    else:
        age = (now - generated).total_seconds() / 86400.0
        if age < -0.01:
            problems.append("generated_at is in the future")
        elif age > max_age_days:
            problems.append(
                f"generated_at is {age:.1f} days old (limit {max_age_days:g})")
    for label in ("inputs", "outputs"):
        rows = doc.get(label, [])
        if not isinstance(rows, list):
            problems.append(f"{label} is not a list")
            continue
        for row in rows:
            check_hash_row(row, problems, external, label)
    commands = doc.get("commands", [])
    if not isinstance(commands, list) or not commands:
        problems.append("commands is empty")
    else:
        for index, command in enumerate(commands, 1):
            if not isinstance(command, dict):
                problems.append(f"command {index} is not an object")
                continue
            code = command.get("exit_code")
            expected = command.get("expected_exit", 0)
            if code != expected:
                problems.append(
                    f"command {index} exited {code!r}, expected {expected!r}")
    return {"kind": "task", "task_id": task_id, "problems": problems,
            "external": external}


def check_manifest(doc: dict[str, Any], max_age_days: float) -> dict[str, Any]:
    problems: list[str] = []
    external: list[str] = []
    if doc.get("status") != "PASS":
        problems.append(f"status is {doc.get('status')!r}, expected 'PASS'")
    generated = parse_time(doc.get("generated_at"))
    if generated is None:
        problems.append("generated_at is missing or not ISO-8601")
    else:
        age = (_dt.datetime.now(_dt.timezone.utc) - generated).total_seconds()
        if age < -60:
            problems.append("generated_at is in the future")
        elif age / 86400.0 > max_age_days:
            problems.append(f"manifest is older than {max_age_days:g} days")
    tasks = doc.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        problems.append("tasks is empty")
    else:
        for task in tasks:
            if task.get("exit_code", 1) != 0:
                problems.append(
                    f"task {task.get('task_id')!r} exited "
                    f"{task.get('exit_code')!r}")
    hashes = doc.get("evidence_hashes", {})
    if not isinstance(hashes, dict) or not hashes:
        problems.append("evidence_hashes is empty")
    else:
        for raw_path, expected in sorted(hashes.items()):
            check_hash_row(
                {"path": raw_path, "sha256": expected}, problems, external,
                "evidence_hashes")
    return {"kind": "manifest", "phase": doc.get("phase"),
            "problems": problems, "external": external}


def verify(paths: list[str], max_age_days: float) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    ok = True
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        entry: dict[str, Any] = {"path": str(raw)}
        if not path.exists():
            entry.update(ok=False, problems=["file is missing"])
        else:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                entry.update(ok=False, problems=[f"cannot read: {exc}"])
            else:
                if not isinstance(doc, dict):
                    entry.update(ok=False, problems=["document is not an object"])
                else:
                    if "task_id" in doc:
                        checked = check_task(doc, max_age_days)
                    elif "phase" in doc:
                        checked = check_manifest(doc, max_age_days)
                    else:
                        checked = {"kind": "unknown",
                                   "problems": ["neither task nor manifest"],
                                   "external": []}
                    entry.update(checked)
                    entry["ok"] = not checked["problems"]
        ok = ok and bool(entry.get("ok"))
        results.append(entry)
    return {"ok": ok, "checked": len(results), "max_age_days": max_age_days,
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", nargs="+", help="evidence JSON files")
    parser.add_argument("--max-age-days", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.max_age_days <= 0:
        print("verify_evidence: --max-age-days must be positive",
              file=sys.stderr)
        return 2
    try:
        report = verify(args.evidence, args.max_age_days)
    except OSError as exc:
        print(f"verify_evidence: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for entry in report["results"]:
            label = entry.get("task_id") or (
                f"phase {entry['phase']}" if entry.get("phase") is not None
                else entry["path"])
            print(f"{'PASS' if entry.get('ok') else 'FAIL'} {label}")
            for problem in entry.get("problems", []):
                print(f"  - {problem}")
            for note in entry.get("external", []):
                print(f"  · {note}")
        print(f"verify_evidence: {'PASS' if report['ok'] else 'FAIL'} "
              f"({report['checked']} document(s))")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
