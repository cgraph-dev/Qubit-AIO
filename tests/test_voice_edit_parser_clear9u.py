"""Ambiguous "clearer" requests are rewrites, not clarify actions."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.parser import parse


def _parse(text):
    return parse(text, selected_text="alpha beta gamma", request_id="r")


def test_rewrite_more_clearly_is_a_rewrite():
    cmd = _parse("rewrite this more clearly")
    assert cmd.action == "rewrite"
    assert cmd.style == "clear"
    assert cmd.needs_clarification is False


def test_make_it_clearer_is_a_rewrite_not_a_clarification():
    cmd = _parse("make it clearer")
    assert cmd.action == "rewrite"
    assert cmd.style == "clear"


def test_explicit_clarify_stays_clarify():
    assert _parse("clarify this").action == "clarify"
    assert _parse("make clear").action == "clarify"


def test_llm_clarification_stays_clarify():
    assert _parse("make this clear for an llm").action == "clarify"
