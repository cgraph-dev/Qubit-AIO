"""Packet 8U — gateway frame guards (Qubit-native O-frames).

Hermetic: pure unit checks plus a driven ``_reader`` against a fake
websocket iterator. Proves oversize/malformed/misshapen frames are
dropped individually (audited + counted) while valid responses and
events still dispatch and the connection survives.
"""
from __future__ import annotations

import asyncio
import json


def _client():
    from voicebridge.openclaw.gateway_ws import GatewayClient

    return GatewayClient("ws://127.0.0.1:9/", "test-token")


def test_check_raw_accepts_valid():
    from voicebridge.openclaw.gateway_ws import FrameGuard

    guard = FrameGuard()
    msg = guard.check_raw('{"type": "event", "event": "x"}')
    assert msg["type"] == "event"
    assert guard.dropped == 0


def test_check_raw_rejects_oversize_invalid_and_nonobject():
    from voicebridge.openclaw.gateway_ws import FrameGuard, GatewayError

    guard = FrameGuard(max_bytes=16)
    for raw, code in (("x" * 17, "FRAME_TOO_LARGE"),
                      ("{nope", "FRAME_NOT_JSON"),
                      ("[1,2]", "FRAME_BAD_SHAPE")):
        try:
            guard.check_raw(raw)
        except GatewayError as exc:
            assert exc.code == code, (raw, exc.code)
        else:
            raise AssertionError(f"accepted: {raw!r}")


def test_check_message_kinds():
    from voicebridge.openclaw.gateway_ws import FrameGuard, GatewayError

    guard = FrameGuard()
    assert guard.check_message({"type": "res", "id": "a"}) == "res"
    assert guard.check_message({"type": "event", "event": "e"}) == "event"
    assert guard.check_message({"type": "future-kind"}) == "ignore"
    assert guard.ignored_unknown == 1
    for bad in ({"no": "type"}, {"type": "res"}, {"type": "event"}):
        try:
            guard.check_message(bad)
        except GatewayError as exc:
            assert exc.code == "FRAME_BAD_SHAPE", bad
        else:
            raise AssertionError(f"accepted: {bad!r}")


def test_reader_drops_bad_frames_and_keeps_connection():
    from voicebridge.openclaw.gateway_ws import FrameGuard

    client = _client()
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    client._pending["call-1"] = fut
    events = []
    client.event_cb = lambda ev, payload: events.append((ev, payload))
    client.connected = True

    big = "x" * (FrameGuard().max_bytes + 1)
    frames = [
        json.dumps({"type": "res", "id": "call-1", "ok": True,
                    "payload": {"hello": "world"}}),
        "{broken json",
        big,
        json.dumps({"type": "mystery", "id": "z"}),
        json.dumps({"type": "event", "event": "tick",
                    "payload": {"n": 1}}),
    ]

    class _FakeWS:
        def __aiter__(self):
            async def _gen():
                for frame in frames:
                    yield frame
            return _gen()

    client.ws = _FakeWS()
    loop.run_until_complete(client._reader())
    loop.close()
    assert fut.done() and fut.result() == {"hello": "world"}
    assert events == [("tick", {"n": 1})]
    assert client.frame_guard.dropped == 2
    assert client.frame_guard.ignored_unknown == 1
    assert client.connected is True
    assert client._pending == {}
