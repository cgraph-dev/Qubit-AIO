"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

An OPTIONAL, disabled-by-default adapter for explicitly requested OpenCode
coding workflows. Ordinary Qubit model routing stays in Packet 8Q; OpenCode is
used only when the owner requests a bounded coding run. OpenCode's ability to
run tools is never treated as permission for Qubit to run arbitrary shell or
edit arbitrary files.

Upstream posture (docs/archive/2026-09-21/UPSTREAM_REUSE.md R8R): DOCUMENT-ONLY reference plus
WRAP of the existing 8F action machinery. No upstream code is vendored; the
local ``opencode`` executable (pinned version below) is invoked at arm's
length as ``opencode run --format json --dir <workdir> <task>``. Never
``npx -y``, a floating package, remote instructions, or an unreviewed MCP
server.

Architecture-gate note (plan §6.2): the local WRAP is decomposed into focused
modules, each under the 300-line gate, with no new runtime dependency
(stdlib + the existing voicebridge contracts only):

- ``policy`` — pin, budgets, validation, task loading, trust flags;
- ``transport`` — sanitized env, argv builder, version probe, confined
  subprocess, output parsing;
- ``workdir`` — disposable worktrees, validation, orphan sweep, diff stats;
- ``specs`` — the two 8F action contracts;
- ``runs`` — spawn-safe executor implementations;
- ``state`` — enablement state and config access;
- ``runner`` — the broker-bound OpenCodeRunner and shared entry.

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
- Minimal sanitized environment: only the fixed allowlist
  (PATH/LANG/LC_ALL/LC_MESSAGES/TZ — no pass-through knob) reaches the
  child. Provider keys, browser cookies, desktop-session variables, HOME,
  and personal paths are never inherited; provider-side auth failures
  surface as typed ``MISSING_CREDENTIALS``.
- ``act.opencode.run`` is DESTRUCTIVE tier: project-code modification stays
  high-impact even under a power profile and always needs a fresh human
  confirmation bound to the exact action hash. Non-disposable (in-project)
  writes additionally require a prior ``preview`` hash (``expected_hash``).
- Live children run confined (``confinement`` module: Bubblewrap ro-system,
  private /tmp+HOME, approved workdir read-write; network retained for
  provider calls). No confinement on the platform means no live execution
  (typed ``CONFINEMENT_UNAVAILABLE``), never an unconfined run.
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

import subprocess

from .policy import (
    ACT_PLAN,
    ACT_RUN,
    DEFAULT_BINARY,
    DEFAULT_TIMEOUT_S,
    MAX_DIFFSTAT_LINES,
    MAX_DIFF_FILES,
    MAX_OUTPUT_CHARS,
    MAX_PROMPT_FILE_BYTES,
    MAX_TASK_CHARS,
    MAX_TIMEOUT_S,
    MAX_WORKDIR_BYTES,
    MAX_WORKDIR_FILES,
    OPENCODE_ACTIONS,
    ORPHAN_MAX_AGE_S,
    PINNED_OPENCODE_VERSION,
    RLIMIT_AS_BYTES,
    RLIMIT_FSIZE_BYTES,
    RLIMIT_NOFILE,
    RLIMIT_NPROC_FLOOR,
    RLIMIT_NPROC_MARGIN,
    SAFE_PATH,
    OpenCodeError,
    _ALLOWED_FLAGS,
    _FORBIDDEN_TOKENS,
    _HOSTILE_MARKERS,
    _RUN_SUBCOMMAND,
    _SECRET_DIR_NAMES,
    _SECRET_FILE_HINTS,
    _assert_within_root,
    _is_secret_file,
    flag_untrusted,
    load_task_text,
    resolve_project_root,
)
from .confinement import (
    BWRAP_BINARY_DEST,
    CONFINED_HOME,
    build_confinement_argv,
    confinement_available,
    find_confinement_binary,
)
from .runner import OpenCodeRunner, opencode_runner, request_coding_run
from .runs import (
    RunOpencode,
    VersionProbe,
    _ExecCtx,
    _effective_state,
    _plan_impl,
    _run_impl,
)
from .specs import opencode_action_specs, opencode_spec, register_opencode_specs
from .state import (
    _adapter_path,
    _opencode_cfg,
    _read_state,
    _resolve_binary,
    _write_state,
    owned_binary,
    owned_binary_dir,
    owned_receipt_path,
    verify_owned_binary,
)
from .transport import (
    _AUTH_FAILURE_MARKERS,
    _SAFE_ENV_ALLOW,
    _limited_preexec,
    _looks_like_auth_failure,
    _looks_like_server_argv,
    _probe_version,
    build_argv,
    parse_run_output,
    real_run_opencode,
    sanitized_env,
)
from .workdir import (
    _cleanup_workdir,
    _prepare_workdir,
    _prune_registered_worktree,
    _status_paths,
    _validate_workdir,
    sweep_orphaned_workdirs,
    worktree_diffstat,
)

__all__ = [
    "ACT_PLAN",
    "ACT_RUN",
    "BWRAP_BINARY_DEST",
    "CONFINED_HOME",
    "DEFAULT_BINARY",
    "DEFAULT_TIMEOUT_S",
    "MAX_DIFFSTAT_LINES",
    "MAX_DIFF_FILES",
    "MAX_OUTPUT_CHARS",
    "MAX_PROMPT_FILE_BYTES",
    "MAX_TASK_CHARS",
    "MAX_TIMEOUT_S",
    "MAX_WORKDIR_BYTES",
    "MAX_WORKDIR_FILES",
    "OPENCODE_ACTIONS",
    "ORPHAN_MAX_AGE_S",
    "PINNED_OPENCODE_VERSION",
    "RLIMIT_AS_BYTES",
    "RLIMIT_FSIZE_BYTES",
    "RLIMIT_NOFILE",
    "RLIMIT_NPROC_FLOOR",
    "RLIMIT_NPROC_MARGIN",
    "SAFE_PATH",
    "OpenCodeError",
    "OpenCodeRunner",
    "RunOpencode",
    "VersionProbe",
    "_ALLOWED_FLAGS",
    "_AUTH_FAILURE_MARKERS",
    "_ExecCtx",
    "_FORBIDDEN_TOKENS",
    "_HOSTILE_MARKERS",
    "_RUN_SUBCOMMAND",
    "_SAFE_ENV_ALLOW",
    "_SECRET_DIR_NAMES",
    "_SECRET_FILE_HINTS",
    "_adapter_path",
    "_assert_within_root",
    "_cleanup_workdir",
    "_effective_state",
    "_is_secret_file",
    "_limited_preexec",
    "_looks_like_auth_failure",
    "_looks_like_server_argv",
    "_opencode_cfg",
    "_plan_impl",
    "_prepare_workdir",
    "_probe_version",
    "_prune_registered_worktree",
    "_read_state",
    "_resolve_binary",
    "_run_impl",
    "_status_paths",
    "_validate_workdir",
    "_write_state",
    "owned_binary",
    "owned_binary_dir",
    "owned_receipt_path",
    "verify_owned_binary",
    "build_argv",
    "build_confinement_argv",
    "confinement_available",
    "find_confinement_binary",
    "flag_untrusted",
    "load_task_text",
    "opencode_action_specs",
    "opencode_runner",
    "opencode_spec",
    "parse_run_output",
    "real_run_opencode",
    "register_opencode_specs",
    "request_coding_run",
    "resolve_project_root",
    "sanitized_env",
    "subprocess",
    "sweep_orphaned_workdirs",
    "worktree_diffstat",
]
