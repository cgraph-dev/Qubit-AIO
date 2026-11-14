"""Durable sessions, task-pinned workspaces, checkpoints and explicit handoffs.

P2-02. A session is the durable owner<->agent lane. It never duplicates
execution truth: a run is still ``bos_job``, a durable task is still
``bos_task`` (``task_class='execution'``) and a workspace is still
``bos_workspace``. This module adds the records *around* those:

* :class:`Session` — participants, visibility, default memory scopes, the
  pinned workspace and the last checkpoint.
* :class:`Handoff` — an explicit, recipient-accepted transfer of one durable
  task. Proposing never moves ownership; only acceptance does, and rejection
  leaves the source exactly where it was.

Every mutation appends an :mod:`eventlog` event in the same transaction as the
row change (aggregate ``session:<id>`` or ``handoff:<id>``), so history is
append-only and rebuildable. Nothing here opens a second database or claims
work: a session with a lost lease reports that honestly and never starts a run.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from . import durable, eventlog, runs
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    HANDOFF_BAD_STATE_CODE,
    HANDOFF_EXISTS_CODE,
    HANDOFF_EXPIRED_CODE,
    HANDOFF_NOT_FOUND_CODE,
    HANDOFF_RECIPIENT_MISMATCH_CODE,
    NOT_FOUND_CODE,
    SESSION_BAD_STATE_CODE,
    SESSION_NOT_FOUND_CODE,
    SESSION_WORKSPACE_MISMATCH_CODE,
    BusinessError,
)
from .ids import new_id
from .util import json_dumps, json_loads, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

SESSION_STATUSES = ("active", "paused", "archived")
SESSION_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"paused", "archived"}),
    "paused": frozenset({"active", "archived"}),
    "archived": frozenset(),
}
SESSION_VISIBILITIES = (
    "private",
    "owner",
    "participants",
    "department",
    "office",
    "tenant",
)
MEMORY_SCOPES = ("agent", "session", "task", "department", "office", "tenant")
PARTICIPANT_TYPES = ("owner", "agent", "system")
HANDOFF_STATUSES = ("proposed", "accepted", "rejected", "cancelled")

_MAX_PARTICIPANTS = 64
_MAX_ARTIFACTS = 128
_MAX_QUESTIONS = 64
_MAX_CAPABILITIES = 64


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _stamp(now: float | None) -> tuple[float, str]:
    moment = time.time() if now is None else float(now)
    return moment, now_iso(moment)


def _json(value: Any) -> str:
    return json_dumps(redact(value))


def _participants(
    value: Iterable[Mapping[str, Any]] | None,
    *,
    owner_principal: str,
    agent_principal: str,
) -> list[dict[str, Any]]:
    if value is None:
        raw: list[Any] = [
            {"principal_type": "owner", "principal_id": owner_principal,
             "role": "owner"},
            {"principal_type": "agent", "principal_id": agent_principal,
             "role": "executor"},
        ]
    else:
        if isinstance(value, (str, bytes, Mapping)):
            raise BusinessError(
                BAD_INPUT_CODE, "participants must be a list of mappings"
            )
        raw = list(value)
    if not raw:
        raise BusinessError(BAD_INPUT_CODE, "a session needs at least one participant")
    if len(raw) > _MAX_PARTICIPANTS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"a session may have at most {_MAX_PARTICIPANTS} participants",
        )
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise BusinessError(
                BAD_INPUT_CODE, "each participant must be a mapping"
            )
        kind = _text(item.get("principal_type"), "principal_type", max_len=20)
        if kind not in PARTICIPANT_TYPES:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown principal_type {kind!r}; expected one of"
                f" {', '.join(PARTICIPANT_TYPES)}",
            )
        principal = _text(item.get("principal_id"), "principal_id", max_len=80)
        role = _optional_text(item.get("role"), "participant role", max_len=64)
        key = f"{kind}:{principal}"
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            {
                "principal_type": kind,
                "principal_id": principal,
                "role": role,
            }
        )
    return cleaned


def _memory_scopes(value: Iterable[str] | None) -> list[str]:
    if value is None:
        return ["agent", "session"]
    if isinstance(value, (str, bytes, Mapping)):
        raise BusinessError(
            BAD_INPUT_CODE, "default_memory_scopes must be a list of scopes"
        )
    cleaned: list[str] = []
    for item in value:
        scope = _text(item, "memory scope", max_len=40)
        if scope not in MEMORY_SCOPES:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown memory scope {scope!r}; expected one of"
                f" {', '.join(MEMORY_SCOPES)}",
            )
        if scope not in cleaned:
            cleaned.append(scope)
    return cleaned


def _string_list(
    value: Iterable[str] | None, label: str, *, limit: int
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a list")
    items = list(value)
    if len(items) > limit:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} may hold at most {limit} entries"
        )
    cleaned: list[str] = []
    for item in items:
        token = _text(item, label, max_len=500)
        if token not in cleaned:
            cleaned.append(token)
    return cleaned


def _deadline(value: Any) -> tuple[str | None, float | None]:
    """Accept an ISO-8601 timestamp or epoch seconds; return both forms."""
    if value is None:
        return None, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        moment = float(value)
        return now_iso(moment), moment
    raw = _text(value, "acceptance_deadline", max_len=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BusinessError(
            BAD_INPUT_CODE, f"acceptance_deadline is not ISO-8601: {raw!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    moment = parsed.timestamp()
    return now_iso(moment), moment


def _principal_row(
    conn: sqlite3.Connection, tenant_id: str, principal_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_principal WHERE tenant_id=? AND principal_id=?",
        (tenant_id, principal_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no principal {principal_id!r} in tenant {tenant_id!r}",
        )
    return row


def _require_active_principal(
    conn: sqlite3.Connection, tenant_id: str, principal_id: str
) -> sqlite3.Row:
    row = _principal_row(conn, tenant_id, principal_id)
    if row["status"] != "active":
        raise BusinessError(
            BAD_STATE_CODE, f"principal {principal_id!r} is {row['status']!r}"
        )
    return row


def _session_row(
    conn: sqlite3.Connection, tenant_id: str, session_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_session WHERE tenant_id=? AND session_id=?",
        (tenant_id, session_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            SESSION_NOT_FOUND_CODE,
            f"no session {session_id!r} in tenant {tenant_id!r}",
        )
    return row


def _handoff_row(
    conn: sqlite3.Connection, tenant_id: str, handoff_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_handoff WHERE tenant_id=? AND handoff_id=?",
        (tenant_id, handoff_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            HANDOFF_NOT_FOUND_CODE,
            f"no handoff {handoff_id!r} in tenant {tenant_id!r}",
        )
    return row


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
            NOT_FOUND_CODE,
            f"no durable task {task_id!r} in tenant {tenant_id!r}",
        )
    return row


def _workspace_task_id(
    conn: sqlite3.Connection, tenant_id: str, workspace_id: str
) -> str:
    row = conn.execute(
        "SELECT task_id FROM bos_workspace WHERE tenant_id=? AND workspace_id=?",
        (tenant_id, workspace_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no workspace {workspace_id!r} in tenant {tenant_id!r}",
        )
    return row["task_id"]


def _agent_row(
    conn: sqlite3.Connection, tenant_id: str, agent_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_agent WHERE tenant_id=? AND agent_id=?",
        (tenant_id, agent_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no agent {agent_id!r} in tenant {tenant_id!r}",
        )
    return row


def _agent_principal(
    conn: sqlite3.Connection, tenant_id: str, agent_row: sqlite3.Row
) -> str:
    """The agent's security principal (never a desk/room).

    Read-old rows may not have ``security_principal_id``; a principal whose
    ``external_ref`` is the agent id is accepted in that case. No principal is
    a typed refusal: a session without a security subject cannot be durable.
    """
    explicit = agent_row["security_principal_id"]
    if explicit:
        row = _principal_row(conn, tenant_id, explicit)
        if row["status"] != "active":
            raise BusinessError(
                SESSION_BAD_STATE_CODE,
                f"agent security principal {explicit!r} is {row['status']!r}",
            )
        return explicit
    row = conn.execute(
        "SELECT * FROM bos_principal WHERE tenant_id=? AND external_ref=?"
        " AND kind IN ('agent','service') ORDER BY created_at, principal_id"
        " LIMIT 1",
        (tenant_id, agent_row["agent_id"]),
    ).fetchone()
    if row is None:
        raise BusinessError(
            SESSION_BAD_STATE_CODE,
            f"agent {agent_row['agent_id']!r} has no security principal;"
            " create one and set security_principal_id",
        )
    if row["status"] != "active":
        raise BusinessError(
            SESSION_BAD_STATE_CODE,
            f"agent security principal {row['principal_id']!r} is"
            f" {row['status']!r}",
        )
    return row["principal_id"]


# -- records ---------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    tenant_id: str
    business_id: str
    agent_id: str
    owner_principal: str
    participants: tuple[dict[str, Any], ...]
    visibility: str
    default_memory_scopes: tuple[str, ...]
    default_workspace_id: str | None
    status: str
    last_checkpoint_id: str | None
    policy_revision: int
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    archived_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> "Session":
        data = dict(row)
        return cls(
            session_id=data["session_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            agent_id=data["agent_id"],
            owner_principal=data["owner_principal"],
            participants=tuple(_mapping_list(data.get("participants_json"))),
            visibility=data["visibility"],
            default_memory_scopes=tuple(
                str(item) for item in _list(data.get("default_memory_scopes_json"))
            ),
            default_workspace_id=data.get("default_workspace_id"),
            status=data["status"],
            last_checkpoint_id=data.get("last_checkpoint_id"),
            policy_revision=int(data["policy_revision"]),
            metadata=json_loads(data.get("metadata_json"), {}),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            archived_at=data.get("archived_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "agent_id": self.agent_id,
            "owner_principal": self.owner_principal,
            "participants": [dict(item) for item in self.participants],
            "visibility": self.visibility,
            "default_memory_scopes": list(self.default_memory_scopes),
            "default_workspace_id": self.default_workspace_id,
            "status": self.status,
            "last_checkpoint_id": self.last_checkpoint_id,
            "policy_revision": self.policy_revision,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }


@dataclass(frozen=True, slots=True)
class Handoff:
    handoff_id: str
    tenant_id: str
    business_id: str
    task_id: str
    session_id: str
    from_principal: str
    to_principal: str
    reason: str
    checkpoint_id: str | None
    artifact_ids: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    acceptance_deadline: str | None
    status: str
    created_at: str
    updated_at: str
    accepted_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> "Handoff":
        data = dict(row)
        return cls(
            handoff_id=data["handoff_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            task_id=data["task_id"],
            session_id=data["session_id"],
            from_principal=data["from_principal"],
            to_principal=data["to_principal"],
            reason=data.get("reason", ""),
            checkpoint_id=data.get("checkpoint_id"),
            artifact_ids=tuple(
                str(item) for item in _list(data.get("artifact_ids_json"))
            ),
            unresolved_questions=tuple(
                str(item) for item in _list(data.get("unresolved_questions_json"))
            ),
            required_capabilities=tuple(
                str(item) for item in _list(data.get("required_capabilities_json"))
            ),
            acceptance_deadline=data.get("acceptance_deadline"),
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            accepted_at=data.get("accepted_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "from_principal": self.from_principal,
            "to_principal": self.to_principal,
            "reason": self.reason,
            "checkpoint_id": self.checkpoint_id,
            "artifact_ids": list(self.artifact_ids),
            "unresolved_questions": list(self.unresolved_questions),
            "required_capabilities": list(self.required_capabilities),
            "acceptance_deadline": self.acceptance_deadline,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "accepted_at": self.accepted_at,
        }


def _list(value: Any) -> list[Any]:
    return json_loads(value, []) if value else []


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in _list(value) if isinstance(item, Mapping)]


# -- sessions --------------------------------------------------------------
def create_session(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    agent_id: str,
    *,
    owner_principal: str,
    participants: Iterable[Mapping[str, Any]] | None = None,
    visibility: str = "private",
    default_memory_scopes: Iterable[str] | None = None,
    default_workspace_id: str | None = None,
    task_id: str | None = None,
    task_title: str | None = None,
    repository_ref: str | None = None,
    worktree_ref: str | None = None,
    storage_class: str = "ephemeral",
    access_policy: Mapping[str, Any] | None = None,
    policy_revision: int = 1,
    metadata: Mapping[str, Any] | None = None,
    actor: str,
    session_id: str | None = None,
    now: float | None = None,
) -> Session:
    """Create a durable session, optionally minting its task+workspace.

    When ``task_title`` is supplied the durable task and its pinned workspace
    are created through :mod:`runs`; this module never invents a second
    task/workspace record.
    """
    sid = _text(session_id or new_id("ses"), "session_id", max_len=80)
    clean_visibility = _text(visibility, "visibility", max_len=20)
    if clean_visibility not in SESSION_VISIBILITIES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown visibility {clean_visibility!r}; expected one of"
            f" {', '.join(SESSION_VISIBILITIES)}",
        )
    if (
        not isinstance(policy_revision, int)
        or isinstance(policy_revision, bool)
        or policy_revision < 1
    ):
        raise BusinessError(
            BAD_INPUT_CODE, "policy_revision must be an integer >= 1"
        )
    if metadata is not None and not isinstance(metadata, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "metadata must be a mapping")
    memory = _memory_scopes(default_memory_scopes)
    moment, stamp = _stamp(now)

    # Validate and, when asked, mint the task/workspace BEFORE opening the
    # session write transaction: ``runs`` owns its own transactions and a
    # nested BEGIN IMMEDIATE here would deadlock on SQLite's write lock.
    with store.read() as conn:
        store._require_business(conn, tenant_id, business_id)
        exists = conn.execute(
            "SELECT 1 FROM bos_session WHERE tenant_id=? AND session_id=?",
            (tenant_id, sid),
        ).fetchone()
        if exists is not None:
            raise BusinessError(
                SESSION_BAD_STATE_CODE, f"session {sid!r} already exists"
            )
        agent = _agent_row(conn, tenant_id, agent_id)
        if agent["business_id"] != business_id:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"agent {agent_id!r} does not belong to business {business_id!r}",
            )
        if agent["status"] == "archived":
            raise BusinessError(
                SESSION_BAD_STATE_CODE, f"agent {agent_id!r} is archived"
            )
        _require_active_principal(conn, tenant_id, owner_principal)
        agent_principal = _agent_principal(conn, tenant_id, agent)
        members = _participants(
            participants,
            owner_principal=owner_principal,
            agent_principal=agent_principal,
        )
        for member in members:
            if member["principal_type"] == "system":
                continue
            _principal_row(conn, tenant_id, member["principal_id"])
        resolved_task_id = _optional_text(task_id, "task_id", max_len=80)
        if resolved_task_id is not None:
            task_row = _task_row(conn, tenant_id, resolved_task_id)
            if task_row["business_id"] != business_id:
                raise BusinessError(
                    BAD_INPUT_CODE,
                    f"task {resolved_task_id!r} does not belong to this business",
                )
        workspace_id = _optional_text(
            default_workspace_id, "default_workspace_id", max_len=80
        )

    if task_title is not None and resolved_task_id is None:
        task = runs.create_task(
            store,
            tenant_id,
            business_id,
            _text(task_title, "task_title", max_len=500),
            objective=str(task_title)[:4000],
            owner_principal=owner_principal,
            assignee_principal=agent_principal,
            actor=actor,
            now=moment,
        )
        resolved_task_id = task.task_id
        workspace = runs.create_workspace(
            store,
            tenant_id,
            task_id=resolved_task_id,
            session_id=sid,
            repository_ref=repository_ref,
            worktree_ref=worktree_ref,
            storage_class=storage_class,
            access_policy=access_policy,
            actor=actor,
            now=moment,
        )
        workspace_id = workspace.workspace_id
    elif workspace_id is not None:
        workspace = runs.get_workspace(store, tenant_id, workspace_id)
        if workspace.session_id != sid:
            raise BusinessError(
                SESSION_WORKSPACE_MISMATCH_CODE,
                f"workspace {workspace_id!r} belongs to session"
                f" {workspace.session_id!r}, not {sid!r}",
            )

    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        _agent_row(conn, tenant_id, agent_id)
        try:
            conn.execute(
                "INSERT INTO bos_session(session_id, tenant_id, business_id,"
                " agent_id, owner_principal, participants_json, visibility,"
                " default_memory_scopes_json, default_workspace_id, status,"
                " last_checkpoint_id, policy_revision, metadata_json,"
                " created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,'active',NULL,?,?,?,?)",
                (
                    sid,
                    tenant_id,
                    business_id,
                    agent_id,
                    owner_principal,
                    _json(members),
                    clean_visibility,
                    json_dumps(memory),
                    workspace_id,
                    policy_revision,
                    json_dumps(redact(dict(metadata or {}))),
                    stamp,
                    stamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise BusinessError(
                SESSION_BAD_STATE_CODE, f"session {sid!r} already exists"
            ) from exc
        eventlog.append_event(
            store,
            tenant_id,
            f"session:{sid}",
            "session.created",
            {
                "business_id": business_id,
                "agent_id": agent_id,
                "owner_principal": owner_principal,
                "visibility": clean_visibility,
                "default_workspace_id": workspace_id,
                "task_id": resolved_task_id,
            },
            actor=actor,
            idempotency_key=f"session.created:{sid}",
            business_id=business_id,
            conn=conn,
            now=moment,
        )
        store._audit(
            conn, tenant_id, business_id, actor, "session.create", "session",
            sid, "ok",
            {"agent_id": agent_id, "workspace_id": workspace_id},
        )
    return get_session(store, tenant_id, sid)


def get_session(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> Session:
    with store.read() as conn:
        return Session.from_row(_session_row(conn, tenant_id, session_id))


def list_sessions(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    agent_id: str | None = None,
    status: str | None = None,
) -> list[Session]:
    if status is not None and status not in SESSION_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown session status {status!r}")
    query = "SELECT * FROM bos_session WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if agent_id is not None:
        query += " AND agent_id=?"
        params.append(agent_id)
    if status is not None:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, session_id"
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(query, params).fetchall()
    return [Session.from_row(row) for row in rows]


def set_session_status(
    store: "BusinessStore",
    tenant_id: str,
    session_id: str,
    target: str,
    *,
    actor: str,
    note: str = "",
    now: float | None = None,
) -> Session:
    target_clean = _text(target, "session status", max_len=20)
    if target_clean not in SESSION_STATUSES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown session status {target_clean!r}; expected one of"
            f" {', '.join(SESSION_STATUSES)}",
        )
    moment, stamp = _stamp(now)
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        current = row["status"]
        if current == target_clean:
            return Session.from_row(row)
        if target_clean not in SESSION_TRANSITIONS[current]:
            raise BusinessError(
                SESSION_BAD_STATE_CODE,
                f"session cannot move {current!r} -> {target_clean!r}",
            )
        archived_at = stamp if target_clean == "archived" else row["archived_at"]
        conn.execute(
            "UPDATE bos_session SET status=?, archived_at=?, updated_at=?"
            " WHERE tenant_id=? AND session_id=?",
            (target_clean, archived_at, stamp, tenant_id, session_id),
        )
        kind = {
            "paused": "session.paused",
            "active": "session.resumed",
            "archived": "session.archived",
        }[target_clean]
        eventlog.append_event(
            store,
            tenant_id,
            f"session:{session_id}",
            kind,
            {"from": current, "to": target_clean, "note": str(note)[:2000]},
            actor=actor,
            idempotency_key=f"{kind}:{session_id}:{stamp}",
            business_id=row["business_id"],
            conn=conn,
            now=moment,
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "session.status",
            "session", session_id, "ok",
            {"from": current, "to": target_clean},
        )
    return get_session(store, tenant_id, session_id)


def bind_workspace(
    store: "BusinessStore",
    tenant_id: str,
    session_id: str,
    workspace_id: str,
    *,
    actor: str,
    now: float | None = None,
) -> Session:
    """Bind an existing task-pinned workspace to this session.

    A workspace belongs to exactly one session (it stores ``session_id``), so
    two sessions can never become concurrent writers to the same worktree.
    """
    wid = _text(workspace_id, "workspace_id", max_len=80)
    moment, stamp = _stamp(now)
    workspace = runs.get_workspace(store, tenant_id, wid)
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        if row["status"] == "archived":
            raise BusinessError(
                SESSION_BAD_STATE_CODE, f"session {session_id!r} is archived"
            )
        if workspace.session_id != session_id:
            raise BusinessError(
                SESSION_WORKSPACE_MISMATCH_CODE,
                f"workspace {wid!r} belongs to session"
                f" {workspace.session_id!r}, not {session_id!r}",
            )
        if row["default_workspace_id"] == wid:
            return Session.from_row(row)
        conn.execute(
            "UPDATE bos_session SET default_workspace_id=?, updated_at=?"
            " WHERE tenant_id=? AND session_id=?",
            (wid, stamp, tenant_id, session_id),
        )
        eventlog.append_event(
            store,
            tenant_id,
            f"session:{session_id}",
            "session.workspace_bound",
            {"workspace_id": wid, "task_id": workspace.task_id},
            actor=actor,
            idempotency_key=f"session.workspace_bound:{session_id}:{wid}",
            business_id=row["business_id"],
            conn=conn,
            now=moment,
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "session.bind_workspace",
            "session", session_id, "ok", {"workspace_id": wid},
        )
    return get_session(store, tenant_id, session_id)


def record_checkpoint(
    store: "BusinessStore",
    tenant_id: str,
    session_id: str,
    *,
    run_id: str,
    sequence: int,
    state: Any,
    actor: str,
    job_id: str | None = None,
    pending_actions: Any = None,
    tool_receipts: Any = None,
    artifact_ids: Any = None,
    workspace_revision: str | None = None,
    policy_revision: str | None = None,
    now: float | None = None,
) -> durable.Checkpoint:
    """Append a recovery checkpoint and point the session at it."""
    session = get_session(store, tenant_id, session_id)
    if session.status == "archived":
        raise BusinessError(
            SESSION_BAD_STATE_CODE, f"session {session_id!r} is archived"
        )
    moment, stamp = _stamp(now)
    checkpoint = durable.record_checkpoint(
        store,
        tenant_id,
        session.business_id,
        run_id,
        sequence,
        state=state,
        pending_actions=pending_actions,
        tool_receipts=tool_receipts,
        artifact_ids=artifact_ids,
        workspace_revision=workspace_revision,
        policy_revision=policy_revision,
        actor=actor,
        job_id=job_id,
    )
    with store.transaction() as conn:
        _session_row(conn, tenant_id, session_id)
        conn.execute(
            "UPDATE bos_session SET last_checkpoint_id=?, updated_at=?"
            " WHERE tenant_id=? AND session_id=?",
            (checkpoint.checkpoint_id, stamp, tenant_id, session_id),
        )
        eventlog.append_event(
            store,
            tenant_id,
            f"session:{session_id}",
            "session.checkpointed",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "run_id": run_id,
                "sequence": sequence,
            },
            actor=actor,
            idempotency_key=f"session.checkpointed:{session_id}:{checkpoint.checkpoint_id}",
            business_id=session.business_id,
            conn=conn,
            now=moment,
        )
    return checkpoint


def last_checkpoint(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> durable.Checkpoint | None:
    session = get_session(store, tenant_id, session_id)
    if not session.last_checkpoint_id:
        return None
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_checkpoint WHERE tenant_id=? AND checkpoint_id=?",
            (tenant_id, session.last_checkpoint_id),
        ).fetchone()
    return durable.Checkpoint.from_row(row) if row is not None else None


# -- handoffs --------------------------------------------------------------
def _transferred(task: runs.WorkTask, *, source: str, recipient: str) -> bool:
    return (
        task.assignee_principal == recipient
        and task.handed_off_to == recipient
        and task.handed_off_from == source
    )


def propose_handoff(
    store: "BusinessStore",
    tenant_id: str,
    session_id: str,
    *,
    to_principal: str,
    actor: str,
    task_id: str | None = None,
    from_principal: str | None = None,
    reason: str = "",
    checkpoint_id: str | None = None,
    artifact_ids: Iterable[str] | None = None,
    unresolved_questions: Iterable[str] | None = None,
    required_capabilities: Iterable[str] | None = None,
    acceptance_deadline: Any = None,
    handoff_id: str | None = None,
    now: float | None = None,
) -> Handoff:
    """Record an explicit handoff offer. Ownership does not move yet."""
    recipient = _text(to_principal, "to_principal", max_len=80)
    moment, stamp = _stamp(now)
    deadline_text, deadline_moment = _deadline(acceptance_deadline)
    artifacts = _string_list(
        artifact_ids, "artifact_ids", limit=_MAX_ARTIFACTS
    )
    questions = _string_list(
        unresolved_questions, "unresolved_questions", limit=_MAX_QUESTIONS
    )
    capabilities = _string_list(
        required_capabilities, "required_capabilities", limit=_MAX_CAPABILITIES
    )
    hid = _text(handoff_id or new_id("hnd"), "handoff_id", max_len=80)

    with store.transaction() as conn:
        session_row = _session_row(conn, tenant_id, session_id)
        if session_row["status"] == "archived":
            raise BusinessError(
                SESSION_BAD_STATE_CODE, f"session {session_id!r} is archived"
            )
        session = Session.from_row(session_row)
        source = _optional_text(
            from_principal, "from_principal", max_len=80
        ) or _agent_principal(
            conn, tenant_id, _agent_row(conn, tenant_id, session.agent_id)
        )
        if recipient == source:
            raise BusinessError(
                HANDOFF_RECIPIENT_MISMATCH_CODE,
                "a handoff needs a recipient other than the source principal",
            )
        _require_active_principal(conn, tenant_id, recipient)
        _principal_row(conn, tenant_id, source)

        resolved_task = _optional_text(task_id, "task_id", max_len=80)
        if resolved_task is None:
            if not session.default_workspace_id:
                raise BusinessError(
                    BAD_INPUT_CODE,
                    "the session has no pinned workspace; bind one before"
                    " handing off",
                )
            resolved_task = _workspace_task_id(
                conn, tenant_id, session.default_workspace_id
            )
        task = runs.WorkTask.from_row(_task_row(conn, tenant_id, resolved_task))
        if task.business_id != session.business_id:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"task {resolved_task!r} does not belong to this business",
            )
        if task.status in runs.TERMINAL_WORKFLOW_STATUSES:
            raise BusinessError(
                HANDOFF_BAD_STATE_CODE,
                f"task {resolved_task!r} is {task.status!r} and cannot move",
            )
        if deadline_moment is not None and deadline_moment <= moment:
            raise BusinessError(
                HANDOFF_EXPIRED_CODE,
                "acceptance_deadline is not in the future",
            )
        for artifact_id in artifacts:
            found = conn.execute(
                "SELECT 1 FROM bos_artifact WHERE tenant_id=? AND artifact_id=?",
                (tenant_id, artifact_id),
            ).fetchone()
            if found is None:
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"no artifact {artifact_id!r} in tenant {tenant_id!r}",
                )
        checkpoint = _optional_text(
            checkpoint_id, "checkpoint_id", max_len=80
        )
        if checkpoint is not None:
            found = conn.execute(
                "SELECT 1 FROM bos_checkpoint WHERE tenant_id=? AND checkpoint_id=?",
                (tenant_id, checkpoint),
            ).fetchone()
            if found is None:
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"no checkpoint {checkpoint!r} in tenant {tenant_id!r}",
                )
        existing = conn.execute(
            "SELECT * FROM bos_handoff WHERE tenant_id=? AND task_id=?"
            " AND status='proposed'",
            (tenant_id, resolved_task),
        ).fetchone()
        if existing is not None:
            raise BusinessError(
                HANDOFF_EXISTS_CODE,
                f"task {resolved_task!r} already has proposed handoff"
                f" {existing['handoff_id']!r}",
            )
        try:
            conn.execute(
                "INSERT INTO bos_handoff(handoff_id, tenant_id, business_id,"
                " task_id, session_id, from_principal, to_principal, reason,"
                " checkpoint_id, artifact_ids_json, unresolved_questions_json,"
                " required_capabilities_json, acceptance_deadline, status,"
                " created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'proposed',?,?)",
                (
                    hid,
                    tenant_id,
                    session.business_id,
                    resolved_task,
                    session_id,
                    source,
                    recipient,
                    str(reason or "")[:2000],
                    checkpoint,
                    json_dumps(artifacts),
                    json_dumps(questions),
                    json_dumps(capabilities),
                    deadline_text,
                    stamp,
                    stamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise BusinessError(
                HANDOFF_EXISTS_CODE, f"handoff {hid!r} already exists"
            ) from exc
        eventlog.append_event(
            store,
            tenant_id,
            f"handoff:{hid}",
            "handoff.proposed",
            {
                "task_id": resolved_task,
                "session_id": session_id,
                "from": source,
                "to": recipient,
                "checkpoint_id": checkpoint,
                "artifact_ids": artifacts,
                "unresolved_questions": questions,
                "required_capabilities": capabilities,
            },
            actor=actor,
            idempotency_key=f"handoff.proposed:{hid}",
            business_id=session.business_id,
            conn=conn,
            now=moment,
        )
        store._audit(
            conn, tenant_id, session.business_id, actor, "handoff.propose",
            "handoff", hid, "ok", {"task_id": resolved_task, "to": recipient},
        )
    return get_handoff(store, tenant_id, hid)


def get_handoff(
    store: "BusinessStore", tenant_id: str, handoff_id: str
) -> Handoff:
    with store.read() as conn:
        return Handoff.from_row(_handoff_row(conn, tenant_id, handoff_id))


def list_handoffs(
    store: "BusinessStore",
    tenant_id: str,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    status: str | None = None,
) -> list[Handoff]:
    if status is not None and status not in HANDOFF_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown handoff status {status!r}")
    query = "SELECT * FROM bos_handoff WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if session_id is not None:
        query += " AND session_id=?"
        params.append(session_id)
    if task_id is not None:
        query += " AND task_id=?"
        params.append(task_id)
    if status is not None:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, handoff_id"
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(query, params).fetchall()
    return [Handoff.from_row(row) for row in rows]


def accept_handoff(
    store: "BusinessStore",
    tenant_id: str,
    handoff_id: str,
    *,
    principal: str,
    actor: str | None = None,
    now: float | None = None,
) -> Handoff:
    """Recipient-only acceptance; the durable task moves to the recipient."""
    who = _text(principal, "principal", max_len=80)
    moment, stamp = _stamp(now)

    # Validate first on a read connection. The durable task transition lives in
    # ``runs`` and owns its own transaction, so it must not run inside ours.
    with store.read() as conn:
        row = _handoff_row(conn, tenant_id, handoff_id)
        if row["status"] != "proposed":
            raise BusinessError(
                HANDOFF_BAD_STATE_CODE,
                f"handoff {handoff_id!r} is {row['status']!r}, not 'proposed'",
            )
        if row["to_principal"] != who:
            raise BusinessError(
                HANDOFF_RECIPIENT_MISMATCH_CODE,
                f"handoff was offered to {row['to_principal']!r}, not {who!r}",
            )
        _require_active_principal(conn, tenant_id, who)
        session_row = _session_row(conn, tenant_id, row["session_id"])
        if session_row["status"] == "archived":
            raise BusinessError(
                SESSION_BAD_STATE_CODE,
                f"session {row['session_id']!r} is archived",
            )
        if row["acceptance_deadline"]:
            _, deadline = _deadline(row["acceptance_deadline"])
            if deadline is not None and moment >= deadline:
                raise BusinessError(
                    HANDOFF_EXPIRED_CODE,
                    f"handoff {handoff_id!r} passed its acceptance deadline",
                )
        task = runs.WorkTask.from_row(_task_row(conn, tenant_id, row["task_id"]))
        if task.status in runs.TERMINAL_WORKFLOW_STATUSES:
            raise BusinessError(
                HANDOFF_BAD_STATE_CODE,
                f"task {task.task_id!r} is {task.status!r} and cannot move",
            )

    if not _transferred(task, source=row["from_principal"], recipient=who):
        # The durable task transition is the source of truth; do it through
        # runs so its events, read-only fence and assignee all agree.
        if task.status != "handed_off":
            runs.handoff_task(
                store,
                tenant_id,
                task.task_id,
                to_principal=who,
                actor=row["from_principal"],
                reason=row["reason"],
                now=moment,
            )
        runs.accept_handoff(
            store, tenant_id, task.task_id, principal=who, now=moment
        )

    with store.transaction() as conn:
        changed = conn.execute(
            "UPDATE bos_handoff SET status='accepted', accepted_at=?,"
            " updated_at=? WHERE tenant_id=? AND handoff_id=?"
            " AND status='proposed'",
            (stamp, stamp, tenant_id, handoff_id),
        ).rowcount
        if changed != 1:
            current = _handoff_row(conn, tenant_id, handoff_id)
            raise BusinessError(
                HANDOFF_BAD_STATE_CODE,
                f"handoff {handoff_id!r} is {current['status']!r}, not 'proposed'",
            )
        eventlog.append_event(
            store,
            tenant_id,
            f"handoff:{handoff_id}",
            "handoff.accepted",
            {
                "task_id": row["task_id"],
                "session_id": row["session_id"],
                "from": row["from_principal"],
                "to": who,
                "checkpoint_id": row["checkpoint_id"],
            },
            actor=actor or who,
            idempotency_key=f"handoff.accepted:{handoff_id}",
            business_id=row["business_id"],
            conn=conn,
            now=moment,
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor or who,
            "handoff.accept", "handoff", handoff_id, "ok",
            {"task_id": row["task_id"], "to": who},
        )
    return get_handoff(store, tenant_id, handoff_id)


def reject_handoff(
    store: "BusinessStore",
    tenant_id: str,
    handoff_id: str,
    *,
    principal: str,
    reason: str = "",
    actor: str | None = None,
    now: float | None = None,
) -> Handoff:
    """Reject an offer. The source task keeps its ownership unchanged."""
    who = _text(principal, "principal", max_len=80)
    moment, stamp = _stamp(now)
    with store.transaction() as conn:
        row = _handoff_row(conn, tenant_id, handoff_id)
        if row["status"] != "proposed":
            raise BusinessError(
                HANDOFF_BAD_STATE_CODE,
                f"handoff {handoff_id!r} is {row['status']!r}, not 'proposed'",
            )
        if who not in (row["to_principal"], row["from_principal"]):
            raise BusinessError(
                HANDOFF_RECIPIENT_MISMATCH_CODE,
                f"{who!r} is neither the source nor the recipient of handoff"
                f" {handoff_id!r}",
            )
        conn.execute(
            "UPDATE bos_handoff SET status='rejected', updated_at=?"
            " WHERE tenant_id=? AND handoff_id=?",
            (stamp, tenant_id, handoff_id),
        )
        eventlog.append_event(
            store,
            tenant_id,
            f"handoff:{handoff_id}",
            "handoff.rejected",
            {
                "task_id": row["task_id"],
                "session_id": row["session_id"],
                "by": who,
                "reason": str(reason or "")[:2000],
            },
            actor=actor or who,
            idempotency_key=f"handoff.rejected:{handoff_id}",
            business_id=row["business_id"],
            conn=conn,
            now=moment,
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor or who,
            "handoff.reject", "handoff", handoff_id, "ok",
            {"task_id": row["task_id"], "by": who},
        )
    return get_handoff(store, tenant_id, handoff_id)


# -- recovery --------------------------------------------------------------
def recover_sessions(
    store: "BusinessStore",
    tenant_id: str,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Report restart state without claiming work.

    An active session whose workspace lease is gone is reported with
    ``lease_lost=True``; it is never paused, resumed into work, or allowed to
    claim a task. Proposed handoffs stay proposed.
    """
    moment = time.time() if now is None else float(now)
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(
            "SELECT * FROM bos_session WHERE tenant_id=? ORDER BY created_at,"
            " session_id",
            (tenant_id,),
        ).fetchall()
        report: list[dict[str, Any]] = []
        for row in rows:
            session = Session.from_row(row)
            active_leases: list[str] = []
            if session.default_workspace_id:
                leases = conn.execute(
                    "SELECT * FROM bos_lease WHERE tenant_id=? AND released_at IS NULL"
                    " AND resource_type='workspace' AND resource_id=?",
                    (tenant_id, session.default_workspace_id),
                ).fetchall()
                for lease in leases:
                    if float(lease["expires_at"]) > moment:
                        active_leases.append(lease["lease_id"])
            pending = conn.execute(
                "SELECT handoff_id FROM bos_handoff WHERE tenant_id=?"
                " AND session_id=? AND status='proposed' ORDER BY created_at",
                (tenant_id, session.session_id),
            ).fetchall()
            report.append(
                {
                    "session_id": session.session_id,
                    "status": session.status,
                    "agent_id": session.agent_id,
                    "default_workspace_id": session.default_workspace_id,
                    "last_checkpoint_id": session.last_checkpoint_id,
                    "lease_held": bool(active_leases),
                    "lease_lost": (
                        session.default_workspace_id is not None
                        and not active_leases
                    ),
                    "active_lease_ids": active_leases,
                    "pending_handoffs": [item["handoff_id"] for item in pending],
                    # Recovery is descriptive. A lost lease means "do not claim
                    # work", never "silently resume the last message".
                    "may_claim_work": bool(active_leases),
                }
            )
    return report


__all__ = [
    "HANDOFF_STATUSES",
    "Handoff",
    "MEMORY_SCOPES",
    "PARTICIPANT_TYPES",
    "SESSION_STATUSES",
    "SESSION_TRANSITIONS",
    "SESSION_VISIBILITIES",
    "Session",
    "accept_handoff",
    "bind_workspace",
    "create_session",
    "get_handoff",
    "get_session",
    "last_checkpoint",
    "list_handoffs",
    "list_sessions",
    "propose_handoff",
    "record_checkpoint",
    "recover_sessions",
    "reject_handoff",
    "set_session_status",
]
