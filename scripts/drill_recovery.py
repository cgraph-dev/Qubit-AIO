#!/usr/bin/env python3
"""P8-03 disaster-recovery drill over a throwaway Business OS database.

It builds a synthetic tenant (jobs, approvals, ledger, checkpoints, outbox and
an event-log index), takes an encrypted snapshot, verifies it, restores it to a
**new** path, replays the audit/workflow/event records, measures RPO/RTO,
demonstrates crash/unknown recovery, tampers a copied snapshot to prove the
refusal, runs the out-of-band kill-switch drill, records and resolves an
incident, and exercises deployment-ring promotion, canary and rollback.

Hermetic: everything lives under a temporary directory (or ``--workdir``); no
network, no live service, no credentials, and no live operator database is read
or written.

Exit codes:
    0  every check passed
    1  a check failed (the JSON report says which)
    2  usage / internal error
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business import (  # noqa: E402
    BusinessStore,
    approvals,
    backup,
    durable,
    eventlog,
    jobs,
    ledger,
)
from voicebridge.business import disaster_recovery as dr  # noqa: E402
from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.util import now_iso  # noqa: E402

DRILL_SCHEMA = "qubit-office-drill/1"
EXPIRED = 1000.0

LIMITATIONS: List[str] = [
    "all state is a throwaway SQLite database under the drill workdir; no live"
    " service, credential or operator database is touched",
    "RPO is the age of the newest committed record when the snapshot started;"
    " a one-shot snapshot does not bound loss after it",
    "RTO is measured on this host for the seeded fixture; it is not an SLA and"
    " does not model a cold machine or remote storage",
    "deployment-ring state is in-memory per controller; the durable record is"
    " the audit trail written by the drill",
    "the kill-switch stoppers are injected drill callables; the leases stopper"
    " is the shipped Business OS lease release",
    "provider cancellation is represented by the injected egress stopper; no"
    " real provider is contacted",
]


def _check(name: str, ok: bool, detail: Any) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _refusal(call, code: str) -> tuple[bool, str]:
    try:
        call()
    except BusinessError as exc:
        return exc.code == code, exc.code
    return False, "no refusal"


def _seed(workdir: Path):
    store = BusinessStore(workdir / "live" / "business.db")
    tenant = store.create_tenant("Drill Holdings")
    business = store.create_business(tenant.tenant_id, "Drill", slug="drill")
    store.create_contact(
        tenant.tenant_id,
        business.business_id,
        kind="customer",
        display_name="Drill Customer",
        email="buyer@drill.test",
    )
    store.create_task(tenant.tenant_id, business.business_id, "Reconcile invoices")

    done, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="send-invoice",
        idempotency_key="drill-job-done",
        input={"invoice": 7},
    )
    jobs.enqueue(store, tenant.tenant_id, done.job_id)
    jobs.claim(
        store,
        tenant.tenant_id,
        done.job_id,
        worker="worker-done",
        lease_seconds=60.0,
    )
    jobs.finish(
        store,
        tenant.tenant_id,
        done.job_id,
        "completed",
        actor="worker-done",
        output={"receipt": "synthetic"},
    )

    running, _ = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="sync-crm",
        idempotency_key="drill-job-running",
        input={"account": "crm-1"},
    )
    jobs.enqueue(store, tenant.tenant_id, running.job_id)
    jobs.claim(
        store,
        tenant.tenant_id,
        running.job_id,
        worker="worker-a",
        lease_seconds=10.0,
        now=EXPIRED,
    )

    approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action="send-invoice",
        requested_by="owner",
        idempotency_key="drill-approval",
        payload={"invoice": 7},
        expires_at=10**12,
    )

    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="cash",
        direction="credit",
        amount_minor=1250,
        currency="USD",
        idempotency_key="drill-ledger-credit",
        job_id=done.job_id,
    )
    ledger.append(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        account="fees",
        direction="debit",
        amount_minor=250,
        currency="USD",
        idempotency_key="drill-ledger-debit",
    )

    for sequence in (1, 2, 3):
        durable.record_checkpoint(
            store,
            tenant.tenant_id,
            business.business_id,
            "drill-run-1",
            sequence,
            state={"step": sequence},
            workspace_revision=f"ws-{sequence}",
            policy_revision="pol-1",
            actor="drill",
        )

    durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        "drill-aggregate-pending",
        "email",
        {"to": "customer@example.test"},
        idempotency_key="drill-outbox-pending",
        actor="drill",
    )
    claimed = durable.enqueue_outbox(
        store,
        tenant.tenant_id,
        business.business_id,
        "drill-aggregate-claimed",
        "notify-crm",
        {"invoice": 7},
        idempotency_key="drill-outbox-claimed",
        actor="drill",
    )
    durable.claim_outbox(
        store,
        tenant.tenant_id,
        claimed.outbox_id,
        worker="worker-a",
        lease_seconds=10.0,
        now=EXPIRED,
    )

    eventlog.append_event(
        store,
        tenant.tenant_id,
        "drill-aggregate-1",
        "invoice.sent",
        {"invoice": 7},
        actor="drill",
        idempotency_key="drill-event-1",
    )
    eventlog.append_event(
        store,
        tenant.tenant_id,
        "drill-aggregate-1",
        "invoice.reconciled",
        {"invoice": 7},
        actor="drill",
        idempotency_key="drill-event-2",
    )

    return store, tenant, business, running, claimed


def _tamper(snapshot_dir: Path) -> None:
    database = snapshot_dir / backup.ENCRYPTED_NAME
    payload = bytearray(database.read_bytes())
    payload[-1] ^= 0xFF
    database.write_bytes(bytes(payload))


def run(workdir: Path) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    store, tenant, business, running, claimed = _seed(workdir)

    # 1. encrypted snapshot / verify / restore / replay + RPO/RTO
    drill = dr.run_restore_drill(store, workdir / "restore-drill")
    checks.append(
        _check(
            "encrypted_snapshot_restored_and_replayed",
            drill["ok"] and drill["snapshot"]["encrypted"],
            {
                "snapshot_id": drill["snapshot"]["snapshot_id"],
                "checks": len(drill["checks"]),
                "restored": drill["restored_path"],
            },
        )
    )
    checks.append(
        _check(
            "restore_target_is_not_the_live_database",
            Path(drill["restored_path"]).resolve() != store.path.resolve(),
            {"live": str(store.path), "restored": drill["restored_path"]},
        )
    )
    checks.append(
        _check(
            "rpo_rto_measured_no_sla",
            drill["rpo"]["sla_claimed"] is False
            and drill["rto"]["sla_claimed"] is False
            and drill["rto"]["rto_seconds"] >= 0.0,
            {"rpo_seconds": drill["rpo"]["rpo_seconds"],
             "rto_seconds": drill["rto"]["rto_seconds"]},
        )
    )

    # 2. a tampered copy is refused
    tampered = workdir / "tampered"
    shutil.copytree(drill["snapshot_dir"], tampered)
    _tamper(tampered)
    tamper_ok, tamper_code = _refusal(
        lambda: dr.restore_snapshot(tampered, workdir / "tampered-restore" / "db"),
        backup.BACKUP_HASH_MISMATCH_CODE,
    )
    checks.append(
        _check("tampered_snapshot_refused", tamper_ok, {"code": tamper_code})
    )

    # 3. crash/unknown recovery on a fresh restore (never the live file)
    recovery_target = workdir / "recovery" / backup.DATABASE_NAME
    dr.restore_snapshot(
        drill["snapshot_dir"], recovery_target, live_db=store.path
    )
    recovered = dr.recover_unknown(BusinessStore(recovery_target), now=10**9)
    recovered_again = dr.recover_unknown(
        BusinessStore(recovery_target), now=10**9
    )
    live_job = jobs.get(store, tenant.tenant_id, running.job_id)
    checks.append(
        _check(
            "unknown_recovery_is_idempotent",
            running.job_id in recovered["unknown_jobs"]
            and claimed.outbox_id in recovered["unknown_outbox"]
            and recovered_again["unknown_jobs"] == []
            and recovered_again["unknown_outbox"] == [],
            {
                "unknown_jobs": recovered["unknown_jobs"],
                "unknown_outbox": recovered["unknown_outbox"],
                "second_sweep": recovered_again,
            },
        )
    )
    checks.append(
        _check(
            "recovery_did_not_mutate_the_live_job",
            live_job.status == "running",
            {"live_job_status": live_job.status},
        )
    )

    # 4. kill-switch drill: one injected surface fails, the rest still run
    calls: List[str] = []

    def scheduler():
        calls.append("scheduler")
        return {"stopped": "scheduler"}

    def gateway():
        calls.append("gateway")
        raise RuntimeError("gateway refused the stop signal")

    def egress():
        calls.append("egress")
        return {"blocked": "egress"}

    controller = dr.KillSwitchController(
        store,
        tenant.tenant_id,
        dr.build_stoppers(
            store,
            tenant.tenant_id,
            scheduler=scheduler,
            gateway=gateway,
            egress=egress,
        ),
    )
    killed = controller.engage(reason="drill", idempotency_key="drill-kill")
    killed_again = controller.engage(
        reason="drill again", idempotency_key="drill-kill-2"
    )
    checks.append(
        _check(
            "kill_switch_records_failure_and_stops_the_rest",
            killed["status"] == "PARTIAL"
            and killed["surfaces_failed"] == ["gateway"]
            and set(killed["surfaces_stopped"])
            == {"scheduler", "leases", "egress"}
            and sorted(calls) == ["egress", "gateway", "scheduler"],
            {
                "status": killed["status"],
                "stopped": killed["surfaces_stopped"],
                "failed": killed["surfaces_failed"],
            },
        )
    )
    checks.append(
        _check(
            "kill_switch_is_idempotent",
            killed_again["already_engaged"] is True
            and sorted(calls) == ["egress", "gateway", "scheduler"],
            {"already_engaged": killed_again["already_engaged"]},
        )
    )

    # 5. incident record, kill-switch link, resolution
    incident = dr.record_incident(
        store,
        tenant.tenant_id,
        kind="provider_outage",
        severity="high",
        summary="drill: provider surface failed to stop",
        actor="drill",
        idempotency_key="drill-incident",
    )
    resolved = dr.resolve_incident(
        store,
        tenant.tenant_id,
        incident["incident_id"],
        summary="drill: surfaces stopped, gateway flagged for follow-up",
        actor="drill",
        kill_switch=killed,
    )
    checks.append(
        _check(
            "incident_recorded_and_resolved",
            incident["status"] == "open"
            and resolved["status"] == "resolved"
            and resolved["kill_switch_status"] == "PARTIAL",
            {
                "incident_id": incident["incident_id"],
                "status": resolved["status"],
                "kill_switch_status": resolved["kill_switch_status"],
            },
        )
    )

    # 6. deployment rings: order, gates, production approval, canary, rollback
    rings = dr.DeploymentRings(store, tenant_id=tenant.tenant_id)
    rings.promote(
        "internal",
        revision="rev-1",
        gates={"backup_verified": True, "dr_drill_passed": drill["ok"]},
    )
    rings.promote(
        "synthetic", revision="rev-1", gates={"internal_soak": True}
    )
    rings.promote(
        "pilot",
        revision="rev-1",
        gates={"synthetic_soak": True, "canary_healthy": True},
    )
    rings.rollout("rev-2", ring="pilot")
    canary = rings.canary("rev-2", ring="pilot", percent=10, healthy=True)

    production_gates = {
        "pilot_soak": True,
        "dr_drill_passed": drill["ok"],
        "rollback_rehearsed": True,
    }
    blocked_ok, blocked_code = _refusal(
        lambda: rings.promote(
            "production", revision="rev-2", gates=production_gates
        ),
        dr.DR_PRODUCTION_APPROVAL_REQUIRED_CODE,
    )
    promoted = rings.promote(
        "production",
        revision="rev-2",
        gates=production_gates,
        owner_approved=True,
        owner="owner",
    )
    rollback = rings.rollback(
        "production",
        to_revision="rev-1",
        reason="drill emergency rollback",
    )
    checks.append(
        _check(
            "ring_gates_and_production_approval",
            blocked_ok
            and promoted["owner_approved"] is True
            and promoted["ring"] == "production"
            and canary["ok"] is True,
            {
                "blocked_code": blocked_code,
                "owner_approved": promoted["owner_approved"],
                "canary_ok": canary["ok"],
            },
        )
    )
    checks.append(
        _check(
            "ring_rollback_is_audited",
            rollback["action"] == "rollback"
            and rollback["revision"] == "rev-1"
            and rings.deployed()["production"] == "rev-1"
            and len(rings.history()) >= 6,
            {
                "revision": rollback["revision"],
                "history": len(rings.history()),
            },
        )
    )

    return {
        "schema": DRILL_SCHEMA,
        "generated_at": now_iso(),
        "mode": "drill",
        "workdir": str(workdir),
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "restore_drill": drill,
        "recovery": recovered,
        "kill_switch": killed,
        "incident": resolved,
        "rings": rings.as_dict(),
        "limitations": list(LIMITATIONS),
    }


def _summary(report: Dict[str, Any]) -> str:
    lines = [
        f"DR drill {'PASS' if report['ok'] else 'FAIL'}"
        f" — {sum(1 for c in report['checks'] if c['ok'])}/{len(report['checks'])}"
        " checks"
    ]
    for item in report["checks"]:
        lines.append(
            f"  [{'PASS' if item['ok'] else 'FAIL'}] {item['name']}"
        )
    rpo = report["restore_drill"]["rpo"]["rpo_seconds"]
    rto = report["restore_drill"]["rto"]["rto_seconds"]
    lines.append(f"  RPO {rpo:.3f}s / RTO {rto:.3f}s (no SLA claimed)")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Qubit Office disaster-recovery drill (hermetic)."
    )
    parser.add_argument("--json", action="store_true", help="print one JSON report")
    parser.add_argument(
        "--workdir",
        default=None,
        help="empty directory for drill state (default: a fresh temp dir)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary workdir after the drill",
    )
    args = parser.parse_args(argv)

    temporary = args.workdir is None
    if temporary:
        workdir = Path(tempfile.mkdtemp(prefix="qubit-drill-"))
    else:
        workdir = Path(args.workdir).expanduser()
        if workdir.exists():
            if not workdir.is_dir():
                print(f"drill: {workdir} is not a directory", file=sys.stderr)
                return 2
            if any(workdir.iterdir()):
                print(
                    f"drill: {workdir} must be empty for a fresh drill",
                    file=sys.stderr,
                )
                return 2
        workdir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    try:
        report = run(workdir)
        report["duration_seconds"] = round(time.perf_counter() - started, 3)
    except BusinessError as exc:
        report = {
            "schema": DRILL_SCHEMA,
            "generated_at": now_iso(),
            "mode": "drill",
            "workdir": str(workdir),
            "ok": False,
            "error": {"code": exc.code, "detail": str(exc)},
            "checks": [],
            "limitations": list(LIMITATIONS),
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"DR drill FAIL — {exc.code}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a drill must not crash silently
        report = {
            "schema": DRILL_SCHEMA,
            "generated_at": now_iso(),
            "mode": "drill",
            "workdir": str(workdir),
            "ok": False,
            "error": {"code": "DRILL_INTERNAL_ERROR", "detail": repr(exc)},
            "checks": [],
            "limitations": list(LIMITATIONS),
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"DR drill INTERNAL ERROR: {exc!r}", file=sys.stderr)
        return 2
    finally:
        if temporary and not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_summary(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
