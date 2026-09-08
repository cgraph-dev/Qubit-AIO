"""Vendored from NousResearch/hermes-agent @ 2237be355906fbe6065ce1815711eee52b2d646e
(tag v2026.9.7), source path hermes_cli/sqlite_util.py — VERBATIM, no local changes.

MIT License, Copyright (c) 2025 Nous Research. Full text in
voicebridge/upstream/ATTRIBUTION.md. Qubit uses write_txn for the memory
store migration transaction and add_column_if_missing for additive schema
evolution.

Original docstring follows.

Shared SQLite primitives for the small per-profile / board stores."""

from __future__ import annotations

import contextlib
import sqlite3


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """``ALTER TABLE <table> ADD COLUMN <ddl>``, idempotent across races: True when this call added
    it, False on the ``duplicate column name`` a concurrent migrator caused.

    ``column`` is the human-readable name for the call site; ``ddl`` carries the actual definition. See
    #21708.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        return True
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return False
        raise


@contextlib.contextmanager
def write_txn(conn: sqlite3.Connection):
    """An IMMEDIATE write transaction. The explicit ROLLBACK is guarded so a SQLite auto-rollback
    (no transaction left under EIO / contention / corruption) cannot shadow the original error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    else:
        conn.execute("COMMIT")
