"""Lane 3 (2026-10-03): STT mishearing tolerance — redo + briefing.

Owner's voice, both ASR-transcribed (journal-proof):
(a) "redo THIS TEXT for me" heard as "redo THESE PACKS for me" → must route
    to the rewrite action (not brain conversation).
(b) "brief me" heard as "breathe." / "Breathe me." / "Brave me." → must route
    to the briefing stage (not a brain "Breathe. I'm here." answer).

Boundaries: the "for me" anchor is required for redo (bare "redo the thing"
must not steal turns); briefing mishear shapes are end-anchored so
"breathe deeply" / "be brave" stay non-briefing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import parser
from voicebridge.voice_edit.service import VoiceEditService


class FakeCfg:
    def __init__(self, data=None, path=None):
        self._data = data or {}
        self.data = self._data
        self.path = path

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return False


def _svc(tmp_path):
    path = tmp_path / "voice.yaml"
    path.write_text("{}", encoding="utf-8")
    cfg = FakeCfg(data={}, path=path)
    svc = VoiceEditService(
        cfg, desktop=FakeDesktop(), brain=None,
        audit=lambda *a: None, estop=lambda cfg: False,
    )
    svc._briefing_weather = lambda: "W"
    svc._briefing_clock = lambda: "C"
    svc._briefing_cron = lambda: "J"
    svc._briefing_unread = lambda: "U"
    return svc


# -- redo: owner's exact misheard string routes to rewrite -------------------

def test_lane3_redo_misheard_packs_routes_to_rewrite():
    cmd = parser.parse("redo these packs for me", selected_text="hello world")
    assert cmd.needs_clarification is False
    assert cmd.action == "rewrite"


def test_lane3_redo_misheard_caps_routes_to_rewrite():
    cmd = parser.parse("Redo THESE PACKS for me", selected_text="hello world")
    assert cmd.needs_clarification is False
    assert cmd.action == "rewrite"


def test_lane3_redo_misheard_with_trailing_style_routes_to_rewrite():
    # Trailing style refines to the specific transform (same convention as
    # the exact phrase: "redo this text for me professionally" →
    # professionalize, per test_audit_redo_rewrite_aliases_preserved).
    # What matters: a deterministic transform, never brain conversation.
    cmd = parser.parse(
        "redo these packs for me professionally", selected_text="hello world")
    assert cmd.needs_clarification is False
    assert cmd.action in ("rewrite", "professionalize")


def test_lane3_redo_misheard_without_selection_still_routes():
    cmd = parser.parse("redo these packs for me", selected_text="")
    assert cmd.needs_clarification is False
    assert cmd.action == "rewrite"


# -- redo boundaries: the "for me" anchor is required -------------------------

def test_lane3_redo_without_for_me_does_not_route():
    for text in ("redo the budget", "redo the thing"):
        cmd = parser.parse(text, selected_text="hello world")
        assert cmd.action != "rewrite" or cmd.needs_clarification is True, text


# -- briefing: owner's exact misheard strings route to briefing ----------------

def test_lane3_briefing_misheard_shapes_match():
    svc_cls = VoiceEditService
    for text in ("breathe.", "Breathe me.", "Brave me.", "brief", "brief me",
                 "Hey qubit, breathe me"):
        assert svc_cls.is_briefing_request(text) is True, text


def test_lane3_briefing_ask_stage_routes_misheard(tmp_path):
    svc = _svc(tmp_path)
    out = svc.ask("Breathe me.")
    assert out.get("ok") is True
    assert out.get("mode") == "briefing"


# -- briefing boundaries ---------------------------------------------------------

def test_lane3_briefing_near_misses_do_not_match():
    svc_cls = VoiceEditService
    for text in ("breathe deeply", "be brave", "redo the budget",
                 "a briefing document", "what's the weather"):
        assert svc_cls.is_briefing_request(text) is False, text
