"""Qubit device sync via device-bound encrypted handoff.

Proves offers are encrypted user-carried bundles with passphrase hygiene,
revocation and device binding are enforced before apply, previews never
write, and enrollment gates the destination.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

PW = "correct horse battery staple"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("VOICE_BRIDGE_PASSPHRASE", PW)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _A:
    def __init__(self, cmd, **kw):
        self.sync_command = cmd
        self.bundle = kw.get("bundle", "")
        self.offer = kw.get("offer", "")
        self.offer_id = kw.get("offer_id", "")
        self.passphrase = kw.get("passphrase", "")
        self.for_device = kw.get("for_device", "")
        self.apply = kw.get("apply", False)
        self.confirm = kw.get("confirm", False)
        self.dry_run = kw.get("dry_run", False)


def _run(cfg, args):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_sync(cfg, args)
    return rc, json.loads(buf.getvalue())


def _offer(cfg, **kw):
    kw.setdefault("confirm", True)
    return _run(cfg, _A("offer", **kw))


def test_offer_list_show_roundtrip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _offer(cfg)
    assert rc == 0, out
    offer = out["offer"]
    assert offer["offer_id"].startswith("sync_")
    assert offer["bytes"] > 0 and offer["classes"]
    assert "sha256" in offer and offer["revoked"] is False
    blob = json.dumps(out)
    assert PW not in blob, "passphrase appears nowhere"
    assert _run(cfg, _A("list"))[1]["offers"][0]["offer_id"] == offer["offer_id"]
    assert _run(cfg, _A("show", offer_id=offer["offer_id"]))[1]["offer"] == offer
    outbox = Path(cfg.state_dir()) / "sync-outbox"
    assert (outbox / f"{offer['offer_id']}.vbp").is_file()
    assert "passphrase" not in (outbox / f"{offer['offer_id']}.json").read_text().lower()


def test_offer_requires_confirm_and_passphrase(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert _offer(cfg, confirm=False)[0] == 3
    monkeypatch.delenv("VOICE_BRIDGE_PASSPHRASE")
    rc, out = _run(cfg, _A("offer", confirm=True, passphrase="short"))
    assert rc == 2 and out["code"] == "SYNC_WEAK_PASSPHRASE", out
    rc, out = _run(cfg, _A("offer", confirm=True, passphrase=""))
    assert rc == 2 and out["code"] == "SYNC_NO_PASSPHRASE", out
    assert _run(cfg, _A("list"))[1]["offers"] == []


def test_revoke_blocks_accept(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    offer_id = _offer(cfg)[1]["offer"]["offer_id"]
    assert _run(cfg, _A("revoke", offer_id=offer_id, confirm=True))[0] == 0
    assert _run(cfg, _A("show", offer_id=offer_id))[1]["offer"]["revoked"] is True
    outbox = Path(cfg.state_dir()) / "sync-outbox"
    assert not (outbox / f"{offer_id}.vbp").exists(), "bundle bytes deleted"
    rc, out = _run(cfg, _A("accept", offer=offer_id))
    assert rc == 2 and out["code"] == "SYNC_REVOKED", out


def test_accept_preview_and_wrong_passphrase(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    offer_id = _offer(cfg)[1]["offer"]["offer_id"]
    rc, out = _run(cfg, _A("accept", offer=offer_id))
    assert rc == 0 and out["applied"] is False
    assert out["plan"]["actions"], "restore plan previewed"
    rc, out = _run(cfg, _A("accept", offer=offer_id,
                           passphrase="wrong passphrase here!!"))
    assert rc == 2 and out["code"] == "SYNC_WRONG_PASSPHRASE", out


def test_accept_apply_needs_enrollment_then_applies(tmp_path, monkeypatch):
    from voicebridge import device as Dev

    cfg = _cfg(tmp_path, monkeypatch)
    offer_id = _offer(cfg)[1]["offer"]["offer_id"]
    rc, out = _run(cfg, _A("accept", offer=offer_id, apply=True, confirm=True))
    assert rc == 2 and out["code"] == "SYNC_DEVICE_MISMATCH", out
    Dev.enroll(cfg, require_new_authorization=False)
    rc, out = _run(cfg, _A("accept", offer=offer_id, apply=True, confirm=True))
    assert rc == 0 and out["applied"] is True, out
    assert _run(cfg, _A("show", offer_id=offer_id))[1]["offer"]["consumed_at"] \
        is not None


def test_device_binding_enforced(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _offer(cfg, for_device="other-pc-123")
    assert rc == 0
    offer_id = out["offer"]["offer_id"]
    rc, out = _run(cfg, _A("accept", offer=offer_id))
    assert rc == 2 and out["code"] == "SYNC_DEVICE_MISMATCH", out


def test_estop_and_audit(tmp_path, monkeypatch):
    from voicebridge import device as Dev
    from voicebridge.logging_setup import setup
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        offer_id = _offer(cfg)[1]["offer"]["offer_id"]
        Dev.enroll(cfg, require_new_authorization=False)
        _run(cfg, _A("accept", offer=offer_id, apply=True, confirm=True))
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "sync.offer" in blob and "sync.accept" in blob
        assert PW not in blob
        power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                     issued_by="cli:user")
        try:
            rc, out = _run(cfg, _A("offer", confirm=True))
            assert rc == 3 and out["code"] == "SYNC_ESTOP", out
        finally:
            power_control_store(cfg).set(emergency_stop=False,
                                         global_disable=False,
                                         issued_by="cli:user")
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_capability_catalog_reports_sync_implemented():
    from voicebridge.capability_catalog import check

    out = check("sync")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"
