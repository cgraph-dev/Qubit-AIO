"""Assign Business OS agents to real tower desks, deterministically.

This is the explicit bridge between the 283 office seats and the five-floor
world plan. It never invents a desk: a desk id comes from the shipped tower
projection. The placement source is recorded so the UI can distinguish an
organisational assignment from a manually pinned ``metadata.world_*`` link.

The assignment remains a Business OS write (one metadata update per agent), so
the caller owns it and can audit it. It does not run a model, spend, publish or
contact anyone.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...business.roles import RoleRegistry
from . import occupancy

SCHEMA = "qubit-office-world-assignment/1"
SOURCE = "office_floor_capacity"
DORMANT = frozenset({"paused", "revoked", "archived"})


def _preferred_floor(agent: Any) -> Optional[int]:
    meta = agent.metadata if isinstance(agent.metadata, dict) else {}
    raw = meta.get("world_floor") or meta.get("floor")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 5 else None


def assign_agents(service: Any, tenant_id: str, *, business_id: Optional[str] = None,
                  actor: str = "dashboard:owner") -> Dict[str, Any]:
    """Assign every active, unplaced agent to a free desk on its preferred floor.

    Preferred floor = the agent's own ``metadata.floor`` (the office plan floor)
    when it exists in the 100-floor tower; otherwise the first active floor with
    a free desk. An explicit existing ``metadata.world_desk`` is preserved and
    reserved. Nothing is assigned twice.
    """
    registry = RoleRegistry.product()
    index = occupancy._Index(registry.floors)
    agents = service.list(tenant_id, business_id=business_id)
    used: set[str] = set()
    # Reserve explicit/manual placements first; never move them.
    for agent in agents:
        desk = (agent.metadata or {}).get("world_desk")
        if desk:
            used.add(str(desk))

    by_floor: Dict[int, List[Any]] = {}
    for desk in index.desks:
        by_floor.setdefault(desk.floor_index, []).append(desk)
    active_floors = sorted(
        (n for n, desks in by_floor.items()
         if n <= 5 and any(d.floor_active for d in desks)),
    )

    assigned: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for agent in sorted(agents, key=lambda a: a.agent_id):
        if agent.status in DORMANT:
            skipped.append({"agent_id": agent.agent_id, "reason": "dormant"})
            continue
        if (agent.metadata or {}).get("world_desk"):
            assigned.append({"agent_id": agent.agent_id, "reason": "already_linked",
                             "desk": agent.metadata.get("world_desk")})
            continue

        preferred = _preferred_floor(agent)
        order = ([preferred] if preferred in active_floors else []) + [
            n for n in active_floors if n != preferred
        ]
        chosen = None
        for floor_no in order:
            for desk in by_floor.get(floor_no, ()):
                if desk.desk_id not in used:
                    chosen = desk
                    break
            if chosen is not None:
                break
        if chosen is None:
            skipped.append({"agent_id": agent.agent_id, "reason": "no_free_desk"})
            continue

        used.add(chosen.desk_id)
        meta = dict(agent.metadata or {})
        meta.update({
            "world_floor": chosen.floor_id,
            "world_room": chosen.room_id,
            "world_desk": chosen.desk_id,
            "world_placement_source": SOURCE,
        })
        try:
            service.update(
                tenant_id,
                agent.agent_id,
                changes={"metadata": meta},
                idempotency_key=f"world-assign:{agent.agent_id}:{chosen.desk_id}",
                actor=actor,
            )
        except Exception as exc:  # noqa: BLE001 - report the agent, continue
            skipped.append({"agent_id": agent.agent_id, "reason": "update_failed",
                            "detail": str(exc)[:200]})
            used.discard(chosen.desk_id)
            continue
        assigned.append({
            "agent_id": agent.agent_id,
            "desk": chosen.desk_id,
            "floor": chosen.floor_id,
            "room": chosen.room_id,
            "source": SOURCE,
        })

    return {
        "ok": True,
        "schema": SCHEMA,
        "tenant_id": tenant_id,
        "business_id": business_id,
        "counts": {
            "agents": len(agents),
            "assigned": len([a for a in assigned if a.get("reason") != "already_linked"]),
            "already_linked": len([a for a in assigned if a.get("reason") == "already_linked"]),
            "skipped": len(skipped),
            "used_desks": len(used),
        },
        "assigned": assigned[:500],
        "skipped": skipped[:500],
    }
