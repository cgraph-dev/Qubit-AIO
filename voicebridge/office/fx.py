"""Event-to-FX projection for Qubit Office (P7-04).

WHAT THIS IS
------------
The authoritative-event consumer behind the office animation layer. It maps a
committed Business OS event (``bos_event``) to one deterministic *presentation*
record — an :class:`FXEvent` — carrying the animation, emote, particle, sound,
stable seed, priority, cancellation policy and an explicit reduced-motion still
cue. It also provides the bounded priority queue, cancel/replace, dedupe and a
deterministic replay projection the client planner mirrors.

WHAT THIS IS NOT
----------------
It is not a second source of business truth and never creates business state.
FX is a projection: every field comes from the authoritative event that was
already committed. There is no clock, no store, no network and no random module
in this file — the same input produces byte-identical output on every run. An
event that is empty, unknown, or carries no FX rule produces **no** effect, so
ambient "work" can never be invented by animation.

Reduced motion and nonessential effects
---------------------------------------
Every rule declares whether it is ``essential`` (a critical status cue such as
completed/failed/unknown/approval/revoked). Under reduced motion, nonessential
effects are dropped and essential ones render their static ``still`` cue — the
status channel is never removed, only its motion is. ``disable_nonessential``
drops every nonessential effect independently of reduced motion.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Tuple
from collections import deque

__all__ = [
    "SCHEMA",
    "VERSION",
    "MODE",
    "DEFAULT_CAPACITY",
    "MAX_REPLAY",
    "SEED_LIMIT",
    "PRIORITY_CRITICAL",
    "PRIORITY_HIGH",
    "PRIORITY_NORMAL",
    "PRIORITY_LOW",
    "CANCELLATIONS",
    "FORBIDDEN_TRUTH_KEYS",
    "EVENT_RULES",
    "FX_RULE_TYPES",
    "FX_INPUT_TYPES",
    "FXError",
    "FXRules",
    "FXEvent",
    "FXQueue",
    "normalize_event",
    "map_event",
    "map_events",
    "replay",
    "seed_for",
    "fx_event_id_for",
    "subject_key",
    "validate_fx_event",
    "build_fx_payload",
    "validate_fx_payload",
]

SCHEMA = "qubit-office-fx/1"
VERSION = 1
MODE = "presentation"

#: Bounded queue default. A burst cannot grow the renderer backlog past this.
DEFAULT_CAPACITY = 32
#: Replay is bounded too; historical reconstruction never scans without a cap.
MAX_REPLAY = 256
#: Seeds are 31-bit so they are safe in JS without a BigInt conversion.
SEED_LIMIT = 2**31

#: Lower number = played sooner. Status cues outrank decoration.
PRIORITY_CRITICAL = 0
PRIORITY_HIGH = 10
PRIORITY_NORMAL = 20
PRIORITY_LOW = 30

CANCELLATIONS = ("none", "replace", "cancel")

#: An FX record may not grow any of these. They would make the projection a
#: second source of business truth. ``room``/``agent`` are presentation scope,
#: not truth, so they are allowed.
FORBIDDEN_TRUTH_KEYS: Tuple[str, ...] = (
    "business_id",
    "tenant_id",
    "legal_entity_id",
    "payload",
    "receipt_id",
    "artifact_id",
    "working",
    "completed",
)


class FXError(ValueError):
    """A typed FX refusal. ``code`` is stable and testable."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.detail}"


# --------------------------------------------------------------------------- rules
@dataclass(frozen=True, slots=True)
class FXRules:
    """One authoritative event type's deterministic presentation mapping."""

    animation: str
    still: str
    emote: Optional[str]
    particles: Optional[str]
    sound: Optional[str]
    priority: int
    essential: bool
    cancellation: str
    fallback: str
    group: str  # "agent" | "room" | "office"


def _r(
    animation: str,
    still: str,
    *,
    emote: Optional[str] = None,
    particles: Optional[str] = None,
    sound: Optional[str] = None,
    priority: int = PRIORITY_NORMAL,
    essential: bool = False,
    cancellation: str = "none",
    fallback: Optional[str] = None,
    group: str = "agent",
) -> FXRules:
    return FXRules(
        animation=animation,
        still=still,
        emote=emote,
        particles=particles,
        sound=sound,
        priority=priority,
        essential=essential,
        cancellation=cancellation,
        fallback=fallback or still,
        group=group,
    )


#: Event type -> rule. These are the authoritative ``bos_event.type`` values
#: emitted by the Business OS modules; a type absent here is a no-op FX.
EVENT_RULES: Dict[str, FXRules] = {
    # -- agent turn / run lifecycle -------------------------------------
    "agent.turn.started": _r("typing", "typing-still", emote="focus"),
    "agent.turn.completed": _r(
        "celebrate", "status-check", emote="done", particles="confetti",
        sound="chime", priority=PRIORITY_HIGH, essential=True,
        cancellation="replace", fallback="status-badge",
    ),
    "agent.turn.failed": _r(
        "error", "status-error", emote="error", particles=None,
        sound="error", priority=PRIORITY_CRITICAL, essential=True,
        cancellation="replace", fallback="status-badge",
    ),
    "agent.turn.unknown": _r(
        "unknown", "status-unknown", emote="unknown", particles=None,
        sound=None, priority=PRIORITY_CRITICAL, essential=True,
        cancellation="replace", fallback="status-badge",
    ),
    "agent.turn.reconciled": _r(
        "reconcile", "status-reconciled", emote="reconciled",
        priority=PRIORITY_HIGH, essential=True, cancellation="replace",
        fallback="status-badge",
    ),
    "agent.turn.cancelled": _r(
        "stop", "status-cancelled", emote="stopped", priority=PRIORITY_HIGH,
        essential=True, cancellation="cancel", fallback="status-badge",
    ),
    "agent.turn.tool.completed": _r("tool-done", "tool-done-still",
                                    emote="tool"),
    "agent.turn.tool.failed": _r(
        "tool-error", "tool-error-still", emote="error", sound="error",
        priority=PRIORITY_HIGH, essential=True, fallback="status-badge",
    ),
    "agent.turn.tool.denied": _r(
        "denied", "status-denied", emote="denied", priority=PRIORITY_HIGH,
        essential=True, fallback="status-badge",
    ),
    "agent.turn.tool.replayed": _r("tool-replay", "tool-replay-still",
                                   emote="replay", priority=PRIORITY_LOW),
    "agent.turn.tool.deduped": _r("tool-dedupe", "tool-dedupe-still",
                                  priority=PRIORITY_LOW),

    # -- approvals ------------------------------------------------------
    "approval.requested": _r(
        "approval", "status-approval", emote="approval", particles="ring",
        sound="alert", priority=PRIORITY_CRITICAL, essential=True,
        fallback="status-badge",
    ),
    "approval.granted": _r(
        "approve", "status-granted", emote="granted", sound="chime",
        priority=PRIORITY_HIGH, essential=True, cancellation="replace",
        fallback="status-badge",
    ),
    "approval.denied": _r(
        "deny", "status-denied", emote="denied", sound="error",
        priority=PRIORITY_CRITICAL, essential=True, cancellation="replace",
        fallback="status-badge",
    ),
    "approval.expired": _r(
        "expired", "status-expired", emote="expired",
        priority=PRIORITY_HIGH, essential=True, cancellation="replace",
        fallback="status-badge",
    ),

    # -- tasks / delegation --------------------------------------------
    "task.created": _r("walk-to-desk", "walk-still", priority=PRIORITY_LOW),
    "task.claimed": _r("typing", "typing-still", emote="focus"),
    "task.transitioned": _r("status-shift", "status-shift-still",
                            priority=PRIORITY_LOW),
    "task.handoff": _r("handoff", "handoff-still", emote="handoff"),
    "task.handed_off": _r("handoff", "handoff-still", emote="handoff"),
    "delegation.created": _r("handoff", "handoff-still", emote="delegated"),
    "delegation.completed": _r("celebrate", "status-check", emote="done",
                               particles="confetti"),
    "delegation.escalated": _r(
        "escalate", "status-escalated", emote="escalated",
        priority=PRIORITY_HIGH, essential=True, fallback="status-badge",
    ),

    # -- artifacts / receipts / finance --------------------------------
    "artifact.created": _r("deliver", "deliver-still", emote="artifact"),
    "receipt.recorded": _r("receipt", "receipt-still", emote="receipt"),
    "finance.posting.posted": _r("posting", "posting-still",
                                 emote="posted"),
    "finance.receipt.recorded": _r("receipt", "receipt-still",
                                   emote="receipt"),
    "finance.payment.completed": _r(
        "receipt", "status-check", emote="paid", sound="chime",
        priority=PRIORITY_HIGH, essential=True, cancellation="replace",
        fallback="status-badge",
    ),
    "finance.payment.failed": _r(
        "error", "status-error", emote="failed", sound="error",
        priority=PRIORITY_CRITICAL, essential=True, cancellation="replace",
        fallback="status-badge",
    ),
    "finance.payment.unknown": _r(
        "unknown", "status-unknown", emote="unknown",
        priority=PRIORITY_CRITICAL, essential=True, cancellation="replace",
        fallback="status-badge",
    ),

    # -- marketing / crm / memory --------------------------------------
    "marketing.published": _r("publish", "publish-still", emote="published",
                              particles="sparkle"),
    "marketing.publication.failed": _r(
        "error", "status-error", emote="failed", priority=PRIORITY_HIGH,
        essential=True, cancellation="replace", fallback="status-badge",
    ),
    "marketing.publication.unknown": _r(
        "unknown", "status-unknown", emote="unknown",
        priority=PRIORITY_HIGH, essential=True, cancellation="replace",
        fallback="status-badge",
    ),
    "crm.support.ticket_opened": _r("ticket", "ticket-still", emote="ticket"),
    "crm.support.message": _r("talk", "talk-still", emote="message"),
    "memory.recorded": _r("memory-write", "memory-still", emote="memory",
                          priority=PRIORITY_LOW),
    "memory.forgotten": _r("memory-clear", "memory-clear-still",
                           priority=PRIORITY_LOW),
    "memory.quarantined": _r(
        "warning", "status-warning", emote="quarantined",
        priority=PRIORITY_HIGH, essential=True, fallback="status-badge",
    ),

    # -- agent lifecycle ------------------------------------------------
    "agent.created": _r("arrive", "arrive-still", emote="joined",
                        particles="sparkle"),
    "agent.create": _r("arrive", "arrive-still", emote="joined",
                       particles="sparkle"),
    "agent.paused": _r(
        "pause", "status-paused", emote="paused", priority=PRIORITY_HIGH,
        essential=True, cancellation="replace", fallback="status-badge",
    ),
    "agent.resumed": _r("resume", "status-resumed", emote="resumed"),
    "agent.stopped": _r(
        "stop", "status-stopped", emote="stopped", priority=PRIORITY_CRITICAL,
        essential=True, cancellation="cancel", fallback="status-badge",
    ),
    "agent.revoked": _r(
        "revoked", "status-revoked", emote="revoked",
        priority=PRIORITY_CRITICAL, essential=True, cancellation="cancel",
        fallback="status-badge",
    ),
    "agent.quarantine": _r(
        "quarantine", "status-quarantined", emote="quarantined",
        priority=PRIORITY_CRITICAL, essential=True, cancellation="replace",
        fallback="status-badge",
    ),
    "agent.archived": _r("farewell", "farewell-still", emote="archived",
                         priority=PRIORITY_LOW),
    "agent.archive": _r("farewell", "farewell-still", emote="archived",
                        priority=PRIORITY_LOW),
}

#: The authoritative types this module knows how to project (ordered).
FX_RULE_TYPES: Tuple[str, ...] = tuple(sorted(EVENT_RULES))
#: The same set as a frozenset, for membership checks in tests/tools.
FX_INPUT_TYPES = frozenset(EVENT_RULES)


# --------------------------------------------------------------------------- model
@dataclass(frozen=True, slots=True)
class FXEvent:
    """One deterministic presentation effect derived from one authoritative event."""

    fx_event_id: str
    event_id: str
    event_type: str
    room: Optional[str]
    agent: Optional[str]
    animation: str
    still: str
    emote: Optional[str]
    particles: Optional[str]
    sound: Optional[str]
    seed: int
    priority: int
    cancellation: str
    replay: bool
    reduced_motion: bool
    fallback: str
    essential: bool
    group: str
    mode: str = MODE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "mode": self.mode,
            "fx_event_id": self.fx_event_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "room": self.room,
            "agent": self.agent,
            "animation": self.animation,
            "still": self.still,
            "emote": self.emote,
            "particles": self.particles,
            "sound": self.sound,
            "seed": self.seed,
            "priority": self.priority,
            "cancellation": self.cancellation,
            "replay": self.replay,
            "reduced_motion": self.reduced_motion,
            "fallback": self.fallback,
            "essential": self.essential,
            "group": self.group,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FXEvent":
        return cls(
            fx_event_id=_text(data.get("fx_event_id")),
            event_id=_text(data.get("event_id")),
            event_type=_text(data.get("event_type")),
            room=_optional_text(data.get("room")),
            agent=_optional_text(data.get("agent")),
            animation=_text(data.get("animation")),
            still=_text(data.get("still")),
            emote=_optional_text(data.get("emote")),
            particles=_optional_text(data.get("particles")),
            sound=_optional_text(data.get("sound")),
            seed=_int(data.get("seed")),
            priority=_int(data.get("priority"), PRIORITY_NORMAL),
            cancellation=_text(data.get("cancellation")) or "none",
            replay=bool(data.get("replay")),
            reduced_motion=bool(data.get("reduced_motion")),
            fallback=_text(data.get("fallback")),
            essential=bool(data.get("essential")),
            group=_text(data.get("group")) or "agent",
            mode=_text(data.get("mode")) or MODE,
        )


# --------------------------------------------------------------------------- helpers
def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any) -> Optional[str]:
    clean = _text(value)
    return clean or None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def normalize_event(
    event: Any,
) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    """Return ``(event_id, event_type, room, agent)`` or ``None``.

    An empty event (no id/type), a non-mapping value and a payload that is not
    a mapping are all refusals — never a fabricated default.
    """
    if not isinstance(event, Mapping):
        return None
    event_id = _text(event.get("event_id"))
    event_type = _text(event.get("type") or event.get("event_type"))
    if not event_id or not event_type:
        return None
    payload = event.get("payload")
    data: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    room = _optional_text(
        event.get("room") or event.get("room_id")
        or data.get("room") or data.get("room_id")
    )
    agent = _optional_text(
        event.get("agent") or event.get("agent_id")
        or data.get("agent") or data.get("agent_id")
    )
    return event_id, event_type, room, agent


def _digest(parts: Iterable[Any]) -> str:
    joined = "|".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def seed_for(event_id: str, animation: str, agent: Optional[str],
             room: Optional[str]) -> int:
    """Stable 31-bit seed: the same subject/effect always replays identically."""
    raw = _digest((event_id, animation, agent or "", room or ""))
    return int(raw[:16], 16) % SEED_LIMIT


def fx_event_id_for(event_id: str, event_type: str, animation: str,
                   agent: Optional[str], room: Optional[str]) -> str:
    raw = _digest((event_id, event_type, animation, agent or "", room or ""))
    return "fx_" + raw[:24]


def subject_key(event: FXEvent) -> Tuple[str, str]:
    """The cancellation/dedupe scope for an effect."""
    if event.group == "agent":
        return event.group, event.agent or ""
    if event.group == "room":
        return event.group, event.room or ""
    return "office", ""


def map_event(
    event: Any,
    *,
    reduced_motion: bool = False,
    disable_nonessential: bool = False,
    replay: bool = False,
) -> Optional[FXEvent]:
    """Map one authoritative event to one :class:`FXEvent`.

    Returns ``None`` for an empty event, an unknown event type, an unknown
    (non-essential) event when ``disable_nonessential`` is set, or any
    nonessential event under reduced motion. Never invents an effect.
    """
    normalized = normalize_event(event)
    if normalized is None:
        return None
    event_id, event_type, room, agent = normalized
    rule = EVENT_RULES.get(event_type)
    if rule is None:
        return None

    animation = rule.animation
    particles = rule.particles
    if reduced_motion:
        if not rule.essential:
            return None
        animation = rule.still
        particles = None
    if disable_nonessential and not rule.essential:
        return None

    return FXEvent(
        fx_event_id=fx_event_id_for(event_id, event_type, animation, agent, room),
        event_id=event_id,
        event_type=event_type,
        room=room,
        agent=agent,
        animation=animation,
        still=rule.still,
        emote=rule.emote,
        particles=particles,
        sound=rule.sound,
        seed=seed_for(event_id, animation, agent, room),
        priority=rule.priority,
        cancellation=rule.cancellation,
        replay=bool(replay),
        reduced_motion=bool(reduced_motion),
        fallback=rule.fallback,
        essential=rule.essential,
        group=rule.group,
    )


def map_events(
    events: Iterable[Any],
    *,
    reduced_motion: bool = False,
    disable_nonessential: bool = False,
    replay: bool = False,
) -> List[FXEvent]:
    """Map events in order, dropping refusals. Deterministic; never raises."""
    mapped: List[FXEvent] = []
    for event in events:
        fx = map_event(
            event,
            reduced_motion=reduced_motion,
            disable_nonessential=disable_nonessential,
            replay=replay,
        )
        if fx is not None:
            mapped.append(fx)
    return mapped


# --------------------------------------------------------------------------- queue
class FXQueue:
    """A bounded, priority-ordered FX queue with cancel/replace and dedupe.

    Ordering is ``(priority, insertion sequence, fx_event_id)`` so two runs on
    the same input pop the same order. Lower ``priority`` plays first.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if not isinstance(capacity, int) or capacity < 1:
            raise FXError("FX_BAD_CAPACITY", "capacity must be a positive integer")
        self._capacity = capacity
        self._items: Dict[str, FXEvent] = {}
        self._order: Dict[str, int] = {}
        self._seq = 0
        self._seen: Deque[str] = deque(maxlen=max(64, capacity * 4))
        self._seen_set: set[str] = set()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, fx_event_id: object) -> bool:
        return fx_event_id in self._items

    def _remember(self, fx_event_id: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            evicted = self._seen[0]
            self._seen_set.discard(evicted)
        self._seen.append(fx_event_id)
        self._seen_set.add(fx_event_id)

    def submit(self, event: FXEvent) -> str:
        """Enqueue one effect. Returns ``accepted|replaced|duplicate|dropped``."""
        validate_fx_event(event)
        if event.fx_event_id in self._items or event.fx_event_id in self._seen_set:
            return "duplicate"

        removed = 0
        if event.cancellation in ("replace", "cancel"):
            removed = self._cancel_matching(subject_key(event))

        if len(self._items) >= self._capacity:
            worst_id = self._worst()
            worst = self._items[worst_id]
            if event.priority >= worst.priority:
                return "dropped"
            self._remove(worst_id)

        self._items[event.fx_event_id] = event
        self._order[event.fx_event_id] = self._seq
        self._seq += 1
        self._remember(event.fx_event_id)
        return "replaced" if removed else "accepted"

    def _worst(self) -> str:
        return max(
            self._items,
            key=lambda fid: (
                self._items[fid].priority,
                self._order.get(fid, 0),
                fid,
            ),
        )

    def _remove(self, fx_event_id: str) -> Optional[FXEvent]:
        event = self._items.pop(fx_event_id, None)
        self._order.pop(fx_event_id, None)
        return event

    def _cancel_matching(self, key: Tuple[str, str]) -> int:
        doomed = [
            fid for fid, event in self._items.items() if subject_key(event) == key
        ]
        for fid in doomed:
            self._remove(fid)
        return len(doomed)

    def cancel(self, fx_event_id: str) -> bool:
        return self._remove(fx_event_id) is not None

    def cancel_subject(
        self,
        *,
        agent: Optional[str] = None,
        room: Optional[str] = None,
        group: Optional[str] = None,
    ) -> int:
        key = (group or ("agent" if agent else "room"), agent or room or "")
        return self._cancel_matching(key)

    def _sorted(self) -> List[FXEvent]:
        return sorted(
            self._items.values(),
            key=lambda e: (e.priority, self._order.get(e.fx_event_id, 0),
                           e.fx_event_id),
        )

    def peek(self) -> Optional[FXEvent]:
        ordered = self._sorted()
        return ordered[0] if ordered else None

    def pop(self) -> Optional[FXEvent]:
        ordered = self._sorted()
        if not ordered:
            return None
        return self._remove(ordered[0].fx_event_id)

    def drain(self) -> List[FXEvent]:
        ordered = self._sorted()
        self._items.clear()
        self._order.clear()
        return ordered


# --------------------------------------------------------------------------- replay
def replay(
    events: Iterable[Any],
    *,
    reduced_motion: bool = False,
    disable_nonessential: bool = False,
) -> List[FXEvent]:
    """Deterministically replay authoritative events into an ordered FX list.

    Applies cancellation and dedupe in source order, marks every effect
    ``replay=True`` and reuses the same stable seed a live dispatch would use.
    Bounded by :data:`MAX_REPLAY`; no clock, store or network is touched.
    """
    accepted: List[FXEvent] = []
    seen: set[str] = set()
    for raw in events:
        fx = map_event(
            raw,
            reduced_motion=reduced_motion,
            disable_nonessential=disable_nonessential,
            replay=True,
        )
        if fx is None or fx.fx_event_id in seen:
            continue
        if fx.cancellation in ("replace", "cancel"):
            key = subject_key(fx)
            accepted = [item for item in accepted if subject_key(item) != key]
        if len(accepted) >= MAX_REPLAY:
            break
        seen.add(fx.fx_event_id)
        accepted.append(fx)
    return accepted


# --------------------------------------------------------------------------- wire
def validate_fx_event(event: FXEvent) -> None:
    """Validate one effect; raise a typed :class:`FXError` on malformed input."""
    if not isinstance(event, FXEvent):
        raise FXError("FX_BAD_EVENT", "expected FXEvent")
    if not event.fx_event_id:
        raise FXError("FX_BAD_EVENT", "fx_event_id is required")
    if not event.event_id:
        raise FXError("FX_BAD_EVENT", "event_id is required")
    if not event.animation:
        raise FXError("FX_BAD_EVENT", "animation is required")
    if not 0 <= event.seed < SEED_LIMIT:
        raise FXError("FX_BAD_SEED", "seed must be a 31-bit non-negative int")
    if event.cancellation not in CANCELLATIONS:
        raise FXError("FX_BAD_CANCELLATION",
                      f"cancellation must be one of {CANCELLATIONS}")
    if event.group not in ("agent", "room", "office"):
        raise FXError("FX_BAD_GROUP", "group must be agent/room/office")
    if not event.fallback:
        raise FXError("FX_BAD_FALLBACK", "fallback cue is required")
    _assert_no_truth(event.to_dict())


def _assert_no_truth(payload: Mapping[str, Any]) -> None:
    leaked = sorted(key for key in FORBIDDEN_TRUTH_KEYS if key in payload)
    if leaked:
        raise FXError(
            "FX_BUSINESS_TRUTH",
            "FX payload may not carry business truth: " + ", ".join(leaked),
        )


def build_fx_payload(events: Iterable[FXEvent]) -> Dict[str, Any]:
    """Serialize a bounded FX batch for the client planner."""
    items = list(events)
    for event in items:
        validate_fx_event(event)
    payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "mode": MODE,
        "business_truth": False,
        "events": [event.to_dict() for event in items],
    }
    _assert_no_truth(payload)
    return payload


def validate_fx_payload(payload: Any) -> List[str]:
    """Return the problems in a client-facing FX payload (empty = valid)."""
    problems: List[str] = []
    if not isinstance(payload, Mapping):
        return ["payload must be an object"]
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}")
    if payload.get("mode") != MODE:
        problems.append(f"mode must be {MODE!r}")
    if payload.get("business_truth") is not False:
        problems.append("business_truth must be false")
    for key in FORBIDDEN_TRUTH_KEYS:
        if key in payload:
            problems.append(f"payload may not carry {key!r}")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        problems.append("events must be a list")
        return problems
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, Mapping):
            problems.append(f"events[{index}] must be an object")
            continue
        try:
            validate_fx_event(FXEvent.from_dict(raw))
        except FXError as exc:
            problems.append(f"events[{index}]: {exc.code}")
    return problems
