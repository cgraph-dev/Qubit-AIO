"""Schema v5 is additive: v1-v4 data survives and the new tables appear.

Hermetic: throwaway SQLite under ``tmp_path``; no network, no live service.
"""
from __future__ import annotations

import sqlite3

from voicebridge.business import BusinessStore
from voicebridge.business.schema import MIGRATIONS, SCHEMA_VERSION


def _build_through(conn: sqlite3.Connection, version: int) -> None:
    for item, statements in MIGRATIONS:
        if item > version:
            continue
        for statement in statements:
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version={item}")


def test_v4_database_migrates_to_v5_and_keeps_rows(tmp_path):
    path = tmp_path / "legacy-v4.db"
    conn = sqlite3.connect(path)
    try:
        _build_through(conn, 4)
        conn.execute(
            "INSERT INTO bos_tenant(tenant_id, name, created_at, metadata_json)"
            " VALUES('ten_legacy','Legacy','2026-01-01T00:00:00.000Z','{}')"
        )
        conn.execute(
            "INSERT INTO bos_business(business_id, tenant_id, name, slug, status,"
            " currency, created_at, updated_at, metadata_json)"
            " VALUES('biz_legacy','ten_legacy','Legacy','legacy','active',"
            " 'USD','2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z','{}')"
        )
        conn.commit()
    finally:
        conn.close()

    store = BusinessStore(path)
    assert SCHEMA_VERSION == 5
    with store.read() as read_conn:
        assert read_conn.execute("PRAGMA user_version").fetchone()[0] == 5
        tables = {
            row[0]
            for row in read_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"bos_session", "bos_handoff"} <= tables
        agent_columns = {
            row[1] for row in read_conn.execute("PRAGMA table_info(bos_agent)")
        }
        assert "security_principal_id" in agent_columns
        assert [
            row[0] for row in read_conn.execute("SELECT name FROM bos_tenant")
        ] == ["Legacy"]
        assert read_conn.execute(
            "SELECT COUNT(*) FROM bos_business"
        ).fetchone()[0] == 1
        indexes = {
            row[0]
            for row in read_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {
            "bos_session_listing",
            "bos_session_agent",
            "bos_session_owner",
            "bos_handoff_listing",
            "bos_handoff_session",
            "bos_handoff_task",
            "bos_handoff_one_proposed",
        } <= indexes


def test_migration_is_idempotent(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    with store.read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    again = BusinessStore(tmp_path / "business.db")
    with again.read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
