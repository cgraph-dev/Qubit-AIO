"""Packet 8U — inbound channel framework breadth (OpenClaw audit #19/#20).

Proves the channel registry reports honest credential-reference-derived states,
configures refs without values, validates signed inbound deliveries
(HMAC/timestamp/replay/caps) and never fabricates a connected state or starts
a listener. Hermetic: tmp XDG state only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request
from pathlib import Path

import pytest

SECRET = "channel-ingress-secret-8u"
BREADTH = ("discord", "telegram", "signal", "matrix", "msteams", "imessage",
           "whatsapp", "slack", "sms")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _no_network(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("inbound registry must not touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _sign(secret: str, ts: float, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body,
                    hashlib.sha256).hexdigest()


def _set(cfg, channel="discord", ref="env:CH_SIGNING_SECRET8U",
         events=None):
    from voicebridge import channels_inbound as CI

    return CI.configure(cfg, channel, secret_ref=ref,
                        events=events or ["message"], confirmed=True)


def _scan(root: Path) -> str:
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out.append(p.read_text(errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


def test_catalog_breadth_all_honest():
    from voicebridge import channels_inbound as CI

    assert tuple(CI.CHANNELS) == tuple(CI.CHANNEL_CAPS)
    assert len(CI.CHANNELS) >= 29
    assert set(BREADTH) <= set(CI.CHANNELS)
    for cid in BREADTH:
        caps = CI.CHANNEL_CAPS[cid]
        assert caps["available"] is False
        assert caps["credential_kind"]


def test_catalog_rows_never_connected(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    cat = CI.catalog(cfg)
    assert cat["available"] == []
    assert cat["connected"] == []
    by_id = {r["channel"]: r for r in cat["channels"]}
    assert set(BREADTH) <= set(by_id)
    for cid in BREADTH:
        row = by_id[cid]
        assert row["state"] in ("UNCONFIGURED", "NO_CREDENTIAL",
                                "UNAVAILABLE", "DISABLED"), row
        assert row["available"] is False
        assert row["transport"] not in ("", "https")
    st = CI.status(cfg)
    assert st["listener"] == "NONE"
    assert st["connected"] == 0
    assert st["channels"] == len(CI.CHANNELS)


def test_configure_reference_only_and_redaction(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_SIGNING_SECRET8U", SECRET)
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.configure(cfg, "discord", secret_ref="env:CH_SIGNING_SECRET8U")
    assert excinfo.value.code == CI.CONFIRM_CODE
    assert not (Path(cfg.state_dir()) / CI.STORE_NAME).exists()
    out = _set(cfg)
    assert out["state"] == "OK"
    blob = json.dumps(out) + json.dumps(CI.accounts(cfg))
    assert SECRET not in blob
    assert "CH_SIGNING_SECRET8U" not in blob
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.configure(cfg, "telegram", secret_ref="raw-secret-value",
                     confirmed=True)
    assert excinfo.value.code == CI.BAD_INPUT_CODE
    with pytest.raises(CI.ChannelError) as excinfo:
        _set(cfg, channel="not-a-channel")
    assert excinfo.value.code == CI.UNKNOWN_CODE


def test_configured_state_is_unavailable_not_connected(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_SIGNING_SECRET8U", SECRET)
    _set(cfg)
    row = next(r for r in CI.catalog(cfg)["channels"]
               if r["channel"] == "discord")
    assert row["state"] == "UNAVAILABLE"
    assert row["code"] == CI.UNAVAILABLE_CODE
    assert row["credential"] == "env:<reference>"
    acct = CI.accounts(cfg)["accounts"][0]
    assert acct["state"] == "UNAVAILABLE" and acct["available"] is False
    monkeypatch.delenv("CH_SIGNING_SECRET8U", raising=False)
    row = next(r for r in CI.catalog(cfg)["channels"]
               if r["channel"] == "discord")
    assert row["state"] == "NO_CREDENTIAL"
    assert row["code"] == CI.NO_CREDENTIAL_CODE


def test_receive_typed_validation_failures(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_SIGNING_SECRET8U", SECRET)
    body = b'{"hello":"world"}'
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=body)
    assert excinfo.value.code == CI.NO_CREDENTIAL_CODE
    _set(cfg)
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="ghost", payload=body)
    assert excinfo.value.code == CI.UNKNOWN_CODE
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=body)
    assert excinfo.value.code == CI.BAD_SIGNATURE_CODE
    ts = time.time()
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=body, signature="0" * 64,
                   timestamp=ts)
    assert excinfo.value.code == CI.BAD_SIGNATURE_CODE
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=body,
                   signature=_sign(SECRET, ts - 3600, body),
                   timestamp=ts - 3600)
    assert excinfo.value.code == CI.STALE_CODE
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=body,
                   signature=_sign(SECRET, ts, body), timestamp=ts,
                   event="deploy")
    assert excinfo.value.code == CI.EVENT_REFUSED_CODE
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord",
                   payload=b"x" * (CI.MAX_BODY_BYTES + 1),
                   signature="0" * 64, timestamp=ts)
    assert excinfo.value.code == CI.TOO_LARGE_CODE


def test_receive_valid_signature_gates_and_replay(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_SIGNING_SECRET8U", SECRET)
    _set(cfg)
    body = b'{"hello":"world"}'
    ts = time.time()
    out = CI.receive(cfg, channel="discord", payload=body,
                     signature=_sign(SECRET, ts, body), timestamp=ts,
                     sender="user#1", delivery_id="d-1")
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == CI.UNAVAILABLE_CODE
    assert out["verified"] is True
    assert out["bytes"] == len(body)
    assert SECRET not in json.dumps(out)
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=body,
                   signature=_sign(SECRET, ts, body), timestamp=ts)
    assert excinfo.value.code == CI.REPLAY_CODE
    ts2 = time.time()
    body2 = b'{"hello":"again"}'
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=body2,
                   signature=_sign(SECRET, ts2, body2), timestamp=ts2,
                   delivery_id="d-1")
    assert excinfo.value.code == CI.REPLAY_CODE
    blob = _scan(cfg.state_dir())
    assert SECRET not in blob
    assert "world" not in blob


def test_no_credential_writes_nothing(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_SIGNING_SECRET8U", SECRET)
    _set(cfg)
    monkeypatch.delenv("CH_SIGNING_SECRET8U", raising=False)
    with pytest.raises(CI.ChannelError) as excinfo:
        CI.receive(cfg, channel="discord", payload=b"{}",
                   signature="0" * 64, timestamp=time.time())
    assert excinfo.value.code == CI.NO_CREDENTIAL_CODE
    assert not (Path(cfg.state_dir()) / CI.LOG_NAME).exists()


def test_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("CH_SIGNING_SECRET8U", SECRET)
    _set(cfg)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        assert CI.status(cfg)["estop"] is True
        assert CI.refuse_estop("receive")["code"] == CI.ESTOP_CODE
        with pytest.raises(CI.ChannelError) as excinfo:
            CI.receive(cfg, channel="discord", payload=b"{}",
                       signature="0" * 64, timestamp=time.time())
        assert excinfo.value.code == CI.ESTOP_CODE
        with pytest.raises(CI.ChannelError) as excinfo:
            CI.configure(cfg, "telegram", secret_ref="env:X", confirmed=True)
        assert excinfo.value.code == CI.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_no_network_anywhere(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    monkeypatch.setenv("CH_SIGNING_SECRET8U", SECRET)
    _set(cfg)
    assert CI.catalog(cfg)["state"] == "OK"
    assert CI.status(cfg)["listener"] == "NONE"
    body = b"{}"
    ts = time.time()
    out = CI.receive(cfg, channel="discord", payload=body,
                     signature=_sign(SECRET, ts, body), timestamp=ts)
    assert out["code"] == CI.UNAVAILABLE_CODE
