"""Packet 8U — D24 Qubit-safe session sharing.

Proves ``voicebridge.session_share`` publishes a locally-served,
token-scoped, expiring, revocable read-only view of a redacted action
receipt from the REAL ``SessionStore``:

* ``publish`` requires confirmation and returns the bearer token exactly
  once; only the token HASH lands on disk;
* ``list_shares`` / ``status`` never render a token or token hash;
* ``resolve`` performs a constant-time token check and returns the redacted
  transcript, or a typed ``SHARE_EXPIRED`` / ``SHARE_REVOKED`` /
  ``SHARE_BAD_TOKEN`` refusal;
* ``purge_expired`` drops expired records; the emergency stop blocks the
  surface with ``SHARE_ESTOP``;
* a secret-shaped receipt value never appears in the shared transcript or
  the persisted share store.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from voicebridge import session_share as SS


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _rec(rid, action_id, detail, state="OK", code="OK", created=1.0):
    return {rid: {"receipt_id": rid, "action_id": action_id, "state": state,
                  "code": code, "runtime": "qubit-broker",
                  "created_at": created, "detail": detail}}


def _seed(cfg, receipts):
    Path(cfg.state_dir()).mkdir(parents=True, exist_ok=True)
    (Path(cfg.state_dir()) / "actions.json").write_text(
        json.dumps({"schema": 1, "receipts": receipts, "audit": []}))


def _share_path(cfg):
    return Path(cfg.state_dir()) / "session-shares.json"


def test_publish_confirms_and_returns_token_once(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, _rec("rec-a", "act.send", "invoice alpha sent"))

    denied = SS.publish(cfg, "rec-a")
    assert denied["state"] == "CONFIRMATION_REQUIRED"
    assert denied["code"] == SS.CONFIRM_CODE
    assert not _share_path(cfg).exists()

    out = SS.publish(cfg, "rec-a", ttl_s=120, confirmed=True)
    assert out["state"] == "OK" and out["changed"] is True
    token = out["token"]
    assert token and out["path"] == f"/share/{out['share']['id']}"
    assert out["url"] == out["path"]

    persisted = _share_path(cfg).read_text()
    assert token not in persisted
    assert hashlib.sha256(token.encode()).hexdigest() in persisted
    assert SS._store_path(cfg).stat().st_mode & 0o777 == 0o600

    listed = json.dumps(SS.list_shares(cfg))
    assert token not in listed
    assert "token_hash" not in listed
    assert SS.list_shares(cfg)["count"] == 1

    resolved = SS.resolve(cfg, out["share"]["id"], token)
    assert resolved["state"] == "OK"
    assert resolved["transcript"]["session_id"] == "rec-a"
    assert resolved["transcript"]["detail"] == "invoice alpha sent"
    assert token not in json.dumps(resolved)


def test_resolve_expiry_and_revoke_and_wrong_token(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, _rec("rec-b", "act.draft", "invoice beta drafted"))

    expiring = SS.publish(cfg, "rec-b", ttl_s=60, confirmed=True)
    exp_id = expiring["share"]["id"]
    assert SS.resolve(cfg, exp_id, expiring["token"])["state"] == "OK"

    future = expiring["expires_at"] + 5
    monkeypatch.setattr(SS, "_now", lambda: future)
    gone = SS.resolve(cfg, exp_id, expiring["token"])
    assert gone["state"] == "EXPIRED" and gone["code"] == SS.EXPIRED_CODE

    revoked = SS.publish(cfg, "rec-b", ttl_s=600, confirmed=True)
    rev_id = revoked["share"]["id"]
    assert SS.revoke(cfg, rev_id)["code"] == SS.CONFIRM_CODE
    assert SS.revoke(cfg, rev_id, confirmed=True)["changed"] is True
    assert SS.revoke(cfg, rev_id, confirmed=True)["changed"] is False
    dead = SS.resolve(cfg, rev_id, revoked["token"])
    assert dead["state"] == "REVOKED" and dead["code"] == SS.REVOKED_CODE

    bad = SS.resolve(cfg, rev_id, "not-the-token")
    assert bad["state"] == "DENIED" and bad["code"] == SS.BAD_TOKEN_CODE

    missing = SS.resolve(cfg, "nope", "whatever")
    assert missing["code"] == SS.NOT_FOUND_CODE
    assert SS.revoke(cfg, "nope", confirmed=True)["code"] == SS.NOT_FOUND_CODE


def test_status_and_list_expose_no_tokens(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, {**_rec("rec-1", "act.one", "first"),
                **_rec("rec-2", "act.two", "second", created=2.0)})

    a = SS.publish(cfg, "rec-1", ttl_s=100, confirmed=True)
    b = SS.publish(cfg, "rec-2", ttl_s=200, confirmed=True)
    blob = json.dumps(SS.list_shares(cfg))
    for tok in (a["token"], b["token"]):
        assert tok not in blob
        assert hashlib.sha256(tok.encode()).hexdigest() not in blob
    assert "token" not in {k for s in SS.list_shares(cfg)["shares"]
                           for k in s}

    st = SS.status(cfg)
    assert st["state"] == "OK" and st["count"] == 2 and st["active"] == 2
    assert st["path_prefix"] == "/share"


def test_purge_expired_keeps_active(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, _rec("rec-p", "act.p", "purge me"))

    short = SS.publish(cfg, "rec-p", ttl_s=10, confirmed=True)
    long = SS.publish(cfg, "rec-p", ttl_s=5000, confirmed=True)
    monkeypatch.setattr(SS, "_now", lambda: short["expires_at"] + 1)

    out = SS.purge_expired(cfg)
    assert out["state"] == "OK" and out["purged"] == 1
    assert out["remaining"] == 1
    remaining = SS.list_shares(cfg)["shares"]
    assert [s["id"] for s in remaining] == [long["share"]["id"]]
    assert SS.resolve(cfg, short["share"]["id"], short["token"])["code"] == \
        SS.NOT_FOUND_CODE
    assert SS.resolve(cfg, long["share"]["id"], long["token"])["state"] == "OK"

    assert SS.purge_expired(cfg)["purged"] == 0


def test_estop_blocks_publish_revoke_and_resolve(tmp_path, monkeypatch):
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, _rec("rec-e", "act.e", "estop target"))
    live = SS.publish(cfg, "rec-e", confirmed=True)

    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        assert SS.estop_engaged(cfg) is True
        assert SS.publish(cfg, "rec-e", confirmed=True)["code"] == SS.ESTOP_CODE
        assert SS.revoke(cfg, live["share"]["id"],
                         confirmed=True)["code"] == SS.ESTOP_CODE
        assert SS.resolve(cfg, live["share"]["id"],
                          live["token"])["code"] == SS.ESTOP_CODE
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")
    assert SS.estop_engaged(cfg) is False
    assert SS.resolve(cfg, live["share"]["id"], live["token"])["state"] == "OK"


def test_secret_in_receipt_is_not_in_shared_transcript(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-abcdefghijklmnop123456"
    _seed(cfg, _rec("rec-sec", "act.leak", f"token={secret}"))

    out = SS.publish(cfg, "rec-sec", confirmed=True)
    resolved = SS.resolve(cfg, out["share"]["id"], out["token"])
    blob = json.dumps(resolved)
    assert secret not in blob
    assert "[REDACTED]" in blob

    persisted = _share_path(cfg).read_text()
    assert secret not in persisted
    assert out["token"] not in persisted


def test_bad_input_unavailable_and_corrupt(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    assert SS.publish(cfg, "", confirmed=True)["code"] == SS.BAD_INPUT_CODE
    assert SS.publish(cfg, "rec-x", ttl_s=0,
                      confirmed=True)["code"] == SS.BAD_INPUT_CODE
    assert SS.publish(cfg, "rec-x",
                      confirmed=True)["code"] == SS.UNAVAILABLE_CODE
    assert SS.resolve(cfg, "", "tok")["code"] == SS.BAD_INPUT_CODE
    assert SS.revoke(cfg, "")["code"] == SS.CONFIRM_CODE

    _seed(cfg, _rec("rec-x", "act.x", "known"))
    assert SS.publish(cfg, "rec-x", confirmed=True)["state"] == "OK"
    assert SS.publish(cfg, "missing",
                      confirmed=True)["code"] == SS.NOT_FOUND_CODE
    _share_path(cfg).write_text("{not json")
    assert SS.list_shares(cfg)["code"] == SS.CORRUPT_CODE
    assert SS.status(cfg)["state"] == "UNAVAILABLE"
    assert SS.resolve(cfg, "rec-x", "tok")["code"] == SS.CORRUPT_CODE
