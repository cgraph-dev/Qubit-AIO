"""HUD projects collector — Qubit-native port of ``backend/collectors/projects.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: PORT (Qubit has a project registry; the previous lane said it did not)
------------------------------------------------------------------------------
The previous lane marked this GAP: "Qubit's projects.db has no stable public
read seam owned by a single writer." Two errors. Qubit has no ``projects.db``
at all; and it has a better seam than upstream did. Evidence:

* :mod:`voicebridge.projects` is the ONE canonical Qubit project registry
  (schema ``qubit-projects/1``): ``list_projects(cfg)`` returns
  ``{state, active_slug, projects[{slug, name, description, folders[],
  primary, archived, board, active, created_at, updated_at}]}``. The same
  registry backs the CLI ``qubit project``, the dashboard Projects page and
  ``GET /api/profiles/projects/tree``.
* upstream ``collect_projects`` preferred ``<home>/projects.db`` and fell back
  to a raw ``~/projects`` directory scan. The directory fallback is NOT ported:
  a folder is not a project, and Qubit's registry already answers "what is a
  project" and "which one is active".

What IS ported: upstream's per-project git enrichment (branch, last-commit
subject + age, dirty-file count, total commits), applied to each registered
folder that still exists on disk. Upstream ran one ``git`` per field with a 5s
timeout; this module runs the same four reads through ONE ``git status
--porcelain=v2 --branch`` call plus ``git log``/``rev-list``, still argv-only,
still never a shell, still timeout-bounded, and still skipped for a folder that
is gone (a stale pointer is reported as ``present: false``, not as a clean
tree).

Pure function, JSON-ready dict, never raises; git absence is reported per
project rather than failing the panel.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Per-repo git work is bounded: this panel must stay a viewer, not a scan.
GIT_TIMEOUT_S = 5.0
GIT_MAX_PROJECTS = 40

#: Same extension table upstream used, kept for the language badges.
_EXT_LANG = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "React", ".tsx": "React/TS", ".rs": "Rust", ".go": "Go",
    ".java": "Java", ".cpp": "C++", ".c": "C", ".rb": "Ruby",
    ".sh": "Shell", ".html": "HTML", ".css": "CSS", ".vue": "Vue",
    ".svelte": "Svelte",
}


def _git(repo: Path, args: List[str]) -> str:
    """One bounded, argv-only git read. Empty string on any failure."""
    try:
        res = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True,
                             timeout=GIT_TIMEOUT_S)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _iso(ts: Any) -> Optional[str]:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(float(ts)).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(ts, str) and ts:
        return ts
    return None


def _languages(folder: Path) -> List[str]:
    langs = set()
    try:
        for item in folder.iterdir():
            if item.is_file() and item.suffix.lower() in _EXT_LANG:
                langs.add(_EXT_LANG[item.suffix.lower()])
            elif item.is_dir() and item.name == "src":
                for sub in item.iterdir():
                    if sub.is_file() and sub.suffix.lower() in _EXT_LANG:
                        langs.add(_EXT_LANG[sub.suffix.lower()])
    except (PermissionError, OSError):
        pass
    return sorted(langs)[:5]


def _activity(ts: Optional[str]) -> str:
    """Upstream's activity bucket, computed from a real timestamp.

    Upstream bucketed by parsing git's human "3 days ago" string; this works
    from ``%ct`` because the string is locale-dependent and unparseable in some
    locales — the same defect class the HUD's own comments call out.
    """
    if not ts:
        return "unknown"
    try:
        then = datetime.fromisoformat(ts).timestamp()
    except (ValueError, OSError):
        return "unknown"
    days = (datetime.now().timestamp() - then) / 86400.0
    if days <= 3:
        return "active"
    if days <= 14:
        return "recent"
    return "stale"


def _enrich(folder: Path) -> Dict[str, Any]:
    """Git + language facts for one existing folder."""
    out: Dict[str, Any] = {
        "present": True, "is_git": False, "branch": "", "dirty_files": 0,
        "total_commits": 0, "last_commit_msg": "", "last_commit_at": None,
        "last_modified": None, "has_readme": False, "has_package_json": False,
        "has_requirements": False, "has_pyproject": False, "languages": [],
        "git_error": "",
    }
    out["languages"] = _languages(folder)
    out["has_readme"] = ((folder / "README.md").is_file()
                         or (folder / "readme.md").is_file())
    out["has_package_json"] = (folder / "package.json").is_file()
    out["has_requirements"] = (folder / "requirements.txt").is_file()
    out["has_pyproject"] = (folder / "pyproject.toml").is_file()
    try:
        out["last_modified"] = _iso(folder.stat().st_mtime)
    except OSError:
        pass
    if not (folder / ".git").exists():
        return out
    out["is_git"] = True
    out["branch"] = _git(folder, ["branch", "--show-current"]) or "HEAD"
    status = _git(folder, ["status", "--porcelain"])
    out["dirty_files"] = len([ln for ln in status.splitlines() if ln.strip()])
    log = _git(folder, ["log", "-1", "--format=%ct|%s"])
    if "|" in log:
        raw_ts, _, subject = log.partition("|")
        out["last_commit_msg"] = subject[:200]
        try:
            out["last_commit_at"] = _iso(float(raw_ts))
        except (TypeError, ValueError):
            pass
    count = _git(folder, ["rev-list", "--count", "HEAD"])
    try:
        out["total_commits"] = int(count)
    except (TypeError, ValueError):
        pass
    return out


def _project(rec: Dict[str, Any]) -> Dict[str, Any]:
    folders = [str(f) for f in (rec.get("folders") or []) if f]
    primary = str(rec.get("primary") or (folders[0] if folders else ""))
    row: Dict[str, Any] = {
        "slug": str(rec.get("slug") or ""),
        "name": str(rec.get("name") or rec.get("slug") or "unnamed"),
        "description": str(rec.get("description") or "")[:400],
        "active": bool(rec.get("active")),
        "archived": bool(rec.get("archived")),
        "board": rec.get("board"),
        "folder_count": len(folders),
        "folders": folders[:10],
        "path": primary,
        "created_at": _iso(rec.get("created_at")),
        "updated_at": _iso(rec.get("updated_at")),
        "present": False,
        "is_git": False,
        "branch": "",
        "dirty_files": 0,
        "total_commits": 0,
        "last_commit_msg": "",
        "last_commit_at": None,
        "last_modified": None,
        "has_readme": False,
        "has_package_json": False,
        "has_requirements": False,
        "has_pyproject": False,
        "languages": [],
        "activity": "unknown",
        "git_error": "",
    }
    if primary:
        try:
            path = Path(primary).expanduser()
            present = path.is_dir()
        except OSError:
            present = False
        if present:
            row.update(_enrich(path))
        else:
            row["git_error"] = ("the registered folder is not on disk; "
                                "reported as present: false, not as a clean "
                                "tree")
    row["activity"] = _activity(row.get("last_commit_at"))
    return row


def collect_projects(home: Optional[Any] = None, cfg: Any = None, *,
                     include_archived: bool = False,
                     git_limit: int = GIT_MAX_PROJECTS) -> Dict[str, Any]:
    """The registered Qubit projects with their real on-disk git state."""
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "projects": [],
        "total": 0,
        "active_slug": "",
        "git_repos": 0,
        "dirty": 0,
        "active_recent": 0,
        "missing_folders": 0,
        "authority": "voicebridge.projects (qubit-projects/1 registry)",
    }
    try:
        from .. import projects as PJ

        res = PJ.list_projects(cfg, include_archived=bool(include_archived))
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"project registry unavailable ({type(exc).__name__}: {exc})"
        return out
    recs = res.get("projects") if isinstance(res, dict) else None
    if not isinstance(recs, list):
        out["detail"] = "the project registry returned no project list"
        return out

    rows: List[Dict[str, Any]] = []
    for i, rec in enumerate(recs):
        if not isinstance(rec, dict):
            continue
        row = _project(rec)
        if i >= git_limit:
            # Beyond the git budget the registry facts stand alone; say so
            # instead of showing an empty repo as if it were inspected.
            row["git_error"] = (f"git not inspected: past the {git_limit}-"
                                f"project inspection budget")
        rows.append(row)

    git_repos = sum(1 for r in rows if r["is_git"])
    order = {"active": 0, "recent": 1, "stale": 2, "unknown": 3}
    rows.sort(key=lambda r: (order.get(r["activity"], 4), r["name"].lower()))
    out.update(
        available=True,
        detail=f"{len(rows)} registered project(s), {git_repos} git repo(s)",
        projects=rows,
        total=len(rows),
        active_slug=str(res.get("active_slug") or ""),
        git_repos=git_repos,
        dirty=sum(1 for r in rows if int(r["dirty_files"] or 0) > 0),
        active_recent=sum(1 for r in rows if r["activity"] in ("active", "recent")),
        missing_folders=sum(1 for r in rows if not r["present"]),
    )
    return out


__all__ = ["collect_projects"]
