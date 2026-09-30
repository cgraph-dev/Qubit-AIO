"""Re-exec / hand-off into the freshly-pulled Qubit update code.

By the time the fast-forward completes, this interpreter is still running the
*old* modules. Finishing the lifecycle (dependency sync, config migration,
validation, restart) on the new code is therefore done by re-executing the
updater and handing it the in-flight state through the environment, exactly the
shape the reference updater uses.

The child is built by :func:`build_handoff_argv` and launched by
:func:`spawn_handoff` — both are module-level names looked up at call time, so a
test can monkeypatch either without any risk of actually spawning a second
updater against the live repo. In a test process (and whenever the guard is set)
the hand-off is reported as skipped rather than launched.

The parent's pre-pull revision and backup directory travel in the environment
(:data:`PRE_REVISION_ENV` / :data:`BACKUP_DIR_ENV`) so the resume child can,
without re-running the dirty check, snapshot, or pull, still produce a receipt
carrying the same revisions and the backup it must roll back to on failure.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

HANDOFF_ENV = "QUBIT_UPDATE_HANDOFF"
RESUME_ENV = "QUBIT_UPDATE_RESUME"
REEXEC_ENV = "QUBIT_UPDATE_REEXEC"
HANDOFF_PID_ENV = "QUBIT_UPDATE_HANDOFF_PID"
# Hand-off state the resume child needs but must not re-derive by pulling.
BACKUP_DIR_ENV = "QUBIT_UPDATE_BACKUP_DIR"
PRE_REVISION_ENV = "QUBIT_UPDATE_PRE_REVISION"
NEW_REVISION_ENV = "QUBIT_UPDATE_NEW_REVISION"

HANDOFF_UNAVAILABLE_CODE = "UPDATE_HANDOFF_UNAVAILABLE"
HANDOFF_FAILED_CODE = "UPDATE_HANDOFF_FAILED"

# The spawner returns the child pid when it can (the default does); an injected
# test spawner may return ``None``.
Spawner = Callable[[Sequence[str], Mapping[str, str]], Any]


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default) if hasattr(cfg, "get") else default
    except Exception:  # noqa: BLE001
        return default


def _real_control_disabled() -> bool:
    """True when a real child process must NOT be spawned (test/guard)."""
    if os.environ.get("QUBIT_UPDATE_DISABLED") == "1":
        return True
    try:
        from . import qubit_runtime

        return bool(qubit_runtime.under_test())
    except Exception:  # noqa: BLE001
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def handoff_enabled(cfg: Any) -> bool:
    return bool(_get(cfg, "update", "handoff", default=True))


def is_resume_child() -> bool:
    """True when this process was launched as a post-pull hand-off child."""
    return (os.environ.get(HANDOFF_ENV) == "1"
            or bool(os.environ.get(RESUME_ENV, "").strip()))


def resume_backup_dir() -> Optional[str]:
    """The parent's pre-apply backup dir handed to a resume child, or ``None``."""
    value = str(os.environ.get(BACKUP_DIR_ENV, "") or "").strip()
    return value or None


def resume_pre_revision() -> Optional[str]:
    """The parent's pre-pull revision handed to a resume child, or ``None``."""
    value = str(os.environ.get(PRE_REVISION_ENV, "") or "").strip()
    return value or None


def resume_new_revision() -> Optional[str]:
    """The parent's post-pull revision handed to a resume child, or ``None``."""
    value = str(os.environ.get(NEW_REVISION_ENV, "") or "").strip()
    return value or None


def new_resume_token() -> str:
    return secrets.token_hex(16)


def build_handoff_argv(cfg: Any, *, resume_token: Optional[str] = None,
                       repo_root: Any = None) -> list:
    """The child argv: ``python -m voicebridge update --apply --json``.

    The resume token travels in the environment (:data:`RESUME_ENV`), not the
    argv, so a token never appears in a process listing.
    """
    return [sys.executable, "-m", "voicebridge", "update", "--apply", "--json"]


def handoff_env(resume_token: Optional[str], *,
                repo_root: Any = None, backup_dir: Any = None,
                pre_revision: Any = None,
                new_revision: Any = None) -> Dict[str, str]:
    """Environment for the hand-off child: resume flag, parent lock pid, and the
    parent's pre/post revisions + backup dir so the child can finish and, on
    failure, roll back to the exact backup the parent created."""
    env = dict(os.environ)
    env[HANDOFF_ENV] = "1"
    env[REEXEC_ENV] = "1"
    if resume_token:
        env[RESUME_ENV] = str(resume_token)
    env.setdefault(HANDOFF_PID_ENV, str(os.getpid()))
    if repo_root is not None:
        env["QUBIT_UPDATE_REPO"] = str(repo_root)
    if backup_dir:
        env[BACKUP_DIR_ENV] = str(backup_dir)
    if pre_revision:
        env[PRE_REVISION_ENV] = str(pre_revision)
    if new_revision:
        env[NEW_REVISION_ENV] = str(new_revision)
    return env


def spawn_handoff(argv: Sequence[str], env: Mapping[str, str]) -> Optional[int]:
    """Default spawner: a detached, environment-injected child process.

    Returns the child pid so the parent can hand the update-lock marker to it
    (no window where neither process owns the lock).
    """
    proc = subprocess.Popen(list(argv), env=dict(env), cwd=None,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    return proc.pid


def attempt_handoff(cfg: Any, *, resume_token: Optional[str] = None,
                    runner: Any = None, repo_root: Any = None,
                    backup_dir: Any = None, pre_revision: Any = None,
                    new_revision: Any = None,
                    spawner: Optional[Spawner] = None) -> Dict[str, Any]:
    """Launch the post-pull hand-off, or report why it was not launched.

    Returns ``{"spawned", "adopted", "state", "code", "resume_token", "argv",
    "pid", "child_pid", "detail"}``. ``pid`` (and its ``child_pid`` alias) is
    the new process id (or ``None`` when the injected spawner does not report
    one) so the parent can hand the update-lock marker to it. A process already
    running as the resume child is reported as ``adopted`` and never re-execs
    (that would recurse). Never raises.
    """
    token = str(resume_token or os.environ.get(RESUME_ENV, "") or "")
    if token or is_resume_child():
        return {"spawned": False, "adopted": True, "state": "OK", "code": "",
                "resume_token": token or None, "argv": [], "pid": None,
                "child_pid": None,
                "detail": "already running on the pulled code (resume child)"}
    if not handoff_enabled(cfg):
        return {"spawned": False, "adopted": False, "state": "SKIPPED",
                "code": "", "resume_token": None, "argv": [], "pid": None,
                "child_pid": None,
                "detail": "hand-off disabled by update.handoff=false"}
    token = new_resume_token()
    argv = build_handoff_argv(cfg, resume_token=token, repo_root=repo_root)
    env = handoff_env(token, repo_root=repo_root, backup_dir=backup_dir,
                      pre_revision=pre_revision, new_revision=new_revision)
    active = spawner if spawner is not None else globals()["spawn_handoff"]
    # Fail closed: never launch a real updater as a side effect of a test.
    if spawner is None and _real_control_disabled():
        return {"spawned": False, "adopted": False, "state": "SKIPPED",
                "code": "", "resume_token": token, "argv": argv, "pid": None,
                "child_pid": None,
                "detail": "hand-off disabled in this environment (test guard); "
                          "continuing in-process"}
    try:
        pid = active(argv, env)
    except Exception as exc:  # noqa: BLE001
        return {"spawned": False, "adopted": False, "state": "FAILED",
                "code": HANDOFF_FAILED_CODE, "resume_token": token,
                "argv": argv, "pid": None, "child_pid": None,
                "detail": str(exc)[:300]}
    child_pid = pid if isinstance(pid, int) and pid > 0 else None
    return {"spawned": True, "adopted": False, "state": "HANDOFF", "code": "",
            "resume_token": token, "argv": argv, "pid": child_pid,
            "child_pid": child_pid,
            "detail": "handed the post-pull lifecycle to a fresh interpreter"}


__all__ = ["HANDOFF_ENV", "RESUME_ENV", "REEXEC_ENV", "HANDOFF_PID_ENV",
           "BACKUP_DIR_ENV", "PRE_REVISION_ENV", "NEW_REVISION_ENV",
           "HANDOFF_UNAVAILABLE_CODE", "HANDOFF_FAILED_CODE", "handoff_enabled",
           "is_resume_child", "resume_backup_dir", "resume_pre_revision",
           "resume_new_revision", "new_resume_token", "build_handoff_argv",
           "handoff_env", "spawn_handoff", "attempt_handoff"]
