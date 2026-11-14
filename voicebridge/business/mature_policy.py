"""P7-03 — mature-content policy for the Office simulation.

Mature content is **off by default**. The owner opts a department in, category
by category, and may narrow it further per agent. Every mature action then
passes consent, age, privacy and HR checks before it is allowed, and every
allowed *or refused* mature event is audited.

Boundaries this module enforces:

* Sexual content is non-graphic and fade-to-black; explicit flags are refused.
* Substance use is simulated with consequences and labelled as such; dosing,
  sourcing and how-to detail are typed refusals.
* Fighting/conflict is non-graphic, with no gore.
* Only fictional agents 18+ participate; missing ages are refused.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .errors import BAD_INPUT_CODE, BusinessError
from .ids import new_id
from .util import now_iso

CATEGORY_ROMANCE = "romance"
CATEGORY_SUBSTANCES = "substances"
CATEGORY_FIGHTING = "fighting"
CATEGORY_CONFLICT = "conflict"
MATURE_CATEGORIES = (
    CATEGORY_ROMANCE,
    CATEGORY_SUBSTANCES,
    CATEGORY_FIGHTING,
    CATEGORY_CONFLICT,
)

ACTION_CATEGORIES: dict[str, str] = {
    "flirt": CATEGORY_ROMANCE,
    "ask_out": CATEGORY_ROMANCE,
    "date": CATEGORY_ROMANCE,
    "hold_hands": CATEGORY_ROMANCE,
    "kiss": CATEGORY_ROMANCE,
    "private_encounter": CATEGORY_ROMANCE,
    "alcohol": CATEGORY_SUBSTANCES,
    "substance_use": CATEGORY_SUBSTANCES,
    "smoke": CATEGORY_SUBSTANCES,
    "vape": CATEGORY_SUBSTANCES,
    "shove": CATEGORY_FIGHTING,
    "fight": CATEGORY_FIGHTING,
    "argument": CATEGORY_CONFLICT,
    "insult": CATEGORY_CONFLICT,
    "gossip": CATEGORY_CONFLICT,
    "report_to_hr": CATEGORY_CONFLICT,
}

LABEL_ROMANCE = "non-graphic-romance"
LABEL_FADE_TO_BLACK = "non-graphic-fade-to-black"
LABEL_SUBSTANCE = "substance-use-simulated"
LABEL_CONFLICT = "non-graphic-conflict"

PRESENTATION_FADE = "fade-to-black"
PRESENTATION_NON_GRAPHIC = "non-graphic"
PRESENTATION_SIMULATED = "simulated-consequence"

MATURE_DISABLED_CODE = "OFFICE_MATURE_DISABLED"
MATURE_DEPARTMENT_DISABLED_CODE = "OFFICE_MATURE_DEPARTMENT_DISABLED"
MATURE_CATEGORY_DISABLED_CODE = "OFFICE_MATURE_CATEGORY_DISABLED"
MATURE_ACTION_UNKNOWN_CODE = "OFFICE_MATURE_ACTION_UNKNOWN"
MATURE_OWNER_REQUIRED_CODE = "OFFICE_MATURE_OWNER_REQUIRED"
MATURE_CONSENT_REQUIRED_CODE = "OFFICE_MATURE_CONSENT_REQUIRED"
MATURE_AGE_UNVERIFIED_CODE = "OFFICE_MATURE_AGE_UNVERIFIED"
MATURE_UNDERAGE_CODE = "OFFICE_MATURE_UNDERAGE"
MATURE_PRIVACY_REQUIRED_CODE = "OFFICE_MATURE_PRIVACY_REQUIRED"
MATURE_HR_REQUIRED_CODE = "OFFICE_MATURE_HR_REQUIRED"
MATURE_HOWTO_REFUSED_CODE = "OFFICE_MATURE_HOWTO_REFUSED"
MATURE_GRAPHIC_REFUSED_CODE = "OFFICE_MATURE_GRAPHIC_REFUSED"

MINIMUM_AGE = 18

_HOWTO_KEYS = frozenset(
    {
        "dose",
        "dosage",
        "dosing",
        "mg",
        "ml",
        "grams",
        "source",
        "sourcing",
        "vendor",
        "dealer",
        "street_price",
        "street_name",
        "how_to",
        "instructions",
        "recipe",
        "synthesis",
        "inject",
    }
)
_HOWTO_PATTERNS = (
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|grams?|doses?)\b", re.I),
    re.compile(r"\bhow\s+to\s+(?:make|cook|synthesi[sz]e|inject|use)\b", re.I),
    re.compile(r"\bwhere\s+to\s+(?:buy|get|source)\b", re.I),
    re.compile(r"\bstreet\s+(?:price|name)s?\b", re.I),
)
_GRAPHIC_FLAGS = frozenset({"explicit", "graphic", "gore", "nudity"})
_GRAPHIC_PATTERN = re.compile(
    r"\b(?:explicit\s+(?:sex|depiction)|graphic\s+(?:sex|violence)|gore)\b",
    re.I,
)


def _text(value: Any, label: str, *, max_len: int = 200) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise BusinessError(BAD_INPUT_CODE, f"{label} is required")
    if len(raw) > max_len:
        raise BusinessError(BAD_INPUT_CODE, f"{label} is too long")
    return raw


@dataclass(frozen=True, slots=True)
class MatureRequest:
    action: str
    department: str
    participants: tuple[str, ...]
    actor: str
    ages: Mapping[str, int] = field(default_factory=dict)
    consents: frozenset[str] = frozenset()
    privacy: str = "public"
    hr_acknowledged: bool = False
    agent_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MatureAuthorization:
    allowed: bool
    action: str
    category: str
    department: str
    label: str
    presentation: str
    hr_review_required: bool
    support_available: bool
    audit_id: str
    reasons: tuple[str, ...] = ()


class MatureContentPolicy:
    """Owner-controlled, department-scoped mature-content policy."""

    def __init__(
        self,
        *,
        owner_id: str | None = None,
        clock: Callable[[], float] | None = None,
        audit_sink: Callable[[Mapping[str, Any]], Any] | None = None,
        audit_limit: int = 512,
    ) -> None:
        self._owner_id = _text(owner_id, "owner_id") if owner_id else None
        self._clock = clock or time.time
        self._audit_sink = audit_sink
        self._audit: deque[dict[str, Any]] = deque(maxlen=max(1, audit_limit))
        self._enabled = False
        self._departments: dict[str, dict[str, Any]] = {}
        self._agent_overrides: dict[tuple[str, str], frozenset[str]] = {}

    # -- owner controls ----------------------------------------------
    @property
    def owner_id(self) -> str | None:
        return self._owner_id

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _require_owner(self, actor: str) -> str:
        actor_id = _text(actor, "actor")
        if self._owner_id is None or actor_id != self._owner_id:
            raise BusinessError(
                MATURE_OWNER_REQUIRED_CODE,
                "only the owner may change the mature-content policy",
            )
        return actor_id

    def enable_globally(self, *, actor: str) -> dict[str, Any]:
        actor_id = self._require_owner(actor)
        self._enabled = True
        return {"enabled": True, "actor": actor_id, "departments": sorted(
            self._departments
        )}

    def disable_all(self, *, actor: str) -> dict[str, Any]:
        actor_id = self._require_owner(actor)
        self._enabled = False
        return {"enabled": False, "actor": actor_id}

    def enable_department(
        self,
        *,
        actor: str,
        department: str,
        categories: tuple[str, ...] | list[str] = MATURE_CATEGORIES,
    ) -> dict[str, Any]:
        actor_id = self._require_owner(actor)
        dept = _text(department, "department")
        selected = frozenset(_text(item, "category") for item in categories)
        if not selected:
            raise BusinessError(
                BAD_INPUT_CODE, "at least one mature category is required"
            )
        unknown = sorted(selected - set(MATURE_CATEGORIES))
        if unknown:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown mature category: {', '.join(unknown)}",
            )
        self._enabled = True
        self._departments[dept] = {
            "categories": selected,
            "approved_by": actor_id,
            "enabled_at": now_iso(float(self._clock())),
        }
        return {
            "department": dept,
            "categories": sorted(selected),
            "approved_by": actor_id,
        }

    def disable_department(self, *, actor: str, department: str) -> dict[str, Any]:
        actor_id = self._require_owner(actor)
        dept = _text(department, "department")
        self._departments.pop(dept, None)
        for key in [key for key in self._agent_overrides if key[0] == dept]:
            del self._agent_overrides[key]
        return {"department": dept, "enabled": False, "actor": actor_id}

    def set_agent_override(
        self,
        *,
        actor: str,
        department: str,
        agent_id: str,
        categories: tuple[str, ...] | list[str],
    ) -> dict[str, Any]:
        actor_id = self._require_owner(actor)
        dept = _text(department, "department")
        agent = _text(agent_id, "agent_id")
        record = self._departments.get(dept)
        if record is None:
            raise BusinessError(
                MATURE_DEPARTMENT_DISABLED_CODE,
                f"department {dept!r} has not opted in to mature content",
            )
        selected = frozenset(_text(item, "category") for item in categories)
        unknown = sorted(selected - set(MATURE_CATEGORIES))
        if unknown:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown mature category: {', '.join(unknown)}",
            )
        if not selected.issubset(record["categories"]):
            raise BusinessError(
                MATURE_CATEGORY_DISABLED_CODE,
                "an agent override cannot exceed the department opt-in",
            )
        self._agent_overrides[(dept, agent)] = selected
        return {
            "department": dept,
            "agent_id": agent,
            "categories": sorted(selected),
            "approved_by": actor_id,
        }

    def status(self) -> dict[str, Any]:
        return {
            "owner_id": self._owner_id,
            "enabled": self._enabled,
            "departments": {
                name: {
                    "categories": sorted(record["categories"]),
                    "approved_by": record["approved_by"],
                    "enabled_at": record["enabled_at"],
                }
                for name, record in sorted(self._departments.items())
            },
            "agent_overrides": {
                f"{dept}:{agent}": sorted(categories)
                for (dept, agent), categories in sorted(
                    self._agent_overrides.items()
                )
            },
        }

    def audit_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._audit)

    # -- authorization ------------------------------------------------
    def category_for(self, action: str) -> str | None:
        return ACTION_CATEGORIES.get(str(action))

    def is_mature(self, action: str) -> bool:
        return str(action) in ACTION_CATEGORIES

    def authorize(self, request: MatureRequest) -> MatureAuthorization:
        moment = float(self._clock())
        action = _text(request.action, "action")
        department = _text(request.department, "department")
        actor = _text(request.actor, "actor")
        category = ACTION_CATEGORIES.get(action)
        participants = tuple(dict.fromkeys(_text(p, "participant")
                                           for p in request.participants))

        def refuse(code: str, detail: str) -> None:
            self._record(
                request,
                category=category,
                decision="refused",
                reason_code=code,
                label=None,
                presentation=None,
                moment=moment,
            )
            raise BusinessError(code, detail)

        if category is None:
            refuse(
                MATURE_ACTION_UNKNOWN_CODE,
                f"{action!r} is not a declared mature action",
            )
        if not self._enabled:
            refuse(
                MATURE_DISABLED_CODE,
                "mature content is off; the owner must opt in first",
            )
        record = self._departments.get(department)
        if record is None:
            refuse(
                MATURE_DEPARTMENT_DISABLED_CODE,
                f"department {department!r} has not opted in to mature content",
            )
        allowed_categories: frozenset[str] = record["categories"]
        if request.agent_id:
            override = self._agent_overrides.get(
                (department, str(request.agent_id))
            )
            if override is not None:
                allowed_categories = allowed_categories & override
        if category not in allowed_categories:
            refuse(
                MATURE_CATEGORY_DISABLED_CODE,
                f"category {category!r} is not enabled for this department",
            )
        if not participants:
            refuse(BAD_INPUT_CODE, "a mature action needs named participants")
        if actor not in participants:
            refuse(
                MATURE_CONSENT_REQUIRED_CODE,
                "the actor must be one of the participants",
            )
        if category == CATEGORY_ROMANCE and len(participants) < 2:
            refuse(
                BAD_INPUT_CODE, "a romance action needs at least two participants"
            )

        missing_ages = [
            participant
            for participant in participants
            if participant not in request.ages
        ]
        if missing_ages and category in (
            CATEGORY_ROMANCE,
            CATEGORY_SUBSTANCES,
            CATEGORY_FIGHTING,
        ):
            refuse(
                MATURE_AGE_UNVERIFIED_CODE,
                "age is unverified for: " + ", ".join(missing_ages),
            )
        for participant in participants:
            age = request.ages.get(participant)
            if age is None:
                continue
            if isinstance(age, bool) or not isinstance(age, int):
                refuse(
                    MATURE_AGE_UNVERIFIED_CODE,
                    f"age for {participant!r} must be an integer",
                )
            if age < MINIMUM_AGE:
                refuse(
                    MATURE_UNDERAGE_CODE,
                    "only adult fictional agents may take part in mature actions",
                )

        if self._contains_howto(request.detail):
            refuse(
                MATURE_HOWTO_REFUSED_CODE,
                "real-world dosing, sourcing or how-to detail is not simulated",
            )
        graphic = self._graphic_reason(request.detail)
        if graphic:
            refuse(MATURE_GRAPHIC_REFUSED_CODE, graphic)

        if category in (
            CATEGORY_ROMANCE,
            CATEGORY_SUBSTANCES,
            CATEGORY_FIGHTING,
        ):
            missing = [
                participant
                for participant in participants
                if participant not in request.consents
            ]
            if missing:
                refuse(
                    MATURE_CONSENT_REQUIRED_CODE,
                    "consent is missing from: " + ", ".join(missing),
                )
        if action == "private_encounter" and request.privacy != "private":
            refuse(
                MATURE_PRIVACY_REQUIRED_CODE,
                "a private encounter needs a private room",
            )
        if category in (CATEGORY_ROMANCE, CATEGORY_FIGHTING) and (
            not request.hr_acknowledged
        ):
            refuse(
                MATURE_HR_REQUIRED_CODE,
                "the workplace HR acknowledgement is required first",
            )

        label, presentation = self._label_for(action, category)
        hr_review_required = category == CATEGORY_CONFLICT
        support_available = category in (
            CATEGORY_SUBSTANCES,
            CATEGORY_FIGHTING,
            CATEGORY_CONFLICT,
        )
        audit = self._record(
            request,
            category=category,
            decision="allowed",
            reason_code=None,
            label=label,
            presentation=presentation,
            moment=moment,
        )
        return MatureAuthorization(
            allowed=True,
            action=action,
            category=category,
            department=department,
            label=label,
            presentation=presentation,
            hr_review_required=hr_review_required,
            support_available=support_available,
            audit_id=audit["audit_id"],
            reasons=(),
        )

    # -- internals ----------------------------------------------------
    def _label_for(self, action: str, category: str) -> tuple[str, str]:
        if category == CATEGORY_ROMANCE:
            if action == "private_encounter":
                return LABEL_FADE_TO_BLACK, PRESENTATION_FADE
            return LABEL_ROMANCE, PRESENTATION_NON_GRAPHIC
        if category == CATEGORY_SUBSTANCES:
            return LABEL_SUBSTANCE, PRESENTATION_SIMULATED
        return LABEL_CONFLICT, PRESENTATION_NON_GRAPHIC

    def _record(
        self,
        request: MatureRequest,
        *,
        category: str | None,
        decision: str,
        reason_code: str | None,
        label: str | None,
        presentation: str | None,
        moment: float,
    ) -> dict[str, Any]:
        record = {
            "audit_id": new_id("mataud"),
            "action": str(request.action),
            "category": category,
            "department": str(request.department),
            "agent_id": request.agent_id,
            "actor": str(request.actor),
            "participants": list(request.participants),
            "decision": decision,
            "reason_code": reason_code,
            "label": label,
            "presentation": presentation,
            "detail_keys": sorted(str(key) for key in request.detail),
            "occurred_at": now_iso(moment),
        }
        self._audit.append(record)
        if self._audit_sink is not None:
            self._audit_sink(dict(record))
        return record

    @staticmethod
    def _contains_howto(detail: Mapping[str, Any]) -> bool:
        for key, value in detail.items():
            if str(key).strip().lower() in _HOWTO_KEYS:
                return True
            for text in _iter_strings(value):
                if any(pattern.search(text) for pattern in _HOWTO_PATTERNS):
                    return True
        return False

    @staticmethod
    def _graphic_reason(detail: Mapping[str, Any]) -> str | None:
        for key, value in detail.items():
            if str(key).strip().lower() in _GRAPHIC_FLAGS and bool(value):
                return "explicit or graphic content is never simulated"
            for text in _iter_strings(value):
                if _GRAPHIC_PATTERN.search(text):
                    return "explicit or graphic content is never simulated"
        return None


def _iter_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_strings(item))
        return tuple(out)
    if isinstance(value, (list, tuple, set, frozenset)):
        out = []
        for item in value:
            out.extend(_iter_strings(item))
        return tuple(out)
    return ()
