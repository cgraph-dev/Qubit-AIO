"""Complete-history dashboard log search with memory bounded by page size."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Callable


def search_log_page(
    path: Path, *, needle: str, offset: int, limit: int,
    accept_line_factory: Callable[[], Callable[[str], bool]],
) -> tuple[list[str], bool]:
    """Return the newest matching page in oldest-to-newest order.

    Two streaming passes keep memory O(limit) even for enormous offsets.
    A fresh filter per pass preserves stateful continuation-line semantics.
    """
    offset = max(0, offset)
    limit = max(0, limit)
    if limit == 0:
        return [], False
    needle = needle.lower()

    def matching_lines():
        accept_line = accept_line_factory()
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            for line in stream:
                if accept_line(line) and needle in line.lower():
                    yield line

    # The dashboard's initial page is the common case. A single bounded
    # streaming pass avoids rescanning large logs just to compute an offset
    # that is already known to be zero. Keep one extra match to report
    # whether an older page exists without retaining the whole history.
    if offset == 0:
        newest = deque(matching_lines(), maxlen=limit + 1)
        has_older = len(newest) > limit
        return list(newest)[-limit:], has_older

    count = sum(1 for _ in matching_lines())
    end = count - offset
    if end <= 0:
        return [], False
    start = max(0, end - limit)
    # A deque bounds retained data even when the selected page is large.
    page: deque[str] = deque(maxlen=limit)
    for index, line in enumerate(matching_lines()):
        if index >= end:
            break
        if index >= start:
            page.append(line)
    return list(page), start > 0
