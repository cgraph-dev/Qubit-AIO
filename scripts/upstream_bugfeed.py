#!/usr/bin/env python3
"""Alert on upstream FIXES that touch the subsystems where a latent bug costs us.

WHY THIS REPLACES THE DRIFT NUMBER
----------------------------------
Qubit stopped chasing upstream at the 2026-10-16 re-freeze. Once the port to
FROZEN_REV lands, "how many commits behind" is no longer a useful metric: it
counts catalog churn, test rewrites and features that belong to someone else's
roadmap, and it grows every day whether or not any of it matters to us.

What DOES matter is a `fix(...)` commit landing in the code paths our product
actually runs, because upstream finds those bugs with a user base we do not
have. This script watches exactly three areas:

    tools/     tool-argument schema + coercion -- voice tool-calling runs here
    gateway/   session routing, transport, delivery -- where outages live
    agent/     the agent loop and tool-call plumbing

Everything else (desktop, platforms we do not ship, catalog, docs) is ignored
on purpose. A watcher that alerts on everything alerts on nothing.

DETERMINISTIC BY DESIGN
-----------------------
Output carries no timestamp and no run-to-run noise, so a cron `monitor:` can
diff this script's output directly: identical output means no new fixes and the
agent run is skipped entirely. Only a genuinely new fix wakes it.

    usage:  python3 scripts/upstream_bugfeed.py [--fetch] [--json PATH]
                                              [--since REV] [--limit N]

    --since  measure from this revision instead of the recorded watermark.
             Use it once after the big port lands, e.g.
                 --since 43faacfb8de7b29548547a7fbdedad0cc6e7391c
    --watermark FILE
             where the "already seen" revision is stored (default under
             .git/, so it is per-checkout and never committed)

Exit codes:
    0  reported (with or without new fixes) -- stdout is the report
    2  could not measure (missing clone, bad rev, failed fetch into a stale
       mirror). Loud on purpose: a watcher that silently reports "no news"
       when it is broken is worse than no watcher.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# The three areas worth waking a human for. Prefix match against repo-relative
# paths. Keep this list SHORT -- see the docstring.
WATCHED_AREAS: dict[str, str] = {
    "tools/": "tool schema/coercion (voice tool-calling)",
    "gateway/": "session routing, transport, delivery",
    "agent/": "agent loop, tool-call plumbing",
}

# A fix is a conventional-commit whose type is fix (any scope, incl. nested
# like fix(tools/args)). Test-only commits are excluded below by path.
_FIX_RE = re.compile(r"^(?:fix|hotfix)(?:\([^)]*\))?!?:", re.IGNORECASE)

# Commits that fix the TEST SUITE or CI are not runtime bugs.
_NON_RUNTIME_RE = re.compile(r"^(?:tests?|ci|build|docs|chore)\b", re.IGNORECASE)


def upstream_clone() -> Path:
    """Locate the upstream mirror. Same candidates the drift script uses."""
    for candidate in (
        Path.home() / "upstream" / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / ".hermes.pre-retirement" / "hermes-agent",
    ):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(
        "upstream_bugfeed: cannot locate the upstream clone "
        "(tried ~/upstream/hermes-agent and the retired hermes locations)"
    )


def git(clone: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if p.returncode != 0:
        raise SystemExit(
            f"upstream_bugfeed: git {' '.join(args)} failed: {p.stderr.strip()[:300]}"
        )
    return p.stdout


def is_runtime_fix(subject: str) -> bool:
    return bool(_FIX_RE.match(subject)) and not _NON_RUNTIME_RE.match(subject)


def commits_touching_area(clone: Path, since: str, head: str) -> dict[str, list[str]]:
    """Map sha -> watched areas it touched, in ONE git call.

    Per-commit `git show` is O(commits) subprocesses; over a 900-commit window
    that is minutes of wall time for a job that runs unattended. Asking git for
    every changed path in the range once and bucketing them is a single call.
    """
    raw = git(
        clone,
        "log",
        "--no-decorate",
        "--name-only",
        "--format=%x01%H",
        f"{since}..{head}",
    )
    out: dict[str, list[str]] = {}
    sha = None
    hit: set[str] = set()
    for line in raw.splitlines():
        if line.startswith("\x01"):
            if sha is not None:
                out[sha] = [a for a in WATCHED_AREAS if a in hit]
            sha = line[1:].strip()
            hit = set()
        elif line.strip():
            for area in WATCHED_AREAS:
                if line.startswith(area):
                    hit.add(area)
    if sha is not None:
        out[sha] = [a for a in WATCHED_AREAS if a in hit]
    return out


def read_watermark(path: Path) -> str | None:
    try:
        rev = path.read_text().strip()
        return rev or None
    except FileNotFoundError:
        return None


def write_watermark(path: Path, rev: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rev + "\n")


def default_watermark() -> Path:
    """Per-checkout, never committed: the mirror's last-alerted head."""
    return Path(__file__).resolve().parents[1] / ".git" / "upstream-bugfeed.sha"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="git fetch before measuring")
    ap.add_argument("--json", metavar="PATH", help="also write the report as JSON here")
    ap.add_argument(
        "--since",
        metavar="REV",
        help="measure from this revision instead of the stored watermark",
    )
    ap.add_argument(
        "--watermark",
        metavar="FILE",
        help="path to the watermark file (default .git/upstream-bugfeed.sha)",
    )
    ap.add_argument("--limit", type=int, default=25, help="max commits to list")
    args = ap.parse_args()

    clone = upstream_clone()

    if args.fetch:
        # Same lesson as the drift script: a failed fetch must not look like
        # "nothing new". Report it and measure the last-known ref.
        res = subprocess.run(
            ["git", "-C", str(clone), "fetch", "--quiet", "origin", "main"],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if res.returncode != 0:
            msg = (res.stderr or res.stdout or "").strip()
            print(
                f"BUGFEED FETCH FAILED (rc={res.returncode}) -- measuring the "
                f"LAST-KNOWN ref, which may be stale: {msg[:300]}"
            )

    head = git(clone, "rev-parse", "origin/main").strip()
    wm_path = Path(args.watermark) if args.watermark else default_watermark()
    since = args.since or read_watermark(wm_path)

    # First run with no watermark: start at head and say so, rather than
    # dumping the entire history as "new".
    first_run = since is None
    if first_run:
        since = head

    if subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", since], capture_output=True
    ).returncode != 0:
        raise SystemExit(
            f"upstream_bugfeed: watermark rev {since[:12]} is not in the mirror. "
            "The mirror was re-cloned or force-pushed; pass --since with a valid rev."
        )

    log = git(
        clone,
        "log",
        "--no-decorate",
        "--format=%H%x1f%s%x1f%an",
        f"{since}..{head}",
    ).splitlines()

    fixes = []
    area_map = commits_touching_area(clone, since, head)
    for line in log:
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        sha, subject, author = parts[0], parts[1], parts[2]
        if not is_runtime_fix(subject):
            continue
        areas = area_map.get(sha, [])
        if not areas:
            continue
        fixes.append(
            {"sha": sha, "subject": subject, "author": author, "areas": areas}
        )

    # Deterministic headline: no timestamp, so a cron monitor can diff this.
    print(f"UPSTREAM BUGFEED  since={since[:12]}  head={head[:12]}")
    print(f"commits_scanned={len(log)}  runtime_fixes={len(fixes)}")
    for area, why in WATCHED_AREAS.items():
        n = sum(1 for f in fixes if area in f["areas"])
        print(f"  {area:10} {n:3}  ({why})")

    if fixes:
        print("\nNEW RUNTIME FIXES:")
        for f in fixes[: args.limit]:
            print(f"  {f['sha'][:10]}  [{','.join(f['areas'])}]  {f['subject']}")
        if len(fixes) > args.limit:
            print(f"  ... {len(fixes) - args.limit} more")
        print(
            "\nThese are fixes upstream found with a user base we do not have. "
            "Read them; port the ones that apply."
        )
    elif not first_run:
        # Nothing to say. The cron monitor reads this exact string.
        print("\nno new runtime fixes in tools/, gateway/ or agent/")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"since": since, "head": head, "scanned": len(log), "fixes": fixes},
                indent=2,
            )
            + "\n"
        )

    # Advance the watermark only after a successful run, so a crash mid-run
    # re-reports rather than silently swallowing fixes.
    if not args.since:
        write_watermark(wm_path, head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
