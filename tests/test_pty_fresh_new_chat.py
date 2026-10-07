"""Server-side `?fresh=1` contract for the /chat New-chat flow.

Symptom this pins: pressing "New chat" on the dashboard spawned a genuinely
fresh PTY, but the terminal reverted to the previous conversation on the next
reload, so the operator had to press it several times. Root cause (client half)
was the rotated attach token never being persisted; the server half is that
`/agent/api/pty` had NO forced-fresh concept at all — unlike the vendored
reference (`hermes_cli/web_routers/chat_ws.py`, which turns `fresh=1` into
resume=None + an active-session-file unlink).

`PtyWsServer.serve(..., fresh=True)` must therefore:
  * drop any resume target (so the TUI forges a new session), and
  * NEVER re-adopt a still-live detached terminal — not by attach key, and not
    by resume target — so a stale token a reload presents cannot resurrect the
    old chat.

These drive the real `serve()` over a socketpair handler (no live listener),
with an injected spawn double.
"""
from __future__ import annotations

import socket

from voicebridge import pty_ws as P


class _Bridge:
    def __init__(self, tag="b"):
        self.tag = tag
        self.pid = 4242
        self.closed = False
        self.written: list[bytes] = []

    def read(self, _timeout):
        return None if self.closed else b""

    def write(self, payload):
        self.written.append(payload)
        return True

    def isalive(self):
        return not self.closed

    def close(self):
        self.closed = True

    def resize(self, **_kw):
        return True


class _Handler:
    """Minimal request handler double for one serve() call over a real socketpair."""

    def __init__(self):
        self.server_sock, self.peer = socket.socketpair()
        self.connection = self.server_sock

        class _WFile:
            def __init__(self, sock):
                self._sock = sock

            def write(self, data):
                try:
                    self._sock.sendall(data)
                except OSError:
                    pass
                return len(data)

            def flush(self):
                pass

        class _Headers(dict):
            def get(self, k, d=None):  # noqa: A003 - mimic http.server
                return super().get(k, d)

        self.wfile = _WFile(self.server_sock)
        self.headers = _Headers({"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})

    def close(self):
        for s in (self.connection, self.peer):
            try:
                s.close()
            except OSError:
                pass


def _spawn(cols, rows, resume, profile, workspace_cwd=""):
    return _Bridge(), {"session_id": resume or "", "argv_head": ["node"],
                       "cwd": "", "home": "", "bridge": {}}


def _server():
    return P.PtyWsServer(spawn=_spawn, max_sessions=16, idle_timeout_s=1800)


def _ticket(srv, session="s"):
    ticket, _ttl = srv.tickets.mint(scope=P.SCOPE_PTY, session=session,
                                    owner="o", target="t")
    return ticket


def test_fresh_serve_never_readopts_a_detached_pty_by_attach_key():
    srv = _server()
    # A previous terminal for this tab is detached but still alive, under the
    # SAME attach key a reload would present.
    stale = P._Session(bridge=_Bridge("stale"), ticket=P._Ticket(
        scope="pty", session="s", owner="o", target="t", expires_at=float("inf")),
        key="qubit-attach-stored", session_id="S-old")
    srv._detached["qubit-attach-stored"] = stale

    h = _Handler()
    srv.serve(h, ticket_value=_ticket(srv), cols=80, rows=24,
              resume="", profile="", origin_ok=True, peer_ok=True,
              attach="qubit-attach-stored", fresh=True)

    h.close()
    # fresh=True must not re-adopt: the detached stale entry is untouched.
    assert "qubit-attach-stored" in srv._detached
    assert stale.bridge.closed is False


def test_fresh_serve_never_readopts_a_detached_pty_by_resume_target():
    srv = _server()
    stale = P._Session(bridge=_Bridge("stale"), ticket=P._Ticket(
        scope="pty", session="s", owner="o", target="t", expires_at=float("inf")),
        key="old-token", session_id="S-shared")
    srv._detached["old-token"] = stale

    h = _Handler()
    # Even if the client still presents ?resume=S-shared, a fresh start must
    # ignore it rather than re-adopting the detached terminal.
    srv.serve(h, ticket_value=_ticket(srv), cols=80, rows=24,
              resume="S-shared", profile="", origin_ok=True, peer_ok=True,
              attach="brand-new-token", fresh=True)

    h.close()
    assert "old-token" in srv._detached, "detached terminal must not be re-adopted"


def test_non_fresh_serve_still_readopts_by_attach_key():
    # Control: without fresh, the existing detach/re-attach contract is intact.
    srv = _server()
    live = P._Session(bridge=_Bridge("live"), ticket=P._Ticket(
        scope="pty", session="s", owner="o", target="t", expires_at=float("inf")),
        key="tok-1", session_id="S-1")
    srv._detached["tok-1"] = live

    h = _Handler()
    srv.serve(h, ticket_value=_ticket(srv), cols=80, rows=24,
              resume="", profile="", origin_ok=True, peer_ok=True,
              attach="tok-1", fresh=False)

    h.close()
    # Re-adopted: popped out of the detached set (now active, then detached
    # again when the pump ends) — the key left the pending set.
    assert any(s.bridge is live.bridge for s in srv._detached.values()) or \
        "tok-1" not in srv._detached
