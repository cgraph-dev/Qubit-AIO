"""Voice Activity Detection (Silero neural VAD preferred, webrtcvad fallback)
+ utterance endpointing + barge-in.

Silero VAD v5 (sherpa-onnx) works on 16 kHz float32 in 512-sample (32 ms)
windows and is far more accurate than webrtcvad in noise. The Endpointer is a
small state machine (backend-agnostic):

    idle -> (speech_start_ms of voiced frames) -> speech
    speech -> (silence_timeout_s of unvoiced) -> done

BargeInDetector watches the mic while TTS is speaking and raises a flag after a
short run of voiced frames, so the orchestrator can stop playback.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..logging_setup import log_event

FRAME_MS = 30
FRAME_RATE = 16000
FRAME_SIZE = FRAME_RATE * FRAME_MS // 1000  # 480 samples


class SileroVadGate:
    """Silero neural VAD via sherpa-onnx (windowed, raw per-window decisions).

    Buffers arbitrary-size 16 kHz int16 PCM into the model's native window
    (512 samples for v4, 576 for v5 — read from the loaded VadModel, NEVER
    hardcoded: a mismatch makes sherpa-onnx exit(255) at the first is_speech
    call) and returns the VAD decision for each completed window. Endpointing
    hysteresis is left to the caller (Endpointer) — here hysteresis is disabled
    so we get raw frame-level speech/non-speech.
    """

    def __init__(self, model_path: Path | str, threshold: float = 0.5,
                 num_threads: int = 1) -> None:
        try:
            import sherpa_onnx as so
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"sherpa-onnx unavailable for Silero VAD: {exc}") from exc
        cfg = so.VadModelConfig(
            sample_rate=FRAME_RATE,
            num_threads=num_threads,
            provider="cpu",
            debug=False,
            silero_vad=so.SileroVadModelConfig(
                model=str(Path(model_path)),
                threshold=float(threshold),
                # sherpa's VadModel is a state machine: zero hysteresis made it
                # never latch into speech (tested). These bridge short word
                # gaps; the Endpointer's own timers dominate end detection.
                min_silence_duration=0.25,
                min_speech_duration=0.1,
                max_speech_duration=30.0,
                # window_size intentionally NOT set here — defaults to the
                # model's native size (512 v4 / 576 v5)
            ),
        )
        self._vad = so.VadModel.create(cfg)
        # NOTE: window_size is a METHOD in sherpa-onnx (not a property)
        self._window = self._vad.window_size() * 2  # samples * 2 bytes (int16)
        self._buf = b""
        self._last = False   # hold last window decision between windows

    def reset(self) -> None:
        self._buf = b""
        self._last = False
        self._vad.reset()

    def is_speech(self, pcm_int16: bytes) -> bool:
        """Decision for the LAST completed window; holds the previous decision
        on frames that don't complete a window.

        Input frames (480 samples) rarely align with the model window
        (576 samples for v5) — without the hold, every ~6th call returns False
        (buffer-refill cadence) and consecutive-frame counters downstream can
        never reach their threshold.
        """
        self._buf += pcm_int16
        while len(self._buf) >= self._window:
            chunk = self._buf[: self._window]
            self._buf = self._buf[self._window:]
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            self._last = self._vad.is_speech(samples)
        return self._last


def _make_vad_gate(backend: str, aggressiveness: int,
                   silero_model: Optional[Path | str] = None):
    """Returns a callable `is_speech(pcm_int16: bytes) -> bool`."""
    if backend == "silero":
        try:
            gate = SileroVadGate(silero_model or _default_silero_model())
            log_event("vad.backend", backend="silero", model=str(silero_model))
            return gate.is_speech
        except Exception as exc:
            log_event("vad.silero.unavailable", error=str(exc)[:160],
                      fallback="webrtc")
    import webrtcvad
    vad = webrtcvad.Vad(aggressiveness)
    return lambda pcm: vad.is_speech(pcm, FRAME_RATE)


def _default_silero_model() -> Path:
    cand = Path.home() / ".local/share/openclaw-voice-bridge/models/silero_vad_v5.onnx"
    if not cand.exists():
        raise FileNotFoundError(f"silero_vad_v5.onnx not found at {cand}")
    return cand


@dataclass
class Endpointer:
    """Detect speech start / end from 30 ms 16 kHz frames of int16 PCM."""

    aggressiveness: int = 2
    speech_start_ms: int = 220        # consecutive voiced time to enter speech
    silence_timeout_s: float = 1.2    # trailing silence that ends the utterance
    max_utterance_s: float = 30.0
    rms_gate: float = 0.004           # frame RMS floor for "voiced" (after gain)
    vad_backend: str = "silero"       # silero | webrtc
    silero_model: Optional[Path | str] = None
    speech_frames: int = field(init=False, default=0)
    silence_frames: int = field(init=False, default=0)
    total_frames: int = field(init=False, default=0)
    voiced_frames: int = field(init=False, default=0)
    state: str = field(init=False, default="idle")  # idle|speech|done

    def __post_init__(self) -> None:
        self._vad = _make_vad_gate(self.vad_backend, self.aggressiveness,
                                   self.silero_model)
        self._start_frames = max(1, int(self.speech_start_ms / FRAME_MS))
        self._silence_frames = max(1, int(self.silence_timeout_s * 1000 / FRAME_MS))
        self._max_frames = int(self.max_utterance_s * 1000 / FRAME_MS)

    def _gate_voiced(self, pcm_int16: bytes) -> bool:
        """VAD decision ANDed with an RMS floor.

        The VAD (webrtcvad at low aggressiveness, or Silero on hiss) can call
        ambient noise "speech" once a gain boost is applied. The RMS gate keeps
        quiet noise out of the speech decision; real speech sits well above it.
        """
        rms = np.sqrt(np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32).__pow__(2).mean()) / 32767.0
        return self._vad(pcm_int16) and rms >= self.rms_gate

    def reset(self, silence_timeout_s: Optional[float] = None) -> None:
        self.speech_frames = 0
        self.silence_frames = 0
        self.total_frames = 0
        self.voiced_frames = 0
        self.state = "idle"
        # dynamic end-of-turn tuning: hands-free/toggle captures get a longer
        # silence window (user pauses mid-thought), hold-to-talk keeps the
        # fast setting (release is the end signal anyway)
        if silence_timeout_s is not None:
            self.silence_timeout_s = float(silence_timeout_s)
            self._silence_frames = max(
                1, int(self.silence_timeout_s * 1000 / FRAME_MS))

    def add_frame(self, pcm_int16: bytes) -> str:
        """Feed one 30 ms frame; returns 'idle'|'speech'|'done'."""
        self.total_frames += 1
        voiced = self._gate_voiced(pcm_int16)
        if voiced:
            self.voiced_frames += 1
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

    def __init__(self, aggressiveness: int = 2, trigger_ms: int = 150,
                 rms_gate: float = 0.004, vad_backend: str = "silero",
                 silero_model: Optional[Path | str] = None) -> None:
        self._vad = _make_vad_gate(vad_backend, aggressiveness, silero_model)
        self._trigger_frames = max(1, trigger_ms // FRAME_MS)
        self._rms_gate = rms_gate
        self._voiced = 0
        self._lock = threading.Lock()
        self._triggered = threading.Event()

    def reset(self) -> None:
        with self._lock:
            self._voiced = 0
            self._triggered.clear()

    def add_frame(self, pcm_int16: bytes) -> bool:
        rms = np.sqrt(np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32).__pow__(2).mean()) / 32767.0
        voiced = self._vad(pcm_int16) and rms >= self._rms_gate
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
    """Simple linear-interpolation resampler (quality fine for 48k->16k speech).

    Preserves input dimensionality (1D mono in -> 1D out, 2D in -> 2D out).
    """
    if src_rate == dst_rate:
        return x
    n_out = int(len(x) * dst_rate / src_rate)
    src_idx = np.linspace(0, len(x) - 1, num=n_out)
    i0 = src_idx.astype(np.int64)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = (src_idx - i0).astype(np.float32)
    if x.ndim > 1:
        frac = frac[:, None]
    return x[i0] * (1 - frac) + x[i1] * frac


class FrameSplitter:
    """Accumulates 16 kHz mono float32 and yields complete 30 ms int16 frames.

    Streams can arrive in ANY block size (e.g. 480 samples @ 48 kHz -> 160
    samples @ 16 kHz per block, which is less than one 480-sample frame).
    Without buffering, n_full == 0 every block and nothing is ever captured —
    this bit us on the Focusrite (blocksize 480)."""

    def __init__(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)

    def feed(self, mono16: np.ndarray) -> list[bytes]:
        """Feed resampled mono; return all complete 30 ms frames as int16 bytes."""
        combined = np.concatenate([self._buf, mono16])
        n_full = len(combined) // FRAME_SIZE
        frames = []
        for i in range(n_full):
            frames.append(pcm16(combined[i * FRAME_SIZE:(i + 1) * FRAME_SIZE]))
        self._buf = combined[n_full * FRAME_SIZE:]
        return frames

    def flush(self) -> list[bytes]:
        out = self.feed(np.zeros(0, dtype=np.float32))
        self._buf = np.zeros(0, dtype=np.float32)
        return out
