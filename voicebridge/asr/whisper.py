"""faster-whisper ASR (active default on this box).

Uses the GPU when possible (cuda + float16). On this machine GPU support needs
the CUDA 12 libraries that ship with Ollama at /usr/local/lib/ollama/cuda_v12;
`bootstrap_cuda_libs()` sets LD_LIBRARY_PATH before ctranslate2 is imported.

VRAM is precious on 8 GB: the load plan is chosen from the *current* free VRAM
(never blind tries), `model: auto` picks small when the GPU is free and base
when an Ollama model is hot, and a transcribe-time OOM (another process grabbed
VRAM mid-session) retries on CPU instead of crashing.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from ..logging_setup import get_logger, log_event
from .base import ASRProvider, ASRUnavailable, Segment, Transcript

log = get_logger()

_CUDA_LIB_CANDIDATES = [
    "/usr/local/lib/ollama/cuda_v12",   # Ollama-bundled CUDA 12 (this box)
    "/usr/lib/x86_64-linux-gnu",        # distro libs
]

# free-VRAM thresholds (MiB): small fp16 ~2GB, small int8 ~1.1GB,
# base fp16 ~0.5GB, base int8 ~0.3GB (+desktop ~1.1GB baseline)
_AUTO_SMALL_MB = 2000
_CUDA_FP16_MB = 1500
_CUDA_INT8_MB = 800


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
        self.model_name = model          # "auto" picks small/base from free VRAM
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.model_dir = model_dir
        self._model = None
        self._actual_device: Optional[str] = None
        self._actual_model: Optional[str] = None
        self._force_cpu = False          # set after a transcribe OOM retry
        self._lock = threading.Lock()

    def _import(self):
        bootstrap_cuda_libs()  # before faster_whisper/ctranslate2 import
        from faster_whisper import WhisperModel
        return WhisperModel

    @staticmethod
    def _free_vram_mb() -> Optional[int]:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            return int(out.splitlines()[0].split(",")[0].strip())
        except Exception:
            return None

    def _plan(self) -> List[Tuple[str, str, str]]:
        """(device, model, compute_type) attempts chosen from current free VRAM."""
        free = self._free_vram_mb()
        model = self.model_name
        if model == "auto":
            model = "small" if (free or 0) >= _AUTO_SMALL_MB else "base"
        if free is None:
            # No GPU detected (headless/no nvidia): don't burn a CUDA load cycle,
            # go straight to CPU int8 (fast fallback) — avoids double model init.
            return [("cpu", model, "int8")]
        if self._force_cpu:
            return [("cpu", model, "int8")]
        attempts: List[Tuple[str, str, str]] = []
        if free >= _CUDA_FP16_MB:
            attempts.append(("cuda", model, "float16"))
        elif free >= _CUDA_INT8_MB:
            attempts.append(("cuda", model, "int8"))
        attempts.append(("cpu", model, "int8"))
        return attempts

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            WhisperModel = self._import()
            attempts = self._plan()
            last_err: Optional[Exception] = None
            for dev, model, ctype in attempts:
                try:
                    self._model = WhisperModel(
                        model, device=dev, compute_type=ctype,
                        download_root=str(self.model_dir) if self.model_dir else None)
                    self._actual_device = dev
                    self._actual_model = model
                    log_event("asr.load", provider=self.name, model=model,
                              device=dev, compute_type=ctype,
                              free_vram_mb=self._free_vram_mb())
                    return
                except Exception as exc:
                    last_err = exc
                    reason = ("cuda oom (gpu busy)" if dev == "cuda"
                              and "out of memory" in str(exc).lower() else str(exc)[:120])
                    log_event("asr.load.fallback", device=dev, model=model,
                              compute_type=ctype, reason=reason)
            raise ASRUnavailable(f"whisper load failed on all devices: {last_err}")

    def _transcribe_once(self, audio_path: Path | str):
        if self._model is None:
            raise ASRUnavailable("asr not loaded")
        segments_iter, info = self._model.transcribe(
            str(audio_path), beam_size=self.beam_size, language=self.language)
        return list(segments_iter), info

    def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000) -> str:
        """Whole-utterance decode of mono int16 PCM at sample_rate (resampled to 16k)."""
        import numpy as np
        self.load()
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        rate = int(sample_rate or 16000)
        if rate != 16000 and audio.size:
            n = int(round(audio.size * 16000 / rate))
            if n > 0:
                x_old = np.arange(audio.size, dtype=np.float64)
                x_new = np.linspace(0.0, audio.size - 1, num=n, dtype=np.float64)
                audio = np.interp(x_new, x_old, audio).astype(np.float32)
        with self._lock:
            segments, _info = self._model.transcribe(
                np.ascontiguousarray(audio), beam_size=self.beam_size,
                language=self.language, vad_filter=True,
                condition_on_previous_text=False)
            return "".join(s.text for s in segments).strip()

    def transcribe(self, audio_path: Path | str) -> Transcript:
        self.load()
        with self._lock:
            try:
                segments, info = self._transcribe_once(audio_path)
            except RuntimeError as exc:
                if "out of memory" in str(exc).lower() and self._actual_device == "cuda":
                    # another process (e.g. Ollama) grabbed VRAM mid-session:
                    # reload on CPU and retry once instead of crashing
                    log_event("asr.transcribe.oom", device=self._actual_device,
                              action="retry on cpu")
                    self.unload()
                    self._force_cpu = True
                    self.load()
                    segments, info = self._transcribe_once(audio_path)
                else:
                    raise
        text = "".join(s.text for s in segments).strip()
        segs = [Segment(s.start, s.end, s.text, getattr(s, "avg_logprob", None)) for s in segments]
        conf = 0.0
        if segments:
            probs = [getattr(s, "avg_logprob", None) for s in segments]
            probs = [p for p in probs if p is not None]
            if probs:
                conf = max(0.0, min(1.0, (sum(probs) / len(probs) + 1.0) / 2.0))
        log_event("asr.transcribe", provider=self.name, device=self._actual_device,
                  model=self._actual_model, text=text,
                  confidence=round(conf, 3), duration_s=info.duration)
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
            self._actual_model = None
