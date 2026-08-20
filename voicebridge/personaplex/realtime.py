"""OpenAI Realtime duplex lane: continuous mic -> Realtime API -> speakers.

A sibling of the PersonaPlex duplex lane with the SAME interface
(start/stop/state/duck/unduck/on_transcript) so the orchestrator can swap
providers via config (`personaplex.provider: realtime|personaplex`).

Why this lane exists: PersonaPlex needs a 24 GB-class GPU; OpenAI Realtime
delivers the same full-duplex, sub-second, natural conversation TODAY on this
8 GB box (the API runs in the cloud). Task routing stays local: the Realtime
session transcribes the user's speech (whisper-1 server-side), we tap the
transcript, classify, and route tasks to OpenClaw/Hermes/local exactly like the
PersonaPlex lane does. Conversation is answered natively by the model; during
tasks the lane is ducked so it cannot hallucinate answers about work it cannot
do.

Wire protocol (OpenAI Realtime API, WebSocket):
  wss://api.openai.com/v1/realtime?model=<model>
  auth: Authorization: Bearer <OPENAI_API_KEY>
  send:  session.update (server_vad + input transcription)
         input_audio_buffer.append  (base64 pcm16 24 kHz mono)
         response.cancel            (stop the model's reply when ducking)
  recv:  session.created/updated, response.audio.delta (pcm16 24k -> speakers),
         response.audio_transcript.delta (text for logging),
         conversation.item.input_audio_transcription.completed (user text -> tap),
         input_audio_buffer.speech_started/speech_stopped, response.done, error
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from ..config import Config
from ..logging_setup import get_logger, log_event

log = get_logger()

SAMPLE_RATE = 24000  # Realtime API expects pcm16 mono 24 kHz
TranscriptCallback = Callable[[str, float], None]


def _read_env_key(env_path: str, name: str) -> str:
    try:
        for line in Path(env_path).expanduser().read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get(name, "")


def read_hermes_default_model(env_path: str = "~/.hermes/config.yaml") -> str:
    """Read the model Hermes is actually configured to use (model.default)."""
    try:
        import yaml
        data = yaml.safe_load(Path(env_path).expanduser().read_text(errors="replace"))
        m = (data or {}).get("model", {})
        if isinstance(m, dict):
            return str(m.get("default") or m.get("model") or "").strip()
        if isinstance(m, str):
            return m.strip()
    except Exception:
        pass
    return ""


class RealtimeUnavailable(Exception):
    pass


class RealtimeDuplex:
    """Owns one continuous OpenAI Realtime session + the routing transcript tap."""

    def __init__(
        self,
        cfg: Config,
        on_transcript: Optional[TranscriptCallback] = None,
        on_state: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.on_transcript = on_transcript
        self.on_state = on_state

        rt = cfg.section("realtime")
        self.model: str = rt.get("model", "gpt-4o-mini-realtime-preview")
        self.key_env: str = rt.get("key_env", "~/.hermes/.env")
        self.reconnect_s: float = float(rt.get("reconnect_s", 5.0))
        self.duck_seconds: float = 3600.0  # persistent until explicit unduck()

        mic = cfg.section("mic")
        self.mic_device = mic.get("device")
        self.mic_rate: int = int(mic.get("sample_rate", 48000))
        self.mic_channels: int = int(mic.get("channels", 2))
        self.mic_blocksize: int = int(mic.get("blocksize", 480))
        self.mic_gain: float = float(mic.get("gain", 4.0))

        self._stop = threading.Event()
        self._duck_until = 0.0
        self._lock = threading.RLock()
        self._state = "down"
        self._send_thread: Optional[threading.Thread] = None
        self._client: Optional[Any] = None
        self._stream = None  # sounddevice InputStream (mic rate)
        self._out = None     # sounddevice OutputStream (24k mono)
        self._transcripts: dict = {}  # item_id -> accumulated text
        self.bytes_sent = 0
        self.bytes_recv = 0

    # ------------------------------------------------------------------ props
    @property
    def state(self) -> str:
        return self._state

    @property
    def live(self) -> bool:
        return self._state == "live"

    def _set_state(self, s: str) -> None:
        if self._state != s:
            self._state = s
            log_event("realtime.state", state=s)
            if self.on_state:
                try:
                    self.on_state(s)
                except Exception:
                    pass

    # ------------------------------------------------------------------ duck
    def duck(self, seconds: Optional[float] = None) -> None:
        with self._lock:
            self._duck_until = time.time() + (seconds if seconds is not None else self.duck_seconds)
            if self.live:
                self._set_state("ducked")
                self._cancel_response_async()

    def unduck(self) -> None:
        with self._lock:
            self._duck_until = 0.0
            if self._state == "ducked" and not self._stop.is_set():
                self._set_state("live")

    def _is_ducked(self) -> bool:
        return time.time() < self._duck_until

    def _cancel_response_async(self) -> None:
        """Ask the API to stop generating (no-op if no client/loop handy)."""
        client = self._client
        if client is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                client.send(json.dumps({"type": "response.cancel"})),
                client.loop).result(timeout=1.0)
        except Exception:
            pass

    # ------------------------------------------------------------------ io
    def _audio_cb(self, indata: np.ndarray, frames: int, t_info, status) -> None:
        if status:
            log.debug("mic status: %s", status)
        try:
            mono = np.asarray(indata, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1)
            mono = mono * self.mic_gain
            if self._is_ducked() or self._stop.is_set():
                return
            client = self._client
            if client is None:
                return
            # 48k -> 24k exact decimation by 2 (any 2:1 rate works)
            if self.mic_rate == SAMPLE_RATE:
                pcm24 = mono
            elif self.mic_rate % SAMPLE_RATE == 0:
                pcm24 = mono[:: self.mic_rate // SAMPLE_RATE]
            else:
                pcm24 = np.interp(
                    np.linspace(0, len(mono) - 1, len(mono) * SAMPLE_RATE // self.mic_rate),
                    np.arange(len(mono)), mono).astype(np.float32)
            pcm16 = np.clip(pcm24 * 32767, -32768, 32767).astype("<i2")
            payload = base64.b64encode(pcm16.tobytes()).decode()
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send(json.dumps(
                        {"type": "input_audio_buffer.append", "audio": payload})),
                    client.loop).result(timeout=0.05)
                self.bytes_sent += len(payload)
            except Exception:
                pass
        except Exception as exc:
            log.warning("audio cb error: %s", exc)

    def _on_rt_audio(self, payload_b64: str) -> None:
        with self._lock:
            if self._is_ducked() or self._out is None or self._stop.is_set():
                return
            try:
                raw = np.frombuffer(base64.b64decode(payload_b64), dtype="<i2")
                self._out.write(raw.astype(np.float32) / 32767.0)
                self.bytes_recv += len(raw) * 2
            except Exception as exc:
                log.debug("output write: %s", exc)

    # ------------------------------------------------------------------ events
    def _on_event(self, client: Any, ev: dict) -> None:
        t = ev.get("type", "")
        if t == "response.audio.delta":
            self._on_rt_audio(ev.get("delta", ""))
        elif t == "response.audio_transcript.delta":
            log_event("realtime.transcript.delta", delta=ev.get("delta", "")[:120])
        elif t == "conversation.item.input_audio_transcription.completed":
            item_id = ev.get("item_id", "")
            text = (ev.get("transcript") or "").strip()
            if text:
                log_event("realtime.user_text", text=text[:300])
                if self.on_transcript:
                    try:
                        self.on_transcript(text, 1.0)
                    except Exception as exc:
                        log.warning("transcript callback failed: %s", exc)
        elif t == "input_audio_buffer.speech_started":
            log_event("realtime.speech.started")
        elif t == "input_audio_buffer.speech_stopped":
            log_event("realtime.speech.stopped")
        elif t == "response.done":
            log_event("realtime.response.done")
        elif t == "error":
            err = ev.get("error", {})
            log_event("realtime.error", code=err.get("code", ""),
                      message=str(err.get("message", ""))[:160])
        else:
            log.debug("realtime event: %s", t)

    # ------------------------------------------------------------------ run
    def start(self) -> bool:
        try:
            import sounddevice  # noqa: F401
            import websockets  # noqa: F401
        except Exception as exc:
            log_event("realtime.start.error", error=f"deps unavailable: {exc}")
            return False
        key = _read_env_key(self.key_env, "OPENAI_API_KEY")
        if not key:
            log_event("realtime.start.error",
                      error="OPENAI_API_KEY not found (set realtime.key_env)")
            return False
        self._stop.clear()
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True,
                                             name="rt-duplex-send")
        self._send_thread.start()
        self._set_state("connecting")
        return True

    def _send_loop(self) -> None:
        blocked_backoff = False
        while not self._stop.is_set():
            try:
                asyncio.run(self._pump_once())
            except RealtimeUnavailable as exc:
                log_event("realtime.retry", error=str(exc)[:160])
                self._set_state("down")
            except Exception as exc:
                err = str(exc)
                if "beta_api_shape_disabled" in err or "invalid_request_error" in err:
                    # account-level block (key/plan without Realtime enabled):
                    # log clearly and back off hard instead of spamming retries
                    if not blocked_backoff:
                        log_event("realtime.account_blocked",
                                  hint="enable Realtime API / add credits at platform.openai.com")
                        blocked_backoff = True
                    self._set_state("down")
                    wait = 300.0
                else:
                    log_event("realtime.retry", error=err[:160])
                    self._set_state("down")
                    wait = self.reconnect_s
                self._close_streams()
                self._client = None
                if self._stop.wait(wait):
                    break
                continue
            finally:
                self._close_streams()
                self._client = None
            if self._stop.wait(self.reconnect_s):
                break
        self._set_state("down")

    async def _pump_once(self) -> None:
        import sounddevice as sd
        import websockets
        key = _read_env_key(self.key_env, "OPENAI_API_KEY")
        url = (f"wss://api.openai.com/v1/realtime?model={self.model}")
        headers = {"Authorization": f"Bearer {key}",
                   "OpenAI-Beta": "realtime=v1"}
        async with websockets.connect(url, additional_headers=headers,
                                      open_timeout=15.0, ping_interval=20.0) as ws:
            self._client = ws
            self._open_streams(sd)
            with self._lock:
                if not self._stop.is_set() and not self._is_ducked():
                    self._set_state("live")
            # configure: server VAD + whisper transcription + audio modality
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "modalities": ["audio", "text"],
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 600,
                    },
                    "input_audio_transcription": {"model": "whisper-1"},
                },
            }))
            log_event("realtime.session.configured", model=self.model)
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    ev = json.loads(raw)
                    if ev.get("type") in ("session.created", "session.updated",
                                          "conversation.created"):
                        continue
                    self._on_event(ws, ev)
            finally:
                with self._lock:
                    self._client = None

    def _open_streams(self, sd) -> None:
        if self._stream is None:
            self._stream = sd.InputStream(
                device=self.mic_device, samplerate=self.mic_rate,
                channels=self.mic_channels, blocksize=self.mic_blocksize,
                dtype="float32", callback=self._audio_cb)
            self._stream.start()
        if self._out is None:
            self._out = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1,
                                        dtype="float32")
            self._out.start()

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
        self._set_state("down")
