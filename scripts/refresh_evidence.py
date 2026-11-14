#!/usr/bin/env python3
"""Re-run the commands recorded in an evidence document and refresh its hashes.

This is the integration-lead tool for the case where a parallel lane changed a
shared source file and the evidence's input hash went stale. It executes the
recorded commands for real, records their observed exit codes and tails, then
recomputes repository-relative input/output hashes. A non-zero command (other
than an explicitly expected one) makes the evidence ``FAIL`` and the tool exits
1 — it never upgrades a result.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def refresh(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    failures: list[str] = []
    commands = []
    for index, command in enumerate(doc.get("commands", []), 1):
        cmd = command.get("cmd")
        expected = command.get("expected_exit", 0)
        if not isinstance(cmd, str) or not cmd.strip():
            failures.append(f"command {index} has no cmd")
            continue
        result = subprocess.run(
            cmd, cwd=ROOT, shell=True, capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT),
                 "TMPDIR": __import__("os").environ.get("TMPDIR", "/tmp")},
            timeout=1800,
        )
        tail_out = "\n".join(result.stdout.strip().splitlines()[-20:])
        tail_err = "\n".join(result.stderr.strip().splitlines()[-20:])
        commands.append({
            "cmd": cmd,
            "exit_code": result.returncode,
            "expected_exit": expected,
            "stdout_tail": tail_out,
            "stderr_tail": tail_err,
        })
        if result.returncode != expected:
            failures.append(
                f"command {index} exited {result.returncode}, expected {expected}")
    for label in ("inputs", "outputs"):
        for row in doc.get(label, []):
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                continue
            candidate = resolve(row["path"])
            if candidate.is_file():
                row["sha256"] = sha256_file(candidate)
    doc["commands"] = commands
    doc["generated_at"] = (
        _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
        .isoformat().replace("+00:00", "Z")
    )
    doc["status"] = "PASS" if not failures else "FAIL"
    notes = doc.setdefault("limitations", [])
    if failures and "refresh failures" not in " ".join(notes):
        notes.append("refresh failures: " + "; ".join(failures))
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return {"path": str(path), "status": doc["status"], "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    reports = []
    for raw in args.evidence:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            print(f"refresh_evidence: missing {path}", file=sys.stderr)
            return 2
        reports.append(refresh(path))
    ok = all(item["status"] == "PASS" for item in reports)
    if args.json:
        print(json.dumps({"ok": ok, "results": reports}, indent=2,
                         sort_keys=True))
    else:
        for item in reports:
            print(f"{item['status']} {item['path']}")
            for failure in item["failures"]:
                print(f"  - {failure}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
