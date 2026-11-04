"""Bounded read helpers for the append-only voice event journals.

The voice journal (``events.jsonl``) grows without bound in normal use and can
reach hundreds of megabytes. Loading it with ``Path.read_text()`` copies the
whole file into memory just to show the newest lines — an avoidable stall and
memory spike. Everything here reads from the END of the file, bounded by
``max_bytes``, and never pulls the whole file into a string unless the caller
asks for it (``iter_lines`` streams line by line).

Pure, read-only, never raises above the file system: a missing/unreadable
path returns an empty result. No second store is created.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, List, Optional

#: Default bound for a "recent events" read. One megabyte is several thousand
#: journal records — far more than any UI tail needs.
DEFAULT_TAIL_BYTES = 1_000_000


def _as_path(path: "str | os.PathLike[str] | Path") -> Path:
    return path if isinstance(path, Path) else Path(path)


def tail_text(path: "str | os.PathLike[str] | Path",
              max_bytes: int = DEFAULT_TAIL_BYTES,
              encoding: str = "utf-8") -> str:
    """Return at most the last ``max_bytes`` of ``path`` decoded as text."""
    p = _as_path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return ""
    try:
        limit = max(1, int(max_bytes))
    except (TypeError, ValueError):
        limit = DEFAULT_TAIL_BYTES
    try:
        with p.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            data = fh.read()
    except OSError:
        return ""
    return data.decode(encoding, "replace")


def tail_lines(path: "str | os.PathLike[str] | Path",
               max_bytes: int = DEFAULT_TAIL_BYTES,
               limit: Optional[int] = None,
               *, drop_partial: bool = True) -> List[str]:
    """Newest lines of ``path``, bounded by bytes (and optionally count).

    When the read started mid-file, ``drop_partial`` removes the first,
    possibly truncated line so a caller never sees half a JSON record.
    """
    text = tail_text(path, max_bytes=max_bytes)
    if not text:
        return []
    lines = text.splitlines()
    if drop_partial and len(lines) > 1:
        try:
            size = _as_path(path).stat().st_size
        except OSError:
            size = 0
        try:
            limit_bytes = max(1, int(max_bytes))
        except (TypeError, ValueError):
            limit_bytes = DEFAULT_TAIL_BYTES
        if size > limit_bytes and not lines[0].lstrip().startswith("{"):
            lines = lines[1:]
    if limit is not None:
        try:
            n = max(0, int(limit))
        except (TypeError, ValueError):
            n = 0
        lines = lines[-n:] if n else []
    return lines


def iter_lines(path: "str | os.PathLike[str] | Path",
               encoding: str = "utf-8") -> Iterator[str]:
    """Stream ``path`` line by line without loading the whole file.

    Unlike :func:`tail_lines` this starts at the beginning: use it when an
    operation genuinely must visit every record (e.g. time-based pruning).
    Memory stays bounded to one line at a time.
    """
    p = _as_path(path)
    try:
        fh = p.open("r", encoding=encoding, errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            yield line.rstrip("\n")


def count_lines(path: "str | os.PathLike[str] | Path",
                encoding: str = "utf-8") -> int:
    """Count lines by streaming; never materialises the file."""
    n = 0
    for _ in iter_lines(path, encoding=encoding):
        n += 1
    return n


__all__ = ("DEFAULT_TAIL_BYTES", "tail_text", "tail_lines",
           "iter_lines", "count_lines")
