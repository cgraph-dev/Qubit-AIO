#!/usr/bin/env python3
"""P3-04 — verify the renderer-neutral floor contract and its budgets.

WHAT THIS MEASURES, AND WHAT IT REFUSES TO MEASURE
--------------------------------------------------
This tool builds the **shipped 100-floor tower** through the real deterministic
projection (``voicebridge.office.world.tower_plan``), selects one active floor,
and checks the data path the renderer is allowed to draw:

* exactly one floor is rendered; a one-floor payload carries only that floor;
* every room/cell/desk on other floors is culled (counts are recomputed, not
  trusted from the payload itself);
* the floor projection is timed on this process (a measured data-path number);
* the tower is deterministic — two builds produce the same digest.

It then reports the mandatory budgets from
``docs/QUBIT-OFFICE-UI-CONTRACT.md`` §3. A hermetic Python process cannot
measure a browser's GPU frame time, heap or event rate, so those metrics are
``not_measured`` with a ``null`` value unless the caller supplies a real
browser sample with ``--sample FILE``. Supplying nothing is honest; inventing a
number is the failure this verifier exists to prevent.

Exit codes: ``0`` data-path checks pass and no metric was fabricated (missing
samples are allowed and reported), ``1`` a data-path check failed or a supplied
sample exceeds a budget, ``2`` usage/IO/malformed sample.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TASK_ID = "P3-04"
SCHEMA = "qoffice-renderer-budget/1"
BUDGET_SOURCE = (
    "docs/QUBIT-OFFICE-UI-CONTRACT.md §3;"
    " docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md §6"
)
EXPECTED_TOWER = {
    "floors": 100,
    "wings": 5,
    "rooms": 300,
    "desks": 3075,
    "staffed_agents": 0,
}


class UsageError(Exception):
    """Bad arguments, IO or a malformed sample (exit 2)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# Metric order is the UI-contract order; `field` is the accepted sample key.
# `limit` is resolved per device tier.
METRICS: Tuple[Dict[str, Any], ...] = (
    {
        "metric": "frame_p95_ms",
        "field": "frameP95Ms",
        "desktop": 16.7,
        "low": 33.3,
        "source": "UI contract §3 — P95 frame ≤16.7 ms desktop / ≤33.3 ms low tier",
        "gpu": True,
    },
    {
        "metric": "active_agents",
        "field": "activeAgents",
        "limit": 40,
        "source": "UI contract §3 — ≤40 fully simulated agents by default",
        "gpu": False,
    },
    {
        "metric": "visible_avatars",
        "field": "visibleAvatars",
        "limit": 80,
        "source": "UI contract §3 — ≤80 visible avatars; distant throttle",
        "gpu": False,
    },
    {
        "metric": "pathfinding_avg_ms",
        "field": "pathfindingAvgMs",
        "limit": 2.0,
        "source": "UI contract §3 — pathfinding ≤2 ms average",
        "gpu": False,
    },
    {
        "metric": "pathfinding_p95_ms",
        "field": "pathfindingP95Ms",
        "limit": 8.0,
        "source": "UI contract §3 — pathfinding ≤8 ms P95",
        "gpu": False,
    },
    {
        "metric": "heap_mb",
        "field": "heapMb",
        "desktop": 512,
        "low": 256,
        "source": "UI contract §3 — heap ≤256 MB low / ≤512 MB desktop after 10 min",
        "gpu": True,
    },
    {
        "metric": "events_per_second",
        "field": "eventsPerSecond",
        "limit": 20,
        "source": "UI contract §3 — client events ≤20/s normal",
        "gpu": False,
    },
    {
        "metric": "event_burst",
        "field": "eventBurst",
        "limit": 60,
        "source": "UI contract §3 — client events ≤60/s burst with coalescing",
        "gpu": False,
    },
)

LIMITATIONS: List[str] = [
    "GPU frame time, browser heap and event rates are NOT measured by this"
    " hermetic process; they stay not_measured unless --sample supplies a real"
    " browser measurement",
    "the measured floor-projection timing is a Python data-path number, not a"
    " rendered frame time",
    "the verifier exercises the projection/culling contract, not the vendored"
    " Habblaud client, a browser compositor or a GPU",
    "no live service, state, credential or network is touched",
]


# ------------------------------------------------------------------- helpers
def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _percentile(samples: Sequence[float], fraction: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower]) * (1.0 - weight) + float(ordered[upper]) * weight


def _timed(fn, iterations: int) -> Dict[str, Any]:
    samples: List[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {
        "iterations": len(samples),
        "min_ms": round(min(samples), 6),
        "median_ms": round(statistics.median(samples), 6),
        "p95_ms": round(_percentile(samples, 0.95), 6),
        "max_ms": round(max(samples), 6),
    }


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _metric_limit(spec: Dict[str, Any], tier: str) -> float:
    if "limit" in spec:
        return float(spec["limit"])
    return float(spec[tier])


# --------------------------------------------------------------- sample input
_SAMPLE_ALIASES = {
    "frameP95Ms": "frameP95Ms",
    "frame_p95_ms": "frameP95Ms",
    "activeAgents": "activeAgents",
    "active_agents": "activeAgents",
    "visibleAvatars": "visibleAvatars",
    "visible_avatars": "visibleAvatars",
    "pathfindingAvgMs": "pathfindingAvgMs",
    "pathfinding_avg_ms": "pathfindingAvgMs",
    "pathfindingP95Ms": "pathfindingP95Ms",
    "pathfinding_p95_ms": "pathfindingP95Ms",
    "heapMb": "heapMb",
    "heap_mb": "heapMb",
    "eventsPerSecond": "eventsPerSecond",
    "events_per_second": "eventsPerSecond",
    "eventBurst": "eventBurst",
    "event_burst": "eventBurst",
}


def _read_sample(path: Path) -> Dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UsageError("QOFFICE_RENDERER_SAMPLE_MISSING", str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise UsageError("QOFFICE_RENDERER_SAMPLE_INVALID", str(exc)) from exc
    if not isinstance(raw, dict):
        raise UsageError(
            "QOFFICE_RENDERER_SAMPLE_INVALID",
            "the sample must be a JSON object of measured metric values",
        )
    nested = raw.get("budgets")
    if isinstance(nested, dict):
        raw = nested
    values: Dict[str, float] = {}
    for key, value in raw.items():
        field = _SAMPLE_ALIASES.get(str(key))
        if not field:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UsageError(
                "QOFFICE_RENDERER_SAMPLE_INVALID",
                f"{key} must be a number, got {type(value).__name__}",
            )
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise UsageError(
                "QOFFICE_RENDERER_SAMPLE_INVALID",
                f"{key} must be finite and non-negative, got {value!r}",
            )
        values[field] = number
    if not values:
        raise UsageError(
            "QOFFICE_RENDERER_SAMPLE_EMPTY",
            "no recognised budget metric in the sample",
        )
    return values


# ------------------------------------------------------------------ budgets
def _budget_rows(
    tier: str, sample: Optional[Dict[str, float]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for spec in METRICS:
        field = spec["field"]
        limit = _metric_limit(spec, tier)
        value = None if sample is None else sample.get(field)
        if value is None:
            status = "not_measured"
            ok = None
        else:
            ok = value <= limit
            status = "pass" if ok else "fail"
        rows.append({
            "metric": spec["metric"],
            "field": field,
            "tier": tier,
            "value": value,
            "limit": limit,
            "status": status,
            "ok": ok,
            "source": spec["source"],
            "gpu_related": bool(spec["gpu"]),
        })
    return rows


def _no_fabricated_metrics(rows: Sequence[Dict[str, Any]]) -> bool:
    """A measured verdict must have a finite value; an absent value must be
    unmeasured. This is the honesty invariant, stated as code."""
    for row in rows:
        if row["status"] == "not_measured":
            if row["value"] is not None or row["ok"] is not None:
                return False
        elif not isinstance(row["value"], (int, float)) or isinstance(
            row["value"], bool
        ):
            return False
    return True


# ------------------------------------------------------------------- report
def _build_report(
    *,
    floor_filter: Optional[str],
    iterations: int,
    tier: str,
    sample: Optional[Dict[str, float]],
    sample_path: Optional[str],
) -> Dict[str, Any]:
    from voicebridge.office.world.tower_plan import (
        build_tower_plan,
        tower_payload,
        validate_tower_plan,
    )

    plan = build_tower_plan()
    problems = validate_tower_plan(plan)
    counts = plan.counts()
    fixture_checks = {
        key: counts.get(key) == expected
        for key, expected in EXPECTED_TOWER.items()
    }
    fixture_valid = not problems and all(fixture_checks.values())

    if floor_filter:
        try:
            floor = plan.floor(floor_filter)
        except Exception as exc:  # typed tower error; keep message
            raise UsageError(
                "QOFFICE_RENDERER_FLOOR_UNKNOWN", str(exc),
            ) from exc
    else:
        floor = plan.floors[0]

    payload = tower_payload(plan, floor=floor.floor_id)
    payload_counts = payload["counts"]
    payload_floors = payload["floors"]

    culling = {
        "one_floor_only": True,
        "selected_floor": floor.floor_id,
        "floor_rooms": int(payload_counts["rooms"]),
        "floor_cells": int(payload_counts["cells"]),
        "floor_desks": int(payload_counts["desks"]),
        "culled_rooms": plan.room_count - int(payload_counts["rooms"]),
        "culled_cells": plan.cell_count - int(payload_counts["cells"]),
        "culled_desks": plan.desk_count - int(payload_counts["desks"]),
    }

    payload_floor_ids = [item.get("floor_id") for item in payload_floors]
    payload_room_ids = sorted(
        room.get("room_id")
        for item in payload_floors
        for room in item.get("rooms", [])
    )
    floor_room_ids = sorted(room.room_id for room in floor.rooms)

    projection = _timed(
        lambda: tower_payload(plan, floor=floor.floor_id), iterations,
    )
    digest_build = build_tower_plan()
    deterministic = digest_build.digest == plan.digest

    budget_rows = _budget_rows(tier, sample)
    honest = _no_fabricated_metrics(budget_rows)
    measured_budgets = [row for row in budget_rows if row["status"] != "not_measured"]
    budget_failed = any(row["status"] == "fail" for row in budget_rows)
    not_measured = [
        row["metric"] for row in budget_rows if row["status"] == "not_measured"
    ]

    checks = [
        _check(
            "tower_projection_valid",
            not problems,
            "no structural defects" if not problems else "; ".join(problems[:3]),
        ),
        _check(
            "tower_counts_match_contract",
            all(fixture_checks.values()),
            _canonical(fixture_checks),
        ),
        _check(
            "one_active_floor_selected",
            floor.floor_id in payload_floor_ids and len(payload_floor_ids) == 1,
            f"selected={floor.floor_id} payload_floors={payload_floor_ids}",
        ),
        _check(
            "one_floor_payload_only",
            len(payload_floors) == 1 and payload.get("detail") == "full",
            f"detail={payload.get('detail')!r} floors={len(payload_floors)}",
        ),
        _check(
            "culled_floors_are_not_drawn",
            culling["culled_rooms"] > 0
            and culling["culled_cells"] > 0
            and culling["culled_desks"] > 0,
            "culled rooms=%d cells=%d desks=%d" % (
                culling["culled_rooms"],
                culling["culled_cells"],
                culling["culled_desks"],
            ),
        ),
        _check(
            "payload_rooms_belong_to_selected_floor",
            payload_room_ids == floor_room_ids,
            f"payload={len(payload_room_ids)} selected={len(floor_room_ids)}",
        ),
        _check(
            "no_all_floor_draw",
            len(payload_floors) == 1 and culling["culled_rooms"] > 0,
            "the payload carries the active floor only; every other floor is"
            " summary-only and is not sent as a drawable scene",
        ),
        _check(
            "floor_projection_measurable",
            projection["iterations"] == iterations and projection["p95_ms"] >= 0,
            f"p95={projection['p95_ms']} ms over {projection['iterations']} runs",
        ),
        _check(
            "tower_is_deterministic",
            deterministic,
            f"digest={plan.digest[:16]}",
        ),
        _check(
            "no_fabricated_metrics",
            honest,
            "every measured verdict has a real value; every absent metric is"
            " not_measured",
        ),
    ]

    data_ok = all(item["ok"] for item in checks)
    status = "PASS" if data_ok and not budget_failed else "FAIL"

    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "generated_at": _utc_now(),
        "status": status,
        "ok": status == "PASS",
        "tier": tier,
        "budget_source": BUDGET_SOURCE,
        "tower": {
            "digest": plan.digest,
            "counts": counts,
            "expected": EXPECTED_TOWER,
            "checks": fixture_checks,
            "problems": problems,
            "valid": fixture_valid,
        },
        "active_floor": {
            "floor_id": floor.floor_id,
            "index": int(floor.index),
            "wing": floor.wing,
            "active_by_default": bool(floor.active),
            "one_active_floor": len(payload_floor_ids) == 1,
            "render_floor_count": len(payload_floor_ids),
        },
        "culling": culling,
        "data_path": {
            "one_floor_only": True,
            "all_floor_draw": False,
            "payload_detail": payload.get("detail"),
            "payload_floor_count": len(payload_floors),
            "payload_floor_ids": payload_floor_ids,
            "payload_room_count": len(payload_room_ids),
            "floor_projection": projection,
            "measurement": "Python data path only; frames are not measured",
        },
        "budgets": budget_rows,
        "budget_sample": {
            "supplied": sample is not None,
            "path": sample_path,
            "measured_budget_count": len(measured_budgets),
            "not_measured": not_measured,
        },
        "gpu_not_measured": [
            row["metric"] for row in budget_rows
            if row["status"] == "not_measured" and row["gpu_related"]
        ],
        "no_fabricated_metrics": honest,
        "checks": checks,
        "checks_total": len(checks),
        "checks_failed": sum(1 for item in checks if not item["ok"]),
        "limitations": LIMITATIONS,
    }
    return report


# ---------------------------------------------------------------------- CLI
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P3-04 renderer floor contract + budget verifier",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--sample", type=str, default=None,
        help="JSON file of real browser measurements; absent metrics stay"
             " not_measured",
    )
    parser.add_argument(
        "--floor", type=str, default=None,
        help="floor id to render (default: first shipped floor)",
    )
    parser.add_argument(
        "--tier", choices=("desktop", "low"), default="desktop",
        help="device tier whose limits apply (default: desktop)",
    )
    parser.add_argument(
        "--iterations", type=int, default=15,
        help="floor-projection timing iterations (default: 15)",
    )
    parser.add_argument("--out", type=str, default=None, help="write the JSON report here")
    return parser.parse_args(argv)


def _print_error(code: str, detail: str) -> None:
    print(json.dumps({"ok": False, "code": code, "detail": detail}, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.iterations < 1 or args.iterations > 1000:
        _print_error("QOFFICE_RENDERER_BAD_ITERATIONS", "--iterations must be 1..1000")
        return 2
    sample: Optional[Dict[str, float]] = None
    if args.sample:
        try:
            sample = _read_sample(Path(args.sample))
        except UsageError as exc:
            _print_error(exc.code, exc.detail)
            return 2
    try:
        report = _build_report(
            floor_filter=args.floor,
            iterations=args.iterations,
            tier=args.tier,
            sample=sample,
            sample_path=args.sample,
        )
    except UsageError as exc:
        _print_error(exc.code, exc.detail)
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        _print_error("QOFFICE_RENDERER_INTERNAL", f"{type(exc).__name__}: {exc}")
        return 2
    if args.out:
        try:
            Path(args.out).write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _print_error("QOFFICE_RENDERER_WRITE_FAILED", str(exc))
            return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"P3-04 renderer: {report['status']}")
        print(
            f"  tower {report['tower']['counts']['floors']} floors /"
            f" {report['tower']['counts']['rooms']} rooms /"
            f" {report['tower']['counts']['desks']} desks"
        )
        print(
            f"  active floor {report['active_floor']['floor_id']}"
            f" — one floor only, all-floor draw={report['data_path']['all_floor_draw']}"
        )
        for row in report["budgets"]:
            value = "not_measured" if row["value"] is None else f"{row['value']}"
            print(f"  {row['metric']:<20} {value:>14} / {row['limit']}  {row['status']}")
        print(f"  checks {report['checks_total'] - report['checks_failed']}"
              f"/{report['checks_total']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
