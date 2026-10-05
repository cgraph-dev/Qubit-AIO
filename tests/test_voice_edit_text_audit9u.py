"""Lane-2 audit: owner mouth shapes for text transforms (TDD red-first).

Every input below is an exact owner mouth shape that must route to a real
transform action (never a clarify loop) or be caught by the narration guard.
Written BEFORE the parser/transform fix; each must fail on the unfixed code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import parser
from voicebridge.voice_edit.transform import (
    TransformEngine,
    is_narration_only,
    strip_narration_preamble,
)


def _action(text: str) -> str:
    cmd = parser.parse(text, selected_text="hello world")
    if cmd.needs_clarification:
        return "CLARIFY"
    return cmd.action


# --- summarize mouth shapes -----------------------------------------------
@pytest.mark.parametrize("text", [
    "sum this up",
    "sum this up please",
    "sum it up",
])
def test_audit_sum_up_routes_to_summarize(text):
    assert _action(text) == "summarize"


# --- expand mouth shapes ---------------------------------------------------
@pytest.mark.parametrize("text", [
    "make it longer",
    "make it longer please",
    "make this longer",
    "lengthen this",
])
def test_audit_longer_routes_to_expand(text):
    assert _action(text) == "expand"


# --- email mouth shapes ----------------------------------------------------
@pytest.mark.parametrize("text", [
    "make this an email",
    "make this an email please",
])
def test_audit_make_this_an_email_routes_to_email(text):
    assert _action(text) == "email"


# --- translate mouth shapes ------------------------------------------------
@pytest.mark.parametrize("text", [
    "convert this to German",
    "convert it to French",
])
def test_audit_convert_routes_to_translate(text):
    cmd = parser.parse(text, selected_text="hello world")
    assert cmd.needs_clarification is False
    assert cmd.action == "translate"


def test_audit_convert_extracts_language():
    cmd = parser.parse("convert this to German", selected_text="x")
    assert cmd.language.lower().startswith("german")


# --- bullet mouth shapes ---------------------------------------------------
@pytest.mark.parametrize("text", [
    "bullet this",
    "make it bullets",
])
def test_audit_bullet_routes_to_bullet_points(text):
    assert _action(text) == "bullet_points"


# --- shorten consistency ---------------------------------------------------
@pytest.mark.parametrize("text", [
    "make it brief",
    "make this brief please",
])
def test_audit_brief_routes_to_shorten(text):
    assert _action(text) == "shorten"


# --- redo preservation (must keep working) --------------------------------
@pytest.mark.parametrize("text", [
    "redo this text for me",
    "redo this text for me professionally",
    "rewrite this",
    "reword this",
    "rephrase this paragraph",
])
def test_audit_redo_rewrite_aliases_preserved(text):
    assert _action(text) in ("rewrite", "professionalize")


# --- narration guard: instruction-echo class -------------------------------
@pytest.mark.parametrize("text", [
    "We are asked to reply with exactly: hello world.",
    "We're asked to reply with exactly: hello world.",
])
def test_audit_instruction_echo_is_narration(text):
    assert is_narration_only(text) is True


@pytest.mark.parametrize("text", [
    "I rewrote your text to be shorter.",
    "I just rewrote your text.",
])
def test_audit_first_person_rewrite_claim_is_narration(text):
    assert is_narration_only(text) is True


@pytest.mark.parametrize("text", [
    # Genuine content that must NEVER be flagged, even with similar shapes.
    "We are asked to deliver results daily.",
    "I went to the store yesterday.",
    "You asked me to call back tomorrow.",
    "Do the thing clearly.",
])
def test_audit_genuine_content_never_narration(text):
    assert is_narration_only(text) is False


def test_audit_echo_with_rewrite_after_colon_recovers_rewrite():
    out = strip_narration_preamble(
        "We are asked to reply with exactly: hello world, rewritten.")
    assert out == "hello world, rewritten."


# --- narration guard: future promise + confirm/request (2026-10-04) ---------
# Journal-proven owner turn: spark4 returned "I will handle the task
# immediately. Please confirm the details to proceed with the app update."
# as ok:true rewrite. Rule: first-person future promise ("I will"/"I'll")
# + (a question OR a confirm/request clause) -> narration, never shipped.
@pytest.mark.parametrize("text", [
    # The exact owner sentence from the 2026-10-04 journal.
    "I will handle the task immediately. Please confirm the details "
    "to proceed with the app update.",
    "I'll handle the update right away. Please confirm to proceed.",
    "I will update the app now. Do you want me to proceed?",
    "I will finish the rewrite. Shall I proceed?",
    "I'll send the draft. Let me know to proceed.",
    "I will take care of it, just let me know to proceed please?",
])
def test_audit_future_promise_with_confirm_is_narration(text):
    assert is_narration_only(text) is True


@pytest.mark.parametrize("text", [
    # Prior-lane constraint: genuine first-person statements (no question,
    # no confirm/request clause) must survive as content.
    "I will move the meeting to Thursday.",
    "I'll meet you at noon tomorrow.",
    "I will handle the task immediately.",
    "We will ship the release on Friday.",
])
def test_audit_plain_first_person_statement_never_narration(text):
    assert is_narration_only(text) is False


class _FakeCfg:
    def __init__(self, data):
        self._data = data

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class _FakeResult:
    def __init__(self, text="", ok=True):
        self.ok = ok
        self.text = text
        self.code = ""
        self.message = ""
        self.provider = "ollama"
        self.model = "qubit-spark4"
        self.details = {"locality": "local"}


def test_audit_owner_promise_sentence_fails_typed_never_shipped():
    cfg = _FakeCfg({"voice_edit": {"routing": "local"}})
    cmd = parser.parse("rewrite this", selected_text="hello world")
    res = TransformEngine(runner=lambda *a, **k: _FakeResult(
        text="I will handle the task immediately. Please confirm the "
             "details to proceed with the app update.")).transform(cfg, cmd)
    assert res.ok is False
    assert res.code == "VOICE_EDIT_NARRATION_ONLY"


# --- reduce trigger -> shorten (2026-10-04) ---------------------------------
# "Reduce this text for me. The quarterly..." misrouted to intent:coding.
# "Reduce X" with a text object unambiguously means make-shorter.
@pytest.mark.parametrize("text", [
    "Reduce this text for me. The quarterly results show growth.",
    "reduce this text for me",
    "reduce this for me",
    "reduce this text",
    "reduce it",
    "reduce the text",
])
def test_audit_reduce_text_routes_to_shorten(text):
    assert _action(text) == "shorten"


@pytest.mark.parametrize("text", [
    # Money sense, no text object: must NOT steal — stays clarification,
    # exactly as it routes today (never a transform).
    "reduce the budget",
    "reduce spending",
])
def test_audit_reduce_money_sense_stays_clarify(text):
    cmd = parser.parse(text, selected_text="hello world")
    assert cmd.needs_clarification is True


def test_audit_shorten_offer_tail_stripped_never_shipped():
    cfg = _FakeCfg({"voice_edit": {"routing": "local"}})
    cmd = parser.parse("reduce this text", selected_text="hello world")
    assert cmd.action == "shorten"
    res = TransformEngine(runner=lambda *a, **k: _FakeResult(
        text="The quarterly results show significant growth. "
             "How can I assist you further?")).transform(cfg, cmd)
    assert res.ok is True
    assert res.text == "The quarterly results show significant growth."
