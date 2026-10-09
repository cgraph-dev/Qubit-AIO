#!/usr/bin/env python3
"""Report how far upstream has drifted from our pinned milestone.

Deterministic output (no timestamps, no run-to-run noise) so a cron `monitor:` can use this
file directly as a change detector: identical output skips the agent run entirely, and only a
GENUINE upstream movement wakes it.

Resolves the upstream clone the same way the audit scripts do, so it keeps working after the
hermes home was retired (the clone now lives at ~/upstream/hermes-agent).

    usage:  python3 scripts/upstream_drift.py [--fetch]
            --fetch   git fetch first (network); omit to measure the last-known ref
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def upstream_clone() -> Path:
    for candidate in (
        Path.home() / "upstream" / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / ".hermes.pre-retirement" / "hermes-agent",
    ):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit("cannot locate the upstream clone (tried ~/upstream/hermes-agent and the "
                     "retired hermes locations)")


def git(clone: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(clone), *args],
                       capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def pinned_rev() -> str:
    """The pinned milestone, read from the burndown instrument when it is next to us.

    Single source of truth: frozen_target_progress.py holds FROZEN_REV. When this script is
    copied into the runtime scripts dir (cron), that sibling is absent, so fall back to the
    repo checkout, then to the recorded constant in docs/FROZEN_TARGET.md.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parent / "frozen_target_progress.py",
        Path.home() / "Projects" / "Repos" / "qubit-voicebridge" / "scripts" / "frozen_target_progress.py",
    ]
    for script in candidates:
        if not script.exists():
            continue
        for line in script.read_text().splitlines():
            if line.startswith("FROZEN_REV"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    # Last resort: the pinned sha recorded in docs/FROZEN_TARGET.md.
    doc = Path.home() / "Projects" / "Repos" / "qubit-voicebridge" / "docs" / "FROZEN_TARGET.md"
    if doc.exists():
        for line in doc.read_text().splitlines():
            if "target commit" in line.lower() and ":" in line:
                return line.split(":", 1)[1].strip()
    raise SystemExit("cannot determine the pinned revision (no frozen_target_progress.py and no "
                     "docs/FROZEN_TARGET.md); refusing to measure against an unknown pin")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="git fetch before measuring")
    ap.add_argument("--json", help="also write the numbers here")
    args = ap.parse_args()

    clone = upstream_clone()
    fetched = False
    if args.fetch:
        # CHECK THE RESULT. A failed fetch and a successful one must not look alike: if the
        # fetch fails we fall back to the last-known ref, and then a frozen mirror reports
        # "0 commits behind" forever -- a monitor that cannot fail loudly is worse than none.
        res = subprocess.run(["git", "-C", str(clone), "fetch", "--quiet", "origin", "main"],
                             capture_output=True, text=True, errors="replace")
        if res.returncode != 0:
            msg = (res.stderr or res.stdout or "").strip()
            print(f"FETCH FAILED (rc={res.returncode}) -- measuring the LAST-KNOWN ref, "
                  f"which may be stale: {msg[:300]}")
        else:
            fetched = True

    pin = pinned_rev()
    head = git(clone, "rev-parse", "origin/main")

    # A pin the mirror has never seen means the mirror predates the pin entirely; every number
    # below would be meaningless. Refuse rather than print a confident lie.
    if subprocess.run(["git", "-C", str(clone), "cat-file", "-e", pin],
                      capture_output=True).returncode != 0:
        raise SystemExit(f"upstream mirror does not contain the pinned rev {pin[:12]}. "
                         "The clone is stale or the pin is wrong -- refusing to report drift "
                         "numbers that cannot be true.")

    pin_short, head_short = pin[:12], head[:12]

    behind = int(git(clone, "rev-list", "--count", f"{pin}..{head}"))

    files = git(clone, "diff", "--name-only", pin, head).splitlines()
    py = [f for f in files if f.endswith(".py")]
    tests = [f for f in py if f.startswith("tests/") or "/tests/" in f]
    catalog = [f for f in files if "plugin-catalog" in f]

    # Deterministic headline. Deliberately NO timestamp: a monitor must not see a new diff
    # just because time passed.
    print(f"UPSTREAM DRIFT  pin={pin_short}  head={head_short}")
    print(f"commits_behind={behind}")
    print(f"files_changed={len(files)}  py={len(py)}  py_production={len(py) - len(tests)}  "
          f"py_tests={len(tests)}  plugin_catalog={len(catalog)}")
    if behind:
        print("recent_subjects:")
        for line in git(clone, "log", "--oneline", "--no-decorate",
                        f"{pin}..{head}").splitlines()[:15]:
            print(f"  {line}")
    else:
        print("recent_subjects: (none — we are at upstream HEAD)")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "pin": pin, "head": head, "commits_behind": behind,
            "files_changed": len(files), "py_production": len(py) - len(tests),
            "py_tests": len(tests), "plugin_catalog": len(catalog),
        }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
