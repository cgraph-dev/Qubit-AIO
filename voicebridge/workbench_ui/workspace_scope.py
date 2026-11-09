"""Workspace-root confinement for the workbench *command* families.

WHAT THIS MODULE IS
---------------------------------------------------------------------------
The git and terminal families in ``workbench_ui_routes`` run commands and
mutate repositories. Before either of those is allowed to touch a path, the path
has to be proven to live inside a **workspace root** Qubit itself registered —
the approved project folders from :mod:`voicebridge.projects` (the same store
``qubit project``, the dashboard Projects page, the voice surfaces and
:mod:`voicebridge.git_scope` read).

Three facts define "inside", and all three are required:

1. the requested path is ABSOLUTE and resolves (symlinks followed) inside a
   registered root — ``..`` is not special-cased, the resolved real path is what
   is compared, so a symlink cannot walk out of the root either;
2. the root exists and is a directory;
3. the *git repository* that would actually be operated on — the work-tree
   top-level git reports for the path — is ALSO inside that root. A path out in
   a plain subdirectory can still be inside a repo whose top level sits ABOVE
   the root; running ``git push`` there would act on a repository nobody
   registered, so that is refused too.

FAILURE IS A TYPED REFUSAL, NEVER A FALLBACK
---------------------------------------------------------------------------
Every failure raises :class:`ScopeRefusal` with a stable ``code``. There is no
"no root given, use the active project" fallback on purpose: a silent
substitution is how a request for ``/etc`` becomes a successful command against
some other repository. A request that names no path at all is
``WB_WORKSPACE_REQUIRED`` — the caller has to say which workspace it means.

``qubit-runtime`` is NOT a workspace root
---------------------------------------------------------------------------
Only registered project folders are roots. The Qubit install checkout is not one
of them even though the listener may be running from it, so the dashboard cannot
be talked into mutating Qubit's own tree through these endpoints.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: No workspace path is longer than this (bytes of text, not on-disk name).
MAX_PATH_CHARS = 4096

#: Nothing about a workspace path may be empty, NUL-bearing, or a flag.
REQUIRED_CODE = "WB_WORKSPACE_REQUIRED"
OUTSIDE_CODE = "WB_WORKSPACE_OUTSIDE"
BAD_INPUT_CODE = "WB_WORKSPACE_BAD_INPUT"
NOT_FOUND_CODE = "WB_WORKSPACE_NOT_FOUND"
REPO_OUTSIDE_CODE = "WB_REPO_OUTSIDE_WORKSPACE"
NO_ROOTS_CODE = "WB_NO_WORKSPACE_ROOTS"
UNAVAILABLE_CODE = "WB_WORKSPACE_ROOTS_UNAVAILABLE"

#: Bound on the ``rev-parse --show-toplevel`` probe (it is a local read).
_TOPLEVEL_TIMEOUT_S = 10


class ScopeRefusal(Exception):
    """A path the workbench may not touch, with the reason the UI renders."""

    def __init__(self, code: str, detail: str, *, path: str = "",
                 roots: Optional[List[str]] = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.path = path
        self.roots = list(roots or [])


def roots(cfg: Any) -> List[Path]:
    """The registered workspace roots, resolved, existing, deduplicated.

    Read from :func:`voicebridge.files.approved_roots`, which is the same
    project-registry projection :mod:`voicebridge.git_scope` and the Files
    surface already confine to — ONE definition of "approved folder", shared
    with them, rather than a second set that could drift and let the workbench
    accept a path the CLI refuses. **A project ARCHIVED while its folders are
    still recorded is still an approved root here**, because the CLI, voice and
    the workbench's own git reads all remain able to operate there; the gate on
    a workbench WRITE is the confirmation journal, not the archived flag.

    A registry that cannot be read is a refusal: assuming "no roots" would be
    indistinguishable from a config mistake, and assuming "all roots" a hole.
    """
    from .. import files as FI

    try:
        FI.approved_roots(cfg)
    except Exception as exc:  # noqa: BLE001 - typed, not a traceback
        raise ScopeRefusal(
            UNAVAILABLE_CODE,
            f"the project registry could not be read ({type(exc).__name__}), so "
            f"no workspace root can be confirmed; nothing was run") from None
    return list(FI.approved_roots(cfg))


def root_strings(cfg: Any) -> List[str]:
    """``roots`` as text, for a refusal payload (never raises: best effort)."""
    try:
        return [str(p) for p in roots(cfg)]
    except ScopeRefusal:
        return []


def confine(cfg: Any, path: str, *, what: str = "workspace",
            allow_root: bool = True) -> Path:
    """Resolve ``path`` strictly inside a registered workspace root.

    Raises :class:`ScopeRefusal` for anything else — a relative path, a path
    that resolves outside every root, a missing path, or (when ``allow_root`` is
    false) the root itself.
    """
    text = str(path or "").strip()
    if not text or "\x00" in text or len(text) > MAX_PATH_CHARS:
        raise ScopeRefusal(
            REQUIRED_CODE,
            f"an absolute {what} path is required; the git and terminal "
            f"endpoints only run inside a registered workspace root")
    if text.startswith("-"):
        # A leading dash would be read as an option by anything downstream.
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"bad {what} path (leading '-')", path=text)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise ScopeRefusal(
            BAD_INPUT_CODE,
            f"{what} path must be absolute; got {text!r}", path=text)
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"cannot resolve {what} path ({exc})",
                           path=text) from None
    known = roots(cfg)
    if not known:
        raise ScopeRefusal(
            NO_ROOTS_CODE,
            "no workspace root is registered, so nothing can be confined to "
            "one; register a project folder with `qubit project add-folder`",
            path=str(resolved))
    inside = None
    for root in known:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        inside = root
        break
    if inside is None:
        raise ScopeRefusal(
            OUTSIDE_CODE,
            f"{what} path {str(resolved)!r} is outside every registered "
            f"workspace root; refused, nothing was run",
            path=str(resolved), roots=[str(r) for r in known])
    if not allow_root and resolved == inside:
        raise ScopeRefusal(
            BAD_INPUT_CODE,
            f"the workspace root itself is not a file path; pass a path "
            f"inside it", path=str(resolved), roots=[str(inside)])
    if not resolved.exists():
        raise ScopeRefusal(NOT_FOUND_CODE,
                           f"{what} path {str(resolved)!r} does not exist",
                           path=str(resolved), roots=[str(inside)])
    return resolved


def repo_root_of(cfg: Any, path: Path) -> Path:
    """The work-tree top level git reports for ``path``, confined to its root.

    ``path`` must already be the result of :func:`confine`. The repository that
    owns it may sit ABOVE the workspace root; a command run there would mutate a
    repo outside the registered workspace, so that is a typed refusal rather
    than a silent narrowing to a subdirectory (git would refuse anyway, but the
    reason has to be the real one).
    """
    from ..git_scope import _clean_env

    start = path if path.is_dir() else path.parent
    try:
        proc = subprocess.run(
            ["git", "--no-pager", "-C", str(start), "rev-parse",
             "--show-toplevel"],
            capture_output=True, timeout=_TOPLEVEL_TIMEOUT_S,
            env=_clean_env(), text=True)
    except (OSError, subprocess.SubprocessError):
        proc = None
    top = (proc.stdout.strip() if proc is not None and proc.returncode == 0
           else "")
    if not top:
        return path
    try:
        resolved_top = Path(top).resolve()
    except (OSError, RuntimeError):
        return path
    try:
        resolved_top.relative_to(confine(cfg, str(resolved_top),
                                         what="repository"))
    except ScopeRefusal as exc:
        if exc.code == OUTSIDE_CODE:
            raise ScopeRefusal(
                REPO_OUTSIDE_CODE,
                f"the git repository at {str(resolved_top)!r} is outside every "
                f"registered workspace root; refused, nothing was run",
                path=str(resolved_top), roots=exc.roots) from None
        raise
    return resolved_top


def git_dir(cfg: Any, path: str) -> Tuple[Path, Path]:
    """``(confined work tree top level, confined starting path)`` for a request."""
    start = confine(cfg, path, what="repository")
    return repo_root_of(cfg, start), start


def refusal_payload(exc: ScopeRefusal, *, endpoint: str) -> Dict[str, Any]:
    """The one wire shape for a confinement refusal.

    ``code`` is stable and machine-readable; ``detail`` is what the UI paints;
    ``workspace_roots`` is present whenever roots were readable so the user can
    see what the path was compared against.
    """
    out: Dict[str, Any] = {
        "ok": False,
        "state": "REFUSED",
        "code": exc.code,
        "endpoint": endpoint,
        "detail": exc.detail,
        "path": exc.path,
        "owner": "qubit-policy",
        "rollback": "nothing was run; nothing to roll back",
        "qubit_counterpart": (
            "CLI `qubit project add-folder <path>` to register the workspace, "
            "then retry"),
    }
    if exc.roots:
        out["workspace_roots"] = list(exc.roots)
    return out


def status_for(exc: ScopeRefusal) -> int:
    """HTTP status for a confinement refusal (a refusal, never a 500)."""
    return {
        REQUIRED_CODE: 400,
        BAD_INPUT_CODE: 400,
        OUTSIDE_CODE: 403,
        REPO_OUTSIDE_CODE: 403,
        NO_ROOTS_CODE: 409,
        NOT_FOUND_CODE: 404,
        UNAVAILABLE_CODE: 503,
    }.get(exc.code, 403)
