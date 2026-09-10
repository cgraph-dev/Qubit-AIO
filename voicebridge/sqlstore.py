"""Qubit SQLite helpers for the small local stores.

Idempotent DDL and explicit write transactions shared by the memory,
schedule, board, and journal stores. Standard-library only.
"""
from __future__ import annotations

import contextlib
import sqlite3
from typing import Iterator


def add_column_if_missing(conn: sqlite3.Connection, table: str,
                          column: str, ddl: str) -> bool:
    """``ALTER TABLE <table> ADD COLUMN <ddl>``; True when added, False
    when a concurrent migrator already added it. Anything else raises."""
    _check_identifier(table)
    _check_identifier(column)
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        return True
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return False
        raise


def _check_identifier(name: str) -> None:
    import re as _re

    if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise ValueError(f"bad SQL identifier: {name!r}")


@contextlib.contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """An IMMEDIATE write transaction with a guarded rollback: an
    auto-rollback underneath (EIO/contention) cannot shadow the error."""
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
