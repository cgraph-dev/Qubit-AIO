"""Canonical in-process session subscription / viewer registry.

Qubit's one gateway listener serves the OpenClaw RPC dialect over a
request/response WebSocket. The upstream session-subscription surface keeps a
server-side registry of live subscribers and viewers per session and fans a
``session.change`` event to them. Qubit keeps the SAME real registry state in
this one module (one authority per process), consulted by every surface that
needs to know who is currently subscribed to or viewing a session.

Delivery is request-scoped rather than server-push: a caller reads the live
registry through the gateway (``sessions.subscribe`` / ``sessions.viewers.set``
return the authoritative state, and the next request observes it). That is a
real local counterpart of the upstream mechanism, tagged with its own typed
code so it is never presented as upstream server-push. No second store, no
duplicate subscriber authority.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

#: SAFE_EQUIVALENT typed codes (real local registry, request-scoped delivery).
SUBSCRIPTIONS_LOCAL_REGISTRY = "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY"
VIEWERS_LOCAL_REGISTRY = "SESSIONS_VIEWERS_LOCAL_REGISTRY"

_KINDS = ("session", "messages", "viewers")


class SessionSubscriptionRegistry:
    """One process-wide registry of live session subscribers/viewers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: Dict[str, Dict[str, Any]] = {}

    def subscribe(self, session_id: str, *, kind: str = "session",
                  viewers: Optional[List[str]] = None,
                  cursor: Any = 0) -> Dict[str, Any]:
        sid = str(session_id or "").strip()[:80]
        if not sid:
            raise ValueError("session id is required")
        k = kind if kind in _KINDS else "session"
        sub_id = f"sub-{uuid.uuid4().hex[:16]}"
        record = {
            "id": sub_id,
            "sessionId": sid,
            "kind": k,
            "cursor": int(cursor or 0),
            "viewers": [str(v)[:120] for v in (viewers or [])],
            "createdAt": time.time(),
        }
        with self._lock:
            self._subs[sub_id] = record
        return dict(record)

    def unsubscribe(self, sub_id: str) -> Optional[Dict[str, Any]]:
        key = str(sub_id or "").strip()
        with self._lock:
            return self._subs.pop(key, None)

    def unsubscribe_session(self, session_id: str, *,
                            kind: Optional[str] = None) -> int:
        sid = str(session_id or "").strip()
        removed = 0
        with self._lock:
            for key in list(self._subs):
                rec = self._subs[key]
                if rec["sessionId"] == sid and (kind is None
                                                or rec["kind"] == kind):
                    self._subs.pop(key, None)
                    removed += 1
        return removed

    def set_viewers(self, session_id: str,
                    viewers: List[str]) -> Dict[str, Any]:
        sid = str(session_id or "").strip()[:80]
        if not sid:
            raise ValueError("session id is required")
        clean = sorted({str(v)[:120] for v in (viewers or []) if str(v)})
        with self._lock:
            for rec in self._subs.values():
                if rec["sessionId"] == sid:
                    rec["viewers"] = clean
        return {"sessionId": sid, "viewers": clean}

    def list(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sid = str(session_id or "").strip()
        with self._lock:
            return [dict(rec) for rec in self._subs.values()
                    if not sid or rec["sessionId"] == sid]

    def active_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def reset(self) -> None:
        with self._lock:
            self._subs.clear()


#: The one registry (module singleton).
REGISTRY = SessionSubscriptionRegistry()


def subscribe(session_id: str, **kwargs: Any) -> Dict[str, Any]:
    return REGISTRY.subscribe(session_id, **kwargs)


def unsubscribe(sub_id: str) -> Optional[Dict[str, Any]]:
    return REGISTRY.unsubscribe(sub_id)


def unsubscribe_session(session_id: str, **kwargs: Any) -> int:
    return REGISTRY.unsubscribe_session(session_id, **kwargs)


def set_viewers(session_id: str, viewers: List[str]) -> Dict[str, Any]:
    return REGISTRY.set_viewers(session_id, viewers)


def list_subscriptions(session_id: Optional[str] = None
                       ) -> List[Dict[str, Any]]:
    return REGISTRY.list(session_id)
