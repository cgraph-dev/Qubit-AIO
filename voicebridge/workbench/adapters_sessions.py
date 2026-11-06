"""Workbench session adapter — the session contract ``/qubit-workbench`` reads.

EVERY CALL IN THIS MODULE GOES THROUGH ONE STORE
---------------------------------------------------------------------------
:class:`voicebridge.qubit_session_store.QubitSessionStore`, the declared
``CHAT_SESSION_AUTHORITY`` (``voicebridge/qubit_compat.py``). It is the same
store ``/qubit-chat``, the terminal, the TUI, the dashboard Sessions page and
the voice bridge read and write. This module therefore opens NO database of its
own: no second writable opener on the 826 MB chat-transcript file, no schema of
its own, no cache. A read that cannot reach the store answers a typed refusal
naming it rather than an empty list that reads as "you have no sessions".

SEND DELEGATES — IT DOES NOT RE-IMPLEMENT
---------------------------------------------------------------------------
:func:`send_turn` calls ``voicebridge.chat_transport.turn`` — the exact
function ``/qubit-chat`` (``voicebridge/qubit_compat.py::h_chat_turn``), the
voice turn path (``voicebridge/voice_edit/service.py::_full_turn_answer`` ->
``CT.turn``) and the CLI all run. That function owns the e-stop check, the
slash/facts/confirmation fast paths, route/model resolution, the broker/policy
gates, the persisted user and assistant rows, the activity projection and the
cancel transport. None of that is duplicated here, and none of it can be
bypassed by this page: a turn sent from the workbench is the same turn, on the
same session, with the same persistence.

Public contract (all pure functions, all JSON-ready dicts, never raise)::

    list_sessions(cfg, limit=50, offset=0, query="", include_archived=False)
    read_transcript(cfg, session_id, limit=500, offset=0)
    read_timeline(cfg, session_id, limit=40)
    send_turn(cfg, session_id, text, model=None, reasoning=None)

Every result carries ``state``: ``"OK"`` on success, or ``"UNAVAILABLE"`` /
``"MISSING"`` / ``"REFUSED"`` / ``"FAILED"`` with a ``code`` and a ``detail``
that names what is missing.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import SESSION_AUTHORITY, UPSTREAM_PIN

#: One page of sessions. The authority caps a page at 1000; the workbench rail
#: shows a bounded window and reports the true total so the UI can say
#: "showing N of M" instead of implying the store only holds N.
PAGE = 50
MAX_PAGE = 200
#: Transcript page size. The authority's own cap is 1000 rows per read.
MAX_MESSAGES = 1000
#: Timeline = recent activity events + recent turns, bounded.
TIMELINE_EVENTS = 40
TIMELINE_TURNS = 20


# --------------------------------------------------------------- plumbing
def _unavailable(code: str, detail: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"state": "UNAVAILABLE", "ok": False,
                           "code": code, "detail": detail,
                           "authority": SESSION_AUTHORITY}
    out.update(extra)
    return out


def _store(cfg: Any) -> Any:
    """Open the ONE session authority.

    Deliberately raises on failure: the callers below turn that into a typed
    refusal naming the authority. A cached or fallback store here would be a
    second reader of the chat transcript, which this module exists to prevent.
    """
    from ..qubit_session_store import QubitSessionStore

    return QubitSessionStore(cfg)


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def _session_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """One rail row: exactly the fields the three-panel list renders."""
    title = str(row.get("title") or row.get("name") or "")
    return {
        "id": str(row.get("id") or ""),
        "title": title or str(row.get("id") or ""),
        "source": str(row.get("source") or ""),
        "model": str(row.get("model") or ""),
        "archived": bool(row.get("archived")),
        "message_count": int(row.get("message_count") or 0),
        "tool_call_count": int(row.get("tool_call_count") or 0),
        "started_at": float(row.get("started_at") or 0),
        "last_active": float(row.get("last_active") or row.get("started_at")
                             or 0),
        "is_active": bool(row.get("is_active")),
        "preview": row.get("preview") or None,
        "snippet": row.get("snippet") or None,
    }


# ------------------------------------------------------------- read: list
def list_sessions(cfg: Any = None, *, limit: int = PAGE, offset: int = 0,
                  query: str = "", include_archived: bool = False
                  ) -> Dict[str, Any]:
    """A page of chat sessions from the canonical store.

    ``total`` is the store's own count under the SAME filters as the page (the
    authority guarantees this); ``shown`` is what this page carries, so
    ``shown < total`` is a real "there is more" signal rather than a cap the
    UI cannot see past.
    """
    page = _int(limit, PAGE, 1, MAX_PAGE)
    start = _int(offset, 0, 0, 10 ** 9)
    try:
        store = _store(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        return _unavailable(
            "WORKBENCH_SESSION_STORE_UNAVAILABLE",
            f"the chat-session authority {SESSION_AUTHORITY!r} could not be "
            f"opened: {type(exc).__name__}: {exc}. This is a backend fault, "
            f"not an empty history.", sessions=[], total=0, shown=0)
    try:
        res = store.list(limit=page, offset=start, query=str(query or ""),
                         include_archived=bool(include_archived))
    except Exception as exc:  # noqa: BLE001
        return _unavailable(
            "WORKBENCH_SESSION_LIST_FAILED",
            f"{SESSION_AUTHORITY}.list raised: {type(exc).__name__}: {exc}",
            sessions=[], total=0, shown=0)
    if not isinstance(res, dict):
        return _unavailable(
            "WORKBENCH_SESSION_CONTRACT_DRIFT",
            "the session authority returned "
            f"{type(res).__name__} where a page dict is contracted",
            sessions=[], total=0, shown=0)
    raw = res.get("sessions") or []
    rows: List[Dict[str, Any]] = [_session_row(r) for r in raw
                                  if isinstance(r, dict)]
    total = int(res.get("total") or len(rows))
    return {
        "state": "OK", "ok": True, "code": None, "detail": "",
        "authority": SESSION_AUTHORITY,
        "sessions": rows,
        "total": total,
        "shown": len(rows),
        "limit": page,
        "offset": start,
        "has_more": (start + len(rows)) < total,
        "query": str(query or ""),
        "include_archived": bool(include_archived),
    }


# ------------------------------------------------------- read: transcript
def read_transcript(cfg: Any = None, session_id: str = "", *,
                    limit: int = 500, offset: int = 0) -> Dict[str, Any]:
    """One session's transcript page, exactly as every other surface reads it."""
    sid = str(session_id or "").strip()[:80]
    if not sid:
        return {"state": "REFUSED", "ok": False,
                "code": "WORKBENCH_SESSION_ID_REQUIRED",
                "detail": "read_transcript needs a session id",
                "authority": SESSION_AUTHORITY}
    page = _int(limit, 500, 1, MAX_MESSAGES)
    start = _int(offset, 0, 0, 10 ** 9)
    try:
        store = _store(cfg)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(
            "WORKBENCH_SESSION_STORE_UNAVAILABLE",
            f"the chat-session authority {SESSION_AUTHORITY!r} could not be "
            f"opened: {type(exc).__name__}: {exc}")
    try:
        res = store.messages(sid, limit=page, offset=start)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("WORKBENCH_SESSION_READ_FAILED",
                            f"{SESSION_AUTHORITY}.messages raised: "
                            f"{type(exc).__name__}: {exc}", session_id=sid)
    if not isinstance(res, dict):
        return _unavailable("WORKBENCH_SESSION_CONTRACT_DRIFT",
                            f"messages() returned {type(res).__name__}",
                            session_id=sid)
    state = str(res.get("state") or "")
    if state == "MISSING":
        # The store's own typed answer: the id is unknown or archived. Passed
        # through unchanged — an unknown id must not look like an empty
        # conversation.
        return {"state": "MISSING", "ok": False,
                "code": str(res.get("code") or "SESSION_NOT_FOUND"),
                "detail": str(res.get("detail") or f"no chat session {sid!r}"),
                "session_id": sid, "authority": SESSION_AUTHORITY,
                "messages": [], "total": 0}
    msgs = [m for m in (res.get("messages") or []) if isinstance(m, dict)]
    total = int(res.get("total") or len(msgs))
    return {
        "state": "OK", "ok": True, "code": None, "detail": "",
        "authority": SESSION_AUTHORITY, "session_id": sid,
        "messages": msgs,
        "total": total,
        "shown": len(msgs),
        "limit": page,
        "offset": start,
        "has_more": (start + len(msgs)) < total,
    }


# --------------------------------------------------------- read: timeline
def read_timeline(cfg: Any = None, session_id: str = "", *,
                  limit: int = TIMELINE_EVENTS) -> Dict[str, Any]:
    """What happened in one session, newest first, in two honest halves.

    ``turns`` comes from the canonical transcript (assistant replies, which is
    what the summary activity event records). ``events`` comes from Qubit's own
    per-session activity projection (``voicebridge.session_activity``), the
    same module the workbench summary reads. Each half carries its own
    ``state`` so a missing activity log does not erase the turns, and an empty
    transcript does not pretend the session had no activity.

    This is NOT upstream's replay/rewind model: Qubit's schema genuinely
    differs and the authority exposes no replay read model, so a replay tab is
    refused by absence rather than fabricated from a foreign schema.
    """
    sid = str(session_id or "").strip()[:80]
    if not sid:
        return {"state": "REFUSED", "ok": False,
                "code": "WORKBENCH_SESSION_ID_REQUIRED",
                "detail": "read_timeline needs a session id",
                "authority": SESSION_AUTHORITY}
    cap = _int(limit, TIMELINE_EVENTS, 1, 200)
    transcript = read_transcript(cfg, sid, limit=MAX_MESSAGES, offset=0)
    turns: List[Dict[str, Any]] = []
    turns_state: Dict[str, Any] = {"state": "OK", "total": 0}
    if transcript.get("state") == "OK":
        for msg in reversed(transcript.get("messages") or []):
            if msg.get("role") != "assistant" or not msg.get("content"):
                continue
            text = str(msg.get("content") or "")
            turns.append({"at": float(msg.get("timestamp") or 0),
                          "role": "assistant",
                          "excerpt": text[:400],
                          "chars": len(text)})
            if len(turns) >= TIMELINE_TURNS:
                break
        # ``limit`` above bounded the transcript page, not the reply count.
        turns_state = {"state": "OK", "total": len(turns)}
    else:
        turns_state = {"state": transcript.get("state"),
                       "code": transcript.get("code"),
                       "detail": transcript.get("detail")}

    events: List[Dict[str, Any]] = []
    events_state: Dict[str, Any]
    try:
        from .. import session_activity as SA

        got = SA.store(cfg).list(sid, limit=cap)
        events = [e for e in (got.get("events") or []) if isinstance(e, dict)]
        events_state = {"state": str(got.get("state") or "OK"),
                        "total": int(got.get("total") or len(events))}
    except Exception as exc:  # noqa: BLE001 - typed, never fatal
        events_state = {"state": "UNAVAILABLE",
                        "code": "WORKBENCH_ACTIVITY_UNAVAILABLE",
                        "detail": f"voicebridge.session_activity is not "
                                  f"usable: {type(exc).__name__}: {exc}"}

    return {
        "state": "OK" if transcript.get("state") == "OK" else
                 str(transcript.get("state") or "UNAVAILABLE"),
        "ok": transcript.get("state") == "OK",
        "code": None if transcript.get("state") == "OK" else
                transcript.get("code"),
        "detail": "" if transcript.get("state") == "OK" else
                  str(transcript.get("detail") or ""),
        "authority": SESSION_AUTHORITY,
        "session_id": sid,
        "turns": turns,
        "turns_state": (turns_state if transcript.get("state") != "OK" else
                        {"state": "OK", "total": len(turns)}),
        "events": events,
        "events_state": events_state,
        "generated_at": time.time(),
        "qubit_counterpart": "voicebridge.session_activity.build_projection",
    }


# ------------------------------------------------------------ write: send
def _turn_entry() -> Any:
    """Resolve ``chat_transport.turn`` — the ONE send path.

    Imports inside the call so this module stays importable in a tree without
    the transport, and so a missing entry point is a typed refusal rather than
    an ImportError at package import.
    """
    from .. import chat_transport as CT

    fn = getattr(CT, "turn", None)
    if not callable(fn):
        raise AttributeError(
            "voicebridge.chat_transport exposes no turn() entry point")
    return fn


def send_turn(cfg: Any = None, session_id: str = "", text: str = "", *,
              model: Optional[str] = None,
              reasoning: Optional[str] = None) -> Dict[str, Any]:
    """Send one turn — through ``chat_transport.turn``, never a private path.

    That function is what ``/qubit-chat`` (``h_chat_turn``), the voice turn
    path (``voice_edit/service.py::_full_turn_answer``) and the CLI call. It
    persists the user and assistant rows to the canonical store itself, so this
    adapter writes NOTHING: no ``append`` call exists in this module. The reply
    shape is the transport's own (``{"ok", "reply", "session_id", "receipt"}``)
    with the workbench's envelope fields added.

    ``session_id`` empty means "the default session", which is the transport's
    own contract (``DEFAULT_SESSION``); a non-existent id is created by the
    transport exactly as it is from ``/qubit-chat``.
    """
    body = str(text or "").strip()
    if not body:
        return {"state": "REFUSED", "ok": False,
                "code": "WORKBENCH_TURN_TEXT_REQUIRED",
                "detail": "send_turn needs non-empty text",
                "authority": SESSION_AUTHORITY}
    try:
        turn = _turn_entry()
    except Exception as exc:  # noqa: BLE001 - a missing transport is typed
        return _unavailable(
            "WORKBENCH_TURN_TRANSPORT_UNAVAILABLE",
            f"the shared turn entry point voicebridge.chat_transport.turn is "
            f"not reachable from this build: {type(exc).__name__}: {exc}. The "
            f"workbench refuses to invent a second send path — /qubit-chat "
            f"and the voice bridge use that one function.",
            session_id=str(session_id or ""))
    try:
        res = turn(cfg, body,
                   session=(str(session_id or "").strip()[:80] or None),
                   model=model,
                   reasoning=reasoning,
                   actor="dashboard:user")
    except Exception as exc:  # noqa: BLE001 - the transport raised, not us
        return {"state": "FAILED", "ok": False,
                "code": "WORKBENCH_TURN_FAILED",
                "detail": f"chat_transport.turn raised: "
                          f"{type(exc).__name__}: {exc}",
                "session_id": str(session_id or ""),
                "authority": SESSION_AUTHORITY}
    out: Dict[str, Any] = dict(res) if isinstance(res, dict) else {
        "ok": False, "code": "WORKBENCH_TURN_CONTRACT_DRIFT",
        "detail": f"chat_transport.turn returned {type(res).__name__}"}
    ok = bool(out.get("ok"))
    out["state"] = "OK" if ok else str(out.get("state") or "FAILED")
    out.setdefault("session_id", str(session_id or ""))
    out["authority"] = SESSION_AUTHORITY
    out["via"] = "voicebridge.chat_transport.turn"
    out["upstream"] = UPSTREAM_PIN
    return out
