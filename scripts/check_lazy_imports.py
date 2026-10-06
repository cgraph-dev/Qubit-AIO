#!/usr/bin/env python3
"""Unresolved lazy-import gate: catch in-function imports of names that don't exist.

Why this exists
---------------
`scripts/sync_import_gate.py` imports each module in a clean subprocess, so it
only ever sees TOP-LEVEL import errors. A symbol imported inside a function body
(`def f(): from qubit_constants import get_hermes_home`) is invisible to it: the
module imports fine and the ImportError only fires when that code path runs —
in front of the operator.

That has now happened three times in the upstream sync. The sync commits say so
themselves (43e3a2b2): *"Three of them are imported LAZILY inside functions, so
the top-level gate never saw them and they would have surfaced as runtime
ImportErrors."*

How it works
------------
A pure static AST scan CANNOT answer this question honestly in this tree. The
vendored code exports names through PEP 562 module-level `__getattr__` fed by
tables (`tools/connectors/__init__.py`, `pm/__init__.py`) and through provider
registries that populate their namespace at import time
(`agent/web_search_registry.py`). A source-only resolver reports ~30 false
positives on correct code — and a gate that cries wolf is worse than no gate,
because people learn to ignore it.

So the two halves are split by what each can actually decide:

  1. AST (source) decides *which* (module, symbol) pairs are imported from a
     function body — nesting depth is a source property, exactly AST's job.
  2. The REAL interpreter decides whether each symbol exists — that is runtime
     state (PEP 562 hooks, registry population), so the check runs one
     subprocess that imports each distinct target module once and answers
     `hasattr`.

Only pairs where the target module imports cleanly AND lacks the attribute are
reported. A target module that fails to import at all is reported separately
(that is the top-level gate's job, but surfacing it keeps one run self-contained
and the failure legible).

Scope: the vendored runtime (`qubit_runtime/`) only, excluding third-party and
generated trees (`node_modules/`, `optional-skills/`, `website/`, `tests/`).

Usage:
    python3 scripts/check_lazy_imports.py           # report, exit 1 on any miss
    python3 scripts/check_lazy_imports.py --json    # machine-readable
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "qubit_runtime"

# Vendored third-party / generated trees: not our import graph to police.
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "optional-skills", "website",
    "tests", ".venv", "venv", "dist", "build", "site-packages", "vendor",
}


def _pick_interpreter(repo: Path) -> str:
    """Use the project venv, the environment the app actually runs in."""
    for cand in (repo / ".venv/bin/python", repo / ".venv/bin/python3"):
        if cand.exists():
            return str(cand)
    return sys.executable


def _iter_runtime_files(runtime: Path):
    for path in runtime.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _module_name(path: Path, runtime: Path) -> str:
    parts = list(path.relative_to(runtime).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def collect_lazy_imports(runtime: Path) -> list[dict]:
    """Every `from <mod> import <name>` that sits INSIDE a function body.

    Module-level and class-level imports are skipped: they already surface in
    the top-level gate, so re-reporting them here would double-count.
    """
    found: list[dict] = []
    for path in _iter_runtime_files(runtime):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, ValueError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.ImportFrom):
                    continue
                if inner.level or not inner.module:
                    continue
                for alias in inner.names:
                    if alias.name == "*":
                        continue
                    found.append({
                        "file": str(path.relative_to(REPO)),
                        "line": inner.lineno,
                        "module": inner.module,
                        "symbol": alias.name,
                    })
    return found


def resolve_symbols(pairs: list[dict], repo: Path, home: str) -> dict:
    """Ask the real interpreter which (module, symbol) pairs resolve.

    One subprocess, one import per distinct module. Returns
    ``{"missing": [[mod, name], ...], "errors": {mod: detail}}``.
    """
    by_module: dict[str, set[str]] = {}
    for item in pairs:
        by_module.setdefault(item["module"], set()).add(item["symbol"])

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{repo / 'qubit_runtime'}:{repo}"
    env["QUBIT_HOME"] = home
    # Importing the tree can create the home and load dotenv; keep it isolated.
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    program = (
        "import importlib, json, sys\n"
        "_out = sys.stdout\n"
        "pairs = json.loads(sys.stdin.read())\n"
        "missing = []\n"
        "errors = {}\n"
        "for mod, names in pairs.items():\n"
        "    try:\n"
        "        m = importlib.import_module(mod)\n"
        "    except Exception as e:\n"
        "        errors[mod] = type(e).__name__ + ': ' + str(e)[:160]\n"
        "        continue\n"
        "    for n in names:\n"
        "        if hasattr(m, n):\n"
        "            continue\n"
        "        # `from pkg import child` may name a SUBMODULE, which hasattr\n"
        "        # misses until it is imported. Import it explicitly; only a\n"
        "        # genuine miss stays in the report.\n"
        "        try:\n"
        "            importlib.import_module(mod + '.' + n)\n"
        "            continue\n"
        "        except Exception:\n"
        "            missing.append([mod, n])\n"
        "_out.write(json.dumps({'missing': missing, 'errors': errors}) + '\\n')\n"
        "_out.flush()\n"
    )
    proc = subprocess.run(
        [_pick_interpreter(repo), "-c", program],
        input=json.dumps({k: sorted(v) for k, v in by_module.items()}),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo),
    )
    # A probed module may reassign sys.stdout at import time (tui_gateway.server
    # reserves real stdout for its JSON-RPC pipe); the payload is written to a
    # snapshotted stream, so prefer stdout and fall back to stderr.
    for stream in (proc.stdout, proc.stderr):
        for line in reversed((stream or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return {"missing": None,
            "errors": {"<gate>": "subprocess produced no payload: "
                                 f"{(proc.stderr or '')[-200:]}"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--runtime", default=str(RUNTIME))
    ap.add_argument("--home", default=os.environ.get(
        "QUBIT_HOME",
        str(Path.home() / ".local/state/qubit-voicebridge/runtime")))
    args = ap.parse_args()

    runtime = Path(args.runtime)
    if not runtime.is_dir():
        print(f"FAIL: runtime tree not found: {runtime}", file=sys.stderr)
        return 2

    pairs = collect_lazy_imports(runtime)
    if not pairs:
        print("OK: no in-function imports found (nothing to resolve).")
        return 0

    # Only pairs whose target module belongs to OUR tree are our business;
    # stdlib / site-packages targets are out of scope.
    ours = {_module_name(p, runtime) for p in _iter_runtime_files(runtime)}
    pairs = [p for p in pairs if p["module"] in ours]

    result = resolve_symbols(pairs, REPO, args.home)
    missing_pairs = result.get("missing")
    import_errors = result.get("errors") or {}

    if missing_pairs is None:
        print("FAIL: could not resolve symbols against the interpreter.")
        for mod, detail in import_errors.items():
            print(f"  {mod}: {detail}")
        return 2

    missing_set = {(m, n) for m, n in missing_pairs}
    unresolved = [p for p in pairs if (p["module"], p["symbol"]) in missing_set]
    # Deduplicate (the same statement can list a name once, but a module may be
    # imported on many lines — every site is worth reporting).
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in unresolved:
        key = (item["file"], item["line"], item["module"], item["symbol"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    # Classify severity. A miss is only a CRASH if the import is unguarded: a
    # bare `from X import Y` inside a function body raises ImportError the moment
    # the function runs. If the statement sits inside try/except (or a swallow
    # helper), the failure is caught by design and the feature degrades instead.
    # Both matter, but reporting them as one number overstates the damage and
    # trains readers to ignore the check. Verified instance: process_identity.py
    # guards `from qubit_constants import PROJECT_ROOT` with a working
    # Path(__file__) fallback, and conversation_compression.py wraps its
    # rotate_segment import in _swallow(...) for a documented no-op.
    def _classify(item: dict) -> str:
        try:
            lines = Path(item["file"]).read_text(errors="replace").split("\n")
        except OSError:
            return "bare"
        # Walk backwards from the import to the start of its enclosing block,
        # looking for a guarding `try:` at a lower indent.
        idx = item["line"] - 1
        if idx < 0 or idx >= len(lines):
            return "bare"
        target_indent = len(lines[idx]) - len(lines[idx].lstrip())
        for j in range(idx - 1, max(-1, idx - 40), -1):
            stripped = lines[j].strip()
            if not stripped:
                continue
            indent = len(lines[j]) - len(lines[j].lstrip())
            if indent < target_indent and stripped.startswith("try:"):
                return "guarded"
            # A `with <swallow-helper>(...)` block guards too: the helper's body
            # is a try/except Exception that logs and continues. Treat any
            # `with ...swallow...` as guarding, since that is the documented
            # idiom for best-effort work in this tree.
            if indent < target_indent and stripped.startswith("with ") and "swallow" in stripped:
                return "guarded"
            if indent == 0 and stripped and not stripped.startswith(("#", ")")):
                break
        return "bare"

    for item in unique:
        item["severity"] = _classify(item)
    crashing = [i for i in unique if i["severity"] == "bare"]
    degrading = [i for i in unique if i["severity"] == "guarded"]

    if args.json:
        print(json.dumps({"unresolved": unique, "import_errors": import_errors,
                          "crashing": len(crashing),
                          "degrading": len(degrading)}, indent=2))
    else:
        if import_errors:
            print(f"{len(import_errors)} target module(s) failed to import "
                  f"(top-level breakage):")
            for mod, detail in sorted(import_errors.items()):
                print(f"  {mod}: {detail}")
            print()
        if crashing:
            print(f"FAIL: {len(crashing)} UNGUARDED lazy import(s) name a symbol "
                  f"that does not exist. These raise ImportError at RUNTIME:\n")
            for item in crashing:
                print(f"  {item['file']}:{item['line']}  "
                      f"from {item['module']} import {item['symbol']}")
            print("\nThe import lives inside a function body, so the top-level "
                  "import gate cannot\nsee it. Fix by adding the name to the "
                  "defining module (the established\nupstream-sync pattern is a "
                  "1:1 re-bind at end of file).\n")
        if degrading:
            print(f"DEGRADED: {len(degrading)} GUARDED lazy import(s) name a "
                  f"missing symbol. No crash — the\nfeature silently does "
                  f"nothing (worse for diagnosis than a crash):\n")
            for item in degrading:
                print(f"  {item['file']}:{item['line']}  "
                      f"from {item['module']} import {item['symbol']}")
            print()
        if not unique and not import_errors:
            print(f"OK: all {len(pairs)} lazy import(s) resolve "
                  f"({len({p['module'] for p in pairs})} distinct modules).")

    # Only UNGUARDED misses are blocking. Guarded misses are reported but do not
    # fail the build: they are real debt, not a broken tree.
    return 1 if (crashing or import_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
