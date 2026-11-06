"""Workbench workspace adapter — the file tree the workbench's right rail shows.

WHAT THIS IS
---------------------------------------------------------------------------
The workbench (absorbed from ``hermes-webui``) has a three-panel layout whose
third panel is a workspace file browser: a directory tree with an inline
preview. The upstream implementation lives in ``api/workspace.py`` (listing
``:1632-1841``, preview ``:1866-1889``) and its own route handler
``api/routes.py::_handle_list_dir`` (``:23502-23570``), backed by an anchored-fd
security layer and a *write* surface.

CONFINEMENT IS QUBIT'S, NOT UPSTREAM'S
---------------------------------------------------------------------------
Nothing here re-implements a path check. Every read goes through
:mod:`voicebridge.files`, Qubit's own approved-root confinement
(``files._resolve_inside``: registered project folders only, ``..`` traversal
and escaping symlinks refused, special files refused) plus the transcript-style
redaction and byte caps. The upstream path-escape GRANT model
(``_escape_authorize`` / token store) is deliberately NOT adopted — it is a
security surface Qubit did not design, and it would widen the roots this page
can read beyond the project folders the owner registered.

READ-ONLY, BY CONSTRUCTION
---------------------------------------------------------------------------
Two capabilities, both reads: :func:`list_tree` and :func:`preview_file`. There
is no write, no mkdir, no delete, no rename, no upload — in Qubit those are
mutations that belong to the brokered confirmation journal
(``voicebridge/file_patch.py`` through ``voicebridge/broker.py``), not to a
viewer. A caller that wants to write is answered a typed refusal pointing at
that journal, never a silent no-op.

WHAT OF UPSTREAM'S LISTING RULES IS KEPT (presentation contract)
---------------------------------------------------------------------------
* directories first, then files, then symlinks (upstream sort rank);
* a hard cap per directory (upstream: 200);
* ``mtime_ns`` serialized as a string (upstream
  ``_browser_timestamp_ns``/``serialize_workspace_entries_for_browser``);
* symlinks whose target is broken or loops are filtered (upstream ``:1676``);
* a symlink whose target escapes the root is shown but **without disclosing
  where it points** — no resolved path, no target-derived size or is_dir
  (upstream ``:1730-1745``, "do not disclose where it points").

Public contract (pure functions, JSON-ready dicts, never raise)::

    list_tree(cfg, path="")
    preview_file(cfg, path, workspace="")
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

UPSTREAM = "github.com/nesquena/hermes-webui@3d9488fca6bb248c77f988bfbffcca9d8cfd9561"

#: Per-directory cap. Same number as upstream's own listing bound. A hit here
#: sets ``truncated`` — the page says "showing 200 of more", it never silently
#: pretends the folder has 200 entries.
MAX_ENTRIES = 200


def _refusal(code: str, detail: str, **extra: Any) -> Dict[str, Any]:
    # ``*_REFUSED`` / ``*_REQUIRED`` codes are policy refusals (caller error);
    # the rest are capability faults — both are typed, never empty data.
    refused = "REFUSED" in code or "REQUIRED" in code
    out: Dict[str, Any] = {"state": "REFUSED" if refused else "UNAVAILABLE",
                           "ok": False, "code": code, "detail": detail,
                           "owner": "qubit-policy",
                           "rollback": "read-only; nothing to roll back"}
    out.update(extra)
    return out


def _project_folder(cfg: Any) -> str:
    """The active project's first folder that still exists, or ``""``.

    Deliberately NOT ``files.active_root``: that helper answers with a cwd
    fallback when a project's recorded folders have all vanished, and the
    workbench must never browse the server's working directory just because it
    happened to be there. This is ``projects.active_folder`` — the project's
    own recorded folders only.
    """
    try:
        from .. import projects as PJ

        return str(PJ.active_folder(cfg) or "")
    except Exception:
        return ""


def _within_roots(cfg: Any, folder: str) -> Optional[str]:
    """The registered project root *folder* resolves inside, or ``None``.

    Uses ``files.approved_roots`` — the project-registered folders only, WITHOUT
    the stale-project cwd fallback that ``files.read_roots`` adds. The workbench
    never reads a folder just because the process happened to be there.
    """
    if not folder:
        return None
    try:
        target = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    try:
        from .. import files as F

        roots = F.approved_roots(cfg)
    except Exception:
        return None
    for root in roots:
        try:
            target.relative_to(root)
            return str(root)
        except ValueError:
            continue
    return None


def _files_error(exc: Exception) -> Dict[str, Any]:
    """Translate ``voicebridge.files.FilesError`` into the workbench envelope.

    The files layer's own code is preserved (``FILES_OUTSIDE_ROOT``,
    ``FILES_BINARY``, ``FILES_TOO_LARGE`` …) so the UI can distinguish "outside
    your project folders" from "too big to preview" — two different answers
    that must not collapse into one empty panel.
    """
    code = str(getattr(exc, "code", "") or "FILES_IO")
    detail = str(getattr(exc, "detail", "") or exc)
    if code.endswith("OUTSIDE_ROOT"):
        detail = (detail + " — the workbench reads only files inside the "
                  "project folders registered in Qubit.")
    return {"state": "REFUSED" if code.endswith(("OUTSIDE_ROOT", "BAD_INPUT"))
            else "UNAVAILABLE",
            "ok": False, "code": code, "detail": detail,
            "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back"}


def _root(cfg: Any) -> str:
    from .. import files as F

    try:
        return F.active_root(cfg)
    except Exception:
        return ""


def _roots(cfg: Any) -> List[str]:
    from .. import files as F

    try:
        return [str(r) for r in F.read_roots(cfg)]
    except Exception:
        return []


def _entry(path: Path, rel_dir: str, root: Path) -> Dict[str, Any]:
    """One wire row — upstream's shape, minus every disclosure upstream drops.

    ``path`` is workspace-relative (``rel_dir/name``), never absolute: the page
    navigates by joining against the workspace it already knows, so the server
    keeps no path knowledge the client does not.
    """
    rel = f"{rel_dir}/{path.name}" if rel_dir else path.name
    sort_rank = 1
    try:
        is_link = path.is_symlink()
        lst = path.lstat() if is_link else path.stat()
    except OSError:
        return {"name": path.name[:120], "path": rel[:300], "type": "unknown",
                "size": None, "mtime_ns": None, "sort_rank": 3}
    mtime_ns = str(int(getattr(lst, "st_mtime_ns", 0) or 0))
    if is_link:
        # Broken / looping link: filter it (upstream :1676). A link whose
        # follow-stat raises can never be opened, so listing it as navigable
        # would be a lie.
        try:
            target = path.resolve(strict=True)
            target.stat()
        except (OSError, RuntimeError):
            return {}
        try:
            inside = target.relative_to(root)
            _ = inside
            escaped = False
        except ValueError:
            escaped = True
        if escaped:
            # Display-only row: the link EXISTS (say so) but where it points is
            # not disclosed (upstream #4581 hardening, :1730-1745).
            return {"name": path.name[:120], "path": rel[:300],
                    "type": "symlink", "size": None, "mtime_ns": mtime_ns,
                    "sort_rank": 0, "target_outside_workspace": True}
        try:
            is_dir = target.is_dir()
            size = None if is_dir else int(target.stat().st_size)
        except OSError:
            return {}
        return {"name": path.name[:120], "path": rel[:300], "type": "symlink",
                "size": size, "mtime_ns": mtime_ns, "sort_rank": 0,
                "target_outside_workspace": False}
    try:
        is_dir = stat.S_ISDIR(lst.st_mode)
        is_file = stat.S_ISREG(lst.st_mode)
    except (AttributeError, ValueError):
        is_dir, is_file = False, False
    if not (is_dir or is_file):
        return {}
    sort_rank = 1 if is_dir else 2
    return {"name": path.name[:120], "path": rel[:300],
            "type": "dir" if is_dir else "file",
            "size": None if is_dir else int(getattr(lst, "st_size", 0) or 0),
            "mtime_ns": mtime_ns,
            "sort_rank": sort_rank}


def _sort_key(entry: Dict[str, Any]) -> Tuple[int, str]:
    return (int(entry.get("sort_rank") or 3), str(entry.get("name") or "").lower())


def list_tree(cfg: Any = None, path: str = "") -> Dict[str, Any]:
    """List one workspace folder, read-only, confined to the project roots.

    Empty ``path`` lists the active project's primary folder — the same default
    ``voicebridge.files.inspect("")`` uses, so the workbench rail and the Files
    page open on the same folder. A folder outside the registered roots is
    refused with ``FILES_OUTSIDE_ROOT``; it is never silently replaced by the
    root listing (that would look like a successful navigation).
    """
    from .. import files as F

    target_text = str(path or "").strip()
    explicit = bool(target_text)
    listing: Dict[str, Any] = {}
    try:
        if not explicit:
            project_root = _project_folder(cfg)
            if not project_root:
                active: Dict[str, Any] = {}
                try:
                    from .. import projects as PJ

                    active = PJ.active_project(cfg)
                except Exception:
                    active = {}
                if not isinstance(active, dict) \
                        or active.get("state") != "OK" \
                        or not active.get("project"):
                    return {"state": "EMPTY", "ok": True,
                            "code": "NO_ACTIVE_PROJECT",
                            "detail": "no active Qubit project, so the "
                                      "workbench has no folder to show. Set "
                                      "one with `qubit project use <slug> "
                                      "--confirm`.",
                            "entries": [], "folder": "", "workspace": "",
                            "roots": [], "truncated": False,
                            "read_only": True}
                # The project exists but every folder it recorded has vanished.
                # ``files.active_root`` answers with a cwd FALLBACK in this
                # case; the workbench does not browse whatever directory the
                # server happens to run in, so it names the stale project.
                return {"state": "EMPTY", "ok": True,
                        "code": "PROJECT_HAS_NO_FOLDERS",
                        "detail": "the active Qubit project's recorded folder "
                                  "no longer exists, so the workbench has no "
                                  "folder to show. Re-register it with "
                                  "`qubit project add-folder`.",
                        "entries": [], "folder": "", "workspace": "",
                        "roots": _roots(cfg), "truncated": False,
                        "read_only": True}
            target_text = project_root
            listing = F.list_children(cfg, target_text)
        else:
            listing = F.list_children(cfg, target_text)
        # An EXPLICIT path must prove it is confined, and the DEFAULT folder
        # must be a registered project folder too. ``list_children`` refuses
        # escapes using ``files.read_roots``, which adds a stale-project cwd
        # fallback — so a caller could browse the process working directory.
        # The workbench looks only at folders registered on a Qubit project, so
        # re-check membership here (both branches) and refuse. Never quietly
        # fall back to the root listing: that would look like a successful
        # navigation.
        base_root = _within_roots(cfg, str(listing.get("folder") or ""))
        if base_root is None:
            where = repr(target_text) if explicit else (
                f"the active project folder {target_text!r}")
            return _refusal(
                "FILES_OUTSIDE_ROOT",
                f"{where} is not inside a project folder registered in "
                f"Qubit, so the workbench will not read it. Register it "
                f"with `qubit project add-folder` first.",
                entries=[], folder="", workspace="",
                roots=_roots(cfg), read_only=True)
    except F.FilesError as exc:
        return _files_error(exc)
    except Exception as exc:  # noqa: BLE001 - a fault is a typed answer
        return _refusal("WORKBENCH_FILES_LIST_FAILED",
                        f"voicebridge.files could not list "
                        f"{target_text!r}: {type(exc).__name__}: {exc}",
                        entries=[], folder="", workspace="")

    # Everything below enumerates ``folder`` — the CONFINED path the files
    # layer resolved, never the raw request. ``Path("")`` would silently mean
    # the process cwd, which is why the raw argument is not used here.
    folder = str(listing.get("folder") or "").strip()[:300]
    if not folder:
        return _refusal("WORKBENCH_FILES_LIST_FAILED",
                        "the files layer returned no resolved folder, so "
                        "there is nothing to enumerate",
                        entries=[], folder="", workspace="")
    roots = _roots(cfg)
    # Resolve the listing folder to the root it lives under, so entry paths are
    # workspace-relative exactly like upstream's ``rel``. ``base_root`` was
    # proven above, so this always finds one.
    root: Path = Path(base_root)
    try:
        resolved = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return _refusal("WORKBENCH_FILES_LIST_FAILED",
                        f"cannot resolve the listing folder ({exc})",
                        entries=[], folder=folder, workspace=str(root))
    try:
        rel_dir = "" if resolved == root else str(resolved.relative_to(root))
    except ValueError:
        # Proven-inside above; a mismatch here means the files layer resolved
        # something other than what it returned. Refuse rather than list a
        # folder outside the proven root.
        return _refusal("FILES_OUTSIDE_ROOT",
                        f"{folder!r} did not resolve inside {str(root)!r}",
                        entries=[], folder="", workspace="")
    if rel_dir == ".":
        rel_dir = ""

    raw_paths: List[Path] = []
    try:
        with os.scandir(str(resolved)) as scan:
            for child in scan:
                raw_paths.append(Path(child.path))
                if len(raw_paths) > MAX_ENTRIES:
                    break
    except OSError as exc:
        return _refusal("WORKBENCH_FILES_LIST_FAILED",
                        f"cannot enumerate {folder!r} ({exc})",
                        entries=[], folder=folder, workspace=str(root))
    truncated = len(raw_paths) > MAX_ENTRIES
    entries: List[Dict[str, Any]] = []
    for child in raw_paths:
        row = _entry(child, rel_dir, root)
        if row:
            entries.append(row)
        if len(entries) >= MAX_ENTRIES:
            break
    entries.sort(key=_sort_key)
    # A filtered symlink also makes the list shorter than the folder — say so.
    if len(entries) < len(raw_paths):
        truncated = True
    return {
        "state": "OK", "ok": True, "code": None, "detail": "",
        "entries": entries,
        "shown": len(entries),
        "truncated": bool(truncated),
        "path": rel_dir,
        "folder": folder,
        "workspace": str(root),
        "roots": roots,
        "read_only": True,
        "confinement": "voicebridge.files (approved project roots)",
        "upstream": UPSTREAM,
    }


def preview_file(cfg: Any = None, path: str = "", *,
                 workspace: str = "",
                 max_chars: int = 20000) -> Dict[str, Any]:
    """Inline preview of one text file, read-only, confined, redacted.

    ``path`` is workspace-relative, exactly as :func:`list_tree` returns it, and
    ``workspace`` is the root it is relative to (``list_tree``'s ``workspace``
    field). The two together are joined and re-checked against the registered
    project roots, so a relative preview can never drift onto the process cwd or
    the "active project" the server happens to have. ``workspace`` may be
    omitted when ``path`` is already absolute.

    The bytes come from ``voicebridge.files.read_file`` — the same bounded,
    redacted read the Files page and the voice ``files read`` capability use —
    so the preview cannot show anything another Qubit read path would refuse.
    Binary content and oversized files are refusals carrying the files layer's
    own code (``FILES_BINARY`` / ``FILES_TOO_LARGE``), not an empty preview.
    """
    from .. import files as F

    target = str(path or "").strip()
    if not target:
        return {"state": "REFUSED", "ok": False,
                "code": "WORKBENCH_FILES_PATH_REQUIRED",
                "detail": "preview_file needs a file path",
                "owner": "qubit-policy",
                "rollback": "read-only; nothing to roll back"}
    base = str(workspace or "").strip()
    if base and not Path(target).is_absolute():
        base_root = _within_roots(cfg, base)
        if base_root is None:
            return _refusal(
                "FILES_OUTSIDE_ROOT",
                f"workspace {base!r} is not a project folder registered in "
                f"Qubit, so the workbench will not read inside it.",
                entries=[], folder="", workspace="")
        target = str((Path(base_root) / target))
    elif not Path(target).is_absolute() and _project_folder(cfg) == "":
        # A bare relative path with no workspace would resolve against a root
        # the caller did not name. Refuse instead of guessing.
        return _refusal(
            "WORKBENCH_FILES_PATH_REQUIRED",
            "preview_file needs an absolute path or a workspace-relative "
            "path plus the workspace it belongs to")
    try:
        got = F.read_file(cfg, target, max_chars=max_chars)
    except F.FilesError as exc:
        return _files_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _refusal("WORKBENCH_FILES_READ_FAILED",
                        f"voicebridge.files could not read {target!r}: "
                        f"{type(exc).__name__}: {exc}")
    text = str(got.get("text") or "")
    name = Path(str(got.get("file") or target)).name
    return {
        "state": "OK", "ok": True, "code": None, "detail": "",
        "file": str(got.get("file") or "")[:300],
        "name": name,
        "size": int(got.get("size") or 0),
        "text": text,
        "clipped": bool(got.get("clipped")),
        "lines": text.count("\n") + (1 if text else 0),
        # Untrusted evidence, exactly as the files layer labelled it: the page
        # must render it as text, never execute or trust it.
        "trust": str(got.get("trust") or "untrusted-file-text"),
        "read_only": True,
        "confinement": "voicebridge.files (approved project roots)",
        "upstream": UPSTREAM,
    }


def refused_mutation(cfg: Any = None, path: str = "", *,
                     action: str = "write") -> Dict[str, Any]:
    """A mutation asked of the viewer — refused, and pointed at the journal.

    Present so a frontend stub cannot mistake "no handler" for "allowed": any
    write path the workbench might grow must answer this, not a silent 200.
    """
    return {
        "state": "REFUSED", "ok": False,
        "code": "WORKBENCH_FILES_READ_ONLY",
        "detail": (f"the workbench file browser is read-only; {action} of "
                   f"{str(path or '<path>')!r} is a mutation and runs only as "
                   f"a brokered, confirmed action with an audit receipt."),
        "qubit_counterpart": "voicebridge.file_patch via voicebridge.broker "
                             "(dashboard Operations → approvals)",
        "owner": "qubit-policy",
        "rollback": "nothing was written; nothing to roll back",
    }
