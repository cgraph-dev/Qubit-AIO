"""The configured ``auxiliary.vision`` provider must win over the main chat model.

Regression: with ``auxiliary.vision.provider: ollama`` + ``model: qubit-brain-vision``
configured, the ``vision_analyze`` tool still sent the image to the main provider
(opencode-go) with the vision model id, and the endpoint answered HTTP 400
"Model is unavailable". Two compounding defects:

1. A local-server alias (``ollama``) has no registry entry, so with no explicit
   ``auxiliary.vision.base_url`` the generic custom branch borrowed the *main*
   runtime endpoint, silently routing the vision model to the main provider.
2. When a pinned vision route could not resolve, ``_resolve_call_client`` fell
   back to ``provider="auto"`` — which re-enters the main provider — instead of
   dedicated vision aggregators or an honest typed failure.

These tests pin the precedence rule and the honest fallback; they are hermetic
(no network, no real SDK clients).
"""

from __future__ import annotations

import pytest

from agent import auxiliary_client as ac


# ── Precedence: a configured local-server vision lane resolves to its own endpoint ──


class TestLocalServerVisionLane:
    def test_ollama_lane_uses_local_endpoint_not_main(self, monkeypatch):
        """`provider: ollama` + no base_url must resolve to the local server, never the main."""
        monkeypatch.setattr(
            ac, "_resolve_task_provider_model",
            lambda *a, **k: ("ollama", "qubit-brain-vision", None, None, None),
        )
        monkeypatch.setattr(ac, "_local_server_vision_base_url", lambda provider: "http://127.0.0.1:11434")
        sentinel = object()
        captured: list[tuple] = []

        def _cached(provider, model=None, async_mode=False, **kw):
            captured.append((provider, model, kw.get("base_url"), kw.get("main_runtime")))
            return sentinel, "qubit-brain-vision"

        monkeypatch.setattr(ac, "_get_cached_client", _cached)

        provider, client, model = ac.resolve_vision_provider_client()

        assert client is sentinel
        assert model == "qubit-brain-vision"
        assert captured == [("ollama", "qubit-brain-vision", "http://127.0.0.1:11434", {})], (
            "the ollama lane must use the local endpoint with no main-runtime borrow"
        )

    def test_ollama_helper_defaults_to_loopback(self, monkeypatch):
        """The helper never returns the main endpoint and always yields a local URL."""
        monkeypatch.setattr(
            "qubit_cli.models_local._get_ollama_base_url", lambda: "http://127.0.0.1:11434"
        )
        assert ac._local_server_vision_base_url("ollama") == "http://127.0.0.1:11434"

    def test_ollama_helper_falls_back_when_resolver_unavailable(self, monkeypatch):
        def _boom():
            raise RuntimeError("resolver unavailable")

        monkeypatch.setattr("qubit_cli.models_local._get_ollama_base_url", _boom)
        assert ac._local_server_vision_base_url("ollama") == "http://localhost:11434"

    def test_non_local_provider_has_no_helper_default(self):
        assert ac._local_server_vision_base_url("openrouter") is None
        assert ac._local_server_vision_base_url("opencode-go") is None


# ── Honest fallback: a pinned route never falls back to the main provider ──────────


class TestPinnedVisionFallback:
    def _call(self, monkeypatch, *, pinned, route_result, aggregator_result=(None, None, None)):
        calls: dict[str, object] = {}
        seen: list[dict] = []

        monkeypatch.setattr(ac, "_vision_task_route_is_pinned", lambda task="vision": pinned)

        def _resolve(**kw):
            seen.append(kw)
            return route_result

        monkeypatch.setattr(ac, "resolve_vision_provider_client", _resolve)

        def _agg(async_mode):
            calls["aggregator"] = True
            return aggregator_result

        monkeypatch.setattr(ac, "_vision_aggregator_only_route", _agg)
        with pytest.raises(ac.AuxiliaryClientUnavailable) as excinfo:
            ac._resolve_call_client(
                "vision", provider=None, model="qubit-brain-vision", base_url=None, api_key=None,
                resolved_provider="ollama", resolved_model="qubit-brain-vision",
                resolved_base_url=None, resolved_api_key=None, resolved_api_mode=None,
                main_runtime={}, async_mode=False,
            )
        return calls, seen, str(excinfo.value)

    def test_pinned_route_uses_aggregators_not_auto(self, monkeypatch):
        calls, seen, message = self._call(monkeypatch, pinned=True, route_result=(None, None, None))
        assert calls.get("aggregator") is True
        # The first resolve is the configured route; the fallback must NOT re-enter "auto".
        assert all(kw.get("provider") != "auto" for kw in seen), (
            "a pinned vision route must not fall back to the main/auto chain"
        )
        assert "vision is not configured" in message

    def test_unpinned_route_keeps_auto_fallback(self, monkeypatch):
        calls, seen, _ = self._call(monkeypatch, pinned=False, route_result=(None, None, None))
        assert "aggregator" not in calls
        assert any(kw.get("provider") == "auto" for kw in seen)


# ── Honest fallback: catalog-only main is deprioritized behind a vision aggregator ──


class TestCatalogOnlyMainDeprioritized:
    def _route(self, monkeypatch, *, declared, aggregator_client):
        monkeypatch.setattr(ac, "_read_main_provider", lambda: "opencode-go")
        monkeypatch.setattr(ac, "_read_main_model", lambda: "deepseek-v4.1-flash")
        monkeypatch.setattr(ac, "_main_model_vision_capability", lambda p, m: True)
        monkeypatch.setattr(ac, "_main_model_vision_is_config_declared", lambda p, m: declared)
        monkeypatch.setattr(ac, "_provider_vision_capability_is_opaque", lambda p: False)
        monkeypatch.setattr(ac, "_resolve_provider_vision_default", lambda p: None)
        main_calls: list = []

        def _main_client(*a, **k):
            main_calls.append(a)
            return object(), "deepseek-v4.1-flash"

        monkeypatch.setattr(ac, "_vision_main_provider_client", _main_client)
        monkeypatch.setattr(
            ac, "_resolve_strict_vision_backend",
            lambda candidate, model=None: (aggregator_client, "openrouter-vision")
            if candidate == "openrouter" else (None, None),
        )
        route = ac._vision_auto_route(
            {"provider": "opencode-go", "model": "deepseek-v4.1-flash"}, None, None, False)
        return route, main_calls

    def test_catalog_only_main_prefers_aggregator(self, monkeypatch):
        aggregator = object()
        route, main_calls = self._route(monkeypatch, declared=False, aggregator_client=aggregator)
        provider, client, model = route
        assert provider == "openrouter"
        assert client is aggregator
        assert main_calls == [], "a catalog-only main model must not be tried before an aggregator"

    def test_config_declared_main_keeps_main_first(self, monkeypatch):
        aggregator = object()
        route, main_calls = self._route(monkeypatch, declared=True, aggregator_client=aggregator)
        provider, _client, _model = route
        assert provider == "opencode-go"
        assert main_calls, "a config-declared vision model keeps the main-first order"

    def test_catalog_only_main_used_when_no_aggregator(self, monkeypatch):
        route, main_calls = self._route(monkeypatch, declared=False, aggregator_client=None)
        provider, _client, _model = route
        assert provider == "opencode-go"
        assert main_calls, "the catalog-only main is the last resort when no aggregator resolves"


# ── Never raises ───────────────────────────────────────────────────────────────────


class TestNeverRaises:
    def test_auto_route_degrades_when_probe_raises(self, monkeypatch):
        from agent import image_routing as ir

        monkeypatch.setattr(ac, "_read_main_provider", lambda: "opencode-go")
        monkeypatch.setattr(ac, "_read_main_model", lambda: "deepseek-v4.1-flash")

        def _boom(*a, **k):
            raise RuntimeError("catalog unreachable")

        monkeypatch.setattr(ir, "_lookup_supports_vision", _boom)
        monkeypatch.setattr(ac, "_resolve_provider_vision_default", lambda p: None)
        monkeypatch.setattr(ac, "_resolve_strict_vision_backend", lambda *a, **k: (None, None))
        assert ac._vision_auto_route(
            {"provider": "opencode-go", "model": "deepseek-v4.1-flash"}, None, None, False
        ) == (None, None, None)

    def test_pinned_unavailable_raises_typed_not_unhandled(self, monkeypatch):
        monkeypatch.setattr(ac, "_vision_task_route_is_pinned", lambda task="vision": True)
        monkeypatch.setattr(ac, "resolve_vision_provider_client", lambda **kw: (None, None, None))
        monkeypatch.setattr(ac, "_vision_aggregator_only_route", lambda async_mode: (None, None, None))
        with pytest.raises(ac.AuxiliaryClientUnavailable):
            ac._resolve_call_client(
                "vision", provider=None, model=None, base_url=None, api_key=None,
                resolved_provider="ollama", resolved_model=None, resolved_base_url=None,
                resolved_api_key=None, resolved_api_mode=None, main_runtime={}, async_mode=False,
            )
