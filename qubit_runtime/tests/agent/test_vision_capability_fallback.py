"""Vision capability is resolved honestly and the auto-route never sends an image
to an endpoint whose image support could not be verified.

Regression: ``_main_model_supports_vision`` read an *unknown* capability as True
("attempt the call"), so the vision auto-route handed user images to a text-only
main model and the provider answered HTTP 400 — the tool surfaced an opaque API
error instead of using an aggregator or reporting that vision is not configured.

The fix keeps the permissive ``_main_model_supports_vision`` predicate for callers
that can still recover, but the auto-route now requires a *verified* capability
(``_main_model_vision_capability(...) is True``) or a provider vision default. When
neither a main model nor an aggregator resolves, ``_resolve_call_client`` raises an
explicit "vision is not configured" error rather than attempting the doomed call.
"""

from __future__ import annotations

import pytest

from agent import auxiliary_client as ac
from agent import image_routing as ir


def _patch_capability(monkeypatch, verdict):
    """Force the tri-state capability lookup to ``verdict`` (True/False/None)."""
    monkeypatch.setattr(ir, "_lookup_supports_vision", lambda *a, **k: verdict)


def _isolate_aggregators(monkeypatch):
    """No strict vision aggregator resolves; the route is decided by the main model."""
    monkeypatch.setattr(ac, "_resolve_strict_vision_backend", lambda *a, **k: (None, None))


class TestCapabilityResolution:
    def test_unknown_capability_is_none(self, monkeypatch):
        _patch_capability(monkeypatch, None)
        assert ac._main_model_vision_capability("p", "m") is None

    def test_unknown_capability_stays_permissive_for_attempt_predicate(self, monkeypatch):
        # The 2-arg predicate keeps its documented permissive contract for callers
        # that are allowed to attempt (and recover) — only the route is strict.
        _patch_capability(monkeypatch, None)
        assert ac._main_model_supports_vision("p", "m") is True

    def test_known_non_vision_model_is_false(self, monkeypatch):
        _patch_capability(monkeypatch, False)
        assert ac._main_model_vision_capability("p", "m") is False
        assert ac._main_model_supports_vision("p", "m") is False

    def test_known_vision_model_is_true(self, monkeypatch):
        _patch_capability(monkeypatch, True)
        assert ac._main_model_vision_capability("p", "m") is True
        assert ac._main_model_supports_vision("p", "m") is True

    def test_probe_failure_reads_as_unknown_and_never_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("catalog unreachable")

        monkeypatch.setattr(ir, "_lookup_supports_vision", _boom)
        assert ac._main_model_vision_capability("p", "m") is None


class TestAutoRouteChoice:
    def _route(self, monkeypatch, capability, *, vision_default=None, provider="fake"):
        monkeypatch.setattr(ac, "_resolve_provider_vision_default", lambda p: vision_default)
        _patch_capability(monkeypatch, capability)
        _isolate_aggregators(monkeypatch)
        sentinel = object()
        calls: list[tuple[str, str]] = []

        def _resolve(provider_name, model, **kw):
            calls.append((provider_name, model))
            return sentinel, model

        monkeypatch.setattr(ac, "resolve_provider_client", _resolve)
        route = ac._vision_auto_route({"provider": provider, "model": "fake-model"}, None, None, False)
        return route, sentinel, calls

    def test_unverified_main_model_is_not_attempted(self, monkeypatch):
        (provider, client, model), _sentinel, calls = self._route(monkeypatch, None)
        assert (provider, client, model) == (None, None, None)
        assert calls == [], "an unverified endpoint must never receive an image request"

    def test_known_non_vision_main_model_is_not_attempted(self, monkeypatch):
        (provider, client, model), _sentinel, calls = self._route(monkeypatch, False)
        assert (provider, client, model) == (None, None, None)
        assert calls == []

    def test_verified_vision_main_model_is_used(self, monkeypatch):
        (provider, client, model), sentinel, calls = self._route(monkeypatch, True)
        assert client is sentinel
        assert provider == "fake"
        assert calls == [("fake", "fake-model")]

    def test_provider_vision_default_wins_over_unverified_main(self, monkeypatch):
        route, sentinel, calls = self._route(
            monkeypatch, None, vision_default="fake-vision-model")
        provider, client, model = route
        assert client is sentinel
        assert model == "fake-vision-model"
        assert calls == [("fake", "fake-vision-model")]

    def test_custom_provider_keeps_permissive_attempt(self, monkeypatch):
        # User-supplied custom endpoints have no catalog to verify against, so the
        # historical permissive attempt is preserved (see TestResolveVisionCustomProvider).
        route, sentinel, calls = self._route(monkeypatch, None, provider="custom")
        _provider, client, _model = route
        assert client is sentinel
        assert calls == [("custom", "fake-model")]

    def test_oauth_provider_keeps_permissive_attempt(self, monkeypatch):
        # Copilot's auth is OAuth and its catalog model set is opaque: attempt, don't block.
        route, sentinel, calls = self._route(monkeypatch, None, provider="copilot")
        _provider, client, _model = route
        assert client is sentinel
        assert calls == [("copilot", "fake-model")]

    def test_route_never_raises_when_probe_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("catalog unreachable")

        monkeypatch.setattr(ir, "_lookup_supports_vision", _boom)
        monkeypatch.setattr(ac, "_resolve_provider_vision_default", lambda provider: None)
        _isolate_aggregators(monkeypatch)
        # Must degrade, not raise.
        assert ac._vision_auto_route(
            {"provider": "fake", "model": "fake-model"}, None, None, False) == (None, None, None)


class TestHonestDegradation:
    def test_no_vision_backend_reports_clear_error(self, monkeypatch):
        monkeypatch.setattr(ac, "resolve_vision_provider_client", lambda **kw: (None, None, None))
        with pytest.raises(ac.AuxiliaryClientUnavailable) as excinfo:
            ac._resolve_call_client(
                "vision", provider=None, model=None, base_url=None, api_key=None,
                resolved_provider="auto", resolved_model=None, resolved_base_url=None,
                resolved_api_key=None, resolved_api_mode=None, main_runtime={}, async_mode=False)
        message = str(excinfo.value)
        assert "vision is not configured" in message
        # The text-provider wording must not be used for the vision route.
        assert "qubit setup" not in message

    def test_text_task_keeps_generic_message(self, monkeypatch):
        monkeypatch.setattr(ac, "_get_cached_client", lambda *a, **k: (None, None))
        with pytest.raises(ac.AuxiliaryClientUnavailable) as excinfo:
            ac._resolve_call_client(
                "title_generation", provider=None, model=None, base_url=None, api_key=None,
                resolved_provider="auto", resolved_model=None, resolved_base_url=None,
                resolved_api_key=None, resolved_api_mode=None, main_runtime={}, async_mode=False)
        assert "No LLM provider configured" in str(excinfo.value)
