"""Single-listener WebSocket PTY for the Qubit terminal chat (`/chat`).

Owner decision 2026-09-17: `/chat` renders the real Qubit terminal. This module
adds ONE WebSocket upgrade path (`/agent/api/pty`) to the existing Qubit
listener — no second listener, no separate service.

Security contract:

* Auth is a short-lived (30s), single-use, scoped ticket minted over the
  authenticated HTTP API (``POST /agent/api/auth/ws-ticket``). The long-lived
  API/session token is NEVER accepted on the WS query string or subprotocol.
* The upgrade validates origin host, loopback peer, ticket scope/session/owner/
  target, and the configured bind interface before the handshake completes.
* The broker emergency stop is consulted before spawning the PTY.
* Limits: bounded concurrent PTYs, idle timeout, bounded frame/read size.
* Cleanup: the PTY child is killed on close, error, stall, e-stop, or shutdown.
* Token material is redacted from logs/receipts/errors and never echoed back.

Protocol (matches the Qubit frontend): client -> server raw bytes are PTY input
except the resize escape ``\\x1b[RESIZE:<cols>;<rows>]``; server -> client frames
are raw PTY output bytes.
"""
from __future__ import annotations

import base64
import hashlib
import asyncio
import os
import re
import secrets
import select
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .logging_setup import log_event
from .redaction import redact_text

# RFC 6455 handshake GUID.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")

SCOPE_PTY = "pty"


class TerminalContextError(Exception):
    """Typed /chat profile failure raised before the real PTY is spawned."""

    def __init__(self, guard: Dict[str, Any]) -> None:
        super().__init__(str(guard.get("detail") or guard.get("code") or ""))
        self.code = str(guard.get("code") or "TERMINAL_CONTEXT_BELOW_MINIMUM")
        self.detail = str(guard.get("detail") or "")

TICKET_TTL_S = 30.0
MAX_SESSIONS = 4
IDLE_TIMEOUT_S = 30 * 60.0
MAX_FRAME_BYTES = 1 << 20          # 1 MiB inbound frame cap
PTY_READ_TIMEOUT = 0.2
PTY_READ_CHUNK = 65536
POLL_TIMEOUT = 0.05
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

# WebSocket opcodes.
_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BIN = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


def _redact(value: Any) -> str:
    try:
        return redact_text(str(value))
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# Short-lived, single-use, scoped tickets
# ---------------------------------------------------------------------------
@dataclass
class _Ticket:
    scope: str
    session: str
    owner: str
    target: str
    expires_at: float
    used: bool = False


class TicketStore:
    """Thread-safe, single-use, TTL-bounded WebSocket ticket authority."""

    def __init__(self, ttl_s: float = TICKET_TTL_S) -> None:
        self._ttl = float(ttl_s)
        self._lock = threading.Lock()
        self._tickets: Dict[str, _Ticket] = {}

    def mint(self, *, scope: str, session: str = "", owner: str = "",
             target: str = "") -> Tuple[str, float]:
        ticket = secrets.token_urlsafe(32)
        exp = time.monotonic() + self._ttl
        with self._lock:
            self._prune()
            self._tickets[ticket] = _Ticket(scope, session, owner, target, exp)
        return ticket, self._ttl

    def consume(self, ticket: str, *, scope: str) -> Optional[_Ticket]:
        if not ticket:
            return None
        with self._lock:
            self._prune()
            rec = self._tickets.pop(ticket, None)
        if rec is None or rec.used or rec.scope != scope:
            return None
        rec.used = True
        if time.monotonic() > rec.expires_at:
            return None
        return rec

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self._tickets.items() if v.expires_at < now]
        for k in stale:
            self._tickets.pop(k, None)


# ---------------------------------------------------------------------------
# WebSocket framing (RFC 6455)
# ---------------------------------------------------------------------------
class WsProtocolError(Exception):
    pass


def _accept_key(sec_key: str) -> str:
    digest = hashlib.sha1((sec_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake(handler: Any) -> None:
    """Complete the server side of the WebSocket upgrade on ``handler``.

    The status line is written as HTTP/1.1 explicitly: the listener defaults to
    HTTP/1.0, and RFC 6455 clients reject a 101 upgrade on HTTP/1.0.
    """
    key = handler.headers.get("Sec-WebSocket-Key", "")
    if not key:
        raise WsProtocolError("missing Sec-WebSocket-Key")
    handler.wfile.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        + f"Sec-WebSocket-Accept: {_accept_key(key)}\r\n".encode("ascii")
        + b"\r\n")
    try:
        handler.wfile.flush()
    except Exception:
        pass


def _recv_exact(sock: Any, n: int) -> Optional[bytes]:
    buf = b""
    deadline = time.monotonic() + 2.0
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (TimeoutError, BlockingIOError):
            # A short read timeout mid-frame is normal on a fragmented TCP
            # segment; retry briefly rather than dropping the connection.
            if time.monotonic() > deadline:
                return None
            continue
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def read_frame(sock: Any) -> Optional[Tuple[int, bytes]]:
    """Read one complete (possibly fragmented) message; None on close/EOF.

    Returns ``(opcode, payload)`` where opcode is the message's first-frame
    opcode (CONT texts are joined). Raises :class:`WsProtocolError` on a
    protocol violation (e.g. an unmasked client frame).
    """
    msg_op: Optional[int] = None
    payload = b""
    while True:
        head = _recv_exact(sock, 2)
        if head is None:
            return None
        b1, b2 = head[0], head[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            ext = _recv_exact(sock, 2)
            if ext is None:
                return None
            length = struct.unpack("!H", ext)[0]
        elif length == 127:
            ext = _recv_exact(sock, 8)
            if ext is None:
                return None
            length = struct.unpack("!Q", ext)[0]
        if length > MAX_FRAME_BYTES:
            raise WsProtocolError("frame_too_large")
        if not masked:
            raise WsProtocolError("unmasked_client_frame")
        mask = _recv_exact(sock, 4)
        if mask is None:
            return None
        data = _recv_exact(sock, length) if length else b""
        if data is None:
            return None
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        if opcode in (_OP_CLOSE, _OP_PING, _OP_PONG):
            return opcode, data
        if opcode == _OP_CONT:
            payload += data
        else:
            if msg_op is not None:
                # New data frame before FIN on the previous one is invalid, but
                # tolerate by treating the new frame as the message start.
                payload = b""
            msg_op = opcode
            payload = data
        if fin:
            return (msg_op if msg_op is not None else _OP_BIN), payload


def write_frame(sock: Any, opcode: int, payload: bytes = b"") -> bool:
    head = bytes([0x80 | (opcode & 0x0F)])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < (1 << 16):
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    try:
        sock.sendall(head + payload)
        return True
    except OSError:
        return False


def close_frame(sock: Any, code: int = 1000, reason: str = "") -> None:
    body = struct.pack("!H", code) + reason.encode("utf-8")[:120]
    write_frame(sock, _OP_CLOSE, body)


# ---------------------------------------------------------------------------
# PTY session
# ---------------------------------------------------------------------------
def terminal_state_home(cfg: Any = None) -> Path:
    """The Qubit runtime home the ``/chat`` PTY/TUI writes ``state.db`` under.

    C7: the terminal and the native chat surfaces MUST share ONE ``state.db``.
    This resolves the same home as the canonical adapter
    (``voicebridge.qubit_session_store.qubit_state_db_path``), never the
    operator's external ``~/.hermes``. It applies the same ``QUBIT_HOME``
    binding the real spawn uses, so callers see the exact home a child gets.
    """
    from . import qubit_runtime
    qubit_runtime.ensure_path()
    return Path(qubit_runtime.use_qubit_home(cfg))


def _assert_terminal_shares_canonical_store(qubit_home: Any,
                                            cfg: Any = None) -> None:
    """Fail closed if the PTY terminal would open a divergent ``state.db``.

    The terminal is the vendored runtime's own single writer of the SAME
    ``state.db`` the canonical adapter (:class:`QubitSessionStore`) uses. If the
    resolved terminal home would point at the operator's external
    ``~/.hermes`` (a second session authority) or any path other than the
    canonical store, refuse with a typed error before the PTY is spawned.
    """
    from . import qubit_session_store as qss
    terminal_db = Path(qubit_home) / qss.STATE_DB_FILENAME
    try:
        qss.assert_canonical_state_db(terminal_db, cfg)
    except qss.SessionDbWriteAuthorityError as exc:
        raise TerminalContextError({
            "ok": False,
            "code": "TERMINAL_STATE_HOME_DIVERGED",
            "detail": str(exc),
            "path": exc.path,
        }) from exc


def _default_spawn(cols: int, rows: int, resume: str, profile: str,
                   *, env_overrides: Optional[Dict[str, str]] = None,
                   home_config: Optional[Dict[str, Any]] = None,
                   cfg: Any = None,
                   workspace_cwd: str = ""
                   ) -> Tuple[Any, Dict[str, Any]]:
    """Spawn the vendored Qubit terminal (``qubit --tui``) behind a PTY.

    Returns ``(bridge, meta)``. Imported lazily so the module loads without the
    vendored tree on sys.path. ``env_overrides`` carries the resolved Qubit
    model profile (model/provider/credentials) into the child so the TUI does
    not resolve an independent model from ``QUBIT_HOME``; ``home_config`` is
    merged into the Qubit QUBIT_HOME ``config.yaml`` so the vendored runtime
    resolves the exact provider/model/base_url. Before spawning it also bridges
    the Qubit MCP registry into that config and maps the session onto the
    canonical conversation (:mod:`voicebridge.terminal_bridge`); both are
    best-effort and never block the terminal.
    """
    from . import qubit_runtime
    qubit_runtime.ensure_path()
    # Point the child at the Qubit-owned runtime home BEFORE it is spawned.
    # Without this the vendored TUI resolves ``QUBIT_HOME`` unset -> ``~/.hermes``
    # and reads the operator's external Qubit install: wrong model (e.g. a
    # Qubit/GLM default), zero Qubit skills/tools, and a second session authority.
    qubit_home = qubit_runtime.use_qubit_home(cfg)
    # C7 write-authority invariant: the terminal's state.db MUST be the same
    # canonical store the QubitSessionStore adapter uses. A divergent home is a
    # typed refusal, never a silent second session authority.
    _assert_terminal_shares_canonical_store(qubit_home, cfg)
    if home_config:
        _write_terminal_profile_config(qubit_home, home_config)
    # Terminal bridge (best-effort, never blocks a terminal open):
    #  (1) merge the ONE Qubit MCP registry into the runtime home config.yaml so
    #      the TUI sees the same configured servers (additive/atomic/backed up);
    #  (2) map this session onto the canonical conversation id so terminal chat,
    #      /qubit-chat and voice share one transcript. The TUI reads
    #      QUBIT_TUI_RESUME while the vendored launch path only sets the
    #      HERMES_ name, so both are seeded below.
    from . import terminal_bridge as TB

    meta: Dict[str, Any] = {}
    try:
        meta["mcp"] = TB.merge_mcp_servers(qubit_home, cfg)
    except Exception as exc:  # noqa: BLE001 - inventory merge is non-fatal
        meta["mcp"] = {"state": "DEGRADED", "detail": _redact(str(exc))[:200]}
    session_id = str(resume or "").strip()
    try:
        mapping = TB.canonical_terminal_session(cfg, resume)
        session_id = str(mapping.get("session_id") or session_id)
    except Exception as exc:  # noqa: BLE001 - keep the raw requested id
        mapping = {"session_id": session_id, "ensured": False,
                   "detail": _redact(str(exc))[:200]}
    meta["session"] = mapping
    from qubit_cli.web_server_chat import _resolve_chat_argv  # type: ignore
    from qubit_cli.pty_bridge import PtyBridge, PtyUnavailableError  # type: ignore

    argv, cwd, env = _resolve_chat_argv(resume=session_id or None,
                                        profile=profile or None,
                                        workspace_cwd=workspace_cwd or None)
    if env is None:
        env = dict(os.environ)
    env["QUBIT_HOME"] = str(qubit_home)
    if session_id:
        env.update(TB.terminal_resume_env(session_id))
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items() if v is not None})
    bridge = PtyBridge.spawn(list(argv), cwd=cwd, env=env, cols=cols, rows=rows)
    return bridge, {"argv_head": argv[:1], "cwd": cwd or "",
                    "home": str(qubit_home), "session_id": session_id,
                    "bridge": meta}


def _write_terminal_profile_config(qubit_home: Any,
                                   guard: Dict[str, Any]) -> bool:
    """Merge the selected profile's provider/model/base_url into the Qubit home.

    The vendored runtime honours ``model.base_url`` only when ``model.provider``
    matches, so this is how a non-default endpoint reaches the TUI. Only
    non-secret routing fields are written; credentials stay in the child env as
    an ``env:`` reference or an injected ``file:`` value. Best-effort: a write
    failure leaves the env seeds authoritative rather than blocking the spawn.
    """
    provider = str(guard.get("provider") or "").strip()
    model = str(guard.get("model") or "").strip()
    base_url = str(guard.get("base_url") or "").strip()
    if not provider or not model:
        return False
    cfg_path = Path(qubit_home) / "config.yaml"
    try:
        import yaml

        data: Dict[str, Any] = {}
        if cfg_path.is_file():
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        model_cfg = data.get("model")
        if not isinstance(model_cfg, dict):
            model_cfg = {}
        model_cfg["provider"] = provider
        model_cfg["default"] = model
        if base_url:
            model_cfg["base_url"] = base_url
        else:
            model_cfg.pop("base_url", None)
        data["model"] = model_cfg
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg_path.with_name(cfg_path.name + ".qubit-tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        os.replace(tmp, cfg_path)
        return True
    except Exception:
        return False


def _provider_key_env_vars(provider: str) -> Tuple[str, ...]:
    """Env var(s) the vendored provider layer reads for *provider*'s API key."""
    name = str(provider or "").strip()
    if not name:
        return ()
    try:
        from . import qubit_runtime
        qubit_runtime.ensure_path()
        from providers import get_provider_profile  # type: ignore

        profile = get_provider_profile(name)
        if profile is not None and profile.env_vars:
            return tuple(str(v) for v in profile.env_vars if v)
    except Exception:
        pass
    return (f"{name.upper().replace('-', '_')}_API_KEY",)


def _read_secret_ref(key_ref: str) -> str:
    """Resolve a Qubit ``file:`` credential reference; ``<unset>`` if absent."""
    ref = str(key_ref or "").strip()
    if not ref.startswith("file:"):
        return ""
    path = os.path.expanduser(ref[len("file:"):])
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def terminal_profile_env(cfg: Any = None, guard: Optional[Dict[str, Any]] = None
                         ) -> Dict[str, str]:
    """Env overrides that make the PTY/TUI use the selected Qubit profile.

    Sets the vendored TUI's launch-scoped model/provider seeds (which take
    precedence over ``QUBIT_HOME`` config) and injects a ``file:`` credential
    under the provider's key env var. An ``env:`` reference is already present
    in the child environment and is left untouched (never re-exported).
    """
    if guard is None:
        try:
            from .providers import resolver as _resolver
            guard = _resolver.terminal_context_guard(cfg)
        except Exception:
            return {}
    if not isinstance(guard, dict) or not guard.get("ok"):
        return {}
    overrides: Dict[str, str] = {}
    model = str(guard.get("model") or "").strip()
    provider = str(guard.get("provider") or "").strip()
    if model:
        overrides["HERMES_MODEL"] = model
        overrides["HERMES_INFERENCE_MODEL"] = model
    if provider:
        overrides["HERMES_TUI_PROVIDER"] = provider
        overrides["HERMES_INFERENCE_PROVIDER"] = provider
    key_ref = str(guard.get("key_ref") or "").strip()
    if key_ref.startswith("file:"):
        secret = _read_secret_ref(key_ref)
        if secret:
            for var in _provider_key_env_vars(provider):
                overrides[var] = secret
    return overrides


@dataclass
class _Session:
    bridge: Any
    ticket: _Ticket
    started_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    loop: Any = None  # dedicated asyncio loop for the vendored async writes


class PtyWsServer:
    """Owns WS-PTY sessions for one WebUI listener (single authority)."""

    def __init__(self, *, cfg: Any = None,
                 spawn: Optional[Callable[[int, int, str, str], Tuple[Any, Dict[str, Any]]]] = None,
                 estop: Optional[Callable[[Any], bool]] = None,
                 context_guard: Optional[Callable[[Any], Dict[str, Any]]] = None,
                 max_sessions: int = MAX_SESSIONS,
                 idle_timeout_s: float = IDLE_TIMEOUT_S) -> None:
        self.cfg = cfg
        self._spawn = spawn or self._production_spawn
        self._estop = estop
        # The context floor protects the REAL terminal spawn. Injected test
        # doubles (``spawn`` supplied) are exempt so unit tests can drive the
        # transport without provisioning a 64k+ provider.
        self._prod_spawn = spawn is None
        if context_guard is None and self._prod_spawn:
            from .providers import resolver as _resolver
            context_guard = _resolver.terminal_context_guard
        self._context_guard = context_guard
        self.tickets = TicketStore()
        self._max = int(max_sessions)
        self._idle = float(idle_timeout_s)
        self._lock = threading.Lock()
        self._active: Dict[int, _Session] = {}

    def _resolve_guard(self, profile_id: str = "") -> Optional[Dict[str, Any]]:
        """Resolve the ``/chat`` context guard for the real vendored spawn.

        Returns the guard dict (pass *or* typed failure) or ``None`` when no
        real-spawn guard applies: a server built with an injected test ``spawn``
        double, or no guard configured. ``profile_id`` is the client's requested
        Qubit model profile id; it is threaded through so the guard selects that
        exact profile (an unknown id is a typed refusal) rather than silently
        resolving a different one.
        """
        if self._context_guard is None or getattr(
                self._spawn, "__func__", None) is not PtyWsServer._production_spawn:
            return None
        requested = str(profile_id or "").strip() or None
        try:
            return self._context_guard(self.cfg, profile_id=requested)
        except TypeError:
            # A guard injected without the profile keyword still resolves its
            # own default; the client profile is never silently substituted.
            return self._context_guard(self.cfg)
        except Exception as exc:  # noqa: BLE001 - fail closed, redacted
            return {"ok": False, "code": "TERMINAL_CONTEXT_UNVERIFIED",
                    "detail": _redact(str(exc))}

    def journal_context_refusal(self, guard: Dict[str, Any], *,
                                requested_profile: str = "",
                                via: str = "ws-pty", session: str = "",
                                owner: str = "", target: str = "") -> None:
        """Audit a refused ``/chat`` open. Non-secret profile/model references
        only — never a credential value, ``key_ref``, or ``base_url``."""
        log_event(
            "webui.pty.context_refused",
            code=str(guard.get("code") or "TERMINAL_CONTEXT_BELOW_MINIMUM"),
            profile_id=str(guard.get("profile_id") or requested_profile or ""),
            provider=str(guard.get("provider") or ""),
            model=str(guard.get("model") or ""),
            context_tokens=guard.get("context_tokens"),
            min_context_tokens=guard.get("min_context_tokens"),
            source=str(guard.get("source") or ""),
            context_verification=str(guard.get("context_verification") or ""),
            context_window_source=str(guard.get("context_window_source") or ""),
            context_verified=guard.get("context_verified"),
            real_context_tokens=guard.get("real_context_tokens"),
            requested_profile=str(requested_profile or ""),
            via=via, session=session, owner=owner, target=target)

    def _production_spawn(self, cols: int, rows: int, resume: str, profile: str,
                          workspace_cwd: str = ""
                          ) -> Tuple[Any, Dict[str, Any]]:
        """Guard + spawn the real terminal with the selected Qubit profile.

        The resolved profile's model/provider are injected into the child env so
        the vendored TUI uses the Qubit-selected model instead of resolving one
        from its own ``QUBIT_HOME`` config. A failing guard is raised as a typed
        error (the caller already rejected the upgrade; this is defense in
        depth). ``profile`` is a *Qubit model profile id*, not a vendored
        profile directory, so it is NOT forwarded to the vendored ``--profile``
        (which scopes ``QUBIT_HOME`` at a profile dir); the vendored runtime
        resolves the Qubit-seeded model/provider from the child env +
        ``QUBIT_HOME`` config instead.
        """
        guard = self._resolve_guard(profile)
        if isinstance(guard, dict) and guard.get("ok") is False:
            raise TerminalContextError(guard)
        overrides = terminal_profile_env(self.cfg, guard) if guard else None
        return _default_spawn(cols, rows, resume, "",
                              env_overrides=overrides, home_config=guard,
                              cfg=self.cfg, workspace_cwd=workspace_cwd)

    def _request_spawn(self, cols: int, rows: int, resume: str, profile: str,
                       workspace_cwd: str = "") -> Tuple[Any, Dict[str, Any]]:
        """Spawn via the production path (which accepts ``workspace_cwd``), or
        an injected/spawn-overridden double (4-arg signature) unchanged.

        Detects production by identity of the CURRENT ``_spawn`` binding, so a
        test that overrides ``pty_ws._spawn`` after construction still wins.
        """
        spawn = self._spawn
        if getattr(spawn, "__func__", None) is PtyWsServer._production_spawn:
            return self._production_spawn(cols, rows, resume, profile,
                                          workspace_cwd)
        return spawn(cols, rows, resume, profile)

    def _estop_engaged(self) -> bool:
        if self._estop is not None:
            try:
                return bool(self._estop(self.cfg))
            except Exception:
                return True
        try:
            from .broker import estop_engaged
            return bool(estop_engaged(self.cfg))
        except Exception:
            return True

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def context_preflight(self, profile_id: str = "") -> Optional[Dict[str, Any]]:
        """Typed ``/chat`` context-floor failure, or ``None`` when it passes.

        The floor only guards the REAL vendored spawn; a server constructed
        with an injected ``spawn`` double (tests) returns ``None`` so the
        transport can be exercised without provisioning a 64k+ provider.
        ``profile_id`` is the client's requested Qubit model profile id, when
        one was supplied.
        """
        guard = self._resolve_guard(profile_id)
        if isinstance(guard, dict) and guard.get("ok") is False:
            return guard
        return None

    def cancel_all(self) -> None:
        """Kill every live PTY child (listener shutdown / e-stop)."""
        with self._lock:
            sessions = list(self._active.values())
        for s in sessions:
            s.cancelled = True
            try:
                s.bridge.close()
            except Exception:
                pass
        with self._lock:
            self._active.clear()

    def serve(self, handler: Any, *, ticket_value: str, cols: int, rows: int,
              resume: str, profile: str, origin_ok: bool,
              peer_ok: bool, workspace_cwd: str = "") -> None:
        """Run one WS-PTY session to completion (called from the handler thread)."""
        # 1) Auth + boundary checks BEFORE the handshake so a rejection is an
        #    HTTP status, never an accepted socket.
        if not peer_ok:
            _reject(handler, 403, "PEER_REFUSED",
                    "PTY WebSocket is bound to the approved local interface.")
            return
        if not origin_ok:
            _reject(handler, 403, "ORIGIN_REFUSED",
                    "PTY WebSocket origin does not match the bound host.")
            return
        ticket = self.tickets.consume(ticket_value, scope=SCOPE_PTY)
        if ticket is None:
            _reject(handler, 401, "TICKET_REQUIRED",
                    "A short-lived scoped PTY ticket is required.")
            return
        if self._estop_engaged():
            _reject(handler, 423, "ESTOP_ENGAGED",
                    "Qubit emergency stop is engaged; terminal refused.")
            return
        # /chat is the long-context surface: refuse a below-floor profile with
        # a typed configuration error BEFORE any PTY is spawned. Never open a
        # terminal that would silently truncate, and never fall back to Brain4.
        # The guard applies only to the real vendored spawn; tests replace
        # ``_spawn`` with a double and must not require a 64k+ provider. The
        # client's Qubit model profile (``?profile=``) is threaded through so an
        # unknown id is a typed refusal, never a silent substitution.
        guard = self._resolve_guard(profile)
        if isinstance(guard, dict) and guard.get("ok") is False:
            status = (503 if guard.get("code") == "TERMINAL_CONTEXT_UNVERIFIED"
                      else 409)
            self.journal_context_refusal(
                guard, requested_profile=profile, via="ws-pty",
                session=ticket.session, owner=ticket.owner,
                target=ticket.target)
            _reject(handler, status,
                    str(guard.get("code")
                        or "TERMINAL_CONTEXT_BELOW_MINIMUM"),
                    _redact(str(guard.get("detail") or ""))[:220])
            return
        with self._lock:
            if len(self._active) >= self._max:
                _reject(handler, 429, "PTY_LIMIT",
                        "Too many concurrent terminal sessions.")
                return

        # 2) Spawn the real terminal behind the vendored PTY bridge.
        try:
            bridge, meta = self._request_spawn(cols, rows, resume, profile,
                                               workspace_cwd)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - typed refusal
            # The vendored TUI launch path calls ``sys.exit`` on an
            # unrecoverable setup failure (e.g. a failed ``npm install`` while
            # resolving the terminal argv). ``SystemExit`` is a ``BaseException``,
            # so ``except Exception`` would let it escape this handler thread and
            # close the socket with no reply — the operator sees a blank terminal
            # with no cause. Convert it (and any other spawn failure) into a
            # truthful typed refusal naming the real error instead.
            detail = getattr(exc, "code", None) or str(exc)
            _reject(handler, 503, "PTY_UNAVAILABLE",
                    _redact(str(detail))[:200])
            return

        session = _Session(bridge=bridge, ticket=ticket)
        with self._lock:
            self._active[id(session)] = session
        try:
            handshake(handler)
        except WsProtocolError as exc:
            self._teardown(session)
            _reject(handler, 400, "WS_HANDSHAKE", str(exc))
            return
        resolved = guard if isinstance(guard, dict) else {}
        bridge_meta = meta.get("bridge") if isinstance(meta, dict) else None
        mcp_meta = (bridge_meta or {}).get("mcp") if isinstance(
            bridge_meta, dict) else None
        log_event("webui.pty.session_open", session=ticket.session,
                  owner=ticket.owner, target=ticket.target, pid=_safe_pid(bridge),
                  cwd=meta.get("cwd", ""),
                  conversation_id=str(meta.get("session_id") or ""),
                  mcp_merge_state=str((mcp_meta or {}).get("state") or ""),
                  profile_id=str(resolved.get("profile_id") or ""),
                  provider=str(resolved.get("provider") or ""),
                  model=str(resolved.get("model") or ""),
                  context_tokens=resolved.get("context_tokens"),
                  context_verified=resolved.get("context_verified"),
                  context_verification=str(
                      resolved.get("context_verification") or ""),
                  context_window_source=str(
                      resolved.get("context_window_source") or ""))
        try:
            self._pump(handler, session)
        finally:
            self._teardown(session)
            log_event("webui.pty.session_close", session=ticket.session,
                      pid=_safe_pid(bridge))

    def _pump(self, handler: Any, session: _Session) -> None:
        sock = handler.connection
        sock.settimeout(POLL_TIMEOUT)
        bridge = session.bridge
        while True:
            if session.cancelled:
                close_frame(sock, 1001, "cancelled")
                return
            if self._estop_engaged():
                session.cancelled = True
                close_frame(sock, 1011, "emergency stop")
                return
            if time.monotonic() - session.last_activity > self._idle:
                close_frame(sock, 1000, "idle timeout")
                return
            # PTY -> WS
            try:
                chunk = bridge.read(PTY_READ_TIMEOUT)
            except Exception:
                chunk = None
            if chunk is None:
                close_frame(sock, 1000, "session ended")
                return
            if chunk:
                session.last_activity = time.monotonic()
                if not write_frame(sock, _OP_BIN, chunk):
                    return
            # WS -> PTY
            try:
                readable, _, _ = select.select([sock], [], [], 0)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            frame = read_frame(sock)
            if frame is None:
                return
            opcode, payload = frame
            if opcode == _OP_CLOSE:
                close_frame(sock, 1000, "bye")
                return
            if opcode == _OP_PING:
                write_frame(sock, _OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if not payload:
                continue
            session.last_activity = time.monotonic()
            match = _RESIZE_RE.fullmatch(payload)
            if match:
                try:
                    bridge.resize(cols=int(match.group(1)),
                                  rows=int(match.group(2)))
                except Exception:
                    pass
                continue
            try:
                if not self._bridge_write(session, payload):
                    close_frame(sock, 1013, "input stalled")
                    return
            except Exception:
                close_frame(sock, 1011, "input error")
                return

    @staticmethod
    def _bridge_write(session: _Session, payload: bytes) -> bool:
        """Write PTY input; the vendored bridge's ``write`` is a coroutine.

        A dedicated per-session loop runs it without touching the listener's
        threads or creating a second server. A synchronous bridge (tests) is
        returned as-is.
        """
        coro = session.bridge.write(payload)
        if not asyncio.iscoroutine(coro):
            return bool(coro)
        if session.loop is None:
            session.loop = asyncio.new_event_loop()
        return bool(session.loop.run_until_complete(coro))

    def _teardown(self, session: _Session) -> None:
        with self._lock:
            self._active.pop(id(session), None)
        try:
            session.bridge.close()
        except Exception:
            pass
        if session.loop is not None:
            try:
                session.loop.close()
            except Exception:
                pass
            session.loop = None


def _safe_pid(bridge: Any) -> int:
    try:
        return int(bridge.pid)
    except Exception:
        return -1


def _reject(handler: Any, status: int, code: str, detail: str) -> None:
    """Typed HTTP rejection for a bad upgrade (never an accepted socket)."""
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Connection", "close")
        handler.end_headers()
        import json
        handler.wfile.write(json.dumps({
            "ok": False, "state": "UNAVAILABLE", "code": code,
            "detail": detail, "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}).encode())
    except Exception:
        pass
    finally:
        handler.close_connection = True


def origin_allowed(handler: Any, allowed_origins: Any = None) -> bool:
    """Origin host must match the Host header (or a configured origin)."""
    origin = (handler.headers.get("Origin") or "").strip()
    if not origin:
        return True  # non-browser client (tests, CLI); credential still gates
    from urllib.parse import urlparse
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    host_header = (handler.headers.get("Host") or "").strip().lower()
    if parsed.netloc.lower() == host_header:
        return True
    configured = {o.rstrip("/").lower() for o in (allowed_origins or [])
                  if o and o != "*"}
    return origin.rstrip("/").lower() in configured


def peer_allowed(handler: Any) -> bool:
    peer = handler.client_address[0] if handler.client_address else ""
    return peer in LOOPBACK


__all__ = [
    "SCOPE_PTY", "TicketStore", "PtyWsServer", "WsProtocolError",
    "handshake", "read_frame", "write_frame", "close_frame",
    "origin_allowed", "peer_allowed", "MAX_SESSIONS",
]
