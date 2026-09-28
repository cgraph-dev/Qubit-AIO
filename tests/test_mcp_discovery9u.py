"""9U — real MCP discovery + resources/prompts through the ONE brokered client.

Proves that MCP client invocation is real, not definition-only:

* ``tools/list`` discovery runs over the brokered stdio transport and returns
  exactly the tools the live server advertises (``mcp_servers.test`` and
  ``mcp_servers.discover``);
* ``resources/list`` / ``resources/read`` / ``prompts/list`` / ``prompts/get``
  run through ``mcp_servers.invoke_method`` behind the shared e-stop +
  durable-journal confirmation gate;
* an unknown method is refused typed at both the client and the registry;
* the gateway ``mcp.app.listResources`` / ``mcp.app.readResource`` handlers
  perform the real read when a configured, ready server is named, and keep the
  typed SAFE_EQUIVALENT pointer when no server is mounted;
* an unreachable server is a typed failure, never a fabricated tool.

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

TOOLS = [{"name": "echo", "description": "echo"}, {"name": "danger"}]


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
        continue  # notification (e.g. notifications/initialized)
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "fake", "version": "1.0"}}})
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "resources/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"resources": [
            {"uri": "file:///a", "name": "a"},
            {"uri": "file:///b", "name": "b"}]}})
    elif method == "resources/read":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"contents": [
            {"uri": params.get("uri"), "text": "content:" + str(params.get("uri"))}]}})
    elif method == "prompts/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "prompts": [{"name": "greet"}]}})
    elif method == "prompts/get":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"messages": [
            {"role": "user", "content": {"type": "text", "text": "hi"}}]}})
    elif method == "tools/call":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "echo"}], "isError": False}})
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


def _seed_stdio(cfg, tmp_path, *, capabilities=None, enabled=True):
    from voicebridge import mcp_servers as MS

    script = tmp_path / "fake_mcp_disco.py"
    script.write_text(FAKE_STDIO_SERVER, encoding="utf-8")
    fields = {"transport": "stdio", "command": sys.executable,
              "args": [str(script)]}
    if capabilities is not None:
        fields["capabilities"] = capabilities
    MS.upsert(cfg, "fake", fields)
    MS.set_enabled(cfg, "fake", enabled)
    return script


def _no_spawn(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("no MCP process may be spawned on this path")

    monkeypatch.setattr(subprocess, "Popen", _boom)


# ---------------------------------------------------------------------------
# tools/list discovery
# ---------------------------------------------------------------------------
def test_test_endpoint_probes_real_tools(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)  # no declared allowlist: discovery populates it

    out = MS.test(cfg, "fake")
    assert out["state"] == "OK", out
    assert out["connected"] is True, out
    assert out["network_probe"] is True, out
    assert out["tools"] == ["echo", "danger"], out
    assert out["code"] == MS.DISCOVER_PROBE_CODE, out


def test_discover_reports_real_tools_and_can_persist_allowlist(
        tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)

    rec = MS.list_servers(cfg)["servers"][0]
    assert rec["invocable"] is False  # no allowlist yet

    # Confirmation is required before any discovery connection is opened.
    with pytest.raises(MS.McpServerError) as exc:
        MS.discover(cfg, "fake")
    assert exc.value.code == MS.INVOKE_CONFIRM_CODE

    out = MS.discover(cfg, "fake", confirmed=True)
    assert out["connected"] is True and out["tools"] == ["echo", "danger"], out
    assert out["invocable"] is False  # not persisted yet

    persisted = MS.discover(cfg, "fake", persist=True, confirmed=True)
    assert persisted["persisted"] is True and persisted["invocable"] is True
    row = next(s for s in MS.list_servers(cfg)["servers"]
               if s["name"] == "fake")
    assert row["capabilities"] == ["echo", "danger"], row
    assert MC.is_invocable(row) is True


def test_direct_client_discovery_and_method_gate(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)
    rec = MS.list_servers(cfg)["servers"][0]

    out = MC.discover_tools(cfg, rec, timeout_s=5.0)
    assert out["names"] == ["echo", "danger"], out
    assert out["state"] == "OK"

    # methods/list is a tool call, not a sanctioned non-tool method.
    with pytest.raises(MC.McpClientError) as exc:
        MC.call_method(cfg, rec, "methods/list")
    assert exc.value.code == MC.METHOD_CODE, exc.value

    # A disabled server cannot even be probed.
    disabled = dict(rec, enabled=False)
    with pytest.raises(MC.McpClientError) as exc2:
        MC.discover_tools(cfg, disabled)
    assert exc2.value.code == MC.DISABLED_CODE, exc2.value


def test_unreachable_server_is_a_typed_failure_not_fabricated(
        tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "dead", {"transport": "stdio",
                            "command": "/nonexistent/definitely-not-mcp"})
    MS.set_enabled(cfg, "dead", True)

    out = MS.test(cfg, "dead")
    assert out["connected"] is False and out["tools"] == [], out
    assert out["code"] == MC.CONNECT_CODE, out


def test_live_probe_is_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)
    _no_spawn(monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        out = MS.test(cfg, "fake")
        assert out["state"] == "BLOCKED", out
        assert out["code"] == MS.INVOKE_ESTOP_CODE, out
        assert out["connected"] is False and out["tools"] == []
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


# ---------------------------------------------------------------------------
# resources + prompts through invoke_method
# ---------------------------------------------------------------------------
def test_invoke_method_lists_and_reads_resources(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)

    listing = MS.invoke_method(cfg, "fake", "resources/list", confirmed=True)
    assert listing["state"] == "OK", listing
    assert listing["connected"] is True and listing["invocable"] is True
    assert listing["names"] == ["file:///a", "file:///b"], listing

    read = MS.invoke_method(cfg, "fake", "resources/read",
                            params={"uri": "file:///a"}, confirmed=True)
    assert read["state"] == "OK", read
    assert "content:file:///a" in read["text"], read


def test_invoke_method_lists_and_gets_prompts(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)

    listing = MS.invoke_method(cfg, "fake", "prompts/list", confirmed=True)
    assert listing["state"] == "OK" and listing["names"] == ["greet"], listing

    got = MS.invoke_method(cfg, "fake", "prompts/get",
                           params={"name": "greet"}, confirmed=True)
    assert got["state"] == "OK", got
    assert got["result"]["messages"][0]["content"]["text"] == "hi", got


def test_invoke_method_refuses_unknown_and_unconfigured(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke_method(cfg, "fake", "evil/method", confirmed=True)
    assert exc.value.code == "MCP_BAD_INPUT", exc.value

    with pytest.raises(MS.McpServerError) as exc2:
        MS.invoke_method(cfg, "ghost", "resources/list", confirmed=True)
    assert exc2.value.code == MS.INVOKE_NOT_FOUND_CODE, exc2.value


def test_invoke_method_unrunnable_is_operator_only_without_spawn(
        tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=False)
    _no_spawn(monkeypatch)

    out = MS.invoke_method(cfg, "fake", "resources/list", confirmed=True)
    assert out["state"] == "OPERATOR_ONLY", out
    assert out["invocable"] is False
    assert out["operator_prerequisite"]


def test_method_invocation_is_estop_and_confirmation_gated(tmp_path,
                                                           monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke_method(cfg, "fake", "resources/list")
    assert exc.value.code == MS.INVOKE_CONFIRM_CODE

    _no_spawn(monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(MS.McpServerError) as exc2:
            MS.invoke_method(cfg, "fake", "resources/list", confirmed=True)
        assert exc2.value.code == MS.INVOKE_ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


# ---------------------------------------------------------------------------
# gateway handlers
# ---------------------------------------------------------------------------
def _gateway_registry():
    from voicebridge.gateway import rpc as R

    return R.default_registry()


def test_gateway_list_and_read_resource_run_real_reads(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"])
    MS.set_enabled(cfg, "fake", True)
    reg = _gateway_registry()

    listing = reg.handle(cfg, "mcp.app.listResources",
                         {"server": "fake", "confirm": True})
    assert listing["state"] == "OK", listing
    assert [r["uri"] for r in listing["resources"]] == ["file:///a",
                                                        "file:///b"], listing

    read = reg.handle(cfg, "mcp.app.readResource",
                      {"server": "fake", "uri": "file:///a", "confirm": True})
    assert read["state"] == "OK", read
    assert read["contents"][0]["uri"] == "file:///a", read


def test_gateway_resource_falls_back_to_typed_pointer_without_server(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _gateway_registry()

    listing = reg.handle(cfg, "mcp.app.listResources", {})
    assert listing["code"] == "MCP_RESOURCES_LOCAL_TOOL", listing
    read = reg.handle(cfg, "mcp.app.readResource", {"uri": "file:///x"})
    assert read["code"] == "MCP_READ_RESOURCE_LOCAL_TOOL", read


def test_gateway_resource_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)
    MS.set_enabled(cfg, "fake", True)
    reg = _gateway_registry()

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "mcp.app.listResources", {"server": "fake"})
    assert exc.value.code == MS.INVOKE_CONFIRM_CODE, exc.value
