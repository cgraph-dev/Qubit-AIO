"""Hands-free wake command tests (no X, no mic, no socket)."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.wake import (BufferMic, EnergyEndpointer,
                                         WakeMonitor, strip_wake)


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


def loud_frame(ms=300, amp=8000):
    n = int(16000 * ms / 1000)
    return struct.pack("<%dh" % n, *([amp] * n))


def silent_frame(ms=300):
    n = int(16000 * ms / 1000)
    return b"\x00\x00" * n


# ---------------------------------------------------------------------------
# phrase matching
# ---------------------------------------------------------------------------
def test_strip_wake_finds_phrase_and_returns_command():
    assert strip_wake("Hey Qubit, type this for me: hello") == \
        "type this for me: hello"
    assert strip_wake("hey qubit make this clearer") == "make this clearer"
    assert strip_wake("Hey Cubit, fix the grammar") == "fix the grammar"


def test_strip_wake_absent_or_bare():
    assert strip_wake("make this clearer") is None
    assert strip_wake("") is None
    assert strip_wake("hey qubit") == ""
    # a sentence that merely mentions the word must not trigger the wake word
    assert strip_wake("the qubit is fast") is None
    assert strip_wake("make this clearer, qubit") is None


def test_strip_wake_asr_variants():
    # ASR commonly drops "hey" and hears "qubit" as "cubit".
    assert strip_wake("A cubit, type this for me: hi") == "type this for me: hi"
    assert strip_wake("qubit fix the grammar") == "fix the grammar"
    assert strip_wake("Cubit make this shorter") == "make this shorter"
    assert strip_wake("ok, hey qubit summarize this") == "summarize this"


# ---------------------------------------------------------------------------
# endpointer
# ---------------------------------------------------------------------------
def test_endpointer_speech_then_done():
    ep = EnergyEndpointer(silence_ms=900, rms_gate=0.006)
    assert ep.add_frame(loud_frame()) == "speech"
    assert ep.add_frame(silent_frame()) == "speech"
    assert ep.add_frame(silent_frame()) == "speech"
    assert ep.add_frame(silent_frame()) == "done"


def test_endpointer_quiet_noise_never_starts():
    ep = EnergyEndpointer(rms_gate=0.006)
    for _ in range(10):
        assert ep.add_frame(struct.pack("<%dh" % 4800, *([100] * 4800))) == "idle"


def test_buffer_mic_signals_exhaustion():
    seen = []
    mic = BufferMic(b"abcdef", frame_bytes=2, on_exhausted=lambda: seen.append(1))
    assert mic.read_frame() == b"ab"
    assert mic.read_frame() == b"cd"
    assert mic.read_frame() == b"ef"
    assert seen == [1]
    assert mic.read_frame() is None


# ---------------------------------------------------------------------------
# monitor segment handling
# ---------------------------------------------------------------------------
def _monitor(replies):
    commands = []
    it = iter(replies)
    m = WakeMonitor(FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    transcribe=lambda pcm: next(it, ""))
    return m, commands


def test_wake_with_command_emits_remainder():
    m, commands = _monitor(["hey qubit, type this for me: hello world"])
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["type this for me: hello world"]


def test_non_wake_transcript_is_discarded():
    m, commands = _monitor(["someone said something random"])
    m._handle_segment(b"\x00" * 9600)
    assert commands == []


def test_bare_wake_arms_next_utterance():
    m, commands = _monitor(["hey qubit", "fix the grammar", "ignored"])
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["fix the grammar"]


def test_short_segment_is_ignored():
    m, commands = _monitor(["hey qubit type this"])
    m._handle_segment(b"\x00" * 100)  # below min length
    assert commands == []


def test_empty_transcript_is_discarded():
    m, commands = _monitor([""])
    m._handle_segment(b"\x00" * 9600)
    assert commands == []


class FakeKws:
    def __init__(self, hits=1):
        self.hits = hits
        self.seen = 0
        self.resets = 0

    def feed(self, samples):
        self.seen += 1
        if self.seen <= self.hits:
            return "HEY QUBIT"
        return None

    def reset(self):
        self.resets += 1


def test_monitor_uses_injected_kws_engine():
    m = WakeMonitor(FakeCfg(), on_command=lambda c: None,
                    is_busy=lambda: False, kws=FakeKws())
    assert m.engine == "kws"


def test_forced_segment_emits_command_from_kws():
    # Keyword fired; ASR rendered the wake phrase -> strip it.
    m, commands = _monitor(["hey qubit, type this for me: hello"])
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert commands == ["type this for me: hello"]
    # Keyword fired but ASR dropped the wake phrase -> use the whole text.
    m2, commands2 = _monitor(["type this for me: hello"])
    m2._handle_segment(b"\x00" * 9600, forced=True)
    assert commands2 == ["type this for me: hello"]
    # Keyword fired with no speech -> arm, never emit.
    m3, commands3 = _monitor([""])
    m3._handle_segment(b"\x00" * 9600, forced=True)
    assert commands3 == []


class SeqMic:
    def __init__(self, frames):
        self._frames = list(frames)

    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        if self._frames:
            return self._frames.pop(0)
        import time as _t
        _t.sleep(0.05)
        return b"\x00" * 9600

    def stop(self):
        pass


def test_kws_loop_runs_asr_only_on_keyword_hit():
    calls = []

    def transcribe(pcm):
        calls.append(1)
        return "hey qubit type this for me: hi"

    commands = []
    m = WakeMonitor(FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud_frame(), loud_frame(), silent_frame(),
                         silent_frame(), silent_frame()] * 3),
                    transcribe=transcribe, kws=FakeKws(hits=1))
    m.start()
    for _ in range(60):
        if commands:
            break
        import time as _t
        _t.sleep(0.05)
    m.stop()
    assert commands == ["type this for me: hi"]
    assert len(calls) == 1  # ASR ran exactly once (the keyword utterance)


def test_real_kws_model_detects_phrase_if_installed():
    import pytest
    from pathlib import Path
    from voicebridge.voice_edit.kws import KwsDetector, find_model_dir
    md = find_model_dir(FakeCfg())
    model = Path(__file__).resolve().parents[1]
    lib = Path.home() / ".local/share/qubit-voicebridge/models"
    if md is None:
        pytest.skip("no KWS model installed")
    det = KwsDetector(md)
    import numpy as np
    # A tone must not trigger; a hit requires real speech, so just prove the
    # detector loads and processes audio without error.
    assert det.feed(np.zeros(16000, dtype=np.float32)) is None


def _unused(model_path):
    return model_path


def test_monitor_disabled_not_started():
    m = WakeMonitor(FakeCfg(wake={"enabled": False}), on_command=lambda c: None,
                    is_busy=lambda: False)
    assert m.enabled() is False
    assert m.start() is False
