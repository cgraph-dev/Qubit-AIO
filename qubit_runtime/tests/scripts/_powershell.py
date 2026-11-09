"""Resolve a PowerShell interpreter that can actually start under the caller's env.

`shutil.which("pwsh")` is not enough. A version-manager shim (mise, asdf) resolves on
PATH and then dies at startup, because it reads its own config from $HOME:

    mise ERROR No version is set for shim: pwsh

Most of these tests run the interpreter with HOME pointed at a tmp_path to keep the
host out of the run — which is exactly the environment where an unpinned shim fails.
That is a host setup gap, not a defect in the rules under test, so it must read as
"no PowerShell available" (skip on POSIX, fail on native Windows) rather than as a
test failure.

Probing with the SAME env the real invocation gets is the point: a shim that works
under the developer's HOME may not work under the test's.
"""
from __future__ import annotations

import os
import shutil
import subprocess


def find_powershell(env: dict[str, str] | None = None) -> str | None:
    """The first PowerShell that starts successfully under *env*, or None."""
    order = ("powershell", "pwsh") if os.name == "nt" else ("pwsh", "powershell")
    probe_env = dict(os.environ) if env is None else env
    for name in order:
        found = shutil.which(name)
        if not found or not runs(found, probe_env):
            continue
        return found
    return None


def runs(pwsh: str, env: dict[str, str] | None = None) -> bool:
    """Whether *pwsh* can start, not merely resolve on PATH."""
    try:
        probe = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0
