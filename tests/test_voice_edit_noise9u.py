"""WebRTC noise-suppression capture-selection tests (no PulseAudio)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import noise


class FakeCfg:
    def __init__(self, data):
        self._d = data

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def test_explicit_device_wins(monkeypatch):
    monkeypatch.setattr(noise, "ensure", lambda cfg: "qubit_aec")
    cfg = FakeCfg({"voice_edit": {"asr_device": "my_mic"}})
    assert noise.resolve_capture_device(cfg) == "my_mic"


def test_auto_uses_raw_source_by_default(monkeypatch):
    # Measured echo-cancel output is ~8x quieter than the raw device, so the
    # default ("auto") records the plain source.
    monkeypatch.setattr(noise, "ensure", lambda cfg: "qubit_aec")
    cfg = FakeCfg({"voice_edit": {}})
    assert noise.resolve_capture_device(cfg) == ""


def test_aec_mode_uses_echo_cancel_source(monkeypatch):
    monkeypatch.setattr(noise, "ensure", lambda cfg: "qubit_aec")
    cfg = FakeCfg({"voice_edit": {"capture_source": "aec"}})
    assert noise.resolve_capture_device(cfg) == "qubit_aec"


def test_disabled_returns_default(monkeypatch):
    called = []
    monkeypatch.setattr(noise, "ensure",
                        lambda cfg: called.append(1) or "qubit_aec")
    cfg = FakeCfg({"voice_edit": {"noise_suppression": False}})
    assert noise.resolve_capture_device(cfg) == ""
    assert called == []


def test_ensure_returns_existing_source(monkeypatch):
    monkeypatch.setattr(noise, "_source_exists", lambda name: True)
    monkeypatch.setattr(noise, "_run", lambda *a, **k: None)
    assert noise.ensure(FakeCfg({"voice_edit": {}})) == "qubit_aec"


def test_ensure_loads_and_releases(monkeypatch):
    state = {"exists": False, "unloaded": None}
    monkeypatch.setattr(noise, "_source_exists", lambda name: state["exists"])
    monkeypatch.setattr(noise, "_run", lambda args, **k: (
        state.__setitem__("unloaded", args[-1]) or "777"
        if args and args[0] == "unload-module" else "777"))
    noise._LOADED_MODULE = None
    assert noise.ensure(FakeCfg({"voice_edit": {}})) == "qubit_aec"
    assert noise._LOADED_MODULE == "777"
    noise.release()
    assert state["unloaded"] == "777"
