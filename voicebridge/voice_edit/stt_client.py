"""Streaming STT client for the desktop helper.

Connects to the ONE existing authenticated STT WebSocket
(``/agent/api/audio/stt/ws``) using a short-lived single-use ticket minted over
the authenticated HTTP route. Audio is captured from a PulseAudio/PipeWire
client (``parec``) so the helper never opens the raw PortAudio device that the
running service owns -- no second ASR authority, no second audio device owner.

The class is fully injectable for tests (``opener``/``mic``), so no test opens a
socket, a microphone, or a subprocess.
"""
from __future__ import annotations

import asyncio
import json
import struct
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional


class SttError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


class ParecMic:
    """PulseAudio/PipeWire capture via ``parec`` (safe concurrent client).

    Emits mono 16-bit little-endian PCM at ``sample_rate``. Never logs audio.
    """

    def __init__(self, *, sample_rate: int = 16000, frame_ms: int = 300,
                 device: str = "", gain: float = 12.0,
                 target_peak: float = 0.5, agc: bool = True,
                 popen: Optional[Callable[..., Any]] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None) -> None:
        self.sample_rate = int(sample_rate)
        self.frame_ms = int(frame_ms)
        self.device = device
        self.agc = bool(agc)
        # Automatic per-frame gain, mirroring the orchestrator's capture chain:
        # a quiet USB interface (~0.003 RMS) otherwise reaches the recognizer
        # far below the level ASR expects, which is why the same mic sounds
        # great in Chrome (which applies its own AGC) but poor here.
        self.gain = max(1.0, float(gain))
        self.target_peak = max(0.05, min(float(target_peak), 0.95))
        self._popen = popen or subprocess.Popen
        self._which = which
        self._proc: Optional[Any] = None
        # Speech-aware automatic gain (browser getUserMedia parity): lift quiet
        # speech to a target RMS, but never amplify the noise floor between
        # words (the old peak-normalizer boosted near-silence by ``gain``, which
        # fed the recognizer room noise and cost words on soft speech).
        self.target_rms = max(0.03, min(float(target_peak) * 0.25, 0.15))
        self._agc = 1.0
        self._noise_rms = 0.0015
        # Capture format is discovered from the source's real spec before
        # starting; requesting a format the source does not natively support
        # (e.g. s16/16k on a s32/48k/4ch device) makes PipeWire emit corrupted
        # audio, which every ASR engine then decodes as hallucinated text.
        self._native_rate = int(sample_rate)
        self._native_channels = 1
        self._native_width = 2          # bytes per sample (s16)
        self._frame_bytes = max(2, int(self._native_rate * frame_ms / 1000) *
                                self._native_channels * self._native_width)

    def available(self) -> bool:
        import shutil
        which = self._which or shutil.which
        return bool(which("parec") or which("arecord") or which("pw-record"))

    def _probe_source_format(self) -> None:
        """Resolve the source's real sample format from PulseAudio/PipeWire."""
        import shutil
        import subprocess
        if not shutil.which("pactl"):
            return
        try:
            name = self.device
            if not name:
                out = subprocess.run(["pactl", "get-default-source"],
                                     capture_output=True, text=True,
                                     timeout=5).stdout.strip()
                name = out
            if not name:
                return
            info = subprocess.run(["pactl", "list", "sources"],
                                  capture_output=True, text=True,
                                  timeout=5).stdout
            block = ""
            for part in info.split("Source #"):
                if f"Name: {name}\n" in part:
                    block = part
                    break
            spec = ""
            for line in block.splitlines():
                if "Sample Specification" in line:
                    spec = line.split(":", 1)[1].strip()
                    break
            # e.g. "s32le 4ch 48000Hz"
            toks = spec.split()
            for tok in toks:
                if tok.endswith("ch"):
                    self._native_channels = int(tok[:-2])
                elif tok.endswith("Hz"):
                    self._native_rate = int(tok[:-2])
            widths = {"s16le": 2, "s24le": 3, "s32le": 4, "float32le": 4}
            fmt = next((f for f in toks if f in widths), None)
            if fmt:
                # parec requests s16le; s32 sources read as s16 contain valid
                # low bytes, so we keep s32 and down-shift in _to_mono_16k.
                self._native_width = widths[fmt]
                self._capture_s32 = fmt in ("s32le", "float32le")
        except Exception:
            pass

    def _sync_frame_bytes(self) -> None:
        """Recompute the read size from the probed stream format.

        Reads pull raw stream bytes, so the size must match the real format
        (rate × channels × width), not the assumed 16k/mono/s16 the
        constructor used. Called from :meth:`start` after the probe.
        """
        width = 4 if getattr(self, "_capture_s32", False) else 2
        self._frame_bytes = max(
            2, int(self._native_rate * self.frame_ms / 1000)
            * self._native_channels * width)

    def start(self) -> None:
        import shutil
        which = self._which or shutil.which
        if which("parec"):
            self._probe_source_format()
            self._sync_frame_bytes()
            # Recompute AFTER the probe: the real stream format (e.g. the
            # Scarlett's s32/4ch/48k) differs from the assumed 16k/mono/s16
            # the constructor used. Without this, reads pull ~12.5 ms while
            # every consumer (AGC, endpointer, KWS) assumes frame_ms — levels
            # pump, utterances fragment into ~40 ms pieces, and the spotter
            # goes deaf on real mic audio while working on clean files.
            self._sync_frame_bytes()
            fmt = "s32le" if getattr(self, "_capture_s32", False) else "s16le"
            argv = ["parec", f"--format={fmt}", f"--rate={self._native_rate}",
                    f"--channels={self._native_channels}",
                    "--latency-msec=100"]
            if self.device:
                argv.append(f"--device={self.device}")
        elif which("pw-record"):
            argv = ["pw-record", "--format=s16", f"--rate={self.sample_rate}",
                    "--channels=1", "-"]
        elif which("arecord"):
            argv = ["arecord", "-q", "-f", "S16_LE", "-r", str(self.sample_rate),
                    "-c", "1", "-t", "raw"]
        else:
            raise SttError("STT_MIC_UNAVAILABLE",
                           "no parec/pw-record/arecord on PATH")
        try:
            self._proc = self._popen(argv, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        except OSError as exc:
            raise SttError("STT_MIC_UNAVAILABLE", str(exc)[:160])
        if self._proc is None or self._proc.stdout is None:
            raise SttError("STT_MIC_UNAVAILABLE", "could not start the capture tool")

    def read_frame(self, timeout: float = 0.5) -> Optional[bytes]:
        if self._proc is None or self._proc.stdout is None:
            return None
        import select
        try:
            r, _, _ = select.select([self._proc.stdout], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not r:
            return None
        try:
            data = self._proc.stdout.read(self._frame_bytes)
        except (OSError, ValueError):
            return None
        if not data:
            return None
        data = self._to_mono_16k(data)
        return self._agc_process(data) if self.agc else self._apply_gain(data)

    def _agc_process(self, data: bytes) -> bytes:
        """Speech-aware AGC + noise-floor protection (browser-like capture).

        Tracks an adaptive noise floor; speech frames are lifted toward
        ``target_rms`` (bounded by ``gain``), non-speech frames are never
        amplified (gain <= 1) so silence/noise does not compete with speech at
        the recognizer. State is per-frame so the level stays stable across an
        utterance.
        """
        try:
            import numpy as np
            a = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            if a.size == 0:
                return data
            rms = float(np.sqrt(np.mean(a * a)))
            # Adaptive noise floor: follow downward quickly, upward slowly.
            if rms < self._noise_rms:
                self._noise_rms = 0.8 * self._noise_rms + 0.2 * rms
            else:
                self._noise_rms = 0.995 * self._noise_rms + 0.005 * rms
            floor = max(self._noise_rms * 2.5, 0.0015)
            if rms > floor:
                desired = self.target_rms / max(rms, 1e-5)
                desired = max(1.0, min(desired, self.gain))
                self._agc = 0.6 * self._agc + 0.4 * desired
                gain = self._agc
            else:
                # Between words: decay toward unity, never boost the floor.
                self._agc = 0.85 * self._agc + 0.15 * 1.0
                gain = min(self._agc, 1.0)
            out = np.clip(a * gain, -1.0, 1.0)
            return (out * 32767.0).astype("<i2").tobytes()
        except Exception:
            return data

    def _to_mono_16k(self, data: bytes) -> bytes:
        """Downmix to mono and resample the native capture format to 16 kHz."""
        if (self._native_rate == self.sample_rate and
                self._native_channels == 1 and
                not getattr(self, "_capture_s32", False)):
            return data
        try:
            import numpy as np
            # Read samples as signed integers at the native width. A 32-bit
            # interface streams full-scale int32, so shifting right by 16 maps
            # it to int16 range (values above ~1.0 are simply clipped).
            is_s32 = getattr(self, "_capture_s32", False)
            raw = np.frombuffer(data, dtype="<i4" if is_s32 else "<i2")
            # Normalize to int16 range: 32-bit ints are full-scale ±2^31,
            # 16-bit ±2^15 (a <<16 shift would overflow by 2^16).
            scale = 65536.0 if is_s32 else 1.0
            a = raw.astype(np.float64) / scale
            ch = self._native_channels
            if ch > 1:
                if a.size % ch:
                    a = a[: a.size - (a.size % ch)]
                m = a.reshape(-1, ch)
                # Pick the channel carrying the real signal (an interface maps
                # the mic to one channel, leaving the others at zero).
                energy = np.sqrt((m ** 2).mean(axis=0))
                a = m[:, int(np.argmax(energy))] if energy.size else m[:, 0]
            a = np.clip(a, -32768, 32767).astype(np.float32)
            if self._native_rate != self.sample_rate and a.size:
                n = int(round(a.size * self.sample_rate / self._native_rate))
                if n > 0:
                    a = np.interp(np.linspace(0, 1, n, endpoint=False),
                                  np.linspace(0, 1, a.size, endpoint=False), a)
            return np.clip(a, -32768, 32767).astype("<i2").tobytes()
        except Exception:
            return data

    def _apply_gain(self, data: bytes) -> bytes:
        """Normalize a frame toward ``target_peak`` (bounded by ``gain``)."""
        try:
            import numpy as np
            a = np.frombuffer(data, dtype="<i2").astype(np.float32)
            if a.size == 0:
                return data
            peak = float(np.max(np.abs(a))) / 32768.0
            if peak <= 0.0005:
                factor = self.gain  # near-silence: lift it so ASR sees something
            else:
                factor = min(self.gain, self.target_peak / peak)
            if factor <= 1.001:
                return data
            out = np.clip(a * factor, -32768, 32767).astype("<i2")
            return out.tobytes()
        except Exception:
            return data

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class BufferingMic:
    """Wrap a mic; record every frame read and expose the joined PCM.

    The stream still drives the live streaming socket (interim partials), while
    the recorded buffer is available for a whole-utterance accurate pass. The
    buffer is bounded: when it exceeds ``max_bytes`` the oldest bytes are
    dropped, so a long-lived stream cannot grow without limit.
    """

    def __init__(self, mic: Any, *, max_bytes: int = 16000 * 2 * 60) -> None:
        self._mic = mic
        self._max_bytes = max(0, int(max_bytes))
        self._buf = bytearray()
        self._lock = threading.Lock()

    def start(self) -> None:
        self._mic.start()

    def stop(self) -> None:
        self._mic.stop()

    def available(self) -> bool:
        try:
            return bool(self._mic.available())
        except Exception:
            return False

    def _record(self, frame: bytes) -> None:
        with self._lock:
            self._buf.extend(frame)
            if self._max_bytes and len(self._buf) > self._max_bytes:
                del self._buf[:len(self._buf) - self._max_bytes]

    def read_frame(self, timeout: float = 0.5) -> Optional[bytes]:
        frame = self._mic.read_frame(timeout)
        if frame:
            self._record(frame)
        return frame

    def pause_until(self, deadline: float) -> Optional[bytes]:
        """Keep draining/recording frames until ``deadline`` (monotonic).

        Returns the last frame read, or None if none arrived. Used to hold the
        capture open across a short gap without losing buffered audio.
        """
        last: Optional[bytes] = None
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            frame = self.read_frame(timeout=max(0.0, min(0.2, remaining)))
            if frame:
                last = frame
        return last

    @property
    def pcm(self) -> bytes:
        with self._lock:
            return bytes(self._buf)

    def reset(self) -> None:
        with self._lock:
            self._buf = bytearray()


class SttClient:
    """One streaming STT request against the existing STT WebSocket."""

    def __init__(self, *, url: str, ticket: str, opener: Optional[Callable] = None,
                 engine: str = "", sample_rate: int = 16000,
                 target_peak: float = 0.5) -> None:
        self.url = self._with_ticket(url, ticket)
        self.ticket = ticket
        self.engine = engine
        self.sample_rate = sample_rate
        # Configured capture level (``voice_edit.mic_target_peak``), clamped to
        # 0.05..0.95 like settings.py. Carried so the caller (wake) and the
        # streaming client share one value; frame leveling itself stays
        # mic-side (ParecMic AGC) and utterance-side (``_normalize_pcm``).
        # Default 0.5 preserves the historical behavior when not passed.
        try:
            self.target_peak = max(0.05, min(float(target_peak), 0.95))
        except Exception:
            self.target_peak = 0.5
        self._opener = opener
        self.partials: List[str] = []
        self.final_text = ""
        self.engine_reported = ""
        self.metrics: Dict[str, Any] = {}
        self.error: Optional[SttError] = None
        self.first_partial_ms: Optional[int] = None
        self.completed_at: Optional[int] = None

    @staticmethod
    def _with_ticket(url: str, ticket: str) -> str:
        """The single-use ticket is a query param; append it when absent."""
        if not ticket or "ticket=" in url:
            return url
        from urllib.parse import quote
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}ticket={quote(ticket, safe='')}"

    async def _connect(self):
        if self._opener is not None:
            return await self._opener(self.url)
        import websockets
        return await websockets.connect(self.url, max_size=1 << 21)

    async def run(self, mic: Any, stop_event: threading.Event, *,
                  on_partial: Optional[Callable[[str], None]] = None,
                  on_status: Optional[Callable[[str], None]] = None,
                  on_ready: Optional[Callable[[], None]] = None) -> str:
        started = time.monotonic()
        seq = 0
        request_id = f"voice-edit-{int(started * 1000)}"
        try:
            ws = await self._connect()
        except Exception as exc:
            self.error = SttError("STT_CONNECT_FAILED", str(exc)[:160])
            return ""
        try:
            await ws.send(json.dumps({
                "type": "start", "request_id": request_id,
                "format": "pcm_s16le", "sample_rate": self.sample_rate,
                "sequence": 0, **({"engine": self.engine} if self.engine else {})}))
            if on_status:
                on_status("listening")
            loop = asyncio.get_running_loop()
            sender_done = asyncio.Event()

            async def _sender():
                nonlocal seq
                try:
                    while not stop_event.is_set():
                        frame = await loop.run_in_executor(
                            None, mic.read_frame, 0.2)
                        if frame is None:
                            # A live mic returning None means "no frame yet"
                            # (Pulse idle). Only the hotkey release sets
                            # ``stop_event``; never finalize on a single gap.
                            continue
                        header = struct.pack("!I", seq)
                        await ws.send(header + frame)
                        seq += 1
                finally:
                    try:
                        await ws.send(json.dumps({"type": "stop",
                                                  "sequence": seq}))
                    except Exception:
                        pass
                    sender_done.set()

            send_task = asyncio.ensure_future(_sender())
            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                    except asyncio.TimeoutError:
                        break
                    except Exception:
                        break
                    msg = json.loads(raw if isinstance(raw, str) else
                                     raw.decode("utf-8", "replace"))
                    kind = msg.get("type", "")
                    if msg.get("engine") and not self.engine_reported:
                        self.engine_reported = str(msg["engine"])
                    if kind == "stt.started" and on_ready is not None:
                        # The server has opened the recognition session: now is
                        # the cue for the user to speak.
                        try:
                            on_ready()
                        except Exception:
                            pass
                    if kind == "stt.partial":
                        text = str(msg.get("text", "") or "")
                        if text:
                            if self.first_partial_ms is None:
                                self.first_partial_ms = int(
                                    (time.monotonic() - started) * 1000)
                            self.partials.append(text)
                            if on_partial:
                                on_partial(text)
                    elif kind == "stt.final":
                        self.final_text = str(msg.get("text", "") or "")
                    elif kind == "stt.completed":
                        if not self.final_text:
                            self.final_text = str(msg.get("final_text", "") or "")
                        self.metrics = msg.get("metrics") or {}
                        self.completed_at = int((time.monotonic() - started) * 1000)
                        break
                    elif kind == "stt.error":
                        self.error = SttError(str(msg.get("code", "STT_ERROR")),
                                              str(msg.get("detail", "")))
                        break
            finally:
                if not sender_done.is_set():
                    stop_event.set()
                    send_task.cancel()
                try:
                    await ws.close()
                except Exception:
                    pass
        except SttError:
            raise
        except Exception as exc:
            self.error = SttError("STT_CLIENT_ERROR", str(exc)[:160])
        return self.final_text


def transcribe_once(client: SttClient, mic: Any, stop_event: threading.Event,
                    *, on_partial: Optional[Callable[[str], None]] = None,
                    on_status: Optional[Callable[[str], None]] = None,
                    on_ready: Optional[Callable[[], None]] = None) -> str:
    """Run one STT request on a fresh asyncio loop (helper worker thread)."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            client.run(mic, stop_event, on_partial=on_partial,
                       on_status=on_status, on_ready=on_ready))
    finally:
        try:
            loop.close()
        except Exception:
            pass


def transcribe_accurate(cfg: Any, pcm: bytes, *,
                        sample_rate: int = 16000) -> tuple[str, str]:
    """Whole-utterance transcription via the accurate transcriber.

    Returns ``(text, fallback_reason)``: ``("text", "")`` on success, else
    ``("", reason)`` so the caller can fall back to the streaming socket. The
    accurate module is imported lazily and guarded, so its absence degrades to
    streaming rather than breaking the caller.
    """
    if not pcm:
        return "", "empty audio"
    try:
        from .asr_accurate import get_transcriber
    except Exception as exc:
        return "", f"accurate unavailable: {exc}"[:160]
    try:
        transcriber = get_transcriber(cfg)
        res = transcriber.transcribe(pcm, sample_rate=sample_rate)
    except Exception as exc:
        return "", str(exc)[:160] or "accurate failed"
    if getattr(res, "ok", False):
        return str(getattr(res, "text", "") or ""), ""
    return "", str(getattr(res, "reason", "") or "accurate unavailable")


__all__ = ["SttClient", "SttError", "ParecMic", "BufferingMic",
           "transcribe_once", "transcribe_accurate"]

