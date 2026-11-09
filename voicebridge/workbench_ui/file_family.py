"""The ``/api/file/*`` + ``/api/files/*`` families — real Qubit file authority.

WHAT THE PORTED CLIENT ACTUALLY CALLS (quoted from ``static/``)
---------------------------------------------------------------------------
``ui.js:27563``   ``POST /api/file/create``       ``{session_id, path, content:''}``
``ui.js:27596``   ``POST /api/file/create-dir``   ``{session_id, path}``
``ui.js:27333``   ``POST /api/file/delete``       ``{session_id, path, recursive:true}``
``ui.js:27530``   ``POST /api/file/delete``       ``{session_id, path}``
``ui.js:26970``   ``POST /api/file/move``         ``{session_id, path, dest_dir}``
                  → reads ``data.new_path`` / ``data.old_path``
``ui.js:27160``   ``POST /api/file/rename``       ``{session_id, path, new_name}``
``workspace.js:1429`` ``POST /api/file/save``     ``{session_id, path, content}``
                  → reads ``saved.content`` / ``saved.editable`` /
                  ``saved.preview_kind``
``workspace.js:290``  ``GET /api/file?session_id=&path=`` → reads ``data.content``
``workspace.js:298``  ``GET /api/file/raw?...&inline=1|download=1`` — an
                  image/media/PDF/HTML/attachment URL the browser fetches
                  directly (so ``format``/unit-test bodies, not ``api()``)
``ui.js:26780``   ``POST /api/file/path``         ``{session_id, path}`` → ``r.path``
``ui.js:26767``   ``POST /api/file/reveal``       ``{session_id, path}``
``ui.js:26773``   ``POST /api/file/open-vscode``  ``{session_id, path}``

THE AUTHORITY, AND WHY NOTHING NEW IS INVENTED
---------------------------------------------------------------------------
* **Path confinement** is :mod:`voicebridge.file_patch` — the ONE brokered
  confined-write lane. It resolves the target with ``realpath`` inside an
  approved project root, refuses a symlink final component, refuses an
  out-of-root parent, and refuses an approved ROOT itself for a delete.
* **Gates** are the ones that lane already enforces and this module does not
  re-implement: the durable emergency-stop / global-disable latch, and the
  shared confinement primitive (``bwrap``) probe. ``confirmed=True`` is passed
  because the ported client runs its own confirm dialog before each mutation
  (``showConfirmDialog`` at ``ui.js:27330`` and ``27533``), which is the same
  two-key decision the dashboard's confirm gate records.
* **Reads** are :mod:`voicebridge.files` (``read_file`` / ``read_bytes``) —
  bounded, redacted, binary-refusing — through
  :mod:`voicebridge.workbench.adapters_workspace`, so a relative path keeps
  the workspace-root semantics the tree uses.
* **Deletes are reversible**: ``file_patch.delete_path`` archives into
  ``<state_dir>/file_patch_backups/deleted`` with a manifest; the rollback
  command travels in the receipt.

WHAT IS DELIBERATELY NOT HERE
---------------------------------------------------------------------------
``file/move`` and ``file/rename`` need a rename lane that ``file_patch`` does
not have, and adding one would mean a second write implementation beside the
brokered one. A move is copy + delete-archive with a different failure matrix
(half-moved trees, cross-device copies) and is refused, typed, rather than
faked with a shell ``mv``. ``POST /api/file/reveal`` and
``/api/file/open-vscode`` launch desktop programs; Qubit runs headless and
launching a GUI file manager on a server is not a capability this product
has, so ``open-vscode`` refuses and ``reveal`` refuses on a headless host or
answers only when a desktop session genuinely exists.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..workbench_shim import UPSTREAM_PIN

#: Upstream's body cap for a text save, mirrored so the refusal happens before
#: a giant body is buffered. ``voicebridge.file_patch.max_upload_bytes`` owns
#: the real enforced cap (100 MiB); this is the preview/save text bound.
MAX_TEXT_SAVE_BYTES = 8 * 1024 * 1024

#: A workspace path as the client writes it: relative, forward-slashed.
_BAD_NAME_RE = re.compile(r"[\x00/\\]")

#: Owner for every payload this module emits (audit + UI attribution).
_OWNER = "qubit-file-authority"


# ------------------------------------------------------------------ helpers
def _refusal(code: str, detail: str, *, endpoint: str, status: int = 0,
             counterpart: str = "", rollback: str = "nothing was changed; "
                                                   "nothing to roll back",
             **extra: Any) -> Tuple[int, Dict[str, Any]]:
    """The one refusal shape this family emits (typed, with a next step)."""
    return (status or _STATUS.get(code, 502)), {
        "ok": False,
        "state": "REFUSED",
        "code": code,
        "endpoint": endpoint,
        "detail": detail,
        "qubit_counterpart": counterpart or "Qubit Dashboard → Files",
        "owner": _OWNER,
        "rollback": rollback,
        "upstream": UPSTREAM_PIN,
        **extra,
    }


#: ``file_patch`` code -> HTTP status. The codes are the lane's own, not new
#: ones invented here: a caller sees the same code the CLI sees.
_STATUS: Dict[str, int] = {
    "FILE_PATCH_CONFIRM_REQUIRED": 409,
    "FILE_PATCH_ESTOP": 403,
    "FILE_PATCH_OUTSIDE_ROOT": 403,
    "FILE_PATCH_BAD_INPUT": 400,
    "FILE_PATCH_NOT_FOUND": 404,
    "FILE_PATCH_CONFLICT": 409,
    "FILE_PATCH_TOO_LARGE": 413,
    "FILE_PATCH_TOO_MANY_LINES": 413,
    "FILE_PATCH_CONFINEMENT_UNAVAILABLE": 503,
    "FILE_PATCH_DELETE_ROOT": 403,
    "FILE_PATCH_DELETE_NOT_EMPTY": 409,
    "FILE_PATCH_DELETE_ARCHIVE": 500,
    "FILE_PATCH_IO": 500,
    "FILES_OUTSIDE_ROOT": 403,
    "FILES_BAD_INPUT": 400,
    "FILES_NOT_FOUND": 404,
    "FILES_BINARY": 415,
    "FILES_TOO_LARGE": 413,
    "FILES_PATH_REQUIRED": 400,
    "FILES_IO": 500,
    # confinement / contract codes this module raises itself
    "WB_WORKSPACE_OUTSIDE": 403,
    "WB_NO_WORKSPACE_ROOTS": 409,
    "WB_FILE_PATH_REQUIRED": 400,
    "WB_FILE_BAD_PATH": 400,
    "WB_FILE_SESSION_REQUIRED": 400,
    "WB_NO_DESKTOP_SESSION": 501,
    "WB_BROKERED_WRITE": 403,
    "WB_CAPABILITY_UNBOUND": 501,
    "WB_FILE_RAW_REQUIRES_BYTES": 501,
}


def _patch_error(exc: Exception, *, endpoint: str) -> Tuple[int, Dict[str, Any]]:
    """Translate ``file_patch.FilePatchError`` (or ``files.FilesError``)."""
    code = str(getattr(exc, "code", "") or "") or "FILE_PATCH_IO"
    detail = str(getattr(exc, "detail", "") or exc)
    return _refusal(code, detail, endpoint=endpoint,
                    rollback="the request was refused before anything was "
                             "written; nothing to roll back")


def _patch():
    from .. import file_patch

    return file_patch


def _body(b: Any) -> Dict[str, Any]:
    return b if isinstance(b, dict) else {}


def _q(q: Any, *names: str) -> str:
    if not isinstance(q, dict):
        return ""
    for name in names:
        value = q.get(name)
        if value is not None and str(value).strip() != "":
            return str(value)
    return ""


def _flag(q: Any, *names: str) -> bool:
    return _q(q, *names).strip().lower() in ("1", "true", "yes", "on")


def _session_id(source: Dict[str, Any], q: Any) -> str:
    """The session a call names. Accepted and used for the audit line only.

    Qubit's confinement is project-scoped, not session-scoped
    (``adapters_workspace`` says so), so a session id never widens or narrows
    what a path may touch — but it is required by the contract, and a request
    that omits it is the client misbehaving, not a workspace answer.
    """
    return _q(source, "session_id", "sid") or _q(q, "session_id", "sid")


def _rel_path(source: Dict[str, Any], q: Any) -> str:
    return str(source.get("path") or "").strip() or _q(q, "path")


def _workspaces():
    from ..workbench import adapters_workspace

    return adapters_workspace


def _roots() -> List[Path]:
    from ..files import approved_roots

    return approved_roots(None)


def resolve_workspace_path(path: str) -> Tuple[Optional[Path], Optional[str]]:
    """Confine a workspace-relative (or absolute) path; ``(path, None)`` or
    ``(None, refusal_code_detail)``.

    A relative path is resolved against the FIRST registered project root — the
    folder the workbench tree opens on when no folder was named. ``.`` and
    ``./`` mean that same root (upstream's rel-dot default), never the process
    working directory. Anything that leaves a root is refused here, and
    ``file_patch.resolve_target`` re-checks it for a write.
    """
    text = str(path or "").strip()
    if not text:
        return None, "WB_FILE_PATH_REQUIRED"
    roots = _roots()
    if not roots:
        return None, "WB_NO_WORKSPACE_ROOTS"
    if Path(text).is_absolute():
        candidate = Path(text)
    else:
        rel = text
        if rel.startswith("./"):
            rel = rel[2:]
        candidate = roots[0] / rel
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"WB_FILE_BAD_PATH ({text!r} did not resolve: {exc})"
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved, None
    return None, (f"WB_WORKSPACE_OUTSIDE ({text!r} resolves to "
                  f"{str(resolved)!r}, which is outside every approved "
                  f"project folder)")


def workspace_rel(path_text: str) -> str:
    """The client-facing workspace-relative form of a confined absolute path."""
    resolved, why = resolve_workspace_path(path_text)
    if resolved is None:
        return str(path_text or "")
    for root in _roots():
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue
        return "" if str(rel) == "." else rel.as_posix()
    return str(path_text or "")


def _confined_or_refusal(path: str, *, endpoint: str
                         ) -> Tuple[Optional[Path], Optional[Tuple[int, Dict[str, Any]]]]:
    """``resolve_workspace_path`` + the typed refusal when it cannot be proven."""
    absolute, why = resolve_workspace_path(path)
    if absolute is not None:
        return absolute, None
    text = str(why or "WB_FILE_PATH_REQUIRED")
    code = "WB_WORKSPACE_OUTSIDE" if text.startswith("WB_WORKSPACE_OUTSIDE") \
        else "WB_FILE_BAD_PATH" if text.startswith("WB_FILE_BAD_PATH") \
        else "WB_NO_WORKSPACE_ROOTS" if text.startswith("WB_NO_WORKSPACE_ROOTS") \
        else "WB_FILE_PATH_REQUIRED"
    return None, _refusal(code, text, endpoint=endpoint)


def refusal(code: str, detail: str, *, endpoint: str, status: int = 0,
            counterpart: str = "", rollback: str = "nothing was changed; "
                                                  "nothing to roll back",
            **extra: Any) -> Tuple[int, Dict[str, Any]]:
    """Public form of :func:`_refusal`, for the listener's raw-bytes branch."""
    return _refusal(code, detail, endpoint=endpoint, status=status,
                    counterpart=counterpart, rollback=rollback, **extra)


def _invalid_name(name: Any) -> bool:
    text = str(name or "")
    return (not text.strip() or text in (".", "..")
            or bool(_BAD_NAME_RE.search(text)))


# -------------------------------------------------------------------- reads
def _preview_payload(got: Dict[str, Any], *, rel: str,
                     include_sha: bool = False) -> Dict[str, Any]:
    """``GET /api/file``'s answer — ``data.content`` is what the pane paints."""
    text = str(got.get("text") or "")
    out: Dict[str, Any] = {
        "path": rel,
        "content": text,
        "size": int(got.get("size") or 0),
        "lines": text.count("\n") + (1 if text else 0),
        # The pane's edit affordance keys off this. It is TRUE because
        # ``file/save`` exists and writes through the brokered lane, not
        # because anything about this read was editable.
        "editable": True,
        "clipped": bool(got.get("clipped")),
        "binary": bool(got.get("binary")),
        "trust": str(got.get("trust") or "untrusted-file-text"),
        "read_only": False,
        "confinement": "voicebridge.file_patch / voicebridge.files "
                       "(approved project roots)",
        "upstream": UPSTREAM_PIN,
    }
    if include_sha:
        out["sha256"] = got.get("sha256") or _sha256(str(got.get("file") or ""))
    return out


def _sha256(path_text: str) -> str:
    try:
        with open(path_text, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _read_text(cfg: Any, absolute: str, *, max_chars: int = 200000
               ) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, Dict[str, Any]]]]:
    """``adapters_workspace.preview_file`` for one confined absolute path."""
    try:
        got = _workspaces().preview_file(cfg, absolute, max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001 - a read fault is a typed answer
        return None, _refusal("FILES_IO",
                              f"the workspace reader raised: "
                              f"{type(exc).__name__}: {exc}",
                              endpoint="file")
    if got.get("ok") is not True:
        # The reader's OWN code (FILES_BINARY / FILES_TOO_LARGE /
        # FILES_OUTSIDE_ROOT) is preserved: they mean different things and must
        # not collapse into one "cannot read".
        return None, _refusal(str(got.get("code") or "FILES_IO"),
                              str(got.get("detail") or "the reader refused"),
                              endpoint="file")
    return got, None


def file_read(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``GET /api/file?session_id=&path=`` — one text file, confined, redacted."""
    source = _body(b)
    raw = _rel_path(source, q)
    absolute, err = _confined_or_refusal(raw, endpoint="file")
    if err is not None:
        return err
    if absolute.is_dir():
        return _refusal("FILES_BAD_INPUT",
                        f"{workspace_rel(str(absolute))!r} is a folder; list it "
                        f"with GET /api/list instead of reading it",
                        endpoint="file")
    got, err = _read_text(cfg, str(absolute))
    if err is not None:
        return err
    return 200, _preview_payload(got, rel=workspace_rel(str(absolute)))


def file_raw_check(cfg: Any, path: str) -> Tuple[Optional[Path],
                                                Optional[Tuple[int, Dict[str, Any]]]]:
    """Confine the raw/file download target, or the refusal to answer with."""
    absolute, err = _confined_or_refusal(path, endpoint="file/raw")
    if err is not None:
        return None, err
    if absolute.is_dir():
        return None, _refusal("FILES_BAD_INPUT",
                             f"{workspace_rel(str(absolute))!r} is a folder; "
                             f"file/raw serves files",
                             endpoint="file/raw")
    if not absolute.is_file():
        return None, _refusal("FILES_NOT_FOUND",
                              f"{workspace_rel(str(absolute))!r} does not "
                              f"exist", endpoint="file/raw")
    return absolute, None


# ------------------------------------------------------------------- writes
def file_create(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/create`` — one new file through the brokered lane.

    ``ui.js:27563`` sends ``{path, content:''}``; the content is honoured when
    it is a string, so the contract holds for a seed body too. Never overwrites:
    an existing target is ``FILE_PATCH_CONFLICT`` (the client opens the file
    afterwards and would otherwise silently clobber it).
    """
    source = _body(b)
    raw = _rel_path(source, q)
    if not _session_id(source, q):
        return _refusal("WB_FILE_SESSION_REQUIRED",
                        "POST /api/file/create needs session_id",
                        endpoint="file/create")
    absolute, err = _confined_or_refusal(raw, endpoint="file/create")
    if err is not None:
        return err
    content = source.get("content")
    data = content.encode("utf-8") if isinstance(content, str) else b""
    try:
        receipt = _patch().write_bytes(
            cfg, str(absolute), data, requested_by="workbench:user",
            confirmed=True, overwrite=False)
    except Exception as exc:  # noqa: BLE001
        return _patch_error(exc, endpoint="file/create")
    return 200, {
        "ok": True,
        "path": workspace_rel(str(absolute)),
        "created": True,
        "size": int(receipt.get("bytes_after") or 0),
        "sha256": receipt.get("after_sha256"),
        "receipt": _receipt_summary(receipt),
        "upstream": UPSTREAM_PIN,
    }


def file_create_dir(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/create-dir`` — one new folder (``mkdir``, non-recursive)."""
    source = _body(b)
    raw = _rel_path(source, q)
    if not _session_id(source, q):
        return _refusal("WB_FILE_SESSION_REQUIRED",
                        "POST /api/file/create-dir needs session_id",
                        endpoint="file/create-dir")
    absolute, err = _confined_or_refusal(raw, endpoint="file/create-dir")
    if err is not None:
        return err
    try:
        receipt = _patch().mkdir_path(
            cfg, str(absolute), requested_by="workbench:user", confirmed=True)
    except Exception as exc:  # noqa: BLE001
        return _patch_error(exc, endpoint="file/create-dir")
    return 200, {
        "ok": True,
        "path": workspace_rel(str(absolute)),
        "created": True,
        "receipt": _receipt_summary(receipt),
        "upstream": UPSTREAM_PIN,
    }


def file_delete(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/delete`` — archive-delete, reversible from the receipt.

    ``recursive`` comes straight from the client (``ui.js:27333`` sends it for a
    folder, ``27530`` omits it for a file), so a non-empty folder without it is
    the lane's own ``FILE_PATCH_DELETE_NOT_EMPTY`` refusal, not a silent tree
    wipe. An approved project ROOT is refused by the lane.
    """
    source = _body(b)
    raw = _rel_path(source, q)
    if not _session_id(source, q):
        return _refusal("WB_FILE_SESSION_REQUIRED",
                        "POST /api/file/delete needs session_id",
                        endpoint="file/delete")
    absolute, err = _confined_or_refusal(raw, endpoint="file/delete")
    if err is not None:
        return err
    recursive = source.get("recursive") is True or _flag(q, "recursive")
    rel = workspace_rel(str(absolute))
    try:
        receipt = _patch().delete_path(
            cfg, str(absolute), requested_by="workbench:user",
            confirmed=True, recursive=recursive)
    except Exception as exc:  # noqa: BLE001
        return _patch_error(exc, endpoint="file/delete")
    return 200, {
        "ok": True,
        "path": rel,
        "deleted": True,
        "kind": receipt.get("kind"),
        # What actually proves the delete is recoverable, not just the word.
        "archived_to": receipt.get("archived_to"),
        "receipt": _receipt_summary(receipt),
        "upstream": UPSTREAM_PIN,
    }


def file_save(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/save`` — overwrite one confined text file, reversibly.

    ``workspace.js:1429`` reads ``saved.content`` back (it caches it as the new
    baseline) and ``saved.editable``/``preview_kind`` for the next edit cycle.
    The prior bytes are backed up first, so the receipt carries the rollback.
    """
    source = _body(b)
    if not _session_id(source, q):
        return _refusal("WB_FILE_SESSION_REQUIRED",
                        "POST /api/file/save needs session_id",
                        endpoint="file/save")
    raw = _rel_path(source, q)
    absolute, err = _confined_or_refusal(raw, endpoint="file/save")
    if err is not None:
        return err
    content = source.get("content")
    if not isinstance(content, str):
        return _refusal("FILE_PATCH_BAD_INPUT",
                        "POST /api/file/save needs a string `content`; nothing "
                        "was written", endpoint="file/save")
    data = content.encode("utf-8")
    if len(data) > MAX_TEXT_SAVE_BYTES:
        return _refusal("FILE_PATCH_TOO_LARGE",
                        f"save body is {len(data)} bytes "
                        f"(limit {MAX_TEXT_SAVE_BYTES})", endpoint="file/save")
    rel = workspace_rel(str(absolute))
    try:
        receipt = _patch().write_bytes(
            cfg, str(absolute), data, requested_by="workbench:user",
            confirmed=True, overwrite=True)
    except Exception as exc:  # noqa: BLE001
        return _patch_error(exc, endpoint="file/save")
    return 200, {
        "ok": True,
        "path": rel,
        # The client's own view of what it just wrote.
        "content": content,
        "editable": True,
        "size": int(receipt.get("bytes_after") or 0),
        "receipt": _receipt_summary(receipt),
        "upstream": UPSTREAM_PIN,
    }


def file_move(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/move`` — no rename lane exists; refused, not faked.

    ``ui.js:26970`` sends ``{path, dest_dir}`` and then reads ``data.new_path``
    back to remap its caches. Qubit's confined-write lane
    (:mod:`voicebridge.file_patch`) has no rename primitive: a move is a copy
    plus a reversible delete-archive with its own failure matrix (cross-device
    copies, half-moved trees), and implementing it here would be a second write
    path beside the brokered one. The destination is still confined and named,
    so the user can see where it would have gone.
    """
    source = _body(b)
    src = _rel_path(source, q)
    dest_dir = str(source.get("dest_dir") or "").strip()
    src_abs, src_err = _confined_or_refusal(src, endpoint="file/move")
    if src_err is not None or src_abs is None:
        assert src_err is not None
        return src_err
    if dest_dir and dest_dir not in (".", "./"):
        dest_abs, dest_err = _confined_or_refusal(dest_dir, endpoint="file/move")
        if dest_err is not None or dest_abs is None:
            assert dest_err is not None
            return dest_err
        target = str(dest_abs / src_abs.name)
    else:
        target = str(src_abs.parent / src_abs.name)
    return _refusal(
        "WB_BROKERED_WRITE",
        f"moving {workspace_rel(str(src_abs))!r} to {workspace_rel(target)!r} "
        f"needs a rename primitive the brokered file lane does not have "
        f"(voicebridge.file_patch performs create/save/delete, each reversible "
        f"on its own). Nothing was moved; copy then delete through "
        f"file/save + file/delete, which are both reversible from their "
        f"receipts.",
        endpoint="file/move", status=403,
        counterpart="Qubit Dashboard → Files (per-file copy/delete receipts)")


def file_rename(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/rename`` — same missing primitive as ``file/move``."""
    source = _body(b)
    src = _rel_path(source, q)
    new_name = str(source.get("new_name") or source.get("name") or "").strip()
    src_abs, src_err = _confined_or_refusal(src, endpoint="file/rename")
    if src_err is not None or src_abs is None:
        assert src_err is not None
        return src_err
    if _invalid_name(new_name):
        return _refusal("FILE_PATCH_BAD_INPUT",
                        f"new_name {new_name!r} is not a single path segment",
                        endpoint="file/rename")
    target = str(src_abs.parent / new_name)
    return _refusal(
        "WB_BROKERED_WRITE",
        f"renaming {workspace_rel(str(src_abs))!r} to {workspace_rel(target)!r} "
        f"needs a rename primitive the brokered file lane does not have; "
        f"nothing was renamed.",
        endpoint="file/rename", status=403,
        counterpart="Qubit Dashboard → Files (per-file copy/delete receipts)")


def _receipt_summary(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """The audit half of a ``file_patch`` receipt, verbatim (never fabricated)."""
    keys = ("state", "code", "before_sha256", "after_sha256", "bytes_before",
            "bytes_after", "backup", "archived_to", "confinement",
            "requested_by", "kind")
    return {k: receipt.get(k) for k in keys if receipt.get(k) not in (None, "")}


# ------------------------------------------------ path / reveal / vscode
def file_path(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/path`` — the confined ABSOLUTE path (``r.path``).

    ``ui.js:26780`` copies this to the clipboard, so it is the real server-side
    path — but only after confinement proves it lives in an approved folder.
    """
    source = _body(b)
    raw = _rel_path(source, q)
    absolute, err = _confined_or_refusal(raw, endpoint="file/path")
    if err is not None:
        return err
    return 200, {
        "ok": True,
        "path": str(absolute),
        "rel_path": workspace_rel(str(absolute)),
        "is_dir": absolute.is_dir(),
        "upstream": UPSTREAM_PIN,
    }


def file_reveal(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/reveal`` — open a file manager, HONESTLY.

    Reveal-in-file-manager is a desktop action: it shells out to
    ``xdg-open``/``open``/``explorer`` on the host that runs the UI. Qubit's
    dashboard listener is a host-service; a server has no file manager, and
    even on a desktop it is not the browser's machine that would open it. The
    capability is therefore answered only where it truly exists — a graphical
    session on this host — and refused everywhere else with the path, which is
    the thing the user actually wanted to see.
    """
    source = _body(b)
    raw = _rel_path(source, q)
    absolute, err = _confined_or_refusal(raw, endpoint="file/reveal")
    if err is not None:
        return err
    display = "wayland-0" in os.environ.get("WAYLAND_DISPLAY", "") or bool(
        os.environ.get("DISPLAY"))
    opener = shutil.which("xdg-open")
    if not (display and opener and os.environ.get("XDG_CURRENT_DESKTOP")):
        return _refusal(
            "WB_NO_DESKTOP_SESSION",
            "reveal-in-file-manager has no authority on this host: Qubit's "
            "dashboard listener runs as a host service and this process has "
            f"no graphical desktop session (DISPLAY={'set' if os.environ.get('DISPLAY') else 'unset'}, "
            f"xdg-open={'found' if opener else 'missing'}). The file's real "
            f"path is {str(absolute)!r}.",
            endpoint="file/reveal",
            counterpart="POST /api/file/path (copy the absolute path), or the "
                        "Qubit Dashboard → Files pane",
            path=str(absolute))
    # A real desktop session exists; the ceiling here is the broker, not this
    # endpoint, so do not pretend to have launched anything.
    return _refusal(
        "WB_BROKERED_WRITE",
        f"a desktop file manager could open on this host, but launching "
        f"{str(absolute)!r} from the dashboard must go through the brokered "
        f"action lane with a receipt; nothing was launched.",
        endpoint="file/reveal",
        counterpart="Qubit Desktop app (reveal in OS file manager)",
        path=str(absolute))


def file_open_vscode(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/file/open-vscode`` — launch VS Code: no Qubit authority."""
    source = _body(b)
    raw = _rel_path(source, q)
    absolute, err = _confined_or_refusal(raw, endpoint="file/open-vscode")
    if err is not None:
        return err
    return _refusal(
        "WB_CAPABILITY_UNBOUND",
        "Qubit never launches an editor process from the dashboard. Opening "
        f"{str(absolute)!r} in an editor is a desktop-app action (the Qubit "
        "Desktop app's reveal/open integration), not something a host-service "
        "listener does.",
        endpoint="file/open-vscode",
        counterpart="Qubit Desktop app (open in editor), or the file's path "
                    f"from POST /api/file/path: {str(absolute)!r}")


# -------------------------------------------------------------- the routes
#: verb -> handler for ``/api/file/<verb>``. ``read`` and the bare ``file``
#: path are handled by the gateway (GET), everything else is a POST verb here.
WRITE_VERBS: Dict[str, Any] = {
    "create": file_create,
    "create-dir": file_create_dir,
    "delete": file_delete,
    "save": file_save,
    "path": file_path,
    "reveal": file_reveal,
    "open-vscode": file_open_vscode,
    # typed refusals — the lane has no rename primitive (see each docstring)
    "move": file_move,
    "rename": file_rename,
}
