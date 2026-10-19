"""Shared fixtures for the ported gateway test tree.

The ported files import the fork's runtime modules by bare name
(``gateway.*``, ``qubit_state``, ``qubit_cli.*``), which live under
``qubit_runtime/`` rather than the repo root. ``ensure_path()`` is the one
sanctioned way to put that root on ``sys.path`` (see tests/tools/conftest.py);
without it every ported file fails collection with ``ModuleNotFoundError``.

This module deliberately does NOT port upstream's collection-time AST anti-pattern
guard: that guard reads source text at collection time, which this repo bans
outright in tests (a test must never assert on source-code text).
"""

from voicebridge import qubit_runtime as _qubit_runtime

_qubit_runtime.ensure_path()
