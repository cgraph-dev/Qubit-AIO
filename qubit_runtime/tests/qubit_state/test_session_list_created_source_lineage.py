"""Local-created source filtering and reset-lineage projection in session listings."""

import json

import pytest

from qubit_state import SessionDB
from qubit_state_local import POLICY_PREFIX


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


def _local(db, session_id, created_source):
    db.create_session(session_id, "local")
    db._execute_write(lambda conn: conn.execute(
        "UPDATE sessions SET created_source=? WHERE id=?", (created_source, session_id)))


def test_local_created_sources_restrict_listing_and_search(db):
    _local(db, "cli-local", "cli")
    _local(db, "kanban-local", "kanban")
    db.create_session("cli-native", "cli")

    search = db.search_sessions(source="cli", local_created_sources=["cli"], limit=50)
    assert {row["id"] for row in search} == {"cli-local", "cli-native"}

    listed = db.list_sessions_rich(sources=["cli"], local_created_sources=["cli"], limit=50)
    assert {row["id"] for row in listed} == {"cli-local", "cli-native"}


def test_surface_label_reader_accepts_local_created_sources(db):
    from qubit_cli.gateway_session_store import SurfaceLabelReader

    _local(db, "cli-local", "cli")
    reader = SurfaceLabelReader(db)

    assert {row["id"] for row in reader.list_sessions_rich(source="cli", limit=50)} == {"cli-local"}
    assert {row["id"] for row in reader.search_sessions(source="cli", limit=50)} == {"cli-local"}


def test_reset_lineage_lists_once_and_carries_lineage_ids(db):
    db.create_session("root", "cli")
    db.create_session("tip", "cli", parent_session_id="root")

    def write(conn):
        conn.execute("UPDATE sessions SET end_reason='session_reset' WHERE id='root'")
        conn.execute("UPDATE sessions SET model_config=? WHERE id='tip'",
                     (json.dumps({"_reset_from": "root"}),))
        conn.execute("INSERT INTO state_meta(key,value) VALUES(?,?)",
                     (POLICY_PREFIX + "root", json.dumps({"session_id": "root", "lineage": ["root", "tip"]})))
    db._execute_write(write)

    listed = db.list_sessions_rich(limit=50)
    ids = {row["id"] for row in listed}
    assert "root" not in ids
    tip = next(row for row in listed if row["id"] == "tip")
    assert tip["_lineage_ids"] == ["root", "tip"]
