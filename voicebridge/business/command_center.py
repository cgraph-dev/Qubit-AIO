"""Read-only command-center projection over the real Business OS state.

The Office command center is a *projection*, never a second store. This module
composes the state that already exists:

* the shipped floor/room/desk tower templates (the same registry the world
  scene serves), with fixed 16x12 cells and six desk slots per cell;
* the Business OS agent roster (``bos_agent``);
* tasks (``bos_task``), durable sessions (``bos_session``) and approvals
  (``bos_approval``).

It returns searchable entries plus honest roll-ups (roster summary, inbox
counts, floor/room/agent links). Every count is derived from a row; a missing
source is a typed ``unavailable`` source state with a reason, never a fabricated
zero. The function is tenant-gated and read-only: it opens the store for reads,
never writes, and never calls a network or model provider.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote

from ..office.world import model as WORLD
from . import agents as AGENTS
from . import approvals as APPROVALS
from . import sessions as SESSIONS
from .errors import BusinessError
from .roles import RoleRegistry
from .store import BusinessStore
from .util import now_iso

SCHEMA = "qubit-office-command-center/1"

KINDS = ("floor", "room", "desk", "agent", "task", "approval")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_KIND_RANK = {kind: index for index, kind in enumerate(KINDS)}

#: The UI deep-link contract (``QUBIT-OFFICE-UI-CONTRACT.md`` §4.5).
ROUTE_TEMPLATE = "/qubit-office?floor={floor_id}&room={room_id}&agent={agent_id}"


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _bounded_limit(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_LIMIT
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise BusinessError("BUSINESS_BAD_INPUT", "limit must be an integer") from exc
    if limit < 1 or limit > MAX_LIMIT:
        raise BusinessError(
            "BUSINESS_BAD_INPUT",
            f"limit must be between 1 and {MAX_LIMIT}",
        )
    return limit


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _clean(value).casefold()


def _kind(value: Any) -> str | None:
    clean = _clean(value).lower()
    if not clean or clean == "all":
        return None
    if clean not in KINDS:
        raise BusinessError(
            "BUSINESS_BAD_INPUT",
            f"unknown kind {clean!r}; expected one of {', '.join(KINDS)}",
        )
    return clean


def _source(
    fn: Callable[[], list[Any]], label: str
) -> tuple[list[Any], dict[str, Any]]:
    """Run one source read; an error is a typed unavailable state, not []."""
    try:
        rows = fn()
    except Exception as exc:  # noqa: BLE001 - each source degrades alone
        code = exc.code if isinstance(exc, BusinessError) else type(exc).__name__
        detail = exc.detail if isinstance(exc, BusinessError) else str(exc)
        return [], {
            "state": "unavailable",
            "count": 0,
            "code": str(code)[:120],
            "reason": str(detail)[:300],
        }
    return rows, {
        "state": "ok" if rows else "empty",
        "count": len(rows),
        "code": None,
        "reason": None,
    }


def _path(
    floor_id: str | None, room_id: str | None = None,
    agent_id: str | None = None,
) -> str | None:
    """Build the contract deep link, or ``None`` when no floor is known."""
    fid = _clean(floor_id)
    if not fid:
        return None
    params: list[str] = [f"floor={quote(fid, safe='')}"]
    rid = _clean(room_id)
    aid = _clean(agent_id)
    if rid:
        params.append(f"room={quote(rid, safe='')}")
    if aid:
        params.append(f"agent={quote(aid, safe='')}")
    return f"/qubit-office?{'&'.join(params)}"


def _link(
    floor_id: str | None = None,
    room_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    path = _path(floor_id, room_id, agent_id)
    return {
        "floor_id": _clean(floor_id) or None,
        "room_id": _clean(room_id) or None,
        "agent_id": _clean(agent_id) or None,
        "path": path,
        "reason": None if path else "no floor is known for this record",
    }


def _entry(
    *,
    entry_id: str,
    kind: str,
    title: str,
    subtitle: str,
    status: str,
    source: str,
    floor_id: str | None = None,
    room_id: str | None = None,
    desk_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    approval_id: str | None = None,
    attention: bool = False,
    link_agent: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "entry_id": entry_id,
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "status": status,
        "source": source,
        "attention": bool(attention),
        "floor_id": _clean(floor_id) or None,
        "room_id": _clean(room_id) or None,
        "desk_id": _clean(desk_id) or None,
        "agent_id": _clean(agent_id) or None,
        "task_id": _clean(task_id) or None,
        "approval_id": _clean(approval_id) or None,
        "link": _link(floor_id, room_id, link_agent or agent_id),
    }
    if extra:
        payload.update(extra)
    return payload


def _search_text(entry: Mapping[str, Any]) -> str:
    parts = [
        entry.get("entry_id"),
        entry.get("kind"),
        entry.get("title"),
        entry.get("subtitle"),
        entry.get("status"),
        entry.get("floor_id"),
        entry.get("room_id"),
        entry.get("desk_id"),
        entry.get("agent_id"),
        entry.get("task_id"),
        entry.get("approval_id"),
    ]
    return " ".join(_norm(item) for item in parts if item)


def _matches(
    entry: Mapping[str, Any],
    *,
    needle: str,
    kind: str | None,
    status: str | None,
    floor_id: str,
    room_id: str,
    agent_id: str,
    task_id: str,
    approval_id: str,
) -> bool:
    if kind and entry.get("kind") != kind:
        return False
    if status and _norm(entry.get("status")) != status:
        return False
    if floor_id and _norm(entry.get("floor_id")) != floor_id:
        return False
    if room_id and _norm(entry.get("room_id")) != room_id:
        return False
    if agent_id and _norm(entry.get("agent_id")) != agent_id:
        return False
    if task_id and _norm(entry.get("task_id")) != task_id:
        return False
    if approval_id and _norm(entry.get("approval_id")) != approval_id:
        return False
    if needle and needle not in _search_text(entry):
        return False
    return True


def _count_by(items: Iterable[Any], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = _clean(getattr(item, attr, "")) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _world_entries(registry: RoleRegistry) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Floors, rooms and desks from the real tower templates."""
    entries: list[dict[str, Any]] = []
    counts = {"floors": 0, "rooms": 0, "desks": 0}
    for floor in registry.floors:
        counts["floors"] += 1
        floor_status = "active" if floor.active_by_default else "dormant"
        entries.append(_entry(
            entry_id=f"floor:{floor.floor_id}",
            kind="floor",
            title=floor.name,
            subtitle=f"{floor.wing.title()} Wing · floor {floor.index:03d}",
            status=floor_status,
            source="world",
            floor_id=floor.floor_id,
            extra={"room_count": len(floor.rooms)},
        ))
        counts["rooms"] += len(floor.rooms)
        for room in floor.rooms:
            entries.append(_entry(
                entry_id=f"room:{floor.floor_id}:{room.room_id}",
                kind="room",
                title=room.name,
                subtitle=f"{floor.name} · {room.kind} · {room.desks} desks",
                status=floor_status,
                source="world",
                floor_id=floor.floor_id,
                room_id=room.room_id,
                extra={"kind_detail": room.kind, "desk_count": room.desks,
                       "role_ids": list(room.role_ids)},
            ))
            cells = WORLD.plan_cell_cluster(
                room.room_id, room.desks, kind=room.kind)
            for cell in cells:
                for desk in cell.desks:
                    counts["desks"] += 1
                    entries.append(_entry(
                        entry_id=f"desk:{desk.desk_id}",
                        kind="desk",
                        title=f"Desk {desk.seat.seat_id}",
                        subtitle=f"{floor.name} · {room.name}",
                        status="vacant",
                        source="world",
                        floor_id=floor.floor_id,
                        room_id=room.room_id,
                        desk_id=desk.desk_id,
                        extra={"cell_id": cell.cell_id,
                               "seat_id": desk.seat.seat_id,
                               "tile": desk.tile.to_dict()},
                    ))
    return entries, counts


def _agent_floor(
    agent: Any, registry: RoleRegistry
) -> tuple[str | None, str | None]:
    """Resolve a floor/room only from an explicit registry match."""
    room = _clean(getattr(agent, "room", ""))
    if room:
        for floor in registry.floors:
            for template in floor.rooms:
                if template.room_id == room:
                    return floor.floor_id, room
    department = _clean(getattr(agent, "department", ""))
    if department:
        for floor in registry.floors:
            if department in floor.department_ids:
                return floor.floor_id, None
    return None, None


def _agent_entries(
    agents: list[AGENTS.Agent], registry: RoleRegistry, *, world_available: bool,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for agent in agents:
        floor_id, room_id = (
            _agent_floor(agent, registry) if world_available else (None, None)
        )
        entries.append(_entry(
            entry_id=f"agent:{agent.agent_id}",
            kind="agent",
            title=agent.display_name,
            subtitle=(
                f"{agent.department} · {agent.role_id} · {agent.status}"
            ),
            status=agent.status,
            source="roster",
            floor_id=floor_id,
            room_id=room_id or _clean(getattr(agent, "room", "")) or None,
            desk_id=_clean(getattr(agent, "desk", "")) or None,
            agent_id=agent.agent_id,
            extra={
                "role_id": agent.role_id,
                "department": agent.department,
                "business_id": agent.business_id,
            },
        ))
    return entries


def _task_entries(tasks: list[Any], agent_ids: set[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for task in tasks:
        assigned = _clean(getattr(task, "assigned_to", ""))
        link_agent = assigned if assigned in agent_ids else None
        entries.append(_entry(
            entry_id=f"task:{task.task_id}",
            kind="task",
            title=task.title,
            subtitle=f"{assigned or 'unassigned'} · {task.status}",
            status=task.status,
            source="tasks",
            agent_id=link_agent,
            task_id=task.task_id,
            extra={"assigned_to": assigned or None,
                   "business_id": task.business_id},
        ))
    return entries


def _approval_entries(
    approvals: list[Any], agent_ids: set[str]
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for approval in approvals:
        requested_by = _clean(getattr(approval, "requested_by", ""))
        link_agent = requested_by if requested_by in agent_ids else None
        payload = getattr(approval, "payload", None)
        risk = ""
        if isinstance(payload, Mapping):
            risk = _clean(payload.get("risk"))
        entries.append(_entry(
            entry_id=f"approval:{approval.approval_id}",
            kind="approval",
            title=approval.action,
            subtitle=(
                f"{requested_by or 'unknown requester'} · {approval.status}"
                + (f" · risk {risk}" if risk else "")
            ),
            status=approval.status,
            source="approvals",
            agent_id=link_agent,
            approval_id=approval.approval_id,
            attention=approval.status == "pending",
            extra={
                "requested_by": requested_by or None,
                "risk": risk or None,
                "business_id": approval.business_id,
                "job_id": approval.job_id,
            },
        ))
    return entries


def _roster_summary(agents: list[AGENTS.Agent]) -> dict[str, Any]:
    active = sum(1 for item in agents if item.status == "active")
    paused = sum(1 for item in agents if item.status == "paused")
    archived = sum(1 for item in agents if item.status == "archived")
    placed = sum(
        1 for item in agents
        if _clean(getattr(item, "room", "")) or _clean(getattr(item, "desk", ""))
    )
    return {
        "total": len(agents),
        "active": active,
        "paused": paused,
        "archived": archived,
        "placed": placed,
        "unplaced": len(agents) - placed,
        "by_status": _count_by(agents, "status"),
        "by_department": _count_by(agents, "department"),
    }


def _inbox(approvals: list[Any]) -> dict[str, Any]:
    pending = [item for item in approvals if item.status == "pending"]
    items = [
        {
            "approval_id": item.approval_id,
            "action": item.action,
            "requested_by": item.requested_by,
            "business_id": item.business_id,
            "job_id": item.job_id,
            "status": item.status,
            "created_at": item.created_at,
            "link": _link(None, None, item.requested_by or None),
        }
        for item in pending
    ]
    return {
        "counts": {
            "approvals_pending": len(pending),
            "approvals_total": len(approvals),
        },
        "items": items,
    }


def command_center(
    store: BusinessStore,
    tenant_id: str,
    *,
    query: Any = None,
    kind: Any = None,
    status: Any = None,
    floor_id: Any = None,
    room_id: Any = None,
    agent_id: Any = None,
    task_id: Any = None,
    approval_id: Any = None,
    limit: Any = None,
    registry: RoleRegistry | None = None,
) -> dict[str, Any]:
    """Compose the command center for one tenant.

    Raises the store's tenant refusal for an unknown/cross-tenant id. Every
    other source degrades independently to a typed ``unavailable`` state.
    """
    # Tenant validation first: a cross-tenant id is a typed not-found, not an
    # empty projection.
    store.require_tenant(tenant_id)
    bounded = _bounded_limit(limit)
    needle = _norm(query)
    kind_filter = _kind(kind)
    status_filter = _norm(status)

    catalog = registry or RoleRegistry.default()

    world_rows, world_state = _source(
        lambda: list(catalog.floors), "world")
    roster_service = AGENTS.AgentControlService(store)
    agents, roster_state = _source(
        lambda: roster_service.list(tenant_id), "roster")
    tasks, tasks_state = _source(
        lambda: store.list_tasks(tenant_id), "tasks")
    session_rows, sessions_state = _source(
        lambda: SESSIONS.list_sessions(store, tenant_id), "sessions")
    approval_rows, approvals_state = _source(
        lambda: APPROVALS.list_approvals(store, tenant_id), "approvals")

    world_entries, world_counts = (
        _world_entries(catalog) if world_rows else ([], {"floors": 0, "rooms": 0, "desks": 0})
    )
    # The catalog read above is the source of truth; if it raised, _world_entries
    # is never called and the state already says unavailable.
    if world_state["state"] == "unavailable":
        world_counts = {"floors": 0, "rooms": 0, "desks": 0}

    agent_entries = _agent_entries(
        agents, catalog,
        world_available=world_state["state"] != "unavailable",
    ) if agents else []
    agent_ids = {item.agent_id for item in agents}
    task_entries = _task_entries(tasks, agent_ids)
    approval_entries = _approval_entries(approval_rows, agent_ids)

    all_entries = world_entries + agent_entries + task_entries + approval_entries
    all_entries.sort(key=lambda item: (
        _KIND_RANK.get(item["kind"], 99),
        _norm(item["title"]),
        item["entry_id"],
    ))

    matched = [
        item for item in all_entries
        if _matches(
            item,
            needle=needle,
            kind=kind_filter,
            status=status_filter,
            floor_id=_norm(floor_id),
            room_id=_norm(room_id),
            agent_id=_norm(agent_id),
            task_id=_norm(task_id),
            approval_id=_norm(approval_id),
        )
    ]
    page = matched[:bounded]

    inbox = _inbox(approval_rows)
    roster = _roster_summary(agents)

    floors_link = [
        {
            "floor_id": floor.floor_id,
            "name": floor.name,
            "wing": floor.wing,
            "active": bool(floor.active_by_default),
            "rooms": len(floor.rooms),
            "path": _path(floor.floor_id),
        }
        for floor in catalog.floors
    ] if world_state["state"] != "unavailable" else []

    counts = {
        "floors": world_counts["floors"],
        "rooms": world_counts["rooms"],
        "desks": world_counts["desks"],
        "agents": len(agents),
        "tasks": len(tasks),
        "sessions": len(session_rows),
        "approvals": len(approval_rows),
        "approvals_pending": inbox["counts"]["approvals_pending"],
        "entries": len(all_entries),
        "matched": len(matched),
        "returned": len(page),
    }

    return {
        "schema": SCHEMA,
        "tenant_id": tenant_id,
        "generated_at": now_iso(),
        "state": "empty" if not matched else "ok",
        "query": {
            "q": _clean(query),
            "kind": kind_filter,
            "status": status_filter or None,
            "floor_id": _clean(floor_id) or None,
            "room_id": _clean(room_id) or None,
            "agent_id": _clean(agent_id) or None,
            "task_id": _clean(task_id) or None,
            "approval_id": _clean(approval_id) or None,
            "limit": bounded,
        },
        "counts": counts,
        "entries": page,
        "matched": len(matched),
        "truncated": len(matched) > len(page),
        "roster": roster,
        "inbox": inbox,
        "sessions": {
            "total": len(session_rows),
            "by_status": _count_by(session_rows, "status"),
        },
        "links": {
            "route_template": ROUTE_TEMPLATE,
            "floors": floors_link,
        },
        "sources": {
            "world": world_state,
            "roster": roster_state,
            "tasks": tasks_state,
            "sessions": sessions_state,
            "approvals": approvals_state,
        },
    }


__all__ = ["SCHEMA", "KINDS", "ROUTE_TEMPLATE", "command_center"]
