# scripts/merge_v0215/__main__.py
"""CLI entry point: ``python -m merge_v0215`` (Task 5).

Checkpoint/rollback driver used around each merge bucket (Task 6, Step 5), plus
the freeze-record generator (the operator gate for Tasks 6+).

Usage::

    python -m merge_v0215 -C <worktree> --checkpoint <name>
    python -m merge_v0215 -C <worktree> --rollback [<name>]
    python -m merge_v0215 -C <worktree> --list
    python -m merge_v0215 -C <worktree> --freeze [--out <dir>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checkpoint import Checkpoint, CheckpointError
from .freeze import write_freeze


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m merge_v0215",
        description="Checkpoint/rollback + freeze record for the v0.21.5 rebase.",
    )
    p.add_argument("-C", "--repo", default=".", help="worktree to operate on")
    p.add_argument("--checkpoint", metavar="NAME", help="record a checkpoint")
    p.add_argument(
        "--rollback",
        nargs="?",
        const="",
        metavar="NAME",
        help="restore a checkpoint (default: the most recent)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="allow rollback to discard uncommitted/untracked work",
    )
    p.add_argument("--list", action="store_true", help="list checkpoints")
    p.add_argument(
        "--freeze", action="store_true", help="write the freeze record"
    )
    p.add_argument("--out", help="freeze output dir (default docs/generated/audit)")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    repo = Path(args.repo).resolve()

    if args.freeze:
        out = write_freeze(repo, audit_dir=Path(args.out) if args.out else None)
        print(f"freeze: {out}")
        return 0

    try:
        cp = Checkpoint(repo)
    except CheckpointError as exc:
        print(f"merge_v0215: {exc}", file=sys.stderr)
        return 1

    if args.checkpoint:
        sha = cp.checkpoint(args.checkpoint)
        print(f"checkpoint {args.checkpoint}: {sha}")
        return 0
    if args.rollback is not None:
        name = args.rollback or None
        sha = cp.rollback(name, force=args.force)
        print(f"rollback {name or '(latest)'}: {sha}")
        return 0
    if args.list:
        for name in cp.list():
            print(name)
        return 0

    _parser().print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
