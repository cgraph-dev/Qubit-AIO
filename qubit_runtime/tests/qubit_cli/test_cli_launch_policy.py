"""The classic CLI's gateway launch policy and resume-refusal exit.

``_gateway_launch``/``_explicit_launch_skills`` feed the pin's ``session.create`` policy;
``_new_session_id`` routes ``/new`` through the gateway on a gateway store (the launch's
route flags stay with the previous session); ``_ensure_gateway_session`` opens the session
once and adopts the id it returns; ``exit_on_resume_refusal`` turns a refused resume into
main's ``Error:`` + exit 2.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from qubit_cli import cli_init_mixin
from qubit_cli.gateway_chat import exit_on_resume_refusal, resume_policy_error
from qubit_cli.gateway_client import GatewayClientError
from qubit_cli.gateway_session_store import GatewaySessionStore


def _store():
    store = GatewaySessionStore.__new__(GatewaySessionStore)
    store.connection = object()
    return store


def test_gateway_launch_maps_the_explicit_flags(monkeypatch):
    import cli
    import agent.skill_commands as skill_commands
    monkeypatch.setattr(cli, "CLI_CONFIG", {"skills": {"auto_load": []}})
    monkeypatch.setattr(skill_commands, "resolve_auto_load_skills", lambda config=None: ["auto"])
    obj = SimpleNamespace(
        model="m", _explicit_model_override=True, _explicit_provider="p",
        _explicit_api_key="k", _explicit_base_url="b", enabled_toolsets=["web"],
        _explicit_toolsets=True, _explicit_reasoning="high", _explicit_max_turns=5,
        _explicit_checkpoints=True, pass_session_id=True, ignore_rules=False,
        _explicit_run_budget=10.0, verbose=True, _preload_skills_requested=["auto", "typed"])
    assert cli_init_mixin._gateway_launch(obj) == {
        "model": "m", "provider": "p", "api_key": "k", "base_url": "b",
        "toolsets": ["web"], "reasoning": "high", "max_turns": 5, "checkpoints": True,
        "pass_session_id": True, "ignore_rules": None, "run_budget": 10.0,
        "verbose": True, "skills": ["typed"],
    }


def test_gateway_launch_leaves_untyped_defaults_to_the_gateway():
    obj = SimpleNamespace(model="config-model", _explicit_model_override=False,
                          _explicit_provider="auto", enabled_toolsets=["web"])
    launch = cli_init_mixin._gateway_launch(obj)
    assert launch["model"] is None and launch["provider"] is None
    assert launch["toolsets"] is None and launch["skills"] is None


def test_explicit_launch_skills_is_none_without_a_typed_pick():
    assert cli_init_mixin._explicit_launch_skills(SimpleNamespace(_preload_skills_requested=[])) is None


def test_new_session_id_off_the_gateway_is_the_local_mint(monkeypatch):
    now = datetime(2026, 10, 21, 12, 0, 0)
    monkeypatch.setattr(cli_init_mixin, "new_session_id", lambda stamp: "local-" + stamp.isoformat())
    obj = SimpleNamespace(_session_db=object(), _gateway_session_open=False)
    assert cli_init_mixin.CLIInitMixin._new_session_id(obj, now) == "local-" + now.isoformat()


def test_new_session_id_on_the_gateway_creates_without_the_route_flags():
    store, created = _store(), []
    store.create = lambda launch, record_cwd=True: created.append((launch, record_cwd)) or "gw-new"
    obj = SimpleNamespace(
        _session_db=store, _gateway_session_open=True, model="m",
        _explicit_model_override=True, _explicit_provider="p", _explicit_api_key="k",
        _explicit_base_url="b", enabled_toolsets=["web"], _explicit_toolsets=True)
    assert cli_init_mixin.CLIInitMixin._new_session_id(obj, datetime(2026, 10, 21)) == "gw-new"
    launch, record_cwd = created[0]
    assert not {"model", "provider", "api_key", "base_url"} & set(launch)
    assert launch["toolsets"] == ["web"] and record_cwd is False
    assert obj.session_id == "gw-new"


def test_ensure_gateway_session_opens_once_and_adopts_the_id(monkeypatch):
    store = _store()
    seen = {}

    def open_session(cli, launch):
        seen["launch"] = launch
        return "gw-sid"

    store.open_session = open_session
    obj = SimpleNamespace(_session_db=store, _gateway_session_open=False, _resumed=False,
                          session_id="local", model="m",
                          _write_terminal_breadcrumb=lambda: seen.setdefault("breadcrumb", True))
    import qubit_cli.gateway_tools as gateway_tools
    monkeypatch.setattr(cli_init_mixin, "_record_shell_hook_consent", lambda: None)
    monkeypatch.setattr(cli_init_mixin, "_adopt_gateway_toolsets",
                        lambda cli, store: seen.setdefault("adopted", True))
    monkeypatch.setattr(gateway_tools, "bind_session",
                        lambda cli, connection: seen.setdefault("bound", connection))
    cli_init_mixin.CLIInitMixin._ensure_gateway_session(obj)
    assert obj._gateway_session_open is True and obj.session_id == "gw-sid"
    assert seen["adopted"] is True and seen["bound"] is store.connection
    assert seen["breadcrumb"] is True and seen["launch"]["skills"] is None
    # Idempotent: a second call does not open another session.
    cli_init_mixin.CLIInitMixin._ensure_gateway_session(obj)


def test_ensure_gateway_session_leaves_an_in_process_store_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_init_mixin, "_record_shell_hook_consent", lambda: calls.append("consent"))
    obj = SimpleNamespace(_session_db=object(), _gateway_session_open=False)
    cli_init_mixin.CLIInitMixin._ensure_gateway_session(obj)
    assert calls == [] and obj._gateway_session_open is False


def test_exit_on_resume_refusal_is_error_and_exit_two(capsys):
    with pytest.raises(SystemExit) as caught:
        exit_on_resume_refusal(resume_policy_error(["model"]))
    assert caught.value.code == 2
    assert capsys.readouterr().err.startswith("Error: Resume retains gateway session policy")


def test_exit_on_resume_refusal_ignores_other_failures():
    assert exit_on_resume_refusal(GatewayClientError("boom")) is None
    assert exit_on_resume_refusal(ValueError("boom")) is None
