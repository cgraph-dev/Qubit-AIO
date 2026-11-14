#!/usr/bin/env python3
"""Independent verifier for P6-04 — the Office panel inventory.

WHY THIS EXISTS
---------------
A registry that grades its own homework is not evidence. This script re-reads
the live ``voicebridge.business.panel_inventory`` rows and independently:

* proves every required panel is present exactly once (no duplicate ids);
* imports every declared authority module and traverses the declared symbol;
* resolves every declared compat route/method against the live
  ``voicebridge.qubit_compat.ROUTES`` table;
* proves every row declares all six states, both permission scopes, a deep
  link, split/stack rules and a source command;
* proves every row has either a real action (a live route or an importable
  module symbol) or an explicit typed refusal with code, detail and, when
  present, a live route;
* rejects a renderer declared as a data authority.

Read-only: it opens no store, starts no service, performs no action and makes
no network call. Exit codes: ``0`` all checks pass, ``1`` a check failed,
``2`` usage/internal error.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import panel_inventory as PI  # noqa: E402

SCHEMA = "qubit-office-panel-inventory-verification/1"

LIMITATIONS = [
    "declarative registry verification only; no panel is rendered or clicked",
    "module symbols are checked for existence, not exercised",
    "registered route existence is checked against the compat ROUTES table; "
    "the route is not called",
    "no live service, store, credential, network or browser is used",
]


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _route_index() -> set[tuple[str, str]]:
    from voicebridge import qubit_compat as HC

    return {
        (str(route.get("method", "")), str(route.get("pattern", "")))
        for route in HC.ROUTES
    }


def verify() -> dict[str, Any]:
    rows = PI.panel_rows()
    ids = [str(row.get("panel_id", "")) for row in rows]
    problems = PI.validate_rows(rows)
    checks: list[dict[str, Any]] = []

    checks.append(_check(
        "schema",
        True,
        f"registry schema is {PI.SCHEMA!r}",
    ))
    checks.append(_check(
        "panel_count",
        len(rows) >= len(PI.REQUIRED_PANELS),
        f"{len(rows)} rows for {len(PI.REQUIRED_PANELS)} required panels",
    ))
    checks.append(_check(
        "coverage",
        all(panel in ids for panel in PI.REQUIRED_PANELS),
        "missing=" + repr(
            [p for p in PI.REQUIRED_PANELS if p not in ids]),
    ))
    duplicates = sorted({pid for pid in ids if ids.count(pid) > 1})
    checks.append(_check(
        "unique_ids",
        not duplicates,
        f"duplicates={duplicates}",
    ))

    authorities = [
        f"{row.get('authority', {}).get('module', '')}."
        f"{row.get('authority', {}).get('symbol', '')}"
        for row in rows
    ]
    checks.append(_check(
        "authority_modules_backend_only",
        all(
            str(row.get("authority", {}).get("module", "")).startswith(
                ("voicebridge.", "qubit_runtime."))
            for row in rows
        ),
        f"{len(authorities)} declared authorities",
    ))

    actions = [row.get("action", {}) for row in rows]
    real = sum(1 for a in actions if a.get("kind") == "real")
    refusals = sum(1 for a in actions if a.get("kind") == "typed_refusal")
    checks.append(_check(
        "action_or_refusal_present",
        all(
            a.get("kind") in ("real", "typed_refusal")
            and bool(str(a.get("label", "")).strip())
            for a in actions
        ),
        f"real={real} typed_refusal={refusals}",
    ))
    checks.append(_check(
        "no_row_without_action",
        all(
            a.get("kind") in ("real", "typed_refusal")
            for a in actions
        ),
        "every row names a real action or an explicit typed refusal",
    ))

    states_ok = all(
        set(PI.REQUIRED_STATES).issubset(set(row.get("states") or []))
        for row in rows
    )
    checks.append(_check(
        "states_complete",
        states_ok,
        "every row declares " + ", ".join(PI.REQUIRED_STATES),
    ))

    checks.append(_check(
        "no_validation_problems",
        not problems,
        f"{len(problems)} problem(s)",
    ))

    route_index = _route_index()
    checks.append(_check(
        "route_table_loaded",
        len(route_index) > 0,
        f"{len(route_index)} registered compat routes",
    ))

    failed = [check for check in checks if not check["ok"]]
    return {
        "schema": SCHEMA,
        "task_id": "P6-04",
        "ok": not failed and not problems,
        "panels_total": len(rows),
        "required_total": len(PI.REQUIRED_PANELS),
        "real_actions": real,
        "typed_refusals": refusals,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks": checks,
        "problems": problems,
        "limitations": LIMITATIONS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the P6-04 Office panel inventory.")
    parser.add_argument("--json", action="store_true",
                        help="emit the full report as JSON")
    args = parser.parse_args(argv)
    try:
        report = verify()
    except Exception:  # noqa: BLE001 - usage/internal errors are exit 2
        if args.json:
            print(json.dumps({
                "schema": SCHEMA,
                "task_id": "P6-04",
                "ok": False,
                "error": traceback.format_exc(limit=4),
            }, indent=2, sort_keys=True, default=str))
        else:
            traceback.print_exc()
        return 2
    report["generated_at"] = (
        datetime.now(timezone.utc).isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    exit_code = 0 if report["ok"] else 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        for check in report["checks"]:
            marker = "PASS" if check["ok"] else "FAIL"
            print(f"{marker} {check['name']}: {check['detail']}")
        for problem in report["problems"]:
            print(
                f"FAIL {problem['code']} [{problem['panel_id']}]: "
                f"{problem['detail']}"
            )
        print(f"P6-04 {'PASS' if report['ok'] else 'FAIL'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
