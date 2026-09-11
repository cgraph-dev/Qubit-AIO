"""Packet 8U — brokered, confined ``apply_patch`` with atomic rollback.

Qubit files are read-only by default (``voicebridge.files``). The audit gap
(OpenClaw ``apply_patch``) is closed with the SMALLEST honest write path: a
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
* **Redacted** — receipts carry hashes/byte counts/metadata and a redacted
  preview, never the secret-shaped payload.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
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

MAX_PATCH_BYTES = 256 * 1024
MAX_PATCH_LINES = 5000
MAX_HUNKS = 500
MAX_LINE_CHARS = 10000
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_CHARS = 2000
MAX_PATH_CHARS = 4096

BACKUP_VERSION = 1

_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:[ \t].*)?$")
_NO_NEWLINE = "\\ No newline at end of file"


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
                   project_root: str = "") -> Path:
    """Resolve ``target_path`` strictly inside an approved project root.

    Symlinks are fully followed; the resolved path (and its parent) must stay
    inside an approved root, and the final component may not be a symlink.
    ``project_root``, when given, must itself already be approved and narrows
    the allowed root.
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
    if target.exists() and not target.is_file():
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


def _apply_hunks(original_text: str, hunks: List[Dict[str, Any]]
                 ) -> Tuple[str, int, int]:
    orig = original_text.splitlines()
    trailing_newline = original_text.endswith("\n")
    out: List[str] = []
    cursor = 0
    added = removed = 0
    for h in hunks:
        if h["old_count"] == 0:
            pos = h["old_start"]
        else:
            pos = h["old_start"] - 1
        if pos < cursor or pos > len(orig):
            raise FilePatchError(CONFLICT_CODE, "hunk is out of range")
        out.extend(orig[cursor:pos])
        idx = pos
        for kind, body in h["ops"]:
            if kind == "ctx":
                if idx >= len(orig) or orig[idx] != body:
                    raise FilePatchError(
                        CONFLICT_CODE, "context line does not match target")
                out.append(body)
                idx += 1
            elif kind == "del":
                if idx >= len(orig) or orig[idx] != body:
                    raise FilePatchError(
                        CONFLICT_CODE, "deleted line does not match target")
                idx += 1
                removed += 1
            else:
                out.append(body)
                added += 1
        cursor = idx
    out.extend(orig[cursor:])
    ends_newline = trailing_newline
    if hunks and hunks[-1]["no_newline"] and cursor == len(orig):
        ends_newline = False
    result = "\n".join(out)
    if out and ends_newline:
        result += "\n"
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
    """Broker capability id for wiring (see report; not registered here)."""
    return "local.apply_patch"
