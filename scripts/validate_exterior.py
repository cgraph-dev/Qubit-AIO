#!/usr/bin/env python3
"""Validate the Qubit Office outside-world data (P3-06).

It builds the real :func:`voicebridge.office.world.exterior.build_exterior_map`
and checks the properties the plan requires:

* unique zone/chunk/portal/asset ids, chunk bounds inside the campus;
* chunk connectivity (global, per-zone, and through portals);
* lobby egress: entrance + outside plus at least one elevator and stairs;
* only active zones animate; dormant zones stay static;
* bounded chunks/assets/LRU/preload (no unbounded resource loading);
* every required exterior feature is represented;
* paid art references are local absolute paths outside the repository.

``--json`` prints one machine-readable report. Nothing is written; the check is
read-only and hermetic (no network, no service, no store).

Exit codes:
    0  every check passed
    1  at least one check failed
    2  usage / build error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.office.world.exterior import (  # noqa: E402
    EXTERIOR_FEATURES,
    SCHEMA,
    VERSION,
    build_exterior_map,
    validate_exterior,
)

EXPECTED_COUNTS = {
    "zones": 6,
    "chunks": 20,
    "portals": 8,
    "assets": 15,
    "active_zones": 5,
    "dormant_zones": 1,
}


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_checks() -> Dict[str, Any]:
    """Build the map, validate it and return the full report."""
    map_obj = build_exterior_map()
    problems = validate_exterior(map_obj)
    counts = map_obj.counts()

    checks: List[Dict[str, Any]] = [
        _check("schema_version", map_obj.schema == SCHEMA
               and map_obj.version == VERSION,
               f"{map_obj.schema} v{map_obj.version}"),
        _check("digest_stable", bool(map_obj.digest)
               and map_obj.digest == build_exterior_map().digest,
               map_obj.digest),
        _check("zone_count", counts["zones"] == EXPECTED_COUNTS["zones"],
               f"zones={counts['zones']}"),
        _check("chunk_count", counts["chunks"] == EXPECTED_COUNTS["chunks"],
               f"chunks={counts['chunks']}"),
        _check("portal_count", counts["portals"] == EXPECTED_COUNTS["portals"],
               f"portals={counts['portals']}"),
        _check("asset_count", counts["assets"] == EXPECTED_COUNTS["assets"],
               f"assets={counts['assets']}"),
        _check("dormant_zone_present", counts["dormant_zones"] >= 1,
               f"dormant_zones={counts['dormant_zones']}"),
        _check("no_animation_in_dormant_zone",
               all(not c.animated for c in map_obj.chunks
                   if not next(z for z in map_obj.zones
                               if z.zone_id == c.zone_id).active),
               "only active zones animate"),
        _check("lobby_has_elevator_and_stairs",
               any(p.kind == "elevator" for p in map_obj.portals)
               and any(p.kind == "stairs" for p in map_obj.portals),
               "elevator + stairs portals declared"),
        _check("features_complete",
               set(EXTERIOR_FEATURES) <= (
                   {a.feature for a in map_obj.assets}
                   | {c.kind for c in map_obj.chunks}),
               f"{len(EXTERIOR_FEATURES)} features represented"),
        _check("paid_art_local_only",
               all((not a.paid) or (a.local_only
                                    and not a.redistribution_allowed)
                   for a in map_obj.assets),
               "paid references are local-only"),
        _check("resource_bounds",
               len(map_obj.chunks) <= map_obj.resource_policy.max_chunks
               and sum(1 for c in map_obj.chunks if c.active)
               <= map_obj.resource_policy.max_active_chunks,
               f"max_chunks={map_obj.resource_policy.max_chunks},"
               f" max_active={map_obj.resource_policy.max_active_chunks}"),
        _check("validation_clean", not problems,
               "no problems" if not problems else "; ".join(problems[:5])),
    ]

    failed = sum(1 for c in checks if not c["ok"])
    return {
        "schema": "qubit-office-exterior-validation/1",
        "ok": failed == 0,
        "mode": "self-check",
        "map_schema": SCHEMA,
        "digest": map_obj.digest,
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
        "problems": problems,
        "counts": counts,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Qubit Office exterior map data.")
    parser.add_argument("--json", action="store_true",
                        help="print one machine-readable JSON report")
    args = parser.parse_args(argv)
    try:
        report = run_checks()
    except Exception as exc:  # noqa: BLE001 - usage/build error, never a pass
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "schema": "qubit-office-exterior-validation/1"}))
        else:
            print(f"validate_exterior: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"[{mark}] {check['name']}: {check['detail']}")
        print(f"exterior: {report['counts']}")
        print("ok" if report["ok"] else "failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
