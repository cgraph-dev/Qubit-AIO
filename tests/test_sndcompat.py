"""Focused regression tests for the NumPy 2.5 / sounddevice compat shim.

sounddevice 0.5.6 (latest) assigns ``data.shape = -1, channels`` in
``sounddevice._array``, an API NumPy 2.5 deprecated (DeprecationWarning). The
shim in ``voicebridge/audio/sndcompat.py`` replaces it with the semantically
identical ``np.reshape`` form so audio streams emit no warning. These tests are
hermetic (no live audio device required).
"""
from __future__ import annotations

import warnings
from pathlib import Path
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_sndcompat_patches_sounddevice_array():
    """Importing the audio package applies the shim: sounddevice._array is the
    NumPy-2.5-safe replacement (not the deprecated in-place assignment)."""
    import voicebridge.audio  # noqa: F401  applies sndcompat
    import sounddevice as sd
    assert sd._array.__name__ == "_array_compat"
    # idempotent
    from voicebridge.audio import sndcompat
    sndcompat.apply()
    assert sd._array.__name__ == "_array_compat"


def test_sndcompat_array_identical_and_no_deprecation():
    """The patched _array returns the exact same (view, no copy) result and
    emits NO NumPy 2.5 DeprecationWarning."""
    import sounddevice as sd
    buf = np.arange(12, dtype=np.float32).tobytes()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        out = sd._array(buf, 3, np.float32)
    deprecations = [str(x.message) for x in w
                    if "data.shape" in str(x.message)
                    or "Setting the shape on a NumPy array" in str(x.message)]
    assert deprecations == [], f"shim must not emit NumPy shape deprecation: {deprecations}"
    # exact expected values (mono→stereo reconstruction), computed without the
    # deprecated in-place shape assignment
    expected = np.arange(12, dtype=np.float32).reshape(4, 3)
    assert out.shape == (4, 3)
    assert np.array_equal(out, expected)
    assert np.shares_memory(out, np.frombuffer(buf, dtype=np.float32)), \
        "reshape must stay a view (no copy), matching the original semantics"


def test_sndcompat_handles_stereo_and_mono():
    """Shape reconstruction is correct for mono (1ch) and stereo (2ch)."""
    import sounddevice as sd
    for channels in (1, 2):
        buf = np.arange(6 * channels, dtype=np.int16).tobytes()
        out = sd._array(buf, channels, np.int16)
        assert out.shape == (6, channels)
        assert out.dtype == np.int16