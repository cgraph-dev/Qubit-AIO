"""Human approval gates.

An approval is a durable pause, not a prompt suggestion. Requesting one moves
the linked job to ``waiting_approval`` and releases its worker lease, so no
worker can keep mutating things while a human decides. Approving returns the
job to ``queued`` (it still has to be claimed); rejecting or cancelling ends
it. A second decision on a decided approval is a typed refusal.
"""
from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING, Any

from . import models
from .errors import (
    APPROVAL_NOT_PENDING_CODE,
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

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled")
DECISIONS = ("approved", "rejected", "cancelled")


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _row(
    conn: sqlite3.Connection, tenant_id: str, approval_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_approval WHERE tenant_id=? AND approval_id=?",
        (tenant_id, approval_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no approval {approval_id!r} in tenant {tenant_id!r}",
        )
    return row


def request(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    action: str,
    requested_by: str,
    idempotency_key: str,
    job_id: str | None = None,
    payload: Any = None,
    policy_version: str = "v1",
    expires_at: float | None = None,
) -> tuple[models.Approval, bool]:
    """Create a pending approval; a replayed key returns the original."""
    action_clean = _text(action, "approval action", max_len=200)
    actor = _text(requested_by, "requested_by", max_len=200)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    policy = _text(policy_version, "policy version", max_len=80)
    body = {} if payload is None else payload
    request_hash = fingerprint(
        {"action": action_clean, "job_id": job_id, "payload": body}
    )
    approval_id = new_id("apr")
    created = now_iso()
    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        existing = conn.execute(
            "SELECT * FROM bos_approval WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if (
                existing["action"] != action_clean
                or existing["request_hash"] != request_hash
            ):
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " approval request",
                )
            return models.Approval.from_row(existing), False
        if job_id is not None:
            job = conn.execute(
                "SELECT * FROM bos_job WHERE tenant_id=? AND business_id=?"
                " AND job_id=?",
                (tenant_id, business_id, job_id),
            ).fetchone()
            if job is None:
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"no job {job_id!r} for this tenant/business",
                )
            if job["status"] not in ("queued", "running"):
                raise BusinessError(
                    BAD_STATE_CODE,
                    "an approval can only pause a queued or running job"
                    f" (status={job['status']!r})",
                )
        try:
            conn.execute(
                "INSERT INTO bos_approval(approval_id, tenant_id, business_id,"
                " job_id, action, status, requested_by, reason, idempotency_key,"
                " request_hash, policy_version, payload_json, created_at,"
                " expires_at) VALUES(?,?,?,?,?,'pending',?,'',?,?,?,?,?,?)",
                (
                    approval_id,
                    tenant_id,
                    business_id,
                    job_id,
                    action_clean,
                    actor,
                    key,
                    request_hash,
                    policy,
                    json_dumps(redact(body)),
                    created,
                    expires_at,
                ),
            )
        except sqlite3.IntegrityError as exc:  # concurrent identical request
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} is already in use",
            ) from exc
        if job_id is not None:
            from . import jobs

            jobs._transition(
                conn,
                store,
                tenant_id,
                job_id,
                "waiting_approval",
                actor=actor,
                note=f"approval {approval_id} requested",
            )
        store._audit(
            conn, tenant_id, business_id, actor, "approval.request", "approval",
            approval_id, "ok",
            {"action": action_clean, "job_id": job_id, "policy_version": policy},
        )
    return get(store, tenant_id, approval_id), True


def get(
    store: "BusinessStore", tenant_id: str, approval_id: str
) -> models.Approval:
    with store.read() as conn:
        return models.Approval.from_row(_row(conn, tenant_id, approval_id))


def list_approvals(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    status: str | None = None,
) -> list[models.Approval]:
    if status is not None and status not in APPROVAL_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown approval status {status!r}")
    query = "SELECT * FROM bos_approval WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if status is not None:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, approval_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.Approval.from_row(row) for row in rows]


def decide(
    store: "BusinessStore",
    *,
    tenant_id: str,
    approval_id: str,
    decision: str,
    decided_by: str,
    reason: str = "",
) -> models.Approval:
    """Decide a pending approval and resume/terminate the linked job."""
    target = _text(decision, "decision", max_len=20)
    if target not in DECISIONS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"decision must be one of {', '.join(DECISIONS)}",
        )
    actor = _text(decided_by, "decided_by", max_len=200)
    note = str(reason or "").strip()[:2000]
    with store.transaction() as conn:
        row = _row(conn, tenant_id, approval_id)
        if row["status"] != "pending":
            raise BusinessError(
                APPROVAL_NOT_PENDING_CODE,
                f"approval {approval_id!r} is already {row['status']!r}",
            )
        job_id = row["job_id"]
        if job_id is not None:
            job = conn.execute(
                "SELECT * FROM bos_job WHERE tenant_id=? AND job_id=?",
                (tenant_id, job_id),
            ).fetchone()
            if job is None or job["status"] != "waiting_approval":
                raise BusinessError(
                    BAD_STATE_CODE,
                    "the linked job is no longer waiting for this approval"
                    f" (job status={None if job is None else job['status']!r})",
                )
        decided = now_iso()
        conn.execute(
            "UPDATE bos_approval SET status=?, decided_by=?, reason=?,"
            " decided_at=? WHERE tenant_id=? AND approval_id=?",
            (target, actor, note, decided, tenant_id, approval_id),
        )
        if job_id is not None:
            from . import jobs

            jobs._transition(
                conn,
                store,
                tenant_id,
                job_id,
                "queued" if target == "approved" else "cancelled",
                actor=actor,
                note=f"approval {approval_id} {target}: {note}".strip(),
            )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "approval.decide",
            "approval", approval_id, "ok",
            {"decision": target, "job_id": job_id, "reason": note},
        )
    return get(store, tenant_id, approval_id)


def expire(
    store: "BusinessStore",
    *,
    now: float | None = None,
    tenant_id: str | None = None,
) -> list[models.Approval]:
    """Expire pending approvals whose deadline has passed.

    An expired approval cancels its linked job: the window for the action
    closed, so continuing would be a decision nobody made.
    """
    moment = time.time() if now is None else float(now)
    ids: list[tuple[str, str]] = []
    with store.read() as conn:
        query = (
            "SELECT tenant_id, approval_id FROM bos_approval"
            " WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < ?"
        )
        params: list[Any] = [moment]
        if tenant_id is not None:
            query += " AND tenant_id=?"
            params.append(tenant_id)
        query += " ORDER BY created_at, approval_id"
        ids = [(row["tenant_id"], row["approval_id"]) for row in conn.execute(query, params)]
    expired: list[models.Approval] = []
    for scope, approval_id in ids:
        with store.transaction() as conn:
            row = _row(conn, scope, approval_id)
            if row["status"] != "pending":
                continue
            decided = now_iso()
            conn.execute(
                "UPDATE bos_approval SET status='expired', decided_by='system',"
                " reason=?, decided_at=? WHERE tenant_id=? AND approval_id=?",
                ("approval window expired", decided, scope, approval_id),
            )
            job_id = row["job_id"]
            if job_id is not None:
                from . import jobs

                job = conn.execute(
                    "SELECT * FROM bos_job WHERE tenant_id=? AND job_id=?",
                    (scope, job_id),
                ).fetchone()
                if job is not None and job["status"] == "waiting_approval":
                    jobs._transition(
                        conn, store, scope, job_id, "cancelled",
                        actor="system", note=f"approval {approval_id} expired",
                    )
            store._audit(
                conn, scope, row["business_id"], "system", "approval.expire",
                "approval", approval_id, "ok", {"job_id": job_id},
            )
        expired.append(get(store, scope, approval_id))
    return expired
