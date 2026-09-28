"""Qubit editor integration via a bounded brokered adapter.

Proves editor checks are read-only, setup never writes live config, serve
mode and hook auto-approval are refused, LSP inventory never executes or
downloads, and installs are refused (pinned-source policy).
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from contextlib import redirect_stdout


def test_acp_check_readonly_shape():
    from voicebridge import acp_lsp as AL

    out = AL.acp_check()
    assert out["schema"] == "qubit-acp-check/1"
    assert out["server"] == "ABSENT"
    assert {e["editor"] for e in out["editors"]} == {"vscode", "zed", "jetbrains"}
    assert all(isinstance(e["config_present"], bool) for e in out["editors"])


def test_acp_setup_prints_never_writes(tmp_path, monkeypatch):
    from voicebridge import acp_lsp as AL

    def _no_write(*a, **k):
        raise AssertionError("setup must never write")

    monkeypatch.setattr("pathlib.Path.write_text", _no_write)
    out = AL.acp_setup_snippet("vscode")
    assert out["state"] == "OK" and "snippet" in out
    assert "paste it yourself" in out["handoff"]
    out = AL.acp_setup_snippet("emacs")
    assert out["state"] == "UNKNOWN" and out["code"] == "ACP_UNKNOWN_EDITOR"


def test_acp_serve_and_hooks_refused():
    from voicebridge import acp_lsp as AL

    out = AL.acp_refuse_serve()
    assert out["code"] == "ACP_NO_SERVER" and out["owner"] == "qubit-policy"
    out = AL.acp_refuse_accept_hooks()
    assert out["state"] == "REJECTED" and out["code"] == "ACP_ACCEPT_HOOKS_REFUSED"


def test_lsp_inventory_never_executes(monkeypatch):
    from voicebridge import acp_lsp as AL

    def _no_spawn(*a, **k):
        raise AssertionError("inventory must use PATH only, never spawn")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    out = AL.lsp_list()
    assert out["layer"] == "ABSENT"
    assert len(out["servers"]) >= 10
    for server in out["servers"]:
        assert set(server) >= {"server", "extensions", "present", "managed"}
    status = AL.lsp_status()
    assert status["active_clients"] == "none"
    assert status["known_servers"] == len(out["servers"])


def test_lsp_which_present_and_unknown(monkeypatch):
    from voicebridge import acp_lsp as AL

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    out = AL.lsp_which("gopls")
    assert out["state"] == "PRESENT" and out["path"] == "/usr/bin/gopls"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert AL.lsp_which("gopls")["state"] == "ABSENT"
    out = AL.lsp_which("cobol-ls-9000")
    assert out["state"] == "UNKNOWN" and out["code"] == "LSP_UNKNOWN_SERVER"


def test_lsp_restart_and_install():
    from voicebridge import acp_lsp as AL

    assert AL.lsp_restart()["state"] == "NO_CLIENTS"
    out = AL.lsp_refuse_install("gopls")
    assert out["code"] == "LSP_NO_DOWNLOADS"
    assert "gopls" in json.dumps(out)


def test_cli_wiring():
    from voicebridge import cli as CLI

    class _A:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    cases = [
        (CLI._cmd_acp, _A(check=True), 0),
        (CLI._cmd_acp, _A(check=False, setup=True, setup_browser=False,
                           editor="zed"), 0),
        (CLI._cmd_acp, _A(check=False, setup=False, setup_browser=True), 0),
        (CLI._cmd_acp, _A(check=False, setup=False, setup_browser=False,
                           version=False), 2),
        (CLI._cmd_acp, _A(check=False, setup=False, setup_browser=False,
                           version=False, accept_hooks=True), 2),
        (CLI._cmd_lsp, _A(lsp_command="status"), 0),
        (CLI._cmd_lsp, _A(lsp_command="list"), 0),
        (CLI._cmd_lsp, _A(lsp_command="which", server="gopls"), 0),
        (CLI._cmd_lsp, _A(lsp_command="restart"), 0),
        (CLI._cmd_lsp, _A(lsp_command="install", server="gopls"), 2),
        (CLI._cmd_lsp, _A(lsp_command="install-all", server=""), 2),
    ]
    for fn, args, rc in cases:
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert fn(None, args) == rc, (fn, args.__dict__)
        json.loads(buf.getvalue())
    text = CLI.build_parser().format_help()
    assert "acp" in text and "lsp" in text


def test_capability_catalog_reports_acp_lsp_implemented():
    from voicebridge.capability_catalog import check

    for token in ("acp", "lsp"):
        out = check(token)
        assert out["state"] == "IMPLEMENTED", (token, out)


def test_voice_backend_acp_refusals_name_their_codes():
    from voicebridge import acp_lsp as AL
    from voicebridge import voice_commands as VC

    serve = VC._b_acp(None, {"action": "serve"})["refused"]
    assert serve == AL.acp_refuse_serve() == AL.acp_serve_refusal()
    assert serve["state"] == "REFUSED" and serve["code"] == "ACP_NO_SERVER"

    hooks = VC._b_acp(None, {"action": "accept_hooks"})["refused"]
    assert hooks == AL.acp_refuse_accept_hooks()
    assert hooks == AL.acp_accept_hooks_refusal()
    assert hooks["state"] == "REJECTED"
    assert hooks["code"] == "ACP_ACCEPT_HOOKS_REFUSED"

    setup = VC._b_acp(None, {"action": "setup", "editor": "zed"})["setup"]
    assert setup["state"] == "OK" and setup["editor"] == "zed"
    assert "ws://127.0.0.1:18789" in setup["snippet"]


def test_voice_backend_lsp_install_and_restart_refusals():
    from voicebridge import acp_lsp as AL
    from voicebridge import voice_commands as VC

    refused = VC._b_lsp(None, {"action": "install", "server": "gopls"})["refused"]
    assert refused == AL.lsp_refuse_install("gopls")
    assert refused["code"] == "LSP_NO_DOWNLOADS" and "gopls" in json.dumps(refused)

    assert VC._b_lsp(None, {"action": "restart"}) == AL.lsp_restart()
    assert VC._b_lsp(None, {"action": "which", "server": "gopls"}) == \
        AL.lsp_which("gopls")


def test_dashboard_extra_acp_and_lsp_refusals():
    from voicebridge import acp_lsp as AL
    from voicebridge import dashboard_ops as DO

    assert DO._extra_acp_serve(None, {}, "dashboard:user") == AL.acp_refuse_serve()
    assert DO._extra_acp_accept_hooks(None, {}, "dashboard:user") == \
        AL.acp_refuse_accept_hooks()
    assert DO._extra_lsp_install(None, {"server": "gopls"}, "dashboard:user") == \
        AL.lsp_refuse_install("gopls")
