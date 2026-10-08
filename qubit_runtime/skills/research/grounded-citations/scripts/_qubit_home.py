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
        """Return the Qubit home directory (default: ``~/.qubit``).

        Mirrors ``qubit_constants.get_qubit_home()``, whose process-home
        precedence is exactly ``QUBIT_HOME`` env var -> canonical platform
        default ``~/.qubit``.  Note the default is ``~/.qubit``, NOT the
        pre-rebrand ``~/.hermes``: an unconditional legacy fallback would point
        at a home that does not exist once the hermes home is retired.
        (``qubit_constants`` is preferred when importable, above; this stdlib
        path serves standalone skill execution.)
        """
        val = os.environ.get("QUBIT_HOME", "").strip()
        return Path(val) if val else Path.home() / ".qubit"
