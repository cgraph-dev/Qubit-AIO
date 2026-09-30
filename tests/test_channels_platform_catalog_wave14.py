"""Channels page — the served platform catalog and the enable/ref round-trip.

The dashboard Channels page reads ``GET /api/messaging/platforms`` through the
compat dispatcher (``voicebridge.qubit_compat.h_platforms``). Before this wave
the handler returned only Qubit account rows, so the connectable catalog was
empty and the enable toggle 404'd on a catalog platform. These tests pin the
ported contract:

1. the served list is the REAL vendored catalog
   (``qubit_cli.web_server_messaging._messaging_platform_catalog``), de-branded;
2. per-platform state is honest and never a fabricated "connected";
3. Enable/Disable persists through the confirm path and is read back from the
   SAME store the GET serves;
4. credentials stay reference-only: a raw secret is a typed
   ``CHANNEL_ENV_REFERENCE_ONLY`` refusal, ``env:``/``vault:``/``file:``
   references are accepted;
5. an unknown id stays a typed ``MSG_NOT_FOUND``.

Hermetic: a throwaway XDG/HOME tree, no live provider, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Catalog platforms the vendored runtime always ships (built-ins from
# ``gateway.config.Platform`` minus ``local``). Not an exact-count assertion.
_KNOWN_PLATFORMS = {"telegram", "discord", "slack", "matrix", "signal"}


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


def _get(cfg):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "GET", "/api/messaging/platforms", {}, {})


def _put(cfg, platform_id, body):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "PUT", f"/api/messaging/platforms/{platform_id}",
                       {}, body)


def _platform(payload, platform_id):
    return next(p for p in payload["platforms"] if p["id"] == platform_id)


def test_served_catalog_is_the_real_platform_set(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = _get(cfg)
    assert code == 200 and body.get("ok") is True, body
    ids = {p["id"] for p in body["platforms"]}
    assert _KNOWN_PLATFORMS <= ids, sorted(ids)
    assert len(ids) >= 20, sorted(ids)
    assert body["gateway_start_command"] == "qubit gateway start"
    assert body.get("env_path")
    # Every row carries the upstream payload shape the SPA reads.
    for row in body["platforms"]:
        for key in ("id", "name", "description", "docs_url", "enabled",
                    "configured", "gateway_running", "state", "env_vars"):
            assert key in row, (row.get("id"), key)
        assert isinstance(row["env_vars"], list)


def test_served_catalog_is_debranded(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _code, body = _get(cfg)
    blob = json.dumps(body)
    assert "Hermes" not in blob and "OpenClaw" not in blob, blob[:400]
    assert "nousresearch.com" not in blob.lower(), blob[:400]
    for row in body["platforms"]:
        assert "nousresearch.com" not in str(row["docs_url"]).lower()


def test_new_platform_is_disabled_and_never_fabricates_connected(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _code, body = _get(cfg)
    tg = _platform(body, "telegram")
    assert tg["enabled"] is False
    assert tg["state"] == "disabled"
    assert tg["configured"] is False
    # The vendored runtime has no Qubit transport adapter for Telegram, so a
    # configured platform must resolve to the honest "unavailable" state.
    assert tg["state"] != "connected"


def test_enable_disable_round_trip_through_confirm_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # A bare toggle stages a single-use confirmation (canonical confirm path).
    code, staged = _put(cfg, "telegram", {"enabled": True})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged

    # Settle by re-issuing with the staged id: the real backend executes once.
    code, done = _put(cfg, "telegram",
                      {"enabled": True, "confirmation_id": cid})
    assert code == 200 and done.get("ok") is True, done

    # The SAME store the GET reads now reports enabled (no split-brain).
    _code, body = _get(cfg)
    assert _platform(body, "telegram")["enabled"] is True

    # Disable round-trips the same way.
    code, staged = _put(cfg, "telegram", {"enabled": False})
    assert code == 409, staged
    code, done = _put(cfg, "telegram",
                      {"enabled": False,
                       "confirmation_id": staged["confirmation_id"]})
    assert code == 200 and done.get("ok") is True, done
    _code, body = _get(cfg)
    assert _platform(body, "telegram")["enabled"] is False

    # The store file is the one write target (references/flags only).
    store = (Path(cfg.state_dir()) / "msg-platforms.json")
    assert store.exists()
    assert "telegram" in json.loads(store.read_text())["platforms"]


def test_raw_secret_is_refused_and_reference_is_accepted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, refused = _put(
        cfg, "telegram",
        {"env": {"TELEGRAM_BOT_TOKEN": "123456:AAFfake-secret"},
         "confirm": True})
    assert code == 403, refused
    assert refused.get("code") == "CHANNEL_ENV_REFERENCE_ONLY", refused

    code, ok = _put(
        cfg, "telegram",
        {"env": {"TELEGRAM_BOT_TOKEN": "env:TELEGRAM_BOT_TOKEN"},
         "confirm": True})
    assert code == 200 and ok.get("ok") is True, ok

    _code, body = _get(cfg)
    field = next(v for v in _platform(body, "telegram")["env_vars"]
                 if v["key"] == "TELEGRAM_BOT_TOKEN")
    assert field["is_set"] is True
    # The reference itself is never echoed as a value.
    assert field["redacted_value"] is None


def test_unknown_and_unknown_env_key_are_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _put(cfg, "no-such-channel", {"enabled": True, "confirm": True})
    assert code == 404 and out.get("code") == "MSG_NOT_FOUND", out

    code, out = _put(
        cfg, "telegram",
        {"env": {"NOT_A_TELEGRAM_KEY": "env:X"}, "confirm": True})
    assert code == 400 and out.get("code") == "MSG_BAD_INPUT", out
