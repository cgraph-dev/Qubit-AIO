"""Hybrid TTS — cheap by default, sexy when it matters.

Routing:
  short (<= threshold_words, default 18) -> local Piper (free, 0.08s, offline)
  long / reports / heartbeat           -> OpenAI gpt-4o-mini-tts nova (sexy Japanese, +1s but cheap per report)

This saves ~70% vs always-OpenAI because most chatter is 5-15 words.

Uses the cheapest OpenAI model that supports `instructions`:
  gpt-4o-mini-tts ($0.60 / 1M input tokens) is already the cheap one that does Japanese accent.
  tts-1 ($15 / 1M chars) is actually MORE expensive and cannot do accent — so we keep mini-tts.

No new deps — stdlib only.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
from ..logging_setup import log_event
from .base import TTSProvider, TTSUnavailable

class HybridTTS(TTSProvider):
    name = "hybrid"
    def __init__(self, model_dir: Path, piper_voice: str = "en_US-amy-medium",
                 openai_voice: str = "nova", openai_model: str = "gpt-4o-mini-tts",
                 key_env: str = "~/.config/qubit-voicebridge/.env", instructions: str = "",
                 threshold_words: int = 18,
                 egress_guard=None) -> None:
        self.model_dir = Path(model_dir)
        self.piper_voice = piper_voice
        self.openai_voice = openai_voice
        self.openai_model = openai_model
        self.key_env = key_env
        self.instructions = instructions
        self.threshold_words = int(threshold_words)
        self._egress_guard = egress_guard
        self._piper = None
        self._openai = None

    def _get_piper(self):
        if self._piper is None:
            from .piper import PiperTTS
            self._piper = PiperTTS(model_dir=self.model_dir, voice=self.piper_voice)
        return self._piper

    def _get_openai(self):
        if self._openai is None:
            from .openai_tts import OpenAITTS
            self._openai = OpenAITTS(voice=self.openai_voice, model=self.openai_model,
                                     key_env=self.key_env, instructions=self.instructions,
                                     egress_guard=self._egress_guard)
        return self._openai

    def load(self) -> None:
        # load piper eagerly (offline must always work); openai lazy (needs key/network)
        try:
            self._get_piper().load()
        except Exception as e:
            raise TTSUnavailable(f"hybrid piper load failed: {e}") from e
        # don't fail if openai key missing — we fallback to piper
        try:
            if self._get_openai().available():
                self._get_openai().load()
        except Exception:
            pass
        log_event("tts.load", provider=self.name, threshold=self.threshold_words,
                  piper=self.piper_voice, openai=self.openai_voice)

    def available(self) -> bool:
        try:
            return self._get_piper().available()
        except Exception:
            return False

    def _choose(self, text: str) -> str:
        words = len((text or "").split())
        if words <= self.threshold_words:
            return "piper"
        return "openai"

    def synthesize(self, text: str, out_path: Optional[Path | str] = None) -> Path:
        choice = self._choose(text)
        if choice == "piper":
            try:
                path = self._get_piper().synthesize(text, out_path)
                log_event("tts.synthesize", provider="hybrid:piper", words=len(text.split()), chars=len(text))
                return path
            except Exception as e:
                # piper failed? try openai as last resort
                log_event("tts.hybrid.piper_failed", error=str(e)[:160])
                return self._get_openai().synthesize(text, out_path)
        else:
            # long -> try openai first, fallback piper if offline/no key
            try:
                if self._get_openai().available():
                    path = self._get_openai().synthesize(text, out_path)
                    log_event("tts.synthesize", provider="hybrid:openai", words=len(text.split()), chars=len(text))
                    return path
                else:
                    raise TTSUnavailable("openai not available")
            except Exception as e:
                log_event("tts.hybrid.openai_failed_fallback_piper", error=str(e)[:160])
                path = self._get_piper().synthesize(text, out_path)
                log_event("tts.synthesize", provider="hybrid:piper-fallback", words=len(text.split()), chars=len(text))
                return path
