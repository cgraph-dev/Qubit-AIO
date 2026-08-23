"""Audio playback with interrupt support (stop speaking / barge-in)."""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from ..logging_setup import log_event


def _log(event: str, **fields) -> None:
    try:
        log_event(event, **fields)
    except Exception:
        pass


def _resample(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Linear-interpolation resample float/int audio to a new sample rate."""
    if from_sr == to_sr or from_sr <= 0 or to_sr <= 0:
        return audio
    is_int = np.issubdtype(audio.dtype, np.integer)
    x = audio.astype(np.float32)
    n_out = int(round(len(x) * to_sr / from_sr))
    # per-sample input positions
    idx = np.linspace(0, len(x) - 1, n_out)
    y = np.zeros((n_out,) + x.shape[1:], dtype=np.float32)
    i0 = np.floor(idx).astype(int)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = (idx - i0).astype(np.float32)
    y = x[i0] * (1 - frac[..., None]) + x[i1] * (frac[..., None])
    if is_int:
        y = np.clip(y, -1, 1) * 32767
        return y.astype(np.int16)
    return y


class Playback:
    """Plays WAV files through the default output (or a pinned device); can be
    stopped at any time. `device` is a sounddevice/PortAudio device name or index;
    when set it overrides the (GNOME-pinned) default sink so the bridge always
    plays to the intended output, e.g. your headphones."""

    def __init__(self, device: Optional[str] = None, device_name: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._playing = False
        self._started_at = 0.0
        self._name = ""
        self.device = device
        self.device_name = device_name

    def _resolve_device(self):
        """Resolve the output device. Priority: explicit index (if it has output
        channels) > name-substring match > a real 8+-channel output > None. This
        stops a shifting device index from silently picking a 0-output device."""
        import sounddevice as _sd
        try:
            devs = _sd.query_devices()
        except Exception:
            return None
        # 1) name substring (most robust)
        if self.device_name:
            for i, d in enumerate(devs):
                if self.device_name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                    return i
        # 2) explicit index, only if it can actually output
        idx = self.device
        try:
            idx = int(idx) if idx is not None else None
        except (ValueError, TypeError):
            idx = None
        if idx is not None and 0 <= idx < len(devs) and devs[idx]["max_output_channels"] > 0:
            return idx
        # 3) fall back to a REAL output, avoiding HDMI/TV (NVidia) and virtual sinks:
        #    prefer USB/analog headsets (2 ch) over 8-ch HDMI (a display, not a speaker).
        bad = ("default", "pipewire", "pulse", "hdmi", "nvidia")
        cands = [i for i, d in enumerate(devs)
                 if d["max_output_channels"] > 0 and not any(b in d["name"].lower() for b in bad)]
        if cands:
            return cands[0]
        # 4) last resort: any output device at all (including HDMI)
        for i, d in enumerate(devs):
            if d["max_output_channels"] > 0:
                return i
        return None

    def play(self, wav_path: Path | str, name: str = "", interruptible: bool = True,
             on_start=None, on_finish=None) -> bool:
        """Blocking playback of a WAV. Returns False if interrupted.

        Tries the pinned device first; if it FAILS to open (e.g. "Device
        unavailable" from a slow-to-handshake USB headset), it retries on the
        system default sink so audio is never silently dropped.
        """
        with self._lock:
            if self._playing:
                # Interrupt whatever is playing first (we are the only speaker).
                sd.stop()
                self._stop.set()
            self._stop = threading.Event()
            self._playing = True
            self._name = name
        try:
            finished = self._play_try(wav_path, on_start)
            if finished is None:   # pinned device failed -> fall back to default
                finished = self._play_try(wav_path, on_start, force_default=True)
            return finished if finished is not None else False
        finally:
            with self._lock:
                self._playing = False
            if on_finish:
                on_finish(finished if finished is not None else False)

    def _play_try(self, wav_path, on_start, force_default=False):
        """Return True=finished, False=interrupted, None=pinned open failed."""
        import sounddevice as _sd
        dev = None if force_default else self._resolve_device()
        target_sr = None
        if dev is not None:
            try:
                target_sr = int(_sd.query_devices()[dev].get("default_samplerate") or 0)
            except Exception:
                target_sr = None
        try:
            with wave.open(str(wav_path), "rb") as wf:
                sr = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
                nch = wf.getnchannels()
            audio = np.frombuffer(frames, dtype=np.int16).reshape(-1, nch)
            if target_sr and sr != target_sr:
                audio = _resample(audio, sr, target_sr)
                sr = target_sr
            try:
                _sd.play(audio, sr, blocking=False, device=dev)
            except Exception as exc:
                _log("playback.device_unavailable", device=str(dev), error=str(exc)[:120])
                return None
            if on_start:
                on_start()
            self._started_at = time.time()
            finished = True
            # Watchdog: never let playback block a request forever (page freeze).
            # Wait while the stream is alive OR up to duration+grace. If a wedged
            # device never becomes active, bail early and report failure so the
            # caller retries on the default device.
            import math
            dur_s = len(audio) / sr if sr else 0.0
            deadline = time.time() + max(dur_s, 0.2) + 2.0
            ever_active = False
            no_play_grace = time.time() + 3.0
            while not self._stop.is_set() and time.time() < deadline:
                try:
                    st = sd.get_stream()
                    if st is not None and st.active:
                        ever_active = True
                    elif not ever_active and time.time() > no_play_grace:
                        break   # never became active (wedged device) -> bail
                except Exception:
                    break
                time.sleep(0.05)
            # If it never produced an active stream, it didn't actually play -> report
            # failure so the caller falls back to a different device.
            if not ever_active and not self._stop.is_set():
                return None
            finished = not self._stop.is_set()
            try:
                sd.stop()
            except Exception:
                pass
            try:
                sd.wait()
            except Exception:
                pass
            return finished
        except Exception as exc:
            _log("playback.play_error", error=str(exc)[:120])
            return None

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
