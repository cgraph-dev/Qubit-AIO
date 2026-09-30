"""C7 — ONE canonical Qubit session/history authority.

Decision (documented): the single writable chat-transcript store is the
vendored Qubit ``SessionDB`` (SQLite ``state.db``) under the Qubit-owned
runtime home (``qubit_runtime.use_qubit_home()``). The terminal/TUI/agent
already writes that database directly; the native surfaces (``/qubit-chat``,
CLI, dashboard, voice) reach the SAME database through
:class:`QubitSessionStore`, a strict adapter that reproduces the native
``ChatSessionStore`` contract on top of it.

There is exactly one writer path per surface and one database, so no
"two writable stores synchronized by copying" exists:

* terminal/TUI/agent  -> ``qubit_state.SessionDB`` (same ``state.db``)
* native chat/CLI/dashboard/voice -> ``QubitSessionStore`` -> same ``SessionDB``

:func:`voicebridge.chat_transport.store` returns this adapter. The legacy JSON
``ChatSessionStore`` is retained only as a non-production utility for the S5
draft/comment stores' unit tests and is never reached by the product surfaces.

Preservation: session ids, message ids/order, timestamps, roles (including
``system`` and ``tool``), tool calls/results, display metadata, and the
session's model/provider/archive columns are read/written through the vendored
API, never dropped. ``delete`` is implemented as the vendored reversible
archive so both surfaces behave identically and nothing is destroyed.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chat_transport import (
    DEFAULT_SESSION,
    MAX_MESSAGES,
    MAX_NAME_CHARS,
    MAX_TEXT_CHARS,
)

SOURCE = "dashboard"
AUTHORITY = "voicebridge.qubit_session_store.QubitSessionStore"
STATE_DB_FILENAME = "state.db"

_DBS: Dict[str, Any] = {}
_DBS_LOCK = threading.Lock()
#: Resolved canonical ``state.db`` path -> the single Qubit-side writer owner.
#: Enforces the second-writer invariant: only :data:`AUTHORITY` may hold the
#: claim, so a Qubit module cannot silently register itself as a second writer
#: of the one chat-transcript store.
_WRITE_OWNERS: Dict[str, str] = {}
_WRITE_LOCK = threading.Lock()
_PRESERVED_ROLES = ("user", "assistant", "system", "tool")


class SessionDbWriteAuthorityError(RuntimeError):
    """Typed refusal: a second/divergent writer targeted the canonical state.db.

    The single writable chat-transcript store is the vendored
    ``qubit_state.SessionDB`` at ``<runtime_home>/state.db`` (see the module
    docstring). Exactly one Qubit-side writer owner may hold it:
    :data:`AUTHORITY` (``QubitSessionStore``). A Qubit module that (a) opens a
    *different* ``state.db`` as the chat-transcript authority — in particular
    the operator's external ``~/.hermes/state.db`` — or (b) registers itself as
    a second writer of the canonical store, is refused with this error before
    any write, so a split-brain store cannot be introduced silently. The
    terminal/TUI/agent is the vendored runtime's own single writer of the SAME
    ``state.db`` and is out of scope for this Qubit-side claim.
    """

    code = "SESSIONDB_SECOND_WRITER_REFUSED"

    def __init__(self, detail: str, *, path: Any = None,
                 owner: str = "") -> None:
        super().__init__(detail)
        self.detail = str(detail)
        self.path = str(path) if path is not None else ""
        self.owner = str(owner)

    def as_dict(self) -> Dict[str, Any]:
        return {"state": "REFUSED", "code": self.code, "detail": self.detail,
                "path": self.path, "owner": self.owner}

# B-04M fidelity: the vendored ``sessions`` columns a migration must restore.
_SESSION_COLUMNS = (
    "source", "user_id", "session_key", "chat_id", "chat_type", "thread_id",
    "display_name", "origin_json", "expiry_finalized", "model", "model_config",
    "system_prompt", "system_prompt_hash", "parent_session_id", "started_at",
    "ended_at", "end_reason", "message_count", "tool_call_count",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "cwd", "git_branch", "git_repo_root",
    "git_metadata_generation", "billing_provider", "billing_base_url",
    "billing_mode", "estimated_cost_usd", "actual_cost_usd", "cost_status",
    "cost_source", "pricing_version", "title", "title_source",
    "last_activity_at", "last_activity_description", "last_activity_provenance",
    "api_call_count", "handoff_state", "handoff_platform", "handoff_error",
    "profile_name", "rewind_count", "archived", "pinned", "hidden",
    "last_read_at", "tool_names",
)
# B-04M: terminal/compression columns must be written AFTER the transcript is
# appended. The vendored transcript guard refuses any append to a session whose
# row says it was closed by compression (`ended_at` set, `end_reason ==
# "compression"`); restoring those columns first made the 26 compression-closed
# sessions in a real Qubit home un-importable, silently dropping their
# messages. Deferring them keeps the guard honest for live writes while still
# restoring the exact terminal state once the history is in place.
_TERMINAL_SESSION_COLUMNS = ("ended_at", "end_reason")
# B-04M: the aggregate counters are also written AFTER the transcript append.
# The vendored batch append bumps ``message_count``/``tool_call_count`` once per
# inserted row, so restoring the source counters first would double them
# (destination = source + imported). Deferring and rewriting them last restores
# the exact source aggregates.
_DEFERRED_SESSION_COLUMNS = _TERMINAL_SESSION_COLUMNS + (
    "message_count", "tool_call_count")
# Message fields the vendored batch append preserves verbatim.
_MESSAGE_FIELDS = (
    "tool_name", "tool_call_id", "tool_calls", "reasoning",
    "reasoning_content", "reasoning_details", "codex_reasoning_items",
    "codex_message_items", "token_count", "finish_reason",
    "effect_disposition", "platform_message_id", "observed",
    "_compressed_summary", "api_content", "display_kind", "display_metadata",
)


def _bounded(text: Any, limit: int) -> str:
    return str(text or "")[:limit]


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    return str(value)


def _db_for(path: Path):
    key = str(path)
    with _DBS_LOCK:
        db = _DBS.get(key)
        if db is None:
            # Second-writer invariant: refuse the external fork store and claim
            # the single Qubit-side writer slot BEFORE constructing the handle.
            refuse_external_state_db(path)
            claim_state_db_writer(path)
            from qubit_state import SessionDB  # type: ignore

            db = SessionDB(db_path=path)
            _DBS[key] = db
        return db


def qubit_state_db_path(cfg: Any = None) -> Path:
    """The canonical database path for *cfg* (Qubit-owned runtime home).

    PRB-03: when a ``Config`` is supplied the path follows ``cfg.state_dir()``
    so a test, profile, or custom state dir gets its OWN ``state.db`` instead of
    the process-global one. With no config the process default is used. The
    fail-closed guard refuses to resolve the operator's real home from a test.
    """
    from . import qubit_runtime

    qubit_runtime.ensure_path()
    home = qubit_runtime.runtime_home_for_cfg(cfg)
    qubit_runtime.assert_safe_test_home(home)
    return Path(home) / STATE_DB_FILENAME


def _external_hermes_state_db() -> Path:
    """The operator's external fork store (``~/.hermes/state.db``).

    Computed from ``Path.home()`` at call time (never a constant) so a test
    sandbox with a redirected ``HOME`` cannot be mistaken for the operator's
    live install. This store is a read-only migration source, never a Qubit
    authority.
    """
    return Path.home() / ".hermes" / STATE_DB_FILENAME


def refuse_external_state_db(path: Any) -> Path:
    """Refuse the external ``~/.hermes/state.db`` as a Qubit authority.

    Returns the candidate path unchanged when it is not the external store so
    calls can be chained. Raises :class:`SessionDbWriteAuthorityError`
    otherwise.
    """
    candidate = Path(path)
    try:
        same = candidate.expanduser().resolve(strict=False) == \
            _external_hermes_state_db().resolve(strict=False)
    except OSError:
        same = False
    if same:
        raise SessionDbWriteAuthorityError(
            "refusing the external ~/.hermes/state.db as a Qubit "
            "chat-transcript authority; the canonical store is the Qubit-owned "
            "runtime home", path=candidate)
    return candidate


def assert_canonical_state_db(path: Any, cfg: Any = None) -> Path:
    """Assert *path* is the ONE canonical ``state.db`` for *cfg*.

    Refuses the external ``~/.hermes/state.db`` and any ``state.db`` that is not
    the path :func:`qubit_state_db_path` resolves for *cfg*. Returns the
    canonical path. This is the seam a Qubit module uses to prove it is opening
    the single authority rather than a divergent store.
    """
    refuse_external_state_db(path)
    canonical = qubit_state_db_path(cfg)
    try:
        if Path(path).resolve(strict=False) != canonical.resolve(strict=False):
            raise SessionDbWriteAuthorityError(
                f"state.db {Path(path)} is not the canonical chat-transcript "
                f"store {canonical} for this profile", path=path)
    except OSError:
        pass
    return canonical


def claim_state_db_writer(path: Any, owner: str = AUTHORITY) -> str:
    """Register *owner* as THE single Qubit-side writer of ``state.db``.

    Idempotent for the same owner. Raises
    :class:`SessionDbWriteAuthorityError` when a *different* owner already
    holds the claim, so a second writer is detected and refused before it opens
    the store.
    """
    key = str(Path(path))
    with _WRITE_LOCK:
        existing = _WRITE_OWNERS.get(key)
        if existing is not None and existing != owner:
            raise SessionDbWriteAuthorityError(
                f"state.db {key} already has Qubit writer {existing!r}; "
                f"refusing second writer {owner!r}", path=key, owner=owner)
        _WRITE_OWNERS[key] = owner
    return owner


def release_state_db_writer(path: Any) -> None:
    """Drop the write claim for *path* (rollback/teardown seam)."""
    with _WRITE_LOCK:
        _WRITE_OWNERS.pop(str(Path(path)), None)


def registered_state_db_writers() -> Dict[str, str]:
    """Snapshot of ``state.db`` path -> registered Qubit writer owner."""
    with _WRITE_LOCK:
        return dict(_WRITE_OWNERS)


def session_db_write_authority(cfg: Any = None) -> Dict[str, Any]:
    """Typed descriptor of the canonical chat-transcript write authority.

    Reports the ONE canonical path, its home, the backend, and the registered
    Qubit writer (if any). Used by diagnostics and the write-authority tests.
    """
    path = qubit_state_db_path(cfg)
    writer = registered_state_db_writers().get(str(path))
    return {
        "state": "OK",
        "authority": AUTHORITY,
        "backend": "qubit_state.SessionDB",
        "file": STATE_DB_FILENAME,
        "path": str(path),
        "home": str(path.parent),
        "writer": writer,
        "external": False,
    }


def close_cached_db(path: Any) -> None:
    """Close and evict the cached ``SessionDB`` for *path* (rollback seam)."""
    key = str(path)
    with _DBS_LOCK:
        db = _DBS.pop(key, None)
    release_state_db_writer(key)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


class QubitSessionStore:
    """Strict native-contract adapter over the canonical vendored ``SessionDB``."""

    BACKEND = "qubit_state.SessionDB"

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg
        self._path = qubit_state_db_path(cfg)
        self._db = _db_for(self._path)

    # -- io ----------------------------------------------------------------
    def reload(self) -> Dict[str, Any]:
        # SQLite is the live source of truth; nothing is cached in-process.
        return {"state": "OK", "count": self._db.session_count()}

    def _session(self, sid: str) -> Optional[Dict[str, Any]]:
        try:
            return self._db.get_session(sid)
        except Exception:
            return None

    # -- mapping -----------------------------------------------------------
    @staticmethod
    def _rec(s: Dict[str, Any]) -> Dict[str, Any]:
        title = s.get("title") or s.get("display_name") or None
        return {
            "id": s.get("id"),
            "name": title,
            "model": s.get("model"),
            "created_at": float(s.get("started_at") or 0),
            "updated_at": float(s.get("last_activity_at") or s.get("ended_at")
                                or s.get("started_at") or 0),
            "archived": bool(s.get("archived")),
            "message_count": int(s.get("message_count") or 0),
            "source": s.get("source"),
            "identity": "qubit-session",
        }

    @staticmethod
    def _msg(m: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "role": str(m.get("role") or ""),
            "content": _coerce_text(m.get("content")),
            "timestamp": float(m.get("timestamp") or 0) or time.time(),
        }
        meta = {k: m.get(k) for k in (
            "tool_name", "tool_call_id", "tool_calls", "reasoning",
            "reasoning_content", "reasoning_details", "display_kind",
            "display_metadata", "token_count", "finish_reason",
            "effect_disposition", "api_content", "id",
            "platform_message_id", "message_id") if m.get(k) is not None}
        if meta:
            out["meta"] = meta
        # B-04M: surface the preserved platform/message identity at the top
        # level so show/messages/export/voice all carry it, not just the meta
        # sidecar.
        mid = m.get("platform_message_id") or m.get("message_id")
        if mid is not None:
            out["message_id"] = str(mid)
            out["platform_message_id"] = str(mid)
        return out

    def _messages(self, sid: str, *, limit: Optional[int] = None,
                  offset: int = 0) -> List[Dict[str, Any]]:
        try:
            rows = self._db.get_messages(sid, include_compacted=True,
                                         limit=limit, offset=offset)
        except Exception:
            return []
        return [self._msg(m) for m in rows]

    def _set_times(self, sid: str, created_at: Any, updated_at: Any) -> None:
        try:
            now = time.time()
            created = (float(created_at)
                       if isinstance(created_at, (int, float)) and created_at > 0
                       else now)
            updated = (float(updated_at)
                       if isinstance(updated_at, (int, float)) and updated_at > 0
                       else created)
            self._db._write_sql(
                "UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = ?",
                (created, updated, sid))
        except Exception:
            pass

    def _restore_session_columns(self, sid: str, src: Dict[str, Any],
                                 system_prompts: Optional[Dict[str, Any]] = None,
                                 *, defer_terminal: bool = False
                                 ) -> None:
        """Restore every preserved session column a migration carried (B-04M).

        The vendored ``create_session`` only writes a subset; the routing,
        aggregate (token/cost), title, pinned/hidden, lineage, and timestamp
        columns are restored here in one UPDATE so nothing is silently dropped.

        ``defer_terminal`` skips ``ended_at``/``end_reason`` and the aggregate
        counters so the transcript can be appended before the session is marked
        closed and the counters are rewritten to their exact source values (see
        ``_DEFERRED_SESSION_COLUMNS``); :meth:`_restore_terminal_columns` writes
        them afterwards.
        """
        row: Dict[str, Any] = {}
        for col in _SESSION_COLUMNS:
            if defer_terminal and col in _DEFERRED_SESSION_COLUMNS:
                continue
            if col in src and src[col] is not None:
                row[col] = src[col]
        # Migration reader aliases.
        if "created_at" in src and "started_at" not in row:
            row["started_at"] = src["created_at"]
        if "updated_at" in src and "last_activity_at" not in row:
            row["last_activity_at"] = src["updated_at"]
        if not row:
            return
        # A referenced system prompt must exist before its hash is written.
        h = row.get("system_prompt_hash")
        if h and isinstance(system_prompts, dict) and system_prompts.get(h):
            try:
                self._db._write_sql(
                    "INSERT OR IGNORE INTO system_prompts(hash, prompt) "
                    "VALUES (?, ?)", (str(h), str(system_prompts[h])))
            except Exception:
                pass
        cols = ", ".join(f"{c} = ?" for c in row)
        self._db._write_sql(f"UPDATE sessions SET {cols} WHERE id = ?",
                            (*row.values(), sid))

    def _restore_terminal_columns(self, sid: str, src: Dict[str, Any]) -> None:
        """Write deferred columns AFTER the transcript is imported.

        Kept separate from :meth:`_restore_session_columns` so a compression-
        closed source session is not marked closed before its messages land and
        the source aggregate counters are not doubled by the append's per-row
        bumps.
        """
        row: Dict[str, Any] = {}
        for col in _DEFERRED_SESSION_COLUMNS:
            if col in src and src[col] is not None:
                row[col] = src[col]
        if not row:
            return
        cols = ", ".join(f"{c} = ?" for c in row)
        self._db._write_sql(f"UPDATE sessions SET {cols} WHERE id = ?",
                            (*row.values(), sid))

    # -- lifecycle ---------------------------------------------------------
    def create(self, name: str = "", *, session_id: Optional[str] = None,
               model: Optional[str] = None,
               identity: str = "dashboard:user",
               created_at: Optional[float] = None,
               updated_at: Optional[float] = None) -> Dict[str, Any]:
        sid = _bounded(session_id, 80).strip() if session_id else ""
        if not sid:
            sid = f"dashboard-{uuid.uuid4().hex[:12]}"
        if self._session(sid) is not None:
            return {"state": "EXISTS", "code": "SESSION_EXISTS",
                    "detail": f"session {sid!r} already exists"}
        title = _bounded(name, MAX_NAME_CHARS).strip() or None
        self._db.create_session(sid, source=SOURCE, model=model,
                                display_name=title)
        if created_at or updated_at:
            self._set_times(sid, created_at, updated_at)
        rec = self._session(sid) or {}
        return {"state": "OK", "session": self._rec(rec), "messages": []}

    def ensure(self, session_id: str, *, model: Optional[str] = None,
               identity: str = "dashboard:user") -> Dict[str, Any]:
        sid = _bounded(session_id, 80).strip() or DEFAULT_SESSION
        if self._session(sid) is None:
            return self.create("", session_id=sid, model=model, identity=identity)
        rec = self._session(sid) or {}
        return {"state": "OK", "session": self._rec(rec),
                "messages": self._messages(sid)}

    def list(self, *, limit: int = 50, offset: int = 0, query: str = "",
             include_archived: bool = False) -> Dict[str, Any]:
        # The page cap mirrors ``messages`` (1000) so a read surface can page
        # through the whole canonical store instead of silently hiding rows
        # past the first 100 (the state.db holds the real agent history).
        # Archived rows are EXCLUDED by default to match the reference
        # dashboard (`archived="exclude"`); recovery callers pass
        # ``include_archived=True`` explicitly (``restore`` is the write path).
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        needle = (query or "").strip()
        rows = self._db.list_sessions_rich(
            limit=limit, offset=offset, include_archived=include_archived,
            order_by_last_active=True, compact_rows=True,
            search_query=needle or None)
        # Message-CONTENT search (reference ``web_routers/sessions.py::
        # search_sessions``). ``list_sessions_rich(search_query=...)`` only
        # matches session ids/titles, so a query appearing solely inside a
        # transcript returned nothing. Search the vendored FTS index for
        # content hits and merge the sessions a title search missed (with a
        # snippet), so `/api/sessions?q=` and the Sessions page search the
        # actual conversation.
        content_hits: Dict[str, Dict[str, Any]] = {}
        if needle:
            try:
                hits = self._db.search_messages(
                    query=needle, limit=max(limit * 5, 50),
                    fields=("session_id", "role", "snippet")) or []
            except Exception:
                hits = []
            for hit in hits:
                sid = str(hit.get("session_id") or "")
                if sid and sid not in content_hits:
                    content_hits[sid] = {"snippet": hit.get("snippet"),
                                         "role": hit.get("role")}

        def _entry(s: Dict[str, Any]) -> Dict[str, Any]:
            title = s.get("title") or s.get("display_name")
            return {
                "id": s.get("id"), "source": s.get("source"),
                "model": s.get("model"),
                "title": title or s.get("id"),
                "name": title,
                "archived": bool(s.get("archived")),
                "started_at": float(s.get("started_at") or 0),
                "ended_at": s.get("ended_at"),
                "last_active": float(s.get("last_active") or s.get("started_at") or 0),
                "is_active": False,
                "message_count": int(s.get("message_count") or 0),
                "tool_call_count": int(s.get("tool_call_count") or 0),
                "input_tokens": int(s.get("input_tokens") or 0),
                "output_tokens": int(s.get("output_tokens") or 0),
                "preview": s.get("preview") or None,
            }

        out = []
        seen: set = set()
        for s in rows:
            entry = _entry(s)
            sid = str(entry.get("id") or "")
            seen.add(sid)
            if sid in content_hits:
                entry["snippet"] = content_hits[sid]["snippet"]
                entry["match_role"] = content_hits[sid]["role"]
            out.append(entry)
        # Sessions matched only by transcript content (not id/title). The page
        # cap is preserved: title matches come first, content hits fill up to
        # ``limit`` (the caller may still page/slice).
        for sid in content_hits:
            if sid in seen:
                continue
            if len(out) >= limit:
                break
            try:
                s = self._db.get_session_rich_row(sid)
            except Exception:
                s = self._session(sid)
            if not isinstance(s, dict) or not s.get("id"):
                continue
            if not include_archived and s.get("archived"):
                continue
            entry = _entry(s)
            entry["snippet"] = content_hits[sid]["snippet"]
            entry["match_role"] = content_hits[sid]["role"]
            out.append(entry)
            seen.add(sid)
        if needle:
            total = len(out)
        else:
            # ``list_sessions_rich`` defaults to ``include_children=False``, so
            # the rows above exclude subagent/compression child sessions. The
            # count MUST use the same filter or the reported total overstates
            # the listable sessions (child rows are never returned) and the two
            # session reads disagree. This mirrors ``session_count``'s own
            # contract ("Count sessions with list_sessions_rich's filters").
            total = int(self._db.session_count(
                include_archived=include_archived, exclude_children=True))
        return {"state": "OK", "sessions": out, "total": total,
                "limit": limit, "offset": offset}

    def show(self, session_id: str) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        # ``delete`` is a reversible archive; a deleted session is MISSING on
        # the show surface until ``restore`` un-archives it (still discoverable
        # via ``list(include_archived=True)`` for recovery).
        if rec is None or rec.get("archived"):
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        msgs = self._messages(sid)
        return {"state": "OK", "session": self._rec(rec), "messages": msgs,
                "message_count": len(msgs)}

    def rename(self, session_id: str, name: str) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        clean = _bounded(name, MAX_NAME_CHARS).strip()
        if not clean:
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": "rename needs a non-empty name"}
        self._db.set_session_title(sid, clean)
        return {"state": "OK", "session": self._rec(self._session(sid) or rec)}

    def archive(self, session_id: str, archived: bool = True) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        self._db.set_session_archived(sid, bool(archived))
        return {"state": "OK", "session": self._rec(self._session(sid) or rec)}

    def delete(self, session_id: str, *, confirm: bool = False) -> Dict[str, Any]:
        """Reversible archive delete — identical on both surfaces, nothing lost."""
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        if not confirm:
            return {"state": "CONFIRM_REQUIRED", "code": "CONFIRM_REQUIRED",
                    "detail": f"deleting chat session {sid!r} needs confirm=true",
                    "session": self._rec(rec)}
        self._db.set_session_archived(sid, True)
        return {"state": "OK", "session": self._rec(self._session(sid) or rec)}

    def restore(self, session_id: str) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no deleted chat session {sid!r}"}
        self._db.set_session_archived(sid, False)
        return {"state": "OK", "session": self._rec(self._session(sid) or rec)}

    def append(self, session_id: str, role: str, content: str, *,
               meta: Optional[Dict[str, Any]] = None,
               model: Optional[str] = None,
               timestamp: Optional[float] = None) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        if self._session(sid) is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        meta = meta or {}
        self._db.append_message(
            sid, role=str(role), content=_bounded(content, MAX_TEXT_CHARS),
            tool_name=meta.get("tool_name"), tool_calls=meta.get("tool_calls"),
            tool_call_id=meta.get("tool_call_id"), timestamp=timestamp)
        if model:
            try:
                self._db.update_session_model(sid, str(model))
            except Exception:
                pass
        latest = self._messages(sid, limit=1, offset=max(
            0, self._db.message_count(sid) - 1))
        return {"state": "OK",
                "message": latest[-1] if latest else None,
                "message_count": int(self._db.message_count(sid))}

    def messages(self, session_id: str, *, limit: int = 500,
                 offset: int = 0) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        if self._session(sid) is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        msgs = self._messages(sid, limit=limit, offset=offset)
        return {"state": "OK", "session_id": sid, "messages": msgs,
                "total": int(self._db.message_count(sid)),
                "limit": limit, "offset": offset}

    def export(self, session_id: str) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        return {"state": "OK", "schema": "qubit-chat-session/1",
                "exported_at": time.time(), "session": self._rec(rec),
                "messages": self._messages(sid)}

    def import_session(self, payload: Any, *, overwrite: bool = False,
                       identity: str = "dashboard:user") -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": "import needs a session envelope"}
        src = payload.get("session") if isinstance(payload.get("session"), dict) \
            else payload
        if not isinstance(src, dict):
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": "import needs a session object"}
        sid = _bounded(src.get("id", ""), 80).strip()
        if not sid:
            sid = f"dashboard-{uuid.uuid4().hex[:12]}"
        existing_rec = self._session(sid)
        expected = 0
        try:
            expected = int(src.get("expected_messages") or 0)
        except (TypeError, ValueError):
            expected = 0
        repair = False
        if existing_rec is not None and not overwrite:
            # B-04 resumable import: a session whose message count is short of
            # the source is a partial/aborted import, so repair it instead of
            # skipping forever. A complete session stays idempotent.
            have = len(self._messages(sid))
            if expected and have < expected:
                repair = True
            else:
                return {"state": "EXISTS", "code": "SESSION_EXISTS",
                        "detail": f"session {sid!r} already exists"}
        if existing_rec is not None and (overwrite or repair):
            self._db.delete_session(sid)
        name = _bounded(src.get("name") or src.get("display_name") or "",
                        MAX_NAME_CHARS).strip() or None
        source = str(src.get("source") or SOURCE)
        self._db.create_session(sid, source=source,
                                model=src.get("model"),
                                display_name=name)
        # B-04M: restore every preserved session column (routing, aggregates,
        # title, pinned/hidden, lineage, timestamps) in one UPDATE.
        try:
            self._restore_session_columns(
                sid, src,
                payload.get("system_prompts")
                if isinstance(payload.get("system_prompts"), dict) else None,
                defer_terminal=True)
        except Exception:
            # Fall back to the timestamp-only restore so a schema gap never
            # aborts the whole session import.
            if src.get("created_at") or src.get("updated_at"):
                self._set_times(sid, src.get("created_at"),
                                src.get("updated_at"))
        incoming = payload.get("messages")
        if not isinstance(incoming, list):
            incoming = src.get("messages") if isinstance(
                src.get("messages"), list) else []
        # B-04M: import EVERY message (no MAX_MESSAGES truncation) in ONE
        # transaction for the whole session. The vendored batch append
        # preserves reasoning, token/finish metadata, tool rows, and the
        # platform message id. A crash before commit leaves the session with
        # no messages but its ``expected_messages`` resume marker, so a re-run
        # repairs it instead of skipping forever.
        batch: List[Dict[str, Any]] = []
        for m in incoming:
            if not isinstance(m, dict) or m.get("role") not in _PRESERVED_ROLES:
                continue
            entry: Dict[str, Any] = {
                "role": str(m.get("role")),
                "content": _coerce_text(m.get("content")),
                "timestamp": m.get("timestamp"),
            }
            for key in _MESSAGE_FIELDS:
                if m.get(key) is not None:
                    entry[key] = m[key]
            meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
            for key in ("tool_name", "tool_call_id", "tool_calls", "reasoning",
                        "reasoning_content", "reasoning_details", "token_count",
                        "finish_reason", "display_kind", "display_metadata",
                        "effect_disposition", "api_content"):
                if key not in entry and meta.get(key) is not None:
                    entry[key] = meta[key]
            mid = m.get("message_id") or m.get("platform_message_id")
            if mid not in (None, "") and "platform_message_id" not in entry:
                entry["platform_message_id"] = str(mid)
            batch.append(entry)
        count = 0
        if batch:
            appender = getattr(self._db, "append_messages_batch", None)
            if callable(appender):
                count = int(appender(sid, batch) or 0)
            else:  # pragma: no cover - vendored facade always provides it
                for entry in batch:
                    self._db.append_message(sid, **entry)
                    count += 1
        # B-04M: only now that the transcript exists, restore the terminal
        # (ended_at/end_reason) columns. Restoring them earlier made the
        # vendored transcript guard refuse the append for every session the
        # source had closed by compression.
        try:
            self._restore_terminal_columns(sid, src)
        except Exception:
            pass
        return {"state": "OK", "session": self._rec(self._session(sid) or {}),
                "imported": count}

    # -- Qubit-owned per-session metadata (goal / owner / suggestions /
    #    typing / plugin patch) -------------------------------------------
    # The vendored ``model_config`` JSON column is the canonical per-session
    # metadata extension point (the vendored runtime itself stores
    # ``yolo_mode`` / ``browser_model_lock`` there). Qubit keeps its
    # session-scoped metadata in the SAME column of the SAME row in the SAME
    # database, so there is no second store and no divergent copy.
    _META_KEYS = ("goal", "goal_updated_at", "owner", "suggestions",
                  "typing", "plugin", "reasoning")

    def meta_get(self, session_id: str) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        meta: Dict[str, Any] = {}
        for key in self._META_KEYS:
            value = self._db.get_session_model_config_value(sid, key, None)
            if value is not None:
                meta[key] = value
        return {"state": "OK", "sessionId": sid, "meta": meta,
                "session": self._rec(rec), "authority": AUTHORITY}

    def meta_patch(self, session_id: str, patch: Dict[str, Any]
                   ) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        if not isinstance(patch, dict) or not patch:
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": "metadata patch must be a non-empty object"}
        clean: Dict[str, Any] = {}
        for key, value in patch.items():
            name = str(key)[:64]
            if value is None or isinstance(value, (str, int, float, bool,
                                                   list, dict)):
                clean[name] = value
            else:
                clean[name] = str(value)
        self._db.patch_session_model_config(sid, clean)
        return {"state": "OK", "sessionId": sid,
                "meta": self.meta_get(sid).get("meta", {}),
                "session": self._rec(self._session(sid) or rec),
                "authority": AUTHORITY}

    # -- destructive transcript mutations (confirmation + e-stop gated) ----
    def reset(self, session_id: str, *, confirm: bool = False
              ) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        if not confirm:
            return {"state": "CONFIRM_REQUIRED", "code": "CONFIRM_REQUIRED",
                    "detail": f"resetting chat session {sid!r} needs "
                              "confirm=true",
                    "session": self._rec(rec)}
        before = int(self._db.message_count(sid))
        self._db.clear_messages(sid)
        return {"state": "OK", "sessionId": sid, "cleared": before,
                "message_count": int(self._db.message_count(sid)),
                "session": self._rec(self._session(sid) or rec),
                "authority": AUTHORITY}

    def rewind(self, session_id: str, message_id: Any, *,
               confirm: bool = False) -> Dict[str, Any]:
        sid = _bounded(session_id, 80)
        rec = self._session(sid)
        if rec is None:
            return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                    "detail": f"no chat session {sid!r}"}
        if not confirm:
            return {"state": "CONFIRM_REQUIRED", "code": "CONFIRM_REQUIRED",
                    "detail": f"rewinding chat session {sid!r} needs "
                              "confirm=true",
                    "session": self._rec(rec)}
        try:
            target = int(message_id)
        except (TypeError, ValueError):
            return {"state": "FAILED", "code": "SESSION_REWIND_INVALID",
                    "detail": "messageId must be an integer row id"}
        try:
            out = self._db.rewind_to_message(sid, target)
        except ValueError as exc:
            return {"state": "FAILED", "code": "SESSION_REWIND_INVALID",
                    "detail": str(exc)[:300]}
        except Exception as exc:  # noqa: BLE001 - vendored guard errors
            code = getattr(exc, "code", None) or "SESSION_REWIND_REFUSED"
            return {"state": "FAILED", "code": str(code),
                    "detail": str(exc)[:300]}
        return {"state": "OK", "sessionId": sid,
                "rewound_count": int(out.get("rewound_count") or 0),
                "target_message_id": target,
                "new_head_id": out.get("new_head_id"),
                "session": self._rec(self._session(sid) or rec),
                "authority": AUTHORITY}

    def cleanup_empty(self) -> Dict[str, Any]:
        try:
            deleted = int(self._db.delete_empty_sessions() or 0)
        except Exception as exc:  # noqa: BLE001 - maintenance best-effort
            return {"state": "FAILED", "code": "SESSION_CLEANUP_FAILED",
                    "detail": str(exc)[:300]}
        return {"state": "OK", "deleted": deleted, "authority": AUTHORITY}

    # -- Qubit-owned session groups --------------------------------------
    # Group definitions live in a Qubit-owned table in the SAME ``state.db``
    # (one database authority, no second store file, all writes through the
    # vendored ``SessionDB`` write transaction so WAL/locking is shared).
    _GROUPS_TABLE = "qubit_session_groups"
    _GROUP_DEFAULTS_ID = "@defaults"

    def _ensure_groups_table(self) -> None:
        def _do(conn: Any) -> None:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS qubit_session_groups ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
                "data TEXT NOT NULL DEFAULT '{}', "
                "created_at REAL NOT NULL DEFAULT 0, "
                "updated_at REAL NOT NULL DEFAULT 0)")
        self._db._execute_write(_do)

    @staticmethod
    def _group_data(raw: Any) -> Dict[str, Any]:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def groups_list(self) -> Dict[str, Any]:
        self._ensure_groups_table()
        rows = self._db._read_all(
            "SELECT id, name, data, created_at, updated_at "
            "FROM qubit_session_groups ORDER BY created_at ASC, id ASC")
        groups: List[Dict[str, Any]] = []
        default_id = None
        for row in rows:
            if row[0] == self._GROUP_DEFAULTS_ID:
                default_id = self._group_data(row[2]).get("defaultGroupId")
                continue
            data = self._group_data(row[2])
            groups.append({"id": row[0], "name": row[1] or row[0],
                           "createdAt": float(row[3] or 0),
                           "updatedAt": float(row[4] or 0), **data})
        return {"state": "OK", "groups": groups, "total": len(groups),
                "defaultGroupId": default_id, "authority": AUTHORITY}

    def group_put(self, group_id: str, name: str = "",
                  fields: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
        gid = _bounded(group_id, 80).strip()
        if not gid or gid == self._GROUP_DEFAULTS_ID:
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": "group id is required and reserved ids are "
                              "refused"}
        self._ensure_groups_table()
        clean = _bounded(name, MAX_NAME_CHARS).strip() or gid
        data = {str(k)[:64]: v for k, v in (fields or {}).items()}
        now = time.time()

        def _do(conn: Any) -> None:
            conn.execute(
                "INSERT INTO qubit_session_groups "
                "(id, name, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "data = excluded.data, updated_at = excluded.updated_at",
                (gid, clean, json.dumps(data), now, now))
        self._db._execute_write(_do)
        return {"state": "OK", "group": {"id": gid, "name": clean,
                "createdAt": now, "updatedAt": now, **data},
                "authority": AUTHORITY}

    def group_update(self, group_id: str, name: Optional[str] = None,
                     fields: Optional[Dict[str, Any]] = None
                     ) -> Dict[str, Any]:
        gid = _bounded(group_id, 80).strip()
        listing = self.groups_list()
        existing = next((g for g in listing["groups"] if g["id"] == gid), None)
        if existing is None:
            return {"state": "MISSING", "code": "GROUP_NOT_FOUND",
                    "detail": f"no session group {gid!r}"}
        merged = {k: v for k, v in existing.items()
                  if k not in ("id", "name", "createdAt", "updatedAt")}
        merged.update({str(k)[:64]: v for k, v in (fields or {}).items()})
        new_name = existing["name"] if name is None else name
        out = self.group_put(gid, str(new_name), merged)
        if out.get("state") == "OK":
            out["group"]["createdAt"] = existing.get("createdAt")
        return out

    def group_rename(self, group_id: str, name: str) -> Dict[str, Any]:
        clean = _bounded(name, MAX_NAME_CHARS).strip()
        if not clean:
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": "rename needs a non-empty name"}
        gid = _bounded(group_id, 80).strip()
        listing = self.groups_list()
        existing = next((g for g in listing["groups"] if g["id"] == gid), None)
        if existing is None:
            return {"state": "MISSING", "code": "GROUP_NOT_FOUND",
                    "detail": f"no session group {gid!r}"}
        if clean != gid and any(g["id"] == clean
                                for g in listing["groups"]):
            return {"state": "EXISTS", "code": "GROUP_EXISTS",
                    "detail": f"session group {clean!r} already exists"}
        data = {k: v for k, v in existing.items()
                if k not in ("id", "name", "createdAt", "updatedAt")}
        now = time.time()

        def _do(conn: Any) -> None:
            conn.execute(
                "UPDATE qubit_session_groups SET id = ?, name = ?, "
                "data = ?, updated_at = ? WHERE id = ?",
                (clean, clean, json.dumps(data), now, gid))
        self._db._execute_write(_do)
        return {"state": "OK", "renamedFrom": gid,
                "group": {"id": clean, "name": clean,
                          "createdAt": existing.get("createdAt"),
                          "updatedAt": now, **data},
                "authority": AUTHORITY}

    def group_delete(self, group_id: str) -> Dict[str, Any]:
        gid = _bounded(group_id, 80).strip()
        self._ensure_groups_table()

        def _do(conn: Any) -> int:
            cur = conn.execute(
                "DELETE FROM qubit_session_groups WHERE id = ?", (gid,))
            return int(cur.rowcount or 0)
        removed = self._db._execute_write(_do)
        if not removed:
            return {"state": "MISSING", "code": "GROUP_NOT_FOUND",
                    "detail": f"no session group {gid!r}"}
        return {"state": "OK", "deleted": gid, "authority": AUTHORITY}

    def group_defaults_get(self) -> Dict[str, Any]:
        self._ensure_groups_table()
        row = self._db._read_one(
            "SELECT data FROM qubit_session_groups WHERE id = ?",
            (self._GROUP_DEFAULTS_ID,))
        data = self._group_data(row[0]) if row is not None else {}
        return {"state": "OK", **data, "defaults": data,
                "defaultGroupId": data.get("defaultGroupId"),
                "authority": AUTHORITY}

    def group_defaults_patch(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(fields, dict) or not fields:
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": "defaults patch must be a non-empty object"}
        self._ensure_groups_table()
        allowed = ("defaultGroupId", "sectionOrder", "cwd", "worktree")
        patch = {str(k)[:64]: v for k, v in fields.items() if k in allowed}
        if not patch:
            return {"state": "FAILED", "code": "MALFORMED",
                    "detail": f"defaults keys must be one of {allowed}"}
        data = self._group_data(self.group_defaults_get()["defaults"])
        data.update(patch)
        now = time.time()

        def _do(conn: Any) -> None:
            conn.execute(
                "INSERT INTO qubit_session_groups "
                "(id, name, data, created_at, updated_at) "
                "VALUES (?, '', ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data, "
                "updated_at = excluded.updated_at",
                (self._GROUP_DEFAULTS_ID, json.dumps(data), now, now))
        self._db._execute_write(_do)
        return {"state": "OK", **data, "defaults": data,
                "authority": AUTHORITY}


def analytics_usage(cfg: Any = None, *, days: int = 30
                    ) -> Optional[Dict[str, Any]]:
    """Read-only usage aggregates from the canonical ``state.db``.

    Returns ``None`` when the database is absent, so the caller can fall back
    to an honest empty answer instead of fabricated numbers. Real token/cost
    columns are summed from the ``sessions`` table (the same store every
    surface reads), so the analytics fallback is not a shadow of zeros.
    """
    path = qubit_state_db_path(cfg)
    if not path.is_file():
        return None
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        tables = {r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'")}
        if "sessions" not in tables:
            return None
        cols = {r[1] for r in conn.execute("pragma table_info(sessions)")}

        def c(name: str, default: str = "0") -> str:
            return name if name in cols else default

        try:
            days_i = max(1, min(int(days), 365))
        except (TypeError, ValueError):
            days_i = 30
        since = time.time() - days_i * 86400
        row = conn.execute(
            f"select count(*), coalesce(sum({c('input_tokens')}),0), "
            f"coalesce(sum({c('output_tokens')}),0), "
            f"coalesce(sum({c('cache_read_tokens')}),0), "
            f"coalesce(sum({c('reasoning_tokens')}),0), "
            f"coalesce(sum({c('api_call_count')}),0), "
            f"coalesce(sum({c('estimated_cost_usd')}),0), "
            f"coalesce(sum({c('actual_cost_usd')}),0), "
            f"coalesce(sum({c('tool_call_count')}),0) "
            f"from sessions where coalesce({c('started_at')},0) >= ?",
            (since,)).fetchone()
        (total_sessions, inp, out, cache, reason, calls, est, act,
         tools) = row
        daily_rows = conn.execute(
            f"select date({c('started_at')},'unixepoch') d, "
            f"coalesce(sum({c('input_tokens')}),0), "
            f"coalesce(sum({c('output_tokens')}),0), "
            f"coalesce(sum({c('cache_read_tokens')}),0), "
            f"coalesce(sum({c('reasoning_tokens')}),0), "
            f"coalesce(sum({c('estimated_cost_usd')}),0), "
            f"coalesce(sum({c('actual_cost_usd')}),0), count(*), "
            f"coalesce(sum({c('api_call_count')}),0) "
            f"from sessions where coalesce({c('started_at')},0) >= ? "
            f"group by d order by d", (since,)).fetchall()
        model_rows = conn.execute(
            f"select coalesce(nullif(model,''),'unknown') m, "
            f"coalesce({c('billing_provider','')},'') p, "
            f"coalesce(sum({c('input_tokens')}),0), "
            f"coalesce(sum({c('output_tokens')}),0), "
            f"coalesce(sum({c('cache_read_tokens')}),0), "
            f"coalesce(sum({c('reasoning_tokens')}),0), "
            f"coalesce(sum({c('estimated_cost_usd')}),0), "
            f"coalesce(sum({c('actual_cost_usd')}),0), count(*), "
            f"coalesce(sum({c('api_call_count')}),0), "
            f"coalesce(sum({c('tool_call_count')}),0), "
            f"coalesce(max({c('started_at')}),0) "
            f"from sessions where coalesce({c('started_at')},0) >= ? "
            f"group by m, p order by 9 desc, 3 desc limit 50",
            (since,)).fetchall()
    finally:
        conn.close()
    return {
        "days": days_i,
        "totals": {
            "total_input": int(inp), "total_output": int(out),
            "total_cache_read": int(cache), "total_reasoning": int(reason),
            "total_estimated_cost": float(est), "total_actual_cost": float(act),
            "total_sessions": int(total_sessions),
            "total_api_calls": int(calls),
        },
        "daily": [
            {"day": str(d), "input_tokens": int(i), "output_tokens": int(o),
             "cache_read_tokens": int(cr), "reasoning_tokens": int(rt),
             "estimated_cost": float(e), "actual_cost": float(a),
             "sessions": int(s), "api_calls": int(api)}
            for (d, i, o, cr, rt, e, a, s, api) in daily_rows
        ],
        "by_model": [
            {"model": str(m), "provider": str(p), "input_tokens": int(i),
             "output_tokens": int(o), "cache_read_tokens": int(cr),
             "reasoning_tokens": int(rt), "estimated_cost": float(e),
             "actual_cost": float(a), "sessions": int(s),
             "api_calls": int(api), "tool_calls": int(tools),
             "last_used_at": float(last),
             "avg_tokens_per_session": round((int(i) + int(o)) / s, 1)
             if s else 0.0}
            for (m, p, i, o, cr, rt, e, a, s, api, tools, last) in model_rows
        ],
    }
