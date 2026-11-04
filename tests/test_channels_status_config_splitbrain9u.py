"""Channels status/config split-brain vs the real dashboard + credential store.

Live ``qubit channels status`` reported ``configured: 0`` while the dashboard
Channels store held 19 rows and the gateway served Discord/Telegram/WhatsApp.
The inbound registry is only ONE config source; the dashboard store
(``msg-platforms.json``, bridged into the gateway ``config.yaml``/``.env``) and
the gateway's own runtime state are the others.

These tests pin the joined view:

* ``configured`` counts channels configured in EITHER store (plus live
  gateway-proven connections), but never claims an inbound listener: the
  listener stays ``NONE`` and per-row ``available`` stays ``False``;
* a configured channel without a Qubit transport keeps the typed
  ``CHANNEL_UNAVAILABLE`` gate instead of reading ``UNCONFIGURED``;
* ``/api/messaging/platforms`` carries ``ingress_url`` from the live gateway's
  own ``gateway_state.json`` only while the gateway is running, exactly like the
  reference; and
* the gateway ``.env`` credential store is recognised as ``configured`` even
  when the dashboard store only holds the enable flag.

Hermetic: throwaway XDG/HOME tree, injected gateway reader/status, no provider,
no network.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# The ids currently present in the live dashboard store (19 rows).
_LIVE_DASHBOARD_STORE = (
    "a2a", "api_server", "bluebubbles", "discord", "email", "feishu",
    "google_chat", "homeassistant", "matrix", "mattermost",
    "msgraph_webhook", "ntfy", "photon", "signal", "slack", "sms",
    "telegram", "whatsapp", "yuanbao",
)
_LIVE_ENABLED = ("discord", "email", "telegram", "whatsapp")
_LIVE_CONNECTED = {
    "discord": {"state": "connected"},
    "telegram": {"state": "connected"},
    "whatsapp": {"state": "connected"},
}


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


def _write_env(cfg, lines):
    # The gateway stores credentials in ITS home's .env (QUBIT_HOME), which is
    # the path ``gateway_supervisor._daemon_home`` reads.
    env_path = Path(os.environ["QUBIT_HOME"]) / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n")


def _live(platforms):
    return {
        "gateway_state": "running",
        "platforms": len(platforms),
        "platforms_map": platforms,
        "connected_platforms": sum(
            1 for v in platforms.values()
            if str(v.get("state", "")).lower() in ("connected", "running", "ok")),
    }


def _live_store():
    platforms = {}
    for pid in _LIVE_DASHBOARD_STORE:
        rec = {"enabled": pid in _LIVE_ENABLED}
        if pid == "discord":
            rec["refs"] = {"DISCORD_BOT_TOKEN": "env:DISCORD_BOT_TOKEN"}
        platforms[pid] = rec
    return platforms


def _write_live_env(cfg):
    """The gateway credential store behind the live dashboard rows."""
    _write_env(cfg, [
        "DISCORD_BOT_TOKEN=discord-live",
        "TELEGRAM_BOT_TOKEN=123:telegram-live",
        "WHATSAPP_ENABLED=1",
    ])


# --------------------------------------------------------------- status
def test_live_status_shape_joins_dashboard_store(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, _live_store())
    _write_live_env(cfg)

    out = CI.status(cfg, gateway_reader=lambda: _live(_LIVE_CONNECTED))

    assert out["channels"] == len(CI.CHANNELS)
    assert out["dashboard_configured"] == 19
    assert out["registry_configured"] == 0
    # Discord (ref), Telegram + WhatsApp (enabled rows / live connections).
    assert out["configured"] == 3, out
    assert out["configured_platforms"] == ["discord", "telegram", "whatsapp"]
    assert out["connected"] == 3
    assert out["connected_platforms"] == ["discord", "telegram", "whatsapp"]
    # Config is not a listener claim.
    assert out["listener"] == "NONE"


def test_disabled_dashboard_rows_do_not_inflate_configured(
        tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, _live_store())
    _write_live_env(cfg)
    out = CI.status(cfg, gateway_reader=lambda: None)
    # No live gateway: only the real store config counts, not the 16 disabled
    # rows and not a fabricated connection.
    assert out["dashboard_configured"] == 19
    assert out["configured"] == 3, out
    assert out["connected"] == 0
    assert out["listener"] == "NONE"


# --------------------------------------------------------------- catalog
def test_catalog_rows_reflect_dashboard_config_not_just_registry(
        tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, _live_store())
    _write_live_env(cfg)
    out = CI.catalog(cfg, gateway_reader=lambda: _live(_LIVE_CONNECTED))
    by_id = {r["channel"]: r for r in out["channels"]}

    for pid, connected in (("discord", True), ("telegram", True),
                           ("whatsapp", True)):
        row = by_id[pid]
        assert row["configured"] is True, row
        assert row["dashboard_configured"] is True, row
        assert row["connected"] is connected, row
        assert row["available"] is False, row
        # No Qubit inbound listener: typed gate, never CONNECTED/AVAILABLE.
        assert row["state"] == "UNAVAILABLE", row
        assert row["code"] == CI.UNAVAILABLE_CODE, row

    # A disabled dashboard row is a real row but not a configuration.
    signal = by_id["signal"]
    assert signal["dashboard_configured"] is True
    assert signal["dashboard_enabled"] is False
    assert signal["configured"] is False
    assert signal["state"] == "DISABLED"

    # Enabled but with no credential reference, no .env key and no live
    # connection: not configured (same rule the reference applies).
    email = by_id["email"]
    assert email["dashboard_enabled"] is True
    assert email["configured"] is False

    # A channel absent from both stores stays honestly unconfigured.
    irc = by_id["irc"]
    assert irc["configured"] is False
    assert irc["state"] == "UNCONFIGURED"
    assert irc["code"] == CI.NO_CREDENTIAL_CODE
    assert out["available"] == []


def test_three_connected_aliases_light_up_canonical_rows(
        tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, {
        "teams": {"enabled": True, "refs": {"TEAMS_BOT_TOKEN": "env:X"}},
        "bluebubbles": {"enabled": True, "refs": {"BLUEBUBBLES_TOKEN": "env:X"}},
        "whatsapp_cloud": {"enabled": True,
                           "refs": {"WHATSAPP_CLOUD_TOKEN": "env:X"}},
    })
    live = _live({
        "teams": {"state": "connected"},
        "bluebubbles": {"state": "connected"},
        "whatsapp_cloud": {"state": "connected"},
    })
    out = CI.status(cfg, gateway_reader=lambda: live)
    assert out["connected"] == 3
    assert out["configured"] == 3
    assert set(out["connected_platforms"]) == {
        "msteams", "imessage", "whatsapp-cloud"}
    assert set(out["configured_platforms"]) == {
        "msteams", "imessage", "whatsapp-cloud"}

    rows = {r["channel"]: r for r in
            CI.catalog(cfg, gateway_reader=lambda: live)["channels"]}
    for cid, runtime_id in (("msteams", "teams"),
                            ("imessage", "bluebubbles"),
                            ("whatsapp-cloud", "whatsapp_cloud")):
        assert rows[cid]["runtime_platform"] == runtime_id
        assert rows[cid]["configured"] is True
        assert rows[cid]["connected"] is True


def test_registry_and_dashboard_configured_are_union_not_split(
        tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_SPLIT_SECRET", "split-secret")
    CI.configure(cfg, "slack", secret_ref="env:CH_SPLIT_SECRET",
                 confirmed=True)
    _write_dashboard_store(cfg, {"telegram": {
        "enabled": True,
        "refs": {"TELEGRAM_BOT_TOKEN": "env:TELEGRAM_BOT_TOKEN"},
    }})
    out = CI.status(cfg, gateway_reader=lambda: None)
    assert out["registry_configured"] == 1
    assert out["dashboard_configured"] == 1
    assert out["configured"] == 2
    assert out["configured_platforms"] == ["slack", "telegram"]


# --------------------------------------------------------- accounts view
def test_accounts_connected_list_uses_live_gateway(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_ACCOUNT_SECRET", "account-secret")
    CI.configure(cfg, "discord", secret_ref="env:CH_ACCOUNT_SECRET",
                 confirmed=True)
    out = CI.accounts(cfg, gateway_reader=lambda: _live(_LIVE_CONNECTED))
    assert out["connected"] == ["discord"]
    row = out["accounts"][0]
    assert row["configured"] is True
    assert row["connected"] is True


# ------------------------------------ /api/messaging/platforms ingress_url
def _dispatch_get(cfg):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "GET", "/api/messaging/platforms", {}, {})


def _platform(payload, platform_id):
    return next(p for p in payload["platforms"] if p["id"] == platform_id)


def _patch_gateway(monkeypatch, running, platforms_map):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "status", lambda cfg: {"running": running})
    monkeypatch.setattr(
        GS, "daemon_runtime_status",
        lambda home=None: {"gateway_state": "running" if running else "stopped",
                           "platforms": len(platforms_map),
                           "platforms_map": platforms_map})


def test_platform_rows_carry_live_ingress_url(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, {"telegram": {"enabled": True}})
    _write_env(cfg, ["TELEGRAM_BOT_TOKEN=123:live"])
    _patch_gateway(monkeypatch, True, {
        "telegram": {"state": "connected",
                     "ingress_url": "http://127.0.0.1:8642/p/default/telegram"},
    })

    code, body = _dispatch_get(cfg)
    assert code == 200 and body.get("ok") is True, body
    for row in body["platforms"]:
        assert "ingress_url" in row, row.get("id")

    tg = _platform(body, "telegram")
    assert tg["ingress_url"] == \
        "http://127.0.0.1:8642/p/default/telegram"
    assert tg["configured"] is True
    assert tg["state"] == "connected"
    # A platform with no runtime entry has no ingress URL, never a guess.
    assert _platform(body, "slack")["ingress_url"] is None


def test_platform_rows_drop_ingress_url_when_gateway_stopped(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _write_dashboard_store(cfg, {"telegram": {"enabled": True}})
    _patch_gateway(monkeypatch, False, {
        "telegram": {"state": "connected",
                     "ingress_url": "http://127.0.0.1:8642/p/default/telegram"},
    })

    code, body = _dispatch_get(cfg)
    assert code == 200 and body.get("ok") is True, body
    tg = _platform(body, "telegram")
    # A stopped gateway's retained record is history, not a current ingress.
    assert tg["ingress_url"] is None


def test_platform_configured_from_gateway_env_without_listener_claim(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # The Channels panel stores only the enable flag; the credential value the
    # gateway actually reads lives in the runtime .env.
    _write_dashboard_store(cfg, {"telegram": {"enabled": True}})
    _write_env(cfg, ["TELEGRAM_BOT_TOKEN=123:live"])
    _patch_gateway(monkeypatch, False, {})

    code, body = _dispatch_get(cfg)
    assert code == 200 and body.get("ok") is True, body
    tg = _platform(body, "telegram")
    assert tg["enabled"] is True
    assert tg["configured"] is True
    assert tg["gateway_running"] is False
    assert tg["state"] == "unavailable"
    assert tg["error_code"] == "MSG_TRANSPORT_UNAVAILABLE"
    assert tg["ingress_url"] is None
