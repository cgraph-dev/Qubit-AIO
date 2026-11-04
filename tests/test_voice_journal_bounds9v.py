"""Bounded journal reads, journal rotation, and transcript privacy.

The live voice journal reached 361 MB because every append survived forever
and every reader loaded the whole file. These tests pin the three fixes:

* byte-bounded tail reads (never ``read_text()`` on the journal),
* size-based rotation with a small keeper set,
* ambient speech is persisted only when verbose transcript logging is on,
  and discarded/media-drop wake transcripts carry no words at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import journal_io
from voicebridge import logging_setup as ls


def _write_journal(path: Path, n: int) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"ts": i, "event": "probe", "n": i}) + "\n")


def test_tail_lines_is_byte_bounded_and_keeps_newest(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_journal(path, 20_000)
    lines = journal_io.tail_lines(path, max_bytes=4096)
    assert 0 < len(lines) < 20_000
    # The newest record is always present; a partial first line is dropped.
    assert json.loads(lines[-1])["n"] == 19_999
    assert all(json.loads(line)["event"] == "probe" for line in lines)


def test_tail_lines_honours_a_count_limit(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_journal(path, 100)
    lines = journal_io.tail_lines(path, max_bytes=1_000_000, limit=7)
    assert len(lines) == 7
    assert json.loads(lines[-1])["n"] == 99


def test_count_lines_streams_the_whole_file(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_journal(path, 123)
    assert journal_io.count_lines(path) == 123


def test_activity_feed_loads_only_a_bounded_tail(tmp_path, monkeypatch):
    from voicebridge import webui

    path = tmp_path / "events.jsonl"
    _write_journal(path, 10)
    seen = {}
    real = journal_io.tail_lines

    def spy(p, max_bytes=journal_io.DEFAULT_TAIL_BYTES, limit=None, **kw):
        seen["max_bytes"] = max_bytes
        return real(p, max_bytes=max_bytes, limit=limit, **kw)

    monkeypatch.setattr(journal_io, "tail_lines", spy)
    feed = webui.ActivityFeed()
    feed.load_history(path, limit=50)
    assert seen["max_bytes"] <= 4_000_000


def test_rotation_bounds_the_journal_and_keeps_backups(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(ls, "_ROTATE_CHECK_S", 0.0)
    ls.setup(jsonl=True, jsonl_path=path, max_bytes=2000, backups=2)
    try:
        for i in range(400):
            ls.log_event("rotation.probe", n=i)
        assert path.exists()
        assert path.stat().st_size <= 4000
        assert path.with_name(path.name + ".1").exists()
    finally:
        ls._EVENT_FILE = None
        ls._EVENT_MAX_BYTES = ls.DEFAULT_EVENT_MAX_BYTES
        ls._EVENT_BACKUPS = ls.DEFAULT_EVENT_BACKUPS
        ls._last_rotate_check = 0.0


def test_transcript_fields_require_opt_in():
    try:
        ls.set_transcripts_enabled(False)
        assert ls.transcript_fields(text="ambient room speech") == {}
        ls.set_transcripts_enabled(True)
        got = ls.transcript_fields(text="ambient room speech")
        assert got == {"text": "ambient room speech"}
    finally:
        ls.set_transcripts_enabled(False)


def test_whisper_journal_omits_words_unless_transcripts_enabled(tmp_path,
                                                               monkeypatch):
    from voicebridge.asr import whisper as whisper_mod

    captured = []

    class _Seg:
        text = "the television said something"
        start = 0.0
        end = 1.0
        avg_logprob = -0.1

    class _Info:
        duration = 1.0

    asr = whisper_mod.FasterWhisperASR()
    monkeypatch.setattr(asr, "load", lambda: None)
    monkeypatch.setattr(asr, "_transcribe_once",
                        lambda _p: ([_Seg()], _Info()))
    monkeypatch.setattr(whisper_mod, "log_event",
                        lambda event, **kw: captured.append((event, kw)))
    try:
        ls.set_transcripts_enabled(False)
        asr.transcribe("probe.wav")
        assert captured[-1][0] == "asr.transcribe"
        assert captured[-1][1]["chars"] == len(_Seg.text)
        assert "text" not in captured[-1][1]

        ls.set_transcripts_enabled(True)
        asr.transcribe("probe.wav")
        assert captured[-1][1]["text"] == _Seg.text
    finally:
        ls.set_transcripts_enabled(False)


class _DebugCfg:
    def __init__(self) -> None:
        self._d = {"voice_edit": {"beep": False, "debug_log": True,
                                  "wake": {"enabled": True}}}

    def get(self, *parts, default=None):
        node = self._d
        for seg in parts:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def _stages(monkeypatch):
    from voicebridge.voice_edit import wake as wake_mod
    stages = []

    def capture(stage, **kw):
        stages.append((stage, kw))

    monkeypatch.setattr(wake_mod, "_trace", capture)
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "")
    return stages


def test_discarded_transcript_never_persists_words(monkeypatch):
    from voicebridge.voice_edit.wake import WakeMonitor

    stages = _stages(monkeypatch)
    m = WakeMonitor(_DebugCfg(), on_command=lambda c: None,
                    is_busy=lambda: False, transcribe=lambda pcm: "",
                    pending_probe=lambda: False)
    m._handle_transcript("some random room conversation")
    discarded = [kw for stage, kw in stages if stage == "discarded"]
    assert discarded, stages
    assert "text" not in discarded[-1]


def test_media_drop_never_persists_words(monkeypatch):
    from voicebridge.voice_edit.wake import WakeMonitor

    media = ("I'm Qubit. Thank you for watching this video, please remember "
             "to like and subscribe and ring the bell for more content")
    stages = _stages(monkeypatch)
    m = WakeMonitor(_DebugCfg(), on_command=lambda c: None,
                    is_busy=lambda: False, transcribe=lambda pcm: "",
                    pending_probe=lambda: False)
    m._handle_transcript(media, forced=True)
    dropped = [kw for stage, kw in stages if stage == "media_drop"]
    assert dropped, stages
    assert "text" not in dropped[-1]
