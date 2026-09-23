"""`hermes sessions set-journal-mode delete|wal` — the offline self-service path for #100896.

``database.journal_mode: delete`` can never self-apply on a store that is already WAL:
``apply_wal_with_fallback`` deliberately never live-downgrades (#68545 — other gateway/cron/worker connections
may hold uncheckpointed WAL commits). Before this command the only escape hatch was an undocumented hand-run
``PRAGMA journal_mode=DELETE``. This runs it under the same admission the maintenance paths use: refuse while
ANY foreign process holds the file or a sidecar (``foreign_state_db_holders``), flip without waiting out openers
(``_set_journal_mode_no_wait``), then verify the header bytes 18/19 SQLite writes for the mode.

Ported verbatim from the 0.21.4 reference ``hermes_cli/sessions_cmd_journal_mode.py`` (git 7ce1904); the only
change is ``hermes_*`` -> ``qubit_*`` imports plus the in-module ``_describe_holder_pid`` shim below, because
the vendored 0.21.1 ``qubit_state_holders`` predates the reference and does not carry
``describe_holder_pid`` (recorded as a hoist-to-holders follow-up).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

try:  # psutil is optional; /proc argv is the primary source on Linux.
    import psutil
except ImportError:  # pragma: no cover - stripped/scaffold installs only
    psutil = None  # type: ignore[assignment]

# SQLite file header: bytes 18 (write version) / 19 (read version) are 1 for rollback-journal, 2 for WAL.
_HEADER_VERSION_BYTES = {"delete": (1, 1), "wal": (2, 2)}
_MODE_BY_HEADER_VERSION = {v: k for k, v in _HEADER_VERSION_BYTES.items()}


def _read_proc_argv(pid: int) -> Optional[List[str]]:
    """Read /proc/<pid>/cmdline without losing argv boundaries."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw = handle.read()
        if not raw:
            return None
        argv = raw.decode("utf-8", "replace").split("\x00")
        if argv[-1] == "":
            argv.pop()
        return argv or None
    except OSError:
        return None


def _describe_holder_pid(pid: int) -> str:
    """``PID 123 (qubit gateway run)`` for operator-facing holder lists; /proc argv first, psutil elsewhere.

    Faithful port of the reference ``hermes_state_holders.describe_holder_pid`` (0.21.4). The vendored 0.21.1
    ``qubit_state_holders`` lacks the symbol, and that module is out of this ticket's ownership, so the shim
    lives here rather than as an out-of-scope edit or a stub. Hoist-to-holders is a recorded follow-up.
    """
    argv = _read_proc_argv(pid)
    if argv is None and psutil is not None:
        try:
            argv = psutil.Process(pid).cmdline() or None
        except Exception:
            argv = None
    who = " ".join(" ".join([os.path.basename(argv[0]), *argv[1:]]).split())[:80] if argv else "command line unavailable"
    return f"PID {pid} ({who})"


def _header_mode(db_path: Path) -> str:
    """Journal mode as the on-disk header reports it (no SQLite connection is open in this process here)."""
    with open(db_path, "rb") as fh:
        head = fh.read(20)
    if len(head) < 20 or head[:16] != b"SQLite format 3\x00":
        return "not-a-database"
    return _MODE_BY_HEADER_VERSION.get((head[18], head[19]), f"unknown({head[18]}/{head[19]})")


def _refusal(target: str, current: str, platform: str, *, on_cross_vm_fs: bool, force: bool) -> Optional[str]:
    """Admission checks that need no I/O, with the platform passed as data so tests need not fake the host."""
    if current == "not-a-database" or current.startswith("unknown("):
        return (f"its file header reads {current}, so it is not a Hermes SQLite store this command can convert "
                "(pass --db PATH to point at one).")
    if target == "wal" and on_cross_vm_fs:
        return ("WAL shared memory silently corrupts on cross-VM filesystems (virtiofs/9p — Docker Desktop, "
                "OrbStack, Lima bind mounts). Keep this store on journal_mode=delete or move it to a native "
                "filesystem.")
    if platform == "win32" and not force:
        # foreign_state_db_holders() has no Windows scan and returns [] there: an empty holder list is
        # "unknown", never "nobody holds it", so the gate must refuse rather than issue a silent all-clear.
        return ("cannot prove the database is quiet on Windows — no holder scan. Stop the gateway, dashboard, "
                "cron and every CLI, then re-run with --force.")
    return None


def cmd_set_journal_mode(args) -> int:
    from qubit_state import _default_db_path
    from qubit_state_holders import foreign_state_db_holders
    from qubit_state_wal import (_path_on_cross_vm_fs, _set_journal_mode_no_wait, is_sqlite_wal_reset_vulnerable,
                                 resolve_journal_mode)

    target = args.mode
    db_path = Path(getattr(args, "db", None) or _default_db_path())
    if not db_path.exists():
        print(f"No database at {db_path} (nothing to convert).")
        return 1
    try:
        current = _header_mode(db_path)
    except OSError as exc:
        print(f"✗ Cannot read {db_path}: {exc}")
        return 1
    if current == target:
        print(f"✓ {db_path} is already journal_mode={target}.")
        return 0
    refusal = _refusal(
        target, current, sys.platform,
        on_cross_vm_fs=target == "wal" and _path_on_cross_vm_fs(str(db_path)),
        force=bool(getattr(args, "force", False)),
    )
    if refusal:
        print(f"✗ Refusing to change the journal mode of {db_path}: {refusal}")
        return 1
    if target == "wal" and is_sqlite_wal_reset_vulnerable():
        print(f"✗ Refusing to enable WAL: the linked SQLite {sqlite3.sqlite_version} has the WAL-reset bug "
              "(https://sqlite.org/wal.html#walresetbug). Upgrade to 3.51.3+ first.")
        return 1
    holders = foreign_state_db_holders(db_path)
    if holders:
        print(f"✗ Refusing to change the journal mode of {db_path}: other processes hold it open "
              "(a live switch would destroy their uncheckpointed commits). Stop them and re-run:")
        for pid, target_path in holders:
            print(f"    pid {pid} — {_describe_holder_pid(pid) if pid > 0 else 'scan'}: {target_path}")
        return 1
    # timeout=0: any opener that appeared between the scan and the flip makes the pragma fail with
    # 'database is locked' instead of sneaking the switch between a writer's transactions.
    try:
        conn = sqlite3.connect(str(db_path), timeout=0.0, isolation_level=None)
    except (OSError, sqlite3.Error) as exc:
        print(f"✗ Cannot open {db_path}: {exc}")
        return 1
    try:
        try:
            reported = _set_journal_mode_no_wait(conn, target.upper())
        except sqlite3.OperationalError as exc:
            print(f"✗ journal_mode={target.upper()} refused: {exc} (another opener appeared; nothing was changed)")
            return 1
    finally:
        conn.close()
    try:
        after = _header_mode(db_path)
    except OSError as exc:
        print(f"✗ Cannot re-read {db_path} after the switch: {exc}")
        return 1
    if after != target:
        print(f"✗ SQLite reported journal_mode={reported or '?'} but the file header reads {after}; not converted.")
        return 1
    print(f"✓ {db_path}: journal_mode {current} → {after} (header verified).")
    configured = resolve_journal_mode()
    if configured != target:
        print(f"  Note: config.yaml has database.journal_mode: {configured} — the next open would switch the file "
              f"back. Set `database.journal_mode: {target}` in config.yaml to keep it.")
    return 0
