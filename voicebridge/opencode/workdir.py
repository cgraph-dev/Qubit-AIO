"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from ..logging_setup import log_event
from .policy import (
    MAX_DIFFSTAT_LINES,
    MAX_DIFF_FILES,
    MAX_WORKDIR_BYTES,
    MAX_WORKDIR_FILES,
    OpenCodeError,
    ORPHAN_MAX_AGE_S,
)
def worktree_diffstat(root: Path) -> Dict[str, Any]:
    """Bounded ``git status/diff --stat`` snapshot (never auto-commits)."""
    git = shutil.which("git")
    if git is None:
        return {"git": "unavailable", "files": [], "stat": ""}
    try:
        st = subprocess.run(
            [git, "status", "--short"], cwd=str(root), capture_output=True,
            text=True, timeout=15)
        df = subprocess.run(
            [git, "diff", "--stat"], cwd=str(root), capture_output=True,
            text=True, timeout=15)
    except Exception as exc:
        return {"git": "failed", "detail": str(exc)[:150]}
    files = [ln for ln in (st.stdout or "").splitlines() if ln.strip()]
    stat_lines = (df.stdout or "").splitlines()
    return {"git": "OK", "files": files[:MAX_DIFF_FILES],
            "files_truncated": len(files) > MAX_DIFF_FILES,
            "stat": "\n".join(stat_lines[:MAX_DIFFSTAT_LINES])}


def _validate_workdir(root: Path, workdir: Path) -> Dict[str, Any]:
    """Validate generated workdir contents before reporting success.

    Walks without following symlinks; enforces file-count and byte caps;
    any symlink resolving OUTSIDE the approved project `root` is a
    SYMLINK_ESCAPE failure (rename/symlink escapes cannot smuggle paths in).
    Returns counts for honest receipts.
    """
    nfiles = 0
    nbytes = 0
    outside: List[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(workdir,
                                                    followlinks=False):
            for name in filenames:
                p = Path(dirpath) / name
                nfiles += 1
                if nfiles > MAX_WORKDIR_FILES:
                    return {"ok": False, "code": "LIMIT_EXCEEDED",
                            "detail": f"workdir exceeds {MAX_WORKDIR_FILES} "
                                      "files",
                            "files": nfiles, "bytes": nbytes,
                            "outside_symlinks": []}
                try:
                    if os.path.islink(p):
                        target = p.resolve(strict=False)
                        try:
                            target.relative_to(root)
                        except ValueError:
                            outside.append(
                                str(p.relative_to(workdir))[:200])
                    else:
                        nbytes += p.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                if nbytes > MAX_WORKDIR_BYTES:
                    return {"ok": False, "code": "LIMIT_EXCEEDED",
                            "detail": f"workdir exceeds "
                                      f"{MAX_WORKDIR_BYTES} bytes",
                            "files": nfiles, "bytes": nbytes,
                            "outside_symlinks": []}
    except Exception as exc:
        return {"ok": False, "code": "VALIDATION_CRASH",
                "detail": f"workdir validation failed: {exc}"[:200],
                "files": nfiles, "bytes": nbytes, "outside_symlinks": []}
    if outside:
        return {"ok": False, "code": "SYMLINK_ESCAPE",
                "detail": "symlinks resolve outside the approved root: "
                          + ", ".join(outside[:8]),
                "files": nfiles, "bytes": nbytes,
                "outside_symlinks": outside[:8]}
    return {"ok": True, "files": nfiles, "bytes": nbytes,
            "outside_symlinks": []}


def _status_paths(root: Path) -> set:
    """Return the set of status-relative paths dirty in `root`.

    Used as a containment tripwire: for disposable runs the project checkout
    itself must not change (all work happens in the isolated worktree). An
    absolute-path write outside the worktree shows up here.
    """
    git = shutil.which("git")
    if git is None:
        return set()
    try:
        st = subprocess.run(
            [git, "status", "--short"], cwd=str(root), capture_output=True,
            text=True, timeout=15)
    except Exception:
        return set()
    paths = set()
    for ln in (st.stdout or "").splitlines():
        body = ln[3:] if len(ln) > 3 else ""
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        body = body.strip().strip('"')
        if body:
            paths.add(body)
    return paths


def _prune_registered_worktree(wt: Path) -> bool:
    """Deregister `wt` from its owning main worktree (best-effort).

    Reads the worktree's ``.git`` pointer file (``gitdir: <main>/.git/
    worktrees/<name>``) to find the owning main root — no git-version-
    dependent plumbing — then removes and prunes through it.
    """
    try:
        git = shutil.which("git")
        if git is None:
            return False
        gitfile = wt / ".git"
        if not gitfile.is_file():
            return False
        content = gitfile.read_text(encoding="utf-8",
                                    errors="replace").strip()
        if not content.startswith("gitdir:"):
            return False
        admindir = Path(content.split(":", 1)[1].strip())
        mainroot = admindir.parent.parent
        if not mainroot.is_dir():
            return False
        rem = subprocess.run(
            [git, "-C", str(mainroot), "worktree", "remove", "--force",
             str(wt)], capture_output=True, text=True, timeout=60)
        if rem.returncode == 0:
            return True
        # Fallback: the working tree may already be gone; prune then drops
        # the stale admin entry.
        subprocess.run([git, "-C", str(mainroot), "worktree", "prune"],
                       capture_output=True, text=True, timeout=60)
        return not wt.exists()
    except Exception:
        return False


def sweep_orphaned_workdirs(max_age_s: float = ORPHAN_MAX_AGE_S) -> Dict[str, Any]:
    """Reap stale disposable-worktree bases (crash/cancel orphans).

    Only bases older than `max_age_s` are touched, so live runs are never
    disturbed. Best-effort: failures are reported, never raised. Called
    automatically before each disposable run and safe to run standalone.
    """
    swept: List[str] = []
    failed: List[str] = []
    now = time.time()
    try:
        bases = sorted(Path(tempfile.gettempdir()).glob("vb-opencode-*"))
    except Exception:
        return {"swept": [], "failed": [], "detail": "tmpdir unreadable"}
    for base in bases:
        try:
            if not base.is_dir() or base.is_symlink():
                continue
            if now - base.stat().st_mtime < max_age_s:
                continue
            wt = base / "wt"
            removed = False
            if wt.is_dir() or (wt / ".git").is_file():
                _prune_registered_worktree(wt)
            shutil.rmtree(base, ignore_errors=True)
            removed = not base.exists()
            (swept if removed else failed).append(base.name)
        except Exception:
            failed.append(base.name)
    return {"swept": swept, "failed": failed,
            "detail": f"swept {len(swept)}, failed {len(failed)}"}


def _prepare_workdir(root: Path, disposable: bool, run_id: str) -> Path:
    """Resolve the execution directory.

    Disposable (default): an isolated detached git worktree under a 0700
    temp base, so the project checkout is never touched. Non-disposable:
    the approved project root itself (requires preview-hash + confirmation
    at the runner layer).
    """
    if not disposable:
        return root
    git = shutil.which("git")
    if git is None:
        raise OpenCodeError("disposable run needs git; "
                            "DISPOSABLE_UNAVAILABLE")
    is_repo = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"], cwd=str(root),
        capture_output=True, text=True, timeout=15)
    if is_repo.returncode != 0:
        raise OpenCodeError("disposable run needs a git checkout; "
                            "DISPOSABLE_UNAVAILABLE")
    try:
        base = Path(tempfile.gettempdir()) / f"vb-opencode-{run_id}"
        base.mkdir(mode=0o700, exist_ok=False)
    except Exception as exc:
        raise OpenCodeError(f"worktree base failed: {exc}") from None
    wt = base / "wt"
    add = subprocess.run(
        [git, "worktree", "add", "--detach", str(wt)], cwd=str(root),
        capture_output=True, text=True, timeout=60)
    if add.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        raise OpenCodeError("git worktree add failed; "
                            "DISPOSABLE_UNAVAILABLE: "
                            f"{add.stderr[:150]}")
    return wt


def _cleanup_workdir(root: Path, workdir: Path, run_id: str) -> None:
    git = shutil.which("git")
    try:
        if git is not None:
            subprocess.run(
                [git, "worktree", "remove", "--force", str(workdir)],
                cwd=str(root), capture_output=True, text=True,
                timeout=60)
        base = workdir.parent
        if base.name.startswith("vb-opencode-"):
            shutil.rmtree(base, ignore_errors=True)
    except Exception:
        pass
    log_event("opencode.run.worktree_cleaned", run_id=run_id)


