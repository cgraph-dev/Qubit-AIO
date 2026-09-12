"""Qubit projects: named multi-folder workspaces.

Qubit projects are human-named workspaces spanning multiple folders/repos.
Qubit maps that surface onto one canonical registry file under the Qubit
state dir (``projects.json``), never a legacy tree:

* ``create/list/show/add-folder/remove-folder/rename/set-primary/use/
  archive/restore`` are real registry operations with typed receipts;
* every recorded folder is pre-validated through the 8R
  :func:`resolve_project_root` gate (must exist, be a directory, never
  ``/``/home-root/secret-bearing) so a recorded folder is always runnable
  as an opencode root — the registry never decorates unrunnable paths;
* ``bind-board`` is typed-refused (``PROJECT_NO_BOARD``) until the kanban
  row lands and provides boards to bind;
* mutations need explicit ``--confirm`` (top-level ``--dry-run`` previews),
  refuse while the emergency stop is latched, append timestamped backups
  (``project rollback`` restores them), and emit redacted audit events via
  :func:`log_event` (the central redaction choke point);
* registry reads are bounded (:data:`MAX_FILE_BYTES`, folder/name caps);
* ``use`` persists the active-project pointer consumed by ``list``/``show``
  (and future workspace consumers); it changes no execution defaults.

Qubit has one canonical state tree,
so the registry is global to the Qubit installation (documented divergence).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-projects/1"
STORE_NAME = "projects.json"
DEFAULT_CLONE_ROOT = "~/Qubit/projects"
MAX_FILE_BYTES = 1_000_000
MAX_PROJECTS = 128
MAX_FOLDERS_PER_PROJECT = 32
MAX_NAME_LEN = 80
MAX_SLUG_LEN = 64
MAX_DESCRIPTION_LEN = 500
MAX_REF_LEN = 200
BACKUP_KEEP = 5

CONFIRM_CODE = "PROJECT_CONFIRM_REQUIRED"
ESTOP_CODE = "PROJECT_ESTOP"
NOT_FOUND_CODE = "PROJECT_NOT_FOUND"
EXISTS_CODE = "PROJECT_EXISTS"
BAD_NAME_CODE = "PROJECT_BAD_NAME"
BAD_FOLDER_CODE = "PROJECT_BAD_FOLDER"
ARCHIVED_CODE = "PROJECT_ARCHIVED"
NO_BOARD_CODE = "PROJECT_NO_BOARD"
CORRUPT_CODE = "PROJECT_STORE_CORRUPT"
ROLLBACK_CODE = "PROJECT_NO_ROLLBACK"
CLONE_REMOTE_CODE = "PROJECT_CLONE_REMOTE_REFUSED"
CLONE_FAILED_CODE = "PROJECT_CLONE_FAILED"
CLONE_DEST_CODE = "PROJECT_CLONE_DEST_EXISTS"
CHECKOUT_FAILED_CODE = "PROJECT_CHECKOUT_FAILED"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


class ProjectError(Exception):
    """Typed project-registry failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def redact_spec(text: str) -> str:
    """Redact user-supplied names/descriptions before echoing them."""
    return _redact(text)


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a human name (lowercase, dashed)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:MAX_SLUG_LEN]


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "projects": {}, "active_slug": None}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ProjectError(CORRUPT_CODE,
                               f"project store is {path.stat().st_size} bytes "
                               f"(limit {MAX_FILE_BYTES}); restore a backup "
                               f"with `project rollback`")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ProjectError:
        raise
    except (OSError, ValueError) as exc:
        raise ProjectError(CORRUPT_CODE,
                           f"project store unreadable: {exc}; restore a "
                           f"backup with `project rollback`") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("projects"), dict):
        raise ProjectError(CORRUPT_CODE,
                           "project store has an unknown schema; restore a "
                           "backup with `project rollback`")
    raw.setdefault("active_slug", None)
    return raw


def _backup(cfg: Any) -> Optional[Path]:
    path = _store_path(cfg)
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    dest = path.with_name(f"{STORE_NAME}.bak.{stamp}")
    try:
        dest.write_bytes(path.read_bytes())
        os.chmod(dest, 0o600)
    except OSError:
        return None
    try:
        olds = sorted(path.parent.glob(f"{STORE_NAME}.bak.*"),
                      key=lambda p: p.name)
        for stale in olds[:-BACKUP_KEEP]:
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError:
        pass
    return dest


def _backups(cfg: Any) -> List[Path]:
    try:
        return sorted(_store_path(cfg).parent.glob(f"{STORE_NAME}.bak.*"),
                      key=lambda p: p.name)
    except OSError:
        return []


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise ProjectError(CORRUPT_CODE,
                           f"project store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"project.{op}", actor=actor, **detail)
    except Exception:
        pass


def _now() -> float:
    return time.time()


def _check_name(name: str) -> str:
    text = (name or "").strip()
    if not text or len(text) > MAX_NAME_LEN:
        raise ProjectError(BAD_NAME_CODE,
                           f"project name must be 1..{MAX_NAME_LEN} chars")
    return text


def _check_slug(slug: str) -> str:
    text = (slug or "").strip().lower()
    if not _SLUG_RE.match(text):
        raise ProjectError(
            BAD_NAME_CODE,
            "project slug must match ^[a-z0-9][a-z0-9-]{0,63}$")
    return text


def _check_description(text: str) -> str:
    if len(text or "") > MAX_DESCRIPTION_LEN:
        raise ProjectError(BAD_NAME_CODE,
                           f"description must be <={MAX_DESCRIPTION_LEN} chars")
    return text or ""


def _resolve_folder(folder: str) -> str:
    """Validate one workspace folder through the 8R project-root gate."""
    from .opencode.policy import resolve_project_root
    from .actions import ActionError

    try:
        return str(resolve_project_root(folder))
    except ActionError as exc:
        raise ProjectError(BAD_FOLDER_CODE, str(exc)) from exc


def _clone_root(cfg: Any) -> Path:
    """Confined root for locally cloned workspace repositories."""
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    raw = str(get("projects", "clone_root", default="") or "").strip()
    return Path(os.path.expanduser(raw or DEFAULT_CLONE_ROOT))


def _looks_remote(source: str) -> bool:
    """True for anything that is not a plain local path (URLs/SCP)."""
    text = str(source or "").strip()
    return bool(_SCHEME_RE.match(text)) or text.startswith("git@") \
        or text.startswith("ssh:")


def _git(args: List[str], cwd: Optional[Path] = None, *,
         timeout: int = 60) -> subprocess.CompletedProcess:
    """Run one bounded git command with prompts disabled (never a shell)."""
    git = shutil.which("git")
    if git is None:
        raise ProjectError(CLONE_FAILED_CODE,
                           "git is required for project clone/checkout")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    try:
        return subprocess.run([git, *args], cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True,
                              timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectError(CLONE_FAILED_CODE,
                           f"git invocation failed: {exc}") from exc


def _is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    out = _git(["rev-parse", "--is-inside-work-tree"], cwd=path, timeout=15)
    return out.returncode == 0 and out.stdout.strip() == "true"


def _check_ref(ref: str) -> str:
    """Validate a git ref/branch/tag for checkout (no option injection)."""
    text = str(ref or "").strip()
    if not text:
        return ""
    if len(text) > MAX_REF_LEN:
        raise ProjectError(BAD_NAME_CODE,
                           f"ref must be <={MAX_REF_LEN} chars")
    if text.startswith("-") or text.startswith("+") or ".." in text \
            or any(ch in text for ch in "\x00\n\r"):
        raise ProjectError(BAD_NAME_CODE,
                           "ref may not start with '-'/'+' or contain '..'")
    return text


def _get(state: Dict[str, Any], slug: str) -> Dict[str, Any]:
    rec = state["projects"].get(slug)
    if rec is None:
        raise ProjectError(NOT_FOUND_CODE, f"no project {slug!r}")
    return rec


def _touch(rec: Dict[str, Any], actor: str) -> None:
    rec["updated_at"] = _now()
    rec["updated_by"] = actor


def _public(rec: Dict[str, Any], active: bool) -> Dict[str, Any]:
    return {"slug": rec["slug"], "name": redact_spec(rec["name"]),
            "description": redact_spec(rec.get("description", "")),
            "folders": list(rec.get("folders", [])),
            "primary": rec.get("primary"),
            "archived": bool(rec.get("archived", False)),
            "board": rec.get("board"),
            "active": active,
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
            "updated_by": rec.get("updated_by")}


# ---------------------------------------------------------------- operations
def create(cfg: Any, name: str, folders: List[str], *, slug: str = "",
           description: str = "", primary: str = "",
           requested_by: str = "cli:user", confirmed: bool = False,
           use: bool = False) -> Dict[str, Any]:
    """Create a named workspace (first folder = primary)."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project create requires explicit --confirm")
    state = _load(cfg)
    if len(state["projects"]) >= MAX_PROJECTS:
        raise ProjectError(EXISTS_CODE,
                           f"project limit reached ({MAX_PROJECTS})")
    clean_name = _check_name(name)
    clean_slug = _check_slug(slug or slugify(clean_name))
    if not clean_slug:
        raise ProjectError(BAD_NAME_CODE,
                           "project name yields an empty slug; pass --slug")
    if clean_slug in state["projects"]:
        raise ProjectError(EXISTS_CODE, f"project {clean_slug!r} exists")
    if len(folders) > MAX_FOLDERS_PER_PROJECT:
        raise ProjectError(BAD_FOLDER_CODE,
                           f"at most {MAX_FOLDERS_PER_PROJECT} folders")
    resolved = [_resolve_folder(f) for f in folders]
    if primary:
        clean_primary = _resolve_folder(primary)
        if clean_primary not in resolved:
            resolved.append(clean_primary)
    else:
        clean_primary = resolved[0] if resolved else None
    rec = {"slug": clean_slug, "name": clean_name,
           "description": _check_description(description),
           "folders": resolved, "primary": clean_primary,
           "archived": False, "board": None,
           "created_at": _now(), "updated_at": _now(),
           "updated_by": requested_by}
    _backup(cfg)
    state["projects"][clean_slug] = rec
    if use:
        state["active_slug"] = clean_slug
    _save(cfg, state)
    _audit("create", requested_by,
           {"slug": clean_slug, "folders": len(resolved),
            "use": bool(use)})
    return {"state": "OK", "project": _public(rec, state["active_slug"] == clean_slug)}


def list_projects(cfg: Any, include_archived: bool = False) -> Dict[str, Any]:
    """List workspaces (archived hidden unless asked)."""
    state = _load(cfg)
    recs = [r for r in state["projects"].values()
            if include_archived or not r.get("archived", False)]
    recs.sort(key=lambda r: r.get("slug", ""))
    active = state.get("active_slug")
    return {"state": "OK", "active_slug": active,
            "projects": [_public(r, r["slug"] == active) for r in recs]}


def show(cfg: Any, slug: str) -> Dict[str, Any]:
    """Show one workspace with its details."""
    state = _load(cfg)
    rec = _get(state, _check_slug(slug))
    return {"state": "OK",
            "project": _public(rec, state.get("active_slug") == rec["slug"])}


def active_project(cfg: Any) -> Dict[str, Any]:
    """Resolve "my active project": the workspace the active-project
    pointer selects, with its details (read-only).

    Shared by `qubit project active`, the voice `projects_active` entry
    (same backend as CLI/dashboard), and any dashboard Tier-1 call. No
    pointer set (or pointer dangling after restore/rollback) returns a
    typed EMPTY state — never a fabricated workspace."""
    state = _load(cfg)
    slug = state.get("active_slug")
    if not slug or slug not in state["projects"]:
        return {"state": "EMPTY", "code": "NO_ACTIVE_PROJECT",
                "detail": "no active workspace; set one with "
                          "`qubit project use <slug> --confirm`",
                "owner": "qubit-policy",
                "rollback": "registry untouched; nothing to roll back"}
    rec = state["projects"][slug]
    return {"state": "OK",
            "project": _public(rec, True)}


def _mutate(cfg: Any, slug: str, actor: str, op: str,
            fn) -> Dict[str, Any]:
    state = _load(cfg)
    rec = _get(state, _check_slug(slug))
    if rec.get("archived", False):
        raise ProjectError(ARCHIVED_CODE,
                           f"project {rec['slug']!r} is archived; "
                           f"restore it before `{op}`")
    fn(state, rec)
    _touch(rec, actor)
    _backup(cfg)
    _save(cfg, state)
    _audit(op, actor, {"slug": rec["slug"]})
    return {"state": "OK",
            "project": _public(rec, state.get("active_slug") == rec["slug"])}


def rename(cfg: Any, slug: str, name: str, *,
           requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Rename a workspace (slug is stable)."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project rename requires explicit --confirm")
    clean = _check_name(name)

    def _do(state, rec):
        rec["name"] = clean

    return _mutate(cfg, slug, requested_by, "rename", _do)


def add_folder(cfg: Any, slug: str, folder: str, *,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Add a validated folder to a workspace."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project add-folder requires explicit --confirm")
    resolved = _resolve_folder(folder)

    def _do(state, rec):
        if resolved in rec["folders"]:
            raise ProjectError(BAD_FOLDER_CODE,
                               f"folder already in {rec['slug']!r}")
        if len(rec["folders"]) >= MAX_FOLDERS_PER_PROJECT:
            raise ProjectError(BAD_FOLDER_CODE,
                               f"at most {MAX_FOLDERS_PER_PROJECT} folders")
        rec["folders"].append(resolved)
        if rec.get("primary") is None:
            rec["primary"] = resolved

    return _mutate(cfg, slug, requested_by, "add-folder", _do)


def remove_folder(cfg: Any, slug: str, folder: str, *,
                  requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Remove a folder (primary falls back to the next remaining folder)."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project remove-folder requires explicit --confirm")
    raw = folder.strip()
    if not raw:
        raise ProjectError(BAD_FOLDER_CODE, "a folder path is required")
    # Removal must not require the folder to still exist on disk: a workspace
    # whose folder vanished would otherwise be permanently stuck in the
    # registry. Match the recorded canonical path (and the raw input) either
    # way; adding a folder still validates existence through the 8R gate.
    try:
        resolved = str(Path(os.path.expanduser(raw)).resolve())
    except (OSError, RuntimeError):
        resolved = os.path.expanduser(raw)

    def _do(state, rec):
        target = next((f for f in rec["folders"]
                       if f in (resolved, raw)), None)
        if target is None:
            raise ProjectError(BAD_FOLDER_CODE,
                               f"folder not in {rec['slug']!r}")
        rec["folders"] = [f for f in rec["folders"] if f != target]
        if rec.get("primary") == target:
            rec["primary"] = rec["folders"][0] if rec["folders"] else None

    return _mutate(cfg, slug, requested_by, "remove-folder", _do)


def set_primary(cfg: Any, slug: str, folder: str, *,
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Set the primary folder (must already be a member)."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project set-primary requires explicit --confirm")
    resolved = _resolve_folder(folder)

    def _do(state, rec):
        if resolved not in rec["folders"]:
            raise ProjectError(BAD_FOLDER_CODE,
                               f"folder not in {rec['slug']!r}; "
                               f"add it first")
        rec["primary"] = resolved

    return _mutate(cfg, slug, requested_by, "set-primary", _do)


def use(cfg: Any, slug: str, *, requested_by: str = "cli:user",
        confirmed: bool = False, clear: bool = False) -> Dict[str, Any]:
    """Set (or clear) the active-project pointer."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project use requires explicit --confirm")
    state = _load(cfg)
    if clear:
        state["active_slug"] = None
    else:
        rec = _get(state, _check_slug(slug))
        if rec.get("archived", False):
            raise ProjectError(ARCHIVED_CODE,
                               f"project {rec['slug']!r} is archived")
        state["active_slug"] = rec["slug"]
    _backup(cfg)
    _save(cfg, state)
    _audit("use", requested_by, {"active_slug": state["active_slug"]})
    return {"state": "OK", "active_slug": state["active_slug"]}


def archive(cfg: Any, slug: str, *, requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Archive a workspace (hidden from default lists, mutations refused)."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project archive requires explicit --confirm")
    state = _load(cfg)
    rec = _get(state, _check_slug(slug))
    rec["archived"] = True
    _touch(rec, requested_by)
    if state.get("active_slug") == rec["slug"]:
        state["active_slug"] = None
    _backup(cfg)
    _save(cfg, state)
    _audit("archive", requested_by, {"slug": rec["slug"]})
    return {"state": "OK",
            "project": _public(rec, False)}


def restore(cfg: Any, slug: str, *, requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Restore an archived workspace."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project restore requires explicit --confirm")
    state = _load(cfg)
    rec = _get(state, _check_slug(slug))
    if not rec.get("archived", False):
        raise ProjectError(ARCHIVED_CODE,
                           f"project {rec['slug']!r} is not archived")
    rec["archived"] = False
    _touch(rec, requested_by)
    _backup(cfg)
    _save(cfg, state)
    _audit("restore", requested_by, {"slug": rec["slug"]})
    return {"state": "OK",
            "project": _public(rec, state.get("active_slug") == rec["slug"])}


def bind_board(cfg: Any, slug: str, board: str, *,
               requested_by: str = "cli:user",
               confirmed: bool = False, unbind: bool = False) -> Dict[str, Any]:
    """Bind (or unbind) a real kanban board (both sides stay consistent)."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project bind-board requires explicit --confirm")
    from .kanban import board_exists, set_board_project, KanbanError

    if unbind:
        def _do(state, rec):
            old = rec.get("board")
            if old:
                try:
                    set_board_project(cfg, old, None,
                                      requested_by=requested_by)
                except KanbanError:
                    pass
            rec["board"] = None

        return _mutate(cfg, slug, requested_by, "bind-board", _do)
    try:
        from . import kanban as KB

        clean_board = KB._check_board_slug(board)
        exists = board_exists(cfg, clean_board)
    except KanbanError as exc:
        raise ProjectError(BAD_NAME_CODE, exc.detail) from exc
    if not exists:
        raise ProjectError(NO_BOARD_CODE,
                           f"no board {board!r}; create it first with "
                           f"`kanban boards-create {board} --confirm`")

    def _do(state, rec):
        previous = rec.get("board")
        if previous and previous != clean_board:
            try:
                set_board_project(cfg, previous, None,
                                  requested_by=requested_by)
            except KanbanError:
                pass
        set_board_project(cfg, clean_board, rec["slug"],
                          requested_by=requested_by)
        rec["board"] = clean_board

    return _mutate(cfg, slug, requested_by, "bind-board", _do)


def clone(cfg: Any, name: str, source: str, *, slug: str = "", ref: str = "",
          description: str = "", requested_by: str = "cli:user",
          confirmed: bool = False, use: bool = False) -> Dict[str, Any]:
    """Clone a LOCAL git repository into a confined workspace.

    Qubit's clone is deliberately local-only: the source must be an existing
    on-disk git repository. Network/URL/SCP sources are typed-refused with
    ``PROJECT_CLONE_REMOTE_REFUSED`` (no outbound fetch ever happens). The
    clone lands under the confined ``projects.clone_root`` (default
    ``~/Qubit/projects``), optionally checks out a validated ``ref``, and is
    registered as a normal workspace with backup/rollback/audit.
    """
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project clone requires explicit --confirm")
    clean_name = _check_name(name)
    clean_slug = _check_slug(slug or slugify(clean_name))
    if not clean_slug:
        raise ProjectError(BAD_NAME_CODE,
                           "project name yields an empty slug; pass --slug")
    raw_source = str(source or "").strip()
    if _looks_remote(raw_source):
        raise ProjectError(
            CLONE_REMOTE_CODE,
            "remote/URL clone sources are refused: project clone is confined "
            "to an existing local git repository (no network fetch)")
    src = Path(os.path.expanduser(raw_source))
    try:
        src_resolved = src.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ProjectError(BAD_FOLDER_CODE,
                           f"clone source {raw_source!r} is not a directory")
    if not src_resolved.is_dir():
        raise ProjectError(BAD_FOLDER_CODE,
                           f"clone source {raw_source!r} is not a directory")
    if not _is_git_repo(src_resolved):
        raise ProjectError(BAD_FOLDER_CODE,
                           f"clone source {raw_source!r} is not a git "
                           f"repository")
    clean_ref = _check_ref(ref)

    state = _load(cfg)
    if clean_slug in state["projects"]:
        raise ProjectError(EXISTS_CODE, f"project {clean_slug!r} exists")
    if len(state["projects"]) >= MAX_PROJECTS:
        raise ProjectError(EXISTS_CODE,
                           f"project limit reached ({MAX_PROJECTS})")
    base = _clone_root(cfg)
    try:
        base.mkdir(parents=True, exist_ok=True)
        base_resolved = base.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectError(CLONE_FAILED_CODE,
                           f"clone root unavailable: {exc}") from exc
    if base_resolved == Path("/") or base_resolved == Path.home().resolve():
        raise ProjectError(CLONE_FAILED_CODE,
                           "clone root may not be / or the home root")
    dest = base_resolved / clean_slug
    if dest.exists():
        raise ProjectError(CLONE_DEST_CODE,
                           f"clone destination already exists: {dest}")
    proc = _git(["clone", "--no-hardlinks", "--local",
                 str(src_resolved), str(dest)])
    if proc.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise ProjectError(CLONE_FAILED_CODE,
                           "git clone failed: "
                           + (proc.stderr or proc.stdout or "")[:300])
    head = ""
    if clean_ref:
        co = _git(["-c", "core.hooksPath=/dev/null",
                   "checkout", clean_ref], cwd=dest)
        if co.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise ProjectError(CHECKOUT_FAILED_CODE,
                               f"git checkout {clean_ref!r} failed: "
                               + (co.stderr or co.stdout or "")[:300])
    rev = _git(["rev-parse", "HEAD"], cwd=dest, timeout=15)
    if rev.returncode == 0:
        head = (rev.stdout or "").strip()[:64]
    try:
        out = create(cfg, clean_name, [str(dest)], slug=clean_slug,
                     description=description, primary=str(dest),
                     requested_by=requested_by, confirmed=True, use=use)
    except ProjectError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    _audit("clone", requested_by,
           {"slug": clean_slug, "ref": clean_ref, "remote": False})
    out["source"] = redact_spec(str(src_resolved))
    out["destination"] = str(dest)
    out["ref"] = clean_ref
    out["head"] = head
    return out


def checkout(cfg: Any, slug: str, ref: str, *,
             requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Check out a validated ref in a workspace's primary git folder.

    Plain ``git checkout`` inside the already-approved project root: no fetch,
    no network, no shell, hooks disarmed. Returns the resulting HEAD.
    """
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project checkout requires explicit --confirm")
    clean_ref = _check_ref(ref)
    if not clean_ref:
        raise ProjectError(BAD_NAME_CODE,
                           "project checkout requires a ref/branch/tag")
    state = _load(cfg)
    rec = _get(state, _check_slug(slug))
    if rec.get("archived", False):
        raise ProjectError(ARCHIVED_CODE,
                           f"project {rec['slug']!r} is archived; restore it "
                           f"before `checkout`")
    folder = rec.get("primary") or (rec["folders"][0] if rec["folders"] else "")
    if not folder:
        raise ProjectError(BAD_FOLDER_CODE,
                           f"project {rec['slug']!r} has no folder to check "
                           f"out")
    root = Path(folder)
    if not _is_git_repo(root):
        raise ProjectError(CHECKOUT_FAILED_CODE,
                           f"{folder!r} is not a git repository")
    co = _git(["-c", "core.hooksPath=/dev/null", "checkout", clean_ref],
              cwd=root)
    if co.returncode != 0:
        raise ProjectError(CHECKOUT_FAILED_CODE,
                           f"git checkout {clean_ref!r} failed: "
                           + (co.stderr or co.stdout or "")[:300])
    head = ""
    rev = _git(["rev-parse", "HEAD"], cwd=root, timeout=15)
    if rev.returncode == 0:
        head = (rev.stdout or "").strip()[:64]
    _touch(rec, requested_by)
    _backup(cfg)
    _save(cfg, state)
    _audit("checkout", requested_by, {"slug": rec["slug"], "ref": clean_ref})
    return {"state": "OK", "ref": clean_ref, "head": head,
            "project": _public(rec, state.get("active_slug") == rec["slug"])}


def rollback(cfg: Any, *, requested_by: str = "cli:user",
             confirmed: bool = False,
             backup: str = "") -> Dict[str, Any]:
    """Restore the registry from a backup snapshot (default: latest)."""
    if not confirmed:
        raise ProjectError(CONFIRM_CODE,
                           "project rollback requires explicit --confirm")
    if backup:
        cand = Path(os.path.expanduser(backup))
        try:
            snap = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProjectError(ROLLBACK_CODE,
                               f"backup unreadable: {exc}") from exc
    else:
        olds = _backups(cfg)
        if not olds:
            raise ProjectError(ROLLBACK_CODE, "no project backups exist")
        try:
            snap = json.loads(olds[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProjectError(ROLLBACK_CODE,
                               f"backup unreadable: {exc}") from exc
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA \
            or not isinstance(snap.get("projects"), dict):
        raise ProjectError(ROLLBACK_CODE, "backup has an unknown schema")
    _backup(cfg)
    _save(cfg, snap)
    _audit("rollback", requested_by, {})
    return {"state": "OK", "restored_projects": sorted(snap["projects"])}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before changing projects.",
            "owner": "qubit-policy",
            "rollback": "registry untouched; nothing to roll back"}
