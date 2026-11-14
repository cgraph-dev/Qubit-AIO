#!/usr/bin/env python3
"""P8-05 — assemble the Qubit Office release evidence and decision/waiver set.

This is the Phase 8 release gate. It **consumes** evidence produced by the
earlier phases and by the owner; it never produces the behavioural evidence
itself and it never signs anything.

Inputs (all read-only):

* ``evidence/phase-0..8/manifest.json`` — the phase gate artefacts. Each must
  exist, parse, report ``status: "PASS"``, be fresh and have every declared
  ``evidence_hashes`` entry still match the file on disk.
* ``docs/QUBIT-OFFICE-DEPENDENCY-LOCK.json`` — the dependency lock. The lock
  must parse, and no dependency may be in an unapproved reuse/licence state
  (an imported dependency needs a verified licence, a revision and notices).
* ``evidence/phase-8/final-matrix.json`` — the *Qubit Office feature matrix*
  evidence (see :mod:`scripts.final_matrix`), produced on the **frozen commit
  and tree**. It must state its scope honestly (office feature matrix, not the
  repo-wide matrix) and record zero failures.
* ``evidence/phase-8/owner-release-decision.json`` — the owner's signed release
  decision (and waiver set). This file is authored and signed by the owner.

What this tool refuses to do:

* invent an owner signature, a final matrix, or a PASS;
* treat a waiver as PASS evidence;
* accept a final matrix that was run on a different commit/tree;
* accept an imported dependency whose licence is not verified.

Verdicts written to ``evidence/phase-8/release.json``:

* ``PASS`` — every required phase manifest is valid/fresh, the dependency lock
  is approved, a matching final-matrix evidence file exists, and an owner
  decision record is present and internally consistent.
* ``READY_FOR_OWNER_DECISION`` — integrity checks passed but one or more
  required artefacts do not exist yet (e.g. ``FINAL_MATRIX_REQUIRED``). The
  exact missing items are listed under ``missing``.
* ``FAIL`` — a present artefact is invalid: stale/corrupt manifest, hash
  mismatch, wrong frozen SHA/tree, unapproved dependency, or a waiver/decision
  that does not meet the owner-sign-off rules.

Exit codes: 0 report produced (PASS or READY_FOR_OWNER_DECISION), 1 FAIL,
2 usage/IO. ``--require-pass`` turns READY into exit 1 for a hard gate.
No network, no live services, no writes outside ``--out``/``--task-evidence``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PHASES = tuple(range(0, 9))
DEFAULT_DEPENDENCY_LOCK = "docs/QUBIT-OFFICE-DEPENDENCY-LOCK.json"
DEFAULT_FINAL_MATRIX = "evidence/phase-8/final-matrix.json"
DEFAULT_OWNER_DECISION = "evidence/phase-8/owner-release-decision.json"
DEFAULT_OUT = "evidence/phase-8/release.json"

DEPENDENCY_LOCK_SCHEMA = "qubit-office-dependency-lock/1"
FINAL_MATRIX_SCHEMA = "qubit-office-final-matrix/1"
RELEASE_SCHEMA = "qubit-office-release-evidence/1"
DECISION_SCHEMA = "qubit-office-release-decision/1"

# A signature must name a human owner. These placeholders/agent identities are
# refused so a machine cannot quietly stand in for the owner.
NON_OWNER_SIGNERS = frozenset({
    "", "agent", "ai", "assistant", "bot", "claude", "codex", "gpt",
    "llm", "opencode", "qwen", "tbd", "unknown", "n/a", "none",
})

APPROVED_REUSE = frozenset({"concept", "candidate", "imported", "approved"})


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


def iso_now(now: _dt.datetime | None = None) -> str:
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "file is missing"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read: {exc}"
    if not isinstance(doc, dict):
        return None, "document is not a JSON object"
    return doc, None


def _freshness(value: Any, now: _dt.datetime, max_age_days: float,
               label: str) -> str | None:
    generated = parse_time(value)
    if generated is None:
        return f"{label}: generated_at is missing or not ISO-8601"
    age_days = (now - generated).total_seconds() / 86400.0
    if age_days < -0.01:
        return f"{label}: generated_at is in the future"
    if age_days > max_age_days:
        return (f"{label}: generated_at is {age_days:.1f} days old "
                f"(limit {max_age_days:g})")
    return None


def git_identifier(root: Path, commit: str = "HEAD") -> dict[str, str]:
    """Return the commit and tree for ``commit`` (defaults to HEAD)."""
    result: dict[str, str] = {"commit": "unknown", "tree": "unknown",
                              "status": "unknown"}
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
    result["status"] = "clean" if not status.stdout.strip() else "dirty"
    return result


def check_dependency_lock(root: Path, path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": _rel(root, path), "present": path.is_file(),
        "sha256": None, "ok": False, "problems": [], "unapproved": [],
    }
    doc, error = _load_json(path)
    if error is not None:
        entry["problems"].append(f"dependency lock: {error}")
        return entry
    entry["sha256"] = sha256_file(path)
    problems: list[str] = []
    unapproved: list[str] = []
    if doc.get("schema") != DEPENDENCY_LOCK_SCHEMA:
        problems.append(
            f"dependency lock schema is {doc.get('schema')!r}, "
            f"expected {DEPENDENCY_LOCK_SCHEMA!r}")
    deps = doc.get("dependencies")
    if not isinstance(deps, list) or not deps:
        problems.append("dependency lock lists no dependencies")
        deps = []
    seen: set[str] = set()
    for dep in deps:
        if not isinstance(dep, dict):
            problems.append("dependency entry is not an object")
            continue
        dep_id = dep.get("id")
        if not isinstance(dep_id, str) or not dep_id.strip():
            problems.append("dependency entry has no id")
            continue
        dep_id = dep_id.strip()
        if dep_id in seen:
            problems.append(f"duplicate dependency id: {dep_id}")
        seen.add(dep_id)
        reuse = dep.get("reuse_status")
        licence = dep.get("license_status")
        if reuse not in APPROVED_REUSE:
            unapproved.append(f"{dep_id}: unknown reuse_status {reuse!r}")
            continue
        if reuse in {"imported", "approved"}:
            if licence != "present":
                unapproved.append(
                    f"{dep_id}: {reuse} requires a verified licence, "
                    f"got {licence!r}")
            if not str(dep.get("revision") or "").strip():
                unapproved.append(
                    f"{dep_id}: {reuse} requires a recorded revision")
            if dep.get("notices") is not True:
                unapproved.append(
                    f"{dep_id}: {reuse} requires notices to be recorded")
        elif reuse == "candidate" and licence == "absent":
            unapproved.append(
                f"{dep_id}: candidate reuse cannot rest on an absent licence")
        if not str(dep.get("permitted_use") or "").strip():
            problems.append(f"{dep_id}: permitted_use is empty")
    paid = doc.get("paid_assets_local_only")
    if isinstance(paid, dict) and paid.get("redistribution_allowed") is not False:
        problems.append(
            "paid_assets_local_only.redistribution_allowed must be false")
    entry["problems"] = problems
    entry["unapproved"] = unapproved
    entry["ok"] = not problems and not unapproved
    entry["dependencies"] = len(deps)
    return entry


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def check_phase_manifests(root: Path, phases: tuple[int, ...],
                          now: _dt.datetime,
                          max_age_days: float) -> dict[str, Any]:
    present: list[int] = []
    missing: list[int] = []
    problems: list[str] = []
    manifests: list[dict[str, Any]] = []
    for phase in phases:
        rel = f"evidence/phase-{phase}/manifest.json"
        path = root / rel
        record: dict[str, Any] = {"phase": phase, "path": rel,
                                  "present": path.is_file()}
        if not path.is_file():
            missing.append(phase)
            manifests.append(record)
            continue
        present.append(phase)
        doc, error = _load_json(path)
        if error is not None:
            problems.append(f"phase {phase} manifest: {error}")
            record["problems"] = [error]
            manifests.append(record)
            continue
        record["sha256"] = sha256_file(path)
        record["status"] = doc.get("status")
        record["git_head"] = doc.get("git_head")
        record["generated_at"] = doc.get("generated_at")
        local: list[str] = []
        if doc.get("phase") != phase:
            local.append(
                f"phase field is {doc.get('phase')!r}, expected {phase}")
        if doc.get("status") != "PASS":
            local.append(f"status is {doc.get('status')!r}, expected 'PASS'")
        stale = _freshness(doc.get("generated_at"), now, max_age_days,
                           f"phase {phase} manifest")
        if stale:
            local.append(stale)
        tasks = doc.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            local.append("tasks is empty")
        else:
            for task in tasks:
                if not isinstance(task, dict):
                    local.append("task entry is not an object")
                elif task.get("exit_code") != 0:
                    local.append(
                        f"task {task.get('task_id')!r} exited "
                        f"{task.get('exit_code')!r}")
        hashes = doc.get("evidence_hashes")
        if not isinstance(hashes, dict) or not hashes:
            local.append("evidence_hashes is empty")
        else:
            for raw_path, expected in sorted(hashes.items()):
                evidence = _resolve(root, raw_path)
                if not isinstance(expected, str) or len(expected) != 64:
                    local.append(f"{raw_path}: invalid sha256 entry")
                elif not evidence.is_file():
                    local.append(f"{raw_path}: evidence file is missing")
                elif sha256_file(evidence) != expected:
                    local.append(
                        f"{raw_path}: sha256 does not match the manifest")
        record["problems"] = local
        problems.extend(
            f"phase {phase} manifest: {item}" for item in local)
        manifests.append(record)
    return {"required": list(phases), "present": present,
            "missing": missing, "problems": problems,
            "manifests": manifests}


def _sha_matches(recorded: Any, frozen: str) -> bool:
    if not isinstance(recorded, str) or not recorded.strip():
        return False
    if recorded == frozen:
        return True
    prefix = recorded.strip()
    return len(prefix) >= 7 and frozen.startswith(prefix)


def check_final_matrix(root: Path, path: Path, frozen_commit: str,
                       frozen_tree: str, now: _dt.datetime,
                       max_age_days: float) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": _rel(root, path), "present": path.is_file(), "sha256": None,
        "ok": False, "problems": [], "scope": None, "commit": None,
        "tree": None, "status": None,
    }
    if not path.is_file():
        return entry
    doc, error = _load_json(path)
    if error is not None:
        entry["problems"].append(f"final matrix: {error}")
        return entry
    entry["sha256"] = sha256_file(path)
    problems = entry["problems"]
    scope = doc.get("scope")
    entry["scope"] = scope
    entry["commit"] = doc.get("commit")
    entry["tree"] = doc.get("tree")
    entry["status"] = doc.get("status")
    if doc.get("schema") != FINAL_MATRIX_SCHEMA:
        problems.append(
            f"final matrix schema is {doc.get('schema')!r}, "
            f"expected {FINAL_MATRIX_SCHEMA!r}")
    status = doc.get("status")
    if status != "PASS":
        problems.append(f"final matrix status is {status!r}, expected 'PASS'")
    if not isinstance(scope, str):
        problems.append("final matrix scope is missing")
    else:
        lowered = scope.lower()
        if "office feature matrix" not in lowered:
            problems.append(
                "final matrix scope must say it is the office feature matrix")
        if "not repo-wide" not in lowered and "not the repo-wide" not in lowered:
            problems.append(
                "final matrix scope must state it is not the repo-wide matrix")
    if not _sha_matches(doc.get("commit"), frozen_commit):
        problems.append(
            f"final matrix commit {doc.get('commit')!r} is not the frozen "
            f"commit {frozen_commit}")
    if not _sha_matches(doc.get("tree"), frozen_tree):
        problems.append(
            f"final matrix tree {doc.get('tree')!r} is not the frozen "
            f"tree {frozen_tree}")
    stale = _freshness(doc.get("generated_at"), now, max_age_days,
                       "final matrix")
    if stale:
        problems.append(stale)
    result = doc.get("result")
    if not isinstance(result, dict):
        problems.append("final matrix result is missing")
    else:
        failed = result.get("failed")
        if not isinstance(failed, int):
            problems.append("final matrix result.failed is not an integer")
        elif failed != 0:
            problems.append(
                f"final matrix reports {failed} failed test(s)")
        passed = result.get("passed")
        if not isinstance(passed, int) or passed <= 0:
            problems.append(
                "final matrix result.passed must be a positive integer")
    entry["ok"] = not problems
    return entry


def _signature_problem(signed_by: Any) -> str | None:
    if not isinstance(signed_by, str):
        return "owner release decision has no signed_by"
    cleaned = signed_by.strip()
    if cleaned.lower() in NON_OWNER_SIGNERS:
        return (f"signed_by {signed_by!r} is not a named human owner; "
                "an agent cannot sign the release decision")
    return None


def _check_waiver(waiver: Any, now: _dt.datetime,
                  max_age_days: float) -> tuple[dict[str, Any], list[str]]:
    record = waiver if isinstance(waiver, dict) else {}
    problems: list[str] = []
    waiver_id = record.get("id")
    if not isinstance(waiver_id, str) or not waiver_id.strip():
        problems.append("waiver has no id")
        waiver_id = "<unnamed>"
    signoff = record.get("owner_signoff")
    signed_by = record.get("owner_signed_by")
    if signoff is not True and not (isinstance(signed_by, str)
                                    and signed_by.strip()):
        problems.append(f"waiver {waiver_id}: owner sign-off is missing")
    if not str(record.get("risk") or "").strip():
        problems.append(f"waiver {waiver_id}: risk is missing")
    if not str(record.get("compensating_control") or "").strip():
        problems.append(
            f"waiver {waiver_id}: compensating_control is missing")
    expires = parse_time(record.get("expires_at"))
    if expires is None:
        problems.append(f"waiver {waiver_id}: expires_at is missing/invalid")
    elif expires <= now:
        problems.append(f"waiver {waiver_id}: expiry has already passed")
    else:
        age_days = (expires - now).total_seconds() / 86400.0
        if age_days > max_age_days:
            problems.append(
                f"waiver {waiver_id}: expiry is more than "
                f"{max_age_days:g} days away")
    return {
        "id": waiver_id,
        "risk": record.get("risk"),
        "expires_at": record.get("expires_at"),
        "compensating_control": record.get("compensating_control"),
        "owner_signoff": signoff is True,
        "problems": problems,
    }, problems


def check_owner_decision(root: Path, path: Path, frozen_commit: str,
                         frozen_tree: str, now: _dt.datetime,
                         max_age_days: float) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": _rel(root, path), "present": path.is_file(), "sha256": None,
        "signed": False, "ok": False, "problems": [], "waivers": [],
    }
    if not path.is_file():
        return entry
    doc, error = _load_json(path)
    if error is not None:
        entry["problems"].append(f"owner release decision: {error}")
        return entry
    entry["sha256"] = sha256_file(path)
    problems = entry["problems"]
    schema = doc.get("schema")
    if schema is not None and schema != DECISION_SCHEMA:
        problems.append(
            f"owner decision schema is {schema!r}, "
            f"expected {DECISION_SCHEMA!r} (or omit it)")
    signature = _signature_problem(doc.get("signed_by"))
    if signature:
        problems.append(signature)
    else:
        entry["signed"] = True
        entry["signed_by"] = doc.get("signed_by")
    role = doc.get("role")
    if role is not None and str(role).strip().lower() != "owner":
        problems.append(
            f"owner decision role is {role!r}, expected 'owner'")
    decision = doc.get("decision")
    if decision != "APPROVED":
        problems.append(
            f"owner decision is {decision!r}, expected 'APPROVED'")
    if not _sha_matches(doc.get("release_sha"), frozen_commit):
        problems.append(
            f"owner decision release_sha {doc.get('release_sha')!r} is not "
            f"the frozen commit {frozen_commit}")
    recorded_tree = doc.get("release_tree")
    if recorded_tree is not None and not _sha_matches(recorded_tree,
                                                      frozen_tree):
        problems.append(
            f"owner decision release_tree {recorded_tree!r} is not the frozen "
            f"tree {frozen_tree}")
    stale = _freshness(doc.get("signed_at"), now, max_age_days,
                       "owner release decision")
    if stale:
        problems.append(stale)
    live = doc.get("live_trading") or {}
    if isinstance(live, dict) and live.get("ready") is True:
        live_signer = _signature_problem(live.get("signed_by"))
        if live_signer:
            problems.append(
                "live-trading readiness is claimed but not signed separately "
                f"by the owner ({live_signer})")
    waivers = doc.get("waivers")
    if waivers is not None and not isinstance(waivers, list):
        problems.append("owner decision waivers is not a list")
        waivers = []
    for waiver in waivers or []:
        record, local = _check_waiver(waiver, now, max_age_days)
        entry["waivers"].append(record)
        problems.extend(local)
    entry["ok"] = not problems
    entry["waiver_note"] = (
        "waivers are recorded exceptions, not PASS evidence")
    return entry


def evaluate(root: Path, *, phases: tuple[int, ...] = DEFAULT_PHASES,
             dependency_lock: str = DEFAULT_DEPENDENCY_LOCK,
             final_matrix: str = DEFAULT_FINAL_MATRIX,
             owner_decision: str = DEFAULT_OWNER_DECISION,
             max_age_days: float = 30.0,
             now: _dt.datetime | None = None,
             commit: str | None = None,
             tree: str | None = None) -> dict[str, Any]:
    """Build the release-evidence report without writing anything.

    ``commit``/``tree`` default to the repository HEAD; they are explicit
    parameters so callers (and tests) can pin the frozen SHA without a live
    checkout.
    """
    root = Path(root)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    frozen = git_identifier(root, commit or "HEAD")
    frozen_commit = commit or frozen["commit"]
    frozen_tree = tree or frozen["tree"]
    if tree is not None:
        frozen["tree"] = tree
        frozen["status"] = frozen.get("status", "unknown")
    phases_report = check_phase_manifests(root, phases, now, max_age_days)
    lock_report = check_dependency_lock(root, _resolve(root, dependency_lock))
    matrix_report = check_final_matrix(
        root, _resolve(root, final_matrix), frozen_commit, frozen_tree, now,
        max_age_days)
    decision_report = check_owner_decision(
        root, _resolve(root, owner_decision), frozen_commit, frozen_tree, now,
        max_age_days)

    problems: list[str] = []
    problems.extend(phases_report["problems"])
    problems.extend(lock_report["problems"])
    problems.extend(lock_report["unapproved"])
    problems.extend(matrix_report["problems"])
    problems.extend(decision_report["problems"])

    missing: list[str] = []
    for phase in phases_report["missing"]:
        missing.append(f"phase {phase} manifest (evidence/phase-{phase}/"
                       f"manifest.json)")
    if not matrix_report["present"]:
        missing.append(
            "FINAL_MATRIX_REQUIRED: final-matrix evidence for the frozen SHA "
            f"{frozen_commit[:12]} (run scripts/final_matrix.py)")
    if not decision_report["present"]:
        missing.append(
            "owner release decision record "
            "(evidence/phase-8/owner-release-decision.json)")

    if problems:
        status = "FAIL"
    elif missing:
        status = "READY_FOR_OWNER_DECISION"
    else:
        status = "PASS"

    limitations = [
        "this gate consumes evidence; it does not run the full repo matrix "
        "or the live behavioural oracle",
        "phase manifests are verified by content hash; their git_head is "
        "recorded, not required to equal the frozen SHA",
        "the final matrix is the Qubit Office feature matrix, not the "
        "repo-wide matrix",
        "waivers are recorded owner-approved exceptions, not PASS evidence",
        "no owner signature is created here; a missing decision is reported "
        "as READY_FOR_OWNER_DECISION",
        "no live services, credentials, network or trading readiness are "
        "exercised",
    ]
    if frozen.get("status") == "dirty":
        limitations.append(
            "the working tree was dirty when this report was generated; a "
            "final matrix must be run on the frozen commit's exact tree")

    return {
        "schema": RELEASE_SCHEMA,
        "document_type": "release-evidence",
        "release_task": "P8-05",
        "status": status,
        "generated_at": iso_now(now),
        "frozen": {
            "commit": frozen_commit,
            "tree": frozen_tree,
            "working_tree": frozen.get("status"),
        },
        "phases": phases_report,
        "dependency_lock": lock_report,
        "final_matrix": matrix_report,
        "owner_decision": decision_report,
        "waivers": decision_report.get("waivers", []),
        "missing": missing,
        "problems": _dedupe(problems),
        "limitations": limitations,
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def write_report(report: dict[str, Any], out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--phases", default=None,
                        help="comma-separated phase numbers (default 0-8)")
    parser.add_argument("--dependency-lock", default=DEFAULT_DEPENDENCY_LOCK)
    parser.add_argument("--final-matrix", default=DEFAULT_FINAL_MATRIX)
    parser.add_argument("--owner-decision", default=DEFAULT_OWNER_DECISION)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--max-age-days", type=float, default=30.0)
    parser.add_argument("--commit", default=None,
                        help="frozen commit (default: git HEAD)")
    parser.add_argument("--tree", default=None,
                        help="frozen tree (default: git HEAD's tree)")
    parser.add_argument("--require-pass", action="store_true",
                        help="exit 1 unless the verdict is PASS")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.max_age_days <= 0:
        print("release_evidence: --max-age-days must be positive",
              file=sys.stderr)
        return 2
    phases = DEFAULT_PHASES
    if args.phases:
        try:
            phases = tuple(int(part) for part in args.phases.split(","))
        except ValueError:
            print("release_evidence: --phases must be comma-separated ints",
                  file=sys.stderr)
            return 2
    try:
        report = evaluate(
            Path(args.root), phases=phases,
            dependency_lock=args.dependency_lock,
            final_matrix=args.final_matrix,
            owner_decision=args.owner_decision,
            max_age_days=args.max_age_days, commit=args.commit,
            tree=args.tree)
        out = write_report(report, _resolve(Path(args.root), args.out))
    except OSError as exc:
        print(f"release_evidence: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"release_evidence: {report['status']} -> {out}")
        for item in report["missing"]:
            print(f"  missing: {item}")
        for problem in report["problems"]:
            print(f"  FAIL: {problem}")
    if report["status"] == "FAIL":
        return 1
    if args.require_pass and report["status"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
