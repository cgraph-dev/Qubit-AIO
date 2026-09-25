"""Resolve QUBIT_HOME for standalone skill scripts.

Skill scripts may run outside the Qubit process (e.g. system Python,
nix env, CI) where ``qubit_constants`` is not importable.  This module
provides the same ``get_qubit_home()`` and ``display_qubit_home()``
contracts as ``qubit_constants`` without requiring it on ``sys.path``.

When ``qubit_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``qubit_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``QUBIT_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from qubit_constants import display_qubit_home as display_qubit_home
    from qubit_constants import get_qubit_home as get_qubit_home
except (ModuleNotFoundError, ImportError):

    def get_qubit_home() -> Path:
        """Return the Qubit home directory (default: ~/.hermes).

        Mirrors ``qubit_constants.get_qubit_home()``."""
        val = os.environ.get("QUBIT_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"

    def display_qubit_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``qubit_constants.display_qubit_home()``."""
        home = get_qubit_home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except ValueError:
            return str(home)
