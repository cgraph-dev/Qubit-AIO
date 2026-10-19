"""Vertex route resolution under `deferred_token_mint`: route without exchanging a JWT.

The gateway-backed classic CLI never sends a Vertex request itself (its gateway session mints the
token for each turn), so resolving the route must not run an OAuth exchange per launch — and the
readiness probe at startup must not either. `deferred_token_mint()` swaps the resolved `api_key`
for the mint-on-first-use callable; `get_vertex_base_url` supplies the endpoint without a token.
"""

import pytest

from agent import vertex_adapter
from qubit_cli import runtime_provider
from qubit_cli.runtime_provider import deferred_token_mint


@pytest.fixture
def vertex_creds(monkeypatch):
    monkeypatch.setattr(vertex_adapter, "get_vertex_config",
                        lambda *a, **k: ("MINTED-NOW", "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1"))
    monkeypatch.setattr(vertex_adapter, "get_vertex_base_url",
                        lambda *a, **k: "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1")
    monkeypatch.setattr(vertex_adapter, "vertex_access_token", lambda: "stub-token-callable")


def test_deferred_mint_resolves_a_callable_key_and_the_endpoint(vertex_creds):
    with deferred_token_mint():
        runtime = runtime_provider._resolve_vertex_runtime("vertex")
    assert callable(runtime["api_key"]), "a routing-only process must not hold an exchanged JWT"
    assert runtime["base_url"].startswith("https://us-central1-aiplatform")
    assert runtime["source"] == "vertex-oauth"


def test_eager_resolution_is_unchanged(vertex_creds):
    runtime = runtime_provider._resolve_vertex_runtime("vertex")
    assert runtime["api_key"] == "MINTED-NOW"


def test_deferred_mint_is_scoped_not_global(vertex_creds):
    with deferred_token_mint():
        assert runtime_provider._DEFER_TOKEN_MINT.get() is True
    assert runtime_provider._DEFER_TOKEN_MINT.get() is False
    assert not callable(runtime_provider._resolve_vertex_runtime("vertex")["api_key"])


def test_missing_endpoint_still_fails_loudly(monkeypatch):
    monkeypatch.setattr(vertex_adapter, "get_vertex_config", lambda *a, **k: (None, None))
    monkeypatch.setattr(vertex_adapter, "get_vertex_base_url", lambda *a, **k: None)
    monkeypatch.setattr(vertex_adapter, "vertex_access_token", lambda: "stub-token-callable")
    with deferred_token_mint():
        with pytest.raises(Exception) as excinfo:
            runtime_provider._resolve_vertex_runtime("vertex")
    assert "Vertex AI credentials could not be resolved" in str(excinfo.value)
