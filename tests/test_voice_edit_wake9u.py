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


def test_kws_cue_on_hit_is_opt_in(monkeypatch):
    """An immediate spotter cue is available but off by default.

    The final ``wake`` beep confirms an executed command after endpointing;
    this cue only says "the spotter heard the phrase now". Default OFF
    because acoustic false-fires exist.
    """
    import voicebridge.voice_edit.wake as wake_mod

    beeps = []
    monkeypatch.setattr(wake_mod, "beep",
                        lambda cfg, kind="wake": beeps.append(kind) or True)

    def run(cue):
        kws = FakeKws(hits=1)
        m = WakeMonitor(
            FakeCfg(wake={"enabled": True, "cue_on_kws": cue,
                          "vad": "energy"}),
            on_command=lambda c: None,
            is_busy=lambda: False,
            mic_factory=lambda: SeqMic(
                [loud_frame(), loud_frame()] + [silent_frame()] * 8),
            transcribe=lambda pcm: "hey qubit what time is it",
            kws=kws)
        _run_loop(m, lambda: kws.seen >= 3)

    run(True)
    assert "ready" in beeps
    beeps.clear()
    run(False)
    assert "ready" not in beeps


def test_forced_segment_emits_command_from_kws():
    # Keyword fired; ASR rendered the wake phrase -> strip it.
    m, commands = _monitor(["hey qubit, type this for me: hello"])
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert commands == ["type this for me: hello"]
    # Keyword fired but ASR rendered no wake phrase at all -> ARM silently,
    # never execute room noise as a command (false-fire tightening: the
    # spotter fires on acoustics alone). The command gets its second chance
    # inside the arm window.
    m2, commands2 = _monitor(["type this for me: hello"])
    m2._handle_segment(b"\x00" * 9600, forced=True)
    assert commands2 == []
    import time as _t
    assert m2._armed_until > _t.monotonic()
    # Keyword fired with no speech -> arm, never emit.
    m3, commands3 = _monitor([""])
    m3._handle_segment(b"\x00" * 9600, forced=True)
    assert commands3 == []


def test_forced_bare_builtin_arms_silently(monkeypatch):
    # A bare built-in ("qubit, ...") on the forced path arms like the
    # unarmed path does — room chatter starting with the name never executes.
    import voicebridge.voice_edit.wake as wake_mod
    beeps = []
    monkeypatch.setattr(wake_mod, "beep",
                        lambda cfg, kind="wake": beeps.append(kind) or True)
    m, commands = _monitor(["qubit, what time is it"])
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert commands == []
    assert beeps == []


def test_forced_full_phrase_beeps_once(monkeypatch):
    # The KWS-hit beep moved to actual emission: exactly one beep per
    # executed command, silence otherwise.
    import voicebridge.voice_edit.wake as wake_mod
    beeps = []
    monkeypatch.setattr(wake_mod, "beep",
                        lambda cfg, kind="wake": beeps.append(kind) or True)
    m, commands = _monitor(["hey qubit, what time is it"])
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert commands == ["what time is it"]
    assert beeps == ["wake"]


def test_forced_trailing_address_still_emits():
    # A deliberate trailing address ("open youtube, qubit") stays executable.
    m, commands = _monitor(["open youtube, qubit"])
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert commands == ["open youtube"]


def test_forced_custom_trigger_still_emits(monkeypatch):
    # Owner custom triggers are directed speech, not bare chatter.
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_custom_triggers", lambda: ["computer"])
    m, commands = _monitor(["computer, open youtube"])
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert commands == ["open youtube"]


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
    assert view["wake"]["asr_fallback_max_s"] == 15.0
    assert view["wake"]["max_s"] == 15.0
    settings_mod.update_voice_edit(
        cfg, {"wake": {"asr_fallback": False, "asr_fallback_max_s": 99}})
    view = settings_mod.settings_view(cfg)
    assert view["wake"]["asr_fallback"] is False
    assert view["wake"]["asr_fallback_max_s"] == 25.0  # clamped, not stored raw


def test_wake_max_s_defaults_and_clamps():
    """Segment cap defaults to the probe bound and stays in 2..25 s."""
    from voicebridge.voice_edit.wake import _wake_max_s
    assert _wake_max_s(FakeCfg()) == 15.0
    assert _wake_max_s(FakeCfg(wake={"enabled": True, "max_s": 99})) == 25.0
    assert _wake_max_s(FakeCfg(wake={"enabled": True, "max_s": 0.5})) == 2.0
    assert _wake_max_s(FakeCfg(wake={"enabled": True, "max_s": 12})) == 12.0


def test_endpointer_completes_continuous_voicing_at_max_s():
    """A never-silent room yields bounded segments, not one endless blob.

    Regression: with the 25 s default cap, continuous voicing (fans, TTS
    feedback, repeated attempts) completed only 25 s segments that always
    exceeded the probe bound and were silently skipped — wake could never
    fire. At the 15 s cap every completed segment fits a probe, while slow
    deliberate speech (the 7.8 s mid-word cut at the old 8 s cap) arrives
    whole.
    """
    from voicebridge.voice_edit.wake import _wake_max_s
    ep = EnergyEndpointer(rms_gate=0.008, max_s=_wake_max_s(FakeCfg()))
    states = [ep.add_frame(loud_frame()) for _ in range(60)]
    assert states[-1] == "done"
    first_done = states.index("done") + 1
    assert 50 <= first_done <= 52  # 15 s / 300 ms frames
    assert first_done * 9600 <= int(15.0 * 16000 * 2)  # fits the probe bound


def test_asr_fallback_bound_matches_segment_cap():
    """Capped segments must still fit a fallback probe (coordination).

    The cap and the probe bound move together: a 14 s slow utterance
    completes as ONE capped segment that still fits the probe, while a
    16 s blob and anything past the 25 s hard clamp are skipped.
    """
    from voicebridge.voice_edit.wake import _asr_fallback_allows, _wake_max_s
    assert _wake_max_s(FakeCfg()) == 15.0
    assert _asr_fallback_allows(FakeCfg(), b"\x00" * 32000 * 14) is True
    assert _asr_fallback_allows(FakeCfg(), b"\x00" * 32000 * 16) is False
    assert _asr_fallback_allows(FakeCfg(), b"\x00" * 32000 * 26) is False


def test_slow_utterance_completes_whole_under_new_cap():
    """14 s of continuous voicing completes as ONE segment fitting the probe.

    The owner's 2026-10-04 journal case: 7.8 s of slow deliberate speech
    was cut mid-word by the old 8 s cap. Under the 15 s cap the same
    speech (and anything up to ~15 s) completes whole.
    """
    from voicebridge.voice_edit.wake import _asr_fallback_allows, _wake_max_s
    ep = EnergyEndpointer(rms_gate=0.008, max_s=_wake_max_s(FakeCfg()))
    states = [ep.add_frame(loud_frame()) for _ in range(60)]  # ~18 s
    assert states[-1] == "done"
    first_done = states.index("done") + 1
    assert 50 <= first_done <= 52  # completes at the 15 s cap, not cut early
    nbytes = first_done * 9600
    assert _asr_fallback_allows(FakeCfg(), b"\x00" * nbytes) is True


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


# ---------------------------------------------------------------------------
# bare-wake arms-only (false-fire tightening, 2026-10-02)
# ---------------------------------------------------------------------------
def test_wake_match_kind_full_vs_bare():
    from voicebridge.voice_edit.wake import wake_match_kind
    assert wake_match_kind("Hey Qubit, open youtube") == "full"
    assert wake_match_kind("hey qubit") == "full"
    assert wake_match_kind("ok, hey qubit summarize this") == "full"
    assert wake_match_kind("Qubit, I'm coming.") == "bare"
    assert wake_match_kind("qubit fix the grammar") == "bare"
    assert wake_match_kind("A cubit, type this") == "bare"
    assert wake_match_kind("someone said something") is None
    assert wake_match_kind("I thought it was Qubit.") is None


def test_bare_qubit_without_hey_is_dropped():
    # Room chatter starting with a bare "Qubit, ..." (no hey) is not
    # direction: silent drop — no beep, no arm, no execute. Saying
    # "hey qubit" stays the way to direct it; the KWS-hit path arms on
    # bare detections instead of executing them.
    m, commands = _monitor(["Qubit, I'm coming."])
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    assert m._armed_until == 0.0


def test_bare_qubit_followup_not_executed():
    m, commands = _monitor(["Qubit, open youtube", "open youtube"])
    m._handle_segment(b"\x00" * 9600)
    m._handle_segment(b"\x00" * 9600)
    assert commands == []


def test_full_hey_qubit_with_command_executes():
    m, commands = _monitor(["Hey Qubit, open youtube"])
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["open youtube"]


# ---------------------------------------------------------------------------
# Lane 1 (2026-10-03): bare-confirm bypass — "yes" settles a live open
# confirmation WITHOUT needing "hey qubit" first. Journal-proof family:
# timer "Yes." discarded 19 s after the ask (12 s arm expired), redo "Yes."
# discarded, test-4 "Conform." discarded 13 s after — all with a live
# unexpired _pending_task (120 s TTL), all dead on the wake gate.
# ---------------------------------------------------------------------------
def test_is_bare_affirmative_reuses_known_sets():
    from voicebridge.voice_edit.wake import is_bare_affirmative
    for yes in ("yes", "Yeah!", "yep", "yup", "confirm", "confirmed",
                "go ahead", "ok", "okay", "sure", "do it", "yes please"):
        assert is_bare_affirmative(yes), yes
    # Bare only: a longer message that merely contains "yes" never settles.
    assert not is_bare_affirmative("yes please set a timer for one minute")
    assert not is_bare_affirmative("yesterday we talked")
    assert not is_bare_affirmative("")
    assert not is_bare_affirmative("hey qubit")


def test_is_bare_negative_reuses_known_sets():
    from voicebridge.voice_edit.wake import is_bare_negative
    for no in ("no", "nah", "cancel", "No thanks.", "stop", "never mind"):
        assert is_bare_negative(no), no
    assert not is_bare_negative("no way Jose run it now")
    assert not is_bare_negative("")
    assert not is_bare_negative("hey qubit")


def _bypass_monitor(transcripts, **kw):
    from voicebridge.voice_edit.wake import WakeMonitor
    commands = []
    m = WakeMonitor(FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    transcribe=lambda pcm: transcripts.pop(0)
                    if transcripts else "", **kw)
    return m, commands


def test_confirm_bypass_settles_live_registry_pending(monkeypatch):
    # Affirmative + live CT registry pending settles through the existing
    # settle (called, not reimplemented) — no wake word, no arm.
    import voicebridge.voice_edit.wake as wake_mod
    settled = []
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "registry")
    monkeypatch.setattr(wake_mod, "_settle_voice_pending",
                        lambda cfg, kind: settled.append(kind) or
                        {"ok": True, "route": "registry"})
    m, commands = _bypass_monitor(["yes"])
    m._handle_segment(b"\x00" * 9600)
    assert settled == ["registry"]
    assert commands == []  # settled directly: nothing re-emitted as a turn
    assert m._armed_until == 0.0


def test_confirm_bypass_settles_live_task_pending(monkeypatch):
    import voicebridge.voice_edit.wake as wake_mod
    settled = []
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "task")
    monkeypatch.setattr(wake_mod, "_settle_voice_pending",
                        lambda cfg, kind: settled.append(kind) or
                        {"ok": True, "route": "task"})
    m, commands = _bypass_monitor(["Yes."])
    m._handle_segment(b"\x00" * 9600)
    assert settled == ["task"]
    assert commands == []


def test_confirm_bypass_helper_probe_emits_to_helper():
    # Helper in-process pending (service.task-mode staging, e.g. the timer):
    # the bare "yes" is emitted so the helper settles + speaks + audits
    # through its existing pending branch.
    m, commands = _bypass_monitor(["yes please"],
                                  pending_probe=lambda: True)
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["yes please"]


def test_confirm_bypass_negative_helper_probe_emits():
    m, commands = _bypass_monitor(["no"], pending_probe=lambda: True)
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["no"]


def test_confirm_bypass_negative_ct_denies_without_emit(monkeypatch):
    import voicebridge.voice_edit.wake as wake_mod
    denied = []
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "task")
    monkeypatch.setattr(wake_mod, "_deny_voice_pending",
                        lambda cfg, kind: denied.append(kind) or True)
    m, commands = _bypass_monitor(["cancel"])
    m._handle_segment(b"\x00" * 9600)
    assert denied == ["task"]
    assert commands == []  # a cancellation runs nothing


def test_yes_without_pending_discards_unchanged(monkeypatch):
    # No live pending anywhere: today's behavior byte-identical (discard).
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "")
    settled = []
    monkeypatch.setattr(wake_mod, "_settle_voice_pending",
                        lambda cfg, kind: settled.append(kind) or {"ok": False})
    m, commands = _bypass_monitor(["yes"])
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    assert settled == []


def test_expired_pending_yes_does_not_settle(monkeypatch):
    # The journal TTL already filtered the expired record: pending() is
    # empty, so "yes" must not settle and must not emit.
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "")
    settled = []
    monkeypatch.setattr(wake_mod, "_settle_voice_pending",
                        lambda cfg, kind: settled.append(kind) or {"ok": False})
    m, commands = _bypass_monitor(["Conform."])
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    assert settled == []


def test_no_double_settle_on_repeated_yes(monkeypatch):
    # Settle-once journal: the first "yes" consumes the record; the second
    # sees nothing pending and settles nothing.
    import voicebridge.voice_edit.wake as wake_mod
    live = ["task"]
    settled = []
    monkeypatch.setattr(wake_mod, "_live_voice_pending",
                        lambda cfg: live.pop(0) if live else "")
    monkeypatch.setattr(wake_mod, "_settle_voice_pending",
                        lambda cfg, kind: settled.append(kind) or {"ok": True})
    m, commands = _bypass_monitor(["yes", "yes"])
    m._handle_segment(b"\x00" * 9600)
    m._handle_segment(b"\x00" * 9600)
    assert settled == ["task"]
    assert commands == []


def test_forced_kws_yes_skips_bypass(monkeypatch):
    # A KWS-hit bare "yes" with a LIVE pending settles through Lane-1 (no
    # raw command emit): the pending is the consent context. With no live
    # pending the same input arms instead of starting a turn on TV dialogue.
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "task")
    settled = []
    monkeypatch.setattr(wake_mod, "_settle_voice_pending",
                        lambda cfg, kind: settled.append(kind) or {"ok": True})
    m, commands = _bypass_monitor(["yes"])
    m._handle_segment(b"\x00" * 9600, forced=True)
    assert settled == ["task"]
    assert commands == []


def test_armed_yes_without_pending_keeps_legacy_path(monkeypatch):
    # Armed + "yes" with NOTHING pending: the legacy armed emit stands
    # (the bypass only fires when a live confirmation exists).
    import time as _t
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "")
    m, commands = _bypass_monitor(["yes"])
    m._armed_until = _t.monotonic() + 12.0
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["yes"]


def test_live_voice_pending_prefers_registry_over_task(monkeypatch):
    # Mirror turn()'s T4/T5 order: registry settles before task.
    import voicebridge.voice_edit.wake as wake_mod
    import voicebridge.chat_transport as _CT

    class _Items(list):
        pass

    monkeypatch.setattr(_CT, "_pending_registry", lambda cfg, sid: ["reg"])
    monkeypatch.setattr(_CT, "_pending_task", lambda cfg, sid: ["task"])
    assert wake_mod._live_voice_pending(FakeCfg()) == "registry"
    monkeypatch.setattr(_CT, "_pending_registry", lambda cfg, sid: [])
    assert wake_mod._live_voice_pending(FakeCfg()) == "task"
    monkeypatch.setattr(_CT, "_pending_task", lambda cfg, sid: [])
    assert wake_mod._live_voice_pending(FakeCfg()) == ""


def test_nonbare_yes_with_live_pending_does_not_settle(monkeypatch):
    # "Yes ..." mid-other-turn is unaffected: a longer message that merely
    # starts with yes is not a bare confirmation, so the bypass must not
    # consume the live confirmation — and with no wake word the turn still
    # discards (today's behavior unchanged).
    import voicebridge.voice_edit.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "task")
    settled = []
    monkeypatch.setattr(wake_mod, "_settle_voice_pending",
                        lambda cfg, kind: settled.append(kind) or {"ok": True})
    m, commands = _bypass_monitor(["yes please set a timer for one minute"])
    m._handle_segment(b"\x00" * 9600)
    assert settled == []
    assert commands == []


# ---------------------------------------------------------------------------
# 2026-10-03 journal bugs: armed empty repeat (19:17) + silent discard
# ---------------------------------------------------------------------------
def _hint_monitor(replies, hints, cfg=None):
    commands = []
    it = iter(replies)
    m = WakeMonitor(cfg or FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    transcribe=lambda pcm: next(it, ""),
                    on_hint=lambda detail: hints.append(detail))
    return m, commands


def test_armed_empty_repeat_keeps_arm_for_command(monkeypatch):
    # Journal 2026-10-03 19:17: "Hey Qubit!" (beep + 12 s arm), then
    # "Hey Qubit." hit the ARMED branch, consumed the arm, stripped to
    # empty and emitted nothing — the window died silently 3 s after
    # opening, so "Redo this text for me." discarded as unarmed. An empty
    # armed repeat must re-beep ("still listening") and keep the arm.
    import voicebridge.voice_edit.wake as wake_mod
    beeps = []
    monkeypatch.setattr(wake_mod, "beep",
                        lambda cfg, kind="wake": beeps.append(kind) or True)
    hints = []
    m, commands = _hint_monitor(
        ["Hey Qubit!", "Hey Qubit.", "Redo this text for me."], hints)
    m._handle_segment(b"\x00" * 9600)  # bare wake -> beep + arm
    assert commands == []
    m._handle_segment(b"\x00" * 9600)  # empty repeat -> re-beep, arm kept
    assert commands == []
    assert hints == []
    m._handle_segment(b"\x00" * 9600)  # command inside the surviving window
    assert commands == ["Redo this text for me."]
    assert beeps == ["wake", "wake"]


def test_armed_real_command_still_consumes_arm():
    # Non-empty armed path byte-identical: consume + emit, no double-fire.
    m, commands = _monitor(["hey qubit", "fix the grammar", "do it again"])
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["fix the grammar"]
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["fix the grammar"]


def test_command_shaped_discard_posts_hint_once():
    # Journal 19:16:43 / 19:17:12+18: perfect "Redo this text for me"
    # discarded unarmed with only a journal trace — the owner heard/saw
    # nothing. Command-shaped discards post one visible hint (no beep,
    # no speech); a second within ~30 s stays silent.
    hints = []
    m, commands = _hint_monitor(
        ["Redo this text for me.", "Redo this text for me."], hints)
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    assert len(hints) == 1
    assert hints[0].lower().startswith("heard:")
    assert "hey qubit" in hints[0].lower()
    m._handle_segment(b"\x00" * 9600)
    assert len(hints) == 1


def test_timer_shaped_discard_posts_hint():
    # Timer shape resolves through the same propose_tool the task path
    # runs — it must hint, not vanish.
    hints = []
    m, commands = _hint_monitor(["set a timer for one minute"], hints)
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    assert len(hints) == 1


def test_chatter_discard_posts_no_hint():
    # Plain chatter with no command shape stays fully silent.
    hints = []
    m, commands = _hint_monitor(["someone said something random"], hints)
    m._handle_segment(b"\x00" * 9600)
    assert commands == []
    assert hints == []


def test_hint_never_for_prompt_echo_or_bare_drop():
    # The echo guard and the bare-drop path own their silence: no hint.
    echo_cfg = FakeCfg(
        stt_local={"initial_prompt": "Qubit, hey Qubit, voice assistant."})
    hints = []
    m, commands = _hint_monitor(["hey Qubit, voice assistant."], hints,
                                cfg=echo_cfg)
    m._handle_segment(b"\x00" * 9600)
    assert commands == [] and hints == []
    hints2 = []
    m2, commands2 = _hint_monitor(["Qubit, I'm coming."], hints2)
    m2._handle_segment(b"\x00" * 9600)
    assert commands2 == [] and hints2 == []


def test_discard_hint_posts_no_beep(monkeypatch):
    # Constraints: NO beep, NO speech on the hint path.
    import voicebridge.voice_edit.wake as wake_mod
    beeps = []
    monkeypatch.setattr(wake_mod, "beep",
                        lambda cfg, kind="wake": beeps.append(kind) or True)
    hints = []
    m, commands = _hint_monitor(["Redo this text for me."], hints)
    m._handle_segment(b"\x00" * 9600)
    assert len(hints) == 1
    assert beeps == []


def test_hint_cooldown_reopens_after_window():
    # Rate limit is ~30 s: an old hint lets a new one through.
    hints = []
    m, commands = _hint_monitor(
        ["Redo this text for me.", "Redo this text for me."], hints)
    m._handle_segment(b"\x00" * 9600)
    assert len(hints) == 1
    m._last_hint_at -= 31.0
    m._handle_segment(b"\x00" * 9600)
    assert len(hints) == 2


def test_segment_open_emits_capturing_for_orb_listening(monkeypatch):
    # Owner 2026-10-04: while THEY talk the orb sat orange — the only
    # listening signals fired at command-accept, never mid-speech. The
    # loop now traces wake.capturing at the segment-open edge (re-fired
    # ~2 s while voiced frames arrive) so the orb shows ME-TALKING live.
    import voicebridge.voice_edit.wake as wake_mod

    stages = []
    monkeypatch.setattr(wake_mod, "_trace",
                        lambda stage, **kw: stages.append(stage))
    m = WakeMonitor(FakeCfg(wake={"enabled": True, "vad": "energy"}),
                    on_command=lambda c: None,
                    is_busy=lambda: False,
                    mic_factory=lambda: SeqMic(
                        [loud_frame(), loud_frame()] * 8
                        + [silent_frame()] * 8),
                    transcribe=lambda pcm: "random room chatter",
                    kws=FakeKws(hits=0))
    _run_loop(m, lambda: "capturing" in stages)
    assert "capturing" in stages, stages
