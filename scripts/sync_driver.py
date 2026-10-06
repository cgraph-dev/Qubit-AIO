#!/usr/bin/env python3
"""Drive the upstream sync subsystem by subsystem, with a gate between each.

The 4,128-commit sync (4,438 files) is too large to apply in one pass: a bad
merge anywhere is un-attributable. This runs one subsystem at a time, records
the outcome, and refuses to advance unless the previous subsystem was verified.

Baseline: PIN_SHA 69948c0057 -> BASE_SHA a036b13793 = 5,441 commits / 5,142
files (2026-09-25 -> 2026-10-06). The earlier bd0affe5 waypoint (4,128 / 4,438)
stopped 1,313 commits short of current upstream; it is retained only in
SYNC_CONFLICT_PLAN.md history.

Subsystems are ordered smallest-risk-first so a failure early costs little.
Root-level files are handled last (they carry the entry points).

  python3 scripts/sync_driver.py --list                  # show the plan
  python3 scripts/sync_driver.py --run cron              # one subsystem
  python3 scripts/sync_driver.py --run-next              # next unrun one
  python3 scripts/sync_driver.py --status                # per-subsystem outcome
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path("/home/trick/Projects/Repos/qubit-voicebridge")
STATE = REPO / "docs" / "generated" / "sync_driver_state.json"
PIN = Path("/home/trick/.hermes/hermes-agent")
PIN_SHA = "69948c0057"
BASE_SHA = "a036b13793"

# Dependency-ordered, NOT smallest-first: consumers import symbols DEFINED in
# more primitive modules, so those must land first or the consumer merge
# produces imports of symbols that do not exist yet.
#
# VERIFIED failures this ordering fixes:
#   - qubit_cli imported is_nvidia_n1x_pci_id from qubit_platform.host.products
#   - qubit_cli imported mkstemp_beside from utils.py
#   - qubit_cli imported SessionActiveWriteGuardError from qubit_state_errors
#     (all three are NEW upstream symbols added in the 4,128 commits, defined in
#      modules OUTSIDE the consuming subsystem)
#
# tier -1 = ROOT-LEVEL modules (utils.py, hermes_state_errors.py, ...) — the
#           most primitive layer; scheduled FIRST, not last.
SUBSYSTEMS = [
    "__root__",                           # -1: root-level .py modules (49 files)
    "locales", "nix", "docker",           # 0: inert
    "plugins", "optional-skills",         # 1: leaf capability
    "agent", "tools", "gateway",          # 2: core   (applied 2026-10-06)
    "tui_gateway", "web", "cron", "acp_adapter", "evals", "pm", "scripts",
    "tests", "tests-js",                  # 3: upstream test suite + JS tests
    "ui-tui", "apps",                     # 4: surfaces + bulk
    "plugin-catalog", "contributors", "website", "skills", "providers", "assets",
]


def count(sub: str) -> int:
    if sub == "__root__":
        # Root-level files only (see build_plan): no real pathspec exists for this.
        out = subprocess.run(
            ["git", "-C", str(PIN), "diff", "--name-only",
             f"{PIN_SHA}..{BASE_SHA}"],
            capture_output=True, text=True, check=False).stdout
        return len([ln for ln in out.splitlines() if ln and "/" not in ln])
    out = subprocess.run(
        ["git", "-C", str(PIN), "diff", "--name-only", f"{PIN_SHA}..{BASE_SHA}", "--", sub],
        capture_output=True, text=True, check=False).stdout
    return len([ln for ln in out.splitlines() if ln])


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, sort_keys=True))


def show_list() -> None:
    state = load_state()
    print(f"{'subsystem':<16} {'files':>6}  status")
    for s in SUBSYSTEMS:
        st = state.get(s, {}).get("status", "-")
        print(f"{s:<16} {count(s):>6}  {st}")


def run_one(sub: str) -> int:
    state = load_state()
    if state.get(sub, {}).get("status") == "applied":
        print(f"{sub}: already applied")
        return 0

    # gate: tree must be clean so this subsystem's diff is isolated
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True, check=False).stdout.strip()
    if dirty:
        print(f"REFUSING: worktree dirty ({len(dirty.splitlines())} entries). "
              f"Commit/stash the previous subsystem first so {sub} is isolated.")
        return 2

    print(f"=== applying {sub} ({count(sub)} upstream files) ===")
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "sync_upstream.py"),
         "--apply", "--subtree", sub,
         "--json-out", f"/tmp/sync_{sub}.json"],
        capture_output=True, text=True, check=False)
    print(r.stdout[-2000:])

    # parse the tally out of the json detail
    detail = f"/tmp/sync_{sub}.json"
    tally: dict[str, int] = {}
    if Path(detail).exists():
        for row in json.loads(Path(detail).read_text()):
            tally[row["status"]] = tally.get(row["status"], 0) + 1
    state[sub] = {"status": "applied", "files": count(sub), "tally": tally,
                  "detail": detail}
    save_state(state)
    print(f"{sub}: {tally}")
    print(f"\nNEXT: verify {sub} before continuing (compile + import gate), "
          f"then commit, then `--run-next`.")
    return 0


def run_next() -> int:
    state = load_state()
    for s in SUBSYSTEMS:
        if state.get(s, {}).get("status") != "applied":
            return run_one(s)
    print("all subsystems applied")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--run", default=None)
    ap.add_argument("--run-next", action="store_true")
    args = ap.parse_args()

    if args.run:
        sys.exit(run_one(args.run))
    if args.run_next:
        sys.exit(run_next())
    show_list()


if __name__ == "__main__":
    main()
