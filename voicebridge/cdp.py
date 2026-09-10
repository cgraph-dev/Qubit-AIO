"""Packet 8U — minimal loopback CDP transport (stdlib only).

Speaks the Chrome DevTools Protocol over plain HTTP (``/json/*``) and a
minimal RFC 6455 websocket client, both pinned to the loopback interface.
No third-party websocket dependency, no remote endpoints: any non-loopback
host is refused before any byte is sent (SSRF-by-config is impossible).

Hermes resolves user-supplied remote CDP endpoints
(``tools/browser_tool_cdp.py`` ``_resolve_cdp_override``); Qubit
deliberately narrows to loopback-only and records the divergence in
``docs/UPSTREAM_REUSE.md`` (independent rewrite, behavior compared).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
HTTP_TIMEOUT_S = 5.0
WS_TIMEOUT_S = 15.0
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 8000

UNAVAILABLE_CODE = "CDP_UNAVAILABLE"
PROTOCOL_CODE = "CDP_PROTOCOL_ERROR"
METHOD_CODE = "CDP_METHOD_ERROR"
LOOPBACK_CODE = "CDP_NOT_LOOPBACK"


class CdpError(Exception):
    """Typed CDP failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _base() -> str:
    return f"http://{CDP_HOST}:{CDP_PORT}"


def http_json(method: str, path: str, *, timeout: float = HTTP_TIMEOUT_S,
              query: str = "") -> Any:
    """GET/PUT one ``/json/*`` endpoint; parsed JSON or CdpError."""
    url = _base() + path + (("?" + query) if query else "")
    req = urllib.request.Request(url, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise CdpError(UNAVAILABLE_CODE,
                               f"CDP {method} {path}: HTTP {resp.status}")
            raw = resp.read(4 * 1024 * 1024)
    except CdpError:
        raise
    except Exception as exc:
        raise CdpError(UNAVAILABLE_CODE,
                       f"no loopback CDP endpoint ({type(exc).__name__})")
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise CdpError(PROTOCOL_CODE, f"CDP {path}: invalid JSON ({exc})")


def probe(*, timeout: float = HTTP_TIMEOUT_S) -> Dict[str, Any]:
    """``/json/version`` or raise CDP_UNAVAILABLE."""
    info = http_json("GET", "/json/version", timeout=timeout)
    if not isinstance(info, dict) or "webSocketDebuggerUrl" not in info:
        raise CdpError(PROTOCOL_CODE, "CDP /json/version: no debugger URL")
    return info


def _recv_exact(sock: socket.socket, n: int, deadline: float) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise CdpError(PROTOCOL_CODE, "CDP websocket: timed out")
        sock.settimeout(timeout)
        try:
            data = sock.recv(min(remaining, 65536))
        except socket.timeout as exc:
            raise CdpError(PROTOCOL_CODE, "CDP websocket: timed out") from exc
        if not data:
            raise CdpError(PROTOCOL_CODE, "CDP websocket: closed by peer")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


class WsSession:
    """One CDP websocket connection (browser-level or target-level URL)."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._next_id = 0
        self.events: List[Dict[str, Any]] = []
        self.closed = False

    # ------------------------------------------------------------- connect
    @classmethod
    def connect(cls, ws_url: str, *,
                timeout: float = WS_TIMEOUT_S) -> "WsSession":
        parts = urllib.parse.urlsplit(ws_url)
        if parts.scheme not in ("ws",):
            raise CdpError(LOOPBACK_CODE,
                           f"CDP websocket must be ws:// loopback, got "
                           f"{parts.scheme or '?'}://")
        host = (parts.hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise CdpError(LOOPBACK_CODE,
                           "CDP websocket refused: loopback only")
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        deadline = time.monotonic() + timeout
        try:
            raw = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise CdpError(UNAVAILABLE_CODE,
                           f"CDP websocket unreachable ({exc})")
        if host == "::1":
            pass  # create_connection already resolved the literal
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("latin-1")
        try:
            raw.settimeout(max(0.1, deadline - time.monotonic()))
            raw.sendall(request)
            header = b""
            while b"\r\n\r\n" not in header:
                chunk = raw.recv(4096)
                if not chunk:
                    raise CdpError(PROTOCOL_CODE, "CDP upgrade: EOF")
                header += chunk
                if len(header) > 16384:
                    raise CdpError(PROTOCOL_CODE, "CDP upgrade: oversize")
                if time.monotonic() > deadline:
                    raise CdpError(PROTOCOL_CODE, "CDP upgrade: timed out")
        except CdpError:
            raw.close()
            raise
        except Exception as exc:
            raw.close()
            raise CdpError(PROTOCOL_CODE, f"CDP upgrade failed ({exc})")
        head = header.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        lines = head.split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2 or parts[1] != "101":
            raw.close()
            raise CdpError(PROTOCOL_CODE,
                           f"CDP upgrade refused: {lines[0][:120]}")
        accept = ""
        for line in lines[1:]:
            if line.lower().startswith("sec-websocket-accept:"):
                accept = line.split(":", 1)[1].strip()
        want = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        if accept != want:
            raw.close()
            raise CdpError(PROTOCOL_CODE, "CDP upgrade: bad accept key")
        return cls(raw)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass

    # --------------------------------------------------------------- frames
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if len(payload) > MAX_FRAME_BYTES:
            raise CdpError(PROTOCOL_CODE, "CDP frame: payload too large")
        header = bytes([0x80 | (opcode & 0x0F)])
        mask_bit = 0x80
        length = len(payload)
        if length < 126:
            header += bytes([mask_bit | length])
        elif length < 65536:
            header += bytes([mask_bit | 126]) + struct.pack(">H", length)
        else:
            header += bytes([mask_bit | 127]) + struct.pack(">Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _recv_frame(self, deadline: float) -> Tuple[int, bytes]:
        head = _recv_exact(self._sock, 2, deadline)
        fin = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(self._sock, 2,
                                                     deadline))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(self._sock, 8,
                                                     deadline))[0]
        if length > MAX_FRAME_BYTES:
            raise CdpError(PROTOCOL_CODE, "CDP frame: peer payload too large")
        mask = _recv_exact(self._sock, 4, deadline) if masked else b""
        payload = _recv_exact(self._sock, length, deadline) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if not fin or opcode == 0x0:
            raise CdpError(PROTOCOL_CODE,
                           "CDP frame: fragmentation unsupported")
        return opcode, payload

    def _recv_message(self, deadline: float) -> Dict[str, Any]:
        while True:
            opcode, payload = self._recv_frame(deadline)
            if opcode == 0x8:
                raise CdpError(PROTOCOL_CODE, "CDP websocket: peer closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode not in (0x1, 0x2):
                continue
            try:
                msg = json.loads(payload.decode("utf-8", "replace"))
            except ValueError as exc:
                raise CdpError(PROTOCOL_CODE,
                               f"CDP message: invalid JSON ({exc})")
            if isinstance(msg, dict):
                return msg
            raise CdpError(PROTOCOL_CODE, "CDP message: not an object")

    # ----------------------------------------------------------------- call
    def call(self, method: str, params: Optional[Dict[str, Any]] = None,
             *, timeout: float = WS_TIMEOUT_S,
             session_id: str = "") -> Any:
        """Send one CDP command; return ``result`` or raise CdpError."""
        if self.closed:
            raise CdpError(PROTOCOL_CODE, "CDP websocket: closed")
        self._next_id += 1
        call_id = self._next_id
        envelope: Dict[str, Any] = {"id": call_id, "method": method,
                                    "params": params or {}}
        if session_id:
            envelope["sessionId"] = session_id
        deadline = time.monotonic() + timeout
        try:
            self._sock.settimeout(max(0.1, deadline - time.monotonic()))
            self._send_frame(0x1, json.dumps(envelope).encode("utf-8"))
        except CdpError:
            raise
        except Exception as exc:
            raise CdpError(PROTOCOL_CODE, f"CDP send failed ({exc})")
        while True:
            if time.monotonic() > deadline:
                raise CdpError(PROTOCOL_CODE,
                               f"CDP {method}: timed out")
            try:
                msg = self._recv_message(deadline)
            except CdpError:
                raise
            except Exception as exc:
                raise CdpError(PROTOCOL_CODE, f"CDP recv failed ({exc})")
            if msg.get("id") == call_id:
                if "error" in msg:
                    err = msg["error"] or {}
                    raise CdpError(METHOD_CODE,
                                   f"CDP {method}: {err.get('message', err)}"
                                   [:300])
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
                if len(self.events) > 64:
                    self.events.pop(0)
