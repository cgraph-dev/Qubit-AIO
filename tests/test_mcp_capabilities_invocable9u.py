"""Dashboard audit fix — MCP capabilities/enabled/invocable and real reads.

Defects covered:

- ``_mcp_fields_from_body`` dropped ``capabilities``, so a dashboard-added
  server could never be invocable (no allowlist surface).
- ``mcp_servers.upsert`` re-validates and drops ``enabled``, so create-with-
  enabled never persisted and an edit silently disabled an enabled server.
- The dashboard list/detail rows stripped ``invocable``/``capabilities``.
- ``GET /api/mcp`` hardcoded an empty server list, contradicting
  ``/api/mcp/servers``.
- The test endpoint hardcoded ``connected=False``/``network_probe=False`` over
  the real probe result.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_create_carries_capabilities_and_enabled(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/servers", {},
        {"name": "cap-server", "transport": "stdio", "command": "cap-mcp",
         "capabilities": ["echo", "ping"], "enabled": True, "confirm": True})
    assert code == 200, (code, out)
    server = out["server"]
    assert server["capabilities"] == ["echo", "ping"], server
    assert server["enabled"] is True, server
    assert server["invocable"] is True, server
    assert server["invocation_code"] == MS.INVOKE_READY_CODE, server

    # The canonical store really persisted both.
    stored = {s["name"]: s for s in MS.list_servers(cfg)["servers"]}
    assert stored["cap-server"]["capabilities"] == ["echo", "ping"]
    assert stored["cap-server"]["enabled"] is True


def test_update_preserves_enabled_and_allowlist(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                {"name": "keep-on", "transport": "stdio", "command": "a",
                 "capabilities": ["t"], "enabled": True, "confirm": True})

    # Edit without an enabled/capabilities field: neither may be wiped.
    code, out = HC.dispatch(cfg, "PUT", "/api/mcp/servers", {},
                            {"name": "keep-on", "transport": "stdio",
                             "command": "b", "confirm": True})
    assert code == 200, (code, out)
    server = out["server"]
    assert server["enabled"] is True, server
    assert server["capabilities"] == ["t"], server


def test_invalid_capabilities_are_a_typed_refusal(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/servers", {},
        {"name": "bad-caps", "transport": "stdio", "command": "x",
         "capabilities": ["not a tool name!"], "confirm": True})
    assert code == 400 and out["code"] == "MCP_BAD_INPUT", (code, out)
    assert "bad-caps" not in {s["name"]
                              for s in MS.list_servers(cfg)["servers"]}


def test_dashboard_rows_include_invocable_and_capabilities(tmp_path,
                                                           monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                {"name": "row-server", "transport": "stdio", "command": "r",
                 "capabilities": ["a", "b"], "enabled": True, "confirm": True})

    code, listed = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    assert code == 200, (code, listed)
    row = next(s for s in listed["servers"] if s["name"] == "row-server")
    assert row["capabilities"] == ["a", "b"], row
    assert row["invocable"] is True, row
    assert row["invocation_code"], row


def test_bare_mcp_route_returns_the_real_list(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # Empty is honest when nothing is registered.
    code, empty = HC.dispatch(cfg, "GET", "/api/mcp", {}, {})
    assert code == 200 and empty["servers"] == [], empty

    HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                {"name": "bare", "transport": "stdio", "command": "c",
                 "confirm": True})
    code, populated = HC.dispatch(cfg, "GET", "/api/mcp", {}, {})
    assert code == 200, (code, populated)
    assert [s["name"] for s in populated["servers"]] == ["bare"], populated
    # Must agree with the canonical route.
    code, canonical = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    assert populated["servers"] == canonical["servers"]


def test_test_endpoint_reports_the_real_probe_result(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                {"name": "probe", "transport": "stdio", "command": "p",
                 "capabilities": ["t"], "enabled": True, "confirm": True})

    real = {"state": "OK", "code": MS.DISCOVER_PROBE_CODE, "name": "probe",
            "valid": True, "connected": True, "tools": ["t1", "t2"],
            "errors": [], "probe": "tools/list (live)", "network_probe": True,
            "detail": "live tools/list returned 2 tool(s)"}
    monkeypatch.setattr(MS, "test", lambda c, n, **k: real)

    code, out = HC.h_mcp_test_local(cfg, {}, {}, {"name": "probe"})
    assert code == 200, (code, out)
    assert out["connected"] is True, out
    assert out["network_probe"] is True, out
    assert out["probe"] == "tools/list (live)", out
    assert out["tools"] == ["t1", "t2"], out
