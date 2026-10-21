"""profiles.create inherits the launch profile's model together with the custom gateway backing it.

``_inherit_launch_model`` pins the launch ``model`` into the new profile through the same
validated path as ``/api/model/set``, and that validation runs inside the new profile's home. A
launch model on a custom ``providers:`` gateway is therefore only valid once the gateway
definition is already in the profile — writing it after the pin never ran (#101885 / #94071).
"""

from __future__ import annotations

import pytest
import qubit_yaml as yaml

import tui_gateway.server as srv


def test_inherit_launch_model_carries_a_custom_provider_gateway(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setenv("FAKE_GW_TOKEN", "gw-FAKE-111")
    (home / "config.yaml").write_text(
        "model:\n  provider: my-gateway\n  default: my-finetune\n"
        "providers:\n  my-gateway:\n    api: https://llm.internal.example.com/v1\n    key_env: GW_KEY\n    api_key: ${FAKE_GW_TOKEN}\n"
        "  unrelated:\n    api: https://other.example.com/v1\n")
    profile = home / "profiles" / "scout"
    profile.mkdir(parents=True)

    assert srv._inherit_launch_model(profile) is True

    cfg = yaml.safe_load((profile / "config.yaml").read_text())
    assert (cfg["model"]["provider"], cfg["model"]["default"]) == ("my-gateway", "my-finetune")
    assert set(cfg["providers"]) == {"my-gateway"}
    # The gateway travels as the launch file wrote it: the ref, never the launch secret.
    assert cfg["providers"]["my-gateway"]["api_key"] == "${FAKE_GW_TOKEN}"


def test_inherit_launch_model_carries_a_custom_providers_endpoint(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    (home / "config.yaml").write_text(
        "model:\n  provider: custom:lab\n  default: lab-model\n"
        "custom_providers:\n  - name: lab\n    base_url: https://lab.internal.example.com/v1\n")
    profile = home / "profiles" / "scout"
    profile.mkdir(parents=True)

    assert srv._inherit_launch_model(profile) is True

    cfg = yaml.safe_load((profile / "config.yaml").read_text())
    assert (cfg["model"]["provider"], cfg["model"]["default"]) == ("custom:lab", "lab-model")
    assert cfg["custom_providers"] == [
        {"name": "lab", "base_url": "https://lab.internal.example.com/v1"}
    ]


@pytest.mark.parametrize("written", ["http://127.0.0.1:11434/v1", "${LAB_URL}"])
@pytest.mark.xfail(strict=True, reason="wave-20 parity defect: profile inheritance drops a bare custom endpoint base_url and the pin rejects the provider")
def test_inherit_launch_model_pins_a_bare_custom_endpoint(monkeypatch, tmp_path, written):
    home = tmp_path / ".qubit"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    for var in ("CUSTOM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LAB_URL", "http://127.0.0.1:11434/v1")
    # Hermetic: the pin probes the endpoint's /models.
    monkeypatch.setattr("qubit_cli.models.probe_api_models", lambda *a, **k: {"models": ["llama3"]})
    (home / "config.yaml").write_text(
        "model:\n  provider: custom\n  default: llama3\n  base_url: '" + written + "'\n")
    profile = home / "profiles" / "scout"
    profile.mkdir(parents=True)
    # Voice mirroring writes config.yaml before inheritance runs (#85755).
    (profile / "config.yaml").write_text("tts:\n  provider: edge\n")

    assert srv._inherit_launch_model(profile) is True

    model = yaml.safe_load((profile / "config.yaml").read_text())["model"]
    # The launch file's spelling travels: a ${VAR} URL stays a ref, never the launch value.
    assert (model["provider"], model["default"], model["base_url"]) == ("custom", "llama3", written)


def test_inherit_launch_model_preserves_explicit_profile_selection(monkeypatch, tmp_path):
    home = tmp_path / ".qubit"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    (home / "config.yaml").write_text("model:\n  provider: nous\n  default: launch-model\n")
    profile = home / "profiles" / "scout"
    profile.mkdir(parents=True)
    selected = "model:\n  provider: openrouter\n  default: profile-model\n"
    (profile / "config.yaml").write_text(selected)

    assert srv._inherit_launch_model(profile) is False
    assert (profile / "config.yaml").read_text() == selected
