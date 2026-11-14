"""Durable workforce lanes for every Business OS agent (P4-01).

WHY THIS EXISTS
---------------
An agent row is not a workplace. P4-01 makes the durable lane explicit:

* one session per agent (the owner<->agent lane);
* one task and one task-pinned workspace bound to that session;
* one scoped memory policy (scope *names* only; this module never writes a
  memory record and there is no memory table);
* explicit role and access grants through :mod:`identity` — deny by default,
  no wildcard anywhere.

Everything here is built from the authorities that already exist
(:mod:`agents`, :mod:`sessions`, :mod:`runs`, :mod:`identity`); this module
adds no table, no second store and no execution path. A desk or room is
presentation and is never consulted for authorization.

Idempotency
-----------
Lane record ids are derived deterministically from
``(tenant_id, business_id, agent_id)``. Re-running :func:`provision_workforce`
returns the existing lane instead of minting a duplicate, and repairs a
half-written lane (a session without its task/workspace, or an unbound
workspace) without moving or claiming work.

Non-working states are explicit
-------------------------------
:func:`workforce_status` reports one state per agent. ``live``/``idle`` are the
only working states. ``paused``, ``revoked``, ``archived``, ``disconnected``
and ``unknown`` never count as working and never occupy a working desk. An
agent record imported from a seed/projection is ``projection``, never ``live``.

Restart reconciliation
----------------------
:func:`reconcile_workforce` is read-only. An expired lease is reported as
``expired`` (and the lane state as ``unknown``); no session or workspace is
claimed, released, resumed or silently re-leased. A paused/revoked lane stays
recorded exactly where it is, simply not working.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import agents as AGENTS
from . import identity as IDENTITY
from . import runs as RUNS
from . import sessions as SESSIONS
from .errors import AGENT_NOT_FOUND_CODE, BusinessError
from .store import BusinessStore

SCHEMA = "qubit-office-workforce/1"

PROVISION_SCHEMA = "qubit-office-workforce-provision/1"
STATUS_SCHEMA = "qubit-office-workforce-status/1"
RECONCILE_SCHEMA = "qubit-office-workforce-reconcile/1"

#: The only states that occupy a working desk.
WORKING_STATES = frozenset({"live", "idle"})
#: Explicit non-working states.
NON_WORKING_STATES = frozenset(
    {"paused", "revoked", "archived", "disconnected", "unknown", "projection"}
)
ALL_STATES = tuple(sorted(WORKING_STATES | NON_WORKING_STATES))

DEFAULT_MEMORY_SCOPES = ("agent", "session", "task")
_PROJECTION_SOURCES = frozenset(
    {"office_plan_building", "seed", "projection", "seed_projection"}
)
_TERMINAL_TASK_STATUSES = frozenset({"completed", "rejected", "cancelled"})
_AGENT_PRINCIPAL_KINDS = ("agent",)


def _stable_id(prefix: str, tenant_id: str, business_id: str, agent_id: str,
               tag: str = "") -> str:
    digest = hashlib.sha256(
        "\x1f".join((tenant_id, business_id, agent_id, tag)).encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}_wf_{digest}"


def _iso(moment: float | None) -> str:
    current = time.time() if moment is None else float(moment)
    return datetime.fromtimestamp(current, tz=timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _is_projection(agent: AGENTS.Agent) -> bool:
    source = str((agent.metadata or {}).get("source") or "").lower()
    return source in _PROJECTION_SOURCES


def _agent_ids(agents: Iterable[Any] | None) -> list[str] | None:
    if agents is None:
        return None
    if isinstance(agents, (str, bytes, Mapping)):
        raise BusinessError(
            "WORKFORCE_BAD_INPUT", "agents must be an iterable of agent ids"
        )
    out: list[str] = []
    for item in agents:
        if isinstance(item, str):
            agent_id = item.strip()
        else:
            agent_id = str(getattr(item, "agent_id", "") or "").strip()
        if not agent_id:
            raise BusinessError(
                "WORKFORCE_BAD_INPUT", "every agents entry needs an agent_id"
            )
        if agent_id not in out:
            out.append(agent_id)
    return out


def _owner_principal(store: BusinessStore, tenant_id: str) -> IDENTITY.Principal:
    for principal in IDENTITY.list_principals(store, tenant_id):
        if principal.kind == "owner" and principal.status == "active":
            return principal
    raise BusinessError(
        "WORKFORCE_NO_OWNER",
        f"tenant {tenant_id!r} has no active owner principal; a durable"
        " workforce lane needs a real human owner, not an invented one",
    )


def _agent_principal(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    agent: AGENTS.Agent,
    actor: str,
) -> tuple[IDENTITY.Principal, bool]:
    if agent.security_principal_id:
        try:
            principal = IDENTITY.get_principal(
                store, tenant_id, agent.security_principal_id
            )
        except BusinessError as exc:
            if exc.code != IDENTITY.PRINCIPAL_NOT_FOUND_CODE:
                raise
            raise BusinessError(
                "WORKFORCE_PRINCIPAL_MISSING",
                f"agent {agent.agent_id!r} names security principal"
                f" {agent.security_principal_id!r}, which does not exist",
            ) from exc
        return principal, False
    for principal in IDENTITY.list_principals(store, tenant_id):
        if (
            principal.kind == "agent"
            and principal.external_ref == agent.agent_id
        ):
            return principal, False
    principal = IDENTITY.create_principal(
        store,
        tenant_id,
        kind="agent",
        external_ref=agent.agent_id,
        principal_id=_stable_id(
            "prn", tenant_id, business_id, agent.agent_id, "principal"
        ),
        actor=actor,
    )
    return principal, True


def _find_lane(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    agent: AGENTS.Agent,
) -> SESSIONS.Session | None:
    candidates = SESSIONS.list_sessions(
        store, tenant_id, business_id=business_id, agent_id=agent.agent_id
    )
    if not candidates:
        return None
    expected = _stable_id("ses", tenant_id, business_id, agent.agent_id)
    for session in candidates:
        if session.session_id == expected:
            return session
    for session in candidates:
        marker = (session.metadata or {}).get("workforce") or {}
        if (
            isinstance(marker, Mapping)
            and marker.get("schema") == SCHEMA
            and marker.get("agent_id") == agent.agent_id
        ):
            return session
    return candidates[0]


def _memory_scopes(agent: AGENTS.Agent) -> tuple[str, ...]:
    marker = (agent.metadata or {}).get("workforce_memory_scopes")
    if marker is None:
        return DEFAULT_MEMORY_SCOPES
    if isinstance(marker, (str, bytes, Mapping)):
        raise BusinessError(
            "WORKFORCE_BAD_INPUT",
            "metadata.workforce_memory_scopes must be a list of scope names",
        )
    cleaned: list[str] = []
    for item in marker:
        scope = str(item or "").strip()
        if scope not in SESSIONS.MEMORY_SCOPES:
            raise BusinessError(
                "WORKFORCE_BAD_INPUT",
                f"unknown memory scope {scope!r}; expected one of"
                f" {', '.join(SESSIONS.MEMORY_SCOPES)}",
            )
        if scope not in cleaned:
            cleaned.append(scope)
    return tuple(cleaned)


def _role_permissions(agent: AGENTS.Agent) -> list[dict[str, str]]:
    permissions: list[dict[str, str]] = []
    for category, scopes in agent.permissions.to_dict().items():
        for scope in scopes:
            permissions.append(
                {
                    "capability": category,
                    "resource": scope,
                    "action": "use",
                }
            )
    return permissions


def _ensure_role(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    agent: AGENTS.Agent,
    actor: str,
) -> tuple[str, bool]:
    role_id = _stable_id("role", tenant_id, business_id, agent.agent_id)
    with store.read() as conn:
        row = conn.execute(
            "SELECT role_id FROM bos_role WHERE tenant_id=? AND role_id=?",
            (tenant_id, role_id),
        ).fetchone()
    if row is not None:
        return role_id, False
    role = IDENTITY.create_role(
        store,
        tenant_id,
        f"workforce:{agent.agent_id}",
        _role_permissions(agent),
        role_id=role_id,
        actor=actor,
    )
    return role.role_id, True


def _grant_exists(store: BusinessStore, tenant_id: str, grant_id: str) -> bool:
    with store.read() as conn:
        row = conn.execute(
            "SELECT 1 FROM bos_grant WHERE tenant_id=? AND grant_id=?",
            (tenant_id, grant_id),
        ).fetchone()
    return row is not None


def _ensure_grant(
    store: BusinessStore,
    tenant_id: str,
    *,
    subject_id: str,
    capability: str,
    resource: str,
    action: str,
    grant_id: str,
    role_id: str | None,
    actor: str,
) -> bool:
    if _grant_exists(store, tenant_id, grant_id):
        return False
    IDENTITY.grant(
        store,
        tenant_id,
        subject_id=subject_id,
        capability=capability,
        resource=resource,
        action=action,
        role_id=role_id,
        grant_id=grant_id,
        actor=actor,
    )
    return True


def _task_exists(store: BusinessStore, tenant_id: str, task_id: str) -> bool:
    try:
        RUNS.get_task(store, tenant_id, task_id)
    except BusinessError as exc:
        if exc.code == RUNS.TASK_NOT_FOUND_CODE:
            return False
        raise
    return True


def _workspace(
    store: BusinessStore, tenant_id: str, workspace_id: str
) -> RUNS.Workspace | None:
    try:
        return RUNS.get_workspace(store, tenant_id, workspace_id)
    except BusinessError as exc:
        if exc.code == RUNS.WORKSPACE_NOT_FOUND_CODE:
            return None
        raise


def provision_workforce(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    *,
    agents: Iterable[Any] | None = None,
    actor: str,
) -> dict[str, Any]:
    """Idempotently give each business agent one durable workforce lane.

    ``agents`` may be agent ids or any object carrying ``agent_id``; ``None``
    means every agent in the business. Archived agents are skipped with a
    typed note — a lane is never fabricated for them.
    """
    if not str(actor or "").strip():
        raise BusinessError("WORKFORCE_BAD_INPUT", "actor is required")
    wanted = _agent_ids(agents)
    service = AGENTS.AgentControlService(store)
    if wanted is None:
        roster = service.list(tenant_id, business_id=business_id)
    else:
        roster = []
        for agent_id in wanted:
            try:
                agent = service.get(tenant_id, agent_id)
            except BusinessError as exc:
                if exc.code == AGENT_NOT_FOUND_CODE:
                    raise BusinessError(
                        "WORKFORCE_AGENT_MISSING",
                        f"agent {agent_id!r} does not exist in tenant"
                        f" {tenant_id!r}",
                    ) from exc
                raise
            if agent.business_id != business_id:
                raise BusinessError(
                    "WORKFORCE_AGENT_MISSING",
                    f"agent {agent_id!r} does not belong to business"
                    f" {business_id!r}",
                )
            roster.append(agent)

    with store.read() as conn:
        store._require_business(conn, tenant_id, business_id)

    owner = _owner_principal(store, tenant_id)
    lanes: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    counters = {
        "created_sessions": 0,
        "created_tasks": 0,
        "created_workspaces": 0,
        "created_roles": 0,
        "created_grants": 0,
    }

    for agent in roster:
        if agent.status == "archived":
            skipped.append(
                {"agent_id": agent.agent_id, "reason": "archived"}
            )
            continue
        principal, principal_created = _agent_principal(
            store, tenant_id, business_id, agent, actor
        )
        del principal_created  # creation is audited by identity itself
        session = _find_lane(store, tenant_id, business_id, agent)
        created_session = False
        if session is None:
            session = SESSIONS.create_session(
                store,
                tenant_id,
                business_id,
                agent.agent_id,
                owner_principal=owner.principal_id,
                visibility="owner",
                default_memory_scopes=_memory_scopes(agent),
                policy_revision=1,
                metadata={
                    "workforce": {
                        "schema": SCHEMA,
                        "business_id": business_id,
                        "agent_id": agent.agent_id,
                        "memory_scopes": list(_memory_scopes(agent)),
                        "role_id": agent.role_id,
                    }
                },
                actor=actor,
                session_id=_stable_id(
                    "ses", tenant_id, business_id, agent.agent_id
                ),
            )
            created_session = True
            counters["created_sessions"] += 1

        workspace = None
        if session.default_workspace_id:
            candidate = _workspace(store, tenant_id, session.default_workspace_id)
            if candidate is not None and candidate.session_id == session.session_id:
                workspace = candidate
        task_id = workspace.task_id if workspace is not None else _stable_id(
            "task", tenant_id, business_id, agent.agent_id
        )
        if workspace is None:
            if not _task_exists(store, tenant_id, task_id):
                RUNS.create_task(
                    store,
                    tenant_id,
                    business_id,
                    f"{agent.display_name} — workforce lane",
                    objective=(
                        f"Durable work lane for {agent.display_name}"
                        f" ({agent.role_id}) in {agent.department}"
                    ),
                    owner_principal=owner.principal_id,
                    assignee_principal=principal.principal_id,
                    actor=actor,
                    task_id=task_id,
                )
                counters["created_tasks"] += 1
            workspace_id = _stable_id(
                "wsp", tenant_id, business_id, agent.agent_id
            )
            workspace = _workspace(store, tenant_id, workspace_id)
            if workspace is None:
                workspace = RUNS.create_workspace(
                    store,
                    tenant_id,
                    task_id=task_id,
                    session_id=session.session_id,
                    storage_class="ephemeral",
                    access_policy={
                        "schema": "qubit-office-workforce-workspace/1",
                        "agent_id": agent.agent_id,
                        "session_id": session.session_id,
                    },
                    actor=actor,
                    workspace_id=workspace_id,
                )
                counters["created_workspaces"] += 1
            if session.default_workspace_id != workspace.workspace_id:
                session = SESSIONS.bind_workspace(
                    store,
                    tenant_id,
                    session.session_id,
                    workspace.workspace_id,
                    actor=actor,
                )

        role_id, role_created = _ensure_role(
            store, tenant_id, business_id, agent, actor
        )
        if role_created:
            counters["created_roles"] += 1
        grants = (
            ("workforce", business_id, "participate", "role", role_id),
            ("session", session.session_id, "use", "session", None),
            ("workspace", workspace.workspace_id, "use", "workspace", None),
            ("task", workspace.task_id, "use", "task", None),
        )
        for capability, resource, action, tag, linked_role in grants:
            grant_id = _stable_id(
                "grnt", tenant_id, business_id, agent.agent_id, tag
            )
            if _ensure_grant(
                store,
                tenant_id,
                subject_id=principal.principal_id,
                capability=capability,
                resource=resource,
                action=action,
                grant_id=grant_id,
                role_id=linked_role,
                actor=actor,
            ):
                counters["created_grants"] += 1

        lanes.append(
            {
                "agent_id": agent.agent_id,
                "principal_id": principal.principal_id,
                "session_id": session.session_id,
                "workspace_id": workspace.workspace_id,
                "task_id": workspace.task_id,
                "role_id": role_id,
                "created_session": created_session,
            }
        )

    return {
        "schema": PROVISION_SCHEMA,
        "tenant_id": tenant_id,
        "business_id": business_id,
        "owner_principal": owner.principal_id,
        "agents": lanes,
        "skipped": skipped,
        **counters,
    }


def _lane_leases(
    store: BusinessStore,
    tenant_id: str,
    *,
    workspace_id: str | None,
    task_id: str | None,
    now: float,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    active = 0
    expired = 0
    for resource_type, resource_id in (
        ("workspace", workspace_id),
        ("task", task_id),
    ):
        if not resource_id:
            continue
        for lease in RUNS.list_leases(
            store,
            tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            active_only=False,
        ):
            if lease.released_at is not None:
                state = "released"
            elif lease.expires_at <= now:
                state = "expired"
                expired += 1
            else:
                state = "active"
                active += 1
            rows.append(
                {
                    "lease_id": lease.lease_id,
                    "resource_type": lease.resource_type,
                    "resource_id": lease.resource_id,
                    "holder": lease.holder,
                    "state": state,
                    "expires_at": lease.expires_at,
                }
            )
    if expired:
        state = "expired"
    elif active:
        state = "active"
    elif rows:
        state = "released"
    else:
        state = "none"
    return {
        "state": state,
        "active_count": active,
        "expired_count": expired,
        "leases": rows,
    }


def _identity_view(
    store: BusinessStore, tenant_id: str, principal_id: str | None
) -> dict[str, Any]:
    if not principal_id:
        return {
            "principal_id": None,
            "status": "unknown",
            "role_id": None,
            "grants": [],
            "wildcard": False,
            "reason": "the agent record names no security principal",
        }
    try:
        principal = IDENTITY.get_principal(store, tenant_id, principal_id)
    except BusinessError as exc:
        if exc.code != IDENTITY.PRINCIPAL_NOT_FOUND_CODE:
            raise
        return {
            "principal_id": principal_id,
            "status": "missing",
            "role_id": None,
            "grants": [],
            "wildcard": False,
            "reason": "the named security principal does not exist",
        }
    grants: list[dict[str, Any]] = []
    wildcard = False
    for grant in IDENTITY.list_grants(
        store, tenant_id, subject_id=principal_id
    ):
        for value in (grant.capability, grant.resource, grant.action):
            if "*" in str(value) or "?" in str(value):
                wildcard = True
        grants.append(
            {
                "grant_id": grant.grant_id,
                "capability": grant.capability,
                "resource": grant.resource,
                "action": grant.action,
                "effect": grant.effect,
                "role_id": grant.role_id,
                "revoked": grant.revoked_at is not None,
                "expired": (
                    grant.expires_at is not None
                    and grant.expires_at <= time.time()
                ),
            }
        )
    role_ids = sorted({item["role_id"] for item in grants if item["role_id"]})
    return {
        "principal_id": principal.principal_id,
        "status": principal.status,
        "role_id": role_ids[0] if len(role_ids) == 1 else None,
        "role_ids": role_ids,
        "grants": grants,
        "wildcard": wildcard,
        "reason": None,
    }


def _policy_revision(
    store: BusinessStore, tenant_id: str, session: SESSIONS.Session | None
) -> dict[str, Any]:
    tenant_policy = None
    with store.read() as conn:
        row = conn.execute(
            "SELECT version, effective_at FROM bos_policy_revision"
            " WHERE tenant_id=? ORDER BY effective_at DESC, revision_id DESC"
            " LIMIT 1",
            (tenant_id,),
        ).fetchone()
        if row is not None:
            tenant_policy = {
                "version": row["version"],
                "effective_at": row["effective_at"],
            }
    return {
        "session_policy_revision": session.policy_revision if session else None,
        "tenant_policy": tenant_policy,
    }


def _active_turns(store: BusinessStore, tenant_id: str, agent_id: str) -> int:
    with store.read() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM bos_agent_turn WHERE tenant_id=?"
                " AND agent_id=? AND status='active'",
                (tenant_id, agent_id),
            ).fetchone()[0]
        )


def _state(
    agent: AGENTS.Agent,
    session: SESSIONS.Session | None,
    workspace: RUNS.Workspace | None,
    task: RUNS.WorkTask | None,
    active_turns: int,
    lease: Mapping[str, Any],
) -> tuple[str, bool, str | None]:
    if agent.status == "archived":
        return "archived", False, "the agent record is archived"
    if agent.status == "revoked":
        return "revoked", False, "the agent record is revoked"
    if agent.status == "paused":
        return "paused", False, "the agent record is paused"
    if session is not None and session.status == "archived":
        return "archived", False, "the durable lane session is archived"
    if session is not None and session.status == "paused":
        return "paused", False, "the durable lane session is paused"
    if _is_projection(agent):
        return (
            "projection",
            False,
            "the agent record comes from a seed/projection import",
        )
    if session is None or workspace is None:
        return (
            "disconnected",
            False,
            "the agent has no durable session/workspace lane",
        )
    if workspace.session_id != session.session_id:
        return (
            "disconnected",
            False,
            "the lane workspace belongs to a different session",
        )
    if (
        lease.get("state") == "expired"
        and (task is None or task.status not in _TERMINAL_TASK_STATUSES)
    ):
        return (
            "unknown",
            False,
            "the workspace lease expired; restart reconciliation is required",
        )
    if active_turns > 0:
        return "live", True, None
    return "idle", True, None


def _agent_view(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    agent: AGENTS.Agent,
    now: float,
) -> dict[str, Any]:
    session = _find_lane(store, tenant_id, business_id, agent)
    workspace: RUNS.Workspace | None = None
    task: RUNS.WorkTask | None = None
    if session is not None and session.default_workspace_id:
        candidate = _workspace(store, tenant_id, session.default_workspace_id)
        if candidate is not None and candidate.session_id == session.session_id:
            workspace = candidate
    if workspace is not None:
        try:
            task = RUNS.get_task(store, tenant_id, workspace.task_id)
        except BusinessError as exc:
            if exc.code != RUNS.TASK_NOT_FOUND_CODE:
                raise
    active_turns = _active_turns(store, tenant_id, agent.agent_id)
    lease = _lane_leases(
        store,
        tenant_id,
        workspace_id=workspace.workspace_id if workspace else None,
        task_id=workspace.task_id if workspace else None,
        now=now,
    )
    state, working, reason = _state(
        agent, session, workspace, task, active_turns, lease
    )
    principal_id = agent.security_principal_id
    if not principal_id:
        for principal in IDENTITY.list_principals(store, tenant_id):
            if (
                principal.kind == "agent"
                and principal.external_ref == agent.agent_id
            ):
                principal_id = principal.principal_id
                break
    identity_view = _identity_view(store, tenant_id, principal_id)
    return {
        "agent_id": agent.agent_id,
        "display_name": agent.display_name,
        "department": agent.department,
        "role_id": agent.role_id,
        "agent_status": agent.status,
        "state": state,
        "working": working,
        "reason": reason,
        "session": (
            {
                "session_id": session.session_id,
                "status": session.status,
                "visibility": session.visibility,
                "agent_id": session.agent_id,
                "policy_revision": session.policy_revision,
                "default_workspace_id": session.default_workspace_id,
            }
            if session is not None
            else None
        ),
        "workspace": (
            {
                "workspace_id": workspace.workspace_id,
                "task_id": workspace.task_id,
                "session_id": workspace.session_id,
                "storage_class": workspace.storage_class,
                "current_revision": workspace.current_revision,
            }
            if workspace is not None
            else None
        ),
        "task": (
            {
                "task_id": task.task_id,
                "status": task.status,
                "assignee_principal": task.assignee_principal,
                "owner_principal": task.owner_principal,
            }
            if task is not None
            else None
        ),
        "lease": lease,
        "memory": {
            "scopes": (
                list(session.default_memory_scopes) if session else []
            ),
            "source": "bos_session.default_memory_scopes_json",
            "table": None,
            "note": "scope names only; this module stores no memory record",
        },
        "identity": identity_view,
        "policy": _policy_revision(store, tenant_id, session),
        "active_turns": active_turns,
        "updated_at": agent.updated_at,
    }


def workforce_status(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Return the per-agent lane, role/access, memory and working state."""
    moment = time.time() if now is None else float(now)
    with store.read() as conn:
        store._require_business(conn, tenant_id, business_id)
    roster = AGENTS.AgentControlService(store).list(
        tenant_id, business_id=business_id
    )
    agents = [
        _agent_view(store, tenant_id, business_id, agent, moment)
        for agent in roster
    ]
    counts = {
        "agents": len(agents),
        "working": sum(1 for item in agents if item["working"]),
        "non_working": sum(1 for item in agents if not item["working"]),
        "live": sum(1 for item in agents if item["state"] == "live"),
        "idle": sum(1 for item in agents if item["state"] == "idle"),
        "projection": sum(
            1 for item in agents if item["state"] == "projection"
        ),
        "paused": sum(1 for item in agents if item["state"] == "paused"),
        "revoked": sum(1 for item in agents if item["state"] == "revoked"),
        "archived": sum(1 for item in agents if item["state"] == "archived"),
        "disconnected": sum(
            1 for item in agents if item["state"] == "disconnected"
        ),
        "unknown": sum(1 for item in agents if item["state"] == "unknown"),
        "lanes": sum(1 for item in agents if item["session"] is not None),
        "expired_leases": sum(
            1 for item in agents if item["lease"]["expired_count"] > 0
        ),
    }
    return {
        "schema": STATUS_SCHEMA,
        "tenant_id": tenant_id,
        "business_id": business_id,
        "generated_at": _iso(moment),
        "agents": agents,
        "counts": counts,
        "working_states": sorted(WORKING_STATES),
        "non_working_states": sorted(NON_WORKING_STATES),
    }


def reconcile_workforce(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    *,
    now: float | None = None,
    actor: str = "reconcile",
) -> dict[str, Any]:
    """Report restart state without claiming, releasing or re-claiming work.

    An expired lease is surfaced as ``expired`` and its lane as ``unknown``.
    Sessions/workspaces are left exactly as recorded; ``claimed`` is always
    false because reconciliation is not an execution path.
    """
    moment = time.time() if now is None else float(now)
    del actor  # reconciliation is read-only; kept for a stable call shape
    status = workforce_status(
        store, tenant_id, business_id, now=moment
    )
    recovery = SESSIONS.recover_sessions(store, tenant_id, now=moment)
    recovery_by_session = {
        row["session_id"]: row for row in recovery
    }
    lanes: list[dict[str, Any]] = []
    for item in status["agents"]:
        session = item["session"] or {}
        session_id = session.get("session_id")
        recovered = recovery_by_session.get(session_id, {})
        lanes.append(
            {
                "agent_id": item["agent_id"],
                "session_id": session_id,
                "workspace_id": (
                    item["workspace"] or {}
                ).get("workspace_id"),
                "task_id": (item["task"] or {}).get("task_id"),
                "agent_status": item["agent_status"],
                "session_status": session.get("status"),
                "state": item["state"],
                "working": item["working"],
                "reason": item["reason"],
                "lease_state": item["lease"]["state"],
                "expired_leases": item["lease"]["expired_count"],
                "may_claim_work": bool(recovered.get("may_claim_work", False)),
                "claimed": False,
            }
        )
    counts = {
        "agents": len(lanes),
        "working": sum(1 for item in lanes if item["working"]),
        "non_working": sum(1 for item in lanes if not item["working"]),
        "expired_leases": sum(
            1 for item in lanes if item["lease_state"] == "expired"
        ),
        "unknown": sum(1 for item in lanes if item["state"] == "unknown"),
        "claimed": 0,
    }
    return {
        "schema": RECONCILE_SCHEMA,
        "tenant_id": tenant_id,
        "business_id": business_id,
        "generated_at": _iso(moment),
        "mutated": False,
        "lanes": lanes,
        "counts": counts,
    }


__all__ = [
    "ALL_STATES",
    "DEFAULT_MEMORY_SCOPES",
    "NON_WORKING_STATES",
    "RECONCILE_SCHEMA",
    "SCHEMA",
    "STATUS_SCHEMA",
    "WORKING_STATES",
    "provision_workforce",
    "reconcile_workforce",
    "workforce_status",
]
