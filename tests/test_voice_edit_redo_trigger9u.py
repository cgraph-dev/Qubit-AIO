"""Redo phrasing for the rewrite transform (owner ask: 'rewrite' hard to say).

TDD red-first: 'redo this text for me' + variants must route to the rewrite
action, with and without selection text; legacy 'rewrite*' triggers stay.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import parser


def test_redo_this_text_for_me_exact_owner_phrase_routes_to_rewrite():
    cmd = parser.parse("redo this text for me", selected_text="hello world")
    assert cmd.needs_clarification is False
    assert cmd.action == "rewrite"


def test_redo_variants_route_to_rewrite():
    for text in ("redo this text", "redo this", "redo it"):
        cmd = parser.parse(text, selected_text="hello world")
        assert cmd.needs_clarification is False, text
        assert cmd.action == "rewrite", text


def test_redo_owner_phrase_without_selection_still_routes():
    cmd = parser.parse("redo this text for me", selected_text="")
    assert cmd.needs_clarification is False
    assert cmd.action == "rewrite"


def test_legacy_rewrite_triggers_still_route():
    for text in ("rewrite this", "rewrite it", "rewrite", "reword"):
        cmd = parser.parse(text, selected_text="hello")
        assert cmd.needs_clarification is False, text
        assert cmd.action == "rewrite", text
