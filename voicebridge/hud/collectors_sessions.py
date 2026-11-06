"""Qubit HUD sessions collector.

Qubit-authored (not vendored): upstream ``hermes-hudui`` has a
``backend/collectors/sessions.py`` but it opens ``state.db`` directly, which
this tree forbids. The Sessions panel, however, is one of the seven in-scope
tabs, so the collector is written here against the ONE session authority.

Every read goes through :class:`voicebridge.qubit_session_store.QubitSessionStore`
(``CHAT_SESSION_AUTHORITY`` in ``voicebridge/qubit_compat.py``), which is the
same store ``/qubit-chat``, the Sessions page and the terminal all write. No
second ``sqlite3`` connection is opened anywhere in this module: ``state.db`` is
826 MB and a second writable opener is a corruption bug, not a style preference.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: One page of sessions. The authority caps a page at 1000; the panel shows a
#: bounded window and reports the true total so the UI can say "showing N of M"
#: instead of implying the store only holds N.
PAGE = 50


def _store(cfg: Any):
    from ..qubit_session_store import QubitSessionStore

    return QubitSessionStore(cfg)


def _summarise(row: Dict[str, Any]) -> Dict[str, Any]:
    """One session row, reduced to what the panel renders."""
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "source": str(row.get("source") or ""),
        "model": str(row.get("model") or ""),
        "created_at": row.get("created_at"),
        "last_active": row.get("last_active") or row.get("updated_at"),
        "message_count": int(row.get("message_count") or 0),
        "archived": bool(row.get("archived")),
    }


def collect_sessions(home: Optional[Any] = None, cfg: Any = None,
                     *, query: str = "", limit: int = PAGE
                     ) -> Dict[str, Any]:
    """A page of chat sessions plus the store's own totals.

    ``available`` is the honest gate: when the authority cannot be constructed
    the panel reports why rather than showing an empty list that reads as "no
    sessions".
    """
    try:
        store = _store(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        return {"available": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "sessions": [], "total": 0, "shown": 0,
                "authority": "voicebridge.qubit_session_store.QubitSessionStore"}

    out: Dict[str, Any] = {
        "available": True,
        "detail": "",
        "authority": "voicebridge.qubit_session_store.QubitSessionStore",
        "query": query,
        "sessions": [],
        "total": 0,
        "shown": 0,
        "page": max(1, int(limit)),
    }
    try:
        page = store.list(limit=max(1, int(limit)), query=query or "")
    except Exception as exc:  # noqa: BLE001
        out["available"] = False
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out

    rows: List[Dict[str, Any]] = []
    if isinstance(page, dict):
        raw = page.get("sessions") or page.get("items") or []
        for row in raw:
            if isinstance(row, dict):
                rows.append(_summarise(row))
        out["total"] = int(page.get("total") or len(rows))
    elif isinstance(page, list):
        for row in page:
            if isinstance(row, dict):
                rows.append(_summarise(row))
        out["total"] = len(rows)
    elif page is not None:
        # The authority returns a dict; anything else is a contract change we
        # report instead of coercing into a misleading empty list.
        out["available"] = False
        out["detail"] = f"unexpected session page type: {type(page).__name__}"
        return out

    out["sessions"] = rows
    out["shown"] = len(rows)
    return out
