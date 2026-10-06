"""Pure talk-event -> orb-state FSM (``voicebridge.voice_edit.talk_fsm``).

Hermetic: pure reducer, no I/O, no threads, no clock. Pins the contract
observers/the orb depend on:

- every documented boundary maps to the documented orb state;
- overlapping events resolve by the orb's priority
  (listening > speaking > working > thinking > idle), error never masked;
- unknown/unmapped/malformed events leave the state unchanged;
- the reducer never raises on any input.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.orb.state import ORB_STATES as LIVE_ORB_STATES
from voicebridge.voice_edit.talk_fsm import (
    ERROR_STATE,
    ORB_STATES,
    TALK_EVENT_STATES,
    TALK_PRIORITY,
    TalkStateMachine,
    event_state,
    normalize_state,
    reduce,
    reduce_many,
)

#: The exact mapping the task requires.
_REQUIRED_MAPPING = {
    "input.speech_started": "listening",
    "interrupt": "listening",
    "transcript.final": "thinking",
    "response.started": "thinking",
    "tool.call": "working",
    "response.audio.delta": "speaking",
    "playback.started": "speaking",
    "response.done": "idle",
    "playback.stopped": "idle",
    "error": "error",
}


def test_orb_vocabulary_matches_the_live_orb_module():
    assert ORB_STATES == tuple(LIVE_ORB_STATES)
    assert set(TALK_EVENT_STATES.values()) - {ERROR_STATE} <= set(ORB_STATES)


def test_every_required_boundary_maps_to_its_orb_state():
    for event_type, state in _REQUIRED_MAPPING.items():
        assert TALK_EVENT_STATES[event_type] == state
        assert reduce("idle", event_type) == state
        assert event_state(event_type) == state


def test_envelope_dicts_are_accepted_like_bare_types():
    assert reduce("idle", {"type": "response.audio.delta", "seq": 4}) == "speaking"
    assert event_state({"type": "tool.call"}) == "working"


def test_priority_order_matches_the_orb_resolver_and_error_outranks_all():
    order = {state: i for i, state in enumerate(TALK_PRIORITY)}
    assert order[ERROR_STATE] < order["listening"]
    assert (order["listening"] < order["speaking"] < order["working"]
            < order["thinking"] < order["idle"])


def test_reduce_many_resolves_overlaps_by_priority():
    assert reduce_many("idle", ["response.done", "response.audio.delta",
                                "input.speech_started"]) == "listening"
    assert reduce_many("idle", ["transcript.final", "playback.started"]) \
        == "speaking"
    assert reduce_many("idle", ["transcript.final", "tool.call"]) == "working"
    assert reduce_many("idle", ["transcript.final", "response.done"]) \
        == "thinking"
    assert reduce_many("idle", ["response.done", "error"]) == "error"
    # Envelope batches behave identically to bare-type batches.
    assert reduce_many("idle", [{"type": "tool.call"},
                                {"type": "playback.started"}]) == "speaking"


def test_reduce_many_with_no_mapped_event_keeps_prev_state():
    assert reduce_many("thinking", []) == "thinking"
    assert reduce_many("thinking", ["not.a.type", "session.state", None]) \
        == "thinking"
    assert reduce_many("idle", ["response.done"]) == "idle"


def test_unknown_unmapped_and_malformed_events_leave_state_unchanged():
    assert reduce("listening", "totally.unknown") == "listening"
    assert reduce("listening", {"type": "session.state"}) == "listening"
    assert reduce("listening", {"not": "an envelope"}) == "listening"
    assert reduce("listening", None) == "listening"
    assert reduce("listening", 12345) == "listening"
    assert reduce("listening", ["response.started"]) == "listening"
    # A malformed PREV state folds to idle when nothing maps.
    assert reduce("bogus", "totally.unknown") == "idle"
    # ... but a mapped event still wins over a malformed prev state.
    assert reduce("bogus", "response.started") == "thinking"


def test_normalize_state_folds_junk_to_idle():
    assert normalize_state("SPEAKING") == "speaking"
    assert normalize_state(" error ") == "error"
    assert normalize_state(None) == "idle"
    assert normalize_state(123) == "idle"
    assert normalize_state(object()) == "idle"


def test_state_machine_apply_sequence_and_reset():
    machine = TalkStateMachine()
    assert machine.current() == "idle"
    assert machine.apply("input.speech_started") == "listening"
    assert machine.apply("transcript.final") == "thinking"
    assert machine.apply("tool.call") == "working"
    assert machine.apply("playback.started") == "speaking"
    assert machine.apply("playback.stopped") == "idle"
    assert machine.apply("bogus") == "idle"
    assert machine.apply_many(["input.speech_started", "error"]) == "error"
    assert machine.reset() == "idle"
    assert TalkStateMachine("speaking").current() == "speaking"


def test_reducer_never_raises_on_any_input():
    class Weird:
        def __str__(self):
            raise RuntimeError("no str")

    for bad in (None, 0, 1.5, [], {}, object(), Weird(),
                {"type": 123}, {"type": None}):
        event_state(bad)          # must not raise
        reduce("idle", bad)       # must not raise
        reduce(bad, bad)
        reduce_many(bad, bad)
        reduce_many("listening", [bad, bad])
    assert reduce_many("listening", None) == "listening"
    assert reduce_many("listening", 12345) == "listening"
