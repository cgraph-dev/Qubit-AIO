"""Accurate-local-STT dependency packaging (item 11).

The accurate decoder (``voicebridge/voice_edit/asr_accurate.py``) decodes a
whole utterance with the vendored ``faster-whisper`` provider and, optionally,
preloads the pip ``nvidia-*-cu12`` runtime libs for GPU via
``voicebridge/asr/gpu.py::ensure_cuda_libraries``. These checks pin the
*packaging* contract for a fresh install:

* ``faster-whisper`` is a bounded core dependency (repo dependency policy:
  ``>=floor,<next_major``);
* the optional ``accel`` extra exists and every entry is bounded;
* the accurate path advertises a typed fallback (never raises, never fabricates
  text) when the optional acceleration libs are absent.

No model is loaded and no device/network is touched: the transcriber's loader
seam is injected, exactly as the module documents.
"""
from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

from voicebridge.voice_edit import asr_accurate

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"

# The cu12 runtime wheels the host already preloads (voicebridge/asr/gpu.py).
ACCEL_WHEELS = (
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cufft-cu12",
    "nvidia-curand-cu12",
    "nvidia-cuda-nvrtc-cu12",
    "nvidia-nvjitlink-cu12",
)


def _toml() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _dep_entry(name: str) -> str:
    for dep in _toml()["project"]["dependencies"]:
        if dep.split(";", 1)[0].strip().startswith(name):
            return dep
    raise AssertionError(f"{name!r} not declared in project.dependencies")


# ---------------------------------------------------------------------------
# 1. faster-whisper: bounded core dependency
# ---------------------------------------------------------------------------
def test_faster_whisper_is_declared_in_core_dependencies():
    entry = _dep_entry("faster-whisper")
    assert entry, entry


def test_faster_whisper_has_both_lower_and_upper_bounds():
    entry = _dep_entry("faster-whisper")
    # repo policy (qubit_runtime/AGENTS.md): >=floor,<next_major
    assert ">=" in entry, entry
    assert "<" in entry, entry


# ---------------------------------------------------------------------------
# 2. accel extra: optional, bounded, the cu12 runtime wheels
# ---------------------------------------------------------------------------
def test_accel_extra_exists_and_every_entry_is_bounded():
    extras = _toml()["project"]["optional-dependencies"]
    assert "accel" in extras, sorted(extras)
    accel = extras["accel"]
    assert accel, "accel extra must not be empty"
    for entry in accel:
        assert ">=" in entry, entry
        assert "<" in entry, entry


def test_accel_extra_is_only_the_nvidia_cu12_runtime_wheels():
    accel = _toml()["project"]["optional-dependencies"]["accel"]
    names = {entry.split(">=", 1)[0].split("==", 1)[0].strip() for entry in accel}
    for required in ACCEL_WHEELS:
        assert required in names, (required, sorted(names))
    for name in names:
        assert name.startswith("nvidia-") and name.endswith("-cu12"), name


def test_accel_is_not_an_always_installed_core_dependency():
    core = _toml()["project"]["dependencies"]
    assert not any(dep.split(";", 1)[0].strip().startswith("nvidia-")
                   for dep in core), core


# ---------------------------------------------------------------------------
# 3. accurate path: typed fallback, never raises, no fabricated text
# ---------------------------------------------------------------------------
def test_is_available_returns_a_bool_for_a_minimal_cfg():
    # ``object()`` has no ``.get``: the probe must still answer, never raise.
    result = asr_accurate.is_available(object())
    assert isinstance(result, bool)


def test_is_available_reports_false_when_faster_whisper_is_absent(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert asr_accurate.is_available(object()) is False

    def _boom(name):
        raise ImportError("faster-whisper / nvidia cu12 libs absent")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    assert asr_accurate.is_available(object()) is False


def test_failure_reasons_are_always_typed_and_non_empty():
    # Default normalization never yields an empty string ...
    assert asr_accurate._reason("") == "accurate transcription failed"
    assert asr_accurate._reason("   \n ").strip()
    # ... and is bounded to a short single line.
    long = asr_accurate._reason("x" * 500)
    assert long and len(long) <= 160

    def raising_loader(*_args, **_kwargs):
        raise RuntimeError("faster-whisper unavailable: nvidia-cublas-cu12 missing")

    result = asr_accurate.AccurateTranscriber(
        object(), loader=raising_loader).transcribe(b"\x00\x00", 16000)
    assert result.ok is False
    assert result.text == ""
    assert isinstance(result.reason, str) and result.reason.strip()


def test_transcriber_preserves_a_typed_loader_failure_without_loading_a_model():
    def failing_loader(cfg, model, pcm, rate):
        return asr_accurate.AccurateResult(
            ok=False, reason="faster-whisper unavailable: optional accel absent")

    result = asr_accurate.AccurateTranscriber(
        object(), loader=failing_loader).transcribe(b"", 16000)
    assert result.ok is False
    assert result.reason == "faster-whisper unavailable: optional accel absent"
