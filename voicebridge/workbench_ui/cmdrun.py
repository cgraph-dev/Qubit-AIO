"""The one command runner the workbench's mutating endpoints use.

WHY NOT ``qubit_cli.web_git``
---------------------------------------------------------------------------
The vendored desktop git module (:mod:`qubit_runtime.qubit_cli.web_git`) is the
right *shape* for the review pane, and its parsers are reused here, but two of
its behaviours are exactly what must not happen on this listener:

* ``review_stage(None)`` and ``review_commit`` fall back to ``git add -A`` — a
  whole-tree stage. The workbench only ever stages EXPLICIT paths.
* it inherits the caller's environment and runs an unbounded ``subprocess.run``
  with no stdin isolation, so a ``fetch``/``push`` on a private remote can sit on
  a credential prompt until the socket dies, and a ``-c core.fsmonitor=…`` in the
  ambient environment survives into the child.

So the argv construction, the timeout, the closed stdin and the config isolation
are owned here (on top of the vendored runtime's own
``noninteractive_git_env`` — the single hardening source, not a second copy), and
everything is argv-only: no shell, ever.

WHAT IS FORBIDDEN
---------------------------------------------------------------------------
Any argument that redirects git's OWN repository/object/config state is refused
before anything runs: ``--git-dir``, ``--work-tree``, ``--namespace``,
``-C``/``--exec-path``/``--config-env`` and every ``-c key=value`` override. A
refused → typed :class:`CmdRefusal`, never a partially applied command. With
those excluded, ``-C <workspace>`` is the only place git can be pointed, and the
repository it operates on is the one :mod:`.workspace_scope` proved is inside a
registered root.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Wall-clock cap for a local read (status/diff/log/branch). A local git read on
#: a warm cache is milliseconds; anything near this is a hung filesystem.
READ_TIMEOUT_S = 20
#: Local MUTATION cap (stage/discard/commit/checkout/branch switch). A commit
#: can run hooks, and a hook is the user's own script — but Qubit has already
#: blanked ``core.hooksPath`` (see ``noninteractive_git_env``), so this budget is
#: for a large index write, not for arbitrary user code.
MUTATE_TIMEOUT_S = 60
#: NETWORK cap (fetch/push/ls-remote). Non-interactive by construction: a
#: credential prompt fails immediately instead of waiting out this budget.
NETWORK_TIMEOUT_S = 120

#: Stdout/stderr carried back to the UI. Both are capped; a diff of a binary
#: blob is not something the pane can paint anyway.
MAX_OUTPUT_CHARS = 200_000

EXEC_UNAVAILABLE_CODE = "WB_GIT_EXEC_UNAVAILABLE"
TIMEOUT_CODE = "WB_GIT_TIMEOUT"
FAILED_CODE = "WB_GIT_FAILED"
FORBIDDEN_ARG_CODE = "WB_GIT_ARG_FORBIDDEN"

#: Global options that move git onto a different repository, object store or
#: config than the one that was confined. Refused outright.
FORBIDDEN_PREFIXES: Tuple[str, ...] = (
    "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
    "-C", "--super-prefix", "--bare",
)
#: Options that take a value and have no legitimate use here: ``-C`` moves git
#: onto another directory (the confined one is already applied by the runner)
#: and ``-c`` injects config. Refused, not skipped.
_VALUE_OPTS = frozenset({"-C", "-c"})


class CmdRefusal(Exception):
    """A command refused before execution, with the reason the UI renders."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _clean_env() -> Dict[str, str]:
    """The non-interactive, config-isolated environment git children get.

    Delegates to the vendored runtime's own hardening
    (``qubit_cli._subprocess_compat.noninteractive_git_env``): ``GIT_TERMINAL_PROMPT=0``,
    ``GCM_INTERACTIVE=Never``, global/system config blanked, pager/editor/
    fsmonitor/hooks/askpass disabled, ``core.sshCommand`` pinned to
    ``BatchMode=yes``. Falls back to a minimal hand-built env when the runtime
    package is not importable, so a missing dependency degrades to *safe*, not to
    the caller's environment.
    """
    try:
        from qubit_cli._subprocess_compat import noninteractive_git_env

        env = noninteractive_git_env()
    except Exception:  # noqa: BLE001 - fallback is stricter, not looser
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_EDITOR": "true",
        }
    env["LC_ALL"] = "C"
    return env


def check_argv(args: Sequence[str]) -> List[str]:
    """Validate a subcommand + args list; return it as a fresh list.

    Every token is checked. A token is forbidden when it IS one of
    :data:`FORBIDDEN_PREFIXES` (with or without ``=``) or starts with
    ``--config``; an option taking a value (``-c key=value``) is refused
    outright rather than inspecting its value, because its value IS config
    injection. The subcommand itself is the first token and is never an option.
    """
    out = [str(a) for a in args]
    for token in out:
        if token in _VALUE_OPTS:
            raise CmdRefusal(
                FORBIDDEN_ARG_CODE,
                f"git argument {token!r} is not allowed: it would inject "
                f"config or move git onto another work tree")
        for bad in FORBIDDEN_PREFIXES:
            if token == bad or token.startswith(bad + "="):
                raise CmdRefusal(
                    FORBIDDEN_ARG_CODE,
                    f"git argument {token!r} is not allowed: it would point git "
                    f"at a repository, config or work tree outside the confined "
                    f"workspace")
        if token.startswith("-c") and token != "-c":
            raise CmdRefusal(
                FORBIDDEN_ARG_CODE,
                f"git argument {token!r} is not allowed: inline config could "
                f"re-enable hooks, pagers or credential prompts")
        if token.startswith("--config="):
            raise CmdRefusal(FORBIDDEN_ARG_CODE,
                             f"git argument {token!r} is not allowed")
    return out


def run(root: Path, args: Sequence[str], *, timeout: int = READ_TIMEOUT_S,
        check: bool = True, extra_env: Optional[Dict[str, str]] = None
        ) -> Tuple[int, str, str]:
    """Run ``git --no-pager -C <root> <args>``; return ``(rc, out, err)``.

    ``stdin`` is closed, ``stdout``/``stderr`` are captured and capped, and the
    environment is the hardened one. A non-zero exit raises
    :class:`CmdRefusal` with the child's stderr (redacted by the caller) unless
    ``check`` is false — a read that legitimately answers non-zero (``diff
    --quiet``, ``rev-parse --verify``) passes ``check=False``.
    """
    argv = check_argv(list(args))
    cmd = ["git", "--no-pager", "-C", str(root), *argv]
    env = _clean_env()
    if extra_env:
        # Only ever a fixed author/committer identity (see
        # ``git_repo.commit_identity_env``); the hardening keys are re-applied
        # after it so a caller cannot smuggle a config or prompt back in.
        env.update({k: v for k, v in extra_env.items()
                    if k.startswith("GIT_AUTHOR_")
                    or k.startswith("GIT_COMMITTER_")})
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
            env=env)
    except FileNotFoundError:
        raise CmdRefusal(EXEC_UNAVAILABLE_CODE,
                         "the git executable is not installed on this host; "
                         "nothing was run") from None
    except subprocess.TimeoutExpired:
        raise CmdRefusal(
            TIMEOUT_CODE,
            f"git {argv[0] if argv else ''} did not finish within {timeout}s; "
            f"nothing further was run") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise CmdRefusal(FAILED_CODE,
                         f"git could not be executed ({type(exc).__name__})") \
            from None
    out = (proc.stdout or "")[:MAX_OUTPUT_CHARS]
    err = (proc.stderr or "")[:MAX_OUTPUT_CHARS]
    if check and proc.returncode != 0:
        first = next((ln.strip() for ln in err.splitlines() if ln.strip()), "")
        raise CmdRefusal(FAILED_CODE,
                         first or f"git exited {proc.returncode}")
    return proc.returncode, out, err


def out(root: Path, args: Sequence[str], *, timeout: int = READ_TIMEOUT_S,
        extra_env: Optional[Dict[str, str]] = None) -> str:
    """Stdout of a command that must succeed."""
    return run(root, args, timeout=timeout, extra_env=extra_env)[1]


def line(root: Path, args: Sequence[str], *, timeout: int = READ_TIMEOUT_S) -> str:
    """First line of a read (``""`` when it fails; used for best-effort probes)."""
    try:
        code, text, _err = run(root, args, timeout=timeout, check=False)
    except CmdRefusal:
        return ""
    return text.splitlines()[0].strip() if code == 0 and text.strip() else ""


def ok_or_empty(root: Path, args: Sequence[str], *,
                timeout: int = READ_TIMEOUT_S) -> str:
    """Stdout when git exits 0, else ``""`` — for optional reads."""
    try:
        code, text, _err = run(root, args, timeout=timeout, check=False)
    except CmdRefusal:
        return ""
    return text if code == 0 else ""


def redact(text: Any) -> str:
    """Qubit's shared redaction, fail-soft to the string itself."""
    try:
        from ..redaction import redact_text

        return redact_text(str(text))
    except Exception:  # noqa: BLE001
        return str(text)


def failure_payload(exc: CmdRefusal, *, endpoint: str,
                    counterpart: str = "") -> Tuple[int, Dict[str, Any]]:
    """The wire shape for a refused/failed git command.

    ``TIMEOUT`` is a 504 (the request was fine, the command did not finish);
    a forbidden argument is a 400 (the caller's request is not one this endpoint
    accepts); everything else is a 502 with the child's own message.
    """
    status = {
        TIMEOUT_CODE: 504,
        FORBIDDEN_ARG_CODE: 400,
        EXEC_UNAVAILABLE_CODE: 503,
    }.get(exc.code, 502)
    return status, {
        "ok": False,
        "state": "FAILED",
        "code": exc.code,
        "endpoint": endpoint,
        "detail": redact(exc.detail)[:400],
        "owner": "qubit-policy",
        "rollback": "the command did not complete; inspect `git status` "
                    "before retrying",
        "qubit_counterpart": counterpart or (
            "CLI `qubit git status|log|diff` (read your workspace from a "
            "terminal)"),
    }


def is_executable() -> bool:
    """Whether ``git`` can be found at all (used by the preflight refusal)."""
    from shutil import which

    return bool(which("git"))


def toplevel(root: Path) -> Optional[Path]:
    """The work-tree top level of ``root`` (``None`` when it is not a repo)."""
    text = line(root, ["rev-parse", "--show-toplevel"])
    if not text:
        return None
    try:
        return Path(text).resolve()
    except (OSError, RuntimeError):
        return None
