"""9u — fix-mcp: repair the two MCP audit defects (isolated Qubit :8895).

1. MED — ``POST /agent/api/mcp/servers/{name}/test`` fabricated success: the
   compat handler ``h_mcp_test_local`` wrapped the canonical probe in
   ``_ok(ok=True)`` unconditionally, so an unreachable server
   (``connected: false`` / ``code: MCP_CONNECT_FAILED`` / ``state:
   UNREACHABLE``) served ``ok: true``. McpPage keyed its toast on ``result.ok``
   and rendered a green "Connected — no tools". The canonical handler fix is
   filed as a patch-spec (``qubit_compat.py`` is coordinator-owned); the served
   boundary correction lives in ``webui.correct_mcp_test_verdict`` and is
   idempotent with it.
2. MED — the MCP capabilities panel rendered ``["[REDACTED]"]`` because
   ``webui._redact_served`` used a *global* seen-set of object ids, so the
   capability reader's legitimately *aliased* list (served under both
   ``result.<key>`` and the top-level ``<key>``) was masked. Fixed by tracking
   ids on the current recursion path only.

Hermetic: the stdio MCP server is a small stdlib python script in ``tmp_path``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "tools": [{"name": "echo"}]}})
    elif method == "resources/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"resources": [
            {"uri": "qa://greeting", "name": "greeting"}]}})
    elif method == "prompts/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"prompts": [
            {"name": "qa-prompt"}]}})
    elif method == "resources/templates/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"resourceTemplates": [
            {"uriTemplate": "qa://item/{id}", "name": "item"}]}})
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


def _seed_stdio(cfg, tmp_path, name="fake"):
    from voicebridge import mcp_servers as MS

    script = tmp_path / f"{name}_mcp.py"
    script.write_text(FAKE_STDIO_SERVER, encoding="utf-8")
    MS.upsert(cfg, name, {"transport": "stdio", "command": sys.executable,
                          "args": [str(script)]})
    MS.set_enabled(cfg, name, True)
    return script


# ---------------------------------------------------------------------------
# Defect 2 — the served boundary must not redact acyclic aliased data
# ---------------------------------------------------------------------------
def test_redactor_preserves_aliased_capability_list():
    from voicebridge.webui import _redact_served

    shared = [{"uri": "qa://greeting", "name": "greeting"}]
    payload = {"ok": True, "names": shared,
               "result": {"resources": shared}, "resources": shared}
    served = _redact_served(payload)
    assert served["resources"] == [{"uri": "qa://greeting",
                                    "name": "greeting"}], served
    assert served["resources"] == served["result"]["resources"], served
    assert "[REDACTED]" not in json.dumps(served), served


def test_redactor_still_redacts_true_cycles_but_masks_secrets():
    from voicebridge.webui import _redact_served

    d = {}
    d["self"] = d
    assert _redact_served(d) == {"self": "[REDACTED]"}
    cycle = []
    cycle.append(cycle)
    assert _redact_served(cycle) == ["[REDACTED]"]

    shared = {"password": "hunter2"}
    served = _redact_served({"a": shared, "b": shared})
    assert served["a"]["password"] == "[REDACTED]", served
    assert served["b"] == served["a"], served


def test_capability_route_lists_survive_served_redaction(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.webui import _redact_served

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)

    for path, key, expect in [
        ("/api/mcp/servers/fake/resources", "resources", "qa://greeting"),
        ("/api/mcp/servers/fake/prompts", "prompts", "qa-prompt"),
        ("/api/mcp/servers/fake/resource-templates",
         "resource_templates", "qa://item/{id}"),
    ]:
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200 and out["state"] == "OK", (path, out)
        assert out[key] == out["result"][
            next(k for k in ("resources", "prompts", "resourceTemplates")
                 if k in out["result"])], out
        served = _redact_served(out)
        assert "[REDACTED]" not in json.dumps(served), (path, served)
        assert len(served[key]) == 1, (path, served)


# ---------------------------------------------------------------------------
# Defect 1 — the served test verdict must be honest
# ---------------------------------------------------------------------------
def test_served_verdict_marks_unreachable_probe_as_failure():
    from voicebridge.webui import correct_mcp_test_verdict

    out = {"ok": True, "name": "probe", "tools": [], "connected": False,
           "code": "MCP_CONNECT_FAILED", "state": "UNREACHABLE",
           "detail": "MCP stdio server closed the connection"}
    served = correct_mcp_test_verdict(
        "POST", "/api/mcp/servers/probe/test", 200, out)
    assert served["ok"] is False, served
    assert served["error"], served


def test_served_verdict_leaves_a_real_success_untouched():
    from voicebridge.webui import correct_mcp_test_verdict

    out = {"ok": True, "name": "probe", "tools": [{"name": "echo"}],
           "connected": True, "code": "MCP_DISCOVERY_OK", "state": "OK"}
    served = correct_mcp_test_verdict(
        "POST", "/api/mcp/servers/probe/test", 200, out)
    assert served["ok"] is True, served
    assert "error" not in served, served


def test_served_verdict_only_touches_the_test_route():
    from voicebridge.webui import correct_mcp_test_verdict

    out = {"ok": True, "connected": False}
    for method, path in [("GET", "/api/mcp/servers/probe/resources"),
                         ("POST", "/api/mcp/servers/probe/invoke"),
                         ("POST", "/api/mcp/servers/probe/test-extra")]:
        assert correct_mcp_test_verdict(method, path, 200,
                                        dict(out)) == out, (method, path)
    # a non-200 test response is never rewritten
    assert correct_mcp_test_verdict(
        "POST", "/api/mcp/servers/probe/test", 400, dict(out)) == out


def test_handler_source_ok_matches_connected(tmp_path, monkeypatch):
    """The canonical handler fix (``ok=out["ok"]``) is correct: ``MS.test``
    already carries the honest flag."""
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)
    ok = MS.test(cfg, "fake")
    assert ok["connected"] is True and ok["ok"] is True, ok

    MS.upsert(cfg, "unreachable", {"transport": "stdio",
                                   "command": "/bin/echo", "args": []})
    MS.set_enabled(cfg, "unreachable", True)
    bad = MS.test(cfg, "unreachable", timeout_s=3.0)
    assert bad["connected"] is False and bad["ok"] is False, bad


def test_dispatch_test_route_plus_boundary_is_honest(tmp_path, monkeypatch):
    """End-to-end served verdict: the compat dispatch result passed through the
    boundary correction reports ``ok: false`` for an unreachable server and
    ``ok: true`` for a live one."""
    from voicebridge import qubit_compat as HC
    from voicebridge.webui import correct_mcp_test_verdict

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path)

    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers/fake/test", {}, {})
    served = correct_mcp_test_verdict(
        "POST", "/api/mcp/servers/fake/test", code, out)
    assert code == 200 and served["ok"] is True, served

    from voicebridge import mcp_servers as MS
    MS.upsert(cfg, "unreachable", {"transport": "stdio",
                                   "command": "/bin/echo", "args": []})
    MS.set_enabled(cfg, "unreachable", True)
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers/unreachable/test",
                            {}, {})
    served = correct_mcp_test_verdict(
        "POST", "/api/mcp/servers/unreachable/test", code, out)
    assert code == 200 and served["ok"] is False, served
    assert served["connected"] is False, served
