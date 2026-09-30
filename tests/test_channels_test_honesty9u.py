"""Channels "Test connection" honesty (audit fix-channels, 2026-09-29).

Pins the honesty defect from the isolated-instance audit: the dashboard
``POST /agent/api/messaging/platforms/{id}/test`` path returned
``200 {"ok": true, "state": "no_account", ...}`` for *every* id -- including
platforms that are not in the catalog and platforms that have never been
connected -- because the handler computed ``ok=True`` unconditionally and
``messaging.link`` only looked up an ACCOUNT id, so catalog platform ids never
matched.

The honest contract (mirroring the vendored reference
``web_routers/messaging.py:916``):

* an unknown id is a typed ``404 MSG_NOT_FOUND`` -- never ``ok: true``;
* a known-but-unconfigured / no-transport platform is ``200 ok: false`` with a
  truthful state and an operator-facing ``message``;
* ``ok: true`` is possible **only** when a real connection is proven
  (``state == "connected"``).

It also pins the module-level root-cause fix: ``messaging.link`` now resolves a
catalog/gateway *platform* id to the accounts bound to that provider (via
``resolve_platform_provider``) instead of requiring an account whose id
literally equals the platform id, and only reports ``LINKED`` when Qubit ships
a real transport adapter (``PROVIDER_CAPS``: slack + whatsapp-cloud).

Hermetic: throwaway XDG/HOME tree, no live provider, no network.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _route_handler_is_patched() -> bool:
    """The route honesty fix lives in a file this task does not own.

    It is captured as ``/tmp/opencode/qubit-audit-2026-09-29/patches/
    fix-channels.patch``; the module-level authority it calls
    (``messaging.platform_connection_test``) is tested unconditionally below.
    Route-level tests are skipped until that patch is applied.
    """
    try:
        from voicebridge import qubit_compat as HC

        return "platform_connection_test" in inspect.getsource(HC.h_msg_test_live)
    except Exception:
        return False


requires_route_patch = pytest.mark.skipif(
    not _route_handler_is_patched(),
    reason="qubit_compat h_msg_test_live patch (fix-channels.patch) not applied")


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


def _test(cfg, platform_id):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "POST",
                       f"/api/messaging/platforms/{platform_id}/test", {}, {})


# --------------------------------------------------- route-level honesty
@requires_route_patch
def test_unknown_platform_is_not_ok(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = _test(cfg, "nonexistent-platform-xyz")
    assert code == 404, body
    assert body.get("ok") is not True, body
    assert body.get("code") == "MSG_NOT_FOUND", body


@requires_route_patch
def test_known_unconfigured_platform_is_not_ok(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = _test(cfg, "telegram")
    assert code == 200, body
    assert body.get("ok") is False, body
    assert body.get("reachable") is False, body
    assert body.get("message"), body
    assert "undefined" not in str(body.get("message")).lower(), body


@requires_route_patch
def test_no_catalog_platform_fabricates_ok(tmp_path, monkeypatch):
    """No channel can be ok:true without a proven live connection."""
    cfg = _cfg(tmp_path, monkeypatch)
    for pid in ("telegram", "slack", "discord", "whatsapp", "matrix",
                "signal", "msteams", "imessage", "feishu"):
        code, body = _test(cfg, pid)
        assert code in (200, 404), (pid, code, body)
        assert body.get("ok") is not True, (pid, body)


# ------------------------------------------------ verdict contract (pure)
def _info(**over):
    base = {"name": "Telegram", "enabled": True, "configured": True,
            "gateway_running": True, "state": "connected", "error_message": "",
            "missing_env": []}
    base.update(over)
    return base


def test_verdict_ok_only_when_a_real_connection_is_proven():
    from voicebridge import messaging as M

    assert M.platform_test_verdict(_info(state="connected"))["ok"] is True
    for state in ("unavailable", "degraded", "not_configured", "disabled",
                  "no_account", "no_credential", ""):
        assert M.platform_test_verdict(_info(state=state))["ok"] is False, state


def test_connection_test_unknown_is_404_not_ok(tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = M.platform_connection_test(cfg, "ghost", lambda c, p: None)
    assert code == 404 and body.get("ok") is False, body
    assert body.get("code") == "MSG_NOT_FOUND", body


def test_connection_test_known_unconfigured_is_200_ok_false(
        tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)

    def resolve(cfg, pid):
        return _info(state="unavailable", error_message="no Qubit transport")

    code, body = M.platform_connection_test(cfg, "telegram", resolve)
    assert code == 200 and body["ok"] is False, body
    assert body["reachable"] is False and body["message"], body


# --------------------------------------------- module-level link honesty
def test_link_unknown_is_no_account(tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    out = M.link(cfg, "never-heard-of-it")
    assert out["state"] == "NO_ACCOUNT", out
    assert out["code"] == M.NOT_FOUND_CODE, out


def test_link_known_platform_without_account_is_no_account(
        tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    for pid in ("slack", "telegram", "discord", "whatsapp", "bluebubbles",
                "teams", "whatsapp_cloud"):
        out = M.link(cfg, pid)
        assert out["state"] == "NO_ACCOUNT", (pid, out)
        assert out["state"] != "LINKED", (pid, out)


def test_link_platform_resolves_the_bound_provider_account(
        tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    # A resolvable credential *and* a shipped transport adapter -> LINKED,
    # and the platform id resolves to the account bound to that provider.
    monkeypatch.setenv("AUDIT_SLACK_TOKEN", "xoxb-audit")
    M.account_add(cfg, "audit-team", "slack", "Audit Team",
                  key_ref="env:AUDIT_SLACK_TOKEN", confirmed=True)
    out = M.link(cfg, "slack")
    assert out["state"] == "LINKED", out
    assert out["account"] == "audit-team", out


def test_link_unavailable_shaper_never_linked_even_with_credential(
        tmp_path, monkeypatch):
    """A credential alone cannot fabricate a connection for a channel with no
    Qubit transport adapter (discord/telegram have send shapers only)."""
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("AUDIT_TG_TOKEN", "123456:secret")
    M.account_add(cfg, "audit-tg", "telegram", "Audit TG",
                  key_ref="env:AUDIT_TG_TOKEN", confirmed=True)
    out = M.link(cfg, "telegram")
    assert out["state"] == "UNAVAILABLE", out
    assert out["state"] != "LINKED", out
    assert out["code"] == M.UNAVAILABLE_CODE, out


def test_link_platform_alias_maps_catalog_spelling(tmp_path, monkeypatch):
    from voicebridge import messaging as M

    cfg = _cfg(tmp_path, monkeypatch)
    assert M.resolve_platform_provider("whatsapp_cloud") == "whatsapp-cloud"
    assert M.resolve_platform_provider("teams") == "msteams"
    assert M.resolve_platform_provider("bluebubbles") == "imessage"
    assert M.resolve_platform_provider("slack") == "slack"
    assert M.resolve_platform_provider("nope") == ""
    monkeypatch.setenv("AUDIT_WA_TOKEN", "EAAG-audit")
    M.account_add(cfg, "audit-wa", "whatsapp-cloud", "Audit WA",
                  key_ref="env:AUDIT_WA_TOKEN", confirmed=True,
                  extra={"phone_number_id": "123456789012345"})
    out = M.link(cfg, "whatsapp_cloud")
    assert out["state"] == "LINKED", out
    assert out["account"] == "audit-wa", out


# ----------------------------------------------- recorded adapter reality
def test_only_slack_and_whatsapp_cloud_ship_a_transport():
    """The honest capability record: which platforms have real adapters."""
    from voicebridge import messaging as M

    available = {p for p, c in M.PROVIDER_CAPS.items()
                 if c.get("available")}
    assert available == {"slack", "whatsapp-cloud"}, available
    for pid in ("discord", "telegram"):
        assert M.PROVIDER_CAPS[pid]["available"] is False, pid


def test_channels_inbound_records_dashboard_config_gap(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    wiring = CI.status(cfg)["dashboard_config_wiring"]
    assert wiring["dashboard_store"] == "msg-platforms.json"
    assert wiring["transport_store"] == "msg-accounts.json"
    assert wiring["consumed_by_adapter"] is False
