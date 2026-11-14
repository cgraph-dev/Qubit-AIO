"""Authoritative, append-only Business OS event log with a transactional outbox.

P1-02. The event log is the record of *what happened*; projections may be
rebuilt from it and nothing else. It is not a second job log: ``bos_job_event``
remains the low-level job state trail, and ``jobs.py`` remains the run state
machine. This module adds the richer, aggregate-ordered envelope the Phase 0
``event.schema.json`` contract defines.

Guarantees:

* **Per-aggregate order.** ``seq`` starts at 1 and is monotonic for
  ``(tenant_id, aggregate_id)``. The unique constraint and ``BEGIN IMMEDIATE``
  make concurrent appends serialise instead of colliding.
* **Idempotent append.** Replaying the same ``idempotency_key`` with the same
  payload hash returns the existing envelope and writes nothing. Reusing the
  key with a different payload (or a different aggregate/type) is a typed
  ``BUSINESS_IDEMPOTENCY_CONFLICT``.
* **Event + outbox are atomic.** Each append also inserts one ``bos_outbox``
  row in the *same* transaction. An event can never be committed without its
  outbox intent, and a failed outbox insert rolls the event back.
* **Payloads are redacted before hashing and storing.** The hash is SHA-256 of
  the canonical (sorted, compact) JSON of the redacted payload.
* **Append-only by trigger.** ``bos_event`` and ``bos_policy_revision`` reject
  UPDATE/DELETE. A correction is a new event, never an edit.

The outbox row needs an owning business because ``bos_outbox`` (v3) is scoped
to a business. ``append_event`` resolves it from, in order: an explicit
``business_id``, the aggregate itself if it is a business, the task/workspace
that owns the aggregate, or the tenant's oldest business. A tenant with no
business at all is a typed refusal; this module never invents scope.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from .errors import (
    BAD_INPUT_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    NOT_FOUND_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, json_loads, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

RETENTION_CLASSES = ("ephemeral", "standard", "extended", "legal-hold")
DEFAULT_RETENTION_CLASS = "standard"

EVENT_BUSINESS_REQUIRED_CODE = "BUSINESS_EVENT_BUSINESS_REQUIRED"
PROJECTION_NOT_FOUND_CODE = "BUSINESS_PROJECTION_NOT_FOUND"
PROJECTION_REWIND_CODE = "BUSINESS_PROJECTION_REWIND"


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _event_type(value: Any) -> str:
    clean = _text(value, "event type", max_len=128)
    if not clean[0].isalpha() or not clean[0].islower():
        raise BusinessError(
            BAD_INPUT_CODE,
            "event type must start with a lowercase letter",
        )
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.:-")
    if len(clean) < 3 or any(char not in allowed for char in clean):
        raise BusinessError(
            BAD_INPUT_CODE,
            "event type must match [a-z][a-z0-9_.:-]{2,127}",
        )
    return clean


def _policy_version(value: Any) -> str:
    clean = _text(value, "policy version", max_len=40)
    raw = clean[1:] if clean.startswith("v") else ""
    parts = raw.split(".") if raw else []
    if not parts or any(not part.isdigit() for part in parts):
        raise BusinessError(BAD_INPUT_CODE, "policy version must look like v1 or v1.2")
    return clean


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """The frozen v1 envelope plus the payload it hashes.

    ``to_dict`` emits exactly the contract fields; ``payload`` is retained for
    in-process consumers and is deliberately absent from the serialized
    envelope (the contract carries ``payload_hash``/``payload_ref``).
    """

    event_id: str
    tenant_id: str
    aggregate_id: str
    sequence: int
    type: str
    actor: str
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    payload_hash: str
    payload: Any
    policy_version: str
    occurred_at: str
    retention_class: str
    retention_until: str | None
    tombstoned_at: str | None
    created_at: str

    @property
    def retention(self) -> dict[str, Any]:
        return {"class": self.retention_class, "until": self.retention_until}

    @property
    def tombstone(self) -> bool:
        return self.tombstoned_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "aggregate_id": self.aggregate_id,
            "sequence": self.sequence,
            "type": self.type,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "idempotency_key": self.idempotency_key,
            "tenant_id": self.tenant_id,
            "actor": self.actor,
            "occurred_at": self.occurred_at,
            "payload_hash": self.payload_hash,
            "policy_version": self.policy_version,
            "retention": self.retention,
            "tombstone": self.tombstone,
        }

    @classmethod
    def from_row(cls, row: Any) -> "EventEnvelope":
        data = dict(row)
        return cls(
            event_id=data["event_id"],
            tenant_id=data["tenant_id"],
            aggregate_id=data["aggregate_id"],
            sequence=int(data["seq"]),
            type=data["type"],
            actor=data["actor"],
            correlation_id=data["correlation_id"],
            causation_id=data["causation_id"],
            idempotency_key=data["idempotency_key"],
            payload_hash=data["payload_hash"],
            payload=json_loads(data.get("payload_json"), {}),
            policy_version=data["policy_version"],
            occurred_at=data["occurred_at"],
            retention_class=data["retention_class"],
            retention_until=data.get("retention_until"),
            tombstoned_at=data["tombstoned_at"],
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class ProjectionCheckpoint:
    tenant_id: str
    projection: str
    aggregate_id: str
    last_seq: int
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "ProjectionCheckpoint":
        data = dict(row)
        return cls(
            tenant_id=data["tenant_id"],
            projection=data["projection"],
            aggregate_id=data["aggregate_id"],
            last_seq=int(data["last_seq"]),
            updated_at=data["updated_at"],
        )


def _resolve_business(
    store: "BusinessStore",
    conn: sqlite3.Connection,
    tenant_id: str,
    aggregate_id: str,
    explicit: str | None,
) -> str:
    if explicit is not None:
        store._require_business(conn, tenant_id, explicit)
        return explicit
    row = conn.execute(
        "SELECT business_id FROM bos_business WHERE tenant_id=? AND business_id=?",
        (tenant_id, aggregate_id),
    ).fetchone()
    if row is not None:
        return row["business_id"]
    row = conn.execute(
        "SELECT business_id FROM bos_task WHERE tenant_id=? AND task_id=?",
        (tenant_id, aggregate_id),
    ).fetchone()
    if row is not None:
        return row["business_id"]
    row = conn.execute(
        "SELECT task_id FROM bos_workspace WHERE tenant_id=? AND workspace_id=?",
        (tenant_id, aggregate_id),
    ).fetchone()
    if row is not None:
        # A workspace is task-pinned; read the task's business.
        task = conn.execute(
            "SELECT business_id FROM bos_task WHERE tenant_id=? AND task_id=?",
            (tenant_id, row["task_id"]),
        ).fetchone()
        if task is not None:
            return task["business_id"]
    row = conn.execute(
        "SELECT business_id FROM bos_business WHERE tenant_id=?"
        " ORDER BY created_at, business_id LIMIT 1",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise BusinessError(
            EVENT_BUSINESS_REQUIRED_CODE,
            f"tenant {tenant_id!r} has no business to own an event outbox row",
        )
    return row["business_id"]


def _event_row(
    conn: sqlite3.Connection, tenant_id: str, event_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_event WHERE tenant_id=? AND event_id=?",
        (tenant_id, event_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE, f"no event {event_id!r} in tenant {tenant_id!r}"
        )
    return row


def append_event(
    store: "BusinessStore",
    tenant_id: str,
    aggregate_id: str,
    event_type: str,
    payload: Any = None,
    *,
    actor: str,
    idempotency_key: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    policy_version: str = "v1",
    business_id: str | None = None,
    retention_class: str = DEFAULT_RETENTION_CLASS,
    now: float | None = None,
    conn: sqlite3.Connection | None = None,
) -> EventEnvelope:
    """Append one event, its outbox row and their audit trail atomically.

    ``conn`` is for a caller that already owns an open ``BEGIN IMMEDIATE``
    transaction (the run/task module does this so a state change and its event
    commit together). When omitted, this function opens its own transaction.
    """
    aggregate = _text(aggregate_id, "aggregate_id", max_len=200)
    kind = _event_type(event_type)
    actor_clean = _text(actor, "actor", max_len=200)
    key = _text(idempotency_key, "idempotency_key", max_len=280)
    policy = _policy_version(policy_version)
    retention = _text(retention_class, "retention class", max_len=20).lower()
    if retention not in RETENTION_CLASSES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown retention class {retention!r}; expected one of "
            f"{', '.join(RETENTION_CLASSES)}",
        )
    corr = _optional_text(correlation_id, "correlation_id", max_len=80) or new_id("cor")
    cause = _optional_text(causation_id, "causation_id", max_len=80)
    explicit_business = _optional_text(business_id, "business_id", max_len=80)
    body = {} if payload is None else payload
    if not isinstance(body, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "event payload must be a mapping")
    safe_payload = redact(dict(body))
    payload_hash = fingerprint(safe_payload)
    moment = time.time() if now is None else float(now)
    stamp = now_iso(moment)
    event_id = new_id("evnt")
    args = (
        store,
        conn,
        tenant_id,
        aggregate,
        kind,
        safe_payload,
        payload_hash,
        actor_clean,
        key,
        corr,
        cause,
        policy,
        retention,
        explicit_business,
        stamp,
        event_id,
    )
    if conn is not None:
        return _append_locked(*args)
    with store.transaction() as owned:
        return _append_locked(store, owned, *args[2:])


def _append_locked(
    store: "BusinessStore",
    conn: sqlite3.Connection,
    tenant_id: str,
    aggregate: str,
    kind: str,
    safe_payload: Mapping[str, Any],
    payload_hash: str,
    actor_clean: str,
    key: str,
    corr: str,
    cause: str | None,
    policy: str,
    retention: str,
    explicit_business: str | None,
    stamp: str,
    event_id: str,
) -> EventEnvelope:
    store._require_tenant(conn, tenant_id)
    existing = conn.execute(
        "SELECT * FROM bos_event WHERE tenant_id=? AND idempotency_key=?",
        (tenant_id, key),
    ).fetchone()
    if existing is not None:
        if (
            existing["payload_hash"] != payload_hash
            or existing["aggregate_id"] != aggregate
            or existing["type"] != kind
        ):
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} was already used for a different"
                " event (payload, aggregate or type changed)",
            )
        return EventEnvelope.from_row(existing)

    owner_business = _resolve_business(
        store, conn, tenant_id, aggregate, explicit_business
    )
    seq = int(
        conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM bos_event"
            " WHERE tenant_id=? AND aggregate_id=?",
            (tenant_id, aggregate),
        ).fetchone()[0]
    )
    try:
        conn.execute(
            "INSERT INTO bos_event(event_id, tenant_id, aggregate_id, seq,"
            " type, actor, correlation_id, causation_id, idempotency_key,"
            " payload_hash, payload_json, policy_version, occurred_at,"
            " retention_class, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                tenant_id,
                aggregate,
                seq,
                kind,
                actor_clean,
                corr,
                cause,
                key,
                payload_hash,
                json_dumps(safe_payload),
                policy,
                stamp,
                retention,
                stamp,
            ),
        )
    except sqlite3.IntegrityError as exc:  # concurrent identical append
        raise BusinessError(
            IDEMPOTENCY_CONFLICT_CODE,
            f"event idempotency key {key!r} is already in use",
        ) from exc

    result = EventEnvelope.from_row(
        conn.execute(
            "SELECT * FROM bos_event WHERE tenant_id=? AND event_id=?",
            (tenant_id, event_id),
        ).fetchone()
    )
    envelope = {**result.to_dict(), "payload": safe_payload}
    outbox_key = f"event:{key}"
    outbox_kind = f"event.{kind}"[:120]
    try:
        conn.execute(
            "INSERT INTO bos_outbox(outbox_id, tenant_id, business_id,"
            " aggregate_id, kind, idempotency_key, request_hash, payload_json,"
            " status, attempts, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,'pending',0,?,?)",
            (
                new_id("obx"),
                tenant_id,
                owner_business,
                aggregate,
                outbox_kind,
                outbox_key,
                payload_hash,
                json_dumps(envelope),
                stamp,
                stamp,
            ),
        )
    except sqlite3.IntegrityError as exc:
        # Same transaction: the event above is rolled back with this raise.
        raise BusinessError(
            IDEMPOTENCY_CONFLICT_CODE,
            f"outbox key {outbox_key!r} is already in use; the event was"
            " rolled back with it",
        ) from exc

    store._audit(
        conn, tenant_id, owner_business, actor_clean, "event.append",
        "event", event_id, "ok",
        {
            "aggregate_id": aggregate,
            "sequence": seq,
            "type": kind,
            "payload_hash": payload_hash,
        },
    )
    return result

def get_event(store: "BusinessStore", tenant_id: str, event_id: str) -> EventEnvelope:
    with store.read() as conn:
        return EventEnvelope.from_row(_event_row(conn, tenant_id, event_id))


def read_events(
    store: "BusinessStore",
    tenant_id: str,
    aggregate_id: str,
    *,
    after_seq: int = 0,
) -> list[EventEnvelope]:
    """Ordered events for one aggregate strictly after ``after_seq``."""
    aggregate = _text(aggregate_id, "aggregate_id", max_len=200)
    if not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0:
        raise BusinessError(BAD_INPUT_CODE, "after_seq must be an integer >= 0")
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(
            "SELECT * FROM bos_event WHERE tenant_id=? AND aggregate_id=?"
            " AND seq > ? ORDER BY seq",
            (tenant_id, aggregate, int(after_seq)),
        ).fetchall()
    return [EventEnvelope.from_row(row) for row in rows]


def replay(
    store: "BusinessStore", tenant_id: str, aggregate_id: str
) -> list[EventEnvelope]:
    """Rebuild an aggregate from its log; identical to a fresh read."""
    return read_events(store, tenant_id, aggregate_id, after_seq=0)


def list_events(
    store: "BusinessStore", tenant_id: str, *, limit: int = 500
) -> list[EventEnvelope]:
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(
            "SELECT * FROM bos_event WHERE tenant_id=?"
            " ORDER BY occurred_at, event_id LIMIT ?",
            (tenant_id, max(1, int(limit))),
        ).fetchall()
    return [EventEnvelope.from_row(row) for row in rows]


# -- projection checkpoints ------------------------------------------------
def _checkpoint_row(
    store: "BusinessStore", tenant_id: str, projection: str, aggregate_id: str
) -> sqlite3.Row | None:
    with store.read() as conn:
        return conn.execute(
            "SELECT * FROM bos_projection_checkpoint WHERE tenant_id=?"
            " AND projection=? AND aggregate_id=?",
            (tenant_id, projection, aggregate_id),
        ).fetchone()


def save_projection_checkpoint(
    store: "BusinessStore",
    tenant_id: str,
    projection: str,
    last_seq: int,
    *,
    aggregate_id: str = "",
    now: float | None = None,
) -> ProjectionCheckpoint:
    """Advance (never rewind) a projection cursor and persist it."""
    name = _text(projection, "projection", max_len=200)
    aggregate = str(aggregate_id or "")
    if not isinstance(last_seq, int) or isinstance(last_seq, bool) or last_seq < 0:
        raise BusinessError(BAD_INPUT_CODE, "last_seq must be an integer >= 0")
    stamp = now_iso(time.time() if now is None else float(now))
    with store.transaction() as conn:
        store._require_tenant(conn, tenant_id)
        row = conn.execute(
            "SELECT * FROM bos_projection_checkpoint WHERE tenant_id=?"
            " AND projection=? AND aggregate_id=?",
            (tenant_id, name, aggregate),
        ).fetchone()
        if row is not None and int(last_seq) < int(row["last_seq"]):
            raise BusinessError(
                PROJECTION_REWIND_CODE,
                f"projection {name!r} is at seq {row['last_seq']}; refusing to"
                f" move backwards to {last_seq}",
            )
        conn.execute(
            "INSERT INTO bos_projection_checkpoint(tenant_id, projection,"
            " aggregate_id, last_seq, updated_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(tenant_id, projection, aggregate_id)"
            " DO UPDATE SET last_seq=excluded.last_seq,"
            " updated_at=excluded.updated_at",
            (tenant_id, name, aggregate, int(last_seq), stamp),
        )
        store._audit(
            conn, tenant_id, None, "system", "projection.checkpoint",
            "projection", f"{name}:{aggregate or '*'}", "ok",
            {"last_seq": int(last_seq)},
        )
    checkpoint = _checkpoint_row(store, tenant_id, name, aggregate)
    assert checkpoint is not None  # written above in the same call
    return ProjectionCheckpoint.from_row(checkpoint)


def read_projection_checkpoint(
    store: "BusinessStore",
    tenant_id: str,
    projection: str,
    *,
    aggregate_id: str = "",
) -> ProjectionCheckpoint | None:
    """Read a cursor, or ``None`` when the projection never saved one."""
    name = _text(projection, "projection", max_len=200)
    row = _checkpoint_row(store, tenant_id, name, str(aggregate_id or ""))
    return None if row is None else ProjectionCheckpoint.from_row(row)


__all__ = [
    "DEFAULT_RETENTION_CLASS",
    "EVENT_BUSINESS_REQUIRED_CODE",
    "EventEnvelope",
    "PROJECTION_NOT_FOUND_CODE",
    "PROJECTION_REWIND_CODE",
    "ProjectionCheckpoint",
    "RETENTION_CLASSES",
    "append_event",
    "get_event",
    "list_events",
    "read_events",
    "read_projection_checkpoint",
    "replay",
    "save_projection_checkpoint",
]
