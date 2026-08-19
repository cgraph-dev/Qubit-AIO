"""Microphone capture with hold-to-talk semantics + VAD endpointing.

Records from the configured input device (or system default), resamples to
16 kHz mono float32, and returns utterance WAVs. The hotkey/PTT state machine
lives in the orchestrator; this module provides the raw capture primitive:

    mic = MicCapture(device=None)
    with mic.open():
        wav_path = mic.record_utterance(stop_requested, barge_in=None)

`stop_requested` is a threading.Event the caller sets to end capture early
(hotkey release). `barge_in`, when given, is a BargeInDetector fed the same
frames so speech during assistant playback can be detected.
"""
from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from .vad import FRAME_RATE, FRAME_SIZE, Endpointer, pcm16, resample_linear


@dataclass
class CaptureResult:
    wav_path: Optional[Path]
    duration_s: float
    frames: int
    ended_by: str  # "silence" | "stop_requested" | "max_duration" | "cancelled"


class MicCapture:
    def __init__(self, device: Optional[int] = None, sample_rate: int = 48000,
                 channels: int = 1, blocksize: int = 480,
                 endpointer: Optional[Endpointer] = None) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.endpointer = endpointer or Endpointer()
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

    def record_utterance(self, stop_requested: threading.Event,
                         barge_in=None, out_dir: Optional[Path] = None,
                         pre_roll_ms: int = 200) -> CaptureResult:
        """Record until silence endpoint, stop_requested, or max duration."""
        if self._stream is None:
            raise RuntimeError("mic not open; call open() first")
        ep = self.endpointer
        ep.reset()
        if barge_in is not None:
            barge_in.reset()

        buf16: list[bytes] = []      # 30 ms int16 frames (final mix for ASR)
        voiced_buf16: list[bytes] = []  # frames while in 'speech' state
        pre_roll_frames = max(1, pre_roll_ms // 30)
        recent: list[bytes] = []
        ended_by = "silence"
        t0 = time.time()

        while True:
            data, _ = self._stream.read(self.blocksize)  # float32 (n, ch)
            if self.channels > 1:
                data = data.mean(axis=1, keepdims=True)
            mono = data[:, 0]
            mono16 = resample_linear(mono, self.sample_rate, FRAME_RATE)
            # split into 30 ms frames
            n_full = len(mono16) // FRAME_SIZE
            for i in range(n_full):
                frame = mono16[i * FRAME_SIZE:(i + 1) * FRAME_SIZE]
                raw = pcm16(frame)
                if barge_in is not None:
                    barge_in.add_frame(raw)
                if stop_requested.is_set():
                    if ep.state == "speech":
                        # Hotkey released mid-speech: allow a short tail for the
                        # last word, then finish.
                        ended_by = "stop_requested"
                        break
                    if ep.state == "idle":
                        ended_by = "cancelled"
                        break
                recent.append(raw)
                if len(recent) > pre_roll_frames:
                    recent.pop(0)
                state = ep.add_frame(raw)
                buf16.append(raw)
                if state == "speech":
                    voiced_buf16.append(raw)
            if stop_requested.is_set() and ended_by != "":
                break
            if ep.state == "done":
                break
            if time.time() - t0 > ep.max_utterance_s + 1.0:
                ended_by = "max_duration"
                break

        if ended_by == "cancelled" or not voiced_buf16:
            return CaptureResult(None, 0.0, 0, ended_by)

        # Keep pre-roll context before the first voiced frame for ASR stability.
        start = max(0, len(buf16) - len(voiced_buf16) - pre_roll_frames)
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
        return CaptureResult(wav_path, duration, len(voiced_buf16), ended_by)
