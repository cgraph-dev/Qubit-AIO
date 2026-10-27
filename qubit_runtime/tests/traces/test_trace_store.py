from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from traces.errors import (
    TraceNotFoundError,
    TraceStoreCorruptError,
    TraceStoreVersionError,
)
from traces.settings import TraceSettings
from traces.store import CURRENT_SCHEMA_VERSION, TraceRow, TraceStore


def _settings(path, **kw) -> TraceSettings:
    base = dict(enabled=True, db_path=str(path), max_excerpt_chars=200)
    base.update(kw)
    return TraceSettings(**base)


def _row(trace_id: str, created_at: str | None = None, **kw) -> TraceRow:
    return TraceRow(id=trace_id, created_at=created_at or "2026-10-27T00:00:00.000Z", **kw)


def test_fresh_store_sets_schema_version_and_reopens_idempotently(tmp_path):
    path = tmp_path / "traces.db"
    with TraceStore(path) as store:
        assert store.count() == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    with TraceStore(path) as store:
        assert store.count() == 0


def test_migration_from_empty_v0(tmp_path):
    path = tmp_path / "traces.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()
    with TraceStore(path) as store:
        store.insert(_row("a"))
        assert store.count() == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_future_schema_is_a_typed_refusal(tmp_path):
    path = tmp_path / "traces.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (99)")
        conn.commit()
    with pytest.raises(TraceStoreVersionError) as exc:
        TraceStore(path)
    assert exc.value.code == "trace_store_version_unsupported"


def test_traces_without_schema_version_is_corrupt(tmp_path):
    path = tmp_path / "traces.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE traces (id TEXT)")
        conn.commit()
    with pytest.raises(TraceStoreCorruptError):
        TraceStore(path)


def test_non_sqlite_file_is_a_typed_error(tmp_path):
    path = tmp_path / "traces.db"
    path.write_bytes(b"this is definitely not sqlite")
    with pytest.raises(TraceStoreCorruptError) as exc:
        TraceStore(path)
    assert exc.value.code == "trace_store_corrupt"


def test_insert_get_and_list_filters(tmp_path):
    with TraceStore(tmp_path / "traces.db") as store:
        store.insert(_row("a", session_id="s1", success=True, latency_ms=10))
        store.insert(_row("b", "2026-10-27T00:01:00.000Z", session_id="s2", success=False,
                          error_code="boom", latency_ms=20))
        assert store.get("a").id == "a"
        assert [r.id for r in store.list()] == ["b", "a"]
        assert [r.id for r in store.list(session_id="s1")] == ["a"]
        assert [r.id for r in store.list(success=False)] == ["b"]
        assert [r.id for r in store.list(limit=1)] == ["b"]
    with pytest.raises(TraceNotFoundError) as exc:
        with TraceStore(tmp_path / "traces.db") as store:
            store.get("missing")
    assert exc.value.code == "trace_not_found"


def test_export_jsonl_contains_one_object_per_line(tmp_path):
    path = tmp_path / "traces.db"
    out = tmp_path / "export.jsonl"
    with TraceStore(path) as store:
        store.insert(_row("a", tool_calls=("read_file",), tool_call_count=1))
        store.insert(_row("b", success=False, error_code="boom"))
        assert store.export_jsonl(out) == 2
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["id"] for line in lines] == ["a", "b"]
    assert lines[0]["tool_calls"] == ["read_file"]
    assert lines[1]["error_code"] == "boom"


def test_prune_by_age_and_record_count(tmp_path):
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    old = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    recent = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    with TraceStore(tmp_path / "traces.db") as store:
        store.insert(_row("old", old))
        store.insert(_row("r1", recent))
        store.insert(_row("r2", "2026-10-26T23:59:00.000Z"))
        assert store.prune(max_age_days=30, now=now) == 1
        assert store.count() == 2
        assert store.prune(max_records=1) == 1
        assert [r.id for r in store.iter_rows()] == ["r2"]
        assert store.prune(max_age_days=0, max_records=0) == 0
        assert store.count() == 1


def test_read_path_does_not_create_a_missing_store(tmp_path):
    from traces.errors import TraceStoreError

    path = tmp_path / "missing" / "traces.db"
    with pytest.raises(TraceStoreError) as exc:
        TraceStore(path, create=False)
    assert exc.value.code == "trace_store_missing"
    assert not path.exists()


def test_read_only_path_does_not_repair_a_zero_byte_store(tmp_path):
    path = tmp_path / "traces.db"
    path.write_bytes(b"")
    with pytest.raises(TraceStoreCorruptError):
        TraceStore(path, create=False)
    assert path.stat().st_size == 0
    assert not (tmp_path / "traces.db-wal").exists()
    assert not (tmp_path / "traces.db-shm").exists()


def test_read_only_path_does_not_chmod_or_create_wal(tmp_path):
    path = tmp_path / "traces.db"
    with TraceStore(path) as store:
        store.insert(_row("ro"))
        store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    path.chmod(0o444)
    before_wal = (tmp_path / "traces.db-wal").exists()
    with TraceStore(path, create=False) as store:
        assert store.count() == 1
    assert path.stat().st_mode & 0o777 == 0o444
    assert (tmp_path / "traces.db-wal").exists() == before_wal


def test_read_sees_a_committed_row_while_a_writer_holds_uncheckpointed_wal(tmp_path):
    path = tmp_path / "traces.db"
    writer = TraceStore(path)
    try:
        writer.insert(_row("live-writer"))
        assert (tmp_path / "traces.db-wal").exists()
        before = (path.stat().st_size, path.stat().st_mode, (tmp_path / "traces.db-wal").stat().st_size)
        with TraceStore(path, create=False) as reader:
            assert reader.get("live-writer").id == "live-writer"
        assert (path.stat().st_size, path.stat().st_mode,
                (tmp_path / "traces.db-wal").stat().st_size) == before
    finally:
        writer.close()
