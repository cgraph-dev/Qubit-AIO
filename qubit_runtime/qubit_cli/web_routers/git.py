"""Git dashboard routes — the remote half of the desktop coding rail + review pane.

The desktop runs these as Electron-local git; over a remote gateway that's the wrong
filesystem, so they are mirrored here with the same auth gate + path hardening as
/api/fs. Logic lives in ``qubit_cli.web_git``; these are thin executor-offloaded
wrappers (git/gh can block).
"""

import asyncio
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from qubit_cli import web_git as _web_git
from qubit_cli._subprocess_compat import bounded_probe_run
from qubit_cli.web_deps import late
from qubit_cli.web_server_files import _fs_path, _managed_files_policy
from qubit_cli.web_models import (
    GitBranchSwitchBody,
    GitCommitBody,
    GitFileBody,
    GitPathBody,
    GitPrListBody,
    GitWorktreeAddBody,
    GitWorktreeRemoveBody,
)

router = APIRouter()

# Late-bound so a test's monkeypatch on the owning module wins at call time.


async def _git_op(fn, *args):
    """Run a (blocking) git op off the event loop; map a failed mutation to 400."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, fn, *args)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "git operation failed")


def _git_path(path: str) -> str:
    return str(_fs_path(path))


def _guard_git_read(request: Request, path: str, file: str | None = None) -> Path:
    """Apply the dashboard's managed-files boundary to a git read.

    A locked files root confines the repository and hides credential files from
    diffs (the same denylist as ``/api/fs``); an unlocked root keeps the old
    unrestricted behaviour. Returns the resolved repository path.
    """
    from pathlib import Path

    from qubit_cli.web_routers.files import _is_sensitive_path

    target = Path(_git_path(path))
    policy = _managed_files_policy(request, create_root=False)
    if policy.locked_root is not None:
        root = policy.locked_root.resolve()
        resolved = target.resolve()
        if resolved != root and root not in resolved.parents:
            raise HTTPException(status_code=403, detail="Path is outside the managed files root")
        if file is not None and _is_sensitive_path(Path(file)):
            raise HTTPException(status_code=403, detail="Access to sensitive files is not allowed")
    return target


def _filter_sensitive_diff(diff: str) -> str:
    """Drop per-file diff hunks for credential paths, keeping safe files intact."""
    from pathlib import Path

    from qubit_cli.web_routers.files import _is_sensitive_path

    kept: list[str] = []
    skipping = False
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            parts = line.rstrip("\n").split(" b/", 1)
            rel = parts[1] if len(parts) == 2 else ""
            skipping = bool(rel) and _is_sensitive_path(Path(rel))
        elif line.startswith("# New (untracked) files:"):
            skipping = False
        if not skipping:
            kept.append(line)
    return "".join(kept)


@router.get("/api/git/status")
async def git_status_route(path: str):
    return await _git_op(_web_git.repo_status, _git_path(path))


# Cached `gh auth status` for the desktop composer's GitHub suggestion pill. GitHub
# deliberately has NO MCP catalog entry (hosted MCP needs a per-host OAuth app; the
# gh-CLI skills are the better integration), so the pill offers `/github-auth` —
# only to users who aren't already authenticated.
_GH_AUTH_TTL_S = 300.0
_gh_auth_cache: Optional[tuple] = None  # (monotonic_ts, payload)
_gh_auth_probe_task: Optional[asyncio.Task] = None
_gh_auth_probe_started = 0.0  # monotonic start of _gh_auth_probe_task


def _probe_gh_auth() -> dict:
    gh = shutil.which("gh")
    if not gh:
        return {"available": False, "authenticated": False}
    try:
        # Exits 0 when at least one host is logged in; DEVNULL stdin guards against any prompt.
        proc = bounded_probe_run([gh, "auth", "status"], timeout=10)
        return {"available": True, "authenticated": bool(proc and proc.returncode == 0)}
    except Exception:
        return {"available": True, "authenticated": False}


def _clear_gh_auth_probe_task(completed_task: asyncio.Task) -> None:
    """Release a completed shared probe even if all requesters disconnected."""
    global _gh_auth_probe_task
    if _gh_auth_probe_task is completed_task:
        _gh_auth_probe_task = None


@router.get("/api/git/gh-auth")
async def gh_auth_status_route(refresh: bool = False):
    """``{"available", "authenticated"}`` for the `gh` CLI; cached 5 min
    (``refresh=true`` bypasses so the pill withdraws right after a login)."""
    global _gh_auth_cache, _gh_auth_probe_task, _gh_auth_probe_started
    asked = time.monotonic()
    if not refresh and _gh_auth_cache and asked - _gh_auth_cache[0] < _GH_AUTH_TTL_S:
        return _gh_auth_cache[1]
    while True:
        if _gh_auth_probe_task is None or _gh_auth_probe_task.done():
            _gh_auth_probe_task = asyncio.create_task(asyncio.to_thread(_probe_gh_auth))
            _gh_auth_probe_started = time.monotonic()
            _gh_auth_probe_task.add_done_callback(_clear_gh_auth_probe_task)
        probe_task, started = _gh_auth_probe_task, _gh_auth_probe_started
        # Shield the shared probe: disconnecting one requester must not cancel the
        # probe that other refreshes/cache misses are awaiting.
        payload = await asyncio.shield(probe_task)
        # A refresh must not accept a probe that started before it was asked for (it may predate
        # `gh auth login`, and its answer would then be cached for the full TTL): wait that one out,
        # then start or join the next. Still only one `gh` runs at a time.
        if not refresh or started >= asked:
            break
    _gh_auth_cache = (time.monotonic(), payload)
    return payload


@router.get("/api/git/worktrees")
async def git_worktrees_route(path: str):
    return {"worktrees": await _git_op(_web_git.worktree_list, _git_path(path))}


@router.get("/api/git/branches")
async def git_branches_route(path: str):
    return {"branches": await _git_op(_web_git.branch_list, _git_path(path))}


@router.get("/api/git/base-branches")
async def git_base_branches_route(path: str):
    return {"branches": await _git_op(_web_git.base_branch_list, _git_path(path))}


@router.get("/api/git/review/list")
async def git_review_list_route(path: str, scope: str = "uncommitted", base: Optional[str] = None):
    return await _git_op(_web_git.review_list, _git_path(path), scope, base)


@router.get("/api/git/review/diff")
async def git_review_diff_route(
    request: Request, path: str, file: str, scope: str = "uncommitted",
    base: Optional[str] = None, staged: bool = False
):
    target = _guard_git_read(request, path, file)
    diff = await _git_op(_web_git.review_diff, str(target), file, scope, base, staged)
    policy = _managed_files_policy(request, create_root=False)
    return {"diff": _filter_sensitive_diff(diff) if policy.locked_root is not None else diff}


@router.get("/api/git/file-diff")
async def git_file_diff_route(request: Request, path: str, file: str):
    target = _guard_git_read(request, path, file)
    diff = await _git_op(_web_git.file_diff_vs_head, str(target), file)
    policy = _managed_files_policy(request, create_root=False)
    return {"diff": _filter_sensitive_diff(diff) if policy.locked_root is not None else diff}


@router.get("/api/git/review/commit-context")
async def git_commit_context_route(request: Request, path: str):
    target = _guard_git_read(request, path)
    result = await _git_op(_web_git.review_commit_context, str(target))
    policy = _managed_files_policy(request, create_root=False)
    if policy.locked_root is not None and isinstance(result, dict) and isinstance(result.get("diff"), str):
        result = {**result, "diff": _filter_sensitive_diff(result["diff"])}
    return result


@router.get("/api/git/review/rev-parse")
async def git_rev_parse_route(path: str, ref: Optional[str] = None):
    return {"sha": await _git_op(_web_git.review_rev_parse, _git_path(path), ref)}


@router.get("/api/git/review/ship-info")
async def git_ship_info_route(path: str):
    return await _git_op(_web_git.review_ship_info, _git_path(path))


@router.post("/api/git/review/pr-list")
async def git_pr_list_route(body: GitPrListBody):
    return await _git_op(_web_git.review_pr_list, _git_path(body.path), body.branches, body.numbers)


@router.post("/api/git/review/stage")
async def git_stage_route(body: GitFileBody):
    return await _git_op(_web_git.review_stage, _git_path(body.path), body.file)


@router.post("/api/git/review/unstage")
async def git_unstage_route(body: GitFileBody):
    return await _git_op(_web_git.review_unstage, _git_path(body.path), body.file)


@router.post("/api/git/review/revert")
async def git_revert_route(body: GitFileBody):
    return await _git_op(_web_git.review_revert, _git_path(body.path), body.file)


@router.post("/api/git/review/commit")
async def git_commit_route(body: GitCommitBody):
    return await _git_op(_web_git.review_commit, _git_path(body.path), body.message, body.push)


@router.post("/api/git/review/push")
async def git_push_route(body: GitPathBody):
    return await _git_op(_web_git.review_push, _git_path(body.path))


@router.post("/api/git/review/create-pr")
async def git_create_pr_route(body: GitPathBody):
    return await _git_op(_web_git.review_create_pr, _git_path(body.path))


@router.post("/api/git/worktree/add")
async def git_worktree_add_route(body: GitWorktreeAddBody):
    options = {
        key: value
        for key, value in body.model_dump(include={"name", "branch", "base", "existingBranch"}).items()
        if value
    }
    return await _git_op(_web_git.worktree_add, _git_path(body.path), options)


@router.post("/api/git/worktree/remove")
async def git_worktree_remove_route(body: GitWorktreeRemoveBody):
    return await _git_op(
        _web_git.worktree_remove, _git_path(body.path), _git_path(body.worktreePath), body.force
    )


@router.post("/api/git/branch/switch")
async def git_branch_switch_route(body: GitBranchSwitchBody):
    return await _git_op(_web_git.branch_switch, _git_path(body.path), body.branch)
