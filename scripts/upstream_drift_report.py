#!/usr/bin/env python3
"""Answer ONE question truthfully: how far behind upstream are we, in COMMITS
and in REAL WORK?

WHY THIS EXISTS
---------------
Reports in this repo have variously claimed 3,842, 11,066, 4,473 and 584 for
"how far behind are we". Every one was arithmetically correct and every one was
answering a DIFFERENT question:

  * commit count          -- how many commits upstream landed since a revision
  * changed-file count    -- how many paths differ across that range
  * branch-only-line count-- how many lines one side has the other lacks

They are not interchangeable, and quoting one in place of another is how the
owner was misled. This script prints all of them, side by side, labelled, from
the FROZEN PIN (not from an arbitrary waypoint like the stale Sept `69948c0057`
or the Oct-06 `a036b13793` sync baseline), and it separates the two things that
actually matter:

    "upstream is merely NEWER on files we already carry"   (drift, not work)
    "upstream has files we do NOT have at all"             (genuine additions)

THE OVERLAP LENS (adapted upstream)
-----------------------------------
This repo already uses the overlap lens for a branch against our HEAD. Applied
to a FOREIGN repo -- upstream shares no merge base with our vendored tree -- the
same idea runs against the PIN as the common ancestor and OUR TREE as the "head"
side:

    base            = FROZEN_REV            (the last revision we vendored from)
    branch          = upstream origin/main  (what upstream has now)
    branch_changed  = git diff --name-only FROZEN_REV..origin/main
    our_tree        = our vendored paths, mapped through the rename engine
    both_changed    = branch_changed that ALSO exist here  -> upstream newer on
                      a file we carry: this is drift/conflict territory, NOT new
    only_branch     = branch_changed absent here           -> genuine additions
    deleted         = branch_changed that upstream removed

For every path in only_branch that ALSO exists here we would count branch_new
lines; in practice a path that exists here is in both_changed by construction, so
only_branch is almost entirely clean adds -- reported as such.

RENAME AWARENESS
----------------
Our tree is a source-level rebrand (`hermes_cli/` -> `qubit_cli/`, `hermes_*` ->
`qubit_*`). Comparing raw upstream paths against raw local paths would report
every vendored-and-renamed file as ABSENT and inflate the additions count by
roughly an order of magnitude. So the existence test routes each upstream path
through the SAME map the audits use (`audit_sync_completeness.route`), imported
from that module rather than re-derived, so this script and the audits can never
disagree about what "we already have" means.

DETERMINISM
-----------
No timestamps in the numbers, no wall-clock. Identical upstream state -> identical
output, so a cron monitor can use the whole output as a change detector. The
resolved HEAD sha is printed so a reader knows exactly what was measured.

Usage:
    .venv/bin/python scripts/upstream_drift_report.py
    .venv/bin/python scripts/upstream_drift_report.py --json out.json
    .venv/bin/python scripts/upstream_drift_report.py --upstream ~/upstream/hermes-agent
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "qubit_runtime"

# Refuse to run from a copy outside the checkout: the existence test resolves
# local paths from REPO, so a copy in /tmp would report EVERY upstream file as a
# genuine addition -- a plausible, badly wrong number.
if not RUNTIME.is_dir() or not (REPO / "scripts").is_dir():
    raise SystemExit(
        f"REFUSING: computed repo root {REPO} is not a Qubit checkout.\n"
        "Run `.venv/bin/python scripts/upstream_drift_report.py` from the repo "
        "(do not copy it elsewhere -- it resolves local paths from __file__)."
    )

# The frozen pin is the single source of truth held by the burndown instrument.
# Read it from there (never hardcode a second copy) so the two can never drift.
PIN_SOURCE = REPO / "scripts" / "frozen_target_progress.py"

# Directories judged to be the porting-critical core: a change here costs port
# effort. Kept as prefix matches on the upstream path.
CORE_DIRS = ("agent/", "gateway/", "tools/", "cron/", "hermes_cli/")


def frozen_pin() -> str:
    """The FROZEN_REV constant, read from its single declared home."""
    if not PIN_SOURCE.is_file():
        raise SystemExit(f"cannot read the frozen pin: {PIN_SOURCE} is missing")
    for line in PIN_SOURCE.read_text().splitlines():
        if line.startswith("FROZEN_REV"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"no FROZEN_REV constant in {PIN_SOURCE}")


def _upstream_clone() -> Path:
    """Locate the upstream clone, tolerating the hermes home being retired."""
    for candidate in (
        Path.home() / "upstream" / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / ".hermes.pre-retirement" / "hermes-agent",
    ):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(
        "cannot locate the upstream clone (tried ~/upstream/hermes-agent, "
        "~/.hermes/hermes-agent, ~/.hermes.pre-retirement/hermes-agent); "
        "refusing to measure drift against a directory that does not exist."
    )


def _load_route():
    """Import the audits' path router so 'we already have it' means the same here.

    Importing (rather than re-deriving the rename map) is the whole point: a
    local re-implementation would silently diverge from the rename engine and
    this report would then contradict the audits it exists to explain.
    """
    audit = REPO / "scripts" / "audit_sync_completeness.py"
    if not audit.is_file():
        raise SystemExit(f"cannot import the path router: {audit} is missing")
    spec = importlib.util.spec_from_file_location("_drift_audit_router", audit)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load the path router module")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                                  # noqa: BLE001
        raise SystemExit(f"cannot exec the path router module: {exc}")
    fn = getattr(mod, "route", None)
    if not callable(fn):
        raise SystemExit("the audit module has no route() -- refusing to guess")
    return fn


def git(clone: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True, text=True, errors="replace",
    )
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def is_test_path(p: str) -> bool:
    """A path is a test iff it is under tests/ (also plugins/*/tests/)."""
    return p.startswith("tests/") or "/tests/" in p


def is_production_py(p: str) -> bool:
    return p.endswith(".py") and not is_test_path(p)


def exists_here(route, upstream_path: str) -> bool:
    """Does our vendored tree carry the routed counterpart of this path?"""
    local = route(upstream_path)
    for base in (RUNTIME, REPO):
        if (base / local).exists():
            return True
    return False


def measure(clone: Path, pin: str) -> dict:
    head = git(clone, "rev-parse", "origin/main").strip()

    # A pin the mirror has never seen means the mirror predates the pin; every
    # number below would be meaningless. Refuse rather than print a confident lie.
    if subprocess.run(["git", "-C", str(clone), "cat-file", "-e", pin],
                      capture_output=True).returncode != 0:
        raise SystemExit(
            f"upstream mirror does not contain the frozen pin {pin[:12]}. "
            "The clone is stale or the pin is wrong -- refusing to report drift "
            "numbers that cannot be true."
        )

    commits_behind = int(git(clone, "rev-list", "--count", f"{pin}..{head}").strip())

    # --name-only over the whole range: the canonical changed-file set, one path
    # per line (rename-aware: a rename contributes its new path). This is the
    # 5,314-class number and it is files, NOT commits.
    changed = [ln for ln in git(clone, "diff", "--name-only", f"{pin}..{head}").splitlines()
               if ln.strip()]
    changed = sorted(set(changed))

    # Status per path (A/M/D) so additions can be told from modifications.
    status: dict[str, str] = {}
    for line in git(clone, "diff", "--name-status", "--no-renames",
                    f"{pin}..{head}").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        st = parts[0]
        if st[0] in "RC" and len(parts) >= 3:
            status[parts[1]] = "A"      # rename target is an add at the new path
            status.setdefault(parts[2], "D")
        elif len(parts) >= 2:
            status[parts[1]] = st

    route = _load_route()

    # --- the overlap lens, applied upstream -------------------------------
    clean_add: list[str] = []       # only_branch: genuine additions
    upstream_newer: list[str] = []  # both_changed class, status M: drift, not work
    both_changed: list[str] = []    # exists here (any status) -> conflict territory
    deleted: list[str] = []

    for p in changed:
        here = exists_here(route, p)
        st = status.get(p, "?")
        if st == "D":
            deleted.append(p)
        if here:
            both_changed.append(p)
        elif st == "A":
            clean_add.append(p)
        elif st == "M":
            upstream_newer.append(p)

    def bucket(paths: list[str]) -> dict:
        prod = [p for p in paths if is_production_py(p)]
        core = [p for p in prod if p.startswith(CORE_DIRS)]
        return {
            "files": len(paths),
            "production_py": len(prod),
            "core_production_py": len(core),
        }

    py_all = [p for p in changed if p.endswith(".py")]
    prod_all = [p for p in changed if is_production_py(p)]
    core_prod_all = [p for p in prod_all if p.startswith(CORE_DIRS)]

    return {
        "pin": pin,
        "head": head,
        "commits_behind": commits_behind,
        "files_changed": len(changed),
        "production_py_changed": len(prod_all),
        "files_changed_py_all": len(py_all),
        "core_dirs_production_py_changed": len(core_prod_all),
        "overlap": {
            "genuine_additions": bucket(clean_add),
            "upstream_merely_newer": bucket(upstream_newer),
            "conflict_territory_existing_files": bucket(both_changed),
            "upstream_deleted": bucket(deleted),
            "_genuine_additions_paths_sample": clean_add[:25],
        },
        "core_dir_breakdown_additions": Counter(
            p.split("/")[0] for p in clean_add if is_production_py(p)
        ).most_common(),
    }


def render(m: dict) -> str:
    o = m["overlap"]
    ga = o["genuine_additions"]
    un = o["upstream_merely_newer"]
    ct = o["conflict_territory_existing_files"]
    dl = o["upstream_deleted"]
    L: list[str] = []
    L.append(f"UPSTREAM DRIFT REPORT  pin={m['pin'][:12]}  head={m['head'][:12]}")
    L.append("")
    L.append("HOW FAR BEHIND (each number answers a different question):")
    L.append(f"  commits_behind                 = {m['commits_behind']}")
    L.append(f"  files_changed                  = {m['files_changed']}")
    L.append(f"  files_changed (all .py)        = {m['files_changed_py_all']}")
    L.append(f"  production_py_changed          = {m['production_py_changed']}")
    L.append(f"  core-dirs production_py_changed= {m['core_dirs_production_py_changed']}"
             f"   (dirs: {', '.join(d.rstrip('/') for d in CORE_DIRS)})")
    L.append("")
    L.append("OVERLAP LENS (what is REAL WORK vs what is upstream merely being newer):")
    L.append(f"  genuine additions (absent here)        = {ga['files']} files "
             f"/ {ga['production_py']} production .py / {ga['core_production_py']} core")
    L.append(f"  upstream merely newer (we already have)= {ct['files']} files "
             f"/ {ct['production_py']} production .py / {ct['core_production_py']} core")
    L.append(f"     of which plain modifications        = {un['files']} files "
             f"/ {un['production_py']} production .py")
    L.append(f"  deleted upstream                       = {dl['files']} files")
    L.append("")
    if m["core_dir_breakdown_additions"]:
        L.append("GENUINE ADDITIONS by top-level dir (production .py only):")
        for d, n in m["core_dir_breakdown_additions"]:
            L.append(f"  {d:<24}{n:>5}")
        L.append("")
    L.append("HEADLINE:")
    L.append(
        f"  We are {m['commits_behind']} commits behind upstream, across "
        f"{m['files_changed']} changed files; {m['production_py_changed']} are "
        f"production .py ({m['core_dirs_production_py_changed']} inside the core "
        f"dirs), of which {ga['production_py']} are files we do not have at all "
        f"({ga['core_production_py']} in core dirs) -- the genuine porting work -- "
        f"while {ct['production_py']} are files we already carry that upstream has "
        f"since revised (drift/conflict territory, not new work)."
    )
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", help="path to the upstream clone "
                    "(default: auto-detect)")
    ap.add_argument("--json", help="write the measured numbers here")
    args = ap.parse_args()

    clone = Path(args.upstream).expanduser().resolve() if args.upstream \
        else _upstream_clone()
    pin = frozen_pin()
    m = measure(clone, pin)
    print(render(m))

    if args.json:
        Path(args.json).write_text(json.dumps(m, indent=2, default=list))
        print(f"\nJSON written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
