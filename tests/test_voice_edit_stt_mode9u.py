"""Voice-edit STT mode tests: BufferingMic, accurate path, wake fallback.

Pure unit tests -- no microphone, no socket, no model. The accurate transcriber
module (``voicebridge.voice_edit.asr_accurate``) is injected as a fake so this
suite passes whether or not it is present on disk.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.voice_edit as ve
from voicebridge.voice_edit import stt_client as sc
from voicebridge.voice_edit import wake
from voicebridge.voice_edit.stt_client import BufferingMic, transcribe_accurate


class FakeCfg:
    def __init__(self, *, mode: str = "accurate", beep: bool = False,
                 wake_cfg=None) -> None:
        self._d = {"voice_edit": {"beep": beep, "stt_mode": mode,
                                  "wake": wake_cfg or {"enabled": True}}}

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


class FakeMic:
    def __init__(self, frames=None) -> None:
        self._frames = list(frames or [])
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def available(self) -> bool:
        return True

    def read_frame(self, timeout: float = 0.2):
        return self._frames.pop(0) if self._frames else None


# ---------------------------------------------------------------------------
# BufferingMic
# ---------------------------------------------------------------------------
def test_buffering_mic_forwards_and_joins_frames():
    inner = FakeMic([b"ab", b"cd"])
    mic = BufferingMic(inner)
    assert mic.read_frame() == b"ab"
    assert mic.read_frame() == b"cd"
    assert mic.pcm == b"abcd"
    assert mic.read_frame() is None


def test_buffering_mic_delegates_lifecycle():
    inner = FakeMic()
    mic = BufferingMic(inner)
    mic.start()
    assert inner.started == 1
    assert mic.available() is True
    mic.stop()
    assert inner.stopped == 1


def test_buffering_mic_respects_max_bytes():
    inner = FakeMic([b"ab", b"cd"])
    mic = BufferingMic(inner, max_bytes=3)
    mic.read_frame()
    mic.read_frame()
    assert mic.pcm == b"bcd"  # oldest byte dropped


def test_buffering_mic_reset_clears():
    inner = FakeMic([b"ab"])
    mic = BufferingMic(inner)
    mic.read_frame()
    assert mic.pcm == b"ab"
    mic.reset()
    assert mic.pcm == b""


# ---------------------------------------------------------------------------
# accurate module injection
# ---------------------------------------------------------------------------
def _fake_accurate(monkeypatch, *, available: bool = True, ok: bool = True,
                   text: str = "", reason: str = ""):
    try:
        mod = importlib.import_module("voicebridge.voice_edit.asr_accurate")
    except Exception:
        mod = types.ModuleType("voicebridge.voice_edit.asr_accurate")
        monkeypatch.setitem(
            sys.modules, "voicebridge.voice_edit.asr_accurate", mod)
    monkeypatch.setattr(ve, "asr_accurate", mod, raising=False)
    monkeypatch.setattr(mod, "is_available", lambda cfg: available,
                        raising=False)

    class _Result:
        pass

    class _Transcriber:
        def transcribe(self, pcm, sample_rate: int = 16000):
            res = _Result()
            res.ok = ok
            res.text = text
            res.reason = reason
            return res

    monkeypatch.setattr(mod, "get_transcriber", lambda cfg: _Transcriber(),
                        raising=False)
    return mod


def _patch_socket(monkeypatch, calls, value: str = "streamed"):
    def fake_once(client, mic, ev, **kwargs):
        calls.append((client, mic, ev))
        return value

    monkeypatch.setattr(sc, "transcribe_once", fake_once)
    monkeypatch.setattr(wake, "_mint_ticket", lambda cfg: "ticket")
    monkeypatch.setattr(wake, "_ws_url",
                        lambda cfg: "ws://test/agent/api/audio/stt/ws")


# ---------------------------------------------------------------------------
# transcribe_accurate
# ---------------------------------------------------------------------------
def test_transcribe_accurate_returns_text(monkeypatch):
    _fake_accurate(monkeypatch, ok=True, text="hello world")
    assert transcribe_accurate(FakeCfg(), b"\x00" * 9600) == ("hello world", "")


def test_transcribe_accurate_returns_reason_on_failure(monkeypatch):
    _fake_accurate(monkeypatch, ok=False, reason="no model")
    assert transcribe_accurate(FakeCfg(), b"\x00" * 9600) == ("", "no model")


def test_transcribe_accurate_empty_pcm(monkeypatch):
    _fake_accurate(monkeypatch, ok=True, text="ignored")
    assert transcribe_accurate(FakeCfg(), b"") == ("", "empty audio")


# ---------------------------------------------------------------------------
# WakeMonitor._do_transcribe
# ---------------------------------------------------------------------------
def _monitor(*, mode: str = "accurate"):
    # A sentinel kws avoids loading the real keyword model.
    return wake.WakeMonitor(FakeCfg(mode=mode), on_command=lambda c: None,
                            is_busy=lambda: False, kws=object())


def test_injected_transcribe_is_used_unchanged(monkeypatch):
    def _no_accurate(cfg):
        raise AssertionError("accurate must not run when transcribe injected")

    monkeypatch.setattr(wake, "_accurate_ready", _no_accurate)
    m = wake.WakeMonitor(FakeCfg(), on_command=lambda c: None,
                         is_busy=lambda: False, kws=object(),
                         transcribe=lambda pcm: "injected")
    assert m._do_transcribe(b"\x00" * 9600) == "injected"


def test_streaming_mode_uses_socket(monkeypatch):
    def _no_accurate(cfg):
        raise AssertionError("accurate must not run in streaming mode")

    monkeypatch.setattr(wake, "_accurate_ready", _no_accurate)
    calls: list = []
    _patch_socket(monkeypatch, calls, "streamed")
    m = _monitor(mode="streaming")
    assert m._do_transcribe(b"\x00" * 9600) == "streamed"
    assert len(calls) == 1


def test_accurate_success_returns_text_without_socket(monkeypatch):
    _fake_accurate(monkeypatch, available=True, ok=True, text="accurate text")
    calls: list = []
    _patch_socket(monkeypatch, calls, "streamed")
    m = _monitor(mode="accurate")
    assert m._do_transcribe(b"\x00" * 9600) == "accurate text"
    assert calls == []


def test_accurate_failure_falls_back_to_socket_and_traces(monkeypatch):
    events: list = []
    monkeypatch.setattr(wake, "_trace",
                        lambda stage, **f: events.append((stage, f)))
    _fake_accurate(monkeypatch, available=True, ok=False, reason="boom")
    calls: list = []
    _patch_socket(monkeypatch, calls, "streamed")
    m = _monitor(mode="accurate")
    assert m._do_transcribe(b"\x00" * 9600) == "streamed"
    assert len(calls) == 1
    assert any(s == "accurate_fallback" and f.get("reason") == "boom"
               for s, f in events)


def test_accurate_unavailable_falls_back_and_traces(monkeypatch):
    events: list = []
    monkeypatch.setattr(wake, "_trace",
                        lambda stage, **f: events.append((stage, f)))
    _fake_accurate(monkeypatch, available=False)
    calls: list = []
    _patch_socket(monkeypatch, calls, "streamed")
    m = _monitor(mode="accurate")
    assert m._do_transcribe(b"\x00" * 9600) == "streamed"
    assert len(calls) == 1
    assert any(s == "accurate_fallback" for s, _ in events)
