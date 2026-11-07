"""HUD replay collector — Qubit-native re-point of ``backend/collectors/replay.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: MAP (Qubit has the data; different schema, one authority)
-------------------------------------------------------------------
The previous lane marked this GAP on the reasoning that Qubit's schema differs
and "the session authority exposes no replay/rewind read model". The first half
is right and the second is wrong. Evidence:

* upstream ``replay.py`` is entirely ``collect_sessions(...)`` +
  ``SELECT messages WHERE session_id ORDER BY timestamp``. It does NOT replay a
  state machine, and its own name for the endpoint is ``GET
  /api/hud/replay/{session_id}`` returning ``ReplayRun``/``ReplayDetail``.
* :class:`voicebridge.qubit_session_store.QubitSessionStore` publishes exactly
  those two reads: ``list()`` (id, title, source, model, started_at,
  last_active, message_count, tool_call_count, tokens) and ``messages(sid,
  limit, offset)`` -> ``{state, session_id, messages[], total, limit,
  offset}`` where a message is ``{role, content, timestamp, meta{id}}``.
* So the panel is a port over the authority. What genuinely does NOT exist is a
  durable turn-boundary/tool-call timeline: the authority's message rows carry
  no ``tool_calls``/``reasoning``/``token_count`` columns (upstream read those
  from its own ``messages`` table). Those fields are reported as ``null`` per
  message, and the panel renders the honest reason instead of an empty tool
  trace — it does not synthesise a tool step from prose.

``rewind`` EXISTS as a mutation in the authority (``QubitSessionStore.rewind``,
confirmation-gated). This collector never calls it: the HUD is a viewer, and
``rewind_available`` only reports whether the session can be addressed for it.
The action's Qubit counterpart is the dashboard/CLI, never this page.

Every read goes through the authority. No sqlite3 connection is opened here.
Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

#: One page of replay runs. The list route is cheap; the panel shows a window
#: and reports the store's own total so "showing N of M" stays truthful.
RUNS_PAGE = 50
#: Transcript page for one run. The authority caps at 1000.
MESSAGES_PAGE = 200


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(float(value)).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value:
        return value
    return None


def _store(cfg: Any):
    from ..qubit_session_store import QubitSessionStore

    return QubitSessionStore(cfg)


def _run(row: Dict[str, Any]) -> Dict[str, Any]:
    """One replay run: the session facts a transcript player needs."""
    return {
        "session_id": str(row.get("id") or ""),
        "title": str(row.get("title") or row.get("name") or ""),
        "source": str(row.get("source") or ""),
        "model": str(row.get("model") or ""),
        "started_at": _iso(row.get("started_at") or row.get("created_at")),
        "last_active": _iso(row.get("last_active") or row.get("ended_at")),
        "message_count": int(row.get("message_count") or 0),
        "tool_call_count": int(row.get("tool_call_count") or 0),
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "archived": bool(row.get("archived")),
        "is_active": bool(row.get("is_active")),
        "preview": str(row.get("preview") or "")[:200],
    }


def _message(row: Dict[str, Any], offset: int) -> Dict[str, Any]:
    """One transcript step.

    ``id`` comes from the authority's ``meta`` envelope. ``tool_calls``,
    ``reasoning`` and ``token_count`` are ``None`` — the authority publishes no
    such columns — and the route's ``trace_note`` says so, so the panel can
    state the gap per run rather than render three empty columns as zeros.
    """
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return {
        "index": offset,
        "id": meta.get("id"),
        "role": str(row.get("role") or "unknown"),
        "content": str(row.get("content") or ""),
        "timestamp": _iso(row.get("timestamp")),
        "tool_calls": None,
        "reasoning": None,
        "token_count": None,
    }


def replay_runs(cfg: Any = None, *, limit: int = RUNS_PAGE,
                query: str = "") -> Dict[str, Any]:
    """The run list: recent sessions, shaped for a transcript player."""
    try:
        store = _store(cfg)
        page = store.list(limit=max(1, int(limit)), query=query or "")
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        return {"available": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "runs": [], "total": 0, "shown": 0,
                "authority": "voicebridge.qubit_session_store.QubitSessionStore"}
    rows = page.get("sessions") if isinstance(page, dict) else None
    if not isinstance(rows, list):
        return {"available": False,
                "detail": "the session authority returned no session list",
                "runs": [], "total": 0, "shown": 0,
                "authority": "voicebridge.qubit_session_store.QubitSessionStore"}
    runs = [_run(r) for r in rows if isinstance(r, dict)]
    total = int((page or {}).get("total") or len(runs)) if isinstance(page, dict) \
        else len(runs)
    return {
        "available": True,
        "detail": f"{len(runs)} replayable run(s)",
        "runs": runs,
        "total": total,
        "shown": len(runs),
        "query": query,
        "authority": "voicebridge.qubit_session_store.QubitSessionStore",
    }


def replay_detail(cfg: Any = None, session_id: str = "", *,
                  limit: int = MESSAGES_PAGE, offset: int = 0) -> Dict[str, Any]:
    """One run's transcript page, in order, with the trace caveat attached."""
    sid = str(session_id or "").strip()
    if not sid:
        return {"available": False, "state": "MALFORMED",
                "detail": "session_id is required", "session": None,
                "messages": [], "total": 0}
    try:
        store = _store(cfg)
        show = store.show(sid)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "detail": f"{type(exc).__name__}: {exc}",
                "session": None, "messages": [], "total": 0}
    state = str(show.get("state") or "")
    if state != "OK":
        return {"available": False,
                "state": state or "MISSING",
                "code": str(show.get("code") or ""),
                "detail": str(show.get("detail") or "session not found"),
                "session": None, "messages": [], "total": 0}
    try:
        page = store.messages(sid, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "detail": f"{type(exc).__name__}: {exc}",
                "session": _run(show.get("session") or {}),
                "messages": [], "total": 0}
    if str(page.get("state") or "") != "OK":
        return {"available": False,
                "state": str(page.get("state") or ""),
                "detail": str(page.get("detail") or ""),
                "session": _run(show.get("session") or {}),
                "messages": [], "total": 0}
    start = int(page.get("offset") or 0)
    messages = [_message(m, start + i) for i, m in
                enumerate(page.get("messages") or []) if isinstance(m, dict)]
    return {
        "available": True,
        "state": "OK",
        "detail": (f"{len(messages)} of {int(page.get('total') or 0)} "
                   f"message(s)"),
        "session": _run(show.get("session") or {}),
        "messages": messages,
        "total": int(page.get("total") or 0),
        "limit": int(page.get("limit") or limit),
        "offset": start,
        # The one honest gap on this panel, stated per run.
        "trace_note": ("The session authority publishes role/content/"
                       "timestamp only: no tool_calls, reasoning or token "
                       "count per message. A per-step trace is therefore not "
                       "available at this layer."),
        # Reported, never performed here.
        "rewind_available": True,
        "rewind_counterpart": "confirmation-gated QubitSessionStore.rewind "
                              "(dashboard / CLI), not this read-only page",
        "authority": "voicebridge.qubit_session_store.QubitSessionStore",
    }


def collect_replay(home: Optional[Any] = None, cfg: Any = None, *,
                   session_id: str = "", limit: int = RUNS_PAGE,
                   messages: int = MESSAGES_PAGE,
                   offset: int = 0) -> Dict[str, Any]:
    """The Replay panel: the run list, and one run's transcript when asked.

    ``home`` is accepted for call-site symmetry and unused — the authority
    resolves the Qubit home itself.
    """
    runs = replay_runs(cfg, limit=limit)
    out: Dict[str, Any] = {"available": runs.get("available", False),
                           "detail": runs.get("detail", ""),
                           "runs": runs.get("runs", []),
                           "total": runs.get("total", 0),
                           "shown": runs.get("shown", 0),
                           "session_id": str(session_id or ""),
                           "detail_view": None,
                           "authority": runs.get("authority", "")}
    if session_id:
        out["detail_view"] = replay_detail(
            cfg, session_id, limit=messages, offset=offset)
    return out


__all__ = ["collect_replay", "replay_runs", "replay_detail"]
