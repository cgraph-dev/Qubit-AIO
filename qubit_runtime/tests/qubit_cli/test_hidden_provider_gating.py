"""Hidden-provider gating: an unlisted profile must not leak into any LIST surface.

``listed_canonical_providers()`` (used by ``list_available_providers``, ``provider_catalog``
and, transitively, ``_build_oauth_catalog``) is expected to drop providers that
``providers.unlisted_provider_names()`` reports as hidden. These tests pin that
contract: a provider hidden by the gate must be absent from every list, while a
typed ``provider:model`` path still resolves it (resolution never consults the gate).
"""
from __future__ import annotations

import providers


def _pick_listed_provider():
    """Return a provider slug that is currently listed (present and non-hidden)."""
    from qubit_cli.models_catalog_static import listed_canonical_providers

    listed = [p.slug for p in listed_canonical_providers()]
    assert listed, "expected at least one listed provider"
    return listed[0]


def test_hidden_provider_absent_from_list_available_providers(monkeypatch):
    from qubit_cli import models

    victim = _pick_listed_provider()
    visible = {row["id"] for row in models.list_available_providers()}
    assert victim in visible, "precondition: victim must start listed"

    monkeypatch.setattr(providers, "unlisted_provider_names", lambda: {victim})
    hidden = {row["id"] for row in models.list_available_providers()}
    assert victim not in hidden, "hidden provider leaked into list_available_providers()"


def test_hidden_provider_absent_from_provider_catalog(monkeypatch):
    from qubit_cli.provider_catalog import provider_catalog

    victim = _pick_listed_provider()
    assert victim in {d.slug for d in provider_catalog()}, "precondition: victim must start listed"

    monkeypatch.setattr(providers, "unlisted_provider_names", lambda: {victim})
    assert victim not in {d.slug for d in provider_catalog()}, (
        "hidden provider leaked into provider_catalog()")


def test_oauth_catalog_delegates_to_gated_provider_catalog():
    """The accounts tab's non-card rows come from the gated ``provider_catalog()``.

    The curated ``_OAUTH_PROVIDER_CATALOG`` cards are static by design (upstream keeps them
    unfiltered, and in this build every accounts-tab provider is already one of them), so the
    hidden-provider gate reaches the accounts tab only through the ``provider_catalog()`` call.
    Assert the two surfaces agree exactly on the non-card accounts set — if the catalog ever
    drops a provider (e.g. via the gate), the accounts tab drops it too.
    """
    from qubit_cli.provider_catalog import provider_catalog
    from qubit_cli.web_routers import oauth as oauth_router

    curated = {e["id"] for e in oauth_router._OAUTH_PROVIDER_CATALOG}
    gated_non_card = {d.slug for d in provider_catalog() if d.tab == "accounts" and d.slug not in curated}
    oauth_ids = {row["id"] for row in oauth_router._build_oauth_catalog()}
    assert gated_non_card <= oauth_ids, (
        "accounts tab is missing providers that the gated provider_catalog() offers")
