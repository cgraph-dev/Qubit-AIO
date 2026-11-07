"""`_gc()` must reap finished runs and abandoned ones — and nothing else.

The registry is process-global and one run per session, so a row that _gc()
forgets to drop makes ``live_run_for_session()`` answer forever: every later
``/chat/start`` for that session is refused ``409 SESSION_BUSY`` and the session
is locked out by one crashed turn. A row that _gc() drops too eagerly loses a
finished turn before the client can read it, so the pane renders a bare
"Processed" row with no text.

These tests pin both directions with explicit clock injection rather than sleep,
so they assert the retention RULE instead of the current constant.
"""
from __future__ import annotations

import time

from voicebridge.workbench_ui import chat_stream as CS


def _age_run(run, seconds: float) -> None:
    """Move a run's clocks into the past without sleeping."""
    run.started_at -= seconds
    if run.finished_at is not None:
        run.finished_at -= seconds


def _register(session_id: str):
    run = CS._new_run(session_id)
    return run


def test_a_finished_run_stays_readable_inside_the_retention_window():
    """A completed turn must remain replayable while its window is open."""
    run = _register("gc-retained")
    run.push({"event": "done", "reply": "kept"})
    _age_run(run, CS._RETAIN_S * 0.5)

    assert CS._get(run.stream_id) is not None, (
        "a run finished inside _RETAIN_S was reaped, so a client attaching "
        "after the turn would get STREAM_NOT_FOUND instead of the reply")


def test_a_finished_run_is_forgotten_past_the_retention_window():
    """Retention is bounded: an old finished run must not leak forever."""
    run = _register("gc-expired")
    run.push({"event": "done", "reply": "gone"})
    _age_run(run, CS._RETAIN_S + 5.0)

    assert CS._get(run.stream_id) is None


def test_retention_is_measured_from_the_finish_not_the_start():
    """A slow turn is not reaped the instant it completes.

    Measuring from started_at meant any turn slower than _RETAIN_S vanished
    the moment it finished, before the client could read the result. The pump's
    ``finally`` stamps finished_at on completion, so age the START only and
    leave the finish fresh: the run must stay readable.
    """
    run = _register("gc-slow-turn")
    run.started_at -= CS._RETAIN_S * 3      # the turn took a long time...
    run.push({"event": "done", "reply": "slow but finished"})
    run.finished_at = time.time()           # ...and finished just now.

    assert CS._get(run.stream_id) is not None, (
        "a slow turn was reaped immediately on completion: retention was "
        "measured from started_at instead of finished_at")


def test_an_orphaned_run_is_reaped_so_the_session_is_not_locked_out():
    """A pump thread that died without settling must not block the session."""
    import threading

    run = _register("gc-orphan")

    def die_without_settling():
        return  # returns without pushing a terminal event

    t = threading.Thread(target=die_without_settling)
    run.thread = t
    t.start()
    t.join()

    assert CS.live_run_for_session("gc-orphan") is None, (
        "the run of a dead pump still answers live_run_for_session(), so "
        "every later /chat/start for this session gets 409 SESSION_BUSY")


def test_a_run_whose_pump_never_started_still_expires_on_age():
    """The absolute backstop: a run with no thread cannot be reaped by liveness."""
    run = _register("gc-never-started")
    run.thread = None                     # the pump was never created
    _age_run(run, CS._HARD_MAX_S + 5.0)

    assert CS._get(run.stream_id) is None


def test_a_live_run_is_never_reaped():
    """The control: a healthy in-flight turn survives every branch of _gc()."""
    run = _register("gc-live")
    run.push({"event": "token", "text": "working"})
    # Fresh, not done, thread present and alive.
    import threading

    t = threading.Thread(target=lambda: time.sleep(0.4))
    run.thread = t
    t.start()
    try:
        assert CS._get(run.stream_id) is not None
    finally:
        t.join()
