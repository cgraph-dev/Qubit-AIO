#!/usr/bin/env python3
"""Safe import walk: import every module, skipping side-effecting ones.

WHY THIS EXISTS
---------------
A previous "import every module" runtime probe imported
``qubit_cli/update_cmd_fleet.py`` and thereby **ran the updater** -- its stderr
shows the package-manager handoff and a dependency install beginning. The scan's
output was contaminated (tool-call JSON mixed into results) and could not be read
as a clean pass/fail. It was contained only because the probe had set an isolated
QUBIT_HOME.

An import walk IS code execution. This script treats it that way:

  1. Each import runs in a SUBPROCESS with a hard timeout, so a module that hangs
     or spawns work cannot stall or mutate the parent.
  2. ``--home`` (required) points every child at a scratch QUBIT_HOME, so any
     module that writes state writes it there.
  3. Modules with module-level side effects are SKIPPED by default. Detection
     looks for (a) a ``__main__`` guard plus module-level calls, (b) known
     side-effect markers (install/update/launch/spawn at module scope), and
     (c) an explicit deny list. Skipped modules are reported, never silently
     dropped -- a skip is a finding, not a pass.
  4. Failures are grouped by exception type, so a single systemic cause is
     visible instead of buried in hundreds of lines.

Usage:
    python3 scripts/safe_import_walk.py --home /tmp/scratch-home \
        [--root qubit_runtime] [--jobs 8] [--json out.json] \
        [--include-side-effecting] [--timeout 30]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Modules whose import is known to DO something. Keep this list explicit and
# reason-bearing: a name here is a decision, not a guess.
DENY_NAMES = {
    "update_cmd_fleet.py",      # ran the updater: package-manager handoff + dep install
    "update_cmd.py",
    "installer.py",
    "setup.py",
    "__main__.py",
}

# Markers that, at MODULE level, mean "importing this will act".
#
# These are matched as CALLS, not as bare substrings. A first attempt matched
# plain substrings and flagged ordinary definitions -- `def _install_source()`,
# `_start_x = ...` -- which would have skipped most of the tree and made the walk
# useless. A marker only counts when a call is actually being made at module scope
# (or a name is assigned the result of one), i.e. the text appears with an opening
# paren and is not preceded by `def`/`class`/`async def`.
SIDE_EFFECT_CALLS = (
    "fire.Fire(",
    "subprocess.run(",
    "subprocess.Popen(",
    "os.exec",
    "sys.exit(",
    "register_cli(",
)

# Bare-identifier prefixes that are dangerous ONLY when they are the called name
# at statement start, e.g. ``install_locales(...)``. Checked with a word-boundary
# regex against the call, never as a substring of an identifier.
SIDE_EFFECT_FUNCS = (
    "install_",
    "launch_",
    "spawn_",
    "start_",
)


def _is_def_or_class(stripped: str) -> bool:
    return (stripped.startswith("def ") or stripped.startswith("class ")
            or stripped.startswith("async def "))


def has_side_effects(path: Path) -> tuple[bool, str]:
    """True when importing this module would execute something. Returns a reason.

    Conservative in the right direction: a missed side effect is worse than an
    extra skip, but an over-eager skip makes the whole walk worthless, so the
    check fires only on real module-scope CALLS.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        return True, f"unreadable: {e}"
    if path.name in DENY_NAMES:
        return True, f"deny-list ({path.name})"
    for lineno, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            continue  # module scope only
        if _is_def_or_class(stripped):
            continue
        for marker in SIDE_EFFECT_CALLS:
            if marker in stripped:
                return True, f"module-level call {marker!r} at line {lineno}"
        # `name(` where name starts with a dangerous prefix, and it is a call not
        # a definition -- anchored so `_start_time = 0` never matches.
        m = re.match(r"^(_?)([A-Za-z_][\w]*)\s*\(", stripped)
        if m and m.group(2).startswith(SIDE_EFFECT_FUNCS):
            return True, f"module-level call {m.group(2)}(...) at line {lineno}"
        # bare `main()` / `sys.exit` style module-scope invocations
        if re.match(r"^main\s*\(\s*\)", stripped):
            return True, f"module-level main() at line {lineno}"
    return False, ""


def to_module(root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


_PROBE = (
    "import importlib, sys, traceback\n"
    "mod = sys.argv[1]\n"
    "try:\n"
    "    importlib.import_module(mod)\n"
    "    print('OK')\n"
    "except BaseException as e:\n"
    "    print('FAIL ' + type(e).__name__ + ': ' + str(e)[:300])\n"
)


def probe(mod: str, py: str, root: str, timeout: int, home: str) -> tuple[str, str]:
    env = dict(os.environ)
    env["QUBIT_HOME"] = home
    env["HERMES_RUNTIME_DIR"] = os.path.join(home, "runtime")
    env["PYTHONPATH"] = os.pathsep.join([root, str(REPO)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        p = subprocess.run([py, "-c", _PROBE, mod], cwd=root, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return mod, f"TIMEOUT after {timeout}s"
    out = (p.stdout or "").strip().split("\n")[-1] if p.stdout else ""
    if out.startswith("OK"):
        return mod, "OK"
    if out.startswith("FAIL"):
        return mod, out[5:].strip()
    err = (p.stderr or "").strip().split("\n")[-1] if p.stderr else ""
    return mod, f"NO-RESULT rc={p.returncode} {err[:200]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="qubit_runtime")
    ap.add_argument("--home", required=True,
                    help="scratch QUBIT_HOME for every child (required)")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--json")
    ap.add_argument("--include-side-effecting", action="store_true")
    args = ap.parse_args()

    root = (REPO / args.root).resolve()
    if not root.is_dir():
        print(f"FAIL: root not found: {root}", file=sys.stderr)
        return 2
    home = Path(args.home)
    home.mkdir(parents=True, exist_ok=True)
    py = sys.executable

    targets: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        mod = to_module(root, path)
        if not mod:
            continue
        risky, why = has_side_effects(path)
        if risky and not args.include_side_effecting:
            skipped.append((mod, why))
            continue
        targets.append((mod, str(path)))

    print(f"# safe import walk -- root={args.root}")
    print(f"# candidates: {len(targets)}   skipped (side-effecting): {len(skipped)}")
    print(f"# scratch home: {home}   timeout: {args.timeout}s   jobs: {args.jobs}")
    print()

    results: list[tuple[str, str]] = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(probe, m, py, str(root), args.timeout, str(home))
                for m, _ in targets]
        for f in as_completed(futs):
            results.append(f.result())
    elapsed = time.time() - t0

    ok = [m for m, r in results if r == "OK"]
    bad = [(m, r) for m, r in results if r != "OK"]

    by_exc: dict[str, list[str]] = defaultdict(list)
    for m, r in bad:
        kind = r.split(":")[0].split(" ")[0]
        by_exc[kind].append(m)

    print(f"# imported OK: {len(ok)}   FAILED: {len(bad)}   ({elapsed:.1f}s)")
    if by_exc:
        print("\n## failures by exception type")
        for kind, mods in sorted(by_exc.items(), key=lambda x: -len(x[1])):
            print(f"\n  {len(mods):4}  {kind}")
            for m in sorted(mods)[:8]:
                print(f"          {m}")
            if len(mods) > 8:
                print(f"          ... {len(mods) - 8} more")
    if skipped:
        print(f"\n## skipped as side-effecting ({len(skipped)}) -- these are NOT passes")
        for m, why in skipped[:20]:
            print(f"      {m}\n          {why}")
        if len(skipped) > 20:
            print(f"      ... {len(skipped) - 20} more")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "root": args.root, "ok": len(ok), "failed": len(bad),
            "skipped": len(skipped), "elapsed_s": round(elapsed, 1),
            "failures": {k: sorted(v) for k, v in by_exc.items()},
            "skipped_detail": [{"module": m, "reason": w} for m, w in skipped],
        }, indent=2))
        print(f"\nJSON written: {args.json}")

    # A failed import is a finding. A side-effecting skip is a finding too, but it
    # is not a crash -- so it does not fail the run, it is reported as unknown.
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
