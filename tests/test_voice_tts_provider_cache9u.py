"""Regression tests for the voice-turn TTS provider cache (service.py).

MEASURED bottleneck (journal 2026-10-06/07): ``qubit_compat.h_audio_speak``
(POST /api/audio/speak — the ONE local TTS exit every spoken reply uses)
builds a fresh provider and calls ``load()`` on EVERY request. For the
piper/sherpa backend that is a full ONNX reload: ~1000-1140 ms fixed cost
per spoken reply, while real synthesis is 40-300 ms. The voice-turn
timeline's ``answer -> tts.synthesize`` median is 1095 ms, dominated by it.

Fix: ``VoiceEditService.__init__`` installs a memoizing wrapper over
``qubit_compat._tts_from_cfg`` so each config reuses ONE warm provider.
These tests pin the contract without loading any model.
"""
from __future__ import annotations

import types

import pytest

from voicebridge.voice_edit import service as svc


class _FakeTTS:
    def __init__(self) -> None:
        self.loads = 0
        self.synths = 0
        self._loaded = False

    def load(self) -> None:
        # Mirrors PiperTTS: the heavy model load happens ONCE per provider
        # object (``_get_sherpa_tts`` caches ``self._sherpa``); later calls
        # are cheap no-ops.
        if not self._loaded:
            self._loaded = True
            self.loads += 1

    def synthesize(self, text: str, out: object = None) -> object:
        self.synths += 1
        return out


class _Factory:
    """Counts constructions and hands out distinct fake providers."""

    def __init__(self) -> None:
        self.calls = 0
        self.made: list = []

    def __call__(self, cfg: object) -> _FakeTTS:
        self.calls += 1
        tts = _FakeTTS()
        self.made.append(tts)
        return tts


class _Cfg:
    """Minimal config double supporting the key signature lookups."""

    def __init__(self, tts: dict | None = None, path: str = "/tmp/v.yaml") -> None:
        self._tts = tts or {}
        self.path = path

    def get(self, *path: str, default: object = None) -> object:
        if path == ("tts",):
            return self._tts
        return default

    def model_dir(self) -> str:
        return "/models"


# ---------------------------------------------------------------------------
# The bug, restated (baseline that motivates the fix)
# ---------------------------------------------------------------------------

def test_per_call_factory_rebuilds_every_time() -> None:
    """Without the cache, two speak calls build two providers (the ~1 s bug)."""
    factory = _Factory()
    cfg = _Cfg()
    factory(cfg).load()
    factory(cfg).load()
    assert factory.calls == 2
    assert factory.made[0] is not factory.made[1]


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------

def test_cached_provider_builds_once_and_returns_same_object() -> None:
    factory = _Factory()
    cached = svc._make_cached_tts_provider(factory)
    cfg = _Cfg()

    first = cached(cfg)
    second = cached(cfg)

    assert factory.calls == 1
    assert first is second


def test_cache_key_changes_rebuild_provider() -> None:
    factory = _Factory()
    cached = svc._make_cached_tts_provider(factory)

    a = cached(_Cfg({"voice": "en_US-amy-medium"}))
    b = cached(_Cfg({"voice": "en_GB-alan-medium"}))
    same = cached(_Cfg({"voice": "en_US-amy-medium"}, path="/tmp/other.yaml"))

    assert factory.calls == 3
    assert a is not b
    assert a is not same  # a different config identity is a different key


def test_load_is_noop_after_first_on_the_shared_object() -> None:
    """Mirrors h_audio_speak: load()+synthesize() twice -> one load, two synths."""
    factory = _Factory()
    cached = svc._make_cached_tts_provider(factory)
    cfg = _Cfg()

    for text in ("Hi.", "Your name is Tricky."):
        tts = cached(cfg)
        tts.load()
        tts.synthesize(text)

    assert factory.calls == 1
    assert factory.made[0].loads == 1
    assert factory.made[0].synths == 2


# ---------------------------------------------------------------------------
# Installer wiring (hermetic: a test double stands in for qubit_compat)
# ---------------------------------------------------------------------------

def test_install_patches_the_module_factory() -> None:
    factory = _Factory()
    module = types.SimpleNamespace(_tts_from_cfg=factory)

    assert svc.install_tts_provider_cache(module) is True
    assert module._tts_from_cfg is not factory

    cfg = _Cfg()
    module._tts_from_cfg(cfg).load()
    module._tts_from_cfg(cfg).load()
    assert factory.calls == 1


def test_install_is_idempotent() -> None:
    factory = _Factory()
    module = types.SimpleNamespace(_tts_from_cfg=factory)

    assert svc.install_tts_provider_cache(module) is True
    installed = module._tts_from_cfg
    assert svc.install_tts_provider_cache(module) is True
    assert module._tts_from_cfg is installed  # not wrapped twice


def test_install_returns_false_without_a_factory() -> None:
    assert svc.install_tts_provider_cache(types.SimpleNamespace()) is False


def test_installed_wrapper_keeps_single_caller_contract() -> None:
    """h_audio_speak has exactly one caller; the wrapper is call-compatible."""
    factory = _Factory()
    module = types.SimpleNamespace(_tts_from_cfg=factory)
    svc.install_tts_provider_cache(module)

    out = _FakeTTS()
    got = module._tts_from_cfg(_Cfg())
    got.load()
    assert got.synthesize("hello", out) is out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
