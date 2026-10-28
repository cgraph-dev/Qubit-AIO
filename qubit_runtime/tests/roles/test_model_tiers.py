"""Role model tiers are real: mapped provider/model, honest fallback, CLI override.

Everything here is hermetic: fake resolver/agent/backends, no model call and no
network. The default local/detail tiers use the resident local brain; `api`/empty use
the active model. Explicit overrides and a cleared `{}` map remain supported.
"""
from __future__ import annotations

import argparse
import io
import json
import socket
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.runtime_port import RuntimeCapabilities, TurnEvent  # noqa: E402
from roles import runner as role_runner  # noqa: E402
from roles.base import AgentRole  # noqa: E402


def _role(tier: str = "local", **kw) -> AgentRole:
    data = dict(
        id="demo",
        title="Demo role",
        description="the demo posture",
        system_prompt="You are the demo role.",
        toolsets=("search",),
        voice_aliases=("run the demo role",),
        source="test",
        model_tier=tier,
    )
    data.update(kw)
    return AgentRole(**data)


def _config(tiers=None, *, provider="active-prov", model="active-model"):
    return {
        "model": {"provider": provider, "default": model},
        "roles": {"model_tiers": dict(tiers or {})},
    }


def _done(text="Role answer.", **data):
    return TurnEvent("done", text=text, data={"completed": True, **data})


class _FakeBackend:
    name = "native"
    capabilities = RuntimeCapabilities(True, True, True, False)
    supports_roles = False

    def __init__(self, events):
        self.events = events
        self.requests = []

    def run_turn(self, request, cancel=None):
        self.requests.append(request)
        yield from self.events


class _Registry:
    def __init__(self, backends):
        self._backends = backends

    def get(self, name):
        return self._backends[name]


def _fake_agent_runtime(monkeypatch):
    """Patch the resolver and AIAgent; return (resolver_calls, agent_kwargs)."""
    resolver_calls: dict = {}
    agent_kwargs: dict = {}

    def resolve(**kwargs):
        resolver_calls.update(kwargs)
        return {
            "provider": kwargs.get("requested") or "fallback-prov",
            "model": kwargs.get("target_model") or "fallback-model",
            "api_key": "k",
            "base_url": "http://localhost",
        }

    class _FakeAgent:
        def __init__(self, **kwargs):
            agent_kwargs.update(kwargs)
            self.model = kwargs.get("model")
            self.provider = kwargs.get("provider")

    monkeypatch.setattr(
        "qubit_cli.runtime_provider.resolve_runtime_provider", resolve)
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
    return resolver_calls, agent_kwargs


def test_default_config_local_tiers_use_the_local_brain():
    from qubit_cli.config_defaults import DEFAULT_CONFIG

    tiers = DEFAULT_CONFIG["roles"]["model_tiers"]
    assert tiers["local"] == {"provider": "ollama", "model": "qubit-brain"}
    assert tiers["detail"] == {"provider": "ollama", "model": "qubit-brain"}
    assert "api" not in tiers


def test_mapped_tier_sets_provider_and_model(monkeypatch):
    resolver_calls, agent_kwargs = _fake_agent_runtime(monkeypatch)
    role = _role("local")

    role_runner._native_role_agent(
        role, role_runner.build_turn_request(role),
        config=_config({"local": {"provider": "tier-prov",
                                  "model": "tier-model"}}))

    assert resolver_calls == {"requested": "tier-prov",
                              "target_model": "tier-model"}
    assert agent_kwargs["model"] == "tier-model"
    assert agent_kwargs["provider"] == "tier-prov"


def test_run_role_reports_the_applied_tier(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    backend = _FakeBackend([_done(model="m", provider="p")])

    result = role_runner.run_role(
        "demo", "hi", registry=_Registry({"native": backend}),
        config=_config({"local": {"provider": "tier-prov",
                                  "model": "tier-model"}}))

    assert result["ok"] is True
    assert result["model_tier"] == "local"
    assert result["model_tier_applied"] is True
    assert result["model_tier_note"] == "roles.model_tiers['local']"
    assert backend.requests


def test_unmapped_local_tier_falls_back_honestly(monkeypatch):
    resolver_calls, agent_kwargs = _fake_agent_runtime(monkeypatch)
    role = _role("local")

    role_runner._native_role_agent(
        role, role_runner.build_turn_request(role), config=_config({}))

    assert resolver_calls == {"requested": "active-prov",
                              "target_model": "active-model"}
    assert agent_kwargs["model"] == "active-model"

    monkeypatch.setattr(role_runner, "get_role", lambda _id: role)
    result = role_runner.run_role(
        "demo", "hi", registry=_Registry({"native": _FakeBackend([_done()])}),
        config=_config({}))

    assert result["ok"] is True
    assert result["model_tier_applied"] is False
    assert "no roles.model_tiers entry for tier 'local'" in result["model_tier_note"]
    assert "using the active model" in result["model_tier_note"]


@pytest.mark.parametrize("tier", ["api", ""])
def test_api_and_empty_tiers_use_the_active_model(monkeypatch, tier):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role(tier))
    result = role_runner.run_role(
        "demo", "hi", registry=_Registry({"native": _FakeBackend([_done()])}),
        config=_config({}))

    assert result["model_tier"] == tier
    assert result["model_tier_applied"] is True
    assert f"{tier or 'empty'} tier uses the active model" in result["model_tier_note"]


def test_string_profile_runs_on_the_active_provider(monkeypatch):
    resolver_calls, agent_kwargs = _fake_agent_runtime(monkeypatch)
    role = _role("detail")

    role_runner._native_role_agent(
        role, role_runner.build_turn_request(role),
        config=_config({"detail": "tier-model-x"}))

    assert resolver_calls == {"requested": "active-prov",
                              "target_model": "tier-model-x"}
    assert agent_kwargs["model"] == "tier-model-x"


def test_cli_override_wins_over_the_tier_map(monkeypatch):
    resolver_calls, agent_kwargs = _fake_agent_runtime(monkeypatch)
    role = _role("local")

    role_runner._native_role_agent(
        role, role_runner.build_turn_request(role),
        config=_config({"local": {"provider": "tier-prov",
                                  "model": "tier-model"}}),
        model_override="cli-model", provider_override="cli-prov")

    assert resolver_calls == {"requested": "cli-prov",
                              "target_model": "cli-model"}
    assert agent_kwargs["model"] == "cli-model"

    monkeypatch.setattr(role_runner, "get_role", lambda _id: role)
    result = role_runner.run_role(
        "demo", "hi", registry=_Registry({"native": _FakeBackend([_done()])}),
        config=_config({"local": {"provider": "tier-prov",
                                  "model": "tier-model"}}),
        model="cli-model", provider="cli-prov")

    assert result["model_tier_applied"] is True
    assert "override" in result["model_tier_note"]


def test_malformed_entry_is_unmapped_and_never_fails(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    result = role_runner.run_role(
        "demo", "hi", registry=_Registry({"native": _FakeBackend([_done()])}),
        config=_config({"local": 123}))

    assert result["ok"] is True
    assert result["model_tier_applied"] is False


def test_no_map_never_opens_a_socket(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())

    def _boom(*args, **kwargs):
        raise AssertionError("a role turn without a map must not open a socket")

    monkeypatch.setattr(socket, "socket", _boom)

    result = role_runner.run_role(
        "demo", "hi", registry=_Registry({"native": _FakeBackend([_done("fine")])}),
        config=_config({}))

    assert result["ok"] is True
    assert result["text"] == "fine"


def test_recipe_role_reports_that_it_owns_its_model(monkeypatch):
    from types import SimpleNamespace

    role = _role("local", system_prompt="", recipe_id="morning_digest")
    monkeypatch.setattr(role_runner, "get_role", lambda _id: role)

    class _Spec:
        id = "morning_digest"
        title = "Morning digest"

    class _RecipeRegistry:
        def get(self, name):
            return _Spec()

    def fake_recipe_runner(spec, **kwargs):
        return SimpleNamespace(
            brief="Good morning.", terminal="done", refusal="", hint="",
            failed=False, cancelled=False, sections=(), events=())

    result = role_runner.run_role(
        "digest", "", recipe_registry=_RecipeRegistry(),
        recipe_runner=fake_recipe_runner)

    assert result["model_tier_applied"] is False
    assert "recipe runner" in result["model_tier_note"]


def test_cli_parser_accepts_model_and_provider():
    from qubit_cli.subcommands.agent import build_agent_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    build_agent_parser(sub, cmd_agent=lambda args: 0)

    args = parser.parse_args([
        "agent", "run", "orchestrator", "--model", "m2",
        "--provider", "p2", "--json"])

    assert args.model == "m2"
    assert args.provider == "p2"
    assert not hasattr(args, "engine")  # not passed -> runner default


def test_cli_forwards_overrides_and_keeps_tier_fields(monkeypatch):
    from qubit_cli import agent_cmd

    monkeypatch.setattr(agent_cmd, "_get_role", lambda _id: _role())
    seen = {}

    def fake_runner(role_id, prompt, **kwargs):
        seen.update(kwargs)
        return {
            "ok": True, "role": role_id, "engine": "native", "text": "hello",
            "model": "cli-model", "provider": "cli-prov",
            "model_tier": "local", "model_tier_applied": True,
            "model_tier_note": "CLI --provider/--model override",
        }

    args = argparse.Namespace(
        agent_action="run", role="demo", prompt="hi", engine="native",
        model="cli-model", provider="cli-prov", json=True)

    out = io.StringIO()
    with redirect_stdout(out):
        assert agent_cmd.cmd_agent(args, runner=fake_runner) == 0

    assert seen == {"engine": "native", "model": "cli-model",
                    "provider": "cli-prov"}
    payload = json.loads(out.getvalue())
    assert payload["model"] == "cli-model"
    assert payload["provider"] == "cli-prov"
    assert payload["model_tier"] == "local"
    assert payload["model_tier_applied"] is True
    assert payload["model_tier_note"] == "CLI --provider/--model override"


def test_cli_omits_unset_overrides(monkeypatch):
    from qubit_cli import agent_cmd

    monkeypatch.setattr(agent_cmd, "_get_role", lambda _id: _role())
    seen = {}

    def fake_runner(role_id, prompt, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "role": role_id, "engine": "native", "text": "hi"}

    args = argparse.Namespace(
        agent_action="run", role="demo", prompt="hi", engine="native",
        model="", provider="", json=True)

    with redirect_stdout(io.StringIO()):
        assert agent_cmd.cmd_agent(args, runner=fake_runner) == 0

    assert seen == {"engine": "native"}
