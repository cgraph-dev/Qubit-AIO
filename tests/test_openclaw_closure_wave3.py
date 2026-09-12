"""OpenClaw closure wave 3 — channel feature / E2EE / dynamic-agent proofs.

Every test drives a real production function and mocks only the external
transport or crypto boundary via an injected callable. No live bot, network
call, credential value, or macOS host is required:

* OC-D12 — Discord voice state/follow/transcript + rich messages.
* OC-D13 — Telegram albums / rich messages / mini-app + initData validation.
* OC-D17 — Matrix E2EE send + multi-account homeserver inventory + receive.
* OC-D16 — Feishu dynamic-agent routing config + dispatch.
* OC-D15 — iMessage stays an honest typed UNAVAILABLE (macOS bridge missing).
* OC-19/OC-48/OC-51/OC-56 — the local part is real while the live
  provider/device half remains a typed external gate.

The live provider transports remain credential-gated: Qubit stamps a redacted
audit receipt but never opens the network or stores a secret value.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _provision(cfg, monkeypatch, provider, env, *, extra=None, account=None):
    from voicebridge import messaging as MG

    monkeypatch.setenv(env, f"{provider}-secret-value")
    aid = account or f"acct-{provider}"
    MG.account_add(cfg, aid, provider, provider + " acct",
                   key_ref=f"env:{env}", extra=extra, confirmed=True)
    return MG


# ------------------------------------------------------------------ OC-D12
def test_ocd12_discord_voice_follow_transcript_rich(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    MG = _provision(cfg, monkeypatch, "discord", "D12_BOT_TOKEN")
    seen = []

    def transport(request):
        seen.append(request)
        return {"id": "msg-1"}

    # No transport: every voice/rich feature is a typed UNAVAILABLE.
    for call in (
        lambda: MG.discord_voice_state(cfg, "acct-discord",
                                       channel_id="chan-9", confirmed=True),
        lambda: MG.discord_voice_follow(cfg, "acct-discord", channel_id="c",
                                        user_id="u1", confirmed=True),
        lambda: MG.discord_voice_transcript(cfg, "acct-discord",
                                            channel_id="c", confirmed=True),
        lambda: MG.discord_rich_message(cfg, "acct-discord", channel_id="c",
                                        content="hi", confirmed=True),
    ):
        with pytest.raises(MG.MessagingError) as exc:
            call()
        assert exc.value.code == MG.UNAVAILABLE_CODE

    # Confirmation is required before any transport is invoked.
    with pytest.raises(MG.MessagingError) as exc:
        MG.discord_voice_state(cfg, "acct-discord", channel_id="chan-9",
                               confirmed=False, transport=transport)
    assert exc.value.code == MG.CONFIRM_CODE

    state = MG.discord_voice_state(cfg, "acct-discord", channel_id="chan-9",
                                   guild_id="guild-1", confirmed=True,
                                   transport=transport)
    assert state["state"] == "OK" and state["joined"] is True
    assert seen[-1]["op"] == 4 and seen[-1]["d"]["channel_id"] == "chan-9"

    follow = MG.discord_voice_follow(cfg, "acct-discord", channel_id="chan-9",
                                     user_id="user-7", confirmed=True,
                                     transport=transport)
    assert follow["following"] == "user-7"
    assert seen[-1]["type"] == "voice.follow"

    transcript = MG.discord_voice_transcript(
        cfg, "acct-discord", channel_id="chan-9", confirmed=True,
        transport=lambda req: {"transcript": "hello team", "segments": 2})
    assert transcript["transcript"] == "hello team"
    assert transcript["segments"] == 2

    rich = MG.discord_rich_message(
        cfg, "acct-discord", channel_id="chan-9",
        embeds=[{"title": "deploy ok"}], confirmed=True, transport=transport)
    assert rich["state"] == "OK"
    assert seen[-1]["body"]["embeds"] == [{"title": "deploy ok"}]
    assert seen[-1]["url"].endswith("/channels/chan-9/messages")

    # An empty rich message is refused before the boundary.
    with pytest.raises(MG.MessagingError) as exc:
        MG.discord_rich_message(cfg, "acct-discord", channel_id="chan-9",
                                confirmed=True, transport=transport)
    assert exc.value.code == MG.BAD_INPUT_CODE


# ------------------------------------------------------------------ OC-D13
def test_ocd13_telegram_albums_rich_miniapp_initdata(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    MG = _provision(cfg, monkeypatch, "telegram", "D13_BOT_TOKEN")
    seen = {}

    def transport(request):
        seen.update(request)
        return {"ok": True, "result": [{"message_id": 11}]}

    album = MG.telegram_album(
        cfg, "acct-telegram", chat_id="42",
        media=[{"type": "photo", "media": "https://cdn.example/p1.jpg"}],
        caption="pics", confirmed=True, transport=transport)
    assert album["state"] == "OK" and album["count"] == 1
    assert seen["type"] == "sendMediaGroup"
    assert seen["body"]["media"][0]["type"] == "photo"

    rich = MG.telegram_rich(
        cfg, "acct-telegram", chat_id="42", text="bold move",
        parse_mode="HTML",
        entities=[{"type": "bold", "offset": 0, "length": 4}],
        confirmed=True,
        transport=lambda req: {"ok": True, "result": {"message_id": 12}})
    assert rich["provider_msg_id"] == "12"

    mini = MG.telegram_mini_app(
        cfg, "acct-telegram", chat_id="42", text="open",
        mini_app_url="https://app.example/x", confirmed=True,
        transport=lambda req: {"ok": True, "result": {"message_id": 13}})
    assert mini["provider_msg_id"] == "13"
    assert mini["mini_app_url"] == "https://app.example/x"

    # Without a transport the features are typed UNAVAILABLE.
    with pytest.raises(MG.MessagingError) as exc:
        MG.telegram_album(cfg, "acct-telegram", chat_id="42",
                          media=[{"type": "photo", "media": "https://x/p"}],
                          confirmed=True)
    assert exc.value.code == MG.UNAVAILABLE_CODE
    # A non-https mini-app URL is refused before the boundary.
    with pytest.raises(MG.MessagingError) as exc:
        MG.telegram_mini_app(cfg, "acct-telegram", chat_id="42", text="x",
                             mini_app_url="http://evil", confirmed=True,
                             transport=transport)
    assert exc.value.code == MG.BAD_INPUT_CODE

    # Telegram WebApp initData HMAC validation is pure and local.
    token = "123456:ABC-DEF"
    fields = [("auth_date", "1700000000"), ("query_id", "q-1"),
              ("user", json.dumps({"id": 7, "username": "ops"}))]
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    init = "&".join(urllib.parse.quote(k, safe="") + "=" +
                    urllib.parse.quote(v, safe="")
                    for k, v in fields + [("hash", digest)])
    good = MG.telegram_validate_init_data(init, token)
    assert good["valid"] is True and good["auth_date"] == 1700000000
    bad = MG.telegram_validate_init_data(init.replace(digest, "0" * 64), token)
    assert bad["valid"] is False and bad["state"] == "INVALID"
    assert MG.telegram_validate_init_data("", token)["valid"] is False


# ------------------------------------------------------------------ OC-D17
def test_ocd17_matrix_e2ee_and_multi_homeserver(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    MG = _provision(
        cfg, monkeypatch, "matrix", "D17_A",
        extra={"homeserver": "https://matrix.a.example"})
    MG.account_add(cfg, "acct-matrix-b", "matrix", "matrix b",
                   key_ref="env:D17_B",
                   extra={"homeserver": "https://matrix.b.example"},
                   confirmed=True)

    inventory = MG.matrix_accounts(cfg)
    assert set(inventory["homeservers"]) == {
        "https://matrix.a.example", "https://matrix.b.example"}

    # E2EE without a crypto boundary is a typed refusal (no plaintext).
    with pytest.raises(MG.MessagingError) as exc:
        MG.matrix_e2ee_send(cfg, "acct-matrix", room_id="!room:x", body="hi",
                            confirmed=True)
    assert exc.value.code == MG.MATRIX_E2EE_CODE
    # Crypto present but no transport is still UNAVAILABLE.
    with pytest.raises(MG.MessagingError) as exc:
        MG.matrix_e2ee_send(
            cfg, "acct-matrix", room_id="!room:x", body="hi", confirmed=True,
            crypto=lambda req: {"ciphertext": "ct"})
    assert exc.value.code == MG.UNAVAILABLE_CODE

    seen = {}

    def transport(request):
        seen.update(request)
        return {"event_id": "$evt-1"}

    out = MG.matrix_e2ee_send(
        cfg, "acct-matrix", room_id="!room:x", body="hi", confirmed=True,
        crypto=lambda req: {"ciphertext": "ct",
                            "algorithm": "m.megolm.v1.aes-sha2"},
        transport=transport)
    assert out["encrypted"] is True
    assert seen["body"]["ciphertext"] == "ct"
    assert "/send/m.room.encrypted/" in seen["url"]

    # Receive half: encrypted events normalize as opaque and only decrypt
    # through an injected olm/megolm boundary.
    from voicebridge import channels_inbound as CI

    event = json.dumps({
        "sender": "@ops:x", "event_id": "$evt-1",
        "content": {"algorithm": "m.megolm.v1.aes-sha2",
                    "ciphertext": "ct"}}).encode()
    normalized = CI.normalize_event("matrix", event)
    assert normalized["encrypted"] is True and normalized["text"] == ""
    with pytest.raises(CI.ChannelError) as exc:
        CI.decrypt_event("matrix", event)
    assert exc.value.code == CI.E2EE_UNAVAILABLE_CODE
    decoded = CI.decrypt_event("matrix", event,
                               decrypt=lambda req: {"body": "clear text"})
    assert decoded["state"] == "OK" and decoded["text"] == "clear text"


# ------------------------------------------------------------------ OC-D16
def test_ocd16_feishu_dynamic_agent_routing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    MG = _provision(
        cfg, monkeypatch, "feishu", "D16_TOKEN",
        extra={"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/x"})
    monkeypatch.setenv("D16_APP_ID", "cli_xxx")

    with pytest.raises(MG.MessagingError) as exc:
        MG.feishu_agent_add(cfg, "triage", app_id_ref="env:D16_APP_ID")
    assert exc.value.code == MG.CONFIRM_CODE
    MG.feishu_agent_add(cfg, "triage", app_id_ref="env:D16_APP_ID",
                        allowlist=["ops"], confirmed=True)
    assert MG.feishu_agents(cfg)["agents"][0]["id"] == "triage"
    assert MG.feishu_agents(cfg)["agents"][0]["app_id_ref_kind"] == "env"

    # Outside the allowlist is refused before any transport call.
    with pytest.raises(MG.MessagingError) as exc:
        MG.feishu_send_as_agent(cfg, "acct-feishu", to="stranger", body="x",
                                agent_id="triage", confirmed=True,
                                transport=lambda req: {"data": {}})
    assert exc.value.code == MG.BAD_INPUT_CODE
    # No transport: typed UNAVAILABLE (the hosted app stays credential-gated).
    with pytest.raises(MG.MessagingError) as exc:
        MG.feishu_send_as_agent(cfg, "acct-feishu", to="ops", body="x",
                                agent_id="triage", confirmed=True)
    assert exc.value.code == MG.UNAVAILABLE_CODE

    seen = {}

    def transport(request):
        seen.update(request)
        return {"data": {"message_id": "om_1"}}

    out = MG.feishu_send_as_agent(cfg, "acct-feishu", to="ops", body="page",
                                  agent_id="triage", confirmed=True,
                                  transport=transport)
    assert out["provider_msg_id"] == "om_1" and out["agent_id"] == "triage"
    assert seen["agent_id"] == "triage"


# ------------------------------------------------------------------ OC-D15
def test_ocd15_imessage_remains_typed_unavailable(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    MG = _provision(cfg, monkeypatch, "imessage", "D15_BRIDGE")
    msg_id = MG.draft(cfg, "acct-imessage", "+15551234567", "hi",
                      confirmed=True)["message"]["id"]
    with pytest.raises(MG.MessagingError) as exc:
        MG.send(cfg, msg_id, confirmed=True, transport=lambda r: {"id": "x"})
    assert exc.value.code == MG.UNAVAILABLE_CODE
    link = MG.link(cfg, "acct-imessage")
    assert link["state"] == "UNAVAILABLE" and link["code"] == MG.UNAVAILABLE_CODE
    # No macOS bridge is fabricated and nothing was sent.
    assert MG.show(cfg, msg_id)["message"]["state"] == "queued"


# ------------------------------------------------------------------ OC-19
def test_oc19_channel_breadth_stays_honest(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import messaging as MG

    catalog = MG.provider_catalog()
    assert set(catalog["available"]) == {"whatsapp-cloud", "slack"}
    by_id = {r["provider"]: r for r in catalog["providers"]}
    for channel in ("discord", "telegram", "signal", "matrix", "msteams",
                    "imessage", "feishu"):
        assert by_id[channel]["state"] == "UNAVAILABLE", channel
        assert by_id[channel]["code"] == MG.UNAVAILABLE_CODE, channel


# ------------------------------------------------------------------ OC-48
def test_oc48_workspace_memory_local_and_external_gate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.memory import workspace as WS

    by_name = {p["name"]: p for p in WS.providers()["providers"]}
    assert by_name["markdown-workspace"]["state"] == "AVAILABLE"
    for external in ("lancedb", "wiki", "honcho"):
        assert by_name[external]["state"] == "UNAVAILABLE"
        assert by_name[external]["code"] == WS.UNAVAILABLE_CODE
    assert WS.write_note(cfg, "n1", "note body", confirmed=True)["state"] == "OK"
    assert WS.dream(cfg, confirmed=True)["state"] == "OK"


# ------------------------------------------------------------------ OC-51
def test_oc51_logbook_capture_boundary(tmp_path, monkeypatch):
    from voicebridge.evaluation import logbook as LB

    book = LB.Logbook(tmp_path / "logbook.jsonl")
    assert LB.capture()["code"] == LB.CAPTURE_UNAVAILABLE
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    out = LB.capture_series(book, grabber=lambda: png, count=1,
                            evidence_dir=tmp_path / "ev", confirmed=True)
    assert out["state"] == "OK" and out["captured"] == 1
    # Unconfirmed capture is refused; nothing is journaled.
    assert LB.capture_series(book, grabber=lambda: png, count=1,
                             confirmed=False)["state"] == "REFUSED"


# ------------------------------------------------------------------ OC-56
def test_oc56_node_media_transport_needs_confirmation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import node_host as NH

    NH.enroll(cfg, name="studio", endpoint="https://node.example:8443",
              capabilities=["screen", "audio", "media_playback"],
              confirmed=True, source="cli")
    node_id = NH.list_nodes(cfg)["nodes"][0]["id"]
    assert NH.dispatch(cfg, node_id, "screen", confirmed=True)["code"] == \
        NH.NODE_DISPATCH_REFUSED
    assert NH.dispatch(cfg, node_id, "audio", confirmed=False,
                       transport=lambda r: {"ok": True})["code"] == \
        NH.NODE_DISPATCH_REFUSED
    out = NH.dispatch(cfg, node_id, "media_playback", confirmed=True,
                      transport=lambda r: {"state": "playing"})
    assert out["state"] == "OK" and out["result"]["state"] == "playing"


# ------------------------------------------------- OC-01/13/14/37 (GATED)
def test_gated_rows_stay_typed_and_never_start_a_second_authority():
    """The remaining open rows are honest typed gates, not fabricated paths."""
    from voicebridge import computer_use as CU
    from voicebridge import gateway_peers as GP
    from voicebridge import ghost
    from voicebridge import kanban as KN

    # OC-01: no second gateway listener is ever started.
    assert GP.serve_refusal("serve")["code"] == GP.GATEWAY_NO_SERVER_CODE
    # OC-13: desktop control has no shipped actuator.
    assert CU.refuse_install()["code"] == CU.NO_DRIVER_CODE
    # OC-14: the ghost adapter is hard-disabled unless an operator opts in.
    assert ghost.ghost_shell_enabled(env={}) is False
    assert ghost.ghost_shell_enabled(
        env={"VOICE_BRIDGE_GHOST_SHELL": "on"}) is True
    # OC-37: workboard dispatch/daemon/scheduler is a typed delegation.
    assert KN.refuse_delegated("swarm")["code"] == KN.DELEGATED_CODE
