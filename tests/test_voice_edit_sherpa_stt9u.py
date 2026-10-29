"""Whole-utterance Sherpa (parakeet) STT: the browser-local accuracy path."""
from pathlib import Path

import voicebridge.voice_edit.asr_accurate as A


class Cfg:
    path = Path("/tmp/qubit-sherpa-test.yaml")

    def __init__(self, asr=None):
        self._asr = asr if asr is not None else {
            "provider": "sherpa", "engine": "parakeet",
            "provider_ep": "cpu", "num_threads": 2,
            "language": "en", "hotwords": "Qubit",
            "hotwords_score": 1.5,
        }

    def section(self, name):
        return self._asr if name == "asr" else {}

    def model_dir(self):
        return Path("/tmp/qubit-sherpa-models")


class FakeSherpa:
    created = 0
    kwargs = None

    def __init__(self, **kw):
        FakeSherpa.created += 1
        FakeSherpa.kwargs = kw

    def decode_pcm(self, audio, sr=16000):
        return "open youtube"


def test_transcribe_sherpa_uses_configured_parakeet(monkeypatch):
    import voicebridge.asr.sherpa as S
    A.reset_sherpa_cache()
    FakeSherpa.created = 0
    monkeypatch.setattr(S, "SherpaASR", FakeSherpa)
    text, reason = A.transcribe_sherpa(Cfg(), b"\x00\x00" * 1600)
    assert (text, reason) == ("open youtube", "")
    assert FakeSherpa.created == 1
    assert FakeSherpa.kwargs["engine"] == "parakeet"
    assert FakeSherpa.kwargs["provider"] == "cpu"


def test_transcribe_sherpa_caches_the_recognizer(monkeypatch):
    import voicebridge.asr.sherpa as S
    A.reset_sherpa_cache()
    FakeSherpa.created = 0
    monkeypatch.setattr(S, "SherpaASR", FakeSherpa)
    A.transcribe_sherpa(Cfg(), b"\x00\x00" * 1600)
    A.transcribe_sherpa(Cfg(), b"\x00\x00" * 1600)
    assert FakeSherpa.created == 1


def test_transcribe_sherpa_empty_text_is_typed(monkeypatch):
    import voicebridge.asr.sherpa as S
    A.reset_sherpa_cache()

    class Empty(FakeSherpa):
        def decode_pcm(self, audio, sr=16000):
            return ""

    monkeypatch.setattr(S, "SherpaASR", Empty)
    text, reason = A.transcribe_sherpa(Cfg(), b"\x00\x00" * 1600)
    assert text == "" and reason == "empty sherpa transcript"


def test_transcribe_sherpa_skips_when_not_configured():
    text, reason = A.transcribe_sherpa(
        Cfg({"provider": "sherpa"}), b"\x00\x00" * 1600)
    assert text == "" and "not configured" in reason


def test_prewarm_sherpa_loads_configured_engine(monkeypatch):
    import voicebridge.asr.sherpa as S
    A.reset_sherpa_cache()
    FakeSherpa.created = 0
    loaded = []

    class Warm(FakeSherpa):
        def load(self):
            loaded.append(True)

    monkeypatch.setattr(S, "SherpaASR", Warm)
    assert A.prewarm_sherpa(Cfg()) is True
    assert loaded == [True]
    assert FakeSherpa.created == 1


def test_prewarm_sherpa_skips_when_not_configured():
    assert A.prewarm_sherpa(Cfg({"provider": "sherpa"})) is False
