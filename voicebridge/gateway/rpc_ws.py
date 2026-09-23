"""Qubit-dialect RPC WebSocket served by the one Qubit listener (D2).

Owner decision 2026-09-18 (D2): implement Qubit-compatible RPC and
WebSocket capabilities through the existing Qubit listener; do not start a
second gateway/server; reuse Qubit authentication, policy, broker, audit,
resource limits, cancellation, and e-stop; add adapters under Qubit-owned
routes/namespaces.

This module adds **no listener**. It reuses the RFC 6455 framing, the shared
single-use ticket authority (scope ``rpc``), and the origin/peer/e-stop checks
already owned by :mod:`voicebridge.pty_ws`, and dispatches each ``req`` frame to
the canonical :mod:`voicebridge.gateway.rpc` registry. A ``res`` frame is
returned for every accepted request; the transport never invents a success and
never opens an unauthenticated socket.

OpenClaw wire compatibility (owner decision D2, "preserve compatible OpenClaw
wire behavior"): immediately after the RFC 6455 upgrade the socket emits the
pre-connect ``connect.challenge`` **event** frame, and a client ``req`` with
``method:"connect"`` is answered with the required ``hello-ok`` payload
(``server``/``features``/``snapshot``/``policy``/``auth``) through the registry's
``connect`` handler. The challenge carries no credential; authorization still
happens at the ticket/e-stop gate *before* the upgrade completes.
"""
from __future__ import annotations

import json
import secrets
import select
import threading
import time
from typing import Any, Dict, Optional

from .. import pty_ws as P

SCOPE_RPC = "rpc"
MAX_FRAMES = 100000
IDLE_TIMEOUT_S = 30 * 60.0


class RpcWsServer:
    """One Qubit-dialect RPC WebSocket authority for a listener (shared)."""

    def __init__(self, *, cfg: Any = None, tickets: Any = None,
                 estop: Optional[Any] = None, max_frames: int = MAX_FRAMES,
                 idle_timeout_s: float = IDLE_TIMEOUT_S,
                 emit_challenge: bool = True) -> None:
        self.cfg = cfg
        self.tickets = tickets
        self._estop = estop
        self._max_frames = int(max_frames)
        self._idle = float(idle_timeout_s)
        self._emit_challenge = bool(emit_challenge)
        self._lock = threading.Lock()
        self._active = 0

    def _estop_engaged(self) -> bool:
        if self._estop is not None:
            try:
                return bool(self._estop(self.cfg))
            except Exception:
                return True
        try:
            from ..broker import estop_engaged
            return bool(estop_engaged(self.cfg))
        except Exception:
            return True

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    def serve(self, handler: Any, *, ticket_value: str, origin_ok: bool,
              peer_ok: bool) -> None:
        """Run one RPC WebSocket session to completion (handler thread)."""
        if not peer_ok:
            P._reject(handler, 403, "PEER_REFUSED",
                      "RPC WebSocket is bound to the approved local interface.")
            return
        if not origin_ok:
            P._reject(handler, 403, "ORIGIN_REFUSED",
                      "RPC WebSocket origin does not match the bound host.")
            return
        if self.tickets is None:
            P._reject(handler, 503, "TICKET_STORE_UNAVAILABLE",
                      "No ticket authority is attached to the RPC socket.")
            return
        ticket = self.tickets.consume(ticket_value, scope=SCOPE_RPC)
        if ticket is None:
            P._reject(handler, 401, "TICKET_REQUIRED",
                      "A short-lived scoped RPC ticket is required.")
            return
        if self._estop_engaged():
            P._reject(handler, 423, "ESTOP_ENGAGED",
                      "Qubit emergency stop is engaged; RPC socket refused.")
            return
        try:
            P.handshake(handler)
        except P.WsProtocolError as exc:
            P._reject(handler, 400, "WS_HANDSHAKE", str(exc))
            return
        with self._lock:
            self._active += 1
        from .rpc import default_registry, handle_request

        registry = default_registry()
        sock = handler.connection
        sock.settimeout(P.POLL_TIMEOUT)
        last = time.monotonic()
        frames = 0
        if self._emit_challenge:
            challenge = _encode({
                "type": "event", "event": "connect.challenge",
                "payload": {"nonce": secrets.token_hex(16),
                            "ts": int(time.time() * 1000)}})
            if not P.write_frame(sock, P._OP_TEXT, challenge):
                return
        try:
            while True:
                if self._estop_engaged():
                    P.close_frame(sock, 1011, "emergency stop")
                    return
                if time.monotonic() - last > self._idle:
                    P.close_frame(sock, 1000, "idle timeout")
                    return
                try:
                    readable, _, _ = select.select([sock], [], [], 0)
                except (OSError, ValueError):
                    return
                if not readable:
                    continue
                frame = P.read_frame(sock)
                if frame is None:
                    P.close_frame(sock, 1000, "session ended")
                    return
                opcode, payload = frame
                if opcode == P._OP_CLOSE:
                    P.close_frame(sock, 1000, "bye")
                    return
                if opcode == P._OP_PING:
                    P.write_frame(sock, P._OP_PONG, payload)
                    continue
                if opcode == P._OP_PONG:
                    continue
                if opcode not in (P._OP_TEXT, P._OP_BIN):
                    continue
                last = time.monotonic()
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    if not P.write_frame(sock, P._OP_TEXT, _encode({
                            "type": "res", "id": None, "ok": False,
                            "error": {"code": "FRAME_BAD_SHAPE",
                                      "message": "frame is not JSON"}})):
                        return
                    continue
                if not isinstance(msg, dict) or msg.get("type") != "req":
                    if not P.write_frame(sock, P._OP_TEXT, _encode({
                            "type": "res", "id": msg.get("id")
                            if isinstance(msg, dict) else None,
                            "ok": False,
                            "error": {"code": "FRAME_BAD_SHAPE",
                                      "message": "only 'req' frames are "
                                                 "accepted"}})):
                        return
                    continue
                res = handle_request(registry, self.cfg, msg)
                if not P.write_frame(sock, P._OP_TEXT, _encode(res)):
                    return
                frames += 1
                if frames > self._max_frames:
                    P.close_frame(sock, 1008, "frame limit")
                    return
        finally:
            with self._lock:
                self._active -= 1


def _encode(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=True, sort_keys=False).encode("utf-8")
