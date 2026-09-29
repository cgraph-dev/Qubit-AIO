"""Local keyword-spotter wake engine (sherpa-onnx KWS).

A dedicated ~3.3M-parameter streaming keyword spotter listens for the wake
phrase only. It is tiny on CPU, needs no network, and is far more reliable than
trying to catch a wake phrase in ASR transcripts: the expensive ASR runs only
for the utterance that actually contained the keyword.

This is a *keyword spotter*, not a transcription engine: it never produces text
and is not a second ASR authority. It feeds the same command path as Right-Ctrl.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

MODEL_PREFIX = "sherpa-onnx-kws-"


def models_root() -> Path:
    try:
        from ..config import canonical_data_dir
        return Path(canonical_data_dir()) / "models"
    except Exception:
        return Path(os.path.expanduser("~/.local/share/qubit-voicebridge/models"))


def find_model_dir(cfg: Any = None) -> Optional[Path]:
    explicit = ""
    try:
        explicit = str(cfg.get("voice_edit", "wake", "kws_model_dir",
                                default="") or "")
    except Exception:
        explicit = ""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_dir() else None
    root = models_root()
    if root.is_dir():
        for p in sorted(root.glob(MODEL_PREFIX + "*")):
            if (p / "tokens.txt").is_file():
                return p
    return None


def _pick(model_dir: Path, kind: str) -> Optional[Path]:
    for pattern in (f"*{kind}*.int8.onnx", f"*{kind}*.onnx"):
        hits = sorted(model_dir.glob(pattern))
        if hits:
            return hits[0]
    return None


class KwsDetector:
    """Thin wrapper over ``sherpa_onnx.KeywordSpotter`` (16 kHz mono float)."""

    def __init__(self, model_dir: Path, *, keywords_file: Optional[Path] = None,
                 threshold: float = 0.25, score: float = 1.0,
                 provider: str = "cpu", num_threads: int = 1) -> None:
        import numpy as np  # noqa: F401  (kept for callers)
        import sherpa_onnx
        model_dir = Path(model_dir)
        tokens = model_dir / "tokens.txt"
        encoder = _pick(model_dir, "encoder")
        decoder = _pick(model_dir, "decoder")
        joiner = _pick(model_dir, "joiner")
        kw = Path(keywords_file) if keywords_file else model_dir / "keywords.txt"
        missing = [str(p) for p in (tokens, encoder, decoder, joiner, kw)
                   if not p or not Path(p).is_file()]
        if missing:
            raise FileNotFoundError("KWS model files missing: " + ", ".join(missing))
        self.model_dir = model_dir
        self._sp = sherpa_onnx.KeywordSpotter(
            tokens=str(tokens), encoder=str(encoder), decoder=str(decoder),
            joiner=str(joiner), keywords_file=str(kw), num_threads=int(num_threads),
            keywords_score=float(score), keywords_threshold=float(threshold),
            num_trailing_blanks=1, provider=provider)
        self._stream = self._sp.create_stream()

    def feed(self, samples: Any) -> Optional[str]:
        """Feed float32 [-1,1] 16 kHz samples; return the keyword on a hit."""
        self._stream.accept_waveform(16000, samples)
        while self._sp.is_ready(self._stream):
            self._sp.decode_stream(self._stream)
        result = self._sp.get_result(self._stream)
        if result:
            self._sp.reset_stream(self._stream)
            return str(result)
        return None

    def reset(self) -> None:
        try:
            self._sp.reset_stream(self._stream)
        except Exception:
            pass


def keywords_for(phrase: str) -> List[str]:
    """BPE lines for a phrase using the installed model's token table.

    Only used as a fallback when a keywords file must be generated; the shipped
    model directory carries its own ``keywords.txt``.
    """
    ptoks = {
        "hey qubit": ["▁HE Y ▁QU B IT", "▁HE Y ▁C U B IT",
                      "▁HE Y ▁QU B I T"],
        "hey cubit": ["▁HE Y ▁C U B IT", "▁HE Y ▁QU B IT"],
    }
    return ptoks.get(phrase.strip().lower(), [])


__all__ = ["KwsDetector", "find_model_dir", "models_root", "keywords_for",
           "MODEL_PREFIX"]
