#!/usr/bin/env python3
"""Machine gate for the P6-02 department / room builder.

Builds a complete one-department sample floor, then exercises the staged
builder the plan asks for: draft -> preview -> validate -> approve -> publish,
plus undo, rollback, clone/merge/split, room/cell/desk edits, style and
assignment controls, and the required negative cases (stretched cell,
unreachable desk, missing door, style collision, invalid/paid asset).

    PYTHONPATH=. python scripts/verify_room_builder.py --json

Exit codes:
    0  every check passed
    1  a builder, map, style, access or asset check failed
    2  usage / import / unexpected error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA = "qubit-office-room-builder-validation/1"
TASK_ID = "P6-02"


class _Report:
    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []
        self.problems: List[str] = []

    def check(self, check_id: str, ok: bool, detail: Any = "") -> bool:
        self.checks.append({"id": check_id, "ok": bool(ok), "detail": detail})
        if not ok:
            self.problems.append(f"{check_id}: {detail}")
        return bool(ok)

    @property
    def status(self) -> str:
        return "PASS" if all(row["ok"] for row in self.checks) else "FAIL"


def _asset_report(document: Any) -> Dict[str, Any]:
    hashes = []
    kinds: Dict[str, int] = {}
    paid = 0
    for room in document.rooms:
        for asset in room.assets:
            hashes.append(asset.sha256 or "")
            kinds[asset.kind] = kinds.get(asset.kind, 0) + 1
            paid += 1 if asset.paid else 0
    digest = hashlib.sha256(
        "|".join(sorted(hashes)).encode("utf-8")).hexdigest()
    return {
        "asset_refs": sum(kinds.values()),
        "kinds": dict(sorted(kinds.items())),
        "paid_refs": paid,
        "hash_digest": digest,
    }


def build_report() -> Dict[str, Any]:
    from voicebridge.business import room_builder as RB
    from voicebridge.office.world import map_schema as MS

    report = _Report()
    document = RB.sample_document()

    # -- happy path -----------------------------------------------------
    problems = RB.validate_room_builder_document(document)
    report.check("sample_valid", not problems, problems[:5])
    summary = MS.compute_summary(document)
    counts = {
        "floors": len(document.floors),
        "rooms": len(document.rooms),
        "cells": len(document.cells),
        "desks": len(document.desks),
        "portals": len(document.portals),
    }
    report.check(
        "capacity_matches_desks",
        summary["capacity"] == len(document.desks) == document.capacity,
        {"summary": summary["capacity"], "desks": len(document.desks),
         "document": document.capacity},
    )
    report.check(
        "egress_matches_portals",
        summary["egress"] == len(document.portals) == document.egress,
        {"summary": summary["egress"], "portals": len(document.portals)},
    )

    builder = RB.RoomBuilder(document)
    preview = builder.preview()
    report.check(
        "preview_valid", preview["valid"] and not preview["problems"],
        {"counts": preview["counts"], "problems": preview["problems"][:5]},
    )
    approval = builder.approve(approved_by="owner:verifier")
    report.check(
        "approve_valid", approval["document_digest"] == preview["document_digest"],
        approval,
    )
    first = builder.publish(published_by="owner:verifier")
    report.check(
        "publish_v1", first.version == 1 and builder.status == "published",
        {"version": first.version, "digest": first.digest},
    )

    # -- edit, preview, publish v2 -------------------------------------
    proposal = builder.propose_add_room(
        floor_id="floor-001",
        room_id="extra-1",
        name="Extra Storage",
        kind="storage",
        desks=2,
    )
    report.check(
        "agent_proposal_does_not_apply",
        len(builder.document.rooms) == len(document.rooms),
        {"rooms": len(builder.document.rooms)},
    )
    builder.apply(proposal, applied_by="owner:verifier")
    report.check(
        "apply_adds_room",
        len(builder.document.rooms) == len(document.rooms) + 1,
        {"rooms": len(builder.document.rooms)},
    )
    second = builder.publish(published_by="owner:verifier")
    report.check(
        "publish_v2", second.version == 2, {"version": second.version})

    # -- undo / redo / rollback ----------------------------------------
    builder.undo()
    report.check(
        "undo_restores_draft",
        len(builder.document.rooms) == len(document.rooms),
        {"rooms": len(builder.document.rooms)},
    )
    builder.redo()
    report.check(
        "redo_restores_edit",
        len(builder.document.rooms) == len(document.rooms) + 1,
        {"rooms": len(builder.document.rooms)},
    )
    restored = builder.rollback(1)
    report.check(
        "rollback_to_v1",
        restored.version == 1
        and len(builder.document.rooms) == len(document.rooms)
        and MS.map_digest(builder.document) == first.digest,
        {"version": restored.version,
         "digest": MS.map_digest(builder.document)},
    )

    # -- room / cell / desk editing ------------------------------------
    fresh = RB.RoomBuilder(document)
    fresh.apply(fresh.propose_add_cell("work-1", desks=2))
    work = next(room for room in fresh.document.rooms if room.room_id == "work-1")
    report.check(
        "add_cell_within_contract",
        len(work.cell_ids) == 2
        and all(cell.cols == 16 and cell.rows == 12
                for cell in fresh.document.cells
                if cell.room_id == "work-1"),
        {"cells": len(work.cell_ids)},
    )
    before = work.capacity
    fresh.apply(fresh.propose_add_desk("work-1"))
    work = next(room for room in fresh.document.rooms if room.room_id == "work-1")
    report.check(
        "add_desk_updates_capacity",
        work.capacity == before + 1,
        {"before": before, "after": work.capacity},
    )
    desk_id = work.desk_ids[-1]
    fresh.apply(fresh.propose_remove_desk(desk_id))
    work = next(room for room in fresh.document.rooms if room.room_id == "work-1")
    report.check(
        "remove_desk_updates_capacity",
        work.capacity == before,
        {"capacity": work.capacity},
    )
    cell_id = work.cell_ids[-1]
    fresh.apply(fresh.propose_remove_cell("work-1", cell_id))
    report.check(
        "remove_cell_and_desks",
        all(cell.cell_id != cell_id for cell in fresh.document.cells),
        {"cell_id": cell_id},
    )

    # -- style, palette/light/signage/seed, assignment ------------------
    style = RB.default_style(
        "finance", 4242, department_style="operations", signage="sign-finance")
    fresh.apply(fresh.propose_set_room_style("work-1", style))
    work = next(room for room in fresh.document.rooms if room.room_id == "work-1")
    report.check(
        "style_controls_recorded",
        work.style.get("palette") == style.palette
        and work.style.get("lighting") == style.lighting
        and work.style.get("signage") == style.signage
        and int(work.style.get("seed", -1)) == style.seed,
        {"style": work.style},
    )
    assignment = RB.RoomAssignment(
        business_id="business:sample",
        department_id="operations",
        role_ids=("operations-lead",),
        agent_ids=("agent:demo",),
        capability_manifest_id="capability:work:work-1",
        memory_scope="memory:operations:work-1",
    )
    fresh.apply(fresh.propose_assign_room("work-1", assignment))
    work = next(room for room in fresh.document.rooms if room.room_id == "work-1")
    report.check(
        "assignment_recorded",
        work.memory_scope == assignment.memory_scope
        and work.capability_manifest_id == assignment.capability_manifest_id
        and work.style.get("assignment", {}).get("role_ids")
        == ["operations-lead"],
        {"memory_scope": work.memory_scope,
         "assignment": work.style.get("assignment")},
    )

    # -- clone / merge -------------------------------------------------
    cloner = RB.RoomBuilder(document)
    cloned = cloner.propose_clone_department("floor-001", "floor-002")
    cloner.apply(cloned)
    report.check(
        "clone_department_valid",
        not cloner.validate()
        and len(cloner.document.floors) == 2
        and len(cloner.document.rooms) == 2 * len(document.rooms),
        {"problems": cloner.validate()[:5],
         "floors": len(cloner.document.floors),
         "rooms": len(cloner.document.rooms)},
    )
    merged = cloner.propose_merge_departments(
        ["floor-001", "floor-002"], "floor-002")
    cloner.apply(merged)
    report.check(
        "merge_departments_valid",
        not cloner.validate()
        and len(cloner.document.floors) == 1
        and len(cloner.document.rooms) == 2 * len(document.rooms),
        {"problems": cloner.validate()[:5],
         "floors": len(cloner.document.floors)},
    )

    splitter = RB.RoomBuilder(document, required_zones=())
    split = splitter.propose_split_department(
        "floor-001", "floor-003", ["store-1"])
    splitter.apply(split)
    moved = next(
        room for room in splitter.document.rooms if room.room_id == "store-1")
    report.check(
        "split_department_moves_rooms",
        moved.floor_id == "floor-003"
        and any(floor.floor_id == "floor-003"
                for floor in splitter.document.floors)
        and not splitter.validate(),
        {"floor_id": moved.floor_id, "problems": splitter.validate()[:5]},
    )
    default_problems = RB.validate_room_builder_document(splitter.document)
    report.check(
        "split_floor_reports_missing_required_rooms",
        any("MISSING_REQUIRED_ROOM" in problem for problem in default_problems),
        default_problems[:3],
    )

    # -- negative: stretched cell --------------------------------------
    stretched_cell = replace(document.cells[0], cols=24)
    stretched = replace(
        document, cells=(stretched_cell, *document.cells[1:]))
    stretched_problems = RB.validate_room_builder_document(stretched)
    report.check(
        "stretched_cell_refused",
        any("16x12" in problem for problem in stretched_problems),
        stretched_problems[:3],
    )

    # -- negative: missing door ----------------------------------------
    entry_room = document.rooms[0]
    no_door_rooms = tuple(
        replace(room, portal_ids=())
        if room.room_id == entry_room.room_id else room
        for room in document.rooms
    )
    no_door = replace(document, rooms=no_door_rooms)
    no_door_problems = RB.validate_room_builder_document(no_door)
    report.check(
        "missing_door_refused",
        any("MISSING_DOOR" in problem for problem in no_door_problems),
        no_door_problems[:3],
    )

    # -- negative: unreachable room/desk -------------------------------
    sealed = replace(document, portals=())
    sealed_problems = RB.validate_room_builder_document(sealed)
    report.check(
        "unreachable_room_refused",
        any("unreachable" in problem.lower() for problem in sealed_problems),
        sealed_problems[:3],
    )

    # -- negative: collision -------------------------------------------
    first_cell = document.cells[0]
    overlapping = replace(
        document,
        cells=(
            first_cell,
            replace(first_cell, cell_id="dup-cell", room_id=first_cell.room_id),
            *document.cells[1:],
        ),
        rooms=tuple(
            replace(room, cell_ids=room.cell_ids + ("dup-cell",))
            if room.room_id == first_cell.room_id else room
            for room in document.rooms
        ),
    )
    collision_problems = RB.validate_room_builder_document(overlapping)
    report.check(
        "cell_collision_refused",
        any("CELL_COLLISION" in problem for problem in collision_problems),
        collision_problems[:3],
    )

    # -- negative: invalid / paid asset --------------------------------
    paid_asset = MS.MapAssetRef(
        asset_id="art:paid",
        kind="recipe",
        ref="recipes/local-only",
        sha256="a" * 64,
        paid=True,
    )
    raw_asset = MS.MapAssetRef(
        asset_id="art:raw",
        kind="tileset",
        ref="art/limezu/floor.png",
        sha256="b" * 64,
    )
    paid_rooms = tuple(
        replace(room, assets=(paid_asset,) + tuple(room.assets))
        if room.room_id == entry_room.room_id else room
        for room in document.rooms
    )
    paid = replace(document, rooms=paid_rooms)
    paid_problems = RB.validate_room_builder_document(paid)
    report.check(
        "paid_art_refused",
        any("paid art" in problem.lower() for problem in paid_problems),
        paid_problems[:3],
    )
    raw_rooms = tuple(
        replace(room, assets=(raw_asset,) + tuple(room.assets))
        if room.room_id == entry_room.room_id else room
        for room in document.rooms
    )
    raw = replace(document, rooms=raw_rooms)
    raw_problems = RB.validate_room_builder_document(raw)
    report.check(
        "raw_sprite_sheet_refused",
        any("raw sprite" in problem.lower() for problem in raw_problems),
        raw_problems[:3],
    )

    # -- negative: unique style per department -------------------------
    equity = RB.RoomBuilder(document)
    equity.apply(equity.propose_clone_department("floor-001", "floor-002"))
    colliding_rooms = tuple(
        replace(room, style={**room.style, "department_style": "operations"})
        if room.floor_id == "floor-002" else room
        for room in equity.document.rooms
    )
    colliding = replace(equity.document, rooms=colliding_rooms)
    style_problems = RB.validate_room_builder_document(colliding)
    report.check(
        "department_style_collision_refused",
        any("STYLE_COLLISION" in problem for problem in style_problems),
        style_problems[:3],
    )

    # -- atomic publish refusal ----------------------------------------
    atomic = RB.RoomBuilder(document)
    atomic.publish()
    versions_before = len(atomic.versions())
    broken = replace(
        document,
        cells=(replace(document.cells[0], cols=32), *document.cells[1:]),
    )
    atomic._document = broken  # deliberately corrupt the draft
    refused = False
    try:
        atomic.publish()
    except RB.RoomBuilderRefused:
        refused = True
    report.check(
        "invalid_publish_refused_atomically",
        refused and len(atomic.versions()) == versions_before,
        {"refused": refused, "versions": len(atomic.versions())},
    )

    # -- asset analytics (hashes, no paid art) -------------------------
    assets = _asset_report(document)
    report.check(
        "asset_report_clean",
        assets["asset_refs"] == len(document.rooms)
        and assets["paid_refs"] == 0
        and assets["kinds"].get("recipe", 0) == len(document.rooms),
        assets,
    )

    return {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "status": report.status,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
        "capacity": int(summary["capacity"]),
        "egress": int(summary["egress"]),
        "assets": assets,
        "checks": report.checks,
        "problems": report.problems,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    parser.add_argument("--out", default=None,
                        help="optional path to write the JSON report")
    args = parser.parse_args(argv)

    try:
        report = build_report()
    except Exception as exc:  # pragma: no cover - defensive usage/import path
        print(f"verify_room_builder: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        try:
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"verify_room_builder: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(payload)
    else:
        print(f"{report['status']}: {report['counts']} "
              f"checks={len(report['checks'])}")
        for problem in report["problems"]:
            print(f"  {problem}", file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
