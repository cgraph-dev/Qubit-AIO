"""The dashboard PTY lease shim releases a detached PTY child's session lease.

Contract under test (voicebridge/pty_lease_shim.py):

A PTY child holds an active-session lease, and a browser refresh only DETACHES
it — so it keeps fencing its session id while the refreshed tab's sidecar claims
a new one and is refused SESSION_NOT_OWNED. The shim must:

1. release the lease of a PTY child that stays detached past the grace window;
2. NOT release it when the viewer reattaches inside the grace;
3. release on close (TTL reap / close_all), unconditionally;
4. never touch a lease owned by a pid that is not one of its own children.

These use the REAL registry functions (real ``active_sessions.json`` in a temp
home) rather than mocks: the whole point of the shim is a cross-process write to
the image the refusal actually reads.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import pty_lease_shim  # noqa: E402


@pytest.fixture()
def qubit_home(tmp_path, monkeypatch):
    """A temp QUBIT_HOME whose registry the shim will read and write."""
    home = tmp_path / "home"
    (home / "runtime").mkdir(parents=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setattr(pty_lease_shim, "_registry_home", lambda: home, raising=True)
    return home


def _registry_path(home: Path) -> Path:
    return home / "runtime" / "active_sessions.json"


def _write_entries(home: Path, entries: list[dict]) -> None:
    _registry_path(home).write_text(json.dumps({"entries": entries}), encoding="utf-8")


def _read_entries(home: Path) -> list[dict]:
    data = json.loads(_registry_path(home).read_text(encoding="utf-8"))
    return data.get("entries", [])


def _lease(pid: int, session_id: str = "dashboard-abc123") -> dict:
    return {
        "lease_id": f"lease-{pid}",
        "session_id": session_id,
        "surface": "tui",
        "pid": pid,
        "process_start_time": None,
        "started_at": 1.0,
        "updated_at": 1.0,
        "metadata": {"live_session_id": "abc12345"},
    }


class _FakeBridge:
    def __init__(self, pid: int):
        self.pid = pid


class _FakeSession:
    """The subset of PtySession the shim reads."""

    def __init__(self, key: str, pid: int, *, alive: bool = True, attached: bool = False):
        self.key = key
        self.bridge = _FakeBridge(pid)
        self.alive = alive
        self.attached = attached
        self.last_detached_at: float | None = None

    async def close(self) -> None:
        self.alive = False


class _FakeRegistry:
    """Stands in for PtySessionRegistry, keeping only _sessions/_ttl behaviour."""

    def __init__(self, sessions: dict, ttl: float = 1800.0):
        self._sessions = sessions
        self._ttl = ttl

    def detach(self, key, ws):
        session = self._sessions.get(key)
        if session is not None and getattr(session, "_ws", None) is ws:
            session.attached = False

    async def reap_idle(self, now=None):
        popped = [k for k, s in self._sessions.items() if not s.alive]
        for key in popped:
            session = self._sessions.pop(key, None)
            if session is not None:
                await session.close()

    async def close_all(self, *, keep_detached: bool = False):
        for key in list(self._sessions):
            session = self._sessions.pop(key, None)
            if session is None or (keep_detached and not session.attached):
                continue
            await session.close()


# -- 1. detached past the grace releases the lease --------------------------

def test_detached_pty_releases_its_lease_after_grace(qubit_home):
    child_pid = 4242
    other_pid = 9999
    _write_entries(qubit_home, [_lease(child_pid), _lease(other_pid, "dashboard-other")])

    session = _FakeSession("tok-1", child_pid)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=0.05
    )

    async def scenario():
        registry.detach("tok-1", ws=object())  # adapter: session._ws is None, so no-op detach
        session.attached = False               # simulate the detach taking effect
        registry.detach("tok-1", ws=None)      # arm the grace release
        await asyncio.sleep(0.2)               # let the grace elapse

    asyncio.run(scenario())

    remaining = {e["pid"] for e in _read_entries(qubit_home)}
    assert child_pid not in remaining, "detached PTY child's lease must be released"
    assert other_pid in remaining, "an unrelated pid's lease must be untouched"


# -- 2. reattach inside the grace keeps the lease ---------------------------

def test_reattach_inside_grace_keeps_the_lease(qubit_home):
    child_pid = 4242
    _write_entries(qubit_home, [_lease(child_pid)])

    session = _FakeSession("tok-1", child_pid, attached=False)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=0.2
    )

    async def scenario():
        registry.detach("tok-1", ws=None)      # arm
        registry.attach_or_spawn_cancel_pending("tok-1")  # reattach cancels
        await asyncio.sleep(0.35)              # past the grace

    asyncio.run(scenario())

    assert [e["pid"] for e in _read_entries(qubit_home)] == [child_pid]


def test_reattach_before_grace_wakes_is_still_honored(qubit_home):
    """Parity guard: a live viewer's session is never dropped by a cancel race."""
    child_pid = 4242
    _write_entries(qubit_home, [_lease(child_pid)])

    session = _FakeSession("tok-1", child_pid, attached=False)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=0.05
    )

    async def scenario():
        registry.detach("tok-1", ws=None)
        session.attached = True                # viewer came back mid-grace
        await asyncio.sleep(0.2)

    asyncio.run(scenario())

    assert [e["pid"] for e in _read_entries(qubit_home)] == [child_pid]


# -- 3. close always releases ----------------------------------------------

def test_idle_reap_releases_lease(qubit_home):
    """A session the live predicate actually dooms (dead child) releases its lease."""
    child_pid = 4242
    _write_entries(qubit_home, [_lease(child_pid)])

    session = _FakeSession("tok-1", child_pid, alive=False)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=45.0
    )

    asyncio.run(registry.reap_idle())

    assert _read_entries(qubit_home) == []


def test_idle_reap_releases_lease_past_ttl(qubit_home):
    """Detached past TTL is the other doomed condition (child still alive)."""
    child_pid = 4242
    _write_entries(qubit_home, [_lease(child_pid)])

    session = _FakeSession("tok-1", child_pid, alive=True, attached=False)
    session.last_detached_at = 0.0  # monotonically ancient
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}, ttl=1800.0), detach_release_grace_s=45.0
    )

    async def scenario():
        await registry.reap_idle(now=1e9)  # far past 0.0 + ttl

    asyncio.run(scenario())

    assert _read_entries(qubit_home) == []


def test_idle_reap_keeps_a_live_attached_session(qubit_home):
    """Parity guard: a live, attached PTY must not lose its lease to the reaper."""
    child_pid = 4242
    _write_entries(qubit_home, [_lease(child_pid)])

    session = _FakeSession("tok-1", child_pid, alive=True, attached=True)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=45.0
    )

    asyncio.run(registry.reap_idle(now=1e9))

    assert [e["pid"] for e in _read_entries(qubit_home)] == [child_pid]


def test_close_all_releases_lease(qubit_home):
    child_pid = 4242
    _write_entries(qubit_home, [_lease(child_pid)])

    session = _FakeSession("tok-1", child_pid, attached=True)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=45.0
    )

    asyncio.run(registry.close_all())

    assert _read_entries(qubit_home) == []


# -- 4. it never widens authorization --------------------------------------

def test_foreign_pid_lease_is_never_touched(qubit_home):
    _write_entries(qubit_home, [_lease(9999, "dashboard-someone-else")])

    session = _FakeSession("tok-1", 4242, attached=False)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=0.05
    )

    async def scenario():
        registry.detach("tok-1", ws=None)
        await asyncio.sleep(0.2)

    asyncio.run(scenario())

    assert [e["pid"] for e in _read_entries(qubit_home)] == [9999]


def test_registry_without_session_lease_is_a_noop(qubit_home):
    """No lease registered for this child: nothing written, nothing raised."""
    _write_entries(qubit_home, [])

    session = _FakeSession("tok-1", 4242, attached=False)
    registry = pty_lease_shim.LeaseReleasingPtyRegistry(
        _FakeRegistry({"tok-1": session}), detach_release_grace_s=0.05
    )

    async def scenario():
        registry.detach("tok-1", ws=None)
        await asyncio.sleep(0.2)

    asyncio.run(scenario())

    assert _read_entries(qubit_home) == []


def test_wrap_registry_is_idempotent():
    inner = _FakeRegistry({})
    wrapped = pty_lease_shim.wrap_registry(inner)
    assert isinstance(wrapped, pty_lease_shim.LeaseReleasingPtyRegistry)
    assert pty_lease_shim.wrap_registry(wrapped) is wrapped
