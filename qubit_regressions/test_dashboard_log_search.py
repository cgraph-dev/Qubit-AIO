"""Regression tests for complete-history dashboard log search."""
from __future__ import annotations

from qubit_cli.log_search import search_log_page


def test_search_finds_matches_older_than_two_thousand_lines(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("needle early\n" + "ordinary\n" * 2500 + "needle late\n")
    page, more = search_log_page(
        path, needle="NEEDLE", offset=0, limit=1, accept_line_factory=lambda: (lambda _: True))
    assert page == ["needle late\n"]
    assert more is True
    older, more = search_log_page(
        path, needle="needle", offset=1, limit=1, accept_line_factory=lambda: (lambda _: True))
    assert older == ["needle early\n"]
    assert more is False


def test_search_filters_before_pagination(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("hit allow\nhit deny\nhit allow\n")
    page, more = search_log_page(
        path, needle="hit", offset=0, limit=1,
        accept_line_factory=lambda: (lambda line: "allow" in line))
    assert page == ["hit allow\n"]
    assert more is True


def test_search_handles_empty_and_past_end(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("one\n")
    assert search_log_page(path, needle="one", offset=0, limit=0,
                           accept_line_factory=lambda: (lambda _: True)) == ([], False)
    assert search_log_page(path, needle="one", offset=9, limit=5,
                           accept_line_factory=lambda: (lambda _: True)) == ([], False)


def test_huge_offset_does_not_allocate_offset_sized_buffer(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("hit\n" * 5)
    page, more = search_log_page(
        path, needle="hit", offset=10**12, limit=2,
        accept_line_factory=lambda: (lambda _: True))
    assert (page, more) == ([], False)


def test_first_page_stateful_filter_runs_one_fresh_pass(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("hit first\nhit second\nhit third\n")
    creations = []

    def factory():
        creations.append(True)
        count = 0

        def accept(_line):
            nonlocal count
            count += 1
            return count != 2

        return accept

    page, more = search_log_page(
        path, needle="hit", offset=0, limit=1,
        accept_line_factory=factory)
    assert page == ["hit third\n"]
    assert more is True
    assert len(creations) == 1



def test_stateful_filter_is_fresh_on_second_pass(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("hit first\nhit second\nhit third\nhit fourth\n")
    creations = []

    def factory():
        creations.append(True)
        count = 0

        def accept(_line):
            nonlocal count
            count += 1
            return count != 2

        return accept

    page, more = search_log_page(
        path, needle="hit", offset=1, limit=1,
        accept_line_factory=factory)
    assert page == ["hit third\n"]
    assert more is True
    assert len(creations) == 2



def test_pagination_is_exhaustive_without_duplicates(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("".join(f"hit {i}\n" for i in range(7)))
    pages = []
    for offset in (0, 3, 6):
        page, more = search_log_page(
            path, needle="HIT", offset=offset, limit=3,
            accept_line_factory=lambda: (lambda _: True))
        pages.append(page)
        assert more is (offset < 6)
    assert pages == [
        ["hit 4\n", "hit 5\n", "hit 6\n"],
        ["hit 1\n", "hit 2\n", "hit 3\n"],
        ["hit 0\n"],
    ]
    assert search_log_page(
        path, needle="hit", offset=7, limit=3,
        accept_line_factory=lambda: (lambda _: True)) == ([], False)


def test_invalid_utf8_and_bom_are_handled(tmp_path):
    path = tmp_path / "agent.log"
    path.write_bytes(b"\xef\xbb\xbfhit first\n\xffhit second\n")
    page, more = search_log_page(
        path, needle="hit", offset=0, limit=5,
        accept_line_factory=lambda: (lambda _: True))
    assert page == ["hit first\n", "\ufffdhit second\n"]
    assert more is False


def test_first_page_reads_log_only_once(tmp_path, monkeypatch):
    """The dashboard's initial log page must not rescan a large file."""
    from pathlib import Path

    path = tmp_path / "agent.log"
    path.write_text("".join(f"hit {i}\n" for i in range(100)))
    original_open = Path.open
    opens = []

    def counted_open(self, *args, **kwargs):
        if self == path:
            opens.append(True)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    page, more = search_log_page(
        path, needle="hit", offset=0, limit=5,
        accept_line_factory=lambda: (lambda _: True))
    assert page == [f"hit {i}\n" for i in range(95, 100)]
    assert more is True
    assert len(opens) == 1


def test_first_page_reports_no_older_page_when_exactly_full(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("hit first\nhit second\n")
    page, more = search_log_page(
        path, needle="hit", offset=0, limit=2,
        accept_line_factory=lambda: (lambda _: True))
    assert page == ["hit first\n", "hit second\n"]
    assert more is False


def test_filtered_history_finds_old_matches_without_search_text(tmp_path):
    """Level/component filters also require complete-history pagination."""
    path = tmp_path / "agent.log"
    path.write_text("allowed old\n" + "excluded\n" * 2500 + "allowed new\n")
    factory = lambda: (lambda line: line.startswith("allowed"))
    latest, more = search_log_page(
        path, needle="", offset=0, limit=1, accept_line_factory=factory)
    assert latest == ["allowed new\n"]
    assert more is True
    older, more = search_log_page(
        path, needle="", offset=1, limit=1, accept_line_factory=factory)
    assert older == ["allowed old\n"]
    assert more is False
