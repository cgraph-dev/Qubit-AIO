"""Packet 8U — per-session activity projection for the dashboard and voice.

The chat session page and the voice surface need one honest, session-scoped
read of *what happened*: the working-tree scope (git), media generation
readiness/activity, and a short spoken-length summary. This module owns that
projection plus the append-only event log it reads.

It deliberately does **not** duplicate
:class:`voicebridge.chat_transport.ChatSessionStore`. Sessions and messages stay
in ``chat_transport``; this module stores only activity events (its own schema
file) and *derives* the projection from the canonical subsystem APIs
(:mod:`voicebridge.git_scope`, :mod:`voicebridge.media_gen`,
:mod:`voicebridge.chat_transport`, :mod:`voicebridge.summarizer`).

No fabricated success: an unavailable subsystem is reported as ``UNAVAILABLE``
with its typed code and reason, never as an empty-but-OK section. Writes are
bounded, atomic (temp + fsync + ``os.replace``), and serialized by a process
lock; reads are pure.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA = "qubit-session-activity/1"
STORE_FILE = "session_activity.json"

EVENT_KINDS: Tuple[str, ...] = ("git_scope", "media_generation", "summary",
                                "note")
MAX_SESSIONS = 2000
MAX_EVENTS_PER_SESSION = 1000
MAX_DETAIL_CHARS = 2000
MAX_SESSION_CHARS = 80
MAX_ACTOR_CHARS = 120


class SessionActivityError(Exception):
    """Typed activity-store failure (never a bare traceback on the surface)."""

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


class SessionActivityStore:
    """Append-only per-session activity log (Qubit-owned, 0600, atomic)."""

    def __init__(self, state_dir: Any, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._dir = Path(state_dir)
        self._path = self._dir / STORE_FILE
        self._clock = clock
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = self._blank()

    @staticmethod
    def _blank() -> Dict[str, Any]:
        return {"schema": SCHEMA, "events": {}, "seq": 0}

    # -- io ----------------------------------------------------------------
    def reload(self) -> Dict[str, Any]:
        """Re-read from disk (idempotent; missing/corrupt resets to blank)."""
        with self._lock:
            data = self._blank()
            if self._path.is_file():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and raw.get("schema") == SCHEMA:
                        if isinstance(raw.get("events"), dict):
                            data["events"] = raw["events"]
                        if isinstance(raw.get("seq"), int):
                            data["seq"] = raw["seq"]
                except (OSError, ValueError):
                    data = self._blank()
            self._state = data
            return {"state": "OK", "count": len(data["events"])}

    def _flush(self) -> None:
        _atomic_write(self._path, json.dumps(self._state, ensure_ascii=False))

    # -- writes ------------------------------------------------------------
    def record(self, session_id: str, kind: str, *, detail: str = "",
               actor: str = "dashboard:user",
               meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Append one bounded event to a session's activity log."""
        sid = _bounded(session_id, MAX_SESSION_CHARS).strip()
        if not sid:
            raise SessionActivityError("SESSION_ACTIVITY_BAD_INPUT",
                                       "session_id is required")
        if kind not in EVENT_KINDS:
            raise SessionActivityError(
                "SESSION_ACTIVITY_BAD_KIND",
                f"kind must be one of {', '.join(EVENT_KINDS)}")
        with self._lock:
            bucket = self._state["events"].setdefault(sid, [])
            self._state["seq"] = int(self._state.get("seq", 0)) + 1
            event = {
                "id": self._state["seq"],
                "session_id": sid,
                "kind": kind,
                "detail": _redact(_bounded(detail, MAX_DETAIL_CHARS)),
                "actor": _redact(_bounded(actor, MAX_ACTOR_CHARS)),
                "at": self._clock(),
            }
            if isinstance(meta, dict) and meta:
                event["meta"] = {str(k): _redact(_bounded(v, MAX_DETAIL_CHARS))
                                 for k, v in list(meta.items())[:20]}
            bucket.append(event)
            if len(bucket) > MAX_EVENTS_PER_SESSION:
                del bucket[:len(bucket) - MAX_EVENTS_PER_SESSION]
            # Bound the number of tracked sessions (oldest activity first).
            if len(self._state["events"]) > MAX_SESSIONS:
                oldest = sorted(
                    self._state["events"],
                    key=lambda s: self._state["events"][s][-1].get("at", 0)
                    if self._state["events"][s] else 0)
                for stale in oldest[:len(self._state["events"]) - MAX_SESSIONS]:
                    self._state["events"].pop(stale, None)
            self._flush()
            return {"state": "OK", "code": "SESSION_ACTIVITY_RECORDED",
                    "event": dict(event), "count": len(bucket)}

    def list(self, session_id: str, *, kind: str = "", limit: int = 50,
             offset: int = 0) -> Dict[str, Any]:
        """Read a session's events newest-first (optionally by kind)."""
        sid = _bounded(session_id, MAX_SESSION_CHARS).strip()
        with self._lock:
            rows = list(self._state["events"].get(sid, []))
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        rows = list(reversed(rows))
        start = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        return {"state": "OK", "session_id": sid, "events": rows[start:start + limit],
                "total": len(rows), "limit": limit, "offset": start}

    def counts(self, session_id: str) -> Dict[str, int]:
        """Per-kind event counts for one session (zeros when absent)."""
        sid = _bounded(session_id, MAX_SESSION_CHARS).strip()
        with self._lock:
            rows = list(self._state["events"].get(sid, []))
        out = {kind: 0 for kind in EVENT_KINDS}
        for row in rows:
            kind = str(row.get("kind") or "")
            out[kind] = out.get(kind, 0) + 1
        return out

    def clear(self, session_id: str, *, confirmed: bool = False
              ) -> Dict[str, Any]:
        """Drop a session's events; destructive, so confirmation is required."""
        sid = _bounded(session_id, MAX_SESSION_CHARS).strip()
        with self._lock:
            bucket = self._state["events"].get(sid, [])
            if not confirmed:
                return {"state": "CONFIRM_REQUIRED",
                        "code": "SESSION_ACTIVITY_CONFIRM_REQUIRED",
                        "detail": f"clearing activity for {sid!r} needs confirm",
                        "session_id": sid, "would_remove": len(bucket)}
            removed = len(bucket)
            self._state["events"].pop(sid, None)
            self._flush()
            return {"state": "OK", "code": "SESSION_ACTIVITY_CLEARED",
                    "session_id": sid, "removed": removed}

    def export(self, session_id: str) -> Dict[str, Any]:
        sid = _bounded(session_id, MAX_SESSION_CHARS).strip()
        with self._lock:
            rows = list(self._state["events"].get(sid, []))
        return {"state": "OK", "schema": SCHEMA, "session_id": sid,
                "exported_at": self._clock(), "events": rows,
                "count": len(rows)}


def store(cfg: Any) -> SessionActivityStore:
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    st = SessionActivityStore(Path(state_dir))
    st.reload()
    return st


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
def _unavailable(code: str, detail: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": code,
            "detail": _redact(detail)[:240]}


def _git_section(cfg: Any, git_root: str) -> Dict[str, Any]:
    try:
        from . import git_scope as GS

        out = GS.status(cfg, git_root)
    except Exception as exc:  # typed GitError or missing repo
        code = getattr(exc, "code", "GIT_SCOPE_ERROR")
        return _unavailable(code, str(getattr(exc, "detail", exc)))
    if not isinstance(out, dict) or out.get("state") != "OK":
        return _unavailable(str((out or {}).get("code", "GIT_SCOPE_ERROR")),
                            str((out or {}).get("detail", "git scope failed")))
    status_text = str(out.get("status") or "")
    changed = [ln for ln in status_text.splitlines() if ln.strip()]
    return {"state": "OK", "root": _redact(out.get("root", "")),
            "changed_count": len(changed), "changed": changed[:200],
            "truncated": bool(out.get("truncated")),
            "rollback": out.get("rollback", "")}


def _media_section(cfg: Any) -> Dict[str, Any]:
    try:
        from . import media_gen as MG

        out = MG.status(cfg)
    except Exception as exc:
        return _unavailable("MEDIA_GEN_ERROR", str(exc))
    if not isinstance(out, dict) or out.get("state") != "OK":
        return _unavailable(str((out or {}).get("code", "MEDIA_GEN_ERROR")),
                            str((out or {}).get("detail", "media status failed")))
    return {"state": "OK", "estop": bool(out.get("estop")),
            "configured": list(out.get("configured", []) or []),
            "unavailable": list(out.get("unavailable", []) or [])}


def _last_assistant_text(session_store: Any, session_id: str) -> str:
    if session_store is None:
        return ""
    try:
        shown = session_store.show(session_id)
    except Exception:
        return ""
    if not isinstance(shown, dict) or shown.get("state") != "OK":
        return ""
    for msg in reversed(shown.get("messages", []) or []):
        if msg.get("role") == "assistant" and msg.get("content"):
            return str(msg["content"])
    return ""


def _summary_section(cfg: Any, session_id: str, session_store: Any,
                     events: List[Dict[str, Any]],
                     summarize_fn: Optional[Callable[..., str]]) -> Dict[str, Any]:
    fn = summarize_fn
    if fn is None:
        try:
            from .summarizer import summarize as fn  # type: ignore[assignment]
        except Exception as exc:
            return _unavailable("SUMMARY_UNAVAILABLE", str(exc))
    source_text = _last_assistant_text(session_store, session_id)
    origin = "session"
    if not source_text:
        source_text = " ".join(
            str(ev.get("detail") or "") for ev in events if ev.get("detail"))
        origin = "activity"
    if not source_text.strip():
        return {"state": "EMPTY", "text": "", "source": "none",
                "detail": "no assistant message or activity to summarize"}
    try:
        text = fn(source_text, {"provider": "extractive", "max_words": 40})
    except Exception as exc:
        return _unavailable("SUMMARY_UNAVAILABLE", str(exc))
    return {"state": "OK", "text": str(text or ""), "source": origin}


def build_projection(cfg: Any, session_id: str, *, git_root: str = "",
                     limit: int = 20, session_store: Any = None,
                     activity_store: Optional[SessionActivityStore] = None,
                     summarize_fn: Optional[Callable[..., str]] = None
                     ) -> Dict[str, Any]:
    """Compose git scope + media + summary + recent events for one session.

    Every section is derived from the canonical subsystem and carries its own
    honest ``state`` (``OK`` / ``EMPTY`` / ``UNAVAILABLE``). ``session_store``
    is the canonical chat store; when omitted the summary section is ``EMPTY``
    (no message source) rather than invented.
    """
    sid = _bounded(session_id, MAX_SESSION_CHARS).strip()
    acts = activity_store or store(cfg)
    listed = acts.list(sid, limit=max(1, min(int(limit), 200)))
    events = listed.get("events", [])
    return {
        "state": "OK",
        "schema": SCHEMA,
        "session_id": sid,
        "generated_at": time.time(),
        "git_scope": _git_section(cfg, git_root),
        "media": _media_section(cfg),
        "summary": _summary_section(cfg, sid, session_store, events,
                                    summarize_fn),
        "events": events,
        "counts": acts.counts(sid),
    }
