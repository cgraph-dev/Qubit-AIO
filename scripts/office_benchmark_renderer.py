#!/usr/bin/env python3
"""P0-08 — honest renderer benchmark over the deterministic office data path.

The benchmark does **not** claim GPU frame times. A hermetic Python process
cannot measure a browser's WebGL/Canvas cost, so this tool measures the part it
genuinely owns and can compare between candidate renderers:

* the full 100-floor tower build (``build_tower_plan``);
* one-floor projection and culling (``tower_payload(plan, floor=...)``);
* the floor's path/portal graph (``assemble_campus``);
* a deterministic shortest-path query on that graph;
* the derived atlas *plan* (sheet metadata derived from the projection, not
  decoded art bytes).

Both candidates — ``phaser`` (the vendored Habblaud renderer path) and
``pixi`` (a candidate) — consume the **same** projection fixture and therefore
report identical data-path metrics. ``gpu_measured`` is ``false`` and
``frame_metrics``/``heap_metrics`` are ``null`` for both; inventing those
numbers would be the failure this harness exists to prevent.

Selection is pinned, not benchmarked: the vendored Habblaud renderer is the
current product and owner ruling 16 permits only additive changes to it, so
``phaser`` remains selected with ``selection_basis="existing_renderer_pinned"``.
A Pixi migration needs a real browser measurement and an owner decision.

Exit codes: ``0`` fixture valid + budgets pass + limitation recorded,
``1`` fixture or budget failure, ``2`` usage/IO.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

TASK_ID = "P0-08"
SCHEMA = "qoffice-renderer-benchmark/1"
FIXTURE_NAME = "tower-template-fixture"
DEFAULT_ITERATIONS = 5
DEFAULT_FLOOR_BUDGET_MS = 33.3
PATH_BUDGET_MS = 8.0
TOWER_BUILD_BUDGET_MS = 1000.0
ATLAS_PLAN_BUDGET_BYTES = 65536
EXPECTED_COUNTS = {
    "floors": 100,
    "wings": 5,
    "rooms": 300,
    "desks": 3075,
    "staffed_agents": 0,
}
LIMITATIONS = [
    "GPU frame time, heap usage and browser compositor cost are NOT measured;"
    " this is a hermetic Python data-path benchmark only",
    "both candidate backends consume the same deterministic projection, so"
    " their reported data-path metrics are identical by construction",
    "atlas size is the size of the derived sheet *plan* (metadata JSON), not"
    " decoded or encoded image bytes",
    "the selected renderer is pinned by product state and owner ruling 16, not"
    " chosen by the numbers in this report",
]


class BenchmarkError(Exception):
    """Usage or I/O refusal (exit 2)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _timed(fn, iterations: int) -> Dict[str, Any]:
    samples: List[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    samples.sort()
    return {
        "samples_ms": [round(value, 6) for value in samples],
        "median_ms": round(statistics.median(samples), 6),
        "p95_ms": round(_percentile(samples, 0.95), 6),
        "min_ms": round(samples[0], 6),
        "max_ms": round(samples[-1], 6),
        "iterations": iterations,
    }


def _percentile(sorted_samples: Sequence[float], fraction: float) -> float:
    """Linear-interpolation percentile over an already sorted sample list."""
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return float(sorted_samples[0])
    position = (len(sorted_samples) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_samples) - 1)
    weight = position - lower
    return float(sorted_samples[lower]) * (1.0 - weight) + float(
        sorted_samples[upper]
    ) * weight


def _atlas_plan(floor) -> Dict[str, Any]:
    """Derive the sheet *plan* for one floor. No art is opened or decoded."""
    sheets = set()
    for room in floor.rooms:
        sheets.add(f"floor:{room.kind}:16x16")
        sheets.add(f"walls:{room.archetype}:16x16")
        sheets.add(f"furniture:{room.archetype}:16x16")
    if floor.rooms:
        sheets.add("characters:idle:16x16")
    ordered = sorted(sheets)
    plan = {
        "tile_px": 16,
        "source": "derived from one-floor room kinds/archetypes; metadata only",
        "sheets": [{"sheet_id": sheet, "layer": sheet.split(":", 1)[0]} for sheet in ordered],
    }
    return {
        "sheet_count": len(ordered),
        "sheets": ordered,
        "manifest_bytes": len(_canonical(plan).encode("utf-8")),
        "manifest_sha256": _digest(plan),
    }


def _one_floor_campus(floor):
    from voicebridge.office.world.model import (
        Business,
        Wing,
        assemble_campus,
        build_room,
    )

    business_id = "biz_fixture_department"
    rooms = [
        build_room(
            room.room_id,
            room.name,
            floor.wing,
            room.desk_count,
            business_id=business_id,
            archetype=room.archetype,
            kind=room.kind,
        )
        for room in floor.rooms
    ]
    wing = Wing(
        wing_id=floor.wing,
        name=f"{floor.wing.title()} Wing",
        index=0,
        legacy_floor_id=0,
        rooms=tuple(rooms),
    )
    business = Business(
        business_id=business_id,
        name="Synthetic Department",
        wing_id=floor.wing,
    )
    return assemble_campus(
        "campus_fixture_department",
        "Synthetic Department Campus",
        [wing],
        [business],
    )


def _build_report(*, floor_filter: Optional[str], iterations: int, floor_budget_ms: float) -> Dict[str, Any]:
    from voicebridge.office.world.tower_plan import (
        build_tower_plan,
        tower_payload,
        validate_tower_plan,
    )

    plan = build_tower_plan()
    problems = validate_tower_plan(plan)
    tower_counts = plan.counts()
    fixture_checks = {
        key: tower_counts.get(key) == expected
        for key, expected in EXPECTED_COUNTS.items()
    }
    fixture_valid = not problems and all(fixture_checks.values())

    if floor_filter:
        floor = plan.floor(floor_filter)
    else:
        floor = plan.floors[0]

    floor_payload = tower_payload(plan, floor=floor.floor_id)
    floor_counts = floor_payload["counts"]

    culling = {
        "one_floor_only": True,
        "selected_floor": floor.floor_id,
        "rooms": floor_counts["rooms"],
        "cells": floor_counts["cells"],
        "desks": floor_counts["desks"],
        "culled_rooms": plan.room_count - floor_counts["rooms"],
        "culled_cells": plan.cell_count - floor_counts["cells"],
        "culled_desks": plan.desk_count - floor_counts["desks"],
    }

    campus = _one_floor_campus(floor)
    graph = campus.path_graph
    nodes = sorted(graph.nodes())
    cell_nodes = [node for node in nodes if node.startswith("room:") and "/cell:" in node]
    start, end = (cell_nodes[0], cell_nodes[-1]) if len(cell_nodes) >= 2 else (nodes[0], nodes[-1])
    path = graph.path(start, end)
    path_counts = {
        "path_nodes": len(nodes),
        "portal_count": len(graph.portals),
        "cell_nodes": len(cell_nodes),
        "path_source": start,
        "path_target": end,
        "path_hops": (len(path) - 1) if path else None,
        "path_reachable": path is not None,
    }

    atlas = _atlas_plan(floor)

    timings = {
        "tower_build": _timed(lambda: build_tower_plan(), iterations),
        "floor_projection": _timed(
            lambda: tower_payload(plan, floor=floor.floor_id), iterations
        ),
        "campus_assembly": _timed(lambda: _one_floor_campus(floor), iterations),
        "path_query": _timed(lambda: graph.path(start, end), iterations),
    }

    data_path = {
        "one_floor_only": True,
        "selected_floor": floor.floor_id,
        "floor_rooms": culling["rooms"],
        "floor_cells": culling["cells"],
        "floor_desks": culling["desks"],
        "culled_rooms": culling["culled_rooms"],
        "culled_cells": culling["culled_cells"],
        "culled_desks": culling["culled_desks"],
        **path_counts,
        "atlas_sheet_count": atlas["sheet_count"],
        "atlas_plan_bytes": atlas["manifest_bytes"],
        "atlas_plan_sha256": atlas["manifest_sha256"],
    }

    basis = (
        "same deterministic one-floor projection fixture for both candidates;"
        " GPU frame/heap cost is not measured in this hermetic process"
    )
    backends = {
        "phaser": {
            "renderer": "phaser",
            "candidate": "vendored Habblaud renderer path (current product)",
            "shared_projection": True,
            "gpu_measured": False,
            "frame_metrics": None,
            "heap_metrics": None,
            "basis": basis,
            "data_path": data_path,
        },
        "pixi": {
            "renderer": "pixi",
            "candidate": "alternative ECS/render scheduler (candidate only)",
            "shared_projection": True,
            "gpu_measured": False,
            "frame_metrics": None,
            "heap_metrics": None,
            "basis": basis,
            "data_path": data_path,
        },
    }

    budgets = [
        {
            "name": "floor_projection_p95_ms",
            "measured": timings["floor_projection"]["p95_ms"],
            "limit": floor_budget_ms,
            "passed": timings["floor_projection"]["p95_ms"] <= floor_budget_ms,
            "source": "docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md §6 low-end frame budget",
        },
        {
            "name": "pathfinding_p95_ms",
            "measured": timings["path_query"]["p95_ms"],
            "limit": PATH_BUDGET_MS,
            "passed": timings["path_query"]["p95_ms"] <= PATH_BUDGET_MS,
            "source": "docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md §6 pathfinding budget",
        },
        {
            "name": "tower_build_p95_ms",
            "measured": timings["tower_build"]["p95_ms"],
            "limit": TOWER_BUILD_BUDGET_MS,
            "passed": timings["tower_build"]["p95_ms"] <= TOWER_BUILD_BUDGET_MS,
            "source": "harness budget (staging-only projection); not a product SLA",
        },
        {
            "name": "atlas_plan_manifest_bytes",
            "measured": atlas["manifest_bytes"],
            "limit": ATLAS_PLAN_BUDGET_BYTES,
            "passed": atlas["manifest_bytes"] <= ATLAS_PLAN_BUDGET_BYTES,
            "source": "derived plan-size bound; not an image-byte budget",
        },
    ]
    budgets_pass = all(item["passed"] for item in budgets)
    status = "PASS" if (fixture_valid and budgets_pass) else "FAIL"

    deterministic = {
        "schema": SCHEMA,
        "fixture": {
            "name": FIXTURE_NAME,
            "tower_digest": plan.digest,
            "tower_counts": tower_counts,
            "fixture_checks": fixture_checks,
        },
        "selection": "phaser",
        "culling": culling,
        "path": path_counts,
        "atlas": {
            "sheet_count": atlas["sheet_count"],
            "sheets": atlas["sheets"],
            "manifest_bytes": atlas["manifest_bytes"],
            "manifest_sha256": atlas["manifest_sha256"],
        },
        "budgets": [
            {key: item[key] for key in ("name", "limit", "passed")}
            for item in budgets
        ],
        "limitations": LIMITATIONS,
    }

    return {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "generated_at": _utc_now(),
        "status": status,
        "fixture": {
            "name": FIXTURE_NAME,
            "tower_digest": plan.digest,
            "tower_counts": tower_counts,
            "selected_floor": floor.floor_id,
            "problems": problems,
            "checks": fixture_checks,
            "valid": fixture_valid,
        },
        "selection": {
            "renderer": "phaser",
            "selection_basis": "existing_renderer_pinned",
            "ruling": "docs/DECISIONS.md ruling 16",
            "reason": (
                "the vendored Habblaud renderer is the current product; owner"
                " ruling 16 permits additive changes only, so a Pixi migration"
                " needs a real browser benchmark and an owner decision"
            ),
            "candidates": ["phaser", "pixi"],
        },
        "backends": backends,
        "culling": culling,
        "timings": timings,
        "budgets": budgets,
        "deterministic_digest": _digest(deterministic),
        "limitations": LIMITATIONS,
    }


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "P0-08 honest renderer data-path benchmark over the shipped tower"
            " projection (no GPU frame metrics are fabricated)."
        )
    )
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    parser.add_argument("--out", default=None, help="also write the JSON report to FILE")
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help="timed samples per metric (default: 5)",
    )
    parser.add_argument(
        "--budget-ms",
        type=float,
        default=DEFAULT_FLOOR_BUDGET_MS,
        help=f"one-floor projection p95 budget in ms (default: {DEFAULT_FLOOR_BUDGET_MS})",
    )
    parser.add_argument(
        "--floor", default=None, help="floor id to project (default: first floor)"
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.iterations < 1:
        _print_error("QOFFICE_BENCH_BAD_ITERATIONS", "--iterations must be >= 1")
        return 2
    if args.budget_ms < 0:
        _print_error("QOFFICE_BENCH_BAD_BUDGET", "--budget-ms must be >= 0")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        report = _build_report(
            floor_filter=args.floor,
            iterations=args.iterations,
            floor_budget_ms=float(args.budget_ms),
        )
    except Exception as exc:  # noqa: BLE001 - typed at the CLI boundary
        _print_error("QOFFICE_BENCH_ERROR", str(exc))
        return 2

    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        try:
            out_path = Path(args.out).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            _print_error("QOFFICE_BENCH_IO", f"cannot write {args.out}: {exc}")
            return 2
        report["out"] = str(Path(args.out).expanduser().resolve())
    if args.json:
        sys.stdout.write(payload + "\n")
    else:
        sys.stdout.write(
            f"{report['status']} {TASK_ID} floor={report['fixture']['selected_floor']} "
            f"rooms={report['culling']['rooms']} desks={report['culling']['desks']} "
            f"culled_rooms={report['culling']['culled_rooms']} "
            f"selected={report['selection']['renderer']} "
            f"gpu_measured=false\n"
        )
    return 0 if report["status"] == "PASS" else 1


def _print_error(code: str, detail: str) -> None:
    sys.stderr.write(f"{code}: {detail}\n")


if __name__ == "__main__":
    raise SystemExit(main())
