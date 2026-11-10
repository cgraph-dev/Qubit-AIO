"""Regression: concurrent office migrations must not race the view rebuild.

The live office opens short-lived connections per read and calls
``store.migrate`` on each one. A store whose ``office_meta.schema`` row is
missing (or stale) used to make every caller rebuild the reporting views;
two callers could interleave ``DROP``/``CREATE`` and the loser answered
"view office_v_agent_pnl already exists", surfacing as an intermittent
``OFFICE_TOWER_UNAVAILABLE`` 503 in the tower endpoint.
"""

from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

from voicebridge.office import store


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    return SimpleNamespace(state_dir=lambda: tmp_path)


def _migrate_once(cfg, errors):
    try:
        conn = store.connect(cfg)
        try:
            store.migrate(conn)
        finally:
            conn.close()
    except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
        errors.append(exc)


def _schema_row(path):
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT value FROM office_meta WHERE key='schema'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_concurrent_migrate_with_missing_schema_row_is_clean(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
    finally:
        conn.close()

    # Reproduce the live state: views exist, the schema row does not.
    conn = sqlite3.connect(str(store.db_path(cfg)))
    try:
        conn.execute("DELETE FROM office_meta WHERE key='schema'")
        conn.commit()
    finally:
        conn.close()
    assert _schema_row(store.db_path(cfg)) is None

    errors: list[BaseException] = []
    threads = [
        threading.Thread(target=_migrate_once, args=(cfg, errors))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert _schema_row(store.db_path(cfg)) == store.SCHEMA
    conn = sqlite3.connect(str(store.db_path(cfg)))
    try:
        views = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'")
        }
    finally:
        conn.close()
    assert {
        "office_v_agent_pnl",
        "office_v_business_pnl",
        "office_v_department_pnl",
        "office_v_org_pnl",
    } <= views


def test_migrate_is_idempotent_and_does_not_leave_a_transaction_open(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    conn = store.connect(cfg)
    try:
        for _ in range(5):
            store.migrate(conn)
            assert conn.in_transaction is False
        assert _schema_row(store.db_path(cfg)) == store.SCHEMA
    finally:
        conn.close()
