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

import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from .vad import FRAME_MS, FRAME_RATE, Endpointer, FrameSplitter, resample_linear


@dataclass
class CaptureResult:
    wav_path: Optional[Path]
    duration_s: float
    frames: int
    ended_by: str  # "silence" | "stop_requested" | "max_duration" | "cancelled"
    peak_rms: float = 0.0


def pin_pulse_default_source(device_name: str | None) -> str | None:
    """Pin the Pulse default SOURCE to the configured mic name.

    The capture path opens the ``pulse``/default aggregate, which routes to
    Pulse's default source. If that default source drifts (monitor, dead
    codec, another app) capture yields zeros or a device error. Bounded,
    non-fatal; returns the matched Pulse source name or None. Requires
    ``pactl`` (standard on PipeWire/Pulse desktops); failure is a no-op so
    the bridge keeps running (typed DEGRADED, never a crash).
    """
    if not device_name:
        return None
    try:
        import subprocess
        import re as _re
        src = subprocess.run(["pactl", "list", "sources", "short"],
                             capture_output=True, text=True, timeout=6)
        if src.returncode != 0:
            return None
        # Normalize both sides so PortAudio display names ("Scarlett 2i2 4th
        # Gen: USB Audio") match pactl names ("..._Scarlett_2i2_4th_Gen_..."):
        # case/space/underscore/punctuation-insensitive token comparison.
        # Generic device words are ignored so only the identifying tokens must
        # match (the pactl name usually drops "audio"/"usb" as a literal word).
        _GENERIC = {"audio", "usb", "digital", "analog", "input", "output",
                    "monitor", "capture", "device", "interface", "usbaudio",
                    "multichannel", "hw"}
        _sig = lambda t: bool(t) and t not in _GENERIC and not t.isdigit()
        want_tokens = [t for t in _re.split(r"[\s_:.,()\-]+",
                                            device_name.lower())
                       if _sig(t)]
        match = None
        for line in src.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[1]   # column 0 is the numeric source index
            # ONLY input sources: monitors are alsa_output.*.monitor loopbacks
            # (the output monitor sorts BEFORE its input source and
            # delivers pure zeros to the capture path).
            if not name.startswith("alsa_input."):
                continue
            have = [t for t in _re.split(r"[\s_:.,()\-]+", name.lower())
                    if _sig(t)]
            # every significant token of the configured name must appear in
            # the pactl source name (order-insensitive, punctuation-insensitive)
            if all(t in have for t in want_tokens):
                match = name
                break
        if not match:
            return None
        cur = subprocess.run(["pactl", "info"], capture_output=True,
                             text=True, timeout=6)
        if match not in (cur.stdout or ""):
            subprocess.run(["pactl", "set-default-source", match],
                           capture_output=True, timeout=6)
        return match
    except Exception:
        return None


def resolve_input_device(name: str | None) -> int | None:
    """Resolve a mic by (sub)string to a sounddevice input index.

    The system default INPUT can silently point at a monitor/loopback or the
    dead motherboard codec after device churn — capture then yields pure zeros
    while the browser (which picks its own device) works fine. Pinning by name
    fixes it the same way playback.device_name does for output.

    Packet 5C/release fix: NEVER return a raw ALSA ``(hw:N,M)`` index for a
    Pulse-managed USB mic. On PipeWire/Pulse systems those raw indices fail
    to open via PortAudio with -9993/-9985 ("Illegal combination of I/O
    devices" / "Device unavailable") even though the SAME device works
    perfectly through the ``pulse``/``default`` aggregate. Resolution here
    therefore prefers the stable ``pulse`` aggregate and falls back to the
    default source (None) when only a raw hw device matches.
    """
    if not name:
        return None
    try:
        import sounddevice as _sd
        devs = _sd.query_devices()
        needle = name.lower()
        # Does the name match any PHYSICAL (raw ALSA hw) input? If so this is
        # a Pulse-managed device whose raw index is not openable via
        # PortAudio; route capture through the stable aggregate instead.
        raw_hit = any(
            needle in d["name"].lower() and "(hw:" in d["name"]
            and d["max_input_channels"] > 0
            for d in devs)
        if raw_hit:
            for i, d in enumerate(devs):
                if d["name"].lower() == "pulse" and d["max_input_channels"] > 0:
                    return i
            for i, d in enumerate(devs):
                if d["name"].lower() in ("pipewire", "default") \
                        and d["max_input_channels"] > 0:
                    return i
            # only raw hw available -> fall back to the default source
            return None
        for i, d in enumerate(devs):
            if (needle in d["name"].lower()
                    and d["max_input_channels"] > 0):
                return i
    except Exception:
        pass
    return None


MIC_SELFCHECK_HINTS = {
    "UNAVAILABLE": "microphone could not be opened — check the configured mic "
                   "and that no other app holds it",
    "SILENT": "microphone delivers ~no signal — enable 48V phantom power, raise "
              "the Scarlett input gain, and check the USB port "
              "(docs/AUDIO_SETUP.md)",
    "DEGRADED": "microphone is very quiet — raise the input gain / check the "
                "cable",
    "HEALTHY": "microphone signal chain OK",
}


def mic_selfcheck(cfg, *, seconds: float = 1.0) -> Dict[str, Any]:
    """Bounded live mic-signal self-check (ONE input stream at a time).

    Opens the SAME capture route the bridge uses (default aggregate at
    ``mic.sample_rate``/``mic.channels``) for ~``seconds`` and measures the
    signal level. A working input chain has a measurable noise floor; a
    silent/failed one is near digital zero or fails to open.

    IMPORTANT: the Scarlett tolerates ONE InputStream at a time (two streams
    corrupted PortAudio). Call this only while NO other input stream on the
    device is open (the orchestrator runs it before the barge stream starts).

    Returns a typed state (never raises, bounded by wall clock):

    - ``UNAVAILABLE`` — the stream could not be opened (device missing or held);
    - ``SILENT``      — opened but ~digital zero (48V phantom / gain / USB);
    - ``DEGRADED``    — very quiet (device alive, low level);
    - ``HEALTHY``     — clear signal chain.
    """
    import sounddevice as sd
    rate = int(cfg.get("mic", "sample_rate", default=48000))
    channels = int(cfg.get("mic", "channels", default=1))
    device = cfg.get("mic", "device", default=None)
    silent_floor = float(cfg.get("mic", "selfcheck_silent_floor", default=1e-5))
    healthy_floor = float(cfg.get("mic", "selfcheck_healthy_floor", default=1e-3))
    block = max(1, rate // 20)
    try:
        peak = 0.0
        samples = 0
        t0 = time.time()
        with sd.InputStream(samplerate=rate, channels=channels,
                            device=device, dtype="float32",
                            blocksize=block) as stream:
            while time.time() - t0 < float(seconds):
                data, _ = stream.read(block)
                mono = data[:, 0] if data.ndim > 1 else data
                if mono.size:
                    peak = max(peak, float(np.abs(mono).max()))
                    samples += int(mono.size)
        if peak < silent_floor:
            state = "SILENT"
        elif peak < healthy_floor:
            state = "DEGRADED"
        else:
            state = "HEALTHY"
        return {
            "state": state, "peak": round(peak, 6),
            "seconds": round(float(seconds), 2),
            "channels": channels, "sample_rate": rate, "samples": samples,
            "hint": MIC_SELFCHECK_HINTS[state],
        }
    except Exception as exc:
        return {"state": "UNAVAILABLE", "error": str(exc)[:160],
                "hint": MIC_SELFCHECK_HINTS["UNAVAILABLE"]}


class MicCapture:
    def __init__(self, device: Optional[int] = None, sample_rate: int = 48000,
                 channels: int = 2, blocksize: int = 480,
                 endpointer: Optional[Endpointer] = None,
                 gain: float = 4.0, min_rms: float = 0.0008,
                 min_speech_s: float = 0.15,
                 normalize_target: float = 0.35) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.endpointer = endpointer or Endpointer()
        self.gain = gain
        self.min_rms = min_rms
        self.min_speech_s = min_speech_s
        self.normalize_target = normalize_target  # peak-normalize pre-amp before ASR
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
        """Downmix to mono taking the loudest/best channel (robust to channel maps).

        NB: pick ONE channel by total energy (not per-sample max-abs * ch0 sign,
        which phase-corrupts frames when the mic isn't on channel 0)."""
        if data.ndim == 1 or data.shape[1] == 1:
            return data[:, 0] if data.ndim == 2 else data
        # energy per channel -> use the channel with the most energy
        chan_energy = np.sqrt((data.astype(np.float32) ** 2).mean(axis=0))
        best = int(np.argmax(chan_energy)) if chan_energy.size else 0
        return data[:, best]

    def record_utterance(self, stop_requested: threading.Event,
                         barge_in=None, out_dir: Optional[Path] = None,
                         pre_roll_ms: int = 200,
                         silence_timeout_s: Optional[float] = None) -> CaptureResult:
        """Record until silence endpoint, stop_requested, or max duration.

        The audio from press→release is ALWAYS saved when it has any audible
        energy (RMS >= min_rms) — VAD is only used to trim silence and to end
        hands-free captures via the silence timeout.

        `silence_timeout_s` overrides the Endpointer's trailing-silence window
        for THIS capture only (toggle/hands-free gets a longer end-of-turn).
        """
        if self._stream is None:
            raise RuntimeError("mic not open; call open() first")
        ep = self.endpointer
        ep.reset(silence_timeout_s=silence_timeout_s)
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
        # Don't discard audible speech just because VAD missed it:
        # quiet Focusrite speech (peak ~0.006) can have voiced_s=0 yet still be
        # perfectly transcribable — ASR is the final judge, not VAD.
        audible = peak_rms >= max(self.min_rms * 3, 0.0015)
        if not buf16 or (ended_by == "cancelled" and peak_rms < self.min_rms) \
                or (voiced_s < self.min_speech_s and not audible):
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

        # Peak-normalize pre-amp: a quiet/variable mic (HyperX) makes ASR drop
        # words or hallucinate. Scale the clip so its peak hits a target fraction
        # of full scale — consistent, non-clipping level regardless of raw gain,
        # fixed hardware gain knob, or autogain swings.
        try:
            _pcm = np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32767.0
            _peak = float(np.max(np.abs(_pcm))) if _pcm.size else 0.0
            _target = float(getattr(self, "normalize_target", 0.35)) or 0.35
            if _peak > 0.001 and _peak < 0.98:
                _scale = _target / _peak
                _pcm = np.clip(_pcm * _scale, -1.0, 1.0)
                payload = (_pcm * 32767).astype(np.int16).tobytes()
        except Exception:
            pass

        out_dir = out_dir or Path("/tmp/qubit-voicebridge")
        out_dir.mkdir(parents=True, exist_ok=True)
        wav_path = out_dir / f"utt-{int(time.time()*1000)}.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(FRAME_RATE)
            wf.writeframes(payload)
        return CaptureResult(wav_path, duration, len(recent), ended_by, peak_rms)
