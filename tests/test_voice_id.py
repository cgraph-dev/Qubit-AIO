"""Speaker verification (voice-ID) tests — real model, real wavs.

Uses the sherpa-onnx wespeaker model + the release test wavs:
  fangjun-sr-1/2.wav  (speaker A, enrollment)
  leijun-sr-1.wav     (speaker B, must be REJECTED)
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from voicebridge.voice_id import SpeakerVerifier, SpeakerUnavailable

MODELS = Path.home() / ".local/share/openclaw-voice-bridge/models"


@pytest.fixture()
def verifier(tmp_path):
    sv = SpeakerVerifier(MODELS, tmp_path, name="user", threshold=0.65)
    sv.load()
    return sv


def _wav(name: str) -> str:
    p = MODELS / name
    if not p.exists():
        pytest.skip(f"test wav missing: {p}")
    return str(p)


def test_model_present():
    assert (MODELS / "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx").exists(), \
        "eres2net model missing (download from speaker-recongition-models release)"


def test_enroll_and_self_verify(verifier):
    score = verifier.enroll(_wav("fangjun-sr-1.wav"))
    assert verifier.ready
    assert score > 0.7, f"enrollment self-score too low: {score}"
    # same speaker, second clip
    accepted, s = verifier.verify_wav(_wav("fangjun-sr-2.wav"))
    assert accepted, f"same speaker rejected (score {s:.3f})"
    assert s > 0.7


def test_reject_other_speaker(verifier):
    verifier.enroll(_wav("fangjun-sr-1.wav"))
    accepted, s = verifier.verify_wav(_wav("leijun-sr-1.wav"))
    assert not accepted, f"different speaker accepted (score {s:.3f})"


def test_persistence_reload(verifier, tmp_path):
    verifier.enroll(_wav("fangjun-sr-1.wav"))
    npy = tmp_path / "speakers" / "user.npy"
    assert npy.exists()
    # fresh instance (no manager in memory) must reload from disk
    sv2 = SpeakerVerifier(MODELS, tmp_path, name="user", threshold=0.65)
    sv2.load()
    assert sv2.ready
    accepted, s = sv2.verify_wav(_wav("fangjun-sr-2.wav"))
    assert accepted


def test_too_short_rejected(verifier, tmp_path):
    import wave
    import numpy as np
    short = tmp_path / "short.wav"
    with wave.open(str(short), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.zeros(8000, dtype=np.int16).tobytes())  # 0.5s
    with pytest.raises(SpeakerUnavailable):
        verifier.embedding_from_wav(short)


def test_not_ready_without_enrollment(tmp_path):
    sv = SpeakerVerifier(MODELS, tmp_path, name="user", threshold=0.65)
    sv.load()
    assert not sv.ready
    accepted, s = sv.verify(None)  # type: ignore[arg-type]
    assert accepted is False and s == 0.0
