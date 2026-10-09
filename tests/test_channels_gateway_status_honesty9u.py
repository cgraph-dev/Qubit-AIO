"""Channels status honesty vs the LIVE gateway (audit fix-channels-2, 2026-10-10).

The user-facing ``qubit channels status`` / ``qubit channels doctor`` surface
read only the Qubit *inbound-framework* registry (``channels-inbound.json``)
and reported ``connected: 0`` / ``configured: 0`` / ``listener: NONE`` -- while
the live Qubit gateway (the authority that actually knows what is connected)
reported discord/telegram/whatsapp ``connected``.

Two stores, two different questions:

* ``channels-inbound.json`` -- the inbound *credential-reference* registry
  (Qubit's framework intent), and
* the gateway's own ``gateway_state.json`` (via
  ``gateway_supervisor.daemon_runtime_status``) -- the live transport's own
  view of itself, which IS the authority for "is this channel connected".

Both are read-only, first-party, and never fabricated. The fix makes the
output honest: it reports the authoritative gateway view, clearly labelled,
instead of silently showing 0 while the gateway is connected.

Hermetic: throwaway XDG/HOME tree; the gateway read is injected, no daemon,
no network, no systemctl.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return Config.load(p)


def _live(platforms):
    """A daemon_runtime_status-shaped record with the given platform map."""
    return {
        "gateway_state": "running",
        "platforms": len(platforms),
        "platforms_map": platforms,
        "connected_platforms": sum(
            1 for v in platforms.values()
            if str(v.get("state", "")).lower() in ("connected", "running", "ok")),
    }


_CONNECTED = {
    "discord": {"state": "connected"},
    "telegram": {"state": "connected"},
    "whatsapp": {"state": "connected"},
}


# --------------------------------------------------------------- status
def test_status_reports_live_gateway_platforms(tmp_path, monkeypatch):
    """When the gateway is connected the status must not silently say 0."""
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    out = CI.status(cfg, gateway_reader=lambda: _live(_CONNECTED))
    gw = out["gateway"]
    assert gw["authoritative_source"] == "gateway_state.json"
    assert gw["state"] == "running"
    assert gw["connected"] == 3
    assert gw["connected_platforms"] == ["discord", "telegram", "whatsapp"]
    assert gw["readable"] is True


def test_status_top_level_connected_is_not_zero_when_gateway_connected(
        tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    out = CI.status(cfg, gateway_reader=lambda: _live(_CONNECTED))
    # the honest top-level count comes from the authority, not the wrong store
    assert out["connected"] == 3, out
    # and the framework registry is still reported separately + labelled
    assert out["configured"] == 0
    assert out["listener"] == "NONE"


def test_status_absent_gateway_is_honest_not_fabricated(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    out = CI.status(cfg, gateway_reader=lambda: None)
    assert out["gateway"]["readable"] is False
    assert out["gateway"]["connected"] == 0
    assert out["gateway"]["connected_platforms"] == []
    assert out["connected"] == 0
    assert out["state"] == "OK"  # the framework itself is fine


def test_status_does_not_fabricate_connected_from_registry(tmp_path, monkeypatch):
    """No registry credential reference can fabricate a live connection."""
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    # gateway reports nothing connected
    out = CI.status(cfg, gateway_reader=lambda: _live(
        {"telegram": {"state": "retrying"}}))
    assert out["gateway"]["connected"] == 0
    assert out["connected"] == 0


def test_configured_reference_never_creates_connected(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    CI.configure(cfg, "discord", secret_ref="env:DISCORD_BOT_TOKEN",
                 confirmed=True, requested_by="test")
    out = CI.status(cfg, gateway_reader=lambda: None)
    assert out["configured"] == 1
    assert out["connected"] == 0  # a reference is not a transport


# --------------------------------------------------------------- catalog
def test_catalog_carries_live_gateway_view(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    out = CI.catalog(cfg, gateway_reader=lambda: _live(_CONNECTED))
    assert out["gateway"]["connected"] == 3
    # the per-channel row for a live platform is honestly labelled
    row = next(r for r in out["channels"] if r["channel"] == "telegram")
    assert row["gateway_state"] == "connected"
    assert row["connected"] is True
    # an unconfigured, unconnected channel stays honest
    other = next(r for r in out["channels"] if r["channel"] == "imap")
    assert other["gateway_state"] == ""
    assert other["connected"] is False


def test_catalog_connected_list_is_live_gateway_platforms(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    out = CI.catalog(cfg, gateway_reader=lambda: _live(_CONNECTED))
    assert set(out["connected"]) == {"discord", "telegram", "whatsapp"}


def test_default_reader_is_the_gateway_supervisor(tmp_path, monkeypatch):
    """The default authority is gateway_supervisor.daemon_runtime_status."""
    from voicebridge import channels_inbound as CI
    from voicebridge import gateway_supervisor as GS

    called = {}

    def fake(home=None):
        called["hit"] = True
        return _live(_CONNECTED)

    monkeypatch.setattr(GS, "daemon_runtime_status", fake)
    cfg = _cfg(tmp_path, monkeypatch)
    out = CI.status(cfg)
    assert called.get("hit") is True
    assert out["gateway"]["connected"] == 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
