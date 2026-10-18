#!/usr/bin/env python3
"""Find modules a port branch BROKE at import time.

WHY: a dangling import does not fail a build. The gateway port shipped two
modules that imported cleanly on HEAD and stopped importing after the port --
the symbols existed upstream but their definitions lived outside the agent's
file list, so only the import half landed. A syntax check passes, tests that
don't touch the file pass, and it breaks at runtime.

WHAT: for each branch, take the changed .py files, try to import each one, and
diff the pass/fail set against the SAME check on the merge base. A file that
passed at base and fails on the branch is a REGRESSION. A file that failed at
base too is pre-existing noise and is ignored.

Usage:
    python3 scripts/port_import_regression.py --base <rev> --branch <branch> [--branch ...]
    python3 scripts/port_import_regression.py --base 71aedb0ea --all
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path("/home/trick/Projects/Repos/qubit-voicebridge")

# Branches carrying the 926-commit port, by subsystem.
BRANCHES = {
    "gateway": "port/gateway-926",
    "hermes_cli": "port/hermes_cli-926",
    "agent": "port/agent-926",
    "tools": "port/tools-926",
    "tui_gateway": "port/tui_gateway-926",
    "platforms": "port/platforms-926",
    "cli_runtime": "port/cli_runtime-926",
}


def git(*args: str) -> str:
    p = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def changed_modules(base: str, branch: str) -> list[str]:
    """Changed .py files under qubit_runtime/, as importable dotted names.

    Only modules whose PARENT package resolves are useful to import; a file in a
    directory with no __init__ is still importable by path in practice, so we do
    not filter on that. Test files are skipped: they import fixtures, not the
    production surface we are auditing.
    """
    out = git("diff", "--name-only", base, branch, "--", "qubit_runtime/*.py")
    mods = []
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel.endswith(".py"):
            continue
        if rel.startswith("qubit_runtime/tests/") or "/tests/" in rel:
            continue
        name = rel[len("qubit_runtime/"):-3].replace("/", ".")
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        mods.append(name)
    return sorted(set(mods))


# Import each module in its OWN subprocess: a module that mutates sys.path or
# raises on import must not poison the next result, and a hard crash (segfault,
# sys.exit) must be attributable.
_PROBE = r"""
import importlib, sys, traceback
sys.path.insert(0, "qubit_runtime")
mod = sys.argv[1]
try:
    importlib.import_module(mod)
except BaseException as e:
    print("FAIL\t%s\t%s" % (type(e).__name__, str(e).replace("\t", " ")[:200]))
    sys.exit(1)
print("OK\t%s\t" % mod)
"""


def probe_import(tree: Path, mod: str, timeout: int = 60) -> tuple[str, str, str]:
    """Return (module, status, detail) for one module in one tree."""
    p = subprocess.run(
        [sys.executable, "-c", _PROBE, mod],
        capture_output=True, text=True, errors="replace",
        cwd=str(tree), timeout=timeout,
    )
    line = (p.stdout or "").strip().splitlines()
    line = line[-1] if line else ""
    if line.startswith("OK\t"):
        return mod, "ok", ""
    if line.startswith("FAIL\t"):
        parts = line.split("\t", 2)
        return mod, "fail", f"{parts[1]}: {parts[2]}" if len(parts) > 2 else parts[1]
    err = (p.stderr or "").strip().splitlines()
    return mod, "fail", (err[-1] if err else f"exit {p.returncode}")[:200]


def probe_tree(tree: Path, mods: list[str], workers: int = 8) -> dict[str, tuple[str, str]]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda m: probe_import(tree, m), mods))
    return {m: (s, d) for m, s, d in results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="merge-base rev to compare against")
    ap.add_argument("--branch", action="append", default=[], help="branch (repeatable)")
    ap.add_argument("--all", action="store_true", help="check every known port branch")
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    targets = dict(BRANCHES) if args.all else {b: b for b in args.branch}
    if not targets:
        raise SystemExit("pass --branch or --all")

    report: dict[str, dict] = {}
    total_regressions = 0

    for name, branch in targets.items():
        tree = REPO.parent / f"wt-port-{name}"
        if not (tree / "qubit_runtime").is_dir():
            # Fall back to the /tmp worktree layout used at dispatch time.
            tree = Path(f"/tmp/wt-port-{name}")
        if not (tree / "qubit_runtime").is_dir():
            print(f"\n=== {name}: SKIP (no worktree at {tree})")
            continue

        mods = changed_modules(args.base, branch)
        if not mods:
            print(f"\n=== {name}: no changed modules")
            continue

        branch_res = probe_tree(tree, mods, args.workers)

        # Baseline: same module names, measured on the merge base. Use a
        # dedicated worktree of the base rev so imports resolve the real tree.
        base_tree = Path(f"/tmp/portbase-{name}")
        if not (base_tree / "qubit_runtime").is_dir():
            subprocess.run(
                ["git", "-C", str(REPO), "worktree", "add", "-q", "--detach",
                 str(base_tree), args.base],
                capture_output=True, text=True,
            )
        base_res = (
            probe_tree(base_tree, mods, args.workers)
            if (base_tree / "qubit_runtime").is_dir()
            else {}
        )

        regressions, new_ok, preexisting = [], [], []
        for m in mods:
            bstat = base_res.get(m, ("absent", ""))[0]
            cstat, detail = branch_res[m]
            if cstat == "fail" and bstat == "ok":
                regressions.append((m, detail))
            elif cstat == "ok" and bstat == "fail":
                new_ok.append(m)
            elif cstat == "fail":
                preexisting.append((m, detail))

        total_regressions += len(regressions)
        report[name] = {
            "branch": branch,
            "changed": len(mods),
            "regressions": regressions,
            "newly_ok": new_ok,
            "preexisting_fail": preexisting,
        }

        print(f"\n=== {name} ({branch}) — {len(mods)} changed modules")
        print(f"    REGRESSIONS (ok at base -> fail now): {len(regressions)}")
        for m, d in regressions:
            print(f"      !! {m}\n         {d}")
        print(f"    newly importable: {len(new_ok)}   pre-existing failures: {len(preexisting)}")

    print(f"\n{'=' * 60}\nTOTAL REGRESSIONS: {total_regressions}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
    return 1 if total_regressions else 0


if __name__ == "__main__":
    sys.exit(main())
