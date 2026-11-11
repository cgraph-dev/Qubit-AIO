"""Office-seat -> Business OS agent bootstrap.

Clicking a seat in the Qubit Office Agents panel must open real controls, not
"no tenant". This module is the one idempotent bridge between the office's own
roster (:mod:`voicebridge.office.agency_org`) and the durable Business OS
control records (:mod:`voicebridge.business.agents`).

It is deliberately *narrow and safe*:

* **No model turn, no spend, no network, no external action.** A created record
  is a control-plane row only: provider ``qubit``, model ``active``, reasoning
  ``medium``, an empty permission map (deny by default), autonomy ``L0``, zero
  spend limits, and approvals for spend/legal/publish/contact/irreversible.
* **Idempotent.** A seat is matched to an existing agent by
  ``metadata.office_seat_slug`` / ``metadata.seat_slug``, then by the exact desk
  slug, then by an exact (unique) display name. An existing record is *never*
  modified — the sync only creates missing seats.
* **The office is read-only.** Nothing here writes the office store, the tower
  or a room; only ``bos_*`` control rows are created.

The two public entry points are :func:`ensure_personal_scope` and
:func:`sync_office_seats`; :func:`plan_building_seats` is the read-only adapter
from the office's plan to the seat shape the sync consumes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from . import agents as AGENTS
from .errors import BAD_INPUT_CODE, EXISTS_CODE, NOT_FOUND_CODE, BusinessError
from .store import BusinessStore

SCHEMA = "qubit-office-business-seats/1"
PERSONAL_TENANT_ID = "ten_personal"
PERSONAL_TENANT_NAME = "Personal"
PERSONAL_BUSINESS_NAME = "Personal"
PERSONAL_BUSINESS_SLUG = "personal"
HUMAN_OWNER = "owner"
SAFE_PROVIDER = "qubit"
SAFE_MODEL = "active"
SAFE_REASONING = "medium"
SOURCE = "office_plan_building"

#: Zero spend, deny-all permissions, observe-only autonomy, human gates.
SAFE_SAFETY: dict[str, Any] = {
    "autonomy": "L0",
    "daily_spend_minor": 0,
    "monthly_spend_minor": 0,
    "per_action_limit_minor": 0,
    "approval_required": list(AGENTS.APPROVAL_ACTIONS),
}


@dataclass(frozen=True, slots=True)
class PersonalScope:
    """The durable Personal tenant/business IDs and whether they were new."""

    tenant_id: str
    business_id: str
    created_tenant: bool
    created_business: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "created_tenant": self.created_tenant,
            "created_business": self.created_business,
        }


@dataclass(frozen=True, slots=True)
class SeatSyncResult:
    """What one sync changed (counts only; no record bodies)."""

    total: int
    created: int
    existing: int
    created_slugs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "created": self.created,
            "existing": self.existing,
            "created_slugs": list(self.created_slugs),
        }


def _text(value: Any, label: str, *, max_len: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _optional_text(value: Any, *, max_len: int = 200) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessError(BAD_INPUT_CODE, "optional text must be a string")
    clean = value.strip()
    return clean[:max_len] if clean else None


def _existing_tenant_by_name(store: BusinessStore, name: str) -> Any:
    for tenant in store.list_tenants():
        if tenant.name == name:
            return tenant
    return None


def ensure_personal_scope(store: BusinessStore) -> PersonalScope:
    """Create or reuse the local ``Personal`` tenant and its business.

    ``ten_personal`` is the canonical ID the rest of the Office bridge already
    assumes; a pre-existing ``Personal`` tenant under another ID is reused
    rather than duplicated, and the first business in that tenant is reused
    before a new one is created. Safe to call repeatedly.
    """
    tenant = None
    try:
        tenant = store.get_tenant(PERSONAL_TENANT_ID)
    except BusinessError as exc:
        if exc.code != NOT_FOUND_CODE:
            raise
    if tenant is None:
        tenant = _existing_tenant_by_name(store, PERSONAL_TENANT_NAME)
    created_tenant = False
    if tenant is None:
        try:
            tenant = store.create_tenant(
                PERSONAL_TENANT_NAME, tenant_id=PERSONAL_TENANT_ID
            )
            created_tenant = True
        except BusinessError as exc:
            if exc.code != EXISTS_CODE:
                raise
            tenant = store.get_tenant(PERSONAL_TENANT_ID)

    businesses = store.list_businesses(tenant.tenant_id)
    created_business = False
    if businesses:
        business = businesses[0]
    else:
        try:
            business = store.create_business(
                tenant.tenant_id,
                PERSONAL_BUSINESS_NAME,
                slug=PERSONAL_BUSINESS_SLUG,
            )
            created_business = True
        except BusinessError as exc:
            if exc.code != EXISTS_CODE:
                raise
            business = store.get_business_by_slug(
                tenant.tenant_id, PERSONAL_BUSINESS_SLUG
            )
    return PersonalScope(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        created_tenant=created_tenant,
        created_business=created_business,
    )


# -- plan adapter ---------------------------------------------------------
def plan_building_seats(root: Any = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read the office's plan and enrich each seat with label/floor metadata.

    This is a pure read of the ported roster; it creates nothing. Importing
    :mod:`voicebridge.office.agency_org` lazily keeps the Business OS package
    cheap for callers that only touch tenant/business primitives.
    """
    from voicebridge.office import agency_org  # local: avoids an import cycle

    plan = agency_org.plan_building(root)
    floors = plan.get("floors") or []
    labels = {str(item.get("slug")): str(item.get("name") or "") for item in floors}
    numbers = {str(item.get("slug")): item.get("number") for item in floors}
    boardroom = plan.get("boardroom") or {}
    exec_floor = boardroom.get("floor") or (len(floors) + 1)
    seats: list[dict[str, Any]] = []
    for raw in plan.get("seats") or []:
        if not isinstance(raw, Mapping):
            continue
        seat = dict(raw)
        division = str(seat.get("division") or "")
        seat["division_label"] = labels.get(division) or ""
        if seat.get("room") == "boardroom" or not division:
            seat["floor"] = exec_floor
        else:
            seat["floor"] = numbers.get(division, exec_floor)
        seats.append(seat)
    return plan, seats


# -- sync -----------------------------------------------------------------
def _seat_metadata(seat: Mapping[str, Any], slug: str) -> dict[str, Any]:
    return {
        "office_seat_slug": slug,
        # Duplicate spelling so a manually created/legacy link still matches.
        "seat_slug": slug,
        "seat_type": str(seat.get("seat_type") or "worker"),
        "division": str(seat.get("division") or ""),
        "supervisor": str(seat.get("supervisor") or ""),
        "floor": seat.get("floor") if seat.get("floor") is not None else "",
        "persona_color": str(seat.get("persona_color") or ""),
        "persona_emoji": str(seat.get("persona_emoji") or ""),
        "source": SOURCE,
    }


def _index_existing(agents: Iterable[AGENTS.Agent]) -> tuple[
    dict[str, AGENTS.Agent], dict[str, AGENTS.Agent], dict[str, int]
]:
    by_slug: dict[str, AGENTS.Agent] = {}
    by_name: dict[str, AGENTS.Agent] = {}
    name_counts: dict[str, int] = {}
    for agent in agents:
        metadata = agent.metadata or {}
        for key in ("office_seat_slug", "seat_slug", "office_slug"):
            raw = str(metadata.get(key) or "").strip()
            if raw and raw not in by_slug:
                by_slug[raw] = agent
        desk = str(agent.desk or "").strip()
        if desk and desk not in by_slug:
            by_slug[desk] = agent
        name = str(agent.display_name or "").strip().lower()
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
            by_name[name] = agent
    return by_slug, by_name, name_counts


def sync_office_seats(
    store: BusinessStore,
    service: AGENTS.AgentControlService,
    tenant_id: str,
    business_id: str,
    seats: Iterable[Mapping[str, Any]],
) -> SeatSyncResult:
    """Create a BOS agent for each office seat that has none. Never edits one.

    Matching order is the durable link (``metadata.office_seat_slug`` /
    ``seat_slug``), then the exact desk slug, then a unique exact display name.
    A seat that matches nothing gets a brand-new control record with the safe
    defaults above; a seat that matches an existing record is left byte-for-byte
    untouched. Re-running the sync creates zero records.
    """
    store.require_business(tenant_id, business_id)
    existing = service.list(tenant_id, business_id=business_id)
    by_slug, by_name, name_counts = _index_existing(existing)
    claimed: set[str] = set()
    created = 0
    created_slugs: list[str] = []
    reused = 0
    total = 0

    for seat in seats:
        if not isinstance(seat, Mapping):
            raise BusinessError(BAD_INPUT_CODE, "each seat must be a mapping")
        total += 1
        slug = _text(seat.get("slug"), "seat slug", max_len=120)
        metadata = _seat_metadata(seat, slug)

        match = by_slug.get(slug)
        if match is not None and match.agent_id in claimed:
            match = None
        if match is None:
            name = str(seat.get("name") or "").strip().lower()
            candidate = by_name.get(name) if name else None
            if (candidate is not None
                    and name_counts.get(name) == 1
                    and candidate.agent_id not in claimed):
                match = candidate
        if match is not None:
            claimed.add(match.agent_id)
            reused += 1
            continue

        seat_type = _text(seat.get("seat_type") or "worker", "seat type",
                          max_len=60)
        department = (
            str(seat.get("division_label") or "").strip()
            or str(seat.get("division") or "").strip()
            or seat_type
        )
        agent, was_created = service.create(
            tenant_id,
            business_id,
            role_id=f"office:{seat_type}"[:120],
            department=department[:120],
            display_name=str(seat.get("name") or slug)[:200],
            human_owner=HUMAN_OWNER,
            provider=SAFE_PROVIDER,
            model=SAFE_MODEL,
            reasoning_effort=SAFE_REASONING,
            room=_optional_text(seat.get("room")),
            desk=slug[:120],
            permissions=AGENTS.Permissions(),
            safety=AGENTS.Safety.from_mapping(SAFE_SAFETY),
            metadata=metadata,
            idempotency_key=f"office-seat:{tenant_id}:{business_id}:{slug}"[:300],
            actor="dashboard:office-seat-bootstrap",
        )
        claimed.add(agent.agent_id)
        if was_created:
            created += 1
            created_slugs.append(slug)
        else:
            reused += 1

    return SeatSyncResult(
        total=total,
        created=created,
        existing=reused,
        created_slugs=tuple(created_slugs),
    )


__all__ = [
    "HUMAN_OWNER",
    "PERSONAL_BUSINESS_NAME",
    "PERSONAL_BUSINESS_SLUG",
    "PERSONAL_TENANT_ID",
    "PERSONAL_TENANT_NAME",
    "PersonalScope",
    "SAFE_MODEL",
    "SAFE_PROVIDER",
    "SAFE_REASONING",
    "SAFE_SAFETY",
    "SCHEMA",
    "SOURCE",
    "SeatSyncResult",
    "ensure_personal_scope",
    "plan_building_seats",
    "sync_office_seats",
]
