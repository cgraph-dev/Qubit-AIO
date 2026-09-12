"""OpenClaw closure wave 2 — messaging/channels proving tests.

One proving test per targeted messaging row. Every test drives a real
production function (``messaging.send``/``_provider_request``,
``channels_inbound.ingest``/``run_ingress``/``normalize_event``/
``gmail_watch``, ``pairing.channel_binding``) and mocks only the external
network/credential boundary via an injected transport. No live provider call
is made and no secret value is written to any store or receipt.

Rows: OC-19, OC-20, OC-21, OC-25, OC-D12, OC-D13, OC-D14, OC-D16, OC-D17.
OC-D15 (iMessage) is deliberately not closed: it needs the macOS Messages
private API and has no runnable transport on this Linux host.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _sign(secret: str, body: bytes, ts: float) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body,
                    hashlib.sha256).hexdigest()


def _provision(cfg, monkeypatch, provider, to, *, env, extra=None):
    from voicebridge import messaging as MG

    monkeypatch.setenv(env, f"{provider}-secret-value")
    MG.account_add(cfg, "acct-" + provider, provider, provider + " acct",
                   key_ref=f"env:{env}", extra=extra, confirmed=True)
    msg_id = MG.draft(cfg, "acct-" + provider, to, f"hello {provider}",
                      confirmed=True)["message"]["id"]
    return MG, msg_id


def _send_and_assert(cfg, MG, msg_id, fake):
    out = MG.send(cfg, msg_id, confirmed=True, transport=fake)
    assert out["state"] == "OK"
    assert MG.show(cfg, msg_id)["message"]["state"] == "sent"
    return out


# ------------------------------------------------------------------ OC-19
def test_oc19_breadth_send_adapters_and_honest_gaps(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    catalog = MG.provider_catalog()
    known = {r["provider"] for r in catalog["providers"]}
    assert {"discord", "telegram", "matrix", "feishu", "slack"} <= known

    seen = {}

    def discord(req):
        seen["discord"] = req
        return {"id": "d-1"}

    MG, msg = _provision(cfg, monkeypatch, "discord", "12345",
                         env="DISCORD_W2",)
    _send_and_assert(cfg, MG, msg, discord)
    assert seen["discord"]["provider"] == "discord"
    assert seen["discord"]["url"].endswith("/channels/12345/messages")

    # A channel with no runnable adapter still refuses even when injected.
    MG, msg2 = _provision(cfg, monkeypatch, "imessage", "+1555",
                          env="IMESSAGE_W2")
    try:
        MG.send(cfg, msg2, confirmed=True, transport=lambda req: {"id": "x"})
        raise AssertionError("iMessage must stay typed UNAVAILABLE")
    except MG.MessagingError as exc:
        assert exc.code == MG.UNAVAILABLE_CODE


# ---------------------------------------------------------------- OC-D12
def test_ocd12_discord_send_and_receive(tmp_path, monkeypatch):
    from voicebridge import messaging as MG
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    MG, msg = _provision(cfg, monkeypatch, "discord", "999", env="D12_TOKEN")
    _send_and_assert(cfg, MG, msg, lambda req: {"id": "d12-1"})

    monkeypatch.setenv("D12_SIGN", "discord-secret")
    CI.configure(cfg, "discord", secret_ref="env:D12_SIGN",
                 events=["message"], confirmed=True)
    body = b'{"id": "m1", "content": "hi bot", ' \
           b'"author": {"id": "u1"}}'
    ts = time.time()
    out = CI.ingest(cfg, channel="discord", payload=body,
                    signature=_sign("discord-secret", body, ts),
                    timestamp=ts, sink=lambda m: {"ok": True})
    assert out["state"] == "DELIVERED"
    assert out["message"]["sender"] == "u1"
    assert out["message"]["text"] == "hi bot"


# ---------------------------------------------------------------- OC-D13
def test_ocd13_telegram_send_and_receive(tmp_path, monkeypatch):
    from voicebridge import messaging as MG
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    MG, msg = _provision(cfg, monkeypatch, "telegram", "42", env="D13_TOKEN")
    _send_and_assert(
        cfg, MG, msg,
        lambda req: {"ok": True, "result": {"message_id": 7}})

    monkeypatch.setenv("D13_SIGN", "telegram-secret")
    CI.configure(cfg, "telegram", secret_ref="env:D13_SIGN",
                 events=["message"], confirmed=True)
    body = b'{"update_id": 5, "message": {"message_id": 7, "text": "hello",' \
           b' "from": {"id": 42, "username": "ops"}}}'
    ts = time.time()
    out = CI.ingest(cfg, channel="telegram", payload=body,
                    signature=_sign("telegram-secret", body, ts),
                    timestamp=ts, sink=lambda m: None)
    assert out["state"] == "DELIVERED"
    assert out["message"]["sender"] == "42"
    assert out["message"]["event_id"] == "7"


# ---------------------------------------------------------------- OC-D14
def test_ocd14_slack_send(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    MG, msg = _provision(cfg, monkeypatch, "slack", "#ops", env="D14_TOKEN")
    seen = {}

    def transport(req):
        seen.update(req)
        return {"ok": True, "ts": "1700000000.000100"}

    out = _send_and_assert(cfg, MG, msg, transport)
    assert out["message"]["provider_msg_id"] == "1700000000.000100"
    assert seen["body"] == {"channel": "#ops", "text": "hello slack"}


# ---------------------------------------------------------------- OC-D16
def test_ocd16_feishu_send_and_receive(tmp_path, monkeypatch):
    from voicebridge import messaging as MG
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    MG, msg = _provision(
        cfg, monkeypatch, "feishu", "ops", env="D16_TOKEN",
        extra={"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/x"})
    seen = {}

    def transport(req):
        seen.update(req)
        return {"data": {"message_id": "om_1"}}

    out = _send_and_assert(cfg, MG, msg, transport)
    assert out["message"]["provider_msg_id"] == "om_1"
    assert seen["body"]["msg_type"] == "text"

    monkeypatch.setenv("D16_SIGN", "feishu-secret")
    CI.configure(cfg, "feishu", secret_ref="env:D16_SIGN",
                 events=["message"], confirmed=True)
    body = b'{"event": {"event_id": "e1", "sender": ' \
           b'{"sender_id": {"open_id": "ou_1"}}, ' \
           b'"message": {"message_id": "om_1", ' \
           b'"content": "{\\"text\\": \\"hello feishu\\"}"}}}'
    ts = time.time()
    out = CI.ingest(cfg, channel="feishu", payload=body,
                    signature=_sign("feishu-secret", body, ts),
                    timestamp=ts, sink=lambda m: {"handled": True})
    assert out["state"] == "DELIVERED"
    assert out["message"]["sender"] == "ou_1"
    assert out["message"]["text"] == "hello feishu"


# ---------------------------------------------------------------- OC-D17
def test_ocd17_matrix_send_and_receive(tmp_path, monkeypatch):
    from voicebridge import messaging as MG
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    MG, msg = _provision(
        cfg, monkeypatch, "matrix", "!room:example.org", env="D17_TOKEN",
        extra={"homeserver": "https://matrix.example.org"})
    seen = {}

    def transport(req):
        seen.update(req)
        return {"event_id": "$evt1"}

    out = _send_and_assert(cfg, MG, msg, transport)
    assert out["message"]["provider_msg_id"] == "$evt1"
    assert seen["url"].startswith("https://matrix.example.org/_matrix/client")
    assert "/send/m.room.message/" in seen["url"]

    monkeypatch.setenv("D17_SIGN", "matrix-secret")
    CI.configure(cfg, "matrix", secret_ref="env:D17_SIGN",
                 events=["message"], confirmed=True)
    body = b'{"event_id": "$evt1", "sender": "@ops:example.org", ' \
           b'"content": {"body": "hello matrix"}}'
    ts = time.time()
    out = CI.ingest(cfg, channel="matrix", payload=body,
                    signature=_sign("matrix-secret", body, ts),
                    timestamp=ts, sink=lambda m: None)
    assert out["state"] == "DELIVERED"
    assert out["message"]["text"] == "hello matrix"


# ------------------------------------------------------------------ OC-20
def test_oc20_bounded_ingress_loop_over_injected_receiver(tmp_path,
                                                          monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("OC20_SIGN", "oc20-secret")
    CI.configure(cfg, "feishu", secret_ref="env:OC20_SIGN",
                 events=["message"], confirmed=True)

    ts = time.time()
    bodies = [b'{"text": "one", "id": "e1"}', b'{"text": "two", "id": "e2"}']
    deliveries = [
        {"payload": b, "signature": _sign("oc20-secret", b, ts),
         "timestamp": ts, "event": "message"}
        for b in bodies
    ]
    handled = []

    def receiver():
        return deliveries.pop(0) if deliveries else None

    def sink(message):
        handled.append(message)
        return {"handled": True}

    out = CI.run_ingress(cfg, channel="feishu", receiver=receiver, sink=sink,
                         max_items=10)
    assert out["state"] == "OK"
    assert out["delivered"] == 2 and out["failed"] == 0
    assert [m["text"] for m in handled] == ["one", "two"]

    # No sink: verified delivery stays an honest typed gate.
    b = b'{"text": "three", "id": "e3"}'
    gate = CI.ingest(cfg, channel="feishu", payload=b,
                     signature=_sign("oc20-secret", b, ts), timestamp=ts)
    assert gate["state"] == "UNAVAILABLE" and gate["verified"] is True

    # A bad signature is refused through the same kernel.
    try:
        CI.ingest(cfg, channel="feishu", payload=b, signature="bad",
                  timestamp=ts, sink=sink)
        raise AssertionError("expected signature refusal")
    except CI.ChannelError as exc:
        assert exc.code == CI.BAD_SIGNATURE_CODE


# ------------------------------------------------------------------ OC-21
def test_oc21_channel_dm_binding(tmp_path, monkeypatch):
    from voicebridge import pairing as PAIR

    cfg = _cfg(tmp_path, monkeypatch)
    code = PAIR.encode_setup(cfg, "phone", ["gateway.access"],
                             account="acct-1", confirmed=True)
    assert PAIR.channel_binding(cfg, "acct-1")["code"] == PAIR.NOT_BOUND_CODE
    assert PAIR.accept(cfg, code["code"], confirmed=True)["dm_pairing"] is True
    bound = PAIR.channel_binding(cfg, "acct-1")
    assert bound["state"] == "OK" and bound["bound"] is True
    assert bound["grant_id"]
    assert PAIR.channel_dm_pairings(cfg)["pairings"][0]["account"] == "acct-1"
    missing = PAIR.channel_binding(cfg, "acct-2")
    assert missing["bound"] is False
    assert missing["code"] == PAIR.NOT_BOUND_CODE


# ------------------------------------------------------------------ OC-25
def test_oc25_gmail_watch_and_pubsub(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("OC25_SIGN", "gmail-secret")
    CI.configure(cfg, "gmail", secret_ref="env:OC25_SIGN",
                 events=["message"], confirmed=True)

    assert CI.gmail_watch(cfg)["code"] == CI.UNAVAILABLE_CODE
    assert CI.gmail_watch_status(cfg)["state"] == "UNCONFIGURED"

    seen = {}

    def transport(req):
        seen.update(req)
        return {"watch_id": "w-1", "expiration": "2026-01-01T00:00:00Z",
                "history_id": "42"}

    out = CI.gmail_watch(cfg, topic="projects/p/topics/gmail",
                         transport=transport)
    assert out["state"] == "OK" and out["watch_id"] == "w-1"
    assert seen["type"] == "gmail.watch"
    assert seen["topic"] == "projects/p/topics/gmail"
    stored = CI.gmail_watch_status(cfg)
    assert stored["state"] == "OK" and stored["watch"]["history_id"] == "42"

    data = base64.b64encode(b'{"emailAddress": "a@b", "historyId": 7}').decode()
    envelope = {"message": {"data": data, "messageId": "m-1",
                            "publishTime": "2026-01-01T00:00:00Z"},
                "subscription": "projects/p/subscriptions/s"}
    push = CI.gmail_pubsub(cfg, envelope, token="gmail-secret")
    assert push["verified"] is True and push["message_id"] == "m-1"
