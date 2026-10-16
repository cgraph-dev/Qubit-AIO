"""Regression tests for the cross-page smoke instrument (not product parity)."""
from scripts.dashboard_page_smoke import classify, routes


def test_route_discovery_excludes_chat_and_parameters():
    assert routes('"/config": ConfigPage, "/chat": Chat, "/sessions/:id": Detail') == ["/config"]


def test_render_crash_is_not_a_pass_even_with_shell_text():
    assert classify("Qubit Config", ["render failed"]) == "render-error"
    assert classify("  ", []) == "blank"


def test_failed_load_must_not_look_like_empty_data():
    assert classify("No sessions yet", [], 1) == "missing-error-recovery"
    assert classify("Failed to load. Retry", [], 1) == "rendered"
