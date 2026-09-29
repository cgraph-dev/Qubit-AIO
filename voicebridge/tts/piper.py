"""Piper TTS — fully local, CPU-only, lightweight (the active default).

Two interchangeable backends render the SAME Piper voice, selected by
``tts.piper_backend`` (or auto-detected):

* ``sherpa`` (default when the pack is present) — ``sherpa_onnx.OfflineTts``.
  This uses **sherpa-onnx's own ONNX Runtime**, the same library the ASR
  streaming engine uses, so the process loads ONE ORT and there is no
  load-order coupling between TTS and ASR (the reason the ASR prewarm had to run
  before Piper's ``onnxruntime`` import).
* ``piper`` — the third-party ``piper-tts`` package (imports the Python
  ``onnxruntime``). Kept as a switchable fallback.

Both are CPU-only. ``sherpa`` needs the ``vits-piper-<voice>`` pack
(``en_US-amy-medium.onnx`` + ``tokens.txt`` + ``espeak-ng-data``); the installer
downloads it. If it is missing the backend falls back to ``piper``.
"""
from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from ..logging_setup import get_logger, log_event
from .base import TTSProvider, TTSUnavailable

log = get_logger()

# espeak-ng (piper's phonemizer) is a process-global C library and is NOT
# thread-safe. Two voices synthesizing concurrently can segfault. One global
# lock serializes ALL synthesis (both backends).
_GLOBAL_TTS_LOCK = threading.Lock()

# ENGLISH ONLY (user decision): no per-language voice switching, no secondary
# voices. The single configured voice speaks everything.


class PiperTTS(TTSProvider):
    name = "piper"

    def __init__(self, model_dir: Path, voice: str = "en_US-amy-medium",
                 sample_rate: int = 22050,
                 backend: Optional[str] = None) -> None:
        self.model_dir = Path(model_dir)
        self.voice = voice
        self.sample_rate = sample_rate
        self.backend = (backend or "").strip().lower() or None
        self._voices: dict[str, object] = {}   # voice cache {voice_name: PiperVoice}
        self._sherpa: object | None = None     # cached OfflineTts
        self._lock = threading.Lock()

    # -- backend selection ------------------------------------------------
    def _sherpa_pack_dir(self, voice: Optional[str] = None) -> Optional[Path]:
        v = voice or self.voice
        for base in (self.model_dir, Path("/tmp/qubit-voicebridge/tts-models")):
            p = base / f"vits-piper-{v}"
            if (p / f"{v}.onnx").exists() and (p / "tokens.txt").exists():
                return p
        return None

    def _resolve_backend(self) -> str:
        if self.backend in ("sherpa", "piper"):
            if self.backend == "sherpa" and self._sherpa_pack_dir() is None:
                return "piper"   # requested but unavailable -> safe fallback
            return self.backend
        return "sherpa" if self._sherpa_pack_dir() is not None else "piper"

    # -- piper-tts backend ------------------------------------------------
    def _model_paths(self, voice: Optional[str] = None) -> tuple[Path, Path]:
        v = voice or self.voice
        onnx = self.model_dir / f"{v}.onnx"
        jsonp = self.model_dir / f"{v}.onnx.json"
        if not onnx.exists():
            # Read-only compat: an unmigrated install keeps working from the
            # legacy model copy; downloads still target the canonical tree.
            from ..config import resolve_model_file
            legacy_onnx = resolve_model_file(Path(f"{v}.onnx"))
            if legacy_onnx.exists() and str(legacy_onnx) != str(onnx):
                return legacy_onnx, legacy_onnx.parent / f"{v}.onnx.json"
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
            log_event("tts.load", provider=self.name, backend="piper",
                      voice=voice_name)
            return obj

    # -- sherpa-onnx backend (shared ORT) ---------------------------------
    def _get_sherpa_tts(self):
        with self._lock:
            if self._sherpa is not None:
                return self._sherpa
            try:
                import sherpa_onnx as so
            except Exception as exc:  # pragma: no cover
                raise TTSUnavailable(
                    f"sherpa-onnx not installed: {exc}") from exc
            pack = self._sherpa_pack_dir()
            if pack is None:
                raise TTSUnavailable(
                    f"sherpa piper voice pack not found under {self.model_dir} "
                    f"(vits-piper-{self.voice})")
            vits = so.OfflineTtsVitsModelConfig(
                model=str(pack / f"{self.voice}.onnx"),
                tokens=str(pack / "tokens.txt"),
                data_dir=str(pack),
            )
            cfg = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
                vits=vits, num_threads=2, provider="cpu"))
            self._sherpa = so.OfflineTts(cfg)
            log_event("tts.load", provider=self.name, backend="sherpa",
                      voice=self.voice)
            return self._sherpa

    def load(self) -> None:
        """Preload the default voice (lazy-loads others on first use)."""
        if self._resolve_backend() == "sherpa":
            self._get_sherpa_tts()
        else:
            self._get_voice(self.voice)

    def synthesize(self, text: str, out_path: Optional[Path | str] = None,
                   language: Optional[str] = None) -> Path:
        # ENGLISH ONLY: single voice, no per-language switching (language arg
        # kept for API compatibility and ignored).
        out_path = Path(out_path) if out_path else Path(
            f"/tmp/qubit-voicebridge/tts-{abs(hash(text))%10**8}.wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        backend = self._resolve_backend()
        if backend == "sherpa":
            tts = self._get_sherpa_tts()
            with _GLOBAL_TTS_LOCK:
                audio = tts.generate(text, sid=0, speed=1.0)
            samples = np.asarray(audio.samples, dtype=np.float32)
            sr = int(audio.sample_rate) or self.sample_rate
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(pcm)
        else:
            obj = self._get_voice(self.voice)
            with _GLOBAL_TTS_LOCK:
                with self._lock:
                    with wave.open(str(out_path), "wb") as wf:
                        obj.synthesize_wav(text, wf)
        log_event("tts.synthesize", provider=self.name, backend=backend,
                  chars=len(text), path=str(out_path), voice=self.voice)
        return out_path

    def available(self) -> bool:
        try:
            if self._resolve_backend() == "sherpa":
                return self._sherpa_pack_dir() is not None
            self._model_paths()
            return True
        except Exception:
            return False
