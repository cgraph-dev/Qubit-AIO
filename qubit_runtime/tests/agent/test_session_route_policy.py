"""Focused checks for conversation-only fallback opt-out; no provider calls."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.session_route_policy import agent_fallback_disabled, session_fallback_disabled
from qubit_state import SessionDB


@pytest.fixture
def store(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("locked", "tui", model="gpt-6.1-sol")
    db.create_session("normal", "tui", model="gpt-6.1-sol")
    db.patch_session_model_config("locked", {"session_fallback_disabled": True})
    yield db
    db.close()


def test_policy_is_session_and_store_scoped(store, tmp_path):
    assert session_fallback_disabled(store, "locked")
    assert not session_fallback_disabled(store, "normal")
    other = SessionDB(tmp_path / "other.db")
    try:
        other.create_session("locked", "tui")
        assert not session_fallback_disabled(other, "locked")
    finally:
        other.close()
    store.patch_session_model_config("locked", {"session_fallback_disabled": False})
    assert not session_fallback_disabled(store, "locked")


def test_lock_survives_reopen_and_runtime_persistence(store):
    from tui_gateway.server import _runtime_model_config

    runtime = SimpleNamespace(model="gpt-6.1-sol", provider="openai-api", base_url="https://api.openai.com/v1")
    cfg = _runtime_model_config(runtime, {"session_fallback_disabled": True})
    store.patch_session_model_config("locked", cfg)
    reopened = SessionDB(store.db_path, read_only=True)
    try:
        assert session_fallback_disabled(reopened, "locked")
    finally:
        reopened.close()


def test_lock_blocks_live_rate_limit_and_billing_switch(store):
    from agent.chat_completion_helpers import try_activate_fallback
    from agent.error_classifier import FailoverReason

    agent = SimpleNamespace(
        _session_db=store, session_id="locked", model="gpt-6.1-sol", provider="openai-api",
        _fallback_chain=[{"provider": "omniroute", "model": "auto"}], _fallback_index=0,
    )
    for reason in (FailoverReason.rate_limit, FailoverReason.billing):
        assert try_activate_fallback(agent, reason) is False
        assert (agent.model, agent.provider, agent._fallback_index) == ("gpt-6.1-sol", "openai-api", 0)
    assert agent_fallback_disabled(agent)


def test_locked_auth_error_does_not_resolve_fallback(monkeypatch):
    from tui_gateway import server
    from qubit_cli.auth import AuthError
    from qubit_cli import runtime_provider

    error = AuthError("primary unavailable")
    resolver = Mock(side_effect=error)
    chain = Mock(return_value=[{"provider": "omniroute", "model": "auto"}])
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", resolver)
    monkeypatch.setattr(server, "_load_fallback_model", chain)
    with pytest.raises(AuthError, match="primary unavailable"):
        server._resolve_runtime_with_fallback({"requested": "openai-api"}, allow_fallback=False)
    assert resolver.call_count == 1
    chain.assert_not_called()


def test_turn_refresh_cannot_restore_global_chain(store, monkeypatch):
    from tui_gateway import agent_callbacks
    from gateway.run import GatewayRunner
    from qubit_cli import config_effective

    apply = Mock()
    load = Mock(return_value={"fallback_providers": [{"provider": "omniroute", "model": "auto"}]})
    monkeypatch.setattr(GatewayRunner, "_apply_fallback_chain_to_agent", apply)
    monkeypatch.setattr(config_effective, "load_user_config_effective", load)
    monkeypatch.setattr(agent_callbacks, "_active_config_path", lambda: "unused", raising=False)
    locked = SimpleNamespace(_session_db=store, session_id="locked")
    agent_callbacks._sync_agent_fallback_with_config("ui", {"agent": locked})
    apply.assert_called_once_with(locked, [])
    load.assert_not_called()
    normal = SimpleNamespace(_session_db=store, session_id="normal")
    agent_callbacks._sync_agent_fallback_with_config("ui2", {"agent": normal})
    assert apply.call_args.args[1] == [{"provider": "omniroute", "model": "auto"}]
    load.assert_called_once()
