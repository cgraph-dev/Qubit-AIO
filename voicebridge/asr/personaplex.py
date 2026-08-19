"""PersonaPlex adapter (pluggable, INACTIVE on this 8 GB box).

PersonaPlex (nvidia/personaplex-7b-v1, Moshi-based realtime speech-to-speech)
cannot run reliably on an 8 GB RTX 3070 Ti:
  * official weights are 15.6 GB bf16 safetensors;
  * community quants (q4_k 4.7 GB GGUF / NF4 4.4 GB) exist, but the only torch
    runtime targets Jetson AGX Orin 32-64 GB (unified memory), and the GGUF
    path is a community moshi.cpp fork with no production Linux audio pipeline;
  * even a technically-loadable quant leaves no headroom for the desktop,
    Ollama agent models, and streaming KV cache that a daily-use stack needs.

This adapter is the *interface* the bridge uses so that PersonaPlex can be
dropped in on a 12 GB+ GPU without touching the rest of the system:

  1. run the PersonaPlex realtime server on the big machine / after upgrade
     (it exposes a WebSocket that accepts 16 kHz PCM and emits transcripts),
  2. set asr.provider: personaplex in voice.yaml,
  3. done — the orchestrator uses this adapter unchanged.

Until then, `available()` returns False and the orchestrator uses whisper.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import numpy as np

from ..logging_setup import log_event
from .base import ASRProvider, ASRUnavailable, Transcript


class PersonaPlexASR(ASRProvider):
    """Client for a PersonaPlex realtime speech server.

    Wire contract (documented here because the server may vary):
      * connect to ws://<host>:<port>/api/audio
      * send 16 kHz mono PCM chunks; receive JSON messages:
          {"type": "transcript", "text": "...", "final": bool}
          {"type": "ready"}
    The bridge sends one utterance and waits for the final transcript.
    """

    name = "personaplex"

    def __init__(self, url: str = "ws://127.0.0.1:8765", timeout_s: float = 5.0) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self._available: bool | None = None

    def load(self) -> None:
        # Nothing to preload; availability is probed per call so a late-starting
        # PersonaPlex server is picked up automatically.
        pass

    def _probe(self) -> bool:
        try:
            import websockets
            with websockets.sync.client.connect(self.url, open_timeout=self.timeout_s):
                return True
        except Exception:
            return False

    def available(self) -> bool:
        try:
            self._available = self._probe()
        except Exception:
            self._available = False
        return bool(self._available)

    def transcribe(self, audio_path: Path | str) -> Transcript:
        if not self.available():
            raise ASRUnavailable(
                "PersonaPlex server not reachable; this adapter is inactive on 8 GB "
                "hardware — set asr.provider: whisper (or run PersonaPlex on a 12 GB+ GPU).")
        # Read WAV -> 16k mono PCM
        import wave
        with wave.open(str(audio_path), "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        pcm = np.frombuffer(frames, dtype=np.int16)
        if sr != 16000:
            # resample via linear interpolation
            n = int(len(pcm) * 16000 / sr)
            idx = np.linspace(0, len(pcm) - 1, n)
            i0 = idx.astype(np.int64)
            i1 = np.minimum(i0 + 1, len(pcm) - 1)
            frac = (idx - i0).astype(np.float32)
            pcm = (pcm[i0] * (1 - frac) + pcm[i1] * frac).astype(np.int16)

        async def _run():
            import websockets
            async with websockets.connect(self.url, open_timeout=self.timeout_s) as ws:
                await ws.send(pcm.tobytes())
                final_text = ""
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), self.timeout_s * 4))
                    if msg.get("type") == "transcript" and msg.get("text"):
                        final_text = msg["text"] if msg.get("final") else final_text or msg["text"]
                        if msg.get("final"):
                            return final_text
                    elif msg.get("type") == "error":
                        raise ASRUnavailable(f"personaplex error: {msg.get('message')}")

        try:
            text = asyncio.run(_run())
        except Exception as exc:
            raise ASRUnavailable(f"personaplex transcribe failed: {exc}") from exc
        log_event("asr.transcribe", provider=self.name, text=text, final=True)
        return Transcript(text=text, provider=self.name)
