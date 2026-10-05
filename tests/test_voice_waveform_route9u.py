"""Waveform intents through the live route stage (voice-final repair lane).

Regression: ``svc.ask("show waveform")`` flips the widget, but the live
wake/helper path never calls ``ask`` first — it calls ``route()``, and the
intent classifier sends "show waveform" to ``task/coding`` (confirm-gated
proposal), so the flip never happens. "hide waveform" classifies as
chat/conversation and works. The wake prefix is stripped upstream
(``strip_wake`` → remainder → ``on_wake_command`` → ``route``), so the
route stage sees the bare remainder and must steer it to the answer path
where ``ask()`` performs the deterministic flip.

No services, no browser, no model, no live config writes (tmp_path only).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import settings as settings_mod
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.wake import strip_wake


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


def _svc(cfg, audit_log=None):
    return VoiceEditService(
        cfg,
        desktop=FakeDesktop(),
        brain=None,
        audit=(lambda op, sec, detail: audit_log.append((op, detail))
               if audit_log is not None else lambda *a: None),
        estop=lambda cfg: False,
    )


def _cfg_with_file(tmp_path, data=None):
    import yaml

    path = tmp_path / "voice.yaml"
    path.write_text(yaml.safe_dump(data or {}), encoding="utf-8")
    live = {"voice_edit": {"enabled": True}}
    return FakeCfg(data=live, path=path)


def _route(cfg, svc, text):
    rid = svc.begin(request_id="r-wave")["request_id"]
    return svc.route(rid, text)


def test_route_show_waveform_steers_to_answer_not_task(tmp_path):
    """RED: route('show waveform') must be answer (ask flips), never task."""
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = _route(cfg, svc, "show waveform")
    assert out.get("mode") == "answer", out
    assert out.get("question") == "show waveform"


def test_route_hide_waveform_steers_to_answer(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = _route(cfg, svc, "hide waveform")
    assert out.get("mode") == "answer", out


def test_route_waveform_with_the_steers_to_answer(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    assert _route(cfg, svc, "show the waveform").get("mode") == "answer"
    assert _route(cfg, svc, "hide the waveform").get("mode") == "answer"


def test_route_waveform_near_miss_not_steered(tmp_path):
    """Settings questions without the adjacent verb must keep router path."""
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = _route(cfg, svc, "show me the waveform settings")
    assert out.get("mode") != "answer" or \
        out.get("decision", {}).get("reason") != "waveform-visible", out


def test_full_wake_path_show_waveform_flips(tmp_path):
    """hey-qubit prefix → strip → route → ask flips the setting + speaks."""
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})
    remainder = strip_wake("hey qubit, show waveform")
    assert remainder == "show waveform", remainder
    routed = _route(cfg, svc, remainder)
    assert routed.get("mode") == "answer", routed
    out = svc.ask(routed["question"])
    assert out.get("ok") is True
    assert out.get("mode") == "waveform"
    assert "waveform on" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True


def test_full_wake_path_hide_waveform_flips(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": True})
    remainder = strip_wake("hey qubit, hide waveform")
    assert remainder == "hide waveform", remainder
    routed = _route(cfg, svc, remainder)
    assert routed.get("mode") == "answer", routed
    out = svc.ask(routed["question"])
    assert out.get("mode") == "waveform"
    assert "waveform off" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
