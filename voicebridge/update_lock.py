"""Cross-process mutual exclusion for in-flight Qubit updates.

A single marker file under the Qubit state directory is the one lock every
update entrypoint (CLI, dashboard card, scheduled job, hand-off child) must hold
for the whole lifecycle. Two concurrent updaters would race the checkout swap
and the dependency/config migration, so a held lock is a typed refusal, never a
silent overwrite.

The marker format is line-oriented (``pid`` then a unix timestamp) and is
deliberately the same shape the reference updater uses, so an external
supervising process can read it. A live holder is adopted rather than refused
when it names our own hand-off parent (:data:`HANDOFF_PID_ENV`) or an ancestor
of this process — that is exactly the child an orchestrating updater spawned.

Every filesystem interaction is confined to the resolved lock path and is
best-effort on release, so a lock failure can never itself corrupt an update.
"""
from __future__ import annotations

import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# A full Qubit update (pull + deps + migration + restart) is minutes; past this
# ceiling a marker is stale and may be reclaimed.
UPDATE_MARKER_MAX_AGE_SECONDS = 20 * 60

MARKER_NAME = ".qubit-update-in-progress"

# Set by an orchestrating updater to its own pid before spawning the hand-off
# child; the child adopts the parent's claim instead of refusing its own lock.
HANDOFF_PID_ENV = "QUBIT_UPDATE_HANDOFF_PID"

# Typed code returned when another live update holds the lock.
UPDATE_LOCK_BUSY_CODE = "UPDATE_LOCK_BUSY"


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default) if hasattr(cfg, "get") else default
    except Exception:  # noqa: BLE001
        return default


def default_lock_dir() -> Path:
    """The Qubit state directory that owns the update marker.

    Resolution order: the product state dir (XDG-aware), then the Qubit runtime
    home, then a conservative XDG fallback. Never a repository path.
    """
    try:
        from . import product as P

        return Path(P.default_paths().state_dir)
    except Exception:  # noqa: BLE001
        try:
            from . import qubit_runtime

            return Path(qubit_runtime.default_home()).parent
        except Exception:  # noqa: BLE001
            base = os.environ.get("XDG_STATE_HOME") or str(
                Path.home() / ".local" / "state")
            return Path(base) / "qubit-voicebridge"


def lock_path(cfg: Any = None, *, home: Any = None) -> Path:
    """Resolve the shared update marker path (``update.lock_dir`` override wins)."""
    if home is not None:
        return Path(home) / MARKER_NAME
    override = str(_get(cfg, "update", "lock_dir", default="") or "").strip()
    base = Path(override).expanduser() if override else default_lock_dir()
    return base / MARKER_NAME


def _pid_alive(pid: int) -> bool:
    """Dependency-free liveness probe. Never raises."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The pid exists but belongs to another user.
        return True
    except OSError:
        return False
    return True


def _handoff_pid() -> Optional[int]:
    """Pid of the orchestrating updater that spawned us, or ``None``."""
    try:
        pid = int(os.environ.get(HANDOFF_PID_ENV, "").strip())
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _parent_pid(pid: int) -> Optional[int]:
    """Parent of ``pid`` without psutil (``/proc`` on Linux, ``ps`` elsewhere)."""
    try:
        if os.path.isdir("/proc"):
            with open(f"/proc/{pid}/stat", "rb") as fh:
                stat = fh.read()
            return int(stat[stat.rindex(b")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _is_ancestor_pid(pid: int) -> bool:
    """True when ``pid`` is a live ancestor of this process (our orchestrator)."""
    if pid <= 0 or pid == os.getpid():
        return False
    if pid == os.getppid():
        return True
    child = os.getpid()
    for _ in range(32):
        parent = _parent_pid(child)
        if parent is None:
            return False
        if parent == pid:
            return True
        if parent == child:
            return False
        child = parent
    return False


@dataclass(frozen=True)
class UpdateHolder:
    """A confirmed-live update currently holding the lock."""

    pid: int
    started_at: float
    age_seconds: float


def read_live_update(path: Optional[Path] = None) -> Optional[UpdateHolder]:
    """Return the live holder of the marker, or ``None``.

    Absent, unreadable, malformed, dead-pid, and past-the-ceiling all mean "no
    live update"; a stale marker is removed so it cannot strand future runs.
    Never raises.
    """
    marker = Path(path) if path is not None else lock_path()
    try:
        lines = marker.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    try:
        pid = int(lines[0].strip())
    except (IndexError, ValueError):
        pid = -1
    try:
        started_at = float(lines[1].strip())
    except (IndexError, ValueError):
        started_at = float("-inf")
    age = time.time() - started_at
    if not _pid_alive(pid) or age > UPDATE_MARKER_MAX_AGE_SECONDS:
        with suppress(OSError):
            marker.unlink()
        return None
    return UpdateHolder(pid=pid, started_at=started_at, age_seconds=age)


def describe_holder(holder: Optional[UpdateHolder]) -> str:
    """One-line, user-facing explanation of who holds the update lock."""
    if holder is None:
        return "another Qubit update is already running"
    minutes, seconds = divmod(int(max(holder.age_seconds, 0)), 60)
    elapsed = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    return (f"another Qubit update (process {holder.pid}) has been running for "
            f"{elapsed}")


class UpdateLock:
    """Context manager owning the shared update marker for this process.

    ``acquired`` is False when another live update holds it (``holder`` then
    names it). Release removes the marker only while this process still owns it,
    so a marker rewritten by a hand-off partner is never deleted from under its
    new owner. :meth:`transfer` hands the marker to the spawned hand-off child
    and makes this process's release a no-op, so the lock is never briefly
    unheld across the hand-off.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else lock_path()
        self.acquired = False
        self.holder: Optional[UpdateHolder] = None

    def acquire(self, *, adopt: bool = False) -> bool:
        """Claim the lock. Returns False (with ``holder`` set) when it is taken.

        ``adopt=True`` is the hand-off child's mode: the marker may already name
        this process (the parent transferred it) or the orchestrator that spawned
        us (:data:`HANDOFF_PID_ENV`) / an ancestor; either way the child claims
        ownership by writing its own pid, so it releases the marker when done. A
        genuinely foreign live holder is still refused.
        """
        existing = read_live_update(path=self.path)
        if existing is not None and existing.pid != os.getpid():
            ours = (existing.pid == _handoff_pid()
                    or _is_ancestor_pid(existing.pid))
            if not ours:
                self.holder = existing
                return False
            if not adopt:
                # Our own orchestrator's claim: run under it, leave its marker.
                return True
            # adopt: fall through and rewrite the marker to this process.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                f"{os.getpid()}\n{int(time.time())}\n", encoding="utf-8")
        except OSError:
            # Best-effort: an unwritable marker must not block the update
            # itself (worse than the race it would prevent).
            return True
        self.acquired = True
        return True

    def transfer_to(self, pid: Optional[int]) -> bool:
        """Cleanly hand the marker to the spawned hand-off child.

        Only the current owner may transfer: this process must hold the lock
        (``acquired``) *and* the on-disk marker must still name it. On success
        the marker is rewritten to the child pid (and the current time) and
        ``acquired`` is cleared so the parent's later :meth:`release` is a
        no-op — the update therefore stays locked across the hand-off, with no
        window where neither process owns it.

        A caller that cannot be trusted (not the holder) or an unknown child pid
        returns False **without** touching the marker or clearing ``acquired``,
        so the lock is not silently dropped and no stale marker is left. Never
        raises.
        """
        if not self.acquired:
            return False
        try:
            child = int(pid) if pid is not None else 0
        except (TypeError, ValueError):
            return False
        if child <= 0:
            return False
        try:
            owner = int(self.path.read_text(
                encoding="utf-8-sig").splitlines()[0].strip())
        except (OSError, IndexError, ValueError):
            return False
        if owner != os.getpid():
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                f"{child}\n{int(time.time())}\n", encoding="utf-8")
        except OSError:
            return False
        self.acquired = False
        return True

    def transfer(self, pid: Optional[int]) -> bool:
        """Backwards-compatible alias for :meth:`transfer_to`."""
        return self.transfer_to(pid)

    def release(self) -> None:
        """Drop the marker if this process still owns it. Never raises."""
        if not self.acquired:
            return
        self.acquired = False
        try:
            owner = int(self.path.read_text(
                encoding="utf-8-sig").splitlines()[0].strip())
        except (OSError, IndexError, ValueError):
            return
        if owner != os.getpid():
            return  # a hand-off partner took ownership; still a live update
        with suppress(OSError):
            self.path.unlink()

    def __enter__(self) -> "UpdateLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


__all__ = [
    "UPDATE_MARKER_MAX_AGE_SECONDS", "MARKER_NAME", "HANDOFF_PID_ENV",
    "UPDATE_LOCK_BUSY_CODE", "UpdateHolder", "UpdateLock", "lock_path",
    "default_lock_dir", "read_live_update", "describe_holder",
]
