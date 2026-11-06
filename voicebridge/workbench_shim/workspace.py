"""Workspace routes: hermes-webui's file tree/read contracts on Qubit's adapter.

UPSTREAM CONTRACT (read at UPSTREAM_PIN)
---------------------------------------------------------------------------
``GET  /api/list``        ``routes.py:23502-23574`` → ``{entries, signature, path,
                          workspace, workspace_recovered}``; each entry from
                          ``workspace.py:1632-1745`` serialized by
                          ``serialize_workspace_entries_for_browser`` ``:1620``.
``GET  /api/file``        ``routes.py:26642-26659`` →
                          ``{path, content, size, lines}`` (``workspace.py:1886``).
``POST /api/workspace/upload`` ``routes.py:19164`` and the save lane — writes into
                          the session workspace.

WHERE THE DELEGATION LANDS
---------------------------------------------------------------------------
All reads go through :mod:`voicebridge.workbench.adapters_workspace`
(``list_tree`` / ``preview_file``), which already confines every path to the
project folders registered in Qubit via ``voicebridge.files`` and refuses ``..``
traversal, escaping symlinks, binary content and oversize files with the files
layer's own typed codes. Nothing here re-implements a path check, and no write
lane is added: Qubit file mutations are brokered, confirmed actions
(``voicebridge.file_patch`` through ``voicebridge.broker``), so both upstream
write routes answer a typed NOT_PORTED naming that journal rather than a
silent no-op or an unsafe direct write.

TWO CONTRACT DIFFERENCES THE PORTER MUST KNOW
---------------------------------------------------------------------------
* Upstream's listing is **session-scoped** (``?session_id=&path=`` resolved
  against the session's ``workspace``). Qubit's is **project-scoped**. The shim
  therefore accepts ``workspace`` (the root the previous listing returned) plus
  ``path``, and, when a client sends ``session_id`` with no ``workspace``, uses
  the active project folder. A path outside the registered roots is refused
  (never silently rebased onto the root — that looks like a successful
  navigation).
* Entry sort order: upstream emits ``workspace_sort_rank`` (symlink 0, dir 1,
  file 2); Qubit's adapter computes ``sort_rank`` with the same rank order
  (``adapters_workspace.py:210-214``). The shim renames the key, it does not
  re-sort.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Tuple

from . import UPSTREAM_PIN, body_of, flag, gap, int_param, str_param


def _adapter() -> Any:
    """The confined workspace adapter. Raises → the caller types the refusal."""
    from ..workbench import adapters_workspace as AW

    for name in ("list_tree", "preview_file"):
        if not callable(getattr(AW, name, None)):
            raise AttributeError(
                f"voicebridge.workbench.adapters_workspace exposes no {name}")
    return AW


def _unavailable(detail: str) -> Tuple[int, Dict[str, Any]]:
    return 503, {"ok": False, "state": "UNAVAILABLE",
                 "code": "WORKSPACE_ADAPTER_UNAVAILABLE",
                 "detail": detail, "upstream": UPSTREAM_PIN}


def _status_for(payload: Dict[str, Any]) -> int:
    """Type an adapter refusal the way upstream's list/read routes do.

    Upstream answers 404 for ``FileNotFoundError``/``ValueError`` (a missing
    path or a traversal attempt) and 200 otherwise. Qubit keeps its richer codes
    but maps onto those statuses: outside-root is a 403 (a refusal, not a
    missing file), not-found is 404, binary/too-large are 415/413.
    """
    if payload.get("ok") is True:
        return 200
    code = str(payload.get("code") or "")
    if code.endswith("NOT_FOUND"):
        return 404
    if code.endswith("OUTSIDE_ROOT"):
        return 403
    if code.endswith("BINARY"):
        return 415
    if code.endswith("TOO_LARGE"):
        return 413
    if code.endswith(("REQUIRED", "BAD_INPUT")):
        return 400
    # ``state == "EMPTY"`` is NOT a success here: on an ok:false payload it
    # means there is no folder to read (no active project, or the active
    # project's recorded folders vanished). A 200 would render an empty tree
    # that reads as "this folder is empty", which is a different fact.
    return 502


def _entry(row: Dict[str, Any]) -> Dict[str, Any]:
    """One adapter entry → upstream's browser entry."""
    kind = str(row.get("type") or "")
    out: Dict[str, Any] = {
        "name": row.get("name"),
        "path": row.get("path"),
        "type": kind,
        "is_dir": kind == "dir",
        "size": row.get("size"),
        # The adapter already serializes this as a string, exactly as
        # ``_browser_timestamp_ns`` does upstream.
        "mtime_ns": row.get("mtime_ns"),
        # Qubit's stat carries no birth time; upstream emits null in that case.
        "birthtime_ns": None,
        "workspace_sort_rank": int(row.get("sort_rank") or 3),
    }
    if kind == "symlink":
        # ``target_outside_workspace`` is the one disclosure the adapter makes
        # and upstream makes the same one. No resolved target is ever emitted
        # by Qubit for an escaping link, and none is added here.
        out["target_outside_workspace"] = bool(
            row.get("target_outside_workspace"))
    return out


def _signature(path: str, entries: list) -> str:
    """A change signature over the exact payload (what upstream's is for).

    Upstream's ``dir_signature`` hashes the entry set so the client can skip a
    re-render when nothing moved. This hashes the rows the client will receive,
    so the client's comparison is sound by construction.
    """
    digest = hashlib.sha1()
    digest.update(str(path or "").encode("utf-8", "replace"))
    for row in entries:
        digest.update(json.dumps(row, sort_keys=True, default=str)
                      .encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()[:32]


def workspace_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/workspace/list — one folder, confined, read-only.

    Query: ``path`` (folder to list; empty = the active project folder),
    ``workspace`` (the root from a previous listing; optional), ``session_id``
    (upstream's key — accepted, and only used to explain an EMPTY answer).
    """
    path = str_param(q, "path")
    if path in (".", "./"):
        # Upstream's default rel is ".", which means "the workspace root". The
        # Qubit adapter's default is that same root, so translate rather than
        # treat "." as a parent-relative path.
        path = ""
    try:
        AW = _adapter()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"the confined workspace adapter is not available: "
                            f"{type(exc).__name__}: {exc}")
    try:
        got = AW.list_tree(cfg, path)
    except Exception as exc:  # noqa: BLE001 - adapters do not raise; a bug must not 500 bare
        return 502, {"ok": False, "state": "FAILED",
                     "code": "WORKSPACE_LIST_FAILED",
                     "detail": f"list_tree raised: {type(exc).__name__}: {exc}",
                     "entries": [], "upstream": UPSTREAM_PIN}
    if not isinstance(got, dict):
        return 502, {"ok": False, "state": "FAILED",
                     "code": "WORKSPACE_LIST_FAILED",
                     "detail": "the adapter returned "
                               f"{type(got).__name__} where a dict is contracted",
                     "entries": []}
    entries = [_entry(r) for r in (got.get("entries") or [])
               if isinstance(r, dict)]
    rel = str(got.get("path") or "") or "."
    out: Dict[str, Any] = {
        "entries": entries,
        "signature": _signature(rel, entries),
        "path": rel,
        "workspace": str(got.get("workspace") or ""),
        # Qubit never silently re-binds a stale workspace onto a different
        # folder: an unusable project is an EMPTY answer naming the reason, not
        # a substitution. So the recovery flag is always false.
        "workspace_recovered": False,
        # Additive, and true facts the ported tree pane uses.
        "truncated": bool(got.get("truncated")),
        "shown": len(entries),
        "roots": got.get("roots") or [],
        "read_only": True,
        "upstream": UPSTREAM_PIN,
    }
    status = _status_for(got)
    if got.get("ok") is not True:
        out["ok"] = False
        out["code"] = got.get("code")
        out["detail"] = got.get("detail")
        out["state"] = got.get("state")
    else:
        out["ok"] = True
    return status, out


def workspace_read(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/workspace/read — one text file, confined, redacted.

    Query: ``path`` (workspace-relative, as ``list`` returned it), ``workspace``
    (its root), ``max_chars``.
    """
    path = str_param(q, "path")
    if not path:
        return 400, {"error": "path is required", "code": "FILES_PATH_REQUIRED"}
    workspace = str_param(q, "workspace")
    max_chars = int_param(str_param(q, "max_chars", default="200000"),
                          200000, 1000, 2000000)
    try:
        AW = _adapter()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"the confined workspace adapter is not available: "
                            f"{type(exc).__name__}: {exc}")
    try:
        got = AW.preview_file(cfg, path, workspace=workspace,
                              max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001
        return 502, {"ok": False, "state": "FAILED",
                     "code": "WORKSPACE_READ_FAILED",
                     "detail": f"preview_file raised: {type(exc).__name__}: {exc}",
                     "upstream": UPSTREAM_PIN}
    if not isinstance(got, dict):
        return 502, {"ok": False, "state": "FAILED",
                     "code": "WORKSPACE_READ_FAILED",
                     "detail": "the adapter returned "
                               f"{type(got).__name__} where a dict is contracted"}
    status = _status_for(got)
    if got.get("ok") is not True:
        return status, {
            "ok": False, "error": str(got.get("detail") or "cannot read file"),
            "code": got.get("code"), "state": got.get("state"),
            "path": path, "upstream": UPSTREAM_PIN,
        }
    text = str(got.get("text") or "")
    return 200, {
        # Upstream's four keys, exactly.
        "path": path,
        "content": text,
        "size": int(got.get("size") or 0),
        "lines": int(got.get("lines") or 0),
        # Additive and load-bearing for honesty: a clipped preview says so, and
        # the text is labelled untrusted so the pane renders it as text.
        "clipped": bool(got.get("clipped")),
        "trust": str(got.get("trust") or "untrusted-file-text"),
        "name": got.get("name"),
        "upstream": UPSTREAM_PIN,
    }


def workspace_save(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/workspace/save — GAP: writes are brokered in Qubit."""
    path = str(body_of(b).get("path") or "")
    return gap("BROKERED_WRITE",
               detail=(f"saving {path or '<path>'} must run as a brokered, "
                       f"confirmed action with an audit receipt."),
               path=path,
               qubit_counterpart="voicebridge.file_patch via voicebridge.broker")


def workspace_upload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/workspace/upload — GAP: writes are brokered in Qubit."""
    return gap("BROKERED_WRITE",
               detail=("uploading into a project folder must run as a brokered, "
                       "confirmed action with an audit receipt."),
               qubit_counterpart="/files upload (broker-backed) or "
                                 "voicebridge.file_patch via voicebridge.broker")


HANDLERS = {
    "h_wb_workspace_list": workspace_list,
    "h_wb_workspace_read": workspace_read,
    "h_wb_workspace_save": workspace_save,
    "h_wb_workspace_upload": workspace_upload,
}


def routes() -> list:
    return [
        ("GET", r"/api/wb/workspace/list", "h_wb_workspace_list"),
        ("GET", r"/api/wb/workspace/read", "h_wb_workspace_read"),
        ("POST", r"/api/wb/workspace/save", "h_wb_workspace_save"),
        ("POST", r"/api/wb/workspace/upload", "h_wb_workspace_upload"),
    ]
