"""Authoritative voice talk-events sequencer (OpenClaw ``talk/talk-events`` port).

Hermetic: pure class, injectable clock, monkeypatched journal. No I/O, no
threads, no service. Pins the contract that observers/UI depend on:

- ``seq`` is monotonic and gap-free across emits (rejections never consume it).
- turn ids are minted uniquely; events carry the current turn id.
- unknown event types are REJECTED with ``ValueError`` (documented contract).
- malformed fields never raise and cannot corrupt the envelope.
- optional journaling is fail-soft.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import logging_setup
from voicebridge.voice_edit.talk_events import (
    TALK_EVENT_TYPES,
    TalkEventSequencer,
)


_REQUIRED_TYPES = {
    "session.state",
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
}


def test_event_types_are_a_frozenset_of_at_least_the_required_boundaries():
    assert isinstance(TALK_EVENT_TYPES, frozenset)
    assert _REQUIRED_TYPES <= TALK_EVENT_TYPES


def test_seq_is_monotonic_and_gap_free_across_many_emits():
    seq = TalkEventSequencer(now=lambda: 1.0)
    events = [seq.emit("transcript.partial") for _ in range(500)]
    assert [e["seq"] for e in events] == list(range(1, 501))
    assert [e["seq"] for e in seq.snapshot()["events"]] == list(range(1, 501))


def test_rejected_emit_does_not_consume_a_sequence_number():
    seq = TalkEventSequencer(now=lambda: 1.0)
    assert seq.emit("response.started")["seq"] == 1
    try:
        seq.emit("not.a.real.type")
    except ValueError:
        pass
    assert seq.emit("response.done")["seq"] == 2


def test_begin_turn_mints_unique_ids_and_events_carry_the_current_turn():
    seq = TalkEventSequencer(now=lambda: 1.0)
    first = seq.begin_turn()
    second = seq.begin_turn()
    assert first != second
    assert seq.current_turn() == second
    ev = seq.emit("transcript.final")
    assert ev["turn_id"] == second
    # An explicit turn id still wins over the current turn.
    other = seq.emit("transcript.final", turn_id=first)
    assert other["turn_id"] == first
    # Ids minted across many turns are all unique.
    ids = {seq.begin_turn() for _ in range(100)}
    assert len(ids) == 100


def test_end_turn_clears_current_turn_and_events_fall_back_to_none():
    seq = TalkEventSequencer(now=lambda: 1.0)
    minted = seq.begin_turn()
    assert seq.end_turn() == minted
    assert seq.current_turn() is None
    assert seq.end_turn() is None
    assert seq.emit("session.state")["turn_id"] is None


def test_unknown_type_is_rejected_with_value_error():
    seq = TalkEventSequencer(now=lambda: 1.0)
    for bogus in ("", "nope", "session.finished", None, 123, ["session.state"]):
        try:
            seq.emit(bogus)  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"emit accepted unknown type {bogus!r}")
    assert seq.snapshot()["seq"] == 0


def test_snapshot_shape_and_envelope_keys():
    seq = TalkEventSequencer(now=lambda: 7.5)
    empty = seq.snapshot()
    assert empty == {
        "seq": 0,
        "turn_id": None,
        "turn_count": 0,
        "events": (),
        "event_count": 0,
    }
    turn = seq.begin_turn()
    event = seq.emit("tool.call", name="graft")
    assert set(event) >= {"seq", "type", "turn_id", "at", "name"}
    assert event["seq"] == 1
    assert event["type"] == "tool.call"
    assert event["turn_id"] == turn
    assert event["at"] == 7.5
    assert event["name"] == "graft"
    snap = seq.snapshot()
    assert snap["seq"] == 1
    assert snap["turn_id"] == turn
    assert snap["turn_count"] == 1
    assert snap["event_count"] == 1
    assert snap["events"] == (event,)
    # snapshot exposes a copy, not writable internal state.
    assert isinstance(snap["events"], tuple)


def test_injectable_clock_sets_exact_at_per_event():
    ticks = iter([10.0, 11.5, 12.25])
    seq = TalkEventSequencer(now=lambda: next(ticks))
    assert seq.emit("input.speech_started")["at"] == 10.0
    assert seq.emit("input.speech_stopped")["at"] == 11.5
    assert seq.emit("response.started")["at"] == 12.25


def test_malformed_clock_never_raises_and_reuses_last_timestamp():
    seq = TalkEventSequencer(now=lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    first = seq.emit("session.state")
    assert first["at"] == 0.0
    second = seq.emit("session.state")
    assert second["at"] == 0.0


def test_junk_fields_never_raise_and_cannot_corrupt_the_envelope():
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    seq = TalkEventSequencer(now=lambda: 3.0)
    seq.begin_turn()
    # ``type``/``turn_id`` are named parameters, so a caller cannot inject them
    # through **fields; ``seq``/``at`` can, and must be ignored.
    event = seq.emit(
        "error",
        seq=999,
        at="not-a-time",
        message="boom",
        detail=Opaque(),
        nested={"a": [1, 2, 3]},
        missing=None,
    )
    assert event["seq"] == 1          # reserved key ignored
    assert event["type"] == "error"
    assert event["turn_id"] == "turn-1"
    assert event["at"] == 3.0         # reserved key ignored
    assert event["message"] == "boom"
    assert event["missing"] is None
    assert event["nested"] == {"a": [1, 2, 3]}
    assert seq.snapshot()["seq"] == 1


def test_journal_path_is_fail_soft_when_log_event_throws(monkeypatch):
    seq = TalkEventSequencer(now=lambda: 1.0)

    def boom(*_args, **_kwargs):
        raise RuntimeError("journal down")

    monkeypatch.setattr(logging_setup, "log_event", boom)
    event = seq.emit("response.done", journal=True)
    assert event["seq"] == 1
    assert event["type"] == "response.done"


def test_journal_path_writes_typed_event_name_with_seq_and_turn(monkeypatch):
    calls = []

    def capture(name, **fields):
        calls.append((name, fields))

    monkeypatch.setattr(logging_setup, "log_event", capture)
    seq = TalkEventSequencer(now=lambda: 1.0)
    seq.begin_turn()
    seq.emit("tool.call", journal=True)
    seq.emit("tool.call", journal=False)
    assert calls == [("voice_edit.talk.tool.call", {"seq": 1, "turn_id": "turn-1"})]


def test_max_events_bounds_history_without_affecting_seq():
    seq = TalkEventSequencer(now=lambda: 1.0, max_events=3)
    for _ in range(5):
        seq.emit("session.state")
    snap = seq.snapshot()
    assert snap["seq"] == 5
    assert snap["event_count"] == 3
    assert [e["seq"] for e in snap["events"]] == [3, 4, 5]
