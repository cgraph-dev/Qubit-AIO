"""D28's `visible` class must reach a consumer, not die at the write seam.

THE DEFECT THIS PINS
--------------------
`office_event.visible` (CHECK IN ('notify','badge','bubble')) is written by every
emit site -- `runner._event` (`runner.py:125/205/248/324`), `agent_memory`
(`:229/:471`), `escalation` (`:418`), `receipts` (`:150`) -- and the live store
holds it (221 rows: 214 badge, 7 bubble). But NO consumer read it:
`tower.activity_feed`'s SELECT did not include the column, and
`office_stream.to_feed` did not carry it. D28 requires the renderer to know which
surface (bubble / badge / popup) an event belongs on *without inventing one*, so
a write-only class is the "event published with no consumer" failure.

These tests pin the RELATIONSHIP: whatever the store holds for `visible` is what
the feed row carries. They do not pin today's count.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from voicebridge import office_stream as OS
from voicebridge.office import tower as TWR


def test_to_feed_carries_visible_from_the_activity_row():
    tower = {
        "characters": [{"agent_id": 1, "slug": "ceo", "position": {"floor": 1, "room_id": 1}}],
        "floorplan": {"floors": [{"number": 1, "rooms": [{"id": 1, "slug": "boardroom"}]}]},
        "activity": [
            {"id": 9, "ts": 1000.0, "kind": "run_started", "agent_id": 1,
             "visible": "badge", "text": "ceo started a turn"},
            {"id": 8, "ts": 999.0, "kind": "consent_denied", "agent_id": 1,
             "visible": "bubble", "text": "ceo was blocked"},
        ],
    }
    feed = OS.to_feed(tower)
    got = {r["id"]: r["visible"] for r in feed}
    assert got == {"9": "badge", "8": "bubble"}


def test_activity_feed_carries_visible_off_office_event(tmp_path):
    """Drive the real store seam: insert a row with a chosen class, read it back."""
    cfg = _cfg_with_state_dir(tmp_path)
    from voicebridge.office import store as store_mod
    conn = store_mod.connect(cfg)
    try:
        store_mod.migrate(conn)
        conn.execute(
            "INSERT INTO office_event (ts, kind, agent_id, payload, visible)"
            " VALUES (?,?,?,?,?)",
            (1000.0, "escalation", None, json.dumps({}), "bubble"))
        conn.commit()
    finally:
        conn.close()
    feed = TWR.activity_feed(cfg, limit=5)
    rows = feed["activity"]
    assert rows, "the feed must return the inserted row"
    assert rows[0]["visible"] == "bubble"


def _cfg_with_state_dir(state_dir: Path):
    """A cfg whose `state_dir()` is a temp dir -- the store resolves its DB there.

    `store.path(cfg)` is `Path(cfg.state_dir()) / 'office' / 'office.db'`, so a
    cfg with a temp state dir gives an isolated store and never touches the live
    one.
    """
    class Cfg:
        def state_dir(self):
            return str(state_dir)
    return Cfg()


def test_the_write_only_class_is_reachable_end_to_end():
    """A guard for the relationship, not a value: the same key flows through
    store -> tower activity row -> feed row."""
    src_tower = (Path(TWR.__file__)).read_text()
    src_stream = (Path(OS.__file__)).read_text()
    assert "e.visible" in src_tower, "tower must SELECT the visible column"
    assert '"visible": row.get("visible")' in src_stream, (
        "office_stream.to_feed must carry visible through to the feed row")
