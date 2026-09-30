"""Release regression: Qwen3 think-marker leaks must never be spoken.

The abliterated quant sometimes ignores the /no_think soft switch and emits
a leading ` response` / ` thinking` marker or an echoed `/think`/`no_think`
token into content. These must be stripped before TTS/dashboard.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.local_llm import _extract_reply, _strip_think  # noqa: E402


def test_leading_think_markers_and_tokens_stripped():
    cases = {
        " response I'm functioning well today.": "I'm functioning well today.",
        "/think hello there": "hello there",
        "no_think wait what": "wait what",
        " thinking\n\nThe capital of France is Paris.":
            "The capital of France is Paris.",
        "response It's raining.": "It's raining.",
        "plain answer": "plain answer",
    }
    for raw, want in cases.items():
        got = _strip_think(raw)
        assert got == want, f"{raw!r} -> {got!r} (want {want!r})"


def test_mid_text_soft_switch_tokens_removed():
    assert _strip_think("x /no_think y") == "x  y"
    assert _strip_think("a /think b") == "a  b"


def test_extract_reply_never_leaks_markers():
    body = {"message": {"content": " response\n\nI'm functioning well."}}
    out = _extract_reply(body)
    assert "<" not in out
    assert out.startswith("I'm functioning well")
    body2 = {"message": {"content": "/think The capital is Paris."}}
    out2 = _extract_reply(body2)
    assert not out2.startswith("/think")
    assert out2.startswith("The capital is Paris.")


def test_tag_pair_and_trailing_marker_shapes():
    cases = {
        "<think </think I am Qubit.": "I am Qubit.",
        " thinking I am functioning well. response": "I am functioning well.",
        " response I am fine. response": "I am fine.",
    }
    for raw, want in cases.items():
        got = _strip_think(raw)
        assert got == want, f"{raw!r} -> {got!r} (want {want!r})"


def test_think_block_with_newlines_strips_answer_only():
    raw = " thinking\n\nLet me reason here.\n\n responseThe answer is 96."
    got = _strip_think(raw)
    assert "reason" not in got
    assert "96" in got