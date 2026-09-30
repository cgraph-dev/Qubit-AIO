"""OpenAI TTS — highest-quality network voice (optional).

Uses the OpenAI audio/speech API (gpt-4o-mini-tts by default, gpt-4o-tts for
premium). Voices: alloy, ash, ballad, coral, echo, fable, onyx, nova,
shimmer, verse. Nova is the default — warm, natural, the user\'s previous
choice.

Requires OPENAI_API_KEY in ~/.config/qubit-voicebridge/.env or the environment, and
VOICE_TOOLS_OPENAI_KEY also accepted (legacy name on this box).
Network-backed: piper stays the offline default; this is opt-in via
tts.provider: openai.

No new pip deps — uses stdlib urllib so it works without the `openai` package.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..logging_setup import log_event
from .base import TTSProvider, TTSUnavailable

DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "nova"  # nova = warm female; best for soft/sexy Japanese accent
# gpt-4o-mini-tts instructions control accent/style (ignored by older tts-1)
DEFAULT_INSTRUCTIONS = "Speak with a warm, soft and sexy tone, with a subtle, elegant Japanese accent. Gentle, breathy and intimate, slightly playful — like a caring Japanese girlfriend. Speak naturally, at a relaxed pace, clear and affectionate."

def _read_key(env_path: Optional[Path] = None) -> str:
    # try env first
    for name in ("OPENAI_API_KEY", "VOICE_TOOLS_OPENAI_KEY"):
        v = os.environ.get(name, "").strip().strip('"').strip("'")
        if v:
            return v
    # then the canonical Qubit runtime .env. The operator's ~/.hermes/.env is
    # never consulted (read-only migration input, not a credential fallback —
    # DECISIONS #9/#17).
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    try:
        from .. import config as _cfg
        candidates.append(_cfg.canonical_runtime_env_path())
    except Exception:
        pass
    for p in candidates:
        try:
            for line in p.read_text(errors="replace").splitlines():
                line=line.strip()
                for name in ("OPENAI_API_KEY", "VOICE_TOOLS_OPENAI_KEY"):
                    if line.startswith(name + "="):
                        return line.split("=",1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ""

class OpenAITTS(TTSProvider):
    name = "openai"

    def __init__(self, voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL,
                 key_env: str = "~/.config/qubit-voicebridge/.env",
                 instructions: str = DEFAULT_INSTRUCTIONS,
                 egress_guard: Optional[Callable[[], bool]] = None) -> None:
        self.voice = voice or DEFAULT_VOICE
        self.model = model or DEFAULT_MODEL
        self.key_env = key_env
        self.instructions = instructions  # optional style instructions for gpt-4o-mini-tts
        # Qubit egress/emergency-stop gate consulted before every network call.
        self._egress_guard = egress_guard
        self._key: Optional[str] = None

    def load(self) -> None:
        key = _read_key(Path(self.key_env) if self.key_env else None)
        if not key:
            raise TTSUnavailable("OPENAI_API_KEY not found in env or the Qubit env file — set it to use openai TTS")
        self._key = key
        log_event("tts.load", provider=self.name, voice=self.voice, model=self.model)

    def available(self) -> bool:
        return bool(_read_key(Path(self.key_env) if self.key_env else None))

    def synthesize(self, text: str, out_path: Optional[Path | str] = None) -> Path:
        if not text or not text.strip():
            raise TTSUnavailable("empty text for openai TTS")
        if self._egress_guard is not None and not self._egress_guard():
            raise TTSUnavailable(
                "external OpenAI TTS refused by Qubit egress/emergency-stop "
                "policy; use the local TTS provider or clear the stop")
        if self._key is None:
            self.load()
        assert self._key
        out_path = Path(out_path) if out_path else Path(f"/tmp/qubit-voicebridge/tts-openai-{abs(hash(text))%10**8}.wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": "wav",
        }
        # gpt-4o-mini-tts supports instructions for style
        if self.instructions:
            payload["instructions"] = self.instructions

        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except Exception as exc:
            raise TTSUnavailable(f"openai TTS failed: {exc}") from exc
        # response is wav bytes when response_format=wav, otherwise mp3
        out_path.write_bytes(data)
        log_event("tts.synthesize", provider=self.name, voice=self.voice, chars=len(text), path=str(out_path))
        return out_path
