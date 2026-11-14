"""Data-driven role/capability registry for the Qubit Office corporation.

The registry is a pure, side-effect-free description of *who can do what*.
It owns no database, starts no agent and calls no model: it is the contract the
Office/UI/API read when it needs to plan a floor, fill a desk or hand a task to
an employee.

Three pieces, all data rather than one class per job:

* :class:`Department` — a canonical division (Executive, Revenue, ...).
* :class:`RoleTemplate` — one employee template: mission, responsibilities,
  skills, toolsets, data scopes, permissions, budget/rate limits, autonomy
  (``L0``–``L5``), approval gates, schedule, KPIs, escalation, model hints, SOP
  and memory refs, service lines and a human owner.
* :class:`FloorTemplate` — one floor/wing of the 100-floor campus. Floors are
  *organizational and visual groups*, never business truth; only an active
  business/room materializes its roles. No hard cap is imposed on floors,
  rooms, desks or agents (:attr:`FloorTemplate.max_rooms` /
  :attr:`FloorTemplate.max_agents` are ``None`` by design).

Safety is validated, not documented. A role cannot default to a wildcard or a
destructive permission, an unknown department, an invalid autonomy level, or a
spend/legal/publish/contact action without its matching approval gate. The
catalog in :mod:`voicebridge.business.role_catalog` derives those gates from
the declared permissions, so adding a role cannot silently skip one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .errors import BusinessError

SCHEMA = "qubit-office-role-registry/1"

AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")
APPROVAL_CATEGORIES = ("spend", "legal", "publish", "contact")
DEFAULT_CURRENCY = "USD"

ROLE_BAD_INPUT_CODE = "BUSINESS_ROLE_BAD_INPUT"
ROLE_UNKNOWN_DEPARTMENT_CODE = "BUSINESS_ROLE_UNKNOWN_DEPARTMENT"
ROLE_DUPLICATE_ID_CODE = "BUSINESS_ROLE_DUPLICATE_ID"
ROLE_UNSAFE_PERMISSION_CODE = "BUSINESS_ROLE_UNSAFE_PERMISSION"
ROLE_MISSING_APPROVAL_CODE = "BUSINESS_ROLE_MISSING_APPROVAL"
ROLE_BAD_AUTONOMY_CODE = "BUSINESS_ROLE_BAD_AUTONOMY"
ROLE_BAD_FLOOR_CODE = "BUSINESS_ROLE_BAD_FLOOR"

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
_WILDCARD_MARKERS = ("*", "?")
_DESTRUCTIVE_PARTS = frozenset(
    {
        "admin",
        "all",
        "any",
        "delete",
        "destroy",
        "drop",
        "everything",
        "exec",
        "format",
        "manage_all",
        "raw_sql",
        "rm",
        "root",
        "shell",
        "sql",
        "sudo",
        "superuser",
        "terminal",
        "truncate",
        "unrestricted",
        "wipe",
        "write_all",
    }
)
_SPEND_DOMAIN = frozenset(
    {
        "billing",
        "charge",
        "invoice",
        "invoices",
        "payment",
        "payments",
        "payout",
        "payouts",
        "procurement",
        "purchase",
        "purchases",
        "refund",
        "refunds",
        "spend",
    }
)
_SPEND_ACTIONS = frozenset(
    {"buy", "charge", "commit", "create", "initiate", "issue", "pay", "send"}
)
_LEGAL_DOMAIN = frozenset(
    {
        "compliance",
        "contract",
        "contracts",
        "ip",
        "legal",
        "licence",
        "license",
        "policy",
        "privacy",
        "terms",
        "vendor",
    }
)
_PUBLISH_ACTION_PARTS = frozenset(
    {"announce", "publish", "post", "release", "upload"}
)
_PUBLISH_PREFIXES = (
    "ads.launch",
    "deploy.production",
    "deploy.release",
    "deploy.rollback",
    "listing.publish",
    "media.upload",
    "release.publish",
)
_BLOCKED_PUBLISH_PARTS = frozenset({"staging", "verify", "review"})
_CONTACT_ACTION_PARTS = frozenset(
    {"call", "contact", "dm", "message", "notify", "outreach", "reply", "send", "sms"}
)

_BUDGET_KEYS = frozenset(
    {"monthly_minor", "per_action_minor", "rate_limit_per_hour", "currency"}
)
_MODEL_KEYS = frozenset({"provider_hint", "model_hint"})
_ROLE_KEYS = frozenset(
    {
        "id",
        "department",
        "title",
        "mission",
        "responsibilities",
        "skills",
        "toolsets",
        "data_scopes",
        "permissions",
        "budget",
        "autonomy",
        "approvals",
        "schedule",
        "kpis",
        "escalation",
        "model",
        "sop_refs",
        "memory_refs",
        "human_owner",
        "service_lines",
        "active_by_default",
    }
)


class RoleRegistryError(BusinessError):
    """A typed refusal from the role registry."""


def _fail(code: str, detail: str) -> None:
    raise RoleRegistryError(code, detail)


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(ROLE_BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        _fail(
            ROLE_BAD_INPUT_CODE,
            f"{label} is longer than {max_len} characters",
        )
    return clean


def _identifier(value: Any, label: str) -> str:
    clean = _text(value, label, max_len=80)
    if not _ID_RE.match(clean):
        _fail(
            ROLE_BAD_INPUT_CODE,
            f"{label} must match ^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$",
        )
    return clean


def _tuple(value: Any, label: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        _fail(ROLE_BAD_INPUT_CODE, f"{label} must be a list or tuple")
    return tuple(value)


def _texts(
    value: Any, label: str, *, required: bool = False, unique: bool = True
) -> tuple[str, ...]:
    items: list[str] = []
    for index, item in enumerate(_tuple(value, label)):
        items.append(_text(item, f"{label}[{index}]", max_len=400))
    if unique:
        items = list(dict.fromkeys(items))
    if required and not items:
        _fail(ROLE_BAD_INPUT_CODE, f"{label} must not be empty")
    return tuple(items)


def _bool(value: Any, label: str, *, default: bool = True) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        _fail(ROLE_BAD_INPUT_CODE, f"{label} must be a bool")
    return value


def _check_keys(data: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        _fail(
            ROLE_BAD_INPUT_CODE,
            f"{label} has unknown field(s): {', '.join(unknown)}",
        )


def _parts(token: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", token.lower()) if part}


def unsafe_token(token: str) -> bool:
    """True when a permission/toolset is wildcard or destructive by default."""
    low = token.strip().lower()
    if not low:
        return True
    if any(marker in low for marker in _WILDCARD_MARKERS):
        return True
    return bool(_parts(low) & _DESTRUCTIVE_PARTS)


def required_approvals(
    permissions: Sequence[str], toolsets: Sequence[str] = ()
) -> frozenset[str]:
    """Approval categories implied by the actions a role may take."""
    required: set[str] = set()
    for token in (*permissions, *toolsets):
        low = token.strip().lower()
        parts = _parts(low)
        if (parts & _SPEND_DOMAIN and parts & _SPEND_ACTIONS) or low.startswith(
            ("ads.budget", "ads.launch", "ads.spend")
        ):
            required.add("spend")
        if parts & _LEGAL_DOMAIN:
            required.add("legal")
        if (
            parts & _PUBLISH_ACTION_PARTS
            and not parts & _BLOCKED_PUBLISH_PARTS
        ) or low.startswith(_PUBLISH_PREFIXES):
            required.add("publish")
        if parts & _CONTACT_ACTION_PARTS:
            required.add("contact")
    return frozenset(required)


def autonomy_rank(level: str) -> int:
    """Numeric rank for ``L0``–``L5`` (higher means more autonomy)."""
    try:
        return AUTONOMY_LEVELS.index(level)
    except ValueError:
        _fail(
            ROLE_BAD_AUTONOMY_CODE,
            f"autonomy must be one of {', '.join(AUTONOMY_LEVELS)}",
        )


def autonomy_at_least(level: str, minimum: str) -> bool:
    """True when ``level`` grants at least the ``minimum`` autonomy."""
    return autonomy_rank(level) >= autonomy_rank(minimum)


def _budget(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(ROLE_BAD_INPUT_CODE, "budget must be a mapping")
    _check_keys(value, _BUDGET_KEYS, "budget")
    out: dict[str, Any] = {}
    for key in ("monthly_minor", "per_action_minor", "rate_limit_per_hour"):
        item = value.get(key)
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or item < 0
        ):
            _fail(
                ROLE_BAD_INPUT_CODE,
                f"budget.{key} must be a non-negative integer",
            )
        out[key] = item
    monthly = out["monthly_minor"]
    per_action = out["per_action_minor"]
    if monthly <= 0:
        _fail(ROLE_BAD_INPUT_CODE, "budget.monthly_minor must be positive")
    if per_action <= 0:
        _fail(ROLE_BAD_INPUT_CODE, "budget.per_action_minor must be positive")
    if per_action > monthly:
        _fail(
            ROLE_BAD_INPUT_CODE,
            "budget.per_action_minor cannot exceed budget.monthly_minor",
        )
    if out["rate_limit_per_hour"] <= 0:
        _fail(
            ROLE_BAD_INPUT_CODE,
            "budget.rate_limit_per_hour must be positive",
        )
    currency = _text(
        value.get("currency", DEFAULT_CURRENCY), "budget.currency", max_len=3
    ).upper()
    if len(currency) != 3 or not currency.isalpha():
        _fail(ROLE_BAD_INPUT_CODE, "budget.currency must be a 3-letter code")
    out["currency"] = currency
    return out


def _model(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _fail(ROLE_BAD_INPUT_CODE, "model must be a mapping")
    _check_keys(value, _MODEL_KEYS, "model")
    return {
        "provider_hint": _text(value.get("provider_hint"), "model.provider_hint"),
        "model_hint": _text(value.get("model_hint"), "model.model_hint"),
    }


@dataclass(frozen=True, slots=True)
class Department:
    """A canonical division of the corporation."""

    id: str
    title: str
    description: str
    service_lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.id, "department.id")
        _text(self.title, "department.title", max_len=120)
        _text(self.description, "department.description", max_len=500)
        object.__setattr__(
            self,
            "service_lines",
            _texts(self.service_lines, "department.service_lines"),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "Department":
        if not isinstance(data, Mapping):
            _fail(ROLE_BAD_INPUT_CODE, "department must be a mapping")
        _check_keys(
            data,
            frozenset({"id", "title", "description", "service_lines"}),
            "department",
        )
        return cls(
            id=_identifier(data.get("id"), "department.id"),
            title=_text(data.get("title"), "department.title", max_len=120),
            description=_text(
                data.get("description"), "department.description", max_len=500
            ),
            service_lines=_texts(
                data.get("service_lines", ()), "department.service_lines"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "service_lines": list(self.service_lines),
        }


@dataclass(frozen=True, slots=True)
class RoomTemplate:
    """A room on a floor, with planned desks and assigned role ids."""

    room_id: str
    name: str
    kind: str
    desks: int
    role_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.room_id, "room.room_id")
        _text(self.name, "room.name", max_len=120)
        _text(self.kind, "room.kind", max_len=40)
        if not isinstance(self.desks, int) or isinstance(self.desks, bool):
            _fail(ROLE_BAD_FLOOR_CODE, "room.desks must be an integer")
        if self.desks <= 0 or self.desks > 1000:
            _fail(ROLE_BAD_FLOOR_CODE, "room.desks must be 1..1000")
        object.__setattr__(
            self,
            "role_ids",
            tuple(_identifier(item, "room.role_id") for item in self.role_ids),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "RoomTemplate":
        if not isinstance(data, Mapping):
            _fail(ROLE_BAD_FLOOR_CODE, "room must be a mapping")
        _check_keys(
            data, frozenset({"room_id", "name", "kind", "desks", "role_ids"}), "room"
        )
        return cls(
            room_id=_identifier(data.get("room_id"), "room.room_id"),
            name=_text(data.get("name"), "room.name", max_len=120),
            kind=_text(data.get("kind"), "room.kind", max_len=40),
            desks=data.get("desks"),
            role_ids=tuple(
                _identifier(item, "room.role_id")
                for item in _tuple(data.get("role_ids", ()), "room.role_ids")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "name": self.name,
            "kind": self.kind,
            "desks": self.desks,
            "role_ids": list(self.role_ids),
        }


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    """One employee template. Validation is part of construction."""

    id: str
    department: str
    title: str
    mission: str
    responsibilities: tuple[str, ...]
    skills: tuple[str, ...]
    toolsets: tuple[str, ...]
    data_scopes: tuple[str, ...]
    permissions: tuple[str, ...]
    budget: Mapping[str, Any]
    autonomy: str
    approvals: tuple[str, ...]
    schedule: str
    kpis: tuple[str, ...]
    escalation: str
    model: Mapping[str, str]
    sop_refs: tuple[str, ...]
    memory_refs: tuple[str, ...]
    human_owner: str
    service_lines: tuple[str, ...] = ()
    active_by_default: bool = True

    def __post_init__(self) -> None:
        _identifier(self.id, "role.id")
        _identifier(self.department, "role.department")
        _text(self.title, "role.title", max_len=120)
        _text(self.mission, "role.mission", max_len=600)
        object.__setattr__(
            self,
            "responsibilities",
            _texts(self.responsibilities, "role.responsibilities", required=True),
        )
        object.__setattr__(
            self, "skills", _texts(self.skills, "role.skills", required=True)
        )
        object.__setattr__(
            self, "toolsets", _texts(self.toolsets, "role.toolsets", required=True)
        )
        object.__setattr__(
            self,
            "data_scopes",
            _texts(self.data_scopes, "role.data_scopes", required=True),
        )
        object.__setattr__(
            self,
            "permissions",
            _texts(self.permissions, "role.permissions", required=True),
        )
        object.__setattr__(self, "kpis", _texts(self.kpis, "role.kpis", required=True))
        object.__setattr__(
            self, "sop_refs", _texts(self.sop_refs, "role.sop_refs", required=True)
        )
        object.__setattr__(
            self,
            "memory_refs",
            _texts(self.memory_refs, "role.memory_refs", required=True),
        )
        object.__setattr__(
            self, "service_lines", _texts(self.service_lines, "role.service_lines")
        )
        object.__setattr__(
            self, "approvals", tuple(dict.fromkeys(self.approvals))
        )
        object.__setattr__(self, "budget", _budget(self.budget))
        object.__setattr__(self, "model", _model(self.model))
        _text(self.schedule, "role.schedule", max_len=120)
        _text(self.escalation, "role.escalation", max_len=600)
        _text(self.human_owner, "role.human_owner", max_len=200)
        _bool(self.active_by_default, "role.active_by_default")

        if self.autonomy not in AUTONOMY_LEVELS:
            _fail(
                ROLE_BAD_AUTONOMY_CODE,
                f"role {self.id!r} autonomy must be one of"
                f" {', '.join(AUTONOMY_LEVELS)}",
            )

        for label, token in (
            *(("permission", item) for item in self.permissions),
            *(("toolset", item) for item in self.toolsets),
            *(("data scope", item) for item in self.data_scopes),
        ):
            if unsafe_token(token):
                _fail(
                    ROLE_UNSAFE_PERMISSION_CODE,
                    f"role {self.id!r} declares unsafe {label} {token!r};"
                    " wildcard and destructive grants are refused by default",
                )

        approvals = tuple(dict.fromkeys(self.approvals))
        for category in approvals:
            if category not in APPROVAL_CATEGORIES:
                _fail(
                    ROLE_BAD_INPUT_CODE,
                    f"role {self.id!r} approval {category!r} must be one of"
                    f" {', '.join(APPROVAL_CATEGORIES)}",
                )
        required = required_approvals(self.permissions, self.toolsets)
        missing = sorted(required - set(approvals))
        if missing:
            _fail(
                ROLE_MISSING_APPROVAL_CODE,
                f"role {self.id!r} needs approval gate(s) {', '.join(missing)}"
                " for its declared spend/legal/publish/contact actions",
            )
        if self.autonomy in {"L4", "L5"} and not approvals:
            _fail(
                ROLE_MISSING_APPROVAL_CODE,
                f"role {self.id!r} autonomy {self.autonomy} requires at least"
                " one human approval gate",
            )

        if "spend" in required and self.budget["per_action_minor"] <= 0:
            _fail(
                ROLE_BAD_INPUT_CODE,
                f"role {self.id!r} can spend but has no per-action budget",
            )

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Every capability token a lookup/filter can match."""
        return tuple(sorted(set(self.permissions) | set(self.toolsets) | set(self.skills)))

    @property
    def autonomy_rank(self) -> int:
        return autonomy_rank(self.autonomy)

    def requires_approval(self, category: str) -> bool:
        return category in self.approvals

    def matches_capability(self, query: str) -> bool:
        needle = query.strip().lower()
        if not needle:
            return False
        for token in (*self.permissions, *self.toolsets, *self.skills, *self.service_lines):
            low = token.lower()
            if low == needle or low.startswith(needle + ".") or needle.startswith(
                low + "."
            ):
                return True
        return False

    @classmethod
    def from_dict(cls, data: Any) -> "RoleTemplate":
        if not isinstance(data, Mapping):
            _fail(ROLE_BAD_INPUT_CODE, "role must be a mapping")
        _check_keys(data, _ROLE_KEYS, "role")
        return cls(
            id=_identifier(data.get("id"), "role.id"),
            department=_identifier(data.get("department"), "role.department"),
            title=_text(data.get("title"), "role.title", max_len=120),
            mission=_text(data.get("mission"), "role.mission", max_len=600),
            responsibilities=_texts(
                data.get("responsibilities"),
                "role.responsibilities",
                required=True,
            ),
            skills=_texts(data.get("skills"), "role.skills", required=True),
            toolsets=_texts(data.get("toolsets"), "role.toolsets", required=True),
            data_scopes=_texts(
                data.get("data_scopes"), "role.data_scopes", required=True
            ),
            permissions=_texts(
                data.get("permissions"), "role.permissions", required=True
            ),
            budget=_budget(data.get("budget")),
            autonomy=_text(data.get("autonomy"), "role.autonomy", max_len=2),
            approvals=_texts(data.get("approvals", ()), "role.approvals"),
            schedule=_text(data.get("schedule"), "role.schedule", max_len=120),
            kpis=_texts(data.get("kpis"), "role.kpis", required=True),
            escalation=_text(data.get("escalation"), "role.escalation", max_len=600),
            model=_model(data.get("model")),
            sop_refs=_texts(data.get("sop_refs"), "role.sop_refs", required=True),
            memory_refs=_texts(
                data.get("memory_refs"), "role.memory_refs", required=True
            ),
            human_owner=_text(
                data.get("human_owner"), "role.human_owner", max_len=200
            ),
            service_lines=_texts(data.get("service_lines", ()), "role.service_lines"),
            active_by_default=_bool(
                data.get("active_by_default"), "role.active_by_default"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "department": self.department,
            "title": self.title,
            "mission": self.mission,
            "responsibilities": list(self.responsibilities),
            "skills": list(self.skills),
            "toolsets": list(self.toolsets),
            "data_scopes": list(self.data_scopes),
            "permissions": list(self.permissions),
            "budget": dict(self.budget),
            "autonomy": self.autonomy,
            "approvals": list(self.approvals),
            "schedule": self.schedule,
            "kpis": list(self.kpis),
            "escalation": self.escalation,
            "model": dict(self.model),
            "sop_refs": list(self.sop_refs),
            "memory_refs": list(self.memory_refs),
            "human_owner": self.human_owner,
            "service_lines": list(self.service_lines),
            "active_by_default": self.active_by_default,
        }


@dataclass(frozen=True, slots=True)
class FloorTemplate:
    """One floor/wing grouping. No hard cap on rooms, desks or agents."""

    floor_id: str
    index: int
    wing: str
    name: str
    department_ids: tuple[str, ...]
    rooms: tuple[RoomTemplate, ...]
    role_ids: tuple[str, ...]
    active_by_default: bool
    desk_capacity: int
    max_rooms: int | None = None
    max_agents: int | None = None
    expansion_allowed: bool = True

    def __post_init__(self) -> None:
        _identifier(self.floor_id, "floor.floor_id")
        if not isinstance(self.index, int) or isinstance(self.index, bool):
            _fail(ROLE_BAD_FLOOR_CODE, "floor.index must be an integer")
        if self.index <= 0:
            _fail(ROLE_BAD_FLOOR_CODE, "floor.index must be positive")
        _text(self.wing, "floor.wing", max_len=60)
        _text(self.name, "floor.name", max_len=160)
        if not self.department_ids:
            _fail(ROLE_BAD_FLOOR_CODE, "floor.department_ids must not be empty")
        object.__setattr__(
            self,
            "department_ids",
            tuple(
                _identifier(item, "floor.department_id")
                for item in self.department_ids
            ),
        )
        if not self.role_ids:
            _fail(ROLE_BAD_FLOOR_CODE, "floor.role_ids must not be empty")
        object.__setattr__(
            self,
            "role_ids",
            tuple(_identifier(item, "floor.role_id") for item in self.role_ids),
        )
        if not self.rooms:
            _fail(ROLE_BAD_FLOOR_CODE, "floor.rooms must not be empty")
        object.__setattr__(
            self,
            "rooms",
            tuple(
                room
                if isinstance(room, RoomTemplate)
                else _fail(
                    ROLE_BAD_FLOOR_CODE, "floor.rooms must contain RoomTemplate"
                )
                for room in self.rooms
            ),
        )
        for room in self.rooms:
            for role_id in room.role_ids:
                if role_id not in self.role_ids:
                    _fail(
                        ROLE_BAD_FLOOR_CODE,
                        f"room {room.room_id!r} assigns {role_id!r}, which is"
                        f" not on floor {self.floor_id!r}",
                    )
        _bool(self.active_by_default, "floor.active_by_default")
        if not isinstance(self.desk_capacity, int) or isinstance(
            self.desk_capacity, bool
        ):
            _fail(ROLE_BAD_FLOOR_CODE, "floor.desk_capacity must be an integer")
        expected = sum(room.desks for room in self.rooms)
        if self.desk_capacity != expected:
            _fail(
                ROLE_BAD_FLOOR_CODE,
                "floor.desk_capacity must equal the sum of its room desks"
                f" ({expected})",
            )
        for label, value in (
            ("max_rooms", self.max_rooms),
            ("max_agents", self.max_agents),
        ):
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                _fail(
                    ROLE_BAD_FLOOR_CODE,
                    f"floor.{label} must be None (no cap) or a positive integer",
                )
        _bool(self.expansion_allowed, "floor.expansion_allowed")

    @classmethod
    def from_dict(cls, data: Any) -> "FloorTemplate":
        if not isinstance(data, Mapping):
            _fail(ROLE_BAD_FLOOR_CODE, "floor must be a mapping")
        _check_keys(
            data,
            frozenset(
                {
                    "floor_id",
                    "index",
                    "wing",
                    "name",
                    "department_ids",
                    "rooms",
                    "role_ids",
                    "active_by_default",
                    "desk_capacity",
                    "max_rooms",
                    "max_agents",
                    "expansion_allowed",
                }
            ),
            "floor",
        )
        rooms = data.get("rooms")
        if not isinstance(rooms, (list, tuple)):
            _fail(ROLE_BAD_FLOOR_CODE, "floor.rooms must be a list")
        return cls(
            floor_id=_identifier(data.get("floor_id"), "floor.floor_id"),
            index=data.get("index"),
            wing=_text(data.get("wing"), "floor.wing", max_len=60),
            name=_text(data.get("name"), "floor.name", max_len=160),
            department_ids=tuple(
                _identifier(item, "floor.department_id")
                for item in _tuple(data.get("department_ids"), "floor.department_ids")
            ),
            rooms=tuple(RoomTemplate.from_dict(room) for room in rooms),
            role_ids=tuple(
                _identifier(item, "floor.role_id")
                for item in _tuple(data.get("role_ids"), "floor.role_ids")
            ),
            active_by_default=_bool(
                data.get("active_by_default"), "floor.active_by_default"
            ),
            desk_capacity=data.get("desk_capacity"),
            max_rooms=data.get("max_rooms"),
            max_agents=data.get("max_agents"),
            expansion_allowed=_bool(
                data.get("expansion_allowed"), "floor.expansion_allowed"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "floor_id": self.floor_id,
            "index": self.index,
            "wing": self.wing,
            "name": self.name,
            "department_ids": list(self.department_ids),
            "rooms": [room.to_dict() for room in self.rooms],
            "role_ids": list(self.role_ids),
            "active_by_default": self.active_by_default,
            "desk_capacity": self.desk_capacity,
            "max_rooms": self.max_rooms,
            "max_agents": self.max_agents,
            "expansion_allowed": self.expansion_allowed,
        }


class RoleRegistry:
    """Validated, queryable role/department/floor catalog."""

    def __init__(
        self,
        departments: Sequence[Department],
        roles: Sequence[RoleTemplate],
        floors: Sequence[FloorTemplate] = (),
    ) -> None:
        self._departments = tuple(departments)
        self._roles = tuple(roles)
        self._floors = tuple(sorted(floors, key=lambda floor: floor.index))
        self._validate()
        self._by_department = {item.id: item for item in self._departments}
        self._by_role = {item.id: item for item in self._roles}
        self._by_floor = {item.floor_id: item for item in self._floors}

    def _validate(self) -> None:
        if not self._departments:
            _fail(ROLE_BAD_INPUT_CODE, "registry needs at least one department")
        if not self._roles:
            _fail(ROLE_BAD_INPUT_CODE, "registry needs at least one role")

        dept_ids: set[str] = set()
        for dept in self._departments:
            if not isinstance(dept, Department):
                _fail(ROLE_BAD_INPUT_CODE, "departments must be Department values")
            if dept.id in dept_ids:
                _fail(
                    ROLE_DUPLICATE_ID_CODE,
                    f"duplicate department id {dept.id!r}",
                )
            dept_ids.add(dept.id)

        role_ids: set[str] = set()
        represented: set[str] = set()
        for role in self._roles:
            if not isinstance(role, RoleTemplate):
                _fail(ROLE_BAD_INPUT_CODE, "roles must be RoleTemplate values")
            if role.id in role_ids:
                _fail(
                    ROLE_DUPLICATE_ID_CODE,
                    f"duplicate role id {role.id!r}",
                )
            role_ids.add(role.id)
            if role.department not in dept_ids:
                _fail(
                    ROLE_UNKNOWN_DEPARTMENT_CODE,
                    f"role {role.id!r} names unknown department"
                    f" {role.department!r}",
                )
            represented.add(role.department)
        missing = sorted(dept_ids - represented)
        if missing:
            _fail(
                ROLE_BAD_INPUT_CODE,
                f"department(s) {', '.join(missing)} have no role template",
            )

        floor_ids: set[str] = set()
        indexes: set[int] = set()
        for floor in self._floors:
            if not isinstance(floor, FloorTemplate):
                _fail(ROLE_BAD_FLOOR_CODE, "floors must be FloorTemplate values")
            if floor.floor_id in floor_ids:
                _fail(
                    ROLE_DUPLICATE_ID_CODE,
                    f"duplicate floor id {floor.floor_id!r}",
                )
            if floor.index in indexes:
                _fail(
                    ROLE_DUPLICATE_ID_CODE,
                    f"duplicate floor index {floor.index}",
                )
            floor_ids.add(floor.floor_id)
            indexes.add(floor.index)
            for dept_id in floor.department_ids:
                if dept_id not in dept_ids:
                    _fail(
                        ROLE_UNKNOWN_DEPARTMENT_CODE,
                        f"floor {floor.floor_id!r} names unknown department"
                        f" {dept_id!r}",
                    )
            for role_id in floor.role_ids:
                if role_id not in role_ids:
                    _fail(
                        ROLE_BAD_FLOOR_CODE,
                        f"floor {floor.floor_id!r} assigns unknown role"
                        f" {role_id!r}",
                    )

    # -- departments ----------------------------------------------------
    @property
    def departments(self) -> tuple[Department, ...]:
        return self._departments

    def get_department(self, department_id: str) -> Department:
        try:
            return self._by_department[department_id]
        except KeyError:
            _fail(
                ROLE_UNKNOWN_DEPARTMENT_CODE,
                f"unknown department {department_id!r}",
            )
            raise AssertionError("unreachable")

    # -- roles ----------------------------------------------------------
    @property
    def roles(self) -> tuple[RoleTemplate, ...]:
        return self._roles

    def get_role(self, role_id: str) -> RoleTemplate:
        try:
            return self._by_role[role_id]
        except KeyError:
            _fail(ROLE_BAD_INPUT_CODE, f"unknown role {role_id!r}")
            raise AssertionError("unreachable")

    def filter_roles(
        self,
        *,
        department: str | None = None,
        capability: str | None = None,
        autonomy: str | None = None,
        autonomy_min: str | None = None,
        autonomy_max: str | None = None,
        service_line: str | None = None,
        active_only: bool = False,
    ) -> tuple[RoleTemplate, ...]:
        if department is not None and department not in self._by_department:
            _fail(
                ROLE_UNKNOWN_DEPARTMENT_CODE,
                f"unknown department {department!r}",
            )
        exact = autonomy_rank(autonomy) if autonomy is not None else None
        low = autonomy_rank(autonomy_min) if autonomy_min is not None else None
        high = autonomy_rank(autonomy_max) if autonomy_max is not None else None
        if low is not None and high is not None and low > high:
            _fail(
                ROLE_BAD_AUTONOMY_CODE,
                "autonomy_min cannot be higher than autonomy_max",
            )
        line = service_line.strip().lower() if service_line else None
        out: list[RoleTemplate] = []
        for role in self._roles:
            if department is not None and role.department != department:
                continue
            if active_only and not role.active_by_default:
                continue
            if capability is not None and not role.matches_capability(capability):
                continue
            if exact is not None and role.autonomy_rank != exact:
                continue
            if low is not None and role.autonomy_rank < low:
                continue
            if high is not None and role.autonomy_rank > high:
                continue
            if line is not None and line not in {
                item.lower() for item in role.service_lines
            }:
                continue
            out.append(role)
        return tuple(out)

    # -- floors ---------------------------------------------------------
    @property
    def floors(self) -> tuple[FloorTemplate, ...]:
        return self._floors

    def get_floor(self, floor_id: str) -> FloorTemplate:
        try:
            return self._by_floor[floor_id]
        except KeyError:
            _fail(ROLE_BAD_FLOOR_CODE, f"unknown floor {floor_id!r}")
            raise AssertionError("unreachable")

    @property
    def active_floors(self) -> tuple[FloorTemplate, ...]:
        return tuple(floor for floor in self._floors if floor.active_by_default)

    @property
    def dormant_floors(self) -> tuple[FloorTemplate, ...]:
        return tuple(floor for floor in self._floors if not floor.active_by_default)

    def filter_floors(
        self,
        *,
        wing: str | None = None,
        department: str | None = None,
        active: bool | None = None,
    ) -> tuple[FloorTemplate, ...]:
        if department is not None and department not in self._by_department:
            _fail(
                ROLE_UNKNOWN_DEPARTMENT_CODE,
                f"unknown department {department!r}",
            )
        wing_clean = wing.strip().lower() if wing else None
        out: list[FloorTemplate] = []
        for floor in self._floors:
            if wing_clean is not None and floor.wing.lower() != wing_clean:
                continue
            if department is not None and department not in floor.department_ids:
                continue
            if active is not None and floor.active_by_default is not active:
                continue
            out.append(floor)
        return tuple(out)

    def materialized_role_ids(self, *, active_only: bool = True) -> tuple[str, ...]:
        """Role ids assigned to floors that would materialize today."""
        floors = self.active_floors if active_only else self._floors
        ids: set[str] = set()
        for floor in floors:
            ids.update(floor.role_ids)
        return tuple(sorted(ids))

    # -- serialization --------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "departments": [item.to_dict() for item in self._departments],
            "roles": [item.to_dict() for item in self._roles],
            "floors": [item.to_dict() for item in self._floors],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RoleRegistry":
        if not isinstance(data, Mapping):
            _fail(ROLE_BAD_INPUT_CODE, "registry must be a mapping")
        _check_keys(
            data, frozenset({"schema", "departments", "roles", "floors"}), "registry"
        )
        schema = data.get("schema", SCHEMA)
        if schema != SCHEMA:
            _fail(
                ROLE_BAD_INPUT_CODE,
                f"registry schema must be {SCHEMA!r}, got {schema!r}",
            )
        departments = data.get("departments")
        roles = data.get("roles")
        floors = data.get("floors", ())
        if not isinstance(departments, (list, tuple)):
            _fail(ROLE_BAD_INPUT_CODE, "registry.departments must be a list")
        if not isinstance(roles, (list, tuple)):
            _fail(ROLE_BAD_INPUT_CODE, "registry.roles must be a list")
        if not isinstance(floors, (list, tuple)):
            _fail(ROLE_BAD_INPUT_CODE, "registry.floors must be a list")
        return cls(
            departments=tuple(Department.from_dict(item) for item in departments),
            roles=tuple(RoleTemplate.from_dict(item) for item in roles),
            floors=tuple(FloorTemplate.from_dict(item) for item in floors),
        )

    @classmethod
    def default(cls) -> "RoleRegistry":
        """The shipped catalog (100+ roles, 100 floor/wing templates)."""
        from . import role_catalog

        return role_catalog.build_registry()
