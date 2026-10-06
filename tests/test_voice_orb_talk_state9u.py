"""Authoritative talk-event -> orb state wiring (``voicebridge.orb.talk_state``).

Hermetic: pure filters/reducers, no I/O, no display, no network. Pins the
contract that closes the "talk FSM not wired" gap:

- ``talk_events_from_journal`` filters the journal to ``voice_edit.talk.*``
  rows and normalizes them to the sequencer envelope shape;
- ``state_from_talk_events`` reduces the most recent turn through the voice
  FSM's priority reducer (``talk_fsm.reduce_many``) to one orb state, and
  returns ``None`` when there are no talk events at all;
- ``resolve_orb_state(..., talk_events=...)`` is authoritative when the talk
  stream yields a state, and byte-identical to the legacy inference when it
  does not;
- nothing raises on junk.
"""
from __future__ import annotations

import time

from voicebridge.orb import state as orb_state
from voicebridge.orb import talk_state


def _row(etype: str, ts: float, turn_id: str = "t1"):
    """A raw journal row as ``logging_setup.log_event`` writes it."""
    return {
        "event": "voice_edit.talk." + etype,
        "ts": ts,
        "turn_id": turn_id,
        "seq": 1,
        "pid": 4242,
    }


def _ev(etype: str, at: float, turn_id: str = "t1"):
    """A normalized talk event as ``talk_events_from_journal`` returns it."""
    return {"type": etype, "at": at, "turn_id": turn_id}


# -- talk_events_from_journal ------------------------------------------------

def test_from_journal_filters_and_normalizes():
    rows = [
        {"event": "stt.final", "ts": 0.5},              # not a talk row
        _row("input.speech_started", 1.0),
        _row("transcript.final", 2.0),
        _row("response.audio.delta", 3.0),
        {"event": "voice_edit.helper.speak", "ts": 3.5},  # another stream
    ]
    assert talk_state.talk_events_from_journal(rows) == [
        {"type": "input.speech_started", "at": 1.0, "turn_id": "t1"},
        {"type": "transcript.final", "at": 2.0, "turn_id": "t1"},
        {"type": "response.audio.delta", "at": 3.0, "turn_id": "t1"},
    ]


def test_from_journal_keeps_only_the_three_normalized_keys():
    (only,) = talk_state.talk_events_from_journal(
        [_row("playback.stopped", 7.25, "turn-9")])
    assert only == {"type": "playback.stopped", "at": 7.25, "turn_id": "turn-9"}
    assert set(only) == {"type", "at", "turn_id"}


def test_from_journal_ignores_malformed_and_unknown_rows():
    rows = [
        None, "x", 5, [], {},
        {"event": None},
        {"event": 123},
        {"event": "voice_edit.talk."},           # empty type
        {"event": "voice_edit.talk.bogus"},      # unknown boundary
        {"event": "voice_edit.talkback.x"},      # wrong prefix
        _row("playback.stopped", 9.0),
    ]
    assert talk_state.talk_events_from_journal(rows) == [
        {"type": "playback.stopped", "at": 9.0, "turn_id": "t1"},
    ]


def test_from_journal_non_list_is_empty_and_never_raises():
    class Weird:
        def __str__(self):
            raise RuntimeError("no str")

    for junk in (None, "x", 5, object(), Weird(), {"event": "voice_edit.talk.error"}):
        assert talk_state.talk_events_from_journal(junk) == []


# -- state_from_talk_events --------------------------------------------------

_TURN = [
    _ev("input.speech_started", 1.0),
    _ev("transcript.final", 2.0),
    _ev("response.started", 3.0),
    _ev("response.audio.delta", 4.0),
    _ev("playback.stopped", 5.0),
]
_EXPECTED = ["listening", "thinking", "thinking", "speaking", "idle"]


def test_full_turn_prefixes_map_to_the_expected_sequence():
    states = [talk_state.state_from_talk_events(_TURN[:i])
              for i in range(1, len(_TURN) + 1)]
    assert states == _EXPECTED
    # The completed turn is over: idle, not a lingering early boundary.
    assert talk_state.state_from_talk_events(_TURN) == "idle"


def test_each_boundary_maps_when_reduced_alone():
    assert [talk_state.state_from_talk_events([ev])
            for ev in _TURN] == _EXPECTED


def test_tool_and_error_boundaries_map():
    assert talk_state.state_from_talk_events([_ev("tool.call", 1.0)]) == "working"
    assert talk_state.state_from_talk_events([_ev("error", 1.0)]) == "error"


def test_most_recent_turn_wins_over_an_earlier_turn():
    events = [
        _ev("input.speech_started", 1.0, "t1"),
        _ev("response.done", 2.0, "t1"),
        _ev("input.speech_started", 3.0, "t2"),
    ]
    assert talk_state.state_from_talk_events(events) == "listening"
    # The newest turn's terminal boundary clears it.
    done = [
        _ev("input.speech_started", 1.0, "t1"),
        _ev("response.done", 2.0, "t2"),
    ]
    assert talk_state.state_from_talk_events(done) == "idle"


def test_simultaneous_events_resolve_by_the_fsm_priority():
    assert talk_state.state_from_talk_events(
        [_ev("input.speech_started", 1.0), _ev("response.audio.delta", 1.0)]
    ) == "listening"
    assert talk_state.state_from_talk_events(
        [_ev("transcript.final", 1.0), _ev("playback.started", 1.0)]
    ) == "speaking"
    assert talk_state.state_from_talk_events(
        [_ev("transcript.final", 1.0), _ev("error", 1.0)]
    ) == "error"


def test_accepts_raw_journal_rows_and_bare_types():
    rows = [_row("input.speech_started", 1.0, "t1"),
            _row("tool.call", 2.0, "t1")]
    assert talk_state.state_from_talk_events(rows) == "working"
    assert talk_state.state_from_talk_events(["response.done"]) == "idle"


def test_no_talk_events_returns_none():
    for empty in ([], (), None, "x", 5, object(),
                  [{"event": "stt.started"}], [None, "x", {}],
                  [{"type": "not.a.talk.type"}]):
        assert talk_state.state_from_talk_events(empty) is None


def test_talk_events_with_no_mapped_boundary_read_idle():
    # A talk event IS present, so the stream is authoritative; the FSM
    # leaves an unmapped boundary (partial transcript) at idle.
    assert talk_state.state_from_talk_events(
        [_row("transcript.partial", 1.0)]) == "idle"


def test_state_from_talk_events_never_raises_on_junk():
    class Weird:
        def __str__(self):
            raise RuntimeError("no str")

    for junk in (None, "x", 5, 1.5, [], {}, object(), Weird(),
                 [None, "x", {}, Weird(), {"type": 5}, {"event": 5},
                  {"type": "response.started", "at": "soon"}]):
        out = talk_state.state_from_talk_events(junk)
        assert out is None or out in orb_state.ORB_STATES or out == "error"


# -- resolve_orb_state authoritative seam ------------------------------------

def test_talk_events_override_the_recency_inference():
    now = time.time()
    # Inference alone reads speaking (fresh tts.speak)...
    evs = [{"ts": now - 0.5, "event": "tts.speak"}]
    assert orb_state.resolve_orb_state({}, evs, now) == "speaking"
    # ...but the authoritative talk stream asserting thinking wins.
    assert orb_state.resolve_orb_state(
        {}, evs, now,
        talk_events=[_ev("transcript.final", now)]) == "thinking"


def test_talk_events_override_the_heartbeat_too():
    now = time.time()
    heard = orb_state.resolve_orb_state(
        {"active_requests": 4}, [], now,
        heartbeat={"state": "listening"})
    assert heard == "listening"
    assert orb_state.resolve_orb_state(
        {"active_requests": 4}, [], now,
        heartbeat={"state": "listening"},
        talk_events=[_ev("playback.stopped", now)]) == "idle"


def test_talk_events_none_or_empty_is_byte_identical():
    now = time.time()
    cases = [
        ({}, [], now),
        ({"active_requests": 2}, [], now),
        ({}, [{"ts": now - 1.0, "event": "stt.started"}], now),
        ({}, [{"ts": now - 1.0, "event": "tts.speak"}], now),
        ({"active_requests": 1},
         [{"ts": now - 1.0, "event": "broker.exec.start"}], now),
        ({"active_requests": 0}, [{"ts": now - 1.0, "event": "stt.final"}], now),
    ]
    for status, evs, t in cases:
        legacy = orb_state.resolve_orb_state(status, evs, t)
        assert orb_state.resolve_orb_state(status, evs, t, talk_events=None) == legacy
        assert orb_state.resolve_orb_state(status, evs, t, talk_events=[]) == legacy


def test_resolve_never_raises_on_junk_talk_events():
    now = time.time()

    class Weird:
        def __str__(self):
            raise RuntimeError("no str")

    for junk in (None, "x", 5, [], {}, object(), Weird(),
                 [None, Weird(), {"type": 9}]):
        out = orb_state.resolve_orb_state({}, [], now, talk_events=junk)
        assert out in orb_state.ORB_STATES or out == "error"
