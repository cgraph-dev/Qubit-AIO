"""Pure talk-event -> orb-state reducer (the voice finite-state machine).

Maps the typed boundaries emitted by
:mod:`voicebridge.voice_edit.talk_events` onto the orb vocabulary
(:data:`voicebridge.orb.state.ORB_STATES`: ``idle``/``listening``/
``thinking``/``working``/``speaking``; plus the terminal ``error`` the orb
display accepts) and resolves overlaps with the SAME priority the orb poller
uses — ``listening > speaking > working > thinking > idle`` — so the FSM and
the recency-window resolver agree on what the orb should show.

The reducer is **pure, deterministic, and never raises**. Unknown or malformed
events leave the current state unchanged; a non-string/malformed state folds to
``idle``. No I/O, no clock, no global state.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

try:  # authoritative orb vocabulary; import is pure and side-effect free.
    from ..orb.state import ORB_STATES as _ORB_STATES
except Exception:  # pragma: no cover - defensive: still fully usable standalone
    _ORB_STATES = ("idle", "listening", "thinking", "working", "speaking")

ORB_STATES = tuple(_ORB_STATES)

#: The one extra terminal state the orb display accepts (orb/state.py
#: ``_normalize_candidate`` passes ``"error"`` through).
ERROR_STATE = "error"

#: event type -> orb state. EXACTLY the documented boundaries; every other
#: type (known-but-unmapped like ``transcript.partial``, unknown, or malformed)
#: leaves the state unchanged. Exposed for tests/observers.
TALK_EVENT_STATES = {
    "input.speech_started": "listening",
    "interrupt": "listening",
    "transcript.final": "thinking",
    "response.started": "thinking",
    "tool.call": "working",
    "response.audio.delta": "speaking",
    "playback.started": "speaking",
    "response.done": "idle",
    "playback.stopped": "idle",
    "error": ERROR_STATE,
}

#: Highest priority first. Mirrors orb/state.py (listening > speaking >
#: working > thinking > idle); ``error`` outranks all so a failure is never
#: masked by a later/overlapping benign event.
TALK_PRIORITY = (
    ERROR_STATE, "listening", "speaking", "working", "thinking", "idle",
)

_PRIORITY_INDEX = {state: index for index, state in enumerate(TALK_PRIORITY)}


def normalize_state(state: Any, default: str = "idle") -> str:
    """Fold any candidate onto the orb vocabulary. Never raises.

    A valid orb state (or ``error``) is returned verbatim (lower-cased);
    anything else folds to ``default``.
    """
    try:
        key = str(state or "").strip().lower()
    except Exception:
        return default
    if key in _PRIORITY_INDEX:
        return key
    return default


def event_state(event: Any) -> Optional[str]:
    """The orb state one talk event maps to, or ``None`` if unmapped/unknown.

    Accepts a bare event type string or a sequencer envelope dict (its
    ``"type"`` field). Never raises.
    """
    try:
        if isinstance(event, dict):
            etype = event.get("type")
        else:
            etype = event
        if not isinstance(etype, str):
            return None
        return TALK_EVENT_STATES.get(etype)
    except Exception:
        return None


def reduce(prev_state: Any, event: Any) -> str:
    """Pure single-event reducer: ``(prev_state, event) -> orb state``.

    A mapped event wins; an unmapped/unknown/malformed event leaves
    ``prev_state`` unchanged (a malformed ``prev_state`` folds to ``idle``).
    Never raises.
    """
    state = event_state(event)
    if state is None:
        return normalize_state(prev_state)
    return state


def reduce_many(prev_state: Any, events: Any) -> str:
    """Reduce an overlapping batch of events by orb priority.

    Maps every known event, then returns the highest-priority mapped state
    (:data:`TALK_PRIORITY`); if no event is mapped, ``prev_state`` is returned
    unchanged. Unknown/malformed events are ignored. Never raises.
    """
    try:
        batch: Iterable[Any] = list(events)
    except Exception:
        batch = []
    best: Optional[str] = None
    best_rank = len(TALK_PRIORITY)
    for event in batch:
        state = event_state(event)
        if state is None:
            continue
        rank = _PRIORITY_INDEX.get(state, len(TALK_PRIORITY))
        if rank < best_rank:
            best_rank = rank
            best = state
    if best is None:
        return normalize_state(prev_state)
    return best


class TalkStateMachine:
    """Stateful wrapper over :func:`reduce` / :func:`reduce_many`.

    Pure logic: no I/O, no clock. ``apply`` consumes one event; ``apply_many``
    consumes a batch with priority resolution. Never raises.
    """

    def __init__(self, initial: Any = "idle") -> None:
        self.state: str = normalize_state(initial)

    def reset(self, state: Any = "idle") -> str:
        self.state = normalize_state(state)
        return self.state

    def current(self) -> str:
        return self.state

    def apply(self, event: Any) -> str:
        self.state = reduce(self.state, event)
        return self.state

    def apply_many(self, events: Any) -> str:
        self.state = reduce_many(self.state, events)
        return self.state


__all__ = [
    "ORB_STATES",
    "ERROR_STATE",
    "TALK_EVENT_STATES",
    "TALK_PRIORITY",
    "normalize_state",
    "event_state",
    "reduce",
    "reduce_many",
    "TalkStateMachine",
]
