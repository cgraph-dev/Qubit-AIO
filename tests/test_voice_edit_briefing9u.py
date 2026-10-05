"""Voice briefings (Slice 4b, 9u).

Owner decisions 2026-10-03 §5 #1/#2: briefings are BOTH (a) on-request
("brief me") AND (b) unprompted spoken completions ("report's ready").
Sleep mode gates (b) unprompted speech, NEVER (a) on-request answers.

Regions: VoiceEditService.brief_me / speak_completion (+ seams) and the
ask() "brief me" hook. No services, no browser, no model, no live config
writes (tmp_path only); slow seams (weather/CDP/CLI) are stubbed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import settings as settings_mod
from voicebridge.voice_edit.service import VoiceEditService


class FakeCfg:
    """Scratch config: .get contract + .data (live sync) + .path (file)."""

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


def _svc(cfg):
    return VoiceEditService(
        cfg,
        desktop=FakeDesktop(),
        brain=None,
        audit=lambda *a: None,
        estop=lambda cfg: False,
    )


def _cfg_with_file(tmp_path, data=None):
    path = tmp_path / "voice.yaml"
    import yaml

    path.write_text(yaml.safe_dump(data or {}), encoding="utf-8")
    return FakeCfg(data={}, path=path)


def _stub_seams(svc, weather="W", clock="C", cron="J", unread="U"):
    svc._briefing_weather = lambda: weather
    svc._briefing_clock = lambda: clock
    svc._briefing_cron = lambda: cron
    svc._briefing_unread = lambda: unread
    return svc


# -- request detection -------------------------------------------------------

def test_briefing_request_matches_brief_me(tmp_path):
    svc = _svc(_cfg_with_file(tmp_path))
    assert svc.is_briefing_request("brief me") is True
    assert svc.is_briefing_request("Hey qubit, brief me") is True
    assert svc.is_briefing_request("give me a morning briefing") is True


def test_briefing_request_rejects_non_briefing(tmp_path):
    svc = _svc(_cfg_with_file(tmp_path))
    assert svc.is_briefing_request("what's the weather") is False
    assert svc.is_briefing_request("") is False
    assert svc.is_briefing_request("going to sleep") is False


# -- brief_me composition ----------------------------------------------------

def test_brief_me_composes_max_three_items_need_to_know_first(tmp_path):
    svc = _stub_seams(_svc(_cfg_with_file(tmp_path)))
    out = svc.brief_me()
    assert out.get("ok") is True
    items = out.get("items") or []
    assert len(items) <= 3
    # Need-to-know (unread/cron) comes before static facts (weather/clock).
    assert items[0] == "U"
    assert items[1] == "J"
    assert out.get("answer") and "U" in out["answer"] and "J" in out["answer"]


def test_brief_me_skips_empty_seams(tmp_path):
    svc = _stub_seams(
        _svc(_cfg_with_file(tmp_path)), weather="", clock="", cron="J",
        unread="")
    out = svc.brief_me()
    assert out.get("ok") is True
    assert out.get("items") == ["J"]
    assert "J" in out.get("answer")


def test_brief_me_all_empty_honest_fallback(tmp_path):
    svc = _stub_seams(
        _svc(_cfg_with_file(tmp_path)), weather="", clock="", cron="",
        unread="")
    out = svc.brief_me()
    assert out.get("ok") is True
    assert out.get("items") == []
    assert out.get("answer")


def test_brief_me_answers_while_sleeping(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _stub_seams(_svc(cfg))
    svc.set_sleep_mode(True)
    assert svc.sleeping() is True
    out = svc.brief_me()
    assert out.get("ok") is True
    assert len(out.get("items") or []) > 0


# -- ask() hook (on-request; sleep never gates) -------------------------------

def test_ask_routes_brief_me(tmp_path):
    svc = _stub_seams(_svc(_cfg_with_file(tmp_path)))
    out = svc.ask("brief me")
    assert out.get("ok") is True
    assert out.get("mode") == "briefing"
    assert len(out.get("items") or []) > 0


def test_ask_brief_me_while_sleeping_still_answers(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _stub_seams(_svc(cfg))
    svc.set_sleep_mode(True)
    out = svc.ask("brief me")
    assert out.get("ok") is True
    assert out.get("mode") == "briefing"


# -- unprompted completion speech (sleep-gated) --------------------------------

def test_speak_completion_awake_speaks(tmp_path):
    svc = _svc(_cfg_with_file(tmp_path))
    assert svc.may_speak_unprompted() is True
    out = svc.speak_completion("The report's ready.")
    assert out.get("ok") is True
    assert out.get("spoken") is True
    assert out.get("answer") == "The report's ready."


def test_speak_completion_sleeping_stays_silent(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    svc.set_sleep_mode(True)
    out = svc.speak_completion("The report's ready.")
    assert out.get("ok") is True
    assert out.get("spoken") is False
    assert out.get("suppressed") is True
    assert not out.get("answer")


def test_speak_completion_empty_is_typed(tmp_path):
    svc = _svc(_cfg_with_file(tmp_path))
    out = svc.speak_completion("  ")
    assert out.get("ok") is False
    assert out.get("code")


# -- away digest (Slice 4 gap: batched "while you were away") -------------------
# Composes recent spoken completions (voice-session History) + the unread/cron
# seams, need-to-know first, max 3. Sleep gates UNPROMPTED delivery only.


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def messages(self, session_id, *, limit=200):
        return {"state": "OK", "messages": list(self._rows)}


def _completion_rows(*texts):
    rows = []
    for t in texts:
        rows.append({"role": "user", "content": "task completed"})
        rows.append({"role": "assistant", "content": t})
    return rows


def test_recent_completions_reads_spoken_history(tmp_path):
    rows = ([{"role": "user", "content": "what time is it"},
             {"role": "assistant", "content": "It is Thursday 03:53."}]
            + _completion_rows("The report's ready.", "Timer done."))
    svc = VoiceEditService(
        _cfg_with_file(tmp_path), desktop=FakeDesktop(), brain=None,
        audit=lambda *a: None, estop=lambda cfg: False,
        session_store=_FakeStore(rows))
    assert svc._recent_completions() == ["The report's ready.", "Timer done."]


def test_recent_completions_empty_store(tmp_path):
    svc = VoiceEditService(
        _cfg_with_file(tmp_path), desktop=FakeDesktop(), brain=None,
        audit=lambda *a: None, estop=lambda cfg: False,
        session_store=_FakeStore([]))
    assert svc._recent_completions() == []


def _digest_svc(tmp_path, completions, unread="U", cron="J"):
    svc = _svc(_cfg_with_file(tmp_path))
    svc._recent_completions = lambda **kw: list(completions)
    svc._briefing_unread = lambda: unread
    svc._briefing_cron = lambda: cron
    return svc


def test_away_digest_on_request_answers_while_sleeping(tmp_path):
    svc = _digest_svc(tmp_path, ["The report's ready."])
    svc.set_sleep_mode(True)
    assert svc.sleeping() is True
    out = svc.away_digest()
    assert out.get("ok") is True
    assert out.get("suppressed") is not True
    items = out.get("items") or []
    assert len(items) <= 3
    assert items[0] == "U"
    assert any("report's ready" in i for i in items)
    assert "J" in (out.get("answer") or "")


def test_away_digest_unprompted_sleeping_suppressed(tmp_path):
    svc = _digest_svc(tmp_path, ["The report's ready."])
    svc.set_sleep_mode(True)
    out = svc.away_digest(unprompted=True)
    assert out.get("ok") is True
    assert out.get("spoken") is False
    assert out.get("suppressed") is True
    assert not out.get("answer")


def test_away_digest_unprompted_awake_delivers(tmp_path):
    svc = _digest_svc(tmp_path, ["The report's ready."])
    assert svc.may_speak_unprompted() is True
    out = svc.away_digest(unprompted=True)
    assert out.get("ok") is True
    assert out.get("spoken") is True
    assert out.get("suppressed") is False
    assert "report's ready" in (out.get("answer") or "")


def test_away_digest_caps_at_three(tmp_path):
    svc = _digest_svc(tmp_path, ["Done one.", "Done two.", "Done three."])
    out = svc.away_digest()
    assert out.get("ok") is True
    assert len(out.get("items") or []) <= 3


def test_away_digest_all_empty_honest_fallback(tmp_path):
    svc = _digest_svc(tmp_path, [], unread="", cron="")
    out = svc.away_digest()
    assert out.get("ok") is True
    assert out.get("items") == []
    assert out.get("answer")


def test_away_digest_unprompted_awake_empty_stays_silent(tmp_path):
    svc = _digest_svc(tmp_path, [], unread="", cron="")
    out = svc.away_digest(unprompted=True)
    assert out.get("ok") is True
    assert out.get("spoken") is False
    assert not out.get("answer")


# -- joint preservation (prior lanes) ------------------------------------------

def test_sleep_command_still_wins_over_briefing_wording(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("going to sleep")
    assert out.get("ok") is True
    assert out.get("mode") == "sleep"
    assert svc.sleeping() is True
    assert settings_mod.settings_view(cfg)["sleep_mode"] is True
