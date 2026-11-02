"""Watch-notice refusal backoff: the retry step/delay curve and the permanent-vs-transient cap.

Companion to ``test_watch_notification_reset_pin.py`` (which drives the whole drain against real
sessions): these pin the two helpers directly so the retry cadence and the drop rule cannot drift.
"""

import queue

import pytest

from tests.module_clock import install_module_clock


def _notifications(monkeypatch):
    from gateway import run_notifications
    clock = install_module_clock(monkeypatch, run_notifications)
    return run_notifications, clock


@pytest.mark.parametrize(
    "step, expected",
    [(1, 4.0), (2, 8.0), (3, 16.0), (4, 32.0), (5, 60.0), (9, 60.0)],
)
def test_refusal_retry_at_doubles_from_the_drain_base_then_caps(monkeypatch, step, expected):
    notifications, clock = _notifications(monkeypatch)
    assert notifications._refusal_retry_at(step) == clock.now + expected


def test_permanent_refusal_counts_and_pops_the_verdict(monkeypatch):
    notifications, clock = _notifications(monkeypatch)
    pending: queue.Queue = queue.Queue()
    evt = {"_refusal_permanent": True}

    notifications.GatewayNotificationsMixin._requeue_refused_watch_event(pending, evt)

    assert evt["_watch_retry_step"] == 1
    assert evt["_watch_injection_attempts"] == 1
    assert "_refusal_permanent" not in evt  # re-stamped on the next injection attempt
    assert evt["_watch_next_retry_at"] == clock.now + 4.0
    assert pending.get_nowait() is evt


def test_permanent_refusal_drops_at_the_cap(monkeypatch):
    notifications, clock = _notifications(monkeypatch)
    pending: queue.Queue = queue.Queue()
    evt = {
        "_refusal_permanent": True,
        "_watch_injection_attempts": notifications._MAX_COMPLETION_INJECTION_ATTEMPTS - 1,
    }

    notifications.GatewayNotificationsMixin._requeue_refused_watch_event(pending, evt)

    assert evt["_watch_injection_attempts"] == notifications._MAX_COMPLETION_INJECTION_ATTEMPTS
    assert pending.empty(), "a proven-permanent refusal is dropped once the budget is spent"
    assert "_watch_next_retry_at" not in evt


def test_transient_refusal_never_spends_the_budget(monkeypatch):
    notifications, clock = _notifications(monkeypatch)
    pending: queue.Queue = queue.Queue()
    evt = {"_refusal_permanent": False}

    delays = []
    for _ in range(12):
        notifications.GatewayNotificationsMixin._requeue_refused_watch_event(pending, evt)
        delays.append(evt["_watch_next_retry_at"] - clock.now)
        clock.now += 1

    assert "_watch_injection_attempts" not in evt
    assert evt["_watch_retry_step"] == 12
    assert delays[:6] == [4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert pending.qsize() == 12, "a transient refusal is retried, never dropped"


def test_async_group_backoff_steps_each_event(monkeypatch):
    notifications, clock = _notifications(monkeypatch)
    group = [{}, {}]

    notifications.GatewayNotificationsMixin._back_off_refused_completions(group)

    for evt in group:
        assert evt["_watch_retry_step"] == 1
        assert evt["_watch_next_retry_at"] == clock.now + 4.0
