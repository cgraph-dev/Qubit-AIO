# scripts/merge_v0215/checkpoint.py
"""Checkpoint / rollback around each merge bucket (Task 5).

The merge mutates the runtime tree bucket by bucket.  A checkpoint commits the
current tree state and points a writer-specific **index** at it, so:

* ``--checkpoint <name>`` records a restorable state (an atomic commit);
* ``--rollback [<name>]`` runs ``git reset --hard`` to that state and
  ``git clean -fd`` to remove files the merge added.

The checkpoint only ever runs in the isolated worktree; the caller (the merge
driver) is responsible for guarding the live tree.

Two correctness points this module is explicit about:

* **Linked worktrees.**  In a linked worktree ``<repo>/.git`` is a *file*
  pointing at ``<common>/worktrees/<name>``.  The private index and its
  bookkeeping therefore live in the real git dir resolved via
  ``git rev-parse --git-dir`` (falling back to a temp dir), never at
  ``<repo>/.git/...``.
* **Rollback is explicit about data loss.**  ``git reset --hard`` +
  ``git clean -fd`` destroys uncommitted/untracked work, so a rollback refuses
  on a dirty tree unless ``force=True`` is passed.

The private index is used only to build checkpoint commits; ``reset --hard``
and ``clean`` run against the **caller's real index** so the worktree and the
normal staging area are restored together (no stale staged diff after a
rollback).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class CheckpointError(RuntimeError):
    """A checkpoint/rollback operation failed."""


class DirtyWorktreeError(CheckpointError):
    """Rollback refused because the worktree has uncommitted changes."""


def _run_git(
    repo: Path,
    *args: str,
    env: Optional[dict] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    # A caller-provided GIT_INDEX_FILE on our own environment would silently
    # retarget every git call; drop it first and apply ``env`` on top.
    full_env.pop("GIT_INDEX_FILE", None)
    if env:
        full_env.update(env)
    res = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=full_env,
        capture_output=True,
        text=True,
    )
    if check and res.returncode != 0:
        raise CheckpointError(
            f"git {' '.join(args)} failed rc={res.returncode}: {res.stderr.strip()}"
        )
    return res


def _head(repo: Path) -> Optional[str]:
    res = _run_git(repo, "rev-parse", "--verify", "HEAD", check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip()


def _branch(repo: Path) -> str:
    res = _run_git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    head = _head(repo)
    return head if head else "detached"


def _resolve_git_dir(repo: Path) -> Path:
    """Resolve the real (per-worktree) git dir for *repo*.

    ``git rev-parse --git-dir`` returns the worktree-specific dir in a linked
    worktree (``<common>/worktrees/<name>``) and ``.git`` in a normal repo.
    The path may be relative to *repo*; resolve it against the repo.
    """
    res = _run_git(repo, "rev-parse", "--git-dir", check=False)
    if res.returncode != 0 or not res.stdout.strip():
        raise CheckpointError(f"not a git worktree: {repo}")
    git_dir = Path(res.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repo / git_dir).resolve()
    return git_dir


class Checkpoint:
    """Create and restore named checkpoints in a git worktree.

    Each ``Checkpoint`` instance owns its own index file so it never perturbs
    the caller's staging area, and so concurrent checkpoint objects do not
    race.  The index and its bookkeeping live in the resolved git dir (a
    temp dir if the git dir is not writable), which keeps them safe from a
    worktree reset and correct in a linked worktree.
    """

    def __init__(self, repo: Path, label: str = "merge") -> None:
        self.repo = Path(repo).resolve()
        self.git_dir = _resolve_git_dir(self.repo)
        self.label = label

        state_dir = self.git_dir
        if not os.access(state_dir, os.W_OK):
            state_dir = Path(tempfile.mkdtemp(prefix="merge_v0215-"))
        self._state_dir = state_dir
        self._index = state_dir / f"merge_v0215-index-{os.getpid()}-{label}"
        self._index_file = state_dir / f"merge_v0215-index-{label}.json"

    # -- internals ---------------------------------------------------------
    def _git(self, *args: str, check: bool = True):
        """Run git with the private index (checkpoint-commit building only)."""
        return _run_git(
            self.repo,
            *args,
            env={"GIT_INDEX_FILE": str(self._index)},
            check=check,
        )

    def _git_real(self, *args: str, check: bool = True):
        """Run git against the caller's real index."""
        return _run_git(self.repo, *args, check=check)

    def _read_index(self) -> dict:
        if not self._index_file.exists():
            return {}
        try:
            return json.loads(self._index_file.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def _write_index(self, index: dict) -> None:
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        self._index_file.write_text(
            json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )

    def is_dirty(self) -> bool:
        """True when the worktree has modified or untracked (non-ignored) files."""
        res = self._git_real("status", "--porcelain", "--untracked-files=all")
        return any(line.strip() for line in res.stdout.splitlines())

    # -- API ---------------------------------------------------------------
    def checkpoint(self, name: str) -> str:
        """Snapshot the worktree as *name*; return the resulting commit sha."""
        if not self._index.exists():
            # Seed the private index from HEAD so only the delta is committed.
            head = _head(self.repo)
            if head is None:
                self._git("read-tree", "--empty")
            else:
                self._git("read-tree", head)
        self._git("add", "-A", "--", ".")
        tree = self._git("write-tree").stdout.strip()
        parent = _head(self.repo)
        commit_args = ["commit-tree", tree, "-m", f"merge_v0215 checkpoint {name}"]
        if parent:
            commit_args += ["-p", parent]
        sha = self._git(*commit_args).stdout.strip()
        index = self._read_index()
        index[name] = {
            "sha": sha,
            "branch": _branch(self.repo),
        }
        self._write_index(index)
        return sha

    def list(self) -> list[str]:
        """Checkpoint names, in creation order."""
        return list(self._read_index().keys())

    def rollback(self, name: Optional[str] = None, force: bool = False) -> str:
        """Restore *name* (default: the most recent checkpoint).

        Refuses on a dirty worktree (``DirtyWorktreeError``) because
        ``reset --hard`` + ``clean -fd`` destroys uncommitted/untracked work;
        pass ``force=True`` to make the loss explicit.  The caller's real
        index is reset together with the worktree, so ``git status`` is clean
        afterwards.
        """
        index = self._read_index()
        if not index:
            raise CheckpointError("no checkpoints recorded")
        target = name if name is not None else next(reversed(index))
        if target not in index:
            raise CheckpointError(f"unknown checkpoint: {target}")
        if not force and self.is_dirty():
            raise DirtyWorktreeError(
                f"rollback to {target!r} would discard uncommitted/untracked "
                f"work; commit it or pass force=True"
            )
        sha = index[target]["sha"]
        # Reset the caller's real index and the worktree together; the private
        # checkpoint index is left untouched (so the record survives).
        self._git_real("reset", "--hard", sha)
        self._git_real("clean", "-fd")
        return sha

    def cleanup(self) -> None:
        """Remove this instance's private index (bookkeeping is kept)."""
        try:
            self._index.unlink()
        except OSError:
            pass
