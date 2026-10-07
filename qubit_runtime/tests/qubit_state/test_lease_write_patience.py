"""A turn-lease maintenance write rides out a sibling's long lock hold.

Defect: ``try_acquire_session_turn_lease`` / ``refresh_session_turn_lease`` /
``release_session_turn_lease`` were issued with the ROUTINE write patience
(20s), while a concurrent Qubit process legitimately held the shared state.db
write lock for 20-60s (multiple long-lived TUI gateways + concurrent writers,
the live "state.db write lock unavailable" storm). The refresher then raised
``Failed to refresh session turn lease`` and hard-interrupted a healthy turn,
and the release leaked the lease row until its TTL.

The lease row cannot be reclaimed by another writer before it expires, so a
lease-maintenance write may safely wait much longer than a routine write. A
turn must still be exclusive — this only widens the bounded wait before the
lease is declared at risk, it does not weaken the lock.
"""

import sqlite3
import threading
import time

from qubit_state import SessionDB


def _hold_write_lock(db_path, hold_s, started_evt):
    """Hold the SQLite write lock on *db_path* for *hold_s* seconds."""
    conn = sqlite3.connect(str(db_path), timeout=1.0, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        started_evt.set()
        time.sleep(hold_s)
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_turn_lease_patience_exceeds_routine_and_stays_within_the_ttl():
    """The lease budget must beat the routine budget but not outlive the TTL."""
    assert SessionDB._TURN_LEASE_WRITE_PATIENCE_S > SessionDB._WRITE_PATIENCE_S
    # LEASE_TTL_SECONDS (agent.turn_facade_lease) is 300s: waiting less than
    # that before declaring the lease at risk can never let a reclaim race in.
    assert SessionDB._TURN_LEASE_WRITE_PATIENCE_S <= 300.0


def test_refresh_survives_a_hold_longer_than_the_routine_budget(
    tmp_path, monkeypatch
):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s", "cli")
        holder = "pid=424242:turn=refresh"
        assert db.try_acquire_session_turn_lease("s", holder, ttl_seconds=300)
        monkeypatch.setattr(SessionDB, "_WRITE_PATIENCE_S", 0.2)
        started = threading.Event()
        thread = threading.Thread(
            target=_hold_write_lock, args=(db.db_path, 2.0, started))
        thread.start()
        try:
            assert started.wait(5.0)
            # Would raise OperationalError on the routine budget; must survive.
            assert db.refresh_session_turn_lease("s", holder) is True
        finally:
            thread.join(timeout=10.0)
        assert not thread.is_alive()
    finally:
        db.close()


def test_release_survives_the_hold_and_removes_the_row(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s", "cli")
        holder = "pid=424242:turn=release"
        assert db.try_acquire_session_turn_lease("s", holder, ttl_seconds=300)
        monkeypatch.setattr(SessionDB, "_WRITE_PATIENCE_S", 0.2)
        started = threading.Event()
        thread = threading.Thread(
            target=_hold_write_lock, args=(db.db_path, 2.0, started))
        thread.start()
        try:
            assert started.wait(5.0)
            db.release_session_turn_lease("s", holder)
        finally:
            thread.join(timeout=10.0)
        assert not thread.is_alive()
        # A different holder can acquire immediately: the row was really deleted.
        assert db.try_acquire_session_turn_lease(
            "s", "pid=525252:turn=next", ttl_seconds=300)
    finally:
        db.close()


def test_acquire_survives_a_hold_longer_than_the_routine_budget(
    tmp_path, monkeypatch
):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s", "cli")
        monkeypatch.setattr(SessionDB, "_WRITE_PATIENCE_S", 0.2)
        started = threading.Event()
        thread = threading.Thread(
            target=_hold_write_lock, args=(db.db_path, 2.0, started))
        thread.start()
        try:
            assert started.wait(5.0)
            assert db.try_acquire_session_turn_lease(
                "s", "pid=424242:turn=acquire", ttl_seconds=300) is True
        finally:
            thread.join(timeout=10.0)
        assert not thread.is_alive()
    finally:
        db.close()
