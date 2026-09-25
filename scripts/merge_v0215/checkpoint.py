# scripts/merge_v0215/checkpoint.py
"""Checkpoint / rollback around each merge bucket (Task 5).

The merge mutates the runtime tree bucket by bucket.  A checkpoint commits the
current tree state and points a writer-specific **index** at it, so:

* ``--checkpoint <name>`` records a restorable state (an atomic commit);
* ``--rollback [<name>]`` runs ``git reset --hard`` to that state and
  ``git clean -fd`` to remove files the merge added.

The checkpoint only ever runs in the isolated worktree; the caller (the merge
driver) is responsible for guarding the live tree.  The index lives under
``.git`` so a worktree reset never discards the checkpoint bookkeeping — a
committed checkpoint survives rolback, matching "record each bucket" (Task 6,
Step 5) then "select a state" (rollback).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional


class CheckpointError(RuntimeError):
    """A checkpoint/rollback operation failed."""


def _run_git(
    repo: Path,
    *args: str,
    env: Optional[dict] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
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


class Checkpoint:
    """Create and restore named checkpoints in a git worktree.

    Each ``Checkpoint`` instance owns its own index file
    (``.git/merge_v0215-index-<pid>``) so it never perturbs the caller's
    staging area, and so concurrent checkpoint objects do not race.
    """

    def __init__(self, repo: Path, label: str = "merge") -> None:
        self.repo = Path(repo)
        if not (self.repo / ".git").exists():
            raise CheckpointError(f"not a git worktree: {self.repo}")
        self.label = label
        self._index = self.repo / ".git" / f"merge_v0215-index-{os.getpid()}-{label}"
        self._index_file = self.repo / ".git" / f"merge_v0215-index-{label}.json"

    # -- internals ---------------------------------------------------------
    def _git(self, *args: str, check: bool = True):
        return _run_git(
            self.repo,
            *args,
            env={"GIT_INDEX_FILE": str(self._index)},
            check=check,
        )

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
        self._git("add", "-A", "--", ".", ":!/.git")
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

    def rollback(self, name: Optional[str] = None) -> str:
        """Restore *name* (default: the most recent checkpoint)."""
        index = self._read_index()
        if not index:
            raise CheckpointError("no checkpoints recorded")
        target = name if name is not None else next(reversed(index))
        if target not in index:
            raise CheckpointError(f"unknown checkpoint: {target}")
        sha = index[target]["sha"]
        self._git("reset", "--hard", sha)
        self._git("clean", "-fd")
        return sha
