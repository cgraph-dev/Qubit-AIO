"""Packet 5B: scoped SQLite/FTS5 personal-memory store.

Contract adapted from the pinned Mem0 record (docs/UPSTREAM_REUSE.md R8):
add/search/update/delete/history semantics with normalized-hash
deduplication and per-record provenance. Local extensions mandated by the
plan: core/working/recall/archive tiers, scope isolation, expiry,
tombstones, supersedes/correction, append-only history, staged inferred
memories (never auto-promoted), and private-file modes. Letta is NOT used
(R9 rejected: no code at the pinned ref).

No vector database, no LLM extraction inside the store, no model-prompt
modification. Text normalization and secret rejection are stdlib-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

TIERS = ("core", "working", "recall", "archive")
VALID_ACTIONS = ("remember", "dup_skip", "correct", "corrected",
                 "forget", "purge", "promote", "expire", "update")

_STOP = frozenset(("a", "an", "the", "and", "or", "of", "to", "in", "on",
                    "for", "is", "are", "was", "were", "what", "how", "my",
                    "we", "me", "i", "you", "about", "with", "do", "did",
                    "can", "please", "tell", "give", "that", "this"))

_SECRET_SHAPES = (
    r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9-]{15,}|"
    r"AKIA[0-9A-Z]{16})\b",
    r"\b[0-9]{16}\b",                       # card-like digit runs
    r"(?i)\b(?:api[_ -]?key|token|secret|password|passwd)\s*[:=]\s*\S+",
)


class MemoryStoreError(Exception):
    """Typed store failure (never a silent pass)."""


class SecretRejectedError(MemoryStoreError):
    """A secret-shaped value was refused at the write boundary."""


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def norm_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


class MemoryStore:
    def __init__(self, path: Path | str, *, fts5_only: bool = True) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._fts5 = self._has_fts5()
            if fts5_only and not self._fts5:
                raise MemoryStoreError("FTS5 required but unavailable "
                                       "(fts5_only mode)")
            self._migrate()
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------ schema
    def _has_fts5(self) -> bool:
        try:
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='memories_fts'").fetchone()
            if row:
                return True
            self._conn.execute(
                "CREATE VIRTUAL TABLE memories_fts USING fts5("
                "text, memory_id UNINDEXED, tier UNINDEXED, "
                "scope UNINDEXED, content='')")
            return True
        except sqlite3.OperationalError:
            return False

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        migrations = [
            """CREATE TABLE IF NOT EXISTS memories(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 mkey TEXT UNIQUE,
                 tier TEXT NOT NULL,
                 scope TEXT NOT NULL DEFAULT 'default',
                 kind TEXT NOT NULL DEFAULT 'fact',
                 text TEXT NOT NULL,
                 norm_hash TEXT NOT NULL,
                 confidence REAL DEFAULT 0.5,
                 sensitivity TEXT DEFAULT 'personal',
                 source TEXT,
                 provenance TEXT DEFAULT '[]',
                 created_at REAL,
                 updated_at REAL,
                 expires_at REAL,
                 supersedes INTEGER,
                 tombstoned INTEGER DEFAULT 0,
                 staged INTEGER DEFAULT 0);
               CREATE INDEX IF NOT EXISTS idx_mem_norm ON memories(norm_hash);
               CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope, tier);
               CREATE INDEX IF NOT EXISTS idx_mem_tomb ON memories(tombstoned);
               CREATE INDEX IF NOT EXISTS idx_mem_sup ON memories(supersedes);
               CREATE TABLE IF NOT EXISTS history(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 memory_id INTEGER NOT NULL,
                 action TEXT NOT NULL,
                 payload TEXT,
                 ts REAL);""",
        ]
        with self._conn:  # atomic migration transaction
            for i in range(version, len(migrations)):
                self._conn.executescript(migrations[i])
                self._conn.execute(f"PRAGMA user_version={i + 1}")

    # --------------------------------------------------------- internals
    def _insert(self, *, tier: str, scope: str, text: str, confidence: float,
                source: str, provenance: List[str], kind: str,
                expires_at: Optional[float], supersedes: Optional[int],
                staged: bool) -> int:
        if tier not in TIERS:
            raise MemoryStoreError(f"unknown tier: {tier}")
        mkey = norm_hash(text)
        cur = self._conn.execute(
            "INSERT INTO memories(mkey, tier, scope, kind, text, norm_hash,"
            " confidence, source, provenance, created_at, updated_at,"
            " expires_at, supersedes, tombstoned, staged)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (mkey, tier, scope, kind, text, mkey, float(confidence), source,
             json.dumps(provenance), time.time(), time.time(), expires_at,
             supersedes, 1 if staged else 0))
        mid = cur.lastrowid
        self._index_fts(mid, tier, scope, text)
        self._history(mid, "remember" if not staged else "remember", None)
        return mid

    def _index_fts(self, memory_id: int, tier: str, scope: str, text: str) -> None:
        if not self._fts5:
            return
        self._conn.execute(
            "INSERT INTO memories_fts(rowid, memory_id, tier, scope, text)"
            " VALUES(?,?,?,?,?)", (memory_id, memory_id, tier, scope, text))

    def _unindex_fts(self, memory_id: int) -> None:
        if self._fts5:
            self._conn.execute(
                "DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))

    def _history(self, memory_id: int, action: str, payload: Any) -> None:
        self._conn.execute(
            "INSERT INTO history(memory_id, action, payload, ts)"
            " VALUES(?,?,?,?)",
            (memory_id, action,
             json.dumps(payload) if payload is not None else None,
             time.time()))

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        d["provenance"] = json.loads(d.get("provenance") or "[]")
        return d

    def _active_select(self, *, scope: Optional[str] = None,
                       tier: Optional[str] = None,
                       staged: Optional[bool] = None) -> List[Dict[str, Any]]:
        q = ["SELECT * FROM memories WHERE tombstoned=0"]
        params: list = []
        if scope is not None:
            q.append("AND scope=?")
            params.append(scope)
        if tier is not None:
            q.append("AND tier=?")
            params.append(tier)
        if staged is not None:
            q.append("AND staged=?")
            params.append(1 if staged else 0)
        else:
            q.append("AND staged=0")
        rows = self._conn.execute(" ".join(q), params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------ explicit ops
    def remember(self, text: str, *, tier: str = "recall",
                 scope: str = "default", confidence: float = 1.0,
                 source: str = "user", provenance: Optional[List[str]] = None,
                 kind: str = "fact",
                 expires_at: Optional[float] = None) -> Dict[str, Any]:
        """Explicit durable fact. Dedupes on normalized hash (Mem0 contract:
        duplicate adds are skipped). Returns the record (existing or new)."""
        if not (text or "").strip():
            raise MemoryStoreError("cannot remember empty text")
        for shape in _SECRET_SHAPES:
            if re.search(shape, text):
                raise SecretRejectedError("secret-shaped content rejected")
        with self._lock:
            mkey = norm_hash(text)
            existing = self._conn.execute(
                "SELECT * FROM memories WHERE mkey=? AND scope=? "
                "AND tombstoned=0 AND staged=0",
                (mkey, scope)).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE memories SET updated_at=? WHERE id=?",
                    (time.time(), existing["id"]))
                self._history(existing["id"], "dup_skip",
                              {"hash": mkey})
                self._conn.commit()
                return self._row_to_dict(existing)
            mid = self._insert(tier=tier, scope=scope, text=text,
                               confidence=confidence, source=source,
                               provenance=list(provenance or []),
                               kind=kind, expires_at=expires_at,
                               supersedes=None, staged=False)
            self._conn.commit()
            return self.get(mid)

    def get(self, memory_id: int) -> Dict[str, Any]:
        row = self._conn.execute("SELECT * FROM memories WHERE id=?",
                                 (memory_id,)).fetchone()
        if row is None:
            raise MemoryStoreError(f"no memory id {memory_id}")
        return self._row_to_dict(row)

    def search(self, query: str, *, scope: Optional[str] = None,
               tier: Optional[str] = None, limit: int = 20,
               include_tombstoned: bool = False) -> List[Dict[str, Any]]:
        """FTS5 full-text search (stdlib), tombstone/expiry filtered.
        Falls back to a deterministic LIKE scan when FTS5 is unavailable."""
        now = time.time()
        with self._lock:
            if self._fts5:
                words = [w for w in re.findall(r"\w+", query)
                         if w.lower() not in _STOP and len(w) >= 2]
                terms = " OR ".join(f'"{w}"' for w in words[:6])
                if not terms:
                    return []
                q = ("SELECT m.* FROM memories_fts f "
                     "JOIN memories m ON m.id = f.rowid "
                     "WHERE memories_fts MATCH ? "
                     f"{'AND m.tombstoned=0 AND m.staged=0' if not include_tombstoned else ''}"
                     " AND (m.expires_at IS NULL OR m.expires_at > ?)")
                params: list = [terms, now]
                if scope is not None:
                    q += " AND m.scope=?"
                    params.append(scope)
                if tier is not None:
                    q += " AND m.tier=?"
                    params.append(tier)
                q += " ORDER BY f.rowid DESC LIMIT ?"
                params.append(int(limit))
                rows = self._conn.execute(q, params).fetchall()
            else:
                q = ["SELECT * FROM memories"]
                if not include_tombstoned:
                    q.append("WHERE tombstoned=0 AND staged=0")
                rows = self._conn.execute(" ".join(q)).fetchall()
                nq = normalize_text(query)
                rows = [r for r in rows if nq in r["text"].lower()
                        and (r["expires_at"] is None or r["expires_at"] > now)
                        and (scope is None or r["scope"] == scope)
                        and (tier is None or r["tier"] == tier)]
                rows = rows[:limit]
            return [self._row_to_dict(r) for r in rows]

    def correct(self, old_text: str, new_text: str, *,
                scope: str = "default", source: str = "user") -> Dict[str, Any]:
        """Correct a stale fact: new record supersedes old; old is
        tombstoned. Corrected facts then outrank stale ones in search."""
        with self._lock:
            target = self._conn.execute(
                "SELECT * FROM memories WHERE mkey=? AND scope=? "
                "AND tombstoned=0 AND staged=0",
                (norm_hash(old_text), scope)).fetchone()
            if target is None:
                raise MemoryStoreError(
                    f"no active memory matching: {old_text[:60]!r}")
            mid = self._insert(tier=target["tier"], scope=scope,
                               text=new_text,
                               confidence=1.0, source=source,
                               provenance=list(json.loads(
                                   target["provenance"] or "[]")),
                               kind=target["kind"], expires_at=None,
                               supersedes=target["id"], staged=False)
            self._conn.execute("UPDATE memories SET tombstoned=1 WHERE id=?",
                               (target["id"],))
            self._unindex_fts(target["id"])
            self._history(target["id"], "corrected",
                          {"superseded_by": mid, "to": new_text})
            self._history(mid, "correct",
                          {"supersedes": target["id"]})
            self._conn.commit()
            return self.get(mid)

    def forget(self, memory_id: int, *, reason: str = "user-forget") -> None:
        """Soft delete: tombstone + unindex; history preserved."""
        with self._lock:
            self._conn.execute("UPDATE memories SET tombstoned=1 WHERE id=?",
                               (memory_id,))
            self._unindex_fts(memory_id)
            self._history(memory_id, "forget", {"reason": reason})
            self._conn.commit()

    def purge(self, memory_id: int) -> None:
        """Hard delete (permanent purge). History keeps the audit trail."""
        with self._lock:
            self._unindex_fts(memory_id)
            self._conn.execute("DELETE FROM memories WHERE id=?",
                               (memory_id,))
            self._history(memory_id, "purge", None)
            self._conn.commit()

    def export(self, *, include_tombstoned: bool = False,
               path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
        """Private JSON export of records (active by default)."""
        with self._lock:
            q = "SELECT * FROM memories"
            if not include_tombstoned:
                q += " WHERE tombstoned=0 AND staged=0"
            rows = [self._row_to_dict(r)
                    for r in self._conn.execute(q).fetchall()]
        if path is not None:
            out = Path(path)
            out.write_text(json.dumps(rows, indent=2))
            try:
                os.chmod(out, 0o600)
            except OSError:
                pass
        return rows

    def what_do_i_know(self, *, scope: Optional[str] = None) -> str:
        recs = self._active_select(scope=scope, staged=False)
        lines = []
        for r in sorted(recs, key=lambda x: (x["tier"], x["id"])):
            lines.append(f"[{r['tier']}/{r['scope']}] {r['text']}")
        return "\n".join(lines) or "(no active memories)"

    def history(self, memory_id: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM history WHERE memory_id=? ORDER BY ts",
            (memory_id,)).fetchall()
        return [{"action": r["action"], "ts": r["ts"],
                 "payload": json.loads(r["payload"]) if r["payload"] else None}
                for r in rows]

    # ---------------------------------------------------- staged inferred
    def stage_inferred(self, text: str, *, scope: str = "default",
                       confidence: float, source: str = "model",
                       source_ids: Optional[List[str]] = None,
                       tier: str = "recall") -> int:
        """Stage a model-inferred candidate. NEVER auto-promoted."""
        with self._lock:
            mid = self._insert(tier=tier, scope=scope, text=text,
                               confidence=float(confidence), source=source,
                               provenance=list(source_ids or []),
                               kind="inferred", expires_at=None,
                               supersedes=None, staged=True)
            self._conn.commit()
            return mid

    def staged(self, *, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            return self._active_select(scope=scope, staged=True)

    def promote(self, memory_id: int) -> Dict[str, Any]:
        """Explicit promotion of a staged candidate to durable memory."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=? AND staged=1",
                (memory_id,)).fetchone()
            if row is None:
                raise MemoryStoreError(
                    f"staged memory not found: {memory_id}")
            for shape in _SECRET_SHAPES:
                if re.search(shape, row["text"]):
                    raise SecretRejectedError(
                        "refusing to promote secret-shaped candidate")
            self._conn.execute(
                "UPDATE memories SET staged=0, kind='fact',"
                " confidence=?, updated_at=? WHERE id=?",
                (min(float(row["confidence"]), 1.0), time.time(), memory_id))
            self._history(memory_id, "promote",
                          {"from_staged": True})
            self._conn.commit()
            return self.get(memory_id)

    def close(self) -> None:
        with self._lock:
            self._conn.close()