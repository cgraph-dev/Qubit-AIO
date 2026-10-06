"""Voice sweep fixes (2026-10-05): over-fire, under-fire, and reread gaps.

Drives the REAL functions on scratch services/stores (no model, no network,
no browser). Every test below FAILED before the fix and passes after:

A. waveform widget must not flip on settings questions
   ("show waveform in game settings" flipped the widget — §84 pre-existing).
B. briefing must not fire on style instructions ("please be brief." spoke a
   full briefing).
C. away-digest must not fire on topical newness ("what's new on YouTube",
   "anything new on Netflix").
D. bare-pronoun "close it" / "shut it down" must not stop YouTube and close
   tabs (no music context).
E. reread shapes ("say that again", "read that back", "repeat that") must
   deterministically replay the last answer instead of being dropped
   (router ignore) or chattered about (brain) — on the voice path AND the
   /qubit-chat turn path.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


class StubBrain:
    """Never consulted on deterministic paths (records calls)."""

    def __init__(self):
        self.calls = []

    def answer(self, *a, **k):
        self.calls.append((a, k))
        raise AssertionError("brain must not be consulted")


class MemStore:
    """Canonical-store double: messages() + append()."""

    def __init__(self, msgs=None):
        self._msgs = list(msgs or [])

    def messages(self, session_id, limit=200):
        return {"messages": list(self._msgs)}

    def append(self, sid, role, content, **k):
        self._msgs.append({"role": role, "content": content})
        return {"state": "OK"}


def _svc(cfg, store=None, brain=None):
    return VoiceEditService(
        cfg, desktop=FakeDesktop(), brain=brain,
        audit=lambda *a: None, estop=lambda cfg: False,
        session_store=store,
    )


def _cfg(enabled=True):
    return FakeCfg(data={"voice_edit": {"enabled": enabled}})


# -- A: waveform settings questions never flip --------------------------------

def test_waveform_settings_question_never_flips_widget():
    svc = _svc(_cfg())
    for text in ("show waveform in game settings",
                 "show wave form in game settings",
                 "show me the waveform settings",
                 "open the waveform settings",
                 "waveform settings"):
        assert svc.waveform_command(text) is None, text
        assert svc.waveform_ingame_command(text) is None, text


def test_waveform_legit_shapes_still_flip():
    svc = _svc(_cfg())
    for text in ("show waveform", "Show Wave Form.", "turn on the waveform",
                 "waveform on", "hide waveform", "disable the waveform",
                 "waveform off"):
        assert svc.waveform_command(text) is not None, text


def test_waveform_settings_route_does_not_answer_waveform():
    svc = _svc(_cfg())
    rid = svc.begin(request_id="r-sweep-wave")["request_id"]
    out = svc.route(rid, "show waveform in game settings")
    assert out.get("decision", {}).get("reason") != "waveform-visible", out
    assert out.get("mode") != "answer" or \
        out.get("decision", {}).get("intent") != "waveform-visible", out


# -- B: style instructions are not briefing requests --------------------------

def test_style_instruction_is_not_briefing():
    for text in ("please be brief.", "be brief", "Be brief, please."):
        assert VoiceEditService.is_briefing_request(text) is False, text


def test_briefing_shapes_still_fire():
    for text in ("brief me", "morning briefing", "breathe.", "Breathe me.",
                 "Brave me.", "brief", "Hey qubit, breathe me",
                 "Can you brief me on Q3?"):
        assert VoiceEditService.is_briefing_request(text) is True, text


# -- C: topical newness is not an away-digest ---------------------------------

def test_topical_newness_is_not_digest():
    for text in ("what's new on YouTube",
                 "is there anything new on Netflix?",
                 "anything new in my inbox",
                 "what's new with the project"):
        assert VoiceEditService.is_digest_request(text) is False, text


def test_bare_newness_still_digests():
    for text in ("what did I miss", "while I was away", "catch me up",
                 "what's new", "anything new", "catch me up on Q3"):
        assert VoiceEditService.is_digest_request(text) is True, text


# -- D: bare pronouns never stop YouTube --------------------------------------

def test_bare_pronoun_never_stops_youtube():
    from voicebridge.local.actions import match
    for text in ("close it", "close this", "shut it down", "stop it"):
        m = match(text)
        assert m is None or m.name != "youtube_stop", (text, m)


def test_music_scoped_stop_still_works():
    from voicebridge.local.actions import match
    for text in ("close the youtube tabs", "stop the music",
                 "shut the music down", "stop the youtube video"):
        m = match(text)
        assert m is not None and m.name == "youtube_stop", text


# -- E: reread replays the last answer ----------------------------------------

def test_repeat_matcher_shapes():
    svc = _svc(_cfg())
    for text in ("say that again", "Say that again.", "say it again",
                 "read that back", "read it back", "repeat that",
                 "repeat it", "what did you say", "what did you just say",
                 "come again", "hey qubit, say that again"):
        assert svc.is_repeat_request(text) is True, text


def test_repeat_matcher_near_misses():
    svc = _svc(_cfg())
    for text in ("say that", "read the news", "repeat after me",
                 "what did you do today", "open youtube"):
        assert svc.is_repeat_request(text) is False, text


def test_repeat_router_answers():
    from voicebridge.voice_edit.router import VoiceRouter
    r = VoiceRouter(FakeCfg(data={"voice_edit": {"router": "deterministic",
                                                 "allow_api": False,
                                                 "routing": "local"}}))
    for text in ("say that again", "read that back", "repeat that"):
        d = r.decide(text, has_selection=False)
        assert d.mode == "answer", (text, d)
        assert d.reason == "repeat", (text, d)


def test_repeat_ask_replays_last_answer_without_brain():
    brain = StubBrain()
    store = MemStore([
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "It is Monday 04:33."},
    ])
    svc = _svc(_cfg(), store=store, brain=brain)
    out = svc.ask("say that again")
    assert out.get("ok") is True, out
    assert out.get("answer") == "It is Monday 04:33.", out
    assert out.get("model") == "repeat", out
    assert brain.calls == [], "deterministic repeat must not consult the brain"


def test_repeat_ask_empty_history_falls_through():
    from voicebridge.voice_edit import service as _S
    svc = _svc(_cfg(), store=MemStore())
    assert svc._repeat_last_answer() == ("", "")


def test_chat_transport_repeat_turn_replays():
    from voicebridge import chat_transport as CT
    store_rows = {"rep-9u": [
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "It is Monday 04:33."},
    ]}

    class _FakeStore:
        def ensure(self, sid, **k):
            sid = (sid or "").strip() or "rep-9u"
            store_rows.setdefault(sid, [])
            return {"state": "OK", "session": {"id": sid}}

        def append(self, sid, role, content, **k):
            store_rows.setdefault(sid, []).append(
                {"role": role, "content": content})
            return {"state": "OK"}

        def messages(self, sid, limit=500):
            return {"messages": list(store_rows.get(sid, []))}

    fake = _FakeStore()
    orig_store, orig_estop = CT.store, CT._estop
    CT.store, CT._estop = (lambda cfg: fake), (lambda cfg: False)
    try:
        out = CT.turn({}, "say that again", session="rep-9u")
    finally:
        CT.store, CT._estop = orig_store, orig_estop
    assert out.get("ok") is True, out
    assert out.get("route") == "repeat", out
    assert out.get("reply") == "It is Monday 04:33.", out
