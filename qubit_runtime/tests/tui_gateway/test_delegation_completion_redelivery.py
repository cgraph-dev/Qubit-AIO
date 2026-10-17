"""A delivered async-delegation completion must be acknowledged, never re-queued.

Regression for the ~18x replay of one batch-complete envelope (``deleg_2e949637``,
2026-10-17) into a single TUI session. The durable delivery ledger has a full
lifecycle (``pending`` -> claimed -> ``delivered``); a delivered row is never
replayed again. But ``_notif_dispatch_event`` branches on ``_notif_submit``'s
return value:

    if not _notif_submit(...):
        _notif_defer_event(evt, claim)   # defer (attempts refunded) + RE-QUEUE
        return
    complete_event_delivery(evt, claim)  # <- never reached

``_notif_submit`` was annotated ``-> None`` and had no ``return``, so
``not None`` was ALWAYS true: even when the turn was admitted and ran (proven by
``tui prompt accepted: ... kind=async_delegation_complete`` in agent.log paired
with a matching ``tui turn finished``), the code deferred the durable claim and
put the event back on the queue. ``defer_completion_delivery`` refunds the
attempt (``MAX(0, attempts-1)``), so the row oscillated 1 -> 0 forever, never hit
the 8-attempt cap, stayed ``pending`` (replayed on restart too) and re-emitted as
a fresh turn every drain.

The fix makes ``_notif_submit`` return the real ``_run_prompt_submit`` admission
verdict. These tests exercise the REAL ``_notif_submit`` against a stubbed
``_run_prompt_submit`` so the None-vs-bool contract cannot regress silently.
"""

from __future__ import annotations

import contextlib
import threading

import pytest

from tui_gateway import server

# ``_notif_dispatch_event`` is published onto ``server`` rebound to the server's own
# globals (``bind_module``), and it imports the durable-ledger ops from
# ``tools.async_delegation`` INSIDE the function body. So module globals (``_notif_submit``,
# ``_notif_defer_event``) are patched on ``server``, the ledger ops on ``tools.async_delegation``.


class _Registry:
    """Minimal completion_queue stand-in; records what is (re)queued."""

    def __init__(self):
        self.queue: list = []
        self.completion_queue = self

    def put(self, evt):
        self.queue.append(evt)

    def get_nowait(self):
        if not self.queue:
            raise IndexError("empty")
        return self.queue.pop(0)

    def empty(self):
        return not self.queue


def _delegation(deleg_id: str = "deleg_2e949637") -> dict:
    return {
        "type": "async_delegation",
        "delegation_id": deleg_id,
        "session_key": "",
        "origin_ui_session_id": "",
        "goal": "do a thing",
        "is_batch": True,
        "results": [{"task_index": 0, "status": "completed", "summary": "done"}],
        "status": "completed",
    }


def _session() -> dict:
    return {"history_lock": threading.RLock(), "running": False, "history": []}


@pytest.fixture
def _isolate(monkeypatch):
    """Neutralize UI emission and profile scoping; nothing here is under test."""
    monkeypatch.setattr(server, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(server, "_session_profile_runtime_scope", lambda session: contextlib.nullcontext())
    monkeypatch.setattr(server, "_async_delegation_display_metadata", lambda evt: {})


@pytest.fixture
def _ledger(monkeypatch):
    """Stub the durable-ledger ops at their import origin (``tools.async_delegation``)."""
    from tools import async_delegation as ad

    calls: list = []
    monkeypatch.setattr(ad, "claim_event_delivery", lambda evt, consumer: "claim-1")
    monkeypatch.setattr(ad, "complete_event_delivery", lambda evt, claim: calls.append(("complete", claim)))
    monkeypatch.setattr(ad, "release_event_delivery", lambda evt, claim: calls.append(("release", claim)))
    monkeypatch.setattr(ad, "defer_completion_delivery", lambda deleg_id, claim: calls.append(("defer", claim)))
    return calls


def test_notif_submit_reports_the_turn_admission_verdict(monkeypatch, _isolate):
    """``_notif_submit`` must return ``_run_prompt_submit``'s bool, not None.

    The bare contract: an admitted turn (True) and a refused turn (False) must be
    distinguishable by the caller. ``None`` is the bug — ``if not None`` reads as
    "refused" and sends a delivered event down the defer/requeue path.

    ``bind_module`` publishes a REBOUND copy onto ``server`` whose globals are
    ``vars(server)`` (the dispatched function, the one production calls); patching
    ``server._run_prompt_submit`` is therefore visible to it.
    """
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *a, **k: True)
    assert server._notif_submit("rid", "sid", _session(), "text", "what") is True

    monkeypatch.setattr(server, "_run_prompt_submit", lambda *a, **k: False)
    assert server._notif_submit("rid", "sid", _session(), "text", "what") is False


def test_admitted_delegation_is_acknowledged_not_re_queued(monkeypatch, _isolate, _ledger):
    """The end-to-end defect: an admitted turn must ack the row, not defer+requeue.

    Before the fix, ``claim_event_delivery`` succeeds and the turn runs, yet the
    dispatch calls ``defer_completion_delivery`` and re-queues the envelope — the
    exact ~18x replay. After the fix it calls ``complete_event_delivery`` and the
    event is never put back.
    """
    session = _session()
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *a, **k: True)  # turn admitted + runs

    requeued: list = []
    monkeypatch.setattr(server, "_notif_defer_event",
                        lambda evt, claim, put=None: (requeued.append(evt), _ledger.append(("defer-path", claim))))

    server._notif_dispatch_event("sid", session, _delegation(), "batch complete text")

    assert ("complete", "claim-1") in _ledger, "an admitted turn must acknowledge the durable row"
    assert not any(kind == "defer" for kind, _ in _ledger), "an admitted turn must NOT defer its row"
    assert requeued == [], "an admitted turn must NOT re-queue the event"


def test_refused_delegation_is_deferred_without_acknowledging(monkeypatch, _isolate, _ledger):
    """The other side of the contract: a turn that never started must defer, not ack.

    A refused admission (session closed / closing) delivered nothing, so the row must
    stay pending for a later drain — acking it would lose the result. This is why the
    fix returns a real verdict rather than hard-coding True.
    """
    session = _session()
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *a, **k: False)  # turn refused

    deferred: list = []
    monkeypatch.setattr(server, "_notif_defer_event",
                        lambda evt, claim, put=None: deferred.append(evt))

    server._notif_dispatch_event("sid", session, _delegation("deleg_refused"), "text")

    assert deferred and deferred[0]["delegation_id"] == "deleg_refused", "a refused turn must defer the event"
    assert not any(kind in ("complete", "release") for kind, _ in _ledger), \
        "a refused turn must neither ack nor release the row — defer refunds the claim"
