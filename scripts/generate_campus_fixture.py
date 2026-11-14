#!/usr/bin/env python3
"""Generate the deterministic campus (tower) fixture for Phase 0 / P0-07.

The fixture is a *bounded* projection of the shipped 100-floor templates: it
records the expected counts, the full-projection digest and per-floor summary
rows, but it never materialises the 3,075-desk / 575-cell detail. It is a
**template**, not production state: no tenant, business or agent is placed, and
every floor/room carries ``staffed_agents: 0``.

    python scripts/generate_campus_fixture.py --check
    python scripts/generate_campus_fixture.py --out /tmp/qoffice-assets/campus.json

``--check`` writes nothing. ``--out`` writes the bounded fixture JSON.

Exit codes:
    0  pass
    1  an invariant failed, or ``validate_tower_plan`` reported problems
    2  usage / I/O error
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.office.world.tower_plan import (  # noqa: E402
    build_tower_plan,
    validate_tower_plan,
)

SCHEMA = "qubit-office-campus-fixture/1"

#: The measured invariants of the shipped templates (P0-07 acceptance).
EXPECTED = {
    "floors": 100,
    "wings": 5,
    "rooms": 300,
    "cells": 575,
    "desks": 3075,
    "staffed_agents": 0,
}

#: Identifiers that would indicate real production state leaked into the
#: template fixture. The template never carries any of these.
_TENANT_ID_RE = re.compile(r"\b(?:tenant|business|agent)[_-][0-9a-f]{6,}\b", re.I)


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def build_fixture(plan) -> Dict[str, Any]:
    counts = plan.counts()
    floors: List[Dict[str, Any]] = [
        {
            "floor_id": floor.floor_id,
            "index": int(floor.index),
            "wing": floor.wing,
            "name": floor.name,
            "active": bool(floor.active),
            "rooms": floor.room_count,
            "desks": floor.desk_count,
            "cells": floor.cell_count,
            "staffed_agents": 0,
        }
        for floor in plan.floors
    ]
    body = {
        "schema": SCHEMA,
        "template": True,
        "production": False,
        "staffed_agents": 0,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plant_digest": plan.digest,
        "counts": counts,
        "wings": [
            {"wing_id": wing.wing_id, "name": wing.name,
             "floors": wing.floor_count, "rooms": wing.room_count,
             "desks": wing.desk_count}
            for wing in plan.wings
        ],
        "floors": floors,
    }
    body["fixture_digest"] = _digest(body)
    return body


def check_fixture(fixture: Dict[str, Any], plan,
                  *, problems: Optional[List[str]] = None,
                  counts: Optional[Dict[str, int]] = None) -> List[str]:
    found: List[str] = list(problems or [])
    counts = dict(counts if counts is not None else plan.counts())
    for key, expected in EXPECTED.items():
        actual = counts.get(key)
        if actual != expected:
            found.append(f"counts.{key}: expected {expected}, got {actual}")
    if fixture.get("template") is not True:
        found.append("fixture.template must be true")
    if fixture.get("production") is not False:
        found.append("fixture.production must be false")
    if counts.get("staffed_agents") != 0:
        found.append("tower must staff zero agents")
    if fixture.get("staffed_agents") != 0:
        found.append("fixture.staffed_agents must be 0")
    if len(fixture.get("floors") or []) != len(plan.floors):
        found.append("fixture floors do not match the plan")
    for floor in fixture.get("floors") or []:
        if floor.get("staffed_agents") != 0:
            found.append(f"floor {floor.get('floor_id')!r}: staffed_agents != 0")
    # The template must carry no real tenant/business/agent identifiers.
    text = json.dumps(fixture, sort_keys=True)
    if _TENANT_ID_RE.search(text):
        found.append("fixture carries a tenant/business/agent identifier")
    return found


def _write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="build + validate the plan; write nothing")
    parser.add_argument("--out", help="write the bounded fixture JSON here")
    parser.add_argument("--fixture-bad", choices=("floors", "rooms", "desks", "staffed"),
                        default=None,
                        help="self-test: corrupt one invariant to prove --check fails")
    args = parser.parse_args(argv)

    if not args.check and not args.out:
        parser.error("choose --check and/or --out")

    try:
        plan = build_tower_plan()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"generate_campus_fixture: cannot build plan: {exc}", file=sys.stderr)
        return 2

    validation = validate_tower_plan(plan)
    fixture = build_fixture(plan)
    counts = plan.counts()

    if args.fixture_bad:
        counts = dict(counts)
        if args.fixture_bad == "staffed":
            counts["staffed_agents"] = 1
        else:
            counts[args.fixture_bad] = counts[args.fixture_bad] - 1

    problems = check_fixture(fixture, plan, problems=validation, counts=counts)

    if problems:
        for problem in problems:
            print(f"generate_campus_fixture: FAIL: {problem}", file=sys.stderr)
        return 1

    if args.out:
        try:
            _write(Path(args.out).expanduser(), fixture)
        except OSError as exc:
            print(f"generate_campus_fixture: cannot write {args.out}: {exc}",
                  file=sys.stderr)
            return 2
        print(f"wrote campus fixture to {args.out}")

    print(json.dumps({"check": "PASS", "counts": counts,
                      "digest": plan.digest,
                      "fixture_digest": fixture["fixture_digest"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
