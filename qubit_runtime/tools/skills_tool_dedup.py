"""skill_view repeat-view dedup registry: per-task cache of (resolved skill name, file_path) ->
digest of the skill_view result served. A repeat view whose result is byte-identical returns a short
stub — the earlier tool result already carries the content verbatim. Content digests need no file
timestamps, so a same-size rewrite in one coarse mtime tick (or on a host whose clock disagrees with
the filesystem's) is never mistaken for "unchanged". Cleared via ``reset_skill_view_dedup()`` on
context compression AND on a committed proactive tool-result prune, because both replace the
original content with a one-line marker.
"""

import hashlib
import json
import os
import threading
from typing import Dict

_skill_view_tracker: dict[str, dict[tuple, str]] = {}
_skill_view_tracker_lock = threading.Lock()
_SKILL_VIEW_DEDUP_CAP = 200

_SKILL_VIEW_DEDUP_MESSAGE = (
    "Skill content unchanged since it was loaded earlier in this "
    "conversation — refer to the earlier skill_view result; it is still "
    "current and complete. (Re-issued after context compression, this "
    "returns the full content again.)")


def dedup_skill_view(task_id, name, file_path, payload: dict, result: str) -> str | None:
    """The "unchanged" stub when this task was already served exactly ``result`` for this skill
    file; otherwise record ``result``'s digest and return None (serve the full result)."""
    # Never dedup setup-needed views: readiness depends on config/env state that
    # changes without the file changing; the model must see the refreshed status.
    if (not task_id or payload.get("setup_needed")
            or payload.get("readiness_status") == "setup_needed"):
        return None
    resolved = str(payload.get("name") or name)
    key = (resolved, file_path or "")
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    with _skill_view_tracker_lock:
        cache = _skill_view_tracker.setdefault(str(task_id), {})
        if cache.get(key) == digest:
            return json.dumps({
                "success": True, "status": "unchanged", "name": resolved,
                "file": file_path or "SKILL.md", "dedup": True, "content_returned": False,
                "message": _SKILL_VIEW_DEDUP_MESSAGE}, ensure_ascii=False)
        cache.pop(key, None)  # re-insert at the end: FIFO eviction keeps the recently served
        cache[key] = digest
        while len(cache) > _SKILL_VIEW_DEDUP_CAP:  # FIFO eviction
            del cache[next(iter(cache))]
    return None


def _skill_view_fingerprint(payload: dict) -> tuple | None:
    """Stat the skill file a successful skill_view served, for change detection."""
    if not (src := payload.get("_source_path")):
        return None
    try:
        st = os.stat(src)
        return (src, st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _record_skill_view(task_id, name, file_path, payload: dict) -> None:
    """Record a served skill_view so an identical repeat can be deduped."""
    # Never dedup setup-needed views: readiness depends on config/env state that
    # changes without the file changing; the model must see the refreshed status.
    if (not task_id or payload.get("setup_needed")
            or payload.get("readiness_status") == "setup_needed"):
        return
    if (fp := _skill_view_fingerprint(payload)) is None:
        return
    key = (str(payload.get("name") or name), file_path or "")
    with _skill_view_tracker_lock:
        cache = _skill_view_tracker.setdefault(str(task_id), {})
        cache[key] = fp
        while len(cache) > _SKILL_VIEW_DEDUP_CAP:  # FIFO eviction
            del cache[next(iter(cache))]


def _check_skill_view_dedup(task_id, name, file_path) -> str | None:
    """Stub when this (skill name, file) was already served to the task in the current
    generation; None otherwise.

    Kept for callers that only know the identity, not the served result: the live path
    (``dedup_skill_view``) compares the result's content digest, which is what decides
    whether a repeat is byte-identical. This helper therefore reports "a repeat with the
    same content would dedup" — it never re-stats the file, because a same-content rewrite
    must not re-arm the cache.
    """
    if not task_id:
        return None
    n = str(name)
    with _skill_view_tracker_lock:
        cache = _skill_view_tracker.get(str(task_id)) or {}
        # Record key is the RESOLVED name; match raw and resolved forms so
        # 'category/skill' and bare-name views coalesce.
        for rec_name, rec_fp in list(cache):
            if rec_fp != (file_path or "") or (
                    rec_name != n and not n.endswith("/" + rec_name)
                    and not rec_name.endswith("/" + n) and n.split(":")[-1] != rec_name):
                continue
            return json.dumps({
                "success": True, "status": "unchanged", "name": rec_name,
                "file": file_path or "SKILL.md", "dedup": True, "content_returned": False,
                "message": _SKILL_VIEW_DEDUP_MESSAGE}, ensure_ascii=False)
    return None


def reset_skill_view_dedup(task_id: str | None = None) -> None:
    """Clear the dedup cache (all tasks when task_id is None); called on context compression."""
    with _skill_view_tracker_lock:
        if task_id is None:
            _skill_view_tracker.clear()
        else:
            _skill_view_tracker.pop(str(task_id), None)
