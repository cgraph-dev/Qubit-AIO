"""OpenClaw contractual-pin ``sessions`` family -> canonical Qubit backends.

Every handler here delegates to the same canonical Qubit module the CLI,
dashboard, ``/qubit-chat``, and voice surfaces already use:

* ``voicebridge.chat_transport`` / ``voicebridge.qubit_session_store`` — the
  single chat-session authority (``QubitSessionStore`` over the vendored
  ``state.db``);
* ``voicebridge.chat_stores`` — per-session file drafts and operator comments;
* ``voicebridge.session_activity`` — the session activity projection;
* ``voicebridge.task_ledger`` — the shared task ledger;
* ``voicebridge.checkpoints`` — journal checkpoints;
* ``voicebridge.git_scope`` — root-confined git reads;
* ``voicebridge.session_share`` — local read-only share authority.

A method whose upstream action has no real local counterpart is left ``OPEN``
(not registered here) rather than backed by a fabricated handler. A method
whose upstream action is a real local counterpart that is not the exact
upstream mechanism emits its own typed code (SAFE_EQUIVALENT); a method that
needs an external service Qubit does not run emits OPERATOR_ONLY.

Mutating handlers route through the canonical store mutation methods, which
enforce the shared confirmation/e-stop gates (``sessions.delete`` needs
``confirm``; ``chat_transport.turn`` checks the e-stop and agent-enabled gate).
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..gateway_ws import GatewayError

SESSION_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes. Qubit is single-operator and runs no cloud
#: session repository or multi-user member model, so these surface the real
#: local read-only share authority instead of inventing members/visibility.
SESSION_MEMBERS_LOCAL_SHARE = "SESSION_MEMBERS_LOCAL_SHARE"
SESSION_VISIBILITY_LOCAL_SHARE = "SESSION_VISIBILITY_LOCAL_SHARE"
SESSION_OBSERVER_LOCAL_SHARE = "SESSION_OBSERVER_LOCAL_SHARE"
#: Real local-counterpart codes for methods whose upstream mechanism differs but
#: whose action maps onto a canonical Qubit module the CLI/dashboard/voice use.
CONVERSATIONS_LOCAL_SESSIONS = "CONVERSATIONS_LOCAL_SESSIONS"
CONVERSATIONS_LOCAL_SESSION_APPEND = "CONVERSATIONS_LOCAL_SESSION_APPEND"
CONVERSATIONS_LOCAL_SESSION_TURN = "CONVERSATIONS_LOCAL_SESSION_TURN"
CONVERSATIONS_LOCAL_SESSION_CANCEL = "CONVERSATIONS_LOCAL_SESSION_CANCEL"
SESSIONS_STEER_LOCAL_INJECT = "SESSIONS_STEER_LOCAL_INJECT"
SESSIONS_FORK_LOCAL_TRANSCRIPT_COPY = "SESSIONS_FORK_LOCAL_TRANSCRIPT_COPY"
USAGE_LOGS_LOCAL_DAILY = "USAGE_LOGS_LOCAL_DAILY"
COMPACTION_LOCAL_CHECKPOINTS = "COMPACTION_LOCAL_CHECKPOINTS"
DISCUSSION_LOCAL_COMMENTS = "DISCUSSION_LOCAL_COMMENTS"
SESSIONS_TITLE_LOCAL_EXTRACTIVE = "SESSIONS_TITLE_LOCAL_EXTRACTIVE"
TERMINAL_UPLOAD_LOCAL_DRAFT = "TERMINAL_UPLOAD_LOCAL_DRAFT"
FILES_REVEAL_LOCAL_PATH = "FILES_REVEAL_LOCAL_PATH"
#: OPERATOR_ONLY typed codes (external credential/service Qubit does not run).
FORGE_API_OPERATOR_REQUIRED = "FORGE_API_OPERATOR_REQUIRED"
RUNTIME_CLOUD_UNAVAILABLE = "RUNTIME_CLOUD_UNAVAILABLE"
MESSAGING_TRANSPORT_OPERATOR_REQUIRED = "MESSAGING_TRANSPORT_OPERATOR_REQUIRED"


# --------------------------------------------------------------------------- #
# shared helpers (all ``_sess_``-prefixed; names merge across family modules)
# --------------------------------------------------------------------------- #
def _sess_store(cfg: Any) -> Any:
    from ... import chat_transport as CT

    return CT.store(cfg)


def _sess_pin_id(params: Dict[str, Any]) -> str:
    if not isinstance(params, dict):
        return ""
    for key in ("sessionKey", "session_id", "sessionId", "key", "id",
                "session"):
        val = params.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:80]
    return ""


def _sess_require_sid(params: Dict[str, Any]) -> str:
    sid = _sess_pin_id(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS, "session key is required")
    return sid


def _sess_limit(params: Dict[str, Any], default: int = 50,
                hi: int = 500) -> int:
    try:
        n = int(params.get("limit", default) or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, hi))


def _sess_estop(cfg: Any) -> bool:
    from ... import broker as BR

    return bool(BR.estop_engaged(cfg))


def _sess_estop_refusal(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
            "detail": f"emergency stop latched; {operation} refused"}


# --------------------------------------------------------------------------- #
# sessions-read / sessions-create / sessions-delete / sessions-mutations
# --------------------------------------------------------------------------- #
def _sess_sessions_preview(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    res = CT.store(cfg).show(sid)
    if str(res.get("state")) != "OK":
        return res
    preview = None
    for msg in reversed(res.get("messages", []) or []):
        if msg.get("role") == "assistant" and msg.get("content"):
            preview = str(msg["content"])[:280]
            break
    return {"state": "OK", "sessionId": sid, "session": res.get("session"),
            "preview": preview,
            "message_count": int(res.get("message_count", 0) or 0)}


def _sess_sessions_describe(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    res = CT.store(cfg).show(sid)
    if str(res.get("state")) != "OK":
        return res
    rec = res.get("session") or {}
    return {"state": "OK", "sessionId": sid, "title": rec.get("name"),
            "model": rec.get("model"), "archived": bool(rec.get("archived")),
            "message_count": int(res.get("message_count", 0) or 0),
            "authority": "voicebridge.chat_transport.ChatSessionStore"}


def _sess_sessions_resolve(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    st = CT.store(cfg)
    sid = _sess_pin_id(params)
    if sid:
        res = st.show(sid)
        if str(res.get("state")) == "OK":
            return {"state": "OK", "ok": True, "key": sid,
                    "session": res.get("session")}
    ref = params.get("reference")
    ref = ref if isinstance(ref, dict) else {}
    needle = str(params.get("label") or ref.get("slug")
                 or params.get("shortId") or ref.get("key") or "").strip()
    if needle:
        listing = st.list(limit=100, query=needle)
        rows = listing.get("sessions", []) or []
        if rows:
            row = rows[0]
            return {"state": "OK", "ok": True, "key": row.get("id"),
                    "session": row}
    if bool(params.get("allowMissing", False)):
        return {"state": "OK", "ok": False, "detail": "no matching session"}
    return {"state": "MISSING", "ok": False, "code": "SESSION_NOT_FOUND",
            "detail": "no matching session"}


def _sess_sessions_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("session create")
    routed = CT.resolve_model(cfg, params.get("model"))
    if not routed.get("ok"):
        return routed
    return CT.store(cfg).create(str(params.get("name", "") or ""),
                                session_id=_sess_pin_id(params) or None,
                                model=routed.get("model"))


def _sess_sessions_delete(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    return CT.store(cfg).delete(sid, confirm=bool(params.get("confirm",
                                                             False)))


def _sess_sessions_recover(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    return CT.store(cfg).restore(sid)


def _sess_sessions_patch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("session patch")
    sid = _sess_require_sid(params)
    st = CT.store(cfg)
    if "name" in params:
        return st.rename(sid, str(params.get("name", "") or ""))
    if "archived" in params:
        return st.archive(sid, bool(params.get("archived")))
    raise GatewayError(SESSION_BAD_PARAMS, "patch needs name or archived")


def _sess_sessions_patch_many(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("session patch")
    targets = params.get("targets")
    patch = params.get("patch")
    if not isinstance(targets, list) or not targets:
        raise GatewayError(SESSION_BAD_PARAMS, "targets is required")
    if not isinstance(patch, dict):
        raise GatewayError(SESSION_BAD_PARAMS, "patch must be an object")
    st = CT.store(cfg)
    results: List[Dict[str, Any]] = []
    for target in targets:
        tid = _sess_pin_id(target if isinstance(target, dict) else {})
        if not tid:
            results.append({"state": "FAILED", "code": SESSION_BAD_PARAMS,
                            "detail": "target has no session key"})
            continue
        if "name" in patch:
            out = st.rename(tid, str(patch.get("name", "") or ""))
        elif "archived" in patch:
            out = st.archive(tid, bool(patch.get("archived")))
        else:
            out = {"state": "FAILED", "code": SESSION_BAD_PARAMS,
                   "detail": "patch needs name or archived"}
        results.append({"key": tid, **out})
    ok = sum(1 for r in results if str(r.get("state")) == "OK")
    return {"state": "OK", "results": results, "applied": ok,
            "total": len(results)}


# --------------------------------------------------------------------------- #
# session-catalog
# --------------------------------------------------------------------------- #
def _sess_catalog_continue(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("catalog continue")
    sid = _sess_require_sid(params)
    return CT.store(cfg).ensure(sid)


def _sess_catalog_archive(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("catalog archive")
    sid = _sess_require_sid(params)
    return CT.store(cfg).archive(sid, archived=True)


def _sess_catalog_start_terminal(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    import base64

    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("terminal start")
    sid = _sess_pin_id(params) or CT.DEFAULT_SESSION
    sess = CT.pty_session(cfg, sid)
    try:
        sess.cols = max(1, int(params.get("cols", sess.cols) or sess.cols))
        sess.rows = max(1, int(params.get("rows", sess.rows) or sess.rows))
    except (TypeError, ValueError):
        raise GatewayError(SESSION_BAD_PARAMS, "cols and rows must be integers")
    return {"state": "OK", "sessionId": sess.session_id, "cols": sess.cols,
            "rows": sess.rows, "alive": sess.alive,
            "banner": base64.b64encode(sess.banner()).decode("ascii"),
            "authority": "voicebridge.chat_transport.AgentPtySession "
                         "(agent-backed, no shell)"}


# --------------------------------------------------------------------------- #
# chat family
# --------------------------------------------------------------------------- #
def _sess_chat_history(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    return CT.store(cfg).messages(sid, limit=_sess_limit(params, 200, 1000))


def _sess_chat_message_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    mid = str(params.get("messageId", params.get("message_id", "")) or "")
    if not mid:
        raise GatewayError(SESSION_BAD_PARAMS, "messageId is required")
    res = CT.store(cfg).messages(sid, limit=1000)
    if str(res.get("state")) != "OK":
        return res
    for msg in res.get("messages", []) or []:
        meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
        ids = {str(msg.get("id", "")), str(msg.get("message_id", "")),
               str(msg.get("platform_message_id", "")),
               str(meta.get("id", "")),
               str(meta.get("message_id", ""))}
        if mid in ids:
            return {"state": "OK", "sessionId": sid, "message": msg}
    return {"state": "MISSING", "code": "MESSAGE_NOT_FOUND",
            "detail": f"no message {mid!r}"}


def _sess_chat_startup(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_pin_id(params) or CT.DEFAULT_SESSION
    res = CT.store(cfg).ensure(sid)
    return {"state": str(res.get("state", "OK")),
            "session": res.get("session"),
            "messages": res.get("messages", [])}


def _sess_chat_metadata(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    routed = CT.resolve_model(cfg, params.get("model"))
    out: Dict[str, Any] = {"state": "OK",
                           "model": routed.get("model") if routed.get("ok")
                           else None,
                           "routed": routed}
    sid = _sess_pin_id(params)
    if sid:
        rec = CT.store(cfg).show(sid)
        if str(rec.get("state")) == "OK":
            out["session"] = rec.get("session")
    return out


def _sess_chat_send(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sess_run_turn(cfg, params)


def _sess_sessions_send(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sess_run_turn(cfg, params)


def _sess_run_turn(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    text = str(params.get("message", params.get("text", "")) or "")
    if not text.strip():
        raise GatewayError(SESSION_BAD_PARAMS, "message is required")
    sid = _sess_pin_id(params) or CT.DEFAULT_SESSION
    res = CT.turn(cfg, text, session=sid, model=params.get("model"),
                  actor="gateway")
    if res.get("ok"):
        return {"state": "OK", "sessionId": res.get("session"),
                "reply": res.get("reply"),
                "message": res.get("message"),
                "message_count": res.get("message_count"),
                "elapsed_ms": res.get("elapsed_ms")}
    return res


def _sess_chat_inject(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    text = str(params.get("message", "") or "")
    if not text.strip():
        raise GatewayError(SESSION_BAD_PARAMS, "message is required")
    label = str(params.get("label", "") or "")[:100]
    return CT.store(cfg).append(
        sid, "user", text, meta={"injected": True, "label": label})


def _sess_chat_abort(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_pin_id(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS, "session key is required")
    return CT.cancel(sid)


def _sess_sessions_abort(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sess_chat_abort(cfg, params)


# --------------------------------------------------------------------------- #
# sessions-files / artifacts (per-session file drafts — canonical local store)
# --------------------------------------------------------------------------- #
def _sess_files_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    return CT.draft_store(cfg).list(
        sid, limit=_sess_limit(params, 100, 500),
        offset=int(params.get("offset", 0) or 0),
        include_content=bool(params.get("includeContent", True)))


def _sess_files_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    path = str(params.get("path", "") or "").strip()
    if not path:
        raise GatewayError(SESSION_BAD_PARAMS, "path is required")
    return CT.draft_store(cfg).get(sid, path)


def _sess_files_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("file write")
    sid = _sess_require_sid(params)
    path = str(params.get("path", "") or "").strip()
    if not path:
        raise GatewayError(SESSION_BAD_PARAMS, "path is required")
    return CT.draft_store(cfg).put(
        sid, path, str(params.get("content", "") or ""),
        base_sha256=str(params.get("baseSha256", "") or ""), actor="gateway")


def _sess_artifacts_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    out = CT.draft_store(cfg).list(
        sid, limit=_sess_limit(params, 100, 500),
        offset=int(params.get("offset", 0) or 0), include_content=False)
    return {"state": str(out.get("state", "OK")),
            "sessionId": sid, "artifacts": out.get("drafts", []),
            "total": out.get("total", 0),
            "authority": "voicebridge.chat_stores.FileDraftStore "
                         "(per-session file artifacts)"}


def _sess_artifacts_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    path = str(params.get("path", params.get("artifactId", "")) or "").strip()
    if not path:
        raise GatewayError(SESSION_BAD_PARAMS, "path is required")
    res = CT.draft_store(cfg).get(sid, path)
    if str(res.get("state")) == "OK":
        return {"state": "OK", "sessionId": sid, "artifact": res.get("draft")}
    return res


def _sess_artifacts_download(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    path = str(params.get("path", params.get("artifactId", "")) or "").strip()
    if not path:
        raise GatewayError(SESSION_BAD_PARAMS, "path is required")
    res = CT.draft_store(cfg).get(sid, path)
    if str(res.get("state")) != "OK":
        return res
    draft = res.get("draft") or {}
    return {"state": "OK", "sessionId": sid, "path": draft.get("path"),
            "content": draft.get("content"), "bytes": draft.get("bytes"),
            "sha256": draft.get("sha256")}


# --------------------------------------------------------------------------- #
# progress-card / session-discussion / transcripts
# --------------------------------------------------------------------------- #
def _sess_progress_card_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    shown = CT.store(cfg).show(sid)
    if str(shown.get("state")) != "OK":
        return shown
    proj = CT.build_projection(cfg, sid, git_root=str(
        params.get("git_root", "") or ""), limit=_sess_limit(params, 20, 200))
    counts = proj.get("counts", {}) if isinstance(proj, dict) else {}
    markdown = ("## Session progress\n\n"
                f"- messages: {shown.get('message_count', 0)}\n"
                f"- activity events: {sum(counts.values()) if counts else 0}\n")
    return {"state": "OK", "sessionKey": sid, "markdown": markdown,
            "plan": [], "projection": proj,
            "authority": "voicebridge.session_activity.build_projection"}


def _sess_discussion_info(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    out = CT.comment_store(cfg).list_comments(
        sid, limit=_sess_limit(params, 100, 500))
    comments = out.get("comments", []) if isinstance(out, dict) else []
    return {"state": "OK", "sessionKey": sid, "comments": comments,
            "count": len(comments),
            "authority": "voicebridge.chat_stores.CommentStore"}


def _sess_transcripts_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    query = str(params.get("query", params.get("providerId", "")) or "")
    out = CT.store(cfg).list(limit=_sess_limit(params, 50, 100), query=query)
    return {"state": "OK", "transcripts": out.get("sessions", []),
            "total": out.get("total", 0),
            "authority": "voicebridge.chat_transport.ChatSessionStore"}


def _sess_transcripts_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    selector = str(params.get("selector", params.get("session_id", "")) or "")
    if not selector:
        raise GatewayError(SESSION_BAD_PARAMS, "selector is required")
    out = CT.store(cfg).export(selector)
    if str(out.get("state")) != "OK":
        return out
    if not bool(params.get("includeUtterances", True)):
        out = {k: v for k, v in out.items() if k != "messages"}
    return out


# --------------------------------------------------------------------------- #
# usage / taskSuggestions / checkpoints / diff
# --------------------------------------------------------------------------- #
def _sess_days(params: Dict[str, Any]) -> int:
    try:
        days = int(params.get("days", 30) or 30)
    except (TypeError, ValueError):
        days = 30
    return max(1, min(days, 365))


def _sess_sessions_usage(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import qubit_session_store as HS

    days = _sess_days(params)
    usage = HS.analytics_usage(cfg, days=days)
    if usage is None:
        return {"state": "EMPTY", "period_days": days, "totals": {},
                "by_model": [], "daily": [],
                "authority": "voicebridge.qubit_session_store.analytics_usage"}
    return {"state": "OK", "period_days": days,
            "totals": usage.get("totals", {}),
            "by_model": usage.get("by_model", []),
            "daily": usage.get("daily", []),
            "authority": "voicebridge.qubit_session_store.analytics_usage"}


def _sess_sessions_usage_timeseries(cfg: Any,
                                    params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import qubit_session_store as HS

    days = _sess_days(params)
    usage = HS.analytics_usage(cfg, days=days)
    series = (usage or {}).get("daily", []) if usage else []
    return {"state": "OK" if usage else "EMPTY", "period_days": days,
            "series": series,
            "authority": "voicebridge.qubit_session_store.analytics_usage"}


def _sess_task_suggestions_list(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import task_ledger as TL

    listing = TL.list_tasks(cfg, limit=_sess_limit(params, 100, 500))
    rows = listing.get("tasks", []) if isinstance(listing, dict) else []
    open_states = {"pending", "queued", "running", "in_progress", "todo"}
    sugg = [r for r in rows
            if str(r.get("state", "")).lower() in open_states]
    return {"state": "OK", "suggestions": sugg[:25], "considered": len(rows),
            "authority": "derived read-only from the shared task ledger; "
                         "nothing is dispatched"}


def _sess_branches_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import checkpoints as CP

    items = CP.list_all(cfg.state_dir())
    return {"state": "OK", "branches": items, "count": len(items),
            "authority": "voicebridge.checkpoints (journal checkpoints)"}


def _sess_sessions_diff(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import git_scope as GI

    path = str(params.get("path", params.get("scope", "")) or "")
    root = str(params.get("root", "") or "")
    commit = str(params.get("commit", "") or "")
    try:
        if commit:
            out = GI.show(cfg, root=root, ref=commit, path=path,
                          requested_by="gateway")
            return {"state": "OK", "ref": out.get("ref"),
                    "path": out.get("path"), "diff": out.get("content", ""),
                    "truncated": bool(out.get("truncated", False)),
                    "authority": "voicebridge.git_scope.show"}
        out = GI.diff(cfg, root=root, path=path, requested_by="gateway")
    except GI.GitError as exc:
        return {"state": "UNAVAILABLE", "code": exc.code,
                "detail": exc.detail,
                "authority": "voicebridge.git_scope (root-confined)"}
    return {"state": "OK", "path": out.get("path", ""),
            "diff": out.get("patch", ""), "stat": out.get("stat", ""),
            "truncated": bool(out.get("truncated", False)),
            "authority": "voicebridge.git_scope.diff"}


# --------------------------------------------------------------------------- #
# terminal family (agent-backed /chat terminal, no shell)
# --------------------------------------------------------------------------- #
def _sess_terminal_open(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    import base64

    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("terminal open")
    sid = _sess_pin_id(params) or CT.DEFAULT_SESSION
    sess = CT.pty_session(cfg, sid)
    try:
        sess.cols = max(1, int(params.get("cols", sess.cols) or sess.cols))
        sess.rows = max(1, int(params.get("rows", sess.rows) or sess.rows))
    except (TypeError, ValueError):
        raise GatewayError(SESSION_BAD_PARAMS, "cols and rows must be integers")
    return {"ok": True, "state": "OK", "sessionId": sess.session_id,
            "cols": sess.cols, "rows": sess.rows, "alive": sess.alive,
            "banner": base64.b64encode(sess.banner()).decode("ascii")}


def _sess_terminal_attach(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    import base64

    from ... import chat_transport as CT

    sid = _sess_pin_id(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS, "sessionId is required")
    sess = CT.pty_session(cfg, sid)
    return {"ok": True, "state": "OK", "sessionId": sess.session_id,
            "cols": sess.cols, "rows": sess.rows, "alive": sess.alive,
            "banner": base64.b64encode(sess.banner()).decode("ascii")}


def _sess_terminal_input(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    import base64

    from ... import chat_transport as CT

    sid = _sess_pin_id(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS, "sessionId is required")
    data = str(params.get("data", "") or "")
    res = CT.pty_feed(
        cfg, sid,
        base64.b64encode(data.encode("utf-8", "replace")).decode("ascii"))
    if not res.get("ok"):
        return res
    output = base64.b64decode(res.get("output", "") or "").decode(
        "utf-8", "replace")
    return {"ok": True, "state": "OK", "sessionId": sid,
            "alive": res.get("alive"), "output": output}


def _sess_terminal_resize(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_pin_id(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS, "sessionId is required")
    sess = CT.pty_session(cfg, sid)
    try:
        sess.cols = max(1, int(params.get("cols", sess.cols) or sess.cols))
        sess.rows = max(1, int(params.get("rows", sess.rows) or sess.rows))
    except (TypeError, ValueError):
        raise GatewayError(SESSION_BAD_PARAMS, "cols and rows must be integers")
    return {"ok": True, "state": "OK", "sessionId": sid, "cols": sess.cols,
            "rows": sess.rows}


def _sess_terminal_close(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_pin_id(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS, "sessionId is required")
    sess = CT.pty_session(cfg, sid)
    sess.alive = False
    cancelled = CT.cancel(sid)
    return {"ok": True, "state": "OK", "sessionId": sid,
            "alive": sess.alive, "cancelled": bool(cancelled.get("ok"))}


# --------------------------------------------------------------------------- #
# sessions-sharing -> local read-only share authority (SAFE_EQUIVALENT)
# --------------------------------------------------------------------------- #
def _sess_members_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SESSION_MEMBERS_LOCAL_SHARE,
            "detail": "Qubit is single-operator with no multi-user session "
                      "member model; local read-only shares are the safe "
                      "counterpart",
            "qubit_counterpart": "voicebridge.session_share.list_shares + "
                                 "`qubit share list`",
            "members": []}


def _sess_members_add(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SESSION_MEMBERS_LOCAL_SHARE,
            "detail": "Qubit runs no member enrollment; publish a local "
                      "read-only share instead",
            "qubit_counterpart": "voicebridge.session_share.publish + "
                                 "`qubit share publish`",
            "changed": False}


def _sess_members_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SESSION_MEMBERS_LOCAL_SHARE,
            "detail": "Qubit runs no member enrollment; revoke a local share "
                      "instead",
            "qubit_counterpart": "voicebridge.session_share.revoke + "
                                 "`qubit share revoke`",
            "changed": False}


def _sess_members_list_evidence(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SESSION_MEMBERS_LOCAL_SHARE,
            "detail": "Qubit has no per-member access evidence; local share "
                      "records are the safe counterpart",
            "qubit_counterpart": "voicebridge.session_share.list_shares",
            "evidence": []}


def _sess_visibility_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SESSION_VISIBILITY_LOCAL_SHARE,
            "detail": "Qubit has no per-session cloud visibility flag; a "
                      "local revocable read-only share is the safe "
                      "counterpart",
            "qubit_counterpart": "voicebridge.session_share.publish/revoke + "
                                 "`qubit share`",
            "changed": False}


# --------------------------------------------------------------------------- #
# sessions-github / sessions-dispatch -> external prerequisites (OPERATOR_ONLY)
# --------------------------------------------------------------------------- #
def _sess_github_publish(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": FORGE_API_OPERATOR_REQUIRED,
            "detail": "publishing session changes needs a configured forge "
                      "credential and a network-reachable forge API; Qubit "
                      "runs no forge service",
            "operator_prerequisite": "a configured forge credential (e.g. a "
                                     "GitHub/GitLab token) and a reachable "
                                     "forge API",
            "qubit_counterpart": "local change review: CLI `qubit git "
                                 "status|log|diff` and the dashboard git "
                                 "review surface"}


def _sess_github_options(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": FORGE_API_OPERATOR_REQUIRED,
            "detail": "no forge options are fetched; Qubit runs no forge "
                      "service",
            "operator_prerequisite": "a configured forge credential and a "
                                     "reachable forge API",
            "qubit_counterpart": "local git branches via `qubit git branches`",
            "options": []}


def _sess_github_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": FORGE_API_OPERATOR_REQUIRED,
            "detail": "no remote GitHub session status is fetched; Qubit runs "
                      "no forge service",
            "operator_prerequisite": "a configured forge credential and a "
                                     "reachable forge API",
            "qubit_counterpart": "local change review via `qubit git`"}


def _sess_github_confirm(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": FORGE_API_OPERATOR_REQUIRED,
            "detail": "no remote publish to confirm; Qubit runs no forge "
                      "service",
            "operator_prerequisite": "a configured forge credential and a "
                                     "reachable forge API",
            "qubit_counterpart": "local change review via `qubit git`"}


def _sess_dispatch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": RUNTIME_CLOUD_UNAVAILABLE,
            "detail": "Qubit runs no cloud worker placement service; sessions "
                      "stay on the local runtime",
            "operator_prerequisite": "an injected cloud-worker provider "
                                     "transport and a reachable provider",
            "qubit_counterpart": "local sessions via the one Qubit runtime"}


def _sess_reclaim(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": RUNTIME_CLOUD_UNAVAILABLE,
            "detail": "no cloud-placed session to reclaim; Qubit sessions are "
                      "already local",
            "operator_prerequisite": "a cloud-placed session (requires a "
                                     "cloud-worker provider transport)",
            "qubit_counterpart": "local sessions via the one Qubit runtime"}


def _sess_move(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": RUNTIME_CLOUD_UNAVAILABLE,
            "detail": "Qubit moves no session between machines; there is only "
                      "the local runtime",
            "operator_prerequisite": "a second cloud worker host and a "
                                     "provider transport",
            "qubit_counterpart": "local sessions via the one Qubit runtime"}


# --------------------------------------------------------------------------- #
# conversations -> canonical chat-session authority (SAFE_EQUIVALENT)
# --------------------------------------------------------------------------- #
def _sess_message_text(params: Dict[str, Any]) -> str:
    for key in ("message", "text", "body", "content"):
        val = params.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return ""


def _sess_conversation_session(params: Dict[str, Any]) -> str:
    """Resolve the canonical session key a conversation call targets.

    Upstream keys conversations by ``conversationRef``/``sourceSessionKey``;
    the canonical Qubit equivalent is the one chat-session key.
    """
    sid = _sess_pin_id(params)
    if sid:
        return sid
    ref = params.get("conversationRef", params.get("conversation_ref", ""))
    if ref is not None and str(ref).strip():
        return str(ref).strip()[:80]
    return ""


def _sess_conversations_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    out = CT.store(cfg).list(
        limit=_sess_limit(params, 50, 100),
        offset=int(params.get("offset", 0) or 0),
        query=str(params.get("query", "") or ""))
    return {"state": str(out.get("state", "OK")),
            "conversations": out.get("sessions", []),
            "total": out.get("total", 0),
            "code": CONVERSATIONS_LOCAL_SESSIONS,
            "qubit_counterpart": "voicebridge.chat_transport.ChatSessionStore "
                                 "(the one chat-session authority)",
            "authority": "local chat sessions; Qubit runs no separate "
                         "multi-party conversation store"}


def _sess_conversations_send(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("conversation send")
    text = _sess_message_text(params)
    if not text.strip():
        raise GatewayError(SESSION_BAD_PARAMS, "message is required")
    sid = _sess_conversation_session(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS,
                           "sourceSessionKey or conversationRef is required")
    out = CT.store(cfg).append(
        sid, "user", text,
        meta={"conversation": str(params.get("conversationRef", "") or ""),
              "source": "conversations.send"})
    return {**out, "code": CONVERSATIONS_LOCAL_SESSION_APPEND,
            "sessionId": sid,
            "qubit_counterpart": "canonical session transcript append + "
                                 "`qubit chat`",
            "authority": "recorded on the one Qubit session authority; Qubit "
                         "runs no background multi-party delivery"}


def _sess_conversations_turn(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    text = _sess_message_text(params)
    if not text.strip():
        raise GatewayError(SESSION_BAD_PARAMS, "message is required")
    sid = _sess_conversation_session(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS,
                           "sourceSessionKey or conversationRef is required")
    res = CT.turn(cfg, text, session=sid, model=params.get("model"),
                  actor="gateway")
    return {**res, "code": CONVERSATIONS_LOCAL_SESSION_TURN,
            "sessionId": res.get("session") or sid,
            "qubit_counterpart": "voicebridge.chat_transport.turn (one real "
                                 "agent turn on the one session authority)",
            "authority": "canonical local agent turn; no separate conversation "
                         "runtime exists"}


def _sess_conversations_turn_cancel(cfg: Any,
                                    params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_conversation_session(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS,
                           "conversationRef or sourceSessionKey is required")
    out = CT.cancel(sid)
    return {**out, "code": CONVERSATIONS_LOCAL_SESSION_CANCEL,
            "cancel_code": out.get("code"), "sessionId": sid,
            "qubit_counterpart": "voicebridge.chat_transport.cancel (the one "
                                 "in-flight turn cancel)",
            "authority": "canonical session turn cancellation"}


# --------------------------------------------------------------------------- #
# sessions-messaging -> inject into the canonical transcript (SAFE_EQUIVALENT)
# --------------------------------------------------------------------------- #
def _sess_sessions_steer(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("session steer")
    sid = _sess_require_sid(params)
    text = _sess_message_text(params)
    if not text.strip():
        raise GatewayError(SESSION_BAD_PARAMS, "message is required")
    out = CT.store(cfg).append(sid, "user", text, meta={"steer": True})
    return {**out, "code": SESSIONS_STEER_LOCAL_INJECT, "sessionId": sid,
            "qubit_counterpart": "canonical transcript append (the next turn "
                                 "consumes the steered message)",
            "authority": "Qubit has no mid-turn steering channel; the steer is "
                         "recorded on the one session authority"}


# --------------------------------------------------------------------------- #
# sessions-rewind -> local transcript fork (SAFE_EQUIVALENT)
# --------------------------------------------------------------------------- #
def _sess_sessions_fork(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    import uuid

    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("session fork")
    sid = _sess_require_sid(params)
    st = CT.store(cfg)
    src = st.show(sid)
    if str(src.get("state")) != "OK":
        return src
    messages = list(src.get("messages", []) or [])
    entry = str(params.get("entryId", params.get("entry_id", "")) or "").strip()
    cut = len(messages)
    if entry:
        if entry.isdigit():
            cut = max(0, min(int(entry), len(messages)))
        else:
            cut = -1
            for i, msg in enumerate(messages):
                ids = {str(msg.get("id", "")),
                       str(msg.get("message_id", "")),
                       str(msg.get("platform_message_id", ""))}
                if entry in ids:
                    cut = i + 1
                    break
            if cut < 0:
                return {"state": "MISSING",
                        "code": "SESSION_ENTRY_NOT_FOUND",
                        "detail": f"no message {entry!r} to fork from"}
    rec = src.get("session") or {}
    base = str(rec.get("name") or sid)
    new_id = f"{sid[:60]}-fork-{uuid.uuid4().hex[:8]}"
    payload = {
        "session": {"id": new_id, "name": f"{base} (fork)", "model":
                    rec.get("model")},
        "messages": messages[:cut],
    }
    created = st.import_session(payload, overwrite=False, identity="gateway")
    if str(created.get("state")) != "OK":
        return created
    return {"state": "OK", "sessionId": new_id, "forked_from": sid,
            "entry_index": cut, "message_count": cut,
            "code": SESSIONS_FORK_LOCAL_TRANSCRIPT_COPY,
            "qubit_counterpart": "voicebridge.chat_transport.ChatSessionStore "
                                 "(a real transcript copy into a new session)",
            "authority": "Qubit has no entry-level conversation branch; the "
                         "fork is a new session carrying the copied transcript"}


# --------------------------------------------------------------------------- #
# usage / compaction -> canonical reads (SAFE_EQUIVALENT)
# --------------------------------------------------------------------------- #
def _sess_sessions_usage_logs(cfg: Any,
                              params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import qubit_session_store as HS

    days = _sess_days(params)
    usage = HS.analytics_usage(cfg, days=days)
    daily = (usage or {}).get("daily", []) if usage else []
    return {"state": "OK" if usage else "EMPTY", "period_days": days,
            "logs": daily,
            "totals": (usage or {}).get("totals", {}) if usage else {},
            "code": USAGE_LOGS_LOCAL_DAILY,
            "qubit_counterpart": "voicebridge.qubit_session_store."
                                 "analytics_usage (daily usage series)",
            "authority": "Qubit exposes the canonical per-day usage series; no "
                         "separate per-request usage log store exists"}


def _sess_compaction_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import checkpoints as CP

    items = CP.list_all(cfg.state_dir())
    return {"state": "OK", "checkpoints": items, "count": len(items),
            "code": COMPACTION_LOCAL_CHECKPOINTS,
            "qubit_counterpart": "voicebridge.checkpoints (journal checkpoints)",
            "authority": "Qubit has no conversation compaction store; the "
                         "canonical named journal checkpoints are the local "
                         "counterpart"}


# --------------------------------------------------------------------------- #
# session-discussion / sessions-title -> canonical reads (SAFE_EQUIVALENT)
# --------------------------------------------------------------------------- #
def _sess_discussion_open(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    out = CT.comment_store(cfg).list_comments(
        sid, limit=_sess_limit(params, 100, 500))
    comments = out.get("comments", []) if isinstance(out, dict) else []
    return {"state": "OK", "sessionKey": sid, "open": True,
            "comments": comments, "count": len(comments),
            "code": DISCUSSION_LOCAL_COMMENTS,
            "qubit_counterpart": "voicebridge.chat_stores.CommentStore (the "
                                 "one per-session discussion thread)",
            "authority": "Qubit opens the canonical local discussion thread; "
                         "no hosted session-discussion service runs"}


def _sess_sessions_title_prepare(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import summarizer as SM

    text = _sess_message_text(params)
    if not text.strip():
        return {"state": "OK", "title": None,
                "code": SESSIONS_TITLE_LOCAL_EXTRACTIVE,
                "qubit_counterpart": "voicebridge.summarizer (extractive)",
                "authority": "no message; no title derived"}
    title = SM.summarize(text, cfg={"provider": "extractive", "max_words": 8,
                                    "max_chars": 120}).strip().rstrip(".")
    return {"state": "OK", "title": title or None,
            "code": SESSIONS_TITLE_LOCAL_EXTRACTIVE,
            "qubit_counterpart": "voicebridge.summarizer.summarize (extractive, "
                                 "no network)",
            "authority": "Qubit derives a deterministic local title; no hosted "
                         "title model is invoked"}


# --------------------------------------------------------------------------- #
# terminal: in-process session registry + draft-store upload
# --------------------------------------------------------------------------- #
def _sess_terminal_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    try:
        scope = str(cfg.state_dir()) if hasattr(cfg, "state_dir") else "global"
    except Exception:
        scope = "global"
    prefix = f"{scope}\0"
    lock = getattr(CT, "_pty_lock", None)
    registry = getattr(CT, "_pty_sessions", {}) or {}
    if lock is not None:
        with lock:
            items = list(registry.items())
    else:
        items = list(registry.items())
    rows: List[Dict[str, Any]] = []
    for key, sess in items:
        if not str(key).startswith(prefix):
            continue
        rows.append({"sessionId": sess.session_id, "alive": bool(sess.alive),
                     "cols": int(sess.cols), "rows": int(sess.rows),
                     "model": sess.model})
    return {"state": "OK", "terminals": rows, "count": len(rows),
            "authority": "voicebridge.chat_transport.AgentPtySession in-process "
                         "registry (the one /chat terminal authority)"}


def _sess_terminal_upload(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    import base64

    from ... import chat_transport as CT

    if _sess_estop(cfg):
        return _sess_estop_refusal("terminal upload")
    sid = _sess_pin_id(params)
    if not sid:
        raise GatewayError(SESSION_BAD_PARAMS, "sessionId is required")
    name = str(params.get("name", "") or "").strip()
    if not name:
        raise GatewayError(SESSION_BAD_PARAMS, "name is required")
    raw = str(params.get("contentBase64",
                         params.get("content_base64", "")) or "")
    try:
        content = base64.b64decode(raw or "", validate=False).decode(
            "utf-8", "replace")
    except Exception as exc:
        raise GatewayError(SESSION_BAD_PARAMS,
                           "contentBase64 is not valid base64") from exc
    out = CT.draft_store(cfg).put(sid, name, content, actor="gateway")
    return {**out, "code": TERMINAL_UPLOAD_LOCAL_DRAFT, "sessionId": sid,
            "name": name,
            "qubit_counterpart": "voicebridge.chat_stores.FileDraftStore (the "
                                 "per-session file store `sessions.files.set` "
                                 "uses)",
            "authority": "uploaded into the canonical per-session draft store; "
                         "Qubit runs no OS-level file drop"}


def _sess_files_reveal(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import chat_transport as CT

    sid = _sess_require_sid(params)
    path = str(params.get("path", params.get("artifactId", "")) or "").strip()
    if not path:
        raise GatewayError(SESSION_BAD_PARAMS, "path is required")
    res = CT.draft_store(cfg).get(sid, path)
    if str(res.get("state")) != "OK":
        return res
    draft = res.get("draft") or {}
    return {"state": "OK", "sessionId": sid, "path": draft.get("path"),
            "sha256": draft.get("sha256"), "bytes": draft.get("bytes"),
            "code": FILES_REVEAL_LOCAL_PATH,
            "qubit_counterpart": "sessions.files.get / the dashboard file "
                                 "viewer (the draft path on the one store)",
            "authority": "Qubit is headless and never opens a server-side file "
                         "manager; it returns the canonical draft reference"}


# --------------------------------------------------------------------------- #
# session-sharing observer -> local share authority (SAFE_EQUIVALENT)
# --------------------------------------------------------------------------- #
def _sess_observer_visibility(cfg: Any,
                              params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": SESSION_OBSERVER_LOCAL_SHARE,
            "detail": "Qubit is single-operator with no per-observer cloud "
                      "visibility model; a local revocable read-only share is "
                      "the safe counterpart",
            "qubit_counterpart": "voicebridge.session_share.list_shares + "
                                 "`qubit share list`",
            "visible": False}


# --------------------------------------------------------------------------- #
# send / message.action / poll -> external messaging transport (OPERATOR_ONLY)
# --------------------------------------------------------------------------- #
def _sess_messaging_operator_gate(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": MESSAGING_TRANSPORT_OPERATOR_REQUIRED,
            "detail": f"{operation} needs a configured outbound channel account "
                      "and a network-reachable provider transport; Qubit runs no "
                      "hosted messaging relay",
            "operator_prerequisite": "a configured messaging account (provider "
                                     "credential + endpoint) and a reachable "
                                     "provider transport",
            "qubit_counterpart": "`qubit msg draft` -> `qubit msg approve` -> "
                                 "`qubit msg send --confirm` (voicebridge."
                                 "messaging, e-stop/confirm gated)"}


def _sess_messaging_send(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sess_messaging_operator_gate("send")


def _sess_message_action(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sess_messaging_operator_gate("message.action")


def _sess_poll(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _sess_messaging_operator_gate("poll")


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
# Each method is registered with a literal ``registry.register("name", handler)``
# call so ``scripts/qubit_upgrade_manifest.py`` can prove (AST) which file
# defines each handler. Only methods with a real canonical local backend (or a
# named local counterpart / operator prerequisite) are registered; every other
# contractual-pin family method stays explicitly OPEN.
def register(registry: Any) -> None:
    """Register every sessions-family handler backed by a canonical module."""
    # sessions-read / create / delete / mutations
    registry.register("sessions.preview", _sess_sessions_preview)
    registry.register("sessions.describe", _sess_sessions_describe)
    registry.register("sessions.resolve", _sess_sessions_resolve)
    registry.register("sessions.create", _sess_sessions_create)
    registry.register("sessions.delete", _sess_sessions_delete)
    registry.register("sessions.recover", _sess_sessions_recover)
    registry.register("sessions.patch", _sess_sessions_patch)
    registry.register("sessions.patchMany", _sess_sessions_patch_many)
    # session-catalog
    registry.register("sessions.catalog.continue", _sess_catalog_continue)
    registry.register("sessions.catalog.archive", _sess_catalog_archive)
    registry.register("sessions.catalog.startTerminal",
                      _sess_catalog_start_terminal)
    # chat / chat-abort / sessions-abort / sessions-messaging
    registry.register("chat.history", _sess_chat_history)
    registry.register("chat.message.get", _sess_chat_message_get)
    registry.register("chat.startup", _sess_chat_startup)
    registry.register("chat.metadata", _sess_chat_metadata)
    registry.register("chat.send", _sess_chat_send)
    registry.register("chat.inject", _sess_chat_inject)
    registry.register("chat.abort", _sess_chat_abort)
    registry.register("sessions.send", _sess_sessions_send)
    registry.register("sessions.abort", _sess_sessions_abort)
    # sessions-files / artifacts
    registry.register("sessions.files.list", _sess_files_list)
    registry.register("sessions.files.get", _sess_files_get)
    registry.register("sessions.files.set", _sess_files_set)
    registry.register("artifacts.list", _sess_artifacts_list)
    registry.register("artifacts.get", _sess_artifacts_get)
    registry.register("artifacts.download", _sess_artifacts_download)
    # progress-card / session-discussion / transcripts
    registry.register("progressCard.get", _sess_progress_card_get)
    registry.register("session.discussion.info", _sess_discussion_info)
    registry.register("transcripts.list", _sess_transcripts_list)
    registry.register("transcripts.get", _sess_transcripts_get)
    # usage / task-suggestions / checkpoints / diff
    registry.register("sessions.usage", _sess_sessions_usage)
    registry.register("sessions.usage.timeseries",
                      _sess_sessions_usage_timeseries)
    registry.register("taskSuggestions.list", _sess_task_suggestions_list)
    registry.register("sessions.branches.list", _sess_branches_list)
    registry.register("sessions.diff", _sess_sessions_diff)
    # terminal (agent-backed /chat terminal, no shell)
    registry.register("terminal.open", _sess_terminal_open)
    registry.register("terminal.attach", _sess_terminal_attach)
    registry.register("terminal.input", _sess_terminal_input)
    registry.register("terminal.resize", _sess_terminal_resize)
    registry.register("terminal.close", _sess_terminal_close)
    # sessions-sharing (local share authority, SAFE_EQUIVALENT typed codes)
    registry.register("session.members.list", _sess_members_list)
    registry.register("session.members.add", _sess_members_add)
    registry.register("session.members.remove", _sess_members_remove)
    registry.register("session.members.listEvidence",
                      _sess_members_list_evidence)
    registry.register("session.visibility.set", _sess_visibility_set)
    # sessions-github / sessions-dispatch (OPERATOR_ONLY typed codes)
    registry.register("sessions.github.publish", _sess_github_publish)
    registry.register("sessions.github.options", _sess_github_options)
    registry.register("sessions.github.status", _sess_github_status)
    registry.register("sessions.github.confirm", _sess_github_confirm)
    registry.register("sessions.dispatch", _sess_dispatch)
    registry.register("sessions.reclaim", _sess_reclaim)
    registry.register("sessions.move", _sess_move)
    # conversations -> canonical chat-session authority (SAFE_EQUIVALENT)
    registry.register("conversations.list", _sess_conversations_list)
    registry.register("conversations.send", _sess_conversations_send)
    registry.register("conversations.turn", _sess_conversations_turn)
    registry.register("conversations.turn.cancel",
                      _sess_conversations_turn_cancel)
    # sessions-messaging / sessions-rewind (SAFE_EQUIVALENT)
    registry.register("sessions.steer", _sess_sessions_steer)
    registry.register("sessions.fork", _sess_sessions_fork)
    # usage / compaction reads (SAFE_EQUIVALENT)
    registry.register("sessions.usage.logs", _sess_sessions_usage_logs)
    registry.register("sessions.compaction.list", _sess_compaction_list)
    # session-discussion / sessions-title (SAFE_EQUIVALENT)
    registry.register("session.discussion.open", _sess_discussion_open)
    registry.register("sessions.title.prepare", _sess_sessions_title_prepare)
    # terminal registry + draft upload (IMPLEMENTED / SAFE_EQUIVALENT)
    registry.register("terminal.list", _sess_terminal_list)
    registry.register("terminal.upload", _sess_terminal_upload)
    registry.register("sessions.files.reveal", _sess_files_reveal)
    # session-observer visibility -> local share authority (SAFE_EQUIVALENT)
    registry.register("sessions.observer.visibility",
                      _sess_observer_visibility)
    # send / message.action / poll -> messaging transport (OPERATOR_ONLY)
    registry.register("send", _sess_messaging_send)
    registry.register("message.action", _sess_message_action)
    registry.register("poll", _sess_poll)
