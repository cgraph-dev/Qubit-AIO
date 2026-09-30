"""Agent 6 — OpenClaw channel + webhook listeners through the one ingress path.

Hermetic: tmp XDG state, synthetic senders/fixtures, HMAC computed locally,
no network, no live accounts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import time
import urllib.request
from pathlib import Path

import pytest

SECRET = "agent6-ingress-secret"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("ingress listeners must not touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _sign(secret, ts, body):
    return hmac.new(secret.encode(), f"{ts}.".encode() + body,
                    hashlib.sha256).hexdigest()


def _configure_channel(cfg, channel="telegram", events=None):
    from voicebridge import channels_inbound as CI

    return CI.configure(cfg, channel, secret_ref="env:AG6_CHANNEL_SECRET",
                        events=events or ["message"], confirmed=True)


def test_channel_listener_delivers_verified_message(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    monkeypatch.setenv("AG6_CHANNEL_SECRET", SECRET)
    _configure_channel(cfg)
    IG.dm_policy_configure(cfg, "telegram", allow=["synthetic_user"],
                           confirmed=True)
    body = json.dumps({"text": "hello from synthetic sender"}).encode()
    ts = time.time()
    out = IG.ChannelListener(cfg).deliver(
        channel="telegram", payload=body, signature=_sign(SECRET, ts, body),
        timestamp=ts, sender="synthetic_user", delivery_id="d-1")
    assert out["state"] == "DELIVERED"
    assert out["message"]["channel"] == "telegram"
    assert out["message"]["part_count"] == 1
    # verification evidence is recorded; no live transport is claimed
    recs = IG.records(cfg)["messages"]
    assert len(recs) == 1 and recs[0]["message_id"] == "d-1"


def test_channel_listener_dm_denied_and_replay(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("AG6_CHANNEL_SECRET", SECRET)
    _configure_channel(cfg)
    IG.dm_policy_configure(cfg, "telegram", allow=["synthetic_user"],
                           confirmed=True)
    body = b'{"text":"hi"}'
    ts = time.time()
    sig = _sign(SECRET, ts, body)
    # a non-allowed sender is refused before dispatch
    with pytest.raises(IG.IngressError) as exc:
        IG.ChannelListener(cfg).deliver(
            channel="telegram", payload=body, signature=sig, timestamp=ts,
            sender="stranger", delivery_id="d-x")
    assert exc.value.code == IG.DM_DENIED_CODE
    # a verified delivery replays only once
    first = IG.ChannelListener(cfg).deliver(
        channel="telegram", payload=body, signature=sig, timestamp=ts,
        sender="synthetic_user", delivery_id="d-r")
    assert first["state"] == "DELIVERED"
    with pytest.raises(CI.ChannelError) as exc:
        IG.ChannelListener(cfg).deliver(
            channel="telegram", payload=body, signature=sig, timestamp=ts,
            sender="synthetic_user", delivery_id="d-r")
    assert exc.value.code == CI.REPLAY_CODE
    assert IG.records(cfg)["count"] == 1


def test_channel_listener_rejects_bad_signature(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("AG6_CHANNEL_SECRET", SECRET)
    _configure_channel(cfg)
    IG.dm_policy_configure(cfg, "telegram", allow=["*"], confirmed=True)
    with pytest.raises(CI.ChannelError) as exc:
        IG.ChannelListener(cfg).deliver(
            channel="telegram", payload=b"{}", signature="0" * 64,
            timestamp=time.time(), sender="synthetic_user")
    assert exc.value.code == CI.BAD_SIGNATURE_CODE
    assert IG.records(cfg)["count"] == 0


def test_webhook_listener_delivers_into_communications(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    monkeypatch.setenv("AG6_WH_SECRET", SECRET)
    WH.add(cfg, "sub1", "http://127.0.0.1:9/hook", ["message"],
           "env:AG6_WH_SECRET", confirmed=True)
    body = json.dumps({"text": "webhook synthetic"}).encode()
    ts = time.time()
    out = IG.WebhookListener(cfg).deliver(
        subscription="sub1", payload=body, signature=_sign(SECRET, ts, body),
        timestamp=ts, delivery_id="w-1")
    assert out["state"] == "DELIVERED"
    assert out["transport"] == "webhook"
    assert out["message"]["channel"] == "sub1"
    assert IG.records(cfg)["count"] == 1


def test_webhook_listener_rejects_bad_signature(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("AG6_WH_SECRET", SECRET)
    WH.add(cfg, "sub2", "http://127.0.0.1:9/hook", ["message"],
           "env:AG6_WH_SECRET", confirmed=True)
    with pytest.raises(WH.WebhookError) as exc:
        IG.WebhookListener(cfg).deliver(
            subscription="sub2", payload=b"{}", signature="deadbeef",
            timestamp=time.time())
    assert exc.value.code == WH.BAD_SIGNATURE_CODE
    assert IG.records(cfg)["count"] == 0


def test_webhook_listener_unknown_subscription(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(WH.WebhookError) as exc:
        IG.WebhookListener(cfg).deliver(
            subscription="ghost", payload=b"{}", signature="x",
            timestamp=time.time())
    assert exc.value.code == WH.NOT_FOUND_CODE
