"""Packet 5B: scoped SQLite/FTS5 personal-memory store.

Contract adapted from the pinned Mem0 record (docs/archive/2026-09-21/UPSTREAM_REUSE.md R8):
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
import math
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


# Separator between the scope and the content identity inside the durable
# ``mkey``. The dedup identity is (scope, content), so the same fact stored in
# two scopes never collides on the UNIQUE(mkey) column while identical facts in
# the same scope still dedup (Mem0 contract).
_SCOPE_SEP = "\x00"


def scoped_key(scope: str, content_key: str) -> str:
    return f"{scope}{_SCOPE_SEP}{content_key}"


def redact_secrets(text: str) -> str:
    """Replace every secret-shaped substring so raw secret-shaped content never
    reaches logs, audit records, prompts, or diagnostic output."""
    out = str(text or "")
    for shape in _SECRET_SHAPES:
        out = re.sub(shape, "[REDACTED]", out)
    return out


def _validate_numeric(name: str, value: Any) -> float:
    """Reject NaN/Inf at the boundary (SQLite would store them silently and
    break JSON/journal round-trips later)."""
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoryStoreError(f"{name} must be a finite number") from exc
    if not math.isfinite(v):
        raise MemoryStoreError(f"{name} must be a finite number")
    return v


def _validate_optional_ts(name: str, value: Optional[Any]) -> None:
    if value is not None:
        _validate_numeric(name, value)


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
            # Migration 2 (Packet 8M): optional GENOME temporal-fact validity
            # window (valid_from INCLUSIVE / valid_until EXCLUSIVE, NULL = open
            # window). Additive nullable columns; legacy rows are unaffected.
            """ALTER TABLE memories ADD COLUMN valid_from REAL;
               ALTER TABLE memories ADD COLUMN valid_until REAL;
               ALTER TABLE memories ADD COLUMN believed_by TEXT;
               CREATE INDEX IF NOT EXISTS idx_mem_valid ON memories(valid_until);""",
            # Migration 3 (Packet 8M repair): scope-qualified dedup identity.
            # The dedup key must be (scope, content), not content alone, so the
            # identical fact can be stored independently in different scopes
            # (repairs the global UNIQUE(mkey) collision). Existing rows are
            # rewritten to scope\x00<content>; the rewrite is one-to-one so the
            # UNIQUE constraint is preserved. Guarded against re-application.
            """UPDATE memories SET mkey = scope || char(0) || mkey
               WHERE instr(mkey, scope || char(0)) != 1;""",
        ]
        # NOTE: an IMMEDIATE write_txn wrapper (see voicebridge.sqlstore)
        # was tried here and REVERTED: executescript()
        # implicitly commits any pending transaction, so an explicit BEGIN
        # IMMEDIATE wrapper ends with "cannot commit - no transaction is
        # active" on every fresh open. The deferred `with self._conn:`
        # transaction is the compatible shape for executescript migrations.
        with self._conn:  # atomic migration transaction
            for i in range(version, len(migrations)):
                self._conn.executescript(migrations[i])
                self._conn.execute(f"PRAGMA user_version={i + 1}")

    # --------------------------------------------------------- internals
    def _insert(self, *, tier: str, scope: str, text: str, confidence: float,
                source: str, provenance: List[str], kind: str,
                expires_at: Optional[float], supersedes: Optional[int],
                staged: bool,
                valid_from: Optional[float] = None,
                valid_until: Optional[float] = None,
                believed_by: Optional[str] = None,
                mkey: Optional[str] = None) -> int:
        if tier not in TIERS:
            raise MemoryStoreError(f"unknown tier: {tier}")
        _validate_numeric("confidence", confidence)
        _validate_optional_ts("expires_at", expires_at)
        _validate_optional_ts("valid_from", valid_from)
        _validate_optional_ts("valid_until", valid_until)
        if (valid_from is not None and valid_until is not None
                and valid_until <= valid_from):
            raise MemoryStoreError(
                "valid_until must be later than valid_from")
        content_key = mkey or norm_hash(text)
        key = scoped_key(scope, content_key)
        cur = self._conn.execute(
            "INSERT INTO memories(mkey, tier, scope, kind, text, norm_hash,"
            " confidence, source, provenance, created_at, updated_at,"
            " expires_at, supersedes, tombstoned, staged, valid_from,"
            " valid_until, believed_by)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
            (key, tier, scope, kind, text, content_key, float(confidence),
             source, json.dumps(provenance), time.time(), time.time(),
             expires_at, supersedes, 1 if staged else 0, valid_from,
             valid_until, believed_by))
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
        # Defensive redaction: secret-shaped content must never enter the
        # append-only audit trail even if a future caller skips the write
        # boundary checks.
        raw = json.dumps(payload) if payload is not None else None
        stored = redact_secrets(raw) if raw is not None else None
        self._conn.execute(
            "INSERT INTO history(memory_id, action, payload, ts)"
            " VALUES(?,?,?,?)",
            (memory_id, action, stored, time.time()))

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
                 expires_at: Optional[float] = None,
                 valid_from: Optional[float] = None,
                 valid_until: Optional[float] = None,
                 believed_by: Optional[str] = None,
                 mkey: Optional[str] = None) -> Dict[str, Any]:
        """Explicit durable fact. Dedupes on normalized hash (Mem0 contract:
        duplicate adds are skipped). Returns the record (existing or new).

        ``valid_from``/``valid_until`` (Packet 8M) carry the GENOME temporal
        window: valid_from is INCLUSIVE, valid_until is EXCLUSIVE (NULL = open).
        ``believed_by`` attributes the fact to a believer (multi-agent).
        ``mkey`` overrides the dedup identity (used by the temporal adapter so
        re-recorded belief facts are distinct windows, not skipped dups)."""
        if not (text or "").strip():
            raise MemoryStoreError("cannot remember empty text")
        for shape in _SECRET_SHAPES:
            if re.search(shape, text):
                raise SecretRejectedError("secret-shaped content rejected")
        _validate_optional_ts("expires_at", expires_at)
        _validate_optional_ts("valid_from", valid_from)
        _validate_optional_ts("valid_until", valid_until)
        if (valid_from is not None and valid_until is not None
                and valid_until <= valid_from):
            raise MemoryStoreError(
                "valid_until must be later than valid_from")
        with self._lock:
            content_key = mkey or norm_hash(text)
            key = scoped_key(scope, content_key)
            existing = self._conn.execute(
                "SELECT * FROM memories WHERE mkey=? AND scope=? "
                "AND tombstoned=0 AND staged=0",
                (key, scope)).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE memories SET updated_at=? WHERE id=?",
                    (time.time(), existing["id"]))
                self._history(existing["id"], "dup_skip",
                              {"hash": content_key})
                self._conn.commit()
                return self._row_to_dict(existing)
            try:
                mid = self._insert(tier=tier, scope=scope, text=text,
                                   confidence=confidence, source=source,
                                   provenance=list(provenance or []),
                                   kind=kind, expires_at=expires_at,
                                   supersedes=None, staged=False,
                                   valid_from=valid_from,
                                   valid_until=valid_until,
                                   believed_by=believed_by,
                                   mkey=content_key)
            except sqlite3.IntegrityError:
                # concurrent same-scope same-content write: another thread won
                # the race; treat as the Mem0 duplicate-skip path
                existing = self._conn.execute(
                    "SELECT * FROM memories WHERE mkey=? AND scope=? "
                    "AND tombstoned=0 AND staged=0",
                    (key, scope)).fetchone()
                if existing is None:
                    raise
                self._conn.execute(
                    "UPDATE memories SET updated_at=? WHERE id=?",
                    (time.time(), existing["id"]))
                self._history(existing["id"], "dup_skip",
                              {"hash": content_key})
                self._conn.commit()
                return self._row_to_dict(existing)
            self._conn.commit()
            return self.get(mid)

    def get(self, memory_id: int) -> Dict[str, Any]:
        row = self._conn.execute("SELECT * FROM memories WHERE id=?",
                                 (memory_id,)).fetchone()
        if row is None:
            raise MemoryStoreError(f"no memory id {memory_id}")
        return self._row_to_dict(row)

    def find_by_content(self, text: str, *, scope: str = "default"
                        ) -> Optional[Dict[str, Any]]:
        """Active record whose content normalizes to ``text`` in ``scope``, or
        None. Used for origin-bound authority checks before a mutation."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE mkey=? AND scope=? "
            "AND tombstoned=0 AND staged=0",
            (scoped_key(scope, norm_hash(text)), scope)).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def search(self, query: str, *, scope: Optional[str] = None,
               tier: Optional[str] = None, limit: int = 20,
               offset: int = 0,
               include_tombstoned: bool = False) -> List[Dict[str, Any]]:
        """FTS5 full-text search (stdlib), tombstone/expiry filtered.

        ``offset`` pages the deterministic result order (the same
        ``ORDER BY f.rowid DESC`` the unpaged query used), so a caller can walk
        a large result set without changing what counts as a match. Falls back
        to a deterministic LIKE scan when FTS5 is unavailable.
        """
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise MemoryStoreError("offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise MemoryStoreError("limit must be a non-negative integer")
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
                q += " ORDER BY f.rowid DESC LIMIT ? OFFSET ?"
                params.extend([int(limit), int(offset)])
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
                rows = rows[offset:offset + limit]
            return [self._row_to_dict(r) for r in rows]

    def correct(self, old_text: str, new_text: str, *,
                scope: str = "default", source: str = "user") -> Dict[str, Any]:
        """Correct a stale fact: new record supersedes old; old is
        tombstoned. Corrected facts then outrank stale ones in search."""
        if not (new_text or "").strip():
            raise MemoryStoreError("cannot correct to empty text")
        for shape in _SECRET_SHAPES:
            if re.search(shape, new_text):
                raise SecretRejectedError("secret-shaped correction rejected")
        with self._lock:
            key = scoped_key(scope, norm_hash(old_text))
            target = self._conn.execute(
                "SELECT * FROM memories WHERE mkey=? AND scope=? "
                "AND tombstoned=0 AND staged=0",
                (key, scope)).fetchone()
            if target is None:
                raise MemoryStoreError(
                    "no active memory matching the requested content")
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

    def update(self, memory_id: int, *, text: Optional[str] = None,
               tier: Optional[str] = None,
               confidence: Optional[float] = None,
               source: str = "user",
               reason: Optional[str] = None) -> Dict[str, Any]:
        """In-place partial update of one active record by id.

        Ported from the reference memory update path (holographic
        ``update_fact`` / Mem0 ``_backend.update``): fields are changed on the
        existing row (no supersede copy) and the append-only history records an
        ``update`` entry. Scope is preserved exactly as stored, so the update
        can never move a record across a permission boundary; a content change
        re-derives the normalized dedup identity and refuses to collide with a
        different active record. Secret-shaped content is rejected at the same
        write boundary ``remember``/``correct`` use.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=? AND tombstoned=0",
                (memory_id,)).fetchone()
            if row is None:
                raise MemoryStoreError(f"no active memory id {memory_id}")
            scope = row["scope"]
            sets: List[str] = []
            params: list = []
            new_text: Optional[str] = None
            if text is not None:
                new_text = str(text)
                if not new_text.strip():
                    raise MemoryStoreError("cannot update to empty text")
                for shape in _SECRET_SHAPES:
                    if re.search(shape, new_text):
                        raise SecretRejectedError(
                            "secret-shaped content rejected")
                new_hash = norm_hash(new_text)
                new_key = scoped_key(scope, new_hash)
                clash = self._conn.execute(
                    "SELECT id FROM memories WHERE mkey=? AND scope=? "
                    "AND tombstoned=0 AND id!=?",
                    (new_key, scope, memory_id)).fetchone()
                if clash is not None:
                    raise MemoryStoreError(
                        "another active memory already has that content")
                sets.extend(("text=?", "norm_hash=?", "mkey=?"))
                params.extend((new_text, new_hash, new_key))
            if tier is not None:
                if tier not in TIERS:
                    raise MemoryStoreError(f"unknown tier: {tier}")
                sets.append("tier=?")
                params.append(tier)
            if confidence is not None:
                sets.append("confidence=?")
                params.append(_validate_numeric("confidence", confidence))
            if not sets:
                raise MemoryStoreError("nothing to update")
            sets.append("updated_at=?")
            params.append(time.time())
            try:
                self._conn.execute(
                    f"UPDATE memories SET {', '.join(sets)} WHERE id=?",
                    (*params, memory_id))
            except sqlite3.IntegrityError:
                # The dedup identity is UNIQUE across scopes' tombstones too;
                # surface a typed store error instead of a raw sqlite failure.
                raise MemoryStoreError(
                    "another memory already has that content") from None
            if new_text is not None or tier is not None:
                self._unindex_fts(memory_id)
                self._index_fts(memory_id, tier or row["tier"], scope,
                                new_text if new_text is not None
                                else row["text"])
            self._history(memory_id, "update",
                          {"source": source, "reason": reason,
                           "fields": sorted(
                               {c.split("=")[0] for c in sets
                                if c != "updated_at=?"})})
            self._conn.commit()
            return self.get(memory_id)

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