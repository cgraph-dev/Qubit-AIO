"""Objective B/E — MCP read parity across dashboard, CLI, and voice.

All three surfaces must resolve the SAME server set and enabled state from the
ONE canonical authority ``voicebridge.mcp_servers.list_servers``:

- dashboard ``GET /api/mcp/servers`` -> ``qubit_compat.h_mcp_servers``
- CLI ``qubit mcp status`` -> ``integration_status.mcp_status``
- voice ``mcp`` -> ``voice_commands._b_mcp`` -> ``integration_status.mcp_status``

A ``mcp.servers`` config declaration must stay visible read-only on all three,
and only ``env:``/``file:``/``vault:`` references may ever be returned — never a
raw secret. No MCP server is connected, spawned, or networked anywhere here.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, config_body: str = "logging:\n  jsonl: false\n"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text(config_body)
    return C.Config.load(p)


def _seed(cfg):
    """Canonical registry: two reference-env servers + one file-ref server."""
    from voicebridge import mcp_servers as MS

    MS.upsert(cfg, "alpha", {
        "transport": "stdio", "command": "alpha-mcp", "args": ["--x"],
        "env": {"TOKEN": "env:ALPHA_TOKEN"}})
    MS.set_enabled(cfg, "alpha", True)
    MS.upsert(cfg, "beta", {
        "transport": "http", "url": "https://beta.test/mcp", "auth": "header",
        "env": {"TOKEN": "vault:beta-token"}})
    MS.set_enabled(cfg, "beta", False)
    MS.upsert(cfg, "gamma", {
        "transport": "stdio", "command": "gamma-mcp",
        "env": {"CERT": "file:/etc/qubit/gamma.pem"}})
    MS.set_enabled(cfg, "gamma", True)


def _dashboard(cfg):
    from voicebridge import qubit_compat as HC

    code, body = HC.h_mcp_servers(cfg, {}, {}, {})
    assert code == 200, body
    return body["servers"]


def _cli(cfg):
    from voicebridge import cli as CLI
    from voicebridge.integration_status import mcp_status

    assert CLI._cmd_mcp_status(cfg) == 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_mcp_status(cfg) == 0
    printed = json.loads(buf.getvalue())
    direct = mcp_status(cfg)
    # The CLI surface prints exactly what the shared status function returns.
    assert printed == direct, (printed, direct)
    return direct


def _voice(cfg):
    from voicebridge import voice_commands as VC

    return VC._b_mcp(VC.make_ctx(cfg), {})["mcp"]


def _pairs(rows):
    return {(str(r["name"]), bool(r.get("enabled"))) for r in rows}


CONFIG_WITH_DECLARED = (
    "logging:\n  jsonl: false\n"
    "mcp:\n"
    "  servers:\n"
    "    legacy-config:\n"
    "      transport: stdio\n"
    "      command: legacy-mcp\n"
)


def test_three_surfaces_report_same_names_and_enabled_state(tmp_path,
                                                            monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, CONFIG_WITH_DECLARED)
    _seed(cfg)

    dash = _dashboard(cfg)
    cli = _cli(cfg)
    voice = _voice(cfg)

    # Exactly the same server set on all three surfaces (registry + declared).
    assert {s["name"] for s in dash} == {"alpha", "beta", "gamma",
                                         "legacy-config"}
    assert set(cli["servers"]) == {s["name"] for s in dash}
    assert set(voice["servers"]) == set(cli["servers"])

    # Same enabled state on all three surfaces.
    assert _pairs(dash) == _pairs(cli["records"]) == _pairs(voice["records"])
    states = dict(_pairs(dash))
    assert states["alpha"] is True
    assert states["beta"] is False
    assert states["gamma"] is True


def test_config_declared_server_is_visible_read_only_on_all_three(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, CONFIG_WITH_DECLARED)
    _seed(cfg)

    dash = {s["name"]: s for s in _dashboard(cfg)}
    cli = {r["name"]: r for r in _cli(cfg)["records"]}
    voice = {r["name"]: r for r in _voice(cfg)["records"]}

    for rows in (dash, cli, voice):
        assert "legacy-config" in rows, rows
        row = rows["legacy-config"]
        # A config declaration is inventory, never an enabled/connected server.
        assert row["enabled"] is False, row
        assert row["connected"] is False, row
        assert row["source"] == "qubit-config", row

    # The declared name is not registered in the writable store (read-only).
    store = json.loads((Path(cfg.state_dir()) / "mcp_servers.json")
                       .read_text(encoding="utf-8"))
    assert "legacy-config" not in store["servers"]
    assert {"alpha", "beta", "gamma"} <= set(store["servers"])


def test_no_raw_secret_is_returned_only_reference_schemes(tmp_path,
                                                          monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)

    # A raw secret write is refused with the typed secret gate (never stored).
    with pytest.raises(MS.McpServerError) as exc:
        MS.upsert(cfg, "raw", {"transport": "stdio", "command": "raw-mcp",
                               "env": {"TOKEN": "sk-live-DO-NOT-RETURN"}})
    assert exc.value.code == "SECRET_WRITE_REFUSED"

    surfaces = {"dashboard": _dashboard(cfg), "cli": _cli(cfg)["records"],
                "voice": _voice(cfg)["records"]}
    blob = json.dumps(surfaces)
    assert "sk-live" not in blob
    assert "DO-NOT-RETURN" not in blob

    # The dashboard wire shape carries env; every value is a reference only.
    allowed = ("env:", "file:", "vault:")
    env_seen = 0
    for row in surfaces["dashboard"]:
        for key, value in dict(row.get("env") or {}).items():
            env_seen += 1
            assert isinstance(value, str), (key, value)
            assert value.startswith(allowed), (key, value)
    assert env_seen == 3

    # The CLI/voice status records omit env entirely (no secret surface).
    for name in ("cli", "voice"):
        for rec in surfaces[name]:
            assert "env" not in rec, (name, rec)

    # Every reference survives intact on the canonical registry read too.
    registry = {s["name"]: s for s in MS.list_servers(cfg)["servers"]}
    assert registry["alpha"]["env"]["TOKEN"] == "env:ALPHA_TOKEN"
    assert registry["beta"]["env"]["TOKEN"] == "vault:beta-token"
    assert registry["gamma"]["env"]["CERT"] == "file:/etc/qubit/gamma.pem"


def test_all_three_read_through_the_one_authority(tmp_path, monkeypatch):
    """All three surfaces call ``mcp_servers.list_servers`` (not a copy)."""
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch, CONFIG_WITH_DECLARED)
    _seed(cfg)

    calls = {"n": 0}
    real = MS.list_servers

    def counting(c):
        calls["n"] += 1
        return real(c)

    monkeypatch.setattr(MS, "list_servers", counting)

    dash = _dashboard(cfg)
    cli = _cli(cfg)
    voice = _voice(cfg)

    # Each surface resolved through the shared authority at least once.
    assert calls["n"] >= 3, calls
    canonical = {s["name"] for s in real(cfg)["servers"]}
    assert {s["name"] for s in dash} == canonical
    assert set(cli["servers"]) == canonical
    assert set(voice["servers"]) == canonical


def test_mcp_status_records_shape_is_additive(tmp_path, monkeypatch):
    """The names-only ``servers`` list is preserved for existing consumers."""
    from voicebridge.integration_status import mcp_status

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    out = mcp_status(cfg)
    assert out["schema"] == "qubit-mcp-status/1"
    assert isinstance(out["servers"], list)
    assert all(isinstance(n, str) for n in out["servers"])
    assert isinstance(out["records"], list)
    assert [r["name"] for r in out["records"]] == out["servers"]
