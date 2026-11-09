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
   and 78 more are answerable from a real Qubit authority. The shim maps those
   84; every other path gets a TYPED REFUSAL, never an empty list — an empty
   list reads as "you have none", and that is a lie the UI would paint. The
   count is MEASURED by ``workbench_ui/_endpoint_table.py``, not asserted here.

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
read-only by construction, and the terminal NEVER spawns a shell: it is the
agent-backed session in ``workbench_ui/terminal_family.py`` behind upstream's
terminal wire (``backend:"agent"``, ``shell:false``), so ``PTY_NO_SHELL`` still
holds for every process-spawning path. This module adds a second implementation
of neither.
"""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

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
    "WB_NO_MODEL_INVENTORY": 503,
    "WB_NOT_IN_PORT_SET": 501,
    "WB_EGRESS_BLOCKED": 403,
    # The config/extension/auth family (workbench_ui/handlers_config.py).
    "WB_AUTH_MODEL_UNSUPPORTED": 501,
    "WB_CREDENTIALS_NOT_IN_CONFIG": 403,
    "WB_BROKERED_INSTALL": 403,
    "WB_PLUGIN_ADMISSION_REFUSED": 409,
    "WB_NOT_FOUND": 404,
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


# ------------------------------------------------- ported-client call set
# Implemented in ``voicebridge/workbench_ui/handlers_extra.py`` (one readable
# file for the paths the ported page actually calls) and re-exported here so the
# route table and the handlers stay one audit surface, as every other family is.
from .workbench_ui.handlers_extra import (chat_start as _x_chat_start,
                                          chat_steer as _x_chat_steer,
                                          chat_stop as _x_chat_stop,
                                          chat_stream_status as _x_chat_status,
                                          session_verb as _x_session_verb,
                                          session_stream as _x_session_stream,
                                          session_flag as _x_session_flag,
                                          sessions_events as _x_sessions_events,
                                          sessions_gateway_probe as _x_gateway_probe,
                                          approval_pending as _x_approval_pending,
                                          approval_respond as _x_approval_respond,
                                          clarify_pending as _x_clarify_pending,
                                          clarify_respond as _x_clarify_respond,
                                          workspace_list as _x_workspace_list,
                                          workspaces as _x_workspaces,
                                          profile_active as _x_profile_active,
                                          profile_switch as _x_profile_switch,
                                          health_agent as _x_health_agent,
                                          dashboard_config as _x_dash_config,
                                          dashboard_status as _x_dash_status,
                                          onboarding_status as _x_onboard,
                                          client_events_log as _x_client_log,
                                          transcribe_capability as _x_transcribe,
                                          updates_check as _x_updates_check)

# The operational-settings call set — settings/models/providers/reasoning,
# extensions/*, tools, and the auth-model refusal. Its own module because these
# handlers carry a sharp write-authority story (one writer per family) and the
# auth refusals need to be readable in isolation from the chat shims.
from .workbench_ui.handlers_config import (
    auth_status as _c_auth_status,
    auth_unsupported as _c_auth_unsupported,
    default_model as _c_default_model,
    extensions_install as _c_ext_install,
    extensions_registry as _c_ext_registry,
    extensions_sidecar_consent as _c_ext_consent,
    extensions_status as _c_ext_status,
    extensions_toggle as _c_ext_toggle,
    providers as _c_providers,
    reasoning as _c_reasoning,
    settings as _c_settings,
    tools as _c_tools)



def chat_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_chat_start(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def chat_steer(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_chat_steer(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def chat_stop(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_chat_stop(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                        dict(p or {}))


def chat_stream_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_chat_status(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                          dict(p or {}))


def session_verb(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/session/<verb>`` — the verb is the tail after ``session/``."""
    params = dict(p or {})
    sub = str(params.get("sub") or "").strip("/")
    params["action"] = (sub[len("session/"):] if sub.startswith("session/")
                        else sub.rsplit("/", 1)[-1])
    return _x_session_verb(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                           params)


def session_stream(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_session_stream(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                             dict(p or {}))


def session_flag(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_session_flag(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                           dict(p or {}))


def sessions_events(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_sessions_events(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                              dict(p or {}))


def approval_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_approval_pending(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                               dict(p or {}))


def approval_respond(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_approval_respond(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                               dict(p or {}))


def clarify_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_clarify_pending(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                              dict(p or {}))


def clarify_respond(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_clarify_respond(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                              dict(p or {}))


def workspace_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_workspace_list(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                             dict(p or {}))


def workspaces(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_workspaces(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def profile_active(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_profile_active(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                             dict(p or {}))


def profile_switch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_profile_switch(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                             dict(p or {}))


def sessions_gateway_probe(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_gateway_probe(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                            dict(p or {}))


def health_agent(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_health_agent(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                           dict(p or {}))


def dashboard_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_dash_config(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                          dict(p or {}))


def dashboard_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_dash_status(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                          dict(p or {}))


def onboarding_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_onboard(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                      dict(p or {}))


def client_events_log(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_client_log(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def transcribe_capability(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_transcribe(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def updates_check(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    return _x_updates_check(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                            dict(p or {}))


# ------------------------------------- operational settings / extensions / auth
# Each wrapper pins the METHOD and the sub-path so the config handlers can branch
# on a write without re-deriving either. ``p['method']`` is set by the gateway;
# ``p['sub']`` is the full path below ``/api/wb/api/`` and is what the auth
# refusal quotes back.
def wb_settings(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/settings`` — GET the flat panel record; POST a narrow write."""
    return _c_settings(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                       dict(p or {}))


def wb_reasoning(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/reasoning`` — the effort chip's read and its two-key write."""
    return _c_reasoning(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                        dict(p or {}))


def wb_providers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/providers`` (+ ``/delete``) — list; every write is refused."""
    return _c_providers(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                        dict(p or {}))


def wb_default_model(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/default-model`` — read the default; POST applies via select_model."""
    return _c_default_model(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                            dict(p or {}))


def wb_tools(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/tools`` and ``/api/mcp/tools`` — the MCP tool inventory."""
    return _c_tools(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                    dict(p or {}))


def wb_extensions_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Any]:
    return _c_ext_status(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def wb_extensions_registry(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    return _c_ext_registry(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                           dict(p or {}))


def wb_extensions_toggle(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Any]:
    return _c_ext_toggle(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def wb_extensions_install(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    return _c_ext_install(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                          dict(p or {}))


def wb_extensions_sidecar_consent(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Any]:
    return _c_ext_consent(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                          dict(p or {}))


def wb_auth_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/auth/status`` — Qubit's real auth model, described truthfully."""
    return _c_auth_status(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                          dict(p or {}))


def wb_auth_unsupported(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/auth/{passkeys,passkey/*}`` — refused, naming the missing authority."""
    params = dict(p or {})
    if not params.get("sub"):
        params["sub"] = "auth"
    return _c_auth_unsupported(cfg, dict(q or {}),
                               b if isinstance(b, dict) else {}, params)


def wb_auth_logout(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/auth/logout`` — the REAL Qubit authority, not a refusal.

    ``qubit_compat.h_auth_logout`` already implements this exact path: it revokes
    the loopback session tokens and answers ``next: /login``. Routing here keeps
    the one implementation instead of adding a second logout beside it.
    """
    try:
        from voicebridge import qubit_compat as HC
        code, out = HC.dispatch(cfg, "POST", "/api/auth/logout",
                                query=dict(q or {}),
                                body=b if isinstance(b, dict) else {})
        return code, out
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"/api/auth/logout unavailable: "
                        f"{type(exc).__name__}: {exc}", endpoint="auth/logout")


# ------------------- settings / onboarding / updates (handlers_settings.py)
# One thin wrapper per handler, for the same reason every other family has them:
# the gateway resolves handlers BY NAME, so a path whose name is missing from
# ``_HANDLERS`` answers ``WB_NOT_IN_PORT_SET`` even though the code exists.
def _s_settings(fn_name: str, cfg: Any, q: Any, b: Any, p: Any
                ) -> Tuple[int, Any]:
    """Delegate one settings/onboarding/updates request to its owner module."""
    from .workbench_ui import handlers_settings as HS

    fn = getattr(HS, fn_name, None)
    if fn is None:  # pragma: no cover - a renamed handler is a build error
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"the settings family has no handler {fn_name!r}",
                        endpoint="settings")
    return fn(cfg, dict(q or {}), b if isinstance(b, dict) else {}, dict(p or {}))


def wb_model_auxiliary(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/model/auxiliary`` — the auxiliary-task pins (no secrets)."""
    return _s_settings("model_auxiliary", cfg, q, b, p)


def wb_providers_self_hosted(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/providers/self-hosted`` — refused (Qubit stores key references)."""
    return _s_settings("providers_self_hosted", cfg, q, b, p)


def wb_provider_cost_history(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/provider/cost-history`` — Qubit's own recorded spend per day."""
    return _s_settings("provider_cost_history", cfg, q, b, p)


def wb_provider_quota(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/provider/quota`` — refused: no quota state is recorded."""
    return _s_settings("provider_quota", cfg, q, b, p)


def wb_models_refresh(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/models/refresh`` — refused: needs the provider's credential."""
    return _s_settings("models_refresh", cfg, q, b, p)


def wb_onboarding_probe(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/onboarding/probe`` — bounded probe of a self-hosted endpoint."""
    return _s_settings("onboarding_probe", cfg, q, b, p)


def wb_onboarding_setup(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/onboarding/setup`` — provider/model through ``select_model``."""
    return _s_settings("onboarding_setup", cfg, q, b, p)


def wb_onboarding_complete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/onboarding/complete`` — acknowledge setup, or refuse."""
    return _s_settings("onboarding_complete", cfg, q, b, p)


def wb_onboarding_oauth_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/onboarding/oauth/start`` — the ONE device-code flow authority."""
    return _s_settings("onboarding_oauth_start", cfg, q, b, p)


def wb_onboarding_oauth_poll(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/onboarding/oauth/poll`` — one status reading of a live flow."""
    return _s_settings("onboarding_oauth_poll", cfg, q, b, p)


def wb_onboarding_oauth_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                               p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/onboarding/oauth/cancel`` — marks the session cancelled."""
    return _s_settings("onboarding_oauth_cancel", cfg, q, b, p)


def wb_system_health(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/system/health`` — host CPU/memory/disk for the insights panel."""
    return _s_settings("system_health", cfg, q, b, p)


def wb_health_restart_bridge(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/health/restart`` and ``POST /api/gateway/{start,stop,restart}``.

    ONE refusal for every way the client asks to bounce the messaging daemon: the
    banner button (``ui.js:14363``) and the Gateway card's lifecycle controls
    (``panels.js:14642`` builds ``/api/gateway/<action>``). The action is only
    used to name the request back — it never selects a different behaviour,
    because none of them is this listener's to perform.
    """
    sub = str((p or {}).get("sub") or "").rstrip("/")
    action = sub.rsplit("/", 1)[-1] if "/" in sub else "restart"
    if action not in ("start", "stop", "restart"):
        action = "restart"
    code, out = _s_settings("health_restart_bridge", cfg, q, b, p)
    if isinstance(out, dict):
        # Re-point the refusal at the action the caller actually asked for, so
        # the panel's toast quotes the right verb.
        out = {**out, "endpoint": f"gateway/{action}",
               "qubit_counterpart": f"CLI `qubit gateway {action}` (asks for "
                                    f"confirmation), or Qubit Dashboard → System"}
    return code, out


def wb_gateway_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/gateway/status`` — the messaging daemon's real state."""
    return _s_settings("gateway_status", cfg, q, b, p)


def wb_git_info(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/git-info`` — the workspace badge's confined repo facts."""
    return _s_settings("git_info", cfg, q, b, p)


def wb_updates_check_q(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/updates/check`` — Qubit's OWN update source, mapped."""
    return _s_settings("updates_check_q", cfg, q, b, p)


def wb_updates_apply_bridge(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/updates/{apply,force,clear_lock,summary}`` — refused, named."""
    return _s_settings("updates_apply_bridge", cfg, q, b, p)



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
    payload = {
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
    # Upstream nests the transcript INSIDE session (api/routes.py:17326), and
    # its client reads d.session.messages — not the top-level array. Serving
    # only the top level made the pane fetch a correct 2-message transcript and
    # then render an empty state: the count came from session.message_count
    # (right) while the rows came from session.messages (absent). Both shapes
    # are emitted so neither old nor new client code loses the transcript.
    payload["session"]["messages"] = msgs
    return 200, payload


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


def _wb_model_rows(config: Any) -> List[Tuple[str, str]]:
    """Flatten ``providers`` from an ``/api/config``-shaped dict.

    Returns ``(provider, model_id)`` pairs in provider-then-id order, deduped on
    the pair (a model listed twice under one provider appears once) and then on
    the bare id (the picker's value is the id, so the same id under two
    providers is one selectable row — the first provider wins and keeps its
    label). Every provider contributes its ``default_model`` plus every key of
    its ``models`` dict; nothing is invented and no id is normalized.
    """
    if not isinstance(config, dict):
        return []
    provs = config.get("providers")
    if not isinstance(provs, dict):
        return []
    pairs: List[Tuple[str, str]] = []
    seen_pairs = set()
    for pname in sorted(provs):
        pcfg = provs.get(pname)
        if not isinstance(pcfg, dict):
            continue
        name = str(pname)
        candidates: List[str] = []
        default_model = pcfg.get("default_model")
        if isinstance(default_model, str) and default_model.strip():
            candidates.append(default_model.strip())
        ids = pcfg.get("models")
        if isinstance(ids, dict):
            candidates.extend(str(k).strip() for k in ids)
        elif isinstance(ids, (list, tuple)):
            candidates.extend(str(m).strip() for m in ids
                              if isinstance(m, str))
        for mid in candidates:
            if not mid:
                continue
            if (name, mid) in seen_pairs:
                continue
            seen_pairs.add((name, mid))
            pairs.append((name, mid))
    return pairs


def h_wb_models(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/wb/api/models — the model picker's inventory.

    An empty picker is not cosmetic: the ported client then falls back to
    upstream's HARDCODED names (``GPT-5.4 Mini``), sends one to
    ``/api/chat/start``, and the turn dies inside the transport with
    CIRCUIT_OPEN while the pane shows a blank "Processed" row. So this NEVER
    answers ``models: []`` as success — an empty inventory is a typed refusal.

    The inventory lives in the fork's ``/api/config`` ``providers`` tree (a
    dict of provider -> ``{default_model, models}``). Qubit's own compat
    ``h_config`` cannot serve it: ``config.DEFAULTS`` has no ``providers`` key,
    and with ``cfg=None`` it degrades to the voice-only subset. Read the same
    record the dashboard reads — the vendored runtime, in-process.
    """
    config: Any = None
    source = ""
    try:
        from voicebridge import qubit_asgi as HA
        code, _hdrs, body = HA.call("GET", "/api/config", query={},
                                    headers={"accept": "application/json"})
        if 200 <= int(code) < 300:
            try:
                config = json.loads(bytes(body).decode("utf-8"))
                source = "qubit_asgi:/api/config"
            except (ValueError, UnicodeDecodeError):
                config = None
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_BACKEND_UNAVAILABLE",
                        f"model inventory unavailable: {type(exc).__name__}: {exc}",
                        endpoint="models")
    pairs = _wb_model_rows(config)
    if not pairs:
        return _refusal(
            "WB_NO_MODEL_INVENTORY",
            "no model inventory is configured: /api/config carries no "
            "providers with a default_model or a models list, so the picker "
            "has nothing real to offer rather than a list it would have to "
            "invent",
            endpoint="models",
            counterpart="configure a provider in Qubit Settings (/dashboard) "
                        "or `qubit config set`")
    active = str(config.get("model") or "").strip()
    provider = str(config.get("provider") or "").strip()
    if not provider:
        # /api/config leaves the top-level provider null; the fork's
        # /api/model/info carries the resolved provider for the active model.
        try:
            from voicebridge import qubit_asgi as HA2
            code, _h2, body2 = HA2.call("GET", "/api/model/info", query={},
                                        headers={"accept": "application/json"})
            if 200 <= int(code) < 300:
                info = json.loads(bytes(body2).decode("utf-8"))
                provider = str(info.get("provider") or "").strip()
                # model/info is the RESOLVED record (the config's top-level
                # ``model`` can lag a switch), so it wins when it has one.
                active = str(info.get("model") or "").strip() or active
        except Exception:  # noqa: BLE001 - label only; inventory stands
            pass
    if provider and not active:
        pcfg = (config.get("providers") or {}).get(provider)
        if isinstance(pcfg, dict):
            active = str(pcfg.get("default_model") or "").strip()
    rows: List[Dict[str, Any]] = []
    seen_ids: Dict[str, int] = {}
    for pname, mid in pairs:
        if mid in seen_ids:
            # The same id can be listed under several providers (the live
            # config lists ``deepseek-v4.1-flash`` under both cheapvibecode's
            # models dict and opencode-go's default). The picker's value is the
            # id, so keep ONE row — but attribute it to the provider that
            # actually carries the ACTIVE model, otherwise the row the user is
            # running is labelled with somebody else's provider.
            if provider and pname == provider and \
                    rows[seen_ids[mid]]["provider"] != provider:
                rows[seen_ids[mid]] = {"id": mid, "provider": pname,
                                       "label": f"{mid} ({pname})",
                                       "active": bool(active) and mid == active}
            continue
        seen_ids[mid] = len(rows)
        rows.append({"id": mid, "provider": pname,
                     "label": f"{mid} ({pname})",
                     "active": bool(active) and mid == active})
    absent = bool(active) and active not in seen_ids
    payload: Dict[str, Any] = {"ok": True, "active": active,
                               "provider": provider, "models": rows,
                               "source": source}
    if absent:
        # The running model is real but not in the configured inventory (a
        # CUSTOM id, or a provider whose model list is empty). Say so instead
        # of silently leaving every row unselected.
        payload["active_not_in_inventory"] = True
    return 200, payload


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


def terminal_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/terminal/start`` — the agent-backed terminal (no shell)."""
    from .workbench_ui import terminal_family as TF

    return TF.start(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                    dict(p or {}))


def terminal_input(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/terminal/input`` — keystrokes -> one agent turn on Enter."""
    from .workbench_ui import terminal_family as TF

    return TF.input_text(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                         dict(p or {}))


def terminal_resize(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/terminal/resize`` — record the pane's geometry."""
    from .workbench_ui import terminal_family as TF

    return TF.resize(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                     dict(p or {}))


def terminal_close(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/terminal/close`` — end the session (idempotent)."""
    from .workbench_ui import terminal_family as TF

    return TF.close(cfg, dict(q or {}), b if isinstance(b, dict) else {},
                    dict(p or {}))


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
    # config surfaces. ``settings``/``reasoning`` are the FLAT panel records
    # (theme/show_thinking/default_model/reasoning_effort). Before this they were
    # routed to ``h_wb_settings``, which answered the raw /api/config tree: every
    # control hydrated from ``undefined`` and painted its default over the user's
    # real value. ``default-model`` POST is a WRITE and used to be answered by the
    # models READ handler, so a model choice was silently discarded.
    "settings": "wb_settings",
    "models": "h_wb_models",
    "model/set": "h_wb_passthrough",
    "default-model": "wb_default_model",
    "reasoning": "wb_reasoning",
    "providers": "wb_providers",
    "providers/delete": "wb_providers",
    "tools": "wb_tools",
    # extensions: read + enable/disable through the ONE plugin admission
    # authority; install/uninstall are code-install and are refused.
    "extensions/status": "wb_extensions_status",
    "extensions/registry": "wb_extensions_registry",
    "extensions/toggle": "wb_extensions_toggle",
    "extensions/install": "wb_extensions_install",
    "extensions/uninstall": "wb_extensions_install",
    "extensions/sidecar-proxy-consent": "wb_extensions_sidecar_consent",
    # auth: status is a truthful read of Qubit's own gate; logout is the REAL
    # qubit_compat authority; every passkey path is a typed refusal because Qubit
    # has no WebAuthn credential store (see handlers_config's auth section).
    "auth/status": "wb_auth_status",
    "auth/logout": "wb_auth_logout",
    "auth/passkeys": "wb_auth_unsupported",
    "auth/passkey/delete": "wb_auth_unsupported",
    "auth/passkey/register": "wb_auth_unsupported",
    "auth/passkey/register/options": "wb_auth_unsupported",

    # settings / onboarding / updates families (workbench_ui/handlers_settings.py).
    # Same rule as every other family: an entry is here only when this listener
    # ANSWERS it — with real data or with a typed refusal that names why. The
    # updater writes (apply/force/clear_lock/summary) and the restart verb are
    # refusals on purpose; the real Qubit path is named in their detail.
    "model/auxiliary": "wb_model_auxiliary",
    "providers/self-hosted": "wb_providers_self_hosted",
    "provider/cost-history": "wb_provider_cost_history",
    "provider/quota": "wb_provider_quota",
    "models/refresh": "wb_models_refresh",
    "onboarding/probe": "wb_onboarding_probe",
    "onboarding/setup": "wb_onboarding_setup",
    "onboarding/complete": "wb_onboarding_complete",
    "onboarding/oauth/start": "wb_onboarding_oauth_start",
    "onboarding/oauth/poll": "wb_onboarding_oauth_poll",
    "onboarding/oauth/cancel": "wb_onboarding_oauth_cancel",
    "system/health": "wb_system_health",
    "health/restart": "wb_health_restart_bridge",
    "gateway/status": "wb_gateway_status",
    "git-info": "wb_git_info",
    "updates/apply": "wb_updates_apply_bridge",
    "updates/force": "wb_updates_apply_bridge",
    "updates/clear_lock": "wb_updates_apply_bridge",
    "updates/summary": "wb_updates_apply_bridge",
    # The Gateway card's lifecycle buttons and the health banner's restart both
    # land here: ONE refusal, because starting/stopping/restarting the messaging
    # daemon is `qubit gateway …` behind the supervisor's confirm gate.
    "gateway/start": "wb_health_restart_bridge",
    "gateway/stop": "wb_health_restart_bridge",
    "gateway/restart": "wb_health_restart_bridge",
    # NOTE: /api/skills, /api/memory, /api/profiles, /api/mcp/servers and
    # /api/logs are EXISTING_SET, not PORT_SET — they are already served by
    # qubit_compat and the gateway dispatches to them there. Listing them in both
    # would double-count them in the endpoint table (the table script caught
    # exactly that). Only the SUB-paths Qubit does not already serve live here.
    #
    # profile/active and profile/switch are NOT passthrough: qubit_compat serves
    # the PLURAL ``/api/profiles/active``, so a passthrough dispatch to the
    # singular ``/api/profile/active`` answered "no compat route" 404 — the
    # browser's boot call, measured live. They route to the dedicated handlers
    # below, which dispatch the plural route themselves.
    "profile/active": "profile_active",
    "profile/switch": "profile_switch",
    "skills/content": "h_wb_passthrough",
    "skills/toggle": "h_wb_passthrough",
    "memory/write": "h_wb_passthrough",
    "mcp/tools": "wb_tools",
    "plugins": "h_wb_passthrough",
    "personalities": "h_wb_settings",
    "personality/set": "h_wb_settings",
    "prompts": "h_wb_settings",
    # workspace files (writes go through voicebridge.file_patch, the ONE
    # brokered confined-write lane; reads through voicebridge.files). Every verb
    # the ported client calls is a PORT row or a file/ family verb, so a file
    # click can never fall through to WB_NOT_IN_PORT_SET.
    "file": "h_wb_file_read",
    "file/raw": "h_wb_file_raw",
    "file/save": "file_family",
    # The write verbs route through ``file_family`` (→ file_patch), but they are
    # listed here too: PORT_SET is what the endpoint table and the doc count are
    # derived from, and a verb that only resolves through the family prefix read
    # as GAP while the live server answered it. One row per verb the ported
    # client calls (ui.js quoted in file_family's docstring).
    "file/create": "file_family",
    "file/create-dir": "file_family",
    "file/delete": "file_family",
    "file/move": "file_family",
    "file/rename": "file_family",
    "file/path": "file_family",
    "file/reveal": "file_family",
    "file/open-vscode": "file_family",
    "projects": "h_wb_projects",
    # terminal — served by the command family (workbench_ui/command_family.py).
    # These rows used to point at ``h_wb_terminal``, a blanket PTY_NO_SHELL
    # refusal; the family keeps that policy (no shell is ever spawned) but
    # answers with the agent-backed session the pane can actually render, so the
    # exact-name lookup must not shadow it.
    "terminal/start": "terminal_start",
    "terminal/close": "terminal_close",
    "terminal/input": "terminal_input",
    "terminal/resize": "terminal_resize",
    # share (refused)
    "share/create": "h_wb_file_save_stub",
    # kanban: the vendored boards page on Qubit's ONE boards store
    # (workbench_ui/kanban_family.py). ``docs/WORKBENCH_UI_PORT.md`` called
    # these twelve GAP; the paths were missing, the authority was not —
    # ``voicebridge.kanban`` is what the CLI, voice and /qubit-boards write, so
    # every handler below ends there and none is a second writer.
    "kanban/config": "kanban_config",
    "kanban/boards": "kanban_boards",
    "kanban/board": "kanban_board",
    "kanban/stats": "kanban_stats",
    "kanban/assignees": "kanban_assignees",
    "kanban/tasks": "kanban_tasks",
    "kanban/tasks/bulk": "kanban_tasks_bulk",
    "kanban/links": "kanban_links",
    "kanban/links/delete": "kanban_links_delete",
    "kanban/dispatch": "kanban_dispatch",
    "kanban/events": "kanban_events",
    # SSE — served by stream_sse() through stream_kind(), never by a JSON
    # handler. Listed here so the endpoint table counts it as answered.
    "kanban/events/stream": "kanban_stream",
}

#: Paths under ``/api/wb/api/`` that are dispatched by the gateway itself rather
#: than by a name in ``PORT_SET``: they need the query string (session ids, stream
#: ids, paging cursors) or the raw sub-path, so a single ``sub`` key is not enough.
#:
#: ``chat/start`` used to answer ``WB_NOT_IN_PORT_SET`` although
#: ``workbench_shim.sessions`` already implemented it — the table said it was
#: served and the live server said it was not. These are the paths the ported
#: client calls that the table simply never listed.
_EXTRA_PREFIXES: Dict[str, str] = {
    # chat: upstream's start/stream pair over chat_transport.stream_turn
    "chat/start": "chat_start",
    "chat/steer": "chat_steer",
    "chat/stop": "chat_stop",
    "chat/stream/status": "chat_stream_status",
    # the composer's command registry and side question
    # (workbench_ui/handlers_commands.py). All six are in the measured call set;
    # three of them (bundles, bundles/resolve, moa/resolve) are typed refusals
    # because Qubit has no such authority, and a refusal that NAMES the missing
    # thing beats the generic WB_NOT_IN_PORT_SET the table used to give.
    "commands": "wb_commands",
    "commands/exec": "wb_commands_exec",
    "commands/bundles": "wb_commands_bundles",
    "commands/bundles/resolve": "wb_commands_bundles_resolve",
    "commands/moa/resolve": "wb_commands_moa_resolve",
    "btw": "wb_btw",
    # the client's sidebar hot channel (SSE; see stream_kind below)
    "sessions/events": "sessions_events",
    # the sidebar's gateway hot channel: ?probe=1 is a JSON preflight; without
    # it the listener serves the same SSE as sessions/events (stream_kind)
    "sessions/gateway/stream": "sessions_gateway_probe",
    # workspace tree the composer's file picker and the workspace panel read
    "list": "workspace_list",
    "workspaces": "workspaces",
    # small status endpoints the page polls on boot; each answers only real facts
    "profile/active": "profile_active",
    "health/agent": "health_agent",
    "dashboard/config": "dashboard_config",
    "dashboard/status": "dashboard_status",
    "onboarding/status": "onboarding_status",
    "client-events/log": "client_events_log",
    "transcribe/capability": "transcribe_capability",
    # The check is Qubit's OWN update source (handlers_settings.updates_check_q),
    # not the old "no upstream channel" stub: the panel reads behind/channel
    # off this reply, and a hardcoded "up to date" hid real updates.
    "updates/check": "wb_updates_check_q",
}

#: Prefixes whose tail is a verb or an id. The gateway resolves these to the
#: named handler, which computes its own ``action`` from ``sub``; the prefix exists
#: so a nested verb (``session/title/regenerate``) never falls through to the
#: bare-id row.
_EXTRA_FAMILIES: Tuple[Tuple[str, str], ...] = (
    ("session/", "session_verb"),
    ("approval/", "approval_family"),
    ("clarify/", "clarify_family"),
    ("file/", "file_family"),
    # every passkey path — including ones not in PORT_SET — is the same refusal,
    # so a version skew in the ported client cannot turn one into a bare 501.
    ("auth/passkey", "auth_family"),
)


def approval_family(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/approval/<verb>`` — pending is read, respond settles once."""
    verb = str(p.get("action") or str(p.get("sub") or "").rsplit("/", 1)[-1])
    if verb == "pending":
        return approval_pending(cfg, q, b, p)
    if verb == "respond":
        return approval_respond(cfg, q, b, p)
    return _refusal("WB_CAPABILITY_UNBOUND",
                    f"/api/approval/{verb} has no Qubit authority; nothing was "
                    f"changed.", endpoint="approval/" + verb)


def clarify_family(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/clarify/<verb>`` — a GAP in both directions (named, not blank)."""
    verb = str(p.get("action") or str(p.get("sub") or "").rsplit("/", 1)[-1])
    if verb == "pending":
        return clarify_pending(cfg, q, b, p)
    if verb == "respond":
        return clarify_respond(cfg, q, b, p)
    return _refusal("WB_CAPABILITY_UNBOUND",
                    f"/api/clarify/{verb} has no Qubit authority",
                    endpoint="clarify/" + verb)


def auth_family(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/auth/passkey…`` — every form of it is the same typed refusal.

    ``auth/status`` and ``auth/logout`` have their own PORT_SET rows and are
    reached FIRST by ``_resolve_handler``; this family exists only so a nested or
    version-skewed passkey path still gets ``WB_AUTH_MODEL_UNSUPPORTED`` (with the
    gap named) instead of a generic "not in the port set" that reads like a bug.
    """
    sub = str(p.get("sub") or "").strip("/")
    return wb_auth_unsupported(cfg, q, b, {**dict(p or {}), "sub": sub or "auth"})


def file_family(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """``/api/file/<verb>`` — the real Qubit file authority (workbench_ui/file_family.py).

    Every verb the ported client calls now has a handler here: reads go through
    ``voicebridge.files`` and writes through ``voicebridge.file_patch``, the ONE
    brokered confined-write lane. ``file/move`` and ``file/rename`` stay typed
    refusals because that lane has no rename primitive; ``file/open-vscode``
    refuses because Qubit never launches an editor; ``file/reveal`` refuses on a
    host without a desktop session. Each refusal names the reason.
    """
    from .workbench_ui import file_family as FF

    sub = str(p.get("sub") or "").strip("/")
    verb = sub.rsplit("/", 1)[-1] if "/" in sub else str(p.get("action") or "")
    handler = FF.WRITE_VERBS.get(verb)
    if handler is None:
        return _refusal("WB_CAPABILITY_UNBOUND",
                        f"/api/file/{verb or '<none>'} has no Qubit authority",
                        endpoint="file/" + (verb or ""))
    return handler(cfg, q, b, p)


def h_wb_file_read(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/file?session_id=&path=`` — the preview pane's text read."""
    from .workbench_ui import file_family as FF

    return FF.file_read(cfg, q, b, p)


def h_wb_file_raw(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/file/raw`` without a writer: a typed refusal, never bytes.

    The listener claims this path before the gateway (``webui`` must write the
    body itself, see ``raw_handler()``). Reaching this handler means the caller
    went through the JSON gateway — answering a JSON refusal is correct there,
    serving bytes under a ``application/json`` framing would not be.
    """
    return _refusal(
        "WB_FILE_RAW_REQUIRES_BYTES",
        "GET /api/file/raw streams file bytes and is served by the listener's "
        "own raw branch, not by the JSON gateway; this path was invoked "
        "through the gateway, so nothing was streamed.",
        endpoint="file/raw")


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

    Then ``_EXTRA_PREFIXES`` / ``_EXTRA_FAMILIES``: the paths the ported client
    actually calls that the original table never listed (``chat/start`` among
    them). A live probe of the page showed all of them answering 501.
    """
    clean = sub.strip("/")
    if not clean:
        return None
    import sys
    mod = sys.modules[__name__]
    name = PORT_SET.get(clean)
    if name:
        return getattr(mod, name, None)
    name = _EXTRA_PREFIXES.get(clean)
    if name:
        return getattr(mod, name, None)
    if clean in EXISTING_SET:
        return h_wb_passthrough
    for prefix, handler_name in _EXTRA_FAMILIES:
        if clean.startswith(prefix) and len(clean) > len(prefix):
            return getattr(mod, handler_name, None)
    # ``kanban/boards/<slug>`` and ``.../<slug>/switch`` carry their slug in the
    # path, so the bare ``kanban/boards`` row cannot answer them. Resolved here
    # (and ONLY when the slug segment is non-empty) so the collection row stays
    # the plain list/create handler.
    if clean.startswith("kanban/boards/"):
        parts = clean[len("kanban/boards/"):].strip("/").split("/")
        if parts and parts[0]:
            return getattr(mod, "kanban_boards", None)
    # The git / worktree / terminal / workspace command families own their own
    # table (``workbench_ui/command_family.py``): thirty-odd paths, most of them
    # keyed by a verb tail, so listing them here would be a second copy of the
    # same list. Checked LAST so a path the earlier tables already answer keeps
    # answering from there (``session/worktree/status`` is a session read).
    return _command_handler(clean)


def _command_handler(clean: str) -> Optional[Callable[..., Any]]:
    """The command-family handler for ``clean``, or None (never raises)."""
    try:
        from .workbench_ui import command_family as CF

        return CF.handler_for(clean)
    except Exception:  # noqa: BLE001 - a broken family must not break routing
        return None


# ---------------------------------------------- composer commands + side question
# ``workbench_ui/handlers_commands.py`` owns the behaviour; these pins exist so
# the gateway's table and the family stay one audit surface (the same reason
# every other family has wrappers here).
def wb_commands(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/commands`` — the real slash catalog in upstream's row shape."""
    from .workbench_ui import handlers_commands as HC

    return HC.commands(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def wb_commands_exec(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/commands/exec`` — run it on ``chat_transport``'s executor."""
    from .workbench_ui import handlers_commands as HC

    return HC.commands_exec(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def wb_commands_bundles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/commands/bundles`` — typed: no persisted bundle registry."""
    from .workbench_ui import handlers_commands as HC

    return HC.commands_bundles(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def wb_commands_bundles_resolve(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/commands/bundles/resolve`` — typed: nothing to resolve against."""
    from .workbench_ui import handlers_commands as HC

    return HC.commands_bundles_resolve(cfg, dict(q or {}), dict(b or {}),
                                       dict(p or {}))


def wb_commands_moa_resolve(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/commands/moa/resolve`` — typed: Qubit has no MoA route."""
    from .workbench_ui import handlers_commands as HC

    return HC.commands_moa_resolve(cfg, dict(q or {}), dict(b or {}),
                                   dict(p or {}))


def wb_btw(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/btw`` — one ephemeral side question on the parent session."""
    from .workbench_ui import handlers_commands as HC

    return HC.btw(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


# -------------------------------------------------------------------- kanban
# The vendored boards page (`panels.js`) on Qubit's ONE boards store. The
# behaviour lives in ``workbench_ui/kanban_family.py``; these pins exist so the
# gateway's NAME-addressed table and the family stay one audit surface — a
# PORT_SET row whose name is missing here answered WB_NOT_IN_PORT_SET while the
# handler existed (the reason every other family has these wrappers too).
def kanban_board(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/board`` — the board, in the native store's lanes."""
    from .workbench_ui import kanban_family as KF

    return KF.board(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_boards(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET|POST /api/kanban/boards`` and ``/<slug>{,/switch, PATCH, DELETE}``.

    The collection row and the slug-scoped verbs resolve to this ONE handler
    (``_resolve_handler`` picks it for ``kanban/boards/<slug>``), so the split
    is made from the SAME ``sub`` the gateway passes — reading a separate
    ``slug`` param would silently serve the collection list for a PATCH.
    """
    from .workbench_ui import kanban_family as KF

    sub = str((p or {}).get("sub") or "").strip("/")
    if sub.startswith("kanban/boards/") and sub[len("kanban/boards/"):].strip("/"):
        return KF.board_verb(cfg, dict(q or {}), dict(b or {}), dict(p or {}))
    return KF.boards(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/stats`` — per-status counts from the store."""
    from .workbench_ui import kanban_family as KF

    return KF.stats(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_assignees(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/assignees`` — the assignee picker's roster."""
    from .workbench_ui import kanban_family as KF

    return KF.assignees(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET|PATCH /api/kanban/config`` — ``dashboard.kanban`` preferences."""
    from .workbench_ui import kanban_family as KF

    return KF.config(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_tasks(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/tasks`` — create on the native store."""
    from .workbench_ui import kanban_family as KF

    return KF.tasks(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_tasks_bulk(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/tasks/bulk`` — per-id status moves, reported each."""
    from .workbench_ui import kanban_family as KF

    return KF.tasks_bulk(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_links(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/links`` — typed refusal (the store has no edges)."""
    from .workbench_ui import kanban_family as KF

    return KF.links(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_links_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/links/delete`` — idempotent no-op, ``removed: false``."""
    from .workbench_ui import kanban_family as KF

    return KF.links_delete(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_dispatch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/dispatch`` — real dry-run plan; a real run refuses."""
    from .workbench_ui import kanban_family as KF

    return KF.dispatch(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_events(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/events?since=N`` — the board timeline's delta."""
    from .workbench_ui import kanban_family as KF

    return KF.events(cfg, dict(q or {}), dict(b or {}), dict(p or {}))


def kanban_stream(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET|POST /api/kanban/events/stream`` reached as JSON rather than SSE.

    ``stream_kind()`` claims this path before the JSON gateway, so a request
    only lands here when something dispatched it through the gateway anyway.
    Answering JSON keeps that caller honest instead of hanging on a socket it
    never opens; the SSE framing lives in ``stream_sse``.
    """
    return 200, {
        "ok": True, "stream": "kanban/events/stream",
        "content_type": "text/event-stream",
        "detail": ("this path is served as SSE by the listener's stream branch "
                   "(workbench_ui_routes.stream_sse); open it with "
                   "EventSource, not fetch()"),
        "events": [], "latest_event_id": 0,
    }


# ------------------------------------------------------------- raw bytes
def raw_handler(sub: str) -> Optional[Callable[..., None]]:
    """The one path in this port that writes BYTES instead of JSON, or None.

    ``workspace.js:291`` builds ``/api/file/raw?...&inline=1|download=1`` and
    the browser fetches it directly for images, media, PDFs, HTML documents and
    attachments — so it must answer with a body and a content type, exactly
    like ``serve_asset`` does, not with a ``(status, json)`` tuple. The
    listener's workbench branch calls this BEFORE the JSON gateway dispatcher
    and hands over the request handler.

    Confinement is the same ``file_family.resolve_workspace_path`` every other
    file route uses, and the bytes come from ``voicebridge.files.read_bytes``
    (bounded, special-file-refusing) — no second reader and no escaped path.
    """
    clean = str(sub or "").strip("/")
    if clean != "file/raw":
        return None

    def handle(handler: Any, cfg: Any, q: Dict[str, Any]) -> None:
        from .workbench_ui import file_family as FF

        absolute, err = FF.file_raw_check(None, str((q or {}).get("path") or ""))
        if err is not None:
            status, payload = err
            handler._json(payload, status)
            return
        from voicebridge import files as F

        try:
            got = F.read_bytes(None, str(absolute))
        except Exception as exc:  # noqa: BLE001 - a refusal, not a traceback
            status, payload = FF.refusal(
                str(getattr(exc, "code", "") or "FILES_IO"),
                str(getattr(exc, "detail", "") or exc),
                endpoint="file/raw")
            handler._json(payload, status)
            return
        download = str((q or {}).get("download") or "").lower() in (
            "1", "true", "yes", "on")
        disposition = "attachment" if download else "inline"
        body = bytes(got.get("data") or b"")
        name = str(got.get("name") or absolute.name)
        handler._send(200, body, str(got.get("mime")
                                     or "application/octet-stream"),
                      extra_headers=[("Content-Disposition",
                                      f'{disposition}; filename="{name}"')])

    return handle


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
            f"({len(PORT_SET) + len(_EXTRA_PREFIXES)} ported, "
            f"{len(EXISTING_SET)} existing of 184 endpoints the upstream UI "
            f"calls, plus {len(_EXTRA_FAMILIES)} routed families). Qubit "
            f"exposes no authority for it; nothing was fabricated.",
            endpoint=sub,
            counterpart=("no Qubit counterpart yet" if head in (
                "kanban", "wiki", "notes", "auth", "extensions", "background",
                "gateway", "commands", "goal", "insights", "media", "mcp")
                else "Qubit Dashboard (/)"))
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
    # the ported client's own call set (workbench_ui/handlers_extra.py). These
    # names are what _EXTRA_PREFIXES / _EXTRA_FAMILIES point at; a name here with
    # no entry answered _WB_NOT_IN_PORT_SET even though the handler existed.
    "chat_start": chat_start,
    "chat_steer": chat_steer,
    "chat_stop": chat_stop,
    "chat_stream_status": chat_stream_status,
    "session_verb": session_verb,
    "session_stream": session_stream,
    "session_flag": session_flag,
    "sessions_events": sessions_events,
    "sessions_gateway_probe": sessions_gateway_probe,
    "approval_pending": approval_pending,
    "approval_respond": approval_respond,
    "approval_family": approval_family,
    "clarify_pending": clarify_pending,
    "clarify_respond": clarify_respond,
    "clarify_family": clarify_family,
    "file_family": file_family,
    "h_wb_file_read": h_wb_file_read,
    "h_wb_file_raw": h_wb_file_raw,
    "wb_commands": wb_commands,
    "wb_commands_exec": wb_commands_exec,
    "wb_commands_bundles": wb_commands_bundles,
    "wb_commands_bundles_resolve": wb_commands_bundles_resolve,
    "wb_commands_moa_resolve": wb_commands_moa_resolve,
    "wb_btw": wb_btw,
    "workspace_list": workspace_list,
    "workspaces": workspaces,
    "profile_active": profile_active,
    "profile_switch": profile_switch,
    "health_agent": health_agent,
    "dashboard_config": dashboard_config,
    "dashboard_status": dashboard_status,
    "onboarding_status": onboarding_status,
    "client_events_log": client_events_log,
    "transcribe_capability": transcribe_capability,
    "updates_check": updates_check,
    # the operational-settings / extensions / auth call set
    # (workbench_ui/handlers_config.py). A name here with no entry answered
    # _WB_NOT_IN_PORT_SET even though the handler existed.
    "wb_settings": wb_settings,
    "wb_reasoning": wb_reasoning,
    "wb_providers": wb_providers,
    "wb_default_model": wb_default_model,
    "wb_tools": wb_tools,
    "wb_extensions_status": wb_extensions_status,
    "wb_extensions_registry": wb_extensions_registry,
    "wb_extensions_toggle": wb_extensions_toggle,
    "wb_extensions_install": wb_extensions_install,
    "wb_extensions_sidecar_consent": wb_extensions_sidecar_consent,
    "wb_auth_status": wb_auth_status,
    "wb_auth_logout": wb_auth_logout,
    "wb_auth_unsupported": wb_auth_unsupported,
    "auth_family": auth_family,
    # settings / onboarding / updates (workbench_ui/handlers_settings.py)
    "wb_model_auxiliary": wb_model_auxiliary,
    "wb_providers_self_hosted": wb_providers_self_hosted,
    "wb_provider_cost_history": wb_provider_cost_history,
    "wb_provider_quota": wb_provider_quota,
    "wb_models_refresh": wb_models_refresh,
    "wb_onboarding_probe": wb_onboarding_probe,
    "wb_onboarding_setup": wb_onboarding_setup,
    "wb_onboarding_complete": wb_onboarding_complete,
    "wb_onboarding_oauth_start": wb_onboarding_oauth_start,
    "wb_onboarding_oauth_poll": wb_onboarding_oauth_poll,
    "wb_onboarding_oauth_cancel": wb_onboarding_oauth_cancel,
    "wb_system_health": wb_system_health,
    "wb_health_restart_bridge": wb_health_restart_bridge,
    "wb_gateway_status": wb_gateway_status,
    "wb_git_info": wb_git_info,
    "wb_updates_check_q": wb_updates_check_q,
    "wb_updates_apply_bridge": wb_updates_apply_bridge,
    # kanban (workbench_ui/kanban_family.py)
    "kanban_board": kanban_board,
    "kanban_boards": kanban_boards,
    "kanban_stats": kanban_stats,
    "kanban_assignees": kanban_assignees,
    "kanban_config": kanban_config,
    "kanban_tasks": kanban_tasks,
    "kanban_tasks_bulk": kanban_tasks_bulk,
    "kanban_links": kanban_links,
    "kanban_links_delete": kanban_links_delete,
    "kanban_dispatch": kanban_dispatch,
    "kanban_events": kanban_events,
    "kanban_stream": kanban_stream,
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


# ---------------------------------------------------------------------------
# SSE: the two paths that are streams, not JSON
# ---------------------------------------------------------------------------
def is_blocking(sub: str) -> bool:
    """Whether answering ``sub`` runs a subprocess (so it must leave the loop).

    The git and worktree families run ``git`` and the workspace family touches
    the project registry: both block, and the listener must hand them to a
    worker thread instead of the event-loop thread. Purely a classification —
    it starts nothing.
    """
    clean = str(sub or "").strip("/")
    return clean.startswith(("git/", "worktree/", "worktrees"))


def stream_kind(sub: str) -> Optional[str]:
    """``"chat"`` / ``"sessions"`` when ``sub`` must be served as SSE, else None.

    Upstream serves both as ``text/event-stream`` and the ported client opens
    them with ``EventSource`` (``messages.js:8206``, ``sessions.js:8523``).
    Answering them with JSON — or with a typed 501 — made the client's
    ``onerror`` reconnect in a permanent loop, which is what the live network
    log showed before this branch. The listener asks THIS function; the framing
    (chunked writes, keep-alives, the closing chunk) lives in ``webui`` where
    every other SSE route already does it.
    """
    clean = str(sub or "").strip("/")
    # Exact match, not a prefix: "chat/stream/status" is a JSON route that lives
    # under the same name space, and a prefix test swallowed it — the status call
    # came back as an SSE body (": connected") instead of the run's JSON state.
    if clean == "chat/stream":
        return "chat"
    if clean == "sessions/events":
        return "sessions"
    # The gateway hot channel the sidebar's EventSource opens
    # (``sessions.js:startGatewaySSE``). It is the same store-backed snapshot
    # stream as ``sessions/events``; answering 501 made the client reconnect in
    # a loop. ``?probe=1`` is a JSON preflight and is routed to its own handler,
    # never here (the listener checks for the probe query first).
    if clean == "sessions/gateway/stream":
        return "sessions"
    # The per-session EventSource the transcript pane opens before it paints.
    # Upstream uses it for confirm-connection, bg_task_complete and self-heal;
    # only the first is load-bearing for rendering, and the client's listener
    # for 'initial' just confirms the connection. Not porting it meant the pane
    # fetched its transcript (200, correct messages) and then hung waiting on a
    # stream that answered 501.
    if clean == "session/stream":
        return "session"
    # The composer terminal's output channel (``terminal.js:559`` opens
    # ``api/terminal/output?session_id=`` as an EventSource). It is not in the
    # 165-endpoint list — that list is built from fetch calls — but the pane
    # dials it the moment ``terminal/start`` succeeds, so answering 501 here
    # would leave a started terminal with no output forever.
    if clean == "terminal/output":
        return "terminal"
    # The ephemeral /btw stream: ``commands.js:2379`` takes ``stream_id`` from
    # the POST and opens an EventSource on this path, listening for ``done``
    # (``{answer}``) and ``apperror`` (``messages.js:10858``). A JSON answer
    # here would make the listener reconnect forever, so it is a real stream
    # with a bounded wait.
    if clean == "btw/stream":
        return "btw"
    # The kanban board's live channel (``panels.js:3282`` opens it with
    # ``EventSource`` and listens for ``hello`` then ``events``). It is a real
    # poll-backed stream over the board's ONE store — answering JSON here made
    # the client's ``onerror`` count up and fall back to a 30s poll it did not
    # need.
    if clean == "kanban/events/stream":
        return "kanban"
    return None


def stream_frames(sub: str, cfg: Any, q: Dict[str, str]) -> Iterable[str]:
    """SSE frames for one stream path. A refusal is an ``error`` frame."""
    kind = stream_kind(sub)
    if kind == "chat":
        from .workbench_ui.chat_stream import chat_stream_frames

        stream_id = str((q or {}).get("stream_id") or "").strip()
        if not stream_id:
            yield (': missing stream_id\n\n'
                   'event: error\n'
                   'data: {"event": "error", "code": "STREAM_ID_REQUIRED", '
                   '"detail": "GET /api/chat/stream needs ?stream_id="}\n\n')
            return
        try:
            after = int(str((q or {}).get("after_seq") or "0"))
        except (TypeError, ValueError):
            after = 0
        yield from chat_stream_frames(stream_id, after)
        return
    if kind == "sessions":
        from .workbench_ui.handlers_extra import sessions_events_frames

        yield from sessions_events_frames(cfg)
        return
    if kind == "terminal":
        from .workbench_ui import command_family as CF

        frames = CF.stream_frames("terminal/output", cfg, dict(q or {}))
        if frames is not None:
            yield from frames
        return
    if kind == "btw":
        from .workbench_ui import handlers_commands as HC

        stream_id = str((q or {}).get("stream_id") or "").strip()
        if not stream_id:
            yield ('event: apperror\\ndata: {"message": "GET /api/btw/stream '
                   'needs ?stream_id="}\\n\\n')
            return
        yield from HC.btw_stream_frames(stream_id)
        return
    if kind == "kanban":
        # The kanban family's own frames — the SAME shape its JSON ``events``
        # read serves, so a tab on SSE and a tab on the 30s poll converge.
        from .workbench_ui import kanban_family as KF

        yield from KF.stream(sub, cfg, dict(q or {}))
        return
    if kind == "session":
        # Confirm-connection only. Upstream also pushes bg_task_complete and
        # server_turn_started self-heal frames here; Qubit's own session store
        # is the authority for both, and inventing those events would be worse
        # than not sending them. `initial` is what the client blocks on.
        yield 'event: initial\ndata: {"event": "initial"}\n\n'
        return
    yield ('event: error\n'
           'data: {"event": "error", "code": "WB_NOT_IN_PORT_SET", '
           '"detail": "this path is not a stream in this port"}\n\n')


def stream_sse(handler: Any, sub: str, cfg: Any,
               q: Optional[Dict[str, str]] = None) -> None:
    """Write one SSE response on ``handler``, framed HTTP/1.1 CHUNKED.

    Copy of the framing ``webui`` uses for its own SSE routes (office tower,
    qubit chat): a body with no ``Content-Length`` and no ``Transfer-Encoding``
    is delimited by connection close, which browsers do NOT treat as a live
    stream — the live probe of this exact page showed ``EventSource`` opening
    and then never delivering a frame. Each frame is one chunk; the terminating
    chunk is written when the frame iterator ends, so the client's ``onerror``
    fires once and its own reconnect ladder takes over (with ``after_seq``, so
    nothing is duplicated).
    """
    handler.protocol_version = "HTTP/1.1"
    handler.close_connection = True
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.send_header("Connection", "close")
    handler.end_headers()

    def write(frame: str) -> None:
        raw = frame.encode("utf-8")
        handler.wfile.write(("%X\r\n" % len(raw)).encode("ascii") + raw + b"\r\n")
        handler.wfile.flush()

    try:
        write(": connected\n\n")
        for frame in stream_frames(sub, cfg, dict(q or {})):
            write(frame)
    except (BrokenPipeError, ConnectionResetError, OSError):
        # The client navigated away or reconnected: not an error, and there is
        # no longer a socket to close a chunked body on.
        return
    try:
        handler.wfile.write(b"0\r\n\r\n")
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
