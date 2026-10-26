"""Streaming STT client tests (fake async connection; no socket, no mic)."""
from __future__ import annotations

import asyncio
import json
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.stt_client import SttClient, transcribe_once


class FakeWs:
    """Server-side scripted connection."""

    def __init__(self, *, events, on_binary=None):
        self._events = list(events)
        self.sent = []
        self.binaries = []
        self.closed = False
        self._on_binary = on_binary

    async def send(self, data):
        self.sent.append(data)
        if isinstance(data, (bytes, bytearray)) and len(data) >= 4:
            self.binaries.append(data)
            if self._on_binary is not None:
                self._on_binary(struct.unpack("!I", data[:4])[0])
        else:
            try:
                msg = json.loads(data)
            except Exception:
                return
            if msg.get("type") == "stop":
                # The server would finalize here; emit the queued events.
                pass

    async def recv(self):
        if self._events:
            ev = self._events.pop(0)
            return json.dumps(ev)
        # No more events: block forever (the client stops on completed anyway).
        await asyncio.sleep(30)
        return "{}"

    async def close(self):
        self.closed = True


class FakeMic:
    def __init__(self, frames):
        self._frames = list(frames)
        self.started = False

    def start(self):
        self.started = True

    def read_frame(self, timeout=0.2):
        return self._frames.pop(0) if self._frames else None

    def stop(self):
        pass


def _run(conn, mic, *, timeout=10.0, on_partial=None):
    client = SttClient(url="ws://test/agent/api/audio/stt/ws?ticket=t",
                       ticket="t", opener=lambda url: _awaitable(conn))
    ev = threading.Event()

    async def _go():
        return await client.run(mic, ev, on_partial=on_partial)
    loop = asyncio.new_event_loop()
    try:
        final = loop.run_until_complete(asyncio.wait_for(_go(), timeout=timeout))
    finally:
        loop.close()
    return client, final


async def _awaitable(value):
    return value


def test_sender_numbers_frames_and_finalizes_without_unbound_error():
    recv_seqs = []
    ready = asyncio.Event()

    class Conn(FakeWs):
        async def recv(self):
            await ready.wait()  # wait for the sender to deliver frames
            if not getattr(self, "_final", False):
                self._final = True
                return json.dumps({"type": "stt.final", "text": "hello world",
                                   "engine": "fake", "sequence": 0})
            if not getattr(self, "_done", False):
                self._done = True
                return json.dumps({"type": "stt.completed", "final_text":
                                   "hello world", "engine": "fake",
                                   "sequence": 0, "metrics": {"audio_ms": 300}})
            await asyncio.sleep(30)
            return "{}"

    def on_binary(seq):
        recv_seqs.append(seq)
        if len(recv_seqs) >= 3:
            ready.set()

    conn = Conn(events=[{"type": "stt.started", "engine": "fake"}],
                on_binary=on_binary)
    mic = FakeMic([b"\x01\x02" * 480] * 3)  # 3 frames then exhausted
    client, final = _run(conn, mic)
    assert client.error is None, client.error
    assert recv_seqs == [0, 1, 2]  # sequential, no UnboundLocalError
    assert final == "hello world"
    assert client.engine_reported == "fake"


def test_ready_callback_fires_on_stt_started():
    conn = FakeWs(events=[
        {"type": "stt.started", "engine": "fake"},
        {"type": "stt.completed", "final_text": "", "engine": "fake",
         "sequence": 0, "metrics": {}},
    ])
    mic = FakeMic([b"\x00\x00" * 480])
    ready = []
    client = SttClient(url="ws://test/ws", ticket="t",
                       opener=lambda url: _awaitable(conn))

    async def _go():
        return await client.run(mic, threading.Event(),
                                on_ready=lambda: ready.append(True))

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_go(), timeout=10))
    finally:
        loop.close()
    assert ready == [True]


def test_partial_then_final_and_completed():
    conn = FakeWs(events=[
        {"type": "stt.started", "engine": "fake"},
        {"type": "stt.partial", "text": "make it", "engine": "fake"},
        {"type": "stt.partial", "text": "make it clear", "engine": "fake"},
        {"type": "stt.final", "text": "make it clearer", "engine": "fake"},
        {"type": "stt.completed", "final_text": "make it clearer",
         "engine": "fake", "sequence": 0, "metrics": {"audio_ms": 300}},
    ])
    mic = FakeMic([b"\x00\x00" * 480])
    client, final = _run(conn, mic)
    assert client.error is None
    assert client.partials == ["make it", "make it clear"]
    assert final == "make it clearer"


def test_partial_segments_are_replaced_and_joined_for_live_display():
    conn = FakeWs(events=[
        {"type": "stt.started", "engine": "fake"},
        {"type": "stt.partial", "text": "hello", "segment": 0, "replace": True},
        {"type": "stt.partial", "text": "hello world", "segment": 0, "replace": True},
        {"type": "stt.partial", "text": "again", "segment": 1, "replace": True},
        {"type": "stt.final", "text": "Hello world again.", "segment": 1},
        {"type": "stt.completed", "final_text": "Hello world again.",
         "sequence": 0, "metrics": {}},
    ])
    mic = FakeMic([b"\x00\x00" * 480])
    seen = []
    client, final = _run(conn, mic, on_partial=seen.append)

    assert seen == ["hello", "hello world", "hello world again"]
    assert client.partials == ["hello", "hello world", "again"]
    assert client.interim_text == "Hello world again."
    assert final == "Hello world again."


def test_server_error_is_typed():
    conn = FakeWs(events=[{"type": "stt.error", "code": "STT_UNAVAILABLE",
                           "detail": "no pack"}])
    mic = FakeMic([b"\x00\x00" * 480])
    client, final = _run(conn, mic, timeout=10)
    assert client.error is not None
    assert client.error.code == "STT_UNAVAILABLE"


def test_parec_mic_applies_gain_to_quiet_audio():
    from voicebridge.voice_edit.stt_client import ParecMic
    import numpy as np
    # quiet speech at ~0.004 peak should be lifted toward the target
    quiet = (np.ones(4800, dtype=np.float32) * (0.05 * 32768)).astype("<i2").tobytes()
    mic = ParecMic(gain=12.0, target_peak=0.5)
    out = np.frombuffer(mic._apply_gain(quiet), dtype="<i2").astype(np.float32)
    assert out.max() > 0.3 * 32768  # clearly boosted
    # already-loud audio is left alone (never attenuated, never clipped up)
    loud = (np.ones(4800, dtype=np.float32) * (0.6 * 32768)).astype("<i2").tobytes()
    assert mic._apply_gain(loud) == loud


def test_normalize_pcm_boosts_quiet_utterance():
    from voicebridge.voice_edit.wake import _normalize_pcm
    import numpy as np
    quiet = (np.ones(16000, dtype=np.float32) * (0.05 * 32768)).astype("<i2").tobytes()
    out = np.frombuffer(_normalize_pcm(quiet), dtype="<i2").astype(np.float32)
    assert out.max() / 32768.0 > 0.3
    # near-silence is left as-is (never amplified into noise)
    silence = b"\x00\x00" * 8000
    assert _normalize_pcm(silence) == silence


def test_ticket_is_appended_to_the_ws_url():
    c = SttClient(url="ws://h/agent/api/audio/stt/ws", ticket="abc/def==")
    assert c.url == "ws://h/agent/api/audio/stt/ws?ticket=abc%2Fdef%3D%3D"
    # Already-present ticket is not duplicated.
    c2 = SttClient(url="ws://h/ws?ticket=keep", ticket="other")
    assert c2.url == "ws://h/ws?ticket=keep"


def test_connect_failure_is_typed():
    async def boom(url):
        raise OSError("refused")

    client = SttClient(url="ws://x/ws", ticket="t", opener=boom)

    class Mic(FakeMic):
        def read_frame(self, timeout=0.2):
            return None

    ev = threading.Event()
    loop = asyncio.new_event_loop()
    try:
        out = loop.run_until_complete(client.run(Mic([]), ev))
    finally:
        loop.close()
    assert out == ""
    assert client.error is not None and client.error.code == "STT_CONNECT_FAILED"


def test_parec_frame_bytes_follow_probed_format(monkeypatch):
    """Reads must match the real stream format, not the assumed default.

    Regression: ``_frame_bytes`` was computed once from 16k/mono/s16 while
    the Scarlett streams s32/4ch/48k, so every read pulled ~12.5 ms while
    AGC/endpointer/KWS assumed 300 ms frames (mangled levels, fragmented
    utterances, deaf spotter on real mic audio).
    """
    import subprocess
    from voicebridge.voice_edit.stt_client import ParecMic

    out = ("Source #57\n"
           "\tName: alsa_input.usb-Focusrite_Foo-00.analog-surround-40\n"
           "\tSample Specification: s32le 4ch 48000Hz\n")

    class _R:
        def __init__(self, stdout):
            self.stdout = stdout

    def _fake_run(argv, **kwargs):
        if "get-default-source" in argv:
            return _R("alsa_input.usb-Focusrite_Foo-00.analog-surround-40\n")
        return _R(out)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)

    mic = ParecMic(device="", frame_ms=300)
    mic._probe_source_format()
    assert (mic._native_rate, mic._native_channels) == (48000, 4)
    mic._sync_frame_bytes()
    assert mic._frame_bytes == 230400, mic._frame_bytes


def test_parec_frame_bytes_default_without_pactl(monkeypatch):
    from voicebridge.voice_edit.stt_client import ParecMic
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    mic = ParecMic(device="", frame_ms=300)
    mic._probe_source_format()
    assert mic._frame_bytes == 9600
