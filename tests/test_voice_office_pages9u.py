"""Office is a deterministic voice destination, not a misroute.

The L1.5 semantic layer used to map "open office" onto the worktree sweep,
because the page had no L0 route. These tests pin the deterministic entry AND
the local-action fallback that both actual voice paths consult first.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import voice_commands as VC
from voicebridge.local.actions import match
from voicebridge.voice_edit.tool_task import propose_tool

OFFICE_URL = "http://127.0.0.1:8890/qubit-office"


def _entry_id(phrase: str) -> str:
    resolution = VC.resolve(phrase)
    assert resolution.kind == VC.MATCH, (phrase, resolution.kind)
    assert resolution.entry is not None, phrase
    return resolution.entry.id


def test_office_phrases_route_to_the_office_page():
    for phrase in ("open office", "qubit office", "qubit, open office",
                   "open qubit office", "open the office", "office"):
        assert _entry_id(phrase) == "office_page", phrase


def test_the_old_misroutes_are_gone():
    # These were the L1.5 outcomes that made the page unreachable by voice.
    for phrase in ("open office", "qubit office"):
        entry = VC.resolve(phrase).entry
        assert entry is not None and entry.id not in (
            "cli_worktree", "cli_worktree_list", "cli_worktree_prune",
        ), (phrase, entry.id)


def test_local_action_opens_the_page_immediately():
    office = match("open office")
    assert office is not None and office.name == "open_website"
    assert office.params["url"] == OFFICE_URL


def test_wake_word_shapes_open_the_page():
    for phrase in ("qubit, open office", "qubit office", "qubit, office"):
        hit = match(phrase)
        assert hit is not None and hit.name == "open_website", phrase
        assert hit.params["url"] == OFFICE_URL, phrase


def test_voice_edit_proposes_the_page_tool_not_a_workshop_task():
    assert propose_tool(None, "open office") == {
        "tool": "local.open_website", "args": {"url": OFFICE_URL}}


def test_office_navigation_is_not_confirmation_gated():
    # Opening a dashboard page is read-only navigation, same class as
    # "open youtube": it must execute, not stage a confirmation.
    entry = VC.CLI_BRIDGE_REGISTRY["office_page"]
    assert entry.confirm is False
    assert entry.backend in VC._BACKENDS
    assert entry.examples and entry.help
