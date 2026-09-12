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
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Tuple

from . import hermes_egress_guard, hermes_runtime

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

# Qubit-owned dashboard surfaces: the fork also has these paths, but Qubit's
# own theme/overlay-plugin/ops endpoints must stay authoritative so the active
# dashboard keeps the Qubit tabs and branding (owner rebrand requirement).
QUBIT_OWNED_PREFIXES = (
    "/api/dashboard",
    "/api/qubit",
    "/api/ops",
)

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_client: Any = None
_token: str = ""
_route_res: List[Pattern[str]] = []
_start_error: Optional[BaseException] = None
_ready = threading.Event()


def set_home(path: str) -> None:
    """Point the fork at a Qubit-owned HERMES_HOME before the bridge starts.

    No-op once the bridge has started (the root is snapshotted at import)."""
    if _client is not None or os.environ.get("HERMES_HOME"):
        return
    os.environ["HERMES_HOME"] = str(path)


def _default_hermes_home() -> str:
    """Qubit-owned state root for the vendored backend (never ~/.hermes)."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "qubit-voicebridge", "hermes")


def _template_re(template: str) -> Pattern[str]:
    """Compile a FastAPI/OpenAPI path template into a concrete-path regex."""
    parts = []
    for seg in template.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            inner = seg[1:-1]
            if inner.endswith(":path"):
                parts.append(".+")
            else:
                parts.append("[^/]+")
        else:
            parts.append(re.escape(seg))
    return re.compile("^" + "/".join(parts) + "$")


async def _startup() -> None:
    global _client, _start_error, _token
    try:
        hermes_runtime.ensure_path()
        # The vendored backend must use a Qubit-owned HERMES_HOME, never the
        # operator's real ~/.hermes (single-authority + Hermes' own
        # live-system guard). Set before importing web_server, which snapshots
        # the root at import time.
        if not os.environ.get("HERMES_HOME"):
            home = _default_hermes_home()
            Path(home).mkdir(parents=True, exist_ok=True)
            os.environ["HERMES_HOME"] = home
        import httpx
        from hermes_cli import web_server as ws  # type: ignore

        # The fork inserts its own root at sys.path[0] on import, which would
        # shadow same-named top-level packages in the Qubit checkout (notably
        # ``tests``). Keep the Qubit repo root first; the fork root stays on the
        # path for its lazy imports.
        import sys
        repo = str(Path(__file__).resolve().parents[1])
        if repo in sys.path:
            sys.path.remove(repo)
        sys.path.insert(0, repo)

        _token = str(getattr(ws, "_SESSION_TOKEN", "") or "")
        transport = httpx.ASGITransport(app=ws.app)
        _client = httpx.AsyncClient(transport=transport,
                                    base_url="http://hermes.internal")
        # Route membership: only proxy paths the fork actually serves. Its auth
        # gate 401s unknown /api/* paths, so membership must be checked before
        # forwarding or Qubit-specific routes would be swallowed.
        try:
            r = await _client.get("/openapi.json")
            paths = (json.loads(r.content) or {}).get("paths", {})
            _route_res.extend(_template_re(p) for p in paths)
        except Exception:
            pass
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
    hdrs = dict(headers or {})
    if _token:
        hdrs.setdefault("X-Hermes-Session-Token", _token)
    req = _client.build_request(method, path, params=query or None,
                                headers=hdrs or None, content=body)
    # Stage 2b: the fork may not make unrestricted outbound provider calls
    # while answering a Qubit read. Deny-by-default, audited, loopback-only.
    with hermes_egress_guard.fork_scope():
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


_gated_res: Optional[List[Pattern[str]]] = None


def _gated_patterns() -> List[Pattern[str]]:
    """Compiled GET patterns from the authoritative Qubit gate table.

    Every route Qubit deliberately gates (``hermes_compat.GATED``) must never
    be proxied to the fork, even when the fork also declares it. Deriving the
    set from the gate table means a new gate cannot accidentally become a
    cutover bypass."""
    global _gated_res
    if _gated_res is None:
        try:
            from . import hermes_compat as HC
            _gated_res = [re.compile(p) for m, p, _g in HC.GATED
                          if m.upper() in ("GET", "HEAD")]
        except Exception:
            _gated_res = []
    return _gated_res


def gated_path(path: str) -> bool:
    """True when a read path must stay on the Qubit compat/gate path.

    Pure policy check (no bridge startup): sensitive read prefixes,
    Qubit-owned dashboard surfaces, and every Qubit-gated route are never
    proxied."""
    clean = path.split("?", 1)[0]
    for prefix in SENSITIVE_READ_PREFIXES + QUBIT_OWNED_PREFIXES:
        if clean == prefix or clean.startswith(prefix + "/"):
            return True
    return any(rx.fullmatch(clean) for rx in _gated_patterns())


def known_route(path: str) -> bool:
    """True when the fork's OpenAPI actually declares this concrete path."""
    _ensure_started()
    clean = path.split("?", 1)[0]
    return any(rx.match(clean) for rx in _route_res)


def safe_to_proxy(method: str, path: str) -> bool:
    """True only for a non-mutating read the fork serves and Qubit does not gate.

    Stage 1/2 proxy reads only. Writes/mutations always stay on the Qubit
    permissions/confirmation/audit path. Sensitive read prefixes are never
    proxied, and the path must exist in the fork's own route table.
    """
    if method.upper() not in ("GET", "HEAD"):
        return False
    if gated_path(path):
        return False
    return known_route(path)


__all__ = ["available", "status", "call", "safe_to_proxy", "known_route",
           "gated_path", "set_home", "SENSITIVE_READ_PREFIXES",
           "QUBIT_OWNED_PREFIXES"]
