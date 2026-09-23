"""9U — MCP registry field round-trips + configurable timeouts.

Regressions for the dashboard audit findings on the canonical MCP store
(:mod:`voicebridge.mcp_servers`) and its brokered client
(:mod:`voicebridge.mcp_client`):

* ``upsert`` must carry ``enabled`` through validation, so a create-with-enabled
  persists and editing an enabled server never silently disables it (D-2);
* ``upsert`` must validate and retain a ``capabilities`` tool allowlist so a
  dashboard-created server can become invocable (D-1, store half);
* ``test`` must report the real ``connected``/``network_probe`` result, not a
  hardcoded false (D-3, store half — the HTTP override lives in qubit_compat);
* the probe/invoke timeouts accept optional ``mcp.*`` config overrides, clamped
  to the client hard cap (D-11).

Hermetic: the stdio server is a small stdlib python script in ``tmp_path``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAKE_STDIO_SERVER = r'''
import json, sys

TOOLS = [{"name": "echo"}, {"name": "danger"}]


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
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"}}})
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    else:
        _send({"jsonrpc": "2.0", "id": mid,
               "error": {"code": -32601, "message": "method not found"}})
'''


def _cfg(tmp_path, monkeypatch, *, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _seed_stdio(cfg, tmp_path, *, capabilities=None, enabled=None):
    script = tmp_path / "fake_mcp_fields.py"
    script.write_text(FAKE_STDIO_SERVER, encoding="utf-8")
    fields = {"transport": "stdio", "command": sys.executable,
              "args": [str(script)]}
    if capabilities is not None:
        fields["capabilities"] = capabilities
    if enabled is not None:
        fields["enabled"] = enabled
    return script, fields


# ---------------------------------------------------------------------------
# D-2: enabled round-trips
# ---------------------------------------------------------------------------
def test_upsert_persists_enabled_on_create(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    out = MS.upsert(cfg, "srv", {"transport": "stdio", "command": "cat",
                                 "capabilities": ["echo"], "enabled": True})
    assert out["server"]["enabled"] is True, out

    raw = json.loads((Path(cfg.state_dir()) / MS.STORE_NAME).read_text())
    assert raw["servers"]["srv"]["enabled"] is True, raw

    row = next(s for s in MS.list_servers(cfg)["servers"]
               if s["name"] == "srv")
    assert row["enabled"] is True, row


def test_upsert_update_preserves_enabled(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "srv", {"transport": "stdio", "command": "cat",
                           "capabilities": ["echo"]})
    MS.set_enabled(cfg, "srv", True)
    assert MS.list_servers(cfg)["servers"][0]["enabled"] is True

    # An edit that does not mention `enabled` must not disable the server.
    MS.upsert(cfg, "srv", {"transport": "stdio", "command": "cat2",
                           "args": ["--x"]})
    row = MS.list_servers(cfg)["servers"][0]
    assert row["enabled"] is True, row
    assert row["command"] == "cat2" and row["args"] == ["--x"], row

    # An explicit `enabled: false` is still honoured.
    MS.upsert(cfg, "srv", {"transport": "stdio", "command": "cat2",
                           "enabled": False})
    assert MS.list_servers(cfg)["servers"][0]["enabled"] is False


# ---------------------------------------------------------------------------
# D-1 (store half): capabilities allowlist round-trips
# ---------------------------------------------------------------------------
def test_upsert_retains_capabilities_allowlist(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "srv", {"transport": "stdio", "command": "cat",
                           "capabilities": ["echo", "danger"],
                           "enabled": True})
    row = MS.list_servers(cfg)["servers"][0]
    assert row["capabilities"] == ["echo", "danger"], row
    assert row["invocable"] is True and MC.is_invocable(row) is True, row

    raw = json.loads((Path(cfg.state_dir()) / MS.STORE_NAME).read_text())
    assert raw["servers"]["srv"]["capabilities"] == ["echo", "danger"], raw

    # The `allowed_tools` alias is normalized onto `capabilities`.
    MS.upsert(cfg, "srv2", {"transport": "stdio", "command": "cat",
                            "allowed_tools": ["only"]})
    row2 = next(s for s in MS.list_servers(cfg)["servers"]
                if s["name"] == "srv2")
    assert row2["capabilities"] == ["only"], row2


# ---------------------------------------------------------------------------
# D-3 (store half): test reports the real live probe result
# ---------------------------------------------------------------------------
def test_test_reports_real_connected_and_network_probe(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    _script, fields = _seed_stdio(cfg, tmp_path, capabilities=["echo"],
                                  enabled=True)
    MS.upsert(cfg, "fake", fields)

    out = MS.test(cfg, "fake")
    assert out["state"] == "OK", out
    assert out["connected"] is True, out
    assert out["network_probe"] is True, out
    assert out["probe"] == "tools/list (live)", out
    assert out["tools"] == ["echo", "danger"], out

    # A config-declared server that is never probed is truthfully not connected.
    (tmp_path / "other").mkdir(exist_ok=True)
    cfg2 = _cfg(tmp_path / "other", monkeypatch)
    cfg2.data.setdefault("mcp", {})["servers"] = ["declared-only"]
    local = MS.test(cfg2, "declared-only")
    assert local["connected"] is False and local["network_probe"] is False
    assert local["probe"] == "local-validation"


# ---------------------------------------------------------------------------
# D-11: configurable, bounded timeouts
# ---------------------------------------------------------------------------
def test_configurable_timeouts_are_bounded(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch,
               extra="mcp:\n  timeout_s: 12.5\n  probe_timeout_s: 3.0\n")
    assert MS._cfg_timeout(cfg, "probe_timeout_s", MS.PROBE_TIMEOUT_S) == 3.0
    assert MC._config_default_timeout(cfg) == 12.5

    # Absent config keeps the hardcoded default.
    (tmp_path / "plain").mkdir(exist_ok=True)
    plain = _cfg(tmp_path / "plain", monkeypatch)
    assert MS._cfg_timeout(plain, "probe_timeout_s",
                           MS.PROBE_TIMEOUT_S) == MS.PROBE_TIMEOUT_S
    assert MC._config_default_timeout(plain) == MC.DEFAULT_TIMEOUT_S

    # An over-cap value is clamped to the client hard cap; a bad value falls back.
    (tmp_path / "over").mkdir(exist_ok=True)
    over = _cfg(tmp_path / "over", monkeypatch,
                extra="mcp:\n  timeout_s: 99999\n  probe_timeout_s: nope\n")
    assert MC._config_default_timeout(over) == MC.MAX_TIMEOUT_S
    assert MS._cfg_timeout(over, "probe_timeout_s",
                           MS.PROBE_TIMEOUT_S) == MS.PROBE_TIMEOUT_S
