#!/usr/bin/env python3
"""Per-file staleness audit: is what we merged the CURRENT upstream revision?

WHY THIS EXISTS
---------------
The campaign reported "95% present" from a file-PRESENCE test: does a path exist in
our tree for each upstream path? Existence is not currency. A file can be present and
still be an OLD revision of a file upstream has since reworked.

The evidence that motivated this: `qubit_cli/update_lock.py` exists in our tree with
**10** top-level defs, while upstream at the same revision it was ported from had grown
to **70**. Four sibling modules in the same feature (`update_pause_record.py`,
`update_cmd_commit.py`, `update_custody.py`, `anon_challenge.py`) are missing outright.
So "present" hid a file that stopped at a much earlier revision.

This script measures, per upstream file:
  * EXISTS   -- is the routed path in our tree at all?
  * STALE    -- present, but upstream changed it AFTER the revision we have
  * CURRENT  -- present and matches the upstream revision we synced from

and reports staleness in SYMBOL terms, not bytes: how many top-level defs upstream has
that we do not. A byte diff is dominated by rebranding churn (hermes_*/qubit_*), which
is why this counts symbols.

SCOPE: upstream `a036b13793` (our baseline) is the "we synced from here" reference.
A file whose upstream content changed between the OLD pin and `a036b13793` but which
we ported from an even earlier revision is a SEPARATE problem (a short-port). This
script flags both: `short_port` (we trail our own baseline) and `behind_head` (we
trail current upstream).

Usage:
    python3 scripts/audit_sync_completeness.py [--json out.json] [--top 40]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Refuse to run from outside the repo. `__file__` is the only correct anchor, but a
# copy of this script placed in /tmp resolves REPO to /tmp and then reports EVERY
# upstream file as absent (8,161 absent / 0 present) -- a plausible-looking result
# that is pure artifact. That happened once. Fail loudly instead.
if not (REPO / "qubit_runtime").is_dir() or not (REPO / "scripts").is_dir():
    raise SystemExit(
        f"REFUSING: computed repo root {REPO} is not a Qubit checkout.\n"
        "Run this script from the repository: "
        "`.venv/bin/python scripts/audit_sync_completeness.py` "
        "(do not copy it elsewhere -- it resolves paths from __file__)."
    )
UP = Path("/home/trick/.hermes/hermes-agent")
BASELINE = "a036b13793"
HEAD = "origin/main"

# upstream prefix -> our prefix
ROUTES = {
    "hermes_cli": "qubit_cli",
    "hermes_platform": "qubit_platform",
    "hermes_state": "qubit_state",
}
RUNTIME = REPO / "qubit_runtime"


def sh(args: list[str], cwd: Path | None = None) -> str:
    try:
        p = subprocess.run(args, cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=600)
        return p.stdout
    except Exception:
        return ""


def route(path: str) -> str:
    """Upstream path -> our path, applying the rename map under tests/ too."""
    for prefix in ("tests/", ""):
        if prefix and not path.startswith(prefix):
            continue
        rest = path[len(prefix):] if prefix else path
        segs = rest.split("/")
        if segs and segs[0] in ROUTES:
            segs[0] = ROUTES[segs[0]]
        if segs and segs[-1].startswith("hermes"):
            segs[-1] = "qubit" + segs[-1][len("hermes"):]
        return prefix + "/".join(segs)
    return path


def top_defs(src: str) -> set[str]:
    """Top-level def/class/assigned names. Empty set when unparseable."""
    if not src:
        return set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def blob(rev: str, path: str) -> str:
    return sh(["git", "-C", str(UP), "show", f"{rev}:{path}"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--rev", default=HEAD,
                    help=f"upstream revision to audit against (default {HEAD})")
    args = ap.parse_args()

    # Every upstream .py file at the reference revision.
    files = [p for p in sh(["git", "-C", str(UP), "ls-tree", "-r", "--name-only",
                            args.rev]).split("\n") if p.endswith(".py")]
    print(f"# sync completeness audit -- upstream {args.rev} vs our tree")
    print(f"# upstream .py files: {len(files)}")

    exists = 0
    absent: list[str] = []
    short_port: list[tuple[str, int, int]] = []   # present but trails our own baseline
    behind_head: list[tuple[str, int, int]] = []
    current = 0

    # Cache blobs per revision to avoid re-shelling.
    for i, up_path in enumerate(files):
        our_path = route(up_path)
        ours = RUNTIME / our_path
        head_src = blob(args.rev, up_path)
        head_syms = top_defs(head_src)
        if not ours.exists():
            absent.append(up_path)
            continue
        exists += 1
        try:
            our_src = ours.read_text(errors="replace")
        except OSError:
            our_src = ""
        our_syms = top_defs(our_src)
        missing = head_syms - our_syms
        if not missing:
            current += 1
            continue

        # Distinguish: did upstream add these AFTER our baseline, or do we trail
        # even our own baseline? Compare against the baseline blob.
        base_syms = top_defs(blob(BASELINE, up_path))
        missing_at_base = base_syms - our_syms
        if missing_at_base:
            short_port.append((up_path, len(missing_at_base), len(base_syms)))
        else:
            behind_head.append((up_path, len(missing), len(head_syms)))

    total_missing_syms = sum(n for _, n, _ in short_port) + sum(n for _, n, _ in behind_head)
    print(f"# EXISTS: {exists}   ABSENT: {len(absent)}   CURRENT: {current}")
    print(f"# SHORT-PORT (trails our own baseline): {len(short_port)} files, "
          f"{sum(n for _, n, _ in short_port)} missing symbols")
    print(f"# BEHIND-HEAD (upstream moved on):      {len(behind_head)} files, "
          f"{sum(n for _, n, _ in behind_head)} missing symbols")
    print(f"# total missing symbols across present files: {total_missing_syms}")

    if short_port:
        print(f"\n## WORST SHORT-PORTS (we never even reached our own baseline)")
        for p, n, tot in sorted(short_port, key=lambda x: -x[1])[:15]:
            print(f"  {n:5} /{tot:5} missing   {p}")

    if absent:
        print(f"\n## ABSENT files by subsystem")
        c = Counter(p.split("/")[0] for p in absent)
        for k, v in c.most_common(15):
            print(f"  {v:5}  {k}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "rev": args.rev, "upstream_py": len(files), "exists": exists,
            "absent": len(absent), "current": current,
            "short_port_files": len(short_port),
            "short_port_symbols": sum(n for _, n, _ in short_port),
            "behind_head_files": len(behind_head),
            "behind_head_symbols": sum(n for _, n, _ in behind_head),
            "worst_short_ports": [
                {"path": p, "missing": n, "baseline_defs": t}
                for p, n, t in sorted(short_port, key=lambda x: -x[1])],
            "absent_paths": absent[:2000],
        }, indent=2))
        print(f"\nJSON written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
