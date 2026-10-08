"""Make the vendored runtime's top-level ``qubit_platform`` package importable.

The fork root (``qubit_runtime/``) is the import root for the runtime's
top-level modules; ``voicebridge.qubit_runtime.ensure_path()`` is the one
sanctioned way to put it on ``sys.path`` (it also pins ``QUBIT_HOME`` and caches
the repo's ``tests`` namespace first so the fork's same-named package cannot
shadow it). Without this the ported ``hermes_platform`` tests cannot resolve
``qubit_platform`` at collection time.
"""
from __future__ import annotations

from voicebridge import qubit_runtime

qubit_runtime.ensure_path()
