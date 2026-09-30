"""9U — /qubit-chat reasoning selector: real, persisted, sent, refused.

The selector is a per-request output budget threaded end to end: validated
once (unknown levels fail typed BEFORE any session write), sent to the
resolved model on the local blocking path, the local streaming path, and
the frontier path (as request max_tokens, policy-capped, receipted), and
persisted per session in the canonical session store. Nothing is silently
substituted and nothing is fabricated on failure.

Hermetic: tmp XDG state only; stubbed transports/adapters; no network.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def test_normalize_reasoning_levels():
    from voicebridge.local_llm import (
        ReasoningInvalidError,
        normalize_reasoning_level,
    )

    assert normalize_reasoning_level(None) is None
    assert normalize_reasoning_level("high") == "high"
    assert normalize_reasoning_level(" High ") == "high"
    # xhigh/ultra are real levels in the canonical vendored ladder
    # (``qubit_constants.VALID_REASONING_EFFORTS``) the picker mirrors.
    assert normalize_reasoning_level("xhigh") == "xhigh"
    assert normalize_reasoning_level("ultra") == "ultra"
    with pytest.raises(ReasoningInvalidError):
        normalize_reasoning_level("bogus")
    with pytest.raises(ReasoningInvalidError):
        normalize_reasoning_level("")


def test_turn_rejects_unknown_reasoning_before_session_write(tmp_path,
                                                             monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    res = CT.turn(cfg, "hello", session="s-reason", reasoning="bogus")
    assert res.get("code") == "REASONING_INVALID", res
    assert res.get("state") == "FAILED", res
    # No partial write: the session was never created.
    assert CT.store(cfg).show("s-reason").get("state") == "MISSING"


def _stub_gateway_role(monkeypatch, *, capture, text="stub reply"):
    import voicebridge.providers.gateway as Gw

    def _fake(prompt, cfg, **kw):
        capture.update(kw)
        from voicebridge.local_llm import resolve_reasoning_budget

        details = {}
        if kw.get("reasoning") is not None:
            details["reasoning_applied"] = {
                "level": kw["reasoning"],
                "max_tokens": resolve_reasoning_budget(kw["reasoning"])}
        return Gw.ProviderResult(
            ok=True, text=text, provider="local", model="qubit-brain4",
            identity="local/qubit-brain4",
            usage=Gw.UsageRecord(
                provider="local", model="qubit-brain4",
                identity="local/qubit-brain4", latency_ms=1),
            details=details)

    monkeypatch.setattr(Gw, "run_role", _fake)


def test_turn_threads_reasoning_to_gateway_and_receipts_it(tmp_path,
                                                           monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    capture = {}
    _stub_gateway_role(monkeypatch, capture=capture)
    res = CT.turn(cfg, "hello", session="s-high", reasoning="high")
    assert res.get("ok") is True, res
    assert capture.get("reasoning") == "high", capture
    receipt = res.get("receipt") or {}
    # The applied level is receipted (the vendored `reasoning` message
    # column carries model trace content, never the selector level).
    applied = receipt.get("reasoning_applied") or {}
    assert applied.get("level") == "high", receipt
    assert res["session_id"] == "s-high"


def test_stream_turn_rejects_unknown_reasoning_first(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    events = list(CT.stream_turn(cfg, "hello", session="s-stream-bad",
                                 reasoning="bogus"))
    assert events and events[0].get("event") == "error", events
    assert events[0].get("code") == "REASONING_INVALID", events


def test_stream_turn_threads_reasoning_to_worker(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    capture = {}
    _stub_gateway_role(monkeypatch, capture=capture, text="streamed!")
    events = list(CT.stream_turn(cfg, "hello", session="s-stream-high",
                                 reasoning="low"))
    assert capture.get("reasoning") == "low", capture
    done = [e for e in events if e.get("event") == "done"]
    assert done, events
    assert done[0]["receipt"].get("reasoning_applied", {}).get("level") \
        == "low", done[0]


def test_local_stream_applies_reasoning_budget(tmp_path, monkeypatch):
    import voicebridge.local_llm as LLM

    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["max_tokens"] = __import__("json").loads(
            req.data.decode())["options"]["num_predict"]

        class _R:
            def read(self):
                return b'{"message": {"content": ""}, "done": true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    import urllib.request as _ureq

    monkeypatch.setattr(_ureq, "urlopen", _fake_urlopen)
    out = LLM.chat_local_stream(
        "hi", "", model="m", url="http://x", timeout_s=1.0,
        max_tokens=100, on_sentence=lambda s: None, reasoning="high")
    assert out == ""
    assert seen.get("max_tokens") == 1024, seen


def test_frontier_applies_reasoning_budget_policy_capped(tmp_path, monkeypatch):
    """The frontier path sends the budget as request max_tokens (capped by
    policy) and receipts it — reusing the 8Q hermetic catalog pattern."""
    import sys
    import time
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from voicebridge.providers import gateway as G
    from voicebridge.providers.catalog import ModelEntry, build_catalog
    from voicebridge.providers.contracts import ProviderRequest

    import dataclasses

    entry = ModelEntry(provider="zen", model="m1", context_tokens=65536,
                       max_output_tokens=4096, supports_tools=True,
                       supports_structured=True, supports_streaming=True,
                       price_in_per_1k=0.0, price_out_per_1k=0.0)
    catalog = build_catalog([entry], source="test", fetched_at=time.time())

    class _Cfg:
        def get(self, *path, default=None):
            if path[:2] == ("intent", "frontier"):
                node = {
                    "enabled": True, "route": "frontier",
                    "privacy_tier": "standard",
                    "allowlist": ["zen/m1"],
                    "providers": {
                        "zen": {"base_url": "https://zen.example/v1",
                                "key_ref": "env:QUBIT_TEST_ZEN_KEY"}},
                    "max_tokens": 64, "max_context_chars": 4000,
                    "max_response_chars": 4000, "timeout_s": 5.0,
                    "max_retries": 0, "rpm": 10, "max_concurrency": 2,
                    "daily_usd": 1.0, "monthly_usd": 10.0,
                }
                if len(path) == 2:
                    return node
                return node.get(path[2], default)
            return default

    monkeypatch.setenv("QUBIT_TEST_ZEN_KEY", "test-key-123")
    seen = {}

    def _transport(url, headers, payload, timeout_s):
        seen["max_tokens"] = payload.get("max_tokens")
        return 200, {"choices": [{"message": {"content": "frontier hi"}}],
                     "usage": {"prompt_tokens": 5, "completion_tokens": 7}}

    res = G.run(ProviderRequest(prompt="hi", route="frontier",
                                provider="zen", model="m1"),
                _Cfg(), catalog=catalog, transport=_transport,
                reasoning="high")
    assert res.ok is True, (res.code, res.message)
    # high = 1024 budget, policy caps at 64: the wire value is the cap.
    assert seen.get("max_tokens") == 64, seen
    assert res.details.get("reasoning_applied") == {
        "level": "high", "max_tokens": 64}, res.details


def test_session_reasoning_persist_and_effective(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    created = CT.store(cfg).create("pref", session_id="s-pref")
    assert created.get("state") == "OK", created

    code, out = HC.dispatch(cfg, "PATCH", "/api/qubit/chat/sessions/s-pref",
                            {}, {"reasoning": "medium"})
    assert code == 200 and out.get("ok") is True, (code, out)

    code, out = HC.dispatch(cfg, "PATCH", "/api/qubit/chat/sessions/s-pref",
                            {}, {"reasoning": "bogus"})
    assert (code, out.get("code")) == (400, "REASONING_INVALID"), (code, out)

    shown = CT.store(cfg).show("s-pref")
    meta = CT.store(cfg).meta_get("s-pref").get("meta", {})
    assert meta.get("reasoning") == "medium", meta

    # Effective preference applies when the turn omits the selector
    # (resolved at the handler layer, like the dashboard send path).
    capture = {}
    _stub_gateway_role(monkeypatch, capture=capture)
    code, res = HC.h_chat_turn(cfg, {}, {"text": "hello", "session": "s-pref"},
                               {})
    assert code == 200 and res.get("ok") is True, (code, res)
    assert capture.get("reasoning") == "medium", capture

    # ... and the explicit selector wins over the preference.
    code, res = HC.h_chat_turn(
        cfg, {}, {"text": "hello again", "session": "s-pref",
                  "reasoning": "low"}, {})
    assert code == 200 and res.get("ok") is True, (code, res)
    assert capture.get("reasoning") == "low", capture


def test_chat_turn_handler_reasoning_codes(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.h_chat_turn(cfg, {}, {"text": "hi", "reasoning": "bogus",
                                         "session": "s-h"}, {})
    assert (code, out.get("code")) == (400, "REASONING_INVALID"), (code, out)
