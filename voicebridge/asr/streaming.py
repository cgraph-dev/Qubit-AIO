"""Streaming STT sessions on the ONE Qubit local ASR authority.

This module does not introduce a second speech authority: it drives the models
already served by :mod:`voicebridge.asr.sherpa` and reuses the exact same
recognizer. Two modes:

* **True streaming** -- ``sherpa_onnx.OnlineRecognizer`` over an int8 streaming
  transducer pack (Nemotron 0.6B 80 ms, or the ultra-light 20M zipformer). The
  recognizer emits a growing hypothesis as audio arrives, so partials appear
  with minimal latency. These packs are optional; when absent the factory falls
  back to the window decoder below with a typed ``fallback_reason``.

* **Window (offline) streaming** -- incremental decode of the growing utterance
  buffer with the already-installed offline Moonshine/Parakeet recognizer. No
  model download is required and it runs real-time on low-spec CPUs (Moonshine
  base measured ~41x realtime on a 3.8 s clip). This is the out-of-the-box
  local engine.

Every partial is a *full hypothesis for the current segment* (``replace=True``):
the client swaps the interim span rather than appending, which is what prevents
repeated partial hypotheses from duplicating text. The client commits only when
it receives ``is_final=True`` for that segment.

Audio clock: sessions accept mono ``float32`` PCM in ``[-1, 1]`` at
``sample_rate`` (16 kHz). The WebSocket layer handles resampling from the
browser's native capture rate.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from ..logging_setup import get_logger, log_event
from .base import ASRUnavailable

log = get_logger()

SAMPLE_RATE = 16000
#: Audio shorter than this is not decoded for a partial (avoids churn on the
#: first frames and gives the recognizer a little context).
MIN_PARTIAL_S = 0.30
#: Minimum wall-clock gap between decodes for the window engine. Bounds CPU on
#: low-spec hosts regardless of how fast frames arrive.
PARTIAL_MIN_INTERVAL_S = 0.25
#: RMS below this is treated as silence and not fed to the recognizer.
SILENCE_RMS = 0.004

# ---------------------------------------------------------------------------
# Model cache (warm starts). Loading a 0.6B transducer on every utterance costs
# 0.5-3 s; the recognizer/offline provider is built once per engine and reused.
# A per-engine lock serialises streams onto a shared recognizer (sherpa streams
# are not safe to decode concurrently).
# ---------------------------------------------------------------------------
_CACHE_LOCK = threading.Lock()
_ONLINE_CACHE: Dict[str, Tuple[Any, threading.Lock]] = {}
_PROVIDER_CACHE: Dict[str, Any] = {}


def _cache_key(cfg: Any, engine: str) -> str:
    try:
        root = str(cfg.model_dir())
    except Exception:
        root = "default"
    try:
        asr = cfg.section("asr")
        threads = asr.get("num_threads", 4)
        ep = (str(asr.get("streaming_provider_ep", "") or "")
              or str(asr.get("provider_ep", "cpu") or "cpu"))
        hot = str(asr.get("hotwords", "") or "")
    except Exception:
        threads, ep, hot = 4, "cpu", ""
    # Hotwords change the recognizer, so they take part in the cache key.
    return f"{engine}|{root}|{threads}|{ep}|{hash(hot) & 0xffffffff}"


class StreamingASRUnavailable(ASRUnavailable):
    """Raised when no local streaming STT path can run (typed refusal)."""


@dataclass
class PartialResult:
    text: str
    is_final: bool = False
    confidence: float = 0.0
    segment: int = 0
    #: Full-hypothesis semantics: the client REPLACES the interim span.
    replace: bool = True

    @property
    def empty(self) -> bool:
        return not self.text.strip()


@dataclass
class StreamingMetrics:
    started_at: float = 0.0
    first_audio_at: Optional[float] = None
    first_partial_at: Optional[float] = None
    final_at: Optional[float] = None
    audio_ms: float = 0.0
    frames: int = 0

    def time_to_first_partial_ms(self) -> Optional[float]:
        if self.first_partial_at is None or self.first_audio_at is None:
            return None
        return round((self.first_partial_at - self.first_audio_at) * 1000.0, 1)

    def finalization_ms(self, stop_at: Optional[float]) -> Optional[float]:
        if self.final_at is None or stop_at is None:
            return None
        return round((self.final_at - stop_at) * 1000.0, 1)

    def as_dict(self, stop_at: Optional[float] = None) -> Dict[str, Any]:
        return {
            "audio_ms": round(self.audio_ms, 1),
            "frames": self.frames,
            "time_to_first_partial_ms": self.time_to_first_partial_ms(),
            "finalization_ms": self.finalization_ms(stop_at),
        }


class StreamingSession:
    """Base streaming session (accept PCM, emit partials, finalize)."""

    engine: str = "local"
    sample_rate: int = SAMPLE_RATE
    fallback_reason: Optional[str] = None

    def __init__(self) -> None:
        self.metrics = StreamingMetrics(started_at=time.monotonic())
        self._closed = False
        self._segment = 0
        self._last_text = ""

    # -- audio -----------------------------------------------------------
    def accept(self, pcm: np.ndarray, *,
               end_of_speech: bool = False) -> Optional[PartialResult]:
        raise NotImplementedError

    def finalize(self, stop_at: Optional[float] = None) -> Optional[PartialResult]:
        raise NotImplementedError

    def cancel(self) -> None:
        self._last_text = ""

    def close(self) -> None:
        self._closed = True

    # -- helpers ---------------------------------------------------------
    def _mark_audio(self, n_samples: int) -> None:
        now = time.monotonic()
        if self.metrics.first_audio_at is None:
            self.metrics.first_audio_at = now
        self.metrics.audio_ms += (n_samples / float(self.sample_rate)) * 1000.0
        self.metrics.frames += 1

    def _partial(self, text: str, confidence: float = 1.0) -> Optional[PartialResult]:
        text = (text or "").strip()
        if not text or text == self._last_text:
            return None
        self._last_text = text
        now = time.monotonic()
        if self.metrics.first_partial_at is None:
            self.metrics.first_partial_at = now
        return PartialResult(text=text, is_final=False, confidence=confidence,
                             segment=self._segment)

    def _final(self, text: str, confidence: float = 1.0) -> Optional[PartialResult]:
        text = (text or "").strip()
        self.metrics.final_at = time.monotonic()
        self._last_text = text
        if not text:
            return None
        return PartialResult(text=text, is_final=True, confidence=confidence,
                             segment=self._segment)


class WindowStreamingSession(StreamingSession):
    """Incremental offline decode over the growing utterance (low-spec path).

    ``decoder`` maps a mono float32 buffer to text. Production passes the
    existing ``SherpaASR`` recognizer (via :meth:`SherpaASR.decode_pcm`); tests
    inject a deterministic double.
    """

    engine = "local-window"

    def __init__(self, decoder: Callable[[np.ndarray], str], *,
                 engine: str = "local-window", sample_rate: int = SAMPLE_RATE,
                 max_window_s: float = 30.0,
                 partial_window_s: float = 8.0,
                 partial_min_interval_s: float = PARTIAL_MIN_INTERVAL_S,
                 silence_rms: float = SILENCE_RMS) -> None:
        super().__init__()
        self.engine = engine
        self.sample_rate = sample_rate
        self._decode = decoder
        self._buf: List[np.ndarray] = []
        self._n = 0
        self._max_samples = int(max_window_s * sample_rate)
        # Partials decode only a bounded trailing window so per-partial cost
        # stays flat on long utterances (low-spec CPU). The FINAL decodes the
        # full buffer, so the authoritative text is never truncated.
        self._partial_samples = int(partial_window_s * sample_rate)
        self._partial_interval = partial_min_interval_s
        self._last_decode_at = 0.0
        self._speech_seen = False
        self._silence_rms = silence_rms

    def _tail(self) -> np.ndarray:
        if not self._buf:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._buf) if len(self._buf) > 1 else self._buf[0]
        if self._n > self._max_samples:
            audio = audio[-self._max_samples:]
        return audio

    def _partial_tail(self) -> np.ndarray:
        audio = self._tail()
        if self._partial_samples > 0 and audio.size > self._partial_samples:
            audio = audio[-self._partial_samples:]
        return audio

    def accept(self, pcm: np.ndarray, *,
               end_of_speech: bool = False) -> Optional[PartialResult]:
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if pcm.size == 0:
            return None
        self._mark_audio(pcm.size)
        rms = float(np.sqrt(np.mean(np.square(pcm)))) if pcm.size else 0.0
        if rms >= self._silence_rms:
            self._speech_seen = True
        # Buffer speech (and a little lead-in) but never flood the recognizer
        # with pure silence.
        if self._speech_seen or rms >= self._silence_rms:
            self._buf.append(pcm)
            self._n += pcm.size
        if not self._speech_seen:
            return None
        if self._n < int(MIN_PARTIAL_S * self.sample_rate):
            return None
        now = time.monotonic()
        if now - self._last_decode_at < self._partial_interval:
            return None
        self._last_decode_at = now
        try:
            text = self._decode(self._partial_tail())
        except ASRUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - decode must not kill the socket
            log_event("asr.stream.partial_error", engine=self.engine,
                      error=type(exc).__name__)
            return None
        return self._partial(text)

    def finalize(self, stop_at: Optional[float] = None) -> Optional[PartialResult]:
        audio = self._tail()
        if audio.size == 0:
            self.metrics.final_at = time.monotonic()
            return None
        try:
            text = self._decode(audio)
        except ASRUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            log_event("asr.stream.final_error", engine=self.engine,
                      error=type(exc).__name__)
            text = self._last_text
        return self._final(text)

    def cancel(self) -> None:
        self._buf = []
        self._n = 0
        self._speech_seen = False
        super().cancel()


class OnlineStreamingSession(StreamingSession):
    """True streaming via ``sherpa_onnx.OnlineRecognizer``.

    The recognizer/stream are injected so the class is testable without a model.
    """

    engine = "local-online"

    def __init__(self, recognizer: Any, stream: Any, *,
                 engine: str = "local-online",
                 sample_rate: int = SAMPLE_RATE,
                 padding_s: float = 0.3,
                 lock: Optional[threading.Lock] = None) -> None:
        super().__init__()
        self.engine = engine
        self.sample_rate = sample_rate
        self._rec = recognizer
        self._stream = stream
        self._lock = lock or threading.Lock()
        self._padding = np.zeros(int(padding_s * sample_rate), dtype=np.float32)
        self._finished = False

    def _drain(self) -> str:
        # The recognizer may be shared across sessions (warm cache), so decode
        # is serialised per engine.
        with self._lock:
            while self._rec.is_ready(self._stream):
                self._rec.decode_stream(self._stream)
            return str(self._rec.get_result(self._stream) or "")

    def accept(self, pcm: np.ndarray, *,
               end_of_speech: bool = False) -> Optional[PartialResult]:
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if pcm.size == 0:
            return None
        self._mark_audio(pcm.size)
        self._stream.accept_waveform(sample_rate=self.sample_rate, waveform=pcm)
        return self._partial(self._drain())

    def finalize(self, stop_at: Optional[float] = None) -> Optional[PartialResult]:
        if not self._finished:
            self._stream.accept_waveform(sample_rate=self.sample_rate,
                                         waveform=self._padding)
            self._stream.input_finished()
            self._finished = True
        return self._final(self._drain())

    def cancel(self) -> None:
        try:
            with self._lock:
                self._rec.reset(self._stream)
        except Exception:
            pass
        super().cancel()


# ---------------------------------------------------------------------------
# Factory -- the single selection point shared by the HTTP and WS surfaces.
# ---------------------------------------------------------------------------
def _sherpa_from_cfg(cfg: Any, *, engine_override: Optional[str] = None):
    """Build the configured offline SherpaASR (mirrors qubit_compat selection).

    Kept local to avoid importing the compat module (and its heavy deps) just to
    open a microphone socket. ``engine_override`` lets the live window decoder
    use a fast model (Moonshine) while file transcription keeps the accurate one.
    """
    from .sherpa import SherpaASR
    try:
        asr_cfg = cfg.section("asr")
    except Exception:
        asr_cfg = {}
    provider = str(asr_cfg.get("provider", "sherpa") or "sherpa")
    model_dir = cfg.model_dir()
    if provider == "whisper":
        from .whisper import FasterWhisperASR
        return FasterWhisperASR(model=asr_cfg.get("model", "base"),
                                device=asr_cfg.get("device", "auto"),
                                compute_type=asr_cfg.get("compute_type", "int8"),
                                language=asr_cfg.get("language"),
                                beam_size=asr_cfg.get("beam_size", 1),
                                model_dir=model_dir)
    return SherpaASR(engine=engine_override or asr_cfg.get("engine", "moonshine"),
                     model_dir=model_dir,
                     provider=asr_cfg.get("provider_ep", "cpu"),
                     num_threads=int(asr_cfg.get("num_threads", 4) or 4),
                     language=asr_cfg.get("language"),
                     hotwords=asr_cfg.get("hotwords", ""),
                     hotwords_score=float(asr_cfg.get("hotwords_score", 1.5)))


def _configured_streaming_engine(cfg: Any) -> str:
    try:
        value = cfg.get("asr", "streaming_engine", default="auto")
    except Exception:
        value = "auto"
    return str(value or "auto").strip().lower()


def open_streaming_session(cfg: Any, *, engine: Optional[str] = None,
                           sample_rate: int = SAMPLE_RATE) -> StreamingSession:
    """Open the best available local streaming STT session.

    ``engine`` (or ``asr.streaming_engine``) is one of ``auto`` (default),
    ``offline-window``, or a true-streaming engine name from
    :data:`sherpa._STREAMING_ENGINES`. ``auto`` prefers an installed streaming
    model and otherwise uses the existing offline model via the window decoder.
    A missing model that the operator explicitly requested raises a typed
    :class:`StreamingASRUnavailable`.
    """
    from . import sherpa as S
    choice = (engine or _configured_streaming_engine(cfg)).strip().lower()
    if choice in ("", "auto"):
        # Default is the existing offline window decoder (Moonshine). A
        # true-streaming pack is opt-in via `asr.streaming_engine` so staging a
        # pack can never silently change the production default.
        return _open_window(cfg, sample_rate=sample_rate)
    if choice == "offline-window":
        return _open_window(cfg, sample_rate=sample_rate)
    if choice in S.streaming_engines():
        return _open_online(cfg, choice, sample_rate=sample_rate)
    raise StreamingASRUnavailable(
        f"unknown streaming engine '{choice}' (use auto|offline-window"
        f"|{'|'.join(S.streaming_engines())})")


def _window_engine_name(cfg: Any) -> str:
    try:
        asr_cfg = cfg.section("asr")
    except Exception:
        asr_cfg = {}
    # Live partials favour a fast model (Moonshine, ~41x realtime, 0 VRAM). The
    # accurate configured engine stays the authority for whole-file transcribe.
    return str(asr_cfg.get("streaming_window_engine", "moonshine")
               or "moonshine").strip() or "moonshine"


def _get_window_provider(cfg: Any, fast: str):
    key = _cache_key(cfg, f"window:{fast}")
    with _CACHE_LOCK:
        provider = _PROVIDER_CACHE.get(key)
        if provider is None:
            provider = _sherpa_from_cfg(cfg, engine_override=fast)
            provider.load()
            _PROVIDER_CACHE[key] = provider
            log_event("asr.stream.load", engine=f"window:{fast}", warm=False)
        return provider


def _open_window(cfg: Any, *, sample_rate: int) -> StreamingSession:
    provider = _get_window_provider(cfg, _window_engine_name(cfg))
    engine = f"{getattr(provider, 'name', 'local')}-{getattr(provider, 'engine', 'window')}"
    return WindowStreamingSession(
        lambda audio: provider.decode_pcm(audio, sample_rate),
        engine=engine, sample_rate=sample_rate)


def _streaming_provider(cfg: Any) -> str:
    """Execution provider for live streaming STT (cuda when configured)."""
    try:
        asr_cfg = cfg.section("asr")
    except Exception:
        asr_cfg = {}
    want = str(asr_cfg.get("streaming_provider_ep", "") or "").strip().lower()
    if not want:
        want = str(asr_cfg.get("provider_ep", "cpu") or "cpu").strip().lower()
    # Only cuda is opt-in; anything else is the safe CPU path.
    return "cuda" if want == "cuda" else "cpu"


def _new_recognizer(cfg: Any, engine: str, sample_rate: int,
                    provider: str, paths: Dict[str, Any],
                    asr_cfg: Dict[str, Any], threads: int):
    import sherpa_onnx as so
    from .sherpa import hotwords_supported
    # Hotword/custom-vocabulary biasing. Only the zipformer transducer runtime
    # supports modified_beam_search; the NeMo transducer (Nemotron) is
    # greedy_search only, so hotwords are ignored there instead of failing the
    # recognizer build. Words are plain text; sherpa BPE-encodes them.
    hot = str(asr_cfg.get("hotwords", "") or "").strip()
    hot_kwargs: Dict[str, Any] = {}
    if hot and hotwords_supported(engine):
        try:
            words = [w.strip() for w in hot.split(",") if w.strip()]
            if words:
                hp = Path("/tmp/qubit-voicebridge") / f"stt-hotwords-{engine}.txt"
                hp.parent.mkdir(parents=True, exist_ok=True)
                hp.write_text("\n".join(words) + "\n")
                hot_kwargs = {
                    "hotwords_file": str(hp),
                    "hotwords_score": float(asr_cfg.get("hotwords_score", 1.5)),
                    "modeling_unit": "bpe",
                    "bpe_vocab": str(paths["tokens"]),
                }
        except Exception:
            hot_kwargs = {}
    # Hotword biasing requires modified_beam_search (sherpa constraint).
    decode = "modified_beam_search" if hot_kwargs else "greedy_search"
    return so.OnlineRecognizer.from_transducer(
        tokens=str(paths["tokens"]),
        encoder=str(paths["encoder"]),
        decoder=str(paths["decoder"]),
        joiner=str(paths["joiner"]),
        num_threads=threads,
        sample_rate=sample_rate,
        feature_dim=80,
        provider=provider,
        decoding_method=decode,
        max_active_paths=4,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0,
        **hot_kwargs,
    )


#: The provider the most recent recognizer was actually built with (health).
_LAST_PROVIDER = ""


def _build_online(cfg: Any, engine: str, sample_rate: int):
    from .sherpa import online_model_paths
    try:
        asr_cfg = cfg.section("asr")
    except Exception:
        asr_cfg = {}
    paths = online_model_paths(cfg.model_dir(), engine)
    threads = int(asr_cfg.get("num_threads", 4) or 4)
    want = _streaming_provider(cfg)
    if want == "cuda":
        from . import gpu
        if gpu.cuda_provider_present():
            gpu.ensure_cuda_libraries()
            try:
                rec = _new_recognizer(cfg, engine, sample_rate, "cuda",
                                      paths, asr_cfg, threads)
                _set_provider("cuda")
                log_event("asr.stream.provider", engine=engine, provider="cuda",
                          cuda_libs=gpu.status()["preloaded_count"])
                return rec
            except Exception as exc:  # noqa: BLE001 - safe CPU fallback
                log_event("asr.stream.cuda_fallback", engine=engine,
                          error=type(exc).__name__)
        else:
            log_event("asr.stream.cuda_unavailable", engine=engine)
    rec = _new_recognizer(cfg, engine, sample_rate, "cpu",
                          paths, asr_cfg, threads)
    _set_provider("cpu")
    return rec


def _set_provider(provider: str) -> None:
    global _LAST_PROVIDER
    _LAST_PROVIDER = provider


def last_provider() -> str:
    return _LAST_PROVIDER


def _legacy_build_online_unused(cfg: Any, engine: str, sample_rate: int):
    import sherpa_onnx as so  # noqa: F401 - kept for reference only
    from .sherpa import online_model_paths
    try:
        asr_cfg = cfg.section("asr")
    except Exception:
        asr_cfg = {}
    paths = online_model_paths(cfg.model_dir(), engine)
    provider = str(asr_cfg.get("provider_ep", "cpu") or "cpu")
    threads = int(asr_cfg.get("num_threads", 4) or 4)
    # Hotword/custom-vocabulary biasing (transducer engines support it). Words
    # are plain text; sherpa BPE-encodes them against tokens.txt.
    hot = str(asr_cfg.get("hotwords", "") or "").strip()
    hot_kwargs: Dict[str, Any] = {}
    if hot:
        try:
            words = [w.strip() for w in hot.split(",") if w.strip()]
            if words:
                hp = Path("/tmp/qubit-voicebridge") / f"stt-hotwords-{engine}.txt"
                hp.parent.mkdir(parents=True, exist_ok=True)
                hp.write_text("\n".join(words) + "\n")
                hot_kwargs = {
                    "hotwords_file": str(hp),
                    "hotwords_score": float(asr_cfg.get("hotwords_score", 1.5)),
                    "modeling_unit": "bpe",
                    "bpe_vocab": str(paths["tokens"]),
                }
        except Exception:
            hot_kwargs = {}
    # Hotword biasing requires modified_beam_search (sherpa constraint).
    decode = "modified_beam_search" if hot_kwargs else "greedy_search"
    return so.OnlineRecognizer.from_transducer(
        tokens=str(paths["tokens"]),
        encoder=str(paths["encoder"]),
        decoder=str(paths["decoder"]),
        joiner=str(paths["joiner"]),
        num_threads=threads,
        sample_rate=sample_rate,
        feature_dim=80,
        provider=provider,
        decoding_method=decode,
        max_active_paths=4,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0,
        **hot_kwargs,
    )


def _get_online(cfg: Any, engine: str, sample_rate: int):
    """Return the cached ``(recognizer, stream_lock)`` for *engine* (load once)."""
    key = _cache_key(cfg, engine)
    with _CACHE_LOCK:
        entry = _ONLINE_CACHE.get(key)
        if entry is None:
            rec = _build_online(cfg, engine, sample_rate)
            entry = (rec, threading.Lock())
            _ONLINE_CACHE[key] = entry
            log_event("asr.stream.load", engine=engine, warm=False)
        return entry


def _open_online(cfg: Any, engine: str, *, sample_rate: int) -> StreamingSession:
    rec, lock = _get_online(cfg, engine, sample_rate)
    stream = rec.create_stream()
    log_event("asr.stream.open", engine=engine, warm=True)
    return OnlineStreamingSession(rec, stream, engine=engine,
                                  sample_rate=sample_rate, lock=lock)


def prewarm(cfg: Any, engine: Optional[str] = None) -> Dict[str, Any]:
    """Load the selected engine into the warm cache and report its health.

    Loading a 0.6B transducer takes seconds on CPU; calling this at startup (or
    from the health route) makes the first real utterance warm.
    """
    from . import sherpa as S
    choice = (engine or _configured_streaming_engine(cfg)).strip().lower()
    t0 = time.monotonic()
    try:
        if choice in ("", "auto", "offline-window"):
            fast = _window_engine_name(cfg)
            p = _get_window_provider(cfg, fast)
            name = f"{getattr(p, 'name', 'local')}-{getattr(p, 'engine', 'window')}"
        elif choice in S.streaming_engines():
            _get_online(cfg, choice, SAMPLE_RATE)
            name = choice
        else:
            return {"ok": False, "code": "STT_UNAVAILABLE",
                    "detail": f"unknown streaming engine '{choice}'"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": getattr(exc, "code", None) or "STT_UNAVAILABLE",
                "detail": str(getattr(exc, "detail", "") or exc)[:200]}
    return {"ok": True, "engine": name, "loaded": True,
            "load_ms": round((time.monotonic() - t0) * 1000, 1), "warm": True}


def cached_engines() -> Dict[str, Any]:
    """Non-secret health view of the warm model cache."""
    with _CACHE_LOCK:
        online = sorted({'|'.join(k.split('|')[:2]) for k in _ONLINE_CACHE})
        window = sorted(k.split('|')[0] for k in _PROVIDER_CACHE)
    from . import gpu
    g = gpu.status()
    return {"loaded_count": len(_ONLINE_CACHE) + len(_PROVIDER_CACHE),
            "online": online, "window": window,
            "provider_ep": _cached_ep(),
            "provider": last_provider() or "",
            "gpu_available": bool(g.get("cuda_provider_present")),
            "cuda": g}


def _cached_ep() -> str:
    for k in _ONLINE_CACHE:
        parts = k.split("|")
        return parts[3] if len(parts) > 3 else ""
    return ""


def _gpu_available() -> bool:
    """True when the sherpa-onnx build ships the CUDA provider (not the
    unrelated Python ``onnxruntime`` package)."""
    from . import gpu
    return gpu.cuda_provider_present()


__all__ = [
    "PartialResult",
    "StreamingMetrics",
    "StreamingSession",
    "WindowStreamingSession",
    "OnlineStreamingSession",
    "StreamingASRUnavailable",
    "open_streaming_session",
    "prewarm",
    "cached_engines",
    "SAMPLE_RATE",
]
