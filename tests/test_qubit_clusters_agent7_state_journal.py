"""Agent 7 — state journal cluster, real-module proofs.

Exercises ``voicebridge.state_journal.HashChainJournal`` directly: append-only
persistence, chain verification, tamper detection (mid-file fail-closed vs
recoverable torn tail), redaction, and deterministic replay.
"""
from __future__ import annotations

import json

from voicebridge.state_journal import HashChainJournal, JournalError


def _journal(tmp_path):
    return HashChainJournal(tmp_path / "state.journal.jsonl",
                            event_prefix="state")


def test_append_verify_persists_across_instances(tmp_path):
    import voicebridge.logging_setup as LS

    old = LS._EVENT_FILE
    journal = _journal(tmp_path)
    journal.append("create", {"id": "a"})
    journal.append("update", {"id": "a", "value": 2})
    # A second instance over the same path must see the same committed chain.
    report = _journal(tmp_path).verify()
    assert report["state"] == "OK" and report["exists"] is True
    assert report["count"] == 2 and report["torn_tail"] is False
    assert LS._EVENT_FILE is old


def test_missing_file_is_ok_and_empty(tmp_path):
    report = _journal(tmp_path).verify()
    assert report == {"state": "OK", "exists": False, "count": 0,
                      "torn_tail": False, "error": ""}


def test_mid_file_corruption_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    journal.append("a", {"id": "1"})
    journal.append("b", {"id": "2"})
    journal.append("c", {"id": "3"})
    path = journal.path
    lines = path.read_text().splitlines()
    lines[1] = "{not json"
    path.write_text("\n".join(lines) + "\n")
    report = _journal(tmp_path).verify()
    assert report["state"] == "failed"
    assert report["torn_tail"] is False
    assert "not a torn tail" in report["error"]


def test_non_object_record_mid_file_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    journal.append("a", {"id": "1"})
    journal.append("b", {"id": "2"})
    journal.append("c", {"id": "3"})
    lines = journal.path.read_text().splitlines()
    lines[1] = "[1, 2, 3]"
    journal.path.write_text("\n".join(lines) + "\n")
    assert _journal(tmp_path).verify()["state"] == "failed"


def test_torn_tail_is_recoverable_prefix(tmp_path):
    journal = _journal(tmp_path)
    journal.append("a", {"id": "1"})
    journal.append("b", {"id": "2"})
    journal.append("c", {"id": "3"})
    lines = journal.path.read_text().splitlines()
    lines[-1] = '{"body": "partial'  # crash mid-write of the final line
    journal.path.write_text("\n".join(lines) + "\n")
    report = _journal(tmp_path).verify()
    assert report["state"] == "OK" and report["torn_tail"] is True
    assert report["count"] == 2


def test_chain_mismatch_detected(tmp_path):
    journal = _journal(tmp_path)
    journal.append("a", {"id": "1"})
    journal.append("b", {"id": "2"})
    lines = journal.path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["chain"] = "0" * 64
    lines[1] = json.dumps(rec)
    journal.path.write_text("\n".join(lines) + "\n")
    report = _journal(tmp_path).verify()
    assert report["state"] == "failed"
    assert "chain mismatch" in report["error"]


def test_secret_payload_is_redacted_but_chain_still_verifies(tmp_path):
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
    journal = _journal(tmp_path)
    journal.append("configure", {"id": "cfg", "token": secret})
    blob = journal.path.read_text()
    assert secret not in blob
    assert journal.verify()["state"] == "OK"


def test_replay_actions_returns_ordered_redacted_history(tmp_path):
    journal = _journal(tmp_path)
    journal.append("create", {"id": "a"})
    journal.append("update", {"id": "a", "value": 2})
    assert journal.replay_actions() == [
        {"action": "create", "payload": {"id": "a"}},
        {"action": "update", "payload": {"id": "a", "value": 2}},
    ]
    assert _journal(tmp_path).replay_actions() == journal.replay_actions()


def test_append_failure_is_typed(tmp_path):
    journal = _journal(tmp_path)
    # A directory at the journal path makes the append open() fail.
    journal.path.mkdir(parents=True, exist_ok=True)
    try:
        journal.append("a", {"id": "1"})
    except JournalError as exc:
        assert "append failed" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected a typed JournalError")
