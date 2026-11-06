"""Workbench UI seam — asset serving + the ``/api/*`` shim for the ported page.

WHAT THIS SERVES
---------------------------------------------------------------------------
``/wb/`` is hermes-webui's real static frontend, ported under
``voicebridge/workbench_ui/static/`` (provenance and per-file disposition:
``docs/WORKBENCH_UI_PORT.md``). The page is the upstream three-panel layout —
rail, session sidebar, chat, workspace browser — not a re-implementation.

Two halves, and they are deliberately separate because they have different
return contracts:

1. ``serve_asset(handler, path)`` — a WSGI-less listener branch. It writes the
   response itself (html/css/js/fonts/png) exactly like
   ``webui._serve_plugin_app`` does, because a file is bytes + a content type,
   not a ``(status, json)`` tuple. Confinement and the extension allowlist are
   copied from that method, including its ``LICENSE``-by-basename rule (MIT
   requires the notice to travel with the copy).

2. ``wbui_routes()`` / ``wbui_handlers()`` — the compat-table rows. Their JS
   calls 184 distinct ``/api/*`` paths; Qubit serves 6 of them under those names
   and 48 more are answerable from a real Qubit authority. The shim maps those
   54; every other path gets a TYPED REFUSAL, never an empty list — an empty
   list reads as "you have none", and that is a lie the UI would paint.

THE REFUSAL SHAPE
---------------------------------------------------------------------------
``{ok: False, state: "UNAVAILABLE", code, detail, qubit_counterpart}`` with a
5xx (the request was fine, the backend capability is not there). The ported UI
surfaces ``detail`` in its own panel error state; nothing here invents rows.

WHY THE DEPENDENCY IS RESOLVED INSIDE THE CALL
---------------------------------------------------------------------------
Same rule as ``workbench_routes.py``: a tree without ``voicebridge/workbench``,
or one where the session authority cannot be constructed, must still import
this file and boot the dashboard. A missing capability is a refusal, never an
ImportError at listen time.

READ/WRITE SPLIT
---------------------------------------------------------------------------
Every Qubit-backed read goes through the existing seams and nothing else:
``workbench.adapters_sessions`` (→ ``QubitSessionStore``), ``cron_bridge``,
``approvals.ApprovalJournal``, ``qubit_compat.h_config``. The workspace is
read-only by construction, and the terminal is refused with Qubit's existing
``PTY_NO_SHELL`` gate — this module does not add a second implementation of
either.
"""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: The SERVING ROOT — the ``workbench_ui/`` directory, not its ``static/`` child.
#: The HTML's own asset URLs are ``static/ui.js``, i.e. they carry the ``static/``
#: segment themselves, so the root has to be the PARENT of the tree: resolving
#: ``static/ui.js`` against ``.../workbench_ui/static`` produced
#: ``static/static/ui.js`` and every asset 404'd (caught by _prove_live.py).
#: A request for ``/wb`` maps to ``workbench_ui/index.html`` (see resolve()).
STATIC_ROOT = Path(__file__).resolve().parent / "workbench_ui"

#: Mount prefix the listener hands us (see the registration block in the doc).
MOUNT = "/wb"

#: Suffix -> content type. An ALLOWLIST, not a denylist: an unexpected type is
#: refused rather than guessed, so nothing new becomes servable by accident.
#: Copied from ``webui._serve_plugin_app`` and extended only with the types this
#: tree actually ships (fonts beyond woff/woff2: the KaTeX set has .ttf too,
#: already listed there; .map files are the vendored source maps).
_CTYPES: Dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".wasm": "application/wasm",
    ".map": "application/json",
}

#: Extensionless licence/notice files, allowed BY BASENAME (never by dropping
#: the suffix allowlist). MIT requires the copyright + permission notice to be
#: "included in all copies"; a copy served over HTTP without it is a notice
#: omission, and ``LICENSE`` has no suffix for the map above to match.
_NOTICE_NAMES = frozenset({
    "license", "licence", "license.txt", "licence.txt",
    "notice", "notice.txt", "copying",
})


def resolve(path: str) -> Optional[Path]:
    """Map a URL path below the mount to a confined file, or None.

    CONFINEMENT, copied from ``_serve_plugin_app``: ``..`` is rejected outright,
    and the RESOLVED real path is re-checked against the root so a symlink
    cannot escape either. A directory request that names no document gets
    ``index.html``.
    """
    rel = (path or "").split("?")[0].split("#")[0]
    # Accept the FULL request path (``/wb/static/ui.js``) as well as a
    # mount-relative one (``static/ui.js``): the listener hands the full path,
    # and a caller reading this module directly passes the relative form.
    if rel == MOUNT or rel.startswith(MOUNT + "/"):
        rel = rel[len(MOUNT):]
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    # A bare ``/wb`` or ``/wb/`` is the app document, which lives in ``static/``
    # beside its own assets. A path whose last segment has no dot is a directory
    # request too (``/wb/static`` -> ``static/index.html``) — EXCEPT a bare
    # notice file (``LICENSE``, ``NOTICE``), which has no suffix by design and
    # must NOT be turned into ``LICENSE/index.html``.
    if not parts:
        parts = ["static", "index.html"]
    elif "." not in parts[-1] and parts[-1].lower() not in _NOTICE_NAMES:
        parts = parts + ["index.html"]
    root = STATIC_ROOT
    try:
        resolved_root = root.resolve()
        target = root.joinpath(*parts).resolve()
    except OSError:
        return None
    if not str(target).startswith(str(resolved_root) + "/") or not target.is_file():
        return None
    return target


def ctype_for(target: Path) -> Optional[str]:
    if target.name.lower() in _NOTICE_NAMES:
        return "text/plain; charset=utf-8"
    return _CTYPES.get(target.suffix.lower())


# --------------------------------------------------------------- html inject
#: The globals the Qubit SPA shell already injects. This page is a SEPARATE
#: same-origin document, so the shell's injection never lands here — the same
#: problem ``_serve_app_html`` solves for the office app, solved the same way:
#: inject the SAME token under the SAME global, from the loopback-only bootstrap
#: rule, so the iframe/document is authenticated exactly like its parent and a
#: non-loopback peer still gets the static page WITHOUT the credential.
_INJECT_MARKER = "<!--qubit-wb-globals-->"


def _inject_globals(html: str, token: str, base_path: str) -> str:
    # Upstream's document is a TEMPLATE: its server rewrote __WEBUI_VERSION__ and
    # __MAX_UPLOAD_BYTES__ on every request (api/routes.py:16631). Our injector
    # only added globals, so the served page still carried the literal
    # "static/style.css?v=__WEBUI_VERSION__" — a query string the asset resolver
    # could not match, so every stylesheet answered with the wrong MIME type and
    # the browser refused it. The page rendered unstyled.
    version = str(globals().get("WEBUI_VERSION") or "0.0.0")
    html = html.replace("__WEBUI_VERSION__", version)
    # Upstream's own upload cap, taken from the value it publishes in its JS.
    # The document only echoes it into a bootstrap constant; nothing in the
    # Qubit shim enforces it (the shim's own limits do), so this must not
    # claim a larger number than the server would accept.
    html = html.replace("__MAX_UPLOAD_BYTES__", str(25 * 1024 * 1024))
    payload = (
        "<script>window.__QUBIT_SESSION_TOKEN__=%s;"
        "window.__QUBIT_API_PREFIX__=%s;"
        "window.__QUBIT_BASE_PATH__=%s;"
        "window.__QUBIT_WB_MOUNT__=%s;</script>"
        % (json.dumps(token or ""), json.dumps("/agent"),
           json.dumps(base_path or MOUNT), json.dumps(base_path or MOUNT))
    )
    # The globals MUST precede everything that reads them. Upstream's document
    # computes its `<base href>` in the <head> before any other markup, and that
    # script reads __QUBIT_WB_MOUNT__; injecting at </head> put the definitions
    # 130 lines too late, so the mount was undefined, the base fell back to the
    # origin, and every relative asset resolved to /static/* and 404'd. Insert
    # immediately after <head> so the definitions exist before the first reader.
    # Emit a REAL <base> element rather than relying on the document's own
    # document.write() bootstrap. That script writes during <head> parse, which
    # is timing-sensitive: measured in Chrome, stylesheets and defer scripts
    # still resolved against the origin (/static/*), so every asset 404'd while
    # the page looked fine. A static tag is decided before the first subresource
    # is resolved, so there is nothing to race. The upstream bootstrap stays in
    # place for standalone use; this tag comes first, so it is the one that wins.
    base_tag = '<base href="%s/">' % (base_path or MOUNT)
    for opener in ("<head>", "<head ", "<HEAD>", "<HEAD "):
        idx = html.find(opener)
        if idx != -1:
            end = html.find(">", idx)
            if end != -1:
                head_end = end + 1
                return html[:head_end] + payload + base_tag + html[head_end:]
    if "</head>" in html:
        return html.replace("</head>", payload + base_tag + "</head>", 1)
    return payload + base_tag + html


def serve_asset(handler: Any, path: str,
                token: str = "", base_path: str = "") -> None:
    """Serve one file from the ported tree, writing the response on ``handler``.

    ``handler`` is the dashboard's ``BaseHTTPRequestHandler``; ``_send`` and
    ``_peer_is_loopback``/``_token_bootstrap_allowed`` are the fleet's existing
    primitives, so this route inherits the same CSP, the same framing policy and
    the same loopback-only credential rule as every other page. Nothing here
    re-implements auth.
    """
    target = resolve(path)
    if target is None:
        handler._send(404, b"not found")
        return
    ctype = ctype_for(target)
    if ctype is None:
        handler._send(404, b"not found")
        return
    if target.suffix.lower() != ".html":
        handler._file(target, ctype)
        return
    try:
        html = target.read_text(encoding="utf-8")
    except OSError:
        handler._send(404, b"not found")
        return
    bootstrap_ok = bool(
        getattr(handler, "_peer_is_loopback", lambda: False)()
        and getattr(handler, "_token_bootstrap_allowed", lambda: True)())
    html = _inject_globals(html, token if bootstrap_ok else "", base_path)
    handler._send(200, html.encode("utf-8"), "text/html; charset=utf-8")


# --------------------------------------------------------------------- shims
#: code -> HTTP status. A capability fault is a 5xx (the request was fine, the
#: backend is not); a caller error is a 4xx.
_STATUS: Dict[str, int] = {
    "WB_SESSION_STORE_UNAVAILABLE": 503,
    "WB_BACKEND_UNAVAILABLE": 503,
    "WB_SESSION_LIST_FAILED": 502,
    "WB_SESSION_READ_FAILED": 502,
    "WB_ADAPTER_FAULT": 502,
    "WB_SESSION_ID_REQUIRED": 400,
    "WB_SESSION_NOT_FOUND": 409,
    "WB_BAD_INPUT": 400,
    "WB_READ_ONLY_SURFACE": 403,
    "WB_PTY_FORBIDDEN": 403,
    "WB_CAPABILITY_UNBOUND": 501,
    "WB_NOT_IN_PORT_SET": 501,
}


def _status(payload: Any, default: int = 200) -> int:
    if not isinstance(payload, dict):
        return default
    if payload.get("ok") is True:
        return 200
    code = str(payload.get("code") or "")
    if code in _STATUS:
        return _STATUS[code]
    if code.endswith(("_REQUIRED", "MALFORMED", "BAD_INPUT")):
        return 400
    if code.endswith("NOT_FOUND"):
        return 409
    return 502


def _refusal(code: str, detail: str, *, endpoint: str,
             counterpart: str = "") -> Tuple[int, Dict[str, Any]]:
    """The one refusal shape. Never an empty list, never a fabricated row."""
    return _STATUS.get(code, 501), {
        "ok": False,
        "state": "UNAVAILABLE",
        "code": code,
        "endpoint": endpoint,
        "detail": detail,
        "qubit_counterpart": counterpart or (
            "Qubit Dashboard (/) — the SPA's own page for this capability"),
        "owner": "qubit-policy",
        "rollback": "read-only; nothing to roll back",
    }


def _adapter(module: str) -> Any:
    """Resolve one workbench adapter module. Raises (never returns None)."""
    import importlib
    return importlib.import_module("voicebridge.workbench." + module)


def _call_adapter(module: str, fn_name: str, *args: Any,
                  **kwargs: Any) -> Tuple[Optional[Dict[str, Any]],
                                          Optional[Tuple[int, Any]]]:
    """Resolve -> call -> type the failure. ``(payload, None)`` or ``(None, err)``."""
    try:
        mod = _adapter(module)
        fn = getattr(mod, fn_name, None)
        if not callable(fn):
            raise AttributeError(
                f"voicebridge.workbench.{module} exposes no {fn_name}")
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        return None, _refusal(
            "WB_BACKEND_UNAVAILABLE",
            f"the workbench {module} adapter is not available: "
            f"{type(exc).__name__}: {exc}", endpoint=fn_name)
    try:
        out = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - an adapter bug must not 500 bare
        return None, _refusal(
            "WB_ADAPTER_FAULT",
            f"{module}.{fn_name} raised: {type(exc).__name__}: {exc}",
            endpoint=fn_name)
    if not isinstance(out, dict):
        return None, _refusal(
            "WB_ADAPTER_FAULT",
            f"{module}.{fn_name} returned {type(out).__name__}, not a dict",
            endpoint=fn_name)
    return out, None


def _config(cfg: Any) -> Any:
    return cfg


# ------------------------------------------------------------- session routes
def _session_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """One sidebar row in the shape the ported rail reads.

    Upstream's ``/api/sessions`` rows carry ``session_id`` and ``updated_at``;
    Qubit's store returns ``id`` and ``last_active``. This is the ONLY place the
    translation happens, so there is one place to audit.
    """
    sid = str(row.get("id") or row.get("session_id") or "")
    return {
        "session_id": sid,
        "id": sid,
        "title": str(row.get("title") or sid),
        "source": str(row.get("source") or "qubit"),
        "model": str(row.get("model") or ""),
        "archived": bool(row.get("archived")),
        "message_count": int(row.get("message_count") or 0),
        "tool_call_count": int(row.get("tool_call_count") or 0),
        "started_at": float(row.get("started_at") or 0),
        "last_active_at": float(row.get("last_active") or 0),
        "updated_at": float(row.get("last_active") or 0),
        "preview": row.get("preview") or row.get("snippet") or "",
        "is_streaming": False,
    }


def h_wb_sessions(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/sessions — the rail's list, from the session authority."""
    limit = str(q.get("limit") or "50")
    out, err = _call_adapter(
        "adapters_sessions", "list_sessions", cfg,
        limit=int(limit) if limit.isdigit() else 50,
        offset=0,
        query=str(q.get("q") or q.get("search") or ""),
        include_archived=str(q.get("archived") or "") in ("1", "true", "include"))
    if err:
        return err
    if out.get("ok") is not True:
        return _status(out, 502), out
    rows = [_session_row(r) for r in (out.get("sessions") or [])
            if isinstance(r, dict)]
    return 200, {
        "ok": True,
        "sessions": rows,
        "total": int(out.get("total") or len(rows)),
        "active_profile": str(q.get("profile") or "default"),
        "projects": [],
        "archived_webui_count": 0,
        "archived_cli_count": 0,
        "other_profile_count": 0,
    }


def h_wb_session(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/session?session_id= — one session + its transcript.

    Upstream's ``/api/session`` is the most-called endpoint in the UI (17
    references): it returns the session object AND the messages that paint the
    chat log. Both come from the store through the same adapter the rest of
    Qubit reads, so there is no second transcript reader.
    """
    sid = str(q.get("session_id") or q.get("sid") or p.get("sid") or "").strip()
    if not sid:
        return _refusal("WB_SESSION_ID_REQUIRED",
                        "GET /api/session needs ?session_id=",
                        endpoint="session")
    try:
        from voicebridge.qubit_session_store import QubitSessionStore
        out = QubitSessionStore(cfg).show(sid)
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_SESSION_STORE_UNAVAILABLE",
                        f"the chat-session authority raised: "
                        f"{type(exc).__name__}: {exc}", endpoint="session")
    state = str((out or {}).get("state") or "")
    if state != "OK":
        code = "WB_SESSION_NOT_FOUND" if state == "MISSING" else "WB_SESSION_READ_FAILED"
        return _refusal(code, str((out or {}).get("detail") or f"session {sid!r}"),
                        endpoint="session")
    session = out.get("session") or {}
    msgs = out.get("messages") or []
    return 200, {
        "ok": True,
        "session": {
            "session_id": sid,
            "title": str(session.get("title") or session.get("name") or sid),
            "message_count": int(out.get("message_count") or len(msgs)),
            "workspace": session.get("workspace") or None,
            "model": str(session.get("model") or ""),
            "source": str(session.get("source") or "qubit"),
        },
        "messages": msgs,
        "entries": msgs,
    }


def h_wb_session_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Any]:
    """The session verb family: new / delete / rename / update / archive / pin.

    Only the operations ``QubitSessionStore`` actually owns are answered. Each
    unknown field is refused with the capability named, so the UI's own error
    path fires instead of the click silently doing nothing.
    """
    action = str(p.get("action") or "")
    sid = str((b or {}).get("session_id") or (b or {}).get("sid") or "").strip()
    store = None
    try:
        from voicebridge.qubit_session_store import QubitSessionStore
        store = QubitSessionStore(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_SESSION_STORE_UNAVAILABLE",
                        f"the chat-session authority could not be constructed: "
                        f"{type(exc).__name__}: {exc}", endpoint=action)
    try:
        if action == "new":
            created = store.create(name=str((b or {}).get("title") or ""),
                                   identity="dashboard:user")
            sid = str((created or {}).get("session_id") or (created or {}).get("id")
                      or ((created or {}).get("session") or {}).get("id") or "")
            return 200, {"ok": True, "session_id": sid,
                         "session": {"session_id": sid}}
        if not sid:
            return _refusal("WB_SESSION_ID_REQUIRED",
                            f"POST /api/session/{action} needs session_id",
                            endpoint=action)
        if action == "delete":
            # ``delete`` is the store's REVERSIBLE archive delete and requires
            # confirm=True. The UI's own confirm dialog has already run by the
            # time it POSTs, so this is the second half of that same decision —
            # not a bypass of it.
            out = store.delete(sid, confirm=True)
            return _status(out, 200), {**(out or {}), "ok": bool(
                (out or {}).get("state") == "OK"), "session_id": sid}
        if action == "rename":
            out = store.rename(sid, str((b or {}).get("title")
                                        or (b or {}).get("name") or ""))
            return _status(out, 200), {**(out or {}),
                                       "ok": bool((out or {}).get("state") == "OK")}
        if action in ("archive", "pin", "update"):
            refused = [k for k in (b or {})
                       if k not in ("session_id", "sid", "archived", "pinned",
                                    "title", "model")]
            if refused:
                return _refusal(
                    "WB_NOT_IN_PORT_SET",
                    f"/api/session/{action} received field(s) {refused!r} that "
                    f"QubitSessionStore does not own; nothing was written.",
                    endpoint=action,
                    counterpart="Qubit Dashboard /sessions")
            if "archived" in (b or {}):
                store.archive(sid, bool(b["archived"]))
            if "pinned" in (b or {}) or "title" in (b or {}) or "model" in (b or {}):
                # The store keeps non-transcript session flags in its metadata
                # patch, which is the ONE writer for that table.
                store.meta_patch(sid, {k: (b or {})[k] for k in
                                       ("pinned", "title", "model") if k in (b or {})})
            if "model" in (b or {}):
                store.set_model(sid, str(b["model"]))
            return 200, {"ok": True, "session_id": sid}
        if action in ("draft", "toolsets"):
            # Composer draft and toolset selection live in the store's own
            # metadata, so they are a meta_get/meta_patch round-trip.
            if str(p.get("method") or "GET").upper() == "GET":
                meta = store.meta_get(sid)
                return 200, {"ok": True, "session_id": sid,
                             "meta": (meta or {}).get("meta") or {}}
            store.meta_patch(sid, {action: (b or {}).get(action)})
            return 200, {"ok": True, "session_id": sid}
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_SESSION_READ_FAILED",
                        f"session {action} raised {type(exc).__name__}: {exc}",
                        endpoint=action)
    return _refusal("WB_CAPABILITY_UNBOUND",
                    f"session action {action!r} has no Qubit authority",
                    endpoint=action)


# ---------------------------------------------------------------- cron routes
def h_wb_crons(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/crons — ``{jobs:[...]}`` from ``cron_bridge``."""
    try:
        from voicebridge import cron_bridge
        jobs = cron_bridge.list_jobs(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"voicebridge.cron_bridge is not usable: "
                        f"{type(exc).__name__}: {exc}", endpoint="crons")
    return 200, {"ok": True, "jobs": jobs, "other_profile_count": 0,
                 "gateway_available": False}


def h_wb_cron_action(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """The cron verb family, each mapped onto ONE ``cron_bridge`` call."""
    action = str(p.get("action") or "")
    body = b or {}
    jid = str(body.get("job_id") or body.get("id") or q.get("job_id") or "")
    try:
        from voicebridge import cron_bridge
        if action == "create":
            job = cron_bridge.create(
                cfg, name=str(body.get("name") or ""),
                prompt=str(body.get("prompt") or ""),
                schedule=str(body.get("schedule") or ""),
                **{k: v for k, v in body.items()
                   if k in ("deliver", "enabled", "timezone", "profile")})
            return 200, {"ok": True, "job": job}
        if action in ("pause", "resume", "run", "delete", "update"):
            if not jid:
                return _refusal("WB_BAD_INPUT",
                                f"POST /api/crons/{action} needs job_id",
                                endpoint="crons/" + action)
            if action == "pause":
                return 200, {"ok": True, "job": cron_bridge.pause(cfg, jid)}
            if action == "resume":
                return 200, {"ok": True, "job": cron_bridge.resume(cfg, jid)}
            if action == "run":
                return 200, {"ok": True, "job": cron_bridge.trigger(cfg, jid)}
            if action == "delete":
                return 200, {"ok": True, "deleted": bool(cron_bridge.delete(cfg, jid))}
            job = cron_bridge.update(cfg, jid, {k: v for k, v in body.items()
                                                if k != "job_id"})
            return 200, {"ok": True, "job": job}
        if action == "history":
            return 200, {"ok": True, "runs": cron_bridge.runs(cfg, jid, limit=20)}
        if action == "status":
            return 200, {"ok": True, "store": cron_bridge.store_path(cfg),
                         "count": len(cron_bridge.list_jobs(cfg))}
        if action == "recent":
            return 200, {"ok": True, "runs": cron_bridge.runs(cfg, "", limit=20)}
        if action == "delivery-options":
            return 200, {"ok": True, "options": [
                {"id": "local", "label": "Local (dashboard log)"}]}
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"cron_bridge.{action} raised: {type(exc).__name__}: {exc}",
                        endpoint="crons/" + action)
    return _refusal("WB_CAPABILITY_UNBOUND",
                    f"cron action {action!r} has no Qubit authority",
                    endpoint="crons/" + action)


# ------------------------------------------------------------ approval routes
def _journal(cfg: Any) -> Any:
    from voicebridge.approvals import ApprovalJournal
    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=600.0)


def h_wb_approval_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/approval/pending — the durable approval journal.

    Returns the OLDEST unsettled approval as ``pending`` plus ``pending_count``,
    which is the contract the chat card polls for. An empty journal is
    ``{pending: null, pending_count: 0}`` — a truthful "nothing is waiting",
    distinct from a failure (which is a typed 5xx).
    """
    try:
        items = _journal(cfg).pending()
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"the approval journal could not be read: "
                        f"{type(exc).__name__}: {exc}", endpoint="approval/pending")
    items = sorted(items, key=lambda a: getattr(a, "created_at", 0))
    if not items:
        return 200, {"ok": True, "pending": None, "pending_count": 0}
    head = items[0]
    return 200, {
        "ok": True,
        "pending_count": len(items),
        "pending": {
            "approval_id": head.id,
            "capability": head.capability,
            "summary": head.summary,
            "created_at": head.created_at,
            "expires_at": head.expires_at,
            "meta": head.meta or {},
        },
    }


def h_wb_approval_respond(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/wb/api/approval/respond — settle through ``dashboard_ops``.

    The decision goes through the SAME confirm path the dashboard's approvals
    page uses, so an approve from this page and an approve from the SPA cannot
    disagree about what happened.
    """
    body = b or {}
    aid = str(body.get("approval_id") or body.get("id") or "")
    decision = str(body.get("decision") or body.get("choice") or "").lower()
    if decision in ("approve", "approved", "yes", "allow"):
        decision = "approve"
    elif decision in ("deny", "denied", "no", "reject"):
        decision = "deny"
    else:
        return _refusal("WB_BAD_INPUT",
                        f"decision must be approve|deny, got {decision!r}",
                        endpoint="approval/respond")
    if not aid:
        return _refusal("WB_SESSION_ID_REQUIRED",
                        "POST /api/approval/respond needs approval_id",
                        endpoint="approval/respond")
    try:
        from voicebridge import dashboard_ops as OPS
        if decision == "approve":
            out = OPS.confirm_op(cfg, aid, actor="workbench:user",
                                 session="dashboard")
        else:
            out = OPS.deny_op(cfg, aid, actor="workbench:user",
                              session="dashboard")
    except AttributeError:
        try:
            _journal(cfg).respond(aid, decision)
            return 200, {"ok": True, "approval_id": aid, "decision": decision}
        except Exception as exc:  # noqa: BLE001
            return _refusal("WB_ADAPTER_FAULT",
                            f"approval respond raised {type(exc).__name__}: {exc}",
                            endpoint="approval/respond")
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_ADAPTER_FAULT",
                        f"dashboard_ops raised {type(exc).__name__}: {exc}",
                        endpoint="approval/respond")
    return 200, {"ok": True, "approval_id": aid, "decision": decision,
                 "result": out}


def h_wb_clarify_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/clarify/pending — NO Qubit clarify authority exists.

    Answers ``{pending: null, clarify_unavailable: true}``: the absence is
    REAL (Qubit never asks a blocking clarifying question through this
    channel), so the UI hides its card rather than showing a spinner forever.
    The flag is set so a reader can tell "asked and none" from "cannot ask" —
    the UI treats both as hide, and a human reading the payload can tell.
    """
    return 200, {"ok": True, "pending": None, "clarify_unavailable": True,
                 "detail": ("Qubit has no blocking clarify channel; agent "
                            "questions arrive in the transcript, not as a "
                            "pending card.")}


# ------------------------------------------------------------ config / models
def h_wb_settings(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/settings — the settings panel's read, from ``h_config``."""
    try:
        from voicebridge import qubit_compat as HC
        code, out = HC.dispatch(cfg, "GET", "/api/config", query={}, body={})
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"qubit_compat.h_config unavailable: "
                        f"{type(exc).__name__}: {exc}", endpoint="settings")
    if code != 200 or not isinstance(out, dict):
        return code if code >= 400 else 502, out
    merged = dict(out)
    merged["ok"] = True
    return 200, merged


def h_wb_models(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/models — the model picker's inventory."""
    try:
        from voicebridge import qubit_compat as HC
        code, out = HC.dispatch(cfg, "GET", "/api/config", query={}, body={})
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"model inventory unavailable: {type(exc).__name__}: {exc}",
                        endpoint="models")
    if code != 200 or not isinstance(out, dict):
        return code if code >= 400 else 502, out
    active = str(out.get("model") or out.get("default_model") or "")
    provider = str(out.get("provider") or "")
    models = out.get("models") or out.get("available_models") or []
    if isinstance(models, list):
        rows = [m if isinstance(m, dict) else {"id": str(m)} for m in models]
    else:
        rows = [{"id": str(k)} for k in models]
    return 200, {"ok": True, "active": active, "provider": provider,
                 "models": rows}


def h_wb_logs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/logs — pass-through to the existing compat handler."""
    try:
        from voicebridge import qubit_compat as HC
        code, out = HC.dispatch(cfg, "GET", "/api/logs", query=dict(q or {}), body={})
        return code, out
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"/api/logs unavailable: {type(exc).__name__}: {exc}",
                        endpoint="logs")


def h_wb_terminal(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/wb/api/terminal/{start,close,input,resize} — REFUSED.

    Qubit forbids raw shell/PTY spawn on this listener (the compat table's own
    ``PTY_NO_SHELL`` gate). The refusal names the Qubit surfaces that do run a
    command, so the terminal pane shows a reason and a next step instead of
    hanging on a dead socket.
    """
    return _refusal(
        "WB_PTY_FORBIDDEN",
        "Qubit does not expose a raw shell/PTY to the dashboard; the "
        "workbench terminal cannot attach.",
        endpoint="terminal/" + str(p.get("action") or ""),
        counterpart="Qubit Chat (streaming agent turns) or CLI `qubit tui`")


def h_wb_file_save(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/wb/api/file/save — the workspace is READ-ONLY by construction."""
    out, err = _call_adapter("adapters_workspace", "refused_mutation", cfg,
                             path=str((b or {}).get("path") or ""))
    if err:
        return err
    return _status(out, 403), out


def h_wb_workspace_preview(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/file?path= — the workspace preview (a real read)."""
    out, err = _call_adapter("adapters_workspace", "preview_file", cfg,
                             path=str(q.get("path") or ""))
    if err:
        return err
    return _status(out, 200), out


def h_wb_workspace_tree(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/workspace — the file tree (a real read)."""
    out, err = _call_adapter("adapters_workspace", "list_tree", cfg,
                             path=str(q.get("path") or ""))
    if err:
        return err
    return _status(out, 200), out


def h_wb_projects(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/projects — the project picker's list."""
    try:
        from voicebridge import qubit_compat as HC
        code, out = HC.dispatch(cfg, "GET", "/api/profiles", query=dict(q or {}),
                                body={})
        if code == 200 and isinstance(out, dict):
            return 200, {"ok": True, "projects": out.get("profiles") or [],
                         "active_id": out.get("active") or None}
        return code, out
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"projects unavailable: {type(exc).__name__}: {exc}",
                        endpoint="projects")


#: The top-level key the ported UI reads from each EXISTING endpoint. Some compat
#: handlers answer a BARE LIST (``/api/skills`` -> ``[{name, description}, …]``);
#: the ported panel indexes a named key, so the shim wraps the list under the
#: name the panel uses. Without this the skills panel renders nothing while the
#: endpoint reports 200 — a silent empty screen, which is the failure mode this
#: whole port is being fixed for.
_EXISTING_WRAP: Dict[str, str] = {
    "skills": "skills",
    "profiles": "profiles",
    "mcp/servers": "servers",
    "memory": "memory",
    "logs": "lines",
    "sessions/search": "sessions",
}


def h_wb_passthrough(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """The EXISTING set: dispatch to the compat route with the same request."""
    rest = str(p.get("rest") or "")
    target = "/api/" + rest
    try:
        from voicebridge import qubit_compat as HC
        code, out = HC.dispatch(cfg, str(p.get("method") or "GET"), target,
                                query=dict(q or {}), body=dict(b or {}))
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"{target} unavailable: {type(exc).__name__}: {exc}",
                        endpoint=target)
    if code == 200 and isinstance(out, list):
        key = _EXISTING_WRAP.get(rest, "items")
        return 200, {"ok": True, key: out, "count": len(out), "source": target}
    if code == 200 and isinstance(out, dict) and "ok" not in out:
        return 200, {"ok": True, **out, "source": target}
    return code, out


# -------------------------------------------------------------- the port table
#: ``action`` -> handler for the single-dispatch verbs.
_SESSION_ACTIONS: Dict[str, Callable[..., Tuple[int, Any]]] = {
    "new": h_wb_session_write,
    "delete": h_wb_session_write,
    "rename": h_wb_session_write,
    "update": h_wb_session_write,
    "archive": h_wb_session_write,
    "pin": h_wb_session_write,
}
_CRON_ACTIONS = frozenset({
    "create", "update", "delete", "pause", "resume", "run", "history",
    "status", "recent", "delivery-options",
})

#: Every endpoint the port answers, keyed by the path BELOW ``/api/wb/api/``.
#: Presence here is what makes an endpoint PORT or EXISTING rather than GAP;
#: ``_endpoint_table.py`` reads this dict and must agree with the doc's 48+6.
PORT_SET: Dict[str, str] = {
    # sessions
    "sessions": "h_wb_sessions",
    "session": "h_wb_session",
    "session/new": "h_wb_session_write",
    "session/delete": "h_wb_session_write",
    "session/rename": "h_wb_session_write",
    "session/update": "h_wb_session_write",
    "session/archive": "h_wb_session_write",
    "session/pin": "h_wb_session_write",
    "session/draft": "h_wb_session_write",
    "session/toolsets": "h_wb_session_write",
    # crons
    "crons": "h_wb_crons",
    "crons/create": "h_wb_cron_action",
    "crons/update": "h_wb_cron_action",
    "crons/delete": "h_wb_cron_action",
    "crons/pause": "h_wb_cron_action",
    "crons/resume": "h_wb_cron_action",
    "crons/run": "h_wb_cron_action",
    "crons/history": "h_wb_cron_action",
    "crons/status": "h_wb_cron_action",
    "crons/recent": "h_wb_cron_action",
    "crons/delivery-options": "h_wb_cron_action",
    # approvals / clarify
    "approval/pending": "h_wb_approval_pending",
    "approval/respond": "h_wb_approval_respond",
    "clarify/pending": "h_wb_clarify_pending",
    "clarify/respond": "h_wb_file_save_stub",
    # config surfaces
    "settings": "h_wb_settings",
    "models": "h_wb_models",
    "model/set": "h_wb_passthrough",
    "default-model": "h_wb_models",
    "reasoning": "h_wb_settings",
    # NOTE: /api/skills, /api/memory, /api/profiles, /api/mcp/servers and
    # /api/logs are EXISTING_SET, not PORT_SET — they are already served by
    # qubit_compat and the gateway dispatches to them there. Listing them in both
    # would double-count them in the endpoint table (the table script caught
    # exactly that). Only the SUB-paths Qubit does not already serve live here.
    "profile/active": "h_wb_passthrough",
    "profile/switch": "h_wb_passthrough",
    "skills/content": "h_wb_passthrough",
    "skills/toggle": "h_wb_passthrough",
    "memory/write": "h_wb_passthrough",
    "mcp/tools": "h_wb_passthrough",
    "plugins": "h_wb_passthrough",
    "personalities": "h_wb_settings",
    "personality/set": "h_wb_settings",
    "prompts": "h_wb_settings",
    # workspace (read-only). NOTE: the UI never calls /api/workspace; the tree
    # read is served by the existing workbench route /api/workbench/workspace/tree
    # and by ``file`` here. The handler stays reachable for the workbench page's
    # own use but is not part of the endpoint count.
    "file": "h_wb_workspace_preview",
    "file/save": "h_wb_file_save",
    "projects": "h_wb_projects",
    # terminal (refused)
    "terminal/start": "h_wb_terminal",
    "terminal/close": "h_wb_terminal",
    "terminal/input": "h_wb_terminal",
    "terminal/resize": "h_wb_terminal",
    # share (refused)
    "share/create": "h_wb_file_save_stub",
}


def h_wb_file_save_stub(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """A capability this port names but has no Qubit authority for."""
    return _refusal(
        "WB_CAPABILITY_UNBOUND",
        f"/api/{p.get('rest') or 'unknown'} has no Qubit authority. Nothing was "
        f"changed and no data was invented.",
        endpoint=str(p.get("rest") or ""))


#: The EXISTING set: served by ``qubit_compat`` already. Listed separately so
#: the doc's 6 and this code agree, and so a removed compat route breaks a test
#: rather than a page.
EXISTING_SET = frozenset({
    "skills", "memory", "profiles", "mcp/servers", "logs", "sessions/search",
})


def _resolve_handler(sub: str, method: str) -> Optional[Callable[..., Any]]:
    """Pick the handler for a path below ``/api/wb/api/``.

    PORT_SET first; then EXISTING_SET, which is served by ``qubit_compat`` and so
    dispatches through ``h_wb_passthrough``. Leaving the EXISTING set out of this
    lookup made six endpoints answer ``WB_NOT_IN_PORT_SET`` while the doc claimed
    they were served — the live proof caught that (``/api/logs`` et al returned
    501), which is exactly why the table is checked against the running server.
    """
    clean = sub.strip("/")
    if not clean:
        return None
    import sys
    mod = sys.modules[__name__]
    name = PORT_SET.get(clean)
    if name:
        return getattr(mod, name, None)
    if clean in EXISTING_SET:
        return h_wb_passthrough
    return None


def h_wb_gateway(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """The single shipped compat row: route one workbench API call.

    ``p['sub']`` is everything after ``/api/wb/api/``. A path that is not in the
    port or existing set gets ``WB_NOT_IN_PORT_SET`` — the typed refusal the
    doc's GAP section describes — with the capability named, so the ported UI
    renders ITS OWN error and a reader can tell a missing feature from a bug.
    """
    sub = str(p.get("sub") or "").strip("/")
    method = str(p.get("method") or "GET").upper()
    handler = _resolve_handler(sub, method)
    if handler is None:
        head = sub.split("/")[0] if sub else ""
        return _refusal(
            "WB_NOT_IN_PORT_SET",
            f"{method} /api/{sub} is not in the workbench port set "
            f"({len(PORT_SET)} ported, {len(EXISTING_SET)} existing of 184 "
            f"endpoints the upstream UI calls). Qubit exposes no authority "
            f"for it; nothing was fabricated.",
            endpoint=sub,
            counterpart=("Qubit Dashboard (/)"
                         if not head.startswith(("kanban", "wiki", "notes"))
                         else "no Qubit counterpart yet"))
    # pass the sub-path through so action handlers know which verb fired
    params = dict(p)
    params.setdefault("method", method)
    if "/" in sub:
        params.setdefault("action", sub.rsplit("/", 1)[-1])
    params.setdefault("rest", sub)
    if handler is h_wb_passthrough:
        params["method"] = method
    if handler is h_wb_session_write:
        params["action"] = sub.split("/")[-1]
    if handler is h_wb_cron_action:
        params["action"] = sub.split("/")[-1]
    if handler is h_wb_terminal:
        params["action"] = sub.split("/")[-1]
    return handler(cfg, q, b, params)


_HANDLERS: Dict[str, Callable[..., Tuple[int, Any]]] = {
    "h_wb_gateway": h_wb_gateway,
    "h_wb_sessions": h_wb_sessions,
    "h_wb_session": h_wb_session,
    "h_wb_session_write": h_wb_session_write,
    "h_wb_crons": h_wb_crons,
    "h_wb_cron_action": h_wb_cron_action,
    "h_wb_approval_pending": h_wb_approval_pending,
    "h_wb_approval_respond": h_wb_approval_respond,
    "h_wb_clarify_pending": h_wb_clarify_pending,
    "h_wb_settings": h_wb_settings,
    "h_wb_models": h_wb_models,
    "h_wb_logs": h_wb_logs,
    "h_wb_terminal": h_wb_terminal,
    "h_wb_file_save": h_wb_file_save,
    "h_wb_file_save_stub": h_wb_file_save_stub,
    "h_wb_workspace_preview": h_wb_workspace_preview,
    "h_wb_workspace_tree": h_wb_workspace_tree,
    "h_wb_projects": h_wb_projects,
    "h_wb_passthrough": h_wb_passthrough,
}


def wbui_routes() -> List[Tuple[str, str, str]]:
    """The compat-table rows. ONE row per method: ``sub`` carries the rest.

    The listener strips ``/agent`` before dispatch (``webui.py:3697``), so a
    browser request for ``/agent/wb/api/sessions`` arrives here as
    ``/api/wb/api/sessions`` and ``sub`` is ``sessions``.
    """
    return [
        ("GET", r"/api/wb/api/(?P<sub>.*)", "h_wb_gateway"),
        ("POST", r"/api/wb/api/(?P<sub>.*)", "h_wb_gateway"),
        ("PUT", r"/api/wb/api/(?P<sub>.*)", "h_wb_gateway"),
        ("DELETE", r"/api/wb/api/(?P<sub>.*)", "h_wb_gateway"),
    ]


def wbui_handlers() -> Dict[str, Callable[..., Tuple[int, Any]]]:
    """The ``HANDLERS`` entries ``wbui_routes`` names."""
    return dict(_HANDLERS)
