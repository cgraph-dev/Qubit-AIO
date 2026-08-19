"""Gateway TTS (optional): reuse OpenClaw's own configured TTS providers.

The OpenClaw gateway exposes `tts.speak` over the WS protocol (verified live:
openai gpt-4o-mini-tts and microsoft/edge providers are configured on this
box). This adapter calls that RPC and plays the returned audio — i.e. the
bridge reuses the OpenClaw speech stack instead of its own local piper.

Note: gateway TTS providers are network-backed (OpenAI/Edge), so this is
*optional*; piper stays the offline default. Requires a GatewayClient.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..logging_setup import log_event
from .base import TTSProvider, TTSUnavailable

if TYPE_CHECKING:
    from ..openclaw.gateway_ws import GatewayClient


class GatewayTTS(TTSProvider):
    name = "gateway"

    def __init__(self, gateway: "GatewayClient", model: Optional[str] = None) -> None:
        self.gateway = gateway
        self.model = model

    def load(self) -> None:
        # Lazily verified at synthesize time (gateway may reconnect).
        pass

    def synthesize(self, text: str, out_path: Optional[Path | str] = None) -> Path:
        if not self.gateway.connected:
            raise TTSUnavailable("gateway not connected for gateway TTS")
        result = self.gateway.tts_speak(text)
        if not result:
            raise TTSUnavailable("gateway tts.speak returned nothing")
        audio_b64, _mime, fmt = result
        out_path = Path(out_path) if out_path else Path(
            f"/tmp/openclaw-voice-bridge/tts-gw-{abs(hash(text))%10**8}.wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(audio_b64)
        # Gateway returns raw PCM (pcm16) or encoded audio; wrap PCM in WAV.
        if fmt and fmt.startswith("pcm"):
            sr = int(fmt.split(":")[1]) if ":" in fmt else 24000
            import wave
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(data)
        else:
            out_path.write_bytes(data)
        log_event("tts.synthesize", provider=self.name, chars=len(text), fmt=fmt)
        return out_path
