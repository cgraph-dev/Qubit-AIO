"""Git + worktree family for the workbench: reads, and confirm-gated writes.

WHAT THE PORTED PAGE CALLS
---------------------------------------------------------------------------
The ported static page does NOT call ``/api/git/*`` — its only git strings are a
manual-``rm`` hint (``ui.js:15059``). The git family exists because the coding
rail and the review pane of THIS dashboard do call it
(``qubit_runtime/qubit_cli/web_routers/git.py``), and the workbench serves the
same page against the same checkout. The wire shapes below are therefore the
rail's own (quoted in :mod:`.git_format`), extended only with the confined
``root``/``workspace`` the workbench has to report.

CONFINEMENT, THE LOAD-BEARING PART
---------------------------------------------------------------------------
Every endpoint here names a workspace path and is refused with a typed code
when that path is not inside a REGISTERED workspace root
(:mod:`.workspace_scope`). The refusal happens BEFORE any command is built, so a
request for ``/etc`` or for another user's checkout runs nothing at all.

FOR THE READ ENDPOINTS this module delegates to the vendored runtime's own
read-only plumbing (``qubit_cli.web_git``) with the ALREADY-CONFINED path —
there is no second parser for status/branches when the rail has one.

FOR THE MUTATING ENDPOINTS (stage/discard/commit/checkout/push/pull/…)
---------------------------------------------------------------------------
Qubit already decided how git writes work on this listener:
``qubit_compat.h_git_write`` answers with a typed *confirmation* and a capability
grant (``git.write.<verb>``); the actual apply belongs to the brokered worktree
flow. So this family does the same thing, in the same journal:

1. validate the request's paths are inside the workspace,
2. hand it to the SHARED confirmation journal
   (:func:`voicebridge.workbench_shim.confirm_gate`) — first POST stages a 409
   with a real ``confirmation_id``, the re-issue settles it,
3. record the ``git.write.<verb>`` grant scoped to the confined repo, and answer
   ``applied: false``.

A 200 that claimed the command ran would be a lie about the repository, so no
mutation here reports one. ``push``/``pull`` additionally require a configured
remote and answer a typed refusal when there is none: the network is never the
thing that discovers a missing remote.

NOTHING STAGES THE WHOLE TREE
---------------------------------------------------------------------------
``stage`` REQUIRES ``path``. The vendored rail's ``review_stage(None)`` runs
``git add -A``; an empty request here is a 400, not a whole-repository stage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import cmdrun, git_format, git_repo, ws_fs
from .workspace_scope import (ScopeRefusal, confine, git_dir, refusal_payload,
                              repo_root_of, status_for)

ENDPOINT = "git"


# ------------------------------------------------------------------- helpers
def _refuse_scope(exc: ScopeRefusal, endpoint: str) -> Tuple[int, Dict[str, Any]]:
    return status_for(exc), refusal_payload(exc, endpoint=endpoint)


def _refuse(code: str, detail: str, *, endpoint: str, status: int = 400
            ) -> Tuple[int, Dict[str, Any]]:
    return status, {
        "ok": False,
        "state": "REFUSED",
        "code": code,
        "endpoint": endpoint,
        "detail": detail,
        "owner": "qubit-policy",
        "rollback": "nothing changed; nothing to roll back",
        "qubit_counterpart": "CLI `qubit git status|log|diff`",
    }


def _path_param(q: Dict[str, Any], b: Dict[str, Any]) -> str:
    for src in (b or {}, q or {}):
        for key in ("path", "root", "workspace", "repo"):
            value = src.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _require_repo(cfg: Any, path: str, endpoint: str
                  ) -> Tuple[Optional[Path], Optional[Path],
                             Optional[Tuple[int, Dict[str, Any]]]]:
    """``(repo, start, refusal)`` — repo confined, or the typed refusal."""
    if not path:
        return None, None, _refuse(
            "WB_WORKSPACE_REQUIRED",
            "a workspace path is required (query or body: path/root/workspace). "
            "The git endpoints only operate inside a registered workspace root.",
            endpoint=endpoint)
    try:
        repo, start = git_dir(cfg, path)
    except ScopeRefusal as exc:
        return None, None, _refuse_scope(exc, endpoint)
    if not git_repo.is_inside_work_tree(repo):
        return None, None, _refuse(
            "GIT_NOT_A_REPO",
            f"{str(repo)!r} is inside a registered workspace but is not a git "
            f"work tree; there is no repository to read",
            endpoint=endpoint, status=409,
        )
    return repo, start, None


def _repo_or_refuse(cfg: Any, path: str, endpoint: str
                    ) -> Tuple[Optional[Path], Optional[Tuple[int, Any]]]:
    """``(repo, refusal)`` — the narrowed form the handlers use."""
    repo, _start, err = _require_repo(cfg, path, endpoint)
    return repo, err


def _run_guarded(endpoint: str, fn: Callable[[], Any]) -> Tuple[int, Any]:
    """Call a git-body function and type every failure it can raise."""
    try:
        return 200, fn()
    except cmdrun.CmdRefusal as exc:
        return cmdrun.failure_payload(exc, endpoint=endpoint,
                                     counterpart="CLI `qubit git status`")
    except ScopeRefusal as exc:
        return _refuse_scope(exc, endpoint)
    except Exception as exc:  # noqa: BLE001 - never a bare 500 to the pane
        return 502, {
            "ok": False, "state": "FAILED", "code": "WB_GIT_INTERNAL",
            "endpoint": endpoint,
            "detail": f"{type(exc).__name__}: {cmdrun.redact(exc)}"[:400],
            "owner": "qubit-policy",
            "rollback": "inspect `git status` in the workspace before retrying",
        }


# ------------------------------------------------------------------- reads
def status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
           p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/git/status?path=`` → the rail's status payload."""
    start = _path_param(q, b)
    repo, err = _repo_or_refuse(cfg, start, "git/status")
    if err:
        return err
    # The route's OWN state code: callers distinguish a non-repo workspace from a
    # repository by this field, so `status_payload` alone left every caller
    # reading `state` as absent (measured: /api/git-info answered "not a
    # repository" for a real repository because the key was missing).
    payload = {**git_format.status_payload(repo, workspace=start),
               "state": "OK"}
    return _run_guarded("git/status", lambda: payload)


def log(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
        p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/git/log?path=&limit=&ref=&skip=`` → bounded commit rows."""
    repo, err = _repo_or_refuse(cfg, _path_param(q, b), "git/log")
    if err:
        return err
    limit = q.get("limit") or b.get("limit") or 30
    skip = q.get("skip") or b.get("skip") or 0
    ref = str(q.get("ref") or b.get("ref") or "HEAD").strip()
    try:
        limit_i = max(1, min(int(limit), 200))
        skip_i = max(0, int(skip))
    except (TypeError, ValueError):
        return _refuse("WB_BAD_INPUT", "limit/skip must be integers",
                       endpoint="git/log")
    if ref.startswith("-"):
        return _refuse("WB_BAD_INPUT", "ref may not start with '-'",
                       endpoint="git/log")
    if not git_repo.ref_exists(repo, ref):
        return _refuse("GIT_REF_NOT_FOUND", f"ref {ref!r} does not resolve",
                       endpoint="git/log", status=404)
    return _run_guarded("git/log",
                        lambda: git_format.log_payload(repo, limit=limit_i,
                                                       ref=ref, skip=skip_i))


def diff(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
         p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/git/diff?path=&file=&staged=&ref=`` → stat + patch."""
    repo, err = _repo_or_refuse(cfg, _path_param(q, b), "git/diff")
    if err:
        return err
    rel = str(q.get("file") or q.get("file_path") or b.get("file") or "").strip()
    staged = str(q.get("staged") or "").lower() in ("1", "true", "yes", "on")
    ref = str(q.get("ref") or "").strip()
    stat_only = str(q.get("stat_only") or "").lower() in ("1", "true", "yes")
    if ref.startswith("-") or (ref and not git_repo.ref_exists(repo, ref)):
        return _refuse("GIT_REF_NOT_FOUND", f"ref {ref!r} does not resolve",
                       endpoint="git/diff", status=404)
    try:
        clean_rel = ws_fs.safe_rel(repo, rel, what="file") if rel else ""
    except ScopeRefusal as exc:
        return _refuse_scope(exc, "git/diff")
    if clean_rel and ws_fs.is_sensitive_rel(clean_rel):
        return 403, {
            "ok": False, "state": "REFUSED", "code": "GIT_SENSITIVE_PATH",
            "endpoint": "git/diff",
            "detail": (f"{clean_rel!r} is credential material; its diff is not "
                       f"served, not staged and not shown"),
            "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back",
        }
    return _run_guarded("git/diff", lambda: git_format.diff_payload(
        repo, staged=staged, ref=ref, path=clean_rel, stat_only=stat_only))


def branches(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
             p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/git/branches?path=`` → ``{"branches": [...]}``."""
    repo, err = _repo_or_refuse(cfg, _path_param(q, b), "git/branches")
    if err:
        return err
    return _run_guarded("git/branches", lambda: {
        "ok": True, "root": str(repo),
        "branches": git_format.branch_rows(repo),
    })


def worktrees(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/git/worktrees?path=`` → ``{"worktrees": [...]}``."""
    repo, err = _repo_or_refuse(cfg, _path_param(q, b), "git/worktrees")
    if err:
        return err
    return _run_guarded("git/worktrees", lambda: {
        "ok": True, "root": str(repo),
        "worktrees": git_format.worktree_rows(repo),
    })


def rev_parse(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/git/review/rev-parse?path=&ref=`` → ``{"sha": ...}``."""
    repo, err = _repo_or_refuse(cfg, _path_param(q, b), "git/rev-parse")
    if err:
        return err
    ref = str(q.get("ref") or b.get("ref") or "HEAD").strip()
    if ref.startswith("-") or len(ref) > 200:
        return _refuse("WB_BAD_INPUT", "bad ref", endpoint="git/rev-parse")
    sha = cmdrun.line(repo, ["rev-parse", "--verify", "--quiet", ref]) or None
    return 200, {"ok": True, "root": str(repo), "ref": ref, "sha": sha}


# ---------------------------------------------------------------- mutations
#: verb -> (human verb, argv the brokered apply runs). The argv is BUILT here
#: (never taken from the request) and every path in it was validated first.
_VERBS = {
    "stage": "stage",
    "unstage": "unstage",
    "discard": "discard",
    "commit": "commit",
    "branch/switch": "checkout",
    "checkout": "checkout",
    "push": "push",
    "pull": "pull",
    "fetch": "fetch",
    "worktree/add": "worktree-add",
    "worktree/remove": "worktree-remove",
}


def _paths_param(q: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    raw = (b or {}).get("paths")
    if isinstance(raw, list) and raw:
        return [str(x) for x in raw if str(x or "").strip()]
    single = (b or {}).get("file") or (b or {}).get("path_file")
    return [str(single)] if single else []


def _grant(cfg: Any, verb: str, repo: Path, detail: str) -> Tuple[int, Dict[str, Any]]:
    """Record the ``git.write.<verb>`` capability grant for this repo."""
    try:
        from ..permissions import permissions_store

        grant = permissions_store(cfg).grant(
            f"git.write.{verb}", requested_by="dashboard:user", confirmed=True,
            scopes=(str(repo),),
            note=f"workbench git {verb}: brokered apply through the opencode "
                 f"worktree flow")
        grant_id = getattr(grant, "id", "")
    except Exception as exc:  # noqa: BLE001 - no grant, no write
        return 503, {
            "ok": False, "state": "UNAVAILABLE",
            "code": "GIT_GRANT_UNAVAILABLE",
            "endpoint": f"git/{verb}",
            "detail": (f"the git {verb} authorization could not be recorded "
                       f"({type(exc).__name__}); nothing was applied"),
            "owner": "qubit-policy",
            "rollback": "nothing changed; nothing to roll back",
        }
    return 200, {
        "ok": True,
        "state": "AUTHORIZED",
        "verb": verb,
        "root": str(repo),
        "grant_id": grant_id,
        "applied": False,
        "detail": detail,
        "qubit_counterpart": ("brokered opencode worktree apply + "
                              "`qubit permissions` grant"),
        "rollback": "nothing applied; the grant is scoped to this repo",
    }


def mutate(verb: str) -> Callable[..., Tuple[int, Any]]:
    """Build the handler for one mutating git verb.

    Kept as a factory so every verb passes the SAME sequence (confine → validate
    → shared confirm → grant) and a new verb cannot skip a step.
    """

    endpoint = "git/" + verb

    def _handler(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
        from ..workbench_shim import confirm_gate

        path = _path_param(q, b)
        repo, err = _repo_or_refuse(cfg, path, endpoint)
        if err:
            return err
        # -- per-verb validation, BEFORE anything is staged or granted -------
        if verb in ("stage", "unstage", "discard"):
            wanted = _paths_param(q, b)
            if not wanted:
                return _refuse(
                    "WB_BAD_INPUT",
                    f"git {verb} requires explicit ``paths`` (it never stages or "
                    f"discards the whole tree)", endpoint=endpoint)
            clean: List[str] = []
            for rel in wanted:
                try:
                    norm = ws_fs.safe_rel(repo, rel, what="path")
                except ScopeRefusal as exc:
                    return _refuse_scope(exc, endpoint)
                if ws_fs.is_sensitive_rel(norm):
                    return _refuse(
                        "GIT_SENSITIVE_PATH",
                        f"{norm!r} is credential material; it cannot be "
                        f"{verb}d through the dashboard", endpoint=endpoint,
                        status=403)
                clean.append(norm)
            detail = f"git {verb} of {len(clean)} path(s) in {str(repo)}"
        elif verb == "commit":
            message = str((b or {}).get("message") or "").strip()
            if not message:
                return _refuse("WB_BAD_INPUT", "commit requires a message",
                               endpoint=endpoint)
            if len(message) > 4000:
                return _refuse("WB_BAD_INPUT",
                               "commit message is limited to 4000 characters",
                               endpoint=endpoint)
            clean = []
            detail = (f"committing staged changes in {str(repo)} with message "
                      f"{message[:80]!r}")
        elif verb == "branch/switch":
            branch = str((b or {}).get("branch") or q.get("branch") or "").strip()
            if not branch or branch.startswith("-") or len(branch) > 200:
                return _refuse("WB_BAD_INPUT", "a branch name is required",
                               endpoint=endpoint)
            if not git_repo.ref_exists(repo, f"refs/heads/{branch}") and \
                    not git_repo.ref_exists(repo, branch):
                return _refuse("GIT_REF_NOT_FOUND",
                               f"branch {branch!r} does not exist in "
                               f"{str(repo)!r}", endpoint=endpoint, status=404)
            clean = [branch]
            detail = f"switching {str(repo)} to branch {branch!r}"
        elif verb in ("push", "pull", "fetch"):
            remotes = git_repo.remotes(repo)
            if not remotes:
                return 409, {
                    "ok": False, "state": "REFUSED",
                    "code": "GIT_NO_REMOTE",
                    "endpoint": endpoint,
                    "detail": (f"{str(repo)!r} has no configured remote, so "
                               f"{verb} has nowhere to go. Nothing was run."),
                    "owner": "qubit-policy",
                    "rollback": "nothing changed; nothing to roll back",
                    "qubit_counterpart": "add a remote with `git remote add`",
                }
            clean = []
            detail = f"git {verb} in {str(repo)} (remote: {', '.join(remotes[:3])})"
        elif verb == "worktree/add":
            name = str((b or {}).get("name") or (b or {}).get("branch") or "").strip()
            if not name or name.startswith("-") or "/" in name or len(name) > 120:
                return _refuse("WB_BAD_INPUT",
                               "worktree add requires a name (no '/', not an "
                               "option)", endpoint=endpoint)
            target = str((b or {}).get("path_dir") or
                         (b or {}).get("worktree_path") or "").strip()
            if target:
                try:
                    ws_fs.confine_target(cfg, target, repo=repo,
                                         what="worktree")
                except ScopeRefusal as exc:
                    return _refuse_scope(exc, endpoint)
            clean = [name]
            detail = f"creating a worktree of {str(repo)} named {name!r}"
        elif verb == "worktree/remove":
            target = str((b or {}).get("worktree_path")
                         or (b or {}).get("target") or "").strip()
            if not target:
                return _refuse("WB_BAD_INPUT",
                               "worktree remove requires worktree_path",
                               endpoint=endpoint)
            try:
                ws_fs.confine_target(cfg, target, repo=repo, what="worktree")
            except ScopeRefusal as exc:
                return _refuse_scope(exc, endpoint)
            clean = [target]
            detail = f"removing the worktree at {target!r}"
        else:  # pragma: no cover - a verb added to _VERBS without a validator
            return _refuse("WB_CAPABILITY_UNBOUND",
                           f"git {verb} has no validator", endpoint=endpoint,
                           status=501)
        # -- shared confirm, then the scoped grant ---------------------------
        staged = confirm_gate(cfg, f"git.{verb}:{repo}", detail, {}, b or {},
                              {"verb": verb, "root": str(repo),
                               "paths": clean})
        if staged is not None:
            payload = staged[1]
            payload.setdefault("endpoint", endpoint)
            payload.setdefault("verb", verb)
            payload.setdefault("root", str(repo))
            return staged[0], payload
        code, out = _grant(cfg, verb, repo, detail)
        if code == 200:
            out["paths"] = clean
            out["workspace"] = path
        return code, out

    _handler.__name__ = "git_" + verb.replace("/", "_")
    return _handler


# Convenience names the route table uses.
stage = mutate("stage")
unstage = mutate("unstage")
discard = mutate("discard")
commit = mutate("commit")
branch_switch = mutate("branch/switch")
push = mutate("push")
pull = mutate("pull")
fetch = mutate("fetch")
worktree_add = mutate("worktree/add")
worktree_remove = mutate("worktree/remove")
