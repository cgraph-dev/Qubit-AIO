"""Release dashboard PTY children's active-session leases (Qubit fork shim).

Why this exists
---------------
``active_sessions.py`` fences a stored session id to ONE live writer, keyed
``(pid, live_session_id)``. The dashboard embeds a real ``qubit --tui`` child
over ``/api/pty``, and that child is the writer once it has run a turn.

A browser refresh does NOT end that child. ``chat_ws.py``'s ``/api/pty``
finally block only detaches::

    # Detach only — the PTY keeps running for a reattach
    PTY_REGISTRY.detach(attach_token, ws)

and ``pty_attach_token.ts`` reuses the tab's stored ``?attach=`` token on a
plain reload, so the SAME child is reattached. It keeps its lease under
``surface='tui'`` for up to ``ttl`` (30 min) or until process exit.

Meanwhile the refreshed page's sidecar mints a NEW ``dashboard-*`` session id
and claims on the first prompt. It is a different writer (different pid AND a
different ``live_session_id``), so ``try_acquire_active_session`` refuses with
``SESSION_NOT_OWNED``::

    This chat is open in another Qubit window/terminal.

and ``_take_over_detached_runtime_lease`` cannot rescue it: that takeover scans
``_sessions.items()`` — the CALLING process's own records — so a lease held by
a DIFFERENT process (the PTY child) is structurally invisible to it
(``session_lifecycle.py:145-155``). No amount of waiting on the same-process
path ever frees it.

Upstream parity
---------------
This is NOT a Qubit regression. ``active_sessions.py``,
``tui_gateway/session_lifecycle.py``, ``tui_gateway/session_reaper.py`` and
``tui_gateway/ws.py`` are byte-identical to the upstream pin (modulo the
qubit->hermes rename), so upstream has the same cross-process blind spot and
offers no mechanism to port. This module is therefore a deliberately small
fork-local shim in ``voicebridge/`` rather than a patch to the vendored tree.

What it does
------------
Wraps ``PtySessionRegistry`` so a PTY child's lease is released when the child
can no longer be the live writer:

* on ``close()`` (TTL reaper, LRU reap, ``close_all``) — the child is gone, so
  its lease must not keep fencing a session nobody is editing;
* on ``detach()`` that is not followed by a reattach inside a short grace — the
  refreshed tab gets the session back instead of racing the 30-minute TTL.

Both paths are strictly subtractive: they only ever REMOVE a lease whose owner
pid is the PTY child we just detached or closed. Nothing is granted, no
authorization is widened, and no lease belonging to another surface or a live
attached viewer is touched.

Identification: the lease entry carries ``pid``, and ``PtyBridge.pid`` is the
child's pid (``pty_bridge.py:110``). Matching on pid is exact — no argv
heuristics, per the root process-identity rule.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

#: How long a detached PTY child may keep its lease before it is released. Long
#: enough that a reattach (a slow reload, a restored tab) still finds its own
#: session; short enough that a stuck tab does not fence the user for the PTY
#: TTL (30 min). The child keeps RUNNING either way — this only drops its claim.
DEFAULT_DETACH_RELEASE_GRACE_S = 45.0

#: Prefix of the per-channel active-session file the PTY child writes its
#: stored session id into (``web_server_chat.py::_active_session_file_for_channel``).
_ACTIVE_SESSION_ENV_KEYS = ("QUBIT_TUI_ACTIVE_SESSION_FILE", "HERMES_TUI_ACTIVE_SESSION_FILE")


def _child_pid(session: Any) -> Optional[int]:
    """The PTY child's pid, or None when it cannot be read. Never raises."""
    bridge = getattr(session, "bridge", None)
    try:
        pid = getattr(bridge, "pid", None)
    except Exception:
        return None
    try:
        pid_int = int(str(pid))
    except (TypeError, ValueError):
        return None
    return pid_int if pid_int > 0 else None


def _registry_home() -> Optional[Path]:
    """The profile home whose registry this process writes. None on failure."""
    try:
        from qubit_constants import get_qubit_home

        return Path(get_qubit_home())
    except Exception:
        return None


def _live_lease_ids_for_pids(pids: set[int]) -> list[str]:
    """Lease ids in the registry currently owned by any pid in ``pids``.

    Read-only and best-effort: an unreadable registry yields no ids, so the
    caller simply does nothing (the normal reclaim paths still run).
    """
    if not pids:
        return []
    home = _registry_home()
    if home is None:
        return []
    try:
        from qubit_cli.active_sessions import _read_entries, _state_path

        entries = _read_entries(_state_path(home))
    except Exception:
        log.debug("pty lease shim: registry unreadable; skipping release", exc_info=True)
        return []
    found: list[str] = []
    for entry in entries:
        try:
            pid = int(entry.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid in pids:
            lease_id = str(entry.get("lease_id") or "")
            if lease_id:
                found.append(lease_id)
    return found


def release_leases_for_sessions(sessions: list[Any], *, reason: str) -> int:
    """Drop the active-session leases owned by these PTY children. Returns count.

    Only leases whose ``pid`` matches a supplied child are removed, and removal
    happens under the registry's own file lock via ``_drop_lease``, so a lease
    acquired between the read and the write is never lost.
    """
    pids = {pid for pid in (_child_pid(s) for s in sessions) if pid is not None}
    if not pids:
        return 0
    lease_ids = _live_lease_ids_for_pids(pids)
    if not lease_ids:
        return 0
    home = _registry_home()
    if home is None:
        return 0
    dropped = 0
    try:
        from qubit_cli.active_sessions import _FileLock, _drop_lease, _lock_path, _read_entries, _state_path

        state_path, lock_path = _state_path(home), _lock_path(home)
        with _FileLock(lock_path):
            entries = _read_entries(state_path)
            kept = entries
            for lease_id in lease_ids:
                kept = _drop_lease(state_path, kept, lease_id)
            dropped = len(entries) - len(kept)
    except Exception:
        log.debug("pty lease shim: release failed (%s)", reason, exc_info=True)
        return 0
    if dropped:
        log.info(
            "pty lease shim: released %d active-session lease(s) for %s (pids=%s)",
            dropped, reason, sorted(pids),
        )
    return dropped


class LeaseReleasingPtyRegistry:
    """``PtySessionRegistry`` wrapper that frees its children's session leases.

    Delegates everything to the wrapped registry and adds lease release at the
    two points where a PTY child stops being the session's live writer. Kept as
    a wrapper (not a subclass) so it can never drift from a vendored-tree change
    to the registry: unknown attributes pass straight through.
    """

    def __init__(self, inner: Any, *, detach_release_grace_s: float = DEFAULT_DETACH_RELEASE_GRACE_S):
        self._inner = inner
        self._detach_grace_s = max(0.0, float(detach_release_grace_s))
        self._pending_detach: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # -- passthrough -------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"LeaseReleasingPtyRegistry(grace={self._detach_grace_s}s, inner={self._inner!r})"

    # -- wrapped lifecycle -------------------------------------------------
    def detach(self, key: str, ws: Any) -> None:
        session = self._inner._sessions.get(key)
        self._inner.detach(key, ws)
        # Only arm the release when the detach actually took effect (the child's
        # ``detach`` ignores a superseded socket) AND nothing is still attached:
        # a live second tab keeps the session, exactly as the PTY layer decides.
        if session is None or getattr(session, "attached", False):
            self._cancel_pending(key)
            return
        if self._detach_grace_s <= 0:
            release_leases_for_sessions([session], reason=f"pty detach {key}")
            return
        self._arm_detach_release(key, session)

    async def close_session(self, key: str, session: Any) -> None:
        """Close ``session`` (already popped by the caller) and free its lease."""
        try:
            await session.close()
        finally:
            release_leases_for_sessions([session], reason=f"pty close {key}")

    async def reap_idle(self, now: Optional[float] = None) -> None:
        # Snapshot the doomed sessions so their leases can be released once the
        # inner reap has closed them. reap_idle pops then awaits close(), so the
        # list must be captured BEFORE the call.
        doomed: list[tuple[str, Any]] = []
        try:
            sessions = getattr(self._inner, "_sessions", {})
            now_m = time.monotonic() if now is None else now
            ttl = float(getattr(self._inner, "_ttl", 0.0) or 0.0)
            for key, session in list(sessions.items()):
                alive = getattr(session, "alive", True)
                attached = getattr(session, "attached", False)
                detached_at = getattr(session, "last_detached_at", None)
                idle_expired = detached_at is not None and (now_m - detached_at) > ttl
                if not alive or (not attached and idle_expired):
                    doomed.append((key, session))
        except Exception:
            doomed = []
        await self._inner.reap_idle(now)
        if doomed:
            self._cancel_pending_many({key for key, _ in doomed})
            release_leases_for_sessions([s for _, s in doomed], reason="pty idle reap")

    async def close_all(self, *, keep_detached: bool = False) -> None:
        before = []
        try:
            before = [(key, session) for key, session in list(getattr(self._inner, "_sessions", {}).items())
                      if not (keep_detached and not getattr(session, "attached", False))]
        except Exception:
            before = []
        await self._inner.close_all(keep_detached=keep_detached)
        if before:
            self._cancel_pending_many({key for key, _ in before})
            release_leases_for_sessions([s for _, s in before], reason="pty close_all")

    # -- detached-grace bookkeeping ----------------------------------------
    def _arm_detach_release(self, key: str, session: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (sync test / shutdown): release immediately rather
            # than leak the claim.
            release_leases_for_sessions([session], reason=f"pty detach {key} (no loop)")
            return

        self._cancel_pending(key)

        async def _release_after_grace() -> None:
            try:
                await asyncio.sleep(self._detach_grace_s)
            except asyncio.CancelledError:
                return
            # Reattach during the grace cancels this task; re-check anyway so a
            # race between cancel() and the sleep waking cannot drop a live
            # viewer's session.
            if getattr(session, "attached", False):
                return
            release_leases_for_sessions([session], reason=f"pty detach grace {key}")

        task = loop.create_task(_release_after_grace())
        self._pending_detach[key] = task
        task.add_done_callback(lambda _t, k=key: self._pending_detach.pop(k, None))

    def _cancel_pending(self, key: str) -> None:
        task = self._pending_detach.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_pending_many(self, keys: set[str]) -> None:
        for key in keys:
            self._cancel_pending(key)

    def attach_or_spawn_cancel_pending(self, key: str) -> None:
        """Call from an attach path so a reattach inside the grace is honored."""
        self._cancel_pending(key)


def wrap_registry(registry: Any, *, detach_release_grace_s: float = DEFAULT_DETACH_RELEASE_GRACE_S) -> Any:
    """Wrap a ``PtySessionRegistry`` for lease release; idempotent.

    ``detach_release_grace_s <= 0`` disables the detached-grace release while
    keeping the close-path release (the strictly-safe half).
    """
    if isinstance(registry, LeaseReleasingPtyRegistry):
        return registry
    try:
        from qubit_cli.config import load_config

        dashboard_cfg = (load_config() or {}).get("dashboard") or {}
        if isinstance(dashboard_cfg, dict) and "pty_lease_release_on_detach_s" in dashboard_cfg:
            detach_release_grace_s = float(dashboard_cfg.get("pty_lease_release_on_detach_s") or 0.0)
    except Exception:
        log.debug("pty lease shim: detach-grace config unreadable; using default", exc_info=True)
    return LeaseReleasingPtyRegistry(registry, detach_release_grace_s=detach_release_grace_s)
