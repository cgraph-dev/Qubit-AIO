"""Resolve QUBIT_HOME for standalone skill scripts.

Skill scripts may run outside the Qubit process (system Python, nix env,
CI) where ``qubit_constants`` is not importable.  This module provides the
same ``get_qubit_home()`` contract without requiring it on ``sys.path``.

When ``qubit_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from qubit_constants import get_qubit_home as get_qubit_home
except (ModuleNotFoundError, ImportError):

    def get_qubit_home() -> Path:
        """Return the Qubit home directory (default: ``~/.hermes``)."""
        val = os.environ.get("QUBIT_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"
