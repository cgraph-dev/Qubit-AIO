"""Primary OpenClaw execution backend: the Gateway WebSocket protocol (v4).

This is the documented programmatic surface of the running gateway
(https://docs.openclaw.ai/gateway/protocol). We connect as a trusted loopback
backend client (`client.id: "gateway-client"`, `mode: "backend"`) with the
existing shared gateway token — no device pairing, no config changes.

Verified live against this machine's gateway (2026.7.1-2, port 18789):
  sessions.create {key, model?, message?} -> runId
  sessions.send   {key, message}          -> runId
  agent.wait      {runId, timeoutMs}      -> status
  chat.history    {sessionKey, limit}     -> transcript
  sessions.abort  {key}                   -> cancel active run
  tts.speak       {text}                  -> audioBase64 (gateway TTS option)

Auto-reconnects with backoff and re-subscribes message events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import websockets

from ..logging_setup import log_event

log = logging.getLogger("voicebridge.gateway")

SCOPES = ["operator.read", "operator.write", "operator.admin",
          "operator.approvals", "operator.talk"]


@dataclass
class GatewayError(Exception):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


FRAME_TYPES = ("res", "event")
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
        if kind == "event" and not isinstance(msg.get("event"), str):
            raise GatewayError("FRAME_BAD_SHAPE",
                               "gateway event has no name")
        return kind

    def note_dropped(self, code: str) -> None:
        self.dropped += 1
        try:
            log_event("openclaw.gateway.frame.drop", code=code)
        except Exception:
            pass


@dataclass
class RunResult:
    status: str            # ok | timeout | failed | aborted
    reply: str = ""
    elapsed_s: float = 0.0
    run_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class GatewayClient:
    """Async gateway WS client with a simple request/response correlation."""

    def __init__(self, url: str, token: str, agent: str = "main",
                 connect_timeout: float = 15.0, max_retries: int = 3,
                 event_cb: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> None:
        self.url = url
        self.token = token
        self.agent = agent
        self.connect_timeout = connect_timeout
        self.max_retries = max_retries
        self.event_cb = event_cb
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._next_id = 0
        self.connected = False
        self.server_version = ""
        self.conn_id = ""
        self._reader_task: Optional[asyncio.Task] = None
        self._subscribed_sessions: set[str] = set()
        self._lock = asyncio.Lock()
        self.frame_guard = FrameGuard()

    # ---- connection ----------------------------------------------------------
    async def connect(self) -> bool:
        for attempt in range(self.max_retries):
            try:
                self.ws = await websockets.connect(
                    self.url, max_size=64 * 1024 * 1024,
                    open_timeout=self.connect_timeout,
                    ping_interval=20, ping_timeout=20)
                await self._handshake()
                self.connected = True
                self._reader_task = asyncio.create_task(self._reader())
                log_event("openclaw.gateway.connect", url=self.url,
                          version=self.server_version, conn=self.conn_id)
                # re-subscribe after reconnect
                for key in list(self._subscribed_sessions):
                    await self.subscribe_messages(key)
                return True
            except Exception as exc:
                log_event("openclaw.gateway.connect.retry", attempt=attempt + 1,
                          error=str(exc)[:200])
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
        self.connected = False
        return False

    async def _handshake(self) -> None:
        assert self.ws is not None
        raw = await asyncio.wait_for(self.ws.recv(), self.connect_timeout)
        chal = json.loads(raw)
        if chal.get("event") != "connect.challenge":
            raise GatewayError("BAD_HANDSHAKE", "expected connect.challenge")
        params = {
            "minProtocol": 4, "maxProtocol": 4,
            "client": {"id": "gateway-client", "version": "0.1.0",
                       "platform": "linux", "mode": "backend"},
            "role": "operator", "scopes": SCOPES,
            "caps": [], "commands": [], "permissions": {},
            "auth": {"token": self.token}, "locale": "en-US",
            "userAgent": "qubit-voicebridge/1.0.0",
        }
        await self.ws.send(json.dumps({"type": "req", "id": "connect",
                                       "method": "connect", "params": params}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), self.connect_timeout)
            msg = json.loads(raw)
            if msg.get("type") == "res" and msg.get("id") == "connect":
                if not msg.get("ok"):
                    raise GatewayError("AUTH_FAILED", json.dumps(msg.get("error", {}))[:300])
                payload = msg["payload"]
                self.server_version = payload.get("server", {}).get("version", "")
                self.conn_id = payload.get("server", {}).get("connId", "")
                return

    async def disconnect(self) -> None:
        self.connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def _reader(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                try:
                    msg = self.frame_guard.check_raw(raw)
                    kind = self.frame_guard.check_message(msg)
                except GatewayError as exc:
                    self.frame_guard.note_dropped(exc.code)
                    continue
                if kind == "ignore":
                    continue
                if kind == "res":
                    fut = self._pending.pop(msg.get("id"), None)
                    if fut and not fut.done():
                        if msg.get("ok"):
                            fut.set_result(msg.get("payload", {}))
                        else:
                            err = msg.get("error", {})
                            fut.set_exception(GatewayError(err.get("code", "RPC_ERROR"),
                                                           err.get("message", ""),
                                                           err.get("details")))
                elif kind == "event":
                    ev = msg.get("event")
                    payload = msg.get("payload", {})
                    if self.event_cb:
                        try:
                            self.event_cb(ev, payload)
                        except Exception:
                            log.exception("event callback failed")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log_event("openclaw.gateway.reader.drop", error=str(exc)[:200])
            self.connected = False
            # notify pending callers so they don't hang
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(GatewayError("CONNECTION_LOST", str(exc)[:200]))
            self._pending.clear()

    async def call(self, method: str, params: Dict[str, Any],
                   timeout: float = 60.0) -> Any:
        """Send an RPC and await its response."""
        if not self.connected or self.ws is None:
            raise GatewayError("NOT_CONNECTED", "gateway websocket is not connected")
        self._next_id += 1
        rid = f"{method}-{self._next_id}-{uuid.uuid4().hex[:6]}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self.ws.send(json.dumps({"type": "req", "id": rid, "method": method,
                                       "params": params}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    # ---- high-level operations ------------------------------------------------
    async def create_session(self, key: str, label: str, model: Optional[str] = None,
                             message: Optional[str] = None,
                             timeout: float = 30.0) -> Dict[str, Any]:
        params: Dict[str, Any] = {"key": key, "label": label}
        if model:
            params["model"] = model
        if message:
            params["message"] = message
        return await self.call("sessions.create", params, timeout=timeout)

    async def send_message(self, key: str, message: str, timeout: float = 30.0) -> Dict[str, Any]:
        return await self.call("sessions.send", {"key": key, "message": message}, timeout=timeout)

    async def wait_run(self, run_id: str, timeout_ms: int = 120_000) -> Dict[str, Any]:
        return await self.call("agent.wait", {"runId": run_id, "timeoutMs": timeout_ms},
                               timeout=(timeout_ms / 1000) + 15)

    async def history(self, session_key: str, limit: int = 10, timeout: float = 30.0) -> List[Dict[str, Any]]:
        res = await self.call("chat.history", {"sessionKey": session_key, "limit": limit}, timeout=timeout)
        return res.get("messages", [])

    async def abort(self, key: str, timeout: float = 30.0) -> Dict[str, Any]:
        return await self.call("sessions.abort", {"key": key}, timeout=timeout)

    async def subscribe_messages(self, key: str, timeout: float = 30.0) -> None:
        if not self.connected:
            return
        self._subscribed_sessions.add(key)
        try:
            await self.call("sessions.messages.subscribe", {"key": key}, timeout=timeout)
        except GatewayError:
            pass

    async def tts_speak(self, text: str, timeout: float = 60.0):
        """Return (audio_b64, mime, format) via the gateway's configured TTS."""
        res = await self.call("tts.speak", {"text": text}, timeout=timeout)
        return (res.get("audioBase64"), res.get("mimeType"), res.get("outputFormat"))

    async def list_sessions(self, limit: int = 100, timeout: float = 30.0) -> List[Dict[str, Any]]:
        res = await self.call("sessions.list", {"limit": limit}, timeout=timeout)
        return res.get("sessions", [])

    async def list_tasks(self, timeout: float = 30.0) -> List[Dict[str, Any]]:
        res = await self.call("tasks.list", {}, timeout=timeout)
        return res.get("tasks") or res.get("items") or []

    async def agents_list(self, timeout: float = 30.0) -> List[Dict[str, Any]]:
        res = await self.call("agents.list", {}, timeout=timeout)
        return res.get("agents", [])

    async def status(self, timeout: float = 15.0) -> Dict[str, Any]:
        return await self.call("status", {}, timeout=timeout)


class GatewaySync:
    """Synchronous facade: runs the async GatewayClient on a background loop.

    Lets the (thread-based) orchestrator use the async client without
    restructuring: call() blocks on a future resolved on the loop thread.
    """

    def __init__(self, url: str, token: str, agent: str = "main",
                 event_cb: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> None:
        self._loop = asyncio.new_event_loop()
        self._client = GatewayClient(url, token, agent, event_cb=event_cb)
        self._thread = None
        self._started = False

    def start(self) -> bool:
        if self._started:
            return self.connected
        import threading
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                        name="gateway-loop")
        self._thread.start()
        self._started = True
        return self.connected

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro, timeout: float):
        if not self._started:
            self.start()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    @property
    def connected(self) -> bool:
        return self._client.connected if self._started else False

    def connect(self, timeout: float = 60.0) -> bool:
        if not self._started:
            self.start()
        return self._submit(self._client.connect(), timeout)

    def disconnect(self, timeout: float = 15.0) -> None:
        if self._started:
            try:
                self._submit(self._client.disconnect(), timeout)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._started = False

    # passthroughs --------------------------------------------------------------
    def create_session(self, key, label, model=None, message=None, timeout=45.0):
        return self._submit(self._client.create_session(key, label, model, message), timeout)

    def send_message(self, key, message, timeout=45.0):
        return self._submit(self._client.send_message(key, message), timeout)

    def wait_run(self, run_id, timeout_ms=120_000):
        return self._submit(self._client.wait_run(run_id, timeout_ms), (timeout_ms / 1000) + 20)

    def history(self, session_key, limit=10, timeout=45.0):
        return self._submit(self._client.history(session_key, limit), timeout)

    def abort(self, key, timeout=45.0):
        return self._submit(self._client.abort(key), timeout)

    def subscribe_messages(self, key, timeout=45.0):
        return self._submit(self._client.subscribe_messages(key), timeout)

    def tts_speak(self, text, timeout=75.0):
        return self._submit(self._client.tts_speak(text), timeout)

    def list_sessions(self, limit=100, timeout=45.0):
        return self._submit(self._client.list_sessions(limit), timeout)

    def list_tasks(self, timeout=45.0):
        return self._submit(self._client.list_tasks(), timeout)

    def agents_list(self, timeout=45.0):
        return self._submit(self._client.agents_list(), timeout)

    def status(self, timeout=30.0):
        return self._submit(self._client.status(), timeout)
