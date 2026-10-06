"""Authoritative voice "talk events" sequencer (OpenClaw ``talk/talk-events`` port).

One ordered event stream with a monotonic ``seq`` and a turn id is the
foundation for a voice finite-state machine and for exact UI (orb) state:
observers reconstruct a turn without guessing from arrival order, because every
typed boundary is stamped with the same sequence counter and the turn it
belongs to.

Ported from the OpenClaw reference (``src/talk/talk-events.ts`` +
``talk-session-controller.ts``), reduced to the boundaries Qubit's voice surface
actually emits. Semantics preserved:

- ``seq`` starts at 1 and increments once per accepted emit (gap-free: an
  unknown type is rejected BEFORE the counter moves).
- ``begin_turn`` mints a new turn id and makes it current; every subsequent
  event carries it until ``end_turn`` (or the next ``begin_turn``) clears it.
- Unknown event types are REJECTED: ``emit`` raises ``ValueError``. This is the
  documented contract — callers pass only members of ``TALK_EVENT_TYPES``.
- Malformed *fields* never raise: the envelope is authoritative, so a field
  named ``seq``/``type``/``turn_id``/``at`` cannot corrupt the ordered stream.
- Optional best-effort journaling (``emit(..., journal=True)``) writes
  ``voice_edit.talk.<type>`` through ``logging_setup.log_event`` and is wrapped
  so a broken/absent journal can never break event delivery.

Pure and deterministic with an injectable clock (``now``). ``at`` follows the
``voicebridge.orb.state`` tl;dr: a float timestamp supplied by the caller's
clock, so tests and replay are exact.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import logging_setup

#: Typed voice boundaries accepted by :class:`TalkEventSequencer`. Turn-scoped
#: shapes (transcript/response/tool/playback) plus session/interrupt/error
#: markers. A type outside this set is rejected by ``emit``.
TALK_EVENT_TYPES = frozenset({
    "session.state",
    "session.started",
    "session.ended",
    "input.speech_started",
    "input.speech_stopped",
    "transcript.partial",
    "transcript.final",
    "response.started",
    "response.audio.delta",
    "response.done",
    "tool.call",
    "tool.result",
    "playback.started",
    "playback.stopped",
    "interrupt",
    "error",
})

#: Envelope keys owned by the sequencer. A caller field with the same name is
#: ignored (never raises) so the ordered stream stays authoritative.
_RESERVED_KEYS = frozenset({"seq", "type", "turn_id", "at"})


class TalkEventSequencer:
    """Stamps typed voice boundaries with a monotonic ``seq`` + current turn id.

    Parameters
    ----------
    now:
        Injectable clock returning seconds (``float``-coercible). Defaults to
        ``time.monotonic``. A malformed clock never raises — the last known
        timestamp is reused (deterministic fallback, starting at ``0.0``).
    turn_id_prefix:
        Prefix for minted turn ids (``turn-1``, ``turn-2``, ...).
    max_events:
        Optional bound on the retained ordered stream (``None`` = keep all).
        Trims the OLDEST events from ``snapshot()`` history only; the sequence
        counter is never affected.
    """

    def __init__(
        self,
        *,
        now: Optional[Callable[[], Any]] = None,
        turn_id_prefix: str = "turn",
        max_events: Optional[int] = None,
    ) -> None:
        self._now: Callable[[], Any] = now if callable(now) else time.monotonic
        self._prefix = str(turn_id_prefix)
        self._seq = 0
        self._turn_seq = 0
        self._turn_id: Optional[str] = None
        self._last_at = 0.0
        self._events: List[Dict[str, Any]] = []
        try:
            self._max_events = int(max_events) if max_events is not None else None
        except Exception:
            self._max_events = None
        if self._max_events is not None and self._max_events <= 0:
            self._max_events = None

    # -- clock --------------------------------------------------------------

    def _read_now(self) -> float:
        """Read the injectable clock; never raises (falls back to last value)."""
        try:
            value = float(self._now())
        except Exception:
            return self._last_at
        self._last_at = value
        return value

    # -- turns --------------------------------------------------------------

    def begin_turn(self) -> str:
        """Mint a new turn id, make it current, and return it.

        Ids are deterministic per instance (``<prefix>-<n>``) and never repeat
        within a sequencer.
        """
        self._turn_seq += 1
        self._turn_id = f"{self._prefix}-{self._turn_seq}"
        return self._turn_id

    def current_turn(self) -> Optional[str]:
        """The active turn id, or ``None`` when no turn is open."""
        return self._turn_id

    def end_turn(self) -> Optional[str]:
        """Clear the active turn; return the id that was active (or ``None``)."""
        ended = self._turn_id
        self._turn_id = None
        return ended

    # -- events -------------------------------------------------------------

    def emit(
        self,
        type: str,
        *,
        turn_id: Optional[str] = None,
        journal: bool = False,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Append a typed event to the stream and return its envelope.

        ``seq`` increments by exactly one per accepted call (starting at 1); an
        unknown ``type`` raises :class:`ValueError` before the counter moves.
        ``turn_id`` defaults to the current turn. Reserved envelope keys in
        ``fields`` are ignored; any other field is copied verbatim. Never raises
        on malformed fields.

        ``journal=True`` additionally writes ``voice_edit.talk.<type>`` via
        ``logging_setup.log_event`` (best effort — a throwing journal is
        swallowed and the event is still returned).
        """
        if not isinstance(type, str) or type not in TALK_EVENT_TYPES:
            raise ValueError(f"unknown talk event type: {type!r}")
        self._seq += 1
        event_turn = turn_id if turn_id is not None else self._turn_id
        envelope: Dict[str, Any] = {}
        for key, value in fields.items():
            if key in _RESERVED_KEYS:
                continue  # envelope is authoritative; never raise
            envelope[str(key)] = value
        envelope["seq"] = self._seq
        envelope["type"] = type
        envelope["turn_id"] = event_turn
        envelope["at"] = self._read_now()
        self._events.append(envelope)
        if self._max_events is not None and len(self._events) > self._max_events:
            del self._events[: len(self._events) - self._max_events]
        if journal:
            self._journal(envelope)
        return envelope

    def _journal(self, event: Dict[str, Any]) -> None:
        """Best-effort structured journal write; NEVER raises."""
        try:
            logging_setup.log_event(
                f"voice_edit.talk.{event['type']}",
                seq=event["seq"],
                turn_id=event["turn_id"],
            )
        except Exception:
            # Delivery must survive a broken/absent journal.
            pass

    def snapshot(self) -> Dict[str, Any]:
        """Return the current stream state.

        Shape::

            {
                "seq": int,             # last emitted seq (0 before any)
                "turn_id": str | None,  # active turn
                "turn_count": int,      # turns minted by begin_turn
                "events": tuple[dict],  # ordered copy of the retained stream
                "event_count": int,
            }
        """
        return {
            "seq": self._seq,
            "turn_id": self._turn_id,
            "turn_count": self._turn_seq,
            "events": tuple(self._events),
            "event_count": len(self._events),
        }


__all__ = ["TALK_EVENT_TYPES", "TalkEventSequencer"]
