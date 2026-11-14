#!/usr/bin/env python3
"""Validate the Qubit Office approved dependency lock.

The lock is the machine-readable form of ``docs/QUBIT-OFFICE-REPO-LEDGER.md``.
It records, for every dependency, what may be reused and under which license
status.  This validator is deliberately small: it checks the invariants that
would otherwise be enforced by prose only.

Exit codes:
    0  the lock is valid
    1  one or more invariant violations
    2  usage or I/O error (missing file, invalid JSON, unwritable output)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "qubit-office-dependency-lock/1"
DEFAULT_LOCK = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "QUBIT-OFFICE-DEPENDENCY-LOCK.json"
)

LICENSE_STATUSES = frozenset(
    {"present", "absent", "plan-recorded", "paid-local-only"}
)
REUSE_STATUSES = frozenset(
    {"concept", "candidate", "approved", "imported", "rejected"}
)
# A license that is not actually present may not be claimed as usable code.
RESTRICTED_LICENSE = frozenset({"absent", "plan-recorded"})
# A noncommercial/no-bundling license permits reading concepts, not importing code.
CONCEPT_ONLY_SPDX = frozenset({"PolyForm-Noncommercial-1.0.0"})

REQUIRED_ROW_FIELDS = (
    "id",
    "name",
    "local_path",
    "url",
    "revision",
    "license_spdx",
    "license_status",
    "reuse_status",
    "permitted_use",
    "prohibited_use",
    "notices",
    "reviewer",
    "reviewed_at",
)


def _violation(code: str, detail: str, row_id: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "detail": detail}
    if row_id is not None:
        item["id"] = row_id
    return item


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("reviewed_at must be a YYYY-MM-DD string or null")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"reviewed_at {value!r} is not an ISO date") from exc


def _validate_row(
    row: Any,
    index: int,
    today: dt.date,
    seen_ids: set[str],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not isinstance(row, dict):
        return [_violation("BAD_ROW", f"dependency[{index}] is not an object")]

    row_id = row.get("id")
    label = row_id if _is_nonempty_text(row_id) else f"dependency[{index}]"
    for field in REQUIRED_ROW_FIELDS:
        if field not in row:
            violations.append(
                _violation("MISSING_FIELD", f"missing required field {field!r}", label)
            )

    if not _is_nonempty_text(row.get("id")):
        violations.append(_violation("MISSING_FIELD", "id must be non-empty text", label))
    else:
        if row["id"] in seen_ids:
            violations.append(
                _violation("DUPLICATE_ID", f"duplicate dependency id {row['id']!r}", label)
            )
        else:
            seen_ids.add(row["id"])

    if not _is_nonempty_text(row.get("name")):
        violations.append(_violation("MISSING_FIELD", "name must be non-empty text", label))

    local_path = row.get("local_path")
    url = row.get("url")
    if not _is_nonempty_text(local_path) and not _is_nonempty_text(url):
        violations.append(
            _violation(
                "MISSING_FIELD",
                "local_path or url must name where the dependency lives",
                label,
            )
        )

    license_status = row.get("license_status")
    if license_status not in LICENSE_STATUSES:
        violations.append(
            _violation(
                "INVALID_STATUS",
                f"license_status {license_status!r} is not one of "
                f"{sorted(LICENSE_STATUSES)}",
                label,
            )
        )
    reuse_status = row.get("reuse_status")
    if reuse_status not in REUSE_STATUSES:
        violations.append(
            _violation(
                "INVALID_STATUS",
                f"reuse_status {reuse_status!r} is not one of {sorted(REUSE_STATUSES)}",
                label,
            )
        )

    if license_status in RESTRICTED_LICENSE and reuse_status in {"approved", "imported"}:
        violations.append(
            _violation(
                "UNAPPROVED_REUSE",
                f"license_status {license_status!r} cannot claim reuse_status "
                f"{reuse_status!r}",
                label,
            )
        )
    if (
        row.get("license_spdx") in CONCEPT_ONLY_SPDX
        and reuse_status in {"approved", "imported"}
    ):
        violations.append(
            _violation(
                "CONCEPT_ONLY_LICENSE",
                f"license_spdx {row.get('license_spdx')!r} permits concepts only;"
                f" it cannot claim reuse_status {reuse_status!r}",
                label,
            )
        )

    reviewer = row.get("reviewer")
    if not _is_nonempty_text(reviewer):
        violations.append(
            _violation("MISSING_FIELD", "reviewer must be a non-empty name", label)
        )

    try:
        reviewed = _parse_date(row.get("reviewed_at"))
    except ValueError as exc:
        violations.append(_violation("BAD_DATE", str(exc), label))
    else:
        if reviewed is not None and reviewed > today:
            violations.append(
                _violation(
                    "FUTURE_REVIEW",
                    f"reviewed_at {reviewed.isoformat()} is in the future",
                    label,
                )
            )

    notices = row.get("notices")
    if not (
        isinstance(notices, bool)
        or _is_nonempty_text(notices)
        or (isinstance(notices, list) and bool(notices))
    ):
        violations.append(
            _violation(
                "MISSING_FIELD",
                "notices must be a bool, non-empty text, or a non-empty list",
                label,
            )
        )
    return violations


def _validate_paid_assets(block: Any) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not isinstance(block, dict):
        return [
            _violation(
                "MISSING_PAID_BLOCK",
                "paid_assets_local_only must be an object",
            )
        ]
    if block.get("redistribution_allowed") is not False:
        violations.append(
            _violation(
                "PAID_REDISTRIBUTION",
                "paid_assets_local_only.redistribution_allowed must be false",
            )
        )
    packs = block.get("packs")
    if not isinstance(packs, list) or not packs:
        violations.append(
            _violation(
                "MISSING_PAID_BLOCK",
                "paid_assets_local_only.packs must be a non-empty list",
            )
        )
    else:
        for index, pack in enumerate(packs):
            if not isinstance(pack, dict) or not _is_nonempty_text(pack.get("name")):
                violations.append(
                    _violation(
                        "MISSING_PAID_BLOCK",
                        f"paid_assets_local_only.packs[{index}] must name a pack",
                    )
                )
    return violations


def validate(lock: Any, *, today: dt.date | None = None) -> list[dict[str, Any]]:
    """Return a list of violation records; an empty list means valid."""
    moment = today or dt.datetime.now(dt.timezone.utc).date()
    if not isinstance(lock, dict):
        return [_violation("BAD_SCHEMA", "lock root must be an object")]
    if lock.get("schema") != SCHEMA:
        return [
            _violation(
                "BAD_SCHEMA",
                f"schema must be {SCHEMA!r}, got {lock.get('schema')!r}",
            )
        ]

    dependencies = lock.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        return [
            _violation(
                "MISSING_FIELD",
                "dependencies must be a non-empty list",
            )
        ]

    violations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(dependencies):
        violations.extend(_validate_row(row, index, moment, seen_ids))
    violations.extend(_validate_paid_assets(lock.get("paid_assets_local_only")))
    return violations


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Qubit Office approved dependency lock."
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK,
        help="dependency lock JSON (default: docs/QUBIT-OFFICE-DEPENDENCY-LOCK.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable report instead of text",
    )
    args = parser.parse_args(argv)

    try:
        lock = _load(args.lock)
    except FileNotFoundError:
        print(f"error: lock file not found: {args.lock}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.lock}: {exc}", file=sys.stderr)
        return 2

    violations = validate(lock)
    checked = len(lock.get("dependencies", [])) if isinstance(lock, dict) else 0
    report = {
        "ok": not violations,
        "schema": lock.get("schema") if isinstance(lock, dict) else None,
        "lock": str(args.lock),
        "checked": checked,
        "violations": violations,
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif violations:
        for item in violations:
            where = f" [{item['id']}]" if item.get("id") else ""
            print(f"FAIL {item['code']}{where}: {item['detail']}")
        print(f"refused: {len(violations)} violation(s) across {checked} dependencies")
    else:
        print(f"PASS: {checked} dependencies valid at {lock.get('schema')}")

    return 0 if not violations else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
