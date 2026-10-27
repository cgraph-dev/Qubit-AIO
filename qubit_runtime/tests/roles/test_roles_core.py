"""Core role registry, runner, CLI and voice-bridge contracts.

Everything here is hermetic: fake backends/factories, no model and no network.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.runtime_port import (  # noqa: E402
    RuntimeCapabilities,
    TurnEvent,
    UnknownRuntimeError,
)
from roles import runner as role_runner  # noqa: E402
from roles.base import AgentRole  # noqa: E402
from roles.registry import (  # noqa: E402
    DuplicateRoleError,
    UnknownRoleError,
    discover_roles,
    discover_module_names,
    get_role,
    list_roles,
)


def _role(role_id: str = "demo", **kw) -> AgentRole:
    data = dict(
        id=role_id,
        title=f"{role_id.title()} role",
        description=f"the {role_id} posture",
        system_prompt="You are the demo role.",
        toolsets=("research",),
        voice_aliases=(f"run the {role_id} role",),
        source="test",
    )
    data.update(kw)
    return AgentRole(**data)


def _module(role: AgentRole):
    return SimpleNamespace(ROLE=role, __name__=f"fake_{role.id}")


class _FakeBackend:
    name = "native"
    capabilities = RuntimeCapabilities(True, True, True, False)
    supports_roles = False

    def __init__(self, events):
        self.events = events
        self.requests = []
        self.ran = False

    def run_turn(self, request, cancel=None):
        self.ran = True
        self.requests.append(request)
        yield from self.events


class _Registry:
    def __init__(self, backends):
        self._backends = backends
        self.names = []

    def get(self, name):
        self.names.append(name)
        if name not in self._backends:
            raise UnknownRuntimeError(name, sorted(self._backends))
        return self._backends[name]


# ------------------------------------------------------------------ registry
def test_discovery_is_sorted_and_has_no_central_list():
    roles = discover_roles([_module(_role("zeta")), _module(_role("alpha"))])
    assert [role.id for role in roles] == ["alpha", "zeta"]
    assert [role.id for role in list_roles()] == sorted(
        role.id for role in list_roles())


def test_discovery_excludes_core_modules():
    names = discover_module_names()
    assert not {"base", "registry", "runner", "__init__"} & set(names)
    assert all(not name.startswith("_") for name in names)


def test_duplicate_ids_are_typed_and_do_not_silently_pick_one():
    with pytest.raises(DuplicateRoleError):
        discover_roles([_module(_role("dup")), _module(_role("dup"))])


def test_unknown_role_is_typed(monkeypatch):
    monkeypatch.setattr(
        "roles.registry.list_roles", lambda: [_role("known")])
    with pytest.raises(UnknownRoleError) as exc:
        get_role("missing")
    assert exc.value.role_id == "missing"
    assert exc.value.available == ["known"]


def test_get_role_reads_the_auto_discovered_set(monkeypatch):
    monkeypatch.setattr("roles.registry.list_roles", lambda: [_role("known")])
    assert get_role("known").id == "known"
    assert get_role(" known ").id == "known"


def test_non_agent_role_value_is_rejected():
    with pytest.raises(TypeError):
        discover_roles([SimpleNamespace(ROLE="not-a-role")])


# -------------------------------------------------------------------- runner
def _done(text="Role answer.", **data):
    payload = {"completed": True, **data}
    return TurnEvent("done", text=text, data=payload)


def test_run_role_native_turn_carries_system_prompt_and_default_prompt(monkeypatch):
    role = _role()
    monkeypatch.setattr(role_runner, "get_role", lambda _id: role)
    backend = _FakeBackend([_done(model="m1", provider="p1")])

    result = role_runner.run_role(
        "demo", "", registry=_Registry({"native": backend}))

    assert result["ok"] is True
    assert result["role"] == "demo"
    assert result["engine"] == "native"
    assert result["text"] == "Role answer."
    assert result["model"] == "m1"
    assert result["provider"] == "p1"
    assert result["elapsed_ms"] >= 0
    request = backend.requests[0]
    assert request.system == role.system_prompt
    assert request.prompt == role_runner._DEFAULT_PROMPT


def test_json_mode_appends_a_json_instruction(monkeypatch):
    role = _role()
    monkeypatch.setattr(role_runner, "get_role", lambda _id: role)
    backend = _FakeBackend([_done("{}")])

    role_runner.run_role("demo", "hi", json_mode=True,
                         registry=_Registry({"native": backend}))

    system = backend.requests[0].system
    assert role.system_prompt in system
    assert "JSON" in system


def test_typed_refusal_from_a_turn_is_preserved(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    backend = _FakeBackend([
        TurnEvent("error", text="cannot", data={
            "refusal": "provider_unavailable", "hint": "set a provider"})])

    result = role_runner.run_role(
        "demo", "hi", registry=_Registry({"native": backend}))

    assert result["ok"] is False
    assert result["refusal"] == "provider_unavailable"
    assert result["hint"] == "set a provider"
    assert result["failed"] is False


def test_failed_and_cancelled_turns_are_distinguished(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    failed = role_runner.run_role(
        "demo", "hi",
        registry=_Registry({"native": _FakeBackend([
            TurnEvent("error", text="boom")])}))
    assert failed["failed"] is True and failed["cancelled"] is False

    cancelled = role_runner.run_role(
        "demo", "hi",
        registry=_Registry({"native": _FakeBackend([
            TurnEvent("error", text="stop", data={"cancelled": True})])}))
    assert cancelled["cancelled"] is True and cancelled["failed"] is False


def test_empty_done_is_not_ok(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    result = role_runner.run_role(
        "demo", "hi",
        registry=_Registry({"native": _FakeBackend([_done("")])}))
    assert result["ok"] is False


def test_unknown_engine_is_typed(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    with pytest.raises(UnknownRuntimeError) as exc:
        role_runner.run_role(
            "demo", "hi", engine="bogus", registry=_Registry({}))
    assert exc.value.name == "bogus"


def test_non_native_engine_refuses_instead_of_dropping_the_role(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    backend = _FakeBackend([_done("should not run")])

    result = role_runner.run_role(
        "demo", "hi", engine="openjarvis",
        registry=_Registry({"openjarvis": backend}))

    assert result["ok"] is False
    assert result["refusal"] == "role_engine_unsupported"
    assert backend.ran is False


def test_non_native_engine_that_advertises_role_support_may_run(monkeypatch):
    monkeypatch.setattr(role_runner, "get_role", lambda _id: _role())
    backend = _FakeBackend([_done("honoured")])
    backend.supports_roles = True

    result = role_runner.run_role(
        "demo", "hi", engine="openjarvis",
        registry=_Registry({"openjarvis": backend}))

    assert result["ok"] is True
    assert result["text"] == "honoured"
    assert result["model_tier"] == "local"
    assert backend.ran is True


def test_native_factory_passes_the_role_toolsets(monkeypatch):
    role = _role(toolsets=("research", "web"))
    captured = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.model = kwargs.get("model")
            self.provider = kwargs.get("provider")

    monkeypatch.setattr("qubit_cli.config.load_config",
                        lambda: {"model": {"provider": "local", "default": "m"}})
    monkeypatch.setattr(
        "qubit_cli.runtime_provider.resolve_runtime_provider",
        lambda **kw: {"provider": "local", "model": "m", "api_key": "k",
                      "base_url": "http://localhost"})
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)

    box = {}
    request = role_runner.build_turn_request(role, "hi")
    agent = role_runner._native_role_agent(role, request, box)

    assert captured["enabled_toolsets"] == ["research", "web"]
    assert box["agent"] is agent


def test_empty_toolsets_use_the_runtime_default(monkeypatch):
    role = _role(toolsets=())
    captured = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("qubit_cli.config.load_config",
                        lambda: {"model": {"provider": "local", "default": "m"}})
    monkeypatch.setattr(
        "qubit_cli.runtime_provider.resolve_runtime_provider",
        lambda **kw: {"provider": "local", "model": "m", "api_key": "k",
                      "base_url": "http://localhost"})
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)

    role_runner._native_role_agent(role, role_runner.build_turn_request(role))
    assert captured["enabled_toolsets"] is None


def test_recipe_role_dispatches_the_existing_runner_without_a_duplicate_prompt(monkeypatch):
    role = _role("digest", system_prompt="", recipe_id="morning_digest")
    monkeypatch.setattr(role_runner, "get_role", lambda _id: role)
    seen = {}

    class _Spec:
        id = "morning_digest"
        title = "Morning digest"

    class _Registry:
        def get(self, name):
            seen["spec"] = name
            return _Spec()

    def fake_recipe_runner(spec, **kwargs):
        seen["spec_obj"] = spec
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            brief="Good morning.", terminal="done", refusal="", hint="",
            failed=False, cancelled=False, sections=(), events=())

    result = role_runner.run_role(
        "digest", "ignored prompt", recipe_registry=_Registry(),
        recipe_runner=fake_recipe_runner)

    assert result["ok"] is True
    assert result["recipe"] == "morning_digest"
    assert result["text"] == "Good morning."
    assert seen["spec"] == "morning_digest"
    assert "prompt" not in seen["kwargs"]


# ----------------------------------------------------------------------- CLI
def _args(**kw):
    base = dict(agent_action="run", role="demo", prompt="hi",
                engine="native", json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_roles_json(monkeypatch):
    from qubit_cli import agent_cmd

    out = io.StringIO()
    with redirect_stdout(out):
        assert agent_cmd.cmd_agent(
            _args(agent_action="roles", json=True), roles=[_role()]) == 0
    payload = json.loads(out.getvalue())
    assert [row["id"] for row in payload] == ["demo"]
    assert payload[0]["voice_aliases"] == ["run the demo role"]


def test_cli_run_json(monkeypatch):
    from qubit_cli import agent_cmd

    monkeypatch.setattr(agent_cmd, "_get_role", lambda _id: _role())
    seen = {}

    def fake_runner(role_id, prompt, *, engine):
        seen.update(role=role_id, prompt=prompt, engine=engine)
        return {"ok": True, "role": role_id, "engine": engine,
                "text": "hello", "model": "m", "provider": "p",
                "elapsed_ms": 1.2}

    out = io.StringIO()
    with redirect_stdout(out):
        assert agent_cmd.cmd_agent(
            _args(json=True), runner=fake_runner) == 0
    payload = json.loads(out.getvalue())
    assert payload["text"] == "hello"
    assert payload["title"] == "Demo role"
    assert seen == {"role": "demo", "prompt": "hi", "engine": "native"}


def test_cli_unknown_role_and_engine_exit_two(monkeypatch):
    from qubit_cli import agent_cmd

    def missing(_id):
        raise UnknownRoleError(_id, [])

    monkeypatch.setattr(agent_cmd, "_get_role", missing)
    err = io.StringIO()
    with redirect_stderr(err):
        assert agent_cmd.cmd_agent(_args()) == 2
    assert "Unknown agent role" in err.getvalue()

    monkeypatch.setattr(agent_cmd, "_get_role", lambda _id: _role())

    def unknown_engine(role_id, prompt, *, engine):
        raise UnknownRuntimeError(engine, ["native"])

    err = io.StringIO()
    with redirect_stderr(err):
        assert agent_cmd.cmd_agent(
            _args(engine="bogus"), runner=unknown_engine) == 2
    assert "Unknown runtime engine" in err.getvalue()


def test_cli_exit_codes_for_refusal_failure_and_cancel(monkeypatch):
    from qubit_cli import agent_cmd

    monkeypatch.setattr(agent_cmd, "_get_role", lambda _id: _role())

    def result(**kw):
        base = {"ok": False, "role": "demo", "engine": "native", "text": ""}
        base.update(kw)
        return base

    with redirect_stderr(io.StringIO()):
        assert agent_cmd.cmd_agent(_args(), runner=lambda *a, **k: result(
            refusal="role_engine_unsupported", hint="use native")) == 2
        assert agent_cmd.cmd_agent(_args(), runner=lambda *a, **k: result(
            failed=True)) == 1
        assert agent_cmd.cmd_agent(_args(), runner=lambda *a, **k: result(
            cancelled=True)) == 130


def test_cli_parser_wires_agent_roles_and_run():
    from qubit_cli.subcommands.agent import build_agent_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    build_agent_parser(sub, cmd_agent=lambda args: 0)

    args = parser.parse_args(
        ["agent", "run", "orchestrator", "--prompt", "x", "--engine", "native",
         "--json"])
    assert args.command == "agent"
    assert args.agent_action == "run"
    assert args.role == "orchestrator"
    assert args.engine == "native"
    assert args.json is True

    listing = parser.parse_args(["agent", "roles", "--json"])
    assert listing.agent_action == "roles" and listing.json is True


# --------------------------------------------------------------------- voice
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    path = tmp_path / "voice.yaml"
    path.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(path)


@pytest.fixture
def voice_cleanup():
    from voicebridge import voice_commands as VC

    before = set(VC.CLI_BRIDGE_REGISTRY)
    had_backend = "agent_role" in VC._BACKENDS
    yield VC
    for entry_id in set(VC.CLI_BRIDGE_REGISTRY) - before:
        VC.CLI_BRIDGE_REGISTRY.pop(entry_id, None)
    if not had_backend:
        VC._BACKENDS.pop("agent_role", None)
        VC._SUMMARIES.pop("agent_role", None)


def test_voice_alias_resolves_and_calls_the_real_runner(
        tmp_path, monkeypatch, voice_cleanup):
    from voicebridge import role_bridges
    from voicebridge import voice_commands as VC

    role = _role("voicedemo", confirmation="auto")
    role_bridges.install(VC, roles=[role])
    monkeypatch.setattr("roles.registry.get_role", lambda _id: role)
    called = {}

    def fake_run(role_id, prompt, *, engine):
        called.update(role=role_id, prompt=prompt, engine=engine)
        return {"ok": True, "role": role_id, "engine": engine,
                "text": "Spoken role answer.", "model": "m", "provider": "p",
                "elapsed_ms": 2.0}

    monkeypatch.setattr("roles.runner.run_role", fake_run)

    resolution = VC.resolve("run the voicedemo role")
    assert resolution.kind == VC.MATCH
    assert resolution.entry.id == "role_voicedemo"

    ctx = VC.make_ctx(_cfg(tmp_path, monkeypatch), session="roles",
                      source="cli")
    outcome = VC.execute(ctx, resolution)

    assert outcome.kind == VC.EXECUTED, (outcome.kind, outcome.speech)
    assert "Spoken role answer" in outcome.speech
    assert outcome.receipt["result"]["agent_role"]["state"] == "OK"
    assert called == {"role": "voicedemo", "prompt": "", "engine": "native"}


def test_voice_preview_role_stages_a_confirmation_and_does_not_bypass_it(
        tmp_path, monkeypatch, voice_cleanup):
    from voicebridge import role_bridges
    from voicebridge import voice_commands as VC

    role = _role("guarded", confirmation="preview")
    role_bridges.install(VC, roles=[role])
    monkeypatch.setattr("roles.registry.get_role", lambda _id: role)
    ran = {"value": False}

    def fake_run(*args, **kwargs):
        ran["value"] = True
        return {"ok": True, "role": "guarded", "engine": "native",
                "text": "x"}

    monkeypatch.setattr("roles.runner.run_role", fake_run)

    ctx = VC.make_ctx(_cfg(tmp_path, monkeypatch), session="roles",
                      source="cli")
    outcome = VC.execute(ctx, VC.resolve("run the guarded role"))

    assert outcome.kind == VC.NEEDS_CONFIRM, (outcome.kind, outcome.speech)
    assert outcome.confirmation_id
    assert ran["value"] is False
