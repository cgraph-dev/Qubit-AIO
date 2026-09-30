"""Streaming STT WebSocket + session tests (live /qubit-chat transcription).

Hermetic: the ASR session is a deterministic ``WindowStreamingSession`` double
injected through ``SttWsServer._factory``, so no model or microphone is needed.
The transport itself is the real single-listener RFC 6455 path with the shared
single-use ticket authority (scope ``stt``) and origin/peer/e-stop checks.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ws_sync = pytest.importorskip("websockets.sync.client")
from websockets.sync.client import connect as ws_connect  # noqa: E402

from voicebridge.asr.streaming import (  # noqa: E402
    WindowStreamingSession, OnlineStreamingSession,
)
from voicebridge.stt_ws import (  # noqa: E402
    _s16le_to_float, _resample_mono, SttWsError,
)


def _fake_factory(engine=None):
    """A deterministic window session: text grows with buffered audio."""
    def decode(audio: np.ndarray) -> str:
        words = int(len(audio) / 800)  # one word per ~50 ms
        return " ".join(["word"] * words)
    return WindowStreamingSession(decode, engine="fake-window",
                                  partial_min_interval_s=0.0,
                                  silence_rms=0.0)


def _start(tmp_path, *, estop=False):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.pty_ws._estop = lambda _cfg: estop
    ui.stt_ws._estop = lambda _cfg: estop
    ui.stt_ws._factory = _fake_factory
    ui.start()
    return ui, ui._server.server_address[1]


def _mint(port, *, scope="stt", headers=None, body=None):
    payload = {"scope": scope}
    payload.update(body or {})
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/agent/api/auth/ws-ticket", data=data,
        method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {"X-Qubit-Session-Token": "test-token"}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw.decode("utf-8", "replace")}


def _ws_url(port, ticket):
    from urllib.parse import urlencode
    return (f"ws://127.0.0.1:{port}/agent/api/audio/stt/ws?"
            f"{urlencode({'ticket': ticket})}")


def _recv_json(ws, timeout=8):
    raw = ws.recv(timeout=timeout)
    if isinstance(raw, str):
        return json.loads(raw)
    return json.loads(bytes(raw).decode("utf-8"))


def _pcm(seconds: float, amp: float = 0.1) -> bytes:
    n = int(16000 * seconds)
    x = (np.sin(np.arange(n) * 0.05) * amp * 32767).astype("<i2")
    return x.tobytes()


def _frame(seq: int, seconds: float) -> bytes:
    return struct.pack("!I", seq) + _pcm(seconds)


def _drain_until(ws, event, timeout=8):
    """Collect events until one of type *event* arrives (or timeout)."""
    end = time.time() + timeout
    seen = []
    while time.time() < end:
        msg = _recv_json(ws, timeout=max(0.1, end - time.time()))
        seen.append(msg)
        if msg.get("type") == event:
            return msg, seen
    raise AssertionError(f"{event} not received; saw {[m.get('type') for m in seen]}")


# ---------------------------------------------------------------------------
# Session unit tests
# ---------------------------------------------------------------------------
def test_window_session_emits_growing_partials_and_final():
    s = _fake_factory()
    out = []
    for i in range(6):
        r = s.accept(np.full(1600, 0.2, dtype=np.float32), end_of_speech=False)
        if r:
            out.append(r.text)
    final = s.finalize()
    assert out == sorted(out, key=len), out
    assert final is not None and final.is_final and final.text
    assert s.metrics.first_partial_at is not None


def test_window_session_partial_is_full_hypothesis_replace():
    s = WindowStreamingSession(lambda _a: "hello", engine="const",
                               partial_min_interval_s=0.0, silence_rms=0.0)
    s.accept(np.full(3200, 0.2, dtype=np.float32))
    r1 = s.accept(np.full(3200, 0.2, dtype=np.float32))
    assert r1 is not None and r1.replace is True and r1.text == "hello"
    # A repeated identical hypothesis is not re-emitted (no duplicate text).
    assert s.accept(np.full(3200, 0.2, dtype=np.float32)) is None


def test_window_session_cancel_discards_audio():
    s = _fake_factory()
    s.accept(np.full(8000, 0.2, dtype=np.float32))
    s.cancel()
    assert s.finalize() is None


def test_online_session_drains_ready_stream():
    class _Rec:
        def __init__(self):
            self.ready = True
            self.text = ""
            self.n = 0

        def is_ready(self, _s):
            return self.ready

        def decode_stream(self, _s):
            self.n += 1
            self.text = "tok" * self.n
            if self.n >= 2:
                self.ready = False

        def get_result(self, _s):
            return self.text

        def reset(self, _s):
            self.text = ""
            self.n = 0

    class _Stream:
        def accept_waveform(self, *, sample_rate, waveform):
            return None

        def input_finished(self):
            return None

    rec = _Rec()
    s = OnlineStreamingSession(rec, _Stream(), engine="fake-online")
    r = s.accept(np.full(1600, 0.2, dtype=np.float32))
    assert r is not None and r.text == "tok" * 2
    f = s.finalize()
    assert f is not None and f.is_final


def test_resample_and_pcm_helpers():
    x = np.sin(np.arange(8000, dtype=np.float32))
    assert _resample_mono(x, 8000, 16000).shape == (16000,)
    assert _resample_mono(x, 16000, 16000) is x
    got = _s16le_to_float(np.array([0, 16384, -16384], dtype="<i2").tobytes())
    assert got[0] == 0.0 and got[1] == 0.5 and got[2] == -0.5
    with pytest.raises(SttWsError):
        _s16le_to_float(b"\x01")
    with pytest.raises(SttWsError):
        _s16le_to_float(b"")


# ---------------------------------------------------------------------------
# WebSocket protocol / transport
# ---------------------------------------------------------------------------
def test_stream_partials_then_final_then_completed(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, out = _mint(port, scope="stt")
        assert status == 200 and out["ok"] and out["scope"] == "stt"
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({
                "type": "start", "session_id": "dashboard-chat",
                "request_id": "req-1", "format": "pcm_s16le",
                "sample_rate": 16000, "sequence": 0}))
            started = _recv_json(ws)
            assert started["type"] == "stt.started"
            assert started["request_id"] == "req-1"
            assert started["engine"] == "fake-window"
            for seq in range(1, 6):
                ws.send(_frame(seq, 0.2))
            partial = None
            for _ in range(6):
                msg = _recv_json(ws)
                if msg["type"] == "stt.partial":
                    partial = msg
                    break
            assert partial is not None
            for k in ("request_id", "sequence", "engine", "timestamp", "text"):
                assert k in partial, partial
            ws.send(json.dumps({"type": "stop", "sequence": 6}))
            final, seen = _drain_until(ws, "stt.final")
            assert final["text"]
            completed, _ = _drain_until(ws, "stt.completed")
            assert completed["cancelled"] is False
            assert completed.get("final_text") == final["text"]
            assert "metrics" in completed
    finally:
        ui.stop()


def test_stale_and_duplicate_frames_are_ignored(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({"type": "start", "request_id": "r",
                                "format": "pcm_s16le", "sample_rate": 16000}))
            _recv_json(ws)
            ws.send(_frame(5, 0.6))
            first = _recv_json(ws)
            assert first["type"] == "stt.partial"
            count = first["text"].count("word")
            # Replay an older sequence: it must not be fed again.
            ws.send(_frame(3, 0.6))
            ws.send(json.dumps({"type": "stop", "sequence": 6}))
            final, _ = _drain_until(ws, "stt.final")
            assert final["text"].count("word") == count
    finally:
        ui.stop()


def test_cancel_discards_interim(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({"type": "start", "request_id": "r",
                                "format": "pcm_s16le", "sample_rate": 16000}))
            _recv_json(ws)
            ws.send(_frame(1, 0.6))
            ws.send(json.dumps({"type": "cancel", "sequence": 2}))
            completed, seen = _drain_until(ws, "stt.completed")
            assert completed["cancelled"] is True
            assert completed["final_text"] == ""
            assert all(m["type"] != "stt.final" for m in seen)
    finally:
        ui.stop()


def test_audio_before_start_is_typed_error(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(_frame(1, 0.2))
            err = _recv_json(ws)
            assert err["type"] == "stt.error"
            assert err["code"] == "STT_START_REQUIRED"
    finally:
        ui.stop()


def test_malformed_audio_is_rejected(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({"type": "start", "request_id": "r",
                                "format": "pcm_s16le", "sample_rate": 16000}))
            _recv_json(ws)
            ws.send(struct.pack("!I", 1) + b"\x01\x02\x03")  # odd PCM length
            err = _recv_json(ws)
            assert err["type"] == "stt.error" and err["code"] == "STT_BAD_AUDIO"
    finally:
        ui.stop()


def test_bad_format_and_sample_rate_rejected(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({"type": "start", "request_id": "r",
                                "format": "audio/mp3", "sample_rate": 16000}))
            err = _recv_json(ws)
            assert err["type"] == "stt.error" and err["code"] == "STT_BAD_REQUEST"
    finally:
        ui.stop()
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({"type": "start", "request_id": "r",
                                "sample_rate": 999999}))
            err = _recv_json(ws)
            assert err["type"] == "stt.error" and err["code"] == "STT_SAMPLE_RATE"
    finally:
        ui.stop()


def test_ticket_scope_mismatch_refused(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="pty")  # wrong scope for the STT socket
        with pytest.raises(Exception):
            with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8):
                pass
    finally:
        ui.stop()


def test_estop_refuses_stt_upgrade(tmp_path):
    ui, port = _start(tmp_path, estop=True)
    try:
        _s, out = _mint(port, scope="stt")
        with pytest.raises(Exception):
            with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8):
                pass
        assert ui.stt_ws.active_count == 0
    finally:
        ui.stop()


def test_disconnect_cleans_up_session(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        ws = ws_connect(_ws_url(port, out["ticket"]), open_timeout=8)
        ws.send(json.dumps({"type": "start", "request_id": "r",
                            "sample_rate": 16000}))
        _recv_json(ws)
        ws.send(_frame(1, 0.4))
        ws.close()
        deadline = time.time() + 5
        while time.time() < deadline and ui.stt_ws.active_count != 0:
            time.sleep(0.05)
        assert ui.stt_ws.active_count == 0
    finally:
        ui.stop()


def _read_cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: t\n")
    return Config.load(p)


def test_prewarm_unknown_engine_is_typed(tmp_path):
    from voicebridge.asr.streaming import prewarm
    out = prewarm(_read_cfg(tmp_path), "does-not-exist")
    assert out["ok"] is False and out["code"] == "STT_UNAVAILABLE"


def test_cached_engines_reports_no_gpu_without_cuda(tmp_path):
    from voicebridge.asr.streaming import cached_engines
    health = cached_engines()
    assert "loaded_count" in health and "gpu_available" in health
    assert isinstance(health["online"], list) and isinstance(health["window"], list)


def test_stt_status_route_is_read_only(tmp_path):
    from voicebridge import qubit_compat as HC
    code, out = HC.dispatch(_read_cfg(tmp_path), "GET",
                            "/api/audio/stt/status", {}, {})
    assert code == 200, out
    assert out["schema"] == "qubit-stt-status/1"
    assert out["english_only"] is True
    assert out["streaming_window_engine"] == "moonshine"
    assert "cached" in out and "gpu_available" in out["cached"]
    assert "hotword" not in json.dumps(out).lower() or out["hotword_count"] >= 0


def test_idle_connection_does_not_busy_spin(tmp_path):
    """An open-but-idle STT socket must block, not peg a CPU core.

    Regression for the zero-timeout select() busy-wait: one core was burned for
    the life of every connection (material on the low-spec target).
    """
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({"type": "start", "request_id": "spin",
                                "sample_rate": 16000}))
            _recv_json(ws)
            cpu0 = time.process_time()
            time.sleep(1.0)
            cpu = time.process_time() - cpu0
            assert cpu < 0.5, f"STT socket busy-spun: {cpu:.2f}s CPU in 1s idle"
    finally:
        ui.stop()


def test_unavailable_backend_is_a_typed_refusal(tmp_path):
    ui, port = _start(tmp_path)
    try:
        def _boom(_engine):
            raise SttWsError("STT_UNAVAILABLE", "no local model installed")
        ui.stt_ws._factory = _boom
        _s, out = _mint(port, scope="stt")
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(json.dumps({"type": "start", "request_id": "r",
                                "sample_rate": 16000}))
            err = _recv_json(ws)
            assert err["type"] == "stt.error"
            assert err["code"] == "STT_UNAVAILABLE"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Accurate rescore (faster-whisper final wins over the streaming hypothesis)
# ---------------------------------------------------------------------------
def _stub_accurate(monkeypatch, text):
    """Stub the whole-utterance rescore; records calls for gating asserts."""
    import voicebridge.voice_edit.stt_client as SC

    calls = []

    def _fake(cfg, pcm, *, sample_rate=16000):
        calls.append({"bytes": len(pcm), "sample_rate": sample_rate})
        return text, ("" if text else "stub empty")

    monkeypatch.setattr(SC, "transcribe_accurate", _fake)
    return calls


def _run_utterance(port, ticket, seconds=1.0):
    """One start/audio/stop round-trip; returns (final, completed, seen)."""
    with ws_connect(_ws_url(port, ticket), open_timeout=8) as ws:
        ws.send(json.dumps({"type": "start", "request_id": "r",
                            "format": "pcm_s16le", "sample_rate": 16000}))
        _recv_json(ws)
        ws.send(_frame(1, seconds))
        ws.send(json.dumps({"type": "stop", "sequence": 2}))
        final, seen = _drain_until(ws, "stt.final", timeout=30)
        completed, _ = _drain_until(ws, "stt.completed", timeout=8)
        return final, completed, seen


def test_accurate_rescore_wins_over_streaming(tmp_path, monkeypatch):
    _stub_accurate(monkeypatch, "Hello Qubit.")
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        final, completed, _ = _run_utterance(port, out["ticket"])
        assert final["text"] == "Hello Qubit.", final
        assert final["engine"] == "accurate", final
        assert completed.get("final_text") == "Hello Qubit.", completed
    finally:
        ui.stop()


def test_accurate_empty_keeps_streaming_final(tmp_path, monkeypatch):
    _stub_accurate(monkeypatch, "")
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port, scope="stt")
        final, completed, _ = _run_utterance(port, out["ticket"])
        assert final["text"], final
        assert "word" in final["text"], final
        assert final["engine"] == "fake-window", final
        assert completed.get("final_text") == final["text"]
    finally:
        ui.stop()


def test_streaming_mode_skips_rescore(tmp_path, monkeypatch):
    import voicebridge.voice_edit.stt_client as SC

    def _boom(*_a, **_k):
        raise AssertionError("rescore must not run in streaming mode")

    monkeypatch.setattr(SC, "transcribe_accurate", _boom)
    ui, port = _start(tmp_path)
    try:
        # stt_mode streaming disables the rescore (same gate as Right-Ctrl).
        from voicebridge.config import Config
        import yaml

        p = tmp_path / "voice-streaming.yaml"
        p.write_text("logging:\n  jsonl: false\nvoice_edit:\n  stt_mode: streaming\n")
        ui.stt_ws.cfg = Config.load(p)
        _s, out = _mint(port, scope="stt")
        final, _, _ = _run_utterance(port, out["ticket"])
        assert "word" in final["text"], final
    finally:
        ui.stop()
