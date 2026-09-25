"""Canonical MCP invocation from the dashboard compat prefix.

The dashboard's confirm-gated ``POST /api/mcp/servers/{name}/invoke`` is owned
by ``voicebridge.qubit_compat.mcp_invoke`` (``voicebridge.mcp_servers``), not the
vendored router. This locks two contracts that only the canonical handler can
provide:

* every real wire spelling of the tool arguments (the SPA's ``args``, the MCP
  client spec's ``arguments``, the voice/gateway ``tool_args``) reaches the one
  brokered ``voicebridge.mcp_servers.invoke`` path — never a silently dropped
  argument list;
* the invocation reads/writes the canonical ``voicebridge.mcp_servers`` registry
  (``<state_dir>/mcp_servers.json``), so a registered server is invocable from
  the dashboard with no second store involved.

Hermetic: the stdio server is a small stdlib echo script written to
``tmp_path``; no network, no credential, no ``cgraph-graft`` dependency.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: Echo MCP server: ``initialize`` + ``tools/list`` + ``tools/call``. The
#: ``tools/call`` result text is the exact ``arguments`` object it received, so
#: a dropped argument is observable.
ECHO_STDIO_SERVER = r'''
import json, sys


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    mid = msg.get("id")
    if mid is None:
        continue
    method = msg.get("method")
    if method == "initialize":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "echo", "version": "1.0"}}})
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo", "description": "echo arguments",
             "inputSchema": {"type": "object"}}]}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(params.get("arguments") or {})}],
            "isError": False}})
    else:
        _send({"jsonrpc": "2.0", "id": mid,
               "error": {"code": -32601, "message": "method not found"}})
'''


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_echo(cfg, tmp_path, *, name="echo-srv"):
    from voicebridge import mcp_servers as MS

    script = tmp_path / "echo_mcp.py"
    script.write_text(ECHO_STDIO_SERVER, encoding="utf-8")
    MS.upsert(cfg, name, {"transport": "stdio", "command": sys.executable,
                          "args": [str(script)], "capabilities": ["echo"]})
    MS.set_enabled(cfg, name, True)
    return script


def _echo_text(out):
    content = (out.get("result") or {}).get("content") or []
    assert content and content[0].get("type") == "text", out
    return json.loads(content[0]["text"])


def test_dashboard_invoke_route_resolves_to_the_canonical_handler():
    from voicebridge import qubit_compat as HC

    route, params = HC.match("POST", "/api/mcp/servers/echo-srv/invoke")
    assert route is not None, route
    assert route["handler"] == "mcp_invoke", route
    assert params == {"name": "echo-srv"}, params
    assert callable(HC.HANDLERS.get("mcp_invoke"))


def test_invoke_forwards_mcp_spec_arguments_key(tmp_path, monkeypatch):
    """The MCP client spec key ``arguments`` must reach the server verbatim."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_echo(cfg, tmp_path)

    code, out = HC.mcp_invoke(
        cfg, {}, {"tool": "echo", "arguments": {"query": "mcp authority"},
                  "confirm": True}, {"name": "echo-srv"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["state"] == "OK", out
    assert out["connected"] is True, out
    assert _echo_text(out) == {"query": "mcp authority"}, out


def test_invoke_forwards_the_spa_args_key(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_echo(cfg, tmp_path)

    code, out = HC.mcp_invoke(
        cfg, {}, {"tool": "echo", "args": {"x": 1}, "confirm": True},
        {"name": "echo-srv"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert _echo_text(out) == {"x": 1}, out


def test_invoke_without_confirm_stages_and_connects_nothing(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_echo(cfg, tmp_path)

    code, out = HC.mcp_invoke(
        cfg, {}, {"tool": "echo", "arguments": {"x": 1}},
        {"name": "echo-srv"})
    assert code == 409, (code, out)
    assert out["code"] == "CONFIRM_REQUIRED", out
    assert out["state"] == "NEEDS_CONFIRMATION", out


def test_invoke_reads_the_canonical_registry_not_a_second_store(
        tmp_path, monkeypatch):
    """A server created through the compat registry is immediately invocable;
    the canonical store file is the only registry written."""
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    script = _seed_echo(cfg, tmp_path)

    # It is the canonical file the compat create writes.
    store = Path(cfg.state_dir()) / MS.STORE_NAME
    assert store.is_file(), store
    assert "echo-srv" in json.loads(store.read_text())["servers"]

    # A dashboard-created server (validated fields, confirm-gated) is
    # invocable through the same canonical path.
    code, out = HC.mcp_create(
        cfg, {}, {"name": "dashboard-srv", "transport": "stdio",
                  "command": sys.executable, "args": [str(script)],
                  "capabilities": ["echo"], "enabled": True, "confirm": True},
        {})
    assert code == 200, (code, out)

    code, out = HC.mcp_invoke(
        cfg, {}, {"tool": "echo", "arguments": {"from": "dashboard"},
                  "confirm": True}, {"name": "dashboard-srv"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert _echo_text(out) == {"from": "dashboard"}, out


def test_invoke_unknown_server_is_typed_not_found(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.mcp_invoke(
        cfg, {}, {"tool": "echo", "confirm": True}, {"name": "ghost"})
    assert code == 404, (code, out)
    assert out["code"] == "MCP_NOT_FOUND", out
