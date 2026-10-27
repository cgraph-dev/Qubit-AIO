"""Atomic writes and advisory locking for the Google Workspace token file.

``google_token.json`` is refreshed from more than one process (the connector
readers and the skill CLI). A plain ``write_text`` can expose a half-written
file to a concurrent reader and silently truncate a token another process just
refreshed. This module is the single writer used by every refresh path:

* the replacement is written to a temporary file **in the same directory**, so
  ``os.replace`` is an atomic rename on the same filesystem;
* the temporary file is created with mode ``0600`` from the first byte, so a
  refreshed token is never briefly world-readable;
* the payload is flushed and ``fsync``-ed before the rename, and the directory
  is ``fsync``-ed afterwards;
* an advisory ``flock`` (POSIX; a no-op where ``fcntl`` is unavailable)
  serialises a read-refresh-write cycle across processes.

Nothing here logs or returns the payload: a failure is reported as ``False`` by
:func:`persist_authorized_user_credentials` so callers can warn without leaking
a token into an exception or a log line.

The module is stdlib-only and lives beside the skill scripts because those run
standalone. Connectors load this exact file rather than keeping a second
implementation.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX only; Windows keeps the atomic replace and skips cross-process locking.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None  # type: ignore[assignment]

TOKEN_FILE_MODE = 0o600


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync so the rename itself is durable."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_json(path: str | os.PathLike[str], payload: Any) -> None:
    """Replace *path* with *payload* atomically, without a readable window.

    Raises the original ``OSError`` on failure; the existing file is left
    untouched and the temporary file is removed before the exception escapes.
    """
    target = Path(path)
    directory = target.parent
    data = json.dumps(payload, indent=2).encode("utf-8")

    # ``mkstemp`` creates the file (0600, O_EXCL) before anything is written,
    # so there is never a world-readable or partially named target.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(directory))
    tmp = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, TOKEN_FILE_MODE)
        except (AttributeError, OSError):  # pragma: no cover - platform-dependent
            pass
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        _fsync_directory(directory)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def token_file_lock(path: str | os.PathLike[str]) -> Iterator[None]:
    """Hold an advisory lock beside *path* for one read-refresh-write cycle.

    The lock file carries no data (mode 0600) and is deliberately never
    removed: unlinking a lock file while another process may still open it
    reintroduces the race it exists to prevent.
    """
    target = Path(path)
    lock_path = target.with_name(target.name + ".lock")
    if fcntl is None:  # pragma: no cover - Windows has no fcntl
        yield
        return

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, TOKEN_FILE_MODE)
    try:
        try:
            os.fchmod(fd, TOKEN_FILE_MODE)
        except (AttributeError, OSError):  # pragma: no cover - platform-dependent
            pass
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def persist_authorized_user_credentials(
    path: str | os.PathLike[str], credentials: Any,
) -> bool:
    """Atomically persist google-auth credentials; ``False`` on any failure.

    Never raises and never includes the serialized token in an error message:
    a caller that cannot persist still has the in-memory credentials for this
    turn, and the only consequence is that the next process refreshes again.
    """
    try:
        payload = json.loads(credentials.to_json())
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if not payload.get("type"):
        payload["type"] = "authorized_user"
    try:
        atomic_write_json(path, payload)
    except (OSError, TypeError, ValueError):
        return False
    return True


__all__ = [
    "TOKEN_FILE_MODE",
    "atomic_write_json",
    "persist_authorized_user_credentials",
    "token_file_lock",
]
