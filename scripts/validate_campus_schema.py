#!/usr/bin/env python3
"""Validate the full shipped campus and its versioned map document.

P3-01 builds every department floor/room/cell/desk from the shipped registry
(the Trading Department included) and P3-03 projects it into the versioned map
schema. This script is the machine gate for both: it builds the campus, runs
``validate_tower_plan`` and ``validate_map``, publishes one map version, and
prints a JSON report.

    PYTHONPATH=. python scripts/validate_campus_schema.py --json

Exit codes:
    0  every invariant passed
    1  a tower or map defect was found
    2  usage / I/O / import error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TASK_IDS = ("P3-01", "P3-03")
SCHEMA = "qubit-office-campus-validation/1"

EXPECTED_COUNTS = {
    "floors": 100,
    "wings": 5,
    "rooms": 300,
    "cells": 575,
    "desks": 3075,
    "staffed_agents": 0,
}

#: The Trading Department floor must carry these rooms (design §2).
TRADING_REQUIRED_ROOMS = (
    "Trading Floor",
    "Strategy Lab",
    "Risk Room",
    "Data Room",
    "Treasury / Accounting",
    "Compliance / Archive",
    "Incident Room",
)
TRADING_STANDARD_ROOMS = (
    "Reception",
    "Kitchen / Break",
    "Restrooms",
    "Lounge",
    "Boardroom",
    "Focus Rooms",
    "Head Office",
    "Storage / Archive",
    "Comms / Ops",
)


def _trading_report(registry: Any, plan: Any) -> Dict[str, Any]:
    trading_floor_id = None
    for template in registry.floors:
        if "trading" in template.department_ids:
            trading_floor_id = template.floor_id
            break
    if trading_floor_id is None:
        return {
            "floor_id": None,
            "rooms": [],
            "missing": list(TRADING_REQUIRED_ROOMS + TRADING_STANDARD_ROOMS),
        }
    floor = plan.floor(trading_floor_id)
    names = [room.name for room in floor.rooms]
    required = list(TRADING_REQUIRED_ROOMS + TRADING_STANDARD_ROOMS)
    return {
        "floor_id": trading_floor_id,
        "rooms": names,
        "missing": [
            name for name in required
            if not any(name in room_name for room_name in names)
        ],
        "department_roles": sorted(
            role.id for role in registry.roles if role.department == "trading"
        ),
        "desks": floor.desk_count,
        "cells": floor.cell_count,
    }


def build_report() -> Dict[str, Any]:
    from voicebridge.business.roles import RoleRegistry
    from voicebridge.office.world.map_schema import (
        MapRegistry,
        build_campus_map,
        map_digest,
        validate_map,
    )
    from voicebridge.office.world.tower_plan import (
        build_tower_plan,
        validate_tower_plan,
    )

    registry = RoleRegistry.default()
    plan = build_tower_plan(registry)
    tower_problems = list(validate_tower_plan(plan))
    counts = plan.counts()
    for key, expected in EXPECTED_COUNTS.items():
        if counts.get(key) != expected:
            tower_problems.append(
                f"counts.{key}: expected {expected}, got {counts.get(key)}")

    document = build_campus_map(plan)
    map_problems = list(validate_map(document))

    map_registry = MapRegistry()
    published = map_registry.publish(document)

    trading = _trading_report(registry, plan)
    if trading["missing"]:
        tower_problems.append(
            "trading floor is missing rooms: " + ", ".join(trading["missing"]))

    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "task_ids": list(TASK_IDS),
        "status": "PASS" if not tower_problems and not map_problems else "FAIL",
        "campus": {
            "counts": counts,
            "digest": plan.digest,
            "departments": len(registry.departments),
            "roles": len(registry.roles),
            "active_floors": plan.active_floor_count,
            "dormant_floors": plan.dormant_floor_count,
        },
        "trading": trading,
        "map": {
            "schema": document.schema,
            "version": document.version,
            "map_id": document.map_id,
            "map_version": published.version,
            "digest": map_digest(document),
            "floors": len(document.floors),
            "rooms": len(document.rooms),
            "cells": len(document.cells),
            "desks": len(document.desks),
            "portals": len(document.portals),
            "capacity": document.capacity,
            "egress": document.egress,
            "owner_id": document.owner_id,
            "tenant_id": document.tenant_id,
            "status": document.status,
        },
        "problems": {
            "tower": tower_problems,
            "map": map_problems,
        },
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        report = build_report()
    except Exception as exc:  # pragma: no cover - defensive usage/import path
        print(f"validate_campus_schema: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"{report['status']}: {report['campus']['counts']} "
            f"trading={report['trading']['floor_id']} "
            f"map={report['map']['digest'][:16]}"
        )
        for group, problems in report["problems"].items():
            for problem in problems:
                print(f"  {group}: {problem}", file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
