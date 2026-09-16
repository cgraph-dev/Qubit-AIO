"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..actions import (
    RB_IMPOSSIBLE, RB_NONE, ST_ADAPTER_DISABLED, ST_OK, ActionValidationError,
)
from ..logging_setup import log_event
from ..redaction import redact_obj, redact_text
from .policy import (
    ACT_PLAN, ACT_RUN, MAX_OUTPUT_CHARS, MAX_TIMEOUT_S, OpenCodeError,
    flag_untrusted, load_task_text, resolve_project_root,
)
from .transport import (
    RunOpencode, _looks_like_auth_failure, build_argv, parse_run_output,
    sanitized_env,
)
from .workdir import (
    _cleanup_workdir, _prepare_workdir, _status_paths, _validate_workdir,
    sweep_orphaned_workdirs, worktree_diffstat,
)
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
        # Containment tripwire (disposable only): the project checkout itself
        # must not change — all work happens in the isolated worktree. An
        # absolute-path write outside the worktree shows up as a new dirty
        # path here. (Writes outside the repository entirely, e.g. /etc, are
        # NOT mechanically blocked: the child is not filesystem-sandboxed.
        # See the module contract: disposable worktree + confirmation + audit
        # is the boundary; live qualification must assume a hostile child
        # can touch owner-readable paths.)
        before_paths = _status_paths(root) if disposable else set()
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
        touched: List[str] = []
        if disposable:
            touched = sorted(_status_paths(root) - before_paths)[:8]
        if touched:
            code = "PROJECT_TOUCHED"
            fail_detail = ("run modified the project checkout outside the "
                           "disposable worktree: " + ", ".join(touched))
            failure_override = True
        else:
            failure_override = False
        if (failure_override or raw.get("state") != "OK"
                or parsed.get("state") != "OK"
                or not validation["ok"]):
            if touched:
                code = "PROJECT_TOUCHED"
                fail_detail = ("run modified the project checkout outside "
                               "the disposable worktree: " + ", ".join(touched))
            elif not validation["ok"]:
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


