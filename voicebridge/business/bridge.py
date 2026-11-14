"""HTTP bridge for the Business OS (Qubit Office Business UI).

This module is the *transport* seam between the dashboard's compat dispatcher
(``voicebridge.qubit_compat``) and the durable Business OS services
(:mod:`voicebridge.business.warroom`,
:mod:`voicebridge.business.agents`,
:mod:`voicebridge.business.roles`). It owns no business rule of its own:

* reads project the same rows the services return;
* mutations call the same owner-only service functions the CLI/voice surfaces
  would call, with the caller's explicit ``owner`` intent and an idempotency
  key;
* nothing here publishes, spends, contacts, or calls a network/platform API;
* every payload is passed through :func:`voicebridge.business.util.redact` so a
  source note, metadata map or audit detail can never re-serve a credential.

The routes are reachable at both ``/api/office/business/...`` (compat
convention) and ``/agent/api/office/business/...`` (the SPA prefix the Office
shell uses). They are only served behind the dashboard session-token gate; a
mutation additionally requires ``{"owner": true}`` so an automated caller that
merely holds a token cannot silently approve an idea or stop an agent.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import secrets
import sqlite3
from typing import Any, Dict, Mapping, Optional, Tuple

from ..integrations.etsy_auth import (
    ETSY_BAD_INPUT,
    ETSY_EXCHANGE_FAILED,
    ETSY_NOT_CONFIGURED,
    ETSY_PKCE_MISMATCH,
    ETSY_REFRESH_FAILED,
    ETSY_STATE_INVALID,
    ETSY_STORE_ERROR,
    ETSY_TRANSPORT_ERROR,
    EtsyAuthError,
    EtsyOAuth,
)
from ..integrations.etsy_auth_transport import EtsyOAuthHttpTransport
from ..office.world import assign as WORLD_ASSIGN
from ..office.world import model as WORLD
from ..office.world import occupancy as OCCUPANCY
from . import agents as AGENTS
from . import effective_state as EFFECTIVE
from . import office_seats as OFFICE_SEATS
from . import pilot as ETSY_PILOT
from . import research_run as RESEARCH_RUN
from . import warroom as WARROOM
from .errors import BusinessError
from .roles import RoleRegistry, RoleRegistryError
from .store import BusinessStore
from .util import redact

SCHEMA = "qubit-office-business/1"
WORLD_SCHEMA = "qubit-office-world-tower/1"
OWNER_ONLY = "BUSINESS_OWNER_ONLY"
TENANT_REQUIRED = "BUSINESS_TENANT_REQUIRED"
INTERNAL = "BUSINESS_BRIDGE_ERROR"

_OWNER_WORDS = frozenset({"owner", "true", "yes", "1"})


def _owner_ok(body: Mapping[str, Any]) -> bool:
    value = body.get("owner")
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in _OWNER_WORDS:
        return True
    return False


def _refusal(status: int, code: str, detail: str) -> Tuple[int, Dict[str, Any]]:
    return status, {
        "ok": False,
        "state": "REFUSED",
        "code": code,
        "detail": detail,
    }


def _owner_refusal(action: str) -> Tuple[int, Dict[str, Any]]:
    return _refusal(
        403,
        OWNER_ONLY,
        f"{action} is an owner action; send owner:true from the owner dashboard",
    )


def _status_for(code: str) -> int:
    if code in (OWNER_ONLY, "BUSINESS_WARROOM_OWNER_ONLY"):
        return 403
    if code in ("BUSINESS_NOT_FOUND", "AGENT_NOT_FOUND", "BUSINESS_UNKNOWN_DEPARTMENT"):
        return 404
    if code in (
        "BUSINESS_EXISTS",
        "BUSINESS_IDEMPOTENCY_CONFLICT",
        "BUSINESS_BAD_STATE",
        "AGENT_NO_ACTIVE_TURN",
        "AGENT_APPROVAL_REQUIRED",
        "AGENT_BUDGET_EXCEEDED",
        "AGENT_TOKEN_LIMIT",
        "AGENT_TURN_LIMIT",
        "AGENT_RATE_LIMITED",
        "AGENT_PAUSED",
        "AGENT_REVOKED",
        "AGENT_ARCHIVED",
        "AGENT_PROVIDER_NOT_READY",
        "AGENT_MODEL_NOT_READY",
    ):
        return 409
    if code in (
        "AGENT_TOOL_DENIED",
        "AGENT_AUTONOMY_DENIED",
    ):
        return 403
    if code in (
        "BUSINESS_BAD_INPUT",
        "BUSINESS_WARROOM_UNVERIFIED_FACT",
        "AGENT_UNKNOWN_SCOPE",
        "AGENT_TOOL_UNKNOWN",
        "BUSINESS_TOOL_NOT_ALLOWED",
    ):
        return 400
    return 400


def _error(exc: BaseException) -> Tuple[int, Dict[str, Any]]:
    if isinstance(exc, BusinessError):
        return _refusal(_status_for(exc.code), exc.code, exc.detail)
    if isinstance(exc, (RoleRegistryError,)):
        return _refusal(400, exc.code, exc.detail)
    if isinstance(exc, sqlite3.Error):
        return _refusal(500, INTERNAL, f"business store error: {str(exc)[:200]}")
    return _refusal(500, INTERNAL, f"business bridge error: {str(exc)[:200]}")


def _store(cfg: Any) -> BusinessStore:
    return BusinessStore.from_config(cfg)


def _text(value: Any, label: str, *, max_len: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError("BUSINESS_BAD_INPUT", f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            "BUSINESS_BAD_INPUT", f"{label} is longer than {max_len} characters"
        )
    return clean


def _optional_text(value: Any, *, max_len: int = 300) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessError("BUSINESS_BAD_INPUT", "optional text must be a string")
    clean = value.strip()
    if not clean:
        return None
    return clean[:max_len]


def _idempotency(body: Mapping[str, Any], action: str, *parts: Any) -> str:
    supplied = body.get("idempotency_key")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()[:300]
    material = {
        "action": action,
        "parts": [str(part) for part in parts],
        "body": {k: v for k, v in body.items() if k not in ("owner", "idempotency_key")},
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:32]
    return f"ui:{action}:{digest}"


def _unique_idempotency(
    body: Mapping[str, Any], action: str, *parts: Any
) -> str:
    """A supplied key wins; otherwise this call is a fresh effect.

    Lifecycle actions (pause/resume/stop/update) may legitimately repeat with
    the same body after the state changed, so a derived body hash would replay
    the stale result. The UI disables its buttons while an action is in flight;
    a caller that needs cross-process replay supplies its own key.
    """
    supplied = body.get("idempotency_key")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()[:300]
    return f"ui:{action}:{secrets.token_hex(8)}"


def _tenant(
    store: BusinessStore,
    query: Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    create_personal: bool = False,
) -> str:
    supplied = body.get("tenant_id") or query.get("tenant_id")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    tenants = store.list_tenants()
    if len(tenants) == 1:
        return tenants[0].tenant_id
    if not tenants and create_personal:
        try:
            tenant = store.create_tenant("Personal", tenant_id="ten_personal")
        except BusinessError as exc:
            if exc.code != "BUSINESS_EXISTS":
                raise
            tenant = store.get_tenant("ten_personal")
        return tenant.tenant_id
    if not tenants:
        raise BusinessError(
            "BUSINESS_NOT_FOUND", "no Business OS tenant exists yet"
        )
    raise BusinessError(
        TENANT_REQUIRED,
        "tenant_id is required when the Business OS has more than one tenant",
    )


def _asdict(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, (list, tuple)):
        return [_asdict(item) for item in value]
    return value


@functools.lru_cache(maxsize=1)
def _registry() -> RoleRegistry:
    return RoleRegistry.default()


def _ok(schema: str = SCHEMA, **fields: Any) -> Tuple[int, Dict[str, Any]]:
    out = {"ok": True, "schema": schema}
    out.update(redact(fields))
    return 200, out


# ---------------------------------------------------------------------------
# Overview / scope
# ---------------------------------------------------------------------------
def h_business_overview(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/office/business/overview -> local Business OS scope.

    Reports tenants and their businesses plus small counts. It never creates a
    tenant on a read; an empty store is a named empty state, not a fabricated
    "Personal" workspace.
    """
    try:
        store = _store(cfg)
        tenants = store.list_tenants()
        views = []
        for tenant in tenants:
            businesses = store.list_businesses(tenant.tenant_id)
            views.append({
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "created_at": tenant.created_at,
                "businesses": [
                    {
                        "business_id": item.business_id,
                        "name": item.name,
                        "slug": item.slug,
                        "status": item.status,
                        "currency": item.currency,
                    }
                    for item in businesses
                ],
            })
        default_tenant = views[0]["tenant_id"] if views else None
        default_business = None
        if views and views[0]["businesses"]:
            default_business = views[0]["businesses"][0]["business_id"]
        return _ok(
            tenants=views,
            default_tenant_id=default_tenant,
            default_business_id=default_business,
            counts={"tenants": len(views)},
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_directory(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/office/business/directory -> roles, departments, floors.

    The registry is a template catalog, not a roster. Nothing here is an agent:
    a floor/role only materialises when an actual Business OS record exists.
    """
    try:
        registry = _registry()
        roles = [item.to_dict() for item in registry.roles]
        departments = [item.to_dict() for item in registry.departments]
        floors = []
        for floor in registry.floors:
            floors.append({
                "floor_id": floor.floor_id,
                "index": floor.index,
                "wing": floor.wing,
                "name": floor.name,
                "department_ids": list(floor.department_ids),
                "role_ids": list(floor.role_ids),
                "room_count": len(floor.rooms),
                "desk_capacity": floor.desk_capacity,
                "active_by_default": floor.active_by_default,
                "max_rooms": floor.max_rooms,
                "max_agents": floor.max_agents,
                "expansion_allowed": floor.expansion_allowed,
            })
        wings = sorted({floor["wing"] for floor in floors})
        return _ok(
            schema="qubit-office-business-directory/1",
            departments=departments,
            roles=roles,
            floors=floors,
            counts={
                "departments": len(departments),
                "roles": len(roles),
                "floors": len(floors),
                "active_floors": sum(1 for f in floors if f["active_by_default"]),
                "dormant_floors": sum(1 for f in floors if not f["active_by_default"]),
                "wings": len(wings),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_roles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/office/business/roles -> departments + role templates."""
    try:
        registry = _registry()
        department = (q.get("department") or "").strip() or None
        roles = registry.filter_roles(department=department) if department else registry.roles
        return _ok(
            schema="qubit-office-business-roles/1",
            departments=[item.to_dict() for item in registry.departments],
            roles=[item.to_dict() for item in roles],
            counts={"departments": len(registry.departments), "roles": len(roles)},
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_floors(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/office/business/org/floors -> floor/wing templates."""
    try:
        registry = _registry()
        wing = (q.get("wing") or "").strip() or None
        floors = registry.filter_floors(wing=wing) if wing else registry.floors
        return _ok(
            schema="qubit-office-business-floors/1",
            floors=[
                {
                    "floor_id": item.floor_id,
                    "index": item.index,
                    "wing": item.wing,
                    "name": item.name,
                    "department_ids": list(item.department_ids),
                    "role_ids": list(item.role_ids),
                    "room_count": len(item.rooms),
                    "desk_capacity": item.desk_capacity,
                    "active_by_default": item.active_by_default,
                    "max_rooms": item.max_rooms,
                    "max_agents": item.max_agents,
                    "expansion_allowed": item.expansion_allowed,
                }
                for item in floors
            ],
            counts={"floors": len(floors), "wings": len({f.wing for f in registry.floors})},
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _occupancy_agent_records(cfg: Any) -> Tuple[list[Dict[str, Any]], str]:
    """Read Business OS agent records through a read-only SQLite connection.

    A missing database is an empty, named state. This function never creates
    the store, never migrates it and never writes: ``?staffed=1`` must stay a
    read even on a machine that has never run the bootstrap.
    """
    store = BusinessStore.from_config(cfg)
    path = store.path
    if not path.exists():
        return [], "missing"
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT agent_id, tenant_id, business_id, department,"
            " display_name, status, desk, room, role_id, metadata_json"
            " FROM bos_agent ORDER BY agent_id"
        ).fetchall()
    except sqlite3.Error:
        return [], "unavailable"
    finally:
        if conn is not None:
            conn.close()
    agents: list[Dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        agents.append({
            "agent_id": row["agent_id"],
            "tenant_id": row["tenant_id"],
            "business_id": row["business_id"],
            "department": row["department"] or "",
            "display_name": row["display_name"] or "",
            "status": row["status"] or "active",
            "desk": row["desk"] or "",
            "room": row["room"] or "",
            "role_id": row["role_id"] or "",
            "metadata": metadata,
        })
    return agents, "business_db"


def _occupancy_seat_records() -> Tuple[list[Dict[str, Any]], str]:
    """Read the office-plan seat list (filesystem only; no network, no store)."""
    try:
        _plan, seats = OFFICE_SEATS.plan_building_seats()
    except Exception:  # noqa: BLE001 - a read surface must not 500
        return [], "unavailable"
    return [dict(seat) for seat in seats], "office_plan"


def _world_room(room: Any, *, active: bool,
                occupants: Optional[Mapping[str, Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
    """Project one room template into the world scene shape.

    Cells come from :mod:`voicebridge.office.world.model`, which owns the fixed
    16x12 primitive and the six desk slots per cell. Nothing here binds an
    agent: every seat's ``occupied_by`` is left as the model produced it, which
    is ``None`` for a template, so an empty desk stays honestly empty.
    """
    cells = WORLD.plan_cell_cluster(
        room.room_id, room.desks, kind=room.kind)
    cell_payload = []
    for cell in cells:
        cell_payload.append({
            "cell_id": cell.cell_id,
            "index": int(cell.index),
            "cols": int(cell.cols),
            "rows": int(cell.rows),
            "origin": cell.origin.to_dict(),
            "desk_slots": [
                {
                    "desk_id": desk.desk_id,
                    "seat_id": desk.seat.seat_id,
                    "tile": desk.tile.to_dict(),
                    "footprint": {
                        "w": int(desk.width),
                        "h": int(desk.height),
                    },
                    "occupied_by": (occupants or {}).get(desk.desk_id),
                }
                for desk in cell.desks
            ],
        })
    return {
        "room_id": room.room_id,
        "name": room.name,
        "kind": room.kind,
        "archetype": room.kind or "work",
        "role_ids": list(room.role_ids),
        "active": bool(active),
        "state": "template",
        "template": True,
        "capacity": int(room.desks),
        "cell_count": len(cell_payload),
        "cells": cell_payload,
    }


def h_world_tower(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/office/world/tower -> the 100-floor tower as room scenes.

    A pure projection over the shipped floor/room templates. By default it is
    explicitly unstaffed: rooms are template scenes with empty desk slots, not
    agents. ``?staffed=1`` adds the explicit occupancy projection: a Business
    OS agent reaches a desk only through an explicit link, and an unmatched
    agent is returned in ``occupancy.unplaced`` with its reason. The endpoint
    is read-only and never creates a floor, a room, a seat, an agent or a
    Business OS record.
    """
    try:
        registry = _registry()
        staffed_mode = _truthy((q or {}).get("staffed"))
        projection = None
        occupants: Dict[str, Dict[str, Any]] = {}
        agents_source = seat_source = ""
        if staffed_mode:
            records, agents_source = _occupancy_agent_records(cfg)
            seats, seat_source = _occupancy_seat_records()
            projection = OCCUPANCY.project_occupancy(
                registry=registry, agents=records, office_seats=seats)
            occupants = projection.by_desk()
        floors: Dict[str, Any] = []
        wing_order: list[str] = []
        wing_counts: Dict[str, Dict[str, int]] = {}
        room_total = cell_total = desk_total = active_room_total = 0
        for floor in registry.floors:
            if floor.wing not in wing_order:
                wing_order.append(floor.wing)
            rooms = [_world_room(
                room, active=floor.active_by_default,
                occupants=occupants or None) for room in floor.rooms]
            floor_cells = sum(room["cell_count"] for room in rooms)
            floor_desks = sum(room["capacity"] for room in rooms)
            active_rooms = sum(1 for room in rooms if room["active"])
            room_total += len(rooms)
            cell_total += floor_cells
            desk_total += floor_desks
            active_room_total += active_rooms
            bucket = wing_counts.setdefault(floor.wing, {
                "floors": 0,
                "active_floors": 0,
                "dormant_floors": 0,
                "rooms": 0,
            })
            bucket["floors"] += 1
            bucket["active_floors" if floor.active_by_default
                   else "dormant_floors"] += 1
            bucket["rooms"] += len(rooms)
            floors.append({
                "floor_id": floor.floor_id,
                "index": int(floor.index),
                "wing": floor.wing,
                "wing_name": f"{floor.wing.title()} Wing",
                "name": floor.name,
                "active_by_default": bool(floor.active_by_default),
                "active": bool(floor.active_by_default),
                "department_ids": list(floor.department_ids),
                "role_ids": list(floor.role_ids),
                "room_count": len(rooms),
                "desk_capacity": floor_desks,
                "cells": floor_cells,
                "capacity": {
                    "rooms": len(rooms),
                    "cells": floor_cells,
                    "desks": floor_desks,
                },
                "rooms": rooms,
            })
        active_floors = sum(1 for floor in floors if floor["active"])
        wings = [
            {
                "wing": wing,
                "name": f"{wing.title()} Wing",
                "floor_count": wing_counts[wing]["floors"],
                "active_floors": wing_counts[wing]["active_floors"],
                "dormant_floors": wing_counts[wing]["dormant_floors"],
                "rooms": wing_counts[wing]["rooms"],
            }
            for wing in wing_order
        ]
        occupancy_counts = projection.counts() if projection else {}
        payload = _ok(
            WORLD_SCHEMA,
            source="templates",
            staffed=bool(occupancy_counts.get("placed")),
            campus={"campus_id": "campus:office", "name": "Qubit Office"},
            counts={
                "floors": len(floors),
                "wings": len(wings),
                "active_floors": active_floors,
                "dormant_floors": len(floors) - active_floors,
                "rooms": room_total,
                "cells": cell_total,
                "desks": desk_total,
                "active_rooms": active_room_total,
                "dormant_rooms": room_total - active_room_total,
                "agents": int(occupancy_counts.get("placed", 0)),
                "occupied_desks": int(occupancy_counts.get("placed", 0)),
            },
            wings=wings,
            floors=floors,
        )[1]
        if projection is not None:
            occupancy = OCCUPANCY.occupancy_payload(projection)
            occupancy["agents_source"] = agents_source
            occupancy["seat_source"] = seat_source
            occupancy["requested"] = True
            payload["occupancy"] = occupancy
            payload["staffed"] = bool(occupancy["staffed"])
        return 200, payload
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# ---------------------------------------------------------------------------
# Office-seat link/bootstrap
# ---------------------------------------------------------------------------
def h_business_bootstrap(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/office/business/bootstrap -> local Personal scope + 283 seats.

    Creates the durable ``Personal`` tenant/business when absent and imports
    every seat of the office's own plan as a Business OS agent that does not
    already have a control record. It is idempotent (a repeat creates zero
    records), owner-gated like every other Business OS write, and it never runs
    a model, spends, contacts anyone, or calls an external platform.
    """
    if not _owner_ok(b):
        return _owner_refusal("bootstrapping the office seat roster")
    try:
        store, service = _service(cfg)
        scope = OFFICE_SEATS.ensure_personal_scope(store)
        plan, seats = OFFICE_SEATS.plan_building_seats()
        if not seats:
            verdict = plan.get("check") or {}
            raise BusinessError(
                "BUSINESS_NOT_FOUND",
                "the office roster is empty: "
                + str(verdict.get("reason") or "no ported personas on disk"),
            )
        result = OFFICE_SEATS.sync_office_seats(
            store, service, scope.tenant_id, scope.business_id, seats)
        return _ok(
            schema="qubit-office-business-bootstrap/1",
            tenant_id=scope.tenant_id,
            business_id=scope.business_id,
            created_tenant=scope.created_tenant,
            created_business=scope.created_business,
            plan_ok=bool(plan.get("ok")),
            total=result.total,
            created=result.created,
            existing=result.existing,
            created_slugs=list(result.created_slugs),
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# ---------------------------------------------------------------------------
# War Room
# ---------------------------------------------------------------------------
def h_business_warroom_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        store = _store(cfg)
        tenant = _tenant(store, q, b)
        business_id = _optional_text(q.get("business_id"))
        state = _optional_text(q.get("state"))
        sessions = WARROOM.list_sessions(
            store, tenant, business_id=business_id, state=state
        )
        return _ok(schema="qubit-office-business-warroom-list/1",
                   tenant_id=tenant, sessions=[_asdict(item) for item in sessions])
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_warroom_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    if not _owner_ok(b):
        return _owner_refusal("opening a War Room session")
    try:
        store = _store(cfg)
        tenant = _tenant(store, q, b, create_personal=True)
        title = _text(b.get("title"), "title", max_len=300)
        business_id = _optional_text(b.get("business_id"))
        key = _idempotency(b, "warroom.create", tenant, business_id, title)
        session, created = WARROOM.create_session(
            store,
            tenant_id=tenant,
            business_id=business_id,
            title=title,
            idempotency_key=key,
        )
        return _ok(
            schema="qubit-office-business-warroom-session/1",
            tenant_id=tenant,
            created=created,
            session=_asdict(session),
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_warroom_get(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        store = _store(cfg)
        tenant = _tenant(store, q, b)
        session_id = _text((p or {}).get("id"), "session id", max_len=80)
        bundle = WARROOM.get_bundle(store, tenant, session_id)
        return _ok(
            schema="qubit-office-business-warroom-bundle/1",
            tenant_id=tenant,
            **{key: _asdict(value) for key, value in bundle.items()},
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def _warroom_mutation(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str], action: str, fn: Any
                      ) -> Tuple[int, Dict[str, Any]]:
    if not _owner_ok(b):
        return _owner_refusal(action)
    try:
        store = _store(cfg)
        tenant = _tenant(store, q, b)
        session_id = _text((p or {}).get("id"), "session id", max_len=80)
        return fn(store, tenant, session_id)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_warroom_source_add(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    def run(store: BusinessStore, tenant: str, session_id: str):
        source, created = WARROOM.add_source(
            store,
            tenant_id=tenant,
            session_id=session_id,
            kind=_text(b.get("kind"), "kind", max_len=40),
            locator=_text(b.get("locator"), "locator", max_len=2048),
            title=_optional_text(b.get("title"), max_len=300) or "",
            metadata=b.get("metadata") if isinstance(b.get("metadata"), Mapping) else None,
        )
        return _ok(schema="qubit-office-business-warroom-source/1",
                   created=created, source=_asdict(source))
    return _warroom_mutation(cfg, q, b, p, "adding a War Room source", run)


def h_business_warroom_evidence_add(cfg: Any, q: Dict[str, str],
                                    b: Dict[str, Any], p: Dict[str, str]
                                    ) -> Tuple[int, Dict[str, Any]]:
    def run(store: BusinessStore, tenant: str, session_id: str):
        evidence, created = WARROOM.add_evidence(
            store,
            tenant_id=tenant,
            session_id=session_id,
            label=_text(b.get("label"), "label", max_len=20),
            claim=_text(b.get("claim"), "claim", max_len=2000),
            source_id=_optional_text(b.get("source_id"), max_len=80),
            quote=str(b.get("quote") or "")[:4000],
            metadata=b.get("metadata") if isinstance(b.get("metadata"), Mapping) else None,
        )
        return _ok(schema="qubit-office-business-warroom-evidence/1",
                   created=created, evidence=_asdict(evidence))
    return _warroom_mutation(cfg, q, b, p, "adding War Room evidence", run)


def h_business_warroom_argument_add(cfg: Any, q: Dict[str, str],
                                    b: Dict[str, Any], p: Dict[str, str]
                                    ) -> Tuple[int, Dict[str, Any]]:
    def run(store: BusinessStore, tenant: str, session_id: str):
        refs = b.get("evidence_ids")
        if refs is not None and not isinstance(refs, list):
            raise BusinessError("BUSINESS_BAD_INPUT", "evidence_ids must be a list")
        argument, created = WARROOM.add_argument(
            store,
            tenant_id=tenant,
            session_id=session_id,
            round_no=b.get("round_no", 1),
            role=_text(b.get("role"), "role", max_len=60),
            stance=_text(b.get("stance"), "stance", max_len=20),
            text=_text(b.get("text"), "text", max_len=4000),
            evidence_ids=[str(item) for item in (refs or [])],
        )
        return _ok(schema="qubit-office-business-warroom-argument/1",
                   created=created, argument=_asdict(argument))
    return _warroom_mutation(cfg, q, b, p, "appending a War Room argument", run)


def h_business_warroom_decide(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    def run(store: BusinessStore, tenant: str, session_id: str):
        decision, created = WARROOM.record_decision(
            store,
            tenant_id=tenant,
            session_id=session_id,
            decision=_text(b.get("decision"), "decision", max_len=20),
            decided_by="owner",
            note=str(b.get("note") or "")[:2000],
            idempotency_key=_idempotency(
                b, "warroom.decision", tenant, session_id,
                b.get("decision"), b.get("note"),
            ),
        )
        return _ok(schema="qubit-office-business-warroom-decision/1",
                   created=created, decision=_asdict(decision))
    return _warroom_mutation(cfg, q, b, p, "recording the owner decision", run)


def h_business_warroom_tasks_create(cfg: Any, q: Dict[str, str],
                                    b: Dict[str, Any], p: Dict[str, str]
                                    ) -> Tuple[int, Dict[str, Any]]:
    def run(store: BusinessStore, tenant: str, session_id: str):
        tasks = WARROOM.create_tasks(store, tenant_id=tenant,
                                     session_id=session_id, actor="owner")
        return _ok(schema="qubit-office-business-warroom-tasks/1",
                   tasks=[_asdict(item) for item in tasks])
    return _warroom_mutation(cfg, q, b, p, "creating approved tasks", run)


def h_business_warroom_etsy_pilot(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any], p: Dict[str, str]
                                  ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/office/business/warroom/etsy-pilot -> one pending pilot.

    Owner-gated and idempotent. It records the shipped Etsy playbook, labelled
    plan/risk evidence and experiments, and leaves the decision to the owner:
    no approval, task, publish, spend or external contact happens here.
    """
    if not _owner_ok(b):
        return _owner_refusal("starting the Etsy pilot")
    try:
        store = _store(cfg)
        tenant = _tenant(store, q, b, create_personal=True)
        business_id = _optional_text(b.get("business_id"))
        provider = b.get("provider")
        if provider is not None and not isinstance(provider, str):
            raise BusinessError(
                "BUSINESS_BAD_INPUT", "provider must be a string when supplied"
            )
        result = ETSY_PILOT.bootstrap_etsy_pilot(
            store,
            tenant_id=tenant,
            business_id=business_id,
            provider=provider.strip() if provider and provider.strip() else None,
        )
        return _ok(
            schema="qubit-office-business-warroom-etsy-pilot/1",
            **_asdict(result),
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_warroom_research(cfg: Any, q: Dict[str, str],
                                b: Dict[str, Any], p: Dict[str, str]
                                ) -> Tuple[int, Dict[str, Any]]:
    """POST /.../warroom/sessions/<id>/research -> cited sources + evidence.

    Owner-gated and idempotent per session+query. It appends the provider's
    source URLs and labelled claims only; it never records a decision, creates a
    task, requests an approval, publishes, spends or contacts anyone.
    """
    if not _owner_ok(b):
        return _owner_refusal("running War Room research")
    try:
        store = _store(cfg)
        tenant = _tenant(store, q, b)
        session_id = _text((p or {}).get("id"), "session id", max_len=80)
        provider = b.get("provider")
        if provider is not None and not isinstance(provider, str):
            raise BusinessError(
                "BUSINESS_BAD_INPUT", "provider must be a string when supplied"
            )
        raw_inputs = b.get("inputs")
        if raw_inputs is not None and not isinstance(raw_inputs, list):
            raise BusinessError("BUSINESS_BAD_INPUT", "inputs must be a list")
        result = RESEARCH_RUN.run_research(
            store,
            tenant_id=tenant,
            session_id=session_id,
            query=_optional_text(b.get("query"), max_len=2000) or "",
            inputs=raw_inputs,
            provider=(provider or "perplexity").strip() or "perplexity",
            cfg=cfg,
        )
        return _ok(
            schema="qubit-office-business-warroom-research/1",
            **_asdict(result),
            source_ids=list(result.source_ids),
            evidence_ids=list(result.evidence_ids),
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# ---------------------------------------------------------------------------
# Agent controls
# ---------------------------------------------------------------------------
def _service(cfg: Any) -> Tuple[BusinessStore, AGENTS.AgentControlService]:
    store = _store(cfg)
    return store, AGENTS.AgentControlService(store)


def h_business_agents_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        store, service = _service(cfg)
        tenant = _tenant(store, q, b)
        agents = service.list(
            tenant,
            business_id=_optional_text(q.get("business_id")),
            status=_optional_text(q.get("status")),
            department=_optional_text(q.get("department")),
            role_id=_optional_text(q.get("role_id")),
        )
        return _ok(schema="qubit-office-business-agents/1",
                   tenant_id=tenant, agents=[item.to_dict() for item in agents])
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_agent_get(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        store, service = _service(cfg)
        tenant = _tenant(store, q, b)
        agent_id = _text((p or {}).get("id"), "agent id", max_len=80)
        agent = service.get(tenant, agent_id)
        audit = service.audit(tenant, agent_id, limit=200)
        return _ok(schema="qubit-office-business-agent/1",
                   tenant_id=tenant,
                   agent=agent.to_dict(),
                   audit=[_asdict(item) for item in audit])
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_agent_effective(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/office/business/agents/<id>/effective -> effective state.

    Read-only and owner/tenant-gated by the same ``_tenant`` rule as the other
    agent reads. It composes the real stores (agent record, identity grants,
    leases, budget/usage, approvals, audit, links) and never writes a row. A
    missing agent is the service's typed ``AGENT_NOT_FOUND`` refusal, so a
    cross-tenant id is indistinguishable from a missing one.
    """
    try:
        store = _store(cfg)
        tenant = _tenant(store, q, b)
        agent_id = _text((p or {}).get("id"), "agent id", max_len=80)
        projection = EFFECTIVE.effective_agent_state(store, tenant, agent_id)
        return _ok(schema=EFFECTIVE.SCHEMA, projection=projection)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_agent_audit(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        store, service = _service(cfg)
        tenant = _tenant(store, q, b)
        agent_id = _text((p or {}).get("id"), "agent id", max_len=80)
        try:
            limit = max(1, min(1000, int(q.get("limit") or 500)))
        except (TypeError, ValueError):
            raise BusinessError("BUSINESS_BAD_INPUT", "limit must be an integer")
        audit = service.audit(tenant, agent_id, limit=limit)
        return _ok(schema="qubit-office-business-agent-audit/1",
                   tenant_id=tenant, agent_id=agent_id,
                   audit=[_asdict(item) for item in audit])
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def _agent_mutation(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str], action: str, fn: Any
                    ) -> Tuple[int, Dict[str, Any]]:
    if not _owner_ok(b):
        return _owner_refusal(action)
    try:
        store, service = _service(cfg)
        tenant = _tenant(store, q, b)
        agent_id = _text((p or {}).get("id"), "agent id", max_len=80)
        return fn(store, service, tenant, agent_id)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_agents_assign_world(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/office/business/agents/world-assign -> assign free tower desks.

    Owner-gated and idempotent per agent+desk. Only active BOS agents without a
    ``world_desk`` are assigned; explicit links are preserved. No model, spend,
    publish or contact.
    """
    if not _owner_ok(b):
        return _owner_refusal("assigning office agents to the world")
    try:
        store, service = _service(cfg)
        tenant = _tenant(store, q, b)
        business_id = _optional_text(
            (b or {}).get("business_id") or (q or {}).get("business_id"))
        result = WORLD_ASSIGN.assign_agents(
            service, tenant, business_id=business_id,
            actor="dashboard:owner")
        return _ok(schema="qubit-office-world-assignment/1",
                   tenant_id=tenant, business_id=business_id,
                   counts=result["counts"], assigned=result["assigned"],
                   skipped=result["skipped"])
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def h_business_agent_preview(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Owner-gated, read-only plan for one prospective agent turn.

    No turn, usage row, audit event, ledger entry, job or tool call is written.
    A refusal is the service's typed code; an allowed answer is the plan.
    """
    def run(store: BusinessStore, service: AGENTS.AgentControlService,
            tenant: str, agent_id: str):
        plan = service.preview_turn(
            tenant,
            agent_id,
            prompt=b.get("prompt"),
            requested_tools=b.get("requested_tools"),
            spend_minor=b.get("spend_minor", 0),
            human_approved=b.get("human_approved", False),
        )
        return _ok(schema="qubit-office-business-agent-preview/1",
                   tenant_id=tenant, agent_id=agent_id,
                   allowed=bool(plan.get("allowed")), plan=plan)
    return _agent_mutation(cfg, q, b, p, "previewing an agent turn", run)


def h_business_agent_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    def run(store: BusinessStore, service: AGENTS.AgentControlService,
            tenant: str, agent_id: str):
        changes = b.get("changes")
        if changes is None:
            changes = {
                key: value for key, value in b.items()
                if key not in ("owner", "tenant_id", "idempotency_key", "changes")
            }
        if not isinstance(changes, Mapping) or not changes:
            raise BusinessError("BUSINESS_BAD_INPUT", "changes must be a non-empty mapping")
        agent = service.update(
            tenant, agent_id, changes=changes,
            idempotency_key=_unique_idempotency(
                b, "agent.update", tenant, agent_id, dict(changes)),
            actor="dashboard:user",
        )
        return _ok(schema="qubit-office-business-agent/1",
                   tenant_id=tenant, agent=agent.to_dict())
    return _agent_mutation(cfg, q, b, p, "updating an agent", run)


def _lifecycle_handler(action: str, method_name: str):
    def handler(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        def run(store: BusinessStore, service: AGENTS.AgentControlService,
                tenant: str, agent_id: str):
            kwargs: Dict[str, Any] = {
                "idempotency_key": _unique_idempotency(b, action, tenant, agent_id),
                "actor": "dashboard:user",
                "note": str(b.get("note") or "")[:2000],
            }
            if action == "agent.stop":
                kwargs["turn_id"] = _optional_text(b.get("turn_id"), max_len=80)
            if action == "agent.takeover":
                kwargs["actor"] = "dashboard:owner"
            if action == "agent.revoke":
                scopes = b.get("scopes")
                if scopes is not None and not isinstance(scopes, list):
                    raise BusinessError("BUSINESS_BAD_INPUT", "scopes must be a list")
                kwargs["scopes"] = [str(item) for item in (scopes or [])] or None
            if method_name in ("pause", "resume", "takeover", "revoke", "archive"):
                out = getattr(service, method_name)(tenant, agent_id, **kwargs)
            else:
                out = service.stop(tenant, agent_id, **kwargs)
            payload = out.to_dict() if hasattr(out, "to_dict") else _asdict(out)
            return _ok(schema=f"qubit-office-business-agent-{method_name}/1",
                       tenant_id=tenant, agent_id=agent_id, result=payload)
        return _agent_mutation(cfg, q, b, p, action, run)
    handler.__name__ = f"h_business_agent_{method_name}"
    return handler


h_business_agent_pause = _lifecycle_handler("agent.pause", "pause")
h_business_agent_resume = _lifecycle_handler("agent.resume", "resume")
h_business_agent_takeover = _lifecycle_handler("agent.takeover", "takeover")
h_business_agent_revoke = _lifecycle_handler("agent.revoke", "revoke")
h_business_agent_archive = _lifecycle_handler("agent.archive", "archive")
h_business_agent_stop = _lifecycle_handler("agent.stop", "stop")


# ---------------------------------------------------------------------------
# Etsy OAuth connection (connection only; never publishes or spends)
# ---------------------------------------------------------------------------
ETSY_STATUS_SCHEMA = "qubit-office-business-etsy-status/1"
ETSY_CONNECT_SCHEMA = "qubit-office-business-etsy-connect/1"


def _etsy_transport() -> Any:
    """The production HTTP transport for the token endpoint.

    Injected explicitly so the pure OAuth state machine never imports a network
    client. A test monkeypatches this factory with a fake.
    """
    return EtsyOAuthHttpTransport()


def _etsy_oauth(cfg: Any, *, transport: Any = None) -> EtsyOAuth:
    return EtsyOAuth.from_config(
        cfg,
        transport=_etsy_transport() if transport is None else transport,
    )


def _etsy_status_code(code: str) -> int:
    if code in (ETSY_NOT_CONFIGURED, ETSY_BAD_INPUT):
        return 400
    if code in (ETSY_STATE_INVALID, ETSY_PKCE_MISMATCH):
        return 409
    if code in (ETSY_TRANSPORT_ERROR, ETSY_EXCHANGE_FAILED, ETSY_REFRESH_FAILED):
        return 502
    if code == ETSY_STORE_ERROR:
        return 500
    return 400


def _etsy_failure(exc: EtsyAuthError) -> Tuple[int, Dict[str, Any]]:
    return _refusal(_etsy_status_code(exc.code), exc.code, exc.detail)


def h_business_etsy_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/office/business/etsy/status -> typed connection status.

    Read-only. The payload carries no access/refresh value and no client
    secret; only the configured/missing labels, the canonical state and
    timestamps. A read never creates the store, starts an authorization or
    contacts Etsy.
    """
    try:
        status = _etsy_oauth(cfg).status()
    except EtsyAuthError as exc:
        status = {
            "platform": "etsy",
            "configured": False,
            "state": "error",
            "code": exc.code,
            "detail": exc.detail,
            "connected": False,
            "missing": [],
        }
    return _ok(ETSY_STATUS_SCHEMA, etsy=status)


def h_business_etsy_authorize(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/office/business/etsy/authorize -> PKCE authorization URL.

    Owner-gated. It stores the pending state/PKCE verifier and returns the URL
    the operator opens at Etsy. It does not use a token and does not contact
    Etsy.
    """
    if not _owner_ok(b):
        return _owner_refusal("starting an Etsy OAuth connection")
    try:
        flow = _etsy_oauth(cfg).begin_authorization()
    except EtsyAuthError as exc:
        return _etsy_failure(exc)
    return _ok(ETSY_CONNECT_SCHEMA, etsy=flow)


def h_business_etsy_complete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/office/business/etsy/complete {code, state} -> exchange.

    Owner-gated. Validates the pending state and PKCE challenge before the
    injected transport exchanges the code, stores the tokens privately and
    returns the resulting status. It never echoes the code, verifier or token.
    """
    if not _owner_ok(b):
        return _owner_refusal("completing an Etsy OAuth connection")
    code = b.get("code")
    state = b.get("state")
    if not isinstance(code, str) or not isinstance(state, str):
        return _refusal(400, ETSY_BAD_INPUT,
                        "the Etsy callback requires string code and state")
    try:
        status = _etsy_oauth(cfg).complete_authorization(code, state)
    except EtsyAuthError as exc:
        return _etsy_failure(exc)
    return _ok(ETSY_STATUS_SCHEMA, etsy=status)


def h_business_etsy_disconnect(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/office/business/etsy/disconnect -> clear the local token.

    Owner-gated. Removes only local OAuth state; it does not call Etsy and it
    cannot affect a listing, order or payout.
    """
    if not _owner_ok(b):
        return _owner_refusal("disconnecting Etsy")
    try:
        status = _etsy_oauth(cfg).disconnect()
    except EtsyAuthError as exc:
        return _etsy_failure(exc)
    return _ok(ETSY_STATUS_SCHEMA, etsy=status)


__all__ = [
    "SCHEMA",
    "h_business_agent_archive",
    "h_business_agent_audit",
    "h_business_agent_effective",
    "h_business_agent_get",
    "h_business_agent_pause",
    "h_business_agent_preview",
    "h_business_agent_resume",
    "h_business_agent_revoke",
    "h_business_agent_stop",
    "h_business_agent_takeover",
    "h_business_agent_update",
    "h_business_agents_assign_world",
    "h_business_agents_list",
    "h_business_bootstrap",
    "h_business_directory",
    "h_business_etsy_authorize",
    "h_business_etsy_complete",
    "h_business_etsy_disconnect",
    "h_business_etsy_status",
    "h_business_floors",
    "h_business_overview",
    "h_business_roles",
    "h_business_warroom_argument_add",
    "h_business_warroom_create",
    "h_business_warroom_decide",
    "h_business_warroom_etsy_pilot",
    "h_business_warroom_evidence_add",
    "h_business_warroom_get",
    "h_business_warroom_list",
    "h_business_warroom_research",
    "h_business_warroom_source_add",
    "h_business_warroom_tasks_create",
    "h_world_tower",
]
