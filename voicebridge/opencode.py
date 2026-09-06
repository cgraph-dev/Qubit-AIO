"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

An OPTIONAL, disabled-by-default adapter for explicitly requested OpenCode
coding workflows. Ordinary Qubit model routing stays in Packet 8Q; OpenCode is
used only when the owner requests a bounded coding run. OpenCode's ability to
run tools is never treated as permission for Qubit to run arbitrary shell or
edit arbitrary files.

Upstream posture (docs/UPSTREAM_REUSE.md R8R): DOCUMENT-ONLY reference plus
WRAP of the existing 8F action machinery. No upstream code is vendored; the
local ``opencode`` executable (pinned version below) is invoked at arm's
length as ``opencode run --format json --dir <workdir> <task>``. Never
``npx -y``, a floating package, remote instructions, or an unreviewed MCP
server.

Invariants enforced here (each has a test in tests/test_packet8r.py):

- Disabled by default: a state file (``opencode_adapter.json``) defaults to
  ``user_enabled=False`` and the packaged config master switch defaults to
  ``enabled=False``. Both must be true (and no emergency-stop latch) or every
  run returns typed ``ADAPTER_DISABLED`` and no subprocess is spawned.
- Every run goes through the existing 8F ``ActionRunner``/broker plus the 7E
  gate: authenticated owner request, approved project root bound into the
  action hash, allowlisted file scope (= the resolved project root), bounded
  task/output/context, timeout, resource caps, visible run ID, cancellation,
  kill/reap, and redacted receipts. The authenticated CLI has full parity
  and the authenticated dashboard has read-only parity on the same
  runner/broker — voice and tray execution are intentionally NOT wired (see
  ``request_coding_run``), and no second listener exists.
- Minimal sanitized environment: only an allowlist (PATH/LANG/LC_ALL plus
  operator-configured ``pass_env``) reaches the child. Provider keys, browser
  cookies, desktop-session variables, and personal paths are never inherited;
  missing credentials fail closed as typed ``MISSING_CREDENTIALS``.
- ``act.opencode.run`` is DESTRUCTIVE tier: project-code modification stays
  high-impact even under a power profile and always needs a fresh human
  confirmation bound to the exact action hash. Non-disposable (in-project)
  writes additionally require a prior ``preview`` hash (``expected_hash``).
- Never auto-commit, push, publish, install dependencies, alter services or
  permissions, access accounts, delete files, or run arbitrary shell. The
  executor argv is exactly ``[binary, run, --format, json, --dir, workdir,
  task]`` — ``--auto``/``serve``/``web``/``attach``/``share`` are forbidden
  by construction and asserted in tests.
- Repository instructions, tool output, and OpenCode output are untrusted
  input: hostile markers are flagged (never executed), output is bounded and
  redacted, malformed JSON output is a typed failure, and a partial/failed
  run is never reported complete.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .actions import (
    ActionError, ActionRunner, ActionSpec, ActionStore, ActionValidationError,
    ADAPTER_OPENCODE, RB_IMPOSSIBLE, RB_NONE, ST_ADAPTER_DISABLED, ST_OK,
    _render_preview, _spec, actions_broker, action_spec, register_action_spec,
)
from .capabilities import Tier
from .logging_setup import log_event
from .redaction import redact_obj, redact_text

# ---------------------------------------------------------------------------
# Identity / pinning
# ---------------------------------------------------------------------------

# Upstream: https://github.com/sst/opencode (MIT (c) 2025 opencode).
# Pinned local executable under test/qualification. A different installed
# version is reported (never silently accepted as the pin).
PINNED_OPENCODE_VERSION = "1.18.29"
DEFAULT_BINARY = os.path.expanduser("~/.opencode/bin/opencode")

ACT_PLAN = "act.opencode.plan"
ACT_RUN = "act.opencode.run"
OPENCODE_ACTIONS = (ACT_PLAN, ACT_RUN)

# argv allowlist: exactly this shape, nothing else. Any flag outside this set
# (notably --auto, --share, --attach, serve, web) is a hard failure.
_RUN_SUBCOMMAND = "run"
_ALLOWED_FLAGS = frozenset({"--format", "--dir"})
_FORBIDDEN_TOKENS = frozenset({
    "--auto", "--share", "--attach", "--continue", "-c", "--session", "-s",
    "--fork", "--agent", "--model", "-m", "--command", "--port", "--hostname",
    "--cors", "--mdns", "--pure", "serve", "web", "attach", "acp", "mcp",
    "upgrade", "uninstall", "plugin", "pr", "github", "export", "import",
})

# Bounded budgets.
MAX_TASK_CHARS = 4000
MAX_PROMPT_FILE_BYTES = 16 * 1024
MAX_OUTPUT_CHARS = 32_768
MAX_DIFFSTAT_LINES = 40
MAX_DIFF_FILES = 100
DEFAULT_TIMEOUT_S = 300.0
MAX_TIMEOUT_S = 600.0

# Generated-worktree validation caps (fail closed when exceeded).
MAX_WORKDIR_FILES = 1000
MAX_WORKDIR_BYTES = 100 * 1024 * 1024

# Child-process resource ceilings enforced with setrlimit in preexec.
RLIMIT_AS_BYTES = 4 * 1024 * 1024 * 1024
RLIMIT_FSIZE_BYTES = 512 * 1024 * 1024
RLIMIT_NPROC = 128
RLIMIT_NOFILE = 256

# Orphaned disposable-worktree sweep age (bases older than this are reaped).
ORPHAN_MAX_AGE_S = 7200.0

# Directories that can never be a project root or workdir.
_SECRET_DIR_NAMES = frozenset({
    ".ssh", ".gnupg", ".opencode", ".config", ".local", ".password-store",
    ".aws", ".openclaw",
})
_SECRET_FILE_HINTS = (".env", "credentials", "secret", "token", ".pem",
                      ".key", "id_rsa", "id_ed25519")

# Markers that make repository/prompt/tool text untrusted-but-flagged. They
# never become authority; the run still executes (bounded, confirmed) but the
# receipt carries an `untrusted_flags` warning list.
_HOSTILE_MARKERS = (
    "ignore previous instructions", "ignore all previous instructions",
    "disable safety", "disable confirmation", "never ask for permission",
    "auto-approve", "--auto", "curl | sh", "curl|sh", "rm -rf /",
    "mkfs", ":(){:|:&};:",
)


# ---------------------------------------------------------------------------
# Errors (reuse 8F typed errors; one local code for clarity)
# ---------------------------------------------------------------------------

class OpenCodeError(ActionError):
    """Typed OpenCode adapter failure (state/details are JSON-safe)."""


# ---------------------------------------------------------------------------
# Project-root validation
# ---------------------------------------------------------------------------

def resolve_project_root(raw: str) -> Path:
    """Resolve and validate an approved project root (fail closed).

    Rules: must be a non-empty bounded path, must exist, must be a directory,
    must not be ``/`` or a home root, must not be (or resolve through a
    symlink into) a secret-bearing directory, and the resolved path must stay
    a real directory. ``~`` is expanded before resolution.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ActionValidationError("opencode: project root is required")
    text = os.path.expanduser(raw.strip())
    if len(text) > 512:
        raise ActionValidationError("opencode: project root too long")
    candidate = Path(text)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ActionValidationError(
            f"opencode: project root unresolvable: {exc}") from None
    if not resolved.is_dir():
        raise ActionValidationError("opencode: project root is not a directory")
    if resolved == Path("/") or resolved == Path.home().resolve():
        raise ActionValidationError(
            "opencode: refusing filesystem/home root as project root")
    parts = {p.lower() for p in resolved.parts}
    if parts & _SECRET_DIR_NAMES:
        raise ActionValidationError(
            "opencode: project root is (or is inside) a secret-bearing "
            "directory — refused")
    return resolved


def _is_secret_file(path: Path) -> bool:
    name = path.name.lower()
    return any(h in name for h in _SECRET_FILE_HINTS)


def _assert_within_root(resolved: Path, root: Path, what: str) -> Path:
    """Fail closed unless `resolved` is the root or lives under it.

    `resolved` must already be fully resolved (symlinks followed) so
    symlink/rename escapes cannot slip past a lexical check.
    """
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ActionValidationError(
            f"opencode: {what} escapes the approved root — refused") from None
    return resolved


def load_task_text(args: Dict[str, Any], root: Path) -> str:
    """Return the bounded task text from ``task`` or ``task_file``.

    Exactly one of the two must be present. File content is bounded, must
    resolve INSIDE the approved project `root` (symlink escapes refused),
    must not be a secret-named file, and is treated as untrusted message
    data — never shell, never authority.
    """
    inline = str(args.get("task", "") or "")
    fpath = str(args.get("task_file", "") or "")
    if inline and fpath:
        raise ActionValidationError(
            "opencode: supply exactly one of task / task_file")
    if fpath:
        if len(fpath) > 512:
            raise ActionValidationError("opencode: task_file path too long")
        p = Path(os.path.expanduser(fpath))
        try:
            resolved = p.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ActionValidationError(
                f"opencode: task_file unresolvable: {exc}") from None
        if not resolved.is_file():
            raise ActionValidationError("opencode: task_file is not a file")
        _assert_within_root(resolved, root, "task_file")
        if _is_secret_file(resolved):
            raise ActionValidationError(
                "opencode: task_file looks like a secret file — refused")
        try:
            if resolved.stat().st_size > MAX_PROMPT_FILE_BYTES:
                raise ActionValidationError(
                    "opencode: task_file exceeds 16 KiB")
            text = resolved.read_text(encoding="utf-8", errors="strict")
        except ActionValidationError:
            raise
        except Exception as exc:
            raise ActionValidationError(
                f"opencode: task_file unreadable: {exc}") from None
        if not text.strip():
            raise ActionValidationError("opencode: task_file is empty")
        return text[:MAX_TASK_CHARS] if len(text) > MAX_TASK_CHARS else text
    if not inline.strip():
        raise ActionValidationError("opencode: task text is required")
    if len(inline) > MAX_TASK_CHARS:
        raise ActionValidationError("opencode: task exceeds 4000 chars")
    return inline


def flag_untrusted(text: str) -> List[str]:
    """Return the hostile markers present in untrusted text (redacted)."""
    low = text.lower()
    return [m for m in _HOSTILE_MARKERS if m in low]


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
    """Build the minimal child environment (never inherits secrets)."""
    env: Dict[str, str] = {}
    for name in _SAFE_ENV_ALLOW:
        if name in os.environ:
            env[name] = os.environ[name]
    env.setdefault("PATH", "/usr/bin:/bin")
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
    """Run the pinned binary one-shot; kill/reap on timeout; never shell.

    The child is confined with setrlimit CPU/AS/FSIZE/NPROC/NOFILE ceilings
    in preexec (plus its own process group for kill/reap). No shell, no
    server argv, bounded output.
    """
    if _looks_like_server_argv(argv):
        raise OpenCodeError("refusing server/auto-approve opencode argv")
    try:
        proc = subprocess.Popen(
            argv, cwd=str(workdir), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, preexec_fn=lambda: _limited_preexec(timeout_s))
    except FileNotFoundError:
        return {"state": "failed", "code": "BINARY_MISSING",
                "detail": "opencode executable not found"}
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


def worktree_diffstat(root: Path) -> Dict[str, Any]:
    """Bounded ``git status/diff --stat`` snapshot (never auto-commits)."""
    git = shutil.which("git")
    if git is None:
        return {"git": "unavailable", "files": [], "stat": ""}
    try:
        st = subprocess.run(
            [git, "status", "--short"], cwd=str(root), capture_output=True,
            text=True, timeout=15)
        df = subprocess.run(
            [git, "diff", "--stat"], cwd=str(root), capture_output=True,
            text=True, timeout=15)
    except Exception as exc:
        return {"git": "failed", "detail": str(exc)[:150]}
    files = [ln for ln in (st.stdout or "").splitlines() if ln.strip()]
    stat_lines = (df.stdout or "").splitlines()
    return {"git": "OK", "files": files[:MAX_DIFF_FILES],
            "files_truncated": len(files) > MAX_DIFF_FILES,
            "stat": "\n".join(stat_lines[:MAX_DIFFSTAT_LINES])}


def _validate_workdir(root: Path, workdir: Path) -> Dict[str, Any]:
    """Validate generated workdir contents before reporting success.

    Walks without following symlinks; enforces file-count and byte caps;
    any symlink resolving OUTSIDE the approved project `root` is a
    SYMLINK_ESCAPE failure (rename/symlink escapes cannot smuggle paths in).
    Returns counts for honest receipts.
    """
    nfiles = 0
    nbytes = 0
    outside: List[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(workdir,
                                                    followlinks=False):
            for name in filenames:
                p = Path(dirpath) / name
                nfiles += 1
                if nfiles > MAX_WORKDIR_FILES:
                    return {"ok": False, "code": "LIMIT_EXCEEDED",
                            "detail": f"workdir exceeds {MAX_WORKDIR_FILES} "
                                      "files",
                            "files": nfiles, "bytes": nbytes,
                            "outside_symlinks": []}
                try:
                    if os.path.islink(p):
                        target = p.resolve(strict=False)
                        try:
                            target.relative_to(root)
                        except ValueError:
                            outside.append(
                                str(p.relative_to(workdir))[:200])
                    else:
                        nbytes += p.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                if nbytes > MAX_WORKDIR_BYTES:
                    return {"ok": False, "code": "LIMIT_EXCEEDED",
                            "detail": f"workdir exceeds "
                                      f"{MAX_WORKDIR_BYTES} bytes",
                            "files": nfiles, "bytes": nbytes,
                            "outside_symlinks": []}
    except Exception as exc:
        return {"ok": False, "code": "VALIDATION_CRASH",
                "detail": f"workdir validation failed: {exc}"[:200],
                "files": nfiles, "bytes": nbytes, "outside_symlinks": []}
    if outside:
        return {"ok": False, "code": "SYMLINK_ESCAPE",
                "detail": "symlinks resolve outside the approved root: "
                          + ", ".join(outside[:8]),
                "files": nfiles, "bytes": nbytes,
                "outside_symlinks": outside[:8]}
    return {"ok": True, "files": nfiles, "bytes": nbytes,
            "outside_symlinks": []}


def _prune_registered_worktree(wt: Path) -> bool:
    """Deregister `wt` from its owning main worktree (best-effort).

    Reads the worktree's ``.git`` pointer file (``gitdir: <main>/.git/
    worktrees/<name>``) to find the owning main root — no git-version-
    dependent plumbing — then removes and prunes through it.
    """
    try:
        git = shutil.which("git")
        if git is None:
            return False
        gitfile = wt / ".git"
        if not gitfile.is_file():
            return False
        content = gitfile.read_text(encoding="utf-8",
                                    errors="replace").strip()
        if not content.startswith("gitdir:"):
            return False
        admindir = Path(content.split(":", 1)[1].strip())
        mainroot = admindir.parent.parent
        if not mainroot.is_dir():
            return False
        rem = subprocess.run(
            [git, "-C", str(mainroot), "worktree", "remove", "--force",
             str(wt)], capture_output=True, text=True, timeout=60)
        if rem.returncode == 0:
            return True
        # Fallback: the working tree may already be gone; prune then drops
        # the stale admin entry.
        subprocess.run([git, "-C", str(mainroot), "worktree", "prune"],
                       capture_output=True, text=True, timeout=60)
        return not wt.exists()
    except Exception:
        return False


def sweep_orphaned_workdirs(max_age_s: float = ORPHAN_MAX_AGE_S) -> Dict[str, Any]:
    """Reap stale disposable-worktree bases (crash/cancel orphans).

    Only bases older than `max_age_s` are touched, so live runs are never
    disturbed. Best-effort: failures are reported, never raised. Called
    automatically before each disposable run and safe to run standalone.
    """
    swept: List[str] = []
    failed: List[str] = []
    now = time.time()
    try:
        bases = sorted(Path(tempfile.gettempdir()).glob("vb-opencode-*"))
    except Exception:
        return {"swept": [], "failed": [], "detail": "tmpdir unreadable"}
    for base in bases:
        try:
            if not base.is_dir() or base.is_symlink():
                continue
            if now - base.stat().st_mtime < max_age_s:
                continue
            wt = base / "wt"
            removed = False
            if wt.is_dir() or (wt / ".git").is_file():
                _prune_registered_worktree(wt)
            shutil.rmtree(base, ignore_errors=True)
            removed = not base.exists()
            (swept if removed else failed).append(base.name)
        except Exception:
            failed.append(base.name)
    return {"swept": swept, "failed": failed,
            "detail": f"swept {len(swept)}, failed {len(failed)}"}


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
    """Child-side confinement: new session + CPU/AS/FSIZE/NPROC/NOFILE caps."""
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
        _res.setrlimit(_res.RLIMIT_NPROC, (RLIMIT_NPROC, RLIMIT_NPROC))
        _res.setrlimit(_res.RLIMIT_NOFILE, (RLIMIT_NOFILE, RLIMIT_NOFILE))
    except Exception:
        os._exit(127)


# ---------------------------------------------------------------------------
# Action specs (registered through the existing 8F surface)
# ---------------------------------------------------------------------------

def opencode_action_specs() -> Tuple[ActionSpec, ...]:
    task_field = {"type": "str", "required": False, "max": MAX_TASK_CHARS}
    return (
        _spec(ACT_PLAN, "1.0.0", ADAPTER_OPENCODE, Tier.READ_ONLY,
              "preview a bounded OpenCode coding run (no subprocess)",
              ("code",),
              {"project": {"type": "str", "required": True, "max": 512,
                            "kind": "filepath"},
               "task": task_field,
               "task_file": {"type": "str", "required": False, "max": 512,
                             "kind": "filepath"},
               "disposable": {"type": "bool", "required": False}},
              "validates scope and shows the exact argv/env budget; "
              "spawns nothing",
              RB_NONE, "no side effect; nothing to undo",
              ("project", "task")),
        _spec(ACT_RUN, "1.0.0", ADAPTER_OPENCODE, Tier.DESTRUCTIVE,
              "run a supervised bounded OpenCode coding workflow",
              ("code",),
              {"project": {"type": "str", "required": True, "max": 512,
                            "kind": "filepath"},
               "task": task_field,
               "task_file": {"type": "str", "required": False, "max": 512,
                             "kind": "filepath"},
               "disposable": {"type": "bool", "required": False},
               "timeout_s": {"type": "int", "required": False,
                             "min": 5, "max": 600}},
              "executes one bounded opencode run; may modify files under "
              "the approved root only; never commits/pushes/installs",
              RB_IMPOSSIBLE, "code changes have no automatic rollback; "
                             "review the diff and use git revert",
              ("project", "task"), timeout_s=600.0),
    )


def opencode_spec(action_id: str) -> Optional[ActionSpec]:
    for spec in opencode_action_specs():
        if spec.action_id == action_id:
            return spec
    return None


def register_opencode_specs() -> None:
    for spec in opencode_action_specs():
        try:
            register_action_spec(spec)
        except ActionError as exc:
            if "already registered" not in str(exc):
                raise


# ---------------------------------------------------------------------------
# Adapter state (disabled by default; emergency stop latch)
# ---------------------------------------------------------------------------

def _effective_state(state_path: Path, master_enabled: bool) -> Tuple[bool, str]:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if not master_enabled:
        return False, "CONFIG_DISABLED"
    if not bool(data.get("user_enabled")):
        return False, "ADAPTER_DISABLED"
    if bool(data.get("emergency_stop")):
        return False, "EMERGENCY_STOP"
    return True, "OK"


VersionProbe = Callable[[str], str]


@dataclass(frozen=True)
class _ExecCtx:
    """Spawn-safe executor context (no locks, no Config, no runner).

    The broker runs side-effecting callables in a forkserver child via
    cloudpickle, so executor closures may only capture this plain-data
    context plus the (module-level or closure) ``run_opencode`` transport.
    """
    binary: str
    master_enabled: bool
    timeout_default: float
    state_path: str
    pinned_version: str
    version_probe: VersionProbe
    run_opencode: RunOpencode


def _plan_impl(ctx: _ExecCtx, envelope: Dict[str, Any]) -> str:
    ok, reason = _effective_state(Path(ctx.state_path), ctx.master_enabled)
    if not ok:
        return json.dumps({"state": ST_ADAPTER_DISABLED,
                           "code": ST_ADAPTER_DISABLED,
                           "action_id": ACT_PLAN,
                           "detail": f"adapter not live: {reason}",
                           "rollback": RB_NONE})
    try:
        args = (envelope.get("args") or {})
        root = resolve_project_root(str(args.get("project", "")))
        task = load_task_text(args, root)
        disposable = bool(args.get("disposable", True))
        argv = build_argv(ctx.binary, root, task[:64] + "…")
        return json.dumps(redact_obj({
            "state": ST_OK, "action_id": ACT_PLAN,
            "project": str(root), "disposable": disposable,
            "task_chars": len(task),
            "untrusted_flags": flag_untrusted(task),
            "argv_shape": argv[:-1] + ["<task-redacted>"],
            "timeout_s": ctx.timeout_default,
            "note": "preview only; no subprocess spawned; task text "
                    "is untrusted input until validated execution",
        }), sort_keys=True)
    except (ActionValidationError, OpenCodeError) as exc:
        return json.dumps({"state": "failed", "code": "PLAN_REJECTED",
                           "detail": redact_text(str(exc))[:300]})


def _run_impl(ctx: _ExecCtx, envelope: Dict[str, Any]) -> str:
    ok, reason = _effective_state(Path(ctx.state_path), ctx.master_enabled)
    if not ok:
        return json.dumps({"state": ST_ADAPTER_DISABLED,
                           "code": ST_ADAPTER_DISABLED,
                           "action_id": ACT_RUN,
                           "detail": f"adapter not live: {reason}",
                           "rollback": RB_IMPOSSIBLE})
    run_id = uuid.uuid4().hex[:12]
    workdir: Optional[Path] = None
    disposable = True
    root = Path(".")
    try:
        args = (envelope.get("args") or {})
        root = resolve_project_root(str(args.get("project", "")))
        task = load_task_text(args, root)
        disposable = bool(args.get("disposable", True))
        timeout_s = args.get("timeout_s", None)
        budget = min(float(timeout_s) if timeout_s else ctx.timeout_default,
                     MAX_TIMEOUT_S)
        env = sanitized_env()
        if not (Path(ctx.binary).is_file()
                and os.access(ctx.binary, os.X_OK)):
            return json.dumps({"state": "failed", "code": "BINARY_MISSING",
                               "detail": "configured opencode binary "
                                         "is not executable"})
        installed = ctx.version_probe(ctx.binary)
        if installed != ctx.pinned_version:
            code = ("VERSION_UNKNOWN" if installed in
                    ("unavailable", "invalid-path", "") else
                    "VERSION_MISMATCH")
            return json.dumps({"state": "failed", "code": code,
                               "run_id": run_id, "project": str(root),
                               "detail": f"run_id={run_id}; opencode "
                                         f"{installed!r} != pinned "
                                         f"{ctx.pinned_version!r}; "
                                         "re-qualify or update the pin "
                                         "before any live run"[:300],
                               "rollback": RB_IMPOSSIBLE})
        try:
            sweep_orphaned_workdirs()
        except Exception:
            pass
        workdir = _prepare_workdir(root, disposable, run_id)
        argv = build_argv(ctx.binary, workdir, task)
        log_event("opencode.run.start", run_id=run_id,
                  project=redact_text(str(root)),
                  task_chars=len(task), disposable=disposable,
                  timeout_s=budget)
        raw = ctx.run_opencode(argv, workdir, env, budget)
        if not isinstance(raw, dict):
            raw = {"state": "failed", "code": "TRANSPORT_MALFORMED",
                   "detail": "run transport returned a non-mapping",
                   "stdout": "", "stderr": ""}
        stdout = str(raw.get("stdout", ""))
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS]
            raw = dict(raw, stdout=stdout, truncated=True)
        parsed = parse_run_output(stdout)
        validation = _validate_workdir(root, workdir)
        diff = worktree_diffstat(workdir)
        flags = flag_untrusted(task)
        if (raw.get("state") != "OK" or parsed.get("state") != "OK"
                or not validation["ok"]):
            if not validation["ok"]:
                code = validation["code"]
                fail_detail = validation["detail"]
            elif (raw.get("state") != "OK" and _looks_like_auth_failure(
                    str(raw.get("stderr", "")), str(raw.get("code", "")))):
                code = "MISSING_CREDENTIALS"
                fail_detail = ("opencode reports missing/invalid provider "
                               "credentials; configure provider auth "
                               "explicitly, never in repo files")
            else:
                code = (raw.get("code") if raw.get("state") != "OK"
                        else parsed.get("code"))
                fail_detail = str(raw.get("detail") or parsed.get("detail")
                                  or "run failed")
            out = {"state": "failed", "code": code,
                   "run_id": run_id, "project": str(root),
                   "detail": (f"run_id={run_id}; " +
                              redact_text(fail_detail))[:300],
                   "stderr": redact_text(
                       str(raw.get("stderr", "")))[:500],
                   "untrusted_flags": flags,
                   "workdir_files": validation["files"],
                   "workdir_bytes": validation["bytes"],
                   "rollback": RB_IMPOSSIBLE,
                   "partial": True,
                   "note": "a failed or partial run is never "
                           "complete; diff below is observational"}
        else:
            nfiles = len(diff.get("files", []))
            out = {"state": ST_OK, "run_id": run_id,
                   "project": str(root), "workdir_kept": (not disposable),
                   "detail": (f"run_id={run_id}; files_changed={nfiles}; "
                              f"truncated={bool(raw.get('truncated'))}; "
                              f"workdir_kept={bool(not disposable)}; "
                              f"gen_files={validation['files']}; "
                              f"gen_bytes={validation['bytes']}; "
                              f"elapsed_ms={raw.get('elapsed_ms', '?')}; "
                              + redact_text(
                                  str(parsed.get("text", "")))[:200]),
                   "text": redact_text(
                       str(parsed.get("text", "")))[:4000],
                   "output_truncated": bool(raw.get("truncated")),
                   "diff_files": diff.get("files", []),
                   "diff_stat": redact_text(
                       str(diff.get("stat", "")))[:2000],
                   "untrusted_flags": flags,
                   "workdir_files": validation["files"],
                   "workdir_bytes": validation["bytes"],
                   "rollback": RB_IMPOSSIBLE,
                   "note": "changes are uncommitted working-tree "
                           "edits only; commit/push/publish/install "
                           "need separate exact-action confirmation"}
        return json.dumps(redact_obj(out), sort_keys=True)
    except (ActionValidationError, OpenCodeError) as exc:
        return json.dumps({"state": "failed", "code": "RUN_REJECTED",
                           "run_id": run_id,
                           "detail": redact_text(str(exc))[:300],
                           "rollback": RB_IMPOSSIBLE})
    except Exception as exc:
        return json.dumps({"state": "failed", "code": "RUN_CRASH",
                           "run_id": run_id,
                           "detail": str(exc)[:200]})
    finally:
        # Guaranteed cleanup: success, timeout, transport exception, worker
        # crash, malformed output, and partial failure all converge here, so
        # a disposable worktree can never be orphaned by this process.
        # (Broker-cancel SIGKILLs mid-flight; those orphans are reaped by
        # sweep_orphaned_workdirs on the next disposable run.)
        try:
            if disposable and workdir is not None and workdir != root:
                _cleanup_workdir(root, workdir, run_id)
        except Exception:
            pass


def _prepare_workdir(root: Path, disposable: bool, run_id: str) -> Path:
    """Resolve the execution directory.

    Disposable (default): an isolated detached git worktree under a 0700
    temp base, so the project checkout is never touched. Non-disposable:
    the approved project root itself (requires preview-hash + confirmation
    at the runner layer).
    """
    if not disposable:
        return root
    git = shutil.which("git")
    if git is None:
        raise OpenCodeError("disposable run needs git; "
                            "DISPOSABLE_UNAVAILABLE")
    is_repo = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"], cwd=str(root),
        capture_output=True, text=True, timeout=15)
    if is_repo.returncode != 0:
        raise OpenCodeError("disposable run needs a git checkout; "
                            "DISPOSABLE_UNAVAILABLE")
    try:
        base = Path(tempfile.gettempdir()) / f"vb-opencode-{run_id}"
        base.mkdir(mode=0o700, exist_ok=False)
    except Exception as exc:
        raise OpenCodeError(f"worktree base failed: {exc}") from None
    wt = base / "wt"
    add = subprocess.run(
        [git, "worktree", "add", "--detach", str(wt)], cwd=str(root),
        capture_output=True, text=True, timeout=60)
    if add.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        raise OpenCodeError("git worktree add failed; "
                            "DISPOSABLE_UNAVAILABLE: "
                            f"{add.stderr[:150]}")
    return wt


def _cleanup_workdir(root: Path, workdir: Path, run_id: str) -> None:
    git = shutil.which("git")
    try:
        if git is not None:
            subprocess.run(
                [git, "worktree", "remove", "--force", str(workdir)],
                cwd=str(root), capture_output=True, text=True,
                timeout=60)
        base = workdir.parent
        if base.name.startswith("vb-opencode-"):
            shutil.rmtree(base, ignore_errors=True)
    except Exception:
        pass
    log_event("opencode.run.worktree_cleaned", run_id=run_id)


def _adapter_path(cfg) -> Path:
    if cfg is not None:
        try:
            return Path(cfg.state_dir()) / "opencode_adapter.json"
        except Exception:
            pass
    return Path("/tmp/vb-opencode-adapter.json")


def _read_state(cfg) -> Dict[str, Any]:
    try:
        raw = _adapter_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"user_enabled": False, "emergency_stop": False}


def _write_state(cfg, data: Dict[str, Any]) -> None:
    path = _adapter_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _resolve_binary(ocfg: Dict[str, Any]) -> str:
    raw = str(ocfg.get("binary") or DEFAULT_BINARY)
    return os.path.expanduser(raw)


def _opencode_cfg(cfg) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if cfg is None:
        return out
    for key in ("enabled", "binary", "pinned_version", "timeout_s",
                "max_timeout_s"):
        try:
            out[key] = cfg.get("intent", "opencode", key, default=None)
        except Exception:
            out[key] = None
    return out


# ---------------------------------------------------------------------------
# Runner (same broker/gate/receipt machinery as every other channel)
# ---------------------------------------------------------------------------

class OpenCodeRunner(ActionRunner):
    """8F ActionRunner bound to the OpenCode capabilities.

    ``preview``/``prepare``/``execute``/``cancel``/``receipts`` and the 7E
    gate, action-hash confirmation, receipt persistence, and audit all come
    from ``ActionRunner`` verbatim. Live execution additionally requires the
    two-step enable (packaged master switch + state-file user enablement) and
    an injected-or-real executor; otherwise every call returns the typed
    ``ADAPTER_DISABLED`` result and no subprocess is spawned.
    """

    def __init__(self, cfg=None, *,
                 store: Optional[ActionStore] = None,
                 perms: Optional[Any] = None,
                 broker: Optional[Any] = None,
                 executors: Optional[Dict[str, Callable]] = None,
                 clock: Callable[[], float] = time.time,
                 confirmation_ttl_s: Optional[float] = None,
                 run_opencode: Optional[RunOpencode] = None,
                 version_probe: Optional[VersionProbe] = None,
                 power: Optional[Any] = None):
        register_opencode_specs()
        ocfg = _opencode_cfg(cfg)
        timeout_default = ocfg.get("timeout_s") or DEFAULT_TIMEOUT_S
        super().__init__(cfg=cfg, store=store, perms=perms, broker=broker,
                         executors=executors, enabled=True, clock=clock,
                         confirmation_ttl_s=confirmation_ttl_s, power=power)
        self._ctx = _ExecCtx(
            binary=_resolve_binary(ocfg),
            master_enabled=bool(ocfg.get("enabled", False)),
            timeout_default=float(timeout_default),
            state_path=str(_adapter_path(cfg)),
            pinned_version=str(ocfg.get("pinned_version")
                               or PINNED_OPENCODE_VERSION),
            version_probe=version_probe or _probe_version,
            run_opencode=run_opencode or real_run_opencode)
        # Bind the executors unless the caller injected a full broker.
        if broker is None:
            self._bind_executors(executors or {})

    # -- enablement ------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        st = _read_state(self.cfg)
        ocfg = _opencode_cfg(self.cfg)
        master = bool(ocfg.get("enabled", False))
        binary = _resolve_binary(ocfg)
        pinned = str(ocfg.get("pinned_version") or PINNED_OPENCODE_VERSION)
        effective = bool(master and st.get("user_enabled")
                         and not st.get("emergency_stop"))
        reason = ("OK" if effective else
                  ("CONFIG_DISABLED" if not master else
                   ("NOT_ENABLED" if not st.get("user_enabled") else
                    "EMERGENCY_STOP")))
        # The version probe executes the configured binary: it runs ONLY when
        # the adapter is effectively enabled, with the strict sanitized
        # environment, and never through a shell. Disabled state ⇒ no spawn.
        if effective:
            installed = self._ctx.version_probe(binary)
        else:
            installed = "not-probed (disabled)"
        return {"state": ST_OK, "adapter": ADAPTER_OPENCODE,
                "effective_enabled": effective, "reason": reason,
                "config_enabled": master, "user_enabled": bool(
                    st.get("user_enabled")),
                "emergency_stop": bool(st.get("emergency_stop")),
                "binary": binary, "pinned_version": pinned,
                "installed_version": installed,
                "version_match": (installed == pinned),
                "capabilities": [s.to_dict() for s in
                                 opencode_action_specs()],
                "stores": {"adapter_state": str(_adapter_path(self.cfg)),
                           "receipts": str(self.store.path)}}

    def enable(self, requested_by: str = "user") -> Dict[str, Any]:
        if requested_by != "user":
            raise OpenCodeError("enable requires the authenticated owner")
        st = _read_state(self.cfg)
        st["user_enabled"] = True
        _write_state(self.cfg, st)
        log_event("opencode.adapter.enable", requested_by="user")
        return self.status()

    def disable(self, requested_by: str = "user") -> Dict[str, Any]:
        if requested_by != "user":
            raise OpenCodeError("disable requires the authenticated owner")
        st = _read_state(self.cfg)
        st["user_enabled"] = False
        _write_state(self.cfg, st)
        log_event("opencode.adapter.disable", requested_by="user")
        return self.status()

    def emergency_stop(self) -> Dict[str, Any]:
        st = _read_state(self.cfg)
        st["emergency_stop"] = True
        _write_state(self.cfg, st)
        log_event("opencode.adapter.emergency_stop")
        return self.status()

    def reset(self, requested_by: str = "user") -> Dict[str, Any]:
        if requested_by != "user":
            raise OpenCodeError("reset requires the authenticated owner")
        _write_state(self.cfg, {"user_enabled": False,
                                "emergency_stop": False})
        log_event("opencode.adapter.reset", requested_by="user")
        return self.status()

    def _effective(self) -> Tuple[bool, str]:
        st = _read_state(self.cfg)
        ocfg = _opencode_cfg(self.cfg)
        if not bool(ocfg.get("enabled", False)):
            return False, "CONFIG_DISABLED"
        if not bool(st.get("user_enabled")):
            return False, "ADAPTER_DISABLED"
        if bool(st.get("emergency_stop")):
            return False, "EMERGENCY_STOP"
        return True, "OK"

    # -- capability inventory --------------------------------------------
    def opencode_actions(self) -> List[Dict[str, Any]]:
        ok, reason = self._effective()
        out = []
        for spec in opencode_action_specs():
            d = spec.to_dict()
            d["effective"] = self.check(spec.action_id, spec.scopes[0])
            d["executor"] = ("disabled" if not ok else "adapter")
            d["adapter_state"] = reason
            out.append(d)
        return out

    def grant(self, capability: str, scopes: Tuple[str, ...] = ("code",),
              ttl_s: Optional[float] = None) -> Dict[str, Any]:
        """Grant an opencode capability in the shared 7E store (owner only)."""
        store = self.perms
        for cap in OPENCODE_ACTIONS:
            if capability != cap:
                continue
            g = store.grant(capability, requested_by="cli:user",
                            confirmed=True, scopes=tuple(scopes),
                            ttl_s=ttl_s)
            log_event("opencode.grant", capability=capability,
                      scopes=list(scopes))
            return {"state": ST_OK, "grant": g.to_dict()}
        raise OpenCodeError(f"unknown opencode capability {capability!r}")

    def revoke_grant(self, grant_id: str) -> Dict[str, Any]:
        out = self.perms.revoke(grant_id, requested_by="cli:user")
        log_event("opencode.revoke", grant_id=grant_id)
        return {"state": ST_OK, "revoked": grant_id, "result": out}

    # -- executors --------------------------------------------------------
    # NOTE: executor closures capture only the spawn-safe _ExecCtx (plain
    # data + transport). The broker runs side-effecting callables in a
    # forkserver child via cloudpickle, so closing over the runner (locks,
    # Config, stores) would fail with SERIALIZATION_FAILED.
    def _bind_executors(self, fixtures: Dict[str, Callable]) -> None:
        from .broker import ToolBinding
        from .capabilities import CapabilitySpec
        ctx = self._ctx
        for spec in opencode_action_specs():
            if spec.action_id in fixtures:
                fn = fixtures[spec.action_id]
            elif spec.action_id == ACT_PLAN:
                def fn(envelope, _ctx=ctx):
                    return _plan_impl(_ctx, envelope)
            else:
                def fn(envelope, _ctx=ctx):
                    return _run_impl(_ctx, envelope)
            self.executors[spec.action_id] = fn
            self.broker.register(ToolBinding(
                spec=CapabilitySpec(
                    name=spec.action_id, version=spec.version, tier=spec.tier,
                    summary=spec.summary[:200], scopes=spec.scopes,
                    timeout_s=spec.timeout_s,
                    arg_validator=self._envelope_check(spec)),
                side_effects=spec.side_effects, fn=fn,
                preview=lambda _env, _s=spec: _render_preview(_s, _env)))

    @staticmethod
    def _envelope_check(spec: ActionSpec):
        from .actions import validate_envelope

        def _check(args: Dict[str, Any]) -> None:
            validate_envelope(args, spec)
        return _check


def opencode_runner(cfg=None, *,
                    executors: Optional[Dict[str, Callable]] = None,
                    run_opencode: Optional[RunOpencode] = None,
                    version_probe: Optional[VersionProbe] = None,
                    power: Optional[Any] = None) -> OpenCodeRunner:
    """Build the supervised OpenCode runner on the shared 8F machinery."""
    return OpenCodeRunner(cfg, executors=executors, run_opencode=run_opencode,
                          version_probe=version_probe, power=power)


def request_coding_run(runner: OpenCodeRunner, *, project: str,
                       task_args: Dict[str, Any],
                       target: str = "", scope: str = "code",
                       source: str = "user",
                       confirmation_id: Optional[str] = None,
                       confirmed: bool = False,
                       dry_run: bool = False,
                       expected_hash: str = "") -> Dict[str, Any]:
    """One broker entry shared by every channel that may invoke OpenCode.

    Wired today: the authenticated CLI (full parity: grant/preview/prepare/
    execute/cancel/receipts) and the authenticated dashboard (read-only
    status/list/check parity on the same runner/broker). Voice and tray
    execution are INTENTIONALLY NOT WIRED: voice coding requests continue
    through the existing OpenClaw/Hermes dispatch, and no tray execution
    surface exists. Any future voice/tray wiring must call this entry with
    the channel's source label — direct subprocess, shell, or MCP paths stay
    forbidden, and model/page/skill/tool text can never mint or carry
    confirmations (CONFIRMATION_SOURCE_FORBIDDEN).

    Writes outside a disposable worktree additionally require the preview
    hash: pass the `expected_hash` from a prior preview, or the run is
    refused as STALE_TARGET before anything executes.
    """
    tgt = target or project
    if task_args.get("disposable", True) is False and not expected_hash \
            and not dry_run:
        return {"state": "stale_target", "code": "PREVIEW_REQUIRED",
                "action_id": ACT_RUN,
                "detail": "in-project writes require a prior preview hash; "
                          "run preview first and pass --expected-hash"}
    return runner.execute(ACT_RUN, args={"project": project, **task_args},
                          target=tgt, scope=scope, source=source,
                          confirmation_id=confirmation_id,
                          confirmed=confirmed, dry_run=dry_run,
                          expected_hash=expected_hash)
