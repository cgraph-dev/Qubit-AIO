"""Authoritative orb state from the voice talk-event journal stream.

The desktop helper emits typed voice boundaries through
:class:`voicebridge.voice_edit.talk_events.TalkEventSequencer` with
``journal=True``, so every boundary lands in the one Qubit journal as a
``voice_edit.talk.<type>`` row carrying ``seq`` + ``turn_id``. That stream
is the authoritative record of what a voice turn is doing — mic open,
transcript final, response start, tool call, TTS playback — and the orb
should derive its state from it directly instead of inferring from
incidental timestamps.

This module bridges the two: it filters a journal-events list down to the
talk rows, normalizes them to the sequencer envelope shape, and reduces the
most recent turn through :func:`voicebridge.voice_edit.talk_fsm.reduce_many`
(the same priority table the orb poller uses). ``resolve_orb_state`` treats a
non-``None`` result as authoritative; when there are no talk events it falls
back byte-identically to today's inference.

Pure, deterministic, no I/O, no clock. Never raises.
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from ..voice_edit.talk_events import TALK_EVENT_TYPES

#: Journal event-name prefix for typed talk boundaries (matches
#: ``TalkEventSequencer._journal`` -> ``logging_setup.log_event``).
TALK_EVENT_PREFIX = "voice_edit.talk."

#: Sentinel for "this event has no usable ``at`` timestamp" — such an event
#: forms its own reduction group (strict stream order).
_MISSING_AT = object()


def talk_events_from_journal(events: Any) -> List[Dict[str, Any]]:
    """Filter a journal-events list to ``voice_edit.talk.*`` rows, normalized.

    Returns a list (oldest first) of ``{"type", "at", "turn_id"}`` dicts:

    - ``type`` is the talk boundary (the ``voice_edit.talk.`` prefix stripped);
    - ``at`` is the row's ``ts`` (the sequencer emit time);
    - ``turn_id`` is the row's turn id (``None`` when the row carries none).

    Only rows whose event name is a known :data:`TALK_EVENT_TYPES` boundary
    are kept. Pure and fail-soft: a non-list input, a non-dict row, a missing
    event name, a malformed type, or anything that raises is skipped. Never
    raises.
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(events, (list, tuple)):
        return out
    for row in events:
        try:
            if not isinstance(row, dict):
                continue
            name = row.get("event")
            if not isinstance(name, str) or not name.startswith(TALK_EVENT_PREFIX):
                continue
            etype = name[len(TALK_EVENT_PREFIX):]
            if etype not in TALK_EVENT_TYPES:
                continue
            out.append({
                "type": etype,
                "at": row.get("ts"),
                "turn_id": row.get("turn_id"),
            })
        except Exception:
            continue
    return out


def _normalize_event(row: Any) -> Optional[Dict[str, Any]]:
    """One event (envelope/row/bare type) -> talk-event dict, or ``None``.

    Accepts an already-normalized ``{"type": ...}`` dict, a raw journal row
    (``{"event": "voice_edit.talk.<type>"}``), or a bare boundary string.
    Never raises.
    """
    try:
        if isinstance(row, str):
            return ({"type": row, "at": None, "turn_id": None}
                    if row in TALK_EVENT_TYPES else None)
        if not isinstance(row, dict):
            return None
        etype = row.get("type")
        if isinstance(etype, str) and etype in TALK_EVENT_TYPES:
            at = row.get("at")
            if at is None:
                at = row.get("ts")
            return {"type": etype, "at": at, "turn_id": row.get("turn_id")}
        name = row.get("event")
        if isinstance(name, str) and name.startswith(TALK_EVENT_PREFIX):
            etype = name[len(TALK_EVENT_PREFIX):]
            if etype in TALK_EVENT_TYPES:
                return {"type": etype, "at": row.get("ts"),
                        "turn_id": row.get("turn_id")}
        return None
    except Exception:
        return None


def _groups_by_at(batch: List[Dict[str, Any]]) -> Iterator[List[Dict[str, Any]]]:
    """Yield reduction groups: equal ``at`` together, missing ``at`` singleton.

    Consecutive events stamped with the same time are released together so
    :func:`reduce_many` can resolve their overlap by priority; events with no
    usable timestamp are kept as singleton groups (strict stream order). Never
    raises.
    """
    group: List[Dict[str, Any]] = []
    key: Any = _MISSING_AT
    for ev in batch:
        at = ev.get("at")
        if not (isinstance(at, (int, float)) and not isinstance(at, bool)):
            at = _MISSING_AT
        if group and (at is _MISSING_AT or key is _MISSING_AT or at != key):
            yield group
            group = []
        key = at
        group.append(ev)
    if group:
        yield group


def state_from_talk_events(events: Any) -> Optional[str]:
    """Reduce the most recent turn's talk events to one orb state.

    Returns ``idle``/``listening``/``thinking``/``working``/``speaking``/
    ``error`` — the state the voice FSM (:mod:`talk_fsm`) asserts for the
    most recent turn — or ``None`` when ``events`` carries no talk events at
    all (the caller then falls back to the recency-window inference).

    The most recent turn is the last event carrying a non-empty ``turn_id``;
    its events (plus any trailing turn-less events after it) are folded in
    order, with equal-``at`` events resolved by priority via
    :func:`voicebridge.voice_edit.talk_fsm.reduce_many`. Pure and fail-soft:
    unknown/malformed events are dropped, and any error yields ``None``.
    """
    try:
        if not isinstance(events, (list, tuple)):
            return None
        batch: List[Dict[str, Any]] = []
        for row in events:
            ev = _normalize_event(row)
            if ev is not None:
                batch.append(ev)
        if not batch:
            return None
        current_turn: Optional[str] = None
        for ev in batch:
            if ev.get("turn_id"):
                current_turn = ev["turn_id"]
        if current_turn is not None:
            start = 0
            for index, ev in enumerate(batch):
                if ev.get("turn_id") == current_turn:
                    start = index
                    break
            window = [
                ev for ev in batch[start:]
                if not ev.get("turn_id") or ev.get("turn_id") == current_turn
            ]
            if window:
                batch = window
        from ..voice_edit.talk_fsm import reduce_many
        state = "idle"
        for group in _groups_by_at(batch):
            state = reduce_many(state, group)
        return state
    except Exception:
        return None


__all__ = [
    "TALK_EVENT_PREFIX",
    "talk_events_from_journal",
    "state_from_talk_events",
]
