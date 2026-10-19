"""Hermetic pins for the live orb STOP harness (``scripts/verify_orb_stop_live.py``).

The live part (a real player subprocess + SIGUSR2 + journal) is best-effort on
a machine with working audio; what a refactor could silently break is the
parsing/verdict logic. These tests pin:

  * ``voice_edit.talk.*`` rows are recognized and everything else is dropped;
  * ``summarize`` requires the canonical ``interrupt`` -> ``playback.stopped``
    -> ``response.done`` order AND a player that ended, and degrades cleanly
    when no player could start;
  * the wav padding keeps a short synth'd clip alive long enough for a stop;
  * ``run`` writes ``evidence.json`` + ``timeline.txt`` and returns DEGRADED
    (no traceback) when no player is installed.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import wave
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_orb_stop_live.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "verify_orb_stop_live_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    assert SCRIPT_PATH.is_file(), f"harness missing: {SCRIPT_PATH}"
    return _load_harness()


def _event(etype, ts, seq, turn="turn-1"):
    return {"ts": ts, "event": f"voice_edit.talk.{etype}", "type": etype,
            "turn_id": turn, "seq": seq, "pid": 1}


def test_classify_keeps_talk_rows_only(harness):
    ev = harness.classify_talk_row({
        "ts": 5.5, "event": "voice_edit.talk.interrupt",
        "turn_id": "turn-2", "seq": 9, "pid": 77})
    assert ev == {"ts": 5.5, "event": "voice_edit.talk.interrupt",
                  "type": "interrupt", "turn_id": "turn-2", "seq": 9,
                  "pid": 77}
    assert harness.classify_talk_row(
        {"event": "voice_edit.cancel", "ts": 1.0}) is None
    assert harness.classify_talk_row({"event": "tts.synthesize"}) is None
    assert harness.classify_talk_row("nope") is None
    # Non-numeric ts is normalized, not dropped.
    ev = harness.classify_talk_row({"event": "voice_edit.talk.error",
                                    "ts": "x"})
    assert ev is not None and ev["ts"] is None


def test_summarize_pass_requires_ordered_triple_and_player_exit(harness):
    events = [
        _event("playback.started", 100.0, 1),
        _event("interrupt", 100.5, 2),
        _event("playback.stopped", 100.6, 3),
        _event("response.done", 100.7, 4),
    ]
    out = harness.summarize(events, t_stop=100.4, player_started=True,
                            player_exited=True)
    assert out["verdict"] == "PASS", out
    assert out["triple_present"] is True and out["triple_ordered"] is True
    assert out["stop_latency_s"] == pytest.approx(0.1, abs=0.001)


def test_summarize_missing_or_out_of_order_triple_fails(harness):
    missing = [_event("interrupt", 1.0, 1), _event("response.done", 1.2, 2)]
    out = harness.summarize(missing, player_started=True, player_exited=True)
    assert out["verdict"] == "FAIL" and "playback.stopped" in out["missing"]

    out_of_order = [
        _event("playback.stopped", 1.0, 1),
        _event("interrupt", 1.1, 2),
        _event("response.done", 1.2, 3),
    ]
    out = harness.summarize(out_of_order, player_started=True,
                            player_exited=True)
    assert out["verdict"] == "FAIL" and out["triple_present"] is True


def test_summarize_degrades_without_a_player(harness):
    out = harness.summarize([], player_started=False, player_exited=None,
                            degraded=["no paplay/aplay installed"])
    assert out["verdict"] == "DEGRADED"
    assert "no paplay/aplay" in " ".join(out["degraded"])


def test_summarize_fails_when_player_survives_the_stop(harness):
    events = [
        _event("interrupt", 1.0, 1),
        _event("playback.stopped", 1.1, 2),
        _event("response.done", 1.2, 3),
    ]
    out = harness.summarize(events, player_started=True, player_exited=False)
    assert out["verdict"] == "FAIL"
    assert "still alive" in out["reason"]


def test_build_timeline_orders_by_ts_and_marks_relative(harness):
    events = [_event("response.done", 2.0, 3), _event("interrupt", 1.0, 2)]
    text = harness.build_timeline(events, 0.5, header=["hdr"])
    lines = text.splitlines()
    assert lines[0] == "hdr"
    assert "interrupt" in lines[1] and "response.done" in lines[2]


def test_pad_wav_keeps_a_short_clip_playing(harness):
    wav = harness.make_tone_wav(0.2)
    padded = harness.pad_wav_min_duration(wav, 2.0)
    with wave.open(io.BytesIO(padded), "rb") as wf:
        duration = wf.getnframes() / float(wf.getframerate())
    assert duration >= 1.9
    # Already-long audio is returned untouched.
    assert harness.pad_wav_min_duration(padded, 1.0) == padded


def test_run_degrades_cleanly_without_a_player(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "_playback_tools", lambda: [])
    out = harness.run(out_dir=tmp_path, duration=0.5)
    assert out["verdict"] == "DEGRADED", out
    cap = Path(out["capture_dir"])
    assert (cap / "evidence.json").is_file()
    assert (cap / "timeline.txt").is_file()
    assert json.loads((cap / "evidence.json").read_text())["verdict"] == "DEGRADED"
