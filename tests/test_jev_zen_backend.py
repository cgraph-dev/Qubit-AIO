"""Jev over the OpenCode Zen relay — body dialect, parsing, and the free door.

Zen's ``/v1/systemone`` is the only door a new install can open: it answers the
same typed-decision contract at cost 0 with **no credential**. That last part is
a trap worth a test — sending a credential to the free door is answered with
``401 Invalid credential``, i.e. a key is worse than none.

The dialect also differs from the OpenRouter alpha path, so the translation is
tested directly rather than through a mock of itself.
"""
from __future__ import annotations

import os

import pytest

from voicebridge.jev import zen


# ── body translation ──────────────────────────────────────────────────────

def test_a_pick_question_becomes_zen_criteria():
    body = zen.to_zen_body("hmm", [{
        "id": "action", "kind": "pick", "ask": "Which action?",
        "options": ["a", "b"],
        "criteria": {"a": "talk", "b": "search"},
    }])
    q = body["questions"]["action"]
    assert q["type"] == "choice"
    assert q["instructions"] == "Which action?"
    assert q["criteria"] == {"a": "talk", "b": "search"}


def test_options_without_criteria_use_the_option_as_its_meaning():
    """An invented description would steer the verdict, so none is made up."""
    body = zen.to_zen_body("x", [{
        "id": "q", "kind": "pick", "ask": "?", "options": ["red", "blue"]}])
    assert body["questions"]["q"]["criteria"] == {"red": "red", "blue": "blue"}


def test_kind_names_map_to_zen_types():
    body = zen.to_zen_body("x", [
        {"id": "a", "kind": "check", "ask": "?"},
        {"id": "b", "kind": "pick", "ask": "?", "options": ["y"]},
        {"id": "c", "kind": "rate", "ask": "?"},
    ])
    assert body["questions"]["a"]["type"] == "noul"
    assert body["questions"]["b"]["type"] == "choice"
    assert body["questions"]["c"]["type"] == "score"


def test_questions_without_an_id_are_dropped_not_misnamed():
    body = zen.to_zen_body("x", [{"kind": "check", "ask": "no id"}])
    assert body["questions"] == {}


def test_the_default_model_is_the_free_one():
    """`jev-latest` answers 'Model is unavailable' on Zen."""
    assert zen.to_zen_body("x", [])["model"] == zen.ZEN_MODEL == "jev-1.13-free"


# ── answer parsing ────────────────────────────────────────────────────────

def _asked():
    return [{"id": "action", "kind": "pick", "ask": "?",
             "options": ["answer_local", "clarify"]}]


def test_a_choice_answer_parses_with_its_confidence():
    answers, problems = zen.parse_zen_answers(
        {"answers": {"action": {"type": "choice", "choice": "clarify",
                                "confidence": 0.92,
                                "probabilities": {"answer_local": 0.08,
                                                  "clarify": 0.92}}}},
        _asked())
    assert problems == []
    assert answers[0]["answer"] == "clarify"
    assert answers[0]["confidence"] == pytest.approx(0.92)
    assert answers[0]["kind"] == "pick"


def test_a_noul_answer_becomes_a_boolean_with_the_probability_as_confidence():
    answers, problems = zen.parse_zen_answers(
        {"answers": {"ok": {"type": "noul", "noul": 0.98}}},
        [{"id": "ok", "kind": "check", "ask": "?"}])
    assert problems == []
    assert answers[0]["answer"] is True
    assert answers[0]["confidence"] == pytest.approx(0.98)


def test_a_low_noul_probability_is_false():
    answers, _ = zen.parse_zen_answers(
        {"answers": {"ok": {"type": "noul", "noul": 0.2}}},
        [{"id": "ok", "kind": "check", "ask": "?"}])
    assert answers[0]["answer"] is False


def test_a_probability_set_that_does_not_match_the_options_is_a_problem():
    """The distribution must key exactly the offered options; no coercion."""
    answers, problems = zen.parse_zen_answers(
        {"answers": {"action": {"type": "choice", "choice": "clarify",
                                "probabilities": {"something_else": 1.0}}}},
        _asked())
    assert answers == []
    assert any("do not match" in p for p in problems)


def test_a_payload_without_answers_is_refused():
    answers, problems = zen.parse_zen_answers({"model": "x"}, _asked())
    assert answers == []
    assert problems


# ── failure shapes ────────────────────────────────────────────────────────

def test_a_transport_error_is_a_typed_refusal_not_an_exception():
    def boom(*a, **k):
        raise OSError("no route to host")

    out = zen.judge("x", _asked(), http_post=boom)
    assert out["ok"] is False
    assert out["code"] == "JEV_TRANSPORT_ERROR"
    assert "answers" not in out, "a refusal must never carry a verdict"


def test_a_timeout_is_a_typed_refusal():
    def slow(*a, **k):
        raise TimeoutError("too slow")

    out = zen.judge("x", _asked(), http_post=slow)
    assert out["ok"] is False
    assert out["code"] == "JEV_TIMEOUT"


def test_no_authorization_header_is_sent_by_default():
    """The free door rejects any credential, so none may be attached."""
    seen = {}

    def capture(url, body, key, timeout_s):
        seen["key"] = key
        return {"answers": {}}

    zen.judge("x", _asked(), http_post=capture)
    assert seen["key"] == "", (
        "a credential was sent to the free Zen door, which answers 401")


# ── live (skipped without network) ────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("QU_LIVE_JEV") != "1",
                    reason="live network test; set QU_LIVE_JEV=1 to run")
def test_live_the_free_door_answers_a_pick_question():
    out = zen.judge("just thinking out loud here", [{
        "id": "action", "kind": "pick",
        "ask": "Which turn action for this utterance?",
        "options": ["answer_local", "web_search", "clarify"],
        "criteria": {"answer_local": "just talk",
                     "web_search": "needs the web",
                     "clarify": "ask the user"},
    }])
    assert out["ok"] is True, out.get("detail")
    assert out["answers"][0]["answer"] in (None, "answer_local",
                                           "web_search", "clarify")
    usage = out.get("usage") or {}
    assert usage.get("cost") == 0
