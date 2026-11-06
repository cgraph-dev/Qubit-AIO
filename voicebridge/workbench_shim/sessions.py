"""Session routes: hermes-webui's sidebar/detail/send contracts on Qubit's store.

UPSTREAM CONTRACT (read at UPSTREAM_PIN)
---------------------------------------------------------------------------
``GET  /api/sessions``      ``routes.py:3721-3743`` + ``route_session_list_cache.py:47-120``
``GET  /api/session``       ``routes.py:16639+`` (``?messages=0`` skips the transcript)
``POST /api/chat/start``    ``routes.py:31125-29993`` (200 shape) / ``:29674-29681`` (409)
``POST /api/chat/steer``    ``streaming.py:19266-19340`` (``{accepted, fallback, stream_id}``)

WHY EVERY ROW IS RE-KEYED
---------------------------------------------------------------------------
Qubit's store answers ``{id, source, model, title, name, archived, started_at,
ended_at, last_active, is_active, message_count, tool_call_count,
input_tokens, output_tokens, preview, snippet}``
(``qubit_session_store.py:623-641``). The ported sidebar reads the SAME facts
under its own names — ``session_id``, ``created_at``, ``updated_at``,
``last_message_at``, ``is_streaming``, ``is_cli_session`` … (``sessions.js:7685-
7720``, ``session_list_cache._SIDEBAR_SESSION_RESPONSE_FIELDS``). This module is
only that dictionary: no field is synthesized, and a field Qubit has no value
for is either honestly derived (``is_streaming`` from ``is_active``) or omitted
— never invented.

WHY THE COUNTERS ARE ZERO AND NOT FABRICATED
---------------------------------------------------------------------------
Upstream reports ``cli_count``, ``archived_webui_count``, ``cli_session_count``
because it merges several session origins (WebUI sidecars, the CLI's state.db
rows, cron/messaging sessions). Qubit has ONE chat transcript with ONE origin,
so every ``cli_*`` count is a true zero and ``archived_count`` is a real count
of the store's archived rows. ``other_profile_count`` is zero because one cron
and one chat home serve the profile. Reporting a number Qubit did not measure
would make the sidebar claim a history split that does not exist.

THE SEND PATH IS THE SHARED ONE
---------------------------------------------------------------------------
:func:`chat_start` calls ``chat_transport.turn`` and nothing else. That function
owns the e-stop check, route/model resolution, the broker/policy gates and the
persisted user/assistant rows. This module therefore has no ``append`` call and
no second persistence path. ``stream_id`` is ``null``: a Qubit turn is
synchronous, and the ported JS already treats a null id as "no live stream"
(``messages.js:2319``).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from . import (UPSTREAM_PIN, body_of, e_stopped, flag, gap, int_param,
               join_rows, str_param, truthy)

#: Sidebar page caps, matching upstream's own bounds closely enough that a
#: client paging by ``limit`` sees the same sizes.
PAGE = 50
MAX_PAGE = 200
MAX_MESSAGES = 1000


# --------------------------------------------------------------- plumbing
def _store(cfg: Any) -> Any:
    """The ONE session authority. Never a second reader of the transcript."""
    from ..qubit_session_store import QubitSessionStore

    return QubitSessionStore(cfg)


def _unavailable(code: str, detail: str) -> Tuple[int, Dict[str, Any]]:
    return 502, {"ok": False, "state": "UNAVAILABLE", "code": code,
                 "detail": detail, "upstream": UPSTREAM_PIN}


def _sidebar_row(row: Dict[str, Any], *, now: float) -> Dict[str, Any]:
    """One store row → upstream's bounded sidebar row.

    Only keys upstream's reader touches are populated; the rest are present so
    the client's `row.x || default` expressions cannot read `undefined` for a
    field the server is responsible for.
    """
    sid = str(row.get("id") or "")
    started = float(row.get("started_at") or 0)
    last = float(row.get("last_active") or started or 0)
    title = str(row.get("title") or row.get("name") or "") or sid
    live = bool(row.get("is_active"))
    return {
        "session_id": sid,
        "title": title,
        "display_title": title,
        # Qubit has no per-session workspace column for chat transcripts; the
        # workbench file routes are project-scoped instead. ``None`` (not "")
        # tells the client "no session workspace" — upstream uses null too.
        "workspace": None,
        "model": row.get("model") or None,
        "model_provider": None,
        "message_count": int(row.get("message_count") or 0),
        "visible_message_count": int(row.get("message_count") or 0),
        "user_message_count": 0,
        "created_at": started,
        "updated_at": last,
        "last_message_at": last,
        "pinned": False,
        "archived": bool(row.get("archived")),
        "is_streaming": live,
        "cron_running": False,
        "has_pending_user_message": False,
        # One transcript, one origin: nothing is a CLI sidecar in Qubit.
        "is_cli_session": False,
        "sidebar_is_cli_session": False,
        "is_messaging_session": False,
        "session_source": str(row.get("source") or "qubit"),
        "source_label": str(row.get("source") or "qubit"),
        "read_only": False,
        "is_read_only": False,
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "preview": row.get("preview") or None,
        "snippet": row.get("snippet") or None,
        "match_type": "content" if row.get("snippet") else None,
        "match_preview": row.get("snippet") or None,
        "attention": {"pending_approval": False, "pending_clarify": False},
        "sidebar_profile_group": "default",
        "profile": "default",
        "age_seconds": max(0.0, now - last) if last else None,
    }


def _session_detail(row: Dict[str, Any]) -> Dict[str, Any]:
    """A ``store.show`` session object → the detail view's session header."""
    sid = str(row.get("id") or "")
    title = str(row.get("name") or row.get("title") or "") or sid
    return {
        "session_id": sid,
        "title": title,
        "display_title": title,
        "workspace": None,
        "model": row.get("model") or None,
        "model_provider": None,
        "message_count": int(row.get("message_count") or 0),
        "created_at": float(row.get("created_at") or 0),
        "updated_at": float(row.get("updated_at") or 0),
        "last_message_at": float(row.get("updated_at") or 0),
        "archived": bool(row.get("archived")),
        "pinned": False,
        "source": row.get("source") or "qubit",
        "is_cli_session": False,
        "read_only": False,
        "profile": "default",
        "identity": row.get("identity") or "qubit-session",
    }


# ------------------------------------------------------------- handlers
def sessions_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/sessions — upstream's sidebar envelope.

    Query: ``limit``, ``offset``, ``q`` (search), ``include_archived``,
    ``all_profiles``.
    """
    limit = int_param(str_param(q, "limit", default=str(PAGE)), PAGE, 1, MAX_PAGE)
    offset = int_param(str_param(q, "offset", default="0"), 0, 0, 10 ** 9)
    query = str_param(q, "q", "query")
    include_archived = flag(q, "include_archived", "archived")
    try:
        store = _store(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        return _unavailable(
            "SESSION_STORE_UNAVAILABLE",
            f"the chat-session authority could not be opened: "
            f"{type(exc).__name__}: {exc}. This is a backend fault, not an "
            f"empty history.")
    try:
        page = store.list(limit=limit, offset=offset, query=query,
                          include_archived=include_archived)
        # ``total`` under the SAME filters as the page; the archived count is a
        # separate real count so the sidebar's archive badge is measured.
        archived = store.list(limit=1, offset=0, include_archived=True)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("SESSION_LIST_FAILED",
                            f"session authority list raised: "
                            f"{type(exc).__name__}: {exc}")
    if not isinstance(page, dict):
        return _unavailable("SESSION_CONTRACT_DRIFT",
                            f"the session authority returned "
                            f"{type(page).__name__} where a page dict is "
                            f"contracted")
    now = time.time()
    rows = [_sidebar_row(r, now=now) for r in join_rows(page.get("sessions"))]
    archived_rows = join_rows(archived.get("sessions") if isinstance(archived, dict)
                              else [])
    archived_count = sum(1 for r in archived_rows if r.get("archived"))
    total = int(page.get("total") or len(rows))
    return 200, {
        "sessions": rows,
        # Upstream keeps hidden archived ancestors here for client-side nesting.
        # Qubit's tree has no hidden parents, so the list is honestly empty.
        "sidebar_reference_sessions": [],
        "cli_count": 0,
        "archived_count": archived_count,
        "archived_webui_count": archived_count,
        "archived_cli_count": 0,
        "include_archived": bool(include_archived),
        "all_profiles": flag(q, "all_profiles"),
        "active_profile": "default",
        "other_profile_count": 0,
        "server_time": now,
        "server_tz": time.strftime("%z"),
        # Additive (upstream's list is unpaged; the ported client pages with
        # these and they are the store's own numbers).
        "total": total,
        "shown": len(rows),
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(rows)) < total,
        "upstream": UPSTREAM_PIN,
    }


def session_detail(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/sessions/<id> — one session, transcript optional.

    ``?messages=0`` returns the header only, exactly as upstream's fast session
    switch does; ``?msg_limit=N`` returns the LAST N rows (upstream's tail
    window) while reporting the true total.
    """
    sid = str_param(p, "id") or str_param(q, "session_id")
    if not sid:
        return 400, {"error": "session_id is required"}
    load_messages = str_param(q, "messages", default="1") != "0"
    try:
        store = _store(cfg)
        shown = store.show(sid)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("SESSION_READ_FAILED",
                            f"session authority show raised: "
                            f"{type(exc).__name__}: {exc}")
    if str(shown.get("state")) != "OK":
        # Upstream answers 404 here and the ported JS self-heals by clearing the
        # stale session id (messages.js:2232) — so 404 is the contract, not 409.
        return 404, {"error": "Session not found", "session_id": sid,
                     "code": str(shown.get("code") or "SESSION_NOT_FOUND")}
    header = _session_detail(join_rows([shown.get("session")])[0]
                             if shown.get("session") else {})
    out: Dict[str, Any] = dict(header)
    if not load_messages:
        out["messages"] = []
        out["messages_truncated"] = False
        out["upstream"] = UPSTREAM_PIN
        return 200, out
    msg_limit = str_param(q, "msg_limit")
    total = int(shown.get("message_count") or 0)
    rows = join_rows(shown.get("messages"))
    truncated = False
    if msg_limit and msg_limit.lower() != "all":
        want = int_param(msg_limit, total or 1, 1, MAX_MESSAGES * 20)
        if want < len(rows):
            rows = rows[-want:]
            truncated = True
    out["messages"] = rows
    out["message_count"] = total
    out["messages_truncated"] = truncated
    out["upstream"] = UPSTREAM_PIN
    return 200, out


def _turn_status(code: str) -> int:
    return {
        "EMERGENCY_STOP": 403,
        "AGENT_DISABLED": 403,
        "MALFORMED": 400,
        "MODEL_NOT_ALLOWED": 400,
        "REASONING_INVALID": 400,
        "CANCELLED": 409,
    }.get(str(code), 502)


def chat_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/chat/start — send one turn through the shared transport.

    Upstream starts a *stream* and answers ``{stream_id, session_id,
    pending_started_at, turn_id, title, effective_model?}``. Qubit answers the
    same envelope for a SYNCHRONOUS turn: the reply is already persisted when
    the call returns, so ``stream_id``/``turn_id``/``pending_started_at`` are
    null and the reply rides along in ``reply`` (additive — upstream's reader
    ignores unknown keys, and the ported client reloads the transcript after a
    send anyway).
    """
    body = body_of(b)
    session_id = str(body.get("session_id") or "").strip()
    message = str(body.get("message") or body.get("text") or "")
    if not session_id:
        return 400, {"error": "session_id required"}
    if not message.strip():
        return 400, {"error": "message is required"}
    if e_stopped(cfg):
        return 403, {"ok": False, "error": "emergency stop latched; agent "
                                           "turn refused",
                     "code": "EMERGENCY_STOP"}
    try:
        from .. import chat_transport as CT
    except Exception as exc:  # noqa: BLE001 - the one send path is required
        return 503, {"ok": False, "code": "TURN_TRANSPORT_UNAVAILABLE",
                     "error": f"chat_transport is not reachable: "
                              f"{type(exc).__name__}: {exc}",
                     "detail": "Qubit refuses to invent a second send path."}
    reasoning = body.get("reasoning")
    try:
        res = CT.turn(cfg, message, session=session_id[:80],
                      model=body.get("model") or None,
                      reasoning=reasoning if isinstance(reasoning, str) else None,
                      actor="dashboard:user")
    except Exception as exc:  # noqa: BLE001 - the transport raised, not us
        return 502, {"ok": False, "error": f"turn failed: "
                                           f"{type(exc).__name__}: {exc}",
                     "code": "TURN_FAILED"}
    if not isinstance(res, dict):
        return 502, {"ok": False, "error": "turn contract drift",
                     "code": "TURN_CONTRACT_DRIFT"}
    if not res.get("ok"):
        code = str(res.get("code") or "TURN_FAILED")
        return _turn_status(code), {
            "ok": False,
            "error": str(res.get("detail") or res.get("error") or code),
            "code": code,
            "session_id": str(res.get("session_id") or session_id),
        }
    sid = str(res.get("session_id") or session_id)
    title: Optional[str] = None
    model: Optional[str] = None
    try:
        shown = _store(cfg).show(sid)
        if str(shown.get("state")) == "OK" and shown.get("session"):
            row = shown["session"]
            title = str(row.get("name") or "") or None
            model = str(row.get("model") or "") or None
    except Exception:  # noqa: BLE001 - header facts are optional on a send
        title, model = None, None
    out: Dict[str, Any] = {
        "ok": True,
        # Synchronous turn: no Qubit stream to point at.
        "stream_id": None,
        "session_id": sid,
        "pending_started_at": None,
        "turn_id": None,
        "title": title,
        "reply": res.get("reply"),
        "message_count": res.get("message_count"),
        "elapsed_ms": res.get("elapsed_ms"),
        "via": "voicebridge.chat_transport.turn",
        "upstream": UPSTREAM_PIN,
    }
    requested_model = str(body.get("model") or "").strip()
    effective = model or requested_model or None
    if effective:
        out["effective_model"] = effective
    provider = str(body.get("model_provider") or "").strip()
    if provider:
        out["effective_model_provider"] = provider
    return 200, out


def chat_steer(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/chat/steer — GAP: no in-flight run to steer."""
    body = body_of(b)
    if not str(body.get("session_id") or "").strip():
        return 400, {"error": "session_id required"}
    if not str(body.get("text") or "").strip():
        return 400, {"error": "text required"}
    return gap("MID_TURN_STEER",
               detail=("A turn already sent through /api/wb/chat/start has "
                       "completed by the time it answers; send the guidance as "
                       "the next turn instead."),
               qubit_counterpart="POST /api/wb/chat/start")


HANDLERS = {
    "h_wb_sessions_list": sessions_list,
    "h_wb_session_detail": session_detail,
    "h_wb_chat_start": chat_start,
    "h_wb_chat_steer": chat_steer,
}


def routes() -> list:
    """Rows for the compat table: (method, pattern, handler-name)."""
    return [
        ("GET", r"/api/wb/sessions", "h_wb_sessions_list"),
        # The bare-id row sits LAST so it cannot shadow the specific sub-paths
        # (there is only one today, but the table is ordered).
        ("GET", r"/api/wb/sessions/(?P<id>[^/]+)", "h_wb_session_detail"),
        ("POST", r"/api/wb/chat/start", "h_wb_chat_start"),
        ("POST", r"/api/wb/chat/steer", "h_wb_chat_steer"),
    ]
