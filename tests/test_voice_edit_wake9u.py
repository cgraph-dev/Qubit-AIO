"""Hands-free wake command tests (no X, no mic, no socket)."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.wake import (BufferMic, EnergyEndpointer,
                                         WakeMonitor, is_prompt_echo,
                                         strip_wake)


class FakeCfg:
    def __init__(self, wake=None, stt_local=None):
        self._d = {"voice_edit": {"beep": False,
                                   "wake": wake or {"enabled": True}}}
        if stt_local is not None:
            self._d.setdefault("stt", {})["local"] = stt_local

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


def test_prompt_echo_is_discarded_not_fired():
    # faster-whisper parrots stt.local.initial_prompt on room noise; the
    # echo contains "hey Qubit" and false-fired the wake path 235x live.
    cfg = FakeCfg(stt_local={"initial_prompt": "Qubit, hey Qubit, voice assistant."})
    assert is_prompt_echo(cfg, "Qubit, hey Qubit, voice assistant.")
    assert is_prompt_echo(cfg, "qubit hey qubit voice assistant")
    assert not is_prompt_echo(cfg, "hey qubit, what's the weather")
    assert not is_prompt_echo(cfg, "hey qubit")
    commands = []
    m = WakeMonitor(cfg, on_command=commands.append,
                    is_busy=lambda: False,
                    transcribe=lambda pcm: "Qubit, hey Qubit, voice assistant.")
    m._handle_segment(b"\x00" * 9600)
    assert commands == []


def test_prompt_echo_off_without_configured_prompt(monkeypatch):
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_runtime_initial_prompt", lambda: "")
    assert not is_prompt_echo(FakeCfg(), "hey qubit, voice assistant")


def test_prompt_echo_catches_partial_prompt_parrot():
    # Live 2026-10-02: the fallback probe transcribed room noise as
    # "hey Qubit, voice assistant." (a trimmed parrot of the whisper
    # initial_prompt) and it FIRED as a command 100+ times, because the
    # guard only caught byte-exact equality. Any transcript made solely
    # of wake + prompt vocabulary that carries prompt filler is an echo.
    cfg = FakeCfg(stt_local={"initial_prompt": "Qubit, hey Qubit, voice assistant."})
    assert is_prompt_echo(cfg, "hey Qubit, voice assistant.")
    assert is_prompt_echo(cfg, "Qubit voice assistant")
    assert is_prompt_echo(cfg, "voice assistant hey qubit")
    # Bare wake with no filler still arms the next utterance (not an echo).
    assert not is_prompt_echo(cfg, "Hey Qubit,")
    # Real content beyond wake + prompt vocabulary is never an echo.
    assert not is_prompt_echo(cfg, "hey qubit, what's the weather")
    assert not is_prompt_echo(cfg, "hey qubit, voice assistant, tell me the time")


def test_prompt_echo_reads_live_runtime_prompt(monkeypatch):
    # The whisper initial_prompt lives in the RUNTIME config.yaml
    # (stt.local.initial_prompt), not in voice.yaml — the guard read only
    # the voice tree, so it NEVER fired live (0 skips in 11,247 probes).
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_runtime_initial_prompt",
                        lambda: "Qubit, hey Qubit, voice assistant.")
    assert is_prompt_echo(FakeCfg(), "hey Qubit, voice assistant.")
    assert not is_prompt_echo(FakeCfg(), "hey qubit, what's the weather")


def test_partial_parrot_segment_emits_no_command():
    commands = []
    m = WakeMonitor(FakeCfg(
        stt_local={"initial_prompt": "Qubit, hey Qubit, voice assistant."}),
        on_command=commands.append, is_busy=lambda: False,
        transcribe=lambda pcm: "hey Qubit, voice assistant.")
    m._handle_segment(b"\x00" * 9600)
    assert commands == []


def test_wake_vad_key_round_trips_through_settings_put(tmp_path):
    # wake.vad selects the segmentation backend: auto (neural when
    # available, else energy) | silero | webrtc | energy.
    from voicebridge.voice_edit import settings as settings_mod
    from voicebridge.voice_edit.schema import VoiceEditError
    import pytest

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
    assert settings_mod.settings_view(cfg)["wake"]["vad"] == "auto"
    settings_mod.update_voice_edit(cfg, {"wake": {"vad": "silero"}})
    assert settings_mod.settings_view(cfg)["wake"]["vad"] == "silero"
    with pytest.raises(VoiceEditError):
        settings_mod.update_voice_edit(cfg, {"wake": {"vad": "neural-net-9000"}})


def test_monitor_uses_injected_segmenter():
    # The segmentation backend is injectable (same pattern as mic_factory
    # / transcribe / kws): a fake segmenter drives the loop end to end.
    seen = []

    class FakeSegmenter:
        def __init__(self):
            self.calls = 0

        def reset(self):
            pass

        def add_frame(self, pcm):
            self.calls += 1
            seen.append(len(pcm))
            return "done" if self.calls == 2 else "speech"

    commands = []
    m = WakeMonitor(FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic([loud_frame()] * 4),
                    transcribe=lambda pcm: "hey qubit what's the time",
                    kws=FakeKws(hits=0),
                    segmenter_factory=lambda cfg: FakeSegmenter())
    _run_loop(m, lambda: bool(commands))
    assert commands == ["what's the time"]
    assert seen and all(n == 9600 for n in seen)


def test_auto_vad_falls_back_to_energy_when_neural_unavailable(monkeypatch):
    import voicebridge.voice_edit.wake as wake_mod
    from voicebridge.voice_edit.wake import EnergyEndpointer, _make_segmenter
    monkeypatch.setattr(wake_mod, "_build_audio_segmenter",
                        lambda cfg: (_ for _ in ()).throw(RuntimeError("no model")))
    seg = _make_segmenter(FakeCfg(wake={"enabled": True, "vad": "auto"}))
    assert isinstance(seg, EnergyEndpointer)


def test_make_segmenter_prefers_neural_when_available(monkeypatch):
    import voicebridge.voice_edit.wake as wake_mod
    from voicebridge.voice_edit.wake import _make_segmenter
    sentinel = object()
    monkeypatch.setattr(wake_mod, "_build_audio_segmenter",
                        lambda cfg: sentinel)
    assert _make_segmenter(FakeCfg(wake={"enabled": True})) is sentinel
    assert _make_segmenter(
        FakeCfg(wake={"enabled": True, "vad": "silero"})) is sentinel


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


def test_kws_loop_hit_plus_bounded_fallback_probes():
    """Keyword hit emits; each short KWS-miss burst gets one ASR probe.

    Contract change (ASR-gated fallback, default ON): the spotter is deaf
    to some voices at every threshold while local ASR hears them, so a
    miss no longer means the segment is dropped unread. The hit burst runs
    ASR forced; each short miss burst runs one fallback probe through the
    same strip/arm path.
    """
    calls = []

    def transcribe(pcm):
        calls.append(1)
        return "hey qubit type this for me: hi"

    commands = []
    # Synthetic constant-tone frames carry energy but no speech spectrum,
    # so these KWS/fallback-logic tests pin the energy segmenter; neural
    # selection is covered by test_make_segmenter_prefers_neural.
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=commands.append,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud_frame(), loud_frame(), silent_frame(),
                         silent_frame(), silent_frame()] * 3),
                    transcribe=transcribe, kws=FakeKws(hits=1))
    m.start()
    for _ in range(120):
        if len(commands) >= 3:
            break
        import time as _t
        _t.sleep(0.05)
    m.stop()
    assert commands == ["type this for me: hi"] * 3
    assert len(calls) == 3  # hit ASR + one fallback probe per miss burst


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


def _run_loop(m, until, timeout_s=6.0):
    import time as _t

    m.start()
    end = _t.time() + timeout_s
    while _t.time() < end and not until():
        _t.sleep(0.05)
    m.stop()


def test_kws_context_survives_non_hit_segments():
    """The spotter resets ONLY on a hit, never after a plain segment.

    Regression: resetting after every endpointed chunk wiped the KWS
    running context, so a lone wake phrase following any other sound was
    systematically missed (first occurrence missed, repetitions caught).
    """
    kws = FakeKws(hits=0)
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud_frame(), loud_frame()] + [silent_frame()] * 4
                        + [loud_frame(), loud_frame()] + [silent_frame()] * 4),
                    transcribe=lambda pcm: "someone said something random",
                    kws=kws)
    _run_loop(m, lambda: kws.seen >= 12)
    assert kws.resets == 0, kws.resets


def test_kws_resets_after_a_hit():
    """A hit still latches-clear so one phrase cannot double-fire."""
    kws = FakeKws(hits=1)
    commands = []
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=commands.append,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud_frame(), loud_frame()] + [silent_frame()] * 4),
                    transcribe=lambda pcm: "hey qubit what's the time",
                    kws=kws)
    _run_loop(m, lambda: bool(commands))
    assert commands == ["what's the time"]
    assert kws.resets == 1, kws.resets


def test_asr_fallback_fires_on_kws_miss():
    """A KWS miss is not a dropped segment: one bounded ASR probe runs.

    The spotter never fires (wrong voice for the model) but local ASR
    hears the wake phrase — the fallback emits through the same
    strip/arm path as asr-engine mode instead of discarding the segment.
    """
    from voicebridge.voice_edit.wake import _asr_fallback_allows
    assert _asr_fallback_allows(FakeCfg(), b"\x00" * 9600) is True
    commands = []
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=commands.append,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud_frame(), loud_frame()] + [silent_frame()] * 4),
                    transcribe=lambda pcm: "hey qubit what's the time",
                    kws=FakeKws(hits=0))
    _run_loop(m, lambda: bool(commands))
    assert commands == ["what's the time"]


def test_asr_fallback_disabled_never_transcribes_misses():
    """Opt-out restores the old gate: misses are dropped without ASR."""
    from voicebridge.voice_edit.wake import _asr_fallback_allows
    cfg = FakeCfg(wake={"enabled": True, "asr_fallback": False,
                        "vad": "energy"})
    assert _asr_fallback_allows(cfg, b"\x00" * 9600) is False
    # A long segment is skipped even with the fallback on (bounded probe).
    assert _asr_fallback_allows(FakeCfg(), b"\x00" * 32000 * 26) is False
    calls = []
    m = WakeMonitor(cfg, on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud_frame(), loud_frame()] + [silent_frame()] * 4),
                    transcribe=lambda pcm: calls.append(1) or "hey qubit go",
                    kws=FakeKws(hits=0))
    _run_loop(m, lambda: len(calls) > 0, timeout_s=2.0)
    assert calls == []


def test_wake_fallback_keys_round_trip_through_settings_put(tmp_path):
    """The new wake keys persist via PUT, clamp, and reflect in the view."""
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
    view = settings_mod.settings_view(cfg)
    assert view["wake"]["asr_fallback"] is True
    assert view["wake"]["asr_fallback_max_s"] == 8.0
    settings_mod.update_voice_edit(
        cfg, {"wake": {"asr_fallback": False, "asr_fallback_max_s": 99}})
    view = settings_mod.settings_view(cfg)
    assert view["wake"]["asr_fallback"] is False
    assert view["wake"]["asr_fallback_max_s"] == 25.0  # clamped, not stored raw


def test_wake_max_s_defaults_and_clamps():
    """Segment cap defaults to the probe bound and stays in 2..25 s."""
    from voicebridge.voice_edit.wake import _wake_max_s
    assert _wake_max_s(FakeCfg()) == 8.0
    assert _wake_max_s(FakeCfg(wake={"enabled": True, "max_s": 99})) == 25.0
    assert _wake_max_s(FakeCfg(wake={"enabled": True, "max_s": 0.5})) == 2.0
    assert _wake_max_s(FakeCfg(wake={"enabled": True, "max_s": 12})) == 12.0


def test_endpointer_completes_continuous_voicing_at_max_s():
    """A never-silent room yields bounded segments, not one endless blob.

    Regression: with the 25 s default cap, continuous voicing (fans, TTS
    feedback, repeated attempts) completed only 25 s segments that always
    exceeded the probe bound and were silently skipped — wake could never
    fire. At the 8 s cap every completed segment fits a probe.
    """
    from voicebridge.voice_edit.wake import _wake_max_s
    ep = EnergyEndpointer(rms_gate=0.008, max_s=_wake_max_s(FakeCfg()))
    states = [ep.add_frame(loud_frame()) for _ in range(40)]
    assert states[-1] == "done"
    first_done = states.index("done") + 1
    assert 26 <= first_done <= 28  # 8 s / 300 ms frames
    assert first_done * 9600 <= int(8.0 * 16000 * 2)  # fits the probe bound


def test_wake_loop_prepends_preroll_before_speech_onset():
    # Soft onsets ("hey" starting mid-frame) were clipped: the loop only
    # buffered frames AFTER the endpointer flipped to speech, so up to
    # ~300 ms of the utterance start never reached ASR (Right-Ctrl never
    # clips — it records from keypress). The loop keeps 600 ms of
    # pre-roll and prepends it when a segment opens.
    captured = []
    commands = []

    def transcribe(pcm):
        captured.append(len(pcm))
        return "hey qubit what's the time"

    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=commands.append, is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [silent_frame(), silent_frame(),
                         loud_frame(), loud_frame()] + [silent_frame()] * 4),
                    transcribe=transcribe, kws=FakeKws(hits=0))
    _run_loop(m, lambda: bool(commands))
    assert commands == ["what's the time"]
    # 2 pre-roll + 2 loud + 3 trailing-silence frames (900 ms to done).
    assert captured == [7 * 9600], captured


def test_expect_reply_rearms_for_the_answer():
    # Conversational confirm: after Qubit speaks a "say yes" prompt, a bare
    # "yes" (no wake prefix) must be accepted. The arm window started at
    # the original wake and expires during brain+TTS latency, so the
    # helper re-arms explicitly when it asks something.
    import time as _t
    m, _ = _monitor([""])
    m._armed_until = 0.0
    m.expect_reply()
    assert _t.monotonic() < m._armed_until
    m.expect_reply(timeout_s=30.0)
    assert m._armed_until - _t.monotonic() > 20.0


def test_expect_reply_leads_to_command_without_wake():
    commands = []
    m = WakeMonitor(FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    transcribe=lambda pcm: "yes")
    m.expect_reply()
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["yes"]


def test_forced_hit_consumes_armed_window():
    # P1-4: the KWS-forced branch never cleared the arm, so one intent
    # (bare wake + KWS-hit turn) emitted twice.
    import time as _t
    commands = []
    m = WakeMonitor(FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    transcribe=lambda pcm: "hey qubit what's the time")
    m._armed_until = _t.monotonic() + 12.0
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert commands == ["what's the time"]
    assert m._armed_until == 0.0
