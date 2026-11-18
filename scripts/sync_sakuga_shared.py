#!/usr/bin/env python3
"""Keep the two `@qubit/shared` packages on the same Sakuga module.

Qubit has two shared packages by design:
  * frontend/qubit-shared        — the dashboard (frontend/qubit-web)
  * qubit_runtime/apps/shared    — the Electron renderer and TUI

Both need the identical framework-facing Sakuga module. Rather than let them
drift, the dashboard copy is the source and this script mirrors it into the
runtime package. `--check` is the CI/commit gate.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "frontend" / "qubit-shared" / "src" / "sakuga"
DEST = REPO / "qubit_runtime" / "apps" / "shared" / "src" / "sakuga"
SUFFIXES = {".ts", ".tsx", ".css", ".md"}


def files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in SUFFIXES)


def check() -> int:
    src = files(SOURCE)
    dst = files(DEST)
    src_rel = {p.relative_to(SOURCE) for p in src}
    dst_rel = {p.relative_to(DEST) for p in dst}
    problems: list[str] = []
    for rel in sorted(src_rel - dst_rel):
        problems.append(f"missing in runtime shared: {rel}")
    for rel in sorted(dst_rel - src_rel):
        problems.append(f"extra in runtime shared: {rel}")
    for rel in sorted(src_rel & dst_rel):
        if not filecmp.cmp(SOURCE / rel, DEST / rel, shallow=False):
            problems.append(f"drifted: {rel}")
    if problems:
        print("sakuga shared: STALE")
        for problem in problems:
            print(f"  - {problem}")
        print("run: python3 scripts/sync_sakuga_shared.py")
        return 1
    print(f"sakuga shared: OK ({len(src)} files)")
    return 0


def sync() -> int:
    SOURCE.mkdir(parents=True, exist_ok=True)
    DEST.mkdir(parents=True, exist_ok=True)
    src = files(SOURCE)
    for path in src:
        rel = path.relative_to(SOURCE)
        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    copied = {p.relative_to(SOURCE) for p in src}
    for path in files(DEST):
        rel = path.relative_to(DEST)
        if rel not in copied:
            path.unlink()
    print(f"sakuga shared: synced {len(src)} files -> {DEST.relative_to(REPO)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args(argv)
    return check() if args.check else sync()


if __name__ == "__main__":
    sys.exit(main())
