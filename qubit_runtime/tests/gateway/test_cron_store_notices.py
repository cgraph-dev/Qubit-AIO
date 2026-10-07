"""The gateway's home-channel notices for cron store degraded-state transitions.

``gateway/cron_store_notices.py`` routes ``cron/store_health.py`` transitions from the ticker
thread onto the gateway loop: ONE "unwritable" notice on entry (naming the store, the error and
since-when) to the served profile that owns the store, and a deferred "recovered" once the repeat
window elapses. "recovered" is dropped when the store degrades again meanwhile, so the last notice
a home channel sees always matches the store's real state, and a store already degraded at boot is
announced when the adapters come up.

The loop clock is manual: the real one-hour window only elapses when the test says so, so every
case is decided by event order, never by wall-clock timing.
"""

from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from cron import store_health


@pytest.fixture()
def cron_store(tmp_path, monkeypatch):
    """Pin the store-health module state and the routing home; start with no degraded store."""
    monkeypatch.setattr(store_health, "_degraded", {})
    monkeypatch.setattr(store_health, "_listener", None)
    monkeypatch.setattr(store_health, "_recovered", {})
    return tmp_path


def test_announces_once_and_defers_recovery(cron_store, monkeypatch):
    """One "unwritable" on entry; "recovered" waits out the repeat window and is dropped if the
    store fails again meanwhile; a store already degraded at boot is announced on install."""
    from gateway import cron_store_notices
    from gateway.cron_store_notices import install_cron_store_notices

    sent = []
    gate = asyncio.Event()
    gate.set()

    async def send(platform, home, transport, message, failure_fmt):
        await gate.wait()  # cleared below to hold a delivery open while the store flips
        sent.append((home.chat_id, message))
        return True

    home = SimpleNamespace(chat_id="c1", thread_id=None)
    profile = cron_store / "profile"  # its cron/ is a symlink to the store on another disk
    profile.mkdir()
    cron_dir = cron_store / "cron"
    cron_dir.mkdir()
    (profile / "cron").symlink_to(cron_dir, target_is_directory=True)
    runner = SimpleNamespace(_send_home_channel_message=send, _served_profile_homes={"p": profile},
                             _served_home_channel_transports=lambda: iter([("p", "telegram", None, home, object())]))
    monkeypatch.setattr("qubit_constants.get_routing_process_qubit_home", lambda: cron_store / "launch")

    loop = asyncio.new_event_loop()
    clock = [loop.time()]
    loop.time = lambda: clock[0]

    async def drain():  # until no due timer and no notice send is left
        while len(asyncio.all_tasks()) > 1 or any(
                not h.cancelled() and h.when() <= clock[0] for h in loop._scheduled):
            await asyncio.sleep(0)

    def settle():  # process queued transitions, let the window elapse, deliver what is due
        loop.run_until_complete(drain())
        clock[0] += cron_store_notices.NOTICE_REPEAT_SECONDS
        loop.run_until_complete(drain())

    def spin():
        for _ in range(5):
            loop.run_until_complete(asyncio.sleep(0))

    enospc = OSError(errno.ENOSPC, "No space left on device")
    try:
        install_cron_store_notices(runner, loop)
        # Boot replay: a store already degraded when the notice installer runs is announced.
        store_health.note_unwritable(enospc, "x", "scan", cron_dir=cron_dir)
        settle()
        assert [m[0] for _, m in sent] == ["⚠"]  # one outage, one entry notice
        assert str(cron_dir.resolve()) in sent[0][1] and "ENOSPC: No space left on device" in sent[0][1]
        assert "since " in sent[0][1]

        # A second failed site within the same outage does not re-announce.
        store_health.note_unwritable(enospc, "x", "advance", cron_dir=cron_dir)
        settle()
        assert [m[0] for _, m in sent] == ["⚠"]

        # Recovery is deferred: the "recovered" notice lands only after the repeat window.
        store_health.note_writable(cron_dir)
        settle()  # schedules the deferred recovered
        settle()  # window elapsed: delivers it
        assert [m[0] for _, m in sent] == ["⚠", "✅"]

        # A fresh outage announces once more; a flap back inside the window cancels the pending
        # "recovered", so no lone recovery notice is ever the last word.
        store_health.note_unwritable(enospc, "x", "scan", cron_dir=cron_dir)
        settle()
        assert [m[0] for _, m in sent] == ["⚠", "✅", "⚠"]
        store_health.note_writable(cron_dir)
        spin()  # the recovered callback is queued but not yet delivered
        store_health.note_unwritable(enospc, "x", "scan", cron_dir=cron_dir)  # fails again
        settle()
        assert [m[0] for _, m in sent][-1] == "⚠"  # still down: the outage notice stands
    finally:
        loop.close()

    # Exactly three notices: entry, deferred recovery, re-entry. The flap back inside the window
    # cancels the pending "recovered" and the re-entry announces no duplicate.
    assert [m[0] for _, m in sent] == ["⚠", "✅", "⚠"]
    assert {chat for chat, _ in sent} == {"c1"}


def test_send_failure_is_logged_not_lost(tmp_path, monkeypatch):
    """A send that raises is logged, never propagated into the ticker thread."""
    from gateway.cron_store_notices import _log_notice_failure

    class _BadFuture:
        def result(self):
            raise RuntimeError("transport down")

    _log_notice_failure(_BadFuture(), "unwritable", "/no/such/store")  # must not raise
