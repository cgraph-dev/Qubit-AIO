"""The ported client's own call set — session detail/verbs, cards, small polls.

WHY THIS MODULE EXISTS SEPARATELY FROM ``workbench_ui_routes``
---------------------------------------------------------------------------
That module owns asset serving; this one owns the *dispatch targets* the ported
page actually calls. Both are Qubit-owned and registered from the same gateway,
but keeping the endpoints here means the gateway stays one routing table and
these handlers stay one readable file.

WHAT IT ADDS OVER THE EARLIER TABLE
---------------------------------------------------------------------------
A live probe of the served page (Chrome, real user gesture, network log) showed
which paths the client calls that the original table never listed. Most of them
answered ``WB_NOT_IN_PORT_SET`` while a delegation already existed:

* ``POST /api/chat/start`` — the whole send path. It was 501; the client rendered
  its own error bubble and no turn ever ran.
* ``GET  /api/session?session_id=…`` — the transcript read. 501, so opening any
  session showed "Loading conversation..." forever.
* ``POST /api/session/update``, ``/session/yolo``, ``/session/stream`` — polled by
  the client while a turn runs.
* ``GET  /api/sessions/events`` — the sidebar's hot channel. 501, so the client
  retried it in a loop.
* ``GET  /api/approval/pending`` — 503 because the journal constructor was given
  ``None`` (the listener has no ``wb_cfg``); the approval card could never appear.

THE THREE HONEST ANSWERS
---------------------------------------------------------------------------
WORKS    — a real Qubit authority answers (``QubitSessionStore``, the shared
           approval journal, ``chat_transport``, ``cron_bridge``).
GAP      — the concept does not exist in Qubit; a typed ``501`` names it. It is
           never answered with an empty success.
REFUSED  — the concept exists but the write lane is brokered (file writes, cron
           writes without confirmation); a typed ``403``/``409`` says which.

NO BYPASS
---------------------------------------------------------------------------
Every write here goes through the same gate a CLI or voice caller passes: the
shared confirm contract (``qubit_compat._compat_confirm``) for cron mutations,
``QubitSessionStore``'s own verbs for session flags. ``confirm=true`` is accepted
only because the ported client has already run its own confirm dialog — the same
two-key rule the dashboard uses.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..workbench_shim import UPSTREAM_PIN, str_param

#: Sidebar row fields the ported rail reads that a Qubit store row must supply
#: with a real value (not ``undefined``); everything else is left absent so the
#: client falls back to its own defaults.
_MESSAGE_CONTENT_CAP = 200_000


# ----------------------------------------------------------------- helpers
def _refuse(code: str, detail: str, *, endpoint: str = "",
            counterpart: str = "", status: int = 0) -> Tuple[int, Dict[str, Any]]:
    table = {
        "WB_BAD_INPUT": 400,
        "WB_READ_ONLY_SURFACE": 403,
        "WB_BROKERED_WRITE": 403,
        "WB_CONFIRM_REQUIRED": 409,
        "WB_NOT_FOUND": 404,
        "WB_CAPABILITY_UNBOUND": 501,
        "WB_NOT_IN_PORT_SET": 501,
        "WB_BACKEND_UNAVAILABLE": 503,
    }
    return (status or table.get(code, 502)), {
        "ok": False,
        "state": "UNAVAILABLE",
        "code": code,
        "endpoint": endpoint,
        "detail": detail,
        "qubit_counterpart": counterpart or "Qubit Dashboard (/)",
        "owner": "qubit-policy",
        "rollback": "read-only; nothing to roll back",
        "upstream": UPSTREAM_PIN,
    }


def _store(cfg: Any) -> Any:
    from ..qubit_session_store import QubitSessionStore

    return QubitSessionStore(cfg)


def _body(b: Any) -> Dict[str, Any]:
    return b if isinstance(b, dict) else {}


def _msg_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """One stored message in upstream's renderer's shape.

    ``role``/``content``/``timestamp`` are what the transcript paints; ``meta``
    carries the tool/reasoning facts upstream's own rows carry so the renderer
    can draw a tool card instead of a blank bubble. ``content`` is coerced to a
    string because the renderer calls ``.trim()``/markdown parsing on it and a
    ``None`` from a tool row would throw mid-render.
    """
    content = row.get("content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    out: Dict[str, Any] = {
        "role": str(row.get("role") or "assistant"),
        "content": content[:_MESSAGE_CONTENT_CAP],
        "timestamp": float(row.get("timestamp") or 0) or time.time(),
    }
    meta = row.get("meta")
    if isinstance(meta, dict) and meta:
        out["meta"] = meta
        for key in ("tool_name", "tool_call_id", "tool_calls", "reasoning",
                    "finish_reason", "token_count"):
            if meta.get(key) not in (None, "", [], {}):
                out[key] = meta[key]
    for key in ("tool_name", "tool_call_id", "tool_calls", "reasoning"):
        if row.get(key) not in (None, "", [], {}) and key not in out:
            out[key] = row[key]
    return out


def _session_header(sid: str, rec: Dict[str, Any], *, message_count: int,
                    live: Dict[str, Any]) -> Dict[str, Any]:
    """Upstream's session object. Every field the client reads is a real value."""
    title = str(rec.get("name") or rec.get("title") or "") or sid
    created = float(rec.get("created_at") or 0) or time.time()
    updated = float(rec.get("updated_at") or created)
    return {
        "session_id": sid,
        "id": sid,
        "title": title,
        "display_title": title,
        # Qubit has no per-session workspace column; the workbench file routes are
        # project-scoped. ``None`` tells the client "no session workspace" — the
        # same meaning upstream gives null.
        "workspace": None,
        "model": rec.get("model") or None,
        "model_provider": None,
        "message_count": message_count,
        "created_at": created,
        "updated_at": updated,
        "last_message_at": updated,
        "is_streaming": bool(live.get("is_streaming")),
        "active_stream_id": live.get("active_stream_id"),
        "archived": bool(rec.get("archived")),
        "pinned": False,
        "read_only": False,
        "is_read_only": False,
        "source": str(rec.get("source") or "qubit"),
        "session_source": str(rec.get("source") or "qubit"),
        "profile": "default",
        "sidebar_profile_group": "default",
        "is_cli_session": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "tool_calls": [],
        "last_usage": {},
        "_messages_truncated": False,
        "_messages_offset": 0,
        "_msg_limit_max": 500,
    }


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------- chat routes
def chat_start(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
               p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/chat/start`` — the real start (see ``workbench_ui.chat_stream``)."""
    from . import chat_stream as CS

    return CS.chat_start(cfg, dict(q or {}), _body(b), dict(p or {}))


def chat_steer(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
               p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/chat/steer`` — GAP: one synchronous turn path, no in-flight run."""
    body = _body(b)
    live = None
    try:
        from . import chat_stream as CS

        live = CS.live_run_for_session(str(body.get("session_id") or ""))
    except Exception:  # noqa: BLE001 - absence just means "no run to steer"
        live = None
    if live is None:
        return 409, {
            "ok": False,
            "accepted": False,
            "fallback": "queue",
            "code": "NO_ACTIVE_STREAM",
            "error": "no active stream to steer",
            "detail": ("Qubit's turn path has no mid-turn steering channel; the "
                       "text can be sent as the next turn."),
        }
    return _refuse(
        "MID_TURN_STEER",
        "mid-turn steering is not ported: chat_transport.turn is a synchronous "
        "turn with no in-flight agent object to stash guidance into. Send the "
        "guidance as the next turn, or stop the run with /api/chat/stop.",
        endpoint="chat/steer", status=501, counterpart="POST /api/chat/stop")


def chat_stop(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/chat/stop`` — cancel the session's live turn."""
    body = _body(b)
    sid = str(body.get("session_id") or str_param(q, "session_id")).strip()
    if not sid:
        return _refuse("WB_BAD_INPUT", "session_id required", endpoint="chat/stop")
    from . import chat_stream as CS

    if CS.cancel_stream(sid):
        return 200, {"ok": True, "cancelled": True, "session_id": sid,
                     "via": "voicebridge.chat_transport.cancel"}
    return 200, {"ok": True, "cancelled": False, "session_id": sid,
                 "detail": "no live turn for this session"}


def chat_stream_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                       p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/chat/stream/status`` — the client's reconnect probe."""
    from . import chat_stream as CS

    return CS.chat_stream_status(cfg, dict(q or {}), _body(b), dict(p or {}))


# ------------------------------------------------------------ session routes
def session_detail(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/session?session_id=…&messages=0|1&msg_limit=N&msg_before=N``.

    Upstream's most-called endpoint: header first (fast switch), transcript on
    request. ``msg_limit`` windows the tail; ``msg_before`` pages backwards from
    an absolute index, which is what the client's "load older" path sends. A
    missing session is ``404`` because the client self-heals on exactly that.
    """
    sid = str(str_param(q, "session_id") or _body(b).get("session_id") or "").strip()
    if not sid:
        return _refuse("WB_BAD_INPUT", "session_id required", endpoint="session")
    want_messages = str(str_param(q, "messages", default="1")) != "0"
    try:
        store = _store(cfg)
        shown = store.show(sid)
    except Exception as exc:  # noqa: BLE001 - a store fault is typed, not a 500
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the session authority raised {type(exc).__name__}: {exc}",
                       endpoint="session")
    if str(shown.get("state")) != "OK":
        return 404, {"ok": False, "error": "Session not found",
                     "code": "SESSION_NOT_FOUND", "session_id": sid}
    from . import chat_stream as CS

    live = CS.session_live_info(sid)
    rec = shown.get("session") or {}
    rows = [r for r in (shown.get("messages") or []) if isinstance(r, dict)]
    total = len(rows)
    header = _session_header(sid, rec, message_count=total, live=live)
    if not want_messages:
        header["messages"] = []
        return 200, {"ok": True, "session": header,
                     "messages": [], "entries": [], "upstream": UPSTREAM_PIN}
    limit_raw = str_param(q, "msg_limit", default="").strip().lower()
    if limit_raw in ("", "all"):
        window = rows
        truncated = False
        offset = 0
    else:
        limit = max(1, min(_int_or(limit_raw, 30), 500))
        before = str_param(q, "msg_before", default="").strip()
        if before.isdigit():
            end = max(0, min(int(before), total))
            start = max(0, end - limit)
            window = rows[start:end]
            offset = start
            truncated = start > 0
        else:
            window = rows[-limit:] if total > limit else rows
            offset = total - len(window)
            truncated = offset > 0
    header["messages"] = [_msg_row(r) for r in window]
    header["_messages_truncated"] = truncated
    header["_messages_offset"] = offset
    header["message_count"] = total
    return 200, {"ok": True, "session": header, "messages": header["messages"],
                 "entries": header["messages"], "upstream": UPSTREAM_PIN}


def sessions_search(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/sessions/search`` — the rail's search box."""
    needle = str(str_param(q, "q", "query", "search")).strip()
    try:
        from .. import qubit_compat as HC

        code, out = HC.dispatch(cfg, "GET", "/api/sessions/search",
                                query={"q": needle}, body={})
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the session search raised {type(exc).__name__}: {exc}",
                       endpoint="sessions/search")
    if code != 200:
        return code, out
    rows = out if isinstance(out, list) else (out.get("sessions") or out.get("items")
                                              or out.get("results") or [])
    return 200, {"ok": True, "sessions": rows, "items": rows,
                 "count": len(rows), "source": "/api/sessions/search"}


#: Session verbs the client posts, mapped onto the ONE store verb that owns the
#: effect. A verb with no Qubit authority is listed in ``_VERB_GAPS`` by name.
def session_verb(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/session/<verb>`` — the client's session mutations."""
    body = _body(b)
    verb = str((p or {}).get("action") or "").strip().lower() or "unknown"
    sid = str(body.get("session_id") or body.get("sid")
              or str_param(q, "session_id")).strip()
    # Verbs that create or need no existing session first.
    if verb == "new":
        try:
            store = _store(cfg)
            created = store.create(name=str(body.get("title") or body.get("name")
                                            or ""), identity="dashboard:user")
        except Exception as exc:  # noqa: BLE001
            return _refuse("WB_BACKEND_UNAVAILABLE",
                           f"session create raised {type(exc).__name__}: {exc}",
                           endpoint="session/new")
        new_sid = str((created or {}).get("session_id")
                      or ((created or {}).get("session") or {}).get("id") or "")
        if not new_sid:
            return _refuse("WB_BACKEND_UNAVAILABLE",
                           "the session store did not return an id",
                           endpoint="session/new")
        from . import chat_stream as CS

        header = _session_header(new_sid, {"name": str(body.get("title") or "")},
                                 message_count=0, live=CS.session_live_info(new_sid))
        return 200, {"ok": True, "session_id": new_sid, "session": header}
    if not sid:
        return _refuse("WB_BAD_INPUT", f"session_id required for /session/{verb}",
                       endpoint="session/" + verb)
    if verb == "rename":
        title = str(body.get("title") or body.get("name") or "").strip()
        if not title:
            return _refuse("WB_BAD_INPUT", "rename needs a title",
                           endpoint="session/rename")
        try:
            out = _store(cfg).rename(sid, title)
        except Exception as exc:  # noqa: BLE001
            return _refuse("WB_BACKEND_UNAVAILABLE",
                           f"rename raised {type(exc).__name__}: {exc}",
                           endpoint="session/rename")
        return _verb_result(out, sid, "rename")
    if verb == "delete":
        # The client already ran its confirm dialog before posting; the store's
        # delete is a REVERSIBLE archive and requires confirm=True explicitly.
        try:
            out = _store(cfg).delete(sid, confirm=True)
        except Exception as exc:  # noqa: BLE001
            return _refuse("WB_BACKEND_UNAVAILABLE",
                           f"delete raised {type(exc).__name__}: {exc}",
                           endpoint="session/delete")
        return _verb_result(out, sid, "delete")
    if verb in ("archive", "pin", "update", "draft"):
        return _session_flags(cfg, sid, verb, body)
    if verb == "move":
        # Projects: Qubit has no per-session project column; refusing keeps the
        # client from claiming a move that did nothing.
        return _refuse("SESSION_PROJECTS", (
            "moving a session into a project is not ported: Qubit's chat "
            "transcripts have no project column. Nothing was changed."),
            endpoint="session/move", status=501,
            counterpart="Qubit Dashboard /workbench (project-scoped files)")
    if verb in ("duplicate", "handoff-summary", "conversation-rounds",
                "lineage/report", "import_cli", "import", "compress",
                "worktree/status", "worktree/remove", "worktree/create",
                "title/regenerate", "continue", "fork"):
        return _refuse("SESSION_EXTRA_VERB", (
            f"session/{verb} has no Qubit authority: the store owns create/rename/"
            f"delete/archive/pin and the transcript itself. Nothing was changed."),
            endpoint="session/" + verb, status=501)
    if verb == "title/regenerate":
        return _refuse("SESSION_TITLE_AUTOGEN", (
            "automatic title regeneration is not ported: titles are set by the "
            "operator (rename) or by the turn itself. Nothing was changed."),
            endpoint="session/title/regenerate", status=501)
    # Unqualified "update" payloads carry fields the store does not own.
    return _refuse("WB_CAPABILITY_UNBOUND",
                   f"session/{verb} has no Qubit authority; nothing was changed.",
                   endpoint="session/" + verb)


def _verb_result(out: Any, sid: str, verb: str) -> Tuple[int, Any]:
    state = str((out or {}).get("state") or "")
    if state == "MISSING":
        return 404, {"ok": False, "error": "Session not found",
                     "code": "SESSION_NOT_FOUND", "session_id": sid}
    if state != "OK":
        return 502, {"ok": False, "code": str((out or {}).get("code")
                                              or "SESSION_WRITE_FAILED"),
                     "error": str((out or {}).get("detail") or verb),
                     "session_id": sid}
    return 200, {"ok": True, "session_id": sid, "verb": verb}


def _session_flags(cfg: Any, sid: str, verb: str,
                   body: Dict[str, Any]) -> Tuple[int, Any]:
    """``update`` / ``archive`` / ``pin`` / ``draft`` on the store's own metadata.

    ``update`` is what the client posts after the model dropdown changes and after
    a toolset pick. The store's ``meta_patch`` is the ONE writer for non-transcript
    session flags, so accepted keys go there; a key the store does not own is
    named in the response instead of being silently dropped.
    """
    try:
        store = _store(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the session authority raised {type(exc).__name__}: {exc}",
                       endpoint="session/" + verb)
    owned = {"archived", "title", "model", "pinned", "composer_draft", "draft",
             "toolsets", "reasoning"}
    unknown = [k for k in body if k not in owned and k not in ("session_id", "sid")]
    changed: Dict[str, Any] = {}
    if "archived" in body:
        store.archive(sid, bool(body["archived"]))
        changed["archived"] = bool(body["archived"])
    if isinstance(body.get("title"), str) and body["title"].strip():
        store.rename(sid, str(body["title"]).strip())
        changed["title"] = str(body["title"]).strip()
    if "model" in body:
        try:
            store.set_model(sid, str(body["model"] or ""))
            changed["model"] = body["model"]
        except Exception:  # noqa: BLE001 - set_model is optional on the store
            unknown.append("model")
    patch: Dict[str, Any] = {}
    for key in ("pinned", "reasoning", "toolsets"):
        if key in body:
            patch[key] = body[key]
    draft = body.get("composer_draft", body.get("draft"))
    if draft is not None:
        patch["composer_draft"] = draft
    if patch:
        try:
            store.meta_patch(sid, patch)
            changed.update(patch)
        except Exception as exc:  # noqa: BLE001
            return _refuse("WB_BROKERED_WRITE",
                           f"the store's meta_patch refused: "
                           f"{type(exc).__name__}: {exc}",
                           endpoint="session/" + verb)
    return 200, {"ok": True, "session_id": sid, "changed": sorted(changed),
                 "ignored_fields": unknown,
                 "detail": ("fields the session store does not own are listed in "
                            "ignored_fields; nothing else was written")
                 if unknown else ""}


def session_stream(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/session/stream?session_id=…`` — the client's transcript poll.

    Not an SSE route in this port: the client polls it every few seconds while a
    turn runs (``known_count`` is its cursor). It answers the same JSON shape as
    ``/api/session`` when the message count moved past the cursor, and a cheap
    ``{changed: false}`` otherwise — so a poll is not a full transcript read.
    """
    sid = str(str_param(q, "session_id") or _body(b).get("session_id") or "").strip()
    if not sid:
        return _refuse("WB_BAD_INPUT", "session_id required",
                       endpoint="session/stream")
    try:
        shown = _store(cfg).show(sid)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the session authority raised {type(exc).__name__}: {exc}",
                       endpoint="session/stream")
    if str(shown.get("state")) != "OK":
        return 404, {"ok": False, "error": "Session not found",
                     "code": "SESSION_NOT_FOUND", "session_id": sid}
    from . import chat_stream as CS

    live = CS.session_live_info(sid)
    rec = shown.get("session") or {}
    rows = [r for r in (shown.get("messages") or []) if isinstance(r, dict)]
    known = _int_or(str_param(q, "known_count", default="0"), 0)
    changed = len(rows) != known or bool(live.get("is_streaming"))
    header = _session_header(sid, rec, message_count=len(rows), live=live)
    if not changed:
        return 200, {"ok": True, "changed": False, "session": header,
                     "messages": [], "message_count": len(rows)}
    header["messages"] = [_msg_row(r) for r in rows[-60:]]
    return 200, {"ok": True, "changed": True, "session": header,
                 "messages": header["messages"], "message_count": len(rows)}


def session_flag(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET/POST /api/session/yolo`` — GAP: no per-session approval bypass."""
    return _refuse("SESSION_YOLO", (
        "a per-session yolo/auto-approve switch does not exist in Qubit: "
        "approvals are settled on one durable journal, and bypassing them is a "
        "policy decision made by the operator, not a session flag."),
        endpoint="session/yolo", status=501,
        counterpart="Qubit approvals (journal + operator policy)")


# ------------------------------------------------------------ sidebar events
def sessions_events(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/sessions/events`` — served as SSE by the listener.

    The handler returns the FIRST frame; the listener's stream branch (see
    ``workbench_ui_routes.stream_kind``) then keeps the connection open and
    re-sends a real snapshot whenever the session list changes. Answering JSON
    here would make the client's ``EventSource`` reconnect forever (it did:
    the live network log showed a 501 loop).
    """
    snap = _sessions_snapshot(cfg)
    if snap.get("error"):
        return 503, {"ok": False, "code": "WB_BACKEND_UNAVAILABLE",
                     "detail": snap["error"]}
    return 200, snap


def _sessions_snapshot(cfg: Any) -> Dict[str, Any]:
    try:
        from ..workbench import adapters_sessions

        out = adapters_sessions.list_sessions(cfg, limit=200, offset=0, query="")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the session list raised {type(exc).__name__}: {exc}"}
    if out.get("ok") is not True:
        return {"error": str(out.get("detail") or out.get("code")
                             or "session list unavailable")}
    rows = out.get("sessions") or []
    return {"sessions": rows, "total": out.get("total") or len(rows),
            "count": len(rows), "at": time.time(), "profile": "default"}


def sessions_events_frames(cfg: Any) -> Iterable[str]:
    """SSE frames for the sidebar channel: a snapshot now, then on change.

    The change signal is the session list's own ``(count, newest id, newest
    activity)`` triple — the same facts the sidebar paints. A poll of a bounded
    list is cheap (one store call), and a keep-alive comment keeps proxies from
    closing the socket.
    """
    last_key = None
    while True:
        snap = _sessions_snapshot(cfg)
        if snap.get("error"):
            yield _frame("error", {"code": "WB_BACKEND_UNAVAILABLE",
                                   "detail": snap["error"]})
            return
        rows = snap["sessions"]
        key = (len(rows),
               (rows[0] or {}).get("id") if rows else None,
               (rows[0] or {}).get("last_active") if rows else None,
               sum(1 for r in rows if isinstance(r, dict) and r.get("is_active")))
        if key != last_key:
            last_key = key
            yield _frame("sessions_changed", {"type": "sessions_changed",
                                              "sessions": rows,
                                              "total": snap["total"],
                                              "profile": "default",
                                              "at": snap["at"]})
        else:
            yield ": keepalive\n\n"
        time.sleep(2.0)


def _frame(name: str, payload: Dict[str, Any]) -> str:
    return (f"event: {name}\n"
            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")


# ------------------------------------------------------------------ small polls
def profile_active(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    try:
        from .. import qubit_compat as HC

        code, out = HC.dispatch(cfg, "GET", "/api/profiles", query={}, body={})
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the profile roster raised {type(exc).__name__}: {exc}",
                       endpoint="profile/active", status=503)
    names: List[str] = []
    if isinstance(out, list):
        names = [str(r.get("name") or r.get("id")) for r in out if isinstance(r, dict)]
    elif isinstance(out, dict):
        names = [str(r.get("name") or r.get("id"))
                 for r in (out.get("profiles") or out.get("items") or [])
                 if isinstance(r, dict)]
    active = "default"
    return 200, {
        "ok": True,
        "profile": {"name": active, "id": active, "active": True},
        "active_profile": active,
        "available_profiles": names,
        "profile_available": True,
        "profiles_available": names,
    }


def health_agent(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/health/agent`` — live facts from the turn path's own authority."""
    model = None
    agent_on = None
    try:
        from .. import chat_transport as CT

        model = CT.configured_model(cfg)
        agent_on = bool(CT._agent_enabled(cfg))  # one authority, read-only here
    except Exception:  # noqa: BLE001 - absent transport is "unknown", not "ok"
        pass
    try:
        from .. import qubit_compat as HC

        stopped = bool(HC._estop(cfg))
    except Exception:  # noqa: BLE001
        stopped = True
    return 200, {
        "ok": True,
        "agent": {"enabled": agent_on, "model": model, "estop": stopped},
        "status": "stopped" if stopped else ("ready" if agent_on else "disabled"),
        "model": model,
        "estop": stopped,
    }


def dashboard_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/dashboard/status`` — the page's own liveness banner."""
    out: Dict[str, Any] = {"ok": True, "dashboard": {"available": True},
                           "version": None, "upstream": UPSTREAM_PIN}
    try:
        out["version"] = _version()
    except Exception:  # noqa: BLE001
        pass
    return 200, out


def dashboard_config(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/dashboard/config`` — only facts Qubit's config actually has."""
    return 200, {
        "ok": True,
        "config": {
            "max_upload_bytes": 25 * 1024 * 1024,
            "upstream": UPSTREAM_PIN,
        },
        "read_only": True,
        "detail": ("Qubit's settings are edited on its dashboard; this route "
                   "publishes the few limits the workbench itself enforces."),
    }


def onboarding_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                      p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/onboarding/status`` — Qubit has no onboarding wizard.

    It answers "complete" ONLY when a turn is actually possible: the model is
    resolvable and the agent is enabled. Otherwise the client shows its
    onboarding nudge, which is the true state.
    """
    ready, why = _agent_ready(cfg)
    return 200, {"ok": True, "complete": ready, "needs_onboarding": not ready,
                 "detail": why, "steps": [], "upstream": UPSTREAM_PIN}


def client_events_log(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                      p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/client-events/log`` — accepted and NOT retained.

    Upstream writes a client-error log the operator can read. Qubit keeps one
    audit trail (``chat_transport._audit``) and this port does not add a second
    sink, so the request is acknowledged and the non-retention is stated.
    """
    return 200, {"ok": True, "logged": False,
                 "detail": ("client events are not persisted by this port: Qubit "
                            "keeps one audit trail (chat_transport._audit) and the "
                            "workbench adds no second sink.")}


def transcribe_capability(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                          p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/transcribe/capability`` — voice input availability, truthfully."""
    engines: List[str] = ["browser"]
    try:
        from .. import qubit_compat as HC

        code, out = HC.dispatch(cfg, "GET", "/api/audio/settings", query={},
                                body={})
        if code == 200 and isinstance(out, dict):
            stt = out.get("stt") if isinstance(out.get("stt"), dict) else {}
            if stt.get("available") or stt.get("engine"):
                engines.insert(0, "server")
    except Exception:  # noqa: BLE001 - no audio authority: browser-only is true
        pass
    return 200, {"ok": True, "available": True, "engines": engines,
                 "server_stt": "server" in engines,
                 "detail": ("voice input uses the browser's speech recognition "
                            "unless a local STT engine is configured")}


def updates_check(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                  p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/updates/check`` — Qubit has no upstream release channel."""
    return 200, {"ok": True, "update_available": False, "current": _version(),
                 "detail": ("Qubit is self-owned and has no upstream release "
                            "channel to check; nothing to update from here.")}


def workspaces(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
               p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/workspaces`` — the approved project roots, from Qubit's own list."""
    try:
        from .. import qubit_compat as HC

        code, out = HC.dispatch(cfg, "GET", "/api/projects", query={}, body={})
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the project list raised {type(exc).__name__}: {exc}",
                       endpoint="workspaces", status=503)
    roots = []
    if isinstance(out, dict):
        roots = out.get("projects") or out.get("items") or []
    elif isinstance(out, list):
        roots = out
    names = []
    for row in roots:
        if isinstance(row, dict):
            names.append({"path": row.get("path") or row.get("root"),
                          "name": row.get("name") or row.get("path")})
    return 200, {"ok": True, "workspaces": names, "count": len(names)}


def _version() -> str:
    """Qubit's package version, read as an attribute (never imported as a name)."""
    try:
        import voicebridge as _vb

        return str(getattr(_vb, "__version__", "") or "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _agent_ready(cfg: Any) -> Tuple[bool, str]:
    """``(a turn is possible, why not)`` from the turn path's own preflight."""
    try:
        from .. import chat_transport as CT

        if not CT._agent_enabled(cfg):
            return False, "agent turns are off (agent.enabled=false)"
        model = CT.configured_model(cfg)
        if not model:
            return False, "no chat model is configured"
        return True, f"turns run on {model}"
    except Exception as exc:  # noqa: BLE001 - an unreadable authority is "not ready"
        return False, f"the turn authority is unreadable: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------- cron verbs
def crons_recent(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/crons/recent?since=`` — the page's completion poll."""
    try:
        from ..workbench_shim import crons as CR

        return CR.h_wb_crons_recent(cfg, dict(q or {}), _body(b), dict(p or {}))
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the cron authority raised {type(exc).__name__}: {exc}",
                       endpoint="crons/recent", status=503)


#: Cron verbs Qubit's ``cron_bridge`` really owns. A verb outside this set is a
#: named refusal, never a silent 200.
_CRON_VERBS = frozenset({"create", "update", "delete", "pause", "resume", "run",
                         "history", "status", "delivery-options"})


def cron_verb(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/crons/<verb>`` — through ``cron_bridge`` with the confirm gate.

    Every mutating verb stages on the shared confirmation journal first (the
    shim does that), so ``409 CONFIRM_REQUIRED`` is the CORRECT answer to an
    unconfirmed write, and the client re-posts with ``confirmation_id``.
    """
    params = dict(p or {})
    sub = str(params.get("sub") or "")
    verb = sub.rsplit("/", 1)[-1] if "/" in sub else str(params.get("action") or "")
    params["action"] = verb
    if verb not in _CRON_VERBS:
        return _refuse("WB_CAPABILITY_UNBOUND",
                       f"/api/crons/{verb} has no Qubit authority; nothing was "
                       f"changed.", endpoint="crons/" + verb)
    try:
        from ..workbench_shim import crons as CR

        return CR.h_wb_cron_action(cfg, dict(q or {}), _body(b), params)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the cron authority raised {type(exc).__name__}: {exc}",
                       endpoint="crons/" + verb, status=503)


# ------------------------------------------------------------- workspace list
def workspace_list(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/list?path=`` — the composer's file picker tree.

    Delegates to the shim's read-only adapter, which owns project-root
    confinement. ``session_id`` is accepted and ignored on purpose: Qubit has no
    per-session workspace, so the tree is the approved project root — the same
    files the client would see for any session.
    """
    try:
        from ..workbench_shim import workspace as WS

        return WS.h_wb_workspace_list(cfg, dict(q or {}), _body(b), dict(p or {}))
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the workspace adapter raised {type(exc).__name__}: {exc}",
                       endpoint="list", status=503)


# ------------------------------------------------------------- approval cards
def approval_pending(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/approval/pending`` — the head of the shared journal."""
    try:
        from ..workbench_shim import approvals as AP

        return AP.h_wb_approval_pending(cfg, dict(q or {}), _body(b), dict(p or {}))
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the approval journal raised {type(exc).__name__}: {exc}",
                       endpoint="approval/pending", status=503)


def approval_respond(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/approval/respond`` — settle once, on the shared journal."""
    try:
        from ..workbench_shim import approvals as AP

        return AP.h_wb_approval_respond(cfg, dict(q or {}), _body(b), dict(p or {}))
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the approval journal raised {type(exc).__name__}: {exc}",
                       endpoint="approval/respond", status=503)


def clarify_pending(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/clarify/pending`` — GAP: no parked-question queue."""
    return 200, {"ok": True, "pending": None, "clarify_unavailable": True,
                 "detail": ("Qubit has no blocking clarify channel; agent "
                            "questions arrive in the transcript, not as a "
                            "pending card.")}


def clarify_respond(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/clarify/respond`` — nothing was ever parked to answer."""
    return _refuse("CLARIFY_QUEUE", (
        "there is no parked clarification to answer: Qubit's clarify is a "
        "routing decision inside a turn, not a queue an agent blocks on."),
        endpoint="clarify/respond", status=501,
        counterpart="reply in the transcript")


HANDLERS = {
    "chat_start": chat_start,
    "chat_steer": chat_steer,
    "chat_stop": chat_stop,
    "chat_stream_status": chat_stream_status,
    "session_detail": session_detail,
    "sessions_search": sessions_search,
    "session_verb": session_verb,
    "session_stream": session_stream,
    "session_flag": session_flag,
    "sessions_events": sessions_events,
    "profile_active": profile_active,
    "health_agent": health_agent,
    "dashboard_status": dashboard_status,
    "dashboard_config": dashboard_config,
    "onboarding_status": onboarding_status,
    "client_events_log": client_events_log,
    "transcribe_capability": transcribe_capability,
    "updates_check": updates_check,
    "workspaces": workspaces,
    "crons_recent": crons_recent,
    "cron_verb": cron_verb,
    "workspace_list": workspace_list,
    "approval_pending": approval_pending,
    "approval_respond": approval_respond,
    "clarify_pending": clarify_pending,
    "clarify_respond": clarify_respond,
}
