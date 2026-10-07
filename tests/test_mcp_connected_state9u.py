"""9U — MCP ``connected`` reflects the real cached discovery state.

Regression: ``GET /agent/api/mcp/servers`` reported ``connected: false`` for
every enabled server even though each answers a live ``tools/list`` — the field
was hardcoded at two layers (``mcp_servers._public`` and
``qubit_compat.h_mcp_servers``).

``connected`` now derives from the SAME cross-process record the agent's MCP
tool layer consults: the vendored per-server schema cache
(``tools/mcp_schema_cache``), which is written ONLY after a successful live
``tools/list``. A server whose discovery was recorded reads connected; an
unreachable / never-discovered server stays honestly false; a corrupt or
unreadable cache never raises. The list read opens no connection of its own.

Hermetic: an isolated ``QUBIT_HOME`` so the real operator cache is never read.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: The served row's exact key set — the fix must not add/remove any field.
EXPECTED_ROW_KEYS = {
    "name", "transport", "auth", "command", "args", "url", "env",
    "capabilities", "enabled", "connected", "invocable", "invocation_code",
    "status", "source",
}


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


def _seed(cfg):
    """One reachable-intent server (cache-able) + one unreachable server."""
    from voicebridge import mcp_servers as MS

    MS.upsert(cfg, "live", {"transport": "http", "url": "https://live.test/mcp",
                            "capabilities": ["t1"], "enabled": True})
    MS.upsert(cfg, "dead", {"transport": "stdio", "command": "/nonexistent/mcp",
                            "capabilities": ["t1"], "enabled": True})
    return "live", "dead"


def _record_discovery(name: str, tools=None) -> None:
    """Record a live discovery through the real vendored cache writer."""
    from voicebridge import qubit_runtime as QR

    QR.ensure_path()
    from tools.mcp_schema_cache import config_fingerprint, write_cache_entry

    write_cache_entry(
        name, config_fingerprint({"url": "https://live.test/mcp"}),
        tools=tools or [{"name": "t1", "description": "", "inputSchema": {}}])


def _cache_path():
    from voicebridge import qubit_runtime as QR

    QR.ensure_path()
    from qubit_constants import get_qubit_home

    return Path(get_qubit_home()) / "cache" / "mcp_schema_cache.json"


# ---------------------------------------------------------------------------
# Real state: recorded discovery => connected
# ---------------------------------------------------------------------------
def test_recorded_discovery_marks_server_connected(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    live, dead = _seed(cfg)
    _record_discovery(live)

    rows = {r["name"]: r for r in MS.list_servers(cfg)["servers"]}
    assert rows[live]["connected"] is True, rows[live]
    assert rows[live]["status"] == "connected", rows[live]
    assert rows[dead]["connected"] is False, rows[dead]
    assert rows[dead]["status"] == "configured-not-connected", rows[dead]


def test_dashboard_list_carries_real_connected(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    live, dead = _seed(cfg)
    _record_discovery(live)

    code, body = HC.h_mcp_servers(cfg, {}, {}, {})
    assert code == 200, body
    rows = {s["name"]: s for s in body["servers"]}
    assert rows[live]["connected"] is True, rows[live]
    assert rows[dead]["connected"] is False, rows[dead]


def test_never_discovered_server_is_disconnected(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    assert all(r["connected"] is False
               for r in MS.list_servers(cfg)["servers"])


# ---------------------------------------------------------------------------
# Fail-soft: never raise
# ---------------------------------------------------------------------------
def test_corrupt_cache_never_raises_and_reads_disconnected(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    cache = _cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not valid json", encoding="utf-8")

    rows = MS.list_servers(cfg)["servers"]  # must not raise
    assert all(r["connected"] is False for r in rows)


def test_cache_read_failure_never_raises(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_runtime as QR

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    QR.ensure_path()
    import tools.mcp_schema_cache as SC

    def _boom():
        raise RuntimeError("cache exploded")

    monkeypatch.setattr(SC, "_load_all", _boom)
    rows = MS.list_servers(cfg)["servers"]  # must not raise
    assert all(r["connected"] is False for r in rows)


def test_missing_vendored_module_never_raises(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_runtime as QR

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)

    def _boom():
        raise RuntimeError("fork unavailable")

    monkeypatch.setattr(QR, "ensure_path", _boom)
    rows = MS.list_servers(cfg)["servers"]  # must not raise
    assert all(r["connected"] is False for r in rows)


# ---------------------------------------------------------------------------
# Fast + shape-stable
# ---------------------------------------------------------------------------
def test_list_opens_no_connection(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)

    calls = []

    def _record(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(MC, "call_method", _record)
    monkeypatch.setattr(MC, "discover_tools", _record)
    MS.list_servers(cfg)
    assert calls == [], calls


def test_list_row_shape_unchanged(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    live, _dead = _seed(cfg)
    _record_discovery(live)

    listing = MS.list_servers(cfg)
    assert set(listing.keys()) == {"schema", "servers", "total"}
    for row in listing["servers"]:
        assert set(row.keys()) == EXPECTED_ROW_KEYS, row


def test_invocation_status_reports_real_connected(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    live, _dead = _seed(cfg)
    _record_discovery(live)

    st = MS.invocation_status(cfg, live)
    assert st["invocable"] is True, st
    assert st["connected"] is True, st
