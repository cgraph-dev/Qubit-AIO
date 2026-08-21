"""Full-duplex PersonaPlex lane: continuous mic -> PersonaPlex server -> speakers.

This is the "PersonaPlex front-end" mode of the bridge:

    mic (continuous) ──► PersonaPlex on Vast.ai ──► speakers
          │                    (full duplex,         ▲
          │                     natural barge-in)    │
          └─► whisper tap ──► intent classifier ────┘ (task lane takes over
                                                     via TTS when needed)

PersonaPlex handles conversation end-to-end (chat, small talk, persona). The
bridge taps the same mic stream with a local whisper ASR so it can *also* see
what was said, classify it, and route tasks to OpenClaw / Hermes / local tools.

Threading model
---------------
- sounddevice callback thread: pushes mic PCM into an asyncio queue and feeds
  the VAD tap (never blocks on network or models).
- send thread (owns the asyncio loop): connects to PersonaPlex, pumps the mic
  queue to the WebSocket, runs the recv loop (audio -> speakers, text -> log).
- tap worker threads: when the local VAD ends an utterance, a short-lived
  worker transcribes it (whisper) and fires `on_transcript` off the audio
  thread so the orchestrator can run long tasks without glitching audio.

Ducking: while a task runs (or a confirmation is pending) the lane stops
feeding PersonaPlex and mutes its output, so the model cannot hallucinate
answers about work it cannot do. `duck()` persists until `unduck()`.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from ..audio.vad import Endpointer, FRAME_SIZE, resample_linear
from ..config import Config
from ..logging_setup import get_logger, log_event
from .client import DuplexClient, DuplexError, PersonaPlexUnavailable

log = get_logger()

TAP_FRAME_BYTES = FRAME_SIZE * 2  # 30 ms @16k int16 = 960 bytes

# A transcript tap is emitted whenever the local VAD ends an utterance.
TranscriptCallback = Callable[[str, float], None]  # (text, confidence)


class PersonaPlexDuplex:
    """Owns one continuous duplex session + the local routing tap."""

    def __init__(
        self,
        cfg: Config,
        on_transcript: Optional[TranscriptCallback] = None,
        on_state: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.on_transcript = on_transcript
        self.on_state = on_state  # state changes: connecting|live|ducked|down

        pp = cfg.section("personaplex")
        self.url: str = pp.get("url", "wss://personaplex:8998/api/chat")
        self.voice_prompt: str = pp.get("voice_prompt", "NATF2.pt")
        self.text_prompt: str = pp.get("text_prompt", "")
        self.ssl_insecure: bool = bool(pp.get("ssl_insecure", True))
        self.reconnect_s: float = float(pp.get("reconnect_s", 5.0))
        self.whisper_tap: bool = bool(pp.get("whisper_tap", True))
        self.sample_rate: int = int(pp.get("sample_rate", DuplexClient.SAMPLE_RATE))
        self._duck_seconds = 3600.0  # persistent until explicit unduck()
        self._output_mute = False    # instant stop of lane speech (PTT take-over)
        self._last_out_audio = 0.0   # for feedback suppression (speaking prop)
        self._noise_floor = 0.0      # adaptive speech-gate noise floor
        self._out_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=256)
        self._out_thread: Optional[threading.Thread] = None

        mic = cfg.section("mic")
        self.mic_device = mic.get("device")
        self.mic_rate: int = int(mic.get("sample_rate", 48000))
        self.mic_channels: int = int(mic.get("channels", 2))
        self.mic_blocksize: int = int(mic.get("blocksize", 480))
        self.mic_gain: float = float(mic.get("gain", 4.0))

        # state
        self._stop = threading.Event()
        self._duck_until = 0.0
        self._lock = threading.RLock()
        self._state = "down"
        self._mic_queue: "asyncio.Queue[np.ndarray]" = asyncio.Queue(maxsize=64)
        self._queue_ref = self._mic_queue  # keep a stable ref across loop restarts

        # threads
        self._send_thread: Optional[threading.Thread] = None
        self._tap_busy = threading.Event()

        # streaming objects (owned by send thread)
        self._client: Optional[DuplexClient] = None
        self._stream = None  # sounddevice InputStream
        self._out = None     # sounddevice OutputStream (24k mono)

        # tap state
        self._tap_frame_buf = bytearray()
        self._tap_pcm = np.zeros(0, np.float32)
        self._tap_endpointer: Optional[Endpointer] = None
        self._tap_started = False

        # whisper tap (lazy)
        self._whisper = None

        # stats
        self.bytes_sent = 0
        self.bytes_recv = 0

    # ------------------------------------------------------------------ props
    @property
    def state(self) -> str:
        return self._state

    @property
    def live(self) -> bool:
        return self._state == "live"

    @property
    def speaking(self) -> bool:
        """True while the lane has recently written audio to the speakers
        (used to suppress the mic tap so it doesn't hear its own voice)."""
        return time.time() - self._last_out_audio < 0.8

    def interrupt_output(self) -> None:
        """Instantly stop lane speech (PTT take-over); resume with resume_output()."""
        self._output_mute = True

    def resume_output(self) -> None:
        self._output_mute = False

    def _set_state(self, s: str) -> None:
        if self._state != s:
            self._state = s
            log_event("personaplex.state", state=s)
            if self.on_state:
                try:
                    self.on_state(s)
                except Exception:
                    pass

    # ------------------------------------------------------------------ tap
    def _make_whisper(self):
        if self._whisper is None:
            from ..asr.whisper import FasterWhisperASR
            asr_cfg = self.cfg.section("asr")
            self._whisper = FasterWhisperASR(
                model=asr_cfg.get("model", "base"),
                device=asr_cfg.get("device", "auto"))
            self._whisper.load()
        return self._whisper

    def _feed_tap(self, mono24: np.ndarray) -> None:
        """Downsample to 16k, run 30 ms VAD frames, collect utterance PCM."""
        if not self.whisper_tap or self._tap_endpointer is None:
            return
        mono16 = resample_linear(mono24, self.sample_rate, 16000).astype(np.float32)
        pcm16 = np.clip(mono16 * 32767, -32768, 32767).astype(np.int16)
        if self._tap_started:
            self._tap_pcm = np.concatenate([self._tap_pcm, pcm16.astype(np.float32) / 32767.0]) \
                if self._tap_pcm.size else pcm16.astype(np.float32) / 32767.0
        self._tap_frame_buf += pcm16.tobytes()
        while len(self._tap_frame_buf) >= TAP_FRAME_BYTES:
            frame = bytes(self._tap_frame_buf[:TAP_FRAME_BYTES])
            del self._tap_frame_buf[:TAP_FRAME_BYTES]
            verdict = self._tap_endpointer.add_frame(frame)
            if verdict == "speech" and not self._tap_started:
                self._tap_started = True
                # re-seed the utterance buffer with the frames that started speech
                self._tap_pcm = np.zeros(0, np.float32)
            elif verdict == "done" and self._tap_started:
                self._tap_started = False
                self._tap_endpointer.reset()
                self._flush_tap()

    def _flush_tap(self) -> None:
        if self._tap_pcm.size < FRAME_SIZE * 4:  # < 120 ms -> noise, skip
            self._tap_pcm = np.zeros(0, np.float32)
            return
        if self._tap_busy.is_set():
            log.debug("tap worker busy; dropping utterance")
            self._tap_pcm = np.zeros(0, np.float32)
            return
        pcm = self._tap_pcm
        self._tap_pcm = np.zeros(0, np.float32)
        self._tap_busy.set()

        def _work():
            try:
                tmp = Path(self.cfg.state_dir()) / "tap-utterance.wav"
                with wave.open(str(tmp), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(np.clip(pcm * 32767, -32768, 32767).astype(np.int16).tobytes())
                tr = self._make_whisper().transcribe(tmp)
                if tr.text and tr.text.strip():
                    conf = float(tr.confidence)
                    log_event("personaplex.tap", text=tr.text[:300], confidence=conf)
                    # confidence gate: whisper hallucinates garbage (0.0-0.1) on
                    # noise/echo — routing it ducks PersonaPlex and the local
                    # piper voice answers nonsense. Real speech is 0.7+.
                    min_conf = float(self.cfg.get("personaplex", "tap_confidence",
                                                  default=0.3))
                    if conf < min_conf:
                        log_event("personaplex.tap.low", text=tr.text[:120],
                                  confidence=conf)
                        return
                    if self.on_transcript:
                        try:
                            self.on_transcript(tr.text.strip(), conf)
                        except Exception as exc:
                            log.warning("transcript callback failed: %s", exc)
            except Exception as exc:
                log_event("personaplex.tap.error", error=str(exc)[:160])
            finally:
                self._tap_busy.clear()

        threading.Thread(target=_work, daemon=True, name="pp-tap").start()

    # ------------------------------------------------------------------ duck
    def duck(self, seconds: Optional[float] = None) -> None:
        """Stop feeding PersonaPlex + mute its output.

        Defaults to a long window; call `unduck()` to resume explicitly.
        """
        with self._lock:
            self._duck_until = time.time() + (seconds if seconds is not None else self._duck_seconds)
            if self.live:
                self._set_state("ducked")

    def unduck(self) -> None:
        with self._lock:
            self._duck_until = 0.0
            if self._state == "ducked" and not self._stop.is_set():
                self._set_state("live")

    def _is_ducked(self) -> bool:
        return time.time() < self._duck_until

    # ------------------------------------------------------------------ io
    def _audio_cb(self, indata: np.ndarray, frames: int, t_info, status) -> None:
        if status:
            log.debug("mic status: %s", status)
        try:
            mono = np.asarray(indata, dtype=np.float32)
            if mono.ndim > 1:
                # loudest channel, not the mean: the Scarlett 4ch profile puts
                # the mic on ch0 and ch1-3 are ~silent — averaging dilutes the
                # voice 4x (RMS 0.008 -> 0.002, below what the model needs)
                mono = np.max(np.abs(mono), axis=1) * np.sign(mono[:, 0] if mono.shape[1] else mono)
            mono = mono * self.mic_gain
            # the tap always runs (its own VAD is tuned for quiet mics) —
            # never gate it behind the send threshold. BUT: never transcribe
            # the lane's own output (echo) — whisper hallucinates gibberish
            # on it and the garbage gets routed.
            if not self.speaking:
                self._feed_tap(mono)
            # ECHO GATE: never feed the server our own output — the speakers
            # play PersonaPlex, the mic hears it, and the model babbles on its
            # own voice forever (gibberish loop). Skip while (and shortly
            # after) the lane is speaking.
            if self.speaking:
                return
            # SPEECH GATE (adaptive): only stream real speech. A static
            # threshold can't win — the noise floor moves with the physical
            # gain. Track a slow noise floor and require signal well above it.
            rms = float(np.sqrt((mono ** 2).mean())) if mono.size else 0.0
            if rms > 0.0:
                if self._noise_floor <= 0.0:
                    self._noise_floor = rms
                else:
                    # slow rise, fast drop: floor follows quiet periods
                    self._noise_floor = min(self._noise_floor * 1.02 + 1e-5, rms)
                floor = self._noise_floor
                adaptive = float(self.cfg.get("personaplex", "adaptive_gate",
                                              default=1.2))
                if rms < max(floor * adaptive, 0.002):
                    return
            else:
                return
            try:
                self._queue_ref.put_nowait(mono)
            except asyncio.QueueFull:
                pass
        except Exception as exc:
            log.warning("audio cb error: %s", exc)

    def _on_pp_audio(self, pcm: np.ndarray) -> None:
        """Audio from the server -> speaker via a WRITER THREAD.

        Never write to sounddevice from the async loop: OutputStream.write()
        blocks, and any stall (generation burst, concurrent piper playback)
        freezes the recv loop -> server transport reset -> session crash.
        """
        with self._lock:
            if self._is_ducked() or self._stop.is_set():
                return
            if self._output_mute:
                return  # PTT take-over: drop the lane's speech immediately
        self.bytes_recv += pcm.nbytes
        self._last_out_audio = time.time()
        try:
            self._out_queue.put_nowait(pcm.astype(np.float32, copy=False))
        except queue.Full:
            try:
                self._out_queue.get_nowait()  # drop oldest, keep latency low
                self._out_queue.put_nowait(pcm.astype(np.float32, copy=False))
            except Exception:
                pass

    def _out_worker(self) -> None:
        """Drains the output queue to the speakers (owns sounddevice writes)."""
        while not self._stop.is_set():
            try:
                pcm = self._out_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                if self._out is None or self._is_ducked():
                    continue
            try:
                self._out.write(pcm)
            except Exception as exc:
                log.debug("output write: %s", exc)

    def _on_pp_text(self, delta: str) -> None:
        log_event("personaplex.text", delta=delta[:120])

    def _on_pp_close(self, reason: Optional[str]) -> None:
        log_event("personaplex.disconnected", reason=reason or "closed")

    # ------------------------------------------------------------------ run
    def start(self) -> bool:
        try:
            import sounddevice  # noqa: F401
        except Exception as exc:
            log_event("personaplex.start.error", error=f"sounddevice unavailable: {exc}")
            return False
        self._stop.clear()
        self._tap_endpointer = Endpointer(
            aggressiveness=int(self.cfg.get("vad", "aggressiveness", default=1)),
            speech_start_ms=int(self.cfg.get("vad", "speech_start_ms", default=180)),
            silence_timeout_s=float(self.cfg.get("vad", "silence_timeout_s", default=1.2)),
            max_utterance_s=float(self.cfg.get("vad", "max_utterance_s", default=30.0)),
        )
        self._tap_started = False
        self._tap_frame_buf = bytearray()
        self._tap_pcm = np.zeros(0, np.float32)
        self._mic_queue = asyncio.Queue(maxsize=64)
        self._queue_ref = self._mic_queue
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True,
                                             name="pp-duplex-send")
        self._send_thread.start()
        self._out_thread = threading.Thread(target=self._out_worker, daemon=True,
                                            name="pp-duplex-out")
        self._out_thread.start()
        self._set_state("connecting")
        return True

    def _send_loop(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._pump_once())
            except PersonaPlexUnavailable as exc:
                log_event("personaplex.retry", error=str(exc)[:160])
                self._set_state("down")
            except Exception as exc:
                log_event("personaplex.retry", error=str(exc)[:160])
                self._set_state("down")
            finally:
                self._close_streams()
                self._client = None
            if self._stop.wait(self.reconnect_s):
                break
        self._set_state("down")

    async def _pump_once(self) -> None:
        import sounddevice as sd
        # fresh queue per loop iteration: asyncio.Queue binds to the loop it is
        # first used on, and reconnect spawns a new loop
        self._mic_queue = asyncio.Queue(maxsize=64)
        self._queue_ref = self._mic_queue
        client = DuplexClient(
            url=self.url, voice_prompt=self.voice_prompt,
            text_prompt=self.text_prompt, ssl_insecure=self.ssl_insecure,
            on_audio=self._on_pp_audio, on_text=self._on_pp_text,
            on_close=self._on_pp_close)
        # generous connect: the server must generate system prompts (~30-60 s
        # on a shared host) before it sends the handshake byte; too-short
        # timeouts cause a permanent connect/retry loop
        await client.connect(timeout_s=float(
            self.cfg.get("personaplex", "connect_timeout_s", default=90.0)))
        self._client = client
        self._open_streams(sd)
        with self._lock:
            if not self._stop.is_set() and not self._is_ducked():
                self._set_state("live")
        t0 = time.monotonic()
        samples_sent = 0
        # inaudible dither floor: ONLY for the duck-silence keepalive path.
        # The speech gate means silence sends nothing, so the model stays quiet
        # between turns (no more gibberish); frames flow only for real speech.
        dither = float(self.cfg.get("personaplex", "dither_floor", default=1.0e-4))
        dither_frame = None
        if dither > 0:
            rng = np.random.default_rng(0)
            dither_frame = (rng.random(self.sample_rate // 50,
                                       dtype=np.float32) - 0.5) * dither
        try:
            while not self._stop.is_set() and client.connected:
                try:
                    pcm = await asyncio.wait_for(self._mic_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if self._is_ducked():
                    # keep the session alive + never accumulate: drain the mic
                    # queue and stream silence so unduck resumes without a burst
                    while not self._mic_queue.empty():
                        try:
                            self._mic_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    sil = np.zeros(self.sample_rate // 50, np.float32)
                    if dither_frame is not None:
                        sil = sil + dither_frame
                    try:
                        await client.send_pcm_async(sil)
                        samples_sent += len(sil)
                    except DuplexError:
                        break
                    await asyncio.sleep(0.02)
                    continue
                # pre-send pacing: throttle BEFORE sending so a queue backlog
                # drains at real-time cadence instead of bursting
                target_s = samples_sent / self.sample_rate
                elapsed = time.monotonic() - t0
                if target_s > elapsed:
                    await asyncio.sleep(target_s - elapsed)
                try:
                    if dither_frame is not None:
                        pcm = pcm + dither_frame
                    await client.send_pcm_async(pcm)
                    self.bytes_sent += pcm.nbytes
                    samples_sent += len(pcm)
                except DuplexError:
                    break
                except Exception as exc:
                    log_event("personaplex.pump.error",
                              error=f"{type(exc).__name__}: {str(exc)[:140]}")
                    break
        finally:
            with self._lock:
                self._client = None
            try:
                await client.close()
            except Exception:
                pass

    def _open_streams(self, sd) -> None:
        if self._stream is None:
            self._stream = sd.InputStream(
                device=self.mic_device, samplerate=self.mic_rate,
                channels=self.mic_channels, blocksize=self.mic_blocksize,
                dtype="float32", callback=self._audio_cb)
            self._stream.start()
        if self._out is None:
            # hardware (Scarlett 48k, ALC892 44.1/48k) rejects 24 kHz and
            # PortAudio won't resample — the pulse/pipewire mixers do. Try the
            # configured device, then pulse/pipewire, then the default; keep
            # the lane alive for INPUT even if no output opens yet.
            candidates = []
            cfg_dev = self.cfg.get("personaplex", "output_device", default=None)
            if cfg_dev:
                candidates.append(cfg_dev)
            for name in ("pulse", "pipewire", None):
                try:
                    idx = sd.default.device[1] if name is None else name
                    o = sd.OutputStream(device=idx, samplerate=self.sample_rate,
                                        channels=1, dtype="float32")
                    o.start()
                    self._out = o
                    log_event("personaplex.output", device=str(idx),
                              rate=self.sample_rate)
                    break
                except Exception as exc:
                    log.warning("output stream %s failed: %s", name, exc)
                    continue

    def _close_streams(self) -> None:
        for s in (self._stream, self._out):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
        self._stream = None
        self._out = None

    def stop(self) -> None:
        self._stop.set()
        self._close_streams()
        if self._send_thread and self._send_thread.is_alive():
            self._send_thread.join(timeout=3.0)
        self._send_thread = None
        if self._out_thread and self._out_thread.is_alive():
            self._out_thread.join(timeout=2.0)
        self._out_thread = None
        self._set_state("down")
