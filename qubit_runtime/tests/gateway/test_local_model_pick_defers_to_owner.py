"""A typed ``/model`` on a LOCAL session commits through the owner's canonical mutation.

The local surface runs its frozen policy, so the runner's per-platform snapshot is not the
authority: the pick must land as a deferred session mutation at the next turn boundary
(``defer_local_model_pick``), not by rewriting the cached agent in place. A messaging chat
keeps the in-process switch. These are behaviour contracts, not snapshots.
"""

import asyncio
from types import SimpleNamespace

import pytest

from gateway.slash_commands_model import GatewayModelCommandsMixin, _ModelSwitchContext


class Runner(GatewayModelCommandsMixin):
    """Every collaborating writer records; the two commits are distinguishable in ``calls``."""

    def __init__(self, calls):
        self.calls = calls
        self.config = SimpleNamespace(multiplex_profiles=False)

    def _switch_cached_agent_model(self, result, ctx, picker):
        self.calls.append("cached_agent_switch")
        return None

    async def _record_model_switch(self, *a, **k):
        self.calls.append("record_switch")
        return None

    async def _model_switch_confirmation(self, *a, **k):
        return "switched"

    def _apply_reasoning_selection(self, *a, **k):
        self.calls.append("apply_reasoning")
        return "reasoning-set"

    def _save_gateway_config_key(self, key_path, value):
        self.calls.append(("save", key_path, value))
        return True


def _result(model="claude-sonnet-5", provider="anthropic"):
    return SimpleNamespace(
        new_model=model, target_provider=provider, provider_label=provider,
        resolved_via_alias=model, model_info=None, base_url="", api_key="",
    )


def _ctx(**kw):
    base = dict(session_key="local:1", source=SimpleNamespace(platform="local", chat_id="s1"),
                config_path=None, persist_global=True)
    base.update(kw)
    return _ModelSwitchContext(**base)


def _local(monkeypatch, deferred):
    monkeypatch.setattr(
        "gateway.session_setting_commands.local_authority_live",
        lambda runner, source: ("authority", SimpleNamespace(source=SimpleNamespace(chat_id="s1"))))

    def _defer(authority, live, result, effort=""):
        deferred.append((authority, result.new_model, result.target_provider, effort))

    monkeypatch.setattr("gateway.session_setting_commands.defer_local_model_pick", _defer)


@pytest.fixture
def no_config_write(monkeypatch):
    from gateway import slash_commands_model

    async def _persist(result, config_path):
        return None

    monkeypatch.setattr(slash_commands_model, "_persist_model_switch_to_config", _persist)


def test_local_pick_defers_to_the_owner_and_skips_the_cached_agent(monkeypatch, no_config_write):
    deferred = []
    runner = Runner([])
    _local(monkeypatch, deferred)

    reply = asyncio.run(runner._commit_model_switch_locked(
        _result(), _ctx(reasoning_effort="high"),
        source=SimpleNamespace(platform="local"), picker=False))

    assert deferred == [("authority", "claude-sonnet-5", "anthropic", "high")]
    assert "cached_agent_switch" not in runner.calls   # the in-process rewrite is NOT the local commit
    assert "record_switch" not in runner.calls         # nor is the per-platform snapshot record
    assert ("save", "agent.reasoning_effort", "high") in runner.calls
    assert reply.endswith("Applies from your next message.")


def test_local_pick_refuses_once_instead_of_switching_for_one_turn(monkeypatch, no_config_write):
    deferred = []
    runner = Runner([])
    _local(monkeypatch, deferred)

    reply = asyncio.run(runner._commit_model_switch_locked(
        _result(), _ctx(one_turn=True), source=SimpleNamespace(platform="local"), picker=False))

    assert "--once" in reply
    assert deferred == []
    assert "cached_agent_switch" not in runner.calls


def test_messaging_chat_keeps_the_in_process_switch(monkeypatch, no_config_write):
    runner = Runner([])
    monkeypatch.setattr("gateway.session_setting_commands.local_authority_live",
                        lambda r, source: (None, None))

    reply = asyncio.run(runner._commit_model_switch_locked(
        _result(), _ctx(source=SimpleNamespace(platform="telegram", chat_id="c1")),
        source=SimpleNamespace(platform="telegram"), picker=False))

    assert "cached_agent_switch" in runner.calls
    assert "record_switch" in runner.calls
    assert not reply.endswith("Applies from your next message.")
