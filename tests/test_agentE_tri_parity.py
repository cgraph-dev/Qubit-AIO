"""Agent E — tri-surface parity audit (sessions/kanban read defaults).

Focused regression for the data mismatches found while auditing tri-parity:
the voice/dashboard backends defaulted ``sessions``, ``kanban_list`` and
``kanban_log`` to limits of 5/10/10 while the live CLI parser defaults to
20/50/50, so the same read returned different result sets per surface.
"""
from __future__ import annotations

import contextlib
import io
import json


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return p, Config.load(p)


def _cli(path, *argv):
    from voicebridge import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = cli.main([*argv, "--config", str(path)])
        except SystemExit as exc:
            rc = getattr(exc, "code", 0) or 0
    text = buf.getvalue().strip()
    try:
        return rc, json.loads(text) if text else None
    except ValueError:
        return rc, None


def _parser_default(family, sub, flag):
    """Default of ``flag`` on the live ``family sub`` CLI parser."""
    import argparse

    from voicebridge.cli import build_parser

    def _descend(node, name):
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction) \
                    and name in action.choices:
                return action.choices[name]
        raise AssertionError(f"no subparser {name!r}")

    node = _descend(build_parser(), family)
    if sub is not None:
        node = _descend(node, sub)
    for action in node._actions:
        if flag in action.option_strings:
            return action.default
    raise AssertionError((family, sub, flag))


def _ctx(cfg):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, actor="agentE", session="agentE", source="user")


def test_kanban_list_and_log_defaults_match_cli(tmp_path, monkeypatch):
    _, cfg = _env(tmp_path, monkeypatch)
    from voicebridge import kanban as KB
    from voicebridge import voice_commands as VC

    seen = {}

    def _spy(name):
        real = getattr(KB, name)

        def wrapper(*args, **kwargs):
            seen[name] = kwargs.get("limit")
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(KB, "list_tasks", _spy("list_tasks"))
    monkeypatch.setattr(KB, "log", _spy("log"))

    assert _parser_default("kanban", "list", "--limit") == KB.LIST_DEFAULT
    assert _parser_default("kanban", "log", "--limit") == KB.LIST_DEFAULT

    VC._b_kanban(_ctx(cfg), {"action": "list"})
    assert seen["list_tasks"] == KB.LIST_DEFAULT
    VC._b_kanban(_ctx(cfg), {"action": "log"})
    assert seen["log"] == KB.LIST_DEFAULT


def test_sessions_default_matches_cli(tmp_path, monkeypatch):
    _, cfg = _env(tmp_path, monkeypatch)
    from voicebridge import auth_status as AS
    from voicebridge import voice_commands as VC

    seen = {}
    real = AS.recent_sessions

    def spy(_cfg, limit=20):
        seen["limit"] = limit
        return real(_cfg, limit)

    monkeypatch.setattr(AS, "recent_sessions", spy)
    assert _parser_default("sessions", "list", "--limit") == 20
    VC._b_sessions(_ctx(cfg), {"action": "list"})
    assert seen["limit"] == 20


def test_three_surfaces_return_same_kanban_list(tmp_path, monkeypatch):
    path, cfg = _env(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    assert _cli(path, "kanban", "init")[0] == 0
    for i in range(12):
        rc, out = _cli(path, "kanban", "create", f"t{i}", "--confirm")
        assert rc == 0 and out["state"] == "OK", (rc, out)

    cli_tasks = _cli(path, "kanban", "list")[1]["tasks"]
    dash = OPS.run_op(cfg, "kanban_list", {}, actor="dashboard:user",
                      session="tri")
    said = VC.voice_say(cfg, "list tasks", actor="voice:user", session="tri",
                        source="user")
    dash_tasks = dash["receipt"]["result"]["tasks"]
    said_tasks = said["receipt"]["result"]["tasks"]

    assert len(cli_tasks) == 12, cli_tasks
    assert [t["id"] for t in cli_tasks] == [t["id"] for t in dash_tasks]
    assert [t["id"] for t in cli_tasks] == [t["id"] for t in said_tasks]
