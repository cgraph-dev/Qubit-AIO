"""P7-05 — outside life, weather, seasons and the social-graph projection.

This is a *presentation* projection, not a simulation engine. It consumes
authoritative/source events supplied by the caller and derives three bounded
views:

* **environment** — the weather / day-night / seasonal state recorded by
  environment events, with the same animated cue and reduced-motion still cue
  the P6-03 outside world declares;
* **ambient outside life** — visitors and vehicles as presentation sprites only,
  restricted to the active exterior zones;
* **social graph** — relationships (friendship / rivalry / trust) folded from
  interaction events, with every social memory row carrying its own scope,
  visibility, retention and provenance.

The hard rules this module enforces
-----------------------------------
* **Source events drive effects.** No events means no environment, no ambient
  sprites and no social graph. Nothing is invented from a default, the clock or
  a random number generator.
* **Active zones only.** An ambient event that names a dormant or unknown zone
  is ignored and reported, never animated.
* **Presentation is not business truth.** No output can grow an
  agent/occupant/seat/desk/run/task/working/completed field; relationships are
  labelled ``simulation_only``.
* **Social records respect an injected ACL.** :class:`SocialACL` is consulted
  per record (``can_read``/``can_write``); a cross-scope record is denied, not
  silently widened. :class:`ScopedSocialACL` is a deny-by-default reference
  implementation.
* **Bounded.** Event scans, ambient sprites, social records/edges/nodes, text
  lengths and provenance lists all have fixed caps; overflow is truncated and
  reported, never allowed to grow with the input.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from . import exterior as _exterior
from . import outside_world as _outside_world

__all__ = [
    "SCHEMA",
    "VERSION",
    "SCENARIO_EVENT_TYPES",
    "ENVIRONMENT_EVENT_TYPES",
    "AMBIENT_EVENT_TYPES",
    "INTERACTION_EVENT_TYPES",
    "SCOPES",
    "VISIBILITIES",
    "RETENTION_CLASSES",
    "MAX_EVENTS",
    "MAX_AMBIENT",
    "MAX_AMBIENT_PER_EVENT",
    "MAX_SOCIAL_RECORDS",
    "MAX_SOCIAL_EDGES",
    "MAX_SOCIAL_NODES",
    "MAX_PROVENANCE_IDS",
    "MAX_TEXT",
    "MAX_TRUST",
    "MAX_TRUST_DELTA",
    "FORBIDDEN_TRUTH_KEYS",
    "OutsideLifeError",
    "SocialScopeDenied",
    "SocialACL",
    "ScopedSocialACL",
    "SocialMemoryRecord",
    "OutsideLife",
    "build_outside_life",
    "outside_life_payload",
    "validate_outside_life",
    "filter_social_records",
    "require_social_read",
    "compute_digest",
]

SCHEMA = "qubit-office-outside-life/1"
VERSION = 1

#: Event ``type`` values the projection understands. Anything else is ignored
#: and counted, never guessed at.
ENVIRONMENT_EVENT_TYPES = frozenset(
    {
        "environment",
        "weather",
        "weather.changed",
        "day_night",
        "day_night.changed",
        "season",
        "season.changed",
    }
)
AMBIENT_EVENT_TYPES = frozenset(
    {
        "outside.visitor",
        "visitor",
        "outside.vehicle",
        "vehicle",
        "outside.ambient",
    }
)
INTERACTION_EVENT_TYPES = frozenset(
    {"interaction", "simulation.interaction", "social.interaction"}
)
SCENARIO_EVENT_TYPES = frozenset(
    set(ENVIRONMENT_EVENT_TYPES) | set(AMBIENT_EVENT_TYPES)
    | set(INTERACTION_EVENT_TYPES)
)

SCOPES = ("agent", "session", "task", "department", "office", "tenant")
VISIBILITIES = ("private", "internal", "department", "office", "public")
RETENTION_CLASSES = ("ephemeral", "standard", "extended", "legal-hold")
DEFAULT_TENANT = "office"
DEFAULT_VISIBILITY = "internal"
DEFAULT_RETENTION = "standard"

MAX_EVENTS = 10_000
MAX_AMBIENT = 64
MAX_AMBIENT_PER_EVENT = 4
MAX_SOCIAL_RECORDS = 512
MAX_SOCIAL_EDGES = 512
MAX_SOCIAL_NODES = 1_024
MAX_PROVENANCE_IDS = 16
MAX_PARTICIPANTS = 8
MAX_TEXT = 200
MAX_TRANSITIONS = 32
MAX_TRUST = 100.0
MAX_TRUST_DELTA = 10.0

#: A presentation payload may not grow any of these keys. They would turn the
#: projection into a second source of business truth.
FORBIDDEN_TRUTH_KEYS: Tuple[str, ...] = (
    "agent",
    "agents",
    "agent_id",
    "occupant",
    "occupants",
    "desk",
    "desks",
    "desk_id",
    "seat",
    "seats",
    "seat_id",
    "run",
    "run_id",
    "task",
    "task_id",
    "working",
    "completed",
    "business_id",
    "session_id",
)

_AMBIENT_CUES: Dict[str, Tuple[str, str]] = {
    "visitor": ("walk-in", "visitor-still"),
    "courier": ("walk-in", "visitor-still"),
    "delivery": ("walk-in", "visitor-still"),
    "guest": ("walk-in", "visitor-still"),
    "vehicle": ("drive-by", "vehicle-parked"),
    "car": ("drive-by", "vehicle-parked"),
    "bus": ("drive-by", "vehicle-parked"),
    "bike": ("roll-by", "vehicle-parked"),
    "cyclist": ("roll-by", "vehicle-parked"),
}
_AMBIENT_DEFAULT_CUE: Dict[str, Tuple[str, str]] = {
    "visitor": ("walk-in", "visitor-still"),
    "vehicle": ("drive-by", "vehicle-parked"),
}

_FRIENDLY_RELATIONSHIPS = frozenset(
    {"friendship", "friend", "collaboration", "collaborate", "mentoring",
     "support", "romance"}
)
_HOSTILE_RELATIONSHIPS = frozenset(
    {"rivalry", "rival", "conflict", "argument", "dispute"}
)


class OutsideLifeError(ValueError):
    """The projection could not be built (a programming error)."""


class SocialScopeDenied(OutsideLifeError):
    """A social record was requested outside the viewer's scope."""

    code = "OFFICE_SOCIAL_SCOPE_DENIED"


# ------------------------------------------------------------------- helpers
def _strip_digest(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip_digest(v) for k, v in value.items() if k != "digest"}
    if isinstance(value, (list, tuple)):
        return [_strip_digest(v) for v in value]
    return value


def compute_digest(payload: Mapping[str, Any]) -> str:
    """Stable SHA-256 of a serialised projection (``digest`` ignored)."""
    return hashlib.sha256(
        json.dumps(
            _strip_digest(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _bounded_text(value: Any, label: str = "", *, max_len: int = MAX_TEXT) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _canonical(value: Any) -> str:
    def norm(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(k): norm(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [norm(v) for v in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return str(item)

    try:
        return json.dumps(norm(value), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return repr(value)


def _fingerprint(event: Mapping[str, Any]) -> str:
    return hashlib.sha1(_canonical(event).encode("utf-8")).hexdigest()


def _stamp(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.6f}"
    return str(value)


def _event_type(event: Mapping[str, Any]) -> str:
    raw = event.get("type", event.get("event_type", ""))
    return _bounded_text(raw, max_len=60).lower()


def _event_id(event: Mapping[str, Any]) -> str:
    raw = event.get("event_id") or event.get("id")
    if raw:
        return _bounded_text(raw, max_len=80)
    return f"evt-{_fingerprint(event)[:16]}"


def _event_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        return payload
    return {}


def _sort_key(event: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (
        _stamp(event.get("occurred_at", event.get("timestamp", event.get("ts")))),
        _event_id(event),
        _fingerprint(event),
    )


def _as_float(value: Any, *, default: float = 0.0, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(lo, min(hi, number))


def _expiry_epoch(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _now_epoch(value: Any) -> Optional[float]:
    return _expiry_epoch(value)


def _is_expired(expires_at: Any, now: Any) -> bool:
    moment = _now_epoch(now)
    if moment is None:
        return False
    expiry = _expiry_epoch(expires_at)
    return expiry is not None and expiry <= moment


def _zone_bounds(map_obj: _exterior.ExteriorMap) -> Dict[str, Dict[str, int]]:
    bounds: Dict[str, Dict[str, int]] = {}
    for zone in map_obj.zones:
        members = [c for c in map_obj.chunks if c.zone_id == zone.zone_id]
        if not members:
            bounds[zone.zone_id] = {"x": 0, "y": 0, "width": 1, "height": 1}
            continue
        xs = [c.x for c in members]
        ys = [c.y for c in members]
        x = min(xs)
        y = min(ys)
        bounds[zone.zone_id] = {
            "x": int(x),
            "y": int(y),
            "width": int(max(c.x + c.width for c in members) - x),
            "height": int(max(c.y + c.height for c in members) - y),
        }
    return bounds


def _cue_index(map_obj: _exterior.ExteriorMap) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {
        (event["kind"], event["value"]): event
        for event in _outside_world.environment_events(map_obj)
    }


# --------------------------------------------------------------- social ACL
class SocialACL(Protocol):
    """Per-record authorization for social memory. Both methods deny by default."""

    def can_read(self, principal: Any, record: "SocialMemoryRecord") -> bool: ...

    def can_write(
        self, principal: Any, scope: str, record: "SocialMemoryRecord"
    ) -> bool: ...


@dataclass(frozen=True)
class ScopedSocialACL:
    """Reference ACL: tenant + department/agent scopes, office optionally.

    It is deliberately deny-by-default: a viewer sees only records in a
    department/agent it names, plus office records when ``allow_office`` is
    explicitly set. Cross-tenant access is always denied.
    """

    tenant_id: str
    departments: frozenset[str] = frozenset()
    agents: frozenset[str] = frozenset()
    allow_office: bool = False

    def __init__(
        self,
        tenant_id: str,
        *,
        departments: Iterable[str] = (),
        agents: Iterable[str] = (),
        allow_office: bool = False,
    ) -> None:
        object.__setattr__(self, "tenant_id", str(tenant_id))
        object.__setattr__(
            self, "departments", frozenset(str(d) for d in departments)
        )
        object.__setattr__(self, "agents", frozenset(str(a) for a in agents))
        object.__setattr__(self, "allow_office", bool(allow_office))

    def _department_of(self, record: "SocialMemoryRecord") -> Optional[str]:
        if record.scope == "department":
            return record.scope_id
        if record.scope == "agent" and "/" in record.scope_id:
            return record.scope_id.split("/", 1)[0]
        raw = record.provenance.get("department")
        return str(raw) if raw else None

    def can_read(self, principal: Any, record: "SocialMemoryRecord") -> bool:
        if record.tenant_id != self.tenant_id:
            return False
        if record.visibility == "public":
            return True
        if record.scope in ("office", "tenant"):
            return self.allow_office and record.visibility in (
                "office",
                "public",
            )
        if record.scope == "department":
            return record.scope_id in self.departments
        if record.scope == "agent":
            if record.scope_id in self.agents:
                return True
            department = self._department_of(record)
            return department is not None and department in self.departments
        return False

    def can_write(
        self, principal: Any, scope: str, record: "SocialMemoryRecord"
    ) -> bool:
        if record.tenant_id != self.tenant_id:
            return False
        if scope in ("office", "tenant"):
            return self.allow_office
        if scope == "department":
            return record.scope_id in self.departments
        if scope == "agent":
            if record.scope_id in self.agents:
                return True
            department = self._department_of(record)
            return department is not None and department in self.departments
        return False


class AllowAllSocialACL:
    """A permissive ACL for a fully-scoped authoritative view.

    Production callers inject :class:`ScopedSocialACL` (or their own); this is
    only for a caller that already resolved the tenant itself.
    """

    def can_read(self, principal: Any, record: "SocialMemoryRecord") -> bool:
        return True

    def can_write(
        self, principal: Any, scope: str, record: "SocialMemoryRecord"
    ) -> bool:
        return True


# ------------------------------------------------------------ social records
@dataclass(frozen=True)
class SocialMemoryRecord:
    """One scoped social fact folded from one interaction event."""

    record_id: str
    tenant_id: str
    scope: str
    scope_id: str
    participants: Tuple[str, ...]
    relationship: str
    trust_delta: float
    provenance: Mapping[str, Any]
    visibility: str
    retention: str
    expires_at: Optional[str]
    source_event_ids: Tuple[str, ...]
    created_at: str
    simulation_only: bool = True

    def to_payload(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "tenant_id": self.tenant_id,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "participants": list(self.participants),
            "relationship": self.relationship,
            "trust_delta": round(float(self.trust_delta), 4),
            "provenance": _strip_digest(dict(self.provenance)),
            "visibility": self.visibility,
            "retention": self.retention,
            "expires_at": self.expires_at,
            "source_event_ids": list(self.source_event_ids),
            "created_at": self.created_at,
            "simulation_only": True,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SocialMemoryRecord":
        return cls(
            record_id=str(payload.get("record_id", "")),
            tenant_id=str(payload.get("tenant_id", "")),
            scope=str(payload.get("scope", "")),
            scope_id=str(payload.get("scope_id", "")),
            participants=tuple(str(p) for p in payload.get("participants", ())),
            relationship=str(payload.get("relationship", "interaction")),
            trust_delta=float(payload.get("trust_delta", 0.0)),
            provenance=dict(payload.get("provenance") or {}),
            visibility=str(payload.get("visibility", DEFAULT_VISIBILITY)),
            retention=str(payload.get("retention", DEFAULT_RETENTION)),
            expires_at=payload.get("expires_at"),
            source_event_ids=tuple(
                str(e) for e in payload.get("source_event_ids", ())
            ),
            created_at=str(payload.get("created_at", "")),
            simulation_only=bool(payload.get("simulation_only", True)),
        )


def _interaction_participants(payload: Mapping[str, Any]) -> Tuple[str, ...]:
    raw = payload.get("participants")
    if raw is None:
        raw = [
            payload.get("from") or payload.get("actor_id"),
            payload.get("to") or payload.get("target_id"),
        ]
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    if not isinstance(raw, Iterable):
        return ()
    values: List[str] = []
    for item in raw:
        text = _bounded_text(item, max_len=80)
        if text and text not in values:
            values.append(text)
    values.sort()
    return tuple(values[:MAX_PARTICIPANTS])


def _interaction_scope(
    payload: Mapping[str, Any],
    participants: Sequence[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(scope, scope_id, department)`` or ``(None, None, None)``."""
    raw_scope = payload.get("scope")
    raw_scope_id = payload.get("scope_id")
    department = _bounded_text(payload.get("department"), max_len=200) or None
    departments = payload.get("departments")
    if department is None and isinstance(departments, Mapping) and participants:
        shared = {
            _bounded_text(departments.get(p), max_len=200)
            for p in participants
            if departments.get(p)
        }
        if len(shared) == 1:
            department = next(iter(shared))
    if raw_scope is None:
        if department is not None:
            return "department", department, department
        return "office", DEFAULT_TENANT, department
    scope = _bounded_text(raw_scope, max_len=20).lower()
    if scope not in SCOPES:
        return None, None, None
    if raw_scope_id is not None:
        scope_id = _bounded_text(raw_scope_id, max_len=200)
    elif scope == "department" and department:
        scope_id = department
    elif scope == "agent" and participants:
        scope_id = participants[0]
    else:
        scope_id = DEFAULT_TENANT
    if not scope_id:
        return None, None, None
    return scope, scope_id, department


def _interaction_record(
    event: Mapping[str, Any], *, now: Any
) -> Optional[SocialMemoryRecord]:
    payload = _event_payload(event)
    participants = _interaction_participants(payload)
    if len(participants) < 2:
        return None
    scope, scope_id, department = _interaction_scope(payload, participants)
    if scope is None or scope_id is None:
        return None
    visibility = _bounded_text(
        payload.get("visibility", DEFAULT_VISIBILITY), max_len=20
    ).lower()
    if visibility not in VISIBILITIES:
        return None
    retention = _bounded_text(
        payload.get("retention", DEFAULT_RETENTION), max_len=20
    ).lower()
    if retention not in RETENTION_CLASSES:
        return None
    expires_at = payload.get("expires_at")
    expires_text = _bounded_text(expires_at, max_len=64) or None
    if _is_expired(expires_text, now):
        return None
    relationship = _bounded_text(
        payload.get("relationship") or payload.get("interaction") or "interaction",
        max_len=40,
    ).lower()
    if not relationship.replace("_", "").replace("-", "").isalnum():
        relationship = "interaction"
    trust_delta = _as_float(
        payload.get("trust_delta", payload.get("trust", 0.0)),
        default=0.0,
        lo=-MAX_TRUST_DELTA,
        hi=MAX_TRUST_DELTA,
    )
    event_id = _event_id(event)
    record_id = "soc-" + hashlib.sha1(
        f"{event_id}:{'|'.join(participants)}:{scope}:{scope_id}".encode("utf-8")
    ).hexdigest()[:16]
    aggregate = _bounded_text(event.get("aggregate_id"), max_len=200)
    provenance: Dict[str, Any] = {
        "source": "interaction_event",
        "event_type": _event_type(event),
        "event_ids": [event_id][:MAX_PROVENANCE_IDS],
        "projection": SCHEMA,
    }
    if aggregate:
        provenance["aggregate_ids"] = [aggregate][:MAX_PROVENANCE_IDS]
    if department:
        provenance["department"] = department
    tenant_id = _bounded_text(
        payload.get("tenant_id") or event.get("tenant_id") or DEFAULT_TENANT,
        max_len=80,
    )
    return SocialMemoryRecord(
        record_id=record_id,
        tenant_id=tenant_id or DEFAULT_TENANT,
        scope=scope,
        scope_id=scope_id,
        participants=participants,
        relationship=relationship,
        trust_delta=trust_delta,
        provenance=provenance,
        visibility=visibility,
        retention=retention,
        expires_at=expires_text,
        source_event_ids=(event_id,),
        created_at=_stamp(
            event.get("occurred_at", event.get("timestamp", event.get("ts")))
        ),
    )


def filter_social_records(
    records: Sequence[SocialMemoryRecord],
    *,
    acl: Optional[SocialACL],
    principal: Any,
    strict: bool = False,
) -> Tuple[List[SocialMemoryRecord], int]:
    """Return ``(readable, denied_count)``; ``strict`` raises on the first denial."""
    if acl is None:
        return list(records), 0
    readable: List[SocialMemoryRecord] = []
    denied = 0
    for record in records:
        if acl.can_read(principal, record):
            readable.append(record)
        else:
            denied += 1
            if strict:
                raise SocialScopeDenied(
                    f"social record {record.record_id!r} is outside the"
                    f" viewer scope ({record.scope}:{record.scope_id})"
                )
    return readable, denied


def require_social_read(
    record: SocialMemoryRecord, acl: SocialACL, principal: Any
) -> None:
    if not acl.can_read(principal, record):
        raise SocialScopeDenied(
            f"social record {record.record_id!r} is outside the viewer scope"
            f" ({record.scope}:{record.scope_id})"
        )


# ---------------------------------------------------------------- projection
@dataclass(frozen=True)
class OutsideLife:
    """A derived, presentation-only view of outside life and the social graph."""

    map_digest: str
    environment: Dict[str, Any]
    ambient: Tuple[Dict[str, Any], ...]
    social: Dict[str, Any]
    zones: Tuple[Dict[str, Any], ...]
    summary: Dict[str, Any]
    constraints: Dict[str, Any]
    ignored: Tuple[Dict[str, Any], ...]
    credit: str
    schema: str = SCHEMA
    version: int = VERSION
    mode: str = "presentation"
    business_truth: bool = False
    digest: str = ""

    def zone(self, zone_id: str) -> Dict[str, Any]:
        for zone in self.zones:
            if zone["zone_id"] == zone_id:
                return zone
        raise OutsideLifeError(f"unknown active zone {zone_id!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "version": int(self.version),
            "mode": self.mode,
            "business_truth": False,
            "map_digest": self.map_digest,
            "digest": self.digest,
            "credit": self.credit,
            "environment": _strip_digest(self.environment),
            "ambient": [_strip_digest(row) for row in self.ambient],
            "social": _strip_digest(self.social),
            "zones": [_strip_digest(row) for row in self.zones],
            "summary": dict(self.summary),
            "constraints": dict(self.constraints),
            "ignored": [_strip_digest(row) for row in self.ignored],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutsideLife":
        return cls(
            map_digest=str(data.get("map_digest", "")),
            environment=dict(data.get("environment", {}) or {}),
            ambient=tuple(dict(row) for row in data.get("ambient", ())),
            social=dict(data.get("social", {}) or {}),
            zones=tuple(dict(row) for row in data.get("zones", ())),
            summary=dict(data.get("summary", {}) or {}),
            constraints=dict(data.get("constraints", {}) or {}),
            ignored=tuple(dict(row) for row in data.get("ignored", ())),
            credit=str(data.get("credit", "")),
            schema=str(data.get("schema", SCHEMA)),
            version=int(data.get("version", VERSION)),
            mode=str(data.get("mode", "presentation")),
            business_truth=bool(data.get("business_truth", False)),
            digest=str(data.get("digest", "")),
        )


def _environment_projection(
    events: Sequence[Mapping[str, Any]],
    map_obj: _exterior.ExteriorMap,
    cues: Mapping[Tuple[str, str], Mapping[str, Any]],
    ignored: List[Dict[str, Any]],
) -> Dict[str, Any]:
    declared = {
        "weather": set(map_obj.variants.weather),
        "day_night": set(map_obj.variants.day_night),
        "season": set(map_obj.variants.seasons),
    }
    transitions: List[Dict[str, Any]] = []
    current: Dict[str, Optional[str]] = {
        "weather": None,
        "day_night": None,
        "season": None,
    }
    for event in events:
        etype = _event_type(event)
        if etype not in ENVIRONMENT_EVENT_TYPES:
            continue
        payload = _event_payload(event)
        if etype == "environment":
            updates = [
                (kind, payload.get(kind))
                for kind in ("weather", "day_night", "season")
                if payload.get(kind) is not None
            ]
        else:
            kind = etype.split(".", 1)[0]
            value = payload.get("value", payload.get(kind))
            updates = [(kind, value)]
        for kind, value in updates:
            if kind not in declared:
                ignored.append(
                    {
                        "event_id": _event_id(event),
                        "reason": "unknown_environment_kind",
                        "detail": kind,
                    }
                )
                continue
            clean = _bounded_text(value, max_len=40).lower()
            if clean not in declared[kind]:
                ignored.append(
                    {
                        "event_id": _event_id(event),
                        "reason": "unknown_environment_value",
                        "detail": f"{kind}={clean}",
                    }
                )
                continue
            current[kind] = clean
            cue = cues.get((kind, clean), {})
            transitions.append(
                {
                    "event_id": _event_id(event),
                    "kind": kind,
                    "value": clean,
                    "occurred_at": _stamp(
                        event.get(
                            "occurred_at",
                            event.get("timestamp", event.get("ts")),
                        )
                    ),
                    "label": str(cue.get("label") or clean),
                    "cue": str(cue.get("cue") or ""),
                    "still_cue": str(cue.get("still_cue") or ""),
                    "animated": bool(
                        cue.get("cue") and cue.get("still_cue")
                        and cue.get("cue") != cue.get("still_cue")
                    ),
                }
            )
    observed = bool(transitions)
    return {
        "observed": observed,
        "weather": current["weather"],
        "day_night": current["day_night"],
        "season": current["season"],
        "transitions": transitions[-MAX_TRANSITIONS:],
        "transitions_total": len(transitions),
    }


def _ambient_projection(
    events: Sequence[Mapping[str, Any]],
    map_obj: _exterior.ExteriorMap,
    bounds: Mapping[str, Mapping[str, int]],
    now: Any,
    ignored: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], bool]:
    active_zones = {
        zone.zone_id: zone for zone in map_obj.zones if zone.active
    }
    rows: List[Dict[str, Any]] = []
    truncated = False
    for event in events:
        etype = _event_type(event)
        if etype not in AMBIENT_EVENT_TYPES:
            continue
        payload = _event_payload(event)
        zone_id = _bounded_text(event.get("zone_id") or payload.get("zone_id"),
                                max_len=80)
        if not zone_id:
            ignored.append(
                {"event_id": _event_id(event), "reason": "missing_zone"}
            )
            continue
        zone = active_zones.get(zone_id)
        if zone is None:
            any_zone = any(z.zone_id == zone_id for z in map_obj.zones)
            ignored.append(
                {
                    "event_id": _event_id(event),
                    "reason": "inactive_zone" if any_zone else "unknown_zone",
                    "detail": zone_id,
                }
            )
            continue
        if _is_expired(payload.get("expires_at"), now):
            ignored.append(
                {"event_id": _event_id(event), "reason": "expired",
                 "detail": zone_id}
            )
            continue
        kind = "vehicle" if "vehicle" in etype else "visitor"
        if etype == "outside.ambient":
            declared_kind = _bounded_text(payload.get("kind"), max_len=40).lower()
            kind = declared_kind if declared_kind in _AMBIENT_CUES else "visitor"
        count = payload.get("count", 1)
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            count_int = 1
        count_int = max(1, min(MAX_AMBIENT_PER_EVENT, count_int))
        variant = _bounded_text(payload.get("variant"), max_len=40).lower()
        if not variant:
            variant = kind
        cue, still = _AMBIENT_CUES.get(
            variant, _AMBIENT_DEFAULT_CUE[kind]
        )
        rect = bounds.get(zone.zone_id, {"x": 0, "y": 0, "width": 1,
                                          "height": 1})
        width = max(1, int(rect["width"]))
        height = max(1, int(rect["height"]))
        for index in range(count_int):
            if len(rows) >= MAX_AMBIENT:
                truncated = True
                break
            seed = hashlib.sha1(
                f"{_event_id(event)}:{index}".encode("utf-8")
            ).hexdigest()
            x = int(rect["x"]) + int(seed[0:8], 16) % width
            y = int(rect["y"]) + int(seed[8:16], 16) % height
            rows.append(
                {
                    "sprite_id": f"outside-{seed[:12]}",
                    "event_id": _event_id(event),
                    "zone_id": zone.zone_id,
                    "kind": kind,
                    "variant": variant,
                    "label": _bounded_text(
                        payload.get("label") or variant.title(), max_len=80
                    ),
                    "x": x,
                    "y": y,
                    "layer": "ambient",
                    "z_index": 20,
                    "cue": cue,
                    "still_cue": still,
                    "animated": bool(zone.animate and cue != still),
                    "presentation_only": True,
                }
            )
        if truncated:
            break
    return rows, truncated


def _social_projection(
    events: Sequence[Mapping[str, Any]],
    *,
    acl: Optional[SocialACL],
    principal: Any,
    now: Any,
    strict_acl: bool,
    ignored: List[Dict[str, Any]],
) -> Dict[str, Any]:
    records: List[SocialMemoryRecord] = []
    for event in events:
        if _event_type(event) not in INTERACTION_EVENT_TYPES:
            continue
        if len(records) >= MAX_SOCIAL_RECORDS:
            ignored.append(
                {"event_id": _event_id(event),
                 "reason": "social_record_cap_reached"}
            )
            break
        record = _interaction_record(event, now=now)
        if record is None:
            ignored.append(
                {"event_id": _event_id(event),
                 "reason": "invalid_interaction_event"}
            )
            continue
        records.append(record)

    readable, denied = filter_social_records(
        records, acl=acl, principal=principal, strict=strict_acl
    )
    readable = sorted(
        readable, key=lambda item: (item.created_at, item.record_id)
    )
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for record in readable:
        participants = list(record.participants)
        for left_index in range(len(participants)):
            for right_index in range(left_index + 1, len(participants)):
                a, b = participants[left_index], participants[right_index]
                key = (a, b) if a <= b else (b, a)
                edge = edges.setdefault(
                    key,
                    {
                        "edge_id": "rel-"
                        + hashlib.sha1(f"{key[0]}|{key[1]}".encode()).hexdigest()[:16],
                        "participants": [key[0], key[1]],
                        "interactions": 0,
                        "trust": 0.0,
                        "friendship_events": 0,
                        "rivalry_events": 0,
                        "last_event_id": "",
                    },
                )
                edge["interactions"] += 1
                edge["trust"] = max(
                    -MAX_TRUST, min(MAX_TRUST, edge["trust"] + record.trust_delta)
                )
                if record.relationship in _FRIENDLY_RELATIONSHIPS:
                    edge["friendship_events"] += 1
                elif record.relationship in _HOSTILE_RELATIONSHIPS:
                    edge["rivalry_events"] += 1
                edge["last_event_id"] = record.source_event_ids[-1]

    edge_rows: List[Dict[str, Any]] = []
    node_totals: Dict[str, Dict[str, Any]] = {}
    for edge in sorted(edges.values(), key=lambda item: item["edge_id"]):
        if len(edge_rows) >= MAX_SOCIAL_EDGES:
            break
        trust = round(edge["trust"], 4)
        if edge["friendship_events"] > edge["rivalry_events"] and trust >= 0:
            classification = "friendship"
        elif edge["rivalry_events"] > edge["friendship_events"] or trust < 0:
            classification = "rivalry"
        else:
            classification = "neutral"
        row = {
            "edge_id": edge["edge_id"],
            "participants": list(edge["participants"]),
            "interactions": int(edge["interactions"]),
            "trust": trust,
            "friendship_events": int(edge["friendship_events"]),
            "rivalry_events": int(edge["rivalry_events"]),
            "classification": classification,
            "last_event_id": edge["last_event_id"],
        }
        edge_rows.append(row)
        for participant in edge["participants"]:
            node = node_totals.setdefault(
                participant,
                {"node_id": participant, "degree": 0, "trust": 0.0},
            )
            node["degree"] += 1
            node["trust"] = max(
                -MAX_TRUST, min(MAX_TRUST, node["trust"] + trust)
            )
    node_rows = [
        {
            "node_id": node["node_id"],
            "degree": int(node["degree"]),
            "trust": round(node["trust"], 4),
        }
        for node in sorted(node_totals.values(), key=lambda item: item["node_id"])
    ][:MAX_SOCIAL_NODES]

    records_payload = [record.to_payload() for record in readable]
    return {
        "observed": bool(records_payload),
        "records": records_payload,
        "edges": edge_rows,
        "nodes": node_rows,
        "denied": int(denied),
        "summary": {
            "records": len(records_payload),
            "edges": len(edge_rows),
            "nodes": len(node_rows),
            "denied": int(denied),
            "friendships": sum(
                1 for edge in edge_rows if edge["classification"] == "friendship"
            ),
            "rivalries": sum(
                1 for edge in edge_rows if edge["classification"] == "rivalry"
            ),
        },
    }


def build_outside_life(
    source_events: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    map_obj: Optional[_exterior.ExteriorMap] = None,
    acl: Optional[SocialACL] = None,
    principal: Any = None,
    now: Any = None,
    strict_acl: bool = False,
) -> OutsideLife:
    """Build the deterministic outside-life projection from source events.

    With no ``source_events`` the projection is empty: no environment, no
    ambient sprites and no social graph. Nothing is defaulted in.
    """
    world_map = map_obj or _exterior.build_exterior_map()
    if source_events is None:
        events: List[Mapping[str, Any]] = []
    else:
        events = [event for event in source_events if isinstance(event, Mapping)]
        if len(events) != len(source_events):
            raise OutsideLifeError("source events must be mappings")
    events.sort(key=_sort_key)
    truncated = len(events) > MAX_EVENTS
    events = events[:MAX_EVENTS]

    cues = _cue_index(world_map)
    bounds = _zone_bounds(world_map)
    ignored: List[Dict[str, Any]] = []
    if truncated:
        ignored.append(
            {
                "event_id": "",
                "reason": "event_cap_reached",
                "detail": str(MAX_EVENTS),
            }
        )

    environment = _environment_projection(events, world_map, cues, ignored)
    ambient, ambient_truncated = _ambient_projection(
        events, world_map, bounds, now, ignored
    )
    social = _social_projection(
        events,
        acl=acl,
        principal=principal,
        now=now,
        strict_acl=strict_acl,
        ignored=ignored,
    )

    active_zones = [zone for zone in world_map.zones if zone.active]
    ambient_by_zone: Dict[str, int] = {}
    for row in ambient:
        ambient_by_zone[row["zone_id"]] = ambient_by_zone.get(row["zone_id"], 0) + 1
    zone_rows = [
        {
            "zone_id": zone.zone_id,
            "kind": zone.kind,
            "label": zone.label,
            "state": "active",
            "animated": bool(zone.animate),
            "bounds": dict(bounds[zone.zone_id]),
            "ambient": ambient_by_zone.get(zone.zone_id, 0),
        }
        for zone in active_zones
    ]

    observed_total = int(environment["observed"]) + len(ambient) + len(
        social["records"]
    )
    summary = {
        "source_events": len(events),
        "truncated": bool(truncated),
        "environment_observed": bool(environment["observed"]),
        "environment_transitions": int(environment["transitions_total"]),
        "ambient": len(ambient),
        "ambient_truncated": bool(ambient_truncated),
        "active_zones": len(zone_rows),
        "social_records": len(social["records"]),
        "social_edges": len(social["edges"]),
        "social_nodes": len(social["nodes"]),
        "social_denied": int(social["denied"]),
        "observed": observed_total > 0,
        "ignored": len(ignored),
    }
    constraints = {
        "presentation_only": True,
        "business_truth": False,
        "simulation_only": True,
        "source_driven": True,
        "no_activity_without_events": True,
        "active_zones_only": True,
        "deterministic": True,
        "reduced_motion_supported": True,
        "max_events": MAX_EVENTS,
        "max_ambient": MAX_AMBIENT,
        "max_ambient_per_event": MAX_AMBIENT_PER_EVENT,
        "max_social_records": MAX_SOCIAL_RECORDS,
        "max_social_edges": MAX_SOCIAL_EDGES,
        "max_social_nodes": MAX_SOCIAL_NODES,
        "acl_scoped": bool(acl is not None),
    }
    life = OutsideLife(
        map_digest=world_map.digest,
        environment=environment,
        ambient=tuple(ambient),
        social=social,
        zones=tuple(zone_rows),
        summary=summary,
        constraints=constraints,
        ignored=tuple(ignored),
        credit=world_map.credit,
    )
    payload = life.to_dict()
    payload["digest"] = compute_digest(payload)
    return OutsideLife.from_dict(payload)


def outside_life_payload(life: OutsideLife) -> Dict[str, Any]:
    """The bounded JSON payload a client reads (projection + summary)."""
    return {
        "ok": not validate_outside_life(life),
        "schema": SCHEMA,
        "version": VERSION,
        "digest": life.digest,
        "summary": dict(life.summary),
        "life": life.to_dict(),
    }


# ---------------------------------------------------------------- validation
def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_keys(item)


def validate_outside_life(
    life: OutsideLife,
    *,
    map_obj: Optional[_exterior.ExteriorMap] = None,
) -> List[str]:
    """Return every structural defect in the projection, or ``[]``."""
    world_map = map_obj or _exterior.build_exterior_map()
    problems: List[str] = []

    if life.schema != SCHEMA:
        problems.append(f"schema: {life.schema!r} != {SCHEMA!r}")
    if life.version != VERSION:
        problems.append(f"version: {life.version} != {VERSION}")
    if life.mode != "presentation" or life.business_truth is not False:
        problems.append("mode: projection must be presentation-only")
    if life.map_digest != world_map.digest:
        problems.append("map_digest: does not match the exterior map")
    if life.digest and life.digest != compute_digest(life.to_dict()):
        problems.append("digest: does not match the serialised projection")

    # -- environment ------------------------------------------------------
    environment = life.environment or {}
    observed = bool(environment.get("observed"))
    transitions = list(environment.get("transitions") or [])
    if not observed:
        if transitions:
            problems.append("environment: unobserved state carries transitions")
        for kind in ("weather", "day_night", "season"):
            if environment.get(kind) is not None:
                problems.append(
                    f"environment.{kind}: invented without a source event"
                )
    else:
        if not transitions:
            problems.append("environment: observed state has no transitions")
        declared = {
            "weather": set(world_map.variants.weather),
            "day_night": set(world_map.variants.day_night),
            "season": set(world_map.variants.seasons),
        }
        for transition in transitions:
            kind = transition.get("kind")
            value = transition.get("value")
            if kind not in declared or value not in declared[kind]:
                problems.append(
                    f"environment transition: unknown {kind}={value!r}"
                )
            if not str(transition.get("cue") or "").strip():
                problems.append("environment transition: missing cue")
            if not str(transition.get("still_cue") or "").strip():
                problems.append("environment transition: missing still cue")
        for kind in ("weather", "day_night", "season"):
            values = [
                transition.get("value")
                for transition in transitions
                if transition.get("kind") == kind
            ]
            expected = values[-1] if values else None
            if environment.get(kind) != expected:
                problems.append(
                    f"environment.{kind}: {environment.get(kind)!r} does not"
                    f" match the last transition {expected!r}"
                )
    if not str(life.credit or "").strip():
        problems.append("credit: the LimeZu credit is required")

    # -- ambient ----------------------------------------------------------
    active_zones = {zone.zone_id for zone in world_map.zones if zone.active}
    if len(life.ambient) > MAX_AMBIENT:
        problems.append(
            f"ambient: {len(life.ambient)} exceed max {MAX_AMBIENT}"
        )
    for sprite in life.ambient:
        where = f"ambient {sprite.get('sprite_id')!r}"
        if sprite.get("zone_id") not in active_zones:
            problems.append(f"{where}: outside an active zone")
        if not sprite.get("presentation_only"):
            problems.append(f"{where}: not marked presentation-only")
        if not str(sprite.get("cue") or "").strip():
            problems.append(f"{where}: missing cue")
        if not str(sprite.get("still_cue") or "").strip():
            problems.append(f"{where}: missing reduced-motion still cue")

    # -- social -----------------------------------------------------------
    social = life.social or {}
    records = list(social.get("records") or [])
    edges = list(social.get("edges") or [])
    nodes = list(social.get("nodes") or [])
    if len(records) > MAX_SOCIAL_RECORDS:
        problems.append(
            f"social: {len(records)} records exceed max {MAX_SOCIAL_RECORDS}"
        )
    if len(edges) > MAX_SOCIAL_EDGES:
        problems.append(
            f"social: {len(edges)} edges exceed max {MAX_SOCIAL_EDGES}"
        )
    if len(nodes) > MAX_SOCIAL_NODES:
        problems.append(
            f"social: {len(nodes)} nodes exceed max {MAX_SOCIAL_NODES}"
        )
    for record in records:
        where = f"social record {record.get('record_id')!r}"
        if record.get("scope") not in SCOPES:
            problems.append(f"{where}: bad scope {record.get('scope')!r}")
        if not str(record.get("scope_id") or "").strip():
            problems.append(f"{where}: missing scope_id")
        if not str(record.get("tenant_id") or "").strip():
            problems.append(f"{where}: missing tenant_id")
        if record.get("visibility") not in VISIBILITIES:
            problems.append(
                f"{where}: bad visibility {record.get('visibility')!r}"
            )
        if record.get("retention") not in RETENTION_CLASSES:
            problems.append(
                f"{where}: bad retention {record.get('retention')!r}"
            )
        if record.get("simulation_only") is not True:
            problems.append(f"{where}: must be simulation-only")
        source_ids = list(record.get("source_event_ids") or [])
        provenance = record.get("provenance") or {}
        if not source_ids:
            problems.append(f"{where}: missing source_event_ids")
        if not provenance.get("event_ids"):
            problems.append(f"{where}: missing provenance event_ids")
        if len(source_ids) > MAX_PROVENANCE_IDS:
            problems.append(f"{where}: provenance exceeds the bounded list")
        trust_delta = float(record.get("trust_delta", 0.0) or 0.0)
        if trust_delta > MAX_TRUST_DELTA or trust_delta < -MAX_TRUST_DELTA:
            problems.append(f"{where}: trust delta out of bounds")
    for edge in edges:
        participants = list(edge.get("participants") or [])
        if len(participants) != 2:
            problems.append(
                f"social edge {edge.get('edge_id')!r}: needs two participants"
            )
        if edge.get("classification") not in (
            "friendship", "rivalry", "neutral"
        ):
            problems.append(
                f"social edge {edge.get('edge_id')!r}: bad classification"
            )

    # -- constraints / summary -------------------------------------------
    constraints = life.constraints or {}
    for key in (
        "presentation_only",
        "simulation_only",
        "source_driven",
        "no_activity_without_events",
        "active_zones_only",
        "deterministic",
    ):
        if constraints.get(key) is not True:
            problems.append(f"constraints: {key} must be true")
    if constraints.get("business_truth") is not False:
        problems.append("constraints: business_truth must be false")
    if life.summary.get("ambient") != len(life.ambient):
        problems.append("summary: ambient disagrees with ambient rows")
    if life.summary.get("social_records") != len(records):
        problems.append("summary: social_records disagrees with records")
    if life.summary.get("observed") != bool(
        environment.get("observed") or life.ambient or records
    ):
        problems.append("summary: observed disagrees with the projection")

    # -- no business truth -----------------------------------------------
    forbidden = sorted(
        {key for key in _walk_keys(life.to_dict()) if key in FORBIDDEN_TRUTH_KEYS}
    )
    if forbidden:
        problems.append(
            f"presentation: forbidden business-truth keys {forbidden}"
        )
    return problems
