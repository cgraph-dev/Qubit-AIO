#!/usr/bin/env python3
"""Verify the Qubit Office outside-world presentation projection (P6-03).

This is the evidence instrument for P6-03. It builds the real presentation
projection over :mod:`voicebridge.office.world.exterior` and checks exactly the
acceptance items the plan names:

* the projection is deterministic and its digest is stable;
* the exterior landmarks and the lobby/security/elevator/stairs strip are all
  presented as semantic places;
* only active zones animate; active/dormant chunks agree with their zone;
* the environment maps every declared weather/day-night/season value to an
  animated cue **and** a reduced-motion still cue;
* portal links reach the tower lobby and at least floor-001 via elevator and
  stairs;
* paid art stays local-only, non-redistributable and outside the public tree;
* no presentation payload grows a business-truth field.

``--json`` prints one machine-readable report. Nothing is written; the check is
read-only and hermetic (no network, no service, no store, no art read).

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

from voicebridge.office.world.outside_world import (  # noqa: E402
    REQUIRED_PLACES,
    SCHEMA,
    VERSION,
    build_outside_world,
    environment_events,
    outside_world_payload,
    validate_outside_world,
)
from voicebridge.office.world.exterior import build_exterior_map  # noqa: E402

SCHEMA_REPORT = "qubit-office-outside-world-verification/1"


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_checks() -> Dict[str, Any]:
    """Build the projection, validate it and return the full report."""
    map_obj = build_exterior_map()
    world = build_outside_world(map_obj)
    again = build_outside_world(build_exterior_map())
    problems = validate_outside_world(
        world, repo_root=REPO_ROOT, map_obj=map_obj)
    payload = outside_world_payload(world)

    zones = world.zones
    chunks = world.chunks
    active_zone_ids = {z["zone_id"] for z in zones if z["state"] == "active"}
    dormant_zone_ids = {z["zone_id"] for z in zones
                        if z["state"] == "dormant"}
    animated = [c for c in chunks if c["animated"]]
    active_chunks = [c for c in chunks if c["active"]]

    declared = {
        ("weather", v) for v in map_obj.variants.weather
    } | {
        ("day_night", v) for v in map_obj.variants.day_night
    } | {
        ("season", v) for v in map_obj.variants.seasons
    }
    events = {(e["kind"], e["value"]): e for e in environment_events(map_obj)}
    mapped = set(events)

    kinds = {p["kind"] for p in world.places}
    kinds.update(p["kind"] for p in world.portals)
    missing_places = [label for label, kind in REQUIRED_PLACES
                      if kind not in kinds]

    paid = [a for a in world.assets if a["paid"]]
    paid_ok = all(
        a["local_only"] and not a["redistribution_allowed"] for a in paid)

    checks: List[Dict[str, Any]] = [
        _check("schema_version",
               world.schema == SCHEMA and world.version == VERSION,
               f"{world.schema} v{world.version}"),
        _check("deterministic",
               world.to_dict() == again.to_dict()
               and world.digest == again.digest,
               world.digest),
        _check("map_digest_matches",
               world.map_digest == map_obj.digest,
               world.map_digest),
        _check("projection_valid", not problems,
               "no problems" if not problems else "; ".join(problems[:6])),
        _check("semantic_places",
               not missing_places,
               "all landmarks presented" if not missing_places
               else f"missing {missing_places}"),
        _check("active_and_dormant_zones",
               bool(active_zone_ids) and bool(dormant_zone_ids),
               f"active={sorted(active_zone_ids)}"
               f" dormant={sorted(dormant_zone_ids)}"),
        _check("only_active_zones_animate",
               all(c["zone_id"] in active_zone_ids for c in animated),
               f"{len(animated)} animated chunks in active zones only"),
        _check("bounded_active_chunks",
               len(chunks) <= map_obj.resource_policy.max_chunks
               and len(active_chunks)
               <= map_obj.resource_policy.max_active_chunks,
               f"chunks={len(chunks)}"
               f" max_chunks={map_obj.resource_policy.max_chunks}"
               f" active={len(active_chunks)}"
               f" max_active={map_obj.resource_policy.max_active_chunks}"),
        _check("environment_event_mapping",
               mapped == declared,
               f"{len(mapped)}/{len(declared)} variant events mapped"),
        _check("reduced_motion_cues",
               all(str(e.get("still_cue") or "").strip()
                   and str(e.get("label") or "").strip()
                   for e in events.values()),
               "every event carries text + still cue"),
        _check("lobby_to_tower_floors",
               any(link["kind"] == "elevator" for link in world.floor_links)
               and any(link["kind"] == "stairs" for link in world.floor_links)
               and any(link["floor_id"] == "floor-001"
                       for link in world.floor_links),
               f"floor_links={len(world.floor_links)}"),
        _check("paid_art_local_only",
               paid_ok,
               f"{len(paid)} paid references stay local-only"),
        _check("presentation_only",
               world.constraints.get("presentation_only") is True
               and world.constraints.get("business_truth") is False,
               "projection declares presentation-only"),
        _check("payload_ok", payload["ok"] is True,
               f"summary={payload['summary']}"),
    ]

    failed = sum(1 for c in checks if not c["ok"])
    return {
        "schema": SCHEMA_REPORT,
        "ok": failed == 0,
        "mode": "self-check",
        "projection_schema": SCHEMA,
        "digest": world.digest,
        "map_digest": world.map_digest,
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
        "problems": problems,
        "summary": dict(world.summary),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Qubit Office outside-world projection.")
    parser.add_argument("--json", action="store_true",
                        help="print one machine-readable JSON report")
    args = parser.parse_args(argv)
    try:
        report = run_checks()
    except Exception as exc:  # noqa: BLE001 - usage/build error, never a pass
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "schema": SCHEMA_REPORT}))
        else:
            print(f"verify_outside_world: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"[{mark}] {check['name']}: {check['detail']}")
        print(f"outside world: {report['summary']}")
        print("ok" if report["ok"] else "failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
