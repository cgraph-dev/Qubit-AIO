"""B-H1 — Hermes MCP server CRUD/test/auth are real registry operations.

Qubit does not mount an unauthenticated external MCP server, but the
configuration of those servers is a real, persisted capability on the canonical
Qubit registry (``voicebridge.mcp_servers``). These tests drive the compat
handlers through the real dispatcher and prove state actually changes, secrets
are refused, and no connection is ever fabricated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_mcp_crud_round_trip(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # Create is confirm-gated then persists a real record.
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                            {"name": "fs", "transport": "stdio",
                             "command": "npx", "args": ["-y", "server-fs"]})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                            {"name": "fs", "transport": "stdio",
                             "command": "/nonexistent/mcp-bh1-server",
                             "args": ["-y", "server-fs"],
                             "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["server"]["name"] == "fs"
    assert out["server"]["connected"] is False

    # The write is observable through the canonical GET inventory.
    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    assert code == 200, out
    rows = {s["name"]: s for s in out["servers"]}
    assert "fs" in rows and rows["fs"]["transport"] == "stdio", rows

    # Enable persists the flag.
    code, out = HC.dispatch(cfg, "PUT", "/api/mcp/servers/fs/enabled", {},
                            {"enabled": True})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/mcp/servers/fs/enabled", {},
                            {"enabled": True, "confirm": True})
    assert code == 200 and out["enabled"] is True, (code, out)
    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    rows = {s["name"]: s for s in out["servers"]}
    assert rows["fs"]["enabled"] is True, rows

    # Test validates locally and then probes the live server with a bounded
    # tools/list. An unreachable server is a truthful typed failure -- never a
    # fabricated connection or tool list.
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers/fs/test", {}, {})
    assert code == 200 and out["valid"] is True, (code, out)
    assert out["connected"] is False
    assert out["code"] == "MCP_CONNECT_FAILED", out
    assert out["tools"] == [], out

    # Delete persists removal.
    code, out = HC.dispatch(cfg, "DELETE", "/api/mcp/servers/fs", {},
                            {"confirm": True})
    assert code == 200 and out["removed"] is True, (code, out)
    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    assert "fs" not in {s["name"] for s in out["servers"]}, out

    store = Path(cfg.state_dir()) / "mcp_servers.json"
    assert store.is_file()
    assert json.loads(store.read_text())["servers"] == {}


def test_mcp_auth_accepts_references_only(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                {"name": "http1", "transport": "http",
                 "url": "https://example.test/mcp", "confirm": True})

    # A raw token is refused with the typed secret gate.
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers/http1/auth", {},
                            {"token": "sk-live-raw", "confirm": True})
    assert (code, out["code"]) == (403, "SECRET_WRITE_REFUSED"), (code, out)

    # A vault reference persists and is visible as reference-set (never value).
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers/http1/auth", {},
                            {"reference": "vault:mcp-http1", "confirm": True})
    assert code == 200 and out["reference"] == "vault:mcp-http1", (code, out)
    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers/http1/auth", {}, {})
    assert code == 200 and out["reference_set"] is True, (code, out)


def test_mcp_invalid_config_is_typed_not_success(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # A stdio server without a command is a typed 400, never a stored record.
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                            {"name": "bad", "transport": "stdio",
                             "confirm": True})
    assert (code, out["code"]) == (400, "MCP_BAD_INPUT"), (code, out)
    # An http server with a non-http url is likewise refused.
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                            {"name": "bad2", "transport": "http",
                             "url": "file:///etc/passwd", "confirm": True})
    assert (code, out["code"]) == (400, "MCP_BAD_INPUT"), (code, out)


def test_mcp_operations_are_dispatchable_on_the_single_listener(tmp_path,
                                                                monkeypatch):
    from voicebridge import qubit_compat as HC

    # Every capability resolves to a real implemented route (no MCP_NO_MOUNT).
    for method, path in (
            ("POST", "/api/mcp/servers"),
            ("PUT", "/api/mcp/servers"),
            ("DELETE", "/api/mcp/servers/x"),
            ("POST", "/api/mcp/servers/x/enabled"),
            ("PUT", "/api/mcp/servers/x/enabled"),
            ("POST", "/api/mcp/servers/x/test"),
            ("GET", "/api/mcp/servers/x/auth"),
            ("POST", "/api/mcp/servers/x/auth")):
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "implemented", (method, path, route)
        assert route["handler"] != "__gate__", (method, path, route)
