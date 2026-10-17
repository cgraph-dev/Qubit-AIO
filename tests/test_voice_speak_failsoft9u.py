"""Voice talk-path fail-soft net (9u): a dead TTS backend must degrade to
silence, never raise into answers/hums/confirmations.

Live 2026-10-1x: dashboard :8890 talked no answers, hummed nothing, and
played no confirmation cues, while text answers and the STT engines stayed
healthy. Every one of those audio paths flows through
``VoiceOrchestrator.speak`` (hums via ``force_local=True``); a missing or
broken TTS voice pack makes ``PiperTTS.load/synthesize`` raise
``TTSUnavailable``, and ``speak`` re-raised it for every non-OpenAI provider
— one missing model collapsed all voice at once (and, with
``speak_web_locally`` on, could break web turns outright). Audio is
best-effort: ``speak`` now logs and returns False.

Companion regression: ``speak_stream`` joined its ``sentences`` generator up
front, so the local-playback loop below iterated an exhausted generator and
streamed replies never sounded on the PC speakers; in web mode it also
ignored ``speak_web_locally`` (which ``speak`` honors). Both are pinned here.

Hermetic: a bare ``VoiceOrchestrator`` (``__new__``, no audio hardware, no
model) with stub TTS/playback. No services, no browser, no live config.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.orchestrator import VoiceOrchestrator  # noqa: E402
from voicebridge.tts.base import TTSUnavailable  # noqa: E402


class _FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node

    def model_dir(self):
        return Path("/tmp/qubit-voicebridge-test-models")


class _BrokenTTS:
    name = "piper"

    def synthesize(self, text, out_path):
        raise TTSUnavailable("piper voice not installed")


class _OpenAIFailingTTS:
    name = "openai"

    def synthesize(self, text, out_path):
        raise TTSUnavailable("openai key missing")


def _orb(*, web_mode=False, speak_locally=True, tts=None):
    orb = VoiceOrchestrator.__new__(VoiceOrchestrator)
    orb.cfg = _FakeCfg({
        "webui": {"speak_web_locally": speak_locally},
        "tts": {"direct": False},
    })
    orb.muted = False
    orb._web_mode = web_mode
    orb._web_spoken = []
    orb._tts = tts if tts is not None else _BrokenTTS()
    orb._cancel = threading.Event()
    orb._barge = None
    orb._barge_stream = None
    orb._interrupt_requested = False
    orb.playback = mock.MagicMock()
    orb.playback.play.return_value = True
    return orb


def _wav_tts():
    tts = mock.MagicMock()
    tts.name = "piper"
    tts.synthesize.side_effect = (
        lambda text, out: Path(str(out)))
    return tts


def test_speak_returns_false_when_tts_synthesize_raises():
    """A dead TTS backend degrades to silence; it must not raise."""
    orb = _orb()
    assert orb.speak("hello there") is False
    orb.playback.play.assert_not_called()


def test_speak_web_mode_records_text_despite_dead_tts():
    """Web turns still record their reply text when local TTS is dead."""
    orb = _orb(web_mode=True, speak_locally=True)
    assert orb.speak("hello there") is False
    assert orb._web_spoken == ["hello there"]
    orb.playback.play.assert_not_called()


def test_speak_web_mode_records_only_when_local_disabled():
    """speak_web_locally=false: record only, no playback attempt, no raise."""
    orb = _orb(web_mode=True, speak_locally=False)
    assert orb.speak("hello there") is True
    assert orb._web_spoken == ["hello there"]
    orb.playback.play.assert_not_called()


def test_speak_force_local_hum_path_fails_soft():
    """Task-progress hums (force_local) survive a dead TTS backend."""
    orb = _orb()
    assert orb.speak("hmm hmm~", name="hum", barge=False,
                     force_local=True) is False


def test_speak_openai_failure_without_fallback_is_silent():
    """An OpenAI TTS failure whose Piper fallback also fails returns False."""
    orb = _orb(tts=_OpenAIFailingTTS())
    with mock.patch("voicebridge.tts.piper.PiperTTS") as piper_cls:
        piper_cls.return_value.load.side_effect = TTSUnavailable("no pack")
        assert orb.speak("hello there") is False
    orb.playback.play.assert_not_called()


def test_speak_stream_generator_input_plays_locally():
    """speak_stream must play generator sentences, not an exhausted join."""
    orb = _orb(tts=_wav_tts())

    def _gen():
        yield "first sentence."
        yield "second sentence."

    spoken = orb.speak_stream(_gen())
    assert spoken == "first sentence. second sentence."
    assert orb.playback.play.call_count == 2


def test_speak_stream_web_mode_honors_speak_web_locally():
    """Web mode: records always; plays locally only when configured."""
    orb = _orb(web_mode=True, speak_locally=True, tts=_wav_tts())

    def _gen():
        yield "streamed reply."

    assert orb.speak_stream(_gen()) == "streamed reply."
    assert orb._web_spoken == ["streamed reply."]
    assert orb.playback.play.call_count == 1

    orb2 = _orb(web_mode=True, speak_locally=False, tts=_wav_tts())
    assert orb2.speak_stream(iter(["streamed reply."])) == "streamed reply."
    assert orb2._web_spoken == ["streamed reply."]
    orb2.playback.play.assert_not_called()


def test_speak_stream_dead_tts_keeps_text():
    """A dead TTS backend keeps the streamed text; synthesis is skipped."""
    orb = _orb(tts=_BrokenTTS())

    def _gen():
        yield "still the reply."

    assert orb.speak_stream(_gen()) == "still the reply."
    orb.playback.play.assert_not_called()


def test_speak_stream_web_mode_without_tts_keeps_text():
    """Web mode records the streamed text even with no TTS provider: the
    browser path synthesizes separately, so the text must survive."""
    orb = _orb(web_mode=True, speak_locally=True)
    orb._tts = None
    assert orb.speak_stream(iter(["recorded anyway."])) == "recorded anyway."
    assert orb._web_spoken == ["recorded anyway."]
    orb.playback.play.assert_not_called()
