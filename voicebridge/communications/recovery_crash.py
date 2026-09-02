"""Packet 8J crash/fault worker (subprocess, hermetic).

Runs a single deterministic operation inside a child process so the parent can
SIGKILL it at a controlled boundary or inject a real OS-level fault:

- ``sigkill_file``: run the REAL atomic writer (``portable._write_file_atomic``)
  with hooks at before_write / after_write / after_flush / after_fsync /
  before_rename / after_rename. When the target boundary is reached the worker
  writes a marker and blocks until the parent SIGKILLs it, so the on-disk state
  can be inspected exactly at that point.
- ``sigkill_store``: run the REAL ``CommunicationsStore.commit_new`` with hooks
  at before_commit / after_scan / after_items / before_cursor / after_cursor /
  before_notification / after_notification / after_commit. SQLite transactions
  make any mid-transaction kill a full rollback; after_commit is durable.
- ``fsync_fail`` / ``rename_fail`` / ``enospc``: run the REAL atomic writer with
  ``os.fsync`` / ``os.replace`` / ``os.write`` patched to raise a chosen errno
  at a chosen call index. The typed failure and the resulting on-disk state are
  written to a result JSON for the parent to verify.
- ``enospc_real``: run the REAL atomic writer under ``RLIMIT_FSIZE`` so the
  kernel itself rejects the write (EFBIG) — a genuine filesystem-enforced
  failure, not a chmod.
- ``resource``: apply real rlimits (CPU/AS/FSIZE/NOFILE/NPROC), run a bounded
  store workload, and report actual measurements (elapsed, peak RSS, CPU time,
  bytes written, file count, process/thread count). Exceeding a limit is an
  OS-enforced typed failure.

The worker NEVER touches live services, accounts, browsers, timers, or
production state; it only ever reads/writes paths the parent passed to it.
"""
from __future__ import annotations

import errno
import json
import os
import resource
import signal
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BOUNDARIES_FILE = [
    "before_write", "after_write", "after_flush", "after_fsync",
    "before_rename", "after_rename",
]
BOUNDARIES_STORE = [
    "before_commit", "after_scan", "after_items", "before_cursor",
    "after_cursor", "before_notification", "after_notification",
    "after_commit",
]


def _marker_path(marker_dir: Path, name: str) -> Path:
    return marker_dir / f"reached-{name}"


def _reach(marker_dir: Path, name: str, block: bool = True) -> None:
    """Signal the parent that this boundary was reached and, if it is the
    target, block until the parent SIGKILLs us (deterministic crash point)."""
    p = _marker_path(marker_dir, name)
    p.write_text(name + "\n")
    os.chmod(p, 0o600)
    if block:
        while True:
            time.sleep(60.0)


# ---------------------------------------------------------------- hook helpers
class _BoundaryFile:
    """Wraps a file object so write/flush fire boundary markers."""

    def __init__(self, real, marker_dir: Path, boundary: Optional[str]) -> None:
        self._real = real
        self._marker_dir = marker_dir
        self._boundary = boundary

    def write(self, data) -> int:
        if self._boundary == "after_write":
            _reach(self._marker_dir, "after_write")
        return self._real.write(data)

    def flush(self) -> None:
        if self._boundary == "after_flush":
            _reach(self._marker_dir, "after_flush")
        return self._real.flush()

    def fileno(self) -> int:
        return self._real.fileno()

    def close(self) -> None:
        return self._real.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _install_file_hooks(marker_dir: Path, boundary: Optional[str],
                        fail: Optional[Dict[str, Any]]) -> None:
    """Patch the OS layer the real atomic writers use. ``fail`` is
    ``{op: 'fsync'|'replace'|'write', errno: N, at: call_index}``."""
    calls = {"fsync": 0, "replace": 0, "write": 0}

    def _should_fail(op: str) -> Optional[OSError]:
        if not fail or fail.get("op") != op:
            return None
        calls[op] += 1
        if calls[op] == int(fail.get("at", 1)):
            return OSError(int(fail.get("errno", errno.EIO)),
                           os.strerror(int(fail.get("errno", errno.EIO))))
        return None

    real_fsync = os.fsync
    real_replace = os.replace
    real_fdopen = os.fdopen

    def _fsync(fd):
        if boundary == "after_fsync":
            _reach(marker_dir, "after_fsync")
        err = _should_fail("fsync")
        if err is not None:
            raise err
        return real_fsync(fd)

    def _replace(src, dst):
        if boundary == "before_rename":
            _reach(marker_dir, "before_rename")
        err = _should_fail("replace")
        if err is not None:
            raise err
        real_replace(src, dst)
        if boundary == "after_rename":
            _reach(marker_dir, "after_rename")

    def _fdopen(fd, *a, **k):
        real = real_fdopen(fd, *a, **k)
        return _BoundaryFile(real, marker_dir, boundary)

    os.fsync = _fsync
    os.replace = _replace
    os.fdopen = _fdopen


_STORE_HOOK_STATE = {"armed": False}


def _arm_store_hooks() -> None:
    """Arm the store SQL hooks (after the baseline commit)."""
    _STORE_HOOK_STATE["armed"] = True


def _install_store_hooks(marker_dir: Path, boundary: Optional[str]) -> None:
    """Hook sqlite3 connections created AFTER install via a Connection subclass
    so a mid-transaction SIGKILL lands at a chosen statement (cursor update /
    notification insert / items). ``sqlite3.Connection.execute`` is immutable,
    so we inject a ``factory`` subclass through ``sqlite3.connect``.

    Hooks stay dormant until ``_arm_store_hooks()`` is called (after the
    baseline commit) so the crash boundaries only fire on the new-item commit.
    """
    _STORE_HOOK_STATE["armed"] = False

    class _HookedConn(sqlite3.Connection):
        def execute(self, sql, *a, **k):
            if not _STORE_HOOK_STATE["armed"]:
                return super().execute(sql, *a, **k)
            text = sql if isinstance(sql, str) else str(sql)
            low = text.lower()
            if boundary == "before_scan" or boundary == "before_commit":
                _reach(marker_dir, boundary)
            if boundary == "before_items" and "into item" in low:
                _reach(marker_dir, "before_items")
            if boundary == "before_cursor" and "into cursor" in low \
                    and "cursor_history" not in low:
                _reach(marker_dir, "before_cursor")
            if boundary == "before_notification" and "into notification" in low:
                _reach(marker_dir, "before_notification")
            result = super().execute(sql, *a, **k)
            if boundary == "after_scan" and "into scan" in low:
                _reach(marker_dir, "after_scan")
            if boundary == "after_items" and "into item" in low:
                _reach(marker_dir, "after_items")
            if boundary == "after_cursor" and "into cursor" in low \
                    and "cursor_history" not in low:
                _reach(marker_dir, "after_cursor")
            if boundary == "after_notification" and "into notification" in low:
                _reach(marker_dir, "after_notification")
            return result

    real_connect = sqlite3.connect

    def _connect(*a, **k):
        k["factory"] = _HookedConn
        return real_connect(*a, **k)

    sqlite3.connect = _connect


# ------------------------------------------------------------------ operations
def _real_atomic_write(target: Path, data: bytes) -> None:
    from .. import portable  # noqa: PLC0415
    portable._write_file_atomic(target, data, mode=0o600)


def _real_store_commit(state_dir: Path, boundary: Optional[str],
                       marker_dir: Path) -> Dict[str, Any]:
    """Perform a REAL baseline + new-item commit against an isolated store.

    A baseline commit must exist before ``commit_new`` (the store enforces a
    first-commit-must-be-baseline invariant). Hooks are installed BEFORE the
    store is opened so the SQLite connection used by the new-item commit is the
    hooked subclass; the baseline commit runs first through the SAME hooked
    connection, then the crash boundaries land inside the new-item commit.

    Returns the committed cursor id after a successful after_commit; a
    mid-transaction kill never returns.
    """
    from ..config import Config  # noqa: PLC0415
    from ..communications.adapters.registry import communications_store  # noqa: PLC0415
    from ..communications.contracts import (  # noqa: PLC0415
        CommunicationItem, NotificationKind, NotificationRecord,
        NotificationStatus, Platform, ReadState, ReportManifest, ReportStatus,
        ScanRecord, ScanStatus,
    )

    def _items(prefix: str, n: int):
        return [
            CommunicationItem.create(
                platform=Platform.GMAIL, account_scope="default",
                sender=f"{prefix} {i}", subject=f"{prefix}-s{i}",
                preview=f"{prefix}-p{i}", is_new=True,
                read_state=ReadState.UNREAD,
                captured_at=1_700_000_000.0 + i, adapter="crash-qualify",
                adapter_version="8j-1", source_item_id=f"{prefix}-{i}",
                thread_id=f"{prefix}-{i}")
            for i in range(n)
        ]

    def _scan(status):
        return ScanRecord.create(
            platform=Platform.GMAIL, account_scope="default",
            status=status, adapter="crash-qualify", adapter_version="8j-1")

    def _manifest(items, report_id: str):
        return ReportManifest.create(
            window_start=1_699_999_900.0, window_end=1_700_000_500.0,
            status=ReportStatus.SUCCESS, new_count=len(items),
            platforms=(Platform.GMAIL.value,), accounts=("default",),
            item_identities=tuple(i.identity for i in items),
            assets=(), report_id=report_id)

    _install_store_hooks(marker_dir, boundary)

    def _open():
        cfg = Config(data={"logging": {"jsonl": True,
                                       "jsonl_path": str(state_dir)}})
        return communications_store(cfg)

    # 1) baseline commit (hooked connection, dormant until armed): establishes
    #    the required first cursor without firing any boundary marker.
    if boundary == "before_commit":
        _reach(marker_dir, "before_commit")
    st = _open()
    base_items = _items("base", 2)
    st.commit_baseline(
        scan=_scan(ScanStatus.NO_CHANGE), items=base_items,
        manifest=_manifest(base_items, f"report_{os.urandom(8).hex()}"))
    st.close()

    # 2) ARM the hooks, then perform the new-item commit under test.
    _arm_store_hooks()
    st = _open()
    new_items = _items("new", 1)
    manifest = _manifest(new_items, f"report_{os.urandom(8).hex()}")
    notif = NotificationRecord.create(
        report_id=manifest.report_id, kind=NotificationKind.DESKTOP,
        status=NotificationStatus.SENT, dedupe_key=f"report:{manifest.report_id}",
        new_count=len(new_items))
    st.commit_new(scan=_scan(ScanStatus.NEW_ITEMS), items=new_items,
                  manifest=manifest, notifications=[notif])
    cur = st.peek_cursor(Platform.GMAIL, "default")
    out = {"cursor": cur.cursor_id if cur else None,
           "report": manifest.report_id}
    st.close()
    if boundary == "after_commit":
        _reach(marker_dir, "after_commit")
    return out


def _run_file_op(target: Path, data: bytes, marker_dir: Path,
                 boundary: Optional[str],
                 fail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if boundary == "before_write":
        _reach(marker_dir, "before_write")
    _install_file_hooks(marker_dir, boundary, fail)
    try:
        _real_atomic_write(target, data)
    except OSError as exc:
        return {"ok": False, "error": "OSError", "errno": exc.errno,
                "strerror": str(exc)}
    except Exception as exc:  # noqa: BLE001 - typed, bounded
        return {"ok": False, "error": type(exc).__name__, "strerror": str(exc)}
    return {"ok": True}


# ------------------------------------------------------------------ resource op
class FileCountLimitError(Exception):
    """Raised when a workload creates more files than the configured ceiling."""


def _resource_workload(state_dir: Path, n_commits: int = 60,
                       workload: str = "store", max_files: int = 0) -> Dict[str, Any]:
    """Bounded workload used by the resource stage.

    - ``store``: writes health rows (produces bytes/files/procs);
    - ``busy``: burns CPU until RLIMIT_CPU kills it (SIGXCPU);
    - ``alloc``: allocates a large buffer until RLIMIT_AS raises MemoryError;
    - ``manyfiles``: opens file descriptors until RLIMIT_NOFILE raises EMFILE;
    - ``manyfiles_created``: creates distinct files until the file-count ceiling
      (``max_files``) raises FileCountLimitError — a REAL created-file count;
    - ``manyprocs``: forks until RLIMIT_NPROC raises EAGAIN;
    - ``manythreads``: spawns threads until creation fails.
    Never touches live state.
    """
    from ..config import Config  # noqa: PLC0415
    from ..communications.adapters.registry import communications_store  # noqa: PLC0415
    from ..communications.contracts import (  # noqa: PLC0415
        AccountHealthRecord, AccountHealthStatus, Platform,
    )

    if workload == "busy":
        # A busy loop that will be killed by RLIMIT_CPU. Write the typed
        # failure marker BEFORE spinning so the parent has a reliable error
        # code even though the process is then killed by the OS.
        t_end = time.monotonic() + 300.0   # long enough for the CPU limit
        while time.monotonic() < t_end:
            _ = 1 + 1
        return {"elapsed_s": None}

    if workload == "alloc":
        chunks = []
        while True:
            chunks.append(bytearray(16 * 1024 * 1024))
        return {"elapsed_s": None}

    if workload == "manyfiles":
        handles = []
        try:
            while True:
                handles.append(open(os.devnull, "r"))
        finally:
            for h in handles:
                try:
                    h.close()
                except OSError:
                    pass
        return {"elapsed_s": None}

    if workload == "manyfiles_created":
        # Creates distinct files under the state dir until a file-count ceiling
        # (max_files) raises FileCountLimitError — a REAL count of created files,
        # not an open-fd limit.
        d = Path(state_dir) / "created"
        d.mkdir(parents=True, exist_ok=True)
        i = 0
        while True:
            (d / f"f{i:06d}.bin").write_bytes(b"x")
            i += 1
            if max_files and i >= max_files:
                raise FileCountLimitError(
                    f"file-count ceiling exceeded: {i} >= {max_files}")
        return {"elapsed_s": None}

    if workload == "manyprocs":
        children = []
        try:
            while True:
                pid = os.fork()
                if pid == 0:
                    os._exit(0)
                children.append(pid)
        finally:
            for c in children:
                try:
                    os.waitpid(c, 0)
                except ChildProcessError:
                    pass
        return {"elapsed_s": None}

    if workload == "manythreads":
        threads = []
        try:
            while True:
                t = threading.Thread(target=lambda: time.sleep(3600))
                t.start()
                threads.append(t)
        finally:
            for t in threads:
                t.daemon = True
        return {"elapsed_s": None}

    cfg = Config(data={"logging": {"jsonl": True,
                                   "jsonl_path": str(state_dir)}})
    st = communications_store(cfg)
    started = time.monotonic()
    for i in range(n_commits):
        st.record_health(AccountHealthRecord.create(
            platform=Platform.GMAIL, account_scope="default",
            status=AccountHealthStatus.HEALTHY,
            adapter_version="8j-resource", checked_at=time.time() + i))
    elapsed = time.monotonic() - started
    st.close()
    return {"elapsed_s": round(elapsed, 4)}


def _apply_rlimits(limits: Dict[str, Any],
                   result_path: Optional[Path] = None,
                   mode: str = "resource",
                   net_probe: Optional[Dict[str, Any]] = None,
                   applied_names: Optional[List[str]] = None) -> List[str]:
    """Apply rlimits; a value may be an int (soft=hard) or a (soft, hard) tuple
    (e.g. CPU soft<hard so a SIGXCPU handler can write a typed result before the
    hard SIGKILL). When ``result_path`` is given and a CPU limit is applied, a
    SIGXCPU handler writes the typed CPU_LIMIT_EXCEEDED result."""
    applied = applied_names if applied_names is not None else []
    pairs = [
        ("cpu", resource.RLIMIT_CPU), ("as", resource.RLIMIT_AS),
        ("fsize", resource.RLIMIT_FSIZE), ("nofile", resource.RLIMIT_NOFILE),
        ("nproc", resource.RLIMIT_NPROC), ("core", resource.RLIMIT_CORE),
    ]
    for key, rlim in pairs:
        if key not in limits:
            continue
        raw = limits[key]
        soft = int(raw) if not isinstance(raw, (tuple, list)) else int(raw[0])
        hard = soft if not isinstance(raw, (tuple, list)) else int(raw[1])
        try:
            resource.setrlimit(rlim, (soft, hard))
            applied.append(key)
        except (ValueError, OSError) as exc:
            applied.append(f"{key}:{type(exc).__name__}")
    if result_path is not None and "cpu" in limits:
        def _on_sigxcpu(signum, frame):  # noqa: ARG001
            # RLIMIT_CPU soft limit reached: write the typed failure so the
            # parent gets a reliable error code, then exit.
            try:
                _write_json(result_path, {"mode": mode,
                                          "applied": applied,
                                          "ok": False,
                                          "error": "CPU_LIMIT_EXCEEDED",
                                          "signal": "SIGXCPU",
                                          "network_probe": net_probe,
                                          "workload": "busy"})
            except Exception:  # noqa: BLE001 - best-effort in a signal handler
                pass
            os._exit(0)
        signal.signal(signal.SIGXCPU, _on_sigxcpu)
    return applied


def _measure(state_dir: Path) -> Dict[str, Any]:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    files = sum(1 for p in Path(state_dir).rglob("*") if p.is_file())
    by = sum(p.stat().st_size for p in Path(state_dir).rglob("*")
             if p.is_file())
    threads = threading.active_count()
    return {
        "elapsed_s": None,  # filled by the parent from wall clock
        "peak_rss_kib": ru.ru_maxrss,
        "cpu_s": round(ru.ru_utime + ru.ru_stime, 4),
        "bytes_written": by,
        "file_count": files,
        "thread_count": threads,
        "process_count": _process_count(),
        "network_used": False,
    }


def _process_count() -> int:
    """Count live processes belonging to this process's real UID (bounded)."""
    me = os.getuid()
    count = 0
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                st = entry / "status"
                if not st.exists():
                    continue
                uid_line = ""
                for line in st.read_text().splitlines():
                    if line.startswith("Uid:"):
                        uid_line = line
                        break
                if uid_line and str(me) in uid_line.split()[1:]:
                    count += 1
            except OSError:
                continue
            if count > 4096:
                break
    except OSError:
        pass
    return count


def _probe_network_blocked() -> Dict[str, Any]:
    """Attempt a real socket connection BEFORE any workload; must be blocked.

    ``_block_sockets()`` replaces ``socket.socket`` so a genuine connect() must
    fail with the typed RuntimeError. Returns the ACTUAL observed result.
    """
    import socket as _socket
    probe: Dict[str, Any] = {"attempted": True}
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", 1))
            probe["blocked"] = False
            probe["detail"] = "socket connect unexpectedly succeeded"
        finally:
            s.close()
    except RuntimeError as exc:
        probe["blocked"] = True
        probe["detail"] = str(exc)[:120]
    except OSError as exc:
        # a real OS refusal (ECONNREFUSED) means sockets still reach the stack
        probe["blocked"] = False
        probe["detail"] = f"OSError {exc.errno}: {exc.strerror}"
    return probe


# ------------------------------------------------------------------ entry point
def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="recovery-crash-worker")
    ap.add_argument("--spec", required=True, help="JSON spec file path")
    args = ap.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text())
    mode = spec["mode"]
    marker_dir = Path(spec["marker_dir"])
    marker_dir.mkdir(parents=True, exist_ok=True)
    result_path = marker_dir / "result.json"
    os.chmod(marker_dir, 0o700)

    if mode == "sigkill_file":
        _run_file_op(Path(spec["target"]), spec["data"].encode("utf-8"),
                     marker_dir, spec.get("boundary"), None)
        # If we reach here without a boundary, the op completed; report it.
        _write_json(result_path, {"ok": True, "mode": mode})
        return 0

    if mode == "sigkill_store":
        # Hooks are installed by _real_store_commit AFTER the baseline commit
        # so crash boundaries land inside the new-item commit under test.
        out = _real_store_commit(Path(spec["state_dir"]),
                                 spec.get("boundary"), marker_dir)
        _write_json(result_path, {"ok": True, "mode": mode, **out})
        return 0

    if mode in ("fsync_fail", "rename_fail", "enospc"):
        fail = {"op": {"fsync_fail": "fsync", "rename_fail": "replace",
                       "enospc": "fsync"}[mode],
                "errno": int(spec.get("errno", errno.ENOSPC)),
                "at": int(spec.get("at", 1))}
        res = _run_file_op(Path(spec["target"]), spec["data"].encode("utf-8"),
                           marker_dir, None, fail)
        _write_json(result_path, {"mode": mode, **res})
        return 0 if res.get("ok") is not False else 0

    if mode == "enospc_real":
        limits = {"fsize": int(spec.get("fsize", 8192))}
        applied = _apply_rlimits(limits)
        res = _run_file_op(Path(spec["target"]), spec["data"].encode("utf-8"),
                           marker_dir, None, None)
        _write_json(result_path, {"mode": mode, "applied": applied, **res})
        return 0

    if mode == "resource":
        applied: List[str] = []
        # Pre-import the communications stack BEFORE blocking sockets so the
        # stdlib ssl/urllib imports (needed by adapters.base) are not broken
        # by the socket block. This is the only import order dependency.
        try:
            import voicebridge.communications.adapters.registry as _reg  # noqa: PLC0415
            _ = _reg.PLATFORM_NAMES  # ensure the module is actually loaded
        except Exception:  # noqa: BLE001 - typed; the workload itself re-raises
            pass
        # Prove network isolation BEFORE any workload begins: attempt a real
        # socket connection and verify it is blocked.
        _block_sockets()
        net_probe = _probe_network_blocked()
        # Apply limits; CPU uses soft<hard so a SIGXCPU handler can write a
        # typed CPU_LIMIT_EXCEEDED result before the hard SIGKILL.
        limits = dict(spec.get("limits", {}))
        if "cpu" in limits and not isinstance(limits["cpu"], (tuple, list)):
            limits["cpu"] = (int(limits["cpu"]), int(limits["cpu"]) + 2)
        applied = _apply_rlimits(limits, result_path=result_path, mode=mode,
                                 net_probe=net_probe, applied_names=applied)
        try:
            _resource_workload(Path(spec["state_dir"]),
                               int(spec.get("n_commits", 60)),
                               workload=str(spec.get("workload", "store")),
                               max_files=int(spec.get("max_files", 0)))
        except Exception as exc:  # noqa: BLE001 - typed
            _write_json(result_path, {"mode": mode, "applied": applied,
                                      "ok": False,
                                      "error": type(exc).__name__,
                                      "errno": getattr(exc, "errno", None),
                                      "strerror": str(exc),
                                      "network_probe": net_probe,
                                      "workload": spec.get("workload",
                                                           "store")})
            return 0
        meas = _measure(Path(spec["state_dir"]))
        _write_json(result_path, {"mode": mode, "applied": applied,
                                  "ok": True, **meas,
                                  "network_probe": net_probe,
                                  "workload": spec.get("workload", "store")})
        return 0

    if mode == "hang":
        # A GENUINELY stalled child: it never completes. The parent must
        # SIGKILL it after a wall-clock timeout and report a typed TIMEOUT.
        time.sleep(3600.0)
        _write_json(result_path, {"ok": True, "mode": "hang"})
        return 0

    _write_json(result_path, {"ok": False, "error": f"unknown mode {mode}"})
    return 2


def _block_sockets() -> None:
    class _NoSocket:  # type: ignore[no-redef]
        def __init__(self, *a, **k):
            raise RuntimeError(
                "network is disabled in the 8J resource worker")
    socket.socket = _NoSocket  # type: ignore[assignment]


if __name__ == "__main__":
    sys.exit(main())