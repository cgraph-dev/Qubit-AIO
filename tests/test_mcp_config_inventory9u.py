"""MCP inventory merges the runtime config.yaml instead of hiding live servers.

Regression: ``voicebridge.mcp_servers.config_server_names`` read only the
voicebridge ``voice.yaml``, so servers the runtime actually loads from
``<QUBIT_HOME>/config.yaml`` (``rea``, ``memelord``) never reached the canonical
inventory — or appeared as ``enabled: false`` with no transport. The inventory
now reads the same top-level ``mcp_servers`` map as the MCP client, carrying
the real transport/enabled state read-only.

Also pins the parked/failed visibility path: cached runtime state is merged
without opening a connection, so a failure is visible without a retry storm.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

RUNTIME_CONFIG = """\
mcp_servers:
  rea:
    command: rea
    args:
      - --mcp
    enabled: true
  memelord:
    url: https://www.memelord.com/api/mcp
    enabled: true
  atlassian:
    url: https://mcp.atlassian.com/v1/mcp/authv2
    enabled: false
  with_secret_env:
    command: secret-mcp
    enabled: true
    env:
      API_TOKEN: sk-live-DO-NOT-RETURN
"""

LEGACY_ONLY = """\
logging:
  jsonl: false
mcp:
  servers:
    legacy-config:
      transport: stdio
      command: legacy-mcp
"""


def _cfg(tmp_path, monkeypatch, *, runtime_yaml: str | None = RUNTIME_CONFIG,
         voice_yaml: str = "logging:\n  jsonl: false\n"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "qhome"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    if runtime_yaml is not None:
        (home / "config.yaml").write_text(runtime_yaml, encoding="utf-8")
    p = tmp_path / "voice.yaml"
    p.write_text(voice_yaml, encoding="utf-8")
    return C.Config.load(p)


def _rows(cfg):
    from voicebridge import mcp_servers as MS

    return {r["name"]: r for r in MS.list_servers(cfg)["servers"]}


def test_runtime_config_servers_carry_transport_and_enabled(tmp_path,
                                                            monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)

    assert MS.config_server_names(cfg) == [
        "atlassian", "memelord", "rea", "with_secret_env"]
    rows = _rows(cfg)

    rea = rows["rea"]
    assert rea["enabled"] is True, rea
    assert rea["transport"] == "stdio" and rea["command"] == "rea", rea
    assert rea["args"] == ["--mcp"], rea
    assert rea["source"] == "qubit-config", rea
    assert rea["connected"] is False and rea["invocable"] is False, rea

    memelord = rows["memelord"]
    assert memelord["enabled"] is True, memelord
    assert memelord["transport"] == "http", memelord
    assert memelord["url"] == "https://www.memelord.com/api/mcp", memelord

    assert rows["atlassian"]["enabled"] is False, rows["atlassian"]
    assert rows["atlassian"]["transport"] == "http", rows["atlassian"]

    # A literal env value in config.yaml never reaches the served inventory.
    assert rows["with_secret_env"]["env"] == {}, rows["with_secret_env"]
    assert "sk-live" not in json.dumps(rows)


def test_config_inventory_is_read_only_and_store_still_wins(tmp_path,
                                                            monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "rea", {"transport": "stdio", "command": "rea-local",
                           "enabled": True})

    # The writable registry is authoritative for a same-name server.
    assert _rows(cfg)["rea"]["command"] == "rea-local"
    # The runtime config file was never rewritten by the read.
    raw = (tmp_path / "qhome" / "config.yaml").read_text(encoding="utf-8")
    assert "command: rea\n" in raw


def test_legacy_nested_shape_stays_inventory_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, runtime_yaml=None,
               voice_yaml=LEGACY_ONLY)
    rows = _rows(cfg)
    assert "legacy-config" in rows, rows
    row = rows["legacy-config"]
    # The runtime client does not load the nested shape: visible but not on.
    assert row["enabled"] is False, row
    assert row["command"] == "legacy-mcp" and row["source"] == "qubit-config", row


def test_cli_status_sees_the_runtime_servers(tmp_path, monkeypatch):
    from voicebridge.integration_status import mcp_status

    cfg = _cfg(tmp_path, monkeypatch)
    out = mcp_status(cfg)
    records = {r["name"]: r for r in out["records"]}
    assert records["rea"]["enabled"] is True, records["rea"]
    assert records["memelord"]["enabled"] is True, records["memelord"]
    assert records["atlassian"]["enabled"] is False, records["atlassian"]


def test_listing_never_opens_a_connection(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise AssertionError("inventory must not connect to an MCP server")

    for name in ("discover_tools", "call_server", "list_resources",
                 "read_resource", "list_prompts", "list_prompt",
                 "get_prompt", "list_resource_templates"):
        monkeypatch.setattr(MC, name, boom, raising=False)

    rows = _rows(cfg)
    assert {"rea", "memelord"} <= set(rows)


def test_cached_parked_state_is_visible_without_probing(tmp_path,
                                                        monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        MS, "_cached_runtime_status",
        lambda configured: {
            "rea": {"name": "rea", "status": "failed", "connected": False,
                    "error": "auth rejected (401)"},
            "memelord": {"name": "memelord", "status": "connected",
                         "connected": True},
        })

    rows = _rows(cfg)
    assert rows["rea"]["status"] == "failed", rows["rea"]
    assert rows["rea"]["error"] == "auth rejected (401)", rows["rea"]
    assert rows["rea"]["connected"] is False, rows["rea"]
    assert rows["memelord"]["connected"] is True, rows["memelord"]


def test_cached_status_helper_uses_only_cached_state(tmp_path, monkeypatch):
    """``_cached_runtime_status`` delegates to the read-only status reader."""
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_runtime as QR

    QRI = QR.ensure_path()
    assert QRI
    from tools import mcp_tool_discovery as MTD

    seen = {}

    def fake_status(configured=None, *, include_runtime=True):
        seen["configured"] = configured
        seen["include_runtime"] = include_runtime
        return [{"name": "rea", "status": "unavailable", "connected": False,
                 "error": "missing credential"}]

    monkeypatch.setattr(MTD, "get_mcp_status", fake_status)
    out = MS._cached_runtime_status({"rea": {"transport": "stdio",
                                             "enabled": True}})
    assert out["rea"]["status"] == "unavailable"
    assert seen["configured"] == {"rea": {"transport": "stdio",
                                          "enabled": True}}
    assert seen["include_runtime"] is True
