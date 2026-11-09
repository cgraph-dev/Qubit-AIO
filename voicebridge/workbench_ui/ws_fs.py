"""Filesystem-side confinement for the workbench workspace/terminal families.

:mod:`.workspace_scope` answers "may the dashboard look at this path at all?".
This module answers the narrower questions the mutating endpoints ask once the
answer was yes:

* :func:`safe_rel` — is this a path RELATIVE to a confined root, with no ``..``
  and no escaping symlink? (``git add`` takes repo-relative pathspecs; a single
  traversal component would stage something outside the workspace.)
* :func:`inside_repo` — does the resolved path stay inside the repository that
  will be operated on? (a file symlinked to another registered repo is still a
  different repository, and ``git add ../other/file`` would be a cross-repo
  write through a path that LOOKED confined.)
* :func:`is_sensitive_rel` — is this credential material? Diffs of ``.env`` and
  ``auth.json`` are not shown, not staged and not diffed, even inside a
  registered workspace.
* :func:`confine_worktree_target` — is a worktree path somewhere new may be
  created/removed: inside a registered root, never the root itself, never the
  repository's own directory.

Nothing here runs a command. It is called BEFORE the command is built.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .workspace_scope import (BAD_INPUT_CODE, OUTSIDE_CODE, ScopeRefusal,
                              confine, roots)

#: Credential basenames, copied from the dashboard's own file guard
#: (``qubit_cli/web_routers/files.py::_is_sensitive_filename``) so the workbench
#: and the Files tab hide the same set. Copied rather than imported because the
#: vendored router pulls in FastAPI at import time.
SENSITIVE_BASENAMES = frozenset({
    "auth.json", "auth.lock", "credentials", "config.yaml",
    ".anthropic_oauth.json", "google_token.json", "google_oauth_pending.json",
    "google_oauth.json", "webhook_subscriptions.json", "bws_cache.json",
    "bws_cache.enc.json", ".git-credentials", ".netrc", ".npmrc", ".pypirc",
})
#: Directories whose whole subtree is credential material.
SENSITIVE_DIR_NAMES = frozenset({"mcp-tokens", "pairing", ".ssh", ".aws",
                                 ".gnupg"})

MAX_REL_CHARS = 4096


def is_sensitive_rel(rel: str) -> bool:
    """Whether a repo-relative path names credential material."""
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p]
    if not parts:
        return False
    name = parts[-1].lower()
    if name == ".env" or name.startswith(".env.") or name == ".envrc":
        return True
    if name in SENSITIVE_BASENAMES:
        return True
    return any(p.lower() in SENSITIVE_DIR_NAMES for p in parts[:-1])


def safe_rel(root: Path, raw: str, *, what: str = "path") -> str:
    """Validate a repo-relative path; return it normalised (``/`` separated).

    Refuses absolute paths, any ``..`` component, empty input and a
    non-existent path. The RESOLVED path must stay inside ``root``, so a
    symlink inside the tree cannot be used to reach outside it.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text or len(text) > MAX_REL_CHARS or "\x00" in text:
        raise ScopeRefusal(BAD_INPUT_CODE, f"a {what} is required")
    candidate = Path(text)
    if candidate.is_absolute():
        raise ScopeRefusal(
            BAD_INPUT_CODE,
            f"{what} must be relative to the workspace root, not absolute "
            f"({text!r})")
    if ".." in candidate.parts:
        raise ScopeRefusal(
            OUTSIDE_CODE,
            f"{what} {text!r} contains '..'; only paths inside the workspace "
            f"are accepted")
    try:
        resolved = (root / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"cannot resolve {what} {text!r} ({exc})") from None
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        raise ScopeRefusal(
            OUTSIDE_CODE,
            f"{what} {text!r} resolves outside the repository "
            f"({str(resolved)!r}); refused", path=str(resolved)) from None
    if not text.endswith("/") and not resolved.exists():
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"{what} {text!r} does not exist in the workspace",
                           path=str(resolved))
    return str(rel) if str(rel) != "." else ""


def inside_repo(repo: Path, rel: str) -> Path:
    """Resolve ``rel`` against ``repo`` and prove it stays inside it."""
    try:
        resolved = (repo / rel).resolve()
        resolved.relative_to(repo)
    except ValueError:
        raise ScopeRefusal(
            OUTSIDE_CODE,
            f"path {rel!r} resolves outside the repository {str(repo)!r}; "
            f"refused") from None
    except (OSError, RuntimeError) as exc:
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"cannot resolve {rel!r} ({exc})") from None
    return resolved


def rel_from_confined(cfg: Any, path: str, *, what: str = "path") -> Dict[str, str]:
    """Map a caller path to ``{"workspace": …, "root": …, "rel": …}``.

    The path may be absolute (inside a registered root) or repo-root-relative
    when the request also named its workspace. Either way the result is the
    confined repository's top level and a rel path that stays inside it.
    """
    from .workspace_scope import git_dir

    repo, start = git_dir(cfg, path)
    base = start if start.is_dir() else start.parent
    try:
        rel = str(base.relative_to(repo))
    except ValueError:
        rel = ""
    return {"workspace": str(start), "root": str(repo),
            "rel": "" if rel == "." else rel}


def confine_target(cfg: Any, target: str, *, repo: Path,
                   what: str = "target") -> Path:
    """A path that may be CREATED or REMOVED: inside a root, not a root itself.

    Used by worktree add/remove. The path does not have to exist yet, so this
    cannot go through :func:`workspace_scope.confine` (which requires an
    existing path); it resolves the PARENT and requires that to be confined and
    a real directory.
    """
    text = str(target or "").strip()
    if not text or "\x00" in text:
        raise ScopeRefusal(BAD_INPUT_CODE, f"a {what} path is required")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"{what} path must be absolute; got {text!r}")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"cannot resolve {what} path ({exc})") from None
    known = roots(cfg)
    host_root = None
    for root in known:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        host_root = root
        break
    if host_root is None:
        raise ScopeRefusal(
            OUTSIDE_CODE,
            f"{what} path {str(resolved)!r} is outside every registered "
            f"workspace root; refused",
            path=str(resolved), roots=[str(r) for r in known])
    if resolved in known:
        raise ScopeRefusal(
            BAD_INPUT_CODE,
            f"{str(resolved)!r} is itself a workspace root and cannot be "
            f"created or removed as a worktree", path=str(resolved))
    if resolved == repo:
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"{str(resolved)!r} is the repository itself",
                           path=str(resolved))
    parent = resolved.parent
    if resolved.exists() and not resolved.is_dir():
        raise ScopeRefusal(BAD_INPUT_CODE,
                           f"{str(resolved)!r} exists and is not a directory",
                           path=str(resolved))
    if not parent.is_dir():
        raise ScopeRefusal(
            BAD_INPUT_CODE,
            f"the parent directory {str(parent)!r} does not exist; a worktree "
            f"is created inside an existing directory", path=str(resolved))
    return resolved


def list_roots(cfg: Any) -> List[Dict[str, str]]:
    """Registered roots as ``[{path, exists}]`` — the client-facing list."""
    out = []
    for root in roots(cfg):
        out.append({"path": str(root), "exists": root.is_dir()})
    return out
