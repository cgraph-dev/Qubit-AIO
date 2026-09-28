"""Agent 8 — Skill Workshop collection lifecycle (OC-41 / OC-D29).

Proves the agent-owned collection: authoring stays quarantined (no
self-verification), digest-signed bounded backups, human-only restore/rollback
with pre-restore protection, and curator status/contracts over the verified
skill store. Hermetic: SQLite + JSON in tmp_path, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import verified_skills as V


def _proposal(**kw):
    base = dict(name="add_two", interface="add_two(x)",
                implementation="return x + 2",
                acceptance_test="assert run(3)==6 and run(0)==2",
                task="add two to a number", cost="normal", trust=1)
    base.update(kw)
    return V.SkillProposal(**base)


def _workshop(tmp_path):
    store = V.VerifiedSkillStore(tmp_path / "skills.db")
    backups = tmp_path / "workshop" / "backups"
    return store, V.SkillWorkshop(store, backups)


def test_author_stays_quarantined_and_contracts(tmp_path):
    store, ws = _workshop(tmp_path)
    out = ws.author(_proposal(), author_kind="agent", requested_by="agent:8")
    assert out["state"] == "OK"
    assert out["skill"]["status"] == V.ST_QUARANTINED
    assert ws.collection()["count"] == 1
    contracts = ws.contracts()["contracts"]
    assert any(c["id"] == "verified-before-active" for c in contracts)
    assert "owner" in ws.contracts()["curator_roles"]
    store.close()


def test_backup_restore_rollback_roundtrip(tmp_path):
    store, ws = _workshop(tmp_path)
    c = ws.author(_proposal())["skill"]
    sid = c["id"]
    b1 = ws.backup(label="quarantined")
    assert b1["skills"] == 1 and Path(b1["path"]).exists()
    assert oct(Path(b1["path"]).stat().st_mode & 0o777) == "0o600"

    store.apply_verdict(sid, decision="verified", requested_by="user")
    b2 = ws.backup(label="verified")
    assert store.get(sid).status == V.ST_VERIFIED

    # restore the earlier quarantined snapshot
    out = ws.restore(b1["backup_id"], requested_by="user")
    assert out["restored_from"] == b1["backup_id"]
    assert store.get(sid).status == V.ST_QUARANTINED

    # rollback to the most recent (verified) snapshot
    out = ws.rollback(requested_by="user")
    assert store.get(sid).status == V.ST_VERIFIED
    assert ws.backups()["count"] >= 2
    store.close()


def test_restore_and_rollback_are_human_only(tmp_path):
    store, ws = _workshop(tmp_path)
    ws.author(_proposal())
    b = ws.backup()
    with pytest.raises(V.WorkshopError):
        ws.restore(b["backup_id"], requested_by="agent")
    with pytest.raises(V.WorkshopError):
        ws.rollback(requested_by="model")
    store.close()


def test_tampered_backup_fails_closed(tmp_path):
    store, ws = _workshop(tmp_path)
    ws.author(_proposal())
    b = ws.backup()
    path = Path(b["path"])
    payload = json.loads(path.read_text())
    payload["snapshot"]["skills"] = []
    path.write_text(json.dumps(payload))
    listed = ws.backups()["backups"]
    assert listed and listed[0]["state"] == "CORRUPT"
    with pytest.raises(V.WorkshopError):
        ws.restore(b["backup_id"], requested_by="user")
    store.close()


def test_invalid_backup_id_refused(tmp_path):
    store, ws = _workshop(tmp_path)
    with pytest.raises(V.WorkshopError):
        ws.restore("../escape", requested_by="user")
    store.close()


def test_curator_status_and_factory(tmp_path, monkeypatch):
    store, ws = _workshop(tmp_path)
    ws.author(_proposal())
    ws.backup()
    status = ws.curator_status()
    assert status["state"] == "OK" and status["curator"] == "QUBIT-OWNED"
    assert status["contracts_enforced"] >= 3 and status["backups"] == 1
    store.close()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    from voicebridge import config as C

    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)
    from voicebridge.verified_skills import verified_skill_workshop

    store2 = V.VerifiedSkillStore(tmp_path / "factory.db")
    ws2 = verified_skill_workshop(cfg, store=store2)
    assert ws2.curator_status()["state"] == "OK"
    store2.close()
