"""Packet 8T-F — journal checkpoints (Hermes checkpoints parity).

Proves named out-of-band journal positions: create records tail seq +
digest without mutating the journal, verify recomputes (tamper-evident),
prune needs explicit confirmation, and names/paths are contained.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _journal(tmp_path: Path) -> Path:
    p = tmp_path / "events.jsonl"
    p.write_text('{"seq": 1, "op": "note"}\n{"seq": 2, "op": "note"}\n')
    return p


def test_create_list_verify_roundtrip(tmp_path):
    from voicebridge import checkpoints as CK

    state = tmp_path / "state"
    journal = _journal(tmp_path)
    created = CK.create(state, journal, "pre-upgrade")
    assert created["state"] == "OK" and created["lines"] == 2
    assert len(created["sha256"]) == 64
    assert (state / "checkpoints.json").exists()
    items = CK.list_all(state)
    assert [i["name"] for i in items] == ["pre-upgrade"]
    assert CK.verify(state, "pre-upgrade")["state"] == "OK"


def test_create_never_mutates_journal(tmp_path):
    from voicebridge import checkpoints as CK

    state = tmp_path / "state"
    journal = _journal(tmp_path)
    before = journal.read_bytes()
    CK.create(state, journal, "a")
    assert journal.read_bytes() == before


def test_tamper_missing_and_bad_names(tmp_path):
    from voicebridge import checkpoints as CK

    state = tmp_path / "state"
    journal = _journal(tmp_path)
    CK.create(state, journal, "a")
    with open(journal, "ab") as fh:
        fh.write(b'{"seq": 3, "op": "evil"}\n')
    out = CK.verify(state, "a")
    assert out["state"] == "TAMPERED"
    assert out["current_lines"] == 3 and out["recorded_lines"] == 2
    assert CK.verify(state, "nope")["state"] == "MISSING"
    with pytest.raises(CK.CheckpointError):
        CK.create(state, journal, "../escape")
    with pytest.raises(CK.CheckpointError):
        CK.create(state, journal, "a")
    with pytest.raises(CK.CheckpointError):
        CK.create(state, "/nonexistent-8tf/j.log", "b")


def test_prune_requires_confirmation(tmp_path):
    from voicebridge import checkpoints as CK

    state = tmp_path / "state"
    journal = _journal(tmp_path)
    CK.create(state, journal, "a")
    assert CK.prune(state, "a", confirm=False)["state"] == "DENIED"
    assert CK.verify(state, "a")["state"] == "OK"
    assert CK.prune(state, "a", confirm=True)["state"] == "OK"
    assert CK.verify(state, "a")["state"] == "MISSING"


def test_plugins_list_maps_to_skills(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge.permissions import PermissionsStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    store = PermissionsStore(tmp_path / "perm.json")
    assert store.skills() == [], "no plugin executes without install"


def test_checkpoints_cli_wiring(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")

    class _Args:
        checkpoints_command = "list"

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_checkpoints(cfg, _Args()) == 0
    assert json.loads(buf.getvalue())["checkpoints"] == []
    assert "checkpoints" in CLI.build_parser().format_help()
