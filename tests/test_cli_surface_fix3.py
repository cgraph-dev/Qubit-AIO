"""Focused CLI-surface fixes: fleet units, install-gated `claw`, and `send` help.

These pin the concrete defects fixed on `fix3/cli` without touching a live
service or config:

* ``qubit restart --only all`` must name the real fourth active unit
  (``qubit-voice-typing.service``), and ``--only agent`` must not be advertised
  because no such unit exists.
* ``qubit claw`` moved out of core into the ``agent-importers`` plugin; it must
  not be advertised as a working core command and must return install guidance.
* ``qubit send``'s examples must name the working ``qubit agent send`` form,
  and an unknown ``--list`` platform is a usage error (exit 2), not a delivery
  failure (exit 1).
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _Cfg:
    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


# ── fleet restart ────────────────────────────────────────────────────────────
def test_fleet_all_names_typing_and_has_no_agent_unit():
    from voicebridge import gateway_supervisor as GS

    names, err = GS._fleet_selection("all")
    assert err is None
    assert names == [
        "qubit-voicebridge.service",
        "qubit-gateway.service",
        "qubit-voice-typing.service",
        "qubit-voice-orb.service",
    ]
    assert "qubit-voice-agent.service" not in names

    typing, typing_err = GS._fleet_selection("typing")
    assert typing_err is None and typing == ["qubit-voice-typing.service"]

    agent, agent_err = GS._fleet_selection("agent")
    assert agent is None and agent_err == GS.BAD_INPUT_CODE


def test_restart_parser_advertises_typing_not_agent():
    from voicebridge import cli

    parser = cli.build_parser()
    subparsers = next(a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction))
    restart = subparsers.choices["restart"]
    only = next(a for a in restart._actions if a.dest == "only")
    assert "typing" in only.choices
    assert "agent" not in only.choices


def test_restart_preview_names_the_typing_unit(capsys):
    from voicebridge import cli

    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    args = argparse.Namespace(only="all", confirm=False)
    rc = cli._cmd_restart(cfg, args)
    payload = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert payload["state"] == "CONFIRM_REQUIRED"
    assert "qubit-voice-typing.service" in payload["units"]


# ── install-gated claw ───────────────────────────────────────────────────────
def test_claw_is_not_advertised_as_a_core_family():
    from voicebridge import cli

    parser = cli.build_parser()
    subparsers = next(a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction))
    assert "claw" not in subparsers.choices
    assert "claw" not in parser.format_help().lower()


def test_claw_returns_install_guidance_instead_of_a_dead_forward():
    from voicebridge import cli_qubit_forward as HF

    route = HF.route(["claw"])
    assert route is not None and route.kind == "refused"
    assert route.code == "QUBIT_CLAW_PLUGIN_REQUIRED"
    assert "qubit plugins install agent-importers" in route.detail
    assert "qubit import-agent" in route.detail


# ── status hint ──────────────────────────────────────────────────────────────
def test_status_summary_hint_names_the_working_agent_status_form(monkeypatch, capsys):
    status = _import_worktree_module("qubit_cli.status")
    for name, value in (
        ("_render_header", lambda ctx: None),
        ("_load_ctx_config", lambda ctx: None),
        ("_configured_model_label", lambda cfg: "m"),
        ("_effective_provider_label", lambda: "p"),
        ("_authenticated_provider_names", lambda: []),
        ("_gateway_state", lambda: (True, "running")),
        ("_connected_platform_labels", lambda: []),
        ("_cron_summary", lambda: "0"),
    ):
        monkeypatch.setattr(status, name, value)

    status._render_summary(types.SimpleNamespace(config={}))
    out = capsys.readouterr().out
    assert "qubit agent status --full" in out
    assert "Run 'qubit status --full'" not in out


# ── send help + exit codes ───────────────────────────────────────────────────
def _import_worktree_module(name: str):
    """Import a module from the *worktree* runtime.

    The installed editable package data root can point at another checkout, so
    pin the worktree's ``qubit_runtime`` first and drop any already-imported
    ``qubit_cli`` modules before importing.
    """
    runtime_root = str(REPO_ROOT / "qubit_runtime")
    sys.path.insert(0, runtime_root)
    for mod_name in list(sys.modules):
        if mod_name == "qubit_cli" or mod_name.startswith("qubit_cli.") \
                or mod_name == "qubit_constants":
            sys.modules.pop(mod_name, None)
    return __import__(name, fromlist=["*"])


def _import_worktree_send_cmd():
    return _import_worktree_module("qubit_cli.send_cmd")


def _send_parser():
    send_cmd = _import_worktree_send_cmd()

    parser = argparse.ArgumentParser(prog="qubit")
    return send_cmd.register_send_subparser(parser.add_subparsers(dest="command"))


def test_send_help_examples_name_the_working_agent_form():
    help_text = _send_parser().format_help()
    assert "qubit agent send --to telegram" in help_text
    assert "qubit send send" not in help_text
    assert "Exit codes: 0 ok, 1 delivery/backend error, 2 usage error." in help_text


def test_send_list_unknown_platform_is_a_usage_error(monkeypatch):
    send_cmd = _import_worktree_send_cmd()

    fake_dir = types.ModuleType("gateway.channel_directory")
    fake_dir.load_directory = lambda: {
        "updated_at": None,
        "platforms": {"telegram": [{"id": "1", "name": "home"}]},
    }
    fake_dir.format_directory_for_display = lambda platforms=None: ""
    monkeypatch.setitem(sys.modules, "gateway.channel_directory", fake_dir)

    fake_cfg = types.ModuleType("gateway.config")
    fake_cfg.load_gateway_config = lambda: types.SimpleNamespace(
        get_connected_platforms=lambda: [])
    monkeypatch.setitem(sys.modules, "gateway.config", fake_cfg)

    rc = send_cmd._list_targets("nope", json_mode=False)
    assert rc == send_cmd._USAGE_EXIT == 2
