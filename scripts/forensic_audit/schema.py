"""Core model for the re-runnable Qubit-vs-upstream forensic audit.

The audit is a *living* artifact: checks produce findings, findings live in a
ledger keyed by a stable ``fingerprint``, and each run diffs against the ledger
to classify every finding as ``NEW`` / ``OPEN`` / ``FIXED`` / ``REGRESSED``.
Owner triage (``ACCEPTED`` / ``WONTFIX`` / ``OPERATOR``) is an overlay that
survives runs.

Design rules (mirroring ``scripts/parity_inventory.py``):

* **Read-only.** Checks never write product files; only ``--update`` writes the
  ledger and the report, under ``docs/generated/audit/``.
* **Deterministic.** ``build_findings`` emits rows sorted by
  ``(severity, check, fingerprint)`` with ``json.dumps(sort_keys=True)`` and no
  timestamps, so two renders are byte-identical.
* **Stable ids.** The fingerprint hashes ``check|path|slug`` and never the line
  number, so a moved line is not a new finding.
* **No milestone claims.** A finding is a pointer to a defect, never parity.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_LEDGER = "qubit-forensic-audit-ledger/1"
SCHEMA_RUN = "qubit-forensic-audit-run/1"

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

# Lifecycle statuses.
STATUS_NEW = "NEW"
STATUS_OPEN = "OPEN"
STATUS_FIXING = "FIXING"
STATUS_FIXED = "FIXED"
STATUS_REGRESSED = "REGRESSED"
# Owner-triage terminal statuses (never flap).
TRIAGE_STATUSES = ("ACCEPTED", "WONTFIX", "OPERATOR")

# Classifications (the forensic enum, kept identical to the audit brief).
CLASSIFICATIONS = (
    "BEHAVIORALLY_IDENTICAL", "QUBIT_REBRANDED_EQUIVALENT", "QUBIT_ADDITIONAL",
    "PARTIAL", "MISSING", "BROKEN", "MISCONFIGURED", "DEPENDENCY_FAILURE",
    "ENVIRONMENT_OR_CREDENTIAL_REQUIRED", "INTENTIONAL_DIFFERENCE",
    "UNKNOWN_REQUIRES_REPRODUCTION",
)

# Gating: a NEW or REGRESSED finding at these severities fails --check.
GATING_SEVERITIES = ("CRITICAL", "HIGH")


def fingerprint(check: str, path: str, slug: str) -> str:
    """Stable identity for a finding; deliberately excludes line numbers."""
    raw = f"{check}|{path}|{slug}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def finding(
    check: str,
    slug: str,
    title: str,
    *,
    path: str = "",
    line: int = 0,
    subsystem: str = "",
    detail: str = "",
    repro: str = "",
    severity: str = "MEDIUM",
    classification: str = "UNKNOWN_REQUIRES_REPRODUCTION",
    cause_type: str = "unknown",
    tier: int = 0,
) -> Dict[str, Any]:
    sev = severity.upper()
    if sev not in SEVERITY_ORDER:
        raise ValueError(f"bad severity: {severity}")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"bad classification: {classification}")
    fp = fingerprint(check, path, slug)
    return {
        "id": f"{check}/{slug}",
        "fingerprint": fp,
        "check": check,
        "tier": tier,
        "title": title,
        "subsystem": subsystem or check,
        "path": path,
        "line": int(line or 0),
        "detail": detail,
        "repro": repro,
        "severity": sev,
        "classification": classification,
        "cause_type": cause_type,
        "status": STATUS_NEW,
    }


def sort_findings(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            SEVERITY_ORDER.get(r.get("severity", "INFO"), 9),
            r.get("check", ""),
            r.get("fingerprint", ""),
        ),
    )


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=1) + "\n"


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def empty_ledger() -> Dict[str, Any]:
    return {"schema": SCHEMA_LEDGER, "run_seq": 0, "findings": {}}


def load_ledger(path: Path) -> Dict[str, Any]:
    data = load_json(path)
    if not data:
        return empty_ledger()
    if data.get("schema") != SCHEMA_LEDGER:
        raise ValueError(f"unexpected ledger schema: {data.get('schema')!r}")
    return data


def load_triage(path: Path) -> Dict[str, Dict[str, str]]:
    """Owner overlay: ``{fingerprint: {"status": ..., "note": ...}}``."""
    data = load_json(path) or {}
    return data.get("triage", {})


def apply_triage(rows: List[Dict[str, Any]],
                 triage: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    for r in rows:
        t = triage.get(r["fingerprint"])
        if t and t.get("status") in TRIAGE_STATUSES:
            r["status"] = t["status"]
            if t.get("note"):
                r["triage_note"] = t["note"]
    return rows


def diff_against_ledger(
    current: List[Dict[str, Any]], previous: Dict[str, Any],
    run_check_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Assign lifecycle status by diffing ``current`` against the ledger.

    - fingerprint absent from previous                -> NEW
    - previous FIXED and present now                  -> REGRESSED
    - owner triage status (ACCEPTED/WONTFIX/OPERATOR) -> preserved
    - previous OPEN/FIXING/NEW/REGRESSED and present  -> OPEN
    - previous status and absent now                  -> FIXED

    ``run_check_ids`` scopes the diff: findings from checks that were NOT run
    (e.g. a ``--fast`` or ``--only`` subset) are preserved untouched instead of
    being falsely closed as FIXED.
    """
    prev_all = previous.get("findings", {})
    if run_check_ids is not None:
        run_set = set(run_check_ids)
        prev = {fp: old for fp, old in prev_all.items()
                if old.get("check") in run_set}
        preserved = [old for old in prev_all.values()
                     if old.get("check") not in run_set]
    else:
        prev = prev_all
        preserved = []
    out: List[Dict[str, Any]] = []
    for row in current:
        fp = row["fingerprint"]
        old = prev.get(fp)
        if old is None:
            row["status"] = STATUS_NEW
        else:
            old_status = old.get("status", STATUS_OPEN)
            if old_status in TRIAGE_STATUSES:
                row["status"] = old_status
                if old.get("triage_note"):
                    row["triage_note"] = old["triage_note"]
            elif old_status == STATUS_FIXED:
                row["status"] = STATUS_REGRESSED
            elif old_status == STATUS_FIXING:
                row["status"] = STATUS_FIXING
            else:
                row["status"] = STATUS_OPEN
        row["first_seen"] = (old or {}).get("first_seen", previous.get("run_seq", 0) + 1)
        out.append(row)

    current_fps = {r["fingerprint"] for r in current}
    for fp, old in prev.items():
        if fp in current_fps:
            continue
        if old.get("status") in TRIAGE_STATUSES:
            # Kept as-is; not re-evaluated.
            closed = dict(old)
            closed["status"] = old["status"]
            out.append(closed)
        else:
            closed = dict(old)
            closed["status"] = STATUS_FIXED
            closed["last_seen"] = previous.get("run_seq", 0)
            out.append(closed)
    out.extend(preserved)
    return out


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    by_check: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + 1
        by_check[r["check"]] = by_check.get(r["check"], 0) + 1
    # Only Tier-0 (deterministic) findings gate: Tier-1 is a snapshot backlog,
    # not a fresh regression signal.
    gating = [
        r["id"] for r in rows
        if r["status"] in (STATUS_NEW, STATUS_REGRESSED)
        and r["severity"] in GATING_SEVERITIES
        and r.get("tier", 0) == 0
    ]
    return {
        "total": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_check": dict(sorted(by_check.items())),
        "gating": sorted(gating),
    }


def ledger_payload(rows: List[Dict[str, Any]], run_seq: int) -> Dict[str, Any]:
    findings = {r["fingerprint"]: r for r in sort_findings(rows)}
    return {"schema": SCHEMA_LEDGER, "run_seq": run_seq, "findings": findings}
