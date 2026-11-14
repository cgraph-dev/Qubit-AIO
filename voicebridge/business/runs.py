"""Durable runs: task, workspace, lease and the single-writer fence.

P1-04. **There is no second execution engine here.** ``bos_job`` (see
``jobs.py``) remains the run/turn executing record: its state machine, leases,
restart recovery and explicit ``unknown`` outcome are authoritative for
execution. This module adds the records the Office needs *around* a run:

* :class:`WorkTask` — a durable objective with explicit movement. Every status
  change appends an event through :mod:`eventlog` in the same transaction as
  the row update, so task history is append-only and rebuildable.
* :class:`Workspace` — a task-pinned working area with one mutable writer. The
  writer must hold a live :class:`Lease` and a matching fencing token, and the
  revision is updated with compare-and-swap.
* :class:`Lease` — a time-bounded, fencing-token lease per resource. An
  expired lease stops work; a stale token is refused; a released lease cannot
  be reused.
* :func:`run_status_for_job` — the canonical Run status projection over a job.

Mapping over ``bos_job``::

    created            -> queued
    queued             -> queued
    running            -> executing
    waiting_approval   -> approval
    unknown            -> unknown
    failed             -> failed
    completed          -> completed
    cancelled          -> cancelled

``leased``/``waiting``/``checkpointed`` are run states that are reached by an
explicit transition or by a recorded checkpoint; they never override the job's
own terminal truth. ``unknown`` is never rendered completed.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from . import eventlog
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    NOT_FOUND_CODE,
    BusinessError,
)
from .ids import new_id
from .util import json_dumps, json_loads, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore
    from .models import JobRun

WORKFLOW_STATUSES = (
    "draft",
    "assigned",
    "claimed",
    "blocked",
    "handed_off",
    "completed",
    "rejected",
    "cancelled",
)
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"assigned", "claimed", "cancelled"}),
    "assigned": frozenset({"claimed", "blocked", "cancelled"}),
    "claimed": frozenset(
        {"blocked", "completed", "rejected", "handed_off", "cancelled"}
    ),
    "blocked": frozenset({"assigned", "claimed", "cancelled"}),
    "handed_off": frozenset({"cancelled"}),
    "completed": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
}
TERMINAL_WORKFLOW_STATUSES = frozenset({"completed", "rejected", "cancelled"})
STORAGE_CLASSES = ("ephemeral", "persistent", "archive")
DATA_CLASSES = ("public", "internal", "confidential", "restricted")

#: Legacy ``bos_task.status`` has a four-value CHECK from v1; keep it in sync
#: without pretending it is the durable workflow status.
_LEGACY_STATUS: dict[str, str] = {
    "draft": "open",
    "assigned": "open",
    "claimed": "doing",
    "blocked": "doing",
    "handed_off": "doing",
    "completed": "done",
    "rejected": "done",
    "cancelled": "cancelled",
}

RUN_STATUSES = (
    "queued",
    "leased",
    "executing",
    "waiting",
    "approval",
    "checkpointed",
    "completed",
    "failed",
    "cancelled",
    "unknown",
)
RUN_STATUS_BY_JOB: dict[str, str] = {
    "created": "queued",
    "queued": "queued",
    "running": "executing",
    "waiting_approval": "approval",
    "unknown": "unknown",
    "failed": "failed",
    "completed": "completed",
    "cancelled": "cancelled",
}
RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"leased", "executing", "cancelled", "failed"}),
    "leased": frozenset({"executing", "cancelled", "failed", "unknown"}),
    "executing": frozenset(
        {"waiting", "approval", "checkpointed", "completed", "failed",
         "cancelled", "unknown"}
    ),
    "waiting": frozenset(
        {"executing", "approval", "cancelled", "failed", "unknown"}
    ),
    "approval": frozenset({"executing", "cancelled", "failed", "unknown"}),
    "checkpointed": frozenset(
        {"executing", "completed", "failed", "cancelled", "unknown"}
    ),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    # Reconciliation only: an unknown outcome never becomes a silent success.
    "unknown": frozenset({"queued", "completed", "failed", "cancelled"}),
}

TASK_NOT_FOUND_CODE = "BUSINESS_TASK_NOT_FOUND"
TASK_READ_ONLY_CODE = "BUSINESS_TASK_READ_ONLY"
TASK_DEPENDENCIES_INCOMPLETE_CODE = "BUSINESS_TASK_DEPENDENCIES_INCOMPLETE"
HANDOFF_RECIPIENT_MISMATCH_CODE = "BUSINESS_HANDOFF_RECIPIENT_MISMATCH"
WORKSPACE_NOT_FOUND_CODE = "BUSINESS_WORKSPACE_NOT_FOUND"
WORKSPACE_REVISION_CONFLICT_CODE = "BUSINESS_WORKSPACE_REVISION_CONFLICT"
LEASE_NOT_FOUND_CODE = "BUSINESS_LEASE_NOT_FOUND"
LEASE_CONFLICT_CODE = "BUSINESS_LEASE_CONFLICT"
LEASE_LOST_CODE = "BUSINESS_LEASE_LOST"
RUN_ILLEGAL_TRANSITION_CODE = "BUSINESS_RUN_ILLEGAL_TRANSITION"


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} must be an integer >= {minimum}"
        )
    return int(value)


def _json_list(value: Iterable[Any] | None, label: str) -> str:
    if value is None:
        return "[]"
    if isinstance(value, (str, bytes, Mapping)):
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a sequence")
    return json_dumps(redact(list(value)))


def _json_mapping(value: Mapping[str, Any] | None, label: str) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a mapping")
    return json_dumps(redact(dict(value)))


def _parse_list(value: Any) -> tuple[Any, ...]:
    parsed = json_loads(value, [])
    return tuple(parsed) if isinstance(parsed, list) else ()


def _parse_mapping(value: Any) -> dict[str, Any]:
    parsed = json_loads(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _principal_exists(
    conn: sqlite3.Connection, tenant_id: str, principal_id: str | None
) -> None:
    if principal_id is None:
        return
    row = conn.execute(
        "SELECT 1 FROM bos_principal WHERE tenant_id=? AND principal_id=?",
        (tenant_id, principal_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no principal {principal_id!r} in tenant {tenant_id!r}",
        )


def _principal_active(
    conn: sqlite3.Connection, tenant_id: str, principal_id: str
) -> None:
    row = conn.execute(
        "SELECT status FROM bos_principal WHERE tenant_id=? AND principal_id=?",
        (tenant_id, principal_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no principal {principal_id!r} in tenant {tenant_id!r}",
        )
    if row["status"] != "active":
        raise BusinessError(
            BAD_STATE_CODE,
            f"principal {principal_id!r} is {row['status']!r};"
            " revoked/suspended principals cannot execute work",
        )


def _task_row(
    conn: sqlite3.Connection, tenant_id: str, task_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_task WHERE tenant_id=? AND task_id=?"
        " AND task_class='execution'",
        (tenant_id, task_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            TASK_NOT_FOUND_CODE, f"no task {task_id!r} in tenant {tenant_id!r}"
        )
    return row


def _workspace_row(
    conn: sqlite3.Connection, tenant_id: str, workspace_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_workspace WHERE tenant_id=? AND workspace_id=?",
        (tenant_id, workspace_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            WORKSPACE_NOT_FOUND_CODE,
            f"no workspace {workspace_id!r} in tenant {tenant_id!r}",
        )
    return row


# -- typed records ---------------------------------------------------------
@dataclass(frozen=True, slots=True)
class WorkTask:
    task_id: str
    tenant_id: str
    business_id: str
    title: str
    objective: str
    status: str
    owner_principal: str | None
    assignee_principal: str | None
    dependencies: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    data_class: str
    budget: dict[str, Any]
    deadline: str | None
    acceptance: tuple[Any, ...]
    artifact_ids: tuple[str, ...]
    handed_off_from: str | None
    handed_off_to: str | None
    read_only: bool
    legacy_status: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "WorkTask":
        data = dict(row)
        return cls(
            task_id=data["task_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            title=data["title"],
            objective=data.get("objective") or "",
            status=data.get("workflow_status") or "draft",
            owner_principal=data.get("owner_principal"),
            assignee_principal=data.get("assignee_principal"),
            dependencies=tuple(_parse_list(data.get("dependencies_json"))),
            required_capabilities=tuple(
                _parse_list(data.get("required_capabilities_json"))
            ),
            data_class=data.get("data_class") or "internal",
            budget=_parse_mapping(data.get("budget_json")),
            deadline=data.get("deadline"),
            acceptance=_parse_list(data.get("acceptance_json")),
            artifact_ids=tuple(_parse_list(data.get("artifact_ids_json"))),
            handed_off_from=data.get("handed_off_from"),
            handed_off_to=data.get("handed_off_to"),
            read_only=bool(data.get("read_only") or 0),
            legacy_status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class Workspace:
    workspace_id: str
    tenant_id: str
    task_id: str
    session_id: str
    repository_ref: str | None
    worktree_ref: str | None
    storage_class: str
    access_policy: dict[str, Any]
    current_revision: str
    artifact_manifest: tuple[Any, ...]
    retention_policy: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Workspace":
        data = dict(row)
        return cls(
            workspace_id=data["workspace_id"],
            tenant_id=data["tenant_id"],
            task_id=data["task_id"],
            session_id=data["session_id"],
            repository_ref=data["repository_ref"],
            worktree_ref=data["worktree_ref"],
            storage_class=data["storage_class"],
            access_policy=_parse_mapping(data.get("access_policy_json")),
            current_revision=data["current_revision"],
            artifact_manifest=tuple(
                _parse_list(data.get("artifact_manifest_json"))
            ),
            retention_policy=data["retention_policy"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: str
    tenant_id: str
    resource_type: str
    resource_id: str
    holder: str
    fencing_token: str
    acquired_at: str
    expires_at: float
    heartbeat_at: str
    released_at: str | None

    @property
    def active(self) -> bool:
        return self.released_at is None

    @classmethod
    def from_row(cls, row: Any) -> "Lease":
        data = dict(row)
        return cls(
            lease_id=data["lease_id"],
            tenant_id=data["tenant_id"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            holder=data["holder"],
            fencing_token=str(data["fencing_token"]),
            acquired_at=data["acquired_at"],
            expires_at=float(data["expires_at"]),
            heartbeat_at=data["heartbeat_at"],
            released_at=data["released_at"],
        )


# -- tasks -----------------------------------------------------------------
def create_task(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    title: str,
    *,
    objective: str = "",
    owner_principal: str | None = None,
    assignee_principal: str | None = None,
    dependencies: Iterable[str] | None = None,
    required_capabilities: Iterable[str] | None = None,
    data_class: str = "internal",
    budget: Mapping[str, Any] | None = None,
    deadline: str | None = None,
    acceptance: Iterable[Any] | None = None,
    actor: str = "system",
    task_id: str | None = None,
    now: float | None = None,
) -> WorkTask:
    clean_title = _text(title, "task title", max_len=500)
    clean_objective = str(objective or "").strip()[:4000]
    cls = _text(data_class, "data_class", max_len=40).lower()
    if cls not in DATA_CLASSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown data class {data_class!r}")
    owner = _optional_text(owner_principal, "owner_principal", max_len=80)
    assignee = _optional_text(assignee_principal, "assignee_principal", max_len=80)
    dep_json = _json_list(dependencies, "dependencies")
    cap_json = _json_list(required_capabilities, "required_capabilities")
    budget_json = _json_mapping(budget, "budget")
    accept_json = _json_list(acceptance, "acceptance")
    tid = _text(task_id or new_id("task"), "task id", max_len=80)
    stamp = now_iso(time.time() if now is None else float(now))
    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        _principal_exists(conn, tenant_id, owner)
        _principal_exists(conn, tenant_id, assignee)
        conn.execute(
            "INSERT INTO bos_task(task_id, tenant_id, business_id, title, status,"
            " assigned_to, created_at, updated_at, metadata_json, objective,"
            " owner_principal, assignee_principal, dependencies_json,"
            " required_capabilities_json, data_class, budget_json, deadline,"
            " acceptance_json, artifact_ids_json, task_class, workflow_status,"
            " read_only) VALUES(?,?,?,?,'open',NULL,?,?,'{}',?,?,?,?,?,?,?,?,?,"
            " '[]','execution','draft',0)",
            (
                tid,
                tenant_id,
                business_id,
                clean_title,
                stamp,
                stamp,
                clean_objective,
                owner,
                assignee,
                dep_json,
                cap_json,
                cls,
                budget_json,
                deadline,
                accept_json,
            ),
        )
        eventlog.append_event(
            store,
            tenant_id,
            tid,
            "task.created",
            {
                "business_id": business_id,
                "title": clean_title,
                "owner_principal": owner,
                "assignee_principal": assignee,
                "data_class": cls,
            },
            actor=actor,
            idempotency_key=f"task.created:{tid}",
            business_id=business_id,
            conn=conn,
            now=None if now is None else float(now),
        )
        store._audit(
            conn, tenant_id, business_id, actor, "task.create", "task", tid,
            "ok", {"title": clean_title},
        )
    return get_task(store, tenant_id, tid)


def get_task(store: "BusinessStore", tenant_id: str, task_id: str) -> WorkTask:
    with store.read() as conn:
        return WorkTask.from_row(_task_row(conn, tenant_id, task_id))


def list_tasks(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    status: str | None = None,
) -> list[WorkTask]:
    if status is not None and status not in WORKFLOW_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown workflow status {status!r}")
    query = "SELECT * FROM bos_task WHERE tenant_id=? AND task_class='execution'"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if status is not None:
        query += " AND workflow_status=?"
        params.append(status)
    query += " ORDER BY created_at, task_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [WorkTask.from_row(row) for row in rows]


def _assert_task_writer(row: sqlite3.Row, actor: str) -> None:
    handed_from = row["handed_off_from"]
    if handed_from and actor == handed_from and actor != row["assignee_principal"]:
        raise BusinessError(
            TASK_READ_ONLY_CODE,
            f"task was handed off by {actor!r}; the source is read-only",
        )
    if int(row["read_only"] or 0) == 1 and actor != row["handed_off_to"]:
        raise BusinessError(
            TASK_READ_ONLY_CODE,
            f"task is handed off to {row['handed_off_to']!r}; the source is"
            " read-only until the recipient accepts",
        )


def claim_task(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    principal: str,
    actor: str | None = None,
    now: float | None = None,
) -> WorkTask:
    """Claim a draft/assigned task; exactly one claimant can win."""
    who = _text(principal, "principal", max_len=80)
    stamp = now_iso(time.time() if now is None else float(now))
    with store.transaction() as conn:
        row = _task_row(conn, tenant_id, task_id)
        _principal_active(conn, tenant_id, who)
        _assert_task_writer(row, actor or who)
        dependencies = _parse_list(row["dependencies_json"])
        if dependencies:
            placeholders = ",".join("?" for _ in dependencies)
            rows = conn.execute(
                f"SELECT task_id, workflow_status FROM bos_task WHERE tenant_id=?"
                f" AND task_id IN ({placeholders})",
                (tenant_id, *dependencies),
            ).fetchall()
            states = {item["task_id"]: item["workflow_status"] for item in rows}
            incomplete = [
                dep for dep in dependencies
                if states.get(dep) != "completed"
            ]
            if incomplete:
                raise BusinessError(
                    TASK_DEPENDENCIES_INCOMPLETE_CODE,
                    f"dependencies are not complete: {', '.join(incomplete)}",
                )
        changed = conn.execute(
            "UPDATE bos_task SET workflow_status='claimed', status='doing',"
            " assignee_principal=?, updated_at=?"
            " WHERE tenant_id=? AND task_id=? AND task_class='execution'"
            " AND workflow_status IN ('draft','assigned')"
            " AND read_only=0"
            " AND (assignee_principal IS NULL OR assignee_principal=?)",
            (who, stamp, tenant_id, task_id, who),
        ).rowcount
        if changed != 1:
            raise BusinessError(
                BAD_STATE_CODE,
                f"task {task_id!r} is {row['workflow_status']!r} or already"
                " assigned to another principal",
            )
        eventlog.append_event(
            store,
            tenant_id,
            task_id,
            "task.claimed",
            {"principal": who},
            actor=actor or who,
            idempotency_key=f"task.claimed:{task_id}:{stamp}",
            conn=conn,
            now=None if now is None else float(now),
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor or who, "task.claim",
            "task", task_id, "ok", {"principal": who},
        )
    return get_task(store, tenant_id, task_id)


def transition_task(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    target: str,
    *,
    actor: str,
    note: str = "",
    now: float | None = None,
) -> WorkTask:
    """Move a task; the update and its event commit together."""
    target_clean = _text(target, "task status", max_len=30)
    if target_clean not in WORKFLOW_STATUSES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown workflow status {target_clean!r}; expected one of "
            f"{', '.join(WORKFLOW_STATUSES)}",
        )
    stamp = now_iso(time.time() if now is None else float(now))
    with store.transaction() as conn:
        row = _task_row(conn, tenant_id, task_id)
        _assert_task_writer(row, actor)
        current = row["workflow_status"]
        if target_clean == current:
            return WorkTask.from_row(row)
        if target_clean not in TASK_TRANSITIONS[current]:
            raise BusinessError(
                BAD_STATE_CODE,
                f"task cannot move {current!r} -> {target_clean!r}",
            )
        conn.execute(
            "UPDATE bos_task SET workflow_status=?, status=?, updated_at=?"
            " WHERE tenant_id=? AND task_id=?",
            (
                target_clean,
                _LEGACY_STATUS[target_clean],
                stamp,
                tenant_id,
                task_id,
            ),
        )
        eventlog.append_event(
            store,
            tenant_id,
            task_id,
            "task.transitioned",
            {"from": current, "to": target_clean, "note": str(note)[:2000]},
            actor=actor,
            idempotency_key=f"task.transitioned:{task_id}:{stamp}:{target_clean}",
            conn=conn,
            now=None if now is None else float(now),
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "task.transition",
            "task", task_id, "ok",
            {"from": current, "to": target_clean, "note": str(note)[:2000]},
        )
    return get_task(store, tenant_id, task_id)


def handoff_task(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    to_principal: str,
    actor: str,
    reason: str = "",
    now: float | None = None,
) -> WorkTask:
    """Hand a claimed/assigned task to an explicit recipient.

    The source becomes read-only immediately; the task stays ``handed_off``
    until :func:`accept_handoff` is called by that recipient.
    """
    recipient = _text(to_principal, "to_principal", max_len=80)
    stamp = now_iso(time.time() if now is None else float(now))
    with store.transaction() as conn:
        row = _task_row(conn, tenant_id, task_id)
        _assert_task_writer(row, actor)
        current = row["workflow_status"]
        if current not in {"draft", "assigned", "claimed", "blocked"}:
            raise BusinessError(
                BAD_STATE_CODE,
                f"task cannot be handed off from {current!r}",
            )
        _principal_exists(conn, tenant_id, recipient)
        _principal_active(conn, tenant_id, recipient)
        source = row["assignee_principal"] or row["owner_principal"] or actor
        conn.execute(
            "UPDATE bos_task SET workflow_status='handed_off', status='doing',"
            " handed_off_from=?, handed_off_to=?, assignee_principal=?,"
            " read_only=1, updated_at=? WHERE tenant_id=? AND task_id=?",
            (source, recipient, recipient, stamp, tenant_id, task_id),
        )
        eventlog.append_event(
            store,
            tenant_id,
            task_id,
            "task.handed_off",
            {
                "from": source,
                "to": recipient,
                "reason": str(reason)[:2000],
            },
            actor=actor,
            idempotency_key=f"task.handed_off:{task_id}:{stamp}",
            conn=conn,
            now=None if now is None else float(now),
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "task.handoff", "task",
            task_id, "ok", {"from": source, "to": recipient},
        )
    return get_task(store, tenant_id, task_id)


def accept_handoff(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    principal: str,
    now: float | None = None,
) -> WorkTask:
    """Recipient acceptance: the only way out of ``handed_off``."""
    who = _text(principal, "principal", max_len=80)
    stamp = now_iso(time.time() if now is None else float(now))
    with store.transaction() as conn:
        row = _task_row(conn, tenant_id, task_id)
        if row["workflow_status"] != "handed_off":
            raise BusinessError(
                BAD_STATE_CODE,
                f"task is {row['workflow_status']!r}, not 'handed_off'",
            )
        if row["handed_off_to"] != who:
            raise BusinessError(
                HANDOFF_RECIPIENT_MISMATCH_CODE,
                f"handoff was offered to {row['handed_off_to']!r}, not {who!r}",
            )
        _principal_active(conn, tenant_id, who)
        conn.execute(
            "UPDATE bos_task SET workflow_status='claimed', status='doing',"
            " read_only=0, updated_at=? WHERE tenant_id=? AND task_id=?",
            (stamp, tenant_id, task_id),
        )
        eventlog.append_event(
            store,
            tenant_id,
            task_id,
            "task.handoff_accepted",
            {"recipient": who, "from": row["handed_off_from"]},
            actor=who,
            idempotency_key=f"task.handoff_accepted:{task_id}:{stamp}",
            conn=conn,
            now=None if now is None else float(now),
        )
        store._audit(
            conn, tenant_id, row["business_id"], who, "task.handoff_accept",
            "task", task_id, "ok", {"recipient": who},
        )
    return get_task(store, tenant_id, task_id)


# -- workspaces ------------------------------------------------------------
def create_workspace(
    store: "BusinessStore",
    tenant_id: str,
    *,
    task_id: str,
    session_id: str,
    repository_ref: str | None = None,
    worktree_ref: str | None = None,
    storage_class: str = "ephemeral",
    access_policy: Mapping[str, Any] | None = None,
    current_revision: str = "1",
    artifact_manifest: Iterable[Any] | None = None,
    retention_policy: str = "standard",
    actor: str = "system",
    workspace_id: str | None = None,
    now: float | None = None,
) -> Workspace:
    task = _text(task_id, "task_id", max_len=80)
    session = _text(session_id, "session_id", max_len=200)
    storage = _text(storage_class, "storage_class", max_len=20).lower()
    if storage not in STORAGE_CLASSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown storage class {storage!r}")
    revision = _text(current_revision, "current_revision", max_len=128)
    retention = _text(retention_policy, "retention_policy", max_len=64)
    policy_json = _json_mapping(access_policy, "access_policy")
    manifest_json = _json_list(artifact_manifest, "artifact_manifest")
    wid = _text(workspace_id or new_id("wsp"), "workspace id", max_len=80)
    stamp = now_iso(time.time() if now is None else float(now))
    with store.transaction() as conn:
        row = _task_row(conn, tenant_id, task)
        conn.execute(
            "INSERT INTO bos_workspace(workspace_id, tenant_id, task_id,"
            " session_id, repository_ref, worktree_ref, storage_class,"
            " access_policy_json, current_revision, artifact_manifest_json,"
            " retention_policy, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                wid,
                tenant_id,
                task,
                session,
                _optional_text(repository_ref, "repository_ref", max_len=1024),
                _optional_text(worktree_ref, "worktree_ref", max_len=1024),
                storage,
                policy_json,
                revision,
                manifest_json,
                retention,
                stamp,
                stamp,
            ),
        )
        eventlog.append_event(
            store,
            tenant_id,
            wid,
            "workspace.created",
            {"task_id": task, "session_id": session, "revision": revision},
            actor=actor,
            idempotency_key=f"workspace.created:{wid}",
            conn=conn,
            now=None if now is None else float(now),
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "workspace.create",
            "workspace", wid, "ok", {"task_id": task},
        )
    return get_workspace(store, tenant_id, wid)


def get_workspace(
    store: "BusinessStore", tenant_id: str, workspace_id: str
) -> Workspace:
    with store.read() as conn:
        return Workspace.from_row(_workspace_row(conn, tenant_id, workspace_id))


def list_workspaces(
    store: "BusinessStore", tenant_id: str, *, task_id: str | None = None
) -> list[Workspace]:
    query = "SELECT * FROM bos_workspace WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if task_id is not None:
        query += " AND task_id=?"
        params.append(task_id)
    query += " ORDER BY created_at, workspace_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Workspace.from_row(row) for row in rows]


def revise_workspace(
    store: "BusinessStore",
    tenant_id: str,
    workspace_id: str,
    *,
    expected_revision: str,
    new_revision: str,
    holder: str,
    fencing_token: str,
    artifact_manifest: Iterable[Any] | None = None,
    access_policy: Mapping[str, Any] | None = None,
    actor: str | None = None,
    now: float | None = None,
) -> Workspace:
    """Single-writer revision with a live lease, fencing token and CAS."""
    expected = _text(expected_revision, "expected_revision", max_len=128)
    target = _text(new_revision, "new_revision", max_len=128)
    who = _text(holder, "holder", max_len=200)
    token = _text(fencing_token, "fencing_token", max_len=64)
    stamp = now_iso(time.time() if now is None else float(now))
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _workspace_row(conn, tenant_id, workspace_id)
        task = conn.execute(
            "SELECT * FROM bos_task WHERE tenant_id=? AND task_id=?",
            (tenant_id, row["task_id"]),
        ).fetchone()
        if task is not None:
            if (
                task["handed_off_from"]
                and who == task["handed_off_from"]
                and who != task["assignee_principal"]
            ):
                raise BusinessError(
                    TASK_READ_ONLY_CODE,
                    "the task was handed off; its source cannot write the"
                    " workspace",
                )
        lease = _assert_fence_conn(
            conn, tenant_id, "workspace", workspace_id, who, token, moment
        )
        if artifact_manifest is not None:
            manifest_json = _json_list(artifact_manifest, "artifact_manifest")
        else:
            manifest_json = row["artifact_manifest_json"]
        if access_policy is not None:
            policy_json = _json_mapping(access_policy, "access_policy")
        else:
            policy_json = row["access_policy_json"]
        changed = conn.execute(
            "UPDATE bos_workspace SET current_revision=?, artifact_manifest_json=?,"
            " access_policy_json=?, updated_at=?"
            " WHERE tenant_id=? AND workspace_id=? AND current_revision=?",
            (
                target,
                manifest_json,
                policy_json,
                stamp,
                tenant_id,
                workspace_id,
                expected,
            ),
        ).rowcount
        if changed != 1:
            raise BusinessError(
                WORKSPACE_REVISION_CONFLICT_CODE,
                f"workspace {workspace_id!r} is not at revision {expected!r}",
            )
        eventlog.append_event(
            store,
            tenant_id,
            workspace_id,
            "workspace.revised",
            {
                "from": expected,
                "to": target,
                "lease_id": lease.lease_id,
            },
            actor=actor or who,
            idempotency_key=f"workspace.revised:{workspace_id}:{stamp}:{target}",
            conn=conn,
            now=moment,
        )
        store._audit(
            conn, tenant_id, None, actor or who, "workspace.revise",
            "workspace", workspace_id, "ok",
            {"from": expected, "to": target, "lease_id": lease.lease_id},
        )
    return get_workspace(store, tenant_id, workspace_id)


# -- leases ----------------------------------------------------------------
def _lease_row(
    conn: sqlite3.Connection, tenant_id: str, lease_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_lease WHERE tenant_id=? AND lease_id=?",
        (tenant_id, lease_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            LEASE_NOT_FOUND_CODE, f"no lease {lease_id!r} in tenant {tenant_id!r}"
        )
    return row


def _assert_fence_conn(
    conn: sqlite3.Connection,
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    holder: str,
    fencing_token: str,
    moment: float,
) -> Lease:
    row = conn.execute(
        "SELECT * FROM bos_lease WHERE tenant_id=? AND resource_type=?"
        " AND resource_id=? AND released_at IS NULL"
        " ORDER BY acquired_at DESC, lease_id DESC LIMIT 1",
        (tenant_id, resource_type, resource_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            LEASE_LOST_CODE,
            f"no active lease for {resource_type}:{resource_id}",
        )
    if row["holder"] != holder or str(row["fencing_token"]) != str(fencing_token):
        raise BusinessError(
            LEASE_LOST_CODE,
            "stale fencing token or wrong holder; another writer owns the"
            " resource",
        )
    if float(row["expires_at"]) <= moment:
        raise BusinessError(LEASE_LOST_CODE, "lease expired; work must stop")
    return Lease.from_row(row)


def acquire_lease(
    store: "BusinessStore",
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    *,
    holder: str,
    ttl_seconds: float = 300.0,
    actor: str | None = None,
    now: float | None = None,
) -> Lease:
    """Acquire the single active lease for one resource; fencing is monotonic."""
    kind = _text(resource_type, "resource_type", max_len=80)
    rid = _text(resource_id, "resource_id", max_len=200)
    who = _text(holder, "holder", max_len=200)
    if ttl_seconds <= 0:
        raise BusinessError(BAD_INPUT_CODE, "ttl_seconds must be positive")
    moment = time.time() if now is None else float(now)
    stamp = now_iso(moment)
    lease_id = new_id("leas")
    with store.transaction() as conn:
        store._require_tenant(conn, tenant_id)
        existing = conn.execute(
            "SELECT * FROM bos_lease WHERE tenant_id=? AND resource_type=?"
            " AND resource_id=? AND released_at IS NULL"
            " ORDER BY acquired_at DESC, lease_id DESC LIMIT 1",
            (tenant_id, kind, rid),
        ).fetchone()
        if existing is not None:
            if float(existing["expires_at"]) > moment:
                raise BusinessError(
                    LEASE_CONFLICT_CODE,
                    f"{kind}:{rid} is already leased to {existing['holder']!r}"
                    " until it expires or is released",
                )
            conn.execute(
                "UPDATE bos_lease SET released_at=? WHERE tenant_id=? AND"
                " lease_id=? AND released_at IS NULL",
                (stamp, tenant_id, existing["lease_id"]),
            )
            store._audit(
                conn, tenant_id, None, actor or who, "lease.expire", "lease",
                existing["lease_id"], "ok", {"resource": f"{kind}:{rid}"},
            )
        row = conn.execute(
            "SELECT COALESCE(MAX(CAST(fencing_token AS INTEGER)), 0)"
            " FROM bos_lease WHERE tenant_id=? AND resource_type=?"
            " AND resource_id=?",
            (tenant_id, kind, rid),
        ).fetchone()
        token = str(int(row[0]) + 1)
        conn.execute(
            "INSERT INTO bos_lease(lease_id, tenant_id, resource_type,"
            " resource_id, holder, fencing_token, acquired_at, expires_at,"
            " heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                lease_id,
                tenant_id,
                kind,
                rid,
                who,
                token,
                stamp,
                moment + float(ttl_seconds),
                stamp,
            ),
        )
        store._audit(
            conn, tenant_id, None, actor or who, "lease.acquire", "lease",
            lease_id, "ok",
            {"resource": f"{kind}:{rid}", "fencing_token": token},
        )
    return get_lease(store, tenant_id, lease_id)


def get_lease(store: "BusinessStore", tenant_id: str, lease_id: str) -> Lease:
    with store.read() as conn:
        return Lease.from_row(_lease_row(conn, tenant_id, lease_id))


def list_leases(
    store: "BusinessStore",
    tenant_id: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    active_only: bool = True,
) -> list[Lease]:
    query = "SELECT * FROM bos_lease WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if resource_type is not None:
        query += " AND resource_type=?"
        params.append(resource_type)
    if resource_id is not None:
        query += " AND resource_id=?"
        params.append(resource_id)
    if active_only:
        query += " AND released_at IS NULL"
    query += " ORDER BY acquired_at, lease_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Lease.from_row(row) for row in rows]


def assert_fence(
    store: "BusinessStore",
    tenant_id: str,
    lease_id: str,
    *,
    holder: str,
    fencing_token: str,
    now: float | None = None,
) -> Lease:
    """Assert a live lease and token before an effect; loss stops execution."""
    who = _text(holder, "holder", max_len=200)
    token = _text(fencing_token, "fencing_token", max_len=64)
    moment = time.time() if now is None else float(now)
    with store.read() as conn:
        row = _lease_row(conn, tenant_id, lease_id)
        if row["released_at"] is not None:
            raise BusinessError(LEASE_LOST_CODE, "lease was released")
        if row["holder"] != who or str(row["fencing_token"]) != token:
            raise BusinessError(
                LEASE_LOST_CODE, "stale fencing token or wrong holder"
            )
        if float(row["expires_at"]) <= moment:
            raise BusinessError(LEASE_LOST_CODE, "lease expired; work must stop")
        return Lease.from_row(row)


def renew_lease(
    store: "BusinessStore",
    tenant_id: str,
    lease_id: str,
    *,
    holder: str,
    fencing_token: str,
    ttl_seconds: float = 300.0,
    now: float | None = None,
) -> Lease:
    who = _text(holder, "holder", max_len=200)
    token = _text(fencing_token, "fencing_token", max_len=64)
    if ttl_seconds <= 0:
        raise BusinessError(BAD_INPUT_CODE, "ttl_seconds must be positive")
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _lease_row(conn, tenant_id, lease_id)
        if row["released_at"] is not None:
            raise BusinessError(LEASE_LOST_CODE, "lease was released")
        if row["holder"] != who or str(row["fencing_token"]) != token:
            raise BusinessError(
                LEASE_LOST_CODE, "stale fencing token or wrong holder"
            )
        if float(row["expires_at"]) <= moment:
            raise BusinessError(LEASE_LOST_CODE, "lease expired; reacquire it")
        conn.execute(
            "UPDATE bos_lease SET expires_at=?, heartbeat_at=?"
            " WHERE tenant_id=? AND lease_id=?",
            (moment + float(ttl_seconds), now_iso(moment), tenant_id, lease_id),
        )
    return get_lease(store, tenant_id, lease_id)


def release_lease(
    store: "BusinessStore",
    tenant_id: str,
    lease_id: str,
    *,
    holder: str,
    fencing_token: str,
    actor: str | None = None,
    now: float | None = None,
) -> Lease:
    who = _text(holder, "holder", max_len=200)
    token = _text(fencing_token, "fencing_token", max_len=64)
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _lease_row(conn, tenant_id, lease_id)
        if row["released_at"] is not None:
            return Lease.from_row(row)
        if row["holder"] != who or str(row["fencing_token"]) != token:
            raise BusinessError(
                LEASE_LOST_CODE, "stale fencing token or wrong holder"
            )
        conn.execute(
            "UPDATE bos_lease SET released_at=? WHERE tenant_id=? AND lease_id=?",
            (now_iso(moment), tenant_id, lease_id),
        )
        store._audit(
            conn, tenant_id, None, actor or who, "lease.release", "lease",
            lease_id, "ok",
            {"resource": f"{row['resource_type']}:{row['resource_id']}"},
        )
    return get_lease(store, tenant_id, lease_id)


# -- run status over jobs --------------------------------------------------
def run_status_for_job(job: "JobRun") -> str:
    """Project the canonical Run status from a ``bos_job`` row."""
    try:
        return RUN_STATUS_BY_JOB[job.status]
    except KeyError as exc:  # pragma: no cover - job.status is CHECKed
        raise BusinessError(
            BAD_STATE_CODE, f"unknown job status {job.status!r}"
        ) from exc


def assert_run_transition(current: str, target: str) -> str:
    """Refuse an illegal Run transition; returns the target on success."""
    if current not in RUN_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown run status {current!r}")
    if target not in RUN_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown run status {target!r}")
    if current == target:
        return target
    if target not in RUN_TRANSITIONS[current]:
        raise BusinessError(
            RUN_ILLEGAL_TRANSITION_CODE,
            f"run cannot move {current!r} -> {target!r}",
        )
    return target


def run_view(store: "BusinessStore", tenant_id: str, job_id: str) -> dict[str, Any]:
    """Read-only run projection over the job + its checkpoints."""
    from . import jobs

    job = jobs.get(store, tenant_id, job_id)
    status = run_status_for_job(job)
    with store.read() as conn:
        checkpoint = conn.execute(
            "SELECT MAX(sequence) FROM bos_checkpoint WHERE tenant_id=?"
            " AND run_id=?",
            (tenant_id, job_id),
        ).fetchone()
    last_sequence = int(checkpoint[0]) if checkpoint and checkpoint[0] else 0
    return {
        "job_id": job.job_id,
        "tenant_id": tenant_id,
        "business_id": job.business_id,
        "status": status,
        "job_status": job.status,
        "last_checkpoint_sequence": last_sequence,
    }


__all__ = [
    "HANDOFF_RECIPIENT_MISMATCH_CODE",
    "LEASE_CONFLICT_CODE",
    "LEASE_LOST_CODE",
    "LEASE_NOT_FOUND_CODE",
    "Lease",
    "RUN_ILLEGAL_TRANSITION_CODE",
    "RUN_STATUSES",
    "RUN_STATUS_BY_JOB",
    "RUN_TRANSITIONS",
    "STORAGE_CLASSES",
    "TASK_DEPENDENCIES_INCOMPLETE_CODE",
    "TASK_NOT_FOUND_CODE",
    "TASK_READ_ONLY_CODE",
    "TASK_TRANSITIONS",
    "TERMINAL_WORKFLOW_STATUSES",
    "WORKFLOW_STATUSES",
    "WORKSPACE_NOT_FOUND_CODE",
    "WORKSPACE_REVISION_CONFLICT_CODE",
    "WorkTask",
    "Workspace",
    "accept_handoff",
    "acquire_lease",
    "assert_fence",
    "assert_run_transition",
    "claim_task",
    "create_task",
    "create_workspace",
    "get_lease",
    "get_task",
    "get_workspace",
    "handoff_task",
    "list_leases",
    "list_tasks",
    "list_workspaces",
    "release_lease",
    "renew_lease",
    "revise_workspace",
    "run_status_for_job",
    "run_view",
    "transition_task",
]
