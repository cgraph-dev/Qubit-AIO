"""Active/dormant room policy and the deterministic scheduler seam.

Master plan §5 and §18.4 make one promise above all others:

    **Business outcomes never depend on whether a room is visible.**

This module is where that promise is kept mechanically. It takes the
authoritative data from :mod:`voicebridge.office.world.model`, reads a bundle
of *view* signals (camera, interaction, urgency, deadlines, priority, fairness),
and returns a decision per room. Applying a decision changes the room's
lifecycle/tier only; it never mutates a business record, a pending event, an
approval or a schedule. Those live on the room and are deliberately left alone
when the room goes ``DORMANT``.

The input set is deliberately wider than the camera, because the plan says the
active set uses "camera + interaction + urgent events + deadlines, not camera
alone". ``camera_rooms`` alone can raise a room only to ``PROXY`` (static
presentation, labels/silhouettes). Simulating agents requires an interaction,
an urgent event, or a deadline.

The lifecycle transitions are the ones declared in ``model.can_transition``;
:func:`drive_room` walks the shortest legal path so a caller never invents an
edge (for example ``DORMANT -> SIMULATION_ACTIVE`` directly). Rehydration
reconstructs a presentation snapshot from authoritative state.

Pure Python, no I/O, no wall clock. ``now`` is always an argument.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Protocol, Tuple

from .model import (
    Campus,
    LogicalRoom,
    RoomState,
    Tier,
    can_transition,
)

__all__ = [
    "ActiveSignals",
    "ActivePolicy",
    "ActiveDecision",
    "ActiveSet",
    "PresentationSnapshot",
    "select_active_set",
    "apply_active_set",
    "drive_room",
    "rehydrate_room",
    "ScheduledEvent",
    "Scheduler",
    "DeterministicScheduler",
]


@dataclass(frozen=True)
class ActiveSignals:
    """Everything the active set may consider. None of it owns business truth."""

    camera_rooms: FrozenSet[str] = frozenset()
    interaction_room: Optional[str] = None
    urgent_rooms: FrozenSet[str] = frozenset()
    deadline_rooms: FrozenSet[str] = frozenset()
    approval_rooms: FrozenSet[str] = frozenset()
    notification_rooms: FrozenSet[str] = frozenset()
    priorities: Mapping[str, int] = field(default_factory=dict)
    #: Seconds a room has been starved of attention; fairness/aging.
    fairness: Mapping[str, float] = field(default_factory=dict)
    now: float = 0.0

    def __post_init__(self) -> None:
        # Freeze the mapping-ish inputs so a caller cannot mutate them after the
        # decision was taken.
        for name in ("priorities", "fairness"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise TypeError(f"ActiveSignals.{name} must be a mapping")


@dataclass(frozen=True)
class ActivePolicy:
    """Tier floors for each signal. Higher floors win; reasons are accumulated."""

    camera_tier: Tier = Tier.PROXY
    interaction_tier: Tier = Tier.FULL
    urgent_tier: Tier = Tier.FULL
    deadline_tier: Tier = Tier.LOW_RATE
    approval_tier: Tier = Tier.LOW_RATE
    notification_tier: Tier = Tier.LOW_RATE
    #: A priority at or above this value raises a room to ``priority_tier``.
    priority_threshold: int = 5
    priority_tier: Tier = Tier.LOW_RATE
    #: Starvation seconds at or above this value raise a dormant room to
    #: ``fairness_tier`` so a quiet room cannot be starved forever.
    fairness_boost_seconds: float = 600.0
    fairness_tier: Tier = Tier.AGGREGATE
    #: Score weights are only used for deterministic ordering/reporting; the
    #: tier is the contract.
    weights: Mapping[str, float] = field(default_factory=lambda: {
        "camera": 1.0,
        "interaction": 8.0,
        "urgent": 10.0,
        "deadline": 6.0,
        "approval": 4.0,
        "notification": 3.0,
        "priority": 2.0,
        "fairness": 1.0,
    })


@dataclass(frozen=True)
class ActiveDecision:
    """What the policy decided for one room, and why."""

    room_id: str
    tier: Tier
    state: RoomState
    score: float
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "tier": int(self.tier),
            "state": self.state.value,
            "score": float(self.score),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ActiveSet:
    """The full decision set for one tick."""

    decisions: Tuple[ActiveDecision, ...]
    now: float = 0.0

    def by_room(self) -> Dict[str, ActiveDecision]:
        return {d.room_id: d for d in self.decisions}

    def active_rooms(self) -> Tuple[str, ...]:
        return tuple(d.room_id for d in self.decisions if d.tier > Tier.DORMANT)

    def dormant_rooms(self) -> Tuple[str, ...]:
        return tuple(d.room_id for d in self.decisions if d.tier == Tier.DORMANT)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "now": float(self.now),
            "decisions": [d.to_dict() for d in self.decisions],
            "active_rooms": list(self.active_rooms()),
            "dormant_rooms": list(self.dormant_rooms()),
        }


def _state_for_tier(tier: Tier, *, simulate: bool) -> RoomState:
    """Map a tier to a lifecycle state.

    The plan's tier numbers are not a single richness axis: tier 3 is the
    *proxy* (labels/lights/silhouettes, i.e. ``PRESENTATION_ACTIVE``) while tier
    2 is low-rate *simulation*. ``simulate`` records whether any simulation
    signal fired, so camera-alone cannot put agents on screen while an approval
    or deadline still can.
    """

    if simulate:
        return RoomState.SIMULATION_ACTIVE
    if tier >= Tier.PROXY:
        return RoomState.PRESENTATION_ACTIVE
    if tier == Tier.AGGREGATE:
        return RoomState.OBSERVED
    return RoomState.DORMANT


def select_active_set(
    campus: Campus,
    signals: ActiveSignals,
    policy: Optional[ActivePolicy] = None,
) -> ActiveSet:
    """Decide every room's tier from the view signals.

    Rooms are returned in ``room_id`` order so two identical inputs produce
    byte-identical output. The function is pure: it reads the campus and the
    signals, and writes nothing.
    """

    policy = policy or ActivePolicy()
    decisions: List[ActiveDecision] = []
    for room in sorted(campus.rooms(), key=lambda r: r.room_id):
        tier = Tier.DORMANT
        simulate = False
        reasons: List[str] = []
        score = 0.0

        def raise_to(candidate: Tier, reason: str, weight: float,
                     detail: Optional[str] = None) -> None:
            nonlocal tier, score
            if candidate > tier:
                tier = candidate
            score += weight
            reasons.append(reason if detail is None else f"{reason}:{detail}")

        if room.room_id in signals.urgent_rooms:
            raise_to(policy.urgent_tier, "urgent", policy.weights.get("urgent", 0.0))
            simulate = True
        if signals.interaction_room == room.room_id:
            raise_to(policy.interaction_tier, "interaction",
                     policy.weights.get("interaction", 0.0))
            simulate = True
        if room.room_id in signals.deadline_rooms:
            raise_to(policy.deadline_tier, "deadline",
                     policy.weights.get("deadline", 0.0))
            simulate = True
        if room.room_id in signals.approval_rooms:
            raise_to(policy.approval_tier, "approval",
                     policy.weights.get("approval", 0.0))
            simulate = True
        if room.room_id in signals.notification_rooms:
            raise_to(policy.notification_tier, "notification",
                     policy.weights.get("notification", 0.0))
            simulate = True
        if room.room_id in signals.camera_rooms:
            # Camera alone is presentation only: silhouettes and labels, never
            # agents pretending to work because the viewer moved.
            raise_to(policy.camera_tier, "camera",
                     policy.weights.get("camera", 0.0))

        priority = max(int(room.priority), int(signals.priorities.get(room.room_id, 0)))
        if priority >= policy.priority_threshold:
            raise_to(policy.priority_tier, "priority",
                     policy.weights.get("priority", 0.0), str(priority))
            simulate = True

        age = float(signals.fairness.get(room.room_id, 0.0))
        if age >= policy.fairness_boost_seconds and tier == Tier.DORMANT:
            raise_to(policy.fairness_tier, "fairness",
                     policy.weights.get("fairness", 0.0), f"{age:.1f}s")

        decisions.append(ActiveDecision(
            room_id=room.room_id,
            tier=tier,
            state=_state_for_tier(tier, simulate=simulate),
            score=round(score, 4),
            reasons=tuple(reasons),
        ))
    return ActiveSet(tuple(decisions), now=float(signals.now))


def _shortest_state_path(source: RoomState, target: RoomState) -> Optional[Tuple[RoomState, ...]]:
    if source == target:
        return (source,)
    seen = {source}
    queue: List[Tuple[RoomState, ...]] = [(source,)]
    while queue:
        trail = queue.pop(0)
        for nxt in sorted(_TRANSITIONS_FOR(trail[-1]), key=lambda s: s.value):
            if nxt in seen:
                continue
            if nxt == target:
                return trail + (nxt,)
            seen.add(nxt)
            queue.append(trail + (nxt,))
    return None


def _TRANSITIONS_FOR(state: RoomState) -> FrozenSet[RoomState]:
    # Imported lazily from the model module's transition map via can_transition
    # would be O(states^2) and less explicit; the table is rebuilt here from the
    # public predicate so the model stays the single source of truth.
    return frozenset(s for s in RoomState
                     if s is not state and can_transition(state, s))


def drive_room(room: LogicalRoom, target: RoomState) -> LogicalRoom:
    """Move a room to ``target`` along legal edges only.

    Raised :class:`ValueError` when no legal path exists; the caller has asked
    for a lifecycle edge the plan does not declare.
    """

    path = _shortest_state_path(room.state, target)
    if path is None:
        raise ValueError(
            f"room {room.room_id!r}: no legal transition "
            f"{room.state.value} -> {target.value}")
    state = room
    for step in path[1:]:
        state = replace(state, state=step)
    return state


def apply_active_set(campus: Campus, active: ActiveSet) -> Campus:
    """Return a campus whose rooms are driven to the decided states.

    Only lifecycle/tier fields change. Pending events, approvals, schedules,
    seats, businesses and geometry are copied verbatim — dormancy never erases
    operational state.
    """

    decisions = active.by_room()
    wings = []
    for wing in campus.wings:
        rooms = []
        for room in wing.rooms:
            decision = decisions.get(room.room_id)
            if decision is None:
                rooms.append(room)
                continue
            moved = drive_room(room, decision.state)
            moved = replace(moved, state=decision.state, tier=decision.tier)
            rooms.append(moved)
        wings.append(replace(wing, rooms=tuple(rooms)))
    return replace(campus, wings=tuple(wings))


@dataclass(frozen=True)
class PresentationSnapshot:
    """A reconstructed view derived from authoritative state.

    This is what a renderer gets after a room rehydrates. It contains no new
    business facts: every value is a projection of the room that already
    existed while it was dormant.
    """

    room_id: str
    state: RoomState
    tier: Tier
    capacity: int
    headcount: int
    seats: Tuple[Dict[str, Any], ...]
    pending_events: int
    approvals: int
    schedules: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "state": self.state.value,
            "tier": int(self.tier),
            "capacity": int(self.capacity),
            "headcount": int(self.headcount),
            "seats": [dict(s) for s in self.seats],
            "pending_events": int(self.pending_events),
            "approvals": int(self.approvals),
            "schedules": int(self.schedules),
        }


def rehydrate_room(room: LogicalRoom) -> Tuple[LogicalRoom, PresentationSnapshot]:
    """Rebuild a room's presentation from state that never stopped existing."""

    rehydrated = drive_room(room, RoomState.PRESENTATION_ACTIVE)
    tier = max(room.tier, Tier.PROXY)
    rehydrated = replace(rehydrated, tier=tier)
    snapshot = PresentationSnapshot(
        room_id=rehydrated.room_id,
        state=rehydrated.state,
        tier=tier,
        capacity=rehydrated.capacity,
        headcount=rehydrated.headcount,
        seats=tuple(s.to_dict() for s in rehydrated.seats),
        pending_events=len(rehydrated.pending_events),
        approvals=len(rehydrated.approvals),
        schedules=len(rehydrated.schedules),
    )
    return rehydrated, snapshot


# ---------------------------------------------------------------- scheduler
@dataclass(frozen=True, order=True)
class ScheduledEvent:
    """A due-time item. Ordering is ``(at, event_id)`` and therefore total."""

    at: float
    event_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"at": float(self.at),
                "event_id": self.event_id,
                "payload": dict(self.payload)}


class Scheduler(Protocol):
    """The clock seam the dormant world depends on.

    Any implementation must be deterministic for a given event set and must not
    consult a wall clock unless the caller asks it to. Business timers are
    scheduled here even while their rooms are dormant.
    """

    def now(self) -> float:
        ...

    def schedule(self, event_id: str, at: float,
                 payload: Optional[Mapping[str, Any]] = None) -> None:
        ...

    def due(self) -> Tuple[ScheduledEvent, ...]:
        ...

    def advance(self, seconds: float) -> Tuple[ScheduledEvent, ...]:
        ...


class DeterministicScheduler:
    """A monotonic scheduler with an injected clock.

    ``advance`` moves the clock and returns every event whose ``at`` is now in
    the past, sorted by ``(at, event_id)``. Duplicate ids are refused so a timer
    cannot be silently double-armed.
    """

    def __init__(self, start: float = 0.0):
        if start < 0:
            raise ValueError("start must be >= 0")
        self._now = float(start)
        self._events: List[ScheduledEvent] = []
        self._ids: set[str] = set()

    def now(self) -> float:
        return self._now

    def schedule(self, event_id: str, at: float,
                 payload: Optional[Mapping[str, Any]] = None) -> None:
        if not event_id:
            raise ValueError("event_id must be non-empty")
        if event_id in self._ids:
            raise ValueError(f"event {event_id!r} is already scheduled")
        if at < self._now:
            raise ValueError(
                f"event {event_id!r} at {at} is before now {self._now}")
        self._events.append(ScheduledEvent(float(at), event_id, dict(payload or {})))
        self._ids.add(event_id)

    def due(self) -> Tuple[ScheduledEvent, ...]:
        return tuple(sorted(
            (e for e in self._events if e.at <= self._now),
            key=lambda e: (e.at, e.event_id),
        ))

    def pending(self) -> Tuple[ScheduledEvent, ...]:
        return tuple(sorted(self._events, key=lambda e: (e.at, e.event_id)))

    def advance(self, seconds: float) -> Tuple[ScheduledEvent, ...]:
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        self._now += float(seconds)
        fired = self.due()
        gone = {e.event_id for e in fired}
        self._events = [e for e in self._events if e.event_id not in gone]
        self._ids.difference_update(gone)
        return fired

    def to_dict(self) -> Dict[str, Any]:
        return {"now": self._now,
                "pending": [e.to_dict() for e in self.pending()]}
