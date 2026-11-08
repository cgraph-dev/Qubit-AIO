"""A corrupt FTS vtable must not refuse the state store's open.

SQLite 3.45 (the CPython 3.12 hosted-runner build, running Hermes' DELETE arm) reports a
corrupt ``messages_fts`` as a bare ``DatabaseError: vtable constructor failed:
messages_fts`` from even a ``LIMIT 0`` probe. That is FTS-layer damage; the canonical
``messages`` rows are intact, so the open has to fail open by dropping and rebuilding the
index instead of raising out of ``SessionDB.__init__``.
"""

from __future__ import annotations

import sqlite3

from qubit_state import SessionDB


def test_corrupt_fts_vtable_at_open_is_rebuilt_not_raised(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    real_probe = SessionDB._fts_table_probe
    state = {"raised": False}

    def probe(self, cursor, table_name):
        if table_name == "messages_fts" and not state["raised"]:
            state["raised"] = True
            raise sqlite3.DatabaseError("vtable constructor failed: messages_fts")
        return real_probe(self, cursor, table_name)

    monkeypatch.setattr(SessionDB, "_fts_table_probe", probe)

    db = SessionDB(db_path=db_path)
    try:
        assert state["raised"], "the corrupt-vtable path was never exercised"
        assert db._fts_enabled, "FTS stayed detached after the fail-open rebuild"
    finally:
        db.close()

    # A second open reads the rebuilt index cleanly, so recovery is durable rather than
    # a one-open special case.
    again = SessionDB(db_path=db_path)
    try:
        assert again._fts_enabled
    finally:
        again.close()
