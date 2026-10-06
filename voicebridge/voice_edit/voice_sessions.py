"""Voice-session namespace + retention.

Voice turns (wake + Right-Ctrl) must never overlay the web/agent ``/chat``
sessions. They live in their own ``voice-*`` namespace on the ONE canonical
chat store (:class:`~voicebridge.qubit_session_store.QubitSessionStore` over the
vendored ``state.db``) and old voice sessions are pruned so they cannot pile up.

Two modes (``voice_edit.voice_session_mode``):

* ``single`` (default) — one stable session (``voice-chat``) carries the spoken
  conversation, so follow-ups anchor to the same transcript.
* ``per_command`` — every command gets a deterministic session id derived from
  its ``request_id`` / timestamp (``voice-<yyyymmdd-HHMMSS>-<short-hash>``), so
  each command is fully isolated.

Everything here is pure and fail-soft: no function raises, and ``prune`` never
touches a non-``voice-*`` session. Retention is config-driven with sane
defaults (24 h TTL, ~200 messages/session, ~50 sessions).
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

#: Every voice session id starts with this; the namespace is what isolates the
#: spoken conversation from ``dashboard-chat``/``/chat`` sessions.
VOICE_SESSION_PREFIX = "voice-"

#: The stable ``single``-mode session id. MUST equal the id the wake path
#: and helper already use (`voicebridge/voice_edit/wake.py::_voice_session_id`,
#: `desktop_helper._DEFAULT_VOICE_SESSION`) so a wake turn, its staged
#: confirmation, and the service turn all land in ONE session — a divergence
#: here breaks the CT-confirm settle (the "yes" would target a different
#: session than the staged action). Already ``voice-``-prefixed, so it is
#: covered by isolation + retention.
SINGLE_SESSION_ID = "voice-chat"

#: Sane retention defaults.
DEFAULT_TTL_S = 24 * 60 * 60
DEFAULT_MAX_MESSAGES_PER_SESSION = 200
DEFAULT_MAX_SESSIONS = 50

#: Config keys (under ``voice_edit``).
_MODE_KEY = "voice_session_mode"
_TTL_KEY = "voice_session_ttl_s"
_MAX_MESSAGES_KEY = "voice_session_max_messages"
_MAX_SESSIONS_KEY = "voice_session_max_sessions"

_PER_COMMAND_ALIASES = frozenset(
    {"per_command", "per-command", "percommand", "per command"})


def _cfg_get(cfg: Any, *path: str, default: Any = None) -> Any:
    """Read a nested config value without ever raising.

    Supports both the real :class:`voicebridge.config.Config` and the plain
    ``dict``/fake configs the tests use (``cfg.get("voice_edit", key)``).
    """
    if cfg is None:
        return default
    try:
        get = getattr(cfg, "get", None)
        if callable(get):
            return get(*path, default=default)
    except TypeError:
        node: Any = cfg
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node
    except Exception:  # noqa: BLE001 - fail-soft read
        return default
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out > 0 else default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    return out if out >= 1 else default


def session_mode(cfg: Any) -> str:
    """``"single"`` (default) or ``"per_command"``; never raises."""
    try:
        raw = str(_cfg_get(cfg, "voice_edit", _MODE_KEY, default="") or "")
    except Exception:  # noqa: BLE001
        raw = ""
    return "per_command" if raw.strip().lower() in _PER_COMMAND_ALIASES \
        else "single"


def is_voice_session(sid: Any) -> bool:
    """True when ``sid`` is in the ``voice-*`` namespace; never raises."""
    try:
        return str(sid or "").startswith(VOICE_SESSION_PREFIX)
    except Exception:  # noqa: BLE001
        return False


def _seconds(now: Any) -> float:
    if now is None or isinstance(now, bool):
        return time.time()
    if isinstance(now, (int, float)):
        return float(now)
    try:
        return float(now.timestamp())  # datetime-like
    except Exception:  # noqa: BLE001
        return time.time()


def voice_session_id(cfg: Any, *, request_id: Optional[str] = None,
                     now: Any = None) -> str:
    """The session id a voice turn belongs to (deterministic; never raises).

    * ``single`` -> :data:`SINGLE_SESSION_ID` (stable across turns).
    * ``per_command`` -> ``voice-<yyyymmdd-HHMMSS>-<short-hash>`` where the hash
      is derived from ``request_id`` when present, else the timestamp second.
    """
    try:
        if session_mode(cfg) != "per_command":
            return SINGLE_SESSION_ID
        ts = _seconds(now)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
        seed = str(request_id or "").strip() or f"{int(ts)}"
        digest = hashlib.sha1(
            seed.encode("utf-8", "replace")).hexdigest()[:8]
        return f"{VOICE_SESSION_PREFIX}{stamp}-{digest}"
    except Exception:  # noqa: BLE001 - always a usable voice id
        return SINGLE_SESSION_ID


def retention_policy(cfg: Any) -> Dict[str, Any]:
    """The resolved retention numbers (config-driven, sane defaults)."""
    ttl = _as_float(
        _cfg_get(cfg, "voice_edit", _TTL_KEY, default=DEFAULT_TTL_S),
        float(DEFAULT_TTL_S))
    max_messages = _as_int(
        _cfg_get(cfg, "voice_edit", _MAX_MESSAGES_KEY,
                 default=DEFAULT_MAX_MESSAGES_PER_SESSION),
        DEFAULT_MAX_MESSAGES_PER_SESSION)
    max_sessions = _as_int(
        _cfg_get(cfg, "voice_edit", _MAX_SESSIONS_KEY,
                 default=DEFAULT_MAX_SESSIONS),
        DEFAULT_MAX_SESSIONS)
    return {"ttl_s": ttl, "max_messages_per_session": max_messages,
            "max_sessions": max_sessions}


def _store(cfg: Any, store: Any = None) -> Any:
    """The canonical chat store (injectable for tests). Never raises."""
    if store is not None:
        return store
    try:
        from .. import chat_transport
        return chat_transport.store(cfg)
    except Exception:  # noqa: BLE001 - caller degrades to a no-op prune
        return None


def _list_sessions(store: Any) -> List[Dict[str, Any]]:
    """Every session row the store exposes, or ``[]`` on any fault."""
    for kwargs in ({"limit": 1000, "include_archived": True},
                   {"limit": 1000}, {}):
        try:
            data = store.list(**kwargs)
        except TypeError:
            continue  # a stub store with a narrower signature
        except Exception:  # noqa: BLE001 - broken store -> nothing to prune
            return []
        if isinstance(data, dict):
            rows = data.get("sessions")
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        return []
    return []


def _recency(row: Dict[str, Any]) -> float:
    for key in ("last_active", "updated_at", "last_active_at",
                "started_at", "created_at"):
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and value > 0:
            return float(value)
    return 0.0


def _message_count(row: Dict[str, Any]) -> int:
    try:
        return int(row.get("message_count") or 0)
    except (TypeError, ValueError):
        return 0


def _delete(store: Any, sid: str) -> bool:
    """Delete one voice session (confirm-gated store API). Never raises."""
    try:
        store.delete(sid, confirm=True)
        return True
    except TypeError:
        # A stub store may not accept ``confirm``.
        try:
            store.delete(sid)
            return True
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False


def prune(cfg: Any, *, now: Any = None, store: Any = None,
          protect: Any = None) -> Dict[str, Any]:
    """Prune old/oversized ``voice-*`` sessions from the canonical store.

    Deletes voice sessions that are older than the TTL or over the per-session
    message cap, then keeps only the ``max_sessions`` newest survivors. Returns
    ``{ok, scanned, deleted, kept, deleted_ttl, deleted_messages, deleted_cap,
    failed}``. Best-effort and total: never raises, never touches a non-voice
    session.

    **The live stable session is never destroyed.** ``SINGLE_SESSION_ID`` (the
    ``single``-mode conversation, ``voice-chat``) is exempt from TTL, message-cap
    *and* max-sessions deletion: it is the one session a voice turn is actively
    reading/writing, so deleting it would silently wipe the live transcript (live
    2026-10-06: ``voice-chat`` held 588 messages against the 200-message cap, so
    the next hot-path sweep deleted the conversation). An optional ``protect``
    iterable names additional ids to spare -- a caller passes the currently
    active session (e.g. a ``per_command`` id resolved for the in-flight turn).
    """
    result: Dict[str, Any] = {
        "ok": True, "scanned": 0, "deleted": 0, "kept": 0,
        "deleted_ttl": 0, "deleted_messages": 0, "deleted_cap": 0,
        "failed": 0,
    }
    try:
        protected: set = {SINGLE_SESSION_ID}
        if protect is not None:
            if isinstance(protect, str):
                protect = [protect]  # a bare id, not an iterable of chars
            try:
                for sid in protect:
                    key = str(sid or "").strip()
                    if key:
                        protected.add(key)
            except TypeError:
                key = str(protect or "").strip()
                if key:
                    protected.add(key)
        policy = retention_policy(cfg)
        ttl = float(policy["ttl_s"])
        max_messages = int(policy["max_messages_per_session"])
        max_sessions = int(policy["max_sessions"])
        ts_now = _seconds(now)
        canonical = _store(cfg, store)
        if canonical is None:
            return result
        sessions = [r for r in _list_sessions(canonical)
                    if is_voice_session(r.get("id"))]
        result["scanned"] = len(sessions)
        # Newest first so the cap's "keep newest" is unambiguous.
        sessions.sort(key=_recency, reverse=True)

        reasons: Dict[str, str] = {}
        for row in sessions:
            sid = str(row.get("id"))
            if sid in protected:
                continue  # the live/stable session is never reclaimed
            last = _recency(row)
            if last > 0 and (ts_now - last) > ttl:
                reasons.setdefault(sid, "ttl")
                continue
            if _message_count(row) > max_messages:
                reasons.setdefault(sid, "messages")
        survivors = [r for r in sessions if str(r.get("id")) not in reasons]
        # Cap only the reclaimable sessions so the protected ones never count
        # against the budget and can never be displaced by newer sessions.
        cap_pool = [r for r in survivors if str(r.get("id")) not in protected]
        if len(cap_pool) > max_sessions:
            for row in cap_pool[max_sessions:]:
                reasons.setdefault(str(row.get("id")), "cap")

        for sid in reasons:
            if _delete(canonical, sid):
                result["deleted"] += 1
                result[f"deleted_{reasons[sid]}"] += 1
            else:
                result["failed"] += 1
        result["kept"] = max(0, len(sessions) - result["deleted"])
        return result
    except Exception:  # noqa: BLE001 - retention is best-effort
        return result


__all__ = [
    "VOICE_SESSION_PREFIX", "SINGLE_SESSION_ID", "DEFAULT_TTL_S",
    "DEFAULT_MAX_MESSAGES_PER_SESSION", "DEFAULT_MAX_SESSIONS",
    "session_mode", "voice_session_id", "is_voice_session",
    "retention_policy", "prune",
]
