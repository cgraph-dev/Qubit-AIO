"""A fresh capture signal must survive the authoritative talk stream.

Regression (2026-10-06): the talk-event stream is authoritative, but the helper
emits ``input.speech_started`` ONLY on the Right-Ctrl path. The hands-free wake
path's mic-open edge is the journal ``voice_edit.wake.capturing`` event, which the
talk stream does not model. So while the orb's recent window still carried the
previous turn's terminal ``voice_edit.talk.response.done`` (idle), the
authoritative path returned ``idle`` and MASKED the live wake capture — the orb
no longer showed listening on "hey qubit" (§93 regression).

Fix: an authoritative ``idle`` may not override a fresh, non-echo capture edge
(listening outranks idle). The talk stream still overrides heartbeat-level
listening (pinned elsewhere) and every non-idle talk state.

Hermetic: pure functions; no I/O, display, or network.
"""
from __future__ import annotations

import time

from voicebridge.orb import state as orb_state
from voicebridge.orb import talk_state


def _talk_done(ts: float, turn_id: str = "t1"):
    return {"event": "voice_edit.talk.response.done", "ts": ts,
            "turn_id": turn_id, "seq": 1}


def _capturing(ts: float):
    return {"ts": ts, "event": "voice_edit.wake.capturing"}


def test_fresh_wake_capture_is_not_masked_by_a_stale_terminal_talk_event():
    now = time.time()
    events = [_talk_done(now - 30.0), _capturing(now - 0.5)]
    talk = talk_state.talk_events_from_journal(events)
    assert talk_state.state_from_talk_events(talk) == "idle"
    # Inference alone reads the live capture...
    assert orb_state.resolve_orb_state({}, events, now) == "listening"
    # ...and the authoritative seam must not swallow it.
    assert orb_state.resolve_orb_state(
        {}, events, now, talk_events=talk) == "listening"


def test_right_ctrl_capture_event_also_survives_terminal_talk():
    now = time.time()
    events = [_talk_done(now - 40.0),
              {"ts": now - 0.3, "event": "voice_edit.helper.listening"}]
    talk = talk_state.talk_events_from_journal(events)
    assert orb_state.resolve_orb_state(
        {}, events, now, talk_events=talk) == "listening"


def test_non_idle_talk_state_still_wins_over_a_capture_event():
    now = time.time()
    events = [{"ts": now - 0.5, "event": "voice_edit.wake.capturing"},
              {"event": "voice_edit.talk.transcript.final", "ts": now - 0.2,
               "turn_id": "t2", "seq": 2}]
    talk = talk_state.talk_events_from_journal(events)
    assert orb_state.resolve_orb_state(
        {}, events, now, talk_events=talk) == "thinking"


def test_terminal_talk_without_a_capture_event_is_still_idle():
    now = time.time()
    events = [_talk_done(now - 1.0)]
    talk = talk_state.talk_events_from_journal(events)
    assert orb_state.resolve_orb_state(
        {}, events, now, talk_events=talk) == "idle"
    # And with no events at all (the pinned heartbeat-override case).
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [], now,
        heartbeat={"state": "listening"},
        talk_events=talk) == "idle"


def test_echo_rearmed_capturing_still_not_listening_under_terminal_talk():
    """A capturing edge older than a fresh speak marker is own-TTS echo."""
    now = time.time()
    events = [_talk_done(now - 40.0),
              {"ts": now - 1.0, "event": "voice_edit.wake.capturing"},
              {"ts": now - 0.2, "event": "tts.speak"}]
    talk = talk_state.talk_events_from_journal(events)
    # Echo re-arm must not re-light listening; the fresh speak marker wins.
    assert orb_state.resolve_orb_state(
        {}, events, now, talk_events=talk) == "speaking"
