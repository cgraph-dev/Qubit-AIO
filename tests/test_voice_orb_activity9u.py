"""Output-activity tracker + its orb wiring (OpenClaw port, 2026-10-06).

Hermetic: injectable clock, no display, no network. ``resolve_orb_state``
gains an optional ``activity`` signal so ``speaking`` stays exact while
reply audio flows even when the heartbeat TTS timeline is momentarily
empty (a missed/unseen frame), and ends at the tracker's tail; ``None``
keeps the legacy behavior byte-identical.
"""
from __future__ import annotations

import time
import types

import pytest

from voicebridge.orb import activity as orb_activity
from voicebridge.orb import state as orb_state


class _Clock:
    """Tiny injectable monotonic-ish clock for deterministic tails."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


def _tracker(t: float = 1000.0):
    clk = _Clock(t)
    return orb_activity.OutputActivityTracker(now=clk), clk


# -- tracker counters / playback transitions --------------------------------

def test_mark_audio_accumulates_and_counts_chunks():
    tr, clk = _tracker()
    tr.mark_stream_opened()
    tr.mark_playback_started()
    tr.mark_audio({"audioMs": 100, "sourceAudioBytes": 10,
                   "sinkAudioBytes": 5})
    clk.advance(0.05)
    # Provider-buggy negatives clamp to zero but the chunk still counts.
    tr.mark_audio({"audioMs": -50, "sourceAudioBytes": -1,
                   "sinkAudioBytes": 2})
    snap = tr.snapshot()
    assert snap["chunks"] == 2
    assert snap["audioMs"] == 100
    assert snap["sourceAudioBytes"] == 10
    assert snap["sinkAudioBytes"] == 7
    assert snap["playbackStarted"] is True
    assert snap["streamEnding"] is False
    assert snap["lastAudioAt"] == clk.t
    assert snap["playbackStartedAt"] == 1000.0


def test_mark_playback_started_is_idempotent():
    tr, clk = _tracker()
    tr.mark_playback_started()
    clk.advance(1.0)
    tr.mark_playback_started()
    assert tr.snapshot()["playbackStartedAt"] == 1000.0


def test_stream_ending_and_reopen_keep_counters_reset_markers():
    tr, clk = _tracker()
    tr.mark_playback_started()
    tr.mark_audio({"audioMs": 50})
    tr.mark_stream_ending()
    assert tr.snapshot()["streamEnding"] is True
    # A new stream clears playback markers but keeps cumulative counters.
    tr.mark_stream_opened()
    snap = tr.snapshot()
    assert snap["streamEnding"] is False
    assert snap["playbackStarted"] is False
    assert "playbackStartedAt" not in snap
    assert "lastAudioAt" not in snap
    assert snap["audioMs"] == 50
    assert snap["chunks"] == 1


# -- is_active: flowing / tail / past tail ----------------------------------

def test_is_active_window_and_tail():
    tr, clk = _tracker()
    assert tr.is_active(clk.t) is False          # no audio observed yet
    tr.mark_audio({"audioMs": 10})
    assert tr.is_active(clk.t) is True            # audio flowing now
    clk.advance(0.10)
    assert tr.is_active(clk.t, tail_s=0.12) is True   # within the tail
    clk.advance(0.05)
    assert tr.is_active(clk.t, tail_s=0.12) is False  # past the tail
    # A fresh frame re-arms it.
    tr.mark_audio({"audioMs": 10})
    assert tr.is_active(clk.t) is True


def test_is_active_future_stamp_is_live():
    tr, clk = _tracker(1000.0)
    tr.mark_audio({"audioMs": 1})
    assert tr.is_active(999.0) is True  # clock skew never drops live audio


def test_reset_clears_everything():
    tr, clk = _tracker()
    tr.mark_playback_started()
    tr.mark_audio({"audioMs": 10})
    tr.mark_stream_ending()
    tr.reset()
    assert tr.snapshot() == {
        "audioMs": 0.0, "chunks": 0, "sourceAudioBytes": 0.0,
        "sinkAudioBytes": 0.0, "playbackStarted": False,
        "streamEnding": False}
    assert tr.is_active(clk.t) is False


# -- watchdog (upstream semantics port) -------------------------------------

def test_watchdog_and_elapsed_playback():
    tr, clk = _tracker()
    assert tr.playback_watchdog_delay_s(clk.t) is None     # not started
    tr.mark_playback_started()
    assert tr.playback_watchdog_delay_s(clk.t) is None     # no audio yet
    tr.mark_audio({"audioMs": 2000})
    clk.advance(0.5)
    assert tr.elapsed_playback_s(clk.t) == pytest.approx(0.5)
    # remaining 1.5 s of audio + 0.25 margin, floor of 1.0 s.
    assert tr.playback_watchdog_delay_s(clk.t, 0.25) == pytest.approx(1.75)


# -- resolve_orb_state activity seam ----------------------------------------

def test_activity_extends_speaking_without_heartbeat_marker():
    tr = orb_activity.OutputActivityTracker()
    tr.mark_audio({"audioMs": 100})
    # No speak event, no spec_tts heartbeat timeline: tracker alone -> green.
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [], time.time(), activity=tr) == "speaking"


def test_inactive_activity_leaves_resolution_unchanged():
    now = time.time()
    stale = orb_activity.OutputActivityTracker(now=lambda: now - 10.0)
    stale.mark_audio({"audioMs": 100})
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [], now, activity=stale) == "idle"
    # A fresh speak event still resolves green via the legacy rule.
    evs = [{"ts": now - 1.0, "event": "tts.speak"}]
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, evs, now, activity=stale) == "speaking"


def test_activity_accepts_plain_bool_signal():
    now = time.time()
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [], now, activity=True) == "speaking"
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [], now, activity=False) == "idle"


def test_activity_none_is_backward_compatible():
    now = time.time()
    evs = [{"ts": now - 2.0, "event": "tts.speak"}]
    assert orb_state.resolve_orb_state({}, evs, now) == \
        orb_state.resolve_orb_state({}, evs, now, activity=None)
    # And a stale event still falls through to idle with no activity.
    assert orb_state.resolve_orb_state(
        {}, [{"ts": now - 60.0, "event": "tts.speak"}], now) == "idle"


# -- never raises on junk ----------------------------------------------------

def test_tracker_never_raises_on_junk():
    tr = orb_activity.OutputActivityTracker()
    for junk in (None, "x", 123, object(), [], {"audioMs": "no"},
                 {"sourceAudioBytes": float("nan")}, float("inf")):
        assert tr.mark_audio(junk) is None
    assert tr.snapshot()["chunks"] == 8  # every call records a chunk
    for junk in (None, "x", object(), float("nan"), float("inf")):
        assert tr.is_active(junk) in (True, False)
    # A broken clock degrades to "no timestamps", never an exception.
    bad = orb_activity.OutputActivityTracker(now=lambda: "soon")
    bad.mark_audio({"audioMs": 1})
    assert bad.snapshot().get("lastAudioAt") is None
    assert bad.is_active(1.0) is False


def test_resolve_never_raises_on_junk_activity():
    now = time.time()
    for junk in ("x", 5, object(), [1], {"is_active": 3}):
        out = orb_state.resolve_orb_state({}, [], now, activity=junk)
        assert out in orb_state.ORB_STATES

    class _Raiser:
        def is_active(self, _now):
            raise RuntimeError("boom")

    assert orb_state.resolve_orb_state(
        {}, [], now, activity=_Raiser()) == "idle"


# -- Qt wiring (skips cleanly without Qt) ------------------------------------

def test_qt_wiring_marks_playback_and_stream_ending():
    qt_app = pytest.importorskip("voicebridge.orb.qt_app")
    if qt_app.QWidget is None:
        pytest.skip("Qt not importable here")
    tracker = orb_activity.OutputActivityTracker()
    fake = types.SimpleNamespace(_activity=tracker)
    now = time.time()
    speak = [{"ts": now - 0.5, "event": "voice_edit.helper.speak"}]
    qt_app.QtVoiceOrb._observe_output_activity(fake, None, speak, None)
    snap = tracker.snapshot()
    assert snap["playbackStarted"] is True
    assert snap["streamEnding"] is False
    done = [{"ts": now - 1.0, "event": "voice_edit.helper.speak"},
            {"ts": now - 0.2, "event": "voice_edit.helper.speak_done"}]
    qt_app.QtVoiceOrb._observe_output_activity(fake, None, done, None)
    assert tracker.snapshot()["streamEnding"] is True


def test_qt_wiring_marks_audio_on_tts_frame():
    qt_app = pytest.importorskip("voicebridge.orb.qt_app")
    if qt_app.QWidget is None:
        pytest.skip("Qt not importable here")
    tracker = orb_activity.OutputActivityTracker()
    fake = types.SimpleNamespace(_activity=tracker)
    start = time.time() - 0.05
    heartbeat = {"spec_tts": [[0.5] * 12, [0.5] * 12, [0.5] * 12],
                 "spec_tts_ts": start}
    qt_app.QtVoiceOrb._observe_output_activity(fake, None, [], heartbeat)
    snap = tracker.snapshot()
    assert snap["chunks"] == 1
    assert snap["lastAudioAt"] is not None
    assert tracker.is_active(time.time()) is True
