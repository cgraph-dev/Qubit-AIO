"""Hermetic tests for the account-backed subscription research tool."""
from __future__ import annotations

import json

from tools import subscription_research_tool as tool


def _body(result: str) -> dict:
    return json.loads(result)


def test_perplexity_success(monkeypatch):
    monkeypatch.setattr(tool, "_omni_settings", lambda: ("http://127.0.0.1:20128/v1", "k"))
    calls = []

    def fake(base, key, path, payload=None, timeout=120.0):
        calls.append((path, payload))
        if path == "/chat/completions":
            return {"model": "pplx-auto", "choices": [{"message": {
                "content": "Answer text\n\nSources:\nhttps://example.com/a"}}],
                "usage": {"total_tokens": 10}}
        raise AssertionError(path)

    monkeypatch.setattr(tool, "_request", fake)
    out = _body(tool._handle_subscription_research({"query": "what?", "engine": "perplexity"}))
    assert out["engine"] == "perplexity"
    assert out["answer"].startswith("Answer text")
    assert out["sources"] == ["https://example.com/a"]
    assert calls and calls[0][0] == "/chat/completions"
    assert calls[0][1]["model"] == "perplexity-web/pplx-auto"


def test_gemini_not_connected_is_typed(monkeypatch):
    monkeypatch.setattr(tool, "_omni_settings", lambda: ("http://127.0.0.1:20128/v1", "k"))
    monkeypatch.setattr(tool, "_request", lambda *a, **k: {"data": [{"id": "perplexity-web/pplx-auto"}]})
    out = _body(tool._handle_subscription_research({"query": "what?", "engine": "gemini"}))
    assert "error" in out
    assert out["reason"] == "provider_not_connected"
    assert "Gemini Web" in out["error"]


def test_missing_key_is_typed(monkeypatch):
    monkeypatch.setattr(tool, "_omni_settings", lambda: ("http://127.0.0.1:20128/v1", ""))
    out = _body(tool._handle_subscription_research({"query": "what?"}))
    assert "error" in out
    assert out["reason"] == "provider_not_configured"


def test_auto_prefers_connected_gemini(monkeypatch):
    monkeypatch.setattr(tool, "_omni_settings", lambda: ("http://127.0.0.1:20128/v1", "k"))

    def fake(base, key, path, payload=None, timeout=120.0):
        if path == "/models":
            return {"data": [{"id": "gemini-web/gemini-4-argon"},
                             {"id": "perplexity-web/pplx-auto"}]}
        return {"model": "gemini-4-argon", "choices": [{"message": {"content": "G answer"}}]}

    monkeypatch.setattr(tool, "_request", fake)
    out = _body(tool._handle_subscription_research({"query": "what?", "engine": "auto"}))
    assert out["engine"] == "gemini"
    assert out["model"] == "gemini-4-argon"


def test_query_is_required(monkeypatch):
    monkeypatch.setattr(tool, "_omni_settings", lambda: ("u", "k"))
    out = _body(tool._handle_subscription_research({"query": "  "}))
    assert "error" in out
    assert out["reason"] == "invalid_request"
