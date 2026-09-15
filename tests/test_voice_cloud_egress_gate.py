"""Voice fallback cloud egress gate (provider plan P2.5).

Local-only mode must be enforced *technically*, not by hiding a selector. The
shipped default (frontier disabled) must never let the voice fallback reach
Perplexity/DeepSeek, and an owner-enabled allowlisted frontier policy must
permit it. These tests exercise the real production methods, not a seam.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _Cfg:
    """Minimal config exposing only the ``intent.frontier`` node and intents."""

    def __init__(self, frontier=None, *, model: str = "") -> None:
        self._frontier = frontier
        self._model = model

    def get(self, *path, default=None):
        if path == ("intent", "frontier"):
            return self._frontier
        if path == ("intent", "deepseek_model"):
            return self._model
        return default


def _self(cfg, *, key=None, conversation=""):
    from voicebridge.orchestrator import VoiceOrchestrator

    class Self:
        pass

    s = Self()
    s.cfg = cfg
    for name in ("_deepseek_model_name", "_perplexity_model_name",
                 "_cloud_egress_allowed", "_chat_deepseek",
                 "_chat_deepseek_stream", "_chat_perplexity"):
        setattr(s, name, getattr(VoiceOrchestrator, name).__get__(s))
    if key is not None:
        s._deepseek_key = lambda: key
        s._perplexity_key = lambda: key
    s.conversation = type("Conv", (), {
        "as_prompt": lambda self: conversation})()
    return s


class _GWRun:
    """Stub for ``gateway.run`` capturing the routed ProviderRequest."""

    def __init__(self, text="gateway answer", ok=True, code="",
                 identity="deepseek/deepseek-v4-flash"):
        self.text = text
        self.ok = ok
        self.code = code
        self.identity = identity
        self.calls = []

    def __call__(self, req, cfg, *args, **kwargs):
        from voicebridge.providers.contracts import ProviderResult, UsageRecord
        self.calls.append({"req": req, "cfg": cfg, "kwargs": kwargs})
        return ProviderResult(
            ok=self.ok, text=self.text, provider=req.provider or "frontier",
            model=req.model, identity=self.identity, code=self.code,
            usage=UsageRecord(provider=req.provider, model=req.model,
                              identity=self.identity, latency_ms=1))


def _method(name: str, s):
    from voicebridge.orchestrator import VoiceOrchestrator

    return getattr(VoiceOrchestrator, name).__get__(s)


def _no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("cloud call bypassed the frontier egress gate")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_local_only_default_denies_cloud_egress():
    s = _self(_Cfg(None))
    allowed = _method("_cloud_egress_allowed", s)
    assert allowed("deepseek", "deepseek-v4-flash") is False
    assert allowed("perplexity", "sonar-pro") is False


def test_enabled_allowlisted_frontier_permits_exact_model():
    frontier = {"enabled": True, "allowlist": ["deepseek/deepseek-v4-flash"]}
    s = _self(_Cfg(frontier))
    allowed = _method("_cloud_egress_allowed", s)
    assert allowed("deepseek", "deepseek-v4-flash") is True
    # An allowlist is exact: a different model stays refused.
    assert allowed("deepseek", "deepseek-other") is False
    assert allowed("perplexity", "sonar-pro") is False


def test_chat_deepseek_refused_without_frontier_policy(monkeypatch):
    _no_network(monkeypatch)
    s = _self(_Cfg(None, model="deepseek-v4-flash"))
    assert _method("_chat_deepseek", s)("hello") == ""
    assert _method("_chat_deepseek_stream", s)("hello", lambda _s: None) == ""


def test_chat_perplexity_refused_without_frontier_policy(monkeypatch):
    _no_network(monkeypatch)
    s = _self(_Cfg(None))
    assert _method("_chat_perplexity", s)("latest news") == ""


def test_cloud_fallback_methods_still_deny_for_unallowlisted_model(
        monkeypatch):
    _no_network(monkeypatch)
    frontier = {"enabled": True, "allowlist": ["deepseek/deepseek-v4-flash"]}
    s = _self(_Cfg(frontier, model="deepseek-other"))
    assert _method("_chat_deepseek", s)("hello") == ""
    assert _method("_chat_perplexity", s)("latest news") == ""


# --------------------------------------------------- gateway routing (P2.1/P2.5)
_DS_FRONTIER = {"enabled": True, "allowlist": ["deepseek/deepseek-v4-flash"]}
_PP_FRONTIER = {"enabled": True, "allowlist": ["perplexity/sonar-pro"]}


def test_enabled_frontier_routes_deepseek_through_gateway(monkeypatch):
    """Allowed cloud egress executes via the canonical gateway, not urllib."""
    from voicebridge.providers import gateway as GW

    _no_network(monkeypatch)
    rec = _GWRun(text="cloud answer")
    monkeypatch.setattr(GW, "run", rec)
    s = _self(_Cfg(_DS_FRONTIER, model="deepseek-v4-flash"), key="sk-test-key")
    assert _method("_chat_deepseek", s)("hello there") == "cloud answer"
    assert len(rec.calls) == 1
    req = rec.calls[0]["req"]
    assert req.route == "frontier"
    assert req.provider == "deepseek"
    assert req.model == "deepseek-v4-flash"
    assert rec.calls[0]["kwargs"]["api_key"] == "sk-test-key"
    assert "deepseek" in rec.calls[0]["kwargs"]["base_url"]


def test_enabled_frontier_routes_perplexity_through_gateway(monkeypatch):
    from voicebridge.providers import gateway as GW

    _no_network(monkeypatch)
    rec = _GWRun(text="web answer", identity="perplexity/sonar-pro")
    monkeypatch.setattr(GW, "run", rec)
    s = _self(_Cfg(_PP_FRONTIER), key="pp-test-key")
    assert _method("_chat_perplexity", s)("latest news") == "web answer"
    assert len(rec.calls) == 1
    req = rec.calls[0]["req"]
    assert (req.route, req.provider, req.model) == (
        "frontier", "perplexity", "sonar-pro")
    assert rec.calls[0]["kwargs"]["api_key"] == "pp-test-key"


def test_deepseek_stream_uses_gateway_and_emits_full_reply(monkeypatch):
    """The frontier route is blocking-only: one truthful segment, not fake SSE."""
    from voicebridge.providers import gateway as GW

    _no_network(monkeypatch)
    rec = _GWRun(text="One. Two!")
    monkeypatch.setattr(GW, "run", rec)
    s = _self(_Cfg(_DS_FRONTIER, model="deepseek-v4-flash"), key="sk-test-key")
    spoken = []
    assert _method("_chat_deepseek_stream", s)("hello", spoken.append) == \
        "One. Two!"
    assert spoken == ["One. Two!"]
    assert len(rec.calls) == 1
    assert rec.calls[0]["req"].route == "frontier"


def test_disabled_frontier_never_reaches_gateway(monkeypatch):
    """Local-only default must refuse before any gateway execution."""
    from voicebridge.providers import gateway as GW

    _no_network(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("gateway executed with frontier disabled")

    monkeypatch.setattr(GW, "run", _boom)
    s = _self(_Cfg(None, model="deepseek-v4-flash"), key="sk-test-key")
    assert _method("_chat_deepseek", s)("hi") == ""
    assert _method("_chat_perplexity", s)("hi") == ""
    assert _method("_chat_deepseek_stream", s)("hi", lambda _s: None) == ""
