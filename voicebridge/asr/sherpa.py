"""Sherpa-onnx ASR (Moonshine / Parakeet-TDT) — the active default on this box.

Replaces faster-whisper: Moonshine base (quantized v2, ~100MB) transcribes a
short utterance in ~0.1s on CPU (measured 41x realtime on a 3.8s clip) with
ZERO VRAM — the GPU is freed entirely for the local LLM / idle. Parakeet-TDT
0.6B v3 (int8, ~640MB) is available as `engine: parakeet` for maximum English
accuracy; it also runs CPU-fast for short clips.

Supported engines (model dirs under <model_dir>):
  moonshine  -> <model_dir>/sherpa-onnx-moonshine-base-en-quantized-2026-02-27/
                (encoder_model.ort + decoder_model_merged.ort + tokens.txt)
  parakeet   -> <model_dir>/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/
                (encoder.int8.onnx + decoder.int8.onnx + joiner.int8.onnx + tokens.txt)

provider: cpu (default, 0 VRAM) | cuda (onnxruntime CUDA EP).
"""
from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from ..logging_setup import get_logger, log_event
from .base import ASRProvider, ASRUnavailable, Transcript

log = get_logger()

# engine -> relative model dir + file layout
_ENGINES = {
    "moonshine": {
        "dir": "sherpa-onnx-moonshine-base-en-quantized-2026-02-27",
        "factory": "moonshine_v2",
    },
    "parakeet": {
        "dir": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "factory": "transducer",
    },
}

# True-streaming transducers (OnlineRecognizer). These are NOT used for the
# offline file path; they power the live /qubit-chat streaming STT socket. All
# are int8-quantized and CPU-real-time; the zipformer-20M is the low-spec
# champion, the 2026-04 Nemotron pack is the low-latency accuracy option.
_STREAMING_ENGINES = {
    # NVIDIA Nemotron Streaming 0.6B (2026-04-25), 4 chunk variants.
    "nemotron-streaming": {
        "dir": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-80ms-int8-2026-04-25",
        "chunk_ms": 80,
    },
    "nemotron-80ms": {
        "dir": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-80ms-int8-2026-04-25",
        "chunk_ms": 80,
    },
    "nemotron-160ms": {
        "dir": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25",
        "chunk_ms": 160,
    },
    "nemotron-560ms": {
        "dir": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25",
        "chunk_ms": 560,
    },
    "nemotron-1120ms": {
        "dir": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-1120ms-int8-2026-04-25",
        "chunk_ms": 1120,
    },
    # NVIDIA Nemotron 3.5 ASR Streaming 0.6B (2026-06-11), 4 chunk variants.
    "nemotron3-80ms": {
        "dir": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-80ms-int8-2026-06-11",
        "chunk_ms": 80,
    },
    "nemotron3-160ms": {
        "dir": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-160ms-int8-2026-06-11",
        "chunk_ms": 160,
    },
    "nemotron3-560ms": {
        "dir": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
        "chunk_ms": 560,
    },
    "nemotron3-1120ms": {
        "dir": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-1120ms-int8-2026-06-11",
        "chunk_ms": 1120,
    },
    # Ultra-light English zipformer; suitable for Cortex-A7 class CPUs.
    "zipformer-streaming": {
        "dir": "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
    },
    # Kroko English streaming zipformer (2025-08-06) — small, newer.
    "zipformer-streaming-kroko": {
        "dir": "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06",
    },
    # Higher-accuracy English streaming zipformer (2023-06-26).
    "zipformer-streaming-en-2023": {
        "dir": "sherpa-onnx-streaming-zipformer-en-2023-06-26",
    },
    # Small bilingual (zh/en) streaming zipformer.
    "zipformer-streaming-small": {
        "dir": "sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16",
    },
}


def streaming_engines() -> tuple:
    return tuple(_STREAMING_ENGINES)


def hotwords_supported(engine: str) -> bool:
    """True when a streaming engine's runtime accepts hotword biasing.

    Hotwords force ``modified_beam_search``; the NeMo transducer streaming impl
    (Nemotron) supports only ``greedy_search`` and must fall back to greedy with
    hotwords ignored.
    """
    return isinstance(engine, str) and engine.startswith("zipformer")


def _pick_weight(base: Path, stem: str) -> Optional[Path]:
    """Best ``<stem>*.onnx`` under *base* (int8 preferred, then any)."""
    cands = sorted(base.glob(f"{stem}*.onnx"))
    if not cands:
        return None
    int8 = [c for c in cands if "int8" in c.name]
    return (int8 or cands)[0]


def _stitch_chunks(parts: list) -> str:
    """Join per-chunk transcripts, dropping a repeated leading overlap.

    Chunked long-form decode can repeat the tail of the previous chunk at the
    start of the next; this removes up to 6 duplicated leading words so the
    joined text does not double-count them.
    """
    words: list = []
    for part in parts:
        w = part.split()
        if not w:
            continue
        if words:
            k = 0
            for cand in range(min(6, len(words), len(w)), 0, -1):
                if words[-cand:] == w[:cand]:
                    k = cand
                    break
            w = w[k:]
        words.extend(w)
    return " ".join(words).strip()


def online_model_paths(model_dir: Path | str, engine: str) -> dict:
    """Resolve encoder/decoder/joiner/tokens for a streaming transducer engine.

    Raises :class:`ASRUnavailable` when the pack is absent so the caller can
    fall back to the offline sliding-window decoder. File names differ across
    sherpa releases, so each weight is discovered by glob (int8 preferred).
    """
    if engine not in _STREAMING_ENGINES:
        raise ASRUnavailable(f"unknown streaming engine: {engine}")
    base = Path(model_dir) / _STREAMING_ENGINES[engine]["dir"]
    if not base.is_dir():
        raise ASRUnavailable(f"streaming model not installed: {base}")
    out = {
        "encoder": _pick_weight(base, "encoder"),
        "decoder": _pick_weight(base, "decoder"),
        "joiner": _pick_weight(base, "joiner"),
        "tokens": base / "tokens.txt",
    }
    missing = [k for k, v in out.items() if v is None or not Path(v).exists()]
    if missing:
        raise ASRUnavailable(
            f"streaming model incomplete under {base}: missing {missing}")
    return {k: Path(v) for k, v in out.items()}


class SherpaASR(ASRProvider):
    name = "sherpa"

    def __init__(self, engine: str = "moonshine", model_dir: Optional[Path] = None,
                 provider: str = "cpu", num_threads: int = 4,
                 language: Optional[str] = None,
                 hotwords: str = "", hotwords_score: float = 1.5) -> None:
        if engine not in _ENGINES:
            raise ASRUnavailable(f"sherpa engine unknown: {engine} (use moonshine|parakeet)")
        self.engine = engine
        self.model_dir = Path(model_dir) if model_dir else Path("models")
        self.provider = provider
        self.num_threads = num_threads
        self.language = language or "en"
        # Hotword boosting (transducer engines only): bias decoding toward the
        # user's world vocabulary — names/projects/jargon that generic models
        # mangle (accuracy complaint: ~70% raw). Comma-separated phrases.
        self.hotwords = (hotwords or "").strip()
        self.hotwords_score = float(hotwords_score)
        self._recognizer = None
        self._lock = threading.Lock()

    def _hotwords_file(self) -> Optional[str]:
        """Materialize the hotword list for sherpa-onnx; None when unused.

        With modeling_unit="bpe" + bpe_vocab=tokens.txt, sherpa encodes PLAIN
        WORDS into BPE itself (verified: sherpa_onnx 1.13.6 signature). Hand-
        encoding pieces ourselves fails — the C++ side strips/handles '▁'
        markers differently than tokens.txt.
        """
        if not self.hotwords or self.engine != "parakeet":
            return None      # moonshine factory has no hotwords support
        try:
            lines = [w.strip() for w in self.hotwords.split(",") if w.strip()]
            if not lines:
                return None
            path = Path("/tmp/qubit-voicebridge") / "asr-hotwords.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n")
            return str(path)
        except Exception:
            return None

    # ---- model wiring -------------------------------------------------------
    def _paths(self):
        from ..config import resolve_model_file
        engine_dir = _ENGINES[self.engine]["dir"]
        base = self.model_dir / engine_dir
        if self.engine == "moonshine":
            wanted = {
                "encoder": "encoder_model.ort",
                "decoder": "decoder_model_merged.ort",
                "tokens": "tokens.txt",
            }
        else:
            wanted = {
                "encoder": "encoder.int8.onnx",
                "decoder": "decoder.int8.onnx",
                "joiner": "joiner.int8.onnx",
                "tokens": "tokens.txt",
            }
        out = {}
        for key, name in wanted.items():
            first_party = base / name
            # Canonical-first; an unmigrated install transparently reads the
            # legacy model copy (read-only compat — downloads still target
            # the canonical tree).
            out[key] = first_party if first_party.exists() else \
                resolve_model_file(Path(engine_dir) / name)
        return out

    def load(self) -> None:
        with self._lock:
            if self._recognizer is not None:
                return
            import sherpa_onnx as so
            p = self._paths()
            missing = [str(v) for v in p.values() if not Path(v).exists()]
            if missing:
                raise ASRUnavailable(
                    f"sherpa {self.engine} model missing: {missing}\n"
                    f"expected under {self.model_dir / _ENGINES[self.engine]['dir']}")
            try:
                if self.engine == "moonshine":
                    self._recognizer = so.OfflineRecognizer.from_moonshine_v2(
                        encoder=str(p["encoder"]),
                        decoder=str(p["decoder"]),
                        tokens=str(p["tokens"]),
                        num_threads=self.num_threads,
                        provider=self.provider,
                        debug=False)
                else:
                    # NeMo parakeet packs need model_type="nemo_transducer"
                    # (feature-first [batch, feat, time] input layout) — the
                    # default "transducer" feeds time-first and sherpa errors
                    # "Got invalid dimensions ... Expected: <feat_dim>".
                    # feature_dim: read from the encoder's ONNX metadata
                    # (parakeet-tdt-0.6b-v2/v3 = 128; older packs = 80)
                    feat_dim = 80
                    try:
                        import onnx as _onnx
                        enc_model = _onnx.load(str(p["encoder"]))
                        meta = {x.key: x.value for x in enc_model.metadata_props}
                        feat_dim = int(meta.get("feat_dim", 80))
                    except Exception:
                        pass
                    self._recognizer = so.OfflineRecognizer.from_transducer(
                        tokens=str(p["tokens"]),
                        encoder=str(p["encoder"]),
                        decoder=str(p["decoder"]),
                        joiner=str(p["joiner"]),
                        num_threads=self.num_threads,
                        sample_rate=16000,
                        feature_dim=feat_dim,
                        provider=self.provider,
                        decoding_method="modified_beam_search",
                        max_active_paths=4,
                        model_type="nemo_transducer",
                        modeling_unit="bpe",
                        bpe_vocab=str(p["tokens"]),
                        **({"hotwords_file": hw, "hotwords_score": self.hotwords_score}
                           if (hw := self._hotwords_file()) else {}))
            except Exception as exc:  # pragma: no cover
                raise ASRUnavailable(f"sherpa {self.engine} load failed: {exc}") from exc
            log_event("asr.load", provider=self.name, engine=self.engine,
                      device=self.provider, threads=self.num_threads,
                      hotwords=len([w for w in self.hotwords.split(",")
                                    if w.strip()]) if self.hotwords else 0)

    def decode_pcm(self, audio: "np.ndarray", sr: int = 16000) -> str:
        """Decode a mono float32 [-1, 1] buffer; shared by file + streaming.

        Moonshine's decoder has a fixed sequence window (~20s, tokens ~1241)
        and CRASHES (onnxruntime broadcast "4 by <n>") on longer audio instead
        of returning empty. Chunk long clips into overlapping windows and
        concat the per-chunk text so continuous speech up to the VAD cap
        (~30s) transcribes reliably. Parakeet handles long clips natively.
        """
        self.load()
        audio = np.asarray(audio, dtype=np.float32)
        if len(audio) == 0:
            return ""
        # Moonshine's decoder mis-broadcasts (onnxruntime "N by M") on longer
        # sequences, and OVERLAPPING chunks make it loop ("a blessing, a
        # blessing, ..."). Measured on the LibriSpeech long clip: 6 s
        # non-overlapping windows with a boundary de-dup stitch give WER 0.04
        # vs 0.42 with the old 8 s/1 s-overlap chunking. Parakeet handles long
        # clips natively (no chunking).
        MAX_CHUNK_S = 6.0
        if self.engine == "moonshine" and len(audio) / max(sr, 1) > MAX_CHUNK_S:
            parts = []
            n = len(audio)
            width = int(sr * MAX_CHUNK_S)
            pos = 0
            while pos < n:
                chunk = audio[pos:pos + width]
                if len(chunk) < int(sr * 0.5):
                    break
                with self._lock:
                    stream = self._recognizer.create_stream()
                    stream.accept_waveform(sample_rate=sr, waveform=chunk)
                    self._recognizer.decode_stream(stream)
                    txt = stream.result.text.strip()
                    if txt:
                        parts.append(txt)
                pos += width
            return _stitch_chunks(parts)
        with self._lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(sample_rate=sr, waveform=audio)
            self._recognizer.decode_stream(stream)
            return stream.result.text.strip()

    def transcribe(self, audio_path: Path | str) -> Transcript:
        self.load()
        audio_path = Path(audio_path)
        with wave.open(str(audio_path), "rb") as wf:
            sr = wf.getframerate()
            sampwidth = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
        if sampwidth != 2:
            raise ASRUnavailable(f"sherpa needs 16-bit PCM, got {sampwidth*8}-bit")
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        dur_s = len(audio) / sr if sr else 0.0
        if len(audio) == 0:
            return Transcript(text="", provider=f"sherpa-{self.engine}", duration_s=0.0)
        text = self.decode_pcm(audio, sr)
        log_event("asr.transcribe", provider=self.name, engine=self.engine,
                  device=self.provider, chars=len(text), raw_dur_s=round(dur_s, 2))
        # sherpa exposes no per-segment confidence; 1.0 = unknown-ok.
        return Transcript(text=text, language=self.language, confidence=1.0,
                          duration_s=dur_s, provider=f"sherpa-{self.engine}")

    def available(self) -> bool:
        try:
            self._paths()
            return True
        except Exception:
            return False

    def unload(self) -> None:
        with self._lock:
            self._recognizer = None
