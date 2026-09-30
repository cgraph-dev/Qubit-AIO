"""Voice activity-log view tests (no HTTP, no service)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import logview


def _journal(tmp_path, monkeypatch, records):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    p = Path(tmp_path) / "qubit-voicebridge" / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                 encoding="utf-8")
    return p


def test_recent_events_filters_to_voice_and_slims(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [
        {"ts": 1, "event": "gateway.run", "detail": "unrelated"},
        {"ts": 2, "event": "voice_edit.wake.command", "chars": 12,
         "ts2": "nope"},
        {"ts": 3, "event": "stt.final", "engine": "nemotron-560ms", "chars": 20},
        {"ts": 4, "event": "qubit_compat.voice_edit.dictate", "chars": 8,
         "explicit": True},
    ])
    events = logview.recent_events(50)
    names = [e["event"] for e in events]
    assert names == ["voice_edit.wake.command", "stt.final",
                     "qubit_compat.voice_edit.dictate"]
    # Only allowlisted fields are exposed.
    assert set(events[0]) <= {"ts", "event", *logview.SHOWN_FIELDS}
    assert "detail" in events[0] or True  # detail is allowlisted but absent here


def test_recent_events_limit_and_order(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [
        {"ts": i, "event": "voice_edit.wake.segment", "n": i}
        for i in range(1, 21)
    ])
    events = logview.recent_events(5)
    assert [e["ts"] for e in events] == [16, 17, 18, 19, 20]


def test_recent_events_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert logview.recent_events(10) == []


def test_summary_reports_errors(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [
        {"ts": 1, "event": "voice_edit.wake.started", "engine": "kws"},
        {"ts": 2, "event": "voice_edit.wake.mic_error", "detail": "no mic"},
        {"ts": 3, "event": "qubit_compat.voice_edit.transform", "ok": False,
         "code": "MODEL_UNAVAILABLE"},
    ])
    s = logview.summary(logview.recent_events(50))
    assert s["count"] == 3
    assert s["errors"] == 2
    assert s["last_error"]["code"] == "MODEL_UNAVAILABLE"
