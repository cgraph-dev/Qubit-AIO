"""Regression tests for complete-history dashboard log search."""
from __future__ import annotations

from qubit_cli.log_search import search_log_page


def test_search_finds_matches_older_than_two_thousand_lines(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("needle early\n" + "ordinary\n" * 2500 + "needle late\n")
    page, more = search_log_page(
        path, needle="NEEDLE", offset=0, limit=1, accept_line=lambda _: True)
    assert page == ["needle late\n"]
    assert more is True
    older, more = search_log_page(
        path, needle="needle", offset=1, limit=1, accept_line=lambda _: True)
    assert older == ["needle early\n"]
    assert more is False


def test_search_filters_before_pagination(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("hit allow\nhit deny\nhit allow\n")
    page, more = search_log_page(
        path, needle="hit", offset=0, limit=1,
        accept_line=lambda line: "allow" in line)
    assert page == ["hit allow\n"]
    assert more is True


def test_search_handles_empty_and_past_end(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("one\n")
    assert search_log_page(path, needle="one", offset=0, limit=0,
                           accept_line=lambda _: True) == ([], False)
    assert search_log_page(path, needle="one", offset=9, limit=5,
                           accept_line=lambda _: True) == ([], False)
