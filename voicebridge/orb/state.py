"""Orb state resolution: status payload + voice journal events -> waveform state.

Mirrors the dashboard widget mapping
(``frontend/qubit-web/src/lib/voice/waveform-state.ts``,
``resolveWaveformState``): priority listening > speaking > working >
thinking > idle (speaking outranks thinking since 2026-10-04 — a fresh
stt.final inside its 12 s window used to mask an already-playing reply
yellow; working was added 2026-10-06 so a running tool/action reads
distinctly from a bare model turn); anything unknown falls back to idle
so the orb can never get stuck animating a state it does not understand.

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

ORB_STATES = ("idle", "listening", "thinking", "working", "speaking")

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

#: Tool/action execution events: the agent is DOING something (a distinct
#: amber "working" action, vs the indigo "thinking" of a bare model turn).
#: ``broker.exec.*`` is the canonical tool-execution envelope; the
#: ``worker.exec.*`` pair covers the headless worker path.
_WORKING_START_EVENTS = frozenset(
    {"broker.exec.start", "worker.exec.start", "worker.dispatch"})
_WORKING_END_EVENTS = frozenset(
    {"broker.exec.finish", "worker.exec.finish"})

#: A tool/action run is treated as live this long after its start marker
#: when no finish marker arrived (crash/leak backstop, same spirit as
#: THINK_WINDOW_S).
WORK_WINDOW_S = 120.0

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
#: Transcript-completion ceiling while a turn is in flight
#: (``active_requests > 0``): the counter alone cannot stick thinking —
#: it is cross-checked against the newest ``stt.final``/``stt.completed``
#: marker, so a leaked count clears at most this long past the last
#: transcript evidence (fail-closed: no evidence at all still trusts the
#: counter). Once the counter returns to 0 only the short idle window
#: below applies.
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

#: How far back the state feed is scanned for the turn's live request id.
#: A turn's begin/capture row is appended at the START of the turn, so the
#: window must span a whole turn (brain latency + playback), not just the
#: short speak window. Same TTL the service itself uses.
ACTIVE_REQUEST_WINDOW_S = PENDING_TTL_S

#: States in which a second orb press means STOP. While a turn is producing
#: output (thinking/working) or its reply is playing (speaking), cancelling
#: the live request is what stops it. ``listening`` is deliberately NOT in
#: this set: a press there must FINISH the capture (the helper's toggle), and
#: cancelling would throw the utterance away.
CANCELABLE_STATES = frozenset({"thinking", "working", "speaking"})

#: Request-id rows that END a turn rather than start one. If the newest
#: request-id row is one of these, the id it carries is no longer live.
_RID_END_EVENTS = frozenset({"stt.cancelled", "voice_edit.cancel"})

#: Minimum dwell per displayed state: the visible orb state holds at
#: least this long before switching, so the 2s poll recency windows
#: cannot flap listening<->idle on one stale/missing event. Listening
#: onset is instant (capture must read live the moment it starts) and
#: error is immediate (not-working must never wait out a dwell).
MIN_DWELL_S = 0.35


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
        if want in ("listening", "speaking", "error"):
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


def speaking_markers(events: Any, now: float,
                     ) -> Tuple[Optional[float], Optional[float]]:
    """Newest ``(speak_ts, done_ts)`` inside the speaking window.

    Either side is ``None`` when that marker is absent/stale. Pure, never
    raises; used to drive the output-activity tracker's playback state.
    """
    return (
        _latest_event_ts(events, now, _SPEAKING_EVENTS, SPEAK_WINDOW_S),
        _latest_event_ts(events, now, _SPEAK_DONE_EVENTS, SPEAK_WINDOW_S),
    )


def _activity_is_active(activity: Any, now: float) -> bool:
    """Read an optional output-activity signal. Never raises.

    Accepts an :class:`~voicebridge.orb.activity.OutputActivityTracker`
    (calls ``is_active(now)``) or a plain truthy/falsy value, so both the
    live orb and hermetic tests can feed the same optional seam.
    """
    if activity is None:
        return False
    try:
        probe = getattr(activity, "is_active", None)
        if callable(probe):
            return bool(probe(now))
        return bool(activity)
    except Exception:
        return False


def resolve_orb_state(status: Any, events: Any, now: float,
                      heartbeat: Any = None, activity: Any = None,
                      talk_events: Any = None) -> str:
    """Map a status payload + recent journal events to one orb state.

    ``heartbeat`` (optional) is the desktop-helper heartbeat mapping. It
    carries two EXACT signals the poll's recency windows cannot match:

    - ``state`` == ``"listening"`` / ``"processing"``: the helper's own
      Right-Ctrl state machine (mic open / turn running), so onset and
      offset are exact instead of a fixed window.
    - ``spec_tts`` (non-empty) + ``spec_tts_ts``: the synthesized reply's
      band timeline. Its presence means audio is playing RIGHT NOW, so a
      long reply stays green for its whole duration instead of dropping
      to idle when the 4 s event window expires (live 2026-10-06: a
      95-char weather answer was green only ~2.5 s then orange while the
      agent was still talking).

    ``activity`` (optional) is an output-activity signal — an
    :class:`~voicebridge.orb.activity.OutputActivityTracker` or a plain
    truthy value. While it reads active, ``speaking`` stays true even if
    the heartbeat TTS timeline is momentarily empty (a missed/unseen
    frame), and it ends at the tracker's tail. ``None`` (the default)
    keeps the legacy behavior byte-identical.

    ``talk_events`` (optional) is the authoritative typed talk-event
    sequence for the current window (see
    :mod:`voicebridge.orb.talk_state`). When it reduces to a state (not
    ``None``), that state is returned verbatim -- the orb then matches the
    voice FSM exactly. When it is ``None``/empty (or reduces to ``None``),
    resolution falls through to the inference below, byte-identically to
    before this seam existed.

    One exception keeps the seam from hiding a live signal: the talk stream
    models the Right-Ctrl path's ``input.speech_started`` but NOT the
    hands-free wake path's mic-open edge, and it can be stale for
    service/browser TTS. So when the authoritative state is ``idle`` (the
    absence of any active boundary), the recency inference is re-run over the
    JOURNAL alone (no status counter, no heartbeat) and any non-``idle``
    result wins -- restoring listening/speaking/working for a fresh capture
    or TTS edge while still honoring the pinned "talk idle overrides
    heartbeat/status" contract.

    Never raises on malformed input; unknown/unreachable -> ``"idle"``.
    """
    try:
        now_f = float(now)
    except (TypeError, ValueError):
        now_f = None
    if talk_events is not None:
        try:
            from . import talk_state as _talk_state
            authoritative = _talk_state.state_from_talk_events(talk_events)
        except Exception:
            authoritative = None
        if authoritative is not None:
            if authoritative == "idle" and now_f is not None:
                try:
                    inferred = resolve_orb_state({}, events, now_f)
                except Exception:
                    inferred = "idle"
                if inferred != "idle":
                    return inferred
            return authoritative
    if now_f is None:
        return "idle"
    now_f = float(now_f)
    hb = heartbeat if isinstance(heartbeat, dict) else {}
    try:
        hb_state = str(hb.get("state") or "").strip().lower()
    except Exception:
        hb_state = ""
    hb_tts_aware = "spec_tts" in hb
    tts_active = False
    tts_ts = 0.0
    if hb_tts_aware:
        try:
            tts_active = bool(hb.get("spec_tts"))
            tts_ts = float(hb.get("spec_tts_ts") or 0.0)
        except Exception:
            tts_active = False
    try:
        # -- 1. listening: helper state is exact (Right-Ctrl mic open);
        #       the wake path arrives as capture events below.
        if hb_state == "listening":
            return "listening"
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
        # -- 2. speaking. EXACT span from the helper's TTS timeline when
        #       available (green for the whole reply); the event window is
        #       kept as an onset fallback (and for older helpers / other
        #       TTS paths). A done/barge-in marker newer than the timeline
        #       start cuts green immediately.
        speaking = False
        if hb_tts_aware and tts_active:
            done_ts = _latest_event_ts(events, now_f, _SPEAK_DONE_EVENTS,
                                       3600.0)
            if done_ts is None or done_ts <= tts_ts:
                speaking = True
        if not speaking:
            speak_ts = _latest_event_ts(events, now_f, _SPEAKING_EVENTS,
                                        SPEAK_WINDOW_S)
            if speak_ts is not None:
                done_ts = _latest_event_ts(events, now_f, _SPEAK_DONE_EVENTS,
                                           SPEAK_WINDOW_S)
                if done_ts is None or speak_ts > done_ts:
                    speaking = True
        # Output-activity extension (2026-10-06): a live tracker keeps
        # green while reply audio flows even when the heartbeat timeline
        # is momentarily empty (a missed frame/event). Inactive -> the
        # legacy decision above stands unchanged (this only extends, it
        # never curtails the done-marker rule).
        if not speaking and _activity_is_active(activity, now_f):
            speaking = True
        if speaking:
            return "speaking"
        # -- 3. working (amber): a tool/action run is live when its start
        #       marker is newer than its finish marker (and not ancient).
        work_start = _latest_event_ts(events, now_f,
                                      _WORKING_START_EVENTS, WORK_WINDOW_S)
        if work_start is not None:
            work_end = _latest_event_ts(events, now_f,
                                        _WORKING_END_EVENTS, WORK_WINDOW_S)
            if work_end is None or work_start > work_end:
                return "working"
        # -- 4. thinking: the helper's PROCESSING state is exact; the
        #       service turn counter + transcript events cover the rest.
        if hb_state == "processing":
            return "thinking"
        active = 0
        if isinstance(status, dict):
            try:
                active = int(status.get("active_requests", 0) or 0)
            except (TypeError, ValueError):
                active = 0
        if active > 0:
            # In-flight cap (2026-10-05): the counter alone cannot stick
            # thinking forever (a leaked count has no age), so it is
            # cross-checked against the newest turn evidence. Fresh
            # evidence inside THINK_WINDOW_S -> thinking; stale evidence
            # (older than the ceiling) falls through to the short idle
            # window below (which is also stale -> idle). No evidence at
            # all trusts the status (fail-closed: the counter is the only
            # signal and contradicting it would hide a real turn).
            fresh = _latest_event_ts(events, now_f, _THINKING_EVENTS,
                                     THINK_WINDOW_S)
            if fresh is not None:
                return "thinking"
            ever = _latest_event_ts(events, now_f, _THINKING_EVENTS, 3600.0)
            if ever is None:
                return "thinking"
            # Stale: fall through (short idle window -> idle).
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


def active_request(events: Any, now: float,
                   window_s: float = ACTIVE_REQUEST_WINDOW_S
                   ) -> Tuple[Optional[str], Optional[float]]:
    """Newest live request id carried by the state feed.

    The voice journal's capture rows already carry the request id
    (``voice_edit.helper.listening``, ``.transcribe.start``, ``.transcript``,
    the wake equivalents, ...), so the orb can learn which staged request a
    second press must cancel WITHOUT a second HTTP call or a new status
    field. Returns ``(rid, ts)``: the newest such id inside ``window_s``, or
    ``(None, None)`` when there is none. A newest row that ends the turn
    (``stt.cancelled``) clears the id instead of resurrecting it.

    Pure, deterministic (newest ``ts`` wins) and never raises.
    """
    try:
        now_f = float(now)
    except (TypeError, ValueError):
        return (None, None)
    try:
        window = float(window_s)
    except (TypeError, ValueError):
        window = ACTIVE_REQUEST_WINDOW_S
    if not isinstance(events, (list, tuple)):
        return (None, None)
    best_ts: Optional[float] = None
    best: Optional[str] = None
    best_event = ""
    for event in events:
        try:
            rid = str(event.get("request_id") or "").strip()
            if not rid:
                continue
            ts = float(event.get("ts"))
        except (TypeError, ValueError):
            continue
        except Exception:
            continue
        try:
            age = now_f - ts
        except Exception:
            continue
        if not (0.0 <= age <= window):
            continue
        if best_ts is None or ts >= best_ts:
            best_ts = ts
            best = rid
            try:
                best_event = str(event.get("event") or "")
            except Exception:
                best_event = ""
    if best_ts is None:
        return (None, None)
    if best_event in _RID_END_EVENTS:
        return (None, best_ts)
    return (best, best_ts)


def _fresh_request(rid: Any, ts: Any, now_f: float) -> Optional[str]:
    """``rid`` when it is a well-formed id inside the pending TTL, else None."""
    try:
        clean = str(rid or "").strip()
        ts_f = float(ts)
    except (TypeError, ValueError):
        return None
    age = now_f - ts_f
    if not clean or age > PENDING_TTL_S or age < 0:
        return None
    return clean


def plan_toggle(pending_rid: Any, pending_ts: Any, now: float,
                *, orb_state: Any = None, active_rid: Any = None,
                active_ts: Any = None
                ) -> Tuple[str, Optional[str]]:
    """Decide the click action for an orb press.

    Returns ``("begin", None)`` when idle (or the staged request is
    missing/expired) and ``("cancel", rid)`` while a request is live.

    A LIVE turn wins over the staged fallback. When ``orb_state`` is one of
    :data:`CANCELABLE_STATES` (thinking/working/speaking) and the state feed
    reported a fresh ``active_rid``, a press must CANCEL that request — the
    second press used to re-plan ``begin`` and re-trigger capture, so the
    reply kept playing. ``listening`` is excluded: there a press finishes
    the capture. When no live turn is known, the original staged-request
    behavior is unchanged (so the three-argument call still plans exactly as
    before). Never raises; malformed state plans ``begin``.
    """
    try:
        now_f = float(now)
    except (TypeError, ValueError):
        return ("begin", None)
    # 1. A live, cancelable turn: STOP it.
    try:
        state_key = str(orb_state or "").strip().lower()
    except Exception:
        state_key = ""
    if state_key in CANCELABLE_STATES:
        live = _fresh_request(active_rid, active_ts, now_f)
        if live:
            return ("cancel", live)
    # 2. A staged (stage-only) request: cancel it while it is fresh.
    rid = _fresh_request(pending_rid, pending_ts, now_f)
    if rid:
        return ("cancel", rid)
    return ("begin", None)


__all__ = [
    "ORB_STATES",
    "LISTEN_WINDOW_S",
    "THINK_WINDOW_S",
    "THINK_IDLE_WINDOW_S",
    "SPEAK_WINDOW_S",
    "PENDING_TTL_S",
    "ACTIVE_REQUEST_WINDOW_S",
    "CANCELABLE_STATES",
    "MIN_DWELL_S",
    "DisplayState",
    "resolve_orb_state",
    "speaking_markers",
    "active_request",
    "plan_toggle",
]
