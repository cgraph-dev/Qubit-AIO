"""OC-48 (no-credential parts): markdown workspace memory + dreaming.

The Packet 5B :class:`voicebridge.memory.store.MemoryStore` (SQLite/FTS5) is
the structured memory authority. Upstream OpenClaw additionally offers a
*markdown workspace* memory: agent notes in the workspace and a periodic
"dreaming" consolidation pass. This module provides that surface locally:

* bounded plain-text notes persisted one file per note (``<name>.md``) under
  ``<state_dir>/memory/workspace`` with a JSON index;
* a deterministic, offline ``dream`` pass that drops empty/duplicate notes
  (keeping the newest) and writes a bounded ``dreaming.md`` summary;
* a typed refusal for every external/cloud provider (LanceDB/Wiki/Honcho
  style sync): the local-only contract means no memory ever leaves the
  machine, so there is nothing to configure with credentials.

Contracts: name slugs only (no traversal), secret redaction at the boundary,
atomic 0600 writes with torn-index recovery (the index is rebuilt from the
note files), emergency-stop fail-closed on mutations, and redacted audit
events through :func:`voicebridge.logging_setup.log_event`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .store import norm_hash, redact_secrets

SCHEMA = "qubit-memory-workspace/1"
WORKSPACE_DIRNAME = "workspace"
INDEX_NAME = "index.json"
DREAM_NAME = "dreaming.md"

MAX_NOTE_BODY = 20000
MAX_NOTES = 500
MAX_TAGS = 16
MAX_TAG_LEN = 40
MAX_SUMMARY = 8000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

ESTOP_CODE = "MEMORY_WORKSPACE_ESTOP"
CONFIRM_CODE = "MEMORY_WORKSPACE_CONFIRM_REQUIRED"
BAD_INPUT_CODE = "MEMORY_WORKSPACE_BAD_INPUT"
NOT_FOUND_CODE = "MEMORY_WORKSPACE_NOT_FOUND"
CORRUPT_CODE = "MEMORY_WORKSPACE_CORRUPT"
EXISTS_CODE = "MEMORY_WORKSPACE_EXISTS"
UNAVAILABLE_CODE = "MEMORY_PROVIDER_UNAVAILABLE"


class WorkspaceError(Exception):
    """Typed workspace-memory failure (never a traceback at the boundary)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------- boundaries
def _redact(text: Any) -> str:
    from ..redaction import redact_text

    return redact_text(redact_secrets("" if text is None else str(text)))


def estop_engaged(cfg: Any) -> bool:
    """Durable emergency-stop / global-disable probe; fails closed."""
    try:
        from ..plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True


def _refuse(code: str, state: str, detail: str) -> Dict[str, Any]:
    return {"state": state, "code": code, "detail": detail, "notes": []}


# ------------------------------------------------------------------- paths
def workspace_dir(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / "memory" / WORKSPACE_DIRNAME


def _index_path(cfg: Any) -> Path:
    return workspace_dir(cfg) / INDEX_NAME


def _note_path(cfg: Any, name: str) -> Path:
    return workspace_dir(cfg) / f"{name}.md"


def _check_name(name: Any) -> str:
    clean = str(name or "").strip().lower()
    if not NAME_RE.match(clean):
        raise WorkspaceError(BAD_INPUT_CODE,
                             "note name must be a safe slug "
                             "(a-z0-9._-, max 64)")
    return clean


def _check_body(body: Any) -> str:
    text = "" if body is None else str(body)
    if len(text) > MAX_NOTE_BODY:
        raise WorkspaceError(BAD_INPUT_CODE,
                             f"note body must be <={MAX_NOTE_BODY} chars")
    return _redact(text)


def _check_tags(tags: Any) -> List[str]:
    if tags is None or tags == "":
        return []
    if isinstance(tags, str):
        tags = [t for t in tags.replace(",", " ").split() if t]
    if not isinstance(tags, (list, tuple)):
        raise WorkspaceError(BAD_INPUT_CODE, "tags must be a list")
    if len(tags) > MAX_TAGS:
        raise WorkspaceError(BAD_INPUT_CODE,
                             f"too many tags (max {MAX_TAGS})")
    clean: List[str] = []
    for tag in tags:
        text = _redact(tag).strip().lower()
        if not text:
            continue
        if len(text) > MAX_TAG_LEN:
            raise WorkspaceError(BAD_INPUT_CODE,
                                 f"tag must be <={MAX_TAG_LEN} chars")
        if text not in clean:
            clean.append(text)
    return clean


# ------------------------------------------------------------------- index
def _blank_index() -> Dict[str, Any]:
    return {"schema": SCHEMA, "notes": {}, "dreams": 0}


def _load_index(cfg: Any) -> Dict[str, Any]:
    path = _index_path(cfg)
    if not path.is_file():
        return _blank_index()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A torn/corrupt index is recoverable: rebuild from the note files.
        return _rebuild_index(cfg)
    if not isinstance(raw, dict) or not isinstance(raw.get("notes"), dict):
        return _rebuild_index(cfg)
    return raw


def _rebuild_index(cfg: Any) -> Dict[str, Any]:
    """Reconstruct the index from the durable note files (recovery path)."""
    index = _blank_index()
    directory = workspace_dir(cfg)
    if not directory.is_dir():
        return index
    for path in sorted(directory.glob("*.md")):
        if path.name == DREAM_NAME:
            continue
        name = path.stem.lower()
        if not NAME_RE.match(name):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        index["notes"][name] = {
            "name": name,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "size": len(body),
            "created_at": path.stat().st_mtime,
            "updated_at": path.stat().st_mtime,
            "tags": [],
        }
    return index


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _save_index(cfg: Any, index: Dict[str, Any]) -> None:
    _atomic_write(_index_path(cfg),
                  json.dumps(index, sort_keys=True, separators=(",", ":")))


# ------------------------------------------------------------------- write
def write_note(cfg: Any, name: Any, body: Any = "", *, tags: Any = None,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Persist one markdown workspace note (confirmed, redacted, bounded)."""
    if estop_engaged(cfg):
        return _refuse(ESTOP_CODE, "ESTOP",
                       "workspace memory is fail-closed while the emergency "
                       "stop is latched")
    if not confirmed:
        return _refuse(CONFIRM_CODE, "denied",
                       "writing workspace memory requires --confirm")
    clean = _check_name(name)
    text = _check_body(body)
    clean_tags = _check_tags(tags)
    index = _load_index(cfg)
    existing = index["notes"].get(clean)
    if existing is None and len(index["notes"]) >= MAX_NOTES:
        raise WorkspaceError(EXISTS_CODE,
                             f"workspace note limit reached ({MAX_NOTES})")
    now = time.time()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _atomic_write(_note_path(cfg, clean), text)
    index["notes"][clean] = {
        "name": clean, "sha256": digest, "size": len(text),
        "created_at": (existing or {}).get("created_at", now),
        "updated_at": now, "tags": clean_tags,
    }
    _save_index(cfg, index)
    from ..logging_setup import log_event

    log_event("memory.workspace.write", name=clean,
              requested_by=requested_by, size=len(text),
              sha256=digest)
    return {"state": "OK", "note": dict(index["notes"][clean]),
            "created": existing is None}


def read_note(cfg: Any, name: Any) -> Dict[str, Any]:
    """Read one note (read-only, redacted, never fabricates)."""
    clean = _check_name(name)
    path = _note_path(cfg, clean)
    if not path.is_file():
        return {"state": "NOT_FOUND", "code": NOT_FOUND_CODE,
                "detail": f"no workspace note {clean!r}", "notes": []}
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"state": "UNAVAILABLE", "code": CORRUPT_CODE,
                "detail": f"note unreadable: {exc}"[:160], "notes": []}
    index = _load_index(cfg)
    meta = index["notes"].get(clean, {"name": clean})
    return {"state": "OK",
            "note": {"name": clean, "body": _redact(body)[:MAX_NOTE_BODY],
                     "tags": list(meta.get("tags", [])),
                     "sha256": hashlib.sha256(
                         body.encode("utf-8")).hexdigest()}}


def list_notes(cfg: Any) -> Dict[str, Any]:
    """List note metadata newest-first; the index self-heals from disk."""
    index = _load_index(cfg)
    notes = []
    for name, meta in index["notes"].items():
        if not _note_path(cfg, name).is_file():
            continue
        notes.append({"name": name,
                      "size": int(meta.get("size", 0)),
                      "tags": list(meta.get("tags", [])),
                      "updated_at": meta.get("updated_at", 0)})
    notes.sort(key=lambda n: (n["updated_at"], n["name"]), reverse=True)
    return {"state": "OK", "count": len(notes), "notes": notes}


def forget_note(cfg: Any, name: Any, *, requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Delete one note (confirmed; index entry removed)."""
    if estop_engaged(cfg):
        return _refuse(ESTOP_CODE, "ESTOP",
                       "workspace memory is fail-closed while the emergency "
                       "stop is latched")
    if not confirmed:
        return _refuse(CONFIRM_CODE, "denied",
                       "forgetting workspace memory requires --confirm")
    clean = _check_name(name)
    path = _note_path(cfg, clean)
    if not path.is_file():
        return {"state": "NOT_FOUND", "code": NOT_FOUND_CODE,
                "detail": f"no workspace note {clean!r}", "notes": []}
    try:
        path.unlink()
    except OSError as exc:
        return {"state": "UNAVAILABLE", "code": CORRUPT_CODE,
                "detail": f"note could not be removed: {exc}"[:160],
                "notes": []}
    index = _load_index(cfg)
    index["notes"].pop(clean, None)
    _save_index(cfg, index)
    from ..logging_setup import log_event

    log_event("memory.workspace.forget", name=clean,
              requested_by=requested_by)
    return {"state": "OK", "forgotten": clean}


# ------------------------------------------------------------------ dream
def dream(cfg: Any, *, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Offline dreaming: dedupe/empty-prune notes and write a summary.

    Deterministic and local-only. Duplicate notes (same ``norm_hash``) keep
    the newest file; empty notes are removed; a bounded ``dreaming.md``
    summary of the surviving notes is written. The index is refreshed."""
    if estop_engaged(cfg):
        return _refuse(ESTOP_CODE, "ESTOP",
                       "workspace memory is fail-closed while the emergency "
                       "stop is latched")
    if not confirmed:
        return _refuse(CONFIRM_CODE, "denied",
                       "dreaming over workspace memory requires --confirm")
    directory = workspace_dir(cfg)
    index = _load_index(cfg)
    removed_empty: List[str] = []
    removed_dupe: List[str] = []
    seen: Dict[str, str] = {}
    paths = list(directory.glob("*.md"))
    paths.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for path in paths:
        if path.name == DREAM_NAME:
            continue
        name = path.stem.lower()
        if not NAME_RE.match(name):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not body.strip():
            path.unlink()
            index["notes"].pop(name, None)
            removed_empty.append(name)
            continue
        key = norm_hash(body)
        if key in seen:
            path.unlink()
            index["notes"].pop(name, None)
            removed_dupe.append(name)
            continue
        seen[key] = name
    # Rebuild summary from the notes that survived (bounded, redacted).
    lines = ["# Dreaming summary", "",
             f"Notes consolidated: {len(seen)}", ""]
    for path in sorted(directory.glob("*.md")):
        if path.name == DREAM_NAME:
            continue
        name = path.stem.lower()
        if name not in seen.values():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()),
                     "")
        lines.append(f"- **{name}**: {_redact(first)[:200]}")
        if sum(len(x) for x in lines) >= MAX_SUMMARY:
            lines.append("- … summary truncated")
            break
    _atomic_write(directory / DREAM_NAME, "\n".join(lines) + "\n")
    index["dreams"] = int(index.get("dreams", 0)) + 1
    _save_index(cfg, index)
    from ..logging_setup import log_event

    log_event("memory.workspace.dream", requested_by=requested_by,
              kept=len(seen), removed_empty=len(removed_empty),
              removed_dupe=len(removed_dupe))
    return {"state": "OK", "kept": len(seen),
            "removed_empty": sorted(removed_empty),
            "removed_dupe": sorted(removed_dupe),
            "summary": str(directory / DREAM_NAME)}


# -------------------------------------------------------------- providers
def providers() -> Dict[str, Any]:
    """Honest provider inventory: one local provider, all cloud refused.

    The local markdown workspace needs no credentials. Every external
    provider (LanceDB/Wiki/Honcho-style sync) is typed UNAVAILABLE by the
    local-only policy: no memory leaves the machine, so no credential can
    ever be required for it."""
    external = ("lancedb", "wiki", "honcho", "cloud-sync")
    return {"state": "OK", "providers": [
        {"name": "markdown-workspace", "state": "AVAILABLE",
         "code": "OK", "detail": "local, credential-free"},
        *[{"name": name, "state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
           "detail": "local-only policy: external memory sync is refused"}
          for name in external]]}
