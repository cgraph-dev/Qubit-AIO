"""9U — real brokered MCP client invocation (stdio + HTTP).

Exercises the ONE brokered client transport behind
``voicebridge.mcp_servers.invoke``:

* an explicit stdio server is spawned and a tool call round-trips through the
  MCP JSON-RPC handshake;
* an http server is connected and called;
* the capability/allowlist gate, e-stop, and shared durable confirmation gate
  every call (fail closed);
* a stdio timeout SIGKILLs the whole process group (verified via the child pid);
* connect / protocol / tool errors are typed;
* resolved credential values never appear in a result, the registry read, or an
  audit event.

These are hermetic: the stdio server is a small stdlib python script written to
``tmp_path`` and the http server is bound to an ephemeral loopback port.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAKE_STDIO_SERVER = r'''
import json, os, sys, time


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


pidfile = os.environ.get("FAKE_PIDFILE")
if pidfile:
    try:
        with open(pidfile, "w") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "serverInfo": {"name": "fake", "version": "1.0"}}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "slow":
            time.sleep(30)
        if name == "boom":
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32000, "message": "tool exploded"}})
            continue
        if name == "fail":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "handled failure"}],
                "isError": True}})
            continue
        if name == "leak":
            text = "token=" + os.environ.get("TOKEN", "")
        else:
            text = "echo:" + json.dumps(args)
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": False}})
    # notifications and unknown methods are ignored
'''


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _write_stdio_server(tmp_path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_STDIO_SERVER, encoding="utf-8")
    return script


def _seed_stdio(cfg, tmp_path, *, capabilities, env=None):
    from voicebridge import mcp_servers as MS

    script = _write_stdio_server(tmp_path)
    fields = {"transport": "stdio", "command": sys.executable,
              "args": [str(script)], "capabilities": capabilities}
    if env is not None:
        fields["env"] = env
    MS.upsert(cfg, "fake", fields)
    MS.set_enabled(cfg, "fake", True)
    return script


def _no_spawn(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no MCP process may be spawned on this path")

    monkeypatch.setattr(subprocess, "Popen", _boom)


# ---------------------------------------------------------------------------
# stdio success
# ---------------------------------------------------------------------------
def test_stdio_success_round_trips_a_tool_call(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"])

    out = MS.invoke(cfg, "fake", tool="echo", args={"q": 1}, confirmed=True)
    assert out["state"] == "OK", out
    assert out["invocable"] is True and out["connected"] is True
    assert out["is_error"] is False and out["redacted"] is True
    assert "echo:" in out["text"]
    assert out["result"]["content"][0]["text"].startswith("echo:")


def test_invocation_status_and_registry_report_invocable(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo", "leak"])

    st = MS.invocation_status(cfg, "fake")
    assert st["state"] == "INVOCABLE", st
    assert st["invocable"] is True and st["connected"] is False
    assert st["code"] == MS.INVOKE_READY_CODE
    assert st["operator_prerequisite"] == ""

    row = next(s for s in MS.list_servers(cfg)["servers"]
               if s["name"] == "fake")
    assert row["invocable"] is True, row
    assert row["invocation_code"] == MS.INVOKE_READY_CODE
    assert row["capabilities"] == ["echo", "leak"]
    assert MC.is_invocable(row) is True


# ---------------------------------------------------------------------------
# connect failure / timeout / protocol / tool errors (typed)
# ---------------------------------------------------------------------------
def test_stdio_connect_failure_is_typed(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "dead", {"transport": "stdio",
                            "command": "/nonexistent/definitely-not-mcp",
                            "capabilities": ["echo"]})
    MS.set_enabled(cfg, "dead", True)

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "dead", tool="echo", confirmed=True)
    assert exc.value.code == MC.CONNECT_CODE, exc.value


def test_stdio_timeout_kills_the_process_group(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    pidfile = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_PIDFILE", str(pidfile))
    _seed_stdio(cfg, tmp_path, capabilities=["slow"],
                env={"FAKE_PIDFILE": "env:FAKE_PIDFILE"})

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "fake", tool="slow", confirmed=True, timeout_s=0.5)
    assert exc.value.code == MC.TIMEOUT_CODE, exc.value

    for _ in range(40):
        if pidfile.exists():
            break
        time.sleep(0.05)
    assert pidfile.exists(), "child never started"
    pid = int(pidfile.read_text())

    killed = False
    for _ in range(60):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            killed = True
            break
        time.sleep(0.05)
    assert killed, f"stdio child {pid} survived the timeout kill"


def test_jsonrpc_error_is_typed(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["boom"])

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "fake", tool="boom", confirmed=True)
    assert exc.value.code == MC.PROTOCOL_CODE, exc.value


def test_tool_level_error_result_is_not_success(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["fail"])

    out = MS.invoke(cfg, "fake", tool="fail", confirmed=True)
    assert out["state"] == "ERROR", out
    assert out["code"] == MC.TOOL_ERROR_CODE
    assert out["is_error"] is True


# ---------------------------------------------------------------------------
# gates: allowlist, e-stop, confirmation
# ---------------------------------------------------------------------------
def test_tool_allowlist_blocks_an_unlisted_tool(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"])
    _no_spawn(monkeypatch)

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "fake", tool="rm", confirmed=True)
    assert exc.value.code == MC.NOT_ALLOWED_CODE, exc.value


def test_server_without_allowlist_stays_operator_only(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    script = _write_stdio_server(tmp_path)
    MS.upsert(cfg, "plain", {"transport": "stdio", "command": sys.executable,
                             "args": [str(script)]})
    MS.set_enabled(cfg, "plain", True)
    _no_spawn(monkeypatch)

    out = MS.invoke(cfg, "plain", tool="echo", confirmed=True)
    assert out["state"] == "OPERATOR_ONLY", out
    assert out["code"] == MS.INVOKE_OPERATOR_CODE
    assert out["invocable"] is False
    assert "no MCP connection was opened" in out["detail"]

    st = MS.invocation_status(cfg, "plain")
    assert st["state"] == "OPERATOR_ONLY"
    assert st["blocked_by"] in ("", "MCP_TOOL_NOT_ALLOWED")


def test_estop_denies_before_any_spawn(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"])
    _no_spawn(monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(MS.McpServerError) as exc:
            MS.invoke(cfg, "fake", tool="echo", confirmed=True)
        assert exc.value.code == MS.INVOKE_ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_confirmation_is_required_and_consumed_once(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.approvals import ApprovalJournal

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"])

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "fake", tool="echo", args={})
    assert exc.value.code == MS.INVOKE_CONFIRM_CODE

    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    item = journal.create("mcp.invoke", "test", ttl_s=120.0, session="mcp",
                          actor="test")
    journal.respond(item.id, "approve")

    out = MS.invoke(cfg, "fake", tool="echo", args={}, confirmation_id=item.id)
    assert out["state"] == "OK", out

    # The durable approval is single-use: replaying it is refused.
    with pytest.raises(MS.McpServerError) as exc2:
        MS.invoke(cfg, "fake", tool="echo", args={}, confirmation_id=item.id)
    assert exc2.value.code == MS.INVOKE_CONFIRM_CODE


# ---------------------------------------------------------------------------
# secret handling
# ---------------------------------------------------------------------------
def test_resolved_secret_is_never_returned_or_registry_visible(
        tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_TOKEN", "sk-live-8u-DO-NOT-RETURN")
    _seed_stdio(cfg, tmp_path, capabilities=["leak"],
                env={"TOKEN": "env:FAKE_TOKEN"})

    out = MS.invoke(cfg, "fake", tool="leak", confirmed=True)
    blob = json.dumps(out)
    assert "sk-live-8u-DO-NOT-RETURN" not in blob, blob
    assert "[REDACTED]" in out["text"], out

    row = next(s for s in MS.list_servers(cfg)["servers"]
               if s["name"] == "fake")
    assert row["env"]["TOKEN"] == "env:FAKE_TOKEN"

    # A raw secret write is still refused at the registry boundary.
    with pytest.raises(MS.McpServerError) as exc:
        MS.upsert(cfg, "raw", {"transport": "stdio", "command": "raw-mcp",
                               "env": {"TOKEN": "sk-live-8u-DO-NOT-RETURN"}})
    assert exc.value.code == "SECRET_WRITE_REFUSED"


def test_unresolvable_vault_reference_fails_closed(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"],
                env={"TOKEN": "vault:no-resolver-here"})
    _no_spawn(monkeypatch)

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "fake", tool="echo", confirmed=True)
    assert exc.value.code == MC.SECRET_CODE, exc.value


def test_capabilities_must_be_valid_tool_names(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(MS.McpServerError) as exc:
        MS.upsert(cfg, "bad", {"transport": "stdio", "command": "x",
                               "capabilities": ["bad tool!"]})
    assert exc.value.code == "MCP_BAD_INPUT"


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------
class _FakeMcpHttpHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            msg = json.loads(raw or b"{}")
        except ValueError:
            msg = {}
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            body = {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "fake-http", "version": "1.0"}}}
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") == "slow":
                time.sleep(5)
            if params.get("name") == "leak":
                text = "token=" + os.environ.get("HTTP_SECRET", "")
            else:
                text = "echo:" + json.dumps(params.get("arguments") or {})
            body = {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}}
        elif mid is None:
            self.send_response(202)
            self.end_headers()
            return
        else:
            body = {"jsonrpc": "2.0", "id": mid, "result": {}}
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def http_mcp():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMcpHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        server.server_close()


def test_http_success_round_trips_a_tool_call(tmp_path, monkeypatch, http_mcp):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _server, url = http_mcp
    MS.upsert(cfg, "web", {"transport": "http", "url": url,
                           "capabilities": ["echo"]})
    MS.set_enabled(cfg, "web", True)

    out = MS.invoke(cfg, "web", tool="echo", args={"a": 1}, confirmed=True)
    assert out["state"] == "OK", out
    assert out["transport"] == "http"
    assert "echo:" in out["text"]


def test_http_timeout_is_typed(tmp_path, monkeypatch, http_mcp):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _server, url = http_mcp
    MS.upsert(cfg, "web", {"transport": "http", "url": url,
                           "capabilities": ["slow"]})
    MS.set_enabled(cfg, "web", True)

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "web", tool="slow", confirmed=True, timeout_s=0.5)
    assert exc.value.code == MC.TIMEOUT_CODE, exc.value


def test_http_connect_failure_is_typed(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "web", {"transport": "http",
                           "url": "http://127.0.0.1:9/mcp",
                           "capabilities": ["echo"]})
    MS.set_enabled(cfg, "web", True)

    with pytest.raises(MS.McpServerError) as exc:
        MS.invoke(cfg, "web", tool="echo", confirmed=True)
    assert exc.value.code == MC.CONNECT_CODE, exc.value


def test_http_secret_is_redacted_from_result(tmp_path, monkeypatch, http_mcp):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("HTTP_SECRET", "sk-http-DO-NOT-RETURN")
    _server, url = http_mcp
    MS.upsert(cfg, "web", {"transport": "http", "url": url,
                           "capabilities": ["leak"],
                           "env": {"TOKEN": "env:HTTP_SECRET"}})
    MS.set_enabled(cfg, "web", True)

    out = MS.invoke(cfg, "web", tool="leak", confirmed=True)
    blob = json.dumps(out)
    assert "sk-http-DO-NOT-RETURN" not in blob, blob
    assert "[REDACTED]" in out["text"], out


# ---------------------------------------------------------------------------
# gateway + CLI/voice entry points
# ---------------------------------------------------------------------------
def test_gateway_call_tool_runs_an_allowlisted_server(tmp_path, monkeypatch):
    from voicebridge.gateway import rpc as R

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"])
    reg = R.default_registry()

    out = reg.handle(cfg, "mcp.app.callTool",
                     {"server": "fake", "tool": "echo",
                      "arguments": {"x": 1}, "confirm": True})
    assert out["state"] == "OK", out
    assert "echo:" in out["text"], out


def test_gateway_call_tool_refusal_is_preserved(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.gateway import rpc as R

    cfg = _cfg(tmp_path, monkeypatch)
    script = _write_stdio_server(tmp_path)
    MS.upsert(cfg, "plain", {"transport": "stdio", "command": sys.executable,
                             "args": [str(script)]})
    MS.set_enabled(cfg, "plain", True)
    reg = R.default_registry()

    out = reg.handle(cfg, "mcp.app.callTool",
                     {"toolName": "plain", "confirm": True})
    assert out["state"] == "OPERATOR_ONLY", out
    assert out["code"] == "MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED", out


def test_cli_and_voice_entry_points_share_the_canonical_path(
        tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, capabilities=["echo"])

    cli = MS.invoke_cli(cfg, "fake", tool="echo", confirmed=True)
    voice = MS.invoke_voice(cfg, "fake", tool="echo", confirmed=True)
    assert cli["state"] == "OK" and voice["state"] == "OK"
    assert cli["requested_by"] == "cli:user"
    assert voice["requested_by"] == "voice:user"
    assert cli["authority"] == voice["authority"]
