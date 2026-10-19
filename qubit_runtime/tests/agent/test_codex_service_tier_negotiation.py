"""Codex service-tier entitlement: the catalog probe feeds the cache the negotiator reads.

The Codex backend accepts ``service_tier: "ultrafast"`` from a plan without the entitlement and
silently serves the default tier, so the account's own catalog is the only authority. Two links
make that work: the ``/models`` probe must retain speed metadata (``remember_catalog``), and every
request-override build must clamp the tier to what the credential advertises
(``negotiate_request_tier``). Either link missing means a user asks for Ultrafast and gets
Standard with no error.
"""

from types import SimpleNamespace

import pytest

from agent import codex_service_tiers, fast_mode, model_metadata


@pytest.fixture(autouse=True)
def _fresh_catalog_caches():
    codex_service_tiers._catalogs.clear()
    model_metadata._codex_oauth_context_cache.clear()
    model_metadata._codex_oauth_max_context_cache.clear()
    yield
    codex_service_tiers._catalogs.clear()
    model_metadata._codex_oauth_context_cache.clear()
    model_metadata._codex_oauth_max_context_cache.clear()


def _codex_agent(**kw):
    base = dict(
        service_tier="auto",
        model="gpt-5.4",
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        api_key="opaque-test-token",
        request_overrides={"service_tier": "ultrafast"},
        fast_auto_seconds=60,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_catalog_probe_retains_speed_metadata(monkeypatch):
    """The probe is what fills ``_catalogs``; without it every tier lookup is unknown."""
    entries = [
        {"slug": "gpt-5.4", "context_window": 400000, "additional_speed_tiers": ["fast"]},
        {"slug": "gpt-5.4-mini", "context_window": 200000, "service_tiers": [{"id": "priority"}]},
        # no speed metadata at all -> absent from the tier table, not "no tiers"
        {"slug": "gpt-5.4-nano", "context_window": 128000},
    ]
    monkeypatch.setattr(model_metadata, "fetch_codex_catalog_entries", lambda get, base_url="": (entries, 200))

    windows, fresh = model_metadata._fetch_codex_oauth_context_lengths_with_source(
        "opaque-test-token", "https://proxy.example.com/backend-api/codex"
    )

    assert fresh is True and windows["gpt-5.4"] == 400000
    tiers = codex_service_tiers._tiers_for_model(
        "opaque-test-token", "https://proxy.example.com/backend-api/codex", "gpt-5.4"
    )
    assert tiers == frozenset({"priority"})
    assert codex_service_tiers._tiers_for_model(
        "opaque-test-token", "https://proxy.example.com/backend-api/codex", "gpt-5.4-nano"
    ) is None


def test_request_tier_clamped_to_advertised_entitlement(monkeypatch):
    monkeypatch.setattr(codex_service_tiers, "_tiers_for_model", lambda token, base, model: frozenset({"priority"}))

    downgraded = fast_mode.effective_request_overrides(_codex_agent())
    assert downgraded["service_tier"] == "priority"

    # Nothing advertised -> Standard, which Codex represents by omitting the field.
    monkeypatch.setattr(codex_service_tiers, "_tiers_for_model", lambda token, base, model: frozenset())
    assert "service_tier" not in fast_mode.effective_request_overrides(_codex_agent())

    # Ultrafast is kept when the account really advertises it.
    monkeypatch.setattr(
        codex_service_tiers, "_tiers_for_model", lambda token, base, model: frozenset({"ultrafast", "priority"})
    )
    assert fast_mode.effective_request_overrides(_codex_agent())["service_tier"] == "ultrafast"

    # Unknown catalog never clamps: a failed lookup must not refuse a paid request.
    monkeypatch.setattr(codex_service_tiers, "_tiers_for_model", lambda token, base, model: None)
    assert fast_mode.effective_request_overrides(_codex_agent())["service_tier"] == "ultrafast"


def test_tier_negotiation_leaves_other_routes_alone(monkeypatch):
    """Only the official Codex responses route is clamped; proxies keep their requested tier."""
    monkeypatch.setattr(codex_service_tiers, "_tiers_for_model", lambda token, base, model: frozenset())

    proxy = _codex_agent(base_url="https://proxy.example.com/backend-api/codex")
    assert fast_mode.effective_request_overrides(proxy)["service_tier"] == "ultrafast"

    chat = _codex_agent(provider="openai", base_url="https://api.openai.com/v1", api_mode="chat_completions")
    assert fast_mode.effective_request_overrides(chat)["service_tier"] == "ultrafast"


def test_ultrafast_offer_never_contradicts_the_catalog(monkeypatch):
    """``ultrafast_offered`` reads the same cache: an unadvertised tier is not offered."""
    monkeypatch.setattr(
        codex_service_tiers, "codex_account_offers_tier", lambda model, tier, **kw: tier not in {"ultrafast"}
    )
    from qubit_cli.models import resolve_fast_mode_overrides

    model, provider, base_url = "gpt-6-astra", "openai-codex", "https://chatgpt.com/backend-api/codex"
    assert resolve_fast_mode_overrides(model, provider=provider, base_url=base_url, tier="ultrafast") == {
        "service_tier": "ultrafast"
    }
    assert codex_service_tiers.ultrafast_offered(model, provider=provider, base_url=base_url) is False
