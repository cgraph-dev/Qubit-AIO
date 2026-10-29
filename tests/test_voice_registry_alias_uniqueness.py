"""Literal voice aliases must have exactly one owner.

The registry is deterministic by longest-literal match, but two entries
sharing the same literal alias resolve to AMBIGUOUS for that phrase — the
caller then has to guess. These regressions pin the browser/launch split and
guard the wider registry against a reintroduced duplicate.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import voice_commands as VC
from voicebridge.local import actions as LA
from voicebridge.voice_edit import custom_commands as CC


def test_open_the_browser_has_one_owner():
    r = VC.resolve("open the browser")
    assert r.kind == VC.MATCH, (r.kind, r.speech)
    assert r.entry.id == "browser_open_browser"


def test_launch_browser_has_one_owner():
    r = VC.resolve("launch browser")
    assert r.kind == VC.MATCH, (r.kind, r.speech)
    assert r.entry.id == "browser_cdp_open"


def test_browser_and_launch_aliases_resolve_to_their_own_entry():
    for entry_id in ("browser_open_browser", "browser_cdp_open",
                     "computer_launch"):
        entry = VC.REGISTRY[entry_id]
        for alias in entry.aliases:
            if alias.startswith("rx:"):
                continue
            r = VC.resolve("qubit, " + alias)
            assert r.kind in (VC.MATCH, VC.CLARIFY), (
                entry_id, alias, r.kind, r.speech)
            assert r.entry is not None and r.entry.id == entry_id, (
                entry_id, alias, getattr(r.entry, "id", None))


def test_no_literal_alias_is_owned_by_two_entries():
    owners = defaultdict(list)
    for entry_id, entry in VC.REGISTRY.items():
        for alias in entry.aliases:
            if not alias.startswith("rx:"):
                owners[alias].append(entry_id)
    duplicates = {alias: ids for alias, ids in owners.items() if len(ids) > 1}
    assert not duplicates, duplicates


def test_single_word_aliases_resolve_when_addressed():
    """Bare single words intentionally fall through; addressed they must not."""
    for entry_id, entry in VC.REGISTRY.items():
        for alias in entry.aliases:
            if alias.startswith("rx:") or " " in alias:
                continue
            r = VC.resolve("qubit, " + alias)
            assert r.kind in (VC.MATCH, VC.CLARIFY), (
                entry_id, alias, r.kind, r.speech)
            assert r.entry is not None and r.entry.id == entry_id, (
                entry_id, alias, getattr(r.entry, "id", None))


def test_local_examples_no_longer_advertise_unreachable_phrases():
    stale = {
        "open the website project in vscode",
        "reply to Ana on messenger saying on my way",
    }
    advertised = {p for phrases in CC.EXAMPLE_PHRASES.values()
                  for p in phrases}
    assert not (advertised & stale), advertised & stale


def test_open_project_has_no_generic_example():
    # A project name only exists in the operator's own project_roots, so no
    # static phrase can verify; the catalog must not invent one.
    assert CC._verified_examples("open_project", LA.match) == []


def test_send_message_local_example_is_not_the_service_reply_phrasing():
    # The local action only accepts the internal staged draft; the natural
    # "reply to ... saying ..." path lives in the service reply-draft code.
    examples = CC._verified_examples("send_message", LA.match)
    assert all("reply to" not in e["text"].lower() for e in examples), examples
