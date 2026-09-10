"""Packet 8U — wake-word spotting inside the single audio listener.

Keyword detection runs through the installed ``sherpa-onnx``
``KeywordSpotter`` (transducer encoder/decoder/joiner + tokens + keywords
file) against the existing microphone stream contract — no second
listener, no new dependency, no commercial model. The spotter needs
model files the user fetches explicitly (model downloads are never
automatic); without them every surface reports typed ``NO_MODEL`` with
the exact fetch step instead of pretending to listen.

File-based detection (``detect_file``) is the complete, tested code
path; arming the live microphone loop additionally needs mic hardware
and the model files, and is a documented manual qualification
(``docs/NATIVE_EQUIVALENTS_8U.md``).

Qubit supports pluggable wake engines (openWakeWord-compatible bundles,
``wake_word_engines.py`` at the pinned ``v2026.9.7 @ 2237be3``
revision); Qubit implements the keyword-spotter contract natively.
Provenance is recorded in ``docs/UPSTREAM_REUSE.md``.
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

NO_MODEL_CODE = "WAKE_NO_MODEL"
BAD_INPUT_CODE = "WAKE_BAD_INPUT"
NOT_FOUND_CODE = "WAKE_NOT_FOUND"
IO_CODE = "WAKE_IO"

MODEL_FILES = ("tokens.txt", "encoder.onnx", "decoder.onnx",
               "joiner.onnx", "keywords.txt")
EXPECTED_SAMPLE_RATE = 16000
MAX_WAV_BYTES = 16 * 1024 * 1024


class WakeError(Exception):
    """Typed wake-word failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def redact_spec(text: Any) -> str:
    try:
        from .redaction import redact_text as _redact
        return _redact(str(text or ""))[:300]
    except Exception:
        return "?"


def model_dir(cfg: Any) -> Path:
    """Directory holding the keyword-spotter model files."""
    try:
        base = Path(cfg.model_dir())
    except Exception:
        base = Path.home() / ".config" / "qubit-voicebridge" / "models"
    return base / "keyword-spotter"


def model_state(cfg: Any) -> Dict[str, Any]:
    """Which spotter files exist (read-only probe)."""
    root = model_dir(cfg)
    files = {}
    for name in MODEL_FILES:
        present = (root / name).is_file()
        files[name] = bool(present)
    ready = all(files.values())
    keywords: List[str] = []
    if (root / "keywords.txt").is_file():
        try:
            for line in (root / "keywords.txt").read_text(
                    encoding="utf-8").splitlines():
                phrase = line.split("/")[0].strip()
                if phrase:
                    keywords.append(redact_spec(phrase)[:60])
        except OSError:
            pass
    return {"ready": ready, "dir": redact_spec(str(root))[:200],
            "files": files, "keywords": keywords[:10]}


def fetch_step(cfg: Any) -> str:
    root = model_dir(cfg)
    return (
        "fetch a sherpa-onnx keyword-spotter bundle (tokens.txt, "
        "encoder.onnx, decoder.onnx, joiner.onnx) plus a keywords.txt "
        f"into {root} (see https://github.com/k2-fsa/sherpa-onnx "
        "keyword-spotting models); Qubit never downloads models "
        "automatically")


def status(cfg: Any) -> Dict[str, Any]:
    """Spotter readiness (read-only; never downloads)."""
    state = model_state(cfg)
    if state["ready"]:
        return {"state": "READY", "engine": "sherpa-onnx KeywordSpotter",
                "keywords": state["keywords"],
                "listener": "single shared audio loop (no second listener)",
                "owner": "qubit-policy"}
    return {"state": "NO_MODEL", "code": NO_MODEL_CODE,
            "detail": "keyword-spotter model files are absent; " +
                      fetch_step(cfg),
            "missing": [name for name, ok in state["files"].items()
                        if not ok],
            "owner": "qubit-policy",
            "rollback": "nothing to roll back"}


def _load_wav_mono16k(path: Path) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise WakeError(NOT_FOUND_CODE, f"no such WAV ({exc})")
    if size > MAX_WAV_BYTES:
        raise WakeError(BAD_INPUT_CODE,
                        f"WAV is {size} bytes (limit {MAX_WAV_BYTES})")
    try:
        with wave.open(str(path), "rb") as fh:
            if fh.getnchannels() != 1 or fh.getsampwidth() != 2 or \
                    fh.getframerate() != EXPECTED_SAMPLE_RATE:
                raise WakeError(
                    BAD_INPUT_CODE,
                    "WAV must be mono 16-bit 16 kHz "
                    f"(got {fh.getnchannels()}ch/"
                    f"{fh.getsampwidth() * 8}bit/"
                    f"{fh.getframerate()}Hz)")
            frames = fh.readframes(fh.getnframes())
    except wave.Error as exc:
        raise WakeError(BAD_INPUT_CODE, f"not a readable WAV ({exc})")
    except OSError as exc:
        raise WakeError(IO_CODE, f"cannot read WAV ({exc})")
    return frames


def _spotter(cfg: Any, num_threads: int = 2) -> Any:
    state = model_state(cfg)
    if not state["ready"]:
        raise WakeError(NO_MODEL_CODE, "spotter model files absent; " +
                        fetch_step(cfg))
    root = model_dir(cfg)
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise WakeError(IO_CODE,
                        f"sherpa-onnx unavailable ({exc})")
    keywords = (root / "keywords.txt").read_text(
        encoding="utf-8").splitlines()
    lines = [ln.strip() for ln in keywords if ln.strip()]
    if not lines:
        raise WakeError(BAD_INPUT_CODE,
                        "keywords.txt has no keyword entries")
    try:
        return sherpa_onnx.KeywordSpotter(
            tokens=str(root / "tokens.txt"),
            encoder=str(root / "encoder.onnx"),
            decoder=str(root / "decoder.onnx"),
            joiner=str(root / "joiner.onnx"),
            keywords_file=str(root / "keywords.txt"),
            num_threads=int(num_threads))
    except Exception as exc:
        raise WakeError(IO_CODE, f"spotter init failed ({exc})")


def detect_file(cfg: Any, path: str, *, threshold: float = 0.25,
                num_threads: int = 2) -> Dict[str, Any]:
    """Run keyword detection over a 16 kHz mono WAV file.

    Real detection when the model files exist; typed NO_MODEL otherwise.
    Returns detections (keyword, time window) — never audio content.
    """
    if not (0.01 <= float(threshold or 0.25) <= 0.99):
        raise WakeError(BAD_INPUT_CODE, "threshold must be 0.01..0.99")
    candidate = Path(str(path or "").strip()).expanduser()
    if not candidate.is_absolute() or "\x00" in str(path):
        raise WakeError(BAD_INPUT_CODE, "WAV path must be absolute")
    frames = _load_wav_mono16k(candidate)
    spotter = _spotter(cfg, num_threads=num_threads)
    import numpy as np

    samples = np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0
    stream = spotter.create_stream()
    window = 8000
    detections: List[Dict[str, Any]] = []
    offset_s = 0.0
    for start in range(0, len(samples), window):
        chunk = samples[start:start + window]
        stream.accept_waveform(EXPECTED_SAMPLE_RATE, chunk)
        while spotter.is_ready(stream):
            spotter.decode_stream(stream)
        result = spotter.get_result(stream)
        text = str(getattr(result, "text", "") or "").strip()
        if text:
            tokens = getattr(result, "tokens", []) or []
            stamps = spotter.timestamps(stream) or []
            detections.append({
                "keyword": redact_spec(text)[:80],
                "at_s": round(offset_s + float(stamps[0])
                              if stamps else offset_s, 2),
                "tokens": [redact_spec(str(t))[:40] for t in tokens][:8]})
        offset_s += len(chunk) / EXPECTED_SAMPLE_RATE
        spotter.reset_stream(stream)
    return {"state": "OK", "file": redact_spec(str(candidate))[:200],
            "detections": detections[:20],
            "samples": int(len(samples))}
