"""Compatibility shim for the installed third-party ``sounddevice``.

sounddevice 0.5.6 (the latest release) assigns ``data.shape = -1, channels`` in
its ``_array()`` helper. NumPy 2.5 deprecated in-place shape assignment and now
emits ``DeprecationWarning: Setting the shape on a NumPy array has been
deprecated in NumPy 2.5``. There is no newer sounddevice release and downgrading
NumPy would change the whole audio/ML stack, so this module replaces
``sounddevice._array`` with the semantically identical ``np.reshape`` form:

- ``np.reshape(data, (-1, channels))`` on the contiguous ``np.frombuffer`` view
  is a view (no copy), exactly like the in-place assignment it replaces;
- the sounddevice stream callbacks resolve ``_array`` from the module globals at
  call time, so a single assignment here covers every stream.

Importing this module applies the patch immediately (idempotent). No production
audio behaviour changes and no third-party package is modified.
"""
from __future__ import annotations

import numpy as np
import sounddevice as sd

_ORIGINAL_SOURCE = None  # reserved: could store sounddevice._array source for audit


def _array_compat(buffer, channels, dtype):
    """NumPy-2.5-safe replacement for sounddevice._array (identical result)."""
    data = np.frombuffer(buffer, dtype=dtype)
    return np.reshape(data, (-1, channels))


def apply() -> None:
    """Patch ``sounddevice._array`` to the NumPy-2.5-safe form (idempotent)."""
    if getattr(sd, "_array", None) is _array_compat:
        return
    sd._array = _array_compat  # type: ignore[attr-defined]


apply()