"""Packet 8T-F — brokered messaging outbox: accounts, drafts, gated sends.

Proves account validation, draft/approve separation, fresh-confirmation
sends, credential gating (nothing attempted without a resolvable
reference), classic-WhatsApp pairing linkage, idempotent receipts,
retry/backoff, link states, redaction, and e-stop behavior — hermetic
via stubbed transports (no network).
"""
from __future__ import annotations

import io
import json
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _acct(cfg, provider="slack", label="team", aid="team", **kw):
    from voicebridge import messaging as MG

    kw.setdefault("key_ref", "env:SLACK_TOKEN")
    kw.setdefault("confirmed", True)
    if provider == "whatsapp-cloud":
        kw.setdefault("extra", {"phone_number_id": "123456789012345"})
    return MG.account_add(cfg, aid, provider, label, **kw)


def _draft(cfg, account="team", to="#ops", body="hello", **kw):
    from voicebridge import messaging as MG

    kw.setdefault("confirmed", True)
    return MG.draft(cfg, account, to, body, **kw)


class _Resp:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_transport(monkeypatch, calls, mode="slack-ok"):
    def _fake(req, timeout=None):
        calls.append(req)
        if mode == "slack-ok":
            assert req.get_header("Authorization", "").startswith("Bearer "), \
                "key travels in the header only"
            assert "xoxb-test" not in req.data.decode(), \
                "key never in the body"
            return _Resp({"ok": True, "ts": "1718000000.0001"})
        if mode == "wa-ok":
            return _Resp({"messages": [{"id": "wamid.123"}]})
        if mode == "slack-deny":
            return _Resp({"ok": False, "error": "channel_not_found"})
        raise AssertionError("network must not be touched")

    monkeypatch.setattr(urllib.request, "urlopen", _fake)


def test_account_crud_and_validation(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    out = _acct(cfg)
    assert out["account"]["endpoint"] == "https://slack.com/api/chat.postMessage"
    assert "SLACK_TOKEN" not in json.dumps(out)
    assert len(MG.account_list(cfg)["accounts"]) == 1
    for bad in [dict(provider="irc"), dict(key_ref="plain-token"),
                dict(provider="whatsapp-cloud", extra={}),
                dict(provider="whatsapp-cloud",
                     extra={"phone_number_id": "abc"})]:
        try:
            _acct(cfg, aid="x", **bad)
        except MG.MessagingError as exc:
            assert exc.code == "MSG_BAD_INPUT", bad
        else:
            raise AssertionError(f"must refuse {bad}")
    assert MG.account_remove(cfg, "team", confirmed=True)["removed"] == "team"


def test_draft_approve_send_slack(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    calls = []
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test-token-1")
    _stub_transport(monkeypatch, calls)
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    out = _draft(cfg, body="deploy done token=abc123secret")
    msg_id = out["message"]["id"]
    assert "abc123secret" not in json.dumps(out), "preview redacted"
    assert MG.approve(cfg, msg_id, confirmed=True)["message"]["state"] == "approved"
    out = MG.send(cfg, msg_id, confirmed=True)
    assert out["message"]["state"] == "sent"
    assert out["message"]["provider_msg_id"] == "1718000000.0001"
    assert len(calls) == 1, "exactly one transport attempt"
    sent = MG.show(cfg, msg_id)["message"]
    assert sent["state"] == "sent"


def test_send_requires_fresh_confirm(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    monkeypatch.setenv("SLACK_TOKEN", "x")
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    msg_id = _draft(cfg)["message"]["id"]
    try:
        MG.send(cfg, msg_id, confirmed=False)
    except MG.MessagingError as exc:
        assert exc.code == "MSG_CONFIRM_REQUIRED"
    else:
        raise AssertionError("fresh confirmation required")


def test_no_credential_queues_without_attempt(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    calls = []
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    _stub_transport(monkeypatch, calls)
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    msg_id = _draft(cfg)["message"]["id"]
    try:
        MG.send(cfg, msg_id, confirmed=True)
    except MG.MessagingError as exc:
        assert exc.code == "MSG_NO_CREDENTIAL"
    else:
        raise AssertionError("must not attempt without a credential")
    assert calls == [], "no transport attempted"
    assert MG.show(cfg, msg_id)["message"]["state"] == "queued"


def test_idempotent_replay_never_resends(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    calls = []
    monkeypatch.setenv("SLACK_TOKEN", "x")
    _stub_transport(monkeypatch, calls)
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    first = _draft(cfg, idempotency_key="order-1")["message"]["id"]
    MG.send(cfg, first, confirmed=True)
    second = _draft(cfg, idempotency_key="order-2")["message"]["id"]
    # rewrite second's key to collide (simulates a client retry)
    from pathlib import Path as _P

    state_path = _P(cfg.state_dir()) / "msg-outbox.json"
    raw = json.loads(state_path.read_text())
    raw["messages"][second]["idempotency_key"] = "order-1"
    state_path.write_text(json.dumps(raw))
    out = MG.send(cfg, second, confirmed=True)
    assert out["message"]["duplicate"] is True
    assert out["message"]["id"] == first, "original receipt returned"
    assert len(calls) == 1, "never resent"


def test_retry_backoff_and_link_states(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    calls = []
    monkeypatch.setenv("SLACK_TOKEN", "x")
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    assert MG.link(cfg, "team")["state"] == "LINKED"
    assert MG.link(cfg, "ghost")["state"] == "NO_ACCOUNT"
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    assert MG.link(cfg, "team")["state"] == "NO_CREDENTIAL"
    monkeypatch.setenv("SLACK_TOKEN", "x")

    def _boom(req, timeout=None):
        calls.append(req)
        raise ConnectionError("down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    msg_id = _draft(cfg)["message"]["id"]
    try:
        MG.send(cfg, msg_id, confirmed=True)
    except MG.MessagingError as exc:
        assert exc.code == "MSG_UPSTREAM"
    shown = MG.show(cfg, msg_id)["message"]
    assert shown["state"] == "queued" and shown["attempts"] == 1
    assert MG.link(cfg, "team")["state"] == "DEGRADED"
    _stub_transport(monkeypatch, calls, mode="slack-ok")
    out = MG.retry(cfg, msg_id, confirmed=True)
    assert out["message"]["state"] == "sent"
    assert out["message"]["attempts"] == 2


def test_whatsapp_pairing_linkage(tmp_path, monkeypatch):
    from voicebridge import messaging as MG
    from voicebridge import pairing as PA

    monkeypatch.setenv("WA_TOKEN", "wa-test-1")
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg, provider="whatsapp", label="phone", aid="phone",
          key_ref="env:WA_TOKEN")
    msg_id = _draft(cfg, account="phone", to="+15551234567",
                    body="hi")["message"]["id"]
    try:
        MG.send(cfg, msg_id, confirmed=True)
    except MG.MessagingError as exc:
        assert exc.code == "MSG_NOT_PAIRED", exc.code
    else:
        raise AssertionError("unpaired classic send must fail")
    created = PA.create(cfg, "owner", ["messaging"], account="phone",
                        confirmed=True)
    PA.approve(cfg, created["code"], confirmed=True)
    try:
        MG.send(cfg, msg_id, confirmed=True)
    except MG.MessagingError as exc:
        assert exc.code == "MSG_NO_TRANSPORT", \
            "paired authz passes; session transport stays manual-only"
    else:
        raise AssertionError("session transport must stay manual-only")


def test_whatsapp_cloud_send(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    calls = []
    monkeypatch.setenv("WA_TOKEN", "wa-test-1")
    _stub_transport(monkeypatch, calls, mode="wa-ok")
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg, provider="whatsapp-cloud", label="biz", aid="biz",
          key_ref="env:WA_TOKEN")
    msg_id = _draft(cfg, account="biz", to="+15551234567",
                    body="hello")["message"]["id"]
    out = MG.send(cfg, msg_id, confirmed=True)
    assert out["message"]["provider_msg_id"] == "wamid.123"
    assert calls[0].full_url.startswith("https://graph.facebook.com/")


def test_audit_redacted_and_estop(tmp_path, monkeypatch):
    from voicebridge import messaging as MG
    from voicebridge.logging_setup import setup
    from voicebridge.power import power_control_store

    calls = []
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test-1")
    _stub_transport(monkeypatch, calls)
    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        _acct(cfg)
        msg_id = _draft(cfg, body="hello token=abc123secret")["message"]["id"]
        MG.send(cfg, msg_id, confirmed=True)
        blob = (__import__("pathlib").Path(cfg.state_dir()) /
                "events.jsonl").read_text()
        assert "msg.send" in blob and "msg.draft" in blob
        assert "abc123secret" not in blob and "xoxb-test-1" not in blob
        power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                     issued_by="cli:user")
        try:
            msg2 = _draft(cfg, body="x")["message"]["id"]
            MG.send(cfg, msg2, confirmed=True)
        except MG.MessagingError as exc:
            assert exc.code == "MSG_ESTOP"
        else:
            raise AssertionError("latched e-stop must refuse sends")
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_cli_wiring():
    import argparse

    from voicebridge import cli as CLI
    from voicebridge.capability_catalog import check

    names = set()
    for action in CLI.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    assert {"pairing", "webhook", "msg"} <= names
    for token in ("send", "whatsapp", "whatsapp-cloud", "slack"):
        out = check(token)
        assert out["state"] == "IMPLEMENTED", (token, out)


def test_dashboard_inventory_and_tile(tmp_path, monkeypatch):
    from voicebridge.runtime_inventory import runtime_inventory

    cfg = _cfg(tmp_path, monkeypatch)
    inv = runtime_inventory(cfg)
    assert inv["messaging"]["state"] == "OK"
    assert inv["messaging"]["accounts"] == 0
    assert "sk-" not in json.dumps(inv["messaging"])
    html = (Path(__file__).resolve().parents[1] / "frontend" /
            "settings.html").read_text()
    assert "body.messaging" in html and "messaging.accounts" in html
