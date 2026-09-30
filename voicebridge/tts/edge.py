"""Edge TTS (optional, network). Microsoft's free neural voices.

Requires network access and ffmpeg to convert the mp3 output to WAV.
Not the default — included as a higher-quality *optional* output layer.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from ..logging_setup import log_event
from .base import TTSProvider, TTSUnavailable


class EdgeTTS(TTSProvider):
    name = "edge"

    def __init__(self, voice: str = "en-US-JennyNeural", rate: str = "+0%",
                 egress_guard=None) -> None:
        self.voice = voice
        self.rate = rate
        # Qubit egress/emergency-stop gate consulted before every network call.
        self._egress_guard = egress_guard

    def load(self) -> None:
        try:
            import edge_tts  # noqa: F401
        except Exception as exc:
            raise TTSUnavailable("edge-tts not installed (pip install edge-tts)") from exc
        if shutil.which("ffmpeg") is None:
            raise TTSUnavailable("ffmpeg not found; needed to convert edge-tts mp3 -> wav")

    def synthesize(self, text: str, out_path: Optional[Path | str] = None) -> Path:
        if self._egress_guard is not None and not self._egress_guard():
            raise TTSUnavailable(
                "external Edge TTS refused by Qubit egress/emergency-stop "
                "policy; use the local TTS provider or clear the stop")
        import edge_tts
        import asyncio
        self.load()
        out_path = Path(out_path) if out_path else Path(f"/tmp/qubit-voicebridge/tts-edge-{abs(hash(text))%10**8}.wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        async def _synth():
            communicate = edge_tts.Communicate(text, voice=self.voice, rate=self.rate)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                mp3_path = tmp.name
            await communicate.save(mp3_path)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path,
                            "-ar", "22050", "-ac", "1", str(out_path)], check=True)
            Path(mp3_path).unlink(missing_ok=True)

        asyncio.run(_synth())
        log_event("tts.synthesize", provider=self.name, voice=self.voice, chars=len(text))
        return out_path
