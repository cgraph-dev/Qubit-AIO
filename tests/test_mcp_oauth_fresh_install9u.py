"""Fresh MCP installs keep the OAuth marker, and parked OAuth state is actionable.

Two live defects this pins:
1. The dashboard catalog installer registered ``url`` only, so a shipped
   OAuth entry (Airtable) reached ``config.yaml`` without ``auth: oauth`` and
   the runtime could never build a provider. The CLI path was already correct;
   the dashboard path dropped it.
2. ``GET /api/mcp/servers`` dropped the cached parked ``error``, so ``memelord``
   (configured OAuth, no cached token) rendered as a bare "not connected" with
   no ``qubit mcp login memelord`` action. A separate dashboard process has no
   runtime state, so the token cache itself supplies the honest typed state.

Hermetic: temp XDG/QUBIT_HOME only; no live service, config, or network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit

_AIRTABLE = "https://mcp.airtable.com/mcp"
_MEMELORD = "https://www.memelord.com/api/mcp"

RUNTIME_CONFIG = f"""\
mcp_servers:
  memelord:
    url: {_MEMELORD}
    auth: oauth
    enabled: true
  airtable:
    url: {_AIRTABLE}
    enabled: true
"""


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    home = tmp_path / "qhome"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    (home / "config.yaml").write_text(RUNTIME_CONFIG, encoding="utf-8")
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p), home


def test_catalog_install_fields_carry_the_oauth_marker():
    from voicebridge import qubit_compat as HC

    entry = SimpleNamespace(
        transport=SimpleNamespace(type="http", url=_AIRTABLE, command=None, args=[]),
        auth=SimpleNamespace(type="oauth", oauth={}),
    )
    fields = HC._catalog_install_fields(entry, inline={}, enabled=True)
    assert fields == {"transport": "http", "enabled": True,
                      "source": "mcp-catalog", "url": _AIRTABLE,
                      "auth": "oauth"}, fields


def test_catalog_install_fields_carry_a_preregistered_oauth_block():
    from voicebridge import qubit_compat as HC

    entry = SimpleNamespace(
        transport=SimpleNamespace(type="http", url="https://example.test/mcp",
                                  command=None, args=[]),
        auth=SimpleNamespace(type="oauth", oauth={"client_id": "abc",
                                                   "redirect_port": 9191}),
    )
    fields = HC._catalog_install_fields(entry, inline={}, enabled=True)
    assert fields["auth"] == "oauth"
    assert fields["oauth"] == {"client_id": "abc", "redirect_port": 9191}


def test_registry_sync_writes_the_oauth_marker_to_runtime_config(tmp_path, monkeypatch):
    import qubit_yaml as yaml

    from voicebridge import mcp_servers as MS

    cfg, home = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "airtable",
              {"transport": "http", "url": _AIRTABLE, "auth": "oauth",
               "oauth": {"client_id": "abc"}, "enabled": True},
              confirmed=True)

    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    entry = raw["mcp_servers"]["airtable"]
    assert entry["auth"] == "oauth", entry
    assert entry["oauth"] == {"client_id": "abc"}, entry
    assert entry["url"] == _AIRTABLE


def test_registry_rejects_oauth_block_without_oauth_auth():
    from voicebridge import mcp_servers as MS

    with pytest.raises(MS.McpServerError) as info:
        MS._validate({"transport": "http", "url": _AIRTABLE,
                      "auth": "none", "oauth": {"client_id": "abc"}})
    assert getattr(info.value, "code", "") == "MCP_BAD_INPUT"


def test_unconfigured_oauth_server_is_typed_login_required(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg, _home = _cfg(tmp_path, monkeypatch)
    # A separate dashboard process has no runtime state.
    monkeypatch.setattr(MS, "_cached_runtime_status", lambda configured: {})

    rows = {r["name"]: r for r in MS.list_servers(cfg)["servers"]}

    # The legacy Airtable row (URL only) is reconciled against the reviewed
    # catalog entry: it must say what is missing and how to fix it, not render
    # as a healthy "configured" server.
    airtable = rows["airtable"]
    assert airtable["status"] == "misconfigured-oauth", airtable
    assert airtable["connected"] is False
    assert "auth: oauth" in airtable["error"], airtable
    assert "qubit mcp login airtable" in airtable["error"], airtable

    # memelord declares auth: oauth but has no cached token either.
    memelord = rows["memelord"]
    assert memelord["status"] == "login-required", memelord
    assert "qubit mcp login memelord" in memelord["error"], memelord


def test_served_inventory_carries_the_parked_reason(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg, _home = _cfg(tmp_path, monkeypatch)
    reason = ("MCP server 'memelord' requires an interactive OAuth login and no "
              "cached OAuth credential was found. Run `qubit mcp login memelord`")
    monkeypatch.setattr(
        MS, "list_servers",
        lambda cfg: {"schema": "x", "servers": [{
            "name": "memelord", "transport": "http", "url": _MEMELORD,
            "command": None, "args": [], "env": {}, "auth": "oauth",
            "enabled": True, "connected": False, "status": "unavailable",
            "error": reason, "source": "qubit-config",
        }], "total": 1})

    code, out = HC.h_mcp_servers(cfg, {}, {}, {})
    assert code == 200
    row = out["servers"][0]
    assert row["status"] == "unavailable"
    assert "qubit mcp login memelord" in row["error"]
    assert "interactive OAuth login" in row["error"]


def test_dashboard_catalog_install_of_airtable_writes_oauth(tmp_path, monkeypatch):
    """End-to-end dashboard install persists the marker the runtime reads."""
    import qubit_yaml as yaml

    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    cfg, home = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/catalog/install", {},
                            {"name": "airtable"})
    assert code == 409, out
    res = OPS.confirm_op(cfg, out["confirmation_id"], actor="dashboard:user",
                         session="dashboard")
    assert res.get("state") == "executed", res

    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    entry = raw["mcp_servers"]["airtable"]
    assert entry["auth"] == "oauth", entry
    assert entry["url"] == _AIRTABLE
