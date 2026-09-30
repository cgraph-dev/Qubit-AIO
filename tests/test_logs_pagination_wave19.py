"""Logs page contract — additive ``offset`` pagination on ``GET /api/logs``.

The upstream 0.21.4 handler (``hermes_cli/web_routers/status.py:794``) accepts
only ``file/lines/level/component/search`` and returns ``{file, lines}``; it has
no offset/cursor pagination and no per-line detail route. Qubit adds a minimal,
backwards-compatible ``offset`` contract on the vendored handler
(``qubit_runtime/qubit_cli/web_routers/status.py`` get_logs): ``offset=0``
returns the same tail as before, and ``has_more``/``next_offset`` are proven
from an extra matching line read — never guessed.

Behavioral: invokes the real route handler over a temp ``QUBIT_HOME`` log file.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "qubit_runtime") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "qubit_runtime"))


def _home(tmp_path, monkeypatch, *, lines=None):
    home = tmp_path / "qhome"
    (home / "logs").mkdir(parents=True, exist_ok=True)
    if lines is not None:
        (home / "logs" / "agent.log").write_text("".join(lines), encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return home


def _get(**params):
    from qubit_cli.web_routers.status import get_logs

    return asyncio.run(get_logs(**params))


def _numbered(count: int, *, level: str = "INFO"):
    return [
        f"2026-09-23 07:00:{i:02d},000 {level} test: L{i:02d}\n"
        for i in range(count)
    ]


def test_offset_pages_walk_backwards_without_gaps(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, lines=_numbered(25))

    page0 = _get(file="agent", lines=10)
    assert page0["offset"] == 0 and page0["limit"] == 10
    assert page0["returned"] == 10 and page0["has_more"] is True
    assert page0["next_offset"] == 10
    assert "L15" in page0["lines"][0] and "L24" in page0["lines"][-1]

    page1 = _get(file="agent", lines=10, offset=10)
    assert page1["returned"] == 10 and page1["has_more"] is True
    assert page1["next_offset"] == 20
    assert "L05" in page1["lines"][0] and "L14" in page1["lines"][-1]

    page2 = _get(file="agent", lines=10, offset=20)
    assert page2["returned"] == 5 and page2["has_more"] is False
    assert page2["next_offset"] is None
    assert "L00" in page2["lines"][0] and "L04" in page2["lines"][-1]

    walked = page0["lines"] + page1["lines"] + page2["lines"]
    labels = [line.rsplit(" ", 1)[-1].strip() for line in walked]
    # Pages are newest-first and each page is chronological; together they must
    # cover every line exactly once (no gap, no overlap).
    assert sorted(labels) == [f"L{i:02d}" for i in range(25)]
    assert len(labels) == len(set(labels)) == 25


def test_offset_zero_is_backwards_compatible(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, lines=_numbered(25))
    body = _get(file="agent", lines=7)
    # The upstream shape is intact: `lines` is the newest tail, unchanged.
    assert len(body["lines"]) == 7
    assert "L24" in body["lines"][-1]
    # The additive fields do not alter the base contract.
    assert set(body) >= {
        "file",
        "lines",
        "offset",
        "limit",
        "returned",
        "has_more",
        "next_offset",
    }


def test_offset_paginates_filtered_lines(tmp_path, monkeypatch):
    lines = []
    for i in range(12):
        level = "ERROR" if i % 3 == 0 else "INFO"
        lines.append(f"2026-09-23 07:00:{i:02d},000 {level} test: L{i:02d}\n")
    _home(tmp_path, monkeypatch, lines=lines)

    first = _get(file="agent", lines=2, level="ERROR")
    # ERROR lines are L00, L03, L06, L09; the page is chronological, so the
    # newest page of 2 is [L06, L09].
    assert "L06" in first["lines"][0] and "L09" in first["lines"][1]
    assert first["has_more"] is True and first["next_offset"] == 2

    second = _get(file="agent", lines=2, level="ERROR", offset=2)
    assert "L00" in second["lines"][0] and "L03" in second["lines"][1]
    assert second["has_more"] is False and second["next_offset"] is None


def test_search_offset_paginates_the_filtered_tail(tmp_path, monkeypatch):
    lines = _numbered(10)
    lines += [f"2026-09-23 07:10:{i:02d},000 INFO test: NEEDLE{i:02d}\n" for i in range(5)]
    _home(tmp_path, monkeypatch, lines=lines)

    first = _get(file="agent", lines=2, search="needle")
    assert first["returned"] == 2 and first["has_more"] is True
    assert "NEEDLE04" in first["lines"][-1]

    rest = _get(file="agent", lines=5, search="needle", offset=2)
    assert rest["returned"] == 3 and rest["has_more"] is False


def test_unknown_file_and_missing_file_are_typed(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, lines=_numbered(3))
    with pytest.raises(HTTPException) as exc:
        _get(file="nope")
    assert exc.value.status_code == 400
    assert "Unknown log file" in exc.value.detail

    # A known file that does not exist still answers with the pagination shape.
    missing = _get(file="gateway", lines=5, offset=3)
    assert missing["lines"] == [] and missing["offset"] == 3
    assert missing["has_more"] is False and missing["next_offset"] is None
