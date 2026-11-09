"""The workspace/project mutating family: ``/api/workspaces/*`` and upload.

WHAT THE PORTED CLIENT CALLS (quoted from ``static/panels.js``)
---------------------------------------------------------------------------
``panels.js:6350``  ``GET  /api/workspaces``            → ``data.workspaces`` rows
``panels.js:6903``  ``POST /api/workspaces/add``        ``{path}``
``panels.js:6893``  ``POST /api/workspaces/rename``     ``{path, name}``
``panels.js:6807``  ``POST /api/workspaces/remove``     ``{path}``
``panels.js:6684``  ``POST /api/workspaces/reorder``    ``{paths: [...]}``
``onboarding.js:514`` ``POST /api/workspaces/add``      ``{path: workspace}``
``loadWorkspaceList`` also reads ``data.terminal_remote_backend``
(``terminal.js:501`` ``syncTerminalBackendState``).

WHERE THE DATA LIVES
---------------------------------------------------------------------------
:mod:`voicebridge.projects` — the one canonical registry (``projects.json`` under
the Qubit state dir) that ``qubit project``, the dashboard Projects page and the
voice surfaces all read and write. Nothing here opens a store of its own.

A workspace row on the wire is one project FOLDER, because that is what the
client's rows address (``w.path`` is fed straight into ``/api/terminal/start``
and into the file picker). A project with two folders therefore yields two rows,
both carrying the project's slug/name so the panel can group them.

WRITES
---------------------------------------------------------------------------
Adding/renaming/removing/reordering workspaces changes which paths the git and
terminal endpoints will accept, which is a security-relevant edit, so every one
of them goes through the SHARED confirmation journal
(:func:`voicebridge.workbench_shim.confirm_gate`) exactly like the cron and
profile writes: the first POST stages a 409 with a real ``confirmation_id``, the
re-issue applies it. The registry's own ``--confirm`` gate is satisfied by that
same settled confirmation — there is no second gate to fake.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..workbench_shim import confirm_gate

ENDPOINT = "workspaces"


def _refuse(code: str, detail: str, *, status: int = 400,
            counterpart: str = "") -> Tuple[int, Dict[str, Any]]:
    return status, {
        "ok": False,
        "state": "REFUSED",
        "code": code,
        "endpoint": ENDPOINT,
        "detail": detail,
        "owner": "qubit-policy",
        "rollback": "nothing changed; nothing to roll back",
        "qubit_counterpart": counterpart or "CLI `qubit project list`",
    }


def _projects() -> Any:
    from .. import projects as PJ

    return PJ


def row_for(path: str, project: Dict[str, Any]) -> Dict[str, Any]:
    """One folder of a project as the client's workspace row."""
    return {
        "path": path,
        "name": str(project.get("name") or project.get("slug") or path),
        "slug": str(project.get("slug") or ""),
        "project": str(project.get("name") or project.get("slug") or ""),
        "is_primary": path == project.get("primary"),
        "active": bool(project.get("active")),
        "exists": Path(path).is_dir(),
    }


def rows(cfg: Any) -> List[Dict[str, Any]]:
    """Every registered folder, in registry order (primary first)."""
    PJ = _projects()
    listed = PJ.list_projects(cfg)
    out: List[Dict[str, Any]] = []
    for rec in (listed.get("projects") if isinstance(listed, dict) else []) or []:
        if not isinstance(rec, dict):
            continue
        folders = [str(r) for r in (rec.get("folders") or []) if str(r or "").strip()]
        primary = str(rec.get("primary") or "")
        if primary in folders:
            folders.remove(primary)
            folders.insert(0, primary)
        for folder in folders:
            out.append(row_for(folder, rec))
    return out


def list_payload(cfg: Any, q: Any = None, b: Any = None,
                 p: Any = None) -> Tuple[int, Dict[str, Any]]:
    """``GET /api/workspaces`` — rows, ``last``, and the terminal backend flag."""
    try:
        items = rows(cfg)
    except Exception as exc:  # noqa: BLE001 - typed, never a bare 500
        code = getattr(exc, "code", "") or "WORKSPACE_STORE_UNAVAILABLE"
        return _refuse(
            str(code),
            f"the project registry could not be read "
            f"({type(exc).__name__}); no workspace is reported",
            status=503, counterpart="CLI `qubit project list`")
    return 200, {
        "ok": True,
        "workspaces": items,
        "count": len(items),
        "last": items[-1]["path"] if items else "",
        # The pane keeps its "embedded terminal is only for local backends"
        # path. Qubit's terminal is agent-backed, not a local shell, so False is
        # the truthful value: the emulator IS mounted, and what it runs says so.
        "terminal_remote_backend": False,
        "qubit_counterpart": "CLI `qubit project list` + dashboard Projects",
    }


# ------------------------------------------------------------------ mutations
def _gate(cfg: Any, action: str, detail: str, b: Dict[str, Any]
          ) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Stage or settle the shared confirmation for a registry write."""
    return confirm_gate(cfg, action, detail, {}, b or {}, {})


def _body_path(b: Dict[str, Any]) -> str:
    return str((b or {}).get("path") or (b or {}).get("workspace") or "").strip()


def add(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/workspaces/add {path}`` — register an existing folder."""
    PJ = _projects()
    path = _body_path(b or {})
    if not path:
        return _refuse("MALFORMED", "path is required",
                       counterpart="CLI `qubit project add-folder <path> --confirm`")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return _refuse("MALFORMED",
                       f"path must be absolute; got {path!r}",
                       counterpart="CLI `qubit project add-folder`")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        return _refuse("MALFORMED", f"cannot resolve {path!r} ({exc})")
    if not resolved.is_dir():
        return _refuse(
            "WORKSPACE_NOT_A_DIRECTORY",
            f"{str(resolved)!r} is not an existing directory; a workspace is a "
            f"real folder Qubit can read and run in",
            counterpart="CLI `qubit project add-folder <path> --confirm`")
    name = str((b or {}).get("name") or resolved.name or str(resolved))
    staged = _gate(cfg, f"workspaces.add:{resolved}", (
        f"adding {str(resolved)!r} to Qubit's project registry, which makes it "
        f"a workspace root the git and terminal endpoints may operate in"),
        dict(b or {}))
    if staged is not None:
        return staged
    try:
        active = PJ.active_project(cfg)
        project = (active.get("project") or {}) if isinstance(active, dict) else {}
        # Add the folder to the active project when there is one, else create a
        # project named after the folder. Both are the registry's own verbs.
        if project.get("slug"):
            PJ.add_folder(cfg, str(project["slug"]), str(resolved),
                          requested_by="dashboard:user", confirmed=True)
        else:
            PJ.create(cfg, name, [str(resolved)], requested_by="dashboard:user",
                      confirmed=True, use=True)
    except Exception as exc:  # noqa: BLE001 - registry errors are typed codes
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        return _refuse(code, str(getattr(exc, "detail", "") or exc),
                       counterpart="CLI `qubit project add-folder`")
    code, payload = list_payload(cfg)
    payload["added"] = str(resolved)
    return code, payload


def rename(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/workspaces/rename {path, name}`` — rename the PROJECT."""
    PJ = _projects()
    path = _body_path(b or {})
    name = str((b or {}).get("name") or "").strip()
    if not path or not name:
        return _refuse("MALFORMED", "path and name are required",
                       counterpart="CLI `qubit project rename <slug> <name> --confirm`")
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return _refuse("MALFORMED", f"cannot resolve {path!r} ({exc})")
    slug = _slug_for_path(cfg, resolved)
    if not slug:
        return _refuse(
            "WORKSPACE_NOT_FOUND",
            f"no registered workspace contains {str(resolved)!r}; nothing was "
            f"renamed", status=404,
            counterpart="CLI `qubit project list`")
    staged = _gate(cfg, f"workspaces.rename:{slug}", (
        f"renaming project {slug!r} to {name!r} — the project id does not "
        f"change, so no workspace path moves"), dict(b or {}))
    if staged is not None:
        return staged
    try:
        PJ.rename(cfg, slug, name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        return _refuse(code, str(getattr(exc, "detail", "") or exc),
                       counterpart="CLI `qubit project rename`")
    code, payload = list_payload(cfg)
    payload["renamed"] = {"path": str(resolved), "name": name}
    return code, payload


def remove(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/workspaces/remove {path}`` — unregister a FOLDER.

    Unregistering removes the path from the set the git/terminal endpoints are
    confined to; the folder itself is never touched on disk. Removing the last
    folder of a project archives the project (the registry's own rule) rather
    than leaving an empty one.
    """
    PJ = _projects()
    path = _body_path(b or {})
    if not path:
        return _refuse("MALFORMED", "path is required",
                       counterpart="CLI `qubit project remove-folder`")
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return _refuse("MALFORMED", f"cannot resolve {path!r} ({exc})")
    slug = _slug_for_path(cfg, resolved)
    if not slug:
        return _refuse(
            "WORKSPACE_NOT_FOUND",
            f"{str(resolved)!r} is not a registered workspace root; nothing was "
            f"removed", status=404, counterpart="CLI `qubit project list`")
    staged = _gate(cfg, f"workspaces.remove:{resolved}", (
        f"unregistering {str(resolved)!r}: git and terminal requests for it "
        f"will be refused from then on. The folder on disk is not deleted."),
        dict(b or {}))
    if staged is not None:
        return staged
    try:
        PJ.remove_folder(cfg, slug, str(resolved), requested_by="dashboard:user",
                         confirmed=True)
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        return _refuse(code, str(getattr(exc, "detail", "") or exc),
                       counterpart="CLI `qubit project remove-folder`")
    code, payload = list_payload(cfg)
    payload["removed"] = str(resolved)
    return code, payload


def reorder(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/workspaces/reorder {paths}`` — preview only.

    The panel drag-sorts rows and posts the new order. Qubit's registry orders
    folders by registration and offers no order field, so honouring this would
    mean writing an ordering the CLI, the Projects page and the voice surfaces
    do not share — a second source of truth for the same list. The request is
    answered with a typed refusal plus the order Qubit actually holds, and
    nothing is written.
    """
    wanted = (b or {}).get("paths")
    if not isinstance(wanted, list) or not wanted:
        return _refuse("MALFORMED", "paths must be a non-empty list")
    try:
        current = rows(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse(str(getattr(exc, "code", "") or "STORE_UNAVAILABLE"),
                       f"the project registry could not be read "
                       f"({type(exc).__name__})", status=503)
    return 501, {
        "ok": False,
        "state": "UNSUPPORTED",
        "code": "WORKSPACE_ORDER_UNSUPPORTED",
        "endpoint": ENDPOINT + "/reorder",
        "detail": (
            "Qubit's project registry has no explicit workspace order — it lists "
            "folders in registration order for the CLI, the Projects page and "
            "voice. The drag order was not saved, and the list is unchanged."),
        "workspaces": current,
        "requested_order": [str(x) for x in wanted][:200],
        "owner": "qubit-policy",
        "rollback": "nothing was written; nothing to roll back",
        "qubit_counterpart": "CLI `qubit project list` (registration order)",
    }


def _slug_for_path(cfg: Any, path: Path) -> str:
    """The slug of the project that records ``path`` as one of its folders."""
    try:
        listed = _projects().list_projects(cfg)
    except Exception:  # noqa: BLE001
        return ""
    target = str(path)
    for rec in (listed.get("projects") if isinstance(listed, dict) else []) or []:
        if not isinstance(rec, dict):
            continue
        folders = {str(Path(str(f)).expanduser().resolve())
                   for f in (rec.get("folders") or []) if str(f or "").strip()}
        if target in folders:
            return str(rec.get("slug") or "")
    return ""


# --------------------------------------------------------------------- upload
def upload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/workspace/upload`` — REFUSED: no direct workspace write.

    Upstream drops a file into the session workspace. Qubit's file mutations are
    brokered, confirmed, reversible actions with a receipt
    (``voicebridge.file_patch`` through ``voicebridge.broker``); a viewer write
    lane would bypass the journal, so this answers a typed refusal naming the
    real path. It is NOT an empty success: the pane must say the file is not
    there.
    """
    return 403, {
        "ok": False,
        "state": "REFUSED",
        "code": "WB_BROKERED_WRITE",
        "endpoint": "workspace/upload",
        "detail": (
            "uploading into a workspace is a file write: Qubit file mutations "
            "are brokered, confirmed, reversible actions with an audit receipt, "
            "never a viewer upload. Nothing was written."),
        "owner": "qubit-policy",
        "rollback": "nothing written; nothing to roll back",
        "qubit_counterpart": "POST /api/fs/write-text (broker confirmation_id) "
                             "or the Qubit file patch workflow",
    }
