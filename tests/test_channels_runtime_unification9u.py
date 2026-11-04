"""Dashboard channel config <-> runtime transport unification.

Three stores describe messaging channels:

* the inbound registry (``channels_inbound.CHANNEL_CAPS``),
* the dashboard Channels store (``msg-platforms.json`` written by
  ``voicebridge.qubit_compat``), and
* the live gateway's own ``gateway_state.json`` (keyed by runtime adapter id).

They used to disagree on spelling (``teams`` vs ``msteams``) and on whether the
dashboard store reached a transport at all. These tests pin the translation and
the truthful wiring record; they are hermetic (throwaway HOME, no provider,
no network).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: t\n")
    from voicebridge.config import Config

    return Config.load(p)


def _write_dashboard_store(cfg, platforms):
    path = Path(cfg.state_dir()) / "msg-platforms.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "qubit-msg-platforms/1",
        "platforms": platforms,
    }))


def test_runtime_platform_aliases_are_bidirectional():
    from voicebridge import channels_inbound as CI

    assert CI.canonical_channel_id("teams") == "msteams"
    assert CI.canonical_channel_id("bluebubbles") == "imessage"
    assert CI.canonical_channel_id("google_chat") == "googlechat"
    assert CI.canonical_channel_id("whatsapp_cloud") == "whatsapp-cloud"
    assert CI.canonical_channel_id("whatsapp") == "whatsapp"
    assert CI.canonical_channel_id("discord") == "discord"

    assert CI.runtime_platform_id("msteams") == "teams"
    assert CI.runtime_platform_id("imessage") == "bluebubbles"
    assert CI.runtime_platform_id("googlechat") == "google_chat"
    assert CI.runtime_platform_id("whatsapp-cloud") == "whatsapp_cloud"
    # Identity ids are not re-mapped to a sibling platform.
    assert CI.runtime_platform_id("whatsapp") == "whatsapp"
    assert CI.runtime_platform_id("discord") == "discord"

    for channel in ("teams", "bluebubbles", "google_chat", "whatsapp_cloud"):
        assert CI.canonical_channel_id(channel) in CI.CHANNELS, channel


def test_gateway_view_translates_runtime_adapter_ids():
    from voicebridge import channels_inbound as CI

    live = {
        "gateway_state": "running",
        "platforms_map": {
            "teams": {"state": "connected"},
            "bluebubbles": {"state": "connected"},
            "whatsapp_cloud": {"state": "connected"},
            "telegram": {"state": "disconnected"},
        },
    }
    view = CI._gateway_view(lambda: live)
    assert view["platforms"]["msteams"] == "connected"
    assert view["platforms"]["imessage"] == "connected"
    assert view["platforms"]["whatsapp-cloud"] == "connected"
    assert view["platforms"]["telegram"] == "disconnected"
    assert view["connected"] == 3
    assert set(view["connected_platforms"]) == {
        "msteams", "imessage", "whatsapp-cloud"}
    assert view["aliases"] == {
        "teams": "msteams",
        "bluebubbles": "imessage",
        "whatsapp_cloud": "whatsapp-cloud",
    }


def test_catalog_joins_the_dashboard_store_and_live_gateway(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, {
        "teams": {"enabled": True, "refs": {"TEAMS_BOT_TOKEN": "env:X"},
                  "updated_by": "dashboard:user"},
    })
    live = {"gateway_state": "running",
            "platforms_map": {"teams": {"state": "connected"}}}

    out = CI.catalog(cfg, gateway_reader=lambda: live)
    row = next(r for r in out["channels"] if r["channel"] == "msteams")
    assert row["runtime_platform"] == "teams"
    assert row["dashboard_configured"] is True
    assert row["dashboard_enabled"] is True
    assert row["gateway_state"] == "connected"
    assert row["connected"] is True
    # The dashboard ref/config makes the channel *configured*; the inbound
    # registry's own credential reference is still absent, so the row keeps
    # the typed no-listener gate instead of claiming a transport.
    assert row["configured"] is True
    assert row["available"] is False
    assert row["state"] == "UNAVAILABLE"
    assert row["code"] == CI.UNAVAILABLE_CODE

    status = CI.status(cfg, gateway_reader=lambda: live)
    assert status["dashboard_configured"] == 1
    assert status["configured"] == 1
    assert "teams" in status["connected_platforms"] or \
        "msteams" in status["connected_platforms"]


def test_dashboard_store_is_reference_only_and_best_effort(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, {
        "telegram": {"enabled": False, "refs": {"TELEGRAM_BOT_TOKEN": "env:X"}},
    })
    parsed = CI._dashboard_platforms(cfg)
    assert parsed["telegram"]["ref_keys"] == ["TELEGRAM_BOT_TOKEN"]
    # No value field ever appears in the projection.
    assert "TELEGRAM_BOT_TOKEN" in parsed["telegram"]["ref_keys"]
    assert all("X" not in str(v) for v in parsed["telegram"].values())

    # A malformed store resolves to {}, never a fabricated config.
    (Path(cfg.state_dir()) / "msg-platforms.json").write_text("{not json")
    assert CI._dashboard_platforms(cfg) == {}


def test_status_reports_the_bridge_as_consumed(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    wiring = CI.status(cfg)["dashboard_config_wiring"]
    assert wiring["consumed_by_adapter"] is True
    assert wiring["bridge"] == "voicebridge.messaging.bridge_platform_to_gateway"
    assert wiring["dashboard_store_writer"] == "voicebridge.qubit_compat.msg_update"
