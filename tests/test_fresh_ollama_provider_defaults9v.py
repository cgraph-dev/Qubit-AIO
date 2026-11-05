"""Fresh runtime configs must carry a usable local Ollama endpoint.

Defect: the first runtime-config writers (the dashboard model mirror and the
``/chat`` terminal profile write) could create ``<QUBIT_HOME>/config.yaml``
with only ``model: {provider: ollama, default: qubit-brain}``. The vendored
resolver then refused chat with ``provider 'ollama' has no endpoint
configured`` because ``providers.ollama.base_url`` was absent.

Contract pinned here:

* both writers fill the safe local defaults on a fresh config;
* an existing user ``providers.ollama`` value (base_url/api_mode/default_model/
  key_env) is preserved, and unrelated providers are untouched;
* the configured default model stays ``qubit-brain``;
* the resulting fresh config resolves to the loopback Ollama endpoint.

Hermetic: isolated state/home dirs, no network or model call.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

OLLAMA_DEFAULTS = {
    "base_url": "http://127.0.0.1:11434/v1",
    "api_mode": "chat_completions",
    "default_model": "qubit-brain",
    "key_env": "",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge import qubit_runtime

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = C.Config(data={
        "logging": {"jsonl": True, "jsonl_path": str(state)},
        "intent": {"frontier": {"profile_overrides": {}}},
    }, path=tmp_path / "voice.yaml")
    home = tmp_path / "qh"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - hermetic home binding only
        pass
    return cfg, Path(qubit_runtime.runtime_home_for_cfg(cfg))


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_fresh_model_persist_fills_ollama_provider_defaults(tmp_path, monkeypatch):
    from voicebridge.providers import endpoints as EP

    cfg, runtime_home = _cfg(tmp_path, monkeypatch)
    assert EP._persist_runtime_model(
        cfg, provider="ollama", model="qubit-brain",
        base_url="", key_ref="") is True

    data = _read(runtime_home / "config.yaml")
    assert data["model"] == {"provider": "ollama", "default": "qubit-brain"}
    assert data["providers"]["ollama"] == OLLAMA_DEFAULTS


def test_fresh_terminal_profile_write_fills_ollama_provider_defaults(tmp_path, monkeypatch):
    from voicebridge import pty_ws

    _cfg_obj, runtime_home = _cfg(tmp_path, monkeypatch)
    assert pty_ws._write_terminal_profile_config(
        runtime_home, {"provider": "ollama", "model": "qubit-brain",
                       "base_url": ""}) is True

    data = _read(runtime_home / "config.yaml")
    assert data["model"] == {"provider": "ollama", "default": "qubit-brain"}
    assert data["providers"]["ollama"] == OLLAMA_DEFAULTS


def test_existing_ollama_provider_values_are_preserved(tmp_path, monkeypatch):
    from voicebridge.providers import endpoints as EP

    cfg, runtime_home = _cfg(tmp_path, monkeypatch)
    config_path = runtime_home / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump({
        "providers": {
            "ollama": {
                "base_url": "http://127.0.0.1:9999/v1",
                "api_mode": "responses",
                "default_model": "my-local-model",
                "key_env": "env:MY_LOCAL_KEY",
                "extra": "keep-me",
            },
            "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
        },
        "custom_providers": [{"name": "work", "base_url": "https://work.example/v1"}],
    }), encoding="utf-8")

    assert EP._persist_runtime_model(
        cfg, provider="ollama", model="qubit-brain",
        base_url="", key_ref="") is True

    data = _read(config_path)
    assert data["providers"]["ollama"] == {
        "base_url": "http://127.0.0.1:9999/v1",
        "api_mode": "responses",
        "default_model": "my-local-model",
        "key_env": "env:MY_LOCAL_KEY",
        "extra": "keep-me",
    }
    assert data["providers"]["openrouter"] == {
        "base_url": "https://openrouter.ai/api/v1"}
    assert data["custom_providers"] == [
        {"name": "work", "base_url": "https://work.example/v1"}]
    # The selected default model is still the local brain.
    assert data["model"] == {"provider": "ollama", "default": "qubit-brain"}


def test_fresh_config_resolves_the_local_ollama_endpoint(tmp_path, monkeypatch):
    from qubit_cli import runtime_provider as rp
    from voicebridge.providers import endpoints as EP

    cfg, runtime_home = _cfg(tmp_path, monkeypatch)
    assert EP._persist_runtime_model(
        cfg, provider="ollama", model="qubit-brain",
        base_url="", key_ref="") is True
    data = _read(runtime_home / "config.yaml")

    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "CUSTOM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rp, "load_config", lambda: data)
    monkeypatch.setattr(
        rp, "load_pool",
        lambda _provider: SimpleNamespace(has_credentials=lambda: False))

    resolved = rp.resolve_runtime_provider(requested="ollama")

    assert resolved["provider"] == "custom"
    assert resolved["base_url"] == "http://127.0.0.1:11434/v1"
    assert resolved["api_key"] == "no-key-required"
