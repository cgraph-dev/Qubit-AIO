#!/usr/bin/env python3
"""Combined acceptance gate for the upstream sync.

WHY THIS EXISTS
---------------
Four separate checks each caught real breakage the others could not see, and each
was run alone, at different times, by different people. That produced a specific
and expensive failure: ``check_entry_point_resolution.py`` reported HARD=0 and it
was read as "done", while ``check_lazy_imports.py`` was at that moment reporting
two genuine RuntimeError-grade breaks (``qubit_constants.PROJECT_ROOT`` and
``relay_shared_metrics.rotate_segment``) that the entry-point sweep cannot see,
because their imports live inside function bodies.

A single number from a single tool is a measurement, not a verdict. This script
runs all four and prints ONE verdict line, so no caller can mistake a partial
green for completion.

THE FOUR CHECKS (each catches a class the others miss)
-----------------------------------------------------
  1. import gate          -- do the ~29 curated modules import at all?
                             Catches top-level breakage in the hot path.
  2. entry-point sweep    -- does every ``from X import Y`` / ``X.Y`` in the
                             whole tree resolve? Catches dropped symbols
                             tree-wide, statically, without importing.
  3. lazy-import check    -- do function-local imports resolve? Catches the
                             class the static sweep structurally cannot: an
                             import inside a function body only fails at call
                             time, so it never shows in an import gate.
  4. capability audit     -- how many upstream symbols are still missing, by
                             subsystem? Not pass/fail -- a BURNDOWN number, so
                             progress toward parity is visible and cannot be
                             confused with correctness.

BLOCKING vs INFORMATIONAL
-------------------------
  1, 2, 3 are BLOCKING: a failure means the tree is broken, not incomplete.
  4 is INFORMATIONAL: a gap count is expected during a sync and never blocks.

Usage:
    python3 scripts/gate_all.py            # all four, one verdict
    python3 scripts/gate_all.py --fast     # skip the capability audit (slow)
    python3 scripts/gate_all.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    """Run a check, return (exit_code, combined output). Never raises."""
    try:
        p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except Exception as e:                                   # noqa: BLE001
        return 125, f"{type(e).__name__}: {e}"


def _find(pattern: str, text: str = "", default=None):
    """First capture group of `pattern` in `text`, else `default`. Never raises."""
    if not text:
        text = ""
    m = re.search(pattern, text, re.MULTILINE)
    return (m.group(1) if m else default)


def check_import_gate() -> dict:
    code, out = _run([PY, "scripts/sync_import_gate.py"], timeout=600)
    line = _find(r"^gate: (.+)$", out, "")
    ok = m = fail = None
    mm = re.search(r"(\d+) OK / (\d+) FAIL", line)
    if mm:
        ok, fail = int(mm.group(1)), int(mm.group(2))
    return {"name": "import gate", "blocking": True, "exit": code,
            "ok": ok, "fail": fail, "detail": line.strip() or out.strip()[-300:]}


def check_entry_point() -> dict:
    code, out = _run([PY, "scripts/check_entry_point_resolution.py"], timeout=900)
    prod = _find(r"^# HARD \(production\): (\d+)", out)
    tests = _find(r"^# HARD \(tests\): (\d+)", out)
    tree = _find(r"^# measured tree: (.+)$", out, "")
    return {"name": "entry-point resolution", "blocking": True, "exit": code,
            "hard_prod": int(prod) if prod is not None else None,
            "hard_tests": int(tests) if tests is not None else None,
            "tree": tree.strip(),
            "detail": f"HARD prod={prod} tests={tests}"}


def check_lazy_imports() -> dict:
    code, out = _run([PY, "scripts/check_lazy_imports.py"], timeout=600)
    failing = _find(r"^FAIL: (\d+) UNGUARDED", out)
    degraded = _find(r"^DEGRADED: (\d+) GUARDED", out)
    # Exit code 0 means nothing CRASHES; the DEGRADED count is surfaced anyway,
    # because a guarded miss means a feature silently does nothing -- real debt
    # that must stay visible without being dressed up as a broken tree.
    return {"name": "lazy imports", "blocking": True, "exit": code,
            "crashing": int(failing) if failing is not None else (0 if code == 0 else None),
            "degrading": int(degraded) if degraded is not None else 0,
            "detail": (f"{failing} unguarded (crash)" if failing
                       else f"0 unguarded; {degraded or 0} guarded (silent no-op)"
                       if degraded else "clean")}


def check_capability() -> dict:
    code, out = _run([PY, "scripts/audit_capability_gaps.py"], timeout=900)
    missing = _find(r"MISSING in Qubit: (\d+)", out)
    files = _find(r"files with gaps: (\d+)", out)
    subsys = re.findall(r"^\s+(\d+)\s+(\S+)\s*$", out, re.MULTILINE)
    return {"name": "capability audit", "blocking": False, "exit": code,
            "missing_symbols": int(missing) if missing is not None else None,
            "files_with_gaps": int(files) if files is not None else None,
            "by_subsystem": {k: int(v) for v, k in subsys},
            "detail": f"{missing} missing symbols across {files} files"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true",
                    help="skip the capability audit (the slow burndown check)")
    ap.add_argument("--json", help="write results here")
    args = ap.parse_args()

    checks = [check_import_gate, check_entry_point, check_lazy_imports]
    if not args.fast:
        checks.append(check_capability)

    results = [fn() for fn in checks]

    print("# Combined sync gate")
    print()
    for r in results:
        mark = "PASS" if (r["exit"] == 0) else ("INFO" if not r["blocking"] else "FAIL")
        tag = "" if r["blocking"] else "  (informational -- never blocks)"
        print(f"  [{mark}] {r['name']:26} {r['detail']}{tag}")
    print()

    blocking_failures = [r for r in results if r["blocking"] and r["exit"] != 0]
    if blocking_failures:
        print(f"VERDICT: NOT READY -- {len(blocking_failures)} blocking check(s) failed")
        for r in blocking_failures:
            print(f"  - {r['name']}: {r['detail']}")
    else:
        print("VERDICT: PASS -- all blocking checks green")
        cap = next((r for r in results if r["name"] == "capability audit"), None)
        if cap and cap.get("missing_symbols"):
            print(f"  NOTE: {cap['missing_symbols']} upstream symbols still missing "
                  f"({cap.get('files_with_gaps')} files). That is a BURNDOWN number, "
                  f"not a defect list -- the tree is not broken, it is incomplete.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"results": results,
             "blocking_failures": [r["name"] for r in blocking_failures],
             "verdict": "NOT_READY" if blocking_failures else "PASS"}, indent=2))
        print(f"\nJSON written: {args.json}")

    return 1 if blocking_failures else 0


if __name__ == "__main__":
    sys.exit(main())
