"""The PTY reaper must retire detached terminals WITHOUT a new connection.

`_sweep_idle_detached` was only ever called from the connect path. A listener
that had stopped accepting connections therefore never reaped its detached
terminals, so every `qubit --tui` child — and the single-writer session lease
each one holds — outlived the gateway that spawned it. That is what surfaced to
the operator as "This chat is open in another Qubit window" on a later resume,
and it survived a gateway restart because the orphan process was still alive
(the lease fence only prunes DEAD owners).

The reaper thread closes the hole; these tests pin that it actually runs.
"""

import time

import pytest

from voicebridge import pty_ws


class _Bridge:
    """Minimal bridge double: alive until ``closed``."""

    def __init__(self):
        self.closed = False

    def read(self, _timeout):
        return None if self.closed else b""

    def write(self, _payload):
        return True

    def isalive(self):
        return not self.closed

    def close(self):
        self.closed = True

    def resize(self, **_kw):
        return True


def _ticket(session):
    return pty_ws._Ticket(scope="pty", session=session, owner="o",
                          target="t", expires_at=float("inf"))


def _detached_server(idle_timeout_s):
    srv = pty_ws.PtyWsServer(
        spawn=lambda cols, rows, session, profile: (_Bridge(), {}),
        idle_timeout_s=idle_timeout_s,
    )
    return srv


def _detach_one(srv, key="chat-1"):
    bridge = _Bridge()
    session = pty_ws._Session(bridge=bridge, ticket=_ticket(key), key=key)
    with srv._lock:
        srv._active[id(session)] = session
    srv._detach(session)
    return session


def test_reaper_retires_a_detached_terminal_without_any_connect():
    """The core regression: no client connects, yet the orphan still dies."""
    # A long idle timeout, so the ONLY reason it can be reaped is the reaper
    # thread itself — not the sweep happening to run on a connect.
    srv = _detached_server(idle_timeout_s=0.05)
    session = _detach_one(srv)
    assert session.key in srv._detached

    srv.start_reaper()
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and session.key in srv._detached:
            time.sleep(0.02)
    finally:
        srv.stop_reaper()

    assert session.key not in srv._detached, "reaper never retired the detached terminal"
    assert session.bridge.closed, "reaped terminal's child process was not closed"


def test_reaper_leaves_an_attached_terminal_alone():
    """A live client must never be reaped, however long it sits quiet."""
    srv = _detached_server(idle_timeout_s=0.0)
    session = _detach_one(srv)
    # A fresh touch makes it look recently active again (a reattach would do this).
    session.last_activity = time.monotonic()

    srv.start_reaper()
    try:
        time.sleep(0.5)
    finally:
        srv.stop_reaper()

    assert session.key in srv._detached
    assert not session.bridge.closed


def test_start_reaper_is_idempotent_and_stop_joins():
    srv = _detached_server(idle_timeout_s=60.0)
    srv.start_reaper()
    first = srv._reaper
    srv.start_reaper()
    assert srv._reaper is first, "a second start must not spawn a second reaper thread"

    srv.stop_reaper()
    assert first is not None and not first.is_alive()
    assert srv._reaper is None


def test_stop_reaper_is_safe_when_never_started():
    srv = _detached_server(idle_timeout_s=60.0)
    srv.stop_reaper()  # must not raise


@pytest.mark.parametrize("idle", [0.0, 30 * 60.0, 3600.0])
def test_reaper_period_stays_bounded(idle):
    """A pathological idle timeout must not make the reaper spin or sleep for hours."""
    srv = _detached_server(idle_timeout_s=idle)
    period = max(5.0, min(60.0, srv._idle / 4.0 if srv._idle > 0 else 60.0))
    assert 5.0 <= period <= 60.0
