#!/usr/bin/env python3
"""Cluster test failures by ROOT CAUSE instead of listing them.

Why: the full `tests/agent` slice reported 285 failed / 23 errors. That number
alone says nothing about whether it is ONE bug or thirty. Reading a 104-minute
log by eye is unreliable; this buckets failures by a normalized error
signature so the distribution is visible.

Usage:
    python3 scripts/test_failure_clusters.py <pytest-target> [more targets...]

Writes a summary to stdout and a full JSON map to
/tmp/test_failure_clusters.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _interp() -> str:
    p = REPO / ".venv/bin/python"
    return str(p) if p.exists() else sys.executable


# Normalize an error line so messages differing only by a value group together.
_NORMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"0x[0-9a-fA-F]+"), "<addr>"),
    (re.compile(r"\b\d{4,}\b"), "<n>"),
    (re.compile(r"'[^']{60,}'"), "<longstr>"),
    (re.compile(r"/tmp/[^\s:]+"), "<tmp>"),
    (re.compile(r"\d+\.\d+"), "<float>"),
]


def normalize(text: str) -> str:
    for rx, rep in _NORMS:
        text = rx.sub(rep, text)
    return text.strip()[:240]


def collect(targets: list[str]) -> dict:
    """Run pytest once, parse its short tracebacks into (test, error) pairs."""
    cmd = [_interp(), "-m", "pytest", *targets, "-q", "--no-header",
           "--tb=short", "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO),
                          env={**dict(__import__("os").environ), "PYTHONPATH": f"qubit_runtime:{REPO}"})
    return {"stdout": proc.stdout, "stderr": proc.stderr, "rc": proc.returncode}


# Pytest's short format marks the error line with 'E   '.
_ERR = re.compile(r"^E\s+(.*)$")


def clusters_from(stdout: str) -> tuple[dict[str, list[str]], Counter]:
    """Group failing tests by normalized error signature.

    Heuristic: within each '____ test ____' section, take the LAST E-line --
    that is the assertion/exception that actually failed, not a context line.
    """
    sections = re.split(r"_{5,}\s+(.+?)\s+_{5,}", stdout)
    by_sig: dict[str, list[str]] = defaultdict(list)
    counter: Counter = Counter()

    # sections[0] is preamble; then alternating (title, body)
    for i in range(1, len(sections) - 1, 2):
        title, body = sections[i], sections[i + 1]
        errs = [normalize(m.group(1)) for m in _ERR.finditer(body)]
        sig = errs[-1] if errs else "NO ERROR LINE CAPTURED"
        by_sig[sig].append(title.strip())
        counter[sig] += 1
    return by_sig, counter


def main() -> int:
    targets = sys.argv[1:] or ["qubit_runtime/tests/agent"]
    run = collect(targets)
    out = (run["stdout"] or "") + "\n" + (run["stderr"] or "")

    # summary line
    m = re.search(r"(\d+) failed.*?(\d+) passed", out)
    if m:
        print(f"pytest reported: {m.group(0)}")
    else:
        print("pytest summary:", [l for l in out.splitlines() if "passed" in l or "error" in l][-2:])

    by_sig, counter = clusters_from(out)
    if not by_sig:
        print("no parseable failure sections (all passed, or a collection error at module level)")
        print(out[-2000:])
        return 0

    print(f"\nDISTINCT ROOT CAUSES: {len(by_sig)}\n")
    for sig, tests in counter.most_common():
        print(f"[{tests:4d} tests] {sig}")
        for t in by_sig[sig][:3]:
            print(f"          e.g. {t}")
        print()

    Path("/tmp/test_failure_clusters.json").write_text(json.dumps(
        {"causes": {s: t for s, t in by_sig.items()}, "counts": dict(counter)}, indent=2))
    print("full map: /tmp/test_failure_clusters.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
