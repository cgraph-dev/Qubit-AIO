"""Whole-utterance accurate local STT for voice typing.

Hermetic: every test injects a ``loader`` seam or a fake cfg, so no model is
downloaded, no GPU/CPU model is loaded, and no audio device is opened.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.asr_accurate import (  # noqa: E402
    ACCURATE_MODELS, DEFAULT_ACCURATE_MODEL, AccurateResult, AccurateTranscriber,
    get_transcriber, is_available, reset_transcribers,
)


class FakeCfg:
    """The cfg surface the accurate decoder reads: ``.get``, ``.path``,
    ``.model_dir``."""

    def __init__(self, data=None, path=None, model_dir=None):
        self.data = data or {}
        self.path = path
        self._model_dir = model_dir

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node

    def model_dir(self):
        return self._model_dir


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_transcribers()
    yield
    reset_transcribers()


# ---------------------------------------------------------------------------
# injected loader seam
# ---------------------------------------------------------------------------
def test_injected_loader_called_with_args_and_returned_unchanged():
    cfg = FakeCfg()
    seen = {}

    def loader(c, model, pcm, sample_rate):
        seen.update(cfg=c, model=model, pcm=pcm, sample_rate=sample_rate)
        return AccurateResult(text="hello world", ok=True, engine="fake",
                              device="cpu")

    tr = AccurateTranscriber(cfg, loader=loader)
    out = tr.transcribe(b"\x00\x00\x01\x00", sample_rate=8000)
    assert out == AccurateResult(text="hello world", ok=True, engine="fake",
                                 device="cpu")
    assert seen["cfg"] is cfg
    assert seen["model"] == DEFAULT_ACCURATE_MODEL
    assert seen["pcm"] == b"\x00\x00\x01\x00"
    assert seen["sample_rate"] == 8000


def test_transcribe_default_sample_rate_is_16000():
    seen = {}

    def loader(c, model, pcm, sample_rate):
        seen["sample_rate"] = sample_rate
        return AccurateResult(ok=True)

    AccurateTranscriber(FakeCfg(), loader=loader).transcribe(b"\x00\x00")
    assert seen["sample_rate"] == 16000


# ---------------------------------------------------------------------------
# model resolution
# ---------------------------------------------------------------------------
def test_default_model_is_small():
    assert DEFAULT_ACCURATE_MODEL == "small"
    assert AccurateTranscriber(FakeCfg()).model == "small"


def test_cfg_stt_model_overrides_default():
    cfg = FakeCfg({"voice_edit": {"stt_model": "base"}})
    assert AccurateTranscriber(cfg).model == "base"


def test_explicit_model_argument_wins():
    cfg = FakeCfg({"voice_edit": {"stt_model": "base"}})
    assert AccurateTranscriber(cfg, model="large-v3").model == "large-v3"


def test_invalid_configured_model_falls_back_to_default():
    cfg = FakeCfg({"voice_edit": {"stt_model": "gigantic"}})
    assert AccurateTranscriber(cfg).model == DEFAULT_ACCURATE_MODEL


# ---------------------------------------------------------------------------
# failure is typed, never raised
# ---------------------------------------------------------------------------
def test_loader_raising_returns_typed_failure_never_raises():
    def boom(c, model, pcm, sample_rate):
        raise RuntimeError("no model on disk")

    out = AccurateTranscriber(FakeCfg(), loader=boom).transcribe(b"\x00\x00")
    assert out.ok is False
    assert "no model on disk" in out.reason
    assert 0 < len(out.reason) <= 160
    assert out.text == ""


def test_reason_is_truncated_and_single_line():
    def boom(c, model, pcm, sample_rate):
        raise RuntimeError("x" * 500 + "\nsecond line")

    out = AccurateTranscriber(FakeCfg(), loader=boom).transcribe(b"\x00\x00")
    assert out.ok is False
    assert len(out.reason) <= 160
    assert "\n" not in out.reason


def test_loader_returning_wrong_type_is_a_typed_failure():
    out = AccurateTranscriber(FakeCfg(), loader=lambda *a: "nope").transcribe(b"\x00\x00")
    assert out.ok is False
    assert out.reason


# ---------------------------------------------------------------------------
# module-level cache
# ---------------------------------------------------------------------------
def test_get_transcriber_cached_for_same_cfg():
    cfg = FakeCfg(path="/tmp/voice.yaml")
    first = get_transcriber(cfg)
    assert get_transcriber(cfg) is first


def test_reset_transcribers_clears_the_cache():
    cfg = FakeCfg(path="/tmp/voice.yaml")
    first = get_transcriber(cfg)
    reset_transcribers()
    second = get_transcriber(cfg)
    assert second is not first


def test_cache_key_separates_distinct_cfgs():
    a = FakeCfg(path="/tmp/a.yaml")
    b = FakeCfg(path="/tmp/b.yaml")
    assert get_transcriber(a) is not get_transcriber(b)


# ---------------------------------------------------------------------------
# interface constants / probe
# ---------------------------------------------------------------------------
def test_accurate_models_are_the_documented_names():
    assert ACCURATE_MODELS == ("tiny", "base", "small", "medium", "large-v3")
    assert DEFAULT_ACCURATE_MODEL in ACCURATE_MODELS


def test_is_available_is_a_bool_and_does_not_raise():
    assert isinstance(is_available(FakeCfg()), bool)
