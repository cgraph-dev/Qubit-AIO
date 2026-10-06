"""The dashboard MCP store and the runtime MCP config must agree.

The dashboard reads/writes ``<state_dir>/mcp_servers.json``
(``voicebridge.mcp_servers``) while the vendored runtime connects the servers in
``<runtime_home>/config.yaml`` ``mcp_servers`` (read by
``tools/mcp_tool_common.mcp_server_enabled``). ``mcp_set_enabled`` used to write
only the JSON store, so a dashboard toggle did not change what the runtime
connected until both were hand-edited. Every write now mirrors to config.yaml,
and a failed mirror rolls the dashboard store back so they cannot diverge.
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
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _json_enabled(cfg, name):
    data = json.loads((cfg.state_dir() / "mcp_servers.json").read_text())
    return data["servers"][name]["enabled"]


def _runtime_servers(cfg):
    from voicebridge import mcp_servers as MS

    return MS._load_runtime_config(cfg).get("mcp_servers", {})


def test_toggle_is_reflected_in_both_stores(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "alpha", {"transport": "http", "url": "https://alpha.test/mcp"})
    MS.set_enabled(cfg, "alpha", True)

    # The runtime's own reader agrees, so a real connection is on. Imported here
    # because the vendored tree is put on sys.path by the first write above.
    from tools.mcp_tool_common import mcp_server_enabled

    assert _json_enabled(cfg, "alpha") is True
    entry = _runtime_servers(cfg)["alpha"]
    assert entry["enabled"] is True
    assert entry["url"] == "https://alpha.test/mcp"
    assert mcp_server_enabled(entry) is True

    MS.set_enabled(cfg, "alpha", False)
    assert _json_enabled(cfg, "alpha") is False
    assert _runtime_servers(cfg)["alpha"]["enabled"] is False
    assert mcp_server_enabled(_runtime_servers(cfg)["alpha"]) is False


def test_delete_removes_from_both_stores(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "beta", {"transport": "stdio", "command": "beta-mcp"})
    MS.set_enabled(cfg, "beta", True)
    assert "beta" in _runtime_servers(cfg)

    MS.delete(cfg, "beta")
    assert "beta" not in _runtime_servers(cfg)


def test_upsert_preserves_operator_fields_on_an_existing_runtime_entry(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_runtime as QR

    cfg = _cfg(tmp_path, monkeypatch)
    QR.runtime_home_for_cfg(cfg)
    QR.ensure_path()
    from qubit_cli.config import load_config, save_config

    config = load_config()
    config.setdefault("mcp_servers", {})["gamma"] = {
        "command": "gamma-mcp", "oauth": {"scope": "x"}, "enabled": False,
    }
    save_config(config)

    MS.upsert(cfg, "gamma", {"transport": "stdio", "command": "gamma-mcp"})
    MS.set_enabled(cfg, "gamma", True)

    entry = _runtime_servers(cfg)["gamma"]
    assert entry["enabled"] is True
    assert entry["oauth"] == {"scope": "x"}  # runtime-owned field preserved
    assert entry["command"] == "gamma-mcp"


def test_mirror_failure_rolls_back_the_dashboard_store(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge.mcp_servers import McpServerError

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "delta", {"transport": "stdio", "command": "delta-mcp"})
    before = _json_enabled(cfg, "delta")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("runtime store unavailable")

    monkeypatch.setattr(MS, "_sync_runtime_upsert", _boom)
    with pytest.raises(McpServerError) as excinfo:
        MS.set_enabled(cfg, "delta", not before)

    assert excinfo.value.code == "MCP_RUNTIME_SYNC_FAILED"
    assert _json_enabled(cfg, "delta") is before
