"""Agent 6 — OpenClaw ingress: DM access control + rich message/album parsing.

Hermetic: tmp XDG state only, synthetic senders, no network, no live account.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _cfg_estop(cfg):
    from voicebridge.power import power_control_store

    return power_control_store(cfg)


# ------------------------------------------------------------- DM policy
def test_dm_policy_default_deny_and_explicit_allow(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    # no policy at all -> fail closed
    allowed, code = IG.dm_allowed(cfg, "telegram", "synthetic_user")
    assert allowed is False and code == IG.NO_POLICY_CODE
    # configure an explicit allow list, default deny for everyone else
    IG.dm_policy_configure(cfg, "telegram", allow=["Synthetic_User"],
                           confirmed=True)
    assert IG.dm_allowed(cfg, "telegram", "synthetic_user") == (True, "")
    allowed, code = IG.dm_allowed(cfg, "telegram", "other_user")
    assert allowed is False and code == IG.DM_DENIED_CODE


def test_dm_policy_deny_wins_and_wildcards(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    # explicit deny beats wildcard allow
    IG.dm_policy_configure(cfg, "discord", allow=["*"],
                           deny=["banned_user"], confirmed=True)
    assert IG.dm_allowed(cfg, "discord", "anyone") == (True, "")
    allowed, code = IG.dm_allowed(cfg, "discord", "banned_user")
    assert allowed is False and code == IG.DM_DENIED_CODE
    # wildcard deny blocks even an allow-listed sender
    IG.dm_policy_configure(cfg, "slack", allow=["synthetic_user"],
                           deny=["*"], confirmed=True)
    allowed, code = IG.dm_allowed(cfg, "slack", "synthetic_user")
    assert allowed is False and code == IG.DM_DENIED_CODE


def test_dm_policy_explicit_open_policy_default_deny_false(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    IG.dm_policy_configure(cfg, "irc", allow=[], deny=[],
                           default_deny=False, confirmed=True)
    assert IG.dm_allowed(cfg, "irc", "someone") == (True, "")


def test_dm_policy_configure_requires_confirm_and_estop(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(IG.IngressError) as exc:
        IG.dm_policy_configure(cfg, "telegram", allow=["x"])
    assert exc.value.code == IG.CONFIRM_CODE
    assert not (Path(cfg.state_dir()) / IG.DM_NAME).exists()
    with pytest.raises(IG.IngressError) as exc:
        IG.dm_policy_configure(cfg, "Bad Channel!", allow=["x"], confirmed=True)
    assert exc.value.code == IG.BAD_INPUT_CODE
    store = _cfg_estop(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(IG.IngressError) as exc:
            IG.dm_policy_configure(cfg, "telegram", allow=["x"], confirmed=True)
        assert exc.value.code == IG.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_dm_policy_show_remove_roundtrip(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    IG.dm_policy_configure(cfg, "matrix", allow=["@synth:example.org"],
                           confirmed=True)
    shown = IG.dm_policy_show(cfg, "matrix")["policy"]
    assert shown["allow"] == ["@synth:example.org"]
    assert shown["default_deny"] is True
    IG.dm_policy_remove(cfg, "matrix", confirmed=True)
    with pytest.raises(IG.IngressError) as exc:
        IG.dm_policy_show(cfg, "matrix")
    assert exc.value.code == IG.NO_POLICY_CODE


# ------------------------------------------------------- rich messages
def test_parse_plain_text_bounded(tmp_path):
    from voicebridge.communications import ingress as IG

    out = IG.parse_message(b"hello synthetic world")
    assert out["part_count"] == 1 and out["parts"][0]["kind"] == "text"
    assert out["is_album"] is False
    assert out["rich"] is False


def test_parse_rich_attachments_and_album(tmp_path):
    from voicebridge.communications import ingress as IG

    blob = base64.b64encode(b"synthetic-image-bytes").decode()
    payload = json.dumps({
        "text": "Synthetic album caption",
        "album_id": "album-synth-1",
        "attachments": [
            {"kind": "image", "mime": "image/png", "name": "a.png",
             "data": blob},
            {"kind": "image", "mime": "image/jpeg", "name": "b.jpg",
             "data": blob},
            {"kind": "button", "text": "Approve"},
            {"kind": "card", "title": "Synthetic Card"},
        ],
    })
    out = IG.parse_message(payload)
    kinds = [p["kind"] for p in out["parts"]]
    assert kinds == ["text", "image", "image", "button", "card"]
    assert out["part_count"] == 5 and out["is_album"] is True
    assert out["rich"] is True
    img = out["parts"][1]
    assert img["byte_count"] == len(b"synthetic-image-bytes")
    assert img["sha256"]


def test_parse_rejects_oversized_and_malformed(tmp_path):
    from voicebridge.communications import ingress as IG

    too_many = json.dumps([{"kind": "file", "name": f"f{i}"}
                           for i in range(IG.MAX_PARTS + 1)])
    with pytest.raises(IG.IngressError) as exc:
        IG.parse_message('{"parts": ' + too_many + '}')
    assert exc.value.code == IG.TOO_LARGE_CODE
    with pytest.raises(IG.IngressError) as exc:
        IG.parse_message(json.dumps({"attachments": [
            {"kind": "image", "mime": "not a mime", "name": "x"}]}))
    assert exc.value.code == IG.BAD_ATTACHMENT_CODE
    with pytest.raises(IG.IngressError) as exc:
        IG.parse_message(json.dumps({"attachments": [
            {"kind": "image", "data": "!!not-base64!!"}]}))
    assert exc.value.code == IG.BAD_ATTACHMENT_CODE
    with pytest.raises(IG.IngressError) as exc:
        IG.parse_message(json.dumps({"attachments": [
            {"kind": "nonsense"}]}))
    assert exc.value.code == IG.BAD_ATTACHMENT_CODE
    big = base64.b64encode(b"x" * (IG.MAX_PART_BYTES + 1)).decode()
    with pytest.raises(IG.IngressError) as exc:
        IG.parse_message(json.dumps({"attachments": [
            {"kind": "file", "data": big}]}))
    assert exc.value.code == IG.TOO_LARGE_CODE


# --------------------------------------------------------- one broker
def test_dispatch_channel_denies_without_policy(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(IG.IngressError) as exc:
        IG.dispatch(cfg, transport="channel", channel="telegram",
                    sender="synthetic_user", payload=b"hi")
    assert exc.value.code == IG.NO_POLICY_CODE
    assert not (Path(cfg.state_dir()) / IG.INBOUND_NAME).exists()


def test_dispatch_delivers_and_persists_redacted_receipt(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    IG.dm_policy_configure(cfg, "telegram", allow=["synthetic_user"],
                           confirmed=True)
    out = IG.dispatch(cfg, transport="channel", channel="telegram",
                      sender="synthetic_user", payload=b"secret=zzz8a",
                      message_id="m-1")
    assert out["state"] == "DELIVERED"
    assert out["message"]["text_preview"]
    blob = json.dumps(out)
    assert "zzz8a" not in blob
    listed = IG.records(cfg)
    assert listed["count"] == 1
    assert listed["messages"][0]["channel"] == "telegram"
    blob2 = (Path(cfg.state_dir()) / IG.INBOUND_NAME).read_text()
    assert "zzz8a" not in json.dumps(IG.records(cfg))


def test_dispatch_album_through_broker(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    IG.dm_policy_configure(cfg, "discord", allow=["*"], confirmed=True)
    blob = base64.b64encode(b"album-bytes").decode()
    payload = json.dumps({"text": "album", "album_id": "a-1",
                          "attachments": [{"kind": "image", "data": blob},
                                          {"kind": "image", "data": blob}]})
    out = IG.dispatch(cfg, transport="channel", channel="discord",
                      sender="someone", payload=payload)
    assert out["message"]["is_album"] is True
    assert out["message"]["part_count"] == 3


def test_dispatch_estop_fails_closed(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    IG.dm_policy_configure(cfg, "telegram", allow=["*"], confirmed=True)
    store = _cfg_estop(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(IG.IngressError) as exc:
            IG.dispatch(cfg, transport="channel", channel="telegram",
                        sender="someone", payload=b"hi")
        assert exc.value.code == IG.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_dispatch_bad_transport_and_sender(tmp_path, monkeypatch):
    from voicebridge.communications import ingress as IG

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(IG.IngressError) as exc:
        IG.dispatch(cfg, transport="carrier-pigeon", channel="telegram",
                    sender="x", payload=b"hi")
    assert exc.value.code == IG.BAD_TRANSPORT_CODE
    with pytest.raises(IG.IngressError) as exc:
        IG.dispatch(cfg, transport="webhook", channel="sub",
                    sender="\x00bad", payload=b"hi")
    assert exc.value.code == IG.BAD_INPUT_CODE


def test_feishu_recognized_with_typed_unavailable(tmp_path, monkeypatch):
    """OC-D16: Feishu is now in both registries with an honest typed state."""
    from voicebridge import channels_inbound as CI
    from voicebridge import messaging as MG

    assert "feishu" in CI.CHANNELS and "feishu" in MG.PROVIDERS
    cat = {r["channel"]: r for r in CI.catalog(_cfg(tmp_path, monkeypatch))["channels"]}
    assert cat["feishu"]["state"] == "UNCONFIGURED"
    assert cat["feishu"]["available"] is False
    row = next(r for r in MG.provider_catalog()["providers"]
               if r["provider"] == "feishu")
    assert row["state"] == "UNAVAILABLE"
    assert row["code"] == MG.UNAVAILABLE_CODE
