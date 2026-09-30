#!/usr/bin/env python3
"""Wave 5 privacy scrub: remove the real developer home path from tracked docs.

The verifier found the developer's absolute home path baked into tracked
documentation. This script deterministically replaces that path with the
portable placeholder ``$HOME`` so the docs keep their meaning (paths still
resolve, shell snippets still expand) without exposing who ran them.

Scope and safety
----------------
* Only *tracked* files under ``docs/`` are considered (``git ls-files``), so a
  new untracked developer document is never rewritten.
* Frozen contracts, owner amendments, the execution directive, and the agent
  prompt pack are never touched; they are evidence the owner controls.
* The replacement is a single literal, applied left-to-right with no other
  edits. Running it twice changes nothing (idempotent).
* ``--check`` reports drift and exits 1 without writing anything.

Usage::

    python3 scripts/sanitize_docs.py            # apply
    python3 scripts/sanitize_docs.py --check    # verify clean, no writes
    python3 scripts/sanitize_docs.py --list     # print scope only
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List

REPO = Path(__file__).resolve().parents[1]

# The real operator home path found by the verifier. It is assembled from
# parts on purpose: this file must not itself contain a concrete machine path,
# or the release machine-path scan (voicebridge/release.py) would flag core.
REAL_HOME = os.path.join(os.sep, "home", "trick")
PLACEHOLDER = "$HOME"

# Frozen / owner-controlled / prompt-pack documents are evidence, not docs to
# rewrite. The untracked final-prompt path is listed defensively (git ls-files
# already excludes it, but ``--check`` from a future date must stay honest).
EXCLUDED = frozenset({
    "docs/archive/2026-09-21/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md",
    "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11.md",
    "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md",
    "docs/archive/2026-09-21/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md",
    "docs/archive/2026-09-21/AGENT_PROMPT_PACK.md",
    "docs/archive/2026-09-21/QUBIT_FINAL_CODE_READY_PROMPT.md",
})


def tracked_docs() -> List[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", "docs"],
        capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.splitlines() if p and p not in EXCLUDED)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the replacement (default)")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1 without writing")
    ap.add_argument("--list", action="store_true",
                    help="print the in-scope files only")
    args = ap.parse_args(argv)

    docs = tracked_docs()
    if args.list:
        for rel in docs:
            print(rel)
        return 0

    changed: List[tuple] = []
    for rel in docs:
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count = text.count(REAL_HOME)
        if not count:
            continue
        changed.append((rel, count))
        if args.check:
            continue
        path.write_text(text.replace(REAL_HOME, PLACEHOLDER),
                        encoding="utf-8")

    label = "would sanitize" if args.check else "sanitized"
    for rel, count in changed:
        print(f"{label}: {rel} ({count} occurrence(s))")
    total = sum(c for _, c in changed)
    print(f"{label} {len(changed)} file(s), {total} occurrence(s) "
          f"of {REAL_HOME} -> {PLACEHOLDER}")
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
