"""Channels page repair (audit fix-channels, 2026-09-29).

Pins the honest-channel repairs:

1. The dashboard ``Test`` button no longer fabricates success. A known channel
   answers HTTP 200 with ``ok: false`` and an operator-facing ``message`` unless
   a real connection is proven (``state == "connected"``); a nonexistent id is a
   typed ``404 MSG_NOT_FOUND`` -- never ``ok: true``. This mirrors the vendored
   reference ``test_messaging_platform`` (``web_routers/messaging.py:916``).
2. ``channels_inbound.status`` documents the dashboard-config root cause: the
   ``msg-platforms.json`` store is not consumed by any adapter.

Hermetic: a throwaway XDG/HOME tree, no live provider, no network.
"""
from __future__ import annotations

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


# --------------------------------------------------------------- unit: verdict
def _info(**over):
    base = {"name": "Telegram", "enabled": True, "configured": True,
            "gateway_running": True, "state": "connected", "error_message": "",
            "missing_env": []}
    base.update(over)
    return base


def test_verdict_ok_only_for_a_real_connected_state():
    from voicebridge import messaging as M

    assert M.platform_test_verdict(_info())["ok"] is True


def test_verdict_disabled_is_not_ok():
    from voicebridge import messaging as M

    out = M.platform_test_verdict(_info(enabled=False))
    assert out["ok"] is False and "disabled" in out["message"].lower()


def test_verdict_missing_setup_names_the_missing_key():
    from voicebridge import messaging as M

    out = M.platform_test_verdict(
        _info(configured=False, missing_env=["TELEGRAM_BOT_TOKEN"]))
    assert out["ok"] is False
    assert "TELEGRAM_BOT_TOKEN" in out["message"]


def test_verdict_gateway_down_is_not_ok():
    from voicebridge import messaging as M

    out = M.platform_test_verdict(_info(gateway_running=False, state=""))
    assert out["ok"] is False and "gateway is not running" in out["message"].lower()


def test_verdict_unavailable_with_error_carries_the_reason():
    from voicebridge import messaging as M

    out = M.platform_test_verdict(_info(
        state="unavailable", error_message="no Qubit transport adapter"))
    assert out["ok"] is False
    assert out["message"] == "no Qubit transport adapter"


def test_verdict_never_reports_ok_without_connected():
    from voicebridge import messaging as M

    for state in ("unavailable", "degraded", "not_configured", "disabled",
                  "disconnected", "gateway_stopped", "startup_failed"):
        out = M.platform_test_verdict(_info(state=state))
        assert out["ok"] is False, state


# ------------------------------------------------ unit: connection_test routing
def _resolve_known(cfg, pid):
    if pid != "telegram":
        return None
    return _info(state="unavailable",
                 error_message="no Qubit transport adapter for this channel")


def test_unknown_platform_is_typed_404_not_ok(tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = M.platform_connection_test(cfg, "nonexistent", _resolve_known)
    assert code == 404, body
    assert body.get("ok") is not True, body
    assert body.get("code") == "MSG_NOT_FOUND", body


def test_known_unavailable_platform_is_200_ok_false_with_message(
        tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = M.platform_connection_test(cfg, "telegram", _resolve_known)
    assert code == 200, body
    assert body["ok"] is False
    assert body["reachable"] is False
    assert body["message"] and "undefined" not in body["message"].lower()


def test_empty_platform_id_is_a_typed_400(tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = M.platform_connection_test(cfg, "", _resolve_known)
    assert code == 400 and body.get("ok") is False, body


# ------------------------------------------------ integration: the live handler
def _test(cfg, platform_id):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "POST",
                       f"/api/messaging/platforms/{platform_id}/test", {}, {})


def test_route_unknown_platform_is_not_ok_true(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = _test(cfg, "nonexistent")
    assert code == 404, body
    assert body.get("ok") is not True, body
    assert body.get("code") == "MSG_NOT_FOUND", body


def test_route_telegram_test_is_honest(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = _test(cfg, "telegram")
    assert code == 200, body
    # No Qubit transport adapter exists, so a real connection cannot be proven.
    assert body.get("ok") is False, body
    assert body.get("reachable") is False, body
    assert body.get("message"), body
    assert "undefined" not in str(body.get("message")).lower(), body


# -------------------------------------------------- documentation: defect 3
def test_channels_inbound_documents_dashboard_store_gap(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    wiring = CI.status(cfg)["dashboard_config_wiring"]
    assert wiring["dashboard_store"] == "msg-platforms.json"
    assert wiring["consumed_by_adapter"] is False
    assert "msg-platforms.json" in wiring["operator_prerequisite"]
