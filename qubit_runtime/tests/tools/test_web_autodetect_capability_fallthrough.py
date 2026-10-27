"""A fresh install must not pin web_extract to a search-only autodetect result.

The shared web backend is autodetected across both capabilities. When the only
installed backend is ddgs/brave-free/searxng (search-only), ``_get_backend``
still returns that name; ``_get_extract_backend`` used to return it unchanged,
so ``web_extract_tool`` refused with "search-only" even though the registry can
resolve a capable extract provider (keyed or anonymous keyless).

An explicitly configured search-only backend keeps the honest error: only an
*autodetected* result falls through to capability-aware resolution.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent.web_search_provider import WebSearchProvider


class _SearchOnly(WebSearchProvider):
    name = "fake-search"
    display_name = "Fake Search"

    def is_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query, limit=5):
        return {"success": True, "data": {"web": []}}


class _ExtractOnly(WebSearchProvider):
    name = "fake-extract"
    display_name = "Fake Extract"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def is_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    async def extract(self, urls, **kwargs):
        self.calls.append(list(urls))
        return [{"url": url, "title": "T", "content": f"text for {url}"} for url in urls]


@pytest.fixture
def providers():
    from agent.web_search_registry import _reset_for_tests, register_provider

    extract = _ExtractOnly()
    _reset_for_tests()
    register_provider(_SearchOnly())
    register_provider(extract)
    yield extract
    _reset_for_tests()


def _patch_resolution(monkeypatch, *, shared, configured):
    from tools import web_tools, web_tools_extract

    monkeypatch.setattr(web_tools, "_get_backend", lambda: shared)
    monkeypatch.setattr(
        web_tools, "_configured_backend",
        lambda key="backend": (configured.get(key) or "").lower().strip())
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(web_tools_extract, "selection_exists", lambda section: False)


def test_autodetected_search_only_falls_through_to_a_capable_extract_provider(
        monkeypatch, providers):
    from tools import web_tools
    from tools.web_tools_extract import _resolve_extract_provider

    _patch_resolution(monkeypatch, shared="fake-search", configured={})
    assert web_tools._get_extract_backend() == ""

    provider, error = _resolve_extract_provider(web_tools._get_extract_backend())
    assert error is None
    assert provider is providers


def test_web_extract_uses_the_fallthrough_provider(monkeypatch, providers):
    from tools import web_tools

    _patch_resolution(monkeypatch, shared="fake-search", configured={})

    async def _allow(_url: str) -> bool:
        return True

    monkeypatch.setattr(web_tools, "async_is_safe_url", _allow)
    monkeypatch.setattr(
        "tools.website_policy.check_website_access", lambda _url: None, raising=False)
    monkeypatch.setattr(
        "tools.web_result_cache.extract_cache_get",
        lambda *_a, **_kw: None, raising=False)

    result = json.loads(asyncio.run(
        web_tools.web_extract_tool(["https://example.com/a"], "markdown")))
    assert result["results"][0]["content"] == "text for https://example.com/a"
    assert providers.calls == [["https://example.com/a"]]


def test_explicit_search_only_backend_keeps_the_honest_error(monkeypatch, providers):
    from tools import web_tools
    from tools.web_tools_extract import _resolve_extract_provider

    _patch_resolution(
        monkeypatch, shared="fake-search", configured={"backend": "fake-search"})
    assert web_tools._get_extract_backend() == "fake-search"

    provider, error = _resolve_extract_provider("fake-search")
    assert provider is None
    assert "search-only" in json.loads(error)["error"]
