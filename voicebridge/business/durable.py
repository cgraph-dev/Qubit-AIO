"""Local durable substrate over the existing Business OS SQLite plane.

This module is the **prototype** the Phase 0 plan (P0-09) asks for: run
checkpoints and a transactional outbox, both on the one ``business.db`` the
control plane already owns. It does not create a second database and it is not
a second execution engine — jobs (``jobs.py``) remain the run state machine,
the ledger remains the money truth, and the audit log remains the append-only
record of everything that happened.

Two records live here:

* :class:`Checkpoint` — an append-only recovery point for a run. ``sequence``
  is dense per run, ``(run_id, sequence)`` is unique, and a duplicate slot is a
  typed refusal: a checkpoint is never rewritten. Resume reads the ordered log
  and the carried ``workspace_revision`` / ``policy_revision``.
* :class:`OutboxMessage` — one intended external effect, keyed by
  ``(tenant_id, idempotency_key)``. Replaying the same key with the same
  request hash returns the existing row; a different hash is a typed conflict.
  A delivery moves ``pending -> claimed -> sent|unknown|dead``. ``unknown`` is
  a first-class outcome and is only left through :func:`reconcile_outbox`, so a
  crash can never be recorded as a silent success.

Temporal stays deferred (owner decision recorded in
``docs/QUBIT-OFFICE-PHASE-0.md`` §1): it is added as an adapter only when a
measured multi-worker/HA need justifies it, and it must pass these same crash,
duplicate, unknown and restore tests first.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import (
    BAD_INPUT_CODE,
    CHECKPOINT_EXISTS_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    NOT_FOUND_CODE,
    OUTBOX_BAD_STATE_CODE,
    OUTBOX_LEASE_LOST_CODE,
    OUTBOX_NOT_FOUND_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, json_loads, now_iso, redact

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

OUTBOX_STATUSES = ("pending", "claimed", "sent", "unknown", "dead")
#: ``pending`` is the retryable state. ``unknown`` is deliberately **not**
#: claimable: a human/reconciler decides its outcome first (``reconcile_outbox``)
#: or marks it dead. Claiming an unknown row would be a blind retry of an
#: effect that may already have happened.
CLAIMABLE_STATUSES = frozenset({"pending"})
TERMINAL_OUTBOX_STATUSES = frozenset({"sent", "dead"})
RECONCILE_OUTCOMES = ("sent", "dead")


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be an integer >= 1")
    return int(value)


def _receipt_text(value: Any) -> str | None:
    """Store a provider receipt as JSON (or bounded text), redacted first."""
    if value is None:
        return None
    if isinstance(value, str):
        return redact(value)[:20000]
    return json_dumps(redact(value))


def _receipt_value(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json_loads(value, value)
    except (TypeError, ValueError):  # pragma: no cover - json_loads is total
        return value


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    tenant_id: str
    business_id: str
    run_id: str
    job_id: str | None
    sequence: int
    state: Any
    pending_actions: Any
    tool_receipts: Any
    artifact_ids: Any
    workspace_revision: str | None
    policy_revision: str | None
    request_hash: str
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Checkpoint":
        data = dict(row)
        return cls(
            checkpoint_id=data["checkpoint_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            run_id=data["run_id"],
            job_id=data["job_id"],
            sequence=int(data["sequence"]),
            state=json_loads(data.get("state_json"), {}),
            pending_actions=json_loads(data.get("pending_actions_json"), []),
            tool_receipts=json_loads(data.get("tool_receipts_json"), []),
            artifact_ids=json_loads(data.get("artifact_ids_json"), []),
            workspace_revision=data["workspace_revision"],
            policy_revision=data["policy_revision"],
            request_hash=data["request_hash"],
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    outbox_id: str
    tenant_id: str
    business_id: str
    aggregate_id: str
    kind: str
    idempotency_key: str
    request_hash: str
    payload: Any
    status: str
    attempts: int
    lease_owner: str | None
    lease_expires_at: float | None
    provider_receipt: Any
    last_error: str | None
    created_at: str
    updated_at: str
    sent_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> "OutboxMessage":
        data = dict(row)
        return cls(
            outbox_id=data["outbox_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            aggregate_id=data["aggregate_id"],
            kind=data["kind"],
            idempotency_key=data["idempotency_key"],
            request_hash=data["request_hash"],
            payload=json_loads(data.get("payload_json"), {}),
            status=data["status"],
            attempts=int(data["attempts"]),
            lease_owner=data["lease_owner"],
            lease_expires_at=(
                float(data["lease_expires_at"])
                if data["lease_expires_at"] is not None
                else None
            ),
            provider_receipt=_receipt_value(data.get("provider_receipt")),
            last_error=data["last_error"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            sent_at=data["sent_at"],
        )


# -- checkpoints -----------------------------------------------------------
def _checkpoint_row(
    conn: sqlite3.Connection, tenant_id: str, run_id: str, sequence: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM bos_checkpoint WHERE tenant_id=? AND run_id=? AND sequence=?",
        (tenant_id, run_id, int(sequence)),
    ).fetchone()


def record_checkpoint(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    run_id: str,
    sequence: int,
    *,
    state: Any,
    pending_actions: Any = None,
    tool_receipts: Any = None,
    artifact_ids: Any = None,
    workspace_revision: str | None = None,
    policy_revision: str | None = None,
    actor: str,
    job_id: str | None = None,
) -> Checkpoint:
    """Append one checkpoint; ``(run_id, sequence)`` may never be reused."""
    run = _text(run_id, "run_id", max_len=200)
    seq = _positive_int(sequence, "sequence")
    job = _optional_text(job_id, "job_id", max_len=200)
    ws_rev = _optional_text(workspace_revision, "workspace_revision", max_len=200)
    pol_rev = _optional_text(policy_revision, "policy_revision", max_len=200)
    body = {} if state is None else state
    actions = [] if pending_actions is None else pending_actions
    receipts = [] if tool_receipts is None else tool_receipts
    artifacts = [] if artifact_ids is None else artifact_ids
    digest = fingerprint(
        {
            "run_id": run,
            "sequence": seq,
            "state": body,
            "pending_actions": actions,
            "tool_receipts": receipts,
            "artifact_ids": artifacts,
            "workspace_revision": ws_rev,
            "policy_revision": pol_rev,
        }
    )
    checkpoint_id = new_id("ckp")
    created = now_iso()
    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        if job is not None:
            found = conn.execute(
                "SELECT 1 FROM bos_job WHERE tenant_id=? AND business_id=?"
                " AND job_id=?",
                (tenant_id, business_id, job),
            ).fetchone()
            if found is None:
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"no job {job!r} for this tenant/business",
                )
        if _checkpoint_row(conn, tenant_id, run, seq) is not None:
            raise BusinessError(
                CHECKPOINT_EXISTS_CODE,
                f"run {run!r} already has a checkpoint at sequence {seq}",
            )
        try:
            conn.execute(
                "INSERT INTO bos_checkpoint(checkpoint_id, tenant_id, business_id,"
                " run_id, job_id, sequence, state_json, pending_actions_json,"
                " tool_receipts_json, artifact_ids_json, workspace_revision,"
                " policy_revision, request_hash, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    checkpoint_id,
                    tenant_id,
                    business_id,
                    run,
                    job,
                    seq,
                    json_dumps(redact(body)),
                    json_dumps(redact(actions)),
                    json_dumps(redact(receipts)),
                    json_dumps(redact(artifacts)),
                    ws_rev,
                    pol_rev,
                    digest,
                    created,
                ),
            )
        except sqlite3.IntegrityError as exc:  # concurrent identical append
            raise BusinessError(
                CHECKPOINT_EXISTS_CODE,
                f"run {run!r} already has a checkpoint at sequence {seq}",
            ) from exc
        store._audit(
            conn, tenant_id, business_id, actor, "checkpoint.record", "checkpoint",
            checkpoint_id, "ok",
            {"run_id": run, "sequence": seq, "request_hash": digest},
        )
        row = conn.execute(
            "SELECT * FROM bos_checkpoint WHERE tenant_id=? AND checkpoint_id=?",
            (tenant_id, checkpoint_id),
        ).fetchone()
    return Checkpoint.from_row(row)


def list_checkpoints(
    store: "BusinessStore", tenant_id: str, run_id: str
) -> list[Checkpoint]:
    """Every checkpoint for one run, ordered by ``sequence`` (oldest first)."""
    run = _text(run_id, "run_id", max_len=200)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM bos_checkpoint WHERE tenant_id=? AND run_id=?"
            " ORDER BY sequence, checkpoint_id",
            (tenant_id, run),
        ).fetchall()
    return [Checkpoint.from_row(row) for row in rows]


# -- outbox ----------------------------------------------------------------
def _outbox_row(
    conn: sqlite3.Connection, tenant_id: str, outbox_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_outbox WHERE tenant_id=? AND outbox_id=?",
        (tenant_id, outbox_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            OUTBOX_NOT_FOUND_CODE,
            f"no outbox message {outbox_id!r} in tenant {tenant_id!r}",
        )
    return row


def _outbox(store: "BusinessStore", tenant_id: str, outbox_id: str) -> OutboxMessage:
    with store.read() as conn:
        return OutboxMessage.from_row(_outbox_row(conn, tenant_id, outbox_id))


def _require_lease(row: sqlite3.Row, worker: str, moment: float) -> None:
    if row["status"] != "claimed":
        raise BusinessError(
            OUTBOX_BAD_STATE_CODE,
            f"outbox message is {row['status']!r}, not 'claimed'",
        )
    if row["lease_owner"] != worker:
        raise BusinessError(
            OUTBOX_LEASE_LOST_CODE,
            f"outbox message is leased to {row['lease_owner']!r}, not {worker!r}",
        )
    expires = row["lease_expires_at"]
    if expires is None or float(expires) < moment:
        raise BusinessError(
            OUTBOX_LEASE_LOST_CODE,
            "outbox lease has expired; recover and reconcile before recording"
            " an effect",
        )


def get_outbox(
    store: "BusinessStore", tenant_id: str, outbox_id: str
) -> OutboxMessage:
    """Read one outbox message inside its tenant scope."""
    return _outbox(store, tenant_id, outbox_id)


def enqueue_outbox(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    aggregate_id: str,
    kind: str,
    payload: Any,
    *,
    idempotency_key: str,
    actor: str,
) -> OutboxMessage:
    """Enqueue one intended effect; replaying the key is one row, not two."""
    aggregate = _text(aggregate_id, "aggregate_id", max_len=200)
    kind_clean = _text(kind, "outbox kind", max_len=120)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    body = {} if payload is None else payload
    digest = fingerprint(
        {"aggregate_id": aggregate, "kind": kind_clean, "payload": body}
    )
    outbox_id = new_id("obx")
    created = now_iso()
    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        existing = conn.execute(
            "SELECT * FROM bos_outbox WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != digest:
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " outbox payload",
                )
            return OutboxMessage.from_row(existing)
        try:
            conn.execute(
                "INSERT INTO bos_outbox(outbox_id, tenant_id, business_id,"
                " aggregate_id, kind, idempotency_key, request_hash, payload_json,"
                " status, attempts, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,'pending',0,?,?)",
                (
                    outbox_id,
                    tenant_id,
                    business_id,
                    aggregate,
                    kind_clean,
                    key,
                    digest,
                    json_dumps(redact(body)),
                    created,
                    created,
                ),
            )
        except sqlite3.IntegrityError as exc:  # concurrent identical enqueue
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} is already in use",
            ) from exc
        store._audit(
            conn, tenant_id, business_id, actor, "outbox.enqueue", "outbox",
            outbox_id, "ok",
            {
                "aggregate_id": aggregate,
                "kind": kind_clean,
                "idempotency_key": key,
                "request_hash": digest,
            },
        )
        row = conn.execute(
            "SELECT * FROM bos_outbox WHERE tenant_id=? AND outbox_id=?",
            (tenant_id, outbox_id),
        ).fetchone()
    return OutboxMessage.from_row(row)


def claim_outbox(
    store: "BusinessStore",
    tenant_id: str,
    outbox_id: str,
    *,
    worker: str,
    lease_seconds: float,
    now: float | None = None,
) -> OutboxMessage:
    """Claim a ``pending`` message; exactly one caller can win."""
    owner = _text(worker, "worker", max_len=200)
    if lease_seconds <= 0:
        raise BusinessError(BAD_INPUT_CODE, "lease_seconds must be positive")
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _outbox_row(conn, tenant_id, outbox_id)
        if row["status"] not in CLAIMABLE_STATUSES:
            raise BusinessError(
                OUTBOX_BAD_STATE_CODE,
                f"outbox message is {row['status']!r}; only 'pending' is"
                " claimable (unknown must be reconciled first)",
            )
        changed = conn.execute(
            "UPDATE bos_outbox SET status='claimed', attempts=attempts + 1,"
            " lease_owner=?, lease_expires_at=?, updated_at=?"
            " WHERE tenant_id=? AND outbox_id=? AND status='pending'",
            (
                owner,
                moment + float(lease_seconds),
                now_iso(moment),
                tenant_id,
                outbox_id,
            ),
        ).rowcount
        if changed != 1:  # pragma: no cover - BEGIN IMMEDIATE serialises writers
            raise BusinessError(
                OUTBOX_BAD_STATE_CODE,
                "outbox claim lost to another worker",
            )
        store._audit(
            conn, tenant_id, row["business_id"], owner, "outbox.claim", "outbox",
            outbox_id, "ok",
            {"attempt": int(row["attempts"]) + 1, "lease_seconds": lease_seconds},
        )
    return _outbox(store, tenant_id, outbox_id)


def heartbeat_outbox(
    store: "BusinessStore",
    tenant_id: str,
    outbox_id: str,
    *,
    worker: str,
    lease_seconds: float,
    now: float | None = None,
) -> OutboxMessage:
    """Extend a held lease; a non-owner or an expired lease is refused."""
    owner = _text(worker, "worker", max_len=200)
    if lease_seconds <= 0:
        raise BusinessError(BAD_INPUT_CODE, "lease_seconds must be positive")
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _outbox_row(conn, tenant_id, outbox_id)
        _require_lease(row, owner, moment)
        conn.execute(
            "UPDATE bos_outbox SET lease_expires_at=?, updated_at=?"
            " WHERE tenant_id=? AND outbox_id=?",
            (moment + float(lease_seconds), now_iso(moment), tenant_id, outbox_id),
        )
    return _outbox(store, tenant_id, outbox_id)


def mark_outbox_sent(
    store: "BusinessStore",
    tenant_id: str,
    outbox_id: str,
    *,
    worker: str,
    provider_receipt: Any,
    now: float | None = None,
) -> OutboxMessage:
    """Record a real effect and its provider receipt under a live lease."""
    owner = _text(worker, "worker", max_len=200)
    receipt = _receipt_text(provider_receipt)
    if not receipt:
        raise BusinessError(
            BAD_INPUT_CODE,
            "a sent outbox message needs a provider receipt; a bare success is"
            " not evidence",
        )
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _outbox_row(conn, tenant_id, outbox_id)
        _require_lease(row, owner, moment)
        stamp = now_iso(moment)
        conn.execute(
            "UPDATE bos_outbox SET status='sent', provider_receipt=?,"
            " lease_owner=NULL, lease_expires_at=NULL, sent_at=?, updated_at=?,"
            " last_error=NULL WHERE tenant_id=? AND outbox_id=?",
            (receipt, stamp, stamp, tenant_id, outbox_id),
        )
        store._audit(
            conn, tenant_id, row["business_id"], owner, "outbox.sent", "outbox",
            outbox_id, "ok", {"attempts": int(row["attempts"])},
        )
    return _outbox(store, tenant_id, outbox_id)


def mark_outbox_unknown(
    store: "BusinessStore",
    tenant_id: str,
    outbox_id: str,
    *,
    worker: str,
    note: str = "",
    now: float | None = None,
) -> OutboxMessage:
    """Park a delivery whose outcome cannot be proven; never retry blindly."""
    owner = _text(worker, "worker", max_len=200)
    moment = time.time() if now is None else float(now)
    detail = redact(str(note or ""))[:2000]
    with store.transaction() as conn:
        row = _outbox_row(conn, tenant_id, outbox_id)
        _require_lease(row, owner, moment)
        conn.execute(
            "UPDATE bos_outbox SET status='unknown', last_error=?,"
            " lease_owner=NULL, lease_expires_at=NULL, updated_at=?"
            " WHERE tenant_id=? AND outbox_id=?",
            (
                detail or "outcome unknown; reconcile before retrying",
                now_iso(moment),
                tenant_id,
                outbox_id,
            ),
        )
        store._audit(
            conn, tenant_id, row["business_id"], owner, "outbox.unknown", "outbox",
            outbox_id, "ok", {"note": detail},
        )
    return _outbox(store, tenant_id, outbox_id)


def mark_outbox_dead(
    store: "BusinessStore",
    tenant_id: str,
    outbox_id: str,
    *,
    worker: str,
    reason: str = "",
    now: float | None = None,
) -> OutboxMessage:
    """Give up on a held delivery; dead is terminal and never silently retried."""
    owner = _text(worker, "worker", max_len=200)
    moment = time.time() if now is None else float(now)
    detail = redact(str(reason or ""))[:2000]
    with store.transaction() as conn:
        row = _outbox_row(conn, tenant_id, outbox_id)
        _require_lease(row, owner, moment)
        conn.execute(
            "UPDATE bos_outbox SET status='dead', last_error=?,"
            " lease_owner=NULL, lease_expires_at=NULL, updated_at=?"
            " WHERE tenant_id=? AND outbox_id=?",
            (
                detail or "delivery abandoned",
                now_iso(moment),
                tenant_id,
                outbox_id,
            ),
        )
        store._audit(
            conn, tenant_id, row["business_id"], owner, "outbox.dead", "outbox",
            outbox_id, "ok", {"reason": detail},
        )
    return _outbox(store, tenant_id, outbox_id)


def reconcile_outbox(
    store: "BusinessStore",
    tenant_id: str,
    outbox_id: str,
    *,
    actor: str,
    outcome: str,
    note: str,
    provider_receipt: Any = None,
    now: float | None = None,
) -> OutboxMessage:
    """Resolve an ``unknown`` delivery to ``sent`` or ``dead``.

    This is the only exit from ``unknown``. ``sent`` requires a provider
    receipt (the proof the effect happened); ``dead`` records why it will not
    be attempted again.
    """
    decision = _text(outcome, "reconciliation outcome", max_len=20).lower()
    if decision not in RECONCILE_OUTCOMES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"reconciliation outcome must be one of {', '.join(RECONCILE_OUTCOMES)}",
        )
    moment = time.time() if now is None else float(now)
    receipt = _receipt_text(provider_receipt)
    if decision == "sent" and not receipt:
        raise BusinessError(
            BAD_INPUT_CODE,
            "reconciling to sent requires the provider receipt that proves it",
        )
    detail = redact(str(note or ""))[:2000]
    with store.transaction() as conn:
        row = _outbox_row(conn, tenant_id, outbox_id)
        if row["status"] != "unknown":
            raise BusinessError(
                OUTBOX_BAD_STATE_CODE,
                f"outbox message is {row['status']!r}; only 'unknown' can be"
                " reconciled",
            )
        stamp = now_iso(moment)
        if decision == "sent":
            conn.execute(
                "UPDATE bos_outbox SET status='sent', provider_receipt=?,"
                " sent_at=?, updated_at=?, last_error=?"
                " WHERE tenant_id=? AND outbox_id=?",
                (receipt, stamp, stamp, detail or row["last_error"],
                 tenant_id, outbox_id),
            )
        else:
            conn.execute(
                "UPDATE bos_outbox SET status='dead', updated_at=?, last_error=?"
                " WHERE tenant_id=? AND outbox_id=?",
                (stamp, detail or "reconciled as dead", tenant_id, outbox_id),
            )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "outbox.reconcile",
            "outbox", outbox_id, "ok",
            {"outcome": decision, "note": detail},
        )
    return _outbox(store, tenant_id, outbox_id)


def recover_outbox(
    store: "BusinessStore", *, now: float | None = None, actor: str = "system"
) -> list[OutboxMessage]:
    """Restart sweep: an expired claim becomes ``unknown``, never ``sent``.

    A live lease is left untouched — another worker may legitimately still own
    it. Nothing is retried here: the reconciler decides ``sent`` or ``dead``.
    """
    moment = time.time() if now is None else float(now)
    recovered: list[OutboxMessage] = []
    with store.transaction() as conn:
        rows = conn.execute(
            "SELECT tenant_id, outbox_id, business_id FROM bos_outbox"
            " WHERE status='claimed'"
            " AND (lease_expires_at IS NULL OR lease_expires_at < ?)"
            " ORDER BY created_at, outbox_id",
            (moment,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE bos_outbox SET status='unknown',"
                " last_error=?, lease_owner=NULL, lease_expires_at=NULL,"
                " updated_at=? WHERE tenant_id=? AND outbox_id=?"
                " AND status='claimed'",
                (
                    "worker lease expired before an outcome was recorded;"
                    " reconcile before retrying",
                    now_iso(moment),
                    row["tenant_id"],
                    row["outbox_id"],
                ),
            )
            store._audit(
                conn, row["tenant_id"], row["business_id"], actor,
                "outbox.recover", "outbox", row["outbox_id"], "ok",
                {"from": "claimed", "to": "unknown"},
            )
            recovered.append(
                OutboxMessage.from_row(
                    conn.execute(
                        "SELECT * FROM bos_outbox WHERE tenant_id=? AND"
                        " outbox_id=?",
                        (row["tenant_id"], row["outbox_id"]),
                    ).fetchone()
                )
            )
        for tenant_id in sorted({item.tenant_id for item in recovered}):
            store._audit(
                conn, tenant_id, None, actor, "recovery.sweep", "outbox", None,
                "ok",
                {
                    "unknown": [
                        item.outbox_id
                        for item in recovered
                        if item.tenant_id == tenant_id
                    ]
                },
            )
    return recovered


__all__ = [
    "CLAIMABLE_STATUSES",
    "Checkpoint",
    "OUTBOX_STATUSES",
    "OutboxMessage",
    "RECONCILE_OUTCOMES",
    "TERMINAL_OUTBOX_STATUSES",
    "claim_outbox",
    "enqueue_outbox",
    "get_outbox",
    "heartbeat_outbox",
    "list_checkpoints",
    "mark_outbox_dead",
    "mark_outbox_sent",
    "mark_outbox_unknown",
    "record_checkpoint",
    "reconcile_outbox",
    "recover_outbox",
]
