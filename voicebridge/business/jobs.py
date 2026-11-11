"""Durable jobs and their explicit state machine.

A job is a unit of work whose outcome matters: sending an invoice, calling a
provider, producing an artifact. The control plane refuses to guess:

* A worker *claims* a queued job with a lease. Only one claim can win.
* A crash leaves the row ``running`` with an expired lease. On restart,
  :func:`recover` moves it to ``unknown`` — never silently back to ``queued``
  and never blindly retried. A human or reconciler decides what happened.
* ``unknown`` is a first-class state: money/email/CRM writes must be
  reconciled against the provider before they are repeated.
* Terminal states (``completed``/``failed``/``cancelled``) have no outgoing
  transitions. Re-running one is a typed refusal, not a second effect.
"""
from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING, Any, Mapping

from . import models
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    NOT_FOUND_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, now_iso, redact

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

JOB_STATUSES = (
    "created",
    "queued",
    "running",
    "waiting_approval",
    "unknown",
    "failed",
    "completed",
    "cancelled",
)
TERMINAL_STATUSES = frozenset({"failed", "completed", "cancelled"})
#: ``unknown -> queued`` is deliberately present but only reachable through
#: :func:`requeue_unknown`, an explicit reconciliation decision. It is never
#: taken by a generic transition or a retry loop.
JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset(
        {"waiting_approval", "unknown", "failed", "completed", "cancelled"}
    ),
    "waiting_approval": frozenset(
        {"queued", "running", "failed", "cancelled"}
    ),
    "unknown": frozenset({"queued", "completed", "failed", "cancelled"}),
    "failed": frozenset(),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _job_row(conn: sqlite3.Connection, tenant_id: str, job_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_job WHERE tenant_id=? AND job_id=?",
        (tenant_id, job_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE, f"no job {job_id!r} in tenant {tenant_id!r}"
        )
    return row


def _event(
    conn: sqlite3.Connection,
    tenant_id: str,
    job_id: str,
    kind: str,
    *,
    actor: str,
    detail: Mapping[str, Any] | None = None,
) -> str:
    seq = int(
        conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM bos_job_event WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
    )
    event_id = new_id("jev")
    conn.execute(
        "INSERT INTO bos_job_event(event_id, tenant_id, job_id, seq, kind, actor,"
        " detail_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            event_id,
            tenant_id,
            job_id,
            seq,
            str(kind),
            str(actor or "system"),
            json_dumps(redact(dict(detail or {}))),
            now_iso(),
        ),
    )
    return event_id


def create(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    kind: str,
    idempotency_key: str,
    input: Any = None,
    actor: str = "system",
) -> tuple[models.JobRun, bool]:
    """Create a job at ``created``; a replayed key returns the original.

    Returns ``(job, created)``. If the same key arrives with a different kind
    or request body the call is a typed ``BUSINESS_IDEMPOTENCY_CONFLICT`` — a
    key identifies one request, not "whatever I sent twice".
    """
    kind_clean = _text(kind, "job kind", max_len=120)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    body = {} if input is None else input
    request_hash = fingerprint({"kind": kind_clean, "input": body})
    job_id = new_id("job")
    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        existing = conn.execute(
            "SELECT * FROM bos_job WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if (
                existing["kind"] != kind_clean
                or existing["request_hash"] != request_hash
            ):
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " job request",
                )
            return models.JobRun.from_row(existing), False
        created_at = now_iso()
        try:
            conn.execute(
                "INSERT INTO bos_job(job_id, tenant_id, business_id, kind, status,"
                " idempotency_key, request_hash, input_json, attempt, created_at,"
                " updated_at) VALUES(?,?,?,?,'created',?,?,?,0,?,?)",
                (
                    job_id,
                    tenant_id,
                    business_id,
                    kind_clean,
                    key,
                    request_hash,
                    json_dumps(redact(body)),
                    created_at,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:  # concurrent identical create
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} is already in use",
            ) from exc
        _event(
            conn, tenant_id, job_id, "job.created", actor=actor,
            detail={"kind": kind_clean},
        )
        store._audit(
            conn, tenant_id, business_id, actor, "job.create", "job", job_id,
            "ok", {"kind": kind_clean, "idempotency_key": key},
        )
    return get(store, tenant_id, job_id), True


def get(store: "BusinessStore", tenant_id: str, job_id: str) -> models.JobRun:
    with store.read() as conn:
        return models.JobRun.from_row(_job_row(conn, tenant_id, job_id))


def list_jobs(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    status: str | None = None,
) -> list[models.JobRun]:
    if status is not None and status not in JOB_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown job status {status!r}")
    query = "SELECT * FROM bos_job WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if status is not None:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, job_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.JobRun.from_row(row) for row in rows]


def list_events(
    store: "BusinessStore", tenant_id: str, job_id: str
) -> list[models.JobEvent]:
    with store.read() as conn:
        _job_row(conn, tenant_id, job_id)
        rows = conn.execute(
            "SELECT * FROM bos_job_event WHERE tenant_id=? AND job_id=?"
            " ORDER BY seq",
            (tenant_id, job_id),
        ).fetchall()
    return [models.JobEvent.from_row(row) for row in rows]


def _transition(
    conn: sqlite3.Connection,
    store: "BusinessStore",
    tenant_id: str,
    job_id: str,
    target: str,
    *,
    actor: str,
    note: str = "",
    output: Any = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    lease_owner: str | None = None,
    lease_expires_at: float | None = None,
    attempt: int | None = None,
    started_at: str | None = None,
) -> models.JobRun:
    row = _job_row(conn, tenant_id, job_id)
    current = row["status"]
    if target not in JOB_TRANSITIONS[current]:
        raise BusinessError(
            BAD_STATE_CODE,
            f"job cannot move {current!r} -> {target!r}",
        )
    updated = now_iso()
    assignments = ["status=?", "updated_at=?"]
    params: list[Any] = [target, updated]
    if output is not None:
        assignments.append("output_json=?")
        params.append(json_dumps(redact(output)))
    if target in TERMINAL_STATUSES:
        assignments.extend(["finished_at=?", "lease_owner=NULL", "lease_expires_at=NULL"])
        params.append(updated)
    elif target in {"waiting_approval", "queued", "unknown"}:
        assignments.extend(["lease_owner=NULL", "lease_expires_at=NULL"])
    if lease_owner is not None:
        assignments.append("lease_owner=?")
        params.append(lease_owner)
    if lease_expires_at is not None:
        assignments.append("lease_expires_at=?")
        params.append(float(lease_expires_at))
    if attempt is not None:
        assignments.append("attempt=?")
        params.append(int(attempt))
    if started_at is not None:
        assignments.append("started_at=COALESCE(started_at, ?)")
        params.append(started_at)
    if error_code is not None:
        assignments.append("last_error_code=?")
        params.append(error_code)
    if error_detail is not None:
        assignments.append("last_error_detail=?")
        params.append(redact(error_detail))
    params.extend([tenant_id, job_id])
    conn.execute(
        f"UPDATE bos_job SET {', '.join(assignments)}"
        " WHERE tenant_id=? AND job_id=?",
        params,
    )
    _event(
        conn, tenant_id, job_id, f"job.{target}", actor=actor,
        detail={"from": current, "to": target, "note": note},
    )
    store._audit(
        conn, tenant_id, row["business_id"], actor, "job.transition", "job",
        job_id, "ok", {"from": current, "to": target, "note": note},
    )
    return models.JobRun.from_row(_job_row(conn, tenant_id, job_id))


def enqueue(
    store: "BusinessStore", tenant_id: str, job_id: str, *, actor: str = "system"
) -> models.JobRun:
    with store.transaction() as conn:
        return _transition(
            conn, store, tenant_id, job_id, "queued", actor=actor,
            note="enqueued",
        )


def claim(
    store: "BusinessStore",
    tenant_id: str,
    job_id: str,
    *,
    worker: str,
    lease_seconds: float = 300.0,
    actor: str = "system",
    now: float | None = None,
) -> models.JobRun:
    """Claim a queued job for ``worker``; exactly one claim can win."""
    owner = _text(worker, "worker", max_len=200)
    if lease_seconds <= 0:
        raise BusinessError(BAD_INPUT_CODE, "lease_seconds must be positive")
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _job_row(conn, tenant_id, job_id)
        if row["status"] != "queued":
            raise BusinessError(
                BAD_STATE_CODE,
                f"only a queued job can be claimed (status={row['status']!r})",
            )
        return _transition(
            conn,
            store,
            tenant_id,
            job_id,
            "running",
            actor=actor,
            note=f"claimed by {owner}",
            lease_owner=owner,
            lease_expires_at=moment + float(lease_seconds),
            attempt=int(row["attempt"]) + 1,
            started_at=now_iso(),
        )


def heartbeat(
    store: "BusinessStore",
    tenant_id: str,
    job_id: str,
    *,
    worker: str,
    lease_seconds: float = 300.0,
    now: float | None = None,
) -> models.JobRun:
    owner = _text(worker, "worker", max_len=200)
    if lease_seconds <= 0:
        raise BusinessError(BAD_INPUT_CODE, "lease_seconds must be positive")
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _job_row(conn, tenant_id, job_id)
        if row["status"] != "running":
            raise BusinessError(
                BAD_STATE_CODE,
                f"only a running job can heartbeat (status={row['status']!r})",
            )
        if row["lease_owner"] != owner:
            raise BusinessError(
                BAD_STATE_CODE,
                f"job is leased to {row['lease_owner']!r}, not {owner!r}",
            )
        conn.execute(
            "UPDATE bos_job SET lease_expires_at=?, updated_at=?"
            " WHERE tenant_id=? AND job_id=?",
            (moment + float(lease_seconds), now_iso(), tenant_id, job_id),
        )
    return get(store, tenant_id, job_id)


def finish(
    store: "BusinessStore",
    tenant_id: str,
    job_id: str,
    status: str,
    *,
    actor: str,
    output: Any = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> models.JobRun:
    """Finish a running/waiting/unknown job as completed/failed/cancelled."""
    target = _text(status, "job status", max_len=20)
    if target not in TERMINAL_STATUSES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"finish status must be one of {', '.join(sorted(TERMINAL_STATUSES))}",
        )
    with store.transaction() as conn:
        return _transition(
            conn,
            store,
            tenant_id,
            job_id,
            target,
            actor=actor,
            note="finished",
            output=output,
            error_code=error_code,
            error_detail=error_detail,
        )


def mark_unknown(
    store: "BusinessStore",
    tenant_id: str,
    job_id: str,
    *,
    actor: str,
    reason: str,
) -> models.JobRun:
    with store.transaction() as conn:
        return _transition(
            conn,
            store,
            tenant_id,
            job_id,
            "unknown",
            actor=actor,
            note="outcome unknown; reconcile before retrying",
            error_code="JOB_OUTCOME_UNKNOWN",
            error_detail=reason,
        )


def requeue_unknown(
    store: "BusinessStore",
    tenant_id: str,
    job_id: str,
    *,
    actor: str,
    note: str,
) -> models.JobRun:
    """Explicit reconciliation decision: ``unknown`` -> ``queued``."""
    with store.transaction() as conn:
        row = _job_row(conn, tenant_id, job_id)
        if row["status"] != "unknown":
            raise BusinessError(
                BAD_STATE_CODE,
                f"only an unknown job can be requeued by hand (status={row['status']!r})",
            )
        return _transition(
            conn, store, tenant_id, job_id, "queued", actor=actor, note=note
        )


def recover(
    store: "BusinessStore",
    *,
    now: float | None = None,
    actor: str = "system",
) -> list[models.JobRun]:
    """Restart sweep: expired ``running`` jobs become ``unknown``.

    A job whose lease is still valid is left untouched — another process may
    legitimately still own it. Jobs in ``created``/``queued`` are safe to
    claim and are not touched. Nothing is retried automatically.
    """
    moment = time.time() if now is None else float(now)
    recovered: list[models.JobRun] = []
    with store.transaction() as conn:
        rows = conn.execute(
            "SELECT tenant_id, job_id FROM bos_job WHERE status='running'"
            " AND (lease_expires_at IS NULL OR lease_expires_at < ?)"
            " ORDER BY created_at, job_id",
            (moment,),
        ).fetchall()
        for row in rows:
            recovered.append(
                _transition(
                    conn,
                    store,
                    row["tenant_id"],
                    row["job_id"],
                    "unknown",
                    actor=actor,
                    note="lease expired during restart; outcome must be reconciled",
                    error_code="JOB_OUTCOME_UNKNOWN",
                    error_detail="worker lease expired before completion",
                )
            )
        for tenant_id in sorted({item.tenant_id for item in recovered}):
            store._audit(
                conn, tenant_id, None, actor, "recovery.sweep", "job", None, "ok",
                {
                    "unknown": [
                        item.job_id
                        for item in recovered
                        if item.tenant_id == tenant_id
                    ]
                },
            )
    return recovered
