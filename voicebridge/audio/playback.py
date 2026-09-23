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


def _resolve_target_rate(dev):
    """Native sample rate to resample to before playback.

    HDMI/TV sinks reject Piper's native 22050 Hz through PortAudio's ALSA
    hostapi (``paInvalidSampleRate`` -9997) while accepting 44100/48000 — that
    is why the agent "stopped working" the moment the OS output was switched to
    the TV, while USB headsets/aggregates (which rate-convert via PipeWire)
    still played fine. So even on the follow-the-default-sink path (``dev`` is
    None) we must resample to the default output device's native rate instead
    of sending 22050 raw. Returns 0 (no resample) if it cannot be determined.

    A ``pulse``/``pipewire`` aggregate returns 0: PipeWire/Pulse converts ANY
    source rate to the active sink at high quality, so resampling in Python
    here would only add artifacts (the "robotic voice").
    """
    import sounddevice as _sd
    try:
        if dev is not None:
            if _sd.query_devices()[dev]["name"].lower() in ("pulse", "pipewire"):
                return 0   # aggregate: play at the source rate, let PipeWire convert
            return int(_sd.query_devices()[dev].get("default_samplerate") or 0)
        out = _sd.default.device
        if not isinstance(out, int):
            # list / tuple / sounddevice._InputOutputPair: [input, output]
            try:
                out = out[1]
            except Exception:
                out = None
        if out is None:
            return 0
        return int(_sd.query_devices()[out].get("default_samplerate") or 0)
    except Exception:
        return 0


def _aggregate_output_index():
    """Follow-default output: prefer the pulse/pipewire aggregate index.

    The aggregate accepts ANY source rate and converts to the active sink via
    PipeWire/Pulse at high quality, so Piper's 22050 Hz plays on an HDMI/TV
    sink (raw ALSA rejects it with -9997) and on headphones without a Python
    resample. Returns None on a bare-ALSA system — ``_play_try`` then uses the
    raw default device with native-rate resampling.
    """
    import sounddevice as _sd
    try:
        for i, d in enumerate(_sd.query_devices()):
            if d["name"].lower() in ("pulse", "pipewire") \
                    and d["max_output_channels"] > 0:
                return i
    except Exception:
        pass
    return None


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
        """Resolve the output device. Priority: nothing pinned (system default
        sink) > name-substring match > explicit index (if it has output
        channels) > heuristic scan. This stops a shifting device index from
        silently picking a 0-output device."""
        import sounddevice as _sd
        # 0) NOTHING PINNED -> follow the SYSTEM DEFAULT SINK through the
        # pulse/pipewire aggregate (PipeWire converts any rate at high quality,
        # so both headphones and HDMI/TV play Piper's 22050 Hz). A bare-ALSA
        # system returns None and _play_try resamples to the raw default
        # device's native rate instead. The old heuristic scan once preferred a
        # silent motherboard ALC892 line-out over the HyperX USB headset, so
        # beeps and TTS "played" successfully into a dead port — PTT felt
        # completely broken. Never guess a port.
        if not self.device and not self.device_name:
            return _aggregate_output_index()
        try:
            devs = _sd.query_devices()
        except Exception:
            return None
        # 1) name substring (most robust)
        if self.device_name:
            for i, d in enumerate(devs):
                if self.device_name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                    return i
            # Pinned name matched NOTHING (e.g. the headset is routed purely
            # inside PipeWire and never shows up as an ALSA hw device).
            # NEVER fall back to heuristic scanning here: that is exactly how
            # audio ended up on a silent motherboard line-out while the real
            # sink was the PipeWire default. Follow the system default instead.
            _log("playback.pin_unmatched", name=self.device_name,
                 hint="using system default sink")
            return None
        # 2) explicit index, only if it can actually output
        idx = self.device
        try:
            idx = int(idx) if idx is not None else None
        except (ValueError, TypeError):
            idx = None
        if idx is not None and 0 <= idx < len(devs) and devs[idx]["max_output_channels"] > 0:
            return idx
        return None

    def play(self, wav_path: Path | str, name: str = "", interruptible: bool = True,
             on_start=None, on_finish=None) -> bool:
        """Blocking playback of a WAV. Returns False if interrupted.

        Tries the pinned device first; if it FAILS to open (e.g. "Device
        unavailable" from a slow-to-handshake USB headset), it retries on the
        system default sink so audio is never silently dropped.
        """
        # Single audio owner: if the Qubit service is running it owns the ALSA
        # device. Opening it from a second process crashes libasound
        # (snd_pcm_poll_descriptors_revents), so a CLI command defers instead.
        from .devlock import is_service_owner, service_holds_audio
        if not is_service_owner() and service_holds_audio():
            _log("playback.deferred_service_running",
                 hint="the Qubit service owns the audio device; CLI audio skipped")
            return False
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
                if finished is None:
                    _log("playback.failed", name=name or "",
                         hint="no output device could be opened; check the "
                              "system default sink (e.g. HDMI/TV)")
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
        target_sr = _resolve_target_rate(dev)
        try:
            with wave.open(str(wav_path), "rb") as wf:
                sr = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
                nch = wf.getnchannels()
            audio = np.frombuffer(frames, dtype=np.int16).reshape(-1, nch)
            if target_sr and sr != target_sr:
                audio = _resample(audio, sr, target_sr)
                sr = target_sr
            # Bounded retry: PipeWire can transiently fail the very first
            # output open with "Protocol error [ALSA error -71]" (EPROTO) —
            # a retry on the SAME default sink normally succeeds.
            played = False
            _last = None
            for _attempt in range(3):
                try:
                    _sd.play(audio, sr, blocking=False, device=dev)
                    played = True
                    break
                except Exception as exc:
                    _last = exc
                    import time as _t
                    _t.sleep(0.2)
            if not played:
                _log("playback.device_unavailable", device=str(dev),
                     error=str(_last)[:120])
                return None
            if on_start:
                on_start()
            self._started_at = time.time()
            finished = True
            # Watchdog: never let playback block a request forever (page freeze).
            # Exit EARLY the moment the stream flips active->inactive (finished);
            # bail fast if the device never becomes active so the caller can
            # fall back to the default sink. Before this early-exit, EVERY clip
            # paid its full duration+2.0s here — an 80ms beep stalled PTT ~2.2s,
            # and the end-beep then sd.stop()'d the still-"playing" start-beep.
            dur_s = len(audio) / sr if sr else 0.0
            t_start = time.time()
            deadline = t_start + max(dur_s + 0.35, 0.6)
            wedged_deadline = t_start + 1.5   # never became active -> bail
            ever_active = False
            while not self._stop.is_set() and time.time() < deadline:
                try:
                    st = sd.get_stream()
                    if st is not None and st.active:
                        ever_active = True
                    elif ever_active:
                        break               # finished playing — done, no stall
                    elif time.time() > wedged_deadline:
                        break               # never became active (wedged) -> bail
                except Exception:
                    break
                time.sleep(0.02)
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
