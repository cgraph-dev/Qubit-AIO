"""In-process ASGI bridge to the Qubit-owned Hermes backend.

Cutover stage 1 (docs/QUBIT_HERMES_CUTOVER_MIGRATION.md). The single-listener
rule holds: the Qubit ``WebUI`` HTTP server remains the ONLY listening socket.
This bridge calls the vendored FastAPI app in-process via an ``httpx``
``ASGITransport`` on a dedicated event-loop thread, so the fork becomes the
backend authority for dashboard reads without opening a second port or
supervising an external process.

The fork app is used as-is (no lifespan): its startup would spawn gateway/cron
services, violating single-authority/single-listener. Routes that need startup
state return their own status and the caller falls back to the Qubit
compatibility layer for 404/405 only.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, Optional, Tuple

from . import hermes_runtime

# Reads that must never be proxied straight to the upstream backend: they are
# secrets/identity/raw-config surfaces Qubit gates explicitly. The Qubit
# compatibility layer answers these with its typed refusal.
SENSITIVE_READ_PREFIXES = (
    "/api/config/raw",
    "/api/env",
    "/api/credentials",
    "/api/secrets",
    "/api/keys",
    "/api/auth",
    "/api/oauth",
    "/api/mcp/oauth",
    "/api/portal",
    "/api/backup",
    "/api/import",
    "/api/export",
)

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_client: Any = None
_start_error: Optional[BaseException] = None
_ready = threading.Event()


async def _startup() -> None:
    global _client, _start_error
    try:
        hermes_runtime.ensure_path()
        import httpx
        from hermes_cli import web_server as ws  # type: ignore

        transport = httpx.ASGITransport(app=ws.app)
        _client = httpx.AsyncClient(transport=transport,
                                    base_url="http://hermes.internal")
    except BaseException as exc:  # noqa: BLE001 - recorded, surfaced by status()
        _start_error = exc


def _runner(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_startup())
    _ready.set()
    loop.run_forever()


def _ensure_started() -> None:
    global _loop, _thread
    with _lock:
        if _loop is not None:
            return
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=_runner, args=(loop,),
                                  name="qubit-hermes-asgi", daemon=True)
        thread.start()
        _loop = loop
        _thread = thread
    _ready.wait(timeout=30)


async def _send(method: str, path: str, query: Dict[str, str],
                headers: Dict[str, str], body: bytes):
    req = _client.build_request(method, path, params=query or None,
                                headers=headers or None, content=body)
    return await _client.send(req)


def available() -> bool:
    _ensure_started()
    return _client is not None


def status() -> Dict[str, Any]:
    """Read-only bridge status (never raises)."""
    out: Dict[str, Any] = {
        "bridge": "qubit-hermes-asgi",
        "single_listener": True,
        "available": False,
        "starts_second_server": False,
    }
    try:
        _ensure_started()
        out["available"] = _client is not None
        if _start_error is not None:
            out["error"] = f"{type(_start_error).__name__}: {_start_error}"[:200]
    except Exception as exc:  # pragma: no cover
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def call(method: str, path: str, *, query: Optional[Dict[str, str]] = None,
         headers: Optional[Dict[str, str]] = None,
         body: bytes = b"", timeout: float = 30.0
         ) -> Tuple[int, Dict[str, str], bytes]:
    """Invoke the vendored backend in-process. Returns (status, headers, body)."""
    _ensure_started()
    if _client is None:
        raise RuntimeError(
            f"hermes backend bridge unavailable: {_start_error!r}"[:200])
    assert _loop is not None
    fut = asyncio.run_coroutine_threadsafe(
        _send(method, path, query or {}, headers or {}, body), _loop)
    resp = fut.result(timeout=timeout)
    return resp.status_code, dict(resp.headers), resp.content


def safe_to_proxy(method: str, path: str) -> bool:
    """True only for a non-mutating read that is not a Qubit-gated surface.

    Stage 1 proxies reads only. Writes/mutations always stay on the Qubit
    permissions/confirmation/audit path.
    """
    if method.upper() not in ("GET", "HEAD"):
        return False
    clean = path.split("?", 1)[0]
    for prefix in SENSITIVE_READ_PREFIXES:
        if clean == prefix or clean.startswith(prefix + "/"):
            return False
    return True


__all__ = ["available", "status", "call", "safe_to_proxy",
           "SENSITIVE_READ_PREFIXES"]
