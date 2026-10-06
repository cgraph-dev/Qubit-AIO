"""Orb live journal tail: parsing, bounded ring, event-driven reads."""
from __future__ import annotations

import json
import time


def test_parse_event_line():
    from voicebridge.orb import stream as S
    assert S.parse_event_line('{"event":"tts.speak","ts":1.5}') == {
        "event": "tts.speak", "ts": 1.5}
    assert S.parse_event_line(b'{"event":"stt.final","ts":2}')["event"] == "stt.final"
    assert S.parse_event_line("") is None
    assert S.parse_event_line("   ") is None
    assert S.parse_event_line("{not json") is None
    assert S.parse_event_line("[1,2,3]") is None
    assert S.parse_event_line('{"ts":1}') is None  # no event name


def test_tail_reads_appended_events(tmp_path):
    from voicebridge.orb import stream as S
    j = tmp_path / "events.jsonl"
    j.write_text('{"event":"old","ts":1}\n', encoding="utf-8")
    got = []
    tail = S.JournalTail(j, max_events=16, on_event=got.append, poll_s=0.02)
    tail.start()
    try:
        time.sleep(0.1)
        # Pre-existing history is NOT replayed (starts at EOF).
        assert tail.recent() == []
        with open(j, "a", encoding="utf-8") as fh:
            fh.write('{"event":"voice_edit.helper.speak","ts":10}\n')
            fh.write('{"event":"voice_edit.helper.speak_done","ts":11}\n')
        deadline = time.time() + 2.0
        while time.time() < deadline and len(tail.recent()) < 2:
            time.sleep(0.02)
        events = [e["event"] for e in tail.recent()]
        assert events == ["voice_edit.helper.speak",
                          "voice_edit.helper.speak_done"], events
        assert [e["event"] for e in got] == events  # on_event fired in order
    finally:
        tail.stop()


def test_tail_ring_is_bounded(tmp_path):
    from voicebridge.orb import stream as S
    j = tmp_path / "events.jsonl"
    j.write_text("", encoding="utf-8")
    tail = S.JournalTail(j, max_events=3, poll_s=0.02)
    tail.start()
    try:
        time.sleep(0.05)
        with open(j, "a", encoding="utf-8") as fh:
            for i in range(6):
                fh.write(json.dumps({"event": "e%d" % i, "ts": i}) + "\n")
        deadline = time.time() + 2.0
        while time.time() < deadline and len(tail.recent()) < 3:
            time.sleep(0.02)
        assert [e["event"] for e in tail.recent()] == ["e3", "e4", "e5"]
        assert tail.recent(limit=1)[0]["event"] == "e5"
    finally:
        tail.stop()


def test_stop_is_safe_and_idempotent(tmp_path):
    from voicebridge.orb import stream as S
    tail = S.JournalTail(tmp_path / "missing.jsonl", poll_s=0.02)
    tail.start()
    tail.stop()
    tail.stop()
    assert tail.recent() == []


def test_missing_path_never_raises():
    from voicebridge.orb import stream as S
    t = S.JournalTail(None)
    t.start()
    assert t.recent() == []
    t.stop()
