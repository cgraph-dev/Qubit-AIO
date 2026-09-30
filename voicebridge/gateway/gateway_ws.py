"""Qubit gateway-protocol frame machinery (transport-neutral).

Frame validation (FrameGuard) plus request/response/event dispatch
(FrameReader) for JSON message streams: every inbound frame is
size-checked, JSON-validated, and shape-checked before dispatch.
Oversize, malformed, or misshapen frames are dropped individually with
an audit event while the stream survives (fail-frame, never
fail-connection). There is no external server transport here: this
module never opens sockets or spawns processes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional


from ..logging_setup import log_event

log = logging.getLogger("voicebridge.gateway")

SCOPES = ["operator.read", "operator.write", "operator.admin",
          "operator.approvals", "operator.talk"]


class GatewayError(Exception):
    def __init__(self, code: str, message: str = "",
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(code, message, details)
        self.code = code
        self.message = message
        self.details = details


FRAME_TYPES = ("res", "event", "req")
MAX_FRAME_BYTES = 8 * 1024 * 1024


class FrameGuard:
    """Typed gateway-protocol frame guards (Qubit-native O-frames).

    Every inbound websocket frame is size-checked, JSON-validated, and
    shape-checked before dispatch: oversize, malformed, or misshapen
    frames are dropped individually with an audit event while the
    connection survives (fail-frame, never fail-connection). Unknown
    frame ``type`` values are ignored for forward compatibility and
    counted. All decisions are fail-closed and counted for health.
    """

    def __init__(self, max_bytes: int = MAX_FRAME_BYTES) -> None:
        self.max_bytes = int(max_bytes)
        self.dropped = 0
        self.ignored_unknown = 0

    def check_raw(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, (bytes, bytearray)):
            size = len(raw)
            try:
                text = bytes(raw).decode("utf-8", "replace")
            except Exception as exc:
                raise GatewayError("FRAME_NOT_JSON",
                                   f"gateway frame undecodable ({exc})")
        else:
            text = str(raw)
            size = len(text.encode("utf-8", "replace"))
        if size > self.max_bytes:
            raise GatewayError("FRAME_TOO_LARGE",
                               f"gateway frame {size} bytes "
                               f"(limit {self.max_bytes})")
        try:
            msg = json.loads(text)
        except ValueError as exc:
            raise GatewayError("FRAME_NOT_JSON",
                               f"gateway frame is not JSON ({exc})")
        if not isinstance(msg, dict):
            raise GatewayError("FRAME_BAD_SHAPE",
                               "gateway frame is not an object")
        return msg

    def check_message(self, msg: Dict[str, Any]) -> str:
        kind = msg.get("type")
        if not isinstance(kind, str) or not kind:
            raise GatewayError("FRAME_BAD_SHAPE",
                               "gateway frame has no type")
        if kind not in FRAME_TYPES:
            self.ignored_unknown += 1
            return "ignore"
        if kind == "res" and not msg.get("id"):
            raise GatewayError("FRAME_BAD_SHAPE",
                               "gateway response has no id")
        if kind == "req" and (not msg.get("id")
                              or not isinstance(msg.get("method"), str)):
            raise GatewayError("FRAME_BAD_SHAPE",
                               "gateway request needs id + method")
        if kind == "event" and not isinstance(msg.get("event"), str):
            raise GatewayError("FRAME_BAD_SHAPE",
                               "gateway event has no name")
        return kind

    def note_dropped(self, code: str) -> None:
        self.dropped += 1
        try:
            log_event("gateway.frame.drop", code=code)
        except Exception:
            pass


class FrameReader:
    """Transport-neutral frame dispatch: responses resolve pending calls,
    events fan out to a callback, bad frames drop counted. Feed it any
    async iterator of raw frames; it never touches a socket itself."""

    def __init__(self, guard: Optional[FrameGuard] = None,
                 event_cb: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                 *, methods: Any = None,
                 respond: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 cfg: Any = None) -> None:
        self.frame_guard = guard or FrameGuard()
        self.event_cb = event_cb
        self.methods = methods
        self.respond = respond
        self.cfg = cfg
        self._pending: Dict[str, asyncio.Future] = {}

    def expect(self, call_id: str, fut: asyncio.Future) -> None:
        """Register a pending response future (test/transport use)."""
        self._pending[call_id] = fut

    def dispatch_request(self, cfg: Any, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Handle one inbound ``req`` frame through the shared RPC registry."""
        from .rpc import handle_request

        return handle_request(self.methods, cfg, msg)

    async def drain(self, frames) -> None:
        """Consume raw frames until the iterator ends. Survives bad data."""
        async for raw in frames:
            try:
                msg = self.frame_guard.check_raw(raw)
                kind = self.frame_guard.check_message(msg)
            except GatewayError as exc:
                self.frame_guard.note_dropped(exc.code)
                continue
            if kind == "ignore":
                continue
            if kind == "req":
                if self.methods is not None and self.respond is not None:
                    try:
                        res = self.dispatch_request(self.cfg, msg)
                    except Exception:
                        res = {"type": "res", "id": msg.get("id"),
                               "ok": False,
                               "error": {"code": "RPC_ERROR",
                                         "message": "method dispatch failed"}}
                    try:
                        self.respond(res)
                    except Exception:
                        log.exception("gateway response sink failed")
                else:
                    self.frame_guard.note_dropped("FRAME_NO_DISPATCH")
                continue
            if kind == "res":
                fut = self._pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    if msg.get("ok"):
                        fut.set_result(msg.get("payload", {}))
                    else:
                        err = msg.get("error", {})
                        fut.set_exception(GatewayError(
                            err.get("code", "RPC_ERROR"),
                            err.get("message", ""), err.get("details")))
            elif kind == "event":
                if self.event_cb:
                    try:
                        self.event_cb(msg.get("event"),
                                      msg.get("payload", {}))
                    except Exception:
                        log.exception("event callback failed")
