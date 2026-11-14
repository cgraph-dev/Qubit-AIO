#!/usr/bin/env python3
"""P7-06 — independent verifier for deterministic simulation audit and replay.

It builds a throwaway authoritative Business OS event log, records an
interaction projection, replays it twice and recomputes every claim:

* two replays of the same history/seed are byte-identical;
* a tampered projection diverges with a typed incident (event ids, expected /
  actual, seed, clock), never a false ``match``;
* causation orders a cause before its effect even when clocks disagree;
* a cancelled replay is labelled ``cancelled`` and a fresh replay still matches;
* an empty history is ``empty``, not an error;
* effects and FX are stubbed, completion claims naming business evidence are
  ignored, and replay performs no store write, model call or socket open;
* the audit ledger is bounded, prunable and tenant-scoped on export.

Hermetic: no network, no service, no credentials, no model. ``--json`` prints a
single machine-readable report.

Exit codes:
    0  every check passed
    1  a check failed
    2  usage / internal error
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business import eventlog  # noqa: E402
from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.simulation_audit import (  # noqa: E402
    DIVERGENCE_CODE,
    TENANT_MISMATCH_CODE,
    AuditLedger,
    export_audit,
    load_history,
    project,
    record_projection,
    replay,
)
from voicebridge.business.store import BusinessStore  # noqa: E402
from voicebridge.business.util import now_iso  # noqa: E402

VERIFICATION_SCHEMA = "qubit-office-simulation-replay-verification/1"
TENANT_NAME = "P7-06 Simulation Fixture"
OTHER_TENANT_NAME = "P7-06 Other Tenant"
CLOCK = "2026-01-01T00:00:00.000Z"
SEED = 424242
BASE_TIME = 1_700_000_000.0
FIXTURE = (
    (
        "office.interaction.started",
        {
            "interaction_id": "int-coffee",
            "agent_id": "agent-ada",
            "target_agent_id": "agent-grace",
            "room_id": "room-lounge",
            "action": "coffee",
            "animation": "walk_to_kitchen",
            "seed": 1001,
        },
    ),
    (
        "office.interaction.effect",
        {
            "interaction_id": "int-coffee",
            "agent_id": "agent-ada",
            "effects": {"energy": 5, "stress": -2, "social": 3},
            "relationships": {"agent-grace": {"trust": 2, "friendship": 1}},
        },
    ),
    (
        "office.interaction.fx",
        {
            "interaction_id": "int-coffee",
            "agent_id": "agent-ada",
            "room_id": "room-lounge",
            "animation": "sip",
            "particle": "steam",
            "seed": 2002,
            "priority": 2,
        },
    ),
    (
        "office.interaction.note",
        {"interaction_id": "int-coffee", "note": "caught up"},
    ),
    (
        "office.interaction.completed",
        {"interaction_id": "int-coffee"},
    ),
)


class _Cfg:
    def __init__(self, state_dir: Path) -> None:
        self._state = state_dir

    def state_dir(self) -> str:
        return str(self._state)


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _append_fixture(
    store: BusinessStore, tenant_id: str, business_id: str,
) -> list[Any]:
    events = []
    previous = None
    for index, (event_type, payload) in enumerate(FIXTURE, start=1):
        event = eventlog.append_event(
            store,
            tenant_id,
            business_id,
            event_type,
            payload,
            actor="agent-ada",
            idempotency_key=f"p7-06-fixture-{index}",
            correlation_id="corr-coffee",
            causation_id=previous,
            now=BASE_TIME + index,
        )
        previous = event.event_id
        events.append(event)
    events.append(eventlog.append_event(
        store,
        tenant_id,
        "task-fixture",
        "task.created",
        {"task_id": "task-fixture"},
        actor="owner",
        idempotency_key="p7-06-fixture-task",
        now=BASE_TIME + len(FIXTURE) + 1,
    ))
    return events


def _counts(store: BusinessStore) -> dict[str, int]:
    with store.read() as conn:
        return {
            table: int(conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "bos_event",
                "bos_audit_event",
                "bos_job",
                "bos_artifact",
                "bos_ledger_entry",
            )
        }


def _direct_event(
    event_id: str,
    event_type: str,
    sequence: int,
    payload: dict[str, Any],
    *,
    at: str,
    tenant_id: str = "ten_direct",
    causation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "aggregate_id": "agg-direct",
        "sequence": sequence,
        "type": event_type,
        "actor": "agent-ada",
        "correlation_id": "corr-direct",
        "causation_id": causation_id,
        "occurred_at": at,
        "payload": payload,
    }


def run_verification(workdir: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    state_dir = workdir / "state"
    store = BusinessStore.from_config(_Cfg(state_dir))
    tenant = store.create_tenant(TENANT_NAME)
    business = store.create_business(
        tenant.tenant_id, TENANT_NAME, slug="p7-06-fixture")
    _append_fixture(store, tenant.tenant_id, business.business_id)
    before = _counts(store)

    history = load_history(store, tenant.tenant_id, limit=100)
    checks.append(_check(
        "authoritative_history_loaded",
        len(history) == 6
        and history[0]["type"] == "office.interaction.started",
        f"events={len(history)} first={history[0]['type']}",
    ))
    checks.append(_check(
        "causation_ordered",
        history[1]["causation_id"] == history[0]["event_id"]
        and history[2]["causation_id"] == history[1]["event_id"],
        "each effect cites the event that caused it",
    ))

    recorded = record_projection(
        history, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    first = replay(
        history, recorded, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    second = replay(
        history, recorded, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    checks.append(_check(
        "replay_matches_recorded",
        first["status"] == "match" and first["incidents"] == [],
        f"status={first['status']} frames={first['frames_checked']}",
    ))
    checks.append(_check(
        "replay_is_deterministic",
        first["replayed"] == second["replayed"]
        and first["run_id"] == second["run_id"]
        and [frame["digest"] for frame in recorded["frames"]]
        == [frame["digest"] for frame in record_projection(
            history, tenant_id=tenant.tenant_id, seed=SEED,
            clock=CLOCK)["frames"]],
        f"digest={first['replayed']['digest'][:16]} run={first['run_id']}",
    ))
    checks.append(_check(
        "seed_is_deterministic",
        recorded["fx"]["seeds"] == [1001, 2002]
        and first["replayed"]["seed"] == SEED,
        f"fx_seeds={recorded['fx']['seeds']} base={first['replayed']['seed']}",
    ))

    tampered = copy.deepcopy(recorded)
    tampered["frames"][1]["state_digest"] = "0" * 64
    divergent = replay(
        history, tampered, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    incident = divergent.get("divergence") or {}
    checks.append(_check(
        "divergence_is_an_incident",
        divergent["status"] == "divergent"
        and divergent["incident_count"] == 1
        and incident.get("code") == DIVERGENCE_CODE
        and incident.get("kind") == "state_digest"
        and incident.get("event_id") == history[1]["event_id"]
        and incident.get("expected") == "0" * 64
        and incident.get("actual") == recorded["frames"][1]["state_digest"]
        and incident.get("seed") == SEED
        and incident.get("clock") == CLOCK,
        (
            f"status={divergent['status']} kind={incident.get('kind')} "
            f"event={incident.get('event_id')} seed={incident.get('seed')}"
        ),
    ))
    checks.append(_check(
        "divergent_is_never_success",
        divergent["status"] != "match"
        and divergent["replayed"]["digest"] == first["replayed"]["digest"],
        "same replay state, different recorded expectation",
    ))

    cancelled = replay(
        history, recorded, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK,
        cancel=lambda: True)
    restarted = replay(
        history, recorded, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    checks.append(_check(
        "cancel_and_replay",
        cancelled["status"] == "cancelled"
        and cancelled["cancelled"] is True
        and restarted["status"] == "match",
        f"cancelled={cancelled['status']} restarted={restarted['status']}",
    ))

    empty_recorded = record_projection(
        [], tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    empty = replay(
        [], empty_recorded, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    checks.append(_check(
        "empty_history_is_empty",
        empty_recorded["state"] == "empty" and empty["status"] == "empty",
        f"record={empty_recorded['state']} replay={empty['status']}",
    ))

    claim_events = [
        _direct_event(
            "claim-e1", "office.interaction.started", 1,
            {"interaction_id": "int-claim", "agent_id": "agent-ada"},
            at="2026-01-01T00:00:01.000Z", tenant_id=tenant.tenant_id,
        ),
        _direct_event(
            "claim-e2", "office.interaction.completed", 2,
            {
                "interaction_id": "int-claim",
                "artifact_id": "art-123",
                "provider_receipt": "rcpt-456",
            },
            at="2026-01-01T00:00:02.000Z", tenant_id=tenant.tenant_id,
            causation_id="claim-e1",
        ),
    ]
    claim = project(
        claim_events, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    checks.append(_check(
        "no_fabricated_work",
        claim["final_state"]["counters"]["claimed_business_truth_ignored"] == 1
        and claim["fabricated_work"] is False
        and "artifacts" not in claim["final_state"]
        and claim["effects"]["mode"] == "stubbed"
        and claim["fx"]["real_calls"] is False,
        "completion claim ignored; no artifact/receipt projected",
    ))

    cause = _direct_event(
        "late-cause", "office.interaction.started", 1,
        {"interaction_id": "int-order", "agent_id": "agent-ada"},
        at="2026-01-01T00:00:10.000Z", tenant_id=tenant.tenant_id,
    )
    effect = _direct_event(
        "early-effect", "office.interaction.effect", 1,
        {"interaction_id": "int-order", "effects": {"focus": 1}},
        at="2026-01-01T00:00:01.000Z", tenant_id=tenant.tenant_id,
        causation_id="late-cause",
    )
    ordered = project(
        [effect, cause], tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    checks.append(_check(
        "cause_precedes_effect",
        [frame["event_id"] for frame in ordered["frames"]]
        == ["late-cause", "early-effect"],
        str([frame["event_id"] for frame in ordered["frames"]]),
    ))

    def no_socket(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("replay opened a socket")

    original_socket = socket.socket
    original_connect = socket.create_connection
    socket.socket = no_socket  # type: ignore[assignment]
    socket.create_connection = no_socket  # type: ignore[assignment]
    try:
        no_network = replay(
            history, recorded, tenant_id=tenant.tenant_id, seed=SEED,
            clock=CLOCK)
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
        socket.create_connection = original_connect  # type: ignore[assignment]
    checks.append(_check(
        "no_external_side_effects",
        no_network["status"] == "match"
        and no_network["external_side_effects"] is False
        and _counts(store) == before,
        "replay ran with sockets disabled and wrote no store rows",
    ))

    other = store.create_tenant(OTHER_TENANT_NAME)
    other_business = store.create_business(
        other.tenant_id, OTHER_TENANT_NAME, slug="p7-06-other")
    eventlog.append_event(
        store,
        other.tenant_id,
        other_business.business_id,
        "office.interaction.started",
        {"interaction_id": "int-other", "agent_id": "agent-other"},
        actor="agent-other",
        idempotency_key="p7-06-other",
        now=BASE_TIME + 100.0,
    )
    other_history = load_history(store, other.tenant_id, limit=100)
    other_recorded = record_projection(
        other_history, tenant_id=other.tenant_id, seed=SEED, clock=CLOCK)
    other_result = replay(
        other_history, other_recorded, tenant_id=other.tenant_id, seed=SEED,
        clock=CLOCK)

    ledger = AuditLedger(max_runs=3, clock=CLOCK)
    match_row = ledger.record(first)
    divergent_row = ledger.record(divergent)
    other_row = ledger.record(other_result)
    export = export_audit(ledger, tenant_id=tenant.tenant_id)
    checks.append(_check(
        "bounded_tenant_scoped_audit",
        ledger.counts(tenant_id=tenant.tenant_id)["runs"] == 2
        and ledger.counts(tenant_id=other.tenant_id)["runs"] == 1
        and {row["run_id"] for row in export["runs"]}
        == {match_row["run_id"], divergent_row["run_id"]}
        and other_row["run_id"] not in {
            row["run_id"] for row in export["runs"]}
        and export["tenant_scoped"] is True
        and export["retention"]["max_runs"] == 3,
        (
            f"runs={export['counts']['runs']} "
            f"divergent={export['counts']['divergent_runs']} "
            f"dropped={export['retention']['dropped_runs']}"
        ),
    ))

    try:
        project(
            history + other_history, tenant_id=tenant.tenant_id, seed=SEED,
            clock=CLOCK)
    except BusinessError as exc:
        refusal = exc.code
    else:
        refusal = None
    checks.append(_check(
        "tenant_mismatch_is_typed",
        refusal == TENANT_MISMATCH_CODE,
        f"code={refusal}",
    ))

    snapshot = {
        "tenant_id": tenant.tenant_id,
        "history_events": len(history),
        "frames": recorded["frame_count"],
        "final_digest": recorded["final_digest"],
        "fx_seeds": recorded["fx"]["seeds"],
        "run_id": first["run_id"],
    }
    passed = sum(1 for check in checks if check["ok"])
    return {
        "schema": VERIFICATION_SCHEMA,
        "task_id": "P7-06",
        "generated_at": now_iso(),
        "state": "PASS" if passed == len(checks) else "FAIL",
        "fixture": snapshot,
        "summary": {
            "checks": len(checks),
            "passed": passed,
            "failed": len(checks) - passed,
        },
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify deterministic office simulation replay (P7-06).")
    parser.add_argument("--json", action="store_true", help="machine report")
    parser.add_argument(
        "--workdir",
        help="directory for the throwaway store (default: a temp dir)",
    )
    args = parser.parse_args(argv)
    workdir = (
        Path(args.workdir).expanduser().resolve()
        if args.workdir else Path(tempfile.mkdtemp(prefix="qubit-p7-06-"))
    )
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        report = run_verification(workdir)
    except Exception as exc:  # noqa: BLE001 - verifier reports, never crashes
        report = {
            "schema": VERIFICATION_SCHEMA,
            "task_id": "P7-06",
            "generated_at": now_iso(),
            "state": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "summary": {"checks": 0, "passed": 0, "failed": 1},
            "checks": [],
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"FAIL {report['error']}")
        return 2
    finally:
        if not args.workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"{mark} {check['name']}: {check['detail']}")
        print(
            f"{report['state']} {report['summary']['passed']}/"
            f"{report['summary']['checks']}")
    return 0 if report["state"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
