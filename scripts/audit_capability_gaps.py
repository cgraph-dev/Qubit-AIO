#!/usr/bin/env python3
"""Capability audit: what did the 5,441 upstream commits ADD that Qubit lacks?

The point of pulling the delta is not just to merge files -- it is to enumerate
capability. Every symbol/function/class upstream introduced at a036b13793 that
Qubit's tree does not define, and every file upstream added that Qubit does not
have, is a CONCRETE, NAMED port item instead of a vague "not fully synced".

Method (deliberately content-based, not existence-based -- an earlier audit
falsely reported 20 unsynced modules by treating "file exists" as "file synced"):

  1. Diff the pin range 69948c0057..a036b13793 in the upstream repo.
  2. For ADDED/MODIFIED python files, extract upstream's ADDED top-level
     definitions (def/class/ALL_CAPS) from the diff's '+' lines.
  3. Map each upstream path to its Qubit counterpart (rename map).
  4. Ask: does the Qubit counterpart define that symbol?  (grep of the real file)
  5. Report the misses, grouped by subsystem, with the upstream file:line.

Output: a markdown table suitable for docs/SYNC_CAPABILITY_GAPS.md.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

PIN = Path.home() / ".hermes/hermes-agent"
REPO = Path(__file__).resolve().parent.parent
FROM, TO = "69948c0057", "a036b13793"

SKIP_PREFIXES = (".github/", "contributors/", "node_modules/", ".git/")

# Upstream path -> Qubit path. Mirrors scripts/sync_upstream.py's routing.
PREFIX_RULES = [
    ("hermes_cli/", "qubit_runtime/qubit_cli/"),
    ("hermes_platform/", "qubit_runtime/qubit_platform/"),
    ("hermes_state", "qubit_runtime/qubit_state"),
]
STEM_RULES = {
    "hermes_constants.py": "qubit_runtime/qubit_constants.py",
    "hermes_logging.py": "qubit_runtime/qubit_logging.py",
    "hermes_bootstrap.py": "qubit_runtime/qubit_bootstrap.py",
}


def sh(cmd: str, cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return r.stdout


def route(upstream_path: str) -> str:
    """Upstream path -> repo-relative Qubit path."""
    for src, dst in PREFIX_RULES:
        if upstream_path.startswith(src):
            return dst + upstream_path[len(src):]
    if upstream_path in STEM_RULES:
        return STEM_RULES[upstream_path]
    # bare hermes_state_*.py -> qubit_state_*.py at repo root of qubit_runtime
    name = upstream_path.split("/")[-1]
    if name.startswith("hermes_state"):
        return "qubit_runtime/" + name.replace("hermes_state", "qubit_state", 1)
    if "/" not in upstream_path:
        return "qubit_runtime/" + upstream_path
    return "qubit_runtime/" + upstream_path


DEF_RE = re.compile(r"^(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*(?::|=)")


def added_defs(upstream_path: str) -> set[str]:
    """Top-level defs/classes/consts upstream ADDED in the pin range."""
    out = sh(f"git diff {FROM}..{TO} -- {upstream_path}", PIN)
    found: set[str] = set()
    for line in out.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        if not body.strip() or body.startswith(" "):
            continue
        m = DEF_RE.match(body)
        if m:
            found.add(m.group(2))
            continue
        m = CONST_RE.match(body)
        if m:
            found.add(m.group(1))
    return found


def grep_has(qbit_path: str, symbol: str) -> bool:
    """Does the Qubit counterpart define/mention this symbol?"""
    p = REPO / qbit_path
    if not p.exists():
        return False
    try:
        txt = p.read_text(errors="ignore")
    except OSError:
        return False
    return re.search(rf"\b{re.escape(symbol)}\b", txt) is not None


def main() -> int:
    delta = [p for p in sh(f"git diff --name-only {FROM}..{TO}", PIN).split()
             if p and not p.startswith(SKIP_PREFIXES)]
    py_files = [p for p in delta if p.endswith(".py")]
    print(f"# Capability audit — upstream {FROM}..{TO}")
    print(f"\ndelta files: {len(delta)}  (python: {len(py_files)})")

    gaps: dict[str, list[str]] = defaultdict(list)
    checked = 0
    for up in py_files:
        syms = added_defs(up)
        if not syms:
            continue
        q = route(up)
        for s in sorted(syms):
            checked += 1
            if not grep_has(q, s):
                gaps[q].append(f"{s}  <- {up}")

    total_missing = sum(len(v) for v in gaps.values())
    print(f"symbols checked: {checked}   MISSING in Qubit: {total_missing}")
    print(f"files with gaps: {len(gaps)}\n")

    # Group by subsystem for a readable report.
    by_sub = Counter()
    for q, syms in gaps.items():
        sub = "/".join(q.split("/")[1:2]) or q
        by_sub[sub] += len(syms)
    print("## missing symbols by subsystem\n")
    for sub, n in by_sub.most_common():
        print(f"  {n:5d}  {sub}")

    if len(sys.argv) > 1 and sys.argv[1] == "--full":
        print("\n## detail\n")
        for q in sorted(gaps, key=lambda k: -len(gaps[k])):
            print(f"\n### {q}  ({len(gaps[q])} missing)")
            for g in gaps[q][:25]:
                print(f"  - {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
