#!/usr/bin/env python3
"""P8-01 — verify the 100-floor / 1,000-agent scale fixture and its budgets.

WHAT THIS MEASURES, AND WHAT IT REFUSES TO MEASURE
--------------------------------------------------
This tool builds the deterministic scale fixture
(:mod:`voicebridge.business.scale_fixture`): 100 floors, 5 wings, 300 rooms,
575 cells, 3,075 desks and 1,000 synthetic projection agents. It verifies:

* the counts and the shipped tower projection's structural validity;
* the fixture's digest is deterministic;
* every agent is a tenant/business-scoped projection record, not an
  operational agent;
* the canonical active/dormant tiering, the active-agent cap (≤40), the
  visible-avatar cap (≤80) and dormant selection;
* hierarchical pathfinding (campus → wing → floor → room → cell) is measured
  in this process and compared with the ≤2 ms average / ≤8 ms P95 budget;
* a deterministic client-event budget model with coalescing (≤20/s normal,
  ≤60/s burst);
* the desktop/low device tier limits.

A hermetic Python process cannot measure a browser's GPU frame time, browser
heap, decoded texture memory or bundle transfer. Those metrics are
``not_measured`` with a ``null`` value unless the caller supplies a real
browser sample via ``--sample FILE``. The event rate is a budget *model*, not
a measurement, and is labelled ``kind: "budget_model"``. Inventing a number is
the failure this verifier exists to prevent.

Exit codes: ``0`` every data-path/budget check passes and no metric was
fabricated, ``1`` a check failed or a supplied sample exceeds a budget, ``2``
usage/IO/malformed sample.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TASK_ID = "P8-01"
SCHEMA = "qoffice-scale/1"
BUDGET_SOURCE = (
    "docs/QUBIT-OFFICE-UI-CONTRACT.md §3;"
    " docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md §5/§6;"
    " docs/QUBIT-OFFICE-COMPLETE-PLAN.md §12 (P8-01)"
)
EXPECTED = {
    "floors": 100,
    "wings": 5,
    "rooms": 300,
    "cells": 575,
    "desks": 3075,
    "agents": 1000,
}


class UsageError(Exception):
    """Bad arguments, IO or a malformed sample (exit 2)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# `field` is the accepted sample key. `limit` is either fixed or resolved per
# device tier. Active/visible/pathfinding are always measured from the fixture;
# frame/heap/assets are browser/GPU numbers and only become measured when the
# caller supplies a real sample.
METRICS: Tuple[Dict[str, Any], ...] = (
    {
        "metric": "frame_p95_ms",
        "field": "frameP95Ms",
        "desktop": 16.7,
        "low": 33.3,
        "source": "UI contract §3 — P95 frame ≤16.7 ms desktop / ≤33.3 ms low",
        "sampleable": True,
    },
    {
        "metric": "active_agents",
        "field": None,
        "limit": 40,
        "source": "UI contract §3 — active fully simulated agents ≤40",
        "sampleable": False,
    },
    {
        "metric": "visible_avatars",
        "field": None,
        "limit": 80,
        "source": "UI contract §3 — visible avatars ≤80; distant throttle",
        "sampleable": False,
    },
    {
        "metric": "pathfinding_avg_ms",
        "field": None,
        "limit": 2.0,
        "source": "UI contract §3 — pathfinding ≤2 ms average",
        "sampleable": False,
    },
    {
        "metric": "pathfinding_p95_ms",
        "field": None,
        "limit": 8.0,
        "source": "UI contract §3 — pathfinding ≤8 ms P95",
        "sampleable": False,
    },
    {
        "metric": "heap_mb",
        "field": "heapMb",
        "desktop": 512,
        "low": 256,
        "source": "UI contract §3 — heap ≤256 MB low / ≤512 MB desktop",
        "sampleable": True,
    },
    {
        "metric": "events_per_second",
        "field": "eventsPerSecond",
        "limit": 20,
        "source": "UI contract §3 — client events ≤20/s normal",
        "sampleable": True,
        "modeled": True,
    },
    {
        "metric": "event_burst",
        "field": "eventBurst",
        "limit": 60,
        "source": "UI contract §3 — client events ≤60/s burst with coalescing",
        "sampleable": True,
        "modeled": True,
    },
    {
        "metric": "decoded_texture_mb",
        "field": "decodedTextureMb",
        "desktop": 256,
        "low": 128,
        "source": "Improvements §6 — decoded texture ≤128 MB low / ≤256 MB desktop",
        "sampleable": True,
    },
    {
        "metric": "map_chunk_mb",
        "field": "mapChunkMb",
        "desktop": 128,
        "low": 32,
        "source": "Improvements §6 — map/chunk data ≤32 MB low / ≤128 MB desktop",
        "sampleable": True,
    },
    {
        "metric": "visible_floor_bundle_mb",
        "field": "visibleFloorBundleMb",
        "limit": 8,
        "source": "Improvements §6 — visible-floor bundle ≤8 MB compressed",
        "sampleable": True,
    },
    {
        "metric": "adjacent_prefetch_mb",
        "field": "adjacentPrefetchMb",
        "limit": 12,
        "source": "Improvements §6 — adjacent prefetch ≤12 MB compressed",
        "sampleable": True,
    },
)

LIMITATIONS: List[str] = [
    "GPU frame time, browser heap, decoded texture memory, map/chunk data and"
    " bundle transfer are NOT measured by this hermetic process; they stay"
    " not_measured unless --sample supplies a real browser measurement",
    "client event rates are a deterministic budget model with coalescing, not"
    " an observed browser rate (kind=budget_model, measured=false)",
    "pathfinding timing is measured in this Python process over an"
    " adjacency-indexed hierarchical graph; it is not a browser or worker frame",
    "the 1,000 agents are synthetic seed/projection records, not an"
    " operational workforce: no agent runs, no model is called, no store is used",
    "no live service, state, credential or network is touched",
    "this is a data/device-budget gate, not a real browser or GPU matrix",
]


# ------------------------------------------------------------------- helpers
def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


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
    "heapMb": "heapMb",
    "heap_mb": "heapMb",
    "eventsPerSecond": "eventsPerSecond",
    "events_per_second": "eventsPerSecond",
    "eventBurst": "eventBurst",
    "event_burst": "eventBurst",
    "decodedTextureMb": "decodedTextureMb",
    "decoded_texture_mb": "decodedTextureMb",
    "mapChunkMb": "mapChunkMb",
    "map_chunk_mb": "mapChunkMb",
    "visibleFloorBundleMb": "visibleFloorBundleMb",
    "visible_floor_bundle_mb": "visibleFloorBundleMb",
    "adjacentPrefetchMb": "adjacentPrefetchMb",
    "adjacent_prefetch_mb": "adjacentPrefetchMb",
}


def _read_sample(path: Path) -> Dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UsageError("QOFFICE_SCALE_SAMPLE_MISSING", str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise UsageError("QOFFICE_SCALE_SAMPLE_INVALID", str(exc)) from exc
    if not isinstance(raw, dict):
        raise UsageError(
            "QOFFICE_SCALE_SAMPLE_INVALID",
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
                "QOFFICE_SCALE_SAMPLE_INVALID",
                f"{key} must be a number, got {type(value).__name__}",
            )
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise UsageError(
                "QOFFICE_SCALE_SAMPLE_INVALID",
                f"{key} must be finite and non-negative, got {value!r}",
            )
        values[field] = number
    if not values:
        raise UsageError(
            "QOFFICE_SCALE_SAMPLE_EMPTY",
            "no recognised scale budget metric in the sample",
        )
    return values


# ------------------------------------------------------------------ budgets
def _budget_rows(
    tier: str,
    sample: Optional[Dict[str, float]],
    *,
    active_agents: int,
    visible_avatars: int,
    pathfinding_avg_ms: float,
    pathfinding_p95_ms: float,
    event_normal: int,
    event_burst: int,
) -> List[Dict[str, Any]]:
    measured_values = {
        "active_agents": float(active_agents),
        "visible_avatars": float(visible_avatars),
        "pathfinding_avg_ms": float(pathfinding_avg_ms),
        "pathfinding_p95_ms": float(pathfinding_p95_ms),
    }
    model_values = {
        "events_per_second": float(event_normal),
        "event_burst": float(event_burst),
    }
    rows: List[Dict[str, Any]] = []
    for spec in METRICS:
        metric = spec["metric"]
        field = spec["field"]
        limit = _metric_limit(spec, tier)
        sampled = None if sample is None or field is None else sample.get(field)

        if metric in measured_values:
            value: Optional[float] = measured_values[metric]
            kind = "measured"
            measured = True
            note = "measured from the deterministic fixture"
        elif sampled is not None:
            value = float(sampled)
            kind = "measured"
            measured = True
            note = "supplied real browser sample"
        elif metric in model_values:
            value = model_values[metric]
            kind = "budget_model"
            measured = False
            note = "deterministic event budget model, not a browser measurement"
        else:
            value = None
            kind = "not_measured"
            measured = False
            note = "browser/GPU metric not measured by this process"

        if value is None:
            status = "not_measured"
            ok: Optional[bool] = None
        else:
            ok = value <= limit
            status = "pass" if ok else "fail"
        rows.append(
            {
                "metric": metric,
                "field": field,
                "tier": tier,
                "value": value,
                "limit": limit,
                "status": status,
                "ok": ok,
                "kind": kind,
                "measured": bool(measured),
                "note": note,
                "source": spec["source"],
            }
        )
    return rows


def _no_fabricated_metrics(rows: Sequence[Dict[str, Any]]) -> bool:
    """The honesty invariant, stated as code.

    ``not_measured`` must carry no value and no verdict. A measured/model row
    must carry a finite value and agree with its own ``measured`` flag.
    """

    for row in rows:
        status = row.get("status")
        kind = row.get("kind")
        value = row.get("value")
        if status == "not_measured":
            if value is not None or row.get("ok") is not None:
                return False
            if kind != "not_measured" or row.get("measured"):
                return False
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(float(value)) or float(value) < 0:
            return False
        if kind == "measured" and not row.get("measured"):
            return False
        if kind == "budget_model" and row.get("measured"):
            return False
    return True


# ------------------------------------------------------------------- report
def _build_report(
    *,
    tier: str,
    iterations: int,
    sample: Optional[Dict[str, float]],
    sample_path: Optional[str],
) -> Dict[str, Any]:
    from voicebridge.business.scale_fixture import (
        DEFAULT_ACTIVE_AGENT_LIMIT,
        DEFAULT_VISIBLE_AVATAR_LIMIT,
        DEVICE_TIERS,
        SYNTHETIC_TENANT,
        build_scale_fixture,
        build_scale_path_graph,
        device_tier_limits,
        model_event_budget,
        sample_pathfinding,
        select_scale_view,
    )
    from voicebridge.office.world import model as world_model
    from voicebridge.office.world.tower_plan import (
        build_tower_plan,
        validate_tower_plan,
    )

    fixture = build_scale_fixture()
    counts = fixture.counts()
    fixture_checks = {
        key: counts.get(key) == expected for key, expected in EXPECTED.items()
    }

    plan = build_tower_plan()
    tower_problems = validate_tower_plan(plan)

    rebuilt = build_scale_fixture()
    deterministic = rebuilt.digest == fixture.digest

    seat_ids = {seat.seat_id for seat in fixture.seats}
    room_ids = {room.room_id for room in fixture.rooms}
    business_ids = {business.business_id for business in fixture.businesses}
    agent_ids = {agent.agent_id for agent in fixture.agents}
    agents_are_projection = all(agent.is_projection for agent in fixture.agents)
    tenant_ok = all(agent.tenant_id == SYNTHETIC_TENANT for agent in fixture.agents)
    business_ok = all(agent.business_id in business_ids for agent in fixture.agents)
    seats_ok = all(agent.seat_id in seat_ids for agent in fixture.agents)
    rooms_ok = all(agent.room_id in room_ids for agent in fixture.agents)
    unique_ids = len(agent_ids) == len(fixture.agents)
    unique_seats = len({agent.seat_id for agent in fixture.agents}) == len(
        fixture.agents
    )

    view = select_scale_view(fixture)
    decision_by_room = {d.room_id: d for d in view.decisions}
    camera_rooms = set(view.camera_room_ids)
    simulated_signals = (
        {view.interaction_room_id}
        | set(view.urgent_room_ids)
        | set(view.deadline_room_ids)
        | set(view.approval_room_ids)
        | set(view.notification_room_ids)
    )
    expected_active_rooms = camera_rooms | set(view.urgent_room_ids) | set(
        view.deadline_room_ids
    ) | set(view.approval_room_ids) | set(view.notification_room_ids)
    tiering_ok = all(
        decision_by_room[room].tier >= world_model.Tier.PROXY
        for room in camera_rooms
        if room not in simulated_signals
    ) and all(
        decision_by_room[room].state == world_model.RoomState.SIMULATION_ACTIVE
        for room in simulated_signals
    ) and set(view.active_room_ids) == expected_active_rooms
    active_rooms_ok = len(view.active_room_ids) <= DEFAULT_ACTIVE_AGENT_LIMIT
    dormant_ok = bool(view.dormant_room_ids) and bool(view.dormant_agents)

    graph = build_scale_path_graph(fixture)
    graph_nodes = graph.nodes()
    graph_ok = (
        graph.portal_count > 0
        and len(graph_nodes) == graph.portal_count + 1
        and all(
            any(node.startswith(prefix) for node in graph_nodes)
            for prefix in ("campus:", "wing:", "floor:", "room:")
        )
    )
    pathfinding = sample_pathfinding(fixture, graph, iterations=iterations)
    pathfinding_avg_ok = pathfinding.avg_ms <= 2.0
    pathfinding_p95_ok = pathfinding.p95_ms <= 8.0

    events = model_event_budget(
        len(view.simulated_agents), len(view.visible_avatars)
    )
    event_normal_ok = events.normal_events <= events.normal_cap
    event_burst_ok = events.burst_events <= events.burst_cap
    coalescing_ok = events.burst_raw >= events.burst_events and events.coalesced >= 0

    device_limits = device_tier_limits(tier)
    device_ok = (
        set(DEVICE_TIERS) == {"desktop", "low"}
        and all(key in DEVICE_TIERS["desktop"] for key in device_limits)
        and DEVICE_TIERS["low"]["frame_p95_ms"] > DEVICE_TIERS["desktop"]["frame_p95_ms"]
        and DEVICE_TIERS["low"]["heap_mb"] < DEVICE_TIERS["desktop"]["heap_mb"]
        and DEVICE_TIERS["desktop"]["active_agents"] == DEFAULT_ACTIVE_AGENT_LIMIT
        and DEVICE_TIERS["desktop"]["visible_avatars"] == DEFAULT_VISIBLE_AVATAR_LIMIT
    )

    budget_rows = _budget_rows(
        tier,
        sample,
        active_agents=len(view.simulated_agents),
        visible_avatars=len(view.visible_avatars),
        pathfinding_avg_ms=pathfinding.avg_ms,
        pathfinding_p95_ms=pathfinding.p95_ms,
        event_normal=events.normal_events,
        event_burst=events.burst_events,
    )
    honest = _no_fabricated_metrics(budget_rows)
    budget_failed = any(row["status"] == "fail" for row in budget_rows)
    measured_rows = [row for row in budget_rows if row["status"] != "not_measured"]
    not_measured = [
        row["metric"] for row in budget_rows if row["status"] == "not_measured"
    ]
    gpu_not_measured = [
        row["metric"]
        for row in budget_rows
        if row["status"] == "not_measured" and row["field"] is not None
    ]

    checks = [
        _check(
            "tower_projection_valid",
            not tower_problems,
            "no structural defects"
            if not tower_problems
            else "; ".join(tower_problems[:3]),
        ),
        _check(
            "fixture_counts_match_contract",
            all(fixture_checks.values()),
            _canonical(fixture_checks),
        ),
        _check(
            "fixture_is_deterministic",
            deterministic,
            f"digest={fixture.digest[:16]}",
        ),
        _check(
            "agents_are_projection_records",
            agents_are_projection,
            "status=projection, source=seed_projection, operational=false",
        ),
        _check(
            "tenant_isolation_and_synthetic_scope",
            tenant_ok and business_ok,
            f"tenant={SYNTHETIC_TENANT} agents={len(fixture.agents)}"
            f" businesses={len(fixture.businesses)}",
        ),
        _check(
            "agent_seats_are_real",
            seats_ok and rooms_ok and unique_ids and unique_seats,
            f"seats={len(seat_ids)} agents={len(fixture.agents)}"
            f" unique_ids={unique_ids} unique_seats={unique_seats}",
        ),
        _check(
            "active_set_uses_canonical_policy",
            tiering_ok,
            f"camera={len(camera_rooms)} simulation={len(simulated_signals)}"
            f" active={len(view.active_room_ids)}",
        ),
        _check(
            "active_agents_within_budget",
            len(view.simulated_agents) <= DEFAULT_ACTIVE_AGENT_LIMIT,
            f"active={len(view.simulated_agents)}"
            f" candidates={view.simulated_candidate_count}"
            f" limit={DEFAULT_ACTIVE_AGENT_LIMIT}",
        ),
        _check(
            "visible_avatars_within_budget",
            len(view.visible_avatars) <= DEFAULT_VISIBLE_AVATAR_LIMIT,
            f"visible={len(view.visible_avatars)}"
            f" candidates={view.visible_candidate_count}"
            f" limit={DEFAULT_VISIBLE_AVATAR_LIMIT}",
        ),
        _check(
            "active_rooms_within_budget",
            active_rooms_ok,
            f"active_rooms={len(view.active_room_ids)}"
            f" limit={DEFAULT_ACTIVE_AGENT_LIMIT}",
        ),
        _check(
            "dormant_rooms_and_agents_selected",
            dormant_ok,
            f"dormant_rooms={len(view.dormant_room_ids)}"
            f" dormant_agents={len(view.dormant_agents)}",
        ),
        _check(
            "hierarchical_path_graph_built",
            graph_ok,
            f"nodes={len(graph_nodes)} portals={graph.portal_count}"
            " hierarchy=campus>wing>floor>room>cell",
        ),
        _check(
            "pathfinding_avg_within_budget",
            pathfinding_avg_ok,
            f"avg={pathfinding.avg_ms:.4f} ms limit=2.0 ms"
            f" queries={pathfinding.queries}",
        ),
        _check(
            "pathfinding_p95_within_budget",
            pathfinding_p95_ok,
            f"p95={pathfinding.p95_ms:.4f} ms limit=8.0 ms",
        ),
        _check(
            "event_normal_within_budget",
            event_normal_ok,
            f"normal={events.normal_events}/s cap={events.normal_cap}/s"
            " (budget model)",
        ),
        _check(
            "event_burst_within_budget",
            event_burst_ok,
            f"burst={events.burst_events}/s cap={events.burst_cap}/s"
            f" raw={events.burst_raw} coalesced={events.coalesced}"
            " (budget model)",
        ),
        _check(
            "event_model_coalesces",
            coalescing_ok,
            f"raw={events.burst_raw} reported={events.burst_events}"
            f" suppressed={events.coalesced}",
        ),
        _check(
            "device_tiers_defined",
            device_ok,
            f"tier={tier} desktop+low present; low frame/heap stricter",
        ),
        _check(
            "no_fabricated_metrics",
            honest,
            "measured rows carry real values; browser/GPU rows without a sample"
            " are not_measured; event rows are labelled budget_model",
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
        "fixture": {
            "schema": "qubit-office-scale-fixture/1",
            "tenant_id": fixture.tenant_id,
            "seed": int(fixture.seed),
            "digest": fixture.digest,
            "counts": counts,
            "expected": dict(EXPECTED),
            "checks": fixture_checks,
            "synthetic": True,
            "projection": True,
            "operational": False,
            "businesses": len(fixture.businesses),
            "departments": len({b.department_id for b in fixture.businesses}),
        },
        "tenant": {
            "tenant_id": SYNTHETIC_TENANT,
            "agents": len(fixture.agents),
            "businesses": len(fixture.businesses),
            "isolated": tenant_ok and business_ok,
            "seed_is_production": False,
        },
        "active_set": {
            **view.to_dict(),
            "policy": "voicebridge.office.world.active.select_active_set",
            "default_active_agent_limit": DEFAULT_ACTIVE_AGENT_LIMIT,
            "default_visible_avatar_limit": DEFAULT_VISIBLE_AVATAR_LIMIT,
        },
        "pathfinding": {
            "graph": graph.to_dict(),
            "report": pathfinding.to_dict(),
            "budget_avg_ms": 2.0,
            "budget_p95_ms": 8.0,
        },
        "events": {
            "model": events.to_dict(),
            "measured": False,
        },
        "device_tiers": {
            name: dict(limits) for name, limits in DEVICE_TIERS.items()
        },
        "budgets": budget_rows,
        "budget_sample": {
            "supplied": sample is not None,
            "path": sample_path,
            "measured_budget_count": len(measured_rows),
            "not_measured": not_measured,
        },
        "gpu_not_measured": gpu_not_measured,
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
        description="P8-01 scale fixture + load/device/frame/heap/event/pathfinding"
        " verifier",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--sample",
        type=str,
        default=None,
        help="JSON file of real browser measurements; absent browser/GPU metrics"
        " stay not_measured",
    )
    parser.add_argument(
        "--tier",
        choices=("desktop", "low"),
        default="desktop",
        help="device tier whose limits apply (default: desktop)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="pathfinding timing iterations (default: 3)",
    )
    parser.add_argument("--out", type=str, default=None, help="write the JSON report here")
    return parser.parse_args(argv)


def _print_error(code: str, detail: str) -> None:
    print(json.dumps({"ok": False, "code": code, "detail": detail}, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.iterations < 1 or args.iterations > 100:
        _print_error("QOFFICE_SCALE_BAD_ITERATIONS", "--iterations must be 1..100")
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
            tier=args.tier,
            iterations=args.iterations,
            sample=sample,
            sample_path=args.sample,
        )
    except UsageError as exc:
        _print_error(exc.code, exc.detail)
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        _print_error("QOFFICE_SCALE_INTERNAL", f"{type(exc).__name__}: {exc}")
        return 2
    if args.out:
        try:
            Path(args.out).write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _print_error("QOFFICE_SCALE_WRITE_FAILED", str(exc))
            return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        counts = report["fixture"]["counts"]
        print(f"P8-01 scale: {report['status']}")
        print(
            f"  fixture {counts['floors']} floors / {counts['wings']} wings /"
            f" {counts['rooms']} rooms / {counts['desks']} desks /"
            f" {counts['agents']} agents"
        )
        print(
            f"  active agents {report['active_set']['active_agents']}"
            f" (limit {report['active_set']['default_active_agent_limit']}),"
            f" visible avatars {report['active_set']['visible_avatars']}"
            f" (limit {report['active_set']['default_visible_avatar_limit']})"
        )
        for row in report["budgets"]:
            value = "not_measured" if row["value"] is None else f"{row['value']}"
            print(f"  {row['metric']:<24} {value:>14} / {row['limit']}"
                  f"  {row['status']} ({row['kind']})")
        print(
            f"  checks {report['checks_total'] - report['checks_failed']}"
            f"/{report['checks_total']}"
        )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
