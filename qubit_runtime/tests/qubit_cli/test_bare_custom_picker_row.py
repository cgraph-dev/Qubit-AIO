"""The bare ``model.provider: custom`` picker row must survive switching away.

The row is built from the session's current-provider slice when the session is
on it; otherwise it is recovered from the static config.yaml ``model:`` block
(#59702). Before that fallback existed the row vanished whenever a different
provider was active, and the unconfigured-canonical fallback substituted a
misleading 0-model placeholder in its place.
"""

import pytest

import qubit_cli.providers as providers_mod
from qubit_cli.model_switch import list_authenticated_providers


# PRODUCT GAP (Qubit fork): ``qubit_cli/model_switch_providers.py``
# ``_lap_bare_custom_row`` dropped the static-config fallback branch that
# upstream ``hermes_cli/model_switch_providers.py`` (pin cbe5e53e, lines
# 1058-1073) has: when the session's current provider is NOT ``custom`` it
# recovers ``base_url``/``default`` from ``load_config().get("model")`` so the
# row survives switching away (#59702). The Qubit copy only serves the row
# while ``current_provider_norm == "custom"``; the config block is never read.
# Do NOT fix product here — recorded for the sync owner.
@pytest.mark.xfail(
    strict=False,
    reason="PRODUCT GAP (#59702): qubit_cli/model_switch_providers._lap_bare_custom_row "
    "omits upstream's static config.yaml `model:` fallback; the bare custom row "
    "vanishes when a different provider is current.",
)
def test_bare_custom_row_survives_switching_away_from_it(monkeypatch):
    """Regression for #59702: the bare ``model.provider: custom`` row must not
    vanish from the picker when the session's current provider is a different
    one."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})
    monkeypatch.setattr(
        "qubit_cli.config.load_config",
        lambda: {
            "model": {"default": "gpt-4o", "provider": "custom", "base_url": "https://www.ccsub.net/v1"}
        },
    )
    # The config-sourced branch serves the warm cache / falls back to the
    # config default; keep the test hermetic — no live probe.
    monkeypatch.setattr("qubit_cli.models.fetch_api_models", lambda *_a, **_kw: [])
    monkeypatch.setattr("qubit_cli.models.cached_fetch_api_models", lambda *_a, **_kw: None)

    providers = list_authenticated_providers(
        current_provider="xai",
        current_base_url="",
        current_model="grok-4",
        user_providers={},
        custom_providers=[],
        probe_custom_providers=False,
    )

    bare_custom = next((p for p in providers if p["slug"] == "custom"), None)
    assert bare_custom is not None, "bare custom endpoint row vanished after switching provider"
    assert bare_custom["name"] == "Custom endpoint"
    assert bare_custom["is_current"] is False
    assert bare_custom["models"] == ["gpt-4o"], "config default model must seed the row when no probe runs"
    assert bare_custom["api_url"] == "https://www.ccsub.net/v1"
    assert bare_custom["source"] == "model-config"


def test_bare_custom_row_config_fallback_defers_to_matching_named_entry(monkeypatch):
    """The config-sourced bare row must not duplicate a named custom_providers
    entry that already covers the same base_url (#59702)."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})
    monkeypatch.setattr(
        "qubit_cli.config.load_config",
        lambda: {
            "model": {"default": "gpt-4o", "provider": "custom", "base_url": "https://www.ccsub.net/v1"}
        },
    )

    providers = list_authenticated_providers(
        current_provider="xai",
        current_base_url="",
        current_model="grok-4",
        user_providers={},
        custom_providers=[{"name": "my-endpoint", "base_url": "https://www.ccsub.net/v1", "model": "gpt-4o"}],
        probe_custom_providers=False,
    )

    assert not any(p["slug"] == "custom" for p in providers)
    assert any(p["slug"] == "custom:my-endpoint" for p in providers)


def test_bare_custom_row_config_fallback_tolerates_legacy_scalar_model(monkeypatch):
    """A legacy bare-string ``model:`` config value must not crash the picker
    (the ConfigContext loader already tolerates it)."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})
    monkeypatch.setattr("qubit_cli.config.load_config", lambda: {"model": "gpt-4o"})

    providers = list_authenticated_providers(
        current_provider="xai",
        current_base_url="",
        current_model="grok-4",
        user_providers={},
        custom_providers=[],
        probe_custom_providers=False,
    )

    assert not any(p["slug"] == "custom" for p in providers)
