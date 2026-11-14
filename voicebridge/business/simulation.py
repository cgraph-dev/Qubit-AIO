"""P7-01 — data-driven needs, traits, relationships and the action catalog.

This module is the *simulation* half of the Office interaction engine. It is
pure data plus deterministic arithmetic: no store handle, no clock, no model,
no network and no random source. An action the agents choose changes **declared
simulation state only** — needs, relationships, cooldowns and a bounded
simulation note. It can never invent or mutate a business fact (an order, a
ledger entry, a completed run, a customer record or a paid amount), and money
or approval authority is never granted by animation.

What lives here
---------------
* :class:`Needs` — nine bounded need values (``0..100``; higher is better, so
  ``hunger``/``thirst`` are *satiety*): energy, hunger, thirst, hygiene,
  social, stress, focus, morale, health.
* :class:`Traits` — the declared personality set from the design.
* :class:`Relationship` — six bounded dimensions (``-100..100``): trust,
  respect, attraction, resentment, friendship, rivalry.
* :class:`AgentSimState` — one agent's bounded simulation state: needs, traits,
  relationships, cooldowns, reputation, tick, revision and bounded history.
* :class:`Preconditions` — needs/mood/relationship/role/time/privacy gates.
* :class:`ActionDefinition` — an action as data, every field the plan names:
  rooms, props, required agents, preconditions, duration, cooldown, animation/
  emote/particles/sound hints, need effects, relationship effects, memory
  event, note, risks/consequences, approval/consent requirement and the
  ``mature_content`` flag.
* :class:`ActionCatalog` — validated catalog, deterministic utility scoring,
  typed eligibility evaluation and selection.
* :class:`ActionResult` / :class:`Selection` — the *declared* outcome. A
  rejected action is typed and never applied (``executed=False``).

Determinism
-----------
Selection iterates actions in sorted id order, ties break to the smaller id,
scores are rounded before comparison, and every state/result carries a
canonical SHA-256 digest. Replaying the same state, context and catalog twice
produces identical bytes. Cooldowns advance by explicit simulation ticks, not
by wall-clock time, so replay is independent of the host clock.

Mature content
--------------
Romance and risky actions are catalogued but flagged ``mature_content=True``
and are **never selected or executed by default**. Enabling them requires the
context to opt in (``allow_mature=True``) *and* the action's own consent /
approval gates to pass. Sexual and fighting actions are described
non-graphically; substance actions declare consequences only and carry no
real-world dosing, sourcing or how-to text.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .errors import BusinessError

SCHEMA = "qubit-office-simulation/1"
VERSION = 1

NEED_FIELDS: tuple[str, ...] = (
    "energy",
    "hunger",
    "thirst",
    "hygiene",
    "social",
    "stress",
    "focus",
    "morale",
    "health",
)
NEED_MIN = 0.0
NEED_MAX = 100.0

TRAITS: tuple[str, ...] = (
    "introvert",
    "ambitious",
    "risk_taker",
    "calm",
    "aggressive",
    "romantic",
    "loner",
    "joker",
    "workaholic",
)

RELATIONSHIP_FIELDS: tuple[str, ...] = (
    "trust",
    "respect",
    "attraction",
    "resentment",
    "friendship",
    "rivalry",
)
RELATIONSHIP_MIN = -100.0
RELATIONSHIP_MAX = 100.0
REPUTATION_MIN = -100.0
REPUTATION_MAX = 100.0

CATEGORIES: tuple[str, ...] = (
    "work",
    "social",
    "needs",
    "conflict",
    "career",
    "romance",
    "risky",
)
#: Categories the shipped catalog must always cover.
REQUIRED_CATEGORIES: tuple[str, ...] = (
    "work",
    "social",
    "needs",
    "conflict",
    "career",
)
MATURE_CATEGORIES: frozenset[str] = frozenset({"romance", "risky"})

TIME_SLOTS: tuple[str, ...] = ("morning", "afternoon", "evening", "night")
MOODS: tuple[str, ...] = ("positive", "neutral", "negative")
PRIVACY_CLASSES: frozenset[str] = frozenset(
    {"public", "department", "private", "restricted"}
)
APPROVAL_REQUIREMENTS: tuple[str, ...] = ("none", "manager", "owner", "hr")
CONSENT_REQUIREMENTS: tuple[str, ...] = ("none", "consent", "all_parties")

#: Effect keys are the nine needs plus reputation. Anything else is refused.
EFFECT_FIELDS: tuple[str, ...] = NEED_FIELDS + ("reputation",)

#: A simulation payload may not grow any of these keys. They name business
#: truth the simulation is forbidden to invent.
FORBIDDEN_TRUTH_KEYS: tuple[str, ...] = (
    "agent_run",
    "amount",
    "claim",
    "committed",
    "completed_run",
    "customer",
    "employee_record",
    "invoice",
    "ledger_entry",
    "order",
    "paid",
    "payout",
    "provider_receipt",
    "receipt",
    "revenue",
    "task_completed",
    "transaction",
)

#: Memory events this module may emit. They are simulation notes, never
#: business events.
MEMORY_EVENT_PREFIX = "simulation."

SIMULATION_INVALID_CODE = "SIMULATION_INVALID"
SIMULATION_UNKNOWN_ACTION_CODE = "SIMULATION_UNKNOWN_ACTION"
SIMULATION_PRECONDITION_CODE = "SIMULATION_PRECONDITION_FAILED"
SIMULATION_WRONG_ROOM_CODE = "SIMULATION_WRONG_ROOM"
SIMULATION_MISSING_PROP_CODE = "SIMULATION_MISSING_PROP"
SIMULATION_PARTICIPANTS_CODE = "SIMULATION_PARTICIPANTS"
SIMULATION_COOLDOWN_CODE = "SIMULATION_COOLDOWN"
SIMULATION_PRIVACY_CODE = "SIMULATION_PRIVACY_VIOLATION"
SIMULATION_MATURE_CODE = "SIMULATION_MATURE_DISABLED"
SIMULATION_CONSENT_CODE = "SIMULATION_CONSENT_REQUIRED"
SIMULATION_APPROVAL_CODE = "SIMULATION_APPROVAL_REQUIRED"
SIMULATION_NO_ACTION_CODE = "SIMULATION_NO_ELIGIBLE_ACTION"

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_.:][a-z0-9]+)*$")
_TEXT_MAX = 400
_NOTE_MAX = 240
_HISTORY_MAX = 32
_NOTES_MAX = 32


class SimulationError(BusinessError):
    """A typed simulation refusal."""


def _fail(code: str, detail: str) -> None:
    raise SimulationError(code, detail)


def _token(value: Any, label: str, *, max_len: int = 80) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(SIMULATION_INVALID_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len or not _TOKEN_RE.match(clean):
        _fail(
            SIMULATION_INVALID_CODE,
            f"{label} must be a stable token (letters, digits, . _ : -)",
        )
    return clean


def _text(value: Any, label: str, *, max_len: int = _TEXT_MAX) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(SIMULATION_INVALID_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        _fail(
            SIMULATION_INVALID_CODE,
            f"{label} is longer than {max_len} characters",
        )
    return clean


def _optional_text(value: Any, label: str, *, max_len: int = _TEXT_MAX) -> str:
    if value is None or value == "":
        return ""
    return _text(value, label, max_len=max_len)


def _bounded(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(SIMULATION_INVALID_CODE, f"{label} must be a number")
    number = float(value)
    if number < low or number > high:
        _fail(
            SIMULATION_INVALID_CODE,
            f"{label} must be within {low}..{high}",
        )
    return number


def _bounded_int(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(SIMULATION_INVALID_CODE, f"{label} must be an integer")
    if value < low or value > high:
        _fail(SIMULATION_INVALID_CODE, f"{label} must be within {low}..{high}")
    return value


def _sequence(value: Any, label: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(SIMULATION_INVALID_CODE, f"{label} must be a sequence")
    return tuple(value)


def _tokens(
    value: Any,
    label: str,
    *,
    allowed: Iterable[str] | None = None,
    required: bool = False,
) -> tuple[str, ...]:
    items = tuple(_token(item, label) for item in _sequence(value, label))
    if required and not items:
        _fail(SIMULATION_INVALID_CODE, f"{label} must not be empty")
    if len(set(items)) != len(items):
        _fail(SIMULATION_INVALID_CODE, f"{label} must not repeat values")
    if allowed is not None:
        allow = frozenset(allowed)
        bad = sorted(set(items) - allow)
        if bad:
            _fail(
                SIMULATION_INVALID_CODE,
                f"{label} has unknown values {bad}",
            )
    return items


def _mapping(
    value: Any,
    label: str,
    *,
    allowed: Iterable[str] | None = None,
) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        _fail(SIMULATION_INVALID_CODE, f"{label} must be a mapping")
    allow = frozenset(allowed) if allowed is not None else None
    out: dict[str, float] = {}
    for raw_key in sorted(value):
        key = _token(raw_key, f"{label} key")
        if allow is not None and key not in allow:
            _fail(
                SIMULATION_INVALID_CODE,
                f"{label} has unknown key {key!r}",
            )
        number = value[raw_key]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            _fail(SIMULATION_INVALID_CODE, f"{label}[{key}] must be a number")
        out[key] = float(number)
    return MappingProxyType(out)


def _pairs(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(SIMULATION_INVALID_CODE, f"{label} must be a mapping")
    out: dict[str, Any] = {}
    for raw_key in sorted(value):
        key = _token(raw_key, f"{label} key")
        out[key] = value[raw_key]
    return MappingProxyType(out)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(payload: Any) -> str:
    """Canonical SHA-256 over a JSON-compatible payload."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, inner in value.items():
            yield str(key)
            yield from _walk_keys(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _walk_keys(inner)


# ------------------------------------------------------------------ needs
@dataclass(frozen=True, slots=True)
class Needs:
    """Nine bounded need values (higher is better; hunger/thirst are satiety)."""

    energy: float = 70.0
    hunger: float = 70.0
    thirst: float = 70.0
    hygiene: float = 70.0
    social: float = 60.0
    stress: float = 20.0
    focus: float = 65.0
    morale: float = 70.0
    health: float = 85.0

    def __post_init__(self) -> None:
        for name in NEED_FIELDS:
            object.__setattr__(
                self,
                name,
                _bounded(getattr(self, name), f"needs.{name}", NEED_MIN, NEED_MAX),
            )

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in NEED_FIELDS}

    def apply(self, effects: Mapping[str, float]) -> "Needs":
        """Return a new bounded value with the declared deltas applied."""
        values = self.as_dict()
        for key, delta in effects.items():
            if key in values:
                values[key] = _clamp(values[key] + float(delta), NEED_MIN, NEED_MAX)
        return Needs(**values)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Needs":
        if not isinstance(data, Mapping):
            _fail(SIMULATION_INVALID_CODE, "needs must be a mapping")
        unknown = sorted(set(data) - set(NEED_FIELDS))
        if unknown:
            _fail(SIMULATION_INVALID_CODE, f"needs has unknown keys {unknown}")
        return cls(**{name: data[name] for name in NEED_FIELDS if name in data})

    def to_dict(self) -> dict[str, float]:
        return self.as_dict()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


# ----------------------------------------------------------------- traits
@dataclass(frozen=True, slots=True)
class Traits:
    """The declared personality set; unknown traits are refused."""

    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "names",
            _tokens(self.names, "traits", allowed=TRAITS),
        )

    def has(self, name: str) -> bool:
        return name in self.names

    def as_dict(self) -> dict[str, Any]:
        return {"names": list(self.names)}

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "Traits":
        return cls(names=tuple(names))

    @classmethod
    def from_dict(cls, data: Any) -> "Traits":
        if isinstance(data, Mapping):
            return cls(names=_sequence(data.get("names", ()), "traits.names"))
        return cls(names=_sequence(data, "traits"))

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


# ----------------------------------------------------------- relationship
@dataclass(frozen=True, slots=True)
class Relationship:
    """Six bounded relationship dimensions between two agents."""

    trust: float = 0.0
    respect: float = 0.0
    attraction: float = 0.0
    resentment: float = 0.0
    friendship: float = 0.0
    rivalry: float = 0.0

    def __post_init__(self) -> None:
        for name in RELATIONSHIP_FIELDS:
            object.__setattr__(
                self,
                name,
                _bounded(
                    getattr(self, name),
                    f"relationship.{name}",
                    RELATIONSHIP_MIN,
                    RELATIONSHIP_MAX,
                ),
            )

    def apply(self, effects: Mapping[str, float]) -> "Relationship":
        values = self.as_dict()
        for key, delta in effects.items():
            if key in values:
                values[key] = _clamp(
                    values[key] + float(delta),
                    RELATIONSHIP_MIN,
                    RELATIONSHIP_MAX,
                )
        return Relationship(**values)

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in RELATIONSHIP_FIELDS}

    @classmethod
    def from_dict(cls, data: Any) -> "Relationship":
        if not isinstance(data, Mapping):
            _fail(SIMULATION_INVALID_CODE, "relationship must be a mapping")
        unknown = sorted(set(data) - set(RELATIONSHIP_FIELDS))
        if unknown:
            _fail(
                SIMULATION_INVALID_CODE,
                f"relationship has unknown keys {unknown}",
            )
        return cls(
            **{name: data[name] for name in RELATIONSHIP_FIELDS if name in data}
        )

    def to_dict(self) -> dict[str, float]:
        return self.as_dict()


# ---------------------------------------------------------- preconditions
@dataclass(frozen=True, slots=True)
class Preconditions:
    """Declarative gates checked before an action may run."""

    needs_min: Mapping[str, float] = field(default_factory=dict)
    needs_max: Mapping[str, float] = field(default_factory=dict)
    mood: tuple[str, ...] = ()
    relationship_min: Mapping[str, float] = field(default_factory=dict)
    relationship_max: Mapping[str, float] = field(default_factory=dict)
    roles: tuple[str, ...] = ()
    time_slots: tuple[str, ...] = ()
    privacy: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "needs_min",
            _mapping(self.needs_min, "preconditions.needs_min", allowed=NEED_FIELDS),
        )
        object.__setattr__(
            self,
            "needs_max",
            _mapping(self.needs_max, "preconditions.needs_max", allowed=NEED_FIELDS),
        )
        object.__setattr__(
            self,
            "relationship_min",
            _mapping(
                self.relationship_min,
                "preconditions.relationship_min",
                allowed=RELATIONSHIP_FIELDS,
            ),
        )
        object.__setattr__(
            self,
            "relationship_max",
            _mapping(
                self.relationship_max,
                "preconditions.relationship_max",
                allowed=RELATIONSHIP_FIELDS,
            ),
        )
        object.__setattr__(
            self, "mood", _tokens(self.mood, "preconditions.mood", allowed=MOODS)
        )
        object.__setattr__(
            self, "roles", _tokens(self.roles, "preconditions.roles")
        )
        object.__setattr__(
            self,
            "time_slots",
            _tokens(
                self.time_slots, "preconditions.time_slots", allowed=TIME_SLOTS
            ),
        )
        object.__setattr__(
            self,
            "privacy",
            _tokens(
                self.privacy,
                "preconditions.privacy",
                allowed=PRIVACY_CLASSES,
            ),
        )
        for name in NEED_FIELDS:
            if name in self.needs_min:
                _bounded(
                    self.needs_min[name],
                    f"preconditions.needs_min.{name}",
                    NEED_MIN,
                    NEED_MAX,
                )
            if name in self.needs_max:
                _bounded(
                    self.needs_max[name],
                    f"preconditions.needs_max.{name}",
                    NEED_MIN,
                    NEED_MAX,
                )
        for name in RELATIONSHIP_FIELDS:
            if name in self.relationship_min:
                _bounded(
                    self.relationship_min[name],
                    f"preconditions.relationship_min.{name}",
                    RELATIONSHIP_MIN,
                    RELATIONSHIP_MAX,
                )
            if name in self.relationship_max:
                _bounded(
                    self.relationship_max[name],
                    f"preconditions.relationship_max.{name}",
                    RELATIONSHIP_MIN,
                    RELATIONSHIP_MAX,
                )

    @property
    def needs_a_target(self) -> bool:
        return bool(self.relationship_min or self.relationship_max)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_min": dict(self.needs_min),
            "needs_max": dict(self.needs_max),
            "mood": list(self.mood),
            "relationship_min": dict(self.relationship_min),
            "relationship_max": dict(self.relationship_max),
            "roles": list(self.roles),
            "time_slots": list(self.time_slots),
            "privacy": list(self.privacy),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Preconditions":
        if not isinstance(data, Mapping):
            _fail(SIMULATION_INVALID_CODE, "preconditions must be a mapping")
        return cls(
            needs_min=data.get("needs_min", {}),
            needs_max=data.get("needs_max", {}),
            mood=tuple(data.get("mood", ())),
            relationship_min=data.get("relationship_min", {}),
            relationship_max=data.get("relationship_max", {}),
            roles=tuple(data.get("roles", ())),
            time_slots=tuple(data.get("time_slots", ())),
            privacy=tuple(data.get("privacy", ())),
        )


# --------------------------------------------------------------- context
@dataclass(frozen=True, slots=True)
class SimulationContext:
    """The deterministic context an action is evaluated against."""

    room_type: str = "work"
    props: frozenset[str] = frozenset()
    privacy_class: str = "department"
    time_slot: str = "morning"
    roles: frozenset[str] = frozenset()
    participant_count: int = 1
    target_agent_id: str | None = None
    allow_mature: bool = False
    consent_granted: bool = False
    approval_granted: bool = False
    deadline_pressure: float = 0.0
    manager_present: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_type", _token(self.room_type, "room_type"))
        object.__setattr__(
            self,
            "props",
            frozenset(_tokens(tuple(self.props), "props")),
        )
        object.__setattr__(
            self,
            "privacy_class",
            _token(self.privacy_class, "privacy_class"),
        )
        if self.privacy_class not in PRIVACY_CLASSES:
            _fail(
                SIMULATION_INVALID_CODE,
                f"privacy_class must be one of {sorted(PRIVACY_CLASSES)}",
            )
        object.__setattr__(self, "time_slot", _token(self.time_slot, "time_slot"))
        if self.time_slot not in TIME_SLOTS:
            _fail(
                SIMULATION_INVALID_CODE,
                f"time_slot must be one of {sorted(TIME_SLOTS)}",
            )
        object.__setattr__(
            self, "roles", frozenset(_tokens(tuple(self.roles), "roles"))
        )
        object.__setattr__(
            self,
            "participant_count",
            _bounded_int(self.participant_count, "participant_count", 1, 64),
        )
        if self.target_agent_id is not None:
            object.__setattr__(
                self,
                "target_agent_id",
                _token(self.target_agent_id, "target_agent_id"),
            )
        for name in (
            "allow_mature",
            "consent_granted",
            "approval_granted",
            "manager_present",
        ):
            if not isinstance(getattr(self, name), bool):
                _fail(SIMULATION_INVALID_CODE, f"{name} must be a boolean")
        object.__setattr__(
            self,
            "deadline_pressure",
            _bounded(self.deadline_pressure, "deadline_pressure", 0.0, 1.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_type": self.room_type,
            "props": sorted(self.props),
            "privacy_class": self.privacy_class,
            "time_slot": self.time_slot,
            "roles": sorted(self.roles),
            "participant_count": self.participant_count,
            "target_agent_id": self.target_agent_id,
            "allow_mature": self.allow_mature,
            "consent_granted": self.consent_granted,
            "approval_granted": self.approval_granted,
            "deadline_pressure": self.deadline_pressure,
            "manager_present": self.manager_present,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SimulationContext":
        if not isinstance(data, Mapping):
            _fail(SIMULATION_INVALID_CODE, "context must be a mapping")
        return cls(
            room_type=data.get("room_type", "work"),
            props=frozenset(data.get("props", ())),
            privacy_class=data.get("privacy_class", "department"),
            time_slot=data.get("time_slot", "morning"),
            roles=frozenset(data.get("roles", ())),
            participant_count=data.get("participant_count", 1),
            target_agent_id=data.get("target_agent_id"),
            allow_mature=data.get("allow_mature", False),
            consent_granted=data.get("consent_granted", False),
            approval_granted=data.get("approval_granted", False),
            deadline_pressure=data.get("deadline_pressure", 0.0),
            manager_present=data.get("manager_present", False),
        )


# ----------------------------------------------------------- action data
@dataclass(frozen=True, slots=True)
class ActionDefinition:
    """One action as data. Construction validates every declared field."""

    action_id: str
    label: str
    category: str
    room_types: tuple[str, ...] = ()
    props: tuple[str, ...] = ()
    required_agents: int = 1
    preconditions: Preconditions = field(default_factory=Preconditions)
    duration_seconds: int = 60
    cooldown_steps: int = 1
    animation: str = "idle"
    emote: str = ""
    particles: str = ""
    sound: str = ""
    effects: Mapping[str, float] = field(default_factory=dict)
    relationship_effects: Mapping[str, float] = field(default_factory=dict)
    memory_event: str = ""
    note: str = ""
    risks: tuple[str, ...] = ()
    approval: str = "none"
    consent: str = "none"
    mature_content: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", _token(self.action_id, "action_id"))
        object.__setattr__(self, "label", _text(self.label, "label", max_len=120))
        category = _token(self.category, "category")
        if category not in CATEGORIES:
            _fail(
                SIMULATION_INVALID_CODE,
                f"category must be one of {sorted(CATEGORIES)}",
            )
        object.__setattr__(self, "category", category)
        object.__setattr__(
            self, "room_types", _tokens(self.room_types, "room_types")
        )
        object.__setattr__(self, "props", _tokens(self.props, "props"))
        object.__setattr__(
            self,
            "required_agents",
            _bounded_int(self.required_agents, "required_agents", 1, 64),
        )
        if not isinstance(self.preconditions, Preconditions):
            _fail(
                SIMULATION_INVALID_CODE,
                "preconditions must be a Preconditions value",
            )
        object.__setattr__(
            self,
            "duration_seconds",
            _bounded_int(self.duration_seconds, "duration_seconds", 1, 86400),
        )
        object.__setattr__(
            self,
            "cooldown_steps",
            _bounded_int(self.cooldown_steps, "cooldown_steps", 0, 10000),
        )
        object.__setattr__(self, "animation", _token(self.animation, "animation"))
        object.__setattr__(self, "emote", _optional_text(self.emote, "emote"))
        object.__setattr__(
            self, "particles", _optional_text(self.particles, "particles")
        )
        object.__setattr__(self, "sound", _optional_text(self.sound, "sound"))
        effects = _mapping(self.effects, "effects", allowed=EFFECT_FIELDS)
        for key, value in effects.items():
            _bounded(value, f"effects.{key}", -100.0, 100.0)
        object.__setattr__(self, "effects", effects)
        rel = _mapping(
            self.relationship_effects,
            "relationship_effects",
            allowed=RELATIONSHIP_FIELDS,
        )
        for key, value in rel.items():
            _bounded(
                value,
                f"relationship_effects.{key}",
                RELATIONSHIP_MIN,
                RELATIONSHIP_MAX,
            )
        object.__setattr__(self, "relationship_effects", rel)
        memory = _optional_text(self.memory_event, "memory_event", max_len=128)
        if memory and not memory.startswith(MEMORY_EVENT_PREFIX):
            _fail(
                SIMULATION_INVALID_CODE,
                f"memory_event must start with {MEMORY_EVENT_PREFIX!r}",
            )
        object.__setattr__(self, "memory_event", memory)
        object.__setattr__(self, "note", _optional_text(self.note, "note",
                                                        max_len=_NOTE_MAX))
        object.__setattr__(self, "risks", _tokens(self.risks, "risks"))
        approval = _token(self.approval, "approval")
        if approval not in APPROVAL_REQUIREMENTS:
            _fail(
                SIMULATION_INVALID_CODE,
                f"approval must be one of {APPROVAL_REQUIREMENTS}",
            )
        object.__setattr__(self, "approval", approval)
        consent = _token(self.consent, "consent")
        if consent not in CONSENT_REQUIREMENTS:
            _fail(
                SIMULATION_INVALID_CODE,
                f"consent must be one of {CONSENT_REQUIREMENTS}",
            )
        object.__setattr__(self, "consent", consent)
        if not isinstance(self.mature_content, bool):
            _fail(SIMULATION_INVALID_CODE, "mature_content must be a boolean")
        if category in MATURE_CATEGORIES and not self.mature_content:
            _fail(
                SIMULATION_INVALID_CODE,
                f"{category} actions must set mature_content=True",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "label": self.label,
            "category": self.category,
            "room_types": list(self.room_types),
            "props": list(self.props),
            "required_agents": self.required_agents,
            "preconditions": self.preconditions.to_dict(),
            "duration_seconds": self.duration_seconds,
            "cooldown_steps": self.cooldown_steps,
            "animation": self.animation,
            "emote": self.emote,
            "particles": self.particles,
            "sound": self.sound,
            "effects": dict(self.effects),
            "relationship_effects": dict(self.relationship_effects),
            "memory_event": self.memory_event,
            "note": self.note,
            "risks": list(self.risks),
            "approval": self.approval,
            "consent": self.consent,
            "mature_content": self.mature_content,
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, data: Any) -> "ActionDefinition":
        if not isinstance(data, Mapping):
            _fail(SIMULATION_INVALID_CODE, "action must be a mapping")
        pre = data.get("preconditions", {})
        if isinstance(pre, Preconditions):
            preconditions = pre
        else:
            preconditions = Preconditions.from_dict(pre)
        return cls(
            action_id=data.get("action_id"),
            label=data.get("label"),
            category=data.get("category"),
            room_types=tuple(data.get("room_types", ())),
            props=tuple(data.get("props", ())),
            required_agents=data.get("required_agents", 1),
            preconditions=preconditions,
            duration_seconds=data.get("duration_seconds", 60),
            cooldown_steps=data.get("cooldown_steps", 1),
            animation=data.get("animation", "idle"),
            emote=data.get("emote", ""),
            particles=data.get("particles", ""),
            sound=data.get("sound", ""),
            effects=data.get("effects", {}),
            relationship_effects=data.get("relationship_effects", {}),
            memory_event=data.get("memory_event", ""),
            note=data.get("note", ""),
            risks=tuple(data.get("risks", ())),
            approval=data.get("approval", "none"),
            consent=data.get("consent", "none"),
            mature_content=data.get("mature_content", False),
        )


# ------------------------------------------------------------ agent state
@dataclass(frozen=True, slots=True)
class AgentSimState:
    """One agent's bounded simulation state; every transition returns a copy."""

    agent_id: str
    needs: Needs = field(default_factory=Needs)
    traits: Traits = field(default_factory=Traits)
    relationships: Mapping[str, Relationship] = field(default_factory=dict)
    cooldowns: Mapping[str, int] = field(default_factory=dict)
    reputation: float = 0.0
    tick: int = 0
    revision: int = 0
    history: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _token(self.agent_id, "agent_id"))
        if not isinstance(self.needs, Needs):
            _fail(SIMULATION_INVALID_CODE, "needs must be a Needs value")
        if not isinstance(self.traits, Traits):
            _fail(SIMULATION_INVALID_CODE, "traits must be a Traits value")
        rels = _pairs(self.relationships, "relationships")
        clean_rels: dict[str, Relationship] = {}
        for target, value in rels.items():
            clean_rels[target] = (
                value
                if isinstance(value, Relationship)
                else Relationship.from_dict(value)
            )
        object.__setattr__(
            self, "relationships", MappingProxyType(clean_rels)
        )
        cds = _pairs(self.cooldowns, "cooldowns")
        clean_cds: dict[str, int] = {}
        for action_id, value in cds.items():
            clean_cds[action_id] = _bounded_int(
                value, f"cooldowns.{action_id}", 0, 10000
            )
        object.__setattr__(self, "cooldowns", MappingProxyType(clean_cds))
        object.__setattr__(
            self,
            "reputation",
            _bounded(self.reputation, "reputation", REPUTATION_MIN, REPUTATION_MAX),
        )
        object.__setattr__(self, "tick", _bounded_int(self.tick, "tick", 0, 10**9))
        object.__setattr__(
            self,
            "revision",
            _bounded_int(self.revision, "revision", 0, 10**9),
        )
        history = tuple(
            _token(item, "history item") for item in _sequence(self.history,
                                                               "history")
        )
        object.__setattr__(self, "history", history[-_HISTORY_MAX:])
        notes = tuple(
            _text(item, "note item", max_len=_NOTE_MAX)
            for item in _sequence(self.notes, "notes")
        )
        object.__setattr__(self, "notes", notes[-_NOTES_MAX:])

    def relationship_with(self, target: str | None) -> Relationship:
        if not target:
            return Relationship()
        return self.relationships.get(target, Relationship())

    def with_relationships(
        self, relationships: Mapping[str, Relationship]
    ) -> "AgentSimState":
        return self._replace(relationships=relationships)

    def _replace(self, **changes: Any) -> "AgentSimState":
        values = {
            "agent_id": self.agent_id,
            "needs": self.needs,
            "traits": self.traits,
            "relationships": dict(self.relationships),
            "cooldowns": dict(self.cooldowns),
            "reputation": self.reputation,
            "tick": self.tick,
            "revision": self.revision,
            "history": self.history,
            "notes": self.notes,
        }
        values.update(changes)
        return AgentSimState(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "needs": self.needs.to_dict(),
            "traits": self.traits.to_dict(),
            "relationships": {
                target: rel.to_dict()
                for target, rel in sorted(self.relationships.items())
            },
            "cooldowns": dict(sorted(self.cooldowns.items())),
            "reputation": self.reputation,
            "tick": self.tick,
            "revision": self.revision,
            "history": list(self.history),
            "notes": list(self.notes),
            "digest": self.digest,
        }

    @property
    def digest(self) -> str:
        payload = {
            "agent_id": self.agent_id,
            "needs": self.needs.to_dict(),
            "traits": list(self.traits.names),
            "relationships": {
                target: rel.to_dict()
                for target, rel in sorted(self.relationships.items())
            },
            "cooldowns": dict(sorted(self.cooldowns.items())),
            "reputation": self.reputation,
            "tick": self.tick,
            "revision": self.revision,
            "history": list(self.history),
            "notes": list(self.notes),
        }
        return digest(payload)

    @classmethod
    def from_dict(cls, data: Any) -> "AgentSimState":
        if not isinstance(data, Mapping):
            _fail(SIMULATION_INVALID_CODE, "state must be a mapping")
        return cls(
            agent_id=data.get("agent_id"),
            needs=Needs.from_dict(data.get("needs", {})),
            traits=Traits.from_dict(data.get("traits", {})),
            relationships=data.get("relationships", {}),
            cooldowns=data.get("cooldowns", {}),
            reputation=data.get("reputation", 0.0),
            tick=data.get("tick", 0),
            revision=data.get("revision", 0),
            history=tuple(data.get("history", ())),
            notes=tuple(data.get("notes", ())),
        )


# ------------------------------------------------------------- catalog
@dataclass(frozen=True, slots=True)
class ActionCatalog:
    """A validated action catalog with deterministic selection."""

    actions: tuple[ActionDefinition, ...]

    def __post_init__(self) -> None:
        actions = tuple(self.actions)
        for action in actions:
            if not isinstance(action, ActionDefinition):
                _fail(
                    SIMULATION_INVALID_CODE,
                    "catalog entries must be ActionDefinition values",
                )
        ids = [action.action_id for action in actions]
        if len(set(ids)) != len(ids):
            _fail(SIMULATION_INVALID_CODE, "catalog action ids must be unique")
        object.__setattr__(
            self, "actions", tuple(sorted(actions, key=lambda a: a.action_id))
        )

    @property
    def by_id(self) -> Mapping[str, ActionDefinition]:
        return MappingProxyType(
            {action.action_id: action for action in self.actions}
        )

    def action(self, action_id: str) -> ActionDefinition:
        clean = _token(action_id, "action_id")
        found = self.by_id.get(clean)
        if found is None:
            _fail(
                SIMULATION_UNKNOWN_ACTION_CODE,
                f"unknown action {clean!r}",
            )
        return found

    def category_counts(self) -> dict[str, int]:
        counts = {category: 0 for category in CATEGORIES}
        for action in self.actions:
            counts[action.category] += 1
        return counts

    def validate(self) -> None:
        counts = self.category_counts()
        missing = [name for name in REQUIRED_CATEGORIES if counts[name] == 0]
        if missing:
            _fail(
                SIMULATION_INVALID_CODE,
                f"catalog is missing required categories {missing}",
            )
        for action in self.actions:
            if action.category in MATURE_CATEGORIES and not action.mature_content:
                _fail(
                    SIMULATION_INVALID_CODE,
                    f"mature action {action.action_id!r} is not flagged",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "version": VERSION,
            "actions": [action.to_dict() for action in self.actions],
            "counts": self.category_counts(),
            "digest": self.digest,
        }

    @property
    def digest(self) -> str:
        return digest(
            {
                "schema": SCHEMA,
                "version": VERSION,
                "actions": [action.digest for action in self.actions],
            }
        )

    @classmethod
    def from_dict(cls, data: Any) -> "ActionCatalog":
        if not isinstance(data, Mapping):
            _fail(SIMULATION_INVALID_CODE, "catalog must be a mapping")
        actions = data.get("actions", ())
        if isinstance(actions, (str, bytes)) or not isinstance(actions, Sequence):
            _fail(SIMULATION_INVALID_CODE, "catalog.actions must be a sequence")
        return cls(actions=tuple(ActionDefinition.from_dict(row) for row in actions))

    # -- evaluation ----------------------------------------------------
    def evaluate(
        self,
        state: AgentSimState,
        action_id: str,
        context: SimulationContext,
    ) -> "Evaluation":
        action = self.by_id.get(action_id)
        if action is None:
            return Evaluation(False, SIMULATION_UNKNOWN_ACTION_CODE,
                              f"unknown action {action_id!r}")
        return _evaluate(state, action, context)

    # -- scoring -------------------------------------------------------
    def score(
        self,
        state: AgentSimState,
        action: ActionDefinition,
        context: SimulationContext,
    ) -> float:
        return _score(state, action, context)

    # -- selection -----------------------------------------------------
    def select(
        self,
        state: AgentSimState,
        context: SimulationContext,
        *,
        categories: Iterable[str] | None = None,
    ) -> "Selection":
        allowed = None
        if categories is not None:
            allowed = frozenset(categories)
        best: ActionDefinition | None = None
        best_score = float("-inf")
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        for action in self.actions:
            if allowed is not None and action.category not in allowed:
                continue
            evaluation = _evaluate(state, action, context)
            if not evaluation.ok:
                rejected.append(
                    {"action_id": action.action_id, "code": evaluation.code}
                )
                continue
            score = _score(state, action, context)
            candidates.append(
                {"action_id": action.action_id, "score": score}
            )
            if score > best_score:
                best = action
                best_score = score
        if best is None:
            return Selection(
                action=None,
                score=0.0,
                candidates=tuple(candidates),
                rejected=tuple(rejected),
                code=SIMULATION_NO_ACTION_CODE,
                detail="no eligible action for this state and context",
            )
        return Selection(
            action=best,
            score=best_score,
            candidates=tuple(candidates),
            rejected=tuple(rejected),
        )


@dataclass(frozen=True, slots=True)
class Evaluation:
    ok: bool
    code: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Selection:
    action: ActionDefinition | None
    score: float
    candidates: tuple[Mapping[str, Any], ...] = ()
    rejected: tuple[Mapping[str, str], ...] = ()
    code: str = ""
    detail: str = ""

    @property
    def action_id(self) -> str | None:
        return self.action.action_id if self.action else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "category": self.action.category if self.action else None,
            "score": self.score,
            "candidates": list(self.candidates),
            "rejected": list(self.rejected),
            "code": self.code,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ActionResult:
    """The declared outcome of one simulation step. Never business truth."""

    action_id: str
    agent_id: str
    category: str
    accepted: bool
    executed: bool
    code: str = ""
    detail: str = ""
    needs_before: Mapping[str, float] = field(default_factory=dict)
    needs_after: Mapping[str, float] = field(default_factory=dict)
    relationship_before: Mapping[str, float] = field(default_factory=dict)
    relationship_after: Mapping[str, float] = field(default_factory=dict)
    relationship_target: str | None = None
    reputation_before: float = 0.0
    reputation_after: float = 0.0
    memory_event: str = ""
    note: str = ""
    risks: tuple[str, ...] = ()
    mature_content: bool = False
    approval: str = "none"
    consent: str = "none"
    animation: str = ""
    emote: str = ""
    particles: str = ""
    sound: str = ""
    duration_seconds: int = 0
    cooldown_steps: int = 0
    declared_only: bool = True
    business_truth: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "action_id": self.action_id,
            "agent_id": self.agent_id,
            "category": self.category,
            "accepted": self.accepted,
            "executed": self.executed,
            "code": self.code,
            "detail": self.detail,
            "needs_before": dict(self.needs_before),
            "needs_after": dict(self.needs_after),
            "relationship_before": dict(self.relationship_before),
            "relationship_after": dict(self.relationship_after),
            "relationship_target": self.relationship_target,
            "reputation_before": self.reputation_before,
            "reputation_after": self.reputation_after,
            "memory_event": self.memory_event,
            "note": self.note,
            "risks": list(self.risks),
            "mature_content": self.mature_content,
            "approval": self.approval,
            "consent": self.consent,
            "animation": self.animation,
            "emote": self.emote,
            "particles": self.particles,
            "sound": self.sound,
            "duration_seconds": self.duration_seconds,
            "cooldown_steps": self.cooldown_steps,
            "declared_only": self.declared_only,
            "business_truth": self.business_truth,
        }
        payload["digest"] = digest(payload)
        return payload


# --------------------------------------------------------------- helpers
def current_mood(state: AgentSimState) -> str:
    """Derive the deterministic mood label from morale and stress."""
    needs = state.needs
    if needs.morale >= 60.0 and needs.stress <= 40.0:
        return "positive"
    if needs.morale < 30.0 or needs.stress >= 75.0:
        return "negative"
    return "neutral"


def _evaluate(
    state: AgentSimState,
    action: ActionDefinition,
    context: SimulationContext,
) -> Evaluation:
    if action.mature_content and not context.allow_mature:
        return Evaluation(
            False,
            SIMULATION_MATURE_CODE,
            f"{action.action_id!r} is mature content and is disabled by default",
        )
    if action.room_types and context.room_type not in action.room_types:
        return Evaluation(
            False,
            SIMULATION_WRONG_ROOM_CODE,
            f"{action.action_id!r} requires room types "
            f"{list(action.room_types)}; context is {context.room_type!r}",
        )
    missing_props = sorted(set(action.props) - set(context.props))
    if missing_props:
        return Evaluation(
            False,
            SIMULATION_MISSING_PROP_CODE,
            f"{action.action_id!r} requires missing props {missing_props}",
        )
    if action.required_agents > context.participant_count:
        return Evaluation(
            False,
            SIMULATION_PARTICIPANTS_CODE,
            f"{action.action_id!r} needs {action.required_agents} agents;"
            f" context has {context.participant_count}",
        )
    pre = action.preconditions
    if pre.privacy and context.privacy_class not in pre.privacy:
        return Evaluation(
            False,
            SIMULATION_PRIVACY_CODE,
            f"{action.action_id!r} requires privacy "
            f"{list(pre.privacy)}; context is {context.privacy_class!r}",
        )
    if pre.time_slots and context.time_slot not in pre.time_slots:
        return Evaluation(
            False,
            SIMULATION_PRECONDITION_CODE,
            f"{action.action_id!r} requires time slots {list(pre.time_slots)};"
            f" context is {context.time_slot!r}",
        )
    if pre.roles:
        missing_roles = sorted(set(pre.roles) - set(context.roles))
        if missing_roles:
            return Evaluation(
                False,
                SIMULATION_PRECONDITION_CODE,
                f"{action.action_id!r} requires roles {missing_roles}",
            )
    if state.cooldowns.get(action.action_id, 0) > 0:
        return Evaluation(
            False,
            SIMULATION_COOLDOWN_CODE,
            f"{action.action_id!r} is on cooldown for"
            f" {state.cooldowns[action.action_id]} step(s)",
        )
    needs = state.needs
    for name, minimum in pre.needs_min.items():
        if getattr(needs, name) < minimum:
            return Evaluation(
                False,
                SIMULATION_PRECONDITION_CODE,
                f"{action.action_id!r} requires {name} >= {minimum};"
                f" state has {getattr(needs, name)}",
            )
    for name, maximum in pre.needs_max.items():
        if getattr(needs, name) > maximum:
            return Evaluation(
                False,
                SIMULATION_PRECONDITION_CODE,
                f"{action.action_id!r} requires {name} <= {maximum};"
                f" state has {getattr(needs, name)}",
            )
    if pre.mood and current_mood(state) not in pre.mood:
        return Evaluation(
            False,
            SIMULATION_PRECONDITION_CODE,
            f"{action.action_id!r} requires mood {list(pre.mood)};"
            f" state is {current_mood(state)!r}",
        )
    if pre.needs_a_target:
        if not context.target_agent_id:
            return Evaluation(
                False,
                SIMULATION_PRECONDITION_CODE,
                f"{action.action_id!r} needs a relationship target",
            )
        relationship = state.relationship_with(context.target_agent_id)
        for name, minimum in pre.relationship_min.items():
            if getattr(relationship, name) < minimum:
                return Evaluation(
                    False,
                    SIMULATION_PRECONDITION_CODE,
                    f"{action.action_id!r} requires relationship.{name}"
                    f" >= {minimum}",
                )
        for name, maximum in pre.relationship_max.items():
            if getattr(relationship, name) > maximum:
                return Evaluation(
                    False,
                    SIMULATION_PRECONDITION_CODE,
                    f"{action.action_id!r} requires relationship.{name}"
                    f" <= {maximum}",
                )
    if action.consent != "none" and not context.consent_granted:
        return Evaluation(
            False,
            SIMULATION_CONSENT_CODE,
            f"{action.action_id!r} requires {action.consent} consent",
        )
    if action.approval != "none" and not context.approval_granted:
        return Evaluation(
            False,
            SIMULATION_APPROVAL_CODE,
            f"{action.action_id!r} requires {action.approval} approval",
        )
    return Evaluation(True)


#: Deterministic trait affinities. Each value is added to the utility score
#: when the agent has that trait and the action has that category.
TRAIT_AFFINITY: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        "introvert": MappingProxyType({"social": -0.6, "work": 0.3, "needs": 0.2}),
        "ambitious": MappingProxyType({"work": 0.8, "career": 0.9}),
        "risk_taker": MappingProxyType({"risky": 1.2, "conflict": 0.4}),
        "calm": MappingProxyType({"conflict": -0.8, "needs": 0.2}),
        "aggressive": MappingProxyType({"conflict": 0.9}),
        "romantic": MappingProxyType({"romance": 1.1}),
        "loner": MappingProxyType({"social": -0.9, "work": 0.2}),
        "joker": MappingProxyType({"social": 0.7}),
        "workaholic": MappingProxyType({"work": 0.9, "career": 0.5}),
    }
)


def _score(
    state: AgentSimState,
    action: ActionDefinition,
    context: SimulationContext,
) -> float:
    score = 0.0
    needs = state.needs
    for key in sorted(action.effects):
        delta = float(action.effects[key])
        if key == "stress":
            # Relief is worth more when the agent is already stressed.
            score += (-delta) * (0.5 + needs.stress / 100.0)
        elif key == "reputation":
            score += delta * 0.25
        else:
            current = getattr(needs, key)
            score += delta * (0.5 + (NEED_MAX - current) / 100.0)
    for trait in state.traits.names:
        score += TRAIT_AFFINITY.get(trait, {}).get(action.category, 0.0)
    if action.relationship_effects:
        relationship = state.relationship_with(context.target_agent_id)
        for key in sorted(action.relationship_effects):
            delta = float(action.relationship_effects[key])
            if key in ("resentment", "rivalry"):
                # Repairing resentment/rivalry is positive utility.
                score += (-delta) * (0.5 + getattr(relationship, key) / 100.0)
            else:
                score += delta * (0.5 + getattr(relationship, key) / 100.0)
    if action.category in ("work", "career"):
        score += context.deadline_pressure * 0.8
        if context.manager_present:
            score += 0.3
    return round(score, 6)


# ---------------------------------------------------------------- simulate
def apply_action(
    state: AgentSimState,
    action: ActionDefinition,
    context: SimulationContext,
) -> tuple[AgentSimState, ActionResult]:
    """Evaluate and, only if eligible, apply declared simulation effects."""
    evaluation = _evaluate(state, action, context)
    needs_before = state.needs.as_dict()
    relationship_before = state.relationship_with(
        context.target_agent_id
    ).as_dict()
    if not evaluation.ok:
        return state, ActionResult(
            action_id=action.action_id,
            agent_id=state.agent_id,
            category=action.category,
            accepted=False,
            executed=False,
            code=evaluation.code,
            detail=evaluation.detail,
            needs_before=MappingProxyType(needs_before),
            needs_after=MappingProxyType(needs_before),
            relationship_before=MappingProxyType(relationship_before),
            relationship_after=MappingProxyType(relationship_before),
            relationship_target=context.target_agent_id,
            reputation_before=state.reputation,
            reputation_after=state.reputation,
            mature_content=action.mature_content,
            approval=action.approval,
            consent=action.consent,
            declared_only=True,
            business_truth=False,
        )

    need_effects = {
        key: value for key, value in action.effects.items() if key in NEED_FIELDS
    }
    new_needs = state.needs.apply(need_effects)
    reputation_delta = float(action.effects.get("reputation", 0.0))
    new_reputation = _clamp(
        state.reputation + reputation_delta, REPUTATION_MIN, REPUTATION_MAX
    )
    new_relationships = dict(state.relationships)
    relationship_after = relationship_before
    if action.relationship_effects and context.target_agent_id:
        target = context.target_agent_id
        updated = state.relationship_with(target).apply(
            action.relationship_effects
        )
        new_relationships[target] = updated
        relationship_after = updated.as_dict()

    # Cooldowns advance by explicit simulation ticks, not wall-clock time.
    new_cooldowns = {
        action_id: max(0, int(value) - 1)
        for action_id, value in state.cooldowns.items()
    }
    if action.cooldown_steps > 0:
        new_cooldowns[action.action_id] = action.cooldown_steps
    else:
        new_cooldowns[action.action_id] = 0

    history = (state.history + (action.action_id,))[-_HISTORY_MAX:]
    notes = state.notes
    if action.memory_event:
        notes = (state.notes + (action.memory_event,))[-_NOTES_MAX:]

    new_state = AgentSimState(
        agent_id=state.agent_id,
        needs=new_needs,
        traits=state.traits,
        relationships=new_relationships,
        cooldowns=new_cooldowns,
        reputation=new_reputation,
        tick=state.tick + 1,
        revision=state.revision + 1,
        history=history,
        notes=notes,
    )
    result = ActionResult(
        action_id=action.action_id,
        agent_id=state.agent_id,
        category=action.category,
        accepted=True,
        executed=True,
        needs_before=MappingProxyType(needs_before),
        needs_after=MappingProxyType(new_needs.as_dict()),
        relationship_before=MappingProxyType(relationship_before),
        relationship_after=MappingProxyType(relationship_after),
        relationship_target=context.target_agent_id,
        reputation_before=state.reputation,
        reputation_after=new_reputation,
        memory_event=action.memory_event,
        note=action.note,
        risks=action.risks,
        mature_content=action.mature_content,
        approval=action.approval,
        consent=action.consent,
        animation=action.animation,
        emote=action.emote,
        particles=action.particles,
        sound=action.sound,
        duration_seconds=action.duration_seconds,
        cooldown_steps=action.cooldown_steps,
        declared_only=True,
        business_truth=False,
    )
    return new_state, result


def simulate(
    state: AgentSimState,
    catalog: ActionCatalog,
    context: SimulationContext,
    *,
    action_id: str | None = None,
    categories: Iterable[str] | None = None,
) -> tuple[AgentSimState, ActionResult]:
    """Select (or request) one action and apply it when eligible.

    With ``action_id`` the requested action is evaluated explicitly; an
    ineligible request is a typed refusal and the state is unchanged. Without
    it the highest-utility eligible action is selected deterministically.
    """
    if action_id is not None:
        action = catalog.action(action_id)
    else:
        selection = catalog.select(state, context, categories=categories)
        if selection.action is None:
            return state, ActionResult(
                action_id="",
                agent_id=state.agent_id,
                category="",
                accepted=False,
                executed=False,
                code=selection.code,
                detail=selection.detail,
                needs_before=MappingProxyType(state.needs.as_dict()),
                needs_after=MappingProxyType(state.needs.as_dict()),
                relationship_before=MappingProxyType(
                    state.relationship_with(
                        context.target_agent_id
                    ).as_dict()
                ),
                relationship_after=MappingProxyType(
                    state.relationship_with(
                        context.target_agent_id
                    ).as_dict()
                ),
                relationship_target=context.target_agent_id,
                reputation_before=state.reputation,
                reputation_after=state.reputation,
                declared_only=True,
                business_truth=False,
            )
        action = selection.action
    return apply_action(state, action, context)


def validate_result(result: ActionResult) -> None:
    """Refuse a result that grew a business-truth key or a non-sim memory."""
    payload = result.to_dict()
    truth = sorted(set(_walk_keys(payload)) & set(FORBIDDEN_TRUTH_KEYS))
    if truth:
        _fail(
            SIMULATION_INVALID_CODE,
            f"simulation result exposes business truth keys {truth}",
        )
    if result.memory_event and not result.memory_event.startswith(
        MEMORY_EVENT_PREFIX
    ):
        _fail(
            SIMULATION_INVALID_CODE,
            "simulation memory events must be simulation.* notes",
        )


# ---------------------------------------------------------- shipped catalog
def _action(
    action_id: str,
    label: str,
    category: str,
    *,
    room_types: Sequence[str] = (),
    props: Sequence[str] = (),
    required_agents: int = 1,
    needs_min: Mapping[str, float] | None = None,
    needs_max: Mapping[str, float] | None = None,
    mood: Sequence[str] = (),
    relationship_min: Mapping[str, float] | None = None,
    roles: Sequence[str] = (),
    time_slots: Sequence[str] = (),
    privacy: Sequence[str] = (),
    duration_seconds: int = 60,
    cooldown_steps: int = 1,
    animation: str = "idle",
    emote: str = "",
    particles: str = "",
    sound: str = "",
    effects: Mapping[str, float] | None = None,
    relationship_effects: Mapping[str, float] | None = None,
    memory_event: str = "",
    note: str = "",
    risks: Sequence[str] = (),
    approval: str = "none",
    consent: str = "none",
    mature_content: bool = False,
) -> ActionDefinition:
    return ActionDefinition(
        action_id=action_id,
        label=label,
        category=category,
        room_types=tuple(room_types),
        props=tuple(props),
        required_agents=required_agents,
        preconditions=Preconditions(
            needs_min=needs_min or {},
            needs_max=needs_max or {},
            mood=tuple(mood),
            relationship_min=relationship_min or {},
            roles=tuple(roles),
            time_slots=tuple(time_slots),
            privacy=tuple(privacy),
        ),
        duration_seconds=duration_seconds,
        cooldown_steps=cooldown_steps,
        animation=animation,
        emote=emote,
        particles=particles,
        sound=sound,
        effects=effects or {},
        relationship_effects=relationship_effects or {},
        memory_event=memory_event,
        note=note,
        risks=tuple(risks),
        approval=approval,
        consent=consent,
        mature_content=mature_content,
    )


def default_actions() -> tuple[ActionDefinition, ...]:
    """The shipped catalog: work, social, needs, conflict, career and mature."""
    return (
        # -- work ------------------------------------------------------
        _action(
            "work-pair-programming",
            "Pair programming",
            "work",
            room_types=("work", "focus"),
            required_agents=2,
            needs_min={"focus": 20.0, "energy": 20.0},
            duration_seconds=1800,
            animation="typing_together",
            effects={"focus": 6.0, "stress": 3.0, "morale": 2.0},
            relationship_effects={"trust": 2.0, "respect": 2.0,
                                  "friendship": 2.0},
            memory_event="simulation.work.pair_programming",
            note="Two simulated agents review code together.",
        ),
        _action(
            "work-code-review",
            "Code review",
            "work",
            room_types=("work", "focus"),
            props=("computer",),
            duration_seconds=1200,
            effects={"focus": 5.0, "stress": 2.0},
            relationship_effects={"respect": 3.0, "trust": 1.0},
            memory_event="simulation.work.code_review",
            note="A simulated review pass; no repository is written.",
        ),
        _action(
            "work-whiteboard",
            "Whiteboard a problem",
            "work",
            room_types=("work", "war_room"),
            props=("whiteboard",),
            required_agents=2,
            duration_seconds=900,
            effects={"focus": 7.0, "morale": 3.0, "stress": 2.0},
            relationship_effects={"trust": 2.0, "respect": 2.0},
            memory_event="simulation.work.whiteboard",
        ),
        _action(
            "work-deadline-sprint",
            "Deadline sprint",
            "work",
            room_types=("work",),
            needs_min={"energy": 30.0},
            needs_max={"stress": 80.0},
            duration_seconds=3600,
            animation="working_fast",
            effects={"energy": -15.0, "stress": 18.0, "focus": 8.0,
                     "morale": -4.0},
            memory_event="simulation.work.deadline_sprint",
            risks=("burnout",),
            note="Simulated overtime pressure; no work product is created.",
        ),
        _action(
            "work-take-call",
            "Take a call",
            "work",
            room_types=("work", "focus"),
            props=("phone",),
            time_slots=("morning", "afternoon"),
            duration_seconds=900,
            effects={"focus": 2.0, "stress": 3.0},
            memory_event="simulation.work.call",
        ),
        _action(
            "work-mentor",
            "Mentor a colleague",
            "work",
            room_types=("work", "focus"),
            required_agents=2,
            duration_seconds=1200,
            effects={"morale": 4.0, "focus": 2.0},
            relationship_effects={"respect": 4.0, "friendship": 2.0,
                                  "trust": 2.0},
            memory_event="simulation.work.mentor",
        ),
        # -- career ----------------------------------------------------
        _action(
            "career-ask-for-raise",
            "Ask for a raise",
            "career",
            room_types=("work", "war_room"),
            needs_min={"morale": 30.0},
            mood=("positive", "neutral"),
            duration_seconds=1800,
            effects={"morale": 8.0, "stress": 6.0},
            memory_event="simulation.career.ask_for_raise",
            risks=("rejection",),
            approval="manager",
            note="A simulated conversation; it never changes payroll.",
        ),
        _action(
            "career-promotion",
            "Discuss a promotion",
            "career",
            room_types=("work", "war_room"),
            mood=("positive",),
            duration_seconds=1800,
            effects={"morale": 10.0, "stress": 4.0},
            memory_event="simulation.career.promotion",
            approval="owner",
            note="Simulation only; no personnel record or pay changes.",
        ),
        _action(
            "career-resign",
            "Resign",
            "career",
            room_types=("work", "war_room"),
            duration_seconds=600,
            effects={"morale": -6.0, "stress": 10.0},
            memory_event="simulation.career.resign",
            risks=("handoff_required", "hr_case"),
            approval="owner",
            note="Simulation only; no real employment record changes.",
        ),
        _action(
            "career-start-side-project",
            "Start a side project",
            "career",
            room_types=("focus", "work"),
            duration_seconds=2400,
            effects={"focus": 4.0, "morale": 5.0, "stress": 2.0,
                     "energy": -6.0},
            memory_event="simulation.career.side_project",
        ),
        # -- social ----------------------------------------------------
        _action(
            "social-coffee",
            "Coffee break",
            "social",
            room_types=("break_room", "work"),
            props=("coffee_machine",),
            required_agents=2,
            duration_seconds=600,
            effects={"social": 12.0, "stress": -6.0, "energy": 4.0,
                     "thirst": 8.0},
            relationship_effects={"friendship": 3.0, "trust": 1.0},
            memory_event="simulation.social.coffee",
        ),
        _action(
            "social-lunch",
            "Lunch together",
            "social",
            room_types=("break_room", "cafeteria"),
            props=("table",),
            required_agents=2,
            duration_seconds=1800,
            effects={"social": 15.0, "hunger": 20.0, "thirst": 15.0,
                     "energy": 6.0, "stress": -4.0},
            relationship_effects={"friendship": 4.0, "trust": 1.0},
            memory_event="simulation.social.lunch",
        ),
        _action(
            "social-water-cooler",
            "Water-cooler chat",
            "social",
            room_types=("break_room", "work"),
            props=("water_cooler",),
            required_agents=2,
            duration_seconds=300,
            effects={"social": 10.0, "stress": -2.0},
            relationship_effects={"friendship": 2.0},
            memory_event="simulation.social.water_cooler",
        ),
        _action(
            "social-joke",
            "Tell a joke",
            "social",
            required_agents=2,
            duration_seconds=120,
            effects={"social": 6.0, "morale": 5.0, "stress": -2.0},
            relationship_effects={"friendship": 2.0},
            memory_event="simulation.social.joke",
        ),
        _action(
            "social-compliment",
            "Give a compliment",
            "social",
            required_agents=2,
            duration_seconds=120,
            effects={"social": 4.0, "morale": 3.0},
            relationship_effects={"respect": 2.0, "friendship": 2.0},
            memory_event="simulation.social.compliment",
        ),
        _action(
            "social-watch-tv",
            "Watch TV together",
            "social",
            room_types=("break_room", "lounge"),
            props=("tv",),
            required_agents=2,
            time_slots=("evening", "night"),
            duration_seconds=1800,
            effects={"social": 8.0, "stress": -5.0, "morale": 2.0},
            relationship_effects={"friendship": 2.0},
            memory_event="simulation.social.watch_tv",
        ),
        _action(
            "social-exercise",
            "Exercise together",
            "social",
            room_types=("gym", "break_room"),
            props=("gym_equipment",),
            required_agents=2,
            needs_min={"energy": 25.0},
            duration_seconds=1800,
            effects={"energy": -8.0, "health": 6.0, "stress": -8.0,
                     "social": 6.0},
            relationship_effects={"friendship": 2.0, "trust": 1.0},
            memory_event="simulation.social.exercise",
        ),
        # -- needs -----------------------------------------------------
        _action(
            "needs-bathroom",
            "Use the bathroom",
            "needs",
            room_types=("bathroom",),
            props=("toilet",),
            privacy=("private", "restricted"),
            duration_seconds=300,
            effects={"hygiene": 8.0, "stress": -2.0},
            memory_event="simulation.needs.bathroom",
        ),
        _action(
            "needs-wash-hands",
            "Wash hands",
            "needs",
            room_types=("bathroom", "break_room", "work"),
            props=("sink",),
            duration_seconds=60,
            effects={"hygiene": 6.0},
            memory_event="simulation.needs.wash_hands",
        ),
        _action(
            "needs-shower",
            "Shower",
            "needs",
            room_types=("bathroom",),
            props=("shower",),
            privacy=("private", "restricted"),
            duration_seconds=900,
            effects={"hygiene": 25.0, "stress": -6.0, "energy": 2.0},
            memory_event="simulation.needs.shower",
        ),
        _action(
            "needs-snack",
            "Grab a snack",
            "needs",
            room_types=("break_room", "cafeteria"),
            props=("snack_machine",),
            duration_seconds=180,
            effects={"hunger": 20.0, "morale": 2.0},
            memory_event="simulation.needs.snack",
        ),
        _action(
            "needs-water",
            "Drink water",
            "needs",
            room_types=("break_room", "work", "cafeteria"),
            props=("water_cooler",),
            duration_seconds=60,
            effects={"thirst": 30.0, "health": 1.0},
            memory_event="simulation.needs.water",
        ),
        _action(
            "needs-nap",
            "Take a nap",
            "needs",
            room_types=("break_room", "focus"),
            props=("couch",),
            privacy=("private", "restricted"),
            needs_max={"stress": 90.0},
            duration_seconds=1800,
            effects={"energy": 25.0, "stress": -10.0, "focus": 5.0},
            memory_event="simulation.needs.nap",
        ),
        _action(
            "needs-medication",
            "Take medication",
            "needs",
            privacy=("private", "restricted"),
            duration_seconds=120,
            effects={"health": 8.0, "stress": -2.0},
            memory_event="simulation.needs.medication",
            note="Simulated health state only; no medical advice.",
        ),
        _action(
            "needs-sick-leave",
            "Take sick leave",
            "needs",
            room_types=("work", "break_room"),
            duration_seconds=600,
            effects={"health": 10.0, "stress": -10.0, "morale": 2.0},
            memory_event="simulation.needs.sick_leave",
            risks=("handoff_required",),
            approval="manager",
        ),
        _action(
            "needs-smoke-vape",
            "Smoke / vape break",
            "needs",
            room_types=("break_room", "outside"),
            privacy=("private", "restricted"),
            duration_seconds=300,
            effects={"stress": -8.0, "health": -6.0},
            memory_event="simulation.needs.smoke_break",
            risks=("health_risk", "policy_check"),
            mature_content=True,
            note="Simulated habit consequence only; no product guidance.",
        ),
        # -- conflict --------------------------------------------------
        _action(
            "conflict-disagreement",
            "Disagree",
            "conflict",
            required_agents=2,
            duration_seconds=300,
            effects={"stress": 6.0, "morale": -3.0},
            relationship_effects={"trust": -2.0, "respect": -1.0,
                                  "resentment": 4.0},
            memory_event="simulation.conflict.disagreement",
            risks=("escalation",),
        ),
        _action(
            "conflict-argument",
            "Argue",
            "conflict",
            required_agents=2,
            needs_min={"stress": 20.0},
            duration_seconds=600,
            effects={"stress": 12.0, "morale": -6.0},
            relationship_effects={"trust": -6.0, "respect": -4.0,
                                  "friendship": -4.0, "resentment": 8.0},
            memory_event="simulation.conflict.argument",
            risks=("escalation", "hr_case"),
        ),
        _action(
            "conflict-insult",
            "Insult a colleague",
            "conflict",
            required_agents=2,
            duration_seconds=120,
            effects={"stress": 8.0, "morale": -5.0},
            relationship_effects={"trust": -8.0, "respect": -8.0,
                                  "friendship": -6.0, "resentment": 10.0},
            memory_event="simulation.conflict.insult",
            risks=("hr_case",),
            approval="hr",
        ),
        _action(
            "conflict-shove",
            "Shove a colleague",
            "conflict",
            required_agents=2,
            duration_seconds=60,
            effects={"health": -4.0, "stress": 15.0, "morale": -8.0},
            relationship_effects={"trust": -12.0, "respect": -10.0,
                                  "resentment": 15.0},
            memory_event="simulation.conflict.shove",
            risks=("hr_case", "injury"),
            approval="hr",
            mature_content=True,
            note="Non-graphic simulated conflict; no gore or instructions.",
        ),
        _action(
            "conflict-report-hr",
            "Report to HR",
            "conflict",
            room_types=("work", "war_room"),
            duration_seconds=900,
            effects={"stress": -4.0, "morale": 2.0},
            relationship_effects={"trust": -2.0, "respect": 1.0},
            memory_event="simulation.conflict.report_hr",
            risks=("hr_case",),
            approval="hr",
            note="Simulation only; no real HR record is created.",
        ),
        _action(
            "conflict-mediate",
            "Mediate a disagreement",
            "conflict",
            required_agents=2,
            duration_seconds=900,
            effects={"stress": -4.0, "morale": 3.0},
            relationship_effects={"resentment": -6.0, "trust": 4.0,
                                  "respect": 3.0},
            memory_event="simulation.conflict.mediate",
        ),
        # -- romance / mature (opt-in) ---------------------------------
        _action(
            "romance-flirt",
            "Flirt",
            "romance",
            required_agents=2,
            duration_seconds=120,
            effects={"morale": 6.0, "stress": -2.0},
            relationship_effects={"attraction": 4.0, "friendship": 2.0},
            memory_event="simulation.romance.flirt",
            consent="consent",
            mature_content=True,
            note="Non-graphic simulated interaction, disabled by default.",
        ),
        _action(
            "romance-ask-out",
            "Ask someone out",
            "romance",
            required_agents=2,
            relationship_min={"attraction": 10.0},
            duration_seconds=300,
            effects={"morale": 5.0, "stress": 4.0},
            relationship_effects={"attraction": 6.0},
            memory_event="simulation.romance.ask_out",
            risks=("rejection",),
            consent="consent",
            mature_content=True,
            note="Non-graphic simulated interaction, disabled by default.",
        ),
        _action(
            "romance-hold-hands",
            "Hold hands",
            "romance",
            required_agents=2,
            relationship_min={"attraction": 20.0},
            privacy=("private", "restricted"),
            duration_seconds=120,
            effects={"morale": 5.0, "stress": -3.0},
            relationship_effects={"attraction": 5.0, "friendship": 3.0},
            memory_event="simulation.romance.hold_hands",
            consent="all_parties",
            mature_content=True,
            note="Non-graphic simulated interaction, disabled by default.",
        ),
        _action(
            "romance-kiss",
            "Kiss",
            "romance",
            required_agents=2,
            relationship_min={"attraction": 30.0},
            privacy=("private", "restricted"),
            duration_seconds=60,
            effects={"morale": 8.0, "stress": -4.0},
            relationship_effects={"attraction": 8.0, "friendship": 2.0},
            memory_event="simulation.romance.kiss",
            risks=("policy_check",),
            consent="all_parties",
            mature_content=True,
            note="Non-graphic simulated interaction, disabled by default.",
        ),
        _action(
            "romance-private-encounter",
            "Private encounter (fade to black)",
            "romance",
            required_agents=2,
            relationship_min={"attraction": 40.0, "trust": 30.0},
            privacy=("private", "restricted"),
            duration_seconds=1800,
            effects={"morale": 10.0, "stress": -10.0, "energy": -8.0},
            relationship_effects={"attraction": 10.0, "friendship": 5.0,
                                  "trust": 4.0},
            memory_event="simulation.romance.private_encounter",
            risks=("policy_check",),
            approval="hr",
            consent="all_parties",
            mature_content=True,
            note="Fade-to-black, non-graphic; disabled by default.",
        ),
        _action(
            "romance-breakup",
            "Break up",
            "romance",
            required_agents=2,
            duration_seconds=600,
            effects={"morale": -10.0, "stress": 12.0},
            relationship_effects={"attraction": -12.0, "friendship": -10.0,
                                  "trust": -6.0, "resentment": 8.0},
            memory_event="simulation.romance.breakup",
            consent="all_parties",
            mature_content=True,
            note="Non-graphic simulated interaction, disabled by default.",
        ),
        # -- risky / mature (opt-in) -----------------------------------
        _action(
            "risky-alcohol",
            "Have a drink (simulated)",
            "risky",
            room_types=("break_room", "bar"),
            props=("bar",),
            duration_seconds=1800,
            effects={"health": -6.0, "stress": -6.0, "morale": 4.0,
                     "focus": -4.0},
            memory_event="simulation.risky.alcohol",
            risks=("health_risk", "hr_case"),
            mature_content=True,
            note="Simulated consequence only; no real-world guidance.",
        ),
        _action(
            "risky-gambling",
            "Gamble (simulated)",
            "risky",
            room_types=("break_room", "lounge"),
            duration_seconds=600,
            effects={"morale": 3.0, "stress": 4.0},
            memory_event="simulation.risky.gambling",
            risks=("financial_risk",),
            approval="owner",
            mature_content=True,
            note="Simulation only; never moves money or creates a ledger row.",
        ),
    )


def default_catalog() -> ActionCatalog:
    """Build and validate the shipped action catalog."""
    catalog = ActionCatalog(actions=default_actions())
    catalog.validate()
    return catalog


__all__ = [
    "SCHEMA",
    "VERSION",
    "NEED_FIELDS",
    "NEED_MIN",
    "NEED_MAX",
    "TRAITS",
    "RELATIONSHIP_FIELDS",
    "RELATIONSHIP_MIN",
    "RELATIONSHIP_MAX",
    "REPUTATION_MIN",
    "REPUTATION_MAX",
    "CATEGORIES",
    "REQUIRED_CATEGORIES",
    "MATURE_CATEGORIES",
    "TIME_SLOTS",
    "MOODS",
    "PRIVACY_CLASSES",
    "APPROVAL_REQUIREMENTS",
    "CONSENT_REQUIREMENTS",
    "EFFECT_FIELDS",
    "FORBIDDEN_TRUTH_KEYS",
    "MEMORY_EVENT_PREFIX",
    "SIMULATION_INVALID_CODE",
    "SIMULATION_UNKNOWN_ACTION_CODE",
    "SIMULATION_PRECONDITION_CODE",
    "SIMULATION_WRONG_ROOM_CODE",
    "SIMULATION_MISSING_PROP_CODE",
    "SIMULATION_PARTICIPANTS_CODE",
    "SIMULATION_COOLDOWN_CODE",
    "SIMULATION_PRIVACY_CODE",
    "SIMULATION_MATURE_CODE",
    "SIMULATION_CONSENT_CODE",
    "SIMULATION_APPROVAL_CODE",
    "SIMULATION_NO_ACTION_CODE",
    "SimulationError",
    "Needs",
    "Traits",
    "Relationship",
    "Preconditions",
    "SimulationContext",
    "ActionDefinition",
    "AgentSimState",
    "ActionCatalog",
    "Evaluation",
    "Selection",
    "ActionResult",
    "apply_action",
    "simulate",
    "current_mood",
    "validate_result",
    "default_actions",
    "default_catalog",
    "digest",
]
