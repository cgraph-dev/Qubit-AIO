"""Local keyword-spotter wake engine (sherpa-onnx KWS).

A dedicated ~3.3M-parameter streaming keyword spotter listens for the wake
phrase only. It is tiny on CPU, needs no network, and gates the expensive ASR:
ASR runs for the utterance that contained the keyword, plus one bounded
fallback probe per short KWS-miss segment (``wake.asr_fallback``), because the
spotter is deaf to some voices at every threshold while local ASR hears them.

This is a *keyword spotter*, not a transcription engine: it never produces text
and is not a second ASR authority. It feeds the same command path as Right-Ctrl.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

MODEL_PREFIX = "sherpa-onnx-kws-"

#: KWS-only input conditioning defaults. The live spotter sees post-AGC mic
#: frames whose peaks are routinely 0.03-0.10 while the model expects a
#: healthy level; boost-only normalization lifts those frames without
#: touching normal-level audio (the ASR path never sees any of this).
DEFAULT_INPUT_TARGET_PEAK = 0.5
DEFAULT_INPUT_MAX_GAIN = 6.0
#: Peaks at/below this are digital silence or the room floor; never amplify
#: them (doing so would only give the spotter louder noise).
MIN_CONDITION_PEAK = 0.002


def condition_samples(samples: Any, *, gain: float = 1.0,
                      normalize: bool = True,
                      target_peak: float = DEFAULT_INPUT_TARGET_PEAK,
                      max_gain: float = DEFAULT_INPUT_MAX_GAIN):
    """Condition one float32 frame for the spotter only.

    Returns ``(out, stats)``. ``stats`` carries only levels/gains — never
    audio. The transform is deliberately boost-only and bounded:

    * ``gain`` is a fixed multiplier clamped to 1..16 (default 1.0);
    * ``normalize`` lifts a quiet frame toward ``target_peak`` by at most
      ``max_gain`` (clamped 1..16);
    * frames already at/above ``target_peak`` (or at/below
      ``MIN_CONDITION_PEAK``) are returned unchanged, so normal audio is
      not distorted and near-silence is not amplified.

    ASR never consumes this output; the wake loop feeds the raw capture to
    every transcription path.
    """
    import numpy as np

    try:
        a = np.asarray(samples, dtype=np.float32)
    except Exception:
        return samples, {"raw_peak": 0.0, "peak": 0.0, "gain": 1.0,
                         "normalized": False}
    stats = {"raw_peak": 0.0, "peak": 0.0, "gain": 1.0,
             "normalized": False}
    if a.size == 0:
        return a, stats
    try:
        raw_peak = float(np.abs(a).max())
    except Exception:
        return a, stats
    stats["raw_peak"] = round(raw_peak, 4)
    try:
        fixed = max(1.0, min(float(gain), 16.0))
    except (TypeError, ValueError):
        fixed = 1.0
    total = fixed
    if fixed > 1.0:
        a = np.clip(a * fixed, -1.0, 1.0)
    try:
        want_norm = bool(normalize)
    except Exception:
        want_norm = False
    if want_norm:
        try:
            tgt = max(0.05, min(float(target_peak), 0.9))
        except (TypeError, ValueError):
            tgt = DEFAULT_INPUT_TARGET_PEAK
        try:
            cap = max(1.0, min(float(max_gain), 16.0))
        except (TypeError, ValueError):
            cap = DEFAULT_INPUT_MAX_GAIN
        try:
            peak = float(np.abs(a).max())
        except Exception:
            peak = 0.0
        if peak > MIN_CONDITION_PEAK:
            factor = min(cap, tgt / peak)
            if factor > 1.0:
                a = np.clip(a * factor, -1.0, 1.0)
                total *= factor
                stats["normalized"] = True
    stats["gain"] = round(total, 3)
    try:
        stats["peak"] = round(float(np.abs(a).max()), 4)
    except Exception:
        stats["peak"] = stats["raw_peak"]
    return a, stats


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
           "condition_samples", "MODEL_PREFIX"]
