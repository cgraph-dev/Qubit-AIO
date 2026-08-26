#!/usr/bin/env python3
"""Packet 7A: separated test/monitor commands.

Category commands keep live and monitoring tests OPT-IN and out of the normal
hermetic path:

  python scripts/run_tests.py unit            hermetic unit/contract (no live)
  python scripts/run_tests.py contract        pinned packet contract tests
  python scripts/run_tests.py integration     HTTP/webui integration tests
  python scripts/run_tests.py live-model      opt-in real-model tests
  python scripts/run_tests.py live-dashboard  opt-in real-bridge dashboard probes
  python scripts/run_tests.py monitor         opt-in monitor/clean-venv checks
  python scripts/run_tests.py all             full suite (includes live)
  python scripts/run_tests.py health          run config doctor --runtime
  python scripts/run_tests.py reconcile       run config doctor --reconcile

Live/monitor commands that result in skipped checks are reported as DEGRADED,
never as a pass. The operator watchdog is a separate process:
  python scripts/dashboard_stress.py --watch 120 --allow-restart   # operator-only
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_LIVE_FILES = ["tests/test_live_stress.py", "tests/test_dashboard_stress.py"]
_LIVE_K = "not AirLLMLive"

CATEGORIES = {
    "unit": ["-q", "-p", "no:cacheprovider",
             *[f"--ignore={f}" for f in _LIVE_FILES],
             "-k", _LIVE_K],
    "contract": ["-q", "-p", "no:cacheprovider",
                 "tests/test_packet0b.py", "tests/test_packet1a.py",
                 "tests/test_packet1b.py", "tests/test_packet2a.py",
                 "tests/test_packet2b.py", "tests/test_packet3a.py",
                 "tests/test_packet3b.py", "tests/test_packet4a.py",
                 "tests/test_packet4b.py", "tests/test_packet4c.py",
                 "tests/test_packet4d.py", "tests/test_packet5a.py",
                 "tests/test_packet5b.py", "tests/test_packet5c.py",
                 "tests/test_packet5c_integration.py", "tests/test_packet6a.py",
                 "tests/test_packet6b.py", "tests/test_packet7a.py",
                 "-k", _LIVE_K],
    "integration": ["-q", "-p", "no:cacheprovider", "tests/test_webui.py",
                    "tests/test_events_agui_conformance.py"],
    "smoke": ["-q", "-p", "no:cacheprovider", "tests/test_smoke.py",
              "-k", _LIVE_K],
    "live-model": ["-q", "-p", "no:cacheprovider",
                   "tests/test_live_stress.py",
                   "tests/test_steal_list_strict.py", "-k", "AirLLMLive"],
    "live-dashboard": ["-q", "-p", "no:cacheprovider",
                       "tests/test_dashboard_stress.py"],
    "monitor": ["-q", "-p", "no:cacheprovider", "tests/test_packet7a.py",
                "-k", "monitor"],
    "all": ["-q", "-p", "no:cacheprovider"],
}

OPT_IN = {"live-model", "live-dashboard", "monitor"}


def _run(args: list[str]) -> int:
    cmd = [sys.executable, "-m", "pytest", *args]
    print("[run_tests] " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(REPO))


def _count_skips(text: str) -> int:
    """Count 'N skipped' from a pytest summary line (used to label live/monitor
    skips as DEGRADED, never a silent pass)."""
    import re
    total = 0
    for line in text.splitlines():
        if " skipped" in line and "in " in line:
            m = re.search(r"(\d+) skipped", line)
            if m:
                total += int(m.group(1))
    return total


def _degraded_on_skip(args: list[str]) -> int:
    rc = _run(args)
    # Live/monitor skips are DEGRADED, never a silent pass. Parse the summary.
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", *args, "-rN", "--no-summary"],
            cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    except Exception:
        return rc
    skipped = _count_skips((out.stdout or "") + (out.stderr or ""))
    if skipped:
        print(f"[run_tests] DEGRADED: {skipped} live/monitor check(s) skipped "
              f"— opt-in evidence not met (not a pass)", flush=True)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Packet 7A separated test commands")
    ap.add_argument("category",
                    choices=sorted(CATEGORIES) + ["health", "reconcile"])
    ap.add_argument("--extra", nargs="*", default=[],
                    help="extra pytest args appended (e.g. -k, -x)")
    args = ap.parse_args(argv)
    if args.category in ("health", "reconcile"):
        cmd = [sys.executable, str(REPO / "scripts" / "config_doctor.py"),
               "--runtime" if args.category == "health" else "--reconcile"]
        print("[run_tests] " + " ".join(cmd), flush=True)
        return subprocess.call(cmd, cwd=str(REPO))
    base = list(CATEGORIES[args.category]) + list(args.extra)
    # Smoke: a skipped voice check (no piper voice) is DEGRADED, never a pass.
    if args.category in OPT_IN or args.category == "smoke":
        return _degraded_on_skip(base)
    return _run(base)


if __name__ == "__main__":
    sys.exit(main())