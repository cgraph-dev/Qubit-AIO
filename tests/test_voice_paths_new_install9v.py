"""VoiceBridge install layout — new ``$QUBIT_HOME/voice`` vs read-old XDG.

The single resolver in ``voicebridge.paths`` and the Config loader must agree:
new installs resolve under ``$QUBIT_HOME/voice/{config,data,state,cache,models}``;
an existing XDG install is read in place and is never moved, deleted, or
silently split.

Hermetic: throwaway HOME/XDG/QUBIT_HOME only. No installer run, no live
service, no network, no live data.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

KINDS = ("config", "data", "state", "cache", "models")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QUBIT_HOME", str(home / ".qubit"))
    monkeypatch.delenv("VOICE_BRIDGE_CONFIG", raising=False)
    yield tmp_path / "home"


def _cfg_module():
    from voicebridge import config as C
    return C


def _layout_module():
    from voicebridge import paths as P
    return P


def test_fresh_machine_resolves_new_install_paths(_isolated):
    P = _layout_module()
    home = _isolated
    layout = P.voice_layout()
    assert layout.mode == "uninitialized"
    assert layout.install_mode == "new"
    assert layout.qubit_home == home / ".qubit"
    assert layout.voice_root == home / ".qubit" / "voice"
    for kind in KINDS:
        assert layout.new[kind] == home / ".qubit" / "voice" / kind
    # Until the installer initializes the new tree, runtime keeps the old XDG
    # paths so a pre-existing process does not write a new tree unasked.
    assert layout.effective["config"] == home / ".config" / "qubit-voicebridge"


def test_initialized_new_tree_wins_and_uses_all_five_dirs(_isolated):
    P = _layout_module()
    home = _isolated
    (home / ".qubit" / "voice").mkdir(parents=True)
    layout = P.voice_layout()
    assert layout.mode == "new"
    assert layout.install_mode == "new"
    for kind in KINDS:
        assert layout.effective[kind] == home / ".qubit" / "voice" / kind


def test_existing_xdg_install_is_read_old_without_being_touched(_isolated):
    P = _layout_module()
    home = _isolated
    old = home / ".config" / "qubit-voicebridge"
    old_data = home / ".local" / "share" / "qubit-voicebridge"
    old_state = home / ".local" / "state" / "qubit-voicebridge"
    old.mkdir(parents=True)
    old_data.mkdir(parents=True)
    old_state.mkdir(parents=True)
    cfg_file = old / "voice.yaml"
    cfg_file.write_text("audio:\n  output_volume: 77\n", encoding="utf-8")
    before = cfg_file.read_bytes()

    layout = P.voice_layout()

    assert layout.mode == "read-old"
    assert layout.install_mode == "read-old"
    assert layout.effective["config"] == old
    assert layout.effective["data"] == old_data
    assert layout.effective["state"] == old_state
    assert layout.effective["models"] == old_data / "models"
    assert set(layout.existing_read_old) >= {"config", "data", "state"}
    # Resolution is read-only: no new tree, no mutation of old data.
    assert not (home / ".qubit").exists()
    assert cfg_file.read_bytes() == before


def test_new_tree_wins_when_both_layouts_exist(_isolated):
    P = _layout_module()
    home = _isolated
    (home / ".config" / "qubit-voicebridge").mkdir(parents=True)
    (home / ".qubit" / "voice").mkdir(parents=True)
    layout = P.voice_layout()
    assert layout.mode == "new"
    assert layout.effective["config"] == home / ".qubit" / "voice" / "config"
    # The old tree is still reported read-old, never hidden or deleted.
    assert "config" in layout.existing_read_old


def test_config_load_uses_new_tree_and_rewrites_bundled_defaults(_isolated):
    C = _cfg_module()
    home = _isolated
    cfg_dir = home / ".qubit" / "voice" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "voice.yaml").write_text(
        "logging:\n  jsonl: false\naudio:\n  output_volume: 41\n",
        encoding="utf-8")

    cfg = C.Config.load()

    assert cfg.path == cfg_dir / "voice.yaml"
    assert cfg.legacy_source is None
    assert cfg.get("audio", "output_volume") == 41
    assert cfg.state_dir() == home / ".qubit" / "voice" / "state"
    assert cfg.data_dir() == home / ".qubit" / "voice" / "data"
    assert cfg.model_dir() == home / ".qubit" / "voice" / "models"
    # The shipped defaults still name the old XDG tree; a new install must
    # resolve them into the new tree rather than pointing at a tree it never
    # created.
    assert cfg.get("tts", "openai", "key_env") == str(cfg_dir / ".env")


def test_config_load_reads_old_tree_in_place(_isolated):
    C = _cfg_module()
    home = _isolated
    old = home / ".config" / "qubit-voicebridge"
    old.mkdir(parents=True)
    cfg_file = old / "voice.yaml"
    cfg_file.write_text(
        "logging:\n  jsonl: false\naudio:\n  output_volume: 42\n",
        encoding="utf-8")
    before = cfg_file.read_bytes()

    cfg = C.Config.load()

    assert cfg.path == cfg_file
    assert cfg.legacy_source == str(cfg_file)
    assert cfg.get("audio", "output_volume") == 42
    assert cfg.state_dir() == home / ".local" / "state" / "qubit-voicebridge"
    assert cfg.model_dir() == (
        home / ".local" / "share" / "qubit-voicebridge" / "models")
    # Read-old is in place: no new tree, no mutation of the old file.
    assert not (home / ".qubit").exists()
    assert cfg_file.read_bytes() == before


def test_resolve_model_file_prefers_new_then_reads_old(_isolated):
    C = _cfg_module()
    home = _isolated
    old_model = (home / ".local" / "share" / "qubit-voicebridge" / "models"
                 / "m.bin")
    old_model.parent.mkdir(parents=True)
    old_model.write_text("old", encoding="utf-8")

    # Existing XDG install: the old model resolves in place.
    assert C.resolve_model_file("m.bin") == old_model

    # New install: a copy under the new tree wins; the old file still serves
    # as a read-only fallback for a model the new tree has not acquired yet.
    new_model = home / ".qubit" / "voice" / "models" / "m.bin"
    new_model.parent.mkdir(parents=True)
    new_model.write_text("new", encoding="utf-8")
    assert C.resolve_model_file("m.bin") == new_model

    absent = C.resolve_model_file("absent.bin")
    assert absent == home / ".qubit" / "voice" / "models" / "absent.bin"


def test_runtime_home_follows_the_layout(_isolated):
    from voicebridge import config as C
    from voicebridge import qubit_runtime as QR

    home = _isolated
    # Existing XDG install keeps the historical runtime home.
    assert QR.default_home() == (
        home / ".local" / "state" / "qubit-voicebridge" / "runtime")
    assert C.canonical_runtime_home() == QR.default_home()

    # A new install puts the runtime at QUBIT_HOME itself and its credential
    # file with the VoiceBridge config.
    (home / ".qubit" / "voice").mkdir(parents=True)
    assert QR.default_home() == home / ".qubit"
    assert C.canonical_runtime_home() == home / ".qubit"
    assert C.canonical_runtime_env_path() == (
        home / ".qubit" / "voice" / "config" / ".env")


def test_legacy_identity_models_remain_read_only_fallbacks(_isolated):
    C = _cfg_module()
    home = _isolated
    legacy = (home / ".local" / "share" / "openclaw-voice-bridge" / "models"
              / "legacy.bin")
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
    # Uninitialized layout keeps the historical XDG effective path; legacy
    # identities are still explicit read-only fallbacks.
    assert C.resolve_model_file("legacy.bin") == legacy
