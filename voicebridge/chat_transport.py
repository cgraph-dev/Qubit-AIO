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
# Turn (non-streaming)
# ---------------------------------------------------------------------------
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
    routed = resolve_route(cfg, model, session=session, role=role)
    if not routed.get("ok"):
        return routed
    resolved_model = routed["model"]

    clean = _bounded((text or "").strip(), MAX_TEXT_CHARS)
    st = store(cfg)
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
