"""TERMINAL_LOCAL_PERSISTENT end to end on a real local bash, and through every creator.

On (the default) carries exported env vars between commands, off starts every command from the
login-time env. cwd carries in both modes; plain shell vars never carry. The switch rides on the
`local_config` shaper, which every creator (terminal tool, file tools, execute_code, lazy
`ensure_task_env`) goes through, and the local builder must actually hand it to the environment —
a builder that drops it silently reverts to the backend default.
"""

import json
from pathlib import Path

import pytest

from tools.environments.local import LocalEnvironment
from tools.terminal_tool import _get_env_config, _resolve_container_task_id, terminal_tool
from tools.terminal_tool_backends import _create_environment, _local_config_from_config
from tools.terminal_tool_lifecycle import _create_configured_env, cleanup_vm


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("setting, exported", [(None, "yes"), ("true", "yes"), ("false", "none")])
def test_local_persistent_controls_exports_but_cwd_always_carries(monkeypatch, tmp_path, setting, exported):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    if setting is None:
        monkeypatch.delenv("TERMINAL_LOCAL_PERSISTENT", raising=False)
    else:
        monkeypatch.setenv("TERMINAL_LOCAL_PERSISTENT", setting)
    task_id = f"local-persist-{setting}"
    (tmp_path / "sub").mkdir()

    def run(cmd):
        return json.loads(terminal_tool(cmd, task_id=task_id))["output"].strip()

    try:
        run("cd sub && export QUBIT_LP_EXPORT=yes; QUBIT_LP_PLAIN=yes")
        assert run("echo ${QUBIT_LP_EXPORT:-none} ${QUBIT_LP_PLAIN:-none}") == f"{exported} none"
        assert run("pwd -P") == str((tmp_path / "sub").resolve())
    finally:
        cleanup_vm(_resolve_container_task_id(task_id))


def test_config_default_is_on_and_reaches_the_environment(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("TERMINAL_LOCAL_PERSISTENT", raising=False)
    config = _get_env_config()
    assert config["local_persistent"] is True
    assert _local_config_from_config(config) == {"persistent": True}

    for value in (config["local_persistent"], False):
        env = _create_configured_env(
            {**config, "local_persistent": value}, "local", image="", cwd=str(tmp_path),
            timeout=30, task_id=f"local-persist-config-{value}", host_cwd=None,
        )
        assert isinstance(env, LocalEnvironment)
        try:
            assert env._persist_env is value
        finally:
            env.cleanup()


def test_the_builder_forwards_the_switch(tmp_path: Path):
    """`_create_environment` must not swallow `local_config` in its kwargs."""
    for value in (False, True):
        env = _create_environment("local", "", str(tmp_path), 30, local_config={"persistent": value})
        try:
            assert env._persist_env is value
        finally:
            env.cleanup()


def test_no_creator_hand_rolls_the_local_config():
    import inspect

    import tools.code_execution_tool as code_exec
    import tools.file_tools as file_tools
    import tools.terminal_tool as terminal_tool

    for module in (file_tools, code_exec):
        assert "local_persistent" not in inspect.getsource(module), (
            f"{module.__name__} hand-rolls the local config instead of going through _create_configured_env"
        )
    # The terminal tool owns the config key itself and must hand it over via the shared creator.
    assert 'plan.config.get("local_persistent"' not in inspect.getsource(terminal_tool)
