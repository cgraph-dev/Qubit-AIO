"""Voice sleep mode (Slice 4a, 9u).

Manual sleep mode (owner decision 2026-10-03 §5 #1): saying "going to sleep" /
"I'm going to sleep" silences UNPROMPTED speech (completions, briefings /
proactive) until the next directed "hey qubit". Sleep gates (b) unprompted,
never (a) on-request (decision §5 #2): direct answers always work.

No services, no browser, no model, no live config writes (tmp_path only).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import settings as settings_mod
from voicebridge.voice_edit.schema import SelectionSnapshot
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


# -- settings key -----------------------------------------------------------

def test_sleep_mode_defaults_false_when_unset(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    assert settings_mod.settings_view(cfg)["sleep_mode"] is False


def test_sleep_mode_put_roundtrip(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    settings_mod.update_voice_edit(cfg, {"sleep_mode": True})
    assert settings_mod.settings_view(cfg)["sleep_mode"] is True
    settings_mod.update_voice_edit(cfg, {"sleep_mode": False})
    assert settings_mod.settings_view(cfg)["sleep_mode"] is False


# -- sleep intent ------------------------------------------------------------

def test_sleep_command_sets_mode(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("I'm going to sleep")
    assert out.get("ok") is True
    assert out.get("mode") == "sleep"
    assert svc.sleeping() is True
    assert settings_mod.settings_view(cfg)["sleep_mode"] is True


def test_sleep_command_variant_without_contraction(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("going to sleep")
    assert out.get("ok") is True
    assert out.get("mode") == "sleep"
    assert svc.sleeping() is True


# -- gate: unprompted suppressed, on-request never gated -----------------------

def test_unprompted_suppressed_while_sleeping(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    assert svc.should_suppress_unprompted() is False
    svc.set_sleep_mode(True)
    assert svc.should_suppress_unprompted() is True
    assert svc.may_speak_unprompted() is False
    svc.set_sleep_mode(False)
    assert svc.should_suppress_unprompted() is False
    assert svc.may_speak_unprompted() is True


def test_direct_answer_never_gated_while_sleeping(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    svc.set_sleep_mode(True)
    # Deterministic clock path: no model in the loop.
    out = svc.ask("what time is it")
    assert out.get("ok") is True
    assert "It is" in str(out.get("answer") or "")
    # A non-directed on-request turn answers but does NOT clear sleep.
    assert svc.sleeping() is True


def test_directed_wake_clears_sleep_and_answers(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    svc.set_sleep_mode(True)
    out = svc.ask("hey qubit, what time is it")
    assert out.get("ok") is True
    assert "It is" in str(out.get("answer") or "")
    assert svc.sleeping() is False
    assert svc.should_suppress_unprompted() is False
