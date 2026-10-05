"""Orb feed truth: listening fires on wake, thinking clears when idle.

Live 2026-10-04 (pixel-read proof: orb showed yellow-green while the feed
had nothing in flight):
1. ``active_requests`` counted begun-but-TTL-expired sessions (status()
   never pruned) — 9 stale with nothing running, so the orb read
   permanent "thinking".
2. The wake path emits ``voice_edit.wake.command`` but the orb only
   matched the bare ``wake.command`` (never in the journal) — listening
   yellow never showed on hands-free turns.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from test_voice_edit9u import FakeDesktop, enabled_cfg  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402
from voicebridge.orb import state as orb_state  # noqa: E402


def test_status_counts_inflight_turns_not_begun_sessions():
    # Begun sessions are staging, not turns: 3 begins, nothing running.
    svc = VoiceEditService(enabled_cfg(), desktop=FakeDesktop(),
                           audit=lambda *a: None,
                           estop=lambda cfg: False)
    rids = [svc.begin()["request_id"] for _ in range(3)]
    assert svc.status()["active_requests"] == 0, svc.status()
    # ... and aged-out sessions stay 0 (the prune hygiene holds too).
    for rid in rids:
        svc._reqs[rid].created_at -= 400.0
    assert svc.status()["active_requests"] == 0, svc.status()


def test_inflight_counter_covers_exactly_the_turn():
    import threading

    svc = VoiceEditService(enabled_cfg(), desktop=FakeDesktop(),
                           audit=lambda *a: None,
                           estop=lambda cfg: False)
    rid = svc.begin()["request_id"]
    gate = threading.Event()
    release = threading.Event()

    def _slow_transform(request_id, transcript):
        gate.set()
        assert release.wait(timeout=10)
        return {"ok": True, "mode": "dictate", "transcript": "x",
                "request_id": request_id}

    svc.transform = _slow_transform  # type: ignore[method-assign]
    th = threading.Thread(target=svc.route, args=(rid, "type this for me x"))
    th.start()
    assert gate.wait(timeout=10)
    assert svc.status()["active_requests"] >= 1, svc.status()
    release.set()
    th.join(timeout=10)
    assert svc.status()["active_requests"] == 0, svc.status()


def test_wake_capturing_resolves_listening_not_command_accept():
    # Capture signals light listening; the command-accept row does NOT (it
    # would mask the thinking color for the turn — live 2026-10-05).
    now = time.time()
    cap = [{"ts": now - 1.0, "event": "voice_edit.wake.capturing"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       cap, now) == "listening"
    cmd = [{"ts": now - 1.0, "event": "voice_edit.wake.command"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       cmd, now) == "idle"
    hcmd = [{"ts": now - 1.0, "event": "voice_edit.helper.wake.command"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       hcmd, now) == "idle"


def test_quiet_feed_resolves_idle_not_thinking():
    now = time.time()
    evs = [{"ts": now - 60.0, "event": "voice_edit.wake.command"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       evs, now) == "idle"


def test_capturing_segment_resolves_listening():
    # The segment-open edge is the only signal that fires WHILE the owner
    # talks (command rows come after the fact).
    now = time.time()
    evs = [{"ts": now - 1.0, "event": "voice_edit.wake.capturing"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       evs, now) == "listening"


def test_speaking_outranks_thinking():
    # Owner contract: GREEN = TALKING whenever talking — a fresh
    # stt.final must not mask a playing reply yellow.
    now = time.time()
    evs = [{"ts": now - 1.0, "event": "stt.final"},
           {"ts": now - 1.0, "event": "voice_edit.helper.speak"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       evs, now) == "speaking"


def test_tts_synthesize_rows_reach_the_orb_and_resolve_speaking():
    # The log filter dropped every tts.* row, so dashboard-rendered speech
    # never lit the orb although its speaking set names tts.synthesize.
    from voicebridge.voice_edit import logview
    assert logview._is_voice_event("tts.synthesize") is True
    slim = logview._slim({"ts": 1.0, "event": "tts.synthesize",
                          "provider": "piper", "chars": 21,
                          "path": "/secret/x.wav"})
    assert slim["event"] == "tts.synthesize" and slim["ts"] == 1.0
    assert "path" not in slim  # on-disk paths never leave the host
    assert slim["chars"] == 21
    now = time.time()
    evs = [{"ts": now - 1.0, "event": "tts.synthesize"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       evs, now) == "speaking"
    stale = [{"ts": now - 30.0, "event": "tts.synthesize"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       stale, now) == "idle"


def test_right_ctrl_listening_event_lights_the_orb():
    # The helper emits `voice_edit.helper.listening` on the Right-Ctrl
    # key-down capture edge; the orb never watched it, so holding Right-Ctrl
    # left the orb orange (live 2026-10-05).
    now = time.time()
    evs = [{"ts": now - 1.0, "event": "voice_edit.helper.listening"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       evs, now) == "listening"


def test_speaking_ends_when_done_marker_is_newer():
    now = time.time()
    speak_only = [{"ts": now - 1.0, "event": "voice_edit.helper.speak"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       speak_only, now) == "speaking"
    # A newer done marker ends green immediately (not after the window).
    ended = [{"ts": now - 3.0, "event": "voice_edit.helper.speak"},
             {"ts": now - 1.0, "event": "voice_edit.helper.speak_done"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       ended, now) == "idle"


def test_speaking_outranks_a_done_older_than_it():
    now = time.time()
    evs = [{"ts": now - 3.0, "event": "voice_edit.helper.speak_done"},
           {"ts": now - 1.0, "event": "voice_edit.helper.speak"}]
    assert orb_state.resolve_orb_state({"active_requests": 0},
                                       evs, now) == "speaking"
