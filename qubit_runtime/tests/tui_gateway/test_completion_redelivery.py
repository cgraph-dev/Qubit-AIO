"""A plain ``completion`` event must not be dispatched as a user turn twice.

Regression for the 89x replay of one process-completion batch into a single
session (2026-10-17). Two independent defects let the same envelope become a
fresh user message on every drain of ``completion_queue``:

1. ``_notif_dispatch_completions`` ran every event through
   ``claim_event_delivery``, but that helper returns ``""`` for a plain
   ``completion`` (only ``async_delegation`` is durable). The empty token was
   appended as if it were a claim, so ``complete_event_delivery`` no-oped and
   nothing was ever marked delivered — the event stayed re-drainable.
2. Neither the dispatch path nor the dedup key gave a plain ``completion`` a
   stable identity: ``_notification_event_dedup_key`` had no ``completion``
   branch, so events with an empty ``session_id`` all collapsed onto
   ``("", "completion")`` — the status row was suppressed but the turn
   dispatch was not.

Asserted at the behavior level: the batch renders once, and draining the same
envelope again submits no further turn.
"""

from __future__ import annotations

import threading

import pytest

from tui_gateway import server


class _Registry:
    """Minimal completion_queue stand-in."""

    def __init__(self):
        self.queue: list = []
        self.completion_queue = self
        self.consumed: set = set()

    def mark_completion_consumed(self, session_id):
        self.consumed.add(session_id)

    def put(self, evt):
        self.queue.append(evt)

    def get_nowait(self):
        if not self.queue:
            raise IndexError("empty")
        return self.queue.pop(0)

    def empty(self):
        return not self.queue


def _completion(proc_id: str, sid: str = "") -> dict:
    return {
        "type": "completion",
        "session_id": sid,
        "session_key": "",
        "task_id": proc_id,
        "exit_code": 0,
        "label": proc_id,
    }


def _session() -> dict:
    return {"history_lock": threading.RLock(), "running": False, "history": []}


def _record_submits(monkeypatch) -> list:
    started: list = []

    def fake_submit(rid, sid, session, text, what, **kwargs):
        started.append(text)
        # Production releases the claimed turn once the batch is dispatched.
        from tui_gateway import session_notifications as sn

        sn._notif_release_turn(session)
        return True

    monkeypatch.setattr(server, "_notif_submit", fake_submit)
    monkeypatch.setattr(server, "_emit", lambda *a, **k: None)
    return started


@pytest.fixture
def fixed_batch(monkeypatch):
    """Render every completion batch to one stable text (dispatch, not formatting)."""
    from tools import process_registry_notifications as prn

    class _Batch:
        def __init__(self, events):
            self.events = tuple(events)

        def render(self, registry):
            return "7 background processes completed."

        def display_text(self, registry):
            return "7 background processes completed."

    monkeypatch.setattr(prn, "ProcessNotificationBatch", _Batch)


def _dispatch(session, registry, events):
    server._notif_dispatch_completions("sid", session, [(evt, "batch text") for evt in events], registry, None)


def test_a_plain_completion_is_delivered_once(monkeypatch, fixed_batch):
    started = _record_submits(monkeypatch)
    session, registry = _session(), _Registry()
    evt = _completion("proc_64dd4f6f1cf1")

    _dispatch(session, registry, [evt])
    assert len(started) == 1, "the first drain delivers the batch"
    assert registry.consumed == {"proc_64dd4f6f1cf1"}, (
        "the registry must be told the completion was delivered, or it keeps offering it"
    )

    for _ in range(3):
        _dispatch(session, registry, [evt])
    assert len(started) == 1, "a completion already delivered must never dispatch again"


def test_a_completion_dedup_key_ignores_output_and_exit_code():
    """Identity is the process session, not its output: a replay must key as the original.

    Pinned by test_notification_event_dedup_key_keeps_completions_one_shot; restated here
    so the redelivery fix cannot drift into fracturing a completion's identity.
    """
    first = {**_completion("proc_aaa"), "command": "make build", "exit_code": 0, "output": "one"}
    replay = {**_completion("proc_aaa"), "command": "make build --again", "exit_code": 1, "output": "two"}
    assert server._notification_event_dedup_key(first) == server._notification_event_dedup_key(replay)


def test_a_completion_dedup_key_is_stable_across_drains():
    evt = _completion("proc_aaa")
    assert server._notification_event_dedup_key(evt) == server._notification_event_dedup_key(dict(evt))


def test_failure_logging_does_not_raise(capsys):
    """The failure logger must not itself raise: a swallowed NameError here turns a handled
    dispatch failure into an unhandled one, which is how a storm hides its own cause."""
    from tui_gateway import session_notifications as sn

    sn._notif_log_failure("probe", ValueError("boom"))

    err = capsys.readouterr().err
    assert "probe" in err and "ValueError: boom" in err

