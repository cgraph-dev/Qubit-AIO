#!/usr/bin/env python3
"""Verify the Qubit Office outside-life / social-graph projection (P7-05).

This is the evidence instrument for P7-05. It builds the real projection over
:mod:`voicebridge.office.world.outside_life` from a deterministic source-event
fixture and checks exactly the acceptance items the plan names:

* source events drive the environment (weather / day-night / seasons);
* an empty source produces an empty projection (no invented activity);
* only active exterior zones receive ambient visitors/vehicles;
* the social graph is folded from interaction events with trust/friendship /
  rivalry classification;
* social memory rows carry scoped ACL, provenance, visibility and retention,
  and a cross-scope viewer is denied;
* replay is deterministic (identical and shuffled inputs agree);
* every collection and text field is bounded;
* the projection is presentation-only and grows no business-truth field.

``--json`` prints one machine-readable report. Nothing is written; the check is
read-only and hermetic (no network, no service, no store, no clock).

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

from voicebridge.office.world import outside_life as OL  # noqa: E402
from voicebridge.office.world.exterior import build_exterior_map  # noqa: E402

SCHEMA_REPORT = "qubit-office-outside-life-verification/1"

# A deterministic fixture. Every event id, time and payload is literal; there is
# no clock, no random value and no live state.
FIXTURE_EVENTS: List[Dict[str, Any]] = [
    {
        "event_id": "evt-env-1",
        "type": "environment",
        "occurred_at": "2026-01-01T08:00:00Z",
        "payload": {"weather": "clear", "day_night": "day", "season": "spring"},
    },
    {
        "event_id": "evt-env-2",
        "type": "environment",
        "occurred_at": "2026-01-01T18:00:00Z",
        "payload": {"weather": "rain", "day_night": "dusk", "season": "autumn"},
    },
    {
        "event_id": "evt-visitor-1",
        "type": "outside.visitor",
        "occurred_at": "2026-01-01T18:05:00Z",
        "zone_id": "zone-campus",
        "payload": {"kind": "visitor", "count": 2, "label": "Courier"},
    },
    {
        "event_id": "evt-visitor-2",
        "type": "outside.vehicle",
        "occurred_at": "2026-01-01T18:06:00Z",
        "zone_id": "zone-parking",
        "payload": {"kind": "vehicle"},
    },
    {
        "event_id": "evt-visitor-3",
        "type": "outside.visitor",
        "occurred_at": "2026-01-01T18:07:00Z",
        "zone_id": "zone-nowhere",
        "payload": {"kind": "visitor"},
    },
    {
        "event_id": "evt-social-1",
        "type": "interaction",
        "occurred_at": "2026-01-01T18:10:00Z",
        "aggregate_id": "office:engineering",
        "payload": {
            "participants": ["agent-b", "agent-a"],
            "department": "engineering",
            "relationship": "friendship",
            "trust_delta": 3,
        },
    },
    {
        "event_id": "evt-social-2",
        "type": "simulation.interaction",
        "occurred_at": "2026-01-01T18:11:00Z",
        "aggregate_id": "office:engineering",
        "payload": {
            "participants": ["agent-b", "agent-c"],
            "department": "engineering",
            "relationship": "collaboration",
            "trust_delta": 1,
        },
    },
    {
        "event_id": "evt-social-3",
        "type": "social.interaction",
        "occurred_at": "2026-01-01T18:12:00Z",
        "aggregate_id": "office:sales",
        "payload": {
            "participants": ["agent-d", "agent-e"],
            "department": "sales",
            "relationship": "rivalry",
            "trust_delta": -4,
        },
    },
    {
        "event_id": "evt-social-4",
        "type": "interaction",
        "occurred_at": "2026-01-01T18:13:00Z",
        "payload": {
            "participants": ["agent-f", "agent-g"],
            "department": "engineering",
            "relationship": "friendship",
            "trust_delta": 2,
            "expires_at": "2020-01-01T00:00:00Z",
        },
    },
]

_NOW = "2026-01-01T19:00:00Z"


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def run_checks() -> Dict[str, Any]:
    """Build the projection, validate it and return the full report."""
    map_obj = build_exterior_map()
    life = OL.build_outside_life(FIXTURE_EVENTS, map_obj=map_obj)
    again = OL.build_outside_life(FIXTURE_EVENTS, map_obj=build_exterior_map())
    shuffled = OL.build_outside_life(
        list(reversed(FIXTURE_EVENTS)), map_obj=build_exterior_map()
    )
    empty = OL.build_outside_life([], map_obj=map_obj)
    expired = OL.build_outside_life(
        FIXTURE_EVENTS, map_obj=map_obj, now=_NOW
    )
    engineering = OL.ScopedSocialACL(
        OL.DEFAULT_TENANT, departments=["engineering"]
    )
    sales = OL.ScopedSocialACL(OL.DEFAULT_TENANT, departments=["sales"])
    scoped = OL.build_outside_life(
        FIXTURE_EVENTS, map_obj=map_obj, acl=engineering
    )
    problems = OL.validate_outside_life(life, map_obj=map_obj)
    payload = OL.outside_life_payload(life)

    environment = life.environment
    active_zone_ids = {z.zone_id for z in map_obj.zones if z.active}
    ambient_ok = all(
        sprite["zone_id"] in active_zone_ids for sprite in life.ambient
    )
    parking_ignored = any(
        item.get("reason") == "inactive_zone"
        and item.get("detail") == "zone-parking"
        for item in life.ignored
    )
    unknown_ignored = any(
        item.get("reason") == "unknown_zone"
        and item.get("detail") == "zone-nowhere"
        for item in life.ignored
    )

    edges = {tuple(edge["participants"]): edge for edge in life.social["edges"]}
    ab = edges.get(("agent-a", "agent-b"))
    de = edges.get(("agent-d", "agent-e"))

    eng_records = [
        record
        for record in scoped.social["records"]
        if record["scope_id"] == "engineering"
    ]
    sales_records = [
        record
        for record in scoped.social["records"]
        if record["scope_id"] == "sales"
    ]
    cross_scope_denied = (
        bool(eng_records)
        and not sales_records
        and scoped.social["denied"] == 1
        and all(
            engineering.can_read(None, OL.SocialMemoryRecord.from_payload(record))
            for record in eng_records
        )
        and not sales.can_read(
            None,
            OL.SocialMemoryRecord.from_payload(
                next(
                    record
                    for record in life.social["records"]
                    if record["scope_id"] == "engineering"
                )
            ),
        )
    )
    strict_denied = False
    try:
        OL.build_outside_life(
            FIXTURE_EVENTS, map_obj=map_obj, acl=engineering, strict_acl=True
        )
    except OL.SocialScopeDenied:
        strict_denied = True

    retention_ok = all(
        record["retention"] in OL.RETENTION_CLASSES
        and record["visibility"] in OL.VISIBILITIES
        and record["provenance"].get("event_ids")
        and record["source_event_ids"]
        for record in life.social["records"]
    )
    expired_dropped = (
        len(expired.social["records"]) == len(life.social["records"]) - 1
        and all(
            record["participants"] != ["agent-f", "agent-g"]
            for record in expired.social["records"]
        )
    )

    bounded = (
        len(life.ambient) <= OL.MAX_AMBIENT
        and len(life.social["records"]) <= OL.MAX_SOCIAL_RECORDS
        and len(life.social["edges"]) <= OL.MAX_SOCIAL_EDGES
        and len(life.social["nodes"]) <= OL.MAX_SOCIAL_NODES
        and all(
            len(str(item.get("label") or "")) <= OL.MAX_TEXT
            for item in list(life.ambient) + list(life.social["records"])
        )
    )
    forbidden = sorted(
        set(_keys(life.to_dict())) & set(OL.FORBIDDEN_TRUTH_KEYS)
    )

    checks: List[Dict[str, Any]] = [
        _check(
            "schema_version",
            life.schema == OL.SCHEMA and life.version == OL.VERSION,
            f"{life.schema} v{life.version}",
        ),
        _check(
            "deterministic_replay",
            life.to_dict() == again.to_dict()
            and life.digest == again.digest
            and life.digest == shuffled.digest,
            life.digest,
        ),
        _check(
            "empty_source_empty_projection",
            empty.summary["observed"] is False
            and empty.environment["observed"] is False
            and empty.environment["weather"] is None
            and empty.environment["day_night"] is None
            and empty.environment["season"] is None
            and empty.ambient == ()
            and empty.social["records"] == []
            and empty.social["edges"] == [],
            "no environment, ambient or social activity",
        ),
        _check(
            "source_event_environment_mapping",
            environment["observed"] is True
            and environment["weather"] == "rain"
            and environment["day_night"] == "dusk"
            and environment["season"] == "autumn"
            and environment["transitions_total"] == 6
            and all(
                transition["cue"] and transition["still_cue"]
                for transition in environment["transitions"]
            ),
            f"weather={environment['weather']}"
            f" day_night={environment['day_night']}"
            f" season={environment['season']}",
        ),
        _check(
            "active_zone_only",
            ambient_ok
            and len(life.ambient) == 2
            and parking_ignored
            and unknown_ignored,
            f"ambient={len(life.ambient)} active_zones="
            f"{sorted(active_zone_ids)}",
        ),
        _check(
            "social_graph_from_events",
            ab is not None
            and ab["classification"] == "friendship"
            and ab["trust"] == 3.0
            and de is not None
            and de["classification"] == "rivalry"
            and de["trust"] == -4.0
            and life.social["summary"]["friendships"] == 3
            and life.social["summary"]["rivalries"] == 1,
            f"records={len(life.social['records'])}"
            f" edges={len(life.social['edges'])}",
        ),
        _check(
            "cross_scope_denied",
            cross_scope_denied and strict_denied,
            f"denied={scoped.social['denied']} strict={strict_denied}",
        ),
        _check(
            "retention_provenance",
            retention_ok and expired_dropped,
            f"records={len(life.social['records'])}"
            f" expired_dropped={expired_dropped}",
        ),
        _check(
            "bounded",
            bounded,
            f"ambient={len(life.ambient)}"
            f" records={len(life.social['records'])}"
            f" edges={len(life.social['edges'])}"
            f" nodes={len(life.social['nodes'])}",
        ),
        _check(
            "presentation_only",
            life.mode == "presentation"
            and life.business_truth is False
            and life.constraints.get("presentation_only") is True
            and life.constraints.get("simulation_only") is True
            and life.constraints.get("source_driven") is True,
            "projection declares presentation-only and simulation-only",
        ),
        _check(
            "no_business_truth",
            not forbidden,
            "no forbidden keys" if not forbidden
            else f"forbidden keys {forbidden}",
        ),
        _check(
            "projection_valid",
            not problems,
            "no problems" if not problems else "; ".join(problems[:6]),
        ),
        _check(
            "payload_ok",
            payload["ok"] is True and payload["digest"] == life.digest,
            f"summary={life.summary}",
        ),
    ]

    failed = sum(1 for check in checks if not check["ok"])
    return {
        "schema": SCHEMA_REPORT,
        "ok": failed == 0,
        "mode": "self-check",
        "projection_schema": OL.SCHEMA,
        "digest": life.digest,
        "map_digest": life.map_digest,
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
        "problems": problems,
        "summary": dict(life.summary),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Qubit Office outside-life projection."
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print one machine-readable JSON report",
    )
    args = parser.parse_args(argv)
    try:
        report = run_checks()
    except Exception as exc:  # noqa: BLE001 - usage/build error, never a pass
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "schema": SCHEMA_REPORT}))
        else:
            print(f"verify_outside_life: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"[{mark}] {check['name']}: {check['detail']}")
        print(f"outside life: {report['summary']}")
        print("ok" if report["ok"] else "failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
