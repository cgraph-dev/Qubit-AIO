"""Orb state resolution: status payload + voice journal events -> waveform state.

Mirrors the dashboard widget mapping
(``frontend/qubit-web/src/lib/voice/waveform-state.ts``,
``resolveWaveformState``): priority listening > speaking > thinking >
idle (speaking outranks thinking since 2026-10-04 — a fresh stt.final
inside its 12 s window used to mask an already-playing reply yellow);
anything unknown falls back to idle so the orb can never get
stuck animating a state it does not understand.

Signal grounding (all read-only, all fail-closed):

- ``listening`` -- recent capture-side activity in the voice journal:
  ``stt.started`` / ``stt.partial`` (capture open, cf. the ``stt.*``
  voice-event prefix in ``voice_edit/logview.py``),
  ``voice_edit.helper.listening`` (Right-Ctrl key-down edge), or
  ``voice_edit.wake.capturing`` (wake segment-open edge) — EXCEPT a
  capturing edge older than a fresh speak marker, which is own-TTS echo
  re-arm (the wake loop re-fires capturing ~every 2 s while the room is
  voiced; 2026-10-05) and is ignored so it cannot re-light listening.
- ``thinking`` -- a turn is in flight: ``active_requests >= 1`` from
  ``GET /api/voice-edit/status`` (``VoiceEditService.status``), or a
  recent ``stt.final`` / ``stt.completed`` (transcribed, turn likely
  running) — but once the turn is over (``active_requests == 0``) only a
  SHORT idle window counts, so a stale transcript cannot stick yellow
  (live 2026-10-04/05: thinking->idle flipped exactly 12 s after
  ``stt.final``).
- ``speaking`` -- recent TTS activity: ``tts.speak`` /
  ``tts.synthesize`` / ``voice_edit.helper.speak`` (exactly the echo
  set ``desktop_helper._TTS_ECHO_EVENTS`` watches).
- ``idle`` -- everything else, including unreachable/malformed input.

Recency windows are deliberately short: the orb shows what is
happening *now*, and stale events must never stick. Worst-case linger
after the 2026-10-05 tightening: listening <= 3 s past the last capture
edge, speaking <= 4 s past the last speak marker (a newer speak_done
cuts it immediately), thinking <= 4 s past the last transcript marker
once the turn counter returns to 0 (12 s only while a turn is genuinely
in flight) — plus up to MIN_DWELL_S of display hysteresis on non-instant
transitions. A ~10 s turn therefore clears all color within ~4 s of its
end instead of ~34 s.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

ORB_STATES = ("idle", "listening", "thinking", "speaking")

#: Capture-side journal events that mean the mic path is live right now.
#: The wake path emits the prefixed names (the bare ``wake.command`` never
#: appears in the journal — 2026-10-04: listening yellow never showed on
#: hands-free turns, only on Right-Ctrl/dashboard STT).
_LISTENING_EVENTS = frozenset({
    # Only true CAPTURE signals: the mic is open on the owner's voice RIGHT
    # NOW. Command-accept rows (``wake.command`` / ``helper.wake.command``)
    # fire AFTER the owner stops talking and were removed from this set — a
    # 3 s listening window outlasted the turn's whole processing phase, so
    # listening masked thinking and the orb never showed the thinking color
    # (live 2026-10-05).
    "stt.started", "stt.partial",
    # Wake segment-open edge (re-fired ~2 s while voiced frames arrive).
    "voice_edit.wake.capturing",
    # Right-Ctrl (push-to-talk) key-down capture edge.
    "voice_edit.helper.listening",
})

#: Transcript-complete events: capture done, the turn is likely running.
_THINKING_EVENTS = frozenset({"stt.final", "stt.completed"})

#: TTS-side journal events that mean Qubit is audibly replying.
_SPEAKING_EVENTS = frozenset(
    {"tts.speak", "tts.synthesize", "voice_edit.helper.speak"})

#: Playback-finished events. A speak token only means "audio started"; green
#: must end when the audio ends, not linger for the whole recency window
#: (live 2026-10-05: the orb stayed green after Qubit stopped talking). When
#: a done marker is newer than the last speak marker, speaking is over.
_SPEAK_DONE_EVENTS = frozenset(
    {"voice_edit.helper.speak_done", "tts.done", "tts.speak_done"})

LISTEN_WINDOW_S = 3.0
THINK_WINDOW_S = 12.0
SPEAK_WINDOW_S = 4.0

#: Transcript-completion window once the turn is OVER (``active_requests
#: == 0``). The full THINK_WINDOW_S applies only while a turn is genuinely
#: in flight (``active_requests > 0``); after that a stale ``stt.final``
#: must clear within seconds, not linger yellow for the whole ceiling.
THINK_IDLE_WINDOW_S = 4.0

#: A staged begin/cancel request older than this is treated as expired
#: (the service prunes by its own TTL; the orb must not cancel a
#: recycled id).
PENDING_TTL_S = 120.0

#: Minimum dwell per displayed state: the visible orb state holds at
#: least this long before switching, so the 2s poll recency windows
#: cannot flap listening<->idle on one stale/missing event. Listening
#: onset is instant (capture must read live the moment it starts) and
#: error is immediate (not-working must never wait out a dwell).
MIN_DWELL_S = 1.0


def _normalize_candidate(candidate: Any) -> str:
    """Fold any unknown candidate to ``"idle"``. Never raises."""
    try:
        key = str(candidate or "").strip().lower()
    except Exception:
        return "idle"
    if key in ORB_STATES or key == "error":
        return key
    return "idle"


class DisplayState:
    """Hysteresis smoother: raw poll state -> stable displayed state.

    ``update(candidate, now)`` returns what the orb should SHOW.
    Listening onset and error apply instantly; every other switch waits
    until the currently displayed state has held ``MIN_DWELL_S``. Pure
    time logic (monotonic seconds), never raises, no I/O.
    """

    def __init__(self, initial: Any = "idle", now: Any = None) -> None:
        self.displayed: str = _normalize_candidate(initial)
        try:
            self._changed_at: Any = None if now is None else float(now)
        except (TypeError, ValueError):
            self._changed_at = None

    def update(self, candidate: Any, now: Any) -> str:
        try:
            want = _normalize_candidate(candidate)
        except Exception:
            return self.displayed
        try:
            now_f = float(now)
        except (TypeError, ValueError):
            return self.displayed
        if want == self.displayed:
            return self.displayed
        # Instant onsets: listening must read live at once; error
        # (not-working) must never wait out a dwell.
        if want in ("listening", "error"):
            self.displayed = want
            self._changed_at = now_f
            return self.displayed
        # First switch after construction is free (no stale dwell).
        if self._changed_at is None:
            self.displayed = want
            self._changed_at = now_f
            return self.displayed
        try:
            held = now_f - float(self._changed_at)
        except (TypeError, ValueError):
            return self.displayed
        if held < 0:
            # Clock stepped back: re-anchor, hold the display.
            self._changed_at = now_f
            return self.displayed
        if held >= MIN_DWELL_S:
            self.displayed = want
            self._changed_at = now_f
        return self.displayed


def _event_age_seconds(event: Any, now: float) -> Optional[float]:
    try:
        if not isinstance(event, dict):
            return None
        ts = float(event.get("ts"))
        age = float(now) - ts
    except (TypeError, ValueError):
        return None
    if age < 0 or age > 3600:
        return None
    return age


def _recent_event_names(events: Any, now: float, window_s: float) -> set:
    names: set = set()
    if not isinstance(events, (list, tuple)):
        return names
    for event in events:
        age = _event_age_seconds(event, now)
        if age is None or age > window_s:
            continue
        try:
            name = str(event.get("event", "") or "")
        except Exception:
            continue
        if name:
            names.add(name)
    return names


def _latest_event_ts(events: Any, now: float, names: set,
                     window_s: float) -> Optional[float]:
    """Newest ts among recent events whose name is in ``names``, else None.

    Used to order a speak marker against its done marker. Never raises.
    """
    latest: Optional[float] = None
    if not isinstance(events, (list, tuple)):
        return None
    for event in events:
        age = _event_age_seconds(event, now)
        if age is None or age > window_s:
            continue
        try:
            if str(event.get("event", "") or "") not in names:
                continue
            ts = float(event.get("ts"))
        except (TypeError, ValueError):
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def resolve_orb_state(status: Any, events: Any, now: float) -> str:
    """Map a status payload + recent journal events to one orb state.

    Never raises on malformed input; unknown/unreachable -> ``"idle"``.
    """
    try:
        now_f = float(now)
    except (TypeError, ValueError):
        return "idle"
    try:
        recent_listen = _recent_event_names(events, now_f, LISTEN_WINDOW_S)
        listening_hit = recent_listen & _LISTENING_EVENTS
        if listening_hit:
            # Echo re-arm guard: the wake segment-open edge re-fires while
            # the room is voiced (own TTS echo), so a bare ``capturing``
            # older than a fresh speak marker is echo, not the owner, and
            # must not re-light listening. Any NON-capturing capture signal
            # (stt.started/partial, the Right-Ctrl key-down edge) still
            # reads live at once — only the re-firing edge is gated.
            non_capture = (listening_hit
                           - {"voice_edit.wake.capturing"})
            if non_capture:
                return "listening"
            try:
                speak_ts = _latest_event_ts(events, now_f,
                                            _SPEAKING_EVENTS,
                                            SPEAK_WINDOW_S)
                cap_ts = _latest_event_ts(
                    events, now_f, {"voice_edit.wake.capturing"},
                    LISTEN_WINDOW_S)
            except Exception:
                return "listening"
            if speak_ts is None or cap_ts is None or cap_ts >= speak_ts:
                return "listening"
            # Echo re-arm: fall through to the speaking/thinking/idle chain
            # (the fresh speak marker below will normally read speaking).
        # Speaking is ACTIVE while the newest speak marker is newer than the
        # newest done marker — so green ends the moment playback finishes
        # instead of lingering for the recency window.
        speak_ts = _latest_event_ts(events, now_f, _SPEAKING_EVENTS,
                                    SPEAK_WINDOW_S)
        if speak_ts is not None:
            done_ts = _latest_event_ts(events, now_f, _SPEAK_DONE_EVENTS,
                                       SPEAK_WINDOW_S)
            if done_ts is None or speak_ts > done_ts:
                return "speaking"
        active = 0
        if isinstance(status, dict):
            try:
                active = int(status.get("active_requests", 0) or 0)
            except (TypeError, ValueError):
                active = 0
        if active > 0:
            return "thinking"
        # Turn over: only the SHORT idle window honors a transcript marker,
        # so a stale stt.final cannot stick yellow for the whole 12 s
        # ceiling once nothing is in flight.
        recent_think = _recent_event_names(events, now_f,
                                           THINK_IDLE_WINDOW_S)
        if recent_think & _THINKING_EVENTS:
            return "thinking"
    except Exception:
        return "idle"
    return "idle"


def plan_toggle(pending_rid: Any, pending_ts: Any, now: float) -> Tuple[str, Optional[str]]:
    """Decide the click action for a staged voice request.

    Returns ``("begin", None)`` when idle (or the staged request is
    missing/expired) and ``("cancel", rid)`` while a staged request is
    still fresh. Never raises; malformed pending state plans ``begin``.
    """
    try:
        now_f = float(now)
    except (TypeError, ValueError):
        return ("begin", None)
    try:
        rid = str(pending_rid or "").strip()
        ts_f = float(pending_ts)
    except (TypeError, ValueError):
        return ("begin", None)
    if not rid:
        return ("begin", None)
    if now_f - ts_f > PENDING_TTL_S or now_f - ts_f < 0:
        return ("begin", None)
    return ("cancel", rid)


__all__ = [
    "ORB_STATES",
    "LISTEN_WINDOW_S",
    "THINK_WINDOW_S",
    "THINK_IDLE_WINDOW_S",
    "SPEAK_WINDOW_S",
    "PENDING_TTL_S",
    "MIN_DWELL_S",
    "DisplayState",
    "resolve_orb_state",
    "plan_toggle",
]
