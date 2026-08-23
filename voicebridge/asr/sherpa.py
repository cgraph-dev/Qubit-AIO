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
from typing import List, Optional

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


class SherpaASR(ASRProvider):
    name = "sherpa"

    def __init__(self, engine: str = "moonshine", model_dir: Optional[Path] = None,
                 provider: str = "cpu", num_threads: int = 4,
                 language: Optional[str] = None) -> None:
        if engine not in _ENGINES:
            raise ASRUnavailable(f"sherpa engine unknown: {engine} (use moonshine|parakeet)")
        self.engine = engine
        self.model_dir = Path(model_dir) if model_dir else Path("models")
        self.provider = provider
        self.num_threads = num_threads
        self.language = language or "en"
        self._recognizer = None
        self._lock = threading.Lock()

    # ---- model wiring -------------------------------------------------------
    def _paths(self):
        base = self.model_dir / _ENGINES[self.engine]["dir"]
        if self.engine == "moonshine":
            return {
                "encoder": base / "encoder_model.ort",
                "decoder": base / "decoder_model_merged.ort",
                "tokens": base / "tokens.txt",
            }
        return {
            "encoder": base / "encoder.int8.onnx",
            "decoder": base / "decoder.int8.onnx",
            "joiner": base / "joiner.int8.onnx",
            "tokens": base / "tokens.txt",
        }

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
                        model_type="nemo_transducer")
            except Exception as exc:  # pragma: no cover
                raise ASRUnavailable(f"sherpa {self.engine} load failed: {exc}") from exc
            log_event("asr.load", provider=self.name, engine=self.engine,
                      device=self.provider, threads=self.num_threads)

    def transcribe(self, audio_path: Path | str) -> Transcript:
        self.load()
        audio_path = Path(audio_path)
        with wave.open(str(audio_path), "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        if wf.getsampwidth() != 2:
            raise ASRUnavailable(f"sherpa needs 16-bit PCM, got {wf.getsampwidth()*8}-bit")
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        dur_s = len(audio) / sr if sr else 0.0
        if len(audio) == 0:
            return Transcript(text="", provider=f"sherpa-{self.engine}", duration_s=0.0)

        # Moonshine's decoder has a fixed sequence window (~20s, tokens ~1241)
        # and CRASHES (onnxruntime broadcast "4 by <n>") on longer audio instead
        # of returning empty. Chunk long clips into overlapping windows and
        # concat the per-chunk text so continuous speech up to the VAD cap
        # (~30s) transcribes reliably. Parakeet handles long clips natively.
        MAX_CHUNK_S = 20.0
        OVERLAP_S = 1.0
        if self.engine == "moonshine" and len(audio) / max(sr, 1) > MAX_CHUNK_S:
            parts = []
            n = len(audio)
            step = int(sr * (MAX_CHUNK_S - OVERLAP_S))
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
                pos += step
            text = " ".join(parts).strip()
        else:
            with self._lock:
                stream = self._recognizer.create_stream()
                stream.accept_waveform(sample_rate=sr, waveform=audio)
                self._recognizer.decode_stream(stream)
                text = stream.result.text.strip()
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
