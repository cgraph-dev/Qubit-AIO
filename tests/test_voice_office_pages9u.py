"""Office/Agent Quest are deterministic voice destinations, not misroutes.

The L1.5 semantic layer used to map "open office" onto the worktree sweep and
"open agent quest" onto the skills workshop, because neither page had an L0
route. These tests pin the deterministic entries AND the local-action fallback
that both actual voice paths consult first.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import voice_commands as VC
from voicebridge.local.actions import match
from voicebridge.voice_edit.tool_task import propose_tool

OFFICE_URL = "http://127.0.0.1:8890/qubit-office"
QUEST_URL = "http://127.0.0.1:8890/qubit-office2"


def _entry_id(phrase: str) -> str:
    resolution = VC.resolve(phrase)
    assert resolution.kind == VC.MATCH, (phrase, resolution.kind)
    assert resolution.entry is not None, phrase
    return resolution.entry.id


def test_office_phrases_route_to_the_office_page():
    for phrase in ("open office", "qubit office", "qubit, open office",
                   "open qubit office", "open the office", "office"):
        assert _entry_id(phrase) == "office_page", phrase


def test_agent_quest_phrases_route_to_the_quest_page():
    for phrase in ("open agent quest", "agent quest", "qubit, open agent quest",
                   "qubit, agent quest"):
        assert _entry_id(phrase) == "quest_page", phrase


def test_the_old_misroutes_are_gone():
    # These were the L1.5 outcomes that made the pages unreachable by voice.
    for phrase in ("open office", "qubit office", "open agent quest",
                   "agent quest"):
        entry = VC.resolve(phrase).entry
        assert entry is not None and entry.id not in (
            "cli_worktree", "cli_worktree_list", "cli_worktree_prune",
            "skills_workshop", "cli_skills_workshop",
        ), (phrase, entry.id)


def test_local_action_opens_the_pages_immediately():
    office = match("open office")
    assert office is not None and office.name == "open_website"
    assert office.params["url"] == OFFICE_URL
    quest = match("open agent quest")
    assert quest is not None and quest.name == "open_website"
    assert quest.params["url"] == QUEST_URL


def test_wake_word_shapes_open_the_pages():
    for phrase in ("qubit, open office", "qubit office", "qubit, office"):
        hit = match(phrase)
        assert hit is not None and hit.name == "open_website", phrase
        assert hit.params["url"] == OFFICE_URL, phrase
    for phrase in ("qubit, open agent quest", "qubit, agent quest"):
        hit = match(phrase)
        assert hit is not None and hit.name == "open_website", phrase
        assert hit.params["url"] == QUEST_URL, phrase


def test_voice_edit_proposes_the_page_tool_not_a_workshop_task():
    assert propose_tool(None, "open office") == {
        "tool": "local.open_website", "args": {"url": OFFICE_URL}}
    assert propose_tool(None, "open agent quest") == {
        "tool": "local.open_website", "args": {"url": QUEST_URL}}


def test_office_navigation_is_not_confirmation_gated():
    # Opening a dashboard page is read-only navigation, same class as
    # "open youtube": it must execute, not stage a confirmation.
    for entry_id in ("office_page", "quest_page"):
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        assert entry.confirm is False, entry_id
        assert entry.backend in VC._BACKENDS, entry_id
        assert entry.examples and entry.help, entry_id
