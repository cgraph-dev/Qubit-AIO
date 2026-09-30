"""Voice-typing STT + router settings for the one voice router.

Covers the three new ``voice_edit`` settings -- ``stt_mode``, ``stt_model`` and
``router`` -- for defaults, validation, and round-trip persistence. Every test
uses a temp config path via the fake cfg's ``.path`` so the real
``~/.config/qubit-voicebridge/voice.yaml`` is never touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import settings as settings_mod
from voicebridge.voice_edit.schema import VoiceEditError


class FakeCfg:
    """Mirrors the settings tests' cfg surface: ``.path``, ``.get`` and
    ``.data`` (the live tree ``_sync_live_cfg`` reflects into)."""

    def __init__(self, data=None, path=None):
        self.data = data or {}
        self.path = path

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """Never resolve the real config/data roots during these tests."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _cfg(tmp_path: Path) -> FakeCfg:
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")
    return FakeCfg({"voice_edit": {"enabled": True}}, path=p)


def _fresh_cfg(tmp_path: Path) -> FakeCfg:
    """A second, untouched config target for export -> import round-trips."""
    p = tmp_path / "voice2.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")
    return FakeCfg({"voice_edit": {"enabled": True}}, path=p)


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------
def test_settings_view_returns_stt_defaults_when_unset(tmp_path):
    cfg = _cfg(tmp_path)
    view = settings_mod.settings_view(cfg)
    assert view["stt_mode"] == "accurate"
    assert view["stt_model"] == "small"
    assert view["router"] == "deterministic"
    # Reading defaults must not have persisted anything.
    assert "stt_mode" not in cfg.path.read_text()
    assert "stt_model" not in cfg.path.read_text()
    assert "router" not in cfg.path.read_text()


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["bogus", "STREAMING", "", 1, None])
def test_update_rejects_bad_stt_mode(tmp_path, value):
    cfg = _cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, {"stt_mode": value})
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"


@pytest.mark.parametrize("value", ["huge", "large", "tiny.en", "", 5, None])
def test_update_rejects_bad_stt_model(tmp_path, value):
    cfg = _cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, {"stt_model": value})
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"


@pytest.mark.parametrize("value", ["both", "determinstic", "", 0, None])
def test_update_rejects_bad_router(tmp_path, value):
    cfg = _cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, {"router": value})
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"


# ---------------------------------------------------------------------------
# accepted values round-trip
# ---------------------------------------------------------------------------
def test_update_accepts_valid_values_and_view_reflects(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "stt_mode": "streaming",
        "stt_model": "medium",
        "router": "brain",
    })
    view = settings_mod.settings_view(cfg)
    assert view["stt_mode"] == "streaming"
    assert view["stt_model"] == "medium"
    assert view["router"] == "brain"
    text = cfg.path.read_text()
    assert "stt_mode: streaming" in text
    assert "stt_model: medium" in text
    assert "router: brain" in text


@pytest.mark.parametrize("mode", ["streaming", "accurate"])
@pytest.mark.parametrize("model", ["tiny", "base", "small", "medium", "large-v3"])
@pytest.mark.parametrize("router", ["deterministic", "brain"])
def test_update_accepts_all_valid_enum_values(tmp_path, mode, model, router):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "stt_mode": mode,
        "stt_model": model,
        "router": router,
    })
    view = settings_mod.settings_view(cfg)
    assert (view["stt_mode"], view["stt_model"], view["router"]) == (
        mode, model, router)


# ---------------------------------------------------------------------------
# export -> import round-trip
# ---------------------------------------------------------------------------
def test_export_import_round_trips_stt_and_routing(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "stt_mode": "streaming",
        "stt_model": "base",
        "router": "brain",
        "asr_device": "qubit_aec",
        "stt_engine": "nemotron-560ms",
        "routing": "api",
        "local_profile": "qubit-brain4",
        "api_profile": "openai",
        "allow_api": False,
        "confirm_before_replace": False,
        "auto_send": True,
        "insertion_method": "typed",
    })
    exported = settings_mod.export_settings(cfg)
    # export must carry the new keys in the settings section
    assert exported["settings"]["stt_mode"] == "streaming"
    assert exported["settings"]["stt_model"] == "base"
    assert exported["settings"]["router"] == "brain"
    assert exported["settings"]["asr_device"] == "qubit_aec"
    assert exported["settings"]["stt_engine"] == "nemotron-560ms"

    p2 = tmp_path / "voice2.yaml"
    p2.write_text("voice_edit:\n  enabled: true\n")
    cfg2 = FakeCfg({"voice_edit": {"enabled": True}}, path=p2)
    settings_mod.import_settings(cfg2, exported)

    view = settings_mod.settings_view(cfg2)
    assert view["stt_mode"] == "streaming"
    assert view["stt_model"] == "base"
    assert view["router"] == "brain"
    assert view["asr_device"] == "qubit_aec"
    assert view["stt_engine"] == "nemotron-560ms"
    # existing routing keys round-trip too
    assert view["routing"] == "api"
    assert view["allow_api"] is False
    assert view["confirm_before_replace"] is False
    assert view["auto_send"] is True
    assert view["insertion_method"] == "typed"
    text = p2.read_text()
    assert "stt_mode: streaming" in text
    assert "stt_model: base" in text
    assert "router: brain" in text


@pytest.mark.parametrize("key,value", [
    ("stt_mode", "bogus"),
    ("stt_model", "large"),
    ("router", "both"),
])
def test_import_rejects_invalid_values(tmp_path, key, value):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "stt_mode": "streaming",
        "stt_model": "base",
        "router": "brain",
    })
    exported = settings_mod.export_settings(cfg)
    exported["settings"][key] = value

    p2 = tmp_path / "voice2.yaml"
    p2.write_text("voice_edit:\n  enabled: true\n")
    cfg2 = FakeCfg({"voice_edit": {"enabled": True}}, path=p2)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.import_settings(cfg2, exported)
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"
    # nothing persisted through the failed import
    assert key not in p2.read_text()


# ---------------------------------------------------------------------------
# default action: export and import agree on the ``default_prompt`` wire key
# ---------------------------------------------------------------------------
def test_export_emits_default_prompt_not_default_action(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"default_action": "summarize"})
    exported = settings_mod.export_settings(cfg)
    # The public/exported key is ``default_prompt`` (what settings_view returns);
    # the persisted ``default_action`` key must not leak into the export.
    assert exported["settings"]["default_prompt"] == "summarize"
    assert "default_action" not in exported["settings"]


def test_export_import_round_trips_default_prompt(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"default_action": "summarize"})
    exported = settings_mod.export_settings(cfg)

    cfg2 = _fresh_cfg(tmp_path)
    settings_mod.import_settings(cfg2, exported)
    assert settings_mod.settings_view(cfg2)["default_prompt"] == "summarize"
    # ... persisted under the canonical internal key.
    assert "default_action: summarize" in cfg2.path.read_text()


def test_import_accepts_legacy_default_action_key(tmp_path):
    cfg = _fresh_cfg(tmp_path)
    settings_mod.import_settings(cfg, {"settings": {"default_action": "email"}})
    assert settings_mod.settings_view(cfg)["default_prompt"] == "email"


def test_import_accepts_default_prompt_wire_key(tmp_path):
    cfg = _fresh_cfg(tmp_path)
    settings_mod.import_settings(cfg, {"settings": {"default_prompt": "shorten"}})
    assert settings_mod.settings_view(cfg)["default_prompt"] == "shorten"


@pytest.mark.parametrize("key", ["default_prompt", "default_action"])
def test_import_rejects_invalid_default_action(tmp_path, key):
    cfg = _fresh_cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.import_settings(cfg, {"settings": {key: "bogus"}})
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"
    assert "bogus" not in cfg.path.read_text()


@pytest.mark.parametrize("key", ["default_prompt", "default_action"])
def test_update_rejects_invalid_default_action(tmp_path, key):
    cfg = _cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, {key: "bogus"})
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"


def test_export_import_round_trips_new_keys_including_default_prompt(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "stt_mode": "accurate",
        "stt_model": "small",
        "router": "deterministic",
        "asr_device": "qubit_aec",
        "stt_engine": "nemotron-560ms",
        "default_action": "humanize",
    })
    exported = settings_mod.export_settings(cfg)

    cfg2 = _fresh_cfg(tmp_path)
    settings_mod.import_settings(cfg2, exported)
    view = settings_mod.settings_view(cfg2)
    assert view["stt_mode"] == "accurate"
    assert view["stt_model"] == "small"
    assert view["router"] == "deterministic"
    assert view["asr_device"] == "qubit_aec"
    assert view["stt_engine"] == "nemotron-560ms"
    assert view["default_prompt"] == "humanize"
