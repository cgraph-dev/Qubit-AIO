"""faster-whisper ASR (active default on this box).

Uses the GPU when possible (cuda + float16). On this machine GPU support needs
the CUDA 12 libraries that ship with Ollama at /usr/local/lib/ollama/cuda_v12;
`bootstrap_cuda_libs()` sets LD_LIBRARY_PATH before ctranslate2 is imported.
If CUDA is unavailable or OOMs, we transparently fall back to CPU and log an
observability event so the operator knows ASR is slower.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger, log_event
from .base import ASRProvider, ASRUnavailable, Segment, Transcript

log = get_logger()

_CUDA_LIB_CANDIDATES = [
    "/usr/local/lib/ollama/cuda_v12",   # Ollama-bundled CUDA 12 (this box)
    "/usr/lib/x86_64-linux-gnu",        # distro libs
]


def bootstrap_cuda_libs() -> None:
    """Prepend a directory containing libcublas.so.12 to LD_LIBRARY_PATH.

    ctranslate2 (faster-whisper backend) needs libcublas.so.12 at import time.
    On this machine the only copy lives inside the Ollama install, which is not
    on the linker path — that is why GPU ASR silently never worked here before.
    """
    if os.environ.get("LD_LIBRARY_PATH"):
        existing = os.environ["LD_LIBRARY_PATH"]
        for cand in existing.split(":"):
            if os.path.exists(os.path.join(cand, "libcublas.so.12")):
                return  # already good
    for cand in _CUDA_LIB_CANDIDATES:
        if os.path.exists(os.path.join(cand, "libcublas.so.12")):
            os.environ["LD_LIBRARY_PATH"] = cand + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
            log_event("asr.cuda.bootstrap", lib_dir=cand)
            return
    log_event("asr.cuda.bootstrap", lib_dir=None, warning="libcublas.so.12 not found")


class FasterWhisperASR(ASRProvider):
    name = "whisper"

    def __init__(self, model: str = "base", device: str = "auto",
                 compute_type: str = "float16", language: Optional[str] = None,
                 beam_size: int = 1, model_dir: Optional[Path] = None) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.model_dir = model_dir
        self._model = None
        self._actual_device: Optional[str] = None
        self._lock = threading.Lock()

    def _import(self):
        bootstrap_cuda_libs()  # before faster_whisper/ctranslate2 import
        from faster_whisper import WhisperModel
        return WhisperModel

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            WhisperModel = self._import()
            device = self.device
            if device == "auto":
                device = "cuda"
            # try: cuda fp16 -> cuda int8 (smaller workspace) -> cpu
            attempts = [(device, self.compute_type)]
            if device == "cuda":
                attempts = [(device, "float16"), (device, "int8"), ("cpu", "int8")]
            last_err: Optional[Exception] = None
            for dev, ctype in attempts:
                try:
                    self._model = WhisperModel(self.model_name, device=dev,
                                               compute_type=ctype,
                                               download_root=str(self.model_dir) if self.model_dir else None)
                    self._actual_device = dev
                    log_event("asr.load", provider=self.name, model=self.model_name,
                              device=dev, compute_type=ctype)
                    return
                except Exception as exc:
                    last_err = exc
                    log_event("asr.load.fallback", device=dev, compute_type=ctype,
                              error=str(exc)[:160])
            raise ASRUnavailable(f"whisper load failed on all devices: {last_err}")

    def transcribe(self, audio_path: Path | str) -> Transcript:
        self.load()
        with self._lock:
            segments_iter, info = self._model.transcribe(
                str(audio_path), beam_size=self.beam_size, language=self.language)
            segments = list(segments_iter)
        text = "".join(s.text for s in segments).strip()
        segs = [Segment(s.start, s.end, s.text, getattr(s, "avg_logprob", None)) for s in segments]
        conf = 0.0
        if segments:
            probs = [getattr(s, "avg_logprob", None) for s in segments]
            probs = [p for p in probs if p is not None]
            if probs:
                conf = max(0.0, min(1.0, (sum(probs) / len(probs) + 1.0) / 2.0))
        log_event("asr.transcribe", provider=self.name, device=self._actual_device,
                  text=text, confidence=round(conf, 3), duration_s=info.duration)
        return Transcript(text=text, language=getattr(info, "language", self.language),
                          confidence=conf, duration_s=info.duration,
                          segments=segs, provider=self.name)

    def available(self) -> bool:
        try:
            self.load()
            return True
        except Exception:
            return False

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._actual_device = None
