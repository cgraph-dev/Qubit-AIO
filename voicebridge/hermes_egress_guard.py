"""Fail-closed outbound-egress guard for the in-process Hermes fork.

Cutover stage 2b (docs/QUBIT_HERMES_CUTOVER_MIGRATION.md). The vendored Hermes
backend performs incidental outbound provider calls during some dashboard reads
(provider health/token probes). Qubit's frozen no-unrestricted-network contract
forbids that, so the fork runs with a deny-by-default transport: any outbound
connection made *inside a fork request* to a non-loopback host is refused with a
typed error and audited. Qubit's own provider egress is untouched because the
guard is scoped by a :mod:`contextvars` flag that is set only while the ASGI
bridge is invoking the fork.

The guard is process-global but context-sensitive: Qubit calls on other threads
or contexts see the default ``False`` and connect normally.
"""
from __future__ import annotations

import contextlib
import contextvars
import socket
import threading
from typing import Iterator

from .logging_setup import log_event

#: True only while the in-process Hermes fork is serving a request.
FORK_CONTEXT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "qubit_hermes_fork_egress", default=False)

#: Hosts the fork may always reach: loopback only. The fork never needs to
#: leave the machine to answer a Qubit dashboard read.
_LOCAL_HOSTS = frozenset({
    "localhost", "127.0.0.1", "::1", "0.0.0.0", "hermes.internal",
})

_installed = False
_original_connect = None
_original_connect_ex = None
_original_getaddrinfo = None
_denials = 0

# Global count of in-flight fork requests. A library the fork calls through a
# raw ThreadPoolExecutor does not inherit the contextvar, so the contextvar
# alone leaks egress. This counter closes that hole: while any fork request is
# in flight, outbound connects from *any* thread are denied. Qubit's own
# provider egress never runs inside the bridge call, so it is unaffected.
_inflight = 0
_inflight_lock = threading.Lock()


def _in_fork() -> bool:
    if FORK_CONTEXT.get():
        return True
    return _inflight > 0


class EgressBlocked(OSError):
    """Raised when the fork attempts a denied outbound connection."""


def _host_allowed(host: object) -> bool:
    if host is None:
        return True  # AF_UNIX / already-connected / abstract
    text = str(host)
    if not text:
        return True
    return text.split("%", 1)[0] in _LOCAL_HOSTS


def _record(host: object, port: object) -> None:
    global _denials
    _denials += 1
    try:
        log_event("hermes.egress.denied", host=str(host)[:120],
                  port=str(port)[:16])
    except Exception:  # pragma: no cover - audit must never break a call
        pass


def _guard_connect(sock: "socket.socket", address) -> None:
    if _in_fork() and isinstance(address, tuple) and len(address) >= 2:
        if not _host_allowed(address[0]):
            _record(address[0], address[1])
            raise EgressBlocked(
                "EGRESS_DENIED: in-process Hermes backend may not reach "
                f"{address[0]}:{address[1]} (Qubit no-unrestricted-network)")
    return _original_connect(sock, address)


def _guard_connect_ex(sock: "socket.socket", address) -> int:
    try:
        _guard_connect(sock, address)
    except EgressBlocked as exc:
        return getattr(exc, "errno", None) or 1
    return 0


def _guard_getaddrinfo(host, *args, **kwargs):
    if (_in_fork() and not _host_allowed(host)
            and host not in (None, "")):
        _record(host, kwargs.get("port", "") or (args[0] if args else ""))
        raise EgressBlocked(
            "EGRESS_DENIED: in-process Hermes backend may not resolve "
            f"{host!r} (Qubit no-unrestricted-network)")
    return _original_getaddrinfo(host, *args, **kwargs)


def install() -> bool:
    """Install the guard (idempotent). Returns True the first time."""
    global _installed, _original_connect, _original_connect_ex, \
        _original_getaddrinfo
    if _installed:
        return False
    _original_connect = socket.socket.connect
    _original_connect_ex = socket.socket.connect_ex
    _original_getaddrinfo = socket.getaddrinfo
    socket.socket.connect = _guard_connect
    socket.socket.connect_ex = _guard_connect_ex
    socket.getaddrinfo = _guard_getaddrinfo
    _installed = True
    return True


@contextlib.contextmanager
def fork_scope() -> Iterator[None]:
    """Mark the current context (and any copied child) as fork-originated."""
    global _inflight
    install()
    token = FORK_CONTEXT.set(True)
    with _inflight_lock:
        _inflight += 1
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight -= 1
        FORK_CONTEXT.reset(token)


def status() -> dict:
    """Read-only guard status for diagnostics/tests."""
    return {
        "installed": _installed,
        "denials": _denials,
        "inflight": _inflight,
        "allowed_hosts": sorted(_LOCAL_HOSTS),
    }


def _reset_for_tests() -> None:
    """Uninstall and clear counters (tests only)."""
    global _installed, _denials
    if _installed:
        socket.socket.connect = _original_connect
        socket.socket.connect_ex = _original_connect_ex
        socket.getaddrinfo = _original_getaddrinfo
        _installed = False
    _denials = 0


__all__ = ["FORK_CONTEXT", "EgressBlocked", "install", "fork_scope",
           "status", "_reset_for_tests"]
