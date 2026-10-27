"""Local SQLite trace store: schema, migration, insert, query, export, prune.

This is deliberately a *local* store. It has no network client and no upload
path. Writes go through :mod:`traces.recorder`, which redacts and bounds text
before it reaches this module.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from traces.errors import (
    TraceInputError,
    TraceNotFoundError,
    TraceStoreCorruptError,
    TraceStoreError,
    TraceStoreVersionError,
)

CURRENT_SCHEMA_VERSION = 1

_SCHEMA_V1 = (
    """
    CREATE TABLE IF NOT EXISTS traces (
        id                TEXT PRIMARY KEY,
        created_at        TEXT NOT NULL,
        session_id        TEXT NOT NULL DEFAULT '',
        engine            TEXT NOT NULL DEFAULT '',
        model             TEXT NOT NULL DEFAULT '',
        prompt_excerpt    TEXT NOT NULL DEFAULT '',
        response_excerpt  TEXT NOT NULL DEFAULT '',
        tool_calls        TEXT NOT NULL DEFAULT '[]',
        tool_call_count   INTEGER NOT NULL DEFAULT 0,
        latency_ms        REAL,
        prompt_tokens     INTEGER,
        completion_tokens INTEGER,
        cost_usd          REAL,
        energy_joules     REAL,
        flops             REAL,
        success           INTEGER NOT NULL DEFAULT 1,
        error_code        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_traces_session ON traces (session_id)",
)

_MIGRATIONS: dict[int, tuple[str, ...]] = {1: _SCHEMA_V1}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TraceRow:
    """One stored trace. Unknown metrics are ``None``, never a fake zero."""

    id: str
    created_at: str = field(default_factory=_utc_now)
    session_id: str = ""
    engine: str = ""
    model: str = ""
    prompt_excerpt: str = ""
    response_excerpt: str = ""
    tool_calls: tuple[str, ...] = ()
    tool_call_count: int = 0
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    energy_joules: float | None = None
    flops: float | None = None
    success: bool = True
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "session_id": self.session_id,
            "engine": self.engine,
            "model": self.model,
            "prompt_excerpt": self.prompt_excerpt,
            "response_excerpt": self.response_excerpt,
            "tool_calls": list(self.tool_calls),
            "tool_call_count": self.tool_call_count,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "energy_joules": self.energy_joules,
            "flops": self.flops,
            "success": self.success,
            "error_code": self.error_code,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | Sequence[Any]) -> "TraceRow":
        values = tuple(row)
        try:
            tool_calls = json.loads(values[7] or "[]")
        except (TypeError, ValueError):
            tool_calls = []
        if not isinstance(tool_calls, list):
            tool_calls = []
        return cls(
            id=str(values[0]),
            created_at=str(values[1]),
            session_id=str(values[2] or ""),
            engine=str(values[3] or ""),
            model=str(values[4] or ""),
            prompt_excerpt=str(values[5] or ""),
            response_excerpt=str(values[6] or ""),
            tool_calls=tuple(str(name) for name in tool_calls),
            tool_call_count=int(values[8] or 0),
            latency_ms=_optional_float(values[9]),
            prompt_tokens=_optional_int(values[10]),
            completion_tokens=_optional_int(values[11]),
            cost_usd=_optional_float(values[12]),
            energy_joules=_optional_float(values[13]),
            flops=_optional_float(values[14]),
            success=bool(values[15]),
            error_code=None if values[16] in (None, "") else str(values[16]),
        )


class TraceStore:
    """Append-only SQLite store for redacted traces.

    ``create=False`` is the read path: a missing store is a typed
    ``trace_store_missing`` refusal instead of creating an empty file.
    Records must already be redacted/bounded by :mod:`traces.recorder`; this
    module deliberately stores exactly what it is given.
    """

    def __init__(self, db_path: str | Path | None = None, *, create: bool = True) -> None:
        if db_path is None or str(db_path) == "":
            from traces.settings import default_db_path

            db_path = default_db_path()
        self._db_path = ":memory:" if str(db_path) == ":memory:" else str(Path(db_path).expanduser())
        self._create = bool(create)
        self._lock = threading.RLock()
        self._conn = self._open()

    # -- lifecycle ---------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        if self._db_path != ":memory:":
            path = Path(self._db_path)
            if not self._create and not path.exists():
                raise TraceStoreError(
                    f"no trace store at {path} (tracing is off by default)",
                    code="trace_store_missing")
            if self._create:
                try:
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                except OSError as exc:
                    raise TraceStoreError(f"cannot create trace store directory: {exc}") from exc
        try:
            if not self._create:
                # A read command must never repair or rewrite the store. ``mode=ro``
                # also makes a zero-byte/truncated file a typed corruption instead
                # of silently creating a schema in place.
                from urllib.parse import quote

                target = self._db_path
                uri = f"file:{quote(str(Path(target).resolve()))}?mode=ro&immutable=1"
                conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self._verify_schema(conn)
                return conn
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if self._db_path != ":memory:":
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.DatabaseError:
                    pass
            self._ensure_schema(conn)
        except TraceStoreError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            raise TraceStoreCorruptError(
                f"trace store at {self._db_path} is not a readable SQLite database: {exc}"
            ) from exc
        if self._db_path != ":memory:":
            try:
                os.chmod(self._db_path, 0o600)
            except OSError:
                pass
        return conn

    @staticmethod
    def _verify_schema(conn: sqlite3.Connection) -> None:
        has_traces = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='traces'"
        ).fetchone() is not None
        has_version = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone() is not None
        if not has_traces or not has_version:
            raise TraceStoreCorruptError(
                "trace store has no schema (empty or corrupt file)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            raise TraceStoreCorruptError(
                "trace store has a traces table but no schema_version row")
        try:
            version = int(row[0])
        except (TypeError, ValueError) as exc:
            raise TraceStoreCorruptError("trace store has an invalid schema version") from exc
        if version > CURRENT_SCHEMA_VERSION:
            raise TraceStoreVersionError(
                f"trace store schema v{version} is newer than this Qubit supports "
                f"(v{CURRENT_SCHEMA_VERSION})")

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        has_traces = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='traces'"
        ).fetchone() is not None
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            if has_traces:
                raise TraceStoreCorruptError(
                    "trace store has a traces table but no schema_version row")
            version = 0
            conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        else:
            try:
                version = int(row[0])
            except (TypeError, ValueError) as exc:
                raise TraceStoreCorruptError("trace store has an invalid schema version") from exc
        if version > CURRENT_SCHEMA_VERSION:
            raise TraceStoreVersionError(
                f"trace store schema v{version} is newer than this Qubit supports "
                f"(v{CURRENT_SCHEMA_VERSION})")
        for target in range(version + 1, CURRENT_SCHEMA_VERSION + 1):
            try:
                for statement in _MIGRATIONS[target]:
                    conn.execute(statement)
                conn.execute("UPDATE schema_version SET version = ?", (target,))
                conn.commit()
            except sqlite3.DatabaseError as exc:
                raise TraceStoreCorruptError(
                    f"trace store migration to v{target} failed: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._create and self._db_path != ":memory:":
                # Checkpoint before closing so an immutable read sees every row
                # without needing a -wal/-shm sidecar (read commands must not
                # mutate the store or its directory).
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.DatabaseError:
                    pass
            self._conn.close()

    def __enter__(self) -> "TraceStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- writes ------------------------------------------------------------

    def insert(self, row: TraceRow) -> None:
        if not str(row.id or "").strip():
            raise TraceInputError("trace id is required")
        if not str(row.created_at or "").strip():
            raise TraceInputError("trace created_at is required")
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO traces (
                        id, created_at, session_id, engine, model,
                        prompt_excerpt, response_excerpt, tool_calls,
                        tool_call_count, latency_ms, prompt_tokens,
                        completion_tokens, cost_usd, energy_joules, flops,
                        success, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.id,
                        row.created_at,
                        row.session_id,
                        row.engine,
                        row.model,
                        row.prompt_excerpt,
                        row.response_excerpt,
                        json.dumps(list(row.tool_calls), ensure_ascii=False),
                        int(row.tool_call_count),
                        _optional_float(row.latency_ms),
                        _optional_int(row.prompt_tokens),
                        _optional_int(row.completion_tokens),
                        _optional_float(row.cost_usd),
                        _optional_float(row.energy_joules),
                        _optional_float(row.flops),
                        1 if row.success else 0,
                        row.error_code,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise TraceStoreError(
                    f"trace {row.id!r} already exists", code="trace_exists") from exc
            except sqlite3.DatabaseError as exc:
                raise TraceStoreError(f"cannot write trace: {exc}") from exc

    def prune(
        self,
        *,
        max_age_days: int | None = None,
        max_records: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """Delete traces older than *max_age_days* and everything past *max_records*.

        ``0`` (or ``None``) disables that bound. Returns the number deleted.
        """
        deleted = 0
        with self._lock:
            try:
                if max_age_days is not None and int(max_age_days) > 0:
                    moment = now or datetime.now(timezone.utc)
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=timezone.utc)
                    cutoff = (moment - timedelta(days=int(max_age_days))).strftime(
                        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    cursor = self._conn.execute(
                        "DELETE FROM traces WHERE created_at < ?", (cutoff,))
                    deleted += max(0, cursor.rowcount)
                if max_records is not None and int(max_records) > 0:
                    cursor = self._conn.execute(
                        """
                        DELETE FROM traces WHERE id NOT IN (
                            SELECT id FROM traces
                            ORDER BY created_at DESC, id DESC
                            LIMIT ?
                        )
                        """,
                        (int(max_records),),
                    )
                    deleted += max(0, cursor.rowcount)
                self._conn.commit()
            except sqlite3.DatabaseError as exc:
                raise TraceStoreError(f"cannot prune traces: {exc}") from exc
        return deleted

    # -- reads -------------------------------------------------------------

    def get(self, trace_id: str) -> TraceRow:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM traces WHERE id = ?", (str(trace_id),)).fetchone()
        if row is None:
            raise TraceNotFoundError(f"no trace with id {trace_id!r}")
        return TraceRow.from_row(row)

    def list(
        self,
        *,
        limit: int = 50,
        session_id: str | None = None,
        success: bool | None = None,
    ) -> list[TraceRow]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if success is not None:
            clauses.append("success = ?")
            params.append(1 if success else 0)
        where = " AND ".join(clauses) if clauses else "1=1"
        bounded = max(1, min(int(limit), 1000))
        params.append(bounded)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM traces WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [TraceRow.from_row(row) for row in rows]

    def iter_rows(self) -> Iterator[TraceRow]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM traces ORDER BY created_at ASC, id ASC")
            rows = cursor.fetchall()
        for row in rows:
            yield TraceRow.from_row(row)

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM traces").fetchone()
        return int(row[0]) if row else 0

    def export_jsonl(self, destination: str | Path | Any | None = None) -> int:
        """Write one JSON object per line; return the number exported.

        ``destination`` may be a path or a text file object. ``None`` writes to
        stdout. The stored text is already redacted; export does not add or
        remove fields.
        """
        count = 0
        close = False
        if destination is None:
            stream = sys.stdout
        elif isinstance(destination, (str, Path)):
            path = Path(destination).expanduser()
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            stream = path.open("w", encoding="utf-8")
            close = True
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        else:
            stream = destination
        try:
            for row in self.iter_rows():
                stream.write(json.dumps(row.as_dict(), ensure_ascii=False) + "\n")
                count += 1
        finally:
            if close:
                stream.close()
        return count


__all__ = ["CURRENT_SCHEMA_VERSION", "TraceRow", "TraceStore"]
