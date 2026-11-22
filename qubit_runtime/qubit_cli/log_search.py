"""Bounded-memory, complete-history search pagination for dashboard logs."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Callable


def search_log_page(
    path: Path, *, needle: str, offset: int, limit: int,
    accept_line: Callable[[str], bool],
) -> tuple[list[str], bool]:
    """Return oldest-to-newest page from matching lines, counting from the newest.

    Unlike a fixed tail sample, this scans the entire log while retaining only
    the requested page and one older match for an accurate has_more.
    accept_line is called on every line so stateful continuation filters work.
    """
    offset = max(0, offset)
    limit = max(0, limit)
    if limit == 0:
        return [], False
    matches: deque[str] = deque(maxlen=offset + limit + 1)
    needle = needle.lower()
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            if accept_line(line) and needle in line.lower():
                matches.append(line)
    recent = list(matches)
    end = len(recent) - offset
    if end <= 0:
        return [], False
    start = max(0, end - limit)
    return recent[start:end], len(recent) > offset + limit
