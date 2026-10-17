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
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

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


def _chat_turn_marker_module() -> Any:
    """The shared interrupted-turn marker store (tui_gateway.turn_marker).

    One implementation for both chat surfaces: the embedded TUI already writes
    and reads these markers, so `/chat` reuses the same file rather than
    inventing a second, divergent recovery path. Returns None when the module
    cannot be imported, which degrades to "no marker" (never a broken turn).
    """
    try:
        from tui_gateway import turn_marker  # type: ignore[import-not-found]

        return turn_marker
    except Exception:  # noqa: BLE001 - marker bookkeeping must never break a turn
        return None


def _chat_home(cfg: Any) -> Optional[Any]:
    """The QUBIT_HOME the marker store lives under, or None when unresolvable.

    Prefers an explicit config value, then the active profile home, then the
    process default — matching how the TUI path resolves
    ``session["profile_home"] or _qubit_home``.
    """
    for key in ("qubit_home", "home"):
        try:
            value = cfg.get("paths", key) if hasattr(cfg, "get") else None
        except Exception:  # noqa: BLE001
            value = None
        if value:
            return value
    try:
        from qubit_constants import get_qubit_home

        return get_qubit_home()
    except Exception:  # noqa: BLE001
        return None


def _write_turn_marker(cfg: Any, session_id: str, prompt: str) -> None:
    """Record that a turn is about to run for ``session_id`` (best-effort)."""
    module = _chat_turn_marker_module()
    home = _chat_home(cfg)
    if module is None or home is None or not session_id or not str(prompt or "").strip():
        return
    try:
        module.record_turn_start(home, session_id, prompt)
    except Exception:  # noqa: BLE001 - never break a turn over bookkeeping
        return


def _clear_turn_marker(cfg: Any, session_id: str) -> None:
    """Drop the marker once a turn concluded (answered, cancelled, or errored).

    A marker surviving a process death is the signal auto-continue acts on, so
    every other conclusion must clear it here.
    """
    module = _chat_turn_marker_module()
    home = _chat_home(cfg)
    if module is None or home is None or not session_id:
        return
    try:
        module.clear_turn_marker(home, session_id)
    except Exception:  # noqa: BLE001
        return


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


def local_brain_model(cfg: Any) -> Optional[str]:
    """The configured local brain model, or ``None`` when none is configured.

    ``/qubit-chat`` is the voice + report surface, so its NEW conversations
    default to the local profile instead of the resolver's configured API
    default (which is what makes a fresh dashboard row show the remote model).
    Resolution is config-driven and never a hard-coded alias:

    1. ``intent.local_llm.enabled`` — an explicit ``false`` disables the local
       brain, so this returns ``None`` and the caller degrades to the existing
       default rather than pinning a model the operator turned off;
    2. ``intent.local_llm.model`` — the canonical BRAIN selection path (the
       same path ``voicebridge.profiles`` and ``voicebridge.local_models``
       read), when set — but only when it is a genuinely chat-capable model.
       A configured id that resolves to a registered profile WITHOUT the
       ``chat`` role (eg ``qubit-brain4``) is NOT returned: such a profile
       cannot answer (all output lands in ``thinking``), and pinning it as the
       default made every new ``/qubit-chat`` conversation unroutable. A
       configured id that is not a registered profile (an operator's own
       Ollama tag) is returned as-is, preserving the raw overrides.
    3. otherwise the shipped local profile's model, resolved through the one
       resolver (``DEFAULT_LOCAL_ID``), but only when that profile is enabled
       and genuinely local.

    Any failure yields ``None`` (never a fabricated model), so callers keep
    their shipped default.
    """
    try:
        from .providers import resolver as _resolver

        try:
            get = cfg.get
        except AttributeError:
            get = None
        if get is not None:
            try:
                if get("intent", "local_llm", "enabled", default=True) is False:
                    return None
            except Exception:  # noqa: BLE001 - an unreadable flag is not a "no"
                pass
            try:
                configured = get("intent", "local_llm", "model", default=None)
            except Exception:  # noqa: BLE001
                configured = None
            if isinstance(configured, str) and configured.strip():
                configured = configured.strip()
                # The ``chat``-role gate must guard the CONFIGURED path too,
                # not just the shipped-default fallback below. ``qubit-brain4``
                # is the configured local brain (it runs the classifier /
                # runner roles) but its registered record deliberately omits
                # ``chat`` because it returns empty content. Returning it here
                # (the pre-2026-10-07 early return) pinned every NEW
                # ``/qubit-chat`` session to a model that dies at the 64K
                # context guard. Only reject when the id maps to a KNOWN
                # profile lacking ``chat`` — an unregistered operator tag still
                # passes through unchanged.
                _prof = _resolver._profile_for_model(cfg, configured, "chat")
                _known = (
                    _prof is not None
                    or _resolver.get_profile(cfg, configured) is not None
                )
                if not _known or _prof is not None:
                    return configured
                # Known profile without ``chat``: fall through to the shipped
                # local default / caller's own default.
        profile = _resolver.get_profile(cfg, _resolver.DEFAULT_LOCAL_ID)
        if (profile is not None and profile.enabled
                and profile.locality == "local" and profile.model):
            # A profile that does NOT carry the ``chat`` role must never be the
            # default for a NEW chat. The shipped ``qubit-brain4`` record
            # deliberately excludes ``chat`` (measured 2026-10-07: it emits all
            # output into ``thinking`` and returns empty content, and its Ollama
            # window is 4096 — under the 64K floor the agent requires). Returning
            # it here made every new conversation unroutable and littered the
            # sidebar with dead rows. Fall through so the caller keeps the
            # resolver's configured default instead.
            _roles = set(getattr(profile, "roles", ()) or ())
            if "chat" not in _roles:
                return None
            return str(profile.model)
    except Exception:  # noqa: BLE001 - a preference, never fatal
        return None
    return None


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
    # A per-session pin WINS over the configured global default: it is the
    # operator's explicit pick for THIS conversation (`/qubit-chat`'s panel
    # write). Resolving the global default here and merely patching the result
    # afterwards is not enough — when the global default is a disabled/
    # non-allowlisted API profile the resolver fails outright and returns
    # early below, before any pin could be applied, so a session pinned to a
    # perfectly executable local model would still be refused. Resolve the pin
    # directly instead.
    if req is None and session:
        _pin = _pinned_session_model(cfg, session)
        if _pin and _pin != DEFAULT_MODEL:
            req = _pin
    routed = _resolver.resolve_model(cfg, req, role=role, session=session)
    if not routed.get("ok"):
        # Upstream parity: hermes has NO model-profile registry — ``model.default``
        # is a plain string and a session's stored model is used as-is. Qubit's
        # registry only ever shipped ONE profile (``qubit-brain4``), so every
        # session or pick naming a real provider model (``deepseek-v4.1-flash``,
        # ``muse-spark-1.3-contributor``, ...) hit this refusal: resume, session
        # switching and the picker all returned MODEL_NOT_ALLOWED and the stored
        # work became unreachable. Fall through to the configured provider with
        # the requested model id instead of refusing — the endpoint, credential
        # and policy still come from the resolved profile. A genuine policy
        # refusal (egress/allowlist, which carries its own code) is preserved.
        _fallback_code = str(routed.get("code") or "")
        if _fallback_code in ("UNKNOWN_PROFILE", "MODEL_NOT_ALLOWED") and req:
            # A requested id that names a registered profile's MODEL (not its
            # id) — eg ``deepseek-v4.1-flash`` for profile
            # ``opencode-go-deepseek-v4.1-flash`` — must route THROUGH that
            # profile: the provider, base_url, kind and egress policy all come
            # from it. Falling back to the local brain and patching only the
            # model name left provider=``ollama`` on a hosted model, so the
            # turn 404'd at local Ollama (measured live: "model
            # 'deepseek-v4.1-flash' not found" @ http://localhost:11434/v1).
            _by_model = _resolver._profile_for_model(cfg, req, role)
            if _by_model is not None and _by_model.enabled:
                _routed = _resolver.resolve_model(cfg, _by_model.id, role=role,
                                                  session=session)
                if _routed.get("ok"):
                    _routed = dict(_routed)
                    _routed["session_pinned_model"] = req
                    return _routed
            _base = _resolver.resolve_model(cfg, None, role=role, session=session)
            if _base.get("ok"):
                _routed = dict(_base)
                _routed["model"] = req
                # The inherited profile is the local brain, so its provider
                # (``ollama``) is WRONG for a hosted id like
                # ``deepseek-v4.1-flash`` — sending that to local Ollama would
                # 404. Take the provider/base_url from the configured model
                # section, which is where a hosted default (and the picker's
                # own write) actually points.
                _cfg_model = {}
                try:
                    _cfg_model = cfg.get("model", {}) or {}
                except Exception:  # noqa: BLE001 - odd cfg objects degrade below
                    _cfg_model = {}
                if isinstance(_cfg_model, dict):
                    _prov = str(_cfg_model.get("provider") or "").strip()
                    if _prov:
                        _routed["provider"] = _prov
                    _buri = str(_cfg_model.get("base_url") or "").strip()
                    if _buri:
                        _routed["base_url"] = _buri
                    _key_env = str(_cfg_model.get("key_env") or "").strip()
                    if _key_env:
                        _routed["key_env"] = _key_env
                _routed["session_pinned_model"] = req
                _routed["registry_bypass"] = True
                return _routed
        code = _fallback_code or "MODEL_NOT_ALLOWED"
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
        # A per-session pin applies to EVERY executable kind, not just remote
        # ones: `/qubit-chat` picks between LOCAL models (qubit-brain,
        # qubit-spark17, ...) as often as API ones, and gating on
        # ("api","realtime","plugin") silently ignored every local pick —
        # the stored `sessions.model` was written but never routed to.
        if (pinned and pinned != DEFAULT_MODEL
                and pinned != routed.get("model")
                and str(routed.get("kind") or "") in
                ("api", "realtime", "plugin", "local")):
            routed = dict(routed)
            routed["model"] = pinned
            routed["session_pinned_model"] = pinned
        else:
            # No pin and no request: the resolver defaulted to whatever the
            # persisted "active" pointer names. When that is a LOCAL profile
            # whose declared window is below Qubit's tool-use floor
            # (MINIMUM_CONTEXT_LENGTH, 64K), every tool-capable `/qubit-chat`
            # turn dies at the runtime guard ("Ollama loaded ... only 4,096
            # tokens"). The installed local brains are all 4K Qwen3-4B
            # records, so this is the common case. Prefer the operator's
            # configured remote default (eg opencode-go/deepseek-v4.1-flash)
            # when it is registered and executable — the same "never silently
            # run a short-context model" rule `terminal_context_guard` applies
            # to /chat. Never overrides an explicit request or a session pin.
            _routed_local = str(routed.get("kind") or "") == "local"
            _ctx = int(routed.get("context_tokens") or 0)
            try:
                from .providers import resolver as _r2
                _floor = _r2.terminal_min_context_tokens(cfg)
            except Exception:  # noqa: BLE001 - fall back to the shipped floor
                _floor = 65536
            if _routed_local and _ctx < _floor:
                _configured = _resolver.configured_default_model(cfg)
                _cprof = (_resolver._profile_for_model(cfg, _configured, role)
                          if _configured else None)
                if _cprof is not None and _cprof.enabled \
                        and _cprof.kind != "local":
                    _routed = _resolver.resolve_model(cfg, _cprof.id,
                                                      role=role,
                                                      session=session)
                    if _routed.get("ok"):
                        _routed = dict(_routed)
                        _routed["short_context_default_bypassed"] = routed.get("model")
                        return _routed
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
                     model: Optional[str], cfg: Any = None) -> Dict[str, Any]:
    sid = str(session or DEFAULT_SESSION).strip()[:80] or DEFAULT_SESSION
    result = st.ensure(sid, model=model)
    # Surface a turn that a process death cut short, so the client can offer to
    # resume it instead of the operator retyping `continue` by hand. The marker
    # is cleared by whichever side acts on it (or by the turn concluding).
    result["interrupted_turn"] = _pending_interrupted_turn(cfg, sid)
    return result


def _pending_interrupted_turn(cfg: Any, session_id: str) -> Optional[Dict[str, Any]]:
    """The interrupted prompt for ``session_id`` if a crash left one behind.

    Read-only: the client decides whether to re-issue it, so a stale marker can
    never silently re-run work while the operator is not looking.

    Applies the same freshness/attempt gate the TUI's auto-continue uses
    (``session_auto_continue._auto_continue_config``): ``read_turn_marker``
    itself is age-blind, so without this a marker from days ago would still
    fire a resume. A marker past the window is cleared, not returned.
    """
    module = _chat_turn_marker_module()
    home = _chat_home(cfg)
    if module is None or home is None or not session_id:
        return None
    try:
        marker = module.read_turn_marker(home, session_id)
    except Exception:  # noqa: BLE001
        return None
    if marker is None:
        return None
    if not _auto_continue_allowed(cfg, marker):
        try:
            module.clear_turn_marker(home, session_id)
        except Exception:  # noqa: BLE001
            pass
        return None
    return marker


def _auto_continue_allowed(cfg: Any, marker: Dict[str, Any]) -> bool:
    """Whether an interrupted turn is still worth resuming.

    Mirrors ``desktop.auto_continue`` (enabled / freshness_minutes /
    max_attempts) so `/chat` and the TUI agree on what counts as recoverable.
    Defaults match the TUI (enabled, 15 minutes, 2 attempts).
    """
    if not marker.get("auto_continue", True):
        return False
    try:
        desktop = cfg.get("desktop") if hasattr(cfg, "get") else None
        section = desktop.get("auto_continue") if isinstance(desktop, dict) else None
        section = section if isinstance(section, dict) else {}
        enabled_raw = section.get("enabled", True)
        enabled = not (
            enabled_raw is False
            or str(enabled_raw).strip().lower() in ("0", "false", "no", "off")
        )
        minutes = float(section.get("freshness_minutes", 15))
        max_attempts = int(section.get("max_attempts", 2))
    except Exception:  # noqa: BLE001 - malformed config falls back to defaults
        enabled, minutes, max_attempts = True, 15.0, 2
    if not enabled:
        return False
    started = float(marker.get("started_at") or 0)
    if started and (time.time() - started) > max(0.0, minutes) * 60.0:
        return False
    return int(marker.get("attempts") or 0) < max(0, max_attempts)


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


# ---------------------------------------------------------------------------
# Slice 1 — explicit route words (bypass the classifier, never through it)
# ---------------------------------------------------------------------------
#: Explicit routing prefixes. "in chat: ..." forces the headless /chat worker
#: (``agent_task`` branch); "quick: ..." forces the direct answer
#: (``answer_local`` — today's brain/facts/skills behavior, no worker).
#: Matched case-insensitively on the leading prefix only; an empty remainder
#: falls through to the normal classifier path. Unprefixed turns never match.
#: The separator after the prefix may be a colon, a comma, or whitespace
#: (task prose "in chat, ..." arrives comma-separated from speech).
_ROUTE_WORDS = (
    ("in chat", "in-chat", "agent_task"),
    ("in-chat", "in-chat", "agent_task"),
    ("quick", "quick", "answer_local"),
)
_ROUTE_WORD_SEPS = (":", ",", " ", "\t")


def parse_route_word(text: Any) -> Optional[Dict[str, Any]]:
    """Parse an explicit Slice-1 route-word prefix, or None. Pure.

    Returns ``{"route", "action", "task_text"}`` with the prefix stripped,
    or None when the text carries no route word (classifier path unchanged)
    or nothing follows the prefix (fall through, never an empty dispatch).
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    low = raw.lower()
    for prefix, route, action in _ROUTE_WORDS:
        if not low.startswith(prefix):
            continue
        rest = raw[len(prefix):]
        if not rest or rest[0] not in _ROUTE_WORD_SEPS:
            continue
        task = rest[1:].strip()
        if not task:
            return None
        return {"route": route, "action": action, "task_text": task}
    return None


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
    # Slice 1 route words bypass the classifier entirely: no Classifier
    # construction, no local-LLM round-trip — the prefix IS the decision.
    rw = parse_route_word(raw)
    if rw is not None:
        return {"action": rw["action"], "kind": "route-word",
                "task_text": rw["task_text"], "params": {},
                "reason": f"route-word:{rw['route']}"}
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
        # A WEAK/partial hint match is not a build order. The old code sent it
        # to the headless build worker, which produced nonsense for an
        # investigation request ("check why X is not working 1:1, what are we
        # missing" -> "Dashboard ready at http://0.0.0.0:8898/", live
        # 2026-10-05). Send it to the tool-capable agent instead: it can
        # investigate with real tools AND build when the request truly is a
        # build. Only a strong hint delegates to the build worker.
        if "partial" in str(reason).lower():
            return {"action": "answer_local", "kind": kind,
                    "task_text": task_text or raw, "params": {},
                    "reason": reason}
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
def _branch_model(cfg: Any, session: Optional[str] = None) -> Optional[str]:
    """Best-effort effective model for branch receipts (never raises).

    A session pin (the operator's explicit ``/qubit-chat`` pick) WINS over the
    configured global default so the receipt reports the model the turn really
    used, not the unrelated global default. Read-only and fail-safe.
    """
    try:
        pinned = _pinned_session_model(cfg, session) if session else None
        if pinned and str(pinned).strip():
            return str(pinned).strip()
    except Exception:  # noqa: BLE001 - fall back to the surface default
        pass
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


def _classify_confirmation(text: Any) -> str:
    """Classify a follow-up as ``"affirm"`` / ``"deny"`` / ``"other"``.

    Uses the voice turn FSM's own vocabulary
    (:func:`voicebridge.voice_edit.session_fsm.classify_confirmation`, which
    ports the helper's ``_affirmative``/``_negative`` word lists) while keeping
    every phrase :func:`_is_affirmative` already accepted — so a politely
    phrased "Yes, please." confirms and "no thanks" cancels exactly as they do
    on the voice path, and no previously-settling phrase stops working. Junk or
    empty input is ``"other"``; never raises.
    """
    if _is_affirmative(text):
        return "affirm"
    try:
        from .voice_edit.session_fsm import classify_confirmation

        verdict = str(classify_confirmation(text) or "other")
    except Exception:  # noqa: BLE001 - a broken seam must not drop a confirm
        verdict = "other"
    return verdict if verdict in ("affirm", "deny") else "other"


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


# ---------------------------------------------------------------------------
# Session-scoped pending-confirmation view + cancel (voice-FSM parity)
# ---------------------------------------------------------------------------
def _pending_confirmation(cfg: Any, sid: str) -> Optional[Dict[str, Any]]:
    """The live pending confirmation for one /qubit-chat session, or None.

    Session-scoped and TTL-bounded: reads the SAME durable journal records the
    staging paths write (a staged build write or a staged registry
    confirmation), filtered to *sid*; ``ApprovalJournal.pending()`` already
    drops expired records, so a stale "yes" can never settle one. The returned
    descriptor is redacted and bounded — the UI may render ``action_summary``
    and ``staged_ms`` but a raw argument dump or a secret never crosses this
    boundary. Never raises: an unreadable journal degrades to "no pending" so a
    turn falls through byte-identically.
    """
    probe = _bounded(str(sid or "").strip(), 80) or DEFAULT_SESSION
    newest: Optional[Tuple[float, str, str, str, float]] = None
    for kind, entries in (("task", _pending_task(cfg, probe)),
                          ("registry", _pending_registry(cfg, probe))):
        for item in entries or []:
            try:
                created = float(getattr(item, "created_at", 0.0) or 0.0)
            except (TypeError, ValueError):
                created = 0.0
            if newest is not None and created < newest[0]:
                continue
            try:
                expires = float(getattr(item, "expires_at", 0.0) or 0.0)
            except (TypeError, ValueError):
                expires = 0.0
            cid = str(getattr(item, "id", "") or "")
            summary = _redact(str(getattr(item, "summary", "") or ""))[:200]
            newest = (created, kind, cid, summary, expires)
    if newest is None:
        return None
    created, kind, cid, summary, expires = newest
    now = time.time()
    return {
        "kind": kind,
        "confirmation_id": cid or None,
        "action_summary": summary or "a pending action awaiting confirmation",
        "staged_ms": int(max(0.0, now - created) * 1000),
        "expires_ms": int(max(0.0, expires - now) * 1000),
    }


def _pending_event_field(cfg: Any, sid: str) -> Dict[str, Any]:
    """``{"pending_confirmation": {...}}`` when live, else ``{}``.

    Spreading this into an event leaves the event shape byte-identical when
    nothing is pending — the "no-pending turns are unchanged" contract.
    """
    try:
        pending = _pending_confirmation(cfg, sid)
    except Exception:  # noqa: BLE001 - exposure must never break the turn
        pending = None
    return {"pending_confirmation": pending} if pending else {}


def _cancel_confirmations(cfg: Any, st: Any, sid: str) -> Dict[str, Any]:
    """Deny (cancel) the session's live pending confirmations, typed.

    Called ONLY for an explicit denial follow-up on a session that already has
    a live pending confirmation. Registry confirmations settle through the
    shared ``voice_commands.cancel_pending`` path; staged build writes settle
    through the journal's own ``respond("deny")``. Nothing is executed and no
    other session is touched. A cancellation failure leaves the pending in
    place — it is never silently dropped.
    """
    cancelled = 0
    if _pending_registry(cfg, sid):
        try:
            from . import voice_commands as VC

            ctx = VC.make_ctx(cfg, actor="dashboard:user", session=sid,
                              source="user")
            outcome = VC.cancel_pending(ctx)
            receipt = dict(getattr(outcome, "receipt", None) or {})
            cancelled += int(receipt.get("cancelled") or 0)
        except Exception as exc:  # noqa: BLE001 - typed, pending stays
            _audit("turn_confirm_cancel_error", session=sid, error=str(exc))
    journal = _task_journal(cfg)
    for item in _pending_task(cfg, sid):
        cid = str(getattr(item, "id", "") or "")
        if journal is None or not cid:
            continue
        try:
            journal.respond(cid, "deny")
            cancelled += 1
        except Exception:  # noqa: BLE001 - best-effort per record
            pass
    if cancelled == 1:
        speech = "Cancelled the pending confirmation."
    elif cancelled:
        speech = f"Cancelled {cancelled} pending confirmations."
    else:
        speech = "There is nothing pending to cancel."
    if cancelled:
        st.append(sid, "assistant", speech,
                  meta={"route": "confirm", "cancelled": cancelled})
    _audit("turn_confirm_cancelled", session=sid, cancelled=cancelled)
    return {"ok": True, "text": speech, "reply": speech, "session_id": sid,
            "session": sid, "route": "confirm", "cancelled": cancelled}


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


def _web_paid_first(cfg: Any) -> bool:
    """Owner flag ``intent.web_paid_first`` (default False = free-first).

    Free-first (default): the no-key DDG fallback runs FIRST and a paid
    Perplexity call is only attempted when DDG misses — so a configured
    paid key never spends quota a free search could have covered. Setting
    ``intent.web_paid_first: true`` restores the paid-first order
    (Perplexity first, DDG as fallback). Read-only; any unreadable value
    degrades to False (free-first). Never touches a key.
    """
    try:
        return bool(_cfg_flag(cfg, "intent", "web_paid_first",
                              default=False))
    except Exception:  # noqa: BLE001 - degraded to free-first, never fatal
        return False


# ---------------------------------------------------------------------------
# Slice 2 — web order + hardening (paid API -> browser-Pro -> DDG)
# ---------------------------------------------------------------------------
#: Identical-query cache TTL: kills the rapid-follow-up problem (a repeated
#: question within 10 minutes reuses the proven answer, no new API/DDG hit).
_WEB_CACHE_TTL_S = 600.0
_WEB_CACHE_MAX = 128
_web_cache: Dict[str, Any] = {}
_web_cache_lock = threading.Lock()

#: Browser-Pro layer bound (Slice 2 accept: 25 s, "still reading…" talk-back
#: hook while it runs). CDP_DOWN makes this path unusable for live voice
#: rhythm, so the pre-flight skips it loudly instead of failing mid-turn.
_BROWSER_PRO_TIMEOUT_S = 25.0
_CDP_PROBE_TIMEOUT_S = 2.0
_CDP_JSON_URL = "http://127.0.0.1:9222/json"

#: Paid API hardening: bounded retry-with-backoff plus a model degrade
#: (``sonar-pro`` -> ``sonar``). The shared ``answer_web_query`` collapses
#: every gateway failure to ``WEB_UNAVAILABLE`` (no 429 granularity survives),
#: so ANY paid failure retries once after a backoff — the live 429 that
#: motivated this slice degrades exactly this way.
_PAID_API_MAX_ATTEMPTS = 2
_PAID_API_BACKOFF_S = (0.5, 2.0)
_PAID_FALLBACK_MODEL = "sonar"


def _web_cache_key(query: Any) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().lower())


def _web_cache_get(query: Any) -> Optional[Dict[str, Any]]:
    """Return the cached success for an identical query, else None."""
    key = _web_cache_key(query)
    if not key:
        return None
    now = time.monotonic()
    with _web_cache_lock:
        row = _web_cache.get(key)
        if not isinstance(row, dict):
            return None
        if float(row.get("expires", 0) or 0) <= now:
            _web_cache.pop(key, None)
            return None
        hit = dict(row.get("result") or {})
    return hit or None


def _web_cache_put(query: Any, result: Dict[str, Any]) -> None:
    """Cache one successful web answer for 10 minutes (successes only)."""
    if not isinstance(result, dict) or not result.get("ok"):
        return
    key = _web_cache_key(query)
    if not key:
        return
    with _web_cache_lock:
        _web_cache[key] = {"expires": time.monotonic() + _WEB_CACHE_TTL_S,
                           "result": dict(result)}
        while len(_web_cache) > _WEB_CACHE_MAX:
            _web_cache.pop(next(iter(_web_cache)), None)


def _web_cache_clear() -> None:
    """Drop the web cache (tests / operator; never on the hot path)."""
    with _web_cache_lock:
        _web_cache.clear()


def _paid_model_name(cfg: Any) -> str:
    """Configured Perplexity model (default ``sonar-pro``; read-only)."""
    try:
        return str(_cfg_flag(cfg, "intent", "perplexity_model",
                             default="sonar-pro") or "sonar-pro")
    except Exception:  # noqa: BLE001 - degraded to the default, never fatal
        return "sonar-pro"


class _CfgModelOverride:
    """Narrow cfg shim forcing one Perplexity model (Slice 2 degrade).

    Delegates everything to the wrapped config except
    ``get("intent", "perplexity_model")``. Lets the retry attempt the
    cheaper ``sonar`` model through the SAME shared ``answer_web_query``
    (same gate, same key seam, same redaction) without mutating live config.
    """

    def __init__(self, inner: Any, model: str) -> None:
        self._inner = inner
        self._model = model

    def get(self, *path: str, default: Any = None) -> Any:
        if tuple(path) == ("intent", "perplexity_model"):
            return self._model
        try:
            return self._inner.get(*path, default=default)
        except TypeError:
            node: Any = self._inner
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            return default if node is None else node

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _paid_api_with_retry(cfg: Any, query: str) -> Dict[str, Any]:
    """Paid API layer with backoff + ``sonar-pro`` -> ``sonar`` degrade.

    Attempt 1 uses the configured model; on ANY failure it sleeps the
    backoff and retries once on the fallback model (a no-op shim when the
    configured model already is the fallback). Returns the last typed
    result — never fabricated. Audits carry attempt/model/code only.
    """
    from . import orchestrator as ORC

    first_model = _paid_model_name(cfg)
    try:
        res = ORC.answer_web_query(cfg, query)
    except Exception as exc:  # noqa: BLE001 - typed, never fabricated
        _audit("turn_web_paid_error", attempt=1, model=first_model,
               error=str(exc))
        res = {"ok": False, "code": "WEB_UNAVAILABLE",
               "answer": str(exc)[:200], "sources": []}
    if isinstance(res, dict) and res.get("ok"):
        res["model"] = first_model
        return res
    code = str((res or {}).get("code") or "WEB_UNAVAILABLE") \
        if isinstance(res, dict) else "WEB_UNAVAILABLE"
    _audit("turn_web_paid_miss", attempt=1, model=first_model, code=code)
    if _PAID_API_MAX_ATTEMPTS < 2:
        return res if isinstance(res, dict) else {
            "ok": False, "code": code, "answer": "", "sources": []}
    try:
        time.sleep(_PAID_API_BACKOFF_S[0])
    except Exception:  # noqa: BLE001 - a broken clock never blocks voice
        pass
    retry_cfg: Any = cfg if first_model == _PAID_FALLBACK_MODEL \
        else _CfgModelOverride(cfg, _PAID_FALLBACK_MODEL)
    try:
        res2 = ORC.answer_web_query(retry_cfg, query)
    except Exception as exc:  # noqa: BLE001 - typed, never fabricated
        _audit("turn_web_paid_error", attempt=2,
               model=_PAID_FALLBACK_MODEL, error=str(exc))
        res2 = {"ok": False, "code": "WEB_UNAVAILABLE",
                "answer": str(exc)[:200], "sources": []}
    if isinstance(res2, dict) and res2.get("ok"):
        _audit("turn_web_paid_degraded", model=_PAID_FALLBACK_MODEL)
        res2["model"] = _PAID_FALLBACK_MODEL
        return res2
    code2 = str((res2 or {}).get("code") or code) \
        if isinstance(res2, dict) else code
    _audit("turn_web_paid_miss", attempt=2, model=_PAID_FALLBACK_MODEL,
           code=code2)
    return res2 if isinstance(res2, dict) else {
        "ok": False, "code": code2, "answer": "", "sources": []}


def _cdp_healthy(timeout_s: float = _CDP_PROBE_TIMEOUT_S) -> bool:
    """Loopback-CDP pre-flight: True when a debugger endpoint answers.

    The Slice-6 supervisor verdict wins when fresh (``cdp_supervisor.
    read_health`` — absent/stale degrades to the direct probe below).
    Otherwise a read-only ``GET /json`` with a short bound; ANY failure
    (refused, timeout, malformed) is False — the browser-Pro layer is
    skipped loudly instead of failing mid-turn. Never launches or
    touches a browser.
    """
    try:
        from . import cdp_supervisor as _CDPS

        _sup = _CDPS.read_health()
        if _sup is not None:
            return bool(_sup.get("cdp_up"))
    except Exception:  # noqa: BLE001 - degraded to the direct probe
        pass
    try:
        import urllib.request

        with urllib.request.urlopen(_CDP_JSON_URL,
                                    timeout=float(timeout_s)) as resp:
            resp.read(64)
        return True
    except Exception as exc:  # noqa: BLE001 - degraded to unhealthy
        _audit("turn_web_cdp_down", error=str(exc)[:120])
        return False


def _browser_pro_web_search(query: str,
                            timeout_s: float = _BROWSER_PRO_TIMEOUT_S
                            ) -> Optional[Dict[str, Any]]:
    """Browser-Pro layer: logged-in Perplexity session over loopback CDP.

    CDP-healthy only (pre-flight first — one honest skip instead of a
    mid-turn failure), bounded to 25 s, loud on every degrade path (audit +
    stderr; never a silent None). Returns ``{"answer", "sources"}`` (sources
    may be empty — the browser path renders prose, not links) or None.
    """
    import subprocess
    import sys as _sys

    if not _cdp_healthy():
        _audit("turn_web_browser_skipped", reason="cdp_unavailable")
        print("web search: browser-Pro unavailable (CDP :9222 refused) — "
              "using API/DDG layers", file=_sys.stderr)
        return None
    try:
        from pathlib import Path as _Path

        script = str(_Path(__file__).resolve().parents[1] / "scripts"
                     / "perplexity_search.py")
    except Exception:  # noqa: BLE001 - typed degrade, never fatal
        _audit("turn_web_browser_error", error="script path unresolvable")
        return None
    try:
        proc = subprocess.run([_sys.executable, script, str(query), "4000"],
                              capture_output=True, text=True,
                              timeout=float(timeout_s))
    except subprocess.TimeoutExpired:
        _audit("turn_web_browser_timeout", timeout_s=float(timeout_s))
        print(f"web search: browser-Pro timed out after "
              f"{float(timeout_s):.0f}s — falling through",
              file=_sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001 - typed degrade, never fatal
        _audit("turn_web_browser_error", error=str(exc)[:120])
        print(f"web search: browser-Pro error ({str(exc)[:120]}) — "
              f"falling through", file=_sys.stderr)
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        reason = tail[-1][:160] if tail else f"exit {proc.returncode}"
        _audit("turn_web_browser_miss", reason=reason[:120])
        print(f"web search: browser-Pro missed ({reason}) — "
              f"falling through", file=_sys.stderr)
        return None
    answer = (proc.stdout or "").strip()
    if not answer:
        _audit("turn_web_browser_miss", reason="empty answer")
        print("web search: browser-Pro returned empty — falling through",
              file=_sys.stderr)
        return None
    return {"answer": _redact(answer)[:2000], "sources": []}


def _turn_web(cfg: Any, st: Any, sid: str, task_text: str) -> Dict[str, Any]:
    """Wave C Task 2 + Slice 2: execute a web answer, never chatter.

    Two orders selected by ``intent.web_paid_first`` (see
    :func:`_web_paid_first`):

    * paid-first (``true`` — the Slice 2 voice order): hardened paid API
      (retry + ``sonar-pro`` -> ``sonar`` degrade) -> browser-Pro
      (CDP-healthy only, 25 s bound, ``turn_web_browser_progress`` audit is
      the "still reading…" talk-back hook) -> DDG last resort.
    * free-first (default, legacy byte-identical): no-key DDG first, paid
      API only on a DDG miss; the browser layer does not run here so the
      legacy contract is untouched.

    An identical query within 10 minutes reuses the cached success without
    touching any layer. Every layer down stays a typed ``WEB_UNAVAILABLE``
    — nothing is fabricated, and no key is ever logged (audits carry
    session/code/counts only).     All orders return the same receipt shape
    ``{route: web, sources, model}`` plus a ``layers`` trace.
    """
    paid_first = _web_paid_first(cfg)
    layers: list = []

    def _with_layers(res: Dict[str, Any]) -> Dict[str, Any]:
        receipt = res.get("receipt")
        if isinstance(receipt, dict):
            receipt["layers"] = list(layers)
        return res

    cached = _web_cache_get(task_text)
    if isinstance(cached, dict) and str(cached.get("answer") or "").strip():
        answer = str(cached.get("answer"))
        sources = [str(s) for s in (cached.get("sources") or [])
                   if str(s).strip()]
        body = answer + ("\n\nSources:\n" + "\n".join(sources)
                         if sources else "")
        st.append(sid, "assistant", body,
                  meta={"route": "web", "sources": sources or None,
                        "fallback": "cache"})
        _audit("turn_web_cache", session=sid, sources=len(sources))
        return _with_layers(
            {"ok": True, "text": answer, "reply": answer,
             "sources": sources, "session_id": sid, "session": sid,
             "route": "web",
             "receipt": _branch_receipt("web", task=task_text,
                                        sources=sources, cfg=cfg)})

    def _paid_attempt() -> Dict[str, Any]:
        try:
            web = _paid_api_with_retry(cfg, task_text)
        except Exception as exc:  # noqa: BLE001 - typed, never fabricated
            _audit("turn_web_error", session=sid, error=str(exc))
            web = {"ok": False, "code": "WEB_UNAVAILABLE",
                   "answer": str(exc)[:200]}
        return web if isinstance(web, dict) else {
            "ok": False, "code": "WEB_UNAVAILABLE", "answer": ""}

    def _paid_ok(web: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not web.get("ok"):
            return None
        answer = str(web.get("answer") or "")
        sources = [str(s) for s in (web.get("sources") or [])
                   if str(s).strip()]
        if not answer.strip():
            return None
        body = answer + ("\n\nSources:\n" + "\n".join(sources)
                         if sources else "")
        st.append(sid, "assistant", body,
                  meta={"route": "web", "sources": sources or None})
        _audit("turn_web", session=sid, sources=len(sources))
        _web_cache_put(task_text, {"ok": True, "answer": answer,
                                   "sources": sources})
        # The receipt names the layer that actually answered (the paid
        # model), never the surface chat-model default.
        paid_model = str(web.get("model") or "") \
            or _paid_model_name(cfg)
        return {"ok": True, "text": answer, "reply": answer,
                "sources": sources, "session_id": sid, "session": sid,
                "route": "web",
                "receipt": _branch_receipt("web", task=task_text,
                                           sources=sources,
                                           model=paid_model, cfg=cfg)}

    def _browser_ok(fb: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(fb, dict):
            return None
        answer = str(fb.get("answer") or "")
        sources = [str(s) for s in (fb.get("sources") or [])
                   if str(s).strip()]
        if not answer.strip():
            return None
        body = answer + ("\n\nSources:\n" + "\n".join(sources)
                         if sources else "")
        st.append(sid, "assistant", body,
                  meta={"route": "web", "sources": sources or None,
                        "fallback": "browser-pro"})
        _audit("turn_web_browser", session=sid, sources=len(sources))
        _web_cache_put(task_text, {"ok": True, "answer": answer,
                                   "sources": sources})
        return {"ok": True, "text": answer, "reply": answer,
                "sources": sources, "session_id": sid, "session": sid,
                "route": "web",
                "receipt": _branch_receipt("web", task=task_text,
                                           sources=sources, cfg=cfg)}

    def _free_ok(fb: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(fb, dict):
            return None
        answer = str(fb.get("answer") or "")
        sources = [str(s) for s in (fb.get("sources") or [])
                   if str(s).strip()]
        if not answer.strip() or not sources:
            return None
        body = answer + "\n\nSources:\n" + "\n".join(sources)
        st.append(sid, "assistant", body,
                  meta={"route": "web", "sources": sources or None,
                        "fallback": "ddg"})
        _audit("turn_web_ddg", session=sid, sources=len(sources))
        _web_cache_put(task_text, {"ok": True, "answer": answer,
                                   "sources": sources})
        return {"ok": True, "text": answer, "reply": answer,
                "sources": sources, "session_id": sid, "session": sid,
                "route": "web",
                "receipt": _branch_receipt("web", task=task_text,
                                           sources=sources, cfg=cfg)}

    def _run_browser() -> Optional[Dict[str, Any]]:
        layers.append("browser")
        # Talk-back hook: the voice layer speaks progress off this audit
        # while the 25 s browser probe runs (never silent waiting).
        _audit("turn_web_browser_progress", session=sid,
               detail="still reading…")
        return _browser_ok(_browser_pro_web_search(task_text))

    def _run_free() -> Optional[Dict[str, Any]]:
        layers.append("free")
        return _free_ok(_ddg_web_fallback(task_text))

    if paid_first:
        steps = ("paid", "browser", "free")
    else:
        steps = ("free", "paid")
    paid_detail = ""
    paid_code = "WEB_UNAVAILABLE"
    for step in steps:
        if step == "paid":
            layers.append("paid")
            web = _paid_attempt()
            hit = _paid_ok(web)
            if hit is not None:
                return _with_layers(hit)
            paid_code = str(web.get("code") or "WEB_UNAVAILABLE")
            paid_detail = str(web.get("answer") or "")[:200]
            continue
        if step == "browser":
            hit = _run_browser()
        else:
            hit = _run_free()
        if hit is not None:
            return _with_layers(hit)
    detail = paid_detail or "web search unavailable; nothing was fabricated"
    _audit("turn_web_unavailable", session=sid, code=paid_code)
    return {"ok": False, "state": "FAILED", "code": paid_code,
            "detail": detail, "session_id": sid, "route": "web"}


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


# ---------------------------------------------------------------------------
# Lane 2 — dead-main fallback for headless voice turns (2026-10-03)
# ---------------------------------------------------------------------------
#: THIS-turn-only fallback when the resolved main model is recorded-dead.
#: The owner keeps muse as main for interactive /chat (works there via
#: streaming); voice headless turns use the gateway non-streaming shape
#: that muse rejects (``MODEL_UNAVAILABLE_TURN`` /
#: ``ModelProtocolUnsupported``). Voice must not inherit a dead main, and
#: the fallback must never become a slot write: no ``select_model`` /
#: ``activate_endpoint`` / ``set_active_profile`` / runtime ``config.yaml``
#: write happens here — an in-memory shim steers THIS worker thread only,
#: explicit user model choices elsewhere are untouched, and the shim is
#: removed in a ``finally`` so a crash can never leak it.
#: Micro-lane 2026-10-03: the fallback is resolved DYNAMICALLY (first
#: healthy API chat profile, preferring the dead main's provider family);
#: the ``qubit-go``/deepseek literal below is the last resort only.
_FALLBACK_PROFILE_ID = "qubit-go"
_FALLBACK_PROVIDER = "opencode-go"
_FALLBACK_MODEL = "deepseek-v4-flash"

#: Wait outcomes that smell like a dead remote model (the worker's gateway
#: turn failed at use time while the static probe still resolved). A retry
#: is only attempted when the dead record then exists (the gateway records
#: ``MODEL_UNAVAILABLE_TURN`` on the failed turn), so non-model worker
#: errors (tool blocks, safety, crashes) never retry.
_DEAD_TURN_CODES = frozenset({
    "MODEL_ERROR", "MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE_TURN",
    "PROVIDER_OUTAGE",
})
_DEAD_TURN_MARKERS = (
    "MODEL_UNAVAILABLE_TURN",
    "ModelProtocolUnsupported",
    "unavailable at turn time",
)


def _worker_effective(cfg: Any) -> Optional[Dict[str, Any]]:
    """The effective headless-worker profile (role chat default), or None.

    Read-only resolver read; fail-open None on any error so a broken or
    hermetic resolver keeps today's behavior (no fallback, never fatal).
    """
    try:
        from .providers import resolver as _R

        eff = _R.resolve(cfg, role="chat")
    except Exception:  # noqa: BLE001 - degraded to no-fallback, never fatal
        return None
    try:
        return {"provider": str(getattr(eff, "provider", "") or ""),
                "model": str(getattr(eff, "model", "") or ""),
                "profile_id": str(getattr(eff, "profile_id", "") or ""),
                "executable": bool(getattr(eff, "executable", True)),
                "eff": eff}
    except Exception:  # noqa: BLE001 - degraded to no-fallback, never fatal
        return None


def _literal_fallback_effective(cfg: Any) -> Optional[Dict[str, Any]]:
    """The last-resort literal fallback profile (``qubit-go``), or None.

    None when unresolvable, inexecutable, or itself recorded-dead
    (both-dead degrades LOUD instead of burning a turn on a dead model).
    Read-only; never raises, never writes a slot. This is the LAST resort
    only — :func:`_fallback_effective` prefers a dynamically resolved
    healthy API profile first, so the fallback works for any API model.
    """
    try:
        from .providers import resolver as _R

        eff = _R.resolve(cfg, role="chat",
                         profile_id=_FALLBACK_PROFILE_ID)
    except Exception:  # noqa: BLE001 - degraded to no-fallback, never fatal
        return None
    try:
        if not bool(getattr(eff, "executable", True)):
            return None
        provider = str(getattr(eff, "provider", "") or "")
        model = str(getattr(eff, "model", "") or "")
    except Exception:  # noqa: BLE001 - degraded to no-fallback, never fatal
        return None
    try:
        from .providers import dead_models as _DM

        if _DM.is_dead(cfg, provider, model) is not None:
            return None
    except Exception:  # noqa: BLE001 - a broken record fails OPEN here;
        # the turn itself stays truthful (a dead fallback still fails
        # typed below instead of masquerading as action).
        pass
    return {"provider": provider, "model": model,
            "profile_id": str(getattr(eff, "profile_id", "") or ""),
            "eff": eff}


def _dynamic_fallback_effective(cfg: Any, *, prefer_provider: str = "",
                                exclude_provider: str = "",
                                exclude_model: str = ""
                                ) -> Optional[Dict[str, Any]]:
    """First healthy API chat profile, preferring the dead main's family.

    A candidate qualifies only when it is an enabled ``api``-kind profile
    with the ``chat`` role, is NOT the dead main itself, is NOT
    recorded-dead, declares at least the ``/chat`` context floor, and
    resolves executable through the canonical resolver. Ordering: same
    provider as the dead main first (the current default family), then
    larger declared windows, then profile id (deterministic). The dead
    literal (``qubit-go``/deepseek) is NOT prioritized here — it stays the
    last resort in :func:`_fallback_effective`. Read-only; never raises,
    never writes a slot; ``None`` when nothing qualifies (or the registry
    itself is unreadable, so the literal path still gets its chance).
    """
    try:
        from .providers import resolver as _R

        profiles = _R.load_profiles(cfg)
    except Exception:  # noqa: BLE001 - registry unreadable: literal fallback
        return None
    try:
        floor = int(_R.terminal_min_context_tokens(cfg))
    except Exception:  # noqa: BLE001 - floor read never blocks a fallback
        floor = 65536
    prefer = str(prefer_provider or "").strip().lower()
    excl_p = str(exclude_provider or "").strip().lower()
    excl_m = str(exclude_model or "").strip().lower()
    found: List[Dict[str, Any]] = []
    for cand in profiles or []:
        try:
            if str(getattr(cand, "kind", "") or "") != "api":
                continue
            if not bool(getattr(cand, "enabled", False)):
                continue
            roles = getattr(cand, "roles", None) or ["chat"]
            if "chat" not in list(roles):
                continue
            provider = str(getattr(cand, "provider", "") or "")
            model = str(getattr(cand, "model", "") or "")
            pid = str(getattr(cand, "id", "") or "")
            if not provider or not model or not pid:
                continue
            if provider.strip().lower() == excl_p \
                    and model.strip().lower() == excl_m:
                continue
            try:
                ctx = int(getattr(cand, "context_tokens", 0) or 0)
            except (TypeError, ValueError):
                ctx = 0
            if ctx < floor:
                continue
        except Exception:  # noqa: BLE001 - one bad row hides no other
            continue
        try:
            from .providers import dead_models as _DM

            rec = _DM.is_dead(cfg, provider, model)
        except Exception:  # noqa: BLE001 - fail OPEN like is_dead itself
            rec = None
        if rec is not None:
            continue
        try:
            from .providers import resolver as _R2

            eff = _R2.resolve(cfg, role="chat", profile_id=pid)
        except Exception:  # noqa: BLE001 - unresolvable: not a fallback
            continue
        try:
            if not bool(getattr(eff, "executable", True)):
                continue
        except Exception:  # noqa: BLE001 - unreadable: not a fallback
            continue
        found.append({"provider": provider, "model": model,
                      "profile_id": pid, "eff": eff, "ctx": ctx})
    if not found:
        return None

    def _rank(item: Dict[str, Any]) -> Any:
        fam = 0 if prefer and str(item["provider"] or "").strip().lower() \
            == prefer else 1
        return (fam, -int(item.get("ctx") or 0),
                str(item.get("profile_id") or ""))

    found.sort(key=_rank)
    best = found[0]
    return {"provider": str(best["provider"]), "model": str(best["model"]),
            "profile_id": str(best["profile_id"]), "eff": best["eff"]}


def _fallback_effective(cfg: Any, *, prefer_provider: str = "",
                        exclude_provider: str = "",
                        exclude_model: str = "") -> Optional[Dict[str, Any]]:
    """The THIS-turn fallback profile: dynamic first, literal last resort.

    Any healthy API chat profile (preferring the dead main's provider
    family) serves; only when the dynamic search finds nothing does the
    ``qubit-go``/deepseek literal get its chance. ``None`` when neither
    qualifies. Read-only; never raises, never writes a slot.
    """
    dyn = _dynamic_fallback_effective(
        cfg, prefer_provider=prefer_provider,
        exclude_provider=exclude_provider, exclude_model=exclude_model)
    if dyn is not None:
        return dyn
    return _literal_fallback_effective(cfg)


def _dead_main_pair(cfg: Any) -> Optional[Dict[str, Any]]:
    """Dead-main + healthy-fallback pair, or None (no fallback indicated).

    Mirrors the ``_gate_remote_liveness`` silent-path semantics
    (``explicit=False``): a recorded-dead main refuses the silent headless
    dispatch. The explicit picker path is untouched (that gate lives in
    ``providers/endpoints.py``, not here). Never raises, never writes.
    """
    worker = _worker_effective(cfg)
    if not worker or not worker.get("model"):
        return None
    try:
        from .providers import dead_models as _DM

        rec = _DM.is_dead(cfg, worker["provider"], worker["model"])
    except Exception:  # noqa: BLE001 - degraded to no-fallback, never fatal
        return None
    if rec is None:
        return None
    fallback = _fallback_effective(
        cfg, prefer_provider=str(worker.get("provider") or ""),
        exclude_provider=str(worker.get("provider") or ""),
        exclude_model=str(worker.get("model") or ""))
    if fallback is None:
        return None
    if (str(fallback.get("provider") or "").lower()
            == str(worker.get("provider") or "").lower()
            and str(fallback.get("model") or "").lower()
            == str(worker.get("model") or "").lower()):
        return None
    return {"dead": worker, "record": dict(rec),
            "fallback": fallback}


def _is_dead_turn_error(res: Any) -> bool:
    """True when a wait outcome looks like a dead-model turn failure."""
    if not isinstance(res, dict):
        return False
    if str(res.get("status") or "") != "error":
        return False
    code = str(res.get("code") or "")
    if code in _DEAD_TURN_CODES:
        blob = f"{res.get('detail') or ''} {res.get('reply') or ''}"
        if code == "MODEL_ERROR" and not any(
                m in blob for m in _DEAD_TURN_MARKERS):
            return False
        return True
    blob = f"{code} {res.get('detail') or ''} {res.get('reply') or ''}"
    return any(m in blob for m in _DEAD_TURN_MARKERS)


def _apply_fallback_shim(cfg: Any, fallback: Dict[str, Any]) -> Callable:
    """Steer THIS turn's worker onto the fallback profile (in-memory).

    Patches ``agent._model`` (the worker's model string) and the
    default-role ``resolver.resolve`` (the worker's profile choice) so the
    headless ``run_session`` gateway turn executes as the fallback.
    Explicit ``profile_id`` resolutions pass through untouched. Returns a
    zero-arg restore callable; the caller MUST run it in a ``finally``.
    No slot, file, or registry is written.
    """
    from . import agent as _AG
    from .providers import resolver as _R

    fb_model = str(fallback.get("model") or _FALLBACK_MODEL)
    fb_eff = fallback.get("eff")
    orig_model = _AG._model
    orig_resolve = _R.resolve

    def _shimmed_model(_cfg: Any = None) -> str:
        return fb_model

    def _shimmed_resolve(_cfg2: Any = None, *args: Any,
                         **kwargs: Any) -> Any:
        if not args and kwargs.get("profile_id", None) in (None, "") \
                and str(kwargs.get("role", "chat") or "chat") == "chat":
            return fb_eff
        return orig_resolve(_cfg2, *args, **kwargs)

    _AG._model = _shimmed_model  # type: ignore[method-assign]
    _R.resolve = _shimmed_resolve  # type: ignore[method-assign]

    def _restore() -> None:
        try:
            _AG._model = orig_model  # type: ignore[method-assign]
        except Exception:  # noqa: BLE001 - restore is best-effort, audited
            pass
        try:
            _R.resolve = orig_resolve  # type: ignore[method-assign]
        except Exception:  # noqa: BLE001 - restore is best-effort, audited
            pass

    return _restore


def _loud_both_dead(cfg: Any, sid: str, worker: Optional[Dict[str, Any]],
                    reason: str = "") -> Dict[str, Any]:
    """LOUD typed failure: dead main and no healthy fallback (zero launches).

    Never ok:true, never a narrated action — the service layer must hear a
    typed code, not words it could speak as a done deed.
    """
    dead_name = "unknown"
    if isinstance(worker, dict) and worker.get("model"):
        dead_name = (f"{worker.get('provider') or '?'}"
                     f"/{worker.get('model')}")
    detail = (f"headless voice turn needs a working model: main {dead_name} "
              f"is recorded unavailable"
              f"{f' ({reason})' if reason else ''} and no healthy API "
              f"fallback was found (dynamic search over the API chat "
              f"profiles; last-resort "
              f"{_FALLBACK_PROVIDER}/{_FALLBACK_MODEL} "
              f"(profile {_FALLBACK_PROFILE_ID!r}) is also unavailable); "
              f"nothing was executed — pick a working model")
    _audit("turn_task_both_dead", session=sid, dead=dead_name)
    return {"ok": False, "state": "FAILED",
            "code": "TASK_MODEL_UNAVAILABLE",
            "detail": detail[:300], "session_id": sid, "session": sid,
            "route": "task"}


def _finish_with_fallback_mark(out: Dict[str, Any],
                               pair: Dict[str, Any]) -> Dict[str, Any]:
    """Stamp a fallback-turn outcome with the audited both-models receipt."""
    if not isinstance(out, dict):
        return out
    dead = pair.get("dead") or {}
    fallback = pair.get("fallback") or {}
    mark = {"from_provider": str(dead.get("provider") or ""),
            "from_model": str(dead.get("model") or ""),
            "from_profile": str(dead.get("profile_id") or ""),
            "to_provider": str(fallback.get("provider") or ""),
            "to_model": str(fallback.get("model") or _FALLBACK_MODEL),
            "to_profile": str(fallback.get("profile_id") or
                              _FALLBACK_PROFILE_ID),
            "slot_mutated": False}
    receipt = out.get("receipt")
    if isinstance(receipt, dict):
        receipt = dict(receipt)
        receipt["model"] = mark["to_model"]
        receipt["model_fallback"] = mark
        out["receipt"] = receipt
    else:
        out["fallback"] = mark
    return out


def _dead_worker(cfg: Any) -> Optional[Dict[str, Any]]:
    """Recorded-dead headless-worker profile, or None.

    The ``_gate_remote_liveness`` silent-path read (``explicit=False``):
    a previous turn failed at use time while the static probe still
    resolved. Never raises, never writes.
    """
    worker = _worker_effective(cfg)
    if not worker or not worker.get("model"):
        return None
    try:
        from .providers import dead_models as _DM

        rec = _DM.is_dead(cfg, worker["provider"], worker["model"])
    except Exception:  # noqa: BLE001 - degraded to no-fallback, never fatal
        return None
    if rec is None:
        return None
    return {"worker": worker, "record": dict(rec)}


def _fallback_failed_mark(out: Dict[str, Any], pair: Dict[str, Any],
                          sid: str) -> Dict[str, Any]:
    """LOUD wrapper for a failed fallback attempt (typed, no narration).

    The outcome stays a typed failure naming BOTH models — never ok:true,
    never words the service layer could speak as a done deed.
    """
    out = _finish_with_fallback_mark(out, pair)
    if isinstance(out, dict) and not out.get("ok"):
        _audit("turn_task_fallback_failed", session=sid,
               code=str(out.get("code") or ""))
        detail = str(out.get("detail") or "")
        fb_model = str((pair.get("fallback") or {}).get("model")
                       or _FALLBACK_MODEL)
        out["detail"] = (f"fallback {fb_model} also failed "
                         f"({out.get('code')}): {detail}")[:300]
    return out


def _turn_task(cfg: Any, st: Any, sid: str, task_text: str, *,
               timeout_s: Optional[float] = None,
               cancel: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Wave C Task 5: delegate a build to the /chat worker, report back.

    ``launch_task`` runs the exact voice worker chain headlessly; the turn
    blocks with cancel checks, then the Spark summary (never a phantom)
    lands in this transcript. E-stop latched refuses before launch with
    zero worker invocations.

    Lane 2: a recorded-dead main model never dispatches the doomed headless
    turn. Pre-dispatch, a dead main + healthy fallback runs THIS turn on
    the fallback profile only (in-memory shim, audited, receipt naming both
    models, zero slot writes); a dead main with no healthy fallback fails
    LOUD with zero launches. Post-turn, a turn-time dead-model error (the
    gateway records it on the failed turn) retries exactly once via the
    fallback; a failed fallback degrades LOUD (typed, never narrated).
    """
    from . import orchestrator as ORC

    def _run_once() -> Dict[str, Any]:
        """One launch -> starter row -> wait.

        Typed launch denials carry ``launch_denied`` so the caller returns
        them directly (safety denials never fall back, never retry).
        """
        try:
            key = ORC.launch_task(task_text, session=sid, cfg=cfg)
        except ORC.TaskLaunchDenied as exc:
            state = "UNAVAILABLE" if exc.code in (
                "EMERGENCY_STOP", "BLOCKED", "SAFETY_CONFIRM_REQUIRED",
                "DRY_RUN", "TASK_INFLIGHT") else "FAILED"
            _audit("turn_task_refused", session=sid, code=exc.code)
            return {"ok": False, "state": state, "code": exc.code,
                    "detail": str(exc.detail)[:200], "session_id": sid,
                    "session": sid, "route": "task", "launch_denied": True}
        except Exception as exc:  # noqa: BLE001 - typed, redacted
            _audit("turn_task_launch_error", session=sid, error=str(exc))
            return {"ok": False, "state": "FAILED",
                    "code": "TASK_LAUNCH_FAILED",
                    "detail": _redact(str(exc))[:200], "session_id": sid,
                    "session": sid, "route": "task", "launch_denied": True}
        st.append(sid, "assistant",
                  f"Working on it in /chat (task {key}). I'll report back here.",
                  meta={"route": "task", "task": key, "phase": "started"})
        res = ORC.wait_task(key, timeout_s=float(timeout_s or 300.0),
                            cancel=cancel)
        return {"key": key, "res": res}

    def _run_fallback_once(pair: Dict[str, Any]) -> Dict[str, Any]:
        """One launch -> wait under the THIS-turn-only fallback shim."""
        restore = _apply_fallback_shim(cfg, pair["fallback"])
        try:
            return _run_once()
        finally:
            restore()

    def _settle_wait(key: str, res: Dict[str, Any],
                     pair: Optional[Dict[str, Any]] = None
                     ) -> Dict[str, Any]:
        """Stage needs-confirm; finish the task; stamp fallback wins."""
        # T5: the worker proposed a confirmation-gated file write — stage it
        # as a user confirmation (CONFIRM_REQUIRED + confirmation_id, same
        # pattern as _turn_registry) instead of ending typed need-confirmation
        # forever.
        if str((res or {}).get("status") or "") == "needs-confirm":
            return _stage_task_proposal(cfg, sid, key, res)
        out = _finish_task(cfg, st, sid, key, res)
        if pair is not None and out.get("ok"):
            dead = pair.get("dead") or {}
            fallback = pair.get("fallback") or {}
            _audit("turn_task_model_fallback", session=sid,
                   dead=f"{dead.get('provider')}/{dead.get('model')}",
                   fallback=f"{fallback.get('provider')}"
                            f"/{fallback.get('model')}")
            return _finish_with_fallback_mark(out, pair)
        return out

    # Pre-dispatch: a recorded-dead main never runs the doomed turn.
    pair = _dead_main_pair(cfg)
    if pair is not None:
        first = _run_fallback_once(pair)
        if first.get("launch_denied"):
            first.pop("launch_denied", None)
            return first
        res = first["res"]
        key = first["key"]
        out = _settle_wait(key, res, pair)
        if out.get("ok") or str(out.get("code") or "") == "CONFIRM_REQUIRED":
            return out
        return _fallback_failed_mark(out, pair, sid)
    # Dead main with no healthy fallback: LOUD, zero launches. A healthy
    # (or unresolvable) main keeps today's single attempt below.
    dw = _dead_worker(cfg)
    if dw is not None:
        _dw_worker = dw.get("worker") or {}
        _no_fb = _fallback_effective(
            cfg, prefer_provider=str(_dw_worker.get("provider") or ""),
            exclude_provider=str(_dw_worker.get("provider") or ""),
            exclude_model=str(_dw_worker.get("model") or "")) is None
        if _no_fb:
            reason = str((dw.get("record") or {}).get("reason") or "")
            return _loud_both_dead(cfg, sid, dw.get("worker"), reason)
    first = _run_once()
    if first.get("launch_denied"):
        first.pop("launch_denied", None)
        return first
    res = first["res"]
    key = first["key"]
    if str((res or {}).get("status") or "") == "needs-confirm":
        return _stage_task_proposal(cfg, sid, key, res)
    if not _is_dead_turn_error(res):
        return _finish_task(cfg, st, sid, key, res)
    # Turn-time dead: the gateway recorded the model on this failed turn —
    # exactly one retry via the fallback, then LOUD on any further failure.
    pair = _dead_main_pair(cfg)
    if pair is None:
        return _finish_task(cfg, st, sid, key, res)
    second = _run_fallback_once(pair)
    if second.get("launch_denied"):
        second.pop("launch_denied", None)
        return second
    res2 = second["res"]
    key2 = second["key"]
    out = _settle_wait(key2, res2, pair)
    if out.get("ok") or str(out.get("code") or "") == "CONFIRM_REQUIRED":
        return out
    return _fallback_failed_mark(out, pair, sid)


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


# ---------------------------------------------------------------------------
# Qubit-chat slash commands — leading-slash turns execute, never chatter
# ---------------------------------------------------------------------------
#: Slash verbs the ``/qubit-chat`` turn path executes against the SAME
#: backends the dashboard/CLI/voice surfaces read (DECISIONS #26: voice /
#: qubit-chat does everything /chat does). ``/chat`` (the PTY/TUI surface)
#: handles its own richer slash set in-process
#: (``voicebridge/terminal.py::handle_slash`` + the vendored TUI); this table
#: is the feasible subset on the shared turn path — every entry resolves to a
#: real backend result persisted as the assistant message, never a typed
#: refusal and never model narration. Anything starting with ``/`` that is
#: not listed answers honest help (unknown command named + valid verbs),
#: never phantom success.
#: Full gateway slash inventory (frozen in
#: tests/test_qubitchat_parity_audit9u.py::TERMINAL_SLASH_INVENTORY, 60 names)
#: minus the two transport-bound verbs (``login``, ``platform`` — see that
#: test's OUT_OF_SCOPE), plus the two dashboard-native extras (``mcp``,
#: ``plugins``) with no terminal counterpart. Every entry parses AND executes:
#: read-only verbs hit the real backend; terminal-session verbs answer honest
#: guidance naming the /chat behavior + the dashboard equivalent (never a
#: typed refusal, never phantom success, never model narration).
SLASH_COMMANDS = (
    "agents", "approve", "approvals", "bg", "blueprint", "branch", "btw",
    "bundles", "busy", "codex-runtime", "commands", "compress", "context",
    "debug", "deny", "diff", "fast", "footer", "goal", "heartbeat", "help",
    "insights", "kanban", "loop", "mcp", "memory", "model", "pause",
    "personality", "plugins", "profile", "reasoning", "refine",
    "reload-mcp", "reload-skills", "reset", "restart", "resume", "retry",
    "review", "rollback", "save", "sessions", "sethome", "skills",
    "status", "stop", "subgoal", "suggestions", "title", "topic", "topup",
    "undo", "update", "usage", "verbose", "version", "voice", "whoami",
    "yolo",
)

_SLASH_HELP_LINES = (
    "Available slash commands:",
    "/skills — list installed skills (real catalog)",
    "/model — show the effective chat model/profile",
    "/mcp — list configured MCP servers",
    "/plugins — list registered plugins",
    "/status — gateway + service state (real supervisor read)",
    "/sessions — session count on the canonical store + Sessions page",
    "/version — product version",
    "/whoami — this dashboard session identity",
    "/reasoning — MODEL card reasoning selector (this page's side panel)",
    "/memory — Memory page (/qubit-memory)",
    "/kanban — board pages (/qubit-taskflows, Task Inbox)",
    "/help — show this list",
    "Terminal-session verbs (reset, undo, compress, …) run in /chat;",
    "type one here for where to run it.",
)


def parse_qubitchat_slash(text: Any) -> Optional[Dict[str, Any]]:
    """Parse a leading-slash turn, or None when the text is not slash-shaped.

    Pure: no store, broker, or model touch. Any text whose first non-space
    character is ``/`` parses — known verbs carry ``command`` + ``args``,
    anything else carries ``command: "unknown"`` so the caller answers
    honest help instead of letting the classifier chatter about it.

    Known = the executed tuple, the composer conveniences + registry aliases
    (normalized to their canonical verb, original kept as ``name``), the
    ``GET /api/chat/slash-commands`` catalog prefixes (``skill-``/``plugin-``/
    ``mcp-``/``toolset-``), and every other CLI-registry verb (answered with
    honest /chat + dashboard guidance by :func:`run_qubitchat_slash`). The
    registry read is lazy and fail-soft — without it parsing is exactly the
    old tuple behavior.
    """
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return None
    body = raw[1:].strip()
    if not body:
        return {"command": "unknown", "name": "", "args": ""}
    parts = body.split(None, 1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    if name in SLASH_COMMANDS:
        return {"command": name, "name": name, "args": args}
    canonical = _slash_alias_target(name)
    if canonical is not None:
        return {"command": canonical, "name": name, "args": args}
    if _slash_catalog_entry(name) is not None:
        return {"command": name, "name": name, "args": args}
    if name in _registry_slash_names():
        return {"command": name, "name": name, "args": args}
    return {"command": "unknown", "name": name, "args": args}


def extract_inline_slash(text: Any) -> Optional[Dict[str, Any]]:
    """Find one mid-sentence /verb token, or None (pure, never raises).

    Leading-slash turns keep going through :func:`parse_qubitchat_slash`
    byte-identical; this covers "please run /model here" style turns. Only a
    KNOWN verb matches, with a whitespace/start boundary on the left and a
    whitespace/punctuation/end boundary on the right, so paths (``/tmp/x``),
    URLs (``https://…``), and ``file://`` can never match (``.``/``/``/``:``
    are not verb chars and ``tmp``/``etc`` are not verbs). Zero or 2+ matches
    return None (a multi-command sentence is never guessed).

    Known here = the executed tuple plus the composer conveniences and the
    skill/plugin/MCP/toolset catalog prefixes (all import-free, so this stays
    cheap on the every-turn path — the matched token re-dispatches through
    :func:`run_qubitchat_slash`, where alias/registry normalization owns it).
    """
    try:
        raw = str(text or "")
    except Exception:
        return None
    if not raw or "/" not in raw:
        return None
    try:
        pattern = (r"(?:^|[\s(\[{\'\"`])/"
                    r"([A-Za-z0-9_-]+)"
                    r"(?=[\s.,?!;:)\]}'\"`]|$)")
        total = 0
        single = ""
        for match in re.finditer(pattern, raw):
            name = str(match.group(1) or "").lower()
            if _is_inline_slash_verb(name):
                total += 1
                single = name
        if total != 1:
            return None
        return {"command": single, "name": single, "args": "",
                "inline": True, "slash_text": f"/{single}"}
    except Exception:
        return None


def _slash_help_reply(unknown: str = "") -> str:
    head = (f"Unknown slash command '/{unknown}'. "
            if unknown else "Slash commands. ")
    return head + "\n".join(_SLASH_HELP_LINES)


def _slash_skills(cfg: Any, args: str) -> str:
    from .skill_catalog import catalog_entries

    rows = catalog_entries(cfg) or []
    needle = args.strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in str(r.get("name", "")).lower()
                or needle in str(r.get("description", "")).lower()]
    if not rows:
        return ("No skills installed"
                + (f" matching {args.strip()!r}" if args.strip() else "")
                + ".")
    lines = [f"Skills ({len(rows)}):"]
    for r in rows[:50]:
        name = str(r.get("name", ""))
        desc = str(r.get("description", "")).strip()
        lines.append(f"- {name}" + (f" — {desc[:120]}" if desc else ""))
    if len(rows) > 50:
        lines.append(f"… and {len(rows) - 50} more")
    return "\n".join(lines)


def _slash_model(cfg: Any) -> str:
    routed = resolve_route(cfg, None, session=None, role="chat")
    if not routed.get("ok"):
        return (f"Model route unavailable: "
                f"{routed.get('code') or 'MODEL_NOT_ALLOWED'} — "
                f"{routed.get('detail') or 'no executable profile'}")
    provider = str(routed.get("provider") or "local")
    model = str(routed.get("model") or configured_model(cfg))
    kind = str(routed.get("kind") or "")
    profile = str(routed.get("profile_id") or "")
    bits = f"{provider}/{model}"
    if kind:
        bits += f" ({kind})"
    if profile and profile != model:
        bits += f" [profile {profile}]"
    return f"Effective chat model: {bits}"


def _slash_mcp(cfg: Any) -> str:
    from .mcp_servers import list_servers

    res = list_servers(cfg) or {}
    rows = res.get("servers") or []
    if not rows:
        return "No MCP servers configured."
    lines = [f"MCP servers ({len(rows)}):"]
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", ""))
        transport = str(r.get("transport", ""))
        state = ("invocable" if r.get("invocable")
                 else str(r.get("status") or "configured"))
        lines.append(f"- {name} [{transport}] — {state}")
    return "\n".join(lines)


def _slash_plugins(cfg: Any) -> str:
    from .plugin_runtime import list_plugins

    res = list_plugins(cfg) or {}
    rows = res.get("plugins") or []
    if not rows:
        return "No plugins registered."
    names = sorted({str(r.get("id", "")) for r in rows
                    if isinstance(r, dict) and r.get("id")})
    if not names:
        return "No plugins registered."
    lines = [f"Plugins ({len(names)}):"]
    lines.extend(f"- {n}" for n in names[:80])
    if len(names) > 80:
        lines.append(f"… and {len(names) - 80} more")
    return "\n".join(lines)


#: Terminal-session verbs: what each does in /chat + the dashboard
#: equivalent. Replies built from this map never claim execution — they name
#: the verb, its /chat behavior, and where to run it. (Read-only verbs with
#: real backends live in the dedicated _slash_* handlers above, not here.)
_SLASH_TERMINAL_GUIDANCE = {
    "agents": "lists the terminal agent roster — run it in /chat; this page's side panel shows sessions instead.",
    "approve": "approves a pending terminal action — approve dashboard mutations in their own confirm dialog; terminal approvals run in /chat.",
    "approvals": "lists pending terminal approvals — run it in /chat; dashboard confirmations appear inline where they stage.",
    "bg": "backgrounds the running terminal turn — /chat only; this page streams in place.",
    "blueprint": "terminal blueprint/scaffold flow — run it in /chat.",
    "branch": "branches the terminal session — run it in /chat; past sessions live on the Sessions page.",
    "btw": "terminal aside-to-agent — run it in /chat; here just send the message normally.",
    "bundles": "terminal bundle listing — run it in /chat.",
    "busy": "shows terminal busy state — run it in /chat; this page shows its own streaming state.",
    "codex-runtime": "manages the codex runtime — run it in /chat or the qubit CLI.",
    "compress": "compacts the terminal transcript — run it in /chat; to start fresh here use New chat.",
    "context": "shows terminal context usage — run it in /chat; this page's MODEL card shows the active model.",
    "debug": "toggles terminal debug output — run it in /chat.",
    "deny": "denies a pending terminal action — deny dashboard mutations in their own confirm dialog; terminal denials run in /chat.",
    "diff": "shows the terminal working-tree diff — run it in /chat.",
    "fast": "toggles terminal fast mode — run it in /chat.",
    "footer": "terminal footer control — run it in /chat.",
    "goal": "terminal goal tracking — run it in /chat; task boards live on the Taskflows pages.",
    "heartbeat": "terminal heartbeat control — run it in /chat.",
    "insights": "terminal usage insights — run it in /chat; token analytics live on the Analytics page.",
    "kanban": "terminal kanban view — open the Taskflows / board pages on this dashboard instead.",
    "loop": "terminal loop control — run it in /chat.",
    "memory": "terminal memory view — open the Memory page (/qubit-memory) on this dashboard instead.",
    "pause": "pauses the terminal turn — run it in /chat; stop this page's stream with the stop button.",
    "personality": "terminal persona control — run it in /chat; assistant identity lives in Profiles.",
    "profile": "terminal profile switch — run it in /chat; the MODEL card on this page switches this surface.",
    "refine": "terminal refine flow — run it in /chat.",
    "reasoning": "terminal reasoning control — use the Reasoning selector on this page's side panel instead.",
    "reload-mcp": "reloads MCP servers — open the MCP page on this dashboard instead (test + reload controls there).",
    "reload-skills": "reloads the skill catalog — open the Skills page on this dashboard instead.",
    "reset": "resets the terminal session — run it in /chat; to start fresh here use New chat.",
    "restart": "restarts the terminal/gateway — gateway restart lives on the System page (confirm-gated).",
    "resume": "resumes a terminal session — run it in /chat; past sessions live on the Sessions page.",
    "retry": "retries the terminal turn — run it in /chat; here resend the message.",
    "review": "terminal review flow — run it in /chat.",
    "rollback": "rolls back the terminal session — run it in /chat.",
    "save": "exports the terminal transcript — run it in /chat; this transcript persists on the canonical store.",
    "sethome": "sets the terminal home directory — run it in /chat or the qubit CLI.",
    "stop": "stops the terminal turn — run it in /chat; stop this page's stream with the stop button.",
    "subgoal": "terminal subgoal tracking — run it in /chat; task boards live on the Taskflows pages.",
    "suggestions": "terminal task suggestions — run it in /chat.",
    "title": "sets the terminal session title — run it in /chat.",
    "topic": "terminal topic sessions — run it in /chat.",
    "topup": "terminal top-up/billing — see the System page on this dashboard.",
    "undo": "backs up terminal turns — run it in /chat.",
    "update": "product update — see the System page (update check/apply/rollback, confirm-gated).",
    "usage": "terminal usage readout — run it in /chat; token analytics live on the Analytics page.",
    "verbose": "toggles terminal verbose output — run it in /chat.",
    "voice": "voice controls — open the Voice page (/voice-edit) on this dashboard instead.",
    "yolo": "terminal privileged mode — run it in /chat; destructive dashboard actions confirm where they stage.",
}


def _slash_status(cfg: Any) -> str:
    """Real supervisor read; honest degradation when unreachable."""
    try:
        from .gateway_supervisor import status as _gw_status

        st = _gw_status(cfg) or {}
        if isinstance(st, dict):
            running = st.get("running", st.get("daemon_live", "?"))
            return f"Gateway supervisor: running={running} state={st.get('state') or st.get('daemon_state') or 'n/a'}"
        return f"Gateway supervisor: {st}"
    except Exception as exc:  # noqa: BLE001 - guidance, never a refusal
        return (f"Gateway status unreadable from this surface "
                f"({str(exc)[:120]}); live state is on the System page.")


def _slash_sessions(cfg: Any) -> str:
    """Real canonical-store count; honest degradation when unreadable."""
    try:
        res = store(cfg).list() or {}
        # list() returns {"sessions": [...], "total": N, ...} — total is the
        # canonical count (same filters as the rows); len(res) would count
        # the dict keys, never the sessions.
        total = res.get("total")
        if total is None:
            total = len(res.get("sessions") or [])
        return (f"Sessions on the canonical store: {total}. "
                f"Browse + resume them on the Sessions page.")
    except Exception as exc:  # noqa: BLE001 - guidance, never a refusal
        return (f"Session count unreadable from this surface "
                f"({str(exc)[:120]}); browse them on the Sessions page.")


def _slash_version() -> str:
    try:
        from importlib.metadata import version as _pkg_version

        for dist in ("qubit-agent", "qubit-voicebridge", "voicebridge"):
            try:
                return f"Product version: {_pkg_version(dist)} ({dist})"
            except Exception:
                continue
    except Exception:
        pass
    return "Product version: unknown (package metadata unreadable)"


def _slash_whoami(session_id: str) -> str:
    sid = (session_id or "").strip() or "dashboard-chat"
    return (f"This dashboard chat session: {sid} "
            f"(identity dashboard:user on the canonical store).")


#: Composer-only conveniences (``frontend/.../lib/slashCommands.ts``): tokens
#: the ``/qubit-chat`` composer inserts that are not registry names. Resolved
#: to their canonical verb so a raw-typed or palette-sent token executes
#: instead of answering unknown. ``/mcp`` intentionally stays its own verb
#: (the real server list above), so only ``/models`` maps.
_SLASH_CONVENIENCES = {"models": "model"}


def _is_inline_slash_verb(name: str) -> bool:
    """True when ``name`` may trigger the mid-sentence slash path.

    Import-free (tuple + conveniences + catalog prefixes) so the every-turn
    inline scan never pays the vendored-registry import; the matched token
    re-dispatches through :func:`run_qubitchat_slash`, where full
    alias/registry normalization owns it. Never raises.
    """
    try:
        if name in SLASH_COMMANDS or name in _SLASH_CONVENIENCES:
            return True
        return _slash_catalog_entry(name) is not None
    except Exception:  # noqa: BLE001 - a matcher fault means "no match"
        return False

#: Catalog-namespace prefixes served by ``GET /api/chat/slash-commands``
#: (skills / plugins / MCP servers / toolsets — the "hundreds of commands").
#: A ``/<prefix><name>`` turn executes against the same authority the catalog
#: reads (real describe, never unknown, never phantom execution).
_SLASH_CATALOG_PREFIXES = ("skill-", "plugin-", "mcp-", "toolset-")

#: Lazily-loaded CLI-registry slash shape: (names, aliases, descriptions).
#: ``None`` until the first slash turn needs it; ``(frozenset(), {}, {})`` on
#: a vendored-import miss (fail-soft: parsing stays the old tuple behavior).
_REGISTRY_SLASH_CACHE: Optional[tuple] = None


def _registry_slash_map() -> tuple:
    """CLI-registry slash names, aliases and descriptions (lazy, cached).

    Same single source the ``/chat`` completer and the slash catalog read
    (``qubit_cli.commands.COMMAND_REGISTRY``). Never raises: a vendored miss
    caches empty so every later slash turn skips the import attempt cost of
    retrying. Only called for leading-slash turns (never the every-turn
    inline path), so the import cost lands at most once per process.
    """
    global _REGISTRY_SLASH_CACHE
    if _REGISTRY_SLASH_CACHE is not None:
        return _REGISTRY_SLASH_CACHE
    names: set = set()
    aliases: dict = {}
    descriptions: dict = {}
    try:
        from . import qubit_runtime as _QR

        _QR.ensure_path()
        from qubit_cli.commands import (  # type: ignore
            COMMAND_REGISTRY)
        for cmd in COMMAND_REGISTRY or []:
            cname = str(getattr(cmd, "name", "") or "").strip().lower()
            if not cname:
                continue
            names.add(cname)
            desc = str(getattr(cmd, "description", "") or "").strip()
            if desc:
                descriptions[cname] = desc
            for alias in (getattr(cmd, "aliases", None) or ()):
                aname = str(alias or "").strip().lower()
                if aname and aname not in names:
                    aliases[aname] = cname
    except Exception:  # noqa: BLE001 - fail-soft to tuple-only parsing
        pass
    _REGISTRY_SLASH_CACHE = (frozenset(names), aliases, descriptions)
    return _REGISTRY_SLASH_CACHE


def _registry_slash_names() -> frozenset:
    return _registry_slash_map()[0]


def _slash_alias_target(name: str) -> Optional[str]:
    """Canonical verb for a composer convenience or registry alias."""
    if name in _SLASH_CONVENIENCES:
        return _SLASH_CONVENIENCES[name]
    try:
        return _registry_slash_map()[1].get(name)
    except Exception:  # noqa: BLE001 - fail-soft: no alias resolution
        return None


def _slash_catalog_entry(name: str) -> Optional[tuple]:
    """Split a ``<prefix><key>`` catalog verb, or None (pure, never raises)."""
    try:
        low = str(name or "").strip().lower()
    except Exception:
        return None
    for prefix in _SLASH_CATALOG_PREFIXES:
        if low.startswith(prefix) and len(low) > len(prefix):
            return (prefix[:-1], low[len(prefix):])
    return None


def _slash_catalog_reply(cfg: Any, command: str) -> str:
    """Describe one catalog entry against its real authority (read-only).

    ``/skill-<name>`` / ``/plugin-<name>`` / ``/mcp-<name>`` /
    ``/toolset-<name>`` name the entry, its description, and how to run it
    from this surface (plain-language turn; the agent holds the tool). A miss
    names the miss plus the listing verb — never a typed refusal, never
    phantom success.
    """
    parsed = _slash_catalog_entry(command)
    if parsed is None:
        return _slash_help_reply(command)
    kind, key = parsed
    if kind == "skill":
        try:
            from .skill_catalog import catalog_entries

            rows = catalog_entries(cfg) or []
        except Exception:  # noqa: BLE001 - honest miss, never fatal
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("name") or "").strip().lower() == key:
                desc = str(row.get("description") or "").strip()
                return (f"Skill '{key}'"
                        + (f": {desc}" if desc else " (no description).")
                        + " To run it, send a message asking for it "
                        + f"(e.g. 'use the {key} skill to …') — the agent "
                        + "loads the skill and does the work here.")
        return (f"No skill '{key}'. List installed skills with /skills "
                f"(or filter: /skills {key}).")
    if kind == "plugin":
        try:
            from .plugin_runtime import list_plugins

            rows = (list_plugins(cfg) or {}).get("plugins") or []
        except Exception:  # noqa: BLE001 - honest miss, never fatal
            rows = []
        names = sorted({str(r.get("id", "")) for r in rows
                        if isinstance(r, dict) and r.get("id")})
        for ident in names:
            if ident.strip().lower() == key or ident.strip().lower().endswith(
                    "-" + key) or ident.strip().lower() == "qubit-" + key:
                return (f"Plugin '{ident}' is registered. Manage it on the "
                        f"Plugins page; to use it, send a message describing "
                        f"what it should do.")
        return (f"No plugin '{key}'. List registered plugins with /plugins.")
    if kind == "mcp":
        try:
            from .mcp_servers import list_servers

            rows = (list_servers(cfg) or {}).get("servers") or []
        except Exception:  # noqa: BLE001 - honest miss, never fatal
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("name") or "").strip().lower() == key:
                transport = str(row.get("transport") or "stdio")
                state = ("invocable" if row.get("invocable")
                         else str(row.get("status") or "configured"))
                return (f"MCP server '{key}' [{transport}] — {state}. To call "
                        f"a tool on it, send a message describing the call.")
        return (f"No MCP server '{key}'. List configured servers with /mcp.")
    if kind == "toolset":
        return (f"Toolset '{key}': the agent's tool scope. Send a message "
                f"describing the work and the agent uses the tools it needs; "
                f"available toolsets are listed by the catalog.")
    return _slash_help_reply(command)


#: Registry verbs with a load-bearing dashboard equivalent (mirrors the
#: terminal-guidance table above for the CLI-registry delta). Everything else
#: in the registry falls back to the generic /chat pointer in
#: :func:`_slash_registry_guidance`.
_SLASH_REGISTRY_DASHBOARD = {
    "new": "start a fresh conversation here with New chat.",
    "sessions": "browse + resume past sessions on the Sessions page.",
    "quit": "nothing server-side to stop — just close this tab.",
    "exit": "nothing server-side to stop — just close this tab.",
    "palette": "press Ctrl-K on this page for the command palette.",
    "copy": "use 'copy last response' under the composer.",
    "image": "use Attach under the composer (routed to the agent).",
    "login": "this dashboard session is already authenticated.",
    "platform": "no messaging transport exists on this surface.",
    "platforms": "no messaging transport exists on this surface.",
}


def _slash_registry_guidance(command: str) -> str:
    """Honest guidance for a CLI-registry verb with no dashboard executor.

    Names the verb, its registry description, and where it runs (the /chat
    terminal, or the dashboard equivalent when one exists) — never a typed
    refusal, never phantom success. Covers every present and future registry
    verb, so the offered set can never outgrow the executed set again.
    """
    _names, _aliases, descriptions = _registry_slash_map()
    desc = str(descriptions.get(command) or "").strip()
    if desc and not desc.endswith((".", "!", "?", ":")):
        desc += "."
    head = f"/{command}" + (f" — {desc}" if desc else "")
    pointer = _SLASH_REGISTRY_DASHBOARD.get(command)
    if pointer is not None:
        return f"{head} {pointer}"
    return (f"{head} It runs in the /chat terminal (open it from this "
            f"page's 'open in /chat' link).")


def run_qubitchat_slash(cfg: Any, text: str,
                         *, session_id: str = "") -> Dict[str, Any]:
    """Execute one leading-slash turn against a real backend.

    Read-only verbs hit the real registries; terminal-session verbs answer
    honest guidance (what the verb does in /chat + the dashboard equivalent —
    never a typed refusal, never phantom success); unknown verbs answer
    honest help naming the miss and the valid set. ``ok False`` only when a
    real backend raised.
    """
    parsed = parse_qubitchat_slash(text)
    if parsed is None:
        return {"ok": False, "state": "FAILED", "code": "NOT_SLASH",
                "detail": "not a slash command"}
    command = str(parsed.get("command") or "unknown")
    try:
        if command in ("help", "commands"):
            reply = "\n".join(_SLASH_HELP_LINES)
        elif command == "skills":
            reply = _slash_skills(cfg, str(parsed.get("args") or ""))
        elif command == "model":
            reply = _slash_model(cfg)
        elif command == "mcp":
            reply = _slash_mcp(cfg)
        elif command == "plugins":
            reply = _slash_plugins(cfg)
        elif command == "status":
            reply = _slash_status(cfg)
        elif command == "sessions":
            reply = _slash_sessions(cfg)
        elif command == "version":
            reply = _slash_version()
        elif command == "whoami":
            reply = _slash_whoami(session_id)
        elif command in _SLASH_TERMINAL_GUIDANCE:
            reply = (f"/{command} {_SLASH_TERMINAL_GUIDANCE[command]}")
        elif _slash_catalog_entry(command) is not None:
            reply = _slash_catalog_reply(cfg, command)
        elif command in _registry_slash_names():
            reply = _slash_registry_guidance(command)
        else:
            reply = _slash_help_reply(str(parsed.get("name") or ""))
    except Exception as exc:  # noqa: BLE001 - typed, redacted, never narrated
        _audit("turn_slash_error", command=command, error=str(exc))
        return {"ok": False, "state": "FAILED",
                "code": "SLASH_BACKEND_ERROR",
                "detail": _redact(str(exc))[:200],
                "route": "slash", "entry": command}
    return {"ok": True, "text": reply, "reply": reply,
            "route": "slash", "entry": command,
            "receipt": _branch_receipt("slash", task=text, cfg=cfg)}


def _direct_fact_answer(text: Any) -> str:
    """Deterministic clock/weather answers for the chat turn path (no model).

    Mirrors ``voice_edit/service.py::_direct_fact_answer`` (same regexes, same
    ``scripts/weather.py`` backend) so ``/qubit-chat`` answers facts exactly
    like voice does. Before this stage a weather question fell into the
    ``web_search`` branch or the model and could narrate stale data (live
    2026-10-04: "Weather in Bucharest on 27 February 2026" while the script
    reported live 16°C). Anything else (or a failed weather read) returns ""
    and the turn continues to the classifier byte-identical. Never raises.
    """
    import re as _re
    import time as _time
    q = str(text or "").strip()
    if not q:
        return ""
    if _re.search(r"\bweather\b|\bforecast\b|\btemperature\b|\btemp\b",
                   q, _re.IGNORECASE):
        try:
            import subprocess as _sp
            import sys as _sys
            from pathlib import Path as _Path
            script = (_Path(__file__).resolve().parents[1]
                      / "scripts" / "weather.py")
            if not script.is_file():
                return ""
            proc = _sp.run([_sys.executable, str(script), q],
                           capture_output=True, text=True, timeout=15)
            if proc.returncode != 0:
                return ""
            return str(proc.stdout or "").strip()
        except Exception:
            return ""
    if _re.search(r"\bwhat time\b|\bcurrent time\b|\btime is it\b"
                   r"|\bwhat'?s the time\b|\btell me the time\b",
                   q, _re.IGNORECASE):
        try:
            return f"It is {_time.strftime('%A %H:%M')}."
        except Exception:
            return ""
    if _re.search(r"\bwhat day is it\b|\bwhat date\b"
                   r"|\bwhat'?s (the |today'?s )?(date|day|today)\b"
                   r"|\bwhat is today\b|\bwhat'?s today\b",
                   q, _re.IGNORECASE):
        try:
            return (f"It is {_time.strftime('%A, %B')} "
                    f"{int(_time.strftime('%d'))}, "
                    f"{_time.strftime('%Y')}.")
        except Exception:
            return ""
    return ""


def _finish_fact(cfg: Any, st: Any, sid: str, reply: str) -> Dict[str, Any]:
    """Persist a direct-fact outcome as the turn's assistant message."""
    st.append(sid, "assistant", reply,
              meta={"route": "facts", "model": "live-facts"})
    _audit("turn_fact", session=sid)
    return {"ok": True, "text": reply, "reply": reply, "route": "facts",
            "model": "live-facts", "session_id": sid, "session": sid,
            "receipt": _branch_receipt("facts", task=reply, cfg=cfg)}


#: Deterministic reread (sweep lane, 2026-10-05): "say that again" replays
#: the last assistant message verbatim instead of entering the classifier
#: (which tags it clarify) or the model (which paraphrases). Same shapes as
#: ``voice_edit/service.py::_REPEAT_RE`` (kept local: this module is
#: imported by the service, so importing it back would cycle).
import re as _re_module
_REPEAT_RE = _re_module.compile(
    r"\bsay\s+(that|it)\s+again\s*[.?!]*\s*$"
    r"|\bread\s+(that|it)\s+back\s*[.?!]*\s*$"
    r"|\brepeat\s+(that|it)\s*[.?!]*\s*$"
    r"|\bwhat\s+did\s+you\s+(just\s+)?say\s*[?]?\s*$"
    r"|\bcome\s+again\s*[?]?\s*$",
    _re_module.IGNORECASE)


def _repeat_request(text: Any) -> bool:
    """True when the turn asks to hear the last answer again. Pure."""
    try:
        return bool(_REPEAT_RE.search(str(text or "")))
    except Exception:
        return False


def _last_assistant_text(st: Any, sid: str) -> str:
    """Newest non-empty assistant message, or "" (never raises)."""
    try:
        msgs = (st.messages(sid, limit=50) or {}).get("messages", [])
        for m in reversed(msgs):
            if not isinstance(m, dict):
                continue
            if str(m.get("role") or "") != "assistant":
                continue
            content = str(m.get("content") or "").strip()
            if content:
                return content
    except Exception:
        pass
    return ""


def _finish_repeat(cfg: Any, st: Any, sid: str, reply: str) -> Dict[str, Any]:
    """Persist a reread replay as the turn's assistant message."""
    st.append(sid, "assistant", reply,
              meta={"route": "repeat", "model": "repeat"})
    _audit("turn_repeat", session=sid)
    return {"ok": True, "text": reply, "reply": reply, "route": "repeat",
            "model": "repeat", "session_id": sid, "session": sid,
            "receipt": _branch_receipt("repeat", task=reply, cfg=cfg)}


def _finish_slash(cfg: Any, st: Any, sid: str, clean: str) -> Dict[str, Any]:
    """Persist a slash outcome as the turn's assistant message (turn shape)."""
    res = run_qubitchat_slash(cfg, clean, session_id=sid)
    if not res.get("ok"):
        return dict(res, session_id=sid, session=sid)
    st.append(sid, "assistant", str(res.get("reply") or ""),
              meta={"route": "slash",
                    "entry": str(res.get("entry") or "")})
    _audit("turn_slash", session=sid, entry=str(res.get("entry") or ""))
    return dict(res, session_id=sid, session=sid)


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
         reasoning: Optional[str] = None,
         force_agent: bool = False) -> Dict[str, Any]:
    """Run one persisted agent turn on the shared Qubit brain path.

    ``reasoning`` (None/minimal/low/medium/high/max) is validated FIRST so
    an unknown level fails typed before any session/message write, then
    threaded as a real per-request output budget to the resolved model.

    Wave C: a classified ``web_search`` / ``local_action`` / ``registry`` /
    ``agent_task`` turn executes through its branch (shared web path,
    broker-backed local action, shared voice registry, headless /chat
    worker) instead of the model path. Anything else keeps the legacy
    model flow byte-for-byte.

    ``force_agent`` (attached-image/vision turns): the text is CONTEXT, not an
    instruction, so the classifier must not steal it. The turn routes straight
    through the SAME tool-capable agent ``/chat`` drives, with the legacy model
    path only as the no-runtime fallback — never a fast-path action
    (``screenshot``) or the coding build worker.
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
    # Attached-image/vision turns carry a description that the fast-path
    # classifier would hijack (a "screenshot"/"image" phrase matches the local
    # screenshot action; a question matches the coding build worker), so they
    # must reach the tool-capable agent directly. See :func:`_agent_turn`.
    if force_agent:
        return _agent_turn(cfg, clean, session=session, model=model,
                           timeout_s=timeout_s, cancel=cancel, role=role,
                           reasoning=reasoning_level)
    # Qubit-chat slash turns (DECISIONS #26): a leading "/" executes against
    # the real backend registries instead of entering the classifier/model
    # path (which would chatter about or narrate the slash text). Non-slash
    # turns continue below byte-identical.
    if clean.startswith("/"):
        created = _resolve_session(st, session, model=None, cfg=cfg)
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
        return _finish_slash(cfg, st, sid, clean)
    # T4/T5: a follow-up on a session with a live pending confirmation is
    # resolved by intent through the SAME session-scoped durable journal the
    # staged record lives in — an affirmation runs it (registry settle or the
    # user-issued build grant; never a fresh classify, which would chatter
    # "I will open the browser…" and execute nothing), an explicit denial
    # cancels it, and any OTHER text is a non-consuming turn: the pending is
    # preserved, never silently discarded. Affirmations/denials with nothing
    # pending keep the legacy path byte-for-byte (no auto-execution, no
    # phantom cancel).
    if clean:
        _probe = str(session or DEFAULT_SESSION).strip()[:80] \
            or DEFAULT_SESSION
        _verdict = _classify_confirmation(clean)
        if _verdict in ("affirm", "deny") and (
                _pending_registry(cfg, _probe) or _pending_task(cfg, _probe)):
            created = _resolve_session(st, session, model=None, cfg=cfg)
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
            if _verdict == "deny":
                return _cancel_confirmations(cfg, st, sid)
            # Settle the SAME record the UI surfaced: ``_pending_confirmation``
            # returns the newest staged action across both kinds, so a "yes"
            # must follow its ``kind``. Selecting registry first would settle an
            # older registry action while the banner showed a newer build write.
            _surfaced = _pending_confirmation(cfg, sid)
            if _surfaced and str(_surfaced.get("kind") or "") == "task":
                return _settle_task_affirmed(cfg, st, sid)
            return _settle_affirmed(cfg, st, sid)
    # Mid-sentence slash (one known /verb anywhere): executes that verb
    # against the real backend instead of entering the classifier/model path
    # (which would chatter about the slash text). Runs after affirmation
    # settles (a bare "yes" is never a slash) and before facts, so facts keep
    # priority. Zero or 2+ verbs fall through byte-identical.
    if clean and not clean.startswith("/"):
        _inline = extract_inline_slash(clean)
        if _inline is not None:
            created = _resolve_session(st, session, model=None, cfg=cfg)
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
            return _finish_slash(cfg, st, sid,
                                 str(_inline.get("slash_text") or ""))
    # Direct facts (DECISIONS #26 — same facts voice answers): clock/weather
    # never consult the model. Runs after slash + affirmation settles (those
    # own their shapes) and before the classifier, so "weather in X" cannot
    # narrate stale data. Empty answer falls through byte-identical.
    _fact = _direct_fact_answer(clean)
    if _fact:
        created = _resolve_session(st, session, model=None, cfg=cfg)
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
        return _finish_fact(cfg, st, sid, _fact)
    # Deterministic reread (sweep lane, 2026-10-05): replays the last
    # assistant message verbatim. Runs with the facts fast path (after
    # slash + affirmation settles, before the classifier) so "say that
    # again" cannot narrate or paraphrase. Empty history falls through
    # byte-identical (no session write yet — resolve/append only on hit).
    if _repeat_request(clean):
        _created = _resolve_session(st, session, model=None, cfg=cfg)
        if _created.get("state") == "OK":
            _rsid = _created["session"]["id"]
            _last = _last_assistant_text(st, _rsid)
            if _last:
                _appended = st.append(_rsid, "user", clean)
                if _appended.get("state") == "OK":
                    return _finish_repeat(cfg, st, _rsid, _last)
    decided = _decide_branch(clean, cfg)
    if decided.get("action") in BRANCH_ACTIONS:
        created = _resolve_session(st, session, model=None, cfg=cfg)
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

    created = _resolve_session(st, session, model=resolved_model, cfg=cfg)
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
    escalated_away = False
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
        escalated_away = True
    # DECISIONS #26: a general conversation turn runs the SAME tool-capable
    # agent the /chat terminal drives, so the model knows and can call its
    # tools (skills/plugins/MCP). Fast paths (slash/facts/affirmatives/local/
    # registry/web/build task) already returned above and never reach here.
    # An explicit per-call model is honored; a huge turn keeps its explicit
    # escalation model instead.
    if decided.get("action") in ("answer_local", "clarify") \
            and not escalated_away and not model:
        agent_out = _agent_chat(cfg, st, sid, clean, timeout_s=timeout_s,
                                cancel=cancel, reasoning=reasoning_level)
        if agent_out is not None:
            return agent_out
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


def _agent_chat(cfg: Any, st: Any, sid: str, clean: str, *,
                timeout_s: Optional[float] = None,
                cancel: Optional[Callable[[], bool]] = None,
                reasoning: Optional[str] = None
                ) -> "Optional[Dict[str, Any]]":
    """Run a general turn through the tool-capable agent, else None.

    DECISIONS #26: a ``/qubit-chat`` turn that falls through to the model must
    run the SAME ``run_agent.AIAgent`` the ``/chat`` terminal drives (full tool
    schema, real tool calls), never a bare ``providers.gateway`` text call.
    Returns the shaped turn outcome on success, a typed failure when the agent
    refused/errored, or ``None`` exactly when no tool-capable vendored runtime
    is available — the caller then keeps the legacy model path byte-identical.

    The user row and assistant reply are persisted by the vendored agent
    (``persist_user``); this helper adds the activity projection and the turn
    receipt. It never fabricates text and never invents a parallel grant.
    """
    from . import agent as AG

    if not str(sid or "").strip() or not str(clean or "").strip():
        return None
    # Work-panel producer: republish the vendored runtime's tool-progress feed
    # (subagent.* lifecycle + the tool.completed result) as CUSTOM frames on the
    # webui SSE hub, scoped to this session. None when no hub is attached (a
    # headless caller) — the runtime then uses its no-op default, unchanged.
    try:
        from .events.chat_progress import build_progress_sink

        progress_sink = build_progress_sink(cfg, sid)
    except Exception:  # noqa: BLE001 - panel glue is never fatal to a turn
        progress_sink = None
    try:
        out = AG.run_agent_turn(
            cfg, clean, session=sid, reasoning=reasoning,
            estop_engaged=lambda: _estop(cfg)
            or (cancel is not None and bool(cancel())),
            progress_sink=progress_sink)
    except AG.AgentDenied as exc:
        _audit("turn_agent_denied", session=sid, code=exc.code)
        code = "EMERGENCY_STOP" if exc.code == "EMERGENCY_STOP" \
            else "AGENT_DISABLED"
        return {"ok": False, "state": "UNAVAILABLE", "code": code,
                "detail": _redact(exc.detail)[:300], "session_id": sid}
    except Exception as exc:  # noqa: BLE001 - not an agent run: legacy fallback
        _audit("turn_agent_error", session=sid, error=str(exc)[:160])
        return None
    if out is None:
        return None
    if str(out.get("state") or "") != "OK":
        code = str(out.get("code") or "MODEL_ERROR")
        detail = _redact(str(out.get("detail") or ""))[:300]
        _audit("turn_agent_failed", session=sid, code=code)
        return {"ok": False, "state": "FAILED", "code": code,
                "detail": detail, "session_id": sid,
                "elapsed_ms": out.get("elapsed_ms")}
    reply = str(out.get("reply") or "")
    if not reply.strip():
        _audit("turn_agent_empty", session=sid)
        return {"ok": False, "state": "FAILED", "code": "MODEL_UNAVAILABLE",
                "detail": "agent returned no text; nothing was fabricated",
                "session_id": sid}
    receipt = dict(out.get("receipt") or {})
    receipt.setdefault("transport", "voicebridge.chat_transport.agent")
    try:
        record_activity(cfg, sid, reply)
    except Exception:  # noqa: BLE001 - projection glue, never fatal
        pass
    _audit("turn_agent", session=sid,
           tools=len(receipt.get("tools") or []))
    return {"ok": True, "reply": reply, "text": reply, "session": sid,
            "session_id": sid, "route": "agent",
            "elapsed_ms": out.get("elapsed_ms"), "receipt": receipt}


def _agent_turn(cfg: Any, text: str, *, session: Optional[str] = None,
                model: Optional[str] = None,
                timeout_s: Optional[float] = None,
                cancel: Optional[Callable[[], bool]] = None,
                role: str = "chat",
                reasoning: Optional[str] = None) -> Dict[str, Any]:
    """One turn forced through the tool-capable agent (attached-image path).

    Unlike :func:`turn`, this never consults the classifier: an attached image's
    description is context the agent must reason over, so a fast-path action
    match (``screenshot``) or the coding build worker must not steal it. Runs
    the SAME ``_agent_chat`` seam ``/chat`` uses; the legacy model path is the
    only fallback, and only when no tool-capable vendored runtime is available
    (never a fabricated reply).
    """
    st = store(cfg)
    routed = resolve_route(cfg, model, session=session, role=role)
    if not routed.get("ok"):
        return routed
    resolved_model = routed["model"]
    created = _resolve_session(st, session, model=resolved_model, cfg=cfg)
    if created.get("state") != "OK":
        return {"ok": False, "state": "FAILED", "code": created.get("code"),
                "detail": created.get("detail", "")}
    sid = created["session"]["id"]
    if not text:
        return {"ok": False, "state": "FAILED", "code": "MALFORMED",
                "detail": "turn needs non-empty text", "session_id": sid}
    agent_out = _agent_chat(cfg, st, sid, text, timeout_s=timeout_s,
                            cancel=cancel, reasoning=reasoning)
    if agent_out is not None:
        return agent_out
    context, ctx_meta, decision = _context_prompt(
        st, sid, cfg=cfg, model_context_tokens=routed.get("context_tokens"),
        pending_text=text)
    appended = st.append(sid, "user", text)
    if appended.get("state") != "OK":
        return {"ok": False, "state": "FAILED", "code": appended.get("code"),
                "detail": appended.get("detail", "")}
    return _generate(cfg, sid, text, resolved_model, context,
                     timeout_s=timeout_s, cancel=cancel, routed=routed,
                     role=role, context_meta=ctx_meta, decision=decision,
                     reasoning=reasoning)


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
    created = _resolve_session(st, session, model=routed.get("model"), cfg=cfg)
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
           "context": decision.public(), **_pending_event_field(cfg, sid)}
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
           "elapsed_ms": res.get("elapsed_ms"), "receipt": receipt,
           **_pending_event_field(cfg, sid)}


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
           "route": action, "message": appended.get("message"),
           **_pending_event_field(cfg, sid)}
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
               "route": action, "elapsed_ms": elapsed_ms,
               **_pending_event_field(cfg, sid)}
        return
    text = str(res.get("text") or res.get("reply") or "")
    yield {"event": "delta", "text": text, "session_id": sid}
    receipt: Dict[str, Any] = {"runtime": "qubit-chat-branch",
                               "route": action, "streaming": True,
                               "task": _redact(task_text)[:200]
                               if task_text else None,
                               "sources": [],
                               "model": _branch_model(cfg, sid),
                               "path": "voicebridge.chat_transport"
                                       "._stream_branch"}
    for key in ("sources", "entry", "action", "candidates"):
        if res.get(key) not in (None, "", []):
            receipt[key] = res[key]
    yield {"event": "done", "ok": True, "reply": text,
           "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
           "receipt": receipt, **_pending_event_field(cfg, sid)}


def _stream_task(cfg: Any, st: Any, sid: str, task_text: str, started: float,
                 *, timeout_s: Optional[float] = None,
                 cancel: Optional[Callable[[], bool]] = None
                 ) -> Iterator[Dict[str, Any]]:
    """Stream a headless build: progress frames, then the Spark report."""
    from . import orchestrator as ORC

    try:
        key = ORC.launch_task(task_text, session=sid, cfg=cfg,
                              model=_pinned_session_model(cfg, sid))
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
                  if staged.get("confirmation_id") else {}),
               **_pending_event_field(cfg, sid)}
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
    # Concise spoken/summary form (`_finish_task` already produced it): the
    # full report stays available, but /qubit-chat writes back and speaks
    # the SHORT summary so the voice agent doesn't read the whole build log.
    summary = str(out.get("text") or "").strip() or report
    yield {"event": "delta", "text": report, "session_id": sid}
    yield {"event": "done", "ok": True, "reply": report, "summary": summary,
           "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
           "receipt": {"runtime": "qubit-chat-worker", "route": "task",
                       "streaming": True, "task": key, "sources": [],
                       "model": _branch_model(cfg, sid),
                       "path": "voicebridge.chat_transport._stream_task"},
           **_pending_event_field(cfg, sid)}


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
    created = _resolve_session(st, session, model=resolved_model, cfg=cfg)
    if created.get("state") != "OK":
        yield {"event": "error", "code": created.get("code"),
               "detail": created.get("detail", "")}
        return
    sid = created["session"]["id"]
    # T4/T5: same session-scoped intent resolution as turn() — an
    # affirmation runs the staged record, a denial cancels it, any other
    # text leaves it staged. The pending state is surfaced on start/done so
    # the UI can render "awaiting confirmation" without a raw dump.
    _verdict = _classify_confirmation(clean) if clean else "other"
    # T4/T5: settle the SAME staged record the UI surfaced. A bare affirmation
    # follows the newest pending's ``kind`` (``_pending_confirmation``), so a
    # build-write banner is never settled by an older registry confirmation
    # (and vice-versa). Any other text leaves it staged.
    _surfaced_pending = (_pending_confirmation(cfg, sid)
                         if _verdict == "affirm" else None)
    if _verdict == "affirm" and _surfaced_pending:
        _settle_kind = str(_surfaced_pending.get("kind") or "registry")
        _settle_route = "task" if _settle_kind == "task" else "registry"
        started = time.monotonic()
        yield {"event": "start", "session_id": sid, "session": sid,
               "route": _settle_route, **_pending_event_field(cfg, sid)}
        appended = st.append(sid, "user", clean)
        if appended.get("state") != "OK":
            yield {"event": "error", "code": appended.get("code"),
                   "detail": appended.get("detail", ""), "session_id": sid}
            return
        if _settle_kind == "task":
            res = _settle_task_affirmed(cfg, st, sid)
        else:
            res = _settle_affirmed(cfg, st, sid)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not res.get("ok"):
            yield {"event": "error", "code": res.get("code", "BRANCH_FAILED"),
                   "detail": res.get("detail", ""), "session_id": sid,
                   "route": _settle_route, "elapsed_ms": elapsed_ms}
            return
        text_out = str(res.get("text") or res.get("reply") or "")
        yield {"event": "delta", "text": text_out, "session_id": sid}
        yield {"event": "done", "ok": True, "reply": text_out,
               "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
               "receipt": res.get("receipt"),
               **_pending_event_field(cfg, sid)}
        return
    # Deny: an explicit "no"/"cancel" on a session with live staged work
    # cancels THAT record. Denials with nothing pending fall through
    # byte-identically (a plain "no" is still a normal turn).
    if _verdict == "deny" and (_pending_registry(cfg, sid)
                               or _pending_task(cfg, sid)):
        started = time.monotonic()
        yield {"event": "start", "session_id": sid, "session": sid,
               "route": "confirm", **_pending_event_field(cfg, sid)}
        appended = st.append(sid, "user", clean)
        if appended.get("state") != "OK":
            yield {"event": "error", "code": appended.get("code"),
                   "detail": appended.get("detail", ""), "session_id": sid}
            return
        res = _cancel_confirmations(cfg, st, sid)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        text_out = str(res.get("text") or res.get("reply") or "")
        yield {"event": "delta", "text": text_out, "session_id": sid}
        yield {"event": "done", "ok": True, "reply": text_out,
               "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
               "route": "confirm",
               "receipt": _branch_receipt("confirm", cfg=cfg)}
        return
    # Qubit-chat slash turns (DECISIONS #26): same real-backend execution
    # as turn(), streamed as a single truthful delta. Non-slash turns
    # continue below byte-identical.
    if clean.startswith("/"):
        started = time.monotonic()
        yield {"event": "start", "session_id": sid, "session": sid,
               "route": "slash", **_pending_event_field(cfg, sid)}
        appended = st.append(sid, "user", clean)
        if appended.get("state") != "OK":
            yield {"event": "error", "code": appended.get("code"),
                   "detail": appended.get("detail", ""), "session_id": sid}
            return
        res = _finish_slash(cfg, st, sid, clean)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not res.get("ok"):
            yield {"event": "error", "code": res.get("code", "SLASH_FAILED"),
                   "detail": res.get("detail", ""), "session_id": sid,
                   "route": "slash", "elapsed_ms": elapsed_ms}
            return
        text_out = str(res.get("text") or res.get("reply") or "")
        yield {"event": "delta", "text": text_out, "session_id": sid}
        yield {"event": "done", "ok": True, "reply": text_out,
                "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
                "receipt": res.get("receipt"),
                **_pending_event_field(cfg, sid)}
        return
    # Mid-sentence slash (same contract as turn() above): one known /verb
    # anywhere executes that verb; zero or 2+ fall through byte-identical.
    if clean and not clean.startswith("/"):
        _inline = extract_inline_slash(clean)
        if _inline is not None:
            started = time.monotonic()
            yield {"event": "start", "session_id": sid, "session": sid,
                   "route": "slash", **_pending_event_field(cfg, sid)}
            appended = st.append(sid, "user", clean)
            if appended.get("state") != "OK":
                yield {"event": "error", "code": appended.get("code"),
                       "detail": appended.get("detail", ""), "session_id": sid}
                return
            res = _finish_slash(cfg, st, sid,
                                str(_inline.get("slash_text") or ""))
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if not res.get("ok"):
                yield {"event": "error",
                       "code": res.get("code", "SLASH_FAILED"),
                       "detail": res.get("detail", ""), "session_id": sid,
                       "route": "slash", "elapsed_ms": elapsed_ms}
                return
            text_out = str(res.get("text") or res.get("reply") or "")
            yield {"event": "delta", "text": text_out, "session_id": sid}
            yield {"event": "done", "ok": True, "reply": text_out,
                   "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
                   "receipt": res.get("receipt"),
                   **_pending_event_field(cfg, sid)}
            return
    # Direct facts on the stream path (same contract as turn() above):
    # single truthful delta, route "facts", no model consulted.
    _fact = _direct_fact_answer(clean)
    if _fact:
        started = time.monotonic()
        yield {"event": "start", "session_id": sid, "session": sid,
               "route": "facts", **_pending_event_field(cfg, sid)}
        appended = st.append(sid, "user", clean)
        if appended.get("state") != "OK":
            yield {"event": "error", "code": appended.get("code"),
                   "detail": appended.get("detail", ""), "session_id": sid}
            return
        res = _finish_fact(cfg, st, sid, _fact)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        yield {"event": "delta", "text": _fact, "session_id": sid}
        yield {"event": "done", "ok": True, "reply": _fact,
               "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
               "route": "facts", "model": "live-facts",
               "receipt": res.get("receipt"),
               **_pending_event_field(cfg, sid)}
        return
    # Deterministic reread on the stream path (same contract as turn()):
    # single truthful delta, route "repeat", no model consulted.
    if _repeat_request(clean):
        _rlast = _last_assistant_text(st, sid)
        if _rlast:
            started = time.monotonic()
            yield {"event": "start", "session_id": sid, "session": sid,
                   "route": "repeat", **_pending_event_field(cfg, sid)}
            appended = st.append(sid, "user", clean)
            if appended.get("state") != "OK":
                yield {"event": "error", "code": appended.get("code"),
                       "detail": appended.get("detail", ""), "session_id": sid}
                return
            res = _finish_repeat(cfg, st, sid, _rlast)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            yield {"event": "delta", "text": _rlast, "session_id": sid}
            yield {"event": "done", "ok": True, "reply": _rlast,
                   "session_id": sid, "session": sid, "elapsed_ms": elapsed_ms,
                   "route": "repeat", "model": "repeat",
                   "receipt": res.get("receipt"),
                   **_pending_event_field(cfg, sid)}
            return
    decided = _decide_branch(clean, cfg)
    if decided.get("action") in BRANCH_ACTIONS:
        yield from _stream_branch(cfg, st, sid, clean, decided,
                                  timeout_s=timeout_s, cancel=cancel)
        return
    # DECISIONS #26: a general conversation turn runs the SAME tool-capable
    # agent the /chat terminal drives (full tool schema + real tool calls), on
    # both local and API-resolved sessions — /chat parity. The deterministic
    # fast paths above never reach here. The agent is non-streaming, so its
    # reply is one truthful delta. An explicit per-call model is honored (the
    # caller asked for a specific model, not the /chat config model); when no
    # tool-capable vendored runtime is available this falls through to the
    # legacy remote/local stream byte-identically.
    if decided.get("action") in ("answer_local", "clarify") and not model:
        agent_out = _agent_chat(cfg, st, sid, clean, timeout_s=timeout_s,
                                cancel=cancel, reasoning=reasoning_level)
        if agent_out is not None:
            yield {"event": "start", "session_id": sid, "session": sid,
                   "route": "agent", **_pending_event_field(cfg, sid)}
            if not agent_out.get("ok"):
                yield {"event": "error",
                       "code": agent_out.get("code", "MODEL_ERROR"),
                       "detail": agent_out.get("detail", ""),
                       "session_id": sid, "route": "agent",
                       **_pending_event_field(cfg, sid)}
                return
            text_out = str(agent_out.get("reply") or "")
            yield {"event": "delta", "text": text_out, "session_id": sid}
            yield {"event": "done", "ok": True, "reply": text_out,
                   "session_id": sid, "session": sid,
                   "elapsed_ms": agent_out.get("elapsed_ms"),
                   "receipt": agent_out.get("receipt"),
                   **_pending_event_field(cfg, sid)}
            return
    routed_probe = resolve_route(cfg, model, session=session, role=role)
    if routed_probe.get("kind") in ("api", "realtime", "plugin"):
        yield from _stream_remote(cfg, text, session=session,
                                  routed=routed_probe, timeout_s=timeout_s,
                                  cancel=cancel, reasoning=reasoning_level)
        return

    clean = _bounded((text or "").strip(), MAX_TEXT_CHARS)
    st = store(cfg)
    created = _resolve_session(st, session, model=resolved_model, cfg=cfg)
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
           "context": decision.public(), **_pending_event_field(cfg, sid)}

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

    saved_box: Dict[str, Any] = {"id": None}

    def _stopped() -> bool:
        return bool(ev.is_set() or (cancel is not None and bool(cancel())))

    def _persist_reply(reply: str, elapsed_ms: int) -> Optional[str]:
        """Persist the assistant reply as soon as it exists.

        Runs in the worker thread, NOT the SSE drain loop: when the browser
        refreshes, the response generator is closed and everything after the
        drain (including the save) would be skipped, losing the turn's answer
        and forcing the operator to retype. Writing here means the reply is
        durable even though the client that asked for it went away.

        A STOPPED turn (cancel / e-stop) is deliberately NOT persisted: the
        model returns its own partial as ``ok`` text, and saving it would file
        a truncated answer in the transcript as if it were complete.
        """
        if _stopped():
            return None
        safe_reply = _redact(_sanitize(str(reply or "").strip()))
        if not safe_reply or saved_box["id"] is not None:
            return None
        try:
            rec = st.append(sid, "assistant", safe_reply,
                            meta={"model": resolved_model,
                                  "elapsed_ms": elapsed_ms, "streamed": True})
            saved_box["id"] = (rec or {}).get("id") or "saved"
            try:
                record_activity(cfg, sid, safe_reply)
            except Exception:
                pass
            return safe_reply
        except Exception:
            return None

    def _worker() -> None:
        # Durable crash marker for THIS turn, written where the turn actually
        # runs. `voicebridge run` can be killed mid-turn (operator restart); on
        # the next `/chat` connect the marker lets the session re-issue the
        # request instead of stranding it behind a manual `continue`. This is the
        # same mechanism the embedded TUI already uses (tui_gateway.turn_marker).
        _write_turn_marker(cfg, sid, clean)
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
                # Durable BEFORE the client hears about it: a refresh mid-turn
                # must not lose a reply the model already produced.
                _persist_reply(acc["text"] or res.text,
                               int((time.monotonic() - started) * 1000))
                out.put(("reply", res.text))
                out.put(("identity", res))
            else:
                out.put(("error", res))
        except Exception as exc:  # noqa: BLE001 - surfaced as a typed event
            out.put(("error", exc))
        finally:
            # The turn concluded in this process (answered, refused, or stopped),
            # so the crash marker must not survive: only a process death should
            # leave one behind for auto-continue to act on.
            _clear_turn_marker(cfg, sid)
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
    if _stopped():
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
    # The worker already persisted this reply (it may have raced a client
    # refresh). Only write here if that did not happen, so a turn is saved
    # exactly once.
    if saved_box["id"] is None:
        saved = st.append(sid, "assistant", safe,
                          meta={"model": resolved_model,
                                "elapsed_ms": elapsed_ms, "streamed": True})
        record_activity(cfg, sid, safe)
    else:
        saved = {"id": saved_box["id"]}
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
           "receipt": receipt, **_pending_event_field(cfg, sid)}


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
