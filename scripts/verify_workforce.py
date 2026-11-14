#!/usr/bin/env python3
"""Independent verifier for the P4-01 workforce lanes.

WHY THIS EXISTS
---------------
The workforce status is not its own witness. This script builds a real
(throwaway) Business OS database with the actual services, provisions the
lanes, and then re-reads the SQLite stores directly:

* agent/session/workspace/task counts and the session<->workspace<->task links;
* exactly one durable lane per non-archived agent;
* the reported working state recomputed from raw agent/session/turn/lease rows;
* ``paused``/``revoked``/``archived`` are never working; a projection is not
  ``live``;
* grants carry no wildcard and every non-archived agent has explicit access;
* expired leases are never reported active; reconciliation claims nothing;
* tenant isolation (another tenant sees none of these agents).

It also proves the verifier can fail: a second fixture has a planted wildcard
grant and a broken workspace link, and the same checks must reject it.

Exit codes: ``0`` all checks pass, ``1`` a check failed, ``2`` usage/internal.

Hermetic by default: no network, no credentials, no live service. ``--db``
verifies an existing store read-only if it is already at the current schema.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import AgentControlService, BusinessStore  # noqa: E402
from voicebridge.business import identity as IDENTITY  # noqa: E402
from voicebridge.business import runs as RUNS  # noqa: E402
from voicebridge.business.schema import SCHEMA_VERSION  # noqa: E402
from voicebridge.business.workforce import (  # noqa: E402
    ALL_STATES,
    reconcile_workforce,
    provision_workforce,
    workforce_status,
)

_TERMINAL_TASKS = frozenset({"completed", "rejected", "cancelled"})
_PROJECTION_SOURCES = frozenset(
    {"office_plan_building", "seed", "projection", "seed_projection"}
)


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[Any]:
    return conn.execute(sql, params).fetchall()


def _json(text: Any) -> Any:
    if not text:
        return {}
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def _raw_agents(
    conn: sqlite3.Connection, tenant_id: str, business_id: str
) -> dict[str, dict[str, Any]]:
    rows = _rows(
        conn,
        "SELECT * FROM bos_agent WHERE tenant_id=? AND business_id=?",
        (tenant_id, business_id),
    )
    return {row["agent_id"]: dict(row) for row in rows}


def _raw_sessions(
    conn: sqlite3.Connection, tenant_id: str, business_id: str
) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        "SELECT * FROM bos_session WHERE tenant_id=? AND business_id=?"
        " ORDER BY created_at, session_id",
        (tenant_id, business_id),
    )
    return [dict(row) for row in rows]


def _raw_workspaces(
    conn: sqlite3.Connection, tenant_id: str
) -> dict[str, dict[str, Any]]:
    rows = _rows(
        conn, "SELECT * FROM bos_workspace WHERE tenant_id=?", (tenant_id,)
    )
    return {row["workspace_id"]: dict(row) for row in rows}


def _raw_tasks(
    conn: sqlite3.Connection, tenant_id: str
) -> dict[str, dict[str, Any]]:
    rows = _rows(
        conn, "SELECT * FROM bos_task WHERE tenant_id=?", (tenant_id,)
    )
    return {row["task_id"]: dict(row) for row in rows}


def _agent_principal(
    conn: sqlite3.Connection, tenant_id: str, agent: dict[str, Any],
    principals: list[dict[str, Any]],
) -> str | None:
    if agent.get("security_principal_id"):
        return str(agent["security_principal_id"])
    for principal in principals:
        if (
            principal["kind"] == "agent"
            and principal["external_ref"] == agent["agent_id"]
        ):
            return principal["principal_id"]
    return None


def _raw_grants(
    conn: sqlite3.Connection, tenant_id: str,
    subject_ids: set[str],
) -> list[dict[str, Any]]:
    if not subject_ids:
        return []
    marks = ",".join("?" for _ in subject_ids)
    rows = _rows(
        conn,
        "SELECT * FROM bos_grant WHERE tenant_id=? AND subject_id IN"
        f" ({marks}) ORDER BY created_at, grant_id",
        (tenant_id, *sorted(subject_ids)),
    )
    return [dict(row) for row in rows]


def _raw_leases(
    conn: sqlite3.Connection, tenant_id: str, resource_type: str,
    resource_id: str | None, now: float,
) -> list[dict[str, Any]]:
    if not resource_id:
        return []
    rows = _rows(
        conn,
        "SELECT * FROM bos_lease WHERE tenant_id=? AND resource_type=?"
        " AND resource_id=? ORDER BY acquired_at, lease_id",
        (tenant_id, resource_type, resource_id),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        if data["released_at"] is not None:
            state = "released"
        elif float(data["expires_at"]) <= now:
            state = "expired"
        else:
            state = "active"
        data["state"] = state
        out.append(data)
    return out


def _expected_state(
    agent: dict[str, Any],
    session: dict[str, Any] | None,
    workspace: dict[str, Any] | None,
    task: dict[str, Any] | None,
    active_turns: int,
    leases: list[dict[str, Any]],
) -> str:
    if agent["status"] == "archived":
        return "archived"
    if agent["status"] == "revoked":
        return "revoked"
    if agent["status"] == "paused":
        return "paused"
    if session is not None and session["status"] == "archived":
        return "archived"
    if session is not None and session["status"] == "paused":
        return "paused"
    metadata = _json(agent.get("metadata_json"))
    source = str(metadata.get("source") or "").lower()
    if source in _PROJECTION_SOURCES:
        return "projection"
    if session is None or workspace is None:
        return "disconnected"
    if workspace["session_id"] != session["session_id"]:
        return "disconnected"
    has_expired = any(row["state"] == "expired" for row in leases)
    if has_expired and (task is None or task["workflow_status"] not in _TERMINAL_TASKS):
        return "unknown"
    if active_turns > 0:
        return "live"
    return "idle"


def _scan(
    conn: sqlite3.Connection, tenant_id: str, business_id: str,
    now: float,
) -> dict[str, Any]:
    agents = _raw_agents(conn, tenant_id, business_id)
    sessions = _raw_sessions(conn, tenant_id, business_id)
    workspaces = _raw_workspaces(conn, tenant_id)
    tasks = _raw_tasks(conn, tenant_id)
    principals = [
        dict(row)
        for row in _rows(
            conn, "SELECT * FROM bos_principal WHERE tenant_id=?", (tenant_id,)
        )
    ]
    turns = {
        row["agent_id"]: int(row["n"])
        for row in _rows(
            conn,
            "SELECT agent_id, COUNT(*) AS n FROM bos_agent_turn"
            " WHERE tenant_id=? AND status='active' GROUP BY agent_id",
            (tenant_id,),
        )
    }
    lanes: dict[str, dict[str, Any]] = {}
    for agent_id in agents:
        matches = [row for row in sessions if row["agent_id"] == agent_id]
        lanes[agent_id] = {
            "sessions": matches,
            "session": matches[0] if matches else None,
        }
    for agent_id, lane in lanes.items():
        session = lane["session"]
        workspace = None
        if session is not None and session.get("default_workspace_id"):
            candidate = workspaces.get(session["default_workspace_id"])
            if candidate is not None and candidate["session_id"] == session["session_id"]:
                workspace = candidate
        lane["workspace"] = workspace
        lane["task"] = (
            tasks.get(workspace["task_id"]) if workspace is not None else None
        )
        leases = _raw_leases(
            conn,
            tenant_id,
            "workspace",
            workspace["workspace_id"] if workspace else None,
            now,
        )
        leases += _raw_leases(
            conn,
            tenant_id,
            "task",
            workspace["task_id"] if workspace else None,
            now,
        )
        lane["leases"] = leases
        lane["principal_id"] = _agent_principal(
            conn, tenant_id, agents[agent_id], principals
        )
        lane["expected_state"] = _expected_state(
            agents[agent_id],
            session,
            workspace,
            lane["task"],
            turns.get(agent_id, 0),
            leases,
        )
    subject_ids = {
        lane["principal_id"] for lane in lanes.values()
        if lane["principal_id"]
    }
    return {
        "agents": agents,
        "sessions": sessions,
        "workspaces": workspaces,
        "tasks": tasks,
        "lanes": lanes,
        "grants": _raw_grants(conn, tenant_id, subject_ids),
    }


def verify_workforce_store(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    *,
    now: float | None = None,
    other_tenant_id: str | None = None,
    other_business_id: str | None = None,
) -> dict[str, Any]:
    """Re-read the stores and verify the workforce projection independently."""
    moment = time.time() if now is None else float(now)
    status = workforce_status(store, tenant_id, business_id, now=moment)
    reconcile = reconcile_workforce(store, tenant_id, business_id, now=moment)
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        raw = _scan(conn, tenant_id, business_id, moment)
        checks: list[dict[str, Any]] = []
        reported = {item["agent_id"]: item for item in status["agents"]}
        agents = raw["agents"]
        lanes = raw["lanes"]

        checks.append(_check(
            "no_invented_agents",
            set(reported) == set(agents),
            f"reported={sorted(reported)} raw={sorted(agents)}",
        ))
        checks.append(_check(
            "counts_match_sql",
            status["counts"]["agents"] == len(agents),
            f"reported={status['counts']['agents']} sql={len(agents)}",
        ))

        lane_problems: list[str] = []
        for agent_id, agent in agents.items():
            if agent["status"] == "archived":
                continue
            sessions = lanes[agent_id]["sessions"]
            if len(sessions) != 1:
                lane_problems.append(f"{agent_id}: {len(sessions)} sessions")
            elif lanes[agent_id]["workspace"] is None:
                lane_problems.append(f"{agent_id}: no bound workspace")
        checks.append(_check(
            "one_lane_per_agent",
            not lane_problems,
            "; ".join(lane_problems) or f"{len(agents)} agents, one lane each",
        ))

        link_problems: list[str] = []
        for agent_id, lane in lanes.items():
            session = lane["session"]
            if session is None:
                continue
            workspace = lane["workspace"]
            if workspace is None:
                link_problems.append(f"{agent_id}: workspace missing")
                continue
            if session.get("default_workspace_id") != workspace["workspace_id"]:
                link_problems.append(f"{agent_id}: session not bound")
            if workspace["session_id"] != session["session_id"]:
                link_problems.append(f"{agent_id}: workspace session mismatch")
            if workspace["task_id"] not in raw["tasks"]:
                link_problems.append(f"{agent_id}: task missing")
            elif workspace["task_id"] != lane["task"]["task_id"]:
                link_problems.append(f"{agent_id}: task mismatch")
        checks.append(_check(
            "session_workspace_links",
            not link_problems,
            "; ".join(link_problems) or "session -> workspace -> task all linked",
        ))

        state_problems = [
            f"{agent_id}: reported={reported[agent_id]['state']}"
            f" expected={lane['expected_state']}"
            for agent_id, lane in lanes.items()
            if reported[agent_id]["state"] != lane["expected_state"]
        ]
        checks.append(_check(
            "state_matches_raw",
            not state_problems,
            "; ".join(state_problems) or "every state recomputed from raw rows",
        ))

        non_working_problems = [
            f"{agent_id}: {reported[agent_id]['state']}"
            for agent_id, agent in agents.items()
            if agent["status"] in {"paused", "revoked", "archived"}
            and (
                reported[agent_id]["working"]
                or reported[agent_id]["state"] != agent["status"]
            )
        ]
        checks.append(_check(
            "paused_revoked_archived_not_working",
            not non_working_problems,
            "; ".join(non_working_problems) or "all declared non-working",
        ))

        projection_problems = [
            f"{agent_id}: {reported[agent_id]['state']}"
            for agent_id, lane in lanes.items()
            if lane["expected_state"] == "projection"
            and (
                reported[agent_id]["working"]
                or reported[agent_id]["state"] != "projection"
            )
        ]
        checks.append(_check(
            "projection_not_live",
            not projection_problems,
            "; ".join(projection_problems) or "seed/projection agents are not live",
        ))

        wildcard = [
            f"{row['grant_id']}:{row['capability']}/{row['resource']}/{row['action']}"
            for row in raw["grants"]
            if any(c in str(row[key]) for key in ("capability", "resource", "action")
                   for c in ("*", "?"))
        ]
        checks.append(_check(
            "no_wildcard_grants",
            not wildcard,
            "; ".join(wildcard) or f"{len(raw['grants'])} explicit grants",
        ))

        missing_access: list[str] = []
        for agent_id, agent in agents.items():
            if agent["status"] == "archived":
                continue
            principal_id = lanes[agent_id]["principal_id"]
            if not principal_id:
                missing_access.append(f"{agent_id}: no principal")
                continue
            mine = [
                row for row in raw["grants"]
                if row["subject_id"] == principal_id
                and row["revoked_at"] is None
                and row["effect"] == "allow"
            ]
            if not mine:
                missing_access.append(f"{agent_id}: no allow grant")
        checks.append(_check(
            "every_agent_has_explicit_access",
            not missing_access,
            "; ".join(missing_access) or "every non-archived agent has an allow grant",
        ))

        active_mismatch: list[str] = []
        for agent_id, lane in lanes.items():
            reported_lease = reported[agent_id]["lease"]
            raw_active = sum(1 for row in lane["leases"] if row["state"] == "active")
            raw_expired = sum(1 for row in lane["leases"] if row["state"] == "expired")
            if reported_lease["active_count"] != raw_active:
                active_mismatch.append(f"{agent_id}: active count")
            if reported_lease["expired_count"] != raw_expired:
                active_mismatch.append(f"{agent_id}: expired count")
            if raw_expired and reported_lease["state"] == "active":
                active_mismatch.append(f"{agent_id}: expired reported active")
        checks.append(_check(
            "expired_leases_not_active",
            not active_mismatch,
            "; ".join(active_mismatch) or "lease states match raw expiry",
        ))

        checks.append(_check(
            "reconcile_claims_nothing",
            reconcile["mutated"] is False
            and all(item["claimed"] is False for item in reconcile["lanes"])
            and reconcile["counts"]["claimed"] == 0,
            f"mutated={reconcile['mutated']} "
            f"claimed={reconcile['counts']['claimed']} "
            f"unknown={reconcile['counts']['unknown']}",
        ))

        if other_tenant_id is not None and other_business_id is not None:
            other = workforce_status(
                store, other_tenant_id, other_business_id
            )
            other_ids = {item["agent_id"] for item in other["agents"]}
            checks.append(_check(
                "tenant_isolation",
                not (other_ids & set(agents)),
                f"other_tenant_agents={sorted(other_ids)}",
            ))
        else:
            checks.append(_check(
                "tenant_isolation",
                set(reported) == set(agents),
                "all reported agents exist in this tenant/business",
            ))
    finally:
        conn.close()

    failed = [row for row in checks if not row["ok"]]
    return {
        "schema": "qubit-office-workforce-verification/1",
        "mode": "store",
        "tenant_id": tenant_id,
        "business_id": business_id,
        "ok": not failed,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks": checks,
        "status": status,
        "reconcile": reconcile,
    }


def _seed_fixture(root: Path) -> dict[str, Any]:
    """Create a real store with idle, expired, paused, revoked, archived and
    projection agents, provision their lanes, and return the handles."""
    store = BusinessStore(root / "business.db")
    tenant = store.create_tenant("Workforce Holdings")
    business = store.create_business(
        tenant.tenant_id, "Workforce", slug="workforce"
    )
    owner = IDENTITY.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref="owner"
    )
    service = AgentControlService(store)

    def make(key: str, name: str, *, source: str | None = None) -> Any:
        agent, _ = service.create(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            role_id=f"role.{key}",
            department="Operations",
            display_name=name,
            human_owner="owner",
            provider="qubit",
            model="active",
            idempotency_key=f"wf-{key}",
            metadata={"source": source} if source else None,
        )
        return agent

    idle = make("idle", "Idle Agent")
    expired = make("expired", "Expired Agent")
    paused = make("paused", "Paused Agent")
    revoked = make("revoked", "Revoked Agent")
    archived = make("archived", "Archived Agent")
    projection = make("projection", "Seed Agent", source="office_plan_building")

    service.pause(
        tenant.tenant_id, paused.agent_id,
        idempotency_key="wf-pause", actor="owner",
    )
    service.revoke(
        tenant.tenant_id, revoked.agent_id,
        idempotency_key="wf-revoke", actor="owner",
    )
    service.archive(
        tenant.tenant_id, archived.agent_id,
        idempotency_key="wf-archive", actor="owner",
    )

    provisioned = provision_workforce(
        store, tenant.tenant_id, business.business_id, actor="owner"
    )
    lanes = {item["agent_id"]: item for item in provisioned["agents"]}
    expired_lane = lanes[expired.agent_id]
    principal_id = expired_lane["principal_id"]
    RUNS.acquire_lease(
        store,
        tenant.tenant_id,
        "workspace",
        expired_lane["workspace_id"],
        holder=principal_id,
        ttl_seconds=1,
        now=time.time() - 100,
    )

    other_tenant = store.create_tenant("Other Holdings")
    other_business = store.create_business(
        other_tenant.tenant_id, "Other", slug="other"
    )
    IDENTITY.create_principal(
        store, other_tenant.tenant_id, kind="owner", external_ref="owner"
    )
    other_agent, _ = service.create(
        tenant_id=other_tenant.tenant_id,
        business_id=other_business.business_id,
        role_id="role.other",
        department="Other",
        display_name="Other Agent",
        human_owner="owner",
        provider="qubit",
        model="active",
        idempotency_key="wf-other",
    )
    provision_workforce(
        store, other_tenant.tenant_id, other_business.business_id,
        actor="owner",
    )
    return {
        "store": store,
        "tenant": tenant,
        "business": business,
        "owner": owner,
        "agents": {
            "idle": idle,
            "expired": expired,
            "paused": paused,
            "revoked": revoked,
            "archived": archived,
            "projection": projection,
        },
        "provisioned": provisioned,
        "lanes": lanes,
        "other": {
            "tenant": other_tenant,
            "business": other_business,
            "agent": other_agent,
        },
    }


def _plant_inconsistencies(root: Path) -> dict[str, Any]:
    """Seed a valid lane, then plant a wildcard grant and break the link."""
    fixture = _seed_fixture(root)
    store: BusinessStore = fixture["store"]
    tenant = fixture["tenant"]
    lane = fixture["lanes"][fixture["agents"]["idle"].agent_id]
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO bos_grant(grant_id, tenant_id, subject_id, role_id,"
            " capability, resource, action, data_class, environment, effect,"
            " expires_at, approval_required, created_at)"
            " VALUES(?,?,?,NULL,'*','*','*','internal','production','allow',"
            " NULL,0,?)",
            ("grnt_planted_wildcard", tenant.tenant_id,
             lane["principal_id"], "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "UPDATE bos_session SET default_workspace_id=NULL"
            " WHERE tenant_id=? AND session_id=?",
            (tenant.tenant_id, lane["session_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return fixture


def run_self_check(now: float | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="qubit-workforce-") as tmp:
        fixture = _seed_fixture(Path(tmp) / "good")
        report = verify_workforce_store(
            fixture["store"],
            fixture["tenant"].tenant_id,
            fixture["business"].business_id,
            now=now,
            other_tenant_id=fixture["other"]["tenant"].tenant_id,
            other_business_id=fixture["other"]["business"].business_id,
        )
        checks.extend(report["checks"])
        checks.append(_check(
            "good_fixture_passes",
            report["ok"],
            f"{report['checks_total'] - report['checks_failed']}/"
            f"{report['checks_total']} store checks",
        ))

        status = report["status"]
        states = {item["agent_id"]: item for item in status["agents"]}
        agents = fixture["agents"]
        checks.append(_check(
            "working_state_is_real",
            states[agents["idle"].agent_id]["working"] is True
            and states[agents["projection"].agent_id]["state"] == "projection"
            and states[agents["projection"].agent_id]["working"] is False,
            "idle works; the seed agent is projection and not working",
        ))
        checks.append(_check(
            "expired_lane_is_unknown",
            states[agents["expired"].agent_id]["state"] == "unknown"
            and states[agents["expired"].agent_id]["lease"]["state"] == "expired",
            f"state={states[agents['expired'].agent_id]['state']} "
            f"lease={states[agents['expired'].agent_id]['lease']['state']}",
        ))
        checks.append(_check(
            "all_states_are_declared",
            all(item["state"] in ALL_STATES for item in status["agents"]),
            f"states={sorted({item['state'] for item in status['agents']})}",
        ))
        checks.append(_check(
            "provision_skips_archived",
            any(item["agent_id"] == agents["archived"].agent_id
                for item in fixture["provisioned"]["skipped"]),
            f"skipped={fixture['provisioned']['skipped']}",
        ))

    with tempfile.TemporaryDirectory(prefix="qubit-workforce-planted-") as tmp:
        fixture = _plant_inconsistencies(Path(tmp) / "planted")
        report = verify_workforce_store(
            fixture["store"],
            fixture["tenant"].tenant_id,
            fixture["business"].business_id,
            now=now,
        )
        failed = {
            check["name"] for check in report["checks"] if not check["ok"]
        }
        checks.append(_check(
            "planted_inconsistency_detected",
            not report["ok"]
            and "no_wildcard_grants" in failed
            and "session_workspace_links" in failed,
            f"failed={sorted(failed)}",
        ))

    failed = [row for row in checks if not row["ok"]]
    return {
        "schema": "qubit-office-workforce-verification/1",
        "mode": "self-check",
        "ok": not failed,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks": checks,
    }


def run_existing_store(
    db: Path, tenant_id: str, business_id: str
) -> dict[str, Any]:
    uri = f"file:{db}?mode=ro"
    probe = sqlite3.connect(uri, uri=True)
    try:
        version = int(probe.execute("PRAGMA user_version").fetchone()[0])
    finally:
        probe.close()
    if version < SCHEMA_VERSION:
        raise RuntimeError(
            f"refusing: {db} is schema v{version}, current is v{SCHEMA_VERSION};"
            " opening it would migrate (a write)"
        )
    store = BusinessStore(db)
    report = verify_workforce_store(store, tenant_id, business_id)
    report["mode"] = "existing-store"
    report["db"] = str(db)
    report.pop("status", None)
    report.pop("reconcile", None)
    return report


def _print_human(report: dict[str, Any]) -> None:
    print(
        f"workforce verifier: {report.get('mode')} "
        f"{'PASS' if report['ok'] else 'FAIL'} "
        f"({report['checks_total'] - report['checks_failed']}/"
        f"{report['checks_total']})"
    )
    for row in report["checks"]:
        print(
            f"  [{'PASS' if row['ok'] else 'FAIL'}] {row['name']}:"
            f" {row['detail']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify P4-01 workforce lanes independently."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the full JSON report"
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="verify an existing store read-only (needs --tenant/--business)",
    )
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--business", default=None)
    args = parser.parse_args(argv)

    try:
        if args.db is not None:
            if not args.tenant or not args.business:
                parser.error("--db requires --tenant and --business")
            report = run_existing_store(args.db, args.tenant, args.business)
        else:
            report = run_self_check()
    except Exception as exc:  # noqa: BLE001 - exit 2 is the typed boundary
        payload = {
            "schema": "qubit-office-workforce-verification/1",
            "mode": "error",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"workforce verifier: ERROR {payload['error']}")
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
