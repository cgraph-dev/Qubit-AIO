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
  kill/reap, and redacted receipts. Voice, dashboard, CLI (and future tray)
  share the same ``OpenCodeRunner``/broker entry — no second listener.
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
DEFAULT_TIMEOUT_S = 300.0
MAX_TIMEOUT_S = 600.0

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


def load_task_text(args: Dict[str, Any]) -> str:
    """Return the bounded task text from ``task`` or ``task_file``.

    Exactly one of the two must be present. File content is bounded, treated
    as untrusted, and never validated as shell (it is message data, not argv).
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

_BASE_ENV_ALLOW = ("PATH", "LANG", "LC_ALL")


def sanitized_env_names(extra: List[str]) -> Dict[str, str]:
    """Build the minimal child environment from explicit extra names."""
    env: Dict[str, str] = {}
    for name in list(_BASE_ENV_ALLOW) + [str(x) for x in extra]:
        low = name.lower()
        if any(s in low for s in ("key", "token", "secret", "cookie", "auth",
                                  "password", "credential", "session",
                                  "display", "wayland", "xdg_runtime",
                                  "dbus", "ssh")):
            raise OpenCodeError(
                f"refusing secret/session env passthrough: {name!r}")
        if name in os.environ:
            env[name] = os.environ[name]
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


def sanitized_env(cfg=None) -> Dict[str, str]:
    """Build the minimal child environment (never inherits secrets).

    Only PATH/LANG/LC_ALL plus operator-configured ``intent.opencode.pass_env``
    names are copied. Anything secret-bearing in the extra list
    (key/token/secret/cookie/auth) is refused at configuration time.
    """
    extra: Tuple[str, ...] = ()
    if cfg is not None:
        try:
            extra = tuple(cfg.get("intent", "opencode", "pass_env",
                                  default=()) or ())
        except Exception:
            extra = ()
    return sanitized_env_names([str(x) for x in extra])


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
    """Run the pinned binary one-shot; kill/reap on timeout; never shell."""
    if _looks_like_server_argv(argv):
        raise OpenCodeError("refusing server/auto-approve opencode argv")
    try:
        proc = subprocess.Popen(
            argv, cwd=str(workdir), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, text=True)
    except FileNotFoundError:
        return {"state": "failed", "code": "BINARY_MISSING",
                "detail": "opencode executable not found"}
    except Exception as exc:
        return {"state": "failed", "code": "SPAWN_FAILED",
                "detail": str(exc)[:200]}
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
                "stderr": (err or "")[:2000]}
    finally:
        try:
            proc.wait(timeout=0)
        except Exception:
            pass
    return {"state": "OK" if code == 0 else "failed",
            "code": "OK" if code == 0 else f"EXIT_{code}",
            "stdout": (out or "")[:MAX_OUTPUT_CHARS],
            "truncated": len(out or "") > MAX_OUTPUT_CHARS,
            "stderr": (err or "")[:2000]}


def parse_run_output(stdout: str) -> Dict[str, Any]:
    """Parse ``--format json`` event stream (bounded); malformed is typed."""
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
    if not texts and stdout.strip():
        # Not JSON-events: treat as raw text (older format), still bounded.
        texts = [stdout.strip()[:MAX_OUTPUT_CHARS]]
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
    return {"git": "OK", "files": files[:50], "files_truncated": len(files) > 50,
            "stat": "\n".join(stat_lines[:MAX_DIFFSTAT_LINES])}


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
    pass_env: Tuple[str, ...]
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
        task = load_task_text(args)
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
    try:
        args = (envelope.get("args") or {})
        root = resolve_project_root(str(args.get("project", "")))
        task = load_task_text(args)
        disposable = bool(args.get("disposable", True))
        timeout_s = args.get("timeout_s", None)
        budget = min(float(timeout_s) if timeout_s else ctx.timeout_default,
                     MAX_TIMEOUT_S)
        try:
            env = sanitized_env_names(list(ctx.pass_env))
        except OpenCodeError as exc:
            return json.dumps({"state": "failed",
                               "code": "MISSING_CREDENTIALS",
                               "detail": str(exc)[:200]})
        if not (Path(ctx.binary).is_file()
                and os.access(ctx.binary, os.X_OK)):
            return json.dumps({"state": "failed", "code": "BINARY_MISSING",
                               "detail": "configured opencode binary "
                                         "is not executable"})
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
        diff = worktree_diffstat(workdir)
        if disposable:
            _cleanup_workdir(root, workdir, run_id)
        flags = flag_untrusted(task)
        if raw.get("state") != "OK" or parsed.get("state") != "OK":
            code = (raw.get("code") if raw.get("state") != "OK"
                    else parsed.get("code"))
            out = {"state": "failed", "code": code,
                   "run_id": run_id, "project": str(root),
                   "detail": (f"run_id={run_id}; " + redact_text(str(
                       raw.get("detail") or parsed.get("detail")
                       or "run failed")))[:300],
                   "stderr": redact_text(
                       str(raw.get("stderr", "")))[:500],
                   "untrusted_flags": flags,
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
                              + redact_text(
                                  str(parsed.get("text", "")))[:200]),
                   "text": redact_text(
                       str(parsed.get("text", "")))[:4000],
                   "output_truncated": bool(raw.get("truncated")),
                   "diff_files": diff.get("files", []),
                   "diff_stat": redact_text(
                       str(diff.get("stat", "")))[:2000],
                   "untrusted_flags": flags,
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
                "max_timeout_s", "pass_env"):
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
                 power: Optional[Any] = None):
        register_opencode_specs()
        ocfg = _opencode_cfg(cfg)
        timeout_default = ocfg.get("timeout_s") or DEFAULT_TIMEOUT_S
        super().__init__(cfg=cfg, store=store, perms=perms, broker=broker,
                         executors=executors, enabled=True, clock=clock,
                         confirmation_ttl_s=confirmation_ttl_s, power=power)
        pass_env = tuple(str(x) for x in (ocfg.get("pass_env") or ()))
        self._ctx = _ExecCtx(
            binary=_resolve_binary(ocfg),
            master_enabled=bool(ocfg.get("enabled", False)),
            timeout_default=float(timeout_default),
            state_path=str(_adapter_path(cfg)),
            pass_env=pass_env,
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
        installed = self._binary_version(binary)
        effective = bool(master and st.get("user_enabled")
                         and not st.get("emergency_stop"))
        reason = ("OK" if effective else
                  ("CONFIG_DISABLED" if not master else
                   ("NOT_ENABLED" if not st.get("user_enabled") else
                    "EMERGENCY_STOP")))
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

    def _binary_version(self, binary: str) -> str:
        try:
            proc = subprocess.run(
                [binary, "--version"], capture_output=True, text=True,
                timeout=10)
            return (proc.stdout or proc.stderr or "").strip()[:64]
        except Exception:
            return "unavailable"

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
                    power: Optional[Any] = None) -> OpenCodeRunner:
    """Build the supervised OpenCode runner on the shared 8F machinery."""
    return OpenCodeRunner(cfg, executors=executors, run_opencode=run_opencode,
                          power=power)


def request_coding_run(runner: OpenCodeRunner, *, project: str,
                       task_args: Dict[str, Any],
                       target: str = "", scope: str = "code",
                       source: str = "user",
                       confirmation_id: Optional[str] = None,
                       confirmed: bool = False,
                       dry_run: bool = False,
                       expected_hash: str = "") -> Dict[str, Any]:
    """One broker entry shared by voice, dashboard, CLI, and tray.

    ``source`` must be ``user``/``automation``/``model``; only the
    authenticated human channel can mint confirmations, and the broker
    refuses model-carried confirmation ids (CONFIRMATION_SOURCE_FORBIDDEN).
    No caller can reach the subprocess except through this gate.
    """
    tgt = target or project
    return runner.execute(ACT_RUN, args={"project": project, **task_args},
                          target=tgt, scope=scope, source=source,
                          confirmation_id=confirmation_id,
                          confirmed=confirmed, dry_run=dry_run,
                          expected_hash=expected_hash)
