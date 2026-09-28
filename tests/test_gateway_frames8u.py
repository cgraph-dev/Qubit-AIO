"""Qubit gateway frame guards (transport-neutral dispatch).

Hermetic: pure unit checks plus a driven ``FrameReader`` against a fake
frame iterator. Proves oversize/malformed/misshapen frames are
dropped individually (audited + counted) while valid responses and
events still dispatch and the stream survives.
"""
from __future__ import annotations

import asyncio
import json


def _reader(events):
    from voicebridge.gateway.gateway_ws import FrameReader

    reader = FrameReader(
        event_cb=lambda ev, payload: events.append((ev, payload)))
    return reader


def test_check_raw_accepts_valid():
    from voicebridge.gateway.gateway_ws import FrameGuard

    guard = FrameGuard()
    msg = guard.check_raw('{"type": "event", "event": "x"}')
    assert msg["type"] == "event"
    assert guard.dropped == 0


def test_check_raw_rejects_oversize_invalid_and_nonobject():
    from voicebridge.gateway.gateway_ws import FrameGuard, GatewayError

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
    from voicebridge.gateway.gateway_ws import FrameGuard, GatewayError

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
    from voicebridge.gateway.gateway_ws import FrameGuard

    events = []
    reader = _reader(events)
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    reader.expect("call-1", fut)

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

    loop.run_until_complete(reader.drain(_FakeWS()))
    loop.close()
    assert fut.done() and fut.result() == {"hello": "world"}
    assert events == [("tick", {"n": 1})]
    assert reader.frame_guard.dropped == 2
    assert reader.frame_guard.ignored_unknown == 1
    assert reader._pending == {}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _fake_ws(frames):
    class _FakeWS:
        def __aiter__(self):
            async def _gen():
                for frame in frames:
                    yield frame
            return _gen()

    return _FakeWS()


def test_rpc_methods_dispatch_through_frame_reader(tmp_path, monkeypatch):
    """OC-D09: the new gateway server methods dispatch through the real,
    transport-neutral FrameReader (Qubit's safe gateway-server equivalent)."""
    from voicebridge.gateway.gateway_ws import FrameReader
    from voicebridge.gateway.rpc import default_registry, handle_request

    cfg = _cfg(tmp_path, monkeypatch)
    assert set(default_registry().names()) >= {
        "environments.prepare", "task.history", "task.suggestions",
        "update.report", "workspace.files", "plugin.mutations", "migrations"}

    responses = []
    reader = FrameReader(methods=default_registry(), cfg=cfg,
                         respond=responses.append)
    frames = [
        json.dumps({"type": "req", "id": "a",
                    "method": "environments.prepare", "params": {}}),
        json.dumps({"type": "req", "id": "b", "method": "task.history",
                    "params": {"limit": 5}}),
        json.dumps({"type": "req", "id": "c", "method": "task.suggestions",
                    "params": {}}),
        json.dumps({"type": "req", "id": "d", "method": "update.report",
                    "params": {}}),
        json.dumps({"type": "req", "id": "e", "method": "workspace.files",
                    "params": {"action": "inspect"}}),
        json.dumps({"type": "req", "id": "f", "method": "plugin.mutations",
                    "params": {"action": "status"}}),
        json.dumps({"type": "req", "id": "g", "method": "migrations",
                    "params": {}}),
        json.dumps({"type": "req", "id": "h", "method": "does.not.exist",
                    "params": {}}),
    ]
    loop = asyncio.new_event_loop()
    loop.run_until_complete(reader.drain(_fake_ws(frames)))
    loop.close()
    by_id = {r["id"]: r for r in responses}
    for rid in "abcdefg":
        assert by_id[rid]["ok"] is True, (rid, by_id.get(rid))
        assert by_id[rid]["type"] == "res"
    assert by_id["h"]["ok"] is False
    assert by_id["h"]["error"]["code"] == "GATEWAY_METHOD_NOT_FOUND"

    # Unknown gated plugin mutation is a typed error, never a fabricated OK.
    res = handle_request(default_registry(), cfg, {
        "type": "req", "id": "z", "method": "plugin.mutations",
        "params": {"action": "register", "manifest": {"id": "x",
                                                      "kind": "tool"}}})
    assert res["ok"] is False and res["error"]["code"]
