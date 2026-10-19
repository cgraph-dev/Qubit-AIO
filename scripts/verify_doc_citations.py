#!/usr/bin/env python3
"""Verify every doc path cited by AGENTS.md and README.md actually resolves.

Two classes of citation are checked, because the docs make two different claims:

  live        a plain `docs/...` path. Must exist in the working tree.
  historical  a path appearing immediately after `git show eafb54e26^:`. It is
              expected to be ABSENT from the tree (it was purged) and must be
              retrievable from that commit instead.

Exit code 0 only when every live path exists and every historical ref is
retrievable from `eafb54e26^`. No network, no writes.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "eafb54e26^"

# `docs/<...>` possibly wrapped in backticks/parens and followed by punctuation.
LIVE_RE = re.compile(r"(?<![\w/.-])(docs/[A-Za-z0-9_./-]*)")
# A git-show ref to the pre-purge tree, e.g. `git show eafb54e26^:docs/x/y.md`
HIST_RE = re.compile(r"git show\s+eafb54e26\^:([A-Za-z0-9_./-]+)")


def strip_tail(path: str) -> str:
    """Drop sentence punctuation that the greedy regex may have swallowed."""
    while path and path[-1] in ".,;:)]}`'\"":
        path = path[:-1]
    return path


def scan(doc: str) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    live: dict[str, list[int]] = {}
    hist: dict[str, list[int]] = {}
    with open(os.path.join(ROOT, doc), encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            hist_spans = list(HIST_RE.finditer(line))
            for m in hist_spans:
                hist.setdefault(strip_tail(m.group(1)), []).append(lineno)
            # Blank out the historical refs so they are not also read as live.
            masked = line
            for m in hist_spans:
                masked = masked.replace(m.group(0), " " * len(m.group(0)))
            for m in LIVE_RE.finditer(masked):
                p = strip_tail(m.group(1))
                if p in ("docs/", "docs"):
                    continue  # bare directory mention, not a citation
                live.setdefault(p, []).append(lineno)
    return live, hist


def git_has(path: str) -> bool:
    r = subprocess.run(
        ["git", "cat-file", "-e", f"{BASE}:{path}"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return r.returncode == 0


def main() -> int:
    docs = sys.argv[1:] or ["AGENTS.md", "README.md"]
    failures = 0
    for doc in docs:
        if not os.path.isfile(os.path.join(ROOT, doc)):
            print(f"FAIL  {doc}: file not found")
            failures += 1
            continue
        live, hist = scan(doc)
        print(f"\n== {doc} ==")
        print(f"-- live citations ({len(live)}) --")
        for path in sorted(live):
            lines = ",".join(map(str, sorted(set(live[path]))))
            full = os.path.join(ROOT, path)
            if path.endswith("/"):
                ok = os.path.isdir(full)
                kind = "dir "
            else:
                ok = os.path.isfile(full)
                kind = "file"
            print(f"  {'PASS' if ok else 'FAIL'}  {kind}  {path:52s}  L{lines}")
            if not ok:
                failures += 1
        if hist:
            print(f"-- historical refs, expected absent, must be in {BASE} ({len(hist)}) --")
            for path in sorted(hist):
                lines = ",".join(map(str, sorted(set(hist[path]))))
                in_tree = os.path.exists(os.path.join(ROOT, path))
                retrievable = git_has(path)
                ok = retrievable and not in_tree
                print(
                    f"  {'PASS' if ok else 'FAIL'}  absent={not in_tree} "
                    f"recoverable={retrievable}  {path:52s}  L{lines}"
                )
                if not ok:
                    failures += 1

    print(f"\n{'OK — every citation resolves' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
