"""Browser-based Voice Edit panel (Next.js/Three.js transport).

The Tk ``PreviewOverlay`` stays the default. This module is the opt-in
``voice_edit.panel_backend: web`` backend: a loopback-only HTTP/WS server that
streams the same status/partial/preview/history/level surface to a local web
panel and routes button actions back to the helper's ``on_action`` exactly as
the Tk overlay does.

The wire contract is frozen (version 1). Server -> client messages:

    hello, status, partial, preview, error, history, level, ping

Client -> server:

    {"type": "action", "action": "apply|copy|edit|retry|revert|cancel|edit_submit",
     "payload": {...}}

Security: random per-process token (query or header), loopback peers only, no
CORS wildcard, bounded history and bounded per-client queues. ``level`` and
``partial`` may be coalesced or dropped under backpressure; inbound actions are
never queued or dropped.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

VERSION = 1
HOST = "127.0.0.1"
MAX_HISTORY = 100
MAX_QUEUE = 64
MAX_TEXT = 20000
PING_INTERVAL_S = 20.0
ALLOWED_ACTIONS = frozenset({
    "apply", "copy", "edit", "retry", "revert", "cancel", "edit_submit",
})
COALESCE_TYPES = frozenset({"level", "partial"})

TOKEN_HEADER = "X-Voice-Edit-Token"
PLACEHOLDER_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Qubit Voice Edit</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;
      background:#07070b;color:#dfe7f3;font:15px/1.5 system-ui,sans-serif}
 main{max-width:34rem;padding:2rem;text-align:center}
 h1{margin:0 0 .5rem;font-size:1.4rem;letter-spacing:.02em}
 code{color:#7ce7ff}
</style></head><body><main>
<h1>Qubit Voice Edit</h1>
<p>The web panel bundle was not found at <code>frontend/voice-edit-hud/out</code>.</p>
<p>This loopback transport is running; build the panel to replace this page.</p>
</main></body></html>
"""


class WebPanelUnavailable(RuntimeError):
    """Typed refusal: the web transport/browser could not be started."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code or "web_panel_unavailable"
        self.detail = detail or self.code


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    backend: str = ""
    executable: str = ""
    url: str = ""
    reason: str = ""
    user_data_dir: str = ""
    pid: int = 0
    process: Any = None


_BROWSERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("chromium", ("chromium", "chromium-browser")),
    ("chrome", ("google-chrome", "google-chrome-stable", "chrome")),
    ("brave", ("brave-browser", "brave")),
    ("edge", ("microsoft-edge", "microsoft-edge-stable", "msedge")),
)


def _cfg_get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def resolve_panel_backend(cfg: Any = None) -> str:
    """Return ``"web"`` or ``"tk"``; anything unknown fails safe to Tk."""
    value = str(_cfg_get(cfg, "voice_edit", "panel_backend",
                         default="tk") or "tk").strip().lower()
    return "web" if value == "web" else "tk"


def find_browser(which: Optional[Callable[[str], Optional[str]]] = None
                 ) -> Optional[Tuple[str, str]]:
    which = which or shutil.which
    for backend, names in _BROWSERS:
        for name in names:
            try:
                path = which(name)
            except Exception:
                path = None
            if path:
                return backend, path
    return None


def build_browser_argv(executable: str, url: str, user_data_dir: str) -> List[str]:
    return [
        str(executable),
        f"--app={url}",
        "--window-size=900,760",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def launch_app_window(
    url: str,
    *,
    which: Optional[Callable[[str], Optional[str]]] = None,
    popen: Optional[Callable[..., Any]] = None,
    mkdtemp: Optional[Callable[..., str]] = None,
) -> LaunchResult:
    """Launch a Chromium-family ``--app`` window with a private profile."""
    found = find_browser(which)
    if not found:
        return LaunchResult(ok=False, url=str(url), reason="browser_not_found")
    backend, executable = found
    try:
        user_data_dir = (mkdtemp or tempfile.mkdtemp)(
            prefix="qubit-voice-edit-")
    except Exception as exc:  # pragma: no cover - host tempdir failure
        return LaunchResult(ok=False, backend=backend, executable=executable,
                            url=str(url), reason=f"profile_dir_failed: {exc}")
    argv = build_browser_argv(executable, str(url), user_data_dir)
    try:
        proc = (popen or subprocess.Popen)(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return LaunchResult(ok=False, backend=backend, executable=executable,
                            url=str(url), reason=f"browser_launch_failed: {exc}",
                            user_data_dir=user_data_dir)
    return LaunchResult(
        ok=True,
        backend=backend,
        executable=executable,
        url=str(url),
        user_data_dir=user_data_dir,
        pid=int(getattr(proc, "pid", 0) or 0),
        process=proc,
    )


def _is_loopback(remote: Optional[str]) -> bool:
    value = str(remote or "")
    return value in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _clamp_level(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _bounded_text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value if value is not None else "")[:limit]


class _Client:
    """One websocket with a bounded, coalescing outbound queue."""

    def __init__(self, ws: Any, *, max_queue: int = MAX_QUEUE) -> None:
        self.ws = ws
        self.max_queue = max(1, int(max_queue))
        self.pending: List[Dict[str, Any]] = []
        self.event = asyncio.Event()
        self.dropped = 0
        self.coalesced = 0
        self.closed = False

    def enqueue(self, msg: Dict[str, Any]) -> None:
        kind = str(msg.get("type") or "")
        if kind in COALESCE_TYPES:
            for idx in range(len(self.pending) - 1, -1, -1):
                if self.pending[idx].get("type") == kind:
                    self.pending[idx] = msg
                    self.coalesced += 1
                    return
            if len(self.pending) >= self.max_queue:
                self.dropped += 1
                return
            self.pending.append(msg)
            self.event.set()
            return
        if len(self.pending) >= self.max_queue:
            for idx, existing in enumerate(self.pending):
                if existing.get("type") in COALESCE_TYPES:
                    del self.pending[idx]
                    self.coalesced += 1
                    break
            else:
                self.dropped += 1
                return
        self.pending.append(msg)
        self.event.set()

    def pop_all(self) -> List[Dict[str, Any]]:
        items = self.pending
        self.pending = []
        self.event.clear()
        return items


class WebPanelOverlay:
    """Loopback HTTP/WS panel backend matching the Tk overlay's public API."""

    def __init__(
        self,
        on_action: Callable[[str, Dict[str, Any]], None],
        *,
        static_dir: Optional[Path] = None,
        launch_browser: bool = True,
        history_limit: int = MAX_HISTORY,
        max_queue: int = MAX_QUEUE,
        launch: Optional[Callable[[str], LaunchResult]] = None,
        port: int = 0,
        ping_interval_s: float = PING_INTERVAL_S,
    ) -> None:
        self._on_action = on_action
        self._static_dir = (Path(static_dir) if static_dir
                            else _default_static_dir())
        self._history_limit = max(1, min(int(history_limit), 1000))
        self._history: Deque[Dict[str, Any]] = deque(maxlen=self._history_limit)
        self._max_queue = max(1, int(max_queue))
        self._ping_interval_s = max(0.0, float(ping_interval_s))
        self._lock = threading.Lock()
        self._state = "idle"
        self._detail = ""
        self._partial = ""
        self._partial_final = False
        self._preview: Optional[Dict[str, Any]] = None
        self._level = 0.0
        self._visible = False
        self._closed = False
        self._clients: set[_Client] = set()
        self._browser: Optional[LaunchResult] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Any = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._running = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._port = int(port) if port else 0
        self.token = secrets.token_urlsafe(32)
        self._token_bytes = self.token.encode("utf-8")

        try:
            self._start(port=int(port) if port else 0)
            if launch_browser:
                url = self.page_url
                self._browser = (launch or launch_app_window)(url)
                if not self._browser.ok:
                    reason = self._browser.reason or "browser_unavailable"
                    raise WebPanelUnavailable("browser_unavailable", reason)
        except BaseException:
            self.close()
            raise

    # -- lifecycle -------------------------------------------------------
    @property
    def port(self) -> int:
        return self._port

    @property
    def page_url(self) -> str:
        return (f"http://{HOST}:{self._port}/voice-edit/"
                f"?token={self.token}")

    @property
    def ws_url(self) -> str:
        return (f"ws://{HOST}:{self._port}/voice-edit/ws"
                f"?token={self.token}")

    @property
    def browser(self) -> Optional[LaunchResult]:
        return self._browser

    def _start(self, *, port: int) -> None:
        try:
            from aiohttp import web
        except Exception as exc:
            raise WebPanelUnavailable("aiohttp_unavailable", str(exc)) from exc

        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_get("/voice-edit", self._handle_redirect)
        app.router.add_get("/voice-edit/", self._handle_index)
        app.router.add_get("/voice-edit/ws", self._handle_ws)
        app.router.add_get("/voice-edit/{path:.*}", self._handle_asset)
        self._app = app

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            sock = None
            runner = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((HOST, port))
                sock.listen(128)
                sock.setblocking(False)
                self._port = sock.getsockname()[1]
                runner = web.AppRunner(app, access_log=None)
                loop.run_until_complete(runner.setup())
                self._runner = runner
                site = web.SockSite(runner, sock)
                loop.run_until_complete(site.start())
                loop.call_soon(self._running.set)
                self._ready.set()
                loop.run_forever()
            except BaseException as exc:
                self._start_error = exc
                self._ready.set()
            finally:
                self._ready.set()
                if runner is not None:
                    with contextlib.suppress(Exception):
                        loop.run_until_complete(runner.cleanup())
                if sock is not None:
                    with contextlib.suppress(Exception):
                        sock.close()
                with contextlib.suppress(Exception):
                    loop.close()

        self._thread = threading.Thread(target=run, daemon=True,
                                        name="voice-edit-web-panel")
        self._thread.start()
        if not self._ready.wait(timeout=8.0):
            raise WebPanelUnavailable("server_start_timeout",
                                      "loopback server did not start")
        if self._start_error is not None:
            raise WebPanelUnavailable("server_start_failed",
                                      str(self._start_error))
        if not self._running.wait(timeout=2.0):
            raise WebPanelUnavailable("server_not_running",
                                      "loopback server loop did not run")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = self._loop
        if loop is not None and loop.is_running():
            async def _shutdown() -> None:
                for client in list(self._clients):
                    await self._close_client(client)
                if self._runner is not None:
                    await self._runner.cleanup()

            with contextlib.suppress(Exception):
                fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
                fut.result(timeout=3.0)
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        executor = getattr(self, "_action_executor", None)
        if executor is not None:
            with contextlib.suppress(Exception):
                executor.shutdown(wait=False, cancel_futures=True)
            self._action_executor = None
        browser = self._browser
        if browser is not None and browser.ok:
            proc = browser.process
            with contextlib.suppress(Exception):
                if proc is not None:
                    proc.terminate()
                elif browser.pid:
                    os.kill(browser.pid, 15)
        if self._browser is not None and self._browser.user_data_dir:
            with contextlib.suppress(Exception):
                shutil.rmtree(self._browser.user_data_dir, ignore_errors=True)

    # -- HTTP ------------------------------------------------------------
    async def _handle_redirect(self, request: Any) -> Any:
        if not _is_loopback(request.remote):
            raise self._forbidden()
        raise self._web_redirect("/voice-edit/")

    async def _handle_index(self, request: Any) -> Any:
        if not _is_loopback(request.remote):
            raise self._forbidden()
        index = self._static_dir / "index.html"
        if index.is_file():
            return self._file_response(index)
        return self._html_response(PLACEHOLDER_HTML)

    async def _handle_asset(self, request: Any) -> Any:
        if not _is_loopback(request.remote):
            raise self._forbidden()
        raw = str(request.match_info.get("path") or "")
        if not raw:
            raise self._web_redirect("/voice-edit/")
        root = self._static_dir.resolve()
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise self._not_found()
        if not candidate.is_file():
            raise self._not_found()
        return self._file_response(candidate)

    def _file_response(self, path: Path) -> Any:
        from aiohttp import web
        return web.FileResponse(path)

    def _html_response(self, body: str) -> Any:
        from aiohttp import web
        return web.Response(text=body, content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    def _forbidden(self) -> Any:
        from aiohttp import web
        return web.HTTPForbidden(text="forbidden",
                                 headers={"Cache-Control": "no-store"})

    def _not_found(self) -> Any:
        from aiohttp import web
        return web.HTTPNotFound(text="not found")

    def _web_redirect(self, location: str) -> Any:
        from aiohttp import web
        return web.HTTPFound(location)

    # -- websocket -------------------------------------------------------
    def _extract_token(self, request: Any, *, query: bool = True) -> str:
        if query:
            value = str(request.query.get("token") or "")
            if value:
                return value
        header = str(request.headers.get(TOKEN_HEADER) or "")
        if header:
            return header
        auth = str(request.headers.get("Authorization") or "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    async def _handle_ws(self, request: Any) -> Any:
        from aiohttp import WSMsgType, web

        if not _is_loopback(request.remote):
            raise self._forbidden()
        supplied = self._extract_token(request)
        if not supplied or not hmac.compare_digest(
                supplied.encode("utf-8", "ignore"), self._token_bytes):
            raise self._forbidden()

        ws = web.WebSocketResponse(heartbeat=30.0, max_msg_size=256 * 1024)
        await ws.prepare(request)
        snapshot = self._snapshot()
        client = _Client(ws, max_queue=max(self._max_queue, len(snapshot) + 8))
        self._clients.add(client)
        for message in snapshot:
            client.enqueue(message)
        writer = asyncio.create_task(self._writer(client),
                                     name="voice-edit-ws-writer")
        pinger = None
        if self._ping_interval_s > 0:
            pinger = asyncio.create_task(self._pinger(client),
                                         name="voice-edit-ws-ping")
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await self._on_message(client, message.data)
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            if pinger is not None:
                pinger.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pinger
            writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer
            await self._close_client(client)
        return ws

    async def _writer(self, client: _Client) -> None:
        while not client.closed:
            await client.event.wait()
            for message in client.pop_all():
                if client.closed:
                    return
                try:
                    await client.ws.send_str(json.dumps(message,
                                                        separators=(",", ":")))
                except Exception:
                    client.closed = True
                    return
            if client.ws.closed:
                client.closed = True
                return

    async def _pinger(self, client: _Client) -> None:
        while not client.closed:
            await asyncio.sleep(self._ping_interval_s)
            if client.closed:
                return
            client.enqueue({"type": "ping"})

    async def _close_client(self, client: _Client) -> None:
        client.closed = True
        self._clients.discard(client)
        with contextlib.suppress(Exception):
            await client.ws.close()

    async def _on_message(self, client: _Client, raw: Any) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            await self._send_error(client, "bad_json", "message is not JSON")
            return
        if not isinstance(message, dict) or message.get("type") != "action":
            await self._send_error(client, "bad_message", "expected an action")
            return
        action = str(message.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            await self._send_error(client, "bad_action",
                                   f"unknown action: {action[:40]}")
            return
        payload = message.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        try:
            loop = asyncio.get_running_loop()
            executor = self._executor()
            await loop.run_in_executor(executor, self._invoke_action,
                                       action, payload)
        except Exception as exc:
            await self._send_error(client, "action_failed", str(exc))

    def _executor(self) -> Any:
        executor = getattr(self, "_action_executor", None)
        if executor is None:
            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(max_workers=1,
                                          thread_name_prefix="voice-edit-action")
            self._action_executor = executor
        return executor

    def _invoke_action(self, action: str, payload: Dict[str, Any]) -> None:
        self._on_action(action, dict(payload))

    async def _send_error(self, client: _Client, code: str, detail: str) -> None:
        client.enqueue(self._error_message(code, detail))

    # -- snapshot / fanout ----------------------------------------------
    def _snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            messages: List[Dict[str, Any]] = [{
                "type": "hello", "version": VERSION, "state": self._state,
            }]
            if self._detail or self._state != "idle":
                messages.append(self._status_message(self._state, self._detail,
                                                     self._level))
            if self._partial:
                messages.append({"type": "partial",
                                 "text": self._partial,
                                 "final": self._partial_final})
            if self._preview is not None:
                messages.append({"type": "preview",
                                 "payload": dict(self._preview)})
            if self._level > 0:
                messages.append({"type": "level", "value": self._level})
            messages.extend(dict(item) for item in self._history)
        return [self._scrub_message(message) for message in messages]

    def _fanout(self, message: Dict[str, Any]) -> None:
        for client in list(self._clients):
            if not client.closed:
                client.enqueue(message)

    def _publish(self, message: Dict[str, Any]) -> None:
        if self._closed:
            return
        message = self._scrub_message(message)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._fanout, message)

    def _status_message(self, state: str, detail: str, level: float
                        ) -> Dict[str, Any]:
        return {"type": "status", "state": state, "detail": detail,
                "level": _clamp_level(level)}

    def _error_message(self, code: str, detail: str) -> Dict[str, Any]:
        return {"type": "error", "code": _bounded_text(code, 120),
                "detail": _bounded_text(detail, 500)}

    def _scrub(self, value: str) -> str:
        token = getattr(self, "token", "")
        if token and token in value:
            return value.replace(token, "***")
        return value

    def _scrub_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        scrubbed: Dict[str, Any] = {}
        for key, value in message.items():
            if isinstance(value, str):
                scrubbed[key] = self._scrub(value)
            elif isinstance(value, dict):
                scrubbed[key] = self._scrub_message(value)
            elif isinstance(value, list):
                scrubbed[key] = [
                    self._scrub(v) if isinstance(v, str)
                    else self._scrub_message(v) if isinstance(v, dict)
                    else v for v in value
                ]
            else:
                scrubbed[key] = value
        return scrubbed

    def _normalize_preview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        detected = payload.get("detected") or {}
        if not isinstance(detected, dict):
            detected = {}
        elapsed = payload.get("elapsed_ms", payload.get("latency_ms"))
        try:
            elapsed_ms: Optional[int] = int(elapsed) if elapsed is not None else None
        except (TypeError, ValueError):
            elapsed_ms = None
        dropped = payload.get("facts_dropped") or []
        if not isinstance(dropped, (list, tuple)):
            dropped = [dropped]
        return {
            "original": _bounded_text(payload.get("original", "")),
            "proposed": _bounded_text(payload.get("proposed", "")),
            "detected": {
                "action": _bounded_text(detected.get("action", ""), 120),
                "style": _bounded_text(detected.get("style", ""), 120),
                "language": _bounded_text(detected.get("language", ""), 64),
            },
            "model": _bounded_text(payload.get("model", ""), 200),
            "provider": _bounded_text(payload.get("provider", ""), 200),
            "privacy_mode": _bounded_text(payload.get("privacy_mode", ""), 120),
            "locality": _bounded_text(payload.get("locality", ""), 120),
            "target_app": _bounded_text(payload.get("target_app", ""), 200),
            "elapsed_ms": elapsed_ms,
            "injection_suspected": bool(payload.get("injection_suspected")),
            "clarification_returned": bool(
                payload.get("clarification_returned")),
            "facts_dropped": [_bounded_text(item, 200) for item in dropped[:20]],
        }

    # -- Tk overlay API --------------------------------------------------
    def set_status(self, state: str, detail: str = "") -> None:
        state = str(state or "idle")
        detail = _bounded_text(detail, 500)
        with self._lock:
            self._state = state
            self._detail = detail
            level = self._level
        self._publish(self._status_message(state, detail, level))

    def set_live_transcript(self, text: str, final: bool = False) -> None:
        value = _bounded_text(text)
        with self._lock:
            self._partial = value
            self._partial_final = bool(final)
        self._publish({"type": "partial", "text": value, "final": bool(final)})

    def set_level(self, level: float) -> None:
        value = _clamp_level(level)
        with self._lock:
            self._level = value
        self._publish({"type": "level", "value": value})

    def show_preview(self, payload: Dict[str, Any]) -> None:
        normalized = self._normalize_preview(payload)
        with self._lock:
            self._preview = normalized
            self._state = "preview"
        self._visible = True
        self._publish({"type": "preview", "payload": normalized})

    def append_history_entry(self, kind: str, heard: str, result: str,
                             ts: str = "") -> None:
        entry = {
            "type": "history",
            "kind": _bounded_text(kind or "turn", 40),
            "heard": _bounded_text(heard, 400),
            "result": _bounded_text(result, 1000),
            "ts": _bounded_text(ts or time.strftime("%H:%M:%S"), 32),
        }
        with self._lock:
            self._history.append(entry)
        self._publish(dict(entry))

    def append_history(self, line: str) -> None:
        self.append_history_entry("history", line, "", time.strftime("%H:%M:%S"))

    def show_error(self, code: str, detail: str) -> None:
        with self._lock:
            self._state = "error"
            self._detail = _bounded_text(detail, 500)
        self._visible = True
        self._publish(self._error_message(code, detail))

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def __del__(self) -> None:  # pragma: no cover - best-effort teardown
        if getattr(self, "_closed", True):
            return
        with contextlib.suppress(Exception):
            self.close()


def _default_static_dir() -> Path:
    return (Path(__file__).resolve().parents[2]
            / "frontend" / "voice-edit-hud" / "out")


__all__ = [
    "ALLOWED_ACTIONS",
    "LaunchResult",
    "WebPanelOverlay",
    "WebPanelUnavailable",
    "build_browser_argv",
    "find_browser",
    "launch_app_window",
    "resolve_panel_backend",
]
