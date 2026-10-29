"""Whole-utterance accurate local STT for the voice-typing surface.

The streaming STT socket is tuned for low latency and hands back partials; this
module is the *accurate* decoder used once an utterance has ended. It reuses the
existing vendored faster-whisper provider (the model singleton cache, the
CUDA -> CPU fallback, the anti-hallucination kwargs and the segment confidence
gate) rather than introducing a second speech backend.

Nothing here is imported at module load: numpy, the CUDA preload and the
vendored ``tools.*`` provider are pulled in lazily inside the default loader, so
importing this module never opens a device, downloads a model, or touches the
network. Tests inject a ``loader`` seam and never load a model.

A failure is always typed: :meth:`AccurateTranscriber.transcribe` never raises
and never fabricates text.
"""
from __future__ import annotations

import importlib.util
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

ACCURATE_MODELS: tuple = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_ACCURATE_MODEL = "small"

_SAMPLE_RATE = 16000
_MAX_REASON = 160


@dataclass
class AccurateResult:
    text: str = ""
    ok: bool = False
    engine: str = ""      # e.g. "faster-whisper-small"
    device: str = ""      # "cuda" | "cpu" | ""
    reason: str = ""      # typed fallback reason when not ok


def _reason(message: Any) -> str:
    """A short, single-line, typed failure reason (never a traceback)."""
    text = " ".join(str(message or "").split())
    return text[:_MAX_REASON] or "accurate transcription failed"


def _resolve_model(cfg: Any) -> str:
    """Resolve the configured model, falling back to the documented default."""
    try:
        name = cfg.get("voice_edit", "stt_model", default=DEFAULT_ACCURATE_MODEL)
    except Exception:
        name = DEFAULT_ACCURATE_MODEL
    name = str(name or "").strip()
    return name if name in ACCURATE_MODELS else DEFAULT_ACCURATE_MODEL


def _preload_cuda() -> None:
    """RTLD_GLOBAL-preload the pip CUDA libs before ctranslate2 imports.

    The vendored ``bootstrap_cuda_libs()`` only sets ``LD_LIBRARY_PATH`` after
    the process has started, which glibc ignores; this is the one that works.
    """
    try:
        from ..asr.gpu import ensure_cuda_libraries
        ensure_cuda_libraries()
    except Exception:
        pass


def _ensure_runtime_path() -> None:
    from ..qubit_runtime import ensure_path
    ensure_path()


def _offline_model_id(cfg: Any, name: str) -> str:
    """Prefer a downloaded snapshot dir so no network access is needed."""
    try:
        root = (Path(cfg.model_dir())
                / f"models--Systran--faster-whisper-{name}" / "snapshots")
        for snap in sorted(root.iterdir()):
            if (snap / "model.bin").is_file():
                return str(snap)
    except Exception:
        pass
    return name


def _resample(np: Any, audio: Any, src_rate: int, dst_rate: int) -> Any:
    """Linear-interpolation resample of a mono float32 signal."""
    if src_rate <= 0 or dst_rate <= 0 or audio.size == 0:
        return audio
    n = int(round(audio.size * dst_rate / src_rate))
    if n <= 0:
        return audio
    x_old = np.arange(audio.size, dtype=np.float64)
    x_new = np.linspace(0.0, audio.size - 1, num=n, dtype=np.float64)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _pcm_to_float(np: Any, pcm: bytes, sample_rate: int) -> Any:
    """Mono int16 PCM -> float32 at 16 kHz."""
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    rate = int(sample_rate or _SAMPLE_RATE)
    if rate != _SAMPLE_RATE and audio.size:
        audio = _resample(np, audio, rate, _SAMPLE_RATE)
    return np.ascontiguousarray(audio)


_OOM_MARKERS = ("out of memory", "out-of-memory", "OOM",
                "cudaErrorMemoryAllocation", "BFCArena",
                "memory for requested buffer")


def _looks_like_oom(exc: BaseException) -> bool:
    """Heuristic: did this fail for lack of VRAM (not a broken install)?"""
    text = str(exc)
    return any(marker.lower() in text.lower() for marker in _OOM_MARKERS)


def _default_loader(cfg: Any, model_name: str, pcm: bytes,
                    sample_rate: int) -> AccurateResult:
    """Decode a whole utterance with the vendored faster-whisper provider.

    Never raises: every failure is returned as a typed :class:`AccurateResult`.
    """
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - numpy is a hard dependency
        return AccurateResult(ok=False, reason=_reason(f"numpy unavailable: {exc}"))
    _preload_cuda()
    try:
        _ensure_runtime_path()
        from tools.transcription_local import (
            _normalize_local_model, build_local_transcribe_kwargs,
            _join_confident_segments, _looks_like_cuda_lib_error)
        from tools.transcription_tools import _get_or_load_local_model
    except Exception as exc:
        return AccurateResult(
            ok=False, reason=_reason(f"faster-whisper unavailable: {exc}"))
    name = _normalize_local_model(model_name) or DEFAULT_ACCURATE_MODEL
    model_id = _offline_model_id(cfg, name)
    try:
        model = _get_or_load_local_model(model_id, {})
    except Exception as exc:
        # A full GPU is routine on 8 GB cards shared with Ollama + the
        # streaming engine: retry the load on CPU instead of failing the
        # rescore (finals tolerate CPU latency; realtime never uses this).
        if _looks_like_oom(exc):
            try:
                from tools.transcription_tools import (
                    _replace_cached_model_on_cpu)
                model = _replace_cached_model_on_cpu(model_id)
            except Exception as exc2:
                return AccurateResult(
                    ok=False,
                    reason=_reason(f"faster-whisper oom, cpu retry failed: {exc2}"))
        else:
            return AccurateResult(
                ok=False, reason=_reason(f"faster-whisper load failed: {exc}"))
    audio = _pcm_to_float(np, pcm, sample_rate)
    kwargs = build_local_transcribe_kwargs()
    try:
        segments, _info = model.transcribe(audio, **kwargs)
    except Exception as exc:
        if not (_looks_like_cuda_lib_error(exc) or _looks_like_oom(exc)):
            return AccurateResult(
                ok=False,
                reason=_reason(f"faster-whisper transcribe failed: {exc}"))
        try:
            from tools.transcription_tools import _replace_cached_model_on_cpu
            model = _replace_cached_model_on_cpu(model_id)
            segments, _info = model.transcribe(audio, **kwargs)
        except Exception as exc2:
            return AccurateResult(
                ok=False,
                reason=_reason(f"faster-whisper cuda failed, cpu retry failed: {exc2}"))
    text = _join_confident_segments(segments, {})
    device = getattr(getattr(model, "model", None), "device", "") or ""
    return AccurateResult(text=text.strip(), ok=True,
                          engine=f"faster-whisper-{name}", device=str(device))


#: Cached whole-utterance Sherpa recognizers (the browser local-STT engine).
_SHERPA_CACHE: Dict[Tuple[str, str, str, str], Any] = {}
_SHERPA_LOCK = threading.Lock()


def _asr_section(cfg: Any) -> Dict[str, Any]:
    try:
        got = cfg.section("asr")
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}


def _sherpa_key(cfg: Any, engine: str, provider_ep: str) -> Tuple[str, str, str, str]:
    try:
        path = str(getattr(cfg, "path", "") or id(cfg))
    except Exception:
        path = str(id(cfg))
    try:
        models = str(cfg.model_dir())
    except Exception:
        models = ""
    return (path, engine, provider_ep, models)


def _sherpa_settings(cfg: Any) -> Tuple[str, str, Dict[str, Any]]:
    """(engine, provider_ep, asr-section) or ('', '', {}) when not usable."""
    asr = _asr_section(cfg)
    if str(asr.get("provider", "sherpa") or "sherpa").lower() != "sherpa":
        return "", "", {}
    engine = str(asr.get("engine", "") or "").strip().lower()
    if engine not in ("parakeet", "moonshine"):
        return "", "", {}
    return engine, str(asr.get("provider_ep", "cpu") or "cpu"), asr


def _get_sherpa(cfg: Any, engine: str, provider_ep: str,
                asr: Dict[str, Any]) -> Any:
    key = _sherpa_key(cfg, engine, provider_ep)
    with _SHERPA_LOCK:
        inst = _SHERPA_CACHE.get(key)
        if inst is None:
            from ..asr.sherpa import SherpaASR
            inst = SherpaASR(
                engine=engine, model_dir=cfg.model_dir(),
                provider=provider_ep,
                num_threads=int(asr.get("num_threads", 4) or 4),
                language=asr.get("language"),
                hotwords=str(asr.get("hotwords", "") or ""),
                hotwords_score=float(asr.get("hotwords_score", 1.5) or 1.5))
            _SHERPA_CACHE[key] = inst
        return inst


def prewarm_sherpa(cfg: Any) -> bool:
    """Load the configured Sherpa decoder now (never raises)."""
    engine, provider_ep, asr = _sherpa_settings(cfg)
    if not engine:
        return False
    try:
        _get_sherpa(cfg, engine, provider_ep, asr).load()
        return True
    except Exception:
        return False


def transcribe_sherpa(cfg: Any, pcm: bytes, *,
                      sample_rate: int = 16000) -> Tuple[str, str]:
    """Whole-utterance decode with the configured Sherpa engine (parakeet).

    This is the same offline path the browser's local STT uses. Returns
    ``(text, "")`` on success or ``("", reason)``; never raises.
    """
    if not pcm:
        return "", "empty audio"
    engine, provider_ep, asr = _sherpa_settings(cfg)
    if not engine:
        return "", "sherpa engine not configured"
    try:
        inst = _get_sherpa(cfg, engine, provider_ep, asr)
        import numpy as np
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        text = str(inst.decode_pcm(audio, sample_rate) or "").strip()
        return (text, "") if text else ("", "empty sherpa transcript")
    except Exception as exc:
        return "", _reason(f"sherpa transcribe failed: {exc}")


def reset_sherpa_cache() -> None:
    with _SHERPA_LOCK:
        _SHERPA_CACHE.clear()


class AccurateTranscriber:
    """Whole-utterance accurate decoder with a model singleton per config."""

    def __init__(self, cfg: Any, *, model: str = "",
                 loader: Optional[Callable[..., AccurateResult]] = None) -> None:
        self._cfg = cfg
        self._loader = loader or _default_loader
        self._model = model or _resolve_model(cfg)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> AccurateResult:
        """Decode an utterance; never raises, never fabricates text."""
        try:
            result = self._loader(self._cfg, self._model, pcm, sample_rate)
        except Exception as exc:
            return AccurateResult(ok=False, reason=_reason(exc))
        if isinstance(result, AccurateResult):
            return result
        return AccurateResult(ok=False,
                              reason=_reason("loader returned no result"))


_cache: Dict[Tuple[int, str, str], AccurateTranscriber] = {}
_cache_lock = threading.Lock()


def _cache_key(cfg: Any, model: str) -> Tuple[int, str, str]:
    try:
        path = str(getattr(cfg, "path", "") or "")
    except Exception:
        path = ""
    return (id(cfg), path, model)


def get_transcriber(cfg: Any) -> AccurateTranscriber:
    """Return the cached transcriber for this cfg/path/model triple."""
    model = _resolve_model(cfg)
    key = _cache_key(cfg, model)
    with _cache_lock:
        inst = _cache.get(key)
        if inst is None:
            inst = AccurateTranscriber(cfg, model=model)
            _cache[key] = inst
        return inst


def reset_transcribers() -> None:
    """Test helper: drop every cached transcriber."""
    with _cache_lock:
        _cache.clear()


def is_available(cfg: Any) -> bool:
    """Cheap reachability probe: is ``faster_whisper`` importable?"""
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def prewarm(cfg: Any) -> bool:
    """Best-effort load of the accurate model so the first utterance is warm.

    The cold path takes ~10 s (model load); the decode itself is ~150 ms warm.
    Never raises and never fabricates text.
    """
    try:
        _preload_cuda()
        _ensure_runtime_path()
        from tools.transcription_local import _normalize_local_model
        from tools.transcription_tools import _get_or_load_local_model
        name = _normalize_local_model(_resolve_model(cfg)) or DEFAULT_ACCURATE_MODEL
        _get_or_load_local_model(_offline_model_id(cfg, name), {})
        return True
    except Exception:
        return False


__all__ = [
    "ACCURATE_MODELS", "DEFAULT_ACCURATE_MODEL", "AccurateResult",
    "AccurateTranscriber", "get_transcriber", "reset_transcribers",
    "transcribe_sherpa", "reset_sherpa_cache", "prewarm_sherpa",
    "is_available", "prewarm",
]
