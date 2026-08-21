"""Microphone capture with hold-to-talk semantics + VAD endpointing.

Design notes (hard-won on the Focusrite Scarlett 2i2 4th Gen / PipeWire):

  * The interface's default PipeWire profile is 4-channel ("analog-surround-40")
    and the mic lands on channel 0 with a *quiet* level unless the gain knob is
    up. webrtcvad at aggressiveness 2 will happily declare a whole utterance
    "silence" if the level is low — and the old code then DISCARDED the audio.
  * So push-to-talk must NOT be VAD-gated: press→release audio is always kept
    (if RMS is above a floor), VAD only trims pre-roll and provides
    hands-free endpointing (silence timeout) when the key is held. ASR is the
    final judge of whether anything was said.
  * A `gain` multiplier is applied before VAD and saving so quiet interfaces
    work; levels are logged so a silent mic is diagnosable in seconds.
"""
from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from .vad import FRAME_MS, FRAME_RATE, FRAME_SIZE, Endpointer, FrameSplitter, pcm16, resample_linear


@dataclass
class CaptureResult:
    wav_path: Optional[Path]
    duration_s: float
    frames: int
    ended_by: str  # "silence" | "stop_requested" | "max_duration" | "cancelled"
    peak_rms: float = 0.0


class MicCapture:
    def __init__(self, device: Optional[int] = None, sample_rate: int = 48000,
                 channels: int = 2, blocksize: int = 480,
                 endpointer: Optional[Endpointer] = None,
                 gain: float = 4.0, min_rms: float = 0.0008,
                 min_speech_s: float = 0.25) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.endpointer = endpointer or Endpointer()
        self.gain = gain
        self.min_rms = min_rms
        self.min_speech_s = min_speech_s
        self._stream: Optional[sd.InputStream] = None

    def open(self) -> "MicCapture":
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            device=self.device,
            blocksize=self.blocksize,
            dtype="float32",
        )
        self._stream.start()
        return self

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def __enter__(self) -> "MicCapture":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def _mono(data: np.ndarray) -> np.ndarray:
        """Downmix to mono taking the loudest channel (robust to channel maps)."""
        if data.ndim == 1 or data.shape[1] == 1:
            return data[:, 0] if data.ndim == 2 else data
        return np.max(np.abs(data), axis=1) * np.sign(data[:, 0] if data.shape[1] else data)

    def record_utterance(self, stop_requested: threading.Event,
                         barge_in=None, out_dir: Optional[Path] = None,
                         pre_roll_ms: int = 200) -> CaptureResult:
        """Record until silence endpoint, stop_requested, or max duration.

        The audio from press→release is ALWAYS saved when it has any audible
        energy (RMS >= min_rms) — VAD is only used to trim silence and to end
        hands-free captures via the silence timeout.
        """
        if self._stream is None:
            raise RuntimeError("mic not open; call open() first")
        ep = self.endpointer
        ep.reset()
        if barge_in is not None:
            barge_in.reset()

        buf16: list[bytes] = []           # 30 ms int16 frames (full capture)
        recent: list[bytes] = []          # rolling pre-roll (idle frames only)
        splitter = FrameSplitter()
        pre_roll_frames = max(1, pre_roll_ms // 30)
        ended_by = "silence"
        t0 = time.time()
        peak_rms = 0.0
        first_voice_idx: Optional[int] = None   # buf16 index where speech began
        zero_frames = 0                   # consecutive dead-stream frames

        while True:
            data, _ = self._stream.read(self.blocksize)  # float32 (n, ch)
            mono = self._mono(data)
            mono = np.clip(mono * self.gain, -1.0, 1.0)
            mono16 = resample_linear(mono, self.sample_rate, FRAME_RATE)
            frame_rms = float(np.sqrt((mono ** 2).mean()))
            if frame_rms < 1e-4:
                zero_frames += 1
                # Fast-fail a DEAD stream (pure zeros): after ~0.6s of nothing
                # and no speech yet, stop — the device needs a reopen (the
                # orchestrator retries once). A quiet-but-working mic has a
                # noise floor ~0.003, well above this.
                if zero_frames >= 20 and ep.state != "speech" \
                        and time.time() - t0 > 0.6:
                    ended_by = "silence"
                    break
            else:
                zero_frames = 0
            for raw in splitter.feed(mono16):
                frame = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
                if barge_in is not None:
                    barge_in.add_frame(raw)
                if stop_requested.is_set():
                    ended_by = "stop_requested" if ep.state == "speech" else (
                        "stop_requested" if peak_rms >= self.min_rms else "cancelled")
                    break
                state = ep.add_frame(raw)
                buf16.append(raw)
                if state == "idle":
                    recent.append(raw)
                    if len(recent) > pre_roll_frames:
                        recent.pop(0)
                if first_voice_idx is None and ep.state == "speech":
                    first_voice_idx = len(buf16) - 1
                rms = float(np.sqrt((frame ** 2).mean()))
                if rms > peak_rms:
                    peak_rms = rms
            if stop_requested.is_set():
                break
            if ep.state == "done":
                break
            if time.time() - t0 > ep.max_utterance_s + 1.0:
                ended_by = "max_duration" if peak_rms >= self.min_rms else "cancelled"
                break

        voiced_s = ep.voiced_frames * FRAME_MS / 1000.0
        if not buf16 or (ended_by == "cancelled" and peak_rms < self.min_rms) \
                or voiced_s < self.min_speech_s:
            return CaptureResult(None, 0.0, 0, ended_by, peak_rms)

        # Include pre-roll context before the first voiced frame for ASR stability.
        # If VAD never entered 'speech' (missed quiet speech), keep the whole
        # capture — ASR is the final judge.
        if first_voice_idx is not None:
            start = max(0, first_voice_idx - pre_roll_frames)
        else:
            start = 0
        payload = b"".join(buf16[start:])
        duration = len(payload) / FRAME_RATE / 2  # int16 = 2 bytes

        out_dir = out_dir or Path("/tmp/openclaw-voice-bridge")
        out_dir.mkdir(parents=True, exist_ok=True)
        wav_path = out_dir / f"utt-{int(time.time()*1000)}.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(FRAME_RATE)
            wf.writeframes(payload)
        return CaptureResult(wav_path, duration, len(recent), ended_by, peak_rms)
