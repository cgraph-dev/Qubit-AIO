"""MCP capability reads on the dashboard compat prefix (resources/prompts/templates).

The vendored router exposes read-only ``/api/mcp/servers/{name}/resources`` /
``/prompts`` / ``/resource-templates`` over the fork's MCP config registry
(``qubit_runtime/qubit_cli/web_routers/mcp.py``), but the dashboard
``/agent/api/mcp/servers`` prefix is quarantined to ``voicebridge.qubit_compat``
(``webui.QUBIT_READ_AUTHORITY``), so those routes 404'd with "no compat route".
These compat handlers resolve the server from the canonical
``voicebridge.mcp_servers`` registry and drain the real list through the ONE
brokered client transport (``voicebridge.mcp_client``) — never a second MCP
client and never a fabricated list.

Hermetic: the stdio server is a small stdlib python script written to
``tmp_path``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAKE_STDIO_SERVER = r'''
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
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "fake", "version": "1.0"}}})
    elif method == "resources/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"resources": [
            {"uri": "file:///a", "name": "a", "mimeType": "text/plain"},
            {"uri": "file:///b", "name": "b"}]}})
    elif method == "resources/templates/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"resourceTemplates": [
            {"uriTemplate": "file:///{path}", "name": "readme",
             "description": "Read a file", "mimeType": "text/markdown"}]}})
    elif method == "prompts/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "prompts": [{"name": "greet", "description": "Say hi"}]}})
    else:
        _send({"jsonrpc": "2.0", "id": mid,
               "error": {"code": -32601, "message": "method not found"}})
'''


TOOLS_ONLY_STDIO_SERVER = r'''
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
            "serverInfo": {"name": "tools-only", "version": "1.0"}}})
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


def _seed_stdio(cfg, tmp_path, *, enabled=True, source=None, name="fake"):
    from voicebridge import mcp_servers as MS

    script = tmp_path / f"{name}_mcp_caps.py"
    script.write_text(source or FAKE_STDIO_SERVER, encoding="utf-8")
    MS.upsert(cfg, name, {"transport": "stdio", "command": sys.executable,
                          "args": [str(script)]})
    MS.set_enabled(cfg, name, enabled)
    return script


def _no_spawn(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("no MCP process may be spawned on this path")

    monkeypatch.setattr(subprocess, "Popen", _boom)


ROUTES = [
    ("/api/mcp/servers/fake/resources", "h_mcp_server_resources", "resources"),
    ("/api/mcp/servers/fake/prompts", "h_mcp_server_prompts", "prompts"),
    ("/api/mcp/servers/fake/resource-templates",
     "h_mcp_server_resource_templates", "resource_templates"),
]


def test_capability_routes_resolve_to_registered_handlers():
    from voicebridge import qubit_compat as HC

    for path, handler, _key in ROUTES:
        route, params = HC.match("GET", path)
        assert route is not None, path
        assert route["handler"] == handler, (path, route)
        assert route["disposition"] == "implemented", (path, route)
        assert params == {"name": "fake"}, params
        assert callable(HC.HANDLERS.get(handler)), (path, handler)


def test_unknown_server_is_a_typed_404_without_spawning(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _no_spawn(monkeypatch)
    for path, _handler, _key in ROUTES:
        code, out = HC.dispatch(cfg, "GET", path.replace("fake", "ghost"),
                                {}, {})
        assert code == 404, (path, code, out)
        assert out["code"] == "MCP_NOT_FOUND", (path, out)


def test_disabled_server_returns_honest_disposition_without_spawning(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=False)
    _no_spawn(monkeypatch)
    for path, _handler, key in ROUTES:
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200, (path, code, out)
        assert out["ok"] is True and out["connected"] is False, (path, out)
        assert out["state"] == "OPERATOR_ONLY", (path, out)
        assert out["code"] == "MCP_SERVER_DISABLED", (path, out)
        assert out[key] == [], (path, out)
        assert out["operator_prerequisite"], (path, out)


def test_enabled_server_drains_real_resources_prompts_and_templates(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=True)

    code, out = HC.dispatch(
        cfg, "GET", "/api/mcp/servers/fake/resources", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["connected"] is True, out
    assert [r["uri"] for r in out["resources"]] == ["file:///a", "file:///b"], out
    assert out["count"] == 2, out

    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers/fake/prompts", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert [p["name"] for p in out["prompts"]] == ["greet"], out

    code, out = HC.dispatch(
        cfg, "GET", "/api/mcp/servers/fake/resource-templates", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["resource_templates"] == [{
        "uriTemplate": "file:///{path}", "name": "readme",
        "description": "Read a file", "mimeType": "text/markdown"}], out


def test_unsupported_capability_is_a_typed_bad_input(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=True)

    with pytest.raises(MS.McpServerError) as exc:
        MS.list_capability(cfg, "fake", "not-a-capability")
    assert exc.value.code == "MCP_BAD_INPUT"


def test_tools_only_server_is_a_typed_error_not_a_fabricated_list(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=True,
                source=TOOLS_ONLY_STDIO_SERVER, name="tools-only")

    code, out = HC.dispatch(
        cfg, "GET", "/api/mcp/servers/tools-only/resources", {}, {})
    assert code == 200, (code, out)
    assert out["ok"] is False and out["state"] == "ERROR", (code, out)
    assert out["connected"] is False, out
    assert out["code"] == "MCP_PROTOCOL_ERROR", out
    assert "method not found" in out["detail"], out


def test_capability_read_never_requires_confirmation(tmp_path, monkeypatch):
    """A read drains without a confirmation id (unlike tool invocation)."""
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=True)

    out = MS.list_capability(cfg, "fake", "resources",
                             requested_by="test:read")
    assert out["state"] == "OK", out
    assert out["connected"] is True, out
    assert [r["uri"] for r in out["result"]["resources"]] == \
        ["file:///a", "file:///b"], out
