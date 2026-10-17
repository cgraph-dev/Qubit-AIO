"""STT wire-contract net (9u): client-exact bytes drive the real socket.

Live 2026-10-1x: ``POST /api/auth/ws-ticket`` (scope ``stt``) minted 200 but
the ``/agent/api/audio/stt/ws`` socket yielded no frames. Both ends are
pinned separately (adapter unit tests; server tests with hand-written
frames), but nothing pinned the two halves TOGETHER — a drift between the
bytes ``LocalStreamingSTTAdapter`` emits and what ``SttWsServer`` accepts
would be invisible to both suites and produce exactly that symptom.

This test sends byte-for-byte what the adapter sends (start JSON with
``sequence: 0``, ``[uint32 BE seq][pcm_s16le]`` binary frames with seq from
1, stop JSON) through a real ticket + real RFC 6455 socket against the real
``SttWsServer`` (deterministic window-session double injected, as in the
server suite) and asserts the full ``stt.started → partial → final →
completed`` exchange with matching ``request_id``.

It also pins the silent-completion explanation: stopping with no audio used
to complete with an empty ``final_text`` and no reason (indistinguishable
from "the recognizer heard nothing" vs "the client sent nothing"). The
server now carries ``warning_code``/``warning`` (``STT_NO_TRANSCRIPT`` plus
the rescore reason, e.g. ``no buffered audio``) so the UI can say which.

Hermetic: loopback HTTP+WS server on an ephemeral port, no model, no mic.
"""
from __future__ import annotations

import json
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ws_sync = pytest.importorskip("websockets.sync.client")
from websockets.sync.client import connect as ws_connect  # noqa: E402

from voicebridge.asr.streaming import WindowStreamingSession  # noqa: E402


def _fake_factory(engine=None):
    """Deterministic window session: text grows with buffered audio."""
    def decode(audio: np.ndarray) -> str:
        words = int(len(audio) / 800)  # one word per ~50 ms
        return " ".join(["word"] * words)
    return WindowStreamingSession(decode, engine="fake-window",
                                  partial_min_interval_s=0.0,
                                  silence_rms=0.0)


def _cfg(tmp_path):
    from voicebridge import config as C

    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _start(tmp_path):
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg = _cfg(tmp_path)

    class _FakeOrch:
        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.pty_ws._estop = lambda _cfg: False
    ui.stt_ws._estop = lambda _cfg: False
    ui.stt_ws._factory = _fake_factory
    ui.start()
    return ui, ui._server.server_address[1]


def _mint(port):
    data = json.dumps({"scope": "stt"}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/agent/api/auth/ws-ticket", data=data,
        method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Qubit-Session-Token", "test-token")
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, json.loads(r.read())


def _recv(ws, timeout=8):
    raw = ws.recv(timeout=timeout)
    if isinstance(raw, str):
        return json.loads(raw)
    return json.loads(bytes(raw).decode("utf-8"))


def _pcm(seconds: float, amp: float = 0.3) -> bytes:
    n = int(16000 * seconds)
    x = (np.sin(np.arange(n) * 0.05) * amp * 32767).astype("<i2")
    return x.tobytes()


def _drain_until(ws, event, timeout=10):
    end = time.time() + timeout
    seen = []
    while time.time() < end:
        msg = _recv(ws, timeout=max(0.1, end - time.time()))
        seen.append(msg)
        if msg.get("type") == event:
            return msg, seen
    raise AssertionError(
        f"{event} not received; saw {[m.get('type') for m in seen]}")


def test_adapter_exact_bytes_drive_full_exchange(tmp_path):
    """Client-exact start/audio/stop yields started/partial/final/completed."""
    _, port = _start(tmp_path)
    status, out = _mint(port)
    assert status == 200, out
    url = (f"ws://127.0.0.1:{port}/agent/api/audio/stt/ws?"
           f"{urlencode({'ticket': out['ticket']})}")
    request_id = "r-contract-1"
    with ws_connect(url, open_timeout=8) as ws:
        # EXACT adapter start shape (see local-streaming-stt-adapter.ts).
        ws.send(json.dumps({
            "type": "start",
            "session_id": "qubit-chat",
            "request_id": request_id,
            "format": "pcm_s16le",
            "sample_rate": 16000,
            "sequence": 0,
        }))
        started, _ = _drain_until(ws, "stt.started")
        assert started["request_id"] == request_id, started
        # EXACT adapter binary shape: [uint32 BE seq][mono s16le], seq from 1.
        seq = 1
        for _ in range(4):
            ws.send(struct.pack("!I", seq) + _pcm(0.3))
            seq += 1
        partial, _ = _drain_until(ws, "stt.partial")
        assert partial["request_id"] == request_id, partial
        assert partial["text"].strip(), partial
        ws.send(json.dumps({"type": "stop", "sequence": seq}))
        final, _ = _drain_until(ws, "stt.final")
        assert final["request_id"] == request_id, final
        assert final["text"].strip(), final
        completed, _ = _drain_until(ws, "stt.completed")
        assert completed["request_id"] == request_id, completed
        assert completed["cancelled"] is False, completed
        assert completed["final_text"].strip(), completed
        assert float(completed["metrics"]["audio_ms"]) > 0, completed
        # A transcripted turn carries no silence warning.
        assert "warning_code" not in completed, completed
        assert "warning" not in completed, completed


def test_silent_stop_explains_itself(tmp_path):
    """Stopping with no audio completes with a typed no-transcript reason."""
    _, port = _start(tmp_path)
    status, out = _mint(port)
    assert status == 200, out
    url = (f"ws://127.0.0.1:{port}/agent/api/audio/stt/ws?"
           f"{urlencode({'ticket': out['ticket']})}")
    request_id = "r-contract-silent"
    with ws_connect(url, open_timeout=8) as ws:
        ws.send(json.dumps({
            "type": "start",
            "session_id": "qubit-chat",
            "request_id": request_id,
            "format": "pcm_s16le",
            "sample_rate": 16000,
            "sequence": 0,
        }))
        started, _ = _drain_until(ws, "stt.started")
        assert started["request_id"] == request_id, started
        ws.send(json.dumps({"type": "stop", "sequence": 1}))
        completed, seen = _drain_until(ws, "stt.completed")
        assert completed["request_id"] == request_id, completed
        assert completed["final_text"] == "", completed
        # No stt.final precedes an empty completion.
        assert [m["type"] for m in seen if m["type"] == "stt.final"] == []
        assert completed.get("warning_code") == "STT_NO_TRANSCRIPT", completed
        assert str(completed.get("warning") or "").strip(), completed
