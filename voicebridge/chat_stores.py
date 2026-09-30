"""Packet 8U — per-session chat projection stores (file drafts + comments/CI).

The chat workspace needs two session-scoped stores that did not exist:

* :class:`FileDraftStore` — unsaved file edits keyed by ``(session_id, path)``;
* :class:`CommentStore` — operator comments and structured CI-detail rows.

Neither duplicates :class:`voicebridge.chat_transport.ChatSessionStore`.
Sessions/messages stay in ``chat_transport`` (one writer for that file); these
stores persist under their *own* schema files and reference a session id only.
A caller may pass ``session_check`` — e.g. the canonical chat store's ``show`` —
so writing to an unknown session is a typed refusal instead of an orphan row.

Single-writer discipline: each store serializes read-modify-write with an
in-process ``RLock`` **and** an ``fcntl`` advisory lock on its file (when
available), then writes atomically (temp + fsync + ``os.replace``). Reads of a
committed file are lock-free. Values are bounded and reads are re-hydrated from
disk, so two store instances cannot silently diverge.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:  # POSIX advisory locking; absent on non-POSIX platforms.
    import fcntl
except ImportError:  # pragma: no cover - platform guard
    fcntl = None  # type: ignore[assignment]

DRAFT_SCHEMA = "qubit-chat-drafts/1"
COMMENT_SCHEMA = "qubit-chat-comments/1"
DRAFT_FILE = "chat_file_drafts.json"
COMMENT_FILE = "chat_comments.json"

MAX_SESSION_CHARS = 80
MAX_PATH_CHARS = 4096
MAX_DRAFT_CHARS = 200_000
MAX_COMMENT_CHARS = 4000
MAX_CI_DETAIL_CHARS = 8000
MAX_ACTOR_CHARS = 120
MAX_ROWS = 5000

CI_STATUSES = ("pending", "queued", "in_progress", "success", "failure",
               "neutral", "cancelled", "skipped", "timed_out",
               "action_required", "unknown")


class ChatStoreError(Exception):
    """Typed chat-store failure (callers surface ``code``/``detail``)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _bounded(text: Any, limit: int) -> str:
    return str(text or "")[:limit]


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text

        return redact_text(str(text if text is not None else ""))
    except Exception:
        return str(text if text is not None else "")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name,
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


@contextlib.contextmanager
def _file_lock(path: Path):
    """Serialise writers of ``path`` across processes (best-effort)."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _validate_session_id(session_id: str) -> str:
    sid = _bounded(session_id, MAX_SESSION_CHARS).strip()
    if not sid:
        raise ChatStoreError("CHAT_STORE_BAD_INPUT", "session_id is required")
    return sid


def _validate_path(path: str) -> str:
    text = str(path or "")
    if not text.strip():
        raise ChatStoreError("CHAT_STORE_BAD_PATH", "a file path is required")
    if "\x00" in text:
        raise ChatStoreError("CHAT_STORE_BAD_PATH",
                             "file path may not contain NUL")
    if len(text) > MAX_PATH_CHARS:
        raise ChatStoreError(
            "CHAT_STORE_BAD_PATH",
            f"file path exceeds {MAX_PATH_CHARS} chars")
    return text


def _check_session(session_check: Optional[Callable[[str], Any]],
                   session_id: str) -> None:
    """Refuse an orphan row when a canonical session checker is supplied."""
    if session_check is None:
        return
    try:
        result = session_check(session_id)
    except ChatStoreError:
        raise
    except Exception as exc:
        raise ChatStoreError("CHAT_STORE_SESSION_CHECK_FAILED",
                             f"session check failed: {exc}")
    ok = result if isinstance(result, bool) else (
        isinstance(result, dict) and result.get("state") == "OK")
    if not ok:
        raise ChatStoreError("CHAT_STORE_SESSION_NOT_FOUND",
                             f"no chat session {session_id!r}")


class _JsonStore:
    """Shared bounded-JSON store base: RLock + file lock + atomic flush."""

    _file = ""
    _schema = ""

    def __init__(self, state_dir: Any, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._dir = Path(state_dir)
        self._path = self._dir / self._file
        self._clock = clock
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = self._blank()

    def _blank(self) -> Dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def reload(self) -> Dict[str, Any]:
        with self._lock:
            data = self._blank()
            if self._path.is_file():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and raw.get("schema") == self._schema:
                        for key, default in data.items():
                            if key in raw and isinstance(raw[key], type(default)):
                                data[key] = raw[key]
                except (OSError, ValueError):
                    data = self._blank()
            self._state = data
            return {"state": "OK"}

    def _flush(self) -> None:
        _atomic_write(self._path, json.dumps(self._state, ensure_ascii=False))

    @contextlib.contextmanager
    def _exclusive(self):
        """One writer: hold the process lock + file lock across read-modify-write."""
        with self._lock:
            with _file_lock(self._path):
                self.reload()
                yield

    def _next_id(self) -> int:
        self._state["seq"] = int(self._state.get("seq", 0)) + 1
        return self._state["seq"]


class FileDraftStore(_JsonStore):
    """Per-session unsaved file drafts (one record per ``(session, path)``)."""

    _file = DRAFT_FILE
    _schema = DRAFT_SCHEMA

    def _blank(self) -> Dict[str, Any]:
        return {"schema": DRAFT_SCHEMA, "drafts": {}, "seq": 0}

    def _session_drafts(self, session_id: str) -> Dict[str, Any]:
        bucket = self._state["drafts"].setdefault(session_id, {})
        if not isinstance(bucket, dict):
            bucket = {}
            self._state["drafts"][session_id] = bucket
        return bucket

    @staticmethod
    def _public(rec: Dict[str, Any], *, include_content: bool = True
                ) -> Dict[str, Any]:
        out = {
            "id": rec.get("id", ""),
            "session_id": rec.get("session_id", ""),
            "path": _redact(rec.get("path", "")),
            "bytes": int(rec.get("bytes", 0) or 0),
            "sha256": rec.get("sha256", ""),
            "base_sha256": rec.get("base_sha256", ""),
            "actor": _redact(rec.get("actor", "")),
            "created_at": rec.get("created_at", 0),
            "updated_at": rec.get("updated_at", 0),
        }
        if include_content:
            out["content"] = rec.get("content", "")
        return out

    def put(self, session_id: str, path: str, content: str, *,
            base_sha256: str = "", actor: str = "dashboard:user",
            session_check: Optional[Callable[[str], Any]] = None
            ) -> Dict[str, Any]:
        """Create or update one draft; returns the stored record."""
        sid = _validate_session_id(session_id)
        clean_path = _validate_path(path)
        text = str(content or "")
        if len(text) > MAX_DRAFT_CHARS:
            raise ChatStoreError(
                "CHAT_STORE_TOO_LARGE",
                f"draft is {len(text)} chars (limit {MAX_DRAFT_CHARS})")
        _check_session(session_check, sid)
        with self._exclusive():
            bucket = self._session_drafts(sid)
            now = self._clock()
            prior = bucket.get(clean_path)
            rec = {
                "id": (prior or {}).get("id") or uuid.uuid4().hex[:12],
                "session_id": sid,
                "path": clean_path,
                "content": text,
                "bytes": len(text.encode("utf-8", "surrogatepass")),
                "sha256": _sha256(text),
                "base_sha256": _bounded(base_sha256, 64),
                "actor": _bounded(actor, MAX_ACTOR_CHARS),
                "created_at": (prior or {}).get("created_at", now),
                "updated_at": now,
            }
            existed = prior is not None
            bucket[clean_path] = rec
            self._flush()
            return {"state": "OK",
                    "code": "DRAFT_SAVED" if existed else "DRAFT_CREATED",
                    "draft": self._public(rec)}

    def get(self, session_id: str, path: str) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        clean_path = _validate_path(path)
        with self._lock:
            self.reload()
            rec = self._state["drafts"].get(sid, {}).get(clean_path)
        if not isinstance(rec, dict):
            return {"state": "MISSING", "code": "DRAFT_NOT_FOUND",
                    "detail": f"no draft for {clean_path!r}"}
        return {"state": "OK", "draft": self._public(rec)}

    def list(self, session_id: str, *, limit: int = 100, offset: int = 0,
             include_content: bool = True) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        with self._lock:
            self.reload()
            rows = list((self._state["drafts"].get(sid) or {}).values())
        rows.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
        start = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        page = rows[start:start + limit]
        return {"state": "OK", "session_id": sid,
                "drafts": [self._public(r, include_content=include_content)
                           for r in page],
                "total": len(rows), "limit": limit, "offset": start}

    def delete(self, session_id: str, path: str, *, confirmed: bool = False
               ) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        clean_path = _validate_path(path)
        if not confirmed:
            return {"state": "CONFIRM_REQUIRED",
                    "code": "DRAFT_CONFIRM_REQUIRED",
                    "detail": f"deleting draft {clean_path!r} needs confirm"}
        with self._exclusive():
            bucket = self._state["drafts"].get(sid, {})
            if clean_path not in bucket:
                return {"state": "MISSING", "code": "DRAFT_NOT_FOUND",
                        "detail": f"no draft for {clean_path!r}"}
            removed = bucket.pop(clean_path)
            self._flush()
            return {"state": "OK", "code": "DRAFT_DELETED",
                    "draft": self._public(removed, include_content=False)}

    def clear_session(self, session_id: str, *, confirmed: bool = False
                      ) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        with self._exclusive():
            bucket = self._state["drafts"].get(sid, {})
            if not confirmed:
                return {"state": "CONFIRM_REQUIRED",
                        "code": "DRAFT_CONFIRM_REQUIRED",
                        "detail": f"clearing drafts for {sid!r} needs confirm",
                        "would_remove": len(bucket)}
            removed = len(bucket)
            self._state["drafts"].pop(sid, None)
            self._flush()
            return {"state": "OK", "code": "DRAFTS_CLEARED",
                    "session_id": sid, "removed": removed}

    def export(self, session_id: str) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        with self._lock:
            self.reload()
            rows = list((self._state["drafts"].get(sid) or {}).values())
        return {"state": "OK", "schema": DRAFT_SCHEMA, "session_id": sid,
                "exported_at": self._clock(),
                "drafts": [self._public(r) for r in rows]}


class CommentStore(_JsonStore):
    """Per-session operator comments and structured CI-detail rows."""

    _file = COMMENT_FILE
    _schema = COMMENT_SCHEMA

    def _blank(self) -> Dict[str, Any]:
        return {"schema": COMMENT_SCHEMA, "comments": {}, "ci": {}, "seq": 0}

    @staticmethod
    def _public_comment(rec: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": rec.get("id"), "session_id": rec.get("session_id", ""),
                "body": _redact(rec.get("body", "")),
                "author": _redact(rec.get("author", "")),
                "target": _redact(rec.get("target", "")),
                "at": rec.get("at", 0),
                "meta": rec.get("meta", {})}

    @staticmethod
    def _public_ci(rec: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": rec.get("id"), "session_id": rec.get("session_id", ""),
                "check": _redact(rec.get("check", "")),
                "status": rec.get("status", "unknown"),
                "detail": _redact(rec.get("detail", "")),
                "url": _redact(rec.get("url", "")),
                "ref": _redact(rec.get("ref", "")),
                "at": rec.get("at", 0),
                "meta": rec.get("meta", {})}

    def add_comment(self, session_id: str, body: str, *,
                    author: str = "dashboard:user", target: str = "",
                    meta: Optional[Dict[str, Any]] = None,
                    session_check: Optional[Callable[[str], Any]] = None
                    ) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        text = str(body or "").strip()
        if not text:
            raise ChatStoreError("COMMENT_EMPTY", "comment body is required")
        if len(text) > MAX_COMMENT_CHARS:
            raise ChatStoreError(
                "CHAT_STORE_TOO_LARGE",
                f"comment exceeds {MAX_COMMENT_CHARS} chars")
        _check_session(session_check, sid)
        with self._exclusive():
            rows = self._state["comments"].setdefault(sid, [])
            rec = {"id": self._next_id(), "session_id": sid, "body": text,
                   "author": _bounded(author, MAX_ACTOR_CHARS),
                   "target": _bounded(target, MAX_PATH_CHARS),
                   "at": self._clock(),
                   "meta": meta if isinstance(meta, dict) else {}}
            rows.append(rec)
            del rows[:-MAX_ROWS]
            self._flush()
            return {"state": "OK", "code": "COMMENT_ADDED",
                    "comment": self._public_comment(rec)}

    def list_comments(self, session_id: str, *, limit: int = 100,
                      offset: int = 0, target: str = "") -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        with self._lock:
            self.reload()
            rows = list(self._state["comments"].get(sid, []))
        if target:
            rows = [r for r in rows if r.get("target") == target]
        start = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        return {"state": "OK", "session_id": sid,
                "comments": [self._public_comment(r)
                             for r in rows[start:start + limit]],
                "total": len(rows), "limit": limit, "offset": start}

    def delete_comment(self, session_id: str, comment_id: int, *,
                       confirmed: bool = False) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        if not confirmed:
            return {"state": "CONFIRM_REQUIRED",
                    "code": "COMMENT_CONFIRM_REQUIRED",
                    "detail": "deleting a comment needs confirm"}
        with self._exclusive():
            rows = self._state["comments"].get(sid, [])
            for index, rec in enumerate(rows):
                if rec.get("id") == comment_id:
                    rows.pop(index)
                    self._flush()
                    return {"state": "OK", "code": "COMMENT_DELETED",
                            "comment": self._public_comment(rec)}
            return {"state": "MISSING", "code": "COMMENT_NOT_FOUND",
                    "detail": f"no comment {comment_id!r} in {sid!r}"}

    def add_ci_detail(self, session_id: str, *, check: str,
                      status: str = "unknown", detail: str = "",
                      url: str = "", ref: str = "",
                      meta: Optional[Dict[str, Any]] = None,
                      session_check: Optional[Callable[[str], Any]] = None
                      ) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        name = str(check or "").strip()
        if not name:
            raise ChatStoreError("CI_CHECK_EMPTY", "CI check name is required")
        state = str(status or "unknown").strip().lower()
        if state not in CI_STATUSES:
            raise ChatStoreError(
                "CI_STATUS_INVALID",
                f"status must be one of {', '.join(CI_STATUSES)}")
        clean_detail = str(detail or "")
        if len(clean_detail) > MAX_CI_DETAIL_CHARS:
            raise ChatStoreError(
                "CHAT_STORE_TOO_LARGE",
                f"CI detail exceeds {MAX_CI_DETAIL_CHARS} chars")
        _check_session(session_check, sid)
        with self._exclusive():
            rows = self._state["ci"].setdefault(sid, [])
            rec = {"id": self._next_id(), "session_id": sid,
                   "check": _bounded(name, MAX_PATH_CHARS), "status": state,
                   "detail": clean_detail, "url": _bounded(url, MAX_PATH_CHARS),
                   "ref": _bounded(ref, MAX_PATH_CHARS), "at": self._clock(),
                   "meta": meta if isinstance(meta, dict) else {}}
            rows.append(rec)
            del rows[:-MAX_ROWS]
            self._flush()
            return {"state": "OK", "code": "CI_DETAIL_ADDED",
                    "ci": self._public_ci(rec)}

    def list_ci_details(self, session_id: str, *, limit: int = 100,
                        offset: int = 0, check: str = "") -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        with self._lock:
            self.reload()
            rows = list(self._state["ci"].get(sid, []))
        if check:
            rows = [r for r in rows if r.get("check") == check]
        start = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        return {"state": "OK", "session_id": sid,
                "ci": [self._public_ci(r) for r in rows[start:start + limit]],
                "total": len(rows), "limit": limit, "offset": start}

    def clear_session(self, session_id: str, *, confirmed: bool = False
                      ) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        with self._exclusive():
            comments = self._state["comments"].get(sid, [])
            ci = self._state["ci"].get(sid, [])
            if not confirmed:
                return {"state": "CONFIRM_REQUIRED",
                        "code": "COMMENT_CONFIRM_REQUIRED",
                        "detail": f"clearing comments for {sid!r} needs confirm",
                        "would_remove_comments": len(comments),
                        "would_remove_ci": len(ci)}
            removed = (len(comments), len(ci))
            self._state["comments"].pop(sid, None)
            self._state["ci"].pop(sid, None)
            self._flush()
            return {"state": "OK", "code": "COMMENTS_CLEARED",
                    "session_id": sid, "removed_comments": removed[0],
                    "removed_ci": removed[1]}

    def export(self, session_id: str) -> Dict[str, Any]:
        sid = _validate_session_id(session_id)
        with self._lock:
            self.reload()
            comments = list(self._state["comments"].get(sid, []))
            ci = list(self._state["ci"].get(sid, []))
        return {"state": "OK", "schema": COMMENT_SCHEMA, "session_id": sid,
                "exported_at": self._clock(),
                "comments": [self._public_comment(r) for r in comments],
                "ci": [self._public_ci(r) for r in ci]}


def draft_store(cfg: Any) -> FileDraftStore:
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    st = FileDraftStore(Path(state_dir))
    st.reload()
    return st


def comment_store(cfg: Any) -> CommentStore:
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    st = CommentStore(Path(state_dir))
    st.reload()
    return st
