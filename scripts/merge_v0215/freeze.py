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


def dirty_count(repo: Path) -> int:
    """Modified tracked files + untracked files in *repo* (excluding ignored)."""
    out = _git(repo, "status", "--porcelain", "--untracked-files=all")
    return sum(1 for line in out.splitlines() if line.strip())


def build_freeze(repo: Path) -> dict:
    """Build the freeze record for *repo* without writing anything."""
    repo = Path(repo)
    sha = _git(repo, "rev-parse", "HEAD").strip()
    branch = _git(repo, "symbolic-ref", "--short", "-q", "HEAD").strip()
    if not branch:
        branch = "detached"
    return {
        "schema": "qubit-v0215-freeze/1",
        "sha": sha,
        "dirty_count": dirty_count(repo),
        "pin": PIN,
        "branch": branch,
    }


def write_freeze(repo: Path, audit_dir: Optional[Path] = None) -> Path:
    """Build the record for *repo* and write ``FREEZE_<short>.json``.

    Returns the written path.  *audit_dir* defaults to
    ``docs/generated/audit/``.
    """
    repo = Path(repo)
    doc = build_freeze(repo)
    out_dir = Path(audit_dir) if audit_dir is not None else AUDIT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"FREEZE_{doc['sha'][:8]}.json"
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out
