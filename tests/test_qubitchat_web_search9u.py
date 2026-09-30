"""Wave B1 Task 2 (backend only): shared web answer path.

Mock-level only — the chat_transport web_search branch is Wave C and is
NOT tested here. These tests pin ``orchestrator.answer_web_query``:
same gateway/timeouts/redaction as the voice path, typed WEB_UNAVAILABLE
on failure, never fabricated.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _TestCfg:
    """Minimal config exposing the intent node + frontier policy."""

    def __init__(self, frontier=None, *, perplexity_enabled=True,
                 model="sonar-pro"):
        self._frontier = frontier
        self._enabled = perplexity_enabled
        self._model = model

    def get(self, *path, default=None):
        if path == ("intent", "frontier"):
            return self._frontier
        if path == ("intent", "perplexity_enabled"):
            return self._enabled
        if path == ("intent", "perplexity_model"):
            return self._model
        if path == ("intent", "perplexity_url"):
            return "https://api.perplexity.ai/chat/completions"
        return default


_PP_FRONTIER = {"enabled": True, "allowlist": ["perplexity/sonar-pro"]}


def _gw_stub(text="Aion 2 releases Q4.", ok=True, code=""):
    from voicebridge.providers.contracts import ProviderResult, UsageRecord

    calls = []

    def _run(req, cfg, *args, **kwargs):
        calls.append({"req": req, "cfg": cfg, "kwargs": kwargs})
        return ProviderResult(
            ok=ok, text=text, provider=req.provider or "frontier",
            model=req.model, identity="perplexity/sonar-pro", code=code,
            usage=UsageRecord(provider=req.provider, model=req.model,
                              identity="perplexity/sonar-pro", latency_ms=1))

    _run.calls = calls
    return _run


def test_answer_web_query_exists_with_shared_signature():
    from voicebridge import orchestrator as ORC
    import inspect

    assert callable(getattr(ORC, "answer_web_query", None))
    sig = inspect.signature(ORC.answer_web_query)
    params = list(sig.parameters)
    assert params[:2] == ["cfg", "text"], sig


def test_answer_web_query_uses_gateway_not_new_client(monkeypatch):
    from voicebridge.providers import gateway as GW
    from voicebridge import orchestrator as ORC

    monkeypatch.setenv("PERPLEXITY_API_KEY", "pp-test-key")
    run = _gw_stub(text="Aion 2 releases Q4 2026. See https://example/aion2")
    monkeypatch.setattr(GW, "run", run)
    out = ORC.answer_web_query(
        _TestCfg(_PP_FRONTIER),
        "use perplexity api to search when Aion 2 will release")
    assert out.get("ok") is True, out
    assert "Aion 2" in out.get("answer", ""), out
    assert out.get("code") == "OK", out
    assert out.get("sources") == ["https://example/aion2"], out
    assert len(run.calls) == 1
    req = run.calls[0]["req"]
    assert (req.route, req.provider, req.model) == (
        "frontier", "perplexity", "sonar-pro")
    assert req.timeout_s == 45.0
    assert run.calls[0]["kwargs"]["api_key"] == "pp-test-key"


def test_answer_web_query_refused_without_frontier(monkeypatch):
    import urllib.request
    from voicebridge.providers import gateway as GW
    from voicebridge import orchestrator as ORC

    def _boom(*a, **k):
        raise AssertionError("gateway executed with frontier disabled")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(GW, "run", _boom)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    out = ORC.answer_web_query(
        _TestCfg(None), "use perplexity to search when Aion 2 will release")
    assert out.get("ok") is False, out
    assert out.get("code") == "WEB_UNAVAILABLE", out


def test_answer_web_query_gated_without_web_intent(monkeypatch):
    from voicebridge.providers import gateway as GW
    from voicebridge import orchestrator as ORC

    def _boom(*a, **k):
        raise AssertionError("gateway executed without web intent")

    monkeypatch.setattr(GW, "run", _boom)
    out = ORC.answer_web_query(_TestCfg(_PP_FRONTIER), "what is 17 times 23")
    assert out.get("ok") is False, out
    assert out.get("code") == "WEB_UNAVAILABLE", out


def test_answer_web_query_redacts_secrets_in_answer(monkeypatch):
    from voicebridge.providers import gateway as GW
    from voicebridge import orchestrator as ORC

    monkeypatch.setenv("PERPLEXITY_API_KEY", "pp-test-key")
    run = _gw_stub(text="leaked sk-live-ABC123XYZ789qrs done")
    monkeypatch.setattr(GW, "run", run)
    out = ORC.answer_web_query(
        _TestCfg(_PP_FRONTIER), "use perplexity to search test")
    assert out.get("ok") is True, out
    assert "sk-live-ABC123XYZ789qrs" not in str(out), out
    assert "[REDACTED]" in out.get("answer", ""), out


def test_handle_conversation_calls_shared_path():
    import inspect
    from voicebridge import orchestrator as ORC

    src = inspect.getsource(ORC.VoiceOrchestrator._handle_conversation)
    assert "answer_web_query" in src, \
        "_handle_conversation must call the shared answer_web_query"


def test_chat_perplexity_delegates_to_shared_path(monkeypatch):
    from voicebridge import orchestrator as ORC

    monkeypatch.setattr(
        ORC, "answer_web_query",
        lambda cfg, text, **kw: {"ok": True, "answer": "shared answer",
                                 "sources": [], "code": "OK"})

    class _Self:
        pass

    s = _Self()
    s.cfg = _TestCfg(_PP_FRONTIER)
    bound = ORC.VoiceOrchestrator._chat_perplexity.__get__(s)
    assert bound("use perplexity to search test") == "shared answer"
