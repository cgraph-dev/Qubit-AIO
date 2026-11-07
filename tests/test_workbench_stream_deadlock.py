"""The chat stream reader must not deadlock against its own run lock.

`_Run.changed` is a ``Condition`` wrapping the SAME non-reentrant ``threading.Lock``
that ``snapshot()`` acquires. The reader used to call ``snapshot()`` from inside
``with run.changed:`` — a self-deadlock on every single turn. The observable
symptom was a workbench turn that opened the SSE stream, received only
``: connected``, and never delivered the ``token``/``done`` events the pump had
already buffered: the reader was wedged holding the lock, so the pump's
``notify_all()`` could never wake it (and the pump itself blocked on ``push``).

These tests pin the behaviour that matters: a run whose events are pushed from
another thread must be readable, in order, to completion.
"""
from __future__ import annotations

import threading
import time

from voicebridge.workbench_ui import chat_stream as CS


def _drain(frames, limit: float = 5.0):
    """Collect frames with a wall-clock bound so a deadlock fails, not hangs."""
    out = []
    deadline = time.monotonic() + limit

    def collect():
        for frame in frames:
            out.append(frame)

    t = threading.Thread(target=collect, daemon=True)
    t.start()
    t.join(max(0.0, deadline - time.monotonic()))
    assert not t.is_alive(), (
        "chat_stream_frames deadlocked: no frame arrived within "
        f"{limit}s — the reader is holding the run lock against itself")
    return out


def test_a_pushed_turn_streams_to_done():
    """A run pushed from another thread is readable end to end."""
    run = CS._new_run("deadlock-regression")
    run.push({"event": "token", "text": "hello"})
    run.push({"event": "done", "reply": "hello"})

    frames = _drain(CS.chat_stream_frames(run.stream_id))

    joined = "".join(frames)
    assert "event: token" in joined
    assert "event: done" in joined
    # The reply the client renders must be present, not just the event name.
    assert "hello" in joined


def test_the_reader_follows_a_run_that_is_still_pumping():
    """Events pushed AFTER the reader attaches still arrive.

    This is the case the deadlock broke: the reader parks waiting for the pump,
    and the pump can only notify if the reader is not holding the same lock.
    """
    run = CS._new_run("deadlock-follow")

    def pump():
        time.sleep(0.2)
        run.push({"event": "token", "text": "late"})
        time.sleep(0.2)
        run.push({"event": "done", "reply": "late"})

    threading.Thread(target=pump, daemon=True).start()

    frames = _drain(CS.chat_stream_frames(run.stream_id))
    joined = "".join(frames)
    assert "late" in joined
    assert "event: done" in joined


def test_snapshot_is_not_called_while_holding_the_condition():
    """A snapshot taken inside the run lock must not be reachable from the reader.

    Guards the exact shape of the bug: if the follow loop regains a nested
    ``snapshot()`` call, a turn with a pending event would wedge.
    """
    run = CS._new_run("deadlock-nested")
    # One event is already waiting, so the reader must yield it without ever
    # entering the wait branch that used to deadlock.
    run.push({"event": "token", "text": "ready"})
    run.push({"event": "done", "reply": "ready"})

    frames = _drain(CS.chat_stream_frames(run.stream_id), limit=3.0)
    assert any("ready" in f for f in frames)


def test_an_unknown_stream_is_a_typed_error_not_a_hang():
    frames = _drain(CS.chat_stream_frames("no-such-stream"), limit=3.0)
    joined = "".join(frames)
    assert "STREAM_NOT_FOUND" in joined
