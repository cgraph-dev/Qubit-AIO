#!/usr/bin/env python3
"""Verify the Qubit Office simulation/action catalog (P7-01).

This is the evidence instrument for P7-01. It exercises the real shipped
catalog on :mod:`voicebridge.business.simulation` and checks the acceptance
items the plan names:

* the catalog validates and covers work, social, needs, conflict and career;
* every romance/risky action is flagged ``mature_content``;
* selection and replay are deterministic (identical digests on repeat);
* preconditions, cooldowns, props, rooms, approval and consent are enforced
  with typed refusals and a refused action is never executed;
* effects and relationships change declared simulation state within bounds;
* mature content is off by default and only routed when the context opts in;
* a result never exposes a business-truth key and only emits ``simulation.*``
  memory notes.

``--json`` prints one machine-readable report. Nothing is written; the check is
read-only and hermetic (no clock, store, network, model or random source).

Exit codes:
    0  every check passed
    1  a check failed or the shipped catalog did not validate
    2  usage error or unexpected internal failure
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business import simulation as sim  # noqa: E402

SCHEMA_REPORT = "qubit-office-simulation-verification/1"


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _context(**overrides: Any) -> sim.SimulationContext:
    values: Dict[str, Any] = {
        "room_type": "work",
        "props": frozenset(
            {"computer", "whiteboard", "phone", "water_cooler",
             "coffee_machine"}
        ),
        "privacy_class": "department",
        "time_slot": "morning",
        "participant_count": 2,
        "target_agent_id": "agent-b",
    }
    values.update(overrides)
    return sim.SimulationContext(**values)


def _state(**overrides: Any) -> sim.AgentSimState:
    values: Dict[str, Any] = {
        "agent_id": "agent-a",
        "traits": sim.Traits.from_names(("calm", "ambitious")),
        "relationships": {
            "agent-b": sim.Relationship(trust=20.0, friendship=15.0),
        },
    }
    values.update(overrides)
    return sim.AgentSimState(**values)


def run_checks() -> Dict[str, Any]:
    catalog = sim.default_catalog()
    counts = catalog.category_counts()
    actions = catalog.actions
    mature = [a for a in actions if a.mature_content]
    mature_categories = [a for a in actions
                         if a.category in sim.MATURE_CATEGORIES]

    # -- catalog --------------------------------------------------------
    catalog_ok = True
    catalog_detail = "catalog validates"
    try:
        catalog.validate()
    except sim.SimulationError as exc:  # pragma: no cover - defensive
        catalog_ok = False
        catalog_detail = f"{exc.code}: {exc}"
    missing_categories = [name for name in sim.REQUIRED_CATEGORIES
                          if counts[name] == 0]

    # -- determinism ----------------------------------------------------
    context = _context()
    base_state = _state()
    first = catalog.select(base_state, context)
    second = catalog.select(_state(), _context())
    selection_ok = (
        first.action_id is not None
        and first.action_id == second.action_id
        and first.score == second.score
    )

    replay_contexts = (
        _context(),
        _context(room_type="break_room", props=frozenset(
            {"coffee_machine", "water_cooler", "snack_machine"})),
        _context(room_type="work", props=frozenset(
            {"computer", "whiteboard", "phone"})),
    )
    replay_actions = ("social-coffee", "needs-water", "work-code-review")

    def _replay() -> tuple[sim.AgentSimState, List[Dict[str, Any]]]:
        state = _state()
        results: List[Dict[str, Any]] = []
        for action_id, ctx in zip(replay_actions, replay_contexts):
            state, result = sim.simulate(
                state, catalog, ctx, action_id=action_id
            )
            results.append(result.to_dict())
        return state, results

    state_a, results_a = _replay()
    state_b, results_b = _replay()
    replay_ok = (
        state_a.digest == state_b.digest
        and [r["digest"] for r in results_a] == [r["digest"] for r in results_b]
        and all(r["executed"] for r in results_a)
    )

    # -- precondition / cooldown / prop / room rejection ----------------
    tired = _state(needs=sim.Needs(energy=5.0))
    tired_state, tired_result = sim.simulate(
        tired, catalog, _context(), action_id="work-deadline-sprint"
    )
    precondition_ok = (
        not tired_result.accepted
        and tired_result.code == sim.SIMULATION_PRECONDITION_CODE
        and not tired_result.executed
        and tired_state.digest == tired.digest
    )

    coffee_state, coffee_result = sim.simulate(
        base_state, catalog, _context(), action_id="social-coffee"
    )
    _, again_result = sim.simulate(
        coffee_state, catalog, _context(), action_id="social-coffee"
    )
    cooldown_ok = (
        coffee_result.executed
        and not again_result.accepted
        and again_result.code == sim.SIMULATION_COOLDOWN_CODE
        and not again_result.executed
    )

    no_props_ctx = _context(props=frozenset())
    _, no_props = sim.simulate(
        base_state, catalog, no_props_ctx, action_id="social-coffee"
    )
    prop_ok = (
        not no_props.accepted
        and no_props.code == sim.SIMULATION_MISSING_PROP_CODE
        and not no_props.executed
    )

    wrong_room_ctx = _context(room_type="bathroom")
    _, wrong_room = sim.simulate(
        base_state, catalog, wrong_room_ctx, action_id="social-coffee"
    )
    room_ok = (
        not wrong_room.accepted
        and wrong_room.code == sim.SIMULATION_WRONG_ROOM_CODE
        and not wrong_room.executed
    )

    # -- approval / consent --------------------------------------------
    _, approval_missing = sim.simulate(
        base_state, catalog, _context(), action_id="career-promotion"
    )
    approval_ok = (
        not approval_missing.accepted
        and approval_missing.code == sim.SIMULATION_APPROVAL_CODE
    )
    romance_ctx = _context(
        privacy_class="private",
        allow_mature=True,
        consent_granted=False,
        approval_granted=True,
    )
    romance_state = _state(relationships={
        "agent-b": sim.Relationship(
            trust=50.0, attraction=60.0, friendship=40.0
        ),
    })
    _, consent_missing = sim.simulate(
        romance_state, catalog, romance_ctx, action_id="romance-kiss"
    )
    consent_ok = (
        not consent_missing.accepted
        and consent_missing.code == sim.SIMULATION_CONSENT_CODE
    )

    # -- effects and relationships --------------------------------------
    coffee_after = coffee_state.relationships["agent-b"]
    before_friendship = base_state.relationships["agent-b"].friendship
    relationship_ok = (
        coffee_result.executed
        and coffee_after.friendship > before_friendship
        and coffee_result.relationship_after["friendship"]
        > coffee_result.relationship_before["friendship"]
        and coffee_result.needs_after["social"]
        > coffee_result.needs_before["social"]
    )
    effects_ok = (
        coffee_result.needs_after["social"] > coffee_result.needs_before["social"]
        and coffee_result.needs_after["stress"]
        < coffee_result.needs_before["stress"]
    )

    # -- mature routing -------------------------------------------------
    mature_all_flagged = all(
        action.mature_content for action in mature_categories
    ) and all(action.mature_content for action in mature)
    mature_off_ok = mature_all_flagged
    default_selection = catalog.select(base_state, _context())
    default_mature_rejected = any(
        row["code"] == sim.SIMULATION_MATURE_CODE
        for row in default_selection.rejected
    )
    _, explicit_mature = sim.simulate(
        romance_state, catalog, _context(), action_id="romance-flirt"
    )
    mature_off_ok = (
        mature_off_ok
        and default_mature_rejected
        and explicit_mature.code == sim.SIMULATION_MATURE_CODE
    )
    mature_select = catalog.select(
        romance_state,
        _context(
            privacy_class="private",
            allow_mature=True,
            consent_granted=True,
            approval_granted=True,
        ),
        categories=("romance",),
    )
    mature_on_ok = (
        mature_select.action is not None
        and mature_select.action.mature_content
        and mature_select.action.category == "romance"
    )

    # -- result safety --------------------------------------------------
    business_truth_ok = True
    business_truth_detail = "no business-truth keys"
    try:
        sim.validate_result(coffee_result)
    except sim.SimulationError as exc:  # pragma: no cover - defensive
        business_truth_ok = False
        business_truth_detail = f"{exc.code}: {exc}"
    payload_keys = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                payload_keys.add(str(key))
                _walk(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                _walk(inner)

    _walk(coffee_result.to_dict())
    truth_hits = sorted(payload_keys & set(sim.FORBIDDEN_TRUTH_KEYS))
    memory_ok = all(
        result.memory_event.startswith(sim.MEMORY_EVENT_PREFIX)
        for result in (coffee_result, tired_result, approval_missing)
        if result.memory_event
    )
    business_truth_ok = business_truth_ok and not truth_hits and memory_ok

    # -- bounded state --------------------------------------------------
    state = _state()
    bounded_contexts = (
        _context(),
        _context(room_type="break_room", props=frozenset(
            {"coffee_machine", "water_cooler", "snack_machine"})),
        _context(room_type="bathroom", privacy_class="private",
                 props=frozenset({"sink", "toilet"})),
        _context(room_type="work", props=frozenset(
            {"computer", "whiteboard", "phone"})),
    )
    for step in range(200):
        state, _ = sim.simulate(
            state, catalog, bounded_contexts[step % len(bounded_contexts)]
        )
    bounded_ok = (
        all(0.0 <= value <= 100.0 for value in state.needs.as_dict().values())
        and sim.REPUTATION_MIN <= state.reputation <= sim.REPUTATION_MAX
        and all(
            sim.RELATIONSHIP_MIN <= value <= sim.RELATIONSHIP_MAX
            for rel in state.relationships.values()
            for value in rel.as_dict().values()
        )
        and len(state.history) <= 32
        and len(state.notes) <= 32
        and all(0 <= value <= 10000 for value in state.cooldowns.values())
        and state.revision == 200
    )

    checks: List[Dict[str, Any]] = [
        _check("schema_version", sim.SCHEMA == sim.SCHEMA
               and sim.VERSION == 1, f"{sim.SCHEMA} v{sim.VERSION}"),
        _check("catalog_valid", catalog_ok, catalog_detail),
        _check(
            "required_categories",
            not missing_categories,
            f"counts={counts}",
        ),
        _check(
            "mature_flagged",
            mature_all_flagged,
            f"{len(mature)} mature actions, {len(mature_categories)}"
            " in mature categories",
        ),
        _check(
            "deterministic_selection",
            selection_ok,
            f"selected {first.action_id!r} score={first.score}",
        ),
        _check(
            "deterministic_replay",
            replay_ok,
            f"state={state_a.digest}",
        ),
        _check(
            "precondition_rejection",
            precondition_ok,
            f"code={tired_result.code}",
        ),
        _check(
            "cooldown_rejection",
            cooldown_ok,
            f"code={again_result.code}",
        ),
        _check("prop_rejection", prop_ok, f"code={no_props.code}"),
        _check("room_rejection", room_ok, f"code={wrong_room.code}"),
        _check(
            "approval_enforced", approval_ok, f"code={approval_missing.code}"
        ),
        _check("consent_enforced", consent_ok, f"code={consent_missing.code}"),
        _check("effects_change_state", effects_ok, "social up, stress down"),
        _check(
            "relationship_change",
            relationship_ok,
            f"friendship {before_friendship} -> {coffee_after.friendship}",
        ),
        _check(
            "mature_off_by_default",
            mature_off_ok,
            "mature actions rejected without opt-in",
        ),
        _check(
            "mature_routing",
            mature_on_ok,
            f"selected {mature_select.action_id!r} with consent + approval",
        ),
        _check(
            "no_business_truth",
            business_truth_ok,
            business_truth_detail + f" keys={sorted(payload_keys)[:6]}",
        ),
        _check(
            "bounded_state",
            bounded_ok,
            f"tick={state.tick} revision={state.revision}"
            f" needs={state.needs.as_dict()}",
        ),
    ]

    return {
        "schema": SCHEMA_REPORT,
        "status": "PASS" if all(row["ok"] for row in checks) else "FAIL",
        "catalog_schema": sim.SCHEMA,
        "catalog_version": sim.VERSION,
        "catalog_digest": catalog.digest,
        "action_count": len(actions),
        "mature_action_count": len(mature),
        "counts_by_category": counts,
        "checks_total": len(checks),
        "checks_failed": sum(1 for row in checks if not row["ok"]),
        "checks": checks,
        "summary": {
            "selected_action": first.action_id,
            "selected_score": first.score,
            "replay_digest": state_a.digest,
            "romance_route": mature_select.action_id,
        },
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_simulation_actions.py",
        description=(
            "Verify the data-driven Office simulation action catalog (P7-01)."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the full report as JSON"
    )
    args = parser.parse_args(argv)
    try:
        report = run_checks()
    except sim.SimulationError as exc:
        failure = {
            "schema": SCHEMA_REPORT,
            "status": "FAIL",
            "code": exc.code,
            "detail": str(exc),
        }
        if args.json:
            json.dump(failure, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        else:
            print(f"FAIL {exc.code}: {exc}", file=sys.stderr)
        return 1
    except Exception:  # pragma: no cover - unexpected
        if args.json:
            json.dump(
                {
                    "schema": SCHEMA_REPORT,
                    "status": "FAIL",
                    "code": "UNEXPECTED",
                    "detail": traceback.format_exc(),
                },
                sys.stdout,
                indent=2,
                sort_keys=True,
            )
            sys.stdout.write("\n")
        else:
            traceback.print_exc()
        return 2

    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(
            f"{report['status']} · {report['action_count']} actions"
            f" · {report['checks_total'] - report['checks_failed']}"
            f"/{report['checks_total']} checks"
            f" · digest {report['catalog_digest'][:16]}…"
        )
        for row in report["checks"]:
            mark = "ok" if row["ok"] else "FAIL"
            print(f"  [{mark}] {row['name']}: {row['detail']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
