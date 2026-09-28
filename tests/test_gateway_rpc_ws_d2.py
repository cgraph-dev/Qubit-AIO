"""D2 — OpenClaw-dialect RPC WebSocket on the single Qubit listener.

Owner decision 2026-09-18 (D2): implement OpenClaw-compatible RPC/WS through
the existing listener, reusing one ticket authority, origin/peer/e-stop checks,
and the canonical in-process registry. No second listener is started.

OpenClaw wire compatibility: the socket emits the pre-connect
``connect.challenge`` event and answers a ``connect`` request with the required
``hello-ok`` payload (server/features/snapshot/policy/auth).
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _Handler:
    """Minimal BaseHTTPRequestHandler double backed by a real socket."""

    def __init__(self, conn: socket.socket, key: str) -> None:
        self.connection = conn
        self.wfile = conn.makefile("wb", buffering=0)
        self.headers = {"Sec-WebSocket-Key": key, "Upgrade": "websocket"}
        self.close_connection = False

    def send_response(self, code: int, message: str = "") -> None:
        self.wfile.write(f"HTTP/1.1 {code} X\r\n".encode())

    def send_header(self, key: str, value: str) -> None:
        self.wfile.write(f"{key}: {value}\r\n".encode())

    def end_headers(self) -> None:
        self.wfile.write(b"\r\n")

    def flush(self) -> None:
        try:
            self.wfile.flush()
        except Exception:
            pass


def _mask(payload: bytes) -> bytes:
    mask = os.urandom(4)
    masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return mask + masked


def _send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    n = len(payload)
    if n < 126:
        head = bytes([0x81, 0x80 | n])
    elif n < (1 << 16):
        head = bytes([0x81, 0x80 | 126]) + struct.pack("!H", n)
    else:
        head = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", n)
    sock.sendall(head + _mask(payload))


def _send_close(sock: socket.socket) -> None:
    sock.sendall(bytes([0x88, 0x80]) + _mask(b""))


class _Conn:
    def __init__(self, server, client, thread, handler, server_conn):
        self.server = server
        self.client = client
        self.thread = thread
        self.handler = handler
        self.server_conn = server_conn
        self.buf = bytearray()

    # -- buffered reads (the challenge may share a TCP segment with the 101) --
    def read_http(self, n: int = 4096) -> bytes:
        """Read the HTTP upgrade response, leaving any WS frames in ``buf``."""
        data = self.client.recv(n)
        self.buf += data
        while b"\r\n\r\n" not in self.buf and len(self.buf) < (1 << 16):
            chunk = self.client.recv(4096)
            if not chunk:
                break
            self.buf += chunk
        idx = self.buf.find(b"\r\n\r\n")
        if idx != -1:
            del self.buf[:idx + 4]
        return data

    def _need(self, n: int) -> None:
        while len(self.buf) < n:
            chunk = self.client.recv(4096)
            if not chunk:
                break
            self.buf += chunk

    def _pop(self, n: int) -> bytes:
        self._need(n)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def recv_frame(self):
        head = self._pop(2)
        if len(head) < 2:
            return None
        b1, b2 = head[0], head[1]
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._pop(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._pop(8))[0]
        payload = self._pop(length) if length else b""
        return opcode, payload

    def close(self) -> None:
        for obj in (self.client, self.server_conn):
            try:
                obj.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                obj.close()
            except OSError:
                pass
        try:
            self.handler.wfile.close()
        except Exception:
            pass
        self.thread.join(timeout=3)


def _run(ticket: str, *, estop=None, origin_ok: bool = True,
         emit_challenge: bool = True) -> _Conn:
    from voicebridge.gateway import rpc_ws as RW
    from voicebridge.pty_ws import TicketStore

    store = TicketStore()
    if ticket == "MINT":
        ticket, _ = store.mint(scope=RW.SCOPE_RPC, session="s", owner="test")
    server = RW.RpcWsServer(cfg=None, tickets=store, estop=estop,
                            emit_challenge=emit_challenge)
    client, server_conn = socket.socketpair()
    client.settimeout(5.0)
    handler = _Handler(server_conn, "dGhlIHNhbXBsZSBub25jZQ==")
    t = threading.Thread(target=server.serve, kwargs=dict(
        handler=handler, ticket_value=ticket, origin_ok=origin_ok,
        peer_ok=True), daemon=True)
    t.start()
    return _Conn(server, client, t, handler, server_conn)


def _read_challenge(conn: _Conn) -> dict:
    opcode, payload = conn.recv_frame()
    assert opcode == 0x1
    event = json.loads(payload)
    assert event["type"] == "event"
    assert event["event"] == "connect.challenge"
    assert event["payload"]["nonce"]
    assert isinstance(event["payload"]["ts"], int)
    return event


def test_rpc_ws_emits_the_openclaw_connect_challenge():
    conn = _run("MINT", estop=lambda cfg: False)
    try:
        http = conn.read_http()
        assert b"101" in http, http
        _read_challenge(conn)
        _send_close(conn.client)
    finally:
        conn.close()


def test_rpc_ws_connect_returns_hello_ok():
    conn = _run("MINT", estop=lambda cfg: False)
    try:
        http = conn.read_http()
        assert b"101" in http, http
        _read_challenge(conn)
        _send_text(conn.client, json.dumps({
            "type": "req", "id": "c1", "method": "connect",
            "params": {"minProtocol": 4, "maxProtocol": 4,
                       "client": {"id": "test", "version": "1.0",
                                  "platform": "linux", "mode": "operator"},
                       "role": "operator",
                       "scopes": ["operator.admin"]}}))
        opcode, payload = conn.recv_frame()
        assert opcode == 0x1
        res = json.loads(payload)
        assert res["type"] == "res" and res["id"] == "c1" and res["ok"] is True
        hello = res["payload"]
        assert hello["type"] == "hello-ok"
        assert hello["protocol"] == 4
        for required in ("server", "features", "snapshot", "policy", "auth"):
            assert required in hello, required
        assert hello["features"]["methods"]
        assert "connect" in hello["features"]["methods"]
        assert hello["features"]["events"] == ["connect.challenge"]
        # A client-supplied scope list never widens the socket's scopes.
        assert hello["auth"]["scopes"] != ["operator.admin"]
        assert hello["auth"]["role"] == "operator"
        _send_close(conn.client)
    finally:
        conn.close()


def test_rpc_ws_round_trips_a_req_frame():
    conn = _run("MINT", estop=lambda cfg: False)
    try:
        http = conn.read_http()
        assert b"101" in http, http
        _read_challenge(conn)
        _send_text(conn.client, json.dumps({
            "type": "req", "id": "1", "method": "diagnostics.cpuProfile",
            "params": {}}))
        opcode, payload = conn.recv_frame()
        assert opcode == 0x1  # text
        res = json.loads(payload)
        assert res["type"] == "res" and res["id"] == "1" and res["ok"] is True
        assert res["payload"]["code"] == "DIAGNOSTICS_OPERATOR_REQUIRED"
        _send_close(conn.client)
    finally:
        conn.close()


def test_rpc_ws_can_serve_a_legacy_client_without_the_challenge():
    conn = _run("MINT", estop=lambda cfg: False, emit_challenge=False)
    try:
        http = conn.read_http()
        assert b"101" in http, http
        _send_text(conn.client, json.dumps({
            "type": "req", "id": "1", "method": "diagnostics.cpuProfile",
            "params": {}}))
        opcode, payload = conn.recv_frame()
        assert opcode == 0x1
        res = json.loads(payload)
        assert res["type"] == "res" and res["id"] == "1"
        _send_close(conn.client)
    finally:
        conn.close()


def test_rpc_ws_requires_a_ticket():
    conn = _run("")
    try:
        resp = conn.client.recv(4096)
        assert b"401" in resp, resp
    finally:
        conn.close()


def test_rpc_ws_refuses_on_estop():
    conn = _run("MINT", estop=lambda cfg: True)
    try:
        resp = conn.client.recv(4096)
        assert b"423" in resp, resp
    finally:
        conn.close()


def test_rpc_ws_refuses_bad_origin():
    conn = _run("MINT", origin_ok=False)
    try:
        resp = conn.client.recv(4096)
        assert b"403" in resp, resp
    finally:
        conn.close()


def test_rpc_ws_is_a_qubit_owned_addition():
    from voicebridge import qubit_compat as HC
    from voicebridge.gateway.rpc_ws import SCOPE_RPC

    assert SCOPE_RPC == "rpc"
    get_route, _ = HC.match("GET", "/api/gateway/rpc")
    assert get_route and get_route["handler"] == "h_gateway_rpc_methods"
