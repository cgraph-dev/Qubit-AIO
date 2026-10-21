from __future__ import annotations

import subprocess
import pytest


def _make_task(kb, *, assignee: str):
    return kb.Task(
        id="t_spawn_tools",
        title="spawn tools",
        body=None,
        assignee=assignee,
        status="running",
        priority=0,
        created_by="test",
        created_at=1,
        started_at=None,
        completed_at=None,
        workspace_kind="dir",
        workspace_path=None,
        claim_lock="lock",
        claim_expires=None,
        tenant=None,
        current_run_id=7,
    )


def test_default_spawn_pins_assignee_profile_cli_toolsets(monkeypatch, tmp_path):
    """Manual profile assignment should keep that profile's CLI tools.

    Regression guard for dispatcher-spawned workers that boot with
    HERMES_KANBAN_TASK: the worker must not collapse to only kanban lifecycle
    tools when the assigned profile's top-level ``toolsets`` is the default
    composite. The spawned CLI gets an explicit --toolsets pin resolved from
    platform_toolsets.cli; model_tools appends task-scoped kanban tools later.
    """
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "elias"
    profile.mkdir(parents=True)
    profile.joinpath("config.yaml").write_text(
        """
platform_toolsets:
  cli:
    - clarify
    - code_execution
    - delegation
    - file
    - memory
    - session_search
    - skills
    - terminal
    - web
toolsets:
  - hermes-cli
agent:
  disabled_toolsets: []
""".lstrip(),
        encoding="utf-8",
    )
    root.joinpath("config.yaml").write_text("toolsets:\n  - kanban\n", encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(root))

    from qubit_cli import kanban_db as kb
    from qubit_cli import kanban_db_dispatch as kbd

    monkeypatch.setattr(kbd, "_resolve_qubit_argv", lambda: ["hermes"])

    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env") or {})
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pid = kbd._default_spawn(_make_task(kb, assignee="elias"), str(workspace))

    assert pid == 4242
    assert captured["env"]["QUBIT_HOME"] == str(profile)
    assert captured["env"]["HERMES_KANBAN_TASK"] == "t_spawn_tools"
    assert "--toolsets" in captured["cmd"]
    pinned = captured["cmd"][captured["cmd"].index("--toolsets") + 1].split(",")
    for required in ("terminal", "web", "file", "skills", "code_execution", "delegation"):
        assert required in pinned


def test_default_spawn_model_override_survives_real_cli_parse(monkeypatch, tmp_path):
    """The dispatcher's pre-``chat`` model flag must reach ``args.model``.

    This is an integration contract between Kanban's worker argv builder and
    the real CLI parser. A parser default once erased the explicit override,
    silently sending the worker to its profile default or fallback instead.
    """
    root = tmp_path / ".hermes"
    (root / "profiles" / "elias").mkdir(parents=True)
    root.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(root))

    from qubit_cli import kanban_db as kb
    from qubit_cli import kanban_db_dispatch as kbd
    from qubit_cli._parser import build_top_level_parser

    monkeypatch.setattr(kbd, "_resolve_qubit_argv", lambda: ["hermes"])
    captured = {}

    class FakeProc:
        pid = 4244

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = _make_task(kb, assignee="elias")
    task.model_override = "gpt-5.6-sol"
    kbd._default_spawn(task, str(workspace))

    parser, _subparsers, _chat_parser = build_top_level_parser()
    # Profile selection is attached by the outer CLI bootstrap rather than
    # build_top_level_parser(); remove that already-validated prefix and parse
    # the worker flags/subcommand through the real shared parser.
    assert captured["cmd"][1:3] == ["-p", "elias"]
    args = parser.parse_args(captured["cmd"][3:])

    assert args.command == "chat"
    assert args.model == "gpt-5.6-sol"
    assert args.query == "work kanban task t_spawn_tools"


def test_default_spawn_resolves_env_passthrough_under_multiplex(monkeypatch, tmp_path):
    """Under multiplex a worker spawn with ``terminal.env_passthrough`` configured must
    forward the ASSIGNEE profile's own value, never crash on an unscoped read or leak the
    dispatcher's ambient os.environ (#109494).
    """
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "elias"
    profile.mkdir(parents=True)
    root.joinpath("config.yaml").write_text(
        "terminal:\n  env_passthrough:\n    - MY_PASSTHROUGH_VAR\n", encoding="utf-8")
    profile.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    profile.joinpath(".env").write_text("MY_PASSTHROUGH_VAR=elias-value\n", encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(root))
    monkeypatch.setenv("MY_PASSTHROUGH_VAR", "dispatcher-value")

    from agent.secret_scope import set_multiplex_active
    from qubit_cli import kanban_db as kb
    from qubit_cli import kanban_db_dispatch as kbd

    monkeypatch.setattr(kbd, "_resolve_qubit_argv", lambda: ["hermes"])

    captured = {}

    class FakeProc:
        pid = 4243

    def fake_popen(cmd, *args, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    set_multiplex_active(True)
    try:
        pid = kbd._default_spawn(_make_task(kb, assignee="elias"), str(workspace))
    finally:
        set_multiplex_active(False)

    assert pid == 4243
    # The assignee's own scoped value, not the dispatcher's ambient os.environ one.
    assert captured["env"].get("MY_PASSTHROUGH_VAR") == "elias-value"


def test_resolve_worker_cli_toolsets_uses_profile_home_not_parent_config(monkeypatch, tmp_path):
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "elias"
    profile.mkdir(parents=True)
    root.joinpath("config.yaml").write_text("platform_toolsets:\n  cli:\n    - kanban\n", encoding="utf-8")
    profile.joinpath("config.yaml").write_text(
        """
platform_toolsets:
  cli:
    - terminal
    - web
toolsets:
  - hermes-cli
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUBIT_HOME", str(root))

    from qubit_cli import kanban_db as kb
    from qubit_cli import kanban_db_dispatch as kbd

    resolved = kbd._resolve_worker_cli_toolsets(str(profile))

    assert resolved is not None
    assert "terminal" in resolved
    assert "web" in resolved
    # Opt-in is no longer inferred for ordinary chats. The dispatcher-owned
    # worker gets lifecycle tools at schema assembly, independently of the
    # assignee's saved chat selection.
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_spawn_tools")
    from model_tools import get_tool_definitions
    names = {t["function"]["name"] for t in get_tool_definitions(resolved, quiet_mode=True, skip_tool_search_assembly=True)}
    assert "kanban_complete" in names
    assert "kanban_list" not in names
    assert resolved != ["kanban"]


@pytest.mark.xfail(strict=True, reason="wave-20 parity defect: default kanban spawn passes mutable launch overrides")
def test_default_spawn_does_not_pass_mutable_launch_overrides(monkeypatch, tmp_path):
    """Owner policy reads overrides from the claimed card, not executable argv."""
    root = tmp_path / ".qubit"
    (root / "profiles" / "elias").mkdir(parents=True)
    root.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(root))

    from qubit_cli import kanban_db as kb
    from qubit_cli import kanban_db_dispatch as kbd

    monkeypatch.setattr(kbd, "_resolve_qubit_argv", lambda: ["qubit"])
    captured = {}

    class FakeProc:
        pid = 4244

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = _make_task(kb, assignee="elias")
    task.model_override = "gpt-5.6-sol"
    kbd._default_spawn(task, str(workspace))

    # Mutable model strings are not launch authority; the owner reads the claimed card.
    assert captured["cmd"][-2:] == ["-m", "qubit_cli.kanban_worker_client"]
    assert task.model_override not in captured["cmd"]

def test_worker_toolsets_come_from_the_assignee_profile_not_the_parent_config(tmp_path, monkeypatch):
    """The worker's toolsets are frozen by the assignee profile's own gateway (its config), never
    by the dispatcher's root/active profile: a root config that only lists ``kanban`` must not
    reach a worker whose profile grants ``terminal`` and ``web``."""
    import json
    from contextlib import closing
    from types import SimpleNamespace

    from qubit_cli import kanban_db as kb
    from qubit_cli.kanban_db_connect import connect
    from gateway.session_contract import Principal
    from gateway.session_kanban import build_kanban_policy

    profile = tmp_path / "profiles" / "elias"
    profile.mkdir(parents=True)
    monkeypatch.setenv("QUBIT_KANBAN_HOME", str(tmp_path))
    monkeypatch.setenv("QUBIT_HOME", str(profile))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("QUBIT_KANBAN_DB", raising=False)
    monkeypatch.setattr("qubit_cli.profiles.resolve_profile_env", lambda name: str(profile))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    kb.create_board("owned")  # explicit creation; connect() never creates a board (#43243)
    with closing(connect(board="owned")) as conn:
        task_id = kb.create_task(conn, title="t", body="b", assignee="elias",
                                 workspace_kind="dir", workspace_path=str(workspace))
        kb.recompute_ready(conn)
        task = kb.claim_task(conn, task_id)
    actor = Principal("owner", str(profile), frozenset({"session:create"}), "transport")
    connection = SimpleNamespace(authority=SimpleNamespace(profile_id=str(profile)), actor=actor, native_owner=True)
    params = dict(board="owned", task_id=task.id, run_id=task.current_run_id, claim_lock=task.claim_lock)

    assignee_config = {"model": {"default": "m", "provider": "custom"},
                       "platform_toolsets": {"cli": ["terminal", "web"]}, "toolsets": ["qubit-cli"]}
    policy, _ = build_kanban_policy(connection, params, assignee_config)
    assert {"terminal", "web", "kanban"} <= set(policy.toolsets)

    root_only_kanban = {"model": {"default": "m", "provider": "custom"}, "platform_toolsets": {"cli": ["kanban"]}}
    other_profile = tmp_path
    with pytest.raises(Exception):
        # A dispatcher acting for another profile cannot mint this worker's policy at all.
        build_kanban_policy(SimpleNamespace(authority=SimpleNamespace(profile_id=str(other_profile)),
                                            actor=actor, native_owner=True), params, root_only_kanban)
    assert json.loads(policy.kanban_json)["profile"] == "elias"
