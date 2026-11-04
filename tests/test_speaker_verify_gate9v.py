"""Speaker verification (voice-ID) is actually wired to barge-in.

Before this fix ``speaker_verify.enabled: true`` was inert: the orchestrator
never constructed a verifier after a refactor and the desktop helper had no
speaker check at all, so the config's "only the enrolled voice can barge in"
was false. The shared ``load_verifier`` / ``allows_pcm`` seam is now used by
both surfaces. Voice-ID remains fail-open (no model/no enrollment/errors must
never wedge normal speech).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import voice_id


class _Cfg:
    def __init__(self, enabled=True):
        self._d = {
            "speaker_verify": {"enabled": enabled, "name": "user",
                               "threshold": 0.65},
        }

    def get(self, section, key, default=None):
        node = self._d.get(section, {})
        return node.get(key, default)

    def model_dir(self):
        return Path("/tmp/models")

    def state_dir(self):
        return Path("/tmp/state")


class _Verifier:
    def __init__(self, ready=True, result=(True, 0.9), error=None):
        self.ready = ready
        self._result = result
        self._error = error
        self.calls = 0
        self.num_speakers = 1

    def load(self):
        return None

    def verify(self, samples, sample_rate=16000):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result


@pytest.fixture(autouse=True)
def _no_journal(monkeypatch):
    monkeypatch.setattr(voice_id, "log_event", lambda *a, **k: None)


def test_load_verifier_disabled_is_none(monkeypatch):
    monkeypatch.setattr(voice_id, "SpeakerVerifier",
                        lambda *a, **k: _Verifier())
    assert voice_id.load_verifier(_Cfg(enabled=False)) is None


def test_load_verifier_ready_returns_verifier(monkeypatch):
    made = []

    def factory(*args, **kwargs):
        v = _Verifier(ready=True)
        made.append((args, kwargs, v))
        return v

    monkeypatch.setattr(voice_id, "SpeakerVerifier", factory)
    got = voice_id.load_verifier(_Cfg())
    assert isinstance(got, _Verifier)
    assert made and made[0][1]["name"] == "user"
    assert made[0][1]["threshold"] == 0.65


def test_load_verifier_no_enrollment_is_none(monkeypatch):
    events = []
    monkeypatch.setattr(voice_id, "log_event",
                        lambda event, **kw: events.append((event, kw)))
    monkeypatch.setattr(voice_id, "SpeakerVerifier",
                        lambda *a, **k: _Verifier(ready=False))
    assert voice_id.load_verifier(_Cfg()) is None
    assert any(e[0] == "speaker.verify.no_enrollment" for e in events)


def test_load_verifier_load_error_fails_open(monkeypatch):
    events = []

    def boom(*a, **k):
        raise voice_id.SpeakerUnavailable("model missing")

    monkeypatch.setattr(voice_id, "log_event",
                        lambda event, **kw: events.append((event, kw)))
    monkeypatch.setattr(voice_id, "SpeakerVerifier", boom)
    assert voice_id.load_verifier(_Cfg()) is None
    assert any(e[0] == "speaker.verify.unavailable" for e in events)


def test_allows_pcm_without_verifier_or_enrollment_is_true():
    assert voice_id.allows_pcm(None, b"\x00\x01" * 16000) is True
    assert voice_id.allows_pcm(_Verifier(ready=False),
                               b"\x00\x01" * 16000) is True


def test_allows_pcm_short_sample_is_true_without_embedding():
    v = _Verifier(result=(False, 0.1))
    assert voice_id.allows_pcm(v, b"\x00\x01" * 100) is True
    assert v.calls == 0


def test_allows_pcm_accepts_owner_and_rejects_impostor():
    owner = _Verifier(result=(True, 0.88))
    assert voice_id.allows_pcm(owner, b"\x00\x01" * 16000) is True
    impostor = _Verifier(result=(False, 0.21))
    assert voice_id.allows_pcm(impostor, b"\x00\x01" * 16000) is False


def test_allows_pcm_embedding_error_fails_open():
    v = _Verifier(error=RuntimeError("embedding failed"))
    assert voice_id.allows_pcm(v, b"\x00\x01" * 16000) is True


def test_helper_barge_gate_uses_the_shared_rule(monkeypatch):
    from voicebridge.desktop_helper import DesktopHelper

    owner = _Verifier(result=(True, 0.9))
    mon = _Verifier(result=(False, 0.1))
    dummy = DesktopHelper.__new__(DesktopHelper)
    dummy._speaker = owner
    dummy._speaker_loaded = True
    assert dummy._barge_speaker_allows(b"\x00\x01" * 16000) is True
    dummy._speaker = mon
    assert dummy._barge_speaker_allows(b"\x00\x01" * 16000) is False
