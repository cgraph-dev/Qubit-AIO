"""Packet 8T-F — webhook verification kernel: HMAC, freshness, replay.

Proves URL policy (SSRF/token rules), reference-only secrets, real
signature verification, timestamp tolerance, nonce replay protection,
dry-run plans without secret resolution, and e-stop gating.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import time
from contextlib import redirect_stdout


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _add(cfg, sub_id="deploy", url="http://127.0.0.1:9999/hook",
         events=("deploy",), secret="env:WH_SECRET"):
    from voicebridge import webhooks as WH

    return WH.add(cfg, sub_id, url, list(events), secret, confirmed=True)


def _sign(secret, ts, body):
    return hmac.new(secret.encode(),
                    f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def test_add_list_show_remove_roundtrip(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    cfg = _cfg(tmp_path, monkeypatch)
    out = _add(cfg)
    assert out["subscription"]["secret"] == "env:<reference>"
    assert "WH_SECRET" not in json.dumps(out)
    assert len(WH.list_subs(cfg)["subscriptions"]) == 1
    assert WH.show(cfg, "deploy")["subscription"]["id"] == "deploy"
    assert WH.remove(cfg, "deploy", confirmed=True)["removed"] == "deploy"
    assert WH.list_subs(cfg)["subscriptions"] == []


def test_url_policy(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    cfg = _cfg(tmp_path, monkeypatch)
    bad = ["http://example.com/hook",
           "http://127.0.0.1:9999/hook".replace("http://", "http://u:p@"),
           "http://10.0.0.5/hook",
           "ftp://127.0.0.1/hook",
           "not-a-url"]
    for url in bad:
        try:
            WH.add(cfg, "x", url, ["e"], "env:S", confirmed=True)
        except WH.WebhookError as exc:
            assert exc.code == "WEBHOOK_BAD_URL", (url, exc.code)
        else:
            raise AssertionError(f"must refuse {url!r}")
    assert WH.add(cfg, "ok", "https://hooks.slack.com/services/T",
                  ["e"], "env:S", confirmed=True)["state"] == "OK"


def test_verify_valid_bad_stale_replay(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_SECRET", "s3cr3t-signing-key")
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b'{"deploy": "prod-42"}'
    ts = time.time()
    sig = _sign("s3cr3t-signing-key", ts, body)
    out = WH.verify(cfg, "deploy", body, sig, ts)
    assert out["state"] == "VALID" and out["bytes"] == len(body)
    assert "prod-42" not in json.dumps(out), "receipts carry no payload"
    try:
        WH.verify(cfg, "deploy", body, sig, ts)
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_REPLAY"
    else:
        raise AssertionError("replay must fail")
    try:
        WH.verify(cfg, "deploy", body, "0" * 64, time.time())
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_BAD_SIGNATURE"
    else:
        raise AssertionError("forgery must fail")
    try:
        WH.verify(cfg, "deploy", body,
                  _sign("s3cr3t-signing-key", ts - 3600, body), ts - 3600)
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_STALE"
    else:
        raise AssertionError("stale must fail")


def test_verify_auth_missing_and_test_plan(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.delenv("WH_SECRET", raising=False)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    try:
        WH.verify(cfg, "deploy", b"{}", "0" * 64, time.time())
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_AUTH_MISSING"
    else:
        raise AssertionError("missing secret must fail")
    plan = WH.test_plan(cfg, "deploy")
    assert plan["would_execute"] == "nothing: verified deliveries record receipts only"


def test_verify_record_inbox_end_to_end(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_SECRET", "k2")
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b'{"deploy": "ok"}'
    ts = time.time()
    out = WH.verify(cfg, "deploy", body, _sign("k2", ts, body), ts,
                    sender="ci", record_inbox=True)
    assert out["state"] == "VALID" and "inbox_id" in out
    from voicebridge import messaging as MG

    shown = MG.inbox_show(cfg, out["inbox_id"])["message"]
    assert shown["sender"] == "ci" and shown["subscription"] == "deploy"


def test_confirm_and_estop_gates(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, cmd, **kw):
            self.webhook_command = cmd
            self.__dict__.update(kw)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_webhook(cfg, _A("add", sub_id="x",
                                      url="http://127.0.0.1:1/h",
                                      event=["e"], secret="env:S"))
    assert rc == 3, "confirm required"
    _add(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_webhook(cfg, _A("remove", sub_id="deploy",
                                          confirm=True))
        assert rc == 3 and json.loads(buf.getvalue())["code"] == "WEBHOOK_ESTOP"
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")
    assert "webhook" in CLI.build_parser().format_help()
