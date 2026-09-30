"""Isolated execution for untrusted/generated code (Packet 3B).

ADAPTS pinned OpenSandbox execd behavior (UPSTREAM_REUSE.md R3, Apache-2.0,
commit 6b2023e9b7eb80a940d88e6ae05fcbc0eb0cf23f): probe.go (startup probe +
version parse + namespace smoke argv), bwrap_linux.go (binary discovery +
setuid detection), config.go (clearenv + secret-shaped env deny), and
sandbox-lifecycle.yml + isolated_session_lifecycle_test.go (lifecycle states,
terminal reason codes, bounded idempotent stop, reap-on-failed-start).

Fail-closed contract:
  * no Bubblewrap / no user systemd / failed smoke => ISOLATION_UNAVAILABLE
    and NOTHING runs on the host — there is no host fallback;
  * every bind source is realpath-resolved; symlink escapes outside declared
    roots are rejected before launch (LOCAL requirement, not upstream);
  * workloads always run network-less with private /proc, minimal filesystem,
    cleared environment;
  * each run lives in its OWN user-systemd transient unit (vb-iso-<id>) with
    MemoryMax/TasksMax/CPUQuota — never inside the bridge cgroup;
  * stop() is idempotent and bounded; TTL expiry, cancellation, timeout, and
    crash converge to a terminal state with an upstream reason code.
"""
from __future__ import annotations

import enum
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from .logging_setup import log_event
from .redaction import redact_text

ISOLATION_UNAVAILABLE = "ISOLATION_UNAVAILABLE"

_BWRAP_VERSION_RE = re.compile(r"b(?:ubble)?wrap\s+(\d+\.\d+\.\d+)")  # probe.go
_SECRET_ENV_RE = re.compile(  # config.go secret-shaped deny, adapted
    r"(?i)(API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|AWS_|CREDENTIAL)")


class IsolationError(RuntimeError):
    """Typed isolation failure (code carries the machine-readable reason)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}".strip())
        self.code = code


class IsolationUnavailable(IsolationError):
    """Raised instead of running ANY untrusted code without isolation."""

    def __init__(self, detail: str = ""):
        super().__init__(ISOLATION_UNAVAILABLE, detail)



# ------------------------------------------------------------------- probing

@dataclass(frozen=True)
class ProbeResult:
    available: bool
    version: str = ""
    message: str = ""            # diagnostic when unavailable


def find_bwrap() -> str:
    """bwrap_linux.go findBwrap order, trimmed to standard locations."""
    found = shutil.which("bwrap")
    return found or next((c for c in ("/usr/bin/bwrap", "/usr/local/bin/bwrap") if os.path.isfile(c) and os.access(c, os.X_OK)), "")


def _is_setuid_bwrap(path: str) -> bool:
    try:
        return bool(os.stat(path).st_mode & 0o4000)   # S_ISUID
    except OSError:
        return False


def _run_capture(argv: list, timeout_s: float = 10.0) -> Tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)[:200]
    return proc.returncode, (proc.stderr or proc.stdout or "")[-300:]


_PROBE_CACHE: Optional[ProbeResult] = None


def probe_isolation(refresh: bool = False) -> ProbeResult:
    """Startup capability probe (probe.go shape); cached per process."""
    global _PROBE_CACHE
    if _PROBE_CACHE is not None and not refresh:
        return _PROBE_CACHE
    bwrap = find_bwrap()
    if not bwrap:
        return ProbeResult(False, "", "bwrap not found in PATH")
    rc, out = _run_capture([bwrap, "--version"])
    m = _BWRAP_VERSION_RE.search(out or "")
    if rc != 0 or not m:
        return ProbeResult(False, "", f"bwrap --version failed: {out[:120]}")
    # Upstream smoke args (probe.go bwrapSmokeArgs), userns mode; the short
    # `true` probe may read-only bind / — real workloads never do.
    smoke = [bwrap, "--unshare-user"] + (
        [] if _is_setuid_bwrap(bwrap) else ["--disable-userns"]) + ["--unshare-pid", "--unshare-uts", "--unshare-ipc",
              "--unshare-cgroup", "--uid", str(os.getuid()),
              "--gid", str(os.getgid()),
              "--ro-bind", "/", "/", "--proc", "/proc", "--", "true"]
    rc, err = _run_capture(smoke)
    if rc != 0:
        return ProbeResult(False, m.group(1),
                           f"namespace smoke test failed: {err[:160]}")
    _, sysout = _run_capture(["systemctl", "--user", "is-system-running"])
    if sysout.strip() not in ("running", "degraded"):
        result = ProbeResult(False, m.group(1),
                             f"user systemd unavailable: {sysout[:120]}")
    else:
        result = ProbeResult(True, m.group(1))
    _PROBE_CACHE = result
    log_event("isolation.probe", available=result.available,
              version=result.version, message=result.message[:200])
    return result


# --------------------------------------------------------------- run request

def _within_roots(real: str, roots: Tuple[str, ...]) -> bool:
    """Boundary-safe membership via commonpath (NOT startswith, which would
    wrongly match /workspace-secret against root /workspace)."""
    r = os.path.normpath(real)
    for root in roots:
        b = os.path.normpath(root)
        try:
            if os.path.commonpath((r, b)) == b:
                return True
        except ValueError:               # mixed abs/rel — never matches
            continue
    return False


def resolve_real_path(path: str, *, allowed_prefixes: Tuple[str, ...],
                      must_be_dir: bool = False) -> str:
    """realpath resolution + symlink-escape rejection (local requirement)."""
    if not os.path.isabs(path):
        raise IsolationError("INVALID_SPEC", f"path not absolute: {path}")
    real = os.path.realpath(path)
    if not _within_roots(real, allowed_prefixes):
        raise IsolationError("SYMLINK_ESCAPE", f"{path} escapes declared roots")
    if must_be_dir and not os.path.isdir(real):
        raise IsolationError("INVALID_SPEC", f"not a directory: {real}")
    return real


@dataclass(frozen=True)
class IsolatedSpec:
    """One untrusted workload: argv only — shell strings never accepted."""
    argv: Tuple[str, ...]
    workspace: str                       # absolute dir, rw-mounted /workspace
    read_only_paths: Tuple[str, ...] = ()
    env_allowlist: Tuple[str, ...] = ()   # names resolved from bridge env
    memory_max_mb: int = 512
    tasks_max: int = 64
    cpu_quota_pct: int = 50
    startup_timeout_s: float = 20.0
    timeout_s: float = 120.0
    ttl_s: float = 600.0

    def __post_init__(self):
        if not self.argv or any(not isinstance(a, str) or not a for a in self.argv):
            raise IsolationError("INVALID_SPEC", "argv must be non-empty strs")


def build_bwrap_argv(spec: IsolatedSpec) -> Tuple[str, list]:
    """Minimal-FS, private-proc, network-none sandbox argv for one run."""
    ws = resolve_real_path(spec.workspace, allowed_prefixes=("/home/", "/tmp/", "/var/tmp/", "/mnt/", "/media/"), must_be_dir=True)
    bwrap = find_bwrap()
    argv = [bwrap, "--unshare-user"]
    if not _is_setuid_bwrap(bwrap):
        argv.append("--disable-userns")
    argv += ["--unshare-pid", "--unshare-ipc", "--unshare-net",
             "--die-with-parent", "--proc", "/proc", "--dev", "/dev",
             "--tmpfs", "/tmp", "--tmpfs", "/run",
             "--ro-bind", "/usr", "/usr",
             "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
             "--ro-bind-try", "/lib64", "/lib64",
             "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
             "--bind", ws, "/workspace", "--clearenv",
             "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME",
             "/workspace", "--setenv", "TMPDIR", "/tmp",
             "--setenv", "LANG", "C.UTF-8"]
    for name in sorted(set(spec.env_allowlist)):
        val = os.environ.get(name)
        if not _SECRET_ENV_RE.search(name) and val and len(val) <= 200:
            argv += ["--setenv", name, val]   # secret-shaped names denied
    for ro in spec.read_only_paths:
        real = resolve_real_path(ro, allowed_prefixes=("/usr/", "/opt/", ws))
        argv += ["--ro-bind", real, real]
    argv += ["--chdir", "/workspace", "--", *spec.argv]
    return ws, argv


# ----------------------------------------------------------------- lifecycle

class State(str, enum.Enum):
    PENDING = "pending"; STARTING = "starting"; RUNNING = "running"
    PAUSED = "paused"; STOPPING = "stopping"
    TERMINATED = "terminated"; FAILED = "failed"


TERMINAL = {State.TERMINATED, State.FAILED}
REASON_USER_DELETE, REASON_TTL_EXPIRY = "user_delete", "ttl_expiry"
REASON_PROVISION_TIMEOUT, REASON_RUNTIME_ERROR = "provision_timeout", "runtime_error"


@dataclass
class RunOutcome:
    ok: bool
    exit_code: Optional[int] = None
    killed_by: str = ""                  # "" | user_delete | ttl_expiry
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    reason: str = ""


def _unit_show(unit: str, prop: str) -> list:
    rc, out = _run_capture(["systemctl", "--user", "show", "-p", prop,
                            "--value", unit], timeout_s=5.0)
    return [] if rc != 0 else out.splitlines()


def _terminal_facts(unit: str) -> Tuple[str, Optional[int]]:
    """(ActiveState, ExecMainStatus) parsed from key=value output (stable)."""
    rc, out = _run_capture(["systemctl", "--user", "show", "-p", "ActiveState",
                            "-p", "ExecMainStatus", unit], timeout_s=5.0)
    if rc != 0:
        return "", None
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        code = int(props.get("ExecMainStatus", ""))
    except ValueError:
        code = None
    return props.get("ActiveState", ""), code


def _wait_unit_state(unit: str, states: tuple, deadline: float) -> bool:
    """Poll until the unit reaches one of `states`; True iff observed."""
    while time.monotonic() < deadline:
        vals = _unit_show(unit, "ActiveState")
        if vals and vals[0] in states:
            return True
        time.sleep(0.05)
    return False


class IsolatedRun:
    """Owns exactly one transient unit end-to-end (cleanup owner object)."""

    def __init__(self, spec: IsolatedSpec):
        self.spec = spec
        self.run_id = uuid.uuid4().hex[:12]
        self.unit, self.state, self.reason = f"vb-iso-{self.run_id}", State.PENDING, ""
        self.workspace = ""
        self._deadline = time.monotonic() + max(1.0, spec.ttl_s)

    def _set(self, state: State, reason: str = "", msg: str = "") -> None:
        self.state, self.reason = state, reason or self.reason
        if state in TERMINAL:                # leave no failed-unit residue
            subprocess.run(["systemctl", "--user", "reset-failed", self.unit],
                           capture_output=True, timeout=5)
        log_event("isolated.state", run_id=self.run_id, unit=self.unit,
                  state=state.value, reason=self.reason,
                  detail=redact_text(msg)[:200])

    def start(self) -> None:
        if self.state is not State.PENDING:
            raise IsolationError("INVALID_SPEC", "start called twice")
        if not probe_isolation().available:
            self._set(State.FAILED, REASON_RUNTIME_ERROR, ISOLATION_UNAVAILABLE)
            raise IsolationUnavailable("probe unavailable; refusing to run")
        try:
            self.workspace, bw_argv = build_bwrap_argv(self.spec)
        except IsolationError as exc:
            self._set(State.FAILED, "invalid_spec", exc.detail)
            raise
        self._set(State.STARTING)
        sp = self.spec
        cmd = (["systemd-run", "--user", "--unit", self.unit,
                "-p", f"MemoryMax={sp.memory_max_mb}M",
                "-p", f"TasksMax={sp.tasks_max}",
                "-p", f"CPUQuota={sp.cpu_quota_pct}%", "-p", "TimeoutStopSec=5s",
                "-p", f"StandardOutput=file:{self.workspace}/stdout.log",
                "-p", f"StandardError=file:{self.workspace}/stderr.log",
                "--"] + bw_argv)
        rc, err = _run_capture(cmd, timeout_s=15.0)
        if rc != 0:
            self._set(State.FAILED, REASON_RUNTIME_ERROR, err[-150:])
            raise IsolationError("START_FAILED", err[:200])  # client-level
        deadline = time.monotonic() + max(2.0, self.spec.startup_timeout_s)
        if _wait_unit_state(self.unit, ("active",), deadline):
            self._set(State.RUNNING)
            return
        self._set(State.FAILED, REASON_PROVISION_TIMEOUT, "never active")
        raise IsolationError(REASON_PROVISION_TIMEOUT.upper(), "never became active")

    def main_pid(self) -> int:
        vals = _unit_show(self.unit, "MainPID")
        return int(vals[0]) if vals and vals[0].isdigit() else 0

    def cgroup_path(self) -> str:
        try:
            with open(f"/proc/{self.main_pid()}/cgroup") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def _read_log(self, name: str) -> Tuple[str, bool]:
        try:
            with open(os.path.join(self.workspace, name), errors="replace") as fh:
                data = fh.read(200_001)
        except OSError:
            return "", False
        return data[:200_000], len(data) > 200_000

    def _signal_unit(self, sig: str) -> None:
        args = ["systemctl", "--user", "kill", f"--signal={sig}", self.unit]
        subprocess.run(args, capture_output=True, timeout=5)

    def _transition(self, sig: str, want: State, new: State) -> None:
        if self.state is not want:
            raise IsolationError("INVALID_SPEC", f"{sig} from {self.state}")
        self._signal_unit(sig)
        self._set(new)

    def pause(self) -> None:
        self._transition("SIGSTOP", State.RUNNING, State.PAUSED)

    def resume(self) -> None:
        self._transition("SIGCONT", State.PAUSED, State.RUNNING)


    def stop(self, reason: str = REASON_USER_DELETE) -> RunOutcome:
        if self.state in TERMINAL:
            return self._outcome(killed_by=reason)
        self._set(State.STOPPING, reason)
        subprocess.run(["systemctl", "--user", "stop", self.unit], capture_output=True, timeout=8)
        if not _wait_unit_state(self.unit, ("inactive", "failed"),
                                time.monotonic() + 6.0):
            self._signal_unit("SIGKILL")     # bounded fallback
            time.sleep(0.2)
        self._set(State.TERMINATED, reason)
        return self._outcome(killed_by=reason)

    def wait(self, timeout_s: Optional[float] = None) -> RunOutcome:
        if self.state is State.PENDING:
            raise IsolationError("INVALID_SPEC", "wait before start")
        budget = max(0.1, float(timeout_s)) if timeout_s else             self.spec.timeout_s
        deadline = min(time.monotonic() + budget, self._deadline)
        while True:
            if self.state not in TERMINAL:
                if time.monotonic() >= self._deadline \
                        or time.monotonic() >= deadline:
                    return self.stop(reason=REASON_TTL_EXPIRY)
            active, code = _terminal_facts(self.unit)
            if active == "failed":
                self._set(State.FAILED, REASON_RUNTIME_ERROR, "unit failed")
                return self._outcome(ok=False, exit_code=code)
            if active == "inactive":
                self._set(State.TERMINATED)
                return self._outcome(ok=(code == 0), exit_code=code)
            if active == "active" and self.state is State.STARTING:
                self._set(State.RUNNING)
            time.sleep(0.05)

    def _outcome(self, ok: bool = True, exit_code: Optional[int] = None,
                 killed_by: str = "") -> RunOutcome:
        out, t1 = self._read_log("stdout.log")
        err, t2 = self._read_log("stderr.log")
        return RunOutcome(ok and self.state is not State.FAILED, exit_code,
                          killed_by, out, err, t1 or t2, self.reason)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.state not in TERMINAL:
            self.stop()
        return False
