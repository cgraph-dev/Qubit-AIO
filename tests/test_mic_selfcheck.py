"""Hermetic tests for the automatic mic-signal self-check (the "check the mic
after restart" feature).

Covers mic_selfcheck typing (HEALTHY / SILENT / DEGRADED / UNAVAILABLE), the
webui /api/status surfacing, and the `voicebridge audio check` CLI guard that
refuses to open a second input stream while the bridge holds the mic (one-input-
stream rule on the Scarlett).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n"
                 "mic:\n  sample_rate: 48000\n  channels: 1\n")
    return Config.load(p)


class _Stream:
    """Fake sounddevice InputStream returning a constant-level block forever."""

    def __init__(self, value):
        self._value = float(value)
        self.channels = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n):
        return np.full((n, 1), self._value, dtype=np.float32), None


def _patch_stream(monkeypatch, value):
    import voicebridge.audio.mic as M
    monkeypatch.setattr(M.sd, "InputStream", lambda **kw: _Stream(value))


def test_selfcheck_healthy(monkeypatch, tmp_path):
    from voicebridge.audio.mic import mic_selfcheck
    # clear signal chain (peak ~0.5)
    _patch_stream(monkeypatch, 0.5)
    res = mic_selfcheck(_cfg(tmp_path), seconds=0.05)
    assert res["state"] == "HEALTHY"
    assert res["peak"] >= 1e-3
    assert res["sample_rate"] == 48000 and res["channels"] == 1


def test_selfcheck_silent(monkeypatch, tmp_path):
    from voicebridge.audio.mic import mic_selfcheck
    # ~digital zero (the broken-after-restart state): peak below the floor
    _patch_stream(monkeypatch, 1e-6)
    res = mic_selfcheck(_cfg(tmp_path), seconds=0.05)
    assert res["state"] == "SILENT"
    assert "phantom" in res["hint"].lower()


def test_selfcheck_degraded_quiet(monkeypatch, tmp_path):
    from voicebridge.audio.mic import mic_selfcheck
    # very quiet but not digital-zero
    _patch_stream(monkeypatch, 5e-5)
    res = mic_selfcheck(_cfg(tmp_path), seconds=0.05)
    assert res["state"] == "DEGRADED"


def test_selfcheck_unavailable(monkeypatch, tmp_path):
    import voicebridge.audio.mic as M
    from voicebridge.audio.mic import mic_selfcheck

    def _boom(**kw):
        raise RuntimeError("device busy")
    monkeypatch.setattr(M.sd, "InputStream", _boom)
    res = mic_selfcheck(_cfg(tmp_path))
    assert res["state"] == "UNAVAILABLE"
    assert res["error"]


def test_webui_status_surfaces_mic_selfcheck(tmp_path):
    from voicebridge.webui import WebUI

    class _FakeOrch:
        def __init__(self):
            self.task = type("T", (), {"active": False})()
            self._route = "gateway"
            self._t0 = 0.0
            self._mic_selfcheck = {"state": "SILENT", "peak": 1e-6}
            self.cfg = type("C", (), {
                "get": lambda self, *p, **kw: kw.get("default"),
                "section": lambda self, n: {},
            })()
            self._tts = None
            self._needs_answer = lambda: False

        def _needs_answer(self):
            return False

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=tmp_path)
    st = ui.status()
    assert st["mic"]["state"] == "SILENT"
    assert "peak" in st["mic"]


def test_cli_audio_check_refused_while_bridge_running(monkeypatch, capsys):
    from argparse import Namespace
    import voicebridge.cli as C
    monkeypatch.setattr(C, "_bridge_service_active", lambda: True)
    rc = C._cmd_audio(_cfg(Path("/tmp")), Namespace(audio_command="check",
                                                    json=False))
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED" in out and "already holds" in out


def test_cli_audio_check_runs_when_bridge_down(monkeypatch, capsys, tmp_path):
    from argparse import Namespace
    import voicebridge.cli as C
    monkeypatch.setattr(C, "_bridge_service_active", lambda: False)
    monkeypatch.setattr(
        "voicebridge.audio.mic.mic_selfcheck",
        lambda cfg, seconds=1.0: {"state": "HEALTHY", "peak": 0.5,
                                  "hint": "microphone signal chain OK"})
    rc = C._cmd_audio(_cfg(tmp_path), Namespace(audio_command="check",
                                                json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "HEALTHY" in out