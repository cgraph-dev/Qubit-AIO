"""Packet 8P verified-skill memory — SQLite persistence mixin.

Owns the schema (versioned via PRAGMA user_version), row encoding/decoding,
and the low-level CRUD/query helpers used by the lifecycle, recall, and
transfer mixins. Newer-than-supported schema is a typed fail-closed error.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from ._models import (SKILL_STATE_SCHEMA, SkillRecord)


class PersistMixin:
    """Provides the SQLite connection + row helpers to the composed store."""

    _conn: sqlite3.Connection
    _lock: threading.RLock

    def _open(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @property
    def path(self) -> Path:
        return self._path

    def _migrate(self) -> None:
        cur = self._conn.execute("PRAGMA user_version")
        ver = int(cur.fetchone()[0])
        if ver > SKILL_STATE_SCHEMA:
            from ._models import VerifiedSkillStateError
            raise VerifiedSkillStateError(
                f"state schema {ver} is newer than supported "
                f"{SKILL_STATE_SCHEMA} (fail closed; do not downgrade in place)")
        if ver < 1:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS skills(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    implementation TEXT NOT NULL,
                    acceptance_test TEXT NOT NULL,
                    task TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    cost TEXT NOT NULL,
                    trust INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    parents TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    embedding_version TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    utility_score REAL NOT NULL,
                    status TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    uses INTEGER NOT NULL,
                    success_rate REAL NOT NULL,
                    pinned INTEGER NOT NULL,
                    check_strength INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    CHECK(status IN ('quarantined','verified','refuted','archived')),
                    CHECK(kind IN ('positive','negative')),
                    CHECK(tier IN ('hot','warm','cold')),
                    CHECK(cost IN ('cheap','normal','expensive'))
                );
                CREATE TABLE IF NOT EXISTS retrievals(
                    skill_id TEXT NOT NULL,
                    task_hash TEXT NOT NULL,
                    retrieved_at REAL NOT NULL,
                    PRIMARY KEY(skill_id, task_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_skills_status_tier
                    ON skills(status, tier);
                CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
                CREATE INDEX IF NOT EXISTS idx_retrievals_skill
                    ON retrievals(skill_id);
                """
            )
            self._conn.execute(f"PRAGMA user_version={SKILL_STATE_SCHEMA}")
        self._conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> SkillRecord:
        return SkillRecord(
            id=str(row["id"]), name=str(row["name"]),
            interface=str(row["interface"]),
            implementation=str(row["implementation"]),
            acceptance_test=str(row["acceptance_test"]),
            task=str(row["task"]),
            capabilities=tuple(json.loads(row["capabilities"] or "[]")),
            cost=str(row["cost"]), trust=int(row["trust"]),
            model=str(row["model"]),
            parents=tuple(json.loads(row["parents"] or "[]")),
            evidence=str(row["evidence"]),
            embedding_version=str(row["embedding_version"]),
            embedding=list(json.loads(row["embedding"] or "[]")),
            utility_score=float(row["utility_score"]),
            status=str(row["status"]), kind=str(row["kind"]),
            tier=str(row["tier"]), uses=int(row["uses"]),
            success_rate=float(row["success_rate"]), pinned=bool(row["pinned"]),
            check_strength=int(row["check_strength"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _insert(self, rec: SkillRecord) -> None:
        # RLock is reentrant: safe whether or not the caller already holds it.
        # Every execute+commit pair is atomic so concurrent writer threads can
        # never split a transaction ("cannot commit - no transaction active").
        with self._lock:
            self._conn.execute(
                """INSERT INTO skills(id,name,interface,implementation,
                   acceptance_test,task,capabilities,cost,trust,model,parents,
                   evidence,embedding_version,embedding,utility_score,status,kind,
                   tier,uses,success_rate,pinned,check_strength,created_at,
                   updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rec.id, rec.name, rec.interface, rec.implementation,
                 rec.acceptance_test, rec.task,
                 json.dumps(sorted(rec.capabilities)), rec.cost, rec.trust,
                 rec.model, json.dumps(list(rec.parents)), rec.evidence,
                 rec.embedding_version, json.dumps(rec.embedding),
                 rec.utility_score, rec.status, rec.kind, rec.tier, rec.uses,
                 rec.success_rate, int(rec.pinned), rec.check_strength,
                 rec.created_at, rec.updated_at))
            self._conn.commit()

    def _update_row(self, rec: SkillRecord) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE skills SET name=?,interface=?,implementation=?,
                   acceptance_test=?,task=?,capabilities=?,cost=?,trust=?,model=?,
                   parents=?,evidence=?,embedding=?,utility_score=?,status=?,kind=?,
                   tier=?,uses=?,success_rate=?,pinned=?,check_strength=?,
                   updated_at=? WHERE id=?""",
                (rec.name, rec.interface, rec.implementation,
                 rec.acceptance_test, rec.task,
                 json.dumps(sorted(rec.capabilities)), rec.cost, rec.trust,
                 rec.model, json.dumps(list(rec.parents)), rec.evidence,
                 json.dumps(rec.embedding), rec.utility_score, rec.status,
                 rec.kind, rec.tier, rec.uses, rec.success_rate,
                 int(rec.pinned), rec.check_strength, rec.updated_at, rec.id))
            self._conn.commit()

    def _get(self, skill_id: str) -> Optional[SkillRecord]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM skills WHERE id=?",
                                     (skill_id,))
            row = cur.fetchone()
            return self._row_to_record(row) if row else None

    def _query(self, sql: str, params: Tuple = ()) -> List[SkillRecord]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [self._row_to_record(r) for r in cur.fetchall()]

    def _distinct_retrieval_tasks(self, skill_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM retrievals WHERE skill_id=?",
                (skill_id,))
            return int(cur.fetchone()[0])

    def _delete_row(self, skill_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM retrievals WHERE skill_id=?",
                               (skill_id,))
            self._conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))
            self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
