"""Packet 8U — messaging channel breadth: typed unavailable states.

Proves Qubit's provider registry recognizes the OpenClaw-breadth channels
(discord/telegram/signal/matrix/msteams/imessage) with honest capabilities:
an unconfigured channel reports a typed UNAVAILABLE state, credential
references stay reference-shaped (env:/file:) and raw secrets are refused,
and no secret value ever reaches receipts or audit output. Hermetic: the
unavailable transports must never touch the network.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import pytest

ADDED_CHANNELS = ("discord", "telegram", "signal", "matrix", "msteams",
                  "imessage")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _no_network(monkeypatch):
    """Any attempt to open a transport must fail the test, not the network."""
    def _boom(req, timeout=None):
        raise AssertionError("unavailable channel must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_registry_contains_openclaw_channels():
    from voicebridge import messaging as MG

    assert tuple(MG.PROVIDERS) == tuple(MG.PROVIDER_CAPS)
    for channel in ADDED_CHANNELS:
        assert channel in MG.PROVIDERS, channel
    # Existing transports stay available; breadth additions stay honest.
    for channel in ("whatsapp-cloud", "slack"):
        assert channel in MG.PROVIDERS
    catalog = MG.provider_catalog()
    by_id = {row["provider"]: row for row in catalog["providers"]}
    assert set(ADDED_CHANNELS) <= set(by_id)
    for channel in ADDED_CHANNELS:
        assert by_id[channel]["state"] == "UNAVAILABLE", channel
        assert by_id[channel]["code"] == MG.UNAVAILABLE_CODE, channel
        assert by_id[channel]["transport"] not in ("", "https"), channel
        assert by_id[channel]["credential_kind"], channel
    assert set(catalog["available"]) == {"whatsapp-cloud", "slack"}
    assert set(ADDED_CHANNELS) <= set(catalog["unavailable"])


def test_unconfigured_channel_reports_typed_unavailable(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    # No account at all: the catalog itself already reports typed UNAVAILABLE.
    catalog = MG.provider_catalog()
    row = next(r for r in catalog["providers"] if r["provider"] == "discord")
    assert row["state"] == "UNAVAILABLE"
    assert row["code"] == MG.UNAVAILABLE_CODE
    # A configured reference on a channel with no adapter is still UNAVAILABLE.
    monkeypatch.setenv("DISCORD_BOT_TOKEN8U", "discord-token-value-8u")
    MG.account_add(cfg, "d1", "discord", "guild", key_ref="env:DISCORD_BOT_TOKEN8U",
                   confirmed=True)
    link = MG.link(cfg, "d1")
    assert link["state"] == "UNAVAILABLE", link
    assert link["code"] == MG.UNAVAILABLE_CODE
    assert "discord-token-value-8u" not in json.dumps(link)
    # A missing credential is a distinct typed state (not a fake success).
    MG.account_add(cfg, "t1", "telegram", "bot",
                   key_ref="env:TELEGRAM_MISSING_TOKEN8U", confirmed=True)
    monkeypatch.delenv("TELEGRAM_MISSING_TOKEN8U", raising=False)
    assert MG.link(cfg, "t1")["state"] == "NO_CREDENTIAL"


def test_send_unavailable_channel_never_fakes_success(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    monkeypatch.setenv("MATRIX_TOKEN8U", "matrix-token-value-8u")
    MG.account_add(cfg, "m1", "matrix", "room", key_ref="env:MATRIX_TOKEN8U",
                   confirmed=True)
    msg_id = MG.draft(cfg, "m1", "@ops:example.org", "token=drop8u",
                      confirmed=True)["message"]["id"]
    with pytest.raises(MG.MessagingError) as excinfo:
        MG.send(cfg, msg_id, confirmed=True)
    assert excinfo.value.code == MG.UNAVAILABLE_CODE
    shown = MG.show(cfg, msg_id)["message"]
    assert shown["state"] == "queued", "must queue, never report sent"


def test_reference_key_accepted_raw_secret_refused(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN8U", "telegram-token-value-8u")
    out = MG.account_add(cfg, "tg", "telegram", "bot",
                         key_ref="env:TELEGRAM_BOT_TOKEN8U", confirmed=True)
    # Public receipt carries the reference kind only, never the value/name.
    assert out["account"]["key_ref_kind"] == "env"
    assert "telegram-token-value-8u" not in json.dumps(out)
    assert "TELEGRAM_BOT_TOKEN8U" not in json.dumps(out)
    secret_file = tmp_path / "token"
    secret_file.write_text("file-secret-value-8u\n")
    out_file = MG.account_add(cfg, "sg", "signal", "device",
                              key_ref=f"file:{secret_file}", confirmed=True)
    assert out_file["account"]["key_ref_kind"] == "file"
    assert "file-secret-value-8u" not in json.dumps(out_file)
    for raw in ("raw-secret-value", "sk-rawsecretvalue123456", "xoxb-rawtoken"):
        with pytest.raises(MG.MessagingError) as excinfo:
            MG.account_add(cfg, "bad", "discord", "guild", key_ref=raw,
                           confirmed=True)
        assert excinfo.value.code == MG.BAD_INPUT_CODE, raw


def test_no_secret_in_receipts_or_audit(tmp_path, monkeypatch):
    from voicebridge import messaging as MG
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    monkeypatch.setenv("MSTEAMS_TOKEN8U", "msteams-token-value-8u")
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        MG.account_add(cfg, "teams", "msteams", "bridge",
                       key_ref="env:MSTEAMS_TOKEN8U", confirmed=True)
        draft_out = MG.draft(cfg, "teams", "channel", "token=bodysecret8u",
                             confirmed=True)
        assert "bodysecret8u" not in json.dumps(draft_out)
        assert "msteams-token-value-8u" not in json.dumps(draft_out)
        msg_id = draft_out["message"]["id"]
        MG.approve(cfg, msg_id, confirmed=True)
        try:
            MG.send(cfg, msg_id, confirmed=True)
        except MG.MessagingError:
            pass
        state_dir = Path(cfg.state_dir())
        events = (state_dir / "events.jsonl").read_text()
        accounts = (state_dir / "msg-accounts.json").read_text()
        outbox = (state_dir / "msg-outbox.json").read_text()
        # The secret VALUE never lands in any store or audit output.
        for blob in (events, accounts, outbox):
            assert "msteams-token-value-8u" not in blob
        # Audit and public reads carry the reference kind, never the var name.
        public = json.dumps(MG.account_list(cfg)) + json.dumps(MG.show(cfg, msg_id))
        assert "MSTEAMS_TOKEN8U" not in public
        assert "MSTEAMS_TOKEN8U" not in events
        assert "msg.account.add" in events
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_cli_and_catalog_still_parse():
    from voicebridge import cli as CLI
    from voicebridge import messaging as MG

    names = set()
    for action in CLI.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    assert {"msg", "gateway"} <= names
    assert "provider_catalog" in dir(MG)
    assert MG.provider_catalog()["state"] == "OK"
