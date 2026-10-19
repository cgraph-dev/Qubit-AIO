"""Focused regression: ``POST /api/audio/speak`` sanitizes markdown.

Ported enforcement from Starnet ``frontend/app/voice.js:speakable()``:
chat replies are markdown by construction, and without sanitizing the
synthesizer reads formatting literally (``"asterisk asterisk"``,
spelled-out URLs, ``"pipe pipe"`` choice markers). Hermetic: the TTS
provider is faked and captures the exact text it was asked to render.

Covers ``voicebridge/tts/base.py:speakable_text`` through the served
route ``voicebridge/qubit_compat.py:h_audio_speak``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _no_background_warm(monkeypatch):
    """This module pins text sanitization, not worker lifecycle.

    ``h_audio_speak`` now warms the shared worker on arrival; keeping that
    inert avoids spawning a real model load beside these fake-provider tests.
    """
    from voicebridge.tts import synth_worker as SW
    monkeypatch.setattr(SW, "warm_async", lambda cfg: False)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _CaptureTTS:
    """Fake provider: records the synthesis text, writes canned WAV bytes."""

    name = "capture"

    def __init__(self):
        self.texts = []

    def load(self):
        pass

    def synthesize(self, text, out_path):
        self.texts.append(text)
        out = Path(out_path)
        out.write_bytes(b"RIFF" + bytes(40))
        return out


def _patch_worker(monkeypatch, fake):
    """Serve the speak call through the worker seam with a recording fake."""
    from voicebridge.tts import synth_worker as SW

    def _synth(cfg, text, out, *, stopped=None, timeout=60.0):
        fake.synthesize(text, out)
        return fake.name

    monkeypatch.setattr(SW, "synthesize_cancellable", _synth)


def _speak(tmp_path, monkeypatch, text):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    fake = _CaptureTTS()
    _patch_worker(monkeypatch, fake)
    code, out = HC.dispatch(cfg, "POST", "/api/audio/speak", {}, {"text": text})
    return code, out, fake


def test_speak_strips_markdown_emphasis_and_headers(tmp_path, monkeypatch):
    code, out, fake = _speak(
        tmp_path, monkeypatch,
        "## Done\n**bold** and *italic* plus _under_ text")
    assert code == 200 and out["ok"] is True, out
    (spoken,) = fake.texts
    assert "**" not in spoken and "##" not in spoken
    assert "bold" in spoken and "Done" in spoken


def test_speak_replaces_bare_urls_and_keeps_link_labels(tmp_path, monkeypatch):
    code, out, fake = _speak(
        tmp_path, monkeypatch,
        "see [the report](https://example.com/r) or https://example.com/x")
    assert code == 200 and out["ok"] is True, out
    (spoken,) = fake.texts
    assert "https://" not in spoken
    assert "the report" in spoken
    assert "a link" in spoken


def test_speak_drops_code_fences_and_choice_markers(tmp_path, monkeypatch):
    code, out, fake = _speak(
        tmp_path, monkeypatch,
        "run `qubit status` now\n```\nrm -rf /\n```\nTASK_QUESTION: pick one | a | b")
    assert code == 200 and out["ok"] is True, out
    (spoken,) = fake.texts
    assert "rm -rf" not in spoken
    assert "TASK_QUESTION" not in spoken
    assert "qubit status" in spoken


def test_speak_replaces_file_paths(tmp_path, monkeypatch):
    code, out, fake = _speak(
        tmp_path, monkeypatch, "saved to /tmp/qubit-voicebridge/out.wav ok")
    assert code == 200 and out["ok"] is True, out
    (spoken,) = fake.texts
    assert "/tmp/qubit-voicebridge/out.wav" not in spoken
    assert "that file" in spoken


def test_speak_plain_text_passes_through_unchanged(tmp_path, monkeypatch):
    code, out, fake = _speak(tmp_path, monkeypatch, "Hello there.")
    assert code == 200 and out["ok"] is True, out
    assert fake.texts == ["Hello there."]


def test_speak_pure_markup_is_typed_400_not_silent_audio(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    fake = _CaptureTTS()
    _patch_worker(monkeypatch, fake)
    code, out = HC.dispatch(
        cfg, "POST", "/api/audio/speak", {}, {"text": "```\nonly code\n```"})
    assert code == 400 and out["ok"] is False, out
    assert out["code"] == "AUDIO_TEXT_REQUIRED"
    assert fake.texts == []
    assert "data_url" not in out


def test_speakable_text_unit_cases():
    from voicebridge.tts.base import speakable_text

    assert speakable_text("") == ""
    assert speakable_text("  spaced   out  ") == "spaced out"
    assert "http" not in speakable_text("go to https://a.b/c now")
    assert speakable_text("# Title\n- one\n1. two") == "Title one two"
