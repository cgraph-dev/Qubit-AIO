"""Voice Activity Detection (webrtcvad) + utterance endpointing + barge-in.

webrtcvad works on 16 kHz mono 16-bit PCM in 10/20/30 ms frames. We feed it
30 ms frames. The Endpointer is a small state machine:

    idle -> (speech_start_ms of voiced frames) -> speech
    speech -> (silence_timeout_s of unvoiced) -> done

BargeInDetector watches the mic while TTS is speaking and raises a flag after a
short run of voiced frames, so the orchestrator can stop playback.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

FRAME_MS = 30
FRAME_RATE = 16000
FRAME_SIZE = FRAME_RATE * FRAME_MS // 1000  # 480 samples


@dataclass
class Endpointer:
    """Detect speech start / end from 30 ms 16 kHz frames of int16 PCM."""

    aggressiveness: int = 2
    speech_start_ms: int = 220        # consecutive voiced time to enter speech
    silence_timeout_s: float = 1.2    # trailing silence that ends the utterance
    max_utterance_s: float = 30.0
    speech_frames: int = field(init=False, default=0)
    silence_frames: int = field(init=False, default=0)
    total_frames: int = field(init=False, default=0)
    state: str = field(init=False, default="idle")  # idle|speech|done

    def __post_init__(self) -> None:
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self.aggressiveness)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"webrtcvad unavailable: {exc}") from exc
        self._start_frames = max(1, int(self.speech_start_ms / FRAME_MS))
        self._silence_frames = max(1, int(self.silence_timeout_s * 1000 / FRAME_MS))
        self._max_frames = int(self.max_utterance_s * 1000 / FRAME_MS)

    def reset(self) -> None:
        self.speech_frames = 0
        self.silence_frames = 0
        self.total_frames = 0
        self.state = "idle"

    def add_frame(self, pcm_int16: bytes) -> str:
        """Feed one 30 ms frame; returns 'idle'|'speech'|'done'."""
        self.total_frames += 1
        voiced = self._vad.is_speech(pcm_int16, FRAME_RATE)
        if self.state == "idle":
            if voiced:
                self.speech_frames += 1
                self.silence_frames = 0
                if self.speech_frames >= self._start_frames:
                    self.state = "speech"
            else:
                self.speech_frames = 0
            return self.state
        if self.state == "speech":
            if voiced:
                self.silence_frames = 0
            else:
                self.silence_frames += 1
            if self.silence_frames >= self._silence_frames or self.total_frames >= self._max_frames:
                self.state = "done"
            return self.state
        return self.state


class BargeInDetector:
    """Detects user speech while the assistant is speaking (for interruption)."""

    def __init__(self, aggressiveness: int = 2, trigger_ms: int = 150) -> None:
        import webrtcvad
        self._vad = webrtcvad.Vad(aggressiveness)
        self._trigger_frames = max(1, trigger_ms // FRAME_MS)
        self._voiced = 0
        self._lock = threading.Lock()
        self._triggered = threading.Event()

    def reset(self) -> None:
        with self._lock:
            self._voiced = 0
            self._triggered.clear()

    def add_frame(self, pcm_int16: bytes) -> bool:
        voiced = self._vad.is_speech(pcm_int16, FRAME_RATE)
        with self._lock:
            self._voiced = self._voiced + 1 if voiced else 0
            if self._voiced >= self._trigger_frames:
                self._triggered.set()
        return self._triggered.is_set()

    @property
    def triggered(self) -> bool:
        return self._triggered.is_set()


def pcm16(chunk: np.ndarray) -> bytes:
    """float32 [-1,1] -> int16 PCM bytes."""
    return (np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def resample_linear(x: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Simple linear-interpolation resampler (quality fine for 48k->16k speech)."""
    if src_rate == dst_rate:
        return x
    n_out = int(len(x) * dst_rate / src_rate)
    src_idx = np.linspace(0, len(x) - 1, num=n_out)
    i0 = src_idx.astype(np.int64)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = (src_idx - i0).astype(np.float32)[:, None]
    return x[i0] * (1 - frac) + x[i1] * frac
