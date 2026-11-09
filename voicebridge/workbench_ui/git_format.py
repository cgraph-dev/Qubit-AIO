"""Wire shapes for the workbench git family.

Every function here is a PURE formatter over output :mod:`.cmdrun` already
captured, plus the vendored runtime's own parsers where one exists. Parsing is
delegated rather than re-derived wherever the vendored code already does it
(``qubit_cli.web_git.worktree_list`` / ``branch_list`` / ``base_branch_list``),
so a change to the desktop's coding rail and the workbench cannot drift apart.

WHERE A SHAPE IS TAKEN FROM
---------------------------------------------------------------------------
The ported page's own JS (``voicebridge/workbench_ui/static/*.js``) does not call
``/api/git/*`` — it reads git facts through ``/api/session`` headers and renders
no review pane. The git family exists here because the desktop's coding rail does
call it and the workbench serves the same page. So the wire contract is the one
that rail already speaks (``qubit_cli/web_routers/git.py`` at the vendored pin):
``{"worktrees": [...]}``, ``{"branches": [...]}``, ``{"files": [...], "base": ...}``,
``{"sha": ...}``. Those keys are quoted verbatim in the family docstrings; nothing
is invented.

TWO EXTENSIONS THE WORKBENCH NEEDS
---------------------------------------------------------------------------
Upstream's rows assume the caller is inside the tree and never show its path.
These rows add ``root`` (the confined work-tree top level actually used) and
``workspace`` (the path the request named), because the workbench can serve
several registered roots and the client has to know which repo answered.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import cmdrun, git_repo

#: How many changed files a status/branch payload carries. A repo with 5000
#: modified files is a fact the pane cannot paint; the totals still say so.
MAX_STATUS_FILES = 500


def _vendored():
    """The vendored coding-rail parsers, or ``None`` when unavailable."""
    try:
        from qubit_cli import web_git as WG

        return WG
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ branches
def branch_rows(repo: Path) -> List[Dict[str, Any]]:
    """Branch picker rows: local heads, then untracked remote-tracking refs.

    Contract: ``{name, checkedOut, isDefault, isRemote, worktreePath}`` — the
    exact keys the desktop rail's ``git_branches_route`` returns under
    ``"branches"``.
    """
    wg = _vendored()
    if wg is not None:
        try:
            return list(wg.branch_list(str(repo)))
        except Exception:  # noqa: BLE001 - fall through to the direct read
            pass
    rows: List[Dict[str, Any]] = []
    trunk = git_repo.default_branch(repo)
    locals_ = [ln.strip() for ln in cmdrun.ok_or_empty(
        repo, ["for-each-ref", "--format=%(refname:short)",
               "--sort=-committerdate", "refs/heads"]).splitlines() if ln.strip()]
    worktrees = worktree_rows(repo)
    path_by_branch = {t.get("branch"): t.get("path") for t in worktrees
                      if t.get("branch")}
    for name in locals_:
        rows.append({"name": name, "checkedOut": name in path_by_branch,
                     "isDefault": bool(trunk and name == trunk),
                     "isRemote": False,
                     "worktreePath": path_by_branch.get(name)})
    local_set = set(locals_)
    remotes = [ln.strip() for ln in cmdrun.ok_or_empty(
        repo, ["for-each-ref", "--format=%(refname:short)",
               "--sort=-committerdate", "refs/remotes"]).splitlines() if ln.strip()]
    for name in remotes:
        if name.endswith("/HEAD") or name.split("/", 1)[-1] in local_set:
            continue
        rows.append({"name": name, "checkedOut": False, "isDefault": False,
                     "isRemote": True, "worktreePath": None})
    return rows


def base_branch_rows(repo: Path) -> List[Dict[str, Any]]:
    """Base-branch picker rows: ``{name, isRemote, isDefault}``."""
    wg = _vendored()
    if wg is not None:
        try:
            return list(wg.base_branch_list(str(repo)))
        except Exception:  # noqa: BLE001
            pass
    default = git_repo.origin_head(repo) or git_repo.default_branch(repo)
    rows = []
    for line in cmdrun.ok_or_empty(
            repo, ["for-each-ref",
                   "--format=%(refname:short)", "--sort=-committerdate",
                   "refs/heads", "refs/remotes"]).splitlines():
        name = line.strip()
        if not name:
            continue
        rows.append({"name": name, "isRemote": name.startswith("origin/"),
                     "isDefault": bool(default and name == default)})
    return rows


def worktree_rows(repo: Path) -> List[Dict[str, Any]]:
    """``{path, branch, isMain, detached, locked}`` per worktree, main first."""
    wg = _vendored()
    if wg is not None:
        try:
            return list(wg.worktree_list(str(repo)))
        except Exception:  # noqa: BLE001 - fall through
            pass
    trees: List[Dict[str, Any]] = []
    for line in cmdrun.ok_or_empty(repo, ["worktree", "list",
                                          "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            trees.append({"path": line[9:].strip(), "branch": None,
                          "isMain": not trees, "detached": False,
                          "locked": False})
        elif not trees:
            continue
        elif line.startswith("branch "):
            trees[-1]["branch"] = line[7:].strip().replace("refs/heads/", "", 1)
        elif line == "detached":
            trees[-1]["detached"] = True
        elif line.startswith("locked"):
            trees[-1]["locked"] = True
    return trees


# -------------------------------------------------------------------- status
def _exit_status_letter(tag: str, xy: str) -> str:
    """One letter for a porcelain-v2 entry, the way the rail renders it."""
    wg = _vendored()
    if wg is not None:
        try:
            return str(wg._status_letter(tag, xy))
        except Exception:  # noqa: BLE001
            pass
    if tag in ("?", "u"):
        return tag.upper()
    code = xy[0] if xy[0] != "." else (xy[1] if len(xy) > 1 else ".")
    return (code if code != "." else "M").upper()


def _numstat(repo: Path, *args: str) -> Dict[str, tuple]:
    counts: Dict[str, tuple] = {}
    for line in cmdrun.ok_or_empty(
            repo, ["diff", "--numstat", "--no-renames", *args]).splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            added = 0 if parts[0] == "-" else int(parts[0] or 0)
            removed = 0 if parts[1] == "-" else int(parts[1] or 0)
        except ValueError:
            continue
        counts[parts[2]] = (added, removed)
    return counts


def status_payload(repo: Path, *, workspace: str = "") -> Dict[str, Any]:
    """``git_status_route``'s payload plus the confined paths that produced it.

    Contract keys (vendored ``web_git.repo_status``): ``branch``,
    ``defaultBranch``, ``detached``, ``ahead``, ``behind``, ``staged``,
    ``unstaged``, ``untracked``, ``conflicted``, ``changed``, ``added``,
    ``removed``, ``files``. ``files`` is capped at :data:`MAX_STATUS_FILES`.
    """
    # ``-z`` is load-bearing: it is what makes the ENTRY records NUL separated.
    # Without it a filename containing a newline splits into two bogus records
    # (measured: ``? a.txt\n? new.txt`` came back as one line-terminated blob).
    raw = cmdrun.out(repo, ["status", "--porcelain=v2", "-z", "--branch",
                            "--untracked-files=normal"])
    header = git_repo.status_headers(raw)
    branch: Optional[str] = header["branch"]
    detached = bool(header["detached"])
    ahead, behind = int(header["ahead"]), int(header["behind"])
    head_sha = str(header["head"] or "")
    counts = _numstat(repo, "HEAD") if git_repo.has_head(repo) else {}
    files: List[Dict[str, Any]] = []
    staged = unstaged = untracked = conflicted = 0
    for tag, xy, path in git_repo.walk_entries(raw):
        is_untracked = tag == "?"
        is_conflicted = tag == "u"
        entry_staged = (tag in ("1", "2") and xy[0] not in (".", "?")) \
            or is_conflicted
        entry_unstaged = is_untracked or (
            tag in ("1", "2") and len(xy) > 1 and xy[1] not in (".", "?")) \
            or is_conflicted
        staged += 1 if entry_staged else 0
        unstaged += 1 if entry_unstaged else 0
        untracked += 1 if is_untracked else 0
        conflicted += 1 if is_conflicted else 0
        if len(files) < MAX_STATUS_FILES:
            added, removed = counts.get(path, (0, 0))
            files.append({
                "path": path,
                "status": _exit_status_letter(tag, xy),
                "staged": entry_staged,
                "unstaged": entry_unstaged,
                "untracked": is_untracked,
                "conflicted": is_conflicted,
                "added": added,
                "removed": removed,
            })
    return {
        "ok": True,
        "root": str(repo),
        "workspace": workspace,
        "branch": branch,
        "head": head_sha or None,
        "defaultBranch": git_repo.default_branch(repo) or None,
        "detached": detached,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "conflicted": conflicted,
        "changed": staged + unstaged + untracked + conflicted,
        "added": sum(a for a, _r in counts.values()),
        "removed": sum(r for _a, r in counts.values()),
        "files": files,
        "files_truncated": (staged + unstaged + untracked + conflicted) > len(files),
        "state": "OK",
        "rollback": "read-only; nothing to roll back",
    }


# ----------------------------------------------------------------------- log
_LOG_FORMAT = "--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s"


def log_payload(repo: Path, *, limit: int = 30, ref: str = "HEAD",
                skip: int = 0) -> Dict[str, Any]:
    """Bounded commit log: ``{entries: [{sha, short, author, email, date,
    subject, body}], truncated, has_more}``."""
    limit = max(1, min(int(limit or 30), 200))
    args = ["log", f"--max-count={limit + 1}", f"--skip={max(0, skip)}",
            _LOG_FORMAT]
    if ref and ref != "HEAD":
        args.append(ref)
    args.append("--")
    text = cmdrun.ok_or_empty(repo, args)
    rows = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 6:
            continue
        rows.append({"sha": parts[0], "short": parts[1], "author": parts[2],
                     "email": parts[3], "date": parts[4], "subject": parts[5],
                     "body": ""})
    truncated = len(rows) > limit
    return {"ok": True, "root": str(repo), "ref": ref or "HEAD",
            "entries": rows[:limit], "truncated": truncated,
            "has_more": truncated, "state": "OK",
            "rollback": "read-only; nothing to roll back"}


# ---------------------------------------------------------------------- diff
def diff_payload(repo: Path, *, staged: bool = False, ref: str = "",
                 path: str = "", stat_only: bool = False) -> Dict[str, Any]:
    """``{stat, patch, path, staged, truncated}`` for one scope.

    ``path`` must already be repo-relative and confinement-checked by the
    caller; it is passed as a LITERAL pathspec so a file whose name contains a
    glob character is addressed exactly.
    """
    args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv"]
    if staged:
        args.append("--cached")
    if ref and ref.upper() != "HEAD":
        args.append(ref)
    target = [f":(literal){path}"] if path else ["."]
    stat = cmdrun.out(repo, [*args, "--stat", "--", *target])
    patch = "" if stat_only else cmdrun.out(repo, [*args, "-U3", "--", *target])
    return {"ok": True, "root": str(repo), "path": path or ".",
            "staged": bool(staged), "ref": ref or "HEAD",
            "stat": stat, "patch": patch,
            "truncated": len(patch) >= cmdrun.MAX_OUTPUT_CHARS,
            "state": "OK", "rollback": "read-only; nothing to roll back"}
