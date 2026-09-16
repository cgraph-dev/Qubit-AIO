"""Packet 8U — scoped project-folder inspection (read-only).

Qubit reads files only inside approved roots: the folders registered on
Qubit projects (plus an explicit CLI path that must still resolve inside
one). Everything else — absolute escapes, ``..`` traversal, symlinks
pointing outside, sockets/FIFOs/devices, oversized or binary files — is
refused with a typed code. File bytes are redacted before they leave
this module, and page/file text is untrusted evidence.

Qubit implements scoped file read/write/search with write guards
(``tools/file_operations*.py``, ``tools/file_tools_write_guards.py``);
Qubit implements the read half natively (writes stay behind the brokered
local-script/isolated-execution paths). Provenance and the deliberate
divergence are recorded in ``docs/UPSTREAM_REUSE.md``.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Dict, List

CONFIRM_CODE = "FILES_CONFIRM_REQUIRED"
NOT_FOUND_CODE = "FILES_NOT_FOUND"
BAD_INPUT_CODE = "FILES_BAD_INPUT"
OUTSIDE_ROOT_CODE = "FILES_OUTSIDE_ROOT"
TOO_LARGE_CODE = "FILES_TOO_LARGE"
BINARY_CODE = "FILES_BINARY"
IO_CODE = "FILES_IO"

MAX_FILE_BYTES = 1024 * 1024  # 1 MiB
MAX_LIST_ENTRIES = 200
MAX_READ_CHARS = 20000
MAX_DEPTH = 4


class FilesError(Exception):
    """Typed files failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def redact_spec(text: Any) -> str:
    try:
        from .redaction import redact_text as _redact
        return _redact(str(text or ""))
    except Exception:
        return "?"


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"files.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


# ------------------------------------------------------------------ roots
def approved_roots(cfg: Any) -> List[Path]:
    """Project-registered folders (resolved, deduplicated)."""
    from . import projects as PJ

    roots: List[Path] = []
    try:
        listing = PJ.list_projects(cfg, include_archived=False)
    except Exception:
        return []
    for rec in listing.get("projects", []):
        for folder in rec.get("folders", []) or []:
            try:
                resolved = Path(folder).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if resolved.is_dir() and resolved not in roots:
                roots.append(resolved)
    return roots


def _resolve_inside(cfg: Any, path: str) -> Path:
    """Resolve ``path`` strictly inside an approved root (or raise).

    Relative paths resolve against the active project's primary folder
    (voice-friendly: "read notes.md" reads it in the active project);
    absolute paths must land inside a project-registered folder.
    """
    text = str(path or "").strip()
    if not text or len(text) > 4096 or "\x00" in text:
        raise FilesError(BAD_INPUT_CODE, "a folder or file path is required")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        from . import projects as PJ

        active = PJ.active_project(cfg)
        if active.get("state") != "OK" or not active.get("project"):
            raise FilesError(BAD_INPUT_CODE,
                             "relative paths need an active project; pass an "
                             "absolute path or set one with "
                             "`qubit project use <slug> --confirm`")
        folders = active["project"].get("folders", []) or []
        if not folders:
            raise FilesError(BAD_INPUT_CODE,
                             "the active project has no folders")
        candidate = Path(str(folders[0])) / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise FilesError(IO_CODE, f"cannot resolve path ({exc})")
    if not resolved.exists() and not resolved.parent.exists():
        raise FilesError(NOT_FOUND_CODE, "no such file or folder")
    for root in approved_roots(cfg):
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise FilesError(OUTSIDE_ROOT_CODE,
                     "path is outside the approved project folders; "
                     "register it with `qubit project add-folder` first")


def _refuse_special(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise FilesError(IO_CODE, f"cannot stat path ({exc})")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise FilesError(BAD_INPUT_CODE,
                         "only regular files and folders may be inspected")


def _is_binary(sample: bytes) -> bool:
    return b"\x00" in sample


# -------------------------------------------------------------------- ops
def _public_dir(entry: Path, root: Path) -> Dict[str, Any]:
    try:
        is_dir = entry.is_dir()
        size = 0 if is_dir else entry.stat().st_size
    except OSError:
        is_dir, size = False, 0
    return {"name": entry.name[:120],
            "rel": redact_spec(str(entry.relative_to(root)))[:300],
            "dir": bool(is_dir), "size": int(size)}


def list_dir(cfg: Any, path: str, *, depth: int = 1) -> Dict[str, Any]:
    """List a folder inside the approved roots (read-only, bounded)."""
    target = _resolve_inside(cfg, path)
    _refuse_special(target)
    if not target.is_dir():
        raise FilesError(BAD_INPUT_CODE, "not a folder; use `files read`")
    depth = max(1, min(int(depth or 1), MAX_DEPTH))
    root = target
    entries: List[Dict[str, Any]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        level = Path(dirpath).relative_to(root)
        if len(level.parts) > depth:
            dirnames[:] = []
            continue
        dirnames.sort()
        if level.parts:
            entries.append(_public_dir(Path(dirpath), root))
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
        for name in sorted(filenames):
            entries.append(_public_dir(Path(dirpath) / name, root))
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
        if truncated:
            break
        if len(level.parts) >= depth:
            dirnames[:] = []
    _audit("list", "qubit", {"path": redact_spec(str(root))[:120],
                             "count": str(len(entries))})
    return {"state": "OK", "folder": redact_spec(str(root))[:300],
            "entries": entries, "truncated": truncated}


def read_file(cfg: Any, path: str, *, max_chars: int = MAX_READ_CHARS
              ) -> Dict[str, Any]:
    """Read a text file inside the approved roots (bounded, redacted)."""
    target = _resolve_inside(cfg, path)
    _refuse_special(target)
    if target.is_dir():
        raise FilesError(BAD_INPUT_CODE, "not a file; use `files list`")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise FilesError(IO_CODE, f"cannot stat file ({exc})")
    if size > MAX_FILE_BYTES:
        raise FilesError(TOO_LARGE_CODE,
                         f"file is {size} bytes (limit {MAX_FILE_BYTES})")
    try:
        with open(target, "rb") as fh:
            sample = fh.read(min(size, 1 << 20) + 1)
    except OSError as exc:
        raise FilesError(IO_CODE, f"cannot read file ({exc})")
    if _is_binary(sample[:8192]):
        raise FilesError(BINARY_CODE, "binary file; content not shown")
    try:
        text = sample.decode("utf-8", "replace")
    except Exception as exc:
        raise FilesError(IO_CODE, f"cannot decode file ({exc})")
    limit = max(1, min(int(max_chars or MAX_READ_CHARS), MAX_READ_CHARS))
    clipped = len(text) > limit
    body = redact_spec(text[:limit])
    _audit("read", "qubit", {"path": redact_spec(str(target))[:120]})
    return {"state": "OK", "file": redact_spec(str(target))[:300],
            "size": size, "clipped": clipped, "text": body,
            "trust": "untrusted-file-text"}


def inspect(cfg: Any, path: str = "") -> Dict[str, Any]:
    """Inspect a folder (listing) or file (metadata + head).

    Empty ``path`` inspects the active project's primary folder ("this
    project folder" for voice); anything else must resolve inside the
    approved roots.
    """
    if not str(path or "").strip():
        from . import projects as PJ

        active = PJ.active_project(cfg)
        if active.get("state") != "OK" or not active.get("project"):
            return {"state": "EMPTY", "code": "NO_ACTIVE_PROJECT",
                    "detail": "no active project; set one with "
                              "`qubit project use <slug> --confirm`, or pass "
                              "an explicit project folder path",
                    "owner": "qubit-policy",
                    "rollback": "nothing to inspect; nothing to roll back"}
        folders = active["project"].get("folders", []) or []
        if not folders:
            return {"state": "EMPTY", "code": "PROJECT_HAS_NO_FOLDERS",
                    "detail": "the active project has no folders",
                    "owner": "qubit-policy",
                    "rollback": "nothing to inspect; nothing to roll back"}
        path = str(folders[0])
    target = _resolve_inside(cfg, path)
    _refuse_special(target)
    if target.is_dir():
        return list_dir(cfg, str(target))
    meta = read_file(cfg, str(target), max_chars=4000)
    meta["head_only"] = True
    return meta
