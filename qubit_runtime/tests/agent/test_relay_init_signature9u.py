"""Regression: Relay plugin init/validate must match the signature the plugin exposes.

A partial vendor sync changed the caller to pass ``additional_plugins_toml=`` to Relay's
``initialize``/``validate``. The installed Relay binding does not accept that keyword, so
every startup failed with ``TypeError: initialize() got an unexpected keyword argument``.

The caller must hand the selected plugins.toml through the mechanism the plugin actually
accepts (a parsed config passed positionally). That shape works for both a plugin whose
signature is ``initialize(config)`` and a newer plugin whose signature is
``initialize(config, *, additional_plugins_toml=None)``. These tests fail with the
keyword form and pass only with the accepted call shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent import relay_runtime

_MISSING = object()


class _PluginWithoutKeyword:
    """Relay plugin namespace that accepts only the positional config argument."""

    def __init__(self) -> None:
        self.initialize_calls: list[tuple[Any, ...]] = []
        self.validate_calls: list[tuple[Any, ...]] = []

    def report(self) -> None:
        return None

    async def initialize(self, config: dict[str, Any]) -> dict[str, Any]:
        self.initialize_calls.append((config,))
        return {"diagnostics": []}

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        self.validate_calls.append((config,))
        return {"diagnostics": []}


class _PluginWithKeyword:
    """Relay plugin namespace that also accepts the newer keyword argument."""

    def __init__(self) -> None:
        self.initialize_calls: list[tuple[Any, ...]] = []
        self.validate_calls: list[tuple[Any, ...]] = []

    def report(self) -> None:
        return None

    async def initialize(
        self,
        config: dict[str, Any],
        *,
        additional_plugins_toml: Any = _MISSING,
    ) -> dict[str, Any]:
        self.initialize_calls.append((config, additional_plugins_toml))
        return {"diagnostics": []}

    def validate(
        self,
        config: dict[str, Any],
        *,
        additional_plugins_toml: Any = _MISSING,
    ) -> dict[str, Any]:
        self.validate_calls.append((config, additional_plugins_toml))
        return {"diagnostics": []}


@pytest.fixture(autouse=True)
def _reset_runtime():
    relay_runtime._reset_for_tests()
    yield
    relay_runtime._reset_for_tests()


def _select_empty_plugins_toml(tmp_path, monkeypatch):
    config = tmp_path / "plugins.toml"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv(relay_runtime.RELAY_PLUGINS_CONFIG_ENV, str(config))
    return config


@pytest.mark.parametrize("plugin_factory", [_PluginWithoutKeyword, _PluginWithKeyword])
def test_initialize_accepts_either_plugin_signature(tmp_path, monkeypatch, plugin_factory):
    _select_empty_plugins_toml(tmp_path, monkeypatch)
    plugin = plugin_factory()
    owner = relay_runtime._ProcessRelayPluginConfiguration()

    assert owner._initialize(SimpleNamespace(plugin=plugin)) is True
    expected = [({}, _MISSING)] if plugin_factory is _PluginWithKeyword else [({},)]
    assert plugin.initialize_calls == expected


@pytest.mark.parametrize("plugin_factory", [_PluginWithoutKeyword, _PluginWithKeyword])
def test_resolve_plugin_sources_accepts_either_plugin_signature(
    tmp_path,
    monkeypatch,
    plugin_factory,
):
    _select_empty_plugins_toml(tmp_path, monkeypatch)
    plugin = plugin_factory()

    sources = relay_runtime.resolve_plugin_sources(SimpleNamespace(plugin=plugin))

    assert isinstance(sources, relay_runtime.RelayPluginSources)
    expected = [({}, _MISSING)] if plugin_factory is _PluginWithKeyword else [({},)]
    assert plugin.validate_calls == expected


def test_keyword_is_never_passed_to_the_plugin(tmp_path, monkeypatch):
    """The caller must not pass ``additional_plugins_toml``; the plugin's sentinel proves it."""
    _select_empty_plugins_toml(tmp_path, monkeypatch)
    plugin = _PluginWithKeyword()
    relay = SimpleNamespace(plugin=plugin)

    owner = relay_runtime._ProcessRelayPluginConfiguration()
    assert owner._initialize(relay) is True
    relay_runtime.resolve_plugin_sources(relay)

    assert plugin.initialize_calls == [({}, _MISSING)]
    assert plugin.validate_calls == [({}, _MISSING)]
