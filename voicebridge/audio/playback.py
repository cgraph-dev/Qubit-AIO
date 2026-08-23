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
    """Plays WAV files through the default output (or a pinned device); can be
    stopped at any time. `device` is a sounddevice/PortAudio device name or index;
    when set it overrides the (GNOME-pinned) default sink so the bridge always
    plays to the intended output, e.g. your headphones."""

    def __init__(self, device: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._playing = False
        self._started_at = 0.0
        self._name = ""
        self.device = device

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
        # Resolve a device each play: if the pinned device is gone, fall back to
        # None (system default) so we never crash.
        dev = self.device
        if dev is not None:
            try:
                dev = int(dev)
            except (TypeError, ValueError):
                pass
            try:
                import sounddevice as _sd
                # Validate the device exists; if not, fall back to default.
                out_devs = [d for d in _sd.query_devices() if d["max_output_channels"] > 0]
                if not (0 <= dev < len(_sd.query_devices())):
                    dev = None
            except Exception:
                dev = None
        try:
            with wave.open(str(wav_path), "rb") as wf:
                sr = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).reshape(-1, wf.getnchannels())
            sd.play(audio, sr, blocking=False, device=dev)
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
