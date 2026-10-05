"""Slice 1 — explicit route words bypass the classifier (9u).

"in chat: ..." -> agent_task (headless /chat worker branch);
"quick: ..." -> answer_local (today's direct-answer behavior).
Unprefixed turns keep the legacy classifier path byte-for-byte.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_parse_route_word_in_chat():
    from voicebridge import chat_transport as CT
    rw = CT.parse_route_word("in chat: build me an expense app")
    assert rw is not None, rw
    assert rw["route"] == "in-chat", rw
    assert rw["action"] == "agent_task", rw
    assert rw["task_text"] == "build me an expense app", rw


def test_parse_route_word_quick():
    from voicebridge import chat_transport as CT
    rw = CT.parse_route_word("quick: what time is it")
    assert rw is not None, rw
    assert rw["route"] == "quick", rw
    assert rw["action"] == "answer_local", rw
    assert rw["task_text"] == "what time is it", rw


def test_parse_route_word_case_insensitive_and_spaced():
    from voicebridge import chat_transport as CT
    rw = CT.parse_route_word("  In Chat:   do the thing  ")
    assert rw is not None and rw["route"] == "in-chat", rw
    assert rw["task_text"] == "do the thing", rw


def test_parse_route_word_none_unprefixed():
    from voicebridge import chat_transport as CT
    assert CT.parse_route_word("what is the weather in Bucharest") is None
    assert CT.parse_route_word("build me an app") is None
    assert CT.parse_route_word("") is None


def test_parse_route_word_empty_remainder_falls_through():
    from voicebridge import chat_transport as CT
    assert CT.parse_route_word("in chat:   ") is None
    assert CT.parse_route_word("quick:") is None


def test_parse_route_word_comma_form():
    """Consolidation pin: "in chat, ..." comma form routes in-chat."""
    from voicebridge import chat_transport as CT
    rw = CT.parse_route_word("in chat, build me an expense app")
    assert rw is not None, rw
    assert rw["route"] == "in-chat" and rw["action"] == "agent_task", rw
    assert rw["task_text"] == "build me an expense app", rw


def test_parse_route_word_space_form():
    """Consolidation pin: "in chat ..." bare-space form routes in-chat."""
    from voicebridge import chat_transport as CT
    rw = CT.parse_route_word("in chat build me an expense app")
    assert rw is not None, rw
    assert rw["route"] == "in-chat", rw
    assert rw["task_text"] == "build me an expense app", rw


def test_parse_route_word_quick_comma_and_space_forms():
    """Consolidation pin: quick comma/space forms (colon already pinned)."""
    from voicebridge import chat_transport as CT
    for text in ("quick, what time is it", "quick what time is it"):
        rw = CT.parse_route_word(text)
        assert rw is not None and rw["route"] == "quick", (text, rw)
        assert rw["task_text"] == "what time is it", (text, rw)


def test_parse_route_word_near_miss_prefixes_untouched():
    """Consolidation pin: near-miss prefixes never route (no shadowing)."""
    from voicebridge import chat_transport as CT
    assert CT.parse_route_word("in chats are great") is None
    assert CT.parse_route_word("quickly do the thing") is None
    assert CT.parse_route_word("in chat,   ") is None


def test_decide_in_chat_bypasses_classifier_to_agent_task():
    from voicebridge import chat_transport as CT
    # Without the prefix this classifies web_search; the prefix must win
    # without consulting the classifier at all.
    d = CT.decide_turn_action(
        "in chat: what is the weather in Bucharest", cfg={})
    assert d["action"] == "agent_task", d
    assert d["task_text"] == "what is the weather in Bucharest", d
    assert d["reason"] == "route-word:in-chat", d


def test_decide_quick_bypasses_classifier_to_answer_local():
    from voicebridge import chat_transport as CT
    # Without the prefix this classifies agent_task; the prefix must win.
    d = CT.decide_turn_action(
        "quick: build me an expense tracking app with a database",
        cfg={})
    assert d["action"] == "answer_local", d
    assert d["task_text"] == "build me an expense tracking app with a database", d
    assert d["reason"] == "route-word:quick", d


def test_decide_unprefixed_untouched():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("whats the weather in Bucharest", cfg={})
    assert d["action"] == "web_search", d
    assert d["reason"] != "route-word:in-chat", d
    assert not str(d["reason"]).startswith("route-word"), d


def test_parse_route_word_hyphen_forms():
    """Lane-2 pin: hyphenated "in-chat" routes exactly like "in chat"."""
    from voicebridge import chat_transport as CT
    for text in ("in-chat: open youtube",
                 "in-chat, open youtube",
                 "in-chat open youtube"):
        rw = CT.parse_route_word(text)
        assert rw is not None, (text, rw)
        assert rw["route"] == "in-chat" and rw["action"] == "agent_task", (text, rw)
        assert rw["task_text"] == "open youtube", (text, rw)


def test_parse_route_word_hyphen_case_and_empty():
    """Lane-2 pin: hyphen form is case-insensitive; empty remainder drops."""
    from voicebridge import chat_transport as CT
    rw = CT.parse_route_word("  In-Chat:   do the thing  ")
    assert rw is not None and rw["route"] == "in-chat", rw
    assert rw["task_text"] == "do the thing", rw
    assert CT.parse_route_word("in-chat:   ") is None
    assert CT.parse_route_word("in-chat:") is None


def test_parse_route_word_hyphen_near_miss_untouched():
    """Lane-2 pin: hyphen lookalikes never route; "in chats" still misses."""
    from voicebridge import chat_transport as CT
    assert CT.parse_route_word("in chats are great") is None
    assert CT.parse_route_word("in-chatbot do the thing") is None


def test_decide_in_chat_hyphen_bypasses_classifier_to_agent_task():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("in-chat: open youtube", cfg={})
    assert d["action"] == "agent_task", d
    assert d["task_text"] == "open youtube", d
    assert d["reason"] == "route-word:in-chat", d
