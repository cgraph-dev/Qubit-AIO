"""Packet 8U — session lifecycle: search/show/rename/archive/delete/resume.

Proves the Qubit session store behind ``qubit sessions``
(``voicebridge.auth_status.SessionStore``) adds idempotent, audited, redacted
lifecycle operations over the append-only action receipts:

* ``search``/``show``/``rename``/``archive`` round-trip through disk;
* ``delete`` requires explicit confirmation and refuses a replay;
* secret-shaped names are redacted in output, the persisted store, and audit;
* lifecycle metadata survives a store reopen while the receipt is never
  mutated, so a deleted session is recoverable via ``resume``.
"""
from __future__ import annotations

import io
import json
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


def _rec(rid, action_id, detail, state="OK", code="OK", created=1.0):
    return {rid: {"receipt_id": rid, "action_id": action_id, "state": state,
                  "code": code, "runtime": "qubit-broker",
                  "created_at": created, "detail": detail}}


def _seed(cfg, receipts):
    Path(cfg.state_dir()).mkdir(parents=True, exist_ok=True)
    (Path(cfg.state_dir()) / "actions.json").write_text(
        json.dumps({"schema": 1, "receipts": receipts, "audit": []}))


def _store(cfg):
    from voicebridge.auth_status import SessionStore
    return SessionStore(cfg.state_dir())


def test_search_show_rename_archive_roundtrip(tmp_path, monkeypatch):
    from voicebridge.auth_status import recent_sessions

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, {**_rec("rec-a", "act.send", "invoice alpha sent", created=3.0),
                **_rec("rec-b", "act.draft", "invoice beta drafted", created=2.0),
                **_rec("rec-c", "act.notes", "meeting notes", created=1.0)})
    store = _store(cfg)

    hits = store.search("invoice")
    assert hits["state"] == "OK" and hits["count"] == 2
    assert {h["receipt_id"] for h in hits["sessions"]} == {"rec-a", "rec-b"}
    assert store.search("")["code"] == "EMPTY_QUERY"

    shown = store.show("rec-a")
    assert shown["state"] == "OK"
    assert shown["session"]["detail"] == "invoice alpha sent"
    assert store.show("nope")["code"] == "SESSION_NOT_FOUND"

    re1 = store.rename("rec-a", "Paid invoices")
    assert re1["state"] == "OK" and re1["changed"] is True
    assert store.show("rec-a")["session"]["name"] == "Paid invoices"
    re2 = store.rename("rec-a", "Paid invoices")
    assert re2["changed"] is False and re2["code"] == "ALREADY_NAMED"

    ar1 = store.archive("rec-a")
    assert ar1["state"] == "OK" and ar1["changed"] is True
    assert store.show("rec-a")["session"]["archived"] is True
    ar2 = store.archive("rec-a")
    assert ar2["changed"] is False and ar2["code"] == "ALREADY_ARCHIVED"

    listed = store.list()
    row = next(r for r in listed["sessions"] if r["receipt_id"] == "rec-a")
    assert row["archived"] is True

    rs = recent_sessions(cfg, 5)
    assert rs["state"] == "OK"
    names = {r["receipt_id"]: r["name"] for r in rs["sessions"]}
    assert names["rec-a"] == "Paid invoices"

    back = store.resume("rec-a")
    assert back["changed"] is True
    assert store.show("rec-a")["session"]["archived"] is False
    assert store.resume("rec-a")["code"] == "ALREADY_ACTIVE"


def test_delete_requires_confirmation_and_refuses_replay(tmp_path, monkeypatch):
    from voicebridge.auth_status import recent_sessions

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, _rec("rec-del", "act.sent", "irreversible send"))
    store = _store(cfg)

    denied = store.delete("rec-del")
    assert denied["state"] == "CONFIRMATION_REQUIRED"
    assert denied["code"] == "CONFIRM_REQUIRED"
    assert store.show("rec-del")["session"]["deleted"] is False

    ok = store.delete("rec-del", confirm=True)
    assert ok["state"] == "OK" and ok["changed"] is True
    assert store.show("rec-del")["session"]["deleted"] is True

    audit_before = len(store.audit())
    replay = store.delete("rec-del", confirm=True)
    assert replay["state"] == "ALREADY_DELETED"
    assert replay["changed"] is False
    assert len(store.audit()) == audit_before + 1

    assert all(r["receipt_id"] != "rec-del"
               for r in recent_sessions(cfg, 5)["sessions"])

    back = store.resume("rec-del")
    assert back["changed"] is True
    assert store.show("rec-del")["session"]["deleted"] is False
    assert any(r["receipt_id"] == "rec-del"
               for r in recent_sessions(cfg, 5)["sessions"])


def test_redaction_in_output_and_persisted_store(tmp_path, monkeypatch):
    from voicebridge.auth_status import recent_sessions

    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-abcdefghijklmnop123456"
    _seed(cfg, _rec("rec-sec", "act.leak", f"token={secret}"))
    store = _store(cfg)

    out = store.rename("rec-sec", f"key {secret}")
    blob = json.dumps(out)
    assert secret not in blob
    assert "[REDACTED]" in blob

    persisted = (Path(cfg.state_dir()) / "sessions.json").read_text()
    assert secret not in persisted
    assert secret not in json.dumps(store.audit())
    assert secret not in json.dumps(store.show("rec-sec"))
    assert secret not in json.dumps(recent_sessions(cfg, 5))


def test_persistence_across_reopen(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, _rec("rec-p", "act.p", "persist me"))

    s1 = _store(cfg)
    s1.rename("rec-p", "Persisted name")
    s1.archive("rec-p")
    s1.delete("rec-p", confirm=True)

    s2 = _store(cfg)
    row = s2.show("rec-p")["session"]
    assert row["name"] == "Persisted name"
    assert row["archived"] is True
    assert row["deleted"] is True

    s2.resume("rec-p")
    s3 = _store(cfg)
    row3 = s3.show("rec-p")["session"]
    assert row3["name"] == "Persisted name"
    assert row3["archived"] is False and row3["deleted"] is False


def test_cli_sessions_subcommands_roundtrip(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, _rec("rec-cli", "act.cli", "cli session target"))
    parser = CLI.build_parser()

    args = parser.parse_args(["sessions", "rename", "rec-cli", "Renamed CLI"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_sessions(cfg, args) == 0
    renamed = json.loads(buf.getvalue())
    assert renamed["session"]["name"] == "Renamed CLI"

    args = parser.parse_args(["sessions", "search", "target"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_sessions(cfg, args) == 0
    assert json.loads(buf.getvalue())["count"] == 1

    args = parser.parse_args(["sessions", "delete", "rec-cli"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_sessions(cfg, args) == 3
    assert json.loads(buf.getvalue())["code"] == "CONFIRM_REQUIRED"

    args = parser.parse_args(["sessions", "delete", "rec-cli", "--confirm"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_sessions(cfg, args) == 0
    assert json.loads(buf.getvalue())["changed"] is True

    args = parser.parse_args(["sessions", "resume", "rec-cli"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_sessions(cfg, args) == 0
    assert json.loads(buf.getvalue())["changed"] is True
