"""Cross-process mutual exclusion for in-flight Qubit updates.

The marker file the Tauri updater writes (``UpdateMarkerGuard`` in
``apps/bootstrap-installer/src-tauri/src/update.rs``) and the Electron desktop reads
(``electron/update-marker.ts``) is the single lock for **all** update entrypoints.
Format and location are byte-compatible with both readers.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Keep in sync with UPDATE_MARKER_MAX_AGE_MS in apps/desktop/electron/update-marker.ts:
# a shorter ceiling here would let Python steal a lock Electron still considers live.
# A full update (git pull + uv sync + desktop rebuild) is minutes.
UPDATE_MARKER_MAX_AGE_SECONDS = 20 * 60

MARKER_NAME = ".hermes-update-in-progress"

# Set by an orchestrating updater (Tauri `hermes-setup --update`) to its own pid before
# spawning `qubit update` as a child stage; the parent holds the marker for its whole run,
# so without this the child would refuse its own parent's lock. Keep in sync with
# update_child_env in apps/bootstrap-installer/src-tauri/src/update.rs.
HANDOFF_PID_ENV = "HERMES_UPDATE_HANDOFF_PID"

# Bound on the parent chain walked by _is_ancestor_pid. Real ancestries are a
# handful of links (init -> desktop -> staged updater -> shim -> us); the cap
# only exists so an unexpected chain can never spin the walk.
_MAX_ANCESTRY_DEPTH = 128

# Bound on the parent chain walked by _is_ancestor_pid. Real ancestries are a
# handful of links (init -> desktop -> staged updater -> shim -> us); the cap
# only exists so an unexpected chain can never spin the walk.
_MAX_ANCESTRY_DEPTH = 128

# Exit code meaning "another updater/instance owns this install right now" — the same
# contract as the Windows shim / venv-holder guards in _cmd_update_impl, matched by the
# Tauri updater (UPDATE_EXIT_CONCURRENT in update.rs) to show "Qubit is still running".
UPDATE_EXIT_CONCURRENT = 2

# Checkout lock file NAMES: a cross-language contract shared byte-for-byte with the Rust
# installer (``apps/bootstrap-installer/src-tauri/src/marker.rs``) and the Electron updater
# (``electron/update-marker.ts`` / ``marker-helper.ts``). KEPT verbatim -- renaming Python alone
# would let two updaters lock different paths and run concurrently against one checkout.
CHECKOUT_LOCK_NAME = ".hermes-update.lock"
GIT_CHECKOUT_LOCK_NAME = "hermes-update.lock"


def update_marker_path() -> Path:
    """Path of the shared update marker.

    Uses the *process* Qubit home (never the context-local profile override): the Rust
    updater resolves ``$QUBIT_HOME`` or the platform default and the desktop pins that same
    value into the updater's env, so a profile-scoped path would be one the other owners never look at.
    """
    from qubit_constants import get_process_qubit_home
    return get_process_qubit_home() / MARKER_NAME


def _pid_alive(pid: int) -> bool:
    """Use the dependency-free, Windows-safe probe before PM is available."""
    if pid <= 0:
        return False
    try:
        from qubit_cli._early_recovery import _pid_is_running
        return _pid_is_running(pid)
    except Exception as exc:
        logger.debug("Could not probe pid %s: %s", pid, exc)
        return False


def _handoff_pid() -> int | None:
    """Pid of the orchestrating updater that spawned us (:data:`HANDOFF_PID_ENV`); malformed
    values count as absent so a broken handoff falls back to the normal refusal."""
    try:
        pid = int(os.environ.get(HANDOFF_PID_ENV, "").strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def _windows_parent_pid(pid: int) -> int | None:
    """The parent of ``pid`` from a Toolhelp32 process snapshot (stdlib ctypes).

    Windows keeps a dead parent's pid in the snapshot and reuses pids, so, like
    psutil, a "parent" created after the child is a recycled pid, not our parent.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for walk in (kernel32.Process32FirstW, kernel32.Process32NextW):
        walk.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        walk.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def created(target: int) -> int | None:
        handle = kernel32.OpenProcess(0x1000, False, target)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)

    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    parent = None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32ProcessID == pid:
                parent = int(entry.th32ParentProcessID)
                break
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    if not parent:
        return None
    parent_created, child_created = created(parent), created(pid)
    if parent_created is not None and child_created is not None and parent_created > child_created:
        return None
    return parent


def _stdlib_parent_pid(pid: int) -> int | None:
    """The parent of ``pid`` without psutil, or ``None`` when unresolvable.

    The update-takeover child is spawned ``-I -S -B`` (qubit_cli/_old_updater.py) so
    psutil cannot import there — and that grandchild is exactly the process that most
    needs the two-hop ancestry walk to adopt the orchestrator's marker. /proc serves
    Linux; macOS keeps /proc absent, so shell out to ps once per hop; Windows has
    neither, so ask the Toolhelp32 snapshot.
    """
    if sys.platform == "win32":
        try:
            return _windows_parent_pid(pid)
        except (OSError, AttributeError, ValueError):
            return None
    try:
        if os.path.isdir("/proc"):
            with open(f"/proc/{pid}/stat", "rb") as fh:
                stat = fh.read()
        else:
            out = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=5,
            ).stdout
            value = int(out.strip() or -1)
            return value if value > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    # Field 4 (1-indexed) is ppid, but comm may contain spaces/parens: split
    # after the closing paren of comm instead of on whitespace.
    try:
        return int(stat[stat.rindex(b")") + 2:].split()[1])
    except (ValueError, IndexError):
        return None


def _is_ancestor_pid(pid: int) -> bool:
    """True when ``pid`` is a live ancestor of this process.

    The orchestrating updater spawns ``qubit update`` as a (grand)child, so a live marker
    owned by one of our ancestors can only be the claim we are already running under — an
    unrelated concurrent updater is never in our parent chain. This heals the fleet of staged
    ``qubit-setup`` binaries that predate the HANDOFF_PID_ENV export and can never send it.

    The chain is walked one link at a time and each ancestor is tested as it is
    discovered. ``psutil.Process.parents()`` cannot be used here: it builds the
    whole chain up to the lowest pid *before* returning, and its per-link
    ``parent()`` tolerates only ``NoSuchProcess``. So any process we may not
    inspect anywhere above us raises ``AccessDenied`` and discards the
    ancestors already collected — including the orchestrator one link down.
    That is not exotic: under firejail with ``ptrace_scope=1``, and in hardened
    containers, ``/proc/1`` is unreadable, so the GUI update deadlocked against
    its own parent on every attempt. Walking incrementally means a failure
    *above* the match can no longer hide it.

    Never includes our own pid, and any failure encountered before a match
    counts as "not an ancestor": an unprovable ancestry must fall back to the
    normal refusal.
    """
    if pid <= 0:
        return False
    if pid == os.getppid():
        return True
    try:
        import psutil

        proc = psutil.Process()
        seen = {proc.pid}
        for _ in range(_MAX_ANCESTRY_DEPTH):
            parent = proc.parent()
            if parent is None:
                return False
            if parent.pid == pid:
                return True
            if parent.pid in seen:
                # Defensive only: psutil's create_time check already rejects a
                # reused ppid, so a true cycle should be unreachable.
                return False
            seen.add(parent.pid)
            proc = parent
        logger.debug(
            "Gave up walking process ancestry for pid %s after %s links",
            pid,
            _MAX_ANCESTRY_DEPTH,
        )
        return False
    except ImportError:
        # -I -S -B takeover child: walk the same chain with stdlib probes.
        child = os.getpid()
        for _ in range(32):
            parent = _stdlib_parent_pid(child)
            if parent is None:
                return False
            if parent == pid:
                return True
            if parent == child:  # pid 1 re-parenting or a kernel loop guard
                return False
            child = parent
        return False
    except Exception as exc:
        logger.debug("Could not walk process ancestry for pid %s: %s", pid, exc)
        return False


@dataclass(frozen=True)
class UpdateHolder:
    """A confirmed-live update currently holding the lock."""

    pid: int
    age_seconds: float


def read_live_update(*, path: Path | None = None) -> UpdateHolder | None:
    """Return the live update holding the lock, or ``None``.

    Mirrors ``readLiveUpdateMarker`` in ``electron/update-marker.ts``: absent, unreadable,
    malformed, dead-pid, and past-the-ceiling all mean "no live update", and a stale marker
    file is deleted so it can't strand future runs. Never raises.
    """
    marker = path or update_marker_path()
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
    return UpdateHolder(pid=pid, age_seconds=age)


def describe_holder(holder: UpdateHolder | None) -> str:
    """One-line, user-facing explanation of who holds the update lock."""
    minutes, seconds = divmod(int(max(0 if holder is None else holder.age_seconds, 0)), 60)
    elapsed = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    who = f", process {holder.pid}" if holder else ""
    return (
        f"✗ Another Qubit update is already running (started {elapsed} ago{who}).\n"
        "\n"
        "  Running two at once would corrupt the install. Wait for it to finish\n"
        "  (watch `qubit logs`), or close the Desktop/dashboard window that\n"
        "  started it, then run `qubit update` again."
    )


class UpdateLock:
    """Context manager owning the shared update marker for this process.

    ``acquired`` is False when another live update holds it; callers decide between hard
    refusal (CLI/dashboard) and waiting. Release only removes the marker when *we* still own
    it, so a marker rewritten by a handoff partner (the Tauri updater writes its own pid) is
    never deleted from under its new owner.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self.path = path or update_marker_path()
        self.acquired = False
        self.holder: UpdateHolder | None = None

    def acquire(self) -> bool:
        """Claim the lock. Returns False (and sets ``holder``) if it's taken.

        A live holder whose pid matches :data:`HANDOFF_PID_ENV` — or is an ancestor of ours —
        is our own orchestrating parent: run under ITS claim and leave its marker untouched on
        release. The ancestry path covers staged updaters older than the env-var export.
        """
        existing = read_live_update(path=self.path)
        # A live claim naming our own pid is a killed update's marker whose pid this retry
        # inherited (containers restart pid numbering): no other live process has our pid, and
        # nothing pre-writes a marker for `qubit update` (it always runs under a parent's claim).
        # It is a new attempt, so it is claimed fresh like a dead holder's. Keeping the old
        # started_at would let the ceiling expire mid-run and admit a second updater.
        if existing is not None and existing.pid != os.getpid():
            if existing.pid == _handoff_pid() or _is_ancestor_pid(existing.pid):
                return True
            self.holder = existing
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(f"{os.getpid()}\n{int(time.time())}\n", encoding="utf-8")
        except OSError as exc:
            # Best-effort, like the Rust guard: an unwritable marker must not block the
            # update itself (worse than the race it prevents). Degrade to pre-lock behavior.
            logger.debug("Could not write update marker %s: %s", self.path, exc)
            return True
        self.acquired = True
        return True

    def release(self) -> None:
        """Drop the marker if this process still owns it. Never raises."""
        if not self.acquired:
            return
        self.acquired = False
        try:
            owner = int(self.path.read_text(encoding="utf-8-sig").splitlines()[0].strip())
        except (OSError, IndexError, ValueError):
            return
        if owner != os.getpid():
            return  # a handoff partner took ownership — still a live update
        with suppress(OSError):
            self.path.unlink()

    def __enter__(self) -> "UpdateLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


# --- the checkout lock (custody for the updater's children) --------------------------------
#
# Ported 1:1 from the milestone ``hermes_cli/update_lock.py`` (upstream ee8dd6c886). The checkout
# kernel lock keeps the install locked while ANY process of the update tree can still write it,
# and is handed to a child through ``pass_fds`` (POSIX) or the update's kill-on-close job (Windows).
# ``update_custody`` is the only consumer: it calls :func:`checkout_lock_fds`,
# :func:`custody_spawn_kwargs`, :func:`bind_child_to_update_tree`, :func:`resume_suspended_child`,
# :func:`_bind_to_kill_on_close_job`, :func:`update_tree_job`, :func:`_git_common_dir`.
#
# The lock-file NAMES below (``.hermes-update.lock`` / ``hermes-update.lock``) are a cross-language
# contract shared byte-for-byte with the Rust installer (``marker.rs``) and the Electron updater
# (``update-marker.ts`` / ``marker-helper.ts``). Renaming them in Python alone would let two
# updaters lock DIFFERENT paths and run concurrently against one checkout. KEPT verbatim.

# This process's hold on the checkout lock: {"path", "fd", "owned", "depth"}. "owned" means
# we opened and locked it; otherwise the fd was inherited from the `qubit update` that holds
# it (pass_fds) and belongs to the whole tree — it is never unlocked or closed here.
_HELD: dict | None = None
_JOBS: list = []


def _default_install_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git_common_dir(root: Path) -> Path | None:
    """The repository's common git dir, read from disk (no git process: -I -S children).

    ``.git`` is the dir itself, or a ``gitdir: <path>`` file (linked worktree, submodule) whose
    target may name the shared dir in ``commondir``. ``None`` when ``root`` is no checkout.
    """
    dot = root / ".git"
    try:
        if dot.is_dir():
            gitdir = dot
        elif dot.is_file():
            text = dot.read_text(encoding="utf-8-sig").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = root / text[len("gitdir:"):].strip()  # an absolute target replaces root
        else:
            return None
        common = gitdir / "commondir"
        if common.is_file():
            gitdir = gitdir / common.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return Path(os.path.normpath(gitdir))


def checkout_lock_path(install_root: Path | str | None = None) -> Path:
    root = Path(install_root or _default_install_root())
    common = _git_common_dir(root)
    return root / CHECKOUT_LOCK_NAME if common is None else common / GIT_CHECKOUT_LOCK_NAME


def _inherited_lock_fd(path: Path) -> int | None:
    """An fd this process inherited that holds the lock on ``path`` (POSIX ``pass_fds``)."""
    if sys.platform == "win32":
        return None
    try:
        target = os.stat(path)
        fd_dir = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
        candidates = [int(name) for name in os.listdir(fd_dir) if name.isdigit()]
    except OSError:
        return None
    import fcntl

    for fd in candidates:
        try:
            # pass_fds clears CLOEXEC in the exec'd child. Local opens (including the
            # concurrent status probe) retain it and must never donate their lifetime:
            # their owner may close the fd while our update still holds custody.
            if not os.get_inheritable(fd):
                continue
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) != (target.st_dev, target.st_ino):
                continue
            # Succeeds only when this open file description already holds the lock (or the
            # lock is free, in which case taking it on an fd we hold is still correct).
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            continue
    return None


def checkout_lock_fds(install_root: Path | str | None = None) -> tuple[int, ...]:
    """Fds a child of the update tree must inherit (``subprocess`` ``pass_fds``) so the
    checkout stays locked while it runs, even after its parent is killed. POSIX only."""
    if sys.platform == "win32":
        return ()
    if _HELD is not None:
        return () if _HELD["fd"] is None else (_HELD["fd"],)
    fd = _inherited_lock_fd(checkout_lock_path(install_root))
    return () if fd is None else (fd,)


def custody_spawn_kwargs() -> dict:
    """``subprocess`` kwargs that keep a checkout MUTATOR (git, the Node source build) in the
    update tree's custody (R2): POSIX children inherit the held lock fd, so the checkout stays
    locked until the last of them exits even if this process is killed. ``{}`` when this process
    holds no checkout lock, and on Windows (bind the Popen with bind_child_to_update_tree)."""
    if sys.platform == "win32" or _HELD is None or _HELD["fd"] is None:
        return {}
    return {"pass_fds": (_HELD["fd"],)}


CREATE_SUSPENDED = 0x00000004


def bind_child_to_update_tree(proc: subprocess.Popen) -> OSError | None:
    """Windows: put an update-tree child in a kill-on-close job owned by this process, so the
    child (and everything it spawns) dies when the lock owner dies and frees the lock.

    Create ``proc`` with :data:`CREATE_SUSPENDED` and resume it after the bind
    (:func:`resume_suspended_child`): it runs no instruction before, so nothing it starts can
    escape the job. The job allows breakaway: a process the tree starts
    with ``CREATE_BREAKAWAY_FROM_JOB`` (the gateways an update restarts or resumes) leaves it and
    outlives the update; every other descendant stays bound. POSIX children inherit the lock fd
    instead (:func:`checkout_lock_fds`). Returns ``None`` when bound, else the refusal (logged):
    the caller runs post-commit work, which must not fail over a weaker lock, so it records the
    refusal and runs the child, which joins the lock holding its own lease byte (R5b): the
    checkout stays busy until it exits, even after the owner's death.
    """
    if sys.platform != "win32":
        return None
    try:
        _bind_to_kill_on_close_job(proc)
    except OSError as exc:
        logger.warning("Could not bind update child %s to the update's job: %s", proc.pid, exc)
        return exc
    return None


def resume_suspended_child(proc: subprocess.Popen) -> None:
    """Windows: resume a :data:`CREATE_SUSPENDED` child (a no-op for a running one). A failed
    resume kills it and raises ``OSError``."""
    import ctypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    if ntdll.NtResumeProcess(int(proc._handle)) != 0:
        proc.kill()
        raise OSError(f"could not resume update child {proc.pid}")


def _bind_to_kill_on_close_job(proc: subprocess.Popen) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    if not kernel32.AssignProcessToJobObject(update_tree_job(), int(proc._handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def update_tree_job() -> int:
    """Windows: this process's kill-on-close job for the update tree (created once, never
    closed: the handle closes when this process dies, killing every process still in it)."""
    if _JOBS:
        return _JOBS[0]
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)  # unnamed, non-inheritable: only we hold it
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = _Extended()
    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
    limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    _JOBS.append(job)  # never closed: the handle closes when this process dies, killing the tree
    return job


# --- the marker mutex (A7 rule 1) -------------------------------------------------------------

MUTEX_SUFFIX = ".lock"
# Bounded wait for the sidecar: holders never run git/builds/network inside it.
MUTEX_WAIT_SECONDS = 10.0


class MarkerBusy(OSError):
    """The marker mutex stayed held past :data:`MUTEX_WAIT_SECONDS`: report busy."""


_MUTEX_HELD = threading.local()


def marker_mutex_path(path: Path) -> Path:
    """``<marker>.lock``: the sidecar every marker MUTATION holds a kernel lock on. Never deleted
    (deleting it would split the lock across two inodes)."""
    return path.with_name(path.name + MUTEX_SUFFIX)


def _windows_open_exclusive(path: Path):
    """The Windows sidecar mutex: the file opened with NO sharing (``FileShare.None`` in the
    PowerShell scripts, ``share_mode(0)`` in marker.rs). ``None`` = someone else has it open."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    invalid = ctypes.c_void_p(-1).value
    for access, disposition in ((0xC0000000, 4), (0x80000000, 3)):  # RW OPEN_ALWAYS; R OPEN_EXISTING
        handle = kernel32.CreateFileW(str(path), access, 0, None, disposition, 0x80, None)
        if handle and handle != invalid:
            return handle
        error = ctypes.get_last_error()
        if error == 32:  # ERROR_SHARING_VIOLATION: held
            return None
        if error != 5:  # anything but access denied: no read-only retry helps
            break
    raise ctypes.WinError(error)


def _windows_close(handle) -> None:
    import ctypes

    ctypes.WinDLL("kernel32").CloseHandle(handle)


def _mutex_acquire(path: Path, wait: float):
    deadline = time.monotonic() + wait
    if sys.platform == "win32":
        while True:
            handle = _windows_open_exclusive(path)
            if handle is not None:
                return handle
            if time.monotonic() >= deadline:
                raise MarkerBusy(f"{path} is held by another update")
            time.sleep(0.02)
    import fcntl

    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except PermissionError:
        fd = os.open(path, os.O_RDONLY)  # flock works on a read-only fd (root-owned sidecar)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MarkerBusy(f"{path} is held by another update") from None
                time.sleep(0.02)
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def marker_mutex(path: Path, *, wait: float = MUTEX_WAIT_SECONDS):
    """Hold the kernel lock on ``<marker>.lock`` (A7 rule 1): read -> judge -> mutate of the marker
    at ``path`` happens entirely inside. Re-entrant per thread; the kernel releases it if we die.
    Raises :class:`MarkerBusy` after ``wait`` seconds, ``OSError`` when it cannot be opened."""
    key = str(marker_mutex_path(path))
    held = getattr(_MUTEX_HELD, "keys", None)
    if held is None:
        held = _MUTEX_HELD.keys = {}
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    handle = _mutex_acquire(Path(key), wait)
    held[key] = 1
    try:
        yield
    finally:
        held.pop(key, None)
        if sys.platform == "win32":
            _windows_close(handle)
        else:
            os.close(handle)  # closing the only fd of this open file description drops the flock
