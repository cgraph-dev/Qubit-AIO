"""P8-03 — disaster recovery, incident records, kill switch and deployment rings.

This module is a *composition* layer over the P1-09 backup/restore surface
(:mod:`voicebridge.business.backup`) and the P4-06 safety surface
(:mod:`voicebridge.business.safety`). It adds no second database, no second
execution engine and no schema change: an incident is an audit row, a kill
switch is the existing :class:`OutOfBandKillSwitch`, and a deployment ring is
an in-memory controller whose every transition is audited.

What it proves
--------------
* **A backup is recovery only after it is restored.** :func:`run_restore_drill`
  takes an encrypted snapshot, verifies it, restores it to a **new** path,
  and then replays the event index, job/workflow trail and audit trail against
  the source database. It measures RPO/RTO with wall-clock timings and never
  claims an SLA.
* **Tenant scope survives a round trip.** :func:`restore_snapshot` refuses a
  restored database whose tenant set disagrees with the request; the tenant
  copy path itself is the existing one, leak check included.
* **A tampered snapshot is refused.** Verification is the existing hash/size
  check; this module does not add a weaker path.
* **Interrupted work becomes ``unknown``, never silently retried.**
  :func:`recover_unknown` composes ``jobs.recover`` and
  ``durable.recover_outbox`` and is idempotent.
* **One operator action stops every surface.** :class:`KillSwitchController`
  wraps :class:`OutOfBandKillSwitch`, which already records a failing stopper
  and still runs the rest.
* **Production never rolls forward without an owner.** :class:`DeploymentRings`
  moves only one ring at a time (internal -> synthetic -> pilot -> production),
  evaluates named promotion gates, and refuses production without explicit
  owner approval.

Honest limits
-------------
* Deployment-ring state is **in-memory per controller**; the durable record is
  the audit trail this module writes. There is no ring state table.
* RPO is the age of the newest committed record when the snapshot started. A
  one-shot snapshot does not bound loss after it.
* RTO is measured on this host's disk and CPU for the seeded fixture; it is not
  an SLA and it does not model a cold machine, a remote object store or a
  network transfer.
* A tenant-scoped snapshot is built from ``backup``'s declared tenant tables,
  which do not include ``bos_event``; the drill reports the event-index replay
  as not applicable for that scope (a whole-database drill replays it).
* Nothing here contacts a provider, a network, a live service or a credential.
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import backup, durable, eventlog, jobs, ledger
from .errors import BAD_INPUT_CODE, BusinessError
from .ids import canonical_json
from .safety import (
    KILL_SWITCH_SURFACES,
    OutOfBandKillSwitch,
    lease_stopper,
)
from .store import BusinessStore
from .util import now_iso, redact

DR_SCHEMA = "qubit-office-disaster-recovery/1"
RECOVERY_SCHEMA = "qubit-office-unknown-recovery/1"
INCIDENT_SCHEMA = "qubit-office-incident/1"
RING_SCHEMA = "qubit-office-deployment-rings/1"
CANARY_SCHEMA = "qubit-office-ring-canary/1"

DR_BAD_INPUT_CODE = "DR_BAD_INPUT"
DR_LIVE_DB_WRITE_REFUSED_CODE = "DR_LIVE_DB_WRITE_REFUSED"
DR_SCOPE_MISMATCH_CODE = "DR_SCOPE_MISMATCH"
DR_REPLAY_FAILED_CODE = "DR_REPLAY_FAILED"
DR_INCIDENT_MISSING_CODE = "DR_INCIDENT_MISSING"
DR_RING_UNKNOWN_CODE = "DR_RING_UNKNOWN"
DR_RING_ORDER_CODE = "DR_RING_ORDER"
DR_RING_PROMOTION_BLOCKED_CODE = "DR_RING_PROMOTION_BLOCKED"
DR_PRODUCTION_APPROVAL_REQUIRED_CODE = "DR_PRODUCTION_APPROVAL_REQUIRED"
DR_RING_ROLLBACK_CODE = "DR_RING_ROLLBACK"

#: Deployment order. A ring may only be entered from the one before it.
RINGS: tuple[str, ...] = ("internal", "synthetic", "pilot", "production")

#: Gates that must be explicitly ``True`` before entering each ring.
RING_GATES: dict[str, tuple[str, ...]] = {
    "internal": ("backup_verified", "dr_drill_passed"),
    "synthetic": ("internal_soak",),
    "pilot": ("synthetic_soak", "canary_healthy"),
    "production": ("pilot_soak", "dr_drill_passed", "rollback_rehearsed"),
}

INCIDENT_SEVERITIES = ("low", "medium", "high", "critical")

#: Tables whose newest ``created_at`` defines the RPO watermark. They are the
#: append-only / business-record tables this surface is responsible for.
_WATERMARK_TABLES: tuple[str, ...] = (
    "bos_business",
    "bos_job",
    "bos_job_event",
    "bos_approval",
    "bos_ledger_entry",
    "bos_audit_event",
    "bos_event",
    "bos_checkpoint",
    "bos_outbox",
)


def _fail(code: str, detail: str) -> None:
    raise BusinessError(code, detail)


def _text(value: Any, label: str, *, max_len: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        _fail(BAD_INPUT_CODE, f"{label} is longer than {max_len} characters")
    return clean


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(rows: Sequence[Any]) -> str:
    return _sha256_text(canonical_json(list(rows)))


# ---------------------------------------------------------------------------
# Restore drills
# ---------------------------------------------------------------------------
def _assert_not_live(target: Path, live_paths: Sequence[Path]) -> None:
    resolved = Path(target).resolve()
    for live in live_paths:
        if live is not None and resolved == Path(live).resolve():
            _fail(
                DR_LIVE_DB_WRITE_REFUSED_CODE,
                f"restore target {target} resolves to the live database"
                f" {live}; a live database is never overwritten",
            )


def _tenant_ids(store: BusinessStore) -> list[str]:
    with store.read() as conn:
        return [
            str(row[0])
            for row in conn.execute(
                "SELECT tenant_id FROM bos_tenant ORDER BY tenant_id"
            ).fetchall()
        ]


def _newest_record_iso(store: BusinessStore) -> str | None:
    newest: str | None = None
    with store.read() as conn:
        present = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in _WATERMARK_TABLES:
            if table not in present:
                continue
            value = conn.execute(
                f'SELECT MAX(created_at) FROM "{table}"'
            ).fetchone()[0]
            if value is not None and (newest is None or str(value) > newest):
                newest = str(value)
    return newest


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _audit_rows(store: BusinessStore, tenant_id: str) -> list[tuple[Any, ...]]:
    return [
        (
            event.event_id,
            event.action,
            event.resource_type,
            event.resource_id,
            event.outcome,
            event.created_at,
        )
        for event in store.list_audit(tenant_id, limit=1_000_000)
    ]


def _event_rows(store: BusinessStore, tenant_id: str) -> list[tuple[Any, ...]]:
    with store.read() as conn:
        aggregates = [
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT aggregate_id FROM bos_event WHERE tenant_id=?"
                " ORDER BY aggregate_id",
                (tenant_id,),
            ).fetchall()
        ]
    rows: list[tuple[Any, ...]] = []
    for aggregate in aggregates:
        for event in eventlog.replay(store, tenant_id, aggregate):
            rows.append(
                (
                    event.aggregate_id,
                    event.sequence,
                    event.event_id,
                    event.type,
                    event.payload_hash,
                )
            )
    return rows


def _workflow_rows(store: BusinessStore, tenant_id: str) -> dict[str, list[Any]]:
    job_rows = [
        (job.job_id, job.kind, job.status, job.attempt)
        for job in jobs.list_jobs(store, tenant_id)
    ]
    job_events: list[tuple[Any, ...]] = []
    for job in jobs.list_jobs(store, tenant_id):
        for event in jobs.list_events(store, tenant_id, job.job_id):
            job_events.append((job.job_id, event.seq, event.kind))
    with store.read() as conn:
        outbox = [
            (str(row[0]), str(row[1]), int(row[2]))
            for row in conn.execute(
                "SELECT outbox_id, status, attempts FROM bos_outbox"
                " WHERE tenant_id=? ORDER BY outbox_id",
                (tenant_id,),
            ).fetchall()
        ]
    return {"jobs": job_rows, "job_events": job_events, "outbox": outbox}


def _ledger_totals(store: BusinessStore, tenant_id: str) -> dict[str, Any]:
    summary = ledger.summary(store, tenant_id)
    return {
        "entry_count": summary["entry_count"],
        "credit_minor": summary["credit_minor"],
        "debit_minor": summary["debit_minor"],
        "net_minor": summary["net_minor"],
    }


def _replay_checks(
    source: BusinessStore,
    restored: BusinessStore,
    tenants: Sequence[str],
    *,
    tenant_scoped: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compare event index, workflow trail and audit trail on raw rows.

    This runs **before** any recovery sweep mutates the restored database, so a
    status difference introduced by recovery is not misreported as data loss.

    A tenant-scoped snapshot uses the declared tenant tables in ``backup``,
    which do not include ``bos_event``. When that scope is requested and the
    restored copy legitimately has no events, the index check is reported as
    not applicable rather than passed off as a successful replay.
    """
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for tenant in tenants:
        audit_left = _audit_rows(source, tenant)
        audit_right = _audit_rows(restored, tenant)
        ok = _digest(audit_left) == _digest(audit_right)
        checks.append(
            {
                "name": f"audit_replay.{tenant}",
                "ok": ok,
                "rows": len(audit_left),
            }
        )
        if not ok:
            failures.append(f"audit_replay.{tenant}")

        event_left = _event_rows(source, tenant)
        event_right = _event_rows(restored, tenant)
        if tenant_scoped and event_left and not event_right:
            checks.append(
                {
                    "name": f"event_index_replay.{tenant}",
                    "ok": True,
                    "applicable": False,
                    "rows": 0,
                    "reason": "a tenant-scoped snapshot does not carry bos_event;"
                    " run a whole-database drill to replay the event index",
                }
            )
        else:
            ok = _digest(event_left) == _digest(event_right)
            checks.append(
                {
                    "name": f"event_index_replay.{tenant}",
                    "ok": ok,
                    "applicable": True,
                    "rows": len(event_left),
                }
            )
            if not ok:
                failures.append(f"event_index_replay.{tenant}")

        left_wf = _workflow_rows(source, tenant)
        right_wf = _workflow_rows(restored, tenant)
        for section in ("jobs", "job_events", "outbox"):
            ok = _digest(left_wf[section]) == _digest(right_wf[section])
            checks.append(
                {
                    "name": f"workflow_{section}_replay.{tenant}",
                    "ok": ok,
                    "rows": len(left_wf[section]),
                }
            )
            if not ok:
                failures.append(f"workflow_{section}_replay.{tenant}")

        left_ledger = _ledger_totals(source, tenant)
        right_ledger = _ledger_totals(restored, tenant)
        ok = left_ledger == right_ledger and right_ledger["net_minor"] == (
            right_ledger["credit_minor"] - right_ledger["debit_minor"]
        )
        checks.append(
            {
                "name": f"ledger_replay.{tenant}",
                "ok": ok,
                "totals": right_ledger,
            }
        )
        if not ok:
            failures.append(f"ledger_replay.{tenant}")
    return checks, failures


def restore_snapshot(
    snapshot_dir: str | Path,
    target: str | Path,
    *,
    tenant_id: str | None = None,
    key: Any = None,
    live_db: str | Path | None = None,
) -> dict[str, Any]:
    """Restore a verified snapshot to a new path and prove the tenant scope.

    ``live_db`` names a database this call must never overwrite. Even without
    it, an existing target is refused by the underlying restore.
    """
    target_path = Path(target)
    live_paths = [Path(live_db)] if live_db is not None else []
    _assert_not_live(target_path, live_paths)
    report = backup.restore(snapshot_dir, target_path, tenant_id=tenant_id, key=key)
    if tenant_id is None:
        if report["scope"].get("tenant_id") is not None:
            _fail(
                DR_SCOPE_MISMATCH_CODE,
                f"whole restore reported a tenant scope {report['scope']!r}",
            )
    else:
        if report["scope"].get("tenant_id") != tenant_id:
            _fail(
                DR_SCOPE_MISMATCH_CODE,
                f"restore scope {report['scope']!r} is not tenant {tenant_id!r}",
            )
        if report["tenants"] != [tenant_id]:
            _fail(
                DR_SCOPE_MISMATCH_CODE,
                f"restored tenants {report['tenants']!r} are not exactly"
                f" [{tenant_id!r}]",
            )
    report = dict(report)
    report["target_written"] = str(target_path)
    report["live_db"] = None if live_db is None else str(live_db)
    return report


def run_restore_drill(
    store: BusinessStore,
    workdir: str | Path,
    *,
    tenant_id: str | None = None,
    key: Any = None,
) -> dict[str, Any]:
    """Snapshot, verify, restore and replay one tenant or the whole database.

    The restore target is always a new file below ``workdir``; the live
    database path is passed to :func:`restore_snapshot` so overwriting it is a
    typed refusal rather than a convention.
    """
    workdir = Path(workdir)
    snapshot_dir = workdir / "snapshot"
    restored_path = workdir / "restored" / backup.DATABASE_NAME
    if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
        _fail(
            DR_BAD_INPUT_CODE,
            f"{snapshot_dir} is not empty; a drill needs a fresh workdir",
        )
    _assert_not_live(restored_path, [store.path])

    newest_before = _newest_record_iso(store)
    started_at = now_iso()

    t0 = time.perf_counter()
    manifest = backup.snapshot(
        store, snapshot_dir, tenant_id=tenant_id, encrypt=True, key=key
    )
    snapshot_seconds = time.perf_counter() - t0

    t1 = time.perf_counter()
    verified = backup.verify_snapshot(snapshot_dir)
    verify_seconds = time.perf_counter() - t1

    t2 = time.perf_counter()
    restored_report = restore_snapshot(
        snapshot_dir,
        restored_path,
        tenant_id=tenant_id,
        key=key,
        live_db=store.path,
    )
    restore_seconds = time.perf_counter() - t2

    restored_store = BusinessStore(restored_path)
    source_tenants = [tenant_id] if tenant_id is not None else _tenant_ids(store)
    t3 = time.perf_counter()
    replay_checks, replay_failures = _replay_checks(
        store,
        restored_store,
        source_tenants,
        tenant_scoped=tenant_id is not None,
    )
    pre_replay_seconds = time.perf_counter() - t3

    t4 = time.perf_counter()
    replay = backup.replay_verify(snapshot_dir, restored_path, key=key)
    replay_seconds = time.perf_counter() - t4

    if replay_failures:
        _fail(
            DR_REPLAY_FAILED_CODE,
            "restore replay failed: " + ", ".join(sorted(set(replay_failures))),
        )

    newest = _parse_iso(newest_before)
    snapshot_started = _parse_iso(manifest["created_at"])
    rpo_seconds = 0.0
    if newest is not None and snapshot_started is not None:
        rpo_seconds = max(0.0, (snapshot_started - newest).total_seconds())
    restore_round = round(restore_seconds, 6)
    replay_round = round(replay_seconds, 6)

    checks = list(replay_checks)
    checks.append(
        {
            "name": "backup_verify",
            "ok": bool(verified["ok"]),
            "encrypted": bool(verified["encrypted"]),
        }
    )
    checks.append(
        {
            "name": "replay_verify",
            "ok": bool(replay["ok"]),
            "checks": [item["name"] for item in replay["checks"]],
        }
    )
    checks.extend(
        {
            "name": f"replay_verify.{item['name']}",
            "ok": bool(item["ok"]),
        }
        for item in replay["checks"]
    )
    ok = all(item["ok"] for item in checks)

    return {
        "ok": ok,
        "schema": DR_SCHEMA,
        "generated_at": now_iso(),
        "started_at": started_at,
        "scope": {"tenant_id": tenant_id},
        "tenants": source_tenants,
        "snapshot_dir": str(snapshot_dir),
        "restored_path": str(restored_path),
        "snapshot": {
            "snapshot_id": manifest["snapshot_id"],
            "created_at": manifest["created_at"],
            "encrypted": bool(manifest["encryption"].get("enabled")),
            "row_counts": manifest.get("row_counts", {}),
            "file_count": len(manifest.get("files", [])),
        },
        "verify": {"ok": bool(verified["ok"]), "encrypted": verified["encrypted"]},
        "restore": {
            "ok": bool(restored_report["ok"]),
            "target": restored_report["target"],
            "scope": restored_report["scope"],
            "tenants": restored_report["tenants"],
            "table_counts": restored_report["table_counts"],
        },
        "replay": {
            "ok": bool(replay["ok"]),
            "checkpoints_by_run": replay.get("checkpoints_by_run", {}),
            "outbox_by_status": replay.get("outbox_by_status", {}),
            "recovered_unknown_jobs": replay.get("recovered_unknown_jobs", []),
        },
        "rpo": {
            "definition": "age of the newest committed business record when the"
            " snapshot started; a one-shot snapshot does not bound loss after it",
            "newest_record_at": newest_before,
            "snapshot_started_at": manifest["created_at"],
            "rpo_seconds": round(rpo_seconds, 6),
            "sla_claimed": False,
        },
        "rto": {
            "definition": "measured snapshot + verify + restore + replay wall"
            " time on this host for the seeded fixture",
            "snapshot_seconds": round(snapshot_seconds, 6),
            "verify_seconds": round(verify_seconds, 6),
            "restore_seconds": restore_round,
            "pre_replay_checks_seconds": round(pre_replay_seconds, 6),
            "replay_seconds": replay_round,
            "rto_seconds": round(restore_round + replay_round, 6),
            "sla_claimed": False,
        },
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Unknown-outcome recovery
# ---------------------------------------------------------------------------
def recover_unknown(
    store: BusinessStore, *, now: float | None = None, actor: str = "recovery"
) -> dict[str, Any]:
    """Restart sweep: expired work becomes ``unknown``; nothing is retried.

    Idempotent: once the rows are ``unknown`` a second sweep finds nothing.
    """
    moment = time.time() if now is None else float(now)
    unknown_jobs = jobs.recover(store, now=moment, actor=actor)
    unknown_outbox = durable.recover_outbox(store, now=moment, actor=actor)
    return {
        "schema": RECOVERY_SCHEMA,
        "generated_at": now_iso(),
        "now": moment,
        "unknown_jobs": [item.job_id for item in unknown_jobs],
        "unknown_outbox": [item.outbox_id for item in unknown_outbox],
        "retried": False,
    }


# ---------------------------------------------------------------------------
# Incident records (audit-backed; no schema change)
# ---------------------------------------------------------------------------
def _incident_id(tenant_id: str, kind: str, key: str) -> str:
    return "inc-" + _sha256_text(f"{tenant_id}\x00{kind}\x00{key}")[:20]


def _incident_events(store: BusinessStore, tenant_id: str) -> list[Any]:
    return [
        event
        for event in store.list_audit(tenant_id, limit=1_000_000)
        if event.action in ("dr.incident.open", "dr.incident.resolve")
    ]


def _incident_view(events: Sequence[Any], incident_id: str) -> dict[str, Any]:
    opened = next(
        (
            event
            for event in events
            if event.action == "dr.incident.open"
            and event.resource_id == incident_id
        ),
        None,
    )
    if opened is None:
        _fail(
            DR_INCIDENT_MISSING_CODE,
            f"no incident {incident_id!r} in this tenant",
        )
    resolved = next(
        (
            event
            for event in events
            if event.action == "dr.incident.resolve"
            and event.resource_id == incident_id
        ),
        None,
    )
    detail = dict(opened.detail or {})
    view = {
        "schema": INCIDENT_SCHEMA,
        "incident_id": incident_id,
        "tenant_id": opened.tenant_id,
        "kind": detail.get("kind"),
        "severity": detail.get("severity"),
        "summary": detail.get("summary"),
        "actor": opened.actor,
        "detected_at": detail.get("detected_at"),
        "opened_at": opened.created_at,
        "status": "open",
        "open_audit_id": opened.event_id,
        "resolve_audit_id": None,
        "resolved_at": None,
        "resolution": None,
        "kill_switch_status": None,
    }
    if resolved is not None:
        resolve_detail = dict(resolved.detail or {})
        view.update(
            {
                "status": "resolved",
                "resolve_audit_id": resolved.event_id,
                "resolved_at": resolved.created_at,
                "resolution": resolve_detail.get("summary"),
                "kill_switch_status": resolve_detail.get("kill_switch_status"),
            }
        )
    return view


def record_incident(
    store: BusinessStore,
    tenant_id: str,
    *,
    kind: str,
    severity: str,
    summary: str,
    actor: str,
    idempotency_key: str,
    detected_at: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Open an incident as a durable audit row; replay returns the same record."""
    tenant = _text(tenant_id, "tenant_id")
    kind_clean = _text(kind, "kind", max_len=120)
    severity_clean = _text(severity, "severity", max_len=20).lower()
    if severity_clean not in INCIDENT_SEVERITIES:
        _fail(
            BAD_INPUT_CODE,
            f"severity must be one of {', '.join(INCIDENT_SEVERITIES)}",
        )
    summary_clean = _text(summary, "summary", max_len=2000)
    who = _text(actor, "actor", max_len=200)
    key = _text(idempotency_key, "idempotency_key", max_len=300)
    incident_id = _incident_id(tenant, kind_clean, key)

    events = _incident_events(store, tenant)
    existing = next(
        (
            event
            for event in events
            if event.action == "dr.incident.open"
            and event.resource_id == incident_id
        ),
        None,
    )
    if existing is not None:
        view = _incident_view(events, incident_id)
        view["replayed"] = True
        return view

    store.audit(
        tenant,
        actor=who,
        action="dr.incident.open",
        resource_type="incident",
        resource_id=incident_id,
        outcome="error" if severity_clean in ("high", "critical") else "ok",
        detail=redact(
            {
                "kind": kind_clean,
                "severity": severity_clean,
                "summary": summary_clean,
                "detected_at": detected_at or now_iso(),
                "idempotency_key": key,
                "details": dict(details or {}),
            }
        ),
    )
    view = _incident_view(_incident_events(store, tenant), incident_id)
    view["replayed"] = False
    return view


def resolve_incident(
    store: BusinessStore,
    tenant_id: str,
    incident_id: str,
    *,
    summary: str,
    actor: str,
    kill_switch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Close an open incident; a second resolve is a replay, not a new row."""
    tenant = _text(tenant_id, "tenant_id")
    ident = _text(incident_id, "incident_id", max_len=120)
    summary_clean = _text(summary, "summary", max_len=2000)
    who = _text(actor, "actor", max_len=200)
    events = _incident_events(store, tenant)
    view = _incident_view(events, ident)
    if view["status"] == "resolved":
        view["replayed"] = True
        return view
    store.audit(
        tenant,
        actor=who,
        action="dr.incident.resolve",
        resource_type="incident",
        resource_id=ident,
        outcome="ok",
        detail=redact(
            {
                "summary": summary_clean,
                "kill_switch_status": None
                if kill_switch is None
                else kill_switch.get("status"),
            }
        ),
    )
    view = _incident_view(_incident_events(store, tenant), ident)
    view["replayed"] = False
    return view


def list_incidents(
    store: BusinessStore, tenant_id: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    tenant = _text(tenant_id, "tenant_id")
    events = _incident_events(store, tenant)
    ids = sorted(
        {
            event.resource_id
            for event in events
            if event.resource_id is not None
            and event.action == "dr.incident.open"
        }
    )
    incidents = [_incident_view(events, ident) for ident in ids]
    incidents.sort(key=lambda item: item["opened_at"], reverse=True)
    return incidents[: max(1, int(limit))]


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------
class KillSwitchController:
    """Per-tenant kill switch over the existing out-of-band implementation.

    Idempotence is durable, not just per object: the existing
    ``safety.kill_switch`` audit row is the record that an operator already
    engaged the switch, so a new controller on the same tenant returns the
    recorded result without calling any stopper again.
    """

    def __init__(
        self,
        store: BusinessStore,
        tenant_id: str,
        stoppers: Mapping[str, Callable[[], Any]],
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.tenant_id = _text(tenant_id, "tenant_id")
        self._switch = OutOfBandKillSwitch(store, stoppers, clock=clock)

    def _existing_report(self) -> dict[str, Any] | None:
        events = self.store.list_audit(
            self.tenant_id, action="safety.kill_switch", limit=1000
        )
        if not events:
            return None
        event = events[-1]
        detail = dict(event.detail or {})
        surfaces = list(detail.get("surfaces") or [])
        stopped = [item.get("surface") for item in surfaces if item.get("ok")]
        failed = [item.get("surface") for item in surfaces if not item.get("ok")]
        status = detail.get("status") or (
            "STOPPED" if not failed else ("PARTIAL" if stopped else "FAILED")
        )
        return {
            "status": status,
            "engaged": status in ("STOPPED", "PARTIAL"),
            "reason": detail.get("reason"),
            "actor": event.actor,
            "surfaces": surfaces,
            "surfaces_stopped": stopped,
            "surfaces_failed": failed,
            "started_at": None,
            "audit_id": event.event_id,
            "audit_error": "",
            "already_engaged": True,
            "reconstructed": True,
        }

    def engage(
        self,
        *,
        reason: str,
        actor: str = "operator",
        idempotency_key: str = "kill-switch",
    ) -> dict[str, Any]:
        existing = self._existing_report()
        if existing is not None:
            return existing
        report = self._switch.engage(
            tenant_id=self.tenant_id,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        return report.as_dict()

    def status(self) -> dict[str, Any]:
        return self._switch.status()


def build_stoppers(
    store: BusinessStore,
    tenant_id: str,
    *,
    scheduler: Callable[[], Any] | None = None,
    gateway: Callable[[], Any] | None = None,
    egress: Callable[[], Any] | None = None,
) -> dict[str, Callable[[], Any]]:
    """Assemble the four required stoppers; injected surfaces are mandatory.

    ``leases`` uses the shipped Business OS lease stopper. The other three are
    external seams this module cannot fabricate: a missing one is a typed
    refusal rather than a no-op that would make an incomplete switch look
    complete.
    """
    provided = {"scheduler": scheduler, "gateway": gateway, "egress": egress}
    missing = [name for name, value in provided.items() if not callable(value)]
    if missing:
        _fail(
            DR_BAD_INPUT_CODE,
            "kill switch needs injected stoppers for: " + ", ".join(missing),
        )
    stoppers: dict[str, Callable[[], Any]] = {
        "scheduler": scheduler,  # type: ignore[dict-item]
        "gateway": gateway,  # type: ignore[dict-item]
        "leases": lease_stopper(store, tenant_id),
        "egress": egress,  # type: ignore[dict-item]
    }
    assert set(stoppers) == set(KILL_SWITCH_SURFACES)
    return stoppers


# ---------------------------------------------------------------------------
# Deployment rings
# ---------------------------------------------------------------------------
class DeploymentRings:
    """Ring-scoped rollout with promotion gates, canary and rollback.

    State is in-memory per controller; every transition is written to the
    audit trail so the durable record survives the process.
    """

    def __init__(
        self,
        store: BusinessStore,
        *,
        tenant_id: str | None = None,
        clock: Callable[[], float] | None = None,
        actor: str = "operator",
    ) -> None:
        self.store = store
        self.tenant_id = None if tenant_id is None else _text(tenant_id, "tenant_id")
        self._clock = clock or time.time
        self._actor = _text(actor, "actor", max_len=200)
        self._lock = threading.RLock()
        self._current: str | None = None
        self._deployed: dict[str, str | None] = {ring: None for ring in RINGS}
        self._history: list[dict[str, Any]] = []

    # -- helpers -------------------------------------------------------
    def _stamp(self) -> str:
        return now_iso(self._clock())

    def _audit(self, action: str, detail: Mapping[str, Any], actor: str) -> None:
        if self.tenant_id is None:
            return
        self.store.audit(
            self.tenant_id,
            actor=actor,
            action=action,
            resource_type="deployment_ring",
            resource_id=str(detail.get("ring", "rings")),
            outcome="ok",
            detail=redact(dict(detail)),
        )

    def _record(self, record: dict[str, Any], actor: str) -> dict[str, Any]:
        self._history.append(record)
        self._audit(f"dr.ring.{record['action']}", record, actor)
        return record

    # -- queries -------------------------------------------------------
    def current_ring(self) -> str | None:
        return self._current

    def deployed(self) -> dict[str, str | None]:
        return dict(self._deployed)

    def history(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._history]

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": RING_SCHEMA,
                "rings": list(RINGS),
                "current_ring": self._current,
                "deployed": dict(self._deployed),
                "history": [dict(item) for item in self._history],
                "gates": {ring: list(RING_GATES[ring]) for ring in RINGS},
            }

    # -- transitions ---------------------------------------------------
    def promote(
        self,
        target_ring: str,
        *,
        revision: str,
        gates: Mapping[str, Any] | None = None,
        owner_approved: bool = False,
        owner: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Advance exactly one ring after its named gates are all true."""
        target = _text(target_ring, "target_ring", max_len=40)
        rev = _text(revision, "revision", max_len=200)
        who = _text(actor or self._actor, "actor", max_len=200)
        if target not in RINGS:
            _fail(DR_RING_UNKNOWN_CODE, f"unknown deployment ring {target!r}")
        gate_values = dict(gates or {})
        with self._lock:
            if self._current is None:
                expected = RINGS[0]
            else:
                index = RINGS.index(self._current)
                if index + 1 >= len(RINGS):
                    _fail(
                        DR_RING_ORDER_CODE,
                        "production is already the current ring; there is no"
                        " ring to promote",
                    )
                expected = RINGS[index + 1]
            if target != expected:
                _fail(
                    DR_RING_ORDER_CODE,
                    f"only {expected!r} may be promoted next from"
                    f" {self._current!r}; refused {target!r}",
                )
            required = RING_GATES[target]
            failed = [name for name in required if gate_values.get(name) is not True]
            if failed:
                _fail(
                    DR_RING_PROMOTION_BLOCKED_CODE,
                    f"ring {target!r} is blocked by gates: " + ", ".join(failed),
                )
            if target == "production":
                owner_clean = owner.strip() if isinstance(owner, str) else ""
                if not owner_approved or not owner_clean:
                    _fail(
                        DR_PRODUCTION_APPROVAL_REQUIRED_CODE,
                        "promotion to production requires explicit owner approval",
                    )
            previous = self._deployed.get(target)
            self._deployed[target] = rev
            self._current = target
            record = {
                "action": "promote",
                "ring": target,
                "revision": rev,
                "previous_revision": previous,
                "gates": gate_values,
                "owner": (owner.strip() if isinstance(owner, str) else None),
                "owner_approved": bool(owner_approved),
                "actor": who,
                "at": self._stamp(),
            }
            return self._record(record, who)

    def rollout(
        self,
        revision: str,
        *,
        ring: str,
        actor: str | None = None,
        owner_approved: bool = False,
    ) -> dict[str, Any]:
        """Deploy a revision to one already-entered ring, never the next one."""
        rev = _text(revision, "revision", max_len=200)
        target = _text(ring, "ring", max_len=40)
        who = _text(actor or self._actor, "actor", max_len=200)
        if target not in RINGS:
            _fail(DR_RING_UNKNOWN_CODE, f"unknown deployment ring {target!r}")
        with self._lock:
            if self._current is None or RINGS.index(target) > RINGS.index(
                self._current
            ):
                _fail(
                    DR_RING_ORDER_CODE,
                    f"ring {target!r} has not been promoted; a ring-scoped"
                    " rollout cannot skip a promotion gate",
                )
            if target == "production" and not owner_approved:
                _fail(
                    DR_PRODUCTION_APPROVAL_REQUIRED_CODE,
                    "a production rollout requires explicit owner approval",
                )
            previous = self._deployed.get(target)
            self._deployed[target] = rev
            record = {
                "action": "rollout",
                "ring": target,
                "revision": rev,
                "previous_revision": previous,
                "owner_approved": bool(owner_approved),
                "actor": who,
                "at": self._stamp(),
            }
            return self._record(record, who)

    def canary(
        self,
        revision: str,
        *,
        ring: str,
        percent: int,
        healthy: bool,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record a canary result; an unhealthy canary rolls the ring back."""
        rev = _text(revision, "revision", max_len=200)
        target = _text(ring, "ring", max_len=40)
        who = _text(actor or self._actor, "actor", max_len=200)
        if target not in RINGS:
            _fail(DR_RING_UNKNOWN_CODE, f"unknown deployment ring {target!r}")
        if not isinstance(percent, int) or isinstance(percent, bool) or not (
            0 <= percent <= 100
        ):
            _fail(BAD_INPUT_CODE, "percent must be an integer in 0..100")
        with self._lock:
            if self._deployed.get(target) is None:
                _fail(
                    DR_RING_ORDER_CODE,
                    f"ring {target!r} has no deployed revision to canary",
                )
            record = {
                "action": "canary",
                "ring": target,
                "revision": rev,
                "percent": percent,
                "healthy": bool(healthy),
                "actor": who,
                "at": self._stamp(),
            }
            self._record(record, who)
            rollback_record = None
            if not healthy:
                rollback_record = self.rollback(
                    target,
                    actor=who,
                    reason="canary unhealthy",
                )
            return {
                "schema": CANARY_SCHEMA,
                "ok": bool(healthy),
                "canary": record,
                "rollback": rollback_record,
            }

    def rollback(
        self,
        ring: str,
        *,
        to_revision: str | None = None,
        reason: str = "operator rollback",
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Return a ring to its previous revision, or to an explicit revision."""
        target = _text(ring, "ring", max_len=40)
        who = _text(actor or self._actor, "actor", max_len=200)
        why = _text(reason, "reason", max_len=1000)
        if target not in RINGS:
            _fail(DR_RING_UNKNOWN_CODE, f"unknown deployment ring {target!r}")
        with self._lock:
            current_revision = self._deployed.get(target)
            if current_revision is None:
                _fail(
                    DR_RING_ROLLBACK_CODE,
                    f"ring {target!r} has nothing deployed to roll back",
                )
            if to_revision is not None:
                destination = _text(to_revision, "to_revision", max_len=200)
            else:
                destination = next(
                    (
                        item["previous_revision"]
                        for item in reversed(self._history)
                        if item["ring"] == target
                        and item["action"] in ("promote", "rollout")
                        and item.get("previous_revision") is not None
                    ),
                    None,
                )
                if destination is None:
                    _fail(
                        DR_RING_ROLLBACK_CODE,
                        f"ring {target!r} has no recorded previous revision;"
                        " pass to_revision explicitly",
                    )
            self._deployed[target] = destination
            record = {
                "action": "rollback",
                "ring": target,
                "revision": destination,
                "previous_revision": current_revision,
                "reason": why,
                "actor": who,
                "at": self._stamp(),
            }
            return self._record(record, who)


__all__ = [
    "CANARY_SCHEMA",
    "DR_SCHEMA",
    "INCIDENT_SCHEMA",
    "RECOVERY_SCHEMA",
    "RING_GATES",
    "RING_SCHEMA",
    "RINGS",
    "DeploymentRings",
    "KillSwitchController",
    "build_stoppers",
    "list_incidents",
    "record_incident",
    "recover_unknown",
    "resolve_incident",
    "restore_snapshot",
    "run_restore_drill",
]
