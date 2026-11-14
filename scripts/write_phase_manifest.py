#!/usr/bin/env python3
"""Write a phase manifest from per-task results and verified evidence.

The manifest is the phase gate artefact: ``status: PASS`` only when every task
exited 0 and every referenced evidence document passes ``verify_evidence``.
No network, no writes outside ``--out``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_evidence import verify  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True)
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, required=True)
    parser.add_argument("--results", required=True,
                        help="JSON list of task result objects")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limitation", action="append", default=[],
                        help="additional limitation to record in the manifest")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"write_phase_manifest: {exc}", file=sys.stderr)
        return 2
    if not isinstance(results, list) or not results:
        print("write_phase_manifest: results must be a non-empty list",
              file=sys.stderr)
        return 2

    evidence_paths = []
    for task in results:
        for path in task.get("evidence", []):
            if path not in evidence_paths:
                evidence_paths.append(path)

    report = verify(evidence_paths, max_age_days=30.0) if evidence_paths else {
        "ok": False, "results": [],
    }
    evidence_hashes = {}
    for raw in evidence_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            evidence_hashes[raw] = sha256_file(path)

    failed = [task for task in results if task.get("exit_code") != 0]
    status = "PASS" if not failed and report["ok"] else "FAIL"
    manifest = {
        "schema": "qubit-office-phase-manifest/1",
        "phase": args.phase,
        "status": status,
        "generated_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "git_head": git_head(),
        "python": platform.python_version(),
        "tasks": results,
        "evidence_hashes": evidence_hashes,
        "evidence_verification": {
            "ok": report["ok"],
            "checked": report.get("checked", 0),
            "failures": [
                {"path": entry.get("path"), "problems": entry.get("problems")}
                for entry in report.get("results", [])
                if not entry.get("ok")
            ],
        },
        "limitations": [
            "gate covers Phase 0 contracts, fixtures, replay and durable "
            "substrate only",
            "no live services, credentials or external side effects",
            "renderer metrics are data-path only; GPU frames are not measured",
            *args.limitation,
        ],
    }
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    if args.json:
        print(json.dumps({"ok": status == "PASS", "out": str(out),
                          "status": status}, sort_keys=True))
    else:
        print(f"write_phase_manifest: phase {args.phase} {status} -> {out}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
