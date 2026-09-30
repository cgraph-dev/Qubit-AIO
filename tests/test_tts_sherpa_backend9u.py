"""Piper TTS shared-ORT backend (sherpa-onnx) tests.

The ``sherpa`` backend renders the piper voice with ``sherpa_onnx.OfflineTts``,
sharing the ASR ONNX Runtime so the process loads one ORT (no TTS/ASR load-order
coupling). The third-party ``piper`` backend is the switchable fallback.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

MODELS = Path.home() / ".local/share/qubit-voicebridge/models"
PACK = MODELS / "vits-piper-en_US-amy-medium"


def test_piper_backend_default_is_sherpa(tmp_path):
    from voicebridge.config import Config
    p = tmp_path / "voice.yaml"
    p.write_text("webui:\n  token: t\n")
    cfg = Config.load(p)
    assert cfg.get("tts", "piper_backend", default="") == "sherpa"


def test_backend_resolution_and_fallback(tmp_path):
    from voicebridge.tts.piper import PiperTTS
    # Explicit piper is honoured regardless of pack availability.
    t = PiperTTS(model_dir=MODELS, backend="piper")
    assert t._resolve_backend() == "piper"
    # Explicit sherpa with no pack -> safe fallback to piper.
    empty = PiperTTS(model_dir=tmp_path, backend="sherpa")
    assert empty._resolve_backend() == "piper"


@pytest.mark.skipif(not PACK.is_dir(), reason="sherpa piper voice pack not installed")
def test_sherpa_backend_synthesizes_valid_wav(tmp_path):
    from voicebridge.tts.piper import PiperTTS
    t = PiperTTS(model_dir=MODELS, voice="en_US-amy-medium", backend="sherpa")
    assert t._resolve_backend() == "sherpa"
    out = t.synthesize("Qubit shared runtime test.", out_path=tmp_path / "a.wav")
    assert out.exists() and out.stat().st_size > 1000
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2
        assert wf.getframerate() > 8000
        assert wf.getnframes() > 0


@pytest.mark.skipif(not PACK.is_dir(), reason="sherpa piper voice pack not installed")
def test_sherpa_backend_does_not_need_python_onnxruntime(tmp_path):
    """The sherpa backend must work without importing the piper onnxruntime."""
    import sys
    # Run in a subprocess so we do not pollute this interpreter's module table.
    import subprocess
    code = (
        "import sys;"
        "from pathlib import Path;"
        "from voicebridge.tts.piper import PiperTTS;"
        "t=PiperTTS(model_dir=Path.home()/'.local/share/qubit-voicebridge/models',"
        " voice='en_US-amy-medium', backend='sherpa');"
        f"t.synthesize('ok', out_path=Path(r'{tmp_path}/b.wav'));"
        "print('ORT_IMPORTED' if 'onnxruntime' in sys.modules else 'ORT_ABSENT')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-400:]
    assert "ORT_ABSENT" in out.stdout, out.stdout + out.stderr[-200:]
