"""Dashboard waveform visibility (voice-final lane).

Backend setting ``voice_edit.waveform_visible`` (default False per owner
2026-10-04 — the OS orb is the only always-on waveform; "show waveform"
re-mounts explicitly) + voice intents "show waveform" / "hide waveform" through
the EXISTING ask-stage mechanism (same shape as sleep mode: deterministic,
audited, remembered — no parallel dispatcher).

No services, no browser, no model, no live config writes (tmp_path only).
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
    path = tmp_path / "voice.yaml"
    import yaml

    path.write_text(yaml.safe_dump(data or {}), encoding="utf-8")
    return FakeCfg(data={}, path=path)


# -- settings key -----------------------------------------------------------

def test_waveform_visible_defaults_false_when_unset(tmp_path):
    # Owner 2026-10-04: dashboard waveform defaults OFF (the OS orb is the
    # only always-on waveform). A fresh/absent key means HIDDEN; "show
    # waveform" re-mounts explicitly. This deliberately reverses the old
    # default-True contract pinned here before.
    cfg = _cfg_with_file(tmp_path)
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False


def test_waveform_visible_put_roundtrip(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    settings_mod.update_voice_edit(cfg, {"waveform_visible": True})
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True


def test_waveform_visible_coerced_like_sibling_bools(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": 0})
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    settings_mod.update_voice_edit(cfg, {"waveform_visible": 1})
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True


# -- voice intents ----------------------------------------------------------

def test_show_waveform_flips_visible_true(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})
    out = svc.ask("show waveform")
    assert out.get("ok") is True
    assert out.get("mode") == "waveform"
    assert "waveform on" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True
    assert any(op == "voice_edit.waveform.set"
               and d.get("visible") is True for op, d in audit_log)


def test_hide_waveform_flips_visible_false(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    out = svc.ask("hide waveform")
    assert out.get("ok") is True
    assert out.get("mode") == "waveform"
    assert "waveform off" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    assert any(op == "voice_edit.waveform.set"
               and d.get("visible") is False for op, d in audit_log)


def test_waveform_intent_with_wake_prefix_and_the(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("hey qubit, hide the waveform")
    assert out.get("mode") == "waveform"
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False


def test_waveform_near_miss_does_not_fire(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    # Boundary pin: settings questions without the command verb adjacent
    # to "waveform" must not flip the widget.
    assert svc.waveform_command("show me the waveform settings") is None
    assert svc.waveform_command("what are the waveform settings") is None
    # Fresh cfg: absent key resolves to the hidden default (owner 2026-10-04).
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
