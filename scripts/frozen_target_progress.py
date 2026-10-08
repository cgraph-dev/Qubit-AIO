#!/usr/bin/env python3
"""BURNDOWN INSTRUMENT: are we at the FROZEN target yet?

ONE COMMAND answers the finish-line question for the freeze pin
`ad12263a5ed4` (see docs/FROZEN_TARGET.md). It runs the completeness audit
against that revision ONLY -- never `origin/main`, which drifts (it moved 11
commits DURING the audit that produced the baseline numbers), and prints a
single verdict line so no human has to read a full audit to know whether the
campaign is done.

WHY THIS EXISTS
---------------
Three agents port toward the freeze in parallel. Without a one-command
readout, "are we there yet?" is answered by a human parsing an audit, and the
answer changes under them. This script fixes the reference revision, fixes the
baseline, and reduces the whole thing to:
    NOT AT TARGET -- N missing
    AT TARGET
plus the DELTA against the frozen baseline, so movement is measured, not felt.

PRODUCTION vs TESTS
-------------------
Tests do NOT block retiring hermes; production does. The audit reports one
combined number, which hides that most of the gap is test files the retirement
never touches. This script splits on the `tests/` path segment (also matching
`plugins/*/tests/`): a missing path is a test iff it starts with `tests/` or
contains a `/tests/` segment. The verdict is driven by PRODUCTION only.

BASELINE (from docs/FROZEN_TARGET.md, clean tree, `--rev ad12263a5ed4`)
-----------------------------------------------------------------------
    absent              261   (234 tests -> ~27 production)
    short_port        111 / 272   files / symbols
    behind_head       214 / 703   files / symbols
    total missing       975
These are FROZEN constants. If the audit against `ad12263a5ed4` disagrees with
its own baseline, that is a bug in this instrument or a moved pin -- not
progress -- and it is flagged loudly.

Usage:
    .venv/bin/python scripts/frozen_target_progress.py
    .venv/bin/python scripts/frozen_target_progress.py --json out.json
    .venv/bin/python scripts/frozen_target_progress.py --no-rerun   # reuse cache
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Refuse to run outside a real checkout: the audit resolves paths from __file__,
# so a copy elsewhere reports every file absent -- a plausible-looking artifact.
if not (REPO / "qubit_runtime").is_dir() or not (REPO / "scripts").is_dir():
    raise SystemExit(
        f"REFUSING: computed repo root {REPO} is not a Qubit checkout.\n"
        "Run `.venv/bin/python scripts/frozen_target_progress.py` from the repo."
    )

# THE FROZEN PIN. Never origin/main. See docs/FROZEN_TARGET.md.
FROZEN_REV = "38880bd2f1e90dbc9a1aeec03af62539ee64719a"

AUDIT = REPO / "scripts" / "audit_sync_completeness.py"
CACHE = REPO / ".frozen_target_progress.cache.json"

# Baseline at the freeze. DO NOT EDIT without re-freezing the pin.
BASE = {
    "absent": 261,
    "short_port_files": 111,
    "short_port_symbols": 272,
    "behind_head_files": 214,
    "behind_head_symbols": 703,
    "total_missing": 975,
}


def is_test_path(p: str) -> bool:
    """True iff an upstream path is a test, so it cannot block retiring hermes."""
    return p.startswith("tests/") or "/tests/" in p


def run_audit() -> dict:
    """Run the completeness audit against FROZEN_REV and return its JSON."""
    with tempfile.NamedTemporaryFile(
        mode="r", suffix=".json", delete=False, dir=str(REPO)
    ) as tf:
        out_path = tf.name
    cmd = [
        sys.executable,
        str(AUDIT),
        "--rev", FROZEN_REV,          # ALWAYS the frozen pin, never origin/main
        "--show-symbols",             # needed to split behind_head by path
        "--json", out_path,
    ]
    print(f"# running audit --rev {FROZEN_REV} (this takes a few minutes)...",
          file=sys.stderr)
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], file=sys.stderr)
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"REFUSING: audit exited {proc.returncode}")
    data = json.loads(Path(out_path).read_text())
    Path(out_path).unlink(missing_ok=True)
    return data


def load_cache() -> dict:
    if not CACHE.exists():
        raise SystemExit(
            f"REFUSING: --no-rerun but no cache at {CACHE}. "
            "Run once without --no-rerun."
        )
    return json.loads(CACHE.read_text())


def split(data: dict) -> dict:
    """Split the audit's numbers into production vs test buckets."""
    absent = data.get("absent_paths", [])
    absent_prod = [p for p in absent if not is_test_path(p)]
    absent_test = [p for p in absent if is_test_path(p)]

    # worst_short_ports carries per-file symbol counts for the short-port branch.
    sp = data.get("worst_short_ports", [])
    sp_prod = [w for w in sp if not is_test_path(w["path"])]
    sp_test = [w for w in sp if is_test_path(w["path"])]

    # The JSON only enumerates the top short-port files; totals come from the
    # summary fields. Split by the enumerated share, keep totals authoritative.
    sp_symbols = data["short_port_symbols"]
    sp_test_share = sum(w["missing"] for w in sp_test)
    sp_prod_share = sp_symbols - sp_test_share if sp else sp_symbols

    # behind_head IS NOT production-only. The `symbols` dict enumerates every
    # behind_head file with its missing names; split it by path. (Reading the
    # combined summary field as production over-charged the gap by ~250 symbols
    # for several waves, because ~80% of behind-head files are TESTS.)
    syms = data.get("symbols") or {}
    bh_prod = [k for k, v in syms.items()
               if isinstance(v, dict) and v.get("branch") == "behind_head"
               and not is_test_path(k)]
    bh_test = [k for k, v in syms.items()
               if isinstance(v, dict) and v.get("branch") == "behind_head"
               and is_test_path(k)]
    bh_prod_syms = sum(len(syms[k]["missing"]) for k in bh_prod)
    bh_test_syms = sum(len(syms[k]["missing"]) for k in bh_test)

    return {
        "absent_prod": len(absent_prod),
        "absent_test": len(absent_test),
        "sp_files_prod": len(sp_prod),
        "sp_files_test": len(sp_test),
        "sp_symbols_prod": sp_prod_share,
        "sp_symbols_test": sp_test_share,
        "bh_files_prod": len(bh_prod),
        "bh_files_test": len(bh_test),
        "bh_symbols_prod": bh_prod_syms,
        "bh_symbols_test": bh_test_syms,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", help="write the measured numbers here")
    ap.add_argument("--no-rerun", action="store_true",
                    help="reuse the last measurement instead of re-running the audit")
    args = ap.parse_args()

    if args.no_rerun:
        data = load_cache()
    else:
        data = run_audit()
        CACHE.write_text(json.dumps(data, indent=2))

    assert data.get("rev") == FROZEN_REV, (
        f"REFUSING: audit ran against {data.get('rev')!r}, not the frozen "
        f"{FROZEN_REV!r}. A wrong revision produces a wrong finish line."
    )

    tot_absent = data["absent"]
    tot_sp_files = data["short_port_files"]
    tot_sp_syms = data["short_port_symbols"]
    tot_bh_files = data["behind_head_files"]
    tot_bh_syms = data["behind_head_symbols"]
    total_missing = tot_sp_syms + tot_bh_syms

    s = split(data)

    # Production missing = absent production files + short/behind symbols in
    # production files. Tests never block retiring hermes.
    prod_missing = (s["absent_prod"]
                    + s["sp_symbols_prod"]
                    + s["bh_symbols_prod"])   # split by path -- NOT the combined total
    test_missing = (s["absent_test"]
                    + s["sp_symbols_test"]
                    + s["bh_symbols_test"])

    print("=" * 68)
    print(f"FROZEN TARGET PROGRESS  --  rev {FROZEN_REV}")
    print("=" * 68)
    print()
    print(f"  {'metric':<34}{'now':>7}{'base':>7}{'delta':>8}")
    print(f"  {'-'*56}")
    rows = [
        ("absent files",              tot_absent,    BASE["absent"]),
        ("short_port files",          tot_sp_files,  BASE["short_port_files"]),
        ("short_port symbols",        tot_sp_syms,   BASE["short_port_symbols"]),
        ("behind_head files",         tot_bh_files,  BASE["behind_head_files"]),
        ("behind_head symbols",       tot_bh_syms,   BASE["behind_head_symbols"]),
        ("TOTAL missing symbols",     total_missing, BASE["total_missing"]),
    ]
    for name, now, base in rows:
        delta = now - base
        sign = f"{delta:+d}" if delta else "0"
        print(f"  {name:<34}{now:>7}{base:>7}{sign:>8}")
    print()

    print("  split: production vs tests (tests do NOT block retiring hermes)")
    print(f"    absent:      prod {s['absent_prod']:>4}   tests {s['absent_test']:>4}")
    print(f"    short_port:  prod {s['sp_symbols_prod']:>4} sym / {s['sp_files_prod']} files"
          f"   tests {s['sp_symbols_test']:>4} sym / {s['sp_files_test']} files")
    print(f"    behind_head: prod {s['bh_symbols_prod']:>4} sym / {s['bh_files_prod']} files"
          f"   tests {s['bh_symbols_test']:>4} sym / {s['bh_files_test']} files")
    print()

    drift = total_missing - BASE["total_missing"]
    closed = -drift
    if drift <= 0:
        print(f"  DELTA vs freeze: {closed:+d} symbols closed "
              f"({total_missing} missing now, {BASE['total_missing']} at freeze)")
    else:
        print(f"  DELTA vs freeze: {drift:+d} symbols (gap WIDENED -- pin may have "
              f"moved, or a port regressed)")
    print()

    # Sanity: against a FIXED revision the audit must reproduce its own baseline
    # for anything the ports did NOT touch. A negative "closed" beyond the work
    # done means the instrument or the pin is wrong, not that work happened.
    if tot_absent > BASE["absent"] and drift > 0:
        print("  ! absent files exceed the freeze baseline against a fixed revision")
        print("  ! that is impossible from porting alone -> instrument or pin is wrong")
        print()

    print("-" * 68)
    if prod_missing > 0:
        print(f"VERDICT: NOT AT TARGET -- {prod_missing} production missing "
              f"({test_missing} test missing, non-blocking)")
    else:
        print("VERDICT: AT TARGET")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "rev": FROZEN_REV,
            "baseline": BASE,
            "now": {
                "absent": tot_absent,
                "short_port_files": tot_sp_files,
                "short_port_symbols": tot_sp_syms,
                "behind_head_files": tot_bh_files,
                "behind_head_symbols": tot_bh_syms,
                "total_missing": total_missing,
            },
            "split": s,
            "production_missing": prod_missing,
            "test_missing": test_missing,
            "delta_total": drift,
            "verdict": "AT_TARGET" if prod_missing == 0 else "NOT_AT_TARGET",
        }, indent=2))
        print(f"\nJSON written: {args.json}")

    return 0 if prod_missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
