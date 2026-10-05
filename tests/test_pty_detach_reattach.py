"""PTY detach/re-attach: a browser refresh must not kill an in-flight turn.

The dashboard closes the ``/api/pty`` websocket when the tab reloads. Before
this change the ``finally`` tore the terminal down unconditionally, so a
refresh killed the running agent turn. Now a client disconnect detaches the
PTY (it keeps running) and the next connect presenting the same stable
conversation key re-attaches to that same bridge instead of spawning a second
terminal.
"""

from voicebridge import pty_ws


class _Bridge:
    """Minimal bridge double: alive until ``closed``, then EOF (``None``)."""

    def __init__(self):
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


def _ticket(session: str) -> pty_ws._Ticket:
    return pty_ws._Ticket(scope="pty", session=session, owner="o",
                          target="t", expires_at=float("inf"))


def _srv() -> pty_ws.PtyWsServer:
    return pty_ws.PtyWsServer(spawn=lambda c, r, s, p, w: (_Bridge(), {}))


def test_detach_keeps_the_pty_and_reattach_returns_the_same_bridge():
    srv = _srv()
    bridge = _Bridge()
    session = pty_ws._Session(bridge=bridge, ticket=_ticket("chat-1"),
                              key="chat-1")
    with srv._lock:
        srv._active[id(session)] = session

    # Client reloads: the websocket closes but the turn is still running.
    srv._detach(session)

    assert "chat-1" not in {s.key for s in srv._active.values()}
    assert srv._detached["chat-1"] is session
    assert bridge.closed is False, "detach must NOT kill the terminal"

    # The reloaded tab reconnects with the same conversation key.
    back = srv._reattach("chat-1")
    assert back is session, "must re-attach the same session"
    assert back.bridge is bridge, "must re-attach the same live terminal"
    assert back.ended is False and back.cancelled is False


def test_reattach_on_unknown_key_returns_none():
    srv = _srv()
    assert srv._reattach("never-seen") is None


def test_reattach_retires_a_session_whose_terminal_exited():
    srv = _srv()
    bridge = _Bridge()
    bridge.closed = True  # the child process exited while detached
    session = pty_ws._Session(bridge=bridge, ticket=_ticket("chat-2"),
                              key="chat-2")
    srv._detached["chat-2"] = session

    assert srv._reattach("chat-2") is None, "a dead terminal must not resurrect"
    assert "chat-2" not in srv._detached


def test_teardown_removes_a_detached_entry():
    srv = _srv()
    session = pty_ws._Session(bridge=_Bridge(), ticket=_ticket("chat-3"),
                              key="chat-3")
    srv._detached["chat-3"] = session

    srv._teardown(session)

    assert "chat-3" not in srv._detached
    assert session.bridge.closed is True


def test_detached_set_is_bounded_by_max_sessions():
    srv = _srv()
    srv._max = 2
    for i in range(4):
        s = pty_ws._Session(bridge=_Bridge(), ticket=_ticket(f"c{i}"),
                            key=f"c{i}")
        srv._detach(s)

    assert len(srv._detached) <= 2, "detached PTYs must be bounded"


def test_detach_all_keeps_live_terminals_alive_at_shutdown():
    """A service stop must not kill an in-flight turn."""
    srv = _srv()
    bridge = _Bridge()
    session = pty_ws._Session(bridge=bridge, ticket=_ticket("chat-9"),
                              key="chat-9")
    with srv._lock:
        srv._active[id(session)] = session

    srv.detach_all()

    assert not srv._active, "active set drains on shutdown"
    assert srv._detached["chat-9"] is session
    assert bridge.closed is False, "shutdown must NOT kill a live terminal"


def test_cancel_all_does_not_kill_detached_terminals():
    """Stop/restart covers active sessions only; detached ones survive."""
    srv = _srv()
    detached_bridge = _Bridge()
    srv._detached["chat-keep"] = pty_ws._Session(
        bridge=detached_bridge, ticket=_ticket("chat-keep"), key="chat-keep")
    active_bridge = _Bridge()
    active = pty_ws._Session(bridge=active_bridge,
                             ticket=_ticket("chat-kill"), key="chat-kill")
    with srv._lock:
        srv._active[id(active)] = active

    srv.cancel_all()

    assert active_bridge.closed is True, "an active PTY is still cancelled"
    assert detached_bridge.closed is False, "a detached PTY must survive"
