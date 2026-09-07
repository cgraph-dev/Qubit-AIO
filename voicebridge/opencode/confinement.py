"""Packet 8R: Bubblewrap filesystem confinement for the OpenCode child.

Fail-closed boundary: the OpenCode subprocess runs inside a Bubblewrap
sandbox that sees ONLY a read-only system, a private /tmp and HOME, minimal
read-only network prerequisites (resolv.conf/hosts/CA bundle), and the single
approved workdir read-write. The network namespace is deliberately retained
(provider API calls are the point of a live run); everything else is denied
by absence. If confinement cannot be established on the current platform,
live execution is disabled with a typed CONFINEMENT_UNAVAILABLE state —
never an unconfined run.

Reuses the existing plan-blessed primitive (Bubblewrap 0.9.0, same user-
namespace smoke shape as voicebridge/isolated.py) without adding a
dependency, daemon, container runtime, or second model server.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

_BWRAP_MIN_VERSION = (0, 9, 0)
_BWRAP_VERSION_RE = re.compile(r"b(?:ubble)?wrap\s+(\d+)\.(\d+)\.(\d+)")

# In-sandbox execution identity for the pinned binary: single-file
# read-only bind (the operator's tool directory is never mounted).
BWRAP_BINARY_DEST = "/opencode-bin"

# In-sandbox HOME replacement (private empty tmpfs): the operator's real
# HOME is never mounted, so no config, cookies, keys, or profiles leak in.
# Deliberately NOT under /home/ — repository packaging gates reject
# home-looking literals in production code, and nothing here may resemble a
# personal path.
CONFINED_HOME = "/sandbox-home"


def find_confinement_binary() -> str:
    """Locate bwrap using the existing isolated helper's search order."""
    try:
        from ..isolated import find_bwrap
    except Exception:
        return ""
    try:
        return find_bwrap()
    except Exception:
        return ""


def _is_setuid(path: str) -> bool:
    try:
        return bool(os.stat(path).st_mode & 0o4000)
    except OSError:
        return False


def confinement_available() -> Tuple[bool, str, str]:
    """Probe the confinement platform. Returns (ok, bwrap_path, reason).

    Fresh probe on every call (platform state, not cached): bwrap present,
    version >= 0.9.0, and a minimal user-namespace smoke passes. No systemd
    requirement — OpenCode runs are plain supervised subprocesses.
    """
    bwrap = find_confinement_binary()
    if not bwrap:
        return False, "", "bwrap not installed"
    try:
        proc = subprocess.run(
            [bwrap, "--version"], capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return False, "", f"bwrap --version failed: {exc}"[:160]
    m = _BWRAP_VERSION_RE.search((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0 or not m:
        return False, "", "bwrap --version unparseable"
    if (int(m.group(1)), int(m.group(2)), int(m.group(3))) < _BWRAP_MIN_VERSION:
        return False, "", f"bwrap too old: {m.group(0)}"
    smoke = ([bwrap, "--unshare-user"]
             + ([] if _is_setuid(bwrap) else ["--disable-userns"])
             + ["--unshare-pid", "--unshare-uts", "--unshare-ipc",
                "--unshare-cgroup", "--uid", str(os.getuid()),
                "--gid", str(os.getgid()), "--ro-bind", "/usr", "/usr",
                "--symlink", "usr/bin", "/bin",
                "--symlink", "usr/lib", "/lib",
                "--symlink", "usr/lib64", "/lib64",
                "--symlink", "usr/sbin", "/sbin",
                "--proc", "/proc", "--tmpfs", "/tmp",
                "--", "/usr/bin/true"])
    try:
        res = subprocess.run(smoke, capture_output=True, text=True,
                             timeout=15)
    except Exception as exc:
        return False, "", f"namespace smoke failed: {exc}"[:160]
    if res.returncode != 0:
        return False, "", (
            f"namespace smoke failed: {(res.stderr or '')[:160]}")
    return True, bwrap, f"OK (bwrap {m.group(0)})"


def _live_process_count() -> int:
    try:
        return sum(1 for _p in os.listdir("/proc") if _p.isdigit())
    except OSError:
        return 256


def build_confinement_argv(bwrap: str, binary: str, workdir: Path,
                           inner_argv: List[str],
                           extra_env: Dict[str, str]) -> List[str]:
    """Wrap the one-shot OpenCode argv in the confinement boundary.

    Visible inside: ro /usr (+ merged-usr symlinks), /proc, /dev, private
    /tmp, private empty HOME, ro network prerequisites, the pinned binary at
    BWRAP_BINARY_DEST, and the approved workdir read-write. Everything else
    — original checkout, other checkouts, real HOME, credentials, /tmp
    outside the run, system dirs — does not exist in the sandbox.

    The process-count cap is applied INSIDE via prlimit (dynamic: live
    owner-process count + margin): capping NPROC before namespace creation
    breaks the setup itself, so preexec must not do it.
    """
    from .policy import RLIMIT_NPROC_FLOOR, RLIMIT_NPROC_MARGIN
    nproc = max(RLIMIT_NPROC_FLOOR,
                _live_process_count() + RLIMIT_NPROC_MARGIN)
    uid, gid = str(os.getuid()), str(os.getgid())
    argv: List[str] = [bwrap, "--unshare-user"]
    if not _is_setuid(bwrap):
        argv.append("--disable-userns")
    argv += ["--unshare-pid", "--unshare-uts", "--unshare-ipc",
             "--unshare-cgroup", "--uid", uid, "--gid", gid,
             "--die-with-parent",
             "--ro-bind", "/usr", "/usr",
             "--symlink", "usr/bin", "/bin",
             "--symlink", "usr/lib", "/lib",
             "--symlink", "usr/lib64", "/lib64",
             "--symlink", "usr/sbin", "/sbin",
             "--proc", "/proc", "--dev", "/dev",
             "--tmpfs", "/tmp",
             "--tmpfs", CONFINED_HOME,
             "--setenv", "HOME", CONFINED_HOME,
             # Explicit empty /etc first: per-file ro-binds below would
             # otherwise materialize a writable /etc as a side effect.
             # Either way it is sandbox-local and discarded, but explicit
             # is auditable.
             "--tmpfs", "/etc",
             "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf",
             "--ro-bind-try", "/etc/hosts", "/etc/hosts",
             "--ro-bind-try", "/etc/ssl/certs/ca-certificates.crt",
             "/etc/ssl/certs/ca-certificates.crt",
             "--ro-bind-try", "/etc/pki/tls/certs/ca-bundle.crt",
             "/etc/pki/tls/certs/ca-bundle.crt",
             "--ro-bind", binary, BWRAP_BINARY_DEST,
             "--bind", str(workdir), str(workdir),
             "--chdir", str(workdir)]
    for key in ("PATH", "LANG", "LC_ALL", "LC_MESSAGES", "TZ"):
        if key in extra_env:
            argv += ["--setenv", key, extra_env[key]]
    argv.append("--")
    return argv + ["prlimit", f"--nproc={nproc}:{nproc}", "--",
                   BWRAP_BINARY_DEST] + list(inner_argv[1:])
