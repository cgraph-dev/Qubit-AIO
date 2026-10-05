"""Full waveform phrase table (2026-10-05): every accepted utterance pinned.

Drives the REAL functions (route()/ask() on a scratch service), not just
regex unit tests. Four commands:

- widget show -> voice_edit.waveform_visible=True,  receipt "Waveform on."
- widget hide -> voice_edit.waveform_visible=False, receipt "Waveform off."
- ingame show -> voice_edit.waveform_ingame=True,   receipt "Game overlay on."
- ingame hide -> voice_edit.waveform_ingame=False,  receipt "Game overlay off."

Ingame qualifier ("ingame"/"in-game"/"in game") must END the utterance and is
checked BEFORE the bare widget shapes in both route() and ask(): a qualified
utterance must NEVER flip the widget flag. Bare widget shapes must NEVER touch
the ingame flag.

Near-misses that must NOT fire the override: "show waveform in game
settings" (settings question), "waveform on the screen", "show me the
waveform settings".

No services, no browser, no model, no live config writes (tmp_path only).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def _route(svc, text):
    rid = svc.begin(request_id="r-wave-table")["request_id"]
    return svc.route(rid, text)


# (utterance, ...) — the full accepted table per command.
WIDGET_SHOW = [
    "show waveform",
    "Show waveform.",
    "SHOW WAVEFORM",
    "show the waveform",
    "hide waveform".replace("hide", "show") + " please",  # trailing word: substring fires
    "hey qubit, show waveform",
    "show wave form",
    "Show Wave Form.",
    "show the wave form",
    "turn on waveform",
    "turn on the waveform",
    "turn on the wave form",
    "hey qubit, turn on the waveform",
    "enable waveform",
    "enable the waveform",
    "enable the wave form",
    "waveform on",
    "Waveform on.",
    "wave form on",
    "Wave Form On.",
    "hey qubit, waveform on",
]

WIDGET_HIDE = [
    "hide waveform",
    "Hide waveform.",
    "HIDE WAVEFORM",
    "hide the waveform",
    "hey qubit, hide waveform",
    "hide wave form",
    "Hide Wave Form.",
    "hide the wave form",
    "turn off waveform",
    "turn off the waveform",
    "turn off the wave form",
    "hey qubit, turn off the waveform",
    "disable waveform",
    "disable the waveform",
    "disable the wave form",
    "waveform off",
    "Waveform off.",
    "wave form off",
    "Wave Form Off.",
    "hey qubit, waveform off",
]

INGAME_SHOW = [
    "show waveform ingame",
    "Show waveform ingame.",
    "SHOW WAVEFORM INGAME",
    "show the waveform ingame",
    "show waveform in game",
    "show waveform in-game",
    "show the waveform in game",
    "hey qubit, show waveform ingame",
    "show wave form ingame",
    "show wave form in game",
    "show the wave form in-game",
    "turn on waveform ingame",
    "turn on the waveform ingame",
    "turn on the waveform in game",
    "turn on the wave form ingame",
    "enable waveform ingame",
    "enable the waveform ingame",
    "enable the wave form in game",
    "waveform on ingame",
    "wave form on ingame",
    "waveform on in game",
    "hey qubit, show waveform in game",
]

INGAME_HIDE = [
    "hide waveform ingame",
    "Hide waveform ingame.",
    "HIDE WAVEFORM INGAME",
    "hide the waveform ingame",
    "hide waveform in game",
    "hide waveform in-game",
    "hide the waveform in game",
    "hey qubit, hide waveform ingame",
    "hide wave form ingame",
    "hide wave form in game",
    "hide the wave form in-game",
    "turn off waveform ingame",
    "turn off the waveform ingame",
    "turn off the waveform in game",
    "turn off the wave form ingame",
    "disable waveform ingame",
    "disable the waveform ingame",
    "disable the wave form in game",
    "waveform off ingame",
    "wave form off ingame",
    "waveform off in game",
    "hey qubit, hide waveform in game",
]

# (utterance, fires_widget, fires_ingame) — near-misses.
# "show waveform in game settings" is documented pre-existing behaviour (§84):
# the bare widget substring fires; the OVERRIDE must not.
NEAR_MISS = [
    ("show waveform in game settings", True, False),
    ("show wave form in game settings", True, False),
    ("show me the waveform settings", False, False),
    ("show me the wave form settings", False, False),
    ("waveform on the screen", False, False),
    ("wave form on the screen", False, False),
    ("what is the waveform status", False, False),
    ("can you describe the waveform widget", False, False),
    ("show waveform", True, False),  # sanity: bare widget is widget-only
    ("hide waveform", True, False),
    ("waveform on", True, False),
    ("waveform off", True, False),
]


@pytest.mark.parametrize("utterance", WIDGET_SHOW)
def test_widget_show_ask_flips_visible_only(tmp_path, utterance):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(
        cfg, {"waveform_visible": False, "waveform_ingame": False})
    out = svc.ask(utterance)
    assert out.get("ok") is True, out
    assert out.get("mode") == "waveform", out
    assert "waveform on" in str(out.get("answer") or "").lower(), out
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False
    assert any(op == "voice_edit.waveform.set"
               and d.get("visible") is True for op, d in audit_log), audit_log
    assert not any(op == "voice_edit.waveform_ingame.set"
                   for op, _ in audit_log), audit_log


@pytest.mark.parametrize("utterance", WIDGET_HIDE)
def test_widget_hide_ask_flips_visible_only(tmp_path, utterance):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(
        cfg, {"waveform_visible": True, "waveform_ingame": True})
    out = svc.ask(utterance)
    assert out.get("ok") is True, out
    assert out.get("mode") == "waveform", out
    assert "waveform off" in str(out.get("answer") or "").lower(), out
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    # Bare hide clears the widget, never the game-overlay override.
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True
    assert any(op == "voice_edit.waveform.set"
               and d.get("visible") is False for op, d in audit_log), audit_log
    assert not any(op == "voice_edit.waveform_ingame.set"
                   for op, _ in audit_log), audit_log


@pytest.mark.parametrize("utterance", INGAME_SHOW)
def test_ingame_show_ask_flips_ingame_only(tmp_path, utterance):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(
        cfg, {"waveform_visible": False, "waveform_ingame": False})
    out = svc.ask(utterance)
    assert out.get("ok") is True, out
    assert out.get("mode") == "waveform-ingame", out
    assert str(out.get("answer") or "") == "Game overlay on.", out
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    assert any(op == "voice_edit.waveform_ingame.set"
               and d.get("ingame") is True for op, d in audit_log), audit_log
    assert not any(op == "voice_edit.waveform.set"
                   for op, _ in audit_log), audit_log


@pytest.mark.parametrize("utterance", INGAME_HIDE)
def test_ingame_hide_ask_flips_ingame_only(tmp_path, utterance):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(
        cfg, {"waveform_visible": True, "waveform_ingame": True})
    out = svc.ask(utterance)
    assert out.get("ok") is True, out
    assert out.get("mode") == "waveform-ingame", out
    assert str(out.get("answer") or "") == "Game overlay off.", out
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True
    assert any(op == "voice_edit.waveform_ingame.set"
               and d.get("ingame") is False for op, d in audit_log), audit_log
    assert not any(op == "voice_edit.waveform.set"
                   for op, _ in audit_log), audit_log


@pytest.mark.parametrize("utterance", WIDGET_SHOW + WIDGET_HIDE)
def test_route_widget_shapes_steer_to_visible(tmp_path, utterance):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    remainder = strip_wake(utterance) or utterance
    out = _route(svc, remainder)
    assert out.get("mode") == "answer", out
    assert out.get("decision", {}).get("reason") == "waveform-visible", out


@pytest.mark.parametrize("utterance", INGAME_SHOW + INGAME_HIDE)
def test_route_ingame_shapes_steer_to_override(tmp_path, utterance):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    remainder = strip_wake(utterance) or utterance
    out = _route(svc, remainder)
    assert out.get("mode") == "answer", out
    assert out.get("decision", {}).get("reason") == "waveform-ingame", out


@pytest.mark.parametrize("utterance,fires_widget,fires_ingame", NEAR_MISS)
def test_near_miss_table(tmp_path, utterance, fires_widget, fires_ingame):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    assert (svc.waveform_command(utterance) is not None) is fires_widget, utterance
    assert (svc.waveform_ingame_command(utterance) is not None) is fires_ingame, utterance


def test_near_miss_settings_question_never_fires_override(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("show waveform in game settings")
    assert out.get("mode") != "waveform-ingame", out
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False


@pytest.mark.parametrize("utterance", INGAME_SHOW + INGAME_HIDE)
def test_full_wake_path_ingame_flips(tmp_path, utterance):
    """hey-prefix (when present) -> strip -> route -> ask flips ingame only."""
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    remainder = strip_wake(utterance) or utterance
    routed = _route(svc, remainder)
    assert routed.get("mode") == "answer", routed
    out = svc.ask(routed["question"])
    assert out.get("ok") is True, out
    assert out.get("mode") == "waveform-ingame", out
