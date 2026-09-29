"""GPU/provider selection + hotword-capability tests for streaming STT.

Hermetic: no model load, no CUDA required. These pin the CUDA/cpu provider
resolution, the safe-fallback shape, and the hotword capability gate that
prevents ``modified_beam_search`` from being requested on the NeMo transducer
(Nemotron) — which crashed the recognizer build.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: t\n")
    return Config.load(p)


def test_streaming_provider_resolution(tmp_path):
    from voicebridge.asr.streaming import _streaming_provider
    cfg = _cfg(tmp_path)
    # default: inherit provider_ep (cpu)
    assert _streaming_provider(cfg) == "cpu"
    cfg.data.setdefault("asr", {})["streaming_provider_ep"] = "cuda"
    assert _streaming_provider(cfg) == "cuda"
    cfg.data["asr"]["streaming_provider_ep"] = "bogus"
    assert _streaming_provider(cfg) == "cpu"  # unknown -> safe cpu


def test_gpu_status_shape():
    from voicebridge.asr import gpu
    st = gpu.status()
    assert set(st) >= {"cuda_provider_present", "cuda_libraries_loaded",
                       "preloaded_count", "error"}
    assert isinstance(st["cuda_provider_present"], bool)
    # presence must match the bundled provider library
    import sherpa_onnx
    lib = (Path(sherpa_onnx.__file__).resolve().parent / "lib"
           / "libonnxruntime_providers_cuda.so")
    assert st["cuda_provider_present"] == lib.exists()


def test_hotwords_supported_only_for_zipformer():
    from voicebridge.asr.sherpa import hotwords_supported
    assert hotwords_supported("zipformer-streaming") is True
    assert hotwords_supported("zipformer-streaming-kroko") is True
    assert hotwords_supported("nemotron-560ms") is False
    assert hotwords_supported("nemotron3-80ms") is False
    assert hotwords_supported("") is False


def test_stt_status_reports_streaming_provider(tmp_path):
    from voicebridge import qubit_compat as HC
    cfg = _cfg(tmp_path)
    cfg.data.setdefault("asr", {})["streaming_provider_ep"] = "cuda"
    cfg.data["asr"]["streaming_engine"] = "nemotron-560ms"
    code, out = HC.dispatch(cfg, "GET", "/api/audio/stt/status", {}, {})
    assert code == 200, out
    assert out["streaming_engine"] == "nemotron-560ms"
    assert out["streaming_provider_ep"] == "cuda"
    assert "active_provider" in out
    assert "gpu_available" in out["cached"]
    # no secrets / transcript in the health payload
    blob = json.dumps(out).lower()
    assert "api_key" not in blob and "token" not in blob
