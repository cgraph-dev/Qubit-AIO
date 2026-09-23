"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from ..actions import ActionError, ActionValidationError
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
# Process-count headroom over the live owner-process count. Applied INSIDE
# the sandbox (prlimit) rather than preexec: capping NPROC before user-
# namespace creation breaks the setup itself on busy machines (observed
# EAGAIN). Floor keeps hermetic determinism on quiet machines.
RLIMIT_NPROC_MARGIN = 128
RLIMIT_NPROC_FLOOR = 512
RLIMIT_NOFILE = 256

# Orphaned disposable-worktree sweep age (bases older than this are reaped).
ORPHAN_MAX_AGE_S = 7200.0

# Fixed safe PATH for every OpenCode child. The parent PATH is NEVER copied:
# it may contain personal directories, OpenCode directories, writable
# locations, or other user-controlled entries that would let a malicious or
# compromised binary resolution escape supervision. Both directories are
# root-owned system locations present inside the confinement sandbox.
SAFE_PATH = "/usr/bin:/bin"

# Directories that can never be a project root or workdir.
_SECRET_DIR_NAMES = frozenset({
    ".ssh", ".gnupg", ".opencode", ".config", ".local", ".password-store",
    ".aws", ".qubit",
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

