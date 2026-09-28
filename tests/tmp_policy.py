"""Bounded, self-cleaning temp policy for Qubit test runs.

The failure this guards against: pytest's default basetemp accumulates under
``/tmp/pytest-of-<user>`` across sessions, and ad-hoc test scratch (throwaway
homes, seeded clones, fixtures) fills ``/tmp`` until the filesystem hits 100%
and kills the run mid-flight. This module owns the pure policy so
``tests/conftest.py`` only wires it into pytest and
``tests/test_tmpdir_policy.py`` can exercise it without booting a nested
session.

Policy:

* a **per-run** basetemp directory at ``<root>/qubit-pytest-<pid>-<token>``
  (root defaults to the system temp dir, overridable with
  ``QUBIT_PYTEST_TMPROOT`` so a big scratch disk can be used);
* ``TMPDIR`` is pointed at that directory, so nothing lands in
  ``/tmp/pytest-of-*``;
* a **free-space guard** raises before the session runs when the filesystem
  behind the temp root has less than the configured floor.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

MIB = 1024 * 1024
DEFAULT_MIN_FREE_MB = 2048
ROOT_ENV = "QUBIT_PYTEST_TMPROOT"
RUN_PREFIX = "qubit-pytest"

# Captured at import, before conftest points ``TMPDIR`` at the per-run dir, so
# ``temp_root()`` stays stable for the whole session.
_SYSTEM_TEMP = Path(tempfile.gettempdir()).resolve()


class TmpSpaceError(RuntimeError):
    """Raised when free space under the test temp root is below the floor."""


def temp_root(env=None) -> Path:
    """Return the directory that hosts per-run basetemp dirs.

    ``QUBIT_PYTEST_TMPROOT`` overrides the system temp dir so runs can be
    steered onto a larger/quota-managed filesystem.
    """
    environ = os.environ if env is None else env
    override = environ.get(ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return _SYSTEM_TEMP


def run_dir_name(pid=None, token=None) -> str:
    """Stable, collision-resistant name for one run's basetemp directory."""
    pid = os.getpid() if pid is None else int(pid)
    if token is None:
        token = next(tempfile._get_candidate_names())
    return f"{RUN_PREFIX}-{pid}-{token}"


def per_run_dir(root, pid=None, token=None) -> Path:
    """The per-run basetemp under ``root`` (no filesystem access)."""
    return Path(root) / run_dir_name(pid=pid, token=token)


def _existing_anchor(path) -> Path:
    """Nearest existing ancestor of ``path`` (for a pre-creation stat)."""
    current = Path(path)
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def free_bytes(path, *, usage=shutil.disk_usage) -> int:
    """Free bytes on the filesystem behind ``path`` (which may not exist yet)."""
    return usage(str(_existing_anchor(path))).free


def space_guard(path, min_free_bytes, *, free=free_bytes) -> "tuple[bool, int]":
    """Return ``(ok, available_bytes)`` for ``path`` against the floor."""
    available = int(free(path))
    return available >= int(min_free_bytes), available


def enforce_space_guard(path, min_free_bytes, *, free=free_bytes) -> None:
    """Raise :class:`TmpSpaceError` with an actionable message when too full."""
    ok, available = space_guard(path, min_free_bytes, free=free)
    if ok:
        return
    floor_mb = int(min_free_bytes) / MIB
    raise TmpSpaceError(
        f"test temp root {Path(path)} has only {available / MIB:.0f} MiB free "
        f"(floor {floor_mb:.0f} MiB); refusing to start so scratch cannot fill "
        f"the disk. Run scripts/tmp_reclaim.sh or set {ROOT_ENV} to a larger "
        f"filesystem."
    )
