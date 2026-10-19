"""Codex request bodies are zstd-compressed on the official route, and the 400 replay rung is live.

Two links the tree can silently lose while every unit still passes:

* ``codex_request_compression_hooks`` must be threaded into the keepalive ``httpx`` client — the
  hooks only reach the wire through ``client_cls`` at construction.
* ``ResponsesApiTransport.drop_unverified_replay`` is the FIRST rung of the
  ``invalid_encrypted_content`` recovery that ``agent/turn_recovery_codex`` already calls; without
  the method that call raised ``AttributeError`` and the turn fell straight through to stripping
  every reasoning blob (losing continuity the session could have kept).
"""

import httpx

from agent import codex_headers, process_bootstrap
from agent.transports.codex import ResponsesApiTransport

_CODEX_BASE = "https://chatgpt.com/backend-api/codex"
_OPENAI_ISSUER = "other:https://api.openai.com/v1"


def _drop_proxy_env(monkeypatch):
    for name in (
        "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
        "https_proxy", "http_proxy", "all_proxy", "NO_PROXY", "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


def test_compression_hooks_reach_the_client(monkeypatch):
    """Whatever the route offers is what the client is built with; a None offer changes nothing."""
    _drop_proxy_env(monkeypatch)
    process_bootstrap.close_shared_transports()

    sentinel = {"request": [lambda request: None]}
    monkeypatch.setattr(codex_headers, "codex_request_compression_hooks", lambda base_url, **kw: sentinel)
    client = process_bootstrap.build_keepalive_http_client(_CODEX_BASE)
    assert client is not None
    try:
        assert client.event_hooks["request"] == sentinel["request"]
    finally:
        client.close()

    monkeypatch.setattr(codex_headers, "codex_request_compression_hooks", lambda base_url, **kw: None)
    plain = process_bootstrap.build_keepalive_http_client(_CODEX_BASE)
    assert plain is not None
    try:
        assert plain.event_hooks["request"] == []
    finally:
        plain.close()
        process_bootstrap.close_shared_transports()


def test_compression_hooks_are_codex_only_and_codec_gated():
    """Only the official backend, and only where the stdlib zstd codec exists."""
    assert codex_headers.codex_request_compression_hooks("https://api.openai.com/v1") is None

    try:
        from compression import zstd  # noqa: F401
    except ImportError:
        assert codex_headers.codex_request_compression_hooks(_CODEX_BASE) is None
    else:
        hooks = codex_headers.codex_request_compression_hooks(_CODEX_BASE)
        assert hooks and hooks["request"]


def test_drop_unverified_replay_keeps_the_routes_own_blobs():
    transport = ResponsesApiTransport()
    transport._last_issuer_kind = _OPENAI_ISSUER
    transport._last_issuer_model = "gpt-5.5"

    own = {"type": "reasoning", "encrypted_content": "OWN", "_issuer_kind": _OPENAI_ISSUER, "_issuer_model": "gpt-5.5"}
    legacy = {"type": "reasoning", "encrypted_content": "LEGACY"}
    foreign = {"type": "reasoning", "encrypted_content": "XAI", "_issuer_kind": "xai:https://api.x.ai/v1", "_issuer_model": "grok-4.3"}
    messages = [{"role": "assistant", "content": "x", "codex_reasoning_items": [own, legacy, foreign]}]

    assert transport.drop_unverified_replay(messages) == 1
    assert messages[0]["codex_reasoning_items"] == [own, foreign]
    # Consumes its own precondition: the escalation rung takes over on a repeat rejection.
    assert transport.drop_unverified_replay(messages) == 0


def test_drop_unverified_replay_is_inert_before_a_request_is_built():
    """No issuer stamp yet -> nothing can be judged foreign; the caller escalates instead."""
    transport = ResponsesApiTransport()
    messages = [{"role": "assistant", "codex_reasoning_items": [{"type": "reasoning", "encrypted_content": "X"}]}]
    assert transport.drop_unverified_replay(messages) == 0
    assert len(messages[0]["codex_reasoning_items"]) == 1
