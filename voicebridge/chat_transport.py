"""Qubit-safe chat transport for the dashboard.

The vendored Qubit Chat tab drives a raw ``/api/pty`` WebSocket (an xterm
mirror of ``qubit --tui``). Qubit shell policy forbids a raw shell/PTY spawn,
so that path is a typed ``PTY_NO_SHELL`` refusal and the page reconnect-loops.
This module is the Qubit-safe equivalent: an agent-backed chat transport on the
SAME backend every other surface uses — :func:`voicebridge.agent.turn` /
``voicebridge.local_llm`` against the resident ``qubit-brain4`` model.

It provides, with no second listener and no shell:

* a persistent chat-session store (create/list/show/rename/delete/export/
  import + reload) under the Qubit state dir;
* a typed turn API that persists real user/assistant messages and gives the
  model bounded prior-conversation context;
* a streaming turn generator (real sentence deltas from
  ``local_llm.chat_local_stream``), cancellation, and typed error/retry;
* a PTY-shaped, agent-backed terminal session (``AgentPtySession``) the xterm
  UI can be rewired to without any shell.

Every entry point fails closed: emergency stop, disabled agent, missing model,
empty model output, and malformed input are typed envelopes — never fabricated
success. Nothing here reads or writes a PTY.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

SCHEMA = "qubit-chat/1"
SESSION_SCHEMA = "qubit-chat-session/1"
DEFAULT_SESSION = "dashboard-chat"
DEFAULT_MODEL = "qubit-brain4"
MAX_TEXT_CHARS = 8000
MAX_NAME_CHARS = 120
MAX_MESSAGES = 5000
CONTEXT_MESSAGES = 12
CONTEXT_CHARS = 4000
STORE_FILE = "chat_sessions.json"

# One cancellation authority per session: a turn registers a stop-event that
# ``cancel()`` sets; the streaming path checks it (and the caller-supplied
# ``cancel`` callable) before every emitted delta.
_cancel_lock = threading.Lock()
_cancel_events: Dict[str, threading.Event] = {}

# Live PTY-shaped sessions (line buffer + cursor state) keyed by session id.
# The xterm UI sends keystrokes one at a time, so a frame handler that built a
# fresh buffer per request could never assemble a line. The session persists in
# this process only; nothing on disk and no OS pty is involved.
_pty_lock = threading.Lock()
_pty_sessions: Dict[str, "AgentPtySession"] = {}
_PTY_MAX = 256


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
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


def _bounded(text: Any, limit: int) -> str:
    return str(text or "")[:limit]


class ChatSessionStore:
    """Persistent chat sessions + messages (Qubit-owned, 0600, atomic)."""

    def __init__(self, state_dir: Any, *, clock: Callable[[], float] = time.time) -> None:
        self._dir = Path(state_dir)
        self._path = self._dir / STORE_FILE
        self._clock = clock
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = self._blank()

    @staticmethod
    def _blank() -> Dict[str, Any]:
        return {"schema": SESSION_SCHEMA, "sessions": {}, "messages": {},
                "tombstones": {}}

    # -- io ----------------------------------------------------------------
    def reload(self) -> Dict[str, Any]:
        """Re-read the store from disk (idempotent; safe on missing/corrupt)."""
        with self._lock:
            data = self._blank()
            if self._path.is_file():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and raw.get("schema") == SESSION_SCHEMA:
                        for key in ("sessions", "messages", "tombstones"):
                            if isinstance(raw.get(key), dict):
                                data[key] = raw[key]
                except (OSError, ValueError):
                    data = self._blank()
            self._state = data
            return {"state": "OK", "count": len(data["sessions"])}

    def _flush(self) -> None:
        _atomic_write(self._path, json.dumps(self._state, ensure_ascii=False))

    def _record(self, session_id: str) -> Optional[Dict[str, Any]]:
        rec = self._state["sessions"].get(session_id)
        return rec if isinstance(rec, dict) else None

    # -- lifecycle ---------------------------------------------------------
    def create(self, name: str = "", *, session_id: Optional[str] = None,
               model: Optional[str] = None,
               identity: str = "dashboard:user",
               created_at: Optional[float] = None,
               updated_at: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            sid = _bounded(session_id, 80).strip() if session_id else ""
            if not sid:
                sid = f"chat-{uuid.uuid4().hex[:12]}"
            if self._record(sid) is not None:
                return {"state": "EXISTS", "code": "SESSION_EXISTS",
                        "detail": f"session {sid!r} already exists"}
            now = self._clock()
            created = (float(created_at)
                       if isinstance(created_at, (int, float)) and created_at > 0
                       else now)
            updated = (float(updated_at)
                       if isinstance(updated_at, (int, float)) and updated_at > 0
                       else created)
            rec = {"id": sid, "name": _bounded(name, MAX_NAME_CHARS) or None,
                   "model": model or None, "created_at": created,
                   "updated_at": updated,
                   "archived": False, "message_count": 0, "identity": identity}
            self._state["sessions"][sid] = rec
            self._state["messages"][sid] = []
            self._state["tombstones"].pop(sid, None)
            self._flush()
            return {"state": "OK", "session": dict(rec), "messages": []}

    def ensure(self, session_id: str, *, model: Optional[str] = None,
               identity: str = "dashboard:user") -> Dict[str, Any]:
        sid = _bounded(session_id, 80).strip() or DEFAULT_SESSION
        rec = self._record(sid)
        if rec is None:
            return self.create("", session_id=sid, model=model, identity=identity)
        return {"state": "OK", "session": dict(rec),
                "messages": list(self._state["messages"].get(sid, []))}

    def list(self, *, limit: int = 50, offset: int = 0, query: str = "",
             include_archived: bool = True) -> Dict[str, Any]:
        with self._lock:
            rows = []
            needle = (query or "").strip().lower()
            for sid, rec in self._state["sessions"].items():
                if not include_archived and rec.get("archived"):
                    continue
                msgs = self._state["messages"].get(sid, []) or []
                preview = ""
                for m in reversed(msgs):
                    if m.get("role") == "assistant" and m.get("content"):
                        preview = str(m["content"])[:200]
                        break
                hay = f"{rec.get('name') or ''} {preview}".lower()
                if needle and needle not in hay:
                    continue
                rows.append({
                    "id": sid, "source": "qubit-chat", "model": rec.get("model"),
                    "title": rec.get("name") or (preview[:80] or sid),
                    "name": rec.get("name"), "archived": bool(rec.get("archived")),
                    "started_at": rec.get("created_at", 0),
                    "ended_at": None, "last_active": rec.get("updated_at", 0),
                    "is_active": False,
                    "message_count": int(rec.get("message_count", 0) or 0),
                    "tool_call_count": 0, "input_tokens": 0, "output_tokens": 0,
                    "preview": preview or None})
            rows.sort(key=lambda r: r.get("last_active") or 0, reverse=True)
            total = len(rows)
            start = max(0, int(offset))
            limit = max(1, min(int(limit), 100))
            return {"state": "OK", "sessions": rows[start:start + limit],
                    "total": total, "limit": limit, "offset": start}

    def show(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._record(_bounded(session_id, 80))
            if rec is None:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no chat session {session_id!r}"}
            msgs = list(self._state["messages"].get(rec["id"], []))
            return {"state": "OK", "session": dict(rec), "messages": msgs,
                    "message_count": len(msgs)}

    def rename(self, session_id: str, name: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._record(_bounded(session_id, 80))
            if rec is None:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no chat session {session_id!r}"}
            clean = _bounded(name, MAX_NAME_CHARS).strip()
            if not clean:
                return {"state": "FAILED", "code": "MALFORMED",
                        "detail": "rename needs a non-empty name"}
            rec["name"] = clean
            rec["updated_at"] = self._clock()
            self._flush()
            return {"state": "OK", "session": dict(rec)}

    def archive(self, session_id: str, archived: bool = True) -> Dict[str, Any]:
        with self._lock:
            rec = self._record(_bounded(session_id, 80))
            if rec is None:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no chat session {session_id!r}"}
            rec["archived"] = bool(archived)
            rec["updated_at"] = self._clock()
            self._flush()
            return {"state": "OK", "session": dict(rec)}

    def delete(self, session_id: str, *, confirm: bool = False) -> Dict[str, Any]:
        with self._lock:
            sid = _bounded(session_id, 80)
            rec = self._record(sid)
            if rec is None:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no chat session {sid!r}"}
            if not confirm:
                return {"state": "CONFIRM_REQUIRED",
                        "code": "CONFIRM_REQUIRED",
                        "detail": f"deleting chat session {sid!r} needs confirm=true",
                        "session": dict(rec)}
            rec["archived"] = True
            rec["deleted"] = True
            rec["updated_at"] = self._clock()
            self._state["tombstones"][sid] = {"deleted_at": self._clock()}
            self._state["sessions"].pop(sid, None)
            self._flush()
            return {"state": "OK", "session": dict(rec)}

    def restore(self, session_id: str) -> Dict[str, Any]:
        """Recover a deleted (tombstoned) session if its record survives."""
        with self._lock:
            sid = _bounded(session_id, 80)
            if sid not in self._state["tombstones"]:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no deleted chat session {sid!r}"}
            # A delete removes the live record; only a reload that still holds
            # it can restore. Rebuild a minimal record when unrecoverable.
            return {"state": "FAILED", "code": "SESSION_UNRECOVERABLE",
                    "detail": "chat session delete is a tombstone; export first "
                              "to keep a restorable copy"}

    def append(self, session_id: str, role: str, content: str, *,
               meta: Optional[Dict[str, Any]] = None,
               model: Optional[str] = None,
               timestamp: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            sid = _bounded(session_id, 80)
            rec = self._record(sid)
            if rec is None:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no chat session {sid!r}"}
            msgs = self._state["messages"].setdefault(sid, [])
            ts = (float(timestamp)
                  if isinstance(timestamp, (int, float)) and timestamp > 0
                  else self._clock())
            msg = {"role": str(role), "content": _bounded(content, MAX_TEXT_CHARS),
                   "timestamp": ts}
            if meta:
                msg["meta"] = {k: v for k, v in meta.items() if v is not None}
            if model:
                rec["model"] = model
            msgs.append(msg)
            if len(msgs) > MAX_MESSAGES:
                del msgs[:len(msgs) - MAX_MESSAGES]
            rec["message_count"] = len(msgs)
            rec["updated_at"] = self._clock()
            self._flush()
            return {"state": "OK", "message": msg, "message_count": len(msgs)}

    def messages(self, session_id: str, *, limit: int = 500,
                 offset: int = 0) -> Dict[str, Any]:
        with self._lock:
            rec = self._record(_bounded(session_id, 80))
            if rec is None:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no chat session {session_id!r}"}
            msgs = list(self._state["messages"].get(rec["id"], []))
            start = max(0, int(offset))
            limit = max(1, min(int(limit), 1000))
            return {"state": "OK", "session_id": rec["id"],
                    "messages": msgs[start:start + limit], "total": len(msgs),
                    "limit": limit, "offset": start}

    def export(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._record(_bounded(session_id, 80))
            if rec is None:
                return {"state": "MISSING", "code": "SESSION_NOT_FOUND",
                        "detail": f"no chat session {session_id!r}"}
            return {"state": "OK", "schema": SESSION_SCHEMA,
                    "exported_at": self._clock(), "session": dict(rec),
                    "messages": list(self._state["messages"].get(rec["id"], []))}

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
        name = _bounded(src.get("name", ""), MAX_NAME_CHARS)
        sid = _bounded(src.get("id", ""), 80).strip()
        with self._lock:
            if sid and self._record(sid) is not None and not overwrite:
                return {"state": "EXISTS", "code": "SESSION_EXISTS",
                        "detail": f"session {sid!r} already exists"}
            created = self.create(name, session_id=sid or None,
                                  model=src.get("model"), identity=identity,
                                  created_at=src.get("created_at"),
                                  updated_at=src.get("updated_at"))
            if created.get("state") == "EXISTS" and overwrite:
                self._state["sessions"].pop(sid, None)
                self._state["messages"].pop(sid, None)
                created = self.create(name, session_id=sid, model=src.get("model"),
                                      identity=identity,
                                      created_at=src.get("created_at"),
                                      updated_at=src.get("updated_at"))
            if created.get("state") != "OK":
                return created
            real = created["session"]["id"]
            incoming = payload.get("messages")
            if not isinstance(incoming, list):
                incoming = src.get("messages") if isinstance(src.get("messages"), list) else []
            count = 0
            for m in incoming[:MAX_MESSAGES]:
                if not isinstance(m, dict) or m.get("role") not in (
                        "user", "assistant", "system"):
                    continue
                self.append(real, m.get("role"), m.get("content", ""),
                            meta=m.get("meta") if isinstance(m.get("meta"), dict) else None,
                            timestamp=m.get("timestamp"))
                count += 1
            return {"state": "OK", "session": self._record(real),
                    "imported": count}


def store(cfg: Any) -> Any:
    """Return the ONE canonical chat store (C7).

    The authoritative store is the vendored ``qubit_state.SessionDB`` (the same
    SQLite ``state.db`` the terminal/TUI/agent writes) reached through the
    strict native-contract adapter :class:`QubitSessionStore`. There is no
    second writable chat store and no copy-sync.
    """
    from .qubit_session_store import QubitSessionStore

    return QubitSessionStore(cfg)


# ---------------------------------------------------------------------------
# Single session authority (A10)
# ---------------------------------------------------------------------------
#: The canonical authority class for chat transcripts. The backed store is the
#: vendored ``qubit_state.SessionDB`` (``state.db`` under the Qubit runtime
#: home); the adapter and the terminal write the SAME database.
SESSION_AUTHORITY = "voicebridge.qubit_session_store.QubitSessionStore"


def session_authority() -> Dict[str, Any]:
    """The one session-authority contract, read from its canonical owner.

    Chat session list/lifecycle read through exactly one authority:
    :class:`voicebridge.qubit_session_store.QubitSessionStore`, a strict
    adapter over the vendored ``qubit_state.SessionDB``. The terminal/TUI/agent
    and every native surface (``/qubit-chat``, CLI, dashboard, voice) read and
    write that one ``state.db``; no second writable chat store exists.
    """
    from . import auth_status

    return auth_status.session_authority()


def session_reasoning(cfg: Any, session_id: str) -> Optional[str]:
    """Session-persisted reasoning preference (None = model default).

    Reads the ``reasoning`` key from the session's ``model_config`` metadata
    (written via the session PATCH route, validated there). An unreadable or
    invalid stored value degrades to None (audited) so a corrupt preference
    can never brick a session; per-request values always win.
    """
    try:
        meta = store(cfg).meta_get(session_id).get("meta", {})
        level = (meta or {}).get("reasoning")
        if level is None:
            return None
        from .local_llm import normalize_reasoning_level

        return normalize_reasoning_level(level)
    except Exception as exc:  # noqa: BLE001 - preference is advisory
        try:
            _audit("reasoning_preference_ignored", session=session_id,
                   error=str(exc)[:120])
        except Exception:
            pass
        return None


def store_boundary() -> Dict[str, Any]:
    """The non-overlapping store-ownership contract (delegated, read-only)."""
    from . import auth_status

    return auth_status.store_boundary()


def owns_chat_session_authority() -> bool:
    """True when this module is still the named chat-transcript authority."""
    contract = session_authority().get("writable_authorities", {}) or {}
    return contract.get("chat_transcript") == SESSION_AUTHORITY


# ---------------------------------------------------------------------------
# Packet 8U projection adapters (S5) — delegated, never a second writer
# ---------------------------------------------------------------------------
def _bind_session_check(store_obj: Any, check: Callable[[str], Any],
                        *methods: str) -> Any:
    """Bind the canonical authority's ``show`` as each store's session guard.

    The S5 stores accept a per-call ``session_check``; binding it here means a
    draft/comment/CI row may only reference a session the single chat authority
    knows, and the S5 stores stay in their own files (never a second writer of
    ``chat_sessions.json``). An explicit per-call ``session_check`` still wins.
    """
    for name in methods:
        original = getattr(store_obj, name, None)
        if original is None:
            continue

        def _bound(*args: Any, _orig: Any = original, _check: Any = check,
                   **kwargs: Any) -> Any:
            kwargs.setdefault("session_check", _check)
            return _orig(*args, **kwargs)

        setattr(store_obj, name, _bound)
    return store_obj


def draft_store(cfg: Any) -> Any:
    """Per-session file-draft store, guarded by the chat session authority."""
    from . import chat_stores as CS

    return _bind_session_check(CS.draft_store(cfg), store(cfg).show, "put")


def comment_store(cfg: Any) -> Any:
    """Per-session comment/CI store, guarded by the chat session authority."""
    from . import chat_stores as CS

    return _bind_session_check(CS.comment_store(cfg), store(cfg).show,
                               "add_comment", "add_ci_detail")


def build_projection(cfg: Any, session_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Session activity projection for one session.

    The canonical chat store is injected as ``session_store`` so the summary
    section reads the same single transcript authority as the chat surface.
    """
    from . import session_activity as SA

    kwargs.setdefault("session_store", store(cfg))
    return SA.build_projection(cfg, session_id, **kwargs)


def record_activity(cfg: Any, session_id: str, assistant_text: str, *,
                    actor: str = "dashboard:user") -> Dict[str, Any]:
    """Append a ``summary`` activity event after a successful turn.

    Best-effort projection glue: the turn already succeeded, so a failure here
    is audited and typed but never fabricates or masks a turn result.
    """
    try:
        from . import session_activity as SA

        return SA.store(cfg).record(session_id, "summary",
                                    detail=assistant_text, actor=actor)
    except Exception as exc:  # noqa: BLE001 - typed, audited, non-fatal
        _audit("activity_error", session=session_id, error=str(exc))
        return {"state": "FAILED", "code": "SESSION_ACTIVITY_ERROR",
                "detail": _redact(str(exc))[:300]}


# ---------------------------------------------------------------------------
# Model routing / guards
# ---------------------------------------------------------------------------
def configured_model(cfg: Any) -> str:
    """Canonical default chat model (never a hard-coded alias).

    An explicit ``agent.model`` override wins; otherwise the ONE resolver answers
    the effective model for the chat role, honoring the operator-configured
    default (the reference's resolution: the ``model`` setting / Qubit runtime
    default, when it maps to a registered profile) before the shipped default.
    Because ``/api/model/info`` and the ``/qubit-chat`` turn path resolve through
    that same function, all surfaces report the same default (PV-02).
    """
    try:
        from .providers import resolver as _resolver

        configured = _resolver.configured_default_model(cfg)
        if configured:
            return configured
        return _resolver.resolve(cfg, role="chat").model
    except Exception:
        return DEFAULT_MODEL


def _pinned_session_model(cfg: Any, session: Optional[str]) -> Optional[str]:
    """The model recorded on a chat session, if any (the ``/chat`` authority).

    The vendored ``/chat`` terminal resumes one long-lived session and honours
    that session's stored model, and the ``/qubit-chat`` header
    (:class:`AgentPtySession.banner`) reads the same row. Returning it lets the
    resolver, the dashboard picker and the stream receipt agree on the ONE
    model the conversation actually uses instead of the configured default.
    Read-only and fail-safe: any error (including a store without ``show``)
    yields ``None`` so a broken store can never brick a turn.
    """
    try:
        sid = str(session or "").strip()[:80]
        if not sid:
            return None
        shown = store(cfg).show(sid)
        if not isinstance(shown, dict) or shown.get("state") != "OK":
            return None
        model = str((shown.get("session") or {}).get("model") or "").strip()
        return model or None
    except Exception:  # noqa: BLE001 - a preference, never fatal
        return None


def resolve_route(cfg: Any, requested: Optional[str] = None, *,
                  session: Optional[str] = None,
                  role: str = "chat") -> Dict[str, Any]:
    """Route a turn through the canonical Qubit model-profile resolver.

    Returns the effective profile detail (provider/model/kind/route/locality/
    executable) for the runtime path. The shipped default local profile
    (``qubit-brain4``) is used when nothing is requested. A ``requested`` value
    may be a registered profile id from the canonical registry (local or
    approved API) or the configured/default local model; anything else is a
    typed ``MODEL_NOT_ALLOWED`` refusal so a bad model can never be silently
    swallowed into a wrong-model turn.

    When no model is explicitly requested and the session already records a
    model (the ``/chat`` terminal's stored selection, which the ``/qubit-chat``
    header also shows), that recorded model is pinned onto the resolved
    external profile — the endpoint, credential and policy still come from the
    resolved profile. This keeps ``/chat``, ``/qubit-chat``, ``model/options``
    and ``model/info`` on one effective model.
    """
    from .providers import resolver as _resolver

    req = (requested or "").strip() or None
    routed = _resolver.resolve_model(cfg, req, role=role, session=session)
    if not routed.get("ok"):
        code = str(routed.get("code") or "MODEL_NOT_ALLOWED")
        # Preserve the historical refusal code for unknown models while
        # surfacing the resolver's precise code for policy/egress failures.
        if code == "UNKNOWN_PROFILE":
            code = "MODEL_NOT_ALLOWED"
        if code == "MODEL_NOT_ALLOWED":
            detail = (
                f"model {requested!r} is not a registered Qubit model profile "
                f"and not the configured brain ({configured_model(cfg)!r}); "
                "refusing to route elsewhere")
        else:
            detail = routed.get("detail") or (
                f"model {requested!r} refused: {code}")
        return {"ok": False, "code": code, "detail": detail}
    if req is None:
        pinned = _pinned_session_model(cfg, session)
        if (pinned and pinned != DEFAULT_MODEL
                and pinned != routed.get("model")
                and str(routed.get("kind") or "") in
                ("api", "realtime", "plugin")):
            routed = dict(routed)
            routed["model"] = pinned
            routed["session_pinned_model"] = pinned
    return routed


def resolve_model(cfg: Any, requested: Optional[str] = None) -> Dict[str, Any]:
    """Backward-compatible minimal resolution ``{"ok", "model"}``.

    Uses the same canonical resolver as :func:`resolve_route`; the runtime
    path uses :func:`resolve_route` for provider/route detail.
    """
    routed = resolve_route(cfg, requested)
    if not routed.get("ok"):
        return routed
    return {"ok": True, "model": routed["model"]}


def _estop(cfg: Any) -> bool:
    try:
        from .broker import estop_engaged
        return bool(estop_engaged(cfg))
    except Exception:
        try:
            from . import power as PW
            return bool(PW.estop_engaged(cfg))
        except Exception:
            return True


def _agent_enabled(cfg: Any) -> bool:
    try:
        return bool(cfg.get("agent", "enabled", default=True))
    except Exception:
        return True


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text
        return redact_text(str(text))
    except Exception:
        return str(text)


def _sanitize(text: str) -> str:
    try:
        from .textutil import sanitize
        return sanitize(text)
    except Exception:
        return text


def _audit(op: str, **fields: Any) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"chat_transport.{op}",
                  {k: _redact(v)[:160] if isinstance(v, str) else v
                   for k, v in fields.items()})
    except Exception:
        pass


def _resolve_session(st: ChatSessionStore, session: Optional[str], *,
                     model: Optional[str]) -> Dict[str, Any]:
    sid = str(session or DEFAULT_SESSION).strip()[:80] or DEFAULT_SESSION
    return st.ensure(sid, model=model)


#: How many transcript rows are read to build/compact context. Larger than the
#: fed window so older turns can still contribute a deterministic summary.
_CONTEXT_READ_LIMIT = 200


def _context_prompt(st: ChatSessionStore, session_id: str,
                    exclude_last: bool = False, *,
                    cfg: Any = None,
                    model_context_tokens: Optional[int] = None,
                    pending_text: str = ""
                    ) -> "tuple[str, dict, Any]":
    """Bounded, dynamic context for prior turns.

    Returns ``(text, meta, decision)``. The policy picks a fast window for a
    simple voice turn and expands (with a deterministic summary of older turns)
    when history/context requires it. Instruction/system messages are always
    retained. ``cfg`` is optional for callers that only want the legacy string
    shape (a Config-less call falls back to the policy defaults).

    ``pending_text`` is the in-flight user turn. It is counted by the policy so
    a single large request still triggers expansion/escalation, but it is NOT
    rendered into the context block (the caller appends it separately).
    """
    from . import context_policy as CTX

    msgs = st.messages(session_id, limit=_CONTEXT_READ_LIMIT).get("messages", [])
    if exclude_last and msgs:
        msgs = msgs[:-1]
    decision_msgs = list(msgs)
    if pending_text:
        decision_msgs = decision_msgs + [{"role": "user",
                                          "content": pending_text}]
    decision = CTX.decide_context(cfg, decision_msgs,
                                  model_context_tokens=model_context_tokens)
    text, meta = CTX.build_context_prompt(msgs, decision)
    return text, meta, decision


def _escalation_route(cfg: Any, decision: Any,
                      routed: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the escalation target for a decision, or a typed refusal.

    Escalation is opt-in: with no ``agent.context.escalation_model`` configured
    there is no target and the caller fails closed with a typed code rather
    than silently truncating or switching to an unapproved provider. When a
    target is configured it resolves through the same canonical resolver, so
    egress/policy/credential checks still apply.
    """
    from . import context_policy as CTX

    pinned = CTX.escalation_target(cfg, routed)
    if not pinned:
        return {"ok": False, "code": "CONTEXT_ESCALATION_UNAVAILABLE",
                "detail": "the request needs a larger context window than the "
                          "local model provides; configure "
                          "agent.context.escalation_model or raise "
                          "agent.context.max_tokens (no silent fallback)"}
    escalated = resolve_route(cfg, pinned, session=None, role="chat")
    if not escalated.get("ok"):
        return {"ok": False, "code": "CONTEXT_ESCALATION_DENIED",
                "detail": f"escalation profile {pinned!r} is not executable: "
                          f"{escalated.get('detail') or escalated.get('code')}"}
    return escalated


def _cancel_event(session_id: str) -> threading.Event:
    with _cancel_lock:
        ev = _cancel_events.get(session_id)
        if ev is None:
            ev = threading.Event()
            _cancel_events[session_id] = ev
        return ev


def cancel(session_id: str) -> Dict[str, Any]:
    """Signal cancellation for the in-flight turn on ``session_id``."""
    with _cancel_lock:
        ev = _cancel_events.get(str(session_id or "").strip())
    if ev is None:
        return {"ok": False, "code": "NO_ACTIVE_TURN",
                "detail": f"no in-flight chat turn for {session_id!r}"}
    ev.set()
    _audit("cancel", session=session_id)
    return {"ok": True, "session_id": session_id, "cancelled": True}


def _release_cancel(session_id: str, ev: threading.Event) -> None:
    with _cancel_lock:
        if _cancel_events.get(session_id) is ev:
            _cancel_events.pop(session_id, None)


# ---------------------------------------------------------------------------
# Shared turn decision (Wave A Task 1 — classify once, no behavior change yet)
# ---------------------------------------------------------------------------
def _intent_cfg(cfg: Any) -> Dict[str, Any]:
    """Plain-dict ``intent`` section for the shared classifier.

    Tests pass a plain dict (plan behavior: ``cfg.get("intent", {})``);
    production passes the Config object, whose ``section("intent")`` is the
    same mapping the voice orchestrator builds its own Classifier from.
    """
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        sub = cfg.get("intent", {})
        return dict(sub) if isinstance(sub, dict) else {}
    section = getattr(cfg, "section", None)
    if callable(section):
        try:
            sub = section("intent")
            return dict(sub) if isinstance(sub, dict) else {}
        except Exception:  # noqa: BLE001 - degraded to defaults, never fatal
            return {}
    return {}


def decide_turn_action(text: Any, *, cfg: Any = None,
                       intent: Any = None) -> Dict[str, Any]:
    """Pure classification: map text -> one action dict. No side effects.

    The single classification both the ``/qubit-chat`` turn path and the
    voice orchestrator's ``_route_intent`` use (log-only there for now):
    classify once through the same
    :class:`voicebridge.intent.classifier.Classifier`, then project the
    Intent kind onto the turn action later waves execute
    (``answer_local`` / ``web_search`` / ``local_action`` / ``registry`` /
    ``agent_task`` / ``clarify`` / ``refuse``).

    Never touches a store, the broker, or a model directly; an ambiguous
    input may consult the locally-configured classifier LLM exactly as the
    voice path already does for the same utterance (localhost only).
    """
    from .intent.classifier import Classifier
    from .intent.state import ConfirmationState, ConversationState

    raw = str(text or "")
    if not raw.strip():
        return {"action": "clarify", "kind": "clarify", "task_text": raw,
                "params": {}, "reason": "empty turn"}
    if intent is None:
        clf = Classifier(_intent_cfg(cfg), ConfirmationState(),
                         ConversationState())
        intent = clf.classify(raw)
    low = raw.lower()
    reason = str(getattr(intent, "reason", None) or "")
    task_text = str(getattr(intent, "task_text", None) or raw)
    params = getattr(intent, "params", None) or {}
    if not isinstance(params, dict):
        params = {}
    kind = str(getattr(intent, "kind", None) or "conversation")
    if kind == "local":
        return {"action": "local_action", "kind": kind,
                "task_text": task_text, "params": dict(params),
                "reason": reason}
    if "perplexity" in low or reason.startswith("weather/forecast"):
        return {"action": "web_search", "kind": "conversation",
                "task_text": task_text or raw, "params": {},
                "reason": reason}
    # Voice-registry match (Wave C Task 4): a concrete dashboard/voice
    # command ("open youtube in the browser", "close that tab") executes
    # through the shared registry instead of chattering or delegating to a
    # worker. ``resolve`` is a pure registry lookup — no side effects, so
    # this stays a pure classification. Never shadows the pinned
    # local/web branches above; never raises (a broken registry degrades
    # to the kind mapping below).
    try:
        from . import voice_commands as _VC

        _res = _VC.resolve(raw)
        if getattr(_res, "kind", None) == "match" and \
                getattr(_res, "entry", None) is not None:
            _eid = str(getattr(_res.entry, "id", "") or "")
            return {"action": "registry", "kind": kind,
                    "task_text": task_text or raw, "params": dict(params),
                    "reason": f"registry:{_eid}" if _eid else reason,
                    "entry": _eid}
    except Exception:  # noqa: BLE001 - degraded to kind mapping, never fatal
        pass
    if kind == "conversation":
        return {"action": "answer_local", "kind": kind,
                "task_text": task_text or raw, "params": {},
                "reason": reason}
    if kind in ("coding", "agent"):
        return {"action": "agent_task", "kind": kind,
                "task_text": task_text or raw, "params": dict(params),
                "reason": reason}
    if kind == "clarify":
        return {"action": "clarify", "kind": kind,
                "task_text": raw, "params": {}, "reason": reason}
    return {"action": "refuse", "kind": kind,
            "task_text": raw, "params": {}, "reason": reason}


# ---------------------------------------------------------------------------
# Wave C branches (Tasks 2-5) — execute inside turn()/stream_turn()
# ---------------------------------------------------------------------------
#: Actions that execute through a Wave C branch instead of the model path.
BRANCH_ACTIONS = ("web_search", "local_action", "registry", "agent_task")


def _cfg_flag(cfg: Any, *path: str, default: Any = True) -> Any:
    """Read one config flag without crashing on a plain-dict test cfg."""
    try:
        get = getattr(cfg, "get", None)
        if callable(get):
            return get(*path, default=default)
    except TypeError:
        try:
            node: Any = cfg
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            return default if node is None else node
        except Exception:  # noqa: BLE001 - degraded to default
            pass
    except Exception:  # noqa: BLE001 - degraded to default
        pass
    return default


def _summarizer_cfg(cfg: Any) -> Any:
    """Summarizer node for :func:`summarizer.summarize` (same as voice)."""
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        sub = cfg.get("summarizer", {})
        return dict(sub) if isinstance(sub, dict) else {}
    section = getattr(cfg, "section", None)
    if callable(section):
        try:
            return section("summarizer")
        except Exception:  # noqa: BLE001 - degraded to defaults
            return {}
    return {}


def _summarize_report(reply: str, cfg: Any) -> str:
    """Spoken summary of a worker report — bulletproof like the voice path.

    Copies the ``_handle_agent_result`` fallback chain: a summarizer failure
    clips the raw report instead of hiding it or fabricating a summary.
    """
    try:
        from .summarizer import summarize

        spoken = summarize(reply, _summarizer_cfg(cfg))
        if str(spoken or "").strip():
            return str(spoken).strip()
    except Exception as exc:  # noqa: BLE001 - clipped report, never hidden
        _audit("task_summarize_failed", error=str(exc))
    try:
        from .summarizer import clamp_words as _cw, _strip_markdown as _sm

        return _cw(_sm(reply or ""),
                   40) or "Task finished — check the full report on " \
                          "the dashboard."
    except Exception:  # noqa: BLE001 - last-resort literal, never empty
        return "Task finished — check the full report on the dashboard."


# ---------------------------------------------------------------------------
# Wave D2 Task 6 — uniform branch receipt shape {route, task, sources, model}
# ---------------------------------------------------------------------------
def _branch_model(cfg: Any) -> Optional[str]:
    """Best-effort surface default for branch receipts (never raises)."""
    try:
        return configured_model(cfg)
    except Exception:  # noqa: BLE001 - degraded to None, never fatal
        return None


def _branch_receipt(action: str, *, task: Any = None,
                    sources: Any = None, model: Any = None,
                    cfg: Any = None) -> Dict[str, Any]:
    """Uniform receipt shape for every branch turn.

    Every branch outcome carries ``{route, task, sources, model}`` (plus the
    runtime marker) so the transcript/audit lineage reads the same no matter
    which path executed — the ``/qubit-chat`` typed turn, the voice turn, or
    the stream variant. ``task`` is free-form user text, so it is redacted
    (a turn receipt must never carry a raw secret); ``model`` falls back to
    the surface default when the branch ran without one.
    """
    if model is None and cfg is not None:
        model = _branch_model(cfg)
    return {"runtime": "qubit-chat-branch",
            "path": "voicebridge.chat_transport",
            "route": str(action or ""),
            "task": _redact(task)[:200] if task else None,
            "sources": [str(s) for s in (sources or [])],
            "model": model}


# ---------------------------------------------------------------------------
# T4 — staged-confirm settle on affirmative follow-up
# ---------------------------------------------------------------------------
#: Pure-affirmation utterances that settle (never stage) when the session
#: holds a pending staged registry confirmation. Kept short/literal so a
#: longer message that merely contains "yes" never settles — only a
#: follow-up that IS an affirmation does. The settle runs through the SAME
#: shared journal path (``voice_commands.confirm_pending`` on the session's
#: latest pending record), never a fresh classify, never auto-execution.
_AFFIRMATIVE_PHRASES = frozenset({
    "yes", "yeah", "yep", "yup", "y",
    "confirm", "confirmed", "confirm it", "confirm yes", "yes confirm",
    "do it", "yes do it", "do it please", "please do", "please do it",
    "go ahead", "proceed", "ok", "okay", "sure", "yes please",
    "affirmative", "approved",
})


def _is_affirmative(text: Any) -> bool:
    """True when the turn text is a bare affirmation (nothing else)."""
    raw = str(text or "").strip().lower()
    if not raw:
        return False
    cleaned = re.sub(r"[^\w\s]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or len(cleaned) > 40:
        return False
    return cleaned in _AFFIRMATIVE_PHRASES


def _pending_registry(cfg: Any, sid: str) -> list:
    """Read-only pending staged registry confirmations for one chat session.

    Uses the existing shared-journal primitives only
    (``make_ctx``/``_journal``/``_pending_for`` — the same durable
    ``approvals.jsonl`` journal ``voice_say`` stages into and
    ``confirm_pending`` settles from). No new store, no write, never raises:
    an unreadable journal degrades to no pending so the turn falls through
    to its normal path.
    """
    try:
        from . import voice_commands as VC

        ctx = VC.make_ctx(cfg, actor="dashboard:user", session=sid,
                          source="user")
        journal = VC._journal(cfg)
        return list(VC._pending_for(ctx, journal))
    except Exception:  # noqa: BLE001 - degraded to no-pending, never fatal
        return []


def _settle_affirmed(cfg: Any, st: Any, sid: str) -> Dict[str, Any]:
    """Settle the session's latest pending staged confirmation once.

    Called only after an affirmative follow-up on a session with pending
    staged work. Settles through ``voice_commands.confirm_pending`` (the
    same settle-once journal path the OPS confirm route delegates to), so
    the confirmation_id comes from the pending record — never a fresh
    classify. A backend failure (e.g. CDP down) surfaces its typed code
    (``BROWSER_UNAVAILABLE``/``CDP_UNAVAILABLE``); an expired/foreign id
    surfaces typed ``NO_CONFIRMATION``. Nothing is ever fake-executed and
    nothing executes without this affirmation.
    """
    from . import voice_commands as VC

    ctx = VC.make_ctx(cfg, actor="dashboard:user", session=sid,
                      source="user")
    try:
        outcome = VC.confirm_pending(ctx)
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        _audit("turn_registry_confirm_error", session=sid,
               error=str(exc))
        return {"ok": False, "state": "FAILED", "code": "REGISTRY_ERROR",
                "detail": _redact(str(exc))[:200], "session_id": sid,
                "route": "registry"}
    kind = str(getattr(outcome, "kind", "") or "")
    speech = str(getattr(outcome, "speech", "") or "")
    receipt = dict(getattr(outcome, "receipt", None) or {})
    entry = getattr(outcome, "entry", None)
    entry_id = str(getattr(entry, "id", "") or "") if entry is not None \
        else ""
    if kind == "executed":
        if speech:
            st.append(sid, "assistant", speech,
                      meta={"route": "registry",
                            "entry": entry_id or None})
        _audit("turn_registry_confirmed", session=sid, entry=entry_id)
        result: Dict[str, Any] = {
            "ok": True, "text": speech, "reply": speech,
            "session_id": sid, "session": sid, "route": "registry",
            "entry": entry_id}
        base = dict(receipt)
        base.setdefault("route", "registry")
        base.setdefault("task", None)
        base.setdefault("sources", [])
        base.setdefault("model", _branch_model(cfg))
        result["receipt"] = base
        return result
    code = str(receipt.get("code") or "")
    if not code:
        low = speech.lower()
        if "nothing to confirm" in low:
            code = "NOTHING_PENDING"
        elif "confirmation is gone" in low:
            code = "NO_CONFIRMATION"
        elif "stale" in low:
            code = "STALE_OP"
        else:
            code = "REFUSED" if kind == "refused" else "REGISTRY_FAILED"
    detail = (speech or "registry execution failed; nothing was "
              "fabricated")[:300]
    if speech:
        st.append(sid, "assistant", speech,
                  meta={"route": "registry", "entry": entry_id or None,
                        "failed": True})
    _audit("turn_registry_confirm_failed", session=sid, entry=entry_id,
           code=code)
    return {"ok": False, "state": "FAILED", "code": code,
            "detail": detail, "session_id": sid, "route": "registry",
            "entry": entry_id}


# ---------------------------------------------------------------------------
# T5 — user-confirmed headless builds complete with files
# ---------------------------------------------------------------------------
#: Journal TTL for a staged build-file confirmation (same 120 s budget as
#: the broker default and the voice confirm TTL — one convention).
_TASK_CONFIRM_TTL_S = 120.0


def _task_journal(cfg: Any) -> Any:
    """The canonical confirmation journal for staged build writes.

    The same durable ``approvals.jsonl`` authority the broker and the voice
    confirm path settle through — never a second store. ``None`` when the
    state dir is unresolvable (hermetic callers) so staging fails typed
    instead of writing somewhere surprising.
    """
    try:
        state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else None
    except Exception:  # noqa: BLE001 - degraded to unavailable, never fatal
        return None
    if not state_dir:
        return None
    try:
        from .approvals import ApprovalJournal

        return ApprovalJournal(Path(state_dir) / "approvals.jsonl",
                               default_ttl_s=_TASK_CONFIRM_TTL_S)
    except Exception:  # noqa: BLE001 - degraded to unavailable, never fatal
        return None


def _task_build_capabilities() -> frozenset:
    """File-write capabilities a build turn may stage (confined writers)."""
    try:
        from . import file_patch as _FP

        return frozenset({
            _FP.capability_name(),
            _FP.mkdir_capability_name(),
            _FP.delete_capability_name(),
            _FP.write_bytes_capability_name(),
        })
    except Exception:  # noqa: BLE001 - fail closed to the one known tool
        return frozenset({"local.files_upload"})


def _pending_task(cfg: Any, sid: str) -> list:
    """Read-only pending staged build-file confirmations for one session.

    Same contract as :func:`_pending_registry`: the shared durable journal,
    filtered to the confined file-write capabilities staged for this chat
    session. No write, never raises — an unreadable journal degrades to no
    pending so the turn falls through to its normal path.
    """
    journal = _task_journal(cfg)
    if journal is None:
        return []
    try:
        items = journal.pending()
    except Exception:  # noqa: BLE001 - degraded to no-pending, never fatal
        return []
    try:
        caps = _task_build_capabilities()
    except Exception:  # noqa: BLE001 - degraded to no-pending, never fatal
        return []
    return [a for a in items
            if str(getattr(a, "capability", "")) in caps
            and isinstance(getattr(a, "meta", None), dict)
            and a.meta.get("session") == sid]


def _stage_task_proposal(cfg: Any, sid: str, key: str,
                         res: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a worker-proposed file write as a user confirmation (T5).

    Mints one digest-bound, single-use, expiring journal record for the
    exact blocked ``{tool, args}`` the worker proposed — the same envelope
    shape as :func:`_turn_registry` (``CONFIRM_REQUIRED`` +
    ``confirmation_id``). Nothing is executed here; the write happens only
    in :func:`_settle_task_affirmed` after a bare affirmation, through the
    one broker path with ``source="user"`` and this id. Full args stay in
    the in-memory task box (keyed by the worker session in the journal
    meta); the journal carries only the digest plus redacted file identity
    — never file bytes, never a secret.
    """
    pending = (res or {}).get("pending")
    if not isinstance(pending, dict) or not pending.get("tool"):
        _audit("turn_task_confirm_malformed", session=sid, task=key)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_FAILED",
                "detail": "worker asked for confirmation but named no file "
                          "write; nothing was staged",
                "session_id": sid, "session": sid, "route": "task",
                "task": key}
    tool = str(pending.get("tool") or "")
    args = dict(pending.get("args") or {})
    if tool not in _task_build_capabilities():
        _audit("turn_task_confirm_refused", session=sid, task=key,
               tool=tool)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_REFUSED",
                "detail": f"worker proposed {tool!r}, which is not a "
                          "confined file write; nothing was staged",
                "session_id": sid, "session": sid, "route": "task",
                "task": key}
    journal = _task_journal(cfg)
    if journal is None:
        _audit("turn_task_confirm_unavailable", session=sid, task=key)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_UNAVAILABLE",
                "detail": "confirmation journal unavailable; the proposed "
                          "write was not staged and nothing was executed",
                "session_id": sid, "session": sid, "route": "task",
                "task": key}
    try:
        from .capabilities import _args_digest

        digest = _args_digest(args)
    except Exception:  # noqa: BLE001 - typed, never staged blind
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_FAILED",
                "detail": "could not bind the proposed write; nothing was "
                          "staged",
                "session_id": sid, "session": sid, "route": "task",
                "task": key}
    target = _redact(str(args.get("target_path") or ""))[:120] or tool
    try:
        blob_len = len(str(args.get("content_base64") or ""))
    except Exception:  # noqa: BLE001 - size is advisory only
        blob_len = 0
    size = f"~{blob_len * 3 // 4} bytes" if blob_len else "unknown size"
    summary = (f"qubit-chat build: write {target} ({size}) — "
               f"say yes to confirm")[:300]
    try:
        item = journal.create(tool, summary, ttl_s=_TASK_CONFIRM_TTL_S,
                              args_digest=digest, issued_by="user",
                              session=sid, task_key=key, target=target)
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        _audit("turn_task_confirm_error", session=sid, task=key,
               error=str(exc))
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_FAILED",
                "detail": _redact(str(exc))[:200], "session_id": sid,
                "session": sid, "route": "task", "task": key}
    _audit("turn_task_confirm_staged", session=sid, task=key,
           target=target)
    return {"ok": False, "state": "UNAVAILABLE",
            "code": "CONFIRM_REQUIRED",
            "detail": (f"Worker proposed writing {target} ({size}). Say "
                       f"yes to confirm — nothing was written yet."),
            "confirmation_id": item.id, "session_id": sid, "session": sid,
            "route": "task", "task": key}


def _settle_task_affirmed(cfg: Any, st: Any, sid: str) -> Dict[str, Any]:
    """Settle the session's latest staged build-file confirmation once.

    Called only after a bare affirmation on a session with a pending staged
    build write (the T4 ``_is_affirmative`` check — reused, not duplicated).
    Approves the journal record, then executes the EXACT blocked call
    through the one broker path with ``source="user"`` and the staged id
    (digest-bound, single-use, expiring). Expired/denied/lost approvals are
    typed failures — never a phantom done, never an execution without the
    affirmation. On success the turn completes done-with-file with a report
    naming the created file.
    """
    from . import orchestrator as ORC

    journal = _task_journal(cfg)
    if journal is None:
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_UNAVAILABLE",
                "detail": "confirmation journal unavailable; nothing was "
                          "executed",
                "session_id": sid, "route": "task"}
    pending = _pending_task(cfg, sid)
    if not pending:
        return {"ok": False, "state": "FAILED", "code": "NOTHING_PENDING",
                "detail": "there is no pending build write to confirm",
                "session_id": sid, "route": "task"}
    target_item = pending[-1]
    cid = str(getattr(target_item, "id", "") or "")
    # A denial/expiry decided elsewhere (dashboard Approvals page, TTL)
    # wins over worker state: re-read the record so the race maps typed.
    fresh = journal.get(cid)
    if fresh is None:
        return {"ok": False, "state": "FAILED", "code": "NO_CONFIRMATION",
                "detail": "that confirmation is gone; nothing was executed",
                "session_id": sid, "route": "task"}
    if str(getattr(fresh, "state", "") or "") == "denied":
        _audit("turn_task_confirm_denied", session=sid)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_DENIED",
                "detail": "that build write was denied; nothing was executed",
                "session_id": sid, "route": "task"}
    if str(getattr(fresh, "state", "") or "") == "aborted":
        _audit("turn_task_confirm_expired", session=sid)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_EXPIRED",
                "detail": "that build confirmation expired; nothing was "
                          "executed — ask again",
                "session_id": sid, "route": "task"}
    tool = str(getattr(fresh, "capability", "") or "")
    meta = getattr(fresh, "meta", None) or {}
    task_key = str(meta.get("task_key") or "")
    proposal = ORC.task_blocked_proposal(task_key) if task_key else None
    if proposal is None or str(proposal.get("tool") or "") != tool:
        try:
            journal.respond(cid, "deny")
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            pass
        _audit("turn_task_confirm_lost", session=sid, task=task_key)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_LOST",
                "detail": "the approved build is no longer available (worker "
                          "state expired); nothing was executed — ask again",
                "session_id": sid, "route": "task"}
    try:
        approved = journal.respond(cid, "approve")
    except (KeyError, ValueError) as exc:
        _audit("turn_task_confirm_gone", session=sid, code="NO_CONFIRMATION")
        return {"ok": False, "state": "FAILED", "code": "NO_CONFIRMATION",
                "detail": _redact(str(exc))[:200] or
                          "that confirmation is gone; nothing was executed",
                "session_id": sid, "route": "task"}
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        _audit("turn_task_confirm_error", session=sid, error=str(exc))
        return {"ok": False, "state": "FAILED", "code": "TASK_CONFIRM_FAILED",
                "detail": _redact(str(exc))[:200], "session_id": sid,
                "route": "task"}
    if str(getattr(approved, "state", "") or "") == "denied":
        _audit("turn_task_confirm_denied", session=sid)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_DENIED",
                "detail": "that build write was denied; nothing was executed",
                "session_id": sid, "route": "task"}
    if str(getattr(approved, "state", "") or "") == "aborted":
        _audit("turn_task_confirm_expired", session=sid)
        return {"ok": False, "state": "FAILED",
                "code": "TASK_CONFIRM_EXPIRED",
                "detail": "that build confirmation expired; nothing was "
                          "executed — ask again",
                "session_id": sid, "route": "task"}
    out = ORC.task_execute_granted(cfg, tool, dict(proposal.get("args")
                                                   or {}), cid)
    if not out.get("ok"):
        try:
            journal.abort(cid)
        except Exception:  # noqa: BLE001 - explicit outcome, best-effort
            pass
        code = str(out.get("code") or "TASK_CONFIRM_FAILED")
        detail = str(out.get("detail") or "granted write failed")[:300]
        st.append(sid, "assistant",
                  f"Build write failed ({code}): {detail}",
                  meta={"route": "task", "phase": "failed"})
        _audit("turn_task_confirm_failed", session=sid, code=code)
        return {"ok": False, "state": "FAILED", "code": code,
                "detail": detail, "session_id": sid, "session": sid,
                "route": "task"}
    import json as _json

    receipt: Dict[str, Any] = {}
    try:
        env = _json.loads(str(out.get("output") or ""))
        if isinstance(env, dict) and isinstance(env.get("receipt"), dict):
            receipt = dict(env["receipt"])
    except Exception:  # noqa: BLE001 - the write happened; name the tool
        pass
    target = str(receipt.get("target") or meta.get("target") or tool)
    report = (f"Built {target} — file written and confirmed.")
    st.append(sid, "assistant", report + f"\n\n_Task {task_key}: done_",
              meta={"route": "task", "task": task_key, "phase": "report"})
    _audit("turn_task_confirm_done", session=sid, task=task_key,
           target=target)
    return {"ok": True, "text": report, "reply": report, "report": report,
            "session_id": sid, "session": sid, "route": "task",
            "task": task_key, "status": "done",
            "confirmation_id": cid,
            "receipt": _branch_receipt("task", task=task_key, cfg=cfg)}


def _ddg_web_fallback(query: str) -> Optional[Dict[str, Any]]:
    """No-key DDG fallback for the web branch (read-only search, no side effects).

    Reuses the existing bridge function ``scripts.web_search.ddg_search``
    (``ddgs`` library) — never shells out, never invents an HTTP client, never
    touches the Perplexity path. Verified: ``ddg_search``/``ddg_answer`` contact
    only DDG; the sole ``~/.hermes`` reference in ``scripts/web_search.py`` is
    the unused-on-this-path ``_HERMES`` constant for the Perplexity fallback
    script (never opened here).

    Returns ``{"answer", "sources"}`` with >=1 real result URL, else None
    (caller keeps typed WEB_UNAVAILABLE — nothing fabricated).
    """
    try:
        from scripts.web_search import ddg_search
    except Exception as exc:  # noqa: BLE001 - typed, audited by caller
        _audit("turn_web_ddg_import_failed", error=str(exc)[:120])
        return None
    try:
        rs = ddg_search(query, max_results=5)
    except Exception as exc:  # noqa: BLE001 - typed, never fabricated
        _audit("turn_web_ddg_failed", error=str(exc)[:120])
        return None
    if not isinstance(rs, list) or not rs:
        return None
    lines = []
    sources = []
    for r in rs:
        if not isinstance(r, dict):
            continue
        href = str(r.get("href") or "").strip()
        title = str(r.get("title") or "").strip()
        body = str(r.get("body") or "").strip()
        if href:
            sources.append(href)
        if title:
            lines.append(f"{title}: {body}" if body else title)
        if len(lines) >= 4:
            break
    if not lines or not sources:
        return None
    return {"answer": _redact("\n".join(lines))[:2000],
            "sources": sources}


def _turn_web(cfg: Any, st: Any, sid: str, task_text: str) -> Dict[str, Any]:
    """Wave C Task 2: execute a web answer, never chatter about searching."""
    from . import orchestrator as ORC

    try:
        web = ORC.answer_web_query(cfg, task_text)
    except Exception as exc:  # noqa: BLE001 - typed, never fabricated
        _audit("turn_web_error", session=sid, error=str(exc))
        web = {"ok": False, "code": "WEB_UNAVAILABLE", "answer": str(exc)[:200]}
    if not isinstance(web, dict) or not web.get("ok"):
        code = str((web or {}).get("code") or "WEB_UNAVAILABLE")
        detail = str((web or {}).get("answer") or "")[:200] or \
            "web search unavailable; nothing was fabricated"
        _audit("turn_web_unavailable", session=sid, code=code)
        fb = _ddg_web_fallback(task_text)
        if fb is None:
            return {"ok": False, "state": "FAILED", "code": code,
                    "detail": detail, "session_id": sid, "route": "web"}
        answer = str(fb.get("answer") or "")
        sources = [str(s) for s in (fb.get("sources") or [])
                   if str(s).strip()]
        if not answer.strip() or not sources:
            return {"ok": False, "state": "FAILED", "code": code,
                    "detail": detail, "session_id": sid, "route": "web"}
        body = answer + "\n\nSources:\n" + "\n".join(sources)
        st.append(sid, "assistant", body,
                  meta={"route": "web", "sources": sources or None,
                        "fallback": "ddg"})
        _audit("turn_web_ddg", session=sid, sources=len(sources))
        return {"ok": True, "text": answer, "reply": answer,
                "sources": sources, "session_id": sid, "session": sid,
                "route": "web",
                "receipt": _branch_receipt("web", task=task_text,
                                           sources=sources, cfg=cfg)}
    answer = str(web.get("answer") or "")
    sources = [str(s) for s in (web.get("sources") or []) if str(s).strip()]
    body = answer + ("\n\nSources:\n" + "\n".join(sources) if sources else "")
    st.append(sid, "assistant", body,
              meta={"route": "web", "sources": sources or None})
    _audit("turn_web", session=sid, sources=len(sources))
    return {"ok": True, "text": answer, "reply": answer, "sources": sources,
            "session_id": sid, "session": sid, "route": "web",
            "receipt": _branch_receipt("web", task=task_text,
                                       sources=sources, cfg=cfg)}


def _turn_local(cfg: Any, st: Any, sid: str, task_text: str) -> Dict[str, Any]:
    """Wave C Task 3: PC action through the canonical broker junction.

    Same path as ``VoiceOrchestrator._handle_local``: ``local.match`` for
    the action, the destructive screen first, then
    ``broker.execute("local.<name>", ...)`` on a host broker carrying the
    full registry/policy/confirmation/e-stop junction (Packet 8O: no
    direct ``LA.execute`` fallback — a disabled broker fails closed).
    """
    from .local import actions as LA

    # Destructive screen FIRST (orchestrator Packet 4D): destruction text
    # never takes the whitelist — it confirm-gates even when no action
    # pattern matches, so a destructive turn can neither execute nor be
    # mistaken for a plain no-match.
    try:
        from .safety.destructive_screen import screen_destructive

        if bool(screen_destructive(task_text).matched):
            _audit("turn_local_confirm", session=sid, action="?")
            return {"ok": False, "state": "UNAVAILABLE",
                    "code": "CONFIRM_REQUIRED",
                    "detail": "destructive local action needs confirmation; "
                              "nothing was executed",
                    "session_id": sid, "route": "local"}
    except Exception:  # noqa: BLE001 - an unreadable screen fails closed
        return {"ok": False, "state": "FAILED", "code": "LOCAL_SCREEN_ERROR",
                "detail": "could not screen the action; nothing was executed",
                "session_id": sid, "route": "local"}
    try:
        m = LA.match(task_text)
    except Exception as exc:  # noqa: BLE001 - typed, never executed blind
        return {"ok": False, "state": "FAILED", "code": "LOCAL_MATCH_ERROR",
                "detail": _redact(str(exc))[:200], "session_id": sid,
                "route": "local"}
    if m is None:
        return {"ok": False, "state": "FAILED", "code": "LOCAL_NO_MATCH",
                "detail": "no local action matched; nothing was executed",
                "session_id": sid, "route": "local"}
    if not _cfg_flag(cfg, "local", "enabled", default=True):
        _audit("turn_local_disabled", session=sid, action=m.name)
        return {"ok": False, "state": "UNAVAILABLE", "code": "LOCAL_DISABLED",
                "detail": "local actions are disabled; nothing was executed",
                "session_id": sid, "route": "local"}
    try:
        from .broker import build_host_broker

        broker = build_host_broker(cfg=cfg)
        res = broker.execute(f"local.{m.name}", dict(m.params or {}),
                             source="user", session=sid,
                             utterance=task_text)
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        _audit("turn_local_error", session=sid, action=m.name,
               error=str(exc))
        return {"ok": False, "state": "FAILED", "code": "LOCAL_ERROR",
                "detail": _redact(str(exc))[:200], "session_id": sid,
                "route": "local"}
    if res.denied == "CONFIRMATION_REQUIRED":
        _audit("turn_local_confirm", session=sid, action=m.name)
        return {"ok": False, "state": "UNAVAILABLE",
                "code": "CONFIRM_REQUIRED",
                "detail": "destructive local action needs confirmation; "
                          "nothing was executed",
                "session_id": sid, "route": "local"}
    if res.denied:
        _audit("turn_local_denied", session=sid, action=m.name,
               code=res.denied)
        return {"ok": False, "state": "FAILED", "code": str(res.denied),
                "detail": "that action was blocked by the safety policy; "
                          "nothing was executed",
                "session_id": sid, "route": "local"}
    msg = res.output if res.ok else (
        f"Action failed: {res.error}" if res.error else "")
    st.append(sid, "assistant", msg,
              meta={"route": "local", "action": m.name})
    _audit("turn_local", session=sid, action=m.name, ok=bool(res.ok))
    if not res.ok:
        return {"ok": False, "state": "FAILED", "code": "LOCAL_FAILED",
                "detail": _redact(msg)[:300], "session_id": sid,
                "route": "local"}
    return {"ok": True, "text": msg, "reply": msg, "session_id": sid,
            "session": sid, "route": "local", "action": m.name,
            "receipt": _branch_receipt("local", task=task_text, cfg=cfg)}


def _turn_registry(cfg: Any, st: Any, sid: str,
                   task_text: str) -> Dict[str, Any]:
    """Wave C Task 4: browser/voice command via the shared voice pipeline.

    Executes through ``voice_commands.voice_say`` — the SAME production op
    dispatcher (registry resolve -> dashboard_ops/confirm pipeline,
    one journal, one settle-once path) the CLI, dashboard, and voice turn
    use, with the same broker/session/confirmation context shape as
    ``handle_turn`` (``actor="dashboard:user"``, chat session id,
    ``source="user"``). No new authority, no new CDP client.
    """
    from . import voice_commands as VC

    try:
        out = VC.voice_say(cfg, task_text, actor="dashboard:user",
                           session=sid, source="user")
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        _audit("turn_registry_error", session=sid, error=str(exc))
        return {"ok": False, "state": "FAILED", "code": "REGISTRY_ERROR",
                "detail": _redact(str(exc))[:200], "session_id": sid,
                "route": "registry"}
    if not isinstance(out, dict):
        return {"ok": False, "state": "FAILED", "code": "REGISTRY_MALFORMED",
                "detail": "registry returned a non-dict outcome",
                "session_id": sid, "route": "registry"}
    state = str(out.get("state") or "")
    speech = str(out.get("speech") or out.get("detail") or "")
    entry = str(out.get("route") or out.get("entry") or "")
    if state == "needs-confirm":
        _audit("turn_registry_confirm", session=sid, entry=entry)
        return {"ok": False, "state": "UNAVAILABLE",
                "code": "CONFIRM_REQUIRED",
                "detail": (speech or "this action needs confirmation; "
                           "nothing was executed")[:300],
                "confirmation_id": out.get("confirmation_id"),
                "session_id": sid, "route": "registry", "entry": entry}
    if state in ("clarify", "ambiguous", "unknown"):
        if speech:
            st.append(sid, "assistant", speech,
                      meta={"route": "clarify", "entry": entry or None})
        return {"ok": True, "text": speech, "reply": speech,
                "session_id": sid, "session": sid, "route": "clarify",
                "candidates": out.get("candidates", [])}
    if state in ("failed", "refused", "error") or not speech:
        code = str(out.get("code") or ("REFUSED" if state == "refused"
                                       else "REGISTRY_FAILED"))
        detail = (speech or str(out.get("detail") or ""))[:300] or \
            "registry execution failed; nothing was fabricated"
        if speech:
            st.append(sid, "assistant", speech,
                      meta={"route": "registry", "entry": entry or None,
                            "failed": True})
        _audit("turn_registry_failed", session=sid, entry=entry, code=code)
        return {"ok": False, "state": "FAILED", "code": code,
                "detail": detail, "session_id": sid, "route": "registry",
                "entry": entry}
    st.append(sid, "assistant", speech,
              meta={"route": "registry", "entry": entry or None})
    _audit("turn_registry", session=sid, entry=entry)
    result: Dict[str, Any] = {
        "ok": True, "text": speech, "reply": speech, "session_id": sid,
        "session": sid, "route": "registry", "entry": entry}
    base = out.get("receipt") if isinstance(out.get("receipt"), dict) else {}
    receipt = dict(base)
    receipt.setdefault("route", "registry")
    receipt.setdefault("task", _redact(task_text)[:200] if task_text else None)
    receipt.setdefault("sources", [])
    receipt.setdefault("model", _branch_model(cfg))
    result["receipt"] = receipt
    return result


# ---------------------------------------------------------------------------
# T5 — build-task acceptance (promise-only replies are typed failures)
# ---------------------------------------------------------------------------
#: Future-tense promise phrasing with no acted work behind it. A worker reply
#: matching one of these with no tool receipts and no action evidence is the
#: proven phantom-done shape ("I will make a one-page website…", zero files).
_PROMISE_RES = (
    re.compile(r"\bi\s+will\b", re.IGNORECASE),
    re.compile(r"\bi['\u2019]ll\b", re.IGNORECASE),
    re.compile(r"\bi['\u2019]m\s+going\s+to\b", re.IGNORECASE),
    re.compile(r"\blet\s+me\b", re.IGNORECASE),
    re.compile(r"\bi\s+can\s+(make|create|build|help|do)\b", re.IGNORECASE),
)

#: Concrete completion evidence: past-tense acted verbs, the build noun,
#: created-file names, on-disk paths, or file/draft/report nouns. A reply
#: carrying any of these (or worker tool receipts) is a report, not a
#: promise. Note ``build`` alone only counts when no promise phrasing is
#: present (see :func:`_worker_acted`): "Pin probe build." is a report,
#: "I will build a website" with no file behind it is not.
_ACTION_RE = re.compile(
    r"\b(built|created|wrote|written|saved|added|finished|completed|"
    r"updated|generated|streamed|delivered|attached|done|build)\b"
    r"|[A-Za-z0-9_][A-Za-z0-9_.\-]*\.(html?|js|jsx|ts|tsx|css|json|md|"
    r"txt|py|png|jpe?g|pdf)\b"
    r"|/(?:[\w.\-]+/)+[\w.\-]+"
    r"|\b(files?|drafts?|reports?|artifacts?)\b",
    re.IGNORECASE)

#: Concrete file identity: an extension filename or an on-disk path. A reply
#: naming one with no tool receipts behind it never counts — naming the file
#: is not creating it (live phantoms 2026-10-01: "Working on hello.html …"
#: and "I created hello.html …", both zero tool calls, zero files). Genuine
#: tool-built work always carries ``steps``/``tool_used`` receipts (proven
#: against the real worker session), which return True before this check.
_FILELIKE_RE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_.\-]*\.(html?|js|jsx|ts|tsx|css|json|md|"
    r"txt|py|png|jpe?g|pdf)\b"
    r"|/(?:[\w.\-]+/)+[\w.\-]+",
    re.IGNORECASE)

#: Progress chatter with no acted work behind it. A worker reply matching one
#: of these with no tool receipts and no past-tense acted verb is the proven
#: phantom-done shape ("Working on hello.html inside the project folder.",
#: zero files — the filename mention alone satisfied the old action regex).
#: A reply that ALSO carries a past-tense acted verb ("Working on it —
#: created hello.html") still counts as a report.
_PROGRESS_RES = (
    re.compile(r"\bworking on\b", re.IGNORECASE),
    re.compile(r"\blooking into\b", re.IGNORECASE),
    re.compile(r"\bchecking( on| into)?\b", re.IGNORECASE),
    re.compile(r"\bgetting started\b", re.IGNORECASE),
    re.compile(r"\bstarting (on|with|to)\b", re.IGNORECASE),
)

#: Past-tense acted verbs: the completion-evidence half of _ACTION_RE. A
#: progress-phrased reply carrying one of these ("created", "built", ...)
#: is a report, not chatter.
_PAST_ACTED_RE = re.compile(
    r"\b(built|created|wrote|written|saved|added|finished|completed|"
    r"updated|generated|streamed|delivered|attached|done)\b",
    re.IGNORECASE)


def _worker_acted(reply: str, res: Dict[str, Any]) -> bool:
    """True when the worker reply shows acted work, not chatter.

    Tool receipts win outright (the worker ran something through the
    broker): ``steps > 0`` or a non-empty ``tool_used`` from the
    ``wait_task`` outcome. A promise-phrased reply ("I will…", "let me…")
    with no created-file evidence behind it never counts, even when it
    borrows the build noun. Progress chatter ("Working on hello.html…")
    with no past-tense acted verb never counts either. And a concrete file
    identity (an extension filename or path) with no receipts never counts
    — naming the file is not creating it; genuine tool-built work always
    carries receipts. Otherwise the reply text itself must carry concrete
    completion evidence (:data:`_ACTION_RE`).
    """
    try:
        if int((res or {}).get("steps") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if str((res or {}).get("tool_used") or "").strip():
        return True
    text = str(reply or "")
    # Future-tense phrasing ("I will…", "I'll create…") never counts without
    # receipts — naming the file is not creating it (live phantom
    # 2026-10-01: "I'll create hello.html", zero tool calls, zero files).
    # With receipts the outcome already returned True above, so no
    # file-mention exception is needed here.
    if any(rx.search(text) for rx in _PROMISE_RES):
        return False
    if any(rx.search(text) for rx in _PROGRESS_RES) \
            and not _PAST_ACTED_RE.search(text):
        return False
    if _FILELIKE_RE.search(text):
        return False
    return bool(_ACTION_RE.search(text))


def _finish_task(cfg: Any, st: Any, sid: str, key: str,
                 res: Dict[str, Any]) -> Dict[str, Any]:
    """Complete a headless task wait: persist + shape the turn outcome.

    Shared by the blocking turn and the streaming variant. A cancelled or
    e-stopped build records the cancellation — never a phantom "done"
    summary. A finished build persists the FULL raw report (same rule as
    the voice ``_handle_agent_result``) while the spoken/text field carries
    the Spark summary.

    T5 acceptance: a ``done`` worker reply with no sign of action (no tool
    receipts, no created-file evidence, no concrete completion report) is
    a typed build failure (``TASK_NO_BUILD`` for a promise-only reply,
    ``TASK_NO_ACTION`` otherwise) — never ``ok:true``. The delegation
    plumbing reports what the worker did; only acted work counts as done.
    """
    status = str((res or {}).get("status") or "error")
    if status == "cancelled":
        st.append(sid, "assistant",
                  f"Task {key} cancelled — nothing was applied after "
                  f"the stop.",
                  meta={"route": "task", "task": key, "phase": "cancelled"})
        _audit("turn_task_cancelled", session=sid, task=key)
        return {"ok": True, "text": "cancelled", "reply": "cancelled",
                "session_id": sid, "session": sid, "route": "task",
                "task": key, "status": "cancelled"}
    if status != "done":
        detail = str((res or {}).get("reply") or
                     (res or {}).get("detail") or status)[:300]
        code = str((res or {}).get("code") or "TASK_FAILED")
        st.append(sid, "assistant", f"Task {key} {status}: {detail}",
                  meta={"route": "task", "task": key, "phase": status})
        _audit("turn_task_failed", session=sid, task=key, code=code)
        return {"ok": False, "state": "FAILED", "code": code,
                "detail": detail, "session_id": sid, "session": sid,
                "route": "task", "task": key, "status": status}
    reply = str((res or {}).get("reply") or "")
    if not reply.strip():
        detail = "worker finished with no reply; nothing was built"
        st.append(sid, "assistant", f"Task {key} failed (TASK_NO_ACTION): "
                  f"{detail}",
                  meta={"route": "task", "task": key, "phase": "failed"})
        _audit("turn_task_no_action", session=sid, task=key)
        return {"ok": False, "state": "FAILED", "code": "TASK_NO_ACTION",
                "detail": detail, "session_id": sid, "session": sid,
                "route": "task", "task": key, "status": "failed"}
    if not _worker_acted(reply, res):
        if any(rx.search(reply) for rx in _PROMISE_RES):
            code = "TASK_NO_BUILD"
            detail = ("worker promised a build but built nothing "
                      f"({ _redact(reply)[:160]}); no tool ran and no file "
                      "was created")
        else:
            code = "TASK_NO_ACTION"
            detail = ("worker reply shows no tool or file action "
                      f"({_redact(reply)[:160]}); nothing was built")
        st.append(sid, "assistant", f"Task {key} failed ({code}): {detail}",
                  meta={"route": "task", "task": key, "phase": "failed"})
        _audit("turn_task_no_build" if code == "TASK_NO_BUILD"
               else "turn_task_no_action",
               session=sid, task=key, code=code)
        return {"ok": False, "state": "FAILED", "code": code,
                "detail": detail, "session_id": sid, "session": sid,
                "route": "task", "task": key, "status": "failed"}
    spoken = _summarize_report(reply, cfg)
    st.append(sid, "assistant", reply + f"\n\n_Task {key}: done_",
              meta={"route": "task", "task": key, "phase": "report"})
    _audit("turn_task_done", session=sid, task=key,
           report_chars=len(reply))
    return {"ok": True, "text": spoken, "reply": spoken, "report": reply,
            "session_id": sid, "session": sid, "route": "task", "task": key,
            "status": "done",
            "receipt": _branch_receipt("task", task=key, cfg=cfg)}


def _turn_task(cfg: Any, st: Any, sid: str, task_text: str, *,
               timeout_s: Optional[float] = None,
               cancel: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Wave C Task 5: delegate a build to the /chat worker, report back.

    ``launch_task`` runs the exact voice worker chain headlessly; the turn
    blocks with cancel checks, then the Spark summary (never a phantom)
    lands in this transcript. E-stop latched refuses before launch with
    zero worker invocations.
    """
    from . import orchestrator as ORC

    try:
        key = ORC.launch_task(task_text, session=sid, cfg=cfg)
    except ORC.TaskLaunchDenied as exc:
        state = "UNAVAILABLE" if exc.code in (
            "EMERGENCY_STOP", "BLOCKED", "SAFETY_CONFIRM_REQUIRED",
            "DRY_RUN", "TASK_INFLIGHT") else "FAILED"
        _audit("turn_task_refused", session=sid, code=exc.code)
        return {"ok": False, "state": state, "code": exc.code,
                "detail": str(exc.detail)[:200], "session_id": sid,
                "session": sid, "route": "task"}
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        _audit("turn_task_launch_error", session=sid, error=str(exc))
        return {"ok": False, "state": "FAILED",
                "code": "TASK_LAUNCH_FAILED",
                "detail": _redact(str(exc))[:200], "session_id": sid,
                "session": sid, "route": "task"}
    st.append(sid, "assistant",
              f"Working on it in /chat (task {key}). I'll report back here.",
              meta={"route": "task", "task": key, "phase": "started"})
    res = ORC.wait_task(key, timeout_s=float(timeout_s or 300.0),
                        cancel=cancel)
    # T5: the worker proposed a confirmation-gated file write — stage it as
    # a user confirmation (CONFIRM_REQUIRED + confirmation_id, same pattern
    # as _turn_registry) instead of ending typed need-confirmation forever.
    if str((res or {}).get("status") or "") == "needs-confirm":
        return _stage_task_proposal(cfg, sid, key, res)
    return _finish_task(cfg, st, sid, key, res)


def _branch_dispatch(cfg: Any, st: Any, sid: str, decided: Dict[str, Any],
                     *, timeout_s: Optional[float] = None,
                     cancel: Optional[Callable[[], bool]] = None
                     ) -> Dict[str, Any]:
    """Run one Wave C branch dict -> turn outcome (single dispatch)."""
    action = str((decided or {}).get("action") or "answer_local")
    task_text = str((decided or {}).get("task_text") or "")
    if action == "web_search":
        return _turn_web(cfg, st, sid, task_text)
    if action == "local_action":
        return _turn_local(cfg, st, sid, task_text)
    if action == "registry":
        return _turn_registry(cfg, st, sid, task_text)
    if action == "agent_task":
        return _turn_task(cfg, st, sid, task_text, timeout_s=timeout_s,
                          cancel=cancel)
    return {"ok": False, "state": "FAILED", "code": "BRANCH_UNKNOWN",
            "detail": f"no branch for action {action!r}",
            "session_id": sid}


def _decide_branch(text: str, cfg: Any) -> Dict[str, Any]:
    """Classify one turn for Wave C branching (never raises).

    A classifier fault degrades to ``answer_local`` so the turn keeps the
    legacy model path instead of failing a chat on a routing error.
    """
    try:
        decided = decide_turn_action(text, cfg=cfg)
        if isinstance(decided, dict) and decided.get("action"):
            return decided
    except Exception as exc:  # noqa: BLE001 - degraded, audited
        _audit("turn_decide_failed", error=str(exc))
    return {"action": "answer_local", "kind": "conversation",
            "task_text": text, "params": {}, "reason": "decide-failed"}


def turn(cfg: Any, text: str, *, session: Optional[str] = None,
         model: Optional[str] = None, timeout_s: Optional[float] = None,
         cancel: Optional[Callable[[], bool]] = None,
         role: str = "chat",
         actor: str = "dashboard:user",
         reasoning: Optional[str] = None) -> Dict[str, Any]:
    """Run one persisted agent turn on the shared Qubit brain path.

    ``reasoning`` (None/minimal/low/medium/high/max) is validated FIRST so
    an unknown level fails typed before any session/message write, then
    threaded as a real per-request output budget to the resolved model.

    Wave C: a classified ``web_search`` / ``local_action`` / ``registry`` /
    ``agent_task`` turn executes through its branch (shared web path,
    broker-backed local action, shared voice registry, headless /chat
    worker) instead of the model path. Anything else keeps the legacy
    model flow byte-for-byte.
    """
    if _estop(cfg):
        return {"ok": False, "state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
                "detail": "emergency stop latched; agent turn refused"}
    if not _agent_enabled(cfg):
        return {"ok": False, "state": "UNAVAILABLE", "code": "AGENT_DISABLED",
                "detail": "agent turns are off (agent.enabled=false)"}
    try:
        from .local_llm import normalize_reasoning_level

        reasoning_level = normalize_reasoning_level(reasoning)
    except Exception as exc:
        return {"ok": False, "state": "FAILED", "code": "REASONING_INVALID",
                "detail": str(exc)[:200]}
    clean = _bounded((text or "").strip(), MAX_TEXT_CHARS)
    st = store(cfg)
    # T4: an affirmative follow-up ("yes"/"confirm"/"do it") on a session
    # with a pending staged registry confirmation settles THAT record
    # through the same shared journal path — never a fresh classify (which
    # would chatter "I will open the browser…" and execute nothing).
    # Non-affirmative text and affirmatives with nothing pending keep the
    # legacy path byte-for-byte (no auto-execution without affirmation).
    if clean and _is_affirmative(clean):
        _probe = str(session or DEFAULT_SESSION).strip()[:80] \
            or DEFAULT_SESSION
        if _pending_registry(cfg, _probe):
            created = _resolve_session(st, session, model=None)
            if created.get("state") != "OK":
                return {"ok": False, "state": "FAILED",
                        "code": created.get("code"),
                        "detail": created.get("detail", "")}
            sid = created["session"]["id"]
            appended = st.append(sid, "user", clean)
            if appended.get("state") != "OK":
                return {"ok": False, "state": "FAILED",
                        "code": appended.get("code"),
                        "detail": appended.get("detail", "")}
            return _settle_affirmed(cfg, st, sid)
        # T5: same bare-affirmation check — a "yes" with a pending staged
        # build write settles THAT record (user-issued grant, one broker
        # path), never a fresh classify. Non-affirmative text keeps the
        # legacy path byte-for-byte (no auto-execution without affirmation).
        if _pending_task(cfg, _probe):
            created = _resolve_session(st, session, model=None)
            if created.get("state") != "OK":
                return {"ok": False, "state": "FAILED",
                        "code": created.get("code"),
                        "detail": created.get("detail", "")}
            sid = created["session"]["id"]
            appended = st.append(sid, "user", clean)
            if appended.get("state") != "OK":
                return {"ok": False, "state": "FAILED",
                        "code": appended.get("code"),
                        "detail": appended.get("detail", "")}
            return _settle_task_affirmed(cfg, st, sid)
    decided = _decide_branch(clean, cfg)
    if decided.get("action") in BRANCH_ACTIONS:
        created = _resolve_session(st, session, model=None)
        if created.get("state") != "OK":
            return {"ok": False, "state": "FAILED",
                    "code": created.get("code"),
                    "detail": created.get("detail", "")}
        sid = created["session"]["id"]
        if not clean:
            return {"ok": False, "state": "FAILED", "code": "MALFORMED",
                    "detail": "turn needs non-empty text", "session_id": sid}
        appended = st.append(sid, "user", clean)
        if appended.get("state") != "OK":
            return {"ok": False, "state": "FAILED",
                    "code": appended.get("code"),
                    "detail": appended.get("detail", "")}
        return _branch_dispatch(cfg, st, sid, decided, timeout_s=timeout_s,
                                cancel=cancel)
    routed = resolve_route(cfg, model, session=session, role=role)
    if not routed.get("ok"):
        return routed
    resolved_model = routed["model"]

    created = _resolve_session(st, session, model=resolved_model)
    if created.get("state") != "OK":
        return {"ok": False, "state": "FAILED", "code": created.get("code"),
                "detail": created.get("detail", "")}
    sid = created["session"]["id"]
    if not clean:
        return {"ok": False, "state": "FAILED", "code": "MALFORMED",
                "detail": "turn needs non-empty text", "session_id": sid}

    context, ctx_meta, decision = _context_prompt(
        st, sid, cfg=cfg, model_context_tokens=routed.get("context_tokens"),
        pending_text=clean)
    # Resolve escalation BEFORE persisting the user row: a pre-execution refusal
    # (no escalation target / denied profile) must leave no orphan turn in the
    # transcript. A post-append failure is retryable by design (retry_last
    # re-runs the stored user message), but a turn refused before it ever ran
    # has nothing to retry and must not be recorded as one.
    if decision.requires_escalation:
        escalated = _escalation_route(cfg, decision, routed)
        if not escalated.get("ok"):
            return {"ok": False, "state": "UNAVAILABLE",
                    "code": escalated.get("code"),
                    "detail": escalated.get("detail"),
                    "session_id": sid, "context": decision.public()}
        routed = escalated
        resolved_model = routed["model"]
        _audit("context_escalated", session=sid,
               reason=",".join(decision.reasons),
               model=resolved_model, tier=decision.tier)
    appended = st.append(sid, "user", clean)
    if appended.get("state") != "OK":
        return {"ok": False, "state": "FAILED", "code": appended.get("code"),
                "detail": appended.get("detail", "")}
    return _generate(cfg, sid, clean, resolved_model, context,
                     timeout_s=timeout_s, cancel=cancel, routed=routed,
                     role=role, context_meta=ctx_meta, decision=decision,
                     reasoning=reasoning_level)


def _generate_remote(cfg: Any, sid: str, clean: str, context: str,
                     routed: Dict[str, Any], *,
                     timeout_s: Optional[float],
                     cancel: Optional[Callable[[], bool]],
                     context_meta: Optional[Dict[str, Any]] = None,
                     decision: Any = None,
                     reasoning: Optional[str] = None) -> Dict[str, Any]:
    """Execute one turn on a registered non-local profile via the gateway.

    Truthful receipts: identity/provider/model come from the gateway result;
    a refusal is typed and redacted, never fabricated text. The profile was
    already validated executable by :func:`resolve_model`, and the gateway
    re-enforces egress, allowlist, budget, credential, and capability policy.
    """
    from .providers import gateway as GW
    from .providers.contracts import ProviderRequest

    st = store(cfg)
    ev = _cancel_event(sid)
    ev.clear()
    prompt = clean
    if context:
        prompt = f"Recent conversation:\n{context}\n\nUser: {clean}"
    started = time.monotonic()

    def _stopped() -> bool:
        return (_estop(cfg) or ev.is_set()
                or (cancel is not None and bool(cancel())))

    if _stopped():
        _release_cancel(sid, ev)
        return {"ok": False, "state": "UNAVAILABLE", "code": "CANCELLED",
                "detail": "cancelled before remote execution", "session_id": sid}
    req = ProviderRequest(
        prompt=prompt, max_tokens=1024,
        timeout_s=min(timeout_s or 120.0, 300.0),
        route="frontier", provider=str(routed.get("provider") or ""),
        model=str(routed.get("model") or ""),
        idempotency_key=f"{sid}:{time.monotonic_ns()}")
    try:
        res = GW.run(req, cfg, cancel=_stopped, reasoning=reasoning,
                     profile_id=str(routed.get("profile_id") or ""),
                     session_id=sid)
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        _release_cancel(sid, ev)
        _audit("turn_remote_error", session=sid, error=str(exc))
        return {"ok": False, "state": "FAILED", "code": "MODEL_ERROR",
                "detail": _redact(str(exc))[:300], "session_id": sid}
    finally:
        _release_cancel(sid, ev)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    receipt = {"runtime": "provider-gateway", "provider": res.provider,
               "model": res.model, "identity": res.identity,
               "profile": routed.get("profile_id"),
               "path": "voicebridge.providers.gateway.run"}
    _applied = (res.details or {}).get("reasoning_applied")
    if _applied is not None:
        receipt["reasoning_applied"] = dict(_applied)
    elif reasoning is not None:
        receipt["reasoning_applied"] = {"level": reasoning,
                                        "max_tokens": None}
    # Truthful audit trail when the provider needed a repaired output budget
    # (reasoning consumed the requested one and the answer did not fit).
    _repairs = (res.details or {}).get("output_budget_repairs")
    if _repairs:
        receipt["output_budget_repairs"] = int(_repairs)
        receipt["effective_max_tokens"] = int(
            (res.details or {}).get("effective_max_tokens") or 0)
    if context_meta is not None:
        receipt["context"] = dict(context_meta)
        if decision is not None:
            receipt["context"]["routing"] = decision.public()
    if res.usage is not None:
        receipt["usage"] = {
            "provider": res.usage.provider, "model": res.usage.model,
            "identity": res.usage.identity,
            "prompt_tokens": res.usage.prompt_tokens,
            "completion_tokens": res.usage.completion_tokens,
            "latency_ms": res.usage.latency_ms,
            "cost_usd_est": res.usage.cost_usd_est,
            "fallback": res.usage.fallback,
            "fallback_reason": res.usage.fallback_reason,
        }
    if not res.ok:
        _audit("turn_remote_failed", session=sid, code=res.code)
        return {"ok": False, "state": "FAILED",
                "code": str(res.code or "MODEL_ERROR"),
                "detail": _redact(res.message or "provider refused")[:300],
                "session_id": sid, "elapsed_ms": elapsed_ms, "receipt": receipt}
    safe = _redact(_sanitize(str(res.text or "").strip()))
    if not safe:
        return {"ok": False, "state": "FAILED", "code": "MODEL_UNAVAILABLE",
                "detail": "provider returned no text; nothing was fabricated",
                "session_id": sid, "elapsed_ms": elapsed_ms, "receipt": receipt}
    saved = st.append(sid, "assistant", safe,
                      meta={"model": res.model, "provider": res.provider,
                            "profile": routed.get("profile_id"),
                            "elapsed_ms": elapsed_ms})
    record_activity(cfg, sid, safe)
    _audit("turn_remote", session=sid, elapsed_ms=elapsed_ms, ok=True)
    return {"ok": True, "reply": safe, "session": sid, "session_id": sid,
            "elapsed_ms": elapsed_ms, "message": saved.get("message"),
            "message_count": saved.get("message_count"), "receipt": receipt}


def _generate(cfg: Any, sid: str, clean: str, resolved_model: str,
              context: str, *, timeout_s: Optional[float],
              cancel: Optional[Callable[[], bool]],
              routed: Optional[Dict[str, Any]] = None,
              role: str = "chat",
              context_meta: Optional[Dict[str, Any]] = None,
              decision: Any = None,
              reasoning: Optional[str] = None) -> Dict[str, Any]:
    """Run an already-persisted user turn through the resolved model path.

    Local/local-default profiles run through ``voicebridge.agent.turn`` (the
    same gateway-backed Brain4 path Qubit Chat/CLI use). A registered non-local
    profile runs through the single provider gateway
    (``voicebridge.providers.gateway.run``) so an approved API model is a real
    adapter, never a local model pretending to be remote — and never a remote
    call pretending to be local.

    Prior conversation is folded into the turn text so the thin agent wrapper
    (which has no conversation-prompt parameter) still sees bounded context;
    the persisted transcript keeps only the clean user message.
    """
    if routed and str(routed.get("kind")) in ("api", "realtime", "plugin"):
        return _generate_remote(cfg, sid, clean, context, routed,
                                timeout_s=timeout_s, cancel=cancel,
                                context_meta=context_meta, decision=decision,
                                reasoning=reasoning)
    from . import agent as AG

    st = store(cfg)
    ev = _cancel_event(sid)
    ev.clear()
    prompt = clean
    if context:
        prompt = f"Recent conversation:\n{context}\n\nUser: {clean}"
    started = time.monotonic()
    try:
        res = AG.turn(cfg, prompt, session=sid,
                      timeout_s=min(timeout_s or 120.0, 300.0),
                      model=resolved_model,
                      role=role,
                      profile_id=(routed or {}).get("profile_id"),
                      reasoning=reasoning,
                      estop_engaged=lambda: _estop(cfg) or ev.is_set()
                      or (cancel is not None and bool(cancel())))
    except AG.AgentDenied as exc:
        _release_cancel(sid, ev)
        _audit("turn_denied", session=sid, code=exc.code)
        status = "EMERGENCY_STOP" if exc.code == "EMERGENCY_STOP" \
            else "AGENT_DISABLED"
        return {"ok": False, "state": "UNAVAILABLE", "code": status,
                "detail": _redact(exc.detail)[:300], "session_id": sid}
    except Exception as exc:  # noqa: BLE001 - typed, redacted, no fabrication
        _release_cancel(sid, ev)
        _audit("turn_error", session=sid, error=str(exc))
        return {"ok": False, "state": "FAILED", "code": "MODEL_ERROR",
                "detail": _redact(str(exc))[:300], "session_id": sid}
    finally:
        _release_cancel(sid, ev)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not res.get("ok"):
        code = str(res.get("code") or "MODEL_ERROR")
        detail = str(res.get("error") or res.get("detail") or "")
        if code == "CANCELLED":
            return {"ok": False, "state": "FAILED", "code": "CANCELLED",
                    "detail": "cancelled during execution", "session_id": sid,
                    "elapsed_ms": elapsed_ms}
        _audit("turn_failed", session=sid, code=code)
        return {"ok": False, "state": "FAILED", "code": code,
                "detail": _redact(detail)[:300], "session_id": sid,
                "elapsed_ms": elapsed_ms}
    safe = _redact(_sanitize(str(res.get("reply", "")).strip()))
    saved = st.append(sid, "assistant", safe,
                      meta={"model": resolved_model,
                            "elapsed_ms": elapsed_ms})
    record_activity(cfg, sid, safe)
    receipt = dict(res.get("receipt") or {})
    receipt.setdefault("transport", "voicebridge.chat_transport.turn")
    if context_meta is not None:
        receipt["context"] = dict(context_meta)
        if decision is not None:
            receipt["context"]["routing"] = decision.public()
    _audit("turn", session=sid, elapsed_ms=elapsed_ms, ok=True,
           context_tier=(decision.tier if decision is not None else None),
           context_window=(decision.window_tokens if decision is not None else None))
    return {"ok": True, "reply": safe, "session": sid, "session_id": sid,
            "elapsed_ms": elapsed_ms,
            "message": saved.get("message"),
            "message_count": saved.get("message_count"),
            "receipt": receipt}


def retry_last(cfg: Any, session: Optional[str] = None, *,
               model: Optional[str] = None, timeout_s: Optional[float] = None,
               cancel: Optional[Callable[[], bool]] = None,
               role: str = "chat",
               reasoning: Optional[str] = None) -> Dict[str, Any]:
    """Re-run the model for a session's last unanswered user message.

    A failed turn persists the user message but no assistant reply, so retry
    re-invokes the model WITHOUT duplicating the transcript. Typed refusal when
    there is nothing to retry (the last turn already produced an assistant
    message). ``reasoning`` reuses the same validated selector as
    :func:`turn`."""
    if _estop(cfg):
        return {"ok": False, "state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
                "detail": "emergency stop latched; agent turn refused"}
    if not _agent_enabled(cfg):
        return {"ok": False, "state": "UNAVAILABLE", "code": "AGENT_DISABLED",
                "detail": "agent turns are off (agent.enabled=false)"}
    routed = resolve_route(cfg, model, session=session, role=role)
    if not routed.get("ok"):
        return routed
    resolved_model = routed["model"]
    st = store(cfg)
    sid = str(session or DEFAULT_SESSION).strip()[:80] or DEFAULT_SESSION
    shown = st.show(sid)
    if shown.get("state") != "OK":
        return {"ok": False, "state": "FAILED", "code": "SESSION_NOT_FOUND",
                "detail": f"no chat session {sid!r}"}
    msgs = shown["messages"]
    if not msgs or msgs[-1].get("role") != "user":
        return {"ok": False, "state": "FAILED", "code": "NOTHING_TO_RETRY",
                "detail": "the last turn already has an assistant reply",
                "session_id": sid}
    clean = _bounded(msgs[-1].get("content", ""), MAX_TEXT_CHARS)
    context, ctx_meta, decision = _context_prompt(
        st, sid, exclude_last=True, cfg=cfg,
        model_context_tokens=routed.get("context_tokens"),
        pending_text=clean)
    if decision.requires_escalation:
        escalated = _escalation_route(cfg, decision, routed)
        if not escalated.get("ok"):
            return {"ok": False, "state": "UNAVAILABLE",
                    "code": escalated.get("code"),
                    "detail": escalated.get("detail"), "session_id": sid,
                    "context": decision.public()}
        routed = escalated
        resolved_model = routed["model"]
    try:
        from .local_llm import normalize_reasoning_level

        retry_reasoning = normalize_reasoning_level(reasoning)
    except Exception as exc:
        return {"ok": False, "state": "FAILED", "code": "REASONING_INVALID",
                "detail": str(exc)[:200], "session_id": sid}
    return _generate(cfg, sid, clean, resolved_model, context,
                     timeout_s=timeout_s, cancel=cancel, routed=routed,
                     role=role, context_meta=ctx_meta, decision=decision,
                     reasoning=retry_reasoning)


# ---------------------------------------------------------------------------
# Streaming turn
# ---------------------------------------------------------------------------
def stream_preflight(cfg: Any, text: str, model: Optional[str] = None, *,
                     session: Optional[str] = None,
                     role: str = "chat") -> tuple:
    """Single preflight for a streaming turn, before any SSE frame opens.

    Returns ``(status, refusal_event, resolved_model)``. When ``refusal_event``
    is not None it carries ``{"event": "error", "code": ..., "detail": ...}``
    and no SSE frame may be emitted. One authority shared by ``stream_turn``
    (yields the event) and the webui SSE front door (maps the status to HTTP
    JSON), so model validation, the empty-text guard and the e-stop probe are
    never duplicated.
    """
    if _estop(cfg):
        return 403, {"event": "error", "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; agent turn refused"}, None
    if not _agent_enabled(cfg):
        return 403, {"event": "error", "code": "AGENT_DISABLED",
                     "detail": "agent turns are off (agent.enabled=false)"}, None
    routed = resolve_route(cfg, model, session=session, role=role)
    if not routed.get("ok"):
        return 400, {"event": "error", "code": routed.get("code"),
                     "detail": routed.get("detail")}, None
    if not (text or "").strip():
        return 400, {"event": "error", "code": "MALFORMED",
                     "detail": "turn needs non-empty text"}, None
    return None, None, routed["model"]


def _stream_remote(cfg: Any, text: str, *, session: Optional[str],
                   routed: Dict[str, Any], timeout_s: Optional[float],
                   cancel: Optional[Callable[[], bool]],
                   reasoning: Optional[str] = None
                   ) -> Iterator[Dict[str, Any]]:
    """Streaming envelope for a registered non-local profile.

    The gateway/provider path is non-streaming, so the reply is emitted as a
    single truthful ``delta`` and the receipt marks ``streaming: false``. It
    never claims token-by-token streaming for a provider that does not offer
    it, and never fabricates text on failure.
    """
    clean = _bounded((text or "").strip(), MAX_TEXT_CHARS)
    st = store(cfg)
    created = _resolve_session(st, session, model=routed.get("model"))
    if created.get("state") != "OK":
        yield {"event": "error", "code": created.get("code"),
               "detail": created.get("detail", "")}
        return
    sid = created["session"]["id"]
    context, ctx_meta, decision = _context_prompt(
        st, sid, cfg=cfg, model_context_tokens=routed.get("context_tokens"),
        pending_text=clean)
    # Atomic ordering: a refused escalation must not leave an orphan user row
    # (same contract as the non-streaming turn).
    if decision.requires_escalation:
        escalated = _escalation_route(cfg, decision, routed)
        if not escalated.get("ok"):
            yield {"event": "error", "code": escalated.get("code"),
                   "detail": escalated.get("detail"), "session_id": sid}
            return
        routed = escalated
    appended = st.append(sid, "user", clean)
    if appended.get("state") != "OK":
        yield {"event": "error", "code": appended.get("code"),
               "detail": appended.get("detail", "")}
        return
    yield {"event": "start", "session_id": sid, "session": sid,
           "model": routed.get("model"), "provider": routed.get("provider"),
           "streaming": False, "message": appended.get("message"),
           "context": decision.public()}
    res = _generate_remote(cfg, sid, clean, context, routed,
                           timeout_s=timeout_s, cancel=cancel,
                           context_meta=ctx_meta, decision=decision,
                           reasoning=reasoning)
    if not res.get("ok"):
        yield {"event": "error", "code": res.get("code"),
               "detail": res.get("detail"), "session_id": sid}
        return
    yield {"event": "delta", "text": res["reply"], "session_id": sid}
    receipt = dict(res.get("receipt") or {})
    receipt["streaming"] = False
    yield {"event": "done", "ok": True, "reply": res["reply"],
           "session_id": sid, "session": sid,
           "elapsed_ms": res.get("elapsed_ms"), "receipt": receipt}


def _stream_branch(cfg: Any, st: Any, sid: str, clean: str,
                   decided: Dict[str, Any], *,
                   timeout_s: Optional[float] = None,
                   cancel: Optional[Callable[[], bool]] = None
                   ) -> Iterator[Dict[str, Any]]:
    """Stream one Wave C branch as start -> delta -> done (never fake).

    The user row is already persisted by the caller. Web/local/registry
    execute synchronously and stream as a single truthful delta (the plan
    forbids a fake token-by-token search narrative). A build task streams
    ``progress`` frames while the worker runs, then the full ``report`` as
    the final delta; old clients ignore the unknown ``progress`` event.
    """
    action = str((decided or {}).get("action") or "")
    task_text = str((decided or {}).get("task_text") or clean)
    appended = st.append(sid, "user", clean)
    if appended.get("state") != "OK":
        yield {"event": "error", "code": appended.get("code"),
               "detail": appended.get("detail", ""), "session_id": sid}
        return
    started = time.monotonic()
    yield {"event": "start", "session_id": sid, "session": sid,
           "route": action, "message": appended.get("message")}
    if action == "agent_task":
        yield from _stream_task(cfg, st, sid, task_text, started,
                                timeout_s=timeout_s, cancel=cancel)
        return
    res = _branch_dispatch(cfg, st, sid, decided, timeout_s=timeout_s,
                           cancel=cancel)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not res.get("ok"):
        yield {"event": "error", "code": res.get("code", "BRANCH_FAILED"),
               "detail": res.get("detail", ""), "session_id": sid,
               "route": action, "elapsed_ms": elapsed_ms}
        return
    text = str(res.get("text") or res.get("reply") or "")
    yield {"event": "delta", "text": text, "session_id": sid}
    receipt: Dict[str, Any] = {"runtime": "qubit-chat-branch",
                               "route": action, "streaming": True,
                               "task": _redact(task_text)[:200]
                               if task_text else None,
                               "sources": [],
                               "model": _branch_model(cfg),
                               "path": "voicebridge.chat_transport"
                                       "._stream_branch"}
    for key in ("sources", "entry", "action", "candidates"):
        if res.get(key) not in (None, "", []):
            receipt[key] = res[key]
    yield {"event": "done", "ok": True, "reply": text,
           "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
           "receipt": receipt}


def _stream_task(cfg: Any, st: Any, sid: str, task_text: str, started: float,
                 *, timeout_s: Optional[float] = None,
                 cancel: Optional[Callable[[], bool]] = None
                 ) -> Iterator[Dict[str, Any]]:
    """Stream a headless build: progress frames, then the Spark report."""
    from . import orchestrator as ORC

    try:
        key = ORC.launch_task(task_text, session=sid, cfg=cfg)
    except ORC.TaskLaunchDenied as exc:
        yield {"event": "error", "code": exc.code,
               "detail": str(exc.detail)[:200], "session_id": sid,
               "route": "agent_task"}
        return
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        yield {"event": "error", "code": "TASK_LAUNCH_FAILED",
               "detail": _redact(str(exc))[:200], "session_id": sid,
               "route": "agent_task"}
        return
    st.append(sid, "assistant",
              f"Working on it in /chat (task {key}). I'll report back here.",
              meta={"route": "task", "task": key, "phase": "started"})
    yield {"event": "progress", "task": key, "status": "working",
           "detail": "worker started", "session_id": sid}
    budget = float(timeout_s or 300.0)
    deadline = time.monotonic() + max(5.0, budget)
    res: Dict[str, Any] = {"status": "timeout", "reply": ""}
    while True:
        try:
            if cancel is not None and bool(cancel()):
                res = ORC.wait_task(key, timeout_s=0.5, cancel=cancel)
                break
        except Exception:  # noqa: BLE001 - a broken cancel probe is ignored
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            res = {"status": "timeout", "reply": "",
                   "detail": "task still running; poll again"}
            break
        res = ORC.wait_task(key, timeout_s=min(5.0, remaining),
                            cancel=cancel)
        status = str((res or {}).get("status") or "")
        if status in ("done", "error", "cancelled", "needs-confirm"):
            break
        if status == "unknown":
            break
        elapsed = int((time.monotonic() - started) * 1000)
        yield {"event": "progress", "task": key, "status": "working",
               "detail": f"still working ({elapsed // 1000}s)",
               "session_id": sid, "elapsed_ms": elapsed}
    if str((res or {}).get("status") or "") == "timeout" and \
            time.monotonic() < deadline:
        res = ORC.wait_task(key, timeout_s=max(0.5, deadline -
                                               time.monotonic()),
                            cancel=cancel)
    # T5: stream the staged confirmation like the blocking turn does — a
    # needs-confirm worker outcome is a CONFIRM_REQUIRED error event (with
    # the confirmation_id), never a silent hang or a phantom done.
    if str((res or {}).get("status") or "") == "needs-confirm":
        staged = _stage_task_proposal(cfg, sid, key, res)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        yield {"event": "error", "code": staged.get("code",
                                                   "CONFIRM_REQUIRED"),
               "detail": staged.get("detail", ""), "session_id": sid,
               "route": "task", "task": key, "elapsed_ms": elapsed_ms,
               **({"confirmation_id": staged["confirmation_id"]}
                  if staged.get("confirmation_id") else {})}
        return
    out = _finish_task(cfg, st, sid, key, res)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not out.get("ok"):
        yield {"event": "error", "code": out.get("code", "TASK_FAILED"),
               "detail": out.get("detail", ""), "session_id": sid,
               "route": "task", "task": key, "elapsed_ms": elapsed_ms}
        return
    if out.get("status") == "cancelled":
        yield {"event": "cancelled", "session_id": sid,
               "elapsed_ms": elapsed_ms, "route": "task", "task": key}
        return
    yield {"event": "progress", "task": key, "status": "done",
           "detail": "worker finished", "session_id": sid,
           "elapsed_ms": elapsed_ms}
    report = str(out.get("report") or out.get("text") or "")
    yield {"event": "delta", "text": report, "session_id": sid}
    yield {"event": "done", "ok": True, "reply": report,
           "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
           "receipt": {"runtime": "qubit-chat-worker", "route": "task",
                       "streaming": True, "task": key, "sources": [],
                       "model": _branch_model(cfg),
                       "path": "voicebridge.chat_transport._stream_task"}}


def stream_turn(cfg: Any, text: str, *, session: Optional[str] = None,
         model: Optional[str] = None, timeout_s: Optional[float] = None,
         cancel: Optional[Callable[[], bool]] = None,
         role: str = "chat",
         actor: str = "dashboard:user",
         reasoning: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Yield typed streaming events for one persisted agent turn.

    Event shapes: ``{"event": "start"}``, ``{"event": "delta", "text": ...}``,
    ``{"event": "done", "reply": ..., "receipt": ...}``,
    ``{"event": "cancelled"}``, ``{"event": "error", "code": ...}``. Deltas
    are real completed sentences streamed from ``local_llm``; nothing is
    synthesized when the model fails. ``reasoning`` is validated before any
    session write and threaded as a real output budget on both the local
    streaming worker and the remote path.

    Wave C: classified branch turns stream through ``_stream_branch``
    (single truthful delta; build tasks add ``progress`` frames + the full
    ``report`` delta) instead of the model worker.
    """
    try:
        from .local_llm import normalize_reasoning_level

        reasoning_level = normalize_reasoning_level(reasoning)
    except Exception as exc:
        yield {"event": "error", "code": "REASONING_INVALID",
               "detail": str(exc)[:200]}
        return
    status, refusal, resolved_model = stream_preflight(cfg, text, model,
                                                       session=session,
                                                       role=role)
    if refusal is not None:
        yield refusal
        return
    # Wave C branches run before any model path (local or remote): a web,
    # local, registry, or build turn never needs the chat model, so a dead
    # or below-floor model must not block it — and a remote-routed session
    # must still execute/confirm/delegate instead of chattering.
    clean = _bounded((text or "").strip(), MAX_TEXT_CHARS)
    st = store(cfg)
    created = _resolve_session(st, session, model=resolved_model)
    if created.get("state") != "OK":
        yield {"event": "error", "code": created.get("code"),
               "detail": created.get("detail", "")}
        return
    sid = created["session"]["id"]
    # T4: same affirmative-settle as turn() — a bare "yes" with pending
    # staged work settles that record instead of reclassifying into chatter.
    if clean and _is_affirmative(clean) and _pending_registry(cfg, sid):
        started = time.monotonic()
        yield {"event": "start", "session_id": sid, "session": sid,
               "route": "registry"}
        appended = st.append(sid, "user", clean)
        if appended.get("state") != "OK":
            yield {"event": "error", "code": appended.get("code"),
                   "detail": appended.get("detail", ""), "session_id": sid}
            return
        res = _settle_affirmed(cfg, st, sid)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not res.get("ok"):
            yield {"event": "error", "code": res.get("code", "BRANCH_FAILED"),
                   "detail": res.get("detail", ""), "session_id": sid,
                   "route": "registry", "elapsed_ms": elapsed_ms}
            return
        text_out = str(res.get("text") or res.get("reply") or "")
        yield {"event": "delta", "text": text_out, "session_id": sid}
        yield {"event": "done", "ok": True, "reply": text_out,
               "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
               "receipt": res.get("receipt")}
        return
    # T5: same affirmative-settle as turn() for staged build writes.
    if clean and _is_affirmative(clean) and _pending_task(cfg, sid):
        started = time.monotonic()
        yield {"event": "start", "session_id": sid, "session": sid,
               "route": "task"}
        appended = st.append(sid, "user", clean)
        if appended.get("state") != "OK":
            yield {"event": "error", "code": appended.get("code"),
                   "detail": appended.get("detail", ""), "session_id": sid}
            return
        res = _settle_task_affirmed(cfg, st, sid)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not res.get("ok"):
            yield {"event": "error", "code": res.get("code", "BRANCH_FAILED"),
                   "detail": res.get("detail", ""), "session_id": sid,
                   "route": "task", "elapsed_ms": elapsed_ms}
            return
        text_out = str(res.get("text") or res.get("reply") or "")
        yield {"event": "delta", "text": text_out, "session_id": sid}
        yield {"event": "done", "ok": True, "reply": text_out,
               "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
               "receipt": res.get("receipt")}
        return
    decided = _decide_branch(clean, cfg)
    if decided.get("action") in BRANCH_ACTIONS:
        yield from _stream_branch(cfg, st, sid, clean, decided,
                                  timeout_s=timeout_s, cancel=cancel)
        return
    routed_probe = resolve_route(cfg, model, session=session, role=role)
    if routed_probe.get("kind") in ("api", "realtime", "plugin"):
        yield from _stream_remote(cfg, text, session=session,
                                  routed=routed_probe, timeout_s=timeout_s,
                                  cancel=cancel, reasoning=reasoning_level)
        return

    clean = _bounded((text or "").strip(), MAX_TEXT_CHARS)
    st = store(cfg)
    created = _resolve_session(st, session, model=resolved_model)
    if created.get("state") != "OK":
        yield {"event": "error", "code": created.get("code"),
               "detail": created.get("detail", "")}
        return
    sid = created["session"]["id"]
    from . import context_policy as CTX

    msgs = st.messages(sid, limit=_CONTEXT_READ_LIMIT).get("messages", [])
    decision = CTX.decide_context(
        cfg, msgs + [{"role": "user", "content": clean}],
        model_context_tokens=routed_probe.get("context_tokens"))
    if decision.requires_escalation:
        escalated = _escalation_route(cfg, decision, routed_probe)
        if not escalated.get("ok"):
            yield {"event": "error", "code": escalated.get("code"),
                   "detail": escalated.get("detail"), "session_id": sid,
                   "context": decision.public()}
            return
        yield from _stream_remote(cfg, text, session=session,
                                  routed=escalated, timeout_s=timeout_s,
                                  cancel=cancel)
        return
    context, ctx_meta = CTX.build_context_prompt(msgs, decision)
    appended = st.append(sid, "user", clean)
    if appended.get("state") != "OK":
        yield {"event": "error", "code": appended.get("code"),
               "detail": appended.get("detail", "")}
        return
    ev = _cancel_event(sid)
    ev.clear()
    started = time.monotonic()
    yield {"event": "start", "session_id": sid, "session": sid,
           "model": resolved_model, "message": appended.get("message"),
           "context": decision.public()}

    out: "queue.Queue[Any]" = queue.Queue()
    done = object()
    # ``chat_local_stream`` can re-emit a growing cumulative buffer on its
    # bounded empty-retry path (see the cross-boundary note in the W2 handoff:
    # local_llm.py line ~534 flushes without clearing ``buf``). Reconstruct the
    # clean text here so a duplicated/cumulative emission can never double the
    # displayed or persisted reply.
    acc = {"text": "", "raw": ""}

    def _on_sentence(sentence: str) -> None:
        s = str(sentence or "")
        if not s:
            return
        raw = acc["raw"]
        if raw and s.startswith(raw):
            suffix = s[len(raw):]
        elif raw and s == raw:
            suffix = ""
        else:
            sep = (" " if acc["text"] and not acc["text"].endswith(" ")
                   and not s.startswith(" ") else "")
            suffix = sep + s
        acc["raw"] = s
        acc["text"] += suffix
        if suffix:
            out.put(("delta", suffix))

    def _worker() -> None:
        try:
            from .providers import gateway as _gateway
            res = _gateway.run_role(
                clean, cfg, role=role,
                profile_id=(routed_probe or {}).get("profile_id"),
                model=resolved_model,
                max_tokens=1024,
                timeout_s=min(timeout_s or 120.0, 300.0),
                local_mode="chat", conversation_prompt=context,
                on_sentence=_on_sentence,
                should_stop=lambda: ev.is_set()
                or (cancel is not None and bool(cancel())),
                reasoning=reasoning_level)
            if res.ok:
                out.put(("reply", res.text))
                out.put(("identity", res))
            else:
                out.put(("error", res))
        except Exception as exc:  # noqa: BLE001 - surfaced as a typed event
            out.put(("error", exc))
        finally:
            out.put(done)

    thread = threading.Thread(target=_worker, name=f"qubit-chat-stream-{sid}",
                              daemon=True)
    thread.start()
    streamed = ""
    final_reply = ""
    identity_res = None
    try:
        while True:
            item = out.get()
            if item is done:
                break
            kind, value = item
            if kind == "delta":
                streamed += value
                yield {"event": "delta", "text": value, "session_id": sid}
            elif kind == "reply":
                final_reply = value or ""
            elif kind == "identity":
                identity_res = value
            elif kind == "error":
                if hasattr(value, "code"):
                    code = ("MODEL_UNAVAILABLE"
                            if getattr(value, "code", "") in
                            ("MODEL_UNAVAILABLE", "EMPTY_EMBEDDING")
                            else "MODEL_ERROR")
                    detail = _redact(str(getattr(value, "message", "")
                                         or "model provider refused"))[:300]
                else:
                    code = "MODEL_ERROR"
                    detail = _redact(str(value))[:300]
                _audit("stream_error", session=sid, code=code)
                yield {"event": "error", "code": code,
                       "detail": detail, "session_id": sid}
                return
    finally:
        _release_cancel(sid, ev)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if ev.is_set() or (cancel is not None and cancel()):
        _audit("stream_cancelled", session=sid, elapsed_ms=elapsed_ms)
        yield {"event": "cancelled", "session_id": sid, "elapsed_ms": elapsed_ms}
        return
    # The reconstructed stream is authoritative: on the retry path
    # ``chat_local_stream`` returns a join of cumulative fragments.
    full = (acc["text"] or final_reply or streamed or "").strip()
    if not full:
        _audit("stream_empty", session=sid, elapsed_ms=elapsed_ms)
        yield {"event": "error", "code": "MODEL_UNAVAILABLE",
               "detail": "local model returned no text; it may be unreachable "
                         "— nothing was fabricated",
               "session_id": sid, "elapsed_ms": elapsed_ms}
        return
    safe = _redact(_sanitize(full.strip()))
    saved = st.append(sid, "assistant", safe,
                      meta={"model": resolved_model,
                            "elapsed_ms": elapsed_ms, "streamed": True})
    record_activity(cfg, sid, safe)
    _audit("stream_done", session=sid, elapsed_ms=elapsed_ms,
           context_tier=decision.tier, context_window=decision.window_tokens)
    receipt = {"runtime": "qubit-native", "model": resolved_model,
               "path": "voicebridge.chat_transport.stream_turn",
               "streaming": True,
               "context": dict(ctx_meta, routing=decision.public())}
    if reasoning_level is not None:
        from .local_llm import resolve_reasoning_budget

        receipt["reasoning_applied"] = {
            "level": reasoning_level,
            "max_tokens": resolve_reasoning_budget(reasoning_level)}
    if identity_res is not None:
        receipt["provider"] = identity_res.provider
        receipt["identity"] = identity_res.identity
        details = dict(getattr(identity_res, "details", None) or {})
        for key in ("profile", "role", "source", "route", "locality",
                    "reasoning_applied"):
            if details.get(key) not in (None, ""):
                receipt[key] = details[key]
        usage = getattr(identity_res, "usage", None)
        if usage is not None:
            receipt["usage"] = {
                "provider": usage.provider, "model": usage.model,
                "identity": usage.identity,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "latency_ms": usage.latency_ms,
                "cost_usd_est": usage.cost_usd_est,
                "fallback": usage.fallback,
                "fallback_reason": usage.fallback_reason,
            }
    yield {"event": "done", "ok": True, "reply": safe, "session_id": sid,
           "session": sid, "elapsed_ms": elapsed_ms,
           "message": saved.get("message"),
           "message_count": saved.get("message_count"),
           "receipt": receipt}


def sse_encode(event: Dict[str, Any]) -> str:
    """Encode one event as an SSE frame (``event:`` + JSON ``data:``)."""
    name = str(event.get("event", "message"))
    return f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def stream_sse(cfg: Any, text: str, **kwargs: Any) -> Iterator[str]:
    """SSE-encoded streaming turn for a threaded HTTP handler."""
    for event in stream_turn(cfg, text, **kwargs):
        yield sse_encode(event)


# ---------------------------------------------------------------------------
# PTY-shaped, agent-backed terminal session (no shell)
# ---------------------------------------------------------------------------
_RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")
_PROMPT = b"qubit> "


class AgentPtySession:
    """A PTY-shaped session with no PTY: keystrokes in, agent output out.

    The frontend xterm sends raw keystrokes; this class buffers a line, runs a
    real agent turn on Enter, and returns the reply as terminal-ready bytes.
    ``\\x1b[RESIZE:cols;rows]`` is accepted (for wire compatibility) and
    stored; Ctrl-C cancels an in-flight turn and Ctrl-D ends the session. No
    process, no shell, no file descriptor is ever spawned.
    """

    def __init__(self, cfg: Any, session_id: str = DEFAULT_SESSION, *,
                 model: Optional[str] = None, cols: int = 80,
                 rows: int = 24) -> None:
        self.cfg = cfg
        self.session_id = str(session_id or DEFAULT_SESSION)[:80]
        self.model = model
        self.cols = int(cols)
        self.rows = int(rows)
        self.alive = True
        self._buf = ""

    @property
    def prompt(self) -> bytes:
        return _PROMPT

    def banner(self) -> bytes:
        st = store(self.cfg)
        ensured = st.ensure(self.session_id, model=self.model)
        model = (ensured.get("session") or {}).get("model") or configured_model(self.cfg)
        return (b"Qubit Chat (agent-backed, no shell)\r\n"
                b"session: " + self.session_id.encode("utf-8") +
                b"  model: " + str(model).encode("utf-8") + b"\r\n" + self.prompt)

    def _submit(self, line: str) -> Iterator[bytes]:
        text = line.strip()
        if not text:
            yield self.prompt
            return
        yield b"\r\n"
        saw_delta = False
        for event in stream_turn(self.cfg, text, session=self.session_id,
                                 model=self.model):
            kind = event.get("event")
            if kind == "delta":
                saw_delta = True
                yield str(event.get("text", "")).encode("utf-8", "replace")
            elif kind == "error":
                if not saw_delta:
                    yield (b"\r\n[" + str(event.get("code", "ERROR")).encode() +
                           b"] " + str(event.get("detail", "")).encode(
                               "utf-8", "replace") + b"\r\n")
            elif kind == "cancelled":
                yield b"\r\n[cancelled]\r\n"
        yield b"\r\n" + self.prompt

    def feed(self, data: bytes) -> Iterator[bytes]:
        """Consume a keystroke frame and yield output bytes."""
        if not self.alive:
            return
        raw = bytes(data or b"")
        # Strip resize control sequences (wire compatibility with the fork).
        for match in _RESIZE_RE.finditer(raw):
            self.cols, self.rows = int(match.group(1)), int(match.group(2))
        raw = _RESIZE_RE.sub(b"", raw)
        i = 0
        while i < len(raw):
            ch = raw[i:i + 1]
            if ch in (b"\r", b"\n"):
                line, self._buf = self._buf, ""
                yield from self._submit(line)
            elif ch in (b"\x7f", b"\x08"):
                if self._buf:
                    self._buf = self._buf[:-1]
                    yield b"\x08 \x08"
            elif ch == b"\x03":
                self._buf = ""
                cancel(self.session_id)
                yield b"^C\r\n" + self.prompt
            elif ch == b"\x04":
                self.alive = False
                yield b"\r\n[session ended]\r\n"
                return
            else:
                try:
                    text = ch.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                if text and text.isprintable():
                    self._buf += text
                    yield ch
            i += 1


def pty_session(cfg: Any, session_id: str = DEFAULT_SESSION, *,
                model: Optional[str] = None, reset: bool = False
                ) -> "AgentPtySession":
    """Return the live PTY-shaped session for ``session_id`` (created on first
    use). ``reset=True`` discards any buffered state and starts fresh."""
    sid = str(session_id or DEFAULT_SESSION)[:80] or DEFAULT_SESSION
    try:
        scope = str(cfg.state_dir()) if hasattr(cfg, "state_dir") else "global"
    except Exception:
        scope = "global"
    key = f"{scope}\0{sid}"
    with _pty_lock:
        if reset or key not in _pty_sessions:
            _pty_sessions[key] = AgentPtySession(cfg, sid, model=model)
            if len(_pty_sessions) > _PTY_MAX:
                oldest = next(iter(_pty_sessions))
                _pty_sessions.pop(oldest, None)
        return _pty_sessions[key]


def pty_feed(cfg: Any, session_id: str, data_b64: str, *,
             model: Optional[str] = None, reset: bool = False) -> Dict[str, Any]:
    """One PTY-shaped frame: base64 input -> base64 output (request/response).

    The line buffer lives in the in-process :func:`pty_session` registry, so a
    keystroke sequence assembles across frames without any shell or OS PTY.
    ``reset=True`` starts a fresh session and returns the banner.
    """
    try:
        payload = base64.b64decode(data_b64 or "", validate=False)
    except Exception:
        return {"ok": False, "code": "MALFORMED", "detail": "input is not base64"}
    sess = pty_session(cfg, session_id, model=model, reset=reset)
    if reset or not (data_b64 or ""):
        return {"ok": True, "session_id": sess.session_id, "alive": sess.alive,
                "output": base64.b64encode(sess.banner()).decode("ascii")}
    body = b"".join(sess.feed(payload))
    return {"ok": True, "session_id": sess.session_id, "alive": sess.alive,
            "output": base64.b64encode(body).decode("ascii")}


__all__ = [
    "SCHEMA", "SESSION_SCHEMA", "DEFAULT_SESSION", "DEFAULT_MODEL",
    "ChatSessionStore", "AgentPtySession", "store", "turn", "retry_last",
    "stream_preflight", "stream_turn", "stream_sse", "sse_encode", "cancel",
    "resolve_model", "resolve_route", "configured_model", "pty_feed", "pty_session",
    "SESSION_AUTHORITY", "session_authority", "store_boundary",
    "owns_chat_session_authority", "draft_store", "comment_store",
    "build_projection", "record_activity",
]
