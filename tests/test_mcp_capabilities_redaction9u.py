"""9u — MCP capability redaction + honest test result.

Two audit defects (isolated Qubit :8895, 2026-09-29):

1. MED — ``webui._redact_served`` used a *global* seen-set of object ids, so any
   acyclic value that was legitimately *aliased* under two keys (exactly what the
   MCP capability reader does: the same list is served as ``result.<key>`` and as
   the top-level ``<key>``) was replaced with ``"[REDACTED]"``. The panel then
   rendered a single redacted entry despite real data. Fixed by tracking object
   ids on the current recursion *path* only, so true cycles still redact.
2. MED — ``mcp_servers.test`` reported a locally-valid probe as a success; the
   compat handler hardcoded ``ok=True``, so an unreachable server rendered as a
   green "Connected — no tools". ``mcp_servers.test`` now carries the honest
   ``ok`` flag (``True`` only when a live probe actually connected).

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
            {"uri": "file:///a", "name": "a"},
            {"uri": "file:///b", "name": "b"}]}})
    elif method == "prompts/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"prompts": [
            {"name": "greet"}]}})
    elif method == "resources/templates/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"resourceTemplates": [
            {"uriTemplate": "file:///{p}", "name": "readme"}]}})
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


def _seed_stdio(cfg, tmp_path, *, enabled=True):
    from voicebridge import mcp_servers as MS

    script = tmp_path / "fake_mcp_redact.py"
    script.write_text(FAKE_STDIO_SERVER, encoding="utf-8")
    MS.upsert(cfg, "fake", {"transport": "stdio", "command": sys.executable,
                            "args": [str(script)]})
    MS.set_enabled(cfg, "fake", enabled)
    return script


# ---------------------------------------------------------------------------
# Defect 2 — transport redaction must not redact legitimately aliased data
# ---------------------------------------------------------------------------
def test_aliased_but_acyclic_list_is_preserved():
    from voicebridge.webui import _redact_served

    shared = [{"uri": "qa://greeting", "name": "greeting"}]
    payload = {"ok": True, "names": shared,
               "result": {"resources": shared}, "resources": shared}
    served = _redact_served(payload)
    assert served["resources"] == [{"uri": "qa://greeting",
                                    "name": "greeting"}], served
    assert served["resources"] == served["result"]["resources"], served
    assert "[REDACTED]" not in json.dumps(served), served


def test_aliased_but_acyclic_dict_is_preserved():
    from voicebridge.webui import _redact_served

    shared = {"x": 1, "nested": {"y": 2}}
    served = _redact_served({"a": shared, "b": shared})
    assert served["a"] == {"x": 1, "nested": {"y": 2}}, served
    assert served["b"] == served["a"], served
    assert "[REDACTED]" not in json.dumps(served), served


def test_true_cycle_still_redacts():
    from voicebridge.webui import _redact_served

    d = {}
    d["self"] = d
    assert _redact_served(d) == {"self": "[REDACTED]"}

    cycle_list = []
    cycle_list.append(cycle_list)
    assert _redact_served(cycle_list) == ["[REDACTED]"]


def test_secret_keys_still_masked_on_aliased_values():
    from voicebridge.webui import _redact_served

    shared = {"password": "hunter2", "secret": False}
    served = _redact_served({"a": shared, "b": shared})
    assert served["a"]["password"] == "[REDACTED]", served
    # non-string value under a credential key is a schema flag, not a secret
    assert served["a"]["secret"] is False, served
    assert served["b"] == served["a"], served


# ---------------------------------------------------------------------------
# Defect 2 end-to-end — the real capability route's aliased list survives
# ---------------------------------------------------------------------------
def test_capability_route_list_survives_transport_redaction(tmp_path,
                                                            monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.webui import _redact_served

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=True)

    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers/fake/resources",
                            {}, {})
    assert code == 200 and out["state"] == "OK" and out["connected"] is True, out
    assert out["resources"] == out["result"]["resources"], out

    served = _redact_served(out)
    assert [r["uri"] for r in served["resources"]] == \
        ["file:///a", "file:///b"], served
    assert served["resources"] == served["result"]["resources"], served
    assert "[REDACTED]" not in json.dumps(served), served


# ---------------------------------------------------------------------------
# Defect 1 — mcp_servers.test carries the honest ok flag
# ---------------------------------------------------------------------------
def test_test_reports_ok_false_when_probe_cannot_connect(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    # /bin/echo accepts stdin then exits: initialize never gets a reply.
    MS.upsert(cfg, "unreachable", {"transport": "stdio",
                                   "command": "/bin/echo", "args": []})
    MS.set_enabled(cfg, "unreachable", True)

    out = MS.test(cfg, "unreachable", timeout_s=3.0)
    assert out["connected"] is False, out
    assert out["ok"] is False, out
    assert out["tools"] == [], out
    assert out["state"] == "UNREACHABLE", out
    assert out["code"] == "MCP_CONNECT_FAILED", out


def test_test_reports_ok_true_only_when_live_probe_connects(tmp_path,
                                                           monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_stdio(cfg, tmp_path, enabled=True)

    out = MS.test(cfg, "fake")
    assert out["connected"] is True, out
    assert out["ok"] is True, out
    assert out["tools"] == ["echo"], out
    assert out["network_probe"] is True, out


def test_test_reports_ok_false_for_inventory_only_server(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    cfg.data.setdefault("mcp", {})["servers"] = ["declared-only"]

    out = MS.test(cfg, "declared-only")
    assert out["connected"] is False, out
    assert out["ok"] is False, out
    assert out["probe"] == "local-validation", out
