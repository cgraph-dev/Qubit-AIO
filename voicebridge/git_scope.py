"""Qubit-scoped read-only git inspection (safe Qubit web_git equivalent).

Qubit exposed git state inside its files/projects dashboard. Qubit keeps
the workflow (status, log, diff, show) but only as bounded read-only
operations: fixed argv, no shell, project-root confinement, no network,
no checkout/commit/reset/push/fetch/pull/clone. Anything mutating is a
typed ``GIT_WRITE_REFUSED`` — use the brokered opencode worktree flow
for writes.

Shared by ``qubit git ...``, the dashboard ``git`` catalog group, and
the voice ``git_*`` entries (same backend, same typed results).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

MAX_OUTPUT_BYTES = 32768
TIMEOUT_S = 20
MAX_LOG_COUNT = 50

NOT_A_REPO_CODE = "GIT_NOT_A_REPO"
SCOPE_CODE = "GIT_SCOPE_REFUSED"
INPUT_CODE = "GIT_BAD_INPUT"
TIMEOUT_CODE = "GIT_TIMEOUT"
FAILED_CODE = "GIT_FAILED"
WRITE_REFUSED_CODE = "GIT_WRITE_REFUSED"

_REF_RE = re.compile(r"^(?:HEAD|HEAD~\d{1,3})$")


class GitError(Exception):
    """Typed git-scope failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"git.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def redact_spec(text: str) -> str:
    """Redact user-supplied paths/specs before echoing them."""
    return _redact(text)


def resolve_root(cfg: Any, root: str = "") -> Path:
    """Resolve the inspection root strictly inside an approved project folder.

    An empty root defaults to the active project's first folder (same
    default the files/voice surfaces use). Symlinks are fully resolved
    and must land inside a project-registered folder.
    """
    from . import files as FI

    text = str(root or "").strip()
    if not text:
        from . import projects as PJ

        active = PJ.active_project(cfg)
        if active.get("state") != "OK" or not active.get("project"):
            raise GitError(INPUT_CODE,
                           "no root given and no active project; pass "
                           "--root <folder> or set one with "
                           "`qubit project use <slug> --confirm`")
        folders = active["project"].get("folders", []) or []
        if not folders:
            raise GitError(INPUT_CODE, "the active project has no folders")
        text = str(folders[0])
    if len(text) > 4096 or "\x00" in text:
        raise GitError(INPUT_CODE, "bad root path")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise GitError(INPUT_CODE, "root must be an absolute path")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise GitError(INPUT_CODE, f"cannot resolve root ({exc})")
    for approved in FI.approved_roots(cfg):
        try:
            resolved.relative_to(approved)
            return resolved
        except ValueError:
            continue
    raise GitError(SCOPE_CODE,
                   "root is outside the approved project folders; "
                   "register it with `qubit project add-folder` first")


def _check_repo(root: Path) -> None:
    try:
        out = subprocess.run(
            ["git", "--no-pager", "-C", str(root),
             "rev-parse", "--is-inside-work-tree"],
            capture_output=True, timeout=TIMEOUT_S,
            env=_clean_env(), text=True)
    except FileNotFoundError as exc:
        raise GitError(FAILED_CODE, f"git executable not found ({exc})")
    except subprocess.TimeoutExpired:
        raise GitError(TIMEOUT_CODE, "git rev-parse timed out")
    if out.returncode != 0 or out.stdout.strip() != "true":
        raise GitError(NOT_A_REPO_CODE,
                       f"{root} is not inside a git work tree")


def _clean_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    return env


def _run(root: Path, argv: List[str]) -> tuple:
    try:
        proc = subprocess.run(
            ["git", "--no-pager", "-C", str(root), *argv],
            capture_output=True, timeout=TIMEOUT_S,
            env=_clean_env())
    except FileNotFoundError as exc:
        raise GitError(FAILED_CODE, f"git executable not found ({exc})")
    except subprocess.TimeoutExpired:
        raise GitError(TIMEOUT_CODE,
                       f"git {' '.join(argv[:2])} timed out")
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[:300]
        raise GitError(FAILED_CODE, f"git failed: {err or proc.returncode}")
    data = proc.stdout[:MAX_OUTPUT_BYTES + 1]
    truncated = len(proc.stdout) > MAX_OUTPUT_BYTES + 1
    text = data.decode("utf-8", "replace")
    return _redact(text), truncated


def _no_dash(value: str, what: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 1024 or text.startswith("-"):
        raise GitError(INPUT_CODE, f"bad {what}")
    return text


def status(cfg: Any, root: str = "",
           requested_by: str = "cli:user") -> Dict[str, Any]:
    """Porcelain work-tree status for one project root (read-only)."""
    resolved = resolve_root(cfg, root)
    _check_repo(resolved)
    text, truncated = _run(
        resolved, ["status", "--porcelain=v2", "--branch",
                   "--untracked-files=normal", "--", "."])
    _audit("status", requested_by, {"root": str(resolved)})
    return {"state": "OK", "root": str(resolved), "status": text,
            "truncated": truncated,
            "rollback": "read-only; nothing to roll back"}


def log(cfg: Any, root: str = "", limit: int = 10,
        requested_by: str = "cli:user") -> Dict[str, Any]:
    """Bounded commit log for one project root (read-only, no network)."""
    resolved = resolve_root(cfg, root)
    _check_repo(resolved)
    try:
        count = max(1, min(int(limit or 10), MAX_LOG_COUNT))
    except (TypeError, ValueError):
        raise GitError(INPUT_CODE, "limit must be 1..50")
    text, truncated = _run(
        resolved, ["log", f"--max-count={count}",
                   "--format=%H%x00%an%x00%ad%x00%s", "--date=iso", "--"])
    _audit("log", requested_by,
           {"root": str(resolved), "limit": str(count)})
    entries = []
    for line in text.splitlines():
        parts = line.split("\x00")
        if len(parts) == 4:
            entries.append({"commit": parts[0][:40], "author": parts[1][:80],
                            "date": parts[2][:32], "subject": parts[3][:200]})
    return {"state": "OK", "root": str(resolved), "entries": entries,
            "truncated": truncated,
            "rollback": "read-only; nothing to roll back"}


def diff(cfg: Any, root: str = "", path: str = "",
         requested_by: str = "cli:user") -> Dict[str, Any]:
    """Bounded unstaged diff, optionally for one in-root path (read-only)."""
    resolved = resolve_root(cfg, root)
    _check_repo(resolved)
    argv = ["diff", "--no-color", "--no-ext-diff", "--stat", "--", "."]
    argv_stat = list(argv)
    scope = "."
    if str(path or "").strip():
        rel = _resolve_rel(resolved, _no_dash(path, "path"))
        scope = str(rel)
        argv_stat = ["diff", "--no-color", "--no-ext-diff", "--stat",
                     "--", scope]
    stat, trunc_stat = _run(resolved, argv_stat)
    argv_patch = ["diff", "--no-color", "--no-ext-diff", "-U2", "--", scope]
    patch, trunc_patch = _run(resolved, argv_patch)
    _audit("diff", requested_by,
           {"root": str(resolved), "path": redact_spec(scope)[:160]})
    return {"state": "OK", "root": str(resolved), "path": scope,
            "stat": stat, "patch": patch,
            "truncated": bool(trunc_stat or trunc_patch),
            "rollback": "read-only; nothing to roll back"}


def show(cfg: Any, root: str = "", ref: str = "HEAD", path: str = "",
         requested_by: str = "cli:user") -> Dict[str, Any]:
    """Show one committed file version: ``HEAD[:path]`` (read-only)."""
    resolved = resolve_root(cfg, root)
    _check_repo(resolved)
    clean_ref = _no_dash(ref or "HEAD", "ref")
    if not _REF_RE.match(clean_ref):
        raise GitError(INPUT_CODE,
                       "ref must be HEAD or HEAD~N (committed history only)")
    if not str(path or "").strip():
        raise GitError(INPUT_CODE, "a file path is required")
    rel = _resolve_rel(resolved, _no_dash(path, "path"))
    text, truncated = _run(
        resolved, ["show", f"{clean_ref}:{rel}"])
    _audit("show", requested_by,
           {"root": str(resolved), "ref": clean_ref,
            "path": redact_spec(str(rel))[:160]})
    return {"state": "OK", "root": str(resolved), "ref": clean_ref,
            "path": str(rel), "content": text, "truncated": truncated,
            "rollback": "read-only; nothing to roll back"}


def _resolve_rel(root: Path, rel: str) -> Path:
    """Resolve a repo-relative path strictly inside the root."""
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise GitError(INPUT_CODE, "path must be repo-relative (no ..)")
    try:
        resolved = (root / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise GitError(INPUT_CODE, f"cannot resolve path ({exc})")
    try:
        resolved.relative_to(root)
    except ValueError:
        raise GitError(SCOPE_CODE, "path escapes the inspection root")
    return resolved.relative_to(root)


def refuse_write(verb: str) -> Dict[str, Any]:
    """Typed refusal for mutating/network git verbs (same shape everywhere)."""
    return {"state": "REFUSED", "code": WRITE_REFUSED_CODE,
            "verb": _redact(verb)[:40],
            "detail": "Qubit git is read-only (status/log/diff/show). "
                      "Writes go through the brokered opencode worktree "
                      "flow; network verbs are never executed.",
            "owner": "qubit-policy",
            "rollback": "nothing changed; nothing to roll back"}


def public_error(exc: GitError) -> Dict[str, Any]:
    """Public JSON envelope for a GitError (redacted, no traceback)."""
    return {"state": "FAILED", "code": exc.code,
            "detail": redact_spec(exc.detail)[:300],
            "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back"}
