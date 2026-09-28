"""OC-51 (no-hardware) — Logbook work-journal tests.

Hermetic; proves typed screen-capture refusal, append-only journaling,
windowed summaries, redaction of secret-shaped summaries, and the read-only
monitor adapter.
"""
from __future__ import annotations

import json
import stat

from voicebridge.evaluation import logbook as LB


def test_capture_is_typed_unavailable():
    out = LB.capture()
    assert out["state"] == "REFUSED"
    assert out["code"] == LB.CAPTURE_UNAVAILABLE
    assert "no screen access" in out["rollback"]


def test_journal_append_entries_and_mode(tmp_path):
    lb = LB.Logbook(tmp_path / "logbook.jsonl")
    entry = LB.LogbookEntry.create(kind="note", summary="did work",
                                   source="user")
    lb.append(entry)
    items = lb.entries()
    assert len(items) == 1 and items[0]["entry_id"] == entry.entry_id
    mode = stat.S_IMODE((tmp_path / "logbook.jsonl").stat().st_mode)
    assert mode == 0o600


def test_summary_aggregates_kinds_and_sources(tmp_path):
    lb = LB.Logbook(tmp_path / "logbook.jsonl")
    lb.append(LB.LogbookEntry.create(kind="note", summary="a", source="user"))
    lb.append(LB.LogbookEntry.create(kind="note", summary="b",
                                     source="monitor"))
    lb.append(LB.LogbookEntry.create(kind="alert", summary="c",
                                     source="monitor"))
    summary = lb.summary()
    assert summary["entries"] == 3
    assert summary["by_kind"] == {"note": 2, "alert": 1}
    assert summary["by_source"] == {"user": 1, "monitor": 2}
    assert summary["latest"]


def test_secret_shaped_summary_is_redacted(tmp_path):
    lb = LB.Logbook(tmp_path / "logbook.jsonl")
    lb.append(LB.LogbookEntry.create(
        kind="note", summary="token=sk-abcdef0123456789 leaked"))
    blob = json.dumps(lb.entries())
    assert "sk-abcdef0123456789" not in blob
    assert "[REDACTED]" in blob


def test_summary_window_filters_old_entries(tmp_path):
    lb = LB.Logbook(tmp_path / "logbook.jsonl")
    lb.append(LB.LogbookEntry.create(kind="old", summary="x", ts=1.0))
    lb.append(LB.LogbookEntry.create(kind="new", summary="y", ts=10_000_000_000.0))
    recent = lb.entries(window_s=3600)
    assert [e["kind"] for e in recent] == ["new"]


def test_append_requires_entry(tmp_path):
    lb = LB.Logbook(tmp_path / "logbook.jsonl")
    try:
        lb.append({"not": "entry"})  # type: ignore[arg-type]
        raise AssertionError("expected LogbookError")
    except LB.LogbookError as exc:
        assert exc.code == "LOGBOOK_BAD_RECORD"


def test_journal_from_monitor_items():
    items = [
        {"platform": "gmail", "subject": "hi", "safe_url": "https://m/1"},
        {"platform": "slack", "summary": "yo"},
    ]
    entries = LB.journal_from_items(items)
    assert [e.kind for e in entries] == ["gmail", "slack"]
    assert entries[0].evidence_refs == ["https://m/1"]
    assert all(e.source == "monitor" for e in entries)
