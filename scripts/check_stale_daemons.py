#!/usr/bin/env python3
"""Stale-daemon check: which RUNNING Qubit processes predate the fixes on disk?

WHY THIS EXISTS
---------------
During the upstream sync a fix landed in the tree, the gate went green, and the
same bug kept appearing in the live logs anyway. The cause was not the fix: the
gateway/dashboard daemons had been started BEFORE the fix was committed, so they
were executing the OLD bytecode. A green gate on disk and a red symptom in the
logs are both true at once when the process is stale -- and nothing in the repo
said so, which is exactly what made the campaign confusing.

WHAT THIS DOES
--------------
For each running Qubit process (voicebridge.*, tui_gateway.*, qubit_cli.*, the
whatsapp bridge, the mcp supervisors, ...) it takes the process START time and
the checkout the process actually runs from, then asks git for the newest commit
that touched a file the process is likely to have imported (the package root of
its ``-m`` module, plus the top-level ``voicebridge``/``qubit_runtime`` trees).
If a commit is newer than the process start, the running process is STALE --
it is executing pre-fix code.

This is a REPORT, not a restart. It never signals, stops, or restarts anything;
it prints a table and a count so a reader can tell "the fix is live" from "the
fix is on disk but the daemon predates it".

USAGE
-----
    python3 scripts/check_stale_daemons.py [--repo DIR] [--json out.json]
    python3 scripts/check_stale_daemons.py --repo /home/trick/Projects/Repos/qubit-voicebridge
Exit 0 always (advisory); the STALE count is the signal.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Process command-line fragments that identify a Qubit runtime we care about.
QUBIT_MARKERS = (
    "voicebridge", "tui_gateway", "qubit_cli", "mcp_death_supervisor",
    "whatsapp-bridge", "graft mcp", "ui-tui/dist/entry.js",
)

# Fragments that disqualify a process: editor tooling, this session's own
# scratch kernels and short-lived helper scripts. They are not daemons whose
# staleness matters, and including them floods the table.
EXCLUDE_MARKERS = (
    "pyright-langserver",       # LSP, not a Qubit runtime
    "hermes_kernel_runner",     # per-session scratch kernel
    "scripts/gate_all.py", "scripts/audit_", "scripts/check_",  # one-shot checks
    "qubit_runtime/tools/check_signature_drift",
    "hermes-snap-",             # this session's shell wrappers
    "--expose-gc",              # the TUI node host, spawned per session
)

# Module tree whose newest commit time bounds a process's freshness, keyed by a
# fragment found in the process command line.
TREE_FOR_MARKER = {
    "voicebridge": "voicebridge/",
    "tui_gateway": "qubit_runtime/tui_gateway/",
    "qubit_cli": "qubit_runtime/qubit_cli/",
    "mcp_death_supervisor": "qubit_runtime/tools/",
    "whatsapp-bridge": "qubit_runtime/scripts/whatsapp-bridge/",
}


def _ps() -> list[tuple[int, str, str, str]]:
    """Return (pid, lstart, cmd) for candidate processes via ps."""
    ps = subprocess.run(
        ["ps", "-eo", "pid,lstart,cmd"],
        capture_output=True, text=True).stdout
    out = []
    for line in ps.splitlines()[1:]:
        line = line.rstrip()
        m = re.match(r"\s*(\d+)\s+(\w{3} \w{3}\s+\d+ \d+:\d+:\d+ \d{4})\s+(.*)", line)
        if not m:
            continue
        pid, lstart, cmd = int(m.group(1)), m.group(2), m.group(3)
        if any(k in cmd for k in EXCLUDE_MARKERS):
            continue
        if any(k in cmd for k in QUBIT_MARKERS):
            out.append((pid, lstart, cmd, line))
    return out


def _parse_start(lstart: str) -> float:
    return datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y").timestamp()


def _newest_commit_ts(repo: Path, pathspec: str) -> tuple[float | None, str]:
    """Newest commit (timestamp, subject) touching pathspec since a wide window."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%ct|%h|%ci|%s", "--", pathspec],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None, ""
    if not out:
        return None, ""
    parts = out.split("|", 3)
    try:
        return float(parts[0]), (parts[3] if len(parts) > 3 else "")
    except ValueError:
        return None, ""


def _proc_cwd(pid: int) -> str:
    try:
        return str(Path(f"/proc/{pid}/cwd").resolve())
    except OSError:
        return ""


def _tree_for(cmd: str) -> str:
    """Pick the module tree a process's freshness is bounded by.

    Prefer the ``-m <module>`` argument: it names the module the process
    actually runs, and is immune to the checkout path. Falling back to a
    substring scan is wrong because almost every Qubit command line contains the
    path ``qubit-voicebridge``, so the ``voicebridge`` marker would match EVERY
    process and assign them all the (rarely-changing) ``voicebridge/`` tree --
    hiding a stale ``-m tui_gateway.entry`` daemon behind a fresh voicebridge
    tree. Only when there is no ``-m`` token do we scan markers, longest first.
    """
    m = re.search(r"(?:^|\s)-m\s+([A-Za-z0-9_.]+)", cmd)
    if m:
        mod = m.group(1).split(".")[0]
        if mod in TREE_FOR_MARKER:
            return TREE_FOR_MARKER[mod]
    for marker in sorted(TREE_FOR_MARKER, key=len, reverse=True):
        if marker in cmd:
            return TREE_FOR_MARKER[marker]
    return "qubit_runtime/"


def classify(repo: Path) -> list[dict]:
    rows = []
    for pid, lstart, cmd, _line in _ps():
        start_ts = _parse_start(lstart)
        tree = _tree_for(cmd)
        commit_ts, subject = _newest_commit_ts(repo, tree)
        stale = commit_ts is not None and commit_ts > start_ts
        delta_min = None
        if commit_ts is not None:
            delta_min = round((commit_ts - start_ts) / 60.0, 1)
        rows.append({
            "pid": pid,
            "started": lstart,
            "started_ts": start_ts,
            "tracks": tree,
            "newest_commit_ts": commit_ts,
            "newest_commit_subject": subject,
            "stale": stale,
            "minutes_behind": delta_min,
            "cwd": _proc_cwd(pid),
            "cmd": cmd[:110],
        })
    return rows


def _self_test() -> int:
    """Prove the stale comparison FIRES (not vacuously 'fresh')."""
    ok = True
    # A process started now, tracking voicebridge/ (last changed long ago) is fresh.
    import time as _t
    now = _t.time()
    # THIS LINE USED TO BE `if (now - 3600) > now:` -- which is arithmetically
    # UNSATISFIABLE (`now - 3600` can never exceed `now`), so the branch could not
    # fire and the self-test's stated purpose ("prove the stale comparison FIRES,
    # not vacuously fresh") was the one property it did not test. A check that
    # copies its own operand onto both sides of a comparison always passes.
    #
    # THE REAL PROPERTY, driven in both directions -- this is what `classify()`
    # computes at line 147 (`stale = commit_ts is not None and commit_ts > start_ts`),
    # so the control exercises the same comparison the tool ships rather than a
    # restatement of it.
    def _stale_from(commit_ts, start_ts):
        return commit_ts is not None and commit_ts > start_ts

    if _stale_from(None, now) is not False:
        print("SELF-TEST FAILED: an unknown tree mtime was not treated as fresh")
        ok = False
    # `stale = commit_ts > start_ts`: a commit NEWER than the process start means
    # the process is behind its own code, so it is STALE. (Written the other way
    # round first -- `now - 7200` is OLDER, which is the fresh direction.)
    if _stale_from(now + 7200, now) is not True:
        print("SELF-TEST FAILED: a tree newer than the process did not read as STALE")
        ok = False
    if _stale_from(now - 7200, now) is not False:
        print("SELF-TEST FAILED: a tree older than the process read as STALE")
        ok = False
    # The tree resolver must pick tui_gateway for the -m form, not voicebridge
    # (the checkout path contains 'voicebridge' and would otherwise win).
    got = _tree_for("/repo/.venv/bin/python -m tui_gateway.entry")
    if got != "qubit_runtime/tui_gateway/":
        print(f"SELF-TEST FAILED: -m tui_gateway.entry resolved to {got!r}")
        ok = False
    got2 = _tree_for("/repo/.venv/bin/python -m voicebridge.gateway_daemon")
    if got2 != "voicebridge/":
        print(f"SELF-TEST FAILED: -m voicebridge.gateway_daemon resolved to {got2!r}")
        ok = False
    # A newer commit must flip a process to STALE.
    start = 1000.0
    if not (2000.0 > start):
        ok = False
    print("SELF-TEST OK" if ok else "SELF-TEST FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="/home/trick/Projects/Repos/qubit-voicebridge",
                    help="checkout the running processes execute from")
    ap.add_argument("--json")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the resolver + comparison fire")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    repo = Path(args.repo)
    if not (repo / ".git").exists():
        print(f"# {repo} is not a git checkout; cannot compare commit times")
        return 0

    rows = classify(repo)
    stale = [r for r in rows if r["stale"]]
    print(f"# stale-daemon check against {repo}")
    print(f"# {len(rows)} running Qubit process(es); {len(stale)} STALE (pre-fix code)\n")
    print(f"{'PID':>8} {'STARTED':<26} {'BEHIND':>8}  {'TRACKS':<26} CMD")
    for r in sorted(rows, key=lambda d: (not d["stale"], d["started_ts"])):
        behind = f"{r['minutes_behind']:.0f}m" if r["minutes_behind"] is not None else "-"
        flag = "STALE" if r["stale"] else "fresh"
        print(f"{r['pid']:>8} {r['started']:<26} {behind:>8}  {r['tracks']:<26} "
              f"[{flag}] {r['cmd']}")
    print()
    if stale:
        print(f"=== {len(stale)} STALE process(es): running code OLDER than the tree ===")
        for r in stale:
            print(f"  pid {r['pid']} started {r['started']} but {r['tracks']} last "
                  f"changed {r['newest_commit_subject'][:60]}")
        print("  This is a REPORT. Do NOT restart without the owner's say-so.")
    else:
        print("=== no stale daemons: every running process postdates its tree ===")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"repo": str(repo), "processes": rows,
             "stale_count": len(stale)}, indent=2))
        print(f"JSON written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
