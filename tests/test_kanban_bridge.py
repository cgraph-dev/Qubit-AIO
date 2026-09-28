"""Kanban compatibility layer (``voicebridge/kanban_bridge.py``).

The Qubit-native store is authoritative; the vendored Hermes kanban dashboard
uses the fork's own ``qubit_cli.kanban_db`` store. These tests prove both
stores are reported truthfully and that the explicit migration converges the
vendored data onto the native store idempotently. Hermetic: temp ``XDG_*``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_vendored(cfg, title="VENDORED-task"):
    from voicebridge import qubit_plugins as HP

    engine = HP._kanban_engine(cfg)
    conn = engine._conn(board=None)
    try:
        tid = engine.kanban_db.create_task(
            conn, title=title, created_by="test", board=None,
            initial_status="running")
        conn.commit()
        return tid
    finally:
        conn.close()


def test_stores_reports_both_and_authoritative(tmp_path, monkeypatch):
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_vendored(cfg)
    out = KBG.stores(cfg)
    assert out["state"] == "OK"
    assert out["authoritative"] == "qubit-native"
    assert out["native"]["authoritative"] is True
    assert out["vendored"]["authoritative"] is False
    assert out["vendored"]["task_count"] == 1
    assert out["native"]["task_count"] == 0
    assert out["native"]["default_board"] == "main"
    assert out["vendored"]["default_board"] != "main"


def test_migrate_imports_and_is_idempotent(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_vendored(cfg, "VENDORED-A")

    first = KBG.migrate(cfg)
    assert first["state"] == "OK" and first["imported"] == 1, first
    titles = [t["title"] for t in N.list_tasks(cfg)["tasks"]]
    assert "VENDORED-A" in titles, titles

    second = KBG.migrate(cfg)
    assert second["imported"] == 0 and second["skipped"] == 1, second
    assert len(N.list_tasks(cfg)["tasks"]) == 1


def test_migrate_dry_run_writes_nothing(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_vendored(cfg, "DRYRUN")

    out = KBG.migrate(cfg, dry_run=True)
    assert out["dry_run"] is True and out["imported"] == 1, out
    assert N.list_tasks(cfg)["tasks"] == []
