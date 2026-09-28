"""PACKET-HOTKEY-RCTRL: capture-path regression tests for the rCtrl PTT fix.

Root cause (live-verified): the capture path opens the PortAudio "default"
input, which follows the Pulse/PipeWire DEFAULT SOURCE. The configured mic
pin (`mic.device_name`) is applied once at `VoiceOrchestrator.start()`; when
the default source later drifts (headset plugged in, GNOME input switch),
the rCtrl capture silently reroutes to a mic the user is not using -> pure
zeros -> "I didn't hear anything", while the web dashboard (browser-side
recording) keeps working.

Fix under test: `_capture_utterance()` re-asserts
`pin_pulse_default_source(cfg.mic.device_name)` before EVERY capture, so the
rCtrl path stays bound to the configured mic without a service restart.

These tests bind the REAL `_capture_utterance` / `_process_utterance` onto a
minimal stub self (the repo's established pattern) so the full hotkey->mic
pipeline logic is exercised with no audio hardware and no live state.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.audio.mic import CaptureResult  # noqa: E402
from voicebridge.config import Config  # noqa: E402
from voicebridge.orchestrator import VoiceOrchestrator  # noqa: E402


class _FakeMic:
    """Context-manager mic that records call order and returns a canned result."""

    def __init__(self, result, order=None):
        self.result = result
        self.order = order if order is not None else []
        self.endpointer = type("EP", (), {"state": "idle"})()

    def open(self):
        self.order.append(("open",))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record_utterance(self, stop_req, barge_in=None, silence_timeout_s=None,
                         **kw):
        self.order.append(("record",))
        return self.result

    def close(self):
        self.order.append(("close",))


class _Transcript:
    def __init__(self, text="", empty=True, confidence=None, language="en"):
        self.text = text
        self.empty = empty
        self.confidence = confidence
        self.language = language


def _result(wav=None, ended_by="stop_requested", peak_rms=0.1):
    return CaptureResult(
        wav_path=Path(wav) if wav else None,
        duration_s=0.3, frames=1, ended_by=ended_by, peak_rms=peak_rms)


def _make_self(cfg_data, mic_result, order=None):
    """Stub `VoiceOrchestrator` with real capture/process methods bound."""
    o = VoiceOrchestrator.__new__(VoiceOrchestrator)
    o.cfg = Config(data=cfg_data)
    o._mic = _FakeMic(mic_result, order)
    o._mic_pin_source = ""
    o._ptt_supported = False            # plain open->record path (no key watchers)
    o._toggle_armed = False
    o._toggle_stop = threading.Event()
    o._ptt_hold = False
    o._press_time = 0.0
    o._release_time = 0.0
    o._stop = threading.Event()
    o.muted = False
    o.spoken = []
    o.push = []
    o.speak = lambda text, *a, **k: o.spoken.append(text)
    o._play_beep = lambda which: None
    o._pause_barge_stream = lambda: False
    o._start_barge_stream = lambda: None
    return o


# ---------------------------------------------------------------- the fix

def test_capture_reasserts_mic_pin_before_open(monkeypatch):
    """A configured mic.device_name must be re-pinned BEFORE the capture
    stream opens, so a drifted Pulse default source cannot reroute rCtrl."""
    order = []
    def fake_pin(name):
        order.append(("pin", name))
        return "alsa_input.usb-Focusrite_Scarlett_2i2_4th_Gen_...-00.analog-surround-40.7"
    monkeypatch.setattr("voicebridge.orchestrator.pin_pulse_default_source", fake_pin)
    o = _make_self(
        {"mic": {"device_name": "Scarlett 2i2 4th Gen: USB Audio"}},
        _result(wav="/tmp/qubit-voice-agent/utt-test.wav"), order=order)
    out = o._capture_utterance()
    assert out == "/tmp/qubit-voice-agent/utt-test.wav"
    assert ("pin", "Scarlett 2i2 4th Gen: USB Audio") in order, order
    assert order[0] == ("pin", "Scarlett 2i2 4th Gen: USB Audio"), \
        "pin must be re-asserted before the mic stream opens"
    assert ("open",) in order and ("record",) in order


def test_capture_skips_pin_without_device_name(monkeypatch):
    """No mic.device_name configured -> no pin call; capture still works."""
    pin_calls = []
    monkeypatch.setattr("voicebridge.orchestrator.pin_pulse_default_source",
                        lambda name: pin_calls.append(name) or "src")
    o = _make_self({"mic": {"device_name": ""}},
                   _result(wav="/tmp/qubit-voice-agent/utt-test.wav"))
    out = o._capture_utterance()
    assert out == "/tmp/qubit-voice-agent/utt-test.wav"
    assert pin_calls == []


def test_capture_pin_missing_mic_degrades_gracefully(monkeypatch):
    """Configured mic absent (pin returns None): capture follows the current
    default and reports the existing typed failure — never guesses a source."""
    pin_calls = []
    monkeypatch.setattr("voicebridge.orchestrator.pin_pulse_default_source",
                        lambda name: pin_calls.append(name) or None)
    o = _make_self(
        {"mic": {"device_name": "Scarlett 2i2 4th Gen: USB Audio"}},
        _result(ended_by="silence", peak_rms=0.0))
    out = o._capture_utterance()
    assert out is None
    assert pin_calls == ["Scarlett 2i2 4th Gen: USB Audio"]
    assert o.spoken and "didn't hear anything" in o.spoken[-1], o.spoken


def test_capture_reasserts_pin_every_turn_after_drift(monkeypatch):
    """The re-pin must happen on EVERY capture (not just at startup), so a
    default-source drift between service restarts is self-corrected without
    a restart — this is the 'service restart behavior' regression."""
    pin_calls = []
    monkeypatch.setattr("voicebridge.orchestrator.pin_pulse_default_source",
                        lambda name: pin_calls.append(name) or "alsa_input.Scarlett")
    o = _make_self(
        {"mic": {"device_name": "Scarlett 2i2 4th Gen: USB Audio"}},
        _result(wav="/tmp/qubit-voice-agent/utt-test.wav"))
    assert o._capture_utterance() == "/tmp/qubit-voice-agent/utt-test.wav"
    assert o._capture_utterance() == "/tmp/qubit-voice-agent/utt-test.wav"
    assert len(pin_calls) == 2, "every capture must re-assert the configured pin"


def test_capture_logs_pin_source_once_per_effective_change(monkeypatch):
    """Telemetry: `mic.pin_source` is emitted only when the effective pin
    CHANGES (drift corrected), not on every capture (no journal spam)."""
    events = []
    monkeypatch.setattr("voicebridge.orchestrator.pin_pulse_default_source",
                        lambda name: "alsa_input.usb-Focusrite_Scarlett_2i2_4th_Gen_...-00")
    monkeypatch.setattr("voicebridge.orchestrator.log_event",
                        lambda ev, **kw: events.append((ev, kw)))
    o = _make_self(
        {"mic": {"device_name": "Scarlett 2i2 4th Gen: USB Audio"}},
        _result(wav="/tmp/qubit-voice-agent/utt-test.wav"))
    o._capture_utterance()   # first: unknown pin -> logs the effective pin
    o._capture_utterance()   # second: unchanged -> no duplicate event
    pins = [e for e in events if e[0] == "mic.pin_source"]
    assert len(pins) == 1, events


# ------------------------------------------------------- failure handling

def test_capture_zero_read_retries_then_reports_microphone_failure(monkeypatch):
    """A dead/zero capture (e.g. the drifted default source delivering pure
    zeros) is retried once and then degrades to the typed spoken failure —
    the exact rCtrl symptom this packet fixes."""
    monkeypatch.setattr("voicebridge.orchestrator.pin_pulse_default_source",
                        lambda name: "alsa_input.Scarlett")
    o = _make_self(
        {"mic": {"device_name": "Scarlett 2i2 4th Gen: USB Audio"}},
        _result(ended_by="silence", peak_rms=0.0))
    out = o._capture_utterance()
    assert out is None
    opens = [c for c in o._mic.order if c[0] == "open"]
    records = [c for c in o._mic.order if c[0] == "record"]
    assert len(records) == 2, "zero-read must be retried once (attempt 1 + 2)"
    assert len(opens) == 2
    assert o.spoken and "didn't hear anything" in o.spoken[-1], o.spoken


def test_capture_mic_exception_reports_typed_failure(monkeypatch):
    """A capture open/record exception (device gone) -> 'I couldn't hear
    you.' and None, never a silent pass."""
    monkeypatch.setattr("voicebridge.orchestrator.pin_pulse_default_source",
                        lambda name: "alsa_input.Scarlett")

    class _BoomMic(_FakeMic):
        def record_utterance(self, stop_req, barge_in=None, silence_timeout_s=None,
                             **kw):
            raise OSError("device unavailable")

    o = VoiceOrchestrator.__new__(VoiceOrchestrator)
    o.cfg = Config(data={"mic": {"device_name": "Scarlett 2i2 4th Gen: USB Audio"}})
    o._mic = _BoomMic(_result(wav="/tmp/x.wav"))
    o._mic_pin_source = ""
    o._ptt_supported = False
    o._toggle_armed = False
    o._toggle_stop = threading.Event()
    o._ptt_hold = False
    o._press_time = 0.0
    o._release_time = 0.0
    o._stop = threading.Event()
    o.muted = False
    o.spoken = []
    o.speak = lambda text, *a, **k: o.spoken.append(text)
    o._play_beep = lambda which: None
    o._pause_barge_stream = lambda: False
    o._start_barge_stream = lambda: None
    out = o._capture_utterance()
    assert out is None
    assert o.spoken and "couldn't hear you" in o.spoken[-1], o.spoken


# ----------------------------------------------------- empty/unrecognized speech

def _process_self(transcript):
    o = VoiceOrchestrator.__new__(VoiceOrchestrator)
    o.cfg = Config(data={"asr": {"min_confidence": 0.02}})
    o._asr = object()   # non-None guard only
    o.spoken = []
    o.push = []
    o.speak = lambda text, *a, **k: o.spoken.append(text)
    o._push_input = lambda text, conf, src: o.push.append((text, conf, src))
    o._last_spoken_language = ""
    o._transcribe_with_fallback = lambda wav: transcript
    return o


def test_process_empty_transcript_reports_unrecognized():
    """Empty ASR transcript -> typed 'I didn't catch that.', no routing."""
    o = _process_self(_Transcript(text="", empty=True))
    o._process_utterance("/tmp/utt.wav")
    assert o.spoken == ["I didn't catch that. Try again."], o.spoken
    assert o.push == [], "empty speech must never reach the router"


def test_process_filler_word_reports_unrecognized():
    """'um'-class filler ASR output is unrecognized speech, not a task."""
    o = _process_self(_Transcript(text="um", empty=False, confidence=1.0))
    o._process_utterance("/tmp/utt.wav")
    assert o.spoken == ["I didn't catch that. Try again."], o.spoken
    assert o.push, "filler is logged as input then rejected"


def test_process_low_confidence_reports_unrecognized():
    """Low-confidence provider transcripts are rejected by the typed gate."""
    o = _process_self(_Transcript(text="something", empty=False, confidence=0.005))
    o._process_utterance("/tmp/utt.wav")
    assert o.spoken == ["Sorry, I didn't catch that. Could you repeat it?"], o.spoken
    assert o.push == []