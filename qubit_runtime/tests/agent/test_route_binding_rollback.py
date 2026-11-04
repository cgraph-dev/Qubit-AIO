"""A late fallback route-bind failure must not leave the agent half-bound.

``bind_route_entry`` used to mutate model/provider/base_url/client and only then run the
failure-prone tail (LM Studio preload, compressor probe, extra_body scoping). A raise there
left the new identity paired with the old client/pool, so the retry loop kept dispatching the
route that had just failed. The swap is now transactional.
"""

from types import SimpleNamespace

import pytest

import agent.route_binding as route_binding


class _Client:
    base_url = "https://fallback.example/v1"
    api_key = "fallback-key"
    _custom_headers = {}


class _Compressor:
    def __init__(self):
        self.model = "primary-model"
        self.context_length = 111
        self.base_url = "https://primary.example/v1"
        self.api_key = "primary-key"
        self.provider = "primary"
        self.api_mode = "chat_completions"
        self.max_tokens = 4096


def _agent():
    compressor = _Compressor()
    return SimpleNamespace(
        model="primary-model", provider="primary", requested_provider="primary",
        base_url="https://primary.example/v1", api_mode="chat_completions",
        api_key="primary-key", client="primary-client",
        _client_kwargs={"api_key": "primary-key", "base_url": "https://primary.example/v1"},
        _anthropic_client="primary-anthropic", _anthropic_api_key="primary-key",
        _anthropic_base_url="https://primary.example/v1", _is_anthropic_oauth=False,
        _credential_pool="primary-pool", _credential_pool_entry_id="primary-entry",
        _config_context_length=999, _reasoning_echo_flag=True,
        _fallback_activated=False, runtime_capabilities={"old": True},
        _use_prompt_caching=True, _use_native_cache_layout=False,
        request_overrides={"extra_body": {"keep": True}}, reasoning_config={"effort": "high"},
        _bedrock_region="us-east-1", _bedrock_guardrail_config={"id": "old"},
        _transport_cache={"transport": "primary"},
        context_compressor=compressor,
        _anthropic_prompt_cache_policy=lambda **_kw: (False, False),
        _ensure_lmstudio_runtime_loaded=lambda: None,
    )


def test_late_bind_failure_rolls_the_route_back(monkeypatch):
    agent = _agent()

    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda *a, **k: (_Client(), "fallback-model"),
    )
    monkeypatch.setattr("qubit_cli.fallback_config.resolve_entry_api_key", lambda _entry: "fallback-key")
    monkeypatch.setattr(
        "agent.chat_completion_helpers._fallback_api_mode_hint",
        lambda *_a, **_k: (False, "chat_completions"),
    )
    monkeypatch.setattr("qubit_cli.providers.is_actual_route", lambda *_a, **_k: True)

    def _rebind_pool(target, provider, model):
        target._credential_pool = "fallback-pool"
        target._credential_pool_entry_id = None

    def _swap_clients(target, client, provider, model, base_url, api_mode):
        target.client = client
        target.api_key = "fallback-key"
        target._client_kwargs = {"api_key": "fallback-key", "base_url": base_url}

    def _late_failure(target):
        # Mutate the compressor, then fail: this is the exact late-bind shape that used to
        # leave the route half-swapped.
        target.context_compressor.model = "fallback-model"
        target.context_compressor.context_length = 200000
        raise RuntimeError("late bind failure")

    monkeypatch.setattr(
        "agent.chat_completion_helpers._rebind_fallback_credential_pool", _rebind_pool)
    monkeypatch.setattr("agent.client_lifecycle._swap_fallback_clients", _swap_clients)
    monkeypatch.setattr("agent.agent_runtime_helpers.sync_credential_pool_entry_id", lambda _a: None)
    monkeypatch.setattr(
        "agent.chat_completion_helpers._update_fallback_context_compressor", _late_failure)

    with pytest.raises(RuntimeError, match="late bind failure"):
        route_binding.bind_route_entry(
            agent, {"provider": "fallback", "model": "fallback-model"}, "fallback", "fallback-model")

    assert (agent.model, agent.provider, agent.requested_provider) == (
        "primary-model", "primary", "primary")
    assert agent.base_url == "https://primary.example/v1"
    assert agent.api_mode == "chat_completions"
    assert (agent.api_key, agent.client) == ("primary-key", "primary-client")
    assert agent._client_kwargs == {
        "api_key": "primary-key", "base_url": "https://primary.example/v1"}
    assert agent._anthropic_client == "primary-anthropic"
    assert agent._fallback_activated is False
    assert agent._credential_pool == "primary-pool"
    assert agent._credential_pool_entry_id == "primary-entry"
    assert agent._config_context_length == 999
    assert agent._reasoning_echo_flag is True
    assert agent.runtime_capabilities == {"old": True}
    assert agent._use_prompt_caching is True and agent._use_native_cache_layout is False
    assert agent.request_overrides == {"extra_body": {"keep": True}}
    assert agent.reasoning_config == {"effort": "high"}
    assert agent._bedrock_region == "us-east-1"
    assert agent._bedrock_guardrail_config == {"id": "old"}
    assert agent._transport_cache == {"transport": "primary"}
    assert (agent.context_compressor.model, agent.context_compressor.context_length) == (
        "primary-model", 111)
