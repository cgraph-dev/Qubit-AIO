"""PV-03 — production callers resolve the profile and execute via the gateway.

Each migrated caller (summarizer, planner, vision runner, intent classifier,
orchestrator local chat) must:
  * resolve its effective model/profile through ``providers.resolver``; and
  * execute through ``providers.gateway.run`` on the local route.

The gateway's local adapters are the ONLY place an Ollama request is made, so
a local-only call performs no frontier/network request and cannot fabricate a
cloud identity. These tests stub ``gateway.run`` (the execution boundary) and
fail closed if any caller attempts a direct network call.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from voicebridge.providers import gateway as GW
from voicebridge.providers.contracts import ProviderResult, UsageRecord

REPO = Path(__file__).resolve().parents[1]


class _Cfg:
    """Minimal config whose only live key is the streaming toggle."""

    def __init__(self, stream_speech: bool = False) -> None:
        self.stream_speech = stream_speech

    def get(self, *path, default=None):
        if path[:2] == ("intent", "stream_speech"):
            return self.stream_speech
        return default


class _Recorder:
    """Stub for ``gateway.run`` capturing the ProviderRequest + kwargs."""

    def __init__(self, text: str = "", ok: bool = True, code: str = "",
                 identity: str = "local/qubit-brain4") -> None:
        self.text = text
        self.ok = ok
        self.code = code
        self.identity = identity
        self.calls = []

    def __call__(self, req, cfg, *args, **kwargs):
        self.calls.append({"req": req, "cfg": cfg, "kwargs": kwargs})
        return ProviderResult(
            ok=self.ok, text=self.text, provider="local",
            model="qubit-brain4", identity=self.identity, code=self.code,
            usage=UsageRecord(provider="local", model="qubit-brain4",
                              identity=self.identity, latency_ms=1))


def _no_direct_network(monkeypatch):
    """A direct urllib call proves a caller bypassed the gateway."""
    def _boom(*a, **k):
        raise AssertionError("direct provider/network call bypassed the gateway")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# --------------------------------------------------------------- summarizer
def test_summarizer_executes_through_gateway(monkeypatch):
    rec = _Recorder(text='{"summary": "All checks passed."}')
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge.summarizer import summarize_ollama

    assert summarize_ollama("agent output", max_chars=100) == "All checks passed."
    assert len(rec.calls) == 1
    req = rec.calls[0]["req"]
    assert req.route == "local"
    assert rec.calls[0]["kwargs"]["local_mode"] == "generate"


# ----------------------------------------------------------------- planner
def test_planner_executes_through_gateway(monkeypatch):
    rec = _Recorder(text=json.dumps(
        {"tool": "local.open_app", "arguments": {"app": "calculator"},
         "confidence": 0.9}))
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge.planner import plan

    proposal = plan("open the calculator")
    assert proposal is not None and proposal.tool == "local.open_app"
    assert len(rec.calls) == 1
    assert rec.calls[0]["req"].route == "local"
    assert rec.calls[0]["kwargs"]["local_mode"] == "chat_raw"


# ------------------------------------------------------------ vision runner
def test_vision_runner_executes_through_gateway(monkeypatch):
    rec = _Recorder(text="a cat on a mat")
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge import vision as V

    out = V._analyze(None, "stub-vision", "AAAA", "describe", 10.0)
    assert out == "a cat on a mat"
    assert len(rec.calls) == 1
    req = rec.calls[0]["req"]
    assert req.route == "local" and req.model == "stub-vision"
    kw = rec.calls[0]["kwargs"]
    assert kw["messages"][0]["images"] == ["AAAA"]
    assert kw["keep_alive"] == 0


def test_vision_runner_typed_failure(monkeypatch):
    rec = _Recorder(ok=False, code="PROVIDER_OUTAGE", text="")
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge import vision as V

    with pytest.raises(V.VisionError) as exc:
        V._analyze(None, "stub-vision", "AAAA", "describe", 10.0)
    assert exc.value.code == V.UPSTREAM_CODE


# --------------------------------------------------------- intent classify
def test_intent_classifier_executes_through_gateway(monkeypatch):
    rec = _Recorder(text=json.dumps(
        {"kind": "conversation", "confidence": 0.8}))
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge.intent.llm import classify_with_ollama

    out = classify_with_ollama("hello there my good friend")
    assert out is not None and out["kind"] == "conversation"
    assert len(rec.calls) == 1
    assert rec.calls[0]["req"].route == "local"
    assert rec.calls[0]["kwargs"]["local_mode"] == "generate"


# ------------------------------------------------------------- orchestrator
def test_orchestrator_local_chat_executes_through_gateway(monkeypatch):
    rec = _Recorder(text="hello there")
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge.orchestrator import VoiceOrchestrator

    class Self:
        _interrupt_requested = False
        cfg = _Cfg(stream_speech=False)

    fn = VoiceOrchestrator._chat_local_reply.__get__(Self())
    reply, spoke = fn("hi", "ctx", {"timeout_s": 4.0, "max_tokens": 100}, "mem")
    assert reply == "hello there" and spoke is False
    assert len(rec.calls) == 1
    assert rec.calls[0]["req"].route == "local"


def test_orchestrator_chat_ollama_executes_through_gateway(monkeypatch):
    rec = _Recorder(text="reply text")
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge.orchestrator import VoiceOrchestrator

    class _Ctx:
        conv_snapshot = ""

    class Self:
        cfg = _Cfg()
        conversation = type("C", (), {"as_prompt": lambda self: ""})()

        def _cur_ctx(self):
            return _Ctx()

        def _voice_conv_prompt(self, _text):
            # The canonical-store context builder; this test exercises gateway
            # routing, not context assembly.
            return ""

    fn = VoiceOrchestrator._chat_ollama.__get__(Self())
    assert fn("hello") == "reply text"
    assert len(rec.calls) == 1
    assert rec.calls[0]["kwargs"]["local_mode"] == "generate"


def test_orchestrator_progress_update_executes_through_gateway(monkeypatch):
    rec = _Recorder(text="working on it")
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    from voicebridge.orchestrator import VoiceOrchestrator

    class Self:
        cfg = _Cfg()
        shared = type("S", (), {"as_prompt": lambda self, **k: ""})()

    fn = VoiceOrchestrator._progress_local_update.__get__(Self())
    assert fn("worker did some things") == "working on it"
    assert len(rec.calls) == 1
    assert rec.calls[0]["req"].route == "local"


# --------------------------------------------- resolver + route truthfulness
def test_run_role_route_matches_resolved_locality(monkeypatch):
    """A local default profile is executed on the local route only."""
    rec = _Recorder(text="local answer")
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    res = GW.run_role("hi", None, role="chat")
    assert res.ok and res.identity == "local/qubit-brain4"
    assert rec.calls[0]["req"].route == "local"


def test_run_role_refuses_non_executable_profile(monkeypatch):
    """A non-executable external profile is a typed refusal, not local text."""
    from voicebridge.providers import resolver as R
    from voicebridge.providers.resolver import EffectiveProfile

    def _fake_resolve(cfg, *, role="chat", profile_id=None, session=None):
        return EffectiveProfile(
            profile_id="api-x", provider="openai", model="gpt-x", kind="api",
            route="frontier", locality="external", source="explicit",
            egress_required=True, egress_allowed=False, allowlisted=False,
            policy_enabled=False, executable=False, capabilities={},
            budgets={}, fallback_chain=(), rollback_to="")

    monkeypatch.setattr(R, "resolve", _fake_resolve)
    rec = _Recorder()
    monkeypatch.setattr(GW, "run", rec)
    _no_direct_network(monkeypatch)

    res = GW.run_role("hi", None, role="chat")
    assert res.ok is False and res.code == "EGRESS_DENIED"
    assert rec.calls == [], "a refused profile must not execute"


# ------------------------------------------------------------ speech wiring
def test_speech_override_prefers_explicit_profile(monkeypatch):
    from voicebridge.providers import speech as SP
    from voicebridge.orchestrator import _speech_override

    calls = []

    class _Eff:
        def to_public_dict(self):
            return {"source": "role-default", "executable": True,
                    "provider": "piper", "voice": "custom-voice",
                    "settings": {}}

    # Signature is resolve_speech(cfg, kind, ...): the previous test encoded
    # the swapped call that made the speech registry dead in production.
    monkeypatch.setattr(SP, "resolve_speech",
                        lambda cfg, kind: (calls.append(kind), _Eff())[1])
    out = _speech_override(None, "speech_out")
    assert calls == ["speech_out"]
    assert out["voice"] == "custom-voice"

    class _Shipped:
        def to_public_dict(self):
            return {"source": "shipped-default", "executable": True}

    monkeypatch.setattr(SP, "resolve_speech", lambda cfg, kind: _Shipped())
    assert _speech_override(None, "speech_in") == {}


def test_speech_resolution_is_wired_into_production_seams():
    """ASR/TTS/realtime selection must consume the canonical speech resolver."""
    src = (REPO / "voicebridge" / "orchestrator.py").read_text()
    assert '_speech_override(self.cfg, "speech_in")' in src
    assert '_speech_override(self.cfg, "speech_out")' in src
    assert "resolve_realtime(self.cfg)" in src
