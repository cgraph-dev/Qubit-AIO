"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from .policy import (
    MAX_OUTPUT_CHARS,
    RLIMIT_AS_BYTES,
    RLIMIT_FSIZE_BYTES,
    RLIMIT_NOFILE,
    SAFE_PATH,
    OpenCodeError,
    _FORBIDDEN_TOKENS,
    _RUN_SUBCOMMAND,
)
# ---------------------------------------------------------------------------
# Sanitized environment + argv
# ---------------------------------------------------------------------------

# Strict explicit allowlist: these — and ONLY these — names are ever copied
# from the parent environment into the OpenCode child. Everything else is
# rejected by construction, including HOME, PYTHONPATH, LD_PRELOAD, BASH_ENV,
# ENV, XDG_CONFIG_HOME, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, DISPLAY,
# WAYLAND_DISPLAY, SSH_AUTH_SOCK, GIT_* overrides, *KEY*/*TOKEN*/*SECRET*/
# *COOKIE*/*AUTH*/*PASSWORD*/*CREDENTIAL* names, and any shell, session,
# desktop, or account variable. There is no operator pass-through knob.
_SAFE_ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "LC_MESSAGES", "TZ")


def sanitized_env() -> Dict[str, str]:
    """Build the minimal child environment (never inherits secrets).

    PATH is the fixed SAFE_PATH constant — the parent PATH is never copied,
    so personal, writable, or attacker-controlled directories can never
    influence binary resolution inside the child.
    """
    env: Dict[str, str] = {}
    for name in _SAFE_ENV_ALLOW:
        if name == "PATH":
            continue
        if name in os.environ:
            env[name] = os.environ[name]
    env["PATH"] = SAFE_PATH
    return env


def build_argv(binary: str, workdir: Path, task: str) -> List[str]:
    """Build the exact one-shot argv (no server, no auto-approve, no shell)."""
    argv = [binary, _RUN_SUBCOMMAND, "--format", "json",
            "--dir", str(workdir), task]
    for tok in argv[1:]:
        if tok in _FORBIDDEN_TOKENS:
            raise OpenCodeError(f"forbidden opencode token: {tok!r}")
    return argv


def _looks_like_server_argv(argv: List[str]) -> bool:
    return any(t in _FORBIDDEN_TOKENS for t in argv[1:])


# ---------------------------------------------------------------------------
# Subprocess execution (injectable for hermetic tests)
# ---------------------------------------------------------------------------

RunOpencode = Callable[[List[str], Path, Dict[str, str], float],
                       Dict[str, Any]]


def real_run_opencode(argv: List[str], workdir: Path, env: Dict[str, str],
                      timeout_s: float) -> Dict[str, Any]:
    """Run the pinned binary one-shot, confined; kill/reap on timeout.

    The child ALWAYS runs inside the Bubblewrap filesystem boundary (ro
    system, private /tmp+HOME, approved workdir read-write). If confinement
    cannot be established, no process is spawned and a typed
    CONFINEMENT_UNAVAILABLE failure is returned. The confined child is
    further limited with setrlimit CPU/AS/FSIZE/NPROC/NOFILE ceilings in
    preexec (plus its own process group for kill/reap). No shell, no server
    argv, bounded output.
    """
    if _looks_like_server_argv(argv):
        raise OpenCodeError("refusing server/auto-approve opencode argv")
    from .confinement import build_confinement_argv, confinement_available
    if not argv or not argv[0]:
        return {"state": "failed", "code": "BINARY_MISSING",
                "detail": "no executable supplied"}
    import shutil as _shutil
    binary = (argv[0] if os.path.isabs(argv[0])
              else (_shutil.which(argv[0]) or ""))
    if not binary:
        return {"state": "failed", "code": "BINARY_MISSING",
                "detail": f"executable not found: {argv[0]}"[:200]}
    argv = [binary] + list(argv[1:])
    ok, bwrap, reason = confinement_available()
    if not ok:
        return {"state": "failed", "code": "CONFINEMENT_UNAVAILABLE",
                "detail": f"live execution disabled: {reason}; "
                          "re-qualify the platform before any live run"
                [:300]}
    confined = build_confinement_argv(bwrap, argv[0], workdir, argv, env)
    try:
        proc = subprocess.Popen(
            confined, cwd=str(workdir), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, preexec_fn=lambda: _limited_preexec(timeout_s))
    except FileNotFoundError:
        return {"state": "failed", "code": "BINARY_MISSING",
                "detail": "confinement binary not found"}
    except Exception as exc:
        return {"state": "failed", "code": "SPAWN_FAILED",
                "detail": str(exc)[:200]}
    t0 = time.monotonic()
    try:
        out, err = proc.communicate(timeout=timeout_s)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", "killed after timeout"
        return {"state": "failed", "code": "TIMEOUT",
                "detail": f"exceeded {timeout_s}s; process group killed",
                "stdout": (out or "")[:MAX_OUTPUT_CHARS],
                "stderr": (err or "")[:2000],
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    finally:
        try:
            proc.wait(timeout=0)
        except Exception:
            pass
    return {"state": "OK" if code == 0 else "failed",
            "code": "OK" if code == 0 else f"EXIT_{code}",
            "stdout": (out or "")[:MAX_OUTPUT_CHARS],
            "truncated": len(out or "") > MAX_OUTPUT_CHARS,
            "stderr": (err or "")[:2000],
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


_AUTH_FAILURE_MARKERS = (
    "unauthenticated", "unauthorized", "authentication required",
    "login required", "please login", "please log in", "api key",
    "invalid api key", "missing credentials", "no auth", "forbidden",
    "http 401", "http 403", "status 401", "status 403",
)


def _looks_like_auth_failure(stderr: str, code: str) -> bool:
    low = (stderr or "").lower()
    return code in ("EXIT_1",) and any(m in low for m in _AUTH_FAILURE_MARKERS)


def parse_run_output(stdout: str) -> Dict[str, Any]:
    """Parse ``--format json`` event stream (bounded, fail closed).

    Only JSON-object lines carrying a text/content/message string field count
    as model output. Anything else — prose, HTML, tool dumps, partial JSON —
    is a typed MALFORMED_OUTPUT failure, never success. A failed or partial
    run must never be reported complete.
    """
    if not stdout.strip():
        return {"state": "failed", "code": "EMPTY_OUTPUT",
                "detail": "opencode produced no output"}
    texts: List[str] = []
    try:
        for line in stdout.splitlines()[:500]:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(evt, dict):
                for key in ("text", "content", "message"):
                    val = evt.get(key)
                    if isinstance(val, str) and val.strip():
                        texts.append(val.strip()[:2000])
                        break
    except Exception as exc:
        return {"state": "failed", "code": "PATCH_MALFORMED",
                "detail": f"output parse failed: {exc}"[:200]}
    if not texts:
        return {"state": "failed", "code": "MALFORMED_OUTPUT",
                "detail": "opencode output carried no JSON text event; "
                          "raw/nonconforming output is never success"}
    combined = "\n".join(texts)[:MAX_OUTPUT_CHARS]
    return {"state": "OK", "text": combined}

def _probe_version(binary: str) -> str:
    """Report the configured binary's ``--version`` (never shell, gated).

    The path must be absolute and point at an executable file; the probe runs
    with the strict sanitized environment. Returns the stripped version line
    or one of: invalid-path / unavailable.
    """
    try:
        p = Path(os.path.expanduser(binary or ""))
        if not p.is_absolute():
            return "invalid-path"
        if not (p.is_file() and os.access(p, os.X_OK)):
            return "unavailable"
        proc = subprocess.run(
            [str(p), "--version"], capture_output=True, text=True,
            timeout=10, env=sanitized_env())
        return ((proc.stdout or proc.stderr or "").strip()[:64]
                or "unavailable")
    except Exception:
        return "unavailable"


def _limited_preexec(cpu_s: float) -> None:
    """Child-side confinement: new session + CPU/AS/FSIZE/NOFILE caps.

    NPROC is deliberately NOT capped here: capping it before user-namespace
    creation breaks the setup itself on busy machines (observed EAGAIN), so
    the process cap is applied inside the sandbox via prlimit (see
    confinement.build_confinement_argv).
    """
    try:
        os.setsid()
        try:
            import resource as _res
        except ImportError:
            return
        cpu = max(1, int(cpu_s) + 60)
        _res.setrlimit(_res.RLIMIT_CPU, (cpu, cpu))
        _res.setrlimit(_res.RLIMIT_AS,
                       (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES))
        _res.setrlimit(_res.RLIMIT_FSIZE,
                       (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))
        _res.setrlimit(_res.RLIMIT_NOFILE, (RLIMIT_NOFILE, RLIMIT_NOFILE))
    except Exception:
        os._exit(127)

