"""OpenClaw contractual-pin handler family: managed git worktrees.

Qubit's canonical worktree surface is the confined, confirmation-gated
``qubit worktree`` lifecycle (``voicebridge/cli.py``) over the scoped
``voicebridge/git_scope`` runner, plus the read-only ``/api/git/worktrees`` and
``/api/git/branches`` projections (``voicebridge/qubit_compat.py``). These
handlers register that same real backend under the OpenClaw gateway method
names — no second worktree service, no fabricated managed snapshots.

Upstream's ``worktrees.restore`` replays a managed snapshot and
``worktrees.gc`` runs a managed-cache sweep with owner retention policy. Qubit
keeps no managed snapshot store, so those two register the typed
``SAFE_EQUIVALENT`` codes below and perform the real local counterpart (a
confined ``git worktree add`` re-materialization and ``git worktree prune``);
the manifest pairs each with a ``PINNED_SURFACE_DISPOSITIONS`` record.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from ..gateway_ws import GatewayError

WT_BAD_PARAMS = "GATEWAY_BAD_PARAMS"
WT_CONFIRM_REQUIRED = "CONFIRMATION_REQUIRED"
WT_ESTOP = "EMERGENCY_STOP"
#: SAFE_EQUIVALENT typed codes (real local counterpart, upstream mechanism
#: differs). The manifest asserts each is emitted by its registered handler.
WORKTREES_RESTORE_LOCAL_READD = "WORKTREES_RESTORE_LOCAL_READD"
WORKTREES_GC_LOCAL_PRUNE = "WORKTREES_GC_LOCAL_PRUNE"

#: Same bounded name contract as the CLI worktree lifecycle.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


# ------------------------------------------------------------------ helpers
def _wt_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return default


def _wt_confirmed(params: Dict[str, Any]) -> bool:
    return _wt_bool(params.get("confirm", params.get("confirmed", False)))


def _wt_param(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _wt_estop(cfg: Any) -> bool:
    from ... import broker as BR

    return bool(BR.estop_engaged(cfg))


def _wt_estop_refusal(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": WT_ESTOP,
            "detail": f"emergency stop latched; worktree {operation} refused"}


def _wt_resolve(cfg: Any, params: Dict[str, Any]) -> Path:
    """Resolve ``repoRoot`` strictly inside an approved project folder."""
    from ... import git_scope as GI

    root = _wt_param(params, "repoRoot", "repo_root", "root", "path")
    try:
        resolved = GI.resolve_root(cfg, root)
        GI._check_repo(resolved)
    except GI.GitError as exc:
        raise GatewayError(exc.code, exc.detail)
    return resolved


def _wt_name(params: Dict[str, Any]) -> str:
    name = _wt_param(params, "name", "id")
    if not _NAME_RE.match(name or ""):
        raise GatewayError(
            WT_BAD_PARAMS,
            "name must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    return name


def _wt_target(root: Path, name: str) -> Optional[Path]:
    """Confined ``<root>/.worktrees/<name>`` target (None when it escapes)."""
    target = root / ".worktrees" / name
    try:
        target.resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    return target


def _wt_locate(root: Path, params: Dict[str, Any]) -> Optional[Path]:
    """Locate a managed worktree by name, id, branch, or confined path."""
    from ...qubit_compat import _git_worktree_rows

    name = _wt_param(params, "name", "id")
    if name and _NAME_RE.match(name):
        target = _wt_target(root, name)
        if target is not None:
            return target
    raw = _wt_param(params, "path", "worktreePath", "worktree_path")
    if raw:
        candidate = Path(raw).expanduser()
        try:
            candidate = (candidate if candidate.is_absolute()
                         else root / candidate).resolve()
            candidate.relative_to(root)
            return candidate
        except (OSError, ValueError):
            return None
    branch = _wt_param(params, "branch")
    if branch:
        for row in _git_worktree_rows(root):
            if row.get("branch") == branch:
                return Path(str(row.get("path")))
    return None


# ----------------------------------------------------------------- handlers
def _wt_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...qubit_compat import _git_worktree_rows

    root = _wt_resolve(cfg, params)
    return {"state": "OK", "root": str(root),
            "worktrees": _git_worktree_rows(root),
            "authority": "voicebridge.qubit_compat._git_worktree_rows "
                         "(git worktree list --porcelain)"}


def _wt_branches(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...qubit_compat import (_git_default_branch, _git_ref_names,
                                 _git_worktree_rows)

    root = _wt_resolve(cfg, params)
    locals_ = _git_ref_names(root, "refs/heads")
    path_by_branch = {t["branch"]: t["path"]
                      for t in _git_worktree_rows(root) if t.get("branch")}
    trunk = _git_default_branch(root)
    local_set = set(locals_)
    remotes = [name for name in _git_ref_names(root, "refs/remotes")
               if not name.endswith("/HEAD")
               and name.split("/", 1)[-1] not in local_set]
    branches = [
        {"name": name, "checkedOut": name in path_by_branch,
         "isDefault": bool(trunk and name == trunk), "isRemote": False,
         "worktreePath": path_by_branch.get(name)} for name in locals_]
    branches += [
        {"name": name, "checkedOut": False, "isDefault": False,
         "isRemote": True, "worktreePath": None} for name in remotes]
    return {"state": "OK", "root": str(root), "branches": branches,
            "authority": "voicebridge.qubit_compat._git_ref_names "
                         "(`git for-each-ref` over heads/remotes)"}


def _wt_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import git_scope as GI

    if _wt_estop(cfg):
        return _wt_estop_refusal("create")
    root = _wt_resolve(cfg, params)
    name = _wt_name(params)
    if not _wt_confirmed(params):
        return {"state": "CONFIRMATION_REQUIRED", "code": WT_CONFIRM_REQUIRED,
                "detail": "worktree create is a mutating git operation; pass "
                          "confirm=true (explicit human authorization)"}
    target = _wt_target(root, name)
    if target is None:
        raise GatewayError(GI.SCOPE_CODE,
                           "worktree target escapes the project root")
    if target.exists():
        raise GatewayError("GIT_WORKTREE_EXISTS", f"{target} already exists")
    branch = _wt_param(params, "branch") or name
    base = _wt_param(params, "baseRef", "base_ref", "base")
    for label, value in (("branch", branch), ("base", base)):
        if value and (value.startswith("-") or ".." in value
                      or len(value) > 256):
            raise GatewayError(GI.INPUT_CODE, f"bad {label}")
    argv = ["worktree", "add", "-b", branch, str(target)]
    if base:
        argv.append(base)
    out, trunc = GI._run(root, argv)
    return {"state": "OK", "root": str(root), "path": str(target),
            "branch": branch, "detail": out, "truncated": trunc,
            "rollback": f"git worktree remove --force {target}",
            "authority": "voicebridge.git_scope._run "
                         "(`git worktree add`, confined to .worktrees/)"}


def _wt_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import git_scope as GI

    if _wt_estop(cfg):
        return _wt_estop_refusal("remove")
    root = _wt_resolve(cfg, params)
    if not _wt_confirmed(params):
        return {"state": "CONFIRMATION_REQUIRED", "code": WT_CONFIRM_REQUIRED,
                "detail": "worktree remove is a mutating git operation; pass "
                          "confirm=true (explicit human authorization)"}
    target = _wt_locate(root, params)
    if target is None:
        raise GatewayError(WT_BAD_PARAMS,
                           "unknown worktree id/name/branch/path")
    try:
        target.relative_to(root)
    except ValueError:
        raise GatewayError(GI.SCOPE_CODE,
                           "worktree target escapes the project root")
    argv = ["worktree", "remove"]
    if _wt_bool(params.get("force", False)):
        argv.append("--force")
    argv.append(str(target))
    out, trunc = GI._run(root, argv)
    prune, _ = GI._run(root, ["worktree", "prune"])
    return {"state": "OK", "root": str(root), "path": str(target),
            "removed": not target.exists(), "detail": out,
            "pruned": prune.strip()[:400], "truncated": trunc,
            "rollback": "re-create with `qubit worktree add`",
            "authority": "voicebridge.git_scope._run "
                         "(`git worktree remove` + `git worktree prune`)"}


def _wt_restore(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import git_scope as GI

    if _wt_estop(cfg):
        return _wt_estop_refusal("restore")
    root = _wt_resolve(cfg, params)
    name = _wt_name(params)
    if not _wt_confirmed(params):
        return {"state": "CONFIRMATION_REQUIRED", "code": WT_CONFIRM_REQUIRED,
                "detail": "worktree restore is a mutating git operation; pass "
                          "confirm=true (explicit human authorization)"}
    target = _wt_target(root, name)
    if target is None:
        raise GatewayError(GI.SCOPE_CODE,
                           "worktree target escapes the project root")
    if target.exists():
        raise GatewayError("GIT_WORKTREE_EXISTS", f"{target} already exists")
    branch = _wt_param(params, "branch") or name
    if branch.startswith("-") or ".." in branch or len(branch) > 256:
        raise GatewayError(GI.INPUT_CODE, "bad branch")
    out, trunc = GI._run(root, ["worktree", "add", str(target), branch])
    return {"state": "OK", "code": WORKTREES_RESTORE_LOCAL_READD,
            "root": str(root), "path": str(target), "branch": branch,
            "detail": out, "truncated": trunc,
            "qubit_counterpart": "qubit worktree add / `git worktree add`",
            "authority": "voicebridge.git_scope._run "
                         "(re-materialize the confined worktree)"}


def _wt_gc(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import git_scope as GI

    root = _wt_resolve(cfg, params)
    out, trunc = GI._run(root, ["worktree", "prune", "--verbose"])
    return {"state": "OK", "code": WORKTREES_GC_LOCAL_PRUNE,
            "root": str(root), "detail": out.strip()[:2000],
            "truncated": trunc,
            "qubit_counterpart": "`git worktree prune`",
            "authority": "voicebridge.git_scope._run "
                         "(`git worktree prune --verbose`)"}


def register(registry: Any) -> None:
    """Register the worktrees-family pinned methods on *registry*."""
    registry.register("worktrees.list", _wt_list)
    registry.register("worktrees.branches", _wt_branches)
    registry.register("worktrees.create", _wt_create)
    registry.register("worktrees.remove", _wt_remove)
    registry.register("worktrees.restore", _wt_restore)
    registry.register("worktrees.gc", _wt_gc)
