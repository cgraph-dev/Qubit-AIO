"""The empty-transcript message must name a silent mic, not an ASR label."""
import array
import math

from voicebridge.desktop_helper import _empty_transcript_reason


def _pcm(peak: int, samples: int = 1600) -> bytes:
    a = array.array("h", [peak, -peak] * (samples // 2))
    return a.tobytes()


def test_silent_capture_names_the_microphone():
    reason = _empty_transcript_reason(b"", "accurate empty")
    assert "microphone signal" in reason.lower(), reason
    low = _pcm(int(0.001 * 32768))
    reason = _empty_transcript_reason(low, "accurate empty")
    assert "microphone signal" in reason.lower(), reason


def test_audible_capture_stays_friendly():
    reason = _empty_transcript_reason(_pcm(1000), "accurate empty")
    assert reason == "No speech recognised.", reason


def test_real_fault_reason_is_preserved():
    assert _empty_transcript_reason(_pcm(1000), "STT_CONNECT_FAILED") == (
        "STT_CONNECT_FAILED")
