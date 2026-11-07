"""Workbench route table — the ``/qubit-workbench`` page's REST surface.

Six routes, one per capability the page needs. Each handler is a thin,
dependency-free wrapper over ``adapters_sessions`` / ``adapters_workspace``:

    GET  /api/workbench/sessions                       -> list_sessions
    GET  /api/workbench/sessions/<id>/messages         -> read_transcript
    GET  /api/workbench/sessions/<id>/timeline         -> read_timeline
    POST /api/workbench/sessions/<id>/turn             -> send_turn
    GET  /api/workbench/workspace/tree                 -> list_tree
    POST /api/workbench/workspace/preview              -> preview_file

WHY A SEPARATE FILE (same reasoning as ``voicebridge/hud_routes.py``)
---------------------------------------------------------------------------
``voicebridge/qubit_compat.py`` is one large vendored-compat module the
dashboard dispatches through. The workbench is a self-owned surface, so its rows
are declared here, in one file that can be read end to end, and registered into
the compat table by ONE block::

    from .workbench.workbench_routes import (workbench_routes as _wb_routes,
                                             workbench_handlers as _wb_handlers)
    for _m, _p, _h in _wb_routes():
        _r(_m, _p, _h)
    HANDLERS.update(_wb_handlers())

THE IMPORT RULE (load-bearing)
---------------------------------------------------------------------------
Nothing in this module imports an adapter or the store at import time: each
handler resolves its adapter INSIDE the call, in a ``try``/``except``. A tree
where ``voicebridge.workbench`` is absent, or where the session authority
cannot be constructed, still imports this file and boots the dashboard; the
route answers a typed 503 naming the missing capability instead of failing the
listener with an ImportError at boot. A missing capability must be a refusal,
never a crash.

READ/WRITE SPLIT
---------------------------------------------------------------------------
Five routes are reads. The one write (``POST .../turn``) does not write
anything itself: it delegates to ``voicebridge.chat_transport.turn``, the same
persisted turn ``/qubit-chat`` and the voice bridge run, so there is no second
send implementation and no second writer to the transcript. The workspace
routes are read-only by construction — a mutation reaches only
``adapters_workspace.refused_mutation``.

The handler signature is the compat table's: ``handler(cfg, q, b, p)``
returning ``(status_code, payload)``.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List, Tuple

#: code -> fault HTTP status. A capability fault is a 5xx (the request was fine,
#: the backend is not); a caller error is a 4xx. Anything unmapped falls back to
#: 400 for ``*_REQUIRED``/``MALFORMED`` and 409 for ``*_NOT_FOUND``.
_STATUS: Dict[str, int] = {
    "WORKBENCH_SESSION_STORE_UNAVAILABLE": 503,
    "WORKBENCH_TURN_TRANSPORT_UNAVAILABLE": 503,
    "WORKBENCH_ACTIVITY_UNAVAILABLE": 503,
    "WORKBENCH_SESSION_LIST_FAILED": 502,
    "WORKBENCH_SESSION_READ_FAILED": 502,
    "WORKBENCH_SESSION_CONTRACT_DRIFT": 502,
    "WORKBENCH_TURN_FAILED": 502,
    "WORKBENCH_TURN_CONTRACT_DRIFT": 502,
    "WORKBENCH_FILES_LIST_FAILED": 502,
    "WORKBENCH_FILES_READ_FAILED": 502,
    "SESSION_NOT_FOUND": 409,
    "FILES_NOT_FOUND": 409,
    "FILES_OUTSIDE_ROOT": 403,
    "FILES_BINARY": 415,
    "FILES_TOO_LARGE": 413,
    "WORKBENCH_FILES_READ_ONLY": 403,
    "WORKBENCH_SESSION_ID_REQUIRED": 400,
    "WORKBENCH_TURN_TEXT_REQUIRED": 400,
    "WORKBENCH_FILES_PATH_REQUIRED": 400,
    "FILES_BAD_INPUT": 400,
}


def _adapter(module: str) -> Any:
    """Resolve one adapter module from this package.

    Raises (never returns None) so every handler has exactly one failure path;
    the caller turns that into the 503.
    """
    return importlib.import_module("." + module, __package__)


def _status(payload: Any, default: int = 200) -> int:
    if not isinstance(payload, dict):
        return default
    if payload.get("ok") is True:
        return 200
    code = str(payload.get("code") or "")
    if code in _STATUS:
        return _STATUS[code]
    state = str(payload.get("state") or "")
    if state == "EMPTY":
        # An empty-but-healthy answer (no active project) is not an error.
        return 200
    if code.endswith(("_REQUIRED", "MALFORMED")):
        return 400
    if code.endswith("NOT_FOUND"):
        return 409
    return 502


def _serve(capability: str, module: str, fn_name: str, *args: Any,
           **kwargs: Any) -> Tuple[int, Any]:
    """Resolve -> call -> type the failure. The adapter owns the payload."""
    try:
        mod = _adapter(module)
        fn = getattr(mod, fn_name, None)
        if not callable(fn):
            raise AttributeError(f"voicebridge.workbench.{module} exposes no "
                                 f"{fn_name}")
    except Exception as exc:  # ModuleNotFoundError, AttributeError, anything
        return 503, {
            "ok": False, "state": "UNAVAILABLE",
            "code": "WORKBENCH_UNAVAILABLE",
            "detail": (f"the workbench {capability} adapter is not available: "
                       f"{type(exc).__name__}: {exc}"),
            "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back",
        }
    try:
        out = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - adapters do not raise, but a bug must not 500 bare
        return 502, {
            "ok": False, "state": "FAILED",
            "code": "WORKBENCH_ADAPTER_FAILED",
            "detail": (f"the workbench {capability} adapter raised: "
                       f"{type(exc).__name__}: {exc}"),
        }
    return _status(out), out


def _q(q: Any, *names: str, default: str = "") -> str:
    if not isinstance(q, dict):
        return default
    for name in names:
        if name in q and q[name] is not None:
            return str(q[name])
    return default


def _b(b: Any) -> Dict[str, Any]:
    return b if isinstance(b, dict) else {}


def _p(p: Any, name: str) -> str:
    return str((p or {}).get(name) or "") if isinstance(p, dict) else ""


# --------------------------------------------------------------- handlers
def h_workbench_sessions(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/workbench/sessions?limit&offset&q&archived"""
    from . import adapters_sessions as S

    return _serve("session-list", "adapters_sessions", "list_sessions",
                  cfg, limit=_q(q, "limit", default="50"),
                  offset=_q(q, "offset", default="0"),
                  query=_q(q, "q", "query"),
                  include_archived=_q(q, "archived", default="") in
                  ("1", "true", "yes"))


def h_workbench_transcript(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/workbench/sessions/<id>/messages?limit&offset"""
    return _serve("transcript", "adapters_sessions", "read_transcript",
                  cfg, _p(p, "id"),
                  limit=_q(q, "limit", default="500"),
                  offset=_q(q, "offset", default="0"))


def h_workbench_timeline(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/workbench/sessions/<id>/timeline?limit"""
    return _serve("timeline", "adapters_sessions", "read_timeline",
                  cfg, _p(p, "id"), limit=_q(q, "limit", default="40"))


def h_workbench_turn(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/workbench/sessions/<id>/turn {text, model?, reasoning?}"""
    body = _b(b)
    return _serve("turn", "adapters_sessions", "send_turn",
                  cfg, _p(p, "id"), body.get("text", ""),
                  model=body.get("model"), reasoning=body.get("reasoning"))


def h_workbench_session(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/workbench/sessions/<id> -> one session with its messages.

    The page reads a session as one object (header fields plus the transcript)
    rather than fetching /messages separately, so this composes the two adapter
    calls instead of making the browser do it. Both halves keep their own typed
    failure: a missing session answers the adapter's MISSING untouched.
    """
    session_id = _p(p, "id")
    status, payload = _serve("session", "adapters_sessions", "read_transcript",
                             cfg, session_id,
                             limit=_q(q, "limit", default="500"),
                             offset=_q(q, "offset", default="0"))
    if status != 200 or not isinstance(payload, dict):
        return status, payload
    merged = dict(payload)
    merged.setdefault("id", session_id)
    # Header fields the ring and title need, when the store already knows them.
    rows = payload.get("sessions")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and str(row.get("id") or "") == session_id:
                merged.setdefault("title", row.get("title"))
                merged.setdefault("model", row.get("model"))
                break
    return 200, merged


def h_workbench_turn_root(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/workbench/turn {session_id, text, model?, reasoning?}

    The page posts a turn here, not to the per-session path: it sends with the
    id in the body so a brand-new session has one call shape. ``session_id`` is
    required — a turn with no session is refused rather than silently starting
    a second store entry.
    """
    body = _b(b)
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        return 400, {"ok": False, "code": "SESSION_REQUIRED",
                     "detail": "Send {\"session_id\": ...} with the turn."}
    return _serve("turn", "adapters_sessions", "send_turn",
                  cfg, session_id, body.get("text", ""),
                  model=body.get("model"), reasoning=body.get("reasoning"))


def h_workbench_file(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/workbench/workspace/file?path=&workspace=&max_chars=

    Same adapter as the POST preview, reachable as a GET because the file pane
    is a read: a viewer should not need a POST to look at a file.
    """
    return _serve("workspace-preview", "adapters_workspace", "preview_file",
                  cfg, _q(q, "path"),
                  workspace=_q(q, "workspace"),
                  max_chars=int(_q(q, "max_chars", default="20000") or 20000))


def h_workbench_tree(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/workbench/workspace/tree?path="""
    return _serve("workspace-tree", "adapters_workspace", "list_tree",
                  cfg, _q(q, "path"))


def h_workbench_preview(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/workbench/workspace/preview {path, workspace?, max_chars?}

    ``path`` is the workspace-relative entry path :func:`list_tree` returned;
    ``workspace`` is that listing's ``workspace`` root.
    """
    body = _b(b)
    return _serve("workspace-preview", "adapters_workspace", "preview_file",
                  cfg, body.get("path", ""),
                  workspace=body.get("workspace", ""),
                  max_chars=body.get("max_chars", 20000))


_HANDLERS: Dict[str, Callable[..., Tuple[int, Any]]] = {
    "h_workbench_sessions": h_workbench_sessions,
    "h_workbench_transcript": h_workbench_transcript,
    "h_workbench_timeline": h_workbench_timeline,
    "h_workbench_turn": h_workbench_turn,
    "h_workbench_tree": h_workbench_tree,
    "h_workbench_preview": h_workbench_preview,
    "h_workbench_session": h_workbench_session,
    "h_workbench_turn_root": h_workbench_turn_root,
    "h_workbench_file": h_workbench_file,
}


def workbench_routes() -> List[Tuple[str, str, str]]:
    """The workbench's rows for the dashboard compat route table."""
    return [
        ("GET", r"/api/workbench/sessions", "h_workbench_sessions"),
        # Specific sub-paths FIRST: an ordered table means the bare-id row below
        # would otherwise match "/sessions/<id>/messages" too and shadow it.
        ("GET", r"/api/workbench/sessions/(?P<id>[^/]+)/messages",
         "h_workbench_transcript"),
        ("GET", r"/api/workbench/sessions/(?P<id>[^/]+)/timeline",
         "h_workbench_timeline"),
        ("POST", r"/api/workbench/sessions/(?P<id>[^/]+)/turn",
         "h_workbench_turn"),
        ("GET", r"/api/workbench/sessions/(?P<id>[^/]+)", "h_workbench_session"),
        ("POST", r"/api/workbench/turn", "h_workbench_turn_root"),
        ("GET", r"/api/workbench/workspace/tree", "h_workbench_tree"),
        ("GET", r"/api/workbench/workspace/file", "h_workbench_file"),
        ("POST", r"/api/workbench/workspace/preview", "h_workbench_preview"),
    ]


def workbench_handlers() -> Dict[str, Callable[..., Tuple[int, Any]]]:
    """The ``HANDLERS`` entries the names in :func:`workbench_routes` resolve to."""
    return dict(_HANDLERS)
