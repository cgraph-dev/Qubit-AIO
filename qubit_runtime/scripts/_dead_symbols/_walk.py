"""Owned-file discovery (deterministic order, vendored trees skipped)."""
from __future__ import annotations

import os
from pathlib import Path

from _consts import PKG, SKIP_PARTS, SKIP_REL_DIRS


def owned_files(root: Path) -> list[Path]:
    base = root / PKG
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = Path(dirpath).relative_to(base).as_posix()
        keep = []
        for d in dirnames:
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if d in SKIP_PARTS or rel in SKIP_REL_DIRS:
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                out.append(Path(dirpath) / fn)
    out.sort()
    return out


def module_name(root: Path, path: Path) -> str:
    return path.relative_to(root / PKG).as_posix()
