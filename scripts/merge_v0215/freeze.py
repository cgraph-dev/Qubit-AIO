# scripts/merge_v0215/freeze.py
"""Freeze record for the v0.21.5 1:1 rebase (Task 5, operator gate for Tasks 6+).

Ruling R15: the isolated worktree *is* the freeze.  Stopping the concurrent
writers on the live product tree is unnecessary because the merge runs only in
this dedicated worktree (single writer).  This module records the frozen base
so Task 6 can assert it is merging from exactly the audited revision.

A freeze record is ``{sha, dirty_count, pin, branch}`` where ``sha`` is the
worktree HEAD (the freeze base), ``dirty_count`` is the number of modified
tracked files plus untracked files, ``pin`` is the vendored upstream commit,
and ``branch`` is the worktree branch.  ``dirty_count == 0`` is the operator
gate.

The record is derived from ``git`` and is byte-reproducible for a fixed
worktree, so it is safe to commit and re-check.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

#: The vendored upstream pin this rebase targets (owner amendment 2026-09-25).
PIN = "69948c005791d4d0e51670ca015238c2c7663749"

#: Default output directory (canonical generated artifacts).
AUDIT_DIR = Path(__file__).resolve().parents[2] / "docs" / "generated" / "audit"


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return res.stdout


def dirty_count(repo: Path, ignore: Optional[Path] = None) -> int:
    """Modified tracked files + untracked files in *repo* (excluding ignored).

    *ignore*, when given, is a path or directory (typically the freeze output
    or the whole audit directory) that is not counted.  The freeze writer
    writes its artifact into the worktree, so without this the record would
    never describe a clean tree; excluding the audit directory keeps the
    record stable across a delete-and-regenerate cycle.
    """
    out = _git(repo, "status", "--porcelain", "--untracked-files=all")
    skip = None
    if ignore is not None:
        try:
            skip = str(Path(ignore).resolve().relative_to(Path(repo).resolve()))
        except ValueError:
            skip = None
    count = 0
    for line in out.splitlines():
        if not line.strip():
            continue
        if skip is not None:
            rel = line[3:].strip()
            if rel == skip or rel.startswith(skip.rstrip("/") + "/"):
                continue
        count += 1
    return count


def build_freeze(repo: Path, ignore: Optional[Path] = None) -> dict:
    """Build the freeze record for *repo* without writing anything."""
    repo = Path(repo)
    sha = _git(repo, "rev-parse", "HEAD").strip()
    branch = _git(repo, "symbolic-ref", "--short", "-q", "HEAD").strip()
    if not branch:
        branch = "detached"
    return {
        "schema": "qubit-v0215-freeze/1",
        "sha": sha,
        "artifact_commit": sha,
        "dirty_count": dirty_count(repo, ignore=ignore),
        "pin": PIN,
        "branch": branch,
    }


def write_freeze(repo: Path, audit_dir: Optional[Path] = None) -> Path:
    """Build the record for *repo* and write ``FREEZE_<short>.json``.

    Returns the written path.  *audit_dir* defaults to
    ``docs/generated/audit/``.  The output is idempotent: the artifact is
    named from the recorded ``sha`` and the audit directory is excluded from
    the dirty count (so the record itself never makes the tree look dirty).

    ``sha`` is the *freeze base*: the clean commit the artifact was generated
    against.  ``artifact_commit`` records the same value at generation time;
    when the artifact is committed it becomes the **parent** of the commit
    that introduces it, because a committed file cannot name its own commit
    without ``--amend``.  Task 6 must therefore verify ``sha`` is an ancestor
    of its start HEAD, not that ``sha == HEAD``.
    """
    repo = Path(repo)
    sha = _git(repo, "rev-parse", "HEAD").strip()
    out_dir = Path(audit_dir) if audit_dir is not None else AUDIT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"FREEZE_{sha[:8]}.json"
    doc = build_freeze(repo, ignore=out_dir)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out
