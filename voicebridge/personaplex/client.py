"""Low-level PersonaPlex /api/chat WebSocket client (mirrors moshi/server.py).

The NVIDIA server accepts Opus frames at the Mimi codec sample rate (24 kHz)
and returns Opus audio plus text-token deltas. `sphn` is the same streaming
Opus wrapper the server itself uses, so frame sizes and format match exactly.
"""
from __future__ import annotations

import asyncio
import ssl
import time
import urllib.parse
from typing import Awaitable, Callable, Optional

import numpy as np

from ..logging_setup import get_logger, log_event

log = get_logger()

try:  # sphn is part of the moshi install; make import failure explicit
    import sphn
except ImportError:  # pragma: no cover - environment check
    sphn = None


def _is_open(ws) -> bool:
    """websockets >=14 exposes state; legacy used .closed — support both."""
    st = getattr(ws, "state", None)
    if st is not None:
        try:
            return st.name == "OPEN"
        except AttributeError:
            pass
    return not getattr(ws, "closed", True)


class PersonaPlexUnavailable(RuntimeError):
    pass


class DuplexError(RuntimeError):
    pass


class DuplexClient:
    """Async full-duplex client for one PersonaPlex conversation session.

    - `send_pcm(pcm)`: 24 kHz mono float32/float64 numpy array -> Opus -> wire
    - `recv` callbacks: `on_audio(pcm_24k_float32)`, `on_text(delta_text)`
    - text deltas arrive in pieces; use `on_text` and join for transcripts
    - one session at a time (server serializes with a lock); reconnect manually
    """

    SAMPLE_RATE = 24000  # Mimi codec rate

    def __init__(
        self,
        url: str = "wss://personaplex:8998/api/chat",
        voice_prompt: str = "NATF2.pt",
        text_prompt: str = "",
        seed: int = -1,
        ssl_insecure: bool = True,
        on_audio: Optional[Callable[[np.ndarray], None]] = None,
        on_text: Optional[Callable[[str], None]] = None,
        on_close: Optional[Callable[[Optional[str]], None]] = None,
    ) -> None:
        self.url = url
        self.voice_prompt = voice_prompt
        self.text_prompt = text_prompt
        self.seed = seed
        self.ssl_insecure = ssl_insecure
        self.on_audio = on_audio
        self.on_text = on_text
        self.on_close = on_close

        self._ws = None
        self._writer: Optional[sphn.OpusStreamWriter] = None  # type: ignore[union-attr]
        self._reader: Optional[sphn.OpusStreamReader] = None  # type: ignore[union-attr]
        self._recv_task: Optional[asyncio.Task] = None
        self._close_reason: Optional[str] = None

    # ------------------------------------------------------------------ conn
    @property
    def connected(self) -> bool:
        return self._ws is not None and _is_open(self._ws)

    def _ssl_ctx(self) -> Optional[ssl.SSLContext]:
        if self.url.startswith("wss://"):
            ctx = ssl.create_default_context()
            if self.ssl_insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def _query(self) -> str:
        q = urllib.parse.urlencode({
            "voice_prompt": self.voice_prompt,
            "text_prompt": self.text_prompt,
            "seed": str(self.seed),
        })
        sep = "&" if "?" in self.url else "?"
        return f"{self.url}{sep}{q}"

    async def connect(self, timeout_s: float = 15.0) -> None:
        if sphn is None:
            raise PersonaPlexUnavailable(
                "sphn package missing - install the moshi stack "
                "(pip install sphn plus libopus-dev) to use PersonaPlex.")
        import websockets
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._query(), ssl=self._ssl_ctx(),
                                   max_size=None, open_timeout=timeout_s,
                                   ping_interval=None,  # aiohttp server fails keepalives
                                   ping_timeout=None),
                timeout=timeout_s + 5.0)
        except Exception as exc:
            raise PersonaPlexUnavailable(f"connect failed: {exc}") from exc
        self._writer = sphn.OpusStreamWriter(self.SAMPLE_RATE)
        self._reader = sphn.OpusStreamReader(self.SAMPLE_RATE)
        self._close_reason = None
        self._recv_task = asyncio.create_task(self._recv_loop())
        log_event("personaplex.connected", url=self.url, voice_prompt=self.voice_prompt)

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._writer = None
        self._reader = None

    # ------------------------------------------------------------------ send
    # sphn accepts only specific frame sizes (120/240/480/960/1920/2880 @24k).
    _FRAME = 480  # 20 ms @ 24 kHz

    def _chunk(self, pcm: np.ndarray) -> np.ndarray:
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1)
        n = (len(pcm) + self._FRAME - 1) // self._FRAME * self._FRAME
        if n > len(pcm):
            pcm = np.pad(pcm, (0, n - len(pcm)))
        return pcm

    def send_pcm(self, pcm: np.ndarray) -> None:
        """Encode + send 24 kHz mono PCM (float or int16), chunked to 20 ms."""
        if not self.connected or self._writer is None:
            raise DuplexError("not connected")
        pcm = self._chunk(pcm)
        for i in range(0, len(pcm), self._FRAME):
            self._writer.append_pcm(pcm[i:i + self._FRAME])  # type: ignore[union-attr]
        while True:
            chunk = self._writer.read_bytes()  # type: ignore[union-attr]
            if not chunk:
                break
            try:
                asyncio.get_running_loop().call_soon(
                    self._ws_send, b"\x01" + chunk)  # type: ignore[union-attr]
            except RuntimeError:
                break  # no running loop (sync misuse) - drop

    def _ws_send(self, payload: bytes) -> None:
        if self._ws is not None and _is_open(self._ws):
            asyncio.ensure_future(self._ws.send(payload))

    async def send_pcm_async(self, pcm: np.ndarray) -> None:
        if not self.connected or self._writer is None:
            raise DuplexError("not connected")
        pcm = self._chunk(pcm)
        for i in range(0, len(pcm), self._FRAME):
            self._writer.append_pcm(pcm[i:i + self._FRAME])  # type: ignore[union-attr]
        while True:
            chunk = self._writer.read_bytes()  # type: ignore[union-attr]
            if not chunk:
                break
            await self._ws.send(b"\x01" + chunk)  # type: ignore[union-attr]

    # ------------------------------------------------------------------ recv
    async def _recv_loop(self) -> None:
        try:
            async for message in self._ws:  # type: ignore[union-attr]
                if not isinstance(message, (bytes, bytearray)):
                    continue
                if len(message) == 0:
                    continue
                kind = message[0]
                payload = bytes(message[1:])
                if kind == 1:  # audio
                    if self._reader is not None:
                        self._reader.append_bytes(payload)  # type: ignore[union-attr]
                        # sphn decodes on a worker thread and can deliver in
                        # bursts; keep draining until a quiet streak (bounded).
                        empty_streak = 0
                        t_drain = time.monotonic()
                        while time.monotonic() - t_drain < 0.1:
                            pcm = self._reader.read_pcm()  # type: ignore[union-attr]
                            if pcm is None:  # stream closed
                                break
                            if pcm.shape[-1] == 0:
                                empty_streak += 1
                                if empty_streak >= 3:
                                    break
                                await asyncio.sleep(0.002)
                                continue
                            empty_streak = 0
                            pcm = np.asarray(pcm, dtype=np.float32)
                            if pcm.ndim > 1:
                                pcm = pcm.mean(axis=1)
                            if self.on_audio:
                                try:
                                    self.on_audio(pcm)
                                except Exception as exc:
                                    log.warning("on_audio callback failed: %s", exc)
                elif kind == 2:  # assistant text delta
                    if self.on_text:
                        try:
                            self.on_text(payload.decode("utf-8", errors="replace"))
                        except Exception as exc:
                            log.warning("on_text callback failed: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._close_reason = str(exc)[:200]
            log.warning("personaplex recv loop ended: %s", exc)
        finally:
            if self.on_close:
                try:
                    self.on_close(self._close_reason)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Sync probe helper (used by CLI / availability checks)
# ---------------------------------------------------------------------------
def probe_sync(url: str, voice_prompt: str = "NATF2.pt", text_prompt: str = "hi",
               timeout_s: float = 10.0, ssl_insecure: bool = True) -> float:
    """Return RTT (s) if a PersonaPlex /api/chat handshake succeeds, else raise."""
    if sphn is None:
        raise PersonaPlexUnavailable("sphn not installed")

    async def _probe():
        import websockets
        q = urllib.parse.urlencode({
            "voice_prompt": voice_prompt, "text_prompt": text_prompt, "seed": "-1"})
        sep = "&" if "?" in url else "?"
        ctx = None
        if url.startswith("wss://"):
            ctx = ssl.create_default_context()
            if ssl_insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
        t0 = time.time()
        async with websockets.connect(f"{url}{sep}{q}", ssl=ctx,
                                      max_size=None, open_timeout=timeout_s) as ws:
            elapsed = time.time() - t0
            # one small opus frame round-trip is not required; handshake is enough
            return elapsed

    return asyncio.run(_probe())
