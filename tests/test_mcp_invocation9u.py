"""9U — MCP read authority + fail-closed client invocation.

Every Qubit surface that reports configured MCP servers must resolve the ONE
canonical authority ``voicebridge.mcp_servers.list_servers`` (dashboard, CLI,
voice, the gateway MCP handlers, and the runtime inventory that backs the
``/chat`` and ``/qubit-chat`` SPA status). Client invocation of a configured
stdio/http server is *not* a Qubit capability: it resolves to a typed
``MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED`` refusal after the shared
confirmation + audit gate, and never connects, spawns, or leaks a credential.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch, config_body: str = "logging:\n  jsonl: false\n"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text(config_body)
    return C.Config.load(p)


def _seed(cfg):
    from voicebridge import mcp_servers as MS

    MS.upsert(cfg, "alpha", {"transport": "stdio", "command": "alpha-mcp",
                             "args": ["--x"],
                             "env": {"TOKEN": "env:ALPHA_TOKEN"}})
    MS.set_enabled(cfg, "alpha", True)
    MS.upsert(cfg, "beta", {"transport": "http",
                            "url": "https://beta.test/mcp", "auth": "header",
                            "env": {"TOKEN": "vault:beta-token"}})
    MS.set_enabled(cfg, "beta", False)


def _no_exec(monkeypatch):
    import socket
    import subprocess
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("MCP invocation must never connect or spawn")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# One read authority across the five surfaces
# ---------------------------------------------------------------------------
def test_every_surface_reads_through_list_servers(tmp_path, monkeypatch):
    from voicebridge import integration_status as IS
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC
    from voicebridge import runtime_inventory as RIN
    from voicebridge import voice_commands as VC
    from voicebridge.gateway.openclaw_handlers import mcp_app

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)

    calls = {"n": 0}
    real = MS.list_servers

    def counting(c):
        calls["n"] += 1
        return real(c)

    monkeypatch.setattr(MS, "list_servers", counting)

    # dashboard: GET /api/mcp/servers
    code, dash = HC.h_mcp_servers(cfg, {}, {}, {})
    assert code == 200, dash
    # CLI: `qubit mcp status` -> integration_status.mcp_status
    cli = IS.mcp_status(cfg)
    # voice: the `mcp` backend
    voice = VC._b_mcp(VC.make_ctx(cfg), {})["mcp"]
    # gateway: the OpenClaw mcp.app handlers
    gateway = mcp_app._mcp_servers(cfg)
    # /chat + /qubit-chat SPA status source: the runtime inventory
    inv = RIN.runtime_inventory(cfg)

    assert calls["n"] >= 5, calls
    canonical = {"alpha", "beta"}
    dash_names = {s["name"] for s in dash["servers"]}
    assert dash_names == canonical
    assert set(cli["servers"]) == canonical
    assert set(voice["servers"]) == canonical
    assert {s["name"] for s in gateway["servers"]} == canonical
    assert set(inv["mcp"]["servers"]) == canonical


def test_status_records_carry_fail_closed_invocation_disposition(
        tmp_path, monkeypatch):
    from voicebridge import integration_status as IS
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)

    out = MS.list_servers(cfg)
    for row in out["servers"]:
        assert row["invocable"] is False, row
        assert row["invocation_code"] == MS.INVOKE_OPERATOR_CODE, row
        assert row["connected"] is False, row

    status = IS.mcp_status(cfg)
    assert status["invocable"] is False
    assert status["invocation_code"] == MS.INVOKE_OPERATOR_CODE
    for rec in status["records"]:
        assert rec["invocable"] is False, rec
        assert rec["invocation_code"] == MS.INVOKE_OPERATOR_CODE, rec


# ---------------------------------------------------------------------------
# Typed invocation disposition — operator-only, never a fabricated call
# ---------------------------------------------------------------------------
def test_invocation_status_is_typed_operator_only(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    _no_exec(monkeypatch)

    st = MS.invocation_status(cfg, "alpha")
    assert st["state"] == "OPERATOR_ONLY", st
    assert st["invocable"] is False and st["connected"] is False
    assert st["code"] == MS.INVOKE_OPERATOR_CODE
    assert st["reason"].startswith("MCP_NOT_CONNECTED_NO_CLIENT_INVOCATION")
    assert st["transport"] == "stdio"
    assert st["operator_prerequisite"]
    assert st["authority"] == "voicebridge.mcp_servers.list_servers"

    with pytest.raises(MS.McpServerError) as exc:
        MS.invocation_status(cfg, "ghost")
    assert exc.value.code == MS.INVOKE_NOT_FOUND_CODE


def test_invoke_refuses_never_connects_and_requires_confirmation(
        tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    _no_exec(monkeypatch)

    # Confirmation is required before any disposition is returned.
    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "alpha", tool="alpha_tool")
    assert exc.value.code == MS.INVOKE_CONFIRM_CODE

    out = MS.invoke(cfg, "alpha", tool="alpha_tool",
                    args={"q": 1}, confirmed=True)
    assert out["state"] == "OPERATOR_ONLY", out
    assert out["code"] == MS.INVOKE_OPERATOR_CODE
    assert out["invocable"] is False and out["connected"] is False
    assert out["tool"] == "alpha_tool"
    assert "no MCP connection was opened" in out["detail"]

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "ghost", confirmed=True)
    assert exc.value.code == MS.INVOKE_NOT_FOUND_CODE


def test_invoke_is_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(MS.McpServerError) as exc:
            MS.invoke(cfg, "alpha", confirmed=True)
        assert exc.value.code == MS.INVOKE_ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_no_secret_value_leaks_through_invocation_paths(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)

    # A raw secret write stays refused at the registry boundary.
    with pytest.raises(MS.McpServerError) as exc:
        MS.upsert(cfg, "raw", {"transport": "stdio", "command": "raw-mcp",
                               "env": {"TOKEN": "sk-live-DO-NOT-RETURN"}})
    assert exc.value.code == "SECRET_WRITE_REFUSED"

    blob = json.dumps({
        "status": MS.invocation_status(cfg, "alpha"),
        "invoke": MS.invoke(cfg, "alpha", confirmed=True),
        "servers": MS.list_servers(cfg),
    })
    assert "sk-live" not in blob and "DO-NOT-RETURN" not in blob
    # Only references are ever present.
    for row in MS.list_servers(cfg)["servers"]:
        for value in dict(row.get("env") or {}).values():
            assert value.startswith(("env:", "file:", "vault:")), value


def test_config_declared_server_is_operator_only_not_invocable(
        tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch,
               "logging:\n  jsonl: false\n"
               "mcp:\n  servers:\n    legacy-config:\n"
               "      transport: stdio\n      command: legacy-mcp\n")
    st = MS.invocation_status(cfg, "legacy-config")
    assert st["state"] == "OPERATOR_ONLY"
    assert st["invocable"] is False
    assert st["code"] == MS.INVOKE_OPERATOR_CODE


# ---------------------------------------------------------------------------
# Gateway MCP surface uses the canonical disposition
# ---------------------------------------------------------------------------
def test_gateway_call_tool_refuses_a_configured_server(tmp_path, monkeypatch):
    from voicebridge.gateway import rpc as R

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    reg = R.default_registry()

    out = reg.handle(cfg, "mcp.app.callTool",
                     {"toolName": "alpha", "confirm": True})
    assert out["state"] == "OPERATOR_ONLY", out
    assert out["code"] == "MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED", out
    assert out["result"] is None and out["invocable"] is False


def test_gateway_call_tool_still_runs_in_process_tools(tmp_path, monkeypatch):
    from voicebridge.gateway import rpc as R

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    reg = R.default_registry()
    out = reg.handle(cfg, "mcp.app.callTool",
                     {"toolName": "builtin.echo", "arguments": {"text": "hi"},
                      "confirm": True})
    assert out["state"] == "OK", out
    assert out["result"]["echo"] == "hi", out


def test_dashboard_status_mounts_servers(tmp_path, monkeypatch):
    """The dashboard wire shape stays additive: env refs survive intact."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    code, body = HC.h_mcp_servers(cfg, {}, {}, {})
    assert code == 200, body
    rows = {s["name"]: s for s in body["servers"]}
    assert rows["alpha"]["env"]["TOKEN"] == "env:ALPHA_TOKEN"
    assert rows["beta"]["env"]["TOKEN"] == "vault:beta-token"
    # No path exposes an invocable/connected server.
    assert all(s["connected"] is False for s in body["servers"])
