"""Audio playback with interrupt support (stop speaking / barge-in)."""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd


class Playback:
    """Plays WAV files through the default output; can be stopped at any time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._playing = False
        self._started_at = 0.0
        self._name = ""

    def play(self, wav_path: Path | str, name: str = "", interruptible: bool = True,
             on_start=None, on_finish=None) -> bool:
        """Blocking playback of a WAV. Returns False if interrupted."""
        with self._lock:
            if self._playing:
                # Interrupt whatever is playing first (we are the only speaker).
                sd.stop()
                self._stop.set()
            self._stop = threading.Event()
            self._playing = True
            self._name = name
        try:
            with wave.open(str(wav_path), "rb") as wf:
                sr = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).reshape(-1, wf.getnchannels())
            sd.play(audio, sr, blocking=False)
            if on_start:
                on_start()
            self._started_at = time.time()
            finished = True
            while sd.get_stream().active:
                if self._stop.is_set():
                    sd.stop()
                    finished = False
                    break
                time.sleep(0.02)
        finally:
            with self._lock:
                self._playing = False
            if on_finish:
                on_finish(finished)
        return finished

    def stop(self) -> None:
        with self._lock:
            if not self._playing:
                return
            self._stop.set()

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._playing

    @property
    def elapsed(self) -> float:
        with self._lock:
            return time.time() - self._started_at if self._playing else 0.0

    def wait_done(self, timeout: Optional[float] = None) -> bool:
        t0 = time.time()
        while self.playing:
            if timeout is not None and time.time() - t0 > timeout:
                return False
            time.sleep(0.05)
        return True
