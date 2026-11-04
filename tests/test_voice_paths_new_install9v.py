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
import subprocess
import sys
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
                "XDG_CACHE_HOME", "LOCALAPPDATA"):
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
    # Uninitialized means "no old data": it is a new install, so the runtime
    # must write the new tree — never an XDG tree that a later run would
    # misread as an existing/read-old install.
    for kind in KINDS:
        assert layout.effective[kind] == home / ".qubit" / "voice" / kind


def test_uninitialized_runtime_creates_only_the_new_tree(_isolated):
    """A new install that runs before the installer writes $QUBIT_HOME/voice.

    This is the regression guard for the old behavior: an uninitialized runtime
    used the XDG paths, so it materialized ``~/.config|data|state/qubit-voicebridge``
    and every later resolution flipped to read-old.
    """
    C = _cfg_module()
    home = _isolated

    cfg = C.Config.load()

    assert cfg.path is None
    assert cfg.state_dir() == home / ".qubit" / "voice" / "state"
    assert cfg.data_dir() == home / ".qubit" / "voice" / "data"
    assert cfg.model_dir() == home / ".qubit" / "voice" / "models"
    assert (home / ".qubit" / "voice" / "state").is_dir()
    assert (home / ".qubit" / "voice" / "data").is_dir()
    assert (home / ".qubit" / "voice" / "models").is_dir()
    # No new XDG voice tree is created for a new install.
    assert not (home / ".config").exists()
    assert not (home / ".local").exists()


def test_uninitialized_runtime_home_is_the_qubit_home(_isolated):
    from voicebridge import config as C
    from voicebridge import qubit_runtime as QR

    home = _isolated
    assert C.voice_layout().mode == "uninitialized"
    assert QR.default_home() == home / ".qubit"
    assert C.canonical_runtime_home() == home / ".qubit"
    assert C.canonical_runtime_env_path() == (
        home / ".qubit" / "voice" / "config" / ".env")


def test_uninitialized_config_rewrites_xdg_defaults_to_the_new_tree(_isolated):
    C = _cfg_module()
    home = _isolated

    cfg = C.Config.load()

    assert cfg.get("tts", "openai", "key_env") == str(
        home / ".qubit" / "voice" / "config" / ".env")
    assert cfg.get("intent", "templates_path") == str(
        home / ".qubit" / "voice" / "config" / "task-templates.yaml")


def test_full_runtime_path_set_never_creates_xdg_for_a_new_install(_isolated):
    """Run the real path consumers in a clean interpreter.

    ``Config`` and the module-level feature-flag state are representative of
    every ``preferred_*_dir`` caller; all of them must land under
    ``$QUBIT_HOME/voice`` when the machine has no existing install.
    """
    home = _isolated
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    code = (
        "from voicebridge import config as C, feature_flags as F\n"
        "cfg = C.Config.load()\n"
        "print(cfg.state_dir())\n"
        "print(cfg.data_dir())\n"
        "print(cfg.model_dir())\n"
        "print(C.canonical_runtime_home())\n"
        "print(C.canonical_runtime_env_path())\n"
        "print(F.DEFAULT_FLAG_STATE)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    lines = [Path(line) for line in r.stdout.splitlines()]
    assert len(lines) == 6
    for path in lines:
        assert path == home / ".qubit" or path.is_relative_to(home / ".qubit"), path
    assert not (home / ".config").exists()
    assert not (home / ".local").exists()


# ---------------------------------------------------------------------------
# Platform path shapes
# ---------------------------------------------------------------------------
def test_linux_and_macos_default_home_is_dot_qubit(monkeypatch, tmp_path):
    P = _layout_module()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(home))

    for platform in ("linux", "darwin"):
        monkeypatch.setattr(P.sys, "platform", platform)
        assert P.qubit_home() == home / ".qubit"
        assert P.voice_home() == home / ".qubit" / "voice"


def test_windows_default_home_is_localappdata_qubit(monkeypatch, tmp_path):
    P = _layout_module()
    local = tmp_path / "AppData" / "Local"
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(P.sys, "platform", "win32")

    assert P.qubit_home() == local / "qubit"
    assert P.voice_home() == local / "qubit" / "voice"
    assert P.voice_layout().new["models"] == local / "qubit" / "voice" / "models"


def test_windows_falls_back_to_userprofile_appdata_without_localappdata(
        monkeypatch, tmp_path):
    P = _layout_module()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(P.sys, "platform", "win32")

    assert P.qubit_home() == home / "AppData" / "Local" / "qubit"


def test_explicit_qubit_home_wins_on_every_platform(monkeypatch, tmp_path):
    P = _layout_module()
    explicit = tmp_path / "custom-home"
    monkeypatch.setenv("QUBIT_HOME", str(explicit))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    for platform in ("linux", "darwin", "win32"):
        monkeypatch.setattr(P.sys, "platform", platform)
        assert P.qubit_home() == explicit
        assert P.voice_home() == explicit / "voice"


def test_external_qubit_home_falls_back_to_the_canonical_default(
        monkeypatch, tmp_path):
    P = _layout_module()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(P.sys, "platform", "linux")
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))

    # ``~/.hermes`` is the read-old external home; writing $QUBIT_HOME/voice
    # there would create a new tree inside it.
    assert P.qubit_home() == home / ".qubit"
    assert P.voice_home() == home / ".qubit" / "voice"


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
    (home / ".local" / "state" / "qubit-voicebridge").mkdir(parents=True)
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


def test_runtime_home_for_cfg_matches_the_layout(_isolated, monkeypatch):
    """The cfg-based resolver agrees with default_home/canonical_runtime_home.

    With no ambient QUBIT_HOME, a new install's runtime home is the Qubit home
    itself (not ``$QUBIT_HOME/voice/state/runtime``), so the bridge, store and
    CLI share one ``state.db``.
    """
    from voicebridge import config as C
    from voicebridge import qubit_runtime as QR

    home = _isolated
    cfg = C.Config.load()
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setattr(QR, "_PINNED_QUBIT_HOME", None, raising=False)

    assert QR.runtime_home_for_cfg(cfg) == home / ".qubit"

    # A custom jsonl path still owns its own runtime child.
    custom = home / "custom-state"
    cfg.data.setdefault("logging", {})["jsonl"] = True
    cfg.data["logging"]["jsonl_path"] = str(custom)
    monkeypatch.setattr(QR, "_PINNED_QUBIT_HOME", None, raising=False)
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    assert QR.runtime_home_for_cfg(cfg) == custom / "runtime"


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
