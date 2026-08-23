"""Piper TTS — fully local, CPU-only, lightweight (the active default).

On this box: ~0.9 s one-time load, synthesis faster than real time (a 50-word
sentence renders in ~0.25 s). Runs on CPU so it never competes with the GPU
(whisper ASR / Ollama agent models).
"""
from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger, log_event
from .base import TTSProvider, TTSUnavailable

log = get_logger()

# espeak-ng (piper's phonemizer) is a process-global C library and is NOT
# thread-safe. Two PiperVoice instances (e.g. the orchestrator's progress
# thread + a test/main-thread synth) synthesizing concurrently segfaults in
# the espeakbridge C extension. One global lock serializes ALL synthesis.
_GLOBAL_TTS_LOCK = threading.Lock()

# ENGLISH ONLY (user decision): no per-language voice switching, no secondary
# voices. The single configured voice speaks everything.


class PiperTTS(TTSProvider):
    name = "piper"

    def __init__(self, model_dir: Path, voice: str = "en_US-amy-medium",
                 sample_rate: int = 22050) -> None:
        self.model_dir = Path(model_dir)
        self.voice = voice
        self.sample_rate = sample_rate
        self._voices: dict[str, object] = {}   # voice cache {voice_name: PiperVoice}
        self._lock = threading.Lock()

    def _model_paths(self, voice: Optional[str] = None) -> tuple[Path, Path]:
        v = voice or self.voice
        onnx = self.model_dir / f"{v}.onnx"
        jsonp = self.model_dir / f"{v}.onnx.json"
        if not onnx.exists():
            raise TTSUnavailable(
                f"piper voice not found: {onnx}\n"
                "Download it (see README): "
                "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
                f"en/en_US/{v.split('-', 2)[2] if '-' in v else 'medium'}/"
                f"{v}.onnx")
        return onnx, jsonp

    def _get_voice(self, voice_name: str):
        """Load (and cache) a PiperVoice for the given voice name."""
        with self._lock:
            if voice_name in self._voices:
                return self._voices[voice_name]
            try:
                from piper import PiperVoice
            except Exception as exc:  # pragma: no cover
                raise TTSUnavailable(f"piper-tts not installed: {exc}") from exc
            onnx, _ = self._model_paths(voice_name)
            obj = PiperVoice.load(str(onnx))
            self._voices[voice_name] = obj
            log_event("tts.load", provider=self.name, voice=voice_name)
            return obj

    def load(self) -> None:
        """Preload the default voice (lazy-loads others on first use)."""
        self._get_voice(self.voice)

    def synthesize(self, text: str, out_path: Optional[Path | str] = None,
                   language: Optional[str] = None) -> Path:
        # ENGLISH ONLY: single voice, no per-language switching (language arg
        # kept for API compatibility and ignored).
        obj = self._get_voice(self.voice)
        out_path = Path(out_path) if out_path else Path(f"/tmp/openclaw-voice-bridge/tts-{abs(hash(text))%10**8}.wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with _GLOBAL_TTS_LOCK:
            with self._lock:
                with wave.open(str(out_path), "wb") as wf:
                    obj.synthesize_wav(text, wf)
        log_event("tts.synthesize", provider=self.name, chars=len(text),
                  path=str(out_path), voice=self.voice)
        return out_path

    def available(self) -> bool:
        try:
            self._model_paths()
            return True
        except Exception:
            return False
