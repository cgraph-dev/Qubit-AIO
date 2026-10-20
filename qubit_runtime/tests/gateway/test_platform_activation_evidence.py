"""The plugin-platform enable pass materializes only platforms with activation evidence."""

from unittest.mock import MagicMock

from gateway.platform_activation import configured_plugin_entries
from gateway.platform_registry import PlatformEntry, PlatformRegistry


def _registry():
    reg = PlatformRegistry()
    entry = PlatformEntry(
        name="sms", label="SMS", adapter_factory=lambda cfg: object(),
        check_fn=lambda: True, source="plugin", required_env=["TWILIO_SID"])
    loader = MagicMock()

    def _load():
        loader()
        reg.register(entry)

    reg.register_deferred("sms", _load)
    reg.note_activation_env("sms", ["TWILIO_SID"])
    return reg, loader


def test_manifest_env_is_evidence_for_an_env_only_platform(tmp_path, monkeypatch):
    monkeypatch.setenv("TWILIO_SID", "AC-test")
    registry, loader = _registry()
    assert [entry.name for entry in configured_plugin_entries(
        registry, yaml_cfg={}, platform_names=(), home=tmp_path)] == ["sms"]
    loader.assert_called_once()


def test_no_evidence_leaves_the_deferred_adapter_unimported(tmp_path, monkeypatch):
    monkeypatch.delenv("TWILIO_SID", raising=False)
    registry, loader = _registry()
    assert configured_plugin_entries(registry, yaml_cfg={}, platform_names=(), home=tmp_path) == []
    loader.assert_not_called()
