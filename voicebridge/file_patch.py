"""Packet 8U — brokered, confined ``apply_patch`` with atomic rollback.

Qubit files are read-only by default (``voicebridge.files``). The audit gap
(Qubit ``apply_patch``) is closed with the SMALLEST honest write path: a
single text file inside an approved project folder, patched by a safe
unified-diff subset, written atomically, and reversible from a durable
backup. It reuses the confinement, confirmation, audit, and redaction stack
rather than inventing a new one:

* **Confinement gate** — the shared
  :func:`voicebridge.opencode.confinement.confinement_available` probe must be
  OK. A platform without the confinement primitive returns typed
  ``FILE_PATCH_CONFINEMENT_UNAVAILABLE``; there is NO host-write fallback.
* **Path confinement** — the target resolves under an approved project root
  with ``realpath`` (symlinks followed) and must stay inside it; the final
  component may not be a symlink. ``..`` traversal and symlink escapes are
  typed refusals.
* **Confirmation** — ``confirmed=False`` (the default) refuses with typed
  ``FILE_PATCH_CONFIRM_REQUIRED`` before anything is touched. No model/
  automation path can self-confirm.
* **E-stop** — the durable emergency-stop / global-disable latch (the same
  :class:`PowerControlStore` the dashboard/voice/broker observe) refuses with
  typed ``FILE_PATCH_ESTOP``.
* **No code execution** — the patch is parsed as text with a strict regex
  subset (context/insert/delete hunks plus the no-newline marker); nothing is
  eval'd, imported, or shell'd, and no ``shell=True`` path exists here.
* **Bounded** — hard caps on patch bytes/lines/hunks, line length, and the
  resulting file size.
* **Atomic + reversible** — original bytes are written to a 0600 backup with
  fsync, then the new content is written via temp-file + fsync + ``os.replace``
  + directory fsync. :func:`rollback` restores the exact prior bytes and is
  idempotent.
* **Line-ending preserving** — untouched and context lines keep their original
  terminator (``\\r\\n`` / ``\\n`` / ``\\r``), added lines use the file's
  dominant ending, and a missing final newline stays missing
  (:func:`detect_line_separator`). A CRLF file is never silently rewritten to
  LF.
* **Redacted** — receipts carry hashes/byte counts/metadata and a redacted
  preview, never the secret-shaped payload.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONFIRM_CODE = "FILE_PATCH_CONFIRM_REQUIRED"
ESTOP_CODE = "FILE_PATCH_ESTOP"
OUTSIDE_ROOT_CODE = "FILE_PATCH_OUTSIDE_ROOT"
BAD_INPUT_CODE = "FILE_PATCH_BAD_INPUT"
NOT_FOUND_CODE = "FILE_PATCH_NOT_FOUND"
PARSE_CODE = "FILE_PATCH_PARSE"
CONFLICT_CODE = "FILE_PATCH_CONFLICT"
TOO_LARGE_CODE = "FILE_PATCH_TOO_LARGE"
TOO_MANY_LINES_CODE = "FILE_PATCH_TOO_MANY_LINES"
CONFINEMENT_CODE = "FILE_PATCH_CONFINEMENT_UNAVAILABLE"
IO_CODE = "FILE_PATCH_IO"
BACKUP_NOT_FOUND_CODE = "FILE_PATCH_BACKUP_NOT_FOUND"
ROLLBACK_CODE = "FILE_PATCH_ROLLBACK"
DELETE_ROOT_CODE = "FILE_PATCH_DELETE_ROOT"
DELETE_NOT_EMPTY_CODE = "FILE_PATCH_DELETE_NOT_EMPTY"
DELETE_ARCHIVE_CODE = "FILE_PATCH_DELETE_ARCHIVE"

MAX_PATCH_BYTES = 256 * 1024
MAX_PATCH_LINES = 5000
MAX_HUNKS = 500
MAX_LINE_CHARS = 10000
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_CHARS = 2000
MAX_PATH_CHARS = 4096
MAX_DELETE_BYTES = 1024 * 1024 * 1024
MAX_DELETE_ENTRIES = 100_000
# Binary upload ceiling, mirroring the upstream managed-files cap
# (``hermes_cli/web_server.py:821`` ``_MANAGED_FILE_MAX_BYTES = 100 * 1024 *
# 1024``) that ``POST /api/files/upload`` / ``/api/files/upload-stream`` enforce
# (``hermes_cli/web_routers/files.py:280,531``).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

BACKUP_VERSION = 1
DELETE_ARCHIVE_VERSION = 1

_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:[ \t].*)?$")
_NO_NEWLINE = "\\ No newline at end of file"

# Line endings Qubit preserves on the write path. ``\r\n`` is matched before
# the single-character forms so a CRLF never counts as a bare CR + LF.
_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
DEFAULT_LINE_SEPARATOR = "\n"
LINE_SEPARATORS = ("\r\n", "\n", "\r")


class FilePatchError(Exception):
    """Typed file-patch failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------ helpers
def _redact(text: Any) -> str:
    from .redaction import redact_text

    return redact_text(str(text if text is not None else ""))


def redact_spec(text: Any) -> str:
    """Redact a user-supplied path/spec before echoing it."""
    return _redact(text)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"file_patch.{op}", actor=_redact(actor),
                  **{k: str(v)[:240] for k, v in detail.items()})
    except Exception:
        pass


def confinement_available() -> Tuple[bool, str, str]:
    """Probe the shared Bubblewrap confinement primitive (fail closed)."""
    try:
        from .opencode.confinement import confinement_available as _probe

        return _probe()
    except Exception as exc:  # pragma: no cover - import guard
        return False, "", f"confinement probe failed: {exc}"[:160]


def estop_engaged(cfg: Any) -> bool:
    """True while the durable emergency-stop / global-disable latch is set."""
    try:
        from .plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_candidate(cfg: Any, target_path: str) -> Path:
    text = str(target_path or "").strip()
    if not text or len(text) > MAX_PATH_CHARS or "\x00" in text:
        raise FilePatchError(BAD_INPUT_CODE, "a target file path is required")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        from . import projects as PJ

        active = PJ.active_project(cfg)
        if active.get("state") != "OK" or not active.get("project"):
            raise FilePatchError(
                BAD_INPUT_CODE,
                "relative paths need an active project; pass an absolute "
                "path or set one with `qubit project use <slug> --confirm`")
        folders = active["project"].get("folders", []) or []
        if not folders:
            raise FilePatchError(BAD_INPUT_CODE,
                                 "the active project has no folders")
        candidate = Path(str(folders[0])) / candidate
    return candidate


def _approved_roots(cfg: Any) -> List[Path]:
    from .files import approved_roots

    return approved_roots(cfg)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_target(cfg: Any, target_path: str, *,
                   project_root: str = "",
                   allow_directory: bool = False) -> Path:
    """Resolve ``target_path`` strictly inside an approved project root.

    Symlinks are fully followed; the resolved path (and its parent) must stay
    inside an approved root, and the final component may not be a symlink.
    ``project_root``, when given, must itself already be approved and narrows
    the allowed root. A directory target is only accepted when
    ``allow_directory`` is true (the delete path); every other caller stays
    file-only.
    """
    candidate = _resolve_candidate(cfg, target_path)
    roots = _approved_roots(cfg)
    if not roots:
        raise FilePatchError(
            OUTSIDE_ROOT_CODE,
            "no approved project folders; register one with "
            "`qubit project add-folder` first")
    explicit: Optional[Path] = None
    raw_root = str(project_root or "").strip()
    if raw_root:
        try:
            explicit = Path(raw_root).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise FilePatchError(BAD_INPUT_CODE,
                                 f"cannot resolve project_root ({exc})")
        if not any(_is_inside(explicit, r) for r in roots):
            raise FilePatchError(
                OUTSIDE_ROOT_CODE,
                "project_root is not an approved project folder")
    try:
        parent_real = candidate.parent.resolve()
    except (OSError, RuntimeError) as exc:
        raise FilePatchError(BAD_INPUT_CODE, f"cannot resolve parent ({exc})")
    if not parent_real.is_dir():
        raise FilePatchError(NOT_FOUND_CODE, "target folder does not exist")
    target = parent_real / candidate.name
    try:
        real = target.resolve()
    except (OSError, RuntimeError) as exc:
        raise FilePatchError(BAD_INPUT_CODE, f"cannot resolve target ({exc})")
    allowed = [explicit] if explicit is not None else roots
    for root in allowed:
        if _is_inside(real, root) and _is_inside(parent_real, root):
            break
    else:
        raise FilePatchError(
            OUTSIDE_ROOT_CODE,
            "target is outside the approved project folders")
    if target.is_symlink():
        raise FilePatchError(
            OUTSIDE_ROOT_CODE,
            "target is a symlink; refusing to follow it for a write")
    if target.exists():
        if allow_directory:
            if not (target.is_file() or target.is_dir()):
                raise FilePatchError(
                    BAD_INPUT_CODE,
                    "target is not a regular file or folder")
        elif not target.is_file():
            raise FilePatchError(BAD_INPUT_CODE,
                                 "target is not a regular file")
    return target


# ------------------------------------------------------------- patch parsing
def _parse_patch(patch_text: str) -> List[Dict[str, Any]]:
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise FilePatchError(BAD_INPUT_CODE, "patch text is empty")
    encoded = patch_text.encode("utf-8", "surrogatepass")
    if len(encoded) > MAX_PATCH_BYTES:
        raise FilePatchError(
            TOO_LARGE_CODE,
            f"patch is {len(encoded)} bytes (limit {MAX_PATCH_BYTES})")
    lines = patch_text.splitlines()
    if len(lines) > MAX_PATCH_LINES:
        raise FilePatchError(
            TOO_MANY_LINES_CODE,
            f"patch has {len(lines)} lines (limit {MAX_PATCH_LINES})")
    for line in lines:
        if len(line) > MAX_LINE_CHARS:
            raise FilePatchError(
                TOO_LARGE_CODE,
                f"patch line exceeds {MAX_LINE_CHARS} chars")

    n = len(lines)
    i = 0
    seen_old = seen_new = 0
    while i < n and lines[i].startswith(("--- ", "+++ ")):
        if lines[i].startswith("--- "):
            seen_old += 1
        else:
            seen_new += 1
        i += 1
    if seen_old > 1 or seen_new > 1:
        raise FilePatchError(PARSE_CODE, "multiple file headers in one patch")

    hunks: List[Dict[str, Any]] = []
    while i < n:
        line = lines[i]
        m = _HUNK_RE.match(line)
        if not m:
            raise FilePatchError(
                PARSE_CODE, f"unexpected patch line: {line[:40]!r}")
        old_start = int(m.group(1))
        old_count = int(m.group(2)) if m.group(2) is not None else 1
        new_count = int(m.group(4)) if m.group(4) is not None else 1
        i += 1
        ops: List[Tuple[str, str]] = []
        seen_old_h = seen_new_h = 0
        no_newline = False
        while i < n and not lines[i].startswith("@@"):
            cl = lines[i]
            if cl == _NO_NEWLINE:
                no_newline = True
                i += 1
                continue
            if not cl:
                raise FilePatchError(PARSE_CODE, "empty line inside a hunk")
            tag, body = cl[0], cl[1:]
            if tag == " ":
                ops.append(("ctx", body))
                seen_old_h += 1
                seen_new_h += 1
            elif tag == "-":
                ops.append(("del", body))
                seen_old_h += 1
            elif tag == "+":
                ops.append(("add", body))
                seen_new_h += 1
            else:
                raise FilePatchError(
                    PARSE_CODE, f"bad hunk line tag {tag!r}")
            i += 1
        if seen_old_h != old_count or seen_new_h != new_count:
            raise FilePatchError(
                PARSE_CODE,
                f"hunk counts mismatch (old {seen_old_h}/{old_count}, "
                f"new {seen_new_h}/{new_count})")
        hunks.append({"old_start": old_start, "old_count": old_count,
                      "new_count": new_count, "ops": ops,
                      "no_newline": no_newline})
    if not hunks:
        raise FilePatchError(PARSE_CODE, "patch contains no hunks")
    if len(hunks) > MAX_HUNKS:
        raise FilePatchError(
            TOO_MANY_LINES_CODE, f"patch has {len(hunks)} hunks "
                                 f"(limit {MAX_HUNKS})")
    last_start = -1
    for h in hunks:
        if h["old_start"] < last_start:
            raise FilePatchError(PARSE_CODE, "hunks out of order")
        last_start = h["old_start"]
    return hunks


def detect_line_separator(text: str,
                          default: str = DEFAULT_LINE_SEPARATOR) -> str:
    """Return the dominant line ending in ``text`` (``\\r\\n`` / ``\\n`` / ``\\r``).

    Empty or single-line text with no terminator returns ``default`` so a new
    file always gets a conventional ``\\n``.
    """
    counts = {sep: 0 for sep in LINE_SEPARATORS}
    for match in _NEWLINE_RE.finditer(text or ""):
        counts[match.group(0)] = counts.get(match.group(0), 0) + 1
    best = max(LINE_SEPARATORS, key=lambda sep: counts.get(sep, 0))
    return best if counts.get(best, 0) else default


def _split_lines_with_sep(text: str) -> List[Tuple[str, str]]:
    """Split into ``(body, terminator)`` pairs, preserving each line's ending.

    The terminator is ``""`` only for a final line without a trailing newline.
    Empty input yields no lines (so a new file is not seeded with a blank one).
    """
    if not text:
        return []
    rows: List[Tuple[str, str]] = []
    cursor = 0
    for match in _NEWLINE_RE.finditer(text):
        rows.append((text[cursor:match.start()], match.group(0)))
        cursor = match.end()
    if cursor < len(text):
        rows.append((text[cursor:], ""))
    return rows


def _apply_hunks(original_text: str, hunks: List[Dict[str, Any]]
                 ) -> Tuple[str, int, int]:
    rows = _split_lines_with_sep(original_text)
    orig = [body for body, _sep in rows]
    seps = [sep for _body, sep in rows]
    # Added lines use the file's dominant ending; unchanged lines keep theirs
    # byte-for-byte so a CRLF (or mixed) file is never silently rewritten to LF.
    default_sep = detect_line_separator(original_text)
    trailing_newline = bool(original_text) and original_text.endswith(
        ("\n", "\r"))
    out: List[Tuple[str, str]] = []
    cursor = 0
    added = removed = 0
    for h in hunks:
        if h["old_count"] == 0:
            pos = h["old_start"]
        else:
            pos = h["old_start"] - 1
        if pos < cursor or pos > len(orig):
            raise FilePatchError(CONFLICT_CODE, "hunk is out of range")
        out.extend(zip(orig[cursor:pos], seps[cursor:pos]))
        idx = pos
        for kind, body in h["ops"]:
            if kind == "ctx":
                if idx >= len(orig) or orig[idx] != body:
                    raise FilePatchError(
                        CONFLICT_CODE, "context line does not match target")
                out.append((body, seps[idx]))
                idx += 1
            elif kind == "del":
                if idx >= len(orig) or orig[idx] != body:
                    raise FilePatchError(
                        CONFLICT_CODE, "deleted line does not match target")
                idx += 1
                removed += 1
            else:
                out.append((body, default_sep))
                added += 1
        cursor = idx
    out.extend(zip(orig[cursor:], seps[cursor:]))
    ends_newline = trailing_newline
    if hunks and hunks[-1]["no_newline"] and cursor == len(orig):
        ends_newline = False
    if out:
        body, sep = out[-1]
        # A kept final line owns its ending; an added/rewritten final line is
        # the only case that needs the terminator decided here.
        out[-1] = (body, (sep or default_sep)) if ends_newline else (body, "")
    result = "".join(body + sep for body, sep in out)
    if len(result.encode("utf-8", "surrogatepass")) > MAX_RESULT_BYTES:
        raise FilePatchError(
            TOO_LARGE_CODE,
            f"result exceeds {MAX_RESULT_BYTES} bytes")
    return result, added, removed


def _redacted_preview(patch_text: str) -> str:
    picked: List[str] = []
    for line in patch_text.splitlines():
        if line[:1] in ("+", "-") and not line.startswith(("+++", "---")):
            picked.append(line)
        if len(picked) >= 40:
            break
    return _redact("\n".join(picked))[:MAX_PREVIEW_CHARS]


# ------------------------------------------------------------------ backups
def _backup_dir(cfg: Any) -> Path:
    path = Path(cfg.state_dir()) / "file_patch_backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backup_id(target: Path) -> str:
    return _sha256(str(target).encode("utf-8"))[:16]


def _write_backup(cfg: Any, target: Path, existed: bool,
                  content: bytes, mode: Optional[int]) -> Path:
    record = {
        "version": BACKUP_VERSION,
        "target": str(target),
        "existed": bool(existed),
        "mode": None if mode is None else int(mode & 0o7777),
        "sha256": _sha256(content),
        "bytes": len(content),
        "created_at": time.time(),
        "content_b64": base64.b64encode(content).decode("ascii"),
    }
    payload = json.dumps(record, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    dest = _backup_dir(cfg) / (
        f"{target.name[:64]}.{_backup_id(target)}.{time.time_ns()}.bak.json")
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".bak-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
        _fsync_dir(dest.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def _latest_backup(cfg: Any, target: Path) -> Optional[Path]:
    token = _backup_id(target)
    candidates: List[Path] = []
    try:
        for entry in _backup_dir(cfg).iterdir():
            if entry.is_file() and entry.name.endswith(".bak.json") \
                    and f".{token}." in entry.name:
                candidates.append(entry)
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _load_backup(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            raw = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise FilePatchError(ROLLBACK_CODE,
                             f"cannot read backup ({exc})")
    if not isinstance(raw, dict) or raw.get("version") != BACKUP_VERSION:
        raise FilePatchError(ROLLBACK_CODE, "unsupported backup record")
    try:
        content = base64.b64decode(raw.get("content_b64", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise FilePatchError(ROLLBACK_CODE, f"corrupt backup ({exc})")
    if _sha256(content) != raw.get("sha256"):
        raise FilePatchError(ROLLBACK_CODE, "backup hash mismatch")
    raw["content"] = content
    return raw


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _atomic_write(path: Path, data: bytes,
                  mode: Optional[int] = None) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".qpatch-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600 if mode is None else (mode & 0o7777))
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -------------------------------------------------------------------- gates
def _require_gates(cfg: Any, confirmed: bool) -> Tuple[str, str]:
    if estop_engaged(cfg):
        raise FilePatchError(
            ESTOP_CODE,
            "emergency stop is latched; reset it before patching files")
    if not confirmed:
        raise FilePatchError(
            CONFIRM_CODE,
            "file patches are writes and require explicit confirmation")
    ok, _binary, reason = confinement_available()
    if not ok:
        raise FilePatchError(
            CONFINEMENT_CODE,
            f"confined writes unavailable: {reason}; refusing host write")
    return reason, _binary


# -------------------------------------------------------------------- apply
def apply_patch(cfg: Any, target_path: str, patch_text: str, *,
                requested_by: str = "cli:user", confirmed: bool = False,
                project_root: str = "") -> Dict[str, Any]:
    """Apply a bounded unified-diff to one confined file; return a receipt.

    Raises :class:`FilePatchError` (typed) for every refusal or failure.
    On success the prior bytes are recoverable with :func:`rollback`.
    """
    actor = str(requested_by or "unknown")[:120]
    confinement_reason, _binary = _require_gates(cfg, confirmed)
    target = resolve_target(cfg, target_path, project_root=project_root)

    existed = target.exists()
    mode: Optional[int] = None
    if existed:
        try:
            before = target.read_bytes()
            mode = target.stat().st_mode & 0o7777
        except OSError as exc:
            raise FilePatchError(IO_CODE, f"cannot read target ({exc})")
        if len(before) > MAX_RESULT_BYTES:
            raise FilePatchError(
                TOO_LARGE_CODE,
                f"target is {len(before)} bytes (limit {MAX_RESULT_BYTES})")
    else:
        before = b""
    try:
        original_text = before.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FilePatchError(BAD_INPUT_CODE,
                             f"target is not UTF-8 text ({exc})")

    hunks = _parse_patch(patch_text)
    after_text, added, removed = _apply_hunks(original_text, hunks)
    after = after_text.encode("utf-8")

    if after == before:
        receipt = _receipt("NO_CHANGE", target, actor, before, after,
                           added, removed, backup=None,
                           confinement=confinement_reason)
        _audit("noop", actor, {"target": str(target),
                               "before": receipt["before_sha256"]})
        return receipt

    backup_path = _write_backup(cfg, target, existed, before, mode)
    try:
        _atomic_write(target, after, mode)
    except OSError as exc:
        raise FilePatchError(IO_CODE, f"cannot write target ({exc})")

    receipt = _receipt("OK", target, actor, before, after, added, removed,
                       backup=backup_path, confinement=confinement_reason)
    receipt["preview"] = _redacted_preview(patch_text)
    _audit("apply", actor, {
        "target": str(target), "before": receipt["before_sha256"],
        "after": receipt["after_sha256"], "backup": str(backup_path),
        "added": str(added), "removed": str(removed)})
    return receipt


def mkdir_path(cfg: Any, target_path: str, *,
               requested_by: str = "cli:user", confirmed: bool = False,
               project_root: str = "") -> Dict[str, Any]:
    """Create one confined directory inside an approved project root.

    Uses the SAME gate order as :func:`apply_patch`: the durable emergency-stop
    / global-disable latch and the shared confinement probe are checked BEFORE
    the target is resolved or created, so a latched e-stop refuses even when a
    caller skipped the confirmed-write path. Without ``confirmed`` it raises
    typed ``FILE_PATCH_CONFIRM_REQUIRED``. An already-existing target, an
    out-of-root path, or a missing parent is a typed refusal that creates
    nothing. The created directory is reversible with ``rmdir``.
    """
    actor = str(requested_by or "unknown")[:120]
    confinement_reason, _binary = _require_gates(cfg, confirmed)
    target = resolve_target(cfg, target_path, project_root=project_root)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise FilePatchError(CONFLICT_CODE, "target already exists")
    except OSError as exc:
        raise FilePatchError(IO_CODE, f"cannot create directory ({exc})")
    _fsync_dir(target.parent)
    _audit("mkdir", actor, {"target": str(target)})
    return {
        "state": "OK",
        "code": "FILES_DIRECTORY_CREATED",
        "target": _redact(str(target))[:MAX_PATH_CHARS],
        "requested_by": _redact(actor)[:120],
        "created": True,
        "confinement": _redact(confinement_reason)[:160],
        "trust": "confined-directory-create",
        "rollback": "remove the created directory (`rmdir`) to undo",
    }


# -------------------------------------------------------------------- upload
def _upload_receipt(state: str, target: Path, actor: str, before: bytes,
                    after: bytes, backup: Optional[Path],
                    confinement: str) -> Dict[str, Any]:
    return {
        "state": state,
        "code": "FILES_UPLOADED" if state == "OK"
                else "FILES_UPLOAD_NO_CHANGE",
        "target": _redact(str(target))[:MAX_PATH_CHARS],
        "requested_by": _redact(actor)[:120],
        "before_sha256": _sha256(before),
        "after_sha256": _sha256(after),
        "bytes_before": len(before),
        "bytes_after": len(after),
        "backup": _redact(str(backup)) if backup is not None else "",
        "confinement": _redact(confinement)[:160],
        "trust": "untrusted-binary-content",
        "rollback": "call file_patch.rollback(..., backup=...) to restore "
                    "the prior bytes",
    }


def write_bytes(cfg: Any, target_path: str, data: bytes, *,
                requested_by: str = "cli:user", confirmed: bool = False,
                overwrite: bool = True,
                project_root: str = "") -> Dict[str, Any]:
    """Write raw bytes to one confined file; brokered and reversible.

    Ports the upstream managed-files binary upload — ``POST /api/files/upload``
    (JSON ``data_url``) and ``POST /api/files/upload-stream`` (multipart
    ``file``), reference ``hermes_cli/web_routers/files.py:493-565`` streaming
    into ``_resolve_managed_path`` under the ``_MANAGED_FILE_MAX_BYTES`` cap
    (``hermes_cli/web_server.py:821``) — onto Qubit's ONE confined-write path.
    It runs the SAME gate order as :func:`apply_patch` / :func:`mkdir_path` /
    :func:`delete_path`: the durable emergency-stop / global-disable latch and
    the shared confinement probe are checked BEFORE the target is resolved, and
    without ``confirmed`` it raises typed ``FILE_PATCH_CONFIRM_REQUIRED``.

    ``data`` is written verbatim (NUL bytes and non-UTF-8 sequences included);
    no text decode ever touches the payload. The target must resolve inside an
    approved project root (absolute escapes, ``..`` traversal, and a symlink
    final component are typed refusals). An existing file is only replaced when
    ``overwrite`` is true (typed ``FILE_PATCH_CONFLICT`` otherwise). The prior
    bytes are written to a durable 0600 backup first, then the payload is
    written via temp-file + fsync + ``os.replace`` + directory fsync, so the
    upload is reversible with :func:`rollback`.
    """
    actor = str(requested_by or "unknown")[:120]
    confinement_reason, _binary = _require_gates(cfg, confirmed)
    if not isinstance(data, (bytes, bytearray)):
        raise FilePatchError(BAD_INPUT_CODE, "binary content must be bytes")
    payload = bytes(data)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise FilePatchError(
            TOO_LARGE_CODE,
            f"upload is {len(payload)} bytes (limit {MAX_UPLOAD_BYTES})")
    target = resolve_target(cfg, target_path, project_root=project_root)

    existed = target.exists()
    mode: Optional[int] = None
    if existed:
        if not overwrite:
            raise FilePatchError(CONFLICT_CODE, "target already exists")
        try:
            before = target.read_bytes()
            mode = target.stat().st_mode & 0o7777
        except OSError as exc:
            raise FilePatchError(IO_CODE, f"cannot read target ({exc})")
    else:
        before = b""

    if existed and payload == before:
        receipt = _upload_receipt("NO_CHANGE", target, actor, before, payload,
                                  backup=None, confinement=confinement_reason)
        _audit("upload_noop", actor, {"target": str(target),
                                      "sha256": receipt["after_sha256"]})
        return receipt

    backup_path = _write_backup(cfg, target, existed, before, mode)
    try:
        _atomic_write(target, payload, mode)
    except OSError as exc:
        raise FilePatchError(IO_CODE, f"cannot write target ({exc})")

    receipt = _upload_receipt("OK", target, actor, before, payload,
                              backup=backup_path,
                              confinement=confinement_reason)
    _audit("upload", actor, {
        "target": str(target), "before": receipt["before_sha256"],
        "after": receipt["after_sha256"], "backup": str(backup_path),
        "bytes": str(len(payload))})
    return receipt


# -------------------------------------------------------------------- delete
def _delete_archive_dir(cfg: Any) -> Path:
    path = _backup_dir(cfg) / "deleted"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dir_stats(path: Path) -> Tuple[int, int]:
    """Bounded ``(entry_count, total_bytes)`` for a directory tree.

    Symlinks are counted by their own ``lstat`` size and never followed, so a
    link pointing outside an approved root cannot inflate the archive.
    """
    entries = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        entries += len(dirnames)
        for name in filenames:
            entries += 1
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return entries, total


def _archive_target(cfg: Any, target: Path, actor: str) -> Path:
    """Move a confined file/folder into the Qubit delete archive (reversible).

    The move is atomic where the archive and the project share a filesystem
    and a copy-then-remove otherwise; either way the bytes are preserved under
    the Qubit state dir, never destroyed, and a manifest records the original
    path so :func:`restore_deleted` can put it back.
    """
    dest_dir = _delete_archive_dir(cfg)
    stem = f"{target.name[:64]}.{_backup_id(target)}.{time.time_ns()}"
    suffix = ".dir" if target.is_dir() else ".file"
    dest = dest_dir / (stem + suffix)
    manifest = dest_dir / (stem + ".manifest.json")
    record = {
        "version": DELETE_ARCHIVE_VERSION,
        "kind": "directory" if target.is_dir() else "file",
        "target": str(target),
        "requested_by": _redact(actor)[:120],
        "deleted_at": time.time(),
    }
    payload = json.dumps(record, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    try:
        shutil.move(str(target), str(dest))
    except OSError as exc:
        raise FilePatchError(IO_CODE, f"cannot archive target ({exc})")
    try:
        fd, tmp = tempfile.mkstemp(dir=str(dest_dir), prefix=".manifest-",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, manifest)
            _fsync_dir(dest_dir)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise FilePatchError(IO_CODE, f"cannot record delete archive ({exc})")
    _fsync_dir(target.parent)
    return dest


def delete_path(cfg: Any, target_path: str, *,
                requested_by: str = "cli:user", confirmed: bool = False,
                recursive: bool = False,
                project_root: str = "") -> Dict[str, Any]:
    """Archive (never destroy) one confined file or folder; typed refusals.

    Runs the SAME gate order as :func:`apply_patch` / :func:`mkdir_path`: the
    durable emergency-stop / global-disable latch and the shared confinement
    probe are checked BEFORE the target is resolved. Without ``confirmed`` it
    raises typed ``FILE_PATCH_CONFIRM_REQUIRED``. The target must resolve
    inside an approved project root; absolute escapes, ``..`` traversal, and
    symlinks (final component) are typed refusals, as is deleting an approved
    root itself. A non-empty directory requires ``recursive=True``. The bytes
    are moved into ``<state_dir>/file_patch_backups/deleted/`` with a manifest,
    so the operation is reversible with :func:`restore_deleted`.
    """
    actor = str(requested_by or "unknown")[:120]
    confinement_reason, _binary = _require_gates(cfg, confirmed)
    roots = _approved_roots(cfg)
    # Refuse an approved root BEFORE the confinement resolver (which would
    # otherwise reject the root's outside-parent with OUTSIDE_ROOT); deleting a
    # registered project folder is never a confined file op.
    try:
        candidate_real = _resolve_candidate(cfg, target_path).resolve()
    except FilePatchError:
        raise
    except (OSError, RuntimeError) as exc:
        raise FilePatchError(BAD_INPUT_CODE, f"cannot resolve target ({exc})")
    if any(candidate_real == root for root in roots):
        raise FilePatchError(
            DELETE_ROOT_CODE,
            "refusing to delete an approved project root itself")
    target = resolve_target(cfg, target_path, project_root=project_root,
                            allow_directory=True)
    try:
        real = target.resolve()
    except (OSError, RuntimeError) as exc:
        raise FilePatchError(BAD_INPUT_CODE, f"cannot resolve target ({exc})")
    if any(real == root for root in roots):
        raise FilePatchError(
            DELETE_ROOT_CODE,
            "refusing to delete an approved project root itself")
    if not target.exists():
        raise FilePatchError(NOT_FOUND_CODE, "target does not exist")
    kind = "directory" if target.is_dir() else "file"
    if kind == "directory":
        entries, total = _dir_stats(target)
        if entries and not recursive:
            raise FilePatchError(
                DELETE_NOT_EMPTY_CODE,
                "directory is not empty; pass recursive=True to archive and "
                "delete it")
        if entries > MAX_DELETE_ENTRIES or total > MAX_DELETE_BYTES:
            raise FilePatchError(
                TOO_LARGE_CODE,
                f"target has {entries} entries / {total} bytes "
                f"(limit {MAX_DELETE_ENTRIES}/{MAX_DELETE_BYTES})")
    archive = _archive_target(cfg, target, actor)
    _audit("delete", actor, {"target": str(target), "kind": kind,
                             "archive": str(archive)})
    return {
        "state": "OK",
        "code": "FILES_DELETED",
        "target": _redact(str(target))[:MAX_PATH_CHARS],
        "requested_by": _redact(actor)[:120],
        "kind": kind,
        "archived_to": _redact(str(archive))[:MAX_PATH_CHARS],
        "confinement": _redact(confinement_reason)[:160],
        "trust": "confined-reversible-delete",
        "rollback": "call file_patch.restore_deleted(cfg, archived_to, "
                    "requested_by=..., confirmed=True) to restore",
    }


def _manifest_target(archive: Path) -> str:
    stem = archive.name
    for suffix in (".dir", ".file"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    manifest = archive.parent / (stem + ".manifest.json")
    if not manifest.is_file():
        return ""
    try:
        with open(manifest, "rb") as fh:
            record = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(record, dict):
        return ""
    return str(record.get("target", "") or "")


def restore_deleted(cfg: Any, archive_path: str, *,
                    target_path: str = "", requested_by: str = "cli:user",
                    confirmed: bool = False,
                    project_root: str = "") -> Dict[str, Any]:
    """Restore an archived delete back to its original confined path.

    ``archive_path`` must be a directory under the Qubit delete archive; the
    original target is read from the archive manifest unless ``target_path``
    is given. The restore runs the same e-stop / confirmation / confinement
    gates as the delete and refuses to overwrite an existing target.
    """
    actor = str(requested_by or "unknown")[:120]
    confinement_reason, _binary = _require_gates(cfg, confirmed)
    archive_root = _delete_archive_dir(cfg).resolve()
    try:
        archive = Path(str(archive_path or "")).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise FilePatchError(BAD_INPUT_CODE, f"cannot resolve archive ({exc})")
    if archive == archive_root or not _is_inside(archive, archive_root):
        raise FilePatchError(
            DELETE_ARCHIVE_CODE,
            "archive is not a Qubit delete archive under the state dir")
    if not archive.is_file() and not archive.is_dir():
        raise FilePatchError(NOT_FOUND_CODE, "delete archive not found")
    dest_text = str(target_path or "").strip() or _manifest_target(archive)
    if not dest_text:
        raise FilePatchError(
            BAD_INPUT_CODE,
            "no original target recorded; pass target_path explicitly")
    dest = resolve_target(cfg, dest_text, project_root=project_root,
                          allow_directory=True)
    if dest.exists():
        raise FilePatchError(CONFLICT_CODE,
                             "restore target already exists")
    try:
        shutil.move(str(archive), str(dest))
    except OSError as exc:
        raise FilePatchError(IO_CODE, f"cannot restore archive ({exc})")
    _fsync_dir(dest.parent)
    _audit("restore", actor, {"archive": str(archive), "target": str(dest)})
    return {
        "state": "OK",
        "code": "FILES_DELETE_RESTORED",
        "target": _redact(str(dest))[:MAX_PATH_CHARS],
        "restored_from": _redact(str(archive))[:MAX_PATH_CHARS],
        "requested_by": _redact(actor)[:120],
        "confinement": _redact(confinement_reason)[:160],
        "trust": "confined-reversible-delete",
    }


def _receipt(state: str, target: Path, actor: str, before: bytes,
             after: bytes, added: int, removed: int,
             backup: Optional[Path], confinement: str) -> Dict[str, Any]:
    code = ("FILE_PATCH_APPLIED" if state == "OK"
            else "FILE_PATCH_NO_CHANGE")
    return {
        "state": state,
        "code": code,
        "target": _redact(str(target))[:MAX_PATH_CHARS],
        "requested_by": _redact(actor)[:120],
        "before_sha256": _sha256(before),
        "after_sha256": _sha256(after),
        "bytes_before": len(before),
        "bytes_after": len(after),
        "lines_added": int(added),
        "lines_removed": int(removed),
        "backup": _redact(str(backup)) if backup is not None else "",
        "confinement": _redact(confinement)[:160],
        "trust": "untrusted-patch-text",
        "rollback": "call file_patch.rollback(..., backup=...) to restore "
                    "the prior bytes",
    }


# ----------------------------------------------------------------- rollback
def rollback(cfg: Any, target_path: str, *,
             backup: Optional[str] = None,
             requested_by: str = "cli:user",
             confirmed: bool = False,
             project_root: str = "") -> Dict[str, Any]:
    """Restore a target's prior bytes from a backup; idempotent and typed.

    When ``backup`` is omitted the newest backup for the resolved target is
    used. Restoring an already-restored file is a typed ``NO_CHANGE`` no-op.
    """
    actor = str(requested_by or "unknown")[:120]
    confinement_reason, _binary = _require_gates(cfg, confirmed)
    target = resolve_target(cfg, target_path, project_root=project_root)

    if backup:
        backup_path: Optional[Path] = Path(str(backup)).expanduser()
    else:
        backup_path = _latest_backup(cfg, target)
    if backup_path is None or not backup_path.is_file():
        raise FilePatchError(
            BACKUP_NOT_FOUND_CODE, "no backup found for this target")
    record = _load_backup(backup_path)
    if str(record.get("target")) != str(target):
        raise FilePatchError(
            ROLLBACK_CODE, "backup does not belong to the resolved target")
    restored = record["content"]
    existed = bool(record.get("existed"))
    mode = record.get("mode")
    mode = None if mode is None else int(mode)

    current = target.read_bytes() if target.exists() else None
    if existed and current is not None and current == restored:
        return _rollback_receipt("NO_CHANGE", target, actor, current,
                                 restored, backup_path, confinement_reason)
    if not existed and current is None:
        return _rollback_receipt("NO_CHANGE", target, actor, b"", restored,
                                 backup_path, confinement_reason)

    try:
        if existed:
            _atomic_write(target, restored, mode)
        elif target.exists():
            os.unlink(target)
            _fsync_dir(target.parent)
    except OSError as exc:
        raise FilePatchError(ROLLBACK_CODE, f"rollback failed ({exc})")

    after = target.read_bytes() if target.exists() else b""
    receipt = _rollback_receipt("OK", target, actor, current or b"",
                                after, backup_path, confinement_reason)
    _audit("rollback", actor, {
        "target": str(target), "before": receipt["before_sha256"],
        "after": receipt["after_sha256"], "backup": str(backup_path)})
    return receipt


def _rollback_receipt(state: str, target: Path, actor: str, before: bytes,
                      after: bytes, backup: Path,
                      confinement: str) -> Dict[str, Any]:
    return {
        "state": state,
        "code": "FILE_PATCH_ROLLED_BACK" if state == "OK"
                else "FILE_PATCH_NO_CHANGE",
        "target": _redact(str(target))[:MAX_PATH_CHARS],
        "requested_by": _redact(actor)[:120],
        "before_sha256": _sha256(before),
        "after_sha256": _sha256(after),
        "bytes_before": len(before),
        "bytes_after": len(after),
        "backup": _redact(str(backup)),
        "confinement": _redact(confinement)[:160],
        "trust": "untrusted-restored-content",
    }


def capability_name() -> str:
    """Broker capability id for the confirmed, reversible file patch."""
    return "local.apply_patch"


def mkdir_capability_name() -> str:
    """Broker capability id for confined directory creation."""
    return "local.files_mkdir"


def delete_capability_name() -> str:
    """Broker capability id for confined, reversible file/folder deletion."""
    return "local.files_delete"


def write_bytes_capability_name() -> str:
    """Broker capability id for the confined, reversible binary upload."""
    return "local.files_upload"
