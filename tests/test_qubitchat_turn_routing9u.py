"""Wave A Task 1 — shared /qubit-chat turn decision (9u).

``chat_transport.decide_turn_action`` is the single classification both the
/qubit-chat turn path and the voice orchestrator use. Pure: no side effects,
no network, no model call. Later waves consume exactly this dict.
"""
from __future__ import annotations


def test_decide_turn_action_perplexity_is_web_search_not_chatter():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("use perplexity api to search when Aion 2 will release", cfg={})
    assert d["action"] == "web_search", d
    assert "aion" in d["task_text"].lower()


def test_decide_weather_is_web_search():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("whats the weather in Bucharest", cfg={})
    assert d["action"] == "web_search", d


def test_decide_build_app_is_agent_task():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("make me an app that tracks expenses", cfg={})
    assert d["action"] == "agent_task", d


def test_decide_open_app_is_local_action():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("open vscode", cfg={})
    assert d["action"] == "local_action", d


def test_decide_empty_is_refuse_or_clarify():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("   ", cfg={})
    assert d["action"] in ("clarify", "refuse"), d
