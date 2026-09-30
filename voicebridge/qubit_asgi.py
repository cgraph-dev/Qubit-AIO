"""In-process ASGI bridge to the Qubit-owned Qubit backend.

Cutover stages 1-3 (docs/archive/2026-09-21/QUBIT_HERMES_CUTOVER_MIGRATION.md). The
single-listener rule holds: the Qubit ``WebUI`` HTTP server remains the ONLY
listening socket. This bridge calls the vendored FastAPI app in-process via an
``httpx`` ``ASGITransport`` on a dedicated event-loop thread, so the fork
becomes the backend authority for dashboard reads without opening a second port
or supervising an external process. Stage 3 adds a fail-closed write-policy
middleware (``write_policy`` + ``safe_write_to_proxy``) that runs the Qubit
e-stop/gate/audit check before any explicitly allowlisted mutation reaches the
fork.

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
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple

from . import qubit_egress_guard, qubit_runtime

# Reads that must never be proxied straight to the upstream backend: they are
# secrets/identity/raw-config/confinement surfaces Qubit gates explicitly. The
# Qubit compatibility layer answers these with its typed refusal or its own
# confined handler.
#
# ``/api/config/raw`` (the raw config dump only) stays on the Qubit handler; the
# non-raw ``GET /api/config`` is a non-gated read, so it is proxy-first to the
# fork and redacted/override-merged at the served boundary (webui._hermes_api).
# ``/auth`` covers the fork's login/callback/native endpoints that are not under
# ``/api/auth``; ``/api/fs`` and ``/api/files`` keep filesystem reads inside the
# Qubit project-root confinement instead of the fork's unrestricted reader
# (audit ``finding.config_proxied``/``finding.auth_prefix_bypass``/
# ``finding.fs_confinement_bypass``).
SENSITIVE_READ_PREFIXES = (
    # True secret / identity / confinement surfaces only. These never proxy to
    # the fork because the fork would either return raw secrets or escape the
    # Qubit confinement roots.
    "/api/config/raw",
    "/api/secrets",
    "/api/keys",
    "/api/auth",
    "/auth",
    "/api/oauth",
    "/api/mcp/oauth",
    # Qubit compatibility gate: these carry Qubit-owned semantics.
    "/api/fs",
    "/api/files",
)

# Qubit-owned dashboard surfaces: the fork also has these paths, but Qubit's
# own theme/overlay-plugin/ops endpoints must stay authoritative so the active
# dashboard keeps the Qubit tabs and branding (owner rebrand requirement).
# The vendored dashboard plugins are Qubit-owned too: Qubit's compat handlers
# call the real vendored engines but rebrand the served payload, and the kanban
# adapter reads the authoritative Qubit-native store (single-store rule).
QUBIT_OWNED_PREFIXES = (
    "/api/dashboard",
    "/api/qubit",
    "/api/ops",
    "/api/plugins/qubit-achievements",
    "/api/plugins/kanban",
)

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_client: Any = None
_token: str = ""
# The header name the vendored fork's HTTP server authenticates with. Read from
# the fork (not hardcoded) so the proxy always matches the fork's own contract
# after a rename — a mismatch made every proxied read (config/analytics/logs)
# answer 401 and storm the dashboard.
_session_header: str = ""
_route_res: List[Pattern[str]] = []
_start_error: Optional[BaseException] = None
_ready = threading.Event()


_overlay_applied = False


def _apply_qubit_config_overlay() -> None:
    """Qubit-owned overlay on the vendored runtime (no upstream file edit).

    The pinned fork's default config names its canonical toolset after the
    upstream product. Qubit keeps the internal id working but presents a
    Qubit-owned default: register ``qubit-cli`` as an alias of the same toolset
    bundle and make it the default rendered by the dashboard.
    """
    global _overlay_applied
    if _overlay_applied:
        return
    _overlay_applied = True
    try:
        from . import qubit_runtime as _HR
        _HR.ensure_path()
        from qubit_cli import config_defaults as _CD
        if _CD.DEFAULT_CONFIG.get("toolsets") == ["hermes-cli"]:
            _CD.DEFAULT_CONFIG["toolsets"] = ["qubit-cli"]
        try:
            import toolsets as _ts
            # B4-07: the string rebrand produced a capital-Q name ("Qubit-cli")
            # in live configs while the overlay only aliased lowercase
            # "qubit-cli", so the id silently resolved to 0 tools. Register
            # every spelling (the resolver is exact-name, not case-folded) and
            # keep ``hermes-cli`` itself as the read-old source.
            bundle = _ts.TOOLSETS.get("hermes-cli")
            if bundle is not None:
                for _alias in ("qubit-cli", "Qubit-cli", "QUBIT-cli"):
                    _ts.TOOLSETS.setdefault(_alias, bundle)
        except Exception:
            pass
    except Exception:
        pass


def set_home(path: str) -> None:
    """Point the fork at a Qubit-owned runtime home before the bridge starts.

    No-op once the bridge has started (the root is snapshotted at import)."""
    _apply_qubit_config_overlay()
    if _client is not None or os.environ.get("QUBIT_HOME"):
        return
    os.environ["QUBIT_HOME"] = str(path)


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
        qubit_runtime.ensure_path()
        # The vendored backend must use a Qubit-owned QUBIT_HOME, never the
        # operator's real ~/.hermes (single-authority + Qubit' own
        # live-system guard). Set before importing web_server, which snapshots
        # the root at import time. Shared resolver with ``qubit_runtime.run``
        # so CLI and dashboard read the exact same home.
        qubit_runtime.use_qubit_home()
        import httpx
        from qubit_cli import web_server as ws  # type: ignore

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
        global _session_header
        _session_header = str(
            getattr(ws, "_SESSION_HEADER_NAME", "")
            or "X-Qubit-Session-Token")
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
                                  name="qubit-runtime-asgi", daemon=True)
        thread.start()
        _loop = loop
        _thread = thread
    _ready.wait(timeout=30)


async def _send(method: str, path: str, query: Dict[str, str],
                headers: Dict[str, str], body: bytes):
    hdrs = dict(headers or {})
    if _token:
        hdrs.setdefault(_session_header or "X-Qubit-Session-Token", _token)
    req = _client.build_request(method, path, params=query or None,
                                headers=hdrs or None, content=body)
    # Stage 2b: the fork may not make unrestricted outbound provider calls
    # while answering a Qubit read. Deny-by-default, audited, loopback-only.
    with qubit_egress_guard.fork_scope():
        return await _client.send(req)


def available() -> bool:
    _ensure_started()
    return _client is not None


def status() -> Dict[str, Any]:
    """Read-only bridge status (never raises)."""
    out: Dict[str, Any] = {
        "bridge": "qubit-runtime-asgi",
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
            f"qubit backend bridge unavailable: {_start_error!r}"[:200])
    assert _loop is not None
    fut = asyncio.run_coroutine_threadsafe(
        _send(method, path, query or {}, headers or {}, body), _loop)
    resp = fut.result(timeout=timeout)
    return resp.status_code, dict(resp.headers), resp.content


_gated_res: Optional[List[Pattern[str]]] = None


def _clean_read_path(path: str) -> str:
    """Query-stripped, trailing-slash-normalized path (matches ``dispatch``)."""
    clean = path.split("?", 1)[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    return clean


def _prefix_gated(clean: str) -> bool:
    for prefix in SENSITIVE_READ_PREFIXES + QUBIT_OWNED_PREFIXES:
        if clean == prefix or clean.startswith(prefix + "/"):
            return True
    return False


def _gated_patterns() -> List[Pattern[str]]:
    """Compiled GET/HEAD patterns from the authoritative Qubit gate table.

    Every route Qubit deliberately gates (``qubit_compat.GATED``) must never be
    proxied to the fork, even when the fork also declares it. Deriving the set
    from the gate table means a new gate cannot accidentally become a cutover
    bypass. This set is deliberately GATE-only: it is also the write path's
    defense-in-depth no-proxy check, which must not be widened by a read route
    that merely happens to share the path."""
    global _gated_res
    if _gated_res is None:
        try:
            from . import qubit_compat as HC
            _gated_res = [re.compile(p) for m, p, _g in HC.GATED
                          if m.upper() in ("GET", "HEAD")]
        except Exception:
            _gated_res = []
    return _gated_res


_owned_read_res: Optional[List[Pattern[str]]] = None


def _owned_read_patterns() -> List[Pattern[str]]:
    """Compiled GET/HEAD patterns for every route Qubit implements itself.

    Historic telemetry only: the retired proxy-first rule consulted this set and
    shadowed 78 Qubit compat GET handlers (audit
    ``finding.fork_shadow_78_get``). ``gated_path`` deliberately does NOT consult
    it now -- non-gated reads are proxy-first to the fork -- so it is kept only
    for tests/telemetry and must not be re-attached to the gate decision. The
    narrow ``webui.QUBIT_READ_AUTHORITY`` exempt list is the only per-route
    override for a handler whose store would otherwise split-brain."""
    global _owned_read_res
    if _owned_read_res is None:
        res: List[Pattern[str]] = []
        try:
            from . import qubit_compat as HC
            for route in HC.ROUTES:
                if str(route.get("method", "")).upper() not in ("GET", "HEAD"):
                    continue
                if route.get("disposition") != "implemented":
                    continue
                try:
                    res.append(re.compile(str(route.get("pattern", ""))))
                except re.error:
                    continue
        except Exception:
            res = []
        _owned_read_res = res
    return _owned_read_res


def _matches(res: List[Pattern[str]], clean: str) -> bool:
    return any(rx.fullmatch(clean) for rx in res)


def gated_path(path: str) -> bool:
    """True when a read path must stay on the Qubit compat/gate path.

    Pure policy check (no bridge startup): sensitive read prefixes,
    Qubit-owned dashboard surfaces, and every GET/HEAD Qubit-gated route are
    never proxied; every other read the fork declares is proxy-first (callers
    may additionally honor the narrow ``webui.QUBIT_READ_AUTHORITY`` exempt
    list). A trailing slash is normalized the same way
    ``qubit_compat.dispatch`` normalizes, so a shimmed path cannot slip past
    the gate into the fork."""
    clean = _clean_read_path(path)
    # Sensitive/confined surfaces, Qubit-owned dashboard surfaces, and Qubit's
    # explicit gate table stay on the Qubit compat path. Every other read the
    # fork declares is served by the vendored Qubit runtime (the Qubit-owned
    # foundation per the owner amendment) so the preserved Qubit dashboard
    # keeps its real data; a Qubit compat handler that is merely a thin/empty
    # stub no longer shadows a working fork reader. ``_owned_read_patterns``
    # remains available for tests/telemetry but is deliberately NOT part of the
    # no-proxy decision.
    return _prefix_gated(clean) or _matches(_gated_patterns(), clean)


def known_route(path: str) -> bool:
    """True when the fork's OpenAPI actually declares this concrete path."""
    _ensure_started()
    clean = path.split("?", 1)[0]
    return any(rx.match(clean) for rx in _route_res)


def safe_to_proxy(method: str, path: str) -> bool:
    """True only for a non-mutating read the fork serves and Qubit does not gate.

    Stage 1/2 proxy reads only, and proxy-first for every non-gated read the
    fork declares. Writes/mutations always stay on the Qubit
    permissions/confirmation/audit path. Sensitive/Qubit-owned prefixes and
    every GET/HEAD Qubit-gated route are never proxied, and the path must also
    exist in the fork's own route table. The caller (``webui._hermes_api``)
    applies the ``QUBIT_READ_AUTHORITY`` exempt list before this check.
    """
    if method.upper() not in ("GET", "HEAD"):
        return False
    if gated_path(path):
        return False
    return known_route(path)


# ---------------------------------------------------------------------------
# Cutover stage 3: Qubit write-policy middleware for fork writes
# ---------------------------------------------------------------------------
#: Operator opt-in allowlist of ``(METHOD, path)`` fork writes that may execute
#: in the fork *after* the Qubit policy middleware has run. It is deliberately
#: EMPTY by default, so enabling ``webui.hermes_writes`` alone never exposes a
#: fork write: each route is added one at a time only after its Qubit
#: authority/parity is proven. This is the reversible next stage recorded in
#: docs/archive/2026-09-21/QUBIT_HERMES_CUTOVER_MIGRATION.md.
WRITE_PROXY_ALLOWLIST: Set[Tuple[str, str]] = set()

_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _sensitive_path(path: str) -> bool:
    clean = path.split("?", 1)[0]
    for prefix in SENSITIVE_READ_PREFIXES + QUBIT_OWNED_PREFIXES:
        if clean == prefix or clean.startswith(prefix + "/"):
            return True
    return False


def safe_write_to_proxy(method: str, path: str) -> bool:
    """True only for an explicitly allowlisted Qubit-policy-cleared fork write.

    Fail-closed: the route must be in ``WRITE_PROXY_ALLOWLIST``, must not be a
    sensitive/Qubit-owned surface, must not be Qubit-gated, and must be declared
    by the fork's OpenAPI. A write that is not allowlisted stays on the Qubit
    compat path (``webui._hermes_api`` falls through to it).
    """
    method = method.upper()
    if method not in _MUTATING_METHODS:
        return False
    clean = path.split("?", 1)[0]
    if (method, clean) not in WRITE_PROXY_ALLOWLIST:
        return False
    # Gate-only no-proxy check: a route that merely has a Qubit *read* handler
    # (same path, different verb) does not block an explicitly allowlisted
    # write, but a Qubit gate always does.
    if _sensitive_path(clean) or _matches(_gated_patterns(),
                                          _clean_read_path(clean)):
        return False
    # A route Qubit deliberately gates for this verb is never fork-written, even
    # if allowlisted (defense in depth; write_policy normally refuses first).
    try:
        from . import qubit_compat as HC
        route, _ = HC.match(method, clean)
        if route is not None and route.get("disposition") == "gated":
            return False
    except Exception:
        pass
    return known_route(clean)


def write_policy(cfg: Any, method: str, path: str
                 ) -> Tuple[bool, Optional[Tuple[int, Dict[str, Any]]]]:
    """Qubit permission/confirmation/audit check for a candidate fork write.

    Returns ``(allowed, refusal)``:

    * ``(False, None)`` -- not an allowlisted cutover write; the caller must
      use the Qubit compat path (default behavior).
    * ``(False, (http, envelope))`` -- allowlisted but refused by Qubit policy
      (emergency stop engaged or a typed compat gate). The refusal is audited
      and the fork is never invoked.
    * ``(True, None)`` -- allowlisted and policy-cleared; the caller may proxy
      the write to the fork under the egress guard, and must audit it.
    """
    method = method.upper()
    clean = path.split("?", 1)[0]
    if method not in _MUTATING_METHODS or \
            (method, clean) not in WRITE_PROXY_ALLOWLIST:
        return False, None
    # One emergency-stop authority: an engaged Qubit e-stop refuses everything.
    try:
        from . import broker as _broker
        if _broker.estop_engaged(cfg):
            from . import qubit_compat as HC
            HC.audit_event("hermes.write.refused", "bridge",
                           {"route": f"{method} {clean}", "code": "ESTOP_ENGAGED"})
            return False, (423, {
                "ok": False, "state": "UNAVAILABLE", "code": "ESTOP_ENGAGED",
                "detail": "Qubit emergency stop is engaged; writes are refused.",
                "qubit_counterpart": "CLI `qubit resume`"})
    except Exception:
        pass
    # A Qubit-typed gate is never converted into a fork write bypass.
    try:
        from . import qubit_compat as HC
        route, _ = HC.match(method, clean)
        if route is not None and route.get("disposition") == "gated":
            gate = route.get("gate", {})
            HC.audit_event("hermes.write.refused", "bridge",
                           {"route": f"{method} {clean}",
                            "code": str(gate.get("code", ""))})
            return False, (gate.get("http", 403), {
                "ok": False, "state": "UNAVAILABLE",
                "code": gate.get("code"),
                "detail": gate.get("detail"),
                "qubit_counterpart": gate.get("counterpart"),
                "owner": gate.get("owner"),
                "rollback": gate.get("rollback")})
    except Exception:
        pass
    return True, None


__all__ = ["available", "status", "call", "safe_to_proxy", "known_route",
           "gated_path", "set_home", "SENSITIVE_READ_PREFIXES",
           "QUBIT_OWNED_PREFIXES", "WRITE_PROXY_ALLOWLIST",
           "safe_write_to_proxy", "write_policy"]
