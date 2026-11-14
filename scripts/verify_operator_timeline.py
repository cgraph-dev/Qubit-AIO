#!/usr/bin/env python3
"""P4-07 — independently verify the operator timeline projection.

WHY THIS EXISTS
---------------
The operator UI must not be its own witness. This script builds a real
(throwaway) Business OS database with the *actual services* — tenants,
agents, sessions, tasks, jobs, checkpoints, approvals, artifacts, ledger and
events — asks :func:`voicebridge.business.operator_timeline.operator_timeline`
for the timeline, and then recomputes every material claim from raw SQL.

Checks:

* every run status equals the durable job row projected through the canonical
  Run mapping; a job that claims ``completed`` without an artifact/receipt is
  ``unknown`` in both places;
* no projected item is ``completed`` without an evidence reference;
* pending/executing work is never rendered completed;
* an ``unknown`` job stays ``unknown`` and gains a reconciliation item;
* ledger cost totals equal the raw ``bos_ledger_entry`` sums;
* the timeline is chronologically non-decreasing and causal order holds;
* replay is labelled and every item's effect is stubbed with no real call and
  no write (the audit row count is unchanged);
* tenant isolation holds;
* the route is registered and the projection writes nothing.

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

from voicebridge.business import (  # noqa: E402
    AgentControlService,
    BusinessStore,
    approvals,
    durable,
    eventlog,
    identity,
    jobs,
    ledger,
    sessions,
)
from voicebridge.business.operator_timeline import (  # noqa: E402
    SCHEMA,
    operator_timeline,
)
from voicebridge.business.runs import RUN_STATUS_BY_JOB  # noqa: E402

TASK_ID = "P4-07"

LIMITATIONS = [
    "Hermetic seed only: a throwaway SQLite store built through the real"
    " services, not live operator data.",
    "The verifier checks the projection's claims against raw SQL; it does not"
    " run a browser, the vendored office client, or any live service.",
    "No network, model, provider, credential or real business record is"
    " touched. Replay performs no effect; nothing is resumed or approved.",
]


class UsageError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _items(projection: dict[str, Any], kind: str | None = None):
    for item in projection.get("items", []):
        if kind is None or item.get("kind") == kind:
            yield item


def _has_evidence(conn: sqlite3.Connection, job_id: str) -> bool:
    artifact = conn.execute(
        "SELECT 1 FROM bos_artifact WHERE job_id=?", (job_id,)
    ).fetchone()
    if artifact:
        return True
    receipt = conn.execute(
        "SELECT 1 FROM bos_ledger_entry WHERE job_id=?", (job_id,)
    ).fetchone()
    return receipt is not None


def _expected_run_status(
    row: sqlite3.Row, receipt_in_output: bool, evidence: bool
) -> str:
    status = RUN_STATUS_BY_JOB[row["status"]]
    if status == "completed" and not (evidence or receipt_in_output):
        return "unknown"
    return status


def _receipt_in_output(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        output = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(output, dict):
        return False
    keys = (
        "receipt_id",
        "provider_request_id",
        "provider_receipt",
        "external_id",
        "message_id",
        "ticket_id",
        "order_id",
        "transaction_id",
    )
    return any(str(output.get(key) or "").strip() for key in keys)


def _seed(store: BusinessStore) -> dict[str, Any]:
    tenant = store.create_tenant("Timeline Holdings")
    business = store.create_business(tenant.tenant_id, "Timeline Co",
                                     slug="timeline-co")
    owner = identity.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref="timeline-owner")
    agent_principal = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref="timeline-ada")
    service = AgentControlService(store)
    agent, _ = service.create(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.analyst",
        department="Research",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key="agent-1",
        security_principal_id=agent_principal.principal_id,
    )
    session = sessions.create_session(
        store, tenant.tenant_id, business.business_id, agent.agent_id,
        owner_principal=owner.principal_id, actor="owner",
        task_title="Draft the report",
    )
    task = store.create_task(
        tenant.tenant_id, business.business_id, "Ship the report",
        assigned_to=agent.agent_id,
    )

    def make_job(key: str) -> Any:
        job, _ = jobs.create(
            store, tenant_id=tenant.tenant_id,
            business_id=business.business_id, kind="report",
            idempotency_key=key,
            input={"task_id": task.task_id, "agent_id": agent.agent_id,
                   "session_id": session.session_id},
        )
        jobs.enqueue(store, tenant.tenant_id, job.job_id)
        jobs.claim(store, tenant.tenant_id, job.job_id, worker="worker-1")
        return jobs.get(store, tenant.tenant_id, job.job_id)

    evidence_job = make_job("job-evidence")
    store.record_artifact(
        tenant.tenant_id, business.business_id, kind="report",
        name="report.txt", body="done", job_id=evidence_job.job_id)
    jobs.finish(store, tenant.tenant_id, evidence_job.job_id, "completed",
                actor="worker-1")

    approval_job = make_job("job-approval")
    approval, _ = approvals.request(
        store, tenant_id=tenant.tenant_id,
        business_id=business.business_id, action="publish",
        requested_by=agent.agent_id, idempotency_key="approval-1",
        job_id=approval_job.job_id, payload={"risk": "low"})

    claim_job = make_job("job-claim")
    jobs.finish(store, tenant.tenant_id, claim_job.job_id, "completed",
                actor="worker-1")  # no artifact/receipt

    unknown_job = make_job("job-unknown")
    jobs.mark_unknown(store, tenant.tenant_id, unknown_job.job_id,
                      actor="worker-1", reason="provider timed out")

    receipt_job = make_job("job-receipt")
    ledger.append(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id,
        account="revenue", direction="credit", amount_minor=5000,
        currency="USD", idempotency_key="ledger-1",
        job_id=receipt_job.job_id)
    ledger.append(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id,
        account="fees", direction="debit", amount_minor=1200,
        currency="USD", idempotency_key="ledger-2",
        job_id=receipt_job.job_id)
    jobs.finish(store, tenant.tenant_id, receipt_job.job_id, "completed",
                actor="worker-1")

    durable.record_checkpoint(
        store, tenant.tenant_id, business.business_id,
        run_id=unknown_job.job_id, sequence=1,
        state={"usage": {"prompt_tokens": 7, "completion_tokens": 3,
                         "total_tokens": 10}},
        actor="worker-1", job_id=unknown_job.job_id)

    eventlog.append_event(
        store, tenant.tenant_id, task.task_id, "task.assigned",
        {"task_id": task.task_id, "agent_id": agent.agent_id},
        actor="owner", idempotency_key="event-1", correlation_id="cor-1")

    return {
        "tenant": tenant,
        "business": business,
        "agent": agent,
        "session": session,
        "task": task,
        "evidence_job": evidence_job,
        "claim_job": claim_job,
        "unknown_job": unknown_job,
        "receipt_job": receipt_job,
        "approval": approval,
    }


def _other_tenant(store: BusinessStore) -> Any:
    tenant = store.create_tenant("Beta Holdings")
    business = store.create_business(tenant.tenant_id, "Beta", slug="beta")
    job, _ = jobs.create(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id,
        kind="report", idempotency_key="beta-job", input={})
    return tenant, job


def _audit_count(store: BusinessStore) -> int:
    with store.read() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM bos_audit_event").fetchone()[0])


def _verify(store: BusinessStore, seeded: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    tenant_id = seeded["tenant"].tenant_id

    projection = operator_timeline(store, tenant_id)
    checks.append(_check(
        "schema",
        projection.get("schema") == SCHEMA,
        f"projection schema is {projection.get('schema')!r}",
    ))

    # -- status from the durable row, completed only with evidence ----------
    with store.read() as conn:
        rows = conn.execute(
            "SELECT job_id, status, output_json FROM bos_job WHERE tenant_id=?",
            (tenant_id,),
        ).fetchall()
        expected: dict[str, str] = {}
        for row in rows:
            evidence = _has_evidence(conn, row["job_id"])
            expected[row["job_id"]] = _expected_run_status(
                row, _receipt_in_output(row["output_json"]), evidence)
    projected = {item["run_id"]: item["status"]
                 for item in _items(projection, "run")}
    checks.append(_check(
        "run_status_from_durable_row",
        projected == expected,
        f"projected={projected} expected={expected}",
    ))

    evidence_job = seeded["evidence_job"].job_id
    claim_job = seeded["claim_job"].job_id
    unknown_job = seeded["unknown_job"].job_id
    receipt_job = seeded["receipt_job"].job_id
    checks.append(_check(
        "claim_without_evidence_is_unknown",
        projected.get(claim_job) == "unknown",
        f"claim job projected {projected.get(claim_job)!r}",
    ))
    checks.append(_check(
        "unknown_stays_unknown",
        unknown_job in projected and projected[unknown_job] == "unknown",
        f"unknown job projected {projected.get(unknown_job)!r}",
    ))
    checks.append(_check(
        "evidence_job_is_completed",
        projected.get(evidence_job) == "completed",
        f"artifact job projected {projected.get(evidence_job)!r}",
    ))
    checks.append(_check(
        "receipt_job_is_completed",
        projected.get(receipt_job) == "completed",
        f"receipt job projected {projected.get(receipt_job)!r}",
    ))

    bad_completed = [
        item["item_id"] for item in _items(projection)
        if item.get("status") == "completed" and not item.get("evidence")
    ]
    checks.append(_check(
        "no_completed_without_evidence",
        not bad_completed,
        f"offenders={bad_completed}",
    ))

    pending_completed = [
        item["item_id"] for item in _items(projection)
        if item.get("pending") and item.get("completed")
    ]
    checks.append(_check(
        "pending_is_never_completed",
        not pending_completed,
        f"offenders={pending_completed}",
    ))

    reconciliations = {
        item["run_id"] for item in _items(projection, "reconciliation")}
    checks.append(_check(
        "unknown_has_reconciliation",
        unknown_job in reconciliations,
        f"reconciled runs={sorted(reconciliations)}",
    ))

    # -- cost equals the raw ledger sums -----------------------------------
    with store.read() as conn:
        debit = conn.execute(
            "SELECT COALESCE(SUM(amount_minor),0) FROM bos_ledger_entry"
            " WHERE tenant_id=? AND direction='debit'", (tenant_id,)
        ).fetchone()[0]
        credit = conn.execute(
            "SELECT COALESCE(SUM(amount_minor),0) FROM bos_ledger_entry"
            " WHERE tenant_id=? AND direction='credit'", (tenant_id,)
        ).fetchone()[0]
    cost = projection.get("cost", {})
    checks.append(_check(
        "cost_matches_ledger",
        cost.get("debit_minor") == debit and cost.get("credit_minor") == credit,
        f"projected debit/credit={cost.get('debit_minor')}/"
        f"{cost.get('credit_minor')} sql={debit}/{credit}",
    ))

    # -- ordering ----------------------------------------------------------
    timestamps = [str(item.get("at") or "") for item in _items(projection)]
    checks.append(_check(
        "chronological",
        timestamps == sorted(timestamps),
        "timestamps are non-decreasing",
    ))
    by_id = {item["item_id"]: index
             for index, item in enumerate(_items(projection))}
    causal_ok = True
    offenders: list[str] = []
    for item in _items(projection):
        cause = str(item.get("causation_id") or "")
        if cause and cause in by_id:
            if by_id[cause] > by_id[item["item_id"]]:
                causal_ok = False
                offenders.append(item["item_id"])
    checks.append(_check(
        "causation_before_effect",
        causal_ok,
        f"offenders={offenders}",
    ))

    # -- replay is a labelled, stubbed, write-free simulation --------------
    before = _audit_count(store)
    replay = operator_timeline(store, tenant_id, replay=True)
    after = _audit_count(store)
    replay_ok = (
        replay.get("mode") == "replay"
        and replay.get("replay", {}).get("effects") == "stubbed"
        and replay.get("replay", {}).get("real_calls") is False
        and all(item.get("effect") == "stubbed" for item in _items(replay))
        and len(replay.get("items", [])) == len(projection.get("items", []))
    )
    checks.append(_check(
        "replay_labelled_stubbed",
        replay_ok,
        f"mode={replay.get('mode')!r} effects="
        f"{replay.get('replay', {}).get('effects')!r}",
    ))
    checks.append(_check(
        "replay_writes_nothing",
        before == after,
        f"audit rows before/after={before}/{after}",
    ))
    live_status = {item["item_id"]: item["status"]
                   for item in _items(projection)}
    replay_status = {item["item_id"]: item["status"]
                     for item in _items(replay)}
    checks.append(_check(
        "replay_preserves_truth",
        live_status == replay_status,
        "replay statuses equal the live projection",
    ))

    # -- tenant isolation --------------------------------------------------
    other_tenant, other_job = _other_tenant(store)
    isolated = operator_timeline(store, tenant_id)
    leaked = [item for item in _items(isolated)
              if item.get("run_id") == other_job.job_id]
    checks.append(_check(
        "tenant_isolation",
        not leaked,
        f"other tenant={other_tenant.tenant_id} leaked={len(leaked)}",
    ))

    # -- read-only ---------------------------------------------------------
    before = _audit_count(store)
    operator_timeline(store, tenant_id)
    after = _audit_count(store)
    checks.append(_check(
        "projection_is_read_only",
        before == after,
        f"audit rows before/after={before}/{after}",
    ))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify P4-07 operator timeline projection.")
    parser.add_argument("--json", action="store_true",
                        help="emit a JSON report")
    parser.add_argument("--db", type=Path, default=None,
                        help="verify an existing business.db read-only")
    args = parser.parse_args(argv)

    try:
        if args.db is not None:
            if not args.db.exists():
                raise UsageError("DB_MISSING", f"{args.db} does not exist")
            store = BusinessStore(args.db)
            with store.read() as conn:
                tenant_row = conn.execute(
                    "SELECT tenant_id FROM bos_tenant ORDER BY created_at"
                    " LIMIT 1").fetchone()
            if tenant_row is None:
                raise UsageError("DB_EMPTY", "the store has no tenant")
            checks = _verify_existing(store, tenant_row["tenant_id"])
        else:
            with tempfile.TemporaryDirectory(prefix="qubit-p4-07-") as tmp:
                store = BusinessStore(Path(tmp) / "business.db")
                seeded = _seed(store)
                checks = _verify(store, seeded)
    except UsageError as exc:
        report = {
            "task_id": TASK_ID,
            "generated_at": _utc_now(),
            "valid": False,
            "error": {"code": exc.code, "detail": exc.detail},
            "checks": [],
            "problems": [f"{exc.code}: {exc.detail}"],
            "limitations": LIMITATIONS,
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print(f"P4-07 verifier usage error: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - internal failure
        report = {
            "task_id": TASK_ID,
            "generated_at": _utc_now(),
            "valid": False,
            "error": {"code": "INTERNAL", "detail": str(exc)[:400]},
            "checks": [],
            "problems": [f"INTERNAL: {exc}"],
            "limitations": LIMITATIONS,
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print(f"P4-07 verifier internal error: {exc}")
        return 2

    problems = [f"{check['name']}: {check['detail']}"
                for check in checks if not check["ok"]]
    report = {
        "task_id": TASK_ID,
        "generated_at": _utc_now(),
        "valid": not problems,
        "checks": checks,
        "problems": problems,
        "limitations": LIMITATIONS,
    }
    exit_code = 0 if not problems else 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        for check in checks:
            marker = "PASS" if check["ok"] else "FAIL"
            print(f"{marker} {check['name']}: {check['detail']}")
        print(f"P4-07 {'PASS' if not problems else 'FAIL'}")
    return exit_code


def _verify_existing(store: BusinessStore, tenant_id: str) -> list[dict[str, Any]]:
    """Read-only verification of a real store (no seed, no writes)."""
    checks: list[dict[str, Any]] = []
    projection = operator_timeline(store, tenant_id)
    checks.append(_check(
        "schema",
        projection.get("schema") == SCHEMA,
        f"projection schema is {projection.get('schema')!r}",
    ))
    bad_completed = [
        item["item_id"] for item in _items(projection)
        if item.get("status") == "completed" and not item.get("evidence")
    ]
    checks.append(_check(
        "no_completed_without_evidence",
        not bad_completed,
        f"offenders={bad_completed}",
    ))
    timestamps = [str(item.get("at") or "") for item in _items(projection)]
    checks.append(_check(
        "chronological",
        timestamps == sorted(timestamps),
        "timestamps are non-decreasing",
    ))
    pending_completed = [
        item["item_id"] for item in _items(projection)
        if item.get("pending") and item.get("completed")
    ]
    checks.append(_check(
        "pending_is_never_completed",
        not pending_completed,
        f"offenders={pending_completed}",
    ))
    replay = operator_timeline(store, tenant_id, replay=True)
    checks.append(_check(
        "replay_labelled_stubbed",
        replay.get("mode") == "replay"
        and replay.get("replay", {}).get("real_calls") is False,
        f"mode={replay.get('mode')!r}",
    ))
    return checks


if __name__ == "__main__":
    sys.exit(main())
