"""Packet 8U — wake-word spotter: readiness, file detection, parity.

Hermetic: isolated XDG/state/model dirs, synthetic WAVs, stubbed
sherpa spotter for wiring. Without model files every surface reports
typed NO_MODEL with the exact fetch step; nothing downloads.
"""
from __future__ import annotations

import io
import json
import struct
import wave
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


def _wav(path: Path, seconds: float = 1.0, rate: int = 16000):
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))


def test_status_no_model_names_fetch_step(tmp_path, monkeypatch):
    from voicebridge import wake as WK

    cfg = _cfg(tmp_path, monkeypatch)
    out = WK.status(cfg)
    assert out["state"] == "NO_MODEL"
    assert out["code"] == WK.NO_MODEL_CODE
    assert "keywords.txt" in out["detail"]
    assert set(out["missing"]) == set(WK.MODEL_FILES)


def test_status_ready_with_model_files(tmp_path, monkeypatch):
    from voicebridge import wake as WK

    cfg = _cfg(tmp_path, monkeypatch)
    root = WK.model_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    for name in WK.MODEL_FILES:
        (root / name).write_bytes(b"stub")
    (root / "keywords.txt").write_text("hey qubit/0.3\n")
    out = WK.status(cfg)
    assert out["state"] == "READY"
    assert out["keywords"] == ["hey qubit"]


def test_detect_file_rejects_bad_input(tmp_path, monkeypatch):
    from voicebridge import wake as WK

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(WK.WakeError) as exc:
        WK.detect_file(cfg, "relative.wav")
    assert exc.value.code == WK.BAD_INPUT_CODE
    bad = tmp_path / "stereo.wav"
    with wave.open(str(bad), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(16000)
        fh.writeframes(b"\x00\x00" * 160)
    with pytest.raises(WK.WakeError) as exc:
        WK.detect_file(cfg, str(bad))
    assert exc.value.code == WK.BAD_INPUT_CODE


def test_detect_file_no_model_is_typed(tmp_path, monkeypatch):
    from voicebridge import wake as WK

    cfg = _cfg(tmp_path, monkeypatch)
    wav = tmp_path / "silence.wav"
    _wav(wav)
    with pytest.raises(WK.WakeError) as exc:
        WK.detect_file(cfg, str(wav))
    assert exc.value.code == WK.NO_MODEL_CODE


def test_detect_file_wiring_with_stubbed_spotter(tmp_path, monkeypatch):
    from voicebridge import wake as WK

    cfg = _cfg(tmp_path, monkeypatch)
    root = WK.model_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    for name in WK.MODEL_FILES:
        (root / name).write_bytes(b"stub")
    (root / "keywords.txt").write_text("hey qubit/0.3\n")
    wav = tmp_path / "utterance.wav"
    _wav(wav, seconds=1.0)

    calls = []

    class _Result:
        text = "hey qubit"
        tokens = ["hey", "qubit"]

    class _Stream:
        def __init__(self):
            self.chunks = []

        def accept_waveform(self, rate, chunk):
            assert rate == 16000
            self.chunks.append(len(chunk))

    class _Spotter:
        def __init__(self, **kw):
            calls.append(kw)

        def create_stream(self):
            return _Stream()

        def is_ready(self, stream):
            return False

        def decode_stream(self, stream):
            pass

        def get_result(self, stream):
            return _Result()

        def timestamps(self, stream):
            return []

        def reset_stream(self, stream):
            pass

    import sys as _sys
    stub = type("M", (), {"KeywordSpotter": _Spotter})
    monkeypatch.setitem(_sys.modules, "sherpa_onnx", stub)
    out = WK.detect_file(cfg, str(wav))
    assert out["state"] == "OK"
    assert out["samples"] == 16000
    assert calls and calls[0]["keywords_file"].endswith("keywords.txt")


def test_cli_wake_status_and_probe(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import wake as WK

    cfg = _cfg(tmp_path, monkeypatch)

    def _run(cmd, **kw):
        args = type("A", (), {"audio_command": cmd, "json": True,
                              "wav": kw.get("wav", ""),
                              "threshold": kw.get("threshold", 0.25)})()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_audio(cfg, args)
        return rc, json.loads(buf.getvalue())

    rc, body = _run("wake-status")
    assert rc == 1 and body["state"] == "NO_MODEL"
    wav = tmp_path / "silence.wav"
    _wav(wav)
    rc, body = _run("wake-probe", wav=str(wav))
    assert rc == 2 and body["code"] == WK.NO_MODEL_CODE


def test_voice_wake_status_parity(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = VC.resolve("qubit, is wake word ready")
    assert res.kind == VC.MATCH and res.entry.id == "wake_status"
    out = VC.execute(_ctx(cfg), res)
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert "model missing" in out.speech.lower()


def test_dashboard_wake_status_parity(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as D

    cfg = _cfg(tmp_path, monkeypatch)
    out = D.run_registry_op(cfg, "wake_status", {},
                            actor="dashboard:user", session="dashboard")
    assert out["state"] == "executed", out
