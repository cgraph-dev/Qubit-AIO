#!/usr/bin/env python3
"""Import gate comparing failure IDENTITY, not just the FAIL count.

Why this exists: a count-only gate silently missed a real regression. A benign
pre-existing failure was REPLACED by a new one, so the FAIL count stayed at 2
while the module that broke changed. Comparing the SET of failing modules
catches that.

Usage:
    # capture a baseline before applying a sync
    python3 scripts/sync_import_gate.py --modules-file mods.txt --save /tmp/gate_before.json

    # after applying
    python3 scripts/sync_import_gate.py --modules-file mods.txt --compare /tmp/gate_before.json

Exit codes: 0 = no regression, 1 = regression (new failing module), 2 = usage error.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_MODULES = [
    "qubit_cli.config",
    "qubit_cli.gateway",
    "qubit_cli.web_server",
    "qubit_cli.status",
    "qubit_cli.status_auth",
    "qubit_cli.sessions_cmd",
    "qubit_cli.sessions_cmd_browse",
    "qubit_cli.plugins_cmd",
    "qubit_cli.runtime_provider",
    "qubit_cli.setup",
    "qubit_cli.setup_terminal",
    "qubit_cli.backup",
    "qubit_cli.backup_restore",
    "agent.skill_commands",
    "agent.display",
    "agent.provider_media",
    "agent.runtime_self_protection",
    "agent.turn_naming",
    "gateway.run",
    "gateway.status",
    "gateway.memory_status",
    "gateway.platforms.telegram",
    "tools.registry",
    "tools.terminal_tool",
    "tools.file_tools",
    "tools.browser_tool",
    "tui_gateway.server",
    "tui_gateway.session_lifecycle",
    "tui_gateway.ws",
    "cron.jobs",
    "cron.scheduler",
]

REPO = Path(__file__).resolve().parent.parent


def _pick_interpreter(repo: Path) -> str:
    """Use the project venv when present.

    Running the gate with the system python produced ~23 bogus failures
    (ModuleNotFoundError: ruamel) that had nothing to do with the sync --
    the harness was measuring its own missing dependencies. The venv is the
    environment the app actually runs in, so that is what the gate must probe.
    """
    for cand in (repo / ".venv/bin/python", repo / ".venv/bin/python3"):
        if cand.exists():
            return str(cand)
    return sys.executable


def probe(modules: list[str], repo: Path, home: str) -> dict[str, str]:
    """Import each module in a clean subprocess; return {module: 'OK'|'FAIL <detail>'}."""
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{repo / 'qubit_runtime'}:{repo}"
    env["QUBIT_HOME"] = home

    # A probed module may reassign ``sys.stdout`` at IMPORT time (tui_gateway.server
    # reserves real stdout for its JSON-RPC pipe and points sys.stdout at stderr). A
    # bare ``print`` would then land on stderr and the harness would see an empty
    # stdout, reporting bogus "gate-crashed" for every module. Snapshot the real
    # stream before the loop and write the payload to it explicitly.
    program = "import importlib, json, sys\n"
    program += "_out = sys.stdout\n"
    program += "results = {}\n"
    program += "mods = json.loads(sys.argv[1])\n"
    program += (
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "        results[m] = 'OK'\n"
        "    except Exception as e:\n"
        "        results[m] = 'FAIL ' + type(e).__name__ + ': ' + str(e)[:120]\n"
        "_out.write(json.dumps(results) + '\\n')\n"
        "_out.flush()\n"
    )
    proc = subprocess.run(
        [_pick_interpreter(repo), "-c", program, json.dumps(modules)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo),
    )
    # Prefer the dedicated stdout payload; fall back to stderr in case a module
    # closed or replaced the stream we captured.
    for stream in (proc.stdout, proc.stderr):
        for line in reversed((stream or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return {m: f"FAIL gate-crashed: {(proc.stderr or '')[-160:]}" for m in modules}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules-file", help="newline-separated module list")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--home", default=os.environ.get("QUBIT_HOME", str(Path.home() / ".local/state/qubit-voicebridge/runtime")))
    ap.add_argument("--save", help="write the baseline JSON here")
    ap.add_argument("--compare", help="compare against a saved baseline JSON")
    args = ap.parse_args()

    modules = DEFAULT_MODULES
    if args.modules_file:
        modules = [ln.strip() for ln in Path(args.modules_file).read_text().splitlines() if ln.strip()]

    results = probe(modules, Path(args.repo), args.home)
    ok = sum(1 for v in results.values() if v == "OK")
    fail = len(results) - ok

    if args.save:
        Path(args.save).write_text(json.dumps(results, indent=2))
        print(f"baseline saved: {args.save}  ({ok} OK / {fail} FAIL)")
        return 0

    print(f"gate: {ok} OK / {fail} FAIL (of {len(results)} modules)")

    if args.compare:
        before = json.loads(Path(args.compare).read_text())
        before_fail = {m for m, v in before.items() if v != "OK"}
        after_fail = {m for m, v in results.items() if v != "OK"}

        newly_broken = sorted(after_fail - before_fail)
        newly_fixed = sorted(before_fail - after_fail)

        if newly_fixed:
            print(f"  FIXED ({len(newly_fixed)}): {', '.join(newly_fixed)}")
        if newly_broken:
            print(f"  REGRESSED ({len(newly_broken)}) -- sync must be reverted:")
            for m in newly_broken:
                print(f"    {m}: {results[m]}")
            return 1
        print("  no regression (failing-module set unchanged)")
        return 0

    for m in sorted(results):
        if results[m] != "OK":
            print(f"  FAIL {m}: {results[m]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
