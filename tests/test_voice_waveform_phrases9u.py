"""Owner-natural waveform phrases (voice-final fix lane, 2026-10-04).

Owner evidence (journal-verified): "hey qubit, waveform off" produced ZERO
flip — the widget regexes only accept verb-first "show/hide waveform", so
noun-first "waveform off/on" (and "turn on/off the waveform",
"enable/disable (the) waveform") can never fire. Journal 09:55:06 shows
"Waveform on." reaching the helper as a command with no flip (answered as
chat instead).

Contract pinned here:
- FIRES (route steers to answer, ask flips + speaks): the owner's verbatim
  "waveform off", plus "waveform on", "turn on/off (the) waveform",
  "enable/disable (the) waveform", and all pre-existing show/hide shapes
  (ingame variants UNCHANGED — still show/hide only).
- NEVER FIRES: "show me the waveform settings" (verb not adjacent),
  "waveform on the screen" (noun-first shape is end-anchored, so a question
  about the widget never flips it), "show waveform in game settings"
  (ingame qualifier not utterance-final — not the override).

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


def _route(cfg, svc, text):
    rid = svc.begin(request_id="r-wave-phrase")["request_id"]
    return svc.route(rid, text)


# (utterance, expected waveform_command() result)
FIRES = [
    # pre-existing verb-first shapes (regression cover)
    ("show waveform", "show"),
    ("hide waveform", "hide"),
    ("show the waveform", "show"),
    ("hide the waveform", "hide"),
    ("hey qubit, show waveform", "show"),
    # owner noun-first shapes (the 2026-10-04 gap)
    ("waveform off", "hide"),
    ("waveform on", "show"),
    ("Waveform on.", "show"),
    ("hey qubit, waveform off", "hide"),
    # turn on/off the waveform
    ("turn on the waveform", "show"),
    ("turn off the waveform", "hide"),
    ("turn on waveform", "show"),
    ("turn off waveform", "hide"),
    ("hey qubit, turn off the waveform", "hide"),
    # enable/disable (the) waveform
    ("enable waveform", "show"),
    ("enable the waveform", "show"),
    ("disable waveform", "hide"),
    ("disable the waveform", "hide"),
    # spaced two-word "wave form" (journal 2026-10-04 10:17:47, owner-verbatim
    # "Show Wave Form." — STT emits the spaced shape; same adjacency guards)
    ("show wave form", "show"),
    ("hide wave form", "hide"),
    ("Show Wave Form.", "show"),
    ("show the wave form", "show"),
    ("hide the wave form", "hide"),
    ("hey qubit, show wave form", "show"),
    ("wave form on", "show"),
    ("wave form off", "hide"),
    ("Wave Form Off.", "hide"),
    ("turn on the wave form", "show"),
    ("turn off the wave form", "hide"),
    ("enable the wave form", "show"),
    ("disable the wave form", "hide"),
]

NEVER_FIRES = [
    "show me the waveform settings",
    "waveform on the screen",
    "what is the waveform status",
    "can you describe the waveform widget",
    # spaced near-misses carry the same guards as the single token
    "show me the wave form settings",
    "wave form on the screen",
]


@pytest.mark.parametrize("utterance,expected", FIRES)
def test_waveform_command_accepts_owner_shapes(utterance, expected):
    """RED: noun-first / turn / enable shapes must parse like show/hide."""
    assert VoiceEditService.waveform_command(utterance) == expected


@pytest.mark.parametrize("utterance", NEVER_FIRES)
def test_waveform_command_keeps_near_miss_guards(utterance):
    assert VoiceEditService.waveform_command(utterance) is None


def test_ingame_variants_unchanged(tmp_path):
    """Ingame override still accepts show/hide only — no on/off there."""
    assert VoiceEditService.waveform_ingame_command("show waveform ingame") == "show"
    assert VoiceEditService.waveform_ingame_command("hide waveform ingame") == "hide"
    assert VoiceEditService.waveform_ingame_command("waveform off") is None
    assert VoiceEditService.waveform_ingame_command("waveform on") is None
    assert VoiceEditService.waveform_ingame_command(
        "show waveform in game settings") is None
    # spaced two-word shape accepted with the same end-anchored qualifier
    assert VoiceEditService.waveform_ingame_command(
        "show wave form ingame") == "show"
    assert VoiceEditService.waveform_ingame_command(
        "hide wave form ingame") == "hide"
    assert VoiceEditService.waveform_ingame_command(
        "show wave form in game settings") is None
    # ... and the bare widget regex still does not claim the settings question
    # as the in-game override either.
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("show waveform in game settings")
    assert out.get("mode") != "waveform-ingame", out


@pytest.mark.parametrize("utterance,expected", FIRES)
def test_route_steers_owner_shapes_to_answer(tmp_path, utterance, expected):
    """The live wake/helper path calls route() — it must steer to ask()."""
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    remainder = strip_wake(utterance) or utterance
    out = _route(cfg, svc, remainder)
    assert out.get("mode") == "answer", out
    assert out.get("decision", {}).get("reason") == "waveform-visible", out


def test_owners_verbatim_waveform_off_flips(tmp_path):
    """Journal-verbatim 2026-10-04: 'waveform off' flips + confirms."""
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": True})
    out = svc.ask("waveform off")
    assert out.get("ok") is True
    assert out.get("mode") == "waveform"
    assert "waveform off" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False


def test_waveform_on_flips(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})
    out = svc.ask("Waveform on.")
    assert out.get("ok") is True
    assert out.get("mode") == "waveform"
    assert "waveform on" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True


def test_full_wake_path_waveform_off_flips(tmp_path):
    """hey-qubit prefix → strip → route → ask flips the setting + speaks."""
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": True})
    remainder = strip_wake("hey qubit, waveform off")
    assert remainder == "waveform off", remainder
    routed = _route(cfg, svc, remainder)
    assert routed.get("mode") == "answer", routed
    out = svc.ask(routed["question"])
    assert out.get("mode") == "waveform"
    assert "waveform off" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False


def test_owners_verbatim_show_wave_form_flips(tmp_path):
    """Journal-verbatim 2026-10-04 10:17:47: 'Show Wave Form.' flips + confirms.

    The spaced two-word shape reached route() as task/coding (confirm-gated
    proposal, never flipped). It must steer to answer and flip like the
    single token.
    """
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})
    routed = _route(cfg, svc, "Show Wave Form.")
    assert routed.get("mode") == "answer", routed
    assert routed.get("decision", {}).get("reason") == "waveform-visible", routed
    out = svc.ask(routed["question"])
    assert out.get("ok") is True
    assert out.get("mode") == "waveform"
    assert "waveform on" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True
