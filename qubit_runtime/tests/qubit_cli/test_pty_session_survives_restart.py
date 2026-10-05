"""A backend restart must not destroy an in-flight, unattended agent turn.

When the operator starts a long task in the dashboard and leaves, a backend
restart used to kill the hosted TUI (and the turn with it), so they had to
retype `continue` on return. Sessions whose viewer has already gone away are
now spared at shutdown and stay under the idle-TTL reaper, so a reconnect
re-attaches to the same terminal instead of forking an empty one.
"""

import asyncio

import pytest

from qubit_cli.pty_session import PtySessionRegistry


class _Bridge:
    def __init__(self):
        self.closed = False

    def read(self, _timeout):
        return b""  # idle tick; never EOF, so the session stays alive

    def is_alive(self):
        return not self.closed

    def resize(self, *_a, **_kw):
        return None

    def close(self):
        self.closed = True


class _Ws:
    """Minimal websocket double; records the close code it was given."""

    def __init__(self):
        self.closed_with = None

    async def send_bytes(self, _data):
        return None

    async def close(self, code=None):
        self.closed_with = code


def _registry(**kw):
    return PtySessionRegistry(ttl=kw.pop("ttl", 60.0), max_sessions=4,
                              buffer_cap=1024, read_timeout=0.01, **kw)


def _spawn():
    return _Bridge()


@pytest.mark.asyncio
async def test_restart_spares_a_detached_session():
    reg = _registry()
    session, created = await reg.attach_or_spawn("tab-1", spawn=_spawn)
    assert created is True

    # The operator reloads / closes the tab: the viewer goes away but the turn
    # keeps running server-side.
    ws = _Ws()
    await session.attach(ws)
    reg.detach("tab-1", ws)

    # Backend shutdown (restart).
    await reg.close_all(keep_detached=True)

    assert session.alive is True, "a detached turn must survive a restart"
    assert session.bridge.closed is False, "its terminal must not be killed"


@pytest.mark.asyncio
async def test_restart_still_closes_attached_sessions():
    reg = _registry()
    session, _ = await reg.attach_or_spawn("tab-2", spawn=_spawn)
    ws = _Ws()
    await session.attach(ws)

    await reg.close_all(keep_detached=True)

    assert session.bridge.closed is True, "a live viewer is still closed"


@pytest.mark.asyncio
async def test_close_all_default_still_closes_everything():
    """The e-stop / explicit-purge path must keep its old meaning."""
    reg = _registry()
    session, _ = await reg.attach_or_spawn("tab-3", spawn=_spawn)
    ws = _Ws()
    await session.attach(ws)
    reg.detach("tab-3", ws)

    await reg.close_all()

    assert session.bridge.closed is True


@pytest.mark.asyncio
async def test_reconnect_reattaches_the_same_terminal():
    reg = _registry()
    session, created = await reg.attach_or_spawn("tab-4", spawn=_spawn)
    assert created is True
    first_ws = _Ws()
    await session.attach(first_ws)
    reg.detach("tab-4", first_ws)

    again, created_again = await reg.attach_or_spawn("tab-4", spawn=_spawn)

    assert created_again is False, "a reconnect must reuse, not re-fork"
    assert again is session
    assert again.bridge is session.bridge
