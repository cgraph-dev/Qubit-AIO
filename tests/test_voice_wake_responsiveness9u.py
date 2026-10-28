"""Wake responsiveness: adaptive trailing silence (double-wake + slow speech).

Hermetic: no mic, X, socket, model or service. The neural wrapper
``_AudioVadSegmenter`` is driven with synthetic 300 ms frames over a real
``EnergyEndpointer`` base (RMS only, no model), so the exact segmentation
state machine the live Silero path uses is exercised end to end.

Journal evidence (2026-10-06, ``~/.local/state/qubit-voicebridge/events.jsonl``):

* ``1791274185.99`` transcript ``'Hey Qubit.'`` (10 chars) then
  ``1791274188.90`` ``"What's my name?"`` as a SEPARATE
  ``voice_edit.wake.segment`` row — the wake phrase and its command were
  split by the tight trailing-silence gate (live ``wake.silence_ms`` = 500).
* The slow-dictation turn split into THREE segments:
  ``1791274275.99`` ``'Hey Qubit.'`` / ``1791274280.53`` ``'Type this for
  me.'`` / ``1791274284.16`` ``'1 plus 1 equals 99.'``; the first
  post-wake segment was emitted as a bare command, then the dictation was
  ``discarded`` unarmed — the dictation was never recorded.
* ``bare_wake_drop`` rows of 6 chars (``'Qubit.'``) show the wake phrase
  arriving split so ASR heard only the bare name, which the §75 tightening
  silently drops — the owner then repeated "hey qubit" (the double-wake).

Fix under test: once real speech is confirmed, ``wake.hold_silence_ms``
(default 1800 ms) becomes the trailing-silence bound for the rest of the
segment, so a mid-utterance pause no longer ends the turn. Drone never
reaches the VAD's speech state, so it cannot open a segment; ``wake.max_s``
still caps every segment.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import wake as wake_mod
from voicebridge.voice_edit.wake import (
    EnergyEndpointer,
    _AudioVadSegmenter,
    _build_audio_segmenter,
    _wake_hold_silence_ms,
)


class FakeCfg:
    def __init__(self, wake=None):
        self._d = {"voice_edit": {"beep": False,
                                   "wake": wake or {"enabled": True}}}

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def voiced(ms=300, amp=8000):
    n = int(16000 * ms / 1000)
    return struct.pack("<%dh" % n, *([amp] * n))


def silent(ms=300):
    n = int(16000 * ms / 1000)
    return b"\x00\x00" * n


def low(ms=300, amp=100):
    n = int(16000 * ms / 1000)
    return struct.pack("<%dh" % n, *([amp] * n))


def _seg(*, base_silence_ms=500, hold_ms=1800, rms_gate=0.006):
    """A neural wrapper over a real 30 ms-frame energy endpointer."""
    base = EnergyEndpointer(frame_ms=30, speech_ms=300,
                            silence_ms=base_silence_ms, max_s=15.0,
                            rms_gate=rms_gate)
    return _AudioVadSegmenter(base, backend="test", hold_silence_ms=hold_ms)


# ---------------------------------------------------------------------------
# 1. one wake burst completes a segment (no double-wake needed)
# ---------------------------------------------------------------------------
def test_wake_phrase_and_command_stay_one_segment_across_a_breath():
    """A natural pause after "hey qubit" must not end the segment.

    RED before the fix: the 500 ms base threshold returned "done" during
    the 600 ms pause, producing the two-segment split the journal shows.
    """
    seg = _seg()
    states = [seg.add_frame(voiced()) for _ in range(3)]      # "hey qubit"
    pause = [seg.add_frame(silent()) for _ in range(2)]       # 600 ms breath
    states += pause
    assert "done" not in pause, "the wake phrase must not end at the breath"
    command = [seg.add_frame(voiced()) for _ in range(3)]     # the command
    states += command
    assert command[-1] == "speech"
    # A real end-of-turn (>= the 1800 ms hold) closes the ONE segment once.
    tail = [seg.add_frame(silent()) for _ in range(7)]
    states += tail
    assert tail[-1] == "done"
    # The ONE segment ran from the wake through the command and only ended
    # in the final silence — never during the breath or the command.
    assert "done" not in pause
    assert "done" not in command
    assert "done" in tail


# ---------------------------------------------------------------------------
# 2. slow speech: a 700 ms mid-pause stays one segment; a real end closes
# ---------------------------------------------------------------------------
def test_slow_speech_700ms_mid_pause_stays_one_segment():
    seg = _seg()
    assert seg.add_frame(voiced()) == "speech"                # wake/opener
    # 700 ms mid-utterance pause ("type this for me:" ... dictation).
    mid = [seg.add_frame(silent(ms=300)), seg.add_frame(silent(ms=300)),
           seg.add_frame(silent(ms=100))]
    assert "done" not in mid, "a 700 ms mid-pause must not end the turn"
    assert seg.add_frame(voiced()) == "speech"                # dictation resumes
    tail = [seg.add_frame(silent()) for _ in range(7)]        # 2100 ms real end
    assert tail[-1] == "done"
    assert tail.count("done") == 1


def test_real_end_of_turn_still_closes_after_hold():
    seg = _seg()
    seg.add_frame(voiced())
    states = [seg.add_frame(silent()) for _ in range(3)]      # 900 ms < hold
    assert "done" not in states
    states = [seg.add_frame(silent()) for _ in range(3)]      # past the hold
    assert states[-1] == "done"


def test_no_hold_keeps_legacy_single_threshold():
    """``hold_silence_ms=None`` is byte-identical to the old behavior."""
    seg = _seg(base_silence_ms=900, hold_ms=None)
    assert seg.add_frame(voiced()) == "speech"
    assert seg.add_frame(silent()) == "speech"
    assert seg.add_frame(silent()) == "speech"
    assert seg.add_frame(silent()) == "done"


# ---------------------------------------------------------------------------
# 3. drone / room noise must not create a segment (VAD/energy guard intact)
# ---------------------------------------------------------------------------
def test_quiet_drone_never_opens_a_segment():
    seg = _seg(rms_gate=0.006)
    for _ in range(40):                                       # ~12 s of drone
        assert seg.add_frame(low(amp=100)) == "idle"


def test_hold_does_not_activate_on_noise():
    """The hold is armed only by real speech, so it cannot extend drone."""
    seg = _seg(rms_gate=0.006)
    for _ in range(40):
        seg.add_frame(low(amp=100))
    assert seg._holding is False
    assert seg._hold_frames is not None


def test_capped_room_noise_still_bounded_by_max_s():
    """Continuous VOICE-level tone is capped at max_s, never endless."""
    seg = _seg(base_silence_ms=500, hold_ms=1800, rms_gate=0.006)
    done_at = None
    for i in range(60):
        if seg.add_frame(voiced()) == "done":
            done_at = i + 1
            break
    assert done_at is not None and done_at <= 51               # 15 s / 300 ms


# ---------------------------------------------------------------------------
# 4. config plumbing + clamps, and never-raises
# ---------------------------------------------------------------------------
def test_hold_silence_default_and_clamps():
    assert _wake_hold_silence_ms(FakeCfg()) == 1800.0
    assert _wake_hold_silence_ms(FakeCfg(wake={"hold_silence_ms": 99})) == 300.0
    assert _wake_hold_silence_ms(
        FakeCfg(wake={"hold_silence_ms": 99999})) == 6000.0
    assert _wake_hold_silence_ms(
        FakeCfg(wake={"hold_silence_ms": 1200})) == 1200.0


def test_hold_silence_round_trips_through_settings_view_and_put(tmp_path):
    from voicebridge.voice_edit import settings as settings_mod

    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")

    class Cfg:
        def __init__(self):
            self.data = {"voice_edit": {"enabled": True}}
            self.path = p

        def get(self, *path, default=None):
            node = self.data
            for seg in path:
                if isinstance(node, dict) and seg in node:
                    node = node[seg]
                else:
                    return default
            return node

    cfg = Cfg()
    assert settings_mod.settings_view(cfg)["wake"]["hold_silence_ms"] == 1800.0
    settings_mod.update_voice_edit(cfg, {"wake": {"hold_silence_ms": 9999}})
    assert settings_mod.settings_view(cfg)["wake"]["hold_silence_ms"] == 6000.0
    settings_mod.update_voice_edit(cfg, {"wake": {"hold_silence_ms": 50}})
    assert settings_mod.settings_view(cfg)["wake"]["hold_silence_ms"] == 300.0
    assert settings_mod.settings_view(cfg)["wake"]["cue_on_kws"] is False
    settings_mod.update_voice_edit(cfg, {"wake": {"cue_on_kws": True}})
    assert settings_mod.settings_view(cfg)["wake"]["cue_on_kws"] is True
    assert settings_mod.settings_view(cfg)["wake"]["cue_on_speech"] is False
    settings_mod.update_voice_edit(cfg, {"wake": {"cue_on_speech": True}})
    assert settings_mod.settings_view(cfg)["wake"]["cue_on_speech"] is True


def test_build_audio_segmenter_wires_hold_from_config(monkeypatch):
    import voicebridge.audio.vad as avad

    captured = {}

    class FakeEP:
        def __init__(self, **kw):
            captured.update(kw)
            self.state = "idle"
            self.silence_timeout_s = kw.get("silence_timeout_s", 0.5)
            self._silence_frames = int(self.silence_timeout_s * 1000 / 30)

        def reset(self, *a, **k):
            self.state = "idle"

        def add_frame(self, pcm):
            return self.state

    monkeypatch.setattr(avad, "Endpointer", FakeEP)
    seg = _build_audio_segmenter(
        FakeCfg(wake={"enabled": True, "silence_ms": 500,
                      "hold_silence_ms": 2100}))
    assert isinstance(seg, _AudioVadSegmenter)
    assert seg._hold_frames == int(round(2100 / 30))
    assert captured.get("silence_timeout_s") == 0.5


def test_segmenter_and_helper_never_raise_on_junk():
    seg = _seg()
    for bad in (None, b"", b"x", 123, object(), "not-bytes"):
        seg.add_frame(bad)                                    # must not raise
    seg.reset()                                               # must not raise

    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("cfg down")

    assert _wake_hold_silence_ms(Broken()) == 1800.0


def test_reset_restores_base_threshold_for_next_segment():
    """A previous segment's hold must never leak into the next one."""
    seg = _seg(base_silence_ms=500, hold_ms=1800)
    seg.add_frame(voiced())
    assert seg._holding is True
    seg.reset()
    assert seg._holding is False
    assert getattr(seg._ep, "_silence_frames") == seg._base_silence_frames
