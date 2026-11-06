#!/usr/bin/env python3
"""Fast local E2E loop for Qubit's user-facing surface.

This is the iteration lane, not the release gate. It runs the browser
new-chat/resume contract and the dashboard/classic-CLI E2E files that cover
Qubit's primary paths, one pytest process per file, in parallel.

Deliberately excluded from the fast lane because they are long, live, or
upstream-baseline churn:
  * tests/e2e/core/cli_tmux/test_classic_cli_live_session.py
  * tests/e2e/core/cli_tmux/test_resize_scrollback.py
  * tests/e2e/core/delivery/test_cron_virtual_clock_soak.py
  * tests/e2e/core/delivery/test_messaging_exactly_once.py

Run the full E2E suite once at freeze; use this while changing code.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "qubit_runtime"
PYTHON = sys.executable
FAST_RUNTIME_PATHS = (
    "tests/e2e/core/dashboard",
    "tests/e2e/core/cli_tmux/test_classic_cli_main_parity.py",
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("TMPDIR", tempfile.gettempdir())
    env["PYTHONPATH"] = os.pathsep.join((str(RUNTIME), str(ROOT)))
    return env


def _run(argv: list[str], cwd: Path) -> int:
    print("$ " + " ".join(argv), flush=True)
    return subprocess.run(argv, cwd=cwd, env=_env(), check=False).returncode


def main() -> int:
    root_rc = _run(
        [PYTHON, "-m", "pytest", "-q", "tests/test_chat_session_navigation_e2e10.py"],
        ROOT,
    )
    runtime_rc = _run(
        [
            PYTHON,
            str(RUNTIME / "scripts" / "run_tests_parallel.py"),
            "--include-integration",
            "-j",
            "4",
            "--file-timeout",
            "180",
            "--file-retries",
            "0",
            *FAST_RUNTIME_PATHS,
        ],
        RUNTIME,
    )
    print(f"\nfast E2E: browser={'PASS' if root_rc == 0 else 'FAIL'} "
          f"dashboard+cli={'PASS' if runtime_rc == 0 else 'FAIL'}", flush=True)
    return 0 if root_rc == 0 and runtime_rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
