#!/usr/bin/env python3
"""Independent verifier for the P2-07 effective-state projection.

WHY THIS EXISTS
---------------
The panel must not be its own witness. This script builds a real (throwaway)
Business OS database with the *actual services* — agent control, identity,
runs/leases, approvals, usage — asks the projection for one agent, and then
recomputes every claim from the source tables and the real identity evaluator.
It never compares the projection to itself.

Checks:
* explicit vs inherited grant counts (raw SQL + role JSON);
* effective capabilities: every projected allow is re-decided by
  ``identity.evaluate``; the projected set equals the independently computed
  set; deny-wins is observed;
* an expired grant is not effective; a revoked grant is not effective;
* an expired lease is not active; an expired approval is not pending;
* spend totals equal the usage rows;
* audit rows exist and are bounded;
* a cross-tenant read cannot see the agent;
* absent data is a typed ``unknown`` with a reason (memory/session).

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
from voicebridge.business import agents as AGENTS  # noqa: E402
from voicebridge.business import approvals as APPROVALS  # noqa: E402
from voicebridge.business import identity as IDENTITY  # noqa: E402
from voicebridge.business import runs as RUNS  # noqa: E402
from voicebridge.business.effective_state import effective_agent_state  # noqa: E402
from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.schema import SCHEMA_VERSION  # noqa: E402


def _key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("capability") or ""),
        str(row.get("resource") or ""),
        str(row.get("action") or ""),
        str(row.get("data_class") or ""),
        str(row.get("environment") or ""),
    )


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _sql(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    return conn.execute(sql, params).fetchone()


def _subject_ids(conn: sqlite3.Connection, tenant_id: str,
                 agent_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT principal_id FROM bos_principal WHERE tenant_id=?"
        " AND external_ref=?", (tenant_id, agent_id)).fetchall()
    ids = [row[0] for row in rows]
    direct = conn.execute(
        "SELECT DISTINCT subject_id FROM bos_grant WHERE tenant_id=?"
        " AND subject_id=?", (tenant_id, agent_id)).fetchall()
    for row in direct:
        if row[0] not in ids:
            ids.append(row[0])
    return ids


def _expected_candidates(conn: sqlite3.Connection, tenant_id: str,
                         subject_ids: list[str]) -> list[dict[str, Any]]:
    if not subject_ids:
        return []
    marks = ",".join("?" for _ in subject_ids)
    rows = conn.execute(
        "SELECT * FROM bos_grant WHERE tenant_id=? AND subject_id IN"
        f" ({marks})", (tenant_id, *subject_ids)).fetchall()
    out: list[dict[str, Any]] = []
    now = time.time()
    for row in rows:
        if row["revoked_at"] is not None:
            continue
        if row["expires_at"] is not None and float(row["expires_at"]) <= now:
            continue
        out.append({
            "capability": row["capability"],
            "resource": row["resource"],
            "action": row["action"],
            "data_class": row["data_class"],
            "environment": row["environment"],
            "effect": row["effect"],
        })
        if row["role_id"]:
            role = conn.execute(
                "SELECT permissions_json FROM bos_role WHERE tenant_id=?"
                " AND role_id=?", (tenant_id, row["role_id"])).fetchone()
            if role is None:
                continue
            try:
                permissions = json.loads(role[0] or "[]")
            except (TypeError, ValueError):
                permissions = []
            for item in permissions:
                if not isinstance(item, dict):
                    continue
                out.append({
                    "capability": item.get("capability"),
                    "resource": item.get("resource", row["resource"]),
                    "action": item.get("action", row["action"]),
                    "data_class": item.get("data_class", row["data_class"]),
                    "environment": item.get(
                        "environment", row["environment"]),
                    "effect": item.get("effect", "allow"),
                })
    return out


def _expected_effective(
    store: BusinessStore,
    conn: sqlite3.Connection,
    tenant_id: str,
    agent: AGENTS.Agent,
    subject_ids: list[str],
) -> set[tuple[str, str, str, str, str]]:
    """Recompute what is effective using the real evaluator + profile filter."""
    effective: set[tuple[str, str, str, str, str]] = set()
    permissions = agent.permissions.to_dict()
    for candidate in _expected_candidates(conn, tenant_id, subject_ids):
        if not candidate.get("capability"):
            continue
        key = _key(candidate)
        decided = False
        for subject in subject_ids:
            try:
                decision = IDENTITY.evaluate(
                    store, tenant_id, subject,
                    key[0], key[1], key[2], key[3], key[4])
            except BusinessError:
                continue
            if decision.allowed:
                decided = True
                break
        if not decided:
            continue
        capability, resource, action, _, _ = key
        if capability in AGENTS.PERMISSION_CATEGORIES:
            selected = [
                scope for scope in permissions.get(capability, [])
                if scope == action or scope == resource
            ]
            if not selected:
                continue
        effective.add(key)
    return effective


def _seed_fixture(root: Path) -> dict[str, Any]:
    store = BusinessStore(root / "business.db")
    tenant = store.create_tenant("Verify Holdings")
    business = store.create_business(
        tenant.tenant_id, "Verify", slug="verify")
    service = AgentControlService(store)
    agent, _created = service.create(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.verify",
        department="Quality",
        display_name="Verifier Agent",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        fallback_provider="provider-b",
        fallback_model="model-b",
        idempotency_key="verify-create",
        permissions={"social": ["read", "draft"], "files": ["/tmp/verify"]},
        safety={
            "autonomy": "L2",
            "daily_spend_minor": 1000,
            "monthly_spend_minor": 5000,
            "per_action_limit_minor": 400,
        },
        metadata={"source": "office_plan_building"},
    )
    principal = IDENTITY.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref=agent.agent_id)
    role = IDENTITY.create_role(
        store, tenant.tenant_id, "verify-role",
        [{"capability": "files", "resource": "/tmp/verify", "action": "read"}],
    )
    explicit = IDENTITY.grant(
        store, tenant.tenant_id, subject_id=principal.principal_id,
        capability="social", resource="feed", action="read")
    deny = IDENTITY.grant(
        store, tenant.tenant_id, subject_id=principal.principal_id,
        capability="social", resource="feed", action="draft", effect="deny")
    # The grant itself names ``toolsets``; the role contributes the actual
    # ``files``/``read`` capability, so that row is inherited, not explicit.
    inherited = IDENTITY.grant(
        store, tenant.tenant_id, subject_id=principal.principal_id,
        capability="toolsets", resource="office", action="use",
        role_id=role.role_id)
    expired = IDENTITY.grant(
        store, tenant.tenant_id, subject_id=principal.principal_id,
        capability="email", resource="inbox", action="send",
        expires_at=time.time() - 30)
    revoked = IDENTITY.grant(
        store, tenant.tenant_id, subject_id=principal.principal_id,
        capability="payments", resource="card", action="charge")
    IDENTITY.revoke_grant(
        store, tenant.tenant_id, revoked.grant_id, actor="owner")

    task = RUNS.create_task(
        store, tenant.tenant_id, business.business_id, "Verify work",
        assignee_principal=principal.principal_id)
    lease = RUNS.acquire_lease(
        store, tenant.tenant_id, "task", task.task_id,
        holder=principal.principal_id, ttl_seconds=1,
        now=time.time() - 100)
    RUNS.create_workspace(
        store, tenant.tenant_id, task_id=task.task_id,
        session_id=principal.principal_id)

    approval = APPROVALS.request(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id,
        action="publish", requested_by=principal.principal_id,
        idempotency_key="verify-approval",
        payload={"agent_id": agent.agent_id},
        expires_at=time.time() - 10)[0]
    pending = APPROVALS.request(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id,
        action="contact", requested_by=principal.principal_id,
        idempotency_key="verify-pending",
        payload={"agent_id": agent.agent_id})[0]

    service.record_usage(
        tenant.tenant_id, agent.agent_id,
        idempotency_key="verify-usage", cost_minor=300, tokens=42)
    store.set_budget(
        tenant.tenant_id, business.business_id,
        currency=business.currency,
        period=time.strftime("%Y-%m"), limit_minor=2000)

    other_tenant = store.create_tenant("Other")
    other_business = store.create_business(
        other_tenant.tenant_id, "Other", slug="other")
    other_agent, _ = service.create(
        tenant_id=other_tenant.tenant_id,
        business_id=other_business.business_id,
        role_id="role.other", department="Other",
        display_name="Other Agent", human_owner="owner",
        provider="provider-a", model="model-a",
        idempotency_key="verify-other",
    )
    return {
        "store": store,
        "tenant": tenant,
        "business": business,
        "agent": agent,
        "principal": principal,
        "grants": {
            "explicit": explicit,
            "deny": deny,
            "inherited": inherited,
            "expired": expired,
            "revoked": revoked,
        },
        "lease": lease,
        "approval": approval,
        "pending": pending,
        "other": {"tenant": other_tenant, "agent": other_agent},
    }


def run_self_check(now: float | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="qubit-effective-") as tmp:
        fx = _seed_fixture(Path(tmp))
        store: BusinessStore = fx["store"]
        tenant = fx["tenant"]
        agent = fx["agent"]
        projection = effective_agent_state(
            store, tenant.tenant_id, agent.agent_id, now=now)

        conn = sqlite3.connect(store.path)
        conn.row_factory = sqlite3.Row
        try:
            subject_ids = _subject_ids(conn, tenant.tenant_id, agent.agent_id)
            total_grants = int(_sql(
                conn,
                "SELECT COUNT(*) FROM bos_grant WHERE tenant_id=?"
                " AND subject_id IN (" + ",".join("?" for _ in subject_ids)
                + ")", (tenant.tenant_id, *subject_ids))[0]) if subject_ids else 0
            inherited_expected = 0
            if subject_ids:
                rows = conn.execute(
                    "SELECT role_id FROM bos_grant WHERE tenant_id=?"
                    " AND subject_id IN ("
                    + ",".join("?" for _ in subject_ids) + ")",
                    (tenant.tenant_id, *subject_ids)).fetchall()
                for row in rows:
                    if not row[0]:
                        continue
                    role = conn.execute(
                        "SELECT permissions_json FROM bos_role"
                        " WHERE tenant_id=? AND role_id=?",
                        (tenant.tenant_id, row[0])).fetchone()
                    if role:
                        try:
                            inherited_expected += len(
                                json.loads(role[0] or "[]"))
                        except ValueError:
                            pass

            explicit_count = len(projection["identity"]["explicit"])
            inherited_count = len(projection["identity"]["inherited"])
            checks.append(_check(
                "explicit_grant_count",
                explicit_count == total_grants,
                f"projection={explicit_count} sql={total_grants}",
            ))
            checks.append(_check(
                "inherited_grant_count",
                inherited_count == inherited_expected,
                f"projection={inherited_count} role_json={inherited_expected}",
            ))

            projected = {_key(row) for row in projection["capabilities"]["effective"]}
            expected = _expected_effective(
                store, conn, tenant.tenant_id, agent, subject_ids)
            checks.append(_check(
                "effective_set_matches_evaluator",
                projected == expected,
                f"projection={sorted(projected)} expected={sorted(expected)}",
            ))

            allow_ok = True
            for row in projection["capabilities"]["effective"]:
                key = _key(row)
                decisions = [
                    IDENTITY.evaluate(
                        store, tenant.tenant_id, subject,
                        key[0], key[1], key[2], key[3], key[4])
                    for subject in subject_ids
                ] if subject_ids else []
                if not any(item.allowed for item in decisions):
                    allow_ok = False
                    break
            checks.append(_check(
                "every_projected_allow_is_authorized", allow_ok,
                "each projected effective capability re-decided by "
                "identity.evaluate",
            ))

            deny_row = next((
                row for row in projection["capabilities"]["denied"]
                if _key(row) == _key({
                    "capability": "social", "resource": "feed",
                    "action": "draft", "data_class": "internal",
                    "environment": "production",
                })
            ), None)
            checks.append(_check(
                "deny_wins",
                bool(deny_row and "deny_grant" in deny_row.get("reasons", [])),
                f"social/draft={deny_row}",
            ))

            expired_key = _key({
                "capability": "email", "resource": "inbox", "action": "send",
                "data_class": "internal", "environment": "production",
            })
            revoked_key = _key({
                "capability": "payments", "resource": "card",
                "action": "charge", "data_class": "internal",
                "environment": "production",
            })
            denied_keys = {
                _key(row) for row in projection["capabilities"]["denied"]}
            expired_ids = {
                row["grant_id"] for row in projection["identity"]["expired"]}
            revoked_ids = {
                row["grant_id"] for row in projection["identity"]["revoked"]}
            checks.append(_check(
                "expired_grant_not_effective",
                expired_key in denied_keys
                and fx["grants"]["expired"].grant_id in expired_ids,
                f"denied={expired_key in denied_keys} "
                f"listed_expired={fx['grants']['expired'].grant_id in expired_ids}",
            ))
            checks.append(_check(
                "revoked_grant_not_effective",
                revoked_key in denied_keys
                and fx["grants"]["revoked"].grant_id in revoked_ids,
                f"denied={revoked_key in denied_keys} "
                f"listed_revoked={fx['grants']['revoked'].grant_id in revoked_ids}",
            ))

            lease_rows = conn.execute(
                "SELECT lease_id, expires_at FROM bos_lease WHERE tenant_id=?"
                " AND holder=? AND released_at IS NULL",
                (tenant.tenant_id, fx["principal"].principal_id)).fetchall()
            expected_expired = {
                row[0] for row in lease_rows
                if float(row[1]) <= (time.time() if now is None else now)
            }
            projected_expired = {
                row["lease_id"] for row in projection["leases"]["expired"]}
            checks.append(_check(
                "expired_lease_not_active",
                projected_expired == expected_expired
                and not projection["leases"]["active"],
                f"projected={sorted(projected_expired)} "
                f"sql={sorted(expected_expired)}",
            ))

            expired_approval = projection["approvals"]["expired"]
            checks.append(_check(
                "expired_approval_not_pending",
                any(row["approval_id"] == fx["approval"].approval_id
                    for row in expired_approval)
                and not any(row["approval_id"] == fx["approval"].approval_id
                            for row in projection["approvals"]["pending"]),
                f"expired={[r['approval_id'] for r in expired_approval]}",
            ))
            checks.append(_check(
                "pending_approval_is_pending",
                any(row["approval_id"] == fx["pending"].approval_id
                    for row in projection["approvals"]["pending"]),
                f"pending={fx['pending'].approval_id}",
            ))

            month = time.strftime("%Y-%m")
            spent = int(_sql(
                conn,
                "SELECT COALESCE(SUM(cost_minor),0) FROM bos_agent_usage"
                " WHERE tenant_id=? AND agent_id=? AND month=?",
                (tenant.tenant_id, agent.agent_id, month))[0])
            checks.append(_check(
                "budget_spend_matches_rows",
                projection["budget"]["spend"]["month_cost_minor"] == spent,
                f"projection={projection['budget']['spend']['month_cost_minor']} "
                f"sql={spent}",
            ))

            audit_rows = int(_sql(
                conn,
                "SELECT COUNT(*) FROM bos_audit_event WHERE tenant_id=?"
                " AND resource_type='agent' AND resource_id=?",
                (tenant.tenant_id, agent.agent_id))[0])
            checks.append(_check(
                "audit_rows_are_real_and_bounded",
                audit_rows > 0
                and 0 < len(projection["audit"]["events"]) <= 25,
                f"sql_agent_rows={audit_rows} "
                f"projection={len(projection['audit']['events'])}",
            ))

            other = fx["other"]
            blocked = False
            try:
                effective_agent_state(
                    store, other["tenant"].tenant_id, agent.agent_id)
            except BusinessError as exc:
                blocked = exc.code == "AGENT_NOT_FOUND"
            other_projection = effective_agent_state(
                store, other["tenant"].tenant_id, other["agent"].agent_id)
            other_grant_ids = {
                row["grant_id"]
                for row in other_projection["identity"]["explicit"]
            }
            leaked = other_grant_ids & {
                fx["grants"][name].grant_id for name in fx["grants"]}
            checks.append(_check(
                "tenant_isolation",
                blocked and not leaked,
                f"cross_tenant_blocked={blocked} leaked={sorted(leaked)}",
            ))

            checks.append(_check(
                "absent_is_typed_unknown",
                projection["memory"]["value"] is None
                and bool(projection["memory"].get("reason"))
                and projection["links"]["sessions"]["value"] is None
                and bool(projection["links"]["sessions"].get("reason")),
                "memory scopes and durable sessions carry None + reason",
            ))
            checks.append(_check(
                "status_is_projection_not_live",
                projection["status"]["value"] == "projection",
                f"status={projection['status']['value']}",
            ))
            checks.append(_check(
                "model_route_and_fallback",
                projection["model"]["primary"]["value"]["route"]
                == f"{agent.provider}/{agent.model}"
                and projection["model"]["fallback"]["value"]["route"]
                == f"{agent.fallback_provider}/{agent.fallback_model}",
                f"primary={projection['model']['primary']['value']} "
                f"fallback={projection['model']['fallback']['value']}",
            ))
        finally:
            conn.close()

    failed = [row for row in checks if not row["ok"]]
    return {
        "schema": "qubit-office-effective-state-verification/1",
        "mode": "self-check",
        "ok": not failed,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks": checks,
    }


def run_existing_store(db: Path, tenant_id: str, agent_id: str) -> dict[str, Any]:
    """Read-only verification against an existing store at the current schema."""
    uri = f"file:{db}?mode=ro"
    probe = sqlite3.connect(uri, uri=True)
    try:
        version = int(probe.execute("PRAGMA user_version").fetchone()[0])
    finally:
        probe.close()
    if version < SCHEMA_VERSION:
        raise RuntimeError(
            f"refusing: {db} is schema v{version}, current is v{SCHEMA_VERSION}; "
            "opening it would migrate (a write)")
    store = BusinessStore(db)
    projection = effective_agent_state(store, tenant_id, agent_id)
    checks: list[dict[str, Any]] = []
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        subject_ids = _subject_ids(conn, tenant_id, agent_id)
        projected = {_key(row) for row in projection["capabilities"]["effective"]}
        allow_ok = True
        for row in projection["capabilities"]["effective"]:
            key = _key(row)
            decisions = [
                IDENTITY.evaluate(
                    store, tenant_id, subject, key[0], key[1], key[2],
                    key[3], key[4])
                for subject in subject_ids
            ] if subject_ids else []
            if not any(item.allowed for item in decisions):
                allow_ok = False
                break
        checks.append(_check(
            "every_projected_allow_is_authorized", allow_ok,
            f"{len(projected)} effective capabilities re-decided"))
        now = time.time()
        projected_expired = {
            row["lease_id"] for row in projection["leases"]["expired"]}
        active_ids = {
            row["lease_id"] for row in projection["leases"]["active"]}
        checks.append(_check(
            "no_expired_lease_in_active",
            not (projected_expired & active_ids),
            f"expired={len(projected_expired)} active={len(active_ids)}"))
        checks.append(_check(
            "no_revoked_grant_effective",
            not any(
                row.get("reasons") and "grant_revoked" in row["reasons"]
                for row in projection["capabilities"]["effective"]),
            "effective rows carry no grant_revoked reason"))
        month = time.strftime("%Y-%m")
        spent = int(_sql(
            conn,
            "SELECT COALESCE(SUM(cost_minor),0) FROM bos_agent_usage"
            " WHERE tenant_id=? AND agent_id=? AND month=?",
            (tenant_id, agent_id, month))[0])
        checks.append(_check(
            "budget_spend_matches_rows",
            projection["budget"]["spend"]["month_cost_minor"] == spent,
            f"projection={projection['budget']['spend']['month_cost_minor']} "
            f"sql={spent}"))
    finally:
        conn.close()
    failed = [row for row in checks if not row["ok"]]
    return {
        "schema": "qubit-office-effective-state-verification/1",
        "mode": "existing-store",
        "db": str(db),
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "ok": not failed,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks": checks,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"effective-state verifier: {report.get('mode')} "
          f"{'PASS' if report['ok'] else 'FAIL'} "
          f"({report['checks_total'] - report['checks_failed']}/"
          f"{report['checks_total']})")
    for row in report["checks"]:
        print(f"  [{'PASS' if row['ok'] else 'FAIL'}] {row['name']}: "
              f"{row['detail']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the P2-07 effective-state projection independently.")
    parser.add_argument("--json", action="store_true",
                        help="emit the report as JSON")
    parser.add_argument("--db", type=Path,
                        help="verify an existing business.db (read-only)")
    parser.add_argument("--tenant", help="tenant id for --db")
    parser.add_argument("--agent", help="agent id for --db")
    args = parser.parse_args(argv)
    try:
        if args.db is not None:
            if not args.tenant or not args.agent:
                parser.error("--db requires --tenant and --agent")
            if not args.db.is_file():
                parser.error(f"no such database: {args.db}")
            report = run_existing_store(args.db, args.tenant, args.agent)
        else:
            if args.tenant or args.agent:
                parser.error("--tenant/--agent require --db")
            report = run_self_check()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({
                "schema": "qubit-office-effective-state-verification/1",
                "ok": False,
                "mode": "self-check" if args.db is None else "existing-store",
                "error": f"{type(exc).__name__}: {exc}",
            }, indent=2, sort_keys=True))
        else:
            print(f"effective-state verifier: ERROR {type(exc).__name__}: {exc}",
                  file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
